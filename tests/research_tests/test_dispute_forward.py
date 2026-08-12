from __future__ import annotations

import pytest
import requests
import json

from src.strategies.rule_lawyer import dispute_forward as forward_module

from src.strategies.rule_lawyer.dispute_forward import (
    active_shadow_case_ids,
    bulletin_updates_fingerprint,
    decode_ancillary,
    extract_market_id,
    extract_title,
    extract_request_question_id,
    normalized_levels,
    request_class,
    request_rounds_fingerprint,
    request_state_poll_interval_seconds,
    load_state,
    bulletin_poll_interval_seconds,
    capture_demands_for_snapshot,
    capture_checkpoint_work,
    get_json,
    stamp_contract_first_seen,
    write_capture_checkpoint_receipts,
)


ONE = 10**18


def test_ancillary_market_and_title_extraction() -> None:
    text = "q: title: Example market?, description: Rules here, market_id: 12345 res_data: p1: 0"
    encoded = "0x" + text.encode().hex()
    assert decode_ancillary(encoded) == text
    assert extract_market_id(text) == "12345"
    assert extract_title(text) == "Example market?"
    assert extract_request_question_id("YES_OR_NO_QUERY-123-0x" + "ab" * 32) == "0x" + "ab" * 32


def test_request_classification() -> None:
    assert request_class(None, 0) == "unsettled"
    assert request_class(0, 0) == "upheld"
    assert request_class(ONE, 0) == "binary_flip"


def test_normalized_levels() -> None:
    book = {
        "bids": [{"price": "0.2", "size": "10"}],
        "asks": [{"price": "0.3", "size": "5"}],
    }
    assert normalized_levels(book) == [
        {"side": "bid", "price": 0.2, "size": 10.0},
        {"side": "ask", "price": 0.3, "size": 5.0},
    ]


def test_load_state_recovers_append_only_events(tmp_path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"case_id":"case-1","raw_request":{"id":"1"}}\n')
    state = load_state(tmp_path / "missing-state.json", events)
    assert state["seen_case_ids"] == ["case-1"]
    assert state["tracked"]["case-1"]["id"] == "1"


def test_load_state_merges_raw_event_after_crash_before_state_write(tmp_path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"case_id":"case-1","raw_request":{"id":"1"}}\n')
    state_path = tmp_path / "state.json"
    state_path.write_text('{"seen_case_ids":[],"tracked":{}}')
    state = load_state(state_path, events)
    assert state["seen_case_ids"] == ["case-1"]
    assert state["tracked"]["case-1"]["id"] == "1"


def test_load_state_does_not_resurrect_retired_case_from_raw_event(tmp_path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"case_id":"case-1","raw_request":{"id":"1"}}\n')
    state_path = tmp_path / "state.json"
    state_path.write_text(
        '{"seen_case_ids":["case-1"],"tracked":{},"retired_case_ids":["case-1"]}'
    )
    state = load_state(state_path, events)
    assert state["tracked"] == {}


def test_load_state_prunes_terminal_cases_but_keeps_pending_and_open(tmp_path) -> None:
    cases = {
        "terminal": {"id": "1"},
        "open": {"id": "2"},
        "pending": {"id": "3"},
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "tracked": cases,
                "pending_trigger_reason_by_case": {"pending": "official_update"},
                "bulletin_watches": {case_id: {} for case_id in cases},
            }
        )
    )
    snapshots = tmp_path / "snapshots.jsonl"
    snapshots.write_text(
        "".join(
            json.dumps(
                {
                    "case_id": case_id,
                    "captured_at_utc": "2026-08-12T00:00:00Z",
                    "proposal": {"request_class": "upheld"},
                    "market_status": {"closed": case_id != "open"},
                }
            )
            + "\n"
            for case_id in cases
        )
    )
    state = load_state(state_path, snapshots_path=snapshots)
    assert set(state["tracked"]) == {"open", "pending"}
    assert set(state["bulletin_watches"]) == {"open", "pending"}
    assert state["terminal_cases_pruned_from_tracking"] == 1


def test_bulletin_poll_interval_is_adaptive() -> None:
    now = 1_000_000
    assert bulletin_poll_interval_seconds(now - 60, now) == 60
    assert bulletin_poll_interval_seconds(now - 7 * 3600, now) == 300
    assert bulletin_poll_interval_seconds(now - 49 * 3600, now) == 1800
    assert request_state_poll_interval_seconds(now - 60, now) == 300
    assert request_state_poll_interval_seconds(now - 7 * 3600, now) == 900
    assert request_state_poll_interval_seconds(now - 49 * 3600, now) == 1800


