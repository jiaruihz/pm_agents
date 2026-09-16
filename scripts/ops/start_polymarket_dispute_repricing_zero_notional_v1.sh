#!/usr/bin/env bash
set -euo pipefail

ROOT="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"

PM_RUNTIME_ROOT="${PM_RUNTIME_ROOT:-$(weather_production_path "$ROOT" pm_runtime_root)}"
DATA_FEED_RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$(weather_production_path "$ROOT" data_feed_runtime_root)}"
OUTPUT_ROOT="${DISPUTE_REPRICING_OUTPUT_ROOT:-$PM_RUNTIME_ROOT/dispute_repricing/forward_v1}"
DB_PATH="${DISPUTE_REPRICING_DB_PATH:-$PM_RUNTIME_ROOT/dispute_repricing/dispute.db}"
MODEL_PATH="${DISPUTE_REPRICING_MODEL_PATH:-$PM_RUNTIME_ROOT/dispute_repricing/dispute_case_panel_v1/market_plus_rules_model.joblib}"
MODEL_SHA256="${DISPUTE_REPRICING_MODEL_SHA256:-0f5f06798c91451b76b897eb0e882fafd50b80b1a4a831a1781360ec09910252}"
EPOCH_ROOT="${DISPUTE_REPRICING_SUBSCRIPTION_EPOCH_ROOT:-$DATA_FEED_RUNTIME_ROOT/market_books/ws_incremental/subscription_epochs}"
HEALTH_PATH="${DISPUTE_REPRICING_HEALTH_PATH:-$OUTPUT_ROOT/health.json}"
LOG_PATH="${DISPUTE_REPRICING_LOG_PATH:-$OUTPUT_ROOT/runtime.log}"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
SESSION="polymarket_dispute_repricing_zero_notional_v1"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$DATA_FEED_RUNTIME_ROOT")"

if [[ ! -f "$MODEL_PATH" ]]; then
  echo "missing dispute model artifact: $MODEL_PATH" >&2
  exit 1
fi

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$OUTPUT_ROOT"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "=$SESSION" 2>/dev/null; then
  echo "already running: session=$SESSION socket=$TMUX_SOCKET"
  exit 0
fi

weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && exec '$PY' -u '$ROOT/scripts/ops/polymarket_dispute_forward.py' \
    --loop --interval-seconds 30 --output-root '$OUTPUT_ROOT' --canonical-db '$DB_PATH' \
    --model '$MODEL_PATH' \
    --model-sha256 '$MODEL_SHA256' \
    --subscription-epoch-root '$EPOCH_ROOT' --health-path '$HEALTH_PATH' \
    >> '$LOG_PATH' 2>&1"

echo "started session=$SESSION socket=$TMUX_SOCKET mode=zero_notional_shadow live_authority=false"
