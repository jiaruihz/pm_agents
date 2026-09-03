#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

PM_RUNTIME_ROOT="${PM_RUNTIME_ROOT:-$(weather_production_path "$PROJECT_DIR" pm_runtime_root)}"
DATA_FEED_RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$(weather_production_path "$PROJECT_DIR" data_feed_runtime_root)}"
OUTPUT_ROOT="${WEATHER_WCIR_HT_OUTPUT_ROOT:-$PM_RUNTIME_ROOT/research/wcir_next_print_helsinki_tokyo_frozen_v1}"
REVIEW_ROOT="${WEATHER_WCIR_HT_REVIEW_ROOT:-$PROJECT_DIR/reviews/wcir_next_official_print_helsinki_tokyo_frozen_v1}"
EVENTS="${WEATHER_WCIR_HT_EVENTS:-$DATA_FEED_RUNTIME_ROOT/output/fast_source_prev_no_trial/events.jsonl}"
PRODUCER_HEALTH="${WEATHER_WCIR_HT_PRODUCER_HEALTH:-$DATA_FEED_RUNTIME_ROOT/output/fast_source_prev_no_trial/latest_summary.json}"
FAST_PATH_ROOT="${WEATHER_WCIR_HT_FAST_PATH_ROOT:-$DATA_FEED_RUNTIME_ROOT/output/live_cross_observations}"
OFFICIAL_PATH_ROOT="${WEATHER_WCIR_HT_OFFICIAL_PATH_ROOT:-$DATA_FEED_RUNTIME_ROOT/output/source_events}"
SETTLEMENT_DB="${WEATHER_WCIR_HT_SETTLEMENT_DB:-$PM_RUNTIME_ROOT/weather.db}"
MARKET_LATEST="${WEATHER_WCIR_HT_MARKET_LATEST:-$DATA_FEED_RUNTIME_ROOT/market_books/latest.json}"
FORWARD_EPOCH_ID="${WEATHER_WCIR_HT_FORWARD_EPOCH_ID:-wcir_ht_next_print_action_shadow_v2_20260904a}"
FORWARD_START_UTC="${WEATHER_WCIR_HT_FORWARD_START_UTC:-2026-09-03T16:45:00Z}"
INTERVAL_SECONDS="${WEATHER_WCIR_HT_INTERVAL_SECONDS:-60}"
SESSION="${WEATHER_WCIR_HT_SESSION:-weather_wcir_next_print_helsinki_tokyo_frozen_shadow_v1}"
PY="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
LOG_FILE="$OUTPUT_ROOT/runtime.log"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$DATA_FEED_RUNTIME_ROOT")"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$OUTPUT_ROOT"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "=$SESSION" 2>/dev/null; then
  echo "already running: session=$SESSION socket=$TMUX_SOCKET"
  exit 0
fi

runner=(
  "$PY" -u "$PROJECT_DIR/scripts/ops/weather_wcir_next_print_helsinki_tokyo_frozen_shadow_v1.py"
  --loop-seconds "$INTERVAL_SECONDS"
  --shadow-root "$OUTPUT_ROOT"
  --review-root "$REVIEW_ROOT"
  --events "$EVENTS"
  --producer-health "$PRODUCER_HEALTH"
  --fast-path-root "$FAST_PATH_ROOT"
  --official-path-root "$OFFICIAL_PATH_ROOT"
  --settlement-db "$SETTLEMENT_DB"
  --ws-runtime-root "$DATA_FEED_RUNTIME_ROOT"
  --market-latest "$MARKET_LATEST"
  --forward-epoch-id "$FORWARD_EPOCH_ID"
  --forward-start-utc "$FORWARD_START_UTC"
)
runner_q=""
for part in "${runner[@]}"; do
  runner_q+="$(printf '%q' "$part") "
done
project_q="$(printf '%q' "$PROJECT_DIR")"
log_q="$(printf '%q' "$LOG_FILE")"
bootstrap="cd $project_q && exec $runner_q >> $log_q 2>&1"
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" "bash -lc $(printf '%q' "$bootstrap")"

echo "started session=$SESSION socket=$TMUX_SOCKET mode=zero_notional_action_shadow epoch=$FORWARD_EPOCH_ID live_authority=false orders=0 fills=0 notional=0"
