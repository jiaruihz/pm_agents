#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_socket)"
cd "$ROOT"

RUNTIME_DIR="${LOW_PRICE_YES_LOTTERY_RUNTIME_DIR:-runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1}"
PID_FILE="$RUNTIME_DIR/loop.pid"
KILL_ALL_LOW_PRICE_YES_LOOPS="${KILL_ALL_LOW_PRICE_YES_LOOPS:-0}"

kill_matching_loops() {
  local pids
  pids="$(pgrep -f 'scripts/ops/low_price_yes_lottery_tiny_live.py loop' || true)"
  if [[ -z "$pids" ]]; then
    echo "no_matching_low_price_yes_loops"
    return 0
  fi
  while read -r candidate; do
    [[ -z "$candidate" ]] && continue
    if [[ "$candidate" == "$$" ]]; then
      continue
    fi
    if kill -0 "$candidate" 2>/dev/null; then
      kill "$candidate" || true
      echo "stopped_matching pid=$candidate"
    fi
  done <<<"$pids"
}

if [[ ! -s "$PID_FILE" ]]; then
  if [[ "$KILL_ALL_LOW_PRICE_YES_LOOPS" == "1" ]]; then
    kill_matching_loops
  fi
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

if [[ "$KILL_ALL_LOW_PRICE_YES_LOOPS" == "1" ]]; then
  kill_matching_loops
fi
