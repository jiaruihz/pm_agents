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
        if re.search(r"weather_jrs_tmux[^\n]*\srun-shell(?:\s|$)", text):
            offenders.append(f"{path.name}:canonical_run_shell")
    assert offenders == []


def test_no_ops_entrypoint_keeps_legacy_jrs_socket_literal():
    offenders = []
    for path in sorted(OPS.iterdir()):
        if path.suffix not in {".sh", ".py"}:
            continue
        text = path.read_text(encoding="utf-8")
        # The failover skill name legitimately contains ``weather-jrs``.
        # Reject only operational use of the retired socket, not docs/routing.
        if "tmux -L " + "weather" + "-jrs" in text:
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
        """#!/bin/sh
printf '%s\n' "$*" >> "$WEATHER_JRS_FAKE_TMUX_LOG"
case "$4" in
  new-session)
    /bin/sh -c "$8"
    ;;
  has-session)
    exit 1
    ;;
esac
""",
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
    assert invocation.startswith("-N -L weather-data-feed-jrs new-session ")
    assert "run-shell" not in invocation
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
    assert '"$tmux_bin" -N -L "$socket" "$@"' in helper_text
    assert '-e "WEATHER_PRODUCTION_CONFIG=$production_config"' in helper_text
    assert "weather_jrs_tmux_guarded_replace_session()" in helper_text


def test_guarded_replace_classifies_only_registered_bounded_sessions() -> None:
    helper = OPS / "weather_jrs_tmux_env.sh"
    command = (
        f"source {shlex.quote(str(helper))}; "
        "for name in "
        "weather_canonical_refresh "
        "weather_reliability_worker_1_2 "
        "weather_controller_feed_health_1_2 "
        "weather_jrs_write_probe_1_2; do "
        "weather_jrs_tmux_session_is_bounded \"$name\" || exit 10; "
        "done; "
        "weather_jrs_tmux_session_is_bounded weather_data_feed_jrs && exit 11; "
        "weather_jrs_tmux_session_is_bounded weather_reliability_worker_persistent && exit 12; "
        "exit 0"
    )

    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


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


def test_shared_helper_rejects_persistent_mutation_without_controller(tmp_path):
    fake_tmux = tmp_path / "tmux"
    fake_log = tmp_path / "tmux.log"
    fake_tmux.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$WEATHER_JRS_FAKE_TMUX_LOG\"\n",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    helper = OPS / "weather_jrs_tmux_env.sh"
    env = {
        **os.environ,
        "WEATHER_JRS_TMUX_BIN": str(fake_tmux),
        "WEATHER_JRS_TMUX_TEST_OVERRIDE": "1",
        "WEATHER_JRS_FAKE_TMUX_LOG": str(fake_log),
    }

    rejected = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(helper))}; weather_jrs_tmux weather-data-feed-jrs new-session -d -s rogue true",
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert rejected.returncode != 0
    assert "outside production controller" in rejected.stderr
    assert not fake_log.exists()

    accepted = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(helper))}; WEATHER_JRS_TMUX_MUTATION_AUTHORITY=controller weather_jrs_tmux weather-data-feed-jrs new-session -d -s managed true",
        ],
        capture_output=True,
        text=True,
        env={
            **env,
            "WEATHER_PRODUCTION_CONFIG": str(ROOT / "src/strategies/runtime/production.yaml"),
        },
    )

    assert accepted.returncode == 0
    assert fake_log.read_text(encoding="utf-8").startswith(
        "-N -L weather-data-feed-jrs new-session"
    )


def test_shared_helper_injects_controller_production_contract_into_session(tmp_path):
    fake_tmux = tmp_path / "tmux"
    fake_log = tmp_path / "tmux.log"
    production_config = tmp_path / "production.yaml"
    production_config.write_text("version: test\n", encoding="utf-8")
    fake_tmux.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$WEATHER_JRS_FAKE_TMUX_LOG\"\n",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    helper = OPS / "weather_jrs_tmux_env.sh"
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(helper))}; "
            "weather_jrs_tmux weather-data-feed-jrs new-session -d -s managed true",
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "WEATHER_JRS_TMUX_BIN": str(fake_tmux),
            "WEATHER_JRS_TMUX_TEST_OVERRIDE": "1",
            "WEATHER_JRS_FAKE_TMUX_LOG": str(fake_log),
            "WEATHER_JRS_TMUX_MUTATION_AUTHORITY": "controller",
            "WEATHER_PRODUCTION_CONFIG": str(production_config),
        },
    )

    assert result.returncode == 0, result.stderr
    invocation = fake_log.read_text(encoding="utf-8")
    assert f"-e WEATHER_PRODUCTION_CONFIG={production_config}" in invocation


