from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from weather_data_feed_service import forecast_curve_collector as collector


ROOT = Path(__file__).resolve().parents[2]


def test_market_loop_has_no_forecast_network_producer() -> None:
    market_loop = (ROOT / "scripts/ops/start_mac_weather_data_feed_loop.sh").read_text()
    market_entry = (ROOT / "scripts/ops/start_mac_weather_data_feed_jrs_tmux.sh").read_text()
    forecast_entry = (ROOT / "scripts/ops/start_weather_forecast_curve_collector_v1.sh").read_text()

    assert "forecast-enrichment" not in market_loop
    assert "FORECAST_ENRICHMENT" not in market_entry
    assert "forecast-enrichment" in forecast_entry
    assert "weather_data_feed_service.forecast_curve_collector" in forecast_entry
    assert "--market-snapshot-dir" not in forecast_entry
    assert "--include-research-cities" not in forecast_entry


def test_dedicated_collector_publishes_operational_curve(monkeypatch, tmp_path) -> None:
    now = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(collector.snapshot, "CITIES", {"TestCity": {"lat": 1.0, "lon": 2.0}})
    monkeypatch.setattr(collector.snapshot, "CITY_MODEL", {"TestCity": "gfs"})
    monkeypatch.setattr(collector, "_selected_models", lambda: {"TestCity": "gfs"})
    monkeypatch.setattr(collector.snapshot, "city_scan_dates", lambda *_args: ["2026-08-07"])
    monkeypatch.setattr(collector.snapshot, "local_settle_utc", lambda *_args: now + timedelta(hours=10))
    monkeypatch.setattr(
        collector.snapshot,
        "_refresh_live_forecast",
        lambda *_args: {
            "source_api": "open_meteo_live_gfs",
            "source_model": "gfs",
            "values_hash": "abc",
            "hourly_curve": [{"time_local": "2026-08-07T15:00", "temperature_f": 80.0}],
            "max_f": 80.0,
            "peak_hour_local": 15,
            "peak_time_local": "2026-08-07T15:00",
            "peak_hour_utc": 7,
            "peak_time_utc": "2026-08-07T07:00:00Z",
            "timezone": "UTC+8",
            "timezone_abbreviation": "GMT+8",
            "utc_offset_seconds": 28800,
            "generationtime_ms": 1.0,
            "detected_at_utc": None,
            "cache_fallback": False,
        },
    )

    result = collector.collect(output_root=tmp_path, now_utc=now)

    assert result["status"] == "ok"
    assert result["fresh_city_targets"] == 1
    assert result["reused_city_targets"] == 0
    assert result["request_attempt_count"] == 1
    assert result["owner"] == "weather_forecast_curve_collector_v1"
    assert result["coverage_ratio"] == 1.0
    assert result["capture_path"]


def test_dedicated_collector_reports_missing_cache_or_source(monkeypatch, tmp_path) -> None:
    now = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(collector.snapshot, "CITIES", {"TestCity": {"lat": 1.0, "lon": 2.0}})
    monkeypatch.setattr(collector.snapshot, "CITY_MODEL", {"TestCity": "gfs"})
    monkeypatch.setattr(collector, "_selected_models", lambda: {"TestCity": "gfs"})
    monkeypatch.setattr(collector.snapshot, "city_scan_dates", lambda *_args: ["2026-08-07"])
    monkeypatch.setattr(collector.snapshot, "local_settle_utc", lambda *_args: now + timedelta(hours=10))
    monkeypatch.setattr(collector.snapshot, "_refresh_live_forecast", lambda *_args: None)

    result = collector.collect(output_root=tmp_path, now_utc=now)

    assert result["status"] == "degraded"
    assert result["available_city_targets"] == 0
    assert result["failed_count"] == 1


def test_dedicated_collector_reports_real_provider_failure(monkeypatch, tmp_path) -> None:
    now = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(collector.snapshot, "CITIES", {"TestCity": {"lat": 1.0, "lon": 2.0}})
    monkeypatch.setattr(collector.snapshot, "CITY_MODEL", {"TestCity": "gfs"})
    monkeypatch.setattr(collector, "_selected_models", lambda: {"TestCity": "gfs"})
    monkeypatch.setattr(collector.snapshot, "city_scan_dates", lambda *_args: ["2026-08-07"])
    monkeypatch.setattr(collector.snapshot, "local_settle_utc", lambda *_args: now + timedelta(hours=10))
    monkeypatch.setattr(collector.snapshot, "_cached_live_forecast", lambda *_args: None)
    monkeypatch.setattr(
        collector.snapshot,
        "curl_json_get",
        lambda *_args, **_kwargs: (503, None, "curl_status=503 upstream unavailable"),
    )

    result = collector.collect(output_root=tmp_path, now_utc=now)

    assert result["refresh_status"] == "provider_request_failed"
    assert result["request_attempt_count"] == 1
    assert result["request_failure_counts"] == {"open_meteo_http_503": 1}
    assert result["failed_examples"][0]["reason"] == "open_meteo_http_503"
    assert "upstream unavailable" in result["request_failure_examples"][0]["error"]
