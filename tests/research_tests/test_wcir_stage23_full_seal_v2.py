from __future__ import annotations

import pytest

from scripts.analysis.forecast_quality.wcir_collector_clock_shadow import CollectorClockShadow
from scripts.analysis.forecast_quality.wcir_stage23_full_seal_v2 import (
    causal_entry_gate,
    build_denominator_rows,
    entry_checkpoint_gate,
    latency_provenance_rows,
)


def _event(source: str | None = "2026-01-01T00:00:00Z", official: str | None = "2026-01-01T00:00:10Z") -> dict:
    return {
        "event_id": "e",
        "city": "X",
        "source": "s",
        "target_date": "2026-01-01",
        "information_event_id": "p",
        "source_detect_ts_utc": source,
        "official_first_seen_at_utc": official,
    }


def _oracle(latency: float = 5.0) -> dict:
    return {
        "event_id": "e",
        "execution_latency_seconds": latency,
        "effective_lead_seconds": 10.0 - latency,
        "action": {"action": "NO_TRADE", "reason": "next_print_does_not_raise_running_max"},
        "paired_executable": False,
        "net_pnl_usd": None,
    }


def test_causal_gate_is_strict_and_equality_fails_closed() -> None:
    assert causal_entry_gate(_event(), _oracle(5))["causal_entry_eligible"]
    equal = causal_entry_gate(_event(), _oracle(10))
    assert not equal["causal_entry_eligible"]
    assert equal["causal_exclusion_reason"] == "ENTRY_EQUALS_OFFICIAL_FIRST_SEEN_FAIL_CLOSED"


def test_post_ready_or_post_official_entry_checkpoint_fails_closed() -> None:
    gate = causal_entry_gate(_event(), _oracle(5))
    assert entry_checkpoint_gate({"checkpoint_at_utc": "2026-01-01T00:00:05Z"}, gate)["entry_checkpoint_causal_guard_passed"]
    late = entry_checkpoint_gate({"checkpoint_at_utc": "2026-01-01T00:00:05.001Z"}, gate)
    assert not late["entry_checkpoint_causal_guard_passed"]
    assert late["entry_checkpoint_causal_exclusion_reason"] == "ENTRY_BOOK_CHECKPOINT_AFTER_DECISION_READY_CUTOFF"
    post_official_gate = dict(gate, effective_entry_decision_ready_ts_utc="2026-01-01T00:00:20Z")
    post = entry_checkpoint_gate({"checkpoint_at_utc": "2026-01-01T00:00:10Z"}, post_official_gate)
    assert not post["entry_checkpoint_causal_guard_passed"]


@pytest.mark.parametrize("source,official,reason", [
    (None, "2026-01-01T00:00:10Z", "MISSING_OR_UNCOMPARABLE_SOURCE_CLOCK"),
    ("2026-01-01T00:00:00Z", None, "MISSING_OR_UNCOMPARABLE_OFFICIAL_FIRST_SEEN_CLOCK"),
    ("bad", "2026-01-01T00:00:10Z", "MISSING_OR_UNCOMPARABLE_SOURCE_CLOCK"),
])
def test_causal_gate_missing_or_uncomparable_clock_fails_closed(source, official, reason) -> None:
    oracle = _oracle(5)
    oracle["effective_lead_seconds"] = None
    result = causal_entry_gate(_event(source, official), oracle)
    assert not result["causal_entry_eligible"]
    assert result["causal_exclusion_reason"] == reason


def test_latency_provenance_marks_legacy_pool_not_forward_primary() -> None:
    event = _event()
    event["event_key"] = "X|2026-01-01|s|clock"
    oracle = _oracle(49.772999999999996)
    oracle["effective_lead_seconds"] = -39.772999999999996
    oracle["latency_contract_id"] = "legacy"
    rows = [dict(event, event_id=str(i)) for i in range(841)]
    oracles = [dict(oracle, event_id=str(i)) for i in range(841)]
    output, summary = latency_provenance_rows(rows, oracles, [event])
    assert len(output) == 841
    assert summary["legacy_status"] == "LEGACY_DIAGNOSTIC_ONLY"
    assert summary["forward_primary_status"] == "NOT_FORWARD_PRIMARY"
    assert all(row["collector_epoch_missing"] for row in output)


def test_denominator_separates_policy_abstain_data_failure_and_research_null() -> None:
    event = _event()
    oracle = _oracle()
    # The production builder enforces 841 rows; use the classification logic via
    # a replicated 841-event fixture to exercise the hard headline check.
    events = [dict(event, event_id=str(i), information_event_id=f"p{i}") for i in range(841)]
    baselines = [{"event_id": str(i), "same_row_intersection_eligible": i == 0} for i in range(841)]
    oracles = []
    for i in range(841):
        row = dict(oracle, event_id=str(i))
        if i < 89:
            row["action"] = {"action": "BUY_OUTCOME_TOKEN"}
            row["paired_executable"] = True
            row["net_pnl_usd"] = 0.1
        oracles.append(row)
    output, summary = build_denominator_rows(baselines, oracles, events)
    assert summary["layers"]["RESEARCH_MARKET_DATA_ELIGIBLE"] == 89
    assert output[100]["operational_disposition"] == "POLICY_ABSTAIN"
    assert output[100]["conditional_alpha_pnl_usd"] is None


def test_heartbeat_and_scheduler_stall_invalidate_every_token() -> None:
    machine = CollectorClockShadow(1, 10, "e", "c")
    machine.request("t", wall_ns=10, monotonic_ns=10)
    machine.acknowledge("t", wall_ns=11, monotonic_ns=11)
    machine.baseline("t", wall_ns=12, monotonic_ns=12)
    machine.heartbeat_timeout()
    assert not machine.tokens["t"].valid


def test_process_restart_resumes_journal_cursor_without_reusing_validity(tmp_path) -> None:
    journal = tmp_path / "journal.jsonl"
    first = CollectorClockShadow(1, 10, "e1", "c1", journal_path=journal, process_instance_id="p1")
    first.request("t", wall_ns=10, monotonic_ns=10)
    second = CollectorClockShadow(1, 10, "e2", "c2", journal_path=journal, process_instance_id="p2")
    assert second.journal_sequence == 1
    assert "t" not in second.tokens
    second.request("t", wall_ns=20, monotonic_ns=20)
    rows = [__import__("json").loads(line) for line in journal.read_text().splitlines()]
    assert [row["sequence"] for row in rows] == [1, 2]
    assert [row["process_instance_id"] for row in rows] == ["p1", "p2"]


def test_scheduler_stall_invalidates_every_token() -> None:
    machine = CollectorClockShadow(1, 10, "e", "c")
    machine.request("t", wall_ns=10, monotonic_ns=10)
    machine.acknowledge("t", wall_ns=11, monotonic_ns=11)
    machine.baseline("t", wall_ns=12, monotonic_ns=12)
    machine.scheduler_tick(monotonic_ns=20, maximum_gap_ns=10)
    with pytest.raises(RuntimeError, match="scheduler stall"):
        machine.scheduler_tick(monotonic_ns=40, maximum_gap_ns=10)
    assert not machine.tokens["t"].valid
