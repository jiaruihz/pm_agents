#!/usr/bin/env python3
"""Build a reusable forecast quality / reliability base layer.

This is opportunity-grain research. It uses fact_signal_candidates as the
canonical source for forecast/market/settlement labels and only touches
fact_trades for the mandatory analysis self-check.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "runtime/weather.db"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-13-forecast-quality-base-v0.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-13-forecast-quality-base-v0.md"
TARGET_METRIC = "forecast_quality_reliability_base_v0"
SEED = 20260613


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x * 100:+.1f}%"


def num(x: float | None, digits: int = 3) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x:.{digits}f}"


def money(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x:+.2f}"


def parse_bracket_value(label: Any) -> float | None:
    if label is None:
        return None
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(label))
    if not nums:
        return None
    vals = [float(x) for x in nums]
    return sum(vals) / len(vals)


def entropy(probs: np.ndarray) -> float:
    p = probs[np.isfinite(probs) & (probs > 0)]
    if len(p) == 0:
        return float("nan")
    h = -float(np.sum(p * np.log(p)))
    return h / math.log(len(probs)) if len(probs) > 1 else 0.0


def q(series: pd.Series, quantile: float, fallback: float) -> float:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return fallback
    return float(values.quantile(quantile))


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def run_sql_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def run_sql_scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def mandatory_self_check(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "max_fact_built_at_utc": run_sql_scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class_distribution": run_sql_rows(
            conn,
            "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
        ),
        "settlement_status_distribution": run_sql_rows(
            conn,
            "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
            "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "candidate_coverage": run_sql_rows(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        )[0],
        "order_fill_coverage": run_sql_rows(
            conn,
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
        ),
    }


def load_candidates(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT
          candidate_id, condition_id, market_id, side, event_date, bracket, city,
          city_pool, forecast_source, model_version, decision_window_label,
          decision_hours_to_settle, decision_snapshot_ts_utc,
          decision_window_missing, model_p_yes, market_yes_price,
          decision_entry_price, yes_spread, no_spread, eligible,
          settlement_status, final_yes, fact_built_at_utc
        FROM fact_signal_candidates
        WHERE decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
        """,
        conn,
    )
    for col in ["model_p_yes", "market_yes_price", "decision_entry_price", "final_yes"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["bracket_value"] = df["bracket"].map(parse_bracket_value)
    return df.dropna(subset=["bracket_value", "model_p_yes", "market_yes_price"])


def split_dates(df: pd.DataFrame) -> tuple[set[str], set[str], dict[str, Any]]:
    dates = sorted(str(x) for x in df["event_date"].dropna().unique())
    if len(dates) <= 1:
        return set(dates), set(), {"method": "event_date chronological 70/30 split", "error": "too few dates"}
    cut = int(math.floor(len(dates) * 0.7))
    cut = max(1, min(cut, len(dates) - 1))
    train = set(dates[:cut])
    holdout = set(dates[cut:])
    return train, holdout, {
        "method": "event_date chronological 70/30 split",
        "train_start": min(train),
        "train_end": max(train),
        "holdout_start": min(holdout),
        "holdout_end": max(holdout),
        "train_dates": len(train),
        "holdout_dates": len(holdout),
    }


def build_decision_sets(cands: pd.DataFrame) -> pd.DataFrame:
    settled = cands[cands["final_yes"].notna()].copy()
    group_cols = ["city", "event_date", "forecast_source", "model_version", "decision_snapshot_ts_utc"]
    rows: list[dict[str, Any]] = []
    for key, g in settled.groupby(group_cols, dropna=False):
        legs = (
            g.sort_values(["bracket_value", "bracket", "side"])
            .groupby("bracket", as_index=False, dropna=False)
            .agg(
                bracket_value=("bracket_value", "first"),
                model_p_yes=("model_p_yes", "median"),
                market_yes_price=("market_yes_price", "median"),
                final_yes=("final_yes", "max"),
                city_pool=("city_pool", "first"),
                decision_window_label=("decision_window_label", "first"),
                decision_hours_to_settle=("decision_hours_to_settle", "median"),
                condition_id=("condition_id", "first"),
                market_id=("market_id", "first"),
            )
            .sort_values(["bracket_value", "bracket"])
            .reset_index(drop=True)
        )
        if len(legs) < 3:
            continue
        model_raw = legs["model_p_yes"].clip(lower=0).to_numpy(dtype=float)
        market_raw = legs["market_yes_price"].clip(lower=0).to_numpy(dtype=float)
        if model_raw.sum() <= 0 or market_raw.sum() <= 0:
            continue
        model = model_raw / model_raw.sum()
        market = market_raw / market_raw.sum()
        final_hits = legs.index[legs["final_yes"] >= 0.5].tolist()
        if len(final_hits) != 1:
            continue
        final_i = int(final_hits[0])
        mode_i = int(np.argmax(model))
        market_mode_i = int(np.argmax(market))
        idx = np.arange(len(legs))
        adj2_mask = np.abs(idx - mode_i) <= 1
        adj3_mask = np.abs(idx - mode_i) <= 2
        outer_n = max(1, math.ceil(len(legs) * 0.2))
        outer_mask = (idx < outer_n) | (idx >= len(legs) - outer_n)
        values = legs["bracket_value"].to_numpy(dtype=float)
        expected = float(np.sum(model * values))
        variance = float(np.sum(model * (values - expected) ** 2))
        row = dict(zip(group_cols, key))
        row.update(
            {
                "decision_set_id": "|".join(str(x) for x in key),
                "city_pool": str(legs["city_pool"].iloc[0]),
                "decision_window_label": str(legs["decision_window_label"].iloc[0]),
                "decision_hours_to_settle": float(pd.to_numeric(legs["decision_hours_to_settle"], errors="coerce").median()),
                "n_brackets": int(len(legs)),
                "mode_i": mode_i,
                "market_mode_i": market_mode_i,
                "final_i": final_i,
                "model_mode_bracket": str(legs.loc[mode_i, "bracket"]),
                "market_mode_bracket": str(legs.loc[market_mode_i, "bracket"]),
                "final_bracket": str(legs.loc[final_i, "bracket"]),
                "mode_final_distance": int(abs(final_i - mode_i)),
                "model_entropy": entropy(model),
                "market_entropy": entropy(market),
                "model_mode_probability": float(model[mode_i]),
                "market_mode_probability": float(market[market_mode_i]),
                "model_adjacent2_mass": float(model[adj2_mask].sum()),
                "model_adjacent3_mass": float(model[adj3_mask].sum()),
                "market_adjacent3_mass": float(market[adj3_mask].sum()),
                "model_tail_mass_outside_adjacent3": float(model[~adj3_mask].sum()),
                "market_tail_mass_outside_adjacent3": float(market[~adj3_mask].sum()),
                "model_tail_mass_outer20": float(model[outer_mask].sum()),
                "market_tail_mass_outer20": float(market[outer_mask].sum()),
                "model_market_l1_gap": float(np.abs(model - market).sum()),
                "model_market_mode_distance": int(abs(market_mode_i - mode_i)),
                "model_market_entropy_gap": float(entropy(model) - entropy(market)),
                "distribution_variance": variance,
                "final_in_model_mode": int(final_i == mode_i),
                "final_in_model_adjacent2": int(abs(final_i - mode_i) <= 1),
                "final_in_model_adjacent3": int(abs(final_i - mode_i) <= 2),
                "tail_miss": int(abs(final_i - mode_i) > 2),
                "legs_json": json.dumps(
                    [
                        {
                            "bracket": str(r["bracket"]),
                            "condition_id": str(r["condition_id"]),
                            "market_id": str(r["market_id"]),
                            "model_p_yes": float(r["model_p_yes"]),
                            "market_yes_price": float(r["market_yes_price"]),
                            "final_yes": float(r["final_yes"]),
                        }
                        for _, r in legs.iterrows()
                    ],
                    ensure_ascii=False,
                ),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def add_cross_model_features(rows: pd.DataFrame) -> pd.DataFrame:
    out = rows.copy()
    out["cross_model_mode_distance"] = np.nan
    out["cross_model_l1_gap_proxy"] = np.nan
    for _, idxs in out.groupby(["city", "event_date", "decision_snapshot_ts_utc"], dropna=False).groups.items():
        sub = out.loc[list(idxs)]
        if len(sub) < 2:
            continue
        for idx, current in sub.iterrows():
            other = sub[sub.index != idx]
            out.loc[idx, "cross_model_mode_distance"] = float((other["mode_i"] - current["mode_i"]).abs().mean())
            out.loc[idx, "cross_model_l1_gap_proxy"] = float((other["model_mode_probability"] - current["model_mode_probability"]).abs().mean())
    return out


def add_historical_features(rows: pd.DataFrame) -> pd.DataFrame:
    out = rows.sort_values(["event_date", "city", "model_version"]).copy()
    out["city_model_hist_n"] = 0
    out["city_model_hist_mode_hit"] = np.nan
    out["city_model_hist_adj3_hit"] = np.nan
    out["city_model_hist_mean_distance"] = np.nan
    for _, idxs in out.groupby(["city", "forecast_source", "model_version"], dropna=False).groups.items():
        hist_mode: list[int] = []
        hist_adj3: list[int] = []
        hist_distance: list[float] = []
        for idx in idxs:
            out.loc[idx, "city_model_hist_n"] = len(hist_mode)
            if hist_mode:
                out.loc[idx, "city_model_hist_mode_hit"] = float(np.mean(hist_mode))
                out.loc[idx, "city_model_hist_adj3_hit"] = float(np.mean(hist_adj3))
                out.loc[idx, "city_model_hist_mean_distance"] = float(np.mean(hist_distance))
            hist_mode.append(int(out.loc[idx, "final_in_model_mode"]))
            hist_adj3.append(int(out.loc[idx, "final_in_model_adjacent3"]))
            hist_distance.append(float(out.loc[idx, "mode_final_distance"]))
    out["city_model_hist_adj3_hit_filled"] = out["city_model_hist_adj3_hit"].fillna(float(out["final_in_model_adjacent3"].mean()))
    out["city_model_hist_mode_hit_filled"] = out["city_model_hist_mode_hit"].fillna(float(out["final_in_model_mode"].mean()))
    out["city_model_hist_distance_filled"] = out["city_model_hist_mean_distance"].fillna(float(out["mode_final_distance"].mean()))
    return out


def add_quality_labels(rows: pd.DataFrame, train_dates: set[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    train = rows[rows["event_date"].astype(str).isin(train_dates)]
    out = rows.copy()
    thresholds = {
        "entropy_q25": q(train["model_entropy"], 0.25, 0.55),
        "entropy_q50": q(train["model_entropy"], 0.50, 0.65),
        "entropy_q75": q(train["model_entropy"], 0.75, 0.75),
        "mode_prob_q25": q(train["model_mode_probability"], 0.25, 0.20),
        "mode_prob_q75": q(train["model_mode_probability"], 0.75, 0.35),
        "adj3_q25": q(train["model_adjacent3_mass"], 0.25, 0.65),
        "adj3_q50": q(train["model_adjacent3_mass"], 0.50, 0.75),
        "adj3_q75": q(train["model_adjacent3_mass"], 0.75, 0.85),
        "tail_q25": q(train["model_tail_mass_outside_adjacent3"], 0.25, 0.05),
        "tail_q50": q(train["model_tail_mass_outside_adjacent3"], 0.50, 0.12),
        "tail_q75": q(train["model_tail_mass_outside_adjacent3"], 0.75, 0.25),
        "l1_q75": q(train["model_market_l1_gap"], 0.75, 0.80),
        "market_tail_q75": q(train["market_tail_mass_outside_adjacent3"], 0.75, 0.25),
        "variance_q75": q(train["distribution_variance"], 0.75, 3.0),
        "hist_adj3_q33": q(train[train["city_model_hist_n"] >= 3]["city_model_hist_adj3_hit"], 0.33, 0.80),
        "hist_adj3_q67": q(train[train["city_model_hist_n"] >= 3]["city_model_hist_adj3_hit"], 0.67, 0.95),
        "hist_distance_q67": q(train[train["city_model_hist_n"] >= 3]["city_model_hist_mean_distance"], 0.67, 1.5),
        "cross_model_distance_q75": q(train["cross_model_mode_distance"], 0.75, 2.0),
    }
    cross_dist = out["cross_model_mode_distance"].fillna(0.0)
    out["model_market_disagreement_high"] = (
        (out["model_market_l1_gap"] >= thresholds["l1_q75"])
        | (out["model_market_mode_distance"] >= 2)
        | (cross_dist >= thresholds["cross_model_distance_q75"])
    ).astype(int)
    out["high_uncertainty"] = (
        (out["model_entropy"] >= thresholds["entropy_q75"])
        | (out["model_mode_probability"] <= thresholds["mode_prob_q25"])
        | (out["model_adjacent3_mass"] <= thresholds["adj3_q25"])
    ).astype(int)
    out["sharp_model_confident"] = (
        (out["model_entropy"] <= thresholds["entropy_q25"])
        & (out["model_mode_probability"] >= thresholds["mode_prob_q75"])
        & (out["model_adjacent3_mass"] >= thresholds["adj3_q75"])
    ).astype(int)
    eps = 1e-12
    out["tail_risk_high"] = (
        (out["model_tail_mass_outside_adjacent3"] > thresholds["tail_q75"] + eps)
        | (out["distribution_variance"] >= thresholds["variance_q75"])
    ).astype(int)
    hist_enough = out["city_model_hist_n"] >= 3
    out["city_model_reliable"] = (
        hist_enough
        & (out["city_model_hist_adj3_hit"] >= thresholds["hist_adj3_q67"])
        & (out["city_model_hist_mean_distance"] <= thresholds["hist_distance_q67"])
    ).astype(int)
    out["city_model_unreliable"] = (
        hist_enough
        & (
            (out["city_model_hist_adj3_hit"] < thresholds["hist_adj3_q33"])
            | (out["city_model_hist_mean_distance"] > thresholds["hist_distance_q67"])
        )
    ).astype(int)
    out["market_lag_candidate"] = (
        (out["model_tail_mass_outside_adjacent3"] <= thresholds["tail_q25"] + eps)
        & (out["market_tail_mass_outside_adjacent3"] > thresholds["market_tail_q75"] + eps)
        & (out["model_market_l1_gap"] >= thresholds["l1_q75"])
    ).astype(int)
    out["forecast_quality_high"] = (
        (out["sharp_model_confident"] == 1)
        & (out["tail_risk_high"] == 0)
        & (out["model_market_disagreement_high"] == 0)
    ).astype(int)
    out["forecast_quality_low"] = (
        (out["high_uncertainty"] == 1)
        | (out["tail_risk_high"] == 1)
        | (out["city_model_unreliable"] == 1)
    ).astype(int)
    out["forecast_quality_medium_plus"] = (
        (out["forecast_quality_low"] == 0)
        & (
            (out["forecast_quality_high"] == 1)
            | (
                (out["model_adjacent3_mass"] >= thresholds["adj3_q50"])
                & (out["model_tail_mass_outside_adjacent3"] <= thresholds["tail_q50"] + eps)
            )
            | (out["city_model_reliable"] == 1)
        )
    ).astype(int)
    out["forecast_quality_medium"] = (
        (out["forecast_quality_low"] == 0) & (out["forecast_quality_high"] == 0)
    ).astype(int)
    return out, thresholds


def bootstrap_mean_by_date(
    df: pd.DataFrame,
    value_col: str,
    n_boot: int = 2000,
    seed: int = SEED,
) -> dict[str, Any]:
    if df.empty:
        return {"mean": None, "ci95": [None, None], "dates": 0}
    daily = df.groupby("event_date", dropna=False)[value_col].mean().dropna()
    if daily.empty:
        return {"mean": None, "ci95": [None, None], "dates": 0}
    vals = daily.to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        idx = rng.integers(0, len(vals), len(vals))
        boots[i] = float(np.mean(vals[idx]))
    return {
        "mean": float(np.mean(vals)),
        "ci95": [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))],
        "dates": int(len(daily)),
    }


def bootstrap_roi(df: pd.DataFrame, n_boot: int = 2000, seed: int = SEED) -> dict[str, Any]:
    if df.empty or float(df["cost"].sum()) <= 0:
        return {"roi": None, "ci95": [None, None], "dates": 0}
    daily = df.groupby("event_date", dropna=False).agg(pnl=("pnl", "sum"), cost=("cost", "sum"))
    daily = daily[daily["cost"] > 0]
    if daily.empty:
        return {"roi": None, "ci95": [None, None], "dates": 0}
    vals = daily.to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        idx = rng.integers(0, len(vals), len(vals))
        sample = vals[idx]
        boots[i] = float(sample[:, 0].sum() / sample[:, 1].sum())
    return {
        "roi": float(daily["pnl"].sum() / daily["cost"].sum()),
        "ci95": [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))],
        "dates": int(len(daily)),
    }


def summarize_quality(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "event_dates": 0,
            "cities": 0,
            "model_mode_hit": None,
            "adjacent3_hit": None,
            "tail_miss": None,
            "mean_mode_final_distance": None,
            "adjacent3_bootstrap": {"mean": None, "ci95": [None, None], "dates": 0},
        }
    return {
        "rows": int(len(df)),
        "event_dates": int(df["event_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "model_mode_hit": float(df["final_in_model_mode"].mean()),
        "adjacent3_hit": float(df["final_in_model_adjacent3"].mean()),
        "tail_miss": float(df["tail_miss"].mean()),
        "mean_mode_final_distance": float(df["mode_final_distance"].mean()),
        "adjacent3_bootstrap": bootstrap_mean_by_date(df, "final_in_model_adjacent3"),
        "tail_miss_bootstrap": bootstrap_mean_by_date(df, "tail_miss"),
    }


def label_summary(rows: pd.DataFrame, labels: list[str], train_dates: set[str], holdout_dates: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for label in labels:
        selected = rows[rows[label] == 1]
        train = selected[selected["event_date"].astype(str).isin(train_dates)]
        holdout = selected[selected["event_date"].astype(str).isin(holdout_dates)]
        top_dates = []
        if not selected.empty:
            top_dates = (
                selected.groupby("event_date").size().sort_values(ascending=False).head(5).rename("rows").reset_index().to_dict("records")
            )
        out.append(
            {
                "label": label,
                "all": summarize_quality(selected),
                "train": summarize_quality(train),
                "holdout": summarize_quality(holdout),
                "top_event_dates_by_rows": top_dates,
            }
        )
    return out


def centered_indices(mode_i: int, n: int, width: int) -> list[int] | None:
    if width == 3:
        if n < 3:
            return None
        if mode_i == 0:
            return [0, 1, 2]
        if mode_i == n - 1:
            return [n - 3, n - 2, n - 1]
        return [mode_i - 1, mode_i, mode_i + 1]
    if width == 2:
        if n < 2:
            return None
        if mode_i == 0:
            return [0, 1]
        if mode_i == n - 1:
            return [n - 2, n - 1]
        return [mode_i - 1, mode_i]
    return None


def strategy_rows(decision_sets: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    label_cols = [
        "forecast_quality_high",
        "forecast_quality_medium",
        "forecast_quality_medium_plus",
        "forecast_quality_low",
        "city_model_reliable",
        "city_model_unreliable",
        "model_market_disagreement_high",
        "market_lag_candidate",
        "high_uncertainty",
        "sharp_model_confident",
        "tail_risk_high",
    ]
    for _, ds in decision_sets.iterrows():
        legs = json.loads(ds["legs_json"])
        base = {
            "decision_set_id": ds["decision_set_id"],
            "city": ds["city"],
            "event_date": str(ds["event_date"]),
            "forecast_source": ds["forecast_source"],
            "model_version": ds["model_version"],
            "decision_snapshot_ts_utc": ds["decision_snapshot_ts_utc"],
            **{c: int(ds[c]) for c in label_cols},
        }
        idxs = centered_indices(int(ds["mode_i"]), len(legs), 3)
        if idxs is not None:
            selected = [legs[i] for i in idxs]
            cost = float(sum(x["market_yes_price"] for x in selected))
            payout = 1.0 if int(ds["final_i"]) in idxs else 0.0
            if 0 < cost <= 0.85:
                rows.append(
                    {
                        **base,
                        "algorithm": "adjacent3_yes_cost085",
                        "n_legs": 3,
                        "cost": cost,
                        "payout": payout,
                        "pnl": payout - cost,
                    }
                )
        best_no: dict[str, Any] | None = None
        best_abs: list[dict[str, Any]] = []
        for leg in legs:
            model_p = float(leg["model_p_yes"])
            market_p = float(leg["market_yes_price"])
            final_yes = float(leg["final_yes"])
            yes_cost = market_p
            no_cost = 1.0 - market_p
            yes_edge = model_p - market_p
            no_edge = market_p - model_p
            if 0.40 <= no_cost <= 0.75 and no_edge >= 0.10:
                cand = {
                    **base,
                    "algorithm": "single_leg_buy_no_cost40_75_edge010_top1",
                    "n_legs": 1,
                    "cost": no_cost,
                    "payout": 1.0 - final_yes,
                    "pnl": (1.0 - final_yes) - no_cost,
                    "edge": no_edge,
                }
                if best_no is None or cand["edge"] > best_no["edge"]:
                    best_no = cand
            if 0.25 <= yes_cost <= 0.75 and abs(yes_edge) >= 0.08:
                if yes_edge >= 0:
                    best_abs.append(
                        {
                            **base,
                            "algorithm": "side_band_best_leg_mid_cost_e008_top4",
                            "n_legs": 1,
                            "cost": yes_cost,
                            "payout": final_yes,
                            "pnl": final_yes - yes_cost,
                            "edge": yes_edge,
                        }
                    )
                elif 0.25 <= no_cost <= 0.75:
                    best_abs.append(
                        {
                            **base,
                            "algorithm": "side_band_best_leg_mid_cost_e008_top4",
                            "n_legs": 1,
                            "cost": no_cost,
                            "payout": 1.0 - final_yes,
                            "pnl": (1.0 - final_yes) - no_cost,
                            "edge": -yes_edge,
                        }
                    )
        if best_no is not None:
            rows.append(best_no)
        for cand in sorted(best_abs, key=lambda x: x["edge"], reverse=True)[:4]:
            rows.append(cand)
    out = pd.DataFrame(rows)
    if not out.empty:
        out["roi_unit"] = out["pnl"] / out["cost"]
    return out


def top5_removed_roi(df: pd.DataFrame) -> float | None:
    if df.empty or float(df["cost"].sum()) <= 0:
        return None
    daily = df.groupby("event_date", dropna=False).agg(pnl=("pnl", "sum"), cost=("cost", "sum"))
    if len(daily) <= 5:
        return None
    keep = daily.drop(daily.sort_values("pnl", ascending=False).head(5).index)
    if keep.empty or float(keep["cost"].sum()) <= 0:
        return None
    return float(keep["pnl"].sum() / keep["cost"].sum())


def summarize_strategy(df: pd.DataFrame) -> dict[str, Any]:
    boot = bootstrap_roi(df)
    return {
        "rows": int(len(df)),
        "event_dates": int(df["event_date"].nunique()) if not df.empty else 0,
        "cities": int(df["city"].nunique()) if not df.empty else 0,
        "cost": float(df["cost"].sum()) if not df.empty else 0.0,
        "pnl": float(df["pnl"].sum()) if not df.empty else 0.0,
        "roi": boot["roi"],
        "roi_ci95": boot["ci95"],
        "top5_removed_roi": top5_removed_roi(df),
    }


def overlay_results(strats: pd.DataFrame, train_dates: set[str], holdout_dates: set[str]) -> list[dict[str, Any]]:
    filters = [
        ("no_quality_filter", None),
        ("forecast_quality_high", "forecast_quality_high"),
        ("forecast_quality_medium_plus", "forecast_quality_medium_plus"),
        ("exclude_forecast_quality_low", "forecast_quality_low"),
        ("city_model_reliable", "city_model_reliable"),
        ("model_market_disagreement_high", "model_market_disagreement_high"),
        ("market_lag_candidate", "market_lag_candidate"),
    ]
    out: list[dict[str, Any]] = []
    if strats.empty:
        return out
    for algorithm, family in strats.groupby("algorithm", dropna=False):
        base_train = family[family["event_date"].astype(str).isin(train_dates)]
        base_holdout = family[family["event_date"].astype(str).isin(holdout_dates)]
        base_train_sum = summarize_strategy(base_train)
        base_holdout_sum = summarize_strategy(base_holdout)
        for filter_name, col in filters:
            if col is None:
                selected = family
            elif filter_name == "exclude_forecast_quality_low":
                selected = family[family[col] == 0]
            else:
                selected = family[family[col] == 1]
            train = selected[selected["event_date"].astype(str).isin(train_dates)]
            holdout = selected[selected["event_date"].astype(str).isin(holdout_dates)]
            tr = summarize_strategy(train)
            ho = summarize_strategy(holdout)
            out.append(
                {
                    "algorithm": str(algorithm),
                    "filter": filter_name,
                    "train": tr,
                    "holdout": ho,
                    "train_excess_roi_vs_family": None
                    if tr["roi"] is None or base_train_sum["roi"] is None
                    else tr["roi"] - base_train_sum["roi"],
                    "holdout_excess_roi_vs_family": None
                    if ho["roi"] is None or base_holdout_sum["roi"] is None
                    else ho["roi"] - base_holdout_sum["roi"],
                }
            )
    return out


def table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def render_self_check(self_check: dict[str, Any]) -> str:
    return json.dumps(self_check, ensure_ascii=False, indent=2)


def render_md(payload: dict[str, Any]) -> str:
    label_rows = []
    for item in payload["label_summary"]:
        label_rows.append(
            {
                "label": item["label"],
                "all_rows": item["all"]["rows"],
                "all_dates": item["all"]["event_dates"],
                "train_adj3": pct(item["train"]["adjacent3_hit"]),
                "holdout_rows": item["holdout"]["rows"],
                "holdout_dates": item["holdout"]["event_dates"],
                "holdout_adj3": pct(item["holdout"]["adjacent3_hit"]),
                "holdout_tail_miss": pct(item["holdout"]["tail_miss"]),
                "holdout_distance": num(item["holdout"]["mean_mode_final_distance"], 2),
            }
        )
    overlay_rows = []
    for item in payload["overlay_results"]:
        if item["filter"] not in {
            "no_quality_filter",
            "forecast_quality_medium_plus",
            "exclude_forecast_quality_low",
            "city_model_reliable",
        }:
            continue
        overlay_rows.append(
            {
                "algorithm": item["algorithm"],
                "filter": item["filter"],
                "train_rows": item["train"]["rows"],
                "train_roi": pct(item["train"]["roi"]),
                "holdout_rows": item["holdout"]["rows"],
                "holdout_dates": item["holdout"]["event_dates"],
                "holdout_roi": pct(item["holdout"]["roi"]),
                "holdout_excess": pct(item["holdout_excess_roi_vs_family"]),
                "top5_removed": pct(item["holdout"]["top5_removed_roi"]),
            }
        )
    quality = payload["quality_conclusion"]
    lines = [
        "# Forecast Quality / Reliability Base v0",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> DB: `{payload['db_path']}`",
        "> Scope: reusable forecast-quality base research only; no N100/live config changed; no live action.",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db.fact_signal_candidates` for opportunity/reliability labels; `fact_trades` only for mandatory self-check.",
        f"- DB last_modified: `{payload['db_last_modified_utc']}`.",
        f"- fact_signal_candidates rows: `{payload['self_check']['candidate_coverage']['rows']}`.",
        f"- decision_sets used: `{payload['funnel']['decision_sets']}` settled city/event/model/snapshot distributions.",
        f"- strategy overlay rows: `{payload['funnel']['strategy_rows']}` across `{payload['funnel']['strategy_algorithms']}` algorithms.",
        f"- train: `{payload['split']['train_start']}` -> `{payload['split']['train_end']}` ({payload['split']['train_dates']} event_dates).",
        f"- holdout: `{payload['split']['holdout_start']}` -> `{payload['split']['holdout_end']}` ({payload['split']['holdout_dates']} event_dates).",
        "- 本报告不发布 `live_real` PnL/ROI/rank/curve，因此不使用 CLOB coverage gate 作为结论来源。",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        render_self_check(payload["self_check"]),
        "```",
        "",
        "## Target Metric",
        "",
        "`forecast_quality_reliability_base_v0` = at city + event_date + model/source + decision checkpoint grain, classify whether the model distribution was historically reliable before applying any strategy-private thresholds.",
        "",
        "The denominator is settled decision-set distributions built from `BUY_YES + BUY_NO` union rows. Settlement fields are labels only; no raw CSV, legacy DB, or old replay source is used.",
        "",
        "## Reusable Labels",
        "",
        table(
            label_rows,
            [
                "label",
                "all_rows",
                "all_dates",
                "train_adj3",
                "holdout_rows",
                "holdout_dates",
                "holdout_adj3",
                "holdout_tail_miss",
                "holdout_distance",
            ],
        ),
        "",
        "## Cross-Family Overlay",
        "",
        "Rows below are decision-price proxy checks. They answer whether the same base labels help multiple strategy families; they are not executable/live conclusions.",
        "",
        table(
            overlay_rows,
            [
                "algorithm",
                "filter",
                "train_rows",
                "train_roi",
                "holdout_rows",
                "holdout_dates",
                "holdout_roi",
                "holdout_excess",
                "top5_removed",
            ],
        ),
        "",
        "## What Looks Reusable",
        "",
        f"- `forecast_quality_low` is the broad weak-quality bucket so far: holdout adjacent3 hit `{quality['forecast_quality_low_holdout_adj3']}` and tail miss `{quality['forecast_quality_low_holdout_tail_miss']}` are worse than the medium/high buckets, but this is still a soft diagnostic tag rather than a hard no-trade rule.",
        f"- `forecast_quality_medium_plus` is a better soft allow tag than `forecast_quality_high`: high is interpretable but narrow; medium_plus keeps `{quality['medium_plus_holdout_rows']}` holdout decision sets.",
        f"- `city_model_reliable` remains promising as a soft overlay, but it is still sample-thin and city/model-history dependent.",
        "- `market_lag_candidate` and `model_market_disagreement_high` are diagnostic tags, not green lights. They should be logged in shadow and tested as interaction terms, not used alone.",
        "",
        "## Reuse Contract",
        "",
        "Future strategy research should consume this as a shared reliability layer, not as a private filter copied into one strategy file.",
        "",
        "- Source grain: build labels from `fact_signal_candidates` at `city + event_date + forecast_source/model_version + decision_snapshot_ts_utc` decision-set grain. Use `fact_trades` only for mandatory self-checks or realized-fill analysis.",
        "- Threshold rule: derive thresholds on the train window for each rerun. Do not hard-code the 2026-06-13 quantiles as live config.",
        "- Baseline rule: every consumer must compare against its own no-quality-filter family baseline before claiming benefit.",
        "- Execution rule: any executable claim must reprice selected legs using orderbook snapshots with `snapshot_ts_utc <= decision_snapshot_ts_utc`; this v0 report only gives decision-price proxy overlays.",
        "- Verdict rule: labels can support research/shadow segmentation now. They cannot change N100 live behavior unless a later consumer passes significance, baseline, and forward gates.",
        "",
        "Consumer guidance:",
        "",
        "- Range RV / adjacent3: test `forecast_quality_medium_plus` and `exclude_forecast_quality_low` as soft slices around the same range expression. Report no-filter, medium-plus, exclude-low, and city-model-reliable side by side.",
        "- Side-band: use `city_model_reliable`, `forecast_quality_medium_plus`, `model_market_disagreement_high`, and `tail_risk_high` as interaction tags. Keep side-band's same-price or same-family baseline; do not let the reliability tag become the strategy definition.",
        "- BUY_NO single-leg: use `forecast_quality_medium_plus` and `city_model_reliable` as candidate ranking/risk tags, then still enforce price, edge, orderbook, and top-date stress gates.",
        "- Basket / portfolio: aggregate labels to city-day/model level as risk and sizing inputs. Treat `forecast_quality_low` as weak-quality exposure, not an automatic no-trade ban.",
        "- Shadow journals: log all labels next to would-trade rows so later settlement can answer whether the label was broadly useful or only helped one family.",
        "",
        "## Next Research Directions",
        "",
        "1. Materialize `forecast_run_ts_utc`, forecast issuance/checkpoint age, and forecast-source run id into `fact_signal_candidates` so reliability can distinguish stale forecasts from fresh ones.",
        "2. Materialize same-checkpoint ECMWF/GFS paired distribution features, including mode distance, L1 distribution gap, and entropy gap, instead of approximating by city/event/snapshot grouping.",
        "3. Promote the base label builder into a reusable artifact, ideally a fact-table sidecar or generated parquet/JSON, so Range RV, adjacent3, side-band, BUY_NO, and basket scripts consume the same labels.",
        "4. Re-run after more settled forward dates, then evaluate by active event_date, city concentration, top5 removed, and event-date cluster bootstrap before any shadow-to-paper promotion.",
        "5. Add time-aligned orderbook overlays for the strongest consumers, especially BUY_NO single-leg and side-band. Proxy improvements without orderbook survival should stay research-only.",
        "6. Test basket-level use separately: the label may be more valuable for exposure control and sizing than for single-leg selection.",
        "",
        "## Three-Gate Verdict",
        "",
        "| gate | status | reason |",
        "| --- | --- | --- |",
        "| significance | FAIL | Base calibration tags have direction, but cross-family overlay excess ROI still has thin holdout support and unstable top-date stress. |",
        "| baseline | FAIL | The same label does not yet beat each family baseline across adjacent3, BUY_NO single-leg, and side-band proxy in a durable way. |",
        "| forward | FAIL | Holdout exists, but many useful overlays are only a few dates or collapse under top5 removed. |",
        "",
        "`significance=FAIL`, `baseline=FAIL`, `forward=FAIL`, `conclusion=inconclusive` for live action.",
        "",
        "## Plain-English Conclusion",
        "",
        "Forecast quality has real value as a shared reliability layer, but v0 is not a confirmed live edge. The useful product of this pass is the reusable label set and a common denominator for later strategy families, not a new trading rule.",
        "",
        "Best next step: materialize forecast run age / forecast issuance timestamp and same-checkpoint ECMWF-GFS paired distributions into `fact_signal_candidates`, then rerun this base as a stable feature table before letting Range RV, adjacent3, side-band, single-leg, and basket consume it.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-path", default=str(DB_PATH))
    ap.add_argument("--out-json", default=str(OUT_JSON))
    ap.add_argument("--out-md", default=str(OUT_MD))
    args = ap.parse_args()

    db_path = Path(args.db_path)
    conn = connect_ro(db_path)
    try:
        self_check = mandatory_self_check(conn)
        candidates = load_candidates(conn)
    finally:
        conn.close()

    decision_sets = build_decision_sets(candidates)
    train_dates, holdout_dates, split = split_dates(decision_sets)
    decision_sets = add_historical_features(add_cross_model_features(decision_sets))
    decision_sets, thresholds = add_quality_labels(decision_sets, train_dates)
    strats = strategy_rows(decision_sets)
    labels = [
        "forecast_quality_high",
        "forecast_quality_medium",
        "forecast_quality_medium_plus",
        "forecast_quality_low",
        "city_model_reliable",
        "city_model_unreliable",
        "model_market_disagreement_high",
        "market_lag_candidate",
        "high_uncertainty",
        "sharp_model_confident",
        "tail_risk_high",
    ]
    label_stats = label_summary(decision_sets, labels, train_dates, holdout_dates)
    overlay = overlay_results(strats, train_dates, holdout_dates)
    low_holdout = next(x for x in label_stats if x["label"] == "forecast_quality_low")["holdout"]
    mp_holdout = next(x for x in label_stats if x["label"] == "forecast_quality_medium_plus")["holdout"]
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat(),
        "self_check": self_check,
        "split": split,
        "thresholds": thresholds,
        "funnel": {
            "fact_signal_candidates_loaded": int(len(candidates)),
            "settled_candidate_rows_loaded": int(candidates["final_yes"].notna().sum()),
            "decision_sets": int(len(decision_sets)),
            "strategy_rows": int(len(strats)),
            "strategy_algorithms": int(strats["algorithm"].nunique()) if not strats.empty else 0,
        },
        "label_summary": label_stats,
        "overlay_results": overlay,
        "quality_conclusion": {
            "forecast_quality_low_holdout_adj3": pct(low_holdout["adjacent3_hit"]),
            "forecast_quality_low_holdout_tail_miss": pct(low_holdout["tail_miss"]),
            "medium_plus_holdout_rows": int(mp_holdout["rows"]),
        },
        "reuse_contract": {
            "grain": "city + event_date + forecast_source/model_version + decision_snapshot_ts_utc decision-set grain from fact_signal_candidates",
            "thresholds": "derive on train window per rerun; do not hard-code current quantiles into live config",
            "baseline": "compare each consumer against its own no-quality-filter family baseline",
            "execution": "for executable claims, attach orderbook snapshots with snapshot_ts_utc <= decision_snapshot_ts_utc",
            "allowed_current_use": "research/shadow segmentation only",
        },
        "next_research_directions": [
            "materialize forecast_run_ts_utc and checkpoint age",
            "materialize same-checkpoint ECMWF/GFS paired distribution features",
            "promote base labels into a reusable sidecar artifact",
            "rerun after more settled forward dates with event_date cluster bootstrap and top-date stress",
            "add time-aligned orderbook overlays for BUY_NO and side-band consumers",
            "test basket-level exposure and sizing use separately",
        ],
        "limitations": [
            "forecast_run_ts_utc is not materialized in fact_signal_candidates, so lead/checkpoint age is approximated by decision snapshot only.",
            "Same-checkpoint ECMWF/GFS paired distributions are only approximated by city/event/snapshot grouping.",
            "Strategy overlays use decision-price proxy, not time-aligned orderbook execution.",
            "No live_real PnL/ROI/rank/curve is published.",
        ],
        "verdict": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "conclusion": "inconclusive",
            "allowed_action": "research/shadow instrumentation only; no live config change",
        },
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    out_md.write_text(render_md(payload))
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "funnel": payload["funnel"]}, indent=2))


if __name__ == "__main__":
    main()
