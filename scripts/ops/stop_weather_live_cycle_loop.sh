#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_socket)"
STRATEGY_INSTANCE="${WEATHER_LIVE_STRATEGY_INSTANCE:-${WEATHER_STRATEGY_INSTANCE:-}}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/live_cycle"
if [[ -n "$STRATEGY_INSTANCE" ]]; then
  PID_FILES=("$LOG_DIR/live_${STRATEGY_INSTANCE}.pid")
else
  PID_FILES=("$LOG_DIR"/live_*.pid "$LOG_DIR/daemon.pid")
fi

found=0
for PID_FILE in "${PID_FILES[@]}"; do
  [[ -e "$PID_FILE" ]] || continue
  found=1

  if [[ ! -s "$PID_FILE" ]]; then
    echo "not running: empty pid file $PID_FILE"
    rm -f "$PID_FILE"
    continue
  fi

  pid="$(cat "$PID_FILE" || true)"
  if [[ "$pid" == tmux:* ]]; then
    session="${pid#tmux:}"
    if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$session" 2>/dev/null; then
      weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$session"
      echo "stopped tmux_socket=$TMUX_SOCKET session=$session file=$PID_FILE"
    else
      echo "not running: stale session=$session file=$PID_FILE"
    fi
    rm -f "$PID_FILE"
    continue
  elif [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    echo "not running: invalid pid file $PID_FILE"
    rm -f "$PID_FILE"
    continue
  fi

  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "stopped pid=$pid file=$PID_FILE"
  else
    echo "not running: stale pid=$pid file=$PID_FILE"
  fi
  rm -f "$PID_FILE"
done

if [[ "$found" -eq 0 ]]; then
  echo "not running: no pid file"
fi
