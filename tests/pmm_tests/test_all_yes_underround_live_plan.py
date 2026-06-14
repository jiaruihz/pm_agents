import argparse
import json
from pathlib import Path

from scripts.ops.all_yes_underround_live_plan_v0 import build_plan


def _candidate(**overrides):
    legs = [
        {
            "condition_id": f"condition-{idx}",
            "bracket": str(20 + idx),
            "best_ask": price,
            "best_bid": max(price - 0.01, 0.001),
            "ask_size": 10.0,
        }
        for idx, price in enumerate([0.10, 0.15, 0.20, 0.22, 0.28])
    ]
    row = {
        "event_date": "2026-06-14",
        "city": "TestCity",
        "event_slug": "highest-temperature-in-test-city-on-june-14-2026",
        "underround": 0.05,
        "total_yes_ask_cost": 0.95,
        "max_yes_spread": 0.01,
        "snapshot_ts_utc": "2026-06-13T18:00:00Z",
        "legs_detail": legs,
    }
    row.update(overrides)
    return row


def _args(scan_json: Path, run_dir: Path):
    return argparse.Namespace(
        command="plan",
        scan_json=str(scan_json),
        run_dir=str(run_dir),
        shares_per_leg=5.0,
        max_baskets_per_cycle=2,
        max_basket_cost_usd=5.0,
        min_underround=0.02,
        max_spread=0.05,
        max_snapshot_age_seconds=180.0,
        decision_ts_utc="2026-06-13T18:01:00Z",
    )


def test_live_plan_builds_non_submitting_order_intents(tmp_path: Path):
    scan_json = tmp_path / "scan.json"
    run_dir = tmp_path / "run"
    scan_json.write_text(
        json.dumps(
            {
                "snapshot_path": "/tmp/snapshot.jsonl.gz",
                "snapshot_summary": {"snapshot_ts_utc_max": "2026-06-13T18:00:00Z"},
                "paper_shadow_candidates": [_candidate()],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = build_plan(_args(scan_json, run_dir))

    assert result["live_now"] is False
    assert result["verdict"] == "DRY_RUN_PLAN_ONLY"
    assert result["planned_baskets"] == 1
    assert result["rejected_baskets"] == 0
    plan = result["plans"][0]
    assert plan["live_submit_enabled"] is False
    assert plan["no_order_placed"] is True
    assert plan["all_leg_or_none_required"] is True
    assert plan["partial_fill_policy"] == "block_live_until_cancel_or_unwind_engine_exists"
    assert plan["execution_contract"] == {
        "contract_version": "all_yes_execution_contract_v0",
        "live_submit_enabled": False,
        "no_order_placed": True,
        "order_submission_mode": "disabled_dry_run_only",
        "all_leg_or_none_required": True,
        "per_leg_time_in_force": "FOK_OR_CANCEL_REQUIRED_BEFORE_LIVE",
        "partial_fill_policy": "reject_partial_before_live",
        "partial_fill_live_action": "cancel_unfilled_then_unwind_filled_or_pause_strategy",
        "max_snapshot_age_seconds": 180.0,
        "required_before_live": [
            "signed_all_leg_order_submitter",
            "post_submit_fill_polling",
            "cancel_all_unfilled_legs_on_any_partial_or_reject",
            "unwind_filled_yes_legs_if_cancel_fails",
            "basket_cost_hard_cap_enforced_at_submit",
            "weather_strategy_deploy_review",
        ],
    }
    assert result["execution_contract"] == plan["execution_contract"]
    assert len(plan["order_intents"]) == 5
    assert {intent["submit_now"] for intent in plan["order_intents"]} == {False}
    assert (run_dir / "latest_live_plan.json").exists()
    assert (run_dir / "live_plan_history.jsonl").exists()


def test_live_plan_rejects_guard_failures_without_order_intents(tmp_path: Path):
    scan_json = tmp_path / "scan.json"
    run_dir = tmp_path / "run"
    bad = _candidate(underround=0.01)
    scan_json.write_text(
        json.dumps(
            {
                "snapshot_path": "/tmp/snapshot.jsonl.gz",
                "snapshot_summary": {"snapshot_ts_utc_max": "2026-06-13T18:00:00Z"},
                "paper_shadow_candidates": [bad],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = build_plan(_args(scan_json, run_dir))

    assert result["planned_baskets"] == 0
    assert result["rejected_baskets"] == 1
    assert result["rejected"][0]["status"] == "rejected_by_guard"
    assert "underround_below_min" in result["rejected"][0]["guard"]["blockers"]
