from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "reviews/wcir_next_print/stage_01/evidence"
sys.path.insert(0, str(TOOLS))

from stage01_tools import (  # noqa: E402
    build_manifest,
    build_review_zip,
    sha256_file,
    verify_evidence_directory,
    verify_review_zip,
)


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "package"
    root.mkdir(parents=True)
    (root / "required.txt").write_text("frozen\n")
    build_manifest(root, metadata={"generated_at_utc": "frozen", "code_commit_sha": "sha", "zero_notional": {"orders": 0, "fills": 0, "notional": 0}})
    return root


def test_package_exact_entry_set_passes(tmp_path: Path) -> None:
    assert verify_evidence_directory(_fixture(tmp_path))["status"] == "pass"


def test_package_missing_extra_and_hash_drift_fail_closed(tmp_path: Path) -> None:
    missing = _fixture(tmp_path / "missing")
    (missing / "required.txt").unlink()
    with pytest.raises(ValueError, match="entry set mismatch"):
        verify_evidence_directory(missing)

    extra = _fixture(tmp_path / "extra")
    (extra / "extra.txt").write_text("unmanifested")
    with pytest.raises(ValueError, match="entry set mismatch"):
        verify_evidence_directory(extra)

    drift = _fixture(tmp_path / "drift")
    (drift / "required.txt").write_text("tampered\n")
    with pytest.raises(ValueError, match="hash drift"):
        verify_evidence_directory(drift)


def test_frozen_reconciliation_preserves_stage00_denominator() -> None:
    results = json.loads((TOOLS / "REPRODUCTION_STAGE_01_RESULTS.json").read_text())
    legacy = results["legacy_reconciliation"]
    assert legacy["legacy_serializations"] == 18726
    assert legacy["legacy_unique_candidate_ids"] == 18625
    assert legacy["legacy_duplicate_serializations"] == 101
    assert legacy["row_count_delta"] == 0
    assert legacy["legacy_headline_mutated"] is False


def test_zip_sidecar_and_exact_entry_set_fail_closed(tmp_path: Path) -> None:
    root = _fixture(tmp_path / "good")
    output = tmp_path / "review.zip"
    build_review_zip(root, output)
    assert verify_review_zip(output)["status"] == "pass"

    output.with_suffix(".zip.sha256").write_text("0" * 64 + "  review.zip\n")
    with pytest.raises(ValueError, match="sidecar hash drift"):
        verify_review_zip(output)

    output.with_suffix(".zip.sha256").write_text(f"{sha256_file(output)}  review.zip\n")
    with zipfile.ZipFile(output, "a") as archive:
        archive.writestr("stage_01/extra.txt", "extra")
    output.with_suffix(".zip.sha256").write_text(f"{sha256_file(output)}  review.zip\n")
    with pytest.raises(ValueError, match="entry set mismatch"):
        verify_review_zip(output)
