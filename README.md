# HCM Dashboard

**https://leomeow123.github.io/hcm-dashboard/**

Daily health monitoring for the Home Cage Monitoring (HCM) recording pipeline. Scans VAST for recording issues, tracks SLEAP inference progress, monitors data transfer, and reports via web dashboard + Slack.

## Problem

The HCM recording computer (robocopy to VAST) is unstable. Common issues:
- Recording crashes mid-day and restarts, producing fragmented sessions
- Days with 50-200+ restart sessions instead of 1 clean 24hr session
- Missing days with no recordings at all
- Empty session folders (0 videos)
- Tiny files (<1MB) from crash artifacts

Nobody notices these problems until weeks later when analysis fails. This dashboard catches them daily.

## Key Numbers

| Metric | Value |
|--------|-------|
| Recording period | 2024-09-24 to present (~500 days) |
| Inference progress | **73.1%** (46,730 / 63,897 videos) |
| Healthy days | 58 |
| Degraded days | 438 |

## Camera Wiring Mismatch

Physical camera labels do not match the Bonsai software IDs on disk. **cam_01 is correct; cam_02/03/04 are cyclically rotated.** See [CAMERA_SWAP.md](CAMERA_SWAP.md) for full mapping and instructions.

| Directory on disk | Physical Camera |
|-------------------|-----------------|
| cam_01/ | Cam 1 (correct) |
| cam_02/ | **Cam 4** |
| cam_03/ | **Cam 2** |
| cam_04/ | **Cam 3** |

The dashboard displays corrected physical camera labels. Files on disk are **never renamed** — the mapping is applied at display time only.

## Dashboard Features

### Recording Calendar
Color-coded heatmap showing every recording day:
- **Green**: Healthy (24hr, all 4 cameras)
- **Yellow/Orange**: Partial or poor coverage
- **Red**: Crash storm (50+ sessions)
- **Gray**: No data
- **Purple dot**: Inference complete for that date

Click any date to see the detail view.

### Transfer Alert
Banner at the top showing whether new data is being transferred from the recording PC to VAST. Green = OK, yellow = delayed, red = stale (recording or transfer may be down).

Note: robocopy runs at 3AM daily. The latest date always shows ~2-3 videos because only hours 00:00-03:00 have been copied. The full picture is available one day later (rescanned automatically).

### Per-Camera Detail
Click a date to see per-camera breakdown:
- Video count, session count, total size
- Tiny files, empty sessions, hours covered
- 24-hour timeline showing which hours were recorded
- Inference progress per camera with progress bar
- Issue flags (crash_day, crash_storm, tiny_files, incomplete, etc.)

### Visual Timeline with Thumbnails
4-camera x 24-hour grid with:
- **Thumbnail screenshots** extracted from each video
- **Day/night cycle**: warm background (9:30am-9:30pm lights off) and dark background (9:30pm-9:30am lights on)
- **Click to enlarge**: lightbox with left/right arrow navigation
- Missing hours shown with dashed borders
- Camera rows in physical order (Cam 1-4)

### Trend Charts
Five views: Hour Coverage, Sessions/Day, Videos/Day, Tiny Files, Inference Progress.

## Daily Automation

Everything runs automatically via cron on exx:

| Time | Job | What |
|------|-----|------|
| 3:00 AM | Robocopy (Windows) | Recording PC copies new data to VAST |
| 6:03 AM | `update_dashboard.sh` | Scan VAST + generate thumbnails + composite image + git push |
| 8:00 AM | GPU `slack_status.sh` | GPU status report to Slack (weekdays) |
| 8:05 AM | `slack_hcm_report.sh` | HCM recording health + visual timeline to Slack |

### update_dashboard.sh (6:03 AM daily)

1. Runs `scan_daily.py` (incremental scan, ~5 seconds)
2. Generates thumbnails for last 30 days (`gen_thumbs.py --days 30 --incremental`)
3. Generates composite visual timeline image (`gen_composite.py`)
4. Manages 30-day sliding window of thumbnails in git
5. Commits and pushes to GitHub Pages

### slack_hcm_report.sh (8:05 AM daily)

Posts to Slack via webhook:
- Recording health for the latest **complete** day (skips the partial robocopy day)
- Per-camera breakdown with transfer status
- Inference progress (from GPU dashboard logs)
- Composite visual timeline image (hosted on GitHub Pages)

## Components

### 1. Daily Scanner (`scan_daily.py`)

Walks VAST, groups sessions by date, produces `hcm_daily_status.json`:

```bash
python3 scan_daily.py              # incremental (only new dates + last 3 days)
python3 scan_daily.py --full       # full rescan (~3 min with parallelism)
python3 scan_daily.py --dry-run    # print without writing
python3 scan_daily.py --days 30    # only last 30 days
python3 scan_daily.py --workers 64 # tune thread count (default: 16)
```

