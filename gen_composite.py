#!/usr/bin/env python3
# /// script
# dependencies = ["opencv-python-headless", "numpy"]
# ///
"""Generate a composite visual timeline image for a date.

Creates a 4-camera × 24-hour grid with thumbnails, day/night coloring,
and camera labels. Output looks like the dashboard's visual timeline panel.

Usage:
    uv run gen_composite.py                     # latest date
    uv run gen_composite.py --date 2026-05-13   # specific date
    uv run gen_composite.py --output out.jpg    # custom output path
"""

import argparse
import json
import os
from pathlib import Path

import cv2
import numpy as np

SCRIPT_DIR = Path(__file__).parent
JSON_FILE = SCRIPT_DIR / "hcm_daily_status.json"
THUMB_DIR = SCRIPT_DIR / "thumbs"

# Camera wiring mismatch — see CAMERA_SWAP.md
CAM_PHYSICAL = {
    "cam_01": "Cam 1", "cam_02": "Cam 4",
    "cam_03": "Cam 2", "cam_04": "Cam 3",
}
CAM_ORDER = ["cam_01", "cam_03", "cam_04", "cam_02"]

# Layout
CELL_W = 52
CELL_H = 40
LABEL_W = 56
HEADER_H = 22
GAP = 1
FONT = cv2.FONT_HERSHEY_SIMPLEX

# Colors (BGR)
BG_COLOR = (23, 17, 13)          # #0d1117
NIGHT_COLOR = (20, 14, 10)       # #0a0e14
DAY_COLOR = (32, 37, 42)         # #2a2520
MISSING_NIGHT = (36, 30, 26)     # border hint
MISSING_DAY = (42, 48, 52)       # border hint
GREEN_BG = (41, 68, 13)          # #0d4429
TEXT_COLOR = (227, 237, 230)      # #e6edf3
DIM_COLOR = (158, 148, 139)      # #8b949e
TITLE_COLOR = (255, 166, 88)     # #58a6ff
DAY_BAND = (48, 64, 74)          # warm
NIGHT_BAND = (40, 30, 26)        # cool


def is_day_hour(h):
    """9:30am-9:30pm is day (lights off for mice)."""
    return 9.5 <= h < 21.5


def load_thumb(cam, session, index):
    """Load a thumbnail and resize to cell size."""
    path = THUMB_DIR / cam / session / f"{str(index).zfill(2)}.jpg"
    if not path.exists():
        return None
    img = cv2.imread(str(path))
    if img is None:
        return None
    return cv2.resize(img, (CELL_W, CELL_H), interpolation=cv2.INTER_AREA)


def draw_text(img, text, pos, scale=0.35, color=TEXT_COLOR, thickness=1):
    cv2.putText(img, text, pos, FONT, scale, color, thickness, cv2.LINE_AA)


