from __future__ import annotations

import pytest

from src.strategies.weather_edge_v1.execution.conditional_stop_loss import (
    TradeAction,
    TriggerCondition,
    build_action_plan,
    evaluate_first_new_event,
    resolve_action_shares,
)


def test_uses_first_new_event_not_latest_event() -> None:
    decision = evaluate_first_new_event(
        [
            {"ts": "2026-07-16T16:55:00Z", "temp": 25.0},
            {"ts": "2026-07-16T16:25:00Z", "temp": 24.0},
            {"ts": "2026-07-16T15:55:00Z", "temp": 24.0},
        ],
        baseline_sequence="2026-07-16T15:55:00Z",
        sequence_field="ts",
        condition=TriggerCondition("temp", "eq", 25.0),
    )
    assert decision.status == "no_trigger"
    assert decision.event == {"ts": "2026-07-16T16:25:00Z", "temp": 24.0}


def test_first_new_event_can_trigger() -> None:
    decision = evaluate_first_new_event(
        [{"ts": "2026-07-16T16:25:00Z", "temp": 25.0}],
        baseline_sequence="2026-07-16T15:55:00Z",
        sequence_field="ts",
        condition=TriggerCondition("temp", "eq", 25.0),
    )
    assert decision.triggered


def test_close_all_uses_wallet_position_and_missing_position_fails_closed() -> None:
    action = TradeAction("close", "yes-token", "SELL_YES", "SELL", "wallet_position_all", limit_price=0.001)
    assert resolve_action_shares(action, wallet_positions={"yes-token": 5.25}) == 5.25
    with pytest.raises(RuntimeError, match="position unavailable"):
        resolve_action_shares(action, wallet_positions={})


def test_action_plan_identity_is_stable_and_keeps_action_size() -> None:
    action = TradeAction("reverse", "no-token", "BUY_NO", "BUY", "fixed_shares", shares=15, limit_price=0.999)
    first = build_action_plan(
        rule_id="rule-v1",
        action=action,
        event_sequence="2026-07-16T16:25:00Z",
        wallet_positions={},
        market={"city": "Amsterdam", "target_date": "2026-07-16", "bracket": "24"},
    )
    second = build_action_plan(
        rule_id="rule-v1",
        action=action,
        event_sequence="2026-07-16T16:25:00Z",
        wallet_positions={},
        market={"city": "Amsterdam", "target_date": "2026-07-16", "bracket": "24"},
    )
    assert first["plan_id"] == second["plan_id"]
    assert first["signal_id"] == second["signal_id"]
    assert first["size"] == 15.0
    assert first["notional"] == 14.985
