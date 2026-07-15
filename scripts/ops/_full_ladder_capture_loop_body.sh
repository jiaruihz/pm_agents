#!/usr/bin/env bash
# Loop body for the full-ladder orderbook capture (invoked by
# start_full_ladder_orderbook_capture.sh inside a dedicated tmux server).
# Kept as its own file so the tmux command stays a single exec with no fragile
# multi-line inline quoting.
set -uo pipefail

SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:?}"
OUTPUT_ROOT="${WEATHER_FULL_LADDER_OUTPUT_ROOT:?}"
CACHE_ROOT="${WEATHER_FULL_LADDER_CACHE_ROOT:?}"
INTERVAL_SEC="${WEATHER_FULL_LADDER_INTERVAL_SEC:-1200}"
ORDERBOOK_BUDGET_SEC="${WEATHER_FULL_LADDER_ORDERBOOK_BUDGET_SEC:-600}"
ORDERBOOK_WORKERS="${WEATHER_FULL_LADDER_ORDERBOOK_WORKERS:-2}"
LOG_FILE="${WEATHER_FULL_LADDER_LOG_FILE:?}"
PY="$SERVICE_DIR/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

mkdir -p "$OUTPUT_ROOT" "$CACHE_ROOT"
cd "$SERVICE_DIR"

# Reach Polymarket the same way the main data feed does: source the service .env
# for the market proxy + credentials.  We deliberately do NOT run our own proxy
# failover -- the main feed keeps a healthy node selected on the shared
# controller, and running a second failover would fight it over node switches.
if [[ -f "$SERVICE_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$SERVICE_DIR/.env"
  set +a
fi

# Polymarket gamma/CLOB is only reachable via the local market proxy; a direct
# connection is rate-limited/blocked under snapshot concurrency (curl 28
# timeouts).  The .env ships this key empty, so default it explicitly.
export WEATHER_DATA_FEED_MARKET_PROXY="${WEATHER_DATA_FEED_MARKET_PROXY:-http://127.0.0.1:7890}"
[[ -n "$WEATHER_DATA_FEED_MARKET_PROXY" ]] || WEATHER_DATA_FEED_MARKET_PROXY="http://127.0.0.1:7890"
export WEATHER_DATA_FEED_MARKET_PROXY
PROJECT_DIR="${PROJECT_DIR:-$HOME/projects/pm_agents}"
FAILOVER="$PROJECT_DIR/scripts/ops/weather_market_proxy_failover.py"

while true; do
  date -u +"[full_ladder] snapshot_start_utc=%Y-%m-%dT%H:%M:%SZ" >> "$LOG_FILE"
  # Ensure the shared proxy controller has a healthy node before snapshotting
  # (best-effort; the main feed also does this, we piggyback on its selection).
  if [[ -x "$PROJECT_DIR/.venv/bin/python" && -f "$FAILOVER" ]]; then
    "$PROJECT_DIR/.venv/bin/python" "$FAILOVER" >> "$LOG_FILE" 2>&1 || true
  fi
  "$PY" -u -m weather_data_feed_service \
    --output-root "$OUTPUT_ROOT" --cache-root "$CACHE_ROOT" \
    snapshot-full -- \
    --orderbook-budget-sec "$ORDERBOOK_BUDGET_SEC" \
    --orderbook-workers "$ORDERBOOK_WORKERS" >> "$LOG_FILE" 2>&1
  rc=$?
  date -u +"[full_ladder] snapshot_done_utc=%Y-%m-%dT%H:%M:%SZ rc=$rc" >> "$LOG_FILE"
  sleep "$INTERVAL_SEC"
done
