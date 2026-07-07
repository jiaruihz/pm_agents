#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:-$HOME/projects/weather_data_feed_service}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$HOME/projects/weather_data_feed_service_runtime}"
OUTPUT_ROOT="${WEATHER_DATA_FEED_TARGETED_OUTPUT_ROOT:-$RUNTIME_ROOT/targeted_output}"
OBS_OUTPUT="${WEATHER_DATA_FEED_OBSERVATION_OUTPUT:-$RUNTIME_ROOT/output/observations/latest.json}"
SOURCE_EVENTS_OUTPUT="${WEATHER_DATA_FEED_SOURCE_EVENTS_OUTPUT_DIR:-$RUNTIME_ROOT/output/source_events}"
FORECAST_ENRICHMENT_OUTPUT="${WEATHER_DATA_FEED_FORECAST_ENRICHMENT_OUTPUT_DIR:-$RUNTIME_ROOT/output/forecast_enrichment}"
CACHE_ROOT="${WEATHER_DATA_FEED_CACHE_ROOT:-$RUNTIME_ROOT/cache}"
LOOP_DIR="${WEATHER_DATA_FEED_LOOP_DIR:-$RUNTIME_ROOT/loop}"
PID_FILE="$LOOP_DIR/data_feed_loop.pid"
LOG_FILE="$LOOP_DIR/data_feed_loop.log"
PY="$SERVICE_DIR/.venv/bin/python"

OBS_INTERVAL_SEC="${WEATHER_DATA_FEED_OBS_INTERVAL_SEC:-300}"
SOURCE_EVENTS_INTERVAL_SEC="${WEATHER_DATA_FEED_SOURCE_EVENTS_INTERVAL_SEC:-120}"
FORECAST_ENRICHMENT_ENABLED="${WEATHER_DATA_FEED_FORECAST_ENRICHMENT_ENABLED:-0}"
FORECAST_ENRICHMENT_INTERVAL_SEC="${WEATHER_DATA_FEED_FORECAST_ENRICHMENT_INTERVAL_SEC:-900}"
SNAPSHOT_INTERVAL_SEC="${WEATHER_DATA_FEED_SNAPSHOT_INTERVAL_SEC:-600}"
SNAPSHOT_COMMAND="${WEATHER_DATA_FEED_SNAPSHOT_COMMAND:-snapshot-targeted}"
SNAPSHOT_ORDERBOOK_BUDGET_SEC="${WEATHER_DATA_FEED_ORDERBOOK_BUDGET_SEC:-60}"
SNAPSHOT_ORDERBOOK_WORKERS="${WEATHER_DATA_FEED_ORDERBOOK_WORKERS:-1}"
MARKET_PROXY_PROBE_TIMEOUT_SEC="${WEATHER_MARKET_PROXY_PROBE_TIMEOUT_SEC:-5}"
MARKET_PROXY_1X_CANDIDATES="${WEATHER_MARKET_PROXY_1X_CANDIDATES:-🇭🇰 香港 01丨1x HK,🇭🇰 香港 02丨1x HK,🇭🇰 香港 03丨1x HK,🇭🇰 香港家宽 01丨1x HK,🇭🇰 香港家宽 02丨1x HK,🇭🇰 香港家宽 03丨1x HK,🇭🇰 香港家宽 04丨1x HK,🇯🇵 日本 01丨1x JP,🇯🇵 日本 02丨1x JP,🇯🇵 日本 03丨1x JP}"
MARKET_PROXY_FAILOVER_SCRIPT="${WEATHER_MARKET_PROXY_FAILOVER_SCRIPT:-$HOME/projects/pm_agents/scripts/ops/weather_market_proxy_failover.py}"

mkdir -p "$LOOP_DIR" "$(dirname "$OBS_OUTPUT")" "$SOURCE_EVENTS_OUTPUT" "$FORECAST_ENRICHMENT_OUTPUT" "$OUTPUT_ROOT" "$CACHE_ROOT"

if [[ "${MAC_WEATHER_DATA_FEED_LOOP_CHILD:-0}" != "1" && -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running pid=$old_pid log=$LOG_FILE"
    exit 0
  fi
fi

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

case "$SNAPSHOT_COMMAND" in
  snapshot|snapshot-targeted|snapshot-full)
    ;;
  *)
    echo "unsupported WEATHER_DATA_FEED_SNAPSHOT_COMMAND=$SNAPSHOT_COMMAND" >&2
    exit 2
    ;;
esac

if [[ "${MAC_WEATHER_DATA_FEED_LOOP_CHILD:-0}" != "1" ]]; then
  nohup env MAC_WEATHER_DATA_FEED_LOOP_CHILD=1 "$0" >>"$LOG_FILE" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$PID_FILE"
  echo "started mac weather data-feed loop pid=$pid log=$LOG_FILE output_root=$OUTPUT_ROOT obs=$OBS_OUTPUT cache=$CACHE_ROOT"
  exit 0
fi

