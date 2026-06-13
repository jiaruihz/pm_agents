#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$ROOT}"
DATA_PROJECT_DIR="${DATA_PROJECT_DIR:-$PROJECT_DIR}"
PY="${PYTHON:-$PROJECT_DIR/.venv/bin/python}"

SNAPSHOT_ROOT="${SNAPSHOT_ROOT:-$DATA_PROJECT_DIR/runtime/weather_edge_v1/market_data/orderbook_snapshots}"
RUN_DIR="${RUN_DIR:-$DATA_PROJECT_DIR/runtime/weather_edge_v1/all_yes_underround_paper_v0}"
DB_PATH="${DB_PATH:-$DATA_PROJECT_DIR/runtime/weather.db}"
GATE_PATH="${GATE_PATH:-$DATA_PROJECT_DIR/runtime/_dashboard_logs/clob_fill_coverage_gate.json}"
STATION_BASIS_GATE_PATH="${STATION_BASIS_GATE_PATH:-$DATA_PROJECT_DIR/runtime/weather_edge_v1/station_basis_shadow_v1/live_prep_gate.json}"
MAX_SNAPSHOT_AGE_SECONDS="${MAX_SNAPSHOT_AGE_SECONDS:-180}"
CYCLE_INTERVAL_SECONDS="${CYCLE_INTERVAL_SECONDS:-30}"

cd "$PROJECT_DIR"
mkdir -p "$RUN_DIR"

echo "all_yes_underround_fresh_paper_loop_v0 start project=$PROJECT_DIR data_project=$DATA_PROJECT_DIR snapshot_root=$SNAPSHOT_ROOT run_dir=$RUN_DIR max_age=${MAX_SNAPSHOT_AGE_SECONDS}s interval=${CYCLE_INTERVAL_SECONDS}s"

while true; do
  "$PY" scripts/ops/all_yes_underround_fresh_paper_cycle_v0.py \
    --snapshot-root "$SNAPSHOT_ROOT" \
    --run-dir "$RUN_DIR" \
    --db-path "$DB_PATH" \
    --gate-path "$GATE_PATH" \
    --station-basis-gate-path "$STATION_BASIS_GATE_PATH" \
    --max-snapshot-age-seconds "$MAX_SNAPSHOT_AGE_SECONDS" || true
  sleep "$CYCLE_INTERVAL_SECONDS"
done
