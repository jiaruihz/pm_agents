from weather_data_feed.forecast_history import forecast_hourly_daily_max_local


def test_gmt_archive_is_grouped_by_city_local_date() -> None:
    payload = {
        "timezone": "GMT",
        "utc_offset_seconds": 0,
        "hourly": {
            "time": ["2026-07-01T01:00", "2026-07-01T23:00", "2026-07-02T01:00"],
            "temperature_2m": [70.0, 90.0, 80.0],
        },
    }

    assert forecast_hourly_daily_max_local(payload, city="Seattle") == {
        "2026-06-30": 70.0,
        "2026-07-01": 90.0,
    }
    assert forecast_hourly_daily_max_local(payload, city="Shanghai") == {
        "2026-07-01": 70.0,
        "2026-07-02": 90.0,
    }


def test_archive_timezone_is_respected_before_city_conversion() -> None:
    payload = {
        "timezone": "Asia/Tokyo",
        "hourly": {
            "time": ["2026-07-01T00:00", "2026-07-01T23:00"],
            "temperature_2m": [71.0, 82.0],
        },
    }

    assert forecast_hourly_daily_max_local(payload, city="Tokyo") == {"2026-07-01": 82.0}
