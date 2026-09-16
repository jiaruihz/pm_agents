"""Deterministic fee-aware executable cost curves for frozen CLOB books.

The functions in this module are pure and Decimal-only.  They never fetch a
book, infer queue position, or place an order.  Callers must supply a frozen,
quality-checked :class:`OrderbookSnapshot` and an explicit fee policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Sequence

from ..contracts import BookLevel, OrderbookSnapshot


EXECUTABLE_COST_VERSION = "polymarket_taker_p_one_minus_p_v1"
_ONE = Decimal("1")
_ZERO = Decimal("0")


class ExecutableCostError(ValueError):
    """Raised when a requested curve cannot be computed deterministically."""


@dataclass(frozen=True, slots=True)
class ExecutableSweep:
    """One target-size point on a frozen executable cost curve."""

    side: Literal["BUY", "SELL"]
    target_size: Decimal
    fills: tuple[BookLevel, ...]
    fully_executable: bool
    gross_value: Decimal | None
    taker_fee: Decimal | None
    slippage_cost: Decimal | None
    effective_value: Decimal | None
    vwap: Decimal | None
    effective_price: Decimal | None
    fee_model_version: str = EXECUTABLE_COST_VERSION


@dataclass(frozen=True, slots=True)
class PairedBuyCost:
    """Synchronized YES/NO buy costs at one target size."""

    orderbook_snapshot_id: str
    orderbook_snapshot_sha256: str
    target_size: Decimal
    fee_rate: Decimal
    slippage_buffer: Decimal
    yes: ExecutableSweep
    no: ExecutableSweep

    @property
    def fully_executable(self) -> bool:
        return self.yes.fully_executable and self.no.fully_executable


def _require_decimal(
    value: Decimal, *, field: str, minimum: Decimal, maximum: Decimal | None = None
) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ExecutableCostError(f"{field} must be a finite Decimal")
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise ExecutableCostError(f"{field} must be >= {minimum}{suffix}")
    return value


def polymarket_taker_fee(
    *, shares: Decimal, price: Decimal, fee_rate: Decimal
) -> Decimal:
    """Return the protocol fee formula for one aggregate price level.

    The theoretical Decimal amount is preserved without inventing exchange
    fill-level rounding.  Actual-fill reconciliation remains authoritative for
    booked fees.
    """

    quantity = _require_decimal(shares, field="shares", minimum=_ZERO)
    probability = _require_decimal(
        price, field="price", minimum=_ZERO, maximum=_ONE
    )
    rate = _require_decimal(
        fee_rate, field="fee_rate", minimum=_ZERO, maximum=_ONE
    )
    return quantity * rate * probability * (_ONE - probability)


def executable_sweep(
    levels: Sequence[BookLevel],
    *,
    target_size: Decimal,
    side: Literal["BUY", "SELL"],
    fee_rate: Decimal,
    slippage_buffer: Decimal = _ZERO,
) -> ExecutableSweep:
    """Sweep one side of a frozen book and return an all-in target-size point."""

    target = _require_decimal(
        target_size, field="target_size", minimum=Decimal("0.000000000000000001")
    )
    rate = _require_decimal(
        fee_rate, field="fee_rate", minimum=_ZERO, maximum=_ONE
    )
    buffer = _require_decimal(
        slippage_buffer, field="slippage_buffer", minimum=_ZERO, maximum=_ONE
    )
    if side not in {"BUY", "SELL"}:
        raise ExecutableCostError("side must be BUY or SELL")
    rows = tuple(levels)
    prices = tuple(item.price for item in rows)
    expected = tuple(sorted(prices, reverse=side == "SELL"))
    if prices != expected:
        raise ExecutableCostError(
            "BUY levels must be ascending and SELL levels descending"
        )

    remaining = target
    gross = _ZERO
    fee = _ZERO
    fills: list[BookLevel] = []
    for level in rows:
        take = min(remaining, level.size)
        if take <= _ZERO:
            continue
        fills.append(BookLevel(price=level.price, size=take))
        gross += take * level.price
        fee += polymarket_taker_fee(
            shares=take, price=level.price, fee_rate=rate
        )
        remaining -= take
        if remaining == _ZERO:
            slippage = target * buffer
            effective = (
                gross + fee + slippage
                if side == "BUY"
                else gross - fee - slippage
            )
            return ExecutableSweep(
                side=side,
                target_size=target,
                fills=tuple(fills),
                fully_executable=True,
                gross_value=gross,
                taker_fee=fee,
                slippage_cost=slippage,
                effective_value=effective,
                vwap=gross / target,
                effective_price=effective / target,
            )

    return ExecutableSweep(
        side=side,
        target_size=target,
        fills=tuple(fills),
        fully_executable=False,
        gross_value=None,
        taker_fee=None,
        slippage_cost=None,
        effective_value=None,
        vwap=None,
        effective_price=None,
    )


def paired_buy_cost(
    book: OrderbookSnapshot,
    *,
    target_size: Decimal,
    fee_rate: Decimal,
    slippage_buffer: Decimal = _ZERO,
) -> PairedBuyCost:
    """Build synchronized all-in YES and NO buy curves from one snapshot."""

    yes = executable_sweep(
        book.yes_leg.asks,
        target_size=target_size,
        side="BUY",
        fee_rate=fee_rate,
        slippage_buffer=slippage_buffer,
    )
    no = executable_sweep(
        book.no_leg.asks,
        target_size=target_size,
        side="BUY",
        fee_rate=fee_rate,
        slippage_buffer=slippage_buffer,
    )
    return PairedBuyCost(
        orderbook_snapshot_id=book.record_id,
        orderbook_snapshot_sha256=book.canonical_sha256,
        target_size=target_size,
        fee_rate=fee_rate,
        slippage_buffer=slippage_buffer,
        yes=yes,
        no=no,
    )


__all__ = [
    "EXECUTABLE_COST_VERSION",
    "ExecutableCostError",
    "ExecutableSweep",
    "PairedBuyCost",
    "executable_sweep",
    "paired_buy_cost",
    "polymarket_taker_fee",
]
