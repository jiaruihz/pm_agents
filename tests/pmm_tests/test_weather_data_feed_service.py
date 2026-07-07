from __future__ import annotations

import importlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from weather_data_feed.models import ObservationRecord
from weather_data_feed.observation_sources import ObservationSourceResult


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
    assert "snapshot-full" in result.stdout
    assert "daily" in result.stdout
    assert "observations" in result.stdout
    assert "source-events" in result.stdout
    assert "runway-observations" in result.stdout
    assert "high-frequency-observations" in result.stdout


def test_snapshot_full_cli_forces_all_orderbook_scope(monkeypatch) -> None:
    from weather_data_feed_service import cli

    calls = []

    def fake_run_legacy(module_name: str, argv: list[str]) -> int:
        calls.append((module_name, argv))
        return 0

    monkeypatch.setattr(cli, "_run_legacy", fake_run_legacy)

    rc = cli.main(["snapshot-full", "--", "--orderbook-scope", "current_d1", "--orderbook-budget-sec", "240"])

    assert rc == 0
    assert calls == [
        (
            "paper_snapshot",
            ["--orderbook-scope", "current_d1", "--orderbook-budget-sec", "240", "--orderbook-scope", "all"],
        )
    ]


def test_snapshot_targeted_cli_forces_strategy_live_orderbook_scope(monkeypatch) -> None:
    from weather_data_feed_service import cli

    calls = []

    def fake_run_legacy(module_name: str, argv: list[str]) -> int:
        calls.append((module_name, argv))
        return 0

    monkeypatch.setattr(cli, "_run_legacy", fake_run_legacy)

    rc = cli.main(["snapshot-targeted", "--", "--orderbook-scope", "all"])

    assert rc == 0
    assert calls == [("paper_snapshot", ["--orderbook-scope", "all", "--orderbook-scope", "strategy_live"])]


