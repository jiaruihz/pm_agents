#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
INTERVAL_SEC="${INTERVAL_SEC:-1800}"
INITIAL_DELAY_SEC="${INITIAL_DELAY_SEC:-0}"
STRATEGY_INSTANCE="${WEATHER_LIVE_STRATEGY_INSTANCE:-${WEATHER_STRATEGY_INSTANCE:-default}}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/live_cycle"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"

echo "weather_live_cycle_loop started at $(date -Is), instance=${STRATEGY_INSTANCE}, initial_delay=${INITIAL_DELAY_SEC}s, interval=${INTERVAL_SEC}s"
sleep "$INITIAL_DELAY_SEC"

while true; do
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  log="$LOG_DIR/loop_${STRATEGY_INSTANCE}_${ts}.log"
  echo "[$(date -Is)] run start: instance=${STRATEGY_INSTANCE} ts=${ts}" | tee -a "$LOG_DIR/loop_${STRATEGY_INSTANCE}.log"
  if .venv/bin/python scripts/ops/weather_live_cycle.py >"$log" 2>&1; then
    echo "[$(date -Is)] run ok: instance=${STRATEGY_INSTANCE} ts=${ts} log=$log" | tee -a "$LOG_DIR/loop_${STRATEGY_INSTANCE}.log"
  else
    rc=$?
    echo "[$(date -Is)] run failed rc=$rc: instance=${STRATEGY_INSTANCE} ts=${ts} log=$log" | tee -a "$LOG_DIR/loop_${STRATEGY_INSTANCE}.log"
  fi
  sleep "$INTERVAL_SEC"
done
