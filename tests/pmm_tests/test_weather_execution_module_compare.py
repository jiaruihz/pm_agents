from scripts.analysis.execution_quality.weather_execution_module_compare import (
    paired_taker_maker,
    summarize,
)


def _chain(*, role: str, maker: bool, price: float | None) -> dict:
    filled = 5.0 if price is not None else 0.0
    return {
        "instance_id": "h1",
        "execution_profile": "split_taker_maker_chase_v1",
        "root_execution_policy": f"{role}_policy",
        "root_child_order_role": role,
        "root_maker_only": int(maker),
        "signal_id": "signal-1",
        "comparison_group_id": "comparison-1",
        "city": "Amsterdam",
        "target_date": "2026-07-16",
        "planned_shares": 5.0,
        "planned_notional": 4.9,
        "filled_shares": filled,
        "fill_rate_shares": filled / 5.0,
        "fill_cost_usd": filled * (price or 0.0),
        "fees_usd": 0.0,
        "average_fill_price": price,
        "first_fill_latency_sec": 1.0 if price is not None else None,
        "settled_fill_shares": filled,
        "settled_fill_cost_usd": filled * (price or 0.0),
        "realized_pnl_usd": filled * (1.0 - (price or 0.0)) if price is not None else None,
        "settlement_available": True,
        "planned_counterfactual_pnl_at_root_limit": 0.1,
        "unfilled_counterfactual_pnl_at_root_limit": 0.0 if price is not None else 0.1,
        "route_policies": [f"{role}_policy"],
    }


def test_paired_taker_maker_uses_same_comparison_group_and_reports_saving():
    report = paired_taker_maker(
        [_chain(role="taker", maker=False, price=0.98), _chain(role="maker", maker=True, price=0.97)]
    )

    assert report["paired_opportunities"] == 1
    assert report["maker_pair_fill_rate"] == 1.0
    assert report["average_maker_price_improvement"] == 0.01
    assert report["gross_price_saving_usd"] == 0.05


def test_summary_keeps_unfilled_maker_in_planned_denominator():
    report = summarize([_chain(role="maker", maker=True, price=None)])[0]

    assert report["planned_shares"] == 5.0
    assert report["filled_shares"] == 0.0
    assert report["fill_rate_shares"] == 0.0
    assert report["resolved_root_chains"] == 1
    assert report["unfilled_counterfactual_pnl_at_root_limit"] == 0.1
