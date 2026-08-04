#!/usr/bin/env python3
"""Infer whether 0x43cb behaves like global or city-specific Tmax models.

This is an action-policy fingerprint study over selected public wallet fills. It
does not observe the wallet's private forecasts, features, unfilled orders, or
model code, so the output is architecture evidence rather than identification.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date
import json
import math
from pathlib import Path
from typing import Any, Callable

import numpy as np
from scipy import stats


METRICS = {
    "first_buy_local_hour": "first_buy_local_hour",
    "strip_width": "yes_positive_brackets",
    "base_fraction": "yes_base_share_fraction",
    "weighted_center": "yes_weighted_ladder_center",
    "buy_span_minutes": "buy_span_minutes",
    "log1p_buy_transactions": "unique_buy_transactions",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolios", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-period-events", type=int, default=10)
    return parser.parse_args()


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def median(values: list[float]) -> float | None:
    return float(np.median(values)) if values else None


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            target = date.fromisoformat(raw["target_date"])
            row: dict[str, Any] = {
                **raw,
                "target": target,
                "target_date_key": target.isoformat(),
                "month": target.strftime("%Y-%m"),
            }
            for metric, column in METRICS.items():
                value = as_float(raw.get(column))
                if metric == "log1p_buy_transactions" and value is not None:
                    value = math.log1p(value)
                row[metric] = value
            row["buy_cost_value"] = as_float(raw.get("buy_cost")) or 0.0
            row["pnl_value"] = as_float(raw.get("economic_pnl_reconstructed"))
            row["cashflow_complete_value"] = raw.get("cashflow_complete") == "True"
            rows.append(row)
    return rows


def design_matrix(
    rows: list[dict[str, Any]],
    categorical: list[str],
) -> np.ndarray:
    columns: list[np.ndarray] = [np.ones(len(rows), dtype=float)]
    for field in categorical:
        levels = sorted({str(row[field]) for row in rows})
        for level in levels[1:]:
            columns.append(
                np.array([str(row[field]) == level for row in rows], dtype=float)
            )
    return np.column_stack(columns)


def fit_r2(rows: list[dict[str, Any]], metric: str, fields: list[str]) -> dict[str, Any]:
    usable = [row for row in rows if row[metric] is not None]
    y = np.array([float(row[metric]) for row in usable], dtype=float)
    x = design_matrix(usable, fields)
    beta, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
    residual = y - x @ beta
    sse = float(residual @ residual)
    centered = y - y.mean()
    sst = float(centered @ centered)
    return {
        "n": len(usable),
        "parameters": int(x.shape[1]),
        "sse": sse,
        "r2": 1.0 - sse / sst if sst else 0.0,
    }


def partial_f(full: dict[str, Any], reduced: dict[str, Any]) -> dict[str, float | None]:
    df_num = int(full["parameters"] - reduced["parameters"])
    df_den = int(full["n"] - full["parameters"])
    if df_num <= 0 or df_den <= 0 or float(full["sse"]) <= 0:
        return {"f": None, "p": None}
    numerator = (float(reduced["sse"]) - float(full["sse"])) / df_num
    denominator = float(full["sse"]) / df_den
    f_value = numerator / denominator
    return {"f": f_value, "p": float(stats.f.sf(f_value, df_num, df_den))}


def categorical_decomposition(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    intercept = fit_r2(rows, metric, [])
    month = fit_r2(rows, metric, ["month"])
    city = fit_r2(rows, metric, ["city"])
    combined = fit_r2(rows, metric, ["month", "city"])
    city_test = partial_f(combined, month)
    month_test = partial_f(combined, city)
    return {
        "n": combined["n"],
        "r2_month_only": month["r2"],
        "r2_city_only": city["r2"],
        "r2_month_plus_city": combined["r2"],
        "incremental_city_r2_after_month": combined["r2"] - month["r2"],
        "incremental_month_r2_after_city": combined["r2"] - city["r2"],
        "partial_city_f": city_test["f"],
        "partial_city_p": city_test["p"],
        "partial_month_f": month_test["f"],
        "partial_month_p": month_test["p"],
        "intercept_sse": intercept["sse"],
    }


def date_city_decomposition(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    date_only = fit_r2(rows, metric, ["target_date_key"])
    city_only = fit_r2(rows, metric, ["city"])
    combined = fit_r2(rows, metric, ["target_date_key", "city"])
    city_test = partial_f(combined, date_only)
    date_test = partial_f(combined, city_only)
    return {
        "n": combined["n"],
        "r2_target_date_only": date_only["r2"],
        "r2_city_only": city_only["r2"],
        "r2_target_date_plus_city": combined["r2"],
        "incremental_city_r2_after_target_date": combined["r2"] - date_only["r2"],
        "incremental_target_date_r2_after_city": combined["r2"] - city_only["r2"],
        "partial_city_f": city_test["f"],
        "partial_city_p": city_test["p"],
        "partial_target_date_f": date_test["f"],
        "partial_target_date_p": date_test["p"],
    }


def period_name(target: date) -> str | None:
    if date(2026, 3, 1) <= target <= date(2026, 4, 30):
        return "early_2026_03_04"
    if date(2026, 6, 1) <= target <= date(2026, 7, 31):
        return "late_2026_06_07"
    return None


def cross_period_stability(
    rows: list[dict[str, Any]], metric: str, min_events: int
) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        period = period_name(row["target"])
        value = row[metric]
        if period is None or value is None:
            continue
        key = (str(row["city"]), period)
        grouped[key].append(float(value))
        counts[key] += 1
    cities = sorted(
        city
        for city in {key[0] for key in grouped}
        if counts[(city, "early_2026_03_04")] >= min_events
        and counts[(city, "late_2026_06_07")] >= min_events
    )
    early = [median(grouped[(city, "early_2026_03_04")]) for city in cities]
    late = [median(grouped[(city, "late_2026_06_07")]) for city in cities]
    if len(cities) >= 3:
        result = stats.spearmanr(early, late)
        rho = float(result.statistic)
        p_value = float(result.pvalue)
    else:
        rho = p_value = None
    return {
        "common_cities": len(cities),
        "min_events_each_period": min_events,
        "spearman_rho": rho,
        "spearman_p": p_value,
        "cities": cities,
        "early_medians": dict(zip(cities, early)),
        "late_medians": dict(zip(cities, late)),
    }


def city_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["city"])].append(row)
    result: list[dict[str, Any]] = []
    for city, city_rows in grouped.items():
        settled = [
            row
            for row in city_rows
            if row["cashflow_complete_value"] and row["pnl_value"] is not None
        ]
        cost = sum(float(row["buy_cost_value"]) for row in settled)
        pnl = sum(float(row["pnl_value"]) for row in settled)
        item: dict[str, Any] = {
            "city": city,
            "events": len(city_rows),
            "target_dates": len({row["target"] for row in city_rows}),
            "buy_cost": sum(float(row["buy_cost_value"]) for row in city_rows),
            "settled_events": len(settled),
            "settled_roi": pnl / cost if cost else None,
            "yes_strip_share": sum(row["expression"] == "yes_strip" for row in city_rows)
            / len(city_rows),
            "contiguous_share": sum(row["yes_strip_contiguous"] == "True" for row in city_rows)
            / len(city_rows),
        }
        for metric in METRICS:
            values = [float(row[metric]) for row in city_rows if row[metric] is not None]
            item[f"median_{metric}"] = median(values)
        result.append(item)
    return sorted(result, key=lambda item: (-item["events"], item["city"]))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    rows = load_rows(args.portfolios)
    cities = city_summary(rows)
    total_cost = sum(float(row["buy_cost_value"]) for row in rows)
    cost_shares = [float(city["buy_cost"]) / total_cost for city in cities if total_cost]
    decomposition = {
        metric: categorical_decomposition(rows, metric) for metric in METRICS
    }
    date_decomposition = {
        metric: date_city_decomposition(rows, metric) for metric in METRICS
    }
    stability = {
        metric: cross_period_stability(rows, metric, args.min_period_events)
        for metric in METRICS
    }
    summary = {
        "schema_version": "wallet_43cb_city_model_architecture_v1",
        "source": str(args.portfolios.resolve()),
        "grain": "selected_public_wallet_city_x_target_date_portfolio",
        "coverage": {
            "portfolios": len(rows),
            "cities": len(cities),
            "target_dates": len({row["target"] for row in rows}),
            "first_target_date": min(row["target"] for row in rows).isoformat(),
            "last_target_date": max(row["target"] for row in rows).isoformat(),
        },
        "city_concentration": {
            "top_city_cost_share": max(cost_shares) if cost_shares else None,
            "top_5_city_cost_share": sum(sorted(cost_shares, reverse=True)[:5]),
            "cost_hhi": sum(share * share for share in cost_shares),
            "effective_city_count": 1.0 / sum(share * share for share in cost_shares)
            if cost_shares
            else None,
        },
        "categorical_variance_decomposition": decomposition,
        "target_date_city_variance_decomposition": date_decomposition,
        "cross_period_city_rank_stability": stability,
        "interpretation_contract": {
            "supports": "persistent action-policy fingerprints after controlling for calendar month",
            "cannot_identify": [
                "private model class or training code",
                "unfilled and cancelled orders",
                "forecast/source state at every fill",
                "whether a city effect comes from model calibration, market liquidity, or execution policy",
            ],
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_csv(args.output / "city_fingerprints.csv", cities)
    print(
        json.dumps(
            {
                "status": "complete",
                "portfolios": len(rows),
                "cities": len(cities),
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
