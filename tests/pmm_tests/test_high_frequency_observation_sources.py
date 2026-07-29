from __future__ import annotations

import sys
import types
import io
import zipfile
from datetime import datetime, timezone

from weather_data_feed.high_frequency_observation_sources import (
    fetch_aemet_10m,
    fetch_cwa,
    fetch_ims_1m,
    fetch_knmi,
    fetch_meteofrance_6m,
    fetch_metservice_1m,
    parse_aemet_payload,
    parse_dwd_10m_zip,
    parse_eccc_swob_xml,
    parse_hko_csv,
    parse_ims_1m_payload,
    parse_jma_amedas_payload,
    parse_knmi_coverage_json,
    parse_meteofrance_payload,
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
        "20260707051000": {
            "temp": [29.1, 0],
            "wind": [4.0, 0],
            "windDirection": [12, 0],
            "gust": [6.5, 0],
            "gustDirection": [15, 0],
            "precipitation10m": [0.5, 0],
            "precipitation1h": [1.0, 0],
            "precipitation3h": [2.0, 0],
            "precipitation24h": [3.0, 0],
            "observationNumber": 42,
            # JMA daily max/min fields deliberately remain raw-only because
            # their day/time semantics are unsafe as PIT model features.
            "maxTemp": [31.0, 0],
            "maxTempTime": {"hour": 15, "minute": 20},
        },
    }

    rows = parse_jma_amedas_payload(payload, target_date="2026-07-07")

    assert len(rows) == 2
    assert rows[-1]["source"] == "jma_amedas"
    assert rows[-1]["station"] == "44166"
    assert rows[-1]["icao"] == "RJTT"
    assert rows[-1]["observation_time_utc"] == "2026-07-06T20:10:00+00:00"
    assert rows[-1]["temp_c"] == 29.1
    assert rows[-1]["wind_speed_ms"] == 4.0
    assert abs(rows[-1]["wind_speed_kt"] - 7.775) < 0.001
    assert rows[-1]["wind_dir_deg"] == 270.0
    assert abs(rows[-1]["wind_gust_kt"] - 12.635) < 0.001
    assert rows[-1]["wind_gust_dir_deg"] == 337.5
    assert rows[-1]["precipitation_10m_mm"] == 0.5
    assert rows[-1]["precipitation_24h_mm"] == 3.0
    assert rows[-1]["jma_temp_quality_code"] == 0
    assert rows[-1]["jma_observation_number"] == 42
    assert "max_temp_c" not in rows[-1]


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
    assert sources["amos_runway"]["Seoul"]["primary_runway"] == "15L"
    assert sources["amos_runway"]["Seoul"]["preferred_temperature_runway"] == "15R/33L"
    assert sources["noaa_madis_hfmetar"]["New York"]["station"] == "KLGA"
    assert sources["singapore_mss"]["Singapore"]["station"] == "S24"
    assert sources["jma_amedas"]["Tokyo"]["station"] == "44166"
    assert sources["mgm"]["Ankara"]["station"] == "17128"
    assert sources["ims_lod"]["Tel Aviv"]["station"] == "225"
    assert sources["fmi"]["Helsinki"]["icao"] == "EFHK"
    assert sources["knmi"]["Amsterdam"]["icao"] == "EHAM"
    assert sources["meteofrance_6m"]["Paris"]["station"] == "95088001"
    assert sources["dwd_10m"]["Munich"]["icao"] == "EDDM"
    assert sources["aemet_10m"]["Madrid"]["station"] == "3129"
    assert sources["ims_1m"]["Tel Aviv"]["icao"] == "LLBG"
    assert sources["metservice_1m"]["Wellington"]["icao"] == "NZWN"
    assert sources["eccc_swob"]["Toronto"]["icao"] == "CYYZ"


