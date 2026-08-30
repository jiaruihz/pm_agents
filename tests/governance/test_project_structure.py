import subprocess
from pathlib import Path

from scripts.ops.check_project_structure import (
    _audit_reviews,
    _audit_roots,
    audit_project,
    tracked_files,
)


ROOT = Path(__file__).resolve().parents[2]


def test_repository_structure_contract_has_no_errors() -> None:
    report = audit_project(ROOT, ROOT / "configs/project_structure.yaml")

    assert report["counts"]["errors"] == 0, report["findings"]
    assert report["metrics"]["skills"]["skill_count"] == 15
    assert report["metrics"]["reviews"]["visible_untracked_files"] == 0
    assert report["metrics"]["reviews"]["physical_files"] is None


def test_review_workspace_ignores_new_packets_but_keeps_boundary_readme() -> None:
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "reviews/new_packet/evidence.parquet"],
        cwd=ROOT,
        check=False,
    )
    readme = subprocess.run(
        ["git", "check-ignore", "--quiet", "reviews/README.md"],
        cwd=ROOT,
        check=False,
    )

    assert ignored.returncode == 0
    assert readme.returncode == 1


def _init_test_repository(path: Path, ignore: str) -> set[str]:
    subprocess.run(["git", "init", "--quiet"], cwd=path, check=True)
    (path / ".gitignore").write_text(ignore, encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=path, check=True)
    return tracked_files(path)


def test_root_audit_catches_untracked_files_and_respects_explicit_exceptions(
    tmp_path: Path,
) -> None:
    tracked = _init_test_repository(tmp_path, "ignored.cache\n")
    (tmp_path / "ignored.cache").write_text("ignored", encoding="utf-8")
    (tmp_path / "pending.txt").write_text("registered debt", encoding="utf-8")
    (tmp_path / "mystery.py").write_text("unknown", encoding="utf-8")
    config = {
        "tracked_root_directories": {},
        "tracked_root_files": [".gitignore"],
        "transient_roots": [],
        "workspace_only_roots": [],
        "legacy_or_workspace_config_roots": [],
        "legacy_untracked_root_files": {"pending.txt": "owner pending"},
    }

    findings, metrics = _audit_roots(tmp_path, config, tracked)
    by_path = {item.path: item for item in findings}

    assert by_path["pending.txt"].code == "legacy_untracked_root_file"
    assert by_path["pending.txt"].severity == "warning"
    assert by_path["mystery.py"].code == "unclassified_workspace_file"
    assert "ignored.cache" not in by_path
    assert metrics["physical_root_files"] == 4


def test_root_audit_ignores_linked_worktree_git_pointer(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text(
        "gitdir: /tmp/example/worktrees/review\n", encoding="utf-8"
    )
    config = {
        "tracked_root_directories": {},
        "tracked_root_files": [],
        "transient_roots": [],
        "workspace_only_roots": [],
        "legacy_or_workspace_config_roots": [],
        "legacy_untracked_root_files": {},
    }

    findings, metrics = _audit_roots(tmp_path, config, set())

    assert findings == []
    assert metrics["physical_root_files"] == 0


def test_review_physical_inventory_is_explicitly_deep(tmp_path: Path) -> None:
    tracked = _init_test_repository(tmp_path, "reviews/**\n")
    review_root = tmp_path / "reviews" / "packet"
    review_root.mkdir(parents=True)
    (review_root / "evidence.bin").write_bytes(b"abc")
    config = {
        "review_workspace": {
            "root": "reviews",
            "metadata_allowlist": [],
            "max_legacy_tracked_files": 0,
            "max_legacy_tracked_bytes": 0,
        }
    }

    findings, metrics = _audit_reviews(tmp_path, config, tracked)
    assert findings == []
    assert metrics["physical_files"] is None
    assert metrics["physical_bytes"] is None

    findings, metrics = _audit_reviews(tmp_path, config, tracked, deep=True)
    assert findings == []
    assert metrics["physical_files"] == 1
    assert metrics["physical_bytes"] == 3
