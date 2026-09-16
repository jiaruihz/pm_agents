import json
from decimal import Decimal

import pytest

from src.strategies.weather_edge_v1.execution.contracts import (
    EXECUTION_SCHEMA_VERSION,
    BookLevel,
    ExecutionConstraints,
    ExecutionContractError,
    ExecutionIntent,
    ExecutionRunContext,
    FeeSchedule,
    MarketBook,
    VenueCapabilities,
    make_execution_config_id,
    make_live_exposure_key,
    make_plan_dedupe_key,
)


def _fixed_behavior():
    return {
        "allocation_policy": "fixed_weight_split",
        "fee_model_version": "fee-v1",
        "refresh_sec": 15,
        "tick_model_version": "tick-v1",
    }


def _intent(**overrides):
    config_id = make_execution_config_id(
        resolved_execution_profile="split_taker_maker_chase_v1",
        fixed_behavior=_fixed_behavior(),
    )
    values = {
        "execution_schema_version": EXECUTION_SCHEMA_VERSION,
        "signal_id": "signal-1",
        "opportunity_id": "opportunity-1",
        "comparison_group_id": "comparison-1",
        "strategy_id": "weather-edge",
        "strategy_instance": "weather-edge-shadow",
        "config_id": "strategy-config-1",
        "execution_profile": "split_taker_maker_chase_v1",
        "resolved_execution_profile": "split_taker_maker_chase_v1",
        "execution_config_id": config_id,
        "plan_dedupe_key": make_plan_dedupe_key(
            strategy_id="weather-edge",
            strategy_instance="weather-edge-shadow",
            config_id="strategy-config-1",
            opportunity_id="opportunity-1",
            execution_profile="split_taker_maker_chase_v1",
            child_role="maker",
        ),
        "live_exposure_key": make_live_exposure_key(
            authorized_scope="weather-edge",
            opportunity_id="opportunity-1",
            token_id="token-1",
            venue_side="BUY",
            outcome_side="YES",
        ),
        "token_id": "token-1",
        "venue_side": "BUY",
        "outcome_side": "YES",
        "signal_side": "YES",
        "total_shares": "5.000",
        "created_at_utc": "2026-07-26T08:00:00Z",
        "constraints": ExecutionConstraints(
            price_floor="0.01",
            price_cap="0.91",
            required_edge="0.03",
            minimum_shares="1",
        ),
    }
    values.update(overrides)
    return ExecutionIntent(**values)


def test_execution_intent_is_mode_neutral_immutable_and_decimal_json_safe():
    intent = _intent(metadata={"research_tag": "phase1"})

    assert intent.total_shares == Decimal("5.000")
    assert intent.to_json()["total_shares"] == "5.000"
    assert intent.to_json()["constraints"]["price_cap"] == "0.91"
    assert "execution_mode" not in intent.to_json()
    assert ExecutionIntent.from_json(intent.to_json()) == intent
    assert json.loads(json.dumps(intent.to_json()))["metadata"] == {"research_tag": "phase1"}
    with pytest.raises(TypeError):
        intent.metadata["research_tag"] = "changed"


@pytest.mark.parametrize("field", ["live_enabled", "execution_mode", "confirm_live", "authorization_ref"])
def test_intent_metadata_and_profile_parameters_cannot_enable_live(field):
    with pytest.raises(ExecutionContractError, match="cannot control execution mode"):
        _intent(metadata={field: True})
    with pytest.raises(ExecutionContractError, match="cannot control execution mode"):
        _intent(profile_parameters={field: True})


def test_execution_run_context_is_the_only_live_gate():
    paper = ExecutionRunContext(
        run_id="paper-run",
        execution_mode="paper",
        run_purpose="shadow",
        dry_run=False,
        confirm_live=False,
        pause_state="paused",
        authorization_ref=None,
        runtime_owner="phase1-test",
        code_commit="test",
        invoked_at_utc="2026-07-26T08:00:00Z",
    )
    assert paper.to_json()["execution_mode"] == "paper"

    base = dict(
        run_id="live-run",
        execution_mode="live",
        run_purpose="live_probe",
        dry_run=False,
        confirm_live=True,
        pause_state="unpaused",
        authorization_ref="approved-deployment-1",
        runtime_owner="phase1-test",
        code_commit="test",
        invoked_at_utc="2026-07-26T08:00:00Z",
    )
    assert ExecutionRunContext(**base).to_json()["authorization_ref"] == "approved-deployment-1"
    for field, value in (("dry_run", True), ("confirm_live", False), ("pause_state", "paused"), ("authorization_ref", None)):
        with pytest.raises(ExecutionContractError):
            ExecutionRunContext(**{**base, field: value})


