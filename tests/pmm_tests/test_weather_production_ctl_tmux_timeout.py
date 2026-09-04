import subprocess
from pathlib import Path

from scripts.ops import weather_production_ctl as ctl
from src.strategies.runtime.production import WeatherProductionSpec


def test_tmux_timeout_returns_bounded_diagnostic_instead_of_crashing(
    monkeypatch, tmp_path: Path
) -> None:
    spec = WeatherProductionSpec(
        version="test",
        host_role="test",
        operational_repo_root=tmp_path,
        canonical_db_path=tmp_path / "weather.db",
        compatibility_db_paths=(Path("runtime/weather.db"),),
        data_feed_runtime_root=tmp_path / "feed",
        pm_runtime_root=tmp_path,
        canonical_tmux_socket="weather-data-feed-jrs",
        canonical_tmux_binary=tmp_path / "tmux",
        market_proxy_state_path=tmp_path / "market_proxy.json",
        market_proxy_default_url="http://127.0.0.1:7897",
        managed_runtimes=(),
    )

    def timeout(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, timeout=30, output="partial")

    monkeypatch.setattr(ctl.subprocess, "run", timeout)

    result = ctl._tmux_on_socket(spec, "probe", "has-session", "-t", "worker")

    assert result.returncode == 124
    assert result.stdout == "partial\ntmux command timed out after 30s"


def test_checked_tmux_preserves_poll_timeout_diagnostic(monkeypatch, tmp_path: Path) -> None:
    spec = WeatherProductionSpec(
        version="test",
        host_role="test",
        operational_repo_root=tmp_path,
        canonical_db_path=tmp_path / "weather.db",
        compatibility_db_paths=(Path("runtime/weather.db"),),
        data_feed_runtime_root=tmp_path / "feed",
        pm_runtime_root=tmp_path,
        canonical_tmux_socket="weather-data-feed-jrs",
        canonical_tmux_binary=tmp_path / "tmux",
        market_proxy_state_path=tmp_path / "market_proxy.json",
        market_proxy_default_url="http://127.0.0.1:7897",
        managed_runtimes=(),
    )

    def fake_tmux(_spec, _socket, *args):
        if args[0] == "new-session":
            return subprocess.CompletedProcess(args, 0, "")
        return subprocess.CompletedProcess(args, 124, "poll timed out")

    monkeypatch.setattr(ctl, "_tmux_on_socket", fake_tmux)

    result = ctl._run_tmux_checked(
        spec, "probe", "worker", "true", timeout_sec=1
    )

    assert result.returncode == 124
    assert result.stdout == "poll timed out"
