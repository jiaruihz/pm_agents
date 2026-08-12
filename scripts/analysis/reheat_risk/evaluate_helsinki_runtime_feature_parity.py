#!/usr/bin/env python3
"""Audit Helsinki runtime feature parity and frozen rich-vs-sparse sensitivity."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit, logit
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.helsinki_remaining_heat_features import (
    build_fmi_remaining_heat_features,
)


DEFAULT_FMI = Path("/Volumes/jrs/pm_agents/research/artifact_store/objects/a3/a3f4ff2f205b6804179f9889757398ac374ad8f09b5336ca391924b1a9cf98cf")
DEFAULT_AUDIT = Path("/Volumes/jrs/pm_agents/research/artifact_store/objects/fd/fd36db931b3077e921df426fee74187f389b9f38aafce2e5531701b121ebf614")
DEFAULT_MODEL = ROOT / "docs/analysis/2026-07/generated/helsinki_remaining_heat_probability_v5/helsinki_remaining_heat_v5_composite_hazard_challenger.joblib"
DEFAULT_OOF_2025 = Path("/Volumes/jrs/pm_agents/research/artifact_store/objects/e0/e0d9467bc2f630a8788ca373b39a4748415dff6385050937a738179fdecc0a2d")
DEFAULT_MARKET_OOF = Path("/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_first_principles_v6/oof_model/oof_checkpoint_predictions.csv.gz")
DEFAULT_MARKET_OPPORTUNITIES = Path("/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_live_readiness_case_audit_v1/evaluation/signal_opportunities_5share.csv")
DEFAULT_MARKET_LABELS = Path("/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_live_readiness_case_audit_v1/evaluation/probability_same_rows.csv")

SPARSE_RUNTIME_FEATURES = {
    "official_running_max_c", "fmi_running_max_c", "pullback_depth_c",
    "distance_to_next_official_boundary_c", "fmi_official_lattice_basis_c",
    "local_hour_sin", "local_hour_cos", "doy_sin", "doy_cos",
    "temp_delta_10m", "temp_delta_20m", "temp_slope_30m_cph",
    "temp_slope_60m_cph", "temp_acceleration_20m",
    "minutes_since_strict_high", "plateau_duration_min",
    "relative_humidity_pct", "dewpoint_depression_c", "wind_speed_ms",
    "pressure_hpa", "cloud_cover_okta", "present_weather_code",
    "source_cadence_gap_min", "source_to_official_level_basis_c",
    "forecast_available", "forecast_run_age_h", "forecast_current_innovation_c",
    "forecast_day_peak_margin_vs_running_c",
    "forecast_future_peak_margin_vs_running_c",
    "forecast_future_peak_margin_vs_boundary_c",
    "forecast_signed_minutes_to_day_peak", "forecast_minutes_to_future_peak",
    "forecast_future_heat_area_above_boundary",
    "forecast_future_hours_above_boundary", "forecast_future_cloud_mean_pct",
    "forecast_future_wind_mean_kmh", "forecast_day_peak_passed",
    "forecast_minutes_since_day_peak", "forecast_minutes_until_day_peak",
    "forecast_future_peak_discount_from_day_peak_c",
    "forecast_future_reheat_strength_c",
}

PARITY_FIELDS = (
    "fmi_running_max_c", "pullback_depth_c", "local_hour_sin", "local_hour_cos",
    "doy_sin", "doy_cos", "solar_elevation_deg", "temp_delta_10m",
    "temp_slope_30m_cph", "temp_slope_60m_cph", "warming_run_count",
    "minutes_since_strict_high", "global_radiation_wm2",
    "diffuse_radiation_wm2", "diffuse_fraction", "longwave_in_wm2",
    "longwave_out_wm2", "reflected_radiation_wm2",
    "radiation_balance_proxy_wm2", "sunshine_seconds",
    "global_radiation_delta_10m", "global_radiation_slope_30m",
    "global_radiation_mean_30m", "sunshine_mean_30m",
    "relative_humidity_pct", "dewpoint_depression_c", "wind_speed_ms",
    "wind_gust_ms", "wind_u_ms", "wind_v_ms", "pressure_hpa",
    "pressure_delta_30m", "precipitation_10m_mm", "precipitation_1h_mm",
    "visibility_m", "cloud_cover_okta", "present_weather_code",
)

CAP_COLUMNS = {
    0.05: "p_bounded_weather_market_c005",
    0.075: "p_bounded_weather_market_c0075",
    0.10: "p_bounded_weather_market_c010",
    0.15: "p_bounded_weather_market_c015",
    0.20: "p_bounded_weather_market_c020",
    0.25: "p_bounded_weather_market_c025",
    0.50: "p_bounded_weather_market_c050",
    1.00: "p_bounded_weather_market_c100",
    1.50: "p_bounded_weather_market_c150",
    2.00: "p_bounded_weather_market_c200",
}


def _raw_row(row: pd.Series) -> dict:
    speed, u, v = row.wind_speed_ms, row.wind_u_ms, row.wind_v_ms
    direction = None
    if pd.notna(speed) and speed and pd.notna(u) and pd.notna(v):
        direction = float(np.degrees(np.arctan2(-u, -v)) % 360)
    return {
        "observation_time_utc": row.observation_time_utc,
        "temp_c": row.temp_c,
        "relative_humidity_pct": row.relative_humidity_pct,
        "dewpoint_depression_c": row.dewpoint_depression_c,
        "wind_speed_ms": speed,
        "wind_gust_ms": row.wind_gust_ms,
        "wind_dir_deg": direction,
        "pressure_hpa": row.pressure_hpa,
        "precipitation_10m_mm": row.precipitation_10m_mm,
        "precipitation_1h_mm": row.precipitation_1h_mm,
        "visibility_m": row.visibility_m,
        "cloud_cover_okta": row.cloud_cover_okta,
        "present_weather_code": row.present_weather_code,
        "global_radiation_wm2": row.global_radiation_wm2,
        "diffuse_radiation_wm2": row.diffuse_radiation_wm2,
        "longwave_in_wm2": row.longwave_in_wm2,
        "longwave_out_wm2": row.longwave_out_wm2,
        "reflected_radiation_wm2": row.reflected_radiation_wm2,
        "sunshine_seconds": row.sunshine_seconds,
    }


def parity_audit(fmi: pd.DataFrame, sample_dates: int) -> dict:
    dates = sorted(fmi.target_date.astype(str).unique())
    positions = np.linspace(0, len(dates) - 1, min(sample_dates, len(dates)), dtype=int)
    chosen = {dates[index] for index in positions}
    frame = fmi.loc[fmi.target_date.astype(str).isin(chosen)].copy()
    comparisons = 0
    mismatches: dict[str, dict[str, float]] = {}
    for _, day in frame.groupby("target_date", sort=False):
        history: list[dict] = []
        for _, row in day.sort_values("observation_time_utc").iterrows():
            history.append(_raw_row(row))
            features = build_fmi_remaining_heat_features(
                history, official_running_max_c=float(row.running_max_c)
            )
            expected = {
                **row.to_dict(),
                "fmi_running_max_c": row.running_max_c,
                "pullback_depth_c": row.running_max_c - row.temp_c,
            }
            for field in PARITY_FIELDS:
                left, right = features[field], expected[field]
                left_missing = left is None or pd.isna(left)
                right_missing = pd.isna(right)
                if left_missing and right_missing:
                    continue
                comparisons += 1
                error = math.inf if left_missing or right_missing else abs(float(left) - float(right))
                if not math.isfinite(error) or error > 1e-8:
                    item = mismatches.setdefault(field, {"count": 0, "max_abs_error": 0.0})
                    item["count"] += 1
                    item["max_abs_error"] = max(item["max_abs_error"], error)
    return {
        "sample_target_dates": len(chosen),
        "sample_rows": len(frame),
        "fields": len(PARITY_FIELDS),
        "finite_comparisons": comparisons,
        "mismatch_fields": mismatches,
        "parity_pass": not mismatches,
    }


def _derived_sparse_base(fmi: pd.DataFrame) -> pd.DataFrame:
    frame = fmi.copy().sort_values(["target_date", "observation_time_utc"])
    grouped = frame.groupby("target_date", sort=False)
    frame["fmi_running_max_c"] = frame.running_max_c
    frame["pullback_depth_c"] = frame.running_max_c - frame.temp_c
    frame["temp_delta_20m"] = frame.temp_c - grouped.temp_c.shift(2)
    frame["temp_acceleration_20m"] = frame.temp_delta_10m - grouped.temp_delta_10m.shift(1)
    frame["plateau_duration_min"] = frame.minutes_since_strict_high
    frame["observation_time_utc"] = pd.to_datetime(frame.observation_time_utc, utc=True)
    frame["source_cadence_gap_min"] = frame.groupby("target_date").observation_time_utc.diff().dt.total_seconds().div(60).fillna(10)
    return frame


def _derived_full_base(fmi: pd.DataFrame) -> pd.DataFrame:
    frame = _derived_sparse_base(fmi)
    grouped = frame.groupby("target_date", sort=False, group_keys=False)
    frame["recent_high_count_60m"] = (
        grouped.temp_c.rolling(7, min_periods=1)
        .apply(lambda values: float(np.sum(values >= np.nanmax(values) - 0.05)))
        .reset_index(level=0, drop=True).sort_index()
    )
    frame["path_volatility_60m"] = (
        grouped.temp_delta_10m.rolling(6, min_periods=1).std(ddof=0)
        .reset_index(level=0, drop=True).sort_index()
    )
    frame["global_radiation_mean_60m"] = (
        grouped.global_radiation_wm2.rolling(6, min_periods=1).mean()
        .reset_index(level=0, drop=True).sort_index()
    )
    frame["radiation_integral_30m"] = (
        grouped.global_radiation_wm2.rolling(3, min_periods=1).sum()
        .reset_index(level=0, drop=True).sort_index() / 6.0
    )
    frame["radiation_integral_60m"] = (
        grouped.global_radiation_wm2.rolling(6, min_periods=1).sum()
        .reset_index(level=0, drop=True).sort_index() / 6.0
    )
    for source, target in (
        ("relative_humidity_pct", "relative_humidity_delta_30m"),
        ("dewpoint_depression_c", "dewpoint_depression_delta_30m"),
        ("wind_speed_ms", "wind_speed_delta_30m"),
        ("cloud_cover_okta", "cloud_cover_delta_30m"),
    ):
        frame[target] = frame[source] - grouped[source].shift(3)
    same = frame.temp_c.eq(grouped.temp_c.shift(1))
    run_key = (~same).groupby(frame.target_date).cumsum()
    frame["same_value_run_count"] = frame.groupby(["target_date", run_key], sort=False).cumcount() + 1
    frame["distinct_print_count_60m"] = (
        grouped.temp_c.rolling(7, min_periods=1)
        .apply(lambda values: float(pd.Series(values).nunique()))
        .reset_index(level=0, drop=True).sort_index()
    )
    frame["single_print_state"] = frame.same_value_run_count.eq(1).astype(float)
    frame["remaining_daylight_proxy"] = np.maximum(0.0, frame.solar_elevation_deg)
    return frame


def _probability(model: dict, frame: pd.DataFrame) -> np.ndarray:
    matrix = frame.reindex(columns=model["features"]).to_numpy(dtype=float)
    hazards = np.column_stack(
        [estimator.predict_proba(matrix)[:, 1] for estimator in model["event_hazard_models"]]
    )
    return 1.0 - np.prod(1.0 - np.clip(hazards, 1e-9, 1 - 1e-9), axis=1)


def _metrics(frame: pd.DataFrame, probability: np.ndarray) -> dict:
    label = frame.label_break_eod.to_numpy(dtype=int)
    p = np.clip(probability, 1e-9, 1 - 1e-9)
    date_count = frame.target_date.astype(str).value_counts()
    weights = frame.target_date.astype(str).map(lambda value: 1.0 / date_count[value]).to_numpy()
    weights /= weights.sum()
    return {
        "brier_target_date_balanced": float(np.sum(weights * (p - label) ** 2)),
        "logloss_target_date_balanced": float(-np.sum(weights * (label * np.log(p) + (1 - label) * np.log(1 - p)))),
        "auc_rows": float(roc_auc_score(label, p)),
    }


def _date_block_bootstrap(
    frame: pd.DataFrame, full_probability: np.ndarray, sparse_probability: np.ndarray,
    *, iterations: int = 1000,
) -> dict:
    work = frame[["target_date", "label_break_eod"]].copy()
    work["full"] = full_probability
    work["sparse"] = sparse_probability
    per_date = []
    for target_date, day in work.groupby("target_date"):
        y = day.label_break_eod.to_numpy(dtype=float)
        full = np.clip(day["full"].to_numpy(dtype=float), 1e-9, 1 - 1e-9)
        sparse = np.clip(day["sparse"].to_numpy(dtype=float), 1e-9, 1 - 1e-9)
        per_date.append(
            (
                target_date,
                float(np.mean((full - y) ** 2) - np.mean((sparse - y) ** 2)),
                float(np.mean(-(y * np.log(full) + (1 - y) * np.log(1 - full))) - np.mean(-(y * np.log(sparse) + (1 - y) * np.log(1 - sparse)))),
            )
        )
    values = np.asarray([[row[1], row[2]] for row in per_date])
    rng = np.random.default_rng(20260812)
    draws = np.empty((iterations, 2))
    for index in range(iterations):
        sample = rng.integers(0, len(values), len(values))
        draws[index] = np.mean(values[sample], axis=0)
    return {
        "target_dates": len(values), "iterations": iterations,
        "brier_delta_rich_minus_sparse_ci95": [float(x) for x in np.quantile(draws[:, 0], [0.025, 0.975])],
        "logloss_delta_rich_minus_sparse_ci95": [float(x) for x in np.quantile(draws[:, 1], [0.025, 0.975])],
    }


def frozen_ablation_frame(
    fmi: pd.DataFrame, audit: pd.DataFrame, model: dict
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    base = _derived_full_base(fmi)
    keys = ["target_date", "observation_time_utc"]
    audit = audit.copy()
    audit["observation_time_utc"] = pd.to_datetime(audit.observation_time_utc, utc=True)
    frame = audit.merge(base, on=keys, how="inner", suffixes=("", "_fmi"))
    frame["distance_to_next_official_boundary_c"] = frame.official_running_max_c + 0.5 - frame.temp_c
    frame["fmi_official_lattice_basis_c"] = np.floor(frame.temp_c + 0.5) - frame.official_running_max_c
    frame["source_to_official_level_basis_c"] = frame.temp_c - frame.official_running_max_c
    frame["terminal_false_risk_proxy"] = (
        frame.fmi_official_lattice_basis_c.gt(0) & frame.single_print_state.eq(1)
    ).astype(float)
    rich = pd.DataFrame(np.nan, index=frame.index, columns=model["features"])
    for field in model["features"]:
        if field in frame:
            rich[field] = pd.to_numeric(frame[field], errors="coerce")
    sparse = pd.DataFrame(np.nan, index=frame.index, columns=model["features"])
    for field in SPARSE_RUNTIME_FEATURES:
        if field in frame:
            sparse[field] = pd.to_numeric(frame[field], errors="coerce")
    rich_probability = _probability(model, rich)
    sparse_probability = _probability(model, sparse)
    return frame, rich_probability, sparse_probability


def frozen_ablation(fmi: pd.DataFrame, audit: pd.DataFrame, model: dict) -> dict:
    frame, rich_probability, sparse_probability = frozen_ablation_frame(
        fmi, audit, model
    )
    full_metrics = _metrics(frame, rich_probability)
    sparse_metrics = _metrics(frame, sparse_probability)
    return {
        "denominator_scope": "frozen 2026 final audit rows, 2026-01-01..2026-07-29; same rows/model; forecast fields held missing in both arms to isolate FMI rich-feature effect",
        "rows": len(frame),
        "target_dates": int(frame.target_date.nunique()),
        "rich_fmi_contract_frozen_probability": full_metrics,
        "sparse_pre_fix_runtime_contract": sparse_metrics,
        "delta_rich_minus_sparse": {
            key: full_metrics[key] - sparse_metrics[key] for key in full_metrics
        },
        "sparse_declared_feature_count": len(SPARSE_RUNTIME_FEATURES),
        "model_feature_count": len(model["features"]),
        "target_date_block_bootstrap": _date_block_bootstrap(frame, rich_probability, sparse_probability),
        "tuning_status": "artifact-frozen; final-audit rows used once for deployment parity, not threshold/model selection",
    }


def _date_equal_metrics(
    frame: pd.DataFrame, probability: str, label: str = "label"
) -> dict:
    daily = []
    for _, day in frame.groupby("target_date"):
        y = day[label].to_numpy(dtype=float)
        p = np.clip(day[probability].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
        daily.append(
            {
                "brier": float(np.mean((p - y) ** 2)),
                "logloss": float(np.mean(-y * np.log(p) - (1 - y) * np.log(1 - p))),
                "bias": float(np.mean(p - y)),
            }
        )
    values = pd.DataFrame(daily)
    return {
        "rows": int(len(frame)),
        "target_dates": int(frame.target_date.nunique()),
        "brier": float(values.brier.mean()),
        "logloss": float(values.logloss.mean()),
        "calibration_bias": float(values.bias.mean()),
        "auc": (
            float(roc_auc_score(frame[label], frame[probability]))
            if frame[label].nunique() == 2
            else None
        ),
    }


def _fit_platt(frame: pd.DataFrame) -> dict[str, float]:
    raw = np.clip(frame.probability.to_numpy(dtype=float), 1e-6, 1 - 1e-6)
    x = logit(raw)
    y = frame.label.to_numpy(dtype=float)
    counts = frame.target_date.astype(str).value_counts()
    weights = frame.target_date.astype(str).map(lambda value: 1 / counts[value]).to_numpy()
    weights /= weights.sum()

    def objective(beta: np.ndarray) -> float:
        p = np.clip(expit(beta[0] + beta[1] * x), 1e-9, 1 - 1e-9)
        return float(-np.sum(weights * (y * np.log(p) + (1 - y) * np.log(1 - p))))

    fitted = minimize(objective, np.asarray([0.0, 1.0]), method="BFGS")
    if not fitted.success:
        raise RuntimeError(f"Platt calibration failed: {fitted.message}")
    return {"intercept": float(fitted.x[0]), "slope": float(fitted.x[1])}


def _apply_platt(probability: pd.Series | np.ndarray, calibration: dict[str, float]) -> np.ndarray:
    raw = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
    return expit(calibration["intercept"] + calibration["slope"] * logit(raw))


def _paired_date_bootstrap(
    frame: pd.DataFrame,
    challenger: str,
    baseline: str,
    *,
    label: str = "label",
    iterations: int = 5000,
) -> dict:
    per_date = []
    for target_date, day in frame.groupby("target_date"):
        y = day[label].to_numpy(dtype=float)
        p1 = np.clip(day[challenger].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
        p0 = np.clip(day[baseline].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
        per_date.append(
            {
                "target_date": target_date,
                "brier": float(np.mean((p1 - y) ** 2 - (p0 - y) ** 2)),
                "logloss": float(
                    np.mean(
                        -y * np.log(p1) - (1 - y) * np.log(1 - p1)
                        + y * np.log(p0) + (1 - y) * np.log(1 - p0)
                    )
                ),
            }
        )
    values = pd.DataFrame(per_date)
    rng = np.random.default_rng(20260812)
    sampled = rng.integers(0, len(values), size=(iterations, len(values)))
    output = {"target_dates": int(len(values)), "iterations": iterations}
    for metric in ("brier", "logloss"):
        raw = values[metric].to_numpy()
        draws = raw[sampled].mean(axis=1)
        output[metric] = {
            "delta": float(raw.mean()),
            "ci_low": float(np.quantile(draws, 0.025)),
            "ci_high": float(np.quantile(draws, 0.975)),
        }
    return output


def weather_robustness(
    oof: pd.DataFrame, audit_frame: pd.DataFrame, audit_probability: np.ndarray
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    development = oof.rename(
        columns={"label_break_eod": "label", "p_eod_composite": "probability"}
    ).copy()
    development["target_date"] = development.target_date.astype(str)
    audit = audit_frame.copy()
    audit["target_date"] = audit.target_date.astype(str)
    audit["label"] = audit.label_break_eod.astype(int)
    audit["probability"] = audit_probability
    slope = audit.temp_slope_30m_cph.fillna(0.0)
    pullback = audit.pullback_depth_c.fillna(0.0)
    audit["path_state"] = np.select(
        [
            slope.gt(0.3) & pullback.lt(0.2),
            pullback.le(0.1) & slope.abs().le(0.3),
            pullback.gt(0.1) & slope.ge(-0.3),
        ],
        ["fresh_runway", "plateau", "pullback"],
        default="fade",
    )
    audit["local_hour"] = audit.local_hour_fmi

    dates = sorted(development.target_date.unique())
    split_specs: list[tuple[str, list[str], list[str]]] = []
    for boundary in range(90, len(dates), 90):
        split_specs.append(
            (f"expanding_{boundary}", dates[:boundary], dates[boundary : boundary + 90])
        )
    months = pd.to_datetime(development.target_date).dt.month
    odd = sorted(development.loc[months.mod(2).eq(1), "target_date"].unique())
    even = sorted(development.loc[months.mod(2).eq(0), "target_date"].unique())
    split_specs.extend(
        [
            ("odd_month_to_even", odd, even),
            ("even_month_to_odd", even, odd),
            ("first_half_to_second", dates[:181], dates[181:]),
            ("second_half_to_first", dates[181:], dates[:181]),
        ]
    )
    split_rows = []
    for split, train_dates, validation_dates in split_specs:
        train = development.loc[development.target_date.isin(train_dates)]
        validation = development.loc[development.target_date.isin(validation_dates)].copy()
        if train.empty or validation.empty:
            continue
        calibration = _fit_platt(train)
        validation["calibrated"] = _apply_platt(validation.probability, calibration)
        raw = _date_equal_metrics(validation, "probability")
        calibrated = _date_equal_metrics(validation, "calibrated")
        split_rows.append(
            {
                "split": split,
                "train_dates": len(train_dates),
                "validation_dates": len(validation_dates),
                **calibration,
                "raw_brier": raw["brier"],
                "calibrated_brier": calibrated["brier"],
                "brier_delta": calibrated["brier"] - raw["brier"],
                "raw_logloss": raw["logloss"],
                "calibrated_logloss": calibrated["logloss"],
                "logloss_delta": calibrated["logloss"] - raw["logloss"],
            }
        )
    split_frame = pd.DataFrame(split_rows)
    # The frozen 2026 parity audit deliberately holds all forecast fields
    # missing.  Its calibration challenger must therefore be trained only on
    # the matching 2025 no-forecast arm; using all 2025 rows would mix a much
    # easier forecast-available regime and create a false apparent upgrade.
    matching_development = development.loc[
        development.forecast_available_x.eq(0)
    ].copy()
    selected_calibration = _fit_platt(matching_development)
    audit["calibrated_2025"] = _apply_platt(audit.probability, selected_calibration)

    slice_rows = []
    audit["hour_window"] = pd.cut(
        audit.local_hour,
        [-np.inf, 10, 14, 18, np.inf],
        labels=["00-10", "10-14", "14-18", "18-24"],
        right=False,
    ).astype(str)
    for dimension in ("hour_window", "path_state"):
        for value, group in audit.groupby(dimension):
            raw = _date_equal_metrics(group, "probability")
            calibrated = _date_equal_metrics(group, "calibrated_2025")
            slice_rows.append(
                {
                    "dimension": dimension,
                    "value": value,
                    "rows": len(group),
                    "target_dates": group.target_date.nunique(),
                    "event_rate": group.label.mean(),
                    "mean_probability": group.probability.mean(),
                    "calibration_gap": group.probability.mean() - group.label.mean(),
                    "accuracy_0p5": float(
                        np.mean(group.probability.ge(0.5).eq(group.label.eq(1)))
                    ),
                    "raw_brier": raw["brier"],
                    "raw_logloss": raw["logloss"],
                    "calibrated_brier": calibrated["brier"],
                    "calibrated_logloss": calibrated["logloss"],
                }
            )
    audit["probability_bin"] = pd.cut(
        audit.probability,
        np.linspace(0, 1, 11),
        include_lowest=True,
    ).astype(str)
    for value, group in audit.groupby("probability_bin", observed=True):
        raw = _date_equal_metrics(group, "probability")
        calibrated = _date_equal_metrics(group, "calibrated_2025")
        slice_rows.append(
            {
                "dimension": "probability_bin",
                "value": value,
                "rows": len(group),
                "target_dates": group.target_date.nunique(),
                "event_rate": group.label.mean(),
                "mean_probability": group.probability.mean(),
                "calibration_gap": group.probability.mean() - group.label.mean(),
                "accuracy_0p5": float(
                    np.mean(group.probability.ge(0.5).eq(group.label.eq(1)))
                ),
                "raw_brier": raw["brier"],
                "raw_logloss": raw["logloss"],
                "calibrated_brier": calibrated["brier"],
                "calibrated_logloss": calibrated["logloss"],
            }
        )
    high_confidence = {}
    for name, frame in (("oof_2025", development), ("audit_2026", audit)):
        high_confidence[name] = {}
        for threshold in (0.90, 0.95, 0.99):
            confident = frame.probability.ge(threshold) | frame.probability.le(1 - threshold)
            wrong = (
                (frame.probability.ge(threshold) & frame.label.eq(0))
                | (frame.probability.le(1 - threshold) & frame.label.eq(1))
            )
            high_confidence[name][str(threshold)] = {
                "confident_rows": int(confident.sum()),
                "wrong_rows": int(wrong.sum()),
                "wrong_target_dates": int(frame.loc[wrong, "target_date"].nunique()),
            }
    summary = {
        "development_oof_2025_all": _date_equal_metrics(development, "probability"),
        "development_oof_2025_matching_no_forecast": _date_equal_metrics(
            matching_development, "probability"
        ),
        "audit_2026_no_forecast_contract_raw": _date_equal_metrics(audit, "probability"),
        "audit_2026_calibrated_from_2025_matching_no_forecast": _date_equal_metrics(
            audit, "calibrated_2025"
        ),
        "selected_2025_matching_no_forecast_platt": selected_calibration,
        "audit_calibration_delta_bootstrap": _paired_date_bootstrap(
            audit, "calibrated_2025", "probability"
        ),
        "repeated_split_count": int(len(split_frame)),
        "repeated_split_platt_brier_better": int(split_frame.brier_delta.lt(0).sum()),
        "repeated_split_platt_logloss_better": int(split_frame.logloss_delta.lt(0).sum()),
        "platt_slope_range": [float(split_frame.slope.min()), float(split_frame.slope.max())],
        "high_confidence_errors": high_confidence,
    }
    return summary, split_frame, pd.DataFrame(slice_rows)


def _mean_date_logloss(frame: pd.DataFrame, probability: str) -> float:
    y = frame.y_break.to_numpy(dtype=float)
    p = np.clip(frame[probability].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
    losses = -y * np.log(p) - (1 - y) * np.log(1 - p)
    return float(
        pd.DataFrame({"target_date": frame.target_date, "loss": losses})
        .groupby("target_date")
        .loss.mean()
        .mean()
    )


def _retrospective_cap_trades(
    opportunities: pd.DataFrame,
    labels: pd.DataFrame,
    cap: float,
    calibration: dict[str, float] | None = None,
) -> pd.DataFrame:
    frame = opportunities.copy()
    no_rows = frame.side.eq("no")
    no = frame.loc[no_rows, ["target_date", "bracket", "decision_ts_utc"]].copy()
    market = np.clip(frame.loc[no_rows, "market_probability"].to_numpy(float), 1e-6, 1 - 1e-6)
    weather = np.clip(frame.loc[no_rows, "weather_no_probability"].to_numpy(float), 1e-6, 1 - 1e-6)
    no["p_no"] = expit(logit(market) + cap * np.tanh((logit(weather) - logit(market)) / cap))
    if calibration is not None:
        no["p_no"] = _apply_platt(no.p_no, calibration)
    frame = frame.merge(
        no, on=["target_date", "bracket", "decision_ts_utc"], how="left", validate="many_to_one"
    )
    frame["probability"] = np.where(frame.side.eq("no"), frame.p_no, 1 - frame.p_no)
    frame["edge"] = frame.probability - frame.effective_cost
    checkpoint_best = (
        frame.sort_values(
            ["target_date", "bracket", "decision_ts_utc", "edge"],
            ascending=[True, True, True, False],
        )
        .groupby(["target_date", "bracket", "decision_ts_utc"], as_index=False)
        .head(1)
    )
    trades = (
        checkpoint_best.loc[checkpoint_best.edge.gt(0)]
        .sort_values("decision_ts_utc")
        .groupby(["target_date", "bracket"], as_index=False)
        .head(1)
    )
    truth = labels[["target_date", "bracket", "y_no"]].drop_duplicates()
    trades = trades.merge(truth, on=["target_date", "bracket"], how="left", validate="many_to_one")
    trades["won"] = np.where(trades.side.eq("no"), trades.y_no.eq(1), trades.y_no.eq(0))
    trades["pnl"] = np.where(trades.won, 5.0 - trades.cash_cost, -trades.cash_cost)
    trades["cap"] = cap
    return trades


def market_cap_robustness(
    market_oof: pd.DataFrame,
    opportunities: pd.DataFrame,
    labels: pd.DataFrame,
) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = sorted(market_oof.target_date.astype(str).unique())
    split_specs: list[tuple[str, list[str], list[str]]] = []
    for index, block in enumerate(np.array_split(dates, 3)):
        validation = list(block)
        split_specs.append(
            (f"contiguous_block_{index}", [d for d in dates if d not in set(validation)], validation)
        )
    split_specs.extend(
        [
            ("odd_to_even", dates[::2], dates[1::2]),
            ("even_to_odd", dates[1::2], dates[::2]),
        ]
    )
    for index in range(5, len(dates)):
        split_specs.append((f"expanding_{index}", dates[:index], [dates[index]]))

    split_rows = []
    fixed_rows = []
    for split, train_dates, validation_dates in split_specs:
        train = market_oof.loc[market_oof.target_date.astype(str).isin(train_dates)]
        validation = market_oof.loc[market_oof.target_date.astype(str).isin(validation_dates)]
        train_scores = {
            cap: _mean_date_logloss(train, column) for cap, column in CAP_COLUMNS.items()
        }
        selected_cap = min(train_scores, key=train_scores.get)
        platt_train = train[["target_date", "y_break", CAP_COLUMNS[0.15]]].rename(
            columns={"y_break": "label", CAP_COLUMNS[0.15]: "probability"}
        )
        platt = _fit_platt(platt_train)
        platt_validation = validation.copy()
        platt_validation["c015_platt"] = _apply_platt(
            platt_validation[CAP_COLUMNS[0.15]], platt
        )
        split_rows.append(
            {
                "split": split,
                "train_dates": len(train_dates),
                "validation_dates": len(validation_dates),
                "selected_cap": selected_cap,
                "selected_validation_logloss": _mean_date_logloss(
                    validation, CAP_COLUMNS[selected_cap]
                ),
                "current_c015_validation_logloss": _mean_date_logloss(
                    validation, CAP_COLUMNS[0.15]
                ),
                "market_validation_logloss": _mean_date_logloss(validation, "market_probability"),
                "platt_intercept": platt["intercept"],
                "platt_slope": platt["slope"],
                "c015_platt_validation_logloss": _mean_date_logloss(
                    platt_validation, "c015_platt"
                ),
            }
        )
        for cap, column in CAP_COLUMNS.items():
            fixed_rows.append(
                {
                    "split": split,
                    "cap": cap,
                    "validation_logloss": _mean_date_logloss(validation, column),
                }
            )
    split_frame = pd.DataFrame(split_rows)
    fixed_frame = pd.DataFrame(fixed_rows)
    fixed_summary = (
        fixed_frame.groupby("cap").validation_logloss.agg(["mean", "max", "std"]).reset_index()
    )

    retrospective_rows = []
    trade_frames = []
    for cap in CAP_COLUMNS:
        trades = _retrospective_cap_trades(opportunities, labels, cap)
        trade_frames.append(trades)
        cost = float(trades.cash_cost.sum())
        pnl = float(trades.pnl.sum())
        retrospective_rows.append(
            {
                "model": f"bounded_c{cap:g}",
                "cap": cap,
                "trades": len(trades),
                "target_dates": trades.target_date.nunique(),
                "wins": int(trades.won.sum()),
                "cash_cost": cost,
                "pnl": pnl,
                "roi": pnl / cost if cost else None,
                "selection_status": "retrospective_diagnostic_not_model_selection",
            }
        )
    full_platt_train = market_oof[
        ["target_date", "y_break", CAP_COLUMNS[0.15]]
    ].rename(columns={"y_break": "label", CAP_COLUMNS[0.15]: "probability"})
    full_platt = _fit_platt(full_platt_train)
    platt_trades = _retrospective_cap_trades(
        opportunities, labels, 0.15, calibration=full_platt
    )
    platt_cost = float(platt_trades.cash_cost.sum())
    platt_pnl = float(platt_trades.pnl.sum())
    retrospective_rows.append(
        {
            "model": "bounded_c015_plus_oof_platt",
            "cap": 0.15,
            "trades": len(platt_trades),
            "target_dates": platt_trades.target_date.nunique(),
            "wins": int(platt_trades.won.sum()),
            "cash_cost": platt_cost,
            "pnl": platt_pnl,
            "roi": platt_pnl / platt_cost if platt_cost else None,
            "selection_status": "retrospective_diagnostic_not_model_selection",
        }
    )
    retrospective = pd.DataFrame(retrospective_rows)
    selected_counts = split_frame.selected_cap.value_counts().sort_index()
    summary = {
        "development_rows": int(len(market_oof)),
        "development_target_dates": int(len(dates)),
        "repeated_split_count": int(len(split_frame)),
        "selected_cap_counts": {str(key): int(value) for key, value in selected_counts.items()},
        "current_c015_beats_market_splits": int(
            split_frame.current_c015_validation_logloss.lt(split_frame.market_validation_logloss).sum()
        ),
        "fixed_cap_minimax_validation": float(
            fixed_summary.sort_values(["max", "mean"]).iloc[0].cap
        ),
        "fixed_cap_minimum_mean_validation": float(
            fixed_summary.sort_values(["mean", "max"]).iloc[0].cap
        ),
        "c015_platt_oof": full_platt,
        "c015_platt_beats_raw_c015_splits": int(
            split_frame.c015_platt_validation_logloss.lt(
                split_frame.current_c015_validation_logloss
            ).sum()
        ),
        "c015_platt_beats_market_splits": int(
            split_frame.c015_platt_validation_logloss.lt(
                split_frame.market_validation_logloss
            ).sum()
        ),
        "retrospective_note": "2026-08-02..11 was already seen and is never used to select a cap",
    }
    return summary, split_frame, fixed_summary, retrospective


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fmi", type=Path, default=DEFAULT_FMI)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--sample-dates", type=int, default=24)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--robustness-dir", type=Path)
    parser.add_argument("--oof-2025", type=Path, default=DEFAULT_OOF_2025)
    parser.add_argument("--market-oof", type=Path, default=DEFAULT_MARKET_OOF)
    parser.add_argument(
        "--market-opportunities", type=Path, default=DEFAULT_MARKET_OPPORTUNITIES
    )
    parser.add_argument("--market-labels", type=Path, default=DEFAULT_MARKET_LABELS)
    args = parser.parse_args()
    fmi = pd.read_csv(args.fmi, compression="gzip")
    audit = pd.read_csv(args.audit, compression="gzip")
    model = joblib.load(args.model)
    result = {
        "schema_version": "helsinki_runtime_feature_parity_v1",
        "feature_formula_parity": parity_audit(fmi, args.sample_dates),
        "frozen_contract_ablation": frozen_ablation(fmi, audit, model),
        "inputs": {
            "fmi": str(args.fmi), "audit": str(args.audit), "model": str(args.model),
        },
    }
    if args.robustness_dir is not None:
        args.robustness_dir.mkdir(parents=True, exist_ok=True)
        audit_frame, rich_probability, _ = frozen_ablation_frame(fmi, audit, model)
        oof = pd.read_csv(args.oof_2025, compression="gzip")
        weather, weather_splits, weather_slices = weather_robustness(
            oof, audit_frame, rich_probability
        )
        market_oof = pd.read_csv(args.market_oof)
        opportunities = pd.read_csv(args.market_opportunities)
        labels = pd.read_csv(args.market_labels)
        market, cap_splits, fixed_caps, retrospective = market_cap_robustness(
            market_oof, opportunities, labels
        )
        result["robustness_audit"] = {
            "schema_version": "helsinki_model_robustness_v1",
            "denominator_scope": {
                "weather_development": "2025 expanding OOF, 51,451 FMI checkpoints / 365 target dates",
                "weather_audit": "2026-01-01..2026-07-29 frozen rich-contract audit, 29,604 checkpoints / 210 target dates",
                "market_development": "2026-07-20..29 expanding OOF, 362 checkpoints / 9 target dates",
                "market_retrospective": "2026-08-02..11 PIT replay, 282 checkpoints / 8 settled target dates; not used for model selection",
            },
            "weather": weather,
            "market_residual": market,
            "decision": (
                "keep c=0.15 zero-notional incumbent; calibration is a research "
                "challenger only; no artifact/live change"
            ),
        }
        weather_splits.to_csv(args.robustness_dir / "weather_repeated_splits.csv", index=False)
        weather_slices.to_csv(args.robustness_dir / "weather_2026_slices.csv", index=False)
        cap_splits.to_csv(args.robustness_dir / "market_cap_repeated_splits.csv", index=False)
        fixed_caps.to_csv(args.robustness_dir / "market_fixed_cap_validation.csv", index=False)
        retrospective.to_csv(
            args.robustness_dir / "market_cap_retrospective_taker.csv", index=False
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
