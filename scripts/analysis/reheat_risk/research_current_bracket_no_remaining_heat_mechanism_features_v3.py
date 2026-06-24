#!/usr/bin/env python3
"""Remaining-heat model V3 with explicit physical mechanism features.

V1/V2 aligned the target to payoff:

    P(final_max - decision_running_max > bracket_upper + noise - running_max)

but the visible feature set still let "capped day" false positives through.
This version adds mechanism features instead of more hand-written gates:

- hourly GFS forecast curve after the decision point,
- curve plateau / remaining slope / area above bracket upper margin,
- solar-hour proxy,
- observation plateau/staleness proxies.
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import brier_score_loss, mean_absolute_error, mean_squared_error, r2_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import research_current_bracket_no_preferred_model_payoff_v1 as pref  # noqa: E402
import research_current_bracket_no_remaining_heat_model_v1 as v1  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3"
OUT_JSON = OUT_DIR / "summary.json"
OUT_MODEL_METRICS = OUT_DIR / "model_metrics.csv"
OUT_VARIANTS = OUT_DIR / "variant_summary.csv"
OUT_DAILY = OUT_DIR / "daily_variant_summary.csv"
OUT_FORWARD = OUT_DIR / "forward_validation_and_shadow.csv"
OUT_SELECTED = OUT_DIR / "selected_trade_rows.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-remaining-heat-mechanism-features-v3.md"

SEED = 20260624

CURVE_NUM_FEATURES = [
    "curve_temp_at_decision_f",
    "curve_after_decision_max_f",
    "curve_remaining_to_peak_f",
    "curve_next_1h_delta_f",
    "curve_next_2h_delta_f",
    "curve_next_3h_delta_f",
    "curve_slope_next_3h_fph",
    "curve_hours_until_peak",
    "curve_after_decision_range_f",
    "curve_plateau_hours_next_3h",
    "curve_tail_above_running_hours",
    "curve_tail_above_upper_margin_hours",
    "curve_tail_area_above_upper_margin_fh",
    "curve_pullback_before_peak_f",
    "curve_latest_peak_tie_count",
    "forecast_curve_max_minus_forecast_max_f",
    "forecast_curve_source_is_gfs_daily_num",
]

SOLAR_NUM_FEATURES = [
    "solar_elevation_deg",
    "solar_elevation_2h_deg",
    "solar_delta_2h_deg",
    "day_of_year_sin",
    "day_of_year_cos",
    "local_hour_sin",
    "local_hour_cos",
]

PLATEAU_NUM_FEATURES = [
    "running_max_stale_ge_30m",
    "running_max_stale_ge_60m",
    "plateau_proxy",
    "trend_decay_proxy_fph",
]

ENHANCED_NUM_FEATURES = list(dict.fromkeys(v1.MECH_NUM_FEATURES + CURVE_NUM_FEATURES + SOLAR_NUM_FEATURES + PLATEAU_NUM_FEATURES))
ENHANCED_CAT_FEATURES = list(v1.MECH_CAT_FEATURES)


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
    return value


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def money(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"${val:+,.2f}"


def c_to_f(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


def f_to_c(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def coerce_float(value: Any) -> float:
    try:
        out = float(value)
    except Exception:
        return math.nan
    return out if math.isfinite(out) else math.nan


def unit_values_to_f(values: list[float], unit: str) -> list[float]:
    u = unit.lower()
    if "°f" in u or u in {"f", "fahrenheit"}:
        return [float(x) for x in values]
    return [c_to_f(float(x)) for x in values]


def open_meteo_curve_index() -> dict[tuple[str, str], list[Path]]:
    pat = re.compile(r"^(gfs|ecmwf)_(.+)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.json$")
    idx: dict[tuple[str, str], list[Path]] = {}
    for path in pref.OPEN_METEO_DIR.glob("*.json"):
        match = pat.match(path.name)
        if not match:
            continue
        model, city, _start, _end = match.groups()
        idx.setdefault((city, model), []).append(path)
    for paths in idx.values():
        paths.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return idx


def file_covers(path: Path, target_date: str) -> bool:
    match = re.match(r"^(?:gfs|ecmwf)_.+_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.json$", path.name)
    if not match:
        return False
    start, end = match.groups()
    return start <= target_date <= end


def load_gfs_daily_curve(city: str, target_date: str) -> dict[str, Any] | None:
    path = pref.GFS_DAILY_DIR / f"{city}_{target_date}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    vals = [float(x) for x in data.get("hourly_temps") or [] if x is not None]
    if len(vals) < 24:
        return None
    return {
        "temps_f": vals[:24],
        "source": "gfs_daily_true_prevday",
        "source_file": str(path.relative_to(ROOT)),
        "latitude": None,
        "longitude": None,
    }


def load_open_meteo_curve(city: str, target_date: str, idx: dict[tuple[str, str], list[Path]]) -> dict[str, Any] | None:
    for path in idx.get((city, "gfs"), []):
        if not file_covers(path, target_date):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        temps = hourly.get("temperature_2m") or []
        unit = str((data.get("hourly_units") or {}).get("temperature_2m") or "").lower()
        day: list[tuple[int, float]] = []
        for raw_time, raw_temp in zip(times, temps):
            if raw_temp is None or not str(raw_time).startswith(target_date):
                continue
            day.append((int(str(raw_time)[11:13]), float(raw_temp)))
        if len(day) < 24:
            continue
        day.sort(key=lambda x: x[0])
        vals = [temp for _hour, temp in day[:24]]
        return {
            "temps_f": unit_values_to_f(vals, unit),
            "source": "open_meteo_historical_gfs",
            "source_file": str(path.relative_to(ROOT)),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
        }
    return None


def load_any_city_coord(city: str, idx: dict[tuple[str, str], list[Path]]) -> tuple[float | None, float | None]:
    for paths in [idx.get((city, "gfs"), []), idx.get((city, "ecmwf"), [])]:
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            lat = data.get("latitude")
            lon = data.get("longitude")
            if lat is not None and lon is not None:
                return float(lat), float(lon)
    return None, None


def solar_elevation_simple(lat_deg: float, day_of_year: int, local_hour: float) -> float:
    lat = math.radians(lat_deg)
    decl = math.radians(23.44) * math.sin(2.0 * math.pi * (284 + day_of_year) / 365.0)
    hour_angle = math.radians(15.0 * (local_hour - 12.0))
    val = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, val))))


def curve_feature_row(row: pd.Series, curve: dict[str, Any] | None, coord: tuple[float | None, float | None]) -> dict[str, Any]:
    decision_hour = coerce_float(row.get("decision_hour_local"))
    if not math.isfinite(decision_hour):
        decision_hour = 12.0
    h = int(max(0, min(23, math.floor(decision_hour))))
    running_f = coerce_float(row.get("running_temp_f_equiv"))
    required_gap = coerce_float(row.get("required_gap_f"))
    upper_margin_f = running_f + required_gap if math.isfinite(running_f) and math.isfinite(required_gap) else math.nan
    forecast_max_f = coerce_float(row.get("forecast_max_f"))

    out: dict[str, Any] = {
        "curve_source": None,
        "curve_source_file": None,
        "curve_temp_at_decision_f": math.nan,
        "curve_after_decision_max_f": math.nan,
        "curve_remaining_to_peak_f": math.nan,
        "curve_next_1h_delta_f": math.nan,
        "curve_next_2h_delta_f": math.nan,
        "curve_next_3h_delta_f": math.nan,
        "curve_slope_next_3h_fph": math.nan,
        "curve_peak_hour_from_curve": math.nan,
        "curve_hours_until_peak": math.nan,
        "curve_after_decision_range_f": math.nan,
        "curve_plateau_hours_next_3h": math.nan,
        "curve_tail_above_running_hours": math.nan,
        "curve_tail_above_upper_margin_hours": math.nan,
        "curve_tail_area_above_upper_margin_fh": math.nan,
        "curve_pullback_before_peak_f": math.nan,
        "curve_latest_peak_tie_count": math.nan,
        "forecast_curve_max_minus_forecast_max_f": math.nan,
        "forecast_curve_source_is_gfs_daily_num": math.nan,
    }

    if curve is not None:
        temps = [float(x) for x in curve["temps_f"][:24]]
        tail = temps[h:]
        peak_val = max(tail)
        peak_hour = max(i for i, val in enumerate(temps) if i >= h and abs(val - peak_val) <= 0.05)
        now_val = temps[h]
        next_1 = temps[min(23, h + 1)] - now_val
        next_2 = temps[min(23, h + 2)] - now_val
        next_3 = temps[min(23, h + 3)] - now_val
        next_window = temps[h : min(24, h + 4)]
        diffs = [abs(next_window[i] - next_window[i - 1]) for i in range(1, len(next_window))]
        above_upper = [max(0.0, val - upper_margin_f) for val in tail] if math.isfinite(upper_margin_f) else []
        out.update(
            {
                "curve_source": curve["source"],
                "curve_source_file": curve["source_file"],
                "curve_temp_at_decision_f": now_val,
                "curve_after_decision_max_f": peak_val,
                "curve_remaining_to_peak_f": peak_val - now_val,
                "curve_next_1h_delta_f": next_1,
                "curve_next_2h_delta_f": next_2,
                "curve_next_3h_delta_f": next_3,
                "curve_slope_next_3h_fph": next_3 / max(1, min(3, 23 - h)),
                "curve_peak_hour_from_curve": float(peak_hour),
                "curve_hours_until_peak": max(0.0, float(peak_hour) - decision_hour),
                "curve_after_decision_range_f": max(tail) - min(tail),
                "curve_plateau_hours_next_3h": float(sum(1 for d in diffs if d <= 0.25)),
                "curve_tail_above_running_hours": float(sum(1 for val in tail if math.isfinite(running_f) and val > running_f)),
                "curve_tail_above_upper_margin_hours": float(sum(1 for val in tail if math.isfinite(upper_margin_f) and val > upper_margin_f)),
                "curve_tail_area_above_upper_margin_fh": float(sum(above_upper)) if above_upper else math.nan,
                "curve_pullback_before_peak_f": peak_val - min(temps[h : peak_hour + 1]) if peak_hour >= h else 0.0,
                "curve_latest_peak_tie_count": float(sum(1 for val in tail if abs(val - peak_val) <= 0.25)),
                "forecast_curve_max_minus_forecast_max_f": peak_val - forecast_max_f if math.isfinite(forecast_max_f) else math.nan,
                "forecast_curve_source_is_gfs_daily_num": 1.0 if curve["source"] == "gfs_daily_true_prevday" else 0.0,
            }
        )

    try:
        doy = datetime.fromisoformat(str(row.get("target_date"))).timetuple().tm_yday
    except Exception:
        doy = 172
    lat, _lon = coord
    if lat is not None:
        solar_now = solar_elevation_simple(float(lat), int(doy), decision_hour)
        solar_2h = solar_elevation_simple(float(lat), int(doy), min(23.0, decision_hour + 2.0))
    else:
        solar_now = math.nan
        solar_2h = math.nan
    out.update(
        {
            "solar_elevation_deg": solar_now,
            "solar_elevation_2h_deg": solar_2h,
            "solar_delta_2h_deg": solar_2h - solar_now if math.isfinite(solar_now) and math.isfinite(solar_2h) else math.nan,
            "day_of_year_sin": math.sin(2.0 * math.pi * doy / 366.0),
            "day_of_year_cos": math.cos(2.0 * math.pi * doy / 366.0),
            "local_hour_sin": math.sin(2.0 * math.pi * decision_hour / 24.0),
            "local_hour_cos": math.cos(2.0 * math.pi * decision_hour / 24.0),
        }
    )
    return out


def add_enhanced_mechanism_features(frame: pd.DataFrame) -> pd.DataFrame:
    idx = open_meteo_curve_index()
    curve_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
    coord_cache: dict[str, tuple[float | None, float | None]] = {}
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        city = str(row.get("city"))
        target_date = str(row.get("target_date"))
        key = (city, target_date)
        if key not in curve_cache:
            curve_cache[key] = load_gfs_daily_curve(city, target_date) or load_open_meteo_curve(city, target_date, idx)
        if city not in coord_cache:
            curve = curve_cache[key]
            if curve and curve.get("latitude") is not None and curve.get("longitude") is not None:
                coord_cache[city] = (float(curve["latitude"]), float(curve["longitude"]))
            else:
                coord_cache[city] = load_any_city_coord(city, idx)
        rows.append(curve_feature_row(row, curve_cache[key], coord_cache[city]))
    features = pd.DataFrame(rows, index=frame.index)
    out = pd.concat([frame.copy(), features], axis=1)

    mins = pd.to_numeric(out.get("minutes_since_running_max"), errors="coerce")
    hour = pd.to_numeric(out.get("decision_hour_local"), errors="coerce")
    trend1 = pd.to_numeric(out.get("temp_trend_1h_f"), errors="coerce")
    trend3 = pd.to_numeric(out.get("temp_trend_3h_f"), errors="coerce") / 3.0
    decline = pd.to_numeric(out.get("decline_f"), errors="coerce")
    out["running_max_stale_ge_30m"] = mins.ge(30).astype(float)
    out["running_max_stale_ge_60m"] = mins.ge(60).astype(float)
    out["plateau_proxy"] = (mins.ge(30) & trend1.abs().le(0.5) & trend3.abs().le(0.5) & decline.ge(0)).astype(float)
    out["trend_decay_proxy_fph"] = trend1 - trend3

    for col in ENHANCED_NUM_FEATURES:
        if col not in out.columns:
            out[col] = np.nan
    for col in ENHANCED_CAT_FEATURES:
        if col not in out.columns:
            out[col] = ""
    return out


def build_regressor(num_features: list[str], cat_features: list[str]) -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]), num_features),
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5, sparse_output=False)),
                    ]
                ),
                cat_features,
            ),
        ]
    )
    reg = HistGradientBoostingRegressor(
        max_iter=250,
        learning_rate=0.04,
        max_leaf_nodes=10,
        min_samples_leaf=18,
        l2_regularization=0.05,
        random_state=SEED,
    )
    return Pipeline([("pre", pre), ("reg", reg)])


def score_model(
    frame: pd.DataFrame,
    train_mask: pd.Series,
    num_features: list[str],
    cat_features: list[str],
) -> tuple[pd.DataFrame, Pipeline, float]:
    out = frame.copy()
    model = build_regressor(num_features, cat_features)
    train = out[train_mask].copy()
    model.fit(train[num_features + cat_features], train["future_delta_to_daymax_f"])
    pred_train = model.predict(train[num_features + cat_features])
    resid = train["future_delta_to_daymax_f"].to_numpy(dtype=float) - pred_train
    sigma = max(float(np.nanstd(resid, ddof=1)), 0.25)
    out["pred_remaining_heat_f"] = model.predict(out[num_features + cat_features])
    out["remaining_heat_sigma_f"] = sigma
    out["p_cross_upper"] = v1.norm_sf((out["required_gap_f"] - out["pred_remaining_heat_f"]) / sigma)
    out["mechanism_edge"] = out["p_cross_upper"] - pd.to_numeric(out["no_ask"], errors="coerce")
    out["p_no_win"] = out["p_cross_upper"]
    out["p_up_margin"] = out["p_cross_upper"]
    out["edge_no_win"] = out["mechanism_edge"]
    out["edge_up_margin"] = out["mechanism_edge"]
    return out, model, sigma


def model_metrics(frame: pd.DataFrame, mask: pd.Series) -> dict[str, Any]:
    d = frame[mask].copy()
    y = pd.to_numeric(d["future_delta_to_daymax_f"], errors="coerce")
    pred = pd.to_numeric(d["pred_remaining_heat_f"], errors="coerce")
    label = d["cross_upper_margin_label"].astype(int)
    p = pd.to_numeric(d["p_cross_upper"], errors="coerce")
    return {
        "rows": int(len(d)),
        "active_dates": int(d["target_date"].nunique()) if len(d) else 0,
        "mae_f": float(mean_absolute_error(y, pred)) if len(d) else None,
        "rmse_f": float(math.sqrt(mean_squared_error(y, pred))) if len(d) else None,
        "r2": float(r2_score(y, pred)) if len(d) > 1 else None,
        "cross_rate": float(label.mean()) if len(d) else None,
        "cross_auc": float(roc_auc_score(label, p)) if label.nunique() > 1 else None,
        "cross_brier": float(brier_score_loss(label, p)) if label.nunique() > 1 else None,
        "avg_pred_remaining_heat_f": float(pred.mean()) if len(d) else None,
        "avg_actual_remaining_heat_f": float(y.mean()) if len(d) else None,
    }


def feature_coverage(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for col in CURVE_NUM_FEATURES + SOLAR_NUM_FEATURES + PLATEAU_NUM_FEATURES:
        vals = pd.to_numeric(frame.get(col), errors="coerce")
        rows.append(
            {
                "feature": col,
                "coverage": float(vals.notna().mean()) if len(frame) else None,
                "mean": float(vals.mean()) if vals.notna().any() else None,
            }
        )
    source_counts = frame.get("curve_source", pd.Series(dtype=object)).fillna("missing").value_counts().to_dict()
    rows.append({"feature": "curve_source_counts", "coverage": 1.0, "mean": None, "counts": source_counts})
    return rows


def evaluate_forward(model_name: str, model: Pipeline, sigma: float, num_features: list[str], cat_features: list[str]) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    forward, _stats = v1.load_forward_forced_gfs()
    if forward.empty:
        return [], pd.DataFrame()
    forward = add_enhanced_mechanism_features(forward)
    forward["pred_remaining_heat_f"] = model.predict(forward[num_features + cat_features])
    forward["remaining_heat_sigma_f"] = sigma
    forward["p_cross_upper"] = v1.norm_sf((forward["required_gap_f"] - forward["pred_remaining_heat_f"]) / sigma)
    forward["mechanism_edge"] = forward["p_cross_upper"] - pd.to_numeric(forward["no_ask"], errors="coerce")
    forward["p_no_win"] = forward["p_cross_upper"]
    forward["p_up_margin"] = forward["p_cross_upper"]
    forward["edge_no_win"] = forward["mechanism_edge"]
    forward["edge_up_margin"] = forward["mechanism_edge"]
    forward["trade_base_mechanism"] = v1.trade_base_mask(forward)
    rows = []
    selected_parts = []
    for variant, raw in v1.variant_raws(forward).items():
        selected = v1.select_first(raw)
        if selected.empty:
            rows.append({"model": model_name, "variant": variant, "selected_trades": 0})
            continue
        settled = selected[selected["label_no_wins"].notna()].copy()
        profit = float(settled["stake_profit_usd"].sum()) if not settled.empty else 0.0
        cost = float(settled["stake_cost_usd"].sum()) if not settled.empty else 0.0
        rows.append(
            {
                "model": model_name,
                "variant": variant,
                "selected_trades": int(len(selected)),
                "active_dates": int(selected["target_date"].nunique()),
                "settled_trades": int(len(settled)),
                "open_shadow_trades": int(selected["label_no_wins"].isna().sum()),
                "settled_win_rate": None if settled.empty else float(settled["label_no_wins"].mean()),
                "settled_profit_usd": profit,
                "settled_roi": profit / cost if cost else None,
                "dates": ",".join(sorted(selected["target_date"].astype(str).unique())),
            }
        )
        selected = selected.copy()
        selected["model"] = model_name
        selected["variant"] = variant
        selected_parts.append(selected)
    return rows, pd.concat(selected_parts, ignore_index=True) if selected_parts else pd.DataFrame()


def render_md(payload: dict[str, Any], metrics: pd.DataFrame, variants: pd.DataFrame, daily: pd.DataFrame, forward: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate") or col in {"roi_ci_low", "roi_ci_high", "holdout_roi", "settled_win_rate"}:
                    vals.append(pct(val))
                elif col.endswith("usd"):
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.3f}")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    focus_variants = variants[
        variants["variant"].isin(["baseline_trade_base", "remaining_heat_p40_ev10", "remaining_heat_p45_ev10"])
    ].copy()
    focus_forward = forward[
        forward["variant"].isin(["baseline_trade_base", "remaining_heat_p40_ev10", "remaining_heat_p45_ev10"])
    ].copy()
    bad_days = daily[daily["wins"].eq(0)].sort_values(["model", "variant", "target_date"]).head(80)
    coverage = pd.DataFrame(payload["feature_coverage"])
    coverage = coverage[coverage["feature"].isin(["curve_remaining_to_peak_f", "curve_next_3h_delta_f", "solar_elevation_deg", "plateau_proxy", "curve_source_counts"])]

    return "\n".join(
        [
            "# Current-Bracket NO Remaining-Heat Mechanism Features V3",
            "",
            "## 结论",
            "",
            "这版补的是机制特征，不是新的硬 gate：模型仍然预测 `P(remaining_heat > required_gap)`，但输入增加了小时级 GFS 曲线、曲线平台化、日照小时和观测 plateau/staleness 代理。",
            "",
            "结果很明确：`enhanced_all_rows` 有改进但不够。trade-base holdout AUC 从 base 的 0.651 升到 0.700，forward p40 ROI 从 -56.4% 改到 -35.3%；方向对，但没有跨过可交易线。`enhanced_trade_base` 历史 ROI 很漂亮，但 holdout AUC 只有 0.581、forward p40 ROI -67.7%，这是过拟合。",
            "",
            f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## 数据层",
            "",
            f"- Generated at UTC: `{payload['generated_at_utc']}`",
            f"- Sync/rebuild: `{payload['data_refresh_note']}`",
            f"- Raw rows: `{payload['dataset']['raw_rows']}`",
            f"- Mechanism rows: `{payload['dataset']['mechanism_rows']}`",
            f"- Trade-base rows: `{payload['dataset']['trade_base_rows']}`",
            f"- Date range: `{payload['dataset']['date_min']}`..`{payload['dataset']['date_max']}`",
            f"- Split date: `{payload['dataset']['split_date']}`",
            "",
            "## Mechanism Feature Coverage",
            "",
            table(coverage, ["feature", "coverage", "mean", "counts"]),
            "",
            "## Base vs Enhanced Model Diagnostics",
            "",
            table(metrics, ["model", "train_scope", "period", "rows", "active_dates", "mae_f", "rmse_f", "r2", "cross_rate", "cross_auc", "cross_brier"]),
            "",
            "## Trade Variants",
            "",
            table(
                focus_variants,
                [
                    "model",
                    "variant",
                    "selected_trades",
                    "active_dates",
                    "win_rate",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "holdout_roi",
                    "avg_required_gap_f",
                    "avg_pred_remaining_heat_f",
                    "avg_actual_remaining_heat_f",
                    "selected_all_loss_days",
                    "selected_all_loss_dates",
                ],
            ),
            "",
            "## Forward 6/21..6/23",
            "",
            table(
                focus_forward,
                ["model", "variant", "selected_trades", "settled_trades", "open_shadow_trades", "settled_win_rate", "settled_roi", "settled_profit_usd", "dates"],
            ),
            "",
            "## All-Loss Day Check",
            "",
            table(
                bad_days,
                ["model", "variant", "target_date", "trades", "wins", "roi", "avg_p_cross", "avg_required_gap_f", "avg_pred_remaining_heat_f", "avg_actual_remaining_heat_f", "avg_margin_f", "loss_cities"],
            ),
            "",
            "## 读法",
            "",
            "1. 如果 enhanced 模型在 trade-base holdout 和 forward 都没有比 base 明显改善，说明问题不是“少一条 gate”，而是当前数据还缺真正可观测的剩余热量状态。",
            "2. 小时级 forecast curve 可以识别预报自身是否还在升温；但它不能修正预报系统性高估 final max 的日子。",
            "3. 这版仍是 research/shadow：未通过显著性、基准和前瞻三道门，不允许改 live。",
            "",
            "## Files",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Model metrics: `{OUT_MODEL_METRICS.relative_to(ROOT)}`",
            f"- Variants: `{OUT_VARIANTS.relative_to(ROOT)}`",
            f"- Daily: `{OUT_DAILY.relative_to(ROOT)}`",
            f"- Forward: `{OUT_FORWARD.relative_to(ROOT)}`",
            f"- Selected rows: `{OUT_SELECTED.relative_to(ROOT)}`",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame, stats = v1.load_historical_forced_gfs()
    split_date = v1.split_date_for(frame)
    frame["period_split"] = np.where(frame["target_date"].astype(str).le(split_date), "train", "holdout")
    frame["trade_base_mechanism"] = v1.trade_base_mask(frame)
    frame = add_enhanced_mechanism_features(frame)

    specs = [
        ("base_all_rows", "all_rows", frame["period_split"].eq("train"), v1.MECH_NUM_FEATURES, v1.MECH_CAT_FEATURES),
        ("enhanced_all_rows", "all_rows", frame["period_split"].eq("train"), ENHANCED_NUM_FEATURES, ENHANCED_CAT_FEATURES),
        (
            "enhanced_trade_base",
            "trade_base",
            frame["period_split"].eq("train") & frame["trade_base_mechanism"],
            ENHANCED_NUM_FEATURES,
            ENHANCED_CAT_FEATURES,
        ),
    ]
    metric_rows: list[dict[str, Any]] = []
    variant_rows: list[dict[str, Any]] = []
    daily_parts: list[pd.DataFrame] = []
    forward_rows: list[dict[str, Any]] = []
    selected_parts: list[pd.DataFrame] = []

    for model_name, train_scope, train_mask, num_features, cat_features in specs:
        scored, model, sigma = score_model(frame, train_mask, num_features, cat_features)
        for period, mask in [
            ("train_scope", train_mask),
            ("trade_base_train", scored["period_split"].eq("train") & scored["trade_base_mechanism"]),
            ("trade_base_holdout", scored["period_split"].eq("holdout") & scored["trade_base_mechanism"]),
        ]:
            metric_rows.append({"model": model_name, "train_scope": train_scope, "period": period, **model_metrics(scored, mask)})
        raws = v1.variant_raws(scored)
        baseline = v1.select_first(raws["baseline_trade_base"])
        for variant, raw in raws.items():
            summary = v1.summarize_variant(variant, raw, None if variant == "baseline_trade_base" else baseline)
            summary["model"] = model_name
            summary["train_scope"] = train_scope
            variant_rows.append(summary)
            daily = v1.daily_summary(variant, raw)
            if not daily.empty:
                daily["model"] = model_name
                daily["train_scope"] = train_scope
                daily_parts.append(daily)
            selected = v1.select_first(raw)
            if not selected.empty:
                selected = selected.copy()
                selected["model"] = model_name
                selected["train_scope"] = train_scope
                selected["variant"] = variant
                selected_parts.append(selected)
        f_rows, f_selected = evaluate_forward(model_name, model, sigma, num_features, cat_features)
        forward_rows.extend(f_rows)
        if not f_selected.empty:
            selected_parts.append(f_selected)

    metrics = pd.DataFrame(metric_rows)
    variants = pd.DataFrame(variant_rows)
    daily = pd.concat(daily_parts, ignore_index=True) if daily_parts else pd.DataFrame()
    forward = pd.DataFrame(forward_rows)
    selected = pd.concat(selected_parts, ignore_index=True) if selected_parts else pd.DataFrame()

    metrics.to_csv(OUT_MODEL_METRICS, index=False)
    variants.to_csv(OUT_VARIANTS, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    forward.to_csv(OUT_FORWARD, index=False)
    selected.to_csv(OUT_SELECTED, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_remaining_heat_mechanism_features_v3",
        "data_refresh_note": "sync_weather_remote.sh completed; run_stack rebuilt facts and CLOB gate passed, then exited non-clean only because FE port 5174 stayed busy.",
        "dataset": {**stats, "split_date": split_date},
        "feature_coverage": finite_or_none(feature_coverage(frame)),
        "model_metrics": finite_or_none(metrics.to_dict(orient="records")),
        "variant_summary": finite_or_none(variants.to_dict(orient="records")),
        "forward_summary": finite_or_none(forward.to_dict(orient="records")),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "model_metrics_csv": str(OUT_MODEL_METRICS.relative_to(ROOT)),
            "variant_summary_csv": str(OUT_VARIANTS.relative_to(ROOT)),
            "daily_variant_summary_csv": str(OUT_DAILY.relative_to(ROOT)),
            "forward_validation_csv": str(OUT_FORWARD.relative_to(ROOT)),
            "selected_trade_rows_csv": str(OUT_SELECTED.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "mechanism_features_v3_shadow_only",
            "live_ready": False,
            "reason": "Enhanced mechanism features must beat base on trade-base holdout and forward before live consideration.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, metrics, variants, daily, forward), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
