import argparse
import json
from pathlib import Path

from scripts.ops.all_yes_underround_basket_executor_v0 import basket_state, build_state


def _contract(**overrides):
    row = {
        "contract_version": "all_yes_execution_contract_v0",
        "live_submit_enabled": False,
        "no_order_placed": True,
        "order_submission_mode": "disabled_dry_run_only",
        "all_leg_or_none_required": True,
        "partial_fill_policy": "reject_partial_before_live",
    }
    row.update(overrides)
    return row


def _basket_plan(**overrides):
    row = {
        "plan_id": "basket-1",
        "city": "Atlanta",
        "event_date": "2026-06-14",
        "live_submit_enabled": False,
        "no_order_placed": True,
        "basket_cost_usd": 4.5,
        "execution_contract": _contract(),
        "order_intents": [
            {"leg_index": 0, "token_id": "token-1", "limit_price": 0.4, "shares": 5.0},
            {"leg_index": 1, "token_id": "token-2", "limit_price": 0.5, "shares": 5.0},
        ],
    }
    row.update(overrides)
    return row


def _executor_row(leg_index: int, token_id: str, price: float):
    return {
        "record_type": "weather_edge_trade_plan",
        "source_basket_plan_id": "basket-1",
        "child_order_role": f"all_yes_leg_{leg_index}",
        "token_id": token_id,
        "limit_price": price,
        "size": 5.0,
        "notional": round(price * 5.0, 6),
        "paper_enabled": True,
        "live_enabled": False,
        "all_yes_live_blocked": True,
        "execution_policy": "all_yes_all_leg_or_none_v0",
    }


def test_basket_state_validates_bridge_rows_without_arming_live():
    state = basket_state(
        _basket_plan(),
        [_executor_row(0, "token-1", 0.4), _executor_row(1, "token-2", 0.5)],
    )

    assert state["status"] == "not_armed_dry_run"
    assert state["no_order_placed"] is True
    assert state["blockers"] == []
    assert state["planned_notional"] == 4.5


def test_basket_state_blocks_missing_executor_leg():
    state = basket_state(_basket_plan(), [_executor_row(0, "token-1", 0.4)])

    assert state["status"] == "blocked_invalid_basket_contract"
    assert "executor_leg_count_mismatch" in state["blockers"]
    assert "executor_leg_missing:all_yes_leg_1" in state["blockers"]


def test_basket_state_fails_closed_on_mock_partial_fill():
    state = basket_state(
        _basket_plan(),
        [_executor_row(0, "token-1", 0.4), _executor_row(1, "token-2", 0.5)],
        [
            {"leg_index": 0, "status": "filled", "filled_shares": 5.0},
            {"leg_index": 1, "status": "partial", "filled_shares": 2.0},
        ],
    )

    assert state["status"] == "fail_closed_cancel_or_unwind_required"
    assert state["no_order_placed"] is True
    assert state["cancel_unfilled_leg_indexes"] == []
    assert state["unwind_filled_leg_indexes"] == [0, 1]


def test_build_state_writes_basket_executor_readiness(tmp_path: Path):
    run_dir = tmp_path / "run"
    plan_json = tmp_path / "latest_live_plan.json"
    executor_jsonl = tmp_path / "executor_trade_plans.jsonl"
    plan_json.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-06-14T06:10:00Z",
                "verdict": "DRY_RUN_PLAN_ONLY",
                "live_now": False,
                "plans": [_basket_plan()],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    executor_jsonl.write_text(
        "\n".join(
            [
                json.dumps(_executor_row(0, "token-1", 0.4)),
                json.dumps(_executor_row(1, "token-2", 0.5)),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = build_state(
        argparse.Namespace(
            command="check",
            plan_json=str(plan_json),
            executor_jsonl=str(executor_jsonl),
            run_dir=str(run_dir),
            mock_fill_json="",
        )
    )

    assert result["verdict"] == "DRY_RUN_BASKET_EXECUTOR_READY"
    assert result["live_now"] is False
    assert result["live_enabled"] is False
    assert result["no_order_placed"] is True
    assert result["baskets_checked"] == 1
    assert result["executor_legs_checked"] == 2
    assert [row["code"] for row in result["passed"]] == ["dry_run_basket_executor_available"]
    assert (run_dir / "latest_basket_executor_readiness.json").exists()
    assert (run_dir / "basket_executor_readiness_history.jsonl").exists()