def test_source_events_cli_dispatches_runner_args(monkeypatch) -> None:
    from weather_data_feed_service import cli

    calls = []

    def fake_main(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    import types

    fake_module = types.SimpleNamespace(main=fake_main)
    monkeypatch.setitem(sys.modules, "weather_data_feed_service.source_events", fake_module)

    rc = cli.main(["source-events", "--", "--cities", "Shanghai", "--sources", "profile_primary"])

    assert rc == 0
    assert calls == [["--cities", "Shanghai", "--sources", "profile_primary"]]


def test_forecast_enrichment_cli_dispatches_runner_args(monkeypatch) -> None:
    from weather_data_feed_service import cli

    calls = []

    def fake_main(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    import types

    fake_module = types.SimpleNamespace(main=fake_main)
    monkeypatch.setitem(sys.modules, "weather_data_feed_service.forecast_enrichment", fake_module)

    rc = cli.main(["forecast-enrichment", "--", "--cities", "Shanghai", "--no-taf"])

    assert rc == 0
    assert calls == [["--cities", "Shanghai", "--no-taf"]]


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


def test_legacy_weather_fetch_does_not_reuse_market_proxy(monkeypatch) -> None:
    monkeypatch.setenv("WEATHER_PREDICT_PROXY", "http://market-proxy.invalid:8080")
    monkeypatch.delenv("WEATHER_DATA_FEED_WEATHER_PROXY", raising=False)
    monkeypatch.delenv("WEATHER_PREDICT_WEATHER_PROXY", raising=False)
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    _drop_legacy_modules()
    pm_edge_compare = importlib.import_module("pm_edge_compare")

    get_calls = []
    client_calls = []

    def fake_get(url, *, params=None, timeout=None, trust_env=None, **_kwargs):
        get_calls.append(
            {
                "url": url,
                "params": params,
                "timeout": timeout,
                "trust_env": trust_env,
            }
        )
        raise RuntimeError("direct failed")

    def fake_client(*args, **kwargs):
        client_calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("weather fetch should not instantiate a proxy client")

    monkeypatch.setattr(pm_edge_compare.httpx, "get", fake_get)
    monkeypatch.setattr(pm_edge_compare.httpx, "Client", fake_client)

    try:
        pm_edge_compare.fetch_weather_url("https://api.open-meteo.com/v1/gfs", params={"latitude": 1})
    except RuntimeError:
        pass

    assert get_calls == [
        {
            "url": "https://api.open-meteo.com/v1/gfs",
            "params": {"latitude": 1},
            "timeout": 10.0,
            "trust_env": False,
        }
    ]
    assert client_calls == []


def test_paper_snapshot_batch_fetches_token_orderbooks(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    _drop_legacy_modules()
    paper_snapshot = importlib.import_module("paper_snapshot")

    seen = []

    def fake_fetch(_client, token_id, top_n=20):
        seen.append((token_id, top_n))
        return {
            "status": "ok",
            "token_id": token_id,
            "fetched_at_utc": "2026-06-29T16:00:00Z",
            "summary": {"best_ask": 0.5},
            "raw": {},
        }

    monkeypatch.setattr(paper_snapshot, "fetch_token_orderbook", fake_fetch)

    rows = {
        "yes-token": {"city": "Shanghai", "token_id": "yes-token"},
        "no-token": {"city": "Shanghai", "token_id": "no-token"},
    }
    result = paper_snapshot.fetch_token_orderbook_batch(None, rows, top_n=5, max_workers=2)

    assert set(result) == {"yes-token", "no-token"}
    assert result["yes-token"][0]["city"] == "Shanghai"
    assert result["yes-token"][1]["summary"]["best_ask"] == 0.5
    assert sorted(seen) == [("no-token", 5), ("yes-token", 5)]


def test_paper_snapshot_strategy_live_orderbook_scope_covers_active_strategy_legs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    _drop_legacy_modules()
    paper_snapshot = importlib.import_module("paper_snapshot")

    markets = [
        {"question": "Will the high temperature in Foo be 66-67°F?"},
        {"question": "Will the high temperature in Foo be 68-69°F?"},
        {"question": "Will the high temperature in Foo be 70-71°F?"},
        {"question": "Will the high temperature in Foo be 72-73°F?"},
        {"question": "Will the high temperature in Foo be 74-75°F?"},
    ]

    targets = paper_snapshot.orderbook_targets_for_strategy_live(
        markets,
        "F",
        {"metar_current_max_f": 69.0},
    )

    assert targets == {
        ("68-69", "yes"),
        ("68-69", "no"),
        ("70-71", "no"),
        ("72-73", "no"),
    }


def test_source_events_builds_append_only_rows_without_proxy(monkeypatch, tmp_path) -> None:
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import source_events

    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]
    captured = []

    def fake_snapshot(cfg_arg, source_name, now_utc, *, settings, recent_minutes):
        captured.append((cfg_arg.city, source_name, settings.proxy_candidates, recent_minutes))
        return {
            "status": "ok",
            "source": source_name,
            "station": cfg_arg.official_icao,
            "source_report_ts_utc": "2026-06-17T10:00:00+00:00",
            "temp_c": 27.0,
            "payload_hash": "hash-a",
            "ts_utc": "2026-06-17T10:00:01+00:00",
            "local_detect_ts_utc": "2026-06-17T10:00:01+00:00",
            "city": cfg_arg.city,
            "target_date": "2026-06-17",
            "unit": cfg_arg.unit,
        }

    monkeypatch.setattr(source_events, "load_city_configs", lambda **_kwargs: [cfg])
    monkeypatch.setattr(source_events, "snapshot_observation_source", fake_snapshot)

    parser = source_events.build_parser()
    args = parser.parse_args(
        [
            "--output-dir",
            str(tmp_path / "source_events"),
            "--now-utc",
            "2026-06-17T10:00:01Z",
            "--sources",
            "profile_primary",
            "--recent-minutes",
            "120",
        ]
    )
    payload = source_events.build_events(args)
    source_events.write_outputs(payload, tmp_path / "source_events")

    assert payload["rows"] == 1
    assert payload["changed"] == 1
    assert payload["records"][0]["producer"] == "weather_data_feed_service.source_events"
    assert payload["records"][0]["changed_since_last"] is True
    assert captured == [("Shanghai", cfg.live_observation_source, (None,), 120)]
    assert (tmp_path / "source_events" / "sources.jsonl").exists()
    assert (tmp_path / "source_events" / "latest.json").exists()


def test_forecast_enrichment_builds_shadow_rows(monkeypatch, tmp_path) -> None:
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed.forecast_sources import ForecastFetchResult
    from weather_data_feed_service import forecast_enrichment

    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]

    def fake_multi_model(*_args, **_kwargs):
        return ForecastFetchResult(
            source_key="open_meteo_multi_model",
            status="ok",
            fetched_at_utc="2026-07-07T02:00:00+00:00",
            latency_ms=10.0,
            payload={
                "daily": {
                    "2026-07-07": {
                        "models": {"ECMWF": 95.0, "GFS": 97.0},
                        "model_count": 2,
                        "model_spread": 2.0,
                    }
                },
                "daily_dates": ["2026-07-07"],
                "model_metadata": {"ECMWF": {"open_meteo_model": "ecmwf_ifs025"}},
                "hourly_values_hash_by_model": {"ECMWF": "hash-a"},
            },
        )

    def fake_context(*_args, **_kwargs):
        return ForecastFetchResult(
            source_key="open_meteo_weather_context",
            status="ok",
            fetched_at_utc="2026-07-07T02:00:00+00:00",
            latency_ms=12.0,
            payload={
                "utc_offset_seconds": 28800,
                "hourly": {
                    "time": ["2026-07-07T12:00", "2026-07-07T13:00"],
                    "temperature_2m": [94.0, 95.0],
                    "cape": [100.0, 120.0],
                    "convective_inhibition": [-5.0, -10.0],
                    "lifted_index": [1.0, 0.5],
                    "boundary_layer_height": [800.0, 900.0],
                },
                "daily": {"time": ["2026-07-07"], "temperature_2m_max": [95.0]},
            },
        )

    def fake_taf(*_args, **_kwargs):
        return ForecastFetchResult(
            source_key="aviationweather_taf",
            status="ok",
            fetched_at_utc="2026-07-07T02:00:00+00:00",
            latency_ms=8.0,
            payload={
                "issue_time": "2026-07-07T00:00:00Z",
                "raw_taf": "TAF ZSPD 070000Z 0700/0800 18008KT SCT030",
            },
        )

    monkeypatch.setattr(forecast_enrichment, "load_city_configs", lambda **_kwargs: [cfg])
    monkeypatch.setattr(forecast_enrichment, "fetch_open_meteo_multi_model", fake_multi_model)
    monkeypatch.setattr(forecast_enrichment, "fetch_open_meteo_weather_context", fake_context)
    monkeypatch.setattr(forecast_enrichment, "fetch_aviationweather_taf", fake_taf)

    args = forecast_enrichment.build_parser().parse_args(
        [
            "--output-dir",
            str(tmp_path / "forecast_enrichment"),
            "--now-utc",
            "2026-07-07T02:00:00Z",
            "--cities",
            "Shanghai",
        ]
    )
    payload = forecast_enrichment.build_payload(args)
    forecast_enrichment.write_outputs(payload, tmp_path / "forecast_enrichment")

    assert payload["rows"] == 1
    assert payload["ok"] == 1
    row = payload["records"][0]
    assert row["schema_version"] == "weather_forecast_enrichment_v1"
    assert row["open_meteo_multi_model"]["target_date"]["model_spread"] == 2.0
    assert row["open_meteo_weather_context"]["target_day_hourly"]["forecast_max"] == 95.0
    assert row["vertical_profile_signal"]["available"] is True
    assert row["taf"]["signal"]["source"] == "aviationweather_taf"
    assert (tmp_path / "forecast_enrichment" / "forecast_enrichment.jsonl").exists()
    assert (tmp_path / "forecast_enrichment" / "latest.json").exists()


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


def test_paper_snapshot_resolves_station_diff_official_metar_station(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    _drop_legacy_modules()
    paper_snapshot = importlib.import_module("paper_snapshot")

    configs = paper_snapshot.load_official_observation_configs()

    chicago = paper_snapshot.resolve_observation_station(
        "Chicago",
        {"icao": "KMDW"},
        configs,
    )
    assert chicago["configured_icao"] == "KMDW"
    assert chicago["metar_icao"] == "KORD"
    assert chicago["official_observation_station"] == "KORD"
    assert chicago["settlement_source_class"] == "official_station_diff_confirmed"
    assert chicago["source_profile_registry_class"] == "official_station_diff_aligned"

    mexico_city = paper_snapshot.resolve_observation_station(
        "MexicoCity",
        {"icao": "MMMX"},
        configs,
    )
    assert mexico_city["metar_icao"] == "MMMX"
    assert mexico_city["source_profile_registry_class"] == "legacy_city_pool"


def test_systemd_units_are_versioned_for_data_feed_service() -> None:
    unit_dir = ROOT / "deploy" / "systemd" / "user"
    snapshot = (unit_dir / "weather-data-feed-snapshot.service").read_text()
    full_snapshot = (unit_dir / "weather-data-feed-full-snapshot.service").read_text()
    full_snapshot_timer = (unit_dir / "weather-data-feed-full-snapshot.timer").read_text()
    observations = (unit_dir / "weather-data-feed-observations.service").read_text()
    observations_timer = (unit_dir / "weather-data-feed-observations.timer").read_text()
    source_events = (unit_dir / "weather-data-feed-source-events.service").read_text()
    source_events_timer = (unit_dir / "weather-data-feed-source-events.timer").read_text()
    daily = (unit_dir / "weather-data-feed-daily.service").read_text()
    timer = (unit_dir / "weather-data-feed-snapshot.timer").read_text()
    installer = (ROOT / "scripts" / "ops" / "install_weather_data_feed_service_units.sh").read_text()

    for text in (snapshot, full_snapshot, observations, source_events, daily):
        assert "weather_data_feed_service" in text
        assert "python -u -m weather_data_feed_service" in text
        assert "EnvironmentFile=-%h/projects/weather_data_feed_service/.env" in text
        assert "WEATHER_DATA_FEED_OUTPUT_ROOT" in text
        assert "WEATHER_DATA_FEED_CACHE_ROOT" in text
        assert "weather-predict" not in text

    assert "snapshot-full -- --orderbook-budget-sec 600 --orderbook-workers 8" in full_snapshot
    assert "snapshot-targeted -- --orderbook-budget-sec 60 --orderbook-workers 4" in snapshot
    assert "source-events -- --output-dir" in source_events
    assert "weather_data_feed_service_runtime/targeted_output" in snapshot
    assert "OnUnitInactiveSec=30min" in timer
    assert "OnUnitInactiveSec=30min" in full_snapshot_timer
    assert "OnUnitInactiveSec=5min" in observations_timer
    assert "OnUnitInactiveSec=2min" in source_events_timer
    assert "weather-data-feed-full-snapshot.service" in installer
    assert "weather-data-feed-observations.service" in installer
    assert "weather-data-feed-source-events.service" in installer
    assert "weather-data-feed-snapshot.service" in installer
    assert "weather-data-feed-daily.service" in installer


def test_observations_cache_row_uses_data_feed_fetcher(monkeypatch) -> None:
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import observations

    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]

    def fake_fetch(_request, settings=None):
        return ObservationSourceResult(
            source_key="aviationweather_metar",
            status="ok",
            records=(
                ObservationRecord(
                    source_key="aviationweather_metar",
                    city="Shanghai",
                    target_date="2026-06-17",
                    station_or_feed="ZSPD",
                    obs_ts_utc="2026-06-17T09:30:00+00:00",
                    ingest_ts_utc="2026-06-17T09:31:00+00:00",
                    temp_c=25.0,
                ),
                ObservationRecord(
                    source_key="aviationweather_metar",
                    city="Shanghai",
                    target_date="2026-06-17",
                    station_or_feed="ZSPD",
                    obs_ts_utc="2026-06-17T10:00:00+00:00",
                    ingest_ts_utc="2026-06-17T10:01:00+00:00",
                    temp_c=27.0,
                    dewpoint_c=23.0,
                    relh=78.0,
                    wind_kt=5.0,
                ),
                ObservationRecord(
                    source_key="aviationweather_metar",
                    city="Shanghai",
                    target_date="2026-06-17",
                    station_or_feed="ZSPD",
                    obs_ts_utc="2026-06-17T10:30:00+00:00",
                    ingest_ts_utc="2026-06-17T10:31:00+00:00",
                    temp_c=26.0,
                    dewpoint_c=22.0,
                    relh=70.0,
                    wind_kt=4.0,
                ),
            ),
            fetched_at_utc="2026-06-17T10:31:00+00:00",
            latency_ms=40.0,
        )

    monkeypatch.setattr(observations, "fetch_observation_source", fake_fetch)

    row = observations.observation_cache_row(
        cfg,
        datetime(2026, 6, 17, 10, 31, tzinfo=timezone.utc),
        settings=observations.FetchSettings(),
    )

    assert row["status"] == "ok"
    assert row["current_temp_c"] == 26.0
    assert row["running_max_c"] == 27.0
    assert row["last_obs_utc"] == "2026-06-17T10:30:00+00:00"
    assert row["cadence_min"] == 30.0


def test_observations_source_chain_uses_awc_cache_before_iem() -> None:
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import observations

    cfg = load_city_configs(include_station_diff=True, only_cities={"Chicago"})[0]

    assert observations._source_chain(cfg, include_fallback_sources=True) == [
        "aviationweather_metar",
        "aviationweather_cache_csv",
        "iem_asos",
    ]


def test_observations_source_chain_uses_synoptic_primary_for_verified_us_city() -> None:
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import observations

    cfg = load_city_configs(include_station_diff=True, only_cities={"Austin"})[0]

    assert observations._source_chain(cfg, include_fallback_sources=True) == [
        "synopticdata_timeseries",
        "aviationweather_metar",
        "iem_asos_madishf_latest",
        "iem_asos",
    ]


def test_observations_cache_reuses_previous_ok_row_on_fetch_failure(monkeypatch, tmp_path) -> None:
    import argparse
    from weather_data_feed import build_observation_cache, write_observation_cache
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import observations

    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]
    output = tmp_path / "latest.json"
    previous = {
        "city": "Shanghai",
        "target_date": "2026-06-17",
        "status": "ok",
        "source": "aviationweather_metar",
        "station": "ZSPD",
        "last_obs_utc": "2026-06-17T10:30:00+00:00",
        "n_obs": 10,
    }
    write_observation_cache(build_observation_cache([previous]), output)

    monkeypatch.setattr(observations, "load_city_configs", lambda **_kwargs: [cfg])
    monkeypatch.setattr(
        observations,
        "observation_cache_row",
        lambda *_args, **_kwargs: {
            "city": "Shanghai",
            "target_date": "2026-06-17",
            "status": "fetch_failed",
            "source": "aviationweather_metar",
            "station": "ZSPD",
            "error": "HTTP 429",
        },
    )
    args = argparse.Namespace(
        output=str(output),
        now_utc="2026-06-17T10:40:00+00:00",
        include_station_diff=False,
        cities=["Shanghai"],
        timeout_sec=3.0,
        max_workers=1,
        include_fallback_sources=False,
    )

    cache = observations.build_cache(args)
    row = cache["records"][0]

    assert row["status"] == "ok"
    assert row["cache_reused_after_fetch_status"] == "fetch_failed"
    assert row["cache_reused_after_fetch_error"] == "HTTP 429"


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
