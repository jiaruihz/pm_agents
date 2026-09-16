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
CANONICAL_DB="$(
  cd "$PROJECT_DIR"
  PYTHONPATH="$PROJECT_DIR" "$PROJECT_DIR/.venv/bin/python" -c \
    'from src.strategies.runtime.production import load_production_spec; print(load_production_spec().canonical_db_path)'
)"

# The release checkout is immutable code, not a second data root.  Provision
# the operational fill cache and every append-only adjustment journal as
# explicit, verified release-runtime bindings before each bounded refresh.
"$PROJECT_DIR/.venv/bin/python" \
  "$PROJECT_DIR/scripts/ops/weather_canonical_refresh_bindings.py" \
  --operational-project-dir "$OPERATIONAL_PROJECT_DIR" \
  --refresh-project-dir "$REFRESH_PROJECT_DIR" \
  --canonical-db "$CANONICAL_DB" \
  >/dev/null
SESSION="weather_canonical_refresh"
JOB_DIR="$PROJECT_DIR/runtime/weather_edge_v1/canonical_refresh"
printf -v JOB_COMMAND \
  'cd %q; export PROJECT_DIR=%q WEATHER_DATA_FEED_RUNTIME_ROOT=%q WEATHER_CANONICAL_REFRESH_RUNTIME_DIR=%q; %q' \
  "$REFRESH_PROJECT_DIR" "$REFRESH_PROJECT_DIR" "$RUNTIME_ROOT" "$JOB_DIR" \
  "$REFRESH_PROJECT_DIR/scripts/ops/run_weather_canonical_refresh_launchd.sh"

WEATHER_JRS_ONESHOT_LOG_MAX_BYTES="${WEATHER_CANONICAL_REFRESH_LOG_MAX_BYTES:-67108864}" \
WEATHER_JRS_ONESHOT_LOG_RETAIN_BYTES="${WEATHER_CANONICAL_REFRESH_LOG_RETAIN_BYTES:-8388608}" \
weather_jrs_tmux_run_oneshot \
  "$RUNTIME_ROOT" \
  "$SESSION" \
  "$JOB_DIR" \
  "$JOB_COMMAND"
