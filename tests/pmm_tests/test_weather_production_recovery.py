from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from scripts.ops import weather_production_ctl as ctl
from src.strategies.runtime.production import (
    WeatherManagedRuntimeSpec,
    WeatherProductionSpec,
)


def _spec(
    tmp_path: Path, runtimes: tuple[WeatherManagedRuntimeSpec, ...]
) -> WeatherProductionSpec:
    return WeatherProductionSpec(
        version="test",
        host_role="test",
        operational_repo_root=tmp_path,
        canonical_db_path=tmp_path / "weather.db",
        compatibility_db_paths=(tmp_path / "compat.db",),
        data_feed_runtime_root=tmp_path / "feed",
        pm_runtime_root=tmp_path / "pm",
        canonical_tmux_socket="weather-data-feed-jrs",
        canonical_tmux_binary=tmp_path / "tmux",
        market_proxy_state_path=tmp_path / "proxy.json",
        market_proxy_default_url="http://127.0.0.1:7897",
        managed_runtimes=runtimes,
    )


def _runtime(tmp_path: Path, **overrides: object) -> WeatherManagedRuntimeSpec:
    values: dict[str, object] = {
        "instance_id": "collector",
        "tmux_session": "collector",
        "role": "collector",
        "execution_mode": "collector",
        "checkout_root": tmp_path,
        "start_script": Path("start.sh"),
        "recovery_policy": "safe",
    }
    values.update(overrides)
    return WeatherManagedRuntimeSpec(**values)


def _start_script(tmp_path: Path) -> Path:
    script = tmp_path / "start.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_recovery_preflight_rejects_incompatible_health_contract(tmp_path: Path) -> None:
    _start_script(tmp_path)
    health = tmp_path / "health.json"
    health.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    runtime = _runtime(
        tmp_path,
        health_path=health,
        expected_health_fields=(("execution_evidence.enabled", True),),
    )

    rows = ctl.collect_recovery_preflight(_spec(tmp_path, (runtime,)))

    assert rows == [
        {
            "instance_id": "collector",
            "status": "error",
            "reason": "health_contract_incompatible_with_last_artifact",
            "mismatches": [
                {
                    "field": "execution_evidence.enabled",
                    "expected": True,
                    "observed": None,
                    "missing": True,
                }
            ],
        }
    ]


def test_checkout_preflight_requires_executable_release_venv(tmp_path: Path) -> None:
    _start_script(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "start.sh"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Recovery Test",
            "-c",
            "user.email=recovery@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    runtime = _runtime(tmp_path)

    result = ctl._checkout_start_preflight(_spec(tmp_path, (runtime,)), runtime)

    assert result is not None
    assert result["reason"].startswith("checkout_bootstrap_missing_venv:")


def test_nonzero_launcher_with_live_session_enters_warming(
    monkeypatch, tmp_path: Path
) -> None:
    script = _start_script(tmp_path)
    runtime = _runtime(tmp_path)
    spec = _spec(tmp_path, (runtime,))
    monkeypatch.setattr(
        ctl.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [str(script)], 1, "health not ready\n", ""
        ),
    )
    monkeypatch.setattr(
        ctl,
        "_tmux",
        lambda *_args: subprocess.CompletedProcess([], 0, "", ""),
    )

    result = ctl._run_start(
        spec, runtime, confirm_live=False, launch_env={"PATH": os.environ["PATH"]}
    )

    assert result["status"] == "warming"
    assert result["session_present_after_start"] is True


