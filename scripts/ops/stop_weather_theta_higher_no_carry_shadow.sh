#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_socket)"
cd "$ROOT"

RUNTIME_DIR="runtime/weather_edge_v1/theta_higher_no_carry_shadow_v1"
PID_FILE="$RUNTIME_DIR/loop.pid"

if [[ ! -s "$PID_FILE" ]]; then
  echo "not_running"
  exit 0
fi

pid="$(cat "$PID_FILE")"
if [[ "$pid" == tmux:* ]]; then
  session="${pid#tmux:}"
  weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$session" 2>/dev/null || true
  echo "stopped tmux_socket=$TMUX_SOCKET session=$session"
elif [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "stopped pid=$pid"
else
  echo "stale_pid pid=$pid"
fi
rm -f "$PID_FILE"
