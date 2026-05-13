# HCM Monitor

Daily health monitoring for the Home Cage Monitoring (HCM) recording pipeline. Scans VAST for recording issues, tracks inference progress, and reports via web dashboard and Slack.

## Problem

The HCM recording computer (robocopy → VAST) is unstable. Common issues:
- Recording crashes mid-day and restarts, producing fragmented sessions
- Days with 50-200+ restart sessions instead of 1 clean 24hr session
- Missing days with no recordings at all
- Empty session folders (0 videos)
- Short videos or 0-byte files from interrupted writes

Nobody notices these problems until weeks later when analysis fails. This tool catches them daily.

## Data Layout

```
vast/lee/2024-09-24-LeeAPP/
├── cam_01/                         # 4 cameras, ~11,800 sessions each
├── cam_02/
├── cam_03/
├── cam_04/
│
└── cam_XX/YYYY-MM-DD-HH-MM-SS/    # session folder (timestamp = start time)
    ├── cam_XX.00.mp4               # 1hr video chunks (~33MB each)
    ├── cam_XX.01.mp4
    ├── ...
    ├── cam_XX.23.mp4               # full day = 24 videos
    └── YYYY-MM-DDTHH_MM_SS.csv    # frame timestamps
```

### What a healthy day looks like

- 1 session per camera starting at ~00:01
- 24 videos per session (cam_XX.00.mp4 through cam_XX.23.mp4)
- Each video ~33MB, ~1hr of recording at 50fps 1280x1024
- All 4 cameras in sync

### What a bad day looks like

- Multiple sessions (recording crashed and restarted)
- <24 total videos across all sessions (missing hours)
- Empty sessions (0 videos)
- 0-byte or tiny files (write interrupted)
- Missing entirely from one or more cameras

## Recording Stats

| Metric | Value |
|--------|-------|
| Start date | 2024-09-24 |
| Latest date | 2026-05-12 (ongoing) |
| Total unique dates | 494 |
| Cameras | 4 (cam_01 through cam_04) |
| Sessions per camera | ~11,800 |
| Video format | H.264, 1280x1024, 50fps, 1hr chunks |
| Expected per day | 24 videos x 4 cameras = 96 videos |
| Total videos | ~63,900 per camera |

## Components

### 1. Daily Scanner (`scan_daily.py`)

Walks VAST, groups sessions by date, produces `hcm_daily_status.json`:

- Per date, per camera: video count, total size, session count, hours covered
- Flags: missing days, short days, crash days (>2 sessions), empty sessions, 0-byte files
- Incremental: only scans dates newer than last run
- Runs via cron daily

### 2. Web Dashboard (`index.html`)

Calendar heatmap + daily detail view:

- Color-coded calendar: green (24 videos), yellow (partial), red (missing/crashed)
- Click a date to see per-camera breakdown
- Timeline showing which hours were recorded
- Trend charts: daily video count, restart frequency, coverage %

### 3. Slack Bot

- Daily morning report: yesterday's recording health
- Alerts on missing days or crash storms
- `/hcm-status` command for on-demand check

### 4. Inference Tracker

Links to gpu-dashboard inference data to show:

- Which dates/videos have been processed by SLEAP
- Inference coverage vs recording coverage
- Estimated completion date

## File Structure

```
hcm-monitor/
├── README.md               # This file
├── DATAMAP.md              # Detailed data structure and known issues
├── scan_daily.py           # VAST scanner → hcm_daily_status.json
├── hcm_daily_status.json   # Scanner output (daily cron updates)
├── index.html              # Web dashboard
└── agent/                  # Slack bot and cron scripts (future)
```

## Dependencies

- **Scanner**: Python 3 (stdlib only, no pip packages)
- **Dashboard**: None (static HTML, reads JSON)
- **Slack**: slack-bolt (same as gpu-dashboard)
