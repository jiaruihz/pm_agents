#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
VOLUME="${WEATHER_JRS_VOLUME:-/Volumes/jrs}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$VOLUME/weather_data_feed_service_runtime}"
TMUX_SOCKET="${WEATHER_DATA_FEED_TMUX_SOCKET:-weather-jrs}"
TMUX_SESSION="${WEATHER_DATA_FEED_TMUX_SESSION:-weather_data_feed_jrs}"
LOG_FILE="$RUNTIME_ROOT/loop/data_feed_tmux.log"
FORECAST_ENRICHMENT_ENABLED="${WEATHER_DATA_FEED_FORECAST_ENRICHMENT_ENABLED:-1}"
FORECAST_ENRICHMENT_INTERVAL_SEC="${WEATHER_DATA_FEED_FORECAST_ENRICHMENT_INTERVAL_SEC:-1800}"

if [[ ! -d "$VOLUME" ]]; then
  echo "missing mounted volume: $VOLUME" >&2
  exit 1
fi

mkdir -p "$RUNTIME_ROOT/loop"
probe="$RUNTIME_ROOT/loop/.tmux_write_probe"
printf 'probe %s\n' "$(date -Iseconds)" > "$probe"
rm -f "$probe"

tmux -L "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
rm -f "$RUNTIME_ROOT/loop/data_feed_loop.pid"
tmux -L "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && export WEATHER_DATA_FEED_RUNTIME_ROOT='$RUNTIME_ROOT' && export WEATHER_DATA_FEED_FORECAST_ENRICHMENT_ENABLED='$FORECAST_ENRICHMENT_ENABLED' && export WEATHER_DATA_FEED_FORECAST_ENRICHMENT_INTERVAL_SEC='$FORECAST_ENRICHMENT_INTERVAL_SEC' && export MAC_WEATHER_DATA_FEED_LOOP_CHILD=1 && exec '$PROJECT_DIR/scripts/ops/start_mac_weather_data_feed_loop.sh' >> '$LOG_FILE' 2>&1"

echo "started $TMUX_SESSION on tmux socket $TMUX_SOCKET"
echo "runtime=$RUNTIME_ROOT"
echo "log=$LOG_FILE"
echo "forecast_enrichment_enabled=$FORECAST_ENRICHMENT_ENABLED"
echo "forecast_enrichment_interval_sec=$FORECAST_ENRICHMENT_INTERVAL_SEC"
