#!/usr/bin/env python3
"""Profile LMVM-style pre-target repricing from complete public wallet history.

The unit is a cashflow-complete city x target_date ladder.  Public activity
timestamps are fills, not private signal or order-post timestamps.  Polygon
receipts establish wallet-side maker/taker roles but cannot reveal cancelled
orders, queue rank, or the contemporaneous book.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import statistics
from typing import Any, Iterable

from research_external_wallet_representative_lineages_v1 import (
    as_float,
    batch_receipts_with_fallback,
)
import research_weatherhk2_case_lineage_v1 as lineage


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError("refusing to write empty CSV")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def percentile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = probability * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def distribution(values: Iterable[float]) -> dict[str, float | int | None]:
    rows = [float(value) for value in values]
    return {
        "n": len(rows),
        "p10": percentile(rows, 0.10),
        "p25": percentile(rows, 0.25),
        "median": percentile(rows, 0.50),
        "p75": percentile(rows, 0.75),
        "p90": percentile(rows, 0.90),
        "mean": statistics.fmean(rows) if rows else None,
    }


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def band(value: float, boundaries: list[float]) -> str:
    lower = 0.0
    for upper in boundaries:
        if value < upper:
            return f"{lower:.2f}-{upper:.2f}"
        lower = upper
    return f"{lower:.2f}+"


def grouped_performance(
    rows: list[dict[str, Any]], key: str
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[key])].append(row)
    result: dict[str, dict[str, Any]] = {}
    for label, members in sorted(groups.items()):
        cost = sum(as_float(row["buy_cost"]) for row in members)
        pnl = sum(as_float(row["pnl"]) for row in members)
        result[label] = {
            "events": len(members),
            "target_dates": len({row["target_date"] for row in members}),
            "buy_cost": cost,
            "pnl": pnl,
            "turnover_roi": ratio(pnl, cost),
            "positive_event_share": ratio(
                sum(as_float(row["pnl"]) > 0 for row in members), len(members)
            ),
        }
    return result


def exit_band(value: float) -> str:
    if value < -0.20:
        return "<-20%"
    if value < -0.05:
        return "-20%..-5%"
    if value < 0:
        return "-5%..0%"
    if value < 0.02:
        return "0%..2%"
    if value < 0.05:
        return "2%..5%"
    if value < 0.10:
        return "5%..10%"
    return "10%+"


def date_stability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_date: dict[str, dict[str, float]] = defaultdict(
        lambda: {"buy_cost": 0.0, "pnl": 0.0}
    )
    for row in rows:
        bucket = by_date[str(row["target_date"])]
        bucket["buy_cost"] += as_float(row["buy_cost"])
        bucket["pnl"] += as_float(row["pnl"])
    dates = sorted(by_date)
    date_rows = [
        {
            "target_date": target_date,
            **by_date[target_date],
            "roi": ratio(
                by_date[target_date]["pnl"], by_date[target_date]["buy_cost"]
            ),
        }
        for target_date in dates
    ]
    top_five = sorted(date_rows, key=lambda row: as_float(row["pnl"]), reverse=True)[:5]
    top_keys = {row["target_date"] for row in top_five}
    without_top = [row for row in date_rows if row["target_date"] not in top_keys]
    midpoint = len(date_rows) // 2

    def summarize(members: list[dict[str, Any]]) -> dict[str, Any]:
        cost = sum(as_float(row["buy_cost"]) for row in members)
        pnl = sum(as_float(row["pnl"]) for row in members)
        return {
            "target_dates": len(members),
            "buy_cost": cost,
            "pnl": pnl,
            "turnover_roi": ratio(pnl, cost),
            "positive_target_date_share": ratio(
                sum(as_float(row["pnl"]) > 0 for row in members), len(members)
            ),
        }

    return {
        "all": summarize(date_rows),
        "early_half": summarize(date_rows[:midpoint]),
        "late_half": summarize(date_rows[midpoint:]),
        "without_top_five_pnl_dates": summarize(without_top),
        "top_five_pnl_dates": top_five,
        "largest_loss_dates": sorted(date_rows, key=lambda row: as_float(row["pnl"]))[:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rpc-url", action="append", required=True)
    args = parser.parse_args()

    wallet = args.wallet.lower()
    snapshot = args.snapshot.resolve()
    analysis = snapshot / "analysis" / "full_ladder_history_v1"
    activity = read_jsonl_gz(snapshot / "weather_activity.jsonl.gz")
    portfolios = read_csv(analysis / "event_portfolios.csv")
    history = json.loads((analysis / "summary.json").read_text(encoding="utf-8"))

    slug_to_key: dict[str, tuple[str, str]] = {}
    portfolio_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for portfolio in portfolios:
        key = (portfolio["city"], portfolio["target_date"])
        portfolio_by_key[key] = portfolio
        for slug in json.loads(portfolio["event_slugs"]):
            slug_to_key[str(slug)] = key

    rows_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in activity:
        key = slug_to_key.get(str(row.get("eventSlug") or ""))
        if key:
            rows_by_key[key].append(row)

    event_rows: list[dict[str, Any]] = []
    for key, portfolio in portfolio_by_key.items():
        if str(portfolio.get("cashflow_complete", "")).lower() != "true":
            continue
        rows = sorted(rows_by_key[key], key=lambda row: int(row.get("timestamp") or 0))
        trades = [row for row in rows if str(row.get("type") or "").upper() == "TRADE"]
        buys = [row for row in trades if str(row.get("side") or "").upper() == "BUY"]
        sells = [row for row in trades if str(row.get("side") or "").upper() == "SELL"]
        yes_buys = [row for row in buys if str(row.get("outcome") or "").lower() == "yes"]
        if not buys:
            continue

        buy_by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in buys:
            buy_by_condition[str(row.get("conditionId") or "")].append(row)
        yes_by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in yes_buys:
            yes_by_condition[str(row.get("conditionId") or "")].append(row)
        main_condition, main_rows = max(
            buy_by_condition.items(),
            key=lambda item: sum(as_float(row.get("usdcSize")) for row in item[1]),
        )
        main_yes_condition = None
        main_yes_rows: list[dict[str, Any]] = []
        if yes_by_condition:
            main_yes_condition, main_yes_rows = max(
                yes_by_condition.items(),
                key=lambda item: sum(as_float(row.get("usdcSize")) for row in item[1]),
            )

        buy_cost = sum(as_float(row.get("usdcSize")) for row in buys)
        buy_shares = sum(as_float(row.get("size")) for row in buys)
        main_cost = sum(as_float(row.get("usdcSize")) for row in main_rows)
        main_shares = sum(as_float(row.get("size")) for row in main_rows)
        main_sells = [
            row
            for row in sells
            if str(row.get("conditionId") or "") == main_condition
            and str(row.get("outcome") or "").lower()
            == str(main_rows[0].get("outcome") or "").lower()
        ]
        main_sell_shares = sum(as_float(row.get("size")) for row in main_sells)
        main_sell_proceeds = sum(as_float(row.get("usdcSize")) for row in main_sells)
        main_sold_fraction = ratio(main_sell_shares, main_shares)
        main_buy_cash_per_share = ratio(main_cost, main_shares)
        main_sell_cash_per_share = ratio(main_sell_proceeds, main_sell_shares)
        main_exit_return = (
            main_sell_cash_per_share / main_buy_cash_per_share - 1
            if main_buy_cash_per_share and main_sell_cash_per_share is not None
            else None
        )
        first_buy_ts = min(int(row.get("timestamp") or 0) for row in buys)
        last_buy_ts = max(int(row.get("timestamp") or 0) for row in buys)
        first_sell_ts = min((int(row.get("timestamp") or 0) for row in sells), default=0)
        last_sell_ts = max((int(row.get("timestamp") or 0) for row in sells), default=0)
        first_main_sell_ts = min(
            (int(row.get("timestamp") or 0) for row in main_sells), default=0
        )
        largest_buy_tx = max(
            (
                sum(
                    as_float(row.get("usdcSize"))
                    for row in buys
                    if row.get("transactionHash") == transaction
                )
                for transaction in {row.get("transactionHash") for row in buys}
            ),
            default=0.0,
        )
        main_title = str(main_rows[0].get("title") or "")
        main_slug = str(main_rows[0].get("slug") or "")
        main_outcome = str(main_rows[0].get("outcome") or "")
        main_yes_slug = str(main_yes_rows[0].get("slug") or "") if main_yes_rows else None
        main_yes_title = str(main_yes_rows[0].get("title") or "") if main_yes_rows else None
        main_yes_cost = sum(as_float(row.get("usdcSize")) for row in main_yes_rows)
        main_yes_shares = sum(as_float(row.get("size")) for row in main_yes_rows)
        entry_quote = ratio(
            sum(as_float(row.get("price")) * as_float(row.get("size")) for row in main_rows),
            main_shares,
        )
        event_rows.append(
            {
                "city": key[0],
                "target_date": key[1],
                "buy_cost": buy_cost,
                "buy_shares": buy_shares,
                "buy_transactions": len({row.get("transactionHash") for row in buys}),
                "buy_conditions": len(buy_by_condition),
                "yes_buy_conditions": len(yes_by_condition),
                "yes_buy_cost_share": ratio(
                    sum(as_float(row.get("usdcSize")) for row in yes_buys), buy_cost
                ),
                "main_condition": main_condition,
                "main_title": main_title,
                "main_slug": main_slug,
                "main_outcome": main_outcome,
                "main_cost": main_cost,
                "main_shares": main_shares,
                "main_cost_share": ratio(main_cost, buy_cost),
                "main_entry_quote": entry_quote,
                "main_cash_per_share": ratio(main_cost, main_shares),
                "main_sell_shares": main_sell_shares,
                "main_sell_proceeds": main_sell_proceeds,
                "main_sold_fraction": main_sold_fraction,
                "main_sell_cash_per_share": main_sell_cash_per_share,
                "main_exit_return": main_exit_return,
                "main_exit_return_band": (
                    exit_band(main_exit_return) if main_exit_return is not None else "no_main_sell"
                ),
                "main_fully_exited": bool(
                    main_sold_fraction is not None and main_sold_fraction >= 0.99
                ),
                "first_main_sell_delay_minutes": (
                    (first_main_sell_ts - first_buy_ts) / 60
                    if first_main_sell_ts
                    else None
                ),
                "main_yes_condition": main_yes_condition,
                "main_yes_title": main_yes_title,
                "main_yes_slug": main_yes_slug,
                "main_yes_cost": main_yes_cost,
                "main_yes_shares": main_yes_shares,
                "main_yes_is_winner": (
                    main_yes_condition == portfolio.get("winner_condition")
                    if main_yes_condition
                    else None
                ),
                "largest_buy_transaction_cost": largest_buy_tx,
                "first_entry_day_offset": portfolio.get("first_entry_day_offset"),
                "first_buy_local_hour": portfolio.get("first_buy_local_hour"),
                "buy_span_minutes": as_float(portfolio.get("buy_span_minutes")),
                "first_buy_to_first_sell_hours": (
                    (first_sell_ts - first_buy_ts) / 3600 if first_sell_ts else None
                ),
                "first_buy_to_last_sell_hours": (
                    (last_sell_ts - first_buy_ts) / 3600 if last_sell_ts else None
                ),
                "first_sell_before_last_buy": bool(
                    first_sell_ts and first_sell_ts < last_buy_ts
                ),
                "has_sell": bool(sells),
                "sell_proceeds": sum(as_float(row.get("usdcSize")) for row in sells),
                "winner_label": portfolio.get("winner_label"),
                "pnl": as_float(portfolio.get("public_cashflow")),
                "roi": ratio(as_float(portfolio.get("public_cashflow")), buy_cost),
                "entry_price_band": band(entry_quote or 0.0, [0.10, 0.20, 0.40, 0.60]),
                "holding_band": (
                    "no_sell"
                    if not first_sell_ts
                    else "<5m"
                    if first_sell_ts - first_buy_ts < 300
                    else "5-30m"
                    if first_sell_ts - first_buy_ts < 1800
                    else "30m-2h"
                    if first_sell_ts - first_buy_ts < 7200
                    else "2-8h"
                    if first_sell_ts - first_buy_ts < 28800
                    else "8h+"
                ),
            }
        )

    utc_hour_cost: dict[str, float] = defaultdict(float)
    for row in activity:
        if (
            str(row.get("type") or "").upper() != "TRADE"
            or str(row.get("side") or "").upper() != "BUY"
        ):
            continue
        hour = datetime.fromtimestamp(
            int(row.get("timestamp") or 0), timezone.utc
        ).hour
        label = (
            "00-06" if hour < 6 else "06-12" if hour < 12 else "12-18" if hour < 18 else "18-24"
        )
        utc_hour_cost[label] += as_float(row.get("usdcSize"))

    transaction_hashes = sorted(
        {
            str(row.get("transactionHash") or "").lower()
            for row in activity
            if str(row.get("type") or "").upper() == "TRADE"
            and row.get("transactionHash")
        }
    )
    receipts = batch_receipts_with_fallback(transaction_hashes, args.rpc_url)
    lineage.WALLET = wallet
    _, orders = lineage.decode_wallet_orders(receipts)
    role_rows: dict[tuple[str, str], dict[str, float | int]] = defaultdict(
        lambda: {"orders": 0, "fill_events": 0, "cash": 0.0, "shares": 0.0}
    )
    for order in orders.values():
        key = (str(order.get("side")), str(order.get("role")))
        role_rows[key]["orders"] += 1
        role_rows[key]["fill_events"] += int(order.get("fill_events") or 0)
        role_rows[key]["cash"] += as_float(order.get("cash"))
        role_rows[key]["shares"] += as_float(order.get("shares"))

    resolved_cost = sum(as_float(row["buy_cost"]) for row in event_rows)
    resolved_pnl = sum(as_float(row["pnl"]) for row in event_rows)
    main_yes_rows = [row for row in event_rows if row["main_yes_condition"]]
    main_sell_rows = [
        row for row in event_rows if row["main_exit_return"] is not None
    ]
    fully_exited_main_rows = [
        row for row in main_sell_rows if row["main_fully_exited"]
    ]
    role_payload = {
        f"{side}:{role}": values
        for (side, role), values in sorted(role_rows.items())
    }
    buy_maker_cash = as_float(
        role_payload.get("BUY:passive_maker_order", {}).get("cash")
    )
    buy_taker_cash = as_float(role_payload.get("BUY:taker_order", {}).get("cash"))
    sell_maker_cash = as_float(
        role_payload.get("SELL:passive_maker_order", {}).get("cash")
    )
    sell_taker_cash = as_float(role_payload.get("SELL:taker_order", {}).get("cash"))
    wallet_settlement = history["settlement_behavior"]
    summary = {
        "schema_version": "lmvm_repricing_profile_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "wallet": wallet,
        "snapshot": str(snapshot),
        "grain": "cashflow-complete city_x_target_date_ladder",
        "coverage": {
            "public_activity_rows": len(activity),
            "all_portfolios": len(portfolios),
            "cashflow_complete_buy_portfolios": len(event_rows),
            "cashflow_complete_resolved_portfolios": wallet_settlement[
                "cashflow_complete_resolved_events"
            ],
            "independent_target_dates": len({row["target_date"] for row in event_rows}),
            "trade_transactions": len(transaction_hashes),
            "receipts_found": sum(value is not None for value in receipts.values()),
            "decoded_wallet_orders": len(orders),
            "metadata_missing_events": history["coverage"]["metadata_incomplete_portfolios"],
        },
        "performance": {
            "wallet_cashflow_complete": {
                "buy_cost": wallet_settlement["cashflow_complete_resolved_buy_cost"],
                "pnl": wallet_settlement["cashflow_complete_resolved_pnl"],
                "turnover_roi": wallet_settlement[
                    "cashflow_complete_turnover_roi"
                ],
            },
            "repricing_buy_portfolios": {
                "buy_cost": resolved_cost,
                "pnl": resolved_pnl,
                "turnover_roi": ratio(resolved_pnl, resolved_cost),
            },
            "positive_event_share": ratio(
                sum(as_float(row["pnl"]) > 0 for row in event_rows), len(event_rows)
            ),
            "negative_event_share": ratio(
                sum(as_float(row["pnl"]) < 0 for row in event_rows), len(event_rows)
            ),
            "pnl_distribution": distribution(as_float(row["pnl"]) for row in event_rows),
            "roi_distribution": distribution(as_float(row["roi"]) for row in event_rows),
            "history_target_date_bootstrap": history["settlement_behavior"]["target_date_block_bootstrap"],
            "date_stability": date_stability(event_rows),
            "largest_win_events": sorted(
                event_rows, key=lambda row: as_float(row["pnl"]), reverse=True
            )[:5],
            "largest_loss_events": sorted(
                event_rows, key=lambda row: as_float(row["pnl"])
            )[:5],
        },
        "selection": {
            "yes_buy_cost_share": ratio(
                sum(as_float(row["buy_cost"]) * as_float(row["yes_buy_cost_share"]) for row in event_rows),
                resolved_cost,
            ),
            "single_condition_event_share": ratio(
                sum(int(row["buy_conditions"]) == 1 for row in event_rows), len(event_rows)
            ),
            "single_yes_condition_event_share": ratio(
                sum(int(row["yes_buy_conditions"]) == 1 for row in event_rows), len(event_rows)
            ),
            "main_cost_share_distribution": distribution(
                as_float(row["main_cost_share"]) for row in event_rows
            ),
            "main_entry_quote_distribution": distribution(
                as_float(row["main_entry_quote"]) for row in event_rows
            ),
            "main_yes_winner_share": ratio(
                sum(row["main_yes_is_winner"] is True for row in main_yes_rows),
                len(main_yes_rows),
            ),
            "main_yes_winner_denominator": len(main_yes_rows),
            "by_entry_price_band": grouped_performance(event_rows, "entry_price_band"),
        },
        "sizing": {
            "event_buy_cost": distribution(as_float(row["buy_cost"]) for row in event_rows),
            "event_buy_shares": distribution(as_float(row["buy_shares"]) for row in event_rows),
            "main_condition_cost": distribution(as_float(row["main_cost"]) for row in event_rows),
            "main_condition_shares": distribution(as_float(row["main_shares"]) for row in event_rows),
            "largest_buy_transaction_cost": distribution(
                as_float(row["largest_buy_transaction_cost"]) for row in event_rows
            ),
            "buy_transactions": distribution(float(row["buy_transactions"]) for row in event_rows),
        },
        "timing": {
            "entry_cost_share_by_target_day_offset": history["entry_fill_timing"]["buy_cost_share_by_target_day_offset"],
            "entry_cost_share_by_local_hour_band": history["entry_fill_timing"]["buy_cost_share_by_local_hour_band"],
            "entry_cost_share_by_utc_hour_band": {
                label: ratio(cost, sum(utc_hour_cost.values()))
                for label, cost in sorted(utc_hour_cost.items())
            },
            "buy_span_minutes": distribution(as_float(row["buy_span_minutes"]) for row in event_rows),
            "first_buy_to_first_sell_hours": distribution(
                as_float(row["first_buy_to_first_sell_hours"])
                for row in event_rows
                if row["first_buy_to_first_sell_hours"] is not None
            ),
            "first_buy_to_last_sell_hours": distribution(
                as_float(row["first_buy_to_last_sell_hours"])
                for row in event_rows
                if row["first_buy_to_last_sell_hours"] is not None
            ),
            "by_holding_band": grouped_performance(event_rows, "holding_band"),
        },
        "exit_diagnostics": {
            "main_condition_with_sell_events": len(main_sell_rows),
            "main_condition_fully_exited_events": len(fully_exited_main_rows),
            "main_condition_fully_exited_share": ratio(
                len(fully_exited_main_rows), len(main_sell_rows)
            ),
            "first_sell_before_last_buy_share": ratio(
                sum(bool(row["first_sell_before_last_buy"]) for row in event_rows),
                len(event_rows),
            ),
            "main_sold_fraction_distribution": distribution(
                as_float(row["main_sold_fraction"]) for row in main_sell_rows
            ),
            "main_exit_return_all_sellers": distribution(
                as_float(row["main_exit_return"]) for row in main_sell_rows
            ),
            "main_exit_return_fully_exited": distribution(
                as_float(row["main_exit_return"])
                for row in fully_exited_main_rows
            ),
            "first_main_sell_delay_minutes": distribution(
                as_float(row["first_main_sell_delay_minutes"])
                for row in main_sell_rows
            ),
            "fully_exited_by_return_band": grouped_performance(
                fully_exited_main_rows, "main_exit_return_band"
            ),
        },
        "execution": {
            "sell_event_share": ratio(sum(bool(row["has_sell"]) for row in event_rows), len(event_rows)),
            "wallet_side_onchain_roles": role_payload,
            "buy_maker_cash_share": ratio(
                buy_maker_cash, buy_maker_cash + buy_taker_cash
            ),
            "buy_taker_cash_share": ratio(
                buy_taker_cash, buy_maker_cash + buy_taker_cash
            ),
            "sell_maker_cash_share": ratio(
                sell_maker_cash, sell_maker_cash + sell_taker_cash
            ),
            "sell_taker_cash_share": ratio(
                sell_taker_cash, sell_maker_cash + sell_taker_cash
            ),
            "public_boundary": (
                "receipts prove wallet-side maker/taker for decoded fills; original "
                "post time, cancelled/unfilled orders, queue rank, and live book are unavailable"
            ),
        },
        "city_performance": dict(
            sorted(
                grouped_performance(event_rows, "city").items(),
                key=lambda item: as_float(item[1]["buy_cost"]),
                reverse=True,
            )
        ),
    }
    output = args.output.resolve()
    write_json(output / "summary.json", summary)
    write_csv(output / "event_profile.csv", event_rows)
    print(json.dumps({"status": "complete", "output": str(output), **summary["coverage"]}, sort_keys=True))


if __name__ == "__main__":
    main()
