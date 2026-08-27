from __future__ import annotations

from dataclasses import replace

import pytest

from src.platform.market_data.executable_book_truth import (
    align_book_checkpoints,
    executable_book_truth,
    invalid_book_truth,
    weather_taker_fee,
)
from weather_data_feed.ws_incremental_book import IncrementalBookReconstructor


def _book():
    engine = IncrementalBookReconstructor()
    engine.activate_epoch("epoch-1", ["token-1"])
    engine.apply_envelope(
        {
            "subscription_epoch_id": "epoch-1",
            "received_at_utc": "2026-08-20T10:00:00Z",
            "message": {
                "event_type": "book",
                "asset_id": "token-1",
                "timestamp": "1000",
                "bids": [
                    {"price": "0.40", "size": "2"},
                    {"price": "0.39", "size": "10"},
                ],
                "asks": [
                    {"price": "0.42", "size": "3"},
                    {"price": "0.43", "size": "10"},
                ],
            },
        }
    )
    return engine.snapshot("token-1", observed_at_utc="2026-08-20T10:00:00Z")


def test_fee_aware_one_five_ten_share_sweeps() -> None:
    truth = executable_book_truth(_book())
    assert truth.book_valid is True
    assert truth.gap_reason is None
    assert truth.queue_truth is False
    assert truth.maker_fill_proxy_only is True
    assert {(row.side, row.shares) for row in truth.sweeps} == {
        (side, shares) for side in ("buy", "sell") for shares in (1.0, 5.0, 10.0)
    }
    buy_five = next(row for row in truth.sweeps if row.side == "buy" and row.shares == 5)
    gross = 3 * 0.42 + 2 * 0.43
    fee = weather_taker_fee(shares=3, price=0.42) + weather_taker_fee(
        shares=2, price=0.43
    )
    assert buy_five.gross_value_usd == pytest.approx(gross)
    assert buy_five.taker_fee_usd == pytest.approx(fee)
    assert buy_five.effective_value_usd == pytest.approx(gross + fee)
    sell_ten = next(row for row in truth.sweeps if row.side == "sell" and row.shares == 10)
    assert sell_ten.effective_value_usd == pytest.approx(
        2 * 0.40 + 8 * 0.39
        - weather_taker_fee(shares=2, price=0.40)
        - weather_taker_fee(shares=8, price=0.39)
    )


def test_insufficient_depth_fails_closed_for_requested_sweep() -> None:
    book = replace(_book(), asks=((0.42, 0.5),))
    truth = executable_book_truth(book)
    buy_one = next(row for row in truth.sweeps if row.side == "buy" and row.shares == 1)
    assert buy_one.fully_executable is False
    assert buy_one.effective_value_usd is None


@pytest.mark.parametrize("shares", [[float("nan")], [float("inf")], [1, 1], [0]])
def test_rejects_invalid_sweep_quantities(shares) -> None:
    with pytest.raises(ValueError, match="shares"):
        executable_book_truth(_book(), shares=shares)


@pytest.mark.parametrize(
    "bids,asks",
    [
        (((float("nan"), 1.0),), ((0.5, 1.0),)),
        (((0.4, -1.0),), ((0.5, 1.0),)),
        (((0.4, 1.0),), ((1.1, 1.0),)),
    ],
)
def test_rejects_invalid_book_levels(bids, asks) -> None:
    with pytest.raises(ValueError, match="book levels"):
        executable_book_truth(replace(_book(), bids=bids, asks=asks))


def test_invalid_truth_requires_reason_and_has_no_sweeps() -> None:
    invalid = invalid_book_truth(token_id="token-1", gap_reason="sequence_gap")
    assert invalid.book_valid is False
    assert invalid.gap_reason == "sequence_gap"
    assert invalid.sweeps == ()
    with pytest.raises(ValueError, match="gap_reason"):
        invalid_book_truth(token_id="token-1", gap_reason="")


def test_event_alignment_is_asof_and_fails_closed_when_stale_or_missing() -> None:
    truth = executable_book_truth(_book())
    rows = align_book_checkpoints(
        [truth],
        [
            {
                "event_id": "event-1",
                "token_id": "token-1",
                "checkpoints": {
                    "source_t0": "2026-08-20T10:00:30Z",
                    "official_plus_120s": "2026-08-20T10:03:00Z",
                },
            },
            {
                "event_id": "event-2",
                "token_id": "missing-token",
                "checkpoints": {"source_t0": "2026-08-20T10:00:30Z"},
            },
        ],
        max_age_seconds=120,
    )
    assert rows[0].book_valid is True
    assert rows[0].age_seconds == 30
    assert rows[1].book_valid is False
    assert rows[1].gap_reason == "book_state_stale"
    assert rows[2].book_valid is False
    assert rows[2].gap_reason == "book_state_missing"


def test_event_alignment_is_deterministic_under_input_permutation() -> None:
    first = executable_book_truth(_book())
    second = replace(
        first,
        as_of_utc="2026-08-20T10:00:10Z",
        truth_id="later-truth",
        book_snapshot_id="later-book",
    )
    events = [
        {
            "event_id": "event-1",
            "token_id": "token-1",
            "checkpoints": {"source_t0": "2026-08-20T10:00:30Z"},
        }
    ]
    assert align_book_checkpoints([first, second], events) == align_book_checkpoints(
        [second, first], events
    )
