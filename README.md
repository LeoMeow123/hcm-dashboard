# HCM Dashboard

**https://leomeow123.github.io/hcm-dashboard/**

Daily health monitoring for the Home Cage Monitoring (HCM) recording pipeline. Scans VAST for recording issues, tracks SLEAP inference progress, monitors data transfer, and reports via web dashboard.

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
| Recording period | 2024-09-24 to present (495 days) |
| Hour coverage | **44.6%** (21,181 / 47,520 camera-hours) |
| Healthy days | 58 (12%) |
| Degraded days | 436 (88%) |
| Crash artifacts | 25,394 tiny files (35% of all videos) |
| Inference progress | 63.2% (45,374 / 71,793 videos) |

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

### Trend Charts
Five views: Hour Coverage, Sessions/Day, Videos/Day, Tiny Files, Inference Progress.

## Data Layout

```
vast/lee/2024-09-24-LeeAPP/
├── cam_01/                         # 4 cameras, ~11,800 sessions each
├── cam_02/
├── cam_03/
├── cam_04/
│
└── cam_XX/YYYY-MM-DD-HH-MM-SS/    # session folder (timestamp = start time)
    ├── cam_XX.00.mp4               # 1hr video chunks
    ├── cam_XX.01.mp4
    ├── ...
    └── cam_XX.23.mp4               # full day = 24 videos
```

### What a healthy day looks like

- 1 session per camera starting at ~00:01
- 24 videos per session (cam_XX.00.mp4 through cam_XX.23.mp4)
- Each video ~50-130MB, ~1hr of recording at 50fps 1280x1024
- All 4 cameras in sync

### What a bad day looks like

- Multiple sessions (recording crashed and restarted)
- <24 total videos across all sessions (missing hours)
- Tiny files <1MB (crash artifacts — recording started then died)
- Missing entirely from one or more cameras

## Components

### 1. Daily Scanner (`scan_daily.py`)

Walks VAST, groups sessions by date, produces `hcm_daily_status.json`:

```bash
python3 scan_daily.py              # incremental (only new dates)
python3 scan_daily.py --full       # full rescan
python3 scan_daily.py --dry-run    # print without writing
python3 scan_daily.py --days 30    # only last 30 days
```

Three scanning layers:
- **Recording**: video count, size, session count, hours covered, flags
- **Inference**: checks `.slp` prediction files per session/date
- **Transfer**: checks latest session date per camera vs today

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

### 3. Web Dashboard (`index.html`)

Static HTML dashboard that reads `hcm_daily_status.json`:
- Calendar heatmap with recording health
- Transfer alert banner
- Inference progress cards and per-date breakdown
- Visual timeline with thumbnails and day/night cycle
- Click-to-enlarge lightbox with arrow navigation
- Trend charts (coverage, sessions, videos, tiny files, inference)

### 4. Slack Bot (planned)

- Daily morning report: yesterday's recording health
- Alerts on missing days or crash storms
- `/hcm-status` command for on-demand check

## File Structure

```
hcm-dashboard/
├── README.md                 # This file
├── DATAMAP.md                # Detailed data structure reference
├── scan_daily.py             # VAST scanner (3 layers: recording, inference, transfer)
├── gen_thumbs.py             # Thumbnail generator (uv run, opencv)
├── hcm_daily_status.json     # Scanner output (1.9MB, compact JSON)
├── index.html                # Web dashboard
└── thumbs/                   # Video thumbnails
    ├── cam_01/               #   Last 30 days in repo
    ├── cam_02/               #   Full set on exx (~546MB)
    ├── cam_03/
    └── cam_04/
```

## Camera Notes

As of May 2026:
- **cam_01**: Calibration checkerboard (no mice)
- **cam_02**: Active mouse cage
- **cam_03**: Calibration checkerboard (no mice)
- **cam_04**: Active mouse cage

All 4 cameras had mice from Sep 2024 through at least Dec 2024. Camera assignments may have changed — verify physical labels against software numbering.

## Dependencies

- **Scanner**: Python 3 (stdlib only)
- **Thumbnails**: `opencv-python-headless` (via `uv run` inline deps)
- **Dashboard**: None (static HTML, reads JSON)
