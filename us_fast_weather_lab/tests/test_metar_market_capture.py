from __future__ import annotations

import json
from datetime import datetime, timezone

from us_fast_weather_lab.lab_to_source_events import materialize_lab_events
from us_fast_weather_lab.metar_market_capture import CHECKPOINTS, materialize_capture_demands
from us_fast_weather_lab.model import metar_event
from us_fast_weather_lab.storage import ClockState, EvidenceStore


def _event(*, city: str = "Miami", source: str = "metar_ws_metar", event_id: str = "event-1", valid: bool = True, temp_c: float | None = 30.0, report_kind: str = "METAR"):
    return {
        "information_event_id": event_id, "city": city, "target_date": "2026-08-30", "source": source,
        "event_role": "new_content", "clock_valid": valid, "pit_eligible": valid, "temp_c": temp_c,
        "transport_received_at_utc": "2026-08-30T10:01:00Z", "transport_received_monotonic_ns": 123,
        "source_report_ts_utc": "2026-08-30T10:00:00Z", "report_kind": report_kind,
    }


def _write_events(tmp_path, *events):
    root = tmp_path / "events" / "2026-08-30"; root.mkdir(parents=True)
    (root / "sources.jsonl").write_text("".join(json.dumps(row) + "\n" for row in events))
    return root.parent


def _market(tmp_path, city="Miami"):
    rows = []
    for bracket in (84, 85, 86, 87, 88):
        for outcome in ("yes", "no"):
            rows.append({"city": city, "event_date": "2026-08-30", "bracket": str(bracket), "outcome": outcome,
                         "condition_id": f"condition-{bracket}", "token_id": f"{bracket}-{outcome}"})
    path = tmp_path / "latest.json"; path.write_text(json.dumps({"records": rows})); return path


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_valid_event_emits_bounded_hot_strip_and_prewindow(tmp_path):
    source = _write_events(tmp_path, _event())
    output = tmp_path / "demands.jsonl"
    result = materialize_capture_demands(source, _market(tmp_path), output)
    rows = _rows(output)
    immediate = [row for row in rows if row["reason"] == "metar_ws_first_seen_hot_strip"]
    scheduled = [row for row in rows if row["reason"] == "metar_ws_next_report_prewindow_hot_strip"]
    assert len(immediate) == 6 and len(scheduled) == 6
    assert {row["token_id"] for row in immediate} == {f"{bracket}-{side}" for bracket in (85, 86, 87) for side in ("yes", "no")}
    assert all(tuple(row["requested_checkpoints_seconds"]) == CHECKPOINTS for row in rows)
    assert all(row["desired_transport"] == "REST_WS" and row["priority"] == "P0" for row in rows)
    assert {row["requested_at_utc"] for row in immediate} == {"2026-08-30T10:01:00.000000Z"}
    assert {row["expires_at_utc"] for row in immediate} == {"2026-08-30T10:06:30.000000Z"}
    assert {row["requested_at_utc"] for row in scheduled} == {"2026-08-30T10:58:00.000000Z"}
    assert {row["expires_at_utc"] for row in scheduled} == {"2026-08-30T11:06:00.000000Z"}
    assert {row["metadata"]["schedule_basis"] for row in scheduled} == {
        "prior_routine_metar_report_plus_1h"
    }
    assert result["scheduled_prewindow_demands_written"] == 6


def test_clock_invalid_event_still_captures_but_is_excluded_from_formal_ranking(tmp_path):
    source = _write_events(tmp_path, _event(event_id="clock", valid=False))
    output = tmp_path / "demands.jsonl"
    result = materialize_capture_demands(source, _market(tmp_path), output)
    assert result["capture_demands_written"] == 12
    assert all(row["metadata"]["formal_latency_eligible"] is False for row in _rows(output))
    resolution = _rows(tmp_path / "demands_resolution.jsonl")
    event_resolution = next(row for row in resolution if row["resolution_id"] == "clock:event")
    assert event_resolution["status"] == "emitted_clock_invalid_exploratory"
    assert event_resolution["formal_latency_eligible"] is False


