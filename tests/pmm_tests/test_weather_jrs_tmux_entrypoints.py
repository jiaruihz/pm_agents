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
        has_internal_probe = (
            "weather_jrs_tmux_start_socket" in text
            or "weather_jrs_tmux_write_probe" in text
        )
        if "new-session" in text and not has_internal_probe:
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
        "WEATHER_JRS_TMUX_TEST_OVERRIDE": "1",
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


def test_shared_helper_pins_full_disk_access_tmux_binary():
    helper_text = (OPS / "weather_jrs_tmux_env.sh").read_text(encoding="utf-8")
    assert (
        'WEATHER_JRS_TMUX_BIN_CANONICAL="/opt/homebrew/Cellar/tmux/3.6b/bin/tmux"'
        in helper_text
    )
    assert (
        'WEATHER_JRS_TMUX_BIN_CANONICAL_SHA256="'
        "74e47d00267734a47daffd0c6d92e6841c671ee88b54aab6c13d239ba8741584"
        '"'
        in helper_text
    )
    assert "command -v tmux" not in helper_text
    assert "WEATHER_JRS_TMUX_TEST_OVERRIDE" in helper_text


def test_shared_helper_rejects_tmux_binary_override_outside_tests(tmp_path):
    fake_tmux = tmp_path / "tmux"
    fake_tmux.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_tmux.chmod(0o755)
    helper = OPS / "weather_jrs_tmux_env.sh"
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(helper))}; weather_jrs_tmux_bin",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "WEATHER_JRS_TMUX_BIN": str(fake_tmux)},
    )
    assert result.returncode != 0
    assert "refusing JRS tmux binary override outside tests" in result.stderr


def test_mac_start_entries_do_not_default_to_legacy_jrs_symlink():
    offenders = []
    legacy_runtime = "$HOME/projects/weather_data_feed_service_runtime"
    for path in sorted(OPS.glob("start_*.sh")):
        if legacy_runtime in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == []


def test_canonical_refresh_launchagent_delegates_to_canonical_tmux():
    installer = (OPS / "install_weather_canonical_refresh_launchagent.sh").read_text(
        encoding="utf-8"
    )
    starter = (OPS / "start_weather_canonical_refresh_tmux.sh").read_text(
        encoding="utf-8"
    )

    assert "start_weather_canonical_refresh_tmux.sh" in installer
    assert "run_weather_canonical_refresh_launchd.sh" not in installer
    assert "weather_jrs_tmux_env.sh" in starter
    assert "weather_jrs_tmux_start_socket" in starter
    assert 'SESSION="weather_canonical_refresh"' in starter
    assert "run_weather_canonical_refresh_launchd.sh" in starter


def test_legacy_direct_launchagent_stack_cannot_start_jrs_workloads():
    text = (OPS / "mac_weather_stack.sh").read_text(encoding="utf-8")
    assert (
        "refusing dormant direct-LaunchAgent weather stack; "
        "use the strategy's canonical JRS tmux start entrypoint"
    ) in text
    for command in (
        "install-launchagents",
        "start",
        "restart",
        "start-live",
        "start-low-price-live",
        "start-low-price-shadow",
        "start-low-price-take-profit",
        "start-low-price-integrated-shadow",
        "start-runtime-monitor",
    ):
        assert command in text


def test_shared_helper_rejects_legacy_socket():
    helper = OPS / "weather_jrs_tmux_env.sh"
    result = subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(helper))}; weather_jrs_tmux_socket weather-jrs"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "refusing non-canonical JRS tmux socket" in result.stderr
