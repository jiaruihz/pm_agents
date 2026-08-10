from __future__ import annotations

import pytest

from weather_data_feed.ws_incremental_book import (
    BookReconstructionError,
    IncrementalBookReconstructor,
    compare_rest_ws_parity,
    extract_market_trade_prints,
    materialize_reconstructed_books,
)


def _envelope(epoch: str, message: object, received: str = "2026-08-09T10:00:00Z"):
    return {
        "subscription_epoch_id": epoch,
        "received_at_utc": received,
        "message": message,
    }


def test_reconstructs_baseline_delta_and_five_share_depth() -> None:
    engine = IncrementalBookReconstructor()
    engine.activate_epoch("epoch-1", ["no-token"])
    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "book",
                "asset_id": "no-token",
                "timestamp": "1000",
                "bids": [{"price": "0.60", "size": "3"}, {"price": "0.59", "size": "4"}],
                "asks": [{"price": "0.62", "size": "2"}, {"price": "0.63", "size": "6"}],
            },
        )
    )
    first = engine.snapshot(
        "no-token", observed_at_utc="2026-08-09T10:00:01Z", requested_shares=5
    )
    assert first.sell_proceeds == pytest.approx(3 * 0.60 + 2 * 0.59)
    assert first.buy_cost == pytest.approx(2 * 0.62 + 3 * 0.63)
    assert first.depth_status == "five_share_executable"

    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "price_change",
                "timestamp": "1010",
                "price_changes": [
                    {
                        "asset_id": "no-token",
                        "side": "BUY",
                        "price": "0.61",
                        "size": "5",
                        "best_bid": "0.61",
                        "best_ask": "0.62",
                    }
                ],
            },
            "2026-08-09T10:00:02Z",
        )
    )
    second = engine.snapshot(
        "no-token", observed_at_utc="2026-08-09T10:00:02Z", requested_shares=5
    )
    assert second.best_bid == 0.61
    assert second.sell_proceeds == pytest.approx(3.05)
    assert second.snapshot_id != first.snapshot_id


def test_extracts_trade_print_without_claiming_own_fill() -> None:
    envelope = {
        **_envelope(
            "epoch-1",
            [
                {
                    "event_type": "last_trade_price",
                    "asset_id": "yes-token",
                    "market": "0xcondition",
                    "price": "0.20",
                    "size": "7.5",
                    "side": "SELL",
                    "timestamp": "1786320032527",
                    "transaction_hash": "0xtx",
                    "fee_rate_bps": "0",
                },
                {"event_type": "tick_size_change", "asset_id": "yes-token"},
            ],
            "2026-08-10T00:00:32.590Z",
        ),
        "received_at_ns": 1_786_320_032_590_148_000,
        "producer_build_id": "build-1",
        "selector_version": "selector-1",
        "_raw_path": "/raw/ws.jsonl",
        "_line_number": 9,
    }

    rows = extract_market_trade_prints(envelope)

    assert len(rows) == 1
    assert rows[0].token_id == "yes-token"
    assert rows[0].side == "SELL"
    assert rows[0].price == 0.20
    assert rows[0].size == 7.5
    assert rows[0].raw_frame_ref.archive_path == "/raw/ws.jsonl"
    assert rows[0].raw_frame_ref.line_number == 9
    assert rows[0].trade_print_id


def test_trade_print_rejects_invalid_exchange_fields() -> None:
    with pytest.raises(BookReconstructionError, match="asset_id and BUY/SELL"):
        extract_market_trade_prints(
            _envelope(
                "epoch-1",
                {
                    "event_type": "last_trade_price",
                    "asset_id": "yes-token",
                    "price": "0.20",
                    "size": "5",
                    "side": "UNKNOWN",
                },
            )
        )


