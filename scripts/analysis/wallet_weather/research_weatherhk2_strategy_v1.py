#!/usr/bin/env python3
"""Describe WeatherHK2's public weather-trading lifecycle.

The analysis is intentionally limited to public fill/activity data.  It does
not infer private signals, unfilled maker orders, queue position, or a
contemporaneous executable order book.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import random
import statistics
from typing import Any, Iterable


WALLET = "0xdadbf9e1df1b8d7a184a0d6ab9c83b2337b61870"
ORDER_FILLED_V2_TOPIC = (
    "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
)


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


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def percentile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def distribution(values: Iterable[float]) -> dict[str, float | int | None]:
    rows = list(values)
    return {
        "n": len(rows),
        "p10": percentile(rows, 0.10),
        "median": percentile(rows, 0.50),
        "p90": percentile(rows, 0.90),
        "mean": statistics.fmean(rows) if rows else None,
    }


def aggregate(
    portfolios: list[dict[str, str]], field: str, value_map: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in portfolios:
        key = row.get(field) or "unknown"
        if value_map:
            key = value_map.get(key, key)
        grouped[key].append(row)
    output: list[dict[str, Any]] = []
    for key, rows in grouped.items():
        cost = sum(as_float(row["buy_cost"]) for row in rows)
        pnl = sum(as_float(row["public_cashflow"]) for row in rows)
        output.append(
            {
                "slice": field,
                "value": key,
                "events": len(rows),
                "target_dates": len({row["target_date"] for row in rows}),
                "buy_cost": round(cost, 6),
                "pnl": round(pnl, 6),
                "roi": ratio(pnl, cost),
                "positive_event_share": ratio(
                    sum(as_float(row["public_cashflow"]) > 0 for row in rows), len(rows)
                ),
            }
        )
    return sorted(output, key=lambda row: row["buy_cost"], reverse=True)


def block_bootstrap_roi(
    portfolios: list[dict[str, str]], iterations: int = 10_000, seed: int = 20260730
) -> dict[str, Any]:
    by_date: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in portfolios:
        by_date[row["target_date"]].append(row)
    dates = sorted(by_date)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        chosen = [rng.choice(dates) for _ in dates]
        cost = sum(
            as_float(row["buy_cost"]) for target in chosen for row in by_date[target]
        )
        pnl = sum(
            as_float(row["public_cashflow"])
            for target in chosen
            for row in by_date[target]
        )
        if cost:
            samples.append(pnl / cost)
    return {
        "block": "target_date",
        "iterations": iterations,
        "dates": len(dates),
        "roi_p025": percentile(samples, 0.025),
        "roi_median": percentile(samples, 0.50),
        "roi_p975": percentile(samples, 0.975),
    }


def phase_label(offset: int) -> str:
    if offset <= -2:
        return "D-2_or_earlier"
    if offset == -1:
        return "D-1"
    if offset == 0:
        return "D0"
    return "post_target"


def build_lifecycle(
    activities: list[dict[str, Any]],
    slug_to_key: dict[str, tuple[str, str]],
    complete_keys: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[tuple[str, str], list[dict[str, Any]]]]:
    by_condition: dict[tuple[str, str, str], dict[str, Any]] = {}
    event_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    unique_trades: dict[tuple[int, str], dict[str, Any]] = {}
    conversion = defaultdict(float)

    for row in activities:
        event_slug = str(row.get("eventSlug") or "")
        key = slug_to_key.get(event_slug)
        if not key or key not in complete_keys:
            continue
        event_rows[key].append(row)
        row_type = str(row.get("type") or "").upper()
        if row_type in {"MERGE", "SPLIT", "REDEEM"}:
            conversion[f"{row_type.lower()}_cash"] += as_float(row.get("usdcSize"))
            conversion[f"{row_type.lower()}_rows"] += 1
        if row_type != "TRADE":
            continue
        timestamp = as_int(row.get("timestamp"))
        transaction = str(row.get("transactionHash") or "")
        unique_trades[(timestamp, transaction)] = row
        condition = str(row.get("conditionId") or "")
        ckey = (key[0], key[1], condition)
        item = by_condition.setdefault(
            ckey,
            {
                "city": key[0],
                "target_date": key[1],
                "condition_id": condition,
                "label": str(row.get("title") or row.get("slug") or ""),
                "yes_buy_shares": 0.0,
                "yes_buy_cost": 0.0,
                "no_buy_shares": 0.0,
                "no_buy_cost": 0.0,
                "yes_sell_shares": 0.0,
                "yes_sell_proceeds": 0.0,
                "no_sell_shares": 0.0,
                "no_sell_proceeds": 0.0,
            },
        )
        outcome = "yes" if str(row.get("outcome") or "").lower() == "yes" else "no"
        side = str(row.get("side") or "").lower()
        shares = as_float(row.get("size"))
        cash = as_float(row.get("usdcSize"))
        item[f"{outcome}_{side}_shares"] += shares
        item[f"{outcome}_{side}_{'cost' if side == 'buy' else 'proceeds'}"] += cash

    lifecycle_rows: list[dict[str, Any]] = []
    dual_events: set[tuple[str, str]] = set()
    roundtrip_events: set[tuple[str, str]] = set()
    diagnostic_pair_shares = 0.0
    diagnostic_pair_cost = 0.0
    for item in by_condition.values():
        yes_vwap = ratio(item["yes_buy_cost"], item["yes_buy_shares"])
        no_vwap = ratio(item["no_buy_cost"], item["no_buy_shares"])
        dual = item["yes_buy_shares"] > 0 and item["no_buy_shares"] > 0
        paired = (
            min(item["yes_buy_shares"], item["no_buy_shares"]) if dual else 0.0
        )
        pair_sum = yes_vwap + no_vwap if yes_vwap is not None and no_vwap is not None else None
        yes_roundtrip = item["yes_buy_shares"] > 0 and item["yes_sell_shares"] > 0
        no_roundtrip = item["no_buy_shares"] > 0 and item["no_sell_shares"] > 0
        event_key = (item["city"], item["target_date"])
        if dual:
            dual_events.add(event_key)
            diagnostic_pair_shares += paired
            if pair_sum is not None:
                diagnostic_pair_cost += paired * pair_sum
        if yes_roundtrip or no_roundtrip:
            roundtrip_events.add(event_key)
        lifecycle_rows.append(
            {
                **item,
                "yes_buy_vwap": yes_vwap,
                "no_buy_vwap": no_vwap,
                "dual_outcome_bought": dual,
                "diagnostic_matched_shares": paired,
                "diagnostic_vwap_sum": pair_sum,
                "diagnostic_underround": pair_sum is not None and pair_sum < 1,
                "yes_roundtrip": yes_roundtrip,
                "no_roundtrip": no_roundtrip,
            }
        )

    ordered_trades = sorted(
        (
            (timestamp, transaction)
            for timestamp, transaction in unique_trades
            if timestamp > 0
        )
    )
    gaps = [
        current[0] - previous[0]
        for previous, current in zip(ordered_trades, ordered_trades[1:])
    ]
    minute_counts: dict[int, int] = defaultdict(int)
    for timestamp, _ in ordered_trades:
        minute_counts[timestamp // 60] += 1
    execution = {
        "unique_trade_transactions": len(ordered_trades),
        "transaction_gap_seconds": distribution(gaps),
        "share_gaps_le_2s": ratio(sum(gap <= 2 for gap in gaps), len(gaps)),
        "share_gaps_le_10s": ratio(sum(gap <= 10 for gap in gaps), len(gaps)),
        "share_gaps_le_60s": ratio(sum(gap <= 60 for gap in gaps), len(gaps)),
        "peak_transactions_per_minute": max(minute_counts.values(), default=0),
        "dual_outcome_buy_events": len(dual_events),
        "roundtrip_events": len(roundtrip_events),
        "conditions": len(lifecycle_rows),
        "dual_outcome_conditions": sum(
            bool(row["dual_outcome_bought"]) for row in lifecycle_rows
        ),
        "underround_conditions_diagnostic": sum(
            bool(row["diagnostic_underround"]) for row in lifecycle_rows
        ),
        "diagnostic_matched_pair_shares": diagnostic_pair_shares,
        "diagnostic_matched_pair_vwap_cost": diagnostic_pair_cost,
        **conversion,
    }
    return lifecycle_rows, execution, event_rows


def build_cases(
    portfolios: list[dict[str, str]],
    event_rows: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    ranked = sorted(portfolios, key=lambda row: as_float(row["public_cashflow"]))
    selected: list[dict[str, str]] = []
    for row in [ranked[0], ranked[-1]]:
        selected.append(row)
    for city, target in [
        ("hong-kong", "2026-07-16"),
        ("shenzhen", "2026-07-09"),
        ("guangzhou", "2026-06-27"),
    ]:
        match = next(
            (
                row
                for row in portfolios
                if row["city"] == city and row["target_date"] == target
            ),
            None,
        )
        if match and match not in selected:
            selected.append(match)

    output: list[dict[str, Any]] = []
    for portfolio in selected:
        key = (portfolio["city"], portfolio["target_date"])
        trades = sorted(
            (
                row
                for row in event_rows.get(key, [])
                if str(row.get("type") or "").upper() == "TRADE"
            ),
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
                        "buy_cost": 0.0,
                        "sell_proceeds": 0.0,
                        "actions": defaultdict(float),
                    }
                )
            phase = phases[-1]
            phase["last_ts"] = timestamp
            phase["trade_rows"] += 1
            side = str(row.get("side") or "").upper()
            cash = as_float(row.get("usdcSize"))
            if side == "BUY":
                phase["buy_cost"] += cash
            else:
                phase["sell_proceeds"] += cash
            action = (
                f"{side} {str(row.get('outcome') or '').upper()} "
                f"{str(row.get('title') or row.get('slug') or '')}"
            )
            phase["actions"][action] += as_float(row.get("size"))
        compact_phases = []
        for phase in phases:
            actions = sorted(
                phase.pop("actions").items(), key=lambda item: item[1], reverse=True
            )[:4]
            compact_phases.append(
                {
                    **phase,
                    "first_utc": datetime.fromtimestamp(
                        phase["first_ts"], timezone.utc
                    ).isoformat(),
                    "last_utc": datetime.fromtimestamp(
                        phase["last_ts"], timezone.utc
                    ).isoformat(),
                    "top_actions_by_shares": [
                        {"action": action, "shares": shares}
                        for action, shares in actions
                    ],
                }
            )
        output.append(
            {
                "city": portfolio["city"],
                "target_date": portfolio["target_date"],
                "winner_label": portfolio["winner_label"],
                "buy_cost": as_float(portfolio["buy_cost"]),
                "pnl": as_float(portfolio["public_cashflow"]),
                "roi": ratio(
                    as_float(portfolio["public_cashflow"]),
                    as_float(portfolio["buy_cost"]),
                ),
                "expression": portfolio["expression"],
                "exit_style": portfolio["exit_style"],
                "phase_count_gap_gt_10m": len(compact_phases),
                "phases": compact_phases,
            }
        )
    return output


def audit_hk_20260714_maker_fills(
    activities: list[dict[str, Any]], rpc_url: str
) -> dict[str, Any]:
    """Decode public Polygon OrderFilled logs for the extreme 28 YES fills."""
    import requests

    rows = [
        row
        for row in activities
        if row.get("eventSlug")
        == "highest-temperature-in-hong-kong-on-july-14-2026"
        and str(row.get("type") or "").upper() == "TRADE"
        and str(row.get("side") or "").upper() == "BUY"
        and str(row.get("outcome") or "").lower() == "yes"
        and "28°C" in str(row.get("title") or "")
        and as_float(row.get("price")) <= 0.003
    ]
    transaction_timestamps: dict[str, int] = {}
    for row in rows:
        transaction_timestamps[str(row["transactionHash"]).lower()] = as_int(
            row["timestamp"]
        )
    transaction_hashes = sorted(transaction_timestamps)
    payload = [
        {
            "jsonrpc": "2.0",
            "method": "eth_getTransactionReceipt",
            "params": [transaction_hash],
            "id": index,
        }
        for index, transaction_hash in enumerate(transaction_hashes)
    ]
    response = requests.post(rpc_url, json=payload, timeout=30)
    response.raise_for_status()
    receipts = {int(item["id"]): item.get("result") for item in response.json()}

    roles = defaultdict(int)
    orders: dict[str, dict[str, Any]] = {}
    receipt_count = 0
    for index, transaction_hash in enumerate(transaction_hashes):
        receipt = receipts.get(index)
        if not receipt:
            continue
        receipt_count += 1
        for log in receipt.get("logs") or []:
            topics = [str(value).lower() for value in log.get("topics") or []]
            if not topics or topics[0] != ORDER_FILLED_V2_TOPIC:
                continue
            maker = "0x" + topics[2][-40:]
            taker = "0x" + topics[3][-40:]
            role = None
            if maker == WALLET:
                role = (
                    "taker_order"
                    if taker == str(log.get("address") or "").lower()
                    else "passive_maker_order"
                )
            elif taker == WALLET:
                role = "direct_fill_counterparty"
            if role is None:
                continue
            roles[role] += 1
            data = str(log.get("data") or "")[2:]
            words = [int(data[offset : offset + 64], 16) for offset in range(0, len(data), 64)]
            side, token_id, maker_amount, taker_amount, fee = words[:5]
            order_hash = topics[1]
            item = orders.setdefault(
                order_hash,
                {
                    "order_hash": order_hash,
                    "role": role,
                    "side": "BUY" if side == 0 else "SELL",
                    "token_id": str(token_id),
                    "fill_events": 0,
                    "maker_amount_raw": 0,
                    "taker_amount_raw": 0,
                    "fee_raw": 0,
                    "first_fill_ts": transaction_timestamps[transaction_hash],
                    "last_fill_ts": transaction_timestamps[transaction_hash],
                },
            )
            item["fill_events"] += 1
            item["maker_amount_raw"] += maker_amount
            item["taker_amount_raw"] += taker_amount
            item["fee_raw"] += fee
            item["first_fill_ts"] = min(
                item["first_fill_ts"], transaction_timestamps[transaction_hash]
            )
            item["last_fill_ts"] = max(
                item["last_fill_ts"], transaction_timestamps[transaction_hash]
            )

    order_rows = []
    for item in orders.values():
        cash = item["maker_amount_raw"] / 1_000_000
        shares = item["taker_amount_raw"] / 1_000_000
        order_rows.append(
            {
                **item,
                "cash": cash,
                "shares": shares,
                "average_price": ratio(cash, shares),
                "fee": item["fee_raw"] / 1_000_000,
                "fill_span_seconds": item["last_fill_ts"] - item["first_fill_ts"],
            }
        )
    order_rows.sort(key=lambda item: item["average_price"], reverse=True)
    return {
        "case": "hong-kong_2026-07-14_28C_YES_price_le_0.003",
        "rpc_url": rpc_url,
        "activity_rows": len(rows),
        "distinct_transactions": len(transaction_hashes),
        "receipts_found": receipt_count,
        "wallet_order_filled_roles": dict(roles),
        "distinct_wallet_order_hashes": len(order_rows),
        "orders": order_rows,
        "activity_total_shares": sum(as_float(row["size"]) for row in rows),
        "activity_total_cash": sum(as_float(row["usdcSize"]) for row in rows),
        "first_fill_ts": min(transaction_timestamps.values()),
        "last_fill_ts": max(transaction_timestamps.values()),
        "fill_span_seconds": max(transaction_timestamps.values())
        - min(transaction_timestamps.values()),
        "interpretation_boundary": (
            "OrderFilled proves wallet role and order reuse at fill time. It does not "
            "reveal offchain order-post time, queue rank, cancelled quantity, or the "
            "contemporaneous full order book."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--rpc-url",
        default="",
        help="Optional Polygon JSON-RPC URL for the HK 2026-07-14 maker audit.",
    )
    args = parser.parse_args()

    full_ladder = args.snapshot / "analysis" / "full_ladder_history_v1"
    all_portfolios = read_csv(full_ladder / "event_portfolios.csv")
    portfolios = [
        row
        for row in all_portfolios
        if row["cashflow_complete"] == "True" and as_float(row["buy_cost"]) > 0
    ]
    activities = read_jsonl_gz(args.snapshot / "weather_activity.jsonl.gz")

    slug_to_key: dict[str, tuple[str, str]] = {}
    for row in portfolios:
        for slug in json.loads(row["event_slugs"]):
            slug_to_key[str(slug)] = (row["city"], row["target_date"])
    complete_keys = {(row["city"], row["target_date"]) for row in portfolios}

    slice_rows: list[dict[str, Any]] = []
    for field, mapping in [
        ("city", None),
        ("expression", None),
        ("exit_style", None),
        (
            "first_entry_day_offset",
            {str(value): phase_label(value) for value in range(-10, 11)},
        ),
    ]:
        slice_rows.extend(aggregate(portfolios, field, mapping))

    lifecycle_rows, execution, event_rows = build_lifecycle(
        activities, slug_to_key, complete_keys
    )
    cases = build_cases(portfolios, event_rows)
    maker_audit = (
        audit_hk_20260714_maker_fills(activities, args.rpc_url)
        if args.rpc_url
        else None
    )

    total_cost = sum(as_float(row["buy_cost"]) for row in portfolios)
    total_pnl = sum(as_float(row["public_cashflow"]) for row in portfolios)
    total_sell = sum(as_float(row["sell_proceeds"]) for row in portfolios)
    total_redeem = sum(as_float(row["redeem_cash"]) for row in portfolios)
    total_merge = sum(as_float(row["merge_cash"]) for row in portfolios)
    by_date_rows = aggregate(portfolios, "target_date")
    ranked_events = sorted(
        portfolios, key=lambda row: as_float(row["public_cashflow"]), reverse=True
    )
    ranked_dates = sorted(by_date_rows, key=lambda row: row["pnl"], reverse=True)
    top3_cities = {"hong-kong", "shenzhen", "guangzhou"}
    top3_rows = [row for row in portfolios if row["city"] in top3_cities]
    top3_cost = sum(as_float(row["buy_cost"]) for row in top3_rows)
    top3_pnl = sum(as_float(row["public_cashflow"]) for row in top3_rows)
    date_rois = [
        row["pnl"] / row["buy_cost"] for row in by_date_rows if row["buy_cost"]
    ]
    ordered_dates = sorted({row["target_date"] for row in portfolios})
    split_index = len(ordered_dates) // 2

    def period_summary(selected_dates: list[str]) -> dict[str, Any]:
        date_set = set(selected_dates)
        rows = [row for row in portfolios if row["target_date"] in date_set]
        cost = sum(as_float(row["buy_cost"]) for row in rows)
        pnl = sum(as_float(row["public_cashflow"]) for row in rows)
        return {
            "date_min": min(selected_dates),
            "date_max": max(selected_dates),
            "target_dates": len(selected_dates),
            "portfolios": len(rows),
            "buy_cost": cost,
            "pnl": pnl,
            "roi": ratio(pnl, cost),
        }

    summary = {
        "schema_version": "weatherhk2_strategy_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "wallet": WALLET,
        "snapshot": str(args.snapshot),
        "data_snapshot": {
            "activity_rows": len(activities),
            "portfolio_rows": len(all_portfolios),
            "cashflow_complete_portfolios": len(portfolios),
            "unsettled_or_incomplete_portfolios": len(all_portfolios) - len(portfolios),
            "unsettled_or_incomplete_share": ratio(
                len(all_portfolios) - len(portfolios), len(all_portfolios)
            ),
            "target_dates": len({row["target_date"] for row in portfolios}),
            "date_min": min(row["target_date"] for row in portfolios),
            "date_max": max(row["target_date"] for row in portfolios),
            "metadata_incomplete_portfolios": sum(
                row["metadata_complete"] != "True" for row in all_portfolios
            ),
            "missing_bracket_metadata_portfolios": sum(
                as_int(row["ladder_brackets"]) == 0 for row in all_portfolios
            ),
        },
        "performance": {
            "buy_cost": total_cost,
            "public_cashflow_pnl": total_pnl,
            "turnover_roi": ratio(total_pnl, total_cost),
            "mean_target_date_roi": statistics.fmean(date_rois),
            "median_target_date_roi": statistics.median(date_rois),
            "positive_target_date_share": ratio(
                sum(row["pnl"] > 0 for row in by_date_rows), len(by_date_rows)
            ),
            "target_date_bootstrap": block_bootstrap_roi(portfolios),
            "public_cashflow_components": {
                "sell_proceeds": total_sell,
                "redeem_cash": total_redeem,
                "merge_cash": total_merge,
                "buy_cost": total_cost,
                "sell_proceeds_over_buy_cost": ratio(total_sell, total_cost),
            },
            "chronological_halves": {
                "early": period_summary(ordered_dates[:split_index]),
                "late": period_summary(ordered_dates[split_index:]),
                "latest_30_target_dates": period_summary(ordered_dates[-30:]),
            },
        },
        "regional_concentration": {
            "top3_cities": sorted(top3_cities),
            "buy_cost": top3_cost,
            "buy_cost_share": ratio(top3_cost, total_cost),
            "pnl": top3_pnl,
            "pnl_share": ratio(top3_pnl, total_pnl),
            "roi": ratio(top3_pnl, top3_cost),
        },
        "profit_concentration": {
            "top_event": {
                "city": ranked_events[0]["city"],
                "target_date": ranked_events[0]["target_date"],
                "pnl": as_float(ranked_events[0]["public_cashflow"]),
                "share_of_total_pnl": ratio(
                    as_float(ranked_events[0]["public_cashflow"]), total_pnl
                ),
            },
            "top5_event_pnl_share": ratio(
                sum(as_float(row["public_cashflow"]) for row in ranked_events[:5]),
                total_pnl,
            ),
            "top5_target_date_pnl_share": ratio(
                sum(row["pnl"] for row in ranked_dates[:5]), total_pnl
            ),
            "roi_excluding_top_event": ratio(
                total_pnl - as_float(ranked_events[0]["public_cashflow"]),
                total_cost - as_float(ranked_events[0]["buy_cost"]),
            ),
            "roi_excluding_top5_target_dates": ratio(
                total_pnl - sum(row["pnl"] for row in ranked_dates[:5]),
                total_cost - sum(row["buy_cost"] for row in ranked_dates[:5]),
            ),
        },
        "execution_and_lifecycle": execution,
        "onchain_case_audit": maker_audit,
        "limitations": [
            "Public activity timestamps are fills, not original order-post times.",
            "The Data API activity rows do not identify maker/taker. Public OrderFilled receipts can recover that role, but not order-post time, cancelled orders, queue position, private signals, or contemporaneous executable book state.",
            "Condition-level paired VWAP sums are lifetime diagnostics and are not evidence that a synchronous arbitrage was executable.",
            "Public cashflow is used only on cashflow-complete resolved portfolios; fee attribution is not independently observable per fill.",
        ],
    }

    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "slice_performance.csv", slice_rows)
    write_csv(args.output / "condition_lifecycle.csv", lifecycle_rows)
    write_csv(args.output / "target_date_performance.csv", by_date_rows)
    write_json(args.output / "case_timelines.json", cases)
    if maker_audit:
        write_json(args.output / "hk_20260714_maker_audit.json", maker_audit)
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
