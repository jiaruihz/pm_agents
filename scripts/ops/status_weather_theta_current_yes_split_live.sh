#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

status_instance() {
  local label="$1"
  local runtime_dir="$2"
  echo "== $label =="
  THETA_CURRENT_YES_RUNTIME_DIR="$runtime_dir" \
    scripts/ops/status_weather_theta_current_yes_tiny_live.sh
}

status_instance \
  "theta_current_yes_fade_confirmed_tiny_live_v1" \
  "runtime/weather_edge_v1/theta_current_yes_fade_confirmed_tiny_live_v1"

status_instance \
  "theta_current_yes_peak_forming_micro_tiny_live_v1" \
  "runtime/weather_edge_v1/theta_current_yes_peak_forming_micro_tiny_live_v1"

status_instance \
  "legacy theta_current_yes_tiny_live_v1" \
  "runtime/weather_edge_v1/theta_current_yes_tiny_live_v1"
