from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from weather_data_feed.models import CityConfig, ObservationRecord
from weather_data_feed.source_policy import load_city_configs
from weather_data_feed.observation_sources import FetchSettings
from weather_data_feed_service import observations


def _record(ts: str) -> ObservationRecord:
    return ObservationRecord(
        source_key="test",
        city="Amsterdam",
        target_date="2026-08-06",
        station_or_feed="EHAM",
        obs_ts_utc=ts,
        ingest_ts_utc="2026-08-06T07:40:00+00:00",
        temp_c=20.0,
    )


def _result(ts: str):
    return SimpleNamespace(
        records=(_record(ts),),
        status="ok",
        error="",
        fetched_at_utc="2026-08-06T07:40:00+00:00",
    )


def _cfg() -> CityConfig:
    return load_city_configs(include_station_diff=True, only_cities={"Amsterdam"})[0]


def test_fetch_result_continues_after_stale_primary(monkeypatch) -> None:
    by_source = {
        "aviationweather_metar": _result("2026-08-06T05:25:00+00:00"),
        "noaa_tgftp_station_txt": _result("2026-08-06T07:25:00+00:00"),
    }
    monkeypatch.setattr(
        observations,
        "fetch_observation_source",
        lambda request, settings: by_source[request.source_key],
    )

    source, result = observations._fetch_result(
        _cfg(),
        "2026-08-06",
        FetchSettings(),
        ["aviationweather_metar", "noaa_tgftp_station_txt"],
    )

    assert source == "noaa_tgftp_station_txt"
    assert result.records[-1].obs_ts_utc == "2026-08-06T07:25:00+00:00"


def test_fetch_result_returns_freshest_stale_source(monkeypatch) -> None:
    by_source = {
        "aviationweather_metar": _result("2026-08-06T04:00:00+00:00"),
        "noaa_tgftp_station_txt": _result("2026-08-06T05:00:00+00:00"),
    }
    monkeypatch.setattr(
        observations,
        "fetch_observation_source",
        lambda request, settings: by_source[request.source_key],
    )

    source, _result_row = observations._fetch_result(
        _cfg(),
        "2026-08-06",
        FetchSettings(),
        ["aviationweather_metar", "noaa_tgftp_station_txt"],
    )

    assert source == "noaa_tgftp_station_txt"