def test_mac_start_entries_do_not_default_to_legacy_jrs_symlink():
    offenders = []
    legacy_runtime = "$HOME/projects/weather_data_feed_service_runtime"
    for path in sorted(OPS.glob("start_*.sh")):
        if legacy_runtime in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == []


def test_data_feed_loop_has_one_forecast_run_owner_and_bounded_open_meteo_refresh():
    data_feed = (OPS / "start_mac_weather_data_feed_loop.sh").read_text(encoding="utf-8")
    forecast_owner = (OPS / "start_weather_forecast_curve_collector_v1.sh").read_text(
        encoding="utf-8"
    )

    assert "forecast_owner=external_controller_managed" in data_feed
    assert "forecast-enrichment" not in data_feed
    assert "weather_data_feed_service.forecast_curve_collector" in forecast_owner
    assert "forecast-enrichment" in forecast_owner
    assert "WEATHER_DATA_FEED_FORECAST_ENRICHMENT_OPEN_METEO_REFRESH_SEC:-21600" in forecast_owner
    assert "--no-single-runs" in forecast_owner


def test_data_feed_snapshot_join_does_not_probe_or_switch_market_proxy():
    data_feed = (OPS / "start_mac_weather_data_feed_loop.sh").read_text(encoding="utf-8")

    assert "weather_market_proxy_failover.py" not in data_feed
    assert "market_proxy_check_start_utc" not in data_feed
    assert "--orderbook-source-latest" in data_feed
    assert "--orderbook-source-max-age-sec" in data_feed


def test_jrs_start_entries_do_not_create_jrs_directories_outside_tmux():
    offenders = []
    for path in sorted(OPS.glob("start_*.sh")):
        text = path.read_text(encoding="utf-8")
        if "/Volumes/jrs" not in text:
            continue
        if re.search(r'(?m)^\s*mkdir -p [^\n]*"\$RUNTIME_ROOT', text):
            offenders.append(f"{path.name}:runtime_root")
        output_is_jrs = re.search(r'(?m)^OUTPUT_DIR=.*\$RUNTIME_ROOT', text) is not None
        if output_is_jrs and re.search(r'(?m)^\s*mkdir -p [^\n]*"\$OUTPUT_DIR"', text):
            offenders.append(f"{path.name}:output_dir")
    assert offenders == []


def test_live_cross_start_uses_its_pinned_release_python():
    text = (OPS / "start_weather_live_cross_observations.sh").read_text(
        encoding="utf-8"
    )

    assert 'PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"' in text
    assert 'if [[ ! -x "$PY" ]]' in text


