#!/usr/bin/env python3
"""Dry-run proof for an all-YES FOK basket executor.

This script is fail-closed by default. The ``check`` command imports only CLOB
type definitions, never reads signing credentials, and never submits or cancels
orders. It verifies that the all-YES executor bridge has enough basket-level
shape to be handed to a future signed FOK executor without losing the all-leg
contract.
"""

from __future__ import annotations

import argparse
import json
import os
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
GATE_JSON_DEFAULT = RUN_DIR_DEFAULT / "live_prep_gate.json"
STRATEGY_ID = "all_yes_underround_basket_v0"
EXECUTION_CONTRACT_VERSION = "all_yes_execution_contract_v0"
REQUIRED_ORDER_TYPE = "FOK"
LIVE_GATE_READY_VERDICT = "READY_FOR_DEPLOY_REVIEW"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "execute"])
    parser.add_argument("--plan-json", default=str(PLAN_JSON_DEFAULT))
    parser.add_argument("--executor-jsonl", default=str(EXECUTOR_JSONL_DEFAULT))
    parser.add_argument("--gate-json", default=str(GATE_JSON_DEFAULT))
    parser.add_argument("--run-dir", default=str(RUN_DIR_DEFAULT))
    parser.add_argument("--mock-fill-json", default="")
    parser.add_argument("--out-jsonl", default="")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--arm", action="store_true")
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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def clob_order_type_audit() -> dict[str, Any]:
    errors: list[str] = []
    for module_name in ("py_clob_client_v2.clob_types", "py_clob_client.clob_types"):
        try:
            module = __import__(module_name, fromlist=["OrderType"])
            order_type = getattr(module, "OrderType")
            names = sorted(name for name in dir(order_type) if name.isupper())
            return {
                "module": module_name,
                "order_types": names,
                "supports_fok": REQUIRED_ORDER_TYPE in names,
                "error": None,
            }
        except Exception as exc:
            errors.append(f"{module_name}:{type(exc).__name__}:{exc}")
    return {
        "module": None,
        "order_types": [],
        "supports_fok": False,
        "error": ";".join(errors),
    }


