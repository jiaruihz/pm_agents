from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ops" / "after_reboot.sh"


def _fake_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    script = repo / "scripts" / "ops" / "after_reboot.sh"
    python = repo / ".venv" / "bin" / "python"
    calls = tmp_path / "calls.log"
    script.parent.mkdir(parents=True)
    python.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, script)
    (script.parent / "weather_jrs_tmux_env.sh").write_text(
        "weather_jrs_tmux_write_probe() { return \"${FAKE_JRS_PROBE_RC:-0}\"; }\n",
        encoding="utf-8",
    )
    python.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['CALLS_LOG'], 'a', encoding='utf-8') as handle:\n"
        "    handle.write(' '.join(args) + '\\n')\n"
        "if args and args[0] == '-c':\n"
        "    sys.argv = [sys.argv[0], *args[2:]]\n"
        "    exec(args[1], {'__name__': '__main__'})\n"
        "    raise SystemExit(0)\n"
        "if any(value.endswith('weather_production_manifest.py') for value in args):\n"
        "    out = pathlib.Path(args[args.index('--json-out') + 1])\n"
        "    count = int(os.environ.get('FAKE_MANAGED_SESSION_COUNT', '0'))\n"
        "    rows = [{'session': 'managed'}] if count else []\n"
        "    out.write_text(json.dumps({'tmux_sessions': rows, 'desired': {'managed_runtime_sessions': ['managed']}}), encoding='utf-8')\n"
        "    raise SystemExit(1)\n"
        "if len(args) >= 2 and args[1] == 'plan':\n"
        "    raise SystemExit(2)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    return repo, calls


def test_auto_recovery_runs_when_preflight_reports_critical(tmp_path: Path) -> None:
    repo, calls = _fake_repo(tmp_path)

    env = os.environ.copy()
    env["CALLS_LOG"] = str(calls)
    result = subprocess.run(
        [
            str(repo / "scripts/ops/after_reboot.sh"),
            "--apply",
            "--confirm-live",
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    invoked = calls.read_text(encoding="utf-8")
    assert "weather_production_ctl.py plan" in invoked
    assert (
        "weather_production_ctl.py recover-jrs-context --apply --reason "
        "explicit post-login recovery --confirm-live"
    ) in invoked
    assert "selected mode=recover-jrs-context observed_sessions=0" in result.stdout


def test_auto_recovery_uses_reconcile_when_managed_session_exists(tmp_path: Path) -> None:
    repo, calls = _fake_repo(tmp_path)
    env = {
        **os.environ,
        "CALLS_LOG": str(calls),
        "FAKE_MANAGED_SESSION_COUNT": "1",
    }

    result = subprocess.run(
        [str(repo / "scripts/ops/after_reboot.sh"), "--apply"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    invoked = calls.read_text(encoding="utf-8")
    assert "weather_production_ctl.py reconcile --apply --reason explicit post-login recovery" in invoked
    assert "recover-jrs-context --apply" not in invoked


def test_restore_manifest_is_forwarded_to_context_recovery(tmp_path: Path) -> None:
    repo, calls = _fake_repo(tmp_path)
    restore = tmp_path / "before.json"
    restore.write_text("{}", encoding="utf-8")
    env = {**os.environ, "CALLS_LOG": str(calls)}

    result = subprocess.run(
        [
            str(repo / "scripts/ops/after_reboot.sh"),
            "--apply",
            "--confirm-live",
            "--restore-manifest",
            str(restore),
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    invoked = calls.read_text(encoding="utf-8")
    assert f"--restore-manifest {restore}" in invoked


def test_auto_route_blocks_when_existing_server_fails_jrs_probe(tmp_path: Path) -> None:
    repo, calls = _fake_repo(tmp_path)
    env = {
        **os.environ,
        "CALLS_LOG": str(calls),
        "FAKE_MANAGED_SESSION_COUNT": "1",
        "FAKE_JRS_PROBE_RC": "1",
    }

    result = subprocess.run(
        [str(repo / "scripts/ops/after_reboot.sh"), "--apply", "--confirm-live"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "refusing auto-route" in result.stderr
    invoked = calls.read_text(encoding="utf-8")
    assert "weather_production_ctl.py reconcile --apply" not in invoked
    assert "weather_production_ctl.py recover-jrs-context --apply" not in invoked
