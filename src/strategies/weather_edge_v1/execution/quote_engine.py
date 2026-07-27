"""Pure, Decimal-safe quote construction from a supplied market-book snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Literal, Mapping

from .contracts import FeeSchedule, JsonContract, MarketBook, VenueCapabilities


QuoteStatus = Literal["quoted", "blocked", "deferred"]
VenueSide = Literal["BUY", "SELL"]
MakerPriceRule = Literal[
    "join_bid",
    "improve_bid",
    "one_tick_below_ask",
    "join_ask",
    "improve_ask",
    "one_tick_above_bid",
]


class QuoteInputError(ValueError):
    """Raised only for malformed quote inputs, never for an ordinary block."""


def _decimal(value: Decimal | str | int, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise QuoteInputError(f"{name} must be a Decimal, integer, or decimal string")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise QuoteInputError(f"invalid decimal for {name}: {value!r}") from exc


def _optional_decimal(value: Decimal | str | int | None, name: str) -> Decimal | None:
    return None if value is None else _decimal(value, name)


def _side(value: str) -> VenueSide:
    result = str(value).upper().strip()
    if result not in {"BUY", "SELL"}:
        raise QuoteInputError("venue_side must be BUY or SELL")
    return result  # type: ignore[return-value]


def round_price_to_tick(price: Decimal | str | int, tick_size: Decimal | str | int, *, venue_side: str) -> Decimal:
    """Normalize a passive price: BUY down, SELL up."""
    value = _decimal(price, "price")
    tick = _decimal(tick_size, "tick_size")
    if tick <= 0:
        raise QuoteInputError("tick_size must be positive")
    rounding = ROUND_FLOOR if _side(venue_side) == "BUY" else ROUND_CEILING
    return (value / tick).to_integral_value(rounding=rounding) * tick


def round_marketable_price_to_tick(
    price: Decimal | str | int,
    tick_size: Decimal | str | int,
    *,
    venue_side: str,
) -> Decimal:
    """Normalize a marketable limit: BUY up, SELL down, preserving executability."""
    value = _decimal(price, "price")
    tick = _decimal(tick_size, "tick_size")
    if tick <= 0:
        raise QuoteInputError("tick_size must be positive")
    rounding = ROUND_CEILING if _side(venue_side) == "BUY" else ROUND_FLOOR
    return (value / tick).to_integral_value(rounding=rounding) * tick


def _passive_rounding_mode(venue_side: VenueSide) -> str:
    return "floor_buy" if venue_side == "BUY" else "ceiling_sell"


def _marketable_rounding_mode(venue_side: VenueSide) -> str:
    return "ceiling_buy" if venue_side == "BUY" else "floor_sell"


def compose_price_cap(
    requested_price: Decimal | str | int,
    *,
    price_floor: Decimal | str | int | None = None,
    price_cap: Decimal | str | int | None = None,
    venue_min_price: Decimal | str | int = "0",
    venue_max_price: Decimal | str | int = "1",
) -> Decimal:
    """Compose strategy and venue bounds without inventing a replacement cap rule."""
    requested = _decimal(requested_price, "requested_price")
    lower = max(_decimal(venue_min_price, "venue_min_price"), _optional_decimal(price_floor, "price_floor") or Decimal("0"))
    upper = min(_decimal(venue_max_price, "venue_max_price"), _optional_decimal(price_cap, "price_cap") or Decimal("1"))
    if lower >= upper:
        raise QuoteInputError("composed price bounds are empty")
    return min(max(requested, lower), upper)


@dataclass(frozen=True)
class QuoteDecision(JsonContract):
    status: QuoteStatus
    reason: str
    venue_side: VenueSide
    maker_only: bool
    requested_shares: Decimal
    fillable_shares: Decimal
    blocked_remainder: Decimal
    expected_vwap: Decimal | None
    worst_price: Decimal | None
    requested_price_exact: Decimal | None
    normalized_price_exact: Decimal | None
    quote_mode: str
    book_fetched_at_utc: str | None
    book_venue_timestamp_utc: str | None
    book_age_sec: Decimal | None
    book_epoch_ref: str | None
    tick_size: Decimal | None
    tick_size_source: str | None
    rounding_mode: str | None
    fee_schedule_ref: str | None
    fee_model_version: str | None
    maker_rebate_program: str | None
    fee_estimate_input: Mapping[str, Any]
    capabilities_fetched_at_utc: str | None

    def __post_init__(self) -> None:
        for name in ("requested_shares", "fillable_shares", "blocked_remainder"):
            value = _decimal(getattr(self, name), name)
            if value < 0:
                raise QuoteInputError(f"{name} cannot be negative")
            object.__setattr__(self, name, value)
        for name in ("expected_vwap", "worst_price", "requested_price_exact", "normalized_price_exact", "book_age_sec", "tick_size"):
            object.__setattr__(self, name, _optional_decimal(getattr(self, name), name))
        object.__setattr__(self, "fee_estimate_input", MappingProxyType(dict(self.fee_estimate_input)))


def _fee_estimate_input(fee_schedule: FeeSchedule | None, *, maker_only: bool) -> Mapping[str, Any]:
    if fee_schedule is None:
        return MappingProxyType({})
    parameters = fee_schedule.maker_fee_parameters if maker_only else fee_schedule.taker_fee_parameters
    return MappingProxyType(
        {
            "fee_schedule_ref": fee_schedule.fee_schedule_ref,
            "fee_formula_id": fee_schedule.fee_formula_id,
            "fee_parameters": parameters,
            "maker_rebate_program": fee_schedule.maker_rebate_program,
        }
    )


def _decision(
    *,
    status: QuoteStatus,
    reason: str,
    venue_side: VenueSide,
    maker_only: bool,
    requested_shares: Decimal,
    fillable_shares: Decimal = Decimal("0"),
    blocked_remainder: Decimal | None = None,
    expected_vwap: Decimal | None = None,
    worst_price: Decimal | None = None,
    requested_price_exact: Decimal | None = None,
    normalized_price_exact: Decimal | None = None,
    quote_mode: str = "",
    market_book: MarketBook | None = None,
    book_age_sec: Decimal | None = None,
    fee_schedule: FeeSchedule | None = None,
    venue_capabilities: VenueCapabilities | None = None,
    rounding_mode: str | None = None,
) -> QuoteDecision:
    return QuoteDecision(
        status=status,
        reason=reason,
        venue_side=venue_side,
        maker_only=maker_only,
        requested_shares=requested_shares,
        fillable_shares=fillable_shares,
        blocked_remainder=requested_shares - fillable_shares if blocked_remainder is None else blocked_remainder,
        expected_vwap=expected_vwap,
        worst_price=worst_price,
        requested_price_exact=requested_price_exact,
        normalized_price_exact=normalized_price_exact,
        quote_mode=quote_mode,
        book_fetched_at_utc=None if market_book is None else market_book.fetched_at_utc,
        book_venue_timestamp_utc=None if market_book is None else market_book.venue_timestamp_utc,
        book_age_sec=book_age_sec,
        book_epoch_ref=None if market_book is None else market_book.book_epoch_ref,
        tick_size=None if market_book is None else market_book.tick_size,
        tick_size_source=None if market_book is None else market_book.tick_size_source,
        rounding_mode=(rounding_mode or _passive_rounding_mode(venue_side)) if market_book is not None else None,
        fee_schedule_ref=None if fee_schedule is None else fee_schedule.fee_schedule_ref,
        fee_model_version=None if fee_schedule is None else fee_schedule.fee_formula_id,
        maker_rebate_program=None if fee_schedule is None else fee_schedule.maker_rebate_program,
        fee_estimate_input=_fee_estimate_input(fee_schedule, maker_only=maker_only),
        capabilities_fetched_at_utc=None if venue_capabilities is None else venue_capabilities.capabilities_fetched_at_utc,
    )


def _quote_context(
    *,
    market_book: MarketBook,
    book_age_sec: Decimal | str | int | None,
    max_book_age_sec: Decimal | str | int | None,
    fee_schedule: FeeSchedule | None,
    venue_capabilities: VenueCapabilities | None,
    venue_side: VenueSide,
    maker_only: bool,
    requested_shares: Decimal,
    order_type: str,
    rounding_mode: str,
) -> tuple[Decimal, QuoteDecision | None]:
    age = _optional_decimal(book_age_sec, "book_age_sec")
    max_age = _optional_decimal(max_book_age_sec, "max_book_age_sec")
    if market_book.status.lower() != "ok":
        return Decimal("0"), _decision(status="blocked", reason="book_not_ok", venue_side=venue_side, maker_only=maker_only, requested_shares=requested_shares, market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    if age is None:
        return Decimal("0"), _decision(status="deferred", reason="book_age_unknown", venue_side=venue_side, maker_only=maker_only, requested_shares=requested_shares, market_book=market_book, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    if age < 0:
        raise QuoteInputError("book_age_sec cannot be negative")
    if max_age is not None and age > max_age:
        return age, _decision(status="blocked", reason="book_stale", venue_side=venue_side, maker_only=maker_only, requested_shares=requested_shares, market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    if fee_schedule is None or venue_capabilities is None:
        return age, _decision(status="deferred", reason="missing_fee_or_capability_snapshot", venue_side=venue_side, maker_only=maker_only, requested_shares=requested_shares, market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    if fee_schedule.fee_schedule_ref != venue_capabilities.fee_schedule_ref:
        return age, _decision(status="blocked", reason="fee_capability_provenance_mismatch", venue_side=venue_side, maker_only=maker_only, requested_shares=requested_shares, market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    if order_type not in venue_capabilities.supported_order_types:
        return age, _decision(status="blocked", reason="unsupported_order_type", venue_side=venue_side, maker_only=maker_only, requested_shares=requested_shares, market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    if maker_only and order_type not in venue_capabilities.post_only_order_types:
        return age, _decision(status="blocked", reason="unsupported_post_only_order_type", venue_side=venue_side, maker_only=maker_only, requested_shares=requested_shares, market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    if market_book.bids and market_book.asks and market_book.bids[0].price >= market_book.asks[0].price:
        return age, _decision(status="blocked", reason="book_locked_or_crossed", venue_side=venue_side, maker_only=maker_only, requested_shares=requested_shares, market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    return age, None


def quote_taker_top_of_book(
    *,
    market_book: MarketBook,
    venue_side: str,
    requested_shares: Decimal | str | int,
    book_age_sec: Decimal | str | int | None,
    max_book_age_sec: Decimal | str | int | None,
    fee_schedule: FeeSchedule | None,
    venue_capabilities: VenueCapabilities | None,
    price_floor: Decimal | str | int | None = None,
    price_cap: Decimal | str | int | None = None,
    venue_min_price: Decimal | str | int = "0",
    venue_max_price: Decimal | str | int = "1",
    order_type: str = "GTC",
) -> QuoteDecision:
    side = _side(venue_side)
    rounding_mode = _marketable_rounding_mode(side)
    shares = _decimal(requested_shares, "requested_shares")
    if shares <= 0:
        raise QuoteInputError("requested_shares must be positive")
    age, blocked = _quote_context(market_book=market_book, book_age_sec=book_age_sec, max_book_age_sec=max_book_age_sec, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, venue_side=side, maker_only=False, requested_shares=shares, order_type=order_type, rounding_mode=rounding_mode)
    if blocked is not None:
        return blocked
    levels = market_book.asks if side == "BUY" else market_book.bids
    if not levels:
        return _decision(status="blocked", reason="missing_top_of_book", venue_side=side, maker_only=False, requested_shares=shares, quote_mode="taker_top_of_book", market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    requested = levels[0].price
    composed = compose_price_cap(requested, price_floor=price_floor, price_cap=price_cap, venue_min_price=venue_min_price, venue_max_price=venue_max_price)
    lower = max(_decimal(venue_min_price, "venue_min_price"), _optional_decimal(price_floor, "price_floor") or Decimal("0"))
    upper = min(_decimal(venue_max_price, "venue_max_price"), _optional_decimal(price_cap, "price_cap") or Decimal("1"))
    normalized = round_marketable_price_to_tick(composed, market_book.tick_size, venue_side=side)
    permitted = normalized >= requested if side == "BUY" else normalized <= requested
    exceeds_upper = side == "BUY" and normalized > upper
    falls_below_lower = side == "SELL" and normalized < lower
    if exceeds_upper or falls_below_lower:
        return _decision(status="blocked", reason="marketable_normalization_outside_price_bound", venue_side=side, maker_only=False, requested_shares=shares, requested_price_exact=requested, normalized_price_exact=normalized, quote_mode="taker_top_of_book", market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    if not permitted or normalized <= _decimal(venue_min_price, "venue_min_price") or normalized >= _decimal(venue_max_price, "venue_max_price"):
        return _decision(status="blocked", reason="top_of_book_outside_price_bounds", venue_side=side, maker_only=False, requested_shares=shares, requested_price_exact=requested, normalized_price_exact=normalized, quote_mode="taker_top_of_book", market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    fillable = min(shares, levels[0].size)
    return _decision(status="quoted" if fillable == shares else "blocked", reason="" if fillable == shares else "insufficient_top_of_book_depth", venue_side=side, maker_only=False, requested_shares=shares, fillable_shares=fillable, expected_vwap=requested, worst_price=requested, requested_price_exact=requested, normalized_price_exact=normalized, quote_mode="taker_top_of_book", market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)


def quote_marketable_limit_exact_shares(
    *,
    market_book: MarketBook,
    venue_side: str,
    requested_shares: Decimal | str | int,
    book_age_sec: Decimal | str | int | None,
    max_book_age_sec: Decimal | str | int | None,
    fee_schedule: FeeSchedule | None,
    venue_capabilities: VenueCapabilities | None,
    price_floor: Decimal | str | int | None = None,
    price_cap: Decimal | str | int | None = None,
    venue_min_price: Decimal | str | int = "0",
    venue_max_price: Decimal | str | int = "1",
    order_type: str = "GTC",
) -> QuoteDecision:
    side = _side(venue_side)
    rounding_mode = _marketable_rounding_mode(side)
    shares = _decimal(requested_shares, "requested_shares")
    if shares <= 0:
        raise QuoteInputError("requested_shares must be positive")
    age, blocked = _quote_context(market_book=market_book, book_age_sec=book_age_sec, max_book_age_sec=max_book_age_sec, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, venue_side=side, maker_only=False, requested_shares=shares, order_type=order_type, rounding_mode=rounding_mode)
    if blocked is not None:
        return blocked
    levels = market_book.asks if side == "BUY" else market_book.bids
    if not levels:
        return _decision(status="blocked", reason="missing_market_depth", venue_side=side, maker_only=False, requested_shares=shares, quote_mode="marketable_limit_exact_shares", market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    upper = min(_decimal(venue_max_price, "venue_max_price"), _optional_decimal(price_cap, "price_cap") or Decimal("1"))
    lower = max(_decimal(venue_min_price, "venue_min_price"), _optional_decimal(price_floor, "price_floor") or Decimal("0"))
    remaining = shares
    fillable = Decimal("0")
    notional = Decimal("0")
    worst: Decimal | None = None
    for level in levels:
        permitted = level.price <= upper if side == "BUY" else level.price >= lower
        if not permitted:
            break
        take = min(remaining, level.size)
        fillable += take
        notional += take * level.price
        remaining -= take
        worst = level.price
        if remaining == 0:
            break
    if fillable == 0 or worst is None:
        return _decision(status="blocked", reason="price_cap_blocks_market_depth", venue_side=side, maker_only=False, requested_shares=shares, quote_mode="marketable_limit_exact_shares", market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    normalized = round_marketable_price_to_tick(worst, market_book.tick_size, venue_side=side)
    exceeds_upper = side == "BUY" and normalized > upper
    falls_below_lower = side == "SELL" and normalized < lower
    if exceeds_upper or falls_below_lower:
        return _decision(status="blocked", reason="marketable_normalization_outside_price_bound", venue_side=side, maker_only=False, requested_shares=shares, fillable_shares=fillable, blocked_remainder=remaining, expected_vwap=notional / fillable, worst_price=worst, requested_price_exact=worst, normalized_price_exact=normalized, quote_mode="marketable_limit_exact_shares", market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    return _decision(status="quoted" if remaining == 0 else "blocked", reason="" if remaining == 0 else "insufficient_full_depth", venue_side=side, maker_only=False, requested_shares=shares, fillable_shares=fillable, blocked_remainder=remaining, expected_vwap=notional / fillable, worst_price=worst, requested_price_exact=worst, normalized_price_exact=normalized, quote_mode="marketable_limit_exact_shares", market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)


def quote_maker(
    *,
    market_book: MarketBook,
    venue_side: str,
    requested_shares: Decimal | str | int,
    price_rule: MakerPriceRule,
    book_age_sec: Decimal | str | int | None,
    max_book_age_sec: Decimal | str | int | None,
    fee_schedule: FeeSchedule | None,
    venue_capabilities: VenueCapabilities | None,
    price_floor: Decimal | str | int | None = None,
    price_cap: Decimal | str | int | None = None,
    improvement_ticks: int = 1,
    venue_min_price: Decimal | str | int = "0",
    venue_max_price: Decimal | str | int = "1",
    order_type: str = "GTC",
) -> QuoteDecision:
    side = _side(venue_side)
    rounding_mode = _passive_rounding_mode(side)
    shares = _decimal(requested_shares, "requested_shares")
    if shares <= 0:
        raise QuoteInputError("requested_shares must be positive")
    if improvement_ticks < 1:
        raise QuoteInputError("improvement_ticks must be at least one")
    age, blocked = _quote_context(market_book=market_book, book_age_sec=book_age_sec, max_book_age_sec=max_book_age_sec, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, venue_side=side, maker_only=True, requested_shares=shares, order_type=order_type, rounding_mode=rounding_mode)
    if blocked is not None:
        return blocked
    if not market_book.bids or not market_book.asks:
        return _decision(status="blocked", reason="missing_two_sided_book", venue_side=side, maker_only=True, requested_shares=shares, quote_mode=price_rule, market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    bid, ask, tick = market_book.bids[0].price, market_book.asks[0].price, market_book.tick_size
    raw_rules: Mapping[tuple[VenueSide, MakerPriceRule], Decimal] = {
        ("BUY", "join_bid"): bid,
        ("BUY", "improve_bid"): bid + tick * improvement_ticks,
        ("BUY", "one_tick_below_ask"): ask - tick * improvement_ticks,
        ("SELL", "join_ask"): ask,
        ("SELL", "improve_ask"): ask - tick * improvement_ticks,
        ("SELL", "one_tick_above_bid"): bid + tick * improvement_ticks,
    }
    raw = raw_rules.get((side, price_rule))
    if raw is None:
        return _decision(status="blocked", reason="maker_price_rule_incompatible_with_side", venue_side=side, maker_only=True, requested_shares=shares, quote_mode=price_rule, market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    composed = compose_price_cap(raw, price_floor=price_floor, price_cap=price_cap, venue_min_price=venue_min_price, venue_max_price=venue_max_price)
    normalized = round_price_to_tick(composed, tick, venue_side=side)
    crosses = normalized >= ask if side == "BUY" else normalized <= bid
    outside = normalized <= _decimal(venue_min_price, "venue_min_price") or normalized >= _decimal(venue_max_price, "venue_max_price")
    if crosses:
        return _decision(status="blocked", reason="maker_would_cross", venue_side=side, maker_only=True, requested_shares=shares, requested_price_exact=raw, normalized_price_exact=normalized, quote_mode=price_rule, market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    if outside:
        return _decision(status="blocked", reason="maker_price_outside_bounds", venue_side=side, maker_only=True, requested_shares=shares, requested_price_exact=raw, normalized_price_exact=normalized, quote_mode=price_rule, market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
    return _decision(status="quoted", reason="", venue_side=side, maker_only=True, requested_shares=shares, fillable_shares=shares, expected_vwap=normalized, worst_price=normalized, requested_price_exact=raw, normalized_price_exact=normalized, quote_mode=price_rule, market_book=market_book, book_age_sec=age, fee_schedule=fee_schedule, venue_capabilities=venue_capabilities, rounding_mode=rounding_mode)
