#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
exec "$PROJECT_DIR/scripts/ops/start_weather_live_runtime_patrol_tmux.sh"
