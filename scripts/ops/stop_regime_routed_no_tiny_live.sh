#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_DIR="$PROJECT_DIR/runtime/weather_edge_v1/regime_routed_no_tiny_live_v1"
PID_FILE="$RUNTIME_DIR/loop.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "not running: missing $PID_FILE"
  exit 0
fi

pid="$(cat "$PID_FILE" || true)"
if [[ -z "$pid" ]]; then
  rm -f "$PID_FILE"
  echo "not running: empty pid file removed"
  exit 0
fi

if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "stopped regime-routed NO tiny-live pid=$pid"
else
  echo "not running: stale pid=$pid"
fi
rm -f "$PID_FILE"
