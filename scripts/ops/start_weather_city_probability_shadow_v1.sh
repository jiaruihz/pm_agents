#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
CONFIG="${CITY_PROBABILITY_SHADOW_CONFIG:-$ROOT/configs/weather/city_probability_shadow_v1.json}"
OUTPUT_DIR="${CITY_PROBABILITY_SHADOW_OUTPUT_DIR:-/Volumes/jrs/weather_data_feed_service_runtime/output/city_probability_shadow_v1}"
SESSION="${CITY_PROBABILITY_SHADOW_TMUX_SESSION:-weather_city_probability_shadow_v1}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
LOG_FILE="$OUTPUT_DIR/runner.log"

mkdir -p "$OUTPUT_DIR"
if pgrep -f "weather_city_probability_shadow_v1.py loop" >/dev/null 2>&1; then
  echo "city probability shadow already running"
  exit 0
fi
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && exec '$PY' -u scripts/ops/weather_city_probability_shadow_v1.py loop --config '$CONFIG' --interval-seconds 60 >> '$LOG_FILE' 2>&1"
echo "started tmux_socket=$TMUX_SOCKET session=$SESSION output=$OUTPUT_DIR log=$LOG_FILE"
