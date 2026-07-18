#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:-$HOME/projects/weather_data_feed_service}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$HOME/projects/weather_data_feed_service_runtime}"
OUTPUT_ROOT="${WEATHER_DATA_FEED_TARGETED_OUTPUT_ROOT:-$RUNTIME_ROOT/targeted_output}"
OBS_OUTPUT="${WEATHER_DATA_FEED_OBSERVATION_OUTPUT:-$RUNTIME_ROOT/output/observations/latest.json}"
SOURCE_EVENTS_OUTPUT="${WEATHER_DATA_FEED_SOURCE_EVENTS_OUTPUT_DIR:-$RUNTIME_ROOT/output/source_events}"
SOURCE_EVENTS_RESEARCH_CITIES="${WEATHER_DATA_FEED_SOURCE_EVENTS_RESEARCH_CITIES:-Seoul HongKong Shenzhen TelAviv Istanbul Moscow}"
FORECAST_ENRICHMENT_OUTPUT="${WEATHER_DATA_FEED_FORECAST_ENRICHMENT_OUTPUT_DIR:-$RUNTIME_ROOT/output/forecast_enrichment}"
RUNWAY_OBSERVATIONS_OUTPUT="${WEATHER_DATA_FEED_RUNWAY_OBSERVATIONS_OUTPUT_DIR:-$RUNTIME_ROOT/output/runway_observations}"
HIGH_FREQUENCY_OBSERVATIONS_OUTPUT="${WEATHER_DATA_FEED_HIGH_FREQUENCY_OBSERVATIONS_OUTPUT_DIR:-$RUNTIME_ROOT/output/high_frequency_observations}"
CACHE_ROOT="${WEATHER_DATA_FEED_CACHE_ROOT:-$RUNTIME_ROOT/cache}"
LOOP_DIR="${WEATHER_DATA_FEED_LOOP_DIR:-$RUNTIME_ROOT/loop}"
PID_FILE="$LOOP_DIR/data_feed_loop.pid"
LOG_FILE="$LOOP_DIR/data_feed_loop.log"
PY="$SERVICE_DIR/.venv/bin/python"

OBS_INTERVAL_SEC="${WEATHER_DATA_FEED_OBS_INTERVAL_SEC:-300}"
SOURCE_EVENTS_INTERVAL_SEC="${WEATHER_DATA_FEED_SOURCE_EVENTS_INTERVAL_SEC:-120}"
FORECAST_ENRICHMENT_ENABLED="${WEATHER_DATA_FEED_FORECAST_ENRICHMENT_ENABLED:-1}"
FORECAST_ENRICHMENT_INTERVAL_SEC="${WEATHER_DATA_FEED_FORECAST_ENRICHMENT_INTERVAL_SEC:-900}"
RUNWAY_OBSERVATIONS_ENABLED="${WEATHER_DATA_FEED_RUNWAY_OBSERVATIONS_ENABLED:-0}"
RUNWAY_OBSERVATIONS_INTERVAL_SEC="${WEATHER_DATA_FEED_RUNWAY_OBSERVATIONS_INTERVAL_SEC:-60}"
RUNWAY_OBSERVATIONS_SOURCES="${WEATHER_DATA_FEED_RUNWAY_OBSERVATIONS_SOURCES:-}"
HIGH_FREQUENCY_OBSERVATIONS_ENABLED="${WEATHER_DATA_FEED_HIGH_FREQUENCY_OBSERVATIONS_ENABLED:-0}"
HIGH_FREQUENCY_OBSERVATIONS_INTERVAL_SEC="${WEATHER_DATA_FEED_HIGH_FREQUENCY_OBSERVATIONS_INTERVAL_SEC:-20}"
HIGH_FREQUENCY_OBSERVATIONS_SOURCES="${WEATHER_DATA_FEED_HIGH_FREQUENCY_OBSERVATIONS_SOURCES:-amos_runway noaa_madis_hfmetar singapore_mss jma_amedas hko_obs cowin_obs fmi mgm ims_lod}"
HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC="${WEATHER_DATA_FEED_HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC:-jma_amedas=300 noaa_madis_hfmetar=300 fmi=60 mgm=300 ims_lod=300}"
HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC="${WEATHER_DATA_FEED_HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC:-jma_amedas=5-8,15-18,25-28,35-38,45-48,55-58:20}"
FAST_OBS_ACTIVE_LOCAL_START_HOUR="${WEATHER_DATA_FEED_FAST_OBS_ACTIVE_LOCAL_START_HOUR:-6}"
FAST_OBS_ACTIVE_LOCAL_END_HOUR="${WEATHER_DATA_FEED_FAST_OBS_ACTIVE_LOCAL_END_HOUR:-22}"
SNAPSHOT_INTERVAL_SEC="${WEATHER_DATA_FEED_SNAPSHOT_INTERVAL_SEC:-600}"
SNAPSHOT_COMMAND="${WEATHER_DATA_FEED_SNAPSHOT_COMMAND:-snapshot-targeted}"
SNAPSHOT_ORDERBOOK_BUDGET_SEC="${WEATHER_DATA_FEED_ORDERBOOK_BUDGET_SEC:-60}"
SNAPSHOT_ORDERBOOK_WORKERS="${WEATHER_DATA_FEED_ORDERBOOK_WORKERS:-1}"
MARKET_PROXY_PROBE_TIMEOUT_SEC="${WEATHER_MARKET_PROXY_PROBE_TIMEOUT_SEC:-5}"
MARKET_PROXY_1X_CANDIDATES="${WEATHER_MARKET_PROXY_1X_CANDIDATES:-🇭🇰 香港 01丨1x HK,🇭🇰 香港 02丨1x HK,🇭🇰 香港 03丨1x HK,🇭🇰 香港家宽 01丨1x HK,🇭🇰 香港家宽 02丨1x HK,🇭🇰 香港家宽 03丨1x HK,🇭🇰 香港家宽 04丨1x HK,🇯🇵 日本 01丨1x JP,🇯🇵 日本 02丨1x JP,🇯🇵 日本 03丨1x JP}"
MARKET_PROXY_FAILOVER_SCRIPT="${WEATHER_MARKET_PROXY_FAILOVER_SCRIPT:-$HOME/projects/pm_agents/scripts/ops/weather_market_proxy_failover.py}"

