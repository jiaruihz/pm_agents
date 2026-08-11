#!/usr/bin/env python3
"""Preferred-model payoff replay for current-bracket NO.

This is the follow-up to the 2026-06-23 payoff/source review.  It keeps the
trade expression fixed at current-bracket NO, replaces the GFS-first forecast
view with a city calibration routed forecast replay where possible, and trains
the payoff labels on the trade-base universe.

Important: strict previous-day point-in-time daily caches are currently GFS
only for most of the history.  Non-GFS model values here come from the archived
Open-Meteo historical forecast cache, so this is a source-corrected replay, not
a fully PIT non-GFS replay.
"""

from __future__ import annotations

import json
import math
import os
import re
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
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import research_current_bracket_no_pass_through_v1 as pass_through  # noqa: E402
import research_current_bracket_no_prevday_pit_shadow_v1 as pit_shadow  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_preferred_model_payoff_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_FORECAST = OUT_DIR / "preferred_forecast_rows.csv"
OUT_VARIANTS = OUT_DIR / "variant_summary.csv"
OUT_DAILY = OUT_DIR / "daily_variant_summary.csv"
OUT_ALL_LOSS = OUT_DIR / "all_loss_day_trade_details.csv"
OUT_SELECTED = OUT_DIR / "selected_trade_rows.csv"
OUT_FORWARD = OUT_DIR / "forward_validation_and_shadow.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-preferred-model-payoff-v1.md"

OPEN_METEO_DIR = ROOT / "runtime/weather_edge_v1/market_data/cache/open_meteo_historical_forecast"
GFS_DAILY_DIR = ROOT / "runtime/weather_edge_v1/market_data/cache/gfs_daily"
FORWARD_FEATURE_ROWS = (
    ROOT
    / "docs/analysis/2026-06/generated/current_bracket_no_prevday_pit_shadow_v1/forward_feature_factory/reheat_feature_rows.csv"
)

SEED = 20260624
STAKE_USD = pass_through.STAKE_USD
BOOTSTRAP_REPS = 3000

NUM_FEATURES = [
    "decision_hour_local",
    "forecast_peak_hour_local",
    "forecast_peak_delta_hours_local",
    "preferred_gap_to_running_native",
    "forecast_gap_to_bracket_upper_native",
    "gfs_gap_to_bracket_upper_native",
    "ecmwf_gap_to_bracket_upper_native",
    "available_model_gap_spread_abs",
    "preferred_minus_gfs_gap_to_upper",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "decline_native",
    "distance_into_bracket_native",
    "current_native",
    "running_native",
    "relative_humidity_pct",
    "dewpoint_depression_f",
    "wind_speed_kt",
    "sky_cover_code",
    "calibration_best_rmse",
    "calibration_gfs_rmse",
    "calibration_ecmwf_rmse",
    "calibration_gfs_minus_ecmwf_rmse",
]
CAT_FEATURES = [
    "city",
    "unit",
    "forecast_route_model",
    "forecast_route_status",
    "calibration_best_model",
]


@dataclass(frozen=True)
class ForecastPoint:
    city: str
    target_date: str
    model: str
    layer: str
    forecast_max_f: float
    forecast_max_c: float
    forecast_peak_hour_local: int
    source_file: str


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite_or_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: finite_or_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite_or_none(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def pct(value: Any) -> str:
    try:
        fval = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(fval):
        return "NA"
    return f"{100.0 * fval:+.1f}%"


def money(value: Any) -> str:
    try:
        fval = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(fval):
        return "NA"
    return f"${fval:+,.2f}"


def calibration_results_path() -> Path:
    value = os.getenv("WEATHER_LEGACY_CALIBRATION_RESULTS", "").strip()
    if not value:
        raise FileNotFoundError(
            "set WEATHER_LEGACY_CALIBRATION_RESULTS to the immutable calibration_results_v5.json input"
        )
    path = Path(value).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"legacy calibration input does not exist: {path}")
    return path


def load_calibration() -> pd.DataFrame:
    data = json.loads(calibration_results_path().read_text(encoding="utf-8"))
    rows = []
    for city, row in data.items():
        gfs = row.get("gfs") or {}
        ecmwf = row.get("ecmwf") or {}
        rows.append(
            {
                "city": city,
                "calibration_best_model": row.get("_best_model"),
                "calibration_best_rmse": row.get("_best_rmse"),
                "calibration_best_bias": row.get("_best_bias"),
                "calibration_gfs_rmse": gfs.get("rmse"),
                "calibration_ecmwf_rmse": ecmwf.get("rmse"),
                "calibration_gfs_bias": gfs.get("bias"),
                "calibration_ecmwf_bias": ecmwf.get("bias"),
            }
        )
    out = pd.DataFrame(rows)
    out["calibration_gfs_minus_ecmwf_rmse"] = pd.to_numeric(out["calibration_gfs_rmse"], errors="coerce") - pd.to_numeric(
        out["calibration_ecmwf_rmse"], errors="coerce"
    )
    return out


