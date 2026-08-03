#!/usr/bin/env bash
set -euo pipefail

# Exact zero-notional production contract for Helsinki FMI pre-cross ladder
# capture.  This is intentionally separate from the generic stale-book
# launcher so controller recovery cannot inherit changing generic defaults.

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="weather_helsinki_pre_cross_active_ladder_shadow"
OUTPUT_DIR="$RUNTIME_ROOT/output/helsinki_pre_cross_active_ladder_shadow"
LOG_FILE="$RUNTIME_ROOT/loop/helsinki_pre_cross_active_ladder_shadow.log"
PY="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "=$TMUX_SESSION" 2>/dev/null; then
  echo "already running session=$TMUX_SESSION socket=$TMUX_SOCKET"
  exit 0
fi

cmd=(
  "$PY"
  "$PROJECT_DIR/scripts/ops/weather_fast_source_stale_book_observer.py"
  --loop
  --output-dir "$OUTPUT_DIR"
  --high-frequency-latest "$RUNTIME_ROOT/output/live_cross_observations/latest.json"
  --interval-seconds 60
  --cities Helsinki
  --sources fmi
  --fresh-scope t_minus_1_no
  --continuous-active-brackets
  --active-bracket-cities Helsinki
  --active-bracket-offsets -1 0 1
  --market-proxy http://127.0.0.1:7890
)

weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd $(printf '%q' "$PROJECT_DIR") && exec $(printf '%q ' "${cmd[@]}") >> $(printf '%q' "$LOG_FILE") 2>&1"

echo "started session=$TMUX_SESSION socket=$TMUX_SOCKET output=$OUTPUT_DIR mode=telemetry_only_no_orders"
