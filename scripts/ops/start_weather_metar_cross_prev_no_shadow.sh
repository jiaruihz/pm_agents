#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/metar_cross_prev_no_shadow"
PID_FILE="$LOG_DIR/loop.pid"
OUT_FILE="$LOG_DIR/loop.out"
PY="$PROJECT_DIR/.venv/bin/python"
TMUX_SESSION="${METAR_CROSS_TMUX_SESSION:-weather_metar_cross_prev_no_shadow}"

mkdir -p "$LOG_DIR"
if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running pid=$old_pid log=$OUT_FILE"
    exit 0
  fi
fi

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

METAR_CROSS_CITIES="${METAR_CROSS_CITIES:-Shanghai Tokyo}"
METAR_CROSS_BASE_INTERVAL_SEC="${METAR_CROSS_BASE_INTERVAL_SEC:-20}"
METAR_CROSS_BURST_INTERVAL_SEC="${METAR_CROSS_BURST_INTERVAL_SEC:-2}"
METAR_CROSS_BURST_WINDOW_MIN="${METAR_CROSS_BURST_WINDOW_MIN:-10}"
METAR_CROSS_SCHEDULER_MODE="${METAR_CROSS_SCHEDULER_MODE:-batch}"
METAR_CROSS_CITY_HOT_PRE_WINDOW_MIN="${METAR_CROSS_CITY_HOT_PRE_WINDOW_MIN:-1}"
METAR_CROSS_CITY_HOT_CHASE_WINDOW_MIN="${METAR_CROSS_CITY_HOT_CHASE_WINDOW_MIN:-10}"
METAR_CROSS_FALLBACK_REPORT_MINUTES="${METAR_CROSS_FALLBACK_REPORT_MINUTES:-0,30,53}"
METAR_CROSS_LEARNED_BURST_WINDOW_MIN="${METAR_CROSS_LEARNED_BURST_WINDOW_MIN:-6}"
METAR_CROSS_MARKET_CACHE_TTL_SEC="${METAR_CROSS_MARKET_CACHE_TTL_SEC:-1800}"
METAR_CROSS_PREBUILD_LIVE_CLIENT="${METAR_CROSS_PREBUILD_LIVE_CLIENT:-1}"
METAR_CROSS_MAX_WORKERS="${METAR_CROSS_MAX_WORKERS:-12}"
METAR_CROSS_MAX_ASK="${METAR_CROSS_MAX_ASK:-0.995}"
METAR_CROSS_OBS_SOURCE="${METAR_CROSS_OBS_SOURCE:-aviationweather_metar}"
METAR_CROSS_SOURCE_BY_CITY_JSON="${METAR_CROSS_SOURCE_BY_CITY_JSON:-}"
METAR_CROSS_SOURCE_BY_CITY_FILE="${METAR_CROSS_SOURCE_BY_CITY_FILE:-}"
METAR_CROSS_INCLUDE_STATION_DIFF="${METAR_CROSS_INCLUDE_STATION_DIFF:-0}"
METAR_CROSS_LIVE="${METAR_CROSS_LIVE:-0}"
METAR_CROSS_CONFIRM_LIVE="${METAR_CROSS_CONFIRM_LIVE:-0}"
METAR_CROSS_MAX_NOTIONAL_PER_TRADE="${METAR_CROSS_MAX_NOTIONAL_PER_TRADE:-3}"
METAR_CROSS_MAX_NOTIONAL_PER_CITY_DAY="${METAR_CROSS_MAX_NOTIONAL_PER_CITY_DAY:-3}"
METAR_CROSS_MAX_NOTIONAL_TOTAL_DAY="${METAR_CROSS_MAX_NOTIONAL_TOTAL_DAY:-15}"
METAR_CROSS_HTTP_TIMEOUT_SEC="${METAR_CROSS_HTTP_TIMEOUT_SEC:-3.0}"
METAR_CROSS_PROXY_MODE="${METAR_CROSS_PROXY_MODE:-direct}"
METAR_CROSS_WEATHER_PROXY_MODE="${METAR_CROSS_WEATHER_PROXY_MODE:-direct}"
METAR_CROSS_MARKET_PROXY_MODE="${METAR_CROSS_MARKET_PROXY_MODE:-$METAR_CROSS_PROXY_MODE}"
METAR_CROSS_WEATHER_PROXY="${METAR_CROSS_WEATHER_PROXY:-}"
METAR_CROSS_MARKET_PROXY="${METAR_CROSS_MARKET_PROXY:-}"
METAR_CROSS_SYNOP_TOKEN="${METAR_CROSS_SYNOP_TOKEN:-}"
METAR_CROSS_DATA_ROOT="${METAR_CROSS_DATA_ROOT:-}"

