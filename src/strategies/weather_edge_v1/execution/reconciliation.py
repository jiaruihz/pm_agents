"""Pure authoritative-order reconciliation and lifecycle identity helpers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .contracts import JsonContract, RestingOrderState, canonical_json


ReconciliationStatus = Literal["ready", "blocked", "terminal", "ambiguous"]
_AMBIGUOUS_STATUSES = frozenset({"unknown", "unavailable", "submit_unknown", "pending_lookup"})


@dataclass(frozen=True)
class OrderChain(JsonContract):
    root_order_id: str
    source_order_id: str


@dataclass(frozen=True)
class ReconciliationResult(JsonContract):
    status: ReconciliationStatus
    reason: str
    root_order_id: str | None
    source_order_id: str | None
    authoritative_state_version: str | None
    requested_shares: Decimal | None
    authoritative_matched_shares: Decimal | None
    authoritative_remaining_shares: Decimal | None
    lifecycle_action_id: str | None = None


def order_chain(order_state: RestingOrderState) -> OrderChain:
    root = order_state.root_order_id or order_state.order_id or order_state.client_order_id
    source = order_state.source_order_id or order_state.order_id or order_state.client_order_id
    return OrderChain(root_order_id=root, source_order_id=source)


def authoritative_remaining_shares(order_state: RestingOrderState | None) -> ReconciliationResult:
    if order_state is None:
        return ReconciliationResult("blocked", "authoritative_order_state_missing", None, None, None, None, None, None)
    chain = order_chain(order_state)
    status = order_state.status.lower().strip()
    if status in _AMBIGUOUS_STATUSES:
        return ReconciliationResult("ambiguous", "authoritative_order_state_unavailable", chain.root_order_id, chain.source_order_id, order_state.authoritative_state_version, order_state.requested_shares, None, None)
    if order_state.matched_shares > order_state.requested_shares:
        return ReconciliationResult("blocked", "authoritative_matched_exceeds_requested", chain.root_order_id, chain.source_order_id, order_state.authoritative_state_version, order_state.requested_shares, order_state.matched_shares, None)
    remaining = order_state.requested_shares - order_state.matched_shares
    terminal = remaining == 0 or status in {"filled", "rejected", "terminal"}
    return ReconciliationResult("terminal" if terminal else "ready", "terminal_order" if terminal else "authoritative_remaining_ready", chain.root_order_id, chain.source_order_id, order_state.authoritative_state_version, order_state.requested_shares, order_state.matched_shares, remaining)


def make_lifecycle_action_id(
    *,
    order_state: RestingOrderState,
    action: str,
    normalized_target_price: Decimal | None,
    remaining_shares: Decimal,
    data_epoch_ref: str | None,
    book_epoch_ref: str | None,
) -> str:
    chain = order_chain(order_state)
    if not order_state.authoritative_state_version:
        raise ValueError("authoritative_state_version is required for lifecycle_action_id")
    payload = {
        "action": str(action),
        "authoritative_state_version": order_state.authoritative_state_version,
        "book_epoch_ref": book_epoch_ref or "",
        "data_epoch_ref": data_epoch_ref or "",
        "normalized_target_price": normalized_target_price,
        "remaining_shares": remaining_shares,
        "root_order_id": chain.root_order_id,
        "source_order_id": chain.source_order_id,
    }
    return "lifecycle:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def reconcile_replacement(
    *,
    order_state: RestingOrderState | None,
    minimum_order_shares: Decimal,
    require_cancel_confirmation: bool,
    action: str | None = None,
    normalized_target_price: Decimal | None = None,
    data_epoch_ref: str | None = None,
    book_epoch_ref: str | None = None,
) -> ReconciliationResult:
    remaining = authoritative_remaining_shares(order_state)
    if order_state is None or remaining.status in {"blocked", "ambiguous"}:
        return remaining
    cancel_final = order_state.status.lower().strip() in {"cancelled", "canceled", "expired"}
    if require_cancel_confirmation and (not order_state.cancel_confirmed or not cancel_final):
        return ReconciliationResult("blocked", "cancel_not_authoritatively_confirmed", remaining.root_order_id, remaining.source_order_id, remaining.authoritative_state_version, remaining.requested_shares, remaining.authoritative_matched_shares, remaining.authoritative_remaining_shares)
    if remaining.authoritative_remaining_shares is None or remaining.authoritative_remaining_shares < minimum_order_shares:
        return ReconciliationResult("blocked", "remaining_shares_below_venue_minimum", remaining.root_order_id, remaining.source_order_id, remaining.authoritative_state_version, remaining.requested_shares, remaining.authoritative_matched_shares, remaining.authoritative_remaining_shares)
    if not action:
        return remaining
    if remaining.authoritative_state_version is None:
        return ReconciliationResult("blocked", "authoritative_state_version_missing", remaining.root_order_id, remaining.source_order_id, None, remaining.requested_shares, remaining.authoritative_matched_shares, remaining.authoritative_remaining_shares)
    action_id = make_lifecycle_action_id(order_state=order_state, action=action, normalized_target_price=normalized_target_price, remaining_shares=remaining.authoritative_remaining_shares, data_epoch_ref=data_epoch_ref, book_epoch_ref=book_epoch_ref)
    return ReconciliationResult(remaining.status, remaining.reason, remaining.root_order_id, remaining.source_order_id, remaining.authoritative_state_version, remaining.requested_shares, remaining.authoritative_matched_shares, remaining.authoritative_remaining_shares, action_id)
