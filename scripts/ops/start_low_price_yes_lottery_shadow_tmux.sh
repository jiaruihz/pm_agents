#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
SESSION="${LOW_PRICE_YES_LOTTERY_SHADOW_TMUX_SESSION:-low_price_yes_lottery_shadow_v1}"
RUNTIME="$PROJECT_DIR/runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1"
LOG_FILE="$RUNTIME/low_price_shadow_tmux.log"

mkdir -p "$RUNTIME"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "already running tmux_socket=$TMUX_SOCKET session=$SESSION"
  exit 0
fi

weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$PROJECT_DIR' && exec env LOW_PRICE_YES_LOTTERY_LOOP_CHILD=1 '$PROJECT_DIR/scripts/ops/start_low_price_yes_lottery_tiny_live.sh' --shadow >> '$LOG_FILE' 2>&1"

echo "started tmux_socket=$TMUX_SOCKET session=$SESSION log=$LOG_FILE"
