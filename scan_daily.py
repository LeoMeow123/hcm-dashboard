#!/usr/bin/env python3
"""HCM Daily Scanner — walks VAST and produces hcm_daily_status.json.

Scans the HCM recording directory, groups sessions by date, computes
per-camera metrics, and flags recording issues. Designed to run daily
via cron with incremental updates.

Uses thread-pool parallelism for VAST I/O (network filesystem where
latency dominates). With 192 CPU cores on exx this is safe — threads
bypass the GIL for I/O-bound work.

Usage:
    python3 scan_daily.py              # incremental (only new dates)
    python3 scan_daily.py --full       # full rescan
    python3 scan_daily.py --dry-run    # print without writing JSON
    python3 scan_daily.py --workers 128  # tune parallelism
"""

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

# --- Configuration ---

DATA_ROOT = Path("/home/exx/vast/lee/2024-09-24-LeeAPP")
INFERENCE_ROOT = Path("/home/exx/vast/leo/datasets/inference-Kuo-Fen-HCM")
INFERENCE_LOG_DIR = Path("/home/exx/vast/leo/2026-01-28-HCM-APP/scratch/2026-02-26-inference-benchmark/inference_log")
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

# Default parallelism: threads per camera for session-level I/O.
# Keep conservative to avoid starving inference and other VAST I/O.
# Full scan: 16 threads × 4 cameras = 64 total concurrent VAST ops.
# Daily incremental only touches a few dates, so speed is fine either way.
DEFAULT_WORKERS = 16


def _scan_one_session(session_dir: Path) -> tuple[list[dict], bool]:
    """Scan a single session directory for video files.

    Returns (video_details, is_empty).
    Thread-safe: no shared mutable state.
    """
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
            "session": session_dir.name,
            "file": mp4,
            "index": idx,
            "bytes": size,
            "wall_hour": wall_hour,
            "wall_hour_end": min(wall_hour + 1, 24),
            "tiny": size < TINY_FILE_BYTES,
        })
    return (videos, False)


def scan_camera(camera: str, after_date: str | None = None,
                workers: int = DEFAULT_WORKERS) -> dict[str, dict]:
    """Scan all sessions for a camera, grouped by date.

    Args:
        camera: Camera name (e.g. "cam_01")
        after_date: Only scan sessions with date > this (YYYY-MM-DD).
                    None = scan everything.
        workers: Thread pool size for parallel session I/O.

    Returns:
        {date_str: {sessions, videos, total_bytes, hours, empty_sessions,
                    zero_byte, tiny_files, video_list}}
    """
    cam_dir = DATA_ROOT / camera
    if not cam_dir.is_dir():
        print(f"  WARNING: {cam_dir} not found, skipping", file=sys.stderr)
        return {}

    # Phase 1: List sessions and group by date (single os.listdir — fast)
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

    # Phase 2: Process all sessions in parallel via thread pool
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

    # Phase 3: Aggregate per-date results
    results = {}
    for date_str in sorted(dates):
        all_vids = date_videos.get(date_str, [])
        total_bytes = sum(v["bytes"] for v in all_vids)
        hours_covered = {v["wall_hour"] for v in all_vids if 0 <= v["wall_hour"] < 24}

        # Non-tiny videos for timeline and duration estimation
        good_vids = [v for v in all_vids if not v["tiny"]]

        timeline = sorted(
            [[v["wall_hour"], v["session"], v["index"],
              round(v["bytes"] / 1_048_576, 1)]
             for v in good_vids],
            key=lambda x: x[0],
        )

        # Estimate fractional hours of actual coverage.
        # Compare each video's size to the median to estimate duration.
        # A full ~1hr video ≈ median size; a 5-min crash leftover ≈ small.
        good_sizes = [v["bytes"] for v in good_vids if v["bytes"] > 0]
        if good_sizes:
            median_bytes = sorted(good_sizes)[len(good_sizes) // 2]
        else:
            median_bytes = 75 * 1_048_576  # fallback 75MB

        fractional_hours = 0.0
        for v in good_vids:
            if v["bytes"] <= 0 or v["wall_hour"] < 0 or v["wall_hour"] >= 24:
                continue
            est_minutes = min(60, max(1, (v["bytes"] / median_bytes) * 60))
            fractional_hours += est_minutes / 60

        results[date_str] = {
            "sessions": len(dates[date_str]),
            "videos": len(all_vids),
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / 1_048_576, 1),
            "hours_covered": sorted(hours_covered),
            "hours_count": round(fractional_hours, 1),
            "empty_sessions": date_empty.get(date_str, 0),
            "zero_byte": sum(1 for v in all_vids if v["bytes"] == 0),
            "tiny_files": sum(1 for v in all_vids if 0 < v["bytes"] < TINY_FILE_BYTES),
            "timeline": timeline,
        }

    return results


def _scan_one_inference_session(session_dir: Path) -> int:
    """Count prediction .slp files in a single inference session directory."""
    try:
        files = os.listdir(session_dir)
    except OSError:
        return 0
    return sum(1 for f in files if PREDICTION_RE.match(f))


