#!/usr/bin/env bash
# Canonical market-book loop. Raw CLOB capture is committed before either
# strategy or full-ladder weather views are materialized.
set -uo pipefail

SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:?}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:?}"
MARKET_BOOKS_ROOT="${WEATHER_MARKET_BOOKS_ROOT:?}"
MARKET_LADDER_ROOT="${WEATHER_MARKET_LADDER_ROOT:?}"
STRATEGY_OUTPUT_ROOT="${WEATHER_STRATEGY_SNAPSHOT_OUTPUT_ROOT:?}"
LEGACY_FULL_OUTPUT_ROOT="${WEATHER_LEGACY_FULL_OUTPUT_ROOT:?}"
CACHE_ROOT="${WEATHER_DATA_FEED_CACHE_ROOT:?}"
OBSERVATION_CACHE="${WEATHER_OBSERVATION_CACHE:?}"
INTERVAL_SEC="${WEATHER_MARKET_BOOKS_INTERVAL_SEC:-300}"
ORDERBOOK_BUDGET_SEC="${WEATHER_MARKET_BOOKS_BUDGET_SEC:-240}"
SOURCE_MAX_AGE_SEC="${WEATHER_MARKET_BOOKS_SOURCE_MAX_AGE_SEC:-420}"
LOG_FILE="${WEATHER_MARKET_BOOKS_LOG_FILE:?}"
PY="$SERVICE_DIR/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

mkdir -p "$MARKET_BOOKS_ROOT" "$MARKET_LADDER_ROOT" \
  "$STRATEGY_OUTPUT_ROOT" "$LEGACY_FULL_OUTPUT_ROOT" "$CACHE_ROOT"
cd "$SERVICE_DIR"

if [[ -f "$SERVICE_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$SERVICE_DIR/.env"
  set +a
fi
export WEATHER_DATA_FEED_MARKET_PROXY="${WEATHER_DATA_FEED_MARKET_PROXY:-http://127.0.0.1:7890}"

while true; do
  cycle_started_epoch="$(date +%s)"
  date -u +"[market_books] capture_start_utc=%Y-%m-%dT%H:%M:%SZ" >> "$LOG_FILE"

  "$PY" -u -m weather_data_feed_service market-books -- \
    --output-root "$MARKET_BOOKS_ROOT" \
    --market-ladder-root "$MARKET_LADDER_ROOT" \
    --legacy-full-orderbook-root "$LEGACY_FULL_OUTPUT_ROOT/orderbook_snapshots" \
    --observation-cache "$OBSERVATION_CACHE" \
    --orderbook-budget-sec "$ORDERBOOK_BUDGET_SEC" >> "$LOG_FILE" 2>&1
  raw_rc=$?

  # Compatibility weather+market view. It may fail or remain partial when
  # forecast data is unavailable; the canonical raw capture above is already
  # durable and remains healthy.
  "$PY" -u -m weather_data_feed_service \
    --output-root "$LEGACY_FULL_OUTPUT_ROOT" --cache-root "$CACHE_ROOT" \
    snapshot-full -- \
    --orderbook-source-latest "$MARKET_BOOKS_ROOT/latest.json" \
    --orderbook-source-max-age-sec "$SOURCE_MAX_AGE_SEC" >> "$LOG_FILE" 2>&1
  view_rc=$?

  cycle_finished_epoch="$(date +%s)"
  cycle_elapsed_sec="$((cycle_finished_epoch - cycle_started_epoch))"
  sleep_sec="$((INTERVAL_SEC - cycle_elapsed_sec))"
  if (( sleep_sec < 0 )); then sleep_sec=0; fi
  date -u +"[market_books] capture_done_utc=%Y-%m-%dT%H:%M:%SZ raw_rc=$raw_rc view_rc=$view_rc elapsed_sec=$cycle_elapsed_sec next_sleep_sec=$sleep_sec" >> "$LOG_FILE"
  sleep "$sleep_sec"
done
