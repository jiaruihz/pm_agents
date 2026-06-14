#!/usr/bin/env python3
"""Validate basket-level execution readiness for all-YES underround plans.

This is deliberately non-submitting. It does not import a CLOB client, read
credentials, place orders, or cancel orders. Its job is to prove that the
all-YES live-prep plan and the weather_order_executor bridge preserve a
basket-level all-leg contract before a signed executor is ever armed.
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
EXECUTOR_JSONL_DEFAULT = RUN_DIR_DEFAULT / "executor_trade_plans.jsonl"
STRATEGY_ID = "all_yes_underround_basket_v0"
EXECUTION_CONTRACT_VERSION = "all_yes_execution_contract_v0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--plan-json", default=str(PLAN_JSON_DEFAULT))
    parser.add_argument("--executor-jsonl", default=str(EXECUTOR_JSONL_DEFAULT))
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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


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
    if contract.get("partial_fill_policy") != "reject_partial_before_live":
        blockers.append("execution_contract_partial_fill_policy_not_reject")
    return blockers


def mock_results_by_basket(mock_fill: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows = mock_fill.get("basket_results") if isinstance(mock_fill, dict) else None
    result: dict[str, list[dict[str, Any]]] = {}
    for row in list(rows or []):
        basket_plan_id = str(row.get("source_basket_plan_id") or row.get("plan_id") or "")
        if not basket_plan_id:
            continue
        result[basket_plan_id] = list(row.get("leg_results") or [])
    return result


def rows_by_basket(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        basket_id = str(row.get("source_basket_plan_id") or "")
        if not basket_id:
            continue
        grouped.setdefault(basket_id, []).append(row)
    return grouped


def basket_state(
    basket_plan: dict[str, Any],
    executor_rows: list[dict[str, Any]],
    mock_leg_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    basket_id = str(basket_plan.get("plan_id") or "")
    intents = list(basket_plan.get("order_intents") or [])
    blockers = contract_blockers(dict(basket_plan.get("execution_contract") or {}))
    if basket_plan.get("live_submit_enabled") is not False:
        blockers.append("basket_live_submit_enabled_not_false")
    if basket_plan.get("no_order_placed") is not True:
        blockers.append("basket_no_order_placed_not_true")
    if not intents:
        blockers.append("basket_order_intents_missing")
    if len(executor_rows) != len(intents):
        blockers.append("executor_leg_count_mismatch")

    rows_by_role = {str(row.get("child_order_role") or ""): row for row in executor_rows}
    for intent in intents:
        role = f"all_yes_leg_{intent.get('leg_index')}"
        row = rows_by_role.get(role)
        if row is None:
            blockers.append(f"executor_leg_missing:{role}")
            continue
        if row.get("live_enabled") is not False:
            blockers.append(f"executor_leg_live_enabled_not_false:{role}")
        if row.get("paper_enabled") is not True:
            blockers.append(f"executor_leg_paper_enabled_not_true:{role}")
        if row.get("all_yes_live_blocked") is not True:
            blockers.append(f"executor_leg_not_live_blocked:{role}")
        if row.get("execution_policy") != "all_yes_all_leg_or_none_v0":
            blockers.append(f"executor_leg_bad_policy:{role}")
        if str(row.get("token_id") or "") != str(intent.get("token_id") or intent.get("condition_id") or ""):
            blockers.append(f"executor_leg_token_mismatch:{role}")
        if to_float(row.get("size")) != to_float(intent.get("shares")):
            blockers.append(f"executor_leg_size_mismatch:{role}")
        if abs(to_float(row.get("limit_price")) - to_float(intent.get("limit_price"))) > 1e-9:
            blockers.append(f"executor_leg_price_mismatch:{role}")

    planned_notional = round(sum(to_float(row.get("notional")) for row in executor_rows), 6)
    basket_cost = to_float(basket_plan.get("basket_cost_usd"))
    if basket_cost > 0 and planned_notional > round(basket_cost + 1e-6, 6):
        blockers.append("executor_notional_exceeds_basket_cost")

    if blockers:
        return {
            "source_basket_plan_id": basket_id,
            "city": basket_plan.get("city"),
            "event_date": basket_plan.get("event_date"),
            "status": "blocked_invalid_basket_contract",
            "no_order_placed": True,
            "leg_intents": len(intents),
            "executor_legs": len(executor_rows),
            "planned_notional": planned_notional,
            "basket_cost_usd": basket_cost,
            "blockers": sorted(set(blockers)),
            "cancel_unfilled_leg_indexes": [],
            "unwind_filled_leg_indexes": [],
        }

    if mock_leg_results is None:
        return {
            "source_basket_plan_id": basket_id,
            "city": basket_plan.get("city"),
            "event_date": basket_plan.get("event_date"),
            "status": "not_armed_dry_run",
            "no_order_placed": True,
            "leg_intents": len(intents),
            "executor_legs": len(executor_rows),
            "planned_notional": planned_notional,
            "basket_cost_usd": basket_cost,
            "blockers": [],
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
        filled_shares = to_float(result.get("filled_shares"))
        expected_shares = to_float(intent.get("shares"))
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
            "source_basket_plan_id": basket_id,
            "city": basket_plan.get("city"),
            "event_date": basket_plan.get("event_date"),
            "status": "fail_closed_cancel_or_unwind_required",
            "no_order_placed": True,
            "leg_intents": len(intents),
            "executor_legs": len(executor_rows),
            "planned_notional": planned_notional,
            "basket_cost_usd": basket_cost,
            "blockers": ["mock_partial_or_rejected_leg"],
            "partial_or_rejected_leg_indexes": sorted(partial_or_rejected),
            "missing_leg_result_indexes": sorted(missing),
            "cancel_unfilled_leg_indexes": sorted(set(unfilled)),
            "unwind_filled_leg_indexes": sorted(set(filled)),
        }

    return {
        "source_basket_plan_id": basket_id,
        "city": basket_plan.get("city"),
        "event_date": basket_plan.get("event_date"),
        "status": "all_legs_would_fill_complete",
        "no_order_placed": True,
        "leg_intents": len(intents),
        "executor_legs": len(executor_rows),
        "planned_notional": planned_notional,
        "basket_cost_usd": basket_cost,
        "blockers": [],
        "cancel_unfilled_leg_indexes": [],
        "unwind_filled_leg_indexes": [],
    }


def build_state(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    plan_path = Path(args.plan_json)
    executor_path = Path(args.executor_jsonl)
    live_plan = read_json(plan_path)
    executor_rows = read_jsonl(executor_path)
    executor_by_basket = rows_by_basket(executor_rows)
    mock_fill = read_json(Path(args.mock_fill_json)) if args.mock_fill_json else {}
    mock_by_basket = mock_results_by_basket(mock_fill)
    blockers: list[dict[str, Any]] = []
    passed: list[dict[str, Any]] = []

    if live_plan.get("status") in {"missing", "invalid"}:
        blockers.append({"code": "live_plan_missing_or_invalid", "path": str(plan_path), "status": live_plan.get("status")})
    if live_plan.get("live_now") is not False:
        blockers.append({"code": "live_plan_live_now_not_false", "live_now": live_plan.get("live_now")})
    if live_plan.get("verdict") != "DRY_RUN_PLAN_ONLY":
        blockers.append({"code": "live_plan_not_dry_run", "verdict": live_plan.get("verdict")})
    if executor_path.exists() and any(row.get("live_enabled") is not False for row in executor_rows):
        blockers.append({"code": "executor_jsonl_contains_live_enabled_leg"})

    basket_states = [
        basket_state(
            plan,
            executor_by_basket.get(str(plan.get("plan_id") or ""), []),
            mock_by_basket.get(str(plan.get("plan_id") or "")) if mock_by_basket else None,
        )
        for plan in list(live_plan.get("plans") or [])
    ]
    invalid = [row for row in basket_states if row.get("status") == "blocked_invalid_basket_contract"]
    fail_closed = [row for row in basket_states if row.get("status") == "fail_closed_cancel_or_unwind_required"]
    if invalid:
        blockers.append({"code": "basket_execution_contract_invalid", "baskets": invalid})
    if fail_closed:
        passed.append({"code": "basket_partial_fill_fail_closed_path_available", "baskets": fail_closed})
    if not blockers:
        passed.append(
            {
                "code": "dry_run_basket_executor_available",
                "message": "Basket executor contract is dry-run only and preserves all-leg grouping.",
                "baskets_checked": len(basket_states),
                "executor_legs_checked": len(executor_rows),
            }
        )

    result = {
        "command": "check",
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "live_now": False,
        "live_enabled": False,
        "no_order_placed": True,
        "plan_path": str(plan_path),
        "executor_jsonl": str(executor_path),
        "run_dir": str(run_dir),
        "baskets_checked": len(basket_states),
        "executor_legs_checked": len(executor_rows),
        "basket_states": basket_states,
        "blockers": blockers,
        "passed": passed,
        "verdict": "DRY_RUN_BASKET_EXECUTOR_READY" if not blockers else "DRY_RUN_BASKET_EXECUTOR_BLOCKED",
        "live_blockers": [
            "signed_order_submission_not_implemented_here",
            "real_fill_polling_cancel_unwind_not_armed",
            "weather_strategy_deploy_review_required",
        ],
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "latest_basket_executor_readiness.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    append_jsonl(run_dir / "basket_executor_readiness_history.jsonl", [result])
    return result


def main() -> None:
    args = parse_args()
    result = build_state(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
