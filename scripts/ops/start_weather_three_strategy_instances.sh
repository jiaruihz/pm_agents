#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
cd "$PROJECT_DIR"

# Production strategy instances:
# 1) Legacy global 25-75 mid-price chain on the current expanded T1 city pool.
# 2) Per-side entry-band chain on the original core city pool.
# 3) mid_price_core_v2 branch on the original core city pool.
#
# The default legacy core list keeps only old-pool cities that were not later
# explicitly removed or side-gated. Override WEATHER_LEGACY_CORE_CITIES to adjust
# without changing code.
LEGACY_CORE_CITIES="${WEATHER_LEGACY_CORE_CITIES:-Boston,LA,London,Miami,NYC,Phoenix,Shanghai,Tokyo,Warsaw}"

WEATHER_LIVE_STRATEGY_INSTANCE=mid_price_core_v1_25_75 \
WEATHER_LIVE_EXECUTION_POLICY=mid_price_core_v1 \
WEATHER_LIVE_ALLOWED_CITIES="" \
WEATHER_LIVE_MIN_ENTRY_PRICE=0.25 \
WEATHER_LIVE_MAX_ENTRY_PRICE=0.75 \
WEATHER_LIVE_MIN_EDGE=0.10 \
WEATHER_LIVE_YES_MIN_ENTRY_PRICE=0.25 \
WEATHER_LIVE_YES_MAX_ENTRY_PRICE=0.75 \
WEATHER_LIVE_YES_MIN_EDGE=0.10 \
WEATHER_LIVE_NO_MIN_ENTRY_PRICE=0.25 \
WEATHER_LIVE_NO_MAX_ENTRY_PRICE=0.75 \
WEATHER_LIVE_NO_MIN_EDGE=0.10 \
INITIAL_DELAY_SEC="${MID_PRICE_CORE_V1_25_75_INITIAL_DELAY_SEC:-0}" \
scripts/ops/start_weather_live_cycle_loop.sh

WEATHER_LIVE_STRATEGY_INSTANCE=mid_price_core_v1_side_band \
WEATHER_LIVE_EXECUTION_POLICY=mid_price_core_v1 \
WEATHER_LIVE_ALLOWED_CITIES="$LEGACY_CORE_CITIES" \
WEATHER_LIVE_MIN_ENTRY_PRICE=0.25 \
WEATHER_LIVE_MAX_ENTRY_PRICE=0.75 \
WEATHER_LIVE_MIN_EDGE=0.10 \
WEATHER_LIVE_YES_MIN_ENTRY_PRICE=0.20 \
WEATHER_LIVE_YES_MAX_ENTRY_PRICE=0.45 \
WEATHER_LIVE_YES_MIN_EDGE=0.20 \
WEATHER_LIVE_NO_MIN_ENTRY_PRICE=0.35 \
WEATHER_LIVE_NO_MAX_ENTRY_PRICE=0.65 \
WEATHER_LIVE_NO_MIN_EDGE=0.10 \
INITIAL_DELAY_SEC="${MID_PRICE_CORE_V1_SIDE_BAND_INITIAL_DELAY_SEC:-60}" \
scripts/ops/start_weather_live_cycle_loop.sh

WEATHER_BRANCH_STRATEGY_INSTANCE=mid_price_core_v2_25_75 \
WEATHER_BRANCH_EXECUTION_POLICY=mid_price_core_v2 \
WEATHER_BRANCH_ALLOWED_CITIES="$LEGACY_CORE_CITIES" \
WEATHER_BRANCH_SOURCE_POLICY=mid_price_core_v1 \
WEATHER_BRANCH_SOURCE_STRATEGY_INSTANCE=mid_price_core_v1_25_75 \
INITIAL_DELAY_SEC="${MID_PRICE_CORE_V2_25_75_INITIAL_DELAY_SEC:-120}" \
scripts/ops/start_weather_policy_branch_loop.sh