def rows_by_basket(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        basket_id = str(row.get("source_basket_plan_id") or "")
        if not basket_id:
            continue
        grouped.setdefault(basket_id, []).append(row)
    return grouped


def mock_results_by_basket(mock_fill: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in list(mock_fill.get("basket_results") or []):
        basket_id = str(row.get("source_basket_plan_id") or row.get("plan_id") or "")
        if basket_id:
            result[basket_id] = list(row.get("leg_results") or [])
    return result


def contract_blockers(contract: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if contract.get("contract_version") != EXECUTION_CONTRACT_VERSION:
        blockers.append("execution_contract_bad_version")
    if contract.get("live_submit_enabled") is not False:
        blockers.append("execution_contract_live_submit_enabled_not_false")
    if contract.get("order_submission_mode") != "disabled_dry_run_only":
        blockers.append("execution_contract_order_submission_mode_not_disabled")
    if contract.get("all_leg_or_none_required") is not True:
        blockers.append("execution_contract_all_leg_or_none_not_true")
    if contract.get("per_leg_time_in_force") != "FOK_OR_CANCEL_REQUIRED_BEFORE_LIVE":
        blockers.append("execution_contract_time_in_force_not_fok")
    if contract.get("partial_fill_policy") != "reject_partial_before_live":
        blockers.append("execution_contract_partial_fill_policy_not_reject")
    return blockers


def basket_state(
    basket_plan: dict[str, Any],
    executor_rows: list[dict[str, Any]],
    mock_leg_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    basket_id = str(basket_plan.get("plan_id") or "")
    intents = list(basket_plan.get("order_intents") or [])
    blockers = contract_blockers(dict(basket_plan.get("execution_contract") or {}))
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
        if row.get("all_yes_all_leg_or_none_required") is not True:
            blockers.append(f"executor_leg_all_leg_required_not_true:{role}")
        if row.get("execution_policy") != "all_yes_all_leg_or_none_v0":
            blockers.append(f"executor_leg_bad_policy:{role}")
        if row.get("order_type") != REQUIRED_ORDER_TYPE or row.get("time_in_force") != REQUIRED_ORDER_TYPE:
            blockers.append(f"executor_leg_not_fok:{role}")
        if row.get("maker_only") is not False:
            blockers.append(f"executor_leg_not_taker_fok:{role}")
        if str(row.get("token_id") or "") != str(intent.get("token_id") or intent.get("condition_id") or ""):
            blockers.append(f"executor_leg_token_mismatch:{role}")
        if abs(to_float(row.get("limit_price")) - to_float(intent.get("limit_price"))) > 1e-9:
            blockers.append(f"executor_leg_price_mismatch:{role}")
        if abs(to_float(row.get("size")) - to_float(intent.get("shares"))) > 1e-9:
            blockers.append(f"executor_leg_size_mismatch:{role}")

    planned_notional = round(sum(to_float(row.get("notional")) for row in executor_rows), 6)
    basket_cost = to_float(basket_plan.get("basket_cost_usd"))
    if basket_cost > 0 and planned_notional > round(basket_cost + 1e-6, 6):
        blockers.append("executor_notional_exceeds_basket_cost")

    common = {
        "source_basket_plan_id": basket_id,
        "city": basket_plan.get("city"),
        "event_date": basket_plan.get("event_date"),
        "leg_intents": len(intents),
        "executor_legs": len(executor_rows),
        "planned_notional": planned_notional,
        "basket_cost_usd": basket_cost,
        "no_order_placed": True,
    }
    if blockers:
        return {
            **common,
            "status": "blocked_invalid_fok_contract",
            "blockers": sorted(set(blockers)),
            "cancel_unfilled_leg_indexes": [],
            "unwind_filled_leg_indexes": [],
        }
    if mock_leg_results is None:
        return {
            **common,
            "status": "not_armed_dry_run_fok",
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
            **common,
            "status": "fail_closed_cancel_or_unwind_required",
            "blockers": ["mock_partial_or_rejected_leg"],
            "partial_or_rejected_leg_indexes": sorted(partial_or_rejected),
            "missing_leg_result_indexes": sorted(missing),
            "cancel_unfilled_leg_indexes": sorted(set(unfilled)),
            "unwind_filled_leg_indexes": sorted(set(filled)),
        }

    return {
        **common,
        "status": "all_legs_would_fill_complete",
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
    clob_audit = clob_order_type_audit()
    blockers: list[dict[str, Any]] = []
    passed: list[dict[str, Any]] = []

    if live_plan.get("status") in {"missing", "invalid"}:
        blockers.append({"code": "live_plan_missing_or_invalid", "path": str(plan_path), "status": live_plan.get("status")})
    if live_plan.get("live_now") is not False:
        blockers.append({"code": "live_plan_live_now_not_false", "live_now": live_plan.get("live_now")})
    if live_plan.get("verdict") != "DRY_RUN_PLAN_ONLY":
        blockers.append({"code": "live_plan_not_dry_run", "verdict": live_plan.get("verdict")})
    if not clob_audit.get("supports_fok"):
        blockers.append({"code": "clob_order_type_fok_missing", "clob_order_type_audit": clob_audit})
    if any(row.get("live_enabled") is not False for row in executor_rows):
        blockers.append({"code": "executor_jsonl_contains_live_enabled_leg"})

    basket_states = [
        basket_state(
            plan,
            executor_by_basket.get(str(plan.get("plan_id") or ""), []),
            mock_by_basket.get(str(plan.get("plan_id") or "")) if mock_by_basket else None,
        )
        for plan in list(live_plan.get("plans") or [])
    ]
    invalid = [row for row in basket_states if row.get("status") == "blocked_invalid_fok_contract"]
    fail_closed = [row for row in basket_states if row.get("status") == "fail_closed_cancel_or_unwind_required"]
    if invalid:
        blockers.append({"code": "basket_fok_execution_contract_invalid", "baskets": invalid})
    if fail_closed:
        passed.append({"code": "fok_partial_fill_fail_closed_path_available", "baskets": fail_closed})
    if not blockers:
        passed.append(
            {
                "code": "dry_run_fok_executor_available",
                "message": "FOK executor proof is present, dry-run only, and preserves all-leg grouping.",
                "baskets_checked": len(basket_states),
                "executor_legs_checked": len(executor_rows),
                "clob_order_type_module": clob_audit.get("module"),
            }
        )

    result = {
        "command": "check",
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "live_now": False,
        "live_enabled": False,
        "no_order_placed": True,
        "order_type": REQUIRED_ORDER_TYPE,
        "plan_path": str(plan_path),
        "executor_jsonl": str(executor_path),
        "run_dir": str(run_dir),
        "clob_order_type_audit": clob_audit,
        "baskets_checked": len(basket_states),
        "executor_legs_checked": len(executor_rows),
        "basket_states": basket_states,
        "blockers": blockers,
        "passed": passed,
        "verdict": "DRY_RUN_FOK_EXECUTOR_READY" if not blockers else "DRY_RUN_FOK_EXECUTOR_BLOCKED",
        "live_blockers": [
            "real_credentials_not_loaded_by_check_command",
            "actual_submit_cancel_unwind_not_armed",
            "forward_shadow_sample_gate_must_pass_first",
            "weather_strategy_deploy_review_required",
        ],
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "latest_fok_executor_readiness.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    append_jsonl(run_dir / "fok_executor_readiness_history.jsonl", [result])
    return result


def extract_order_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("order_id", "orderID", "id"):
        value = payload.get(key)
        if value:
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("order_id", "orderID", "id"):
            value = data.get(key)
            if value:
                return str(value)
    return ""


def live_flags_valid(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "live", False) and getattr(args, "confirm_live", False) and getattr(args, "arm", False))


def live_gate_blocker(gate: dict[str, Any]) -> dict[str, Any] | None:
    if gate.get("status") in {"missing", "invalid"}:
        return {
            "code": "live_prep_gate_missing_or_invalid",
            "message": "Live FOK execution requires a valid all-YES live_prep_gate.json.",
            "path": gate.get("path"),
            "status": gate.get("status"),
        }
    if gate.get("verdict") != LIVE_GATE_READY_VERDICT:
        return {
            "code": "live_prep_gate_not_ready",
            "message": "Live FOK execution is blocked until the all-YES live-prep gate reaches READY_FOR_DEPLOY_REVIEW.",
            "verdict": gate.get("verdict"),
            "blocker_codes": [row.get("code") for row in list(gate.get("blockers") or [])],
        }
    return None


def build_live_fok_place_fn():
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds, OrderArgsV2, OrderType
        from py_clob_client_v2.constants import POLYGON

        clob_v2 = True
    except ModuleNotFoundError:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
        from py_clob_client.constants import POLYGON

        OrderArgsV2 = OrderArgs  # type: ignore[assignment]
        clob_v2 = False

    host = os.getenv("CLOB_BASE_URL", "").strip() or os.getenv("PM_API_BASE_URL", "").strip() or "https://clob.polymarket.com"
    chain_id = int(os.getenv("CLOB_CHAIN_ID", str(POLYGON)))
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip() or os.getenv("PM", "").strip()
    if not private_key:
        raise RuntimeError("missing POLYGON_WALLET_PRIVATE_KEY or PM")

    signature_type_raw = int(os.getenv("CLOB_SIGNATURE_TYPE", "-1"))
    funder = os.getenv("PM_ADDRESS", "").strip()
    try:
        signer_addr = ClobClient(host, chain_id=chain_id, key=private_key).get_address()
    except Exception:
        signer_addr = ""
    signature_type = signature_type_raw
    if signature_type < 0:
        signature_type = 1 if funder and signer_addr and funder.lower() != signer_addr.lower() else 0

    api_key = os.getenv("CLOB_API_KEY", "").strip()
    api_secret = os.getenv("CLOB_SECRET", "").strip()
    api_pass = os.getenv("CLOB_PASS_PHRASE", "").strip()
    creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass) if api_key and api_secret and api_pass else None
    client = ClobClient(
        host,
        chain_id=chain_id,
        key=private_key,
        creds=creds,
        signature_type=signature_type,
        funder=funder or None,
    )
    if creds is None:
        if clob_v2:
            client.set_api_creds(client.derive_api_key())
        else:
            client.set_api_creds(client.create_or_derive_api_creds())

    def place(row: dict[str, Any]) -> dict[str, Any]:
        signed_order = client.create_order(
            OrderArgsV2(
                token_id=str(row["token_id"]),
                price=float(row["limit_price"]),
                size=float(row["size"]),
                side=str(row.get("order_side") or "BUY"),
            )
        )
        if clob_v2:
            response = client.post_order(signed_order, order_type=OrderType.FOK)
        else:
            response = client.post_order(signed_order, orderType=OrderType.FOK)
        return {
            "place": response,
            "clob_client": "py_clob_client_v2" if clob_v2 else "py_clob_client",
            "order_type": REQUIRED_ORDER_TYPE,
            "order_id": extract_order_id(response),
        }

    return place


def execute_baskets(
    *,
    args: argparse.Namespace,
    live_place_fn: Any | None = None,
) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    out_path = Path(args.out_jsonl) if args.out_jsonl else run_dir / "fok_executor_orders.jsonl"
    plan_path = Path(args.plan_json)
    executor_path = Path(args.executor_jsonl)
    gate_path = Path(args.gate_json)
    live_plan = read_json(plan_path)
    live_gate = read_json(gate_path)
    executor_rows = read_jsonl(executor_path)
    executor_by_basket = rows_by_basket(executor_rows)
    readiness_args = argparse.Namespace(
        command="check",
        plan_json=str(plan_path),
        executor_jsonl=str(executor_path),
        run_dir=str(run_dir),
        mock_fill_json=getattr(args, "mock_fill_json", ""),
    )
    readiness = build_state(readiness_args)
    rows: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    live_requested = bool(getattr(args, "live", False))
    live_enabled = live_flags_valid(args)

    if live_requested and not live_enabled:
        blockers.append(
            {
                "code": "live_flags_missing",
                "message": "Live FOK execution requires --live --confirm-live --arm together.",
            }
        )
    if live_requested:
        gate_blocker = live_gate_blocker(live_gate)
        if gate_blocker is not None:
            blockers.append(gate_blocker)
    if readiness.get("verdict") != "DRY_RUN_FOK_EXECUTOR_READY":
        blockers.append(
            {
                "code": "fok_readiness_not_ready",
                "verdict": readiness.get("verdict"),
                "blockers": readiness.get("blockers"),
            }
        )

    if live_enabled and not blockers and live_place_fn is None:
        live_place_fn = build_live_fok_place_fn()

    for basket_plan in list(live_plan.get("plans") or []):
        basket_id = str(basket_plan.get("plan_id") or "")
        leg_rows = executor_by_basket.get(basket_id, [])
        filled: list[int] = []
        failed: list[int] = []
        basket_records: list[dict[str, Any]] = []
        for row in leg_rows:
            leg_index_text = str(row.get("child_order_role") or "").replace("all_yes_leg_", "")
            try:
                leg_index = int(leg_index_text)
            except Exception:
                leg_index = -1
            record = {
                "record_type": "all_yes_fok_executor_order",
                "generated_at_utc": now_utc(),
                "strategy_id": STRATEGY_ID,
                "source_basket_plan_id": basket_id,
                "city": basket_plan.get("city"),
                "event_date": basket_plan.get("event_date"),
                "leg_index": leg_index,
                "token_id": row.get("token_id"),
                "bracket": row.get("bracket"),
                "limit_price": row.get("limit_price"),
                "size": row.get("size"),
                "order_type": REQUIRED_ORDER_TYPE,
                "live_requested": live_requested,
                "live_enabled": live_enabled,
                "no_order_placed": bool(blockers) or not live_enabled,
            }
            if blockers:
                record.update({"status": "blocked", "blockers": blockers})
            elif not live_enabled:
                record.update({"status": "dry_run_would_submit_fok"})
            else:
                try:
                    assert live_place_fn is not None
                    response = live_place_fn(row)
                    order_id = extract_order_id(response.get("place")) or str(response.get("order_id") or "")
                    filled.append(leg_index)
                    record.update(
                        {
                            "status": "submitted_fok",
                            "no_order_placed": False,
                            "order_id": order_id,
                            "exchange_response": response,
                        }
                    )
                except Exception as exc:
                    failed.append(leg_index)
                    record.update(
                        {
                            "status": "error_fail_closed",
                            "error": f"{type(exc).__name__}: {exc}",
                            "requires_unwind_filled_leg_indexes": sorted(filled),
                        }
                    )
            basket_records.append(record)
        if live_enabled and failed:
            for record in basket_records:
                record["basket_status"] = "fail_closed_unwind_required"
                record["requires_unwind_filled_leg_indexes"] = sorted(filled)
        elif live_enabled and basket_records:
            for record in basket_records:
                record["basket_status"] = "all_legs_submitted_fok"
        rows.extend(basket_records)

    write_jsonl(out_path, rows)
    result = {
        "command": "execute",
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "live_requested": live_requested,
        "live_enabled": live_enabled,
        "no_order_placed": bool(blockers) or not live_enabled,
        "order_type": REQUIRED_ORDER_TYPE,
        "plan_path": str(plan_path),
        "executor_jsonl": str(executor_path),
        "gate_json": str(gate_path),
        "live_gate_verdict": live_gate.get("verdict"),
        "out_jsonl": str(out_path),
        "baskets": len(list(live_plan.get("plans") or [])),
        "orders_written": len(rows),
        "blockers": blockers,
        "readiness_verdict": readiness.get("verdict"),
        "verdict": (
            "FOK_EXECUTION_BLOCKED"
            if blockers
            else ("LIVE_FOK_EXECUTION_ATTEMPTED" if live_enabled else "DRY_RUN_FOK_EXECUTOR_EXECUTED")
        ),
    }
    (run_dir / "latest_fok_executor_execute.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    append_jsonl(run_dir / "fok_executor_execute_history.jsonl", [result])
    return result


def main() -> None:
    args = parse_args()
    if args.command == "execute":
        result = execute_baskets(args=args)
    else:
        result = build_state(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
