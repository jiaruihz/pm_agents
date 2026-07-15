#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
OUTPUT_DIR="${CURRENT_YES_HEAT_DEATH_LIVE_OUTPUT_DIR:-$ROOT/runtime/weather_edge_v1/current_yes_heat_death_tiny_live_v1}"
SESSION="${CURRENT_YES_HEAT_DEATH_LIVE_SCREEN_SESSION:-weather_current_yes_heat_death_tiny_live_v1}"
LOG_FILE="$OUTPUT_DIR/runner.log"

mkdir -p "$OUTPUT_DIR"
if pgrep -f "weather_current_yes_heat_death_tiny_live_v1.py loop" >/dev/null 2>&1; then
  echo "current-YES heat-death tiny-live already running"
  exit 0
fi

screen -dmS "$SESSION" sh -c \
  "cd '$ROOT' && exec '$PY' -u scripts/ops/weather_current_yes_heat_death_tiny_live_v1.py loop \
    --output-dir '$OUTPUT_DIR' \
    --fixed-order-shares 10 \
    --max-orders-per-utc-day 3 \
    --max-ask 0.99 \
    --max-snapshot-age-min 20 \
    --order-ttl-min 15 \
    --interval-seconds 30 \
    --live --confirm-live >> '$LOG_FILE' 2>&1"

echo "started $SESSION output=$OUTPUT_DIR log=$LOG_FILE"
