#!/usr/bin/env python3
"""Build a strict, self-auditing WCIR review-stage zip."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage_dir", type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--timestamp")
    args = parser.parse_args()
    stage = args.stage_dir.resolve()
    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=stage, text=True
    ).strip()
    manifest_path = stage / "EVIDENCE_MANIFEST.json"
    package_name = f"wcir-next-print-{args.label}-{timestamp}.zip"
    package_path = stage / package_name
    sidecar_path = stage / f"{package_name}.sha256"

    payload = sorted(
        path
        for path in stage.rglob("*")
        if path.is_file()
        and path != manifest_path
        and path != package_path
        and path != sidecar_path
        and path.suffix not in {".zip"}
        and not path.name.endswith(".zip.sha256")
        and "__pycache__" not in path.parts
    )
    manifest = {
        "schema_version": "wcir_review_evidence_manifest_v1",
        "stage": args.label,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "commit_sha": commit,
        "entry_policy": "zip entries must equal every manifested payload entry plus EVIDENCE_MANIFEST.json; no extras",
        "external_sidecar_required": True,
        "payload_entries": [identity(path, stage) for path in payload],
        "payload_entry_count": len(payload),
        "manifest_self_exclusion_reason": "self-hash is not recursive; manifest is verified as an explicit zip entry",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    expected = payload + [manifest_path]
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in expected:
            archive.write(path, path.relative_to(stage).as_posix())

    expected_names = {path.relative_to(stage).as_posix() for path in expected}
    with zipfile.ZipFile(package_path) as archive:
        actual_names = set(archive.namelist())
        if actual_names != expected_names:
            raise RuntimeError(
                f"package entry mismatch missing={sorted(expected_names-actual_names)} "
                f"extra={sorted(actual_names-expected_names)}"
            )
        for entry in manifest["payload_entries"]:
            payload_bytes = archive.read(str(entry["path"]))
            if len(payload_bytes) != entry["size_bytes"]:
                raise RuntimeError(f"package size mismatch: {entry['path']}")
            if hashlib.sha256(payload_bytes).hexdigest() != entry["sha256"]:
                raise RuntimeError(f"package hash mismatch: {entry['path']}")
    digest = sha(package_path)
    sidecar_path.write_text(f"{digest}  {package_name}\n")
    print(json.dumps({
        "package": str(package_path),
        "sidecar": str(sidecar_path),
        "commit_sha": commit,
        "entry_count": len(expected),
        "sha256": digest,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
