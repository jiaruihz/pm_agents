from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from us_fast_weather_lab.cli import command_replay
from us_fast_weather_lab.model import metar_event
from us_fast_weather_lab.storage import ClockState, EvidenceStore


def _store(tmp_path) -> EvidenceStore:
    store = EvidenceStore(tmp_path / "runtime")
    store.start_run(config_hash="abc", vantage_id="TEST")
    store.set_clock_state(ClockState(1.0, True))
    return store


def test_raw_capture_is_append_only_and_content_addressed(tmp_path) -> None:
    store = _store(tmp_path)
    payload = b'{"id":"message-1"}'
    first = store.capture_transport(
        source_id="WIS2_ORIGIN",
        endpoint="broker-a",
        topic="origin/test",
        payload=payload,
        wall_ns=time.time_ns(),
        monotonic_ns=time.monotonic_ns(),
    )
    second = store.capture_transport(
        source_id="WIS2_ORIGIN",
        endpoint="broker-a",
        topic="origin/test",
        payload=payload,
        wall_ns=time.time_ns(),
        monotonic_ns=time.monotonic_ns(),
    )
    assert first.transport_message_id != second.transport_message_id
    assert first.raw_payload_sha256 == second.raw_payload_sha256
    assert len(store.query("SELECT * FROM transport_message")) == 2
    raw_path = store.query("SELECT raw_payload_path FROM transport_message LIMIT 1")[0][0]
    assert raw_path
    store.close()


def test_schema_rejects_update_delete_and_preserves_revisions(tmp_path) -> None:
    store = _store(tmp_path)
    capture = store.capture_transport(
        source_id="AWC_API",
        endpoint="aviationweather.gov",
        topic="request",
        payload=b"payload",
        wall_ns=time.time_ns(),
        monotonic_ns=time.monotonic_ns(),
    )
    reference_ns = int(datetime(2026, 8, 28, 11, tzinfo=timezone.utc).timestamp() * 1e9)
    first = metar_event("METAR KJFK 281051Z 05003KT 10SM 22/21 A2998", reference_ns=reference_ns)
    correction = metar_event("METAR KJFK 281051Z COR 05003KT 10SM 22/21 A2998", reference_ns=reference_ns)
    assert first and correction
    for event in (first, correction):
        store.record_observation(
            capture=capture,
            event=event,
            source_id="AWC_API",
            vantage_id="TEST",
            notification_ns=None,
            fetch_started_ns=capture.wall_ns - 100,
            fetch_finished_ns=capture.wall_ns + 100,
            decoded_ns=capture.wall_ns + 200,
        )
    assert len(store.query("SELECT * FROM observation_event")) == 2
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("UPDATE observation_event SET station_id='XXXX'")  # noqa: SLF001
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("DELETE FROM transport_message")  # noqa: SLF001
    store.close()


def test_wis2_notification_and_fetch_rows_are_indexed(tmp_path) -> None:
    store = _store(tmp_path)
    capture = store.capture_transport(
        source_id="WIS2_CACHE",
        endpoint="broker-a",
        topic="cache/test",
        payload=b"{}",
        wall_ns=time.time_ns(),
        monotonic_ns=time.monotonic_ns(),
    )
    notification = {
        "id": "00000000-0000-0000-0000-000000000001",
        "properties": {"pubtime": "2026-08-28T11:00:00Z", "data_id": "data-1", "cache": True},
        "links": [{"rel": "canonical", "href": "https://example.test/metar.txt"}],
    }
    store.record_notification(capture, notification)
    store.record_fetch(
        capture,
        {
            "relation": "canonical",
            "url": "https://example.test/metar.txt",
            "started_at_ns": capture.wall_ns,
            "first_byte_at_ns": capture.wall_ns + 1,
            "finished_at_ns": capture.wall_ns + 2,
            "http_status": 200,
            "content_type": "text/plain",
            "content_encoding": "",
            "sniffed_format": "tac_or_wmo_text",
            "payload": b"METAR KJFK 281051Z 05003KT 10SM 22/21 A2998",
            "integrity_status": "not_provided",
        },
    )
    assert len(store.query("SELECT * FROM wis2_notification")) == 1
    assert len(store.query("SELECT * FROM payload_fetch")) == 1
    enriched = store.query(
        "SELECT source_message_id, source_data_id, source_pubtime FROM transport_message_enriched"
    )[0]
    assert enriched["source_message_id"] == notification["id"]
    assert enriched["source_data_id"] == "data-1"
    assert enriched["source_pubtime"] == "2026-08-28T11:00:00Z"
    store.close()