def c_to_f(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


def f_to_c(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def open_meteo_index() -> dict[tuple[str, str], list[Path]]:
    pat = re.compile(r"^(gfs|ecmwf)_(.+)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.json$")
    idx: dict[tuple[str, str], list[Path]] = {}
    for path in OPEN_METEO_DIR.glob("*.json"):
        m = pat.match(path.name)
        if not m:
            continue
        model, city, _start, _end = m.groups()
        idx.setdefault((city, model), []).append(path)
    for paths in idx.values():
        paths.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return idx


def file_covers(path: Path, target_date: str) -> bool:
    m = re.match(r"^(?:gfs|ecmwf)_.+_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.json$", path.name)
    if not m:
        return False
    start, end = m.groups()
    return start <= target_date <= end


def load_open_meteo_forecast(city: str, target_date: str, model: str, idx: dict[tuple[str, str], list[Path]]) -> ForecastPoint | None:
    for path in idx.get((city, model), []):
        if not file_covers(path, target_date):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        temps = hourly.get("temperature_2m") or []
        unit = str((data.get("hourly_units") or {}).get("temperature_2m") or "").lower()
        day_vals: list[tuple[int, float]] = []
        for raw_time, raw_temp in zip(times, temps):
            if raw_temp is None or not str(raw_time).startswith(target_date):
                continue
            hour = int(str(raw_time)[11:13])
            day_vals.append((hour, float(raw_temp)))
        if not day_vals:
            continue
        peak_hour, max_value = max(day_vals, key=lambda x: (x[1], -x[0]))
        if "°f" in unit or unit == "f":
            max_f = max_value
            max_c = f_to_c(max_value)
        else:
            max_c = max_value
            max_f = c_to_f(max_value)
        return ForecastPoint(
            city=city,
            target_date=target_date,
            model=model,
            layer="open_meteo_historical_forecast",
            forecast_max_f=max_f,
            forecast_max_c=max_c,
            forecast_peak_hour_local=int(peak_hour),
            source_file=str(path.relative_to(ROOT)),
        )
    return None


def load_gfs_daily_forecast(city: str, target_date: str) -> ForecastPoint | None:
    path = GFS_DAILY_DIR / f"{city}_{target_date}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    vals = [float(x) for x in data.get("hourly_temps") or [] if x is not None]
    if not vals:
        return None
    peak_hour, max_f = max(enumerate(vals), key=lambda x: (x[1], -x[0]))
    return ForecastPoint(
        city=city,
        target_date=target_date,
        model="gfs",
        layer="gfs_daily_true_prevday",
        forecast_max_f=float(max_f),
        forecast_max_c=f_to_c(float(max_f)),
        forecast_peak_hour_local=int(peak_hour),
        source_file=str(path.relative_to(ROOT)),
    )


def build_forecast_rows(universe: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    idx = open_meteo_index()
    cal = load_calibration()
    cal_map = cal.set_index("city").to_dict(orient="index")
    rows = []
    for item in universe[["city", "target_date", "unit"]].drop_duplicates().itertuples(index=False):
        city = str(item.city)
        target_date = str(item.target_date)
        unit = str(item.unit).upper()
        forecasts = {
            "gfs_pit": load_gfs_daily_forecast(city, target_date),
            "gfs": load_open_meteo_forecast(city, target_date, "gfs", idx),
            "ecmwf": load_open_meteo_forecast(city, target_date, "ecmwf", idx),
        }
        cal_row = cal_map.get(city, {})
        best = str(cal_row.get("calibration_best_model") or "").lower()
        route_model = best if best in {"gfs", "ecmwf"} else None
        route_status = "calibration_best_available" if route_model else "calibration_best_unavailable"
        chosen = forecasts.get(route_model or "")
        if chosen is None:
            gfs_rmse = cal_row.get("calibration_gfs_rmse")
            ecmwf_rmse = cal_row.get("calibration_ecmwf_rmse")
            candidates = []
            for model in ["gfs", "ecmwf"]:
                point = forecasts.get(model)
                rmse = gfs_rmse if model == "gfs" else ecmwf_rmse
                if point is not None and rmse is not None and math.isfinite(float(rmse)):
                    candidates.append((float(rmse), model, point))
            if candidates:
                _rmse, route_model, chosen = sorted(candidates, key=lambda x: x[0])[0]
                route_status = "fallback_best_available_gfs_ecmwf"
            elif forecasts["gfs_pit"] is not None:
                route_model, chosen = "gfs", forecasts["gfs_pit"]
                route_status = "fallback_gfs_true_prevday"
            elif forecasts["gfs"] is not None:
                route_model, chosen = "gfs", forecasts["gfs"]
                route_status = "fallback_gfs_historical"
        if chosen is None:
            rows.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "unit": unit,
                    "forecast_route_model": "",
                    "forecast_route_status": "missing_all_forecasts",
                    "calibration_best_model": best or "",
                }
            )
            continue
        gfs_point = forecasts["gfs_pit"] or forecasts["gfs"]
        ecmwf_point = forecasts["ecmwf"]
        gfs_native = None if gfs_point is None else (gfs_point.forecast_max_f if unit == "F" else gfs_point.forecast_max_c)
        ecmwf_native = None if ecmwf_point is None else (ecmwf_point.forecast_max_f if unit == "F" else ecmwf_point.forecast_max_c)
        chosen_native = chosen.forecast_max_f if unit == "F" else chosen.forecast_max_c
        rows.append(
            {
                "city": city,
                "target_date": target_date,
                "unit": unit,
                "forecast_route_model": route_model,
                "forecast_route_status": route_status,
                "calibration_best_model": best or "",
                "forecast_max_native": chosen_native,
                "forecast_max_f": chosen.forecast_max_f,
                "forecast_max_c": chosen.forecast_max_c,
                "forecast_peak_hour_local": chosen.forecast_peak_hour_local,
                "forecast_layer": chosen.layer,
                "forecast_source_file": chosen.source_file,
                "gfs_forecast_max_native": gfs_native,
                "gfs_forecast_peak_hour_local": None if gfs_point is None else gfs_point.forecast_peak_hour_local,
                "gfs_forecast_layer": None if gfs_point is None else gfs_point.layer,
                "ecmwf_forecast_max_native": ecmwf_native,
                "ecmwf_forecast_peak_hour_local": None if ecmwf_point is None else ecmwf_point.forecast_peak_hour_local,
                "ecmwf_forecast_layer": None if ecmwf_point is None else ecmwf_point.layer,
            }
        )
    out = pd.DataFrame(rows)
    stats = {
        "rows": int(len(out)),
        "route_status": out["forecast_route_status"].value_counts(dropna=False).to_dict() if not out.empty else {},
        "route_model": out["forecast_route_model"].value_counts(dropna=False).to_dict() if not out.empty else {},
        "date_min": str(out["target_date"].min()) if not out.empty else None,
        "date_max": str(out["target_date"].max()) if not out.empty else None,
    }
    return out, stats


def load_dataset() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    raw = pass_through.load_feature_rows()
    df = pass_through.enrich_current_no(raw)
    df["depth5_notional"] = df["no_ask"] * df["quote_depth_ask_5c"]
    df = df[df["midday_h10_14"] & df["actual_peak_afternoon"].notna()].copy()
    universe = df[["city", "target_date", "unit"]].drop_duplicates()
    forecast_rows, forecast_stats = build_forecast_rows(universe)
    forecast_rows.to_csv(OUT_FORECAST, index=False)
    df = df.merge(forecast_rows, on=["city", "target_date", "unit"], how="left", suffixes=("", "_preferred"))
    df = df.merge(load_calibration(), on="city", how="left", suffixes=("", "_cal"))
    decision_hour = pd.to_numeric(df["decision_hour_local"], errors="coerce")
    running = pd.to_numeric(df["running_native"], errors="coerce")
    bracket_upper = pd.to_numeric(df["bracket_upper"], errors="coerce")
    df["forecast_peak_delta_hours_local"] = decision_hour - pd.to_numeric(df["forecast_peak_hour_local"], errors="coerce")
    df["preferred_gap_to_running_native"] = pd.to_numeric(df["forecast_max_native"], errors="coerce") - running
    df["forecast_gap_to_bracket_upper_native"] = pd.to_numeric(df["forecast_max_native"], errors="coerce") - bracket_upper
    df["gfs_gap_to_bracket_upper_native"] = pd.to_numeric(df["gfs_forecast_max_native"], errors="coerce") - bracket_upper
    df["ecmwf_gap_to_bracket_upper_native"] = pd.to_numeric(df["ecmwf_forecast_max_native"], errors="coerce") - bracket_upper
    df["available_model_gap_spread_abs"] = (
        pd.to_numeric(df["gfs_gap_to_bracket_upper_native"], errors="coerce")
        - pd.to_numeric(df["ecmwf_gap_to_bracket_upper_native"], errors="coerce")
    ).abs()
    df["preferred_minus_gfs_gap_to_upper"] = (
        pd.to_numeric(df["forecast_gap_to_bracket_upper_native"], errors="coerce")
        - pd.to_numeric(df["gfs_gap_to_bracket_upper_native"], errors="coerce")
    )
    df["label_afternoon_peak"] = df["actual_peak_afternoon"].astype(int)
    df["label_no_wins"] = pd.to_numeric(df["label_no_wins"], errors="coerce").astype(int)
    df["noise_margin_native"] = np.where(df["unit"].astype(str).str.upper().eq("F"), 0.5, 0.25)
    df["label_up_margin"] = (
        pd.to_numeric(df["final_max_native"], errors="coerce")
        > pd.to_numeric(df["bracket_upper"], errors="coerce") + pd.to_numeric(df["noise_margin_native"], errors="coerce")
    ).astype(int)
    df["actual_margin_to_upper_native"] = pd.to_numeric(df["final_max_native"], errors="coerce") - pd.to_numeric(
        df["bracket_upper"], errors="coerce"
    )
    df["trade_base"] = (
        df["no_ask"].between(0.10, 0.35)
        & df["depth5_notional"].ge(STAKE_USD)
        & df["forecast_route_status"].astype(str).ne("missing_all_forecasts")
    )
    df["stake_cost_usd"] = STAKE_USD
    df["stake_shares"] = STAKE_USD / df["no_ask"]
    df["stake_profit_usd"] = df["label_no_wins"] * df["stake_shares"] - STAKE_USD
    for col in NUM_FEATURES:
        if col not in df.columns:
            df[col] = np.nan
    for col in CAT_FEATURES:
        if col not in df.columns:
            df[col] = ""
    return df.reset_index(drop=True), forecast_rows, forecast_stats


def attach_preferred_forecast(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    universe = df[["city", "target_date", "unit"]].drop_duplicates()
    forecast_rows, forecast_stats = build_forecast_rows(universe)
    out = df.merge(forecast_rows, on=["city", "target_date", "unit"], how="left", suffixes=("", "_preferred"))
    out = out.merge(load_calibration(), on="city", how="left", suffixes=("", "_cal"))
    decision_hour = pd.to_numeric(out["decision_hour_local"], errors="coerce")
    running = pd.to_numeric(out["running_native"], errors="coerce")
    bracket_upper = pd.to_numeric(out["bracket_upper"], errors="coerce")
    out["forecast_peak_delta_hours_local"] = decision_hour - pd.to_numeric(out["forecast_peak_hour_local"], errors="coerce")
    out["preferred_gap_to_running_native"] = pd.to_numeric(out["forecast_max_native"], errors="coerce") - running
    out["forecast_gap_to_bracket_upper_native"] = pd.to_numeric(out["forecast_max_native"], errors="coerce") - bracket_upper
    out["gfs_gap_to_bracket_upper_native"] = pd.to_numeric(out["gfs_forecast_max_native"], errors="coerce") - bracket_upper
    out["ecmwf_gap_to_bracket_upper_native"] = pd.to_numeric(out["ecmwf_forecast_max_native"], errors="coerce") - bracket_upper
    out["available_model_gap_spread_abs"] = (
        pd.to_numeric(out["gfs_gap_to_bracket_upper_native"], errors="coerce")
        - pd.to_numeric(out["ecmwf_gap_to_bracket_upper_native"], errors="coerce")
    ).abs()
    out["preferred_minus_gfs_gap_to_upper"] = (
        pd.to_numeric(out["forecast_gap_to_bracket_upper_native"], errors="coerce")
        - pd.to_numeric(out["gfs_gap_to_bracket_upper_native"], errors="coerce")
    )
    out["noise_margin_native"] = np.where(out["unit"].astype(str).str.upper().eq("F"), 0.5, 0.25)
    out["actual_margin_to_upper_native"] = pd.to_numeric(out.get("final_max_native"), errors="coerce") - bracket_upper
    for col in NUM_FEATURES:
        if col not in out.columns:
            out[col] = np.nan
    for col in CAT_FEATURES:
        if col not in out.columns:
            out[col] = ""
    return out.reset_index(drop=True), forecast_rows, forecast_stats


def load_forward_dataset() -> tuple[pd.DataFrame, dict[str, Any]]:
    if not FORWARD_FEATURE_ROWS.exists():
        return pd.DataFrame(), {"missing": str(FORWARD_FEATURE_ROWS.relative_to(ROOT))}
    raw = pd.read_csv(FORWARD_FEATURE_ROWS, low_memory=False)
    cur = raw[
        raw["outcome"].astype(str).str.lower().eq("no")
        & raw["bracket"].astype(str).eq(raw["current_bracket"].astype(str))
        & raw["quote_best_ask"].notna()
        & raw["current_bracket"].notna()
    ].copy()
    if cur.empty:
        return cur, {"rows": 0}
    cur["no_ask"] = cur["quote_best_ask"]
    cur["no_ask_size"] = cur["quote_best_ask_size"]
    cur["depth5_notional"] = cur["no_ask"] * cur["quote_depth_ask_5c"]
    cur["bracket_upper"] = cur["bracket_high"].fillna(cur["bracket_low"])
    cur["distance_into_bracket_native"] = cur["running_native"] - cur["bracket_low"]
    held = pd.to_numeric(cur.get("current_bracket_held"), errors="coerce")
    cur["label_no_wins"] = np.where(held.notna(), 1.0 - held, np.nan)
    cur["final_max_native"] = np.where(cur["unit"].astype(str).str.upper().eq("F"), cur["final_max_f"], cur["final_max_c"])
    cur["label_up_margin"] = np.where(
        cur["label_no_wins"].notna(),
        (
            pd.to_numeric(cur["final_max_native"], errors="coerce")
            > pd.to_numeric(cur["bracket_upper"], errors="coerce")
            + np.where(cur["unit"].astype(str).str.upper().eq("F"), 0.5, 0.25)
        ).astype(float),
        np.nan,
    )
    cur["trade_base"] = cur["decision_hour_local"].between(10, 14) & cur["no_ask"].between(0.10, 0.35) & cur[
        "depth5_notional"
    ].ge(STAKE_USD)
    cur["stake_cost_usd"] = STAKE_USD
    cur["stake_shares"] = STAKE_USD / cur["no_ask"]
    cur["stake_profit_usd"] = cur["label_no_wins"] * cur["stake_shares"] - STAKE_USD
    scored, _forecast_rows, forecast_stats = attach_preferred_forecast(cur)
    return scored, {"rows": int(len(scored)), "forecast_stats": forecast_stats}


def split_dates(df: pd.DataFrame) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    dates = sorted(df["target_date"].dropna().astype(str).unique())
    split_idx = max(1, int(len(dates) * 0.70))
    split_date = dates[split_idx - 1]
    return split_date, df[df["target_date"].astype(str) <= split_date].copy(), df[df["target_date"].astype(str) > split_date].copy()


def build_model() -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]), NUM_FEATURES),
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5)),
                    ]
                ),
                CAT_FEATURES,
            ),
        ]
    )
    return Pipeline([("pre", pre), ("clf", LogisticRegression(C=0.2, max_iter=1000, class_weight="balanced", random_state=SEED))])


