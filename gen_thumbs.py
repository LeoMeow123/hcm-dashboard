#!/usr/bin/env python3
# /// script
# dependencies = ["opencv-python-headless"]
# ///
"""Generate thumbnail images for HCM videos on both rigs.

Driven by hcm_daily_status.json (schema 2): every timeline entry is
[wall_hour, session, index, mb, thumb_cam], and the thumbnail for it lives at
thumbs/{thumb_cam}/{session}/{index:02d}.jpg. thumb_cam is the directory the video
is in (Bonsai: swapped disk id; frameforge: physical id = cage) and session is the
Bonsai recording-session folder or the frameforge day folder (YYYY-MM-DD-00-00-00).
The dashboard, gen_composite.py and the Slack report resolve thumbs the same way.

Usage:
    uv run gen_thumbs.py --days 30 --incremental   # daily cron
    uv run gen_thumbs.py --date 2026-10-09
    uv run gen_thumbs.py --latest                  # only the newest hour file per camera
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import cv2

SCRIPT_DIR = Path(__file__).parent
JSON_FILE = SCRIPT_DIR / "hcm_daily_status.json"
THUMB_DIR = SCRIPT_DIR / "thumbs"
OLD_ROOT = Path("/home/exx/vast/lee/2024-09-24-LeeAPP")          # Bonsai (frozen)
FF_ROOT = Path("/home/exx/vast/leo/frameforge")                   # frameforge

THUMB_WIDTH = 320
THUMB_QUALITY = 70
SEEK_SEC = 10  # extract the frame 10 s in (first keyframe interval on both rigs)
TINY_BYTES = 1_000_000


def extract_thumbnail(video_path: Path, thumb_path: Path) -> bool:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return False
    fps = cap.get(cv2.CAP_PROP_FPS) or 50
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    target_frame = min(int(fps * SEEK_SEC), max(total_frames // 2, 1))
    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        return False
    h, w = frame.shape[:2]
    frame = cv2.resize(frame, (THUMB_WIDTH, int(h * THUMB_WIDTH / w)), interpolation=cv2.INTER_AREA)
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(thumb_path), frame, [cv2.IMWRITE_JPEG_QUALITY, THUMB_QUALITY])
    return True


def _deployments() -> list[str]:
    try:
        return sorted(d for d in os.listdir(FF_ROOT) if len(d) == 10 and d[4] == "-" and (FF_ROOT / d).is_dir())
    except OSError:
        return []


def resolve_video(entry: dict, thumb_cam: str, session: str, index: int) -> Path | None:
    """Find the .mp4 behind a timeline entry on whichever rig it came from."""
    rig = entry.get("rig", "bonsai")
    name = f"{thumb_cam}.{index:02d}.mp4"
    candidates = []
    if rig in ("frameforge", "mixed"):
        deps = [entry.get("deployment")] if entry.get("deployment") else []
        ff_part = (entry.get("parts") or {}).get("frameforge") or {}
        if ff_part.get("deployment") and ff_part["deployment"] not in deps:
            deps.append(ff_part["deployment"])
        deps += [d for d in _deployments() if d not in deps]
        candidates += [FF_ROOT / d / thumb_cam / session / name for d in deps]
    if rig in ("bonsai", "mixed"):
        candidates.append(OLD_ROOT / thumb_cam / session / name)
    for p in candidates:
        if p.exists():
            return p
    return None


def jobs_for_date(data: dict, date_str: str) -> list[tuple[Path, Path]]:
    day = data["dates"].get(date_str)
    if not day:
        return []
    jobs = []
    for cam, entry in day.get("cameras", {}).items():
        for t in entry.get("timeline", []):
            session, index = t[1], int(t[2])
            thumb_cam = t[4] if len(t) > 4 else entry.get("disk_cam", cam)
            thumb = THUMB_DIR / thumb_cam / session / f"{index:02d}.jpg"
            jobs.append((entry, thumb_cam, session, index, thumb))
    return jobs


def main():
    parser = argparse.ArgumentParser(description="HCM Thumbnail Generator")
    parser.add_argument("--date", help="Process a single date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Process the last N days")
    parser.add_argument("--latest", action="store_true", help="Only the newest hour file per camera")
    parser.add_argument("--incremental", action="store_true", help="Skip existing thumbnails")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    with open(JSON_FILE) as f:
        data = json.load(f)

    if args.latest:
        jobs = []
        for cam, lf in data.get("scan_info", {}).get("latest_frames", {}).items():
            entry = data["dates"].get(lf["date"], {}).get("cameras", {}).get(cam, {"rig": "frameforge"})
            jobs.append((entry, cam, lf["session"], int(lf["hour"]), SCRIPT_DIR / lf["thumb"]))
        dates = []
    else:
        dates = sorted(data["dates"])
        if args.date:
            dates = [args.date]
        elif args.days:
            cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
            dates = [d for d in dates if d > cutoff]
        jobs = [j for d in dates for j in jobs_for_date(data, d)]
    print(f"{len(dates)} dates, {len(jobs)} videos")

    stats = {"generated": 0, "skipped": 0, "failed": 0, "missing": 0}
    todo = []
    for entry, thumb_cam, session, index, thumb in jobs:
        if args.incremental and thumb.exists():
            stats["skipped"] += 1
            continue
        video = resolve_video(entry, thumb_cam, session, index)
        if video is None:
            stats["missing"] += 1
            continue
        try:
            if video.stat().st_size < TINY_BYTES:
                stats["skipped"] += 1
                continue
        except OSError:
            stats["missing"] += 1
            continue
        todo.append((video, thumb))

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(extract_thumbnail, v, t): (v, t) for v, t in todo}
        for i, f in enumerate(as_completed(futs), 1):
            ok = False
            try:
                ok = f.result()
            except Exception as exc:
                print(f"  ERROR {futs[f][0].name}: {exc}", file=sys.stderr)
            stats["generated" if ok else "failed"] += 1
            if i % 50 == 0:
                print(f"  {i}/{len(todo)} ...")

    print(f"--- Done --- generated {stats['generated']}, skipped {stats['skipped']}, "
          f"failed {stats['failed']}, source missing {stats['missing']}")


if __name__ == "__main__":
    main()
