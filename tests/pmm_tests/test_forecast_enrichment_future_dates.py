from weather_data_feed.forecast_sources import ForecastFetchResult
from weather_data_feed_service.forecast_enrichment import (
    _compact_multi_model_payload,
)


def test_compact_multi_model_payload_keeps_future_dates() -> None:
    result = ForecastFetchResult(
        source_key="open_meteo_multi_model",
        status="ok",
        fetched_at_utc="2026-07-28T00:00:00+00:00",
        latency_ms=10.0,
        payload={
            "daily": {
                "2026-07-28": {
                    "models": {"ECMWF": 95.0, "GFS": 97.0},
                    "model_count": 2,
                },
                "2026-07-29": {
                    "models": {"ECMWF": 94.0, "GFS": 98.0},
                    "model_count": 2,
                },
            },
            "daily_dates": ["2026-07-28", "2026-07-29"],
            "model_metadata": {
                "ECMWF": {"open_meteo_model": "ecmwf_ifs025"}
            },
            "hourly_values_hash_by_model": {"ECMWF": "hash-a"},
        },
    )

    compact = _compact_multi_model_payload(result, "2026-07-28")

    assert compact["target_date"]["models"]["GFS"] == 97.0
    assert compact["daily"]["2026-07-29"]["models"]["GFS"] == 98.0
    assert compact["daily_dates"] == ["2026-07-28", "2026-07-29"]
