#!/usr/bin/env bash
# slack_hcm_report.sh — Post daily HCM recording health + visual timeline to Slack
#
# Posts text summary + composite visual timeline image via webhook.
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

# Camera wiring mismatch — see CAMERA_SWAP.md
SOFTWARE_TO_PHYSICAL = {
    "cam_01": "Cam 1", "cam_02": "Cam 4",
    "cam_03": "Cam 2", "cam_04": "Cam 3",
}
CAM_ORDER = ["cam_01", "cam_03", "cam_04", "cam_02"]

sorted_dates = sorted(data["dates"].keys())
if not sorted_dates:
    print(json.dumps({"text": ":x: No HCM data available"}))
    sys.exit(0)

# The latest date is always incomplete (~2-3 videos) because robocopy
# runs at 3AM — only hours 00:00-03:00 are copied. Report the previous
# day which has the full 24hr picture.
if len(sorted_dates) >= 2:
    report_date = sorted_dates[-2]
    latest_date = sorted_dates[-1]
else:
    report_date = sorted_dates[-1]
    latest_date = report_date

day = data["dates"][report_date]
summary = day["summary"]
transfer = data.get("scan_info", {}).get("transfer", {})

# Transfer
max_behind = max((info.get("days_behind") or 999) for info in transfer.values()) if transfer else 999
if max_behind <= 1:
    transfer_line = ":white_check_mark: Transfer OK"
elif max_behind <= 3:
    transfer_line = f":warning: Transfer delayed ({max_behind}d old)"
else:
    transfer_line = f":x: Transfer stale ({max_behind}d)!"

# Per-camera
cam_lines = []
for cam in CAM_ORDER:
    label = SOFTWARE_TO_PHYSICAL[cam]
    c = day.get("cameras", {}).get(cam)
    if not c or "videos" not in c:
        cam_lines.append(f"   {label}: no data")
        continue
    flags = c.get("flags", [])
    icon = ":white_check_mark:" if "healthy" in flags else ":warning:" if c["videos"] > 0 else ":x:"
    flag_str = ""
    if "crash_storm" in flags: flag_str = " - :rotating_light: crash storm"
    elif "crash_day" in flags: flag_str = " - crashes"
    elif "incomplete" in flags: flag_str = " - incomplete"
    cam_lines.append(
        f"   {icon} {label}: {c['videos']} vid, {c['sessions']} sess, "
        f"{c['hours_count']}/24h{flag_str}"
    )

status_emoji = {"healthy": ":large_green_circle:", "degraded": ":large_yellow_circle:", "missing": ":red_circle:"}
day_status = summary.get("status", "unknown")

overall = data.get("scan_info", {}).get("overall", {})
inf_done = overall.get("inference_videos_done", 0)
inf_total = overall.get("inference_videos_total", 1)
inf_pct = inf_done / inf_total * 100 if inf_total > 0 else 0

# Urgent inference job: cam_01 + cam_03 (Cam 1 + Cam 2), May 15 – Jun 7
urgent_cams = ["cam_01", "cam_03"]
urgent_start, urgent_end = "2026-05-15", "2026-06-07"
cam_physical = {"cam_01": "Cam 1", "cam_03": "Cam 2"}
u_rec = {c: 0 for c in urgent_cams}
u_inf = {c: 0 for c in urgent_cams}
for d_str, d_data in data["dates"].items():
    if d_str < urgent_start or d_str > urgent_end:
        continue
    for uc in urgent_cams:
        cc = d_data.get("cameras", {}).get(uc, {})
        u_rec[uc] += cc.get("videos", 0)
        u_inf[uc] += cc.get("inference", {}).get("videos_done", 0)
u_total_rec = sum(u_rec.values())
u_total_inf = sum(u_inf.values())
urgent_lines = []
if u_total_rec > 0:
    u_pct = u_total_inf / u_total_rec * 100
    u_remaining = u_total_rec - u_total_inf
    bar_len = 15
    for uc in urgent_cams:
        r, i = u_rec[uc], u_inf[uc]
        p = i / r * 100 if r > 0 else 0
        fl = int(p / 100 * bar_len)
        b = "\u2588" * fl + "\u2591" * (bar_len - fl)
        urgent_lines.append(f"   {cam_physical[uc]}: `{b}` {p:.1f}% ({i}/{r})")
    fl_t = int(u_pct / 100 * bar_len)
    bt = "\u2588" * fl_t + "\u2591" * (bar_len - fl_t)
    urgent_lines.append(f"   *Total: `{bt}` {u_pct:.1f}% ({u_total_inf}/{u_total_rec}) - {u_remaining} remaining*")

text_lines = [
    f":house: *HCM Recording Health - {report_date}*",
    "",
    transfer_line,
    "",
    f"{status_emoji.get(day_status, '')} *Recording ({report_date}):*",
] + cam_lines + [
    f"   *Total: {summary['total_videos']} vid, {summary['total_sessions']} sess - {day_status}*",
    "",
    f":microscope: *Inference:* {inf_done:,}/{inf_total:,} ({inf_pct:.1f}%)",
]
if urgent_lines:
    text_lines += ["", ":rotating_light: *Urgent Inference (May 15 - Jun 7, Cam 1 + Cam 2)*"] + urgent_lines
text = "\n".join(text_lines)

# Build Block Kit payload with image
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
