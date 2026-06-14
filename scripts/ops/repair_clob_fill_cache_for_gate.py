#!/usr/bin/env python3
"""Build a cap-safe local CLOB fill cache for the current weather DB.

The CLOB coverage gate is intentionally strict: the persistent cache must match
the current submitted-order universe and no fill aggregate may exceed its local
order cap. This helper writes a repaired candidate cache and can replace the
local cache with a timestamped backup.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_CACHE = ROOT / "runtime/weather_edge_v1/clob_fills.jsonl"
DEFAULT_OUTPUT = ROOT / "runtime/weather_edge_v1/clob_fills.gate_repaired.jsonl"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_order_caps(db_path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
              o.execution_id,
              o.order_id,
              o.shares,
              o.limit_price,
              o.cost_usd,
              o.notional,
              json_extract(o.exchange_response, '$.place.orderID') AS clob_order_id,
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
    finally:
        conn.close()
    caps = {}
    for row in rows:
        order_id = str(row["clob_order_id"] or row["order_id"] or "")
        shares = float(row["shares"] or 0.0)
        limit_price = float(row["limit_price"] or 0.0)
        caps[(str(row["execution_id"]), order_id)] = {
            "max_shares": shares,
            "max_cost": max(
                shares * limit_price,
                float(row["cost_usd"] or 0.0),
                float(row["notional"] or 0.0),
            ),
            "city": row["city"],
            "target_date": row["target_date"],
            "bracket": row["bracket"],
        }
    return caps


def row_sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
    source = str(row.get("source") or "")
    if source == "order_exchange_response_matched":
        priority = 0
    elif row.get("public_trade_key"):
        priority = 1
    else:
        priority = 2
    return (
        priority,
        str(row.get("filled_at_utc") or ""),
        str(row.get("created_at_utc") or ""),
    )


def repair_rows(rows: list[dict[str, Any]], caps: dict[tuple[str, str], dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    dropped_stale = 0
    for row in rows:
        key = (str(row.get("execution_id") or ""), str(row.get("order_id") or ""))
        if key not in caps:
            dropped_stale += 1
            continue
        grouped[key].append(row)

    kept: list[dict[str, Any]] = []
    dropped_over_cap: list[dict[str, Any]] = []
    dropped_duplicate_fill_id = 0
    seen_fill_ids: set[str] = set()
    for key, group in grouped.items():
        cap = caps[key]
        shares_sum = 0.0
        cost_sum = 0.0
        for row in sorted(group, key=row_sort_key):
            fill_id = str(row.get("fill_id") or "")
            if fill_id in seen_fill_ids:
                dropped_duplicate_fill_id += 1
                continue
            shares = float(row.get("filled_shares") or 0.0)
            price = float(row.get("filled_price") or 0.0)
            cost = shares * price
            next_shares = shares_sum + shares
            next_cost = cost_sum + cost
            if cap["max_shares"] > 0 and next_shares > cap["max_shares"] + 1e-6:
                dropped_over_cap.append({**row, "drop_reason": "shares_over_cap", "cap": cap})
                continue
            if cap["max_cost"] > 0 and next_cost > cap["max_cost"] + 0.02:
                dropped_over_cap.append({**row, "drop_reason": "cost_over_cap", "cap": cap})
                continue
            kept.append(row)
            seen_fill_ids.add(fill_id)
            shares_sum = next_shares
            cost_sum = next_cost

    kept.sort(key=lambda row: (str(row.get("filled_at_utc") or ""), str(row.get("execution_id") or ""), str(row.get("fill_id") or "")))
    summary = {
        "input_rows": len(rows),
        "output_rows": len(kept),
        "order_caps": len(caps),
        "dropped_stale_order_rows": dropped_stale,
        "dropped_over_cap_rows": len(dropped_over_cap),
        "dropped_duplicate_fill_id_rows": dropped_duplicate_fill_id,
        "sample_dropped_over_cap": [
            {
                "fill_id": row.get("fill_id"),
                "execution_id": row.get("execution_id"),
                "order_id": row.get("order_id"),
                "filled_shares": row.get("filled_shares"),
                "filled_price": row.get("filled_price"),
                "filled_at_utc": row.get("filled_at_utc"),
                "drop_reason": row.get("drop_reason"),
                "cap": row.get("cap"),
            }
            for row in dropped_over_cap[:20]
        ],
    }
    return kept, summary


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    rows = read_jsonl(args.cache)
    caps = load_order_caps(args.db)
    repaired, summary = repair_rows(rows, caps)
    write_jsonl(args.output, repaired)
    summary["db"] = str(args.db)
    summary["cache"] = str(args.cache)
    summary["output"] = str(args.output)

    if args.replace:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = args.cache.with_name(f"{args.cache.name}.bak_gate_repair_{stamp}")
        if args.cache.exists():
            shutil.copy2(args.cache, backup)
        shutil.copy2(args.output, args.cache)
        summary["replaced_cache"] = str(args.cache)
        summary["backup"] = str(backup)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
