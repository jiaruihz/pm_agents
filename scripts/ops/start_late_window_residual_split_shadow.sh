#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
SESSION="${LATE_WINDOW_RESIDUAL_SPLIT_SESSION:-late_window_residual_split_shadow}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "${WEATHER_TMUX_SOCKET:-}")"
RUNTIME_DIR="${LATE_WINDOW_RESIDUAL_SPLIT_RUNTIME_DIR:-$ROOT/runtime/weather_edge_v1/late_window_residual_split_v1}"
LOG_DIR="$RUNTIME_DIR/logs"
INTERVAL_SECONDS="${LATE_WINDOW_RESIDUAL_SPLIT_INTERVAL_SECONDS:-900}"

mkdir -p "$LOG_DIR"

if tmux -L "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "already running: tmux -L $TMUX_SOCKET attach -t $SESSION"
  exit 0
fi

tmux -L "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && exec env PYTHONUNBUFFERED=1 .venv/bin/python scripts/ops/late_window_residual_split_runner_v1.py loop --interval-seconds '$INTERVAL_SECONDS' >> '$LOG_DIR/loop.log' 2>&1"

echo "started: tmux -L $TMUX_SOCKET attach -t $SESSION"
echo "summary: $RUNTIME_DIR/latest_summary.json"
echo "log: $LOG_DIR/loop.log"
