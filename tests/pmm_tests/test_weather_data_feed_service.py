from __future__ import annotations

import importlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

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


def test_paper_snapshot_batch_retries_transient_proxy_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    _drop_legacy_modules()
    paper_snapshot = importlib.import_module("paper_snapshot")
    monkeypatch.setattr(paper_snapshot, "ORDERBOOK_BATCH_RETRIES", 2)
    monkeypatch.setattr(paper_snapshot, "ORDERBOOK_BATCH_RETRY_BACKOFF_SEC", 0)

    class FlakyClient:
        attempts = 0

        def post(self, url, *, json, timeout):
            self.attempts += 1
            request = httpx.Request("POST", url)
            if self.attempts < 3:
                raise httpx.ConnectTimeout("proxy handshake timeout", request=request)
            return httpx.Response(
                200,
                request=request,
                json=[
                    {
                        "asset_id": row["token_id"],
                        "bids": [
                            {"price": f"0.{index + 10:03d}", "size": "1"}
                            for index in range(25)
                        ],
                        "asks": [],
                        "timestamp": "1",
                        "hash": row["token_id"],
                    }
                    for row in json
                ],
            )

    client = FlakyClient()
    rows = {"hot-token": {"capture_priority": "hot", "token_id": "hot-token"}}
    result = paper_snapshot.fetch_token_orderbook_batch(client, rows)
    book = result["hot-token"][1]

    assert client.attempts == 3
    assert book["status"] == "ok"
    assert book["request_attempt_count"] == 3
    assert book["prior_attempt_errors"] == [
        "ConnectTimeout: proxy handshake timeout",
        "ConnectTimeout: proxy handshake timeout",
    ]
    assert len(book["summary"]["bids"]) == 20
    assert len(book["raw"]["bids"]) == 25
    assert book["raw_payload_hash"] == paper_snapshot.canonical_json_hash(book["raw"])


def test_paper_snapshot_batch_does_not_retry_contract_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    _drop_legacy_modules()
    paper_snapshot = importlib.import_module("paper_snapshot")
    monkeypatch.setattr(paper_snapshot, "ORDERBOOK_BATCH_RETRIES", 2)
    monkeypatch.setattr(paper_snapshot, "ORDERBOOK_BATCH_RETRY_BACKOFF_SEC", 0)

    class InvalidPayloadClient:
        attempts = 0

        def post(self, url, *, json, timeout):
            self.attempts += 1
            return httpx.Response(200, request=httpx.Request("POST", url), json={})

    client = InvalidPayloadClient()
    rows = {"hot-token": {"capture_priority": "hot", "token_id": "hot-token"}}
    result = paper_snapshot.fetch_token_orderbook_batch(client, rows)
    book = result["hot-token"][1]

    assert client.attempts == 1
    assert book["status"] == "batch_error"
    assert book["request_attempt_count"] == 1
    assert book["error_retryable"] is False


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


