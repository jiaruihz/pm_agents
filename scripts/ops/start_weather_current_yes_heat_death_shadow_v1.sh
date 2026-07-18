#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
OUTPUT_DIR="${CURRENT_YES_HEAT_DEATH_OUTPUT_DIR:-$ROOT/runtime/weather_edge_v1/current_yes_heat_death_shadow_v1}"
SESSION="${CURRENT_YES_HEAT_DEATH_TMUX_SESSION:-${CURRENT_YES_HEAT_DEATH_SCREEN_SESSION:-weather_current_yes_heat_death_shadow_v1}}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "${WEATHER_DATA_FEED_TMUX_SOCKET:-}")"
LOG_FILE="$OUTPUT_DIR/runner.log"

mkdir -p "$OUTPUT_DIR"
if pgrep -f "weather_current_yes_heat_death_shadow_v1.py loop" >/dev/null 2>&1; then
  echo "current-YES heat-death shadow already running"
  exit 0
fi

tmux -L "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && exec '$PY' -u scripts/ops/weather_current_yes_heat_death_shadow_v1.py loop \
    --snapshot-dir '$RUNTIME_ROOT/targeted_output/paper_snapshots' \
    --observation-cache '$RUNTIME_ROOT/output/observations/latest.json' \
    --forecast-curve-dir '$RUNTIME_ROOT/targeted_output/forecast_hourly_curves' \
    --output-dir '$OUTPUT_DIR' \
    --interval-seconds 30 >> '$LOG_FILE' 2>&1"

echo "started tmux_socket=$TMUX_SOCKET session=$SESSION output=$OUTPUT_DIR log=$LOG_FILE"
