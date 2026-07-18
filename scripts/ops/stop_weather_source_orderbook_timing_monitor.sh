#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_socket)"
PID_FILE="$PROJECT_DIR/runtime/weather_edge_v1/source_orderbook_timing/loop.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "not running: no pid file"
  exit 0
fi

pid="$(cat "$PID_FILE" || true)"
if [[ -z "$pid" ]]; then
  rm -f "$PID_FILE"
  echo "not running: empty pid file removed"
  exit 0
fi

if [[ "$pid" == tmux:* ]]; then
  session="${pid#tmux:}"
  if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$session" 2>/dev/null; then
    weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$session"
    echo "stopped timing monitor tmux=$session"
  else
    echo "not running: stale tmux session=$session"
  fi
  rm -f "$PID_FILE"
  exit 0
fi

if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "stopped timing monitor pid=$pid"
else
  echo "not running: stale pid=$pid"
fi
rm -f "$PID_FILE"
