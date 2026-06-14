#!/usr/bin/env python3
"""Validate the all-YES dry-run execution state.

This is not a live executor. It imports no CLOB client, reads no signing
credentials, and never submits or cancels orders. Its job is to keep a
machine-readable fail-closed state artifact beside the dry-run live plan so a
future signed executor can be reviewed against the same all-leg contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


RUN_DIR_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_paper_v0"
PLAN_JSON_DEFAULT = RUN_DIR_DEFAULT / "latest_live_plan.json"
STRATEGY_ID = "all_yes_underround_basket_v0"
EXECUTION_CONTRACT_VERSION = "all_yes_execution_contract_v0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--plan-json", default=str(PLAN_JSON_DEFAULT))
    parser.add_argument("--run-dir", default=str(RUN_DIR_DEFAULT))
    parser.add_argument("--mock-fill-json", default="")
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {"status": "invalid", "path": str(path)}


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def contract_blockers(contract: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if contract.get("contract_version") != EXECUTION_CONTRACT_VERSION:
        blockers.append("execution_contract_bad_version")
    if contract.get("live_submit_enabled") is not False:
        blockers.append("execution_contract_live_submit_enabled_not_false")
    if contract.get("no_order_placed") is not True:
        blockers.append("execution_contract_no_order_placed_not_true")
    if contract.get("order_submission_mode") != "disabled_dry_run_only":
        blockers.append("execution_contract_order_submission_mode_not_disabled")
    if contract.get("all_leg_or_none_required") is not True:
        blockers.append("execution_contract_all_leg_or_none_not_true")
    if contract.get("per_leg_time_in_force") != "FOK_OR_CANCEL_REQUIRED_BEFORE_LIVE":
        blockers.append("execution_contract_time_in_force_not_blocking_live")
    if contract.get("partial_fill_policy") != "reject_partial_before_live":
        blockers.append("execution_contract_partial_fill_policy_not_reject")
    if contract.get("partial_fill_live_action") != "cancel_unfilled_then_unwind_filled_or_pause_strategy":
        blockers.append("execution_contract_partial_fill_action_not_fail_closed")
    return blockers


def mock_results_by_plan(mock_fill: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows = mock_fill.get("plan_results") if isinstance(mock_fill, dict) else None
    result: dict[str, list[dict[str, Any]]] = {}
    for row in list(rows or []):
        plan_id = str(row.get("plan_id") or "")
        if not plan_id:
            continue
        result[plan_id] = list(row.get("leg_results") or [])
    return result


def plan_execution_state(plan: dict[str, Any], mock_leg_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    intents = list(plan.get("order_intents") or [])
    blockers = contract_blockers(dict(plan.get("execution_contract") or {}))
    if plan.get("live_submit_enabled") is not False:
        blockers.append("plan_live_submit_enabled_not_false")
    if plan.get("no_order_placed") is not True:
        blockers.append("plan_no_order_placed_not_true")
    if not intents:
        blockers.append("plan_order_intents_missing")
    if any(intent.get("submit_now") is not False for intent in intents):
        blockers.append("plan_intent_submit_now_not_false")

    if blockers:
        return {
            "plan_id": plan.get("plan_id"),
            "status": "blocked_invalid_plan_contract",
            "no_order_placed": True,
            "blockers": blockers,
            "cancel_unfilled_leg_indexes": [],
            "unwind_filled_leg_indexes": [],
        }

    if mock_leg_results is None:
        return {
            "plan_id": plan.get("plan_id"),
            "status": "not_armed_dry_run",
            "no_order_placed": True,
            "blockers": [],
            "leg_intents": len(intents),
            "cancel_unfilled_leg_indexes": [],
            "unwind_filled_leg_indexes": [],
        }

    results_by_index = {int(row.get("leg_index")): row for row in mock_leg_results if row.get("leg_index") is not None}
    filled: list[int] = []
    unfilled: list[int] = []
    partial_or_rejected: list[int] = []
    missing: list[int] = []
    for intent in intents:
        idx = int(intent.get("leg_index"))
        result = results_by_index.get(idx)
        if result is None:
            missing.append(idx)
            unfilled.append(idx)
            continue
        status = str(result.get("status") or "").lower()
        filled_shares = float(result.get("filled_shares") or 0.0)
        expected_shares = float(intent.get("shares") or 0.0)
        if status == "filled" and filled_shares >= expected_shares:
            filled.append(idx)
        elif status in {"partial", "rejected", "cancelled", "expired", "error"}:
            partial_or_rejected.append(idx)
            if filled_shares > 0:
                filled.append(idx)
            else:
                unfilled.append(idx)
        else:
            partial_or_rejected.append(idx)
            unfilled.append(idx)

    if missing or partial_or_rejected:
        return {
            "plan_id": plan.get("plan_id"),
            "status": "fail_closed_cancel_or_unwind_required",
            "no_order_placed": True,
            "blockers": ["mock_partial_or_rejected_leg"],
            "partial_or_rejected_leg_indexes": sorted(partial_or_rejected),
            "missing_leg_result_indexes": sorted(missing),
            "cancel_unfilled_leg_indexes": sorted(set(unfilled)),
            "unwind_filled_leg_indexes": sorted(set(filled)),
        }

    return {
        "plan_id": plan.get("plan_id"),
        "status": "all_legs_would_fill_complete",
        "no_order_placed": True,
        "blockers": [],
        "cancel_unfilled_leg_indexes": [],
        "unwind_filled_leg_indexes": [],
    }


def build_state(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    plan_path = Path(args.plan_json)
    live_plan = read_json(plan_path)
    mock_fill = read_json(Path(args.mock_fill_json)) if args.mock_fill_json else {}
    mock_by_plan = mock_results_by_plan(mock_fill)
    blockers: list[dict[str, Any]] = []
    passed: list[dict[str, Any]] = []

    if live_plan.get("status") in {"missing", "invalid"}:
        blockers.append({"code": "live_plan_missing_or_invalid", "path": str(plan_path), "status": live_plan.get("status")})
    if live_plan.get("live_now") is not False:
        blockers.append({"code": "live_plan_live_now_not_false", "live_now": live_plan.get("live_now")})
    if live_plan.get("verdict") != "DRY_RUN_PLAN_ONLY":
        blockers.append({"code": "live_plan_not_dry_run", "verdict": live_plan.get("verdict")})

    root_contract_blockers = contract_blockers(dict(live_plan.get("execution_contract") or {}))
    if root_contract_blockers:
        blockers.append({"code": "root_execution_contract_invalid", "blockers": root_contract_blockers})

    plan_states = [
        plan_execution_state(plan, mock_by_plan.get(str(plan.get("plan_id"))) if mock_by_plan else None)
        for plan in list(live_plan.get("plans") or [])
    ]
    unsafe_states = [row for row in plan_states if row.get("status") == "blocked_invalid_plan_contract"]
    fail_closed_states = [row for row in plan_states if row.get("status") == "fail_closed_cancel_or_unwind_required"]
    if unsafe_states:
        blockers.append({"code": "plan_execution_contract_invalid", "plans": unsafe_states})
    if fail_closed_states:
        passed.append({"code": "partial_fill_fail_closed_path_available", "plans": fail_closed_states})
    if not blockers:
        passed.append(
            {
                "code": "dry_run_executor_state_available",
                "message": "Executor state artifact is dry-run only and fail-closed.",
                "plans_checked": len(plan_states),
            }
        )

    result = {
        "command": "check",
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "live_now": False,
        "no_order_placed": True,
        "plan_path": str(plan_path),
        "run_dir": str(run_dir),
        "plans_checked": len(plan_states),
        "plan_states": plan_states,
        "blockers": blockers,
        "passed": passed,
        "verdict": "DRY_RUN_EXECUTOR_READY" if not blockers else "DRY_RUN_EXECUTOR_BLOCKED",
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "latest_executor_readiness.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    append_jsonl(run_dir / "executor_readiness_history.jsonl", [result])
    return result


def main() -> None:
    args = parse_args()
    result = build_state(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
