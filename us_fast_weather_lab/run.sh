#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "missing .venv/bin/python; create the project environment first" >&2
  exit 2
fi

exec .venv/bin/python -m us_fast_weather_lab.cli smoke "$@"

