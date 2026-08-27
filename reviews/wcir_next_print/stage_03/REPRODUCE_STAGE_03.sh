#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$repo_root"
replay_tmp="$(mktemp -d "${TMPDIR:-/tmp}/wcir-stage03-replay.XXXXXX")"
trap 'rm -rf "$replay_tmp"' EXIT
python3 scripts/analysis/forecast_quality/research_wcir_stage02_stage03.py \
  --offline \
  --runtime-root /tmp/wcir-stage03-no-runtime \
  --frozen-root "$repo_root/reviews/wcir_next_print" \
  --output-root "$replay_tmp/reviews/wcir_next_print"
python3 scripts/analysis/forecast_quality/verify_wcir_stage23_replay.py \
  --frozen-root "$repo_root/reviews/wcir_next_print" \
  --replay-root "$replay_tmp/reviews/wcir_next_print"
python3 -m pytest -q \
  tests/pmm_tests/test_weather_ws_incremental_book.py \
  tests/pmm_tests/test_executable_book_truth.py
