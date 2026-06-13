#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$ROOT}"
DATA_PROJECT_DIR="${DATA_PROJECT_DIR:-$PROJECT_DIR}"
PY="${PYTHON:-$PROJECT_DIR/.venv/bin/python}"

RUN_DIR="${RUN_DIR:-$DATA_PROJECT_DIR/runtime/weather_edge_v1/all_yes_underround_paper_v0}"
DB_PATH="${DB_PATH:-$DATA_PROJECT_DIR/runtime/weather.db}"
GATE_PATH="${GATE_PATH:-$DATA_PROJECT_DIR/runtime/_dashboard_logs/clob_fill_coverage_gate.json}"
STATION_BASIS_GATE_PATH="${STATION_BASIS_GATE_PATH:-$DATA_PROJECT_DIR/runtime/weather_edge_v1/station_basis_shadow_v1/live_prep_gate.json}"
WEATHER_PREDICT_DIR="${WEATHER_PREDICT_DIR:-/home/jiarui/projects/weather-predict}"
MICRO_SNAPSHOT_DIR="${MICRO_SNAPSHOT_DIR:-$RUN_DIR/micro_orderbook_snapshots}"
MICRO_SUMMARY_PATH="${MICRO_SUMMARY_PATH:-$RUN_DIR/micro_snapshot_summary.json}"
SCAN_JSON_PATH="${SCAN_JSON_PATH:-$RUN_DIR/latest_scan.json}"
SCAN_MD_PATH="${SCAN_MD_PATH:-$RUN_DIR/latest_scan.md}"
MICRO_DATES="${MICRO_DATES:-}"
MICRO_DAYS_FORWARD="${MICRO_DAYS_FORWARD:-1}"
MICRO_CITIES="${MICRO_CITIES:-}"
MICRO_CITY_POOL="${MICRO_CITY_POOL:-all}"
MICRO_EVENT_CONCURRENCY="${MICRO_EVENT_CONCURRENCY:-12}"
MICRO_ORDERBOOK_CONCURRENCY="${MICRO_ORDERBOOK_CONCURRENCY:-80}"
MAX_SNAPSHOT_AGE_SECONDS="${MAX_SNAPSHOT_AGE_SECONDS:-180}"
MIN_FILE_STABLE_SECONDS="${MIN_FILE_STABLE_SECONDS:-0}"
MIN_SNAPSHOT_ROWS="${MIN_SNAPSHOT_ROWS:-100}"

cd "$PROJECT_DIR"
mkdir -p "$RUN_DIR"

micro_args=(
  --weather-predict-dir "$WEATHER_PREDICT_DIR"
  --snapshot-dir "$MICRO_SNAPSHOT_DIR"
  --days-forward "$MICRO_DAYS_FORWARD"
  --city-pool "$MICRO_CITY_POOL"
  --event-concurrency "$MICRO_EVENT_CONCURRENCY"
  --orderbook-concurrency "$MICRO_ORDERBOOK_CONCURRENCY"
  --summary-out "$MICRO_SUMMARY_PATH"
)
if [[ -n "$MICRO_DATES" ]]; then
  micro_args+=(--dates "$MICRO_DATES")
fi
if [[ -n "$MICRO_CITIES" ]]; then
  micro_args+=(--cities "$MICRO_CITIES")
fi

"$PY" scripts/ops/all_yes_underround_micro_snapshot_v0.py "${micro_args[@]}"

snapshot_path="$("$PY" - "$MICRO_SUMMARY_PATH" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1]))["out"])
PY
)"

"$PY" scripts/ops/all_yes_underround_fresh_paper_cycle_v0.py \
  --snapshot-path "$snapshot_path" \
  --run-dir "$RUN_DIR" \
  --db-path "$DB_PATH" \
  --gate-path "$GATE_PATH" \
  --station-basis-gate-path "$STATION_BASIS_GATE_PATH" \
  --scan-json "$SCAN_JSON_PATH" \
  --scan-md "$SCAN_MD_PATH" \
  --max-snapshot-age-seconds "$MAX_SNAPSHOT_AGE_SECONDS" \
  --min-file-stable-seconds "$MIN_FILE_STABLE_SECONDS" \
  --min-snapshot-rows "$MIN_SNAPSHOT_ROWS"
