#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
REFRESH_PROJECT_DIR="$(
  cd "$PROJECT_DIR"
  PYTHONPATH="$PROJECT_DIR" "$PROJECT_DIR/.venv/bin/python" -c \
    'from src.strategies.runtime.production import load_production_spec; spec = load_production_spec(); print(spec.canonical_refresh_checkout_root or spec.operational_repo_root)'
)"
OPERATIONAL_PROJECT_DIR="$(
  cd "$PROJECT_DIR"
  PYTHONPATH="$PROJECT_DIR" "$PROJECT_DIR/.venv/bin/python" -c \
    'from src.strategies.runtime.production import load_production_spec; print(load_production_spec().operational_repo_root)'
)"

# The release checkout is immutable code, not a second data root.  The fill
# coverage gate still defaults to a checkout-relative cache path, so provision
# that path as an explicit pointer to the operational cache before every run.
# This also makes newly provisioned release SHAs safe on their first refresh.
CANONICAL_FILL_CACHE="$OPERATIONAL_PROJECT_DIR/runtime/weather_edge_v1/clob_fills.jsonl"
REFRESH_FILL_CACHE="$REFRESH_PROJECT_DIR/runtime/weather_edge_v1/clob_fills.jsonl"
if [[ ! -f "$CANONICAL_FILL_CACHE" ]]; then
  echo "canonical fill cache missing: $CANONICAL_FILL_CACHE" >&2
  exit 1
fi
mkdir -p "$(dirname "$REFRESH_FILL_CACHE")"
if [[ -L "$REFRESH_FILL_CACHE" ]]; then
  ln -sfn "$CANONICAL_FILL_CACHE" "$REFRESH_FILL_CACHE"
elif [[ -e "$REFRESH_FILL_CACHE" ]]; then
  echo "release fill cache path is not a symlink: $REFRESH_FILL_CACHE" >&2
  exit 1
else
  ln -s "$CANONICAL_FILL_CACHE" "$REFRESH_FILL_CACHE"
fi
SESSION="weather_canonical_refresh"
JOB_DIR="$PROJECT_DIR/runtime/weather_edge_v1/canonical_refresh"
printf -v JOB_COMMAND \
  'cd %q; export PROJECT_DIR=%q WEATHER_DATA_FEED_RUNTIME_ROOT=%q; %q' \
  "$REFRESH_PROJECT_DIR" "$REFRESH_PROJECT_DIR" "$RUNTIME_ROOT" \
  "$REFRESH_PROJECT_DIR/scripts/ops/run_weather_canonical_refresh_launchd.sh"

weather_jrs_tmux_run_oneshot \
  "$RUNTIME_ROOT" \
  "$SESSION" \
  "$JOB_DIR" \
  "$JOB_COMMAND"
