from datetime import datetime, timezone

from scripts.ops.weather_source_orderbook_timing_monitor import (
    in_update_window,
    parse_metar_temp_c,
)


def test_parse_metar_temp_c_handles_positive_and_negative():
    assert parse_metar_temp_c("ZSPD 170330Z 29003MPS 9999 SCT020 29/23 Q1008") == 29.0
    assert parse_metar_temp_c("KDEN 170330Z 01005KT 10SM FEW020 M03/M08 A2992") == -3.0


def test_in_update_window_targets_hour_and_half_hour():
    assert in_update_window(datetime(2026, 6, 17, 3, 59, tzinfo=timezone.utc), window_min=2)
    assert in_update_window(datetime(2026, 6, 17, 4, 31, tzinfo=timezone.utc), window_min=2)
    assert not in_update_window(datetime(2026, 6, 17, 4, 10, tzinfo=timezone.utc), window_min=2)