def test_delta_before_baseline_and_new_epoch_fail_closed() -> None:
    engine = IncrementalBookReconstructor()
    engine.activate_epoch("epoch-1", ["no-token"])
    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "price_change",
                "price_changes": [
                    {
                        "asset_id": "no-token",
                        "side": "BUY",
                        "price": "0.50",
                        "size": "5",
                        "best_bid": "0.50",
                        "best_ask": "0.51",
                    }
                ],
            },
        )
    )
    with pytest.raises(BookReconstructionError, match="delta_before_book_baseline"):
        engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:01Z")

    engine.activate_epoch("epoch-2", ["no-token"])
    with pytest.raises(BookReconstructionError, match="book_baseline_missing"):
        engine.snapshot("no-token", observed_at_utc="2026-08-09T10:01:00Z")
    with pytest.raises(BookReconstructionError, match="epoch mismatch"):
        engine.apply_envelope(_envelope("epoch-1", {"event_type": "book"}))


def test_best_quote_parity_mismatch_invalidates_book() -> None:
    engine = IncrementalBookReconstructor()
    engine.activate_epoch("epoch-1", ["no-token"])
    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "book",
                "asset_id": "no-token",
                "bids": [{"price": "0.50", "size": "5"}],
                "asks": [{"price": "0.55", "size": "5"}],
            },
        )
    )
    with pytest.raises(BookReconstructionError, match="disagrees"):
        engine.apply_envelope(
            _envelope(
                "epoch-1",
                {
                    "event_type": "price_change",
                    "price_changes": [
                        {
                            "asset_id": "no-token",
                            "side": "BUY",
                            "price": "0.51",
                            "size": "5",
                            "best_bid": "0.50",
                            "best_ask": "0.55",
                        }
                    ],
                },
            )
        )
    with pytest.raises(BookReconstructionError, match="best_quote_parity_mismatch"):
        engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:02Z")


def test_best_bid_ask_message_is_a_parity_proof_not_a_synthetic_delta() -> None:
    engine = IncrementalBookReconstructor()
    engine.activate_epoch("epoch-1", ["no-token"])
    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "book",
                "asset_id": "no-token",
                "timestamp": "1000",
                "bids": [{"price": "0.50", "size": "5"}],
                "asks": [{"price": "0.55", "size": "5"}],
            },
        )
    )
    assert engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "best_bid_ask",
                "asset_id": "no-token",
                "timestamp": "1001",
                "best_bid": "0.50",
                "best_ask": "0.55",
            },
            "2026-08-09T10:00:01Z",
        )
    ) == ("no-token",)
    checked = engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:01Z")
    assert checked.parity_check_count == 1
    assert checked.last_parity_raw_frame_ref is not None

    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "best_bid_ask",
                "asset_id": "no-token",
                "timestamp": "1002",
                "best_bid": "0.51",
                "best_ask": "0.55",
            },
            "2026-08-09T10:00:02Z",
        )
    )
    with pytest.raises(BookReconstructionError, match="best_quote_parity_pending"):
        engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:02Z")
    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "price_change",
                "timestamp": "1002",
                "price_changes": [
                    {
                        "asset_id": "no-token",
                        "side": "BUY",
                        "price": "0.51",
                        "size": "5",
                        "best_bid": "0.51",
                        "best_ask": "0.55",
                    }
                ],
            },
            "2026-08-09T10:00:02.001Z",
        )
    )
    reconciled = engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:02.001Z")
    assert reconciled.best_bid == 0.51


def test_empty_side_zero_one_sentinels_match_empty_levels() -> None:
    engine = IncrementalBookReconstructor()
    engine.activate_epoch("epoch-1", ["no-token"])
    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "book",
                "asset_id": "no-token",
                "bids": [],
                "asks": [{"price": "0.99", "size": "5"}],
            },
        )
    )
    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "price_change",
                "price_changes": [
                    {
                        "asset_id": "no-token",
                        "side": "SELL",
                        "price": "0.98",
                        "size": "5",
                        "best_bid": "0",
                        "best_ask": "0.98",
                    }
                ],
            },
        )
    )
    snapshot = engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:01Z")
    assert snapshot.best_bid is None
    assert snapshot.best_ask == 0.98


