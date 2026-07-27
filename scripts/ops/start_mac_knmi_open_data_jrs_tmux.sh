#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

VOLUME="${WEATHER_JRS_VOLUME:-/Volumes/jrs}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$VOLUME/weather_data_feed_service_runtime}"
SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:-$PROJECT_DIR}"
ENV_FILE="${KNMI_ENV_FILE:-$SERVICE_DIR/.env.knmi}"
OUTPUT_DIR="${KNMI_OPEN_DATA_OUTPUT_DIR:-$RUNTIME_ROOT/output/knmi_open_data}"
LOG_FILE="${KNMI_OPEN_DATA_LOG_FILE:-$RUNTIME_ROOT/loop/knmi_open_data.log}"
HOT_INTERVAL_SEC="${KNMI_OPEN_DATA_HOT_INTERVAL_SEC:-10}"
COLD_INTERVAL_SEC="${KNMI_OPEN_DATA_COLD_INTERVAL_SEC:-300}"
HOT_WINDOW_START_SEC="${KNMI_OPEN_DATA_HOT_WINDOW_START_SEC:-205}"
HOT_WINDOW_END_SEC="${KNMI_OPEN_DATA_HOT_WINDOW_END_SEC:-260}"
TIMEOUT_SEC="${KNMI_OPEN_DATA_TIMEOUT_SEC:-15}"
TMUX_SESSION="${KNMI_OPEN_DATA_TMUX_SESSION:-weather_knmi_open_data_jrs}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"

if [[ ! -d "$VOLUME" ]]; then
  echo "missing mounted volume: $VOLUME" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing KNMI env file: $ENV_FILE" >&2
  exit 1
fi

weather_jrs_tmux "$TMUX_SOCKET" run-shell \
  "mkdir -p '$OUTPUT_DIR' '$RUNTIME_ROOT/loop' && probe='$OUTPUT_DIR/.write_probe' && : > \"\$probe\" && rm \"\$probe\""

weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$SERVICE_DIR' && set -a && source '$ENV_FILE' && set +a && exec '$SERVICE_DIR/.venv/bin/python' -u -m weather_data_feed_service.knmi_open_data --output-dir '$OUTPUT_DIR' --hot-interval-sec '$HOT_INTERVAL_SEC' --cold-interval-sec '$COLD_INTERVAL_SEC' --hot-window-start-sec '$HOT_WINDOW_START_SEC' --hot-window-end-sec '$HOT_WINDOW_END_SEC' --timeout-sec '$TIMEOUT_SEC' >> '$LOG_FILE' 2>&1"

sleep 1
if ! weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; then
  echo "failed to start $TMUX_SESSION; inspect $LOG_FILE" >&2
  exit 1
fi

echo "started $TMUX_SESSION on tmux socket $TMUX_SOCKET"
echo "service_dir=$SERVICE_DIR"
echo "output_dir=$OUTPUT_DIR"
echo "log=$LOG_FILE"
echo "hot_interval_sec=$HOT_INTERVAL_SEC"
echo "cold_interval_sec=$COLD_INTERVAL_SEC"
echo "hot_window_sec=$HOT_WINDOW_START_SEC..$HOT_WINDOW_END_SEC"
