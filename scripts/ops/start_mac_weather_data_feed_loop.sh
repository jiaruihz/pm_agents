#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
source "$PROJECT_DIR/scripts/ops/weather_process_supervision.sh"
SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:-$HOME/projects/weather_data_feed_service}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$(weather_production_path "$PROJECT_DIR" data_feed_runtime_root)}"
OUTPUT_ROOT="${WEATHER_STRATEGY_SNAPSHOT_ROOT:-$(weather_production_path "$PROJECT_DIR" strategy_snapshot_root)}"
FORECAST_CURVE_ROOT="${WEATHER_DATA_FEED_FORECAST_CURVE_ROOT:-$(weather_production_path "$PROJECT_DIR" forecast_hourly_curve_dir)}"
OBS_OUTPUT="${WEATHER_DATA_FEED_OBSERVATION_OUTPUT:-$(weather_production_path "$PROJECT_DIR" observation_cache_path)}"
SOURCE_EVENTS_OUTPUT="${WEATHER_DATA_FEED_SOURCE_EVENTS_OUTPUT_DIR:-$RUNTIME_ROOT/output/source_events}"
SOURCE_EVENTS_RESEARCH_CITIES="${WEATHER_DATA_FEED_SOURCE_EVENTS_RESEARCH_CITIES:-Seoul HongKong Shenzhen TelAviv Istanbul Moscow}"
RUNWAY_OBSERVATIONS_OUTPUT="${WEATHER_DATA_FEED_RUNWAY_OBSERVATIONS_OUTPUT_DIR:-$RUNTIME_ROOT/output/runway_observations}"
CACHE_ROOT="${WEATHER_DATA_FEED_CACHE_ROOT:-$RUNTIME_ROOT/cache}"
MARKET_BOOKS_LATEST="${WEATHER_MARKET_BOOKS_LATEST:-$(weather_production_path "$PROJECT_DIR" market_books_latest)}"
LOOP_DIR="${WEATHER_DATA_FEED_LOOP_DIR:-$RUNTIME_ROOT/loop}"
PID_FILE="$LOOP_DIR/data_feed_loop.pid"
LOG_FILE="$LOOP_DIR/data_feed_loop.log"
PY="$SERVICE_DIR/.venv/bin/python"

OBS_INTERVAL_SEC="${WEATHER_DATA_FEED_OBS_INTERVAL_SEC:-300}"
SOURCE_EVENTS_INTERVAL_SEC="${WEATHER_DATA_FEED_SOURCE_EVENTS_INTERVAL_SEC:-120}"
SOURCE_EVENTS_SHARD_ONLY="${WEATHER_DATA_FEED_SOURCE_EVENTS_SHARD_ONLY:-1}"
RUNWAY_OBSERVATIONS_ENABLED="${WEATHER_DATA_FEED_RUNWAY_OBSERVATIONS_ENABLED:-0}"
RUNWAY_OBSERVATIONS_INTERVAL_SEC="${WEATHER_DATA_FEED_RUNWAY_OBSERVATIONS_INTERVAL_SEC:-60}"
RUNWAY_OBSERVATIONS_SOURCES="${WEATHER_DATA_FEED_RUNWAY_OBSERVATIONS_SOURCES:-}"
FAST_OBS_ACTIVE_LOCAL_START_HOUR="${WEATHER_DATA_FEED_FAST_OBS_ACTIVE_LOCAL_START_HOUR:-6}"
FAST_OBS_ACTIVE_LOCAL_END_HOUR="${WEATHER_DATA_FEED_FAST_OBS_ACTIVE_LOCAL_END_HOUR:-22}"
SNAPSHOT_INTERVAL_SEC="${WEATHER_DATA_FEED_SNAPSHOT_INTERVAL_SEC:-600}"
SNAPSHOT_COMMAND="${WEATHER_DATA_FEED_SNAPSHOT_COMMAND:-strategy-snapshot}"
SNAPSHOT_ORDERBOOK_BUDGET_SEC="${WEATHER_DATA_FEED_ORDERBOOK_BUDGET_SEC:-120}"
SNAPSHOT_ORDERBOOK_WORKERS="${WEATHER_DATA_FEED_ORDERBOOK_WORKERS:-4}"
OBS_TIMEOUT_SEC="${WEATHER_DATA_FEED_OBS_TIMEOUT_SEC:-90}"
OBSERVATION_ADDITIONAL_CITIES="${WEATHER_DATA_FEED_OBSERVATION_ADDITIONAL_CITIES:-Seoul}"
SOURCE_EVENTS_TIMEOUT_SEC="${WEATHER_DATA_FEED_SOURCE_EVENTS_TIMEOUT_SEC:-120}"
SNAPSHOT_TIMEOUT_SEC="${WEATHER_DATA_FEED_SNAPSHOT_TIMEOUT_SEC:-600}"