def test_knmi_start_resolves_release_paths_from_production_spec():
    text = (OPS / "start_mac_knmi_open_data_jrs_tmux.sh").read_text(
        encoding="utf-8"
    )

    assert 'release("knmi").checkout_root' in text
    assert 'release("control_plane").checkout_root' in text
    assert "/Users/deepsleep/projects/pm_agents_knmi_recovery" not in text
    assert "/Users/deepsleep/projects/pm_agents_prod" not in text


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
    assert "weather_jrs_tmux_run_oneshot" in starter
    assert "canonical_refresh_checkout_root" in starter
    assert "load_production_spec().operational_repo_root" in starter
    assert 'CANONICAL_FILL_CACHE="$OPERATIONAL_PROJECT_DIR/runtime/weather_edge_v1/clob_fills.jsonl"' in starter
    assert 'ln -sfn "$CANONICAL_FILL_CACHE" "$REFRESH_FILL_CACHE"' in starter
    assert 'export PROJECT_DIR=%q WEATHER_DATA_FEED_RUNTIME_ROOT=%q WEATHER_CANONICAL_REFRESH_RUNTIME_DIR=%q' in starter
    assert "WEATHER_CANONICAL_REFRESH_RUNTIME_DIR" in starter
    assert 'SESSION="weather_canonical_refresh"' in starter
    assert "WEATHER_JRS_ONESHOT_LOG_MAX_BYTES" in starter
    assert "WEATHER_JRS_ONESHOT_LOG_RETAIN_BYTES" in starter
    assert "67108864" in starter
    assert "8388608" in starter
    assert "run_weather_canonical_refresh_launchd.sh" in starter
    assert "weather_jrs_tmux_start_socket" not in starter
    assert "weather_jrs_tmux_mkdir" not in starter
    assert "new-session" not in starter
    assert "has-session" not in starter
    assert "last_exit_status" not in starter
    assert "STATUS_BRIDGE" not in starter
    assert 'LAUNCHD_LOG_DIR="$HOME/Library/Logs/' in installer
    assert "RUNTIME_DIR=" not in installer

    refresh = (OPS / "run_weather_canonical_refresh_launchd.sh").read_text(
        encoding="utf-8"
    )
    assert "weather_market_proxy_env.sh" in refresh
    assert "load_production_spec().operational_repo_root" in refresh
    assert 'MARKET_PROXY="$(weather_resolve_market_proxy "$PROXY_CONTROL_ROOT")"' in refresh
    assert 'weather_export_market_proxy_env "$MARKET_PROXY"' in refresh
    assert "materialize_weather_city_runtime_canonical_v1.py" in refresh
    assert "WCIR_BUNDLES_PATH" in refresh
    assert "--expected-db" in refresh
    assert "--state" in refresh
    assert "--max-new-rows 5000" in refresh
    assert "--max-new-bytes 67108864" in refresh
    assert "--max-settlement-updates 5000" in refresh
    assert "wcir_candidate_materialization.json" in refresh
    assert refresh.index("weather_clob_fill_coverage_gate.py") < refresh.index(
        "materialize_weather_city_runtime_canonical_v1.py"
    )
    assert "127.0.0.1:7890" not in refresh
    assert "127.0.0.1:7897" not in refresh

    proxy_helper = (OPS / "weather_market_proxy_env.sh").read_text(
        encoding="utf-8"
    )
    assert "weather_resolve_market_proxy()" in proxy_helper
    assert 'cd "$project_root"' in proxy_helper
    assert "from scripts.ops.weather_market_proxy_ctl import read_state" in proxy_helper


def test_shared_helper_owns_jrs_oneshot_lifecycle():
    helper = (OPS / "weather_jrs_tmux_env.sh").read_text(encoding="utf-8")

    assert "weather_jrs_tmux_run_oneshot()" in helper
    assert "weather_jrs_tmux_start_socket" in helper
    assert "weather_jrs_tmux_mkdir" in helper
    assert "weather_jrs_tmux_exec_checked" in helper
    assert " run-shell " not in helper
    assert 'new-session -d -s "$session"' in helper
    assert 'has-session -t "=$session"' in helper
    assert 'local status_file="$job_dir/last_exit_status"' in helper
    assert "JRS tmux one-shot exited without status" in helper


