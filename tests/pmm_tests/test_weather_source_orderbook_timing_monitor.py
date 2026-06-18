from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from scripts.ops.weather_source_orderbook_timing_monitor import (
    expand_source_names,
    in_update_window,
    load_monitor_city_configs,
    parse_awc_cache_csv_records,
    parse_aviationweather_records,
    parse_metar_report_time,
    parse_metar_temp_c,
    parse_tgftp_header_time,
    source_station_id,
    synoptic_obs_lists,
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


def test_source_expand_accepts_awc_cache_alias():
    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]

    assert expand_source_names(cfg, ["profile_primary", "awc_cache", "tgftp", "weather_gov", "checkwx", "synoptic"]) == [
        "aviationweather_metar",
        "aviationweather_cache_csv",
        "noaa_tgftp_station_txt",
        "weather_gov_latest",
        "checkwx_html",
        "synopticdata_timeseries",
    ]


def test_tgftp_header_and_metar_report_time_parser():
    text = "2026/06/17 16:00\nZSPD 171600Z 11002MPS 6000 NSC 24/24 Q1008 NOSIG\n"
    header = parse_tgftp_header_time(text)

    assert header is not None
    assert header.isoformat() == "2026-06-17T16:00:00+00:00"
    assert parse_metar_report_time(text.splitlines()[1], header).isoformat() == "2026-06-17T16:00:00+00:00"


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


def test_aviationweather_records_prefer_raw_metar_report_time_over_rounded_api_time():
    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]
    records = parse_aviationweather_records(
        [
            {
                "reportTime": "2026-06-17T18:00:00Z",
                "temp": 26,
                "rawOb": "ZSPD 171750Z 26/23",
            },
        ],
        ZoneInfo(cfg.timezone_name),
        datetime(2026, 6, 18, tzinfo=timezone.utc).date(),
    )

    latest_dt, latest_temp, latest_raw = records[-1]
    assert latest_dt.isoformat() == "2026-06-17T17:50:00+00:00"
    assert latest_temp == 26.0
    assert latest_raw["rawOb"] == "ZSPD 171750Z 26/23"


def test_awc_cache_csv_parser_filters_station_and_local_day():
    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]
    text = "\n".join(
        [
            "station_id,observation_time,temp_c,raw_text",
            "RJTT,2026-06-17T11:00:00Z,27,RJTT 171100Z 27/23",
            "ZSPD,2026-06-17T10:30:00Z,25,ZSPD 171030Z 25/23",
            "ZSPD,2026-06-17T11:00:00Z,26,ZSPD 171100Z 26/23",
        ]
    )

    records = parse_awc_cache_csv_records(
        text,
        cfg.official_icao,
        ZoneInfo(cfg.timezone_name),
        datetime(2026, 6, 17, tzinfo=timezone.utc).date(),
    )

    latest_dt, latest_temp, latest_raw = records[-1]
    assert latest_dt.isoformat() == "2026-06-17T11:00:00+00:00"
    assert latest_temp == 26.0
    assert latest_raw["raw_text"] == "ZSPD 171100Z 26/23"


def test_synoptic_helpers_parse_wrh_station_and_observation_lists():
    assert source_station_id("https://www.weather.gov/wrh/timeseries?site=UUWW") == "UUWW"
    assert source_station_id("RJTT") == "RJTT"

    times, temps = synoptic_obs_lists(
        {
            "STATION": [
                {
                    "OBSERVATIONS": {
                        "date_time": ["2026-06-17T15:00:00Z", "2026-06-17T15:30:00Z"],
                        "air_temp_set_1": [23.0, 24.0],
                    }
                }
            ]
        }
    )

    assert times[-1] == "2026-06-17T15:30:00Z"
    assert temps[-1] == 24.0


def test_monitor_research_city_configs_can_include_blocked_source_profiles():
    default_configs = load_monitor_city_configs(
        include_station_diff=False,
        include_research_cities=False,
        only_cities={"Moscow"},
    )
    research_configs = load_monitor_city_configs(
        include_station_diff=False,
        include_research_cities=True,
        only_cities={"Moscow"},
    )

    assert default_configs == []
    assert len(research_configs) == 1
    assert research_configs[0].city == "Moscow"
    assert research_configs[0].official_icao == "UUWW"
