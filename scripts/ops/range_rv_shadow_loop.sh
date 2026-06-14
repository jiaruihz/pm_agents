#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
INTERVAL_SEC="${INTERVAL_SEC:-1800}"
INITIAL_DELAY_SEC="${INITIAL_DELAY_SEC:-60}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/live_cycle"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"

echo "range_rv_shadow_loop started at $(date -Is), initial_delay=${INITIAL_DELAY_SEC}s, interval=${INTERVAL_SEC}s"
sleep "$INITIAL_DELAY_SEC"

while true; do
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  log="$LOG_DIR/range_rv_shadow_v0_${ts}.log"
  echo "[$(date -Is)] range RV shadow start: ts=${ts}" | tee -a "$LOG_DIR/range_rv_shadow_v0.log"
  if .venv/bin/python scripts/ops/range_rv_shadow_v0.py >"$log" 2>&1; then
    echo "[$(date -Is)] range RV shadow ok: ts=${ts} log=$log" | tee -a "$LOG_DIR/range_rv_shadow_v0.log"
  else
    rc=$?
    echo "[$(date -Is)] range RV shadow failed rc=$rc: ts=${ts} log=$log" | tee -a "$LOG_DIR/range_rv_shadow_v0.log"
  fi
  sleep "$INTERVAL_SEC"
done
