from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from us_fast_weather_lab.commercial_ws import (
    MetarWsCollector,
    MetarWsConfig,
    SynopticPushCollector,
    SynopticPushConfig,
    metar_ws_source_id,
    synoptic_temperature_payload,
)
from us_fast_weather_lab.model import metar_event
from us_fast_weather_lab.reports import _paired_rows
from us_fast_weather_lab.storage import ClockState, EvidenceStore
import us_fast_weather_lab.commercial_ws as commercial_ws


def _store(tmp_path) -> EvidenceStore:
    store = EvidenceStore(tmp_path / "runtime")
    store.start_run(config_hash="commercial", vantage_id="TEST")
    store.set_clock_state(ClockState(1.0, True))
    return store


def test_metar_ws_channel_classes_are_not_collapsed() -> None:
    assert metar_ws_source_id("metar.obs.katl") == "METAR_WS_METAR"
    assert metar_ws_source_id("metar.obs10.katl") == "METAR_WS_HFMETAR"
    assert metar_ws_source_id("metar.atis.katl") == "METAR_WS_DATIS"


def test_metar_ws_cached_replay_is_preserved_but_excluded_from_pairs(tmp_path) -> None:
    store = _store(tmp_path)
    collector = MetarWsCollector(
        store,
        config=MetarWsConfig("wss://stream.metar.ws/v1/ws", 3.0, ("metar.obs.katl",)),
        api_key="test-only-key",
        stations={"KATL"},
        vantage_id="TEST",
    )
    wall_ns = int(datetime(2026, 8, 29, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    raw_report = "METAR KATL 290052Z 00000KT 10SM CLR 25/20 A3000"
    collector.process_frame(
        json.dumps(
            {
                "cached": True,
                "type": "publication",
                "channel": "metar.obs.katl",
                "data": {
                    "station": "KATL",
                    "report_time": "2026-08-29T00:52:00Z",
                    "temp_c": "25",
                    "raw": raw_report,
                },
            }
        ),
        wall_ns=wall_ns,
        monotonic_ns=time.monotonic_ns(),
    )
    cached_seen = store.query("SELECT source_id, evidence_status FROM source_observation_seen")[0]
    assert cached_seen["source_id"] == "METAR_WS_METAR"
    assert cached_seen["evidence_status"] == "cached_replay_non_latency"

    awc_capture = store.capture_transport(
        source_id="AWC_API",
        endpoint="aviationweather.gov",
        topic="request",
        payload=raw_report.encode(),
        wall_ns=wall_ns + 1_000_000,
        monotonic_ns=time.monotonic_ns(),
    )
    event = metar_event(raw_report, reference_ns=wall_ns)
    assert event
    store.record_observation(
        capture=awc_capture,
        event=event,
        source_id="AWC_API",
        vantage_id="TEST",
        notification_ns=None,
        fetch_started_ns=wall_ns,
        fetch_finished_ns=wall_ns,
        decoded_ns=wall_ns + 1_000_000,
    )
    assert _paired_rows(store._conn, valid_only=False) == []  # noqa: SLF001
    store.close()


def test_synoptic_air_temperature_becomes_distinct_high_frequency_event(tmp_path) -> None:
    message = {
        "type": "data",
        "data": [
            {"set": 1, "stid": "KATL", "value": 25.3, "qc": [], "date": 202608290055, "sensor": "air_temp"},
            {"set": 1, "stid": "KATL", "value": 2.0, "qc": [], "date": 202608290055, "sensor": "wind_speed"},
        ],
    }
    normalized = synoptic_temperature_payload(message, {"KATL"})
    assert normalized is not None
    assert json.loads(normalized) == [
        {
            "air_temperature_c": 25.3,
            "observation_time": "2026-08-29T00:55:00+00:00",
            "qc": [],
            "report_kind": "HF_TEMPERATURE",
            "sensor_set": 1,
            "station_id": "KATL",
        }
    ]

    store = _store(tmp_path)
    collector = SynopticPushCollector(
        store,
        config=SynopticPushConfig(
            "wss://push.synopticdata.com/feed", 3.0, ("KATL",), ("air_temp",)
        ),
        api_token="test-only-token",
        vantage_id="TEST",
    )
    wall_ns = int(datetime(2026, 8, 29, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    collector.process_frame(json.dumps(message), wall_ns=wall_ns, monotonic_ns=time.monotonic_ns())
    event = store.query("SELECT station_id, report_kind, air_temperature_c FROM observation_event")[0]
    assert event["station_id"] == "KATL"
    assert event["report_kind"] == "HF_TEMPERATURE"
    assert event["air_temperature_c"] == 25.3
    store.close()


def test_metar_ws_datis_is_not_same_report_as_official_metar(tmp_path) -> None:
    store = _store(tmp_path)
    collector = MetarWsCollector(
        store,
        config=MetarWsConfig("wss://stream.metar.ws/v1/ws", 3.0, ("metar.atis.katl",)),
        api_key="test-only-key",
        stations={"KATL"},
        vantage_id="TEST",
    )
    wall_ns = int(datetime(2026, 8, 29, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    raw_report = "METAR KATL 290052Z 00000KT 10SM CLR 25/20 A3000"
    collector.process_frame(
        json.dumps(
            {
                "cached": False,
                "type": "publication",
                "channel": "metar.atis.katl",
                "data": {
                    "station": "KATL",
                    "report_time": "2026-08-29T00:52:00Z",
                    "temp_c": "25",
                    "raw": raw_report,
                },
            }
        ),
        wall_ns=wall_ns,
        monotonic_ns=time.monotonic_ns(),
    )
    datis = store.query("SELECT report_kind FROM observation_event")[0]
    assert datis["report_kind"] == "DATIS_DERIVED"

    awc_capture = store.capture_transport(
        source_id="AWC_API",
        endpoint="aviationweather.gov",
        topic="request",
        payload=raw_report.encode(),
        wall_ns=wall_ns + 1_000_000,
        monotonic_ns=time.monotonic_ns(),
    )
    event = metar_event(raw_report, reference_ns=wall_ns)
    assert event
    store.record_observation(
        capture=awc_capture,
        event=event,
        source_id="AWC_API",
        vantage_id="TEST",
        notification_ns=None,
        fetch_started_ns=wall_ns,
        fetch_finished_ns=wall_ns,
        decoded_ns=wall_ns + 1_000_000,
    )
    assert _paired_rows(store._conn, valid_only=False) == []  # noqa: SLF001
    store.close()


def test_commercial_reconnect_backoff_is_interruptible(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)

    def fail_connect(*_args, **_kwargs):
        raise OSError("synthetic connection failure")

    monkeypatch.setattr(commercial_ws.websockets, "connect", fail_connect)
    collector = MetarWsCollector(
        store,
        config=MetarWsConfig("wss://stream.metar.ws/v1/ws", 30.0, ("metar.obs.katl",)),
        api_key="test-only-key",
        stations={"KATL"},
        vantage_id="TEST",
    )
    collector.start()
    deadline = time.monotonic() + 2.0
    while collector.errors == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    collector.stop()
    assert time.monotonic() - started < 1.0
    assert collector._thread is not None and not collector._thread.is_alive()  # noqa: SLF001
    store.close()


def test_synoptic_failed_resume_falls_back_to_fresh_url(tmp_path) -> None:
    store = _store(tmp_path)
    collector = SynopticPushCollector(
        store,
        config=SynopticPushConfig(
            "wss://push.synopticdata.com/feed", 3.0, ("KATL",), ("air_temp",)
        ),
        api_token="test-only-token",
        vantage_id="TEST",
    )
    collector.session_id = "expired-session"
    assert collector._connection_url().endswith("/test-only-token/expired-session")  # noqa: SLF001
    collector._invalidate_failed_resume(True)  # noqa: SLF001
    assert collector.session_id is None
    fresh_url = collector._connection_url()  # noqa: SLF001
    assert "/test-only-token/?" in fresh_url
    assert "stid=KATL" in fresh_url
    store.close()
