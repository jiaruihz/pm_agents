"""Shared output contract for versioned research-machine artifacts."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from src.strategies.runtime.production import load_production_spec


RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def resolve_run_output(
    family: str,
    *,
    run_id: str | None,
    explicit_output: Path | None,
    artifact_root: Path | None = None,
) -> Path:
    """Resolve one immutable run directory; explicit test/temp paths are allowed."""
    if explicit_output is not None:
        return explicit_output
    if not run_id or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(
            "a stable --run-id is required when --output-dir/--out is omitted"
        )
    root = artifact_root or load_production_spec().research_artifact_root
    return root / "active" / family / run_id


def prepare_new_run_output(path: Path) -> Path:
    """Create a run directory, refusing to overwrite any prior run output."""
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty research run: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_content_addressed_artifact(
    sha256: str,
    *,
    artifact_root: Path | None = None,
) -> Path:
    """Resolve and verify one immutable artifact-store object by content SHA."""
    if not SHA256_PATTERN.fullmatch(sha256):
        raise ValueError(f"invalid artifact sha256: {sha256!r}")
    root = artifact_root or load_production_spec().research_artifact_root
    path = root / "objects" / sha256[:2] / sha256
    if not path.is_file():
        raise FileNotFoundError(f"content-addressed research artifact missing: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != sha256:
        raise ValueError(
            f"content-addressed research artifact hash mismatch: {path} "
            f"expected={sha256} actual={actual}"
        )
    return path
