#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
INTERVAL_SEC="${INTERVAL_SEC:-1800}"
INITIAL_DELAY_SEC="${INITIAL_DELAY_SEC:-300}"
EXECUTION_POLICY="${WEATHER_BRANCH_EXECUTION_POLICY:-maker_queue_v1}"
SOURCE_POLICY="${WEATHER_BRANCH_SOURCE_POLICY:-mid_price_core_v1}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/live_cycle"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"

echo "weather_policy_branch_loop started at $(date -Is), policy=${EXECUTION_POLICY}, source=${SOURCE_POLICY}, initial_delay=${INITIAL_DELAY_SEC}s, interval=${INTERVAL_SEC}s"
sleep "$INITIAL_DELAY_SEC"

while true; do
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  log="$LOG_DIR/policy_branch_${EXECUTION_POLICY}_${ts}.log"
  echo "[$(date -Is)] policy branch start: policy=${EXECUTION_POLICY} ts=${ts}" | tee -a "$LOG_DIR/policy_branch_${EXECUTION_POLICY}.log"
  if .venv/bin/python scripts/ops/weather_policy_branch.py \
      --execution-policy "$EXECUTION_POLICY" \
      --source-policy "$SOURCE_POLICY" \
      >"$log" 2>&1; then
    echo "[$(date -Is)] policy branch ok: policy=${EXECUTION_POLICY} ts=${ts} log=$log" | tee -a "$LOG_DIR/policy_branch_${EXECUTION_POLICY}.log"
  else
    rc=$?
    echo "[$(date -Is)] policy branch failed rc=$rc: policy=${EXECUTION_POLICY} ts=${ts} log=$log" | tee -a "$LOG_DIR/policy_branch_${EXECUTION_POLICY}.log"
  fi
  sleep "$INTERVAL_SEC"
done
