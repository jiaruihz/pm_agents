#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
PID_FILE="$PROJECT_DIR/runtime/weather_edge_v1/live_cycle/telegram_control.pid"

if [[ ! -s "$PID_FILE" ]]; then
  echo "not running: no pid file"
  exit 0
fi

pid="$(cat "$PID_FILE" || true)"
if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
  echo "not running: invalid pid file"
  rm -f "$PID_FILE"
  exit 0
fi

if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "stopped weather telegram control pid=$pid"
else
  echo "not running: stale pid=$pid"
fi
rm -f "$PID_FILE"