def test_orderbook_enrichment_summary_separates_scope_from_missing_targets(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    _drop_legacy_modules()
    paper_snapshot = importlib.import_module("paper_snapshot")

    summary = paper_snapshot.summarize_orderbook_enrichment(
        [
            {"yes_book_status": "ok", "no_book_status": "orderbook_scope_skipped"},
            {"yes_book_status": "orderbook_scope_skipped", "no_book_status": "orderbook_budget_exhausted"},
        ],
        scope="strategy_live",
        budget_sec=120,
        spent_sec=120.25,
    )

    assert summary["status"] == "incomplete"
    assert summary["target_count"] == 2
    assert summary["target_ok_count"] == 1
    assert summary["target_incomplete_count"] == 1
    assert summary["target_status_counts"] == {"ok": 1, "orderbook_budget_exhausted": 1}


def test_paper_snapshot_json_publish_is_atomic(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    _drop_legacy_modules()
    paper_snapshot = importlib.import_module("paper_snapshot")

    destination = tmp_path / "snapshot.json"
    paper_snapshot.publish_json_atomic(destination, {"batch": "complete"})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"batch": "complete"}
    assert not list(tmp_path.glob(".*.tmp"))


def test_paper_snapshot_availability_clock_follows_batch_completion(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    _drop_legacy_modules()
    paper_snapshot = importlib.import_module("paper_snapshot")
    payload = {
        "ts_utc": "2026-07-29T05:51:32Z",
        "records": [{"snapshot_ts_utc": "2026-07-29T05:51:32Z"}],
    }

    paper_snapshot.stamp_snapshot_availability(
        payload,
        "2026-07-29T05:55:16Z",
    )

    assert payload["collection_started_at_utc"] == "2026-07-29T05:51:32Z"
    assert payload["available_at_utc"] == "2026-07-29T05:55:16Z"
    assert payload["records"][0]["available_at_utc"] == "2026-07-29T05:55:16Z"


def test_paper_snapshot_preserves_near_binary_ladder_siblings(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    _drop_legacy_modules()
    paper_snapshot = importlib.import_module("paper_snapshot")

    markets = [
        {
            "question": "Will the highest temperature in Foo be 20°C or below?",
            "outcomePrices": "[0.0005, 0.9995]",
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": '["yes-low", "no-low"]',
        },
        {
            "question": "Will the highest temperature in Foo be 21°C?",
            "outcomePrices": "[0.42, 0.58]",
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": '["yes-mid", "no-mid"]',
        },
        {
            "question": "Will the highest temperature in Foo be 22°C or higher?",
            "outcomePrices": "[0.9995, 0.0005]",
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": '["yes-high", "no-high"]',
        },
    ]

    brackets, entries = paper_snapshot.gamma_market_ladder(markets)

    assert brackets == [("20", 0.0005), ("21", 0.42), ("22+", 0.9995)]
    assert [row["yes_token_id"] for row in entries] == ["yes-low", "yes-mid", "yes-high"]


def test_source_events_builds_append_only_rows_without_proxy(monkeypatch, tmp_path) -> None:
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import source_events

    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]
    captured = []

    def fake_snapshot(cfg_arg, source_name, now_utc, *, settings, recent_minutes, include_record_rows=False):
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

    shard_only = tmp_path / "source_events_shard_only"
    source_events.write_outputs({**payload, "_write_aggregate": False}, shard_only)
    assert not (shard_only / "sources.jsonl").exists()
    assert len(list(shard_only.glob("????-??-??/sources.jsonl"))) == 1
    assert parser.parse_args(["--shard-only"]).shard_only is True


def test_source_events_cli_summary_excludes_private_state(monkeypatch, capsys, tmp_path) -> None:
    from weather_data_feed_service import source_events

    monkeypatch.setattr(
        source_events,
        "build_events",
        lambda _args: {
            "status": "ok",
            "rows": 1,
            "records": [{"city": "Chicago"}],
            "append_records": [{"city": "Chicago"}],
            "_state": {"large": "private"},
            "_state_path": str(tmp_path / "state.json"),
        },
    )
    monkeypatch.setattr(source_events, "write_outputs", lambda *_args: None)

    assert source_events.main(["--output-dir", str(tmp_path)]) == 0
    summary = json.loads(capsys.readouterr().out)

    assert summary == {"rows": 1, "status": "ok"}


def test_source_events_recovers_missing_awc_report_as_late_backfill(tmp_path) -> None:
    from weather_data_feed_service import source_events

    journal = tmp_path / "sources.jsonl"
    existing = {
        "city": "Busan",
        "target_date": "2026-07-16",
        "source": "aviationweather_metar",
        "station": "RKPK",
        "source_report_ts_utc": "2026-07-16T04:00:00+00:00",
        "payload_hash": "existing",
    }
    journal.write_text(json.dumps(existing) + "\n", encoding="utf-8")
    latest = {
        **existing,
        "source_report_ts_utc": "2026-07-16T06:29:00+00:00",
        "payload_hash": "latest",
    }
    missing = {
        **existing,
        "source_report_ts_utc": "2026-07-16T05:00:00+00:00",
        "temp_c": 33.0,
        "raw_metar": "METAR RKPK 160500Z 29003KT CAVOK 33/21 Q1002",
        "payload_hash": "missing",
    }
    state = {}

    rows = source_events.late_awc_backfills([latest], [existing, missing, latest], state, journal_path=journal)

    assert [row["source_report_ts_utc"] for row in rows] == ["2026-07-16T05:00:00+00:00"]
    assert rows[0]["first_seen_type"] == "late_backfill"
    assert rows[0]["original_first_seen_unknown"] is True
    assert rows[0]["recovered_from_multi_record_payload"] is True


def test_source_event_information_headers_preserve_first_seen_and_late_boundary() -> None:
    from weather_data_feed_service.source_events import annotate_information_events

    state = {}
    base = {
        "status": "ok",
        "source": "aviationweather_metar",
        "city": "Atlanta",
        "target_date": "2026-07-28",
        "station": "KATL",
        "source_report_ts_utc": "2026-07-28T12:00:00Z",
        "local_detect_ts_utc": "2026-07-28T12:00:03Z",
        "temp_c": 25.0,
        "raw_metar": "METAR KATL 281200Z 00000KT 25/20",
        "payload_hash": "source-a",
    }
    first = annotate_information_events(
        [base], state, raw_source_path="sources.jsonl", available_at_utc="2026-07-28T12:00:04Z"
    )[0]
    replay = annotate_information_events(
        [{**base, "local_detect_ts_utc": "2026-07-28T12:02:03Z"}],
        state,
        raw_source_path="sources.jsonl",
        available_at_utc="2026-07-28T12:02:04Z",
    )[0]
    revision = annotate_information_events(
        [{**base, "temp_c": 26.0, "raw_metar": "METAR KATL 281200Z 00000KT 26/20 COR", "payload_hash": "source-b"}],
        state,
        raw_source_path="sources.jsonl",
        available_at_utc="2026-07-28T12:03:04Z",
    )[0]
    late = annotate_information_events(
        [{**base, "first_seen_type": "late_backfill", "original_first_seen_unknown": True}],
        state,
        raw_source_path="sources.jsonl",
        available_at_utc="2026-07-28T16:00:04Z",
    )[0]

    assert replay["information_event_id"] == first["information_event_id"]
    assert replay["first_seen_at_utc"] == first["first_seen_at_utc"]
    assert revision["event_role"] == "revision"
    assert revision["revision_of_event_id"] == first["information_event_id"]
    assert late["pit_lineage_class"] == "late_backfill_first_seen_unknown"
    assert late["first_seen_at_utc"] is None


def test_source_events_expands_fallbacks_only_for_research_profiles() -> None:
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import source_events

    normal_cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]
    synoptic_cfg = load_city_configs(include_station_diff=False, only_cities={"Austin"})[0]
    research_cfg = {
        cfg.city: cfg
        for cfg in load_city_configs(
            include_station_diff=True,
            include_research_cities=True,
            research_cities={"HongKong"},
        )
    }["HongKong"]

    assert source_events.requested_sources(
        normal_cfg,
        ["profile_primary"],
        include_fallback_sources=True,
    ) == ["aviationweather_metar"]
    assert source_events.requested_sources(
        normal_cfg,
        ["profile_primary"],
        include_fallback_sources=True,
        include_awc_cache_first_arrival=True,
    ) == ["aviationweather_metar", "aviationweather_cache_csv"]
    assert source_events.requested_sources(
        synoptic_cfg,
        ["profile_primary"],
        include_fallback_sources=True,
        include_awc_cache_first_arrival=True,
    ) == ["synopticdata_timeseries", "aviationweather_cache_csv"]
    assert source_events.requested_sources(
        research_cfg,
        ["profile_primary"],
        include_fallback_sources=True,
    ) == ["aviationweather_metar", "noaa_tgftp_station_txt"]


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
    assert not (tmp_path / "forecast_enrichment" / "forecast_enrichment.jsonl").exists()
    dated_journals = tuple(
        (tmp_path / "forecast_enrichment").glob(
            "????-??-??/forecast_enrichment.jsonl"
        )
    )
    assert len(dated_journals) == 1
    dated_journal = dated_journals[0]
    assert (tmp_path / "forecast_enrichment" / "latest.json").exists()
    persisted_row = json.loads(dated_journal.read_text(encoding="utf-8").splitlines()[0])
    assert persisted_row["taf"]["information_event"]["raw_source_path"] == str(
        dated_journal
    )

    def unexpected_open_meteo(*_args, **_kwargs):
        raise AssertionError("fresh Open-Meteo evidence should be reused")

    monkeypatch.setattr(
        forecast_enrichment,
        "fetch_open_meteo_multi_model",
        unexpected_open_meteo,
    )
    monkeypatch.setattr(
        forecast_enrichment,
        "fetch_open_meteo_weather_context",
        unexpected_open_meteo,
    )
    reused = forecast_enrichment.fetch_city_forecast_enrichment(
        cfg,
        datetime(2026, 7, 7, 3, 0, tzinfo=timezone.utc),
        settings=forecast_enrichment.ForecastFetchSettings(),
        forecast_days=3,
        previous=row,
        open_meteo_refresh_sec=21600,
    )
    assert reused["status"] == "ok"
    assert reused["open_meteo_reuse"]["reused"] is True
    assert reused["open_meteo_reuse"]["max_age_sec"] == 3600

    failed_latest = dict(payload)
    failed_row = dict(row)
    failed_row["open_meteo_multi_model"] = {
        "result": {"status": "fetch_failed", "fetched_at_utc": "2026-07-07T03:00:00Z"}
    }
    failed_latest["records"] = [failed_row]
    (tmp_path / "forecast_enrichment" / "latest.json").write_text(
        json.dumps(failed_latest),
        encoding="utf-8",
    )
    recovered = forecast_enrichment.load_reusable_open_meteo_rows(
        tmp_path / "forecast_enrichment",
        now_utc=datetime(2026, 7, 7, 3, 0, tzinfo=timezone.utc),
        max_age_sec=21600,
    )
    assert recovered["Shanghai"]["open_meteo_multi_model"]["result"]["status"] == "ok"


def test_high_frequency_partition_only_preserves_live_cross_default(tmp_path) -> None:
    from weather_data_feed_service import high_frequency_observations

    payload = {
        "generated_at_utc": "2026-07-07T02:00:00+00:00",
        "new_observation_records": [
            {
                "city": "Shanghai",
                "source": "aviationweather_metar",
                "station": "ZSPD",
                "observation_time_utc": "2026-07-07T01:55:00+00:00",
                "value_f": 90.0,
            }
        ],
    }

    partition_root = tmp_path / "high_frequency_observations"
    high_frequency_observations.write_outputs(
        payload,
        partition_root,
        write_aggregate=False,
    )
    assert not (partition_root / "high_frequency_observations.jsonl").exists()
    assert (
        partition_root
        / "2026-07-07"
        / "high_frequency_observations.jsonl"
    ).exists()
    assert (partition_root / "latest.json").exists()

    live_cross_root = tmp_path / "live_cross_observations"
    high_frequency_observations.write_outputs(payload, live_cross_root)
    assert (live_cross_root / "high_frequency_observations.jsonl").exists()
    assert (
        live_cross_root
        / "2026-07-07"
        / "high_frequency_observations.jsonl"
    ).exists()


def test_high_frequency_cli_partition_only_flag() -> None:
    from weather_data_feed_service import high_frequency_observations

    args = high_frequency_observations.build_parser().parse_args(["--partition-only"])
    assert args.partition_only is True


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


def test_retired_systemd_units_fail_closed() -> None:
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
        assert "Retired weather data-feed systemd entrypoint" in text
        assert "ExecStart=/usr/bin/false" in text
        assert "weather_data_feed_service_runtime" not in text
        assert "python -u -m weather_data_feed_service" not in text

    assert "OnUnitInactiveSec=30min" in timer
    assert "OnUnitInactiveSec=30min" in full_snapshot_timer
    assert "OnUnitInactiveSec=5min" in observations_timer
    assert "OnUnitInactiveSec=2min" in source_events_timer
    assert "weather-data-feed-full-snapshot.service" in installer
    assert "weather-data-feed-observations.service" in installer
    assert "weather-data-feed-source-events.service" in installer
    assert "weather-data-feed-snapshot.service" in installer
    assert "weather-data-feed-daily.service" in installer
    assert "retired entrypoint" in installer


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
                        dewpoint_c=23.0,
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
    assert row["running_min_c"] == 25.0
    assert row["running_min_obs_utc"] == "2026-06-17T09:30:00+00:00"
    assert row["minutes_since_running_min"] == 61.0
    assert row["rebound_c"] == 1.0
    assert row["last_obs_utc"] == "2026-06-17T10:30:00+00:00"
    assert row["cadence_min"] == 30.0
    assert row["first_running_max_obs_utc"] == "2026-06-17T10:00:00+00:00"
    assert row["last_running_max_obs_utc"] == "2026-06-17T10:00:00+00:00"
    assert row["minutes_since_first_running_max"] == 31.0
    assert row["minutes_since_last_strict_new_high"] == 31.0
    assert row["same_running_max_obs_count"] == 1
    assert row["dewpoint_change_1h_f"] == pytest.approx(-1.8)
    assert row["d_dwpf_1h"] == pytest.approx(-1.8)


def test_observations_source_chain_uses_awc_cache_before_iem() -> None:
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import observations

    cfg = load_city_configs(include_station_diff=True, only_cities={"Chicago"})[0]

    assert observations._source_chain(cfg, include_fallback_sources=True) == [
        "aviationweather_metar",
        "aviationweather_cache_csv",
        "noaa_tgftp_station_txt",
        "iem_asos",
    ]


def test_observations_marks_expected_local_midnight_gap(monkeypatch) -> None:
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import observations

    cfg = load_city_configs(include_station_diff=True, only_cities={"Chicago"})[0]
    monkeypatch.setattr(
        observations,
        "_fetch_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("empty")),
    )

    row = observations.observation_cache_row(
        cfg,
        datetime(2026, 7, 29, 5, 17, tzinfo=timezone.utc),
        settings=observations.FetchSettings(),
        include_fallback_sources=True,
    )

    assert row["status"] == "awaiting_first_observation"
    assert row["local_day_elapsed_min"] == 17
    assert row["first_observation_grace_min"] == 90


def test_observations_empty_after_local_midnight_grace_is_failure(monkeypatch) -> None:
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import observations

    cfg = load_city_configs(include_station_diff=True, only_cities={"Chicago"})[0]
    monkeypatch.setattr(
        observations,
        "_fetch_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("empty")),
    )

    row = observations.observation_cache_row(
        cfg,
        datetime(2026, 7, 29, 7, 0, tzinfo=timezone.utc),
        settings=observations.FetchSettings(),
        include_fallback_sources=True,
    )

    assert row["status"] == "fetch_failed"


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

    assert row["status"] == "reused_after_fetch_error"
    assert row["last_success_status"] == "ok"
    assert row["age_min"] == 10.0
    assert row["cache_reused_after_fetch_status"] == "fetch_failed"
    assert row["cache_reused_after_fetch_error"] == "HTTP 429"


def test_observations_additional_city_adds_coverage_without_live_eligibility(
    monkeypatch,
    tmp_path,
) -> None:
    import argparse
    from weather_data_feed_service import observations

    captured: dict = {}

    def fake_configs(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(observations, "load_city_configs", fake_configs)
    args = argparse.Namespace(
        output=str(tmp_path / "latest.json"),
        now_utc="2026-08-09T03:30:00+00:00",
        include_station_diff=True,
        cities=None,
        additional_cities=["Seoul"],
        timeout_sec=3.0,
        max_workers=1,
        include_fallback_sources=True,
    )

    cache = observations.build_cache(args)

    assert captured["include_research_cities"] is True
    assert captured["research_cities"] == {"Seoul"}
    assert captured["only_cities"] is None
    assert cache["summary"]["additional_cities"] == ["Seoul"]


def test_observations_main_writes_daily_append_only_history(
    monkeypatch,
    tmp_path,
) -> None:
    import json
    from weather_data_feed_service import observations

    generated_at_utc = "2026-08-09T13:30:00+00:00"
    cache = {
        "schema_version": "weather_data_feed_observation_cache_v1",
        "generated_at_utc": generated_at_utc,
        "records": [
            {
                "schema_version": "weather_data_feed_observation_cache_v1",
                "city": "Helsinki",
                "target_date": "2026-08-09",
                "status": "ok",
                "source": "aviationweather_metar",
                "station": "EFHK",
                "last_obs_utc": "2026-08-09T13:20:00+00:00",
                "running_max_c": 23.0,
                "current_temp_c": 22.0,
            }
        ],
        "summary": {"cities": 1, "ok": 1, "non_ok": 0},
    }
    monkeypatch.setattr(observations, "build_cache", lambda _args: cache)
    monkeypatch.setattr(observations, "PRODUCER_BUILD_ID", "test-sha")
    monkeypatch.setattr(observations, "PRODUCER_BUILD_ID_BASIS", "test")
    output = tmp_path / "observations" / "latest.json"

    assert observations.main(["--output", str(output)]) == 0

    latest = json.loads(output.read_text(encoding="utf-8"))
    daily_rows = [
        json.loads(line)
        for line in (output.parent / "2026-08-09" / "observations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert latest == cache
    assert not (output.parent / "observations.jsonl").exists()
    assert len(daily_rows) == 1
    row = daily_rows[0]
    assert row["observation_cache_generated_at_utc"] == generated_at_utc
    assert row["producer"] == "weather_data_feed_service.observations"
    assert row["producer_build_id"] == "test-sha"
    assert row["batch_capture_id"]
    assert row["observation_history_id"]
    assert row["available_at_utc"] == generated_at_utc
    assert row["ingested_at_utc"] == generated_at_utc


def test_observations_cache_keeps_running_max_monotone_across_truncated_fallback(monkeypatch, tmp_path) -> None:
    import argparse
    from weather_data_feed import build_observation_cache, write_observation_cache
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import observations

    cfg = load_city_configs(include_station_diff=False, only_cities={"Wuhan"})[0]
    output = tmp_path / "latest.json"
    previous = {
        "city": "Wuhan",
        "target_date": "2026-07-19",
        "status": "ok",
        "source": "aviationweather_metar",
        "station": "ZHHH",
        "running_max_c": 33.0,
        "running_max_obs_utc": "2026-07-19T09:00:00+00:00",
    }
    write_observation_cache(build_observation_cache([previous]), output)

    monkeypatch.setattr(observations, "load_city_configs", lambda **_kwargs: [cfg])
    monkeypatch.setattr(
        observations,
        "observation_cache_row",
        lambda *_args, **_kwargs: {
            "city": "Wuhan",
            "target_date": "2026-07-19",
            "status": "ok",
            "source": "aviationweather_cache_csv",
            "station": "ZHHH",
            "fetched_at_utc": "2026-07-19T10:17:30+00:00",
            "current_temp_c": 32.0,
            "running_max_c": 32.0,
            "running_max_obs_utc": "2026-07-19T10:00:00+00:00",
            "n_obs": 1,
        },
    )
    args = argparse.Namespace(
        output=str(output),
        now_utc="2026-07-19T10:17:30+00:00",
        include_station_diff=False,
        cities=["Wuhan"],
        timeout_sec=3.0,
        max_workers=1,
        include_fallback_sources=True,
    )

    cache = observations.build_cache(args)
    row = cache["records"][0]

    assert row["source"] == "aviationweather_cache_csv"
    assert row["current_temp_c"] == 32.0
    assert row["running_max_c"] == 33.0
    assert row["decline_c"] == 1.0
    assert row["running_max_obs_utc"] == "2026-07-19T09:00:00+00:00"
    assert row["minutes_since_running_max"] == 77.5
    assert row["history_continuity_status"] == "merged_previous_running_max"
    assert row["history_continuity_raw_running_max_c"] == 32.0
    assert cache["summary"]["running_max_continuity_merges"] == 1


def test_observations_cache_keeps_running_max_after_error_reuse_then_fallback(monkeypatch, tmp_path) -> None:
    import argparse
    from weather_data_feed import build_observation_cache, write_observation_cache
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import observations

    cfg = load_city_configs(include_station_diff=False, only_cities={"Wuhan"})[0]
    output = tmp_path / "latest.json"
    previous = {
        "city": "Wuhan",
        "target_date": "2026-07-19",
        "status": "reused_after_fetch_error",
        "last_success_status": "ok",
        "source": "aviationweather_metar",
        "station": "ZHHH",
        "running_max_c": 33.0,
        "running_max_obs_utc": "2026-07-19T09:00:00+00:00",
    }
    write_observation_cache(build_observation_cache([previous]), output)

    monkeypatch.setattr(observations, "load_city_configs", lambda **_kwargs: [cfg])
    monkeypatch.setattr(
        observations,
        "observation_cache_row",
        lambda *_args, **_kwargs: {
            "city": "Wuhan",
            "target_date": "2026-07-19",
            "status": "ok",
            "source": "aviationweather_cache_csv",
            "station": "ZHHH",
            "fetched_at_utc": "2026-07-19T10:17:30+00:00",
            "current_temp_c": 32.0,
            "running_max_c": 32.0,
            "running_max_obs_utc": "2026-07-19T10:00:00+00:00",
            "n_obs": 1,
        },
    )
    args = argparse.Namespace(
        output=str(output),
        now_utc="2026-07-19T10:17:30+00:00",
        include_station_diff=False,
        cities=["Wuhan"],
        timeout_sec=3.0,
        max_workers=1,
        include_fallback_sources=True,
    )

    row = observations.build_cache(args)["records"][0]

    assert row["running_max_c"] == 33.0
    assert row["history_continuity_status"] == "merged_previous_running_max"


def test_observations_cache_keeps_running_min_monotone_across_truncated_fallback(
    monkeypatch, tmp_path
) -> None:
    import argparse
    from weather_data_feed import build_observation_cache, write_observation_cache
    from weather_data_feed.source_policy import load_city_configs
    from weather_data_feed_service import observations

    cfg = load_city_configs(include_station_diff=False, only_cities={"Wuhan"})[0]
    output = tmp_path / "latest.json"
    previous = {
        "city": "Wuhan",
        "target_date": "2026-07-19",
        "status": "ok",
        "source": "aviationweather_metar",
        "station": "ZHHH",
        "running_max_c": 32.0,
        "running_min_c": 24.0,
        "running_min_obs_utc": "2026-07-19T00:00:00+00:00",
    }
    write_observation_cache(build_observation_cache([previous]), output)
    monkeypatch.setattr(observations, "load_city_configs", lambda **_kwargs: [cfg])
    monkeypatch.setattr(
        observations,
        "observation_cache_row",
        lambda *_args, **_kwargs: {
            "city": "Wuhan",
            "target_date": "2026-07-19",
            "status": "ok",
            "source": "aviationweather_cache_csv",
            "station": "ZHHH",
            "fetched_at_utc": "2026-07-19T10:00:00+00:00",
            "current_temp_c": 29.0,
            "running_max_c": 32.0,
            "running_min_c": 29.0,
            "running_min_obs_utc": "2026-07-19T10:00:00+00:00",
        },
    )
    args = argparse.Namespace(
        output=str(output),
        now_utc="2026-07-19T10:00:00+00:00",
        include_station_diff=False,
        cities=["Wuhan"],
        additional_cities=None,
        timeout_sec=3.0,
        max_workers=1,
        include_fallback_sources=True,
    )

    cache = observations.build_cache(args)
    row = cache["records"][0]

    assert row["running_min_c"] == 24.0
    assert row["rebound_c"] == 5.0
    assert row["minutes_since_running_min"] == 600.0
    assert row["history_continuity_min_status"] == "merged_previous_running_min"
    assert cache["summary"]["running_min_continuity_merges"] == 1


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
