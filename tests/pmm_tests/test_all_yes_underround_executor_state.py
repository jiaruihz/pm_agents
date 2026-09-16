import argparse
import json
from pathlib import Path

from scripts.ops.all_yes_underround_executor_state_v0 import build_state, plan_execution_state


def _contract(**overrides):
    row = {
        "contract_version": "all_yes_execution_contract_v0",
        "live_submit_enabled": False,
        "no_order_placed": True,
        "order_submission_mode": "disabled_dry_run_only",
        "all_leg_or_none_required": True,
        "per_leg_time_in_force": "FOK_OR_CANCEL_REQUIRED_BEFORE_LIVE",
        "partial_fill_policy": "reject_partial_before_live",
        "partial_fill_live_action": "cancel_unfilled_then_unwind_filled_or_pause_strategy",
    }
    row.update(overrides)
    return row


def _plan(**overrides):
    row = {
        "plan_id": "p1",
        "live_submit_enabled": False,
        "no_order_placed": True,
        "execution_contract": _contract(),
        "order_intents": [
            {"leg_index": 0, "shares": 5.0, "submit_now": False},
            {"leg_index": 1, "shares": 5.0, "submit_now": False},
        ],
    }
    row.update(overrides)
    return row


def test_plan_execution_state_is_not_armed_without_mock_results():
    state = plan_execution_state(_plan())

    assert state["status"] == "not_armed_dry_run"
    assert state["no_order_placed"] is True
    assert state["blockers"] == []


def test_plan_execution_state_fails_closed_on_partial_fill():
    state = plan_execution_state(
        _plan(),
        [
            {"leg_index": 0, "status": "filled", "filled_shares": 5.0},
            {"leg_index": 1, "status": "partial", "filled_shares": 2.0},
        ],
    )

    assert state["status"] == "fail_closed_cancel_or_unwind_required"
    assert state["cancel_unfilled_leg_indexes"] == []
    assert state["unwind_filled_leg_indexes"] == [0, 1]


def test_build_state_writes_dry_run_readiness_artifact(tmp_path: Path):
    run_dir = tmp_path / "run"
    plan_json = tmp_path / "latest_live_plan.json"
    plan_json.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-06-14T05:00:00+00:00",
                "verdict": "DRY_RUN_PLAN_ONLY",
                "live_now": False,
                "execution_contract": _contract(),
                "plans": [_plan()],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = build_state(argparse.Namespace(command="check", plan_json=str(plan_json), run_dir=str(run_dir), mock_fill_json=""))

    assert result["verdict"] == "DRY_RUN_EXECUTOR_READY"
    assert result["live_now"] is False
    assert result["no_order_placed"] is True
    assert result["plans_checked"] == 1
    assert [row["code"] for row in result["passed"]] == ["dry_run_executor_state_available"]
    assert (run_dir / "latest_executor_readiness.json").exists()
    assert (run_dir / "executor_readiness_history.jsonl").exists()
