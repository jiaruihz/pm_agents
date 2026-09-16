#!/usr/bin/env python3
"""Hybrid adjacent3 + single-leg weather strategy research v0.

Local research only. The target metric is forecast-first city-day hybrid alpha:
whether a fixed adjacent3 range can be improved by at most one high-conviction
single YES overlay without relying on filled-sample selection.

Primary source is runtime/weather.db.fact_signal_candidates. Time-aligned raw
orderbook costs are attached through research_range_rv_scanner, which enforces
orderbook_snapshot_ts <= decision_snapshot_ts_utc.
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
sys.path.insert(0, str(ROOT))
sys.path.append(str(SCRIPT_DIR))

import research_range_rv_scanner as scanner  # noqa: E402
from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)


DB_DEFAULT = ROOT / "runtime" / "weather.db"
ORDERBOOK_GLOB_DEFAULT = scanner.ORDERBOOK_GLOB_DEFAULT
TARGET_METRIC = "city_day_hybrid_adjacent3_single_alpha"
RULE_ORDER = [
    "adjacent3_base",
    "adjacent3_medium_quality",
    "single_high_conviction_yes",
    "adjacent3_medium_plus_single_yes",
    "outside_range_no_overlay",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--orderbook-glob", default=str(ORDERBOOK_GLOB_DEFAULT))
    parser.add_argument("--market-cost-cap", type=float, default=0.85)
    parser.add_argument("--min-range-edge", type=float, default=0.0)
    parser.add_argument("--single-min-edge", type=float, default=0.10)
    parser.add_argument("--single-min-model-prob", type=float, default=0.18)
    parser.add_argument("--single-max-cost", type=float, default=0.25)
    parser.add_argument("--outside-no-min-edge", type=float, default=0.10)
    parser.add_argument("--outside-no-max-model-prob", type=float, default=0.20)
    parser.add_argument("--outside-no-max-cost", type=float, default=0.80)
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260610)
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
        "fact_signal_candidates_event_range": rows(
            conn,
            "SELECT MIN(event_date) AS min_event_date, MAX(event_date) AS max_event_date, "
            "COUNT(DISTINCT event_date) AS event_dates, COUNT(*) AS rows FROM fact_signal_candidates",
        )[0],
        "candidate_settlement_by_decision_window": rows(
            conn,
            "SELECT decision_window_missing, settlement_status, COUNT(*) AS rows "
            "FROM fact_signal_candidates GROUP BY decision_window_missing, settlement_status "
            "ORDER BY decision_window_missing, settlement_status",
        ),
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
        """,
    )


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

    decision_sets: list[dict[str, Any]] = []
    for key, by_bracket in grouped.items():
        legs = sorted(by_bracket.values(), key=lambda row: scanner.bracket_sort_value(str(row["bracket"])))
        if len(legs) < 3:
            continue
        model = normalize([float(row["model_p_yes"]) for row in legs])
        market = normalize([float(row["market_yes_price"]) for row in legs])
        if not any(model):
            continue
        mode_i = max(range(len(model)), key=lambda idx: model[idx])
        start = max(0, min(mode_i - 1, len(legs) - 3))
        end = start + 3
        selected = legs[start:end]
        selected_idxs = set(range(start, end))
        selected_labels_ready = all(
            row.get("settlement_status") == "settled" and row.get("final_yes") is not None for row in selected
        )
        final_hit = None
        if selected_labels_ready:
            final_hit = int(any(float(row["final_yes"]) >= 0.5 for row in selected))
        model_mass = sum(model[start:end])
        market_cost = sum(float(row["market_yes_price"]) for row in selected)
        single_candidates = []
        for idx, row in enumerate(legs):
            edge = model[idx] - float(row["market_yes_price"])
            label_ready = row.get("settlement_status") == "settled" and row.get("final_yes") is not None
            single_candidates.append(
                {
                    "idx": idx,
                    "row": row,
                    "model_prob_norm": model[idx],
                    "market_cost": float(row["market_yes_price"]),
                    "single_edge": edge,
                    "inside_adjacent3": int(idx in selected_idxs),
                    "label_ready": label_ready,
                }
            )
        decision_sets.append(
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
                "mode_i": mode_i,
                "mode_bracket": legs[mode_i]["bracket"],
                "adjacent3_start_i": start,
                "adjacent3_end_i": end - 1,
                "adjacent3_legs": selected,
                "adjacent3_brackets": [str(row["bracket"]) for row in selected],
                "adjacent3_model_mass": model_mass,
                "adjacent3_market_cost": market_cost,
                "adjacent3_edge": model_mass - market_cost,
                "adjacent3_final_hit": final_hit,
                "adjacent3_labels_ready": selected_labels_ready,
                "model_entropy": entropy(model),
                "model_mode_probability": model[mode_i],
                "model_tail_mass_outside_adjacent3": 1.0 - model_mass,
                "model_market_l1_gap": sum(abs(a - b) for a, b in zip(model, market)),
                "single_candidates": single_candidates,
            }
        )
    return [row for row in decision_sets if row["decision_dt"] is not None]


