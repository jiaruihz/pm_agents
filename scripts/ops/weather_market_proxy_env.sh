#!/usr/bin/env bash
# Normalize market-data / Polymarket proxy env for shell-launched runners.
#
# Canonical project key:
#   WEATHER_DATA_FEED_MARKET_PROXY
#
# Legacy aliases are still accepted so older LaunchAgents and strategy-specific
# wrappers keep working while new code consumes one normalized environment.

weather_resolve_market_proxy() {
  local project_root="$1"
  local python_bin="$project_root/.venv/bin/python"
  [[ -x "$python_bin" ]] || python_bin="python3"
  (
    cd "$project_root" || exit 1
    PYTHONPATH="$project_root${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" - <<'PY'
from scripts.ops.weather_market_proxy_ctl import read_state
print(read_state()["proxy_url"])
PY
  )
}

weather_export_market_proxy_env() {
  local proxy="${1:-}"
  if [[ -z "$proxy" ]]; then
    proxy="${WEATHER_DATA_FEED_MARKET_PROXY:-}"
  fi
  if [[ -z "$proxy" ]]; then
    proxy="${WEATHER_PREDICT_MARKET_PROXY:-}"
  fi
  if [[ -z "$proxy" ]]; then
    proxy="${WEATHER_PREDICT_PROXY:-}"
  fi
  if [[ -z "$proxy" ]]; then
    proxy="${LOW_PRICE_YES_LOTTERY_MARKET_PROXY:-}"
  fi
  if [[ -z "$proxy" ]]; then
    proxy="${POLYMARKET_PROXY_URL:-}"
  fi
  if [[ -z "$proxy" ]]; then
    return 0
  fi

  export WEATHER_DATA_FEED_MARKET_PROXY="${WEATHER_DATA_FEED_MARKET_PROXY:-$proxy}"
  export WEATHER_PREDICT_MARKET_PROXY="${WEATHER_PREDICT_MARKET_PROXY:-$proxy}"
  export WEATHER_PREDICT_PROXY="${WEATHER_PREDICT_PROXY:-$proxy}"
  export POLYMARKET_PROXY_URL="${POLYMARKET_PROXY_URL:-$proxy}"

  export HTTP_PROXY="${HTTP_PROXY:-$proxy}"
  export HTTPS_PROXY="${HTTPS_PROXY:-$proxy}"
  export ALL_PROXY="${ALL_PROXY:-$proxy}"
  export http_proxy="${http_proxy:-$HTTP_PROXY}"
  export https_proxy="${https_proxy:-$HTTPS_PROXY}"
  export all_proxy="${all_proxy:-$ALL_PROXY}"
}
