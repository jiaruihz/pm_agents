from __future__ import annotations

import copy
import hashlib
import zipfile

import pytest

from scripts.analysis.forecast_quality.wcir_collector_clock_shadow import (
    CollectorClockShadow,
    BoundedFrameBuffer,
    ExpectedTokenDemand,
)
from scripts.analysis.forecast_quality.wcir_stage23_rev2_closure import (
    STAGE3,
    build_reconciliation,
    denominator_diagnostic,
    read_json,
    read_jsonl_gz,
)
from scripts.analysis.forecast_quality.package_wcir_stage23_rev2_closure import verify_archive


def _machine() -> CollectorClockShadow:
    return CollectorClockShadow(2, 2, "epoch-1", "connection-1")


def test_reconnect_invalidates_and_requires_new_baseline() -> None:
    m = _machine()
    m.request("t", wall_ns=1, monotonic_ns=1)
    m.acknowledge("t", wall_ns=2, monotonic_ns=2)
    m.baseline("t", wall_ns=3, monotonic_ns=3)
    assert m.tokens["t"].valid
    m.reconnect(new_connection_id="connection-2", new_epoch_id="epoch-2")
    assert not m.tokens["t"].valid
    with pytest.raises(RuntimeError, match="delta without valid baseline"):
        m.delta("t", wall_ns=4, monotonic_ns=4, exchange_event_ts_ms=1)
    m.request("t", wall_ns=5, monotonic_ns=5)
    m.acknowledge("t", wall_ns=6, monotonic_ns=6)
    m.baseline("t", wall_ns=7, monotonic_ns=7)
    assert m.tokens["t"].valid


def test_gap_cannot_clear_via_rest_and_clock_regressions_fail_closed() -> None:
    m = _machine()
    m.request("t", wall_ns=10, monotonic_ns=10)
    m.acknowledge("t", wall_ns=11, monotonic_ns=11)
    m.baseline("t", wall_ns=12, monotonic_ns=12)
    m.open_gap("t", "SEQUENCE_UNAVAILABLE_GAP")
    before = copy.deepcopy(m.snapshot())
    m.rest_parity_observation("t", {"bids": [[0.4, 5]]})
    assert m.snapshot() == before
    assert not m.tokens["t"].valid
    with pytest.raises(RuntimeError, match="wall clock regression"):
        m.request("x", wall_ns=1, monotonic_ns=13)


def test_missing_exchange_clock_and_capacity_fail_closed() -> None:
    m = CollectorClockShadow(1, 100, "e", "c")
    m.request("t", wall_ns=1, monotonic_ns=1)
    m.acknowledge("t", wall_ns=2, monotonic_ns=2)
    m.baseline("t", wall_ns=3, monotonic_ns=3)
    with pytest.raises(RuntimeError, match="missing exchange event clock"):
        m.delta("t", wall_ns=4, monotonic_ns=4, exchange_event_ts_ms=None)
    with pytest.raises(RuntimeError, match="capacity"):
        m.request("other", wall_ns=5, monotonic_ns=5)


def test_liveness_exchange_regression_rate_unsubscribe_and_journal(tmp_path) -> None:
    journal = tmp_path / "journal.jsonl"
    m = CollectorClockShadow(1, 1, "e1", "c1", journal_path=journal)
    m.request("t", wall_ns=1, monotonic_ns=1)
    with pytest.raises(RuntimeError, match="rate limit"):
        m.request("t", wall_ns=2, monotonic_ns=2)
    m.acknowledge("t", wall_ns=3, monotonic_ns=3)
    m.baseline("t", wall_ns=4, monotonic_ns=4)
    m.delta("t", wall_ns=5, monotonic_ns=5, exchange_event_ts_ms=10)
    with pytest.raises(RuntimeError, match="exchange event clock regression"):
        m.delta("t", wall_ns=6, monotonic_ns=6, exchange_event_ts_ms=9)
    assert not m.tokens["t"].valid
    m.unsubscribe("t", "test_end")
    m.request("other", wall_ns=2_000_000_000, monotonic_ns=2_000_000_000)
    m.acknowledge("other", wall_ns=2_000_000_001, monotonic_ns=2_000_000_001)
    m.baseline("other", wall_ns=2_000_000_002, monotonic_ns=2_000_000_002)
    m.mark_liveness("other", "DEAD")
    assert not m.tokens["other"].valid
    lines = journal.read_text().splitlines()
    sequences = [__import__("json").loads(line)["sequence"] for line in lines]
    assert sequences == list(range(1, len(lines) + 1))


