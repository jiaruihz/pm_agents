from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.ops import verify_repo


def test_profiles_expand_without_silently_dropping_maintained_suites() -> None:
    fast = verify_repo.validation_commands("fast", "python")
    maintained = verify_repo.validation_commands("maintained", "python")
    full = verify_repo.validation_commands("full", "python")

    assert maintained[: len(fast)] == fast
    assert full[: len(maintained)] == maintained
    maintained_paths = set(maintained[-1])
    assert set(verify_repo.MAINTAINED_TEST_PATHS) <= maintained_paths
    assert {
        f"--ignore={path}" for path in verify_repo.RELEASE_CONVERGENCE_TEST_PATHS
    } <= maintained_paths
    assert any(
        set(verify_repo.RELEASE_CONVERGENCE_TEST_PATHS) <= set(command)
        for command in full
    )
    assert any(
        token.endswith("check_weather_docs.py") for command in full for token in command
    )
    assert any("tests/research_tests" in command for command in full)


def test_maintained_profile_references_only_committed_paths() -> None:
    paths = (
        *verify_repo.MAINTAINED_TEST_PATHS,
        *verify_repo.RELEASE_CONVERGENCE_TEST_PATHS,
    )
    for path in paths:
        completed = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", path],
            cwd=verify_repo.ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, path


def test_runner_stops_at_first_failure_and_sets_isolated_python_env(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def fake_run(command, *, cwd, env, check):
        calls.append((command, cwd, env))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(verify_repo.subprocess, "run", fake_run)

    assert verify_repo.run_profile("full", repo_root=tmp_path, python="python") == 7
    assert len(calls) == 1
    assert calls[0][1] == tmp_path
    assert calls[0][2]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert calls[0][2]["PYTHONPATH"].split(":", 1)[0] == str(tmp_path)
