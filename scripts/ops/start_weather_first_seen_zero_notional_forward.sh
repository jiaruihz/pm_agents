#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$REPO_ROOT/scripts/ops/weather_jrs_tmux_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
PM_RUNTIME_ROOT="${WEATHER_PM_RUNTIME_ROOT:-/Volumes/jrs/pm_agents/runtime}"
SESSION="${WEATHER_FIRST_SEEN_SESSION:-weather_first_seen_zero_notional}"
SOCKET="$(weather_jrs_tmux_socket)"
PY="${WEATHER_FIRST_SEEN_PYTHON:-$REPO_ROOT/.venv/bin/python}"
DB_PATH="${WEATHER_FIRST_SEEN_DB_PATH:-$PM_RUNTIME_ROOT/weather.db}"
OUTPUT_DIR="${WEATHER_FIRST_SEEN_OUTPUT_DIR:-$RUNTIME_ROOT/output/first_seen_zero_notional}"
FEATURE_STORE="${WEATHER_FIRST_SEEN_FEATURE_STORE:-$PM_RUNTIME_ROOT/weather_edge_v1/feature_store}"
SOURCE_EVENTS="${WEATHER_FIRST_SEEN_SOURCE_EVENTS_PATH:-$RUNTIME_ROOT/output/source_events/sources.jsonl}"
HIGH_FREQUENCY_OBSERVATIONS="${WEATHER_FIRST_SEEN_HIGH_FREQUENCY_OBSERVATIONS_PATH:-$RUNTIME_ROOT/output/high_frequency_observations/high_frequency_observations.jsonl}"
FORECAST_CURVES="${WEATHER_FIRST_SEEN_FORECAST_CURVES_PATH:-$RUNTIME_ROOT/targeted_output/forecast_hourly_curves}"
FORECAST_ENRICHMENT="${WEATHER_FIRST_SEEN_FORECAST_ENRICHMENT_PATH-$RUNTIME_ROOT/output/forecast_enrichment/forecast_enrichment.jsonl}"
PAPER_SNAPSHOTS="${WEATHER_FIRST_SEEN_PAPER_SNAPSHOTS_PATH:-$RUNTIME_ROOT/targeted_output/paper_snapshots}"

weather_jrs_tmux_write_probe "$SOCKET" "$RUNTIME_ROOT"
weather_jrs_tmux_write_probe "$SOCKET" "$PM_RUNTIME_ROOT"

if weather_jrs_tmux "$SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "first-seen zero-notional session already running: socket=$SOCKET session=$SESSION"
  exit 0
fi

command_string="$(
  printf "cd %q && exec %q -u scripts/ops/weather_first_seen_zero_notional_forward.py" \
    "$REPO_ROOT" "$PY"
  printf " --db %q" "$DB_PATH"
  printf " --out %q" "$OUTPUT_DIR/candidates.jsonl"
  printf " --state %q" "$OUTPUT_DIR/state.json"
  printf " --source-events %q" "$SOURCE_EVENTS"
  printf " --high-frequency-observations %q" "$HIGH_FREQUENCY_OBSERVATIONS"
  printf " --forecast-curves %q" "$FORECAST_CURVES"
  if [[ -n "$FORECAST_ENRICHMENT" ]]; then
    printf " --forecast-enrichment %q" "$FORECAST_ENRICHMENT"
  fi
  printf " --paper-snapshots %q" "$PAPER_SNAPSHOTS"
  printf " --feature-store %q" "$FEATURE_STORE"
  printf " --max-snapshot-lag-minutes 20"
  printf " --snapshot-lookback-files 48"
  printf " --event-limit 500"
  printf " --bootstrap-at-end --loop --interval-seconds 60"
  printf " >> %q 2>&1" "$OUTPUT_DIR/forward.log"
)"

weather_jrs_tmux "$SOCKET" new-session -d -s "$SESSION" \
  "mkdir -p $(printf '%q' "$OUTPUT_DIR") && $command_string"

sleep 1
weather_jrs_tmux "$SOCKET" has-session -t "$SESSION"
echo "started first-seen zero-notional forward: socket=$SOCKET session=$SESSION"
