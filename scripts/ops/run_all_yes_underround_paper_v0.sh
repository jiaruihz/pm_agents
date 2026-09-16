#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-.venv/bin/python}"

"$PY" scripts/analysis/market_structure_edge/research_all_yes_underround_live_prep_v0.py
"$PY" scripts/ops/all_yes_underround_paper_exec_v0.py cycle
"$PY" scripts/ops/all_yes_underround_paper_exec_v0.py monitor
"$PY" scripts/analysis/market_structure_edge/research_all_yes_underround_live_prep_v0.py
"$PY" scripts/analysis/market_structure_edge/research_all_yes_underround_persistence_v0.py
