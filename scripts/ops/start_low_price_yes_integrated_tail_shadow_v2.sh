#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="${LOW_PRICE_YES_INTEGRATED_TAIL_TMUX_SESSION:-low_price_yes_integrated_tail_shadow_v2}"
RUNTIME_DIR="${LOW_PRICE_YES_INTEGRATED_TAIL_SHADOW_RUNTIME_DIR:-runtime/weather_edge_v1/low_price_yes_integrated_tail_shadow_v2}"
LOG_FILE="$PROJECT_DIR/$RUNTIME_DIR/shadow_tmux.log"
OBSERVATION_CACHE="${LOW_PRICE_YES_INTEGRATED_TAIL_OBSERVATION_CACHE:-$RUNTIME_ROOT/output/observations/latest.json}"
SNAPSHOT_DIR="${LOW_PRICE_YES_INTEGRATED_TAIL_SNAPSHOT_DIR:-$RUNTIME_ROOT/targeted_output/paper_snapshots}"
INTERVAL_SECONDS="${LOW_PRICE_YES_INTEGRATED_TAIL_INTERVAL_SECONDS:-300}"
BOOK_TIMEOUT_SECONDS="${LOW_PRICE_YES_INTEGRATED_TAIL_BOOK_TIMEOUT_SEC:-5}"
BOOK_PROXY="${LOW_PRICE_YES_INTEGRATED_TAIL_MARKET_PROXY:-http://127.0.0.1:7890}"

mkdir -p "$PROJECT_DIR/$RUNTIME_DIR"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; then
  echo "already running tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
  exit 0
fi

weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd $(printf '%q' "$PROJECT_DIR") && exec env LOW_PRICE_YES_INTEGRATED_TAIL_SHADOW_RUNTIME_DIR=$(printf '%q' "$RUNTIME_DIR") $(printf '%q' "$PROJECT_DIR/.venv/bin/python") -u scripts/ops/low_price_yes_integrated_tail_shadow_v2.py loop --observation-cache $(printf '%q' "$OBSERVATION_CACHE") --snapshot-dir $(printf '%q' "$SNAPSHOT_DIR") --interval-seconds $(printf '%q' "$INTERVAL_SECONDS") --book-timeout-sec $(printf '%q' "$BOOK_TIMEOUT_SECONDS") --book-proxy $(printf '%q' "$BOOK_PROXY") --min-ask 0.05 --max-ask 0.20 --min-edge 0.20 --max-candidates-per-run 80 >> $(printf '%q' "$LOG_FILE") 2>&1"

echo "started tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION log=$LOG_FILE"
