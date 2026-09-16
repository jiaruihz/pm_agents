from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
from zoneinfo import ZoneInfo

from weather_data_feed.models import ObservationRecord
from weather_data_feed.observation_sources import (
    FetchSettings,
    ObservationSourceRequest,
    ObservationSourceResult,
    SourceRouter,
    build_iem_local_day_params,
    expand_source_names,
    infer_cadence_min,
    normalize_source_name,
    parse_aviationweather_records,
    parse_awc_cache_csv_records,
    parse_iem_asos_records,
    parse_metar_report_time,
    parse_metar_temp_c,
    parse_tgftp_header_time,
    snapshot_observation_source,
)
from weather_data_feed.observation_sources import fetchers
from weather_data_feed.source_policy import load_city_configs


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


def test_iem_parser_and_params_use_city_local_day():
    tz = ZoneInfo("Asia/Shanghai")
    params = build_iem_local_day_params("zspd", tz, "2026-06-18", columns=("tmpc", "dwpc"))

    assert ("station", "ZSPD") in params
    assert ("data", "tmpc") in params
    assert ("data", "dwpc") in params
    assert ("year1", "2026") in params
    assert ("day1", "17") in params
    assert ("day2", "19") in params

    records = parse_iem_asos_records(
        "\n".join(
            [
                "station,valid,tmpc,dwpc",
                "ZSPD,2026-06-17 15:50,24,22",
                "ZSPD,2026-06-17 16:10,25,22",
                "ZSPD,2026-06-18 16:10,26,23",
                "ZSPD,2026-06-18 17:10,M,23",
            ]
        ),
        tz,
        "2026-06-18",
    )

    assert [row[1] for row in records] == [25.0]
    assert records[-1][2]["dwpc"] == "22"


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


def test_infer_cadence_min_from_observation_records():
    records = (
        ObservationRecord(
            source_key="aviationweather_metar",
            city="Shanghai",
            target_date="2026-06-18",
            station_or_feed="ZSPD",
            obs_ts_utc="2026-06-17T10:00:00+00:00",
            ingest_ts_utc="2026-06-17T10:01:00+00:00",
            temp_c=25.0,
        ),
        ObservationRecord(
            source_key="aviationweather_metar",
            city="Shanghai",
            target_date="2026-06-18",
            station_or_feed="ZSPD",
            obs_ts_utc="2026-06-17T10:30:00+00:00",
            ingest_ts_utc="2026-06-17T10:31:00+00:00",
            temp_c=26.0,
        ),
        ObservationRecord(
            source_key="aviationweather_metar",
            city="Shanghai",
            target_date="2026-06-18",
            station_or_feed="ZSPD",
            obs_ts_utc="2026-06-17T11:00:00+00:00",
            ingest_ts_utc="2026-06-17T11:01:00+00:00",
            temp_c=27.0,
        ),
    )

    assert infer_cadence_min(list(records)) == 30.0


def test_snapshot_observation_source_uses_data_feed_fetcher(monkeypatch):
    cfg = load_city_configs(include_station_diff=False, only_cities={"Shanghai"})[0]

    def fake_fetch(request, settings=None):
        assert isinstance(settings, FetchSettings)
        return ObservationSourceResult(
            source_key=request.source_key,
            status="ok",
            records=(
                ObservationRecord(
                    source_key=request.source_key,
                    city=request.city,
                    target_date=request.target_date,
                    station_or_feed=request.station_or_feed,
                    obs_ts_utc="2026-06-17T10:30:00+00:00",
                    ingest_ts_utc="2026-06-17T10:31:00+00:00",
                    temp_c=26.0,
                    raw_text="ZSPD 171030Z 26/23",
                ),
            ),
            fetched_at_utc="2026-06-17T10:31:00+00:00",
            latency_ms=50.0,
            metadata={"raw_payload_hash": "abc", "source_fetch_start_utc": "2026-06-17T10:30:59+00:00"},
        )

    monkeypatch.setattr(fetchers, "fetch_observation_source", fake_fetch)

    row = snapshot_observation_source(
        cfg,
        "aviationweather_metar",
        datetime(2026, 6, 17, 10, 31, tzinfo=timezone.utc),
        settings=FetchSettings(),
        include_record_rows=True,
    )

    assert row["source"] == "aviationweather_metar"
    assert row["temp_c"] == 26.0
    assert row["record_count"] == 1
    assert row["source_report_ts_utc"] == "2026-06-17T10:30:00+00:00"
    assert row["_record_rows"][0]["source_report_ts_utc"] == "2026-06-17T10:30:00+00:00"
    assert row["_record_rows"][0]["raw_metar"] == "ZSPD 171030Z 26/23"


def test_awc_cache_fetcher_decompresses_gzip_payload(monkeypatch):
    fetchers._AWC_CACHE_TEXT = None
    payload = gzip.compress(
        "\n".join(
            [
                "raw_text,station_id,observation_time,temp_c",
                '"METAR ZSPD 171030Z 26/23",ZSPD,2026-06-17T10:30:00.000Z,26',
            ]
        ).encode()
    )

    class Response:
        content = payload

    monkeypatch.setattr(fetchers, "_http_get", lambda *args, **kwargs: Response())

    result = fetchers.fetch_aviationweather_cache_csv(
        ObservationSourceRequest(
            city="Shanghai",
            station_or_feed="ZSPD",
            target_date="2026-06-17",
            timezone_name="Asia/Shanghai",
            source_key="aviationweather_cache_csv",
        )
    )

    assert result.status == "ok"
    assert result.records[0].temp_c == 26.0


def test_http_fetcher_ignores_environment_proxy(monkeypatch):
    calls = []

    class Response:
        text = "ok"
        content = b"ok"

        def raise_for_status(self):
            return None

    def fake_get(*args, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(fetchers.httpx, "get", fake_get)

    fetchers._http_get("https://example.test", settings=FetchSettings(proxy_candidates=(None,)))

    assert calls[0]["proxy"] is None
    assert calls[0]["trust_env"] is False
