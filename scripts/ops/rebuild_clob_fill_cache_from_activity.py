#!/usr/bin/env python3
"""Rebuild the persistent CLOB fill cache from Polymarket public activity.

Use this when the existing cache was built with a per-order fill assumption and
therefore under-counted partial fills. The script is read-only against
Polymarket and writes only the local JSONL cache when --replace is supplied.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.weather_polymarket_account_activity import iter_activity  # noqa: E402
from weather_dashboard.ingest.clob_fill_sync import (  # noqa: E402
    _exact_activity_fee_for_fill,
    _extract_immediate_place_fill,
    _fallback_fee_details,
    _index_public_activity_by_tx,
    _make_fill_id,
    _make_public_trade_fill_id,
    _maker_only,
    _public_buy_fee_details,
    _public_trade_key,
    _select_public_partial_fills,
    _ts_to_iso,
)


def load_orders(db_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    o.execution_id,
                    o.order_id,
                    json_extract(o.exchange_response, '$.place.orderID') AS clob_order_id,
                    o.shares,
                    o.limit_price,
                    o.placed_at_utc,
                    o.order_side,
                    o.exchange_response,
                    sig.condition_id,
                    sig.token_id
                FROM orders o
                JOIN plans   p   ON o.plan_id   = p.plan_id
                JOIN signals sig ON p.signal_id = sig.signal_id
                WHERE o.venue  = 'polymarket_clob'
                  AND o.status = 'submitted'
                ORDER BY o.placed_at_utc ASC
                """
            )
        ]
    finally:
        conn.close()


def placed_ts(row: dict[str, Any]) -> int:
    raw = str(row.get("placed_at_utc") or "")
    if not raw:
        return 0
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp())


def build_cache_rows(orders: list[dict[str, Any]], activity_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    public_trades = [row for row in activity_rows if row.get("type") == "TRADE"]
    activity_by_tx = _index_public_activity_by_tx(public_trades)
    used: set[str] = set()
    cache_rows: list[dict[str, Any]] = []
    orders_with_fill = 0
    immediate_match_rows = 0
    for order in orders:
        execution_id = str(order["execution_id"])
        clob_order_id = str(order.get("clob_order_id") or order.get("order_id") or "")
        if not clob_order_id:
            continue
        immediate_fill = _extract_immediate_place_fill(order)
        if immediate_fill is not None:
            fee_details = _exact_activity_fee_for_fill(
                transaction_hashes=immediate_fill["transaction_hashes"],
                activity_by_tx=activity_by_tx,
                condition_id=str(order.get("condition_id") or ""),
                token_id=str(order.get("token_id") or ""),
                order_side=str(order.get("order_side") or ""),
                expected_shares=immediate_fill["filled_shares"],
            ) or {
                key: immediate_fill[key]
                for key in ("fees_usd", "fee_source", "fee_rate", "transaction_hash", "fee_metadata")
            }
            created_at = dt.datetime.now(dt.timezone.utc).isoformat()
            cache_rows.append(
                {
                    "fill_id": _make_fill_id(execution_id, clob_order_id),
                    "execution_id": execution_id,
                    "order_id": clob_order_id,
                    "filled_shares": immediate_fill["filled_shares"],
                    "filled_price": immediate_fill["filled_price"],
                    "fees_usd": fee_details["fees_usd"],
                    "fee_source": fee_details["fee_source"],
                    "fee_rate": fee_details["fee_rate"],
                    "fee_metadata": fee_details["fee_metadata"],
                    "transaction_hash": fee_details["transaction_hash"],
                    "filled_at_utc": immediate_fill["filled_at_utc"],
                    "created_at_utc": created_at,
                    "source": "order_exchange_response_matched",
                }
            )
            orders_with_fill += 1
            immediate_match_rows += 1
            continue
        try:
            shares = float(order.get("shares") or 0.0)
        except (TypeError, ValueError):
            shares = 0.0
        try:
            limit_price = float(order.get("limit_price") or 0.0)
        except (TypeError, ValueError):
            limit_price = 0.0
        selected = _select_public_partial_fills(
            public_trades,
            used_public_trade_keys=used,
            condition_id=str(order.get("condition_id") or ""),
            token_id=str(order.get("token_id") or ""),
            order_side=str(order.get("order_side") or ""),
            limit_price=limit_price,
            row_shares=shares,
            placed_ts=placed_ts(order),
        )
        if selected:
            orders_with_fill += 1
        for idx, trade in enumerate(selected):
            key = _public_trade_key(trade)
            fill_id = (
                _make_fill_id(execution_id, clob_order_id)
                if idx == 0
                else _make_public_trade_fill_id(execution_id, key)
            )
            try:
                filled_shares = float(trade.get("size") or 0.0)
            except (TypeError, ValueError):
                filled_shares = 0.0
            try:
                filled_price = float(trade.get("price") or 0.0)
            except (TypeError, ValueError):
                filled_price = 0.0
            filled_at = _ts_to_iso(trade.get("timestamp"))
            fee_details = _public_buy_fee_details(trade) or _fallback_fee_details(
                shares=filled_shares,
                price=filled_price,
                maker_only=_maker_only(order),
            )
            created_at = dt.datetime.now(dt.timezone.utc).isoformat()
            cache_rows.append(
                {
                    "fill_id": fill_id,
                    "execution_id": execution_id,
                    "order_id": clob_order_id,
                    "filled_shares": filled_shares,
                    "filled_price": filled_price,
                    "fees_usd": fee_details["fees_usd"],
                    "fee_source": fee_details["fee_source"],
                    "fee_rate": fee_details["fee_rate"],
                    "fee_metadata": fee_details["fee_metadata"],
                    "filled_at_utc": filled_at,
                    "created_at_utc": created_at,
                    "source": "polymarket_public_activity",
                    "public_trade_key": key,
                    "transaction_hash": fee_details["transaction_hash"],
                }
            )
            used.add(key)
    summary = {
        "orders": len(orders),
        "orders_with_fill": orders_with_fill,
        "immediate_match_rows": immediate_match_rows,
        "activity_trade_rows": len(public_trades),
        "cache_rows": len(cache_rows),
        "cache_cost_usd": round(sum(float(r["filled_shares"]) * float(r["filled_price"]) for r in cache_rows), 6),
        "used_public_trades": len(used),
    }
    return cache_rows, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=ROOT / "runtime" / "weather.db")
    parser.add_argument("--cache-path", type=Path, default=ROOT / "runtime" / "weather_edge_v1" / "clob_fills.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime" / "weather_edge_v1" / "clob_fills.rebuilt.jsonl")
    parser.add_argument("--max-rows", type=int, default=10000)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    wallet = os.getenv("PM_ADDRESS", "").strip()
    if not wallet:
        raise SystemExit("missing PM_ADDRESS")

    orders = load_orders(args.db_path)
    activity = iter_activity(wallet, max_rows=args.max_rows)
    rows, summary = build_cache_rows(orders, activity)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    summary["output"] = str(args.output)

    if args.replace:
        backup = args.cache_path.with_suffix(args.cache_path.suffix + f".bak_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
        if args.cache_path.exists():
            shutil.copy2(args.cache_path, backup)
        shutil.copy2(args.output, args.cache_path)
        summary["replaced_cache"] = str(args.cache_path)
        summary["backup"] = str(backup)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
