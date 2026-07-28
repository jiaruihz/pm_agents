#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
VOLUME="${WEATHER_JRS_VOLUME:-/Volumes/jrs}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$VOLUME/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
TMUX_SESSION="${WEATHER_FAST_OBS_TMUX_SESSION:-weather_fast_obs_jrs}"
LOG_FILE="$RUNTIME_ROOT/loop/fast_observations_tmux.log"
SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:-$PROJECT_DIR}"
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

if [[ ! -d "$VOLUME" ]]; then
  echo "missing mounted volume: $VOLUME" >&2
  exit 1
fi

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop"

weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && export WEATHER_DATA_FEED_SERVICE_DIR='$SERVICE_DIR' && export WEATHER_DATA_FEED_RUNTIME_ROOT='$RUNTIME_ROOT' && export WEATHER_FAST_OBS_INTERVAL_SEC='$INTERVAL_SEC' && export WEATHER_FAST_OBS_RUNWAY_SOURCES='$RUNWAY_SOURCES' && export WEATHER_FAST_OBS_HIGH_FREQUENCY_SOURCES='$HIGH_FREQUENCY_SOURCES' && export WEATHER_FAST_OBS_HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC='$HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC' && export WEATHER_FAST_OBS_HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC='$HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC' && export WEATHER_FAST_OBS_MAX_WORKERS='$MAX_WORKERS' && export WEATHER_FAST_OBS_TIMEOUT_SEC='$TIMEOUT_SEC' && export WEATHER_FAST_OBS_ACTIVE_LOCAL_START_HOUR='$ACTIVE_LOCAL_START_HOUR' && export WEATHER_FAST_OBS_ACTIVE_LOCAL_END_HOUR='$ACTIVE_LOCAL_END_HOUR' && export WEATHER_FAST_OBS_ALWAYS_ACTIVE_CITIES='$ALWAYS_ACTIVE_CITIES' && exec '$PROJECT_DIR/scripts/ops/weather_fast_observations_loop.sh' >> '$LOG_FILE' 2>&1"

sleep 1
if ! weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; then
  echo "failed to start $TMUX_SESSION; inspect $LOG_FILE" >&2
  exit 1
fi

echo "started $TMUX_SESSION on tmux socket $TMUX_SOCKET"
echo "runtime=$RUNTIME_ROOT"
echo "service_dir=$SERVICE_DIR"
echo "log=$LOG_FILE"
echo "interval_sec=$INTERVAL_SEC"
echo "runway_sources=$RUNWAY_SOURCES"
echo "high_frequency_sources=$HIGH_FREQUENCY_SOURCES"
echo "high_frequency_source_min_interval_sec=$HIGH_FREQUENCY_SOURCE_MIN_INTERVAL_SEC"
echo "high_frequency_source_minute_window_min_interval_sec=$HIGH_FREQUENCY_SOURCE_MINUTE_WINDOW_MIN_INTERVAL_SEC"
echo "max_workers=$MAX_WORKERS"
echo "timeout_sec=$TIMEOUT_SEC"
echo "active_local_start_hour=$ACTIVE_LOCAL_START_HOUR"
echo "active_local_end_hour=$ACTIVE_LOCAL_END_HOUR"
echo "always_active_cities=$ALWAYS_ACTIVE_CITIES"
