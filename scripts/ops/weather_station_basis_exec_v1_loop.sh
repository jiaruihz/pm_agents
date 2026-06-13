#!/usr/bin/env bash
# Station-basis v1 executor loop (dry-run parity by default).
# Consumes station_basis_shadow_v1 entries every 5 min and writes an independent
# station_basis_exec_v1 audit ledger.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"
PY="$PROJECT_DIR/.venv/bin/python"

export STATION_BASIS_EXEC_MODE="${STATION_BASIS_EXEC_MODE:-dry_run}"

while true; do
  "$PY" scripts/ops/weather_station_basis_exec_v1.py run || true
  sleep 300
done
