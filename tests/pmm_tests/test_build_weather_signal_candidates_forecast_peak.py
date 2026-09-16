from __future__ import annotations

import json

from scripts.etl.build_weather_signal_candidates import _ForecastPeakIndex


def test_forecast_peak_index_derives_local_peak_from_utc_cache(tmp_path):
    cache_root = tmp_path / "cache"
    folder = cache_root / "gfs_v4"
    folder.mkdir(parents=True)
    (folder / "gfs_v4_Boston_2026-06-01_2026-06-01.json").write_text(
        json.dumps(
            {
                "timezone": "GMT",
                "utc_offset_seconds": 0,
                "hourly": {
                    "time": [
                        "2026-06-01T16:00",
                        "2026-06-01T17:00",
                        "2026-06-01T18:00",
                        "2026-06-01T19:00",
                    ],
                    "temperature_2m": [78.0, 80.0, 83.0, 81.0],
                },
            }
        )
    )

    index = _ForecastPeakIndex(cache_root)
    rec, enriched = index.enrich(
        {
            "city": "Boston",
            "event_date": "2026-06-01",
            "forecast_source": "open_meteo_live_gfs",
            "unit": "F",
            "ts_utc": "2026-06-01T17:30:00Z",
        }
    )

    assert enriched
    assert rec["forecast_peak_hour_local"] == 14
    assert rec["forecast_peak_time_local"] == "2026-06-01T14:00"
    assert rec["forecast_peak_hour_utc"] == 18
    assert rec["forecast_hourly_count"] == 4
    assert rec["forecast_values_hash"]
    assert rec["forecast_peak_delta_hours_local"] == -0.5


def test_forecast_peak_index_does_not_override_snapshot_fields(tmp_path):
    cache_root = tmp_path / "cache"
    folder = cache_root / "gfs_v4"
    folder.mkdir(parents=True)
    (folder / "gfs_v4_Boston_2026-06-01_2026-06-01.json").write_text(
        json.dumps(
            {
                "timezone": "GMT",
                "utc_offset_seconds": 0,
                "hourly": {"time": ["2026-06-01T18:00"], "temperature_2m": [83.0]},
            }
        )
    )

    index = _ForecastPeakIndex(cache_root)
    rec, enriched = index.enrich(
        {
            "city": "Boston",
            "event_date": "2026-06-01",
            "forecast_source": "open_meteo_live_gfs",
            "forecast_peak_hour_local": 13,
            "forecast_values_hash": "snapshot-hash",
        }
    )

    assert not enriched
    assert rec["forecast_peak_hour_local"] == 13
    assert rec["forecast_values_hash"] == "snapshot-hash"
