#!/usr/bin/env python3
"""Diagnose HeadA would-live source differences and candidate loss filters.

The fixed denominator is the first would-live entry per signal from the current
zero-notional shadow journal. Execution is evaluated as taker at the captured
fresh ask plus the official Weather fee. Candidate filters are diagnostics:
they are evaluated separately on train (through 2026-07-23) and frozen forward
(2026-07-24 onward), and do not mutate the production selector.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
ENTRIES_DEFAULT = ROOT / "docs/analysis/2026-07/generated/heada_would_live_shadow_v2/entries.csv"
JOURNAL_DEFAULT = ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/would_live_entries.jsonl"
SHADOW_DECISIONS_DEFAULT = (
    ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/shadow_decisions.jsonl"
)
DB_DEFAULT = ROOT / "runtime/weather.db"
OUT_DIR_DEFAULT = ROOT / "docs/analysis/2026-07/generated/heada_would_live_filter_diagnostic_v1"
HISTORICAL_DEFAULT = (
    ROOT / "docs/analysis/2026-07/generated/low_price_yes_forecast_source_calibration_v1/candidate_rows.csv"
)
TRAIN_END = "2026-07-23"
FRESH_BOOK_FAILURE_START = "2026-07-25T06:26:55Z"
RNG_SEED = 20260728


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=Path, default=ENTRIES_DEFAULT)
    parser.add_argument("--journal", type=Path, default=JOURNAL_DEFAULT)
    parser.add_argument("--shadow-decisions", type=Path, default=SHADOW_DECISIONS_DEFAULT)
    parser.add_argument("--db", type=Path, default=DB_DEFAULT)
    parser.add_argument("--historical-rows", type=Path, default=HISTORICAL_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR_DEFAULT)
    parser.add_argument("--bootstrap-samples", type=int, default=20_000)
    return parser.parse_args()


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def parse_bounds(value: Any) -> tuple[float | None, float | None]:
    text = str(value or "").strip().replace("−", "-").replace("°", "")
    if not text:
        return (None, None)
    if "-" in text and not text.startswith("-"):
        left, right = text.split("-", 1)
        try:
            return (min(float(left), float(right)), max(float(left), float(right)))
        except ValueError:
            return (None, None)
    number = text.lower().replace("or higher", "").replace("or below", "").replace("+", "").strip()
    try:
        parsed = float(number)
    except ValueError:
        return (None, None)
    return (parsed, parsed)


def load_winning_brackets(db_path: Path) -> dict[tuple[str, str], str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    rows = conn.execute(
        """
        SELECT city, target_date, bracket
        FROM settlement_outcomes
        WHERE settlement_status = 'settled' AND final_price >= 0.999
        """
    ).fetchall()
    conn.close()
    return {(str(city).lower(), str(target_date)): str(bracket) for city, target_date, bracket in rows}


def bucket(value: float | None, cuts: list[float], labels: list[str]) -> str:
    if value is None:
        return "missing"
    for cut, label in zip(cuts, labels):
        if value <= cut:
            return label
    return labels[-1]


def enrich_rows(entries: list[dict[str, Any]], journal: list[dict[str, Any]], winners: dict[tuple[str, str], str]) -> list[dict[str, Any]]:
    raw_by_signal: dict[str, dict[str, Any]] = {}
    for row in journal:
        signal_id = str(row.get("signal_id") or "")
        if signal_id and bool(row.get("would_live_entry")):
            raw_by_signal.setdefault(signal_id, row)

    output: list[dict[str, Any]] = []
    for entry in entries:
        if str(entry.get("intended_hot_tail_dist_gt0")).lower() != "true":
            continue
        if str(entry.get("settled")).lower() != "true":
            continue
        raw = raw_by_signal.get(str(entry.get("signal_id") or ""), {})
        score = raw.get("score_dist_features") or {}
        forecast_source = str(entry.get("forecast_source") or "").lower()
        forecast_model = "ecmwf" if "ecmwf" in forecast_source else "gfs" if "gfs" in forecast_source else "other"
        target_low, target_high = parse_bounds(entry.get("bracket"))
        winning_bracket = winners.get((str(entry["city"]).lower(), str(entry["target_date"])), "")
        winner_low, winner_high = parse_bounds(winning_bracket)
        forecast_native = finite(entry.get("forecast_max_native"))
        win = int(float(entry["win"]))
        if win:
            miss_direction = "ticket_won"
        elif None not in (target_low, target_high, winner_low, winner_high) and winner_high < target_low:
            miss_direction = "settled_below_ticket"
        elif None not in (target_low, target_high, winner_low, winner_high) and winner_low > target_high:
            miss_direction = "settled_above_ticket"
        else:
            miss_direction = "different_or_unparsed"
        if None in (winner_low, winner_high, forecast_native):
            winner_vs_forecast = "unparsed"
        elif winner_low > forecast_native:
            winner_vs_forecast = "winner_above_forecast"
        elif winner_high < forecast_native:
            winner_vs_forecast = "winner_below_forecast"
        else:
            winner_vs_forecast = "forecast_inside_winning_bracket"

        best_ask = float(entry["best_ask"])
        model_p = float(entry["model_p_yes"])
        raw_dist = finite(score.get("raw_dist_br"))
        adj_dist = finite(score.get("adj_dist_p50_br"))
        peak_delta = finite(raw.get("forecast_peak_delta_hours_local"))
        telemetry_ok = str(raw.get("tail_telemetry_status") or "") == "ok"
        native_dist = finite(entry.get("forecast_to_bracket_low_native"))
        lattice_width = 2.0 if str(entry.get("unit") or "").upper() == "F" else 1.0
        distance_lattice_steps = None if native_dist is None else native_dist / lattice_width
        enriched = {
            **entry,
            "win": win,
            "forecast_model": forecast_model,
            "winning_bracket": winning_bracket,
            "miss_direction": miss_direction,
            "winner_vs_forecast": winner_vs_forecast,
            "distance_lattice_steps": distance_lattice_steps,
            "tail_telemetry_status": raw.get("tail_telemetry_status"),
            "tail_telemetry_error": raw.get("tail_telemetry_error"),
            # The production checkout missed its bias artifact for most rows.
            # Do not treat the runner's default-filled score fields as evidence.
            "raw_dist_br": raw_dist if telemetry_ok else None,
            "adj_dist_p50_br": adj_dist if telemetry_ok else None,
            "bias_mean_asof": finite(score.get("bias_mean_asof")) if telemetry_ok else None,
            "bias_p90_asof": finite(score.get("bias_p90_asof")) if telemetry_ok else None,
            "hot_tail_pct_asof": finite(score.get("hot_tail_pct_asof")) if telemetry_ok else None,
            "cold_tail_pct_asof": finite(score.get("cold_tail_pct_asof")) if telemetry_ok else None,
            "score_dist_probability": finite(raw.get("score_dist_probability")) if telemetry_ok else None,
            "fresh_spread": finite(raw.get("fresh_spread")),
            "forecast_peak_delta_hours_local": peak_delta,
            "decision_snapshot_age_hours": finite(raw.get("decision_snapshot_age_hours")),
            "edge_at_fresh_ask": model_p - best_ask,
            "ask_band": bucket(best_ask, [0.08, 0.11, 0.14, math.inf], ["05_08c", "08_11c", "11_14c", "14_20c"]),
            "distance_lattice_band": bucket(
                distance_lattice_steps,
                [0.5, 1.0, 1.5, math.inf],
                ["0_0p5", "0p5_1", "1_1p5", "gt1p5"],
            ),
            "spread_band": bucket(
                finite(raw.get("fresh_spread")),
                [0.01, 0.02, math.inf],
                ["le1c", "1_2c", "gt2c"],
            ),
            "peak_clock_band": bucket(peak_delta, [-6.0, 0.0, math.inf], ["pre_peak_gt6h", "pre_peak_le6h", "after_peak_clock"]),
            "period": "train" if str(entry["target_date"]) <= TRAIN_END else "forward",
        }
        output.append(enriched)
    return output


def normalize_historical_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        ask = float(row["ask"])
        model_p = float(row["model_p_yes"])
        output.append(
            {
                "target_date": row["target_date"],
                "city": row["city"],
                "forecast_model": str(row["forecast_model"]).lower(),
                "win": int(str(row["win"]).lower() == "true"),
                "best_ask": ask,
                "taker_cost_usd": float(row["cost"]),
                "taker_fee_usd": float(row["entry_fee"]),
                "taker_pnl_usd": float(row["pnl"]),
                "distance_lattice_steps": finite(row.get("raw_dist_br")),
                "fresh_spread": finite(row.get("fact_yes_spread")),
                "forecast_peak_delta_hours_local": finite(row.get("forecast_peak_delta_hours_local")),
                "edge_at_fresh_ask": model_p - ask,
                "period": "historical",
            }
        )
    return output


def fresh_book_failure_impact(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    failures = [
        row
        for row in rows
        if str(row.get("created_at_utc") or "") >= FRESH_BOOK_FAILURE_START
        and row.get("blocker") == "fresh_book_fetch_failed"
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in failures:
        key = str(row.get("signal_id") or row.get("condition_id") or "")
        grouped[key].append(row)
    unique_rows = []
    for signal_id, group in sorted(grouped.items(), key=lambda item: min(str(row.get("created_at_utc")) for row in item[1])):
        first = min(group, key=lambda row: str(row.get("created_at_utc") or ""))
        unique_rows.append(
            {
                "signal_id": signal_id,
                "target_date": first.get("target_date"),
                "city": first.get("city"),
                "bracket": first.get("bracket"),
                "forecast_source": first.get("forecast_source"),
                "model_p_yes": first.get("model_p_yes"),
                "snapshot_ask": first.get("snapshot_ask"),
                "book_error": first.get("book_error"),
                "attempt_rows": len(group),
                "first_failure_utc": min(str(row.get("created_at_utc") or "") for row in group),
                "last_failure_utc": max(str(row.get("created_at_utc") or "") for row in group),
            }
        )
    by_date = []
    for target_date in sorted({str(row.get("target_date") or "") for row in unique_rows}):
        date_unique = [row for row in unique_rows if str(row.get("target_date") or "") == target_date]
        date_attempts = [row for row in failures if str(row.get("target_date") or "") == target_date]
        by_date.append(
            {
                "target_date": target_date,
                "unique_candidates": len(date_unique),
                "repeated_failure_rows": len(date_attempts),
            }
        )
    summary = {
        "failure_start_utc": FRESH_BOOK_FAILURE_START,
        "repeated_failure_rows": len(failures),
        "unique_candidates": len(unique_rows),
        "target_dates": len({row["target_date"] for row in unique_rows}),
        "by_target_date": by_date,
        "book_error_counts": {
            error: sum(1 for row in failures if str(row.get("book_error") or "") == error)
            for error in sorted({str(row.get("book_error") or "") for row in failures})
        },
    }
    return summary, unique_rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(float(row["taker_cost_usd"]) for row in rows)
    pnl = sum(float(row["taker_pnl_usd"]) for row in rows)
    fees = sum(float(row["taker_fee_usd"]) for row in rows)
    return {
        "rows": len(rows),
        "dates": len({row["target_date"] for row in rows}),
        "cities": len({row["city"] for row in rows}),
        "wins": sum(int(row["win"]) for row in rows),
        "win_rate": sum(int(row["win"]) for row in rows) / len(rows) if rows else None,
        "avg_ask": sum(float(row["best_ask"]) for row in rows) / len(rows) if rows else None,
        "cost_usd": cost,
        "fees_usd": fees,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "market_fee_baseline_roi": -fees / cost if cost else None,
    }


def segment(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) if row.get(key) not in (None, "") else "missing")].append(row)
    return [{key: value, **summarize(group)} for value, group in sorted(groups.items())]


def block_bootstrap_delta(
    full_rows: list[dict[str, Any]],
    kept_rows: list[dict[str, Any]],
    *,
    samples: int,
) -> tuple[float | None, float | None]:
    dates = sorted({str(row["target_date"]) for row in full_rows})
    if len(dates) < 2 or not kept_rows:
        return (None, None)

    def daily_map(rows: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
        out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for row in rows:
            out[str(row["target_date"])][0] += float(row["taker_cost_usd"])
            out[str(row["target_date"])][1] += float(row["taker_pnl_usd"])
        return {key: (value[0], value[1]) for key, value in out.items()}

    full = daily_map(full_rows)
    kept = daily_map(kept_rows)
    rng = np.random.default_rng(RNG_SEED)
    values = []
    for _ in range(samples):
        sample_dates = rng.choice(dates, size=len(dates), replace=True)
        full_cost = sum(full.get(str(day), (0.0, 0.0))[0] for day in sample_dates)
        full_pnl = sum(full.get(str(day), (0.0, 0.0))[1] for day in sample_dates)
        kept_cost = sum(kept.get(str(day), (0.0, 0.0))[0] for day in sample_dates)
        kept_pnl = sum(kept.get(str(day), (0.0, 0.0))[1] for day in sample_dates)
        if full_cost > 0 and kept_cost > 0:
            values.append(kept_pnl / kept_cost - full_pnl / full_cost)
    if not values:
        return (None, None)
    low, high = np.quantile(values, [0.025, 0.975])
    return (float(low), float(high))


def block_bootstrap_group_roi_delta(
    left_rows: list[dict[str, Any]],
    right_rows: list[dict[str, Any]],
    *,
    samples: int,
) -> tuple[float | None, float | None]:
    dates = sorted({str(row["target_date"]) for row in left_rows + right_rows})
    if len(dates) < 2:
        return (None, None)

    def daily_map(rows: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
        out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for row in rows:
            out[str(row["target_date"])][0] += float(row["taker_cost_usd"])
            out[str(row["target_date"])][1] += float(row["taker_pnl_usd"])
        return {key: (value[0], value[1]) for key, value in out.items()}

    left = daily_map(left_rows)
    right = daily_map(right_rows)
    rng = np.random.default_rng(RNG_SEED)
    values = []
    for _ in range(samples):
        sample_dates = rng.choice(dates, size=len(dates), replace=True)
        left_cost = sum(left.get(str(day), (0.0, 0.0))[0] for day in sample_dates)
        left_pnl = sum(left.get(str(day), (0.0, 0.0))[1] for day in sample_dates)
        right_cost = sum(right.get(str(day), (0.0, 0.0))[0] for day in sample_dates)
        right_pnl = sum(right.get(str(day), (0.0, 0.0))[1] for day in sample_dates)
        if left_cost > 0 and right_cost > 0:
            values.append(left_pnl / left_cost - right_pnl / right_cost)
    if not values:
        return (None, None)
    low, high = np.quantile(values, [0.025, 0.975])
    return (float(low), float(high))


def zero_win_upper_95(n: int, wins: int) -> float | None:
    if n <= 0 or wins != 0:
        return None
    return 1.0 - 0.05 ** (1.0 / n)


def filter_diagnostics(rows: list[dict[str, Any]], *, samples: int) -> list[dict[str, Any]]:
    definitions: list[tuple[str, str, Callable[[dict[str, Any]], bool]]] = [
        ("source_gfs", "forecast source is GFS", lambda row: row["forecast_model"] == "gfs"),
        (
            "distance_gt_1_lattice_step",
            "ticket low is >1 settlement-lattice step above forecast",
            lambda row: finite(row["distance_lattice_steps"]) is not None
            and float(row["distance_lattice_steps"]) > 1.0,
        ),
        (
            "distance_gt_1p5_lattice_steps",
            "ticket low is >1.5 settlement-lattice steps above forecast",
            lambda row: finite(row["distance_lattice_steps"]) is not None
            and float(row["distance_lattice_steps"]) > 1.5,
        ),
        ("ask_le_8c", "fresh ask <=8c", lambda row: float(row["best_ask"]) <= 0.08),
        (
            "spread_gt_2c",
            "fresh spread >2c",
            lambda row: finite(row["fresh_spread"]) is not None and float(row["fresh_spread"]) > 0.02,
        ),
        ("model_edge_ge_30c", "model p minus fresh ask >=30c", lambda row: float(row["edge_at_fresh_ask"]) >= 0.30),
        (
            "after_forecast_peak_clock",
            "decision clock is after the forecast peak clock",
            lambda row: finite(row["forecast_peak_delta_hours_local"]) is not None
            and float(row["forecast_peak_delta_hours_local"]) > 0.0,
        ),
        (
            "gfs_and_distance_gt_1_step",
            "GFS and >1 settlement-lattice step above forecast",
            lambda row: row["forecast_model"] == "gfs"
            and finite(row["distance_lattice_steps"]) is not None
            and float(row["distance_lattice_steps"]) > 1.0,
        ),
        (
            "gfs_and_ask_le_8c",
            "GFS and fresh ask <=8c",
            lambda row: row["forecast_model"] == "gfs" and float(row["best_ask"]) <= 0.08,
        ),
    ]
    output = []
    for name, mechanism, predicate in definitions:
        removed = [row for row in rows if predicate(row)]
        kept = [row for row in rows if not predicate(row)]
        row: dict[str, Any] = {
            "filter": name,
            "mechanism": mechanism,
            **{f"removed_{key}": value for key, value in summarize(removed).items()},
            **{f"kept_{key}": value for key, value in summarize(kept).items()},
        }
        row["removed_zero_win_upper_95"] = zero_win_upper_95(len(removed), sum(int(item["win"]) for item in removed))
        delta_ci = block_bootstrap_delta(rows, kept, samples=samples)
        base_roi = summarize(rows)["roi"]
        kept_roi = summarize(kept)["roi"]
        row["kept_roi_delta_vs_full"] = None if base_roi is None or kept_roi is None else kept_roi - base_roi
        row["kept_roi_delta_ci_low"] = delta_ci[0]
        row["kept_roi_delta_ci_high"] = delta_ci[1]
        for period in ("train", "forward"):
            period_removed = [item for item in removed if item["period"] == period]
            period_kept = [item for item in kept if item["period"] == period]
            row.update({f"{period}_removed_{key}": value for key, value in summarize(period_removed).items()})
            row.update({f"{period}_kept_{key}": value for key, value in summarize(period_kept).items()})
        output.append(row)
    return output


def numeric_feature_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    features = (
        "best_ask",
        "edge_at_fresh_ask",
        "distance_lattice_steps",
        "raw_dist_br",
        "adj_dist_p50_br",
        "bias_mean_asof",
        "bias_p90_asof",
        "hot_tail_pct_asof",
        "cold_tail_pct_asof",
        "score_dist_probability",
        "fresh_spread",
        "forecast_peak_delta_hours_local",
    )
    output = []
    for feature in features:
        for outcome, group in (("winner", [row for row in rows if row["win"] == 1]), ("loser", [row for row in rows if row["win"] == 0])):
            values = [value for value in (finite(row.get(feature)) for row in group) if value is not None]
            output.append(
                {
                    "feature": feature,
                    "outcome": outcome,
                    "n": len(values),
                    "mean": float(np.mean(values)) if values else None,
                    "median": float(np.median(values)) if values else None,
                    "q25": float(np.quantile(values, 0.25)) if values else None,
                    "q75": float(np.quantile(values, 0.75)) if values else None,
                }
            )
    return output


def source_feature_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    features = (
        "best_ask",
        "edge_at_fresh_ask",
        "distance_lattice_steps",
        "fresh_spread",
        "forecast_peak_delta_hours_local",
    )
    output = []
    for feature in features:
        for source in sorted({str(row["forecast_model"]) for row in rows}):
            values = [
                value
                for value in (finite(row.get(feature)) for row in rows if row["forecast_model"] == source)
                if value is not None
            ]
            output.append(
                {
                    "feature": feature,
                    "forecast_model": source,
                    "n": len(values),
                    "mean": float(np.mean(values)) if values else None,
                    "median": float(np.median(values)) if values else None,
                    "q25": float(np.quantile(values, 0.25)) if values else None,
                    "q75": float(np.quantile(values, 0.75)) if values else None,
                }
            )
    return output


def source_cross_segments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for second_key in ("period", "ask_band", "distance_lattice_band", "peak_clock_band", "winner_vs_forecast"):
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[(str(row["forecast_model"]), str(row[second_key]))].append(row)
        for (source, second_value), group in sorted(groups.items()):
            output.append(
                {
                    "forecast_model": source,
                    "second_dimension": second_key,
                    "second_value": second_value,
                    **summarize(group),
                }
            )
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key, value in row.items() if not isinstance(value, (dict, list))})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{key: row.get(key) for key in fields} for row in rows])


def main() -> int:
    args = parse_args()
    rows = enrich_rows(read_csv(args.entries), read_jsonl(args.journal), load_winning_brackets(args.db))
    failure_summary, failure_rows = fresh_book_failure_impact(read_jsonl(args.shadow_decisions))
    historical_rows = normalize_historical_rows(read_csv(args.historical_rows))
    filters = filter_diagnostics(rows, samples=args.bootstrap_samples)
    historical_filters = filter_diagnostics(historical_rows, samples=args.bootstrap_samples)
    historical_by_filter = {
        row["filter"]: {
            "historical_removed": {key.removeprefix("removed_"): value for key, value in row.items() if key.startswith("removed_")},
            "historical_kept": {key.removeprefix("kept_"): value for key, value in row.items() if key.startswith("kept_")},
        }
        for row in historical_filters
    }
    for row in filters:
        row.update(historical_by_filter[row["filter"]])
    current_ecmwf = [row for row in rows if row["forecast_model"] == "ecmwf"]
    current_gfs = [row for row in rows if row["forecast_model"] == "gfs"]
    historical_ecmwf = [row for row in historical_rows if row["forecast_model"] == "ecmwf"]
    historical_gfs = [row for row in historical_rows if row["forecast_model"] == "gfs"]
    current_source_delta_ci = block_bootstrap_group_roi_delta(
        current_ecmwf,
        current_gfs,
        samples=args.bootstrap_samples,
    )
    historical_source_delta_ci = block_bootstrap_group_roi_delta(
        historical_ecmwf,
        historical_gfs,
        samples=args.bootstrap_samples,
    )
    segment_keys = (
        "forecast_model",
        "period",
        "ask_band",
        "distance_lattice_band",
        "spread_band",
        "peak_clock_band",
        "tail_telemetry_status",
        "miss_direction",
        "winner_vs_forecast",
    )
    segments = []
    for key in segment_keys:
        segments.extend({"segment": key, **row} for row in segment(rows, key))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "enriched_entries.csv", rows)
    write_csv(args.out_dir / "segments.csv", segments)
    write_csv(args.out_dir / "filter_diagnostics.csv", filters)
    write_csv(args.out_dir / "outcome_feature_summary.csv", numeric_feature_summary(rows))
    write_csv(args.out_dir / "source_feature_summary.csv", source_feature_summary(rows))
    write_csv(args.out_dir / "source_cross_segments.csv", source_cross_segments(rows))
    write_csv(args.out_dir / "fresh_book_failure_unique_candidates.csv", failure_rows)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "grain": "first zero-notional would-live entry per signal, intended dist>0, settled",
        "execution_assumption": "taker at captured fresh best ask plus official Weather fee",
        "train_end": TRAIN_END,
        "multiple_candidate_filters_k": len(filters),
        "multiple_testing_adjustment": "none; diagnostic only, no live selector action",
        "fresh_book_failure_impact": failure_summary,
        "overall": summarize(rows),
        "train": summarize([row for row in rows if row["period"] == "train"]),
        "forward": summarize([row for row in rows if row["period"] == "forward"]),
        "by_source": segment(rows, "forecast_model"),
        "historical_denominator": summarize(historical_rows),
        "historical_by_source": segment(historical_rows, "forecast_model"),
        "current_ecmwf_minus_gfs_roi": summarize(current_ecmwf)["roi"] - summarize(current_gfs)["roi"],
        "current_ecmwf_minus_gfs_roi_ci_95": current_source_delta_ci,
        "historical_ecmwf_minus_gfs_roi": summarize(historical_ecmwf)["roi"] - summarize(historical_gfs)["roi"],
        "historical_ecmwf_minus_gfs_roi_ci_95": historical_source_delta_ci,
        "by_miss_direction": segment(rows, "miss_direction"),
        "filters": filters,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
