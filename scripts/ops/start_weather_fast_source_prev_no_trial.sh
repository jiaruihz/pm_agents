#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
source "$PROJECT_DIR/scripts/ops/weather_market_proxy_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
# Reuse the data-feed tmux server so child processes inherit the macOS permission
# context that can read and write the external JRS runtime volume.
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
TMUX_SESSION="weather_fast_source_prev_no_trial"
TARGET_DATE="${WEATHER_FAST_PREV_NO_TARGET_DATE:-}"
INTERVAL_SEC="${WEATHER_FAST_PREV_NO_INTERVAL_SEC:-30}"
BURST_INTERVAL_SEC="${WEATHER_FAST_PREV_NO_BURST_INTERVAL_SEC:-5}"
SOURCES="${WEATHER_FAST_PREV_NO_SOURCES:-jma_amedas singapore_mss fmi amos_runway mgm ims_lod noaa_madis_hfmetar}"
LIVE_CITIES="${WEATHER_FAST_PREV_NO_LIVE_CITIES:-Helsinki Busan Tokyo Seoul}"
SHADOW_CITIES="${WEATHER_FAST_PREV_NO_SHADOW_CITIES:-Singapore TelAviv Ankara Istanbul Atlanta Miami SanFrancisco}"
MAX_SOURCE_AGE_MIN="${WEATHER_FAST_PREV_NO_MAX_SOURCE_AGE_MIN:-15}"
MAX_SOURCE_DETECT_AGE_MIN="${WEATHER_FAST_PREV_NO_MAX_SOURCE_DETECT_AGE_MIN:-5}"
MAX_SOURCE_OBSERVATION_LAG_MIN="${WEATHER_FAST_PREV_NO_MAX_SOURCE_OBSERVATION_LAG_MIN:-15}"
NEXT_METAR_WINDOW_MIN="${WEATHER_FAST_PREV_NO_NEXT_METAR_WINDOW_MIN:-20}"
BOOK_TIMEOUT_SEC="${WEATHER_FAST_PREV_NO_BOOK_TIMEOUT_SEC:-5}"
POST_ONLY_IMMEDIATE_REPRICES="${WEATHER_FAST_PREV_NO_POST_ONLY_IMMEDIATE_REPRICES:-${WEATHER_FAST_PREV_NO_FOK_IMMEDIATE_RETRIES:-2}}"
TAKER_SHARES="${WEATHER_FAST_PREV_NO_TAKER_SHARES:-10}"
MAKER_SHARES="${WEATHER_FAST_PREV_NO_MAKER_SHARES:-5}"
MIN_TAKER_SHARES="${WEATHER_FAST_PREV_NO_MIN_TAKER_SHARES:-5}"
DEPTH_HOT_RETRY_SEC="${WEATHER_FAST_PREV_NO_DEPTH_HOT_RETRY_SEC:-10}"
DEPTH_HOT_RETRY_FAST_INTERVAL_SEC="${WEATHER_FAST_PREV_NO_DEPTH_HOT_RETRY_FAST_INTERVAL_SEC:-0.5}"
DEPTH_HOT_RETRY_FAST_WINDOW_SEC="${WEATHER_FAST_PREV_NO_DEPTH_HOT_RETRY_FAST_WINDOW_SEC:-3}"
DEPTH_HOT_RETRY_SLOW_INTERVAL_SEC="${WEATHER_FAST_PREV_NO_DEPTH_HOT_RETRY_SLOW_INTERVAL_SEC:-1}"
OUTPUT_DIR="${WEATHER_FAST_PREV_NO_OUTPUT_DIR:-$RUNTIME_ROOT/output/fast_source_prev_no_trial}"
HIGH_FREQUENCY_LATEST="${WEATHER_FAST_PREV_NO_HIGH_FREQUENCY_LATEST:-$RUNTIME_ROOT/output/live_cross_observations/latest.json}"
SOURCE_NOTIFY_PATH="${WEATHER_FAST_PREV_NO_SOURCE_NOTIFY_PATH:-$RUNTIME_ROOT/loop/live_cross_observation_notify.json}"
FAST_LANES="${WEATHER_LIVE_CROSS_OBS_FAST_LANES:-amos_core=amos_runway:Busan,Seoul@5}"
ADDITIONAL_HIGH_FREQUENCY_LATESTS="${WEATHER_FAST_PREV_NO_ADDITIONAL_HIGH_FREQUENCY_LATESTS:-}"
ADDITIONAL_SOURCE_NOTIFY_PATHS="${WEATHER_FAST_PREV_NO_ADDITIONAL_SOURCE_NOTIFY_PATHS:-}"
SOURCE_EVENTS_JSONL="${WEATHER_FAST_PREV_NO_SOURCE_EVENTS_JSONL:-$RUNTIME_ROOT/output/source_events}"
NOTIFY_POLL_SEC="${WEATHER_FAST_PREV_NO_NOTIFY_POLL_SEC:-0.1}"
OPPORTUNITY_HEARTBEAT_SEC="${WEATHER_FAST_PREV_NO_OPPORTUNITY_HEARTBEAT_SEC:-300}"
MARKET_PROXY="$(weather_resolve_market_proxy "$PROJECT_DIR")"
ENABLE_LIVE="${WEATHER_FAST_PREV_NO_LIVE:-1}"
CONFIRM_LIVE="${WEATHER_FAST_PREV_NO_CONFIRM_LIVE:-1}"
ACKNOWLEDGE_HISTORICAL_SHARE_CAP_INCIDENTS="${WEATHER_FAST_PREV_NO_ACKNOWLEDGE_HISTORICAL_SHARE_CAP_INCIDENTS:-1}"
LOG_FILE="$RUNTIME_ROOT/loop/fast_source_prev_no_trial.log"
PID_FILE="$RUNTIME_ROOT/loop/fast_source_prev_no_trial.pid"

