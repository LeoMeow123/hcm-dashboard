#!/usr/bin/env bash
# HCM Dashboard daily update — run via cron at 6AM
# Scans VAST for new recordings, generates thumbnails, pushes to GitHub.
#
# Crontab entry:
#   3 6 * * * /home/exx/vast/leo/vibing/hcm-monitor/update_dashboard.sh >> /home/exx/vast/leo/vibing/hcm-monitor/update.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Dashboard update started: $(date '+%Y-%m-%d %H:%M:%S') ==="

# 1. Scan VAST (incremental — only new dates + incomplete inference)
echo "[1/5] Scanning VAST..."
python3 scan_daily.py

# 2. Generate thumbnails for last 30 days (skip existing)
echo "[2/5] Generating thumbnails..."
VIRTUAL_ENV= uv run --no-project --with opencv-python-headless gen_thumbs.py --days 30 --incremental

# 3. Git: manage 30-day sliding window of thumbs
echo "[3/5] Updating git thumbs..."
CUTOFF="$(date -d '30 days ago' '+%Y-%m-%d')"

# Collect dirs to remove and dirs to add
to_remove=()
to_add=()
for cam in cam_01 cam_02 cam_03 cam_04; do
  [ -d "thumbs/$cam" ] || continue
  for d in thumbs/$cam/*/; do
    [ -d "$d" ] || continue
    sdate="$(basename "$d")"
    sdate="${sdate:0:10}"
    if [[ "$sdate" < "$CUTOFF" ]]; then
      to_remove+=("$d")
    else
      to_add+=("$d")
    fi
  done
done

# Batch remove old from git index
if [ ${#to_remove[@]} -gt 0 ]; then
  echo "  Removing ${#to_remove[@]} old thumb dirs from git..."
  printf '%s\0' "${to_remove[@]}" | xargs -0 git rm -r --cached --quiet 2>/dev/null || true
fi

# Batch add recent
if [ ${#to_add[@]} -gt 0 ]; then
  echo "  Adding ${#to_add[@]} thumb dirs to git..."
  printf '%s\0' "${to_add[@]}" | xargs -0 git add -f 2>/dev/null || true
fi

# 4. Commit if there are changes
echo "[4/5] Committing..."
git add hcm_daily_status.json
if git diff --cached --quiet; then
  echo "No changes to commit."
else
  git commit -m "Daily update: $(date '+%Y-%m-%d')"
fi

# 5. Push
echo "[5/5] Pushing to GitHub..."
git push

echo "=== Dashboard update complete: $(date '+%Y-%m-%d %H:%M:%S') ==="