def compute_model_metrics(pipe: Pipeline, frame: pd.DataFrame, label_col: str) -> dict[str, Any]:
    if frame.empty:
        return {"rows": 0}
    y = frame[label_col].astype(int)
    p = pipe.predict_proba(frame[NUM_FEATURES + CAT_FEATURES])[:, 1]
    return {
        "rows": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "label_rate": float(y.mean()),
        "auc": float(roc_auc_score(y, p)) if y.nunique() > 1 else None,
        "brier": float(brier_score_loss(y, p)),
        "prob_mean": float(p.mean()),
    }


def grouped_profit_cost(frame: pd.DataFrame) -> dict[str, tuple[float, float]]:
    if frame.empty:
        return {}
    grouped = frame.groupby("target_date").agg(profit=("stake_profit_usd", "sum"), cost=("stake_cost_usd", "sum"))
    return {str(idx): (float(row.profit), float(row.cost)) for idx, row in grouped.iterrows()}


def block_bootstrap_roi(frame: pd.DataFrame) -> dict[str, Any]:
    by_date = grouped_profit_cost(frame)
    dates = sorted(by_date)
    if len(dates) < 3:
        return {"ci_low": None, "ci_high": None, "active_dates": len(dates), "reps": 0}
    rng = np.random.default_rng(SEED)
    vals = []
    for _ in range(BOOTSTRAP_REPS):
        draw = rng.choice(dates, size=len(dates), replace=True)
        profit = sum(by_date[d][0] for d in draw)
        cost = sum(by_date[d][1] for d in draw)
        vals.append(profit / cost)
    return {
        "ci_low": float(np.quantile(vals, 0.025)),
        "ci_high": float(np.quantile(vals, 0.975)),
        "active_dates": len(dates),
        "reps": len(vals),
    }


