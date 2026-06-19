from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from weather_data_feed.models import ObservationRecord
from weather_data_feed.observation_sources import (
    ObservationSourceRequest,
    ObservationSourceResult,
    SourceRouter,
    expand_source_names,
    normalize_source_name,
    parse_aviationweather_records,
    parse_awc_cache_csv_records,
    parse_metar_report_time,
    parse_metar_temp_c,
    parse_tgftp_header_time,
)


def test_observation_source_aliases_match_fast_bot_names():
    assert normalize_source_name("awc_cache") == "aviationweather_cache_csv"
    assert normalize_source_name("tgftp") == "noaa_tgftp_station_txt"
    assert normalize_source_name("weather_gov_wrh") == "synopticdata_timeseries"
    assert expand_source_names(
        ["source_profiles", "checkwx"],
        primary="aviationweather",
        fallback_sources=["iem"],
    ) == ["aviationweather_metar", "iem_asos", "checkwx_html"]


def test_metar_parsers_handle_tgftp_and_raw_report_time():
    text = "2026/06/17 18:00\nZSPD 171750Z 12002MPS 9999 SCT020 26/23 Q1008\n"
    header = parse_tgftp_header_time(text)

    assert header is not None
    assert parse_metar_temp_c(text.splitlines()[1]) == 26.0
    assert parse_metar_report_time(text.splitlines()[1], header).isoformat() == "2026-06-17T17:50:00+00:00"


def test_aviationweather_parsers_filter_station_and_local_day():
    tz = ZoneInfo("Asia/Shanghai")
    records = parse_aviationweather_records(
        [
            {"reportTime": "2026-06-17T18:00:00Z", "temp": 26, "rawOb": "ZSPD 171750Z 26/23"},
            {"reportTime": "2026-06-17T10:00:00Z", "temp": 25, "rawOb": "ZSPD 171000Z 25/23"},
        ],
        tz,
        "2026-06-18",
    )

    assert len(records) == 1
    assert records[0][0].isoformat() == "2026-06-17T17:50:00+00:00"

    csv_records = parse_awc_cache_csv_records(
        "\n".join(
            [
                "station_id,observation_time,temp_c,raw_text",
                "RJTT,2026-06-17T18:00:00Z,27,RJTT 171800Z 27/23",
                "ZSPD,2026-06-17T18:00:00Z,26,ZSPD 171750Z 26/23",
            ]
        ),
        "ZSPD",
        tz,
        "2026-06-18",
    )

    assert len(csv_records) == 1
    assert csv_records[0][2]["raw_text"] == "ZSPD 171750Z 26/23"


@dataclass(frozen=True)
class StubAdapter:
    source_key: str = "aviationweather"

    def fetch(self, request: ObservationSourceRequest) -> ObservationSourceResult:
        return ObservationSourceResult(
            source_key=request.source_key,
            status="ok",
            records=(
                ObservationRecord(
                    source_key=request.source_key,
                    city=request.city,
                    target_date=request.target_date,
                    station_or_feed=request.station_or_feed,
                    obs_ts_utc="2026-06-17T17:50:00Z",
                    ingest_ts_utc="2026-06-17T17:50:02Z",
                    temp_c=26.0,
                ),
            ),
        )


def test_source_router_normalizes_adapter_keys():
    router = SourceRouter()
    router.register(StubAdapter())

    result = router.fetch(
        ObservationSourceRequest(
            city="Shanghai",
            station_or_feed="ZSPD",
            target_date="2026-06-18",
            timezone_name="Asia/Shanghai",
            source_key="aviationweather_metar",
        )
    )

    assert result.status == "ok"
    assert result.records[0].source_key == "aviationweather_metar"
