from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

from .contracts import ExecutionProfile, LifecycleContext, LifecycleDecision, MarketBook, RestingOrderState
from .quote_engine import round_marketable_price_to_tick, round_price_to_tick
from .reconciliation import ReconciliationResult, authoritative_remaining_shares, reconcile_replacement


def _parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_data_update_lifecycle_fields(
    *,
    data_source: str,
    data_epoch_ref: str,
    data_epoch_ts_utc: str | datetime,
    next_data_update_due_utc: str | datetime,
    cancel_buffer_sec: int = 90,
    now: datetime | None = None,
) -> dict[str, Any]:
    source = str(data_source or "").strip()
    epoch_ref = str(data_epoch_ref or "").strip()
    if not source:
        raise ValueError("data_source is required")
    if not epoch_ref:
        raise ValueError("data_epoch_ref is required")
    epoch_ts = _parse_utc(data_epoch_ts_utc)
    update_due = _parse_utc(next_data_update_due_utc)
    buffer_sec = max(0, int(cancel_buffer_sec))
    cancel_at = update_due - timedelta(seconds=buffer_sec)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if update_due <= epoch_ts:
        raise ValueError("next_data_update_due_utc must be after data_epoch_ts_utc")
    if cancel_at <= current:
        raise ValueError("inside pre-data-update blackout; maker quote is not allowed")
    return {
        "data_update_source": source,
        "data_epoch_ref": epoch_ref,
        "data_epoch_ts_utc": epoch_ts.isoformat(),
        "next_data_update_due_utc": update_due.isoformat(),
        "cancel_before_data_update_utc": cancel_at.isoformat(),
        "expires_at_utc": cancel_at.isoformat(),
        "cancel_buffer_sec": buffer_sec,
        "cancel_reason": "pre_data_update",
        "post_update_reprice_required": True,
    }


