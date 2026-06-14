#!/usr/bin/env bash
# Station-basis executor loop (OPTIONAL, dry-run parity by default).
# Consumes shadow entries every 5 min, applies risk guards, logs intended orders.
# Real placement stays HARD-GATED inside the python (see weather_station_basis_exec.py).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"
PY="$PROJECT_DIR/.venv/bin/python"

# default dry_run; only an explicit env on the launcher could change it
export STATION_BASIS_EXEC_MODE="${STATION_BASIS_EXEC_MODE:-dry_run}"

while true; do
  "$PY" scripts/ops/weather_station_basis_exec.py run || true
  sleep 300
done
