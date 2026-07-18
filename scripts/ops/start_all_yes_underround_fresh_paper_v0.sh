#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
TMUX_SESSION="${ALL_YES_UNDERROUND_FRESH_TMUX_SESSION:-all_yes_underround_fresh_paper_v0}"
RUN_DIR="${RUN_DIR:-$PROJECT_DIR/runtime/weather_edge_v1/all_yes_underround_paper_v0}"
PID_FILE="$RUN_DIR/fresh_loop.pid"
OUT_FILE="$RUN_DIR/fresh_loop.out"

mkdir -p "$RUN_DIR"

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
  "cd $(printf '%q' "$PROJECT_DIR") && exec bash scripts/ops/all_yes_underround_fresh_paper_loop_v0.sh > $(printf '%q' "$OUT_FILE") 2>&1"
echo "tmux:$TMUX_SESSION" > "$PID_FILE"
echo "started all-YES underround fresh paper loop tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION log=$OUT_FILE"
