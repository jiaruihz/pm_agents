#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/live_cycle"
PID_FILE="$LOG_DIR/telegram_control.pid"
OUT_FILE="$LOG_DIR/telegram_control.out"
mkdir -p "$LOG_DIR"

if [[ -s "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running pid=$old_pid"
    exit 0
  fi
fi

cd "$PROJECT_DIR"
setsid .venv/bin/python scripts/ops/weather_telegram_control.py </dev/null >"$OUT_FILE" 2>&1 &
pid="$!"
echo "$pid" >"$PID_FILE"
echo "started weather telegram control pid=$pid"
