#!/usr/bin/env python3
"""Build the strict Stage 0–3 GPT Pro consultation archive."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "reviews/wcir_next_print/stage_00_03_consolidated_review"

LOCAL_ENTRY_NAMES = (
    "GPT_PRO_CONSOLIDATED_REVIEW_AND_MODEL_TRAINING_CONSULTATION.md",
    "STAGE_00_03_EVIDENCE_INDEX.md",
    "CURRENT_READINESS_AND_OPEN_QUESTIONS.json",
    "INDEPENDENT_REVIEW_EVIDENCE.md",
)


EXTERNAL_ENTRIES = {
    "authoritative/WCIR_NEXT_PRINT_CODEX_MASTER_PLAN_v1.md": ROOT / "reviews/wcir_next_print/stage_00/evidence/WCIR_NEXT_PRINT_CODEX_MASTER_PLAN_v1.md",
    "stage_packages/stage_00_original.zip": ROOT / "reviews/wcir_next_print/stage_00/wcir-next-print-stage-00-review-20260826T173345Z.zip",
    "stage_packages/stage_00_rev2.zip": ROOT / "reviews/wcir_next_print/stage_00_rev2/wcir-next-print-stage-00-review-rev2-20260827T041136Z.zip",
    "stage_packages/stage_01.zip": ROOT / "reviews/wcir_next_print/stage_01/wcir-next-print-stage-01-review-20260827T080739Z.zip",
    "stage_packages/stage_02.zip": ROOT / "reviews/wcir_next_print/stage_02/wcir-next-print-stage-02-review-20260827T090456Z.zip",
    "stage_packages/stage_03.zip": ROOT / "reviews/wcir_next_print/stage_03/wcir-next-print-stage-03-review-20260827T090456Z.zip",
    "stage_sidecars/stage_00_rev2.zip.sha256": ROOT / "reviews/wcir_next_print/stage_00_rev2/wcir-next-print-stage-00-review-rev2-20260827T041136Z.zip.sha256",
    "stage_sidecars/stage_01.zip.sha256": ROOT / "reviews/wcir_next_print/stage_01/wcir-next-print-stage-01-review-20260827T080739Z.zip.sha256",
    "stage_sidecars/stage_02.zip.sha256": ROOT / "reviews/wcir_next_print/stage_02/wcir-next-print-stage-02-review-20260827T090456Z.zip.sha256",
    "stage_sidecars/stage_03.zip.sha256": ROOT / "reviews/wcir_next_print/stage_03/wcir-next-print-stage-03-review-20260827T090456Z.zip.sha256",
    "stage_packets/STAGE_00_REV2_PACKET.md": ROOT / "reviews/wcir_next_print/stage_00_rev2/GPT_PRO_REVIEW_PACKET_STAGE_00_REV2.md",
    "stage_packets/STAGE_01_PACKET.md": ROOT / "reviews/wcir_next_print/stage_01/GPT_PRO_REVIEW_PACKET_STAGE_01.md",
    "stage_packets/STAGE_02_PACKET.md": ROOT / "reviews/wcir_next_print/stage_02/GPT_PRO_REVIEW_PACKET_STAGE_02.md",
    "stage_packets/STAGE_03_PACKET.md": ROOT / "reviews/wcir_next_print/stage_03/GPT_PRO_REVIEW_PACKET_STAGE_03.md",
    "stage_decisions/STAGE_00_ORIGINAL_INDEPENDENT_REVIEW.md": ROOT / "reviews/wcir_next_print/stage_00_rev2/evidence/GPT_PRO_STAGE00_INDEPENDENT_REVIEW.md",
    "stage_decisions/STAGE_00_REV2_PRODUCTION_HEALTH_BLOCKER.md": ROOT / "reviews/wcir_next_print/stage_00_rev2/PRODUCTION_HEALTH_BLOCKER.md",
    "stage_decisions/STAGE_01_INDEPENDENT_REVIEW.md": ROOT / "reviews/wcir_next_print/stage_01/evidence/INDEPENDENT_CODE_REVIEW_STAGE01.md",
    "stage_decisions/STAGE_02_03_INDEPENDENT_REVIEW.md": ROOT / "reviews/wcir_next_print/STAGE_02_03_INDEPENDENT_REVIEW_EVIDENCE.md",
    "stage_decisions/STAGE_03_CITY_DECISION.md": ROOT / "reviews/wcir_next_print/stage_03/CITY_DIRECTION_DECISION.md",
    "stage_decisions/STAGE_03_ORACLE_RESULTS.json": ROOT / "reviews/wcir_next_print/stage_03/PERFECT_PRINT_ORACLE_RESULTS.json",
}

SIDECAR_PAIRS = (
    ("stage_packages/stage_00_rev2.zip", "stage_sidecars/stage_00_rev2.zip.sha256"),
    ("stage_packages/stage_01.zip", "stage_sidecars/stage_01.zip.sha256"),
    ("stage_packages/stage_02.zip", "stage_sidecars/stage_02.zip.sha256"),
    ("stage_packages/stage_03.zip", "stage_sidecars/stage_03.zip.sha256"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def entry_identity(archive_path: str, source: Path) -> dict[str, object]:
    return {
        "archive_path": archive_path,
        "source_repo_path": source.relative_to(ROOT).as_posix(),
        "size_bytes": source.stat().st_size,
        "sha256": sha256(source),
    }


def verify_stage_sidecars() -> None:
    for archive_entry, sidecar_entry in SIDECAR_PAIRS:
        archive = EXTERNAL_ENTRIES[archive_entry]
        sidecar = EXTERNAL_ENTRIES[sidecar_entry]
        lines = sidecar.read_text().splitlines()
        if len(lines) != 1:
            raise RuntimeError(f"sidecar must contain exactly one line: {sidecar}")
        parts = lines[0].split()
        if len(parts) != 2:
            raise RuntimeError(f"invalid sidecar format: {sidecar}")
        declared_sha, declared_name = parts
        if declared_name != archive.name:
            raise RuntimeError(
                f"sidecar filename mismatch: {declared_name} != {archive.name}"
            )
        actual_sha = sha256(archive)
        if declared_sha != actual_sha:
            raise RuntimeError(
                f"sidecar hash mismatch: {archive} declared={declared_sha} actual={actual_sha}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timestamp")
    args = parser.parse_args()
    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", timestamp) is None:
        raise ValueError("timestamp must match YYYYMMDDTHHMMSSZ")
    BUNDLE.mkdir(parents=True, exist_ok=True)
    manifest_path = BUNDLE / "CONSOLIDATED_EVIDENCE_MANIFEST.json"
    package_name = (
        f"wcir-next-print-stage-00-03-consolidated-review-and-training-consult-{timestamp}.zip"
    )
    package_path = BUNDLE / package_name
    sidecar_path = BUNDLE / f"{package_name}.sha256"
    if package_path.parent != BUNDLE or sidecar_path.parent != BUNDLE:
        raise RuntimeError("package outputs escaped the bundle directory")

    local_entries = {name: BUNDLE / name for name in LOCAL_ENTRY_NAMES}
    allowed_generated = {manifest_path.name, package_path.name, sidecar_path.name}
    unexpected = sorted(
        path.name
        for path in BUNDLE.iterdir()
        if path.is_file()
        and path.name not in LOCAL_ENTRY_NAMES
        and path.name not in allowed_generated
        and path.suffix != ".zip"
        and not path.name.endswith(".zip.sha256")
    )
    if unexpected:
        raise RuntimeError(f"unmanifested local consultation files: {unexpected}")
    entries = {**local_entries, **EXTERNAL_ENTRIES}
    missing = [archive for archive, source in entries.items() if not source.is_file()]
    if missing:
        raise RuntimeError(f"missing consultation inputs: {missing}")
    if len(entries) != len(set(entries)):
        raise RuntimeError("duplicate archive entry")
    verify_stage_sidecars()

    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    identities = [
        entry_identity(archive_path, source)
        for archive_path, source in sorted(entries.items())
    ]
    manifest = {
        "schema_version": "wcir_stage_00_03_consolidated_evidence_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository_commit_sha": commit,
        "authoritative_plan_entry": "authoritative/WCIR_NEXT_PRINT_CODEX_MASTER_PLAN_v1.md",
        "start_here_entry": "GPT_PRO_CONSOLIDATED_REVIEW_AND_MODEL_TRAINING_CONSULTATION.md",
        "entry_policy": "archive entries equal every declared entry plus this manifest; no extra or missing files",
        "manifest_self_exclusion_reason": "self-hash is non-recursive and verified as an explicit archive entry",
        "entries": identities,
        "entry_count_excluding_manifest": len(identities),
        "external_sidecar_required": True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    expected = {identity["archive_path"] for identity in identities} | {
        manifest_path.name
    }
    with zipfile.ZipFile(
        package_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for archive_path, source in sorted(entries.items()):
            archive.write(source, archive_path)
        archive.write(manifest_path, manifest_path.name)

    with zipfile.ZipFile(package_path) as archive:
        actual = set(archive.namelist())
        if actual != expected:
            raise RuntimeError(
                f"entry mismatch missing={sorted(expected-actual)} extra={sorted(actual-expected)}"
            )
        for identity in identities:
            data = archive.read(str(identity["archive_path"]))
            if len(data) != identity["size_bytes"]:
                raise RuntimeError(f"size mismatch: {identity['archive_path']}")
            if hashlib.sha256(data).hexdigest() != identity["sha256"]:
                raise RuntimeError(f"hash mismatch: {identity['archive_path']}")

    package_sha = sha256(package_path)
    sidecar_path.write_text(f"{package_sha}  {package_name}\n")
    print(
        json.dumps(
            {
                "package": str(package_path),
                "sidecar": str(sidecar_path),
                "sha256": package_sha,
                "repository_commit_sha": commit,
                "archive_entry_count": len(expected),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
