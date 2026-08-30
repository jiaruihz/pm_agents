from __future__ import annotations

import fcntl
from pathlib import Path
import subprocess

import pytest

from scripts.ops.weather_production_release import (
    ReleaseError,
    _resolve_release_namespace,
    _resolve_release_root,
    run_release,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, text=True, capture_output=True).stdout.strip()


def _commit(repo: Path, text: str) -> str:
    (repo / "tracked.txt").write_text(text)
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", text)
    return _git(repo, "rev-parse", "HEAD")


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    old = _commit(repo, "old")
    (repo / ".venv").mkdir()
    releases = tmp_path / "releases"
    old_checkout = releases / "control_plane" / old
    old_checkout.parent.mkdir(parents=True)
    spec = repo / "src/strategies/runtime/production.yaml"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "operational_repo_root: " + str(repo) + "\n"
        "production_release_root: " + str(releases) + "\n"
        "production_releases:\n"
        "  - release_id: control_plane\n"
        "    checkout_root: " + str(old_checkout) + "\n"
        "    expected_repo_sha: " + old + "\n"
    )
    _git(repo, "add", str(spec.relative_to(repo)))
    _git(repo, "commit", "-m", "production config")
    new = _commit(repo, "new")
    _git(repo, "worktree", "add", "--detach", str(old_checkout), old)
    return repo, spec, old, new


def test_dry_run_does_not_create_or_repin(tmp_path: Path) -> None:
    _, spec, old, new = _fixture(tmp_path)
    original = spec.read_text()
    result = run_release(release_id="control_plane", source_sha=new, expected_old_sha=old, production_spec=spec, apply=False, confirm_production=False, reason="")
    assert result["mode"] == "dry_run"
    assert result["pin_updated"] is False
    assert not Path(result["new"]["checkout_root"]).exists()
    assert spec.read_text() == original


def test_expected_old_mismatch_is_rejected(tmp_path: Path) -> None:
    _, spec, old, new = _fixture(tmp_path)
    with pytest.raises(ReleaseError, match="expected-old-sha mismatch"):
        run_release(release_id="control_plane", source_sha=new, expected_old_sha="0" * 40, production_spec=spec, apply=False, confirm_production=False, reason="")
    assert old in spec.read_text()


def test_apply_is_idempotent_and_preserves_symlink_and_text(tmp_path: Path) -> None:
    repo, spec, old, new = _fixture(tmp_path)
    result = run_release(release_id="control_plane", source_sha=new, expected_old_sha=old, production_spec=spec, apply=True, confirm_production=True, reason="test release")
    target = Path(result["new"]["checkout_root"])
    assert result["pin_updated"] is True and result["worktree_created"] is True
    assert _git(target, "rev-parse", "HEAD") == new
    assert (target / ".venv").is_symlink()
    assert (target / ".venv").resolve() == (repo / ".venv").resolve()
    text = spec.read_text()
    assert str(target) in text and new in text and old not in text
    again = run_release(release_id="control_plane", source_sha=new, expected_old_sha=new, production_spec=spec, apply=True, confirm_production=True, reason="idempotent test")
    assert again["worktree_created"] is False
    assert again["pin_updated"] is True


def test_non_descendant_source_is_rejected(tmp_path: Path) -> None:
    repo, spec, old, new = _fixture(tmp_path)
    _git(repo, "checkout", "-B", "operational", new)
    _git(repo, "checkout", "-b", "side", old)
    side = _commit(repo, "side")
    _git(repo, "checkout", "operational")
    with pytest.raises(ReleaseError, match="not a descendant"):
        run_release(release_id="control_plane", source_sha=side, expected_old_sha=old, production_spec=spec, apply=False, confirm_production=False, reason="")


def test_apply_requires_explicit_confirmation_and_reason(tmp_path: Path) -> None:
    _, spec, old, new = _fixture(tmp_path)
    with pytest.raises(ReleaseError, match="requires --confirm-production"):
        run_release(release_id="control_plane", source_sha=new, expected_old_sha=old, production_spec=spec, apply=True, confirm_production=False, reason="")


def test_apply_rejects_preexisting_production_spec_changes(tmp_path: Path) -> None:
    _, spec, old, new = _fixture(tmp_path)
    spec.write_text(spec.read_text() + "unrelated: user-change\n")

    with pytest.raises(ReleaseError, match="pre-existing changes"):
        run_release(
            release_id="control_plane",
            source_sha=new,
            expected_old_sha=old,
            production_spec=spec,
            apply=True,
            confirm_production=True,
            reason="must not overwrite user work",
        )


def test_rejects_production_spec_copy_outside_operational_repo(
    tmp_path: Path,
) -> None:
    _, spec, old, new = _fixture(tmp_path)
    copied = tmp_path / "production-copy.yaml"
    copied.write_text(spec.read_text())

    with pytest.raises(ReleaseError, match="operational repository production.yaml"):
        run_release(
            release_id="control_plane",
            source_sha=new,
            expected_old_sha=old,
            production_spec=copied,
            apply=False,
            confirm_production=False,
            reason="",
        )


def test_release_root_rejects_symlink_components(tmp_path: Path) -> None:
    real_root = tmp_path / "real-releases"
    real_root.mkdir()
    alias = tmp_path / "release-alias"
    alias.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ReleaseError, match="symlink component"):
        _resolve_release_root(alias)

    namespace_target = real_root / "real-control-plane"
    namespace_target.mkdir()
    (real_root / "control_plane").symlink_to(
        namespace_target, target_is_directory=True
    )
    with pytest.raises(ReleaseError, match="namespace must not be a symlink"):
        _resolve_release_namespace(real_root, "control_plane")


def test_checkout_rejects_non_runtime_untracked_files(tmp_path: Path) -> None:
    _, spec, old, new = _fixture(tmp_path)
    old_checkout = tmp_path / "releases/control_plane" / old
    (old_checkout / "unexpected.py").write_text("raise RuntimeError\n")

    with pytest.raises(ReleaseError, match="unsafe tracked/untracked changes"):
        run_release(
            release_id="control_plane",
            source_sha=new,
            expected_old_sha=old,
            production_spec=spec,
            apply=False,
            confirm_production=False,
            reason="",
        )


def test_apply_refuses_concurrent_release_operation(tmp_path: Path) -> None:
    repo, spec, old, new = _fixture(tmp_path)
    lock_path = repo / ".git/weather-production-release.lock"
    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ReleaseError, match="holds the lock"):
            run_release(
                release_id="control_plane",
                source_sha=new,
                expected_old_sha=old,
                production_spec=spec,
                apply=True,
                confirm_production=True,
                reason="concurrency test",
            )
    assert old in spec.read_text()