def test_unmapped_or_incomplete_rows_are_ledgered_without_demand(tmp_path):
    source = _write_events(tmp_path, _event(event_id="temp", temp_c=None), _event(event_id="badcity", city="Boston"))
    output = tmp_path / "demands.jsonl"
    result = materialize_capture_demands(source, _market(tmp_path), output)
    assert result["capture_demands_written"] == 0
    resolution = _rows(tmp_path / "demands_resolution.jsonl")
    assert {row["blocker"] for row in resolution} == {"temperature_missing", "city_not_in_fixed_cohort"}


def test_incomplete_yes_no_pair_fails_closed_for_entire_event(tmp_path):
    source = _write_events(tmp_path, _event())
    market = _market(tmp_path)
    payload = json.loads(market.read_text())
    payload["records"] = [
        row for row in payload["records"]
        if not (row["bracket"] == "86" and row["outcome"] == "no")
    ]
    market.write_text(json.dumps(payload))
    output = tmp_path / "demands.jsonl"

    result = materialize_capture_demands(source, market, output)

    assert result["capture_demands_written"] == 0
    resolution = _rows(tmp_path / "demands_resolution.jsonl")
    assert resolution[0]["blocker"] == "hot_strip_yes_no_pair_incomplete"


def test_speci_does_not_create_mechanical_next_hour_prewindow(tmp_path):
    source = _write_events(tmp_path, _event(report_kind="SPECI"))
    output = tmp_path / "demands.jsonl"

    result = materialize_capture_demands(source, _market(tmp_path), output)

    assert result["capture_demands_written"] == 6
    assert {row["reason"] for row in _rows(output)} == {"metar_ws_first_seen_hot_strip"}
    assert result["scheduled_prewindow_demands_written"] == 0


def test_restart_is_idempotent(tmp_path):
    source = _write_events(tmp_path, _event())
    output = tmp_path / "demands.jsonl"; market = _market(tmp_path)
    first = materialize_capture_demands(source, market, output)
    second = materialize_capture_demands(source, market, output)
    assert first["capture_demands_written"] == 12
    assert second == {"capture_demands_written": 0, "resolution_rows_written": 0, "scheduled_prewindow_demands_written": 0}


def test_chicago_uses_kord_official_profile_mapping(tmp_path):
    store = EvidenceStore(tmp_path / "lab"); store.start_run(config_hash="x", vantage_id="TEST"); store.set_clock_state(ClockState(0, True))
    wall = int(datetime(2026, 8, 30, 10, tzinfo=timezone.utc).timestamp() * 1e9)
    raw = "METAR KORD 301000Z 00000KT 10SM CLR 20/10 A3000"
    capture = store.capture_transport(source_id="METAR_WS_METAR", endpoint="test", topic="test", payload=raw.encode(), wall_ns=wall, monotonic_ns=999)
    event = metar_event(raw, reference_ns=wall); assert event
    store.record_observation(capture=capture, event=event, source_id="METAR_WS_METAR", vantage_id="TEST", notification_ns=None, fetch_started_ns=None, fetch_finished_ns=None, decoded_ns=wall + 1)
    output = tmp_path / "events"; materialize_lab_events(store.db_path, output); store.close()
    rows = _rows(output / "2026-08-30" / "sources.jsonl")
    chicago = next(row for row in rows if row["city"] == "Chicago")
    assert chicago["station"] == "KORD" and chicago["transport_received_monotonic_ns"] == 999


def test_output_has_no_execution_fields(tmp_path):
    source = _write_events(tmp_path, _event())
    output = tmp_path / "demands.jsonl"
    materialize_capture_demands(source, _market(tmp_path), output)
    forbidden = {"order", "intent", "fill", "notional", "side"}
    assert all(not (set(row) & forbidden) for row in _rows(output))
