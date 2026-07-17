#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SESSION="${D1_YES_HIGH_MID_LIVE_SESSION:-d1_yes_high_mid_live_v1}"
TMUX_SOCKET="${WEATHER_DATA_FEED_TMUX_SOCKET:-weather-jrs}"
RUNTIME_DIR="${D1_YES_HIGH_MID_LIVE_RUNTIME_DIR:-$ROOT/runtime/weather_edge_v1/d1_yes_high_mid_live_v1}"
INTERVAL_SECONDS="${D1_YES_HIGH_MID_LIVE_INTERVAL_SECONDS:-60}"
SHARES="${D1_YES_HIGH_MID_LIVE_SHARES:-5}"
MAKER_SHARES="${D1_YES_HIGH_MID_LIVE_MAKER_SHARES:-5}"
MAX_CITY_DAYS="${D1_YES_HIGH_MID_LIVE_MAX_CITY_DAYS_PER_DAY:-10}"
MAX_DAILY_COST_USD="${D1_YES_HIGH_MID_LIVE_MAX_DAILY_COST_USD:-100}"
ORDER_TTL_MIN="${D1_YES_HIGH_MID_LIVE_ORDER_TTL_MIN:-45}"
OBSERVATION_CACHE="${D1_YES_HIGH_MID_OBSERVATION_CACHE:-/Volumes/jrs/weather_data_feed_service_runtime/output/observations/latest.json}"
FULL_LADDER_DIR="${D1_YES_HIGH_MID_FULL_LADDER_DIR:-/Volumes/jrs/weather_data_feed_service_runtime/full_ladder_output/orderbook_snapshots}"
TARGETED_DIR="${D1_YES_HIGH_MID_TARGETED_DIR:-/Volumes/jrs/weather_data_feed_service_runtime/targeted_output/orderbook_snapshots}"

mkdir -p "$RUNTIME_DIR"

if tmux -L "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "already running: tmux -L $TMUX_SOCKET attach -t $SESSION"
  exit 0
fi

tmux -L "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && exec env PYTHONUNBUFFERED=1 .venv/bin/python -u scripts/ops/d1_yes_high_mid_shadow_v1.py loop --strategy-instance d1_yes_high_mid_live_v1 --runtime-dir '$RUNTIME_DIR' --observation-cache '$OBSERVATION_CACHE' --orderbook-dir '$FULL_LADDER_DIR' --orderbook-dir '$TARGETED_DIR' --interval-seconds '$INTERVAL_SECONDS' --shares '$SHARES' --maker-shares '$MAKER_SHARES' --max-city-days-per-day '$MAX_CITY_DAYS' --max-daily-cost-usd '$MAX_DAILY_COST_USD' --order-ttl-min '$ORDER_TTL_MIN' --live --confirm-live >> '$RUNTIME_DIR/live_loop.log' 2>&1"

echo "started: tmux -L $TMUX_SOCKET attach -t $SESSION"
echo "summary: $RUNTIME_DIR/latest_summary.json"
echo "orders: $RUNTIME_DIR/live_orders.jsonl"