def test_multi_level_delta_checks_parity_after_whole_message() -> None:
    engine = IncrementalBookReconstructor()
    engine.activate_epoch("epoch-1", ["no-token"])
    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "book",
                "asset_id": "no-token",
                "bids": [
                    {"price": "0.50", "size": "5"},
                    {"price": "0.49", "size": "5"},
                ],
                "asks": [{"price": "0.55", "size": "5"}],
            },
        )
    )
    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "price_change",
                "price_changes": [
                    {
                        "asset_id": "no-token",
                        "side": "BUY",
                        "price": "0.50",
                        "size": "0",
                        "best_bid": "0.51",
                        "best_ask": "0.55",
                    },
                    {
                        "asset_id": "no-token",
                        "side": "BUY",
                        "price": "0.51",
                        "size": "5",
                        "best_bid": "0.51",
                        "best_ask": "0.55",
                    },
                ],
            },
        )
    )
    assert engine.snapshot(
        "no-token", observed_at_utc="2026-08-09T10:00:01Z"
    ).best_bid == 0.51


def test_duplicate_frames_are_idempotent_and_snapshot_identity_is_state_based() -> None:
    engine = IncrementalBookReconstructor()
    engine.activate_epoch(
        "epoch-1",
        ["no-token"],
        producer_build_id="build-1",
        selector_version="selector-1",
        capture_policy={"scope": "hot-strip"},
        token_rows={"no-token": {"city": "Helsinki", "bracket": "22"}},
    )
    envelope = {
        **_envelope(
            "epoch-1",
            {
                "event_type": "book",
                "asset_id": "no-token",
                "timestamp": "1000",
                "hash": "book-hash-1",
                "bids": [{"price": "0.50", "size": "5"}],
                "asks": [{"price": "0.55", "size": "5"}],
            },
        ),
        "received_at_ns": 1_786_269_600_000_000_000,
        "producer_build_id": "build-1",
        "_raw_path": "/raw/ws.jsonl",
        "_line_number": 7,
    }
    assert engine.apply_envelope(envelope) == ("no-token",)
    first = engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:01Z")
    assert engine.apply_envelope(envelope) == ()
    second = engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:09Z")

    assert engine.duplicate_frame_count == 1
    assert first.book_snapshot_id == second.book_snapshot_id
    assert first.feature_book_snapshot_id == first.book_snapshot_id
    assert first.execution_book_snapshot_id is None
    assert first.baseline_raw_frame_ref.archive_path == "/raw/ws.jsonl"
    assert first.baseline_raw_frame_ref.line_number == 7
    execution = engine.snapshot(
        "no-token",
        observed_at_utc="2026-08-09T10:00:09Z",
        book_role="execution_quote",
    )
    assert execution.feature_book_snapshot_id is None
    assert execution.execution_book_snapshot_id == first.book_snapshot_id


