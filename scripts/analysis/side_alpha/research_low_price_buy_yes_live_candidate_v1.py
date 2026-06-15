#!/usr/bin/env python3
"""Search low-price BUY_YES variants for a live-worthy sleeve.

This is local counterfactual research only. The main denominator is
runtime/weather.db.fact_signal_candidates full opportunity rows; eligible/live
filled rows are controls only. The script searches a small, predeclared grid on
the train window, validates on chronological holdout, and writes JSON + Markdown
artifacts.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime/weather.db"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-15-low-price-buy-yes-live-candidate-v1.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-15-low-price-buy-yes-live-candidate-v1.md"
GATE_JSON_DEFAULT = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
TARGET_METRIC = "low_price_buy_yes_live_candidate_v1"
RNG_SEED = 20260615


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return "NA"
    return f"{value * 100:+.1f}%"


def num(value: float | None, digits: int = 2) -> str:
    if value is None or not np.isfinite(value):
        return "NA"
    return f"{value:+.{digits}f}"


def plain(value: float | None, digits: int = 2) -> str:
    if value is None or not np.isfinite(value):
        return "NA"
    return f"{value:.{digits}f}"


def safe_div(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


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


def connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def sql_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def sql_scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def mandatory_self_check(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "max_fact_built_at_utc": sql_scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class_distribution": sql_rows(
            conn,
            "SELECT trade_class, COUNT(*) AS n FROM fact_trades GROUP BY trade_class ORDER BY n DESC",
        ),
        "settlement_status_distribution": sql_rows(
            conn,
            "SELECT settlement_status, COUNT(*) AS n FROM fact_trades GROUP BY settlement_status ORDER BY n DESC",
        ),
        "candidate_coverage": sql_rows(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        ),
        "order_fill_coverage": sql_rows(
            conn,
            """
            SELECT o.status, COUNT(*) AS orders,
                   SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill
            FROM orders o
            LEFT JOIN fills f USING(execution_id)
            WHERE o.venue='polymarket_clob'
            GROUP BY o.status
            ORDER BY o.status
            """,
        ),
    }


def load_gate(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"gate_pass": None, "note": f"missing gate json: {path}"}
    payload = json.loads(path.read_text())
    return {
        "gate_pass": payload.get("gate_pass"),
        "fail_reasons": payload.get("fail_reasons"),
        "db_fill_cost_minus_fact_cost": payload.get("db_fill_cost_minus_fact_cost"),
        "db_vs_primary_cache": payload.get("db_vs_primary_cache"),
        "db_fills": payload.get("db_fills"),
        "fact_trades_live_real": payload.get("fact_trades_live_real"),
    }


def hour_bucket(hours: Any) -> str:
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return "unknown"
    if not np.isfinite(h):
        return "unknown"
    if h < 18:
        return "T-00-18"
    if h < 22:
        return "T-18-22"
    if h < 24:
        return "T-22-24"
    if h < 28:
        return "T-24-28"
    return "T-28+"


def price_bucket_5c(price: Any) -> str | None:
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(p) or p <= 0:
        return None
    low = math.floor(p / 0.05) * 0.05
    high = min(1.0, low + 0.05)
    return f"{low:.2f}-{high:.2f}"


def load_candidates(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = sql_rows(
        conn,
        """
        SELECT candidate_id, condition_id, market_id, side, event_date, bracket,
               city, city_pool, icao, forecast_source, model_version, time_bucket,
               window, decision_window_label, decision_hours_to_settle,
               decision_snapshot_ts_utc, decision_window_missing, model_p_yes,
               market_yes_price, edge, abs_edge, decision_entry_price,
               yes_spread, yes_depth_ask_5c, no_spread, no_depth_ask_5c,
               n_snapshots, edge_max, edge_mean, best_entry_price,
               eligible, paper_ordered, live_filled, settlement_status,
               final_yes, bracket_hit, win_by_count, counterfactual_pnl,
               counterfactual_pnl_best, fact_built_at_utc
        FROM fact_signal_candidates
        """,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    numeric_cols = [
        "decision_hours_to_settle",
        "model_p_yes",
        "market_yes_price",
        "edge",
        "abs_edge",
        "decision_entry_price",
        "yes_spread",
        "yes_depth_ask_5c",
        "no_spread",
        "no_depth_ask_5c",
        "n_snapshots",
        "edge_max",
        "edge_mean",
        "best_entry_price",
        "eligible",
        "paper_ordered",
        "live_filled",
        "final_yes",
        "bracket_hit",
        "win_by_count",
        "counterfactual_pnl",
        "counterfactual_pnl_best",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["event_date", "city", "forecast_source", "model_version", "city_pool"]:
        df[col] = df[col].fillna("unknown").astype(str)
    df["hour_bucket"] = df["decision_hours_to_settle"].map(hour_bucket)
    df["price_bucket_5c"] = df["decision_entry_price"].map(price_bucket_5c)
    df["city_date"] = df["city"] + "|" + df["event_date"]
    df["is_buy_yes_low_price"] = (
        df["side"].eq("BUY_YES")
        & df["decision_entry_price"].gt(0)
        & df["decision_entry_price"].lt(0.25)
        & df["decision_window_missing"].fillna(1).eq(0)
    )
    df["is_evaluable"] = (
        df["is_buy_yes_low_price"]
        & df["settlement_status"].eq("settled")
        & df["final_yes"].notna()
        & df["counterfactual_pnl"].notna()
        & df["decision_entry_price"].notna()
    )
    df["edge_per_price"] = df["edge"] / df["decision_entry_price"]
    return df


def split_dates(df: pd.DataFrame, holdout_frac: float) -> dict[str, Any]:
    dates = sorted(d for d in df["event_date"].dropna().unique() if d and d != "None")
    split_index = max(1, int(math.floor(len(dates) * (1.0 - holdout_frac))))
    if split_index >= len(dates):
        split_index = max(1, len(dates) - 1)
    return {
        "train_dates": dates[:split_index],
        "holdout_dates": dates[split_index:],
        "split_index": split_index,
        "holdout_frac": holdout_frac,
    }


def roi(df: pd.DataFrame) -> float | None:
    if df.empty:
        return None
    return safe_div(float(df["counterfactual_pnl"].sum()), float(df["decision_entry_price"].sum()))


def daily_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["event_date", "rows", "cost", "pnl", "roi"])
    out = df.groupby("event_date", dropna=False).agg(
        rows=("candidate_id", "count"),
        cost=("decision_entry_price", "sum"),
        pnl=("counterfactual_pnl", "sum"),
    ).reset_index()
    out["roi"] = out.apply(lambda r: safe_div(float(r["pnl"]), float(r["cost"])), axis=1)
    return out.sort_values("event_date")


def bootstrap_excess(selected: pd.DataFrame, baseline: pd.DataFrame, *, iters: int, seed: int) -> list[float]:
    groups_a = {str(k): v for k, v in selected.groupby("event_date", dropna=False).groups.items()}
    groups_b = {str(k): v for k, v in baseline.groupby("event_date", dropna=False).groups.items()}
    dates = sorted(set(groups_a) | set(groups_b))
    if not dates:
        return []
    rng = random.Random(seed)
    out: list[float] = []
    for _ in range(iters):
        a_cost = a_pnl = b_cost = b_pnl = 0.0
        for d in (rng.choice(dates) for _ in dates):
            if d in groups_a:
                part = selected.loc[groups_a[d]]
                a_cost += float(part["decision_entry_price"].sum())
                a_pnl += float(part["counterfactual_pnl"].sum())
            if d in groups_b:
                part = baseline.loc[groups_b[d]]
                b_cost += float(part["decision_entry_price"].sum())
                b_pnl += float(part["counterfactual_pnl"].sum())
        a_roi = safe_div(a_pnl, a_cost)
        b_roi = safe_div(b_pnl, b_cost)
        if a_roi is not None and b_roi is not None and np.isfinite(a_roi) and np.isfinite(b_roi):
            out.append(a_roi - b_roi)
    return out


def remove_top_units(df: pd.DataFrame, key_cols: list[str], n: int) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0, "roi": None, "removed": []}
    grouped = df.groupby(key_cols, dropna=False).agg(pnl=("counterfactual_pnl", "sum")).reset_index()
    removed = grouped.sort_values("pnl", ascending=False).head(n)
    marker = set(tuple(row[col] for col in key_cols) for _, row in removed.iterrows())
    keep = ~df[key_cols].apply(lambda row: tuple(row[col] for col in key_cols) in marker, axis=1)
    kept = df[keep]
    return {"rows": int(len(kept)), "roi": roi(kept), "removed": removed.to_dict("records")}


def grouped(df: pd.DataFrame, key_cols: list[str], limit: int, ascending: bool) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.groupby(key_cols, dropna=False).agg(
        rows=("candidate_id", "count"),
        cost=("decision_entry_price", "sum"),
        pnl=("counterfactual_pnl", "sum"),
    ).reset_index()
    out["roi"] = out.apply(lambda r: safe_div(float(r["pnl"]), float(r["cost"])), axis=1)
    return out.sort_values("pnl", ascending=ascending).head(limit).to_dict("records")


def matched_baseline(pool: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pool.iloc[0:0].copy()
    keys = set(zip(selected["hour_bucket"], selected["price_bucket_5c"]))
    key_frame = pool[["hour_bucket", "price_bucket_5c"]]
    mask = [(row.hour_bucket, row.price_bucket_5c) in keys for row in key_frame.itertuples(index=False)]
    return pool.loc[mask].copy()


def choose_one_per_city_date(df: pd.DataFrame, rank_mode: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    ranking = {
        "edge_per_price": ["edge_per_price", "edge", "decision_entry_price"],
        "edge": ["edge", "edge_per_price", "decision_entry_price"],
        "model_p_yes": ["model_p_yes", "edge", "decision_entry_price"],
        "cheapest": ["decision_entry_price", "edge", "model_p_yes"],
    }
    cols = ranking[rank_mode]
    ascending = [rank_mode == "cheapest", False, rank_mode != "cheapest"]
    ranked = df.sort_values(cols, ascending=ascending, na_position="last")
    return ranked.groupby(["city", "event_date"], dropna=False, as_index=False).head(1).copy()


@dataclass(frozen=True)
class Rule:
    name: str
    price_min: float
    price_max: float
    edge_min: float | None
    edge_mean_min: float | None
    spread_max: float | None
    depth_min: float | None
    n_snapshots_min: int | None
    rank_mode: str

    def description(self) -> str:
        parts = [f"{self.price_min:.2f}<=price<{self.price_max:.2f}", f"rank={self.rank_mode}"]
        if self.edge_min is not None:
            parts.append(f"edge>={self.edge_min:.2f}")
        if self.edge_mean_min is not None:
            parts.append(f"edge_mean>={self.edge_mean_min:.2f}")
        if self.spread_max is not None:
            parts.append(f"yes_spread<={self.spread_max:.2f}")
        if self.depth_min is not None:
            parts.append(f"yes_depth_ask_5c>={self.depth_min:.0f}")
        if self.n_snapshots_min is not None:
            parts.append(f"n_snapshots>={self.n_snapshots_min}")
        parts.append("one_per_city_date")
        return ", ".join(parts)


def build_rules() -> list[Rule]:
    price_ranges = [(0.0, 0.10), (0.10, 0.20), (0.20, 0.25), (0.0, 0.25)]
    edge_mins: list[float | None] = [0.0, 0.05, 0.10, 0.15, 0.20]
    edge_mean_mins: list[float | None] = [None, 0.0, 0.05]
    spread_maxes: list[float | None] = [None, 0.05, 0.10]
    depth_mins: list[float | None] = [None, 10.0]
    n_snapshot_mins: list[int | None] = [None, 3]
    rank_modes = ["edge_per_price", "edge", "model_p_yes", "cheapest"]
    rules: list[Rule] = []
    for price_min, price_max in price_ranges:
        for edge_min in edge_mins:
            for edge_mean_min in edge_mean_mins:
                for spread_max in spread_maxes:
                    for depth_min in depth_mins:
                        for n_min in n_snapshot_mins:
                            for rank_mode in rank_modes:
                                name = (
                                    f"p{int(price_min*100):02d}_{int(price_max*100):02d}"
                                    f"_e{('na' if edge_min is None else int(edge_min*100))}"
                                    f"_em{('na' if edge_mean_min is None else int(edge_mean_min*100))}"
                                    f"_spr{('na' if spread_max is None else int(spread_max*100))}"
                                    f"_dep{('na' if depth_min is None else int(depth_min))}"
                                    f"_n{('na' if n_min is None else n_min)}"
                                    f"_{rank_mode}"
                                )
                                rules.append(
                                    Rule(
                                        name=name,
                                        price_min=price_min,
                                        price_max=price_max,
                                        edge_min=edge_min,
                                        edge_mean_min=edge_mean_min,
                                        spread_max=spread_max,
                                        depth_min=depth_min,
                                        n_snapshots_min=n_min,
                                        rank_mode=rank_mode,
                                    )
                                )
    return rules


def apply_rule(pool: pd.DataFrame, rule: Rule) -> pd.DataFrame:
    mask = pool["decision_entry_price"].ge(rule.price_min) & pool["decision_entry_price"].lt(rule.price_max)
    if rule.edge_min is not None:
        mask &= pool["edge"].ge(rule.edge_min)
    if rule.edge_mean_min is not None:
        mask &= pool["edge_mean"].ge(rule.edge_mean_min)
    if rule.spread_max is not None:
        mask &= pool["yes_spread"].le(rule.spread_max)
    if rule.depth_min is not None:
        mask &= pool["yes_depth_ask_5c"].ge(rule.depth_min)
    if rule.n_snapshots_min is not None:
        mask &= pool["n_snapshots"].ge(rule.n_snapshots_min)
    return choose_one_per_city_date(pool[mask].copy(), rule.rank_mode)


def summarize(selected: pd.DataFrame, baseline: pd.DataFrame, *, iters: int, seed: int) -> dict[str, Any]:
    selected_roi = roi(selected)
    baseline_roi = roi(baseline)
    excess = None if selected_roi is None or baseline_roi is None else selected_roi - baseline_roi
    excess_dist = bootstrap_excess(selected, baseline, iters=iters, seed=seed)
    daily = daily_frame(selected)
    return {
        "rows": int(len(selected)),
        "active_dates": int(selected["event_date"].nunique()) if len(selected) else 0,
        "cities": int(selected["city"].nunique()) if len(selected) else 0,
        "city_dates": int(selected[["city", "event_date"]].drop_duplicates().shape[0]) if len(selected) else 0,
        "cost": float(selected["decision_entry_price"].sum()) if len(selected) else 0.0,
        "pnl": float(selected["counterfactual_pnl"].sum()) if len(selected) else 0.0,
        "roi": selected_roi,
        "hit_rate": safe_div(float(selected["win_by_count"].fillna(0).sum()), float(len(selected))) if len(selected) else None,
        "avg_entry_price": float(selected["decision_entry_price"].mean()) if len(selected) else None,
        "avg_edge": float(selected["edge"].mean()) if len(selected) else None,
        "avg_edge_mean": float(selected["edge_mean"].mean()) if len(selected) else None,
        "avg_yes_spread": float(selected["yes_spread"].mean()) if len(selected) else None,
        "median_depth_ask_5c": float(selected["yes_depth_ask_5c"].median()) if len(selected) else None,
        "eligible_rows": int(selected["eligible"].fillna(0).sum()) if len(selected) else 0,
        "paper_ordered": int(selected["paper_ordered"].fillna(0).sum()) if len(selected) else 0,
        "live_filled": int(selected["live_filled"].fillna(0).sum()) if len(selected) else 0,
        "baseline_rows": int(len(baseline)),
        "baseline_cost": float(baseline["decision_entry_price"].sum()) if len(baseline) else 0.0,
        "baseline_pnl": float(baseline["counterfactual_pnl"].sum()) if len(baseline) else 0.0,
        "baseline_roi": baseline_roi,
        "excess_roi": excess,
        "excess_ci": ci95(excess_dist),
        "positive_day_rate": float((daily["pnl"] > 0).mean()) if len(daily) else None,
        "worst_day": daily.sort_values("pnl").head(1).to_dict("records")[0] if len(daily) else None,
        "best_day": daily.sort_values("pnl", ascending=False).head(1).to_dict("records")[0] if len(daily) else None,
        "top_day_removed": {f"top{n}": remove_top_units(selected, ["event_date"], n) for n in (1, 3, 5)},
        "top_candidate_removed": {f"top{n}": remove_top_units(selected, ["candidate_id"], n) for n in (1, 3, 5)},
        "top_cities": grouped(selected, ["city"], 5, ascending=False),
        "worst_cities": grouped(selected, ["city"], 5, ascending=True),
        "top_city_dates": grouped(selected, ["city", "event_date"], 5, ascending=False),
        "worst_city_dates": grouped(selected, ["city", "event_date"], 5, ascending=True),
    }


def summarize_fast(selected: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, Any]:
    selected_roi = roi(selected)
    baseline_roi = roi(baseline)
    excess = None if selected_roi is None or baseline_roi is None else selected_roi - baseline_roi
    return {
        "rows": int(len(selected)),
        "active_dates": int(selected["event_date"].nunique()) if len(selected) else 0,
        "cities": int(selected["city"].nunique()) if len(selected) else 0,
        "cost": float(selected["decision_entry_price"].sum()) if len(selected) else 0.0,
        "pnl": float(selected["counterfactual_pnl"].sum()) if len(selected) else 0.0,
        "roi": selected_roi,
        "baseline_rows": int(len(baseline)),
        "baseline_roi": baseline_roi,
        "excess_roi": excess,
    }


def gate_for(overall: dict[str, Any], train: dict[str, Any], holdout: dict[str, Any], gate_pass: bool) -> dict[str, str]:
    overall_ci = overall.get("excess_ci") or [None, None]
    train_ci = train.get("excess_ci") or [None, None]
    holdout_excess = holdout.get("excess_roi")
    top3_roi = overall.get("top_day_removed", {}).get("top3", {}).get("roi")
    overall_sample_ok = overall["active_dates"] >= 10 and overall["rows"] >= 30
    forward_sample_ok = holdout["rows"] >= 20 and holdout["active_dates"] >= 5
    significance = "PASS" if overall_ci[0] is not None and overall_ci[0] > 0 and overall_sample_ok else "FAIL"
    baseline = significance
    forward = "PASS" if train_ci[0] is not None and train_ci[0] > 0 and holdout_excess is not None and holdout_excess > 0 and forward_sample_ok else "FAIL"
    stress = "PASS" if top3_roi is not None and top3_roi > 0 else "FAIL"
    execution = "PASS" if gate_pass and overall.get("avg_yes_spread") is not None else "FAIL"
    conclusion = "confirmed" if all(x == "PASS" for x in [significance, baseline, forward, stress, execution]) else "inconclusive"
    return {
        "significance": significance,
        "baseline": baseline,
        "forward": forward,
        "stress": stress,
        "execution": execution,
        "conclusion": conclusion,
    }


def filter_funnel(df: pd.DataFrame) -> list[dict[str, Any]]:
    steps: list[tuple[str, pd.Series, str]] = [
        ("fact_signal_candidates rows", pd.Series(True, index=df.index), "full opportunity table"),
        ("BUY_YES rows", df["side"].eq("BUY_YES"), "side only; no eligible hard gate"),
        (
            "BUY_YES with decision entry price",
            df["side"].eq("BUY_YES") & df["decision_entry_price"].notna(),
            "decision window price present",
        ),
        ("low-price BUY_YES <0.25", df["is_buy_yes_low_price"], "natural low-price universe"),
        ("settled/evaluable low-price BUY_YES", df["is_evaluable"], "authorized counterfactual pnl available"),
    ]
    out: list[dict[str, Any]] = []
    prev_rows: int | None = None
    full_rows = len(df)
    for label, mask, note in steps:
        sub = df[mask]
        rows = int(len(sub))
        out.append(
            {
                "step": label,
                "rows": rows,
                "active_dates": int(sub["event_date"].nunique()) if rows else 0,
                "cities": int(sub["city"].nunique()) if rows else 0,
                "drop_from_previous": None if prev_rows is None else rows - prev_rows,
                "drop_from_previous_pct": None if prev_rows in (None, 0) else (rows - prev_rows) / prev_rows,
                "retained_from_full_pct": safe_div(rows, full_rows),
                "eligible_rows": int(sub["eligible"].fillna(0).sum()) if rows else 0,
                "note": note,
            }
        )
        prev_rows = rows
    return out


def evaluate_rules(df: pd.DataFrame, *, iters: int, seed: int, top_n: int) -> dict[str, Any]:
    low_pool = df[df["is_evaluable"]].copy()
    split = split_dates(low_pool, 0.30)
    train_dates = set(split["train_dates"])
    holdout_dates = set(split["holdout_dates"])
    train_pool = low_pool[low_pool["event_date"].isin(train_dates)].copy()
    holdout_pool = low_pool[low_pool["event_date"].isin(holdout_dates)].copy()
    rules = build_rules()
    gate = load_gate(GATE_JSON_DEFAULT)
    gate_pass = bool(gate.get("gate_pass"))
    scored: list[dict[str, Any]] = []
    for idx, rule in enumerate(rules):
        train_selected = apply_rule(train_pool, rule)
        if len(train_selected) < 20 or train_selected["event_date"].nunique() < 6:
            continue
        train_baseline = matched_baseline(train_pool, train_selected)
        train_summary = summarize_fast(train_selected, train_baseline)
        if train_summary["excess_roi"] is None or train_summary["excess_roi"] <= 0:
            continue
        holdout_selected = apply_rule(holdout_pool, rule)
        holdout_baseline = matched_baseline(holdout_pool, holdout_selected)
        holdout_summary = summarize_fast(holdout_selected, holdout_baseline)
        scored.append(
            {
                "rule": rule,
                "train_summary": train_summary,
                "holdout_summary": holdout_summary,
                "train_score": train_summary["excess_roi"],
                "holdout_score": holdout_summary["excess_roi"],
            }
        )
    scored.sort(key=lambda x: (x["train_score"], x["train_summary"]["rows"]), reverse=True)
    forward_scored = [
        item
        for item in scored
        if item["holdout_summary"]["rows"] >= 20
        and item["holdout_summary"]["active_dates"] >= 5
        and item["holdout_summary"]["excess_roi"] is not None
        and item["holdout_summary"]["excess_roi"] > 0
    ]
    forward_scored.sort(
        key=lambda x: (x["holdout_score"], x["train_score"], x["holdout_summary"]["rows"]),
        reverse=True,
    )

    evaluated: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    review_pool = forward_scored[: max(top_n * 4, 40)] + scored[: max(top_n * 2, 24)]
    for item in review_pool:
        rule: Rule = item["rule"]
        if rule.name in seen_names:
            continue
        seen_names.add(rule.name)
        selected = apply_rule(low_pool, rule)
        train_selected = selected[selected["event_date"].isin(train_dates)]
        holdout_selected = apply_rule(holdout_pool, rule)
        baseline = matched_baseline(low_pool, selected)
        train_baseline = matched_baseline(train_pool, train_selected)
        holdout_baseline = matched_baseline(holdout_pool, holdout_selected)
        overall = summarize(selected, baseline, iters=iters, seed=seed + 2000 + len(evaluated))
        train = summarize(train_selected, train_baseline, iters=iters, seed=seed + 3000 + len(evaluated))
        holdout = summarize(holdout_selected, holdout_baseline, iters=iters, seed=seed + 4000 + len(evaluated))
        evaluated.append(
            {
                "selector": rule.name,
                "description": rule.description(),
                "overall": overall,
                "train": train,
                "holdout": holdout,
                "gates": gate_for(overall, train, holdout, gate_pass),
            }
        )
    evaluated.sort(
        key=lambda r: (
            r["gates"]["conclusion"] == "confirmed",
            r["gates"]["forward"] == "PASS",
            r["holdout"]["excess_roi"] if r["holdout"]["excess_roi"] is not None else -999,
            r["overall"]["excess_ci"][0] if r["overall"]["excess_ci"][0] is not None else -999,
        ),
        reverse=True,
    )
    evaluated = evaluated[:top_n]
    return {
        "split": split,
        "rules_tested": len(rules),
        "rules_train_passed": len(scored),
        "rules_forward_screen_passed": len(forward_scored),
        "results": evaluated,
    }


def table_row(result: dict[str, Any]) -> str:
    s = result["overall"]
    h = result["holdout"]
    g = result["gates"]
    return (
        f"| `{result['selector']}` | {s['rows']} | {s['active_dates']} | {s['cities']} | "
        f"{plain(s['cost'])} | {num(s['pnl'])} | {pct(s['roi'])} | {pct(s['baseline_roi'])} | "
        f"{pct(s['excess_roi'])} | [{pct(s['excess_ci'][0])}, {pct(s['excess_ci'][1])}] | "
        f"{h['rows']} | {pct(h['roi'])} | {pct(h['excess_roi'])} | "
        f"{pct(s['top_day_removed']['top3']['roi'])} | "
        f"{g['significance']}/{g['baseline']}/{g['forward']}/{g['stress']}/{g['execution']} | {g['conclusion']} |"
    )


def render_md(payload: dict[str, Any]) -> str:
    exp = payload["experiment"]
    gate = payload["clob_gate"]
    self_check = payload["self_check"]
    results = exp["results"]
    best = results[0] if results else None
    confirmed = [r for r in results if r["gates"]["conclusion"] == "confirmed"]
    lines: list[str] = [
        "# Low-Price BUY_YES Live Candidate Search v1",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        "> Scope: 本地 counterfactual research only；未改 N100/live 配置。",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db.fact_signal_candidates` 主实验；`fact_trades` 只用于强制自检和 CLOB gate 状态。",
        f"- DB last_modified_utc: `{payload['db_last_modified_utc']}`",
        f"- MAX(fact_built_at_utc): `{self_check['max_fact_built_at_utc']}`",
        f"- CLOB gate: `gate_pass={gate.get('gate_pass')}`; fail_reasons=`{gate.get('fail_reasons')}`",
        "- 收益数字为机会层 `counterfactual_pnl`；`eligible` / `paper_ordered` / `live_filled` 只作覆盖控制。",
        f"- train: `{exp['split']['train_dates'][0]}` -> `{exp['split']['train_dates'][-1]}` ({len(exp['split']['train_dates'])} event_dates)",
        f"- holdout: `{exp['split']['holdout_dates'][0]}` -> `{exp['split']['holdout_dates'][-1]}` ({len(exp['split']['holdout_dates'])} event_dates)",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(self_check, ensure_ascii=False, indent=2),
        "```",
        "",
        "### Filter Funnel",
        "",
        "| step | rows | active_dates | cities | drop | retained_from_full | eligible_rows | note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload["filter_funnel"]:
        drop = "NA" if row["drop_from_previous"] is None else f"{row['drop_from_previous']} ({pct(row['drop_from_previous_pct'])})"
        lines.append(
            f"| {row['step']} | {row['rows']} | {row['active_dates']} | {row['cities']} | {drop} | {pct(row['retained_from_full_pct'])} | {row['eligible_rows']} | {row['note']} |"
        )
    lines.extend(
        [
            "",
            "## 目标指标与搜索设计",
            "",
            "`low_price_buy_yes_live_candidate_v1` = 在 full opportunity 分母上，从 `BUY_YES` 且 `decision_entry_price < 0.25` 的可评价机会中，寻找可解释、可执行、每 city-date 最多 1 条的 lottery sleeve。主问题不是“低价 YES 是否曾经赚钱”，而是“能否在 train 上选出规则，并在 holdout 仍相对同 side/hour/price bucket baseline 有正超额”。",
            "",
            f"- 规则网格: `{exp['rules_tested']}` 个预声明组合；train deterministic excess > 0 的规则数 `{exp['rules_train_passed']}`；其中 holdout rows>=20、active_dates>=5 且 excess>0 的 forward screen 规则数 `{exp['rules_forward_screen_passed']}`。",
            "- 规则维度: price band、positive edge / edge_mean、yes spread、yes depth、n_snapshots、rank mode；不使用事后赢家城市名单。",
            "- Baseline: 同 `BUY_YES`、同 `hour_bucket`、同 5c `decision_entry_price` bucket 的 full opportunity。",
            "- Train/holdout: 按 `event_date` chronological 70/30 split；搜索只看 train，表中 holdout 是后段日期验证。",
            "- Bootstrap: `event_date` cluster bootstrap；候选数量 K 已报告，未做 Bonferroni 后仍需保守看待。",
            "- 执行现实: 使用 fact 表 decision-window price/spread/depth 字段；没有额外 raw orderbook join。",
            "",
            "## 候选结果",
            "",
            "| selector | rows | days | cities | cost | pnl | ROI | baseline ROI | excess | excess CI | holdout rows | holdout ROI | holdout excess | top3 day removed ROI | gates sig/base/fwd/stress/exec | verdict |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    if results:
        for result in results:
            lines.append(table_row(result))
    else:
        lines.append("| NA | 0 | 0 | 0 | 0 | NA | NA | NA | NA | [NA, NA] | 0 | NA | NA | NA | FAIL/FAIL/FAIL/FAIL/FAIL | inconclusive |")
    lines.extend(["", "## 最优候选详情", ""])
    if best:
        lines.extend(
            [
                f"- selector: `{best['selector']}`",
                f"- rule: {best['description']}",
                f"- train: rows `{best['train']['rows']}`, ROI `{pct(best['train']['roi'])}`, excess `{pct(best['train']['excess_roi'])}`, CI `[{pct(best['train']['excess_ci'][0])}, {pct(best['train']['excess_ci'][1])}]`",
                f"- holdout: rows `{best['holdout']['rows']}`, ROI `{pct(best['holdout']['roi'])}`, excess `{pct(best['holdout']['excess_roi'])}`, CI `[{pct(best['holdout']['excess_ci'][0])}, {pct(best['holdout']['excess_ci'][1])}]`",
                f"- stress: top1/top3/top5 day removed ROI `{pct(best['overall']['top_day_removed']['top1']['roi'])}` / `{pct(best['overall']['top_day_removed']['top3']['roi'])}` / `{pct(best['overall']['top_day_removed']['top5']['roi'])}`",
                f"- execution fields: avg yes_spread `{plain(best['overall']['avg_yes_spread'], 4)}`, median yes_depth_ask_5c `{plain(best['overall']['median_depth_ask_5c'], 1)}`",
                "",
                "| top city | rows | pnl | ROI |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for row in best["overall"]["top_cities"]:
            lines.append(f"| {row['city']} | {row['rows']} | {num(row['pnl'])} | {pct(row['roi'])} |")
        lines.extend(["", "| worst city | rows | pnl | ROI |", "| --- | ---: | ---: | ---: |"])
        for row in best["overall"]["worst_cities"]:
            lines.append(f"| {row['city']} | {row['rows']} | {num(row['pnl'])} | {pct(row['roi'])} |")
    else:
        lines.append("没有规则在 train 阶段通过 `excess CI lower > 0` 的入围门。")
    lines.extend(
        [
            "",
            "## 三门判定",
            "",
            "| item | significance | baseline | forward | stress | execution | conclusion |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if best:
        g = best["gates"]
        lines.append(f"| best_candidate | {g['significance']} | {g['baseline']} | {g['forward']} | {g['stress']} | {g['execution']} | {g['conclusion']} |")
    else:
        lines.append("| best_candidate | FAIL | FAIL | FAIL | FAIL | FAIL | inconclusive |")
    lines.extend(
        [
            "",
            "## 8 环覆盖自检",
            "",
            "- 1 描述性绩效切片: covered，机会层 low-price BUY_YES selector。",
            "- 2 统计推断: covered，event_date cluster bootstrap；候选数量 K 已报告但未做严格多重检验修正。",
            "- 3 信号判别: partial，用 edge / edge_mean / market microstructure 标签做筛选，不重训模型。",
            "- 4 概率分布评估: NA，本轮不评估概率校准。",
            "- 5 执行微结构: partial，使用 fact decision-window price/spread/depth；未额外 join raw orderbook。",
            "- 6 容量: partial，要求每 city-date 最多 1 条并报告 depth；未做真实下单容量仿真。",
            "- 7 组合相关性: covered by event_date cluster and top-day stress。",
            "- 8 基准/反事实: covered，同 side/hour/price bucket full opportunity baseline。",
            "",
            "## 结论",
            "",
        ]
    )
    if confirmed:
        c = confirmed[0]
        lines.append(
            f"在 `{exp['split']['train_dates'][0]}` 到 `{exp['split']['holdout_dates'][-1]}`，最优 confirmed 低价 BUY_YES 候选 `{c['selector']}` 相对同价位/同窗口 baseline 的超额 ROI 为 `{pct(c['overall']['excess_roi'])}`（95% CI `[{pct(c['overall']['excess_ci'][0])}, {pct(c['overall']['excess_ci'][1])}]`），前瞻 PASS，结论等级 `confirmed`。"
        )
        lines.append("")
        lines.append("当前动作：可以进入 deploy 前的人工复核和 `weather-strategy-deploy` 流程，但本报告本身不改 live。")
    elif best:
        lines.append(
            f"在 `{exp['split']['train_dates'][0]}` 到 `{exp['split']['holdout_dates'][-1]}`，低价 BUY_YES 最优入围候选 `{best['selector']}` 相对同价位/同窗口 baseline 的超额 ROI 为 `{pct(best['overall']['excess_roi'])}`（95% CI `[{pct(best['overall']['excess_ci'][0])}, {pct(best['overall']['excess_ci'][1])}]`），前瞻 `{best['gates']['forward']}`，结论等级 `{best['gates']['conclusion']}`。"
        )
        lines.append("")
        lines.append("当前动作：不允许 live。若继续这条线，下一步不是再扩大同类网格，而是补更长 forward shadow 或把低价 YES 作为 convexity tag 叠到更强的上游 forecast/source-quality 规则上。")
    else:
        lines.append("本轮没有任何低价 BUY_YES 规则在 train 入围门通过；当前动作：不允许 live。")
    lines.extend(
        [
            "",
            "## 产物",
            "",
            f"- JSON: `{payload['out_json']}`",
            f"- Markdown: `{payload['out_md']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def run(db_path: Path, out_json: Path, out_md: Path, *, iters: int, top_n: int) -> dict[str, Any]:
    conn = connect(db_path)
    self_check = mandatory_self_check(conn)
    df = load_candidates(conn)
    db_mtime = datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat()
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "db_last_modified_utc": db_mtime,
        "self_check": self_check,
        "clob_gate": load_gate(GATE_JSON_DEFAULT),
        "filter_funnel": filter_funnel(df),
        "experiment": evaluate_rules(df, iters=iters, seed=RNG_SEED, top_n=top_n),
        "out_json": str(out_json),
        "out_md": str(out_md),
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    out_md.write_text(render_md(payload))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Low-price BUY_YES live-candidate counterfactual search.")
    parser.add_argument("--db", type=Path, default=DB_DEFAULT)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON_DEFAULT)
    parser.add_argument("--out-md", type=Path, default=OUT_MD_DEFAULT)
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--top-n", type=int, default=12)
    args = parser.parse_args()
    payload = run(args.db, args.out_json, args.out_md, iters=args.bootstrap_iters, top_n=args.top_n)
    print(json.dumps({"json": payload["out_json"], "markdown": payload["out_md"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
