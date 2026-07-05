#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_DIR="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_RUNTIME_DIR:-$PROJECT_DIR/runtime/weather_edge_v1/tmax_distribution_edge_live_candidate_v1}"
LOG_FILE="$RUNTIME_DIR/live_candidate_loop.log"
PY="$PROJECT_DIR/.venv/bin/python"
SESSION="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_SESSION:-tmax_distribution_edge_live_candidate_v1}"

INTERVAL_SEC="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_INTERVAL_SEC:-900}"
ASK_FLOOR="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_ASK_FLOOR:-0.20}"
ASK_CEILING="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_ASK_CEILING:-0.99}"
EDGE_THRESHOLD="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_EDGE_THRESHOLD:-0.02}"
FIXED_SHARES="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_FIXED_SHARES:-5}"
MAX_ORDERS="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_MAX_ORDERS:-1}"
MAX_SNAPSHOT_AGE_MIN="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_MAX_SNAPSHOT_AGE_MIN:-60}"
MAX_FRESH_ASK_DRIFT="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_MAX_FRESH_ASK_DRIFT:-0.02}"
FEE_RATE="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_FEE_RATE:-0.05}"
CLOB_RETRIES="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_CLOB_RETRIES:-2}"
EXCLUDE_TREND3H_FLAT="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_EXCLUDE_TREND3H_FLAT:-1}"
EXECUTE="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_EXECUTE:-1}"
LIVE="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_LIVE:-0}"
CONFIRM_LIVE="${TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_CONFIRM_LIVE:-0}"

mkdir -p "$RUNTIME_DIR"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

args=(
  "loop"
  "--runtime-dir" "$RUNTIME_DIR"
  "--interval-seconds" "$INTERVAL_SEC"
  "--ask-floor" "$ASK_FLOOR"
  "--ask-ceiling" "$ASK_CEILING"
  "--edge-threshold" "$EDGE_THRESHOLD"
  "--fixed-shares" "$FIXED_SHARES"
  "--max-orders" "$MAX_ORDERS"
  "--max-snapshot-age-min" "$MAX_SNAPSHOT_AGE_MIN"
  "--max-fresh-ask-drift" "$MAX_FRESH_ASK_DRIFT"
  "--fee-rate" "$FEE_RATE"
  "--clob-retries" "$CLOB_RETRIES"
)

if [[ "$EXCLUDE_TREND3H_FLAT" == "1" ]]; then
  args+=("--exclude-trend3h-flat")
else
  args+=("--allow-trend3h-flat")
fi

if [[ "$EXECUTE" == "1" ]]; then
  args+=("--execute")
fi

if [[ "$LIVE" == "1" ]]; then
  if [[ "$CONFIRM_LIVE" != "1" ]]; then
    echo "refusing live mode: set TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_CONFIRM_LIVE=1 explicitly" >&2
    exit 2
  fi
  args+=("--live" "--confirm-live")
fi

runner_cmd=(
  "$PY" "-u" "scripts/ops/tmax_distribution_edge_live_candidate_v1.py"
  "${args[@]}"
)

runner_cmd_q=""
for part in "${runner_cmd[@]}"; do
  runner_cmd_q+="$(printf '%q' "$part") "
done

project_q="$(printf '%q' "$PROJECT_DIR")"
log_q="$(printf '%q' "$LOG_FILE")"
if [[ -f "$PROJECT_DIR/.env" ]]; then
  env_load="set -a; . $(printf '%q' "$PROJECT_DIR/.env"); set +a;"
else
  env_load=":"
fi

proxy_norm='if [[ -z ${HTTP_PROXY:-} && -n ${WEATHER_DATA_FEED_MARKET_PROXY:-} ]]; then export HTTP_PROXY="$WEATHER_DATA_FEED_MARKET_PROXY"; fi; if [[ -z ${HTTPS_PROXY:-} && -n ${WEATHER_DATA_FEED_MARKET_PROXY:-} ]]; then export HTTPS_PROXY="$WEATHER_DATA_FEED_MARKET_PROXY"; fi; if [[ -z ${ALL_PROXY:-} && -n ${WEATHER_DATA_FEED_MARKET_PROXY:-} ]]; then export ALL_PROXY="$WEATHER_DATA_FEED_MARKET_PROXY"; fi; export http_proxy="${http_proxy:-${HTTP_PROXY:-}}"; export https_proxy="${https_proxy:-${HTTPS_PROXY:-}}"; export all_proxy="${all_proxy:-${ALL_PROXY:-}}";'
env_check="printf '[%s] env_check http_proxy=%s https_proxy=%s all_proxy=%s wallet=%s pm_addr=%s clob_base=%s\\n' \"\$(date -u +%Y-%m-%dT%H:%M:%SZ)\" \"\$([[ -n \${HTTP_PROXY:-} ]] && echo 1 || echo 0)\" \"\$([[ -n \${HTTPS_PROXY:-} ]] && echo 1 || echo 0)\" \"\$([[ -n \${ALL_PROXY:-} ]] && echo 1 || echo 0)\" \"\$([[ -n \${PM:-}\${POLYGON_WALLET_PRIVATE_KEY:-} ]] && echo 1 || echo 0)\" \"\$([[ -n \${PM_ADDRESS:-} ]] && echo 1 || echo 0)\" \"\$([[ -n \${CLOB_BASE_URL:-} ]] && echo 1 || echo 0)\" >> $log_q"
bootstrap="cd $project_q && $env_load $proxy_norm $env_check && exec $runner_cmd_q >> $log_q 2>&1"

if command -v tmux >/dev/null 2>&1; then
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "already running tmux session=$SESSION log=$LOG_FILE"
    exit 0
  fi
  tmux new-session -d -s "$SESSION" "bash -lc $(printf '%q' "$bootstrap")"
  echo "started tmux session=$SESSION log=$LOG_FILE"
  exit 0
fi

nohup bash -lc "$bootstrap" >/dev/null 2>&1 < /dev/null &
echo "started pid=$! log=$LOG_FILE"
