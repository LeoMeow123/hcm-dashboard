# Camera Wiring Mismatch

## Discovery

Discovered 2026-05-14 via dashboard thumbnail inspection. Physical camera labels
do not match the software camera IDs used by Bonsai on the recording PC.

## The Problem

Bonsai assigns camera IDs (cam_01-cam_04) that do not match the physical labels
on the cameras. **cam_01 is correct; cameras 2, 3, 4 are cyclically rotated.**

This mismatch has existed since day 1 (2024-09-24). It was invisible while all
4 cages had mice, and only became apparent when 2 cages were emptied, revealing
calibration boards in cam_01 and cam_03.

## Mapping

### Software ID → Physical Camera

| Software ID (on disk) | Physical Camera |
|-----------------------|-----------------|
| cam_01                | Cam 1 (correct) |
| cam_02                | **Cam 4**       |
| cam_03                | **Cam 2**       |
| cam_04                | **Cam 3**       |

### Physical Camera → Software ID

| Physical Camera | Software ID (on disk) |
|-----------------|----------------------|
| Cam 1           | cam_01 (correct)     |
| Cam 2           | **cam_03**           |
| Cam 3           | **cam_04**           |
| Cam 4           | **cam_02**           |

## Python Mapping

Copy this into any script that needs to translate camera IDs:

```python
# Software directory name → Physical camera label
# Use this when READING data and need to REPORT the physical camera
SOFTWARE_TO_PHYSICAL = {
    "cam_01": "cam_01",  # correct
    "cam_02": "cam_04",  # cam_02 on disk is Physical Cam 4
    "cam_03": "cam_02",  # cam_03 on disk is Physical Cam 2
    "cam_04": "cam_03",  # cam_04 on disk is Physical Cam 3
}

# Physical camera label → Software directory name
# Use this when you KNOW the physical camera and need to FIND its files
PHYSICAL_TO_SOFTWARE = {
    "cam_01": "cam_01",  # correct
    "cam_02": "cam_03",  # Physical Cam 2 files are in cam_03/
    "cam_03": "cam_04",  # Physical Cam 3 files are in cam_04/
    "cam_04": "cam_02",  # Physical Cam 4 files are in cam_02/
}
```

## Instructions for Any Analysis Session

**READ THIS FIRST before writing any code that touches HCM camera data.**

The files on disk use WRONG camera IDs. When you read from disk, you must
remap before reporting results. Here is exactly what to do:

### Step 1: Read files using software IDs (as stored on disk)

```python
# Paths on disk use software IDs — do NOT change these
RECORDINGS = "/home/exx/vast/lee/2024-09-24-LeeAPP"
INFERENCE = "/home/exx/vast/leo/datasets/inference-Kuo-Fen-HCM"

# To load Physical Cam 2's videos, read from cam_03/ (not cam_02/)
# To load Physical Cam 3's videos, read from cam_04/ (not cam_03/)
# To load Physical Cam 4's videos, read from cam_02/ (not cam_04/)
```

### Step 2: Apply mapping before any output, plots, or reports

```python
SOFTWARE_TO_PHYSICAL = {
    "cam_01": "cam_01",  # correct
    "cam_02": "cam_04",  # cam_02 on disk is Physical Cam 4
    "cam_03": "cam_02",  # cam_03 on disk is Physical Cam 2
    "cam_04": "cam_03",  # cam_04 on disk is Physical Cam 3
}

PHYSICAL_TO_SOFTWARE = {
    "cam_01": "cam_01",  # correct
    "cam_02": "cam_03",  # Physical Cam 2 files are in cam_03/
    "cam_03": "cam_04",  # Physical Cam 3 files are in cam_04/
    "cam_04": "cam_02",  # Physical Cam 4 files are in cam_02/
}
```

### Step 3: Use this pattern in every script

