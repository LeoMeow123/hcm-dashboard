#!/usr/bin/env python3
"""HCM Daily Scanner — walks VAST and produces hcm_daily_status.json.

Scans the HCM recording directory, groups sessions by date, computes
per-camera metrics, and flags recording issues. Designed to run daily
via cron with incremental updates.

Usage:
    python3 scan_daily.py              # incremental (only new dates)
    python3 scan_daily.py --full       # full rescan
    python3 scan_daily.py --dry-run    # print without writing JSON
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# --- Configuration ---

DATA_ROOT = Path("/home/exx/vast/lee/2024-09-24-LeeAPP")
INFERENCE_ROOT = Path("/home/exx/vast/leo/datasets/inference-Kuo-Fen-HCM")
CAMERAS = ["cam_01", "cam_02", "cam_03", "cam_04"]
OUTPUT_FILE = Path(__file__).parent / "hcm_daily_status.json"

# Thresholds
TINY_FILE_BYTES = 1_000_000  # 1MB — crash artifact
EXPECTED_VIDEOS_PER_DAY = 24
CRASH_SESSION_THRESHOLD = 2  # >1 session means at least one crash
PREDICTION_RE = re.compile(r"^cam_\d{2}\.\d{2}\.predictions\.slp$")

# Regex patterns
SESSION_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})-(\d{2})$")
VIDEO_RE = re.compile(r"^cam_\d{2}\.(\d{2})\.mp4$")


def scan_camera(camera: str, after_date: str | None = None) -> dict[str, dict]:
    """Scan all sessions for a camera, grouped by date.

    Args:
        camera: Camera name (e.g. "cam_01")
        after_date: Only scan sessions with date > this (YYYY-MM-DD).
                    None = scan everything.

    Returns:
        {date_str: {sessions, videos, total_bytes, hours, empty_sessions,
                    zero_byte, tiny_files, video_list}}
    """
    cam_dir = DATA_ROOT / camera
    if not cam_dir.is_dir():
        print(f"  WARNING: {cam_dir} not found, skipping", file=sys.stderr)
        return {}

    # Group sessions by date
    dates: dict[str, list[Path]] = defaultdict(list)

    try:
        entries = sorted(os.listdir(cam_dir))
    except OSError as e:
        print(f"  ERROR listing {cam_dir}: {e}", file=sys.stderr)
        return {}

    for entry in entries:
        m = SESSION_RE.match(entry)
        if not m:
            continue
        date_str = m.group(1)
        if after_date and date_str <= after_date:
            continue
        dates[date_str].append(cam_dir / entry)

    # Process each date
    results = {}
    for date_str in sorted(dates):
        sessions = dates[date_str]
        total_videos = 0
        total_bytes = 0
        hours_covered = set()
        empty_sessions = 0
        zero_byte = 0
        tiny_files = 0
        video_details = []

        for session_dir in sessions:
            try:
                files = os.listdir(session_dir)
            except OSError:
                empty_sessions += 1
                continue

            mp4s = [f for f in files if f.endswith(".mp4")]
            if not mp4s:
                empty_sessions += 1
                continue

            for mp4 in mp4s:
                vm = VIDEO_RE.match(mp4)
                if not vm:
                    continue

                video_idx = int(vm.group(1))
                filepath = session_dir / mp4

                try:
                    size = os.path.getsize(filepath)
                except OSError:
                    size = 0

                total_videos += 1
                total_bytes += size
                hours_covered.add(video_idx)

                if size == 0:
                    zero_byte += 1
                elif size < TINY_FILE_BYTES:
                    tiny_files += 1

                # Compute wall-clock hour from session start + video index
                sess_match = SESSION_RE.match(session_dir.name)
                if sess_match:
                    start_hour = int(sess_match.group(2))
                    start_min = int(sess_match.group(3))
                    wall_hour = start_hour + video_idx
                    # Approximate: if session starts at :30+, the video
                    # spans into the next clock hour
                    wall_hour_end = wall_hour + 1
                else:
                    wall_hour = video_idx
                    wall_hour_end = video_idx + 1

                video_details.append({
                    "session": session_dir.name,
                    "file": mp4,
                    "index": video_idx,
                    "bytes": size,
                    "wall_hour": wall_hour,
                    "wall_hour_end": min(wall_hour_end, 24),
                    "tiny": size < TINY_FILE_BYTES,
                })

        # Build compact timeline: [wall_hour, session, index] tuples as arrays
        # Sorted by wall_hour. Excludes crash artifacts.
        timeline = sorted(
            [
                [v["wall_hour"], v["session"], v["index"]]
                for v in video_details
                if not v["tiny"]
            ],
            key=lambda x: x[0],
        )

        results[date_str] = {
            "sessions": len(sessions),
            "videos": total_videos,
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / 1_048_576, 1),
            "hours_covered": sorted(hours_covered),
            "hours_count": len(hours_covered),
            "empty_sessions": empty_sessions,
            "zero_byte": zero_byte,
            "tiny_files": tiny_files,
            "timeline": timeline,
        }

    return results


def scan_inference(camera: str, skip_dates: set[str] | None = None) -> dict[str, dict]:
    """Scan inference output for a camera, grouped by date.

    Args:
        camera: Camera name (e.g. "cam_01")
        skip_dates: Dates to skip (already complete). None = scan all.

    Returns:
        {date_str: {sessions_done: int, videos_done: int}}
    """
    inf_dir = INFERENCE_ROOT / camera
    if not inf_dir.is_dir():
        return {}

    try:
        entries = sorted(os.listdir(inf_dir))
    except OSError:
        return {}

    dates: dict[str, list[str]] = defaultdict(list)
    skipped = 0
    for entry in entries:
        m = SESSION_RE.match(entry)
        if not m:
            continue
        date_str = m.group(1)
        if skip_dates and date_str in skip_dates:
            skipped += 1
            continue
        dates[date_str].append(entry)

    if skipped:
        print(f"    Skipped {skipped} sessions ({len(skip_dates)} complete dates)")

    results = {}
    for date_str, sessions in dates.items():
        total_slp = 0
        for session in sessions:
            session_dir = inf_dir / session
            try:
                files = os.listdir(session_dir)
            except OSError:
                continue
            total_slp += sum(1 for f in files if PREDICTION_RE.match(f))

        results[date_str] = {
            "sessions_done": len(sessions),
            "videos_done": total_slp,
        }

    return results


def get_inference_skip_dates(data: dict) -> dict[str, set[str]]:
    """Extract per-camera sets of dates to skip in inference scan.

    Skip a date for a camera if:
    - Inference is complete (inf_vids >= rec_vids > 0), OR
    - No recording data exists (can't compare, rescanning is pointless)
    """
    cam_skip: dict[str, set[str]] = {c: set() for c in CAMERAS}
    for date_str, day_data in data.get("dates", {}).items():
        for cam in CAMERAS:
            cam_entry = day_data.get("cameras", {}).get(cam, {})
            rec_vids = cam_entry.get("videos", 0)
            inf = cam_entry.get("inference", {})
            inf_vids = inf.get("videos_done", 0)
            if rec_vids == 0:
                # No recording data — nothing to compare against
                cam_skip[cam].add(date_str)
            elif inf_vids >= rec_vids:
                # Inference complete
                cam_skip[cam].add(date_str)
    return cam_skip


def check_transfer_freshness() -> dict:
    """Check the latest session date per camera to detect transfer gaps."""
    result = {}
    today = datetime.now().strftime("%Y-%m-%d")

    for camera in CAMERAS:
        cam_dir = DATA_ROOT / camera
        try:
            entries = sorted(os.listdir(cam_dir))
        except OSError:
            result[camera] = {"latest_session": None, "latest_date": None, "days_behind": None}
            continue

        latest = None
        for entry in reversed(entries):
            m = SESSION_RE.match(entry)
            if m:
                latest = entry
                break

        if latest:
            latest_date = SESSION_RE.match(latest).group(1)
            try:
                delta = (datetime.strptime(today, "%Y-%m-%d") -
                         datetime.strptime(latest_date, "%Y-%m-%d")).days
            except ValueError:
                delta = None
            result[camera] = {
                "latest_session": latest,
                "latest_date": latest_date,
                "days_behind": delta,
            }
        else:
            result[camera] = {"latest_session": None, "latest_date": None, "days_behind": None}

    return result


def compute_flags(day: dict) -> list[str]:
    """Compute issue flags for a single date+camera entry."""
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
    if day["hours_count"] == EXPECTED_VIDEOS_PER_DAY and day["sessions"] == 1:
        flags.append("healthy")
    return flags


def compute_day_summary(cam_data: dict[str, dict]) -> dict:
    """Compute cross-camera summary for a date."""
    cameras_present = [c for c in CAMERAS if c in cam_data and "videos" in cam_data[c]]
    cameras_missing = [c for c in CAMERAS if c not in cam_data or "videos" not in cam_data[c]]

    total_videos = sum(cam_data[c]["videos"] for c in cameras_present)
    total_bytes = sum(cam_data[c]["total_bytes"] for c in cameras_present)
    total_sessions = sum(cam_data[c]["sessions"] for c in cameras_present)

    # All cameras have full coverage?
    all_healthy = all(
        cam_data.get(c, {}).get("hours_count", 0) == EXPECTED_VIDEOS_PER_DAY
        and cam_data.get(c, {}).get("sessions", 0) == 1
        for c in CAMERAS
    )

    # Worst camera stats
    max_sessions = max(
        (cam_data[c]["sessions"] for c in cameras_present), default=0
    )

    return {
        "cameras_present": len(cameras_present),
        "cameras_missing": cameras_missing,
        "total_videos": total_videos,
        "total_mb": round(total_bytes / 1_048_576, 1),
        "total_sessions": total_sessions,
        "max_sessions_any_cam": max_sessions,
        "status": "healthy" if all_healthy else "degraded" if total_videos > 0 else "missing",
    }


def load_existing(path: Path) -> dict:
    """Load existing JSON output, or return empty structure."""
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: Could not load {path}: {e}", file=sys.stderr)
    return {"scan_info": {}, "dates": {}}


def find_latest_date(data: dict) -> str | None:
    """Find the most recent date in existing data."""
    dates = list(data.get("dates", {}).keys())
    return max(dates) if dates else None


def main():
    parser = argparse.ArgumentParser(description="HCM Daily Scanner")
    parser.add_argument("--full", action="store_true", help="Full rescan (ignore existing data)")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE, help="Output JSON path")
    parser.add_argument("--days", type=int, help="Only scan the last N days")
    args = parser.parse_args()

    # Load existing or start fresh
    if args.full:
        data = {"scan_info": {}, "dates": {}}
        after_date = None
        print("Full scan mode — scanning all dates")
    else:
        data = load_existing(args.output)
        after_date = find_latest_date(data)
        if after_date:
            print(f"Incremental scan — scanning dates after {after_date}")
        else:
            print("No existing data — scanning all dates")

    if args.days:
        cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        if after_date is None or cutoff > after_date:
            after_date = cutoff
        print(f"Limited to last {args.days} days (after {after_date})")

    # Build skip sets for inference (dates already complete per camera)
    if args.full:
        cam_complete = {c: set() for c in CAMERAS}
    else:
        cam_complete = get_inference_skip_dates(data)
        total_skip = sum(len(v) for v in cam_complete.values())
        if total_skip:
            print(f"Inference: skipping {total_skip} camera-date pairs (complete or no recording data)")

    # Scan each camera (recording + inference)
    all_cam_data: dict[str, dict[str, dict]] = {}
    all_inf_data: dict[str, dict[str, dict]] = {}
    for camera in CAMERAS:
        print(f"Scanning {camera} recordings...")
        cam_results = scan_camera(camera, after_date)
        all_cam_data[camera] = cam_results
        print(f"  Found {len(cam_results)} dates with data")

        print(f"Scanning {camera} inference...")
        inf_results = scan_inference(camera, cam_complete.get(camera))
        all_inf_data[camera] = inf_results
        print(f"  Found {len(inf_results)} dates with inference")

    # Check transfer freshness
    print("Checking transfer freshness...")
    transfer = check_transfer_freshness()
    for cam, info in transfer.items():
        behind = info["days_behind"]
        status = "OK" if behind is not None and behind <= 1 else "STALE" if behind else "UNKNOWN"
        print(f"  {cam}: latest {info['latest_date']} ({behind}d behind) — {status}")

    # Merge into output structure
    all_dates = set()
    for cam_results in all_cam_data.values():
        all_dates.update(cam_results.keys())

    # Collect all dates from both recording and inference
    for cam_results in all_inf_data.values():
        all_dates.update(cam_results.keys())

    new_dates = 0
    for date_str in sorted(all_dates):
        cam_entries = {}
        for camera in CAMERAS:
            if date_str in all_cam_data[camera]:
                entry = all_cam_data[camera][date_str]
                entry["flags"] = compute_flags(entry)
                cam_entries[camera] = entry

            # Merge inference data
            if date_str in all_inf_data.get(camera, {}):
                inf = all_inf_data[camera][date_str]
                if camera in cam_entries:
                    cam_entries[camera]["inference"] = inf
                else:
                    cam_entries.setdefault(camera, {})
                    cam_entries[camera]["inference"] = inf

        summary = compute_day_summary(cam_entries)

        # Inference summary for this date
        inf_sessions = sum(
            all_inf_data.get(c, {}).get(date_str, {}).get("sessions_done", 0)
            for c in CAMERAS
        )
        inf_videos = sum(
            all_inf_data.get(c, {}).get(date_str, {}).get("videos_done", 0)
            for c in CAMERAS
        )
        rec_sessions = sum(
            cam_entries.get(c, {}).get("sessions", 0) for c in CAMERAS
        )
        rec_videos = sum(
            cam_entries.get(c, {}).get("videos", 0) for c in CAMERAS
        )
        summary["inference"] = {
            "sessions_done": inf_sessions,
            "videos_done": inf_videos,
            "sessions_total": rec_sessions,
            "videos_total": rec_videos,
            "complete": inf_sessions >= rec_sessions > 0 if rec_sessions else False,
        }

        data.setdefault("dates", {})[date_str] = {
            "summary": summary,
            "cameras": cam_entries,
        }
        new_dates += 1

    # Update scan info
    all_date_keys = sorted(data.get("dates", {}).keys())
    data["scan_info"] = {
        "last_scan": datetime.now().isoformat(),
        "scan_mode": "full" if args.full else "incremental",
        "new_dates_scanned": new_dates,
        "total_dates": len(all_date_keys),
        "date_range": {
            "first": all_date_keys[0] if all_date_keys else None,
            "last": all_date_keys[-1] if all_date_keys else None,
        },
        "data_root": str(DATA_ROOT),
    }

    # Compute overall stats
    total_healthy = sum(
        1 for d in data["dates"].values()
        if d["summary"]["status"] == "healthy"
    )
    total_degraded = sum(
        1 for d in data["dates"].values()
        if d["summary"]["status"] == "degraded"
    )
    total_missing = sum(
        1 for d in data["dates"].values()
        if d["summary"]["status"] == "missing"
    )
    # Inference totals
    inf_complete_dates = sum(
        1 for d in data["dates"].values()
        if d["summary"].get("inference", {}).get("complete", False)
    )
    total_inf_videos = sum(
        d["summary"].get("inference", {}).get("videos_done", 0)
        for d in data["dates"].values()
    )
    total_rec_videos = sum(
        d["summary"].get("inference", {}).get("videos_total", 0)
        for d in data["dates"].values()
    )

    data["scan_info"]["overall"] = {
        "healthy_days": total_healthy,
        "degraded_days": total_degraded,
        "missing_days": total_missing,
        "inference_complete_dates": inf_complete_dates,
        "inference_videos_done": total_inf_videos,
        "inference_videos_total": total_rec_videos,
    }
    data["scan_info"]["transfer"] = transfer

    # Print summary
    print(f"\n--- Scan Complete ---")
    print(f"Total dates: {len(all_date_keys)}")
    print(f"New/updated: {new_dates}")
    print(f"Healthy: {total_healthy} | Degraded: {total_degraded} | Missing: {total_missing}")
    print(f"Inference: {inf_complete_dates} dates complete, "
          f"{total_inf_videos}/{total_rec_videos} videos "
          f"({total_inf_videos/max(total_rec_videos,1)*100:.1f}%)")
    for cam, info in transfer.items():
        print(f"Transfer {cam}: {info['latest_date']} ({info['days_behind']}d behind)")

    if args.dry_run:
        # Print a sample
        print(f"\nSample (last 3 dates):")
        for date_str in all_date_keys[-3:]:
            day = data["dates"][date_str]
            s = day["summary"]
            print(f"  {date_str}: {s['status']} — {s['total_videos']} videos, "
                  f"{s['total_sessions']} sessions, {s['cameras_present']}/4 cameras")
            for cam in CAMERAS:
                if cam in day["cameras"]:
                    c = day["cameras"][cam]
                    flags = ", ".join(c["flags"]) if c["flags"] else "none"
                    print(f"    {cam}: {c['videos']} videos, {c['sessions']} sessions, "
                          f"{c['total_mb']}MB, flags=[{flags}]")
    else:
        with open(args.output, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        print(f"\nWritten to {args.output}")
        print(f"File size: {os.path.getsize(args.output) / 1_048_576:.1f} MB")


if __name__ == "__main__":
    main()