def test_bounded_buffer_drops_fail_closed() -> None:
    q = BoundedFrameBuffer(1)
    q.put({"id": 1})
    with pytest.raises(RuntimeError, match="backpressure frame drop"):
        q.put({"id": 2})
    assert q.dropped_frames == 1


def test_expected_demand_hash_is_stable_and_zero_notional() -> None:
    demand = ExpectedTokenDemand("Busan", "2026-08-29", ("c",), ("y",), ("n",), ("27", "28"), 1, 2, 3, "next print", "frozen universe")
    assert demand.to_dict()["market_identity_hash"] == demand.to_dict()["market_identity_hash"]
    assert _machine().snapshot()["notional_usd"] == 0


def test_reconciliation_excludes_noncausal_legacy_reaction_row() -> None:
    rows, summary = build_reconciliation(
        read_jsonl_gz(STAGE3 / "evidence/PRIMARY_ORACLE_ROWS.jsonl.gz"),
        read_jsonl_gz(STAGE3 / "evidence/REACTION_WINDOW_ROWS.jsonl.gz"),
        read_jsonl_gz(STAGE3 / "evidence/MATCHED_BASELINE_ROWS.jsonl.gz"),
        read_json(STAGE3 / "CITY_GATES.json"),
        read_json(STAGE3 / "REACTION_INFERENCE_AND_CONCENTRATION.json"),
    )
    mismatch = [row for row in rows if row["diagnostic_old_reaction_disagrees_with_causal_primary"]]
    assert [row["event_id"] for row in mismatch] == ["819527fa18591a1a0759fed3e073e90ee3f807b00e2ad17f6b3b446503456695"]
    assert summary["canonical_headlines_from_single_row_table"]["reaction_primary_aggregate"] == 89


def test_reconciliation_duplicate_and_missing_inputs_fail_closed() -> None:
    oracle = [{"event_id": str(i), "action": {"action": "NO_TRADE"}} for i in range(841)]
    baseline = [{"event_id": str(i)} for i in range(841)]
    with pytest.raises(RuntimeError, match="duplicate oracle"):
        build_reconciliation(oracle + [oracle[0]], [], baseline)
    with pytest.raises(RuntimeError, match="baseline event set mismatch"):
        build_reconciliation(oracle, [], baseline[:-1])


def test_denominator_keeps_all_events_and_reports_intersection_separately() -> None:
    rows = [
        {
            "event_id": "a", "target_date": "2026-01-01", "official_print_id": "p1",
            "same_row_intersection_eligible": True, "persistence": {"status": "available"},
            "recent_slope": {"status": "available"}, "forecast_only": {"status": "available"},
            "market_only_features": {"status": "available"},
        },
        {
            "event_id": "b", "target_date": "2026-01-02", "official_print_id": "p2",
            "same_row_intersection_eligible": False, "persistence": {"status": "available"},
            "recent_slope": {"status": "available"}, "forecast_only": {"status": "available"},
            "market_only_features": {"status": "unavailable"},
        },
    ]
    oracle = [
        {"event_id": "a", "action": {"action": "NO_TRADE"}},
        {"event_id": "b", "action": {"action": "NO_TRADE"}},
    ]
    result = denominator_diagnostic(rows, oracle)
    assert result["full_event_denominator"]["raw_n"] == 2
    assert result["all_baseline_exact_intersection"]["raw_n"] == 1


@pytest.mark.parametrize("mutation", ["missing", "extra", "size", "hash", "duplicate"])
def test_full_package_verifier_fails_closed(tmp_path, mutation: str) -> None:
    original = b"frozen-evidence\n"
    payload = b"same-size-drift\n" if mutation == "hash" else original
    manifest = {"entries": [{"path": "evidence.bin", "size_bytes": len(original), "sha256": hashlib.sha256(original).hexdigest()}]}
    archive_path = tmp_path / "test.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        if mutation != "missing":
            archive.writestr("evidence.bin", original + b"x" if mutation == "size" else payload)
        if mutation == "extra":
            archive.writestr("unexpected", b"x")
        if mutation == "duplicate":
            archive.writestr("evidence.bin", original)
        archive.writestr("PACKAGE_CONTENTS.json", b"{}")
    with pytest.raises(RuntimeError):
        verify_archive(archive_path, manifest)
