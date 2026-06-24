#!/usr/bin/env python3
"""Train and evaluate current-YES future-break hazard v3.

V3 deliberately separates the weather question from the trade question:

1. Weather label: after the current running-max bracket is visible, will a
   later official observation break that current bracket?
2. Trade expression: after pricing and liquidity, does BUY_YES on the current
   bracket have positive executable expectancy?
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/current_yes_future_break_hazard_v3/factory/reheat_feature_rows.csv"
FACTORY_SUMMARY = ROOT / "docs/analysis/2026-06/generated/current_yes_future_break_hazard_v3/factory_summary.json"
BASE_MODEL = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_future_break_hazard_v3"
OUT_SCORED = OUT_DIR / "future_break_hazard_v3_scored_rows.csv"
OUT_METRICS = OUT_DIR / "future_break_hazard_v3_model_metrics.csv"
OUT_RULES = OUT_DIR / "future_break_hazard_v3_rule_comparison.csv"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-23-current-yes-future-break-hazard-v3.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-23-current-yes-future-break-hazard-v3.md"

SEED = 20260623
TRAIN_END = "2026-05-31"
HOLDOUT_START = "2026-06-01"
FORWARD_START = "2026-06-18"
PEAK_DECLINE_MAX_NATIVE = 0.25

BASE_ALIAS = {
    "yes_current_ask": "current_yes_ask",
    "log_yes_size": "log_current_yes_size",
    "decline_c": "decline_from_max_c",
    "relh_now": "relative_humidity_pct",
    "sknt_now": "wind_speed_kt",
    "sky_now": "sky_cover_code",
    "d_tmpf_1h": "temp_trend_1h_f",
    "d_tmpf_3h": "temp_trend_3h_f",
}

WEATHER_NUMERIC = [
    "decision_hour_local",
    "month",
    "current_native",
    "running_native",
    "running_value",
    "decline_native",
    "decline_from_max_c",
    "gap_running_to_d1_low_native",
    "gap_current_to_d1_low_native",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "sky_cover_code",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max_capped",
    "plateau_obs_count_at_high",
    "plateau_duration_min",
    "at_high_stalled_ge2_obs_num",
    "at_high_stalled_ge30m_num",
    "just_touched_high_num",
    "running_max_first_obs_hour_local",
    "running_max_first_obs_before_noon_num",
    "decision_minus_running_max_hour",
    "morning_spike_reheat_window_num",
    "forecast_peak_still_ahead_num",
    "warming_while_at_high_num",
    "decision_obs_age_min",
    "gfs_forecast_peak_delta_hours_local",
    "ecmwf_forecast_peak_delta_hours_local",
    "min_forecast_peak_delta_hours_local",
    "max_forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "gfs_forecast_gap_to_running_native",
    "ecmwf_forecast_gap_to_running_native",
    "min_forecast_gap_to_running_native",
    "max_forecast_gap_to_running_native",
    "forecast_peak_models_agree_le_1h_num",
]
MARKET_NUMERIC = [
    "current_yes_ask",
    "log_current_yes_size",
    "current_yes_spread",
    "d1_no_ask",
    "d1_no_spread",
    "ask_gap_d1_no_minus_yes",
]
CAT_FEATURES = ["city", "unit"]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        out = float(value)
        return None if not math.isfinite(out) else out
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f * 100:+.{digits}f}%"


def num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f:.{digits}f}"


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
            "fact_signal_candidates": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, MIN(event_date) AS min_event_date, MAX(event_date) AS max_event_date, "
                "MIN(decision_snapshot_ts_utc) AS min_decision_ts, MAX(decision_snapshot_ts_utc) AS max_decision_ts "
                "FROM fact_signal_candidates",
            )[0],
            "fact_trades": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, MIN(target_date) AS min_target_date, MAX(target_date) AS max_target_date, "
                "SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) AS missing_bracket_rows, "
                "SUM(CASE WHEN settlement_status IS NULL OR settlement_status <> 'settled' THEN 1 ELSE 0 END) AS unsettled_rows "
                "FROM fact_trades",
            )[0],
            "fact_trades_by_class": query_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY rows DESC",
            ),
            "settlement_outcomes": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, MIN(target_date) AS min_target_date, MAX(target_date) AS max_target_date "
                "FROM settlement_outcomes",
            )[0],
        }
    finally:
        conn.close()


def boolish(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    out = numeric.fillna(0).astype(float) > 0
    text_mask = numeric.isna()
    if text_mask.any():
        out.loc[text_mask] = series.loc[text_mask].astype(str).str.lower().isin({"true", "1", "1.0", "yes"})
    return out


def local_hour_float(ts: pd.Timestamp, timezone_name: str) -> float:
    if pd.isna(ts):
        return np.nan
    try:
        from zoneinfo import ZoneInfo

        local = pd.Timestamp(ts).floor("s").to_pydatetime().astimezone(ZoneInfo(str(timezone_name)))
    except Exception:
        return np.nan
    return local.hour + local.minute / 60.0 + local.second / 3600.0


def add_plateau_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["minutes_since_running_max_num"] = pd.to_numeric(out["minutes_since_running_max"], errors="coerce")
    first_max_dt = out["decision_last_obs_dt"] - pd.to_timedelta(out["minutes_since_running_max_num"], unit="m")
    out["running_max_first_obs_hour_local"] = [
        local_hour_float(ts, tz) for ts, tz in zip(first_max_dt, out["timezone"], strict=False)
    ]
    out["running_max_first_obs_before_noon_num"] = (
        pd.to_numeric(out["running_max_first_obs_hour_local"], errors="coerce").lt(12.0).astype(float)
    )
    out["decision_minus_running_max_hour"] = (
        pd.to_numeric(out["decision_hour_local"], errors="coerce")
        - pd.to_numeric(out["running_max_first_obs_hour_local"], errors="coerce")
    )
    out["morning_spike_reheat_window_num"] = (
        out["running_max_first_obs_before_noon_num"].eq(1.0)
        & pd.to_numeric(out["decision_hour_local"], errors="coerce").between(12.0, 15.5)
    ).astype(float)
    out["forecast_peak_still_ahead_num"] = (
        pd.to_numeric(out["min_forecast_peak_delta_hours_local"], errors="coerce").gt(0).astype(float)
    )
    out["warming_while_at_high_num"] = pd.to_numeric(out["temp_trend_1h_f"], errors="coerce").gt(0).astype(float)

    key_cols = ["city", "target_date", "running_value", "decision_last_obs_utc"]
    obs = (
        out.sort_values(["city", "target_date", "running_value", "decision_last_obs_dt", "decision_snapshot_dt"])
        .drop_duplicates(key_cols, keep="last")
        .copy()
    )
    obs["_at_high"] = pd.to_numeric(obs["decline_native"], errors="coerce").le(PEAK_DECLINE_MAX_NATIVE)
    plateau_count: dict[int, int] = {}
    plateau_duration: dict[int, float] = {}
    for _key, group in obs.groupby(["city", "target_date", "running_value"], sort=False):
        run_count = 0
        streak_start: pd.Timestamp | None = None
        for idx, row in group.sort_values("decision_last_obs_dt").iterrows():
            obs_dt = row["decision_last_obs_dt"]
            if bool(row["_at_high"]):
                run_count += 1
                if streak_start is None:
                    streak_start = obs_dt
                duration = np.nan if pd.isna(obs_dt) or pd.isna(streak_start) else max(0.0, (obs_dt - streak_start).total_seconds() / 60.0)
            else:
                run_count = 0
                streak_start = None
                duration = 0.0
            plateau_count[idx] = run_count
            plateau_duration[idx] = duration
    obs["plateau_obs_count_at_high"] = pd.Series(plateau_count)
    obs["plateau_duration_min"] = pd.Series(plateau_duration)
    out = out.merge(obs[key_cols + ["plateau_obs_count_at_high", "plateau_duration_min"]], on=key_cols, how="left")
    out["plateau_obs_count_at_high"] = pd.to_numeric(out["plateau_obs_count_at_high"], errors="coerce").fillna(0)
    out["plateau_duration_min"] = pd.to_numeric(out["plateau_duration_min"], errors="coerce").fillna(0.0)
    out["at_high_stalled_ge2_obs_num"] = out["plateau_obs_count_at_high"].ge(2).astype(float)
    out["at_high_stalled_ge30m_num"] = out["plateau_duration_min"].ge(30.0).astype(float)
    out["just_touched_high_num"] = out["plateau_obs_count_at_high"].le(1).astype(float)
    return out


def prepare_current_yes_rows() -> pd.DataFrame:
    df = pd.read_csv(FEATURE_ROWS)
    df = df[(df["outcome"].astype(str).str.lower() == "yes") & (df["bracket"].astype(str) == df["current_bracket"].astype(str))]
    df = df.drop_duplicates(["city", "target_date", "decision_snapshot_ts_utc", "current_bracket"]).copy()
    df = df[df["current_yes_ask"].notna() & df["current_bracket_held"].notna()].copy()
    df["target_date"] = df["target_date"].astype(str)
    df["label_survive"] = boolish(df["current_bracket_held"]).astype(int)
    df["label_future_break"] = 1 - df["label_survive"]
    df["period"] = np.where(df["target_date"] <= TRAIN_END, "train", "holdout")
    df["forward_period"] = np.where(df["target_date"] >= FORWARD_START, "forward_tail", df["period"])
    df["decision_snapshot_dt"] = pd.to_datetime(df["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    df["decision_last_obs_dt"] = pd.to_datetime(df["decision_last_obs_utc"], utc=True, errors="coerce")
    df["decision_obs_age_min"] = (df["decision_snapshot_dt"] - df["decision_last_obs_dt"]).dt.total_seconds() / 60.0
    df["month"] = pd.to_datetime(df["target_date"], errors="coerce").dt.month
    df["log_current_yes_size"] = np.log1p(pd.to_numeric(df["current_yes_ask_size"], errors="coerce").clip(lower=0))
    df["ask_gap_d1_no_minus_yes"] = pd.to_numeric(df["d1_no_ask"], errors="coerce") - pd.to_numeric(df["current_yes_ask"], errors="coerce")
    df["gap_running_to_d1_low_native"] = pd.to_numeric(df["bracket_low"], errors="coerce") + 1.0 - pd.to_numeric(df["running_native"], errors="coerce")
    df["gap_current_to_d1_low_native"] = pd.to_numeric(df["bracket_low"], errors="coerce") + 1.0 - pd.to_numeric(df["current_native"], errors="coerce")
    df["minutes_since_running_max_capped"] = pd.to_numeric(df["minutes_since_running_max"], errors="coerce").clip(upper=240)
    for col in [
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "gfs_forecast_gap_to_running_native",
        "ecmwf_forecast_gap_to_running_native",
        "forecast_peak_hour_spread",
        "forecast_peak_models_agree_le_1h",
        "current_yes_spread",
        "d1_no_spread",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["min_forecast_peak_delta_hours_local"] = df[
        ["gfs_forecast_peak_delta_hours_local", "ecmwf_forecast_peak_delta_hours_local"]
    ].min(axis=1)
    df["max_forecast_peak_delta_hours_local"] = df[
        ["gfs_forecast_peak_delta_hours_local", "ecmwf_forecast_peak_delta_hours_local"]
    ].max(axis=1)
    df["min_forecast_gap_to_running_native"] = df[
        ["gfs_forecast_gap_to_running_native", "ecmwf_forecast_gap_to_running_native"]
    ].min(axis=1)
    df["max_forecast_gap_to_running_native"] = df[
        ["gfs_forecast_gap_to_running_native", "ecmwf_forecast_gap_to_running_native"]
    ].max(axis=1)
    df["forecast_peak_models_agree_le_1h_num"] = pd.to_numeric(df.get("forecast_peak_models_agree_le_1h"), errors="coerce")
    df = add_plateau_features(df)
    return df


def prepare_peak_rows(current_yes_rows: pd.DataFrame) -> pd.DataFrame:
    return current_yes_rows[pd.to_numeric(current_yes_rows["decline_native"], errors="coerce").le(PEAK_DECLINE_MAX_NATIVE)].copy()


def make_pipeline(numeric_features: list[str], c: float = 0.3) -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric_features),
            ("cat", OneHotEncoder(handle_unknown="ignore"), CAT_FEATURES),
        ]
    )
    return Pipeline([("pre", pre), ("model", LogisticRegression(max_iter=3000, C=c, random_state=SEED))])


def score_artifact(rows: pd.DataFrame, artifact: dict[str, Any]) -> np.ndarray:
    numeric_features = list(artifact["numeric_features"])
    categorical_features = list(artifact["categorical_features"])
    work = rows.copy()
    for feature, alias in BASE_ALIAS.items():
        if feature not in work.columns and alias in work.columns:
            work[feature] = work[alias]
    for feature in numeric_features:
        if feature not in work.columns:
            work[feature] = np.nan
    for feature in categorical_features:
        if feature not in work.columns:
            work[feature] = ""
    numeric = work[numeric_features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    medians = np.asarray(artifact["numeric_medians"], dtype=float)
    means = np.asarray(artifact["numeric_means"], dtype=float)
    scales = np.asarray(artifact["numeric_scales"], dtype=float)
    numeric = np.where(np.isfinite(numeric), numeric, medians)
    numeric = (numeric - means) / scales
    cat_parts = []
    for idx, feature in enumerate(categorical_features):
        values = work[feature].astype(str).to_numpy()
        cats = [str(x) for x in artifact["categories"][idx]]
        mat = np.zeros((len(work), len(cats)), dtype=float)
        lookup = {cat: i for i, cat in enumerate(cats)}
        for row_idx, value in enumerate(values):
            col_idx = lookup.get(str(value))
            if col_idx is not None:
                mat[row_idx, col_idx] = 1.0
        cat_parts.append(mat)
    x = np.hstack([numeric] + cat_parts)
    logits = x @ np.asarray(artifact["coef"], dtype=float) + float(artifact["intercept"])
    return 1.0 / (1.0 + np.exp(-logits))


def model_metrics(frame: pd.DataFrame, model: str, p_col: str) -> dict[str, Any]:
    if frame.empty:
        return {"model": model, "rows": 0}
    y = frame["label_survive"].to_numpy(dtype=int)
    p = np.clip(frame[p_col].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
    pred = (p >= 0.5).astype(int)
    return {
        "model": model,
        "rows": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "actual_survive_rate": float(y.mean()),
        "mean_pred_survive": float(p.mean()),
        "auc_survive": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None,
        "auc_future_break": float(roc_auc_score(1 - y, 1 - p)) if len(np.unique(y)) > 1 else None,
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p)) if len(np.unique(y)) > 1 else None,
        "accuracy_at_0_5": float(accuracy_score(y, pred)),
        "balanced_accuracy_at_0_5": float(balanced_accuracy_score(y, pred)),
        "mean_edge_vs_ask": float((frame[p_col] - frame["current_yes_ask"]).mean()),
    }


def date_cluster_bootstrap_roi(frame: pd.DataFrame, cost_col: str, pnl_col: str, reps: int = 4000) -> list[float | None]:
    by_date = frame.groupby("target_date")[[cost_col, pnl_col]].sum()
    if by_date.shape[0] < 2:
        return [None, None]
    rng = np.random.default_rng(SEED)
    vals = by_date.to_numpy(dtype=float)
    out = []
    for _ in range(reps):
        sample = vals[rng.integers(0, len(vals), size=len(vals))]
        cost = float(sample[:, 0].sum())
        out.append(float(sample[:, 1].sum() / cost) if cost else np.nan)
    arr = np.asarray(out)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return [None, None]
    return [float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))]


def summarize_trade(frame: pd.DataFrame, p_col: str, name: str) -> dict[str, Any]:
    if frame.empty:
        return {
            "rule": name,
            "orders": 0,
            "active_dates": 0,
            "cities": 0,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "win_rate": None,
            "avg_ask": None,
            "avg_p": None,
            "avg_edge": None,
            "bootstrap_roi_ci95": [None, None],
            "forward_tail_orders": 0,
            "forward_tail_roi": None,
        }
    pnl_series = frame["label_survive"] - frame["current_yes_ask"]
    cost = float(frame["current_yes_ask"].sum())
    pnl = float(pnl_series.sum())
    enriched = frame.assign(_pnl=pnl_series)
    forward = enriched[enriched["target_date"].ge(FORWARD_START)]
    f_cost = float(forward["current_yes_ask"].sum()) if len(forward) else 0.0
    f_pnl = float(forward["_pnl"].sum()) if len(forward) else 0.0
    return {
        "rule": name,
        "orders": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "win_rate": float(frame["label_survive"].mean()),
        "avg_ask": float(frame["current_yes_ask"].mean()),
        "avg_p": float(frame[p_col].mean()),
        "avg_edge": float((frame[p_col] - frame["current_yes_ask"]).mean()),
        "bootstrap_roi_ci95": date_cluster_bootstrap_roi(enriched, "current_yes_ask", "_pnl"),
        "forward_tail_orders": int(len(forward)),
        "forward_tail_dates": int(forward["target_date"].nunique()) if len(forward) else 0,
        "forward_tail_roi": f_pnl / f_cost if f_cost else None,
    }


def rule_masks(frame: pd.DataFrame) -> dict[str, tuple[pd.Series, str]]:
    tradable = (
        frame["decision_hour_local"].between(12, 18)
        & frame["current_yes_ask"].between(0.35, 0.97, inclusive="both")
        & frame["current_yes_ask_size"].fillna(0).ge(5.0)
    )
    live_like = (
        frame["decision_hour_local"].between(13, 17)
        & frame["current_yes_ask"].between(0.50, 0.97, inclusive="both")
        & frame["min_forecast_peak_delta_hours_local"].fillna(-999).ge(-1.0)
    )
    downtrend = pd.to_numeric(frame["temp_trend_3h_f"], errors="coerce").le(2.0)
    stalled = frame["plateau_obs_count_at_high"].ge(2)
    return {
        "market_tradable_all": (tradable, "p_market"),
        "v3_edge_ge_02": (tradable & frame["p_v3"].ge(0.55) & (frame["p_v3"] - frame["current_yes_ask"]).ge(0.02), "p_v3"),
        "v3_edge_ge_05": (tradable & frame["p_v3"].ge(0.60) & (frame["p_v3"] - frame["current_yes_ask"]).ge(0.05), "p_v3"),
        "v3_live_like_edge_ge_02": (live_like & frame["p_v3"].ge(0.60) & (frame["p_v3"] - frame["current_yes_ask"]).ge(0.02), "p_v3"),
        "v3_stalled_edge_ge_02": (live_like & stalled & frame["p_v3"].ge(0.60) & (frame["p_v3"] - frame["current_yes_ask"]).ge(0.02), "p_v3"),
        "v3_downtrend_edge_ge_02": (live_like & downtrend & frame["p_v3"].ge(0.60) & (frame["p_v3"] - frame["current_yes_ask"]).ge(0.02), "p_v3"),
        "weather_only_edge_ge_02": (live_like & frame["p_weather"].ge(0.60) & (frame["p_weather"] - frame["current_yes_ask"]).ge(0.02), "p_weather"),
        "base_v9_edge_ge_02": (live_like & frame["p_base"].ge(0.60) & (frame["p_base"] - frame["current_yes_ask"]).ge(0.02), "p_base"),
    }


def grid_select(train: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for min_p in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]:
        for min_edge in [0.00, 0.02, 0.04, 0.06, 0.08, 0.10]:
            for min_hour in [12, 13, 14, 15]:
                for require_stalled in [False, True]:
                    mask = (
                        train["decision_hour_local"].between(min_hour, 18)
                        & train["current_yes_ask"].between(0.35, 0.97, inclusive="both")
                        & train["current_yes_ask_size"].fillna(0).ge(5.0)
                        & train["p_v3"].ge(min_p)
                        & (train["p_v3"] - train["current_yes_ask"]).ge(min_edge)
                    )
                    if require_stalled:
                        mask &= train["plateau_obs_count_at_high"].ge(2)
                    sub = train[mask]
                    if len(sub) < 25 or sub["target_date"].nunique() < 5:
                        continue
                    row = summarize_trade(sub, "p_v3", f"grid_h{min_hour}_p{min_p:.2f}_edge{min_edge:.2f}_stalled{int(require_stalled)}")
                    row.update({"min_p": min_p, "min_edge": min_edge, "min_hour": min_hour, "require_stalled": require_stalled})
                    rows.append(row)
    rows.sort(key=lambda r: (r["roi"] if r["roi"] is not None else -999, r["active_dates"], r["orders"]), reverse=True)
    return rows[:12]


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# Current-YES Future-Break Hazard V3",
        "",
        "Status: research-only",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: `future_break_hazard_v3` predicts whether the current running-max YES bracket survives, and reports the equivalent future-break probability as `1 - p_survive`.",
        "",
        "## 数据快照",
        "",
        f"- 数据源: `{payload['data_snapshot']['feature_rows']}` + `runtime/weather.db` self-check.",
        f"- Feature layer generated at UTC: `{payload['data_snapshot']['factory_generated_at_utc']}`.",
        f"- Feature target-date range: `{payload['coverage']['source_min_target_date']}`..`{payload['coverage']['source_max_target_date']}`.",
        f"- Row grain: one peak current-YES state = city + target_date + orderbook snapshot + current running-max bracket.",
        f"- Records: source rows {payload['coverage']['source_rows']}, current-YES rows {payload['coverage']['current_yes_rows']}, peak rows {payload['coverage']['peak_rows']}.",
        f"- Unsettled/missing bracket: model feature rows use `settlement_outcomes`; DB fact_trades self-check `{payload['data_self_check']['fact_trades']}`.",
        "",
        "## Funnel",
        "",
        "| step | rows | dates | cities |",
        "|---|---:|---:|---:|",
        f"| source feature rows | {payload['coverage']['source_rows']} | {payload['coverage']['source_dates']} | {payload['coverage']['source_cities']} |",
        f"| current YES rows | {payload['coverage']['current_yes_rows']} | {payload['coverage']['current_yes_dates']} | {payload['coverage']['current_yes_cities']} |",
        f"| peak-forming rows | {payload['coverage']['peak_rows']} | {payload['coverage']['peak_dates']} | {payload['coverage']['peak_cities']} |",
        f"| train peak rows | {payload['coverage']['train_rows']} | {payload['coverage']['train_dates']} | {payload['coverage']['train_cities']} |",
        f"| holdout peak rows | {payload['coverage']['holdout_rows']} | {payload['coverage']['holdout_dates']} | {payload['coverage']['holdout_cities']} |",
        f"| forward-tail rows | {payload['coverage']['forward_tail_rows']} | {payload['coverage']['forward_tail_dates']} | {payload['coverage']['forward_tail_cities']} |",
        "",
        "## Model Accuracy",
        "",
        "| model | rows | actual survive | mean p | AUC survive | AUC break | Brier | Logloss | accuracy | balanced acc | avg edge |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["holdout_model_metrics"]:
        lines.append(
            f"| {row['model']} | {row['rows']} | {pct(row.get('actual_survive_rate'))} | {pct(row.get('mean_pred_survive'))} | "
            f"{num(row.get('auc_survive'))} | {num(row.get('auc_future_break'))} | {num(row.get('brier'))} | {num(row.get('logloss'))} | "
            f"{pct(row.get('accuracy_at_0_5'))} | {pct(row.get('balanced_accuracy_at_0_5'))} | {pct(row.get('mean_edge_vs_ask'))} |"
        )
    lines.extend(
        [
            "",
            "## Trading Backtest",
            "",
            "| rule | orders | dates | cities | avg ask | win | ROI | CI | forward rows | forward ROI |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["holdout_rule_comparison"]:
        ci = row.get("bootstrap_roi_ci95") or [None, None]
        lines.append(
            f"| {row['rule']} | {row['orders']} | {row['active_dates']} | {row['cities']} | {num(row.get('avg_ask'))} | "
            f"{pct(row.get('win_rate'))} | {pct(row.get('roi'))} | [{pct(ci[0])}, {pct(ci[1])}] | "
            f"{row.get('forward_tail_orders', 0)} | {pct(row.get('forward_tail_roi'))} |"
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"significance={payload['verdict']['significance']} / baseline={payload['verdict']['baseline']} / forward={payload['verdict']['forward']} / conclusion={payload['verdict']['conclusion']}",
            "",
            payload["verdict"]["plain_text"],
            "",
            "## 8-Ring Coverage",
            "",
            "- Covered: descriptive slices, date bootstrap, signal discrimination, probability calibration, time-aligned orderbook pricing, target-date block correlation, market-price baseline.",
            "- Not covered enough for live: real forward shadow fills, maker/taker execution, capacity beyond top ask size, post-2026-06-20 settled feature rows.",
            "",
            "## Outputs",
            "",
            f"- scored rows: `{payload['outputs']['scored_rows']}`",
            f"- model metrics: `{payload['outputs']['metrics']}`",
            f"- rule comparison: `{payload['outputs']['rule_comparison']}`",
            f"- json: `{payload['outputs']['json']}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    current_yes_rows = prepare_current_yes_rows()
    rows = prepare_peak_rows(current_yes_rows)
    train = rows[rows["period"].eq("train")].copy()
    holdout = rows[rows["period"].eq("holdout")].copy()

    weather_model = make_pipeline(WEATHER_NUMERIC, c=0.3)
    v3_model = make_pipeline(WEATHER_NUMERIC + MARKET_NUMERIC, c=0.3)
    weather_model.fit(train[WEATHER_NUMERIC + CAT_FEATURES], train["label_survive"])
    v3_model.fit(train[WEATHER_NUMERIC + MARKET_NUMERIC + CAT_FEATURES], train["label_survive"])

    rows["p_weather"] = weather_model.predict_proba(rows[WEATHER_NUMERIC + CAT_FEATURES])[:, 1]
    rows["p_v3"] = v3_model.predict_proba(rows[WEATHER_NUMERIC + MARKET_NUMERIC + CAT_FEATURES])[:, 1]
    rows["p_market"] = rows["current_yes_ask"].clip(1e-6, 1 - 1e-6)
    rows["p_future_break_v3"] = 1.0 - rows["p_v3"]
    base_artifact = json.loads(BASE_MODEL.read_text(encoding="utf-8"))
    rows["p_base"] = score_artifact(rows, base_artifact)
    rows["edge_v3"] = rows["p_v3"] - rows["current_yes_ask"]
    rows.to_csv(OUT_SCORED, index=False)

    train = rows[rows["period"].eq("train")].copy()
    holdout = rows[rows["period"].eq("holdout")].copy()
    metrics = pd.DataFrame(
        [
            model_metrics(holdout, "market_price_as_probability", "p_market"),
            model_metrics(holdout, "weather_only_future_break_v3", "p_weather"),
            model_metrics(holdout, "base_current_yes_v9", "p_base"),
            model_metrics(holdout, "market_plus_weather_future_break_v3", "p_v3"),
        ]
    )
    metrics.to_csv(OUT_METRICS, index=False)

    rule_rows = []
    for name, (mask, p_col) in rule_masks(holdout).items():
        rule_rows.append(summarize_trade(holdout[mask].copy(), p_col, name))
    grid_rows = grid_select(train)
    for grid in grid_rows[:4]:
        mask = (
            holdout["decision_hour_local"].between(int(grid["min_hour"]), 18)
            & holdout["current_yes_ask"].between(0.35, 0.97, inclusive="both")
            & holdout["current_yes_ask_size"].fillna(0).ge(5.0)
            & holdout["p_v3"].ge(float(grid["min_p"]))
            & (holdout["p_v3"] - holdout["current_yes_ask"]).ge(float(grid["min_edge"]))
        )
        if bool(grid["require_stalled"]):
            mask &= holdout["plateau_obs_count_at_high"].ge(2)
        rule_rows.append(summarize_trade(holdout[mask].copy(), "p_v3", "train_selected_" + str(grid["rule"])))
    pd.DataFrame(rule_rows).to_csv(OUT_RULES, index=False)

    main_rule = next(row for row in rule_rows if row["rule"] == "v3_live_like_edge_ge_02")
    ci = main_rule.get("bootstrap_roi_ci95") or [None, None]
    significance_pass = bool(ci[0] is not None and ci[0] > 0)
    baseline_pass = bool(main_rule["roi"] is not None and main_rule["roi"] > 0 and main_rule["avg_edge"] is not None and main_rule["avg_edge"] > 0)
    forward_pass = bool(
        main_rule["forward_tail_orders"] >= 10
        and main_rule["forward_tail_roi"] is not None
        and main_rule["forward_tail_roi"] > 0
    )
    conclusion = "confirmed" if significance_pass and baseline_pass and forward_pass else "inconclusive"

    source = pd.read_csv(FEATURE_ROWS, usecols=["city", "target_date"])
    factory = json.loads(FACTORY_SUMMARY.read_text(encoding="utf-8"))
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_yes_future_break_hazard_v3",
        "data_snapshot": {
            "feature_rows": str(FEATURE_ROWS.relative_to(ROOT)),
            "factory_summary": str(FACTORY_SUMMARY.relative_to(ROOT)),
            "factory_generated_at_utc": factory.get("generated_at_utc"),
            "train_end": TRAIN_END,
            "holdout_start": HOLDOUT_START,
            "forward_tail_start": FORWARD_START,
        },
        "data_self_check": data_self_check(),
        "coverage": {
            "source_rows": int(len(source)),
            "source_min_target_date": str(source["target_date"].min()),
            "source_max_target_date": str(source["target_date"].max()),
            "source_dates": int(source["target_date"].nunique()),
            "source_cities": int(source["city"].nunique()),
            "current_yes_rows": int(len(current_yes_rows)),
            "current_yes_dates": int(current_yes_rows["target_date"].nunique()),
            "current_yes_cities": int(current_yes_rows["city"].nunique()),
            "peak_rows": int(len(rows)),
            "peak_dates": int(rows["target_date"].nunique()),
            "peak_cities": int(rows["city"].nunique()),
            "train_rows": int(len(train)),
            "train_dates": int(train["target_date"].nunique()),
            "train_cities": int(train["city"].nunique()),
            "holdout_rows": int(len(holdout)),
            "holdout_dates": int(holdout["target_date"].nunique()),
            "holdout_cities": int(holdout["city"].nunique()),
            "forward_tail_rows": int((holdout["target_date"] >= FORWARD_START).sum()),
            "forward_tail_dates": int(holdout.loc[holdout["target_date"] >= FORWARD_START, "target_date"].nunique()),
            "forward_tail_cities": int(holdout.loc[holdout["target_date"] >= FORWARD_START, "city"].nunique()),
        },
        "holdout_model_metrics": metrics.to_dict(orient="records"),
        "holdout_rule_comparison": rule_rows,
        "train_selected_grid_top12": grid_rows,
        "verdict": {
            "significance": "PASS" if significance_pass else "FAIL",
            "baseline": "PASS" if baseline_pass else "FAIL",
            "forward": "PASS" if forward_pass else "FAIL",
            "conclusion": conclusion,
            "shadow": "telemetry_only" if conclusion == "inconclusive" else "shadow_candidate",
            "live": "no_live_change",
            "plain_text": (
                f"在 {HOLDOUT_START}..{source['target_date'].max()} holdout，V3 live-like edge>=0.02 规则 "
                f"ROI 为 {pct(main_rule['roi'])}，95% 日期 bootstrap CI [{pct(ci[0])}, {pct(ci[1])}]；"
                f"forward-tail 自 {FORWARD_START} 起 {main_rule['forward_tail_orders']} rows，ROI {pct(main_rule['forward_tail_roi'])}。"
                "未同时通过显著性、基准、前瞻三门，不能上 live。"
            ),
        },
        "outputs": {
            "scored_rows": str(OUT_SCORED.relative_to(ROOT)),
            "metrics": str(OUT_METRICS.relative_to(ROOT)),
            "rule_comparison": str(OUT_RULES.relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(json_ready(payload))
    print(json.dumps(json_ready(payload["verdict"]), indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
