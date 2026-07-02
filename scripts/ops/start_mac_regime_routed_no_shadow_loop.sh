#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_DIR="${REGIME_ROUTED_NO_SHADOW_RUNTIME_DIR:-$PROJECT_DIR/runtime/weather_edge_v1/regime_routed_no_shadow_v1}"
PID_FILE="$RUNTIME_DIR/shadow_loop.pid"
LOG_FILE="$RUNTIME_DIR/shadow_loop.log"
PY="$PROJECT_DIR/.venv/bin/python"

INTERVAL_SEC="${REGIME_ROUTED_NO_INTERVAL_SEC:-60}"
SNAPSHOT_DIR="${REGIME_ROUTED_NO_SNAPSHOT_DIR:-$HOME/projects/weather_data_feed_service_runtime/targeted_output/paper_snapshots}"
OBSERVATION_CACHE="${REGIME_ROUTED_NO_OBSERVATION_CACHE:-$HOME/projects/weather_data_feed_service_runtime/output/observations/latest.json}"
BASE_NOTIONAL="${REGIME_ROUTED_NO_BASE_NOTIONAL:-9}"
DAILY_GROSS_CAP="${REGIME_ROUTED_NO_DAILY_GROSS_CAP:-9}"
MAX_ORDERS="${REGIME_ROUTED_NO_MAX_ORDERS:-1}"
MIN_ORDER_SHARES="${REGIME_ROUTED_NO_MIN_ORDER_SHARES:-5}"
MAX_SNAPSHOT_AGE_MIN="${REGIME_ROUTED_NO_MAX_SNAPSHOT_AGE_MIN:-120}"
MAX_OBS_AGE_MIN="${REGIME_ROUTED_NO_MAX_OBS_AGE_MIN:-20}"

mkdir -p "$RUNTIME_DIR"

if [[ "${MAC_REGIME_ROUTED_NO_SHADOW_CHILD:-0}" != "1" && -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running pid=$old_pid log=$LOG_FILE"
    exit 0
  fi
fi

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

if [[ "${MAC_REGIME_ROUTED_NO_SHADOW_CHILD:-0}" != "1" ]]; then
  nohup env MAC_REGIME_ROUTED_NO_SHADOW_CHILD=1 "$0" >>"$LOG_FILE" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$PID_FILE"
  echo "started regime-routed NO shadow loop pid=$pid log=$LOG_FILE snapshot_dir=$SNAPSHOT_DIR"
  exit 0
fi

echo "$$" > "$PID_FILE"
trap 'rc=$?; date -u +"[regime_routed_no_shadow] loop_exit_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"; rm -f "$PID_FILE"' EXIT
date -u +"[regime_routed_no_shadow] loop_start_utc=%Y-%m-%dT%H:%M:%SZ pid=$$ snapshot_dir=$SNAPSHOT_DIR"

cd "$PROJECT_DIR"
while true; do
  date -u +"[regime_routed_no_shadow] cycle_start_utc=%Y-%m-%dT%H:%M:%SZ"
  set +e
  REGIME_ROUTED_NO_RUNTIME_DIR="$RUNTIME_DIR" "$PY" -u scripts/ops/regime_routed_no_tiny_live.py \
    --snapshot-dir "$SNAPSHOT_DIR" \
    --observation-cache "$OBSERVATION_CACHE" \
    --base-notional "$BASE_NOTIONAL" \
    --daily-gross-cap "$DAILY_GROSS_CAP" \
    --max-orders "$MAX_ORDERS" \
    --min-order-shares "$MIN_ORDER_SHARES" \
    --max-snapshot-age-min "$MAX_SNAPSHOT_AGE_MIN" \
    --max-obs-age-min "$MAX_OBS_AGE_MIN"
  rc=$?
  set -e
  date -u +"[regime_routed_no_shadow] cycle_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
  sleep "$INTERVAL_SEC"
done
