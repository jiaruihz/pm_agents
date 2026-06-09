#!/usr/bin/env python3
"""Forecast-first adjacent2/3 Range RV counterfactual research.

Target metric:
    forecast_first_adjacent_range_rv_alpha

Primary analysis grain is ``runtime/weather.db.fact_signal_candidates``.
Executable prices use raw orderbook snapshots only through the existing
time-aligned matcher: latest orderbook ``snapshot_ts_utc <=
decision_snapshot_ts_utc``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))

import research_range_rv_scanner as scanner  # noqa: E402


OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-forecast-first-adjacent-range-rv-v0-1.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-forecast-first-adjacent-range-rv-v0-1.md"
MASS_THRESHOLDS = (0.55, 0.60, 0.65, 0.70)
COST_THRESHOLDS = (0.70, 0.75, 0.80)
EDGE_THRESHOLDS = (0.05, 0.10, 0.15)
HOUR_BUCKETS = ("T-12-18", "T-18-24", "T-24-36", "T-36+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(scanner.DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--orderbook-glob", default=str(scanner.ORDERBOOK_GLOB_DEFAULT))
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260609)
    parser.add_argument("--train-frac", type=float, default=0.70)
    parser.add_argument("--spread-cap", type=float, default=0.10)
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


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def money(value: float | None) -> str:
    return "NA" if value is None else f"{value:+.2f}"


def ci_text(value: list[float | None] | None) -> str:
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


def hour_bucket(hours: Any) -> str | None:
    if hours is None:
        return None
    h = float(hours)
    if 12 <= h < 18:
        return "T-12-18"
    if 18 <= h < 24:
        return "T-18-24"
    if 24 <= h < 36:
        return "T-24-36"
    if h >= 36:
        return "T-36+"
    return None


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


def data_self_check(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    gate_path = ROOT / "runtime" / "_dashboard_logs" / "clob_fill_coverage_gate.json"
    gate = None
    if gate_path.exists():
        try:
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            gate = {"status": "unreadable"}
    return {
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat()
        if db_path.exists()
        else None,
        "fact_trades_max_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "fact_signal_candidates_max_built_at_utc": scalar(
            conn, "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"
        ),
        "fact_trades_rows": scalar(conn, "SELECT COUNT(*) FROM fact_trades"),
        "fact_signal_candidates_rows": scalar(conn, "SELECT COUNT(*) FROM fact_signal_candidates"),
        "fact_trades_by_class": rows(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class"),
        "fact_trades_by_settlement_status": rows(
            conn,
            "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
            "FROM fact_trades GROUP BY settlement_status",
        ),
        "fact_signal_candidate_coverage": rows(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        )[0],
        "orders_fill_join_check": rows(
            conn,
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status",
        ),
        "missing_bracket_rows": scalar(
            conn,
            "SELECT COUNT(*) FROM fact_trades WHERE settlement_status='missing_bracket'",
        ),
        "unsettled_rows": scalar(
            conn,
            "SELECT COUNT(*) FROM fact_trades WHERE settlement_status IS NULL OR settlement_status<>'settled'",
        ),
        "clob_fill_coverage_gate": gate,
    }


def load_base_rows(conn: sqlite3.Connection) -> tuple[int, int, list[dict[str, Any]]]:
    total = scalar(conn, "SELECT COUNT(*) FROM fact_signal_candidates")
    settled_decision = scalar(
        conn,
        "SELECT COUNT(*) FROM fact_signal_candidates "
        "WHERE settlement_status='settled' AND decision_window_missing=0 "
        "AND decision_snapshot_ts_utc IS NOT NULL",
    )
    yes_rows = rows(
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
          forecast_source,
          model_version,
          decision_hours_to_settle,
          decision_snapshot_ts_utc,
          model_p_yes,
          market_yes_price,
          yes_spread,
          eligible,
          paper_ordered,
          live_filled,
          settlement_status,
          final_yes,
          fact_built_at_utc
        FROM fact_signal_candidates
        WHERE side='BUY_YES'
          AND settlement_status='settled'
          AND decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND condition_id IS NOT NULL
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND market_yes_price > 0
          AND market_yes_price < 1
          AND final_yes IS NOT NULL
        """,
    )
    return total, settled_decision, yes_rows


