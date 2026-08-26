from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest


SANDBOX_EXEC = shutil.which("sandbox-exec")
pytestmark = pytest.mark.skipif(
    platform.system() != "Darwin" or SANDBOX_EXEC is None,
    reason="OS-level proof uses the macOS sandbox available on the production host family",
)


def _sandboxed(code: str, *, deny_process_exec: bool = False) -> subprocess.CompletedProcess[str]:
    assert SANDBOX_EXEC is not None
    policy = "(version 1) (allow default) (deny network*)"
    if deny_process_exec:
        policy += " (deny process-exec*)"
    safe_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
    }
    return subprocess.run(
        [SANDBOX_EXEC, "-p", policy, sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=safe_env,
    )


def test_os_sandbox_denies_raw_network_connect() -> None:
    result = _sandboxed(
        "import socket,sys\n"
        "try:\n"
        " s=socket.socket(); s.settimeout(0.2); s.connect(('1.1.1.1',443))\n"
        "except PermissionError as exc:\n"
        " print('NETWORK_DENIED', exc.errno); sys.exit(0)\n"
        "except OSError as exc:\n"
        " print('WRONG_OS_ERROR', type(exc).__name__, exc.errno); sys.exit(2)\n"
        "sys.exit(3)\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NETWORK_DENIED 1" in result.stdout


def test_os_process_deny_profile_fails_closed_before_exec() -> None:
    result = _sandboxed(
        "raise SystemExit('initial process must not execute')\n",
        deny_process_exec=True,
    )
    assert result.returncode == 71
    assert "Operation not permitted" in result.stderr


def test_os_sandbox_receives_explicit_secret_free_environment() -> None:
    result = _sandboxed(
        "import os,sys\n"
        "bad=[k for k in os.environ if any(x in k.upper() for x in "
        "('PROXY','PRIVATE_KEY','API_KEY','AUTH','SECRET','PASSWORD','COOKIE','SIGNATURE'))]\n"
        "print('BAD_ENV', bad)\n"
        "sys.exit(0 if not bad else 4)\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BAD_ENV []" in result.stdout