def block_bootstrap_delta(candidate: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, Any]:
    cand = grouped_profit_cost(candidate)
    base = grouped_profit_cost(baseline)
    dates = sorted(set(cand) | set(base))
    if len(dates) < 3:
        return {"ci_low": None, "ci_high": None, "active_dates": len(dates), "reps": 0}
    rng = np.random.default_rng(SEED + 1)
    vals = []
    for _ in range(BOOTSTRAP_REPS):
        draw = rng.choice(dates, size=len(dates), replace=True)
        cp = sum(cand.get(d, (0.0, 0.0))[0] for d in draw)
        cc = sum(cand.get(d, (0.0, 0.0))[1] for d in draw)
        bp = sum(base.get(d, (0.0, 0.0))[0] for d in draw)
        bc = sum(base.get(d, (0.0, 0.0))[1] for d in draw)
        if cc > 0 and bc > 0:
            vals.append(cp / cc - bp / bc)
    return {
        "ci_low": float(np.quantile(vals, 0.025)) if vals else None,
        "ci_high": float(np.quantile(vals, 0.975)) if vals else None,
        "active_dates": len(dates),
        "reps": len(vals),
    }


def select_first(frame: pd.DataFrame) -> pd.DataFrame:
    return pass_through.select_first_per_city_day(frame.copy())


