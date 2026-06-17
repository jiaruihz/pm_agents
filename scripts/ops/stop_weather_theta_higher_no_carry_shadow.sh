#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

RUNTIME_DIR="runtime/weather_edge_v1/theta_higher_no_carry_shadow_v1"
PID_FILE="$RUNTIME_DIR/loop.pid"

if [[ ! -s "$PID_FILE" ]]; then
  echo "not_running"
  exit 0
fi

pid="$(cat "$PID_FILE")"
if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "stopped pid=$pid"
else
  echo "stale_pid pid=$pid"
fi
rm -f "$PID_FILE"
