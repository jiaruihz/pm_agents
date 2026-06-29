#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_DIR="$PROJECT_DIR/runtime/weather_edge_v1/regime_routed_no_tiny_live_v1"
PID_FILE="$RUNTIME_DIR/loop.pid"
OUT_FILE="$RUNTIME_DIR/loop.out"
PY="$PROJECT_DIR/.venv/bin/python"

mkdir -p "$RUNTIME_DIR"
if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running pid=$old_pid log=$OUT_FILE"
    exit 0
  fi
fi

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

REGIME_ROUTED_NO_INTERVAL_SEC="${REGIME_ROUTED_NO_INTERVAL_SEC:-60}"
REGIME_ROUTED_NO_BASE_NOTIONAL="${REGIME_ROUTED_NO_BASE_NOTIONAL:-5}"
REGIME_ROUTED_NO_DAILY_GROSS_CAP="${REGIME_ROUTED_NO_DAILY_GROSS_CAP:-5}"
REGIME_ROUTED_NO_MIN_ORDER_SHARES="${REGIME_ROUTED_NO_MIN_ORDER_SHARES:-5}"
REGIME_ROUTED_NO_MIN_CURRENT_ESCAPE_MARGIN_NATIVE="${REGIME_ROUTED_NO_MIN_CURRENT_ESCAPE_MARGIN_NATIVE:-0}"
REGIME_ROUTED_NO_MAX_ORDERS="${REGIME_ROUTED_NO_MAX_ORDERS:-1}"
REGIME_ROUTED_NO_ORDER_TTL_MIN="${REGIME_ROUTED_NO_ORDER_TTL_MIN:-30}"
REGIME_ROUTED_NO_OBS_SOURCE="${REGIME_ROUTED_NO_OBS_SOURCE:-weather_data_feed_observation_cache}"
REGIME_ROUTED_NO_RECENT_HOURS="${REGIME_ROUTED_NO_RECENT_HOURS:-30}"
REGIME_ROUTED_NO_MAX_OBS_AGE_MIN="${REGIME_ROUTED_NO_MAX_OBS_AGE_MIN:-20}"
REGIME_ROUTED_NO_MAX_SNAPSHOT_AGE_MIN="${REGIME_ROUTED_NO_MAX_SNAPSHOT_AGE_MIN:-45}"
REGIME_ROUTED_NO_SNAPSHOT_DIR="${REGIME_ROUTED_NO_SNAPSHOT_DIR:-}"
DEFAULT_OBSERVATION_CACHE="$HOME/projects/weather_data_feed_service_runtime/output/observations/latest.json"
if [[ -z "${REGIME_ROUTED_NO_OBSERVATION_CACHE:-}" && -f "$DEFAULT_OBSERVATION_CACHE" ]]; then
  REGIME_ROUTED_NO_OBSERVATION_CACHE="$DEFAULT_OBSERVATION_CACHE"
else
  REGIME_ROUTED_NO_OBSERVATION_CACHE="${REGIME_ROUTED_NO_OBSERVATION_CACHE:-}"
fi
REGIME_ROUTED_NO_LIVE="${REGIME_ROUTED_NO_LIVE:-1}"
REGIME_ROUTED_NO_CONFIRM_LIVE="${REGIME_ROUTED_NO_CONFIRM_LIVE:-1}"

args=(
  "$PROJECT_DIR/scripts/ops/regime_routed_no_tiny_live.py"
  --base-notional "$REGIME_ROUTED_NO_BASE_NOTIONAL"
  --daily-gross-cap "$REGIME_ROUTED_NO_DAILY_GROSS_CAP"
  --min-order-shares "$REGIME_ROUTED_NO_MIN_ORDER_SHARES"
  --min-current-no-escape-margin-native "$REGIME_ROUTED_NO_MIN_CURRENT_ESCAPE_MARGIN_NATIVE"
  --max-orders "$REGIME_ROUTED_NO_MAX_ORDERS"
  --order-ttl-min "$REGIME_ROUTED_NO_ORDER_TTL_MIN"
  --obs-source "$REGIME_ROUTED_NO_OBS_SOURCE"
  --recent-hours "$REGIME_ROUTED_NO_RECENT_HOURS"
  --max-obs-age-min "$REGIME_ROUTED_NO_MAX_OBS_AGE_MIN"
  --max-snapshot-age-min "$REGIME_ROUTED_NO_MAX_SNAPSHOT_AGE_MIN"
)

if [[ -n "$REGIME_ROUTED_NO_SNAPSHOT_DIR" ]]; then
  args+=(--snapshot-dir "$REGIME_ROUTED_NO_SNAPSHOT_DIR")
fi
if [[ -n "$REGIME_ROUTED_NO_OBSERVATION_CACHE" ]]; then
  args+=(--observation-cache "$REGIME_ROUTED_NO_OBSERVATION_CACHE")
fi
if [[ "${REGIME_ROUTED_NO_INCLUDE_STATION_DIFF:-0}" == "1" ]]; then
  args+=(--include-station-diff)
fi
if [[ -n "${REGIME_ROUTED_NO_TARGET_DATE:-}" ]]; then
  args+=(--target-date "$REGIME_ROUTED_NO_TARGET_DATE")
fi
if [[ -n "${REGIME_ROUTED_NO_CITIES:-}" ]]; then
  # shellcheck disable=SC2206
  city_args=($REGIME_ROUTED_NO_CITIES)
  args+=(--cities "${city_args[@]}")
fi
if [[ "$REGIME_ROUTED_NO_LIVE" == "1" ]]; then
  args+=(--live)
fi
if [[ "$REGIME_ROUTED_NO_CONFIRM_LIVE" == "1" ]]; then
  args+=(--confirm-live)
fi

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

(
  cd "$PROJECT_DIR"
  while true; do
    date -u +"[regime_routed_no] cycle_start_utc=%Y-%m-%dT%H:%M:%SZ"
    set +e
    "$PY" -u "${args[@]}"
    rc=$?
    set -e
    if [[ "$rc" -ne 0 ]]; then
      date -u +"[regime_routed_no] runner_failed_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
    fi
    sleep "$REGIME_ROUTED_NO_INTERVAL_SEC"
  done
) >>"$OUT_FILE" 2>&1 < /dev/null &

pid=$!
echo "$pid" > "$PID_FILE"
echo "started regime-routed NO tiny-live pid=$pid log=$OUT_FILE base_N=$REGIME_ROUTED_NO_BASE_NOTIONAL daily_cap=$REGIME_ROUTED_NO_DAILY_GROSS_CAP current_escape_margin_gt=$REGIME_ROUTED_NO_MIN_CURRENT_ESCAPE_MARGIN_NATIVE"
