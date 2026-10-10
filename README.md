# HCM Dashboard

**https://leomeow123.github.io/hcm-dashboard/**

Daily health monitoring for the Home Cage Monitoring (HCM) recording pipeline: is the rig recording every hour on all four cameras, are the hour files landing on VAST, is SLEAP keeping up, and what do the cages look like right now. Web dashboard + Slack report.

## Two rigs, one dashboard

| | Bonsai rig (archive) | frameforge rig (live) |
|---|---|---|
| Period | 2024-09-24 → 2026-10-07 13:21 | 2026-10-07 14:19 → present |
| Host | recording PC, robocopy to VAST at 3 AM | `talmolab-rigAD00` (10.3.14.126), writes to VAST directly |
| Video | `/home/exx/vast/lee/2024-09-24-LeeAPP/<disk_cam>/<session>/<disk_cam>.NN.mp4` | `/home/exx/vast/leo/frameforge/<deployment>/cam_0N/<YYYY-MM-DD-00-00-00>/cam_0N.HH.mp4` |
| Files | one session folder per (re)start, NN relative to session start | one folder per calendar day, one file per clock hour, lands ~30 s after the hour |
| Timestamps | none | `cam_0N.HH.h5`: int64 ns per frame (`fps`, `host` attrs) |
| Camera ids | **swapped** on disk ([CAMERA_SWAP.md](CAMERA_SWAP.md)) | `cam_0N` = physical cage N, no swap |
| SLEAP output | `/home/exx/vast/leo/datasets/inference-Kuo-Fen-HCM/<disk_cam>/<session>/` | `/home/exx/vast/leo/datasets/inference-frameforge-HCM/<deployment>/cam_0N/<day>/cam_0N.HH.predictions.slp` |
| Heartbeat | — | `/home/exx/vast/leo/frameforge/_ff_heartbeat/talmolab-rigAD00.json`, hourly |
| SLEAP workers | exx + lee-hcm (finished) | one per camera on lee-hcm: `2026-04-03-HCM-inference-accelerator/run_frameforge.sh`, tmux `hcm-ff-cam_0N`, logs `inference_log/ff_cam_0N_console.log` |

The brief for the switch, with file facts and quality reports, is `/home/exx/vast/leo/2026-10-07-HCM-rig-switch/NEW_DATA_BRIEF.md`.

**Everything in the dashboard is keyed by physical camera.** `scan_daily.py` re-keys the Bonsai archive through the swap table and keeps each entry's disk folder in `disk_cam`; frameforge entries need no mapping. The rig-switch day (2026-10-07) merges both halves per cage.

## What a healthy day looks like (frameforge)

Per camera per day: 24 hour files, 24.0 h recorded (from the `.h5` timestamps, not file sizes), no inter-frame gap over 100 ms, an `.h5` next to every `.mp4`, no tiny files. Today is `in_progress`: the expected file count is the number of completed clock hours, and the detail view shows the hours that have landed and which of them SLEAP has finished. Flags: `healthy`, `in_progress`, `incomplete`, `missing_hours`, `short_file` (a file that did not cover its hour: a recording break), `frame_gaps`, `missing_h5`, `delivery_lag` (today, more than one hour behind), `tiny_files`, `rig_switch`.

Rig status at the top of the page combines the heartbeat age (ok ≤ 90 min, warn ≤ 3 h, stale beyond) with how far the newest hour file on each camera is behind the clock (ok ≤ 75 min, warn ≤ 3 h, stale beyond). Four thumbnails of the newest hour file sit under it: the fastest "are the cameras alive and pointed right" check.

## Bonsai archive (frozen)

The old recording PC was unstable: crash restarts fragmented days into dozens of sessions, whole days went missing, and 35% of its files were tiny crash artifacts. Those days keep their original health rules (sessions, tiny files, estimated hours) and stay browsable in the calendar; their totals sit in one line under the summary tiles. SLEAP on the archive is at 97,275 / 97,315 videos and no longer changes.

## Camera Wiring Mismatch

