#!/usr/bin/env python3
"""Build a compact, strict WCIR Stage 2/3 rev2 GPT Pro review packet."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.research_wcir_stage02_stage03_rev2 import (  # noqa: E402
    evidence_manifest,
    write_json,
)
REVIEW_FILES = (
    "stage_02_rev2/BOOK_VALIDITY_AND_FAILURE_CONTRACT.md",
    "stage_02_rev2/COLLECTOR_AND_INPUT_FREEZE.json",
    "stage_02_rev2/DUPLICATE_EVENT_IMMUTABILITY_AUDIT.json",
    "stage_02_rev2/FULL_ARCHIVE_REPLAY_MANIFEST.json",
    "stage_02_rev2/EVENT_ALIGNED_BOOK_COVERAGE.json",
    "stage_02_rev2/UNIQUE_FAILURE_REASON_AUDIT.json",
    "stage_02_rev2/COVERAGE_ROOT_CAUSE_DIAGNOSIS.json",
    "stage_02_rev2/DETERMINISM_IDENTITY_COMPARISON.json",
    "stage_02_rev2/REPRODUCTION_RESULTS.json",
    "stage_02_rev2/INDEPENDENT_CODE_REVIEW.md",
    "stage_02_rev2/EVIDENCE_MANIFEST.json",
    "stage_02_rev2/GPT_PRO_REVIEW_PACKET_STAGE_02_REV2.md",
    "stage_03_rev2/FULL_TWO_SIDED_ORACLE_CONTRACT.md",
    "stage_03_rev2/TWO_SIDED_PRIMARY_ORACLE_RESULTS.json",
    "stage_03_rev2/EX_POST_ENVELOPE_MANIFEST.json",
    "stage_03_rev2/MATCHED_BASELINES_AND_ROW_INTERSECTION.json",
    "stage_03_rev2/REACTION_INFERENCE_AND_CONCENTRATION.json",
    "stage_03_rev2/CITY_GATES.json",
    "stage_03_rev2/FORECAST_BASELINE_INPUT_MANIFEST.json",
    "stage_03_rev2/EVIDENCE_MANIFEST.json",
    "stage_03_rev2/GPT_PRO_REVIEW_PACKET_STAGE_03_REV2.md",
    "stage_02_03_rev2_review/START_HERE_GPT_PRO.md",
)
MAX_PACKAGE_BYTES = 10 * 1024 * 1024


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_archive(output: Path, entries: list[dict[str, object]]) -> None:
    expected_names = {str(entry["path"]) for entry in entries} | {
        "PACKAGE_CONTENTS.json"
    }
    with zipfile.ZipFile(output) as archive:
        actual_names = set(archive.namelist())
        if actual_names != expected_names:
            raise RuntimeError(
                f"zip entry-set mismatch missing={sorted(expected_names - actual_names)} "
                f"extra={sorted(actual_names - expected_names)}"
            )
        for entry in entries:
            actual = hashlib.sha256(archive.read(str(entry["path"]))).hexdigest()
            if actual != entry["sha256"]:
                raise RuntimeError(f"zip hash mismatch: {entry['path']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-root", type=Path, default=ROOT / "reviews/wcir_next_print")
    parser.add_argument("--timestamp")
    args = parser.parse_args()
    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.review_root / "stage_02_03_rev2_review"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"wcir-stage-02-03-rev2-gpt-pro-review-{timestamp}.zip"
    sidecar = output.with_suffix(output.suffix + ".sha256")

    for stage in ("stage_02_rev2", "stage_03_rev2"):
        stage_root = args.review_root / stage
        write_json(
            stage_root / "EVIDENCE_MANIFEST.json",
            evidence_manifest(stage_root, exclude={"EVIDENCE_MANIFEST.json"}),
        )

    entries = []
    for relative in REVIEW_FILES:
        path = args.review_root / relative
        if not path.is_file():
            raise RuntimeError(f"required review file missing: {relative}")
        entries.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": digest(path),
            }
        )
    manifest = {
        "package_kind": "compact_summary_and_direction_only_no_raw_or_row_level_data",
        "entry_count_excluding_manifest": len(entries),
        "entries": entries,
        "raw_data_included": False,
        "row_level_evidence_included": False,
        "strict_entry_set": True,
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for entry in entries:
            archive.write(args.review_root / entry["path"], entry["path"])
        archive.writestr(
            "PACKAGE_CONTENTS.json",
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
    if output.stat().st_size > MAX_PACKAGE_BYTES:
        output.unlink()
        raise RuntimeError("review package exceeds 10 MiB compact-packet limit")

    verify_archive(output, entries)
    expected_names = {entry["path"] for entry in entries} | {"PACKAGE_CONTENTS.json"}
    sha = digest(output)
    sidecar.write_text(f"{sha}  {output.name}\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "zip": str(output),
                "sha256": sha,
                "sidecar": str(sidecar),
                "size_bytes": output.stat().st_size,
                "entry_count": len(expected_names),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