mkdir -p "$LOOP_DIR" "$(dirname "$OBS_OUTPUT")" "$SOURCE_EVENTS_OUTPUT" "$FORECAST_ENRICHMENT_OUTPUT" "$RUNWAY_OBSERVATIONS_OUTPUT" "$HIGH_FREQUENCY_OBSERVATIONS_OUTPUT" "$OUTPUT_ROOT" "$CACHE_ROOT"

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
  exec "$PROJECT_DIR/scripts/ops/start_mac_weather_data_feed_jrs_tmux.sh"
fi

echo "$$" > "$PID_FILE"
date -u +"[mac_data_feed] loop_start_utc=%Y-%m-%dT%H:%M:%SZ pid=$$ output_root=$OUTPUT_ROOT obs=$OBS_OUTPUT source_events=$SOURCE_EVENTS_OUTPUT forecast_enrichment=$FORECAST_ENRICHMENT_OUTPUT forecast_enrichment_enabled=$FORECAST_ENRICHMENT_ENABLED runway_observations=$RUNWAY_OBSERVATIONS_OUTPUT runway_observations_enabled=$RUNWAY_OBSERVATIONS_ENABLED high_frequency_observations=$HIGH_FREQUENCY_OBSERVATIONS_OUTPUT high_frequency_observations_enabled=$HIGH_FREQUENCY_OBSERVATIONS_ENABLED cache=$CACHE_ROOT"