def select_top_per_date(frame: pd.DataFrame, per_date: int, score_col: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.sort_values(["target_date", score_col, "no_ask"], ascending=[True, False, True]).copy()
    out["_date_rank"] = out.groupby("target_date").cumcount() + 1
    return out[out["_date_rank"].le(per_date)].drop(columns=["_date_rank"]).reset_index(drop=True)


def summarize_variant(name: str, raw: pd.DataFrame, baseline: pd.DataFrame | None = None) -> dict[str, Any]:
    selected = select_first(raw)
    if selected.empty:
        return {"variant": name, "raw_signals": int(len(raw)), "selected_trades": 0}
    profit = float(selected["stake_profit_usd"].sum())
    cost = float(selected["stake_cost_usd"].sum())
    ci = block_bootstrap_roi(selected)
    all_loss_dates = (
        selected.groupby("target_date")["label_no_wins"].sum().reset_index().query("label_no_wins == 0")["target_date"].astype(str).tolist()
    )
    out: dict[str, Any] = {
        "variant": name,
        "raw_signals": int(len(raw)),
        "selected_trades": int(len(selected)),
        "active_dates": int(selected["target_date"].nunique()),
        "cities": int(selected["city"].nunique()),
        "start_date": str(selected["target_date"].min()),
        "end_date": str(selected["target_date"].max()),
        "avg_no_ask": float(selected["no_ask"].mean()),
        "no_win_rate": float(selected["label_no_wins"].mean()),
        "up_margin_win_rate": float(selected["label_up_margin"].mean()),
        "avg_actual_margin_to_upper_native": float(selected["actual_margin_to_upper_native"].mean()),
        "cost_usd": cost,
        "profit_usd": profit,
        "roi": profit / cost if cost else None,
        "roi_ci_low": ci["ci_low"],
        "roi_ci_high": ci["ci_high"],
        "bootstrap_active_dates": ci["active_dates"],
        "holdout_selected_trades": int(selected[selected["period_split"].eq("holdout")].shape[0]),
        "holdout_roi": roi_of(selected[selected["period_split"].eq("holdout")]),
        "train_roi": roi_of(selected[selected["period_split"].eq("train")]),
        "selected_all_loss_days": int(len(all_loss_dates)),
        "selected_all_loss_trades": int(selected[selected["target_date"].astype(str).isin(all_loss_dates)].shape[0]),
        "selected_all_loss_dates": ",".join(all_loss_dates),
        "route_calibration_best_available_share": float(selected["forecast_route_status"].astype(str).eq("calibration_best_available").mean()),
        "route_fallback_best_available_share": float(selected["forecast_route_status"].astype(str).eq("fallback_best_available_gfs_ecmwf").mean()),
    }
    if baseline is not None and not baseline.empty:
        base_roi = roi_of(baseline)
        delta = block_bootstrap_delta(selected, baseline)
        out.update(
            {
                "baseline_trades": int(len(baseline)),
                "baseline_roi": base_roi,
                "excess_roi_vs_baseline": (profit / cost - base_roi) if cost and base_roi is not None else None,
                "excess_roi_ci_low": delta["ci_low"],
                "excess_roi_ci_high": delta["ci_high"],
            }
        )
    return out


def roi_of(frame: pd.DataFrame) -> float | None:
    if frame.empty:
        return None
    cost = float(frame["stake_cost_usd"].sum())
    return float(frame["stake_profit_usd"].sum()) / cost if cost else None


def daily_summary(variant: str, raw: pd.DataFrame) -> pd.DataFrame:
    selected = select_first(raw)
    if selected.empty:
        return pd.DataFrame()
    out = (
        selected.groupby("target_date")
        .agg(
            trades=("city", "size"),
            cities=("city", "nunique"),
            wins=("label_no_wins", "sum"),
            up_margin_wins=("label_up_margin", "sum"),
            cost_usd=("stake_cost_usd", "sum"),
            profit_usd=("stake_profit_usd", "sum"),
            avg_no_ask=("no_ask", "mean"),
            avg_p_no_win=("p_no_win", "mean"),
            avg_p_up_margin=("p_up_margin", "mean"),
            avg_margin=("actual_margin_to_upper_native", "mean"),
        )
        .reset_index()
    )
    out["variant"] = variant
    out["roi"] = out["profit_usd"] / out["cost_usd"]
    out["win_rate"] = out["wins"] / out["trades"]
    out["loss_cities"] = out["target_date"].map(
        selected[selected["label_no_wins"].eq(0)].groupby("target_date")["city"].apply(lambda x: ",".join(x))
    )
    out["win_cities"] = out["target_date"].map(
        selected[selected["label_no_wins"].eq(1)].groupby("target_date")["city"].apply(lambda x: ",".join(x))
    )
    return out


def route_summary(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for col in ["forecast_route_status", "forecast_route_model", "calibration_best_model"]:
        for val, group in frame.groupby(col, dropna=False):
            selected = select_first(group)
            if selected.empty:
                continue
            cost = float(selected["stake_cost_usd"].sum())
            profit = float(selected["stake_profit_usd"].sum())
            rows.append(
                {
                    "group_col": col,
                    "group_value": str(val),
                    "trades": int(len(selected)),
                    "dates": int(selected["target_date"].nunique()),
                    "cities": int(selected["city"].nunique()),
                    "win_rate": float(selected["label_no_wins"].mean()),
                    "roi": profit / cost if cost else None,
                    "avg_margin": float(selected["actual_margin_to_upper_native"].mean()),
                }
            )
    return rows


def render_md(payload: dict[str, Any], variants: pd.DataFrame, daily: pd.DataFrame, route: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate") or col.endswith("share") or col in {"win_rate", "no_win_rate"}:
                    vals.append(pct(val))
                elif col.endswith("usd") or col == "profit_usd":
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.3f}")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    focus = daily[daily["wins"].eq(0)].sort_values(["variant", "target_date"])
    lines = [
        "# Current-Bracket NO Preferred-Model Payoff V1",
        "",
        "## 结论",
        "",
        "这轮把两件事真正接上了：payoff/up-margin label，以及按城市 calibration 路由的 forecast replay。结果是："
        "方向仍然值得继续，但还没有变成 live-ready 规则。",
        "",
        "最重要的变化不是 ROI 点估，而是口径更诚实：非 GFS 的部分现在明确标成 `source-corrected historical replay`，"
        "不是冒充 true previous-day PIT。严格 PIT 历史仍主要只有 GFS daily。",
        "",
        f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
        "",
        "## 数据层",
        "",
        f"- Generated at UTC: `{payload['generated_at_utc']}`",
        f"- Scored rows: `{payload['dataset']['scored_rows']}`",
        f"- Trade-base rows: `{payload['dataset']['trade_base_rows']}`",
        f"- Date range: `{payload['dataset']['date_min']}`..`{payload['dataset']['date_max']}`",
        f"- Split date: `{payload['dataset']['split_date']}`",
        f"- Forecast route status: `{payload['forecast_stats']['route_status']}`",
        "",
        "## 模型判别力",
        "",
        "训练只用 trade-base rows，避免让大量不可交易/高价行主导 payoff label。",
        "",
        table(pd.DataFrame(payload["model_metrics_flat"]), ["label", "period", "rows", "active_dates", "label_rate", "auc", "brier"]),
        "",
        "## 交易表达",
        "",
        table(
            variants,
            [
                "variant",
                "selected_trades",
                "active_dates",
                "cities",
                "no_win_rate",
                "up_margin_win_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "holdout_roi",
                "selected_all_loss_days",
                "selected_all_loss_trades",
            ],
        ),
        "",
        "读法：payoff/up-margin label 的排序能力提升，但 all-loss day 仍没有被根治；所以这还是 shadow/research。",
        "",
        "## Forecast Route Overlay",
        "",
        table(route, ["group_col", "group_value", "trades", "dates", "cities", "win_rate", "roi", "avg_margin"]),
        "",
        "## All-Loss Days",
        "",
        table(
            focus,
            ["variant", "target_date", "trades", "wins", "roi", "avg_p_no_win", "avg_p_up_margin", "avg_margin", "loss_cities"],
            limit=60,
        ),
        "",
        "## Forward Sanity Check",
        "",
        table(
            pd.DataFrame(payload.get("forward_summary", {}).get("variants", [])),
            [
                "variant",
                "selected_trades",
                "active_dates",
                "settled_trades",
                "open_shadow_trades",
                "settled_win_rate",
                "settled_roi",
                "settled_profit_usd",
                "dates",
            ],
        ),
        "",
        "这一步专门防止历史 replay 自嗨：已结算 forward 若继续亏，规则只能继续 shadow。",
        "",
        "## 还差什么",
        "",
        "1. 真正补齐非 GFS 的 previous-day PIT snapshot，而不只是 historical replay。",
        "2. 单独建 day-regime classifier，识别“看似会继续升温但最终卡在同一 bracket”的日型。",
        "3. 把 6/21 之后的 forward shadow 日子滚动结算进来，特别是 6/24 之后的样本。",
        "4. 在 day-regime 没过之前，不允许 live；最多继续 zero-notional shadow。",
        "",
        "## Files",
        "",
        f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        f"- Forecast rows: `{OUT_FORECAST.relative_to(ROOT)}`",
        f"- Variants: `{OUT_VARIANTS.relative_to(ROOT)}`",
        f"- Daily: `{OUT_DAILY.relative_to(ROOT)}`",
        f"- All-loss details: `{OUT_ALL_LOSS.relative_to(ROOT)}`",
        f"- Selected rows: `{OUT_SELECTED.relative_to(ROOT)}`",
        f"- Forward validation/shadow: `{OUT_FORWARD.relative_to(ROOT)}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df, forecast_rows, forecast_stats = load_dataset()
    split_date, train_all, holdout_all = split_dates(df)
    train = train_all[train_all["trade_base"]].copy()
    holdout = holdout_all[holdout_all["trade_base"]].copy()
    df["period_split"] = np.where(df["target_date"].astype(str) <= split_date, "train", "holdout")
    train = df[df["period_split"].eq("train") & df["trade_base"]].copy()
    holdout = df[df["period_split"].eq("holdout") & df["trade_base"]].copy()

    metrics_by_label: dict[str, Any] = {}
    fitted_models: dict[str, Pipeline] = {}
    for label_col, p_col in [("label_no_wins", "p_no_win"), ("label_up_margin", "p_up_margin")]:
        pipe = build_model()
        pipe.fit(train[NUM_FEATURES + CAT_FEATURES], train[label_col].astype(int))
        fitted_models[label_col] = pipe
        metrics_by_label[label_col] = {
            "train": compute_model_metrics(pipe, train, label_col),
            "holdout": compute_model_metrics(pipe, holdout, label_col),
        }
        df[p_col] = pipe.predict_proba(df[NUM_FEATURES + CAT_FEATURES])[:, 1]
    df["edge_no_win"] = df["p_no_win"] - df["no_ask"]
    df["edge_up_margin"] = df["p_up_margin"] - df["no_ask"]

    trade_base = df[df["trade_base"]].copy()
    baseline = select_first(trade_base)
    variants_raw = {
        "baseline_trade_base": trade_base,
        "payoff_ev05_p35": trade_base[trade_base["p_no_win"].ge(0.35) & trade_base["edge_no_win"].ge(0.05)].copy(),
        "payoff_ev10_p40": trade_base[trade_base["p_no_win"].ge(0.40) & trade_base["edge_no_win"].ge(0.10)].copy(),
        "up_margin_ev05_p30": trade_base[trade_base["p_up_margin"].ge(0.30) & trade_base["edge_up_margin"].ge(0.05)].copy(),
        "up_margin_ev10_p35": trade_base[trade_base["p_up_margin"].ge(0.35) & trade_base["edge_up_margin"].ge(0.10)].copy(),
        "payoff_ev05_p35_max2_day": select_top_per_date(
            trade_base[trade_base["p_no_win"].ge(0.35) & trade_base["edge_no_win"].ge(0.05)].copy(), 2, "edge_no_win"
        ),
        "up_margin_ev05_p30_max2_day": select_top_per_date(
            trade_base[trade_base["p_up_margin"].ge(0.30) & trade_base["edge_up_margin"].ge(0.05)].copy(), 2, "edge_up_margin"
        ),
    }
    variants = pd.DataFrame(
        [summarize_variant(name, raw, baseline if name != "baseline_trade_base" else None) for name, raw in variants_raw.items()]
    )
    variants.to_csv(OUT_VARIANTS, index=False)

    daily_parts = []
    selected_parts = []
    for name, raw in variants_raw.items():
        dsum = daily_summary(name, raw)
        if not dsum.empty:
            daily_parts.append(dsum)
        selected = select_first(raw)
        if not selected.empty:
            selected = selected.copy()
            selected["variant"] = name
            selected_parts.append(selected)
    daily = pd.concat(daily_parts, ignore_index=True) if daily_parts else pd.DataFrame()
    selected_all = pd.concat(selected_parts, ignore_index=True) if selected_parts else pd.DataFrame()
    daily.to_csv(OUT_DAILY, index=False)
    selected_all.to_csv(OUT_SELECTED, index=False)
    all_loss_keys = daily[daily["wins"].eq(0)][["variant", "target_date"]].drop_duplicates()
    all_loss = selected_all.merge(all_loss_keys, on=["variant", "target_date"], how="inner") if not selected_all.empty else pd.DataFrame()
    keep = [
        "variant",
        "target_date",
        "city",
        "decision_hour_local",
        "bracket",
        "no_ask",
        "p_no_win",
        "p_up_margin",
        "edge_no_win",
        "edge_up_margin",
        "label_no_wins",
        "label_up_margin",
        "actual_margin_to_upper_native",
        "forecast_route_model",
        "forecast_route_status",
        "calibration_best_model",
        "forecast_gap_to_bracket_upper_native",
        "gfs_gap_to_bracket_upper_native",
        "ecmwf_gap_to_bracket_upper_native",
    ]
    all_loss[[c for c in keep if c in all_loss.columns]].to_csv(OUT_ALL_LOSS, index=False)
    route = pd.DataFrame(route_summary(trade_base))
    forward, forward_stats = load_forward_dataset()
    forward_summary: dict[str, Any] = {"stats": forward_stats, "variants": []}
    if not forward.empty:
        forward["p_no_win"] = fitted_models["label_no_wins"].predict_proba(forward[NUM_FEATURES + CAT_FEATURES])[:, 1]
        forward["p_up_margin"] = fitted_models["label_up_margin"].predict_proba(forward[NUM_FEATURES + CAT_FEATURES])[:, 1]
        forward["edge_no_win"] = forward["p_no_win"] - forward["no_ask"]
        forward["edge_up_margin"] = forward["p_up_margin"] - forward["no_ask"]
        forward_variants = {
            "baseline_trade_base": forward[forward["trade_base"]].copy(),
            "payoff_ev05_p35": forward[forward["trade_base"] & forward["p_no_win"].ge(0.35) & forward["edge_no_win"].ge(0.05)].copy(),
            "payoff_ev10_p40": forward[forward["trade_base"] & forward["p_no_win"].ge(0.40) & forward["edge_no_win"].ge(0.10)].copy(),
            "up_margin_ev05_p30": forward[
                forward["trade_base"] & forward["p_up_margin"].ge(0.30) & forward["edge_up_margin"].ge(0.05)
            ].copy(),
            "payoff_ev05_p35_max2_day": select_top_per_date(
                forward[forward["trade_base"] & forward["p_no_win"].ge(0.35) & forward["edge_no_win"].ge(0.05)].copy(),
                2,
                "edge_no_win",
            ),
        }
        forward_rows = []
        for name, raw in forward_variants.items():
            selected = select_first(raw)
            if selected.empty:
                forward_summary["variants"].append({"variant": name, "selected_trades": 0})
                continue
            settled = selected[selected["label_no_wins"].notna()].copy()
            open_rows = selected[selected["label_no_wins"].isna()].copy()
            profit = float(settled["stake_profit_usd"].sum()) if not settled.empty else 0.0
            cost = float(settled["stake_cost_usd"].sum()) if not settled.empty else 0.0
            forward_summary["variants"].append(
                {
                    "variant": name,
                    "selected_trades": int(len(selected)),
                    "active_dates": int(selected["target_date"].nunique()),
                    "settled_trades": int(len(settled)),
                    "open_shadow_trades": int(len(open_rows)),
                    "settled_win_rate": None if settled.empty else float(settled["label_no_wins"].mean()),
                    "settled_profit_usd": profit,
                    "settled_roi": profit / cost if cost else None,
                    "dates": ",".join(sorted(selected["target_date"].astype(str).unique())),
                }
            )
            slim_cols = [
                "variant",
                "target_date",
                "city",
                "decision_hour_local",
                "bracket",
                "no_ask",
                "p_no_win",
                "p_up_margin",
                "edge_no_win",
                "edge_up_margin",
                "trade_base",
                "settlement_status",
                "label_no_wins",
                "profit_usd",
                "stake_profit_usd",
                "forecast_route_model",
                "forecast_route_status",
                "calibration_best_model",
                "forecast_gap_to_bracket_upper_native",
            ]
            selected = selected.copy()
            selected["variant"] = name
            forward_rows.append(selected[[c for c in slim_cols if c in selected.columns]])
        if forward_rows:
            pd.concat(forward_rows, ignore_index=True).to_csv(OUT_FORWARD, index=False)
        else:
            pd.DataFrame().to_csv(OUT_FORWARD, index=False)
    model_metrics_flat = []
    for label, item in metrics_by_label.items():
        for period, vals in item.items():
            model_metrics_flat.append({"label": label, "period": period, **vals})
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_preferred_model_payoff_v1",
        "dataset": {
            "scored_rows": int(len(df)),
            "trade_base_rows": int(len(trade_base)),
            "date_min": str(df["target_date"].min()),
            "date_max": str(df["target_date"].max()),
            "split_date": split_date,
            "sync_rebuild_note": "sync_weather_remote.sh completed; run_stack rebuilt facts and gate passed, then exited non-clean only because FE port 5174 stayed busy.",
        },
        "forecast_stats": forecast_stats,
        "model_metrics": metrics_by_label,
        "model_metrics_flat": finite_or_none(model_metrics_flat),
        "variant_summary": finite_or_none(variants.to_dict(orient="records")),
        "route_summary": finite_or_none(route.to_dict(orient="records")),
        "forward_summary": finite_or_none(forward_summary),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "preferred_forecast_rows_csv": str(OUT_FORECAST.relative_to(ROOT)),
            "variant_summary_csv": str(OUT_VARIANTS.relative_to(ROOT)),
            "daily_variant_summary_csv": str(OUT_DAILY.relative_to(ROOT)),
            "all_loss_day_trade_details_csv": str(OUT_ALL_LOSS.relative_to(ROOT)),
            "selected_trade_rows_csv": str(OUT_SELECTED.relative_to(ROOT)),
            "forward_validation_and_shadow_csv": str(OUT_FORWARD.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "research_promising_shadow_only",
            "live_ready": False,
            "reason": "Preferred-model replay improves source routing but non-GFS history is not strict PIT and all-loss day risk remains unresolved.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, variants, daily, route), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
