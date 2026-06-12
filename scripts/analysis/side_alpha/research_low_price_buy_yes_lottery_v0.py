#!/usr/bin/env python3
"""Low-price BUY_YES lottery sleeve research.

Local counterfactual research only. Main experiment uses
runtime/weather.db.fact_signal_candidates full opportunity rows. fact_trades is
used only for mandatory source self-checks and live CLOB gate status.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime/weather.db"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-12-low-price-buy-yes-lottery-v0.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-12-low-price-buy-yes-lottery-v0.md"
GATE_JSON_DEFAULT = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
TARGET_METRIC = "low_price_buy_yes_lottery_v0"
RNG_SEED = 20260612


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


def plain_num(value: float | None, digits: int = 2) -> str:
    if value is None or not np.isfinite(value):
        return "NA"
    return f"{value:.{digits}f}"


def safe_div(num_value: float, den_value: float) -> float | None:
    return num_value / den_value if den_value else None


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
    uri = f"file:{path.resolve()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
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


def hour_bucket(hours: Any) -> str:
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return "unknown"
    if not np.isfinite(h):
        return "unknown"
    if h < 12:
        return "T-00-12"
    if h < 18:
        return "T-12-18"
    if h < 24:
        return "T-18-24"
    if h < 30:
        return "T-24-30"
    return "T-30+"


def load_candidates(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = sql_rows(
        conn,
        """
        SELECT candidate_id, condition_id, market_id, side, event_date, bracket,
               city, city_pool, forecast_source, model_version, time_bucket,
               window, decision_window_label, decision_hours_to_settle,
               decision_snapshot_ts_utc, decision_window_missing, model_p_yes,
               market_yes_price, edge, abs_edge, decision_entry_price,
               eligible, paper_ordered, live_filled, settlement_status,
               final_yes, bracket_hit, win_by_count, counterfactual_pnl,
               counterfactual_pnl_best, fact_built_at_utc
        FROM fact_signal_candidates
        """,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in [
        "decision_hours_to_settle",
        "model_p_yes",
        "market_yes_price",
        "edge",
        "abs_edge",
        "decision_entry_price",
        "eligible",
        "paper_ordered",
        "live_filled",
        "final_yes",
        "bracket_hit",
        "win_by_count",
        "counterfactual_pnl",
        "counterfactual_pnl_best",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["event_date"] = df["event_date"].astype(str)
    df["hour_bucket"] = df["decision_hours_to_settle"].map(hour_bucket)
    df["price_bucket_5c"] = df["decision_entry_price"].map(price_bucket_5c)
    df["is_evaluable"] = (
        df["settlement_status"].eq("settled")
        & df["final_yes"].notna()
        & df["counterfactual_pnl"].notna()
        & df["decision_entry_price"].notna()
        & df["decision_window_missing"].fillna(1).eq(0)
        & df["side"].eq("BUY_YES")
    )
    df["is_low_price_buy_yes"] = df["side"].eq("BUY_YES") & df["decision_entry_price"].gt(0) & df[
        "decision_entry_price"
    ].lt(0.25)
    return df


def filter_funnel(df: pd.DataFrame) -> list[dict[str, Any]]:
    steps: list[tuple[str, pd.Series, str]] = [
        ("fact_signal_candidates rows", pd.Series(True, index=df.index), "full opportunity table"),
        ("BUY_YES rows", df["side"].eq("BUY_YES"), "side only; no eligible hard gate"),
        ("BUY_YES with decision entry price", df["side"].eq("BUY_YES") & df["decision_entry_price"].notna(), "decision window price available"),
        ("low-price BUY_YES <0.25", df["is_low_price_buy_yes"], "natural lottery universe before settlement"),
        (
            "settled low-price BUY_YES",
            df["is_low_price_buy_yes"] & df["settlement_status"].eq("settled") & df["final_yes"].notna(),
            "realized outcome available for counterfactual",
        ),
        (
            "decision-window present",
            df["is_low_price_buy_yes"]
            & df["settlement_status"].eq("settled")
            & df["final_yes"].notna()
            & df["decision_window_missing"].fillna(1).eq(0),
            "exclude missing decision window",
        ),
        (
            "counterfactual evaluable",
            df["is_low_price_buy_yes"] & df["is_evaluable"],
            "has authorized counterfactual_pnl and cost proxy",
        ),
    ]
    out: list[dict[str, Any]] = []
    prev_rows: int | None = None
    full_rows = len(df)
    for step, mask, note in steps:
        sub = df[mask]
        rows = int(len(sub))
        out.append(
            {
                "step": step,
                "rows": rows,
                "active_dates": int(sub["event_date"].nunique()) if rows else 0,
                "cities": int(sub["city"].nunique()) if rows else 0,
                "drop_from_previous": None if prev_rows is None else rows - prev_rows,
                "drop_from_previous_pct": None if prev_rows in (None, 0) else (rows - prev_rows) / prev_rows,
                "retained_from_full_pct": safe_div(rows, full_rows),
                "eligible_rows": int(sub["eligible"].fillna(0).sum()) if rows and "eligible" in sub else 0,
                "note": note,
            }
        )
        prev_rows = rows
    return out


def split_dates(df: pd.DataFrame, holdout_frac: float) -> dict[str, Any]:
    dates = sorted(d for d in df["event_date"].dropna().unique() if d and d != "None")
    if not dates:
        return {"train_dates": [], "holdout_dates": [], "split_index": 0}
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
    g = df.groupby("event_date", dropna=False).agg(
        rows=("candidate_id", "count"),
        cost=("decision_entry_price", "sum"),
        pnl=("counterfactual_pnl", "sum"),
    ).reset_index()
    g["roi"] = g.apply(lambda r: safe_div(float(r["pnl"]), float(r["cost"])), axis=1)
    return g.sort_values("event_date")


def grouped_concentration(df: pd.DataFrame, key_cols: list[str], limit: int = 5) -> list[dict[str, Any]]:
    if df.empty:
        return []
    g = df.groupby(key_cols, dropna=False).agg(
        rows=("candidate_id", "count"),
        cost=("decision_entry_price", "sum"),
        pnl=("counterfactual_pnl", "sum"),
    ).reset_index()
    g["roi"] = g.apply(lambda r: safe_div(float(r["pnl"]), float(r["cost"])), axis=1)
    return g.sort_values("pnl", ascending=False).head(limit).to_dict("records")


def bottom_grouped(df: pd.DataFrame, key_cols: list[str], limit: int = 5) -> list[dict[str, Any]]:
    if df.empty:
        return []
    g = df.groupby(key_cols, dropna=False).agg(
        rows=("candidate_id", "count"),
        cost=("decision_entry_price", "sum"),
        pnl=("counterfactual_pnl", "sum"),
    ).reset_index()
    g["roi"] = g.apply(lambda r: safe_div(float(r["pnl"]), float(r["cost"])), axis=1)
    return g.sort_values("pnl").head(limit).to_dict("records")


def positive_share(df: pd.DataFrame, key_cols: list[str], top_n: int) -> float | None:
    if df.empty:
        return None
    g = df.groupby(key_cols, dropna=False).agg(pnl=("counterfactual_pnl", "sum")).reset_index()
    positives = g[g["pnl"] > 0]
    total_pos = float(positives["pnl"].sum())
    if total_pos <= 0:
        return None
    top = float(positives.sort_values("pnl", ascending=False).head(top_n)["pnl"].sum())
    return top / total_pos


def remove_top_units(df: pd.DataFrame, key_cols: list[str], n: int) -> dict[str, Any]:
    if df.empty:
        return {"roi": None, "rows": 0, "removed": []}
    g = df.groupby(key_cols, dropna=False).agg(pnl=("counterfactual_pnl", "sum")).reset_index()
    removed = g.sort_values("pnl", ascending=False).head(n)
    if removed.empty:
        return {"roi": roi(df), "rows": int(len(df)), "removed": []}
    marker = set(tuple(row[col] for col in key_cols) for _, row in removed.iterrows())
    keep_mask = ~df[key_cols].apply(lambda row: tuple(row[col] for col in key_cols) in marker, axis=1)
    kept = df[keep_mask]
    return {
        "roi": roi(kept),
        "rows": int(len(kept)),
        "removed": removed.to_dict("records"),
    }


def bootstrap_roi(df: pd.DataFrame, *, iters: int, seed: int) -> list[float]:
    groups = {str(k): v for k, v in df.groupby("event_date", dropna=False).groups.items()}
    dates = sorted(groups)
    if not dates:
        return []
    rng = random.Random(seed)
    out: list[float] = []
    for _ in range(iters):
        cost = 0.0
        pnl = 0.0
        for d in (rng.choice(dates) for _ in dates):
            part = df.loc[groups[d]]
            cost += float(part["decision_entry_price"].sum())
            pnl += float(part["counterfactual_pnl"].sum())
        value = safe_div(pnl, cost)
        if value is not None and np.isfinite(value):
            out.append(value)
    return out


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


def matched_baseline(pool: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pool.iloc[0:0].copy()
    keys = set(zip(selected["hour_bucket"], selected["price_bucket_5c"]))
    key_frame = pool[["hour_bucket", "price_bucket_5c"]]
    mask = [(row.hour_bucket, row.price_bucket_5c) in keys for row in key_frame.itertuples(index=False)]
    return pool.loc[mask].copy()


def summarize_slice(selected: pd.DataFrame, baseline: pd.DataFrame, *, iters: int, seed: int) -> dict[str, Any]:
    selected_roi = roi(selected)
    baseline_roi = roi(baseline)
    excess = None if selected_roi is None or baseline_roi is None else selected_roi - baseline_roi
    daily = daily_frame(selected)
    worst = daily.sort_values("pnl").head(1).to_dict("records")
    best = daily.sort_values("pnl", ascending=False).head(1).to_dict("records")
    roi_dist = bootstrap_roi(selected, iters=iters, seed=seed)
    excess_dist = bootstrap_excess(selected, baseline, iters=iters, seed=seed + 17)
    return {
        "rows": int(len(selected)),
        "active_dates": int(selected["event_date"].nunique()) if len(selected) else 0,
        "cities": int(selected["city"].nunique()) if len(selected) else 0,
        "city_dates": int(selected[["city", "event_date"]].drop_duplicates().shape[0]) if len(selected) else 0,
        "eligible_rows": int(selected["eligible"].fillna(0).sum()) if len(selected) else 0,
        "paper_ordered": int(selected["paper_ordered"].fillna(0).sum()) if len(selected) else 0,
        "live_filled": int(selected["live_filled"].fillna(0).sum()) if len(selected) else 0,
        "cost": float(selected["decision_entry_price"].sum()) if len(selected) else 0.0,
        "pnl": float(selected["counterfactual_pnl"].sum()) if len(selected) else 0.0,
        "roi": selected_roi,
        "roi_ci": ci95(roi_dist),
        "hit_rate": safe_div(float(selected["win_by_count"].fillna(0).sum()), float(len(selected))) if len(selected) else None,
        "avg_entry_price": float(selected["decision_entry_price"].mean()) if len(selected) else None,
        "avg_abs_edge": float(selected["abs_edge"].mean()) if len(selected) else None,
        "baseline_rows": int(len(baseline)),
        "baseline_cost": float(baseline["decision_entry_price"].sum()) if len(baseline) else 0.0,
        "baseline_pnl": float(baseline["counterfactual_pnl"].sum()) if len(baseline) else 0.0,
        "baseline_roi": baseline_roi,
        "excess_roi": excess,
        "excess_ci": ci95(excess_dist),
        "positive_day_rate": float((daily["pnl"] > 0).mean()) if len(daily) else None,
        "worst_day": worst[0] if worst else None,
        "best_day": best[0] if best else None,
        "top_day_removed": {
            f"top{n}": remove_top_units(selected, ["event_date"], n) for n in (1, 3, 5)
        },
        "top_candidate_removed": {
            f"top{n}": remove_top_units(selected, ["candidate_id"], n) for n in (1, 3, 5)
        },
        "top_cities": grouped_concentration(selected, ["city"], 5),
        "worst_cities": bottom_grouped(selected, ["city"], 5),
        "top_city_dates": grouped_concentration(selected, ["city", "event_date"], 5),
        "worst_city_dates": bottom_grouped(selected, ["city", "event_date"], 5),
        "concentration": {
            "top1_city_positive_pnl_share": positive_share(selected, ["city"], 1),
            "top3_city_positive_pnl_share": positive_share(selected, ["city"], 3),
            "top1_date_positive_pnl_share": positive_share(selected, ["event_date"], 1),
            "top3_date_positive_pnl_share": positive_share(selected, ["event_date"], 3),
            "top1_city_date_positive_pnl_share": positive_share(selected, ["city", "event_date"], 1),
            "top3_city_date_positive_pnl_share": positive_share(selected, ["city", "event_date"], 3),
        },
    }


def selector_masks(pool: pd.DataFrame) -> dict[str, tuple[str, pd.Series]]:
    price = pool["decision_entry_price"]
    return {
        "price_lt_010": ("BUY_YES price <0.10", price.gt(0) & price.lt(0.10)),
        "price_010_020": ("BUY_YES price 0.10-0.20", price.ge(0.10) & price.lt(0.20)),
        "price_020_025": ("BUY_YES price 0.20-0.25", price.ge(0.20) & price.lt(0.25)),
        "old_side_band_lottery_leg": (
            "old side-band lottery leg: BUY_YES 0.20-0.25 and abs_edge>=0.20",
            price.ge(0.20) & price.lt(0.25) & pool["abs_edge"].ge(0.20),
        ),
    }


def gates_for(summary: dict[str, Any], holdout: dict[str, Any], *, identity_baseline: bool) -> dict[str, str]:
    lo, hi = summary.get("excess_ci") or [None, None]
    h_excess = holdout.get("excess_roi")
    h_rows = holdout.get("rows", 0)
    if identity_baseline:
        baseline = "NA"
        significance = "NA"
    else:
        significance = "PASS" if lo is not None and hi is not None and lo > 0 else "FAIL"
        baseline = significance
    forward = "PASS" if (not identity_baseline and h_rows >= 10 and h_excess is not None and h_excess > 0) else "FAIL"
    conclusion = "inconclusive"
    if significance == "PASS" and baseline == "PASS" and forward == "PASS":
        conclusion = "shadow_candidate"
    return {
        "significance": significance,
        "baseline": baseline,
        "forward": forward,
        "conclusion": conclusion,
    }


def run_experiment(df: pd.DataFrame, *, iters: int, seed: int) -> dict[str, Any]:
    low_pool = df[df["is_low_price_buy_yes"] & df["is_evaluable"]].copy()
    split = split_dates(low_pool, 0.30)
    train_dates = set(split["train_dates"])
    holdout_dates = set(split["holdout_dates"])
    selectors = selector_masks(low_pool)
    results = []
    for name, (description, mask) in selectors.items():
        selected = low_pool[mask].copy()
        baseline = matched_baseline(low_pool, selected)
        train_selected = selected[selected["event_date"].isin(train_dates)]
        train_baseline = baseline[baseline["event_date"].isin(train_dates)]
        holdout_selected = selected[selected["event_date"].isin(holdout_dates)]
        holdout_baseline = baseline[baseline["event_date"].isin(holdout_dates)]
        identity_baseline = name in {"price_lt_010", "price_010_020", "price_020_025"}
        overall = summarize_slice(selected, baseline, iters=iters, seed=seed)
        train = summarize_slice(train_selected, train_baseline, iters=iters, seed=seed + 101)
        holdout = summarize_slice(holdout_selected, holdout_baseline, iters=iters, seed=seed + 202)
        results.append(
            {
                "selector": name,
                "description": description,
                "baseline": "same BUY_YES, same hour_bucket, same 5c price_bucket full opportunity",
                "identity_baseline": identity_baseline,
                "overall": overall,
                "train": train,
                "holdout": holdout,
                "gates": gates_for(overall, holdout, identity_baseline=identity_baseline),
            }
        )

    eligible_control = []
    eligible_pool = low_pool[low_pool["eligible"].fillna(0).eq(1)].copy()
    for name, (description, mask) in selector_masks(eligible_pool).items():
        selected = eligible_pool[mask].copy()
        baseline = matched_baseline(eligible_pool, selected)
        eligible_control.append(
            {
                "selector": name,
                "description": description,
                "note": "eligible=1 control only; not used as main denominator",
                "overall": summarize_slice(selected, baseline, iters=iters, seed=seed + 303),
            }
        )
    return {
        "split": split,
        "results": results,
        "eligible_control": eligible_control,
    }


def table_row(label: str, s: dict[str, Any], gates: dict[str, str] | None = None) -> str:
    gate = "" if gates is None else f" | {gates['significance']}/{gates['baseline']}/{gates['forward']} | {gates['conclusion']}"
    return (
        f"| {label} | {s['rows']} | {s['active_dates']} | {s['cities']} | "
        f"{plain_num(s['cost'])} | {num(s['pnl'])} | {pct(s['roi'])} | "
        f"{pct(s['excess_roi'])} | [{pct(s['excess_ci'][0])}, {pct(s['excess_ci'][1])}] | "
        f"{pct(s['top_day_removed']['top1']['roi'])} / {pct(s['top_day_removed']['top3']['roi'])} / {pct(s['top_day_removed']['top5']['roi'])} | "
        f"{num(s['worst_day']['pnl']) if s['worst_day'] else 'NA'}"
        f"{gate} |"
    )


def render_md(payload: dict[str, Any]) -> str:
    gate = payload["clob_gate"]
    self_check = payload["self_check"]
    exp = payload["experiment"]
    lines: list[str] = [
        "# Low-Price BUY_YES Lottery Sleeve v0",
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
        "- 因 gate 未通过，本报告不发布 live_real PnL/ROI；所有收益数字均为机会层 `counterfactual_pnl`。",
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
            "## 目标指标与设计",
            "",
            "`low_price_buy_yes_lottery_v0` = 在 full opportunity 分母上，只看 `BUY_YES` 且 `decision_entry_price < 0.25` 的可评价机会，用授权字段 `counterfactual_pnl` / `decision_entry_price` 计算持有到结算的机会层 ROI。`eligible=1` 只作为对照，不作为主实验硬门。",
            "",
            "- Baseline: 同 `BUY_YES`、同 `hour_bucket`、同 5c `decision_entry_price` bucket 的 full opportunity。",
            "- Train/holdout: 按 `event_date` chronological 70/30 split。",
            "- Bootstrap: `event_date` cluster bootstrap。",
            "- Raw orderbook: 本轮未额外 join raw orderbook；使用 fact 表中的 decision window price，因此没有新增 `snapshot_ts <= decision_snapshot_ts` join 风险。",
            "",
            "## 主结果",
            "",
            "| selector | rows | days | cities | cost | pnl | ROI | excess | excess CI | top day removed top1/top3/top5 ROI | worst_day_pnl | gates | verdict |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | --- | --- |",
        ]
    )
    for result in exp["results"]:
        lines.append(table_row(result["selector"], result["overall"], result["gates"]))
    lines.extend(
        [
            "",
            "说明：前三个 price-only selector 的 matched baseline 与 selector 本身同分母，因此 `excess=0`、baseline/significance 标 `NA`；它们用于观察低价 YES 自身的偏度、坏日和集中度。`old_side_band_lottery_leg` 才是相对同价位 full opportunity 的筛选检验。",
            "",
            "## Train / Holdout",
            "",
            "| selector | train rows | train ROI | train excess CI | holdout rows | holdout ROI | holdout excess CI | holdout worst day |",
            "| --- | ---: | ---: | --- | ---: | ---: | --- | ---: |",
        ]
    )
    for result in exp["results"]:
        tr = result["train"]
        ho = result["holdout"]
        lines.append(
            f"| {result['selector']} | {tr['rows']} | {pct(tr['roi'])} | [{pct(tr['excess_ci'][0])}, {pct(tr['excess_ci'][1])}] | "
            f"{ho['rows']} | {pct(ho['roi'])} | [{pct(ho['excess_ci'][0])}, {pct(ho['excess_ci'][1])}] | "
            f"{num(ho['worst_day']['pnl']) if ho['worst_day'] else 'NA'} |"
        )
    lines.extend(["", "## Top Removed / Worst Day / Concentration", ""])
    for result in exp["results"]:
        overall = result["overall"]
        lines.extend(
            [
                f"### {result['selector']}",
                "",
                f"- top day removed ROI: top1 `{pct(overall['top_day_removed']['top1']['roi'])}`, top3 `{pct(overall['top_day_removed']['top3']['roi'])}`, top5 `{pct(overall['top_day_removed']['top5']['roi'])}`.",
                f"- top candidate removed ROI: top1 `{pct(overall['top_candidate_removed']['top1']['roi'])}`, top3 `{pct(overall['top_candidate_removed']['top3']['roi'])}`, top5 `{pct(overall['top_candidate_removed']['top5']['roi'])}`.",
                f"- worst_day: `{overall['worst_day']['event_date'] if overall['worst_day'] else 'NA'}` pnl `{num(overall['worst_day']['pnl']) if overall['worst_day'] else 'NA'}`.",
                f"- positive PnL concentration: top1 city `{pct(overall['concentration']['top1_city_positive_pnl_share'])}`, top3 city `{pct(overall['concentration']['top3_city_positive_pnl_share'])}`, top1 date `{pct(overall['concentration']['top1_date_positive_pnl_share'])}`, top3 date `{pct(overall['concentration']['top3_date_positive_pnl_share'])}`, top1 city-date `{pct(overall['concentration']['top1_city_date_positive_pnl_share'])}`.",
                "",
                "| top city | rows | pnl | ROI |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for row in overall["top_cities"][:5]:
            lines.append(f"| {row['city']} | {row['rows']} | {num(row['pnl'])} | {pct(row['roi'])} |")
        lines.extend(["", "| top city-date | rows | pnl | ROI |", "| --- | ---: | ---: | ---: |"])
        for row in overall["top_city_dates"][:5]:
            lines.append(f"| {row['city']} {row['event_date']} | {row['rows']} | {num(row['pnl'])} | {pct(row['roi'])} |")
        lines.append("")
    lines.extend(
        [
            "## Eligible=1 Control",
            "",
            "这段只作为旧 eligible 口径对照；主实验不使用它做硬门。",
            "",
            "| selector | eligible rows | ROI | excess | excess CI |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in exp["eligible_control"]:
        s = row["overall"]
        lines.append(
            f"| {row['selector']} | {s['rows']} | {pct(s['roi'])} | {pct(s['excess_roi'])} | [{pct(s['excess_ci'][0])}, {pct(s['excess_ci'][1])}] |"
        )
    old_leg = next(r for r in exp["results"] if r["selector"] == "old_side_band_lottery_leg")
    price_low = next(r for r in exp["results"] if r["selector"] == "price_lt_010")
    price_mid = next(r for r in exp["results"] if r["selector"] == "price_010_020")
    price_high = next(r for r in exp["results"] if r["selector"] == "price_020_025")
    lines.extend(
        [
            "",
            "## 三门判定",
            "",
            "| selector | significance | baseline | forward | conclusion |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for result in exp["results"]:
        g = result["gates"]
        lines.append(f"| {result['selector']} | {g['significance']} | {g['baseline']} | {g['forward']} | {g['conclusion']} |")
    lines.extend(
        [
            "",
            "## 8 环覆盖自检",
            "",
            "- 1 描述性绩效切片: covered，机会层 low-price BUY_YES selector。",
            "- 2 统计推断: covered，event_date cluster bootstrap。",
            "- 3 信号判别: partial，仅检验 price/old side-band edge gate，不重训模型。",
            "- 4 概率分布评估: NA，本轮不评估概率校准。",
            "- 5 执行微结构: partial，使用 fact decision price/spread 派生字段；未额外 join raw orderbook。",
            "- 6 容量: NA。",
            "- 7 组合相关性: covered by event_date cluster and city/date concentration。",
            "- 8 基准/反事实: covered，同 side/window/price bucket full opportunity baseline。",
            "",
            "## 结论",
            "",
            (
                "这个方向目前的意思是：低价 BUY_YES 更像高偏度、日期和城市集中度很强的机会层彩票暴露，"
                "而不是已经能从旧 side-band 规则里单独提炼出的可复制 sleeve。"
                f"<0.10 / 0.10-0.20 / 0.20-0.25 三档 ROI 分别为 `{pct(price_low['overall']['roi'])}`、`{pct(price_mid['overall']['roi'])}`、`{pct(price_high['overall']['roi'])}`，"
                f"但 price-only selector 的 matched baseline 是自身；旧 side-band lottery leg 相对同价位同窗口 baseline 的 overall excess 为 `{pct(old_leg['overall']['excess_roi'])}`，95% CI `[{pct(old_leg['overall']['excess_ci'][0])}, {pct(old_leg['overall']['excess_ci'][1])}]`，holdout excess 为 `{pct(old_leg['holdout']['excess_roi'])}`。"
            ),
            "",
            (
                "它赚/亏主要来自少数 event_date 和 city-date 命中；top day / top candidate removed 后 ROI 明显回落，"
                f"其中 `0.20-0.25` 桶 top5 day removed ROI 为 `{pct(price_high['overall']['top_day_removed']['top5']['roi'])}`，"
                f"old side-band lottery leg top5 day removed ROI 为 `{pct(old_leg['overall']['top_day_removed']['top5']['roi'])}`。"
            ),
            "",
            (
                "最大问题是样本有效分母从全机会到可评价低价 BUY_YES 掉数很大，且 old side-band lottery leg 的筛选样本更薄，"
                "excess CI 跨 0，holdout 也不能把筛选效果和同价位 base-rate 分开。"
            ),
            "",
            (
                "如果放宽/修正 decision window 覆盖、延长样本、或加入真实可成交 orderbook sleeve 成本，点估计有可能反转；"
                "但当前 evidence 更支持把它当作待观测的凸性风险/收益来源，而不是 live 规则。当前动作：仅研究，不允许 live。"
            ),
            "",
            "## 产物",
            "",
            f"- JSON: `{payload['out_json']}`",
            f"- Markdown: `{payload['out_md']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def run(db_path: Path, out_json: Path, out_md: Path, *, iters: int) -> dict[str, Any]:
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
        "experiment": run_experiment(df, iters=iters, seed=RNG_SEED),
        "out_json": str(out_json),
        "out_md": str(out_md),
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    out_md.write_text(render_md(payload))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Low-price BUY_YES lottery sleeve counterfactual research.")
    parser.add_argument("--db", type=Path, default=DB_DEFAULT)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON_DEFAULT)
    parser.add_argument("--out-md", type=Path, default=OUT_MD_DEFAULT)
    parser.add_argument("--bootstrap-iters", type=int, default=2000)
    args = parser.parse_args()
    payload = run(args.db, args.out_json, args.out_md, iters=args.bootstrap_iters)
    print(json.dumps({"json": payload["out_json"], "markdown": payload["out_md"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
