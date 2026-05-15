#!/usr/bin/env bash
# HCM Dashboard daily update — run via cron at 6AM
# Scans VAST for new recordings, generates thumbnails, pushes to GitHub.
#
# Crontab entry:
#   3 6 * * * /home/exx/vast/leo/vibing/hcm-monitor/update_dashboard.sh >> /home/exx/vast/leo/vibing/hcm-monitor/update.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

LOGDATE="$(date '+%Y-%m-%d %H:%M:%S')"
echo "=== Dashboard update started: $LOGDATE ==="

# 1. Scan VAST (incremental — only new dates)
echo "[1/5] Scanning VAST..."
python3 scan_daily.py

# 2. Generate thumbnails for last 30 days (skip existing)
echo "[2/5] Generating thumbnails..."
VIRTUAL_ENV= uv run --no-project --with opencv-python-headless gen_thumbs.py --days 30 --incremental

# 3. Git: remove old thumbs beyond 30 days, add new ones
echo "[3/5] Updating git thumbs..."
CUTOFF_DATE="$(date -d '30 days ago' '+%Y-%m-%d')"

# Remove tracked thumbs older than 30 days
for cam in cam_01 cam_02 cam_03 cam_04; do
  if [ -d "thumbs/$cam" ]; then
    for session_dir in thumbs/$cam/*/; do
      [ -d "$session_dir" ] || continue
      session_name="$(basename "$session_dir")"
      # Extract date from session name (YYYY-MM-DD-HH-MM-SS)
      session_date="${session_name:0:10}"
      if [[ "$session_date" < "$CUTOFF_DATE" ]]; then
        git rm -r --cached --quiet "$session_dir" 2>/dev/null || true
      fi
    done
  fi
done

# Force-add last 30 days of thumbs
for cam in cam_01 cam_02 cam_03 cam_04; do
  if [ -d "thumbs/$cam" ]; then
    for session_dir in thumbs/$cam/*/; do
      [ -d "$session_dir" ] || continue
      session_name="$(basename "$session_dir")"
      session_date="${session_name:0:10}"
      if [[ "$session_date" > "$CUTOFF_DATE" ]] || [[ "$session_date" == "$CUTOFF_DATE" ]]; then
        git add -f "$session_dir" 2>/dev/null || true
      fi
    done
  fi
done

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
