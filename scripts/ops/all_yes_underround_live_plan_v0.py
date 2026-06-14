#!/usr/bin/env python3
"""Build non-submitting live-prep plans for all-YES underround baskets.

This script is intentionally dry-run only. It does not import a CLOB client,
does not read signing credentials, and does not submit or cancel orders. Its
job is to turn current scanner candidates into an auditable all-leg plan that a
future live executor can be reviewed against.
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

from scripts.ops.all_yes_underround_guards import BasketGuardConfig, check_candidate, decision_to_dict


SCAN_JSON_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_paper_v0" / "latest_scan.json"
RUN_DIR_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_paper_v0"
STRATEGY_ID = "all_yes_underround_basket_v0"
EXECUTION_CONTRACT_VERSION = "all_yes_execution_contract_v0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan"])
    parser.add_argument("--scan-json", default=str(SCAN_JSON_DEFAULT))
    parser.add_argument("--run-dir", default=str(RUN_DIR_DEFAULT))
    parser.add_argument("--shares-per-leg", type=float, default=5.0)
    parser.add_argument("--max-baskets-per-cycle", type=int, default=2)
    parser.add_argument("--max-basket-cost-usd", type=float, default=5.0)
    parser.add_argument("--min-underround", type=float, default=0.02)
    parser.add_argument("--max-spread", type=float, default=0.05)
    parser.add_argument("--max-snapshot-age-seconds", type=float, default=180.0)
    parser.add_argument("--decision-ts-utc")
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def plan_id(scan: dict[str, Any], candidate: dict[str, Any]) -> str:
    raw = "|".join(
        [
            STRATEGY_ID,
            str(scan.get("snapshot_summary", {}).get("snapshot_ts_utc_max")),
            str(candidate.get("event_date")),
            str(candidate.get("city")),
            str(candidate.get("event_slug")),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def build_execution_contract(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "contract_version": EXECUTION_CONTRACT_VERSION,
        "live_submit_enabled": False,
        "no_order_placed": True,
        "order_submission_mode": "disabled_dry_run_only",
        "all_leg_or_none_required": True,
        "per_leg_time_in_force": "FOK_OR_CANCEL_REQUIRED_BEFORE_LIVE",
        "partial_fill_policy": "reject_partial_before_live",
        "partial_fill_live_action": "cancel_unfilled_then_unwind_filled_or_pause_strategy",
        "max_snapshot_age_seconds": args.max_snapshot_age_seconds,
        "required_before_live": [
            "signed_all_leg_order_submitter",
            "post_submit_fill_polling",
            "cancel_all_unfilled_legs_on_any_partial_or_reject",
            "unwind_filled_yes_legs_if_cancel_fails",
            "basket_cost_hard_cap_enforced_at_submit",
            "weather_strategy_deploy_review",
        ],
    }


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    scan_path = Path(args.scan_json)
    scan = read_json(scan_path)
    decision_ts = args.decision_ts_utc or now_utc()
    guard_cfg = BasketGuardConfig(
        min_underround=args.min_underround,
        shares_per_leg=args.shares_per_leg,
        max_basket_cost_usd=args.max_basket_cost_usd,
        max_yes_spread=args.max_spread,
        max_candidates_per_cycle=args.max_baskets_per_cycle,
        max_snapshot_age_seconds=args.max_snapshot_age_seconds,
    )

    plans: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    execution_contract = build_execution_contract(args)
    for candidate in list(scan.get("paper_shadow_candidates") or []):
        guard = check_candidate(cfg=guard_cfg, repo_root=ROOT, candidate=candidate, decision_ts_utc=decision_ts)
        common = {
            "strategy_id": STRATEGY_ID,
            "plan_id": plan_id(scan, candidate),
            "generated_at_utc": now_utc(),
            "decision_ts_utc": decision_ts,
            "source_scan": str(scan_path),
            "source_snapshot": scan.get("snapshot_path"),
            "event_date": candidate.get("event_date"),
            "city": candidate.get("city"),
            "event_slug": candidate.get("event_slug"),
            "underround": candidate.get("underround"),
            "total_yes_ask_cost": candidate.get("total_yes_ask_cost"),
            "max_yes_spread": candidate.get("max_yes_spread"),
            "live_submit_enabled": False,
            "no_order_placed": True,
            "execution_mode": "dry_run_plan_only",
            "guard": decision_to_dict(guard),
        }
        if not guard.allow:
            rejected.append({**common, "status": "rejected_by_guard"})
            continue
        if len(plans) >= args.max_baskets_per_cycle:
            continue
        plans.append(
            {
                **common,
                "status": "would_submit_after_deploy_review",
                "basket_cost_usd": guard.basket_cost_usd,
                "expected_profit_usd": guard.expected_profit_usd,
                "all_leg_or_none_required": True,
                "partial_fill_policy": "block_live_until_cancel_or_unwind_engine_exists",
                "execution_contract": execution_contract,
                "live_blockers": [
                    "no_signed_order_submitter_in_this_script",
                    "no_partial_fill_cancel_or_unwind_engine",
                    "requires_weather_strategy_deploy_review",
                ],
                "order_intents": [
                    {
                        "leg_index": index,
                        "condition_id": leg.get("condition_id"),
                        "bracket": leg.get("bracket"),
                        "token_side": "YES",
                        "order_side": "BUY",
                        "limit_price": leg.get("price"),
                        "shares": leg.get("shares"),
                        "notional_usd": leg.get("notional_usd"),
                        "available_ask_size": leg.get("available_ask_size"),
                        "time_in_force": "FOK_OR_CANCEL_REQUIRED_BEFORE_LIVE",
                        "submit_now": False,
                    }
                    for index, leg in enumerate(guard.leg_orders)
                ],
            }
        )

    result = {
        "command": "plan",
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "live_now": False,
        "scan_path": str(scan_path),
        "run_dir": str(run_dir),
        "scanner_candidate_count": len(list(scan.get("paper_shadow_candidates") or [])),
        "planned_baskets": len(plans),
        "rejected_baskets": len(rejected),
        "execution_contract": execution_contract,
        "plans": plans,
        "rejected": rejected,
        "verdict": "DRY_RUN_PLAN_ONLY",
        "next_actions": [
            "Keep this as a review artifact only; do not submit orders from it.",
            "Before live, implement signed all-leg-or-none submission plus partial-fill cancel/unwind.",
            "Deploy any live executor only through weather-strategy-deploy.",
        ],
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "latest_live_plan.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_jsonl(run_dir / "live_plan_history.jsonl", [result])
    return result


def main() -> None:
    args = parse_args()
    result = build_plan(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
