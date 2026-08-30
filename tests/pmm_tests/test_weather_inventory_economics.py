from decimal import Decimal

import pytest

from src.strategies.weather_edge_v1.execution.contracts import ExecutionContractError
from src.strategies.weather_edge_v1.execution.inventory_economics import (
    ARM_NAMES,
    EpisodeLedger,
    IncentiveCredit,
    InventoryArmOutcome,
    PairedInventorySummary,
    PendingFillCorner,
    PositionLeg,
    TargetInventoryVector,
)


def vector(*, corners=()):
    yes = PositionLeg("cond-1", "yes", "5")
    no = PositionLeg("cond-1", "no", "5")
    return TargetInventoryVector(
        "cond-1",
        {"cold": (yes, no), "hot": (PositionLeg("cond-1", "yes", "0"), PositionLeg("cond-1", "no", "10"))},
        corners,
    )


def test_all_buy_and_all_sell_pending_corners_are_valid_and_deterministic():
    result = vector(corners=(
        PendingFillCorner("all-buy", buy_fills={"yes": "2"}),
        PendingFillCorner("all-sell", sell_fills={"no": "3"}),
    ))
    assert result.pending_fill_corners[0].sell_fills == {}
    assert result.pending_fill_corners[1].buy_fills == {}
    assert result.identity == vector(corners=(
        PendingFillCorner("all-buy", buy_fills={"yes": "2"}),
        PendingFillCorner("all-sell", sell_fills={"no": "3"}),
    )).identity


def test_partial_cancel_late_fill_envelope_accepts_nonnegative_independent_corners():
    result = vector(corners=(
        PendingFillCorner("partial", buy_fills={"yes": "2.5"}, sell_fills={"yes": "1"}),
        PendingFillCorner("late-fill-after-cancel", buy_fills={"no": "1"}),
    ))
    assert result.pending_fill_corners[0].buy_fills["yes"] == Decimal("2.5")


def test_vector_rejects_topology_mismatch_and_nonfinite_values():
    with pytest.raises(ExecutionContractError):
        TargetInventoryVector("c", {"a": (PositionLeg("c", "y", "1"),), "b": (PositionLeg("c", "n", "1"),)})
    with pytest.raises(ExecutionContractError):
        PendingFillCorner("bad", buy_fills={"yes": "NaN"})


def test_merge_has_no_extra_pnl_and_ledger_conserves_wealth():
    ledger = EpisodeLedger(
        opening_wealth="100",
        closing_wealth="108.60",
        external_cash_flow="0",
        core_trading_pnl="8",
        position_operation_pnl="0",
        incentives=(IncentiveCredit("maker_rebate", "0.50"), IncentiveCredit("lp_reward", "0.10")),
        rounding_residual="0",
        incremental_financing_cost="0.20",
        incremental_operating_cost="0.05",
    )
    assert ledger.position_operation_pnl == 0
    assert ledger.accounting_net_pnl == Decimal("8.60")
    assert ledger.accounting_component_total == Decimal("8.60")
    assert ledger.economic_profit == Decimal("8.35")
    assert ledger.realized_incentives["taker_rebate"] == 0


def test_ledger_rejects_estimated_incentive_and_bad_residual():
    with pytest.raises(ExecutionContractError):
        IncentiveCredit("maker_rebate", "1", realization="estimated")
    with pytest.raises(ExecutionContractError):
        EpisodeLedger("0", "2", core_trading_pnl="1", rounding_residual="0")


def outcome(arm, pnl=None):
    return InventoryArmOutcome(arm, pnl or {"s1": "1", "s2": "-2"}, capital_time="3", expected_shortfall_tail_count=1)


def test_five_arms_have_fixed_names_and_paired_risk_summary():
    summary = PairedInventorySummary({name: outcome(name) for name in ARM_NAMES})
    assert tuple(summary.outcomes) == ARM_NAMES
    assert summary.outcomes["hold_to_settlement"].worst_case_pnl == Decimal("-2")
    assert summary.outcomes["hold_to_settlement"].expected_shortfall == Decimal("-2")
    assert summary.paired_delta_to_no_action["immediate_taker_exit"]["s1"] == 0
    with pytest.raises(ExecutionContractError):
        InventoryArmOutcome("something_else", {"s": "0"}, "1")


def test_paired_summary_rejects_denominator_drift_and_floats():
    outcomes = {name: outcome(name) for name in ARM_NAMES}
    outcomes["no_action"] = outcome("no_action", {"different": "0"})
    with pytest.raises(ExecutionContractError):
        PairedInventorySummary(outcomes)
    with pytest.raises(ExecutionContractError):
        InventoryArmOutcome("no_action", {"s": 1.0}, "1")