def test_request_round_fingerprint_ignores_delivery_metadata() -> None:
    base = [{"id": "r1", "proposedPrice": "1", "settlementPrice": None}]
    delivered = [{**base[0], "subgraph": "copy", "local_seen": "later"}]
    assert request_rounds_fingerprint(base) == request_rounds_fingerprint(delivered)


def test_dispute_trigger_declares_hot_capture_without_starting_writer() -> None:
    snapshot = {
        "case_id": "case-1",
        "market_id": "100",
        "condition_id": "0xabc",
        "tokens": ["yes-token", "no-token"],
        "contract_corpus": {"contract_corpus_sha256": "corpus-1"},
        "market_group_snapshot": {"group_snapshot_id": "group-snapshot-1"},
    }
    rows = capture_demands_for_snapshot(
        snapshot,
        reason="dispute_first_seen",
        requested_at_utc="2026-08-12T00:00:00Z",
    )
    assert len(rows) == 2
    assert {row["token_id"] for row in rows} == {"yes-token", "no-token"}
    assert all(row["priority"] == "P0" for row in rows)
    assert all(row["desired_transport"] == "REST_WS" for row in rows)
    assert all(row["expires_at_utc"] == "2026-08-12T00:10:00Z" for row in rows)


def test_rest_checkpoint_work_is_rebuilt_from_append_only_demand_receipts(tmp_path) -> None:
    snapshot = {
        "case_id": "case-1",
        "market_id": "100",
        "condition_id": "0xabc",
        "tokens": ["yes-token"],
        "contract_corpus": {"contract_corpus_sha256": "corpus-1"},
    }
    demand = capture_demands_for_snapshot(
        snapshot,
        reason="dispute_first_seen",
        requested_at_utc="2026-08-12T00:00:00Z",
    )[0]
    (tmp_path / "capture_demands.jsonl").write_text(json.dumps(demand) + "\n")
    due, outstanding = capture_checkpoint_work(tmp_path, now_ts=1_786_493_100)
    assert outstanding == {"case-1"}
    assert [row["checkpoint_seconds"] for row in due["case-1"]] == [0, 30, 120, 300]
    captured = {
        "case_id": "case-1",
        "captured_at_utc": "2026-08-12T00:05:00Z",
        "book_captures": {"yes-token": {"book_capture_id": "book-1"}},
    }
    assert write_capture_checkpoint_receipts(
        tmp_path, captured, due["case-1"], captured_at_ts=1_786_493_100
    ) == 4
    assert write_capture_checkpoint_receipts(
        tmp_path, captured, due["case-1"], captured_at_ts=1_786_493_100
    ) == 0
    next_due, outstanding = capture_checkpoint_work(tmp_path, now_ts=1_786_493_101)
    assert next_due == {}
    assert outstanding == {"case-1"}


