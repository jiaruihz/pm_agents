#!/usr/bin/env bash
set -euo pipefail

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$HOME/projects/weather_data_feed_service_runtime}"
LOOP_DIR="${WEATHER_DATA_FEED_LOOP_DIR:-$RUNTIME_ROOT/loop}"
PID_FILE="$LOOP_DIR/data_feed_loop.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "not running: missing $PID_FILE"
  exit 0
fi

pid="$(cat "$PID_FILE" || true)"
if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "not running"
  exit 0
fi

kill "$pid"
rm -f "$PID_FILE"
echo "stopped mac weather data-feed loop pid=$pid"