Physical camera labels do not match the Bonsai software IDs on disk. **cam_01 is correct; cam_02/03/04 are cyclically rotated.** See [CAMERA_SWAP.md](CAMERA_SWAP.md) for full mapping and instructions.

## Cage Event Log

Full append-only log of cage / mouse events (fatalities, relabels, mice-in
dates) is maintained at `/home/exx/vast/leo/2026-05-28-HCM_drinking/EVENTS.md`.
Most recent: **2026-07-30 15:00 PT — COHORT SWAP** — prior Tau/WT/PD mice
transferred out (to CRAF 2909 r5); new **all-WT 4-mo** cohort placed in all 4
cages (Cam 1/2 = ♀ WT ×3, Cam 3/4 = ♂ WT ×2). Everything from this timestamp on is
the new WT cohort — do not label it Tau/PD.

| Directory on disk | Physical Camera |
|-------------------|-----------------|
| cam_01/ | Cam 1 (correct) |
| cam_02/ | **Cam 4** |
| cam_03/ | **Cam 2** |
| cam_04/ | **Cam 3** |

The dashboard displays corrected physical camera labels. Files on disk are **never renamed** — the mapping is applied at display time only.

## Dashboard Features

### Rig status and latest frames
Banner: heartbeat age and hour-file delivery per camera (see above). Below it, the newest hour file's thumbnail from each camera, click to enlarge.

### Summary tiles
New rig first: days since the switch, hour coverage (camera-hours recorded / expected), healthy days, frame gaps, SLEAP progress with backlog, workers alive and minutes per file. One line underneath carries the Bonsai archive totals.

### Recording Calendar
Color-coded heatmap of every day on both rigs:
- **Green**: 22-24 h on average across cameras
- **Yellow/Orange**: partial coverage
- **Dark red**: nothing recorded · **Gray**: no data
- **Blue outline**: today, still filling in
- **Purple dot**: SLEAP complete for that date

The page opens on the newest day. Click any date for the detail view.

### Per-Camera Detail
frameforge days: hour files / expected, hours recorded, MB, frames, frame gaps (max gap in ms), missing `.h5`; a 24 h coverage bar drawn from the exact first and last timestamp of every file (red segment = gap or missing `.h5`); a 24-cell strip of which hours SLEAP has finished; flags. Bonsai days keep the old layout (videos, sessions, tiny files, estimated coverage). The switch day shows both.

### SLEAP Inference panel
Per camera: hour files done / recorded, backlog with the oldest waiting hour and an ETA at the worker's measured rate, worker alive or silent (from the console log's last write), the file it is processing, failures on its last scan.

### Visual Timeline with Thumbnails
4-camera x 24-hour grid with:
- **Thumbnail screenshots** extracted from each video
- **Day/night cycle**: warm background (9:30am-9:30pm lights off) and dark background (9:30pm-9:30am lights on)
- **Click to enlarge**: lightbox with left/right arrow navigation
- Missing hours shown with dashed borders
- Camera rows in physical order (Cam 1-4)

### Trend Charts
Six views: Hour Coverage, Sessions/Day, Videos/Day, Tiny Files, Frame Gaps, Inference Progress.

## Daily Automation

Everything runs automatically via cron on exx:

| Time | Job | What |
|------|-----|------|
| every hour | frameforge (rig) | hour file + `.h5` land on VAST ~30 s after the hour; SLEAP workers poll every 10 min |
| 8:00 AM | `update_dashboard.sh` | Scan both rigs + thumbnails + composite + git push |
| 8:00 AM | GPU `slack_status.sh` | GPU status report to Slack (weekdays) |
| 8:20 AM | `slack_hcm_report.sh` | HCM recording health + visual timeline to Slack |

The scan is cheap now (about 10 s; the frozen Bonsai totals are cached), so the update could run hourly. If you do that, split a small live JSON out of the 4 MB status file first: every push commits it.

### update_dashboard.sh (8:00 AM daily)

1. `uv run --with h5py --with numpy scan_daily.py` (incremental: new dates + last 3 days, both rigs)
2. Thumbnails for the last 30 days (`gen_thumbs.py --days 30 --incremental`, driven by the JSON, both rigs)
3. Composite visual timeline for the latest **complete** day (`gen_composite.py`)
4. 30-day sliding window of thumbnails in git
5. Commit and push to GitHub Pages

