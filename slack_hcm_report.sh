#!/usr/bin/env bash
# slack_hcm_report.sh — Post daily HCM recording health + visual timeline to Slack
#
# Posts text summary + composite visual timeline image via webhook. Reads the
# schema-2 status JSON (both rigs; physical camera keys), reports the latest
# complete day, today's hour count so far, rig heartbeat and SLEAP backlog.
# The composite image is hosted on GitHub Pages (pushed by update_dashboard.sh).
#
# Usage:
#   bash slack_hcm_report.sh          # send to Slack
#   bash slack_hcm_report.sh --dry    # print to terminal only
#
# Cron (daily 8:20am and 10:20am, after update_dashboard.sh at 8:00/10:00):
#   20 8 * * * /home/exx/vast/leo/vibing/hcm-monitor/slack_hcm_report.sh >> /home/exx/vast/leo/vibing/hcm-monitor/slack_report.log 2>&1
#   20 10 * * * /home/exx/vast/leo/vibing/hcm-monitor/slack_hcm_report.sh >> /home/exx/vast/leo/vibing/hcm-monitor/slack_report.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Load secrets from config file (not committed to git)
source "$SCRIPT_DIR/.slack_config"
COMPOSITE_URL="https://leomeow123.github.io/hcm-dashboard/composite_latest.jpg"
JSON_FILE="$SCRIPT_DIR/hcm_daily_status.json"
DRY_RUN="${1:-}"

