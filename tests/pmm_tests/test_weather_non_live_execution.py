from src.strategies.weather_edge_v1.execution.contracts import (
    ChildOrderPlan,
    ExecutionConstraints,
    ExecutionIntent,
    ExecutionRunContext,
)
from src.strategies.weather_edge_v1.runtime.execution_journal import JsonlExecutionJournal
from src.strategies.weather_edge_v1.runtime.non_live import (
    ContractRisk,
    NonLiveOrderSpec,
    NonLivePaperVenue,
    market_book_from_legacy_plan,
)
from src.strategies.weather_edge_v1.runtime.order_runtime import OrderRuntime


def test_non_live_paper_venue_has_no_network_and_preserves_preplanned_quote(tmp_path):
    plan = {
        "token_id": "token-1",
        "created_at_utc": "2026-08-02T10:00:00Z",
        "tick_size": 0.001,
        "fixed_order_shares": 5,
        "size": 5,
        "best_bid": 0.10,
        "best_ask": 0.11,
        "maker_limit_price": 0.101,
    }
    book = market_book_from_legacy_plan(plan)
    intent = ExecutionIntent(
        execution_schema_version="weather_execution_v1",
        signal_id="signal-1",
        opportunity_id="opportunity-1",
        comparison_group_id="comparison-1",
        strategy_id="strategy-1",
        strategy_instance="paper-1",
        config_id="config-1",
        execution_profile="single_side_maker_v1",
        resolved_execution_profile="single_side_maker_v1",
        execution_config_id="exec-config-1",
        plan_dedupe_key="plan-key-1",
        live_exposure_key="exposure-key-1",
        token_id="token-1",
        venue_side="BUY",
        outcome_side="YES",
        signal_side="BUY_YES",
        total_shares=5,
        created_at_utc="2026-08-02T10:00:00Z",
        constraints=ExecutionConstraints(price_cap="0.101", minimum_shares=5, maximum_shares=5),
    )
    child = ChildOrderPlan(
        intent=intent,
        child_role="maker_first",
        requested_shares=5,
        execution_policy="maker_queue_v2",
        order_lifecycle_policy="maker_until_data_update",
        maker_only=True,
    )
    venue = NonLivePaperVenue({"token-1": NonLiveOrderSpec(book)})
    runtime = OrderRuntime(
        venue=venue,
        risk=ContractRisk(),
        journal=JsonlExecutionJournal(tmp_path / "execution.jsonl", writer_id="paper-1"),
        planner=lambda *args: [],
    )
    context = ExecutionRunContext(
        run_id="paper-run-1",
        execution_mode="paper",
        run_purpose="shadow",
        dry_run=False,
        confirm_live=False,
        pause_state="paused",
        authorization_ref=None,
        runtime_owner="paper-1",
        code_commit="test",
        invoked_at_utc="2026-08-02T10:00:00Z",
    )

    result = runtime.submit_preplanned(intent, child, context)

    assert result.actions[0].status == "submitted"
    assert venue.place_calls == [
        {
            "status": "accepted",
            "execution_mode": "paper",
            "venue": "non_live_paper",
            "client_order_id": "plan-key-1:maker_first",
            "requested_price": "0.101",
            "requested_shares": "5",
            "maker_only": True,
            "book_epoch_ref": "legacy_plan_snapshot",
            "fee_schedule_ref": "non_live_fee_v1",
            "replacement_of": None,
        }
    ]