def test_exchange_timestamp_regression_and_sequence_gap_fail_closed() -> None:
    engine = IncrementalBookReconstructor()
    engine.activate_epoch("epoch-1", ["no-token"])
    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "book",
                "asset_id": "no-token",
                "timestamp": "1000",
                "sequence": 10,
                "bids": [{"price": "0.50", "size": "5"}],
                "asks": [{"price": "0.55", "size": "5"}],
            },
        )
    )
    with pytest.raises(BookReconstructionError, match="sequence gap"):
        engine.apply_envelope(
            _envelope(
                "epoch-1",
                {
                    "event_type": "price_change",
                    "timestamp": "1010",
                    "sequence": 12,
                    "price_changes": [
                        {
                            "asset_id": "no-token",
                            "side": "BUY",
                            "price": "0.51",
                            "size": "5",
                            "best_bid": "0.51",
                            "best_ask": "0.55",
                        }
                    ],
                },
                "2026-08-09T10:00:01Z",
            )
        )
    with pytest.raises(BookReconstructionError, match="exchange_sequence_gap"):
        engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:02Z")

    engine.activate_epoch("epoch-2", ["no-token"])
    engine.apply_envelope(
        _envelope(
            "epoch-2",
            {
                "event_type": "book",
                "asset_id": "no-token",
                "timestamp": "2000",
                "bids": [{"price": "0.50", "size": "5"}],
                "asks": [{"price": "0.55", "size": "5"}],
            },
            "2026-08-09T10:01:00Z",
        )
    )
    with pytest.raises(BookReconstructionError, match="timestamp regressed"):
        engine.apply_envelope(
            _envelope(
                "epoch-2",
                {
                    "event_type": "price_change",
                    "timestamp": "1999",
                    "price_changes": [
                        {
                            "asset_id": "no-token",
                            "side": "BUY",
                            "price": "0.51",
                            "size": "5",
                            "best_bid": "0.51",
                            "best_ask": "0.55",
                        }
                    ],
                },
                "2026-08-09T10:01:01Z",
            )
        )


def test_selector_epoch_can_carry_but_reconnect_requires_fresh_baseline() -> None:
    engine = IncrementalBookReconstructor()
    engine.activate_epoch("epoch-1", ["no-token"])
    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "book",
                "asset_id": "no-token",
                "bids": [{"price": "0.50", "size": "5"}],
                "asks": [{"price": "0.55", "size": "5"}],
            },
        )
    )
    first = engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:01Z")
    engine.activate_epoch("epoch-2", ["no-token"], carry_forward=True)
    carried = engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:02Z")
    assert carried.best_bid == 0.50
    assert carried.book_snapshot_id != first.book_snapshot_id

    engine.activate_epoch("epoch-3", ["no-token"], carry_forward=False)
    with pytest.raises(BookReconstructionError, match="book_baseline_missing"):
        engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:03Z")


def test_rest_ws_parity_is_independent_and_never_backfills_state() -> None:
    engine = IncrementalBookReconstructor()
    engine.activate_epoch("epoch-1", ["no-token"])
    engine.apply_envelope(
        _envelope(
            "epoch-1",
            {
                "event_type": "book",
                "asset_id": "no-token",
                "timestamp": "1000",
                "hash": "same-hash",
                "bids": [
                    {"price": "0.50", "size": "5"},
                    {"price": "0.49", "size": "7"},
                ],
                "asks": [
                    {"price": "0.55", "size": "5"},
                    {"price": "0.56", "size": "7"},
                ],
            },
        )
    )
    snapshot = engine.snapshot("no-token", observed_at_utc="2026-08-09T10:00:01Z")
    rest = {
        "book_capture_id": "rest-1",
        "exchange_book_ts_raw": "1000",
        "exchange_book_hash": "same-hash",
        "raw": {
            "bids": [{"price": 0.50, "size": 5}, {"price": 0.49, "size": 7}],
            "asks": [{"price": 0.55, "size": 5}, {"price": 0.56, "size": 7}],
        },
    }
    parity = compare_rest_ws_parity(snapshot, rest)
    assert parity.parity_status == "full_depth_parity"
    assert parity.same_exchange_hash is True
    assert parity.blockers == ()

    prefix = compare_rest_ws_parity(
        snapshot,
        {
            **rest,
            "book_capture_id": "rest-prefix",
            "raw": {
                "bids": [{"price": 0.50, "size": 5}],
                "asks": [{"price": 0.55, "size": 5}],
            },
        },
    )
    assert prefix.parity_status == "rest_depth_prefix_parity"
    assert prefix.rest_depth_is_ws_prefix is True
    assert prefix.blockers == ()

    mismatch = compare_rest_ws_parity(
        snapshot,
        {
            **rest,
            "book_capture_id": "rest-2",
            "raw": {
                "bids": [{"price": 0.49, "size": 5}],
                "asks": [{"price": 0.56, "size": 5}],
            },
        },
    )
    assert mismatch.parity_status == "parity_mismatch"
    assert mismatch.blockers == ("best_quote_mismatch",)
    assert engine.snapshot(
        "no-token", observed_at_utc="2026-08-09T10:00:02Z"
    ).book_snapshot_id == snapshot.book_snapshot_id


