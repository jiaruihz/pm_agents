#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SESSION="${LATE_WINDOW_RESIDUAL_VALUE_D1_LIVE_SESSION:-late_window_residual_value_d1_no_live}"
TMUX_SOCKET="${WEATHER_TMUX_SOCKET:-weather-jrs}"
RUNTIME_DIR="${LATE_WINDOW_RESIDUAL_SPLIT_RUNTIME_DIR:-$ROOT/runtime/weather_edge_v1/late_window_residual_split_v1}"
LOG_DIR="$RUNTIME_DIR/logs"
INTERVAL_SECONDS="${LATE_WINDOW_RESIDUAL_VALUE_D1_INTERVAL_SECONDS:-60}"
SHARES="${LATE_WINDOW_RESIDUAL_VALUE_D1_SHARES:-5}"
MAX_ORDERS="${LATE_WINDOW_RESIDUAL_VALUE_D1_MAX_ORDERS:-1}"
MAX_DAILY_COST_USD="${LATE_WINDOW_RESIDUAL_VALUE_D1_MAX_DAILY_COST_USD:-25}"
ORDER_TTL_MIN="${LATE_WINDOW_RESIDUAL_VALUE_D1_ORDER_TTL_MIN:-45}"

mkdir -p "$LOG_DIR"

if tmux -L "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "already running: tmux -L $TMUX_SOCKET attach -t $SESSION"
  exit 0
fi

tmux -L "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && exec env PYTHONUNBUFFERED=1 .venv/bin/python scripts/ops/late_window_residual_split_runner_v1.py loop --interval-seconds '$INTERVAL_SECONDS' --shares '$SHARES' --max-orders '$MAX_ORDERS' --max-daily-cost-usd '$MAX_DAILY_COST_USD' --order-ttl-min '$ORDER_TTL_MIN' --live --confirm-live >> '$LOG_DIR/value_d1_live_loop.log' 2>&1"

echo "started: tmux -L $TMUX_SOCKET attach -t $SESSION"
echo "summary: $RUNTIME_DIR/latest_summary.json"
echo "log: $LOG_DIR/value_d1_live_loop.log"