Scanning layers:
- **Recording**: video count, size, session count, hours covered, flags
- **Inference (per-date)**: checks `.slp` prediction files per session
- **Inference (totals)**: reads from GPU dashboard JSONL logs for accurate global counts
- **Transfer**: checks latest session date per camera vs today

Performance:
- **Parallel I/O**: 4 cameras scanned concurrently, each with a thread pool (default 16 threads) for session-level VAST I/O
- **Incremental**: only scans new dates + rescans last 3 days for late robocopy data
- **Inference skip**: skips dates already complete or without recording data
- **Atomic write**: JSON written to temp file then renamed, so interrupted scans can't corrupt the dashboard
- ~5 seconds incremental, ~3 minutes full scan

### 2. Thumbnail Generator (`gen_thumbs.py`)

Extracts one frame (at 10 seconds) from each video as a 320px JPEG thumbnail:

```bash
uv run gen_thumbs.py                      # all dates
uv run gen_thumbs.py --date 2024-12-01    # single date
uv run gen_thumbs.py --days 30            # last 30 days
uv run gen_thumbs.py --incremental        # skip existing
```

- Skips tiny files (<1MB crash artifacts)
- ~12KB per thumbnail, ~546MB total for all 46,000+ videos
- Requires `opencv-python-headless` (handled by `uv run` inline deps)

### 3. Composite Generator (`gen_composite.py`)

Generates a visual timeline image (4-camera x 24-hour grid) for Slack reports:

```bash
uv run gen_composite.py                     # latest date
uv run gen_composite.py --date 2026-05-13   # specific date
uv run gen_composite.py --output out.jpg    # custom output path
```

- Uses thumbnails from `thumbs/` directory
- Day/night coloring, camera labels (physical names), status badge
- ~50-110KB JPEG output

### 4. Web Dashboard (`index.html`)

Static HTML dashboard that reads `hcm_daily_status.json`:
- Calendar heatmap with recording health
- Transfer alert banner
- Inference progress cards and per-date breakdown
- Visual timeline with thumbnails and day/night cycle
- Click-to-enlarge lightbox with arrow navigation
- Trend charts (coverage, sessions, videos, tiny files, inference)
- Camera labels show physical names (Cam 1-4) with software ID in parentheses

### 5. Slack Integration

- **Daily report** (`slack_hcm_report.sh`): recording health + composite image at 8:05 AM
- **Slash command** (`/hcm-status`): on-demand status via existing GPU Slack bot
- Secrets stored in `.slack_config` (gitignored)

## File Structure

```
hcm-dashboard/
├── README.md                 # This file
├── DATAMAP.md                # Detailed data structure reference
├── CAMERA_SWAP.md            # Camera wiring mismatch documentation
├── scan_daily.py             # VAST scanner
├── gen_thumbs.py             # Thumbnail generator
├── gen_composite.py          # Composite visual timeline generator
├── update_dashboard.sh       # Daily cron: scan + thumbs + push
├── slack_hcm_report.sh       # Daily cron: Slack report
├── .slack_config             # Slack secrets (gitignored)
├── hcm_daily_status.json     # Scanner output (compact JSON)
├── composite_latest.jpg      # Latest visual timeline composite
├── index.html                # Web dashboard
└── thumbs/                   # Video thumbnails
    ├── cam_01/               #   Last 30 days in repo
    ├── cam_02/               #   Full set on exx (~546MB)
    ├── cam_03/
    └── cam_04/
```

## Camera Notes

As of May 2026 (see [CAMERA_SWAP.md](CAMERA_SWAP.md) for full details):
- **Cam 1** (cam_01 on disk): Calibration checkerboard (no mice)
- **Cam 2** (cam_03 on disk): Active mouse cage
- **Cam 3** (cam_04 on disk): Active mouse cage
- **Cam 4** (cam_02 on disk): Calibration checkerboard (no mice)

All 4 cameras had mice from Sep 2024 through at least Jan 2026. The mismatch between physical labels and software IDs has existed since day 1 — discovered May 2026 via dashboard thumbnails.

## Data Layout

```
vast/lee/2024-09-24-LeeAPP/
├── cam_01/                         # 4 cameras, ~11,800 sessions each
├── cam_02/                         # (software IDs, not physical — see CAMERA_SWAP.md)
├── cam_03/
├── cam_04/
│
└── cam_XX/YYYY-MM-DD-HH-MM-SS/    # session folder (timestamp = start time)
    ├── cam_XX.00.mp4               # 1hr video chunks
    ├── cam_XX.01.mp4
    ├── ...
    └── cam_XX.23.mp4               # full day = 24 videos
```

## Dependencies

- **Scanner**: Python 3 (stdlib + `concurrent.futures` for parallel I/O)
- **Thumbnails/Composite**: `opencv-python-headless`, `numpy` (via `uv run` inline deps)
- **Dashboard**: None (static HTML, reads JSON with cache-busting)
- **Slack**: webhook + bot token (in `.slack_config`)
