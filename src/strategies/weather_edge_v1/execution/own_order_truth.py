"""Pure, conservative reconstruction of one Polymarket order's own-order truth.

The User Channel deliberately has no sequence number.  Consequently websocket
updates are useful evidence, but never become final truth until a sufficiently
new authenticated REST order snapshot has been applied.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from weather_clock_contract import parse_utc, utc_text

from .contracts import RestingOrderState, canonical_json


_SOURCES = frozenset({"user_ws_order", "user_ws_trade", "rest_order"})
_TERMINAL = frozenset({"FILLED", "CANCELLED", "EXPIRED", "REJECTED"})
_TRADE_CONFIRMED = frozenset({"MATCHED", "FILLED", "CONFIRMED"})
_ORDER_STATUSES = frozenset({"LIVE", "MATCHED", *_TERMINAL})


def _text(value: Any) -> str | None:
    value = None if value is None else str(value).strip()
    return value or None


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("decimal values must not be floats")
    value = Decimal(str(value))
    if not value.is_finite() or value < 0:
        raise ValueError("decimal must be finite and non-negative")
    return value


def normalize_status(value: Any) -> str:
    text = (_text(value) or "UNKNOWN").upper().replace("-", "_")
    if text.startswith("ORDER_STATUS_"):
        text = text[len("ORDER_STATUS_") :]
    if text in {"CANCELED", "CANCELLED"}:
        return "CANCELLED"
    if text in {"MATCHED", "FILLED", "FULLY_FILLED"}:
        return "FILLED" if text != "MATCHED" else "MATCHED"
    if text.startswith("LIVE") or text in {"OPEN", "UNMATCHED", "PARTIALLY_FILLED"}:
        return "LIVE"
    if text.startswith("EXPIRE"):
        return "EXPIRED"
    if text.startswith("REJECT"):
        return "REJECTED"
    if text.startswith("CONFIRM"):
        return "CONFIRMED"
    return text


@dataclass(frozen=True)
class TruthBlocker:
    code: str
    message: str
    event_identity: str | None = None


@dataclass(frozen=True)
class OwnOrderObservation:
    source: str
    received_at_utc: str
    source_timestamp: str | None
    event_identity: str
    order_id: str | None
    lifecycle_owner: str | None
    venue_owner: str | None
    status: str
    requested_shares: Decimal | None = None
    absolute_matched_shares: Decimal | None = None
    trade_id: str | None = None
    trade_matched_shares: Decimal | None = None
    maker_allocations: tuple[tuple[str, Decimal], ...] = ()
    malformed_reason: str | None = None


@dataclass(frozen=True)
class OwnOrderTruthState:
    expected_order_id: str
    lifecycle_owner: str
    expected_venue_owner: str | None = None
    venue_owner: str | None = None
    requested_shares: Decimal | None = None
    absolute_matched_shares: Decimal = Decimal("0")
    trade_matched_shares: Decimal = Decimal("0")
    effective_matched_shares: Decimal = Decimal("0")
    remaining_shares: Decimal | None = None
    status: str = "UNKNOWN"
    raw_status: str = "UNKNOWN"
    terminal: bool = False
    reconciliation_required: bool = False
    latest_ws_received_at_utc: str | None = None
    latest_rest_received_at_utc: str | None = None
    counted_trade_ids: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    blockers: tuple[TruthBlocker, ...] = ()
    authoritative_state_version: str = ""

    @property
    def clean(self) -> bool:
        return not self.blockers and not self.reconciliation_required


def _identity(source: str, payload: Mapping[str, Any]) -> str:
    # The complete normalized incoming row makes retransmissions stable even
    # for order messages which do not have a separate event id.
    return hashlib.sha256((source + ":" + canonical_json(dict(payload))).encode()).hexdigest()


def normalize_own_order_observation(
    source: str, payload: Mapping[str, Any], *, received_at_utc: str,
) -> OwnOrderObservation:
    """Normalise an untrusted User Channel/REST row without float arithmetic."""
    identity = _identity(source, payload)
    malformed: str | None = None
    if source not in _SOURCES:
        malformed = "unsupported source"
    try:
        received_at = utc_text(
            received_at_utc,
            field="own_order_received_at_utc",
            timespec="microseconds",
        )
    except ValueError as exc:
        received_at = str(received_at_utc)
        malformed = malformed or str(exc)
    event_type = _text(payload.get("event_type"))
    if source == "user_ws_order" and event_type not in {None, "order"}:
        malformed = malformed or "unexpected websocket event_type"
    if source == "user_ws_trade" and event_type not in {None, "trade"}:
        malformed = malformed or "unexpected websocket event_type"
    order_id = _text(payload.get("id")) if source != "user_ws_trade" else _text(payload.get("taker_order_id"))
    # Venue `owner`/`order_owner` is an API-key/account identity, not the
    # strategy's internal lifecycle owner.  Only an explicit internal envelope
    # field may assert lifecycle ownership.
    owner = _text(payload.get("lifecycle_owner"))
    venue_owner = _text(
        payload.get("order_owner")
        or payload.get("trade_owner")
        or payload.get("owner")
    )
    requested = absolute = allocation = None
    maker_allocations: tuple[tuple[str, Decimal], ...] = ()
    trade_id = _text(payload.get("id")) if source == "user_ws_trade" else None
    try:
        if source in {"user_ws_order", "rest_order"}:
            if payload.get("original_size") is None:
                malformed = malformed or "missing original_size"
            else:
                requested = _decimal(payload["original_size"])
                if requested <= 0:
                    malformed = malformed or "original_size must be positive"
            if payload.get("size_matched") is None:
                malformed = malformed or "missing size_matched"
            else:
                absolute = _decimal(payload["size_matched"])
        elif source == "user_ws_trade":
            matched = None
            maker_matches = [m for m in payload.get("maker_orders", ()) if _text(m.get("order_id"))]
            maker_allocations = tuple((_text(m.get("order_id")) or "", _decimal(m.get("matched_amount"))) for m in maker_matches)
            if _text(payload.get("taker_order_id")):
                matched = payload.get("size", payload.get("matched_amount"))
            if matched is not None:
                allocation = _decimal(matched)
    except (InvalidOperation, ValueError, TypeError) as exc:
        malformed = malformed or f"invalid decimal: {exc}"
    if source == "user_ws_trade" and not trade_id:
        malformed = malformed or "missing trade id"
    status = normalize_status(payload.get("status", payload.get("type")))
    if source in {"user_ws_order", "rest_order"} and status not in _ORDER_STATUSES:
        malformed = malformed or f"unsupported order status: {status}"
    return OwnOrderObservation(source, received_at, _text(payload.get("timestamp", payload.get("last_update"))), identity, order_id, owner, venue_owner, status, requested, absolute, trade_id, allocation, maker_allocations, malformed)


class OwnOrderTruthReducer:
    def __init__(
        self,
        *,
        expected_order_id: str,
        lifecycle_owner: str,
        expected_venue_owner: str | None = None,
    ) -> None:
        self.expected_order_id = str(expected_order_id).strip()
        self.lifecycle_owner = str(lifecycle_owner).strip()
        self.expected_venue_owner = _text(expected_venue_owner)
        if not self.expected_order_id or not self.lifecycle_owner:
            raise ValueError("expected_order_id and lifecycle_owner are required")

    def initial_state(self) -> OwnOrderTruthState:
        return _version(
            OwnOrderTruthState(
                self.expected_order_id,
                self.lifecycle_owner,
                expected_venue_owner=self.expected_venue_owner,
            )
        )

    def reduce(self, state: OwnOrderTruthState, observation: OwnOrderObservation | Mapping[str, Any]) -> OwnOrderTruthState:
        if isinstance(observation, Mapping):
            try:
                observation = normalize_own_order_observation(
                    str(observation["source"]), observation.get("payload", observation), received_at_utc=str(observation["received_at_utc"])
                )
            except Exception as exc:
                return _blocked(state, "malformed_row", str(exc), None)
        if (
            state.expected_order_id != self.expected_order_id
            or state.lifecycle_owner != self.lifecycle_owner
            or state.expected_venue_owner != self.expected_venue_owner
        ):
            return _blocked(state, "reducer_identity", "state belongs to another reducer", observation.event_identity)
        if observation.malformed_reason:
            return _blocked(state, "malformed_row", observation.malformed_reason, observation.event_identity)
        if observation.lifecycle_owner and observation.lifecycle_owner != self.lifecycle_owner:
            return _blocked(state, "owner_mismatch", "observation lifecycle owner differs", observation.event_identity)
        if (
            self.expected_venue_owner
            and observation.venue_owner
            and observation.venue_owner != self.expected_venue_owner
        ):
            return _blocked(
                state,
                "venue_owner_mismatch",
                "observation venue owner differs",
                observation.event_identity,
            )
        if (
            state.venue_owner
            and observation.venue_owner
            and state.venue_owner != observation.venue_owner
        ):
            return _blocked(
                state,
                "venue_owner_conflict",
                "venue owner changed within one order chain",
                observation.event_identity,
            )

        # A trade may identify our order as taker, or as one maker allocation.
        trade_amount = observation.trade_matched_shares
        relevant = observation.order_id == self.expected_order_id
        if observation.source == "user_ws_trade" and not relevant:
            maker_amounts = [amount for order_id, amount in observation.maker_allocations if order_id == self.expected_order_id]
            relevant = bool(maker_amounts)
            if relevant:
                trade_amount = sum(maker_amounts, Decimal("0"))
        if not relevant:
            return _blocked(state, "order_mismatch", "observation is not for expected order", observation.event_identity)
        if observation.source == "user_ws_trade" and observation.status not in _TRADE_CONFIRMED:
            return _version(_with_evidence(state, observation.event_identity, observation.received_at_utc, ws=True))
        if (
            state.terminal
            and observation.source != "user_ws_trade"
            and observation.status not in _TERMINAL
        ):
            return _blocked(state, "terminal_reopening", "terminal order received non-terminal update", observation.event_identity)

        next_state = _with_evidence(state, observation.event_identity, observation.received_at_utc, ws=observation.source.startswith("user_ws"), rest=observation.source == "rest_order")
        if observation.venue_owner:
            next_state = replace(next_state, venue_owner=observation.venue_owner)
        if observation.requested_shares is not None:
            if next_state.requested_shares is not None and next_state.requested_shares != observation.requested_shares:
                return _blocked(next_state, "requested_size_conflict", "original_size changed", observation.event_identity)
            next_state = replace(next_state, requested_shares=observation.requested_shares)
        if observation.absolute_matched_shares is not None:
            if observation.absolute_matched_shares < next_state.absolute_matched_shares:
                return _blocked(next_state, "absolute_regression", "size_matched regressed", observation.event_identity)
            next_state = replace(next_state, absolute_matched_shares=observation.absolute_matched_shares)
        if observation.source == "user_ws_trade" and observation.trade_id not in next_state.counted_trade_ids:
            amount = trade_amount or Decimal("0")
            next_state = replace(next_state, trade_matched_shares=next_state.trade_matched_shares + amount, counted_trade_ids=next_state.counted_trade_ids + (str(observation.trade_id),))
        effective = max(next_state.absolute_matched_shares, next_state.trade_matched_shares)
        if next_state.requested_shares is not None and effective > next_state.requested_shares:
            return _blocked(next_state, "matched_exceeds_requested", "matched shares exceed original_size", observation.event_identity)
        full = (
            next_state.requested_shares is not None
            and effective == next_state.requested_shares
            and effective > 0
        )
        if full:
            status = "FILLED"
            terminal = True
        elif state.terminal and observation.source == "user_ws_trade":
            # A late fill after cancel/expiry changes matched quantity, not the
            # already terminal lifecycle disposition.
            status = state.status
            terminal = True
        else:
            status = "LIVE" if observation.status == "MATCHED" else observation.status
            terminal = status in _TERMINAL
        remaining = None if next_state.requested_shares is None else max(next_state.requested_shares - effective, Decimal("0"))
        result = replace(next_state, effective_matched_shares=effective, remaining_shares=remaining, status=status, raw_status=observation.status, terminal=terminal)
        if observation.source == "rest_order":
            rest_is_new_enough = (
                result.latest_ws_received_at_utc is None
                or _at_or_after(
                    observation.received_at_utc,
                    result.latest_ws_received_at_utc,
                )
            )
            rest_covers_trade_evidence = (
                observation.absolute_matched_shares is not None
                and observation.absolute_matched_shares
                >= result.trade_matched_shares
            )
            result = replace(
                result,
                reconciliation_required=not (
                    rest_is_new_enough and rest_covers_trade_evidence
                ),
            )
        return _version(result)

    def replay(self, observations: Iterable[OwnOrderObservation | Mapping[str, Any]]) -> OwnOrderTruthState:
        state = self.initial_state()
        for item in observations:
            state = self.reduce(state, item)
        return state


def _with_evidence(state: OwnOrderTruthState, identity: str, received_at_utc: str, *, ws: bool = False, rest: bool = False) -> OwnOrderTruthState:
    evidence = state.evidence if identity in state.evidence else state.evidence + (identity,)
    latest_ws = _latest(state.latest_ws_received_at_utc, received_at_utc) if ws else state.latest_ws_received_at_utc
    latest_rest = _latest(state.latest_rest_received_at_utc, received_at_utc) if rest else state.latest_rest_received_at_utc
    return replace(state, evidence=evidence, reconciliation_required=(state.reconciliation_required or ws), latest_ws_received_at_utc=latest_ws, latest_rest_received_at_utc=latest_rest)


def _at_or_after(candidate: str, reference: str) -> bool:
    candidate_at = parse_utc(candidate, field="candidate_received_at_utc")
    reference_at = parse_utc(reference, field="reference_received_at_utc")
    assert candidate_at is not None and reference_at is not None
    return candidate_at >= reference_at


def _latest(first: str | None, second: str) -> str:
    if first is None:
        return second
    return second if _at_or_after(second, first) else first


def _blocked(state: OwnOrderTruthState, code: str, message: str, identity: str | None) -> OwnOrderTruthState:
    blocker = TruthBlocker(code, message, identity)
    if blocker in state.blockers:
        return _version(state)
    return _version(replace(state, blockers=state.blockers + (blocker,)))


def _version(state: OwnOrderTruthState) -> OwnOrderTruthState:
    # REST closure needs the observation time; defer it here from evidence is
    # impossible, so reducer sets it explicitly in its small wrapper below.
    payload = {name: getattr(state, name) for name in state.__dataclass_fields__ if name != "authoritative_state_version"}
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    return replace(state, authoritative_state_version=digest)


def project_resting_order_state(state: OwnOrderTruthState, lineage: Mapping[str, Any]) -> RestingOrderState:
    """Project only reconciled, blocker-free truth into existing lifecycle state."""
    if not state.clean:
        raise ValueError("cannot project unreconciled or blocked own-order truth")
    if state.requested_shares is None or state.remaining_shares is None:
        raise ValueError("cannot project state without requested size")
    values = dict(lineage)
    values.update({"order_id": state.expected_order_id, "expected_venue_order_id": state.expected_order_id, "requested_shares": state.requested_shares, "matched_shares": state.effective_matched_shares, "remaining_shares": state.remaining_shares, "status": state.status, "raw_venue_status": state.raw_status, "authoritative_state_version": state.authoritative_state_version, "lifecycle_owner": state.lifecycle_owner, "cancel_confirmed": state.status in {"CANCELLED", "EXPIRED"}, "order_state_provenance": "polymarket_own_order_truth"})
    return RestingOrderState(**values)
