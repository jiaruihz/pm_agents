from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

from weather_data_feed.high_frequency_observation_sources import (
    fetch_cwa,
    fetch_knmi,
    parse_hko_csv,
    parse_jma_amedas_payload,
    parse_singapore_mss_payload,
    supported_high_frequency_sources,
)


def test_singapore_mss_parser_extracts_s24_changi_reference_station() -> None:
    payload = {
        "metadata": {"stations": [{"id": "S24", "name": "Upper Changi Road North"}]},
        "items": [
            {
                "timestamp": "2026-07-07T06:01:00+08:00",
                "readings": [{"station_id": "S24", "value": 31.4}],
            }
        ],
    }

    rows = parse_singapore_mss_payload(
        payload,
        target_date="2026-07-07",
        fetched_at=datetime(2026, 7, 6, 22, 2, tzinfo=timezone.utc),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "singapore_mss"
    assert row["city"] == "Singapore"
    assert row["station"] == "S24"
    assert row["icao"] == "WSSS"
    assert row["observation_time_utc"] == "2026-07-06T22:01:00+00:00"
    assert row["temp_c"] == 31.4
    assert row["source_kind"] == "official_reference_station"


def test_singapore_mss_parser_accepts_v2_open_data_shape() -> None:
    payload = {
        "code": 0,
        "data": {
            "stations": [{"id": "S24", "name": "Upper Changi Road North"}],
            "readings": [
                {
                    "timestamp": "2026-07-08T02:29:00+08:00",
                    "data": [{"stationId": "S24", "value": 29}],
                }
            ],
        },
    }

    rows = parse_singapore_mss_payload(payload, target_date="2026-07-08")

    assert len(rows) == 1
    assert rows[0]["station"] == "S24"
    assert rows[0]["observation_time_utc"] == "2026-07-07T18:29:00+00:00"
    assert rows[0]["temp_c"] == 29.0


def test_jma_amedas_parser_extracts_haneda_ten_minute_temp() -> None:
    payload = {
        "20260707050000": {"temp": [28.8, 0]},
        "20260707051000": {"temp": [29.1, 0]},
    }

    rows = parse_jma_amedas_payload(payload, target_date="2026-07-07")

    assert len(rows) == 2
    assert rows[-1]["source"] == "jma_amedas"
    assert rows[-1]["station"] == "44166"
    assert rows[-1]["icao"] == "RJTT"
    assert rows[-1]["observation_time_utc"] == "2026-07-06T20:10:00+00:00"
    assert rows[-1]["temp_c"] == 29.1


def test_hko_csv_parser_extracts_named_station() -> None:
    text = "\n".join(
        [
            "Automatic Weather Station,Date time,Air Temperature(degree Celsius)",
            "HK Observatory,202607071215,32.0",
            "Lau Fau Shan,202607071215,33.2",
        ]
    )

    rows = parse_hko_csv(text, city="Hong Kong", target_date="2026-07-07")

    assert len(rows) == 1
    assert rows[0]["source"] == "hko_obs"
    assert rows[0]["station"] == "HK Observatory"
    assert rows[0]["observation_time_utc"] == "2026-07-07T04:15:00+00:00"
    assert rows[0]["temp_c"] == 32.0


def test_supported_high_frequency_sources_cover_requested_open_project_sources() -> None:
    sources = supported_high_frequency_sources()

    assert sources["amos_runway"]["Seoul"]["station"] == "RKSI"
    assert sources["noaa_madis_hfmetar"]["New York"]["station"] == "KLGA"
    assert sources["singapore_mss"]["Singapore"]["station"] == "S24"
    assert sources["jma_amedas"]["Tokyo"]["station"] == "44166"
    assert sources["mgm"]["Ankara"]["station"] == "17128"
    assert sources["ims_lod"]["Tel Aviv"]["station"] == "225"
    assert sources["fmi"]["Helsinki"]["icao"] == "EFHK"
    assert sources["knmi"]["Amsterdam"]["icao"] == "EHAM"


def test_auth_required_sources_are_explicit_when_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("CWA_OPEN_DATA_AUTH", raising=False)
    monkeypatch.delenv("CWA_OPEN_DATA_API_KEY", raising=False)
    monkeypatch.delenv("KNMI_API_KEY", raising=False)

    cwa = fetch_cwa("Taipei")
    knmi = fetch_knmi("Amsterdam")

    assert cwa.status == "auth_required"
    assert "CWA_OPEN_DATA" in cwa.error
    assert knmi.status == "auth_required"
    assert "KNMI_API_KEY" in knmi.error


def test_high_frequency_observations_cli_dispatches_runner_args(monkeypatch) -> None:
    from weather_data_feed_service import cli

    calls = []

    def fake_main(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    fake_module = types.SimpleNamespace(main=fake_main)
    monkeypatch.setitem(sys.modules, "weather_data_feed_service.high_frequency_observations", fake_module)

    rc = cli.main(["high-frequency-observations", "--", "--cities", "Singapore", "--sources", "singapore_mss"])

    assert rc == 0
    assert calls == [["--cities", "Singapore", "--sources", "singapore_mss"]]


def test_high_frequency_history_append_rows_keep_latest_per_station() -> None:
    from weather_data_feed_service.high_frequency_observations import append_history_rows

    rows = [
        {"source": "noaa_madis_hfmetar", "city": "New York", "station": "KLGA", "observation_time_utc": "2026-07-08T01:00:00+00:00", "temp_c": 20},
        {"source": "noaa_madis_hfmetar", "city": "New York", "station": "KLGA", "observation_time_utc": "2026-07-08T01:05:00+00:00", "temp_c": 21},
        {"source": "jma_amedas", "city": "Tokyo", "station": "44166", "observation_time_utc": "2026-07-08T01:00:00+00:00", "temp_c": 29},
    ]

    append_rows = append_history_rows(rows)

    assert len(append_rows) == 2
    assert [row for row in append_rows if row["city"] == "New York"][0]["temp_c"] == 21
