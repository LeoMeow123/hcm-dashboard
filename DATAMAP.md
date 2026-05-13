# HCM Data Map

Detailed documentation of the HCM recording data on VAST.

## Source

- **Path**: `/home/exx/vast/lee/2024-09-24-LeeAPP/`
- **Origin**: Basler cameras → recording PC → robocopy to VAST
- **Recording start**: 2024-09-24
- **Status**: Ongoing (latest: 2026-05-12)

## Directory Structure

```
2024-09-24-LeeAPP/
├── cam_01/          # Mouse cage 1
├── cam_02/          # Mouse cage 2
├── cam_03/          # Mouse cage 3
├── cam_04/          # Mouse cage 4
└── video_metadata.csv   # 5.3M-line ffprobe dump (pre-existing)
```

### Session Folders

Each session is a timestamped directory: `YYYY-MM-DD-HH-MM-SS`

The timestamp is when the recording software started (or restarted).

```
cam_01/2026-05-10-00-21-05/
├── cam_01.00.mp4    # Hour 0 (33MB, ~1hr at 50fps)
├── cam_01.01.mp4    # Hour 1
├── ...
├── cam_01.23.mp4    # Hour 23 (sometimes shorter, ~21MB)
└── (optional) YYYY-MM-DDTHH_MM_SS.csv   # Frame timestamps
```

### Video File Naming

- `cam_XX.NN.mp4` where NN = sequential chunk index (00-23 for full day)
- Each chunk is ~1 hour of recording
- ~33MB per chunk (H.264, 1280x1024, 50fps)
- Last chunk of the day is often shorter (~21MB)

### Frame Timestamp CSV

- Present in some sessions, not all
- Contains per-frame timestamps with sub-millisecond precision
- Format: `frame_number,ISO_timestamp`

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

### Other Pre-existing Files
- `duration_by_date.csv` / `.png` — duration analysis
- `duration_iqr_by_date.csv` / `.png` — IQR analysis
- `_video_list.txt` — file listing
- `_ffprobe_run.log` — ffprobe execution log

## Inference Data

Inference results live in a separate directory:
```
/home/exx/vast/leo/datasets/inference-Kuo-Fen-HCM/
├── cam_01/   # .slp prediction files
├── cam_02/
├── cam_03/
└── cam_04/
```

Inference progress is tracked in JSONL logs at:
```
/home/exx/vast/leo/2026-01-28-HCM-APP/scratch/2026-02-26-inference-benchmark/inference_log/
├── cam_01_progress.jsonl
├── cam_02_progress.jsonl
├── cam_03_progress.jsonl
└── cam_04_progress.jsonl
```

## Key Metrics for Daily Monitor

For each date, per camera:

| Metric | How to compute | Healthy value |
|--------|---------------|---------------|
| Sessions | Count of session folders for that date | 1 |
| Videos | Total .mp4 files across all sessions | 24 |
| Total size | Sum of file sizes | ~780MB |
| Hours covered | Count of unique video indices | 24 |
| Empty sessions | Sessions with 0 .mp4 files | 0 |
| Zero-byte files | .mp4 files with 0 bytes | 0 |
| Tiny files | .mp4 files < 1MB | 0 |
| Camera sync | All 4 cameras have same date | Yes |
