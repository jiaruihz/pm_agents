from __future__ import annotations

import json
import sqlite3

import pytest

import us_fast_weather_lab.cli as cli


class _CommercialCollector:
    source_id = "METAR_WS_METAR"
    last_init = None

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        type(self).last_init = (args, kwargs)
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def summary(self) -> dict[str, bool]:
        return {"started": self.started, "stopped": self.stopped}


class _StartFailureCollector(_CommercialCollector):
    def start(self) -> None:
        self.started = True
        raise RuntimeError("synthetic start failure")


class _StopFailureCollector(_CommercialCollector):
    def stop(self) -> None:
        self.stopped = True
        raise RuntimeError("synthetic stop failure")


def _commercial_args(tmp_path, *extra: str):
    return cli.build_parser().parse_args(
        [
            "smoke",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--reports-root",
            str(tmp_path / "reports"),
            "--vantage-id",
            "TEST",
            "--duration-sec",
            "0.01",
            "--enable-metar-ws",
            "--disable-wis2",
            "--disable-awc",
            *extra,
        ]
    )


def _run_end_rows(runtime_root):
    conn = sqlite3.connect(runtime_root / "evidence.sqlite3")
    try:
        return conn.execute("SELECT termination_reason FROM collector_run_end").fetchall()
    finally:
        conn.close()


def test_commercial_only_smoke_skips_public_collectors_and_keeps_key_secret(
    tmp_path, monkeypatch, capsys
) -> None:
    secret = "test-commercial-secret-must-not-print"
    monkeypatch.setenv("METAR_WS_API_KEY", secret)
    monkeypatch.setattr(cli, "MetarWsCollector", _CommercialCollector)
    monkeypatch.setattr(cli, "discover_brokers", lambda *args, **kwargs: pytest.fail("WIS2 discovery ran"))
    monkeypatch.setattr(cli, "Wis2Collector", lambda *args, **kwargs: pytest.fail("WIS2 collector ran"))
    monkeypatch.setattr(cli, "AwcCollector", lambda *args, **kwargs: pytest.fail("AWC collector ran"))
    monkeypatch.setattr(cli, "command_replay", lambda args: 0)
    monkeypatch.setattr(
        cli,
        "generate_reports",
        lambda *args, **kwargs: {"counts": {"runs": 1}, "disposition": "BLOCKED_BY_ACCESS_OR_INSUFFICIENT_EVIDENCE"},
    )

    args = _commercial_args(tmp_path)

    assert cli.command_smoke(args) == 0
    output = capsys.readouterr().out
    assert secret not in output
    assert '"status":"disabled"' in output
    assert (tmp_path / "runtime" / "last_run_summary.json").exists()
    assert _CommercialCollector.last_init is not None
    _, init_kwargs = _CommercialCollector.last_init
    assert {"KDAL", "KHOU"}.issubset(init_kwargs["stations"])
    assert {"metar.obs.kdal", "metar.obs.khou", "metar.obs10.kdal", "metar.obs10.khou"}.issubset(
        set(init_kwargs["config"].channels)
    )
    channels = set(init_kwargs["config"].channels)
    # The versioned wide cohort adds stations absent from the legacy eight-city
    # list, but D-ATIS is deliberately still the explicit commercial list.
    assert {"metar.obs.katl", "metar.obs.kbkf", "metar.obs10.katl", "metar.obs10.kbkf"}.issubset(channels)
    assert "metar.atis.kbkf" not in channels


def test_commercial_start_failure_still_seals_run_without_secret(tmp_path, monkeypatch) -> None:
    secret = "test-start-secret-must-not-persist"
    monkeypatch.setenv("METAR_WS_API_KEY", secret)
    monkeypatch.setattr(cli, "MetarWsCollector", _StartFailureCollector)
    with pytest.raises(RuntimeError, match="synthetic start failure"):
        cli.command_smoke(_commercial_args(tmp_path))
    assert _run_end_rows(tmp_path / "runtime") == [("exception",)]
    summary_text = (tmp_path / "runtime" / "last_run_summary.json").read_text()
    assert secret not in summary_text
    assert json.loads(summary_text)["termination_reason"] == "exception"


def test_commercial_stop_failure_does_not_skip_run_end(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("METAR_WS_API_KEY", "test-stop-secret-must-not-persist")
    monkeypatch.setattr(cli, "MetarWsCollector", _StopFailureCollector)
    with pytest.raises(RuntimeError, match="cleanup failed after evidence seal"):
        cli.command_smoke(_commercial_args(tmp_path))
    assert _run_end_rows(tmp_path / "runtime") == [("duration_complete",)]


def test_commercial_only_smoke_fails_fast_when_no_collector_is_enabled(tmp_path) -> None:
    args = cli.build_parser().parse_args(
        [
            "smoke",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--vantage-id",
            "TEST",
            "--disable-wis2",
            "--disable-awc",
        ]
    )

    with pytest.raises(RuntimeError, match="no collectors enabled"):
        cli.command_smoke(args)
    assert not (tmp_path / "runtime").exists()