def test_final_rest_checkpoint_retires_terminal_case_same_cycle(
    tmp_path, monkeypatch
) -> None:
    raw = {
        "subgraph": "test-subgraph",
        "id": "request-1",
        "ancillaryData": "0x" + b"q: title: T, market_id: 100".hex(),
        "disputeTimestamp": "100",
    }
    case_id = "test-subgraph:request-1"
    demand = capture_demands_for_snapshot(
        {
            "case_id": case_id,
            "market_id": "100",
            "condition_id": "condition-1",
            "tokens": ["yes-token"],
            "contract_corpus": {"contract_corpus_sha256": "corpus-1"},
        },
        reason="dispute_first_seen",
        requested_at_utc="1970-01-01T00:00:00Z",
    )[0]
    (tmp_path / "capture_demands.jsonl").write_text(json.dumps(demand) + "\n")
    prior_due, _ = capture_checkpoint_work(tmp_path, now_ts=900)
    prior_snapshot = {
        "case_id": case_id,
        "captured_at_utc": "1970-01-01T00:15:00+00:00",
        "book_captures": {"yes-token": {"book_capture_id": "book-prior"}},
    }
    assert write_capture_checkpoint_receipts(
        tmp_path, prior_snapshot, prior_due[case_id], captured_at_ts=900
    ) == 5
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "seen_case_ids": [case_id],
                "tracked": {case_id: raw},
                "request_state_fingerprint_by_case": {case_id: "stable"},
                "last_request_state_poll_ts_by_case": {case_id: 3_600},
                "lifecycle_state_hydrated_from_snapshots": True,
            }
        )
    )
    monkeypatch.setattr(forward_module, "SUBGRAPHS", {"test-subgraph": "unused"})
    monkeypatch.setattr(
        forward_module, "fetch_disputed_requests_since", lambda *args, **kwargs: []
    )
    monkeypatch.setattr(
        forward_module,
        "capture_case",
        lambda *args, **kwargs: {
            "case_id": case_id,
            "captured_at_utc": "1970-01-01T01:00:00+00:00",
            "market_id": "100",
            "condition_id": "condition-1",
            "tokens": ["yes-token"],
            "contract_corpus": {"fragments": []},
            "bulletin_state": {},
            "request_rounds_fingerprint": "stable",
            "proposal": {"request_class": "upheld"},
            "market_status": {"closed": True},
            "raw_request": raw,
            "book_captures": {"yes-token": {"book_capture_id": "book-final"}},
            "books": {},
            "reverse_token": "",
            "reverse_executable_vwap": {"25": None},
        },
    )
    summary = forward_module.run_capture_once(
        tmp_path, now_ts=3_600, fetch_source_evidence=False, workers=1
    )
    state = json.loads((tmp_path / "state.json").read_text())
    assert summary["rest_checkpoint_receipts_written"] == 1
    assert summary["rest_checkpoint_cases_outstanding"] == 0
    assert state["tracked"] == {}
    assert state["retired_case_ids"] == [case_id]


def test_active_shadow_case_ids_excludes_settled_positions(tmp_path) -> None:
    (tmp_path / "shadow_positions.jsonl").write_text(
        '{"position_id":"p1","case_id":"c1"}\n'
        '{"position_id":"p2","case_id":"c2"}\n'
    )
    (tmp_path / "shadow_markouts.jsonl").write_text(
        '{"position_id":"p1","checkpoint":"settlement"}\n'
    )
    assert active_shadow_case_ids(tmp_path) == {"c2"}


def test_contract_fragment_first_seen_survives_recapture() -> None:
    seen = {}
    first = {
        "case_id": "c1",
        "captured_at_utc": "2026-01-01T00:00:00Z",
        "contract_corpus": {"fragments": [{"origin": "gamma", "content_sha256": "abc"}]},
    }
    stamp_contract_first_seen(first, seen)
    second = {
        "case_id": "c1",
        "captured_at_utc": "2026-01-01T01:00:00Z",
        "contract_corpus": {"fragments": [{"origin": "gamma", "content_sha256": "abc"}]},
    }
    stamp_contract_first_seen(second, seen)
    assert second["contract_corpus"]["fragments"][0]["first_observed_at_utc"] == "2026-01-01T00:00:00Z"


def test_get_json_retries_transient_503(monkeypatch) -> None:
    failed = requests.Response()
    failed.status_code = 503
    failed.url = "https://example.test/data"
    good = requests.Response()
    good.status_code = 200
    good._content = b'{"ok": true}'
    replies = iter([failed, good])
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: next(replies))
    assert get_json("https://example.test/data", retry_base_seconds=0) == {"ok": True}


def test_bulletin_fingerprint_changes_with_creator_update() -> None:
    empty = bulletin_updates_fingerprint([])
    one = bulletin_updates_fingerprint(
        [{"timestamp": 10, "update_hex": "0x1234", "text": "Clarification"}]
    )
    assert empty != one
    assert one == bulletin_updates_fingerprint(
        [{"timestamp": 10, "update_hex": "0x1234", "text": "Clarification"}]
    )


