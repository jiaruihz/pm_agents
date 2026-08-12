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


def frozen_ablation(fmi: pd.DataFrame, audit: pd.DataFrame, model: dict) -> dict:
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fmi", type=Path, default=DEFAULT_FMI)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--sample-dates", type=int, default=24)
    parser.add_argument("--output", type=Path, required=True)
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
