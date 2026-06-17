#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

RUNTIME_DIR="runtime/weather_edge_v1/theta_higher_no_carry_shadow_v1"
PID_FILE="$RUNTIME_DIR/loop.pid"
LOG_FILE="$RUNTIME_DIR/loop.log"
mkdir -p "$RUNTIME_DIR"

if [[ -s "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE")"
  if kill -0 "$old_pid" 2>/dev/null; then
    echo "already_running pid=$old_pid"
    exit 0
  fi
fi

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
MIN_DECLINE_C="${MIN_DECLINE_C:-0.5}"
MIN_NO_ASK="${MIN_NO_ASK:-0.75}"
MAX_NO_ASK="${MAX_NO_ASK:-0.97}"
MIN_AVAILABLE_NOTIONAL="${MIN_AVAILABLE_NOTIONAL:-5}"
MIN_GAP_TO_NEXT_BRACKET_C="${MIN_GAP_TO_NEXT_BRACKET_C:-0}"
MAX_SNAPSHOT_AGE_MIN="${MAX_SNAPSHOT_AGE_MIN:-45}"
MAX_OBS_AGE_MIN="${MAX_OBS_AGE_MIN:-20}"
PRE_METAR_UPDATE_BLACKOUT_MIN="${PRE_METAR_UPDATE_BLACKOUT_MIN:-6}"
SHADOW_NOTIONAL="${SHADOW_NOTIONAL:-5}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-900}"

args=(
  scripts/ops/weather_theta_higher_no_carry_shadow.py
  loop
  --min-decline-c "$MIN_DECLINE_C"
  --min-no-ask "$MIN_NO_ASK"
  --max-no-ask "$MAX_NO_ASK"
  --min-available-notional "$MIN_AVAILABLE_NOTIONAL"
  --min-gap-to-next-bracket-c "$MIN_GAP_TO_NEXT_BRACKET_C"
  --max-snapshot-age-min "$MAX_SNAPSHOT_AGE_MIN"
  --max-obs-age-min "$MAX_OBS_AGE_MIN"
  --pre-metar-update-blackout-min "$PRE_METAR_UPDATE_BLACKOUT_MIN"
  --shadow-notional "$SHADOW_NOTIONAL"
  --interval-seconds "$INTERVAL_SECONDS"
)
if [[ -n "${MAX_CURRENT_YES_ASK:-}" ]]; then
  args+=(--max-current-yes-ask "$MAX_CURRENT_YES_ASK")
fi

nohup "$PYTHON_BIN" "${args[@]}" >>"$LOG_FILE" 2>&1 &
pid="$!"
echo "$pid" >"$PID_FILE"
echo "started pid=$pid log=$LOG_FILE"
