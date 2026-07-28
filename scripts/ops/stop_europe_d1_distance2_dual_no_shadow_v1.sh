#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="${EUROPE_D1_DISTANCE2_TMUX_SESSION:-europe_d1_distance2_dual_no_shadow_v1}"

if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; then
  weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION"
  echo "stopped $TMUX_SESSION"
else
  echo "not running $TMUX_SESSION"
fi