def group_decision_sets(candidates: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in candidates:
        key = (str(row["city"]), str(row["event_date"]), str(row["decision_snapshot_ts_utc"]))
        bracket = str(row["bracket"])
        prev = grouped[key].get(bracket)
        if prev is None or float(row["model_p_yes"]) > float(prev["model_p_yes"]):
            grouped[key][bracket] = row
    out = []
    for by_bracket in grouped.values():
        items = sorted(by_bracket.values(), key=lambda item: bracket_sort_value(str(item["bracket"])))
        if len(items) >= 2:
            out.append(items)
    return out


def enumerate_adjacent_ranges(decision_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for items in decision_sets:
        mode_idx = max(range(len(items)), key=lambda idx: float(items[idx]["model_p_yes"]))
        raw_model_total = sum(float(row["model_p_yes"]) for row in items)
        raw_market_total = sum(float(row["market_yes_price"]) for row in items)
        model_norm = {
            str(row["bracket"]): safe_div(float(row["model_p_yes"]), raw_model_total) or 0.0 for row in items
        }
        market_norm = {
            str(row["bracket"]): safe_div(float(row["market_yes_price"]), raw_market_total) or 0.0 for row in items
        }
        for width in (2, 3):
            if len(items) < width:
                continue
            for start in range(0, len(items) - width + 1):
                end = start + width
                legs = items[start:end]
                base = items[0]
                model_mass = sum(model_norm[str(row["bracket"])] for row in legs)
                market_cost = sum(float(row["market_yes_price"]) for row in legs)
                market_mass_norm = sum(market_norm[str(row["bracket"])] for row in legs)
                payout = sum(float(row["final_yes"]) for row in legs)
                spreads = [row["yes_spread"] for row in legs]
                h_bucket = hour_bucket(base["decision_hours_to_settle"])
                decision_dt = scanner.parse_ts(str(base["decision_snapshot_ts_utc"]))
                out.append(
                    {
                        "candidate_id": (
                            f"forecast_first_adjacent_range|{base['city']}|{base['event_date']}|"
                            f"{base['decision_snapshot_ts_utc']}|adjacent{width}|{start}|{end}"
                        ),
                        "city": base["city"],
                        "city_pool": base["city_pool"],
                        "event_date": base["event_date"],
                        "target_date": base["event_date"],
                        "decision_snapshot_ts_utc": base["decision_snapshot_ts_utc"],
                        "decision_dt": decision_dt,
                        "decision_hours_to_settle": base["decision_hours_to_settle"],
                        "decision_hours_bucket": h_bucket,
                        "range_type": f"adjacent{width}",
                        "width": width,
                        "brackets": [row["bracket"] for row in legs],
                        "condition_ids": [row["condition_id"] for row in legs],
                        "market_ids": [row["market_id"] for row in legs],
                        "mode_bracket": items[mode_idx]["bracket"],
                        "mode_in_range": int(start <= mode_idx < end),
                        "model_mass": model_mass,
                        "market_mass_norm": market_mass_norm,
                        "market_cost": market_cost,
                        "model_minus_market_cost": model_mass - market_cost,
                        "model_minus_market_mass_norm": model_mass - market_mass_norm,
                        "settled_payout": payout,
                        "decision_proxy_cost": market_cost,
                        "decision_proxy_pnl": payout - market_cost,
                        "all_legs_have_price": int(all(row["market_yes_price"] is not None for row in legs)),
                        "max_yes_spread": max(float(value) for value in spreads if value is not None)
                        if all(value is not None for value in spreads)
                        else None,
                        "all_legs_have_spread": int(all(value is not None for value in spreads)),
                        "all_legs_eligible": int(all(int(row.get("eligible") or 0) for row in legs)),
                        "paper_ordered_legs": sum(int(row.get("paper_ordered") or 0) for row in legs),
                        "live_filled_legs": sum(int(row.get("live_filled") or 0) for row in legs),
                        "_legs": legs,
                    }
                )
    return [row for row in out if row["decision_dt"] is not None]


def attach_orderbook(rows_in: list[dict[str, Any]], orderbook_glob: str) -> dict[str, Any]:
    leg_candidates: list[dict[str, Any]] = []
    for row in rows_in:
        for idx, leg in enumerate(row["_legs"]):
            leg_candidates.append(
                {
                    "candidate_id": f"{row['candidate_id']}|{idx}",
                    "condition_id": leg["condition_id"],
                    "market_id": leg["market_id"],
                    "event_date": row["event_date"],
                    "city": row["city"],
                    "bracket": leg["bracket"],
                    "side": "BUY_YES",
                    "outcome": "yes",
                    "market_yes_price": leg["market_yes_price"],
                    "decision_entry_price": leg["market_yes_price"],
                    "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                    "decision_dt": row["decision_dt"],
                    "final_yes": leg["final_yes"],
                    "counterfactual_pnl": None,
                    "live_filled": leg["live_filled"],
                    "price_bucket": "",
                }
            )
    matched, coverage = scanner.match_time_aligned_orderbooks(leg_candidates, orderbook_glob)
    by_candidate = {row["candidate_id"]: row for row in matched}
    full = 0
    partial = 0
    for row in rows_in:
        cost = 0.0
        pnl = 0.0
        age = 0.0
        matched_legs = 0
        for idx in range(len(row["_legs"])):
            leg = by_candidate.get(f"{row['candidate_id']}|{idx}")
            if leg is None or leg.get("taker_cost_usd") is None or leg.get("taker_pnl_usd") is None:
                continue
            matched_legs += 1
            cost += float(leg["taker_cost_usd"])
            pnl += float(leg["taker_pnl_usd"])
            age += float(leg["orderbook_age_minutes"])
        if matched_legs == len(row["_legs"]):
            full += 1
            row["orderbook_taker_cost"] = cost
            row["orderbook_taker_pnl"] = pnl
            row["orderbook_avg_age_minutes"] = safe_div(age, len(row["_legs"]))
        elif matched_legs:
            partial += 1
    coverage.update(
        {
            "strategy_rows": len(rows_in),
            "fully_matched_strategy_rows": full,
            "partial_strategy_rows": partial,
            "fully_matched_strategy_rate": safe_div(full, len(rows_in)),
        }
    )
    return coverage


def tradable_proxy(rows_in: list[dict[str, Any]], spread_cap: float) -> list[dict[str, Any]]:
    return [
        row
        for row in rows_in
        if row["mode_in_range"]
        and row["decision_hours_bucket"] in HOUR_BUCKETS
        and row["all_legs_have_price"]
        and row["all_legs_have_spread"]
        and row["max_yes_spread"] is not None
        and float(row["max_yes_spread"]) <= spread_cap
    ]


def metric_rows(rows_in: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    if source == "decision_proxy":
        return [
            {**row, "eval_cost": row["decision_proxy_cost"], "eval_pnl": row["decision_proxy_pnl"]}
            for row in rows_in
        ]
    return [
        {**row, "eval_cost": row["orderbook_taker_cost"], "eval_pnl": row["orderbook_taker_pnl"]}
        for row in rows_in
        if row.get("orderbook_taker_cost") is not None and row.get("orderbook_taker_pnl") is not None
    ]


def profile_grid() -> list[dict[str, Any]]:
    out = []
    for width in (2, 3):
        for mass_min in MASS_THRESHOLDS:
            for cost_max in COST_THRESHOLDS:
                for edge_min in EDGE_THRESHOLDS:
                    for bucket in HOUR_BUCKETS:
                        out.append(
                            {
                                "range_type": f"adjacent{width}",
                                "model_mass_min": mass_min,
                                "market_cost_max": cost_max,
                                "edge_min": edge_min,
                                "decision_hours_bucket": bucket,
                            }
                        )
    return out


def apply_profile(rows_in: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows_in
        if row["range_type"] == profile["range_type"]
        and row["decision_hours_bucket"] == profile["decision_hours_bucket"]
        and float(row["model_mass"]) >= float(profile["model_mass_min"])
        and float(row["market_cost"]) <= float(profile["market_cost_max"])
        and float(row["model_minus_market_cost"]) >= float(profile["edge_min"])
    ]


def baseline_rows(rows_in: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows_in
        if row["range_type"] == profile["range_type"]
        and row["decision_hours_bucket"] == profile["decision_hours_bucket"]
    ]


def summarize(rows_in: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(float(row.get("eval_cost") or 0.0) for row in rows_in)
    pnl = sum(float(row.get("eval_pnl") or 0.0) for row in rows_in)
    by_date: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "pnl": 0.0})
    for row in rows_in:
        slot = by_date[str(row["event_date"])]
        slot["cost"] += float(row.get("eval_cost") or 0.0)
        slot["pnl"] += float(row.get("eval_pnl") or 0.0)
    top5 = sorted(by_date.items(), key=lambda item: item[1]["pnl"], reverse=True)[:5]
    top5_set = {date for date, _ in top5}
    drop_cost = sum(value["cost"] for date, value in by_date.items() if date not in top5_set)
    drop_pnl = sum(value["pnl"] for date, value in by_date.items() if date not in top5_set)
    return {
        "rows": len(rows_in),
        "active_dates": len(by_date),
        "active_city_days": len({(row["city"], row["event_date"]) for row in rows_in}),
        "cost": cost,
        "pnl": pnl,
        "roi": safe_div(pnl, cost),
        "avg_model_mass": safe_div(sum(float(row["model_mass"]) for row in rows_in), len(rows_in)),
        "avg_market_cost": safe_div(sum(float(row["market_cost"]) for row in rows_in), len(rows_in)),
        "avg_edge": safe_div(sum(float(row["model_minus_market_cost"]) for row in rows_in), len(rows_in)),
        "drop_top5_event_dates": sorted(top5_set),
        "drop_top5_pnl": drop_pnl,
        "drop_top5_roi": safe_div(drop_pnl, drop_cost),
    }


def roi(rows_in: list[dict[str, Any]]) -> float | None:
    s = summarize(rows_in)
    return s["roi"]


def bootstrap(selected: list[dict[str, Any]], baseline: list[dict[str, Any]], iters: int, seed: int) -> dict[str, Any]:
    selected_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    baseline_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        selected_by_date[str(row["event_date"])].append(row)
    for row in baseline:
        baseline_by_date[str(row["event_date"])].append(row)
    dates = sorted(set(selected_by_date) | set(baseline_by_date))
    if not dates:
        return {"roi_ci95": [None, None], "excess_roi_ci95": [None, None]}
    rng = random.Random(seed)
    roi_samples: list[float] = []
    excess_samples: list[float] = []
    for _ in range(iters):
        sel_sample: list[dict[str, Any]] = []
        base_sample: list[dict[str, Any]] = []
        for _ in dates:
            pick = rng.choice(dates)
            sel_sample.extend(selected_by_date.get(pick, []))
            base_sample.extend(baseline_by_date.get(pick, []))
        sel_roi = roi(sel_sample)
        base_roi = roi(base_sample)
        if sel_roi is not None and math.isfinite(sel_roi):
            roi_samples.append(sel_roi)
        if sel_roi is not None and base_roi is not None and math.isfinite(sel_roi - base_roi):
            excess_samples.append(sel_roi - base_roi)
    return {"roi_ci95": ci95(roi_samples), "excess_roi_ci95": ci95(excess_samples)}


def evaluate(rows_in: list[dict[str, Any]], profile: dict[str, Any], iters: int, seed: int) -> dict[str, Any]:
    selected = apply_profile(rows_in, profile)
    baseline = baseline_rows(rows_in, profile)
    selected_summary = summarize(selected)
    baseline_summary = summarize(baseline)
    selected_roi = selected_summary["roi"]
    baseline_roi = baseline_summary["roi"]
    excess = None if selected_roi is None or baseline_roi is None else selected_roi - baseline_roi
    boot = bootstrap(selected, baseline, iters=iters, seed=seed)
    return {
        "profile": profile,
        "selected": selected_summary,
        "baseline": baseline_summary,
        "excess_roi": excess,
        "roi_ci95_cluster_by_event_date": boot["roi_ci95"],
        "excess_roi_ci95_cluster_by_event_date": boot["excess_roi_ci95"],
    }


def gate(train_eval: dict[str, Any] | None, holdout_eval: dict[str, Any] | None) -> dict[str, Any]:
    reasons = []
    if not train_eval or not holdout_eval:
        return {
            "significance": "NA",
            "baseline": "NA",
            "forward": "NA",
            "verdict": "inconclusive",
            "reasons": ["missing_train_or_holdout_eval"],
        }
    roi_ci = train_eval["roi_ci95_cluster_by_event_date"]
    excess_ci = train_eval["excess_roi_ci95_cluster_by_event_date"]
    significance = "PASS" if roi_ci[0] is not None and roi_ci[0] > 0 else "FAIL"
    baseline = "PASS" if excess_ci[0] is not None and excess_ci[0] > 0 else "FAIL"
    holdout_roi_ci = holdout_eval["roi_ci95_cluster_by_event_date"]
    holdout_excess_ci = holdout_eval["excess_roi_ci95_cluster_by_event_date"]
    forward = (
        "PASS"
        if holdout_roi_ci[0] is not None
        and holdout_roi_ci[0] > 0
        and holdout_excess_ci[0] is not None
        and holdout_excess_ci[0] > 0
        else "FAIL"
    )
    if significance == "FAIL":
        reasons.append("train_roi_ci_crosses_or_below_zero")
    if baseline == "FAIL":
        reasons.append("train_excess_roi_ci_crosses_or_below_zero")
    if forward == "FAIL":
        reasons.append("holdout_roi_or_excess_ci_crosses_or_below_zero")
    verdict = "confirmed" if significance == baseline == forward == "PASS" else "inconclusive"
    return {
        "significance": significance,
        "baseline": baseline,
        "forward": forward,
        "verdict": verdict,
        "reasons": reasons,
    }


def split_dates(dates: list[str], train_frac: float) -> tuple[set[str], set[str], str | None]:
    ordered = sorted(dates)
    if len(ordered) < 2:
        return set(ordered), set(), None
    split_idx = max(1, min(len(ordered) - 1, int(math.floor(len(ordered) * train_frac))))
    return set(ordered[:split_idx]), set(ordered[split_idx:]), ordered[split_idx]


def run_source(
    rows_in: list[dict[str, Any]],
    *,
    source: str,
    train_dates: set[str],
    holdout_dates: set[str],
    iters: int,
    seed: int,
) -> dict[str, Any]:
    train_rows = [row for row in rows_in if row["event_date"] in train_dates]
    holdout_rows = [row for row in rows_in if row["event_date"] in holdout_dates]
    train_evals = []
    for idx, profile in enumerate(profile_grid()):
        selected = apply_profile(train_rows, profile)
        if not selected:
            continue
        train_evals.append(evaluate(train_rows, profile, iters, seed + idx * 17))
    train_evals.sort(
        key=lambda item: (
            item["excess_roi"] if item["excess_roi"] is not None else -999.0,
            item["selected"]["roi"] if item["selected"]["roi"] is not None else -999.0,
            item["selected"]["rows"],
        ),
        reverse=True,
    )
    top = train_evals[0] if train_evals else None
    holdout_eval = evaluate(holdout_rows, top["profile"], iters, seed + 99991) if top else None
    g = gate(top, holdout_eval)
    return {
        "source": source,
        "profiles_preregistered": len(profile_grid()),
        "profiles_with_train_rows": len(train_evals),
        "train_rows_available": len(train_rows),
        "holdout_rows_available": len(holdout_rows),
        "train_top_by_excess_roi": top,
        "holdout_evaluation": holdout_eval,
        "gates": g,
    }


def filter_funnel(
    *,
    total_fact_rows: int,
    settled_decision_rows: int,
    yes_rows: list[dict[str, Any]],
    decision_sets: list[list[dict[str, Any]]],
    all_ranges: list[dict[str, Any]],
    tradable_rows: list[dict[str, Any]],
    orderbook_coverage: dict[str, Any],
    train_dates: set[str],
    holdout_dates: set[str],
) -> dict[str, Any]:
    after_filters = {
        "yes_leg_rows": len(yes_rows),
        "around_model_mode": sum(1 for row in all_ranges if row["mode_in_range"]),
        "all_legs_have_price": sum(1 for row in all_ranges if row["mode_in_range"] and row["all_legs_have_price"]),
        "all_legs_have_spread": sum(
            1 for row in all_ranges if row["mode_in_range"] and row["all_legs_have_price"] and row["all_legs_have_spread"]
        ),
        "spread_cap": len(tradable_rows),
        "model_mass_thresholds": {
            str(th): sum(1 for row in tradable_rows if float(row["model_mass"]) >= th) for th in MASS_THRESHOLDS
        },
        "market_cost_thresholds": {
            str(th): sum(1 for row in tradable_rows if float(row["market_cost"]) <= th) for th in COST_THRESHOLDS
        },
        "edge_thresholds": {
            str(th): sum(1 for row in tradable_rows if float(row["model_minus_market_cost"]) >= th)
            for th in EDGE_THRESHOLDS
        },
        "hour_buckets": {
            bucket: sum(1 for row in tradable_rows if row["decision_hours_bucket"] == bucket) for bucket in HOUR_BUCKETS
        },
    }
    return {
        "fact_signal_candidates_rows": total_fact_rows,
        "settled_decision_window_present_rows": settled_decision_rows,
        "decision_sets_count": len(decision_sets),
        "enumerated_adjacent2_adjacent3_ranges_count": len(all_ranges),
        "after_each_strategy_filter_count": after_filters,
        "orderbook_matched_count": {
            "strategy_rows": orderbook_coverage.get("strategy_rows"),
            "fully_matched_strategy_rows": orderbook_coverage.get("fully_matched_strategy_rows"),
            "partial_strategy_rows": orderbook_coverage.get("partial_strategy_rows"),
            "matched_candidate_rows": orderbook_coverage.get("matched_candidate_rows"),
            "candidate_rows": orderbook_coverage.get("candidate_rows"),
        },
        "train_holdout": {
            "train_rows": sum(1 for row in tradable_rows if row["event_date"] in train_dates),
            "holdout_rows": sum(1 for row in tradable_rows if row["event_date"] in holdout_dates),
            "train_active_dates": len(train_dates),
            "holdout_active_dates": len(holdout_dates),
            "train_date_min": min(train_dates) if train_dates else None,
            "train_date_max": max(train_dates) if train_dates else None,
            "holdout_date_min": min(holdout_dates) if holdout_dates else None,
            "holdout_date_max": max(holdout_dates) if holdout_dates else None,
        },
    }


def compact_eval(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {}
    selected = item["selected"]
    baseline = item["baseline"]
    return {
        "profile": item["profile"],
        "rows": selected["rows"],
        "active_dates": selected["active_dates"],
        "roi": selected["roi"],
        "roi_ci": item["roi_ci95_cluster_by_event_date"],
        "baseline_roi": baseline["roi"],
        "excess_roi": item["excess_roi"],
        "excess_ci": item["excess_roi_ci95_cluster_by_event_date"],
        "top5_removed_roi": selected["drop_top5_roi"],
        "cost": selected["cost"],
        "pnl": selected["pnl"],
    }


def table(rows_in: list[list[Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows_in:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def write_markdown(report: dict[str, Any], path: Path) -> None:
    ff = report["filter_funnel"]
    self_check = report["data_self_check"]
    gate = (self_check.get("clob_fill_coverage_gate") or {}).get("gate_pass")
    rows_out = []
    for key in ("full_opportunity", "old_eligible_control"):
        for source in ("decision_proxy", "time_aligned_orderbook"):
            result = report["results"][key][source]
            train = compact_eval(result.get("train_top_by_excess_roi"))
            holdout = compact_eval(result.get("holdout_evaluation"))
            rows_out.append(
                [
                    key,
                    source,
                    result["gates"]["verdict"],
                    f"{result['gates']['significance']}/{result['gates']['baseline']}/{result['gates']['forward']}",
                    train.get("rows", 0),
                    pct(train.get("roi")),
                    pct(train.get("excess_roi")),
                    holdout.get("rows", 0),
                    pct(holdout.get("roi")),
                    pct(holdout.get("excess_roi")),
                    pct(holdout.get("top5_removed_roi")),
                ]
            )
    conclusion = report["conclusion"]
    lines = [
        "# Forecast-first adjacent2/3 Range RV",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`runtime/weather.db` 的 `fact_signal_candidates` / `fact_trades`；orderbook 仅用于 executable price replay。",
        f"- DB mtime UTC：`{self_check['db_last_modified_utc']}`。",
        f"- fact built at：trades `{self_check['fact_trades_max_built_at_utc']}`；candidates `{self_check['fact_signal_candidates_max_built_at_utc']}`。",
        f"- fact rows：trades `{self_check['fact_trades_rows']}`；signal candidates `{self_check['fact_signal_candidates_rows']}`。",
        f"- unsettled rows：`{self_check['unsettled_rows']}`；missing_bracket rows：`{self_check['missing_bracket_rows']}`。",
        f"- CLOB coverage gate：`{gate}`。本报告不是 live_real PnL/ROI 发布；该 gate 只作为数据完整性状态声明。",
        "",
        "## Target Metric",
        "",
        "`forecast_first_adjacent_range_rv_alpha` = 在每个 `city + event_date + decision_snapshot_ts_utc` decision set 内，先由 forecast distribution 的 mode 确定相邻 2/3 个 YES bracket range，再评估 model mass 相对 executable market cost 的超额 ROI。",
        "",
        "## Filter Funnel",
        "",
        table(
            [
                ["fact_signal_candidates rows", ff["fact_signal_candidates_rows"]],
                ["settled + decision_window present rows", ff["settled_decision_window_present_rows"]],
                ["YES leg rows used for range enumeration", ff["after_each_strategy_filter_count"]["yes_leg_rows"]],
                ["decision_sets count", ff["decision_sets_count"]],
                ["enumerated adjacent2 / adjacent3 ranges", ff["enumerated_adjacent2_adjacent3_ranges_count"]],
                ["around model mode", ff["after_each_strategy_filter_count"]["around_model_mode"]],
                ["all legs have price", ff["after_each_strategy_filter_count"]["all_legs_have_price"]],
                ["all legs have spread", ff["after_each_strategy_filter_count"]["all_legs_have_spread"]],
                ["spread cap", ff["after_each_strategy_filter_count"]["spread_cap"]],
                ["orderbook fully matched strategy rows", ff["orderbook_matched_count"]["fully_matched_strategy_rows"]],
            ],
            ["step", "count"],
        ),
        "",
        f"- mass thresholds counts：`{ff['after_each_strategy_filter_count']['model_mass_thresholds']}`",
        f"- market cost thresholds counts：`{ff['after_each_strategy_filter_count']['market_cost_thresholds']}`",
        f"- edge thresholds counts：`{ff['after_each_strategy_filter_count']['edge_thresholds']}`",
        f"- hour buckets counts：`{ff['after_each_strategy_filter_count']['hour_buckets']}`",
        f"- train rows/date：`{ff['train_holdout']['train_rows']}` rows, `{ff['train_holdout']['train_active_dates']}` dates `{ff['train_holdout']['train_date_min']}` to `{ff['train_holdout']['train_date_max']}`。",
        f"- holdout rows/date：`{ff['train_holdout']['holdout_rows']}` rows, `{ff['train_holdout']['holdout_active_dates']}` dates `{ff['train_holdout']['holdout_date_min']}` to `{ff['train_holdout']['holdout_date_max']}`。",
        "",
        "## Results",
        "",
        table(
            rows_out,
            [
                "slice",
                "source",
                "verdict",
                "gates",
                "train rows",
                "train ROI",
                "train excess",
                "holdout rows",
                "holdout ROI",
                "holdout excess",
                "holdout top5 removed ROI",
            ],
        ),
        "",
        "## Selected Profiles",
        "",
    ]
    for key in ("full_opportunity", "old_eligible_control"):
        for source in ("decision_proxy", "time_aligned_orderbook"):
            result = report["results"][key][source]
            train = compact_eval(result.get("train_top_by_excess_roi"))
            holdout = compact_eval(result.get("holdout_evaluation"))
            lines.extend(
                [
                    f"### {key} / {source}",
                    "",
                    f"- profiles preregistered/tested on train：`{result['profiles_preregistered']}` / `{result['profiles_with_train_rows']}`。",
                    f"- selected profile：`{train.get('profile')}`。",
                    f"- train ROI：{pct(train.get('roi'))} CI {ci_text(train.get('roi_ci'))}；baseline {pct(train.get('baseline_roi'))}；excess {pct(train.get('excess_roi'))} CI {ci_text(train.get('excess_ci'))}；top5 removed {pct(train.get('top5_removed_roi'))}。",
                    f"- holdout ROI：{pct(holdout.get('roi'))} CI {ci_text(holdout.get('roi_ci'))}；baseline {pct(holdout.get('baseline_roi'))}；excess {pct(holdout.get('excess_roi'))} CI {ci_text(holdout.get('excess_ci'))}。",
                    f"- holdout top5 removed ROI：{pct(holdout.get('top5_removed_roi'))}；active dates `{holdout.get('active_dates')}`。",
                    f"- gates：`{result['gates']['significance']}/{result['gates']['baseline']}/{result['gates']['forward']}` -> `{result['gates']['verdict']}`；reasons `{result['gates'].get('reasons')}`。",
                    "",
                ]
            )
    lines.extend(
        [
            "## 三门结论",
            "",
            f"- significance：`{conclusion['significance']}`；baseline：`{conclusion['baseline']}`；forward：`{conclusion['forward']}`。",
            f"- final conclusion：`{conclusion['label']}`。",
            f"- live action：`{conclusion['live_action']}`。",
            "",
            "## 8 环覆盖自检",
            "",
            "- 1 描述性绩效切片：覆盖，基于 `fact_signal_candidates` counterfactual range rows。",
            "- 2 统计推断：覆盖，bootstrap 按 `event_date` cluster。",
            "- 3 信号判别：部分覆盖，用 forecast mode/mass 和 model-cost edge，不做额外 IC。",
            "- 4 概率分布评估：部分覆盖，仅检验 range mass，不做全分布校准。",
            "- 5 执行微结构：覆盖，可执行版本使用 time-aligned orderbook all-leg match。",
            "- 6 容量：未覆盖，仅使用 best ask，不做 depth/size 放大。",
            "- 7 组合相关性：部分覆盖，event_date cluster bootstrap。",
            "- 8 基准/反事实：覆盖，baseline 是同 width/hour bucket 的 around-mode range before mass/cost/edge filters。",
            "",
            "## 样本过滤误伤风险",
            "",
            f"- 风险判断：`{report['filter_injury_risk']['label']}`。",
            f"- 依据：{report['filter_injury_risk']['detail']}",
            "",
            "## Notes",
            "",
            "- 本任务是 counterfactual research，未改 N100/live 配置。",
            "- 旧单腿 `eligible` 没有作为主分析硬门，只作为 old eligible control slice。",
            "- 不按城市或日期事后挑 winner；profile 网格按 width/mass/cost/edge/hour bucket 预注册。",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def strip_private(rows_in: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows_in:
        out.append({key: value for key, value in row.items() if key not in {"_legs", "decision_dt"}})
    return out


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    conn = connect(str(db_path))
    total, settled_decision, yes_rows = load_base_rows(conn)
    decision_sets = group_decision_sets(yes_rows)
    all_ranges = enumerate_adjacent_ranges(decision_sets)
    tradable = tradable_proxy(all_ranges, args.spread_cap)
    orderbook_coverage = attach_orderbook(tradable, args.orderbook_glob)
    dates = sorted({row["event_date"] for row in tradable})
    train_dates, holdout_dates, split_date = split_dates(dates, args.train_frac)

    proxy_rows = metric_rows(tradable, "decision_proxy")
    orderbook_rows = metric_rows(tradable, "time_aligned_orderbook")
    old_proxy_rows = [row for row in proxy_rows if row["all_legs_eligible"]]
    old_orderbook_rows = [row for row in orderbook_rows if row["all_legs_eligible"]]
    results = {
        "full_opportunity": {
            "decision_proxy": run_source(
                proxy_rows,
                source="decision_proxy",
                train_dates=train_dates,
                holdout_dates=holdout_dates,
                iters=args.bootstrap_iters,
                seed=args.seed,
            ),
            "time_aligned_orderbook": run_source(
                orderbook_rows,
                source="time_aligned_orderbook",
                train_dates=train_dates,
                holdout_dates=holdout_dates,
                iters=args.bootstrap_iters,
                seed=args.seed + 100000,
            ),
        },
        "old_eligible_control": {
            "decision_proxy": run_source(
                old_proxy_rows,
                source="decision_proxy",
                train_dates=train_dates,
                holdout_dates=holdout_dates,
                iters=args.bootstrap_iters,
                seed=args.seed + 200000,
            ),
            "time_aligned_orderbook": run_source(
                old_orderbook_rows,
                source="time_aligned_orderbook",
                train_dates=train_dates,
                holdout_dates=holdout_dates,
                iters=args.bootstrap_iters,
                seed=args.seed + 300000,
            ),
        },
    }
    primary = results["full_opportunity"]["time_aligned_orderbook"]
    primary_gates = primary["gates"]
    label = primary_gates["verdict"]
    holdout = primary.get("holdout_evaluation") or {}
    holdout_ci = holdout.get("roi_ci95_cluster_by_event_date") or [None, None]
    holdout_excess_ci = holdout.get("excess_roi_ci95_cluster_by_event_date") or [None, None]
    if (
        label != "confirmed"
        and holdout_ci[1] is not None
        and holdout_ci[1] < 0
        and holdout_excess_ci[1] is not None
        and holdout_excess_ci[1] < 0
    ):
        label = "fail"
    around_mode_rows = sum(1 for row in all_ranges if row["mode_in_range"])
    tradable_loss = 1.0 - safe_div(len(tradable), around_mode_rows) if around_mode_rows else None
    orderbook_loss = 1.0 - safe_div(len(orderbook_rows), len(proxy_rows)) if proxy_rows else None
    worst_loss = max(value for value in (tradable_loss, orderbook_loss) if value is not None)
    injury_label = "high" if worst_loss > 0.30 else "medium" if worst_loss > 0.10 else "low"
    report = {
        "metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "script": str(Path(__file__).relative_to(ROOT)),
            "target_metric": "forecast_first_adjacent_range_rv_alpha",
            "counterfactual_research_only": True,
            "spread_cap": args.spread_cap,
            "train_frac": args.train_frac,
            "split_date": split_date,
            "bootstrap_cluster": "event_date",
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
        },
        "data_self_check": data_self_check(conn, db_path),
        "filter_funnel": filter_funnel(
            total_fact_rows=total,
            settled_decision_rows=settled_decision,
            yes_rows=yes_rows,
            decision_sets=decision_sets,
            all_ranges=all_ranges,
            tradable_rows=tradable,
            orderbook_coverage=orderbook_coverage,
            train_dates=train_dates,
            holdout_dates=holdout_dates,
        ),
        "orderbook_coverage": orderbook_coverage,
        "results": results,
        "conclusion": {
            "label": label,
            "basis": "full_opportunity/time_aligned_orderbook",
            "significance": primary_gates["significance"],
            "baseline": primary_gates["baseline"],
            "forward": primary_gates["forward"],
            "live_action": "none; three gates must pass before any live action",
        },
        "filter_injury_risk": {
            "label": injury_label,
            "detail": (
                f"tradable proxy rows {len(tradable)} / around-mode rows {around_mode_rows}; "
                f"executable orderbook rows {len(orderbook_rows)} / decision-proxy rows {len(proxy_rows)}. "
                "The main injury risk is the all-leg spread requirement and spread cap; orderbook all-leg matching did not remove additional rows in this run."
            ),
        },
        "sample_rows": strip_private(tradable[:20]),
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    write_markdown(report, out_md)
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "conclusion": label}, ensure_ascii=False))


if __name__ == "__main__":
    main()
