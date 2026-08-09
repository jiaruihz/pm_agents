import hashlib
import gzip
import json
import subprocess
from pathlib import Path

from scripts.ops import weather_research_artifact_ctl as artifact_ctl


def test_archive_filters_exact_paths_and_removes_only_selected(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    selected = repo / "docs/analysis/2026-06/generated/old/rows.csv"
    retained = repo / "docs/analysis/2026-08/generated/current/rows.csv"
    selected.parent.mkdir(parents=True)
    retained.parent.mkdir(parents=True)
    selected.write_bytes(b"old rows\n")
    retained.write_bytes(b"current rows\n")
    artifact_root = tmp_path / "artifact_store"
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)
    monkeypatch.setattr(
        artifact_ctl,
        "select_artifacts",
        lambda: (
            [
                {
                    "path": str(selected.relative_to(repo)),
                    "size_bytes": selected.stat().st_size,
                    "git_tracked": False,
                    "repo_eligible": False,
                    "required_in_worktree": False,
                    "selected": True,
                },
                {
                    "path": str(retained.relative_to(repo)),
                    "size_bytes": retained.stat().st_size,
                    "git_tracked": False,
                    "repo_eligible": False,
                    "required_in_worktree": False,
                    "selected": True,
                },
            ],
            {
                "all_file_count": 2,
                "all_bytes": selected.stat().st_size + retained.stat().st_size,
                "selected_file_count": 2,
                "selected_bytes": selected.stat().st_size + retained.stat().st_size,
                "selected_tracked_file_count": 0,
                "selected_tracked_bytes": 0,
                "selected_untracked_file_count": 2,
                "selected_untracked_bytes": (
                    selected.stat().st_size + retained.stat().st_size
                ),
            },
        ),
    )
    monkeypatch.setattr(
        artifact_ctl,
        "load_production_spec",
        lambda: type("Spec", (), {"research_artifact_root": artifact_root})(),
    )
    monkeypatch.setattr(
        artifact_ctl,
        "git_snapshot",
        lambda root=repo: {"head": "abc", "dirty_entry_count": 2},
    )

    result = artifact_ctl.archive(
        "batch_old",
        selected_paths={str(selected.relative_to(repo))},
        apply=True,
    )

    assert result["selection"]["selected_file_count"] == 1
    assert not selected.exists()
    assert retained.read_bytes() == b"current rows\n"


def test_archive_reads_explicit_ignored_artifact_from_historical_worktree(
    tmp_path, monkeypatch
):
    control_repo = tmp_path / "control"
    control_repo.mkdir()
    historical = tmp_path / "historical"
    artifact = historical / "docs/analysis/2026-08/generated/old/summary.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"result": "kept"}\n', encoding="utf-8")
    artifact_root = tmp_path / "artifact_store"
    relative = str(artifact.relative_to(historical))
    monkeypatch.setattr(artifact_ctl, "ROOT", control_repo)
    monkeypatch.setattr(
        artifact_ctl,
        "validate_archive_source_root",
        lambda root: root.resolve(),
    )
    monkeypatch.setattr(
        artifact_ctl,
        "select_worktree_artifacts",
        lambda root, paths: (
            [
                {
                    "path": relative,
                    "size_bytes": artifact.stat().st_size,
                    "git_tracked": False,
                    "repo_eligible": False,
                    "required_in_worktree": False,
                    "selected": True,
                }
            ],
            {
                "all_file_count": 1,
                "all_bytes": artifact.stat().st_size,
                "selected_file_count": 1,
                "selected_bytes": artifact.stat().st_size,
                "selected_tracked_file_count": 0,
                "selected_tracked_bytes": 0,
                "selected_untracked_file_count": 1,
                "selected_untracked_bytes": artifact.stat().st_size,
            },
        ),
    )
    monkeypatch.setattr(
        artifact_ctl,
        "load_production_spec",
        lambda: type("Spec", (), {"research_artifact_root": artifact_root})(),
    )
    monkeypatch.setattr(
        artifact_ctl,
        "git_snapshot",
        lambda root=control_repo: {"head": "abc", "dirty_entry_count": 0},
    )

    result = artifact_ctl.archive(
        "historical_worktree",
        selected_paths={relative},
        source_root=historical,
        apply=True,
    )

    assert result["repo_root"] == str(historical.resolve())
    assert not artifact.exists()
    archived = Path(result["files"][0]["object_path"])
    assert archived.read_text(encoding="utf-8") == '{"result": "kept"}\n'


