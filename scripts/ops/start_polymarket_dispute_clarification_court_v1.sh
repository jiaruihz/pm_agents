#!/usr/bin/env bash
set -euo pipefail

ROOT="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"

PM_RUNTIME_ROOT="${PM_RUNTIME_ROOT:-$(weather_production_path "$ROOT" pm_runtime_root)}"
DATA_FEED_RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$(weather_production_path "$ROOT" data_feed_runtime_root)}"
OUTPUT_ROOT="${DISPUTE_REPRICING_OUTPUT_ROOT:-$PM_RUNTIME_ROOT/dispute_repricing/forward_v1}"
HEALTH_PATH="${DISPUTE_CLARIFICATION_HEALTH_PATH:-$OUTPUT_ROOT/clarification_health.json}"
LOG_PATH="${DISPUTE_CLARIFICATION_LOG_PATH:-$OUTPUT_ROOT/clarification_runtime.log}"
MODEL="${DISPUTE_CLARIFICATION_MODEL:-gpt-5.4}"
REASONING_EFFORT="${DISPUTE_CLARIFICATION_REASONING_EFFORT:-medium}"
MAX_NEW_CARDS="${DISPUTE_CLARIFICATION_MAX_NEW_CARDS:-8}"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
SESSION="polymarket_dispute_clarification_court_v1"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$DATA_FEED_RUNTIME_ROOT")"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$OUTPUT_ROOT"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "=$SESSION" 2>/dev/null; then
  echo "already running: session=$SESSION socket=$TMUX_SOCKET"
  exit 0
fi

weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && exec '$PY' -u '$ROOT/scripts/ops/polymarket_clarification_forward.py' \
    --loop --interval-seconds 60 --output-root '$OUTPUT_ROOT' --health-path '$HEALTH_PATH' \
    --codex --model '$MODEL' --reasoning-effort '$REASONING_EFFORT' \
    --max-new-cards '$MAX_NEW_CARDS' >> '$LOG_PATH' 2>&1"

echo "started session=$SESSION socket=$TMUX_SOCKET mode=zero_notional_shadow live_authority=false"
