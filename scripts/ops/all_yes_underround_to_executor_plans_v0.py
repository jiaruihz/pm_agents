#!/usr/bin/env python3
"""Bridge all-YES dry-run basket plans to weather_order_executor plan JSONL.

The bridge is deliberately live-disabled. It lets us verify field shape,
notional caps, and downstream paper logging before any signed all-leg executor
exists. Real live submission still requires a separate deploy-reviewed executor.
"""

from __future__ import annotations

import argparse
import hashlib
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
OUT_JSONL_DEFAULT = RUN_DIR_DEFAULT / "executor_trade_plans.jsonl"
SUMMARY_DEFAULT = RUN_DIR_DEFAULT / "executor_trade_plan_summary.json"
STRATEGY_ID = "all_yes_underround_basket_v0"
EXECUTION_CONTRACT_VERSION = "all_yes_execution_contract_v0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["convert"])
    parser.add_argument("--plan-json", default=str(PLAN_JSON_DEFAULT))
    parser.add_argument("--out-jsonl", default=str(OUT_JSONL_DEFAULT))
    parser.add_argument("--summary-out", default=str(SUMMARY_DEFAULT))
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {"status": "invalid", "path": str(path)}


def stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def contract_is_safe(contract: dict[str, Any]) -> bool:
    return (
        contract.get("contract_version") == EXECUTION_CONTRACT_VERSION
        and contract.get("live_submit_enabled") is False
        and contract.get("no_order_placed") is True
        and contract.get("order_submission_mode") == "disabled_dry_run_only"
        and contract.get("all_leg_or_none_required") is True
        and contract.get("partial_fill_policy") == "reject_partial_before_live"
    )


def build_executor_plan(basket_plan: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    price = to_float(intent.get("limit_price"), 0.0)
    shares = to_float(intent.get("shares"), 0.0)
    base = {
        "strategy": "weather_edge_v1",
        "strategy_instance": "all_yes_underround_dry_run_bridge_v0",
        "strategy_id": STRATEGY_ID,
        "strategy_family": "all_yes_underround",
        "decision_mode": "all_yes_underround_basket",
        "execution_mode": "all_yes_dry_run_bridge",
        "profile": STRATEGY_ID,
        "city": str(basket_plan.get("city") or ""),
        "target_date": str(basket_plan.get("event_date") or ""),
        "market_slug": str(basket_plan.get("event_slug") or ""),
        "market_id": str(intent.get("condition_id") or ""),
        "event_slug": str(basket_plan.get("event_slug") or ""),
        "bracket": str(intent.get("bracket") or ""),
        "token_id": str(intent.get("token_id") or intent.get("condition_id") or ""),
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "order_type": "FOK",
        "time_in_force": "FOK",
        "market_price": price,
        "best_bid": 0.0,
        "best_ask": price,
        "spread": 0.0,
        "limit_price": price,
        "quote_status": "accepted",
        "quote_reason": "all_yes_underround_dry_run_bridge",
        "quote_edge": to_float(basket_plan.get("underround"), 0.0),
        "required_quote_edge": 0.0,
        "quote_best_bid": 0.0,
        "quote_best_ask": price,
        "quote_spread": 0.0,
        "quote_tick_size": 0.001,
        "quote_mode": "all_yes_limit_ask",
        "child_order_role": f"all_yes_leg_{intent.get('leg_index')}",
        "maker_only": False,
        "order_notional_cap": round(price * shares, 6),
        "size": shares,
        "notional": round(price * shares, 6),
        "execution_policy": "all_yes_all_leg_or_none_v0",
        "all_yes_all_leg_or_none_required": True,
        "tick_size": 0.001,
        "entry_price_window": "0.00-1.00",
        "sizing_mode": "fixed_shares",
        "fixed_order_shares": shares,
        "max_order_shares": shares,
        "edge": to_float(basket_plan.get("underround"), 0.0),
        "min_edge": 0.0,
        "paper_enabled": True,
        "live_enabled": False,
        "source_basket_plan_id": str(basket_plan.get("plan_id") or ""),
        "all_yes_live_blocked": True,
        "all_yes_live_block_reason": "requires_signed_all_leg_executor_and_deploy_review",
    }
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": stable_hash(base),
        "signal_id": stable_hash({"source_basket_plan_id": basket_plan.get("plan_id"), "leg_index": intent.get("leg_index")}),
        "created_at_utc": now_utc(),
        "status": "accepted",
        "risk_status": "passed",
        "risk_reason": "",
        **base,
    }


def convert(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = Path(args.plan_json)
    out_path = Path(args.out_jsonl)
    summary_path = Path(args.summary_out)
    live_plan = read_json(plan_path)
    blockers: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    if live_plan.get("live_now") is not False:
        blockers.append({"code": "source_live_plan_live_now_not_false"})
    if live_plan.get("verdict") != "DRY_RUN_PLAN_ONLY":
        blockers.append({"code": "source_live_plan_not_dry_run", "verdict": live_plan.get("verdict")})
    if not contract_is_safe(dict(live_plan.get("execution_contract") or {})):
        blockers.append({"code": "source_execution_contract_not_safe"})
    for plan in list(live_plan.get("plans") or []):
        if not contract_is_safe(dict(plan.get("execution_contract") or {})):
            blockers.append({"code": "plan_execution_contract_not_safe", "plan_id": plan.get("plan_id")})
            continue
        for intent in list(plan.get("order_intents") or []):
            if intent.get("submit_now") is not False:
                blockers.append({"code": "intent_submit_now_not_false", "plan_id": plan.get("plan_id"), "leg_index": intent.get("leg_index")})
                continue
            rows.append(build_executor_plan(plan, intent))

    if blockers:
        rows = []
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    result = {
        "command": "convert",
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "live_now": False,
        "source_plan": str(plan_path),
        "out_jsonl": str(out_path),
        "plans": len(rows),
        "source_baskets": len(list(live_plan.get("plans") or [])),
        "blockers": blockers,
        "verdict": "DRY_RUN_EXECUTOR_PLANS_READY" if not blockers else "DRY_RUN_EXECUTOR_PLANS_BLOCKED",
        "live_enabled": False,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    args = parse_args()
    result = convert(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
