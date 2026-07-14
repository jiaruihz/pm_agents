#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:-$HOME/projects/pm_agents}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
RUNWAY_OUTPUT="${WEATHER_FAST_OBS_RUNWAY_OUTPUT_DIR:-$RUNTIME_ROOT/output/runway_observations}"
HIGH_FREQUENCY_OUTPUT="${WEATHER_FAST_OBS_HIGH_FREQUENCY_OUTPUT_DIR:-$RUNTIME_ROOT/output/high_frequency_observations}"
LOOP_DIR="${WEATHER_FAST_OBS_LOOP_DIR:-$RUNTIME_ROOT/loop}"
PID_FILE="$LOOP_DIR/fast_observations_loop.pid"
INTERVAL_SEC="${WEATHER_FAST_OBS_INTERVAL_SEC:-20}"
RUNWAY_SOURCES="${WEATHER_FAST_OBS_RUNWAY_SOURCES:-amos}"
HIGH_FREQUENCY_SOURCES="${WEATHER_FAST_OBS_HIGH_FREQUENCY_SOURCES:-amos_runway noaa_madis_hfmetar singapore_mss jma_amedas hko_obs cowin_obs fmi mgm ims_lod}"
HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC="${WEATHER_FAST_OBS_HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC:-jma_amedas=300 noaa_madis_hfmetar=300 fmi=60 mgm=300 ims_lod=300}"
HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC="${WEATHER_FAST_OBS_HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC:-jma_amedas=5-8,15-18,25-28,35-38,45-48,55-58:20}"
MAX_WORKERS="${WEATHER_FAST_OBS_MAX_WORKERS:-8}"
TIMEOUT_SEC="${WEATHER_FAST_OBS_TIMEOUT_SEC:-5}"
ACTIVE_LOCAL_START_HOUR="${WEATHER_FAST_OBS_ACTIVE_LOCAL_START_HOUR:-6}"
ACTIVE_LOCAL_END_HOUR="${WEATHER_FAST_OBS_ACTIVE_LOCAL_END_HOUR:-22}"
ALWAYS_ACTIVE_CITIES="${WEATHER_FAST_OBS_ALWAYS_ACTIVE_CITIES:-Seoul Tokyo}"
PY="$SERVICE_DIR/.venv/bin/python"

mkdir -p "$LOOP_DIR" "$RUNWAY_OUTPUT" "$HIGH_FREQUENCY_OUTPUT"

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

echo "$$" > "$PID_FILE"
date -u +"[fast_obs] loop_start_utc=%Y-%m-%dT%H:%M:%SZ pid=$$ interval_sec=$INTERVAL_SEC runway_sources=$RUNWAY_SOURCES high_frequency_sources=$HIGH_FREQUENCY_SOURCES high_frequency_source_min_interval_sec=$HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC high_frequency_source_minute_window_min_interval_sec=$HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC active_local_start_hour=$ACTIVE_LOCAL_START_HOUR active_local_end_hour=$ACTIVE_LOCAL_END_HOUR always_active_cities=$ALWAYS_ACTIVE_CITIES"

cd "$SERVICE_DIR"
trap 'rc=$?; date -u +"[fast_obs] loop_exit_utc=%Y-%m-%dT%H:%M:%SZ returncode=$rc"; rm -f "$PID_FILE"' EXIT

while true; do
  started_epoch="$(date +%s)"
  date -u +"[fast_obs] runway_start_utc=%Y-%m-%dT%H:%M:%SZ"
  set +e
  read -r -a runway_source_args <<< "$RUNWAY_SOURCES"
  "$PY" -u -m weather_data_feed_service \
    runway-observations -- \
    --output-dir "$RUNWAY_OUTPUT" \
    --sources "${runway_source_args[@]}" \
    --active-local-start-hour "$ACTIVE_LOCAL_START_HOUR" \
    --active-local-end-hour "$ACTIVE_LOCAL_END_HOUR" \
    --max-workers "$MAX_WORKERS"
  runway_rc=$?
  set -e
  date -u +"[fast_obs] runway_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$runway_rc"

  date -u +"[fast_obs] high_frequency_start_utc=%Y-%m-%dT%H:%M:%SZ"
  set +e
  read -r -a high_frequency_source_args <<< "$HIGH_FREQUENCY_SOURCES"
  read -r -a always_active_city_args <<< "$ALWAYS_ACTIVE_CITIES"
  read -r -a high_frequency_interval_args <<< "$HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC"
  high_frequency_interval_cli=()
  for interval_arg in "${high_frequency_interval_args[@]}"; do
    high_frequency_interval_cli+=(--source-min-interval-sec "$interval_arg")
  done
  read -r -a high_frequency_window_interval_args <<< "$HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC"
  for interval_arg in "${high_frequency_window_interval_args[@]}"; do
    high_frequency_interval_cli+=(--source-minute-window-min-interval-sec "$interval_arg")
  done
  "$PY" -u -m weather_data_feed_service \
    high-frequency-observations -- \
    --output-dir "$HIGH_FREQUENCY_OUTPUT" \
    --sources "${high_frequency_source_args[@]}" \
    --active-local-start-hour "$ACTIVE_LOCAL_START_HOUR" \
    --active-local-end-hour "$ACTIVE_LOCAL_END_HOUR" \
    --always-active-cities "${always_active_city_args[@]}" \
    "${high_frequency_interval_cli[@]}" \
    --max-workers "$MAX_WORKERS" \
    --timeout-sec "$TIMEOUT_SEC"
  high_frequency_rc=$?
  set -e
  date -u +"[fast_obs] high_frequency_done_utc=%Y-%m-%dT%H:%M:%SZ returncode=$high_frequency_rc"

  elapsed=$(( $(date +%s) - started_epoch ))
  sleep_for=$(( INTERVAL_SEC - elapsed ))
  if (( sleep_for < 5 )); then
    sleep_for=5
  fi
  sleep "$sleep_for"
done
