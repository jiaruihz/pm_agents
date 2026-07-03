#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_DIR="${WEATHER_RUNTIME_MONITOR_DIR:-$PROJECT_DIR/runtime/weather_edge_v1/runtime_monitor}"
PID_FILE="$RUNTIME_DIR/loop.pid"
OUT_FILE="$RUNTIME_DIR/loop.out"
PY="$PROJECT_DIR/.venv/bin/python"

mkdir -p "$RUNTIME_DIR"

if [[ "${WEATHER_RUNTIME_MONITOR_CHILD:-0}" != "1" && -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running pid=$old_pid log=$OUT_FILE"
    exit 0
  fi
fi

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

INTERVAL_SECONDS="${WEATHER_RUNTIME_MONITOR_INTERVAL_SECONDS:-300}"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

if [[ "${WEATHER_RUNTIME_MONITOR_CHILD:-0}" != "1" ]]; then
  nohup env WEATHER_RUNTIME_MONITOR_CHILD=1 "$PROJECT_DIR/scripts/ops/start_weather_runtime_monitor.sh" >>"$OUT_FILE" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$PID_FILE"
  echo "started weather runtime monitor pid=$pid log=$OUT_FILE interval=${INTERVAL_SECONDS}s"
  exit 0
fi

echo "$$" > "$PID_FILE"
cd "$PROJECT_DIR"
trap 'rc=$?; date -u +"[weather_runtime_monitor] loop_exit_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"; rm -f "$PID_FILE"' EXIT
while true; do
  date -u +"[weather_runtime_monitor] cycle_start_utc=%Y-%m-%dT%H:%M:%SZ"
  tmp_out="$RUNTIME_DIR/last_run.out.tmp"
  last_out="$RUNTIME_DIR/last_run.out"
  set +e
  "$PY" -u scripts/ops/weather_runtime_monitor.py --runtime-dir "$RUNTIME_DIR" >"$tmp_out" 2>>"$OUT_FILE"
  rc=$?
  set -e
  if [[ "$rc" -eq 0 ]]; then
    mv "$tmp_out" "$last_out"
  else
    date -u +"[weather_runtime_monitor] runner_failed_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
    rm -f "$tmp_out"
  fi
  date -u +"[weather_runtime_monitor] cycle_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
  sleep "$INTERVAL_SECONDS"
done