cmd=(
  "$PROJECT_DIR/.venv/bin/python"
  "$PROJECT_DIR/scripts/ops/weather_fast_source_prev_no_trial.py"
  --loop
  --output-dir "$OUTPUT_DIR"
  --high-frequency-latest "$HIGH_FREQUENCY_LATEST"
  --source-notify-path "$SOURCE_NOTIFY_PATH"
  --source-events-jsonl "$SOURCE_EVENTS_JSONL"
  --notify-poll-sec "$NOTIFY_POLL_SEC"
  --opportunity-heartbeat-sec "$OPPORTUNITY_HEARTBEAT_SEC"
  --interval-sec "$INTERVAL_SEC"
  --burst-interval-sec "$BURST_INTERVAL_SEC"
  --max-source-age-min "$MAX_SOURCE_AGE_MIN"
  --max-source-detect-age-min "$MAX_SOURCE_DETECT_AGE_MIN"
  --max-source-observation-lag-min "$MAX_SOURCE_OBSERVATION_LAG_MIN"
  --next-metar-window-min "$NEXT_METAR_WINDOW_MIN"
  --book-timeout-sec "$BOOK_TIMEOUT_SEC"
  --post-only-immediate-reprices "$POST_ONLY_IMMEDIATE_REPRICES"
  --taker-shares "$TAKER_SHARES"
  --maker-shares "$MAKER_SHARES"
  --min-taker-shares "$MIN_TAKER_SHARES"
  --depth-hot-retry-sec "$DEPTH_HOT_RETRY_SEC"
  --depth-hot-retry-fast-interval-sec "$DEPTH_HOT_RETRY_FAST_INTERVAL_SEC"
  --depth-hot-retry-fast-window-sec "$DEPTH_HOT_RETRY_FAST_WINDOW_SEC"
  --depth-hot-retry-slow-interval-sec "$DEPTH_HOT_RETRY_SLOW_INTERVAL_SEC"
  --sources
)
read -r -a source_args <<< "$SOURCES"
cmd+=("${source_args[@]}")
cmd+=(--live-cities)
read -r -a live_city_args <<< "$LIVE_CITIES"
cmd+=("${live_city_args[@]}")
cmd+=(--shadow-cities)
read -r -a shadow_city_args <<< "$SHADOW_CITIES"
cmd+=("${shadow_city_args[@]}")
if [[ -n "$FAST_LANES" ]]; then
  read -r -a fast_lane_args <<< "$FAST_LANES"
  for item in "${fast_lane_args[@]}"; do
    cmd+=(--observation-fast-lane "$item")
  done
fi
if [[ -n "$ADDITIONAL_HIGH_FREQUENCY_LATESTS" ]]; then
  read -r -a additional_latest_args <<< "$ADDITIONAL_HIGH_FREQUENCY_LATESTS"
  for item in "${additional_latest_args[@]}"; do
    cmd+=(--additional-high-frequency-latest "$item")
  done
fi
if [[ -n "$ADDITIONAL_SOURCE_NOTIFY_PATHS" ]]; then
  read -r -a additional_notify_args <<< "$ADDITIONAL_SOURCE_NOTIFY_PATHS"
  for item in "${additional_notify_args[@]}"; do
    cmd+=(--additional-source-notify-path "$item")
  done
