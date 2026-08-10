#!/usr/bin/env bash
# Normalize market-data / Polymarket proxy env for shell-launched runners.
#
# Canonical project key:
#   WEATHER_DATA_FEED_MARKET_PROXY
#
# Legacy aliases are exported only as identical compatibility mirrors. They are
# never independent inputs; the controller-owned state is the single source.

weather_resolve_market_proxy() {
  local project_root="$1"
  local python_bin="$project_root/.venv/bin/python"
  [[ -x "$python_bin" ]] || python_bin="python3"
  PYTHONPATH="$project_root${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" - <<'PY'
from scripts.ops.weather_market_proxy_ctl import read_state
print(read_state()["proxy_url"])
PY
}

weather_export_market_proxy_env() {
  local proxy="${1:-}"
  if [[ -z "$proxy" ]]; then
    echo "market proxy must be resolved from controller state" >&2
    return 2
  fi

  export WEATHER_DATA_FEED_MARKET_PROXY="$proxy"
  export WEATHER_PREDICT_MARKET_PROXY="$proxy"
  export WEATHER_PREDICT_PROXY="$proxy"
  export POLYMARKET_PROXY_URL="$proxy"

  export HTTP_PROXY="$proxy"
  export HTTPS_PROXY="$proxy"
  export ALL_PROXY="$proxy"
  export http_proxy="$proxy"
  export https_proxy="$proxy"
  export all_proxy="$proxy"
}
