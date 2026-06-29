import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from scripts.ops.weather_source_orderbook_timing_monitor import (
    cycle_once,
    expand_source_names,
    find_markets_for_brackets,
    in_update_window,
    latest_source_event_rows,
    load_monitor_city_configs,
    parse_metar_rmk_temp_c,
    parse_awc_cache_csv_records,
    parse_aviationweather_records,
    parse_metar_report_time,
    parse_metar_temp_c,
    parse_tgftp_header_time,
    source_station_id,
    synoptic_obs_lists,
)
from weather_data_feed.observation_sources.fetchers import _iem_asos_raw_records, arith_round, c_to_f
from src.strategies.weather_edge_v1.official_observation_feed.source_policy import load_city_configs


def test_parse_metar_temp_c_handles_positive_and_negative():
    assert parse_metar_temp_c("ZSPD 170330Z 29003MPS 9999 SCT020 29/23 Q1008") == 29.0
    assert parse_metar_temp_c("KDEN 170330Z 01005KT 10SM FEW020 M03/M08 A2992") == -3.0


def test_parse_metar_rmk_temp_c_exposes_tenth_degree_boundary():
    raw = "KSFO 251956Z 30014KT 10SM FEW006 21/13 A2991 RMK AO2 SLP129 T02060128 $"

    assert parse_metar_temp_c(raw) == 21.0
    assert parse_metar_rmk_temp_c(raw) == 20.6
    assert arith_round(c_to_f(parse_metar_temp_c(raw))) == 70
    assert arith_round(c_to_f(parse_metar_rmk_temp_c(raw))) == 69


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


def test_source_expand_accepts_settlement_basis_discovery_sources():
    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]

    assert expand_source_names(cfg, ["wu_history", "wu_current", "iem_asos_madishf", "iem_asos_routine"]) == [
        "weather_com_history_hourly",
        "weather_com_current",
        "iem_asos_madishf_latest",
        "iem_asos_routine_latest",
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


def test_iem_family_parser_splits_madishf_from_routine_and_keeps_boundary_fields():
    text = "\n".join(
        [
            "station,valid,tmpf,metar",
            "SFO,2026-06-25 19:50,M,KSFO 251950Z AUTO 30014KT 10SM CLR 21/13 A2991 RMK T02100130 MADISHF",
            "SFO,2026-06-25 19:56,69.00,KSFO 251956Z 30014KT 10SM FEW006 21/13 A2991 RMK AO2 SLP129 T02060128 $",
        ]
    )

    tz = ZoneInfo("America/Los_Angeles")
    madishf = _iem_asos_raw_records(text, tz, "2026-06-25", family="madishf")
    routine = _iem_asos_raw_records(text, tz, "2026-06-25", family="routine")

    assert len(madishf) == 1
    assert len(routine) == 1
    assert madishf[0][3]["source_family"] == "madishf"
    assert madishf[0][3]["rmk_temp_c"] == 21.0
    assert madishf[0][3]["temp_round_f"] == 70
    assert routine[0][3]["source_family"] == "routine"
    assert routine[0][3]["rmk_temp_c"] == 20.6
    assert routine[0][3]["temp_round_f"] == 69


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


def test_find_markets_for_brackets_includes_top_tail_for_full_book_monitoring():
    markets = [
        {"groupItemTitle": "79°F or below", "question": "79°F or below"},
        {"groupItemTitle": "80-81°F", "question": "80-81°F"},
        {"groupItemTitle": "82°F or higher", "question": "82°F or higher"},
    ]

    labels = [market["groupItemTitle"] for _bracket, market in find_markets_for_brackets(markets, [79, 80, 82])]

    assert labels == ["79°F or below", "80-81°F", "82°F or higher"]


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


def test_latest_source_event_rows_reads_data_feed_source_events(tmp_path):
    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]
    path = tmp_path / "latest.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "city": "Shanghai",
                        "target_date": "2026-06-17",
                        "source": "aviationweather_metar",
                        "station": "ZSPD",
                        "status": "ok",
                        "temp_c": 27.0,
                        "payload_hash": "hash-primary",
                        "local_detect_ts_utc": "2026-06-17T10:00:01+00:00",
                    },
                    {
                        "city": "Shanghai",
                        "target_date": "2026-06-17",
                        "source": "aviationweather_cache_csv",
                        "station": "ZSPD",
                        "status": "ok",
                        "temp_c": 26.0,
                        "payload_hash": "hash-cache",
                        "local_detect_ts_utc": "2026-06-17T10:00:02+00:00",
                    },
                ]
            }
        )
    )

    rows = latest_source_event_rows(
        path,
        [cfg],
        ["profile_primary", "aviationweather_cache_csv"],
        datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc),
    )

    assert [row["source"] for row in rows["Shanghai"]] == [
        "aviationweather_metar",
        "aviationweather_cache_csv",
    ]
    assert all(row["source_input"] == "data_feed_source_events" for row in rows["Shanghai"])
    assert rows["Shanghai"][0]["temp_c"] == 27.0


def test_cycle_once_uses_source_events_for_signal_inputs(monkeypatch, tmp_path):
    import scripts.ops.weather_source_orderbook_timing_monitor as monitor

    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]
    source_events = tmp_path / "latest.json"
    source_events.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "city": "Shanghai",
                        "target_date": "2026-06-17",
                        "source": "aviationweather_metar",
                        "station": "ZSPD",
                        "status": "ok",
                        "temp_c": 27.0,
                        "payload_hash": "hash-primary",
                        "local_detect_ts_utc": "2026-06-17T10:00:01+00:00",
                    }
                ]
            }
        )
    )
    out_dir = tmp_path / "timing"
    monkeypatch.setattr(monitor, "OUT_DIR", out_dir)

    def fail_live_fetch(*_args, **_kwargs):
        raise AssertionError("cycle_once should not live-fetch weather sources")

    seen_temps = []

    def fake_books(cfg_arg, now_utc, temp_c, *, radius):
        seen_temps.append((cfg_arg.city, temp_c, radius))
        return [
            {
                "city": cfg_arg.city,
                "token_id": "token-1",
                "payload_hash": "book-hash",
                "status": "ok",
            }
        ]

    monkeypatch.setattr(monitor, "source_snapshot", fail_live_fetch)
    monkeypatch.setattr(monitor, "fetch_orderbook_rows", fake_books)

    counts = cycle_once(
        [cfg],
        sources=["profile_primary"],
        bracket_radius=1,
        max_workers=1,
        source_input="source-events",
        source_events_path=source_events,
    )

    assert counts["source_rows"] == 1
    assert counts["book_rows"] == 1
    assert seen_temps == [("Shanghai", 27.0, 1)]
    source_rows = [json.loads(line) for line in (out_dir / "sources.jsonl").read_text().splitlines()]
    assert source_rows[0]["source_input"] == "data_feed_source_events"
