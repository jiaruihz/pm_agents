#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$repo_root"
export PYTHONPATH="$repo_root"
export NO_PROXY='*'
export no_proxy='*'

test ! -e /tmp/wcir-runtime-root-required
.venv/bin/python scripts/analysis/forecast_quality/wcir_stage23_rev2_closure.py
.venv/bin/pytest -q \
  tests/research_tests/test_wcir_stage23_rev2.py \
  tests/research_tests/test_wcir_stage23_rev2_closure.py
.venv/bin/python scripts/analysis/forecast_quality/package_wcir_stage23_rev2_closure.py

.venv/bin/python - <<'PY'
import json
from pathlib import Path
root = Path('reviews/wcir_next_print/stage_02_03_rev2_closure')
s = json.loads((root/'PRIMARY_REACTION_GATE_RECONCILIATION_SUMMARY.json').read_text())
h = s['canonical_headlines_from_single_row_table']
assert s['row_count'] == 841
assert {h[k] for k in ('primary_oracle','reaction_primary_aggregate','date_concentration','bootstrap','city_gate')} == {89}
assert s['by_city']['Busan']['paired_primary'] == 56
assert h['baseline_intersection'] == 1
v = json.loads((root/'BOOK_VALIDITY_CORRIGENDUM_RESULTS.json').read_text())
assert v['row_count'] == 841 and v['reconstruction_valid_count'] == 485 and v['freshness_eligible_count'] == 0
PY
