#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

PM_RUNTIME_ROOT="${PM_RUNTIME_ROOT:-$(weather_production_path "$PROJECT_DIR" pm_runtime_root)}"
DATA_FEED_RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$(weather_production_path "$PROJECT_DIR" data_feed_runtime_root)}"
RESEARCH_ARTIFACT_ROOT="${WEATHER_RESEARCH_ARTIFACT_ROOT:-$(weather_production_path "$PROJECT_DIR" research_artifact_root)}"
OUTPUT_ROOT="${WEATHER_AMSTERDAM_WCIR_OUTPUT_ROOT:-$PM_RUNTIME_ROOT/research/wcir_amsterdam_frozen_v2}"
BOOTSTRAP_PREDICTIONS="${WEATHER_AMSTERDAM_WCIR_BOOTSTRAP_PREDICTIONS:-$RESEARCH_ARTIFACT_ROOT/wcir_amsterdam_score_only_shadow/epoch=wcir_amsterdam_score_only_v2_bb04a8acbeafd66b/predictions.jsonl}"
REVIEW_OUTPUT="${WEATHER_AMSTERDAM_WCIR_REVIEW_OUTPUT:-$PROJECT_DIR/reviews/wcir_unified_data_amsterdam_pilot_v1_1}"
SOURCE_EVENTS="${WEATHER_AMSTERDAM_WCIR_SOURCE_EVENTS:-$DATA_FEED_RUNTIME_ROOT/output/source_events}"
OBSERVATIONS_ROOT="${WEATHER_AMSTERDAM_WCIR_OBSERVATIONS_ROOT:-$DATA_FEED_RUNTIME_ROOT/output/observations}"
KNMI_ROOT="${WEATHER_AMSTERDAM_WCIR_KNMI_ROOT:-$DATA_FEED_RUNTIME_ROOT/output/knmi_open_data}"
DECISIONS="${WEATHER_AMSTERDAM_WCIR_DECISIONS:-$DATA_FEED_RUNTIME_ROOT/output/city_probability_runtime_v3/decision_bundles.jsonl}"
MARKET_LATEST="${WEATHER_AMSTERDAM_WCIR_MARKET_LATEST:-$DATA_FEED_RUNTIME_ROOT/market_books/latest.json}"
SETTLEMENT_DB="${WEATHER_AMSTERDAM_WCIR_SETTLEMENT_DB:-$PM_RUNTIME_ROOT/weather.db}"
INTERVAL_SECONDS="${WEATHER_AMSTERDAM_WCIR_INTERVAL_SECONDS:-60}"
SESSION="${WEATHER_AMSTERDAM_WCIR_SESSION:-weather_amsterdam_wcir_frozen_shadow_v2}"
PY="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
LOG_FILE="$OUTPUT_ROOT/runtime.log"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$DATA_FEED_RUNTIME_ROOT")"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$OUTPUT_ROOT"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "=$SESSION" 2>/dev/null; then
  echo "already running: session=$SESSION socket=$TMUX_SOCKET"
  exit 0
fi

runner=(
  "$PY" -u "$PROJECT_DIR/scripts/ops/weather_amsterdam_wcir_shadow_v2.py"
  --loop-seconds "$INTERVAL_SECONDS"
  --shadow-root "$OUTPUT_ROOT"
  --bootstrap-predictions "$BOOTSTRAP_PREDICTIONS"
  --review-output "$REVIEW_OUTPUT"
  --knmi-root "$KNMI_ROOT"
  --observations-root "$OBSERVATIONS_ROOT"
  --decisions "$DECISIONS"
  --source-events "$SOURCE_EVENTS"
  --market-latest "$MARKET_LATEST"
  --ws-runtime-root "$DATA_FEED_RUNTIME_ROOT"
  --settlement-db "$SETTLEMENT_DB"
  --max-events 256
)
runner_q=""
for part in "${runner[@]}"; do
  runner_q+="$(printf '%q' "$part") "
done
project_q="$(printf '%q' "$PROJECT_DIR")"
log_q="$(printf '%q' "$LOG_FILE")"
bootstrap="cd $project_q && exec $runner_q >> $log_q 2>&1"
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" "bash -lc $(printf '%q' "$bootstrap")"

echo "started session=$SESSION socket=$TMUX_SOCKET mode=zero_notional_shadow live_authority=false orders=0 fills=0 notional=0"
