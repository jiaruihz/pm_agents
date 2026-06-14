import argparse
import json
from pathlib import Path

from scripts.ops.all_yes_underround_fok_executor_v0 import basket_state, build_state, execute_baskets


def _contract(**overrides):
    row = {
        "contract_version": "all_yes_execution_contract_v0",
        "live_submit_enabled": False,
        "no_order_placed": True,
        "order_submission_mode": "disabled_dry_run_only",
        "all_leg_or_none_required": True,
        "per_leg_time_in_force": "FOK_OR_CANCEL_REQUIRED_BEFORE_LIVE",
        "partial_fill_policy": "reject_partial_before_live",
    }
    row.update(overrides)
    return row


def _basket_plan(**overrides):
    row = {
        "plan_id": "basket-1",
        "city": "Istanbul",
        "event_date": "2026-06-14",
        "basket_cost_usd": 4.5,
        "execution_contract": _contract(),
        "order_intents": [
            {"leg_index": 0, "token_id": "token-1", "limit_price": 0.4, "shares": 5.0},
            {"leg_index": 1, "token_id": "token-2", "limit_price": 0.5, "shares": 5.0},
        ],
    }
    row.update(overrides)
    return row


def _executor_row(leg_index: int, token_id: str, price: float, **overrides):
    row = {
        "record_type": "weather_edge_trade_plan",
        "source_basket_plan_id": "basket-1",
        "child_order_role": f"all_yes_leg_{leg_index}",
        "token_id": token_id,
        "limit_price": price,
        "size": 5.0,
        "notional": round(price * 5.0, 6),
        "paper_enabled": True,
        "live_enabled": False,
        "maker_only": False,
        "order_type": "FOK",
        "time_in_force": "FOK",
        "all_yes_all_leg_or_none_required": True,
        "execution_policy": "all_yes_all_leg_or_none_v0",
    }
    row.update(overrides)
    return row


def test_basket_state_accepts_fok_all_leg_contract():
    state = basket_state(
        _basket_plan(),
        [_executor_row(0, "token-1", 0.4), _executor_row(1, "token-2", 0.5)],
    )

    assert state["status"] == "not_armed_dry_run_fok"
    assert state["no_order_placed"] is True
    assert state["blockers"] == []


def test_basket_state_blocks_non_fok_leg():
    state = basket_state(
        _basket_plan(),
        [_executor_row(0, "token-1", 0.4), _executor_row(1, "token-2", 0.5, order_type="GTC")],
    )

    assert state["status"] == "blocked_invalid_fok_contract"
    assert "executor_leg_not_fok:all_yes_leg_1" in state["blockers"]


def test_basket_state_fails_closed_on_partial_fok_result():
    state = basket_state(
        _basket_plan(),
        [_executor_row(0, "token-1", 0.4), _executor_row(1, "token-2", 0.5)],
        [
            {"leg_index": 0, "status": "filled", "filled_shares": 5.0},
            {"leg_index": 1, "status": "error", "filled_shares": 0.0},
        ],
    )

    assert state["status"] == "fail_closed_cancel_or_unwind_required"
    assert state["cancel_unfilled_leg_indexes"] == [1]
    assert state["unwind_filled_leg_indexes"] == [0]


def test_build_state_writes_fok_executor_readiness(tmp_path: Path):
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

    assert result["verdict"] == "DRY_RUN_FOK_EXECUTOR_READY"
    assert result["order_type"] == "FOK"
    assert result["live_now"] is False
    assert result["live_enabled"] is False
    assert result["no_order_placed"] is True
    assert result["clob_order_type_audit"]["supports_fok"] is True
    assert [row["code"] for row in result["passed"]] == ["dry_run_fok_executor_available"]
    assert (run_dir / "latest_fok_executor_readiness.json").exists()
    assert (run_dir / "fok_executor_readiness_history.jsonl").exists()


def _write_plan_and_executor(tmp_path: Path):
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
    return run_dir, plan_json, executor_jsonl


def _execute_args(run_dir: Path, plan_json: Path, executor_jsonl: Path, out_jsonl: Path, **overrides):
    row = {
        "command": "execute",
        "plan_json": str(plan_json),
        "executor_jsonl": str(executor_jsonl),
        "run_dir": str(run_dir),
        "mock_fill_json": "",
        "out_jsonl": str(out_jsonl),
        "live": False,
        "confirm_live": False,
        "arm": False,
    }
    row.update(overrides)
    return argparse.Namespace(**row)


def test_execute_baskets_dry_run_writes_would_submit_rows(tmp_path: Path):
    run_dir, plan_json, executor_jsonl = _write_plan_and_executor(tmp_path)
    out_jsonl = tmp_path / "orders.jsonl"

    result = execute_baskets(args=_execute_args(run_dir, plan_json, executor_jsonl, out_jsonl))

    assert result["verdict"] == "DRY_RUN_FOK_EXECUTOR_EXECUTED"
    assert result["live_enabled"] is False
    assert result["no_order_placed"] is True
    rows = [json.loads(line) for line in out_jsonl.read_text().splitlines()]
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"dry_run_would_submit_fok"}
    assert {row["no_order_placed"] for row in rows} == {True}


def test_execute_baskets_blocks_live_without_all_flags(tmp_path: Path):
    run_dir, plan_json, executor_jsonl = _write_plan_and_executor(tmp_path)
    out_jsonl = tmp_path / "orders.jsonl"

    result = execute_baskets(args=_execute_args(run_dir, plan_json, executor_jsonl, out_jsonl, live=True))

    assert result["live_requested"] is True
    assert result["live_enabled"] is False
    assert [row["code"] for row in result["blockers"]] == ["live_flags_missing"]
    rows = [json.loads(line) for line in out_jsonl.read_text().splitlines()]
    assert {row["status"] for row in rows} == {"blocked"}
    assert {row["no_order_placed"] for row in rows} == {True}


def test_execute_baskets_can_use_injected_live_fok_place_fn(tmp_path: Path):
    run_dir, plan_json, executor_jsonl = _write_plan_and_executor(tmp_path)
    out_jsonl = tmp_path / "orders.jsonl"
    calls = []

    def fake_place(row):
        calls.append(row["token_id"])
        return {"place": {"order_id": f"order-{row['token_id']}"}}

    result = execute_baskets(
        args=_execute_args(run_dir, plan_json, executor_jsonl, out_jsonl, live=True, confirm_live=True, arm=True),
        live_place_fn=fake_place,
    )

    assert result["verdict"] == "LIVE_FOK_EXECUTION_ATTEMPTED"
    assert result["live_enabled"] is True
    assert result["no_order_placed"] is False
    assert calls == ["token-1", "token-2"]
    rows = [json.loads(line) for line in out_jsonl.read_text().splitlines()]
    assert {row["status"] for row in rows} == {"submitted_fok"}
    assert {row["basket_status"] for row in rows} == {"all_legs_submitted_fok"}
    assert {row["order_type"] for row in rows} == {"FOK"}
