#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
source "$ROOT/scripts/ops/weather_market_proxy_env.sh"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
PM_RUNTIME_ROOT="${WEATHER_PM_RUNTIME_ROOT:-/Volumes/jrs/pm_agents/runtime}"
OUTPUT_DIR="${CURRENT_YES_CORE_CARRY_TINY_LIVE_V2_OUTPUT_DIR:-$PM_RUNTIME_ROOT/weather_edge_v1/current_yes_core_carry_tiny_live_v2}"
MARKET_WS_HEALTH="${WEATHER_MARKET_BOOKS_WS_HEALTH:-/Volumes/jrs/weather_data_feed_service_runtime/market_books/collector_health.json}"
LIVE_ORDERS="$OUTPUT_DIR/live_orders.jsonl"
SESSION="${CURRENT_YES_CORE_CARRY_NEAR_CORE_WS_TMUX_SESSION:-weather_core_carry_near_core_ws_controller}"
LOG_FILE="$OUTPUT_DIR/near_core_ws_controller.log"
MARKET_PROXY="$(weather_resolve_market_proxy "$ROOT")"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$PM_RUNTIME_ROOT")"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$OUTPUT_DIR"
session_cmd="cd '$ROOT' && export PYTHONPATH='$ROOT' && exec '$PY' -u scripts/ops/weather_core_carry_near_core_ws_controller.py \
  --output-dir '$OUTPUT_DIR' \
  --live-orders '$LIVE_ORDERS' \
  --market-ws-health '$MARKET_WS_HEALTH' \
  --market-proxy '$MARKET_PROXY' \
  --heartbeat-ms 250 \
  --readiness-publish-interval-ms 500 \
  --market-health-max-age-sec 30 \
  --private-max-silence-sec 15 \
  --ping-interval-sec 10 \
  --live --confirm-live >> '$LOG_FILE' 2>&1"
weather_jrs_tmux_guarded_replace_session "$TMUX_SOCKET" "$SESSION" "$session_cmd"

echo "started tmux_socket=$TMUX_SOCKET session=$SESSION output=$OUTPUT_DIR log=$LOG_FILE"
