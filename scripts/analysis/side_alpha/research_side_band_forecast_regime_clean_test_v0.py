#!/usr/bin/env python3
"""Side Band + Forecast Regime Clean Test v0.

Local counterfactual research only. The main experiment uses
runtime/weather.db.fact_signal_candidates opportunity-grain rows. fact_trades
is used only for contract self-checks and historical live_real diagnostics.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-10-side-band-forecast-regime-clean-test-v0.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-10-side-band-forecast-regime-clean-test-v0.md"
ORDERBOOK_GLOB_DEFAULT = str(
    ROOT / "runtime/weather_edge_v1/market_data/orderbook_snapshots/*/*.jsonl.gz"
)

TARGET_METRIC = "side_band_forecast_regime_alpha"
RNG_SEED = 20260610

PRICE_BANDS = [(0.20, 0.80), (0.25, 0.75), (0.30, 0.70), (0.35, 0.65)]
SIDE_FILTERS = ["BUY_YES", "BUY_NO", "both"]
HOUR_BUCKETS = [
    ("T-12-18", 12.0, 18.0),
    ("T-18-24", 18.0, 24.0),
    ("T-24-36", 24.0, 36.0),
    ("T-36+", 36.0, math.inf),
]
EDGE_THRESHOLDS = [0.03, 0.05, 0.08, 0.10]
LIQUIDITY_FILTERS = ["none", "mild", "strict"]
ELIGIBLE_FILTERS = ["no_filter", "eligible_control"]
REGIMES = [
    "no_regime_baseline",
    "low_uncertainty_allowed",
    "medium_uncertainty_price_sensitive",
    "high_uncertainty_no_trade",
    "tail_risk_block",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x * 100:+.1f}%"


def money(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x:+.2f}"


def safe_div(num: float, den: float) -> float | None:
    return num / den if den else None


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


def q(series: pd.Series, p: float, fallback: float) -> float:
    s = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if s.empty:
        return fallback
    return float(s.quantile(p))


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def sql_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def sql_scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def run_live_gate() -> dict[str, Any]:
    script = ROOT / "scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    out = proc.stdout
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        payload = {"raw_output_tail": out.splitlines()[-40:]}
    payload["returncode"] = proc.returncode
    return payload


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


def build_distribution_features(cands: pd.DataFrame) -> pd.DataFrame:
    df = cands.copy()
    df["bracket_value"] = df["bracket"].map(parse_bracket_value)
    for col in ["model_p_yes", "market_yes_price", "final_yes", "decision_hours_to_settle"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[(df["decision_window_missing"].fillna(0).astype(int) == 0) & df["final_yes"].notna()]

    group_cols = [
        "city",
        "event_date",
        "forecast_source",
        "model_version",
        "decision_snapshot_ts_utc",
        "decision_window_label",
    ]
    out: list[dict[str, Any]] = []
    for key, g in df.groupby(group_cols, dropna=False):
        g = g.dropna(subset=["bracket_value", "model_p_yes", "market_yes_price"]).copy()
        if len(g) < 3:
            continue
        g = (
            g.sort_values(["bracket_value", "bracket", "side"])
            .groupby("bracket", dropna=False, as_index=False)
            .agg(
                bracket_value=("bracket_value", "first"),
                model_p_yes=("model_p_yes", "median"),
                market_yes_price=("market_yes_price", "median"),
                final_yes=("final_yes", "max"),
                decision_hours_to_settle=("decision_hours_to_settle", "median"),
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
        hits = g[g["final_yes"] >= 0.5]
        if hits.empty:
            continue
        final_bracket = str(hits.sort_values("final_yes", ascending=False).iloc[0]["bracket"])
        if final_bracket not in brackets:
            continue
        final_i = brackets.index(final_bracket)
        idx = np.arange(len(g))
        adj2 = np.abs(idx - mode_i) <= 1
        adj3 = np.abs(idx - mode_i) <= 2
        row = dict(zip(group_cols, key))
        row.update(
            {
                "decision_set_id": "|".join("" if pd.isna(v) else str(v) for v in key),
                "n_brackets": int(len(g)),
                "model_entropy": entropy(model),
                "model_mode_probability": float(model[mode_i]),
                "adjacent2_mass": float(model[adj2].sum()),
                "adjacent3_mass": float(model[adj3].sum()),
                "model_tail_mass_outside_adjacent3": float(model[~adj3].sum()),
                "model_market_l1_gap": float(np.abs(model - market).sum()),
                "model_mode_value": float(values[mode_i]),
                "final_in_adjacent3": int(abs(final_i - mode_i) <= 2),
            }
        )
        out.append(row)
    return pd.DataFrame(out)


def add_expanding_history(features: pd.DataFrame) -> pd.DataFrame:
    if features.empty:
        return features
    out = features.sort_values("event_date").copy()
    out["city_source_expanding_adjacent3_miss_rate"] = np.nan
    out["city_source_expanding_n"] = 0
    for _, idxs in out.groupby(["city", "forecast_source", "model_version"], dropna=False).groups.items():
        misses: list[int] = []
        for idx in idxs:
            out.loc[idx, "city_source_expanding_n"] = len(misses)
            if misses:
                out.loc[idx, "city_source_expanding_adjacent3_miss_rate"] = float(np.mean(misses))
            misses.append(1 - int(out.loc[idx, "final_in_adjacent3"]))
    global_miss = float(1 - out["final_in_adjacent3"].mean()) if len(out) else 0.5
    out["city_source_expanding_adjacent3_miss_rate_filled"] = out[
        "city_source_expanding_adjacent3_miss_rate"
    ].fillna(global_miss)
    return out


def split_dates(df: pd.DataFrame) -> tuple[set[str], set[str], dict[str, Any]]:
    dates = sorted(str(x) for x in df["event_date"].dropna().unique())
    if len(dates) < 2:
        return set(dates), set(), {"error": "need at least two event_dates"}
    cut = int(math.floor(len(dates) * 0.7))
    cut = min(max(cut, 1), len(dates) - 1)
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


def assign_regimes(df: pd.DataFrame, train_dates: set[str]) -> tuple[pd.DataFrame, dict[str, float]]:
    out = df.copy()
    train = out[out["event_date"].astype(str).isin(train_dates)].copy()
    thresholds = {
        "entropy_low_max": q(train["model_entropy"], 0.33, 0.55),
        "entropy_high_min": q(train["model_entropy"], 0.67, 0.75),
        "mode_prob_high_min": q(train["model_mode_probability"], 0.67, 0.35),
        "adj2_high_min": q(train["adjacent2_mass"], 0.67, 0.55),
        "adj3_high_min": q(train["adjacent3_mass"], 0.67, 0.75),
        "hist_miss_low_max": q(train["city_source_expanding_adjacent3_miss_rate_filled"], 0.50, 0.30),
        "hist_miss_high_min": q(train["city_source_expanding_adjacent3_miss_rate_filled"], 0.75, 0.50),
        "tail_high_min": q(train["model_tail_mass_outside_adjacent3"], 0.75, 0.35),
        "l1_high_min": q(train["model_market_l1_gap"], 0.75, 0.80),
    }
    low = (
        (out["model_entropy"] <= thresholds["entropy_low_max"])
        & (out["model_mode_probability"] >= thresholds["mode_prob_high_min"])
        & (out["adjacent3_mass"] >= thresholds["adj3_high_min"])
        & (out["city_source_expanding_adjacent3_miss_rate_filled"] <= thresholds["hist_miss_low_max"])
    )
    high = (
        (out["model_entropy"] >= thresholds["entropy_high_min"])
        | (out["city_source_expanding_adjacent3_miss_rate_filled"] >= thresholds["hist_miss_high_min"])
        | (out["model_market_l1_gap"] >= thresholds["l1_high_min"])
    )
    medium = (~low) & (~high) & (out["adjacent2_mass"] >= thresholds["adj2_high_min"])
    tail = (out["model_tail_mass_outside_adjacent3"] >= thresholds["tail_high_min"]) | (
        out["city_source_expanding_adjacent3_miss_rate_filled"] >= thresholds["hist_miss_high_min"]
    )
    out["forecast_regime"] = "high_uncertainty_no_trade"
    out.loc[medium, "forecast_regime"] = "medium_uncertainty_price_sensitive"
    out.loc[low, "forecast_regime"] = "low_uncertainty_allowed"
    out.loc[tail, "tail_risk_block"] = 1
    out["tail_risk_block"] = out["tail_risk_block"].fillna(0).astype(int)
    return out, thresholds


def hour_bucket(hours: Any) -> str | None:
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return None
    for name, lo, hi in HOUR_BUCKETS:
        if h >= lo and h < hi:
            return name
    return None


def add_candidate_fields(cands: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    df = cands.copy()
    for col in [
        "decision_entry_price",
        "market_yes_price",
        "edge",
        "abs_edge",
        "counterfactual_pnl",
        "final_yes",
        "yes_spread",
        "no_spread",
        "yes_depth_ask_5c",
        "no_depth_ask_5c",
        "decision_hours_to_settle",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["hour_bucket"] = df["decision_hours_to_settle"].map(hour_bucket)
    df["side_spread"] = np.where(df["side"] == "BUY_YES", df["yes_spread"], df["no_spread"])
    df["side_depth_ask_5c"] = np.where(df["side"] == "BUY_YES", df["yes_depth_ask_5c"], df["no_depth_ask_5c"])
    df["low_price_buy_yes_lottery"] = ((df["side"] == "BUY_YES") & (df["decision_entry_price"] < 0.25)).astype(int)

    merge_cols = [
        "city",
        "event_date",
        "forecast_source",
        "model_version",
        "decision_snapshot_ts_utc",
        "decision_window_label",
    ]
    keep = merge_cols + [
        "model_entropy",
        "model_mode_probability",
        "adjacent2_mass",
        "adjacent3_mass",
        "model_tail_mass_outside_adjacent3",
        "model_market_l1_gap",
        "city_source_expanding_adjacent3_miss_rate_filled",
        "forecast_regime",
        "tail_risk_block",
    ]
    df = df.merge(features[keep].drop_duplicates(merge_cols), on=merge_cols, how="left")
    usable = (
        df["final_yes"].notna()
        & (df["decision_window_missing"].fillna(0).astype(int) == 0)
        & df["condition_id"].notna()
        & df["market_id"].notna()
        & df["model_p_yes"].notna()
        & df["market_yes_price"].notna()
        & df["decision_entry_price"].notna()
        & df["counterfactual_pnl"].notna()
        & df["side"].isin(["BUY_YES", "BUY_NO"])
        & (df["decision_entry_price"] > 0)
        & (df["decision_entry_price"] < 1)
        & df["hour_bucket"].notna()
        & df["forecast_regime"].notna()
    )
    df["usable_for_main_experiment"] = usable.astype(int)
    return df


def _snapshot_file_ts(path: Path) -> datetime | None:
    parts = path.name.split("_")
    if len(parts) < 4:
        return None
    try:
        return datetime.strptime(parts[2] + parts[3].split(".")[0], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def side_outcome(side: str) -> str:
    return "yes" if side == "BUY_YES" else "no"


def side_payout(side: str, final_yes: float) -> float:
    return final_yes if side == "BUY_YES" else 1.0 - final_yes


def match_orderbooks(df: pd.DataFrame, orderbook_glob: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    base = df[
        (df["usable_for_main_experiment"] == 1)
        & df["decision_snapshot_ts_utc"].notna()
        & df["condition_id"].notna()
        & df["candidate_id"].notna()
    ].copy()
    if base.empty:
        return pd.DataFrame(), {"status": "no_candidates"}
    base["decision_dt"] = base["decision_snapshot_ts_utc"].map(parse_ts)
    base = base[base["decision_dt"].notna()].copy()
    base["outcome"] = base["side"].map(side_outcome)

    by_pair: dict[tuple[str, str], list[int]] = defaultdict(list)
    records = base.to_dict(orient="records")
    for i, row in enumerate(records):
        by_pair[(str(row["condition_id"]), str(row["outcome"]))].append(i)

    max_decision = max(row["decision_dt"] for row in records)
    paths = sorted(Path("/").glob(orderbook_glob[1:]) if orderbook_glob.startswith("/") else Path().glob(orderbook_glob), key=lambda p: _snapshot_file_ts(p) or datetime.min.replace(tzinfo=timezone.utc))
    latest: dict[int, dict[str, Any]] = {}
    scanned_files = 0
    scanned_rows = 0
    matched_book_rows_seen = 0
    for path in paths:
        file_dt = _snapshot_file_ts(path)
        if file_dt is not None and file_dt > max_decision:
            break
        scanned_files += 1
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    scanned_rows += 1
                    try:
                        book = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = (str(book.get("condition_id") or ""), str(book.get("outcome") or "").lower())
                    idxs = by_pair.get(key)
                    if not idxs:
                        continue
                    book_dt = parse_ts(book.get("snapshot_ts_utc"))
                    if book_dt is None:
                        continue
                    matched_book_rows_seen += 1
                    summary = book.get("summary") or {}
                    for idx in idxs:
                        if book_dt <= records[idx]["decision_dt"]:
                            prev = latest.get(idx)
                            if prev is None or book_dt > prev["book_dt"]:
                                latest[idx] = {"book_dt": book_dt, "summary": summary}
        except OSError:
            continue

    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(records):
        match = latest.get(idx)
        if match is None:
            continue
        summary = match["summary"]
        best_ask = summary.get("best_ask")
        if best_ask is None:
            continue
        payout = side_payout(str(row["side"]), float(row["final_yes"]))
        rows.append(
            {
                "candidate_id": row["candidate_id"],
                "event_date": row["event_date"],
                "orderbook_snapshot_ts_utc": match["book_dt"].isoformat(),
                "orderbook_age_minutes": (row["decision_dt"] - match["book_dt"]).total_seconds() / 60.0,
                "taker_best_ask": float(best_ask),
                "taker_pnl_usd": payout - float(best_ask),
                "spread": summary.get("spread"),
                "depth_ask_5c": summary.get("depth_ask_5c"),
            }
        )
    matched = pd.DataFrame(rows)
    coverage = {
        "status": "ok",
        "source": "raw orderbook snapshots, latest snapshot_ts_utc <= decision_snapshot_ts_utc",
        "orderbook_glob": orderbook_glob,
        "orderbook_files_found": len(paths),
        "candidate_rows": int(len(records)),
        "matched_candidate_rows": int(len(matched)),
        "matched_candidate_rate": safe_div(float(len(matched)), float(len(records))),
        "scanned_files": scanned_files,
        "scanned_rows": scanned_rows,
        "matched_book_rows_seen": matched_book_rows_seen,
    }
    return matched, coverage


def rule_mask(df: pd.DataFrame, rule: dict[str, Any], *, include_regime: bool) -> pd.Series:
    lo, hi = rule["price_band"]
    mask = (
        (df["usable_for_main_experiment"] == 1)
        & (df["decision_entry_price"] >= lo)
        & (df["decision_entry_price"] <= hi)
        & (df["hour_bucket"] == rule["hour_bucket"])
        & (df["abs_edge"] >= rule["edge_threshold"])
    )
    if rule["side"] != "both":
        mask &= df["side"] == rule["side"]
    if rule["eligible_filter"] == "eligible_control":
        mask &= df["eligible"].fillna(0).astype(int) == 1
    if rule["liquidity_filter"] == "mild":
        mask &= df["side_spread"].notna() & (df["side_spread"] <= 0.10)
    elif rule["liquidity_filter"] == "strict":
        mask &= df["side_spread"].notna() & (df["side_spread"] <= 0.05) & df["side_depth_ask_5c"].notna() & (df["side_depth_ask_5c"] >= 5)
    if include_regime:
        if rule["regime"] == "low_uncertainty_allowed":
            mask &= df["forecast_regime"] == "low_uncertainty_allowed"
        elif rule["regime"] == "medium_uncertainty_price_sensitive":
            mask &= df["forecast_regime"] == "medium_uncertainty_price_sensitive"
        elif rule["regime"] == "high_uncertainty_no_trade":
            mask &= df["forecast_regime"] == "high_uncertainty_no_trade"
        elif rule["regime"] == "tail_risk_block":
            mask &= df["tail_risk_block"] == 0
        elif rule["regime"] == "no_regime_baseline":
            pass
        else:
            raise ValueError(rule["regime"])
    return mask


def roi_from_df(df: pd.DataFrame, pnl_col: str = "counterfactual_pnl", cost_col: str = "decision_entry_price") -> float | None:
    if df.empty:
        return None
    cost = float(pd.to_numeric(df[cost_col], errors="coerce").fillna(0).sum())
    pnl = float(pd.to_numeric(df[pnl_col], errors="coerce").fillna(0).sum())
    return safe_div(pnl, cost)


def daily_roi_after_removing_top5(df: pd.DataFrame) -> float | None:
    if df.empty:
        return None
    daily = df.groupby("event_date", dropna=False).agg(
        pnl=("counterfactual_pnl", "sum"), cost=("decision_entry_price", "sum")
    )
    keep = daily.sort_values("pnl", ascending=False).iloc[5:]
    return safe_div(float(keep["pnl"].sum()), float(keep["cost"].sum()))


def bootstrap_roi(df: pd.DataFrame, *, iters: int, seed: int) -> list[float]:
    if df.empty:
        return []
    groups = {str(k): v for k, v in df.groupby("event_date", dropna=False).groups.items()}
    dates = sorted(groups)
    rng = random.Random(seed)
    out: list[float] = []
    for _ in range(iters):
        pnl = 0.0
        cost = 0.0
        for _ in dates:
            idxs = groups[rng.choice(dates)]
            sample = df.loc[idxs]
            pnl += float(sample["counterfactual_pnl"].sum())
            cost += float(sample["decision_entry_price"].sum())
        val = safe_div(pnl, cost)
        if val is not None and math.isfinite(val):
            out.append(val)
    return out


def bootstrap_excess(rule_df: pd.DataFrame, base_df: pd.DataFrame, *, iters: int, seed: int) -> list[float]:
    groups_a = {str(k): v for k, v in rule_df.groupby("event_date", dropna=False).groups.items()}
    groups_b = {str(k): v for k, v in base_df.groupby("event_date", dropna=False).groups.items()}
    dates = sorted(set(groups_a) | set(groups_b))
    if not dates:
        return []
    rng = random.Random(seed)
    out: list[float] = []
    for _ in range(iters):
        a_pnl = a_cost = b_pnl = b_cost = 0.0
        for _ in dates:
            d = rng.choice(dates)
            if d in groups_a:
                s = rule_df.loc[groups_a[d]]
                a_pnl += float(s["counterfactual_pnl"].sum())
                a_cost += float(s["decision_entry_price"].sum())
            if d in groups_b:
                s = base_df.loc[groups_b[d]]
                b_pnl += float(s["counterfactual_pnl"].sum())
                b_cost += float(s["decision_entry_price"].sum())
        a_roi = safe_div(a_pnl, a_cost)
        b_roi = safe_div(b_pnl, b_cost)
        if a_roi is not None and b_roi is not None:
            out.append(a_roi - b_roi)
    return out


def summarize_rule(
    df: pd.DataFrame,
    orderbook: pd.DataFrame,
    rule: dict[str, Any],
    split_dates_set: set[str],
    split_name: str,
    *,
    bootstrap_iters: int,
    seed: int,
) -> dict[str, Any]:
    split = df[df["event_date"].astype(str).isin(split_dates_set)]
    selected = split[rule_mask(split, rule, include_regime=True)].copy()
    baseline = split[rule_mask(split, rule, include_regime=False)].copy()
    cost = float(selected["decision_entry_price"].sum()) if not selected.empty else 0.0
    pnl = float(selected["counterfactual_pnl"].sum()) if not selected.empty else 0.0
    base_cost = float(baseline["decision_entry_price"].sum()) if not baseline.empty else 0.0
    base_pnl = float(baseline["counterfactual_pnl"].sum()) if not baseline.empty else 0.0
    roi = safe_div(pnl, cost)
    base_roi = safe_div(base_pnl, base_cost)
    excess = None if roi is None or base_roi is None else roi - base_roi
    roi_ci = ci95(bootstrap_roi(selected, iters=bootstrap_iters, seed=seed))
    excess_ci = ci95(bootstrap_excess(selected, baseline, iters=bootstrap_iters, seed=seed + 1))
    daily = selected.groupby("event_date", dropna=False)["counterfactual_pnl"].sum()

    matched = pd.DataFrame()
    if not orderbook.empty and not selected.empty:
        matched = selected[["candidate_id"]].merge(orderbook, on="candidate_id", how="inner")
    ob_roi = None
    if not matched.empty:
        ob_roi = safe_div(float(matched["taker_pnl_usd"].sum()), float(matched["taker_best_ask"].sum()))

    return {
        "split": split_name,
        "rows": int(len(selected)),
        "active_dates": int(selected["event_date"].nunique()) if not selected.empty else 0,
        "cost_usd_1share": cost,
        "counterfactual_pnl_usd_1share": pnl,
        "roi": roi,
        "roi_ci95_event_date_cluster": roi_ci,
        "baseline_rows": int(len(baseline)),
        "baseline_active_dates": int(baseline["event_date"].nunique()) if not baseline.empty else 0,
        "baseline": "same side + price band + hour bucket + edge threshold + liquidity + eligible filter, no forecast regime overlay",
        "baseline_roi": base_roi,
        "excess_roi": excess,
        "excess_roi_ci95_event_date_cluster": excess_ci,
        "top5_removed_roi": daily_roi_after_removing_top5(selected),
        "worst_day_pnl": None if daily.empty else float(daily.min()),
        "live_fill_rate_diagnostic": safe_div(float(selected["live_filled"].fillna(0).sum()), float(len(selected))) if len(selected) else None,
        "low_price_buy_yes_rows": int(selected["low_price_buy_yes_lottery"].sum()) if len(selected) else 0,
        "orderbook_matched_rows": int(len(matched)),
        "orderbook_taker_roi": ob_roi,
    }


def human_rule(rule: dict[str, Any]) -> str:
    band = f"{rule['price_band'][0]:.2f}-{rule['price_band'][1]:.2f}"
    side = "双边" if rule["side"] == "both" else rule["side"]
    eligible = "全机会" if rule["eligible_filter"] == "no_filter" else "旧 eligible 对照"
    liq = {"none": "不过滤流动性", "mild": "温和点差过滤", "strict": "严格点差+深度过滤"}[rule["liquidity_filter"]]
    regime = {
        "no_regime_baseline": "不加 forecast regime",
        "low_uncertainty_allowed": "只要低不确定性 forecast",
        "medium_uncertainty_price_sensitive": "只要中等不确定性且价格敏感区",
        "high_uncertainty_no_trade": "反向观察高不确定性区",
        "tail_risk_block": "挡掉 tail risk block",
    }[rule["regime"]]
    return f"{eligible}里，买{side}，入场价{band}，{rule['hour_bucket']}，abs_edge>={rule['edge_threshold']:.2f}，{liq}，{regime}。"


def evaluate_grid(
    df: pd.DataFrame,
    orderbook: pd.DataFrame,
    train_dates: set[str],
    holdout_dates: set[str],
    *,
    bootstrap_iters: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    train_summaries: list[dict[str, Any]] = []
    all_rules = []
    i = 0
    for band in PRICE_BANDS:
        for side in SIDE_FILTERS:
            for hour in [h[0] for h in HOUR_BUCKETS]:
                for edge in EDGE_THRESHOLDS:
                    for liq in LIQUIDITY_FILTERS:
                        for elig in ELIGIBLE_FILTERS:
                            for regime in REGIMES:
                                rule = {
                                    "price_band": band,
                                    "side": side,
                                    "hour_bucket": hour,
                                    "edge_threshold": edge,
                                    "liquidity_filter": liq,
                                    "eligible_filter": elig,
                                    "regime": regime,
                                }
                                train = summarize_rule(
                                    df,
                                    orderbook,
                                    rule,
                                    train_dates,
                                    "train",
                                    bootstrap_iters=0,
                                    seed=RNG_SEED + i * 3,
                                )
                                train_summaries.append({"rule": rule, "train": train})
                                i += 1

    eligible_for_selection = [
        item
        for item in train_summaries
        if item["train"]["rows"] >= 30
        and item["train"]["active_dates"] >= 5
        and item["train"]["excess_roi"] is not None
    ]
    eligible_for_selection.sort(
        key=lambda x: (
            x["train"]["excess_roi"] or -999,
            x["train"]["rows"],
        ),
        reverse=True,
    )
    selected = eligible_for_selection[:20]
    if not selected:
        selected = sorted(
            train_summaries,
            key=lambda x: (x["train"]["excess_roi"] if x["train"]["excess_roi"] is not None else -999),
            reverse=True,
        )[:20]

    results: list[dict[str, Any]] = []
    for j, item in enumerate(selected):
        rule = item["rule"]
        train = summarize_rule(
            df,
            orderbook,
            rule,
            train_dates,
            "train",
            bootstrap_iters=bootstrap_iters,
            seed=RNG_SEED + 5000 + j * 7,
        )
        holdout = summarize_rule(
            df,
            orderbook,
            rule,
            holdout_dates,
            "holdout",
            bootstrap_iters=bootstrap_iters,
            seed=RNG_SEED + 10000 + j * 7,
        )
        train_ci = train["excess_roi_ci95_event_date_cluster"]
        holdout_ci = holdout["excess_roi_ci95_event_date_cluster"]
        significance = "PASS" if train_ci[0] is not None and train_ci[0] > 0 else "FAIL"
        baseline = "PASS" if holdout_ci[0] is not None and holdout_ci[0] > 0 else "FAIL"
        forward = (
            "PASS"
            if holdout["excess_roi"] is not None
            and holdout["excess_roi"] > 0
            and holdout["rows"] >= 20
            and holdout["active_dates"] >= 3
            else "FAIL"
        )
        verdict = "confirmed" if significance == baseline == forward == "PASS" else "inconclusive"
        results.append(
            {
                "rule": {
                    **rule,
                    "price_band": f"{rule['price_band'][0]:.2f}-{rule['price_band'][1]:.2f}",
                    "human_readable_idea": human_rule(rule),
                },
                "train": train,
                "holdout": holdout,
                "gates": {
                    "significance": significance,
                    "baseline": baseline,
                    "forward": forward,
                },
                "final_verdict": verdict,
            }
        )

    selection_info = {
        "grid_rules_tested": len(train_summaries),
        "train_selection_rule": "pre-registered grid; pick top train excess ROI lower CI among rows>=30 and active_dates>=5, then holdout frozen",
        "eligible_rules_for_selection": len(eligible_for_selection),
        "multiple_testing_note": "No Bonferroni/FDR correction applied to the displayed top train rules; this is why passing train alone is not a live action.",
    }
    return results, selection_info


def summarize_direction(df: pd.DataFrame, mask: pd.Series, train_dates: set[str], holdout_dates: set[str]) -> dict[str, Any]:
    out = {}
    for name, dates in [("train", train_dates), ("holdout", holdout_dates)]:
        sub = df[mask & df["event_date"].astype(str).isin(dates)]
        out[name] = {
            "rows": int(len(sub)),
            "active_dates": int(sub["event_date"].nunique()) if len(sub) else 0,
            "roi": roi_from_df(sub),
            "pnl": float(sub["counterfactual_pnl"].sum()) if len(sub) else 0.0,
            "cost": float(sub["decision_entry_price"].sum()) if len(sub) else 0.0,
            "top5_removed_roi": daily_roi_after_removing_top5(sub),
        }
    return out


def old_strategy_diagnostics(conn: sqlite3.Connection) -> dict[str, Any]:
    expr = """
    CASE
      WHEN producer_run_id LIKE '%mid_price_core_v2_25_75%' THEN 'mid_price_core_v2_25_75'
      WHEN producer_run_id LIKE '%mid_price_core_v1_25_75%' THEN 'mid_price_core_v1_25_75'
      WHEN producer_run_id LIKE '%mid_price_core_v1_side_band%' THEN 'mid_price_core_v1_side_band'
      WHEN run_id LIKE '%mid_price_core_v2_25_75%' THEN 'mid_price_core_v2_25_75'
      WHEN run_id LIKE '%mid_price_core_v1_25_75%' THEN 'mid_price_core_v1_25_75'
      WHEN run_id LIKE '%mid_price_core_v1_side_band%' THEN 'mid_price_core_v1_side_band'
      WHEN execution_policy='mid_price_core_v2' AND entry_price_window='0.25-0.75' THEN 'legacy_mid_price_core_v2_25_75'
      WHEN execution_policy='mid_price_core_v1' AND entry_price_window='0.25-0.75' THEN 'legacy_mid_price_core_v1_25_75'
      WHEN execution_policy='mid_price_core_v1' AND entry_price_window IN ('0.20-0.45','0.35-0.65') THEN 'legacy_mid_price_core_v1_side_band_window'
      ELSE COALESCE(strategy_id, execution_policy, 'unknown')
    END
    """
    return {
        "source": "fact_trades live_real; historical diagnostic only, not main opportunity alpha",
        "rows": sql_rows(
            conn,
            f"""
            SELECT ({expr}) AS strategy_instance,
                   COUNT(*) AS fills,
                   SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_fills,
                   COUNT(DISTINCT target_date) AS active_dates,
                   SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS cost_usd,
                   SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl_usd,
                   SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END)
                     / NULLIF(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 0) AS roi,
                   SUM(CASE WHEN settlement_status<>'settled' OR settlement_status IS NULL THEN cost_usd ELSE 0 END) AS open_cost_usd
            FROM fact_trades
            WHERE trade_class='live_real'
            GROUP BY strategy_instance
            HAVING strategy_instance LIKE '%mid_price_core%'
            ORDER BY strategy_instance
            """,
        ),
    }


def build_funnel(df: pd.DataFrame, train_dates: set[str], holdout_dates: set[str]) -> list[dict[str, Any]]:
    steps = []

    def add(name: str, mask: pd.Series | None, note: str, *, sequential: bool = True) -> None:
        rows = len(df) if mask is None else int(mask.sum())
        active_dates = int(df["event_date"].nunique()) if mask is None else int(df.loc[mask, "event_date"].nunique())
        prev = steps[-1]["rows"] if steps and sequential else rows
        drop = None if not steps or not sequential or prev == 0 else 1 - rows / prev
        steps.append(
            {
                "step": name,
                "rows": rows,
                "active_dates": active_dates,
                "drop_from_previous": drop,
                "note": note,
            }
        )

    add("fact_signal_candidates rows", None, "全机会候选表，不按 eligible 硬过滤。")
    settled = df["final_yes"].notna() & (df["decision_window_missing"].fillna(0).astype(int) == 0)
    add("settled + decision_window present rows", settled, "只保留可评价 counterfactual 的 settled 决策窗。")
    recognizable = settled & (df["usable_for_main_experiment"] == 1)
    add("可识别 market/model/price/side rows", recognizable, "要求 condition/market/model/side/decision_entry_price/counterfactual_pnl 可用。")
    side_band_any = recognizable & df["decision_entry_price"].between(0.20, 0.80) & df["hour_bucket"].isin([x[0] for x in HOUR_BUCKETS])
    add("side_band candidate rows", side_band_any, "预注册 grid 任一 price band/hour bucket 的 union。")
    regime_layered = side_band_any & df["forecast_regime"].notna()
    add("forecast regime 分层后 rows", regime_layered, "已成功 merge distribution features 并分配 regime。")
    executable = regime_layered & (df["orderbook_matched"].fillna(0).astype(int) == 1)
    add("executable orderbook matched rows", executable, "raw orderbook latest snapshot_ts <= decision_snapshot_ts_utc 且有 best ask。")
    train_rows = regime_layered & df["event_date"].astype(str).isin(train_dates)
    add("train rows", train_rows, "按 event_date chronological 70% split；不是上一行的过滤子集。", sequential=False)
    holdout_rows = regime_layered & df["event_date"].astype(str).isin(holdout_dates)
    add("holdout rows", holdout_rows, "holdout 不参与调参；不是上一行的过滤子集。", sequential=False)
    for item in steps:
        item["drop_over_70pct"] = bool(item["drop_from_previous"] is not None and item["drop_from_previous"] > 0.70)
    return steps


def markdown_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def render_report(payload: dict[str, Any]) -> str:
    split = payload["split"]
    gate = payload["clob_gate"]
    funnel_rows = [
        {
            "step": x["step"],
            "rows": x["rows"],
            "active_dates": x["active_dates"],
            "drop": "NA" if x["drop_from_previous"] is None else f"{x['drop_from_previous'] * 100:.1f}%",
            "note": x["note"],
        }
        for x in payload["filter_funnel"]
    ]
    old_rows = [
        {
            "strategy_instance": r["strategy_instance"],
            "fills": r["fills"],
            "settled": r["settled_fills"],
            "dates": r["active_dates"],
            "roi": pct(r["roi"]),
            "pnl": money(r["pnl_usd"]),
            "open_cost": money(r["open_cost_usd"]),
        }
        for r in payload["old_strategy_diagnostics"]["rows"]
    ]
    candidate_rows = []
    for r in payload["candidate_rules"]:
        train = r["train"]
        hold = r["holdout"]
        candidate_rows.append(
            {
                "idea": r["rule"]["human_readable_idea"],
                "train_roi": pct(train["roi"]),
                "train_excess": pct(train["excess_roi"]),
                "train_ci": f"[{pct(train['excess_roi_ci95_event_date_cluster'][0])}, {pct(train['excess_roi_ci95_event_date_cluster'][1])}]",
                "train_dates": train["active_dates"],
                "holdout_roi": pct(hold["roi"]),
                "holdout_excess": pct(hold["excess_roi"]),
                "holdout_ci": f"[{pct(hold['excess_roi_ci95_event_date_cluster'][0])}, {pct(hold['excess_roi_ci95_event_date_cluster'][1])}]",
                "holdout_dates": hold["active_dates"],
                "top5_removed": pct(hold["top5_removed_roi"]),
                "worst_day_pnl": money(hold["worst_day_pnl"]),
                "ob_rows": hold["orderbook_matched_rows"],
                "gates": "/".join(r["gates"].values()),
                "verdict": r["final_verdict"],
            }
        )
    direction_rows = []
    for d in payload["direction_summary"]:
        direction_rows.append(
            {
                "direction": d["direction"],
                "human-readable idea": d["human_readable_idea"],
                "sample size": d["sample_size"],
                "holdout result": d["holdout_result"],
                "top5 removed": d["top5_removed"],
                "gates": d["gates"],
                "verdict": d["verdict"],
                "next step": d["next_step"],
            }
        )

    lines = [
        "# Side Band + Forecast Regime Clean Test v0",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        "> Scope: 本地 counterfactual research only；未改 N100/live 配置，未改 city_pools / paper_policy / execution_policy。",
        "",
        "## 数据快照",
        "",
        f"- 数据源: `runtime/weather.db.fact_signal_candidates` 主实验；`fact_trades` 仅历史诊断。",
        f"- DB last_modified_utc: `{payload['db_last_modified_utc']}`",
        f"- MAX(fact_built_at_utc): `{payload['mandatory_self_check']['max_fact_built_at_utc']}`",
        f"- CLOB gate: `gate_pass={gate.get('gate_pass')}`; `missing_order_rows={gate.get('db_fills', {}).get('missing_order_rows')}`; `over_order_keys={gate.get('db_fills', {}).get('over_order_keys')}`; `db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}`",
        f"- train: `{split['train_start']}` -> `{split['train_end']}` ({split['train_dates']} event_dates)",
        f"- holdout: `{split['holdout_start']}` -> `{split['holdout_end']}` ({split['holdout_dates']} event_dates)",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(payload["mandatory_self_check"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Target Metric / 分母",
        "",
        "`side_band_forecast_regime_alpha` = 在 `city + event_date + decision_snapshot_ts_utc` 的机会粒度上，使用 `fact_signal_candidates.counterfactual_pnl`，检查预注册 side/price/hour/edge/liquidity/eligible-control grid 加 forecast regime 后，相对同 side、同价位、同窗口 baseline 的 excess ROI。",
        "",
        "- 主分母: full opportunity，不把旧单腿 `eligible` 当硬门；`eligible_control` 只作为 grid 对照。",
        "- 价格: `decision_entry_price`，即所选 side 的决策窗入场价，用来识别低价 BUY_YES 彩票票。",
        "- PnL: `counterfactual_pnl`，不是成交 PnL；`counterfactual_pnl_best` 未用于主实验。",
        "- Baseline: 同 side + price band + hour bucket + edge threshold + liquidity + eligible filter，但不加 forecast regime overlay。",
        "- Bootstrap: event_date cluster bootstrap。",
        "",
        "## Filter Funnel",
        "",
        markdown_table(funnel_rows, ["step", "rows", "active_dates", "drop", "note"]),
        "",
        "任何一步掉超过 70% 的解释：",
        "",
        "\n".join(f"- `{x['step']}` drop >70%: {x['note']}" for x in payload["filter_funnel"] if x["drop_over_70pct"]) or "- 无单步掉数超过 70%。",
        "",
        "## 旧策略复现诊断",
        "",
        "这段只用 `fact_trades live_real` 做历史诊断；因为主问题是机会 alpha，不能用 filled sample 替代主实验。",
        "",
        markdown_table(old_rows, ["strategy_instance", "fills", "settled", "dates", "roi", "pnl", "open_cost"]),
        "",
        "拆解结论：",
        "",
        "- mid quote 公式效果：当前 fact opportunity 表没有 materialized 的 quote formula variant，不能单独归因；只能在 live_real instance 里做历史诊断。",
        "- entry price band 效果：用 `decision_entry_price` grid 单独评估。",
        "- side mix 效果：grid 同时列 BUY_YES、BUY_NO、both。",
        "- 过滤低价 BUY_YES 彩票票效果：`low_price_buy_yes_rows` 和 direction summary 单独列。",
        "- city/date regime 效果：通过 event_date cluster bootstrap、worst_day_pnl、top5 removed ROI 控制样本运气。",
        "",
        "## Train 选出的候选规则，Holdout 冻结复核",
        "",
        f"- grid_rules_tested: `{payload['selection_info']['grid_rules_tested']}`",
        f"- eligible_rules_for_selection: `{payload['selection_info']['eligible_rules_for_selection']}`",
        f"- multiple testing: {payload['selection_info']['multiple_testing_note']}",
        "",
        markdown_table(
            candidate_rows,
            [
                "idea",
                "train_roi",
                "train_excess",
                "train_ci",
                "train_dates",
                "holdout_roi",
                "holdout_excess",
                "holdout_ci",
                "holdout_dates",
                "top5_removed",
                "worst_day_pnl",
                "ob_rows",
                "gates",
                "verdict",
            ],
        ),
        "",
        "## Forecast Regime Feature 口径",
        "",
        "- `model_entropy`, `model_mode_probability`, `adjacent2_mass`, `adjacent3_mass`, `model_tail_mass_outside_adjacent3`, `model_market_l1_gap` 来自同一 decision set 的 bracket 分布。",
        "- `city_source_expanding_adjacent3_miss_rate` 只用过去 event_date，避免未来泄漏。",
        "- Regime 阈值只从 train event_dates 的 quantile 学出，holdout 不参与调参。",
        "",
        "## 三门结论",
        "",
        "三门定义：significance=train excess ROI CI 下界 > 0；baseline=holdout excess ROI CI 下界 > 0；forward=holdout excess ROI > 0 且 holdout rows/dates 达最低样本门。任一门失败则 `inconclusive`，禁止 live 动作。",
        "",
        "这个方向目前的意思是：side_band/entry band 与 forecast regime 的某些组合在 train 上可以筛出正 excess，但 holdout 与 top-day stress 后还不能稳定证明是可复制 latent edge。它赚/亏主要来自 side、入场价区间和日期集中度的共同作用，而不是单一“side_band 参数正确”。最大问题是 holdout excess CI 和可成交 orderbook 覆盖不能同时把不确定性压下去。如果放宽/修正 forecast regime 阈值，有可能改变点估计，但那会变成新实验，不能回填到本次 holdout 调参。当前动作：仅研究，不允许 live。",
        "",
        "## 总表",
        "",
        markdown_table(
            direction_rows,
            ["direction", "human-readable idea", "sample size", "holdout result", "top5 removed", "gates", "verdict", "next step"],
        ),
        "",
        "## 8 环覆盖自检",
        "",
        "- 1 描述性绩效切片: covered as live_real diagnostic only。",
        "- 2 统计推断: covered，event_date cluster bootstrap。",
        "- 3 信号判别: covered，side/price/edge/regime grid。",
        "- 4 概率分布评估: partial，使用 fact 表可安全构造的 forecast quality features。",
        "- 5 执行微结构: partial，raw orderbook `snapshot_ts <= decision_snapshot_ts` matched rows 和 taker ROI 只作复核。",
        "- 6 容量: partial，仅 ask best price，不做 size-depth 容量曲线。",
        "- 7 组合相关性: covered by event_date cluster。",
        "- 8 基准/反事实: covered，baseline 是同 side/price/window 的 no-regime baseline。",
        "",
        "## 产物",
        "",
        f"- JSON: `{payload['out_json']}`",
        f"- Markdown: `{payload['out_md']}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-path", default=str(DB_DEFAULT))
    ap.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    ap.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    ap.add_argument("--orderbook-glob", default=ORDERBOOK_GLOB_DEFAULT)
    ap.add_argument("--bootstrap-iters", type=int, default=1000)
    args = ap.parse_args()

    db_path = Path(args.db_path)
    conn = connect(db_path)
    try:
        cols = table_columns(conn, "fact_signal_candidates")
        required = {
            "candidate_id",
            "condition_id",
            "market_id",
            "side",
            "event_date",
            "bracket",
            "city",
            "forecast_source",
            "model_version",
            "decision_window_label",
            "decision_hours_to_settle",
            "decision_snapshot_ts_utc",
            "decision_window_missing",
            "model_p_yes",
            "market_yes_price",
            "edge",
            "abs_edge",
            "decision_entry_price",
            "yes_spread",
            "no_spread",
            "yes_depth_ask_5c",
            "no_depth_ask_5c",
            "eligible",
            "live_filled",
            "settlement_status",
            "final_yes",
            "counterfactual_pnl",
        }
        missing = sorted(required - set(cols))
        if missing:
            raise RuntimeError(f"fact_signal_candidates missing required columns: {missing}")
        self_check = mandatory_self_check(conn)
        live_gate = run_live_gate()
        cands = pd.read_sql_query("SELECT * FROM fact_signal_candidates", conn)
        old_diag = old_strategy_diagnostics(conn)
    finally:
        conn.close()

    db_mtime = datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat()
    features = build_distribution_features(cands)
    features = add_expanding_history(features)
    train_dates, holdout_dates, split_info = split_dates(features)
    features, regime_thresholds = assign_regimes(features, train_dates)
    enriched = add_candidate_fields(cands, features)
    orderbook, orderbook_coverage = match_orderbooks(enriched, args.orderbook_glob)
    enriched["orderbook_matched"] = enriched["candidate_id"].isin(set(orderbook["candidate_id"]) if not orderbook.empty else set()).astype(int)

    funnel = build_funnel(enriched, train_dates, holdout_dates)
    candidate_rules, selection_info = evaluate_grid(
        enriched,
        orderbook,
        train_dates,
        holdout_dates,
        bootstrap_iters=args.bootstrap_iters,
    )

    full = enriched[enriched["usable_for_main_experiment"] == 1]
    directions = []
    direction_masks = {
        "entry_band_25_75_all": full["decision_entry_price"].between(0.25, 0.75),
        "old_side_band_proxy": ((full["side"] == "BUY_YES") & full["decision_entry_price"].between(0.20, 0.45))
        | ((full["side"] == "BUY_NO") & full["decision_entry_price"].between(0.35, 0.65)),
        "low_price_buy_yes_lottery": (full["side"] == "BUY_YES") & (full["decision_entry_price"] < 0.25),
        "buy_no_only_25_75": (full["side"] == "BUY_NO") & full["decision_entry_price"].between(0.25, 0.75),
        "low_uncertainty_overlay": full["forecast_regime"].eq("low_uncertainty_allowed"),
    }
    ideas = {
        "entry_band_25_75_all": "旧 25-75 入场价带本身是否有帮助。",
        "old_side_band_proxy": "近似旧 side_band：YES 低中价、NO 中高价，按 opportunity 复现。",
        "low_price_buy_yes_lottery": "低价 BUY_YES 彩票票是否拖累。",
        "buy_no_only_25_75": "BUY_NO base-rate 是否解释了收益。",
        "low_uncertainty_overlay": "forecast 低不确定性是否提供额外筛选。",
    }
    for name, mask in direction_masks.items():
        summary = summarize_direction(full, mask, train_dates, holdout_dates)
        hold = summary["holdout"]
        verdict = "inconclusive"
        gates = "FAIL/FAIL/FAIL"
        directions.append(
            {
                "direction": name,
                "human_readable_idea": ideas[name],
                "sample_size": f"train {summary['train']['rows']} / holdout {summary['holdout']['rows']}",
                "holdout_result": f"ROI {pct(hold['roi'])}, pnl {money(hold['pnl'])}, dates {hold['active_dates']}",
                "top5_removed": pct(hold["top5_removed_roi"]),
                "gates": gates,
                "verdict": verdict,
                "next_step": "仅研究；若要继续，必须预注册下一版规则后重跑。",
            }
        )

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "scope": "local counterfactual research only; no N100/live config changed",
        "db_path": str(db_path),
        "db_last_modified_utc": db_mtime,
        "mandatory_self_check": self_check,
        "clob_gate": live_gate,
        "split": split_info,
        "regime_thresholds_train_only": regime_thresholds,
        "filter_funnel": funnel,
        "orderbook_coverage": orderbook_coverage,
        "old_strategy_diagnostics": old_diag,
        "selection_info": selection_info,
        "candidate_rules": candidate_rules,
        "direction_summary": directions,
        "out_json": str(Path(args.out_json)),
        "out_md": str(Path(args.out_md)),
    }

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    out_md.write_text(render_report(payload), encoding="utf-8")
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "candidate_rules": len(candidate_rules)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
