from decimal import Decimal

import pytest

from src.strategies.weather_edge_v1.execution.economics import (
    FeeEstimateError,
    estimate_polymarket_v2_fee,
)


def test_v2_fee_uses_shares_price_curve_and_five_decimal_rounding() -> None:
    assert estimate_polymarket_v2_fee(
        shares="5", rate="0.02", price="0.901", exponent="1"
    ) == Decimal("0.00892")


def test_v2_fee_honors_weather_exponent_one_and_minimum_quantum() -> None:
    assert estimate_polymarket_v2_fee(
        shares="1", rate="0.01", price="0.5", exponent="1"
    ) == Decimal("0.00250")
    assert estimate_polymarket_v2_fee(
        shares="0.0001", rate="0.01", price="0.5", exponent="1"
    ) == Decimal("0")


@pytest.mark.parametrize("exponent", ("-1", "0.5"))
def test_v2_fee_rejects_non_integer_or_negative_exponents(exponent: str) -> None:
    with pytest.raises(FeeEstimateError, match="exponent"):
        estimate_polymarket_v2_fee(
            shares="1", rate="0.01", price="0.5", exponent=exponent
        )
