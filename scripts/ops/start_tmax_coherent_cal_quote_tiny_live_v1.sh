#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
RUNTIME_DIR="${TMAX_COHERENT_CAL_QUOTE_RUNTIME_DIR:-$PROJECT_DIR/runtime/weather_edge_v1/tmax_coherent_cal_quote_tiny_live_v1}"
LOG_FILE="$RUNTIME_DIR/tmax_coherent_cal_quote_tiny_live_loop.log"
PY="$PROJECT_DIR/.venv/bin/python"
SESSION="${TMAX_COHERENT_CAL_QUOTE_SESSION:-tmax_coherent_cal_quote_tiny_live_v1}"

INTERVAL_SEC="${TMAX_COHERENT_CAL_QUOTE_INTERVAL_SEC:-900}"
MAX_OBS_AGE_MIN="${TMAX_COHERENT_CAL_QUOTE_MAX_OBS_AGE_MIN:-90}"
LIVE="${TMAX_COHERENT_CAL_QUOTE_LIVE:-1}"
CONFIRM_LIVE="${TMAX_COHERENT_CAL_QUOTE_CONFIRM_LIVE:-${WEATHER_STRATEGY_CONFIRM_LIVE:-0}}"

if [[ "$LIVE" == "1" && "$CONFIRM_LIVE" != "1" ]]; then
  echo "refusing live mode: set TMAX_COHERENT_CAL_QUOTE_CONFIRM_LIVE=1 explicitly" >&2
  exit 2
fi

mkdir -p "$RUNTIME_DIR"
[[ -x "$PY" ]] || PY="python3"

runner_cmd=(
  "$PY" -u scripts/ops/tmax_distribution_edge_live_candidate_v1.py loop
  --strategy-instance tmax_coherent_cal_quote_tiny_live_v1
  --strategy-id tmax_coherent_cal_quote_tiny_live_v1
  --runtime-dir "$RUNTIME_DIR"
  --model-mode coherent_cal_quote
  --interval-seconds "$INTERVAL_SEC"
  --policy-id first_lock_no_current_yes
  --first-lock-city-day
  --prior-live-orders "$PROJECT_DIR/runtime/weather_edge_v1/tmax_distribution_edge_first_lock_no_current_yes_tiny_live_v1/live_orders.jsonl"
  --active-expression current_no,d1_no,d2_no,d1_yes,d2_yes
  --ask-floor 0.40
  --ask-ceiling 0.99
  --edge-threshold 0.02
  --fixed-shares 5
  --max-orders 1
  --max-snapshot-age-min 60
  --max-obs-age-min "$MAX_OBS_AGE_MIN"
  --max-fresh-ask-drift 0.02
  --fee-rate 0.05
  --clob-retries 2
  --exclude-trend3h-flat
  --execute
)
if [[ "$LIVE" == "1" ]]; then
  runner_cmd+=(--live --confirm-live)
fi

runner_cmd_q=""
for part in "${runner_cmd[@]}"; do runner_cmd_q+="$(printf '%q' "$part") "; done
project_q="$(printf '%q' "$PROJECT_DIR")"
log_q="$(printf '%q' "$LOG_FILE")"
proxy_helper_q="$(printf '%q' "$PROJECT_DIR/scripts/ops/weather_market_proxy_env.sh")"
env_load=":"
if [[ -f "$PROJECT_DIR/.env" ]]; then
  env_load="set -a; . $(printf '%q' "$PROJECT_DIR/.env"); set +a;"
fi
bootstrap="cd $project_q && $env_load . $proxy_helper_q; weather_export_market_proxy_env; exec $runner_cmd_q >> $log_q 2>&1"

if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "already running tmux_socket=$TMUX_SOCKET session=$SESSION log=$LOG_FILE"
  exit 0
fi
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" "bash -lc $(printf '%q' "$bootstrap")"
echo "started tmux_socket=$TMUX_SOCKET session=$SESSION log=$LOG_FILE"
