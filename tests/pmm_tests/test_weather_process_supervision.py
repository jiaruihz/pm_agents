from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/ops/weather_process_supervision.sh"


def run_bash(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f"source {HELPER!s}; {command}"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def test_weather_run_with_timeout_preserves_success_status() -> None:
    result = run_bash("weather_run_with_timeout 2 bash -c 'exit 0'")

    assert result.returncode == 0


def test_weather_run_with_timeout_returns_124_and_terminates_child() -> None:
    result = run_bash("weather_run_with_timeout 1 bash -c 'sleep 5'")

    assert result.returncode == 124
    assert "weather child timeout after 1s" in result.stderr


def test_weather_run_with_timeout_rejects_invalid_deadline() -> None:
    result = run_bash("weather_run_with_timeout 0 true")

    assert result.returncode == 2
    assert "invalid weather child timeout" in result.stderr


def test_weather_start_with_timeout_async_does_not_block() -> None:
    result = run_bash(
        "started=$SECONDS; "
        "weather_start_with_timeout_async 2 bash -c 'sleep 1'; "
        "elapsed=$((SECONDS-started)); "
        "test \"$elapsed\" -eq 0; "
        "wait \"$WEATHER_ASYNC_PID\""
    )

    assert result.returncode == 0
