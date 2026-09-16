"""Executable cost curve coverage for exact Polymarket taker fees."""

from decimal import Decimal

import pytest

from src.polymarket_alpha.books import (
    ExecutableCostError,
    executable_sweep,
    polymarket_taker_fee,
)
from src.polymarket_alpha.contracts import BookLevel


D = Decimal


def test_official_taker_fee_is_not_flat_ad_valorem() -> None:
    fee = polymarket_taker_fee(
        shares=D("1"), price=D("0.949"), fee_rate=D("0.04")
    )
    assert fee == D("0.00193596")
    assert D("0.949") + fee == D("0.95093596")
    assert fee != D("0.949") * D("0.04")


def test_multilevel_buy_sweep_seals_fills_fee_and_all_in_price() -> None:
    result = executable_sweep(
        (
            BookLevel(price=D("0.40"), size=D("2")),
            BookLevel(price=D("0.50"), size=D("10")),
        ),
        target_size=D("5"),
        side="BUY",
        fee_rate=D("0.05"),
        slippage_buffer=D("0.01"),
    )
    assert result.fully_executable is True
    assert result.fills == (
        BookLevel(price=D("0.40"), size=D("2")),
        BookLevel(price=D("0.50"), size=D("3")),
    )
    assert result.gross_value == D("2.30")
    assert result.taker_fee == D("0.0615")
    assert result.slippage_cost == D("0.05")
    assert result.effective_price == D("0.4823")


def test_insufficient_or_unsorted_depth_fails_closed() -> None:
    insufficient = executable_sweep(
        (BookLevel(price=D("0.40"), size=D("1")),),
        target_size=D("2"), side="BUY", fee_rate=D("0.05"),
    )
    assert insufficient.fully_executable is False
    assert insufficient.effective_price is None

    with pytest.raises(ExecutableCostError, match="ascending"):
        executable_sweep(
            (
                BookLevel(price=D("0.50"), size=D("1")),
                BookLevel(price=D("0.40"), size=D("1")),
            ),
            target_size=D("1"), side="BUY", fee_rate=D("0.05"),
        )
