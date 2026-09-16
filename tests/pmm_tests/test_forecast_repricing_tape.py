from __future__ import annotations

from weather_model_evaluation.forecast_repricing_tape import replay_passive_orders


def _frame(epoch: str, ts: str, ns: int, message: object) -> dict:
    return {
        "subscription_epoch_id": epoch,
        "received_at_utc": ts,
        "received_at_ns": ns,
        "message": message,
    }


def test_queue_conservative_fill_requires_trade_volume_beyond_queue() -> None:
    epochs = [
        {
            "subscription_epoch_id": "epoch-1",
            "started_at_utc": "2026-08-10T00:00:00Z",
            "reason": "connect",
            "token_ids": ["yes-token"],
            "token_rows": {
                "yes-token": {
                    "city": "TestCity",
                    "event_date": "2026-08-10",
                    "bracket": "30",
                    "outcome": "yes",
                    "condition_id": "condition-30",
                }
            },
        }
    ]
    frames = [
        _frame(
            "epoch-1",
            "2026-08-10T00:00:00Z",
            1,
            {
                "event_type": "book",
                "asset_id": "yes-token",
                "bids": [{"price": "0.20", "size": "5"}],
                "asks": [{"price": "0.22", "size": "10"}],
            },
        ),
        _frame(
            "epoch-1",
            "2026-08-10T00:00:10Z",
            2,
            {
                "event_type": "last_trade_price",
                "asset_id": "yes-token",
                "market": "condition-30",
                "price": "0.20",
                "size": "5",
                "side": "SELL",
                "timestamp": "10",
                "transaction_hash": "0x1",
            },
        ),
        _frame(
            "epoch-1",
            "2026-08-10T00:00:20Z",
            3,
            {
                "event_type": "last_trade_price",
                "asset_id": "yes-token",
                "market": "condition-30",
                "price": "0.20",
                "size": "5",
                "side": "SELL",
                "timestamp": "20",
                "transaction_hash": "0x2",
            },
        ),
        _frame(
            "epoch-1",
            "2026-08-10T00:01:20Z",
            4,
            {
                "event_type": "price_change",
                "price_changes": [
                    {
                        "asset_id": "yes-token",
                        "side": "BUY",
                        "price": "0.21",
                        "size": "10",
                        "best_bid": "0.21",
                        "best_ask": "0.22",
                    }
                ],
            },
        ),
    ]

    rows, summary = replay_passive_orders(epochs, frames)

    scored = [row for row in rows if row["status"] == "filled_exit_scoreable"]
    assert len(scored) == 1
    assert scored[0]["sell_volume_at_or_below_quote"] == 10.0
    assert scored[0]["queue_ahead_shares"] == 5.0
    assert scored[0]["pnl"] > 0
    assert scored[0]["dynamic_pnl"] is not None
    assert scored[0]["dynamic_pnl"] > 0
    assert summary["queue_conservative_fills"] == 1


def test_bid_plus_tick_posts_inside_spread_with_zero_visible_queue() -> None:
    epochs = [
        {
            "subscription_epoch_id": "epoch-1",
            "started_at_utc": "2026-08-10T00:00:00Z",
            "reason": "connect",
            "token_ids": ["yes-token"],
            "token_rows": {
                "yes-token": {
                    "city": "TestCity",
                    "event_date": "2026-08-10",
                    "bracket": "30",
                    "outcome": "yes",
                    "condition_id": "condition-30",
                }
            },
        }
    ]
    frames = [
        _frame(
            "epoch-1",
            "2026-08-10T00:00:00Z",
            1,
            {
                "event_type": "book",
                "asset_id": "yes-token",
                "bids": [{"price": "0.20", "size": "100"}],
                "asks": [{"price": "0.23", "size": "10"}],
            },
        ),
        _frame(
            "epoch-1",
            "2026-08-10T00:00:10Z",
            2,
            {
                "event_type": "last_trade_price",
                "asset_id": "yes-token",
                "market": "condition-30",
                "price": "0.20",
                "size": "5",
                "side": "SELL",
                "timestamp": "10",
                "transaction_hash": "0x1",
            },
        ),
        _frame(
            "epoch-1",
            "2026-08-10T00:01:10Z",
            3,
            {
                "event_type": "price_change",
                "price_changes": [
                    {
                        "asset_id": "yes-token",
                        "side": "BUY",
                        "price": "0.21",
                        "size": "10",
                        "best_bid": "0.21",
                        "best_ask": "0.23",
                    }
                ],
            },
        ),
    ]

    rows, summary = replay_passive_orders(epochs, frames, quote_mode="bid_plus_tick")

    scored = [row for row in rows if row["status"] == "filled_exit_scoreable"]
    assert len(scored) == 1
    assert scored[0]["entry_reference_bid"] == 0.20
    assert scored[0]["entry_bid"] == 0.21
    assert scored[0]["queue_ahead_shares"] == 0.0
    assert scored[0]["adverse_cancel_at_utc"] is None
    assert summary["queue_conservative_fills"] == 1


def test_bid_plus_tick_skips_one_tick_spread_instead_of_crossing() -> None:
    epochs = [
        {
            "subscription_epoch_id": "epoch-1",
            "started_at_utc": "2026-08-10T00:00:00Z",
            "reason": "connect",
            "token_ids": ["yes-token"],
            "token_rows": {"yes-token": {"outcome": "yes"}},
        }
    ]
    frames = [
        _frame(
            "epoch-1",
            "2026-08-10T00:00:00Z",
            1,
            {
                "event_type": "book",
                "asset_id": "yes-token",
                "bids": [{"price": "0.20", "size": "100"}],
                "asks": [{"price": "0.21", "size": "10"}],
            },
        )
    ]

    rows, summary = replay_passive_orders(epochs, frames, quote_mode="bid_plus_tick")

    assert rows == []
    assert summary["orders_posted"] == 0
    assert summary["non_postable_quotes"] == 1


def test_bid_plus_tick_uses_native_mill_tick_at_extreme_price() -> None:
    epochs = [
        {
            "subscription_epoch_id": "epoch-1",
            "started_at_utc": "2026-08-10T00:00:00Z",
            "reason": "connect",
            "token_ids": ["yes-token"],
            "token_rows": {"yes-token": {"outcome": "yes"}},
        }
    ]
    frames = [
        _frame(
            "epoch-1",
            "2026-08-10T00:00:00Z",
            1,
            {
                "event_type": "book",
                "asset_id": "yes-token",
                "bids": [{"price": "0.035", "size": "100"}],
                "asks": [{"price": "0.050", "size": "10"}],
            },
        )
    ]

    rows, summary = replay_passive_orders(epochs, frames, quote_mode="bid_plus_tick")

    assert len(rows) == 1
    assert rows[0]["entry_bid"] == 0.036
    assert rows[0]["tick_size"] == 0.001
    assert summary["orders_posted"] == 1
