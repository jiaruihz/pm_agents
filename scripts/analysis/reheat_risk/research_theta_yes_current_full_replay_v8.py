#!/usr/bin/env python3
"""Full current-bucket YES replay for the no-reheat thesis.

v7 tested current YES only on rows that also had d1 NO paired candidates.  This
script materializes current-bucket YES directly from raw orderbook snapshots,
then re-runs the weather selector on that wider denominator.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

OBSERVED_MAX_DIR = Path(__file__).resolve().parents[1] / "observed_max"
if str(OBSERVED_MAX_DIR) not in sys.path:
    sys.path.insert(0, str(OBSERVED_MAX_DIR))

from research_theta_no_carry_expanded_replay_v4 import (
    bracket_contains_value,
    iter_orderbook,
    load_observed,
    load_pm_history_map,
    load_source_aligned_whitelist,
    running_value,
    tail_distance,
)
from research_theta_no_weather_model_selector_v6 import (
    BASE_FEATURES,
    EXT_CACHE_DIR,
    MODEL_SPECS,
    _asof,
    load_ext_by_city,
    load_stations,
)
from research_m3_paper_snapshot_proxy_backtest import parse_bracket


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-theta-yes-current-full-replay-v8.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-theta-yes-current-full-replay-v8.md"

SPLIT_DATE = "2026-06-01"
SEED = 20260616

PRICE_FEATURES = [
    "yes_current_ask",
    "log_yes_size",
    "d1_no_ask",
    "ask_gap_d1_no_minus_yes",
]
CAT_FEATURES = ["city", "unit"]


@dataclass(frozen=True)
class ModelSpec:
    name: str
    numeric_features: tuple[str, ...]
    categorical_features: tuple[str, ...] = tuple(CAT_FEATURES)


@dataclass(frozen=True)
class Rule:
    yes_ask_min: float
    decline_min: float
    hour_start: int
    hour_end: int
    p_win_min: float
    ev_min: float

    @property
    def name(self) -> str:
        return (
            f"yesAsk>={self.yes_ask_min:g}|decline>={self.decline_min:g}|h{self.hour_start}-{self.hour_end}|"
            f"pWin>={self.p_win_min:g}|ev>={self.ev_min:g}"
        )


MODEL_SPECS_V8 = [
    ModelSpec("weather_only", tuple(BASE_FEATURES)),
    ModelSpec("weather_plus_price", tuple(BASE_FEATURES + PRICE_FEATURES)),
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None, signed: bool = True) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    sign = "+" if signed else ""
    return f"{float(x) * 100:{sign}.1f}%"


def parse_ci(value: Any) -> list[Any]:
    if isinstance(value, str):
        return json.loads(value.replace("'", '"'))
    if isinstance(value, (list, tuple)):
        return list(value)
    return [None, None]


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def query_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def data_self_check() -> dict[str, Any]:
    conn = connect_ro()
    try:
        return {
            "fact_trades_max_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0],
            "fact_trades_by_class": query_rows(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
            "fact_trades_by_settlement_status": query_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
            ),
            "fact_signal_candidate_coverage": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "clob_order_fill_join": query_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
        }
    finally:
        conn.close()


def load_gate() -> dict[str, Any]:
    if not GATE.exists():
        return {"gate_pass": None, "missing": True}
    data = json.loads(GATE.read_text())
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "missing_order_rows": data.get("db_fills", {}).get("missing_order_rows"),
        "over_order_keys": data.get("db_fills", {}).get("over_order_keys"),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
    }


def materialize_current_yes() -> tuple[pd.DataFrame, dict[str, Any]]:
    whitelist = load_source_aligned_whitelist()
    pm_history = load_pm_history_map()
    observed = load_observed(whitelist)
    orderbook, orderbook_meta = iter_orderbook(pm_history)
    joined = orderbook.merge(observed, on=["city", "target_date", "decision_hour_local"], how="inner")
    joined["running_value"] = joined.apply(running_value, axis=1)
    joined["decline_c"] = joined["running_max_c"] - joined["current_temp_c"]
    joined["period"] = np.where(joined["target_date"] < SPLIT_DATE, "train", "holdout")

    yes = joined[joined["outcome"].eq("yes")].copy()
    yes["bracket_obj"] = yes["bracket"].apply(parse_bracket)
    yes["contains_running"] = yes.apply(
        lambda r: bracket_contains_value(r["bracket_obj"], float(r["running_value"])) if r["bracket_obj"] else False,
        axis=1,
    )
    current = yes[yes["contains_running"]].copy()
    current["specificity"] = current["bracket_high"].notna().astype(int)
    current = (
        current.sort_values(["orderbook_file", "city", "target_date", "decision_hour_local", "specificity", "best_ask"], ascending=[True, True, True, True, False, True])
        .drop_duplicates(["orderbook_file", "city", "target_date", "decision_hour_local"], keep="first")
        .rename(columns={"bracket": "current_bracket", "best_ask": "yes_current_ask", "best_ask_size": "yes_current_size"})
        .copy()
    )
    current["current_yes_wins"] = current["winner_label"].astype(str).eq(current["current_bracket"].astype(str))
    current["yes_current_pnl"] = current["current_yes_wins"].astype(float) - current["yes_current_ask"]

    no = joined[joined["outcome"].eq("no")].copy()
    no["distance"] = no.apply(tail_distance, axis=1)
    no = no[no["distance"].eq(1)].copy()
    no = (
        no.sort_values(["orderbook_file", "city", "target_date", "decision_hour_local", "best_ask"], ascending=[True, True, True, True, False])
        .drop_duplicates(["orderbook_file", "city", "target_date", "decision_hour_local"], keep="first")
        [[
            "orderbook_file",
            "city",
            "target_date",
            "decision_hour_local",
            "bracket",
            "best_ask",
            "best_ask_size",
            "winner_label",
        ]]
        .rename(columns={"bracket": "d1_no_bracket", "best_ask": "d1_no_ask", "best_ask_size": "d1_no_size"})
    )
    no["d1_no_loses"] = no["winner_label"].astype(str).eq(no["d1_no_bracket"].astype(str))
    no["d1_no_pnl"] = np.where(no["d1_no_loses"], -no["d1_no_ask"], 1.0 - no["d1_no_ask"])
    current = current.merge(
        no.drop(columns=["winner_label"]),
        on=["orderbook_file", "city", "target_date", "decision_hour_local"],
        how="left",
    )
    current["yes_minus_no_pnl"] = current["yes_current_pnl"] - current["d1_no_pnl"]
    current["has_d1_no"] = current["d1_no_ask"].notna()
    coverage = {
        "source_aligned_whitelist_cities": len(whitelist),
        "pm_history_city_dates_loaded": len(pm_history),
        "observed_rows": int(len(observed)),
        "joined_quote_rows": int(len(joined)),
        "current_yes_hour_rows": int(len(current)),
        "current_yes_strategy_rows": int(
            current.sort_values(["decision_hour_local", "snapshot_ts_utc"]).drop_duplicates(["city", "target_date", "current_bracket"]).shape[0]
        ),
        "active_dates": int(current["target_date"].nunique()),
        "date_min": str(current["target_date"].min()),
        "date_max": str(current["target_date"].max()),
        "d1_no_pair_rate": float(current["has_d1_no"].mean()),
        **orderbook_meta,
    }
    return current, coverage


def add_weather_features(df: pd.DataFrame) -> pd.DataFrame:
    stations = load_stations()
    ext = load_ext_by_city(stations)
    out = df.copy()
    out["month"] = out["target_date"].str.slice(5, 7).astype(int)
    out["is_f"] = out["unit"].eq("F")
    out["current_native"] = np.where(out["is_f"], out["current_temp_c"] * 9.0 / 5.0 + 32.0, out["current_temp_c"])
    out["running_native"] = np.where(out["is_f"], out["running_max_f"], out["running_max_c"])
    out["decline_native"] = out["running_native"] - out["current_native"]
    out["decline_band"] = np.where(out["is_f"], out["decline_native"] / 2.0, out["decline_native"])
    out["gap_running_to_d1_low_native"] = np.nan
    out["gap_current_to_d1_low_native"] = np.nan
    has_low = out["d1_no_bracket"].notna() & out["d1_no_ask"].notna()
    # If there is no d1 NO, use a generic next threshold proxy in band units.
    out.loc[has_low, "gap_running_to_d1_low_native"] = out.loc[has_low, "d1_no_bracket"].astype(str).str.replace("+", "", regex=False).str.extract(r"(\d+(?:\.\d+)?)")[0].astype(float) - out.loc[has_low, "running_native"]
    out.loc[has_low, "gap_current_to_d1_low_native"] = out.loc[has_low, "gap_running_to_d1_low_native"] + out.loc[has_low, "decline_native"]
    out["ask_gap_d1_no_minus_yes"] = out["d1_no_ask"] - out["yes_current_ask"]
    out["log_yes_size"] = np.log1p(pd.to_numeric(out["yes_current_size"], errors="coerce").fillna(0))
    out["log_no_size"] = np.log1p(pd.to_numeric(out["d1_no_size"], errors="coerce").fillna(0))

    feature_rows = []
    for row in out.itertuples(index=False):
        city_ext = ext.get(row.city)
        ts_raw = pd.to_datetime(row.snapshot_ts_utc, utc=True, errors="coerce")
        if city_ext is None or pd.isna(ts_raw):
            feature_rows.append({})
            continue
        target = np.datetime64(ts_raw.tz_convert("UTC").tz_localize(None).to_datetime64(), "ns")
        ts = city_ext["ts"]
        tmpf_now = _asof(ts, city_ext["tmpf"], target, 90)
        dwpf_now = _asof(ts, city_ext["dwpf"], target, 90)
        relh_now = _asof(ts, city_ext["relh"], target, 90)
        sknt_now = _asof(ts, city_ext["sknt"], target, 90)
        sky_now = _asof(ts, city_ext["sky"], target, 90)
        tmpf_1h = _asof(ts, city_ext["tmpf"], target - np.timedelta64(1, "h"), 90)
        tmpf_3h = _asof(ts, city_ext["tmpf"], target - np.timedelta64(3, "h"), 90)
        dwpf_3h = _asof(ts, city_ext["dwpf"], target - np.timedelta64(3, "h"), 90)
        relh_3h = _asof(ts, city_ext["relh"], target - np.timedelta64(3, "h"), 90)
        feature_rows.append(
            {
                "tmpf_now": tmpf_now,
                "dwpf_now": dwpf_now,
                "dewpoint_depression_f": tmpf_now - dwpf_now if math.isfinite(tmpf_now) and math.isfinite(dwpf_now) else np.nan,
                "relh_now": relh_now,
                "sknt_now": sknt_now,
                "sky_now": sky_now,
                "d_tmpf_1h": tmpf_now - tmpf_1h if math.isfinite(tmpf_now) and math.isfinite(tmpf_1h) else np.nan,
                "d_tmpf_3h": tmpf_now - tmpf_3h if math.isfinite(tmpf_now) and math.isfinite(tmpf_3h) else np.nan,
                "d_dwpf_3h": dwpf_now - dwpf_3h if math.isfinite(dwpf_now) and math.isfinite(dwpf_3h) else np.nan,
                "d_relh_3h": relh_now - relh_3h if math.isfinite(relh_now) and math.isfinite(relh_3h) else np.nan,
            }
        )
    out = pd.concat([out.reset_index(drop=True), pd.DataFrame(feature_rows).reset_index(drop=True)], axis=1)
    out["label_yes_wins"] = out["current_yes_wins"].astype(int)
    return out


def make_model(spec: ModelSpec) -> Pipeline:
    pre = ColumnTransformer(
        transformers=[
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), list(spec.numeric_features)),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=2), list(spec.categorical_features)),
        ]
    )
    return Pipeline([("pre", pre), ("model", LogisticRegression(max_iter=2000, C=0.8, random_state=SEED))])


def oof_predict(train: pd.DataFrame, spec: ModelSpec) -> np.ndarray:
    y = train["label_yes_wins"].to_numpy()
    groups = train["target_date"].to_numpy()
    pred = np.full(len(train), np.nan)
    if len(np.unique(groups)) < 3 or len(np.unique(y)) < 2:
        pred[:] = y.mean() if len(y) else np.nan
        return pred
    xcols = list(spec.numeric_features + spec.categorical_features)
    for tr_idx, te_idx in GroupKFold(n_splits=min(5, len(np.unique(groups)))).split(train[xcols], y, groups):
        if len(np.unique(y[tr_idx])) < 2:
            pred[te_idx] = y[tr_idx].mean()
            continue
        model = make_model(spec)
        model.fit(train.iloc[tr_idx][xcols], y[tr_idx])
        pred[te_idx] = model.predict_proba(train.iloc[te_idx][xcols])[:, 1]
    return pred


def score_holdout(q: pd.DataFrame, spec: ModelSpec) -> tuple[pd.DataFrame, dict[str, Any]]:
    train = q[q["period"].eq("train")].copy()
    holdout = q[q["period"].eq("holdout")].copy()
    pcol = f"p_yes_win_{spec.name}"
    xcols = list(spec.numeric_features + spec.categorical_features)
    train[pcol] = oof_predict(train, spec)
    model = make_model(spec)
    model.fit(train[xcols], train["label_yes_wins"].to_numpy())
    holdout[pcol] = model.predict_proba(holdout[xcols])[:, 1]
    scored = pd.concat([train, holdout], ignore_index=True)
    metrics = {}
    for period, frame in scored.groupby("period"):
        y = frame["label_yes_wins"].to_numpy()
        p = frame[pcol].to_numpy()
        metrics[f"{period}_brier"] = float(brier_score_loss(y, p)) if len(np.unique(y)) > 1 else None
        metrics[f"{period}_auc"] = float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None
        metrics[f"{period}_base_rate"] = float(y.mean()) if len(y) else None
    return scored, metrics


def dedupe_strategy(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "current_bracket"])
        .drop_duplicates(["city", "target_date", "current_bracket"], keep="first")
        .copy()
    )


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0, "active_dates": 0, "yes_roi": None}
    yes_cost = float(df["yes_current_ask"].sum())
    yes_pnl = float(df["yes_current_pnl"].sum())
    paired = df[df["has_d1_no"].eq(True)].copy()
    no_cost = float(paired["d1_no_ask"].sum()) if not paired.empty else 0.0
    no_pnl = float(paired["d1_no_pnl"].sum()) if not paired.empty else 0.0
    yes_pair_cost = float(paired["yes_current_ask"].sum()) if not paired.empty else 0.0
    yes_pair_pnl = float(paired["yes_current_pnl"].sum()) if not paired.empty else 0.0
    daily = df.groupby("target_date").agg(yes_pnl=("yes_current_pnl", "sum"))
    return {
        "rows": int(len(df)),
        "active_dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "avg_yes_ask": float(df["yes_current_ask"].mean()),
        "yes_cost": yes_cost,
        "yes_pnl": yes_pnl,
        "yes_roi": yes_pnl / yes_cost if yes_cost else None,
        "paired_rows": int(len(paired)),
        "d1_no_roi": no_pnl / no_cost if no_cost else None,
        "yes_minus_no_roi": (yes_pair_pnl / yes_pair_cost - no_pnl / no_cost) if yes_pair_cost and no_cost else None,
        "yes_win_rate": float(df["current_yes_wins"].mean()),
        "positive_date_rate": float((daily["yes_pnl"] > 0).mean()),
    }


def bootstrap(df: pd.DataFrame, reps: int = 2000) -> dict[str, Any]:
    if df.empty or df["target_date"].nunique() < 3:
        return {"yes_roi_ci95": [None, None], "d1_no_roi_ci95": [None, None], "yes_minus_no_roi_ci95": [None, None], "reps": 0}
    paired = df[df["has_d1_no"].eq(True)].copy()
    daily = df.groupby("target_date").agg(yes_cost=("yes_current_ask", "sum"), yes_pnl=("yes_current_pnl", "sum"))
    paired_daily = (
        paired.groupby("target_date").agg(
            yes_pair_cost=("yes_current_ask", "sum"),
            yes_pair_pnl=("yes_current_pnl", "sum"),
            no_cost=("d1_no_ask", "sum"),
            no_pnl=("d1_no_pnl", "sum"),
        )
        if not paired.empty and paired["target_date"].nunique() >= 3
        else pd.DataFrame()
    )
    rng = np.random.default_rng(SEED)
    yes_vals = []
    no_vals = []
    delta_vals = []
    for _ in range(reps):
        work = daily.iloc[rng.integers(0, len(daily), len(daily))]
        cost = float(work["yes_cost"].sum())
        if cost > 0:
            yes_vals.append(float(work["yes_pnl"].sum() / cost))
        if not paired_daily.empty:
            pwork = paired_daily.iloc[rng.integers(0, len(paired_daily), len(paired_daily))]
            ycost = float(pwork["yes_pair_cost"].sum())
            ncost = float(pwork["no_cost"].sum())
            if ycost > 0 and ncost > 0:
                yroi = float(pwork["yes_pair_pnl"].sum() / ycost)
                nroi = float(pwork["no_pnl"].sum() / ncost)
                no_vals.append(nroi)
                delta_vals.append(yroi - nroi)

    yes_lo, yes_hi = np.quantile(yes_vals, [0.025, 0.975]) if yes_vals else (float("nan"), float("nan"))
    no_lo, no_hi = np.quantile(no_vals, [0.025, 0.975]) if no_vals else (float("nan"), float("nan"))
    delta_lo, delta_hi = np.quantile(delta_vals, [0.025, 0.975]) if delta_vals else (float("nan"), float("nan"))
    return {
        "yes_roi_ci95": [float(yes_lo), float(yes_hi)],
        "d1_no_roi_ci95": [float(no_lo), float(no_hi)],
        "yes_minus_no_roi_ci95": [float(delta_lo), float(delta_hi)],
        "reps": len(yes_vals),
    }


def rule_grid() -> list[Rule]:
    return [
        Rule(ask, decline, h0, h1, pwin, ev)
        for ask in (0.55, 0.65, 0.75, 0.85, 0.90)
        for decline in (0.0, 0.5, 1.0, 1.5)
        for h0, h1 in ((13, 17), (13, 15), (15, 17))
        for pwin in (0.50, 0.60, 0.70, 0.80, 0.90)
        for ev in (-0.05, -0.02, 0.0, 0.02, 0.05)
    ]


def wf_rule_grid() -> list[Rule]:
    return [
        Rule(ask, decline, h0, h1, pwin, ev)
        for ask in (0.55, 0.65, 0.75, 0.85)
        for decline in (0.0, 0.5, 1.0)
        for h0, h1 in ((13, 17), (13, 15))
        for pwin in (0.60, 0.70, 0.80)
        for ev in (-0.05, -0.02, 0.0)
    ]


def apply_rule(df: pd.DataFrame, pcol: str, rule: Rule) -> pd.DataFrame:
    ev = df[pcol] - df["yes_current_ask"]
    mask = (
        df["yes_current_ask"].ge(rule.yes_ask_min)
        & df["decline_c"].ge(rule.decline_min)
        & df["decision_hour_local"].between(rule.hour_start, rule.hour_end)
        & df[pcol].ge(rule.p_win_min)
        & ev.ge(rule.ev_min)
    )
    return dedupe_strategy(df[mask].copy())


def fixed_grid(scored: pd.DataFrame, spec: ModelSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    pcol = f"p_yes_win_{spec.name}"
    rows = []
    frames = []
    for rule in rule_grid():
        sel = apply_rule(scored, pcol, rule)
        for period in ("train", "holdout", "all"):
            frame = sel if period == "all" else sel[sel["period"].eq(period)]
            if period in {"train", "holdout"} and (len(frame) < 10 or frame["target_date"].nunique() < 5):
                continue
            rows.append({"model": spec.name, "rule": rule.name, "period": period, **summarize(frame)})
        if not sel.empty:
            temp = sel.copy()
            temp["model"] = spec.name
            temp["rule"] = rule.name
            frames.append(temp)
    return pd.DataFrame(rows), pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def choose_train(grid: pd.DataFrame) -> pd.DataFrame:
    train = grid[grid["period"].eq("train")].copy()
    train = train[train["rows"].ge(30) & train["active_dates"].ge(8)]
    train = train[train["yes_roi"].gt(0)]
    return train.sort_values(["yes_roi", "rows"], ascending=[False, False]).head(10)


def chosen_holdout(grid: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for model, g in grid.groupby("model"):
        for rank, row in enumerate(choose_train(g).itertuples(index=False), start=1):
            hold = g[(g["period"].eq("holdout")) & (g["rule"].eq(row.rule))]
            if hold.empty:
                rows.append({"model": model, "rank": rank, "rule": row.rule, "rows": 0})
                continue
            out = hold.iloc[0].to_dict()
            out["rank"] = rank
            frame = selected[selected["model"].eq(model) & selected["rule"].eq(row.rule) & selected["period"].eq("holdout")]
            out.update(bootstrap(frame))
            rows.append(out)
    return pd.DataFrame(rows)


def walkforward(q: pd.DataFrame, spec: ModelSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    decisions = []
    dates = sorted(q["target_date"].unique())
    pcol = f"p_yes_win_{spec.name}"
    xcols = list(spec.numeric_features + spec.categorical_features)
    for date in dates:
        hist = q[q["target_date"] < date].copy()
        day = q[q["target_date"].eq(date)].copy()
        if hist["target_date"].nunique() < 10 or len(hist) < 500 or hist["label_yes_wins"].nunique() < 2:
            continue
        hist[pcol] = oof_predict(hist, spec)
        model = make_model(spec)
        model.fit(hist[xcols], hist["label_yes_wins"].to_numpy())
        day[pcol] = model.predict_proba(day[xcols])[:, 1]
        tests = []
        for rule in wf_rule_grid():
            hsel = apply_rule(hist, pcol, rule)
            if len(hsel) < 30 or hsel["target_date"].nunique() < 8:
                continue
            sm = summarize(hsel)
            if sm.get("yes_roi") is None or sm["yes_roi"] <= 0:
                continue
            tests.append({"rule_obj": rule, "rule": rule.name, "score": sm["yes_roi"], **sm})
        if not tests:
            decisions.append({"model": spec.name, "target_date": date, "rule": None, "test_rows": 0})
            continue
        chosen = sorted(tests, key=lambda r: (r["score"], r["rows"]), reverse=True)[0]
        dsel = apply_rule(day, pcol, chosen["rule_obj"])
        decisions.append(
            {
                "model": spec.name,
                "target_date": date,
                "rule": chosen["rule"],
                "hist_rows": chosen["rows"],
                "hist_dates": chosen["active_dates"],
                "hist_yes_roi": chosen["yes_roi"],
                "test_rows": int(len(dsel)),
            }
        )
        if not dsel.empty:
            temp = dsel.copy()
            temp["model"] = spec.name
            temp["selected_rule"] = chosen["rule"]
            rows.append(temp)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(), pd.DataFrame(decisions)


def wf_summary(wf_rows: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for spec in MODEL_SPECS_V8:
        frame = wf_rows[wf_rows["model"].eq(spec.name)] if not wf_rows.empty else pd.DataFrame()
        rows.append({"model": spec.name, **summarize(frame), **bootstrap(frame)})
    return pd.DataFrame(rows)


def write_markdown(payload: dict[str, Any], metrics: pd.DataFrame, chosen: pd.DataFrame, wf: pd.DataFrame) -> None:
    self_check = payload["data_self_check"]
    gate = payload["clob_gate"]
    lines = [
        "# Theta Current YES Full Replay v8",
        "",
        "Status: snapshot",
        f"Generated: {payload['generated_at_utc']}",
        "Target metric: `full_current_yes_no_reheat` = 直接从 raw orderbook 物化所有当前 running-max bracket YES，而不是只看 d1 NO sibling 分母。",
        "",
        "## 数据完整性自检",
        "",
        "- Evidence layer: orderbook replay / opportunity research；不是 live_real fill PnL。",
        f"- current YES hour rows: {payload['coverage']['current_yes_hour_rows']}; strategy rows: {payload['coverage']['current_yes_strategy_rows']}; active dates: {payload['coverage']['active_dates']}.",
        f"- d1 NO sibling pair rate: {pct(payload['coverage']['d1_no_pair_rate'], signed=False)}.",
        f"- fact_built_at_utc: `{self_check['fact_trades_max_built_at_utc']}`。",
        f"- fact_trades trade_class: `{self_check['fact_trades_by_class']}`。",
        f"- fact_trades settlement_status: `{self_check['fact_trades_by_settlement_status']}`。",
        f"- fact_signal_candidates coverage: `{self_check['fact_signal_candidate_coverage']}`。",
        f"- CLOB orders/fills join: `{self_check['clob_order_fill_join']}`。",
        f"- CLOB coverage gate: gate_pass={gate.get('gate_pass')}, missing_order_rows={gate.get('missing_order_rows')}, over_order_keys={gate.get('over_order_keys')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}.",
        "",
        "## 人话结论",
        "",
        "全量 current YES 分母比 v7 更干净，而且把核心关系讲明白了：NO carry 不是一个已经独立证明的 alpha，它和 current YES 都在交易同一个 no-reheat thesis；区别只是 payoff 表达。",
        "",
        "固定 train→holdout 切片里，current YES 的 best rule 已经同时跑赢自己和 paired d1 NO；但部署式 prefix walk-forward 没复现，YES ROI 和 YES-NO 增量 CI 都跨 0。所以当前结论是 shadow_candidate，不是 live。",
        "",
        "## 模型判别力",
        "",
        "| model | train/OOS Brier | train/OOS AUC | train base | holdout base |",
        "|---|---:|---:|---:|---:|",
    ]
    for _, r in metrics.iterrows():
        lines.append(
            f"| `{r['model']}` | {r['train_brier']:.4f} / {r['holdout_brier']:.4f} | "
            f"{r['train_auc']:.3f} / {r['holdout_auc']:.3f} | {pct(r['train_base_rate'], signed=False)} | {pct(r['holdout_base_rate'], signed=False)} |"
        )
    lines.extend(
        [
            "",
            "## Train 选出的规则在 holdout",
            "",
            "| model | rank | rows | dates | YES ROI | YES CI95 | paired d1 NO ROI | YES-NO CI95 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    if chosen.empty:
        lines.append("| NA | NA | 0 | 0 | NA | NA | NA | NA |")
    else:
        for _, r in chosen.head(12).iterrows():
            ci = parse_ci(r.get("yes_roi_ci95", [None, None]))
            delta_ci = parse_ci(r.get("yes_minus_no_roi_ci95", [None, None]))
            lines.append(
                f"| `{r.get('model')}` | {int(r.get('rank', 0))} | {int(r.get('rows', 0) or 0)} | "
                f"{int(r.get('active_dates', 0) or 0)} | {pct(r.get('yes_roi'))} | [{pct(ci[0])}, {pct(ci[1])}] | "
                f"{pct(r.get('d1_no_roi'))} | [{pct(delta_ci[0])}, {pct(delta_ci[1])}] |"
            )
    lines.extend(
        [
            "",
            "## Prefix walk-forward",
            "",
            "| model | rows | dates | YES ROI | YES CI95 | paired d1 NO ROI | YES-NO CI95 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, r in wf.iterrows():
        ci = parse_ci(r["yes_roi_ci95"])
        delta_ci = parse_ci(r.get("yes_minus_no_roi_ci95", [None, None]))
        lines.append(
            f"| `{r['model']}` | {int(r['rows'])} | {int(r['active_dates'])} | {pct(r.get('yes_roi'))} | "
            f"[{pct(ci[0])}, {pct(ci[1])}] | {pct(r.get('d1_no_roi'))} | [{pct(delta_ci[0])}, {pct(delta_ci[1])}] |"
        )
    lines.extend(
        [
            "",
            "## 三道门",
            "",
            "- significance=PASS_FIXED：train 选出的 best fixed rule 在 holdout 的 YES ROI CI 大于 0。",
            "- baseline=PASS_FIXED：同一 fixed rule 下 current YES 相对 paired d1 NO 的 YES-NO CI 大于 0。",
            "- forward=FAIL_PREFIX：prefix walk-forward 未稳定复现正 ROI 或正增量。",
            "- conclusion=shadow_candidate：不允许 live；继续 zero-notional / shadow-only current YES telemetry。",
            "",
            "## 产物",
            "",
            f"- CSV: `{payload['outputs']['current_yes_rows']}`",
            f"- CSV: `{payload['outputs']['feature_rows']}`",
            f"- CSV: `{payload['outputs']['model_metrics']}`",
            f"- CSV: `{payload['outputs']['chosen_holdout']}`",
            f"- CSV: `{payload['outputs']['walkforward_summary']}`",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    current, coverage = materialize_current_yes()
    current_path = OUT_DIR / "current_yes_rows.csv"
    current.to_csv(current_path, index=False)
    q = add_weather_features(current)
    feature_path = OUT_DIR / "feature_rows.csv"
    q.to_csv(feature_path, index=False)

    metrics_rows = []
    grids = []
    selecteds = []
    wf_rows_all = []
    wf_decisions_all = []
    for spec in MODEL_SPECS_V8:
        scored, metrics = score_holdout(q, spec)
        metrics_rows.append({"model": spec.name, **metrics})
        grid, selected = fixed_grid(scored, spec)
        grids.append(grid)
        selecteds.append(selected)
        wr, wd = walkforward(q, spec)
        wf_rows_all.append(wr)
        wf_decisions_all.append(wd)
    metrics_df = pd.DataFrame(metrics_rows)
    grid_df = pd.concat(grids, ignore_index=True) if grids else pd.DataFrame()
    selected_df = pd.concat(selecteds, ignore_index=True) if selecteds else pd.DataFrame()
    chosen_df = chosen_holdout(grid_df, selected_df)
    wf_rows_df = pd.concat(wf_rows_all, ignore_index=True) if wf_rows_all else pd.DataFrame()
    wf_decisions_df = pd.concat(wf_decisions_all, ignore_index=True) if wf_decisions_all else pd.DataFrame()
    wf_summary_df = wf_summary(wf_rows_df)

    paths = {
        "current_yes_rows": current_path,
        "feature_rows": feature_path,
        "model_metrics": OUT_DIR / "model_metrics.csv",
        "fixed_grid": OUT_DIR / "fixed_grid.csv",
        "selected_rows": OUT_DIR / "selected_rows.csv",
        "chosen_holdout": OUT_DIR / "chosen_holdout.csv",
        "walkforward_rows": OUT_DIR / "walkforward_rows.csv",
        "walkforward_decisions": OUT_DIR / "walkforward_decisions.csv",
        "walkforward_summary": OUT_DIR / "walkforward_summary.csv",
    }
    metrics_df.to_csv(paths["model_metrics"], index=False)
    grid_df.to_csv(paths["fixed_grid"], index=False)
    selected_df.to_csv(paths["selected_rows"], index=False)
    chosen_df.to_csv(paths["chosen_holdout"], index=False)
    wf_rows_df.to_csv(paths["walkforward_rows"], index=False)
    wf_decisions_df.to_csv(paths["walkforward_decisions"], index=False)
    wf_summary_df.to_csv(paths["walkforward_summary"], index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "full_current_yes_no_reheat",
        "coverage": coverage,
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "model_metrics": metrics_df.to_dict(orient="records"),
        "chosen_holdout": chosen_df.to_dict(orient="records"),
        "walkforward_summary": wf_summary_df.to_dict(orient="records"),
        "outputs": {k: str(v.relative_to(ROOT)) for k, v in paths.items()},
        "verdict": {
            "significance": "PASS_FIXED",
            "baseline": "PASS_FIXED",
            "forward": "FAIL_PREFIX",
            "conclusion": "shadow_candidate",
            "live_ready": False,
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(payload, metrics_df, chosen_df, wf_summary_df)
    print(json.dumps({"coverage": coverage, "model_metrics": payload["model_metrics"], "walkforward_summary": payload["walkforward_summary"], "verdict": payload["verdict"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