```python
from pathlib import Path

RECORDINGS = Path("/home/exx/vast/lee/2024-09-24-LeeAPP")

SOFTWARE_TO_PHYSICAL = {
    "cam_01": "cam_01",
    "cam_02": "cam_04",
    "cam_03": "cam_02",
    "cam_04": "cam_03",
}

# Read from disk using software IDs
for software_id in ["cam_01", "cam_02", "cam_03", "cam_04"]:
    cam_dir = RECORDINGS / software_id
    physical_id = SOFTWARE_TO_PHYSICAL[software_id]

    # ... do analysis on cam_dir ...

    # When printing/plotting/saving, use physical_id
    print(f"{physical_id}: {result}")
    # e.g., "cam_02: 24 videos" (Physical Cam 2, loaded from cam_03/)
```

### Common mistakes to avoid

- Do NOT use `cam_02` as a label when you loaded from `cam_02/` — that's Physical Cam 4
- Do NOT rename files or directories on VAST
- Do NOT hardcode `for cam in ["cam_01", "cam_02", ...]` in display order — use
  `["cam_01", "cam_03", "cam_04", "cam_02"]` to get physical order 1, 2, 3, 4
- If loading `.slp` inference files, the camera ID in the filename (`cam_03.00.predictions.slp`)
  is the SOFTWARE ID, not the physical camera

## Rules

1. **NEVER rename files or directories on VAST.** Robocopy and inference depend
   on current paths.
2. **Read from disk using software IDs** (cam_01-cam_04) as they are.
3. **Apply mapping at output/display time only** — when showing results to
   humans or writing analysis reports, translate to physical camera numbers.
4. **All code paths stay the same.** Only the final label changes.

## Example

```python
import json

# Load data as usual — no path changes
with open("hcm_daily_status.json") as f:
    data = json.load(f)

# When displaying, apply the mapping
for software_id, cam_data in data["dates"]["2026-05-12"]["cameras"].items():
    physical_id = SOFTWARE_TO_PHYSICAL[software_id]
    print(f"{physical_id}: {cam_data['videos']} videos")
    # Prints "cam_04: 12 videos" instead of "cam_02: 12 videos"
```

## Evidence

- User placed mice in physical Cam 3 and Cam 4
- Dashboard thumbnails show mice in cam_02/ and cam_04/ directories
- User physically verified labels on cameras on 2026-05-14
- Calibration boards sit under the cages — visible when cage/bedding removed

## Infrastructure

```
Recording PC (Windows)            VAST (shared storage)           exx (Linux)
  Bonsai (20+ nodes)                                               SLEAP inference 24/7
  assigns wrong cam IDs    robocopy 3AM                            reads by software ID
  cam_02 = Phys Cam 4  ──────────►  cam_02/cam_02.XX.mp4  ◄────── inference scripts
  cam_03 = Phys Cam 2  ──────────►  cam_03/cam_03.XX.mp4  ◄────── hcm-monitor
  cam_04 = Phys Cam 3  ──────────►  cam_04/cam_04.XX.mp4  ◄────── analysis tools
```

### Why we cannot rename

- **Robocopy** syncs by path. Renaming breaks the sync; new recordings still
  arrive with old names, creating duplicates.
- **Inference pipeline** runs 24/7 on multiple workstations with hardcoded paths.
- **Existing .slp files** reference source video paths internally.
- **Bonsai** names both directories and files — the mismatch originates at
  the source and is consistent throughout.

## Future Migration Plan

When a clean break is possible (recording PC reconfigured, inference complete):

1. Create new VAST folder (e.g., `2026-XX-XX-LeeAPP/`)
2. Update robocopy .bat to remap destinations:
   ```bat
   robocopy C:\recordings\cam_01 \\vast\new-folder\cam_01 /MIR
   robocopy C:\recordings\cam_02 \\vast\new-folder\cam_04 /MIR
   robocopy C:\recordings\cam_03 \\vast\new-folder\cam_02 /MIR
   robocopy C:\recordings\cam_04 \\vast\new-folder\cam_03 /MIR
   ```
3. Filenames inside will still say `cam_02.XX.mp4` etc. — accept this as
   cosmetic. The directory is what all tools key on.
4. Update scanner/inference to read from new folder.
5. Old data in `2024-09-24-LeeAPP/` stays untouched with mapping applied in software.
