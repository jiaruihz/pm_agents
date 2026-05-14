#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/rui/projects/pm_agent}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/live_cycle"
PID_FILE="$LOG_DIR/daemon.pid"
OUT_FILE="$LOG_DIR/daemon.out"
mkdir -p "$LOG_DIR"

if [[ -s "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running pid=$old_pid"
    exit 0
  fi
fi

cd "$PROJECT_DIR"
nohup scripts/ops/weather_live_cycle_loop.sh >"$OUT_FILE" 2>&1 &
pid="$!"
echo "$pid" >"$PID_FILE"
echo "started pid=$pid"
