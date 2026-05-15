# HCM Data Map

Detailed documentation of the HCM recording data on VAST.

## Source

- **Path**: `/home/exx/vast/lee/2024-09-24-LeeAPP/`
- **Origin**: Basler cameras → recording PC → robocopy to VAST
- **Recording start**: 2024-09-24
- **Status**: Ongoing (latest: 2026-05-15)
- **Camera wiring**: Software IDs do not match physical labels — see [CAMERA_SWAP.md](CAMERA_SWAP.md)

## Directory Structure

```
2024-09-24-LeeAPP/
├── cam_01/          # Physical Cam 1 — calibration board (as of May 2026)
├── cam_02/          # Physical Cam 4 — calibration board (as of May 2026)
├── cam_03/          # Physical Cam 2 — active mouse cage
├── cam_04/          # Physical Cam 3 — active mouse cage
└── video_metadata.csv   # 5.3M-line ffprobe dump (pre-existing)
```

**WARNING**: Directory names (cam_01-04) are software IDs assigned by Bonsai.
They do NOT match physical camera labels. See [CAMERA_SWAP.md](CAMERA_SWAP.md).

### Session Folders

Each session is a timestamped directory: `YYYY-MM-DD-HH-MM-SS`

The timestamp is when the recording software started (or restarted).

```
cam_01/2026-05-10-00-21-05/
├── cam_01.00.mp4    # Chunk 0 (~50-130MB, ~1hr at 50fps)
├── cam_01.01.mp4    # Chunk 1
├── ...
├── cam_01.23.mp4    # Chunk 23 (full day = 24 chunks)
└── (optional) YYYY-MM-DDTHH_MM_SS.csv   # Frame timestamps
```

### Video File Naming

- `cam_XX.NN.mp4` where NN = sequential chunk index (00-23 for full day)
- Each chunk is ~1 hour of recording
- Size varies by activity: 46-130MB per chunk (H.264, 1280x1024, 50fps)
- **Video index is relative to session start, not clock hour**
  - Session starting at 08:11 → `cam_01.00.mp4` covers ~08:11-09:11, not 00:00-01:00
  - This means crash restarts create duplicate index 00 files covering different clock hours

### Frame Timestamp CSV

- Present in some sessions, not all
- Contains per-frame timestamps with sub-millisecond precision
- Format: `frame_number,ISO_timestamp`

## Data Loss Summary

| Metric | Value |
|--------|-------|
| Expected camera-hours | 47,520 (495 days x 24h x 4 cams) |
| Actual camera-hours covered | 21,181 (44.6%) |
| **Missing camera-hours** | **26,339 (55.4%)** |
| Total videos on disk | 71,793 |
| Usable videos (>1MB) | 46,399 |
| Crash artifacts (<1MB) | 25,394 (35.4% of all videos) |
| Total sessions | 47,228 (expected 1,980) |
| Excess sessions from crashes | 45,248 |

### Why more videos than expected

71,793 videos on disk > 47,520 expected because each crash restart creates a new session starting at index 00. The recording crashes, restarts, writes `cam_01.00.mp4` (a tiny crash artifact), crashes again — never reaching indices 01-23. This produces many duplicate index-0 files covering hour 0 while later hours go unrecorded.

## Session Count Per Camera

| Camera | Sessions | Notes |
|--------|----------|-------|
| cam_01 | 11,805 | |
| cam_02 | 11,817 | Slightly more (extra restarts?) |
| cam_03 | 11,806 | |
| cam_04 | 11,800 | |

## Video Count Distribution (cam_01)

| Videos in session | Count | Meaning |
|-------------------|-------|---------|
| 0 | 13 | Empty session (crash before first write) |
| 1 | 10,815 | Short session (crashed after ~1hr or less) |
| 2-23 | 915 | Partial day |
| 24 | 62 | Full 24hr recording |

**91% of sessions have only 1 video** — the recording software crashes frequently and each restart creates a new session folder.

## Sessions Per Day Distribution

A healthy day has **1 session** with **24 videos**.

| Sessions/day | Days | Interpretation |
|--------------|------|----------------|
| 1 | 133 | Ideal: clean 24hr recording |
| 2-3 | 68 | Minor: 1-2 restarts |
| 4-10 | ~60 | Bad: multiple crashes |
| 11-50 | ~100 | Very bad: crash loop |
| 51-100 | ~40 | Severe: almost continuous crashing |
| 100+ | ~25 | Critical: recording PC needs intervention |

