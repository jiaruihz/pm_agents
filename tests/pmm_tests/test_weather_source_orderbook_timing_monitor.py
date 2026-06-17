from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from scripts.ops.weather_source_orderbook_timing_monitor import (
    expand_source_names,
    in_update_window,
    parse_aviationweather_records,
    parse_metar_temp_c,
)
from src.strategies.weather_edge_v1.official_observation_feed.source_policy import load_city_configs


def test_parse_metar_temp_c_handles_positive_and_negative():
    assert parse_metar_temp_c("ZSPD 170330Z 29003MPS 9999 SCT020 29/23 Q1008") == 29.0
    assert parse_metar_temp_c("KDEN 170330Z 01005KT 10SM FEW020 M03/M08 A2992") == -3.0


def test_in_update_window_targets_hour_and_half_hour():
    assert in_update_window(datetime(2026, 6, 17, 3, 59, tzinfo=timezone.utc), window_min=2)
    assert in_update_window(datetime(2026, 6, 17, 4, 31, tzinfo=timezone.utc), window_min=2)
    assert not in_update_window(datetime(2026, 6, 17, 4, 10, tzinfo=timezone.utc), window_min=2)


def test_source_profiles_expand_to_primary_and_fallback_sources():
    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]

    assert expand_source_names(cfg, ["source_profiles", "checkwx_html"]) == [
        "aviationweather_metar",
        "iem_asos",
        "checkwx_html",
    ]


def test_aviationweather_records_keep_raw_payload_aligned_to_latest_time():
    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]
    records = parse_aviationweather_records(
        [
            {"reportTime": "2026-06-17T11:00:00Z", "temp": 26, "rawOb": "ZSPD 171100Z 26/23"},
            {"reportTime": "2026-06-17T10:00:00Z", "temp": 25, "rawOb": "ZSPD 171000Z 25/23"},
        ],
        ZoneInfo(cfg.timezone_name),
        datetime(2026, 6, 17, tzinfo=timezone.utc).date(),
    )

    latest_dt, latest_temp, latest_raw = records[-1]
    assert latest_dt.isoformat() == "2026-06-17T11:00:00+00:00"
    assert latest_temp == 26.0
    assert latest_raw["rawOb"] == "ZSPD 171100Z 26/23"