### slack_hcm_report.sh (8:20 AM daily)

Posts to Slack via webhook: rig heartbeat and hour-file delivery, recording health for the latest complete day per camera (hour files, hours recorded, frames, gaps), today's hour count so far, SLEAP backlog and worker state, the frozen Bonsai line, the composite image, a dashboard button. `bash slack_hcm_report.sh --dry` prints it.

## Components

### 1. Daily Scanner (`scan_daily.py`)

Walks both rigs on VAST, groups by date, produces `hcm_daily_status.json` (schema 2):

```bash
uv run scan_daily.py                   # incremental (new dates + last 3 days), with h5 frame stats
uv run scan_daily.py --full            # full rescan of both rigs (~15 min: the Bonsai tree is big)
uv run scan_daily.py --refresh-legacy  # incremental, but recount the frozen Bonsai totals
uv run scan_daily.py --dry-run         # print without writing
python3 scan_daily.py                  # works without h5py: file counts and sizes only
```

Scanning layers:
- **frameforge recording**: per day folder, per hour file: size, `.h5` frame count, first and last timestamp, max inter-frame gap, gaps over 100 ms. Hours recorded come from the timestamps.
- **Bonsai recording** (only when the scan window reaches 2026-10-07 or on `--full`): unchanged session logic.
- **Inference per date**: `.predictions.slp` per day folder (frameforge, with the list of hours done) or per session (Bonsai).
- **Inference totals**: frameforge recounted every run (small tree) plus worker state from the console log tails; Bonsai totals cached in `scan_info.overall.legacy` and only recounted on `--full` / `--refresh-legacy`.
- **Rig freshness**: heartbeat JSON age; newest hour file per camera vs the clock.

Schema 2 in one paragraph: `dates[date].cameras` is keyed by physical camera. Each entry has `rig` (`bonsai` | `frameforge` | `mixed`), `disk_cam` (folder on disk), `timeline` entries `[wall_hour, session, index, mb, thumb_cam]`, and for frameforge `files[]` with `start`, `end`, `span_s`, `frames`, `max_gap_ms`, `gaps`, `h5`, plus `hours_missing`, `frames`, `gaps`, `missing_h5`. `summary.status` adds `in_progress`; `summary.hours_expected` is 24 or the clock hour today. `scan_info` carries `rig` (heartbeat), `transfer` (per camera `minutes_behind`, `status`, `thumb`), `latest_frames`, `rig_switch`, and `overall.frameforge` / `overall.legacy`. `overall.inference_per_camera` keeps the Bonsai disk-keyed totals because `update_combined_progress.sh` in the accelerator folder still reads it. A schema-1 file is migrated in place on the first run.

Performance: incremental scan about 10 s (reading a day of `.h5` files for all cameras takes about 1.4 s); atomic write.

### 2. Thumbnail Generator (`gen_thumbs.py`)

Extracts one frame (10 s in) from each video as a 320 px JPEG. Driven by the status JSON, so it finds videos on either rig; the thumbnail for a timeline entry is `thumbs/{thumb_cam}/{session}/{index:02d}.jpg`.

```bash
uv run gen_thumbs.py --days 30 --incremental   # daily cron
uv run gen_thumbs.py --date 2026-10-09
uv run gen_thumbs.py --latest                  # only the newest hour file per camera
```

- Skips tiny files (<1MB crash artifacts); 8 videos in parallel
- ~12KB per thumbnail
- Requires `opencv-python-headless` (handled by `uv run` inline deps)

### 3. Composite Generator (`gen_composite.py`)

Generates a visual timeline image (4-camera x 24-hour grid) for Slack reports:

```bash
uv run gen_composite.py                     # latest complete date
uv run gen_composite.py --date 2026-10-09   # specific date
uv run gen_composite.py --output out.jpg    # custom output path
```

- Uses thumbnails from `thumbs/`; default date is the latest complete day
- Day/night coloring, physical camera labels, rig name, status badge, purple underline on hours SLEAP has finished
- ~50-120KB JPEG output

