#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_socket "${WEATHER_DATA_FEED_TMUX_SOCKET:-}")"
TMUX_SESSION="${WEATHER_DATA_FEED_TMUX_SESSION:-weather_data_feed_jrs}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"

tmux -L "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
rm -f "$RUNTIME_ROOT/loop/data_feed_loop.pid"
echo "stopped $TMUX_SESSION on tmux socket $TMUX_SOCKET"
