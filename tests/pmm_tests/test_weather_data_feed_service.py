from __future__ import annotations

import importlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEGACY_DIR = ROOT / "weather_data_feed_service" / "legacy_weather_predict"


def _drop_legacy_modules() -> None:
    for name in (
        "paper_snapshot",
        "daily_pipeline",
        "pm_edge_compare",
        "calibration_backtest",
        "edge_backtest",
        "city_pools",
        "weather_data_feed_service.legacy_weather_predict.paper_snapshot",
        "weather_data_feed_service.legacy_weather_predict.daily_pipeline",
        "weather_data_feed_service.legacy_weather_predict.pm_edge_compare",
        "weather_data_feed_service.legacy_weather_predict.calibration_backtest",
        "weather_data_feed_service.legacy_weather_predict.edge_backtest",
        "weather_data_feed_service.legacy_weather_predict.city_pools",
    ):
        sys.modules.pop(name, None)


def test_weather_data_feed_service_cli_help_imports() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "weather_data_feed_service", "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Weather data feed service" in result.stdout
    assert "snapshot" in result.stdout
    assert "daily" in result.stdout


def test_legacy_runners_use_configured_runtime_roots(tmp_path, monkeypatch) -> None:
    output_root = tmp_path / "out"
    cache_root = tmp_path / "cache"
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(output_root))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(cache_root))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    _drop_legacy_modules()

    pm_edge_compare = importlib.import_module("pm_edge_compare")
    calibration_backtest = importlib.import_module("calibration_backtest")
    edge_backtest = importlib.import_module("edge_backtest")
    daily_pipeline = importlib.import_module("daily_pipeline")
    paper_snapshot = importlib.import_module("paper_snapshot")

    assert pm_edge_compare.CACHE_DIR == cache_root
    assert pm_edge_compare.OUTPUT_DIR == output_root
    assert calibration_backtest.CACHE_DIR == str(cache_root)
    assert edge_backtest.CACHE_DIR == cache_root
    assert edge_backtest.OUTPUT_DIR == output_root
    assert daily_pipeline.CACHE_DIR == cache_root
    assert daily_pipeline.CACHE_PM == cache_root / "pm_history"
    assert daily_pipeline.CACHE_GFS == cache_root / "gfs_daily"
    assert paper_snapshot.OUTPUT_DIR == output_root / "paper_snapshots"
    assert paper_snapshot.ORDERBOOK_OUTPUT_DIR == output_root / "orderbook_snapshots"


def test_paper_snapshot_metar_accepts_epoch_obs_time(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    _drop_legacy_modules()
    paper_snapshot = importlib.import_module("paper_snapshot")

    class Response:
        status_code = 200

        def json(self):
            return [
                {
                    "obsTime": 1781888100,
                    "temp": 32,
                }
            ]

    class Client:
        def get(self, *_args, **_kwargs):
            return Response()

    state = paper_snapshot.fetch_live_metar_state(
        Client(),
        "EHAM",
        "2026-06-19",
        "Amsterdam",
        datetime(2026, 6, 19, 17, 1, 46, tzinfo=timezone.utc),
    )

    assert state["metar_current_max_f"] == 90
    assert state["metar_latest_temp_f"] == 90
    assert state["metar_latest_ts_utc"] == "2026-06-19T16:55:00Z"
    assert state["metar_source"] == "aviationweather_live"


def test_systemd_units_are_versioned_for_data_feed_service() -> None:
    unit_dir = ROOT / "deploy" / "systemd" / "user"
    snapshot = (unit_dir / "weather-data-feed-snapshot.service").read_text()
    daily = (unit_dir / "weather-data-feed-daily.service").read_text()
    timer = (unit_dir / "weather-data-feed-snapshot.timer").read_text()
    installer = (ROOT / "scripts" / "ops" / "install_weather_data_feed_service_units.sh").read_text()

    for text in (snapshot, daily):
        assert "weather_data_feed_service" in text
        assert "python -u -m weather_data_feed_service" in text
        assert "EnvironmentFile=-%h/projects/weather_data_feed_service/.env" in text
        assert "WEATHER_DATA_FEED_OUTPUT_ROOT" in text
        assert "WEATHER_DATA_FEED_CACHE_ROOT" in text
        assert "weather-predict" not in text

    assert "OnUnitInactiveSec=30min" in timer
    assert "weather-data-feed-snapshot.service" in installer
    assert "weather-data-feed-daily.service" in installer


def test_daily_parity_check_flags_missing_new_tree(tmp_path) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_cache = old_root / "cache" / "pm_history"
    old_cache.mkdir(parents=True)
    (old_cache / "Amsterdam_2026-06-18.json").write_text(json.dumps({"brackets": [1, 2]}))

    missing = subprocess.run(
        [
            sys.executable,
            "scripts/ops/weather_data_feed_daily_parity_check.py",
            "--old-root",
            str(old_root),
            "--new-root",
            str(new_root),
            "--relative-dir",
            "cache/pm_history",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 1
    assert '"status": "fail"' in missing.stdout

    new_cache = new_root / "cache" / "pm_history"
    new_cache.mkdir(parents=True)
    (new_cache / "Amsterdam_2026-06-18.json").write_text(json.dumps({"brackets": [1, 2]}))
    ok = subprocess.run(
        [
            sys.executable,
            "scripts/ops/weather_data_feed_daily_parity_check.py",
            "--old-root",
            str(old_root),
            "--new-root",
            str(new_root),
            "--relative-dir",
            "cache/pm_history",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert '"status": "ok"' in ok.stdout
