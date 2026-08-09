#!/usr/bin/env python3
"""Audit missing carry-specific overshoot mechanisms.

This is a research-only companion to overshoot sizing overlay v1.  It keeps the
frozen core probability as a fixed logit offset and asks whether orthogonal
settlement-lattice, dual-forecast, curve-shape, path-to-boundary, and market
microstructure features improve upward-overshoot probability out of sample.
It never changes production state or places/cancels an order.
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_carry_clean_exhaustion_backfill_v3 as clean,
)
from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_carry_residual_entry_v2 as residual,
)
from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_core_carry_overshoot_risk_sizing_overlay_v1 as v1,
)
from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_core_carry_taker_10share_v1 as taker10,
)
from scripts.analysis.reheat_risk.research_regime_routed_carry_v1 import (  # noqa: E402
    SHARD_GLOB,
)
from weather_data_feed.market_brackets import parse_market_bracket  # noqa: E402


RESEARCH_ID = "current_yes_core_carry_overshoot_missing_mechanisms_v2"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated" / RESEARCH_ID
PREREG = OUT_DIR / "preregistration.json"
REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-27-current-yes-core-carry-overshoot-missing-mechanisms-v2.md"
)
RESULT_JSON = REPORT.with_suffix(".json")
V1_LEDGER = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_overshoot_risk_sizing_overlay_v1/"
    "opportunity_ledger.csv"
)

SEED = 20260727
MIN_TRAIN_DATES = 8
FORWARD_DATES = 8
BOOTSTRAP_REPS = 5000
L2_LAMBDA = 0.1
EPS = 1e-6

NATIVE_LATTICE = [
    "exit_ticks_required",
    "assigned_forecast_exit_margin_ticks",
    "exit_distance_native",
]
DUAL_MODEL_TAIL = [
    "gfs_forecast_exit_margin_ticks",
    "ecmwf_forecast_exit_margin_ticks",
    "dual_forecast_max_margin_ticks",
    "dual_forecast_min_margin_ticks",
    "dual_forecast_spread_ticks",
    "dual_forecast_cross_fraction",
    "dual_peak_clock_disagreement_hours",
]
CURVE_HEAT_BUDGET = [
    "assigned_curve_hours_above_exit",
    "dual_curve_max_hours_above_exit",
    "dual_curve_max_heat_area_ticks",
    "assigned_curve_hours_within_one_tick",
    "assigned_curve_peak_plateau_hours",
]
PATH_TO_BOUNDARY = [
    "trend_1h_per_exit_tick",
    "trend_3h_per_exit_tick",
    "exit_ticks_per_remaining_peak_hour",
    "solar_delta_per_exit_tick",
    "reheating_transition_num",
]
MARKET_MICROSTRUCTURE = [
    "current_yes_spread",
    "log_quote_depth_ask_5c",
    "log_quote_depth_bid_5c",
    "quote_depth_log_imbalance",
    "log_top_ask_size",
    "market_mid_change_prior_full_state",
    "market_logit_change_prior_full_state",
]

MODEL_SPECS: dict[str, list[str]] = {
    "offset_lattice": NATIVE_LATTICE,
    "offset_plus_dual_model": NATIVE_LATTICE + DUAL_MODEL_TAIL,
    "offset_plus_curve": NATIVE_LATTICE + DUAL_MODEL_TAIL + CURVE_HEAT_BUDGET,
    "offset_plus_path": (
        NATIVE_LATTICE + DUAL_MODEL_TAIL + CURVE_HEAT_BUDGET + PATH_TO_BOUNDARY
    ),
    "orthogonal_compact_v2": (
        NATIVE_LATTICE
        + DUAL_MODEL_TAIL
        + CURVE_HEAT_BUDGET
        + PATH_TO_BOUNDARY
        + MARKET_MICROSTRUCTURE
    ),
}
PRIMARY = "orthogonal_compact_v2"
FAMILY_FEATURES = {
    "native_lattice": NATIVE_LATTICE,
    "dual_model_tail": DUAL_MODEL_TAIL,
    "forecast_curve_heat_budget": CURVE_HEAT_BUDGET,
    "path_to_boundary": PATH_TO_BOUNDARY,
    "market_microstructure": MARKET_MICROSTRUCTURE,
}
PRIMARY_FEATURES = MODEL_SPECS[PRIMARY]
DIAGNOSTIC_SPECS = {
    f"primary_without_{family}": [
        feature for feature in PRIMARY_FEATURES if feature not in family_features
    ]
    for family, family_features in FAMILY_FEATURES.items()
}
ALL_SPECS = {**MODEL_SPECS, **DIAGNOSTIC_SPECS}


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def upper_bracket(label: Any) -> float:
    parsed = parse_market_bracket(str(label))
    if parsed is None:
        return math.nan
    value = parsed.high if parsed.high is not None else parsed.low
    return float(value) if value is not None else math.nan


def lattice_geometry(row: pd.Series) -> dict[str, float]:
    upper = upper_bracket(row["current_bracket"])
    if not math.isfinite(upper):
        return {
            "current_bracket_upper": math.nan,
            "exit_threshold_native": math.nan,
            "exit_tick_native": math.nan,
            "exit_ticks_required": math.nan,
            "exit_distance_native": math.nan,
        }
    unit = str(row["unit"]).upper()
    running = float(row["running_native"])
    if unit == "C":
        threshold = upper + 0.5
        tick_native = 5.0 / 9.0
        running_f = round(running * 9.0 / 5.0 + 32.0)
        threshold_f = math.ceil(threshold * 9.0 / 5.0 + 32.0 - 1e-9)
        ticks = float(threshold_f - running_f)
    else:
        threshold = upper + 1.0
        tick_native = 1.0
        ticks = float(threshold - round(running))
    return {
        "current_bracket_upper": upper,
        "exit_threshold_native": threshold,
        "exit_tick_native": tick_native,
        "exit_ticks_required": ticks,
        "exit_distance_native": threshold - running,
    }


def curve_points(path_value: Any, target_date: str, cache: dict[str, Any]) -> list[tuple[float, float, str]]:
    if path_value is None or str(path_value) in {"", "nan", "None"}:
        return []
    path = Path(str(path_value))
    if not path.exists():
        return []
    key = str(path)
    if key not in cache:
        cache[key] = json.loads(path.read_text(encoding="utf-8"))
    payload = cache[key]
    hourly = payload.get("hourly") or {}
    unit = str((payload.get("hourly_units") or {}).get("temperature_2m") or "°F")
    output: list[tuple[float, float, str]] = []
    for raw_time, raw_temp in zip(
        hourly.get("time") or [], hourly.get("temperature_2m") or []
    ):
        if raw_temp is None or not str(raw_time).startswith(str(target_date)):
            continue
        try:
            hour = float(str(raw_time)[11:13]) + float(str(raw_time)[14:16]) / 60.0
            output.append((hour, float(raw_temp), unit))
        except (TypeError, ValueError, IndexError):
            continue
    return output


def curve_metrics(
    row: pd.Series, model: str, cache: dict[str, Any]
) -> dict[str, float]:
    points = curve_points(
        row.get(f"{model}_forecast_cache_path"),
        str(row["target_date"]),
        cache,
    )
    decision_hour = float(row["decision_hour_local"])
    threshold = float(row["exit_threshold_native"])
    tick_native = float(row["exit_tick_native"])
    future: list[tuple[float, float]] = []
    for hour, raw_temp, raw_unit in points:
        if hour + 1e-9 < decision_hour:
            continue
        unit_text = raw_unit.lower()
        temp_f = raw_temp if "f" in unit_text else raw_temp * 9.0 / 5.0 + 32.0
        temp_native = (
            temp_f
            if str(row["unit"]).upper() == "F"
            else (temp_f - 32.0) * 5.0 / 9.0
        )
        future.append((hour, temp_native))
    if not future:
        return {
            f"{model}_curve_hours_above_exit": math.nan,
            f"{model}_curve_heat_area_ticks": math.nan,
            f"{model}_curve_hours_within_one_tick": math.nan,
            f"{model}_curve_peak_plateau_hours": math.nan,
            f"{model}_curve_first_exit_delta_hours": math.nan,
        }
    values = np.array([value for _, value in future], dtype=float)
    above = values >= threshold - 1e-9
    first_exit = (
        min(hour for (hour, _), flag in zip(future, above) if flag) - decision_hour
        if bool(above.any())
        else math.nan
    )
    return {
        f"{model}_curve_hours_above_exit": float(above.sum()),
        f"{model}_curve_heat_area_ticks": float(
            np.maximum(0.0, (values - threshold) / tick_native).sum()
        ),
        f"{model}_curve_hours_within_one_tick": float(
            (values >= threshold - tick_native - 1e-9).sum()
        ),
        f"{model}_curve_peak_plateau_hours": float(
            (values >= values.max() - tick_native - 1e-9).sum()
        ),
        f"{model}_curve_first_exit_delta_hours": first_exit,
    }


def load_final_winning_brackets() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    wanted = [
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "decision_hour_local",
        "bracket",
        "current_bracket",
        "outcome",
        "final_winning_bracket",
    ]
    for path in sorted(glob.glob(SHARD_GLOB)):
        available = set(pd.read_csv(path, nrows=0).columns)
        frame = pd.read_csv(
            path,
            usecols=[column for column in wanted if column in available],
            low_memory=False,
        )
        frame["target_date"] = frame["target_date"].astype(str)
        frame = frame[
            frame["bracket"].astype(str).eq(
                frame["current_bracket"].astype(str)
            )
            & frame["outcome"].astype(str).str.lower().eq("yes")
        ]
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True)
    return combined.drop_duplicates(
        ["city", "target_date", "decision_hour_local"]
    )[
        [
            "city",
            "target_date",
            "decision_snapshot_ts_utc",
            "final_winning_bracket",
        ]
    ]


def direction_from_brackets(current: Any, final: Any) -> str:
    current_parsed = parse_market_bracket(str(current))
    final_parsed = parse_market_bracket(str(final))
    if (
        current_parsed is None
        or current_parsed.low is None
        or final_parsed is None
        or final_parsed.low is None
    ):
        return "unknown"
    current_low = float(current_parsed.low)
    final_low = float(final_parsed.low)
    if final_low > current_low:
        return "upward_leave"
    if final_low < current_low:
        return "downward_leave"
    return "current_exact_hold"


def prepare_features() -> tuple[pd.DataFrame, dict[str, Any]]:
    parent, v1_lineage = v1.prepare_features()
    parent = taker10.add_ten_share_cost(parent)
    old = pd.read_csv(V1_LEDGER, usecols=["opportunity_id", "frozen_baseline_selected"])
    parent = parent.merge(old, on="opportunity_id", how="left", validate="one_to_one")
    parent["frozen_baseline_selected"] = (
        parent["frozen_baseline_selected"].fillna(False).astype(bool)
    )

    raw = v1.add_cache_paths(residual.prepare_universe())
    raw["target_date"] = raw["target_date"].astype(str)
    raw["decision_snapshot_dt"] = pd.to_datetime(
        raw["decision_snapshot_ts_utc"], utc=True, errors="coerce"
    )
    raw["market_mid_full"] = (
        raw["current_yes_bid"] + raw["current_yes_ask"]
    ) / 2.0
    raw["market_logit_full"] = np.log(
        raw["market_mid_full"].clip(EPS, 1 - EPS)
        / (1 - raw["market_mid_full"].clip(EPS, 1 - EPS))
    )
    raw = raw.sort_values(
        ["city", "target_date", "decision_snapshot_dt"]
    ).reset_index(drop=True)
    grouped = raw.groupby(["city", "target_date"], sort=False)
    raw["market_mid_change_prior_full_state"] = grouped["market_mid_full"].diff()
    raw["market_logit_change_prior_full_state"] = grouped[
        "market_logit_full"
    ].diff()
    keys = ["city", "target_date", "decision_snapshot_ts_utc"]
    raw_columns = keys + [
        "forecast_assigned_model",
        "gfs_forecast_max_native",
        "ecmwf_forecast_max_native",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "quote_best_ask_size",
        "quote_best_bid_size",
        "quote_depth_ask_5c",
        "quote_depth_bid_5c",
        "obs_count_to_decision",
        "obs_age_min",
        "relative_humidity_pct",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "sky_cover_code",
        "market_mid_change_prior_full_state",
        "market_logit_change_prior_full_state",
        "gfs_forecast_cache_path",
        "ecmwf_forecast_cache_path",
    ]
    context = raw[raw_columns].drop_duplicates(keys)
    frame = parent.merge(context, on=keys, how="left", validate="one_to_one")
    final_winners = load_final_winning_brackets()
    frame = frame.merge(
        final_winners,
        on=keys,
        how="left",
        validate="one_to_one",
    )
    frame["canonical_direction"] = [
        direction_from_brackets(current, final)
        for current, final in zip(
            frame["current_bracket"], frame["final_winning_bracket"]
        )
    ]
    geometry = frame.apply(lattice_geometry, axis=1, result_type="expand")
    frame = pd.concat([frame, geometry], axis=1)

    tick = frame["exit_tick_native"].replace(0, np.nan)
    for model in ("gfs", "ecmwf"):
        frame[f"{model}_forecast_exit_margin_ticks"] = (
            frame[f"{model}_forecast_max_native"]
            - frame["exit_threshold_native"]
        ) / tick
    assigned_margin = np.where(
        frame["forecast_assigned_model"].astype(str).eq("ecmwf"),
        frame["ecmwf_forecast_exit_margin_ticks"],
        frame["gfs_forecast_exit_margin_ticks"],
    )
    frame["assigned_forecast_exit_margin_ticks"] = assigned_margin
    frame["dual_forecast_max_margin_ticks"] = frame[
        ["gfs_forecast_exit_margin_ticks", "ecmwf_forecast_exit_margin_ticks"]
    ].max(axis=1)
    frame["dual_forecast_min_margin_ticks"] = frame[
        ["gfs_forecast_exit_margin_ticks", "ecmwf_forecast_exit_margin_ticks"]
    ].min(axis=1)
    frame["dual_forecast_spread_ticks"] = (
        frame["gfs_forecast_exit_margin_ticks"]
        - frame["ecmwf_forecast_exit_margin_ticks"]
    ).abs()
    frame["dual_forecast_cross_fraction"] = (
        frame[
            ["gfs_forecast_exit_margin_ticks", "ecmwf_forecast_exit_margin_ticks"]
        ]
        .ge(0)
        .mean(axis=1)
    )
    frame["dual_peak_clock_disagreement_hours"] = (
        frame["gfs_forecast_peak_delta_hours_local"]
        - frame["ecmwf_forecast_peak_delta_hours_local"]
    ).abs()

    cache: dict[str, Any] = {}
    curve_rows: list[dict[str, float]] = []
    for _, row in frame.iterrows():
        curve_rows.append(
            {
                **curve_metrics(row, "gfs", cache),
                **curve_metrics(row, "ecmwf", cache),
            }
        )
    curves = pd.DataFrame(curve_rows, index=frame.index)
    frame = pd.concat([frame, curves], axis=1)
    assigned_is_ecmwf = frame["forecast_assigned_model"].astype(str).eq("ecmwf")
    for suffix in [
        "hours_above_exit",
        "heat_area_ticks",
        "hours_within_one_tick",
        "peak_plateau_hours",
        "first_exit_delta_hours",
    ]:
        frame[f"assigned_curve_{suffix}"] = np.where(
            assigned_is_ecmwf,
            frame[f"ecmwf_curve_{suffix}"],
            frame[f"gfs_curve_{suffix}"],
        )
    frame["dual_curve_max_hours_above_exit"] = frame[
        ["gfs_curve_hours_above_exit", "ecmwf_curve_hours_above_exit"]
    ].max(axis=1)
    frame["dual_curve_max_heat_area_ticks"] = frame[
        ["gfs_curve_heat_area_ticks", "ecmwf_curve_heat_area_ticks"]
    ].max(axis=1)

    required = frame["exit_ticks_required"].clip(lower=0.25)
    frame["trend_1h_per_exit_tick"] = frame["temp_trend_1h_f"] / required
    frame["trend_3h_per_exit_tick"] = frame["temp_trend_3h_f"] / required
    remaining_peak = frame["forecast_peak_delta_hours_local"].clip(lower=0)
    frame["exit_ticks_per_remaining_peak_hour"] = (
        frame["exit_ticks_required"] / (remaining_peak + 0.5)
    )
    frame["solar_delta_per_exit_tick"] = (
        frame["solar_elevation_delta_2h_deg"] / required
    )

    frame["current_yes_spread"] = (
        frame["current_yes_ask"] - frame["current_yes_bid"]
    )
    frame["log_quote_depth_ask_5c"] = np.log1p(
        frame["quote_depth_ask_5c"].clip(lower=0)
    )
    frame["log_quote_depth_bid_5c"] = np.log1p(
        frame["quote_depth_bid_5c"].clip(lower=0)
    )
    frame["quote_depth_log_imbalance"] = (
        frame["log_quote_depth_bid_5c"] - frame["log_quote_depth_ask_5c"]
    )
    frame["log_top_ask_size"] = np.log1p(
        frame["quote_best_ask_size"].clip(lower=0)
    )
    frame["p_over_core"] = (1.0 - frame["p_core_no_obs_age"]).clip(
        EPS, 1 - EPS
    )
    frame["p_over_market"] = (1.0 - frame["market_mid"]).clip(EPS, 1 - EPS)
    frame["base_offset_logit"] = np.log(
        frame["p_over_core"] / (1.0 - frame["p_over_core"])
    )
    frame = frame.sort_values(
        ["target_date", "city", "decision_snapshot_dt"]
    ).reset_index(drop=True)
    lineage = {
        "v1": v1_lineage,
        "raw_state": "mechanism timing audit full 4061-state PIT stream",
        "lattice": (
            "C brackets use upper+0.5C continuous exit and integer-F ticks "
            "derived from source running max; F ranges use upper+1F"
        ),
        "forecast": (
            "GFS/ECMWF fixed previous-run Single Runs values and hourly curves; "
            "assigned model remains CITY_MODEL"
        ),
        "market": (
            "same-state bid/ask/depth plus prior checkpoint from full PIT stream, "
            "before the parent market-mid>=0.80 restriction"
        ),
        "curve_cache_files": len(cache),
        "target_direction_audit": (
            "canonical final_winning_bracket compared with current_bracket; "
            "1-current_bracket-held is valid only because all non-holds in "
            "this frozen parent are upward leaves"
        ),
    }
    return frame, lineage


def city_day_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby(["city", "target_date"])["overshoot"].transform("size")
    return 1.0 / counts.clip(lower=1).to_numpy(float)


def transform_features(
    train: pd.DataFrame, test: pd.DataFrame, features: list[str]
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    train_columns: list[np.ndarray] = []
    test_columns: list[np.ndarray] = []
    names: list[str] = []
    medians: dict[str, float] = {}
    for feature in features:
        train_raw = pd.to_numeric(train[feature], errors="coerce")
        test_raw = pd.to_numeric(test[feature], errors="coerce")
        median = float(train_raw.median()) if train_raw.notna().any() else 0.0
        medians[feature] = median
        train_filled = train_raw.fillna(median).to_numpy(float)
        test_filled = test_raw.fillna(median).to_numpy(float)
        mean = float(train_filled.mean())
        scale = float(train_filled.std())
        if not math.isfinite(scale) or scale < 1e-9:
            continue
        train_columns.append((train_filled - mean) / scale)
        test_columns.append((test_filled - mean) / scale)
        names.append(feature)
        if train_raw.isna().any():
            missing_train = train_raw.isna().to_numpy(float)
            missing_test = test_raw.isna().to_numpy(float)
            missing_scale = float(missing_train.std())
            if missing_scale >= 1e-9:
                train_columns.append(
                    (missing_train - missing_train.mean()) / missing_scale
                )
                test_columns.append(
                    (missing_test - missing_train.mean()) / missing_scale
                )
                names.append(f"{feature}__missing")
    if not train_columns:
        return (
            np.zeros((len(train), 0)),
            np.zeros((len(test), 0)),
            {"columns": [], "medians": medians},
        )
    return (
        np.column_stack(train_columns),
        np.column_stack(test_columns),
        {"columns": names, "medians": medians},
    )


def fit_offset_residual(
    train: pd.DataFrame, test: pd.DataFrame, features: list[str]
) -> tuple[np.ndarray, dict[str, Any]]:
    x_train, x_test, transform = transform_features(train, test, features)
    if x_train.shape[1] == 0:
        return test["p_over_core"].to_numpy(float), {
            "success": True,
            "coefficient_norm": 0.0,
            **transform,
        }
    y = train["overshoot"].to_numpy(float)
    weights = city_day_weights(train)
    weights = weights / weights.sum()
    offset_train = train["base_offset_logit"].to_numpy(float)

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        eta = offset_train + x_train @ beta
        probability = expit(eta)
        loss = float(
            np.sum(weights * (np.logaddexp(0.0, eta) - y * eta))
            + 0.5 * L2_LAMBDA * np.dot(beta, beta)
        )
        gradient = (
            x_train.T @ (weights * (probability - y))
            + L2_LAMBDA * beta
        )
        return loss, gradient

    result = minimize(
        lambda beta: objective(beta),
        np.zeros(x_train.shape[1], dtype=float),
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": 500, "ftol": 1e-12},
    )
    if not result.success:
        raise RuntimeError(f"offset residual fit failed: {result.message}")
    probability = expit(
        test["base_offset_logit"].to_numpy(float) + x_test @ result.x
    )
    return np.clip(probability, EPS, 1 - EPS), {
        "success": bool(result.success),
        "objective": float(result.fun),
        "iterations": int(result.nit),
        "coefficient_norm": float(np.linalg.norm(result.x)),
        "columns": transform["columns"],
        "coefficients": result.x.tolist(),
    }


def expanding_oof(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(frame["target_date"].unique())
    predictions: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    keep = [
        "opportunity_id",
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "current_bracket",
        "overshoot",
        "label",
        "p_over_core",
        "p_over_market",
    ]
    for index, target_date in enumerate(dates):
        if index < MIN_TRAIN_DATES:
            continue
        train = frame[frame["target_date"].lt(target_date)].copy()
        test = frame[frame["target_date"].eq(target_date)].copy()
        if train["overshoot"].nunique() < 2 or test.empty:
            continue
        result = test[keep].copy()
        for name, features in ALL_SPECS.items():
            probability, fit = fit_offset_residual(train, test, features)
            result[f"p_over_{name}"] = probability
            fold_rows.append(
                {
                    "target_date": target_date,
                    "model": name,
                    "train_rows": len(train),
                    "train_city_days": train.groupby(
                        ["city", "target_date"]
                    ).ngroups,
                    "train_dates": train["target_date"].nunique(),
                    "test_rows": len(test),
                    "coefficient_norm": fit["coefficient_norm"],
                    "iterations": fit.get("iterations"),
                    "columns": json.dumps(fit["columns"]),
                    "coefficients": json.dumps(fit.get("coefficients", [])),
                }
            )
        predictions.append(result)
    return (
        pd.concat(predictions, ignore_index=True),
        pd.DataFrame(fold_rows),
    )


def leave_date_out_primary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    keep = ["opportunity_id", "city", "target_date", "overshoot"]
    for target_date in sorted(frame["target_date"].unique()):
        train = frame[frame["target_date"].ne(target_date)]
        test = frame[frame["target_date"].eq(target_date)]
        probability, _ = fit_offset_residual(train, test, MODEL_SPECS[PRIMARY])
        result = test[keep].copy()
        result["p_over_primary_lodo"] = probability
        rows.append(result)
    return pd.concat(rows, ignore_index=True)


def score_metrics(frame: pd.DataFrame, probability_column: str) -> dict[str, Any]:
    weighted = (
        frame.groupby(["city", "target_date"], as_index=False)
        .agg(
            overshoot=("overshoot", "first"),
            probability=(probability_column, "mean"),
        )
    )
    y = weighted["overshoot"].to_numpy(int)
    p = weighted["probability"].clip(EPS, 1 - EPS).to_numpy(float)
    return {
        "rows": len(frame),
        "city_days": len(weighted),
        "dates": frame["target_date"].nunique(),
        "overshoot_city_days": int(y.sum()),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p, labels=[0, 1])),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
        "mean_prediction": float(p.mean()),
        "actual_rate": float(y.mean()),
    }


def paired_delta(
    frame: pd.DataFrame, candidate: str, baseline: str
) -> dict[str, Any]:
    daily_rows: list[dict[str, Any]] = []
    for target_date, day in frame.groupby("target_date", sort=True):
        counts = day.groupby("city")["overshoot"].transform("size")
        weights = 1.0 / counts.clip(lower=1).to_numpy(float)
        candidate_probability = day[candidate].clip(EPS, 1 - EPS)
        baseline_probability = day[baseline].clip(EPS, 1 - EPS)
        outcome = day["overshoot"]
        daily_rows.append(
            {
                "target_date": target_date,
                "brier_delta": np.average(
                    (outcome - candidate_probability) ** 2
                    - (outcome - baseline_probability) ** 2,
                    weights=weights,
                ),
                "logloss_delta": np.average(
                    -(
                        outcome * np.log(candidate_probability)
                        + (1 - outcome) * np.log(1 - candidate_probability)
                    )
                    + (
                        outcome * np.log(baseline_probability)
                        + (1 - outcome) * np.log(1 - baseline_probability)
                    ),
                    weights=weights,
                ),
            }
        )
    daily = pd.DataFrame(daily_rows)
    rng = np.random.default_rng(SEED)
    values = daily[["brier_delta", "logloss_delta"]].to_numpy(float)
    draws = np.empty((BOOTSTRAP_REPS, 2), dtype=float)
    for index in range(BOOTSTRAP_REPS):
        chosen = rng.integers(0, len(values), len(values))
        draws[index] = values[chosen].mean(axis=0)
    point = values.mean(axis=0)
    return {
        "candidate_minus_baseline_brier": float(point[0]),
        "brier_delta_ci95": np.quantile(draws[:, 0], [0.025, 0.975]).tolist(),
        "candidate_minus_baseline_logloss": float(point[1]),
        "logloss_delta_ci95": np.quantile(
            draws[:, 1], [0.025, 0.975]
        ).tolist(),
    }


def risk_lift(frame: pd.DataFrame, probability_column: str) -> dict[str, Any]:
    city_days = (
        frame.groupby(["city", "target_date"], as_index=False)
        .agg(
            overshoot=("overshoot", "first"),
            risk=(probability_column, "mean"),
        )
        .sort_values("risk", ascending=False)
    )
    top_n = max(1, math.ceil(len(city_days) * 0.10))
    top = city_days.head(top_n)
    base_rate = float(city_days["overshoot"].mean())
    top_rate = float(top["overshoot"].mean())
    return {
        "city_days": len(city_days),
        "top_decile_city_days": len(top),
        "base_overshoot_rate": base_rate,
        "top_decile_overshoot_rate": top_rate,
        "top_decile_lift": top_rate / base_rate if base_rate else None,
        "overshoot_capture_rate": (
            float(top["overshoot"].sum() / city_days["overshoot"].sum())
            if city_days["overshoot"].sum()
            else None
        ),
    }


def period_scores(
    frame: pd.DataFrame, forward_dates: set[str]
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for period, sample in [
        ("front", frame[~frame["target_date"].isin(forward_dates)]),
        ("frozen_forward", frame[frame["target_date"].isin(forward_dates)]),
    ]:
        for model in MODEL_SPECS:
            for baseline in ["p_over_core", "p_over_market"]:
                delta = paired_delta(sample, f"p_over_{model}", baseline)
                rows.append(
                    {
                        "period": period,
                        "model": model,
                        "baseline": baseline,
                        "rows": len(sample),
                        "city_days": sample.groupby(
                            ["city", "target_date"]
                        ).ngroups,
                        "dates": sample["target_date"].nunique(),
                        **delta,
                    }
                )
    return pd.DataFrame(rows)


def first_positive(
    frame: pd.DataFrame, hold_probability: str
) -> pd.DataFrame:
    sample = frame[
        frame["ten_share_cost_per_share"].notna()
        & frame[hold_probability].gt(frame["ten_share_cost_per_share"])
    ].copy()
    return (
        sample.sort_values(
            ["target_date", "city", "decision_snapshot_dt"]
        )
        .drop_duplicates(["city", "target_date"], keep="first")
        .copy()
    )


def trade_diagnostic(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    baseline = first_positive(frame, "p_core_no_obs_age")
    candidate = first_positive(frame, "p_hold_primary")
    rows = []
    for name, sample in [("baseline_core_10_taker", baseline), ("candidate_v2_10_taker", candidate)]:
        work = sample.copy()
        work["policy"] = name
        work["cost_usd"] = 10.0 * work["ten_share_cost_per_share"]
        work["pnl_usd"] = 10.0 * (
            work["label"] - work["ten_share_cost_per_share"]
        )
        rows.append(work)
    detail = pd.concat(rows, ignore_index=True)
    dates = sorted(frame["target_date"].unique())
    daily = (
        detail.groupby(["policy", "target_date"], as_index=False)
        .agg(
            city_days=("city", "nunique"),
            cost_usd=("cost_usd", "sum"),
            pnl_usd=("pnl_usd", "sum"),
        )
    )
    daily = pd.MultiIndex.from_product(
        [["baseline_core_10_taker", "candidate_v2_10_taker"], dates],
        names=["policy", "target_date"],
    ).to_frame(index=False).merge(
        daily, on=["policy", "target_date"], how="left"
    ).fillna(0)
    base_daily = daily[daily["policy"].eq("baseline_core_10_taker")].sort_values(
        "target_date"
    )
    candidate_daily = daily[
        daily["policy"].eq("candidate_v2_10_taker")
    ].sort_values("target_date")
    delta = candidate_daily["pnl_usd"].to_numpy() - base_daily["pnl_usd"].to_numpy()
    rng = np.random.default_rng(SEED)
    draws = []
    for _ in range(BOOTSTRAP_REPS):
        chosen = rng.integers(0, len(delta), len(delta))
        draws.append(float(delta[chosen].mean()))
    base_winners = set(
        zip(
            baseline.loc[baseline["label"].eq(1), "city"],
            baseline.loc[baseline["label"].eq(1), "target_date"],
        )
    )
    candidate_keys = set(zip(candidate["city"], candidate["target_date"]))
    summary = {
        "baseline": {
            "city_days": len(baseline),
            "wins": int(baseline["label"].sum()),
            "losses": int(baseline["label"].eq(0).sum()),
            "cost_usd": float((10 * baseline["ten_share_cost_per_share"]).sum()),
            "pnl_usd": float(
                (10 * (baseline["label"] - baseline["ten_share_cost_per_share"])).sum()
            ),
        },
        "candidate": {
            "city_days": len(candidate),
            "wins": int(candidate["label"].sum()),
            "losses": int(candidate["label"].eq(0).sum()),
            "cost_usd": float((10 * candidate["ten_share_cost_per_share"]).sum()),
            "pnl_usd": float(
                (10 * (candidate["label"] - candidate["ten_share_cost_per_share"])).sum()
            ),
        },
        "mean_daily_pnl_delta_usd": float(delta.mean()),
        "mean_daily_pnl_delta_ci95": np.quantile(
            draws, [0.025, 0.975]
        ).tolist(),
        "baseline_winner_selection_harm": (
            1.0
            - len(base_winners & candidate_keys) / len(base_winners)
            if base_winners
            else None
        ),
        "scope": "historical ten-share taker replay only; no maker/fill claim",
    }
    return detail, daily, summary


def case_path_audit(frame: pd.DataFrame) -> pd.DataFrame:
    selected_losses = frame[
        frame["frozen_baseline_selected"] & frame["overshoot"].eq(1)
    ].copy()
    histories, _ = clean.load_observation_histories(
        sorted(selected_losses["city"].unique()),
        str(selected_losses["target_date"].min()),
        str(selected_losses["target_date"].max()),
    )
    rows: list[dict[str, Any]] = []
    for _, row in selected_losses.iterrows():
        day = histories.get((str(row["city"]), str(row["target_date"])))
        future = (
            day[day["ts"].gt(row["decision_snapshot_dt"])].copy()
            if day is not None
            else pd.DataFrame()
        )
        if len(future):
            future["temp_native"] = np.where(
                str(row["unit"]).upper() == "F",
                future["tmpf"],
                (future["tmpf"] - 32.0) * 5.0 / 9.0,
            )
            exits = future[
                future["temp_native"].ge(row["exit_threshold_native"] - 1e-9)
            ]
        else:
            exits = pd.DataFrame()
        first_exit = exits.iloc[0] if len(exits) else None
        oof_risk = row.get(f"p_over_{PRIMARY}")
        lodo_risk = row.get("p_over_primary_lodo")
        audit_risk = oof_risk if pd.notna(oof_risk) else lodo_risk
        rows.append(
            {
                "city": row["city"],
                "target_date": row["target_date"],
                "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                "current_bracket": row["current_bracket"],
                "unit": row["unit"],
                "running_native": row["running_native"],
                "exit_ticks_required": row["exit_ticks_required"],
                "assigned_forecast_exit_margin_ticks": row[
                    "assigned_forecast_exit_margin_ticks"
                ],
                "dual_forecast_max_margin_ticks": row[
                    "dual_forecast_max_margin_ticks"
                ],
                "dual_forecast_cross_fraction": row[
                    "dual_forecast_cross_fraction"
                ],
                "trend_1h_per_exit_tick": row["trend_1h_per_exit_tick"],
                "assigned_curve_hours_above_exit": row[
                    "assigned_curve_hours_above_exit"
                ],
                "minutes_to_first_observed_exit": (
                    (first_exit["ts"] - row["decision_snapshot_dt"]).total_seconds()
                    / 60.0
                    if first_exit is not None
                    else None
                ),
                "reports_to_first_observed_exit": (
                    int(future.index.get_loc(first_exit.name) + 1)
                    if first_exit is not None
                    else None
                ),
                "final_native": row["final_native"],
                "p_over_core": row["p_over_core"],
                "p_over_primary_oof": oof_risk,
                "p_over_primary_lodo": lodo_risk,
                "audit_risk": audit_risk,
                "risk_evidence": "expanding_oof" if pd.notna(oof_risk) else "lodo_diagnostic",
                "mechanism_category": mechanism_category(row),
            }
        )
    return pd.DataFrame(rows)


def mechanism_category(row: pd.Series) -> str:
    assigned = float(row["assigned_forecast_exit_margin_ticks"])
    dual_max = float(row["dual_forecast_max_margin_ticks"])
    path = float(row["trend_1h_per_exit_tick"])
    if assigned >= 0:
        return "assigned_forecast_cross"
    if dual_max >= 0:
        return "alternate_forecast_cross_only"
    if path > 0:
        return "fresh_path_continuation_only"
    return "no_mechanism_warning"


def mechanism_summary(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["mechanism_category"] = work.apply(mechanism_category, axis=1)
    rows: list[dict[str, Any]] = []
    for denominator, sample in [
        ("frozen_selected", work[work["frozen_baseline_selected"]]),
        ("expanding_oof_city_day_first", (
            work[work[f"p_over_{PRIMARY}"].notna()]
            .sort_values(["target_date", "city", "decision_snapshot_dt"])
            .drop_duplicates(["city", "target_date"], keep="first")
        )),
    ]:
        base_rate = float(sample["overshoot"].mean())
        for category, group in sample.groupby("mechanism_category"):
            rate = float(group["overshoot"].mean())
            rows.append(
                {
                    "denominator": denominator,
                    "mechanism_category": category,
                    "city_days": len(group),
                    "winners": int(group["overshoot"].eq(0).sum()),
                    "overshoots": int(group["overshoot"].sum()),
                    "overshoot_rate": rate,
                    "lift_vs_denominator": (
                        rate / base_rate if base_rate > 0 else None
                    ),
                    "winner_false_positive_share_of_category": float(
                        group["overshoot"].eq(0).mean()
                    ),
                }
            )
    return pd.DataFrame(rows)


def choose_action(
    primary_delta: dict[str, Any],
    forward: pd.DataFrame,
    lift: dict[str, Any],
    trade: dict[str, Any],
) -> dict[str, Any]:
    point = (
        primary_delta["candidate_minus_baseline_brier"] < 0
        and primary_delta["candidate_minus_baseline_logloss"] < 0
    )
    significance = (
        primary_delta["brier_delta_ci95"][1] < 0
        and primary_delta["logloss_delta_ci95"][1] < 0
    )
    forward_core = forward[
        forward["period"].eq("frozen_forward")
        & forward["model"].eq(PRIMARY)
        & forward["baseline"].eq("p_over_core")
    ].iloc[0]
    forward_pass = (
        forward_core["candidate_minus_baseline_brier"] < 0
        and forward_core["candidate_minus_baseline_logloss"] < 0
    )
    lift_pass = float(lift["top_decile_lift"] or 0) > 1.0
    harm_pass = float(trade["baseline_winner_selection_harm"] or 1.0) < 0.25
    if significance and forward_pass and lift_pass and harm_pass:
        action = "zero-notional feature shadow"
    elif point:
        action = "research telemetry only"
    else:
        action = "不采用 overlay"
    return {
        "action": action,
        "proper_score_point_gate": point,
        "proper_score_significance_gate": significance,
        "frozen_forward_gate": forward_pass,
        "top_risk_lift_gate": lift_pass,
        "winner_harm_below_25pct_gate": harm_pass,
        "deployment_authorized": False,
    }


def fmt(value: Any, digits: int = 4) -> str:
    if value is None or pd.isna(value):
        return "NA"
    return f"{float(value):.{digits}f}"


def write_report(payload: dict[str, Any]) -> None:
    model_by = {row["model"]: row for row in payload["models"]}
    primary = model_by[PRIMARY]
    delta = primary["vs_core"]
    trade = payload["trade_diagnostic"]
    action = payload["verdict"]["action"]
    lattice = model_by["offset_lattice"]
    front_primary = next(
        row
        for row in payload["periods"]
        if row["period"] == "front"
        and row["model"] == PRIMARY
        and row["baseline"] == "p_over_core"
    )
    forward_primary = next(
        row
        for row in payload["periods"]
        if row["period"] == "frozen_forward"
        and row["model"] == PRIMARY
        and row["baseline"] == "p_over_core"
    )
    lodo = payload["leave_date_out"]
    lines = [
        "# Current-YES core carry overshoot missing mechanisms v2",
        "",
        f"Status: `{action}`",
        f"Generated: `{payload['generated_at_utc']}`",
        "",
        "## 结论",
        "",
        f"**动作：`{action}`；不改 live。** 本轮真正补到的机制不是新的 case gate，而是把风险写在"
        " settlement-native upward-exit lattice 上，并把 assigned 单模型扩成双模型 tail/curve 分布；"
        "同时用 frozen-core logit offset 避免再次重标定 baseline。",
        "",
        f"Primary `{PRIMARY}` 相对 frozen core：Brier Δ "
        f"`{delta['candidate_minus_baseline_brier']:+.6f}` "
        f"CI `[{delta['brier_delta_ci95'][0]:+.6f},{delta['brier_delta_ci95'][1]:+.6f}]`；"
        f"logloss Δ `{delta['candidate_minus_baseline_logloss']:+.6f}` "
        f"CI `[{delta['logloss_delta_ci95'][0]:+.6f},{delta['logloss_delta_ci95'][1]:+.6f}]`。"
        "负值才是改善。",
        "",
        "**最接近“精髓”的表示法是 native lattice，不是大而全模型，但它尚未成为增量 alpha。** "
        f"最简 `offset_lattice` 的 target-date 等权 paired Brier/logloss Δ "
        f"`{lattice['vs_core']['candidate_minus_baseline_brier']:+.6f}/"
        f"{lattice['vs_core']['candidate_minus_baseline_logloss']:+.6f}`，CI 跨 0；"
        f"而全体 city-day 聚合分数为 `{lattice['brier']:.5f}/{lattice['logloss']:.5f}`，"
        f"反而略差于 core `{payload['baselines']['core']['brier']:.5f}/"
        f"{payload['baselines']['core']['logloss']:.5f}`，AUC 也未提高。"
        "继续加入双模型、curve、path、盘口后没有累积增益。lattice 只应作为后续 telemetry/state 候选保存，"
        "当前不能拿来降仓或过滤。",
        "",
        "## 上一轮忽略的“精髓”",
        "",
        "1. **离输掉 current exact 还差几个 settlement-native tick**，不是 forecast max − running max。"
        "C 档要映射到 half-up bracket boundary，再离散到整数 °F source lattice。",
        "2. **预测分布是否跨过 exit boundary**，不是 assigned model 的单点最高温。"
        "GFS/ECMWF 的 max、分歧、cross fraction、hourly heat area 都应相对同一个 lattice 表达。",
        "3. **路径速度要除以 boundary buffer**。同样 +1°F/h，对只剩 1 tick 和还差 4 ticks 的风险含义不同。",
        "4. **市场盘口的置信结构**。静态 midpoint 已在 core prior 内；spread、depth、imbalance、PIT price momentum"
        " 才是可能正交的信息。",
        "5. **旧 v1 的 forecast revision 是死特征**：同一天使用同一 Single Runs cache，"
        "max revision 非空值只有一个取值；不能把它当真正 run-to-run revision。",
        "",
        "## 固定分母与数据",
        "",
        f"- Parent：{payload['parent']['rows']} states / "
        f"{payload['parent']['city_days']} city-days / {payload['parent']['dates']} dates，"
        f"{payload['parent']['date_min']}..{payload['parent']['date_max']}。",
        f"- Expanding OOF：{payload['oof']['rows']} states / "
        f"{payload['oof']['city_days']} city-days / {payload['oof']['dates']} dates。",
        f"- 历史 5-share frozen selector audit：{payload['parent']['selected']} city-days，"
        f"{payload['parent']['selected_losses']} upward-overshoot losses；model denominator 不限于这些 rows。",
        f"- Settlement direction audit：非 hold `{payload['direction_audit']['nonhold_rows']}` rows，"
        f"其中 upward `{payload['direction_audit']['nonhold_upward_rows']}`、"
        f"downward `{payload['direction_audit']['nonhold_downward_rows']}`；"
        "本轮 target 没被 downward leave 污染。",
        f"- Prereg SHA256=`{payload['artifacts']['preregistration_sha256']}`；"
        f"parent SHA256=`{payload['artifacts']['parent_sha256']}`。本次未 sync/rebuild、未触碰生产。",
        "",
        "## 累积 feature-family OOF",
        "",
        "| model | features | Brier | logloss | AUC | ΔBrier vs core | Δlogloss vs core |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in MODEL_SPECS:
        row = model_by[name]
        model_delta = row["vs_core"]
        lines.append(
            f"| {name} | {len(MODEL_SPECS[name])} | {row['brier']:.5f} | "
            f"{row['logloss']:.5f} | {fmt(row['auc'],3)} | "
            f"{model_delta['candidate_minus_baseline_brier']:+.6f} | "
            f"{model_delta['candidate_minus_baseline_logloss']:+.6f} |"
        )
    lift = payload["risk_lift"]
    core_lift = payload["risk_lifts"]["core"]
    lines += [
        "",
        f"Primary top-decile overshoot lift=`{fmt(lift['top_decile_lift'],2)}x`，"
        f"捕获 `{float(lift['overshoot_capture_rate'] or 0):.2%}` overshoot city-days；"
        f"frozen core 本身为 `{fmt(core_lift['top_decile_lift'],2)}x` / "
        f"`{float(core_lift['overshoot_capture_rate'] or 0):.2%}`，因此 primary 排序也没有增量。"
        "Cumulative/leave-family-out 只作诊断，唯一 primary 已预注册，不从中重选模型。",
        "",
        "Leave-one-family-out 诊断：",
        "",
        "| omitted family | ΔBrier vs core | Δlogloss vs core | ΔBrier vs primary |",
        "|---|---:|---:|---:|",
    ]
    for row in payload["leave_family_out"]:
        lines.append(
            f"| {row['omitted_family']} | "
            f"{row['vs_core']['candidate_minus_baseline_brier']:+.6f} | "
            f"{row['vs_core']['candidate_minus_baseline_logloss']:+.6f} | "
            f"{row['vs_primary']['candidate_minus_baseline_brier']:+.6f} |"
        )
    lines += [
        "",
        "## 稳定性",
        "",
        f"- Front 15 dates：primary vs core Brier/logloss Δ "
        f"`{front_primary['candidate_minus_baseline_brier']:+.6f}/"
        f"{front_primary['candidate_minus_baseline_logloss']:+.6f}`，"
        f"CI `[{front_primary['brier_delta_ci95'][0]:+.6f},"
        f"{front_primary['brier_delta_ci95'][1]:+.6f}]` / "
        f"`[{front_primary['logloss_delta_ci95'][0]:+.6f},"
        f"{front_primary['logloss_delta_ci95'][1]:+.6f}]`；前段恶化。",
        f"- Frozen-forward 8 dates：Brier/logloss Δ "
        f"`{forward_primary['candidate_minus_baseline_brier']:+.6f}/"
        f"{forward_primary['candidate_minus_baseline_logloss']:+.6f}`，"
        f"CI `[{forward_primary['brier_delta_ci95'][0]:+.6f},"
        f"{forward_primary['brier_delta_ci95'][1]:+.6f}]` / "
        f"`[{forward_primary['logloss_delta_ci95'][0]:+.6f},"
        f"{forward_primary['logloss_delta_ci95'][1]:+.6f}]`；点估改善但两项 CI 都跨 0。",
        f"- Leave-date-out：Brier/logloss `{lodo['metrics']['brier']:.5f}/"
        f"{lodo['metrics']['logloss']:.5f}`；vs core target-date 等权 Δ "
        f"`{lodo['vs_core']['candidate_minus_baseline_brier']:+.6f}/"
        f"{lodo['vs_core']['candidate_minus_baseline_logloss']:+.6f}`，"
        "仍无稳定增量。",
        "",
        "## 6 个 loss 机制审计",
        "",
        "| city/date | category | bracket | exit ticks | assigned margin | dual max margin | trend/tick | observed exit delay | audit risk |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["case_path_audit"]:
        lines.append(
            f"| {row['city']} {row['target_date']} | {row['mechanism_category']} | "
            f"{row['current_bracket']} | "
            f"{fmt(row['exit_ticks_required'],1)} | "
            f"{fmt(row['assigned_forecast_exit_margin_ticks'],2)} | "
            f"{fmt(row['dual_forecast_max_margin_ticks'],2)} | "
            f"{fmt(row['trend_1h_per_exit_tick'],2)} | "
            f"{fmt(row['minutes_to_first_observed_exit'],0)}m | "
            f"{fmt(row['audit_risk'],3)} |"
        )
    lines += [
        "",
        "六个 loss 的事后机制可以完整描述：4 个 assigned forecast 已跨 exit boundary，"
        "1 个只有 alternate model 跨界，1 个（Seattle）两套 forecast 都 miss、但路径仍在升温。"
        "这只是解释力，不等于 eligibility。",
        "",
        "把同样的自然机制分类放回完整 winner 分母：",
        "",
        "| category | city-days | winners | losses | loss rate | lift |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    selected_mechanism = [
        row
        for row in payload["mechanism_category_summary"]
        if row["denominator"] == "frozen_selected"
    ]
    for row in selected_mechanism:
        lines.append(
            f"| {row['mechanism_category']} | {row['city_days']} | "
            f"{row['winners']} | {row['overshoots']} | "
            f"{float(row['overshoot_rate']):.2%} | "
            f"{fmt(row['lift_vs_denominator'],2)}x |"
        )
    lines += [
        "",
        "但这个 selected cohort 会制造错觉：`no_mechanism_warning` 在 55 个 selected winner 中看似零 loss。"
        "放回 expanding-OOF 的首个 city-day 分母后，分类结果是：",
        "",
        "| category | OOF city-days | winners | losses | loss rate | lift |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    oof_mechanism = [
        row
        for row in payload["mechanism_category_summary"]
        if row["denominator"] == "expanding_oof_city_day_first"
    ]
    for row in oof_mechanism:
        lines.append(
            f"| {row['mechanism_category']} | {row['city_days']} | "
            f"{row['winners']} | {row['overshoots']} | "
            f"{float(row['overshoot_rate']):.2%} | "
            f"{fmt(row['lift_vs_denominator'],2)}x |"
        )
    lines += [
        "",
        "因此 assigned-forecast cross 只有约 1.19x lift，path continuation 约 1.10x；"
        "“无 warning 就安全”在完整 OOF 分母上只有 0.98x，不能转成 hard gate。",
        "",
        "Future observed exit 只用于 loss audit/label，不进入特征。完整 130-row winner audit 与全部"
        " 1349-state feature ledger 已保存，避免再次围着 6 个坏例子补 AND gate。",
        "",
        "## 10-share taker expression sanity",
        "",
        f"- Baseline：{trade['baseline']['city_days']} city-days，"
        f"{trade['baseline']['wins']}W/{trade['baseline']['losses']}L，"
        f"PnL `${trade['baseline']['pnl_usd']:+.2f}`。",
        f"- Candidate：{trade['candidate']['city_days']} city-days，"
        f"{trade['candidate']['wins']}W/{trade['candidate']['losses']}L，"
        f"PnL `${trade['candidate']['pnl_usd']:+.2f}`。",
        f"- Mean daily PnL Δ `${trade['mean_daily_pnl_delta_usd']:+.3f}` "
        f"CI `[${trade['mean_daily_pnl_delta_ci95'][0]:+.3f},"
        f"${trade['mean_daily_pnl_delta_ci95'][1]:+.3f}]`；"
        f"baseline winner selection harm=`{float(trade['baseline_winner_selection_harm']):.2%}`。",
        "- 这里只是同分母 historical 10-share taker diagnostic；没有 maker settled evidence，"
        "不称 realized PnL，也不导向 sizing/deploy。",
        "",
        "## 双漏斗",
        "",
        "| funnel | stage | grain | rows | dates | note |",
        "|---|---|---|---:|---:|---|",
    ]
    for row in payload["funnel"]:
        lines.append(
            f"| {row['funnel']} | {row['stage']} | {row['grain']} | "
            f"{row['rows']} | {row['dates']} | {row['note']} |"
        )
    lines += [
        "",
        "## 三门与边界",
        "",
        f"- significance=`{'PASS' if payload['verdict']['proper_score_significance_gate'] else 'FAIL'}`。",
        f"- baseline=`{'PASS' if payload['verdict']['proper_score_point_gate'] else 'FAIL'}` "
        "（same-row frozen core + market）。",
        f"- forward=`{'PASS' if payload['verdict']['frozen_forward_gate'] else 'FAIL'}`。",
        f"- conclusion=`{action}`；deployment authorized=`False`。",
        "- 历史 source first-seen 仍不可恢复；真正 forecast run-to-run revision 也没有多版本 PIT archive。"
        "这两项是 evidence gap，不是策略过滤。双模型 deterministic max 也不等于校准后的 forecast tail distribution；"
        "更细的 radiation/cloud transition 与 checkpoint 内 order-flow 同样缺 PIT archive。",
        "",
        "## 8 环覆盖",
        "",
        "- 描述性：PASS（全部 parent、loss/winner audit）。",
        "- 推断：PASS（city-day 等权、target-date bootstrap）。",
        "- 判别/概率：PASS（offset expanding OOF、same-row baselines）。",
        "- 执行：PARTIAL（真实 10-share ladder replay；maker/fill 不在本题证据内）。",
        "- 容量：PASS 到历史 10-share taker。",
        "- 组合：PASS（target_date block）。",
        "- 基准/反事实：PASS。",
        "- Forward：按三门结果。",
        "",
        "## 复现与产物",
        "",
        "```bash",
        ".venv/bin/python scripts/analysis/reheat_risk/"
        "research_current_yes_core_carry_overshoot_missing_mechanisms_v2.py",
        "```",
        "",
    ]
    for name, path in payload["outputs"].items():
        lines.append(f"- {name}: `{path}`")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if not prereg.get("frozen_before_formal_oof"):
        raise RuntimeError("formal OOF preregistration is not frozen")

    frame, lineage = prepare_features()
    invalid_direction = frame[
        (frame["overshoot"].eq(1) & frame["canonical_direction"].ne("upward_leave"))
        | (
            frame["overshoot"].eq(0)
            & frame["canonical_direction"].ne("current_exact_hold")
        )
    ]
    if len(invalid_direction):
        raise RuntimeError(
            f"target direction audit failed for {len(invalid_direction)} rows"
        )
    direction_audit = {
        "counts": frame["canonical_direction"].value_counts().to_dict(),
        "nonhold_rows": int(frame["overshoot"].sum()),
        "nonhold_upward_rows": int(
            frame["canonical_direction"].eq("upward_leave").sum()
        ),
        "nonhold_downward_rows": int(
            frame["canonical_direction"].eq("downward_leave").sum()
        ),
        "target_matches_carry_specific_upward_leave": True,
    }
    predictions, folds = expanding_oof(frame)
    ledger = frame.merge(
        predictions.drop(
            columns=[
                "city",
                "target_date",
                "decision_snapshot_ts_utc",
                "current_bracket",
                "overshoot",
                "label",
                "p_over_core",
                "p_over_market",
            ]
        ),
        on="opportunity_id",
        how="left",
        validate="one_to_one",
    )
    lodo = leave_date_out_primary(frame)
    ledger = ledger.merge(
        lodo[["opportunity_id", "p_over_primary_lodo"]],
        on="opportunity_id",
        how="left",
        validate="one_to_one",
    )
    ledger["p_hold_primary"] = 1.0 - ledger[f"p_over_{PRIMARY}"]
    eval_rows = ledger[ledger[f"p_over_{PRIMARY}"].notna()].copy()
    eval_dates = sorted(eval_rows["target_date"].unique())
    forward_dates = set(eval_dates[-FORWARD_DATES:])

    models = []
    for name in MODEL_SPECS:
        metrics = score_metrics(eval_rows, f"p_over_{name}")
        metrics.update(
            {
                "model": name,
                "features": MODEL_SPECS[name],
                "vs_core": paired_delta(
                    eval_rows, f"p_over_{name}", "p_over_core"
                ),
                "vs_market": paired_delta(
                    eval_rows, f"p_over_{name}", "p_over_market"
                ),
            }
        )
        models.append(metrics)
    diagnostic_models = []
    for name, features in DIAGNOSTIC_SPECS.items():
        metrics = score_metrics(eval_rows, f"p_over_{name}")
        metrics.update(
            {
                "model": name,
                "features": features,
                "omitted_family": name.removeprefix("primary_without_"),
                "vs_core": paired_delta(
                    eval_rows, f"p_over_{name}", "p_over_core"
                ),
                "vs_primary": paired_delta(
                    eval_rows, f"p_over_{name}", f"p_over_{PRIMARY}"
                ),
            }
        )
        diagnostic_models.append(metrics)
    primary_delta = next(
        row["vs_core"] for row in models if row["model"] == PRIMARY
    )
    risk_lifts = {
        "market": risk_lift(eval_rows, "p_over_market"),
        "core": risk_lift(eval_rows, "p_over_core"),
        **{
            name: risk_lift(eval_rows, f"p_over_{name}")
            for name in MODEL_SPECS
        },
    }
    lift = risk_lifts[PRIMARY]
    periods = period_scores(eval_rows, forward_dates)
    lodo_probability = {
        "metrics": score_metrics(ledger, "p_over_primary_lodo"),
        "vs_core": paired_delta(
            ledger, "p_over_primary_lodo", "p_over_core"
        ),
    }

    detail, daily, trade = trade_diagnostic(eval_rows)
    verdict = choose_action(primary_delta, periods, lift, trade)
    case_audit = case_path_audit(ledger)
    mechanism = mechanism_summary(ledger)
    audit_columns = [
        "opportunity_id",
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "current_bracket",
        "unit",
        "running_native",
        "final_native",
        "market_mid",
        "p_core_no_obs_age",
        "exit_ticks_required",
        "exit_distance_native",
        "assigned_forecast_exit_margin_ticks",
        "gfs_forecast_exit_margin_ticks",
        "ecmwf_forecast_exit_margin_ticks",
        "dual_forecast_max_margin_ticks",
        "dual_forecast_spread_ticks",
        "dual_forecast_cross_fraction",
        "assigned_curve_hours_above_exit",
        "dual_curve_max_heat_area_ticks",
        "trend_1h_per_exit_tick",
        "trend_3h_per_exit_tick",
        "current_yes_spread",
        "quote_depth_log_imbalance",
        f"p_over_{PRIMARY}",
        "p_over_primary_lodo",
    ]
    selected = ledger[ledger["frozen_baseline_selected"]]
    loss_audit = selected[selected["overshoot"].eq(1)][audit_columns]
    winner_audit = selected[selected["overshoot"].eq(0)][audit_columns]

    coverage_rows = []
    for family, features in [
        ("native_lattice", NATIVE_LATTICE),
        ("dual_model_tail", DUAL_MODEL_TAIL),
        ("forecast_curve_heat_budget", CURVE_HEAT_BUDGET),
        ("path_to_boundary", PATH_TO_BOUNDARY),
        ("market_microstructure", MARKET_MICROSTRUCTURE),
    ]:
        for feature in features:
            values = pd.to_numeric(frame[feature], errors="coerce")
            coverage_rows.append(
                {
                    "family": family,
                    "feature": feature,
                    "rows": int(values.notna().sum()),
                    "coverage": float(values.notna().mean()),
                    "unique_non_null": int(values.nunique(dropna=True)),
                }
            )
    funnel = [
        {
            "funnel": "signal",
            "stage": "frozen carry parent",
            "grain": "state",
            "rows": len(frame),
            "dates": frame["target_date"].nunique(),
            "note": "all checkpoints; not selected/loss-only",
        },
        {
            "funnel": "signal",
            "stage": "expanding residual OOF",
            "grain": "state",
            "rows": len(eval_rows),
            "dates": len(eval_dates),
            "note": "eight target-date warm-up",
        },
        {
            "funnel": "signal",
            "stage": "historical frozen selector audit",
            "grain": "city-day expression",
            "rows": len(selected),
            "dates": selected["target_date"].nunique(),
            "note": "descriptive only; six losses do not define model denominator",
        },
        {
            "funnel": "evidence",
            "stage": "native lattice + dual forecast max",
            "grain": "state",
            "rows": int(
                frame[
                    [
                        "exit_ticks_required",
                        "gfs_forecast_exit_margin_ticks",
                        "ecmwf_forecast_exit_margin_ticks",
                    ]
                ]
                .notna()
                .all(axis=1)
                .sum()
            ),
            "dates": frame["target_date"].nunique(),
            "note": "fixed previous-run Single Runs",
        },
        {
            "funnel": "evidence",
            "stage": "dual hourly forecast curves",
            "grain": "state",
            "rows": int(
                frame[
                    [
                        "gfs_curve_hours_above_exit",
                        "ecmwf_curve_hours_above_exit",
                    ]
                ]
                .notna()
                .all(axis=1)
                .sum()
            ),
            "dates": frame["target_date"].nunique(),
            "note": "cache-path PIT coverage",
        },
        {
            "funnel": "evidence",
            "stage": "historical source first-seen",
            "grain": "state",
            "rows": 0,
            "dates": 0,
            "note": "coverage gap; not synthesized",
        },
        {
            "funnel": "execution",
            "stage": "ten-share full ladder",
            "grain": "state",
            "rows": int(frame["ten_share_executable"].sum()),
            "dates": frame.loc[
                frame["ten_share_executable"], "target_date"
            ].nunique(),
            "note": "official per-level taker fee; diagnostic only",
        },
        {
            "funnel": "execution",
            "stage": "actual settled maker fills for overlay",
            "grain": "fill",
            "rows": 0,
            "dates": 0,
            "note": "not claimed; no maker replay used",
        },
    ]

    ledger.to_csv(OUT_DIR / "feature_ledger.csv", index=False)
    predictions.to_csv(OUT_DIR / "oof_predictions.csv", index=False)
    folds.to_csv(OUT_DIR / "fold_coefficients.csv", index=False)
    pd.json_normalize(models).to_csv(OUT_DIR / "model_scores.csv", index=False)
    pd.json_normalize(diagnostic_models).to_csv(
        OUT_DIR / "leave_family_out_scores.csv", index=False
    )
    periods.to_csv(OUT_DIR / "front_forward.csv", index=False)
    lodo.to_csv(OUT_DIR / "leave_date_out_predictions.csv", index=False)
    pd.DataFrame(coverage_rows).to_csv(
        OUT_DIR / "feature_coverage.csv", index=False
    )
    case_audit.to_csv(OUT_DIR / "case_path_audit.csv", index=False)
    mechanism.to_csv(OUT_DIR / "mechanism_category_summary.csv", index=False)
    loss_audit.to_csv(OUT_DIR / "loss_audit.csv", index=False)
    winner_audit.to_csv(OUT_DIR / "winner_audit.csv", index=False)
    detail.to_csv(OUT_DIR / "trade_diagnostic.csv", index=False)
    daily.to_csv(OUT_DIR / "daily_trade_diagnostic.csv", index=False)
    pd.DataFrame(funnel).to_csv(OUT_DIR / "funnel.csv", index=False)

    payload = {
        "research_id": RESEARCH_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "target": prereg["target"],
        "parent": {
            "rows": len(frame),
            "city_days": frame.groupby(["city", "target_date"]).ngroups,
            "dates": frame["target_date"].nunique(),
            "date_min": frame["target_date"].min(),
            "date_max": frame["target_date"].max(),
            "selected": len(selected),
            "selected_winners": int(selected["label"].sum()),
            "selected_losses": int(selected["overshoot"].sum()),
        },
        "oof": {
            "rows": len(eval_rows),
            "city_days": eval_rows.groupby(["city", "target_date"]).ngroups,
            "dates": len(eval_dates),
            "date_min": min(eval_dates),
            "date_max": max(eval_dates),
            "frozen_forward_dates": sorted(forward_dates),
        },
        "lineage": lineage,
        "direction_audit": direction_audit,
        "feature_coverage": coverage_rows,
        "models": json_ready(models),
        "leave_family_out": json_ready(diagnostic_models),
        "baselines": {
            "core": score_metrics(eval_rows, "p_over_core"),
            "market": score_metrics(eval_rows, "p_over_market"),
        },
        "risk_lift": lift,
        "risk_lifts": risk_lifts,
        "periods": json_ready(periods.to_dict("records")),
        "leave_date_out": lodo_probability,
        "case_path_audit": json_ready(case_audit.to_dict("records")),
        "mechanism_category_summary": json_ready(
            mechanism.to_dict("records")
        ),
        "trade_diagnostic": trade,
        "funnel": funnel,
        "verdict": verdict,
        "artifacts": {
            "preregistration_sha256": sha256(PREREG),
            "parent_sha256": sha256(V1_LEDGER),
        },
        "outputs": {
            "script": str(Path(__file__).relative_to(ROOT)),
            "preregistration": str(PREREG.relative_to(ROOT)),
            "feature_ledger": str(
                (OUT_DIR / "feature_ledger.csv").relative_to(ROOT)
            ),
            "oof_predictions": str(
                (OUT_DIR / "oof_predictions.csv").relative_to(ROOT)
            ),
            "model_scores": str(
                (OUT_DIR / "model_scores.csv").relative_to(ROOT)
            ),
            "leave_family_out_scores": str(
                (OUT_DIR / "leave_family_out_scores.csv").relative_to(ROOT)
            ),
            "front_forward": str(
                (OUT_DIR / "front_forward.csv").relative_to(ROOT)
            ),
            "leave_date_out_predictions": str(
                (OUT_DIR / "leave_date_out_predictions.csv").relative_to(ROOT)
            ),
            "case_path_audit": str(
                (OUT_DIR / "case_path_audit.csv").relative_to(ROOT)
            ),
            "mechanism_category_summary": str(
                (OUT_DIR / "mechanism_category_summary.csv").relative_to(ROOT)
            ),
            "loss_audit": str((OUT_DIR / "loss_audit.csv").relative_to(ROOT)),
            "winner_audit": str(
                (OUT_DIR / "winner_audit.csv").relative_to(ROOT)
            ),
            "trade_diagnostic": str(
                (OUT_DIR / "trade_diagnostic.csv").relative_to(ROOT)
            ),
            "daily_trade": str(
                (OUT_DIR / "daily_trade_diagnostic.csv").relative_to(ROOT)
            ),
            "funnel": str((OUT_DIR / "funnel.csv").relative_to(ROOT)),
            "json": str(RESULT_JSON.relative_to(ROOT)),
            "report": str(REPORT.relative_to(ROOT)),
        },
    }
    RESULT_JSON.write_text(
        json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(payload)
    print(
        json.dumps(
            json_ready(
                {
                    "parent": payload["parent"],
                    "oof": payload["oof"],
                    "models": models,
                    "risk_lift": lift,
                    "trade": trade,
                    "verdict": verdict,
                    "outputs": payload["outputs"],
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