def attach_data_update_lifecycle(
    signal: Mapping[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    return {**dict(signal), **build_data_update_lifecycle_fields(**kwargs)}


_TERMINAL_STATUSES = frozenset({"filled", "rejected", "terminal"})


def _now(value: str | datetime) -> datetime:
    return _parse_utc(value)


def _maker_leg(profile: ExecutionProfile, order_state: RestingOrderState):
    for leg in profile.legs:
        if leg.execution_policy == order_state.execution_policy:
            return leg
    return next((leg for leg in profile.legs if leg.maker_only), None)


def _decision(
    *,
    action: str,
    reason: str,
    reconciliation: ReconciliationResult,
    replacement_price: Decimal | None = None,
) -> LifecycleDecision:
    return LifecycleDecision(
        action=action,
        reason=reason,
        replacement_shares=reconciliation.authoritative_remaining_shares if action in {"REPRICE_MAKER", "REPOST_LOWER", "TAKER_FALLBACK"} else None,
        replacement_price=replacement_price,
        lifecycle_action_id=reconciliation.lifecycle_action_id,
        root_order_id=reconciliation.root_order_id,
        source_order_id=reconciliation.source_order_id,
        authoritative_state_version=reconciliation.authoritative_state_version,
    )


def _rest(reason: str, reconciliation: ReconciliationResult) -> LifecycleDecision:
    return _decision(action="REST", reason=reason, reconciliation=reconciliation)


def _action_reconciliation(
    *,
    order_state: RestingOrderState,
    market_book: MarketBook,
    lifecycle_context: LifecycleContext,
    action: str,
    target_price: Decimal | None,
    require_cancel_confirmation: bool,
) -> ReconciliationResult:
    return reconcile_replacement(
        order_state=order_state,
        minimum_order_shares=market_book.minimum_order_shares,
        require_cancel_confirmation=require_cancel_confirmation,
        action=action,
        normalized_target_price=target_price,
        data_epoch_ref=lifecycle_context.data_epoch_ref,
        book_epoch_ref=market_book.book_epoch_ref,
    )


def _cancel_decision(
    *,
    reason: str,
    order_state: RestingOrderState,
    market_book: MarketBook,
    lifecycle_context: LifecycleContext,
) -> LifecycleDecision:
    reconciliation = _action_reconciliation(order_state=order_state, market_book=market_book, lifecycle_context=lifecycle_context, action="CANCEL", target_price=None, require_cancel_confirmation=False)
    if reconciliation.status != "ready":
        return _rest(reconciliation.reason, reconciliation)
    return _decision(action="CANCEL", reason=reason, reconciliation=reconciliation)


def _maker_price(
    *,
    order_state: RestingOrderState,
    market_book: MarketBook,
    price_cap: Decimal,
) -> Decimal | None:
    if not market_book.bids or not market_book.asks:
        return None
    bid = market_book.bids[0].price
    ask = market_book.asks[0].price
    if order_state.venue_side == "BUY":
        target = round_price_to_tick(min(bid + market_book.tick_size, ask - market_book.tick_size, price_cap), market_book.tick_size, venue_side="BUY")
        return target if target < ask else None
    target = round_price_to_tick(max(ask - market_book.tick_size, bid + market_book.tick_size, price_cap), market_book.tick_size, venue_side="SELL")
    return target if target > bid else None


def _fallback_price(order_state: RestingOrderState, market_book: MarketBook, price_cap: Decimal) -> Decimal | None:
    levels = market_book.asks if order_state.venue_side == "BUY" else market_book.bids
    if not levels:
        return None
    target = round_marketable_price_to_tick(levels[0].price, market_book.tick_size, venue_side=order_state.venue_side)
    if order_state.venue_side == "BUY" and target > price_cap:
        return None
    if order_state.venue_side == "SELL" and target < price_cap:
        return None
    return target


def _taker_fallback_decision(
    *,
    initial: ReconciliationResult,
    order_state: RestingOrderState,
    market_book: MarketBook,
    lifecycle_context: LifecycleContext,
) -> LifecycleDecision:
    valid_fallback = all(
        (
            lifecycle_context.thesis_valid,
            lifecycle_context.token_unchanged,
            lifecycle_context.book_fresh,
            lifecycle_context.price_cap_valid,
            lifecycle_context.depth_valid,
            lifecycle_context.fee_adjusted_edge_valid,
            lifecycle_context.taker_price_cap is not None,
        )
    )
    if not valid_fallback:
        return _rest("taker_fallback_requirements_not_met", initial)
    target = _fallback_price(order_state, market_book, lifecycle_context.taker_price_cap)
    if target is None:
        return _rest("taker_fallback_price_cap_or_depth_block", initial)
    reconciliation = _action_reconciliation(order_state=order_state, market_book=market_book, lifecycle_context=lifecycle_context, action="TAKER_FALLBACK", target_price=target, require_cancel_confirmation=True)
    if reconciliation.status != "ready":
        return _rest(reconciliation.reason, reconciliation)
    return _decision(action="TAKER_FALLBACK", reason="maker_cancel_confirmed_and_fallback_valid", reconciliation=reconciliation, replacement_price=target)


def evaluate_order_lifecycle(
    *,
    profile: ExecutionProfile,
    order_state: RestingOrderState | None,
    market_book: MarketBook | None,
    lifecycle_context: LifecycleContext,
    now_utc: str | datetime,
) -> LifecycleDecision:
    """Return a pure lifecycle decision; runtime owns every side effect and retry."""
    initial = authoritative_remaining_shares(order_state)
    if order_state is None:
        return _rest("authoritative_order_state_missing", initial)
    if market_book is None:
        return _rest("fresh_market_book_missing", initial)
    if order_state.lifecycle_owner and lifecycle_context.lifecycle_owner and order_state.lifecycle_owner != lifecycle_context.lifecycle_owner:
        return _rest("lifecycle_owner_mismatch", initial)
    if initial.status == "ambiguous":
        return _rest("authoritative_order_state_unavailable", initial)
    if initial.status == "blocked":
        return _rest(initial.reason, initial)
    if order_state.status.lower().strip() in _TERMINAL_STATUSES or initial.authoritative_remaining_shares == 0:
        return _decision(action="TERMINAL", reason="terminal_order", reconciliation=initial)

    leg = _maker_leg(profile, order_state)
    if leg is None or not order_state.maker_only:
        return _rest("order_is_not_profile_maker_leg", initial)
    now = _now(now_utc)
    deadline = _now(lifecycle_context.deadline_utc) if lifecycle_context.deadline_utc else None
    deadline_passed = deadline is not None and now >= deadline
    fallback_enabled = "taker_fallback" in leg.order_lifecycle_policy
    cancel_final = order_state.status.lower().strip() in {"cancelled", "canceled", "expired"}
    data_epoch_changed = bool(order_state.data_epoch_ref and lifecycle_context.data_epoch_ref and order_state.data_epoch_ref != lifecycle_context.data_epoch_ref)
    if data_epoch_changed and profile.data_epoch_policy == "cancel":
        if cancel_final:
            if order_state.status.lower().strip() == "expired":
                return _decision(action="TERMINAL", reason="expired_order_data_epoch_changed", reconciliation=initial)
            return _rest("cancel_final_data_epoch_changed", initial)
        return _cancel_decision(reason="data_epoch_changed", order_state=order_state, market_book=market_book, lifecycle_context=lifecycle_context)
    if data_epoch_changed and (
        not lifecycle_context.token_unchanged or not lifecycle_context.thesis_valid
    ):
        if cancel_final:
            return _rest("cancel_final_invalidated_by_data_epoch", initial)
        return _cancel_decision(
            reason=(
                "token_changed"
                if not lifecycle_context.token_unchanged
                else "thesis_invalidated_by_data_epoch"
            ),
            order_state=order_state,
            market_book=market_book,
            lifecycle_context=lifecycle_context,
        )
    if cancel_final:
        if fallback_enabled and deadline_passed:
            if not order_state.cancel_confirmed:
                return _rest("cancel_final_fallback_cancel_unconfirmed", initial)
            return _taker_fallback_decision(initial=initial, order_state=order_state, market_book=market_book, lifecycle_context=lifecycle_context)
        if order_state.status.lower().strip() == "expired":
            return _decision(action="TERMINAL", reason="expired_order", reconciliation=initial)
        if fallback_enabled:
            return _rest("cancel_final_before_fallback_deadline", initial)
        return _rest("cancel_final_no_fallback_profile", initial)

    if deadline_passed:
        return _cancel_decision(reason="maker_deadline_reached", order_state=order_state, market_book=market_book, lifecycle_context=lifecycle_context)

    if lifecycle_context.repost_price is not None and lifecycle_context.repost_price < order_state.posted_price:
        if not (lifecycle_context.thesis_valid and lifecycle_context.token_unchanged and lifecycle_context.book_fresh):
            return _rest("maker_repost_requirements_not_met", initial)
        target = round_price_to_tick(lifecycle_context.repost_price, market_book.tick_size, venue_side=order_state.venue_side)
        reconciliation = _action_reconciliation(order_state=order_state, market_book=market_book, lifecycle_context=lifecycle_context, action="REPOST_LOWER", target_price=target, require_cancel_confirmation=False)
        if reconciliation.status != "ready":
            return _rest(reconciliation.reason, reconciliation)
        return _decision(action="REPOST_LOWER", reason="strategy_supplied_lower_repost", reconciliation=reconciliation, replacement_price=target)
    if leg.reprice_policy == "none":
        return _rest("static_maker_profile", initial)
    if not (lifecycle_context.thesis_valid and lifecycle_context.token_unchanged and lifecycle_context.book_fresh and lifecycle_context.price_cap_valid and lifecycle_context.maker_price_cap is not None):
        return _rest("maker_reprice_requirements_not_met", initial)
    if leg.max_reprices is not None and order_state.reprice_count >= leg.max_reprices:
        return _rest("maker_reprice_limit_reached", initial)
    target = _maker_price(order_state=order_state, market_book=market_book, price_cap=lifecycle_context.maker_price_cap)
    if target is None:
        return _rest("no_valid_resting_reprice", initial)
    if order_state.venue_side == "BUY" and target <= order_state.posted_price:
        return _rest("maker_price_not_improved", initial)
    if order_state.venue_side == "SELL" and target >= order_state.posted_price:
        return _rest("maker_price_not_improved", initial)
    reconciliation = _action_reconciliation(order_state=order_state, market_book=market_book, lifecycle_context=lifecycle_context, action="REPRICE_MAKER", target_price=target, require_cancel_confirmation=False)
    if reconciliation.status != "ready":
        return _rest(reconciliation.reason, reconciliation)
    return _decision(action="REPRICE_MAKER", reason="fresh_book_capped_maker_reprice", reconciliation=reconciliation, replacement_price=target)
