#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
SESSION="weather_canonical_refresh"
JOB_DIR="$PROJECT_DIR/runtime/weather_edge_v1/canonical_refresh"
printf -v JOB_COMMAND \
  'cd %q; export WEATHER_DATA_FEED_RUNTIME_ROOT=%q; %q' \
  "$PROJECT_DIR" "$RUNTIME_ROOT" \
  "$PROJECT_DIR/scripts/ops/run_weather_canonical_refresh_launchd.sh"

weather_jrs_tmux_run_oneshot \
  "$RUNTIME_ROOT" \
  "$SESSION" \
  "$JOB_DIR" \
  "$JOB_COMMAND"
