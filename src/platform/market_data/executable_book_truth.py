"""Fee-aware executable book truth built from verified reconstructed books.

This module is deliberately downstream of :mod:`ws_incremental_book`.  It does
not repair transport gaps, synthesize queue position, or use REST to mutate a
WebSocket state.  Its only jobs are to expose explicit validity, calculate
two-sided executable sweeps, and align already-verified states to event clocks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

from .ws_incremental_book import ReconstructedBook
from weather_clock_contract import parse_utc


EXECUTABLE_BOOK_TRUTH_SCHEMA_VERSION = "weather_executable_book_truth_v1"
EVENT_ALIGNED_BOOK_SCHEMA_VERSION = "weather_event_aligned_book_v1"
WEATHER_TAKER_FEE_RATE = 0.05
STANDARD_SWEEP_SHARES = (1.0, 5.0, 10.0)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _parse_utc(value: str) -> datetime:
    parsed = parse_utc(value, field="executable_book_timestamp")
    assert parsed is not None
    return parsed


def weather_taker_fee(*, shares: float, price: float) -> float:
    """Official Weather taker fee model applied to one fill level."""

    if not math.isfinite(shares) or not math.isfinite(price):
        raise ValueError("shares and price must be finite")
    if shares < 0 or not 0.0 <= price <= 1.0:
        raise ValueError("invalid shares or price")
    return float(shares) * WEATHER_TAKER_FEE_RATE * float(price) * (1.0 - float(price))


@dataclass(frozen=True)
class ExecutableSweep:
    shares: float
    side: str
    gross_value_usd: float | None
    taker_fee_usd: float | None
    effective_value_usd: float | None
    average_price: float | None
    fully_executable: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutableBookTruth:
    book_snapshot_id: str | None
    subscription_epoch_id: str | None
    token_id: str
    source: str
    as_of_utc: str | None
    received_at_utc: str | None
    exchange_ts_ms: int | None
    book_valid: bool
    gap_reason: str | None
    queue_truth: bool
    maker_fill_proxy_only: bool
    sequence_status: str | None
    gap_detection_status: str | None
    sweeps: tuple[ExecutableSweep, ...]
    truth_id: str
    schema_version: str = EXECUTABLE_BOOK_TRUTH_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "sweeps": [row.to_dict() for row in self.sweeps]}


@dataclass(frozen=True)
class EventAlignedBook:
    event_id: str
    checkpoint: str
    checkpoint_at_utc: str
    token_id: str
    book_truth_id: str | None
    book_snapshot_id: str | None
    book_valid: bool
    gap_reason: str | None
    age_seconds: float | None
    queue_truth: bool
    alignment_id: str
    schema_version: str = EVENT_ALIGNED_BOOK_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sweep(
    levels: Sequence[tuple[float, float]], *, shares: float, side: str
) -> ExecutableSweep:
    if shares <= 0:
        raise ValueError("shares must be positive")
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    remaining = float(shares)
    gross = 0.0
    fee = 0.0
    for price, available in levels:
        if (
            not math.isfinite(float(price))
            or not math.isfinite(float(available))
            or not 0.0 <= float(price) <= 1.0
            or float(available) < 0.0
        ):
            raise ValueError("book levels require finite price in [0,1] and non-negative size")
        take = min(remaining, float(available))
        if take <= 0:
            continue
        gross += take * float(price)
        fee += weather_taker_fee(shares=take, price=float(price))
        remaining -= take
        if remaining <= 1e-12:
            effective = gross + fee if side == "buy" else gross - fee
            return ExecutableSweep(
                shares=float(shares),
                side=side,
                gross_value_usd=gross,
                taker_fee_usd=fee,
                effective_value_usd=effective,
                average_price=gross / float(shares),
                fully_executable=True,
            )
    return ExecutableSweep(
        shares=float(shares),
        side=side,
        gross_value_usd=None,
        taker_fee_usd=None,
        effective_value_usd=None,
        average_price=None,
        fully_executable=False,
    )


def executable_book_truth(
    book: ReconstructedBook,
    *,
    shares: Iterable[float] = STANDARD_SWEEP_SHARES,
) -> ExecutableBookTruth:
    """Convert one verified reconstruction into fee-aware two-sided truth."""

    quantities = tuple(float(value) for value in shares)
    if (
        not quantities
        or any(not math.isfinite(value) or value <= 0 for value in quantities)
        or len(set(quantities)) != len(quantities)
    ):
        raise ValueError("shares must be unique, finite, positive quantities")
    sweeps: list[ExecutableSweep] = []
    bids = tuple(sorted(book.bids, reverse=True))
    asks = tuple(sorted(book.asks))
    for quantity in quantities:
        sweeps.append(_sweep(asks, shares=float(quantity), side="buy"))
        sweeps.append(_sweep(bids, shares=float(quantity), side="sell"))
    basis = {
        "book_snapshot_id": book.book_snapshot_id,
        "sweeps": [row.to_dict() for row in sweeps],
        "queue_truth": False,
    }
    return ExecutableBookTruth(
        book_snapshot_id=book.book_snapshot_id,
        subscription_epoch_id=book.subscription_epoch_id,
        token_id=book.token_id,
        source="polymarket_ws_incremental_reconstruction",
        as_of_utc=book.observed_at_utc,
        received_at_utc=book.last_frame_received_at_utc,
        exchange_ts_ms=book.exchange_ts_ms,
        book_valid=True,
        gap_reason=None,
        queue_truth=False,
        maker_fill_proxy_only=True,
        sequence_status=book.sequence_status,
        gap_detection_status=book.gap_detection_status,
        sweeps=tuple(sweeps),
        truth_id=_hash(basis),
    )


def invalid_book_truth(
    *, token_id: str, gap_reason: str, as_of_utc: str | None = None
) -> ExecutableBookTruth:
    """Represent an unavailable book explicitly; never backfill a gap."""

    if not gap_reason:
        raise ValueError("gap_reason is required for an invalid book")
    basis = {
        "token_id": str(token_id),
        "as_of_utc": as_of_utc,
        "gap_reason": gap_reason,
        "book_valid": False,
    }
    return ExecutableBookTruth(
        book_snapshot_id=None,
        subscription_epoch_id=None,
        token_id=str(token_id),
        source="polymarket_ws_incremental_reconstruction",
        as_of_utc=as_of_utc,
        received_at_utc=None,
        exchange_ts_ms=None,
        book_valid=False,
        gap_reason=str(gap_reason),
        queue_truth=False,
        maker_fill_proxy_only=True,
        sequence_status=None,
        gap_detection_status=None,
        sweeps=(),
        truth_id=_hash(basis),
    )


def align_book_checkpoints(
    truths: Iterable[ExecutableBookTruth],
    events: Iterable[Mapping[str, Any]],
    *,
    max_age_seconds: float = 120.0,
) -> tuple[EventAlignedBook, ...]:
    """Align each event checkpoint to the latest valid state known at that time.

    Event mappings require ``event_id``, ``token_id`` and ``checkpoints`` where
    checkpoints maps a stable checkpoint name to a timezone-aware UTC string.
    A stale or absent state is emitted as an invalid row, not silently dropped.
    """

    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be non-negative")
    by_token: dict[str, list[tuple[datetime, ExecutableBookTruth]]] = {}
    for truth in truths:
        if not truth.book_valid or truth.as_of_utc is None:
            continue
        by_token.setdefault(truth.token_id, []).append((_parse_utc(truth.as_of_utc), truth))
    for rows in by_token.values():
        rows.sort(key=lambda item: (item[0], item[1].truth_id))

    output: list[EventAlignedBook] = []
    for event in events:
        event_id = str(event["event_id"])
        token_id = str(event["token_id"])
        checkpoints = event.get("checkpoints")
        if not isinstance(checkpoints, Mapping):
            raise ValueError("event checkpoints must be a mapping")
        candidates = by_token.get(token_id, ())
        for checkpoint, raw_at in checkpoints.items():
            at = _parse_utc(str(raw_at))
            eligible = [item for item in candidates if item[0] <= at]
            selected = eligible[-1] if eligible else None
            age = (at - selected[0]).total_seconds() if selected else None
            valid = bool(selected is not None and age is not None and age <= max_age_seconds)
            gap_reason = None if valid else "book_state_stale" if selected else "book_state_missing"
            truth = selected[1] if valid and selected else None
            basis = {
                "event_id": event_id,
                "checkpoint": str(checkpoint),
                "checkpoint_at_utc": at.isoformat(),
                "book_truth_id": truth.truth_id if truth else None,
                "gap_reason": gap_reason,
            }
            output.append(
                EventAlignedBook(
                    event_id=event_id,
                    checkpoint=str(checkpoint),
                    checkpoint_at_utc=at.isoformat(),
                    token_id=token_id,
                    book_truth_id=truth.truth_id if truth else None,
                    book_snapshot_id=truth.book_snapshot_id if truth else None,
                    book_valid=valid,
                    gap_reason=gap_reason,
                    age_seconds=age,
                    queue_truth=False,
                    alignment_id=_hash(basis),
                )
            )
    return tuple(output)