{
  cd "$SERVICE_DIR"
  trap 'rc=$?; date -u +"[mac_data_feed] loop_exit_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"; rm -f "$PID_FILE"' EXIT
  if [[ -f "$SERVICE_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$SERVICE_DIR/.env"
    set +a
    raw_amsc_session_line="$(grep -E '^WEATHER_DATA_FEED_AMSC_SESSION_ID=' "$SERVICE_DIR/.env" | tail -n 1 || true)"
    if [[ -n "$raw_amsc_session_line" ]]; then
      raw_amsc_session="${raw_amsc_session_line#WEATHER_DATA_FEED_AMSC_SESSION_ID=}"
      raw_amsc_session="${raw_amsc_session%\"}"
      raw_amsc_session="${raw_amsc_session#\"}"
      raw_amsc_session="${raw_amsc_session%\'}"
      raw_amsc_session="${raw_amsc_session#\'}"
      export WEATHER_DATA_FEED_AMSC_SESSION_ID="$raw_amsc_session"
    fi
  fi
  RUNWAY_OBSERVATIONS_ENABLED="${WEATHER_DATA_FEED_RUNWAY_OBSERVATIONS_ENABLED:-$RUNWAY_OBSERVATIONS_ENABLED}"
  RUNWAY_OBSERVATIONS_INTERVAL_SEC="${WEATHER_DATA_FEED_RUNWAY_OBSERVATIONS_INTERVAL_SEC:-$RUNWAY_OBSERVATIONS_INTERVAL_SEC}"
  RUNWAY_OBSERVATIONS_SOURCES="${WEATHER_DATA_FEED_RUNWAY_OBSERVATIONS_SOURCES:-$RUNWAY_OBSERVATIONS_SOURCES}"
  SOURCE_EVENTS_RESEARCH_CITIES="${WEATHER_DATA_FEED_SOURCE_EVENTS_RESEARCH_CITIES:-$SOURCE_EVENTS_RESEARCH_CITIES}"
  HIGH_FREQUENCY_OBSERVATIONS_ENABLED="${WEATHER_DATA_FEED_HIGH_FREQUENCY_OBSERVATIONS_ENABLED:-$HIGH_FREQUENCY_OBSERVATIONS_ENABLED}"
  HIGH_FREQUENCY_OBSERVATIONS_INTERVAL_SEC="${WEATHER_DATA_FEED_HIGH_FREQUENCY_OBSERVATIONS_INTERVAL_SEC:-$HIGH_FREQUENCY_OBSERVATIONS_INTERVAL_SEC}"
  HIGH_FREQUENCY_OBSERVATIONS_SOURCES="${WEATHER_DATA_FEED_HIGH_FREQUENCY_OBSERVATIONS_SOURCES:-$HIGH_FREQUENCY_OBSERVATIONS_SOURCES}"
  HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC="${WEATHER_DATA_FEED_HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC:-$HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC}"
  HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC="${WEATHER_DATA_FEED_HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC:-$HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC}"
  FAST_OBS_ACTIVE_LOCAL_START_HOUR="${WEATHER_DATA_FEED_FAST_OBS_ACTIVE_LOCAL_START_HOUR:-$FAST_OBS_ACTIVE_LOCAL_START_HOUR}"
  FAST_OBS_ACTIVE_LOCAL_END_HOUR="${WEATHER_DATA_FEED_FAST_OBS_ACTIVE_LOCAL_END_HOUR:-$FAST_OBS_ACTIVE_LOCAL_END_HOUR}"
  read -r -a RUNWAY_OBSERVATIONS_SOURCE_ARGS <<< "$RUNWAY_OBSERVATIONS_SOURCES"
  read -r -a SOURCE_EVENTS_RESEARCH_CITY_ARGS <<< "$SOURCE_EVENTS_RESEARCH_CITIES"
  read -r -a HIGH_FREQUENCY_OBSERVATIONS_SOURCE_ARGS <<< "$HIGH_FREQUENCY_OBSERVATIONS_SOURCES"
  date -u +"[mac_data_feed] runtime_config_utc=%Y-%m-%dT%H:%M:%SZ runway_observations_enabled=$RUNWAY_OBSERVATIONS_ENABLED runway_observations_interval_sec=$RUNWAY_OBSERVATIONS_INTERVAL_SEC runway_observations_sources=${RUNWAY_OBSERVATIONS_SOURCES:-all} source_events_research_cities=${SOURCE_EVENTS_RESEARCH_CITIES:-none} high_frequency_observations_enabled=$HIGH_FREQUENCY_OBSERVATIONS_ENABLED high_frequency_observations_interval_sec=$HIGH_FREQUENCY_OBSERVATIONS_INTERVAL_SEC high_frequency_observations_sources=${HIGH_FREQUENCY_OBSERVATIONS_SOURCES:-all} high_frequency_source_min_interval_sec=$HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC high_frequency_source_minute_window_min_interval_sec=${HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC:-none} fast_obs_active_local_start_hour=$FAST_OBS_ACTIVE_LOCAL_START_HOUR fast_obs_active_local_end_hour=$FAST_OBS_ACTIVE_LOCAL_END_HOUR service_dir=$SERVICE_DIR"
  next_obs=0
  next_source_events=0
  next_forecast_enrichment=0
  next_runway_observations=0
  next_high_frequency_observations=0
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
      source_events_args=(
        --output-dir "$SOURCE_EVENTS_OUTPUT"
        --include-station-diff
        --include-fallback-sources
        --include-awc-cache-first-arrival
        --max-workers 4
      )
      if [[ -n "$SOURCE_EVENTS_RESEARCH_CITIES" ]]; then
        source_events_args+=(--include-research-cities --research-cities "${SOURCE_EVENTS_RESEARCH_CITY_ARGS[@]}")
      fi
      WEATHER_DATA_FEED_SOURCE_EVENTS_OUTPUT_DIR="$SOURCE_EVENTS_OUTPUT" \
        "$PY" -u -m weather_data_feed_service \
          source-events -- \
          "${source_events_args[@]}"
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
    if [[ "$RUNWAY_OBSERVATIONS_ENABLED" == "1" ]] && (( now >= next_runway_observations )); then
      date -u +"[mac_data_feed] runway_observations_start_utc=%Y-%m-%dT%H:%M:%SZ"
      set +e
      if [[ -n "$RUNWAY_OBSERVATIONS_SOURCES" ]]; then
        "$PY" -u -m weather_data_feed_service \
          runway-observations -- \
          --output-dir "$RUNWAY_OBSERVATIONS_OUTPUT" \
          --sources "${RUNWAY_OBSERVATIONS_SOURCE_ARGS[@]}" \
          --active-local-start-hour "$FAST_OBS_ACTIVE_LOCAL_START_HOUR" \
          --active-local-end-hour "$FAST_OBS_ACTIVE_LOCAL_END_HOUR" \
          --max-workers 4
      else
        "$PY" -u -m weather_data_feed_service \
          runway-observations -- \
          --output-dir "$RUNWAY_OBSERVATIONS_OUTPUT" \
          --active-local-start-hour "$FAST_OBS_ACTIVE_LOCAL_START_HOUR" \
          --active-local-end-hour "$FAST_OBS_ACTIVE_LOCAL_END_HOUR" \
          --max-workers 4
      fi
      rc=$?
      set -e
      date -u +"[mac_data_feed] runway_observations_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
      next_runway_observations=$(( $(date +%s) + RUNWAY_OBSERVATIONS_INTERVAL_SEC ))
    fi

    now="$(date +%s)"
    if [[ "$HIGH_FREQUENCY_OBSERVATIONS_ENABLED" == "1" ]] && (( now >= next_high_frequency_observations )); then
      date -u +"[mac_data_feed] high_frequency_observations_start_utc=%Y-%m-%dT%H:%M:%SZ"
      set +e
      read -r -a HIGH_FREQUENCY_SOURCE_INTERVAL_ARGS <<< "$HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC"
      HIGH_FREQUENCY_SOURCE_INTERVAL_CLI=()
      for interval_arg in "${HIGH_FREQUENCY_SOURCE_INTERVAL_ARGS[@]}"; do
        HIGH_FREQUENCY_SOURCE_INTERVAL_CLI+=(--source-min-interval-sec "$interval_arg")
      done
      read -r -a HIGH_FREQUENCY_SOURCE_WINDOW_INTERVAL_ARGS <<< "$HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC"
      for interval_arg in "${HIGH_FREQUENCY_SOURCE_WINDOW_INTERVAL_ARGS[@]}"; do
        HIGH_FREQUENCY_SOURCE_INTERVAL_CLI+=(--source-minute-window-min-interval-sec "$interval_arg")
      done
      if [[ -n "$HIGH_FREQUENCY_OBSERVATIONS_SOURCES" ]]; then
        "$PY" -u -m weather_data_feed_service \
          high-frequency-observations -- \
          --output-dir "$HIGH_FREQUENCY_OBSERVATIONS_OUTPUT" \
          --sources "${HIGH_FREQUENCY_OBSERVATIONS_SOURCE_ARGS[@]}" \
          --active-local-start-hour "$FAST_OBS_ACTIVE_LOCAL_START_HOUR" \
          --active-local-end-hour "$FAST_OBS_ACTIVE_LOCAL_END_HOUR" \
          "${HIGH_FREQUENCY_SOURCE_INTERVAL_CLI[@]}" \
          --max-workers 4
      else
        "$PY" -u -m weather_data_feed_service \
          high-frequency-observations -- \
          --output-dir "$HIGH_FREQUENCY_OBSERVATIONS_OUTPUT" \
          --active-local-start-hour "$FAST_OBS_ACTIVE_LOCAL_START_HOUR" \
          --active-local-end-hour "$FAST_OBS_ACTIVE_LOCAL_END_HOUR" \
          "${HIGH_FREQUENCY_SOURCE_INTERVAL_CLI[@]}" \
          --max-workers 4
      fi
      rc=$?
      set -e
      date -u +"[mac_data_feed] high_frequency_observations_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
      next_high_frequency_observations=$(( $(date +%s) + HIGH_FREQUENCY_OBSERVATIONS_INTERVAL_SEC ))
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
