#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
CONFIG="${CITY_PROBABILITY_SHADOW_CONFIG:-$ROOT/configs/weather/city_probability_shadow_v2.json}"
OUTPUT_DIR="${CITY_PROBABILITY_SHADOW_OUTPUT_DIR:-$RUNTIME_ROOT/output/city_probability_shadow_v2}"
SESSION="weather_city_probability_shadow_v2"
LOG_FILE="$OUTPUT_DIR/runner.log"
ACTION="${1:-start}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"

if [[ "$ACTION" == "status" ]]; then
  weather_jrs_tmux "$TMUX_SOCKET" list-panes -t "=$SESSION" -F '#{session_name} #{pane_pid} #{pane_start_command}'
  exit 0
fi
if [[ "$ACTION" == "stop" ]]; then
  weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "=$SESSION"
  echo "stopped session=$SESSION"
  exit 0
fi
if [[ "$ACTION" != "start" ]]; then
  echo "usage: $0 [start|stop|status]" >&2
  exit 2
fi
if [[ ! -x "$PY" ]]; then
  echo "python interpreter is not executable: $PY (set PYTHON_BIN explicitly)" >&2
  exit 1
fi

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$OUTPUT_DIR"
weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "=$SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && exec '$PY' -u '$ROOT/scripts/ops/weather_city_probability_shadow_v2.py' loop --config '$CONFIG' --interval-seconds 60 >> '$LOG_FILE' 2>&1"
echo "started session=$SESSION socket=$TMUX_SOCKET output=$OUTPUT_DIR config=$CONFIG"
