#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
SESSION="${WEATHER_LIVE_PATROL_SCREEN_SESSION:-weather_live_runtime_patrol}"
LOG_FILE="$RUNTIME_ROOT/loop/weather_live_runtime_patrol.log"

mkdir -p "$RUNTIME_ROOT/loop" "$RUNTIME_ROOT/output/live_runtime_patrol"
screen -S "$SESSION" -X quit 2>/dev/null || true
pkill -f "$PROJECT_DIR/scripts/ops/weather_live_runtime_patrol.py --loop" 2>/dev/null || true
screen -dmS "$SESSION" sh -c \
  "cd $(printf '%q' "$PROJECT_DIR") && exec $(printf '%q' "$PROJECT_DIR/.venv/bin/python") $(printf '%q' "$PROJECT_DIR/scripts/ops/weather_live_runtime_patrol.py") --loop --interval-sec 60 --lookback-min 5 --stop-runner-on-submit-failure >> $(printf '%q' "$LOG_FILE") 2>&1"

echo "started weather live runtime patrol session=$SESSION interval_sec=60"
echo "health=$RUNTIME_ROOT/output/live_runtime_patrol/latest.json"
echo "log=$LOG_FILE"