def test_shared_helper_runs_jrs_oneshot_and_propagates_status(tmp_path):
    fake_tmux = tmp_path / "tmux"
    fake_tmux.write_text(
        """#!/bin/sh
case "$4" in
  run-shell)
    /bin/sh -c "$4"
    ;;
  has-session)
    exit 1
    ;;
  new-session)
    /bin/sh -c "$8"
    exit 0
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    helper = OPS / "weather_jrs_tmux_env.sh"
    runtime_root = tmp_path / "runtime"
    job_dir = tmp_path / "jobs" / "refresh"
    env = {
        **os.environ,
        "WEATHER_JRS_TMUX_BIN": str(fake_tmux),
        "WEATHER_JRS_TMUX_TEST_OVERRIDE": "1",
    }

    success = subprocess.run(
        [
            "bash",
            "-c",
            (
                'source "$1"; '
                'weather_jrs_tmux_run_oneshot "$2" test_job "$3" '
                '"printf success"'
            ),
            "_",
            str(helper),
            str(runtime_root),
            str(job_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert success.returncode == 0, success.stderr
    assert (job_dir / "tmux.log").read_text(encoding="utf-8") == "success"
    assert (job_dir / "last_exit_status").read_text(encoding="utf-8") == "0\n"

    failure = subprocess.run(
        [
            "bash",
            "-c",
            (
                'source "$1"; '
                'weather_jrs_tmux_run_oneshot "$2" failing_job "$3" false'
            ),
            "_",
            str(helper),
            str(runtime_root),
            str(job_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert failure.returncode == 1
    assert "JRS tmux one-shot failed" in failure.stderr


def test_shared_helper_compacts_opted_in_oneshot_log_before_append(tmp_path):
    fake_tmux = tmp_path / "tmux"
    fake_tmux.write_text(
        """#!/bin/sh
case "$4" in
  has-session)
    exit 1
    ;;
  new-session)
    /bin/sh -c "$8"
    exit 0
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    helper = OPS / "weather_jrs_tmux_env.sh"
    runtime_root = tmp_path / "runtime"
    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True)
    (job_dir / "tmux.log").write_text("0123456789abcdef", encoding="utf-8")
    env = {
        **os.environ,
        "WEATHER_JRS_TMUX_BIN": str(fake_tmux),
        "WEATHER_JRS_TMUX_TEST_OVERRIDE": "1",
        "WEATHER_JRS_ONESHOT_LOG_MAX_BYTES": "10",
        "WEATHER_JRS_ONESHOT_LOG_RETAIN_BYTES": "4",
    }

    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                'source "$1"; '
                'weather_jrs_tmux_run_oneshot "$2" compact_job "$3" '
                '"printf fresh"'
            ),
            "_",
            str(helper),
            str(runtime_root),
            str(job_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert (job_dir / "tmux.log").read_text(encoding="utf-8") == "cdeffresh"


def test_shared_helper_rejects_invalid_oneshot_log_bounds(tmp_path):
    fake_tmux = tmp_path / "tmux"
    fake_tmux.write_text(
        """#!/bin/sh
case "$4" in
  has-session)
    exit 1
    ;;
  new-session)
    /bin/sh -c "$8"
    exit 0
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    env = {
        **os.environ,
        "WEATHER_JRS_TMUX_BIN": str(fake_tmux),
        "WEATHER_JRS_TMUX_TEST_OVERRIDE": "1",
        "WEATHER_JRS_ONESHOT_LOG_MAX_BYTES": "10",
        "WEATHER_JRS_ONESHOT_LOG_RETAIN_BYTES": "10",
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; weather_jrs_tmux_run_oneshot "$2" invalid_job "$3" true',
            "_",
            str(OPS / "weather_jrs_tmux_env.sh"),
            str(tmp_path / "runtime"),
            str(tmp_path / "job"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert "invalid JRS one-shot log retain bytes" in result.stderr


def test_shared_helper_does_not_compact_when_session_already_exists(tmp_path):
    fake_tmux = tmp_path / "tmux"
    fake_tmux.write_text(
        """#!/bin/sh
case "$4" in
  has-session)
    [ "$6" = "=running_job" ] && exit 0
    exit 1
    ;;
  new-session)
    [ "$7" = "running_job" ] && exit 1
    /bin/sh -c "$8"
    exit 0
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True)
    log_file = job_dir / "tmux.log"
    log_file.write_text("0123456789abcdef", encoding="utf-8")
    env = {
        **os.environ,
        "WEATHER_JRS_TMUX_BIN": str(fake_tmux),
        "WEATHER_JRS_TMUX_TEST_OVERRIDE": "1",
        "WEATHER_JRS_ONESHOT_LOG_MAX_BYTES": "10",
        "WEATHER_JRS_ONESHOT_LOG_RETAIN_BYTES": "4",
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; weather_jrs_tmux_run_oneshot "$2" running_job "$3" true',
            "_",
            str(OPS / "weather_jrs_tmux_env.sh"),
            str(tmp_path / "runtime"),
            str(job_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "already running; skipping" in result.stdout
    assert log_file.read_text(encoding="utf-8") == "0123456789abcdef"


def test_shared_helper_leaves_existing_log_unbounded_without_opt_in(tmp_path):
    fake_tmux = tmp_path / "tmux"
    fake_tmux.write_text(
        """#!/bin/sh
case "$4" in
  has-session)
    exit 1
    ;;
  new-session)
    /bin/sh -c "$8"
    exit 0
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True)
    log_file = job_dir / "tmux.log"
    log_file.write_text("existing", encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                'source "$1"; '
                'weather_jrs_tmux_run_oneshot "$2" unbounded_job "$3" '
                '"printf fresh"'
            ),
            "_",
            str(OPS / "weather_jrs_tmux_env.sh"),
            str(tmp_path / "runtime"),
            str(job_dir),
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "WEATHER_JRS_TMUX_BIN": str(fake_tmux),
            "WEATHER_JRS_TMUX_TEST_OVERRIDE": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    assert log_file.read_text(encoding="utf-8") == "existingfresh"


def test_shared_helper_captures_errexit_job_status_and_log(tmp_path):
    fake_tmux = tmp_path / "tmux"
    fake_tmux.write_text(
        """#!/bin/sh
case "$4" in
  has-session)
    exit 1
    ;;
  new-session)
    /bin/sh -c "$8"
    exit 0
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    runtime_root = tmp_path / "runtime"
    job_dir = tmp_path / "job"
    env = {
        **os.environ,
        "WEATHER_JRS_TMUX_BIN": str(fake_tmux),
        "WEATHER_JRS_TMUX_TEST_OVERRIDE": "1",
    }
    command = (
        f'source "{OPS / "weather_jrs_tmux_env.sh"}"; '
        'weather_jrs_tmux_run_oneshot '
        f'"{runtime_root}" errexit_job "{job_dir}" '
        "'set -eu; printf before-failure; false; printf unreachable'"
    )
    result = subprocess.run(
        ["bash", "-lc", command],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "JRS tmux one-shot failed" in result.stderr
    assert "exited without status" not in result.stderr
    assert (job_dir / "last_exit_status").read_text(encoding="utf-8").strip() == "1"
    assert (job_dir / "tmux.log").read_text(encoding="utf-8") == "before-failure"


def test_legacy_direct_launchagent_stack_cannot_start_jrs_workloads():
    for retired in (
        "mac_weather_stack.sh",
        "start_regime_routed_no_tiny_live.sh",
        "stop_regime_routed_no_tiny_live.sh",
        "prepare_mac_weather_external_runtime.sh",
        "weather_n100_proxy_failover.sh",
        "install_weather_live_runtime_patrol_launchagent.sh",
        "start_weather_live_runtime_patrol_tmux.sh",
        "start_weather_live_runtime_patrol.sh",
        "run_weather_live_runtime_patrol_launchd.sh",
    ):
        assert not (OPS / retired).exists()


def test_shared_helper_rejects_legacy_socket():
    helper = OPS / "weather_jrs_tmux_env.sh"
    result = subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(helper))}; weather_jrs_tmux_socket weather-jrs"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "refusing non-canonical JRS tmux socket" in result.stderr


