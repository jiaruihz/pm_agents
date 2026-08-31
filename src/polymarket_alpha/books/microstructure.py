"""PIT-safe research features from one frozen paired Polymarket book.

This module is deliberately downstream of capture receipts and upstream of no
decision component.  It does not fetch books, rank markets, size capital, infer
queue position, or place orders.  Every record binds the exact paired snapshot,
the local availability receipt and a versioned feature policy.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..contracts import (
    AlphaContract,
    BookCaptureReceipt,
    BookCaptureStatus,
    BookLeg,
    BookLevel,
    CommonEnvelope,
    OrderbookSnapshot,
    stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256
from .executable_cost import ExecutableSweep, executable_sweep


BOOK_MICROSTRUCTURE_SOURCE = "polymarket_alpha.books.microstructure"
BOOK_MICROSTRUCTURE_VERSION = "book_microstructure_v1"
_ZERO = Decimal("0")
_ONE = Decimal("1")


class BookFeatureStatus(StrEnum):
    USABLE = "USABLE"
    QUARANTINED = "QUARANTINED"


class BookMicrostructurePolicy(CommonEnvelope):
    feature_policy_id: str
    top_levels: int = Field(gt=0)
    target_sizes: tuple[Decimal, ...]
    fee_rate: Decimal = Field(ge=0, le=1)
    slippage_buffer: Decimal = Field(ge=0, le=1)
    max_source_to_receipt_seconds: int = Field(gt=0)
    research_only: Literal[True] = True
    decision_use: Literal["PROHIBITED"] = "PROHIBITED"
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("target_sizes")
    @classmethod
    def targets_are_canonical(
        cls, value: tuple[Decimal, ...]
    ) -> tuple[Decimal, ...]:
        if not value or any(item <= _ZERO for item in value):
            raise ValueError("target_sizes must be non-empty and positive")
        if value != tuple(sorted(set(value))):
            raise ValueError("target_sizes must be unique and sorted")
        return value

    @model_validator(mode="after")
    def identity_is_bound(self) -> "BookMicrostructurePolicy":
        if self.feature_policy_id != self.record_id:
            raise ValueError("feature_policy_id must equal record_id")
        if not self.record_id.startswith("book_microstructure_policy:"):
            raise ValueError("feature policy uses the wrong namespace")
        return self


class BookLegMicrostructure(AlphaContract):
    token_id: str
    best_bid: Decimal | None = Field(default=None, ge=0, le=1)
    best_ask: Decimal | None = Field(default=None, ge=0, le=1)
    mid: Decimal | None = Field(default=None, ge=0, le=1)
    spread: Decimal | None = Field(default=None, ge=0, le=1)
    top_bid_size: Decimal | None = Field(default=None, gt=0)
    top_ask_size: Decimal | None = Field(default=None, gt=0)
    bid_depth_size: Decimal = Field(ge=0)
    ask_depth_size: Decimal = Field(ge=0)
    bid_depth_notional: Decimal = Field(ge=0)
    ask_depth_notional: Decimal = Field(ge=0)
    depth_imbalance: Decimal | None = Field(default=None, ge=-1, le=1)
    bid_size_hhi: Decimal | None = Field(default=None, ge=0, le=1)
    ask_size_hhi: Decimal | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def quote_fields_are_complete(self) -> "BookLegMicrostructure":
        quote = (self.best_bid, self.best_ask, self.mid, self.spread)
        if any(item is None for item in quote) and any(item is not None for item in quote):
            raise ValueError("quote fields must be all present or all absent")
        return self


class BookImpactPoint(AlphaContract):
    target_size: Decimal = Field(gt=0)
    fully_executable: bool
    yes_buy_effective_price: Decimal | None = None
    yes_sell_effective_price: Decimal | None = None
    no_buy_effective_price: Decimal | None = None
    no_sell_effective_price: Decimal | None = None
    yes_buy_impact: Decimal | None = None
    yes_sell_impact: Decimal | None = None
    no_buy_impact: Decimal | None = None
    no_sell_impact: Decimal | None = None
    paired_buy_cost: Decimal | None = None
    paired_buy_premium: Decimal | None = None
    paired_sell_proceeds: Decimal | None = None
    paired_sell_discount: Decimal | None = None

    @model_validator(mode="after")
    def optional_metrics_match_coverage(self) -> "BookImpactPoint":
        values = (
            self.yes_buy_effective_price,
            self.yes_sell_effective_price,
            self.no_buy_effective_price,
            self.no_sell_effective_price,
            self.yes_buy_impact,
            self.yes_sell_impact,
            self.no_buy_impact,
            self.no_sell_impact,
            self.paired_buy_cost,
            self.paired_buy_premium,
            self.paired_sell_proceeds,
            self.paired_sell_discount,
        )
        if self.fully_executable != all(item is not None for item in values):
            raise ValueError("impact values must be complete exactly when executable")
        return self


class BookMicrostructureFeatureRecord(CommonEnvelope):
    feature_id: str
    feature_policy_id: str
    feature_policy_sha256: str
    orderbook_snapshot_id: str
    orderbook_snapshot_sha256: str
    book_receipt_id: str
    book_receipt_sha256: str
    market_id: str
    capture_group_id: str
    available_at: datetime
    as_of: datetime
    status: BookFeatureStatus
    quarantine_reasons: tuple[str, ...]
    quality_flags: tuple[str, ...]
    source_to_receipt_seconds: Decimal = Field(ge=0)
    capture_to_receipt_seconds: Decimal = Field(ge=0)
    yes: BookLegMicrostructure
    no: BookLegMicrostructure
    paired_top_ask_premium: Decimal | None = None
    paired_top_bid_discount: Decimal | None = None
    impact_curve: tuple[BookImpactPoint, ...]
    insufficient_target_sizes: tuple[Decimal, ...]
    research_only: Literal[True] = True
    decision_use: Literal["PROHIBITED"] = "PROHIBITED"
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator(
        "feature_policy_sha256",
        "orderbook_snapshot_sha256",
        "book_receipt_sha256",
    )
    @classmethod
    def hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("available_at", "as_of")
    @classmethod
    def clocks_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("quarantine_reasons", "quality_flags")
    @classmethod
    def labels_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("feature labels cannot be blank")
        if normalized != tuple(sorted(set(normalized))):
            raise ValueError("feature labels must be unique and sorted")
        return normalized

    @model_validator(mode="after")
    def identity_and_status_are_bound(self) -> "BookMicrostructureFeatureRecord":
        if self.feature_id != self.record_id:
            raise ValueError("feature_id must equal record_id")
        if not self.record_id.startswith("book_microstructure_feature:"):
            raise ValueError("book feature uses the wrong namespace")
        if self.available_at > self.as_of:
            raise ValueError("feature cannot be available after as_of")
        if (self.status is BookFeatureStatus.QUARANTINED) != bool(
            self.quarantine_reasons
        ):
            raise ValueError("feature status must match quarantine reasons")
        targets = tuple(item.target_size for item in self.impact_curve)
        if targets != tuple(sorted(set(targets))):
            raise ValueError("impact curve targets must be unique and sorted")
        expected_insufficient = tuple(
            item.target_size for item in self.impact_curve if not item.fully_executable
        )
        if self.insufficient_target_sizes != expected_insufficient:
            raise ValueError("insufficient targets must match the impact curve")
        return self


def build_book_microstructure_policy(
    *,
    run_id: str,
    created_at: datetime,
    top_levels: int = 5,
    target_sizes: tuple[Decimal, ...] = (
        Decimal("1"),
        Decimal("5"),
        Decimal("10"),
    ),
    fee_rate: Decimal = _ZERO,
    slippage_buffer: Decimal = _ZERO,
    max_source_to_receipt_seconds: int = 30,
) -> BookMicrostructurePolicy:
    created = ensure_utc(created_at)
    identity = {
        "run_id": run_id,
        "created_at": created,
        "top_levels": top_levels,
        "target_sizes": target_sizes,
        "fee_rate": fee_rate,
        "slippage_buffer": slippage_buffer,
        "max_source_to_receipt_seconds": max_source_to_receipt_seconds,
        "version": BOOK_MICROSTRUCTURE_VERSION,
    }
    policy_id = stable_record_id("book_microstructure_policy", identity)
    return BookMicrostructurePolicy(
        record_id=policy_id,
        feature_policy_id=policy_id,
        run_id=run_id,
        created_at=created,
        source=BOOK_MICROSTRUCTURE_SOURCE,
        source_version=BOOK_MICROSTRUCTURE_VERSION,
        provenance=(),
        extensions={},
        top_levels=top_levels,
        target_sizes=target_sizes,
        fee_rate=fee_rate,
        slippage_buffer=slippage_buffer,
        max_source_to_receipt_seconds=max_source_to_receipt_seconds,
    )


def _seconds(delta: timedelta) -> Decimal:
    microseconds = (
        (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    )
    return Decimal(microseconds) / Decimal(1_000_000)


def _hhi(levels: tuple[BookLevel, ...]) -> Decimal | None:
    total = sum((item.size for item in levels), _ZERO)
    if total == _ZERO:
        return None
    return sum(((item.size / total) ** 2 for item in levels), _ZERO)


def _leg_features(leg: BookLeg, top_levels: int) -> BookLegMicrostructure:
    bids = leg.bids[:top_levels]
    asks = leg.asks[:top_levels]
    bid_size = sum((item.size for item in bids), _ZERO)
    ask_size = sum((item.size for item in asks), _ZERO)
    denominator = bid_size + ask_size
    quote: dict[str, Decimal | None]
    if bids and asks:
        best_bid, best_ask = bids[0].price, asks[0].price
        quote = {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": (best_bid + best_ask) / Decimal("2"),
            "spread": best_ask - best_bid,
        }
    else:
        quote = {"best_bid": None, "best_ask": None, "mid": None, "spread": None}
    return BookLegMicrostructure(
        token_id=leg.token_id,
        top_bid_size=None if not bids else bids[0].size,
        top_ask_size=None if not asks else asks[0].size,
        bid_depth_size=bid_size,
        ask_depth_size=ask_size,
        bid_depth_notional=sum((item.price * item.size for item in bids), _ZERO),
        ask_depth_notional=sum((item.price * item.size for item in asks), _ZERO),
        depth_imbalance=None
        if denominator == _ZERO
        else (bid_size - ask_size) / denominator,
        bid_size_hhi=_hhi(bids),
        ask_size_hhi=_hhi(asks),
        **quote,
    )


def _impact(
    *,
    target: Decimal,
    yes_leg: BookLeg,
    no_leg: BookLeg,
    yes: BookLegMicrostructure,
    no: BookLegMicrostructure,
    policy: BookMicrostructurePolicy,
) -> BookImpactPoint:
    sweeps = (
        executable_sweep(
            yes_leg.asks,
            target_size=target,
            side="BUY",
            fee_rate=policy.fee_rate,
            slippage_buffer=policy.slippage_buffer,
        ),
        executable_sweep(
            yes_leg.bids,
            target_size=target,
            side="SELL",
            fee_rate=policy.fee_rate,
            slippage_buffer=policy.slippage_buffer,
        ),
        executable_sweep(
            no_leg.asks,
            target_size=target,
            side="BUY",
            fee_rate=policy.fee_rate,
            slippage_buffer=policy.slippage_buffer,
        ),
        executable_sweep(
            no_leg.bids,
            target_size=target,
            side="SELL",
            fee_rate=policy.fee_rate,
            slippage_buffer=policy.slippage_buffer,
        ),
    )
    yes_buy, yes_sell, no_buy, no_sell = sweeps
    complete = (
        yes.best_bid is not None
        and yes.best_ask is not None
        and no.best_bid is not None
        and no.best_ask is not None
        and all(item.fully_executable for item in sweeps)
    )
    if not complete:
        return BookImpactPoint(target_size=target, fully_executable=False)
    return _complete_impact_point(
        target=target,
        yes_buy=yes_buy,
        yes_sell=yes_sell,
        no_buy=no_buy,
        no_sell=no_sell,
        yes=yes,
        no=no,
    )


def _complete_impact_point(
    *,
    target: Decimal,
    yes_buy: ExecutableSweep,
    yes_sell: ExecutableSweep,
    no_buy: ExecutableSweep,
    no_sell: ExecutableSweep,
    yes: BookLegMicrostructure,
    no: BookLegMicrostructure,
) -> BookImpactPoint:
    assert yes_buy.effective_price is not None
    assert yes_sell.effective_price is not None
    assert no_buy.effective_price is not None
    assert no_sell.effective_price is not None
    assert yes.best_bid is not None and yes.best_ask is not None
    assert no.best_bid is not None and no.best_ask is not None
    buy_cost = yes_buy.effective_price + no_buy.effective_price
    sell_proceeds = yes_sell.effective_price + no_sell.effective_price
    return BookImpactPoint(
        target_size=target,
        fully_executable=True,
        yes_buy_effective_price=yes_buy.effective_price,
        yes_sell_effective_price=yes_sell.effective_price,
        no_buy_effective_price=no_buy.effective_price,
        no_sell_effective_price=no_sell.effective_price,
        yes_buy_impact=yes_buy.effective_price - yes.best_ask,
        yes_sell_impact=yes.best_bid - yes_sell.effective_price,
        no_buy_impact=no_buy.effective_price - no.best_ask,
        no_sell_impact=no.best_bid - no_sell.effective_price,
        paired_buy_cost=buy_cost,
        paired_buy_premium=buy_cost - _ONE,
        paired_sell_proceeds=sell_proceeds,
        paired_sell_discount=_ONE - sell_proceeds,
    )


def extract_book_microstructure_features(
    *,
    snapshot: OrderbookSnapshot,
    receipt: BookCaptureReceipt,
    policy: BookMicrostructurePolicy,
    as_of: datetime,
) -> BookMicrostructureFeatureRecord:
    """Materialize one deterministic research-only PIT feature record."""

    now = ensure_utc(as_of)
    if policy.created_at > now:
        raise ValueError("future feature policy is not PIT-visible at as_of")
    if receipt.status is not BookCaptureStatus.ACCEPTED:
        raise ValueError("microstructure features require an ACCEPTED receipt")
    if (
        receipt.market_id != snapshot.identity.market_id
        or receipt.orderbook_snapshot_id != snapshot.record_id
        or receipt.orderbook_snapshot_sha256 != snapshot.canonical_sha256
        or receipt.capture_group_id != snapshot.capture_group_id
        or receipt.source_observed_at != snapshot.source_observed_at
    ):
        raise ValueError("receipt does not bind the exact paired snapshot")
    if snapshot.source_observed_at > snapshot.captured_at:
        raise ValueError("snapshot source clock cannot follow capture")
    if snapshot.captured_at > receipt.received_at:
        raise ValueError("receipt cannot precede paired capture")
    if receipt.received_at > now:
        raise ValueError("future receipt is not PIT-visible at as_of")
    if snapshot.created_at > receipt.received_at or receipt.created_at > now:
        raise ValueError("contract creation clocks are not PIT-visible")

    yes = _leg_features(snapshot.yes_leg, policy.top_levels)
    no = _leg_features(snapshot.no_leg, policy.top_levels)
    source_age = _seconds(receipt.received_at - snapshot.source_observed_at)
    capture_age = _seconds(receipt.received_at - snapshot.captured_at)
    reasons: set[str] = set()
    if snapshot.stale:
        reasons.add("BOOK_STALE")
    reasons.update(f"QUALITY_FLAG:{item}" for item in snapshot.quality_flags)
    if source_age > Decimal(policy.max_source_to_receipt_seconds):
        reasons.add("SOURCE_TO_RECEIPT_TTL_EXCEEDED")
    for label, features in (("YES", yes), ("NO", no)):
        if features.best_bid is None or features.best_ask is None:
            reasons.add(f"ONE_SIDED:{label}")
        elif features.best_bid >= features.best_ask:
            reasons.add(f"CROSSED_OR_LOCKED:{label}")

    curve = tuple(
        _impact(
            target=target,
            yes_leg=snapshot.yes_leg,
            no_leg=snapshot.no_leg,
            yes=yes,
            no=no,
            policy=policy,
        )
        for target in policy.target_sizes
    )
    feature_id = stable_record_id(
        "book_microstructure_feature",
        snapshot.record_id,
        snapshot.canonical_sha256,
        receipt.record_id,
        receipt.canonical_sha256,
        policy.record_id,
        policy.canonical_sha256,
    )
    paired_ask = (
        None
        if yes.best_ask is None or no.best_ask is None
        else yes.best_ask + no.best_ask - _ONE
    )
    paired_bid = (
        None
        if yes.best_bid is None or no.best_bid is None
        else _ONE - yes.best_bid - no.best_bid
    )
    return BookMicrostructureFeatureRecord(
        record_id=feature_id,
        feature_id=feature_id,
        run_id=policy.run_id,
        created_at=receipt.received_at,
        source=BOOK_MICROSTRUCTURE_SOURCE,
        source_version=BOOK_MICROSTRUCTURE_VERSION,
        provenance=(),
        extensions={},
        feature_policy_id=policy.record_id,
        feature_policy_sha256=policy.canonical_sha256,
        orderbook_snapshot_id=snapshot.record_id,
        orderbook_snapshot_sha256=snapshot.canonical_sha256,
        book_receipt_id=receipt.record_id,
        book_receipt_sha256=receipt.canonical_sha256,
        market_id=snapshot.identity.market_id,
        capture_group_id=snapshot.capture_group_id,
        available_at=receipt.received_at,
        as_of=receipt.received_at,
        status=BookFeatureStatus.QUARANTINED if reasons else BookFeatureStatus.USABLE,
        quarantine_reasons=tuple(sorted(reasons)),
        quality_flags=tuple(sorted(set(snapshot.quality_flags))),
        source_to_receipt_seconds=source_age,
        capture_to_receipt_seconds=capture_age,
        yes=yes,
        no=no,
        paired_top_ask_premium=paired_ask,
        paired_top_bid_discount=paired_bid,
        impact_curve=curve,
        insufficient_target_sizes=tuple(
            item.target_size for item in curve if not item.fully_executable
        ),
    )


__all__ = [
    "BOOK_MICROSTRUCTURE_SOURCE",
    "BOOK_MICROSTRUCTURE_VERSION",
    "BookFeatureStatus",
    "BookImpactPoint",
    "BookLegMicrostructure",
    "BookMicrostructureFeatureRecord",
    "BookMicrostructurePolicy",
    "build_book_microstructure_policy",
    "extract_book_microstructure_features",
]
