#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

RUNTIME_DIR="${THETA_CURRENT_YES_RUNTIME_DIR:-runtime/weather_edge_v1/theta_current_yes_tiny_live_v1}"
PID_FILE="$RUNTIME_DIR/loop.pid"
LOG_FILE="$RUNTIME_DIR/loop.log"
mkdir -p "$RUNTIME_DIR" "runtime/weather_edge_v1/live"

if [[ -s "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE")"
  if kill -0 "$old_pid" 2>/dev/null; then
    echo "already_running pid=$old_pid"
    exit 0
  fi
fi

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
THETA_CURRENT_YES_MODE="${THETA_CURRENT_YES_MODE:-live}"
MAX_ORDER_NOTIONAL="${MAX_ORDER_NOTIONAL:-5}"
MAX_CITY_DAY_NOTIONAL="${MAX_CITY_DAY_NOTIONAL:-5}"
MIN_AVAILABLE_NOTIONAL="${MIN_AVAILABLE_NOTIONAL:-5}"
MAX_TAKER_CUSHION="${MAX_TAKER_CUSHION:-0.02}"
CROSS_TICK_BUFFER="${CROSS_TICK_BUFFER:-0.001}"
MAX_ORDERS="${MAX_ORDERS:-20}"
MAX_SNAPSHOT_AGE_MIN="${MAX_SNAPSHOT_AGE_MIN:-45}"
MAX_OBS_AGE_MIN="${MAX_OBS_AGE_MIN:-20}"
PRE_METAR_UPDATE_BLACKOUT_MIN="${PRE_METAR_UPDATE_BLACKOUT_MIN:-6}"
MIN_GAP_TO_NEXT_BRACKET_C="${MIN_GAP_TO_NEXT_BRACKET_C:-0}"
MIN_FORECAST_PEAK_DELTA_HOURS="${MIN_FORECAST_PEAK_DELTA_HOURS:--1.999}"
ALLOW_MISSING_FORECAST_PEAK="${ALLOW_MISSING_FORECAST_PEAK:-0}"
THETA_CURRENT_YES_ENTRY_PROFILE_MODE="${THETA_CURRENT_YES_ENTRY_PROFILE_MODE:-both}"
FADE_CONFIRMED_MODEL_MODE="${FADE_CONFIRMED_MODEL_MODE:-base}"
FADE_CONFIRMED_MODEL_ARTIFACT="${FADE_CONFIRMED_MODEL_ARTIFACT:-docs/analysis/2026-06/generated/theta_current_yes_fade_confirmed_model_v1/fade_confirmed_model.json}"
ENABLE_PEAK_FORMING_LIVE="${ENABLE_PEAK_FORMING_LIVE:-1}"
PEAK_FORMING_MAX_DECLINE_C="${PEAK_FORMING_MAX_DECLINE_C:-0.25}"
PEAK_FORMING_MIN_ASK="${PEAK_FORMING_MIN_ASK:-0.50}"
PEAK_FORMING_MAX_ASK="${PEAK_FORMING_MAX_ASK:-0.97}"
PEAK_FORMING_MIN_P="${PEAK_FORMING_MIN_P:-0.60}"
PEAK_FORMING_MIN_EDGE="${PEAK_FORMING_MIN_EDGE:-0.02}"
PEAK_FORMING_MIN_FORECAST_DELTA_HOURS="${PEAK_FORMING_MIN_FORECAST_DELTA_HOURS:--1.0}"
PEAK_FORMING_METAR_VETO="${PEAK_FORMING_METAR_VETO:-1}"
PEAK_FORMING_MIN_MINUTES_SINCE_RUNNING_MAX="${PEAK_FORMING_MIN_MINUTES_SINCE_RUNNING_MAX:-10}"
PEAK_FORMING_FORECAST_BUST_MARGIN_C="${PEAK_FORMING_FORECAST_BUST_MARGIN_C:-0.1}"
PEAK_FORMING_CLOUD_CLEARING_MIN_DROP="${PEAK_FORMING_CLOUD_CLEARING_MIN_DROP:-2}"
PEAK_FORMING_WARMING_TREND_MIN_D_TMPF_3H="${PEAK_FORMING_WARMING_TREND_MIN_D_TMPF_3H:-1.5}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-900}"
NO_TELEGRAM="${NO_TELEGRAM:-0}"

if [[ "$THETA_CURRENT_YES_MODE" != "live" && "$THETA_CURRENT_YES_MODE" != "telemetry" ]]; then
  echo "invalid THETA_CURRENT_YES_MODE=$THETA_CURRENT_YES_MODE (expected live or telemetry)" >&2
  exit 2
fi

args=(
  scripts/ops/weather_theta_current_yes_tiny_live.py
  loop
  --max-order-notional "$MAX_ORDER_NOTIONAL"
  --max-city-day-notional "$MAX_CITY_DAY_NOTIONAL"
  --min-available-notional "$MIN_AVAILABLE_NOTIONAL"
  --max-taker-cushion "$MAX_TAKER_CUSHION"
  --cross-tick-buffer "$CROSS_TICK_BUFFER"
  --max-orders "$MAX_ORDERS"
  --max-snapshot-age-min "$MAX_SNAPSHOT_AGE_MIN"
  --max-obs-age-min "$MAX_OBS_AGE_MIN"
  --pre-metar-update-blackout-min "$PRE_METAR_UPDATE_BLACKOUT_MIN"
  --min-gap-to-next-bracket-c "$MIN_GAP_TO_NEXT_BRACKET_C"
  --min-forecast-peak-delta-hours "$MIN_FORECAST_PEAK_DELTA_HOURS"
  --entry-profile-mode "$THETA_CURRENT_YES_ENTRY_PROFILE_MODE"
  --fade-confirmed-model-mode "$FADE_CONFIRMED_MODEL_MODE"
  --fade-confirmed-model-artifact "$FADE_CONFIRMED_MODEL_ARTIFACT"
  --interval-seconds "$INTERVAL_SECONDS"
)

if [[ "$ALLOW_MISSING_FORECAST_PEAK" == "1" ]]; then
  args+=(--allow-missing-forecast-peak)
fi

if [[ "$ENABLE_PEAK_FORMING_LIVE" == "1" ]]; then
  args+=(
    --enable-peak-forming-live
    --peak-forming-max-decline-c "$PEAK_FORMING_MAX_DECLINE_C"
    --peak-forming-min-ask "$PEAK_FORMING_MIN_ASK"
    --peak-forming-max-ask "$PEAK_FORMING_MAX_ASK"
    --peak-forming-min-p "$PEAK_FORMING_MIN_P"
    --peak-forming-min-edge "$PEAK_FORMING_MIN_EDGE"
    --peak-forming-min-forecast-delta-hours "$PEAK_FORMING_MIN_FORECAST_DELTA_HOURS"
    --peak-forming-min-minutes-since-running-max "$PEAK_FORMING_MIN_MINUTES_SINCE_RUNNING_MAX"
    --peak-forming-forecast-bust-margin-c "$PEAK_FORMING_FORECAST_BUST_MARGIN_C"
    --peak-forming-cloud-clearing-min-drop "$PEAK_FORMING_CLOUD_CLEARING_MIN_DROP"
    --peak-forming-warming-trend-min-d-tmpf-3h "$PEAK_FORMING_WARMING_TREND_MIN_D_TMPF_3H"
  )
  if [[ "$PEAK_FORMING_METAR_VETO" != "1" ]]; then
    args+=(--disable-peak-forming-metar-veto)
  fi
fi

if [[ "$THETA_CURRENT_YES_MODE" == "live" ]]; then
  args+=(--live --confirm-live)
fi

if [[ "$NO_TELEGRAM" == "1" ]]; then
  args+=(--no-telegram)
fi

nohup "$PYTHON_BIN" "${args[@]}" >>"$LOG_FILE" 2>&1 &

pid="$!"
echo "$pid" >"$PID_FILE"
echo "started mode=$THETA_CURRENT_YES_MODE instance=${THETA_CURRENT_YES_STRATEGY_INSTANCE:-theta_current_yes_tiny_live_v1} entry_profile_mode=$THETA_CURRENT_YES_ENTRY_PROFILE_MODE fade_confirmed_model_mode=$FADE_CONFIRMED_MODEL_MODE pid=$pid log=$LOG_FILE"
