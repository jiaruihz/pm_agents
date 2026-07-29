#!/usr/bin/env python3
"""Audit Core Carry maker fills against authenticated CLOB trade order ids."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import TradeParams
from py_clob_client_v2.constants import POLYGON


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def venue_order_id(row: dict[str, Any]) -> str:
    response = row.get("exchange_response") if isinstance(row.get("exchange_response"), dict) else {}
    place = response.get("place") if isinstance(response.get("place"), dict) else {}
    for payload in (row, place, response):
        for key in ("venue_order_id", "expected_venue_order_id", "order_id", "orderID", "id"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    return ""


def parse_epoch(value: Any) -> int:
    raw = str(value or "").replace("Z", "+00:00")
    if not raw:
        return 0
    parsed = dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp())


def build_client() -> ClobClient:
    host = os.getenv("CLOB_BASE_URL", "").strip() or "https://clob.polymarket.com"
    private_key = (
        os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip()
        or os.getenv("PM", "").strip()
    )
    if not private_key:
        raise RuntimeError("missing POLYGON_WALLET_PRIVATE_KEY or PM")
    funder = os.getenv("PM_ADDRESS", "").strip() or None
    probe = ClobClient(host, chain_id=POLYGON, key=private_key)
    signer = probe.get_address()
    signature_type = int(os.getenv("CLOB_SIGNATURE_TYPE", "-1"))
    if signature_type < 0:
        signature_type = int(bool(funder and signer.lower() != funder.lower()))
    client = ClobClient(
        host,
        chain_id=POLYGON,
        key=private_key,
        signature_type=signature_type,
        funder=funder,
    )
    client.set_api_creds(client.derive_api_key())
    return client


def matched_by_order(trades: list[dict[str, Any]]) -> dict[str, float]:
    matched: defaultdict[str, float] = defaultdict(float)
    seen: set[tuple[str, str]] = set()
    for trade in trades:
        trade_id = str(trade.get("id") or "")
        taker_id = str(trade.get("taker_order_id") or "")
        if taker_id and (trade_id, taker_id) not in seen:
            matched[taker_id] += float(trade.get("size") or 0.0)
            seen.add((trade_id, taker_id))
        for maker in trade.get("maker_orders") or []:
            order_id = str(maker.get("order_id") or "")
            if order_id and (trade_id, order_id) not in seen:
                matched[order_id] += float(maker.get("matched_amount") or 0.0)
                seen.add((trade_id, order_id))
    return dict(matched)


def embedded_authenticated_matches(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Read authenticated order snapshots already persisted in lifecycle rows."""

    matched: defaultdict[str, float] = defaultdict(float)

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        order_id = str(value.get("order_id") or value.get("id") or "")
        status = str(
            value.get("raw_venue_status")
            or value.get("status")
            or ""
        ).upper()
        shares = float(
            value.get("matched_shares")
            or value.get("size_matched")
            or value.get("sizeMatched")
            or 0.0
        )
        if order_id and shares > 0 and status in {"MATCHED", "FILLED"}:
            matched[order_id] = max(matched[order_id], shares)
        for item in value.values():
            visit(item)

    for row in rows:
        visit(row.get("exchange_response"))
    return dict(matched)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", type=Path, required=True)
    parser.add_argument("--fill-cache", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    load_dotenv()
    rows = [
        row
        for row in iter_jsonl(args.orders)
        if str(row.get("strategy_id") or "") in {
            "current_yes_core_carry_v2",
            "current_yes_core_carry_v3",
        }
    ]
    maker_rows = [
        row
        for row in rows
        if str(row.get("execution_policy") or "")
        == "current_yes_residual_carry_maker_v1"
    ]
    initial_intents = [row for row in maker_rows if row.get("child_order_role") == "maker"]
    posted_rows = [
        row
        for row in maker_rows
        if str(row.get("status") or "") == "submitted" and venue_order_id(row)
    ]
    local_orders = {venue_order_id(row): row for row in posted_rows}
    taker_rows = [
        row
        for row in rows
        if str(row.get("execution_policy") or "")
        == "current_yes_residual_carry_taker_v1"
        and str(row.get("status") or "") == "submitted"
        and venue_order_id(row)
    ]
    local_taker_orders = {venue_order_id(row): row for row in taker_rows}

    tokens: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in posted_rows:
        tokens[str(row.get("token_id") or "")].append(row)

    client = build_client()
    authenticated_trades: list[dict[str, Any]] = []
    for token_id, token_rows in tokens.items():
        after = max(0, min(parse_epoch(row.get("created_at_utc")) for row in token_rows) - 60)
        authenticated_trades.extend(
            client.get_trades(TradeParams(asset_id=token_id, after=after))
        )
    authenticated_trades = list(
        {str(row.get("id") or json.dumps(row, sort_keys=True)): row for row in authenticated_trades}.values()
    )
    matched = matched_by_order(authenticated_trades)
    for order_id, shares in embedded_authenticated_matches(maker_rows).items():
        matched[order_id] = max(matched.get(order_id, 0.0), shares)

    intent_rows: list[dict[str, Any]] = []
    for intent in initial_intents:
        key = (
            str(intent.get("city") or ""),
            str(intent.get("target_date") or ""),
            str(intent.get("token_id") or ""),
        )
        child_ids = [
            order_id
            for order_id, row in local_orders.items()
            if (
                str(row.get("city") or ""),
                str(row.get("target_date") or ""),
                str(row.get("token_id") or ""),
            )
            == key
        ]
        filled = sum(matched.get(order_id, 0.0) for order_id in child_ids)
        intent_rows.append(
            {
                "strategy_id": intent.get("strategy_id"),
                "city": key[0],
                "target_date": key[1],
                "bracket": intent.get("bracket"),
                "initial_status": intent.get("status"),
                "posted_child_orders": len(child_ids),
                "authenticated_filled_shares": round(filled, 6),
                "authenticated_filled": filled > 0,
            }
        )

    matched_local_orders = {
        order_id: shares for order_id, shares in matched.items() if order_id in local_orders
    }
    matched_taker_orders = {
        order_id: shares
        for order_id, shares in matched.items()
        if order_id in local_taker_orders
    }
    submitted_intents = [row for row in intent_rows if row["initial_status"] == "submitted"]
    filled_intents = [row for row in intent_rows if row["authenticated_filled"]]
    cache_audit: dict[str, Any] = {}
    if args.fill_cache:
        cache_rows = [
            row
            for row in iter_jsonl(args.fill_cache)
            if str(row.get("order_id") or "") in local_orders
        ]
        false_rows = [
            row
            for row in cache_rows
            if str(row.get("order_id") or "") not in matched_local_orders
        ]
        taker_cache_rows = [
            row
            for row in iter_jsonl(args.fill_cache)
            if str(row.get("order_id") or "") in local_taker_orders
        ]
        false_taker_rows = [
            row
            for row in taker_cache_rows
            if str(row.get("order_id") or "") not in matched_taker_orders
        ]
        cache_audit = {
            "fill_cache_path": str(args.fill_cache),
            "cache_maker_fill_rows": len(cache_rows),
            "cache_maker_filled_shares": round(
                sum(float(row.get("filled_shares") or 0.0) for row in cache_rows), 6
            ),
            "cache_rows_without_authenticated_order_match": len(false_rows),
            "cache_shares_without_authenticated_order_match": round(
                sum(float(row.get("filled_shares") or 0.0) for row in false_rows), 6
            ),
            "cache_fill_ids_without_authenticated_order_match": [
                row.get("fill_id") for row in false_rows
            ],
            "cache_taker_fill_rows": len(taker_cache_rows),
            "cache_taker_rows_without_authenticated_order_match": len(false_taker_rows),
            "cache_taker_fill_ids_without_authenticated_order_match": [
                row.get("fill_id") for row in false_taker_rows
            ],
        }
    result = {
        "orders_path": str(args.orders),
        "initial_maker_intents": len(initial_intents),
        "initial_maker_submitted": len(submitted_intents),
        "initial_maker_post_errors": len(initial_intents) - len(submitted_intents),
        "distinct_posted_maker_order_ids": len(local_orders),
        "authenticated_filled_maker_order_ids": len(matched_local_orders),
        "authenticated_filled_maker_intents": len(filled_intents),
        "authenticated_maker_fill_intent_rate": (
            len(filled_intents) / len(submitted_intents) if submitted_intents else None
        ),
        "authenticated_maker_filled_shares": round(sum(matched_local_orders.values()), 6),
        "authenticated_trade_rows_fetched": len(authenticated_trades),
        "submitted_taker_order_ids": len(local_taker_orders),
        "authenticated_filled_taker_order_ids": len(matched_taker_orders),
        "authenticated_filled_order_rows": [
            {
                "order_id": order_id,
                "filled_shares": round(shares, 6),
                "strategy_id": local_orders[order_id].get("strategy_id"),
                "city": local_orders[order_id].get("city"),
                "target_date": local_orders[order_id].get("target_date"),
                "bracket": local_orders[order_id].get("bracket"),
            }
            for order_id, shares in sorted(matched_local_orders.items())
        ],
        "fill_cache_audit": cache_audit,
        "intent_rows": intent_rows,
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