def test_replay_audit_is_scoped_to_latest_collector_run(tmp_path) -> None:
    runtime_root = tmp_path / "runtime"
    store = EvidenceStore(runtime_root)
    store.set_clock_state(ClockState(1.0, True))

    store.start_run(config_hash="old", vantage_id="TEST")
    old_capture = store.capture_transport(
        source_id="AWC_API",
        endpoint="aviationweather.gov",
        topic="request",
        payload=b"old payload",
        wall_ns=time.time_ns(),
        monotonic_ns=time.monotonic_ns(),
    )
    reference_ns = int(datetime(2026, 8, 28, 11, tzinfo=timezone.utc).timestamp() * 1e9)
    old_event = metar_event("METAR KJFK 281051Z 05003KT 10SM 22/21 A2998", reference_ns=reference_ns)
    assert old_event
    store.record_observation(
        capture=old_capture,
        event=old_event,
        source_id="AWC_API",
        vantage_id="TEST",
        notification_ns=None,
        fetch_started_ns=old_capture.wall_ns,
        fetch_finished_ns=old_capture.wall_ns,
        decoded_ns=old_capture.wall_ns,
    )
    store.end_run("test rollover")

    latest_run_id = store.start_run(config_hash="new", vantage_id="TEST")
    new_capture = store.capture_transport(
        source_id="AWC_API",
        endpoint="aviationweather.gov",
        topic="request",
        payload=b"new payload",
        wall_ns=time.time_ns(),
        monotonic_ns=time.monotonic_ns(),
    )
    new_event = metar_event("METAR KLAX 281053Z 26005KT 10SM 21/18 A2997", reference_ns=reference_ns)
    assert new_event
    store.record_observation(
        capture=new_capture,
        event=new_event,
        source_id="AWC_API",
        vantage_id="TEST",
        notification_ns=None,
        fetch_started_ns=new_capture.wall_ns,
        fetch_finished_ns=new_capture.wall_ns,
        decoded_ns=new_capture.wall_ns,
    )
    rawless_capture = store.capture_transport(
        source_id="SYNOPTIC_PUSH_HF_TEMP",
        endpoint="push.synopticdata.com",
        topic="data",
        payload=b'{"type":"data"}',
        wall_ns=time.time_ns(),
        monotonic_ns=time.monotonic_ns(),
    )
    rawless_event = metar_event(
        "",
        reference_ns=reference_ns,
        override={
            "station_id": "KLAX",
            "observation_time": "2026-08-28T10:54:00+00:00",
            "report_kind": "HF_TEMPERATURE",
            "air_temperature_c": 21.4,
        },
    )
    assert rawless_event
    store.record_observation(
        capture=rawless_capture,
        event=rawless_event,
        source_id="SYNOPTIC_PUSH_HF_TEMP",
        vantage_id="TEST",
        notification_ns=rawless_capture.wall_ns,
        fetch_started_ns=None,
        fetch_finished_ns=None,
        decoded_ns=rawless_capture.wall_ns,
    )
    store.close()

    assert command_replay(argparse.Namespace(runtime_root=str(runtime_root))) == 0

    audit_store = EvidenceStore(runtime_root)
    audit = audit_store.query(
        "SELECT run_id, details_json FROM replay_audit ORDER BY replayed_at_ns DESC LIMIT 1"
    )[0]
    details = json.loads(audit["details_json"])
    assert audit["run_id"] == latest_run_id
    assert details["observations"] == 2
    assert details["raw_records"] == 1
    assert details["replayed_records"] == 2
    assert details["stable_identity"] is True
    audit_store.close()
