#!/usr/bin/env python3
"""Build and verify strict WCIR closure and collector-amendment review zips."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[3]
REVIEW = ROOT / "reviews/wcir_next_print"
CLOSURE = REVIEW / "stage_02_03_rev2_closure"
COLLECTOR = REVIEW / "collector_clock_amendment_v1"
LEGACY_FILES = (
    "stage_02_rev2/RAW_LINEAGE_RANDOM_SAMPLE.json",
    "stage_02_rev2/REST_WS_COMPARABLE_PARITY.json",
    "stage_02_rev2/evidence/EVENT_BOOK_COVERAGE_MATRIX.jsonl.gz",
    "stage_02_rev2/evidence/FROZEN_EVENT_MARKET_UNIVERSE.jsonl.gz",
    "stage_02_rev2/evidence/FROZEN_MARKET_TOKEN_IDENTITIES.jsonl.gz",
    "stage_02_rev2/evidence/FROZEN_PIPELINE_LATENCY_ROWS.jsonl.gz",
    "stage_02_rev2/evidence/REPLAY_CHUNKED_RESULT.json",
    "stage_02_rev2/evidence/REPLAY_REVERSE_RESULT.json",
    "stage_03_rev2/INDEPENDENT_PNL_RECALCULATION.json",
    "stage_03_rev2/evidence/EX_POST_ENVELOPE_ROWS.jsonl.gz",
    "stage_03_rev2/evidence/FROZEN_FORECAST_BASELINE_ROWS.jsonl.gz",
    "stage_03_rev2/evidence/MATCHED_BASELINE_ROWS.jsonl.gz",
    "stage_03_rev2/evidence/PRIMARY_ORACLE_ROWS.jsonl.gz",
    "stage_03_rev2/evidence/REACTION_WINDOW_ROWS.jsonl.gz",
)


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def entry(path: Path, archive_path: str) -> dict[str, object]:
    return {"path": archive_path, "source_path": str(path), "size_bytes": path.stat().st_size, "sha256": digest(path)}


def verify_archive(path: Path, manifest: dict[str, object]) -> None:
    entries = list(manifest["entries"])
    expected = {str(row["path"]) for row in entries} | {"PACKAGE_CONTENTS.json"}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("package contains duplicate zip entry names")
        actual = set(names)
        if actual != expected:
            raise RuntimeError(f"package entry-set mismatch missing={sorted(expected-actual)} extra={sorted(actual-expected)}")
        for row in entries:
            payload = archive.read(str(row["path"]))
            if len(payload) != row["size_bytes"]:
                raise RuntimeError(f"package size drift: {row['path']}")
            if digest_bytes(payload) != row["sha256"]:
                raise RuntimeError(f"package hash drift: {row['path']}")


def write_package(output: Path, entries: list[dict[str, object]], kind: str) -> dict[str, object]:
    manifest = {
        "package_kind": kind,
        "strict_whitelist": True,
        "unmanifested_extra_files_allowed": False,
        "entries": [{key: value for key, value in row.items() if key != "source_path"} for row in entries],
        "entry_count_excluding_manifest": len(entries),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for row in entries:
            archive.write(str(row["source_path"]), str(row["path"]))
        archive.writestr("PACKAGE_CONTENTS.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    verify_archive(output, manifest)
    sha = digest(output)
    sidecar = output.with_suffix(output.suffix + ".sha256")
    sidecar.write_text(f"{sha}  {output.name}\n", encoding="utf-8")
    return {"zip": str(output), "sha256": sha, "sidecar": str(sidecar), "size_bytes": output.stat().st_size, "entry_count": len(entries) + 1}


def _root_entries(root: Path, prefix: str) -> list[dict[str, object]]:
    skip_suffixes = (".zip", ".zip.sha256")
    paths = [
        path for path in root.rglob("*")
        if path.is_file()
        and not path.name.endswith(skip_suffixes)
        and path.name != "FULL_REVIEW_PACKAGE.sha256"
    ]
    return [entry(path, f"{prefix}/{path.relative_to(root)}") for path in sorted(paths)]


def write_directory_manifest(root: Path) -> None:
    paths = [
        path for path in root.rglob("*")
        if path.is_file()
        and path.name != "EVIDENCE_MANIFEST.json"
        and path.name != "FULL_REVIEW_PACKAGE.sha256"
        and not path.name.endswith((".zip", ".zip.sha256"))
    ]
    rows = [
        {"path": str(path.relative_to(root)), "size_bytes": path.stat().st_size, "sha256": digest(path)}
        for path in sorted(paths)
    ]
    payload = {
        "root": str(root),
        "entry_count": len(rows),
        "entries": rows,
        "strict_entry_set": True,
        "unmanifested_extra_files_allowed": False,
    }
    (root / "EVIDENCE_MANIFEST.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timestamp")
    args = parser.parse_args()
    stamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    write_directory_manifest(CLOSURE)
    write_directory_manifest(COLLECTOR)
    closure_entries = _root_entries(CLOSURE, "stage_02_03_rev2_closure")
    closure_entries += [entry(REVIEW / relative, relative) for relative in LEGACY_FILES]
    collector_entries = _root_entries(COLLECTOR, "collector_clock_amendment_v1")
    code_files = (
        "scripts/analysis/forecast_quality/wcir_collector_clock_shadow.py",
        "scripts/analysis/forecast_quality/wcir_stage23_rev2_closure.py",
        "scripts/analysis/forecast_quality/package_wcir_stage23_rev2_closure.py",
        "tests/research_tests/test_wcir_stage23_rev2_closure.py",
    )
    collector_entries += [entry(ROOT / relative, relative) for relative in code_files]
    closure_out = CLOSURE / f"wcir-stage-02-03-rev2-evidence-closure-{stamp}.zip"
    collector_out = COLLECTOR / f"wcir-collector-clock-amendment-v1-{stamp}.zip"
    result = {
        "closure": write_package(closure_out, closure_entries, "wcir_stage23_rev2_full_evidence_closure"),
        "collector": write_package(collector_out, collector_entries, "wcir_collector_clock_amendment_isolated_shadow_review"),
    }
    # Required stable pointer to the full-package digest.
    (CLOSURE / "FULL_REVIEW_PACKAGE.sha256").write_text(
        f"{result['closure']['sha256']}  {closure_out.name}\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