def test_public_materializer_replays_epoch_chain_deterministically() -> None:
    epochs = [
        {
            "subscription_epoch_id": "epoch-1",
            "started_at_utc": "2026-08-09T09:59:00Z",
            "reason": "connect",
            "token_ids": ["no-token"],
            "token_rows": {"no-token": {"city": "Helsinki", "bracket": "22"}},
            "producer_build_id": "build-1",
            "selector_version": "selector-1",
            "capture_policy": {"scope": "hot-strip"},
        },
        {
            "subscription_epoch_id": "epoch-2",
            "previous_subscription_epoch_id": "epoch-1",
            "started_at_utc": "2026-08-09T10:00:01Z",
            "reason": "selector_reconcile",
            "token_ids": ["no-token"],
            "token_rows": {"no-token": {"city": "Helsinki", "bracket": "22"}},
            "producer_build_id": "build-1",
            "selector_version": "selector-1",
            "capture_policy": {"scope": "hot-strip"},
        },
    ]
    baseline = _envelope(
        "epoch-1",
        {
            "event_type": "book",
            "asset_id": "no-token",
            "timestamp": "1000",
            "bids": [{"price": "0.50", "size": "5"}],
            "asks": [{"price": "0.55", "size": "5"}],
        },
        "2026-08-09T10:00:00Z",
    )
    delta = _envelope(
        "epoch-2",
        {
            "event_type": "price_change",
            "timestamp": "1010",
            "price_changes": [
                {
                    "asset_id": "no-token",
                    "side": "BUY",
                    "price": "0.51",
                    "size": "5",
                    "best_bid": "0.51",
                    "best_ask": "0.55",
                }
            ],
        },
        "2026-08-09T10:00:02Z",
    )
    first = materialize_reconstructed_books(epochs, [delta, baseline, delta])
    second = materialize_reconstructed_books(epochs, [baseline, delta, delta])

    assert first.run_id == second.run_id
    assert [row.book_snapshot_id for row in first.snapshots] == [
        row.book_snapshot_id for row in second.snapshots
    ]
    assert first.input_frames == 3
    assert first.applied_frames == 2
    assert first.duplicate_frames == 1
    assert first.reconstruction_errors == 0
    assert first.snapshots[-1].delta_frame_count == 1
    assert first.snapshots[-1].baseline_raw_frame_ref.frame_id
    assert first.snapshots[-1].delta_chain_hash


def test_materializer_slice_starting_mid_socket_fails_closed_without_predecessor() -> None:
    epoch = {
        "subscription_epoch_id": "epoch-mid-socket",
        "previous_subscription_epoch_id": "epoch-not-in-slice",
        "started_at_utc": "2026-08-09T10:00:00Z",
        "reason": "selector_reconcile",
        "token_ids": ["no-token"],
    }
    delta = _envelope(
        "epoch-mid-socket",
        {
            "event_type": "price_change",
            "timestamp": "1010",
            "price_changes": [
                {
                    "asset_id": "no-token",
                    "side": "BUY",
                    "price": "0.51",
                    "size": "5",
                    "best_bid": "0.51",
                    "best_ask": "0.55",
                }
            ],
        },
        "2026-08-09T10:00:01Z",
    )

    run = materialize_reconstructed_books([epoch], [delta])

    assert run.snapshots == ()
    assert run.reconstruction_errors == 0
    assert len(run.blockers) == 1
    assert run.blockers[0]["reason"] == "delta_before_book_baseline"
    assert run.blockers[0]["recovery_status"] == "open"
