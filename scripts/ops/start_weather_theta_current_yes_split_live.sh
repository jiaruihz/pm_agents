#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

RESTART_CURRENT_YES_SPLIT="${RESTART_CURRENT_YES_SPLIT:-1}"
STOP_SHARED_CURRENT_YES_INSTANCE="${STOP_SHARED_CURRENT_YES_INSTANCE:-1}"

if [[ "$STOP_SHARED_CURRENT_YES_INSTANCE" == "1" ]]; then
  THETA_CURRENT_YES_RUNTIME_DIR="runtime/weather_edge_v1/theta_current_yes_tiny_live_v1" \
  KILL_ALL_CURRENT_YES_LOOPS="1" \
    scripts/ops/stop_weather_theta_current_yes_tiny_live.sh >/dev/null || true
fi

start_profile() {
  local instance="$1"
  local runtime_dir="$2"
  local profile_mode="$3"
  local enable_peak="$4"

  if [[ "$RESTART_CURRENT_YES_SPLIT" == "1" ]]; then
    THETA_CURRENT_YES_RUNTIME_DIR="$runtime_dir" \
      scripts/ops/stop_weather_theta_current_yes_tiny_live.sh >/dev/null || true
  fi

  env \
    THETA_CURRENT_YES_STRATEGY_INSTANCE="$instance" \
    THETA_CURRENT_YES_RUNTIME_DIR="$runtime_dir" \
    THETA_CURRENT_YES_ENTRY_PROFILE_MODE="$profile_mode" \
    ENABLE_PEAK_FORMING_LIVE="$enable_peak" \
    MAX_ORDER_NOTIONAL="${MAX_ORDER_NOTIONAL:-3}" \
    MAX_CITY_DAY_NOTIONAL="${MAX_CITY_DAY_NOTIONAL:-3}" \
    scripts/ops/start_weather_theta_current_yes_tiny_live.sh
}

start_profile \
  "theta_current_yes_fade_confirmed_tiny_live_v1" \
  "runtime/weather_edge_v1/theta_current_yes_fade_confirmed_tiny_live_v1" \
  "fade_confirmed" \
  "0"

start_profile \
  "theta_current_yes_peak_forming_micro_tiny_live_v1" \
  "runtime/weather_edge_v1/theta_current_yes_peak_forming_micro_tiny_live_v1" \
  "peak_forming_micro" \
  "1"
