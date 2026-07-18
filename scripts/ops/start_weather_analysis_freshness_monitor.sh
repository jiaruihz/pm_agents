#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_DIR="${WEATHER_ANALYSIS_FRESHNESS_DIR:-$PROJECT_DIR/runtime/weather_edge_v1/analysis_freshness_monitor}"
SESSION="${WEATHER_ANALYSIS_FRESHNESS_SESSION:-weather_analysis_freshness_monitor}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "${WEATHER_TMUX_SOCKET:-}")"
INTERVAL_SECONDS="${WEATHER_ANALYSIS_FRESHNESS_INTERVAL_SECONDS:-900}"
PY="$PROJECT_DIR/.venv/bin/python"

mkdir -p "$RUNTIME_DIR"
[[ -x "$PY" ]] || PY="python3"

if tmux -L "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "already running session=$SESSION socket=$TMUX_SOCKET"
  exit 0
fi

tmux -L "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$PROJECT_DIR' && exec '$PY' -u scripts/ops/weather_analysis_freshness_monitor.py --loop --interval-seconds '$INTERVAL_SECONDS' >>'$RUNTIME_DIR/loop.log' 2>&1"
echo "started session=$SESSION socket=$TMUX_SOCKET interval=${INTERVAL_SECONDS}s"
