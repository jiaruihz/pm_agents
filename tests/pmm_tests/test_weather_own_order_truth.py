from decimal import Decimal

import pytest

from src.strategies.weather_edge_v1.execution.own_order_truth import (
    OwnOrderTruthReducer,
    normalize_own_order_observation,
    project_resting_order_state,
)


ORDER = "ord-1"
OWNER = "owner-1"


def obs(source, payload, at):
    return normalize_own_order_observation(source, payload, received_at_utc=at)


def order(status="LIVE", matched="0", size="10", at="2026-08-30T00:00:00Z", source="user_ws_order"):
    return obs(source, {"event_type": "order", "id": ORDER, "original_size": size, "size_matched": matched, "status": status, "timestamp": at}, at)


def trade(*, taker=True, amount="3", trade_id="trade-1", status="MATCHED", at="2026-08-30T00:00:01Z"):
    payload = {"event_type": "trade", "id": trade_id, "status": status, "timestamp": at}
    if taker:
        payload.update({"taker_order_id": ORDER, "size": amount})
    else:
        payload["maker_orders"] = [{"order_id": ORDER, "matched_amount": amount}, {"order_id": "other", "matched_amount": "99"}]
    return obs("user_ws_trade", payload, at)


def rest(status="LIVE", matched="0", at="2026-08-30T00:00:02Z", size="10"):
    return obs("rest_order", {"id": ORDER, "original_size": size, "size_matched": matched, "status": status, "timestamp": at}, at)


def reducer():
    return OwnOrderTruthReducer(expected_order_id=ORDER, lifecycle_owner=OWNER)


def test_partial_trade_duplicate_and_rest_parity():
    state = reducer().replay([order(), trade(), trade(), rest(matched="3")])
    assert state.effective_matched_shares == Decimal("3")
    assert state.trade_matched_shares == Decimal("3")
    assert not state.reconciliation_required
    assert not state.blockers


def test_absolute_evidence_and_same_trade_do_not_double_count():
    state = reducer().replay([order(matched="3"), trade(amount="3"), rest(matched="3")])
    assert state.effective_matched_shares == Decimal("3")


def test_stale_rest_does_not_close_websocket_reconciliation():
    state = reducer().replay([order(at="2026-08-30T00:00:02Z"), rest(at="2026-08-30T00:00:01Z")])
    assert state.reconciliation_required


def test_rest_received_later_must_also_cover_trade_evidence():
    state = reducer().replay([order(), trade(), rest(matched="0")])
    assert state.reconciliation_required
    state = reducer().reduce(state, rest(matched="3", at="2026-08-30T00:00:03Z"))
    assert not state.reconciliation_required


def test_received_clocks_compare_as_instants_not_lexical_strings():
    state = reducer().replay(
        [
            order(at="2026-08-30T01:00:00+01:00"),
            rest(at="2026-08-30T00:00:01Z"),
        ]
    )
    assert not state.reconciliation_required


@pytest.mark.parametrize("taker", [True, False])
def test_taker_and_maker_relations(taker):
    state = reducer().replay([order(), trade(taker=taker), rest(matched="3")])
    assert state.effective_matched_shares == Decimal("3")


def test_partial_fill_then_cancel_is_terminal_with_remaining():
    state = reducer().replay([order(), trade(), order("CANCELED", "3", at="2026-08-30T00:00:02Z"), rest("CANCELLED", "3", at="2026-08-30T00:00:03Z")])
    assert state.terminal and state.status == "CANCELLED"
    assert state.remaining_shares == Decimal("7")


def test_late_trade_after_cancel_updates_quantity_without_reopening():
    state = reducer().replay(
        [
            order(),
            order("CANCELLED", "3", at="2026-08-30T00:00:02Z"),
            trade(amount="4", at="2026-08-30T00:00:03Z"),
        ]
    )
    assert state.terminal and state.status == "CANCELLED"
    assert state.effective_matched_shares == Decimal("4")
    assert state.remaining_shares == Decimal("6")


def test_partial_matched_status_remains_live_until_full():
    state = reducer().replay([order("MATCHED", "3"), rest("LIVE", "3")])
    assert state.status == "LIVE"
    assert not state.terminal


