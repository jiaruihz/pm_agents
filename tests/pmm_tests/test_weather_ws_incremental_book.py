from __future__ import annotations

import pytest

from weather_data_feed.ws_incremental_book import (
    BookReconstructionError,
    IncrementalBookReconstructor,
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
