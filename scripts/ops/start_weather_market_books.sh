#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:-$PROJECT_DIR}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$(weather_production_path "$PROJECT_DIR" data_feed_runtime_root)}"
MARKET_BOOKS_ROOT="${WEATHER_MARKET_BOOKS_ROOT:-$(weather_production_path "$PROJECT_DIR" market_books_root)}"
MARKET_LADDER_ROOT="${WEATHER_MARKET_LADDER_ROOT:-$(weather_production_path "$PROJECT_DIR" market_ladder_snapshot_root)}"
CACHE_ROOT="${WEATHER_DATA_FEED_CACHE_ROOT:-$RUNTIME_ROOT/cache}"
OBSERVATION_CACHE="${WEATHER_OBSERVATION_CACHE:-$(weather_production_path "$PROJECT_DIR" observation_cache_path)}"
INTERVAL_SEC="${WEATHER_MARKET_BOOKS_INTERVAL_SEC:-300}"
ORDERBOOK_BUDGET_SEC="${WEATHER_MARKET_BOOKS_BUDGET_SEC:-240}"
SOURCE_MAX_AGE_SEC="${WEATHER_MARKET_BOOKS_SOURCE_MAX_AGE_SEC:-420}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="${WEATHER_MARKET_BOOKS_TMUX_SESSION:-weather_market_books}"
LOG_FILE="${WEATHER_MARKET_BOOKS_LOG_FILE:-$RUNTIME_ROOT/market_books/collector.log}"

if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; then
  echo "already running: tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
  exit 0
fi

weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "export WEATHER_DATA_FEED_SERVICE_DIR='$SERVICE_DIR' \
     WEATHER_DATA_FEED_RUNTIME_ROOT='$RUNTIME_ROOT' \
     WEATHER_MARKET_BOOKS_ROOT='$MARKET_BOOKS_ROOT' \
     WEATHER_MARKET_LADDER_ROOT='$MARKET_LADDER_ROOT' \
     WEATHER_DATA_FEED_CACHE_ROOT='$CACHE_ROOT' \
     WEATHER_OBSERVATION_CACHE='$OBSERVATION_CACHE' \
     WEATHER_MARKET_BOOKS_INTERVAL_SEC='$INTERVAL_SEC' \
     WEATHER_MARKET_BOOKS_BUDGET_SEC='$ORDERBOOK_BUDGET_SEC' \
     WEATHER_MARKET_BOOKS_SOURCE_MAX_AGE_SEC='$SOURCE_MAX_AGE_SEC' \
     WEATHER_MARKET_BOOKS_LOG_FILE='$LOG_FILE' \
   && exec bash '$PROJECT_DIR/scripts/ops/_weather_market_books_loop_body.sh'"

sleep 1
weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null
echo "started tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION log=$LOG_FILE"