def split_dates(decision_sets: list[dict[str, Any]]) -> dict[str, Any]:
    settled_dates = sorted(
        {
            str(row["event_date"])
            for row in decision_sets
            if row.get("adjacent3_labels_ready") and row.get("adjacent3_final_hit") is not None
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
    adj3_med = percentile([float(row["adjacent3_model_mass"]) for row in train], 0.50) or 0.0
    tail_med = percentile([float(row["model_tail_mass_outside_adjacent3"]) for row in train], 0.50) or 1.0
    entropy_med = percentile([float(row["model_entropy"]) for row in train], 0.50) or 1.0
    for row in decision_sets:
        row["quality_medium"] = int(
            float(row["adjacent3_model_mass"]) >= adj3_med
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


def selected_pnl(legs: list[dict[str, Any]], side: str) -> tuple[float | None, float | None, int | None]:
    if not all(row.get("settlement_status") == "settled" and row.get("final_yes") is not None for row in legs):
        return None, None, None
    if side == "BUY_YES":
        cost = sum(float(row["market_yes_price"]) for row in legs)
        payout = 1.0 if any(float(row["final_yes"]) >= 0.5 for row in legs) else 0.0
    elif side == "BUY_NO":
        cost = sum(1.0 - float(row["market_yes_price"]) for row in legs)
        payout = sum(1.0 if float(row["final_yes"]) < 0.5 else 0.0 for row in legs)
    else:
        raise ValueError(f"unsupported side: {side}")
    return cost, payout - cost, int(payout)


def make_eval_row(
    rule_id: str,
    row: dict[str, Any],
    legs: list[dict[str, Any]],
    extra: dict[str, Any],
    *,
    side: str = "BUY_YES",
) -> dict[str, Any]:
    cost, pnl, hit = selected_pnl(legs, side)
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
        "leg_side": side,
        "n_legs": len(legs),
        "brackets": [str(leg["bracket"]) for leg in legs],
        "condition_ids": [str(leg["condition_id"]) for leg in legs],
        "model_prob_sum": extra.get("model_prob_sum"),
        "market_prob_sum": extra.get("market_prob_sum"),
        "range_edge": extra.get("range_edge"),
        "model_entropy": row["model_entropy"],
        "model_mode_probability": row["model_mode_probability"],
        "model_tail_mass_outside_adjacent3": row["model_tail_mass_outside_adjacent3"],
        "model_market_l1_gap": row["model_market_l1_gap"],
        "quality_medium": row["quality_medium"],
        "quality_low_uncertainty": row["quality_low_uncertainty"],
        "overlay_single_bracket": extra.get("overlay_single_bracket"),
        "overlay_single_edge": extra.get("overlay_single_edge"),
        "overlay_single_model_prob": extra.get("overlay_single_model_prob"),
        "overlay_single_market_cost": extra.get("overlay_single_market_cost"),
        "overlay_inside_adjacent3": extra.get("overlay_inside_adjacent3"),
        "settlement_status": "settled" if cost is not None else "unsettled_or_unusable",
        "final_hit": hit,
        "decision_proxy_cost": cost,
        "decision_proxy_pnl": pnl,
        "_legs": legs,
    }


def select_rows(
    decision_sets: list[dict[str, Any]],
    *,
    market_cost_cap: float,
    min_range_edge: float,
    single_min_edge: float,
    single_min_model_prob: float,
    single_max_cost: float,
    outside_no_min_edge: float,
    outside_no_max_model_prob: float,
    outside_no_max_cost: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in decision_sets:
        base_pass = row["adjacent3_market_cost"] <= market_cost_cap and row["adjacent3_edge"] >= min_range_edge
        if base_pass:
            out.append(
                make_eval_row(
                    "adjacent3_base",
                    row,
                    row["adjacent3_legs"],
                    {
                        "model_prob_sum": row["adjacent3_model_mass"],
                        "market_prob_sum": row["adjacent3_market_cost"],
                        "range_edge": row["adjacent3_edge"],
                    },
                )
            )
        if base_pass and row["quality_medium"]:
            out.append(
                make_eval_row(
                    "adjacent3_medium_quality",
                    row,
                    row["adjacent3_legs"],
                    {
                        "model_prob_sum": row["adjacent3_model_mass"],
                        "market_prob_sum": row["adjacent3_market_cost"],
                        "range_edge": row["adjacent3_edge"],
                    },
                )
            )

        eligible_singles = [
            item
            for item in row["single_candidates"]
            if item["single_edge"] >= single_min_edge
            and item["model_prob_norm"] >= single_min_model_prob
            and item["market_cost"] <= single_max_cost
        ]
        eligible_singles.sort(key=lambda item: (item["single_edge"], item["model_prob_norm"]), reverse=True)
        if eligible_singles:
            single = eligible_singles[0]
            single_leg = single["row"]
            single_extra = {
                "model_prob_sum": single["model_prob_norm"],
                "market_prob_sum": single["market_cost"],
                "range_edge": single["single_edge"],
                "overlay_single_bracket": str(single_leg["bracket"]),
                "overlay_single_edge": single["single_edge"],
                "overlay_single_model_prob": single["model_prob_norm"],
                "overlay_single_market_cost": single["market_cost"],
                "overlay_inside_adjacent3": single["inside_adjacent3"],
            }
            out.append(make_eval_row("single_high_conviction_yes", row, [single_leg], single_extra))
            if base_pass and row["quality_medium"] and not single["inside_adjacent3"]:
                combined = row["adjacent3_legs"] + [single_leg]
                out.append(
                    make_eval_row(
                        "adjacent3_medium_plus_single_yes",
                        row,
                        combined,
                        {
                            "model_prob_sum": row["adjacent3_model_mass"] + single["model_prob_norm"],
                            "market_prob_sum": row["adjacent3_market_cost"] + single["market_cost"],
                            "range_edge": row["adjacent3_edge"] + single["single_edge"],
                            **single_extra,
                        },
                    )
                )
        outside_no_candidates = []
        for item in row["single_candidates"]:
            if item["inside_adjacent3"]:
                continue
            no_edge = item["market_cost"] - item["model_prob_norm"]
            no_cost = 1.0 - item["market_cost"]
            if (
                no_edge >= outside_no_min_edge
                and item["model_prob_norm"] <= outside_no_max_model_prob
                and no_cost <= outside_no_max_cost
            ):
                outside_no_candidates.append({**item, "no_edge": no_edge, "no_cost": no_cost})
        outside_no_candidates.sort(key=lambda item: (item["no_edge"], -item["no_cost"]), reverse=True)
        if outside_no_candidates:
            no_item = outside_no_candidates[0]
            no_leg = no_item["row"]
            out.append(
                make_eval_row(
                    "outside_range_no_overlay",
                    row,
                    [no_leg],
                    {
                        "model_prob_sum": 1.0 - no_item["model_prob_norm"],
                        "market_prob_sum": no_item["no_cost"],
                        "range_edge": no_item["no_edge"],
                        "overlay_single_bracket": str(no_leg["bracket"]),
                        "overlay_single_edge": no_item["no_edge"],
                        "overlay_single_model_prob": no_item["model_prob_norm"],
                        "overlay_single_market_cost": no_item["no_cost"],
                        "overlay_inside_adjacent3": 0,
                    },
                    side="BUY_NO",
                )
            )
    return out


def attach_orderbook(eval_rows: list[dict[str, Any]], orderbook_glob: str) -> dict[str, Any]:
    settled = []
    for row in eval_rows:
        if row.get("decision_proxy_pnl") is None:
            continue
        settled.append(row)
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


def overlap_summary(eval_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for rule_id in RULE_ORDER:
        rule_rows = [row for row in eval_rows if row["rule_id"] == rule_id]
        with_overlay = [row for row in rule_rows if row.get("overlay_inside_adjacent3") is not None]
        inside = sum(int(row.get("overlay_inside_adjacent3") or 0) for row in with_overlay)
        out.append(
            {
                "rule_id": rule_id,
                "rows": len(rule_rows),
                "overlay_rows": len(with_overlay),
                "inside_adjacent3_rows": inside,
                "inside_adjacent3_rate": safe_div(inside, len(with_overlay)),
            }
        )
    return out


def gate_status(summary: list[dict[str, Any]], rule_id: str) -> dict[str, Any]:
    holdout = summary_row(summary, rule_id, "holdout", "orderbook_taker")
    decision_holdout = summary_row(summary, rule_id, "holdout", "decision_proxy")
    ci = holdout.get("roi_ci95") or [None, None]
    significance = bool(ci[0] is not None and ci[0] > 0 and holdout.get("usable_rows", 0) >= 30)
    baseline = False
    forward = bool(
        holdout.get("usable_rows", 0) >= 30
        and holdout.get("active_dates", 0) >= 10
        and (holdout.get("roi") or 0) > 0
        and (decision_holdout.get("roi") or 0) > 0
        and (holdout.get("top5_removed_roi") is not None)
        and holdout.get("top5_removed_roi") > 0
    )
    return {
        "rule_id": rule_id,
        "significance": "PASS" if significance else "FAIL",
        "baseline": "FAIL",
        "forward": "PASS" if forward else "FAIL",
        "conclusion": "confirmed" if significance and baseline and forward else "inconclusive",
        "reason": "baseline is not proven against a matched no-trade/random baseline in v0; orderbook sample must be larger.",
    }


def strip_private(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"_legs", "decision_dt"}}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = report["summary"]
    rows_out = []
    for rule_id in RULE_ORDER:
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
    gate_rows = [
        [row["rule_id"], row["significance"], row["baseline"], row["forward"], row["conclusion"]]
        for row in report["gates"]
    ]
    overlap_rows = [
        [
            row["rule_id"],
            row["rows"],
            row["overlay_rows"],
            row["inside_adjacent3_rows"],
            pct(row["inside_adjacent3_rate"]),
        ]
        for row in report["overlap_summary"]
    ]
    lines = [
        "# Hybrid Adjacent3 + Single / Outside NO v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> git_sha: `{report['git_sha']}`",
        f"> target_metric: `{report['target_metric']}`",
        f"> Scope: local research only; no N100/live config changed; no live orders.",
        "",
        "## 数据快照",
        "",
        "- 数据源：`runtime/weather.db.fact_signal_candidates` / `fact_trades`。",
        f"- fact_signal_candidates event range：`{report['data_self_check']['fact_signal_candidates_event_range']}`。",
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
        "## 固定规则",
        "",
        "- `adjacent3_base`：围绕模型 mode 买相邻 3 档 YES，`market_cost <= 0.85` 且 `model_mass > market_cost`。",
        "- `adjacent3_medium_quality`：同上，附加 train-only forecast quality soft gate。",
        "- `single_high_conviction_yes`：每个 city-day decision set 最多选 1 个 YES，要求固定阈值 `model_prob_norm >= 0.18`、`model_prob_norm - market_yes_price >= 0.10`、`market_yes_price <= 0.25`。",
        "- `adjacent3_medium_plus_single_yes`：medium adjacent3 之外最多叠加 1 个外侧强 YES；如果强 YES 已在 adjacent3 内，不重复加仓。",
        "- `outside_range_no_overlay`：只看 adjacent3 外侧，若市场 YES 比模型高估至少 10pct、模型命中概率不超过 20%、NO 成本不超过 0.80，则买该外侧档 NO。",
        "",
        "## Filter Funnel",
        "",
        table(
            ["step", "count"],
            [
                ["fact_signal_candidates rows", report["data_self_check"]["candidate_coverage"]["rows"]],
                ["decision sets", report["filter_funnel"]["decision_sets"]],
                ["eval rows", report["filter_funnel"]["eval_rows"]],
                ["settled eval rows", report["orderbook_coverage"].get("eval_rows_settled")],
                ["orderbook matched eval rows", report["orderbook_coverage"].get("eval_rows_orderbook_matched")],
            ],
        ),
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
        "## 单腿重叠检查",
        "",
        table(["rule", "rows", "overlay rows", "inside adjacent3 rows", "inside rate"], overlap_rows),
        "",
        "## 三门状态",
        "",
        table(["rule", "significance", "baseline", "forward", "conclusion"], gate_rows),
        "",
        "## 人话结论",
        "",
        "- 单腿可以作为 Range RV 的补充，但前提是分清重复加注、降级表达和区间外互补；本脚本把内部 YES、外侧 YES、外侧 NO 分开看。",
        "- 当前 fixed hybrid 点估计没有形成可 live 的证据：orderbook matched 行数太少，baseline 还没有做成 matched no-trade/random 对照，三门不可能过。",
        "- 这次固定口径下 `outside_range_no_overlay` 没有触发，说明区间外 NO 暂时只是值得继续 shadow 的概念，不是当前 fact 近窗里的实盘候选。",
        "- 如果后续要继续，优先把这个 hybrid 作为 shadow journal 规则之一，而不是直接真钱。",
        "",
        "## 产物",
        "",
        f"- JSON summary：`{report['out_json']}`",
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
    quality_thresholds = add_quality(decision_sets, split["train_dates"])
    eval_rows = select_rows(
        decision_sets,
        market_cost_cap=args.market_cost_cap,
        min_range_edge=args.min_range_edge,
        single_min_edge=args.single_min_edge,
        single_min_model_prob=args.single_min_model_prob,
        single_max_cost=args.single_max_cost,
        outside_no_min_edge=args.outside_no_min_edge,
        outside_no_max_model_prob=args.outside_no_max_model_prob,
        outside_no_max_cost=args.outside_no_max_cost,
    )
    orderbook_coverage = attach_orderbook(eval_rows, str(args.orderbook_glob))
    summary = build_summary(eval_rows, split, seed=args.seed, bootstrap_iters=args.bootstrap_iters)
    gates = [gate_status(summary, rule_id) for rule_id in RULE_ORDER]
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "out_json": str(Path(args.out_json)),
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
        "quality_thresholds_train_only": quality_thresholds,
        "rule_params": {
            "market_cost_cap": args.market_cost_cap,
            "min_range_edge": args.min_range_edge,
            "single_min_edge": args.single_min_edge,
            "single_min_model_prob": args.single_min_model_prob,
            "single_max_cost": args.single_max_cost,
            "outside_no_min_edge": args.outside_no_min_edge,
            "outside_no_max_model_prob": args.outside_no_max_model_prob,
            "outside_no_max_cost": args.outside_no_max_cost,
            "range_width": 3,
            "primary_side": "BUY_YES",
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
        },
        "filter_funnel": {
            "loaded_candidates": len(candidates),
            "decision_sets": len(decision_sets),
            "eval_rows": len(eval_rows),
        },
        "orderbook_coverage": orderbook_coverage,
        "summary": summary,
        "gates": gates,
        "overlap_summary": overlap_summary(eval_rows),
        "sample_rows": [strip_private(row) for row in eval_rows[:25]],
    }
    output_dir = prepare_new_run_output(
        resolve_run_output(
            "hybrid_adjacent3_single_v0",
            run_id=args.run_id,
            explicit_output=args.output_dir,
        )
    )
    report["living_doc"] = "docs/analysis/market_structure_edge.md"
    result_json = output_dir / "result.json"
    write_json(result_json, report)
    print(result_json)


if __name__ == "__main__":
    main()
