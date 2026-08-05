import hashlib
import json
from pathlib import Path

from scripts.ops import weather_research_artifact_ctl as artifact_ctl


def test_restore_recreates_content_addressed_artifact(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    content = b"research evidence\n"
    digest = hashlib.sha256(content).hexdigest()
    archived = tmp_path / "objects" / digest[:2] / digest
    archived.parent.mkdir(parents=True)
    archived.write_bytes(content)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "pm_agents_research_artifact_manifest_v1",
                "files": [
                    {
                        "path": "docs/analysis/generated/evidence.csv",
                        "size_bytes": len(content),
                        "sha256": digest,
                        "object_path": str(archived),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)

    result = artifact_ctl.restore(
        manifest,
        selected_paths={"docs/analysis/generated/evidence.csv"},
        apply=True,
    )

    restored = repo / "docs/analysis/generated/evidence.csv"
    assert restored.read_bytes() == content
    assert result["restored_file_count"] == 1


def test_restore_refuses_to_overwrite_different_content(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    destination = repo / "docs/analysis/generated/evidence.csv"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"different")
    content = b"archived"
    digest = hashlib.sha256(content).hexdigest()
    archived = tmp_path / "objects" / digest[:2] / digest
    archived.parent.mkdir(parents=True)
    archived.write_bytes(content)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "pm_agents_research_artifact_manifest_v1",
                "files": [
                    {
                        "path": "docs/analysis/generated/evidence.csv",
                        "size_bytes": len(content),
                        "sha256": digest,
                        "object_path": str(archived),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)

    try:
        artifact_ctl.restore(
            manifest,
            selected_paths=set(),
            apply=True,
        )
    except RuntimeError as exc:
        assert "refusing to overwrite" in str(exc)
    else:
        raise AssertionError("restore must fail closed on a different destination")


def test_restore_recreates_symlink_from_snapshot(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "pm_agents_research_artifact_manifest_v1",
                "files": [
                    {
                        "path": "docs/analysis/generated/source.jsonl.gz",
                        "kind": "symlink",
                        "link_target": "/Volumes/jrs/research/source.jsonl.gz",
                        "size_bytes": 42,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)

    result = artifact_ctl.restore(manifest, selected_paths=set(), apply=True)

    restored = repo / "docs/analysis/generated/source.jsonl.gz"
    assert restored.is_symlink()
    assert restored.readlink() == Path("/Volumes/jrs/research/source.jsonl.gz")
    assert result["restored_file_count"] == 1
