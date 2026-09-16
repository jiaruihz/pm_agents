#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$(weather_production_path "$ROOT" data_feed_runtime_root)}"
MARKET_PROXY="${WEATHER_PROPOSAL_REWARD_MARKET_PROXY:-$(weather_production_path "$ROOT" market_proxy_default_url)}"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
SESSION="weather_proposal_reward_shadow_v1"
OUTPUT_DIR="$RUNTIME_ROOT/output/proposal_reward_shadow_v1"
CONFIG="$ROOT/configs/weather/proposal_reward_shadow_v1.json"
LOG_FILE="$RUNTIME_ROOT/loop/proposal_reward_shadow_v1.log"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"
weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "=$SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && exec '$PY' -u '$ROOT/scripts/ops/weather_proposal_reward_shadow_v1.py' loop --config '$CONFIG' --output-dir '$OUTPUT_DIR' --market-proxy '$MARKET_PROXY' >> '$LOG_FILE' 2>&1"

echo "started session=$SESSION socket=$TMUX_SOCKET output=$OUTPUT_DIR mode=zero_notional_shadow submission_enabled=false"
