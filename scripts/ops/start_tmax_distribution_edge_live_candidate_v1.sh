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

ENV_PREFIX=""
if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
  ENV_PREFIX="set -a; . $(printf '%q' "$PROJECT_DIR/.env"); set +a;"
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

cmd=(
  "cd" "$PROJECT_DIR" "&&"
  "$ENV_PREFIX"
  "$PY" "-u" "scripts/ops/tmax_distribution_edge_live_candidate_v1.py"
  "${args[@]}"
  ">>" "$LOG_FILE" "2>&1"
)

if command -v tmux >/dev/null 2>&1; then
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "already running tmux session=$SESSION log=$LOG_FILE"
    exit 0
  fi
  tmux new-session -d -s "$SESSION" "${cmd[*]}"
  echo "started tmux session=$SESSION log=$LOG_FILE"
  exit 0
fi

nohup bash -lc "${cmd[*]}" >/dev/null 2>&1 < /dev/null &
echo "started pid=$! log=$LOG_FILE"