def test_auth_required_sources_are_explicit_when_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("CWA_OPEN_DATA_AUTH", raising=False)
    monkeypatch.delenv("CWA_OPEN_DATA_API_KEY", raising=False)
    monkeypatch.delenv("KNMI_API_KEY", raising=False)
    monkeypatch.delenv("METEOFRANCE_API_TOKEN", raising=False)
    monkeypatch.delenv("METEOFRANCE_API_KEY", raising=False)
    monkeypatch.delenv("AEMET_API_KEY", raising=False)
    monkeypatch.delenv("IMS_API_TOKEN", raising=False)
    monkeypatch.delenv("METSERVICE_API_KEY", raising=False)

    cwa = fetch_cwa("Taipei")
    knmi = fetch_knmi("Amsterdam")
    meteofrance = fetch_meteofrance_6m("Paris")
    aemet = fetch_aemet_10m("Madrid")
    ims = fetch_ims_1m("Tel Aviv")
    metservice = fetch_metservice_1m("Wellington")

    assert cwa.status == "auth_required"
    assert "CWA_OPEN_DATA" in cwa.error
    assert knmi.status == "auth_required"
    assert "KNMI_API_KEY" in knmi.error
    assert meteofrance.status == "auth_required"
    assert "METEOFRANCE" in meteofrance.error
    assert aemet.status == "auth_required"
    assert ims.status == "auth_required"
    assert metservice.status == "auth_required"


def test_knmi_and_meteofrance_fetchers_use_documented_auth_and_temperature_parameter(monkeypatch) -> None:
    from weather_data_feed import high_frequency_observation_sources as sources

    calls: list[dict[str, object]] = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, *, params=None, headers=None, settings=None):
        calls.append({"url": url, "params": params, "headers": headers, "settings": settings})
        if "knmi.nl" in url:
            return Response(
                {
                    "domain": {"axes": {"t": {"values": ["2026-07-15T04:10:00Z"]}}},
                    "ranges": {"ta": {"values": [18.6]}},
                }
            )
        return Response(
            [
                {
                    "type": "Feature",
                    "properties": {"validity_time": "2026-07-15T04:12:00Z", "t": 293.15},
                }
            ]
        )

    monkeypatch.setattr(sources, "_http_get", fake_get)
    monkeypatch.setenv("KNMI_API_KEY", "knmi-key")
    monkeypatch.setenv("METEOFRANCE_API_KEY", "mf-key")

    assert fetch_knmi("Amsterdam").status == "ok"
    assert fetch_meteofrance_6m("Paris").status == "ok"
    assert calls[0]["params"]["parameter-name"] == "ta"
    assert calls[0]["headers"]["Authorization"] == "knmi-key"
    assert calls[1]["headers"]["Authorization"] == "Bearer mf-key"
    assert "/DPObs/v2/station/infrahoraire-6m" in calls[1]["url"]


def test_new_official_source_parsers_normalize_temperature_and_timestamps() -> None:
    fetched = datetime(2026, 7, 15, 4, 20, tzinfo=timezone.utc)
    knmi = parse_knmi_coverage_json(
        {
            "coverages": [
                {
                    "domain": {"axes": {"t": {"values": ["2026-07-15T04:10:00Z"]}}},
                    "ranges": {"ta": {"values": [18.6]}},
                    "eumetnet:locationId": "0-20000-0-06240",
                }
            ]
        },
        target_date="2026-07-15",
        fetched_at=fetched,
    )
    meteofrance = parse_meteofrance_payload(
        [
            {
                "type": "Feature",
                "properties": {
                    "validity_time": "2026-07-15T04:12:00Z",
                    "reference_time": "2026-07-15T04:18:00Z",
                    "t": 293.15,
                    "u": 64,
                },
            }
        ],
        target_date="2026-07-15",
        fetched_at=fetched,
    )
    aemet = parse_aemet_payload(
        [{"fint": "2026-07-15T06:10:00+02:00", "ta": 21.4, "hr": 51}],
        target_date="2026-07-15",
        fetched_at=fetched,
    )
    ims = parse_ims_1m_payload(
        {
            "data": [
                {
                    "datetime": "2026-07-15T06:15:00",
                    "channels": [{"name": "TD", "value": 25.7}, {"name": "RH", "value": 48}],
                }
            ]
        },
        target_date="2026-07-15",
        fetched_at=fetched,
    )

    assert knmi[0]["temp_c"] == 18.6
    assert knmi[0]["observation_time_utc"] == "2026-07-15T04:10:00+00:00"
    assert meteofrance[0]["temp_c"] == 20.0
    assert meteofrance[0]["station"] == "95088001"
    assert aemet[0]["observation_time_utc"] == "2026-07-15T04:10:00+00:00"
    assert ims[0]["observation_time_utc"] == "2026-07-15T03:15:00+00:00"


