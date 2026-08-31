#!/usr/bin/env python3
"""Compare complete external weather-wallet histories on a fixed cashflow basis.

Inputs are outputs from ``research_external_wallet_full_ladder_history_v1.py``.
The comparison keeps each city x target_date ladder intact and uses only
cashflow-complete resolved portfolios.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import statistics
from typing import Any

from research_external_wallet_full_ladder_history_v1 import target_date_block_bootstrap


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def percentile(values: list[float], probability: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def read_events(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def aggregate_period(rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
    pnl = sum(float(row["public_cashflow"]) for row in rows)
    cost = sum(float(row["buy_cost"]) for row in rows)
    return {
        "events": len(rows),
        "pnl": pnl,
        "buy_cost": cost,
        "turnover_roi": ratio(pnl, cost),
    }


def date_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in events:
        grouped.setdefault(str(row["target_date"]), []).append(row)
    output = []
    for target_date, rows in sorted(grouped.items()):
        aggregate = aggregate_period(rows)
        output.append(
            {
                "target_date": target_date,
                **aggregate,
            }
        )
    return output


def max_drawdown(rows: list[dict[str, Any]]) -> float:
    cumulative = 0.0
    peak = 0.0
    drawdown = 0.0
    for row in rows:
        cumulative += float(row["pnl"])
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    return drawdown


def split_half(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    midpoint = len(rows) // 2
    return rows[:midpoint], rows[midpoint:]


def period_from_dates(rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
    pnl = sum(float(row["pnl"]) for row in rows)
    cost = sum(float(row["buy_cost"]) for row in rows)
    return {
        "target_dates": len(rows),
        "pnl": pnl,
        "buy_cost": cost,
        "turnover_roi": ratio(pnl, cost),
    }


def positive_pnl_concentration(
    rows: list[dict[str, Any]], count: int
) -> float | None:
    positive = sorted(
        (float(row["pnl"]) for row in rows if float(row["pnl"]) > 0),
        reverse=True,
    )
    total = sum(positive)
    return ratio(sum(positive[:count]), total)


def horizon_profile(offset_cost_share: dict[str, Any]) -> dict[str, Any]:
    shares = {str(key): float(value or 0) for key, value in offset_cost_share.items()}
    d0 = shares.get("0", 0.0)
    d1 = shares.get("-1", 0.0)
    d2_or_earlier = sum(
        share
        for offset, share in shares.items()
        if offset.lstrip("-").isdigit() and int(offset) <= -2
    )
    later_or_unknown = max(0.0, 1.0 - d0 - d1 - d2_or_earlier)
    buckets = {
        "d2_or_earlier": d2_or_earlier,
        "d1": d1,
        "d0": d0,
        "later_or_unknown": later_or_unknown,
    }
    dominant = max(buckets, key=buckets.get)
    return {
        "buy_cost_share": buckets,
        "dominant_horizon": dominant,
        "dominant_horizon_share": buckets[dominant],
    }


def event_horizon_bucket(row: dict[str, Any]) -> str:
    raw = str(row.get("first_entry_day_offset") or "").strip()
    try:
        offset = int(float(raw))
    except ValueError:
        return "unknown"
    if offset <= -2:
        return "d2_or_earlier"
    if offset == -1:
        return "d1"
    if offset == 0:
        return "d0"
    return "later"


def event_slice_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = sum(float(row["public_cashflow"]) for row in rows)
    cost = sum(float(row["buy_cost"]) for row in rows)
    prepared = [
        {
            "cashflow_complete": True,
            "target_date": str(row["target_date"]),
            "public_cashflow": float(row["public_cashflow"]),
            "buy_cost": float(row["buy_cost"]),
        }
        for row in rows
    ]
    bootstrap = target_date_block_bootstrap(prepared) if prepared else {"ci95": [None, None]}
    return {
        "events": len(rows),
        "independent_target_dates": len({str(row["target_date"]) for row in rows}),
        "buy_cost": cost,
        "pnl": pnl,
        "turnover_roi": ratio(pnl, cost),
        "target_date_block_ci95": bootstrap["ci95"],
    }


def grouped_event_summaries(
    rows: list[dict[str, Any]], key_fn: Any
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(key_fn(row)), []).append(row)
    return {
        key: event_slice_summary(group)
        for key, group in sorted(grouped.items())
    }


def monthly_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in events:
        grouped.setdefault(str(row["target_date"])[:7], []).append(row)
    return [
        {"month": month, **aggregate_period(rows)}
        for month, rows in sorted(grouped.items())
    ]


def dominant_expression(counts: dict[str, int]) -> tuple[str, float | None]:
    total = sum(int(value) for value in counts.values())
    if not total:
        return "unknown", None
    label, count = max(counts.items(), key=lambda item: int(item[1]))
    return label, int(count) / total


def classify_copyability(
    *,
    complete_events: int,
    target_dates: int,
    median_event_buy_cost: float | None,
    p90_event_buy_cost: float | None,
    median_transactions: float | None,
    median_sessions: float | None,
    median_span_minutes: float | None,
    near_binary_buy_share: float | None,
    sell_event_share: float | None,
    median_first_buy_to_first_sell_hours: float | None,
    sell_proceeds_share_ge_95c: float | None,
    mean_events_per_target_date: float | None,
    dominant_expression_label: str,
    dominant_expression_share: float | None,
) -> dict[str, Any]:
    blockers: list[str] = []
    if complete_events < 20 or target_dates < 15:
        blockers.append("insufficient_independent_history")
    if (median_transactions or 0) > 10 or (median_sessions or 0) > 4:
        blockers.append("high_frequency_or_many_staged_entries")
    if (median_span_minutes or 0) > 360:
        blockers.append("long_active_execution_window")
    if (median_event_buy_cost or 0) > 300 or (p90_event_buy_cost or 0) > 1_000:
        blockers.append("large_event_capital_requirement")
    if near_binary_buy_share is None:
        blockers.append("near_binary_price_diagnostics_missing")
    elif near_binary_buy_share > 0.25:
        blockers.append("near_binary_buy_dependence")
    if (mean_events_per_target_date or 0) > 12:
        blockers.append("high_daily_market_breadth")
    if (
        (sell_event_share or 0) >= 0.75
        and median_first_buy_to_first_sell_hours is not None
        and median_first_buy_to_first_sell_hours <= 1
        and (sell_proceeds_share_ge_95c or 0) < 0.8
    ):
        blockers.append("subhour_repricing_timing_dependence")

    if (median_transactions or 0) <= 2 and (median_sessions or 0) <= 2:
        execution_style = "low_frequency"
    elif (median_transactions or 0) <= 10 and (median_sessions or 0) <= 4:
        execution_style = "moderate_frequency"
    else:
        execution_style = "high_frequency_or_staged"

    if (median_event_buy_cost or 0) <= 100 and (p90_event_buy_cost or 0) <= 500:
        capital_style = "small"
    elif (median_event_buy_cost or 0) <= 300 and (p90_event_buy_cost or 0) <= 1_000:
        capital_style = "moderate"
    else:
        capital_style = "large_or_concentrated"

    return {
        "screen_purpose": "research prioritization only; not a strategy/live gate",
        "copyable_for_our_execution": not blockers,
        "blockers": blockers,
        "execution_style": execution_style,
        "capital_style": capital_style,
        "events_per_target_date": mean_events_per_target_date,
        "dominant_expression": dominant_expression_label,
        "dominant_expression_share": dominant_expression_share,
    }


def summarize(label: str, analysis_dir: Path) -> dict[str, Any]:
    summary = json.loads((analysis_dir / "summary.json").read_text(encoding="utf-8"))
    raw_events = read_events(analysis_dir / "event_portfolios.csv")
    city_rows = read_events(analysis_dir / "city_summary.csv")
    events = [
        row
        for row in raw_events
        if as_bool(row.get("cashflow_complete")) and float(row.get("buy_cost") or 0) > 0
    ]
    dates = date_rows(events)
    months = monthly_rows(events)
    event_rois = [
        float(row["public_cashflow"]) / float(row["buy_cost"])
        for row in events
    ]
    date_rois = [
        float(row["turnover_roi"])
        for row in dates
        if row["turnover_roi"] is not None
    ]
    month_rois = [
        float(row["turnover_roi"])
        for row in months
        if row["turnover_roi"] is not None
    ]
    early, late = split_half(dates)
    without_top_five = sorted(dates, key=lambda row: float(row["pnl"]))[:-5]
    lifetime = period_from_dates(dates)
    bootstrap = summary["settlement_behavior"]["target_date_block_bootstrap"]
    hour_timing = summary["entry_fill_timing"]
    sell_behavior = summary["sell_behavior"]
    target_day_share = (
        hour_timing["buy_cost_share_by_target_day_offset"].get("0") or 0.0
    )
    horizons = horizon_profile(hour_timing["buy_cost_share_by_target_day_offset"])
    portfolio = summary["portfolio_summary"]
    event_buy_costs = [float(row["buy_cost"]) for row in events]
    expression_label, expression_share = dominant_expression(
        summary["expression_counts"]
    )
    median_event_buy_cost = percentile(event_buy_costs, 0.50)
    p90_event_buy_cost = percentile(event_buy_costs, 0.90)
    median_transactions = portfolio["unique_buy_transactions"]["median"]
    median_sessions = portfolio["buy_sessions_gap_gt_5m"]["median"]
    median_span_minutes = portfolio["buy_span_minutes"]["median"]
    # Pre-2026-08 full-ladder summaries did not materialize these three price
    # diagnostics.  Keep the historical wallet in the comparison with an
    # explicit null instead of either failing the cohort or silently treating
    # the missing share as zero.
    near_binary_buy_share = portfolio.get("buy_cost_share_ge_95c")
    ranked_cities = sorted(
        (
            {
                "city": str(row["label"]),
                "buy_cost": float(row.get("buy_cost") or 0),
                "events": int(float(row.get("events") or 0)),
                "turnover_roi": (
                    float(row["turnover_roi"])
                    if row.get("turnover_roi") not in (None, "")
                    else None
                ),
            }
            for row in city_rows
        ),
        key=lambda row: float(row["buy_cost"]),
        reverse=True,
    )
    city_total_cost = sum(float(row["buy_cost"]) for row in ranked_cities)
    city_shares = [
        float(row["buy_cost"]) / city_total_cost
        for row in ranked_cities
        if city_total_cost
    ]
    return {
        "label": label,
        "wallet": summary["wallet"],
        "snapshot_id": summary["snapshot_id"],
        "source": str(analysis_dir),
        "coverage": {
            "activity_rows": summary["coverage"]["activity_rows"],
            "events_total": summary["coverage"]["city_target_date_portfolios"],
            "cashflow_complete_events": len(events),
            "cashflow_complete_event_share": ratio(
                len(events),
                summary["coverage"]["city_target_date_portfolios"],
            ),
            "independent_target_dates": len(dates),
            "cities": summary["coverage"]["cities"],
            "metadata_incomplete_portfolios": summary["coverage"][
                "metadata_incomplete_portfolios"
            ],
            "target_date_min": dates[0]["target_date"] if dates else None,
            "target_date_max": dates[-1]["target_date"] if dates else None,
        },
        "lifetime": lifetime,
        "average_roi": {
            "mean_event_roi": statistics.fmean(event_rois) if event_rois else None,
            "median_event_roi": statistics.median(event_rois) if event_rois else None,
            "mean_target_date_roi": statistics.fmean(date_rois) if date_rois else None,
            "median_target_date_roi": statistics.median(date_rois) if date_rois else None,
            "mean_active_month_roi": statistics.fmean(month_rois) if month_rois else None,
            "median_active_month_roi": statistics.median(month_rois) if month_rois else None,
        },
        "stability": {
            "target_date_block_ci95": bootstrap["ci95"],
            "positive_target_date_share": ratio(
                sum(float(row["pnl"]) > 0 for row in dates),
                len(dates),
            ),
            "positive_event_share": ratio(
                sum(float(row["public_cashflow"]) > 0 for row in events),
                len(events),
            ),
            "max_drawdown_by_target_date": max_drawdown(dates),
            "early_half": period_from_dates(early),
            "late_half": period_from_dates(late),
            "latest_30_target_dates": period_from_dates(dates[-30:]),
            "without_top_five_pnl_dates": period_from_dates(without_top_five),
            "top_one_positive_date_pnl_share": positive_pnl_concentration(dates, 1),
            "top_five_positive_date_pnl_share": positive_pnl_concentration(dates, 5),
            "positive_active_month_share": ratio(
                sum(float(row["pnl"]) > 0 for row in months),
                len(months),
            ),
        },
        "replication_inputs": {
            "median_event_buy_cost": median_event_buy_cost,
            "p90_event_buy_cost": p90_event_buy_cost,
            "buy_price_cost_weighted": portfolio.get("buy_price_cost_weighted"),
            "buy_cost_share_ge_95c": portfolio.get("buy_cost_share_ge_95c"),
            "buy_cost_share_ge_99c": portfolio.get("buy_cost_share_ge_99c"),
            "yes_buy_cost_share": portfolio["yes_buy_cost_share"],
            "buy_cost_share_by_target_day_offset": hour_timing[
                "buy_cost_share_by_target_day_offset"
            ],
            "target_day_buy_cost_share": target_day_share,
            "horizon_profile": horizons,
            "sell_event_share": portfolio["sell_event_share"],
            "settlement_without_sell_share": portfolio[
                "settlement_without_sell_share"
            ],
            "sell_proceeds_over_buy_cost": sell_behavior[
                "sell_proceeds_over_buy_cost"
            ],
            "sell_price_cost_weighted": sell_behavior[
                "sell_price_cost_weighted"
            ],
            "median_buy_sessions_gap_gt_5m": portfolio[
                "buy_sessions_gap_gt_5m"
            ]["median"],
            "median_buy_span_minutes": portfolio["buy_span_minutes"]["median"],
            "median_unique_buy_transactions": portfolio[
                "unique_buy_transactions"
            ]["median"],
            "median_positive_yes_brackets": portfolio[
                "yes_positive_brackets"
            ]["median"],
            "median_first_buy_to_first_sell_hours": sell_behavior[
                "first_buy_to_first_sell_hours"
            ]["median"],
            "sell_proceeds_share_ge_95c": sell_behavior[
                "sell_proceeds_share_ge_95c"
            ],
            "sell_proceeds_share_ge_99c": sell_behavior[
                "sell_proceeds_share_ge_99c"
            ],
            "exit_style_counts": summary["exit_style_counts"],
            "expression_counts": summary["expression_counts"],
            "dominant_expression": expression_label,
            "dominant_expression_share": expression_share,
            "contiguous_yes_strip_share": portfolio["contiguous_yes_strip_share"],
            "median_first_buy_local_hour": portfolio["first_buy_local_hour"][
                "median"
            ],
            "median_cost_weighted_buy_local_hour": portfolio[
                "cost_weighted_buy_local_hour"
            ]["median"],
        },
        "copyability": classify_copyability(
            complete_events=len(events),
            target_dates=len(dates),
            median_event_buy_cost=median_event_buy_cost,
            p90_event_buy_cost=p90_event_buy_cost,
            median_transactions=median_transactions,
            median_sessions=median_sessions,
            median_span_minutes=median_span_minutes,
            near_binary_buy_share=near_binary_buy_share,
            sell_event_share=portfolio["sell_event_share"],
            median_first_buy_to_first_sell_hours=sell_behavior[
                "first_buy_to_first_sell_hours"
            ]["median"],
            sell_proceeds_share_ge_95c=sell_behavior[
                "sell_proceeds_share_ge_95c"
            ],
            mean_events_per_target_date=ratio(len(events), len(dates)),
            dominant_expression_label=expression_label,
            dominant_expression_share=expression_share,
        ),
        "strategy_breakdowns": {
            "by_expression": grouped_event_summaries(
                events, lambda row: row.get("expression") or "unknown"
            ),
            "by_entry_horizon": grouped_event_summaries(events, event_horizon_bucket),
        },
        "city_profile": {
            "top_city": ranked_cities[0]["city"] if ranked_cities else None,
            "top_city_buy_cost_share": city_shares[0] if city_shares else None,
            "top_3_buy_cost_share": sum(city_shares[:3]) if city_shares else None,
            "effective_city_count": (
                1 / sum(value * value for value in city_shares)
                if city_shares
                else None
            ),
            "top_cities": [
                {**row, "buy_cost_share": city_shares[index]}
                for index, row in enumerate(ranked_cities[:5])
            ],
        },
        "date_rows": dates,
        "month_rows": months,
    }


def parse_run(value: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or not label or not raw_path:
        raise argparse.ArgumentTypeError("--run must be LABEL=/path/to/analysis")
    return label, Path(raw_path).expanduser().resolve()


def runs_from_batch_manifest(path: Path) -> list[tuple[str, Path]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    runs: list[tuple[str, Path]] = []
    for row in payload.get("results") or []:
        if row.get("status") != "complete":
            continue
        wallet = str(row["wallet"]).lower()
        runs.append((wallet, Path(str(row["analysis"])).resolve()))
    if not runs:
        raise ValueError(f"batch manifest has no completed runs: {path}")
    return runs


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "label",
        "wallet",
        "target_date_min",
        "target_date_max",
        "events",
        "target_dates",
        "buy_cost",
        "median_event_buy_cost",
        "p90_event_buy_cost",
        "pnl",
        "lifetime_turnover_roi",
        "mean_target_date_roi",
        "median_target_date_roi",
        "ci95_low",
        "ci95_high",
        "late_half_roi",
        "latest_30_target_dates_roi",
        "without_top_five_pnl_dates_roi",
        "max_drawdown_by_target_date",
        "yes_buy_cost_share",
        "target_day_buy_cost_share",
        "sell_event_share",
        "median_first_buy_to_first_sell_hours",
        "median_buy_sessions_gap_gt_5m",
        "median_buy_span_minutes",
        "median_unique_buy_transactions",
        "sell_proceeds_share_ge_95c",
        "sell_proceeds_share_ge_99c",
        "buy_price_cost_weighted",
        "buy_cost_share_ge_95c",
        "buy_cost_share_ge_99c",
        "dominant_expression",
        "dominant_expression_share",
        "execution_style",
        "capital_style",
        "copyable_for_our_execution",
        "copyability_blockers",
        "top_city",
        "top_city_buy_cost_share",
        "top_3_buy_cost_share",
        "effective_city_count",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            ci = row["stability"]["target_date_block_ci95"]
            writer.writerow(
                {
                    "label": row["label"],
                    "wallet": row["wallet"],
                    "target_date_min": row["coverage"]["target_date_min"],
                    "target_date_max": row["coverage"]["target_date_max"],
                    "events": row["coverage"]["cashflow_complete_events"],
                    "target_dates": row["coverage"]["independent_target_dates"],
                    "buy_cost": row["lifetime"]["buy_cost"],
                    "median_event_buy_cost": row["replication_inputs"][
                        "median_event_buy_cost"
                    ],
                    "p90_event_buy_cost": row["replication_inputs"][
                        "p90_event_buy_cost"
                    ],
                    "pnl": row["lifetime"]["pnl"],
                    "lifetime_turnover_roi": row["lifetime"]["turnover_roi"],
                    "mean_target_date_roi": row["average_roi"]["mean_target_date_roi"],
                    "median_target_date_roi": row["average_roi"][
                        "median_target_date_roi"
                    ],
                    "ci95_low": ci[0],
                    "ci95_high": ci[1],
                    "late_half_roi": row["stability"]["late_half"]["turnover_roi"],
                    "latest_30_target_dates_roi": row["stability"][
                        "latest_30_target_dates"
                    ]["turnover_roi"],
                    "without_top_five_pnl_dates_roi": row["stability"][
                        "without_top_five_pnl_dates"
                    ]["turnover_roi"],
                    "max_drawdown_by_target_date": row["stability"][
                        "max_drawdown_by_target_date"
                    ],
                    "yes_buy_cost_share": row["replication_inputs"][
                        "yes_buy_cost_share"
                    ],
                    "target_day_buy_cost_share": row["replication_inputs"][
                        "target_day_buy_cost_share"
                    ],
                    "sell_event_share": row["replication_inputs"]["sell_event_share"],
                    "median_first_buy_to_first_sell_hours": row[
                        "replication_inputs"
                    ]["median_first_buy_to_first_sell_hours"],
                    "median_buy_sessions_gap_gt_5m": row["replication_inputs"][
                        "median_buy_sessions_gap_gt_5m"
                    ],
                    "median_buy_span_minutes": row["replication_inputs"][
                        "median_buy_span_minutes"
                    ],
                    "median_unique_buy_transactions": row["replication_inputs"][
                        "median_unique_buy_transactions"
                    ],
                    "sell_proceeds_share_ge_95c": row["replication_inputs"][
                        "sell_proceeds_share_ge_95c"
                    ],
                    "sell_proceeds_share_ge_99c": row["replication_inputs"][
                        "sell_proceeds_share_ge_99c"
                    ],
                    "buy_price_cost_weighted": row["replication_inputs"][
                        "buy_price_cost_weighted"
                    ],
                    "buy_cost_share_ge_95c": row["replication_inputs"][
                        "buy_cost_share_ge_95c"
                    ],
                    "buy_cost_share_ge_99c": row["replication_inputs"][
                        "buy_cost_share_ge_99c"
                    ],
                    "dominant_expression": row["copyability"][
                        "dominant_expression"
                    ],
                    "dominant_expression_share": row["copyability"][
                        "dominant_expression_share"
                    ],
                    "execution_style": row["copyability"]["execution_style"],
                    "capital_style": row["copyability"]["capital_style"],
                    "copyable_for_our_execution": row["copyability"][
                        "copyable_for_our_execution"
                    ],
                    "copyability_blockers": ",".join(
                        row["copyability"]["blockers"]
                    ),
                    "top_city": row["city_profile"]["top_city"],
                    "top_city_buy_cost_share": row["city_profile"][
                        "top_city_buy_cost_share"
                    ],
                    "top_3_buy_cost_share": row["city_profile"][
                        "top_3_buy_cost_share"
                    ],
                    "effective_city_count": row["city_profile"][
                        "effective_city_count"
                    ],
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="append", type=parse_run, default=[])
    parser.add_argument("--batch-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    runs = list(args.run)
    if args.batch_manifest:
        runs.extend(runs_from_batch_manifest(args.batch_manifest.resolve()))
    if not runs:
        parser.error("at least one --run or --batch-manifest is required")
    comparisons = [summarize(label, path) for label, path in runs]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "comparison.json").write_text(
        json.dumps(
            {
                "schema_version": "external_wallet_performance_compare_v2",
                "grain": "cashflow_complete_city_x_target_date_ladder",
                "fee_basis": (
                    "public activity usdcSize cashflow; actual fills include observed "
                    "transaction cash effects"
                ),
                "wallets": comparisons,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "wallets": len(comparisons),
                "output": str(output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
