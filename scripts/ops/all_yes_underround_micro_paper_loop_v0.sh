#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$ROOT}"
DATA_PROJECT_DIR="${DATA_PROJECT_DIR:-$PROJECT_DIR}"
RUN_DIR="${RUN_DIR:-$DATA_PROJECT_DIR/runtime/weather_edge_v1/all_yes_underround_paper_v0}"
CYCLE_INTERVAL_SECONDS="${CYCLE_INTERVAL_SECONDS:-30}"
SNAPSHOT_SERVICE_NAME="${SNAPSHOT_SERVICE_NAME:-}"

cd "$PROJECT_DIR"
mkdir -p "$RUN_DIR"

echo "all_yes_underround_micro_paper_loop_v0 start project=$PROJECT_DIR data_project=$DATA_PROJECT_DIR run_dir=$RUN_DIR interval=${CYCLE_INTERVAL_SECONDS}s city_pool=${MICRO_CITY_POOL:-all} snapshot_service=${SNAPSHOT_SERVICE_NAME:-none}"

while true; do
  PROJECT_DIR="$PROJECT_DIR" DATA_PROJECT_DIR="$DATA_PROJECT_DIR" RUN_DIR="$RUN_DIR" \
    bash scripts/ops/run_all_yes_underround_micro_paper_v0.sh || true
  sleep "$CYCLE_INTERVAL_SECONDS"
done
