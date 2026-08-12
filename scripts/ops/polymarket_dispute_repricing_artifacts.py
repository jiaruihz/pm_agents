#!/usr/bin/env python3
"""Verify or atomically install immutable dispute-repricing shadow artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

import joblib


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "configs/dispute_repricing/zero_notional_shadow_v1.json"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if payload.get("live_authority") is not False:
        raise ValueError("artifact manifest must explicitly deny live authority")
    if payload.get("execution_mode") != "zero_notional_shadow":
        raise ValueError("artifact manifest execution mode is not zero_notional_shadow")
    return payload


def artifact_items(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [("model", manifest["model"]), ("research_summary", manifest["research_summary"])]


def verify_source(manifest: dict[str, Any], source_root: Path) -> dict[str, Any]:
    verified: list[dict[str, Any]] = []
    for name, item in artifact_items(manifest):
        path = source_root / str(item["source_path"])
        if not path.is_file():
            raise FileNotFoundError(f"missing {name} artifact: {path}")
        actual = file_sha256(path)
        if actual != str(item["sha256"]).lower():
            raise RuntimeError(
                f"{name} sha256 mismatch: expected={item['sha256']} actual={actual}"
            )
        verified.append({"name": name, "path": str(path), "sha256": actual})
    model_path = source_root / str(manifest["model"]["source_path"])
    model = joblib.load(model_path)
    if not callable(getattr(model, "predict_proba", None)):
        raise TypeError("dispute model artifact does not expose predict_proba")
    return {
        "schema_version": "dispute_repricing_artifact_verification_v1",
        "status": "verified",
        "execution_mode": "zero_notional_shadow",
        "live_authority": False,
        "artifacts": verified,
    }


def install(manifest: dict[str, Any], source_root: Path, target_root: Path) -> dict[str, Any]:
    verification = verify_source(manifest, source_root)
    installed: list[dict[str, Any]] = []
    for name, item in artifact_items(manifest):
        source = source_root / str(item["source_path"])
        target = target_root / str(item["install_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            actual = file_sha256(target)
            if actual != str(item["sha256"]).lower():
                raise RuntimeError(
                    f"refusing to overwrite conflicting {name} artifact: {target}: {actual}"
                )
            installed.append({"name": name, "path": str(target), "status": "already_current"})
            continue
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            if file_sha256(temporary) != str(item["sha256"]).lower():
                raise RuntimeError(f"copied {name} artifact failed sha256 verification")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        installed.append({"name": name, "path": str(target), "status": "installed"})
    return {**verification, "status": "installed", "target_root": str(target_root), "installed": installed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "install"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--target-root", type=Path)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    if args.action == "verify":
        result = verify_source(manifest, args.source_root)
    else:
        if args.target_root is None:
            parser.error("install requires --target-root")
        result = install(manifest, args.source_root, args.target_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
