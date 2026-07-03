#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_DIR="${TMAX_DISTRIBUTION_EDGE_SHADOW_RUNTIME_DIR:-$PROJECT_DIR/runtime/weather_edge_v1/tmax_distribution_edge_shadow_v1}"
PID_FILE="$RUNTIME_DIR/shadow_loop.pid"
LOG_FILE="$RUNTIME_DIR/shadow_loop.log"
PY="$PROJECT_DIR/.venv/bin/python"

INTERVAL_SEC="${TMAX_DISTRIBUTION_EDGE_INTERVAL_SEC:-900}"
SOURCE="${TMAX_DISTRIBUTION_EDGE_SOURCE:-$PROJECT_DIR/docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv}"
REFRESH_SOURCE="${TMAX_DISTRIBUTION_EDGE_REFRESH_SOURCE:-0}"

mkdir -p "$RUNTIME_DIR"

if [[ "${TMAX_DISTRIBUTION_EDGE_SHADOW_CHILD:-0}" != "1" && -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running pid=$old_pid log=$LOG_FILE"
    exit 0
  fi
fi

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

if [[ "${TMAX_DISTRIBUTION_EDGE_SHADOW_CHILD:-0}" != "1" ]]; then
  nohup env TMAX_DISTRIBUTION_EDGE_SHADOW_CHILD=1 "$0" >>"$LOG_FILE" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$PID_FILE"
  echo "started tmax distribution edge shadow loop pid=$pid log=$LOG_FILE source=$SOURCE refresh_source=$REFRESH_SOURCE"
  exit 0
fi

echo "$$" > "$PID_FILE"
trap 'rc=$?; date -u +"[tmax_distribution_edge_shadow] loop_exit_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"; rm -f "$PID_FILE"' EXIT
date -u +"[tmax_distribution_edge_shadow] loop_start_utc=%Y-%m-%dT%H:%M:%SZ pid=$$ source=$SOURCE refresh_source=$REFRESH_SOURCE"

cd "$PROJECT_DIR"
while true; do
  date -u +"[tmax_distribution_edge_shadow] cycle_start_utc=%Y-%m-%dT%H:%M:%SZ"
  set +e
  refresh_args=()
  if [[ "$REFRESH_SOURCE" == "1" ]]; then
    refresh_args=(--refresh-source)
  fi
  "$PY" -u scripts/ops/tmax_distribution_edge_shadow_v1.py run \
    --runtime-dir "$RUNTIME_DIR" \
    --source "$SOURCE" \
    "${refresh_args[@]}"
  rc=$?
  set -e
  date -u +"[tmax_distribution_edge_shadow] cycle_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
  sleep "$INTERVAL_SEC"
done
