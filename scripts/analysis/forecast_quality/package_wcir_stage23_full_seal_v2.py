#!/usr/bin/env python3
"""Build and verify strict WCIR full-seal-v2 and bounded-canary packages."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.package_wcir_stage23_rev2_closure import (
    _root_entries,
    entry,
    write_directory_manifest,
    write_package,
)


REVIEW = ROOT / "reviews/wcir_next_print"
SEAL = REVIEW / "stage_02_03_rev2_full_seal_v2"
CANARY = REVIEW / "collector_clock_amendment_v2_canary"
PRIOR = REVIEW / "stage_02_03_rev2_closure"
STAGE2 = REVIEW / "stage_02_rev2"
STAGE3 = REVIEW / "stage_03_rev2"
EVENTS = REVIEW / "stage_03/evidence/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz"


def selected_root_entries(root: Path, prefix: str) -> list[dict[str, object]]:
    return _root_entries(root, prefix)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timestamp")
    args = parser.parse_args()
    stamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    write_directory_manifest(SEAL)
    write_directory_manifest(CANARY)

    code_files = (
        "scripts/analysis/forecast_quality/wcir_stage23_rev2_closure.py",
        "scripts/analysis/forecast_quality/wcir_stage23_full_seal_v2.py",
        "scripts/analysis/forecast_quality/wcir_collector_clock_shadow.py",
        "scripts/analysis/forecast_quality/wcir_isolated_network_canary.py",
        "scripts/analysis/forecast_quality/package_wcir_stage23_rev2_closure.py",
        "scripts/analysis/forecast_quality/package_wcir_stage23_full_seal_v2.py",
        "tests/research_tests/test_wcir_stage23_rev2_closure.py",
        "tests/research_tests/test_wcir_stage23_full_seal_v2.py",
    )
    seal_entries = selected_root_entries(SEAL, "stage_02_03_rev2_full_seal_v2")
    seal_entries += selected_root_entries(PRIOR, "prior_full_evidence_seal")
    seal_entries += selected_root_entries(STAGE2, "immutable_stage_02_rev2")
    seal_entries += selected_root_entries(STAGE3, "immutable_stage_03_rev2")
    seal_entries.append(entry(EVENTS, "immutable_stage_03/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz"))
    seal_entries += [entry(ROOT / relative, relative) for relative in code_files]

    canary_entries = selected_root_entries(CANARY, "collector_clock_amendment_v2_canary")
    canary_entries += [entry(ROOT / relative, relative) for relative in code_files[2:]]
    seal_out = SEAL / f"wcir-stage23-rev2-full-evidence-seal-v2-{stamp}.zip"
    canary_out = CANARY / f"wcir-collector-clock-v2-bounded-network-canary-{stamp}.zip"
    result = {
        "full_evidence_seal": write_package(seal_out, seal_entries, "wcir_stage23_rev2_full_evidence_seal_v2"),
        "bounded_network_canary": write_package(canary_out, canary_entries, "wcir_collector_clock_v2_bounded_public_read_only_canary"),
    }
    (SEAL / "FULL_REVIEW_PACKAGE.sha256").write_text(
        f"{result['full_evidence_seal']['sha256']}  {seal_out.name}\n", encoding="utf-8"
    )
    (CANARY / "FULL_REVIEW_PACKAGE.sha256").write_text(
        f"{result['bounded_network_canary']['sha256']}  {canary_out.name}\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
