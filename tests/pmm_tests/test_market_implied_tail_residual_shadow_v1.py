from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ops/market_implied_tail_residual_shadow_v1.py"
SPEC = importlib.util.spec_from_file_location("market_implied_tail_residual_shadow_v1", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def book(capture_id: str, outcome: str, bid: float, ask: float) -> dict:
    return {
        "book_capture_id": capture_id,
        "outcome": outcome,
        "status": "ok",
        "summary": {"best_bid": bid, "best_ask": ask, "bid_size": 10, "ask_size": 12, "depth_ask_5c": 20},
    }


def fixture() -> tuple[dict, dict]:
    books = {
        "batch_capture_id": "batch-1",
        "available_at_utc": "2026-08-07T08:10:00Z",
        "producer_build_id": "source-sha",
        "records": [
            book("y1", "yes", 0.09, 0.11), book("n1", "no", 0.88, 0.90),
            book("y2", "yes", 0.39, 0.41), book("n2", "no", 0.58, 0.60),
            book("y3", "yes", 0.19, 0.21), book("n3", "no", 0.78, 0.80),
        ],
    }
    ladders = {
        "batch_capture_id": "batch-1",
        "available_at_utc": "2026-08-07T08:10:00Z",
        "records": [{
            "city": "Tokyo", "target_date": "2026-08-07", "event_id": "event-1", "event_slug": "event",
            "market_distribution_complete": True, "two_sided_book_distribution_complete": True,
            "rungs": [
                {"bracket": "30", "market_id": "m1", "yes_book_capture_id": "y1", "no_book_capture_id": "n1"},
                {"bracket": "31", "market_id": "m2", "yes_book_capture_id": "y2", "no_book_capture_id": "n2"},
                {"bracket": "32+", "market_id": "m3", "yes_book_capture_id": "y3", "no_book_capture_id": "n3"},
            ],
        }],
    }
    return books, ladders


def test_build_rows_is_zero_notional_and_deduplicates_checkpoint() -> None:
    books, ladders = fixture()
    rows, state, counts = MODULE.build_rows(
        books, ladders, {}, ingested_at=datetime(2026, 8, 7, 8, 11, tzinfo=timezone.utc), build_id="test-sha"
    )
    assert counts == {"events_seen": 1, "events_new": 1, "rungs_emitted": 3, "events_missing_timezone": 0}
    assert [row["mode_distance"] for row in rows] == ["cold_1", "mode", "hot_1"]
    assert rows[1]["normalized_direct_mid"] == pytest.approx(0.4 / 0.7)
    assert rows[2]["market_implied_hotter_tail_mass"] == pytest.approx(0.2 / 0.7)
    assert all(row["notional_usd"] == 0.0 for row in rows)
    assert all(row["execution_mode"] == "shadow_zero_notional" for row in rows)
    assert all("order" not in key and "trade_intent" not in key for row in rows for key in row)

    repeated, _, repeated_counts = MODULE.build_rows(
        books, ladders, state, ingested_at=datetime(2026, 8, 7, 8, 12, tzinfo=timezone.utc), build_id="test-sha"
    )
    assert repeated == []
    assert repeated_counts["events_new"] == 0


def test_batch_mismatch_fails_closed() -> None:
    books, ladders = fixture()
    ladders["batch_capture_id"] = "other"
    try:
        MODULE.build_rows(books, ladders, {}, ingested_at=datetime.now(timezone.utc), build_id="test")
    except ValueError as exc:
        assert "batch mismatch" in str(exc)
    else:
        raise AssertionError("expected batch mismatch to fail")
