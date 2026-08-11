#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$(weather_production_path "$PROJECT_DIR" data_feed_runtime_root)}"
INPUT_ROOT="${WEATHER_TMIN_CROSS_PREV_NO_INPUT_ROOT:-$RUNTIME_ROOT/output/source_event_ladder_repricing_shadow/lowest_10m}"
OUTPUT_DIR="${WEATHER_TMIN_CROSS_PREV_NO_OUTPUT_DIR:-$RUNTIME_ROOT/output/tmin_cross_prev_no_shadow_v1}"
SESSION="${WEATHER_TMIN_CROSS_PREV_NO_SESSION:-weather_tmin_cross_prev_no_shadow_v1}"
INTERVAL="${WEATHER_TMIN_CROSS_PREV_NO_INTERVAL_SEC:-30}"
LOG_FILE="$OUTPUT_DIR/runner.log"
PY="$PROJECT_DIR/.venv/bin/python"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"

mkdir -p "$OUTPUT_DIR"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "already running tmux_socket=$TMUX_SOCKET session=$SESSION output=$OUTPUT_DIR"
  exit 0
fi

runner=(
  "$PY" -u scripts/ops/weather_tmin_cross_prev_no_shadow_v1.py
  --events "$INPUT_ROOT/events.jsonl"
  --quotes "$INPUT_ROOT/quote_snapshots.jsonl"
  --output-dir "$OUTPUT_DIR"
  --loop
  --interval-seconds "$INTERVAL"
)
runner_q=""
for part in "${runner[@]}"; do
  runner_q+="$(printf '%q' "$part") "
done
project_q="$(printf '%q' "$PROJECT_DIR")"
log_q="$(printf '%q' "$LOG_FILE")"
bootstrap="cd $project_q && exec $runner_q >> $log_q 2>&1"
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" "bash -lc $(printf '%q' "$bootstrap")"
echo "started tmux_socket=$TMUX_SOCKET session=$SESSION output=$OUTPUT_DIR mode=zero_notional_shadow orders_enabled=false"