def test_dwd_and_eccc_public_parsers_keep_source_publication_semantics() -> None:
    csv_text = "STATIONS_ID;MESS_DATUM;QN;TT_10;RF_10;TD_10;eor\n01262;202607150400;2;18.2;72.0;13.1;eor\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("produkt_zehn_now_tu_test.txt", csv_text)
    dwd = parse_dwd_10m_zip(buffer.getvalue(), target_date="2026-07-15")
    eccc = parse_eccc_swob_xml(
        """<root><element name="date_tm" value="2026-07-15T04:00:00.000Z"/><element name="air_temp" value="22.3"/><element name="max_air_temp_pst1hr" value="22.8"/></root>""",
        target_date="2026-07-15",
    )

    assert dwd[0]["temp_c"] == 18.2
    assert dwd[0]["station"] == "01262"
    assert eccc[0]["temp_c"] == 22.3
    assert eccc[0]["max_temp_c_past_1h"] == 22.8


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


def test_high_frequency_always_active_cities_bypass_daytime_window() -> None:
    from weather_data_feed_service.high_frequency_observations import _jobs, build_parser

    args = build_parser().parse_args(
        [
            "--sources",
            "jma_amedas",
            "singapore_mss",
            "--active-local-start-hour",
            "6",
            "--active-local-end-hour",
            "22",
            "--always-active-cities",
            "Tokyo",
        ]
    )
    active, skipped = _jobs(args.sources, args.cities, datetime(2026, 7, 14, 17, 0, tzinfo=timezone.utc), args)

    assert ("jma_amedas", "Tokyo") in active
    assert any(row["city"] == "Singapore" and row["reason"] == "outside_active_local_window" for row in skipped)


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


def test_high_frequency_minute_window_interval_overrides_source_interval() -> None:
    from weather_data_feed_service.high_frequency_observations import (
        _apply_active_minute_window_intervals,
        _parse_source_minute_window_intervals,
    )

    rules = _parse_source_minute_window_intervals(["jma_amedas=5-8,15-18,25-28,35-38,45-48,55-58:20"])
    intervals, active = _apply_active_minute_window_intervals(
        {"jma_amedas": 300.0, "fmi": 300.0},
        rules,
        now_utc=datetime(2026, 7, 9, 4, 37, 12, tzinfo=timezone.utc),
    )

    assert intervals["jma_amedas"] == 20.0
    assert intervals["fmi"] == 300.0
    assert active[0]["source"] == "jma_amedas"
    assert active[0]["window_start_minute"] == 35.0


def test_high_frequency_minute_window_interval_keeps_base_outside_window() -> None:
    from weather_data_feed_service.high_frequency_observations import (
        _apply_active_minute_window_intervals,
        _parse_source_minute_window_intervals,
    )

    rules = _parse_source_minute_window_intervals(["jma_amedas=5-8,15-18,25-28,35-38,45-48,55-58:20"])
    intervals, active = _apply_active_minute_window_intervals(
        {"jma_amedas": 300.0},
        rules,
        now_utc=datetime(2026, 7, 9, 4, 39, 0, tzinfo=timezone.utc),
    )

    assert intervals["jma_amedas"] == 300.0
    assert active == []
