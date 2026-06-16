#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

RUNTIME_DIR="runtime/weather_edge_v1/theta_current_yes_tiny_live_v1"
PID_FILE="$RUNTIME_DIR/loop.pid"
LOG_FILE="$RUNTIME_DIR/loop.log"
mkdir -p "$RUNTIME_DIR" "runtime/weather_edge_v1/live"

if [[ -s "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE")"
  if kill -0 "$old_pid" 2>/dev/null; then
    echo "already_running pid=$old_pid"
    exit 0
  fi
fi

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
MAX_ORDER_NOTIONAL="${MAX_ORDER_NOTIONAL:-5}"
MAX_CITY_DAY_NOTIONAL="${MAX_CITY_DAY_NOTIONAL:-10}"
MIN_AVAILABLE_NOTIONAL="${MIN_AVAILABLE_NOTIONAL:-5}"
MAX_ORDERS="${MAX_ORDERS:-20}"
MAX_SNAPSHOT_AGE_MIN="${MAX_SNAPSHOT_AGE_MIN:-45}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-900}"

nohup "$PYTHON_BIN" scripts/ops/weather_theta_current_yes_tiny_live.py loop \
  --live \
  --confirm-live \
  --max-order-notional "$MAX_ORDER_NOTIONAL" \
  --max-city-day-notional "$MAX_CITY_DAY_NOTIONAL" \
  --min-available-notional "$MIN_AVAILABLE_NOTIONAL" \
  --max-orders "$MAX_ORDERS" \
  --max-snapshot-age-min "$MAX_SNAPSHOT_AGE_MIN" \
  --interval-seconds "$INTERVAL_SECONDS" \
  >>"$LOG_FILE" 2>&1 &

pid="$!"
echo "$pid" >"$PID_FILE"
echo "started pid=$pid log=$LOG_FILE"
