"""Deterministic, forecast-only Polymarket V2 fee calculations."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


FEE_QUANTUM_USD = Decimal("0.00001")


class FeeEstimateError(ValueError):
    """The supplied fee parameters cannot safely represent the V2 formula."""


def _decimal(value: Decimal | str | int, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise FeeEstimateError(f"{name} must be a Decimal, integer, or decimal string")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FeeEstimateError(f"invalid decimal for {name}: {value!r}") from exc


def estimate_polymarket_v2_fee(
    *,
    shares: Decimal | str | int,
    rate: Decimal | str | int,
    price: Decimal | str | int,
    exponent: Decimal | str | int,
) -> Decimal:
    """Return a five-decimal forecast of ``shares*r*(p*(1-p))**e``.

    This is an estimate only.  It must never be recorded as a realized fee or
    maker rebate.  V2's exponent is intentionally restricted to a non-negative
    integer: Decimal fractional powers would introduce non-deterministic float
    semantics here.
    """

    parsed_shares = _decimal(shares, "shares")
    parsed_rate = _decimal(rate, "rate")
    parsed_price = _decimal(price, "price")
    parsed_exponent = _decimal(exponent, "exponent")
    if parsed_shares < 0 or parsed_rate < 0:
        raise FeeEstimateError("shares and rate must be non-negative")
    if not Decimal("0") < parsed_price < Decimal("1"):
        raise FeeEstimateError("price must be strictly between 0 and 1")
    if parsed_exponent < 0 or parsed_exponent != parsed_exponent.to_integral_value():
        raise FeeEstimateError("exponent must be a non-negative integer")

    raw_fee = parsed_shares * parsed_rate * (
        parsed_price * (Decimal("1") - parsed_price)
    ) ** int(parsed_exponent)
    if raw_fee < FEE_QUANTUM_USD:
        return Decimal("0")
    return raw_fee.quantize(FEE_QUANTUM_USD, rounding=ROUND_HALF_UP)
