from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from us_fast_weather_lab.lab_to_source_events import materialize_lab_events
from us_fast_weather_lab.model import metar_event
from us_fast_weather_lab.storage import ClockState, EvidenceStore


def _store(tmp_path, *, valid: bool = True) -> EvidenceStore:
    store = EvidenceStore(tmp_path / "lab")
    store.start_run(config_hash="test", vantage_id="TEST")
    store.set_clock_state(ClockState(0.5 if valid else 250.0, valid))
    return store


def _record(store: EvidenceStore, raw: str, *, source_id: str, wall_ns: int, valid: bool = True) -> None:
    store.set_clock_state(ClockState(0.5 if valid else 250.0, valid))
    capture = store.capture_transport(
        source_id=source_id,
        endpoint="test",
        topic="test",
        payload=raw.encode(),
        wall_ns=wall_ns,
        monotonic_ns=time.monotonic_ns(),
    )
    event = metar_event(raw, reference_ns=wall_ns)
    assert event is not None
    store.record_observation(
        capture=capture,
        event=event,
        source_id=source_id,
        vantage_id="TEST",
        notification_ns=None,
        fetch_started_ns=None,
        fetch_finished_ns=None,
        decoded_ns=wall_ns + 1,
    )


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_materializer_keeps_meteorological_classes_and_is_idempotent(tmp_path) -> None:
    store = _store(tmp_path)
    wall_ns = int(datetime(2026, 8, 29, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    raw = "METAR KATL 290052Z 00000KT 10SM CLR 25/20 A3000"
    _record(store, raw, source_id="METAR_WS_METAR", wall_ns=wall_ns)
    _record(store, raw, source_id="METAR_WS_HFMETAR", wall_ns=wall_ns + 1_000)
    _record(store, raw, source_id="METAR_WS_DATIS", wall_ns=wall_ns + 2_000)
    output = tmp_path / "out"
    first = materialize_lab_events(store.db_path, output)
    assert first["appended_information_events"] == 3
    assert materialize_lab_events(store.db_path, output)["appended_information_events"] == 0
    rows = _rows(output / "2026-08-28" / "sources.jsonl")
    assert {row["source"] for row in rows} == {"metar_ws_metar", "metar_ws_hfmetar", "metar_ws_datis"}
    assert {row["report_class"] for row in rows} == {"official_metar", "high_frequency_metar", "datis_derived"}
    assert all(row["pit_eligible"] for row in rows)
    store.close()


def test_materializer_preserves_correction_and_clock_invalid_as_non_actionable(tmp_path) -> None:
    store = _store(tmp_path)
    wall_ns = int(datetime(2026, 8, 29, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    first_raw = "METAR KATL 290052Z 00000KT 10SM CLR 25/20 A3000"
    cor_raw = "METAR KATL 290052Z COR 00000KT 10SM CLR 26/20 A3000"
    _record(store, first_raw, source_id="METAR_WS_METAR", wall_ns=wall_ns)
    _record(store, cor_raw, source_id="METAR_WS_METAR", wall_ns=wall_ns + 1_000, valid=False)
    output = tmp_path / "out"
    result = materialize_lab_events(store.db_path, output)
    assert result["appended_information_events"] == 2
    rows = _rows(output / "2026-08-28" / "sources.jsonl")
    base = next(row for row in rows if not row["is_correction"])
    correction = next(row for row in rows if row["is_correction"])
    assert correction["revision_of_event_id"] == base["information_event_id"]
    assert correction["status"] == "clock_invalid_non_actionable"
    assert correction["pit_eligible"] is False
    assert correction["first_seen_at_utc"] is None
    store.close()


def test_materializer_links_correction_that_arrives_before_base(tmp_path) -> None:
    store = _store(tmp_path)
    wall_ns = int(datetime(2026, 8, 29, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    first_raw = "METAR KATL 290052Z 00000KT 10SM CLR 25/20 A3000"
    cor_raw = "METAR KATL 290052Z COR 00000KT 10SM CLR 26/20 A3000"
    _record(store, cor_raw, source_id="METAR_WS_METAR", wall_ns=wall_ns)
    _record(store, first_raw, source_id="METAR_WS_METAR", wall_ns=wall_ns + 1_000)
    output = tmp_path / "out"
    materialize_lab_events(store.db_path, output)
    rows = _rows(output / "2026-08-28" / "sources.jsonl")
    base = next(row for row in rows if not row["is_correction"])
    correction = next(row for row in rows if row["is_correction"])
    assert correction["event_role"] == "revision"
    assert correction["revision_of_event_id"] == base["information_event_id"]
    store.close()


def test_materializer_recovers_missing_source_ledger_after_partial_write(tmp_path) -> None:
    store = _store(tmp_path)
    wall_ns = int(datetime(2026, 8, 29, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    _record(
        store,
        "METAR KATL 290052Z 00000KT 10SM CLR 25/20 A3000",
        source_id="METAR_WS_METAR",
        wall_ns=wall_ns,
    )
    output = tmp_path / "out"
    first = materialize_lab_events(store.db_path, output)
    assert first["appended_information_events"] == 1
    source_path = output / "2026-08-28" / "sources.jsonl"
    source_path.unlink()
    recovered = materialize_lab_events(store.db_path, output)
    assert recovered["appended_information_events"] == 0
    assert recovered["appended_source_events"] == 1
    assert len(_rows(source_path)) == 1
    store.close()


def test_cached_replay_is_not_materialized(tmp_path) -> None:
    store = _store(tmp_path)
    wall_ns = int(datetime(2026, 8, 29, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    raw = "METAR KATL 290052Z 00000KT 10SM CLR 25/20 A3000"
    store.set_clock_state(ClockState(0.5, True))
    capture = store.capture_transport(
        source_id="METAR_WS_METAR", endpoint="test", topic="test", payload=raw.encode(), wall_ns=wall_ns, monotonic_ns=time.monotonic_ns()
    )
    event = metar_event(raw, reference_ns=wall_ns)
    assert event
    store.record_observation(
        capture=capture, event=event, source_id="METAR_WS_METAR", vantage_id="TEST", notification_ns=None,
        fetch_started_ns=None, fetch_finished_ns=None, decoded_ns=wall_ns + 1, evidence_status="cached_replay_non_latency"
    )
    assert materialize_lab_events(store.db_path, tmp_path / "out")["appended_information_events"] == 0
    store.close()