args=(
  "$PROJECT_DIR/scripts/ops/weather_metar_cross_prev_no_shadow.py"
  loop
  --base-interval-sec "$METAR_CROSS_BASE_INTERVAL_SEC"
  --burst-interval-sec "$METAR_CROSS_BURST_INTERVAL_SEC"
  --burst-window-min "$METAR_CROSS_BURST_WINDOW_MIN"
  --scheduler-mode "$METAR_CROSS_SCHEDULER_MODE"
  --city-hot-pre-window-min "$METAR_CROSS_CITY_HOT_PRE_WINDOW_MIN"
  --city-hot-chase-window-min "$METAR_CROSS_CITY_HOT_CHASE_WINDOW_MIN"
  --fallback-report-minutes "$METAR_CROSS_FALLBACK_REPORT_MINUTES"
  --learned-burst-window-min "$METAR_CROSS_LEARNED_BURST_WINDOW_MIN"
  --market-cache-ttl-sec "$METAR_CROSS_MARKET_CACHE_TTL_SEC"
  --max-workers "$METAR_CROSS_MAX_WORKERS"
  --max-ask "$METAR_CROSS_MAX_ASK"
  --obs-source "$METAR_CROSS_OBS_SOURCE"
  --max-notional-per-trade "$METAR_CROSS_MAX_NOTIONAL_PER_TRADE"
  --max-notional-per-city-day "$METAR_CROSS_MAX_NOTIONAL_PER_CITY_DAY"
  --max-notional-total-day "$METAR_CROSS_MAX_NOTIONAL_TOTAL_DAY"
)
if [[ -n "$METAR_CROSS_SOURCE_BY_CITY_JSON" ]]; then
  args+=(--source-by-city-json "$METAR_CROSS_SOURCE_BY_CITY_JSON")
fi
if [[ -n "$METAR_CROSS_SOURCE_BY_CITY_FILE" ]]; then
  args+=(--source-by-city-file "$METAR_CROSS_SOURCE_BY_CITY_FILE")
fi
if [[ "$METAR_CROSS_PREBUILD_LIVE_CLIENT" == "0" ]]; then
  args+=(--no-prebuild-live-client)
else
  args+=(--prebuild-live-client)
fi
if [[ "$METAR_CROSS_CITIES" != "ALL" && "$METAR_CROSS_CITIES" != "__all__" ]]; then
  args+=(--cities $METAR_CROSS_CITIES)
fi
if [[ "$METAR_CROSS_INCLUDE_STATION_DIFF" == "1" ]]; then
  args+=(--include-station-diff)
fi
if [[ "$METAR_CROSS_LIVE" == "1" ]]; then
  args+=(--live)
fi
if [[ "$METAR_CROSS_CONFIRM_LIVE" == "1" ]]; then
  args+=(--confirm-live)
fi
env_args=(
  "METAR_CROSS_HTTP_TIMEOUT_SEC=$METAR_CROSS_HTTP_TIMEOUT_SEC"
  "METAR_CROSS_PROXY_MODE=$METAR_CROSS_PROXY_MODE"
  "METAR_CROSS_WEATHER_PROXY_MODE=$METAR_CROSS_WEATHER_PROXY_MODE"
  "METAR_CROSS_MARKET_PROXY_MODE=$METAR_CROSS_MARKET_PROXY_MODE"
  "METAR_CROSS_WEATHER_PROXY=$METAR_CROSS_WEATHER_PROXY"
  "METAR_CROSS_MARKET_PROXY=$METAR_CROSS_MARKET_PROXY"
  "METAR_CROSS_SYNOP_TOKEN=$METAR_CROSS_SYNOP_TOKEN"
  "METAR_CROSS_DATA_ROOT=$METAR_CROSS_DATA_ROOT"
)

env_prefix=""
if [[ -f "$PROJECT_DIR/.env" ]]; then
  env_prefix="set -a; source $(printf '%q' "$PROJECT_DIR/.env"); set +a;"
fi

if command -v tmux >/dev/null 2>&1 && [[ "${METAR_CROSS_START_MODE:-tmux}" == "tmux" ]]; then
  if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "already running tmux=$TMUX_SESSION log=$OUT_FILE"
    exit 0
  fi
  printf -v quoted_args '%q ' "${args[@]}"
  printf -v quoted_env '%q ' env "${env_args[@]}"
  tmux new-session -d -s "$TMUX_SESSION" "cd $(printf '%q' "$PROJECT_DIR") && $env_prefix exec $quoted_env $(printf '%q' "$PY") -u $quoted_args >>$(printf '%q' "$OUT_FILE") 2>&1"
  echo "tmux:$TMUX_SESSION" > "$PID_FILE"
  echo "started metar-cross prev-NO shadow tmux=$TMUX_SESSION log=$OUT_FILE cities=$METAR_CROSS_CITIES"
  exit 0
fi

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi
env "${env_args[@]}" nohup "$PY" -u "${args[@]}" >"$OUT_FILE" 2>&1 < /dev/null &
pid=$!
echo "$pid" > "$PID_FILE"
echo "started metar-cross prev-NO shadow pid=$pid log=$OUT_FILE cities=$METAR_CROSS_CITIES"