echo "$$" > "$PID_FILE"
date -u +"[mac_data_feed] loop_start_utc=%Y-%m-%dT%H:%M:%SZ pid=$$ output_root=$OUTPUT_ROOT obs=$OBS_OUTPUT source_events=$SOURCE_EVENTS_OUTPUT forecast_enrichment=$FORECAST_ENRICHMENT_OUTPUT forecast_enrichment_enabled=$FORECAST_ENRICHMENT_ENABLED cache=$CACHE_ROOT"

{
  cd "$SERVICE_DIR"
  trap 'rc=$?; date -u +"[mac_data_feed] loop_exit_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"; rm -f "$PID_FILE"' EXIT
  if [[ -f "$SERVICE_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$SERVICE_DIR/.env"
    set +a
  fi
  next_obs=0
  next_source_events=0
  next_forecast_enrichment=0
  next_snapshot=0
  while true; do
    now="$(date +%s)"
    if (( now >= next_obs )); then
      date -u +"[mac_data_feed] observations_start_utc=%Y-%m-%dT%H:%M:%SZ"
      set +e
      "$PY" -u -m weather_data_feed_service \
        observations \
        --output "$OBS_OUTPUT" \
        --include-station-diff \
        --include-fallback-sources \
        --max-workers 4
      rc=$?
      set -e
      date -u +"[mac_data_feed] observations_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
      next_obs=$(( $(date +%s) + OBS_INTERVAL_SEC ))
    fi

    now="$(date +%s)"
    if (( now >= next_source_events )); then
      date -u +"[mac_data_feed] source_events_start_utc=%Y-%m-%dT%H:%M:%SZ"
      set +e
      WEATHER_DATA_FEED_SOURCE_EVENTS_OUTPUT_DIR="$SOURCE_EVENTS_OUTPUT" \
        "$PY" -u -m weather_data_feed_service \
          source-events -- \
          --output-dir "$SOURCE_EVENTS_OUTPUT" \
          --include-station-diff \
          --include-fallback-sources \
          --max-workers 4
      rc=$?
      set -e
      date -u +"[mac_data_feed] source_events_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
      next_source_events=$(( $(date +%s) + SOURCE_EVENTS_INTERVAL_SEC ))
    fi

    now="$(date +%s)"
    if [[ "$FORECAST_ENRICHMENT_ENABLED" == "1" ]] && (( now >= next_forecast_enrichment )); then
      date -u +"[mac_data_feed] forecast_enrichment_start_utc=%Y-%m-%dT%H:%M:%SZ"
      set +e
      "$PY" -u -m weather_data_feed_service \
        forecast-enrichment -- \
        --output-dir "$FORECAST_ENRICHMENT_OUTPUT" \
        --include-station-diff \
        --max-workers 4
      rc=$?
      set -e
      date -u +"[mac_data_feed] forecast_enrichment_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
      next_forecast_enrichment=$(( $(date +%s) + FORECAST_ENRICHMENT_INTERVAL_SEC ))
    fi

    now="$(date +%s)"
    if (( now >= next_snapshot )); then
      date -u +"[mac_data_feed] snapshot_start_utc=%Y-%m-%dT%H:%M:%SZ"
      if [[ -x "$MARKET_PROXY_FAILOVER_SCRIPT" ]]; then
        date -u +"[mac_data_feed] market_proxy_check_start_utc=%Y-%m-%dT%H:%M:%SZ"
        set +e
        WEATHER_DATA_FEED_SNAPSHOT_DIR="$OUTPUT_ROOT/paper_snapshots" \
        WEATHER_MARKET_PROXY_PROBE_TIMEOUT_SEC="$MARKET_PROXY_PROBE_TIMEOUT_SEC" \
        WEATHER_MARKET_PROXY_1X_CANDIDATES="$MARKET_PROXY_1X_CANDIDATES" \
          "$MARKET_PROXY_FAILOVER_SCRIPT"
        proxy_rc=$?
        set -e
        date -u +"[mac_data_feed] market_proxy_check_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$proxy_rc"
        if [[ "$proxy_rc" -ne 0 ]]; then
          date -u +"[mac_data_feed] snapshot_skipped_utc=%Y-%m-%dT%H:%M:%SZ reason=market_proxy_unhealthy"
          next_snapshot=$(( $(date +%s) + SNAPSHOT_INTERVAL_SEC ))
          sleep 10
          continue
        fi
      fi
      set +e
      "$PY" -u -m weather_data_feed_service \
        --output-root "$OUTPUT_ROOT" \
        --cache-root "$CACHE_ROOT" \
        "$SNAPSHOT_COMMAND" -- \
        --orderbook-budget-sec "$SNAPSHOT_ORDERBOOK_BUDGET_SEC" \
        --orderbook-workers "$SNAPSHOT_ORDERBOOK_WORKERS"
      rc=$?
      set -e
      date -u +"[mac_data_feed] snapshot_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
      next_snapshot=$(( $(date +%s) + SNAPSHOT_INTERVAL_SEC ))
    fi

    sleep 10
  done
}
