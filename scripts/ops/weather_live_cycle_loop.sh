#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/rui/projects/pm_agent}"
INTERVAL_SEC="${INTERVAL_SEC:-1800}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/live_cycle"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"

echo "weather_live_cycle_loop started at $(date -Is), interval=${INTERVAL_SEC}s"

while true; do
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  log="$LOG_DIR/loop_${ts}.log"
  echo "[$(date -Is)] run start: $ts" | tee -a "$LOG_DIR/loop.log"
  if .venv/bin/python scripts/ops/weather_live_cycle.py >"$log" 2>&1; then
    echo "[$(date -Is)] run ok: $ts log=$log" | tee -a "$LOG_DIR/loop.log"
  else
    rc=$?
    echo "[$(date -Is)] run failed rc=$rc: $ts log=$log" | tee -a "$LOG_DIR/loop.log"
  fi
  sleep "$INTERVAL_SEC"
done
