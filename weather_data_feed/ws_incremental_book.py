"""Deterministic Polymarket WebSocket order-book reconstruction.

Raw WS frames remain the transport truth.  This module turns one subscription
epoch into an in-memory book only after an explicit ``book`` baseline.  Deltas
from another epoch, or deltas received before a baseline, never inherit state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping


RECONSTRUCTION_SCHEMA_VERSION = "weather_ws_reconstructed_book_v1"


class BookReconstructionError(RuntimeError):
    """The raw WS stream cannot support an executable reconstructed book."""


@dataclass(frozen=True)
class ReconstructedBook:
    subscription_epoch_id: str
    token_id: str
    observed_at_utc: str
    exchange_ts_ms: int | None
    baseline_received_at_utc: str
    last_frame_received_at_utc: str
    best_bid: float | None
    best_ask: float | None
    best_bid_size: float
    best_ask_size: float
    sell_proceeds: float | None
    buy_cost: float | None
    requested_shares: float
    depth_status: str
    snapshot_id: str
    schema_version: str = RECONSTRUCTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _TokenBook:
    bids: dict[float, float]
    asks: dict[float, float]
    baseline_received_at_utc: str
    last_frame_received_at_utc: str
    exchange_ts_ms: int | None


def _levels(raw: Iterable[Mapping[str, Any]]) -> dict[float, float]:
    output: dict[float, float] = {}
    for row in raw:
        price = float(row["price"])
        size = float(row["size"])
        if size > 0:
            output[price] = size
    return output


def _sweep(levels: Mapping[float, float], shares: float, *, reverse: bool) -> float | None:
    remaining = float(shares)
    value = 0.0
    for price in sorted(levels, reverse=reverse):
        take = min(remaining, float(levels[price]))
        value += take * float(price)
        remaining -= take
        if remaining <= 1e-12:
            return value
    return None


class IncrementalBookReconstructor:
    """Reconstruct books inside one explicitly declared subscription epoch."""

    def __init__(self, *, strict_best_parity: bool = True) -> None:
        self.strict_best_parity = strict_best_parity
        self.epoch_id: str | None = None
        self.token_ids: frozenset[str] = frozenset()
        self.books: dict[str, _TokenBook] = {}
        self.blocked_tokens: dict[str, str] = {}

    def activate_epoch(self, epoch_id: str, token_ids: Iterable[str]) -> None:
        self.epoch_id = str(epoch_id)
        self.token_ids = frozenset(str(value) for value in token_ids)
        self.books = {}
        self.blocked_tokens = {}

    def apply_envelope(self, envelope: Mapping[str, Any]) -> tuple[str, ...]:
        epoch_id = str(envelope.get("subscription_epoch_id") or "")
        if not self.epoch_id or epoch_id != self.epoch_id:
            raise BookReconstructionError(
                f"subscription epoch mismatch: active={self.epoch_id!r} frame={epoch_id!r}"
            )
        received = str(envelope.get("received_at_utc") or "")
        if not received:
            raise BookReconstructionError("WS envelope is missing received_at_utc")
        payload = envelope.get("message")
        messages = payload if isinstance(payload, list) else [payload]
        updated: list[str] = []
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            event_type = str(message.get("event_type") or message.get("type") or "")
            if event_type == "book":
                token_id = str(message.get("asset_id") or "")
                if token_id not in self.token_ids:
                    continue
                self.books[token_id] = _TokenBook(
                    bids=_levels(message.get("bids") or ()),
                    asks=_levels(message.get("asks") or ()),
                    baseline_received_at_utc=received,
                    last_frame_received_at_utc=received,
                    exchange_ts_ms=_int_or_none(message.get("timestamp")),
                )
                self.blocked_tokens.pop(token_id, None)
                updated.append(token_id)
            elif event_type == "price_change":
                parity_rows: dict[str, Mapping[str, Any]] = {}
                for change in message.get("price_changes") or ():
                    token_id = str(change.get("asset_id") or "")
                    if token_id not in self.token_ids:
                        continue
                    book = self.books.get(token_id)
                    if book is None:
                        self.blocked_tokens[token_id] = "delta_before_book_baseline"
                        continue
                    side = str(change.get("side") or "").upper()
                    levels = book.bids if side == "BUY" else book.asks if side == "SELL" else None
                    if levels is None:
                        raise BookReconstructionError(f"unsupported price_change side={side!r}")
                    price = float(change["price"])
                    size = float(change["size"])
                    if size > 0:
                        levels[price] = size
                    else:
                        levels.pop(price, None)
                    book.last_frame_received_at_utc = received
                    book.exchange_ts_ms = _int_or_none(message.get("timestamp"))
                    # A single WS message can mutate several levels of the same
                    # token.  Reported best_bid/best_ask describe the final
                    # message state, so parity is checked only after all of its
                    # level mutations have been applied.
                    parity_rows[token_id] = change
                    updated.append(token_id)
                for token_id, change in parity_rows.items():
                    self._verify_best(token_id, change)
        return tuple(dict.fromkeys(updated))

    def _verify_best(self, token_id: str, change: Mapping[str, Any]) -> None:
        if not self.strict_best_parity:
            return
        book = self.books[token_id]
        reconstructed_bid = max(book.bids, default=None)
        reconstructed_ask = min(book.asks, default=None)
        reported_bid = _float_or_none(change.get("best_bid"))
        reported_ask = _float_or_none(change.get("best_ask"))
        # CLOB deltas use 0/1 as empty-side sentinels.  They are not executable
        # price levels and must compare equal to an empty reconstructed side.
        if reported_bid == 0.0:
            reported_bid = None
        if reported_ask == 1.0:
            reported_ask = None
        if not _same_price(reconstructed_bid, reported_bid) or not _same_price(
            reconstructed_ask, reported_ask
        ):
            self.books.pop(token_id, None)
            self.blocked_tokens[token_id] = "best_quote_parity_mismatch"
            raise BookReconstructionError(
                "reconstructed best quote disagrees with WS delta: "
                f"token={token_id} reconstructed=({reconstructed_bid},{reconstructed_ask}) "
                f"reported=({reported_bid},{reported_ask})"
            )

    def snapshot(
        self, token_id: str, *, observed_at_utc: str, requested_shares: float = 5.0
    ) -> ReconstructedBook:
        token_id = str(token_id)
        if requested_shares <= 0:
            raise ValueError("requested_shares must be positive")
        book = self.books.get(token_id)
        if book is None:
            reason = self.blocked_tokens.get(token_id, "book_baseline_missing")
            raise BookReconstructionError(f"token={token_id} is not reconstructable: {reason}")
        best_bid = max(book.bids, default=None)
        best_ask = min(book.asks, default=None)
        sell_proceeds = _sweep(book.bids, requested_shares, reverse=True)
        buy_cost = _sweep(book.asks, requested_shares, reverse=False)
        state = {
            "schema_version": RECONSTRUCTION_SCHEMA_VERSION,
            "subscription_epoch_id": self.epoch_id,
            "token_id": token_id,
            "observed_at_utc": observed_at_utc,
            "baseline_received_at_utc": book.baseline_received_at_utc,
            "last_frame_received_at_utc": book.last_frame_received_at_utc,
            "bids": sorted(book.bids.items(), reverse=True),
            "asks": sorted(book.asks.items()),
        }
        snapshot_id = hashlib.sha256(
            json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ReconstructedBook(
            subscription_epoch_id=str(self.epoch_id),
            token_id=token_id,
            observed_at_utc=observed_at_utc,
            exchange_ts_ms=book.exchange_ts_ms,
            baseline_received_at_utc=book.baseline_received_at_utc,
            last_frame_received_at_utc=book.last_frame_received_at_utc,
            best_bid=best_bid,
            best_ask=best_ask,
            best_bid_size=float(book.bids.get(best_bid, 0.0)) if best_bid is not None else 0.0,
            best_ask_size=float(book.asks.get(best_ask, 0.0)) if best_ask is not None else 0.0,
            sell_proceeds=sell_proceeds,
            buy_cost=buy_cost,
            requested_shares=float(requested_shares),
            depth_status=(
                "five_share_executable"
                if buy_cost is not None and sell_proceeds is not None
                else "insufficient_depth"
            ),
            snapshot_id=snapshot_id,
        )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _same_price(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return abs(left - right) <= 1e-9
