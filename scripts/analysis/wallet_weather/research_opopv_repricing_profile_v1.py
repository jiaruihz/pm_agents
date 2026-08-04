#!/usr/bin/env python3
"""Profile opopv public weather inventory and low-complexity repricing subsets.

The analysis grain is a cashflow-complete city x target-date ladder.  Public
activity timestamps are fill timestamps, not private signal/post timestamps.
This script deliberately does not infer maker/taker from public activity; that
requires Polygon receipts and is reported only for separately decoded cases.
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
import random
import statistics
from typing import Any, Iterable


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
        raise RuntimeError("refusing to write empty event profile")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def truthy(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def percentile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = probability * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def distribution(values: Iterable[float]) -> dict[str, Any]:
    rows = [float(value) for value in values]
    return {
        "n": len(rows),
        "min": min(rows) if rows else None,
        "p10": percentile(rows, 0.10),
        "p25": percentile(rows, 0.25),
        "median": percentile(rows, 0.50),
        "p75": percentile(rows, 0.75),
        "p90": percentile(rows, 0.90),
        "p95": percentile(rows, 0.95),
        "max": max(rows) if rows else None,
        "mean": statistics.fmean(rows) if rows else None,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(as_float(row["buy_cost"]) for row in rows)
    pnl = sum(as_float(row["pnl"]) for row in rows)
    return {
        "events": len(rows),
        "target_dates": len({row["target_date"] for row in rows}),
        "buy_cost": cost,
        "pnl": pnl,
        "turnover_roi": ratio(pnl, cost),
        "positive_event_share": ratio(
            sum(as_float(row["pnl"]) > 0 for row in rows), len(rows)
        ),
        "buy_transactions": distribution(
            as_float(row["buy_transactions"]) for row in rows
        ),
        "buy_span_hours": distribution(
            as_float(row["buy_span_hours"]) for row in rows
        ),
        "first_buy_to_first_sell_hours": distribution(
            as_float(row["first_buy_to_first_sell_hours"])
            for row in rows
            if row["first_buy_to_first_sell_hours"] is not None
        ),
        "main_exit_return": distribution(
            as_float(row["main_exit_return"])
            for row in rows
            if row["main_exit_return"] is not None
        ),
    }


def block_bootstrap(rows: list[dict[str, Any]], samples: int = 5000) -> dict[str, Any]:
    by_date: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        bucket = by_date[str(row["target_date"])]
        bucket[0] += as_float(row["pnl"])
        bucket[1] += as_float(row["buy_cost"])
    dates = sorted(by_date)
    if not dates:
        return {"independent_target_dates": 0, "ci95": [None, None]}
    rng = random.Random(20260804)
    draws: list[float] = []
    for _ in range(samples):
        selected = [dates[rng.randrange(len(dates))] for _ in dates]
        pnl = sum(by_date[value][0] for value in selected)
        cost = sum(by_date[value][1] for value in selected)
        if cost:
            draws.append(pnl / cost)
    return {
        "block": "target_date",
        "independent_target_dates": len(dates),
        "samples": samples,
        "point_turnover_roi": ratio(
            sum(value[0] for value in by_date.values()),
            sum(value[1] for value in by_date.values()),
        ),
        "ci95": [percentile(draws, 0.025), percentile(draws, 0.975)],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    snapshot = args.snapshot.resolve()
    analysis = snapshot / "analysis" / "full_ladder_history_v1"
    history = json.loads((analysis / "summary.json").read_text(encoding="utf-8"))
    portfolios = read_csv(analysis / "event_portfolios.csv")
    activity = read_jsonl_gz(snapshot / "weather_activity.jsonl.gz")
    metadata = read_jsonl_gz(snapshot / "event_metadata.jsonl.gz")

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

    ladder: dict[str, dict[str, Any]] = {}
    for wrapper in metadata:
        event = wrapper.get("metadata")
        if not isinstance(event, dict):
            continue
        markets = [
            row for row in event.get("markets") or []
            if isinstance(row, dict) and row.get("conditionId")
        ]
        for index, market in enumerate(markets):
            ladder[str(market["conditionId"])] = {
                "index": index,
                "size": len(markets),
                "position": index / (len(markets) - 1) if len(markets) > 1 else 0.5,
            }

    event_rows: list[dict[str, Any]] = []
    utc_buy_cost: dict[int, float] = defaultdict(float)
    for key, portfolio in portfolio_by_key.items():
        if not truthy(portfolio.get("cashflow_complete")) or not truthy(portfolio.get("resolved")):
            continue
        rows = sorted(rows_by_key[key], key=lambda row: int(row.get("timestamp") or 0))
        trades = [row for row in rows if str(row.get("type") or "").upper() == "TRADE"]
        buys = [row for row in trades if str(row.get("side") or "").upper() == "BUY"]
        sells = [row for row in trades if str(row.get("side") or "").upper() == "SELL"]
        if not buys:
            continue

        buy_by_leg: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in buys:
            leg = (str(row.get("conditionId") or ""), str(row.get("outcome") or "").lower())
            buy_by_leg[leg].append(row)
            hour = datetime.fromtimestamp(int(row.get("timestamp") or 0), timezone.utc).hour
            utc_buy_cost[hour] += as_float(row.get("usdcSize"))
        main_leg, main_buys = max(
            buy_by_leg.items(),
            key=lambda item: sum(as_float(row.get("usdcSize")) for row in item[1]),
        )
        main_sells = [
            row for row in sells
            if (str(row.get("conditionId") or ""), str(row.get("outcome") or "").lower()) == main_leg
        ]
        main_cost = sum(as_float(row.get("usdcSize")) for row in main_buys)
        main_buy_shares = sum(as_float(row.get("size")) for row in main_buys)
        main_sell_cash = sum(as_float(row.get("usdcSize")) for row in main_sells)
        main_sell_shares = sum(as_float(row.get("size")) for row in main_sells)
        main_buy_cash_per_share = ratio(main_cost, main_buy_shares)
        main_sell_cash_per_share = ratio(main_sell_cash, main_sell_shares)
        main_exit_return = (
            main_sell_cash_per_share / main_buy_cash_per_share - 1
            if main_buy_cash_per_share and main_sell_cash_per_share is not None
            else None
        )
        first_buy = min(int(row.get("timestamp") or 0) for row in buys)
        last_buy = max(int(row.get("timestamp") or 0) for row in buys)
        first_sell = min((int(row.get("timestamp") or 0) for row in sells), default=0)
        last_sell = max((int(row.get("timestamp") or 0) for row in sells), default=0)
        yes_buys = [row for row in buys if str(row.get("outcome") or "").lower() == "yes"]
        no_buys = [row for row in buys if str(row.get("outcome") or "").lower() == "no"]
        main_ladder = ladder.get(main_leg[0], {})
        winner_ladder = ladder.get(str(portfolio.get("winner_condition") or ""), {})
        offset = int(float(portfolio["first_entry_day_offset"])) if portfolio.get("first_entry_day_offset") else None
        event_rows.append({
            "city": key[0],
            "target_date": key[1],
            "expression": portfolio.get("expression"),
            "exit_style": portfolio.get("exit_style"),
            "buy_cost": as_float(portfolio.get("buy_cost")),
            "pnl": as_float(portfolio.get("public_cashflow")),
            "roi": ratio(as_float(portfolio.get("public_cashflow")), as_float(portfolio.get("buy_cost"))),
            "buy_conditions": len(buy_by_leg),
            "all_yes": bool(yes_buys) and not no_buys,
            "all_no": bool(no_buys) and not yes_buys,
            "yes_buy_cost_share": ratio(sum(as_float(row.get("usdcSize")) for row in yes_buys), as_float(portfolio.get("buy_cost"))),
            "buy_transactions": int(float(portfolio.get("unique_buy_transactions") or 0)),
            "buy_span_hours": (last_buy - first_buy) / 3600,
            "first_entry_day_offset": offset,
            "has_sell": bool(sells),
            "first_sell_before_first_buy": bool(first_sell and first_sell < first_buy),
            "first_sell_before_last_buy": bool(first_sell and first_sell < last_buy),
            "first_buy_to_first_sell_hours": (first_sell - first_buy) / 3600 if first_sell else None,
            "first_buy_to_last_sell_hours": (last_sell - first_buy) / 3600 if last_sell else None,
            "sell_proceeds": as_float(portfolio.get("sell_proceeds")),
            "sell_price_cost_weighted": as_float(portfolio.get("sell_price_cost_weighted")),
            "main_condition": main_leg[0],
            "main_outcome": main_leg[1],
            "main_cost": main_cost,
            "main_cost_share": ratio(main_cost, as_float(portfolio.get("buy_cost"))),
            "main_entry_cash_per_share": main_buy_cash_per_share,
            "main_sell_cash_per_share": main_sell_cash_per_share,
            "main_sold_fraction": ratio(main_sell_shares, main_buy_shares),
            "main_exit_return": main_exit_return,
            "main_ladder_position": main_ladder.get("position"),
            "main_winner_index_distance": (
                int(main_ladder["index"]) - int(winner_ladder["index"])
                if main_ladder.get("index") is not None and winner_ladder.get("index") is not None
                else None
            ),
            "main_yes_is_winner": main_leg[1] == "yes" and main_leg[0] == portfolio.get("winner_condition"),
            "winner_label": portfolio.get("winner_label"),
            "has_redeem": truthy(portfolio.get("has_redeem")),
            "has_merge": truthy(portfolio.get("has_merge")),
        })

    subsets = {
        "all_resolved_complete": event_rows,
        "single_condition_active_sell": [
            row for row in event_rows if row["buy_conditions"] == 1 and row["has_sell"]
        ],
        "single_yes_D2_D1_active_sell": [
            row for row in event_rows
            if row["buy_conditions"] == 1 and row["all_yes"] and row["has_sell"]
            and row["first_entry_day_offset"] in {-2, -1}
        ],
        "single_yes_D2_D1_clean_sequence": [
            row for row in event_rows
            if row["buy_conditions"] == 1 and row["all_yes"] and row["has_sell"]
            and row["first_entry_day_offset"] in {-2, -1}
            and not row["first_sell_before_first_buy"]
        ],
    }
    subset_summary = {
        label: {**summarize(rows), "target_date_bootstrap": block_bootstrap(rows)}
        for label, rows in subsets.items()
    }

    yes_main = [row for row in event_rows if row["main_outcome"] == "yes"]
    main_sellers = [row for row in event_rows if row["main_exit_return"] is not None]
    total_buy_cost = sum(as_float(row["buy_cost"]) for row in event_rows)
    total_sell_proceeds = sum(as_float(row["sell_proceeds"]) for row in event_rows)
    total_pnl = sum(as_float(row["pnl"]) for row in event_rows)
    summary = {
        "schema_version": "opopv_repricing_profile_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "wallet": args.wallet.lower(),
        "snapshot": str(snapshot),
        "grain": "cashflow_complete_resolved_city_x_target_date_ladder",
        "coverage": {
            "public_weather_activity_rows": len(activity),
            "all_city_target_date_portfolios": history["coverage"]["city_target_date_portfolios"],
            "gamma_resolved_portfolios": history["settlement_behavior"]["gamma_resolved_events"],
            "cashflow_complete_resolved_portfolios": history["settlement_behavior"]["cashflow_complete_resolved_events"],
            "resolved_complete_buy_portfolios": len(event_rows),
            "independent_target_dates": len({row["target_date"] for row in event_rows}),
            "metadata_incomplete_portfolios": history["coverage"]["metadata_incomplete_portfolios"],
            "query_end_utc": json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))["coverage"]["query_end_utc"],
        },
        "performance": {
            "wallet_cashflow_complete": {
                "buy_cost": history["settlement_behavior"]["cashflow_complete_resolved_buy_cost"],
                "pnl": history["settlement_behavior"]["cashflow_complete_resolved_pnl"],
                "turnover_roi": history["settlement_behavior"]["cashflow_complete_turnover_roi"],
                "target_date_block_bootstrap": history["settlement_behavior"]["target_date_block_bootstrap"],
            },
            "repricing_buy_portfolios": {
                "buy_cost": total_buy_cost,
                "pnl": total_pnl,
                "turnover_roi": ratio(total_pnl, total_buy_cost),
                "target_date_block_bootstrap": block_bootstrap(event_rows),
            },
            "public_cashflow_break_even_incremental_fee_over_buy_plus_sell_notional": ratio(total_pnl, total_buy_cost + total_sell_proceeds),
            "pnl_distribution": distribution(as_float(row["pnl"]) for row in event_rows),
            "roi_distribution": distribution(as_float(row["roi"]) for row in event_rows),
        },
        "selection": {
            "yes_buy_cost_share": ratio(
                sum(as_float(row["buy_cost"]) * as_float(row["yes_buy_cost_share"]) for row in event_rows),
                total_buy_cost,
            ),
            "buy_condition_count_distribution": dict(sorted(Counter(int(row["buy_conditions"]) for row in event_rows).items())),
            "single_condition_share": ratio(sum(row["buy_conditions"] == 1 for row in event_rows), len(event_rows)),
            "main_cost_share_distribution": distribution(as_float(row["main_cost_share"]) for row in event_rows),
            "main_entry_cash_per_share_distribution": distribution(as_float(row["main_entry_cash_per_share"]) for row in event_rows),
            "main_yes_winner_share": ratio(sum(row["main_yes_is_winner"] for row in yes_main), len(yes_main)),
            "main_yes_winner_denominator": len(yes_main),
            "main_winner_index_distance_distribution": distribution(
                as_float(row["main_winner_index_distance"])
                for row in event_rows if row["main_winner_index_distance"] is not None
            ),
        },
        "timing_exit": {
            "buy_fill_cost_share_by_target_day_offset": history["entry_fill_timing"]["buy_cost_share_by_target_day_offset"],
            "portfolio_cost_share_by_first_entry_day_offset": {
                str(offset): ratio(
                    sum(as_float(row["buy_cost"]) for row in event_rows if row["first_entry_day_offset"] == offset),
                    total_buy_cost,
                )
                for offset in sorted({row["first_entry_day_offset"] for row in event_rows if row["first_entry_day_offset"] is not None})
            },
            "entry_buy_cost_share_by_utc_hour": {
                f"{hour:02d}": ratio(cost, sum(utc_buy_cost.values()))
                for hour, cost in sorted(utc_buy_cost.items())
            },
            "sell_event_share": ratio(sum(row["has_sell"] for row in event_rows), len(event_rows)),
            "first_sell_before_last_buy_share": ratio(sum(row["first_sell_before_last_buy"] for row in event_rows), len(event_rows)),
            "buy_span_hours": distribution(as_float(row["buy_span_hours"]) for row in event_rows),
            "first_buy_to_first_sell_hours": distribution(
                as_float(row["first_buy_to_first_sell_hours"])
                for row in event_rows if row["first_buy_to_first_sell_hours"] is not None
            ),
            "first_buy_to_last_sell_hours": distribution(
                as_float(row["first_buy_to_last_sell_hours"])
                for row in event_rows if row["first_buy_to_last_sell_hours"] is not None
            ),
            "main_exit_return": distribution(as_float(row["main_exit_return"]) for row in main_sellers),
            "sell_proceeds_share_ge_95c": history["sell_behavior"]["sell_proceeds_share_ge_95c"],
            "sell_proceeds_share_ge_99c": history["sell_behavior"]["sell_proceeds_share_ge_99c"],
        },
        "low_complexity_subsets": subset_summary,
    }

    write_json(args.output / "summary.json", summary)
    write_csv(args.output / "event_profile.csv", event_rows)
    print(json.dumps({
        "status": "complete",
        "events": len(event_rows),
        "roi": summary["performance"]["wallet_cashflow_complete"]["turnover_roi"],
        "single_yes_D2_D1_roi": subset_summary["single_yes_D2_D1_active_sell"]["turnover_roi"],
        "output": str(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