### 4. Web Dashboard (`index.html`)

Static HTML dashboard that reads `hcm_daily_status.json`:
- Rig status banner and newest-frame strip
- Summary tiles (new rig) with the archive totals in one line
- Calendar heatmap over both rigs, opens on the newest day
- SLEAP inference panel per camera with worker state
- Day detail per camera (exact coverage bar from timestamps on the new rig)
- Visual timeline with thumbnails, day/night cycle, inference marks
- Click-to-enlarge lightbox with arrow navigation
- Trend charts (coverage, sessions, videos, tiny files, frame gaps, inference)
- Physical camera labels everywhere; the disk folder is shown in small print

### 5. Slack Integration

- **Daily report** (`slack_hcm_report.sh`): rig status, recording health, today so far, SLEAP backlog, composite image at 8:20 AM
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

- **frameforge rig (live)**: `cam_0N` is physical cage N, verified by eye against the old rig and by SLEAP mouse counts (3, 3, 2, 2 in cages 1-4). Never apply the Bonsai swap to it.
- **Bonsai archive**: disk folders are rotated (disk cam_02 = Cam 4, cam_03 = Cam 2, cam_04 = Cam 3), see [CAMERA_SWAP.md](CAMERA_SWAP.md). The scanner maps them; files are never renamed.
- Current cohort (since 2026-07-30): all WT, Cam 1/2 = three females, Cam 3/4 = two males.

## Data Layout

```
/home/exx/vast/leo/frameforge/                       # frameforge rig (live)
├── 2026-10-05/                                      # one folder per deployment
│   └── cam_01 … cam_04/                             # physical cage N
│       └── 2026-10-09-00-00-00/                     # one folder per calendar day (PT)
│           ├── cam_01.00.mp4  cam_01.00.h5          # HH = clock hour, one pair per hour
│           └── …              cam_01.23.h5
└── _ff_heartbeat/talmolab-rigAD00.json              # {hostname, ip, timestamp}, hourly

/home/exx/vast/lee/2024-09-24-LeeAPP/                # Bonsai rig (frozen 2026-10-07 13:21)
└── cam_XX/YYYY-MM-DD-HH-MM-SS/cam_XX.NN.mp4         # session = (re)start time, NN relative to it
```

## Dependencies

- **Scanner**: Python 3 + `h5py`/`numpy` for frame stats (via `uv run` inline deps; falls back to counts without them)
- **Thumbnails/Composite**: `opencv-python-headless`, `numpy` (via `uv run` inline deps)
- **Dashboard**: None (static HTML, reads JSON with cache-busting)
- **Slack**: webhook + bot token (in `.slack_config`)

## Roadmap

Polish ideas toward a lab-level tool, roughly in priority order.

### Next up

- **Hourly refresh.** The rig delivers hourly and the scan takes 10 s; run the update hourly with a small live JSON (scan_info + last 7 days) next to the big archive file so the newest-frame strip and rig banner are never more than an hour old.
- **Dark-frame and frozen-frame detection** on the newest thumbnails (mean brightness, difference between consecutive hours) feeding the rig banner.
- **Runbook links** on each problem state (heartbeat stale, delivery lag, worker silent, frame gaps): what to check on talmolab-rigAD00 and lee-hcm.

### Later

- **Calendar by weekday.** A GitHub-style week grid shows weekend and robocopy-schedule patterns that the day-of-month grid hides. Overlay cohort periods from the experiment timeline as coloured bands.
- **Per-camera lanes** for the last 8 weeks, since cameras fail independently.
- **Immediate alerts** for the states that matter (heartbeat stale, no hour file for 3 h, worker silent, frame gaps on a day); keep the daily digest but make it quiet when nothing is wrong.
- **Export.** CSV of the daily status and a "copy day summary" button for Slack.
- **Phone layout.** A "last 14 days" compact view instead of the 31-column calendar on small screens.

### Shared with the GPU Dashboard

- One visual language: same header, a nav strip linking every lab tool, same status colours and card styles from one shared stylesheet.
- Freshness everywhere: every number says when it was measured and when the next scan is due.
