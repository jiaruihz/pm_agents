#!/usr/bin/env python3
"""Replay four representative complete-ladder cases for an external wallet.

The case selector uses only cashflow-complete resolved city x target-date
portfolios: largest win, largest loss, a non-trivial near-flat case, and a
typical positive-ROI case. Public fills and Polygon CLOB V2 receipts establish
order/fill/settlement evidence; private signal, plan, post time, cancellations,
and queue rank remain unobserved.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import statistics
import time
from typing import Any

import requests

from research_external_wallet_strategy_v1 import CITY_TIMEZONES
import research_weatherhk2_case_lineage_v1 as lineage


def as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def truthy(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def canonical_city_name(city: str) -> str:
    special = {
        "la": "LosAngeles",
        "nyc": "NewYork",
        "hong-kong": "HongKong",
        "kuala-lumpur": "KualaLumpur",
        "san-francisco": "SanFrancisco",
        "sao-paulo": "SaoPaulo",
        "tel-aviv": "TelAviv",
    }
    return special.get(city, "".join(part.capitalize() for part in city.split("-")))


def batch_receipts_with_fallback(
    transaction_hashes: list[str],
    rpc_urls: list[str],
) -> dict[str, Any]:
    receipts: dict[str, Any] = {transaction: None for transaction in transaction_hashes}
    for rpc_index, rpc_url in enumerate(rpc_urls):
        missing = [
            transaction
            for transaction, receipt in receipts.items()
            if receipt is None
        ]
        if not missing:
            break
        chunk_size = 100 if rpc_index == 0 else 20
        for start in range(0, len(missing), chunk_size):
            chunk = missing[start : start + chunk_size]
            payload = [
                {
                    "jsonrpc": "2.0",
                    "method": "eth_getTransactionReceipt",
                    "params": [transaction],
                    "id": index,
                }
                for index, transaction in enumerate(chunk)
            ]
            recovered: dict[int, Any] = {}
            for attempt in range(3):
                try:
                    response = requests.post(rpc_url, json=payload, timeout=30)
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, list):
                        raise RuntimeError(
                            f"expected batch response list, got {type(body).__name__}"
                        )
                    recovered = {
                        int(item["id"]): item.get("result")
                        for item in body
                        if isinstance(item, dict) and item.get("id") is not None
                    }
                    break
                except (requests.RequestException, ValueError, RuntimeError):
                    if attempt < 2:
                        time.sleep(0.5 * (attempt + 1))
            for index, transaction in enumerate(chunk):
                receipt = recovered.get(index)
                if receipt is not None:
                    receipts[transaction] = receipt
    return receipts


def select_cases(
    portfolios: list[dict[str, str]],
    *,
    minimum_buy_cost: float,
) -> list[tuple[str, str, str]]:
    eligible = [
        row
        for row in portfolios
        if truthy(row.get("cashflow_complete"))
        and truthy(row.get("resolved"))
        and as_float(row.get("buy_cost")) >= minimum_buy_cost
    ]
    if not eligible:
        raise RuntimeError("no cashflow-complete resolved portfolios meet minimum cost")

    for row in eligible:
        row["_pnl"] = as_float(row.get("public_cashflow"))  # type: ignore[assignment]
        row["_roi"] = as_float(  # type: ignore[assignment]
            row.get("turnover_roi_reconstructed")
        )
        row["_cost"] = as_float(row.get("buy_cost"))  # type: ignore[assignment]

    positives = [row for row in eligible if as_float(row["_pnl"]) > 0]
    negatives = [row for row in eligible if as_float(row["_pnl"]) < 0]
    if not positives or not negatives:
        raise RuntimeError("representative selection requires positive and negative cases")

    selected: list[tuple[str, dict[str, Any]]] = [
        ("extreme_win", max(positives, key=lambda row: as_float(row["_pnl"]))),
        ("loss", min(negatives, key=lambda row: as_float(row["_pnl"]))),
    ]
    used = {(row["city"], row["target_date"]) for _, row in selected}

    flat_pool = [
        row
        for row in eligible
        if (row["city"], row["target_date"]) not in used
        and abs(as_float(row["_roi"])) <= 0.05
    ]
    if not flat_pool:
        flat_pool = [
            row
            for row in eligible
            if (row["city"], row["target_date"]) not in used
        ]
    flat = min(
        flat_pool,
        key=lambda row: (abs(as_float(row["_pnl"])), abs(as_float(row["_roi"]))),
    )
    selected.append(("flat", flat))
    used.add((flat["city"], flat["target_date"]))

    positive_roi_median = statistics.median(
        as_float(row["_roi"]) for row in positives
    )
    cost_median = statistics.median(as_float(row["_cost"]) for row in eligible)
    typical_pool = [
        row
        for row in positives
        if (row["city"], row["target_date"]) not in used
        and as_float(row["_cost"]) >= cost_median
    ]
    if not typical_pool:
        typical_pool = [
            row
            for row in positives
            if (row["city"], row["target_date"]) not in used
        ]
    typical = min(
        typical_pool,
        key=lambda row: (
            abs(as_float(row["_roi"]) - positive_roi_median),
            -as_float(row["_cost"]),
        ),
    )
    selected.append(("typical_win", typical))
    return [
        (case_class, str(row["city"]), str(row["target_date"]))
        for case_class, row in selected
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rpc-url", action="append", required=True)
    parser.add_argument("--forecast-root", type=Path)
    parser.add_argument("--source-events-root", type=Path)
    parser.add_argument("--minimum-buy-cost", type=float, default=50.0)
    args = parser.parse_args()

    wallet = args.wallet.lower()
    if not wallet.startswith("0x") or len(wallet) != 42:
        raise ValueError(f"invalid wallet: {wallet}")
    snapshot = args.snapshot.resolve()
    analysis = snapshot / "analysis" / "full_ladder_history_v1"
    portfolios = read_csv(analysis / "event_portfolios.csv")
    activities = read_jsonl_gz(snapshot / "weather_activity.jsonl.gz")
    history_summary = json.loads((analysis / "summary.json").read_text())
    case_specs = select_cases(
        portfolios,
        minimum_buy_cost=args.minimum_buy_cost,
    )

    lineage.WALLET = wallet
    lineage.CITY_TIMEZONES.update(CITY_TIMEZONES)
    for _, city, _ in case_specs:
        lineage.CITY_SOURCE_NAMES.setdefault(city, canonical_city_name(city))

    portfolio_by_key = {
        (row["city"], row["target_date"]): row for row in portfolios
    }
    slug_to_key: dict[str, tuple[str, str]] = {}
    for row in portfolios:
        for slug in json.loads(row["event_slugs"]):
            slug_to_key[str(slug)] = (row["city"], row["target_date"])

    selected_keys = {(city, target_date) for _, city, target_date in case_specs}
    rows_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in activities:
        key = slug_to_key.get(str(row.get("eventSlug") or ""))
        if key in selected_keys:
            rows_by_key[key].append(row)

    transaction_hashes = sorted(
        {
            str(row.get("transactionHash") or "").lower()
            for rows in rows_by_key.values()
            for row in rows
            if str(row.get("type") or "").upper() == "TRADE"
            and row.get("transactionHash")
        }
    )
    receipts = batch_receipts_with_fallback(transaction_hashes, args.rpc_url)
    events_by_tx, all_orders = lineage.decode_wallet_orders(receipts)

    cases: list[dict[str, Any]] = []
    for case_class, city, target_date in case_specs:
        key = (city, target_date)
        portfolio = portfolio_by_key[key]
        rows = rows_by_key[key]
        trades = [
            row for row in rows if str(row.get("type") or "").upper() == "TRADE"
        ]
        buys = [
            row for row in trades if str(row.get("side") or "").upper() == "BUY"
        ]
        case_assets = {
            str(row.get("asset") or "")
            for row in trades
            if row.get("asset")
        }
        case_transactions = {
            str(row.get("transactionHash") or "").lower()
            for row in trades
            if row.get("transactionHash")
        }
        case_events_by_tx = {
            transaction: [
                event
                for event in events
                if str(event.get("token_id") or "") in case_assets
            ]
            for transaction, events in events_by_tx.items()
            if transaction in case_transactions
        }
        case_orders = [
            order
            for order in all_orders.values()
            if str(order.get("token_id") or "") in case_assets
            and set(order["transactions"]) & case_transactions
        ]

        buy_by_tx: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"cash": 0.0, "timestamp": 0}
        )
        for row in buys:
            transaction = str(row.get("transactionHash") or "").lower()
            buy_by_tx[transaction]["cash"] += as_float(row.get("usdcSize"))
            buy_by_tx[transaction]["timestamp"] = lineage.as_int(row.get("timestamp"))
        largest_buy = max(
            buy_by_tx.items(), key=lambda item: item[1]["cash"], default=("", {})
        )
        decision_points = {
            "first_buy": min(
                (lineage.as_int(row.get("timestamp")) for row in buys),
                default=0,
            ),
            "largest_buy_transaction": lineage.as_int(
                largest_buy[1].get("timestamp")
            ),
            "first_sell": min(
                (
                    lineage.as_int(row.get("timestamp"))
                    for row in trades
                    if str(row.get("side") or "").upper() == "SELL"
                ),
                default=0,
            ),
        }

        role_summary: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {
                "orders": 0,
                "fill_events": 0,
                "cash": 0.0,
                "shares": 0.0,
                "fee": 0.0,
            }
        )
        for order in case_orders:
            role = role_summary[str(order["role"])]
            role["orders"] += 1
            role["fill_events"] += int(order["fill_events"])
            role["cash"] += as_float(order["cash"])
            role["shares"] += as_float(order["shares"])
            role["fee"] += as_float(order["fee"])

        cases.append(
            {
                "case_class": case_class,
                "city": city,
                "target_date": target_date,
                "event_slugs": json.loads(portfolio["event_slugs"]),
                "winner_condition": portfolio["winner_condition"],
                "winner_label": portfolio["winner_label"],
                "expression": portfolio["expression"],
                "exit_style": portfolio["exit_style"],
                "performance": {
                    "buy_cost": as_float(portfolio["buy_cost"]),
                    "sell_proceeds": as_float(portfolio["sell_proceeds"]),
                    "merge_cash": as_float(portfolio["merge_cash"]),
                    "redeem_cash": as_float(portfolio["redeem_cash"]),
                    "split_cash": as_float(portfolio["split_cash"]),
                    "public_cashflow_pnl": as_float(portfolio["public_cashflow"]),
                    "turnover_roi": as_float(
                        portfolio["turnover_roi_reconstructed"]
                    ),
                    "token_payout_reconstructed": as_float(
                        portfolio["token_payout_reconstructed"]
                    ),
                },
                "activity": {
                    "rows": len(rows),
                    "trade_rows": len(trades),
                    "transactions": len(case_transactions),
                    "first_trade_utc": lineage.iso_utc(
                        min(lineage.as_int(row.get("timestamp")) for row in trades)
                    ),
                    "last_trade_utc": lineage.iso_utc(
                        max(lineage.as_int(row.get("timestamp")) for row in trades)
                    ),
                    "conversion_types": dict(
                        Counter(
                            str(row.get("type") or "").upper()
                            for row in rows
                            if str(row.get("type") or "").upper() != "TRADE"
                        )
                    ),
                },
                "decision_points": {
                    name: {
                        "timestamp": timestamp,
                        "utc": lineage.iso_utc(timestamp) if timestamp else None,
                        "local": (
                            lineage.iso_local(timestamp, city) if timestamp else None
                        ),
                        "latest_pit_forecast": lineage.latest_forecast(
                            args.forecast_root,
                            city,
                            target_date,
                            timestamp,
                        ),
                    }
                    for name, timestamp in decision_points.items()
                },
                "source_path": lineage.source_path_summary(
                    args.source_events_root,
                    city,
                    target_date,
                ),
                "onchain_order_roles": dict(role_summary),
                "receipt_coverage": {
                    "transactions": len(case_transactions),
                    "receipts_found": sum(
                        receipts.get(transaction) is not None
                        for transaction in case_transactions
                    ),
                },
                "phases_gap_gt_10m": lineage.phase_rows(
                    rows,
                    city,
                    case_events_by_tx,
                ),
                "condition_flows": lineage.condition_flows(rows),
                "orders": sorted(
                    case_orders,
                    key=lambda order: (
                        str(order["role"]),
                        str(order["side"]),
                        -as_float(order["cash"]),
                    ),
                ),
                "evidence_boundary": {
                    "signal": "unobserved_private_state",
                    "plan": "inferred_only_from_public_fills",
                    "order": (
                        "role/order_hash/fills_onchain; "
                        "post/cancel/queue_unobserved"
                    ),
                    "settlement_pnl": "cashflow_complete_public_activity",
                },
            }
        )

    output = {
        "schema_version": "external_wallet_representative_lineages_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "label": args.label,
        "wallet": wallet,
        "snapshot": str(snapshot),
        "history_summary": {
            "coverage": history_summary["coverage"],
            "portfolio_summary": history_summary["portfolio_summary"],
            "settlement_behavior": history_summary["settlement_behavior"],
            "entry_fill_timing": history_summary["entry_fill_timing"],
            "sell_behavior": history_summary["sell_behavior"],
            "expression_counts": history_summary["expression_counts"],
            "exit_style_counts": history_summary["exit_style_counts"],
        },
        "case_selection": {
            "grain": "cashflow-complete resolved city x target_date ladder",
            "minimum_buy_cost": args.minimum_buy_cost,
            "rules": {
                "extreme_win": "largest positive public cashflow PnL",
                "loss": "largest negative public cashflow PnL",
                "flat": "smallest absolute PnL among abs(ROI)<=5%",
                "typical_win": (
                    "positive case nearest wallet median positive ROI, "
                    "restricted to above-median buy cost"
                ),
            },
            "selected_cases": [
                {
                    "case_class": case_class,
                    "city": city,
                    "target_date": target_date,
                }
                for case_class, city, target_date in case_specs
            ],
        },
        "onchain_receipt_coverage": {
            "rpc_urls": args.rpc_url,
            "transactions": len(transaction_hashes),
            "receipts_found": sum(
                receipt is not None for receipt in receipts.values()
            ),
        },
        "cases": cases,
    }
    write_json(args.output, output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "label": args.label,
                "cases": len(cases),
                "transactions": len(transaction_hashes),
                "receipts_found": output["onchain_receipt_coverage"][
                    "receipts_found"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
