#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

stop_instance() {
  local runtime_dir="$1"
  THETA_CURRENT_YES_RUNTIME_DIR="$runtime_dir" \
    scripts/ops/stop_weather_theta_current_yes_tiny_live.sh
}

stop_instance "runtime/weather_edge_v1/theta_current_yes_fade_confirmed_tiny_live_v1"
stop_instance "runtime/weather_edge_v1/theta_current_yes_peak_forming_micro_tiny_live_v1"
stop_instance "runtime/weather_edge_v1/theta_current_yes_tiny_live_v1"
