from decimal import Decimal

import pytest

from src.strategies.weather_edge_v1.execution.contracts import BookLevel, ExecutionContractError, FeeSchedule, MarketBook, VenueCapabilities
from src.strategies.weather_edge_v1.execution.selective_maker import (
    AuthorizedRoutePlan,
    PITMarketState,
    PassiveFillEstimate,
    RealizedIncentive,
    RealizedIncentiveLedger,
    RealizedPayout,
    RouteInput,
    StageEvidence,
    TinyLiveAuthorization,
    TransitionHazardEstimate,
    build_authorized_route_plan,
    classify_pit_state,
    route_candidate,
)


def book(*, bid="0.40", ask="0.43", bids=True, asks=True):
    return MarketBook("yes", "ok", "2026-08-30T00:00:00Z", None, "epoch-1", "0.01", "fixture", "1",
        (BookLevel(bid, "10"),) if bids else (), (BookLevel(ask, "10"),) if asks else ())


def fee():
    return FeeSchedule("clob", "fee-1", "2026-08-30T00:00:00Z", "formula", {}, {})


def caps():
    return VenueCapabilities("clob", "p1", "c1", "pUSD", ("GTC",), ("GTC",), 2, 2, {"GTC": 2}, 1, "2026-08-30T00:00:00Z", "fee-1")


def pit(**kwargs):
    values = dict(candidate_id="c-1", feature_epoch_ref="pit-1", market_book=book(), book_age_sec="1", max_book_age_sec="5", coverage_complete=True)
    values.update(kwargs)
    return PITMarketState(**values)


def fill_estimate(*, probability="0.95", conservative="0.95", evidence_kind="actual_own_order_calibrated"):
    return PassiveFillEstimate(
        model_id="fill-model-1",
        feature_epoch_ref="pit-1",
        horizon_seconds=30,
        probability=probability,
        conservative_probability=conservative,
        evidence_kind=evidence_kind,
    )


def hazard_estimate(*, probability="0", upper=None, evidence_kind="actual_markout_calibrated"):
    return TransitionHazardEstimate(
        model_id="hazard-model-1",
        feature_epoch_ref="pit-1",
        horizon_seconds=30,
        probability=probability,
        conservative_upper_probability=probability if upper is None else upper,
        evidence_kind=evidence_kind,
    )


def live_authorization(**overrides):
    values = dict(
        authorization_ref="approve-1", confirm_live=True, manifest_healthy=True,
        collateral_asset="pUSD", expected_collateral_asset="pUSD", collateral_verified=True,
        requested_notional="1", notional_cap="2", active_notional="0",
        current_market_notional="0", market_notional_cap="2",
        current_city_date_notional="0", city_date_notional_cap="2",
        inventory_snapshot_ref="inventory-snapshot-1", inventory_snapshot_fresh=True,
        daily_submitted_notional="0", daily_notional_cap="2",
        current_daily_loss="0", daily_loss_cap="2",
        frozen_policy_id="policy-sha", frozen_config_id="cfg-sha",
        sole_reconciler_ready=True, cancel_all_ready=True,
        private_user_ws_ready=True, rest_reconciliation_ready=True,
        stage0_fee_truth_ready=True, stage0_own_order_truth_ready=True,
        release_identity_verified=True,
        stage=StageEvidence("micro_live_measurement", True),
    )
    values.update(overrides)
    return TinyLiveAuthorization(**values)


def test_state_is_pit_only_and_fails_closed_for_bad_market_evidence():
    assert classify_pit_state(pit()).state == "healthy_passive_candidate"
    assert classify_pit_state(pit(dislocation_observed=True)).state == "transient_dislocation_candidate"
    bad = classify_pit_state(pit(book_age_sec="6", coverage_complete=False, market_book=book(bids=False)))
    assert bad.state == "unknown"
    assert set(bad.blockers) == {"book_stale", "coverage_gap", "one_sided_book"}
    assert classify_pit_state(pit(market_book=book(bid="0.43", ask="0.43"))).blockers == ("book_locked_or_crossed",)


def test_same_candidate_has_maker_taker_skip_arms_and_maker_never_crosses():
    decision = route_candidate(RouteInput(
        pit(), "BUY", "2", fee(), caps(), fair_value="0.60",
        taker_fee_estimate="0.01", transition_hazard_estimate=hazard_estimate(),
        passive_fill_estimate=fill_estimate(),
    ))
    assert decision.selected_route == "maker"
    assert decision.maker and decision.maker.maker_only and decision.maker.normalized_price_exact == Decimal("0.41")
    assert decision.taker and decision.taker.expected_vwap == Decimal("0.43")
    assert decision.taker_all_in_cost == Decimal("0.87")
    assert decision.skip_incremental_cost == 0
    blocked_maker = route_candidate(RouteInput(
        pit(market_book=book(bid="0.40", ask="0.41")), "BUY", "1", fee(), caps(),
        fair_value="0.60", transition_hazard_estimate=hazard_estimate(),
        passive_fill_estimate=fill_estimate(),
    ))
    # A one-tick book is also outside the frozen passive regime, so the
    # selector retains the row but fails closed to skip.
    assert blocked_maker.selected_route == "taker"
    assert blocked_maker.maker and blocked_maker.maker.reason == "maker_would_cross"
    unknown = route_candidate(RouteInput(pit(coverage_complete=False), "BUY", "1", fee(), caps(), fair_value="0.60"))
    assert unknown.selected_route == "taker"
    assert "coverage_gap" in unknown.blockers
    assert unknown.identity == route_candidate(RouteInput(pit(coverage_complete=False), "BUY", "1", fee(), caps(), fair_value="0.60")).identity


