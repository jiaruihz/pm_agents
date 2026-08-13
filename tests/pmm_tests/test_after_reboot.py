from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ops" / "after_reboot.sh"


def test_explicit_recovery_runs_when_preflight_reports_critical(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    script = repo / "scripts" / "ops" / "after_reboot.sh"
    python = repo / ".venv" / "bin" / "python"
    calls = tmp_path / "calls.log"
    script.parent.mkdir(parents=True)
    python.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, script)
    python.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CALLS_LOG\"\n"
        "case \"$*\" in\n"
        "  *weather_production_manifest.py*) exit 2 ;;\n"
        "  *'weather_production_ctl.py plan'*) exit 2 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    python.chmod(0o755)

    env = os.environ.copy()
    env["CALLS_LOG"] = str(calls)
    result = subprocess.run(
        [
            str(script),
            "--recover-jrs-context",
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
