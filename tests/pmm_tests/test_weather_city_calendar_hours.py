from weather_data_feed import city_in_local_hour_window, city_local_hour


def test_city_local_hour_and_wrapped_active_window() -> None:
    now_utc = "2026-07-18T21:30:00+00:00"

    assert city_local_hour("Tokyo", now_utc) == 6.5
    assert city_in_local_hour_window("Tokyo", now_utc, start_hour=6, end_hour=22)
    assert city_in_local_hour_window("Tokyo", now_utc, start_hour=22, end_hour=8)
    assert not city_in_local_hour_window("Tokyo", now_utc, start_hour=8, end_hour=22)
