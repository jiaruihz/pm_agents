#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
SESSION="weather_tokyo_current_break_active_ladder_shadow_v1"
OUTPUT_DIR="$RUNTIME_ROOT/output/tokyo_current_break_active_ladder_shadow"
LOG_FILE="$RUNTIME_ROOT/loop/tokyo_current_break_active_ladder_shadow_v1.log"
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

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"
weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "=$SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && exec '$ROOT/.venv/bin/python' '$ROOT/scripts/ops/weather_fast_source_stale_book_observer.py' --loop --output-dir '$OUTPUT_DIR' --high-frequency-latest '$RUNTIME_ROOT/output/live_cross_observations/latest.json' --interval-seconds 60 --cities Tokyo --sources jma_amedas --fresh-scope all --continuous-active-brackets --active-bracket-cities Tokyo --active-bracket-offsets -1 0 1 --market-proxy http://127.0.0.1:7890 >> '$LOG_FILE' 2>&1"
echo "started session=$SESSION socket=$TMUX_SOCKET output=$OUTPUT_DIR mode=telemetry_only_no_orders"
