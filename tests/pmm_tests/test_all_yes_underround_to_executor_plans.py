import argparse
import json
from pathlib import Path

from scripts.ops.all_yes_underround_to_executor_plans_v0 import convert


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


def _args(plan_json: Path, out_jsonl: Path, summary_out: Path):
    return argparse.Namespace(
        command="convert",
        plan_json=str(plan_json),
        out_jsonl=str(out_jsonl),
        summary_out=str(summary_out),
    )


def test_convert_writes_live_disabled_weather_executor_plans(tmp_path: Path):
    plan_json = tmp_path / "latest_live_plan.json"
    out_jsonl = tmp_path / "executor_plans.jsonl"
    summary_out = tmp_path / "summary.json"
    plan_json.write_text(
        json.dumps(
            {
                "verdict": "DRY_RUN_PLAN_ONLY",
                "live_now": False,
                "execution_contract": _contract(),
                "plans": [
                    {
                        "plan_id": "basket-1",
                        "city": "Istanbul",
                        "event_date": "2026-06-14",
                        "event_slug": "highest-temperature-in-istanbul-on-june-14-2026",
                        "underround": 0.025,
                        "execution_contract": _contract(),
                        "order_intents": [
                            {
                                "leg_index": 0,
                                "condition_id": "cond-1",
                                "token_id": "token-1",
                                "bracket": "30",
                                "limit_price": 0.2,
                                "shares": 5,
                                "submit_now": False,
                            },
                            {
                                "leg_index": 1,
                                "condition_id": "cond-2",
                                "token_id": "token-2",
                                "bracket": "31",
                                "limit_price": 0.3,
                                "shares": 5,
                                "submit_now": False,
                            },
                        ],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = convert(_args(plan_json, out_jsonl, summary_out))

    assert result["verdict"] == "DRY_RUN_EXECUTOR_PLANS_READY"
    assert result["plans"] == 2
    rows = [json.loads(line) for line in out_jsonl.read_text().splitlines()]
    assert len(rows) == 2
    assert {row["record_type"] for row in rows} == {"weather_edge_trade_plan"}
    assert {row["live_enabled"] for row in rows} == {False}
    assert {row["paper_enabled"] for row in rows} == {True}
    assert {row["execution_mode"] for row in rows} == {"all_yes_dry_run_bridge"}
    assert {row["order_type"] for row in rows} == {"FOK"}
    assert {row["time_in_force"] for row in rows} == {"FOK"}
    assert {row["all_yes_all_leg_or_none_required"] for row in rows} == {True}
    assert {row["risk_status"] for row in rows} == {"passed"}
    assert json.loads(summary_out.read_text())["plans"] == 2


def test_convert_blocks_unsafe_contract_without_rows(tmp_path: Path):
    plan_json = tmp_path / "latest_live_plan.json"
    out_jsonl = tmp_path / "executor_plans.jsonl"
    summary_out = tmp_path / "summary.json"
    plan_json.write_text(
        json.dumps(
            {
                "verdict": "DRY_RUN_PLAN_ONLY",
                "live_now": False,
                "execution_contract": _contract(live_submit_enabled=True),
                "plans": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = convert(_args(plan_json, out_jsonl, summary_out))

    assert result["verdict"] == "DRY_RUN_EXECUTOR_PLANS_BLOCKED"
    assert result["plans"] == 0
    assert [row["code"] for row in result["blockers"]] == ["source_execution_contract_not_safe"]
    assert out_jsonl.read_text() == ""
