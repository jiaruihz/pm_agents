from decimal import Decimal

from src.strategies.weather_edge_v1.execution.contracts import RestingOrderState
from src.strategies.weather_edge_v1.execution.reconciliation import (
    authoritative_remaining_shares,
    order_chain,
    reconcile_replacement,
)


def _order(**overrides):
    values = {
        "order_id": "venue-order-1",
        "client_order_id": "client-order-1",
        "expected_venue_order_id": "venue-order-1",
        "root_order_id": "root-order-1",
        "source_order_id": "source-order-1",
        "plan_id": "plan-1",
        "token_id": "token-1",
        "venue_side": "BUY",
        "outcome_side": "YES",
        "requested_shares": "10",
        "matched_shares": "4",
        "remaining_shares": "5",
        "posted_price": "0.50",
        "status": "cancelled",
        "created_at_utc": "2026-07-26T08:00:00Z",
        "maker_only": True,
        "execution_profile": "split_taker_maker_chase_v1",
        "execution_policy": "maker-v1",
        "order_lifecycle_policy": "maker_chase_then_taker_fallback_v1",
        "reprice_count": 1,
        "data_epoch_ref": "metar:old",
        "authoritative_state_version": "order-state-v2",
        "lifecycle_owner": "owner-a",
        "cancel_confirmed": True,
    }
    values.update(overrides)
    return RestingOrderState(**values)


def test_authoritative_partial_fill_remaining_and_cancel_race_are_exact_and_idempotent():
    partial = _order()
    remaining = authoritative_remaining_shares(partial)
    assert remaining.status == "ready"
    assert remaining.authoritative_remaining_shares == Decimal("6")
    assert remaining.root_order_id == "root-order-1"
    assert remaining.source_order_id == "source-order-1"

    first = reconcile_replacement(
        order_state=partial,
        minimum_order_shares=Decimal("1"),
        require_cancel_confirmation=True,
        action="TAKER_FALLBACK",
        normalized_target_price=Decimal("0.60"),
        data_epoch_ref="metar:new",
        book_epoch_ref="book-v7",
    )
    replay = reconcile_replacement(
        order_state=partial,
        minimum_order_shares=Decimal("1"),
        require_cancel_confirmation=True,
        action="TAKER_FALLBACK",
        normalized_target_price=Decimal("0.60"),
        data_epoch_ref="metar:new",
        book_epoch_ref="book-v7",
    )
    assert first.status == "ready"
    assert first.authoritative_remaining_shares == Decimal("6")
    assert first.lifecycle_action_id == replay.lifecycle_action_id

    raced_fill = _order(matched_shares="5", remaining_shares="5", authoritative_state_version="order-state-v3")
    raced = reconcile_replacement(
        order_state=raced_fill,
        minimum_order_shares=Decimal("1"),
        require_cancel_confirmation=True,
        action="TAKER_FALLBACK",
        normalized_target_price=Decimal("0.60"),
        data_epoch_ref="metar:new",
        book_epoch_ref="book-v7",
    )
    assert raced.authoritative_remaining_shares == Decimal("5")
    assert raced.lifecycle_action_id != first.lifecycle_action_id
    changed_epoch = reconcile_replacement(
        order_state=partial,
        minimum_order_shares=Decimal("1"),
        require_cancel_confirmation=True,
        action="TAKER_FALLBACK",
        normalized_target_price=Decimal("0.60"),
        data_epoch_ref="metar:later",
        book_epoch_ref="book-v8",
    )
    assert changed_epoch.lifecycle_action_id != first.lifecycle_action_id


def test_reconciliation_blocks_missing_ambiguous_unconfirmed_and_dust_replacements():
    assert authoritative_remaining_shares(None).reason == "authoritative_order_state_missing"
    ambiguous = authoritative_remaining_shares(_order(status="unknown"))
    assert ambiguous.status == "ambiguous"
    unconfirmed = reconcile_replacement(
        order_state=_order(cancel_confirmed=False),
        minimum_order_shares=Decimal("1"),
        require_cancel_confirmation=True,
    )
    assert unconfirmed.reason == "cancel_not_authoritatively_confirmed"
    still_open = reconcile_replacement(
        order_state=_order(status="live", cancel_confirmed=True),
        minimum_order_shares=Decimal("1"),
        require_cancel_confirmation=True,
    )
    assert still_open.reason == "cancel_not_authoritatively_confirmed"
    dust = reconcile_replacement(
        order_state=_order(requested_shares="5", matched_shares="4.5", remaining_shares="0.5"),
        minimum_order_shares=Decimal("1"),
        require_cancel_confirmation=True,
    )
    assert dust.reason == "remaining_shares_below_venue_minimum"
    full = authoritative_remaining_shares(_order(status="filled", matched_shares="10", remaining_shares="0"))
    assert full.status == "terminal" and full.authoritative_remaining_shares == Decimal("0")
    assert order_chain(_order()).to_json() == {"root_order_id": "root-order-1", "source_order_id": "source-order-1"}
