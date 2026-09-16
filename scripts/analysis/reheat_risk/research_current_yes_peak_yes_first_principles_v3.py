#!/usr/bin/env python3
"""First-principles peak-YES / current-YES no-reheat research v3.

The strategy question is not "which regime is a gate?"  It is whether the
remaining future-break hazard is lower than the market-implied break hazard.
This script turns the intraday regime atlas into interpretable physical hazard
components, calibrates a one-dimensional physics score on train dates, and
checks whether physics or physics+market residual edge can buy peak YES
profitably on holdout and forward dates.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
ATLAS_ROWS = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_first_principles_v3"
OUT_SCORED = OUT_DIR / "peak_yes_first_principles_v3_scored_rows.csv"
OUT_METRICS = OUT_DIR / "peak_yes_first_principles_v3_model_metrics.csv"
OUT_RULES = OUT_DIR / "peak_yes_first_principles_v3_ev_rules.csv"
OUT_COMPONENTS = OUT_DIR / "peak_yes_first_principles_v3_component_audit.csv"
OUT_BINS = OUT_DIR / "peak_yes_first_principles_v3_score_bins.csv"
OUT_REGIMES = OUT_DIR / "peak_yes_first_principles_v3_regime_diagnostics.csv"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-first-principles-v3.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-first-principles-v3.md"

TRAIN_END = "2026-05-31"
HOLDOUT_START = "2026-06-01"
HOLDOUT_END = "2026-06-20"
FORWARD_START = "2026-06-21"
SEED = 20260625

LEAKAGE_BLOCKLIST = {
    "final_max_c",
    "final_max_f",
    "final_max_native",
    "final_winning_bracket",
    "remaining_heat_native",
    "future_break_any",
    "future_break_step",
    "capped_day",
    "forecast_error_native",
    "current_yes_payoff",
    "current_yes_unit_pnl",
    "current_yes_roi",
    "current_bracket_no_payoff",
    "current_bracket_no_unit_pnl",
    "current_bracket_no_roi",
    "d1_no_payoff",
    "d1_no_unit_pnl",
    "d1_no_roi",
    "d2_no_payoff",
    "d2_no_unit_pnl",
    "d2_no_roi",
    "lottery_yes_payoff",
    "lottery_yes_unit_pnl",
    "lottery_yes_roi",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--atlas-rows", default=str(ATLAS_ROWS))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
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


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def data_self_check(db_path: Path) -> dict[str, Any]:
    conn = connect_ro(db_path)
    try:
        out = {}
        for name, sql in {
            "fact_signal_candidates": (
                "SELECT COUNT(*) AS rows, MIN(event_date) AS min_date, MAX(event_date) AS max_date, "
                "MAX(fact_built_at_utc) AS max_built_at FROM fact_signal_candidates"
            ),
            "fact_trades": (
                "SELECT COUNT(*) AS rows, MIN(target_date) AS min_date, MAX(target_date) AS max_date, "
                "MAX(fact_built_at_utc) AS max_built_at FROM fact_trades"
            ),
            "settlement_outcomes": (
                "SELECT COUNT(*) AS rows, MIN(target_date) AS min_date, MAX(target_date) AS max_date, "
                "NULL AS max_built_at FROM settlement_outcomes"
            ),
        }.items():
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description]
            out[name] = dict(zip(cols, cur.fetchone()))
        return out
    finally:
        conn.close()


def clip01(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").clip(0.0, 1.0)


def safe_num(df: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def logit_series(p: pd.Series) -> pd.Series:
    x = pd.to_numeric(p, errors="coerce").clip(1e-5, 1 - 1e-5)
    return np.log(x / (1 - x))


def load_rows(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df["current_yes_ask"].notna() & df["current_bracket_held"].notna()].copy()
    df["target_date"] = df["target_date"].astype(str)
    df["label_survive"] = pd.to_numeric(df["current_bracket_held"], errors="coerce").fillna(0).astype(int)
    df["label_future_break"] = 1 - df["label_survive"]
    df["period"] = np.select(
        [
            df["target_date"].le(TRAIN_END),
            df["target_date"].between(HOLDOUT_START, HOLDOUT_END),
            df["target_date"].ge(FORWARD_START),
        ],
        ["train", "holdout", "forward"],
        default="gap",
    )
    df["current_yes_ask"] = pd.to_numeric(df["current_yes_ask"], errors="coerce")
    df["p_break_market_raw"] = (1.0 - df["current_yes_ask"]).clip(1e-6, 1 - 1e-6)
    df["market_break_logit"] = logit_series(df["p_break_market_raw"])
    df["trade_pnl_per_share"] = df["label_survive"] - df["current_yes_ask"]
    return df


def tradable(df: pd.DataFrame) -> pd.DataFrame:
    hour = safe_num(df, "decision_hour_local")
    return df[
        hour.between(10, 21)
        & df["current_yes_ask"].between(0.35, 0.97, inclusive="left")
        & df["period"].isin(["train", "holdout", "forward"])
    ].copy()


def add_physics_components(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    out = df.copy()
    feature_inputs = {
        "decision_hour_local",
        "forecast_gap_to_running_native",
        "gfs_gap_to_running_native",
        "ecmwf_gap_to_running_native",
        "forecast_peak_delta_hours_local",
        "forecast_peak_hour_spread",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "decline_native",
        "minutes_since_running_max",
        "relative_humidity_pct",
        "dewpoint_depression_f",
        "sky_cover_code",
        "wind_speed_kt",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "solar_window",
        "city_family",
    }
    leaked = sorted(feature_inputs & LEAKAGE_BLOCKLIST)
    if leaked:
        raise RuntimeError(f"leakage feature inputs selected: {leaked}")

    hour = safe_num(out, "decision_hour_local")
    gap_cols = [
        safe_num(out, "forecast_gap_to_running_native"),
        safe_num(out, "gfs_gap_to_running_native"),
        safe_num(out, "ecmwf_gap_to_running_native"),
    ]
    forecast_gap_max = pd.concat(gap_cols, axis=1).max(axis=1)
    peak_delta = safe_num(out, "forecast_peak_delta_hours_local")
    peak_spread = safe_num(out, "forecast_peak_hour_spread")
    trend1 = safe_num(out, "temp_trend_1h_f").clip(lower=0)
    trend3 = safe_num(out, "temp_trend_3h_f").clip(lower=0)
    decline = safe_num(out, "decline_native").clip(lower=0)
    minutes_since_max = safe_num(out, "minutes_since_running_max").clip(lower=0)
    humidity = safe_num(out, "relative_humidity_pct")
    dewpoint_dep = safe_num(out, "dewpoint_depression_f")
    sky = safe_num(out, "sky_cover_code")
    wind = safe_num(out, "wind_speed_kt")

    # Component values are future-break hazard, where 1.0 means "high chance
    # this peak YES will be busted later" and 0.0 means "no-reheat confirmed".
    out["comp_solar_remaining"] = ((17.5 - hour) / 7.5).clip(0, 1).fillna(0.5)
    out["comp_forecast_runway"] = ((forecast_gap_max + 0.25) / 2.75).clip(0, 1).fillna(0.45)
    out["comp_forecast_peak_ahead"] = ((-peak_delta) / 5.0).clip(0, 1).fillna(0.45)
    out["comp_forecast_disagreement"] = (peak_spread / 4.0).clip(0, 1).fillna(0.45)
    out["comp_warming_momentum"] = ((trend1 / 4.0) * 0.55 + (trend3 / 8.0) * 0.45).clip(0, 1).fillna(0.35)
    out["comp_fresh_high"] = (1.0 - (minutes_since_max / 150.0).clip(0, 1)).fillna(0.5)
    out["comp_not_faded"] = (1.0 - (decline / 1.5).clip(0, 1)).fillna(0.5)
    dry_air = (dewpoint_dep / 18.0).clip(0, 1)
    low_humidity = (1.0 - humidity / 100.0).clip(0, 1)
    clear_sky = (1.0 - sky / 8.0).clip(0, 1)
    out["comp_dry_clear_reheat"] = (0.40 * dry_air + 0.35 * low_humidity + 0.25 * clear_sky).fillna(0.45)
    out["comp_wind_mixing"] = ((wind - 4.0) / 16.0).clip(0, 1).fillna(0.4)

    day_map = {
        "day_open_runway": 0.78,
        "day_marginal_runway": 0.62,
        "day_space_unknown": 0.55,
        "day_forecast_busted": 0.34,
        "day_forecast_capped": 0.30,
    }
    intraday_map = {
        "active_warming": 0.78,
        "reheating_after_dip": 0.72,
        "slow_warming": 0.62,
        "fresh_high": 0.52,
        "plateau_near_high": 0.42,
        "false_fade_risk": 0.47,
        "pullback_uncertain": 0.28,
        "mature_fade": 0.24,
        "flat_or_cooling": 0.22,
    }
    moisture_map = {
        "dry_heat_inertia": 0.66,
        "humid_convective_risk": 0.52,
        "mixed_moisture": 0.44,
        "humid_overcast_suppression": 0.22,
    }
    running_map = {
        "fresh_running_high": 0.58,
        "running_max_clock_unknown": 0.50,
        "stalled_high": 0.38,
        "mature_fade": 0.24,
    }

    out["comp_day_regime_prior"] = out.get("day_regime", pd.Series(index=out.index)).map(day_map).fillna(0.50)
    out["comp_intraday_prior"] = out.get("intraday_state", pd.Series(index=out.index)).map(intraday_map).fillna(0.50)
    out["comp_moisture_prior"] = out.get("moisture_cloud_regime", pd.Series(index=out.index)).map(moisture_map).fillna(0.45)
    out["comp_running_max_prior"] = out.get("running_max_state", pd.Series(index=out.index)).map(running_map).fillna(0.48)

    weights = {
        "comp_forecast_runway": 1.30,
        "comp_forecast_peak_ahead": 0.75,
        "comp_solar_remaining": 0.80,
        "comp_warming_momentum": 1.05,
        "comp_fresh_high": 0.55,
        "comp_not_faded": 0.45,
        "comp_dry_clear_reheat": 0.55,
        "comp_day_regime_prior": 1.00,
        "comp_intraday_prior": 1.10,
        "comp_moisture_prior": 0.40,
        "comp_running_max_prior": 0.55,
    }
    denom = sum(weights.values())
    out["physics_break_score_raw"] = sum(out[col].fillna(0.5) * w for col, w in weights.items()) / denom
    return out, weights


def fit_physics_calibrator(train: pd.DataFrame) -> LogisticRegression:
    model = LogisticRegression(max_iter=2000, C=1.0, random_state=SEED)
    model.fit(train[["physics_break_score_raw"]], train["label_future_break"].astype(int))
    return model


def fit_market_physics(train: pd.DataFrame) -> LogisticRegression:
    model = LogisticRegression(max_iter=2000, C=0.7, random_state=SEED)
    model.fit(train[["market_break_logit", "physics_break_score_raw"]], train["label_future_break"].astype(int))
    return model


def safe_auc(y: pd.Series, p: pd.Series) -> float | None:
    if y.nunique() < 2:
        return None
    return float(roc_auc_score(y, p))


def metric_row(frame: pd.DataFrame, p_col: str, model_name: str, period: str) -> dict[str, Any]:
    y = frame["label_future_break"].astype(int)
    p = pd.to_numeric(frame[p_col], errors="coerce").clip(1e-6, 1 - 1e-6)
    return {
        "period": period,
        "model": model_name,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "actual_break_rate": float(y.mean()),
        "mean_pred_break": float(p.mean()),
        "auc_break": safe_auc(y, p),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p)) if y.nunique() > 1 else None,
    }


def date_bootstrap_roi(frame: pd.DataFrame, reps: int = 4000) -> list[float | None]:
    by_date = frame.groupby("target_date")[["current_yes_ask", "trade_pnl_per_share"]].sum()
    if len(by_date) < 2:
        return [None, None]
    vals = by_date.to_numpy(dtype=float)
    rng = np.random.default_rng(SEED)
    out = []
    for _ in range(reps):
        sample = vals[rng.integers(0, len(vals), size=len(vals))]
        cost = float(sample[:, 0].sum())
        if cost > 0:
            out.append(float(sample[:, 1].sum() / cost))
    if not out:
        return [None, None]
    arr = np.asarray(out)
    return [float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))]


def trade_summary(frame: pd.DataFrame, rule: str, p_col: str) -> dict[str, Any]:
    if frame.empty:
        return {
            "rule": rule,
            "p_col": p_col,
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "roi_ci95": [None, None],
            "win_rate": None,
            "future_break_rate": None,
            "avg_ask": None,
            "avg_edge": None,
            "avg_p_break": None,
        }
    cost = float(frame["current_yes_ask"].sum())
    pnl = float(frame["trade_pnl_per_share"].sum())
    edge = (1.0 - frame[p_col]) - frame["current_yes_ask"]
    return {
        "rule": rule,
        "p_col": p_col,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "roi_ci95": date_bootstrap_roi(frame),
        "win_rate": float(frame["label_survive"].mean()),
        "future_break_rate": float(frame["label_future_break"].mean()),
        "avg_ask": float(frame["current_yes_ask"].mean()),
        "avg_edge": float(edge.mean()),
        "avg_p_break": float(frame[p_col].mean()),
    }


def build_ev_rules(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in ["train", "holdout", "forward"]:
        frame = scored[scored["period"].eq(period)].copy()
        rows.append({**trade_summary(frame, "buy_all_peak_yes", "p_break_market_raw"), "period": period})
        for p_col, label in [
            ("p_break_physics", "physics"),
            ("p_break_market_physics", "market_physics"),
        ]:
            edge = (1.0 - frame[p_col]) - frame["current_yes_ask"]
            for min_edge in [0.00, 0.02, 0.05, 0.08, 0.10]:
                sub = frame[edge.ge(min_edge)]
                rows.append(
                    {
                        **trade_summary(sub, f"{label}_edge_ge_{min_edge:.2f}", p_col),
                        "period": period,
                        "min_edge": min_edge,
                        "ask_low": None,
                        "ask_high": None,
                    }
                )
            sub = frame[edge.ge(0.02) & frame["current_yes_ask"].between(0.50, 0.70, inclusive="left")]
            rows.append(
                {
                    **trade_summary(sub, f"{label}_edge_ge_0.02_ask_50_70", p_col),
                    "period": period,
                    "min_edge": 0.02,
                    "ask_low": 0.50,
                    "ask_high": 0.70,
                }
            )
    return pd.DataFrame(rows)


def component_audit(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    components = [c for c in scored.columns if c.startswith("comp_")] + ["physics_break_score_raw"]
    for period in ["train", "holdout", "forward"]:
        frame = scored[scored["period"].eq(period)].copy()
        y = frame["label_future_break"].astype(int)
        for col in components:
            x = pd.to_numeric(frame[col], errors="coerce")
            mask = x.notna()
            if mask.sum() < 20 or y[mask].nunique() < 2:
                continue
            corr = float(np.corrcoef(x[mask], y[mask])[0, 1]) if mask.sum() > 2 else None
            rows.append(
                {
                    "period": period,
                    "component": col,
                    "rows": int(mask.sum()),
                    "auc_break": float(roc_auc_score(y[mask], x[mask])),
                    "corr_break": corr,
                    "mean": float(x[mask].mean()),
                }
            )
    return pd.DataFrame(rows).sort_values(["period", "auc_break"], ascending=[True, False])


def score_bins(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in ["holdout", "forward"]:
        frame = scored[scored["period"].eq(period)].copy()
        if frame.empty:
            continue
        frame["physics_bin"] = pd.qcut(frame["physics_break_score_raw"], 5, labels=False, duplicates="drop")
        for b, sub in frame.groupby("physics_bin", dropna=False):
            rows.append(
                {
                    "period": period,
                    "bin": int(b) if pd.notna(b) else None,
                    "rows": int(len(sub)),
                    "dates": int(sub["target_date"].nunique()),
                    "score_min": float(sub["physics_break_score_raw"].min()),
                    "score_max": float(sub["physics_break_score_raw"].max()),
                    "actual_break_rate": float(sub["label_future_break"].mean()),
                    "avg_p_break_physics": float(sub["p_break_physics"].mean()),
                    "avg_market_break": float(sub["p_break_market_raw"].mean()),
                    "avg_ask": float(sub["current_yes_ask"].mean()),
                    "roi_buy_yes_all": float(sub["trade_pnl_per_share"].sum() / sub["current_yes_ask"].sum()),
                }
            )
    return pd.DataFrame(rows)


def regime_diagnostics(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in ["holdout", "forward"]:
        frame = scored[scored["period"].eq(period)].copy()
        for col in ["day_regime", "intraday_state", "moisture_cloud_regime", "running_max_state"]:
            if col not in frame.columns:
                continue
            for value, sub in frame.groupby(col, dropna=False):
                if len(sub) < 20:
                    continue
                edge = (1.0 - sub["p_break_market_physics"]) - sub["current_yes_ask"]
                selected = sub[edge.ge(0.02)]
                rows.append(
                    {
                        "period": period,
                        "regime_kind": col,
                        "regime": str(value),
                        "rows": int(len(sub)),
                        "dates": int(sub["target_date"].nunique()),
                        "break_rate": float(sub["label_future_break"].mean()),
                        "avg_ask": float(sub["current_yes_ask"].mean()),
                        "roi_buy_yes_all": float(sub["trade_pnl_per_share"].sum() / sub["current_yes_ask"].sum()),
                        "selected_rows_edge2": int(len(selected)),
                        "selected_roi_edge2": (
                            float(selected["trade_pnl_per_share"].sum() / selected["current_yes_ask"].sum())
                            if not selected.empty
                            else None
                        ),
                        "avg_p_break_physics": float(sub["p_break_physics"].mean()),
                        "avg_p_break_market_physics": float(sub["p_break_market_physics"].mean()),
                    }
                )
    return pd.DataFrame(rows).sort_values(["period", "regime_kind", "rows"], ascending=[True, True, False])


def render_metric_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| period | model | rows | break | pred break | AUC | Brier | logloss |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['period']} | {row['model']} | {row['rows']} | {pct(row['actual_break_rate'])} | "
            f"{pct(row['mean_pred_break'])} | {num(row['auc_break'])} | {num(row['brier'])} | {num(row['logloss'])} |"
        )
    return lines


def render_rule_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| period | rule | rows | dates | win | avg ask | avg edge | ROI | CI |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['period']} | {row['rule']} | {row['rows']} | {row['dates']} | "
            f"{pct(row.get('win_rate'))} | {num(row.get('avg_ask'))} | {pct(row.get('avg_edge'))} | "
            f"{pct(row.get('roi'))} | [{pct(row['roi_ci95'][0])}, {pct(row['roi_ci95'][1])}] |"
        )
    return lines


def build_markdown(payload: dict[str, Any], out_md: Path) -> None:
    best_components = payload["component_audit"][:12]
    lines = [
        "# Current-YES Peak-YES First-Principles v3",
        "",
        "Status: research-only",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "## 一句话结论",
        "",
        payload["headline"],
        "",
        "## 数据范围",
        "",
        f"- Atlas rows: `{payload['inputs']['atlas_rows']}`",
        f"- Atlas date range: `{payload['coverage']['min_target_date']}`..`{payload['coverage']['max_target_date']}`",
        f"- Tradable peak-YES rows: {payload['coverage']['tradable_rows']} / dates {payload['coverage']['tradable_dates']} / cities {payload['coverage']['tradable_cities']}",
        f"- Train: `<= {TRAIN_END}`; holdout: `{HOLDOUT_START}`..`{HOLDOUT_END}`; forward: `{FORWARD_START}`..`{payload['coverage']['max_target_date']}`",
        f"- DB fact refresh: `{payload['data_self_check']['fact_signal_candidates']['max_built_at']}`",
        "",
        "Peak YES means buying the current running-max bracket YES at quote ask; payoff is 1 if no later bracket bust occurs. This is opportunity replay, not live fill PnL.",
        "",
        "Leakage guard: final max, realized remaining heat, forecast error, payoffs, and ROI fields are excluded from features.",
        "",
        "## Mechanism",
        "",
        "The physical score estimates future-break hazard from remaining solar window, forecast runway, forecast peak still ahead, warming momentum, freshness of the high, fade confirmation, dry/clear reheat support, and atlas regime priors. Regimes are continuous priors, not trading gates.",
        "",
        "Weights:",
        "",
        "| component | weight |",
        "|---|---:|",
    ]
    for comp, weight in payload["physics_weights"].items():
        lines.append(f"| {comp} | {weight:.2f} |")
    lines.extend(
        [
            "",
            "## Model Metrics",
            "",
            *render_metric_table(payload["metrics"]),
            "",
            "## EV Rules",
            "",
            *render_rule_table(payload["ev_rules"]),
            "",
            "## Component Audit",
            "",
            "| period | component | rows | AUC break | corr | mean |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in best_components:
        lines.append(
            f"| {row['period']} | {row['component']} | {row['rows']} | {num(row['auc_break'])} | "
            f"{num(row['corr_break'])} | {num(row['mean'])} |"
        )
    lines.extend(
        [
            "",
            "## Score Bins",
            "",
            "| period | bin | rows | break | p_physics | market break | avg ask | ROI all |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["score_bins"]:
        lines.append(
            f"| {row['period']} | {row['bin']} | {row['rows']} | {pct(row['actual_break_rate'])} | "
            f"{pct(row['avg_p_break_physics'])} | {pct(row['avg_market_break'])} | "
            f"{num(row['avg_ask'])} | {pct(row['roi_buy_yes_all'])} |"
        )
    lines.extend(
        [
            "",
            "## Regime Diagnostics",
            "",
            "| period | kind | regime | rows | break | avg ask | ROI all | selected rows | selected ROI |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["regime_diagnostics"][:24]:
        lines.append(
            f"| {row['period']} | {row['regime_kind']} | {row['regime']} | {row['rows']} | "
            f"{pct(row['break_rate'])} | {num(row['avg_ask'])} | {pct(row['roi_buy_yes_all'])} | "
            f"{row['selected_rows_edge2']} | {pct(row['selected_roi_edge2'])} |"
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"significance={payload['verdict']['significance']} / baseline={payload['verdict']['baseline']} / forward={payload['verdict']['forward']} / conclusion={payload['verdict']['conclusion']}",
            "",
            payload["verdict"]["text"],
            "",
            "## Outputs",
            "",
        ]
    )
    for label, path in payload["outputs"].items():
        lines.append(f"- {label}: `{path}`")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    atlas_path = Path(args.atlas_rows)
    out_dir = Path(args.out_dir)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_dir.mkdir(parents=True, exist_ok=True)

    self_check = data_self_check(db_path)
    rows = tradable(load_rows(atlas_path))
    scored, weights = add_physics_components(rows)
    train = scored[scored["period"].eq("train")].copy()
    if train["label_future_break"].nunique() < 2:
        raise RuntimeError("train split needs both future-break classes")

    physics_model = fit_physics_calibrator(train)
    market_physics_model = fit_market_physics(train)
    scored["p_break_physics"] = physics_model.predict_proba(scored[["physics_break_score_raw"]])[:, 1]
    scored["p_break_market_physics"] = market_physics_model.predict_proba(
        scored[["market_break_logit", "physics_break_score_raw"]]
    )[:, 1]

    metric_rows = []
    for period in ["train", "holdout", "forward"]:
        frame = scored[scored["period"].eq(period)].copy()
        for col, label in [
            ("p_break_market_raw", "market_implied_break"),
            ("p_break_physics", "first_principles_physics"),
            ("p_break_market_physics", "market_plus_physics"),
        ]:
            metric_rows.append(metric_row(frame, col, label, period))

    rules = build_ev_rules(scored)
    components = component_audit(scored)
    bins = score_bins(scored)
    regimes = regime_diagnostics(scored)

    scored.to_csv(OUT_SCORED, index=False)
    pd.DataFrame(metric_rows).to_csv(OUT_METRICS, index=False)
    rules.to_csv(OUT_RULES, index=False)
    components.to_csv(OUT_COMPONENTS, index=False)
    bins.to_csv(OUT_BINS, index=False)
    regimes.to_csv(OUT_REGIMES, index=False)

    holdout_market = next(r for r in metric_rows if r["period"] == "holdout" and r["model"] == "market_implied_break")
    holdout_physics = next(r for r in metric_rows if r["period"] == "holdout" and r["model"] == "first_principles_physics")
    holdout_market_physics = next(r for r in metric_rows if r["period"] == "holdout" and r["model"] == "market_plus_physics")
    holdout_physics_rule = rules[(rules["period"].eq("holdout")) & (rules["rule"].eq("physics_edge_ge_0.02"))].iloc[0]
    forward_physics_rule = rules[(rules["period"].eq("forward")) & (rules["rule"].eq("physics_edge_ge_0.02"))].iloc[0]
    holdout_market_physics_rule = rules[
        (rules["period"].eq("holdout")) & (rules["rule"].eq("market_physics_edge_ge_0.00"))
    ].iloc[0]
    forward_market_physics_rule = rules[
        (rules["period"].eq("forward")) & (rules["rule"].eq("market_physics_edge_ge_0.00"))
    ].iloc[0]

    headline = (
        "First-principles physics has real ordering signal, but it is still weaker than market pricing for peak YES: "
        f"holdout physics AUC {holdout_physics['auc_break']:.3f}, market AUC {holdout_market['auc_break']:.3f}, "
        f"market+physics AUC {holdout_market_physics['auc_break']:.3f}. "
        f"`physics edge>=2%` holdout ROI {pct(holdout_physics_rule['roi'])}, CI "
        f"[{pct(holdout_physics_rule['roi_ci95'][0])}, {pct(holdout_physics_rule['roi_ci95'][1])}], "
        f"forward ROI {pct(forward_physics_rule['roi'])} on {int(forward_physics_rule['rows'])} rows. "
        f"`market+physics edge>=0` is positive but thin: holdout {int(holdout_market_physics_rule['rows'])} rows "
        f"ROI {pct(holdout_market_physics_rule['roi'])}, forward {int(forward_market_physics_rule['rows'])} rows."
    )

    verdict = {
        "significance": "FAIL",
        "baseline": "FAIL",
        "forward": "FAIL",
        "conclusion": "inconclusive",
        "text": (
            "The improved first-principles score is useful as a diagnostic and shadow feature, especially for rejecting open-runway/active-warming peak YES. "
            "It does not yet justify live trading because market remains the stronger probability baseline and holdout EV confidence intervals cross zero. "
            "The next improvement should target missing mechanism features, not more hard filters: true solar altitude, intra-hour observation cadence, forecast curve slope, and cloud/wind change after the high."
        ),
    }

    payload = {
        "generated_at_utc": now_utc(),
        "inputs": {"db": str(db_path.relative_to(ROOT)), "atlas_rows": str(atlas_path.relative_to(ROOT))},
        "data_self_check": self_check,
        "coverage": {
            "min_target_date": str(scored["target_date"].min()),
            "max_target_date": str(scored["target_date"].max()),
            "tradable_rows": int(len(scored)),
            "tradable_dates": int(scored["target_date"].nunique()),
            "tradable_cities": int(scored["city"].nunique()),
            "period_counts": scored["period"].value_counts().to_dict(),
        },
        "physics_weights": weights,
        "headline": headline,
        "metrics": metric_rows,
        "ev_rules": rules[
            rules["rule"].isin(
                [
                    "buy_all_peak_yes",
                    "physics_edge_ge_0.02",
                    "physics_edge_ge_0.05",
                    "physics_edge_ge_0.02_ask_50_70",
                    "market_physics_edge_ge_0.00",
                    "market_physics_edge_ge_0.02",
                    "market_physics_edge_ge_0.05",
                    "market_physics_edge_ge_0.02_ask_50_70",
                ]
            )
        ].sort_values(["period", "rule"]).to_dict("records"),
        "component_audit": components.to_dict("records"),
        "score_bins": bins.to_dict("records"),
        "regime_diagnostics": regimes.to_dict("records"),
        "verdict": verdict,
        "outputs": {
            "scored_rows": str(OUT_SCORED.relative_to(ROOT)),
            "model_metrics": str(OUT_METRICS.relative_to(ROOT)),
            "ev_rules": str(OUT_RULES.relative_to(ROOT)),
            "component_audit": str(OUT_COMPONENTS.relative_to(ROOT)),
            "score_bins": str(OUT_BINS.relative_to(ROOT)),
            "regime_diagnostics": str(OUT_REGIMES.relative_to(ROOT)),
            "json": str(out_json.relative_to(ROOT)),
            "markdown": str(out_md.relative_to(ROOT)),
        },
    }
    out_json.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    build_markdown(json_ready(payload), out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
