from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from src.platform.market_data.execution_evidence import ExecutionEvidenceRecorder


NOW = datetime(2026, 8, 30, 1, 0, tzinfo=timezone.utc)


def _epoch() -> dict:
    return {
        "subscription_epoch_id": "epoch-1",
        "reason": "connect",
        "token_ids": ["yes-token"],
        "producer_build_id": "build-1",
        "selector_version": "selector-1",
        "capture_policy": {"scope": "test"},
        "token_rows": {
            "yes-token": {
                "city": "Helsinki",
                "event_date": "2026-08-30",
                "condition_id": "condition-1",
                "market_id": "market-1",
                "bracket": "22",
                "outcome": "YES",
            }
        },
    }


def _envelope(message: object, at: datetime, line: int) -> dict:
    return {
        "subscription_epoch_id": "epoch-1",
        "received_at_utc": at.isoformat().replace("+00:00", "Z"),
        "received_at_ns": int(at.timestamp() * 1_000_000_000),
        "producer": "test",
        "producer_build_id": "build-1",
        "selector_version": "selector-1",
        "message": message,
        "_raw_path": "/raw/market_books_ws.jsonl",
        "_line_number": line,
    }


def _read_product(root, product: str) -> list[dict]:
    rows = []
    for path in sorted((root / product).glob("*/*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text().splitlines())
    return rows


def test_recorder_materializes_public_tape_and_bounded_book_evidence(tmp_path) -> None:
    recorder = ExecutionEvidenceRecorder(
        tmp_path,
        min_periodic_interval_sec=10,
        checkpoint_grace_sec=5,
        daily_budget_bytes=10_000_000,
    )
    recorder.activate_epoch(_epoch())
    recorder.register_capture_demands(
        [
            {
                "resolution_status": "resolved_direct_token",
                "demand_id": "demand-1",
                "token_id": "yes-token",
                "strategy_key": "reheat_risk.current_yes",
                "trigger_event_id": "event-1",
                "requested_at_utc": NOW.isoformat(),
                "expires_at_utc": (NOW + timedelta(seconds=40)).isoformat(),
                "requested_checkpoints_seconds": [0, 30],
            }
        ]
    )
    baseline = _envelope(
        {
            "event_type": "book",
            "asset_id": "yes-token",
            "timestamp": "1000",
            "bids": [{"price": "0.60", "size": "10"}],
            "asks": [{"price": "0.62", "size": "10"}],
        },
        NOW,
        1,
    )
    recorder.ingest(baseline, now_utc=NOW)
    trade = _envelope(
        {
            "event_type": "last_trade_price",
            "asset_id": "yes-token",
            "market": "condition-1",
            "price": "0.61",
            "size": "5",
            "side": "BUY",
            "timestamp": "1001",
            "transaction_hash": "0xtx",
        },
        NOW + timedelta(seconds=1),
        2,
    )
    recorder.ingest(trade, now_utc=NOW + timedelta(seconds=1))
    delta = _envelope(
        {
            "event_type": "price_change",
            "timestamp": "1011",
            "price_changes": [
                {
                    "asset_id": "yes-token",
                    "side": "SELL",
                    "price": "0.62",
                    "size": "0",
                    "best_bid": "0.60",
                    "best_ask": "0.63",
                },
                {
                    "asset_id": "yes-token",
                    "side": "SELL",
                    "price": "0.63",
                    "size": "10",
                    "best_bid": "0.60",
                    "best_ask": "0.63",
                },
            ],
        },
        NOW + timedelta(seconds=11),
        3,
    )
    recorder.ingest(delta, now_utc=NOW + timedelta(seconds=11))
    recorder.poll(NOW + timedelta(seconds=30))

    books = _read_product(tmp_path, "public_books")
    prints = _read_product(tmp_path, "market_trade_prints")
    assert {row["evidence_reason"] for row in books} == {
        "fresh_book_baseline",
        "public_trade_context",
        "periodic_changed_state",
        "requested_strategy_checkpoint",
    }
    assert len([row for row in books if row["evidence_reason"] == "requested_strategy_checkpoint"]) == 2
    assert all(row["execution_book_snapshot_id"] is None for row in books)
    assert all(row["feature_book_snapshot_id"] is None for row in books)
    assert all(row["execution_claim_status"].startswith("unjoined") for row in books)
    assert books[0]["baseline_raw_frame_ref"]["archive_path"] == "/raw/market_books_ws.jsonl"
    assert books[0]["baseline_raw_frame_ref"]["line_number"] == 1
    assert len(prints) == 1
    assert prints[0]["evidence_class"] == "public_exchange_trade_not_own_fill"
    assert prints[0]["own_fill_claim_status"] == "not_joined"
    health = recorder.health(NOW + timedelta(seconds=31))
    assert health["status"] == "ok"
    assert health["requested_checkpoint_rows"] == 2
    assert health["pending_requested_checkpoints"] == 0
    assert health["market_trade_print_rows"] == 1
    recorder.close()


def test_reconnect_requires_fresh_baseline_before_book_evidence(tmp_path) -> None:
    recorder = ExecutionEvidenceRecorder(tmp_path, daily_budget_bytes=1_000_000)
    recorder.activate_epoch(_epoch())
    recorder.ingest(
        _envelope(
            {
                "event_type": "book",
                "asset_id": "yes-token",
                "bids": [{"price": "0.50", "size": "5"}],
                "asks": [{"price": "0.55", "size": "5"}],
            },
            NOW,
            1,
        ),
        now_utc=NOW,
    )
    reconnect = {**_epoch(), "subscription_epoch_id": "epoch-2"}
    recorder.activate_epoch(reconnect)
    recorder.poll(NOW + timedelta(seconds=1))
    assert recorder.engine.books == {}
    recorder.close()


def test_periodic_changed_state_ignores_unchanged_parity_lineage(tmp_path) -> None:
    recorder = ExecutionEvidenceRecorder(
        tmp_path,
        min_periodic_interval_sec=10,
        daily_budget_bytes=1_000_000,
    )
    recorder.activate_epoch(_epoch())
    recorder.ingest(
        _envelope(
            {
                "event_type": "book",
                "asset_id": "yes-token",
                "bids": [{"price": "0.50", "size": "5"}],
                "asks": [{"price": "0.55", "size": "5"}],
            },
            NOW,
            1,
        ),
        now_utc=NOW,
    )
    recorder.ingest(
        _envelope(
            {
                "event_type": "best_bid_ask",
                "asset_id": "yes-token",
                "best_bid": "0.50",
                "best_ask": "0.55",
                "timestamp": "1011",
            },
            NOW + timedelta(seconds=11),
            2,
        ),
        now_utc=NOW + timedelta(seconds=11),
    )

    books = _read_product(tmp_path, "public_books")
    assert len(books) == 1
    assert books[0]["evidence_reason"] == "fresh_book_baseline"
    assert books[0]["public_book_state_id"]
    recorder.close()


def test_evidence_budget_is_hard_bounded_without_degrading_raw_owner(tmp_path) -> None:
    recorder = ExecutionEvidenceRecorder(tmp_path, daily_budget_bytes=100)
    recorder.activate_epoch(_epoch())
    recorder.ingest(
        _envelope(
            {
                "event_type": "book",
                "asset_id": "yes-token",
                "bids": [{"price": "0.50", "size": "5"}],
                "asks": [{"price": "0.55", "size": "5"}],
            },
            NOW,
            1,
        ),
        now_utc=NOW,
    )

    health = recorder.health(NOW)
    assert health["status"] == "budget_exhausted"
    assert health["budget_exhausted"] is True
    assert health["skipped_budget_rows"] >= 1
    assert health["day_bytes"] <= 100
    recorder.close()


def test_committed_evidence_is_idempotent_across_process_restart(tmp_path) -> None:
    demand = {
        "resolution_status": "resolved_direct_token",
        "demand_id": "restart-demand",
        "token_id": "yes-token",
        "strategy_key": "weather.metar_ws_event_repricing",
        "requested_at_utc": NOW.isoformat(),
        "expires_at_utc": (NOW + timedelta(seconds=10)).isoformat(),
        "requested_checkpoints_seconds": [0],
    }
    baseline = _envelope(
        {
            "event_type": "book",
            "asset_id": "yes-token",
            "timestamp": "1000",
            "bids": [{"price": "0.50", "size": "5"}],
            "asks": [{"price": "0.55", "size": "5"}],
        },
        NOW,
        1,
    )
    trade = _envelope(
        {
            "event_type": "last_trade_price",
            "asset_id": "yes-token",
            "market": "condition-1",
            "price": "0.52",
            "size": "2",
            "side": "BUY",
            "timestamp": "1001",
            "transaction_hash": "0xrestart",
        },
        NOW + timedelta(seconds=1),
        2,
    )

    first = ExecutionEvidenceRecorder(tmp_path, daily_budget_bytes=1_000_000)
    first.activate_epoch(_epoch())
    first.register_capture_demands([demand])
    first.ingest(baseline, now_utc=NOW)
    first.ingest(trade, now_utc=NOW + timedelta(seconds=1))
    first.close()
    before_books = _read_product(tmp_path, "public_books")
    before_prints = _read_product(tmp_path, "market_trade_prints")

    restarted = ExecutionEvidenceRecorder(tmp_path, daily_budget_bytes=1_000_000)
    restarted_epoch = {**_epoch(), "subscription_epoch_id": "epoch-2"}
    restarted.activate_epoch(restarted_epoch)
    restarted.register_capture_demands([demand])
    restarted.ingest(
        {**baseline, "subscription_epoch_id": "epoch-2"}, now_utc=NOW
    )
    restarted.ingest(
        {**trade, "subscription_epoch_id": "epoch-2"},
        now_utc=NOW + timedelta(seconds=1),
    )
    health = restarted.health(NOW + timedelta(seconds=2))
    restarted.close()

    assert _read_product(tmp_path, "public_books") == before_books
    assert _read_product(tmp_path, "market_trade_prints") == before_prints
    assert health["pending_requested_checkpoints"] == 0
    assert health["dedupe_evidence_ids"] == len(before_books)
    assert health["dedupe_trade_print_ids"] == len(before_prints)
    assert health["dedupe_completed_checkpoint_ids"] == 1
    assert health["dedupe_restore_errors"] == 0
