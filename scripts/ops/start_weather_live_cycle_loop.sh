#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
STRATEGY_INSTANCE="${WEATHER_LIVE_STRATEGY_INSTANCE:-${WEATHER_STRATEGY_INSTANCE:-default}}"
TMUX_SESSION="${WEATHER_LIVE_CYCLE_TMUX_SESSION:-weather_live_cycle_${STRATEGY_INSTANCE}}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/live_cycle"
PID_FILE="$LOG_DIR/live_${STRATEGY_INSTANCE}.pid"
OUT_FILE="$LOG_DIR/live_${STRATEGY_INSTANCE}.out"
mkdir -p "$LOG_DIR"

if [[ -s "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ "$old_pid" == "tmux:$TMUX_SESSION" ]] && weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "already running tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
    exit 0
  elif [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running pid=$old_pid"
    exit 0
  fi
fi

weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd $(printf '%q' "$PROJECT_DIR") && exec env WEATHER_LIVE_STRATEGY_INSTANCE=$(printf '%q' "$STRATEGY_INSTANCE") scripts/ops/weather_live_cycle_loop.sh > $(printf '%q' "$OUT_FILE") 2>&1"
echo "tmux:$TMUX_SESSION" >"$PID_FILE"
echo "started instance=$STRATEGY_INSTANCE tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
