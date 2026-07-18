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
