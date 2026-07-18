import os
import re
import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "scripts/ops"


def test_jrs_managed_start_entries_use_canonical_tmux_helper():
    offenders: list[str] = []
    for path in sorted(OPS.glob("start_*.sh")):
        text = path.read_text(encoding="utf-8")
        manages_process = any(token in text for token in ("new-session", "screen -dmS", "nohup"))
        if not manages_process:
            continue
        if "weather_jrs_tmux_env.sh" not in text:
            offenders.append(f"{path.name}:missing_canonical_helper")
        if "new-session" in text and "weather_jrs_tmux_start_socket" not in text:
            offenders.append(f"{path.name}:missing_internal_write_probe")
        if "weather-jrs" in text or "weather-full-ladder" in text:
            offenders.append(f"{path.name}:legacy_socket_literal")
        if "screen -dmS" in text:
            offenders.append(f"{path.name}:screen_start")
        if "nohup" in text:
            offenders.append(f"{path.name}:nohup_start")
        if "_START_MODE" in text or "SCREEN_SESSION" in text:
            offenders.append(f"{path.name}:alternate_process_manager_knob")
        if re.search(r"\$\{[A-Z0-9_]*TMUX_SOCKET", text):
            offenders.append(f"{path.name}:socket_override_knob")
        if re.search(r"(?:^|\s)(?:tmux|\"\$TMUX_BIN\")\s+(?:-L|has-session|new-session|new-window|kill-session)", text):
            offenders.append(f"{path.name}:raw_tmux_command")
    assert offenders == []


def test_no_ops_entrypoint_keeps_legacy_jrs_socket_literal():
    offenders = []
    for path in sorted(OPS.iterdir()):
        if path.suffix not in {".sh", ".py"}:
            continue
        if "weather-jrs" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == []


def test_tmux_pid_stop_entries_use_canonical_helper():
    offenders = []
    for path in sorted(OPS.glob("stop_*.sh")):
        text = path.read_text(encoding="utf-8")
        if "tmux:*" not in text:
            continue
        if "weather_jrs_tmux_env.sh" not in text or 'weather_jrs_tmux "$TMUX_SOCKET"' not in text:
            offenders.append(path.name)
    assert offenders == []


def test_shared_helper_owns_socket_and_runs_probe_inside_tmux(tmp_path):
    fake_tmux = tmp_path / "tmux"
    fake_log = tmp_path / "tmux.log"
    fake_tmux.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$WEATHER_JRS_FAKE_TMUX_LOG\"\n",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    helper = OPS / "weather_jrs_tmux_env.sh"
    runtime_root = tmp_path / "runtime with spaces"
    env = {
        **os.environ,
        "WEATHER_JRS_TMUX_BIN": str(fake_tmux),
        "WEATHER_JRS_FAKE_TMUX_LOG": str(fake_log),
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(helper))}; weather_jrs_tmux_start_socket {shlex.quote(str(runtime_root))}",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.stdout.strip() == "weather-data-feed-jrs"
    invocation = fake_log.read_text(encoding="utf-8")
    assert invocation.startswith("-L weather-data-feed-jrs run-shell ")
    assert str(runtime_root).replace(" ", "\\ ") in invocation


def test_shared_helper_rejects_legacy_socket():
    helper = OPS / "weather_jrs_tmux_env.sh"
    result = subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(helper))}; weather_jrs_tmux_socket weather-jrs"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "refusing non-canonical JRS tmux socket" in result.stderr
