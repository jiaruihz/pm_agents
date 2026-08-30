from pathlib import Path

from weather_model_evaluation import ReplayRunIdentity, verify_replay_artifacts
from weather_model_evaluation.dataset_manifest import sha256_file


def test_verifier_requires_real_managed_file_hashes(tmp_path: Path):
    artifact = tmp_path / "answer.json"; artifact.write_text("{}")
    verdict = verify_replay_artifacts([
        {"path": str(artifact), "sha256": sha256_file(artifact)},
        {"path": str(tmp_path / "gone"), "sha256": "a" * 64},
        {"path": str(artifact), "source_snapshot_hash": "b" * 64},
        {"legacy": True},
    ], managed_roots=[tmp_path])
    assert [item["status"] for item in verdict["artifacts"]] == ["verified", "missing", "unverifiable", "legacy_unbound"]
    assert verdict["status"] == "blocked_missing_artifact"
    assert len(verdict["verifier_build_id"]) == 64


def test_replay_identity_is_content_addressed():
    identity = ReplayRunIdentity("dataset", "split", "catalog", "code", ("a" * 64,))
    assert len(identity.identity_hash) == 64