### Worst Days

| Date | Sessions | Period |
|------|----------|--------|
| 2025-06-04 | 204 | May-Jun 2025 crash storm |
| 2025-06-03 | 183 | |
| 2025-06-01 | 172 | |
| 2025-05-31 | 162 | |
| 2025-12-29 | 114 | Dec 2025 instability |

## Known Problem Periods

### 1. Jan-Feb 2025 Escalation
- Jan 13: 27 sessions → escalated to 69/day by Feb 4
- Recording PC increasingly unstable
- Brief recovery after Feb 6

### 2. May-Jun 2025 Crash Storm
- May 6: 58 sessions → peaked at 204 sessions/day (Jun 4)
- Recording essentially unusable — more time crashing than recording
- Resolved ~Jun 9

### 3. Oct-Dec 2025 Instability
- Oct 21 onwards: 32-114 sessions/day
- Extended period of unreliable recording

### 4. Missing Periods
- November 2025: entirely absent
- Feb-Apr 2026: mostly absent (Jan 16 → Apr 6 gap)
- Various single-day gaps throughout

## Inference Data

Inference results (SLEAP pose predictions):
```
/home/exx/vast/leo/datasets/inference-Kuo-Fen-HCM/
├── cam_01/   # ~7,520 session folders with .slp files
├── cam_02/   # ~7,585 sessions
├── cam_03/   # ~7,570 sessions
└── cam_04/   # ~7,521 sessions
```

Each session folder contains `cam_XX.NN.predictions.slp` files.

### Inference Progress

| Camera | Videos Done | Videos Total | Progress |
|--------|-------------|--------------|----------|
| cam_01 (Phys Cam 1) | 11,584 | 15,935 | 72.7% |
| cam_02 (Phys Cam 4) | 11,816 | 16,009 | 73.8% |
| cam_03 (Phys Cam 2) | 11,664 | 15,995 | 72.9% |
| cam_04 (Phys Cam 3) | 11,666 | 15,958 | 73.1% |
| **Total** | **46,730** | **63,897** | **73.1%** |

Running on exx + 2 helper workstations.

Inference progress tracked in JSONL logs at:
```
/home/exx/vast/leo/2026-01-28-HCM-APP/scratch/2026-02-26-inference-benchmark/inference_log/
├── cam_01_progress.jsonl
├── cam_02_progress.jsonl
├── cam_03_progress.jsonl
└── cam_04_progress.jsonl
```

## Existing Metadata

### video_metadata.csv (5.3M lines)
Pre-existing ffprobe dump with columns:
```
file_path, file_name, parent_dir, size_bytes, mtime_iso, ctime_iso,
format_name, duration_sec, bit_rate, nb_streams,
video_codec, video_profile, width, height, pix_fmt,
avg_frame_rate, r_frame_rate, nb_frames, video_bit_rate, video_duration_sec,
audio_codec, audio_sample_rate, audio_channels,
format_tags_json, video_tags_json, ffprobe_error, raw_json
```

Note: paths in this CSV use `/root/vast/...` (generated from a different mount point).

## Robocopy Timing

Robocopy runs at **3AM daily** via Windows Task Scheduler on the recording PC.

**Important**: This means the latest date on VAST always shows only ~2-3 videos
(hours 00:00-03:00). The full 24 hours of video arrive one day later when the
next robocopy runs. The scanner automatically rescans the last 3 days to catch
this late-arriving data.

## Key Metrics for Daily Monitor

For each date, per camera:

| Metric | How to compute | Healthy value |
|--------|---------------|---------------|
| Sessions | Count of session folders for that date | 1 |
| Videos | Total .mp4 files across all sessions | 24 |
| Total size | Sum of file sizes | ~780MB-2.5GB |
| Hours covered | Wall-clock hours with video (session start + index) | 24 |
| Empty sessions | Sessions with 0 .mp4 files | 0 |
| Tiny files | .mp4 files < 1MB (crash artifacts) | 0 |
| Inference done | .slp files exist for all videos | Yes |
| Transfer fresh | Latest session date = today or yesterday | Yes |
