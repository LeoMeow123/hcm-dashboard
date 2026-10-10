#!/usr/bin/env python3
# /// script
# dependencies = ["h5py", "numpy"]
# ///
"""HCM Daily Scanner — walks VAST and produces hcm_daily_status.json.

Two acquisition rigs feed one dashboard:

  Bonsai rig (frozen)   2024-09-24 → 2026-10-07 13:21
      /home/exx/vast/lee/2024-09-24-LeeAPP/<disk_cam>/<session>/<disk_cam>.NN.mp4
      session = recording start time, NN = chunk index relative to it; crash restarts
      create many sessions per day. Disk camera ids are SWAPPED (CAMERA_SWAP.md).

  frameforge rig        2026-10-07 14:19 → present   (talmolab-rigAD00)
      /home/exx/vast/leo/frameforge/<deployment>/cam_0N/<YYYY-MM-DD-00-00-00>/cam_0N.HH.mp4
      one folder per calendar day, one file per clock hour, a .h5 of per-frame
      nanosecond timestamps next to every .mp4. cam_0N IS physical cage N.

Output schema v2: `dates[date].cameras` is keyed by PHYSICAL camera for both rigs.
Each camera entry carries `rig` ("bonsai" | "frameforge" | "mixed" on the switch day)
and `disk_cam` (where its files live). Timeline entries are
[wall_hour, session, index, mb, thumb_cam] so thumbnails resolve for either rig.

Usage:
    python3 scan_daily.py                # incremental (new dates + last 3 days)
    python3 scan_daily.py --full         # full rescan of both rigs (~15 min: legacy tree is big)
    python3 scan_daily.py --refresh-legacy   # incremental, but recount the frozen Bonsai totals
    python3 scan_daily.py --dry-run      # print without writing JSON
    uv run scan_daily.py                 # with h5py: exact per-hour coverage and frame gaps

Without h5py the frameforge scan still works (file counts and sizes only).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

try:
    import h5py  # type: ignore
    import numpy as np  # type: ignore
    HAVE_H5 = True
except ImportError:  # plain python3: no frame stats
    HAVE_H5 = False

# --- Configuration ---------------------------------------------------------------

SCHEMA_VERSION = 2

# Bonsai rig (frozen)
OLD_ROOT = Path("/home/exx/vast/lee/2024-09-24-LeeAPP")
OLD_INFERENCE_ROOT = Path("/home/exx/vast/leo/datasets/inference-Kuo-Fen-HCM")
ROI_LOG_DIR = Path("/home/exx/vast/leo/datasets/roi-Kuo-Fen-HCM")
OLD_RIG_LAST_DATE = "2026-10-07"           # last Bonsai frame 2026-10-07 13:21 PT

# frameforge rig
FF_ROOT = Path("/home/exx/vast/leo/frameforge")
FF_INFERENCE_ROOT = Path("/home/exx/vast/leo/datasets/inference-frameforge-HCM")
FF_HEARTBEAT = FF_ROOT / "_ff_heartbeat" / "talmolab-rigAD00.json"
FF_WORKER_LOG_DIR = Path("/home/exx/vast/leo/2026-04-03-HCM-inference-accelerator/inference_log")
FF_HOST = "talmolab-rigAD00"
RIG_SWITCH_DATE = "2026-10-07"
RIG_SWITCH = {
    "date": RIG_SWITCH_DATE,
    "old_rig_last_frame": "2026-10-07T13:21",
    "new_rig_first_frame": "2026-10-07T14:19:50",
    "host": FF_HOST,
}

PHYSICAL_CAMS = ["cam_01", "cam_02", "cam_03", "cam_04"]
# Bonsai disk directory -> physical cage (CAMERA_SWAP.md). Never applied to frameforge.
OLD_DISK_TO_PHYSICAL = {"cam_01": "cam_01", "cam_02": "cam_04", "cam_03": "cam_02", "cam_04": "cam_03"}
OLD_PHYSICAL_TO_DISK = {v: k for k, v in OLD_DISK_TO_PHYSICAL.items()}

OUTPUT_FILE = Path(__file__).parent / "hcm_daily_status.json"

# Thresholds
TINY_FILE_BYTES = 1_000_000          # 1MB — crash artifact
EXPECTED_VIDEOS_PER_DAY = 24
CRASH_SESSION_THRESHOLD = 2          # >1 session means at least one crash (Bonsai)
FPS = 50.0
FRAME_GAP_MS = 100.0                 # inter-frame gap above this = dropped frames (5 frames)
FULL_HOUR_S = 3570.0                 # a file shorter than this did not cover its hour
HEARTBEAT_OK_MIN, HEARTBEAT_WARN_MIN = 90, 180      # heartbeat updates hourly
DELIVERY_OK_MIN, DELIVERY_WARN_MIN = 75, 180        # hour file lands ~30 s after the hour
WORKER_ALIVE_MIN = 40                # worker log silent longer than this = worker down (poll 10 min + 22 min/file)

PREDICTION_RE = re.compile(r"^cam_\d{2}\.\d{2}\.predictions\.slp$")
SESSION_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})-(\d{2})$")
VIDEO_RE = re.compile(r"^cam_\d{2}\.(\d{2})\.mp4$")
H5_RE = re.compile(r"^cam_\d{2}\.(\d{2})\.h5$")
DEPLOYMENT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_WORKERS = 16


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# =====================================================================================
# Bonsai rig (frozen) — unchanged scanning logic, results tagged with rig/disk_cam
# =====================================================================================

def _scan_one_session(session_dir: Path) -> tuple[list[dict], bool]:
    """Scan a single Bonsai session directory for video files. Returns (videos, is_empty)."""
    try:
        files = os.listdir(session_dir)
    except OSError:
        return ([], True)
    mp4s = [f for f in files if f.endswith(".mp4")]
    if not mp4s:
        return ([], True)
    sess_match = SESSION_RE.match(session_dir.name)
    start_hour = int(sess_match.group(2)) if sess_match else 0
    videos = []
    for mp4 in mp4s:
        vm = VIDEO_RE.match(mp4)
        if not vm:
            continue
        idx = int(vm.group(1))
        try:
            size = os.path.getsize(session_dir / mp4)
        except OSError:
            size = 0
        wall_hour = start_hour + idx
        videos.append({
            "session": session_dir.name, "file": mp4, "index": idx, "bytes": size,
            "wall_hour": wall_hour, "wall_hour_end": min(wall_hour + 1, 24),
            "tiny": size < TINY_FILE_BYTES,
        })
    return (videos, False)


def scan_bonsai_camera(disk_cam: str, after_date: str | None = None,
                       workers: int = DEFAULT_WORKERS) -> dict[str, dict]:
    """Scan all Bonsai sessions for one DISK camera, grouped by date."""
    cam_dir = OLD_ROOT / disk_cam
    if not cam_dir.is_dir():
        print(f"  WARNING: {cam_dir} not found, skipping", file=sys.stderr)
        return {}
    try:
        entries = sorted(os.listdir(cam_dir))
    except OSError as e:
        print(f"  ERROR listing {cam_dir}: {e}", file=sys.stderr)
        return {}

    dates: dict[str, list[Path]] = defaultdict(list)
    for entry in entries:
        m = SESSION_RE.match(entry)
        if not m:
            continue
        date_str = m.group(1)
        if after_date and date_str <= after_date:
            continue
        dates[date_str].append(cam_dir / entry)
    if not dates:
        return {}

    work = [(ds, sd) for ds, sds in dates.items() for sd in sds]
    date_videos: dict[str, list[dict]] = defaultdict(list)
    date_empty: dict[str, int] = defaultdict(int)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_scan_one_session, sd): ds for ds, sd in work}
        for f in as_completed(futures):
            ds = futures[f]
            try:
                videos, empty = f.result()
            except Exception as exc:
                print(f"  WARNING: session scan failed: {exc}", file=sys.stderr)
                continue
            date_videos[ds].extend(videos)
            if empty:
                date_empty[ds] += 1

    results = {}
    for date_str in sorted(dates):
        all_vids = date_videos.get(date_str, [])
        total_bytes = sum(v["bytes"] for v in all_vids)
        hours_covered = {v["wall_hour"] for v in all_vids if 0 <= v["wall_hour"] < 24}
        good_vids = [v for v in all_vids if not v["tiny"]]
        timeline = sorted(
            [[v["wall_hour"], v["session"], v["index"], round(v["bytes"] / 1_048_576, 1), disk_cam]
             for v in good_vids],
            key=lambda x: x[0],
        )
        # Fractional hours via a minute-level coverage map (overlapping crash
        # sessions are not double counted; only a session's last file can be short).
        session_vids: dict[str, list[dict]] = defaultdict(list)
        for v in good_vids:
            if 0 <= v["wall_hour"] < 24 and v["bytes"] > 0:
                session_vids[v["session"]].append(v)
        good_sizes = [v["bytes"] for v in good_vids if v["bytes"] > 0]
        median_bytes = sorted(good_sizes)[len(good_sizes) // 2] if good_sizes else 75 * 1_048_576
        coverage = [0] * 1440
        for sess_name, svids in session_vids.items():
            svids.sort(key=lambda v: v["index"])
            parts = sess_name.split("-")
            start_min = int(parts[4]) if len(parts) >= 6 else 0
            for i, v in enumerate(svids):
                if i == len(svids) - 1:
                    others_avg = (sum(vv["bytes"] for vv in svids[:-1]) / (len(svids) - 1)
                                  if len(svids) >= 2 else median_bytes)
                    ratio = v["bytes"] / others_avg if others_avg > 0 else 1.0
                    est_min = int(min(60, max(1, ratio * 60)))
                else:
                    est_min = 60
                vid_start = v["wall_hour"] * 60 + start_min
                for m in range(max(0, vid_start), min(vid_start + est_min, 1440)):
                    coverage[m] = 1
        results[date_str] = {
            "rig": "bonsai",
            "disk_cam": disk_cam,
            "sessions": len(dates[date_str]),
            "videos": len(all_vids),
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / 1_048_576, 1),
            "hours_covered": sorted(hours_covered),
            "hours_count": round(sum(coverage) / 60, 1),
            "empty_sessions": date_empty.get(date_str, 0),
            "zero_byte": sum(1 for v in all_vids if v["bytes"] == 0),
            "tiny_files": sum(1 for v in all_vids if 0 < v["bytes"] < TINY_FILE_BYTES),
            "timeline": timeline,
        }
    return results


def _scan_one_inference_session(session_dir: Path) -> int:
    try:
        files = os.listdir(session_dir)
    except OSError:
        return 0
    return sum(1 for f in files if PREDICTION_RE.match(f))


def scan_bonsai_inference(disk_cam: str, skip_dates: set[str] | None = None,
                          workers: int = DEFAULT_WORKERS) -> dict[str, dict]:
    """Count Bonsai-era .predictions.slp per date for one DISK camera."""
    inf_dir = OLD_INFERENCE_ROOT / disk_cam
    if not inf_dir.is_dir():
        return {}
    try:
        entries = sorted(os.listdir(inf_dir))
    except OSError:
        return {}
    dates: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        m = SESSION_RE.match(entry)
        if not m:
            continue
        date_str = m.group(1)
        if skip_dates and date_str in skip_dates:
            continue
        dates[date_str].append(entry)
    if not dates:
        return {}
    work = [(ds, inf_dir / sess) for ds, sessions in dates.items() for sess in sessions]
    date_slp: dict[str, int] = defaultdict(int)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_scan_one_inference_session, sd): ds for ds, sd in work}
        for f in as_completed(futures):
            ds = futures[f]
            try:
                date_slp[ds] += f.result()
            except Exception as exc:
                print(f"  WARNING: inference scan failed: {exc}", file=sys.stderr)
    return {ds: {"sessions_done": len(sessions), "videos_done": date_slp.get(ds, 0)}
            for ds, sessions in dates.items()}


def get_bonsai_totals() -> dict:
    """Count every .mp4 and .predictions.slp of the frozen Bonsai rig (slow: ~15 min)."""
    totals = {}
    for disk_cam in PHYSICAL_CAMS:
        rec_dir = OLD_ROOT / disk_cam
        inf_dir = OLD_INFERENCE_ROOT / disk_cam
        if not rec_dir.exists():
            continue
        videos_total = sessions_total = 0
        for sess in rec_dir.iterdir():
            if not sess.is_dir():
                continue
            n = sum(1 for f in sess.iterdir() if f.suffix == ".mp4")
            if n > 0:
                videos_total += n
                sessions_total += 1
        videos_done = sessions_done = 0
        if inf_dir.exists():
            for sess in inf_dir.iterdir():
                if not sess.is_dir():
                    continue
                n = sum(1 for f in sess.iterdir() if f.name.endswith(".predictions.slp"))
                if n > 0:
                    videos_done += n
                    sessions_done += 1
        totals[disk_cam] = {"videos_done": videos_done, "videos_total": videos_total,
                            "sessions_done": sessions_done, "sessions_total": sessions_total}
    return totals


_ROI_LINE = re.compile(r"(cam_\d+)\s+(\d+)/(\d+)\s+ok=(\d+)\s+err=(\d+)\s+([\d.]+)\s+vid/s\s+ETA\s+([\d.]+)")


def get_roi_totals() -> dict:
    """ROI backfill progress from worker log tails (Bonsai era, finished)."""
    out = {}
    if not ROI_LOG_DIR.is_dir():
        return out
    for lp in sorted(ROI_LOG_DIR.glob("_log_cam_*.log")):
        cam = lp.name.replace("_log_", "").replace(".log", "")
        try:
            tail = lp.read_text(errors="ignore").splitlines()[-80:]
        except OSError:
            continue
        latest = {}
        for ln in tail:
            m = _ROI_LINE.search(ln)
            if m:
                latest[m.group(3)] = m
        if not latest:
            continue
        done = total = errs = 0
        rate = 0.0
        for m in latest.values():
            done += int(m.group(2)); total += int(m.group(3)); errs += int(m.group(5)); rate += float(m.group(6))
        out[cam] = {"videos_done": done, "videos_total": total, "errors": errs, "rate_vps": round(rate, 2)}
    return out


# =====================================================================================
# frameforge rig
# =====================================================================================

def _ff_deployments() -> list[str]:
    try:
        return sorted(d for d in os.listdir(FF_ROOT) if DEPLOYMENT_RE.match(d) and (FF_ROOT / d).is_dir())
    except OSError:
        return []


def _h5_stats(path: Path) -> dict | None:
    """Per-file frame stats from the timestamp sidecar. None if unreadable or no h5py."""
    if not HAVE_H5:
        return None
    try:
        with h5py.File(path, "r") as h:
            ds = h["timestamps"]
            n = int(ds.shape[0])
            if n == 0:
                return {"frames": 0}
            fps = float(h.attrs.get("fps", FPS))
            ts = ds[:]
            first, last = int(ts[0]), int(ts[-1])
            gaps = np.diff(ts) / 1e6  # ms
            big = gaps[gaps > FRAME_GAP_MS]
            frame_ms = 1000.0 / fps
            return {
                "frames": n,
                "fps": fps,
                "start_ns": first,
                "end_ns": last,
                "span_s": round((last - first) / 1e9 + 1.0 / fps, 3),
                "max_gap_ms": round(float(gaps.max()), 1) if n > 1 else 0.0,
                "gaps": int(big.size),
                "frames_lost": int(round(float((big / frame_ms - 1).sum()))) if big.size else 0,
            }
    except Exception as exc:  # corrupt / half-written file
        print(f"  WARNING: h5 read failed {path.name}: {exc}", file=sys.stderr)
        return None


def _scan_ff_day(cam: str, deployment: str, day_dir: Path) -> dict:
    """Scan one frameforge day folder for one camera."""
    try:
        files = os.listdir(day_dir)
    except OSError:
        return {}
    mp4s = {int(m.group(1)): f for f in files if (m := VIDEO_RE.match(f))}
    h5s = {int(m.group(1)): f for f in files if (m := H5_RE.match(f))}
    recs = []
    for h in sorted(mp4s):
        p = day_dir / mp4s[h]
        try:
            st = p.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        rec = {"h": h, "file": mp4s[h], "bytes": size, "mtime": mtime,
               "mb": round(size / 1_048_576, 1), "h5": h in h5s}
        if h in h5s:
            stats = _h5_stats(day_dir / h5s[h])
            if stats:
                rec.update(stats)
        recs.append(rec)
    return {"cam": cam, "deployment": deployment, "session": day_dir.name, "files": recs}


def scan_frameforge_camera(cam: str, after_date: str | None = None,
                           workers: int = DEFAULT_WORKERS) -> dict[str, dict]:
    """Scan frameforge day folders for one camera (physical == disk id), grouped by date."""
    work: list[tuple[str, str, Path]] = []
    for dep in _ff_deployments():
        cam_dir = FF_ROOT / dep / cam
        if not cam_dir.is_dir():
            continue
        try:
            entries = sorted(os.listdir(cam_dir))
        except OSError:
            continue
        for entry in entries:
            m = SESSION_RE.match(entry)
            if not m:
                continue
            date_str = m.group(1)
            if after_date and date_str <= after_date:
                continue
            work.append((date_str, dep, cam_dir / entry))
    if not work:
        return {}

    by_date: dict[str, list[dict]] = defaultdict(list)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_scan_ff_day, cam, dep, d): ds for ds, dep, d in work}
        for f in as_completed(futures):
            ds = futures[f]
            try:
                r = f.result()
            except Exception as exc:
                print(f"  WARNING: frameforge day scan failed: {exc}", file=sys.stderr)
                continue
            if r:
                by_date[ds].append(r)

    today = today_str()
    now = datetime.now()
    results = {}
    for date_str, days in by_date.items():
        # Normally one folder per date; a redeploy could produce two — pool their files.
        recs = [dict(r, session=d["session"], deployment=d["deployment"]) for d in days for r in d["files"]]
        recs.sort(key=lambda r: (r["h"], r["mtime"]))
        by_hour: dict[int, dict] = {}
        for r in recs:
            by_hour.setdefault(r["h"], r)   # first file for an hour wins
        files = [by_hour[h] for h in sorted(by_hour)]

        total_bytes = sum(r["bytes"] for r in recs)
        good = [r for r in files if r["bytes"] >= TINY_FILE_BYTES]
        good_sizes = sorted(r["bytes"] for r in good) or [90 * 1_048_576]
        median_bytes = good_sizes[len(good_sizes) // 2]

        covered_s = 0.0
        frames = frames_lost = gaps = 0
        max_gap = 0.0
        with_stats = 0
        file_out = []
        for r in good:
            if "span_s" in r:
                span = min(r["span_s"], 3600.0)
                with_stats += 1
                frames += r["frames"]; frames_lost += r.get("frames_lost", 0); gaps += r.get("gaps", 0)
                max_gap = max(max_gap, r.get("max_gap_ms", 0.0))
                start = datetime.fromtimestamp(r["start_ns"] / 1e9).strftime("%H:%M:%S")
                end = datetime.fromtimestamp(r["end_ns"] / 1e9).strftime("%H:%M:%S")
            else:
                span = 3600.0 * min(1.0, r["bytes"] / median_bytes) if median_bytes else 3600.0
                start = f"{r['h']:02d}:00:00"
                end = f"{r['h'] + 1:02d}:00:00" if r["h"] < 23 else "24:00:00"
            covered_s += span
            fo = {"h": r["h"], "mb": r["mb"], "start": start, "end": end, "span_s": round(span, 1), "h5": r["h5"]}
            if "frames" in r:
                fo.update({"frames": r["frames"], "max_gap_ms": r.get("max_gap_ms", 0.0), "gaps": r.get("gaps", 0)})
            file_out.append(fo)

        hours_covered = sorted(by_hour)
        is_today = date_str == today
        latest_mtime = max((r["mtime"] for r in recs), default=0.0)
        entry = {
            "rig": "frameforge",
            "disk_cam": cam,
            "deployment": days[-1]["deployment"],
            "session": days[-1]["session"],
            "sessions": 1,
            "videos": len(files),
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / 1_048_576, 1),
            "hours_covered": hours_covered,
            "hours_count": round(covered_s / 3600, 2),
            "hours_missing": [h for h in range(24) if h not in by_hour] if not is_today else
                             [h for h in range(now.hour) if h not in by_hour],
            "empty_sessions": 0,
            "zero_byte": sum(1 for r in files if r["bytes"] == 0),
            "tiny_files": sum(1 for r in files if 0 < r["bytes"] < TINY_FILE_BYTES),
            "missing_h5": sum(1 for r in files if not r["h5"]),
            "frames": frames,
            "frames_lost": frames_lost,
            "gaps": gaps,
            "max_gap_ms": max_gap,
            "frame_stats": with_stats == len(good) and len(good) > 0,
            "last_file_at": datetime.fromtimestamp(latest_mtime).isoformat(timespec="seconds") if latest_mtime else None,
            "timeline": [[r["h"], r["session"], r["h"], r["mb"], cam] for r in good],
            "files": file_out,
        }
        entry["flags"] = compute_ff_flags(entry, date_str, now)
        results[date_str] = entry
    return results


def compute_ff_flags(e: dict, date_str: str, now: datetime) -> list[str]:
    flags = []
    is_today = date_str == now.strftime("%Y-%m-%d")
    if e["videos"] == 0:
        flags.append("no_videos")
    if is_today:
        flags.append("in_progress")
        # the file for hour H lands at H+1:00:30; allow one hour of slack
        if e["videos"] < now.hour - 1:
            flags.append("delivery_lag")
    else:
        if e["videos"] < EXPECTED_VIDEOS_PER_DAY:
            flags.append("incomplete")
        if e["hours_missing"]:
            flags.append("missing_hours")
    if e["gaps"] > 0:
        flags.append("frame_gaps")
    if e["missing_h5"] > 0:
        flags.append("missing_h5")
    if e["zero_byte"] > 0:
        flags.append("zero_byte_files")
    if e["tiny_files"] > 0:
        flags.append("tiny_files")
    # A short file is a recording break, except the deployment's first file and today's newest hour.
    last_h = max((f["h"] for f in e["files"]), default=-1)
    shorts = [f for f in e["files"] if f["span_s"] < FULL_HOUR_S and not (is_today and f["h"] == last_h)]
    if date_str == RIG_SWITCH_DATE:
        shorts = [f for f in shorts if f["h"] != min(ff["h"] for ff in e["files"])]
        flags.append("rig_switch")
    if shorts:
        flags.append("short_file")
    bad = {"no_videos", "incomplete", "missing_hours", "frame_gaps", "missing_h5", "zero_byte_files",
           "tiny_files", "short_file", "delivery_lag", "rig_switch", "in_progress"}
    if not (bad & set(flags)) and e["videos"] == EXPECTED_VIDEOS_PER_DAY and e["hours_count"] >= 23.9:
        flags.append("healthy")
    return flags


def scan_frameforge_inference(cam: str, skip_dates: set[str] | None = None) -> dict[str, dict]:
    """Count frameforge .predictions.slp per date for one camera."""
    results: dict[str, dict] = {}
    try:
        deps = sorted(d for d in os.listdir(FF_INFERENCE_ROOT) if DEPLOYMENT_RE.match(d))
    except OSError:
        return {}
    for dep in deps:
        cam_dir = FF_INFERENCE_ROOT / dep / cam
        if not cam_dir.is_dir():
            continue
        for entry in sorted(os.listdir(cam_dir)):
            m = SESSION_RE.match(entry)
            if not m:
                continue
            date_str = m.group(1)
            if skip_dates and date_str in skip_dates:
                continue
            try:
                files = os.listdir(cam_dir / entry)
            except OSError:
                continue
            hours = sorted(int(f.split(".")[1]) for f in files if PREDICTION_RE.match(f))
            r = results.setdefault(date_str, {"sessions_done": 0, "videos_done": 0, "hours_done": []})
            r["sessions_done"] += 1
            r["videos_done"] += len(hours)
            r["hours_done"] = sorted(set(r["hours_done"]) | set(hours))
    return results


_WORKER_TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
_WORKER_DONE = re.compile(r"done (\d+)s \(total (\d+)\)")
_WORKER_SCAN = re.compile(r"Scan #(\d+): (\d+) videos, (\d+) processed, (\d+) done before, (\d+) still landing, (\d+) failed")
_WORKER_FILE = re.compile(r"\] (\S+/cam_\d{2}\.\d{2}\.mp4) \.\.\.")


def _ff_worker_status(cam: str, now: datetime) -> dict:
    """State of the SLEAP worker for one camera from its console log tail."""
    lp = FF_WORKER_LOG_DIR / f"ff_{cam}_console.log"
    out = {"log": str(lp), "alive": False, "last_seen": None, "silent_min": None,
           "sec_per_file": None, "processing": None, "processing_since": None,
           "failed_last_scan": 0, "still_landing": 0}
    if not lp.exists():
        out["missing_log"] = True
        return out
    try:
        mtime = datetime.fromtimestamp(lp.stat().st_mtime)
        lines = lp.read_text(errors="ignore").splitlines()[-120:]
    except OSError:
        return out
    silent = (now - mtime).total_seconds() / 60
    out["last_seen"] = mtime.isoformat(timespec="seconds")
    out["silent_min"] = round(silent)
    out["alive"] = silent <= WORKER_ALIVE_MIN
    durs = [int(m.group(1)) for ln in lines if (m := _WORKER_DONE.search(ln))]
    if durs:
        out["sec_per_file"] = round(sum(durs[-10:]) / len(durs[-10:]))
    for ln in lines:
        m = _WORKER_SCAN.search(ln)
        if m:
            out["failed_last_scan"] = int(m.group(6)); out["still_landing"] = int(m.group(5))
    last = next((ln for ln in reversed(lines) if ln.strip()), "")
    fm = _WORKER_FILE.search(last)
    if fm and "Saved file" not in last and "done" not in last:
        out["processing"] = fm.group(1)
        tm = _WORKER_TS.match(last)
        out["processing_since"] = tm.group(1) if tm else None
    return out


def get_frameforge_totals(now: datetime) -> dict:
    """Recorded vs inferred hour-files on the frameforge rig, plus worker health."""
    per_cam = {}
    for cam in PHYSICAL_CAMS:
        rec = inf = 0
        latest_slp = 0.0
        rec_hours: set[tuple[str, int]] = set()
        inf_hours: set[tuple[str, int]] = set()
        for dep in _ff_deployments():
            cam_dir = FF_ROOT / dep / cam
            if cam_dir.is_dir():
                for day in os.listdir(cam_dir):
                    if not SESSION_RE.match(day):
                        continue
                    try:
                        for f in os.listdir(cam_dir / day):
                            if (m := VIDEO_RE.match(f)):
                                rec_hours.add((day, int(m.group(1))))
                    except OSError:
                        pass
        try:
            inf_deps = sorted(d for d in os.listdir(FF_INFERENCE_ROOT) if DEPLOYMENT_RE.match(d))
        except OSError:
            inf_deps = []
        for dep in inf_deps:
            cam_dir = FF_INFERENCE_ROOT / dep / cam
            if cam_dir.is_dir():
                for day in os.listdir(cam_dir):
                    if not SESSION_RE.match(day):
                        continue
                    try:
                        for f in os.listdir(cam_dir / day):
                            if PREDICTION_RE.match(f):
                                inf_hours.add((day, int(f.split(".")[1])))
                                try:
                                    latest_slp = max(latest_slp, (cam_dir / day / f).stat().st_mtime)
                                except OSError:
                                    pass
                    except OSError:
                        pass
        rec, inf = len(rec_hours), len(rec_hours & inf_hours)
        backlog = sorted(rec_hours - inf_hours)
        w = _ff_worker_status(cam, now)
        eta_h = round(len(backlog) * w["sec_per_file"] / 3600, 1) if w["sec_per_file"] and backlog else 0.0
        per_cam[cam] = {
            "videos_done": inf, "videos_total": rec, "backlog": len(backlog),
            "backlog_oldest": f"{backlog[0][0][:10]} {backlog[0][1]:02d}:00" if backlog else None,
            "eta_hours": eta_h,
            "last_done_at": datetime.fromtimestamp(latest_slp).isoformat(timespec="seconds") if latest_slp else None,
            "worker": w,
        }
    done = sum(c["videos_done"] for c in per_cam.values())
    total = sum(c["videos_total"] for c in per_cam.values())
    backlog = sum(c["backlog"] for c in per_cam.values())
    rates = [c["worker"]["sec_per_file"] for c in per_cam.values() if c["worker"]["sec_per_file"]]
    return {
        "rig": "frameforge", "host": FF_HOST,
        "videos_done": done, "videos_total": total, "backlog": backlog,
        "keeping_up": all(c["backlog"] <= 1 for c in per_cam.values()),
        "workers_alive": sum(1 for c in per_cam.values() if c["worker"]["alive"]),
        "sec_per_file": round(sum(rates) / len(rates)) if rates else None,
        "per_camera": per_cam,
        "worker_log_dir": str(FF_WORKER_LOG_DIR),
    }


def check_rig_heartbeat(now: datetime) -> dict:
    out = {"host": FF_HOST, "file": str(FF_HEARTBEAT), "status": "missing", "heartbeat_at": None, "age_min": None}
    try:
        hb = json.loads(FF_HEARTBEAT.read_text())
        ts = datetime.fromisoformat(hb["timestamp"])
        age = (now.astimezone() - ts).total_seconds() / 60
        out.update({"host": hb.get("hostname", FF_HOST), "ip": hb.get("ip"),
                    "heartbeat_at": hb["timestamp"], "age_min": round(age),
                    "status": "ok" if age <= HEARTBEAT_OK_MIN else "warn" if age <= HEARTBEAT_WARN_MIN else "stale"})
    except (OSError, ValueError, KeyError) as exc:
        out["error"] = str(exc)
    return out


def check_ff_delivery(now: datetime) -> dict:
    """Per physical camera: how far behind the clock the newest hour file is."""
    result = {}
    today = now.strftime("%Y-%m-%d")
    for cam in PHYSICAL_CAMS:
        latest = None  # (date, hour, path, mtime)
        for dep in _ff_deployments():
            cam_dir = FF_ROOT / dep / cam
            if not cam_dir.is_dir():
                continue
            days = sorted(d for d in os.listdir(cam_dir) if SESSION_RE.match(d))
            for day in reversed(days):
                try:
                    hours = sorted(int(m.group(1)) for f in os.listdir(cam_dir / day) if (m := VIDEO_RE.match(f)))
                except OSError:
                    hours = []
                if hours:
                    p = cam_dir / day / f"{cam}.{hours[-1]:02d}.mp4"
                    try:
                        mt = p.stat().st_mtime
                    except OSError:
                        mt = 0.0
                    cand = (day[:10], hours[-1], p, mt)
                    if latest is None or cand[:2] > latest[:2]:
                        latest = cand
                    break
        if not latest:
            result[cam] = {"latest_session": None, "latest_date": None, "latest_hour": None,
                           "days_behind": None, "minutes_behind": None, "status": "missing"}
            continue
        date_s, hour, p, mt = latest
        hour_end = datetime.strptime(date_s, "%Y-%m-%d") + timedelta(hours=hour + 1)
        behind = (now - hour_end).total_seconds() / 60
        result[cam] = {
            "latest_session": p.parent.name,
            "latest_date": date_s,
            "latest_hour": hour,
            "latest_file": str(p),
            "latest_file_at": datetime.fromtimestamp(mt).isoformat(timespec="seconds") if mt else None,
            "days_behind": (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(date_s, "%Y-%m-%d")).days,
            "minutes_behind": round(behind),
            "status": "ok" if behind <= DELIVERY_OK_MIN else "warn" if behind <= DELIVERY_WARN_MIN else "stale",
            "thumb": f"thumbs/{cam}/{p.parent.name}/{hour:02d}.jpg",
        }
    return result


# =====================================================================================
# Merge, summaries, migration
# =====================================================================================

def compute_bonsai_flags(day: dict) -> list[str]:
    flags = []
    if day["videos"] == 0:
        flags.append("no_videos")
    if day["videos"] < EXPECTED_VIDEOS_PER_DAY:
        flags.append("incomplete")
    if day["sessions"] > CRASH_SESSION_THRESHOLD:
        flags.append("crash_day")
    if day["sessions"] > 50:
        flags.append("crash_storm")
    if day["empty_sessions"] > 0:
        flags.append("empty_sessions")
    if day["zero_byte"] > 0:
        flags.append("zero_byte_files")
    if day["tiny_files"] > 0:
        flags.append("tiny_files")
    if day["hours_count"] >= 23 and day["sessions"] == 1:
        flags.append("healthy")
    return flags


def merge_rig_parts(old: dict | None, new: dict | None) -> dict:
    """Combine a Bonsai and a frameforge entry for the same physical camera and date."""
    if old is None:
        return new
    if new is None:
        return old
    m = {
        "rig": "mixed",
        "disk_cam": new["disk_cam"],
        "old_disk_cam": old["disk_cam"],
        "deployment": new.get("deployment"),
        "session": new.get("session"),
        "sessions": old["sessions"] + new["sessions"],
        "videos": old["videos"] + new["videos"],
        "total_bytes": old["total_bytes"] + new["total_bytes"],
        "total_mb": round(old["total_mb"] + new["total_mb"], 1),
        "hours_covered": sorted(set(old["hours_covered"]) | set(new["hours_covered"])),
        "hours_count": round(old["hours_count"] + new["hours_count"], 2),
        "hours_missing": [h for h in range(24) if h not in set(old["hours_covered"]) | set(new["hours_covered"])],
        "empty_sessions": old["empty_sessions"] + new["empty_sessions"],
        "zero_byte": old["zero_byte"] + new["zero_byte"],
        "tiny_files": old["tiny_files"] + new["tiny_files"],
        "missing_h5": new.get("missing_h5", 0),
        "frames": new.get("frames", 0), "frames_lost": new.get("frames_lost", 0), "gaps": new.get("gaps", 0),
        "max_gap_ms": new.get("max_gap_ms", 0.0), "frame_stats": new.get("frame_stats", False),
        "last_file_at": new.get("last_file_at"),
        "timeline": sorted(old["timeline"] + new["timeline"], key=lambda t: t[0]),
        "files": new.get("files", []),
        "parts": {"bonsai": old, "frameforge": new},
    }
    flags = (set(old.get("flags", [])) | set(new.get("flags", []))) - {"healthy", "incomplete"}
    flags.add("rig_switch")
    m["flags"] = sorted(flags)
    inf_old, inf_new = old.get("inference"), new.get("inference")
    if inf_old or inf_new:
        m["inference"] = {
            "sessions_done": (inf_old or {}).get("sessions_done", 0) + (inf_new or {}).get("sessions_done", 0),
            "videos_done": (inf_old or {}).get("videos_done", 0) + (inf_new or {}).get("videos_done", 0),
            "hours_done": (inf_new or {}).get("hours_done", []),
        }
    return m


def compute_day_summary(cam_data: dict[str, dict], date_str: str, now: datetime) -> dict:
    cameras_present = [c for c in PHYSICAL_CAMS if c in cam_data and "videos" in cam_data[c]]
    cameras_missing = [c for c in PHYSICAL_CAMS if c not in cameras_present]
    total_videos = sum(cam_data[c]["videos"] for c in cameras_present)
    total_bytes = sum(cam_data[c]["total_bytes"] for c in cameras_present)
    total_sessions = sum(cam_data[c]["sessions"] for c in cameras_present)
    rigs = {cam_data[c].get("rig", "bonsai") for c in cameras_present}
    rig = "mixed" if len(rigs) > 1 or "mixed" in rigs else (rigs.pop() if rigs else None)
    is_today = date_str == now.strftime("%Y-%m-%d")
    all_healthy = len(cameras_present) == 4 and all("healthy" in cam_data[c].get("flags", []) for c in PHYSICAL_CAMS)
    if is_today and rig in ("frameforge", "mixed"):
        status = "in_progress"
    elif all_healthy:
        status = "healthy"
    elif total_videos > 0:
        status = "degraded"
    else:
        status = "missing"
    out = {
        "rig": rig,
        "cameras_present": len(cameras_present),
        "cameras_missing": cameras_missing,
        "total_videos": total_videos,
        "total_mb": round(total_bytes / 1_048_576, 1),
        "total_sessions": total_sessions,
        "max_sessions_any_cam": max((cam_data[c]["sessions"] for c in cameras_present), default=0),
        "hours_expected": (now.hour if is_today else 24),
        "status": status,
    }
    if rig in ("frameforge", "mixed"):
        out["frames"] = sum(cam_data[c].get("frames", 0) for c in cameras_present)
        out["gaps"] = sum(cam_data[c].get("gaps", 0) for c in cameras_present)
        out["frames_lost"] = sum(cam_data[c].get("frames_lost", 0) for c in cameras_present)
        out["hours_recorded"] = round(sum(cam_data[c].get("hours_count", 0) for c in cameras_present), 1)
    return out


def migrate_v1(data: dict) -> dict:
    """Re-key a schema-1 file (disk camera ids) to physical ids; tag rig and thumb cam."""
    if data.get("scan_info", {}).get("schema") == SCHEMA_VERSION:
        return data
    n = 0
    for date_str, day in data.get("dates", {}).items():
        cams = day.get("cameras", {})
        if any(c.get("rig") for c in cams.values()):
            continue  # already tagged
        new_cams = {}
        for disk, entry in cams.items():
            phys = OLD_DISK_TO_PHYSICAL.get(disk, disk)
            entry["rig"] = "bonsai"
            entry["disk_cam"] = disk
            for t in entry.get("timeline", []):
                if len(t) == 4:
                    t.append(disk)
            new_cams[phys] = entry
        day["cameras"] = new_cams
        s = day.get("summary", {})
        s["cameras_missing"] = [OLD_DISK_TO_PHYSICAL.get(c, c) for c in s.get("cameras_missing", [])]
        s["rig"] = "bonsai"
        n += 1
    if n:
        print(f"Migrated {n} dates from schema 1 (disk ids) to schema 2 (physical ids)")
    return data


def get_inference_skip_dates(data: dict) -> dict[str, set[str]]:
    """Per PHYSICAL camera: dates whose inference is complete or that have no recording."""
    cam_skip: dict[str, set[str]] = {c: set() for c in PHYSICAL_CAMS}
    for date_str, day_data in data.get("dates", {}).items():
        for cam in PHYSICAL_CAMS:
            e = day_data.get("cameras", {}).get(cam, {})
            rec = e.get("videos", 0)
            inf = e.get("inference", {}).get("videos_done", 0)
            if rec == 0 or inf >= rec:
                cam_skip[cam].add(date_str)
    return cam_skip


def load_existing(path: Path) -> dict:
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: Could not load {path}: {e}", file=sys.stderr)
    return {"scan_info": {}, "dates": {}}


# =====================================================================================
# Main
# =====================================================================================

def main():
    parser = argparse.ArgumentParser(description="HCM Daily Scanner (Bonsai + frameforge)")
    parser.add_argument("--full", action="store_true", help="Full rescan of both rigs (slow)")
    parser.add_argument("--refresh-legacy", action="store_true",
                        help="Recount the frozen Bonsai inference totals (~15 min); otherwise cached")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE, help="Output JSON path")
    parser.add_argument("--days", type=int, help="Only scan the last N days")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="I/O threads per camera")
    args = parser.parse_args()

    t0 = datetime.now()
    now = t0
    workers = args.workers
    print(f"HCM scanner v{SCHEMA_VERSION} — h5 frame stats: {'on' if HAVE_H5 else 'OFF (no h5py; run with uv run)'}")
    RESCAN_DAYS = 3

    if args.full:
        data = {"scan_info": {}, "dates": {}}
        after_date = None
        print("Full scan mode — scanning all dates on both rigs")
    else:
        data = migrate_v1(load_existing(args.output))
        dates_known = list(data.get("dates", {}).keys())
        after_date = max(dates_known) if dates_known else None
        print(f"Incremental scan — dates after {after_date}" if after_date else "No existing data — scanning all dates")
        rescan_cutoff = (now - timedelta(days=RESCAN_DAYS + 1)).strftime("%Y-%m-%d")
        if after_date and rescan_cutoff < after_date:
            after_date = rescan_cutoff
        print(f"Rescanning dates after {after_date} (last {RESCAN_DAYS} days: hour files keep landing)")

    if args.days:
        cutoff = (now - timedelta(days=args.days)).strftime("%Y-%m-%d")
        if after_date is None or cutoff > after_date:
            after_date = cutoff
        print(f"Limited to last {args.days} days (after {after_date})")

    cam_skip = {c: set() for c in PHYSICAL_CAMS} if args.full else get_inference_skip_dates(data)
    # Never skip the rescan window: a day that looked complete can gain hour files
    # (today on the frameforge rig; both halves of the rig-switch day).
    if after_date:
        for c in cam_skip:
            cam_skip[c] = {d for d in cam_skip[c] if d <= after_date}

    # --- Recordings: both rigs, 4 cameras each, in parallel ---
    scan_old = args.full or after_date is None or after_date < OLD_RIG_LAST_DATE
    print(f"Scanning recordings (frameforge{' + bonsai' if scan_old else ''})...")
    ff_rec: dict[str, dict[str, dict]] = {}
    old_rec: dict[str, dict[str, dict]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(scan_frameforge_camera, cam, after_date, workers): ("ff", cam) for cam in PHYSICAL_CAMS}
        if scan_old:
            futs.update({pool.submit(scan_bonsai_camera, disk, after_date, workers): ("old", disk) for disk in PHYSICAL_CAMS})
        for f in as_completed(futs):
            kind, cam = futs[f]
            res = f.result()
            (ff_rec if kind == "ff" else old_rec)[cam] = res
            print(f"  {kind:3s} {cam}: {len(res)} dates")
    t_rec = datetime.now()
    print(f"  Recording scan: {(t_rec - t0).total_seconds():.1f}s")

    # --- Inference per date ---
    print("Scanning inference...")
    ff_inf: dict[str, dict[str, dict]] = {}
    old_inf: dict[str, dict[str, dict]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(scan_frameforge_inference, cam, cam_skip[cam]): ("ff", cam) for cam in PHYSICAL_CAMS}
        if scan_old:
            futs.update({pool.submit(scan_bonsai_inference, disk, cam_skip[OLD_DISK_TO_PHYSICAL[disk]], workers): ("old", disk)
                         for disk in PHYSICAL_CAMS})
        for f in as_completed(futs):
            kind, cam = futs[f]
            res = f.result()
            (ff_inf if kind == "ff" else old_inf)[cam] = res
            print(f"  {kind:3s} {cam}: {len(res)} dates")
    t_inf = datetime.now()
    print(f"  Inference scan: {(t_inf - t_rec).total_seconds():.1f}s")

    # --- Rig freshness ---
    print("Checking rig heartbeat and hour-file delivery...")
    heartbeat = check_rig_heartbeat(now)
    transfer = check_ff_delivery(now)
    print(f"  heartbeat: {heartbeat['status']} ({heartbeat.get('age_min')} min old)")
    for cam, info in transfer.items():
        print(f"  {cam}: latest {info['latest_date']} {info['latest_hour']}:00 ({info['minutes_behind']} min behind) — {info['status']}")

    # --- Merge into dates ---
    all_dates: set[str] = set()
    for d in (*ff_rec.values(), *ff_inf.values()):
        all_dates.update(d.keys())
    for d in (*old_rec.values(), *old_inf.values()):
        all_dates.update(d.keys())

    new_dates = 0
    for date_str in sorted(all_dates):
        existing_day = data.get("dates", {}).get(date_str, {})
        cam_entries = dict(existing_day.get("cameras", {}))
        for phys in PHYSICAL_CAMS:
            disk = OLD_PHYSICAL_TO_DISK[phys]
            prev = cam_entries.get(phys)
            prev_parts = prev.get("parts") if prev and prev.get("rig") == "mixed" else None
            old_e = old_rec.get(disk, {}).get(date_str)
            new_e = ff_rec.get(phys, {}).get(date_str)
            if old_e is not None:
                old_e["flags"] = compute_bonsai_flags(old_e)
            # keep the half that was not rescanned this run
            if old_e is None and prev is not None:
                old_e = prev_parts["bonsai"] if prev_parts else (prev if prev.get("rig", "bonsai") == "bonsai" else None)
            if new_e is None and prev is not None:
                new_e = prev_parts["frameforge"] if prev_parts else (prev if prev.get("rig") == "frameforge" else None)
            # carry per-date inference forward onto the halves, then apply fresh counts
            if old_e is not None and "inference" not in old_e and prev and prev.get("rig", "bonsai") == "bonsai":
                old_e["inference"] = prev.get("inference", {})
            if date_str in old_inf.get(disk, {}) and old_e is not None:
                old_e["inference"] = old_inf[disk][date_str]
            if date_str in ff_inf.get(phys, {}):
                if new_e is None:
                    new_e = {"rig": "frameforge", "disk_cam": phys, "sessions": 0, "videos": 0, "total_bytes": 0,
                             "total_mb": 0.0, "hours_covered": [], "hours_count": 0.0, "hours_missing": [],
                             "empty_sessions": 0, "zero_byte": 0, "tiny_files": 0, "missing_h5": 0, "frames": 0,
                             "frames_lost": 0, "gaps": 0, "max_gap_ms": 0.0, "frame_stats": False,
                             "timeline": [], "files": [], "flags": ["no_videos"]}
                new_e["inference"] = ff_inf[phys][date_str]
            merged = merge_rig_parts(old_e, new_e) if (old_e and new_e) else (new_e or old_e)
            if merged is not None:
                cam_entries[phys] = merged
        summary = compute_day_summary(cam_entries, date_str, now)
        inf_videos = sum(cam_entries.get(c, {}).get("inference", {}).get("videos_done", 0) for c in PHYSICAL_CAMS)
        inf_sessions = sum(cam_entries.get(c, {}).get("inference", {}).get("sessions_done", 0) for c in PHYSICAL_CAMS)
        rec_videos = sum(cam_entries.get(c, {}).get("videos", 0) for c in PHYSICAL_CAMS)
        rec_sessions = sum(cam_entries.get(c, {}).get("sessions", 0) for c in PHYSICAL_CAMS)
        summary["inference"] = {
            "sessions_done": inf_sessions, "videos_done": inf_videos,
            "sessions_total": rec_sessions, "videos_total": rec_videos,
            "complete": bool(rec_videos) and inf_videos >= rec_videos,
        }
        data.setdefault("dates", {})[date_str] = {"summary": summary, "cameras": cam_entries}
        new_dates += 1

    # --- Totals ---
    all_date_keys = sorted(data["dates"])
    prev_overall = data.get("scan_info", {}).get("overall", {})
    legacy = prev_overall.get("legacy")
    if args.full or args.refresh_legacy or not legacy:
        if legacy is None and prev_overall.get("inference_per_camera") and not (args.full or args.refresh_legacy):
            # first run after migration: reuse the v1 numbers rather than walking 70k dirs
            per_cam = prev_overall["inference_per_camera"]
            computed_at = data["scan_info"].get("last_scan")
        else:
            print("Counting Bonsai totals (frozen rig; this takes ~15 min)...")
            per_cam = get_bonsai_totals()
            computed_at = datetime.now().isoformat(timespec="seconds")
        old_dates = [d for d in all_date_keys if d <= OLD_RIG_LAST_DATE]
        legacy = {
            "rig": "bonsai", "first_date": old_dates[0] if old_dates else None, "last_date": OLD_RIG_LAST_DATE,
            "days": len(old_dates),
            "videos_done": sum(t.get("videos_done", 0) for t in per_cam.values()),
            "videos_total": sum(t.get("videos_total", 0) for t in per_cam.values()),
            "per_camera": per_cam,  # DISK ids (read by update_combined_progress.sh)
            "computed_at": computed_at,
        }
    ff = get_frameforge_totals(now)
    ff_dates = [d for d in all_date_keys if d >= RIG_SWITCH_DATE]
    ff["first_date"] = ff_dates[0] if ff_dates else None
    ff["days"] = len(ff_dates)
    ff["healthy_days"] = sum(1 for d in ff_dates if data["dates"][d]["summary"]["status"] == "healthy")
    ff["hours_recorded"] = round(sum(data["dates"][d]["summary"].get("hours_recorded", 0) for d in ff_dates), 1)
    ff["frame_gaps"] = sum(data["dates"][d]["summary"].get("gaps", 0) for d in ff_dates)
    ff["frames"] = sum(data["dates"][d]["summary"].get("frames", 0) for d in ff_dates)

    status_counts = defaultdict(int)
    for d in data["dates"].values():
        status_counts[d["summary"]["status"]] += 1
    inf_complete_dates = sum(1 for d in data["dates"].values() if d["summary"].get("inference", {}).get("complete"))
    roi_totals = get_roi_totals()

    data["scan_info"] = {
        "schema": SCHEMA_VERSION,
        "last_scan": now.isoformat(),
        "scan_mode": "full" if args.full else "incremental",
        "frame_stats": HAVE_H5,
        "new_dates_scanned": new_dates,
        "total_dates": len(all_date_keys),
        "date_range": {"first": all_date_keys[0] if all_date_keys else None,
                       "last": all_date_keys[-1] if all_date_keys else None},
        "data_root": str(FF_ROOT),
        "data_roots": {"bonsai": str(OLD_ROOT), "frameforge": str(FF_ROOT)},
        "inference_roots": {"bonsai": str(OLD_INFERENCE_ROOT), "frameforge": str(FF_INFERENCE_ROOT)},
        "rig_switch": RIG_SWITCH,
        "rig": heartbeat,
        "transfer": transfer,
        "latest_frames": {cam: {"date": t["latest_date"], "hour": t["latest_hour"], "session": t["latest_session"],
                                "thumb": t.get("thumb"), "at": t.get("latest_file_at")}
                          for cam, t in transfer.items() if t.get("latest_date")},
        "overall": {
            "healthy_days": status_counts["healthy"],
            "degraded_days": status_counts["degraded"],
            "missing_days": status_counts["missing"],
            "in_progress_days": status_counts["in_progress"],
            "inference_complete_dates": inf_complete_dates,
            "inference_videos_done": legacy["videos_done"] + ff["videos_done"],
            "inference_videos_total": legacy["videos_total"] + ff["videos_total"],
            "inference_per_camera": legacy["per_camera"],   # compat: disk ids, Bonsai era
            "legacy": legacy,
            "frameforge": ff,
            "roi_videos_done": sum(t["videos_done"] for t in roi_totals.values()),
            "roi_videos_total": sum(t["videos_total"] for t in roi_totals.values()),
            "roi_rate_vps": round(sum(t.get("rate_vps", 0) for t in roi_totals.values()), 2),
            "roi_per_camera": roi_totals,
        },
    }

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n--- Scan Complete ({elapsed:.1f}s) ---")
    print(f"Total dates: {len(all_date_keys)}  (new/updated: {new_dates})")
    print(f"Healthy: {status_counts['healthy']} | Degraded: {status_counts['degraded']} | "
          f"Missing: {status_counts['missing']} | In progress: {status_counts['in_progress']}")
    print(f"frameforge: {ff['days']} days, {ff['videos_done']}/{ff['videos_total']} hour-files inferred, "
          f"backlog {ff['backlog']}, workers alive {ff['workers_alive']}/4, ~{ff['sec_per_file']}s/file")
    print(f"bonsai (frozen): {legacy['videos_done']}/{legacy['videos_total']} videos")

    if args.dry_run:
        print("\nSample (last 3 dates):")
        for date_str in all_date_keys[-3:]:
            day = data["dates"][date_str]
            s = day["summary"]
            print(f"  {date_str}: {s['status']} [{s.get('rig')}] — {s['total_videos']} videos, "
                  f"{s['cameras_present']}/4 cameras, {s.get('hours_recorded', '')}h")
            for cam in PHYSICAL_CAMS:
                if cam in day["cameras"]:
                    c = day["cameras"][cam]
                    inf = c.get("inference", {}).get("videos_done", "-")
                    print(f"    {cam} [{c.get('rig')}]: {c['videos']} videos, {c['hours_count']}h, "
                          f"gaps={c.get('gaps', '-')}, inf={inf}, flags=[{', '.join(c.get('flags', []))}]")
    else:
        with tempfile.NamedTemporaryFile("w", dir=args.output.parent, suffix=".json", delete=False) as f:
            json.dump(data, f, separators=(",", ":"))
            tmp_path = f.name
        os.replace(tmp_path, args.output)
        print(f"\nWritten to {args.output} ({os.path.getsize(args.output) / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