def test_startup_convergence_requires_post_start_health_artifact(
    monkeypatch, tmp_path: Path
) -> None:
    _start_script(tmp_path)
    health = tmp_path / "health.json"
    health.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    started_at = time.time()
    os.utime(health, (started_at + 1, started_at + 1))
    runtime = _runtime(
        tmp_path,
        health_path=health,
        accepted_health_statuses=("ok",),
        startup_grace_sec=5,
    )
    spec = _spec(tmp_path, (runtime,))
    snapshot = {
        "status": "healthy",
        "findings": [],
        "generated_at_utc": "2026-08-30T00:00:00Z",
        "tmux_sessions": [
            {
                "session": "collector",
                "panes": [
                    {
                        "pane_current_path": str(tmp_path),
                        "pane_start_command": "python collector.py",
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(ctl.manifest_tool, "collect_manifest", lambda _spec: snapshot)
    actions = [
        {
            "instance_id": "collector",
            "status": "warming",
            "started_at_epoch": started_at,
        }
    ]

    ctl.wait_for_startup_convergence(spec, actions, poll_interval_sec=0)

    assert actions[0]["status"] == "started"
    assert actions[0]["converged"] is True


def test_startup_convergence_times_out_when_artifact_did_not_refresh(
    monkeypatch, tmp_path: Path
) -> None:
    _start_script(tmp_path)
    health = tmp_path / "health.json"
    health.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    old = time.time() - 60
    os.utime(health, (old, old))
    runtime = _runtime(
        tmp_path,
        health_path=health,
        accepted_health_statuses=("ok",),
        startup_grace_sec=0,
    )
    spec = _spec(tmp_path, (runtime,))
    snapshot = {
        "status": "healthy",
        "findings": [],
        "generated_at_utc": "2026-08-30T00:00:00Z",
        "tmux_sessions": [
            {
                "session": "collector",
                "panes": [
                    {
                        "pane_current_path": str(tmp_path),
                        "pane_start_command": "python collector.py",
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(ctl.manifest_tool, "collect_manifest", lambda _spec: snapshot)
    actions = [
        {
            "instance_id": "collector",
            "status": "started",
            "started_at_epoch": time.time(),
        }
    ]

    ctl.wait_for_startup_convergence(spec, actions, poll_interval_sec=0)

    assert actions[0]["status"] == "error"
    assert actions[0]["reason"] == "startup_convergence_timeout"
    assert actions[0]["artifact_refreshed"] is False


def test_startup_mtime_uses_canonical_tmux_when_local_stat_fails(
    monkeypatch, tmp_path: Path
) -> None:
    _start_script(tmp_path)
    health = tmp_path / "missing-locally.json"
    runtime = _runtime(tmp_path, health_path=health)
    spec = _spec(tmp_path, (runtime,))
    started_at = time.time()
    observed_ns = int((started_at + 1) * 1_000_000_000)
    calls: list[tuple[object, ...]] = []

    def fake_checked(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((*args, kwargs))
        return subprocess.CompletedProcess([], 0, f"{observed_ns}\n", "")

    monkeypatch.setattr(ctl, "_run_tmux_checked", fake_checked)

    assert ctl._startup_artifact_refreshed(spec, runtime, started_at) is True
    assert calls
    assert calls[0][2] == "weather_controller_health_mtime"


def test_context_recovery_aborts_when_existing_server_cannot_be_stopped(
    monkeypatch, tmp_path: Path
) -> None:
    spec = _spec(tmp_path, ())
    calls: list[tuple[str, ...]] = []

    def fake_tmux(_spec: WeatherProductionSpec, *args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ("list-sessions",):
            return subprocess.CompletedProcess(args, 0, "keeper: 1 windows\n", "")
        if args == ("kill-server",):
            return subprocess.CompletedProcess(args, 1, "permission denied\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(ctl, "_tmux", fake_tmux)
    monkeypatch.setattr(
        ctl,
        "collect_prospective_jrs_context_health",
        lambda _spec: {"status": "healthy", "returncode": 0, "output": ""},
    )

    try:
        ctl.recover_jrs_context(
            spec, {"tmux_sessions": []}, confirm_live=True
        )
    except RuntimeError as exc:
        assert "failed to stop canonical tmux server" in str(exc)
    else:
        raise AssertionError("expected kill-server failure")

    assert not any(call and call[0] == "new-session" for call in calls)


def test_context_recovery_accepts_already_absent_server(
    monkeypatch, tmp_path: Path
) -> None:
    spec = _spec(tmp_path, ())
    calls: list[tuple[str, ...]] = []

    def fake_tmux(_spec: WeatherProductionSpec, *args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ("list-sessions",):
            return subprocess.CompletedProcess(args, 1, "no server running\n", "")
        if args and args[0] == "new-session":
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(ctl, "_tmux", fake_tmux)
    monkeypatch.setattr(
        ctl,
        "collect_prospective_jrs_context_health",
        lambda _spec: {"status": "healthy", "returncode": 0, "output": ""},
    )
    monkeypatch.setattr(
        ctl,
        "collect_jrs_context_health",
        lambda _spec: {"status": "healthy", "returncode": 0, "output": ""},
    )

    actions = ctl.recover_jrs_context(
        spec, {"tmux_sessions": []}, confirm_live=True
    )

    kill = next(action for action in actions if action["action"] == "kill_server")
    assert kill["skipped"] is True
    assert ("kill-server",) not in calls
