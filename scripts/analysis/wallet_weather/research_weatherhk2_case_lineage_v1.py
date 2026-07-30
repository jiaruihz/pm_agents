#!/usr/bin/env python3
"""Replay representative WeatherHK2 cases from public fills to settlement.

This is an external-wallet lineage, so private signal/plan and offchain order
post/cancel history remain unavailable. Polygon CLOB V2 OrderFilled receipts
are decoded to distinguish passive maker orders from taker orders.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

import requests


WALLET = "0xdadbf9e1df1b8d7a184a0d6ab9c83b2337b61870"
ORDER_FILLED_V2_TOPIC = (
    "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
)
CASE_SPECS = [
    ("extreme_win", "hong-kong", "2026-07-14"),
    ("path_rotation_win", "shenzhen", "2026-07-09"),
    ("flat", "hong-kong", "2026-07-21"),
    ("loss", "hong-kong", "2026-07-16"),
    ("typical_win", "hong-kong", "2026-07-28"),
]
CITY_SOURCE_NAMES = {
    "hong-kong": "HongKong",
    "shenzhen": "Shenzhen",
    "guangzhou": "Guangzhou",
}
CITY_TIMEZONES = {
    "hong-kong": "Asia/Hong_Kong",
    "shenzhen": "Asia/Shanghai",
    "guangzhou": "Asia/Shanghai",
}
FORECAST_CACHE: dict[tuple[str, str, str, int], dict[str, Any] | None] = {}


def as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_ts(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(
            datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        )
    except (TypeError, ValueError):
        return 0


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
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def label_from_title(title: str) -> str:
    match = re.search(r" be (.+?) on [A-Z][a-z]+ \d+\?", title)
    return match.group(1) if match else title


def iso_utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def iso_local(timestamp: int, city: str) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(
        ZoneInfo(CITY_TIMEZONES[city])
    ).isoformat()


def batch_receipts(transaction_hashes: list[str], rpc_url: str) -> dict[str, Any]:
    receipts: dict[str, Any] = {}
    for start in range(0, len(transaction_hashes), 100):
        chunk = transaction_hashes[start : start + 100]
        payload = [
            {
                "jsonrpc": "2.0",
                "method": "eth_getTransactionReceipt",
                "params": [transaction_hash],
                "id": index,
            }
            for index, transaction_hash in enumerate(chunk)
        ]
        response = requests.post(rpc_url, json=payload, timeout=30)
        response.raise_for_status()
        by_id = {int(item["id"]): item.get("result") for item in response.json()}
        for index, transaction_hash in enumerate(chunk):
            receipts[transaction_hash] = by_id.get(index)
    return receipts


def decode_wallet_orders(
    receipts: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    events_by_tx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    orders: dict[str, dict[str, Any]] = {}
    for transaction_hash, receipt in receipts.items():
        if not receipt:
            continue
        for log in receipt.get("logs") or []:
            topics = [str(value).lower() for value in log.get("topics") or []]
            if not topics or topics[0] != ORDER_FILLED_V2_TOPIC:
                continue
            exchange = str(log.get("address") or "").lower()
            signer = "0x" + topics[2][-40:]
            event_taker = "0x" + topics[3][-40:]
            if signer != WALLET:
                continue
            role = (
                "taker_order"
                if event_taker == exchange
                else "passive_maker_order"
            )
            data = str(log.get("data") or "")[2:]
            words = [
                int(data[offset : offset + 64], 16)
                for offset in range(0, len(data), 64)
            ]
            side_raw, token_id, maker_amount, taker_amount, fee = words[:5]
            side = "BUY" if side_raw == 0 else "SELL"
            if side == "BUY":
                cash_raw, shares_raw = maker_amount, taker_amount
            else:
                cash_raw, shares_raw = taker_amount, maker_amount
            event = {
                "transaction_hash": transaction_hash,
                "order_hash": topics[1],
                "role": role,
                "side": side,
                "token_id": str(token_id),
                "cash": cash_raw / 1_000_000,
                "shares": shares_raw / 1_000_000,
                "fee": fee / 1_000_000,
                "event_taker": event_taker,
                "exchange": exchange,
            }
            events_by_tx[transaction_hash].append(event)
            order = orders.setdefault(
                topics[1],
                {
                    "order_hash": topics[1],
                    "role": role,
                    "side": side,
                    "token_id": str(token_id),
                    "fill_events": 0,
                    "cash": 0.0,
                    "shares": 0.0,
                    "fee": 0.0,
                    "transactions": set(),
                },
            )
            order["fill_events"] += 1
            order["cash"] += event["cash"]
            order["shares"] += event["shares"]
            order["fee"] += event["fee"]
            order["transactions"].add(transaction_hash)
    for order in orders.values():
        order["transactions"] = sorted(order["transactions"])
        order["transaction_count"] = len(order["transactions"])
        order["average_price"] = (
            order["cash"] / order["shares"] if order["shares"] else None
        )
    return events_by_tx, orders


def latest_forecast(
    root: Path | None, city: str, target_date: str, decision_ts: int
) -> dict[str, Any] | None:
    if root is None or not root.exists() or decision_ts <= 0:
        return None
    local_day = datetime.fromtimestamp(decision_ts, timezone.utc).astimezone(
        ZoneInfo("Asia/Shanghai")
    ).date()
    cache_key = (str(root), city, target_date, decision_ts)
    if cache_key in FORECAST_CACHE:
        return FORECAST_CACHE[cache_key]
    path_candidates: list[tuple[int, Path]] = []
    for day in [local_day - timedelta(days=1), local_day]:
        directory = root / day.isoformat()
        if not directory.exists():
            continue
        for path in directory.glob("forecast_hourly_curves_*.jsonl"):
            match = re.search(r"_(\d{8})_(\d{6})_", path.name)
            if not match:
                continue
            file_local = datetime.strptime(
                "".join(match.groups()), "%Y%m%d%H%M%S"
            ).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            file_ts = int(file_local.timestamp())
            if file_ts <= decision_ts:
                path_candidates.append((file_ts, path))
    city_probe = f'"city": "{CITY_SOURCE_NAMES.get(city, city)}"'
    target_probe = f'"target_date": "{target_date}"'
    selected: dict[str, Any] | None = None
    for _, path in sorted(path_candidates, reverse=True):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if city_probe not in line or target_probe not in line:
                    continue
                row = json.loads(line)
                if parse_ts(row.get("available_at_utc")) <= decision_ts:
                    selected = {**row, "_path": str(path)}
                    break
        if selected:
            break
    if selected is None:
        FORECAST_CACHE[cache_key] = None
        return None
    row = selected
    max_f = as_float(row.get("forecast_max_f"))
    output = {
        "path": row["_path"],
        "capture_id": row.get("capture_id"),
        "available_at_utc": row.get("available_at_utc"),
        "first_seen_utc": row.get("forecast_first_seen_utc"),
        "source": row.get("forecast_source"),
        "assigned_model": row.get("forecast_assigned_model"),
        "fallback": row.get("forecast_model_fallback"),
        "forecast_max_f": max_f,
        "forecast_max_c": (max_f - 32) * 5 / 9,
        "forecast_peak_time_local": row.get("forecast_peak_time_local"),
        "values_hash": row.get("forecast_values_hash"),
    }
    FORECAST_CACHE[cache_key] = output
    return output


def source_path_summary(
    root: Path | None, city: str, target_date: str
) -> dict[str, Any] | None:
    if root is None:
        return None
    path = root / target_date / "sources.jsonl"
    if not path.exists():
        return None
    expected_city = CITY_SOURCE_NAMES[city].lower()
    selected: dict[tuple[Any, ...], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if str(row.get("city") or "").lower() != expected_city:
                continue
            if str(row.get("target_date") or "") != target_date:
                continue
            if row.get("status") != "ok" or row.get("temp_c") is None:
                continue
            key = (
                row.get("source"),
                row.get("source_report_ts_utc"),
                as_float(row.get("temp_c")),
            )
            detected = parse_ts(row.get("local_detect_ts_utc"))
            existing = selected.get(key)
            if not existing or detected < parse_ts(existing.get("local_detect_ts_utc")):
                selected[key] = row
    rows = sorted(
        selected.values(), key=lambda row: parse_ts(row.get("local_detect_ts_utc"))
    )
    if not rows:
        return None
    changes: list[dict[str, Any]] = []
    running_max: float | None = None
    for row in rows:
        temp = as_float(row["temp_c"])
        if running_max is None or temp > running_max:
            running_max = temp
            changes.append(
                {
                    "detected_at_utc": row.get("local_detect_ts_utc"),
                    "report_ts_utc": row.get("source_report_ts_utc"),
                    "source": row.get("source"),
                    "station": row.get("station"),
                    "temp_c": temp,
                    "running_max_c": running_max,
                    "raw_metar": row.get("raw_metar"),
                }
            )
    return {
        "path": str(path),
        "sources": sorted({str(row.get("source")) for row in rows}),
        "first_seen_rows": len(rows),
        "observed_min_c": min(as_float(row["temp_c"]) for row in rows),
        "observed_max_c": max(as_float(row["temp_c"]) for row in rows),
        "running_max_changes": changes,
        "settlement_source": rows[0].get("settlement_source"),
        "mapping_rule": rows[0].get("mapping_rule"),
        "basis_warning": (
            "Live source observations are probability features, not settlement truth."
        ),
    }


def hko_official_paths(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if path is None or not path.exists():
        return by_date
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("city") != "HongKong" or row.get("source") != "hko_obs":
                continue
            event_key = str(row.get("event_key") or "")
            if event_key in seen:
                continue
            seen.add(event_key)
            by_date[str(row.get("target_date"))].append(
                {
                    "event_key": event_key,
                    "source_obs_ts_utc": row.get("source_obs_ts_utc"),
                    "source_detect_ts_utc": row.get("source_detect_ts_utc"),
                    "source_temp_c": row.get("source_temp_c"),
                    "source_floor_bracket_c": row.get("source_floor_bracket_c"),
                    "t_minus_1_no_bracket_c": row.get("t_minus_1_no_bracket_c"),
                    "status": row.get("status"),
                    "best_ask": row.get("best_ask"),
                    "ask_size": row.get("ask_size"),
                    "book_liquidity_state": row.get("book_liquidity_state"),
                }
            )
    for rows in by_date.values():
        rows.sort(key=lambda row: parse_ts(row.get("source_obs_ts_utc")))
    return by_date


def phase_rows(
    rows: list[dict[str, Any]],
    city: str,
    events_by_tx: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    trades = sorted(
        (row for row in rows if str(row.get("type") or "").upper() == "TRADE"),
        key=lambda row: as_int(row.get("timestamp")),
    )
    phases: list[dict[str, Any]] = []
    for row in trades:
        timestamp = as_int(row.get("timestamp"))
        if not phases or timestamp - phases[-1]["last_ts"] > 600:
            phases.append(
                {
                    "first_ts": timestamp,
                    "last_ts": timestamp,
                    "trade_rows": 0,
                    "transactions": set(),
                    "buy_cost": 0.0,
                    "sell_proceeds": 0.0,
                    "actions": defaultdict(
                        lambda: {"shares": 0.0, "cash": 0.0, "rows": 0}
                    ),
                    "roles": Counter(),
                }
            )
        phase = phases[-1]
        phase["last_ts"] = timestamp
        phase["trade_rows"] += 1
        transaction = str(row.get("transactionHash") or "").lower()
        phase["transactions"].add(transaction)
        side = str(row.get("side") or "").upper()
        cash = as_float(row.get("usdcSize"))
        shares = as_float(row.get("size"))
        if side == "BUY":
            phase["buy_cost"] += cash
        else:
            phase["sell_proceeds"] += cash
        action = (
            f"{side} {str(row.get('outcome') or '').upper()} "
            f"{label_from_title(str(row.get('title') or row.get('slug') or ''))}"
        )
        phase["actions"][action]["shares"] += shares
        phase["actions"][action]["cash"] += cash
        phase["actions"][action]["rows"] += 1
        for event in events_by_tx.get(transaction, []):
            phase["roles"][event["role"]] += 1

    output: list[dict[str, Any]] = []
    for phase in phases:
        ranked = sorted(
            phase["actions"].items(),
            key=lambda item: item[1]["cash"],
            reverse=True,
        )[:5]
        output.append(
            {
                "first_ts": phase["first_ts"],
                "last_ts": phase["last_ts"],
                "first_utc": iso_utc(phase["first_ts"]),
                "last_utc": iso_utc(phase["last_ts"]),
                "first_local": iso_local(phase["first_ts"], city),
                "last_local": iso_local(phase["last_ts"], city),
                "trade_rows": phase["trade_rows"],
                "unique_transactions": len(phase["transactions"]),
                "buy_cost": phase["buy_cost"],
                "sell_proceeds": phase["sell_proceeds"],
                "onchain_order_role_fill_events": dict(phase["roles"]),
                "top_actions_by_cash": [
                    {"action": action, **values} for action, values in ranked
                ],
            }
        )
    return output


def condition_flows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flows: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if str(row.get("type") or "").upper() != "TRADE":
            continue
        condition = str(row.get("conditionId") or "")
        outcome = str(row.get("outcome") or "").upper()
        key = (condition, outcome)
        item = flows.setdefault(
            key,
            {
                "condition_id": condition,
                "label": label_from_title(
                    str(row.get("title") or row.get("slug") or "")
                ),
                "outcome": outcome,
                "buy_shares": 0.0,
                "buy_cost": 0.0,
                "sell_shares": 0.0,
                "sell_proceeds": 0.0,
            },
        )
        side = str(row.get("side") or "").upper()
        if side == "BUY":
            item["buy_shares"] += as_float(row.get("size"))
            item["buy_cost"] += as_float(row.get("usdcSize"))
        else:
            item["sell_shares"] += as_float(row.get("size"))
            item["sell_proceeds"] += as_float(row.get("usdcSize"))
    output = []
    for item in flows.values():
        item["net_trade_shares"] = item["buy_shares"] - item["sell_shares"]
        item["trade_cashflow"] = item["sell_proceeds"] - item["buy_cost"]
        output.append(item)
    return sorted(
        output,
        key=lambda item: item["buy_cost"] + item["sell_proceeds"],
        reverse=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--forecast-root", type=Path)
    parser.add_argument("--source-events-root", type=Path)
    parser.add_argument("--hko-events", type=Path)
    args = parser.parse_args()

    portfolios = read_csv(
        args.snapshot / "analysis" / "full_ladder_history_v1" / "event_portfolios.csv"
    )
    activities = read_jsonl_gz(args.snapshot / "weather_activity.jsonl.gz")
    portfolio_by_key = {
        (row["city"], row["target_date"]): row for row in portfolios
    }
    slug_to_key: dict[str, tuple[str, str]] = {}
    for row in portfolios:
        for slug in json.loads(row["event_slugs"]):
            slug_to_key[str(slug)] = (row["city"], row["target_date"])

    selected_keys = {(city, target_date) for _, city, target_date in CASE_SPECS}
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
    receipts = batch_receipts(transaction_hashes, args.rpc_url)
    events_by_tx, all_orders = decode_wallet_orders(receipts)
    hko_by_date = hko_official_paths(args.hko_events)

    cases: list[dict[str, Any]] = []
    for case_class, city, target_date in CASE_SPECS:
        key = (city, target_date)
        portfolio = portfolio_by_key[key]
        rows = rows_by_key[key]
        trades = [
            row for row in rows if str(row.get("type") or "").upper() == "TRADE"
        ]
        buys = [
            row for row in trades if str(row.get("side") or "").upper() == "BUY"
        ]
        buy_by_tx: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"cash": 0.0, "timestamp": 0}
        )
        for row in buys:
            transaction = str(row.get("transactionHash") or "").lower()
            buy_by_tx[transaction]["cash"] += as_float(row.get("usdcSize"))
            buy_by_tx[transaction]["timestamp"] = as_int(row.get("timestamp"))
        largest_buy = max(
            buy_by_tx.items(), key=lambda item: item[1]["cash"], default=("", {})
        )
        decision_points = {
            "first_buy": min((as_int(row.get("timestamp")) for row in buys), default=0),
            "largest_buy_transaction": as_int(largest_buy[1].get("timestamp")),
            "first_sell": min(
                (
                    as_int(row.get("timestamp"))
                    for row in trades
                    if str(row.get("side") or "").upper() == "SELL"
                ),
                default=0,
            ),
        }
        case_transactions = {
            str(row.get("transactionHash") or "").lower()
            for row in trades
            if row.get("transactionHash")
        }
        case_orders = [
            order
            for order in all_orders.values()
            if set(order["transactions"]) & case_transactions
        ]
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
            role = role_summary[order["role"]]
            role["orders"] += 1
            role["fill_events"] += order["fill_events"]
            role["cash"] += order["cash"]
            role["shares"] += order["shares"]
            role["fee"] += order["fee"]

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
                    "turnover_roi": as_float(portfolio["turnover_roi_reconstructed"]),
                    "token_payout_reconstructed": as_float(
                        portfolio["token_payout_reconstructed"]
                    ),
                },
                "activity": {
                    "rows": len(rows),
                    "trade_rows": len(trades),
                    "transactions": len(case_transactions),
                    "first_trade_utc": iso_utc(
                        min(as_int(row.get("timestamp")) for row in trades)
                    ),
                    "last_trade_utc": iso_utc(
                        max(as_int(row.get("timestamp")) for row in trades)
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
                        "utc": iso_utc(timestamp) if timestamp else None,
                        "local": iso_local(timestamp, city) if timestamp else None,
                        "latest_pit_forecast": latest_forecast(
                            args.forecast_root,
                            city,
                            target_date,
                            timestamp,
                        ),
                    }
                    for name, timestamp in decision_points.items()
                },
                "source_path": source_path_summary(
                    args.source_events_root, city, target_date
                ),
                "settlement_facing_hko_path": hko_by_date.get(target_date),
                "onchain_order_roles": dict(role_summary),
                "receipt_coverage": {
                    "transactions": len(case_transactions),
                    "receipts_found": sum(
                        receipts.get(transaction) is not None
                        for transaction in case_transactions
                    ),
                },
                "phases_gap_gt_10m": phase_rows(rows, city, events_by_tx),
                "condition_flows": condition_flows(rows),
                "orders": sorted(
                    case_orders,
                    key=lambda order: (order["role"], order["side"], -order["cash"]),
                ),
                "evidence_boundary": {
                    "signal": "unobserved_private_state",
                    "plan": "inferred_only_from_public_fills",
                    "order": "role/order_hash/fills_onchain; post/cancel/queue_unobserved",
                    "settlement_pnl": "cashflow_complete_public_activity",
                },
            }
        )

    output = {
        "schema_version": "weatherhk2_case_lineage_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "wallet": WALLET,
        "snapshot": str(args.snapshot),
        "case_count": len(cases),
        "selected_cases": [
            {"case_class": case_class, "city": city, "target_date": target_date}
            for case_class, city, target_date in CASE_SPECS
        ],
        "onchain_receipt_coverage": {
            "transactions": len(transaction_hashes),
            "receipts_found": sum(receipt is not None for receipt in receipts.values()),
        },
        "cases": cases,
    }
    write_json(args.output, output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "cases": len(cases),
                "transactions": len(transaction_hashes),
                "receipts_found": output["onchain_receipt_coverage"][
                    "receipts_found"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
