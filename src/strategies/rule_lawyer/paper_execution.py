"""Materialize selected rule-lawyer intents through the non-live paper boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.platform.execution_runtime import execution_bundle
from src.platform.storage.jsonl import append_jsonl_row
from src.strategies.rule_lawyer.trade_intents import read_signal_candidates


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append(path: Path, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        append_jsonl_row(path, row)


def sync_paper_execution(output_root: Path) -> dict[str, Any]:
    candidates = {
        str(row.get("candidate_id") or ""): row
        for row in read_signal_candidates(output_root)
    }
    intents = _rows(output_root / "trade_intents.jsonl")
    plan_path = output_root / "paper_plans.jsonl"
    order_path = output_root / "paper_orders.jsonl"
    fill_path = output_root / "paper_fills.jsonl"
    existing_plan_ids = {str(row.get("plan_id") or "") for row in _rows(plan_path)}
    existing_order_ids = {str(row.get("order_id") or "") for row in _rows(order_path)}
    existing_fills = _rows(fill_path)
    existing_fill_ids = {str(row.get("fill_id") or "") for row in existing_fills}
    existing_intents = {str(row.get("intent_id") or "") for row in existing_fills}
    plans: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    blocked: list[dict[str, str]] = []
    for intent in intents:
        intent_id = str(intent.get("intent_id") or "")
        if intent_id in existing_intents:
            continue
        candidate = candidates.get(str(intent.get("candidate_id") or ""))
        try:
            if candidate is None:
                raise ValueError("candidate missing")
            plan, order, fill = execution_bundle(intent=intent, candidate=candidate)
        except (KeyError, TypeError, ValueError) as exc:
            blocked.append({"intent_id": intent_id, "reason": str(exc)})
            continue
        if plan.plan_id not in existing_plan_ids:
            plans.append(plan.to_dict())
            existing_plan_ids.add(plan.plan_id)
        if order.order_id not in existing_order_ids:
            orders.append(order.to_dict())
            existing_order_ids.add(order.order_id)
        if fill.fill_id not in existing_fill_ids:
            fills.append(fill.to_dict())
            existing_fill_ids.add(fill.fill_id)
        existing_intents.add(intent_id)
    _append(plan_path, plans)
    _append(order_path, orders)
    _append(fill_path, fills)
    return {
        "schema_version": "dispute_paper_execution_sync_v1",
        "input_intents": len(intents),
        "new_plans": len(plans),
        "new_orders": len(orders),
        "new_fills": len(fills),
        "total_fills": len(existing_intents),
        "blocked": blocked,
        "actual_notional": 0.0,
        "live_authority": False,
    }
