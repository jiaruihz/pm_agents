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
THETA_CURRENT_YES_MODE="${THETA_CURRENT_YES_MODE:-live}"
MAX_ORDER_NOTIONAL="${MAX_ORDER_NOTIONAL:-5}"
MAX_CITY_DAY_NOTIONAL="${MAX_CITY_DAY_NOTIONAL:-10}"
MIN_AVAILABLE_NOTIONAL="${MIN_AVAILABLE_NOTIONAL:-5}"
MAX_TAKER_CUSHION="${MAX_TAKER_CUSHION:-0.02}"
CROSS_TICK_BUFFER="${CROSS_TICK_BUFFER:-0.001}"
MAX_ORDERS="${MAX_ORDERS:-20}"
MAX_SNAPSHOT_AGE_MIN="${MAX_SNAPSHOT_AGE_MIN:-45}"
MAX_OBS_AGE_MIN="${MAX_OBS_AGE_MIN:-20}"
PRE_METAR_UPDATE_BLACKOUT_MIN="${PRE_METAR_UPDATE_BLACKOUT_MIN:-6}"
MIN_GAP_TO_NEXT_BRACKET_C="${MIN_GAP_TO_NEXT_BRACKET_C:-0}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-900}"
NO_TELEGRAM="${NO_TELEGRAM:-0}"

if [[ "$THETA_CURRENT_YES_MODE" != "live" && "$THETA_CURRENT_YES_MODE" != "telemetry" ]]; then
  echo "invalid THETA_CURRENT_YES_MODE=$THETA_CURRENT_YES_MODE (expected live or telemetry)" >&2
  exit 2
fi

args=(
  scripts/ops/weather_theta_current_yes_tiny_live.py
  loop
  --max-order-notional "$MAX_ORDER_NOTIONAL"
  --max-city-day-notional "$MAX_CITY_DAY_NOTIONAL"
  --min-available-notional "$MIN_AVAILABLE_NOTIONAL"
  --max-taker-cushion "$MAX_TAKER_CUSHION"
  --cross-tick-buffer "$CROSS_TICK_BUFFER"
  --max-orders "$MAX_ORDERS"
  --max-snapshot-age-min "$MAX_SNAPSHOT_AGE_MIN"
  --max-obs-age-min "$MAX_OBS_AGE_MIN"
  --pre-metar-update-blackout-min "$PRE_METAR_UPDATE_BLACKOUT_MIN"
  --min-gap-to-next-bracket-c "$MIN_GAP_TO_NEXT_BRACKET_C"
  --interval-seconds "$INTERVAL_SECONDS"
)

if [[ "$THETA_CURRENT_YES_MODE" == "live" ]]; then
  args+=(--live --confirm-live)
fi

if [[ "$NO_TELEGRAM" == "1" ]]; then
  args+=(--no-telegram)
fi

nohup "$PYTHON_BIN" "${args[@]}" >>"$LOG_FILE" 2>&1 &

pid="$!"
echo "$pid" >"$PID_FILE"
echo "started mode=$THETA_CURRENT_YES_MODE pid=$pid log=$LOG_FILE"
