#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
exec "$PROJECT_DIR/scripts/ops/stop_mac_weather_data_feed_jrs_tmux.sh"
