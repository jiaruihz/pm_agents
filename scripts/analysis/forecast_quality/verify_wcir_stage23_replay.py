#!/usr/bin/env python3
"""Compare an isolated Stage 2/3 replay with sealed derived evidence."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


EXACT_FILES = (
    "stage_02/DETERMINISM_TEST_RESULTS.json",
    "stage_02/GAP_AND_RECONNECT_AUDIT.json",
    "stage_02/REST_WS_RECONCILIATION.json",
    "stage_02/EXECUTABLE_SWEEP_TESTS.json",
    "stage_02/EVENT_ALIGNED_BOOK_COVERAGE.json",
    "stage_02/evidence/FROZEN_EXECUTABLE_BOOK_TRUTHS_2026-08-26.jsonl.gz",
    "stage_02/evidence/EVENT_ALIGNED_BOOK_ROWS_2026-08-26.jsonl.gz",
    "stage_03/NEXT_REPORT_DATASET_MANIFEST.json",
    "stage_03/PERFECT_PRINT_ORACLE_RESULTS.json",
    "stage_03/SIMPLE_NOWCAST_BASELINES.json",
    "stage_03/evidence/STAGE03_COMPUTATION_SUMMARY.json",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-root", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, required=True)
    args = parser.parse_args()
    failures = []
    for relative in EXACT_FILES:
        frozen = args.frozen_root / relative
        replay = args.replay_root / relative
        if not frozen.exists() or not replay.exists():
            failures.append(f"missing:{relative}")
        elif digest(frozen) != digest(replay):
            failures.append(f"hash_mismatch:{relative}")
    if failures:
        raise RuntimeError("replay comparison failed: " + ", ".join(failures))
    print(f"exact replay verified: {len(EXACT_FILES)}/{len(EXACT_FILES)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