@pytest.mark.parametrize(
"events,code",
[
    ([order(matched="11")], "matched_exceeds_requested"),
    ([order(size="10"), order(size="11", at="2026-08-30T00:00:01Z")], "requested_size_conflict"),
    ([order(matched="4"), order(matched="3", at="2026-08-30T00:00:01Z")], "absolute_regression"),
    ([obs("user_ws_order", {"id": "wrong", "original_size": "10", "size_matched": "0", "status": "LIVE"}, "2026-08-30T00:00:00Z")], "order_mismatch"),
    ([order("CANCELLED"), order("LIVE", at="2026-08-30T00:00:01Z")], "terminal_reopening"),
],
)
def test_anomalies_are_blockers(events, code):
    state = reducer().replay(events)
    assert code in {blocker.code for blocker in state.blockers}


def test_owner_mismatch_and_malformed_row_are_blockers():
    bad_owner = obs("user_ws_order", {"id": ORDER, "original_size": "10", "size_matched": "0", "status": "LIVE", "lifecycle_owner": "another"}, "2026-08-30T00:00:00Z")
    malformed = obs("rest_order", {"id": ORDER, "original_size": 10.0, "size_matched": "0", "status": "LIVE"}, "2026-08-30T00:00:01Z")
    codes = {item.code for item in reducer().replay([bad_owner, malformed]).blockers}
    assert {"owner_mismatch", "malformed_row"} <= codes


def test_venue_owner_is_separate_from_lifecycle_owner_and_can_be_pinned():
    raw = {
        "event_type": "order",
        "id": ORDER,
        "original_size": "10",
        "size_matched": "0",
        "status": "LIVE",
        "owner": "api-owner",
    }
    observation = obs("user_ws_order", raw, "2026-08-30T00:00:00Z")
    good = OwnOrderTruthReducer(
        expected_order_id=ORDER,
        lifecycle_owner=OWNER,
        expected_venue_owner="api-owner",
    ).replay([observation])
    assert not good.blockers
    bad = OwnOrderTruthReducer(
        expected_order_id=ORDER,
        lifecycle_owner=OWNER,
        expected_venue_owner="different-api-owner",
    ).replay([observation])
    assert "venue_owner_mismatch" in {item.code for item in bad.blockers}


def test_exact_replay_is_deterministic_and_state_version_is_stable():
    events = [order(), trade(), rest(matched="3")]
    assert reducer().replay(events) == reducer().replay(events)
    assert reducer().replay(events).authoritative_state_version == reducer().replay(events).authoritative_state_version


def test_projection_is_gated_until_reconciled_and_uses_immutable_lineage():
    state = reducer().replay([order(), trade()])
    with pytest.raises(ValueError):
        project_resting_order_state(state, {})
    state = reducer().replay([order(), trade(), rest(matched="3")])
    projected = project_resting_order_state(state, {
        "client_order_id": "client-1", "root_order_id": None, "source_order_id": None,
        "plan_id": "plan-1", "token_id": "token-1", "venue_side": "BUY", "outcome_side": "YES",
        "posted_price": "0.5", "created_at_utc": "2026-08-30T00:00:00Z", "maker_only": True,
        "execution_profile": "p", "execution_policy": "maker", "order_lifecycle_policy": "keep", "reprice_count": 0,
    })
    assert projected.matched_shares == Decimal("3")


def test_cancel_projection_carries_authoritative_confirmation():
    state = reducer().replay(
        [order("CANCELLED"), rest("CANCELLED", at="2026-08-30T00:00:02Z")]
    )
    projected = project_resting_order_state(state, {
        "client_order_id": "client-1", "root_order_id": None, "source_order_id": None,
        "plan_id": "plan-1", "token_id": "token-1", "venue_side": "BUY", "outcome_side": "YES",
        "posted_price": "0.5", "created_at_utc": "2026-08-30T00:00:00Z", "maker_only": True,
        "execution_profile": "p", "execution_policy": "maker", "order_lifecycle_policy": "keep", "reprice_count": 0,
    })
    assert projected.cancel_confirmed is True
