#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
PM_RUNTIME_ROOT="${WEATHER_PM_RUNTIME_ROOT:-$(weather_production_path "$PROJECT_DIR" pm_runtime_root)}"
RUNTIME_DIR="${TMAX_FIRST_LOCK_NO_CURRENT_YES_RUNTIME_DIR:-$PM_RUNTIME_ROOT/weather_edge_v1/tmax_distribution_edge_first_lock_no_current_yes_shadow_v1}"
LOG_FILE="$RUNTIME_DIR/first_lock_no_current_yes_shadow_loop.log"
PY="$PROJECT_DIR/.venv/bin/python"
SESSION="${TMAX_FIRST_LOCK_NO_CURRENT_YES_SESSION:-tmax_distribution_edge_first_lock_no_current_yes_shadow_v1}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
SNAPSHOT_DIR="${TMAX_FIRST_LOCK_NO_CURRENT_YES_SNAPSHOT_DIR:-$(weather_production_path "$PROJECT_DIR" strategy_paper_snapshot_dir)}"

INTERVAL_SEC="${TMAX_FIRST_LOCK_NO_CURRENT_YES_INTERVAL_SEC:-900}"
ASK_FLOOR="${TMAX_FIRST_LOCK_NO_CURRENT_YES_ASK_FLOOR:-0.40}"
ASK_CEILING="${TMAX_FIRST_LOCK_NO_CURRENT_YES_ASK_CEILING:-0.99}"
EDGE_THRESHOLD="${TMAX_FIRST_LOCK_NO_CURRENT_YES_EDGE_THRESHOLD:-0.02}"
FIXED_SHARES="${TMAX_FIRST_LOCK_NO_CURRENT_YES_FIXED_SHARES:-5}"
MAX_ORDERS="${TMAX_FIRST_LOCK_NO_CURRENT_YES_MAX_ORDERS:-1}"
MAX_SNAPSHOT_AGE_MIN="${TMAX_FIRST_LOCK_NO_CURRENT_YES_MAX_SNAPSHOT_AGE_MIN:-60}"
MAX_FRESH_ASK_DRIFT="${TMAX_FIRST_LOCK_NO_CURRENT_YES_MAX_FRESH_ASK_DRIFT:-0.02}"
FEE_RATE="${TMAX_FIRST_LOCK_NO_CURRENT_YES_FEE_RATE:-0.05}"
CLOB_RETRIES="${TMAX_FIRST_LOCK_NO_CURRENT_YES_CLOB_RETRIES:-2}"

mkdir -p "$RUNTIME_DIR"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

runner_cmd=(
  "$PY" "-u" "scripts/ops/tmax_distribution_edge_live_candidate_v1.py" "loop"
  "--strategy-instance" "tmax_distribution_edge_first_lock_no_current_yes_shadow_v1"
  "--runtime-dir" "$RUNTIME_DIR"
  "--snapshot-dir" "$SNAPSHOT_DIR"
  "--interval-seconds" "$INTERVAL_SEC"
  "--policy-id" "first_lock_no_current_yes"
  "--first-lock-city-day"
  "--active-expression" "current_no,d1_no,d2_no,d1_yes,d2_yes"
  "--ask-floor" "$ASK_FLOOR"
  "--ask-ceiling" "$ASK_CEILING"
  "--edge-threshold" "$EDGE_THRESHOLD"
  "--fixed-shares" "$FIXED_SHARES"
  "--max-orders" "$MAX_ORDERS"
  "--max-snapshot-age-min" "$MAX_SNAPSHOT_AGE_MIN"
  "--max-fresh-ask-drift" "$MAX_FRESH_ASK_DRIFT"
  "--fee-rate" "$FEE_RATE"
  "--clob-retries" "$CLOB_RETRIES"
  "--exclude-trend3h-flat"
  "--execute"
)

runner_cmd_q=""
for part in "${runner_cmd[@]}"; do
  runner_cmd_q+="$(printf '%q' "$part") "
done

project_q="$(printf '%q' "$PROJECT_DIR")"
log_q="$(printf '%q' "$LOG_FILE")"
proxy_helper_q="$(printf '%q' "$PROJECT_DIR/scripts/ops/weather_market_proxy_env.sh")"
if [[ -f "$PROJECT_DIR/.env" ]]; then
  env_load="set -a; . $(printf '%q' "$PROJECT_DIR/.env"); set +a;"
else
  env_load=":"
fi

proxy_norm=". $proxy_helper_q; weather_export_market_proxy_env;"
env_check="printf '[%s] env_check http_proxy=%s https_proxy=%s all_proxy=%s wallet=%s pm_addr=%s clob_base=%s\\n' \"\$(date -u +%Y-%m-%dT%H:%M:%SZ)\" \"\$([[ -n \${HTTP_PROXY:-} ]] && echo 1 || echo 0)\" \"\$([[ -n \${HTTPS_PROXY:-} ]] && echo 1 || echo 0)\" \"\$([[ -n \${ALL_PROXY:-} ]] && echo 1 || echo 0)\" \"\$([[ -n \${PM:-}\${POLYGON_WALLET_PRIVATE_KEY:-} ]] && echo 1 || echo 0)\" \"\$([[ -n \${PM_ADDRESS:-} ]] && echo 1 || echo 0)\" \"\$([[ -n \${CLOB_BASE_URL:-} ]] && echo 1 || echo 0)\" >> $log_q"
bootstrap="cd $project_q && $env_load $proxy_norm $env_check && exec $runner_cmd_q >> $log_q 2>&1"

if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "already running tmux_socket=$TMUX_SOCKET session=$SESSION log=$LOG_FILE"
  exit 0
fi
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" "bash -lc $(printf '%q' "$bootstrap")"
echo "started tmux_socket=$TMUX_SOCKET session=$SESSION log=$LOG_FILE"