def test_failed_first_capture_persists_trigger_until_demand_is_written(
    tmp_path, monkeypatch
) -> None:
    raw = {
        "subgraph": "test-subgraph",
        "id": "request-1",
        "ancillaryData": "0x" + b"q: title: T, market_id: 100".hex(),
        "disputeTimestamp": "100",
    }
    monkeypatch.setattr(forward_module, "SUBGRAPHS", {"test-subgraph": "unused"})
    monkeypatch.setattr(
        forward_module,
        "fetch_disputed_requests_since",
        lambda *args, **kwargs: [dict(raw)],
    )
    monkeypatch.setattr(
        forward_module,
        "capture_case",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary")),
    )
    first = forward_module.run_capture_once(
        tmp_path, now_ts=1_000, fetch_source_evidence=False, workers=1
    )
    case_id = "test-subgraph:request-1"
    assert first["capture_demands_written"] == 0
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["pending_trigger_reason_by_case"][case_id] == "dispute_first_seen"

    def successful_capture(*args, **kwargs):
        return {
            "case_id": case_id,
            "captured_at_utc": "1970-01-01T00:16:41+00:00",
            "market_id": "100",
            "condition_id": "condition-1",
            "tokens": ["yes-token", "no-token"],
            "contract_corpus": {
                "contract_corpus_sha256": "corpus-1",
                "fragments": [],
            },
            "bulletin_state": {},
            "raw_request": raw,
            "proposal": {"request_class": "unsettled"},
            "market_status": {"closed": False},
            "books": {},
            "reverse_token": "",
            "reverse_executable_vwap": {"25": None},
        }

    monkeypatch.setattr(forward_module, "capture_case", successful_capture)
    second = forward_module.run_capture_once(
        tmp_path, now_ts=1_001, fetch_source_evidence=False, workers=1
    )
    assert second["capture_demands_written"] == 2
    state = json.loads((tmp_path / "state.json").read_text())
    assert case_id not in state["pending_trigger_reason_by_case"]
    demands = [json.loads(line) for line in (tmp_path / "capture_demands.jsonl").read_text().splitlines()]
    assert {row["token_id"] for row in demands} == {"yes-token", "no-token"}

    # Simulate a crash after raw demand append but before mutable pending state
    # was cleared.  Retry must not duplicate either raw demand row.
    state["pending_trigger_reason_by_case"] = {case_id: "dispute_first_seen"}
    state["tracked"] = {case_id: raw}
    (tmp_path / "state.json").write_text(json.dumps(state))
    third = forward_module.run_capture_once(
        tmp_path, now_ts=1_002, fetch_source_evidence=False, workers=1
    )
    assert third["capture_demands_written"] == 0
    assert len((tmp_path / "capture_demands.jsonl").read_text().splitlines()) == 2


def test_old_case_does_not_periodically_refetch_full_books(tmp_path, monkeypatch) -> None:
    raw = {
        "subgraph": "test-subgraph",
        "id": "request-1",
        "ancillaryData": "0x" + b"q: title: T, market_id: 100".hex(),
        "disputeTimestamp": "100",
    }
    case_id = "test-subgraph:request-1"
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "seen_case_ids": [case_id],
                "tracked": {case_id: raw},
                "last_capture_ts_by_case": {case_id: 100},
                "request_state_fingerprint_by_case": {case_id: "stable"},
                "last_request_state_poll_ts_by_case": {case_id: 1_000},
            }
        )
    )
    monkeypatch.setattr(forward_module, "SUBGRAPHS", {"test-subgraph": "unused"})
    monkeypatch.setattr(
        forward_module,
        "fetch_disputed_requests_since",
        lambda *args, **kwargs: [dict(raw)],
    )
    monkeypatch.setattr(
        forward_module,
        "capture_case",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("full capture should not run")
        ),
    )
    summary = forward_module.run_capture_once(
        tmp_path, now_ts=1_001, fetch_source_evidence=False, workers=1
    )
    assert summary["cases_due"] == 0
    assert summary["snapshots_written"] == 0


