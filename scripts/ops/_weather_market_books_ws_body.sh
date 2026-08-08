#!/usr/bin/env bash
# Selective WebSocket owner for the weather_market_books tmux session.
set -euo pipefail

SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:?}"
MARKET_BOOKS_ROOT="${WEATHER_MARKET_BOOKS_ROOT:?}"
OBSERVATION_CACHE="${WEATHER_OBSERVATION_CACHE:?}"
WS_OUTPUT_ROOT="${WEATHER_MARKET_BOOKS_WS_OUTPUT_ROOT:?}"
WS_HEALTH_PATH="${WEATHER_MARKET_BOOKS_WS_HEALTH_PATH:?}"
WS_LOG_FILE="${WEATHER_MARKET_BOOKS_WS_LOG_FILE:?}"
WS_SOURCE_EVENTS="${WEATHER_MARKET_BOOKS_WS_SOURCE_EVENTS:?}"
WS_CITIES="${WEATHER_MARKET_BOOKS_WS_CITIES:-Amsterdam Tokyo Helsinki Busan Seoul}"
WS_ACTIVE_BRACKET_COUNT="${WEATHER_MARKET_BOOKS_WS_ACTIVE_BRACKET_COUNT:-5}"
WS_POST_INVALIDATION_SEC="${WEATHER_MARKET_BOOKS_WS_POST_INVALIDATION_SEC:-300}"
WS_EVENT_BURST_SEC="${WEATHER_MARKET_BOOKS_WS_EVENT_BURST_SEC:-120}"
WS_DAILY_PAYLOAD_BUDGET_BYTES="${WEATHER_MARKET_BOOKS_WS_DAILY_PAYLOAD_BUDGET_BYTES:-1000000000}"
PY="$SERVICE_DIR/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

mkdir -p "$WS_OUTPUT_ROOT"
cd "$SERVICE_DIR"

if [[ -f "$SERVICE_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$SERVICE_DIR/.env"
  set +a
fi
export WEATHER_DATA_FEED_BUILD_ID="$(git -C "$SERVICE_DIR" rev-parse HEAD)"
source "$SERVICE_DIR/scripts/ops/weather_market_proxy_env.sh"
export WEATHER_DATA_FEED_MARKET_PROXY="$(weather_resolve_market_proxy "$SERVICE_DIR")"

read -r -a ws_city_args <<< "$WS_CITIES"
ws_cmd=(
  "$PY" -u -m weather_data_feed_service market-books-ws --
  --market-books-latest "$MARKET_BOOKS_ROOT/latest.json"
  --observation-cache "$OBSERVATION_CACHE"
  --source-events-jsonl "$WS_SOURCE_EVENTS"
  --output-root "$WS_OUTPUT_ROOT"
  --health-path "$WS_HEALTH_PATH"
  --active-bracket-count "$WS_ACTIVE_BRACKET_COUNT"
  --post-invalidation-sec "$WS_POST_INVALIDATION_SEC"
  --event-burst-sec "$WS_EVENT_BURST_SEC"
  --daily-payload-budget-bytes "$WS_DAILY_PAYLOAD_BUDGET_BYTES"
  --cities
)
ws_cmd+=("${ws_city_args[@]}")
if [[ -n "$WEATHER_DATA_FEED_MARKET_PROXY" ]]; then
  ws_cmd+=(--market-proxy "$WEATHER_DATA_FEED_MARKET_PROXY")
fi

exec "${ws_cmd[@]}" >> "$WS_LOG_FILE" 2>&1