def test_controller_contracts_separate_historical_stale_book_modes():
    broad = (OPS / "start_weather_fast_source_stale_book_production.sh").read_text(
        encoding="utf-8"
    )
    helsinki = (
        OPS / "start_weather_helsinki_pre_cross_active_ladder_shadow.sh"
    ).read_text(encoding="utf-8")

    assert 'TMUX_SESSION="weather_fast_source_stale_book"' in broad
    assert "--fresh-scope t_minus_1_no" in broad
    assert "--high-frequency-latest" in broad
    assert "--high-frequency-jsonl" in broad
    assert "live_cross_observations_root" in broad
    assert "output/high_frequency_observations" not in broad
    assert "--continuous-active-brackets" not in broad
    assert "--live" not in broad
    assert 'TMUX_SESSION="weather_helsinki_pre_cross_active_ladder_shadow"' in helsinki
    assert "--cities Helsinki" in helsinki
    assert "--sources fmi" in helsinki
    assert "--continuous-active-brackets" in helsinki
    assert "--live" not in helsinki


def test_source_event_repricing_uses_canonical_live_cross_inputs():
    text = (OPS / "start_weather_source_event_ladder_repricing_shadow.sh").read_text(
        encoding="utf-8"
    )

    assert text.count("--high-frequency-latest") == 2
    assert text.count("--high-frequency-jsonl") == 2
    assert "live_cross_observations_root" in text
    assert "output/high_frequency_observations" not in text