mkdir -p "$LOOP_DIR" "$(dirname "$OBS_OUTPUT")" "$SOURCE_EVENTS_OUTPUT" "$RUNWAY_OBSERVATIONS_OUTPUT" "$OUTPUT_ROOT" "$CACHE_ROOT"

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

if [[ "$SNAPSHOT_COMMAND" != "strategy-snapshot" ]]; then
  echo "unsupported production snapshot command: $SNAPSHOT_COMMAND (required: strategy-snapshot)" >&2
  exit 2
fi

if [[ "${WEATHER_DATA_FEED_HIGH_FREQUENCY_OBSERVATIONS_ENABLED:-0}" != "0" ]]; then
  echo "duplicate high-frequency producer is retired; use controller instance weather_live_cross_observations" >&2
  exit 2
fi

if [[ "${MAC_WEATHER_DATA_FEED_LOOP_CHILD:-0}" != "1" ]]; then
  exec "$PROJECT_DIR/scripts/ops/start_mac_weather_data_feed_jrs_tmux.sh"
fi

echo "$$" > "$PID_FILE"
date -u +"[mac_data_feed] loop_start_utc=%Y-%m-%dT%H:%M:%SZ pid=$$ output_root=$OUTPUT_ROOT obs=$OBS_OUTPUT source_events=$SOURCE_EVENTS_OUTPUT forecast_owner=external_controller_managed fast_observation_owner=weather_live_cross_observations runway_observations=$RUNWAY_OBSERVATIONS_OUTPUT runway_observations_enabled=$RUNWAY_OBSERVATIONS_ENABLED cache=$CACHE_ROOT"

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
  FAST_OBS_ACTIVE_LOCAL_START_HOUR="${WEATHER_DATA_FEED_FAST_OBS_ACTIVE_LOCAL_START_HOUR:-$FAST_OBS_ACTIVE_LOCAL_START_HOUR}"
  FAST_OBS_ACTIVE_LOCAL_END_HOUR="${WEATHER_DATA_FEED_FAST_OBS_ACTIVE_LOCAL_END_HOUR:-$FAST_OBS_ACTIVE_LOCAL_END_HOUR}"
  OBSERVATION_ADDITIONAL_CITIES="${WEATHER_DATA_FEED_OBSERVATION_ADDITIONAL_CITIES:-$OBSERVATION_ADDITIONAL_CITIES}"
  read -r -a RUNWAY_OBSERVATIONS_SOURCE_ARGS <<< "$RUNWAY_OBSERVATIONS_SOURCES"
  read -r -a SOURCE_EVENTS_RESEARCH_CITY_ARGS <<< "$SOURCE_EVENTS_RESEARCH_CITIES"
  read -r -a OBSERVATION_ADDITIONAL_CITY_ARGS <<< "$OBSERVATION_ADDITIONAL_CITIES"
  date -u +"[mac_data_feed] runtime_config_utc=%Y-%m-%dT%H:%M:%SZ runway_observations_enabled=$RUNWAY_OBSERVATIONS_ENABLED runway_observations_interval_sec=$RUNWAY_OBSERVATIONS_INTERVAL_SEC runway_observations_sources=${RUNWAY_OBSERVATIONS_SOURCES:-all} source_events_research_cities=${SOURCE_EVENTS_RESEARCH_CITIES:-none} fast_observation_owner=weather_live_cross_observations fast_obs_active_local_start_hour=$FAST_OBS_ACTIVE_LOCAL_START_HOUR fast_obs_active_local_end_hour=$FAST_OBS_ACTIVE_LOCAL_END_HOUR service_dir=$SERVICE_DIR"
  next_obs=0
  next_source_events=0
  next_runway_observations=0
  next_snapshot=0
  snapshot_supervisor_pid=""
  while true; do
    if [[ -n "$snapshot_supervisor_pid" ]] && ! kill -0 "$snapshot_supervisor_pid" 2>/dev/null; then
      set +e
      wait "$snapshot_supervisor_pid"
      rc=$?
      set -e
      date -u +"[mac_data_feed] snapshot_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
      snapshot_supervisor_pid=""
      next_snapshot=$(( $(date +%s) + SNAPSHOT_INTERVAL_SEC ))
    fi

    now="$(date +%s)"
    if (( now >= next_obs )); then
      date -u +"[mac_data_feed] observations_start_utc=%Y-%m-%dT%H:%M:%SZ"
      set +e
      weather_run_with_timeout "$OBS_TIMEOUT_SEC" \
        "$PY" -u -m weather_data_feed_service \
        observations \
        --output "$OBS_OUTPUT" \
        --include-station-diff \
        --include-fallback-sources \
        --additional-cities "${OBSERVATION_ADDITIONAL_CITY_ARGS[@]}" \
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
      if [[ "$SOURCE_EVENTS_SHARD_ONLY" == "1" ]]; then
        source_events_args+=(--shard-only)
      fi
      weather_run_with_timeout "$SOURCE_EVENTS_TIMEOUT_SEC" \
        env WEATHER_DATA_FEED_SOURCE_EVENTS_OUTPUT_DIR="$SOURCE_EVENTS_OUTPUT" \
        "$PY" -u -m weather_data_feed_service \
          source-events -- \
          "${source_events_args[@]}"
      rc=$?
      set -e
      date -u +"[mac_data_feed] source_events_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
      next_source_events=$(( $(date +%s) + SOURCE_EVENTS_INTERVAL_SEC ))
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
          --partition-only \
          --max-workers 4
      else
        "$PY" -u -m weather_data_feed_service \
          runway-observations -- \
          --output-dir "$RUNWAY_OBSERVATIONS_OUTPUT" \
          --active-local-start-hour "$FAST_OBS_ACTIVE_LOCAL_START_HOUR" \
          --active-local-end-hour "$FAST_OBS_ACTIVE_LOCAL_END_HOUR" \
          --partition-only \
          --max-workers 4
      fi
      rc=$?
      set -e
      date -u +"[mac_data_feed] runway_observations_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"
      next_runway_observations=$(( $(date +%s) + RUNWAY_OBSERVATIONS_INTERVAL_SEC ))
    fi

    now="$(date +%s)"
    if [[ -z "$snapshot_supervisor_pid" ]] && (( now >= next_snapshot )); then
      date -u +"[mac_data_feed] snapshot_start_utc=%Y-%m-%dT%H:%M:%SZ"
      # This view only joins canonical on-disk weather and market-books data.
      # Network/proxy health belongs to the market-books owner; freshness is
      # enforced below by --orderbook-source-max-age-sec.
      weather_start_with_timeout_async "$SNAPSHOT_TIMEOUT_SEC" \
        env WEATHER_DATA_FEED_FORECAST_CURVE_ROOT="$FORECAST_CURVE_ROOT" \
        "$PY" -u -m weather_data_feed_service \
        --output-root "$OUTPUT_ROOT" \
        --cache-root "$CACHE_ROOT" \
        "$SNAPSHOT_COMMAND" -- \
        --observation-cache "$OBS_OUTPUT" \
        --observation-cache-max-age-sec 900 \
        --orderbook-source-latest "$MARKET_BOOKS_LATEST" \
        --orderbook-source-max-age-sec 420 \
        --orderbook-budget-sec "$SNAPSHOT_ORDERBOOK_BUDGET_SEC" \
        --orderbook-workers "$SNAPSHOT_ORDERBOOK_WORKERS"
      snapshot_supervisor_pid="$WEATHER_ASYNC_PID"
    fi

    sleep 10
  done
}
