from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.ops.polymarket_dispute_repricing_artifacts import install, load_manifest, verify_source
from scripts.ops.polymarket_dispute_forward import verify_model_artifact


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_committed_manifest_verifies_current_model_and_summary() -> None:
    manifest_path = Path("configs/dispute_repricing/zero_notional_shadow_v1.json")
    manifest = load_manifest(manifest_path)
    result = verify_source(manifest, Path.cwd())
    assert result["status"] == "verified"
    assert result["live_authority"] is False
    assert len(result["artifacts"]) == 2


def test_install_is_atomic_idempotent_and_refuses_conflicting_artifact(tmp_path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    model_source = source_root / "model.joblib"
    summary_source = source_root / "summary.json"
    source_root.mkdir()
    original_manifest = load_manifest(
        Path("configs/dispute_repricing/zero_notional_shadow_v1.json")
    )
    real_model = Path(original_manifest["model"]["source_path"])
    model_source.write_bytes(real_model.read_bytes())
    summary_source.write_text(json.dumps({"evidence": "test"}))
    manifest = {
        "execution_mode": "zero_notional_shadow",
        "live_authority": False,
        "model": {
            "source_path": "model.joblib",
            "install_path": "installed/model.joblib",
            "sha256": _sha(model_source),
        },
        "research_summary": {
            "source_path": "summary.json",
            "install_path": "installed/summary.json",
            "sha256": _sha(summary_source),
        },
    }
    first = install(manifest, source_root, target_root)
    second = install(manifest, source_root, target_root)
    assert {row["status"] for row in first["installed"]} == {"installed"}
    assert {row["status"] for row in second["installed"]} == {"already_current"}
    (target_root / "installed/summary.json").write_text("conflict")
    with pytest.raises(RuntimeError, match="refusing to overwrite conflicting"):
        install(manifest, source_root, target_root)


def test_runtime_model_hash_fails_closed(tmp_path) -> None:
    path = tmp_path / "model.bin"
    path.write_bytes(b"model")
    actual = verify_model_artifact(path, None)
    assert actual == _sha(path)
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        verify_model_artifact(path, "0" * 64)
