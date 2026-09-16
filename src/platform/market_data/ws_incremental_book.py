"""Canonical deterministic reconstruction of Polymarket WebSocket books.

Raw WebSocket frames and subscription manifests remain the transport truth.
This module turns them into immutable token-level book states with enough
lineage to answer exactly which baseline and delta chain a model observed.

The contract fails closed on reconnects without a fresh ``book`` baseline,
receipt/exchange-clock regressions, detectable sequence gaps, and best-quote
parity failures.  REST books are never used to fill WebSocket gaps; they are an
independent parity observation only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from weather_clock_contract import parse_utc


# These are wire-format versions, not ownership declarations.  They remain
# unchanged during the platform extraction so old raw frames replay to the
# same identities.  New wire schemas require an explicit migration.
RECONSTRUCTION_SCHEMA_VERSION = "weather_ws_reconstructed_book_v2"
RECONSTRUCTION_RUN_SCHEMA_VERSION = "weather_ws_reconstruction_run_v1"
REST_WS_PARITY_SCHEMA_VERSION = "weather_rest_ws_book_parity_v1"
MARKET_TRADE_PRINT_SCHEMA_VERSION = "weather_market_trade_print_v1"


class BookReconstructionError(RuntimeError):
    """The raw WS stream cannot support an executable reconstructed book."""


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass(frozen=True)
class RawFrameRef:
    frame_id: str
    received_at_utc: str
    received_at_ns: int | None
    archive_path: str | None
    line_number: int | None
    producer_build_id: str | None


@dataclass(frozen=True)
class MarketTradePrint:
    """An exchange-reported match carried by the public market channel.

    ``side`` is retained exactly as reported by the exchange.  Consumers may
    compare it with the immediately preceding reconstructed book, but this
    data-layer contract deliberately does not relabel it as maker or taker.
    """

    trade_print_id: str
    subscription_epoch_id: str
    token_id: str
    market: str | None
    price: float
    size: float
    side: str
    exchange_ts_ms: int | None
    received_at_utc: str
    received_at_ns: int | None
    transaction_hash: str | None
    fee_rate_bps: int | None
    raw_frame_ref: RawFrameRef
    producer_build_id: str | None
    selector_version: str | None
    schema_version: str = MARKET_TRADE_PRINT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReconstructedBook:
    subscription_epoch_id: str
    token_id: str
    observed_at_utc: str
    exchange_ts_ms: int | None
    exchange_book_hash: str | None
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
    book_snapshot_id: str
    feature_book_snapshot_id: str | None
    execution_book_snapshot_id: str | None
    book_role: str
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    baseline_raw_frame_ref: RawFrameRef
    delta_first_raw_frame_ref: RawFrameRef | None
    delta_last_raw_frame_ref: RawFrameRef | None
    delta_frame_count: int
    delta_chain_hash: str
    last_parity_raw_frame_ref: RawFrameRef | None
    parity_check_count: int
    raw_lineage_id: str
    producer_build_id: str | None
    selector_version: str | None
    capture_policy_id: str
    capture_policy: dict[str, Any]
    token_map_id: str
    subscription_set_id: str
    sequence_status: str
    gap_detection_status: str
    last_exchange_sequence: int | None
    schema_version: str = RECONSTRUCTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RestWsParity:
    token_id: str
    ws_book_snapshot_id: str
    rest_book_capture_id: str | None
    ws_exchange_ts_ms: int | None
    rest_exchange_ts_ms: int | None
    exchange_clock_skew_ms: int | None
    parity_status: str
    same_exchange_hash: bool | None
    full_depth_equal: bool
    rest_depth_is_ws_prefix: bool
    best_quote_equal: bool
    blockers: tuple[str, ...]
    schema_version: str = REST_WS_PARITY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReconstructionRun:
    snapshots: tuple[ReconstructedBook, ...]
    declared_epochs: int
    input_frames: int
    applied_frames: int
    duplicate_frames: int
    reconstruction_errors: int
    blockers: tuple[dict[str, Any], ...]
    run_id: str
    schema_version: str = RECONSTRUCTION_RUN_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "snapshots": [row.to_dict() for row in self.snapshots],
        }


@dataclass
class _TokenBook:
    bids: dict[float, float]
    asks: dict[float, float]
    baseline_received_at_utc: str
    last_frame_received_at_utc: str
    exchange_ts_ms: int | None
    exchange_book_hash: str | None
    baseline_ref: RawFrameRef
    delta_first_ref: RawFrameRef | None
    delta_last_ref: RawFrameRef | None
    delta_frame_count: int
    delta_chain_hash: str
    last_parity_ref: RawFrameRef | None
    parity_check_count: int
    last_exchange_sequence: int | None
    seen_event_ids: set[str]


def _levels(raw: Iterable[Mapping[str, Any]]) -> dict[float, float]:
    output: dict[float, float] = {}
    for row in raw:
        price = float(row["price"])
        size = float(row["size"])
        if size > 0:
            output[price] = size
    return output


def _canonical_levels(raw: Iterable[Mapping[str, Any]]) -> tuple[tuple[float, float], ...]:
    return tuple(sorted(_levels(raw).items()))


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


def canonical_ws_frame_id(envelope: Mapping[str, Any]) -> str:
    """Return the content identity used for dedupe and raw lineage."""

    basis = {
        "subscription_epoch_id": envelope.get("subscription_epoch_id"),
        "received_at_utc": envelope.get("received_at_utc"),
        "received_at_ns": envelope.get("received_at_ns"),
        "producer": envelope.get("producer"),
        "producer_build_id": envelope.get("producer_build_id"),
        "message": envelope.get("message"),
    }
    computed = _canonical_hash(basis)
    declared = str(envelope.get("raw_frame_id") or "")
    if declared and declared != computed:
        raise BookReconstructionError(
            f"raw_frame_id mismatch: declared={declared} computed={computed}"
        )
    return computed


def extract_market_trade_prints(
    envelope: Mapping[str, Any],
) -> tuple[MarketTradePrint, ...]:
    """Extract immutable ``last_trade_price`` evidence from one raw frame.

    The public market channel reports a completed match, not our own order
    lifecycle.  This helper therefore materializes tape evidence while
    leaving queue position and own-fill attribution to downstream joins.
    """

    frame_id = canonical_ws_frame_id(envelope)
    frame_ref = _frame_ref(envelope, frame_id)
    payload = envelope.get("message")
    messages = payload if isinstance(payload, list) else [payload]
    output: list[MarketTradePrint] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        event_type = str(message.get("event_type") or message.get("type") or "")
        if event_type != "last_trade_price":
            continue
        token_id = str(message.get("asset_id") or "")
        side = str(message.get("side") or "").upper()
        if not token_id or side not in {"BUY", "SELL"}:
            raise BookReconstructionError("trade print requires asset_id and BUY/SELL side")
        price = _float_or_none(message.get("price"))
        size = _float_or_none(message.get("size"))
        if price is None or not 0.0 < price < 1.0 or size is None or size <= 0:
            raise BookReconstructionError("trade print has invalid price or size")
        exchange_ts_ms = _int_or_none(message.get("timestamp"))
        transaction_hash = (
            str(message.get("transaction_hash"))
            if message.get("transaction_hash")
            else None
        )
        basis = {
            "subscription_epoch_id": envelope.get("subscription_epoch_id"),
            "token_id": token_id,
            "market": message.get("market"),
            "price": price,
            "size": size,
            "side": side,
            "exchange_ts_ms": exchange_ts_ms,
            "transaction_hash": transaction_hash,
        }
        output.append(
            MarketTradePrint(
                trade_print_id=_canonical_hash(basis),
                subscription_epoch_id=str(envelope.get("subscription_epoch_id") or ""),
                token_id=token_id,
                market=(str(message.get("market")) if message.get("market") else None),
                price=price,
                size=size,
                side=side,
                exchange_ts_ms=exchange_ts_ms,
                received_at_utc=str(envelope.get("received_at_utc") or ""),
                received_at_ns=_int_or_none(envelope.get("received_at_ns")),
                transaction_hash=transaction_hash,
                fee_rate_bps=_int_or_none(message.get("fee_rate_bps")),
                raw_frame_ref=frame_ref,
                producer_build_id=(
                    str(envelope.get("producer_build_id"))
                    if envelope.get("producer_build_id")
                    else None
                ),
                selector_version=(
                    str(envelope.get("selector_version"))
                    if envelope.get("selector_version")
                    else None
                ),
            )
        )
    return tuple(output)


def _frame_ref(envelope: Mapping[str, Any], frame_id: str) -> RawFrameRef:
    return RawFrameRef(
        frame_id=frame_id,
        received_at_utc=str(envelope.get("received_at_utc") or ""),
        received_at_ns=_int_or_none(envelope.get("received_at_ns")),
        archive_path=(
            str(envelope.get("archive_path") or envelope.get("_raw_path"))
            if envelope.get("archive_path") or envelope.get("_raw_path")
            else None
        ),
        line_number=_int_or_none(envelope.get("line_number") or envelope.get("_line_number")),
        producer_build_id=(
            str(envelope.get("producer_build_id"))
            if envelope.get("producer_build_id")
            else None
        ),
    )


def _receive_order(envelope: Mapping[str, Any]) -> int:
    received_ns = _int_or_none(envelope.get("received_at_ns"))
    if received_ns is not None:
        return received_ns
    try:
        parsed = parse_utc(
            envelope.get("received_at_utc"), field="received_at_utc"
        )
    except ValueError as exc:
        raise BookReconstructionError(
            "WS envelope has an invalid timezone-aware received_at_utc"
        ) from exc
    assert parsed is not None
    return int(parsed.timestamp() * 1_000_000_000)


def _message_sequence(message: Mapping[str, Any], change: Mapping[str, Any] | None = None) -> int | None:
    for source in (change or {}, message):
        for key in ("sequence", "seq", "sequence_number"):
            value = _int_or_none(source.get(key))
            if value is not None:
                return value
    return None


class IncrementalBookReconstructor:
    """Reconstruct books inside explicitly declared subscription epochs."""

    def __init__(self, *, strict_best_parity: bool = True) -> None:
        self.strict_best_parity = strict_best_parity
        self.epoch_id: str | None = None
        self.token_ids: frozenset[str] = frozenset()
        self.books: dict[str, _TokenBook] = {}
        self.blocked_tokens: dict[str, str] = {}
        self.producer_build_id: str | None = None
        self.selector_version: str | None = None
        self.capture_policy: dict[str, Any] = {}
        self.capture_policy_id = _canonical_hash({})
        self.token_map_id = _canonical_hash({})
        self.subscription_set_id = _canonical_hash([])
        self.seen_frame_ids: set[str] = set()
        self.last_receive_order: int | None = None
        self.applied_frame_count = 0
        self.duplicate_frame_count = 0
        self.last_apply_status = "not_started"
        self.last_raw_frame_id: str | None = None

    def activate_epoch(
        self,
        epoch_id: str,
        token_ids: Iterable[str],
        *,
        carry_forward: bool = False,
        producer_build_id: str | None = None,
        selector_version: str | None = None,
        capture_policy: Mapping[str, Any] | None = None,
        token_rows: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        next_tokens = frozenset(str(value) for value in token_ids)
        if carry_forward and self.epoch_id is None:
            raise BookReconstructionError("cannot carry book state without a previous epoch")
        if carry_forward:
            books = {
                token_id: book
                for token_id, book in self.books.items()
                if token_id in next_tokens
            }
            blocked = {
                token_id: reason
                for token_id, reason in self.blocked_tokens.items()
                if token_id in next_tokens
            }
        else:
            books = {}
            blocked = {}
            self.last_receive_order = None
        normalized_rows = {
            str(token): dict(metadata)
            for token, metadata in (token_rows or {}).items()
            if str(token) in next_tokens
        }
        self.epoch_id = str(epoch_id)
        self.token_ids = next_tokens
        self.books = books
        self.blocked_tokens = blocked
        self.producer_build_id = producer_build_id
        self.selector_version = selector_version
        self.capture_policy = dict(capture_policy or {})
        self.capture_policy_id = _canonical_hash(self.capture_policy)
        self.token_map_id = _canonical_hash(normalized_rows)
        self.subscription_set_id = _canonical_hash(sorted(next_tokens))
        self.last_apply_status = "epoch_activated_with_carry" if carry_forward else "epoch_activated_fresh"

    def apply_envelope(self, envelope: Mapping[str, Any]) -> tuple[str, ...]:
        epoch_id = str(envelope.get("subscription_epoch_id") or "")
        if not self.epoch_id or epoch_id != self.epoch_id:
            raise BookReconstructionError(
                f"subscription epoch mismatch: active={self.epoch_id!r} frame={epoch_id!r}"
            )
        received = str(envelope.get("received_at_utc") or "")
        if not received:
            raise BookReconstructionError("WS envelope is missing received_at_utc")
        frame_id = canonical_ws_frame_id(envelope)
        self.last_raw_frame_id = frame_id
        if frame_id in self.seen_frame_ids:
            self.duplicate_frame_count += 1
            self.last_apply_status = "duplicate_frame_ignored"
            return ()
        receive_order = _receive_order(envelope)
        if self.last_receive_order is not None and receive_order < self.last_receive_order:
            affected = _message_token_ids(envelope.get("message")) & set(self.token_ids)
            self._block(affected, "out_of_order_receive_clock")
            self.last_apply_status = "blocked_out_of_order_receive_clock"
            raise BookReconstructionError("WS receive clock moved backwards")
        self.seen_frame_ids.add(frame_id)
        self.last_receive_order = receive_order
        frame_ref = _frame_ref(envelope, frame_id)
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
                exchange_ts = _int_or_none(message.get("timestamp"))
                event_id = _canonical_hash({"event_type": "book", "message": message})
                existing = self.books.get(token_id)
                if existing and event_id in existing.seen_event_ids:
                    continue
                if (
                    existing
                    and exchange_ts is not None
                    and existing.exchange_ts_ms is not None
                    and exchange_ts < existing.exchange_ts_ms
                ):
                    self._block({token_id}, "out_of_order_exchange_timestamp")
                    raise BookReconstructionError(
                        f"book exchange timestamp regressed for token={token_id}"
                    )
                sequence = _message_sequence(message)
                self.books[token_id] = _TokenBook(
                    bids=_levels(message.get("bids") or ()),
                    asks=_levels(message.get("asks") or ()),
                    baseline_received_at_utc=received,
                    last_frame_received_at_utc=received,
                    exchange_ts_ms=exchange_ts,
                    exchange_book_hash=(
                        str(message.get("hash")) if message.get("hash") else None
                    ),
                    baseline_ref=frame_ref,
                    delta_first_ref=None,
                    delta_last_ref=None,
                    delta_frame_count=0,
                    delta_chain_hash=_canonical_hash([]),
                    last_parity_ref=None,
                    parity_check_count=0,
                    last_exchange_sequence=sequence,
                    seen_event_ids={event_id},
                )
                self.blocked_tokens.pop(token_id, None)
                updated.append(token_id)
            elif event_type == "price_change":
                parity_rows: dict[str, Mapping[str, Any]] = {}
                touched_in_frame: set[str] = set()
                for change in message.get("price_changes") or ():
                    token_id = str(change.get("asset_id") or "")
                    if token_id not in self.token_ids:
                        continue
                    book = self.books.get(token_id)
                    if book is None:
                        self.blocked_tokens.setdefault(token_id, "delta_before_book_baseline")
                        continue
                    exchange_ts = _int_or_none(message.get("timestamp"))
                    event_id = _canonical_hash(
                        {
                            "event_type": "price_change",
                            "token_id": token_id,
                            "exchange_ts_ms": exchange_ts,
                            "change": change,
                        }
                    )
                    if event_id in book.seen_event_ids:
                        continue
                    if (
                        exchange_ts is not None
                        and book.exchange_ts_ms is not None
                        and exchange_ts < book.exchange_ts_ms
                    ):
                        self._block({token_id}, "out_of_order_exchange_timestamp")
                        raise BookReconstructionError(
                            f"delta exchange timestamp regressed for token={token_id}"
                        )
                    sequence = _message_sequence(message, change)
                    first_token_change_in_frame = token_id not in touched_in_frame
                    if (
                        sequence is not None
                        and book.last_exchange_sequence is not None
                        and first_token_change_in_frame
                        and sequence > book.last_exchange_sequence + 1
                    ):
                        self._block({token_id}, "exchange_sequence_gap")
                        raise BookReconstructionError(
                            f"exchange sequence gap for token={token_id}: "
                            f"last={book.last_exchange_sequence} next={sequence}"
                        )
                    if (
                        sequence is not None
                        and book.last_exchange_sequence is not None
                        and first_token_change_in_frame
                        and sequence <= book.last_exchange_sequence
                    ):
                        self._block({token_id}, "out_of_order_exchange_sequence")
                        raise BookReconstructionError(
                            f"exchange sequence regressed for token={token_id}"
                        )
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
                    book.exchange_ts_ms = exchange_ts
                    if change.get("hash"):
                        book.exchange_book_hash = str(change["hash"])
                    if sequence is not None:
                        book.last_exchange_sequence = sequence
                    book.seen_event_ids.add(event_id)
                    if token_id not in touched_in_frame:
                        if book.delta_first_ref is None:
                            book.delta_first_ref = frame_ref
                        book.delta_last_ref = frame_ref
                        book.delta_frame_count += 1
                        book.delta_chain_hash = _canonical_hash(
                            {"previous": book.delta_chain_hash, "frame_id": frame_id}
                        )
                        touched_in_frame.add(token_id)
                    parity_rows[token_id] = change
                    updated.append(token_id)
                for token_id, change in parity_rows.items():
                    self._verify_best(token_id, change)
            elif event_type == "best_bid_ask":
                token_id = str(message.get("asset_id") or "")
                if token_id not in self.token_ids:
                    continue
                book = self.books.get(token_id)
                if book is None:
                    self.blocked_tokens.setdefault(token_id, "parity_before_book_baseline")
                    continue
                exchange_ts = _int_or_none(message.get("timestamp"))
                if (
                    exchange_ts is not None
                    and book.exchange_ts_ms is not None
                    and exchange_ts < book.exchange_ts_ms
                ):
                    self._block({token_id}, "out_of_order_exchange_timestamp")
                    raise BookReconstructionError(
                        f"best-quote exchange timestamp regressed for token={token_id}"
                    )
                event_id = _canonical_hash(
                    {"event_type": "best_bid_ask", "token_id": token_id, "message": message}
                )
                if event_id in book.seen_event_ids:
                    continue
                if self._best_matches(token_id, message):
                    if self.blocked_tokens.get(token_id) == "best_quote_parity_pending":
                        self.blocked_tokens.pop(token_id, None)
                else:
                    # The exchange can publish best_bid_ask immediately before
                    # the matching price_change frame.  Keep the pre-delta
                    # state in memory so that frame can reconcile it, but make
                    # the book unavailable to model checkpoints meanwhile.
                    self.blocked_tokens[token_id] = "best_quote_parity_pending"
                book.seen_event_ids.add(event_id)
                book.last_frame_received_at_utc = received
                book.last_parity_ref = frame_ref
                book.parity_check_count += 1
                updated.append(token_id)
        self.applied_frame_count += 1
        self.last_apply_status = "applied" if updated else "applied_no_relevant_updates"
        return tuple(dict.fromkeys(updated))

    def _block(self, token_ids: Iterable[str], reason: str) -> None:
        for token_id in token_ids:
            self.books.pop(token_id, None)
            self.blocked_tokens[token_id] = reason

    def _verify_best(self, token_id: str, change: Mapping[str, Any]) -> None:
        if not self.strict_best_parity:
            return
        if self._best_matches(token_id, change):
            if self.blocked_tokens.get(token_id) == "best_quote_parity_pending":
                self.blocked_tokens.pop(token_id, None)
            return
        book = self.books[token_id]
        reconstructed_bid = max(book.bids, default=None)
        reconstructed_ask = min(book.asks, default=None)
        reported_bid = _float_or_none(change.get("best_bid"))
        reported_ask = _float_or_none(change.get("best_ask"))
        if reported_bid == 0.0:
            reported_bid = None
        if reported_ask == 1.0:
            reported_ask = None
        self._block({token_id}, "best_quote_parity_mismatch")
        raise BookReconstructionError(
            "reconstructed best quote disagrees with WS evidence: "
            f"token={token_id} reconstructed=({reconstructed_bid},{reconstructed_ask}) "
            f"reported=({reported_bid},{reported_ask})"
        )

    def _best_matches(self, token_id: str, change: Mapping[str, Any]) -> bool:
        book = self.books[token_id]
        reconstructed_bid = max(book.bids, default=None)
        reconstructed_ask = min(book.asks, default=None)
        reported_bid = _float_or_none(change.get("best_bid"))
        reported_ask = _float_or_none(change.get("best_ask"))
        if reported_bid == 0.0:
            reported_bid = None
        if reported_ask == 1.0:
            reported_ask = None
        return _same_price(reconstructed_bid, reported_bid) and _same_price(
            reconstructed_ask, reported_ask
        )

    def snapshot(
        self,
        token_id: str,
        *,
        observed_at_utc: str,
        requested_shares: float = 5.0,
        book_role: str = "model_feature",
    ) -> ReconstructedBook:
        token_id = str(token_id)
        if requested_shares <= 0:
            raise ValueError("requested_shares must be positive")
        if book_role not in {
            "model_feature",
            "execution_quote",
            "market_state_evidence",
        }:
            raise ValueError(
                "book_role must be model_feature, execution_quote, or market_state_evidence"
            )
        if token_id in self.blocked_tokens:
            raise BookReconstructionError(
                f"token={token_id} is not reconstructable: {self.blocked_tokens[token_id]}"
            )
        book = self.books.get(token_id)
        if book is None:
            reason = self.blocked_tokens.get(token_id, "book_baseline_missing")
            raise BookReconstructionError(f"token={token_id} is not reconstructable: {reason}")
        best_bid = max(book.bids, default=None)
        best_ask = min(book.asks, default=None)
        sell_proceeds = _sweep(book.bids, requested_shares, reverse=True)
        buy_cost = _sweep(book.asks, requested_shares, reverse=False)
        bids = tuple(sorted(book.bids.items(), reverse=True))
        asks = tuple(sorted(book.asks.items()))
        lineage = {
            "baseline_raw_frame_ref": asdict(book.baseline_ref),
            "delta_first_raw_frame_ref": (
                asdict(book.delta_first_ref) if book.delta_first_ref else None
            ),
            "delta_last_raw_frame_ref": (
                asdict(book.delta_last_ref) if book.delta_last_ref else None
            ),
            "delta_frame_count": book.delta_frame_count,
            "delta_chain_hash": book.delta_chain_hash,
            "last_parity_raw_frame_ref": (
                asdict(book.last_parity_ref) if book.last_parity_ref else None
            ),
            "parity_check_count": book.parity_check_count,
        }
        raw_lineage_id = _canonical_hash(lineage)
        state = {
            "schema_version": RECONSTRUCTION_SCHEMA_VERSION,
            "subscription_epoch_id": self.epoch_id,
            "token_id": token_id,
            "bids": bids,
            "asks": asks,
            "raw_lineage_id": raw_lineage_id,
            "producer_build_id": self.producer_build_id,
            "selector_version": self.selector_version,
            "capture_policy_id": self.capture_policy_id,
            "token_map_id": self.token_map_id,
            "subscription_set_id": self.subscription_set_id,
        }
        snapshot_id = _canonical_hash(state)
        sequence_status = (
            "exchange_sequence_verified"
            if book.last_exchange_sequence is not None
            else "exchange_sequence_unavailable"
        )
        gap_status = (
            "sequence_and_best_quote_parity_checked"
            if book.last_exchange_sequence is not None
            else "best_quote_parity_checked_sequence_unavailable"
        )
        return ReconstructedBook(
            subscription_epoch_id=str(self.epoch_id),
            token_id=token_id,
            observed_at_utc=observed_at_utc,
            exchange_ts_ms=book.exchange_ts_ms,
            exchange_book_hash=book.exchange_book_hash,
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
                if abs(float(requested_shares) - 5.0) <= 1e-12
                and buy_cost is not None
                and sell_proceeds is not None
                else "requested_shares_two_way_executable"
                if buy_cost is not None and sell_proceeds is not None
                else "insufficient_depth"
            ),
            snapshot_id=snapshot_id,
            book_snapshot_id=snapshot_id,
            feature_book_snapshot_id=snapshot_id if book_role == "model_feature" else None,
            execution_book_snapshot_id=snapshot_id if book_role == "execution_quote" else None,
            book_role=book_role,
            bids=bids,
            asks=asks,
            baseline_raw_frame_ref=book.baseline_ref,
            delta_first_raw_frame_ref=book.delta_first_ref,
            delta_last_raw_frame_ref=book.delta_last_ref,
            delta_frame_count=book.delta_frame_count,
            delta_chain_hash=book.delta_chain_hash,
            last_parity_raw_frame_ref=book.last_parity_ref,
            parity_check_count=book.parity_check_count,
            raw_lineage_id=raw_lineage_id,
            producer_build_id=self.producer_build_id,
            selector_version=self.selector_version,
            capture_policy_id=self.capture_policy_id,
            capture_policy=dict(self.capture_policy),
            token_map_id=self.token_map_id,
            subscription_set_id=self.subscription_set_id,
            sequence_status=sequence_status,
            gap_detection_status=gap_status,
            last_exchange_sequence=book.last_exchange_sequence,
        )


def compare_rest_ws_parity(
    ws_book: ReconstructedBook,
    rest_book: Mapping[str, Any],
    *,
    max_clock_skew_ms: int = 2_000,
) -> RestWsParity:
    """Compare independent REST evidence without mutating the WS state."""

    raw = rest_book.get("raw") if isinstance(rest_book.get("raw"), Mapping) else rest_book
    rest_bids = _canonical_levels(raw.get("bids") or ())
    rest_asks = _canonical_levels(raw.get("asks") or ())
    ws_bids = tuple(sorted(ws_book.bids))
    ws_asks = tuple(sorted(ws_book.asks))
    full_equal = ws_bids == rest_bids and ws_asks == rest_asks
    ws_bids_best_first = tuple(sorted(ws_book.bids, reverse=True))
    rest_bids_best_first = tuple(sorted(rest_bids, reverse=True))
    ws_asks_best_first = tuple(sorted(ws_book.asks))
    rest_asks_best_first = tuple(sorted(rest_asks))
    rest_is_prefix = (
        ws_bids_best_first[: len(rest_bids_best_first)] == rest_bids_best_first
        and ws_asks_best_first[: len(rest_asks_best_first)] == rest_asks_best_first
    )
    rest_best_bid = max((price for price, _ in rest_bids), default=None)
    rest_best_ask = min((price for price, _ in rest_asks), default=None)
    best_equal = _same_price(ws_book.best_bid, rest_best_bid) and _same_price(
        ws_book.best_ask, rest_best_ask
    )
    rest_exchange_ts = _int_or_none(
        rest_book.get("exchange_book_ts_raw") or raw.get("timestamp")
    )
    skew = (
        abs(rest_exchange_ts - ws_book.exchange_ts_ms)
        if rest_exchange_ts is not None and ws_book.exchange_ts_ms is not None
        else None
    )
    rest_hash = rest_book.get("exchange_book_hash") or raw.get("hash")
    same_hash = (
        str(rest_hash) == str(ws_book.exchange_book_hash)
        if rest_hash not in (None, "") and ws_book.exchange_book_hash not in (None, "")
        else None
    )
    blockers: list[str] = []
    if skew is None:
        status = "not_comparable_missing_exchange_clock"
        blockers.append("exchange_clock_missing")
    elif skew > max_clock_skew_ms:
        status = "not_comparable_clock_skew"
        blockers.append("exchange_clock_skew_exceeds_policy")
    elif full_equal:
        status = "full_depth_parity"
    elif best_equal and rest_is_prefix:
        status = "rest_depth_prefix_parity"
    elif best_equal:
        status = "best_quote_parity_only"
        blockers.append("full_depth_mismatch")
    else:
        status = "parity_mismatch"
        blockers.append("best_quote_mismatch")
    return RestWsParity(
        token_id=ws_book.token_id,
        ws_book_snapshot_id=ws_book.book_snapshot_id,
        rest_book_capture_id=(
            str(rest_book.get("book_capture_id")) if rest_book.get("book_capture_id") else None
        ),
        ws_exchange_ts_ms=ws_book.exchange_ts_ms,
        rest_exchange_ts_ms=rest_exchange_ts,
        exchange_clock_skew_ms=skew,
        parity_status=status,
        same_exchange_hash=same_hash,
        full_depth_equal=full_equal,
        rest_depth_is_ws_prefix=rest_is_prefix,
        best_quote_equal=best_equal,
        blockers=tuple(blockers),
    )


def materialize_reconstructed_books(
    subscription_epochs: Sequence[Mapping[str, Any]],
    frames: Sequence[Mapping[str, Any]],
    *,
    requested_shares: float = 5.0,
    strict_best_parity: bool = True,
) -> ReconstructionRun:
    """Replay epoch manifests and raw frames into immutable book snapshots."""

    epochs = sorted(subscription_epochs, key=lambda row: str(row.get("started_at_utc") or ""))
    epoch_ids = [str(row.get("subscription_epoch_id") or "") for row in epochs]
    if not epochs or any(not epoch_id for epoch_id in epoch_ids):
        raise BookReconstructionError("subscription epochs must declare non-empty identities")
    if len(set(epoch_ids)) != len(epoch_ids):
        raise BookReconstructionError("duplicate subscription_epoch_id")
    declared = set(epoch_ids)
    unknown = sorted(
        {
            str(frame.get("subscription_epoch_id") or "")
            for frame in frames
            if str(frame.get("subscription_epoch_id") or "") not in declared
        }
    )
    if unknown:
        raise BookReconstructionError(f"raw frames reference undeclared epochs: {unknown}")
    frames_by_epoch: dict[str, list[Mapping[str, Any]]] = {}
    for frame in frames:
        frames_by_epoch.setdefault(str(frame.get("subscription_epoch_id")), []).append(frame)
    engine = IncrementalBookReconstructor(strict_best_parity=strict_best_parity)
    snapshots: list[ReconstructedBook] = []
    blockers: list[dict[str, Any]] = []
    open_blockers: dict[str, int] = {}
    errors = 0
    applied_before = 0
    duplicate_before = 0
    previous_epoch_id: str | None = None

    def close_blocker(token_id: str, at_utc: str, recovery_status: str) -> None:
        index = open_blockers.pop(token_id, None)
        if index is None:
            return
        blockers[index]["recovered_at_utc"] = at_utc
        blockers[index]["recovery_status"] = recovery_status

    def open_blocker(
        token_id: str,
        *,
        epoch_id: str,
        at_utc: str,
        raw_frame_id: str | None,
        reason: str,
    ) -> None:
        current = open_blockers.get(token_id)
        if current is not None and blockers[current]["reason"] == reason:
            return
        if current is not None:
            close_blocker(token_id, at_utc, "superseded_by_new_blocker")
        blockers.append(
            {
                "subscription_epoch_id": epoch_id,
                "token_id": token_id,
                "started_at_utc": at_utc,
                "raw_frame_id": raw_frame_id,
                "reason": reason,
                "recovered_at_utc": None,
                "recovery_status": "open",
            }
        )
        open_blockers[token_id] = len(blockers) - 1

    for epoch in epochs:
        epoch_id = str(epoch["subscription_epoch_id"])
        declared_previous = str(epoch.get("previous_subscription_epoch_id") or "") or None
        # A null predecessor is an explicit reconnect/root boundary.  State
        # from the previous socket must not be carried across it, but the new
        # epoch is still valid once it supplies fresh book baselines.  A
        # non-null predecessor pointing anywhere except the immediately prior
        # epoch remains a real chain gap and must fail closed.
        if (
            previous_epoch_id is not None
            and declared_previous is not None
            and declared_previous != previous_epoch_id
        ):
            raise BookReconstructionError(
                f"subscription epoch chain gap: epoch={epoch_id} "
                f"declared_previous={declared_previous} expected={previous_epoch_id}"
            )
        carry_requested = str(epoch.get("reason") or "") == "selector_reconcile"
        carry = (
            carry_requested
            and previous_epoch_id is not None
            and declared_previous == previous_epoch_id
        )
        epoch_started = str(epoch.get("started_at_utc") or "")
        next_tokens = {str(value) for value in epoch.get("token_ids") or ()}
        if previous_epoch_id is not None:
            for token_id in list(open_blockers):
                if not carry:
                    close_blocker(
                        token_id,
                        epoch_started,
                        "reconnect_reset_requires_fresh_baseline",
                    )
                elif token_id not in next_tokens:
                    close_blocker(token_id, epoch_started, "subscription_ended")
        engine.activate_epoch(
            epoch_id,
            epoch.get("token_ids") or (),
            carry_forward=carry,
            producer_build_id=(
                str(epoch.get("producer_build_id")) if epoch.get("producer_build_id") else None
            ),
            selector_version=(
                str(epoch.get("selector_version")) if epoch.get("selector_version") else None
            ),
            capture_policy=(epoch.get("capture_policy") or {}),
            token_rows=(epoch.get("token_rows") or {}),
        )
        if carry:
            for token_id in sorted(engine.books):
                try:
                    snapshot = engine.snapshot(
                        token_id,
                        observed_at_utc=str(epoch.get("started_at_utc")),
                        requested_shares=requested_shares,
                    )
                except BookReconstructionError as exc:
                    open_blocker(
                        token_id,
                        epoch_id=epoch_id,
                        at_utc=epoch_started,
                        raw_frame_id=None,
                        reason=engine.blocked_tokens.get(token_id, str(exc)),
                    )
                    continue
                close_blocker(token_id, epoch_started, "recovered_by_verified_state")
                snapshots.append(snapshot)
        ordered_frames = sorted(
            frames_by_epoch.get(epoch_id, ()),
            key=lambda row: (_receive_order(row), canonical_ws_frame_id(row)),
        )
        for frame in ordered_frames:
            try:
                updated = engine.apply_envelope(frame)
            except BookReconstructionError as exc:
                errors += 1
                for token_id, reason in engine.blocked_tokens.items():
                    open_blocker(
                        token_id,
                        epoch_id=epoch_id,
                        at_utc=str(frame.get("received_at_utc") or ""),
                        raw_frame_id=engine.last_raw_frame_id,
                        reason=reason,
                )
                continue
            for token_id, reason in engine.blocked_tokens.items():
                open_blocker(
                    token_id,
                    epoch_id=epoch_id,
                    at_utc=str(frame.get("received_at_utc") or ""),
                    raw_frame_id=engine.last_raw_frame_id,
                    reason=reason,
                )
            for token_id in updated:
                try:
                    snapshot = engine.snapshot(
                        token_id,
                        observed_at_utc=str(frame.get("received_at_utc")),
                        requested_shares=requested_shares,
                    )
                except BookReconstructionError as exc:
                    open_blocker(
                        token_id,
                        epoch_id=epoch_id,
                        at_utc=str(frame.get("received_at_utc") or ""),
                        raw_frame_id=engine.last_raw_frame_id,
                        reason=engine.blocked_tokens.get(token_id, str(exc)),
                    )
                    continue
                close_blocker(
                    token_id,
                    str(frame.get("received_at_utc") or ""),
                    "recovered_by_verified_state",
                )
                snapshots.append(snapshot)
        previous_epoch_id = epoch_id
    applied = engine.applied_frame_count - applied_before
    duplicates = engine.duplicate_frame_count - duplicate_before
    run_basis = {
        "schema_version": RECONSTRUCTION_RUN_SCHEMA_VERSION,
        "epoch_ids": epoch_ids,
        "raw_frame_ids": sorted(canonical_ws_frame_id(frame) for frame in frames),
        "snapshot_ids": [row.book_snapshot_id for row in snapshots],
        "blockers": blockers,
    }
    return ReconstructionRun(
        snapshots=tuple(snapshots),
        declared_epochs=len(epochs),
        input_frames=len(frames),
        applied_frames=applied,
        duplicate_frames=duplicates,
        reconstruction_errors=errors,
        blockers=tuple(blockers),
        run_id=_canonical_hash(run_basis),
    )


def _message_token_ids(message: Any) -> set[str]:
    messages = message if isinstance(message, list) else [message]
    output: set[str] = set()
    for row in messages:
        if not isinstance(row, Mapping):
            continue
        token = row.get("asset_id") or row.get("assetId") or row.get("token_id")
        if token:
            output.add(str(token))
        for change in row.get("price_changes") or ():
            if isinstance(change, Mapping) and change.get("asset_id"):
                output.add(str(change["asset_id"]))
    return output


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
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