PAYLOAD=$(python3 - "$JSON_FILE" "$COMPOSITE_URL" "$DRY_RUN" << 'PYEOF'
import json, sys
from datetime import datetime

json_file = sys.argv[1]
composite_url = sys.argv[2]
dry_run = len(sys.argv) > 3 and sys.argv[3] == "--dry"

with open(json_file) as f:
    data = json.load(f)

# Schema 2: cameras keyed by PHYSICAL cage on both rigs (scan_daily.py applies
# CAMERA_SWAP.md to the old Bonsai data; frameforge cam_0N is cage N natively).
CAMS = ["cam_01", "cam_02", "cam_03", "cam_04"]
label = lambda c: f"Cam {int(c[-2:])}"
si = data.get("scan_info", {})
dates = data.get("dates", {})
sorted_dates = sorted(dates)
if not sorted_dates:
    print(json.dumps({"text": ":x: No HCM data available"}))
    sys.exit(0)

# Report the latest COMPLETE day; today is still filling in hour by hour.
complete = [d for d in sorted_dates if dates[d]["summary"].get("status") != "in_progress"]
report_date = complete[-1] if complete else sorted_dates[-1]
today_key = sorted_dates[-1] if dates[sorted_dates[-1]]["summary"].get("status") == "in_progress" else None
day = dates[report_date]
summary = day["summary"]
rig = summary.get("rig") or "bonsai"

# --- Rig / delivery (frameforge) -------------------------------------------
hb = si.get("rig") or {}
transfer = si.get("transfer") or {}
rig_lines = []
if hb:
    hb_s = hb.get("status")
    hb_icon = {"ok": ":white_check_mark:", "warn": ":warning:"}.get(hb_s, ":x:")
    rig_lines.append(f"{hb_icon} Rig `{hb.get('host', '?')}` heartbeat {hb.get('age_min', '?')} min ago"
                     + ("" if hb_s == "ok" else f" ({hb_s})"))
    worst = max((t.get("minutes_behind") or 0) for t in transfer.values()) if transfer else None
    statuses = {t.get("status") for t in transfer.values()}
    if worst is not None:
        if statuses <= {"ok"}:
            latest = next(iter(transfer.values()))
            rig_lines.append(f":white_check_mark: Hour files landing: newest {latest.get('latest_date')} "
                             f"{latest.get('latest_hour', 0):02d}:00 on all cameras ({worst} min behind)")
        else:
            lag = ", ".join(f"{label(c)} {t.get('latest_date')} {(t.get('latest_hour') or 0):02d}:00 ({t.get('minutes_behind')} min)"
                            for c, t in transfer.items() if t.get("status") != "ok")
            icon = ":x:" if "stale" in statuses or "missing" in statuses else ":warning:"
            rig_lines.append(f"{icon} Hour files behind: {lag}")
else:  # old-rig fallback
    max_behind = max((t.get("days_behind") or 999) for t in transfer.values()) if transfer else 999
    rig_lines.append(":white_check_mark: Transfer OK" if max_behind <= 1 else
                     f":warning: Transfer delayed ({max_behind}d old)" if max_behind <= 3 else
                     f":x: Transfer stale ({max_behind}d)!")

# --- Recording, latest complete day ----------------------------------------
cam_lines = []
for cam in CAMS:
    c = day.get("cameras", {}).get(cam)
    if not c or "videos" not in c:
        cam_lines.append(f"   :x: {label(cam)}: no data")
        continue
    flags = c.get("flags", [])
    icon = ":white_check_mark:" if "healthy" in flags else ":warning:" if c["videos"] > 0 else ":x:"
    if c.get("rig") in ("frameforge", "mixed"):
        notes = []
        if c.get("hours_missing"): notes.append(f"missing h {','.join(str(h) for h in c['hours_missing'])}")
        if c.get("gaps"): notes.append(f"{c['gaps']} frame gaps (max {c.get('max_gap_ms', 0):.0f} ms)")
        if c.get("missing_h5"): notes.append(f"{c['missing_h5']} without .h5")
        if "short_file" in flags: notes.append("short file")
        if "rig_switch" in flags: notes.append("rig switch day")
        cam_lines.append(f"   {icon} {label(cam)}: {c['videos']}/24 h files, {c['hours_count']:.1f} h recorded, "
                         f"{c.get('frames', 0):,} frames" + (f" - {'; '.join(notes)}" if notes else ""))
    else:
        flag_str = (" - :rotating_light: crash storm" if "crash_storm" in flags else
                    " - crashes" if "crash_day" in flags else " - incomplete" if "incomplete" in flags else "")
        cam_lines.append(f"   {icon} {label(cam)}: {c['videos']} vid, {c['sessions']} sess, {c['hours_count']}/24h{flag_str}")

status_emoji = {"healthy": ":large_green_circle:", "degraded": ":large_yellow_circle:", "missing": ":red_circle:",
                "in_progress": ":large_blue_circle:"}
day_status = summary.get("status", "unknown")
if rig in ("frameforge", "mixed"):
    total_line = (f"   *Total: {summary.get('hours_recorded', 0)}/96 camera-hours, {summary.get('frames', 0):,} frames, "
                  f"{summary.get('gaps', 0)} gaps - {day_status}*")
else:
    total_line = f"   *Total: {summary['total_videos']} vid, {summary['total_sessions']} sess - {day_status}*"

# --- Today so far -------------------------------------------------------------
today_lines = []
if today_key:
    t = dates[today_key]
    exp = t["summary"].get("hours_expected", 0)
    per = ", ".join(f"{label(c)} {t['cameras'].get(c, {}).get('videos', 0)}" for c in CAMS)
    gaps = t["summary"].get("gaps", 0)
    today_lines.append(f":hourglass_flowing_sand: *Today ({today_key}) so far:* {per} hour files "
                       f"(clock at {exp}h){' - ' + str(gaps) + ' frame gaps' if gaps else ''}")

# --- SLEAP inference --------------------------------------------------------
overall = si.get("overall", {})
ff = overall.get("frameforge") or {}
legacy = overall.get("legacy") or {}
inf_lines = []
if ff:
    done, total, backlog = ff.get("videos_done", 0), ff.get("videos_total", 0), ff.get("backlog", 0)
    alive = ff.get("workers_alive", 0)
    spf = ff.get("sec_per_file")
    icon = ":white_check_mark:" if alive == 4 and ff.get("keeping_up") else ":warning:"
    inf_lines.append(f"{icon} *SLEAP (new rig):* {done:,}/{total:,} hour files, backlog {backlog}"
                     f"{' (keeping up)' if ff.get('keeping_up') else ' (falling behind)'}, "
                     f"workers {alive}/4 alive" + (f", ~{spf // 60} min/file" if spf else ""))
    down = [label(c) for c, pc in ff.get("per_camera", {}).items() if not pc.get("worker", {}).get("alive")]
    if down:
        inf_lines.append(f"   :x: worker down: {', '.join(down)} - `tmux ls` on lee-hcm, relaunch run_frameforge.sh")
    lagging = [(label(c), pc) for c, pc in ff.get("per_camera", {}).items() if pc.get("backlog", 0) > 1]
    for lb, pc in lagging:
        inf_lines.append(f"   :warning: {lb}: {pc['backlog']} behind (oldest {pc.get('backlog_oldest')}, ETA ~{pc.get('eta_hours')} h)")
if legacy:
    ld, lt = legacy.get("videos_done", 0), legacy.get("videos_total", 1)
    inf_lines.append(f":file_cabinet: Old rig (Bonsai, to {legacy.get('last_date')}): {ld:,}/{lt:,} ({ld / max(lt, 1) * 100:.2f}%), frozen")
roi_done, roi_total = overall.get("roi_videos_done", 0), overall.get("roi_videos_total", 0)
if roi_total and roi_done < roi_total:
    inf_lines.append(f":jigsaw: *ROI Backfill:* {roi_done:,}/{roi_total:,} ({roi_done / roi_total * 100:.1f}%)")

text_lines = [f":house: *HCM Recording Health - {report_date}*", ""] + rig_lines + [
    "", f"{status_emoji.get(day_status, '')} *Recording ({report_date}, {rig} rig):*"] + cam_lines + [total_line]
if today_lines:
    text_lines += [""] + today_lines
text_lines += [""] + inf_lines
text = "\n".join(text_lines)

blocks = [
    {"type": "section", "text": {"type": "mrkdwn", "text": text}},
    {
        "type": "image",
        "image_url": composite_url + f"?t={int(datetime.now().timestamp())}",
        "alt_text": f"HCM Visual Timeline - {report_date}",
        "title": {"type": "plain_text", "text": f"Visual Timeline - {report_date}"},
    },
    {
        "type": "actions",
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": ":bar_chart: Open Dashboard"},
            "url": "https://leomeow123.github.io/hcm-dashboard/",
        }],
    },
]
payload = {"text": text, "blocks": blocks}

if dry_run:
    print(text)
    print(f"\nComposite: {composite_url}")
    print("Dashboard: https://leomeow123.github.io/hcm-dashboard/")
else:
    print(json.dumps(payload))
PYEOF
)

if [[ "$DRY_RUN" == "--dry" ]]; then
    echo "$PAYLOAD"
else
    curl -s -X POST "$SLACK_WEBHOOK" \
      -H 'Content-type: application/json' \
      -d "$PAYLOAD" > /dev/null
    echo "$(date '+%Y-%m-%d %H:%M:%S') HCM report sent to Slack"
fi
