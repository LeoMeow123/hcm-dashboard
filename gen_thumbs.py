#!/usr/bin/env python3
# /// script
# dependencies = ["opencv-python-headless"]
# ///
"""Generate thumbnail images for HCM videos.

Extracts a single frame (10 seconds in) from each video and saves as
a small JPEG. Thumbnails are stored in thumbs/{cam}/{session}/{index}.jpg

Usage:
    uv run gen_thumbs.py                      # generate for all dates
    uv run gen_thumbs.py --date 2024-12-01    # single date
    uv run gen_thumbs.py --days 7             # last 7 days
    uv run gen_thumbs.py --incremental        # skip existing thumbs
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import cv2

DATA_ROOT = Path("/home/exx/vast/lee/2024-09-24-LeeAPP")
THUMB_DIR = Path(__file__).parent / "thumbs"
CAMERAS = ["cam_01", "cam_02", "cam_03", "cam_04"]

SESSION_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})-(\d{2})$")
VIDEO_RE = re.compile(r"^(cam_\d{2})\.(\d{2})\.mp4$")

THUMB_WIDTH = 320
THUMB_QUALITY = 70
SEEK_SEC = 10  # extract frame at 10 seconds in


def extract_thumbnail(video_path: Path, thumb_path: Path) -> bool:
    """Extract a single frame from a video and save as JPEG thumbnail."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return False

    fps = cap.get(cv2.CAP_PROP_FPS) or 50
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Seek to SEEK_SEC or middle of video if too short
    target_frame = min(int(fps * SEEK_SEC), max(total_frames // 2, 1))
    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        return False

    # Resize maintaining aspect ratio
    h, w = frame.shape[:2]
    new_w = THUMB_WIDTH
    new_h = int(h * new_w / w)
    frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(thumb_path), frame, [cv2.IMWRITE_JPEG_QUALITY, THUMB_QUALITY])
    return True


def get_dates_to_process(args) -> list[str]:
    """Determine which dates to process based on args."""
    if args.date:
        return [args.date]

    # Collect all dates from cam_01
    dates = set()
    cam_dir = DATA_ROOT / "cam_01"
    for entry in os.listdir(cam_dir):
        m = SESSION_RE.match(entry)
        if m:
            dates.add(m.group(1))

    dates = sorted(dates)

    if args.days:
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        dates = [d for d in dates if d > cutoff]

    return dates


def process_date(date_str: str, incremental: bool = False) -> dict:
    """Generate thumbnails for all videos on a given date."""
    stats = {"generated": 0, "skipped": 0, "failed": 0, "total": 0}

    for camera in CAMERAS:
        cam_dir = DATA_ROOT / camera
        if not cam_dir.is_dir():
            continue

        for entry in sorted(os.listdir(cam_dir)):
            m = SESSION_RE.match(entry)
            if not m or m.group(1) != date_str:
                continue

            session_dir = cam_dir / entry
            try:
                files = sorted(os.listdir(session_dir))
            except OSError:
                continue

            for f in files:
                vm = VIDEO_RE.match(f)
                if not vm:
                    continue

                stats["total"] += 1
                video_path = session_dir / f
                # thumbs/cam_01/2024-12-01-00-01-05/00.jpg
                thumb_path = THUMB_DIR / camera / entry / f"{vm.group(2)}.jpg"

                if incremental and thumb_path.exists():
                    stats["skipped"] += 1
                    continue

                # Skip tiny files (crash artifacts < 1MB)
                if video_path.stat().st_size < 1_000_000:
                    stats["skipped"] += 1
                    continue

                if extract_thumbnail(video_path, thumb_path):
                    stats["generated"] += 1
                else:
                    stats["failed"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(description="HCM Thumbnail Generator")
    parser.add_argument("--date", help="Process a single date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Process last N days")
    parser.add_argument("--incremental", action="store_true", help="Skip existing thumbnails")
    args = parser.parse_args()

    dates = get_dates_to_process(args)
    print(f"Processing {len(dates)} dates...")

    total_stats = {"generated": 0, "skipped": 0, "failed": 0, "total": 0}

    for i, date_str in enumerate(dates):
        stats = process_date(date_str, incremental=args.incremental)
        for k in total_stats:
            total_stats[k] += stats[k]
        if stats["generated"] > 0 or (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(dates)}] {date_str}: "
                  f"+{stats['generated']} generated, {stats['skipped']} skipped, "
                  f"{stats['failed']} failed")

    print(f"\n--- Done ---")
    print(f"Total: {total_stats['total']} videos")
    print(f"Generated: {total_stats['generated']}")
    print(f"Skipped: {total_stats['skipped']}")
    print(f"Failed: {total_stats['failed']}")


if __name__ == "__main__":
    main()
