#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-.venv/bin/python}"

"$PY" scripts/ops/all_yes_underround_fresh_paper_cycle_v0.py "$@"
