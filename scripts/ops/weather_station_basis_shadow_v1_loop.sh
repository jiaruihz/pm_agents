#!/usr/bin/env bash
# Station-basis v1 shadow loop: one cycle every 15 min, settle once per hour.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

PY="${PYTHON:-$PROJECT_DIR/.venv/bin/python}"
i=0
while true; do
  "$PY" scripts/ops/weather_station_basis_shadow_v1.py cycle || true
  if (( i % 4 == 0 )); then
    "$PY" scripts/ops/weather_station_basis_shadow_v1.py settle || true
  fi
  i=$((i + 1))
  sleep 900
done
