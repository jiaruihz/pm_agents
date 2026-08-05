import hashlib
import gzip
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


def test_dependency_scan_resolves_split_path_joins_and_directory_reads(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    source = repo / "scripts/analysis/family/research_example.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        """from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
INPUT_ROOT = ROOT / "docs/analysis/2026-07" / "generated/example_v1"
INPUT = INPUT_ROOT / "input.csv"
rows = INPUT.read_text()
children = list(INPUT_ROOT.iterdir())
""",
        encoding="utf-8",
    )
    rows = {
        "docs/analysis/2026-07/generated/example_v1/input.csv": {},
        "docs/analysis/2026-07/generated/example_v1/nested/labels.csv": {},
        "docs/analysis/2026-07/generated/unrelated/output.csv": {},
    }
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)

    result = artifact_ctl.discover_archived_dependencies([source], rows)

    assert result == {
        "scripts/analysis/family/research_example.py": [
            "docs/analysis/2026-07/generated/example_v1/input.csv",
            "docs/analysis/2026-07/generated/example_v1/nested/labels.csv",
        ]
    }


def test_restore_dependencies_uses_minimal_script_plan(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    content = b"archived input\n"
    digest = hashlib.sha256(content).hexdigest()
    archived = tmp_path / "objects" / digest
    archived.parent.mkdir()
    archived.write_bytes(content)
    row = {
        "path": "docs/analysis/generated/input.csv",
        "size_bytes": len(content),
        "sha256": digest,
        "object_path": str(archived),
    }
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)
    monkeypatch.setattr(
        artifact_ctl,
        "dependency_plan",
        lambda scripts: (
            {
                "affected_script_count": 1,
                "archived_file_count": 1,
                "archived_bytes": len(content),
                "missing_in_worktree_count": 1,
                "scripts": {next(iter(scripts)): [row["path"]]},
            },
            [row],
        ),
    )

    result = artifact_ctl.restore_dependencies(
        {"scripts/analysis/example.py"}, apply=True
    )

    assert (repo / row["path"]).read_bytes() == content
    assert result["restored_file_count"] == 1


def test_prune_corrupt_tombstones_bad_gzip_and_deletes_unique_object(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact_root = tmp_path / "artifact_store"
    object_path = artifact_root / "objects" / "aa" / ("a" * 64)
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(b"not a complete gzip stream")
    digest = artifact_ctl.sha256_file(object_path)
    manifest = artifact_root / "manifests" / "archive.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "schema_version": artifact_ctl.ARTIFACT_MANIFEST_SCHEMA,
                "files": [
                    {
                        "path": "docs/analysis/generated/broken.jsonl.gz",
                        "size_bytes": object_path.stat().st_size,
                        "sha256": digest,
                        "object_path": str(object_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)

    result = artifact_ctl.prune_corrupt(
        {"docs/analysis/generated/broken.jsonl.gz"},
        run_id="prune_bad_gzip",
        apply=True,
        artifact_root=artifact_root,
    )

    assert result["deleted_object_bytes"] == len(b"not a complete gzip stream")
    assert not object_path.exists()
    assert artifact_ctl.archived_artifact_rows(artifact_root) == {}
    tombstone = json.loads(
        (artifact_root / "manifests/prune_bad_gzip.json").read_text()
    )
    assert tombstone["tombstones"][0]["reason"] == "gzip_integrity_failure"
    try:
        artifact_ctl.restore(
            manifest,
            selected_paths={"docs/analysis/generated/broken.jsonl.gz"},
            apply=False,
        )
    except ValueError as exc:
        assert "intentionally tombstoned" in str(exc)
    else:
        raise AssertionError("tombstoned evidence must not be offered for restore")


def test_prune_corrupt_refuses_valid_gzip(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact_root = tmp_path / "artifact_store"
    object_path = artifact_root / "objects" / "bb" / ("b" * 64)
    object_path.parent.mkdir(parents=True)
    with gzip.open(object_path, "wb") as handle:
        handle.write(b"valid evidence")
    digest = artifact_ctl.sha256_file(object_path)
    manifest = artifact_root / "manifests" / "archive.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "schema_version": artifact_ctl.ARTIFACT_MANIFEST_SCHEMA,
                "files": [
                    {
                        "path": "docs/analysis/generated/valid.jsonl.gz",
                        "size_bytes": object_path.stat().st_size,
                        "sha256": digest,
                        "object_path": str(object_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)

    try:
        artifact_ctl.prune_corrupt(
            {"docs/analysis/generated/valid.jsonl.gz"},
            run_id="must_not_prune",
            apply=True,
            artifact_root=artifact_root,
        )
    except ValueError as exc:
        assert "valid gzip" in str(exc)
    else:
        raise AssertionError("valid gzip must never be pruned")


def test_prune_reproduced_records_exact_replay_and_deletes_unique_object(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    producer = repo / "scripts/analysis/research_example.py"
    producer.parent.mkdir(parents=True)
    producer.write_text("print('replay')\n", encoding="utf-8")
    relative = "docs/analysis/generated/example/rows.csv"
    content = b"value\n1\n"
    digest = hashlib.sha256(content).hexdigest()
    artifact_root = tmp_path / "artifact_store"
    object_path = artifact_root / "objects" / digest[:2] / digest
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(content)
    manifest = artifact_root / "manifests/archive.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "schema_version": artifact_ctl.ARTIFACT_MANIFEST_SCHEMA,
                "files": [
                    {
                        "path": relative,
                        "size_bytes": len(content),
                        "sha256": digest,
                        "object_path": str(object_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    reproduced_root = tmp_path / "clean_checkout"
    reproduced_producer = reproduced_root / "scripts/analysis/research_example.py"
    reproduced_producer.parent.mkdir(parents=True)
    reproduced_producer.write_bytes(producer.read_bytes())
    reproduced = reproduced_root / relative
    reproduced.parent.mkdir(parents=True)
    reproduced.write_bytes(content)
    runtime_db = tmp_path / "weather.db"
    runtime_db.write_bytes(b"mutable canonical")
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)
    monkeypatch.setattr(
        artifact_ctl,
        "git_file_at_revision",
        lambda revision, relative: producer.read_bytes(),
    )
    monkeypatch.setattr(
        artifact_ctl,
        "dependency_plan",
        lambda scripts: (
            {
                "affected_script_count": 0,
                "archived_file_count": 0,
                "archived_bytes": 0,
                "missing_in_worktree_count": 0,
                "scripts": {},
            },
            [],
        ),
    )

    result = artifact_ctl.prune_reproduced(
        {relative},
        run_id="replay_exact",
        producer="scripts/analysis/research_example.py",
        reproduced_root=reproduced_root,
        code_revision="abc123",
        runtime_identity_inputs=(runtime_db,),
        replay_args=("--max-files", "10"),
        apply=True,
        artifact_root=artifact_root,
    )

    assert result["deleted_object_bytes"] == len(content)
    assert result["reproduction_proof"]["match"] == "sha256_exact"
    assert result["reproduction_proof"]["replay_command"].endswith("--max-files 10")
    assert result["reproduction_proof"]["runtime_inputs"][0]["kind"] == "mutable_file_identity"
    assert result["tombstones"][0]["reason"] == "exact_clean_reproduction"
    assert not object_path.exists()
    assert artifact_ctl.archived_artifact_rows(artifact_root) == {}


def test_prune_reproduced_refuses_hash_mismatch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    producer = repo / "scripts/analysis/research_example.py"
    producer.parent.mkdir(parents=True)
    producer.write_text("print('replay')\n", encoding="utf-8")
    relative = "docs/analysis/generated/example/rows.csv"
    content = b"original\n"
    digest = hashlib.sha256(content).hexdigest()
    artifact_root = tmp_path / "artifact_store"
    object_path = artifact_root / "objects" / digest[:2] / digest
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(content)
    manifest = artifact_root / "manifests/archive.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "schema_version": artifact_ctl.ARTIFACT_MANIFEST_SCHEMA,
                "files": [
                    {
                        "path": relative,
                        "size_bytes": len(content),
                        "sha256": digest,
                        "object_path": str(object_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    reproduced_root = tmp_path / "clean_checkout"
    reproduced_producer = reproduced_root / "scripts/analysis/research_example.py"
    reproduced_producer.parent.mkdir(parents=True)
    reproduced_producer.write_bytes(producer.read_bytes())
    reproduced = reproduced_root / relative
    reproduced.parent.mkdir(parents=True)
    reproduced.write_bytes(b"different\n")
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)
    monkeypatch.setattr(
        artifact_ctl,
        "git_file_at_revision",
        lambda revision, relative: producer.read_bytes(),
    )
    monkeypatch.setattr(
        artifact_ctl,
        "dependency_plan",
        lambda scripts: ({"scripts": {}}, []),
    )

    try:
        artifact_ctl.prune_reproduced(
            {relative},
            run_id="must_not_prune",
            producer="scripts/analysis/research_example.py",
            reproduced_root=reproduced_root,
            code_revision="abc123",
            apply=True,
            artifact_root=artifact_root,
        )
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("non-identical replay must never be pruned")
