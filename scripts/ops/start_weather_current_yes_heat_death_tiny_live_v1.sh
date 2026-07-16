#!/usr/bin/env bash
# Start the heat-death tiny-live probe as two separately attributed heads
# (preregistration: docs/analysis/2026-07/2026-07-15-heat-death-live-promotion-preregistration-v1.md):
#   h1_late_carry        ask [0.95, 0.99]
#   h2_early_dislocation ask [0.50, 0.93]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"

start_head() {
  local head="$1"
  local total_shares="$2"
  local taker_shares="$3"
  local maker_shares="$4"
  local instance="current_yes_heat_death_tiny_live_${head}_v1"
  local output_dir="$ROOT/runtime/weather_edge_v1/$instance"
  local session="weather_$instance"
  local log_file="$output_dir/runner.log"

  mkdir -p "$output_dir"
  if pgrep -f "weather_current_yes_heat_death_tiny_live_v1.py loop --head $head" >/dev/null 2>&1; then
    echo "$instance already running"
    return 0
  fi

  screen -dmS "$session" sh -c \
    "cd '$ROOT' && exec '$PY' -u scripts/ops/weather_current_yes_heat_death_tiny_live_v1.py loop \
      --head $head \
      --output-dir '$output_dir' \
      --fixed-order-shares '$total_shares' \
      --taker-order-shares '$taker_shares' \
      --maker-order-shares '$maker_shares' \
      --max-orders-per-utc-day 3 \
      --max-snapshot-age-min 20 \
      --order-ttl-min 15 \
      --maker-chase-refresh-sec 30 \
      --maker-chase-window-min 3 \
      --interval-seconds 30 \
      --live --confirm-live >> '$log_file' 2>&1"

  echo "started $session output=$output_dir log=$log_file"
}

start_head h1_late_carry 10 5 5
start_head h2_early_dislocation 5 5 0