fi
if [[ -n "$TARGET_DATE" ]]; then
  cmd+=(--target-date "$TARGET_DATE")
fi
if [[ -n "$MARKET_PROXY" ]]; then
  cmd+=(--market-proxy "$MARKET_PROXY")
fi
if [[ "$ENABLE_LIVE" == "1" ]]; then
  cmd+=(--live)
fi
if [[ "$CONFIRM_LIVE" == "1" ]]; then
  cmd+=(--confirm-live)
fi
if [[ "$ACKNOWLEDGE_HISTORICAL_SHARE_CAP_INCIDENTS" == "1" ]]; then
  cmd+=(--acknowledge-historical-share-cap-incidents)
fi

printf -v quoted_cmd '%q ' "${cmd[@]}"
printf -v session_cmd \
  'set -eu; mkdir -p %q %q; cd %q; set -a; [[ -f .env ]] && source .env || true; set +a; export PYTHONPATH=%q; exec %s >> %q 2>&1' \
  "$RUNTIME_ROOT/loop" "$OUTPUT_DIR" "$PROJECT_DIR" "$PROJECT_DIR" "$quoted_cmd" "$LOG_FILE"
weather_jrs_tmux_guarded_replace_session "$TMUX_SOCKET" "$TMUX_SESSION" "$session_cmd"
printf -v marker_command \
  "printf 'tmux:%%s\\n' %q > %q" \
  "$TMUX_SESSION" "$PID_FILE"
weather_jrs_tmux_exec_checked "$TMUX_SOCKET" "fast_source_pid_marker" "$marker_command"

echo "started fast_source_prev_no_trial tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
echo "target_date=${TARGET_DATE:-auto_today}"
echo "interval_sec=$INTERVAL_SEC"
echo "burst_interval_sec=$BURST_INTERVAL_SEC"
echo "sources=$SOURCES"
echo "live_cities=$LIVE_CITIES"
echo "shadow_cities=$SHADOW_CITIES"
echo "city_trade_policy=scripts/ops/weather_fast_source_city_policy.py"
echo "max_source_age_min=$MAX_SOURCE_AGE_MIN"
echo "max_source_detect_age_min=$MAX_SOURCE_DETECT_AGE_MIN"
echo "max_source_observation_lag_min=$MAX_SOURCE_OBSERVATION_LAG_MIN"
echo "next_metar_window_min=$NEXT_METAR_WINDOW_MIN"
echo "post_only_immediate_reprices=$POST_ONLY_IMMEDIATE_REPRICES"
echo "configured_execution_budget=taker_${TAKER_SHARES}_shares+maker_${MAKER_SHARES}_shares_with_city_cap"
echo "execution_profile=fast_source_depth_adaptive_taker_maker_v1 (Seoul policy override: T-10 taker-only)"
echo "min_taker_shares=$MIN_TAKER_SHARES"
echo "depth_hot_retry=${DEPTH_HOT_RETRY_FAST_INTERVAL_SEC}s_for_${DEPTH_HOT_RETRY_FAST_WINDOW_SEC}s_then_${DEPTH_HOT_RETRY_SLOW_INTERVAL_SEC}s_until_${DEPTH_HOT_RETRY_SEC}s"
echo "output_dir=$OUTPUT_DIR"
echo "high_frequency_latest=$HIGH_FREQUENCY_LATEST"
echo "source_notify_path=$SOURCE_NOTIFY_PATH"
echo "observation_fast_lanes=$FAST_LANES"
echo "additional_high_frequency_latests=$ADDITIONAL_HIGH_FREQUENCY_LATESTS"
echo "additional_source_notify_paths=$ADDITIONAL_SOURCE_NOTIFY_PATHS"
echo "source_events_jsonl=$SOURCE_EVENTS_JSONL"
echo "notify_poll_sec=$NOTIFY_POLL_SEC"
echo "opportunity_heartbeat_sec=$OPPORTUNITY_HEARTBEAT_SEC"
echo "log=$LOG_FILE"
echo "pid_file=$PID_FILE"
echo "live=$ENABLE_LIVE confirm_live=$CONFIRM_LIVE"
echo "acknowledge_historical_share_cap_incidents=$ACKNOWLEDGE_HISTORICAL_SHARE_CAP_INCIDENTS"
echo "market_proxy=$([[ -n "$MARKET_PROXY" ]] && echo configured || echo direct)"
