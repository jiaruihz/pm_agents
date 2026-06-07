#!/usr/bin/env python3
"""Fail-closed consistency gate for weather live CLOB fills.

This script is intentionally read-only. It checks whether recovered CLOB fills
are internally safe enough to use as the basis for live_real strategy PnL.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def _round(value: float) -> float:
    return round(float(value or 0.0), 6)


def load_order_caps(conn: sqlite3.Connection) -> dict[tuple[str, str], dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
          o.execution_id,
          o.order_id,
          o.shares,
          o.limit_price,
          o.cost_usd,
          o.notional,
          o.exchange_response,
          sig.city,
          sig.target_date,
          sig.bracket
        FROM orders o
        JOIN plans p ON p.plan_id = o.plan_id
        JOIN signals sig ON sig.signal_id = p.signal_id
        WHERE o.venue='polymarket_clob'
          AND o.status='submitted'
        """
    ).fetchall()
    out = {}
    for row in rows:
        exchange_cost = 0.0
        exchange_shares = 0.0
        try:
            response = json.loads(row["exchange_response"] or "{}")
            place = response.get("place") or {}
            exchange_cost = float(place.get("makingAmount") or 0.0)
            exchange_shares = float(place.get("takingAmount") or 0.0)
        except (TypeError, ValueError, json.JSONDecodeError):
            exchange_cost = 0.0
            exchange_shares = 0.0
        shares = float(row["shares"] or 0.0)
        limit_price = float(row["limit_price"] or 0.0)
        out[(str(row["execution_id"]), str(row["order_id"]))] = {
            "shares": float(row["shares"] or 0.0),
            "limit_price": float(row["limit_price"] or 0.0),
            "max_shares": max(shares, exchange_shares),
            "max_cost": max(
                shares * limit_price,
                float(row["cost_usd"] or 0.0),
                float(row["notional"] or 0.0),
                exchange_cost,
            ),
            "notional": float(row["notional"] or 0.0),
            "city": row["city"],
            "target_date": row["target_date"],
            "bracket": row["bracket"],
        }
    return out


def summarize_rows(
    rows: list[dict[str, Any]],
    *,
    order_caps: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    by_execution: dict[str, dict[str, float]] = defaultdict(
        lambda: {"fills": 0.0, "shares": 0.0, "cost": 0.0}
    )
    by_order: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"fills": 0.0, "shares": 0.0, "cost": 0.0}
    )
    missing_order_rows = 0
    missing_order_cost = 0.0
    for row in rows:
        execution_id = str(row.get("execution_id") or "")
        order_id = str(row.get("order_id") or "")
        shares = float(row.get("filled_shares") or 0.0)
        price = float(row.get("filled_price") or 0.0)
        cost = shares * price
        by_execution[execution_id]["fills"] += 1.0
        by_execution[execution_id]["shares"] += shares
        by_execution[execution_id]["cost"] += cost
        key = (execution_id, order_id)
        if key not in order_caps:
            missing_order_rows += 1
            missing_order_cost += cost
            continue
        by_order[key]["fills"] += 1.0
        by_order[key]["shares"] += shares
        by_order[key]["cost"] += cost

    over_order = []
    for key, agg in by_order.items():
        cap = order_caps[key]
        max_shares = cap["max_shares"]
        max_cost = cap["max_cost"]
        shares_over = agg["shares"] - max_shares
        cost_over = agg["cost"] - max_cost
        if shares_over > 1e-6 or cost_over > 0.05:
            over_order.append(
                {
                    "execution_id": key[0],
                    "order_id": key[1],
                    "city": cap["city"],
                    "target_date": cap["target_date"],
                    "bracket": cap["bracket"],
                    "fills": int(agg["fills"]),
                    "fill_shares": _round(agg["shares"]),
                    "order_shares": _round(max_shares),
                    "fill_cost": _round(agg["cost"]),
                    "order_limit_cost": _round(max_cost),
                    "shares_over": _round(max(shares_over, 0.0)),
                    "cost_over": _round(max(cost_over, 0.0)),
                }
            )
    over_order.sort(key=lambda row: (row["cost_over"], row["shares_over"]), reverse=True)
    total_cost = sum(float(row.get("filled_shares") or 0.0) * float(row.get("filled_price") or 0.0) for row in rows)
    return {
        "rows": len(rows),
        "distinct_fill_ids": len({str(row.get("fill_id") or "") for row in rows if row.get("fill_id")}),
        "distinct_executions": len(by_execution),
        "cost_usd": _round(total_cost),
        "multi_fill_executions": sum(1 for agg in by_execution.values() if agg["fills"] > 1),
        "missing_order_rows": missing_order_rows,
        "missing_order_cost_usd": _round(missing_order_cost),
        "over_order_keys": len(over_order),
        "over_order_cost_usd": _round(sum(float(row["fill_cost"]) for row in over_order)),
        "over_order_excess_cost_usd": _round(sum(float(row["cost_over"]) for row in over_order)),
        "top_over_order": over_order[:25],
    }


