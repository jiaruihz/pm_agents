#!/usr/bin/env python3
"""Matched baselines for forecast-quality adjacent3 weather range research.

This script answers whether the frozen `forecast_quality_medium + adjacent3`
shadow candidate has excess value over simple matched baselines:

1. no-filter model-mode adjacent3 on the same opportunity universe.
2. market-mode adjacent3 on the same decision set/time/city.

It is local research only. It reads runtime/weather.db.fact_signal_candidates
and uses time-aligned orderbook logic through research_range_rv_scanner.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = ROOT / "scripts" / "analysis" / "market_structure_edge"
sys.path.append(str(SCRIPT_DIR))

import research_range_rv_scanner as scanner  # noqa: E402


DB_DEFAULT = ROOT / "runtime" / "weather.db"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-adjacent3-quality-matched-baseline-v0.json"
OUT_JSONL_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-adjacent3-quality-matched-baseline-v0.jsonl"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-adjacent3-quality-matched-baseline-v0.md"
ORDERBOOK_GLOB_DEFAULT = scanner.ORDERBOOK_GLOB_DEFAULT
TARGET_METRIC = "forecast_quality_adjacent3_matched_baseline_excess"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-jsonl", default=str(OUT_JSONL_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--orderbook-glob", default=str(ORDERBOOK_GLOB_DEFAULT))
    parser.add_argument("--market-cost-cap", type=float, default=0.85)
    parser.add_argument("--same-cost-tolerance", type=float, default=0.05)
    parser.add_argument("--min-edge", type=float, default=0.0)
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260610)
    return parser.parse_args()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def safe_div(num: float, den: float) -> float | None:
    return num / den if den else None


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def table(headers: list[str], rows_in: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows_in:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


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


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def normalize(values: list[float]) -> list[float]:
    total = sum(max(value, 0.0) for value in values)
    if total <= 0:
        return [0.0 for _ in values]
    return [max(value, 0.0) / total for value in values]


def entropy(probs: list[float]) -> float:
    values = [p for p in probs if p > 0]
    if not values:
        return 0.0
    denom = math.log(len(probs)) if len(probs) > 1 else 1.0
    return -sum(p * math.log(p) for p in values) / denom


def data_self_check(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    return {
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat()
        if db_path.exists()
        else None,
        "max_fact_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class_distribution": rows(
            conn, "SELECT trade_class, COUNT(*) AS n FROM fact_trades GROUP BY trade_class ORDER BY trade_class"
        ),
        "settlement_status_distribution": rows(
            conn,
            "SELECT settlement_status, COUNT(*) AS n FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "candidate_coverage": rows(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        )[0],
        "order_fill_coverage": rows(
            conn,
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
        ),
        "candidate_event_range": rows(
            conn,
            "SELECT MIN(event_date) AS min_event_date, MAX(event_date) AS max_event_date, "
            "COUNT(DISTINCT event_date) AS event_dates, COUNT(*) AS rows FROM fact_signal_candidates",
        )[0],
    }


def load_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows(
        conn,
        """
        SELECT
          candidate_id,
          condition_id,
          market_id,
          side,
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
          live_filled,
          final_yes,
          settlement_status,
          decision_window_missing,
          fact_built_at_utc
        FROM fact_signal_candidates
        WHERE side='BUY_YES'
          AND decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND condition_id IS NOT NULL
          AND market_id IS NOT NULL
          AND event_date IS NOT NULL
          AND city IS NOT NULL
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND market_yes_price > 0
          AND market_yes_price < 1
          AND eligible=1
        """,
    )


def selected_window(center_idx: int, n: int, width: int = 3) -> tuple[int, int]:
    start = max(0, min(center_idx - 1, n - width))
    return start, start + width


def build_decision_sets(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in candidates:
        key = (
            str(row["city"]),
            str(row["event_date"]),
            str(row["forecast_source"]),
            str(row["model_version"]),
            str(row["decision_snapshot_ts_utc"]),
        )
        grouped[key][str(row["bracket"])] = row

    out: list[dict[str, Any]] = []
    for key, by_bracket in grouped.items():
        legs = sorted(by_bracket.values(), key=lambda row: scanner.bracket_sort_value(str(row["bracket"])))
        if len(legs) < 3:
            continue
        model = normalize([float(row["model_p_yes"]) for row in legs])
        market = normalize([float(row["market_yes_price"]) for row in legs])
        if not any(model) or not any(market):
            continue
        model_mode_i = max(range(len(model)), key=lambda idx: model[idx])
        market_mode_i = max(range(len(market)), key=lambda idx: market[idx])
        model_start, model_end = selected_window(model_mode_i, len(legs))
        market_start, market_end = selected_window(market_mode_i, len(legs))
        selected = legs[model_start:model_end]
        selected_labels_ready = all(
            row.get("settlement_status") == "settled" and row.get("final_yes") is not None for row in selected
        )
        final_hit = None
        if selected_labels_ready:
            final_hit = int(any(float(row["final_yes"]) >= 0.5 for row in selected))
        model_mass = sum(model[model_start:model_end])
        market_cost = sum(float(row["market_yes_price"]) for row in selected)
        out.append(
            {
                "decision_set_id": "|".join(key),
                "city": key[0],
                "event_date": key[1],
                "target_date": key[1],
                "forecast_source": key[2],
                "model_version": key[3],
                "decision_snapshot_ts_utc": key[4],
                "decision_dt": scanner.parse_ts(key[4]),
                "decision_hours_to_settle": selected[0]["decision_hours_to_settle"],
                "n_brackets": len(legs),
                "legs": legs,
                "model_probs_norm": model,
                "market_probs_norm": market,
                "model_mode_i": model_mode_i,
                "market_mode_i": market_mode_i,
                "model_adjacent3_start": model_start,
                "model_adjacent3_end": model_end - 1,
                "market_adjacent3_start": market_start,
                "market_adjacent3_end": market_end - 1,
                "model_adjacent3_legs": selected,
                "market_adjacent3_legs": legs[market_start:market_end],
                "model_adjacent3_mass": model_mass,
                "model_adjacent3_market_cost": market_cost,
                "model_adjacent3_edge": model_mass - market_cost,
                "model_entropy": entropy(model),
                "model_mode_probability": model[model_mode_i],
                "model_tail_mass_outside_adjacent3": 1.0 - model_mass,
                "model_market_l1_gap": sum(abs(a - b) for a, b in zip(model, market)),
                "model_adjacent3_final_hit": final_hit,
                "model_adjacent3_labels_ready": selected_labels_ready,
            }
        )
    return [row for row in out if row["decision_dt"] is not None]


def split_dates(decision_sets: list[dict[str, Any]]) -> dict[str, Any]:
    settled_dates = sorted(
        {
            str(row["event_date"])
            for row in decision_sets
            if row.get("model_adjacent3_labels_ready") and row.get("model_adjacent3_final_hit") is not None
        }
    )
    if len(settled_dates) <= 1:
        cut = len(settled_dates)
    else:
        cut = max(1, min(len(settled_dates) - 1, int(math.floor(len(settled_dates) * 0.70))))
    train_dates = set(settled_dates[:cut])
    holdout_dates = set(settled_dates[cut:])
    return {
        "train_dates": train_dates,
        "holdout_dates": holdout_dates,
        "train_start": min(train_dates) if train_dates else None,
        "train_end": max(train_dates) if train_dates else None,
        "holdout_start": min(holdout_dates) if holdout_dates else None,
        "holdout_end": max(holdout_dates) if holdout_dates else None,
    }


def add_quality(decision_sets: list[dict[str, Any]], train_dates: set[str]) -> dict[str, Any]:
    train = [row for row in decision_sets if str(row["event_date"]) in train_dates]
    adj3_med = percentile([float(row["model_adjacent3_mass"]) for row in train], 0.50) or 0.0
    tail_med = percentile([float(row["model_tail_mass_outside_adjacent3"]) for row in train], 0.50) or 1.0
    entropy_med = percentile([float(row["model_entropy"]) for row in train], 0.50) or 1.0
    for row in decision_sets:
        row["quality_medium"] = int(
            float(row["model_adjacent3_mass"]) >= adj3_med
            and float(row["model_tail_mass_outside_adjacent3"]) <= tail_med
        )
        row["quality_low_uncertainty"] = int(
            row["quality_medium"] == 1 and float(row["model_entropy"]) <= entropy_med
        )
    return {
        "adjacent3_mass_train_median": adj3_med,
        "tail_mass_train_median": tail_med,
        "entropy_train_median": entropy_med,
    }


def selected_pnl(legs: list[dict[str, Any]]) -> tuple[float | None, float | None, int | None]:
    if not all(row.get("settlement_status") == "settled" and row.get("final_yes") is not None for row in legs):
        return None, None, None
    cost = sum(float(row["market_yes_price"]) for row in legs)
    payout = 1.0 if any(float(row["final_yes"]) >= 0.5 for row in legs) else 0.0
    return cost, payout - cost, int(payout)


def make_eval_row(rule_id: str, row: dict[str, Any], legs: list[dict[str, Any]]) -> dict[str, Any]:
    cost, pnl, hit = selected_pnl(legs)
    model_idxs = [row["legs"].index(leg) for leg in legs]
    model_mass = sum(row["model_probs_norm"][idx] for idx in model_idxs)
    market_cost = sum(float(leg["market_yes_price"]) for leg in legs)
    return {
        "range_id": f"{rule_id}|{row['decision_set_id']}",
        "rule_id": rule_id,
        "decision_set_id": row["decision_set_id"],
        "city": row["city"],
        "event_date": row["event_date"],
        "target_date": row["target_date"],
        "forecast_source": row["forecast_source"],
        "model_version": row["model_version"],
        "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
        "decision_dt": row["decision_dt"],
        "decision_hours_to_settle": row["decision_hours_to_settle"],
        "leg_side": "BUY_YES",
        "n_legs": len(legs),
        "brackets": [str(leg["bracket"]) for leg in legs],
        "condition_ids": [str(leg["condition_id"]) for leg in legs],
        "model_prob_sum": model_mass,
        "market_prob_sum": market_cost,
        "range_edge": model_mass - market_cost,
        "model_entropy": row["model_entropy"],
        "model_mode_probability": row["model_mode_probability"],
        "model_tail_mass_outside_adjacent3": row["model_tail_mass_outside_adjacent3"],
        "model_market_l1_gap": row["model_market_l1_gap"],
        "quality_medium": row["quality_medium"],
        "quality_low_uncertainty": row["quality_low_uncertainty"],
        "settlement_status": "settled" if cost is not None else "unsettled_or_unusable",
        "final_hit": hit,
        "decision_proxy_cost": cost,
        "decision_proxy_pnl": pnl,
        "_legs": legs,
    }


def aggregate_baselines(eval_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in eval_rows:
        if row["rule_id"] != "same_cost_random_adjacent3_candidate_for_medium":
            continue
        grouped[(row["rule_id"], row["decision_set_id"])].append(row)

    synthetic: list[dict[str, Any]] = []
    for (_rule_id, decision_set_id), items in grouped.items():
        first = items[0]
        settled_items = [row for row in items if row.get("decision_proxy_cost") is not None]
        orderbook_items = [row for row in items if row.get("orderbook_taker_cost") is not None]
        row = {
            key: value
            for key, value in first.items()
            if key not in {"range_id", "rule_id", "brackets", "condition_ids", "n_legs", "_legs"}
        }
        row["range_id"] = f"same_cost_random_adjacent3_for_medium|{decision_set_id}"
        row["rule_id"] = "same_cost_random_adjacent3_for_medium"
        row["n_legs"] = 3
        row["baseline_samples_per_decision"] = len(items)
        row["baseline_orderbook_samples_per_decision"] = len(orderbook_items)
        row["brackets"] = ["baseline_mean"]
        row["condition_ids"] = []
        row["settlement_status"] = "settled" if settled_items else "unsettled_or_unusable"
        if settled_items:
            row["decision_proxy_cost"] = sum(float(item["decision_proxy_cost"]) for item in settled_items) / len(
                settled_items
            )
            row["decision_proxy_pnl"] = sum(float(item["decision_proxy_pnl"]) for item in settled_items) / len(
                settled_items
            )
        else:
            row["decision_proxy_cost"] = None
            row["decision_proxy_pnl"] = None
        if orderbook_items:
            row["orderbook_taker_cost"] = sum(float(item["orderbook_taker_cost"]) for item in orderbook_items) / len(
                orderbook_items
            )
            row["orderbook_taker_pnl"] = sum(float(item["orderbook_taker_pnl"]) for item in orderbook_items) / len(
                orderbook_items
            )
            row["orderbook_maker_proxy_cost"] = None
            row["orderbook_maker_proxy_pnl"] = None
            row["orderbook_avg_age_minutes"] = sum(
                float(item.get("orderbook_avg_age_minutes") or 0.0) for item in orderbook_items
            ) / len(orderbook_items)
            row["price_source"] = "time_aligned_orderbook_baseline_mean"
        else:
            row["orderbook_taker_cost"] = None
            row["orderbook_taker_pnl"] = None
            row["orderbook_maker_proxy_cost"] = None
            row["orderbook_maker_proxy_pnl"] = None
            row["orderbook_avg_age_minutes"] = None
            row["price_source"] = "decision_market_proxy_baseline_mean"
        synthetic.append(row)
    return synthetic


def select_rows(
    decision_sets: list[dict[str, Any]],
    *,
    market_cost_cap: float,
    min_edge: float,
    same_cost_tolerance: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in decision_sets:
        base_pass = row["model_adjacent3_market_cost"] <= market_cost_cap and row["model_adjacent3_edge"] >= min_edge
        if base_pass:
            out.append(make_eval_row("model_adjacent3_no_filter", row, row["model_adjacent3_legs"]))
        if base_pass and row["quality_medium"]:
            out.append(make_eval_row("model_adjacent3_medium_quality", row, row["model_adjacent3_legs"]))
            out.append(make_eval_row("matched_market_mode_adjacent3_for_medium", row, row["market_adjacent3_legs"]))
            target_brackets = [str(leg["bracket"]) for leg in row["model_adjacent3_legs"]]
            target_cost = row["model_adjacent3_market_cost"]
            candidate_i = 0
            for start in range(0, len(row["legs"]) - 2):
                legs = row["legs"][start : start + 3]
                brackets = [str(leg["bracket"]) for leg in legs]
                if brackets == target_brackets:
                    continue
                cost = sum(float(leg["market_yes_price"]) for leg in legs)
                if abs(cost - target_cost) > same_cost_tolerance:
                    continue
                baseline = make_eval_row("same_cost_random_adjacent3_candidate_for_medium", row, legs)
                baseline["range_id"] = f"{baseline['range_id']}|candidate_{candidate_i}"
                baseline["baseline_candidate_i"] = candidate_i
                baseline["target_cost"] = target_cost
                baseline["cost_distance"] = abs(cost - target_cost)
                candidate_i += 1
                out.append(baseline)
    return out


def attach_orderbook(eval_rows: list[dict[str, Any]], orderbook_glob: str) -> dict[str, Any]:
    settled = [row for row in eval_rows if row.get("decision_proxy_pnl") is not None]
    coverage = scanner.attach_orderbook_costs(settled, orderbook_glob)
    by_id = {row["range_id"]: row for row in settled}
    matched = 0
    for row in eval_rows:
        ob = by_id.get(row["range_id"])
        if ob and ob.get("orderbook_taker_cost") is not None:
            matched += 1
            row["orderbook_taker_cost"] = ob["orderbook_taker_cost"]
            row["orderbook_taker_pnl"] = ob["orderbook_taker_pnl"]
            row["orderbook_maker_proxy_cost"] = ob["orderbook_maker_proxy_cost"]
            row["orderbook_maker_proxy_pnl"] = ob["orderbook_maker_proxy_pnl"]
            row["orderbook_avg_age_minutes"] = ob["orderbook_avg_age_minutes"]
            row["price_source"] = "time_aligned_orderbook"
        else:
            row["orderbook_taker_cost"] = None
            row["orderbook_taker_pnl"] = None
            row["orderbook_maker_proxy_cost"] = None
            row["orderbook_maker_proxy_pnl"] = None
            row["orderbook_avg_age_minutes"] = None
            row["price_source"] = "decision_market_proxy"
    coverage["eval_rows"] = len(eval_rows)
    coverage["eval_rows_settled"] = len(settled)
    coverage["eval_rows_orderbook_matched"] = matched
    coverage["eval_rows_orderbook_matched_rate"] = safe_div(matched, len(eval_rows))
    return coverage


def split_label(row: dict[str, Any], split: dict[str, Any]) -> str:
    date = str(row["event_date"])
    if date in split["train_dates"]:
        return "train"
    if date in split["holdout_dates"]:
        return "holdout"
    return "unsettled_or_outside"


def summarize(items: list[dict[str, Any]], *, pnl_key: str, cost_key: str, seed: int, bootstrap_iters: int) -> dict[str, Any]:
    usable = [row for row in items if row.get(pnl_key) is not None and row.get(cost_key) is not None]
    cost = sum(float(row[cost_key]) for row in usable)
    pnl = sum(float(row[pnl_key]) for row in usable)
    by_date: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "pnl": 0.0, "rows": 0.0})
    for row in usable:
        slot = by_date[str(row["event_date"])]
        slot["cost"] += float(row[cost_key])
        slot["pnl"] += float(row[pnl_key])
        slot["rows"] += 1.0
    top5 = sorted(by_date.items(), key=lambda item: item[1]["pnl"], reverse=True)[:5]
    top5_dates = {date for date, _value in top5}
    rem_cost = sum(value["cost"] for date, value in by_date.items() if date not in top5_dates)
    rem_pnl = sum(value["pnl"] for date, value in by_date.items() if date not in top5_dates)
    boot = []
    dates = sorted(by_date)
    if dates:
        rng = random.Random(seed)
        for _ in range(bootstrap_iters):
            sample = [by_date[rng.choice(dates)] for _i in dates]
            sample_cost = sum(value["cost"] for value in sample)
            sample_pnl = sum(value["pnl"] for value in sample)
            if sample_cost:
                boot.append(sample_pnl / sample_cost)
    return {
        "rows": len(items),
        "usable_rows": len(usable),
        "active_dates": len(by_date),
        "cost": cost,
        "pnl": pnl,
        "roi": safe_div(pnl, cost),
        "roi_ci95": ci95(boot),
        "top5_removed_roi": safe_div(rem_pnl, rem_cost),
        "worst_day_pnl": min((value["pnl"] for value in by_date.values()), default=None),
        "hit_rate": safe_div(sum(int(row.get("final_hit") or 0) for row in usable), len(usable)),
    }


def build_summary(eval_rows: list[dict[str, Any]], split: dict[str, Any], *, seed: int, bootstrap_iters: int) -> list[dict[str, Any]]:
    out = []
    for rule_id in sorted({row["rule_id"] for row in eval_rows}):
        rule_rows = [row for row in eval_rows if row["rule_id"] == rule_id]
        for label in ("train", "holdout", "unsettled_or_outside"):
            part = [row for row in rule_rows if split_label(row, split) == label]
            out.append(
                {
                    "rule_id": rule_id,
                    "split": label,
                    "decision_proxy": summarize(
                        part,
                        pnl_key="decision_proxy_pnl",
                        cost_key="decision_proxy_cost",
                        seed=seed,
                        bootstrap_iters=bootstrap_iters,
                    ),
                    "orderbook_taker": summarize(
                        part,
                        pnl_key="orderbook_taker_pnl",
                        cost_key="orderbook_taker_cost",
                        seed=seed,
                        bootstrap_iters=bootstrap_iters,
                    ),
                }
            )
    return out


def summary_row(summary: list[dict[str, Any]], rule_id: str, split: str, source: str) -> dict[str, Any]:
    for row in summary:
        if row["rule_id"] == rule_id and row["split"] == split:
            return row[source]
    return {}


def excess_row(summary: list[dict[str, Any]], selected: str, baseline: str, split: str, source: str) -> dict[str, Any]:
    selected_row = summary_row(summary, selected, split, source)
    baseline_row = summary_row(summary, baseline, split, source)
    selected_roi = selected_row.get("roi")
    baseline_roi = baseline_row.get("roi")
    return {
        "selected_rule_id": selected,
        "baseline_rule_id": baseline,
        "split": split,
        "source": source,
        "selected_roi": selected_roi,
        "baseline_roi": baseline_roi,
        "excess_roi": None if selected_roi is None or baseline_roi is None else selected_roi - baseline_roi,
        "selected_rows": selected_row.get("usable_rows", 0),
        "baseline_rows": baseline_row.get("usable_rows", 0),
        "selected_active_dates": selected_row.get("active_dates", 0),
        "baseline_active_dates": baseline_row.get("active_dates", 0),
    }


def build_excess(summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = [
        ("model_adjacent3_medium_quality", "model_adjacent3_no_filter"),
        ("model_adjacent3_medium_quality", "same_cost_random_adjacent3_for_medium"),
        ("model_adjacent3_medium_quality", "matched_market_mode_adjacent3_for_medium"),
    ]
    out = []
    for selected, baseline in pairs:
        for split in ("train", "holdout"):
            for source in ("decision_proxy", "orderbook_taker"):
                out.append(excess_row(summary, selected, baseline, split, source))
    return out


def baseline_diagnostics(eval_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {(row["rule_id"], row["decision_set_id"]): row for row in eval_rows}
    medium_rows = [row for row in eval_rows if row["rule_id"] == "model_adjacent3_medium_quality"]
    same_market_mode = 0
    missing_market_mode = 0
    for row in medium_rows:
        baseline = by_id.get(("matched_market_mode_adjacent3_for_medium", row["decision_set_id"]))
        if baseline is None:
            missing_market_mode += 1
            continue
        if row["brackets"] == baseline["brackets"]:
            same_market_mode += 1
    same_cost_candidates = [
        row for row in eval_rows if row["rule_id"] == "same_cost_random_adjacent3_candidate_for_medium"
    ]
    same_cost_decisions = {row["decision_set_id"] for row in same_cost_candidates}
    return {
        "medium_quality_rows": len(medium_rows),
        "matched_market_mode_rows": len(medium_rows) - missing_market_mode,
        "model_mode_equals_market_mode_rows": same_market_mode,
        "model_mode_equals_market_mode_rate": safe_div(same_market_mode, len(medium_rows)),
        "same_cost_random_candidate_rows": len(same_cost_candidates),
        "same_cost_random_matched_decision_sets": len(same_cost_decisions),
        "same_cost_random_match_rate": safe_div(len(same_cost_decisions), len(medium_rows)),
        "interpretation": (
            "Market-mode can degenerate when model and market select the same adjacent3. "
            "Same-cost random is the primary non-degenerate matched baseline."
        ),
    }


def gate_status(summary: list[dict[str, Any]], excess: list[dict[str, Any]]) -> dict[str, Any]:
    holdout = summary_row(summary, "model_adjacent3_medium_quality", "holdout", "orderbook_taker")
    ci = holdout.get("roi_ci95") or [None, None]
    random_excess = [
        row
        for row in excess
        if row["selected_rule_id"] == "model_adjacent3_medium_quality"
        and row["baseline_rule_id"] == "same_cost_random_adjacent3_for_medium"
        and row["split"] == "holdout"
        and row["source"] == "orderbook_taker"
    ][0]
    significance = bool(ci[0] is not None and ci[0] > 0 and holdout.get("usable_rows", 0) >= 30)
    baseline = bool((random_excess.get("excess_roi") or 0) > 0 and random_excess.get("selected_rows", 0) >= 30)
    forward = bool(
        holdout.get("usable_rows", 0) >= 30
        and holdout.get("active_dates", 0) >= 10
        and (holdout.get("top5_removed_roi") is not None)
        and holdout.get("top5_removed_roi") > 0
    )
    return {
        "significance": "PASS" if significance else "FAIL",
        "baseline": "PASS" if baseline else "FAIL",
        "forward": "PASS" if forward else "FAIL",
        "conclusion": "confirmed" if significance and baseline and forward else "inconclusive",
    }


def strip_private(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"_legs", "decision_dt"}}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows_in: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows_in:
            fh.write(json.dumps(strip_private(row), ensure_ascii=False, sort_keys=True) + "\n")


def write_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = report["summary"]
    rows_out = []
    for rule_id in (
        "model_adjacent3_no_filter",
        "model_adjacent3_medium_quality",
        "same_cost_random_adjacent3_for_medium",
        "matched_market_mode_adjacent3_for_medium",
    ):
        for split in ("train", "holdout"):
            dp = summary_row(summary, rule_id, split, "decision_proxy")
            ob = summary_row(summary, rule_id, split, "orderbook_taker")
            rows_out.append(
                [
                    rule_id,
                    split,
                    dp.get("usable_rows", 0),
                    pct(dp.get("roi")),
                    fmt_ci(dp.get("roi_ci95")),
                    pct(dp.get("top5_removed_roi")),
                    ob.get("usable_rows", 0),
                    pct(ob.get("roi")),
                    fmt_ci(ob.get("roi_ci95")),
                    pct(ob.get("top5_removed_roi")),
                ]
            )
    excess_rows = [
        [
            row["selected_rule_id"],
            row["baseline_rule_id"],
            row["split"],
            row["source"],
            row["selected_rows"],
            row["baseline_rows"],
            pct(row["selected_roi"]),
            pct(row["baseline_roi"]),
            pct(row["excess_roi"]),
        ]
        for row in report["excess"]
    ]
    diagnostic_rows = [[key, value] for key, value in report["baseline_diagnostics"].items()]
    lines = [
        "# Adjacent3 Quality Matched Baseline v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> git_sha: `{report['git_sha']}`",
        f"> target_metric: `{report['target_metric']}`",
        f"> Scope: local research only; no N100/live config changed; no live orders.",
        "",
        "## 数据快照",
        "",
        "- 数据源：`runtime/weather.db.fact_signal_candidates` / `fact_trades`。",
        f"- fact_signal_candidates event range：`{report['data_self_check']['candidate_event_range']}`。",
        f"- train：`{report['split']['train_start']}` -> `{report['split']['train_end']}`；holdout：`{report['split']['holdout_start']}` -> `{report['split']['holdout_end']}`。",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(
            {
                "max_fact_built_at_utc": report["data_self_check"]["max_fact_built_at_utc"],
                "trade_class_distribution": report["data_self_check"]["trade_class_distribution"],
                "settlement_status_distribution": report["data_self_check"]["settlement_status_distribution"],
                "candidate_coverage": report["data_self_check"]["candidate_coverage"],
                "order_fill_coverage": report["data_self_check"]["order_fill_coverage"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## Baseline 定义",
        "",
        "- `model_adjacent3_no_filter`：所有 cost/edge 合格的 model-mode adjacent3。",
        "- `model_adjacent3_medium_quality`：同一 model-mode adjacent3，但要求 train-only medium quality。",
        "- `same_cost_random_adjacent3_for_medium`：只在 medium_quality 触发的同一个 decision set 内，找成本差不超过 0.05 的其它 adjacent3，取 baseline mean。",
        "- `matched_market_mode_adjacent3_for_medium`：只在 medium_quality 触发的同一个 decision set 内，改买 market-mode adjacent3；当前主要用于退化诊断。",
        "",
        "## 当前证据",
        "",
        table(
            [
                "rule",
                "split",
                "decision rows",
                "decision ROI",
                "decision CI",
                "decision top5 removed",
                "orderbook rows",
                "orderbook ROI",
                "orderbook CI",
                "orderbook top5 removed",
            ],
            rows_out,
        ),
        "",
        "## Excess vs Baseline",
        "",
        table(
            [
                "selected",
                "baseline",
                "split",
                "source",
                "selected rows",
                "baseline rows",
                "selected ROI",
                "baseline ROI",
                "excess ROI",
            ],
            excess_rows,
        ),
        "",
        "## Baseline 退化诊断",
        "",
        table(["field", "value"], diagnostic_rows),
        "",
        "## 三门状态",
        "",
        table(
            ["gate", "status"],
            [[key, value] for key, value in report["gates"].items()],
        ),
        "",
        "## 人话结论",
        "",
        "- medium_quality adjacent3 的点估计仍然好看，但相对 same-cost random adjacent3 的 baseline 证据还不够硬。",
        "- 最公平的 live-readiness baseline 是同一个 decision set 内的 same-cost random adjacent3；它控制了 city/date/time/width/cost。",
        "- market-mode adjacent3 在当前样本里与 model-mode adjacent3 完全重合，所以只作为退化诊断，不能证明模型相对市场 mode 有额外 alpha。",
        "- 当前动作仍是 shadow/paper，不给 live 动作。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    conn = connect(str(db_path))
    check = data_self_check(conn, db_path)
    candidates = load_candidates(conn)
    decision_sets = build_decision_sets(candidates)
    split = split_dates(decision_sets)
    thresholds = add_quality(decision_sets, split["train_dates"])
    eval_rows = select_rows(
        decision_sets,
        market_cost_cap=args.market_cost_cap,
        min_edge=args.min_edge,
        same_cost_tolerance=args.same_cost_tolerance,
    )
    orderbook_coverage = attach_orderbook(eval_rows, str(args.orderbook_glob))
    synthetic_baselines = aggregate_baselines(eval_rows)
    summary_rows = [
        row for row in eval_rows if row["rule_id"] != "same_cost_random_adjacent3_candidate_for_medium"
    ] + synthetic_baselines
    summary = build_summary(summary_rows, split, seed=args.seed, bootstrap_iters=args.bootstrap_iters)
    excess = build_excess(summary)
    gates = gate_status(summary, excess)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "out_json": str(Path(args.out_json)),
        "out_jsonl": str(Path(args.out_jsonl)),
        "out_md": str(Path(args.out_md)),
        "data_self_check": check,
        "split": {
            "train_start": split["train_start"],
            "train_end": split["train_end"],
            "train_dates": len(split["train_dates"]),
            "holdout_start": split["holdout_start"],
            "holdout_end": split["holdout_end"],
            "holdout_dates": len(split["holdout_dates"]),
        },
        "quality_thresholds_train_only": thresholds,
        "rule_params": {
            "market_cost_cap": args.market_cost_cap,
            "same_cost_tolerance": args.same_cost_tolerance,
            "min_edge": args.min_edge,
            "range_width": 3,
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
        },
        "filter_funnel": {
            "loaded_candidates": len(candidates),
            "decision_sets": len(decision_sets),
            "eval_rows": len(eval_rows),
            "summary_rows": len(summary_rows),
            "synthetic_same_cost_baseline_rows": len(synthetic_baselines),
        },
        "orderbook_coverage": orderbook_coverage,
        "summary": summary,
        "excess": excess,
        "baseline_diagnostics": baseline_diagnostics(eval_rows),
        "gates": gates,
        "sample_rows": [strip_private(row) for row in summary_rows[:25]],
    }
    write_json(Path(args.out_json), report)
    write_jsonl(Path(args.out_jsonl), summary_rows)
    write_md(Path(args.out_md), report)
    print(args.out_json)
    print(args.out_jsonl)
    print(args.out_md)


if __name__ == "__main__":
    main()
