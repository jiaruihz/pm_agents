#!/usr/bin/env bash
# Canonical market-book loop. This is the only Gamma/CLOB raw owner.
set -uo pipefail

SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:?}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:?}"
MARKET_BOOKS_ROOT="${WEATHER_MARKET_BOOKS_ROOT:?}"
MARKET_LADDER_ROOT="${WEATHER_MARKET_LADDER_ROOT:?}"
CACHE_ROOT="${WEATHER_DATA_FEED_CACHE_ROOT:?}"
OBSERVATION_CACHE="${WEATHER_OBSERVATION_CACHE:?}"
INTERVAL_SEC="${WEATHER_MARKET_BOOKS_INTERVAL_SEC:-300}"
ORDERBOOK_BUDGET_SEC="${WEATHER_MARKET_BOOKS_BUDGET_SEC:-240}"
LOG_FILE="${WEATHER_MARKET_BOOKS_LOG_FILE:?}"
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

mkdir -p "$MARKET_BOOKS_ROOT" "$MARKET_LADDER_ROOT" "$CACHE_ROOT" "$WS_OUTPUT_ROOT"
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

"${ws_cmd[@]}" >> "$WS_LOG_FILE" 2>&1 &
ws_pid=$!
cleanup() {
  kill "$ws_pid" 2>/dev/null || true
  wait "$ws_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM HUP

while true; do
  cycle_started_epoch="$(date +%s)"
  date -u +"[market_books] capture_start_utc=%Y-%m-%dT%H:%M:%SZ" >> "$LOG_FILE"

  "$PY" -u -m weather_data_feed_service market-books -- \
    --output-root "$MARKET_BOOKS_ROOT" \
    --market-ladder-root "$MARKET_LADDER_ROOT" \
    --observation-cache "$OBSERVATION_CACHE" \
    --orderbook-budget-sec "$ORDERBOOK_BUDGET_SEC" >> "$LOG_FILE" 2>&1
  raw_rc=$?

  cycle_finished_epoch="$(date +%s)"
  cycle_elapsed_sec="$((cycle_finished_epoch - cycle_started_epoch))"
  sleep_sec="$((INTERVAL_SEC - cycle_elapsed_sec))"
  if (( sleep_sec < 0 )); then sleep_sec=0; fi
  date -u +"[market_books] capture_done_utc=%Y-%m-%dT%H:%M:%SZ raw_rc=$raw_rc elapsed_sec=$cycle_elapsed_sec next_sleep_sec=$sleep_sec" >> "$LOG_FILE"
  sleep "$sleep_sec"
done