def test_market_book_preserves_full_depth_epoch_and_exact_decimal_json():
    book = MarketBook(
        token_id="token-1",
        status="ok",
        fetched_at_utc="2026-07-26T08:00:00Z",
        venue_timestamp_utc="2026-07-26T07:59:59Z",
        book_epoch_ref="tick-001-reseed-7",
        tick_size="0.001",
        tick_size_source="venue-instrument",
        minimum_order_shares="5",
        bids=(BookLevel("0.800", "10"), BookLevel("0.799", "20")),
        asks=(BookLevel("0.801", "8"), BookLevel("0.802", "30")),
    )

    assert book.to_json()["bids"][1] == {"price": "0.799", "size": "20"}
    assert MarketBook.from_json(book.to_json()) == book
    with pytest.raises(ExecutionContractError, match="asks must be ordered"):
        MarketBook(
            token_id=book.token_id,
            status=book.status,
            fetched_at_utc=book.fetched_at_utc,
            venue_timestamp_utc=book.venue_timestamp_utc,
            book_epoch_ref=book.book_epoch_ref,
            tick_size=book.tick_size,
            tick_size_source=book.tick_size_source,
            minimum_order_shares=book.minimum_order_shares,
            bids=book.bids,
            asks=(BookLevel("0.802", "8"), BookLevel("0.801", "30")),
        )


def test_fee_and_venue_capability_provenance_round_trip():
    fees = FeeSchedule(
        venue="polymarket_clob",
        fee_schedule_ref="fees-2026-07-26",
        fee_schedule_fetched_at_utc="2026-07-26T08:00:00Z",
        fee_formula_id="conditional-fee-v1",
        taker_fee_parameters={"rate": "0.05"},
        maker_fee_parameters={"rate": "0"},
        maker_rebate_program="unconfirmed",
    )
    capabilities = VenueCapabilities(
        venue="polymarket_clob",
        protocol_version="clob-v2",
        client_version="py-clob-client-v2",
        collateral_asset="USDC",
        supported_order_types=("GTC", "GTD"),
        post_only_order_types=("GTD",),
        price_precision=3,
        size_precision=6,
        amount_precision_by_order_type={"GTC": 6, "GTD": 6},
        gtd_security_threshold_sec=60,
        capabilities_fetched_at_utc="2026-07-26T08:00:00Z",
        fee_schedule_ref=fees.fee_schedule_ref,
    )

    assert json.loads(json.dumps(fees.to_json()))["fee_formula_id"] == "conditional-fee-v1"
    assert capabilities.to_json()["post_only_order_types"] == ["GTD"]


def test_execution_config_excludes_transient_inputs_but_keys_separate_plans_and_exposure():
    first = make_execution_config_id(
        resolved_execution_profile="split_taker_maker_chase_v1",
        fixed_behavior=_fixed_behavior(),
    )
    second = make_execution_config_id(
        resolved_execution_profile="split_taker_maker_chase_v1",
        fixed_behavior={"tick_model_version": "tick-v1", "refresh_sec": 15, "fee_model_version": "fee-v1", "allocation_policy": "fixed_weight_split"},
    )
    changed = make_execution_config_id(
        resolved_execution_profile="split_taker_maker_chase_v1",
        fixed_behavior={**_fixed_behavior(), "refresh_sec": 30},
    )

    assert first == second
    assert first != changed
    with pytest.raises(ExecutionContractError, match="transient"):
        make_execution_config_id(
            resolved_execution_profile="split_taker_maker_chase_v1",
            fixed_behavior={**_fixed_behavior(), "total_shares": "10"},
        )

    small = _intent(total_shares="5", strategy_price_cap="0.91", next_data_update_due_utc="2026-07-26T08:30:00Z")
    large = _intent(total_shares="10", strategy_price_cap="0.95", next_data_update_due_utc="2026-07-26T08:45:00Z")
    assert small.execution_config_id == large.execution_config_id == first
    assert _intent(metadata={"comparison_note": "A"}).execution_config_id == _intent(
        metadata={"comparison_note": "B"}
    ).execution_config_id == first
    assert make_plan_dedupe_key(
        strategy_id="weather-edge",
        strategy_instance="weather-edge-shadow",
        config_id="strategy-config-1",
        opportunity_id="opportunity-1",
        execution_profile="taker_now_v1",
        child_role="taker",
    ) != small.plan_dedupe_key
    assert make_live_exposure_key(
        authorized_scope="weather-edge",
        opportunity_id="opportunity-1",
        token_id="token-1",
        venue_side="BUY",
        outcome_side="YES",
    ) == small.live_exposure_key
