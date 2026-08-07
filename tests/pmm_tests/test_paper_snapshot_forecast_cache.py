from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "weather_data_feed_service/legacy_weather_predict/paper_snapshot.py"
SPEC = importlib.util.spec_from_file_location("paper_snapshot_forecast_cache", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_forecast_curve_cache_is_explicit_and_preserves_pit_timestamp() -> None:
    details = runner._forecast_details_from_curve_row(
        {
            "city": "Amsterdam",
            "target_date": "2026-07-19",
            "forecast_model": "ecmwf",
            "forecast_source": "open_meteo_live_ecmwf",
            "forecast_max_f": 70.2,
            "forecast_peak_hour_local": 15,
            "forecast_peak_time_local": "2026-07-19T15:00",
            "forecast_peak_hour_utc": 13,
            "forecast_peak_time_utc": "2026-07-19T13:00:00Z",
            "forecast_hourly_count": 1,
            "forecast_values_hash": "abc123",
            "forecast_timezone": "Europe/Amsterdam",
            "forecast_timezone_abbreviation": "GMT+2",
            "forecast_utc_offset_seconds": 7200,
            "snapshot_ts_utc": "2026-07-18T16:58:20Z",
            "hourly_curve": [
                {"time_local": "2026-07-19T15:00", "temperature_f": 70.2}
            ],
        },
        cache_age_sec=1800,
    )

    assert details is not None
    assert details["source_model"] == "ecmwf"
    assert details["source_api"] == "open_meteo_live_ecmwf_cached_curve"
    assert details["detected_at_utc"] == "2026-07-18T16:58:20Z"
    assert details["cache_fallback"] is True
    assert details["cache_age_sec"] == 1800.0


def test_forecast_429_disables_repeated_live_calls(monkeypatch) -> None:
    calls = []

    def fake_curl(*args, **kwargs):
        calls.append((args, kwargs))
        return 429, None, "rate limited"

    monkeypatch.setattr(runner, "curl_json_get", fake_curl)
    monkeypatch.setattr(
        runner,
        "_cached_live_forecast",
        lambda city, target_date, model: {"source_model": model, "cache_fallback": True},
    )
    monkeypatch.setattr(runner, "_FORECAST_LIVE_DISABLED_REASON", None)
    cfg = {"lat": 1.0, "lon": 2.0}

    first = runner._refresh_live_forecast(None, "ecmwf", "Amsterdam", cfg, "2026-07-19")
    second = runner._refresh_live_forecast(None, "gfs", "Taipei", cfg, "2026-07-19")

    assert first["cache_fallback"] is True
    assert second["cache_fallback"] is True
    assert runner._FORECAST_LIVE_DISABLED_REASON == "open_meteo_http_429"
    assert len(calls) == 1


def test_fresh_durable_curve_skips_live_forecast_call(monkeypatch) -> None:
    calls = []

    def fake_curl(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, {}, ""

    monkeypatch.setattr(runner, "curl_json_get", fake_curl)
    monkeypatch.setattr(
        runner,
        "_cached_live_forecast",
        lambda *_args: {
            "source_model": "ecmwf",
            "cache_fallback": True,
            "cache_age_sec": 900,
        },
    )
    monkeypatch.setattr(runner, "_FORECAST_LIVE_DISABLED_REASON", None)

    result = runner._refresh_live_forecast(
        None,
        "ecmwf",
        "Amsterdam",
        {"lat": 52.31, "lon": 4.76},
        "2026-08-07",
    )

    assert result["cache_fallback"] is True
    assert calls == []


def test_forecast_fetch_never_reuses_market_proxy(monkeypatch) -> None:
    calls = []

    def fake_curl(*args, **kwargs):
        calls.append((args, kwargs))
        return 429, None, "rate limited"

    monkeypatch.setattr(runner, "curl_json_get", fake_curl)
    monkeypatch.setattr(runner, "PROXY", "http://market-proxy.invalid:8080")
    monkeypatch.setattr(runner, "_cached_live_forecast", lambda *_args: None)
    monkeypatch.setattr(runner, "_FORECAST_LIVE_DISABLED_REASON", None)

    runner._refresh_live_forecast(
        None,
        "gfs",
        "Chengdu",
        {"lat": 30.67, "lon": 104.07},
        "2026-07-28",
    )

    assert len(calls) == 1
    assert "proxy" not in calls[0][1]


def test_snapshot_forecast_consumer_never_calls_network(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(runner, "curl_json_get", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(
        runner,
        "_cached_live_forecast",
        lambda *_args: {"source_model": "gfs", "cache_fallback": True, "cache_age_sec": 7200},
    )

    result = runner._fetch_live_forecast(None, "gfs", "Chengdu", {"lat": 1, "lon": 2}, "2026-08-07")

    assert result["cache_fallback"] is True
    assert calls == []


def test_public_snapshot_fetchers_are_cache_only(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(runner, "curl_json_get", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(runner, "_cached_live_forecast", lambda *_args: None)

    cfg = {"lat": 1, "lon": 2}
    assert runner.fetch_live_gfs(None, "Chengdu", cfg, "2026-08-07") is None
    assert runner.fetch_live_ecmwf(None, "Amsterdam", cfg, "2026-08-07") is None
    assert calls == []


def test_cached_curve_is_not_recaptured_as_new_forecast_evidence() -> None:
    assert runner.should_capture_forecast_curve(
        {"hourly_curve": [{"temperature_f": 70.0}], "cache_fallback": False}
    )
    assert not runner.should_capture_forecast_curve(
        {"hourly_curve": [{"temperature_f": 70.0}], "cache_fallback": True}
    )