def test_legacy_case_without_request_fingerprint_gets_one_reconciliation_capture(
    tmp_path, monkeypatch
) -> None:
    raw = {
        "subgraph": "test-subgraph",
        "id": "request-1",
        "ancillaryData": "0x" + b"q: title: T, market_id: 100".hex(),
        "disputeTimestamp": "100",
    }
    case_id = "test-subgraph:request-1"
    (tmp_path / "state.json").write_text(
        json.dumps({"seen_case_ids": [case_id], "tracked": {case_id: raw}})
    )
    monkeypatch.setattr(forward_module, "SUBGRAPHS", {"test-subgraph": "unused"})
    monkeypatch.setattr(forward_module, "fetch_disputed_requests_since", lambda *a, **k: [])
    rounds = [{**raw, "settlementPrice": "0", "proposedPrice": "0"}]
    monkeypatch.setattr(forward_module, "fetch_request_rounds", lambda *a, **k: rounds)
    received_rounds = []

    def capture(*args, **kwargs):
        received_rounds.append(kwargs.get("request_rounds"))
        return {
            "case_id": case_id,
            "captured_at_utc": "1970-01-01T00:16:41+00:00",
            "market_id": "100",
            "condition_id": "condition-1",
            "tokens": [],
            "contract_corpus": {"fragments": []},
            "bulletin_state": {},
            "request_rounds_fingerprint": request_rounds_fingerprint(rounds),
            "proposal": {"request_class": "upheld"},
            "market_status": {"closed": True},
            "raw_request": raw,
            "books": {},
            "reverse_token": "",
            "reverse_executable_vwap": {"25": None},
        }

    monkeypatch.setattr(forward_module, "capture_case", capture)
    summary = forward_module.run_capture_once(
        tmp_path, now_ts=1_001, fetch_source_evidence=False, workers=1
    )
    assert summary["request_state_change_triggers"] == 0
    assert summary["bootstrap_reconciliations_due"] == 1
    assert summary["snapshots_written"] == 1
    assert received_rounds == [rounds]
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["tracked"] == {}

    # The overlap query may return the same terminal row again. Its lifecycle
    # tombstone must suppress resurrection and another full capture.
    monkeypatch.setattr(
        forward_module,
        "fetch_disputed_requests_since",
        lambda *args, **kwargs: [dict(raw)],
    )
    monkeypatch.setattr(
        forward_module,
        "capture_case",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("retired lifecycle should not be captured again")
        ),
    )
    repeated = forward_module.run_capture_once(
        tmp_path, now_ts=1_002, fetch_source_evidence=False, workers=1
    )
    assert repeated["tracked_cases_before_capture"] == 0
    assert repeated["cases_due"] == 0

    # A changed dispute identity represents a genuinely new lifecycle and must
    # reopen the case.
    reopened_raw = {**raw, "disputeHash": "new-dispute"}
    monkeypatch.setattr(
        forward_module,
        "fetch_disputed_requests_since",
        lambda *args, **kwargs: [reopened_raw],
    )
    monkeypatch.setattr(
        forward_module,
        "capture_case",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("reopened")),
    )
    reopened = forward_module.run_capture_once(
        tmp_path, now_ts=1_003, fetch_source_evidence=False, workers=1
    )
    assert reopened["tracked_cases_before_capture"] == 1
    assert reopened["cases_due"] == 1


def test_legacy_reconciliation_full_capture_is_bounded_per_run(tmp_path, monkeypatch) -> None:
    tracked = {
        f"test-subgraph:request-{index}": {
            "subgraph": "test-subgraph",
            "id": f"request-{index}",
            "ancillaryData": "0x" + f"q: title: T, market_id: {index}".encode().hex(),
            "disputeTimestamp": "100",
        }
        for index in range(3)
    }
    (tmp_path / "state.json").write_text(
        json.dumps({"seen_case_ids": sorted(tracked), "tracked": tracked})
    )
    monkeypatch.setattr(forward_module, "SUBGRAPHS", {"test-subgraph": "unused"})
    monkeypatch.setattr(forward_module, "fetch_disputed_requests_since", lambda *a, **k: [])
    monkeypatch.setattr(
        forward_module,
        "fetch_request_rounds",
        lambda subgraph, ancillary: [{"id": ancillary, "settlementPrice": None}],
    )
    captured = []

    def capture(raw, **kwargs):
        captured.append(raw["id"])
        return {
            "case_id": f"test-subgraph:{raw['id']}",
            "captured_at_utc": "1970-01-01T00:16:41+00:00",
            "tokens": [],
            "contract_corpus": {"fragments": []},
            "bulletin_state": {},
            "request_rounds_fingerprint": request_rounds_fingerprint(
                kwargs["request_rounds"]
            ),
            "proposal": {"request_class": "unsettled"},
            "market_status": {"closed": False},
            "books": {},
            "reverse_token": "",
            "reverse_executable_vwap": {"25": None},
        }

    monkeypatch.setattr(forward_module, "capture_case", capture)
    summary = forward_module.run_capture_once(
        tmp_path,
        now_ts=1_001,
        fetch_source_evidence=False,
        workers=1,
        max_bootstrap_reconciliations_per_run=1,
    )
    assert len(captured) == 1
    assert summary["bootstrap_reconciliations_due"] == 1
    assert summary["bootstrap_reconciliations_pending"] == 2
