#!/usr/bin/env python3
"""Research forecast quality regimes for Range RV.

This script is intentionally opportunity/distribution-grain research. It does
not publish live PnL and does not modify live/N100 configuration.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "runtime/weather.db"
MARKET_DATA_DIR = REPO_ROOT / "runtime/weather_edge_v1/market_data"
OUT_JSON = REPO_ROOT / "docs/analysis/2026-06/2026-06-09-forecast-quality-regime-signal-value.json"
OUT_MD = REPO_ROOT / "docs/analysis/2026-06/2026-06-09-forecast-quality-regime-signal-value.md"


TARGET_METRIC = "forecast_quality_regime_signal_value"
RNG_SEED = 20260609


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x * 100:+.1f}%"


def flt(x: Any) -> float | None:
    if x is None:
        return None
    try:
        y = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(y):
        return None
    return y


def parse_bracket_value(label: Any) -> float | None:
    if label is None:
        return None
    text = str(label).strip()
    nums = re.findall(r"-?\d+(?:\.\d+)?", text)
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


def bootstrap_rate_by_date(
    df: pd.DataFrame,
    value_col: str,
    date_col: str = "event_date",
    n_boot: int = 2000,
    seed: int = RNG_SEED,
) -> dict[str, Any]:
    if df.empty:
        return {"n": 0, "dates": 0, "mean": None, "ci95": [None, None]}
    daily = df.groupby(date_col, dropna=False)[value_col].mean().dropna()
    if daily.empty:
        return {"n": int(len(df)), "dates": 0, "mean": None, "ci95": [None, None]}
    dates = daily.index.to_numpy()
    vals = daily.to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        idx = rng.integers(0, len(vals), len(vals))
        boots[i] = float(np.mean(vals[idx]))
    return {
        "n": int(len(df)),
        "dates": int(len(dates)),
        "mean": float(np.mean(vals)),
        "ci95": [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))],
    }


def auc_score(y_true: pd.Series, score: pd.Series) -> float | None:
    d = pd.DataFrame({"y": y_true, "s": score}).dropna()
    if d.empty or d["y"].nunique() < 2:
        return None
    ranks = d["s"].rank(method="average")
    n_pos = int((d["y"] == 1).sum())
    n_neg = int((d["y"] == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    rank_sum_pos = float(ranks[d["y"] == 1].sum())
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def q(series: pd.Series, p: float, fallback: float) -> float:
    s = series.replace([np.inf, -np.inf], np.nan).dropna()
    if s.empty:
        return fallback
    return float(s.quantile(p))


def load_table(conn: sqlite3.Connection, table: str) -> pd.DataFrame:
    return pd.read_sql_query(f"SELECT * FROM {table}", conn)


def run_sql_scalar(conn: sqlite3.Connection, sql: str) -> Any:
    try:
        return conn.execute(sql).fetchone()[0]
    except Exception as exc:  # report audit errors, do not hide them
        return {"error": repr(exc)}


def run_sql_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        return [{"error": repr(exc)}]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    except Exception:
        return []


def audit_cache() -> dict[str, Any]:
    top = {}
    if not MARKET_DATA_DIR.exists():
        return {"exists": False, "path": str(MARKET_DATA_DIR), "top_level": top}
    for child in sorted(MARKET_DATA_DIR.iterdir()):
        if child.is_dir():
            files = list(child.rglob("*"))
            file_count = sum(1 for p in files if p.is_file())
            sample = next((str(p.relative_to(REPO_ROOT)) for p in files if p.is_file()), None)
            top[child.name] = {"files": file_count, "sample": sample}
        elif child.is_file():
            top[child.name] = {"files": 1, "sample": str(child.relative_to(REPO_ROOT))}
    join_candidates = [
        {
            "source": "fact_signal_candidates / fact_trades",
            "join_key": "condition_id + side + event_date(target_date); distribution grain deduped to city/event_date/source/model/snapshot/bracket",
            "safe_for_features": True,
            "leakage_risk": "low when settlement fields are used only as labels",
            "used": True,
        },
        {
            "source": "orderbook_snapshots/*.jsonl.gz",
            "join_key": "token/condition_id + snapshot_ts_utc <= decision_snapshot_ts_utc",
            "safe_for_features": True,
            "leakage_risk": "medium if latest snapshot is used instead of time-aligned <= decision snapshot",
            "used": False,
        },
        {
            "source": "cache/pm_history/*.json",
            "join_key": "city + event_date + bracket",
            "safe_for_features": False,
            "leakage_risk": "final settlement; labels only",
            "used": False,
        },
        {
            "source": "cache/iem/*.csv and cache/wu_obs/*.csv",
            "join_key": "icao/city + observation timestamp",
            "safe_for_features": False,
            "leakage_risk": "weather observations can occur after decision/event; needs timestamp <= decision_snapshot_ts_utc materialized before use",
            "used": False,
        },
        {
            "source": "raw forecast issue/cache files",
            "join_key": "city + event_date + forecast_source + forecast_run_ts <= decision_snapshot_ts_utc",
            "safe_for_features": False,
            "leakage_risk": "not currently materialized in local fact tables; forecast run age needs pipeline support",
            "used": False,
        },
    ]
    return {"exists": True, "path": str(MARKET_DATA_DIR), "top_level": top, "join_candidates": join_candidates}


def build_distribution_rows(cands: pd.DataFrame) -> pd.DataFrame:
    need = [
        "city",
        "event_date",
        "forecast_source",
        "model_version",
        "decision_hours_to_settle",
        "decision_snapshot_ts_utc",
        "decision_window_label",
        "bracket",
        "side",
        "model_p_yes",
        "market_yes_price",
        "final_yes",
        "settlement_status",
    ]
    missing = [c for c in need if c not in cands.columns]
    if missing:
        raise RuntimeError(f"fact_signal_candidates missing required columns: {missing}")

    df = cands.copy()
    df["bracket_value"] = df["bracket"].map(parse_bracket_value)
    df["model_p_yes"] = pd.to_numeric(df["model_p_yes"], errors="coerce")
    df["market_yes_price"] = pd.to_numeric(df["market_yes_price"], errors="coerce")
    df["final_yes"] = pd.to_numeric(df["final_yes"], errors="coerce")
    if "decision_window_missing" in df.columns:
        df = df[df["decision_window_missing"].fillna(0).astype(int) == 0]
    settled = df[df["final_yes"].notna()].copy()
    if settled.empty:
        return pd.DataFrame()

    # The fact table is opportunity-grain by side, but the forecast distribution
    # is bracket-grain. Do not hard-code a side enum here: some historical rows
    # use BUY_YES/BUY_NO, while other research rows may use YES/NO. We collapse
    # duplicate side rows per bracket below.
    yes = settled.copy()
    group_cols = [
        "city",
        "event_date",
        "forecast_source",
        "model_version",
        "decision_snapshot_ts_utc",
        "decision_window_label",
    ]
    rows = []
    for key, g in yes.groupby(group_cols, dropna=False):
        g = g.dropna(subset=["bracket_value", "model_p_yes", "market_yes_price"]).copy()
        if len(g) < 3:
            continue
        g = (
            g.sort_values(["bracket_value", "bracket", "side"])
            .groupby("bracket", dropna=False, as_index=False)
            .agg(
                {
                    "bracket_value": "first",
                    "model_p_yes": "median",
                    "market_yes_price": "median",
                    "final_yes": "max",
                    "decision_hours_to_settle": "median",
                    "settlement_status": lambda s: next((x for x in s if pd.notna(x)), None),
                }
            )
            .sort_values(["bracket_value", "bracket"])
        )
        model_raw = g["model_p_yes"].clip(lower=0).to_numpy(dtype=float)
        market_raw = g["market_yes_price"].clip(lower=0).to_numpy(dtype=float)
        if model_raw.sum() <= 0 or market_raw.sum() <= 0:
            continue
        model = model_raw / model_raw.sum()
        market = market_raw / market_raw.sum()
        brackets = g["bracket"].astype(str).tolist()
        values = g["bracket_value"].to_numpy(dtype=float)
        mode_i = int(np.argmax(model))
        final_hits = g[g["final_yes"] >= 0.5]
        if final_hits.empty:
            continue
        final_bracket = str(final_hits.sort_values("final_yes", ascending=False).iloc[0]["bracket"])
        if final_bracket not in brackets:
            continue
        final_i = brackets.index(final_bracket)
        idx = np.arange(len(g))
        adj2_mask = np.abs(idx - mode_i) <= 1
        adj3_mask = np.abs(idx - mode_i) <= 2
        outer_n = max(1, math.ceil(len(g) * 0.2))
        outer_mask = (idx < outer_n) | (idx >= len(g) - outer_n)
        row = dict(zip(group_cols, key))
        row.update(
            {
                "decision_set_id": "|".join("" if pd.isna(v) else str(v) for v in key),
                "n_brackets": int(len(g)),
                "decision_hours_to_settle": float(pd.to_numeric(g["decision_hours_to_settle"], errors="coerce").median()),
                "model_entropy": entropy(model),
                "market_entropy": entropy(market),
                "model_mode_probability": float(model[mode_i]),
                "market_mode_probability": float(market[mode_i]),
                "model_mode_bracket": brackets[mode_i],
                "model_mode_value": float(values[mode_i]),
                "final_bracket": final_bracket,
                "final_value": float(values[final_i]),
                "model_adjacent2_mass": float(model[adj2_mask].sum()),
                "model_adjacent3_mass": float(model[adj3_mask].sum()),
                "market_adjacent3_mass": float(market[adj3_mask].sum()),
                "model_tail_mass_outer20": float(model[outer_mask].sum()),
                "market_tail_mass_outer20": float(market[outer_mask].sum()),
                "model_tail_mass_outside_adjacent3": float(model[~adj3_mask].sum()),
                "market_tail_mass_outside_adjacent3": float(market[~adj3_mask].sum()),
                "model_market_entropy_gap": entropy(model) - entropy(market),
                "model_market_l1_gap": float(np.abs(model - market).sum()),
                "distribution_variance": float(np.sum(model * (values - np.sum(model * values)) ** 2)),
                "market_distribution_variance": float(np.sum(market * (values - np.sum(market * values)) ** 2)),
                "final_in_model_mode": int(final_i == mode_i),
                "final_in_model_adjacent2": int(abs(final_i - mode_i) <= 1),
                "final_in_model_adjacent3": int(abs(final_i - mode_i) <= 2),
                "tail_miss": int(abs(final_i - mode_i) > 2),
                "settlement_status": str(g["settlement_status"].dropna().iloc[0]) if g["settlement_status"].notna().any() else None,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def add_cross_model_disagreement(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    out = rows.copy()
    out["ecmwf_gfs_mode_distance"] = np.nan
    out["ecmwf_gfs_entropy_gap_abs"] = np.nan
    key_cols = ["city", "event_date", "decision_snapshot_ts_utc"]
    by_key = out.groupby(key_cols, dropna=False).groups
    for _, idxs in by_key.items():
        sub = out.loc[list(idxs)]
        if len(sub) < 2:
            continue
        for i in sub.index:
            current = out.loc[i]
            other = sub[sub.index != i]
            same_source_other = other[other["forecast_source"] == current["forecast_source"]]
            if same_source_other.empty:
                compare = other
            else:
                compare = same_source_other
            distances = (compare["model_mode_value"] - current["model_mode_value"]).abs()
            entropy_gaps = (compare["model_entropy"] - current["model_entropy"]).abs()
            out.loc[i, "ecmwf_gfs_mode_distance"] = float(distances.mean()) if not distances.empty else np.nan
            out.loc[i, "ecmwf_gfs_entropy_gap_abs"] = float(entropy_gaps.mean()) if not entropy_gaps.empty else np.nan
    return out


def add_historical_calibration(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    out = rows.sort_values("event_date").copy()
    out["city_source_historical_mode_miss_rate"] = np.nan
    out["city_source_historical_adj3_miss_rate"] = np.nan
    out["city_source_historical_n"] = 0
    groups = out.groupby(["city", "forecast_source", "model_version"], dropna=False)
    for _, idxs in groups.groups.items():
        hist_mode = []
        hist_adj3 = []
        for idx in idxs:
            out.loc[idx, "city_source_historical_n"] = len(hist_mode)
            if hist_mode:
                out.loc[idx, "city_source_historical_mode_miss_rate"] = float(np.mean(hist_mode))
                out.loc[idx, "city_source_historical_adj3_miss_rate"] = float(np.mean(hist_adj3))
            hist_mode.append(1 - int(out.loc[idx, "final_in_model_mode"]))
            hist_adj3.append(1 - int(out.loc[idx, "final_in_model_adjacent3"]))
    global_adj3 = float(1 - out["final_in_model_adjacent3"].mean()) if len(out) else np.nan
    out["city_source_historical_adj3_miss_rate_filled"] = out[
        "city_source_historical_adj3_miss_rate"
    ].fillna(global_adj3)
    out["city_source_historical_mode_miss_rate_filled"] = out[
        "city_source_historical_mode_miss_rate"
    ].fillna(float(1 - out["final_in_model_mode"].mean()) if len(out) else np.nan)
    return out


def add_buckets(rows: pd.DataFrame) -> pd.DataFrame:
    out = rows.copy()
    bins = [-np.inf, 22, 24, 26, 28, np.inf]
    labels = ["lt22", "t22_24", "t24_26", "t26_28", "gt28"]
    out["decision_hours_to_settle_bucket"] = pd.cut(
        out["decision_hours_to_settle"], bins=bins, labels=labels, right=False
    ).astype(str)
    return out


def split_train_holdout(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    dates = sorted(str(x) for x in rows["event_date"].dropna().unique())
    if not dates:
        return rows.iloc[0:0], rows.iloc[0:0], {"error": "no event_date"}
    cut = max(1, int(math.floor(len(dates) * 0.7)))
    if cut >= len(dates):
        cut = max(1, len(dates) - 1)
    train_dates = set(dates[:cut])
    holdout_dates = set(dates[cut:])
    info = {
        "method": "event_date chronological 70/30 split",
        "train_start": min(train_dates) if train_dates else None,
        "train_end": max(train_dates) if train_dates else None,
        "holdout_start": min(holdout_dates) if holdout_dates else None,
        "holdout_end": max(holdout_dates) if holdout_dates else None,
        "train_dates": len(train_dates),
        "holdout_dates": len(holdout_dates),
    }
    return rows[rows["event_date"].astype(str).isin(train_dates)].copy(), rows[
        rows["event_date"].astype(str).isin(holdout_dates)
    ].copy(), info


def derive_regimes(train: pd.DataFrame, rows: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = rows.copy()
    thresholds = {
        "entropy_low_max": q(train["model_entropy"], 0.33, 0.5),
        "entropy_high_min": q(train["model_entropy"], 0.67, 0.75),
        "mode_prob_high_min": q(train["model_mode_probability"], 0.67, 0.35),
        "adj2_high_min": q(train["model_adjacent2_mass"], 0.67, 0.55),
        "adj3_high_min": q(train["model_adjacent3_mass"], 0.67, 0.75),
        "hist_adj3_miss_low_max": q(train["city_source_historical_adj3_miss_rate_filled"], 0.50, 0.25),
        "hist_adj3_miss_high_min": q(train["city_source_historical_adj3_miss_rate_filled"], 0.75, 0.45),
        "disagreement_low_max": q(train["ecmwf_gfs_mode_distance"], 0.50, 1.0),
        "disagreement_high_min": q(train["ecmwf_gfs_mode_distance"], 0.75, 2.0),
        "model_tail_low_max": q(train["model_tail_mass_outside_adjacent3"], 0.25, 0.10),
        "market_tail_high_min": q(train["market_tail_mass_outside_adjacent3"], 0.75, 0.30),
    }
    dis = out["ecmwf_gfs_mode_distance"].fillna(thresholds["disagreement_low_max"])
    low = (
        (out["model_entropy"] <= thresholds["entropy_low_max"])
        & (out["model_mode_probability"] >= thresholds["mode_prob_high_min"])
        & (out["model_adjacent3_mass"] >= thresholds["adj3_high_min"])
        & (out["city_source_historical_adj3_miss_rate_filled"] <= thresholds["hist_adj3_miss_low_max"])
        & (dis <= thresholds["disagreement_low_max"])
    )
    high = (
        (out["model_entropy"] >= thresholds["entropy_high_min"])
        | (out["city_source_historical_adj3_miss_rate_filled"] >= thresholds["hist_adj3_miss_high_min"])
        | (dis >= thresholds["disagreement_high_min"])
    )
    medium = (~low) & (~high) & (out["model_adjacent2_mass"] >= thresholds["adj2_high_min"])
    tail_fade = (
        (out["model_tail_mass_outside_adjacent3"] <= thresholds["model_tail_low_max"])
        & (out["market_tail_mass_outside_adjacent3"] >= thresholds["market_tail_high_min"])
        & (~high)
    )
    out["forecast_quality_regime"] = "high_uncertainty_no_trade"
    out.loc[medium, "forecast_quality_regime"] = "medium_uncertainty_adjacent2_only_if_cheap"
    out.loc[low, "forecast_quality_regime"] = "low_uncertainty_adjacent3_allowed"
    out.loc[tail_fade, "tail_overpriced_low_model_tail_risk"] = 1
    out["tail_overpriced_low_model_tail_risk"] = out["tail_overpriced_low_model_tail_risk"].fillna(0).astype(int)
    return out, thresholds


def summarize_by_regime(df: pd.DataFrame, split_name: str) -> list[dict[str, Any]]:
    rows = []
    for regime, g in df.groupby("forecast_quality_regime", dropna=False):
        item = {
            "split": split_name,
            "regime": str(regime),
            "n": int(len(g)),
            "event_dates": int(g["event_date"].nunique()),
            "cities": int(g["city"].nunique()),
        }
        for col in [
            "final_in_model_mode",
            "final_in_model_adjacent2",
            "final_in_model_adjacent3",
            "tail_miss",
        ]:
            item[col] = bootstrap_rate_by_date(g, col)
        rows.append(item)
    tail = df[df["tail_overpriced_low_model_tail_risk"] == 1]
    if not tail.empty:
        item = {
            "split": split_name,
            "regime": "tail_overpriced_low_model_tail_risk",
            "n": int(len(tail)),
            "event_dates": int(tail["event_date"].nunique()),
            "cities": int(tail["city"].nunique()),
        }
        for col in [
            "final_in_model_mode",
            "final_in_model_adjacent2",
            "final_in_model_adjacent3",
            "tail_miss",
        ]:
            item[col] = bootstrap_rate_by_date(tail, col)
        rows.append(item)
    return rows


def feature_signal_value(df: pd.DataFrame, split_name: str) -> list[dict[str, Any]]:
    features = [
        "model_entropy",
        "model_mode_probability",
        "model_adjacent2_mass",
        "model_adjacent3_mass",
        "model_tail_mass_outside_adjacent3",
        "model_market_entropy_gap",
        "model_market_l1_gap",
        "distribution_variance",
        "ecmwf_gfs_mode_distance",
        "city_source_historical_adj3_miss_rate_filled",
    ]
    labels = [
        "final_in_model_mode",
        "final_in_model_adjacent2",
        "final_in_model_adjacent3",
        "tail_miss",
    ]
    out = []
    for feat in features:
        if feat not in df.columns:
            continue
        for label in labels:
            auc = auc_score(df[label], df[feat])
            corr = df[[feat, label]].dropna().corr(method="spearman").iloc[0, 1] if df[[feat, label]].dropna().shape[0] > 2 else np.nan
            out.append(
                {
                    "split": split_name,
                    "feature": feat,
                    "label": label,
                    "auc_feature_high_predicts_label": None if auc is None else float(auc),
                    "spearman": None if not np.isfinite(corr) else float(corr),
                    "n": int(df[[feat, label]].dropna().shape[0]),
                }
            )
    return out


def markdown_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def render_report(payload: dict[str, Any]) -> str:
    audit = payload["data_audit"]
    split = payload["split"]
    coverage = payload["coverage"]
    regime_rows = []
    for r in payload["regime_eval"]:
        regime_rows.append(
            {
                "split": r["split"],
                "regime": r["regime"],
                "n": r["n"],
                "dates": r["event_dates"],
                "mode_hit": pct(r["final_in_model_mode"]["mean"]),
                "adj2_hit": pct(r["final_in_model_adjacent2"]["mean"]),
                "adj3_hit": pct(r["final_in_model_adjacent3"]["mean"]),
                "tail_miss": pct(r["tail_miss"]["mean"]),
            }
        )
    feat_rows = []
    for r in payload["feature_signal_value"]:
        if r["split"] == "holdout" and r["label"] in {"final_in_model_adjacent3", "tail_miss"}:
            feat_rows.append(
                {
                    "feature": r["feature"],
                    "label": r["label"],
                    "auc": "NA" if r["auc_feature_high_predicts_label"] is None else f"{r['auc_feature_high_predicts_label']:.3f}",
                    "spearman": "NA" if r["spearman"] is None else f"{r['spearman']:.3f}",
                    "n": r["n"],
                }
            )
    source_rows = [
        {
            "forecast_source": r.get("forecast_source"),
            "model_version": r.get("model_version"),
            "rows": r.get("rows"),
        }
        for r in coverage["source_model"]
    ]
    city_rows = [
        {"city": r.get("city"), "rows": r.get("rows")}
        for r in coverage["city"][:20]
    ]
    cache_rows = [
        {
            "source": r["source"],
            "join_key": r["join_key"],
            "safe": r["safe_for_features"],
            "used": r["used"],
            "leakage_risk": r["leakage_risk"],
        }
        for r in payload["cache_audit"].get("join_candidates", [])
    ]
    ci_rows = []
    for r in payload["regime_eval"]:
        if r["split"] == "holdout":
            ci_rows.append(
                {
                    "regime": r["regime"],
                    "adj3_mean": pct(r["final_in_model_adjacent3"]["mean"]),
                    "adj3_ci": f"[{pct(r['final_in_model_adjacent3']['ci95'][0])}, {pct(r['final_in_model_adjacent3']['ci95'][1])}]",
                    "tail_miss_mean": pct(r["tail_miss"]["mean"]),
                    "tail_miss_ci": f"[{pct(r['tail_miss']['ci95'][0])}, {pct(r['tail_miss']['ci95'][1])}]",
                }
            )
    lines = [
        "# Forecast Quality Regime Signal Value",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> DB: `{payload['db_path']}`",
        "> Scope: local research only; no N100/live config changed; no live action.",
        "",
        "## 数据快照",
        "",
        f"- 数据源: `runtime/weather.db.fact_signal_candidates` 优先；`fact_trades` 仅用于 contract 自检和 live fill 覆盖背景。",
        f"- DB last_modified: `{audit['db_last_modified_utc']}`",
        f"- fact_signal_candidates rows: `{audit['fact_signal_candidates_rows']}`",
        f"- fact_trades rows: `{audit['fact_trades_rows']}`",
        f"- decision_sets used: `{audit['decision_sets_used']}`",
        f"- unsettled fact_signal_candidates: `{audit['fact_signal_candidates_unsettled_rows']}`",
        f"- missing_bracket fact_signal_candidates: `{audit['fact_signal_candidates_missing_bracket_rows']}`",
        f"- train: `{split['train_start']}` -> `{split['train_end']}` ({split['train_dates']} event_dates)",
        f"- holdout: `{split['holdout_start']}` -> `{split['holdout_end']}` ({split['holdout_dates']} event_dates)",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(payload["mandatory_self_check"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Data Availability / Join Audit",
        "",
        f"- forecast_source/model_version coverage rows: `{len(coverage['source_model'])}`",
        f"- city coverage rows: `{len(coverage['city'])}`",
        f"- possible raw cache joins are audited below and in JSON under `cache_audit.join_candidates`.",
        "",
        "### forecast_source / model_version coverage",
        "",
        markdown_table(source_rows, ["forecast_source", "model_version", "rows"]),
        "",
        "### city coverage top20",
        "",
        markdown_table(city_rows, ["city", "rows"]),
        "",
        "### possible raw cache joins",
        "",
        markdown_table(cache_rows, ["source", "join_key", "safe", "used", "leakage_risk"]),
        "",
        "### 当前 fact 表已有 features",
        "",
        "- `city`, `event_date`, `forecast_source`, `model_version`, `decision_hours_to_settle`, `model_p_yes`, `market_yes_price`, `final_yes`, `settlement_status`.",
        "- 从同一 decision-set 的 bracket distribution 可构造 `model_entropy`, `model_mode_probability`, `adjacent2/adjacent3 mass`, `tail mass`, `model-market entropy gap`, `distribution_variance`。",
        "- 从历史 event_date expanding window 可构造 `city/source historical calibration error`，本报告用过去样本的 adjacent3 miss rate，不用未来 outcome。",
        "",
        "### 需要后续管道补充的 features",
        "",
        "- `forecast run age`: fact 表没有 forecast issuance/run timestamp；需要物化 `forecast_run_ts_utc <= decision_snapshot_ts_utc`。",
        "- `observed trend / weather stability`: 本地 `iem/wu_obs` 是观测 cache，必须按 observation timestamp 截断到 decision 前才可做 feature；当前未安全物化。",
        "- 更严格的 `ECMWF/GFS disagreement`: 本报告只在 fact decision snapshot 内做同 city-day/source-model 分布差异，完整版本应物化同一 forecast issuance/checkpoint 的 paired distribution。",
        "",
        "## Forecast Quality Regime Evaluation",
        "",
        markdown_table(regime_rows, ["split", "regime", "n", "dates", "mode_hit", "adj2_hit", "adj3_hit", "tail_miss"]),
        "",
        "### Holdout cluster bootstrap CI",
        "",
        markdown_table(ci_rows, ["regime", "adj3_mean", "adj3_ci", "tail_miss_mean", "tail_miss_ci"]),
        "",
        "## Feature Signal Value",
        "",
        "Holdout AUC uses feature-high predicts label. For `tail_miss`, higher is worse; for `adjacent3`, higher is better.",
        "",
        markdown_table(feat_rows[:24], ["feature", "label", "auc", "spearman", "n"]),
        "",
        "## 推荐给 Range RV Planner 的 regimes",
        "",
        "- `low_uncertainty_adjacent3_allowed`: model entropy 低、mode probability 高、adjacent3 mass 高、历史 adjacent3 miss 低、跨模型 mode disagreement 低。可作为 Range RV 的 adjacent3 允许前置 regime。",
        "- `medium_uncertainty_adjacent2_only_if_cheap`: 不满足 low，但 adjacent2 mass 仍高且未触发 high uncertainty。只能在 market price 足够便宜时考虑 adjacent2，不作为独立交易信号。",
        "- `high_uncertainty_no_trade`: entropy 高、历史 miss 高或跨模型 disagreement 高。Range RV 应默认拒绝。",
        "- `tail_overpriced_low_model_tail_risk`: model outside-adjacent3 tail mass 低而 market outside-adjacent3 tail mass 高，且未触发 high uncertainty。仅允许 tail fade 研究路径，不是 live action。",
        "",
        "## 8 环覆盖自检",
        "",
        "- 1 描述性绩效切片: NA，本报告不输出 PnL/ROI。",
        "- 2 统计推断: covered，用 event_date cluster bootstrap 评估 hit/miss rate。",
        "- 3 信号判别: covered，评估 quality features 对 mode/adjacent/tail labels 的信号价值。",
        "- 4 概率分布评估: covered，本报告核心。",
        "- 5 执行微结构: partial，仅审计可 join source，不做 executable PnL。",
        "- 6 容量: NA。",
        "- 7 组合相关性: partial，bootstrap cluster 按 event_date。",
        "- 8 基准/反事实: NA，不训练 PnL classifier，不输出策略收益。",
        "",
        "## 结论等级",
        "",
        "`significance=NA`, `baseline=NA`, `forward=NA`, `conclusion=inconclusive` for live action. This is a research regime artifact only.",
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
    conn = sqlite3.connect(db_path)
    try:
        ft_cols = table_columns(conn, "fact_trades")
        fc_cols = table_columns(conn, "fact_signal_candidates")
        mandatory_self_check = {
            "max_fact_built_at_utc": run_sql_scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
            "trade_class_distribution": run_sql_rows(conn, "SELECT trade_class, COUNT(*) AS n FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
            "settlement_status_distribution": run_sql_rows(conn, "SELECT settlement_status, COUNT(*) AS n FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status"),
            "candidate_coverage": run_sql_rows(conn, "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled FROM fact_signal_candidates"),
            "order_fill_coverage": run_sql_rows(conn, "SELECT o.status, COUNT(*) AS orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill FROM orders o LEFT JOIN fills f USING(execution_id) WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status"),
        }
        cands = load_table(conn, "fact_signal_candidates")
        fact_trades_rows = int(run_sql_scalar(conn, "SELECT COUNT(*) FROM fact_trades") or 0)
    finally:
        conn.close()

    dist = build_distribution_rows(cands)
    dist = add_cross_model_disagreement(dist)
    dist = add_historical_calibration(dist)
    dist = add_buckets(dist)
    train, holdout, split_info = split_train_holdout(dist)
    dist, thresholds = derive_regimes(train, dist)
    train = dist[dist["event_date"].astype(str).isin(set(train["event_date"].astype(str)))].copy()
    holdout = dist[dist["event_date"].astype(str).isin(set(holdout["event_date"].astype(str)))].copy()

    regime_eval = summarize_by_regime(train, "train") + summarize_by_regime(holdout, "holdout")
    feature_eval = feature_signal_value(train, "train") + feature_signal_value(holdout, "holdout")

    db_mtime = datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat() if db_path.exists() else None
    source_model = (
        cands.groupby(["forecast_source", "model_version"], dropna=False)
        .size()
        .reset_index(name="rows")
        .sort_values("rows", ascending=False)
        .to_dict(orient="records")
    )
    city_cov = (
        cands.groupby("city", dropna=False)
        .size()
        .reset_index(name="rows")
        .sort_values("rows", ascending=False)
        .to_dict(orient="records")
    )
    status_counts = Counter(str(x) for x in cands.get("settlement_status", pd.Series(dtype=str)).fillna("NULL"))
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "scope": "local research only; no N100/live config changed; no live action",
        "db_path": str(db_path),
        "data_audit": {
            "db_last_modified_utc": db_mtime,
            "fact_signal_candidates_rows": int(len(cands)),
            "fact_trades_rows": fact_trades_rows,
            "decision_sets_used": int(len(dist)),
            "fact_signal_candidates_unsettled_rows": int(status_counts.get("unsettled", 0)),
            "fact_signal_candidates_missing_bracket_rows": int(status_counts.get("missing_bracket", 0)),
            "fact_signal_candidates_columns": fc_cols,
            "fact_trades_columns": ft_cols,
        },
        "mandatory_self_check": mandatory_self_check,
        "coverage": {"source_model": source_model, "city": city_cov[:80]},
        "cache_audit": audit_cache(),
        "split": split_info,
        "thresholds_train_derived": thresholds,
        "regime_eval": regime_eval,
        "feature_signal_value": feature_eval,
        "feature_availability": {
            "fact_available": [
                "city",
                "event_date",
                "forecast_source",
                "model_version",
                "decision_hours_to_settle",
                "model_p_yes",
                "market_yes_price",
                "final_yes",
                "settlement_status",
                "distribution-derived entropy/mode/adjacent/tail/variance",
                "historical calibration error from past event_date only",
            ],
            "needs_pipeline": [
                "forecast run age with forecast_run_ts_utc",
                "timestamp-safe observed trend/weather stability",
                "paired ECMWF/GFS disagreement at identical forecast issuance/checkpoint",
            ],
        },
        "regime_definitions_for_range_rv": {
            "low_uncertainty_adjacent3_allowed": "low model entropy + high mode probability + high adjacent3 mass + low historical adjacent3 miss + low cross-model disagreement",
            "medium_uncertainty_adjacent2_only_if_cheap": "not low or high uncertainty, but adjacent2 model mass remains high; requires cheap market entry in planner",
            "high_uncertainty_no_trade": "high entropy, high historical miss, or high cross-model mode disagreement",
            "tail_overpriced_low_model_tail_risk": "model outside-adjacent3 tail mass low while market outside-adjacent3 tail mass high, excluding high uncertainty",
        },
    }

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(render_report(payload), encoding="utf-8")
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    print(json.dumps({
        "decision_sets": payload["data_audit"]["decision_sets_used"],
        "train_dates": split_info.get("train_dates"),
        "holdout_dates": split_info.get("holdout_dates"),
        "out_json": str(out_json),
        "out_md": str(out_md),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