def test_router_uses_weather_value_hazard_inventory_and_urgency():
    base = dict(
        pit=pit(), venue_side="BUY", shares="1", fee_schedule=fee(),
        venue_capabilities=caps(), transition_hazard_estimate=hazard_estimate(),
        passive_fill_estimate=fill_estimate(),
    )
    assert route_candidate(RouteInput(**base, fair_value="0.60")).selected_route == "maker"
    assert route_candidate(RouteInput(
        **{**base, "transition_hazard_estimate": hazard_estimate(probability="1")},
        fair_value="0.44", inventory_risk_per_share="0.01",
    )).selected_route == "skip"
    urgent = route_candidate(RouteInput(**base, fair_value="0.60", taker_urgency=True))
    assert urgent.selected_route == "taker"
    adverse = route_candidate(RouteInput(**{**base, "pit": pit(adverse_flow_observed=True)}, fair_value="0.60"))
    assert adverse.state.state == "unknown"
    assert adverse.selected_route == "taker"


def test_maker_wait_hazard_does_not_erase_immediate_taker_and_fill_model_is_required():
    base = dict(
        pit=pit(), venue_side="BUY", shares="1", fee_schedule=fee(),
        venue_capabilities=caps(), fair_value="0.60",
    )
    missing = route_candidate(RouteInput(
        **base, transition_hazard_estimate=hazard_estimate(),
    ))
    assert missing.selected_route == "taker"
    assert "maker:passive_fill_probability_missing" in missing.blockers

    prior_only = route_candidate(RouteInput(
        **base, transition_hazard_estimate=hazard_estimate(),
        passive_fill_estimate=fill_estimate(evidence_kind="measurement_prior"),
    ))
    assert prior_only.selected_route == "taker"
    assert "maker:passive_fill_probability_not_actual_calibrated" in prior_only.blockers

    hazard_prior_only = route_candidate(RouteInput(
        **base,
        transition_hazard_estimate=hazard_estimate(
            evidence_kind="measurement_prior"
        ),
        passive_fill_estimate=fill_estimate(),
    ))
    assert hazard_prior_only.selected_route == "taker"
    assert "maker:transition_hazard_not_actual_calibrated" in hazard_prior_only.blockers

    high_hazard = route_candidate(RouteInput(
        **base,
        transition_hazard_estimate=hazard_estimate(probability="1"),
        transition_hazard_penalty="0.20",
        passive_fill_estimate=fill_estimate(),
    ))
    assert high_hazard.selected_route == "taker"
    assert high_hazard.taker_risk_adjusted_fair_value == Decimal("0.595")
    assert high_hazard.maker_risk_adjusted_fair_value == Decimal("0.395")

    low_fill = route_candidate(RouteInput(
        **base, transition_hazard_estimate=hazard_estimate(),
        passive_fill_estimate=fill_estimate(probability="0.30", conservative="0.20"),
    ))
    assert low_fill.selected_route == "taker"
    assert low_fill.maker_expected_edge_per_share < low_fill.taker_edge_per_share

    with pytest.raises(ExecutionContractError, match="BUY acquisition only"):
        RouteInput(
            pit(), "SELL", "1", fee(), caps(), fair_value="0.60",
            transition_hazard_estimate=hazard_estimate(),
            passive_fill_estimate=fill_estimate(),
        )


def test_stage_prevents_circular_fill_gate_and_requires_forward_proof_only_later():
    measurement = StageEvidence("micro_live_measurement", zero_notional_ready=True, actual_maker_fills=0)
    assert measurement.blockers == ()
    frozen = StageEvidence(
        "frozen_forward_profitability", True, actual_maker_fills=19,
        forward_lower_bound_per_share="0.01",
        minimum_economic_return_per_share="0.005",
    )
    assert frozen.blockers == ("first_look_measurement_floor_not_met",)
    assert StageEvidence(
        "frozen_forward_profitability", True, actual_maker_fills=20,
        forward_lower_bound_per_share="0.005",
        minimum_economic_return_per_share="0.005",
    ).blockers == ("forward_profitability_not_proven",)
    assert StageEvidence(
        "frozen_forward_profitability", False, actual_maker_fills=20,
        forward_lower_bound_per_share="0.01",
        minimum_economic_return_per_share="0.005",
    ).blockers == ("zero_notional_readiness_incomplete",)