def load_db_fill_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              f.fill_id,
              f.execution_id,
              f.order_id,
              f.filled_shares,
              f.filled_price
            FROM fills f
            JOIN orders o ON o.execution_id = f.execution_id
            WHERE o.venue='polymarket_clob'
              AND o.status='submitted'
            """
        )
    ]


def load_cache_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _fill_id_set(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("fill_id") or "") for row in rows if row.get("fill_id")}


def _cost(rows: list[dict[str, Any]]) -> float:
    return sum(
        float(row.get("filled_shares") or 0.0) * float(row.get("filled_price") or 0.0)
        for row in rows
    )


def fact_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS rows,
          COUNT(DISTINCT fill_id) AS fill_ids,
          COUNT(DISTINCT execution_id) AS executions,
          COALESCE(SUM(cost_usd), 0.0) AS cost_usd
        FROM fact_trades
        WHERE trade_class='live_real'
        """
    ).fetchone()
    return {
        "rows": int(row["rows"] or 0),
        "fill_ids": int(row["fill_ids"] or 0),
        "executions": int(row["executions"] or 0),
        "cost_usd": _round(row["cost_usd"] or 0.0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "runtime" / "weather.db")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / "runtime" / "weather_edge_v1" / "clob_fills.jsonl",
    )
    parser.add_argument("--extra-cache", type=Path, action="append", default=[])
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db))
    order_caps = load_order_caps(conn)
    db_rows = load_db_fill_rows(conn)
    db_fills = summarize_rows(db_rows, order_caps=order_caps)
    db_fill_ids = _fill_id_set(db_rows)
    facts = fact_summary(conn)
    payload: dict[str, Any] = {
        "db": str(args.db),
        "order_caps": len(order_caps),
        "db_fills": db_fills,
        "fact_trades_live_real": facts,
        "db_fill_cost_minus_fact_cost": _round(db_fills["cost_usd"] - facts["cost_usd"]),
        "db_vs_primary_cache": None,
        "cache_checks": {},
        "gate_pass": False,
        "fail_reasons": [],
    }
    cache_paths = [args.cache, *args.extra_cache]
    for index, cache_path in enumerate(cache_paths):
        cache_rows = load_cache_rows(cache_path)
        cache_summary = summarize_rows(cache_rows, order_caps=order_caps)
        payload["cache_checks"][str(cache_path)] = cache_summary

        if cache_summary["missing_order_rows"]:
            payload["fail_reasons"].append(
                f"cache_fills_missing_or_mismatched_order_id:{cache_path}"
            )
        if cache_summary["over_order_keys"]:
            payload["fail_reasons"].append(f"cache_fills_exceed_order_cap:{cache_path}")

        if index == 0:
            cache_fill_ids = _fill_id_set(cache_rows)
            db_not_in_cache = sorted(db_fill_ids - cache_fill_ids)
            cache_not_in_db = sorted(cache_fill_ids - db_fill_ids)
            cost_delta = _round(_cost(db_rows) - _cost(cache_rows))
            payload["db_vs_primary_cache"] = {
                "db_not_in_cache": len(db_not_in_cache),
                "cache_not_in_db": len(cache_not_in_db),
                "db_cost_minus_cache_cost": cost_delta,
                "sample_db_not_in_cache": db_not_in_cache[:25],
                "sample_cache_not_in_db": cache_not_in_db[:25],
            }
            if db_not_in_cache or cache_not_in_db:
                payload["fail_reasons"].append("db_cache_fill_id_mismatch")
            if abs(cost_delta) > 0.01:
                payload["fail_reasons"].append("db_cache_cost_mismatch")

    if db_fills["missing_order_rows"]:
        payload["fail_reasons"].append("db_fills_missing_or_mismatched_order_id")
    if db_fills["over_order_keys"]:
        payload["fail_reasons"].append("db_fills_exceed_order_cap")
    if abs(float(payload["db_fill_cost_minus_fact_cost"])) > 0.01:
        payload["fail_reasons"].append("fact_trades_cost_not_equal_fills_cost")
    payload["gate_pass"] = not payload["fail_reasons"]

    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if payload["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