def generate_composite(date_str, data):
    day = data["dates"].get(date_str)
    if not day:
        return None

    # Canvas size
    grid_w = 24 * (CELL_W + GAP)
    total_w = LABEL_W + grid_w + 10
    # title + day/night band + hour labels + 4 cam rows + footer
    total_h = 36 + 6 + HEADER_H + 4 * (CELL_H + GAP) + 24

    canvas = np.full((total_h, total_w, 3), BG_COLOR, dtype=np.uint8)

    # Title
    title = f"HCM Visual Timeline - {date_str}"
    status = day["summary"].get("status", "unknown")
    draw_text(canvas, title, (8, 20), scale=0.48, color=TITLE_COLOR, thickness=1)
    # Status badge
    status_colors = {"healthy": (80, 185, 63), "degraded": (34, 153, 210), "missing": (73, 81, 248)}
    sc = status_colors.get(status, DIM_COLOR)
    draw_text(canvas, status.upper(), (total_w - 100, 20), scale=0.35, color=sc)

    y_offset = 32

    # Day/night band
    for h in range(24):
        x = LABEL_W + h * (CELL_W + GAP)
        color = DAY_BAND if is_day_hour(h + 0.5) else NIGHT_BAND
        cv2.rectangle(canvas, (x, y_offset), (x + CELL_W, y_offset + 4), color, -1)
    y_offset += 6

    # Hour labels
    for h in range(24):
        x = LABEL_W + h * (CELL_W + GAP)
        label = str(h)
        text_size = cv2.getTextSize(label, FONT, 0.3, 1)[0]
        tx = x + (CELL_W - text_size[0]) // 2
        draw_text(canvas, label, (tx, y_offset + 14), scale=0.3, color=DIM_COLOR)
    y_offset += HEADER_H

    # Subtitle
    subtitle_y = y_offset - 4
    sub = "Day (lights off) 9:30-21:30 | Night (lights on) 21:30-9:30"
    draw_text(canvas, sub, (LABEL_W, subtitle_y), scale=0.25, color=DIM_COLOR)

    # Camera rows
    for cam in CAM_ORDER:
        label = CAM_PHYSICAL[cam]
        c = day.get("cameras", {}).get(cam)

        # Camera label
        draw_text(canvas, label, (4, y_offset + CELL_H // 2 + 4), scale=0.38, color=TEXT_COLOR)

        # Build hour map from timeline
        hour_map = {}
        if c and c.get("timeline"):
            for t in c["timeline"]:
                h = min(t[0], 23)
                if h not in hour_map:
                    hour_map[h] = {"session": t[1], "index": t[2]}

        for h in range(24):
            x = LABEL_W + h * (CELL_W + GAP)
            is_day = is_day_hour(h + 0.5)

            if h in hour_map:
                entry = hour_map[h]
                thumb = load_thumb(cam, entry["session"], entry["index"])
                if thumb is not None:
                    canvas[y_offset:y_offset + CELL_H, x:x + CELL_W] = thumb
                    # Thin green border
                    cv2.rectangle(canvas, (x, y_offset), (x + CELL_W - 1, y_offset + CELL_H - 1), GREEN_BG, 1)
                else:
                    # Has video but no thumb
                    bg = DAY_COLOR if is_day else NIGHT_COLOR
                    cv2.rectangle(canvas, (x, y_offset), (x + CELL_W, y_offset + CELL_H), bg, -1)
                    draw_text(canvas, "?", (x + CELL_W // 2 - 3, y_offset + CELL_H // 2 + 4),
                              scale=0.35, color=DIM_COLOR)
            else:
                # Missing hour
                bg = MISSING_DAY if is_day else MISSING_NIGHT
                cv2.rectangle(canvas, (x, y_offset), (x + CELL_W, y_offset + CELL_H), bg, -1)
                # Dashed border effect
                for dx in range(0, CELL_W, 4):
                    cv2.line(canvas, (x + dx, y_offset), (x + min(dx + 2, CELL_W), y_offset),
                             DIM_COLOR if is_day else (50, 40, 36), 1)
                    cv2.line(canvas, (x + dx, y_offset + CELL_H - 1),
                             (x + min(dx + 2, CELL_W), y_offset + CELL_H - 1),
                             DIM_COLOR if is_day else (50, 40, 36), 1)

        y_offset += CELL_H + GAP

    # Footer
    y_offset += 4
    summary = day["summary"]
    footer = (f"{summary['total_videos']} videos | {summary['total_sessions']} sessions | "
              f"{summary['cameras_present']}/4 cameras")
    draw_text(canvas, footer, (LABEL_W, y_offset + 10), scale=0.3, color=DIM_COLOR)
    draw_text(canvas, "leomeow123.github.io/hcm-dashboard", (total_w - 240, y_offset + 10),
              scale=0.28, color=DIM_COLOR)

    return canvas


def main():
    parser = argparse.ArgumentParser(description="HCM Visual Timeline Composite")
    parser.add_argument("--date", help="Date to render (YYYY-MM-DD). Default: latest.")
    parser.add_argument("--output", "-o", help="Output path. Default: composite_{date}.jpg")
    args = parser.parse_args()

    with open(JSON_FILE) as f:
        data = json.load(f)

    sorted_dates = sorted(data["dates"].keys())
    if not sorted_dates:
        print("No dates in JSON")
        return

    date_str = args.date or sorted_dates[-1]
    if date_str not in data["dates"]:
        print(f"Date {date_str} not found in data")
        return

    img = generate_composite(date_str, data)
    if img is None:
        print(f"No data for {date_str}")
        return

    output = args.output or str(SCRIPT_DIR / f"composite_{date_str}.jpg")
    cv2.imwrite(output, img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f"Saved: {output} ({os.path.getsize(output) // 1024}KB)")


if __name__ == "__main__":
    main()