def test_live_authorization_is_default_fail_closed_and_needs_all_contracts():
    assert live_authorization().allowed
    denied = live_authorization(confirm_live=False, collateral_asset="USDC")
    assert not denied.allowed
    assert {"live_confirmation_missing", "collateral_asset_unverified"}.issubset(denied.blockers)
    exhausted = live_authorization(daily_submitted_notional="2")
    assert "daily_notional_cap_violation" in exhausted.blockers
    concentrated = live_authorization(
        current_market_notional="2", inventory_snapshot_fresh=False,
    )
    assert {
        "inventory_snapshot_stale",
        "market_notional_cap_violation",
    }.issubset(concentrated.blockers)


def test_authorized_route_plan_uses_sole_target_set_and_taker_cancel_handoff():
    base = dict(
        pit=pit(), venue_side="BUY", shares="1", fee_schedule=fee(),
        venue_capabilities=caps(), fair_value="0.60",
        transition_hazard_estimate=hazard_estimate(),
    )
    maker_decision = route_candidate(RouteInput(
        **base, passive_fill_estimate=fill_estimate(),
    ))
    maker_plan = build_authorized_route_plan(
        maker_decision,
        live_authorization(),
        owner_id="weather-mm-v21",
        strategy_head="weather-first-maker",
        capital_sleeve="measurement-only",
        generation=1,
        generated_at_utc="2026-08-30T00:00:00Z",
        continuation_policy_id="policy-sha",
    )
    assert isinstance(maker_plan, AuthorizedRoutePlan)
    assert maker_plan.target_order_set.mode == "NORMAL"
    assert maker_plan.target_order_set.targets[0].token_id == "yes"
    assert maker_plan.taker_quote is None

    taker_decision = route_candidate(RouteInput(
        **base,
        passive_fill_estimate=fill_estimate(
            probability="0.30", conservative="0.20"
        ),
    ))
    taker_plan = build_authorized_route_plan(
        taker_decision,
        live_authorization(),
        owner_id="weather-mm-v21",
        strategy_head="weather-first-maker",
        capital_sleeve="measurement-only",
        generation=2,
        generated_at_utc="2026-08-30T00:00:01Z",
        continuation_policy_id="policy-sha",
    )
    assert taker_plan.target_order_set.mode == "CANCEL_ONLY"
    assert taker_plan.taker_requires_clean_empty_maker_set is True
    assert taker_plan.taker_quote is taker_decision.taker
    with pytest.raises(ExecutionContractError, match="authorization blocked"):
        build_authorized_route_plan(
            maker_decision,
            live_authorization(confirm_live=False),
            owner_id="weather-mm-v21",
            strategy_head="weather-first-maker",
            capital_sleeve="measurement-only",
            generation=3,
            generated_at_utc="2026-08-30T00:00:02Z",
            continuation_policy_id="policy-sha",
        )


def test_realized_incentives_are_immutable_deduplicated_and_not_estimates():
    rebate = RealizedIncentive("maker_rebate", "payout-1", "pUSD", "1.25", "fill-1")
    reward = RealizedIncentive("liquidity_reward", "payout-2", "pUSD", "0.25", "quote-1")
    payout1 = RealizedPayout("payout-1", "pUSD", "1.25")
    payout2 = RealizedPayout("payout-2", "pUSD", "0.25")
    ledger = RealizedIncentiveLedger((reward, rebate), (payout2, payout1))
    assert ledger.total == Decimal("1.50")
    assert ledger.net_economic(realized_trading_pnl="2", realized_costs="0.5") == Decimal("3")
    assert [item.namespace for item in ledger.as_episode_credits()] == ["maker_rebate", "lp_reward"]
    assert ledger.identity == RealizedIncentiveLedger(
        (rebate, reward), (payout1, payout2)
    ).identity
    # One venue payout may legitimately be allocated across distinct immutable
    # eligibility rows; only the same allocation is a double count.
    shared_payout = RealizedIncentive("liquidity_reward", "payout-1", "pUSD", "0.10", "quote-2")
    combined_payout = RealizedPayout("payout-1", "pUSD", "1.35")
    assert RealizedIncentiveLedger(
        (rebate, shared_payout), (combined_payout,)
    ).total == Decimal("1.35")
    with pytest.raises(ExecutionContractError, match="duplicate"):
        RealizedIncentiveLedger((rebate, rebate), (payout1,))
    with pytest.raises(ExecutionContractError, match="exceed"):
        RealizedIncentiveLedger(
            (rebate, shared_payout), (payout1,)
        )
    with pytest.raises(ExecutionContractError, match="estimated"):
        RealizedIncentive("holding_reward", "payout-3", "pUSD", "1", "position-1", realization="estimated")
