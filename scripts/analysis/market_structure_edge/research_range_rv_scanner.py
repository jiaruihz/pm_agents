#!/usr/bin/env python3
"""Range relative-value scanner for weather city-day brackets.

Target metric:
    city_day_range_relative_value_alpha

The scanner uses fact_signal_candidates as the primary grain. It builds
city-day decision snapshots, enumerates single, adjacent 2/3 bracket ranges and
below/above tails, then evaluates whether model range probability beats market
range probability after execution cost. Raw orderbook prices are matched only
when snapshot_ts_utc <= decision_snapshot_ts_utc.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
OUT_JSON_DEFAULT = ROOT / "docs" / "archive" / "analysis" / "2026-06" / "2026-06-09-range-rv-scanner-v0.json"
OUT_MD_DEFAULT = ROOT / "docs" / "archive" / "analysis" / "2026-06" / "2026-06-09-range-rv-scanner-v0.md"
ORDERBOOK_GLOB_DEFAULT = (
    ROOT / "runtime" / "weather_edge_v1" / "market_data" / "orderbook_snapshots" / "*" / "*.jsonl.gz"
)

sys.path.append(str(ROOT / "scripts" / "analysis" / "execution_quality"))
from research_executable_edge import match_time_aligned_orderbooks, parse_ts  # noqa: E402


RangeRow = dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--orderbook-glob", default=str(ORDERBOOK_GLOB_DEFAULT))
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260609)
    parser.add_argument("--train-frac", type=float, default=0.70)
    parser.add_argument("--min-rule-rows", type=int, default=30)
    parser.add_argument("--edge-thresholds", default="0.02,0.05,0.10,0.15,0.20")
    return parser.parse_args()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def safe_div(num: float, den: float) -> float | None:
    return num / den if den else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def ci95(values: list[float]) -> list[float | None]:
    return [percentile(values, 0.025), percentile(values, 0.975)]


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def money(value: float | None) -> str:
    return "NA" if value is None else f"{value:+.2f}"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def bracket_sort_value(label: str) -> float:
    text = str(label).strip()
    if text.endswith("+"):
        text = text[:-1]
    if "-" in text:
        text = text.split("-", 1)[0]
    try:
        return float(text)
    except ValueError:
        return 9999.0


def side_cost_from_yes(side: str, yes_price: float) -> float:
    return yes_price if side == "BUY_YES" else 1.0 - yes_price


def side_payout(side: str, final_yes: float) -> float:
    return final_yes if side == "BUY_YES" else 1.0 - final_yes


def leg_pnl_unit_notional(side: str, entry_price: float, final_yes: float) -> float:
    """PnL for $1 notional at entry price."""
    if entry_price <= 0.0 or entry_price >= 1.0:
        return 0.0
    if side == "BUY_YES":
        return (1.0 - entry_price) / entry_price if final_yes >= 0.5 else -1.0
    return -1.0 if final_yes >= 0.5 else (1.0 - entry_price) / entry_price


def side_spread(row: dict[str, Any], side: str) -> float | None:
    key = "yes_spread" if side == "BUY_YES" else "no_spread"
    value = row.get(key)
    return None if value is None else float(value)


def load_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows(
        conn,
        """
        SELECT
          candidate_id,
          condition_id,
          market_id,
          event_date,
          city,
          city_pool,
          bracket,
          side,
          forecast_source,
          model_version,
          decision_hours_to_settle,
          decision_snapshot_ts_utc,
          model_p_yes,
          market_yes_price,
          decision_entry_price,
          yes_spread,
          no_spread,
          eligible,
          paper_ordered,
          live_filled,
          settlement_status,
          final_yes,
          fact_built_at_utc
        FROM fact_signal_candidates
        WHERE final_yes IS NOT NULL
          AND settlement_status='settled'
          AND decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND condition_id IS NOT NULL
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND market_yes_price > 0
          AND market_yes_price < 1
        """,
    )


def group_decision_sets(candidates: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[
            (
                str(row["city"]),
                str(row["event_date"]),
                str(row["forecast_source"]),
                str(row["model_version"]),
                str(row["decision_snapshot_ts_utc"]),
            )
        ].append(row)
    decision_sets: list[list[dict[str, Any]]] = []
    for items in grouped.values():
        unique_by_bracket: dict[str, dict[str, Any]] = {}
        for row in items:
            bracket = str(row["bracket"])
            prev = unique_by_bracket.get(bracket)
            if prev is None or abs(float(row["model_p_yes"]) - float(row["market_yes_price"])) > abs(
                float(prev["model_p_yes"]) - float(prev["market_yes_price"])
            ):
                unique_by_bracket[bracket] = row
        ordered = sorted(unique_by_bracket.values(), key=lambda row: bracket_sort_value(str(row["bracket"])))
        if len(ordered) >= 2:
            decision_sets.append(ordered)
    return decision_sets


def range_shape(range_type: str, direction: str) -> str:
    if range_type == "single":
        return "single_bracket"
    if direction == "long_range":
        if range_type in {"adjacent_2", "adjacent_3"}:
            return "inside_range_yes"
        if range_type == "below_tail":
            return "below_tail"
        if range_type == "above_tail":
            return "above_tail"
    if direction == "short_range":
        return "outside_range_no"
    return range_type


def payoff_diagnostics(
    possible_final_legs: list[dict[str, Any]],
    selected_legs: list[dict[str, Any]],
    leg_side: str,
    entry_price_by_bracket: dict[str, float],
) -> dict[str, Any]:
    payoff_by_final_temp: dict[str, float] = {}
    raw_p_by_bracket: dict[str, float] = {
        str(final_leg["bracket"]): float(final_leg["model_p_yes"])
        for final_leg in possible_final_legs
    }
    p_total = sum(raw_p_by_bracket.values())
    p_by_bracket = {
        bracket: (prob / p_total if p_total > 0 else 0.0)
        for bracket, prob in raw_p_by_bracket.items()
    }
    for final_leg in possible_final_legs:
        final_bracket = str(final_leg["bracket"])
        total = 0.0
        for leg in selected_legs:
            bracket = str(leg["bracket"])
            entry = entry_price_by_bracket[bracket]
            final_yes = 1.0 if bracket == final_bracket else 0.0
            total += leg_pnl_unit_notional(leg_side, entry, final_yes)
        payoff_by_final_temp[final_bracket] = total
    expected_pnl = sum(p_by_bracket[bracket] * pnl for bracket, pnl in payoff_by_final_temp.items())
    loss_probability = sum(p_by_bracket[bracket] for bracket, pnl in payoff_by_final_temp.items() if pnl < 0)
    large_loss_probability = sum(p_by_bracket[bracket] for bracket, pnl in payoff_by_final_temp.items() if pnl <= -1.0)
    worst_case_loss = min(payoff_by_final_temp.values()) if payoff_by_final_temp else 0.0
    return {
        "payoff_by_final_temp": payoff_by_final_temp,
        "expected_pnl": expected_pnl,
        "worst_case_loss": worst_case_loss,
        "loss_probability": loss_probability,
        "large_loss_probability": large_loss_probability,
    }


def enumerate_ranges(decision_sets: list[list[dict[str, Any]]]) -> list[RangeRow]:
    out: list[RangeRow] = []
    for items in decision_sets:
        n = len(items)
        ranges: list[tuple[str, int, int]] = []
        for idx in range(n):
            ranges.append(("single", idx, idx + 1))
        for width, name in ((2, "adjacent_2"), (3, "adjacent_3")):
            for start in range(0, n - width + 1):
                ranges.append((name, start, start + width))
        for end in range(1, n):
            ranges.append(("below_tail", 0, end))
        for start in range(1, n):
            ranges.append(("above_tail", start, n))

        base = items[0]
        decision_dt = parse_ts(str(base["decision_snapshot_ts_utc"]))
        if decision_dt is None:
            continue
        for range_type, start, end in ranges:
            legs = items[start:end]
            market_prob_sum = sum(float(row["market_yes_price"]) for row in legs)
            model_prob_sum = sum(float(row["model_p_yes"]) for row in legs)
            hit_count = sum(float(row["final_yes"]) for row in legs)
            range_edge = model_prob_sum - market_prob_sum
            direction = "long_range" if range_edge >= 0 else "short_range"
            leg_side = "BUY_YES" if direction == "long_range" else "BUY_NO"
            entry_price_by_bracket = {
                str(row["bracket"]): side_cost_from_yes(leg_side, float(row["market_yes_price"]))
                for row in legs
            }
            proxy_cost = sum(entry_price_by_bracket[str(row["bracket"])] for row in legs)
            proxy_payout = sum(side_payout(leg_side, float(row["final_yes"])) for row in legs)
            spread_cost = 0.0
            spread_seen = 0
            for row in legs:
                spread = side_spread(row, leg_side)
                if spread is not None:
                    spread_cost += spread
                    spread_seen += 1
            maker_proxy_cost = max(0.0, proxy_cost - spread_cost) if spread_seen == len(legs) else None
            maker_proxy_pnl = None if maker_proxy_cost is None else proxy_payout - maker_proxy_cost
            payoff = payoff_diagnostics(items, legs, leg_side, entry_price_by_bracket)
            out.append(
                {
                    "range_id": (
                        f"{base['city']}|{base['event_date']}|{base['forecast_source']}|"
                        f"{base['model_version']}|{base['decision_snapshot_ts_utc']}|"
                        f"{range_type}|{start}|{end}|{direction}"
                    ),
                    "city": base["city"],
                    "city_pool": base["city_pool"],
                    "event_date": base["event_date"],
                    "target_date": base["event_date"],
                    "forecast_source": base["forecast_source"],
                    "model_version": base["model_version"],
                    "decision_snapshot_ts_utc": base["decision_snapshot_ts_utc"],
                    "decision_dt": decision_dt,
                    "decision_hours_to_settle": base["decision_hours_to_settle"],
                    "range_type": range_type,
                    "direction": direction,
                    "range_shape": range_shape(range_type, direction),
                    "leg_side": leg_side,
                    "bracket_start": legs[0]["bracket"],
                    "bracket_end": legs[-1]["bracket"],
                    "brackets": [row["bracket"] for row in legs],
                    "condition_ids": [row["condition_id"] for row in legs],
                    "n_legs": len(legs),
                    "market_prob_sum": market_prob_sum,
                    "model_prob_sum": model_prob_sum,
                    "range_edge": range_edge,
                    "abs_range_edge": abs(range_edge),
                    "hit_count": hit_count,
                    "eligible_legs": sum(int(row.get("eligible") or 0) for row in legs),
                    "all_legs_eligible": int(all(int(row.get("eligible") or 0) for row in legs)),
                    "paper_ordered_legs": sum(int(row.get("paper_ordered") or 0) for row in legs),
                    "live_filled_legs": sum(int(row.get("live_filled") or 0) for row in legs),
                    "settled_payout": proxy_payout,
                    "taker_cost": proxy_cost,
                    "taker_pnl": proxy_payout - proxy_cost,
                    "maker_proxy_cost": maker_proxy_cost,
                    "maker_proxy_pnl": maker_proxy_pnl,
                    "payoff_by_final_temp": payoff["payoff_by_final_temp"],
                    "expected_pnl": payoff["expected_pnl"],
                    "worst_case_loss": payoff["worst_case_loss"],
                    "loss_probability": payoff["loss_probability"],
                    "large_loss_probability": payoff["large_loss_probability"],
                    "price_source": "decision_market_proxy",
                    "_legs": legs,
                }
            )
    return out


def attach_orderbook_costs(range_rows: list[RangeRow], orderbook_glob: str) -> dict[str, Any]:
    leg_candidates: list[dict[str, Any]] = []
    leg_keys: list[tuple[str, int]] = []
    for range_idx, range_row in enumerate(range_rows):
        side = str(range_row["leg_side"])
        for leg_idx, leg in enumerate(range_row["_legs"]):
            leg_candidates.append(
                {
                    "candidate_id": f"{range_row['range_id']}|{leg_idx}",
                    "condition_id": leg["condition_id"],
                    "market_id": leg["market_id"],
                    "event_date": range_row["event_date"],
                    "city": range_row["city"],
                    "bracket": leg["bracket"],
                    "side": side,
                    "market_yes_price": leg["market_yes_price"],
                    "decision_entry_price": side_cost_from_yes(side, float(leg["market_yes_price"])),
                    "decision_snapshot_ts_utc": range_row["decision_snapshot_ts_utc"],
                    "decision_dt": range_row["decision_dt"],
                    "final_yes": leg["final_yes"],
                    "counterfactual_pnl": None,
                    "live_filled": leg["live_filled"],
                    "outcome": "yes" if side == "BUY_YES" else "no",
                    "price_bucket": "",
                }
            )
            leg_keys.append((range_idx, leg_idx))

    matched, coverage = match_time_aligned_orderbooks(leg_candidates, orderbook_glob)
    by_candidate = {row["candidate_id"]: row for row in matched}
    matched_ranges = 0
    partial_ranges = 0
    for range_idx, range_row in enumerate(range_rows):
        taker_cost = 0.0
        taker_pnl = 0.0
        maker_cost = 0.0
        maker_pnl = 0.0
        age_sum = 0.0
        all_matched = True
        for leg_idx, leg in enumerate(range_row["_legs"]):
            key = f"{range_row['range_id']}|{leg_idx}"
            row = by_candidate.get(key)
            if row is None or row.get("taker_cost_usd") is None:
                all_matched = False
                continue
            taker_cost += float(row["taker_cost_usd"])
            taker_pnl += float(row["taker_pnl_usd"])
            if row.get("maker_cost_proxy_usd") is not None and row.get("maker_pnl_proxy_usd") is not None:
                maker_cost += float(row["maker_cost_proxy_usd"])
                maker_pnl += float(row["maker_pnl_proxy_usd"])
            else:
                all_matched = False
            age_sum += float(row["orderbook_age_minutes"])
        if all_matched:
            matched_ranges += 1
            range_row["orderbook_taker_cost"] = taker_cost
            range_row["orderbook_taker_pnl"] = taker_pnl
            range_row["orderbook_maker_proxy_cost"] = maker_cost
            range_row["orderbook_maker_proxy_pnl"] = maker_pnl
            range_row["orderbook_avg_age_minutes"] = safe_div(age_sum, len(range_row["_legs"]))
            range_row["price_source"] = "time_aligned_orderbook"
        else:
            if any(f"{range_row['range_id']}|{idx}" in by_candidate for idx in range(len(range_row["_legs"]))):
                partial_ranges += 1
    coverage["range_rows"] = len(range_rows)
    coverage["fully_matched_range_rows"] = matched_ranges
    coverage["partial_range_rows"] = partial_ranges
    coverage["fully_matched_range_rate"] = safe_div(matched_ranges, len(range_rows))
    return coverage


def strip_private(rows_in: list[RangeRow]) -> list[RangeRow]:
    out: list[RangeRow] = []
    for row in rows_in:
        clean = {key: value for key, value in row.items() if key not in {"_legs", "decision_dt"}}
        out.append(clean)
    return out


def metric_rows(rows_in: list[RangeRow], *, source: str) -> list[RangeRow]:
    if source == "orderbook":
        return [
            {
                **row,
                "eval_cost": row["orderbook_taker_cost"],
                "eval_pnl": row["orderbook_taker_pnl"],
                "eval_maker_cost": row["orderbook_maker_proxy_cost"],
                "eval_maker_pnl": row["orderbook_maker_proxy_pnl"],
            }
            for row in rows_in
            if row.get("orderbook_taker_cost") is not None and row.get("orderbook_taker_pnl") is not None
        ]
    return [
        {
            **row,
            "eval_cost": row["taker_cost"],
            "eval_pnl": row["taker_pnl"],
            "eval_maker_cost": row["maker_proxy_cost"],
            "eval_maker_pnl": row["maker_proxy_pnl"],
        }
        for row in rows_in
    ]


def roi_for(rows_in: list[RangeRow], cost_key: str = "eval_cost", pnl_key: str = "eval_pnl") -> float | None:
    cost = sum(float(row.get(cost_key) or 0.0) for row in rows_in)
    pnl = sum(float(row.get(pnl_key) or 0.0) for row in rows_in)
    return safe_div(pnl, cost)


def summarize(rows_in: list[RangeRow], *, source: str) -> dict[str, Any]:
    cost = sum(float(row.get("eval_cost") or 0.0) for row in rows_in)
    pnl = sum(float(row.get("eval_pnl") or 0.0) for row in rows_in)
    maker_cost_rows = [row for row in rows_in if row.get("eval_maker_cost") is not None and row.get("eval_maker_pnl") is not None]
    maker_cost = sum(float(row["eval_maker_cost"]) for row in maker_cost_rows)
    maker_pnl = sum(float(row["eval_maker_pnl"]) for row in maker_cost_rows)
    by_date: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "pnl": 0.0})
    for row in rows_in:
        slot = by_date[str(row["event_date"])]
        slot["cost"] += float(row.get("eval_cost") or 0.0)
        slot["pnl"] += float(row.get("eval_pnl") or 0.0)
    top5_dates = sorted(by_date.items(), key=lambda item: item[1]["pnl"], reverse=True)[:5]
    top5_set = {date for date, _value in top5_dates}
    drop_top5_cost = sum(value["cost"] for date, value in by_date.items() if date not in top5_set)
    drop_top5_pnl = sum(value["pnl"] for date, value in by_date.items() if date not in top5_set)
    worst_case_values = [float(row.get("worst_case_loss") or 0.0) for row in rows_in]
    return {
        "source": source,
        "rows": len(rows_in),
        "active_event_dates": len({row["event_date"] for row in rows_in}),
        "active_city_days": len({(row["city"], row["event_date"]) for row in rows_in}),
        "market_prob_sum": sum(float(row["market_prob_sum"]) for row in rows_in),
        "model_prob_sum": sum(float(row["model_prob_sum"]) for row in rows_in),
        "range_edge": sum(float(row["range_edge"]) for row in rows_in),
        "avg_abs_range_edge": safe_div(sum(float(row["abs_range_edge"]) for row in rows_in), len(rows_in)),
        "taker_cost": cost,
        "settled_pnl": pnl,
        "taker_roi": safe_div(pnl, cost),
        "maker_proxy_cost": maker_cost,
        "maker_proxy_settled_pnl": maker_pnl,
        "maker_proxy_roi": safe_div(maker_pnl, maker_cost),
        "avg_expected_pnl_unit_notional": safe_div(sum(float(row.get("expected_pnl") or 0.0) for row in rows_in), len(rows_in)),
        "avg_worst_case_loss_unit_notional": safe_div(sum(worst_case_values), len(worst_case_values)),
        "avg_loss_probability": safe_div(sum(float(row.get("loss_probability") or 0.0) for row in rows_in), len(rows_in)),
        "avg_large_loss_probability": safe_div(sum(float(row.get("large_loss_probability") or 0.0) for row in rows_in), len(rows_in)),
        "drop_top5_event_dates": sorted(top5_set),
        "drop_top5_settled_pnl": drop_top5_pnl,
        "drop_top5_taker_roi": safe_div(drop_top5_pnl, drop_top5_cost),
    }


def grouped_summary(rows_in: list[RangeRow], key: str, *, source: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[RangeRow]] = defaultdict(list)
    for row in rows_in:
        grouped[str(row.get(key) or "unknown")].append(row)
    out = []
    for name, items in sorted(grouped.items()):
        item = summarize(items, source=source)
        item[key] = name
        out.append(item)
    out.sort(key=lambda row: row.get("settled_pnl") or 0.0, reverse=True)
    return out


def split_train_holdout(dates: list[str], train_frac: float) -> tuple[set[str], set[str], str | None]:
    ordered = sorted(dates)
    if not ordered:
        return set(), set(), None
    split_idx = max(1, min(len(ordered) - 1, int(math.floor(len(ordered) * train_frac))))
    train = set(ordered[:split_idx])
    holdout = set(ordered[split_idx:])
    return train, holdout, ordered[split_idx] if split_idx < len(ordered) else None


def apply_rule(rows_in: list[RangeRow], rule: dict[str, Any]) -> list[RangeRow]:
    edge_min = float(rule["abs_edge_min"])
    return [
        row
        for row in rows_in
        if row["range_type"] == rule["range_type"]
        and row["direction"] == rule["direction"]
        and float(row["abs_range_edge"]) >= edge_min
    ]


def baseline_for(rows_in: list[RangeRow], rule: dict[str, Any]) -> list[RangeRow]:
    return [
        row
        for row in rows_in
        if row["range_type"] == rule["range_type"] and row["direction"] == rule["direction"]
    ]


def bootstrap_delta(
    selected: list[RangeRow],
    baseline: list[RangeRow],
    *,
    iters: int,
    seed: int,
) -> dict[str, Any]:
    selected_by_date: dict[str, list[RangeRow]] = defaultdict(list)
    baseline_by_date: dict[str, list[RangeRow]] = defaultdict(list)
    for row in selected:
        selected_by_date[str(row["event_date"])].append(row)
    for row in baseline:
        baseline_by_date[str(row["event_date"])].append(row)
    dates = sorted(set(selected_by_date) | set(baseline_by_date))
    if not dates:
        return {"roi_ci95": [None, None], "excess_roi_ci95": [None, None]}
    rng = random.Random(seed)
    roi_samples: list[float] = []
    delta_samples: list[float] = []
    for _ in range(iters):
        sel_sample: list[RangeRow] = []
        base_sample: list[RangeRow] = []
        for _date in dates:
            pick = rng.choice(dates)
            sel_sample.extend(selected_by_date.get(pick, []))
            base_sample.extend(baseline_by_date.get(pick, []))
        sel_roi = roi_for(sel_sample)
        base_roi = roi_for(base_sample)
        if sel_roi is not None and math.isfinite(sel_roi):
            roi_samples.append(sel_roi)
        if sel_roi is not None and base_roi is not None and math.isfinite(sel_roi - base_roi):
            delta_samples.append(sel_roi - base_roi)
    return {
        "roi_ci95": ci95(roi_samples),
        "excess_roi_ci95": ci95(delta_samples),
    }


def evaluate_rule(
    rows_in: list[RangeRow],
    rule: dict[str, Any],
    *,
    source: str,
    bootstrap_iters: int,
    seed: int,
) -> dict[str, Any]:
    selected = apply_rule(rows_in, rule)
    baseline = baseline_for(rows_in, rule)
    selected_summary = summarize(selected, source=source)
    baseline_summary = summarize(baseline, source=source)
    selected_roi = selected_summary["taker_roi"]
    baseline_roi = baseline_summary["taker_roi"]
    excess_roi = None if selected_roi is None or baseline_roi is None else selected_roi - baseline_roi
    boot = bootstrap_delta(selected, baseline, iters=bootstrap_iters, seed=seed)
    return {
        "rule": dict(rule),
        "selected": selected_summary,
        "baseline": baseline_summary,
        "excess_roi": excess_roi,
        "roi_ci95_cluster_by_event_date": boot["roi_ci95"],
        "excess_roi_ci95_cluster_by_event_date": boot["excess_roi_ci95"],
    }


def select_rules(
    rows_in: list[RangeRow],
    *,
    source: str,
    thresholds: list[float],
    min_rule_rows: int,
    bootstrap_iters: int,
    seed: int,
) -> list[dict[str, Any]]:
    candidate_rules = []
    range_types = sorted({row["range_type"] for row in rows_in})
    directions = sorted({row["direction"] for row in rows_in})
    idx = 0
    for range_type in range_types:
        for direction in directions:
            for threshold in thresholds:
                rule = {"range_type": range_type, "direction": direction, "abs_edge_min": threshold}
                selected = apply_rule(rows_in, rule)
                if len(selected) < min_rule_rows:
                    continue
                evaluated = evaluate_rule(
                    rows_in,
                    rule,
                    source=source,
                    bootstrap_iters=bootstrap_iters,
                    seed=seed + idx * 17,
                )
                idx += 1
                candidate_rules.append(evaluated)
    candidate_rules.sort(
        key=lambda item: (
            item["excess_roi"] if item["excess_roi"] is not None else -999.0,
            item["selected"]["settled_pnl"],
        ),
        reverse=True,
    )
    return candidate_rules


def gate_result(train_eval: dict[str, Any] | None, holdout_eval: dict[str, Any] | None) -> dict[str, str]:
    if not train_eval or not holdout_eval:
        return {"significance": "NA", "baseline": "NA", "forward": "NA", "verdict": "inconclusive"}
    roi_ci = train_eval.get("roi_ci95_cluster_by_event_date") or [None, None]
    excess_ci = train_eval.get("excess_roi_ci95_cluster_by_event_date") or [None, None]
    significance = "PASS" if roi_ci[0] is not None and roi_ci[0] > 0 else "FAIL"
    baseline = "PASS" if excess_ci[0] is not None and excess_ci[0] > 0 else "FAIL"
    holdout_roi_ci = holdout_eval.get("roi_ci95_cluster_by_event_date") or [None, None]
    holdout_excess_ci = holdout_eval.get("excess_roi_ci95_cluster_by_event_date") or [None, None]
    forward = (
        "PASS"
        if holdout_roi_ci[0] is not None
        and holdout_roi_ci[0] > 0
        and holdout_excess_ci[0] is not None
        and holdout_excess_ci[0] > 0
        else "FAIL"
    )
    verdict = "confirmed" if significance == "PASS" and baseline == "PASS" and forward == "PASS" else "inconclusive"
    return {"significance": significance, "baseline": baseline, "forward": forward, "verdict": verdict}


def filter_family(rows_in: list[RangeRow], family: str) -> list[RangeRow]:
    if family == "mixed_single_allowed":
        return rows_in
    if family == "single_only":
        return [row for row in rows_in if row["range_type"] == "single"]
    if family == "true_range_only":
        return [row for row in rows_in if row["range_type"] != "single"]
    if family == "adjacent_only":
        return [row for row in rows_in if row["range_type"] in {"adjacent_2", "adjacent_3"}]
    if family == "tail_only":
        return [row for row in rows_in if row["range_type"] in {"below_tail", "above_tail"}]
    if family == "inside_range_yes":
        return [row for row in rows_in if row["range_shape"] == "inside_range_yes"]
    if family == "outside_range_no":
        return [row for row in rows_in if row["range_shape"] == "outside_range_no"]
    raise ValueError(f"unknown family: {family}")


def run_family_experiments(
    rows_in: list[RangeRow],
    *,
    source: str,
    train_dates: set[str],
    holdout_dates: set[str],
    thresholds: list[float],
    min_rule_rows: int,
    bootstrap_iters: int,
    seed: int,
) -> list[dict[str, Any]]:
    families = [
        "mixed_single_allowed",
        "single_only",
        "true_range_only",
        "adjacent_only",
        "tail_only",
        "inside_range_yes",
        "outside_range_no",
    ]
    out: list[dict[str, Any]] = []
    for idx, family in enumerate(families):
        family_rows = filter_family(rows_in, family)
        train_rows = [row for row in family_rows if row["event_date"] in train_dates]
        holdout_rows = [row for row in family_rows if row["event_date"] in holdout_dates]
        rules = select_rules(
            train_rows,
            source=source,
            thresholds=thresholds,
            min_rule_rows=min_rule_rows,
            bootstrap_iters=bootstrap_iters,
            seed=seed + idx * 1000,
        )
        top = rules[0] if rules else None
        holdout_eval = (
            evaluate_rule(
                holdout_rows,
                top["rule"],
                source=source,
                bootstrap_iters=bootstrap_iters,
                seed=seed + idx * 1000 + 500,
            )
            if top
            else None
        )
        out.append(
            {
                "family": family,
                "source": source,
                "train_rows_available": len(train_rows),
                "holdout_rows_available": len(holdout_rows),
                "rules_tested": len(rules),
                "train_selected": top,
                "holdout_evaluation": holdout_eval,
                "gates": gate_result(top, holdout_eval),
            }
        )
    return out


def data_self_check(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "fact_trades_max_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "fact_signal_candidates_max_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"),
        "fact_trades_by_class": rows(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class"),
        "fact_trades_by_settlement_status": rows(
            conn, "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows FROM fact_trades GROUP BY settlement_status"
        ),
        "fact_signal_candidate_coverage": rows(
            conn,
            """
            SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled
            FROM fact_signal_candidates
            """,
        )[0],
        "clob_order_fill_join": rows(
            conn,
            """
            SELECT o.status, COUNT(*) AS orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill
            FROM orders o LEFT JOIN fills f USING(execution_id)
            WHERE o.venue='polymarket_clob'
            GROUP BY o.status
            """,
        ),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def small_table(items: list[dict[str, Any]], keys: list[str], limit: int = 8) -> list[str]:
    lines = ["| " + " | ".join(keys) + " |", "|" + "|".join("---" for _ in keys) + "|"]
    for item in items[:limit]:
        cells = []
        for key in keys:
            value = item.get(key)
            if isinstance(value, float):
                if "roi" in key or "edge" in key:
                    cells.append(pct(value))
                else:
                    cells.append(f"{value:.2f}")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def family_row(item: dict[str, Any]) -> str:
    train = item.get("train_selected")
    holdout = item.get("holdout_evaluation")
    gates = item.get("gates") or {}
    if not train:
        return (
            f"| `{item['family']}` | {item.get('train_rows_available', 0)} | {item.get('holdout_rows_available', 0)} | "
            "NA | NA | NA | NA | NA | NA | NA | "
            f"`{gates.get('verdict', 'inconclusive')}` |"
        )
    rule = train["rule"]
    rule_text = f"{rule['range_type']}/{rule['direction']}/edge>={rule['abs_edge_min']}"
    holdout_roi = holdout.get("selected", {}).get("taker_roi") if holdout else None
    holdout_excess = holdout.get("excess_roi") if holdout else None
    holdout_drop = holdout.get("selected", {}).get("drop_top5_taker_roi") if holdout else None
    return (
        f"| `{item['family']}` | {item.get('train_rows_available', 0)} | {item.get('holdout_rows_available', 0)} | "
        f"`{rule_text}` | {pct(train['selected']['taker_roi'])} | {pct(train['excess_roi'])} | "
        f"{pct(train['selected']['drop_top5_taker_roi'])} | {pct(holdout_roi)} | {pct(holdout_excess)} | "
        f"{pct(holdout_drop)} | `{gates.get('significance')}/{gates.get('baseline')}/{gates.get('forward')} -> {gates.get('verdict')}` |"
    )


def write_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    proxy = report["decision_proxy"]
    orderbook = report["orderbook_executable_subset"]
    top_proxy = proxy.get("train_selected_rules", [{}])[0] if proxy.get("train_selected_rules") else None
    top_holdout = proxy.get("holdout_evaluation")
    lines = [
        "# Range RV Scanner v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{report['target_metric']}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: local research only; no N100/live config changed.",
        "",
        "## 数据快照",
        "",
        f"- 数据源: `runtime/weather.db.fact_signal_candidates` primary; `fact_trades` only for mandatory freshness/status self-check.",
        f"- DB last_modified: `{report['db_last_modified_utc']}`",
        f"- fact built at: `{report['data_self_check']['fact_signal_candidates_max_built_at_utc']}`",
        f"- scanner input candidates: `{report['input']['candidate_rows']}` seen-complete rows; decision sets `{report['input']['decision_sets']}`; range rows `{report['input']['range_rows']}`; all-legs-eligible range rows `{report['input']['all_legs_eligible_range_rows']}`.",
        f"- unsettled used in scanner: `0` (filter `settlement_status='settled'` and `final_yes IS NOT NULL`).",
        f"- missing_bracket used in scanner: `0`.",
        f"- local cache note: user requested local `runtime/weather.db`; no N100/live sync or config change was run.",
        "",
        "## Target Metric",
        "",
        "`city_day_range_relative_value_alpha` = train-selected city-day range YES/NO basket ROI minus matched baseline ROI, where the range is enumerated inside one city-day decision snapshot and model edge is `SUM(model_p_yes) - SUM(market_yes_price)`.",
        "",
        "Ranges enumerated: single bracket, adjacent 2, adjacent 3, below-tail, above-tail. Positive range edge buys YES across the range; negative range edge buys NO across the range. Shape tags include `single_bracket`, `inside_range_yes`, `outside_range_no`, `below_tail`, and `above_tail`.",
        "",
        "## Gates",
        "",
        "| gate | status |",
        "|---|---|",
    ]
    for key, value in report["gates"].items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(
        [
            "",
            "Final verdict: `inconclusive`. This report gives no live action.",
            "",
            "## Train / Holdout",
            "",
            f"- Split field: `event_date`.",
            f"- Train dates: `{report['split']['train_start']}` to `{report['split']['train_end']}`.",
            f"- Holdout dates: `{report['split']['holdout_start']}` to `{report['split']['holdout_end']}`.",
            f"- Rule grid: `range_type x direction x abs(range_edge) threshold`; K tested on train = `{proxy['rules_tested']}`.",
            "",
        ]
    )
    if top_proxy:
        rule = top_proxy["rule"]
        lines.extend(
            [
                "## Selected Train Rule",
                "",
                f"`{rule['range_type']} / {rule['direction']} / abs_range_edge >= {rule['abs_edge_min']}`",
                "",
                "| window | rows | dates | taker cost | settled pnl | ROI | ROI CI | baseline ROI | excess ROI | excess CI | top5 removed ROI | avg worst loss | avg loss prob |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
                (
                    f"| train | {top_proxy['selected']['rows']} | {top_proxy['selected']['active_event_dates']} | "
                    f"{top_proxy['selected']['taker_cost']:.2f} | {money(top_proxy['selected']['settled_pnl'])} | "
                    f"{pct(top_proxy['selected']['taker_roi'])} | {fmt_ci(top_proxy['roi_ci95_cluster_by_event_date'])} | "
                    f"{pct(top_proxy['baseline']['taker_roi'])} | {pct(top_proxy['excess_roi'])} | "
                    f"{fmt_ci(top_proxy['excess_roi_ci95_cluster_by_event_date'])} | "
                    f"{pct(top_proxy['selected']['drop_top5_taker_roi'])} | "
                    f"{money(top_proxy['selected']['avg_worst_case_loss_unit_notional'])} | "
                    f"{pct(top_proxy['selected']['avg_loss_probability'])} |"
                ),
            ]
        )
        if top_holdout:
            lines.append(
                f"| holdout | {top_holdout['selected']['rows']} | {top_holdout['selected']['active_event_dates']} | "
                f"{top_holdout['selected']['taker_cost']:.2f} | {money(top_holdout['selected']['settled_pnl'])} | "
                f"{pct(top_holdout['selected']['taker_roi'])} | {fmt_ci(top_holdout['roi_ci95_cluster_by_event_date'])} | "
                f"{pct(top_holdout['baseline']['taker_roi'])} | {pct(top_holdout['excess_roi'])} | "
                f"{fmt_ci(top_holdout['excess_roi_ci95_cluster_by_event_date'])} | "
                f"{pct(top_holdout['selected']['drop_top5_taker_roi'])} | "
                f"{money(top_holdout['selected']['avg_worst_case_loss_unit_notional'])} | "
                f"{pct(top_holdout['selected']['avg_loss_probability'])} |"
            )
    lines.extend(
        [
            "",
            "## Follow-up Families",
            "",
            "These experiments keep single-leg opportunity visible, but evaluate it separately from true range RV. Each family still selects only on train and only verifies on holdout.",
            "",
            "### Seen Complete Universe",
            "",
            "| family | train rows | holdout rows | selected train rule | train ROI | train excess | train top5 removed ROI | holdout ROI | holdout excess | holdout top5 removed ROI | gates |",
            "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in proxy.get("family_experiments", {}).get("seen_complete", []):
        lines.append(family_row(item))
    lines.extend(
        [
            "",
            "### Eligible-only Universe",
            "",
            "| family | train rows | holdout rows | selected train rule | train ROI | train excess | train top5 removed ROI | holdout ROI | holdout excess | holdout top5 removed ROI | gates |",
            "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in proxy.get("family_experiments", {}).get("eligible_only", []):
        lines.append(family_row(item))
    lines.extend(
        [
            "",
            "## Range Type Background",
            "",
            "| range_type | rows | dates | taker cost | settled pnl | ROI | avg abs edge | top5 removed ROI |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in proxy["by_range_type"]:
        lines.append(
            f"| `{item['range_type']}` | {item['rows']} | {item['active_event_dates']} | {item['taker_cost']:.2f} | "
            f"{money(item['settled_pnl'])} | {pct(item['taker_roi'])} | {pct(item['avg_abs_range_edge'])} | "
            f"{pct(item['drop_top5_taker_roi'])} |"
        )
    lines.extend(
        [
            "",
            "## Shape Background",
            "",
            "| shape | rows | dates | settled pnl | ROI | avg worst loss | avg loss prob |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in proxy["by_range_shape"]:
        lines.append(
            f"| `{item['range_shape']}` | {item['rows']} | {item['active_event_dates']} | "
            f"{money(item['settled_pnl'])} | {pct(item['taker_roi'])} | "
            f"{money(item['avg_worst_case_loss_unit_notional'])} | {pct(item['avg_loss_probability'])} |"
        )
    lines.extend(
        [
            "",
            "## Orderbook Executable Subset",
            "",
            "Orderbook prices are matched with the same constraint used by executable-edge research: latest `snapshot_ts_utc <= decision_snapshot_ts_utc`. This subset is a capacity/execution check, not a replacement for the full decision proxy.",
            "",
            f"- Leg coverage: `{orderbook['coverage'].get('matched_candidate_rows')}` / `{orderbook['coverage'].get('candidate_rows')}`.",
            f"- Fully matched ranges: `{orderbook['coverage'].get('fully_matched_range_rows')}` / `{orderbook['coverage'].get('range_rows')}`.",
            "",
        ]
    )
    if orderbook.get("train_selected_rules"):
        top_ob = orderbook["train_selected_rules"][0]
        ob_holdout = orderbook.get("holdout_evaluation")
        lines.extend(
            [
                "| window | rows | dates | taker cost | settled pnl | ROI | baseline ROI | excess ROI | top5 removed ROI |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
                (
                    f"| train | {top_ob['selected']['rows']} | {top_ob['selected']['active_event_dates']} | "
                    f"{top_ob['selected']['taker_cost']:.2f} | {money(top_ob['selected']['settled_pnl'])} | "
                    f"{pct(top_ob['selected']['taker_roi'])} | {pct(top_ob['baseline']['taker_roi'])} | "
                    f"{pct(top_ob['excess_roi'])} | {pct(top_ob['selected']['drop_top5_taker_roi'])} |"
                ),
            ]
        )
        if ob_holdout:
            lines.append(
                f"| holdout | {ob_holdout['selected']['rows']} | {ob_holdout['selected']['active_event_dates']} | "
                f"{ob_holdout['selected']['taker_cost']:.2f} | {money(ob_holdout['selected']['settled_pnl'])} | "
                f"{pct(ob_holdout['selected']['taker_roi'])} | {pct(ob_holdout['baseline']['taker_roi'])} | "
                f"{pct(ob_holdout['excess_roi'])} | {pct(ob_holdout['selected']['drop_top5_taker_roi'])} |"
            )
    lines.extend(
        [
            "",
            "## 8 环覆盖自检",
            "",
            "| 环 | 覆盖 | 说明 |",
            "|---|---|---|",
            "| 1 描述性绩效切片 | yes | range-level settled counterfactual from fact_signal_candidates |",
            "| 2 统计推断 | yes | event_date cluster bootstrap |",
            "| 3 信号判别 | partial | range_edge train selection, no independent model-rank proof |",
            "| 4 概率分布评估 | partial | range probability sums only, no full calibration model |",
            "| 5 执行微结构 | partial | time-aligned orderbook subset plus decision proxy |",
            "| 6 容量 | no | no size/depth capacity sweep |",
            "| 7 组合相关性 | partial | event_date cluster bootstrap, no cross-city rho model |",
            "| 8 基准/反事实 | yes | matched range_type+direction baseline and excess ROI |",
            "",
            "## Notes",
            "",
            "- Baseline is matched by `range_type + direction` in the same train/holdout window, before applying the train-selected edge threshold.",
            "- `settled_pnl` is a research counterfactual at range-row grain; it is not live_real PnL.",
            "- Any failed gate means `inconclusive`; this scanner must not be used to change live sizing, city pools, or N100 config.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    thresholds = [float(part) for part in args.edge_thresholds.split(",") if part.strip()]
    conn = connect(args.db_path)
    candidates = load_candidates(conn)
    decision_sets = group_decision_sets(candidates)
    range_rows = enumerate_ranges(decision_sets)
    orderbook_coverage = attach_orderbook_costs(range_rows, args.orderbook_glob)
    clean_rows = strip_private(range_rows)

    all_dates = sorted({str(row["event_date"]) for row in clean_rows})
    train_dates, holdout_dates, split_date = split_train_holdout(all_dates, args.train_frac)
    proxy_rows = metric_rows(clean_rows, source="proxy")
    ob_rows = metric_rows(clean_rows, source="orderbook")
    proxy_eligible_rows = [row for row in proxy_rows if int(row.get("all_legs_eligible") or 0) == 1]
    ob_eligible_rows = [row for row in ob_rows if int(row.get("all_legs_eligible") or 0) == 1]
    proxy_train = [row for row in proxy_rows if row["event_date"] in train_dates]
    proxy_holdout = [row for row in proxy_rows if row["event_date"] in holdout_dates]
    ob_train = [row for row in ob_rows if row["event_date"] in train_dates]
    ob_holdout = [row for row in ob_rows if row["event_date"] in holdout_dates]

    proxy_rules = select_rules(
        proxy_train,
        source="decision_market_proxy",
        thresholds=thresholds,
        min_rule_rows=args.min_rule_rows,
        bootstrap_iters=args.bootstrap_iters,
        seed=args.seed + 100,
    )
    proxy_top = proxy_rules[0] if proxy_rules else None
    proxy_holdout_eval = (
        evaluate_rule(
            proxy_holdout,
            proxy_top["rule"],
            source="decision_market_proxy",
            bootstrap_iters=args.bootstrap_iters,
            seed=args.seed + 200,
        )
        if proxy_top
        else None
    )

    ob_rules = select_rules(
        ob_train,
        source="time_aligned_orderbook",
        thresholds=thresholds,
        min_rule_rows=max(10, min(args.min_rule_rows, 20)),
        bootstrap_iters=args.bootstrap_iters,
        seed=args.seed + 300,
    )
    ob_top = ob_rules[0] if ob_rules else None
    ob_holdout_eval = (
        evaluate_rule(
            ob_holdout,
            ob_top["rule"],
            source="time_aligned_orderbook",
            bootstrap_iters=args.bootstrap_iters,
            seed=args.seed + 400,
        )
        if ob_top
        else None
    )
    gates = gate_result(proxy_top, proxy_holdout_eval)
    if ob_top and ob_holdout_eval and gates["verdict"] == "confirmed":
        ob_gates = gate_result(ob_top, ob_holdout_eval)
        if ob_gates["verdict"] != "confirmed":
            gates["forward"] = "FAIL"
            gates["verdict"] = "inconclusive"

    db_path = Path(args.db_path)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": "city_day_range_relative_value_alpha",
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "train_frac": args.train_frac,
            "min_rule_rows": args.min_rule_rows,
            "edge_thresholds": thresholds,
            "orderbook_glob": args.orderbook_glob,
        },
        "data_self_check": data_self_check(conn),
        "input": {
            "candidate_rows": len(candidates),
            "decision_sets": len(decision_sets),
            "range_rows": len(clean_rows),
            "event_dates": len(all_dates),
            "cities": len({row["city"] for row in clean_rows}),
            "all_legs_eligible_range_rows": sum(int(row.get("all_legs_eligible") or 0) for row in clean_rows),
        },
        "split": {
            "split_date": split_date,
            "train_start": min(train_dates) if train_dates else None,
            "train_end": max(train_dates) if train_dates else None,
            "holdout_start": min(holdout_dates) if holdout_dates else None,
            "holdout_end": max(holdout_dates) if holdout_dates else None,
            "train_event_dates": len(train_dates),
            "holdout_event_dates": len(holdout_dates),
        },
        "decision_proxy": {
            "overall": summarize(proxy_rows, source="decision_market_proxy"),
            "by_range_type": grouped_summary(proxy_rows, "range_type", source="decision_market_proxy"),
            "by_range_shape": grouped_summary(proxy_rows, "range_shape", source="decision_market_proxy"),
            "by_direction": grouped_summary(proxy_rows, "direction", source="decision_market_proxy"),
            "rules_tested": len(proxy_rules),
            "train_selected_rules": proxy_rules[:10],
            "holdout_evaluation": proxy_holdout_eval,
            "family_experiments": {
                "seen_complete": run_family_experiments(
                    proxy_rows,
                    source="decision_market_proxy",
                    train_dates=train_dates,
                    holdout_dates=holdout_dates,
                    thresholds=thresholds,
                    min_rule_rows=args.min_rule_rows,
                    bootstrap_iters=args.bootstrap_iters,
                    seed=args.seed + 10000,
                ),
                "eligible_only": run_family_experiments(
                    proxy_eligible_rows,
                    source="decision_market_proxy",
                    train_dates=train_dates,
                    holdout_dates=holdout_dates,
                    thresholds=thresholds,
                    min_rule_rows=max(10, min(args.min_rule_rows, 20)),
                    bootstrap_iters=args.bootstrap_iters,
                    seed=args.seed + 20000,
                ),
            },
        },
        "orderbook_executable_subset": {
            "coverage": orderbook_coverage,
            "overall": summarize(ob_rows, source="time_aligned_orderbook"),
            "by_range_type": grouped_summary(ob_rows, "range_type", source="time_aligned_orderbook"),
            "by_range_shape": grouped_summary(ob_rows, "range_shape", source="time_aligned_orderbook"),
            "by_direction": grouped_summary(ob_rows, "direction", source="time_aligned_orderbook"),
            "rules_tested": len(ob_rules),
            "train_selected_rules": ob_rules[:10],
            "holdout_evaluation": ob_holdout_eval,
            "family_experiments": {
                "seen_complete": run_family_experiments(
                    ob_rows,
                    source="time_aligned_orderbook",
                    train_dates=train_dates,
                    holdout_dates=holdout_dates,
                    thresholds=thresholds,
                    min_rule_rows=max(10, min(args.min_rule_rows, 20)),
                    bootstrap_iters=args.bootstrap_iters,
                    seed=args.seed + 30000,
                ),
                "eligible_only": run_family_experiments(
                    ob_eligible_rows,
                    source="time_aligned_orderbook",
                    train_dates=train_dates,
                    holdout_dates=holdout_dates,
                    thresholds=thresholds,
                    min_rule_rows=max(10, min(args.min_rule_rows, 20)),
                    bootstrap_iters=args.bootstrap_iters,
                    seed=args.seed + 40000,
                ),
            },
        },
        "gates": gates,
        "sample_range_rows": clean_rows[:20],
    }
    write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(f"verdict={gates['verdict']} significance={gates['significance']} baseline={gates['baseline']} forward={gates['forward']}")


if __name__ == "__main__":
    main()