def scan_inference(camera: str, skip_dates: set[str] | None = None,
                   workers: int = DEFAULT_WORKERS) -> dict[str, dict]:
    """Scan inference output for a camera, grouped by date.

    Args:
        camera: Camera name (e.g. "cam_01")
        skip_dates: Dates to skip (already complete). None = scan all.
        workers: Thread pool size for parallel session I/O.

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

    if not dates:
        return {}

    # Process sessions in parallel
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

    results = {}
    for date_str, sessions in dates.items():
        results[date_str] = {
            "sessions_done": len(sessions),
            "videos_done": date_slp.get(date_str, 0),
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


def get_inference_totals() -> dict:
    """Read inference totals from GPU dashboard JSONL logs.

    These logs have accurate videos_done/videos_total per camera,
    unlike our own scan which may miss dates.
    """
    totals = {}
    for camera in CAMERAS:
        log_file = INFERENCE_LOG_DIR / f"{camera}_progress.jsonl"
        if not log_file.exists():
            continue
        try:
            with open(log_file) as f:
                lines = f.readlines()
            # Read last valid JSON entry
            for line in reversed(lines):
                try:
                    d = json.loads(line.strip())
                    totals[camera] = {
                        "videos_done": d.get("videos_done", 0),
                        "videos_total": d.get("videos_total", 0),
                        "sessions_done": d.get("sessions_done", 0),
                        "sessions_total": d.get("sessions_total", 0),
                    }
                    break
                except (json.JSONDecodeError, KeyError):
                    continue
        except OSError:
            continue
    return totals


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
    if day["hours_count"] >= 23 and day["sessions"] == 1:
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
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Thread pool workers per camera (default: {DEFAULT_WORKERS})")
    args = parser.parse_args()

    t0 = datetime.now()
    workers = args.workers
    print(f"Using {workers} I/O threads per camera ({workers * 4} total across 4 cameras)")

    # How many recent days to always rescan (catches late-arriving robocopy data)
    RESCAN_DAYS = 3

    # Load existing or start fresh
    if args.full:
        data = {"scan_info": {}, "dates": {}}
        after_date = None
        rescan_dates = set()
        print("Full scan mode — scanning all dates")
    else:
        data = load_existing(args.output)
        after_date = find_latest_date(data)
        if after_date:
            print(f"Incremental scan — scanning dates after {after_date}")
        else:
            print("No existing data — scanning all dates")

        # Always rescan recent days (robocopy adds files 1-2 days late)
        # +1 because scan_camera uses strict > (skips dates <= after_date)
        rescan_cutoff = (datetime.now() - timedelta(days=RESCAN_DAYS + 1)).strftime("%Y-%m-%d")
        rescan_dates = {d for d in data.get("dates", {}) if d >= rescan_cutoff}
        if rescan_dates:
            print(f"Rescanning {len(rescan_dates)} recent dates (last {RESCAN_DAYS} days)")
            # Set after_date to before the rescan window so these dates get picked up
            if after_date and rescan_cutoff < after_date:
                after_date = rescan_cutoff

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

    # --- Scan recordings: 4 cameras in parallel, each with thread pool ---
    print("Scanning recordings (4 cameras in parallel)...")
    all_cam_data: dict[str, dict[str, dict]] = {}
    with ThreadPoolExecutor(max_workers=4) as cam_pool:
        futures = {
            cam_pool.submit(scan_camera, cam, after_date, workers): cam
            for cam in CAMERAS
        }
        for f in as_completed(futures):
            cam = futures[f]
            all_cam_data[cam] = f.result()
            print(f"  {cam}: {len(all_cam_data[cam])} dates")

    t_rec = datetime.now()
    print(f"  Recording scan: {(t_rec - t0).total_seconds():.1f}s")

    # --- Scan inference: 4 cameras in parallel ---
    print("Scanning inference (4 cameras in parallel)...")
    all_inf_data: dict[str, dict[str, dict]] = {}
    with ThreadPoolExecutor(max_workers=4) as cam_pool:
        futures = {
            cam_pool.submit(scan_inference, cam, cam_complete.get(cam), workers): cam
            for cam in CAMERAS
        }
        for f in as_completed(futures):
            cam = futures[f]
            all_inf_data[cam] = f.result()
            print(f"  {cam}: {len(all_inf_data[cam])} dates")

    t_inf = datetime.now()
    print(f"  Inference scan: {(t_inf - t_rec).total_seconds():.1f}s")

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
    # Inference totals — use GPU dashboard logs (accurate global counts)
    inf_totals = get_inference_totals()
    total_inf_videos = sum(t["videos_done"] for t in inf_totals.values())
    total_rec_videos = sum(t["videos_total"] for t in inf_totals.values())
    inf_complete_dates = sum(
        1 for d in data["dates"].values()
        if d["summary"].get("inference", {}).get("complete", False)
    )

    data["scan_info"]["overall"] = {
        "healthy_days": total_healthy,
        "degraded_days": total_degraded,
        "missing_days": total_missing,
        "inference_complete_dates": inf_complete_dates,
        "inference_videos_done": total_inf_videos,
        "inference_videos_total": total_rec_videos,
        "inference_per_camera": {cam: inf_totals.get(cam, {}) for cam in CAMERAS},
    }
    data["scan_info"]["transfer"] = transfer

    elapsed = (datetime.now() - t0).total_seconds()

    # Print summary
    print(f"\n--- Scan Complete ({elapsed:.1f}s) ---")
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
        # Atomic write: write to temp file then rename, so interrupted
        # scans can't leave a corrupted JSON for the dashboard.
        with tempfile.NamedTemporaryFile(
            "w", dir=args.output.parent, suffix=".json", delete=False
        ) as f:
            json.dump(data, f, separators=(",", ":"))
            tmp_path = f.name
        os.replace(tmp_path, args.output)
        print(f"\nWritten to {args.output}")
        print(f"File size: {os.path.getsize(args.output) / 1_048_576:.1f} MB")


if __name__ == "__main__":
    main()