def test_archive_refuses_different_hash_for_existing_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / "docs/analysis/2026-06/generated/old/rows.csv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"new revision\n")
    artifact_root = tmp_path / "artifact_store"
    old_object = artifact_root / "objects/aa/old"
    old_object.parent.mkdir(parents=True)
    old_object.write_bytes(b"old revision\n")
    old_digest = artifact_ctl.sha256_file(old_object)
    manifest = artifact_root / "manifests/old.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "schema_version": artifact_ctl.ARTIFACT_MANIFEST_SCHEMA,
                "files": [
                    {
                        "path": str(source.relative_to(repo)),
                        "size_bytes": old_object.stat().st_size,
                        "sha256": old_digest,
                        "object_path": str(old_object),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    row = {
        "path": str(source.relative_to(repo)),
        "size_bytes": source.stat().st_size,
        "git_tracked": False,
        "repo_eligible": False,
        "required_in_worktree": False,
        "selected": True,
    }
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)
    monkeypatch.setattr(
        artifact_ctl,
        "select_artifacts",
        lambda: (
            [row],
            {
                "all_file_count": 1,
                "all_bytes": source.stat().st_size,
                "selected_file_count": 1,
                "selected_bytes": source.stat().st_size,
                "selected_tracked_file_count": 0,
                "selected_tracked_bytes": 0,
                "selected_untracked_file_count": 1,
                "selected_untracked_bytes": source.stat().st_size,
            },
        ),
    )
    monkeypatch.setattr(
        artifact_ctl,
        "load_production_spec",
        lambda: type("Spec", (), {"research_artifact_root": artifact_root})(),
    )
    monkeypatch.setattr(
        artifact_ctl,
        "git_snapshot",
        lambda root=repo: {"head": "abc", "dirty_entry_count": 1},
    )

    try:
        artifact_ctl.archive(
            "ambiguous",
            selected_paths={str(source.relative_to(repo))},
            apply=True,
        )
    except RuntimeError as exc:
        assert "ambiguous archive revision" in str(exc)
    else:
        raise AssertionError("archive must reject a second hash for one repository path")
    assert source.exists()


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


def test_external_worktree_snapshot_preserves_runtime_without_removing_source(
    tmp_path, monkeypatch
):
    control_repo = tmp_path / "control"
    control_repo.mkdir()
    historical = tmp_path / "historical"
    state = historical / "runtime/strategy/state.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"state": "historical"}\n', encoding="utf-8")
    artifact_root = tmp_path / "artifact_store"
    monkeypatch.setattr(artifact_ctl, "ROOT", control_repo)
    monkeypatch.setattr(
        artifact_ctl,
        "validate_archive_source_root",
        lambda root: root.resolve(),
    )
    monkeypatch.setattr(
        artifact_ctl,
        "load_production_spec",
        lambda: type("Spec", (), {"research_artifact_root": artifact_root})(),
    )
    monkeypatch.setattr(
        artifact_ctl,
        "git_snapshot",
        lambda root=control_repo: {"head": "abc", "dirty_entry_count": 0},
    )
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "")

    result = artifact_ctl.snapshot_worktree(
        "historical_runtime",
        apply=True,
        source_root=historical,
        selected_paths={"runtime/strategy/state.json"},
    )

    assert result["snapshot_kind"] == "dirty_worktree"
    assert state.read_text(encoding="utf-8") == '{"state": "historical"}\n'
    archived = Path(result["files"][0]["object_path"])
    assert archived.read_bytes() == state.read_bytes()


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


def test_dependency_scan_resolves_argparse_input_defaults_but_not_outputs(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    source = repo / "scripts/analysis/family/research_example.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        """import argparse
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument("--observed-detail", type=Path, default=str(ROOT / "docs/analysis/2026-07/generated/example_v1/input.csv"))
parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/analysis/2026-07/generated/example_v1/output")
""",
        encoding="utf-8",
    )
    rows = {
        "docs/analysis/2026-07/generated/example_v1/input.csv": {},
        "docs/analysis/2026-07/generated/example_v1/output/result.csv": {},
    }
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)

    result = artifact_ctl.discover_archived_dependencies([source], rows)

    assert result == {
        "scripts/analysis/family/research_example.py": [
            "docs/analysis/2026-07/generated/example_v1/input.csv"
        ]
    }


def test_dependency_scan_resolves_path_lists_and_glob_patterns(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / "scripts/analysis/family/research_example.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        '''from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
FEATURE_ROWS = [
    ROOT / "docs/analysis/2026-06/generated/atlas/feature_factory_a/reheat_feature_rows.csv",
    ROOT / "docs/analysis/2026-06/generated/atlas/feature_factory_b/reheat_feature_rows.csv",
]
SHARD_GLOB = str(ROOT / "docs/analysis/2026-06/generated/atlas/feature_factory_*/reheat_feature_rows.csv")
OUT_JSON = ROOT / "docs/analysis/2026-06/generated/atlas/result.json"
for path in FEATURE_ROWS:
    path.read_text()
rows = list(ROOT.glob(SHARD_GLOB))
''',
        encoding="utf-8",
    )
    rows = {
        "docs/analysis/2026-06/generated/atlas/feature_factory_a/reheat_feature_rows.csv": {},
        "docs/analysis/2026-06/generated/atlas/feature_factory_b/reheat_feature_rows.csv": {},
        "docs/analysis/2026-06/generated/atlas/feature_factory_c/reheat_feature_rows.csv": {},
        "docs/analysis/2026-06/generated/atlas/result.json": {},
    }
    monkeypatch.setattr(artifact_ctl, "ROOT", repo)

    result = artifact_ctl.discover_archived_dependencies([source], rows)

    assert result == {
        "scripts/analysis/family/research_example.py": [
            "docs/analysis/2026-06/generated/atlas/feature_factory_a/reheat_feature_rows.csv",
            "docs/analysis/2026-06/generated/atlas/feature_factory_b/reheat_feature_rows.csv",
            "docs/analysis/2026-06/generated/atlas/feature_factory_c/reheat_feature_rows.csv",
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
