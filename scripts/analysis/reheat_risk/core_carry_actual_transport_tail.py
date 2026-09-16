#!/usr/bin/env python3
"""Actual-METAR transition challenger and Core Carry tail-risk overlay.

This is a research-only replay.  It joins report-time IEM/METAR observations
as-of each frozen decision and evaluates whether actual cloud/rain/dewpoint and
directional transport transitions materially concentrate upward exits.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk.validate_reheat_tail_feature_discrimination_v1 import (  # noqa: E402
    ICAO_COORDS,
)
from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)
from weather_data_feed.sky_cover import SKY_COVER_CODE  # noqa: E402


RESEARCH_ID = "current_yes_core_carry_actual_transport_tail_v1"
RUN_ID = "iem_actual_transition_monotone_tail_20260807"
FEATURE_LEDGER = ROOT / (
    "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_overshoot_missing_mechanisms_v2/feature_ledger.csv"
)
STATION_SUMMARY = ROOT / (
    "docs/analysis/2026-06/generated/theta_no_wu_obs_patch_v1/summary.json"
)
IEM_BASE_DIR = Path(
    "/Volumes/jrs-archive/pm_agents/research/artifact_store/active/"
    "core_carry_actual_transport_v1/raw_cache/iem_ext"
)
IEM_AUGMENTED = ROOT / (
    "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_physical_semantic_audit_v2/iem_augmented_weather.csv"
)
PREREG = ROOT / (
    "docs/analysis/2026-08/"
    "2026-08-07-current-yes-core-carry-actual-transport-tail-"
    "preregistration.json"
)
REPORT = ROOT / (
    "docs/analysis/2026-08/"
    "2026-08-07-current-yes-core-carry-actual-transport-tail-v1.md"
)
RESULT = REPORT.with_suffix(".json")

SEED = 20260807
WARMUP_DATES = 8
FORWARD_DATES = 8
L2 = 0.2
EPS = 1e-6
TAIL_QUANTILE = 0.80
BOOTSTRAP_REPS = 5000

FEATURES = [
    "forecast_exit_pressure",
    "fresh_high_heat",
    "warm_moist_transport",
    "hemisphere_warm_transport",
    "dewpoint_surge_heat",
    "dry_warming_transport",
    "clearing_reheat",
    "frontal_warm_transition",
    "cold_transport_protection",
    "cloud_cooling_protection",
    "rain_cooling_protection",
    "gust_warming_transport",
    "falling_pressure_warm_transition",
]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def station_contract() -> tuple[dict[str, str], dict[str, float]]:
    payload = json.loads(STATION_SUMMARY.read_text(encoding="utf-8"))
    station = {str(row["city"]): str(row["icao"]).upper() for row in payload["stations"]}
    latitude = {city: float(ICAO_COORDS[icao][0]) for city, icao in station.items()}
    return station, latitude


def load_iem_histories() -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    stations, _ = station_contract()
    inverse = {icao: city for city, icao in stations.items()}
    frames: list[pd.DataFrame] = []
    files: list[Path] = []
    for icao, city in sorted(inverse.items()):
        paths = sorted(IEM_BASE_DIR.glob(f"iem_ext_{icao}_*.csv"))
        if len(paths) != 1:
            raise RuntimeError(f"expected one IEM base file for {city}/{icao}, got {paths}")
        path = paths[0]
        frame = pd.read_csv(path, low_memory=False, na_values=["M"])
        frame["city"] = city
        frames.append(frame)
        files.append(path)
    base = pd.concat(frames, ignore_index=True)
    base["valid_utc"] = pd.to_datetime(base["valid"], utc=True, errors="coerce")
    for column in ["tmpf", "dwpf", "relh", "drct", "sknt"]:
        base[column] = pd.to_numeric(base[column], errors="coerce")
    base["sky_level"] = base["skyc1"].astype(str).str.upper().map(SKY_COVER_CODE)

    augmented = pd.read_csv(IEM_AUGMENTED, low_memory=False, na_values=["M"])
    augmented["valid_utc"] = pd.to_datetime(augmented["valid"], utc=True, errors="coerce")
    for column in ["gust", "p01i", "alti"]:
        augmented[column] = pd.to_numeric(augmented[column], errors="coerce")
    augmented = augmented[
        ["city", "valid_utc", "wxcodes", "gust", "p01i", "alti"]
    ].drop_duplicates(["city", "valid_utc"], keep="last")
    merged = base.merge(
        augmented,
        on=["city", "valid_utc"],
        how="left",
        validate="many_to_one",
    )
    histories = {
        city: group.sort_values("valid_utc").drop_duplicates("valid_utc", keep="last")
        for city, group in merged.groupby("city")
    }
    return histories, {
        "stations": len(histories),
        "base_rows": len(base),
        "merged_rows": len(merged),
        "base_files": len(files),
        "base_sha256": {path.name: file_sha256(path) for path in files},
        "augmented_sha256": file_sha256(IEM_AUGMENTED),
        "pit_limit": "report-time observation timestamps; original first-seen ingestion latency unavailable",
    }


def asof_row(history: pd.DataFrame, timestamp: pd.Timestamp, tolerance_min: int = 90) -> pd.Series | None:
    eligible = history[history["valid_utc"].le(timestamp)]
    if eligible.empty:
        return None
    row = eligible.iloc[-1]
    age = (timestamp - row["valid_utc"]).total_seconds() / 60.0
    return row if age <= tolerance_min else None


def circular_difference(left: float, right: float) -> float:
    return abs((float(left) - float(right) + 180.0) % 360.0 - 180.0)


def direction_persistence(history: pd.DataFrame, decision: pd.Timestamp) -> float:
    recent = history[history["valid_utc"].between(decision - pd.Timedelta(hours=3), decision)]
    directions = pd.to_numeric(recent["drct"], errors="coerce").dropna()
    if len(directions) < 2:
        return math.nan
    radians = np.deg2rad(directions.to_numpy(float))
    return float(math.hypot(np.sin(radians).mean(), np.cos(radians).mean()))


def has_precipitation(text: str) -> bool:
    tokens = re.sub(r"[+-]", " ", str(text).upper()).split()
    return any(any(code in token for code in ("RA", "DZ", "SH", "TS", "SN")) for token in tokens)


def actual_transition_row(
    history: pd.DataFrame,
    decision: pd.Timestamp,
    latitude: float,
    parent: pd.Series,
) -> dict[str, Any]:
    now = asof_row(history, decision)
    lag1 = asof_row(history, decision - pd.Timedelta(hours=1))
    lag3 = asof_row(history, decision - pd.Timedelta(hours=3))
    if now is None or lag1 is None or lag3 is None:
        return {feature: math.nan for feature in FEATURES} | {"actual_transition_status": "missing_asof_path"}

    def value(row: pd.Series, column: str) -> float:
        raw = row.get(column)
        return float(raw) if pd.notna(raw) else math.nan

    temp1 = value(now, "tmpf") - value(lag1, "tmpf")
    temp3 = value(now, "tmpf") - value(lag3, "tmpf")
    dew1 = value(now, "dwpf") - value(lag1, "dwpf")
    dew3 = value(now, "dwpf") - value(lag3, "dwpf")
    wind = value(now, "sknt")
    direction = value(now, "drct")
    old_direction = value(lag3, "drct")
    sky_now = value(now, "sky_level")
    sky_old = value(lag3, "sky_level")
    sky_delta = sky_now - sky_old if math.isfinite(sky_now) and math.isfinite(sky_old) else math.nan
    persistence = direction_persistence(history, decision)
    shift = (
        circular_difference(direction, old_direction)
        if math.isfinite(direction) and math.isfinite(old_direction)
        else math.nan
    )
    recent = history[history["valid_utc"].between(decision - pd.Timedelta(hours=1), decision)]
    wx_text = " ".join(recent["wxcodes"].dropna().astype(str).tolist())
    precip_amount = pd.to_numeric(recent["p01i"], errors="coerce").fillna(0)
    precip = float(has_precipitation(wx_text) or (len(precip_amount) and precip_amount.max() > 0))
    gust = pd.to_numeric(recent["gust"], errors="coerce").max()
    gust_excess = max(0.0, float(gust) - wind) if pd.notna(gust) and math.isfinite(wind) else math.nan
    alti_now, alti_old = value(now, "alti"), value(lag3, "alti")
    pressure3 = (alti_now - alti_old) * 33.8639 if math.isfinite(alti_now) and math.isfinite(alti_old) else math.nan

    wind_factor = max(0.0, wind - 5.0) / 15.0 if math.isfinite(wind) else math.nan
    persistence_value = persistence if math.isfinite(persistence) else math.nan
    warm_component = (
        -math.cos(math.radians(direction)) * (1.0 if latitude >= 0 else -1.0)
        if math.isfinite(direction)
        else math.nan
    )
    solar = max(0.0, float(parent.get("solar_elevation_deg") or 0.0)) / 90.0
    daylight_h = max(0.0, float(parent.get("daylight_remaining_minutes") or 0.0)) / 60.0
    heat = solar * math.sqrt(min(daylight_h, 15.0))
    age_h = max(0.0, float(parent.get("minutes_since_last_strict_new_high") or 0.0)) / 60.0
    fresh = math.exp(-age_h / 1.5)
    dpd = max(0.0, value(now, "tmpf") - value(now, "dwpf"))

    def product(*items: float) -> float:
        return float(np.prod(items)) if all(math.isfinite(item) for item in items) else math.nan

    return {
        "actual_transition_status": "ok_report_time_proxy",
        "forecast_exit_pressure": float(np.clip(parent["assigned_forecast_exit_margin_ticks"], -10, 10)),
        "fresh_high_heat": fresh * heat,
        "warm_moist_transport": product(wind_factor, persistence_value, max(0.0, dew3) / 3.6, math.exp(-max(0.0, -temp3) / 1.8)),
        "hemisphere_warm_transport": product(wind_factor, persistence_value, max(0.0, warm_component), 1.0 + max(0.0, dew3) / 3.6, math.exp(-max(0.0, -temp3) / 1.8)),
        "dewpoint_surge_heat": max(0.0, dew1) / 1.8 * heat,
        "dry_warming_transport": product(wind_factor, max(0.0, temp3) / 3.6, max(0.0, -dew3) / 3.6),
        "clearing_reheat": max(0.0, -sky_delta) * heat * (1.0 + max(0.0, temp1) / 1.8) if math.isfinite(sky_delta) else math.nan,
        "frontal_warm_transition": (shift / 90.0) * (max(0.0, temp3) + max(0.0, dew3)) / 3.6 if math.isfinite(shift) else math.nan,
        "cold_transport_protection": -product(wind_factor, persistence_value, max(0.0, -temp3) / 3.6, 1.0 + max(0.0, -dew3) / 3.6),
        "cloud_cooling_protection": -max(0.0, sky_delta) * max(0.0, -temp1) / 1.8 * (1.0 + precip) if math.isfinite(sky_delta) else math.nan,
        "rain_cooling_protection": -precip * max(0.0, -temp1) / 1.8 * (1.0 + dpd / 18.0),
        "gust_warming_transport": product((gust_excess / 15.0) if math.isfinite(gust_excess) else math.nan, max(0.0, temp1) / 1.8, heat),
        "falling_pressure_warm_transition": max(0.0, -pressure3) / 3.0 * (max(0.0, temp3) + max(0.0, dew3)) / 3.6 if math.isfinite(pressure3) else math.nan,
        "actual_temp_1h_f": temp1,
        "actual_temp_3h_f": temp3,
        "actual_dewpoint_1h_f": dew1,
        "actual_dewpoint_3h_f": dew3,
        "actual_wind_direction_deg": direction,
        "actual_wind_speed_kt": wind,
        "actual_wind_persistence_3h": persistence,
        "actual_wind_shift_3h_deg": shift,
        "actual_sky_level": sky_now,
        "actual_sky_delta_3h": sky_delta,
        "actual_precip_1h": precip,
        "actual_gust_excess_kt": gust_excess,
        "actual_pressure_3h_hpa": pressure3,
    }


def build_frame() -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = pd.read_csv(FEATURE_LEDGER, low_memory=False)
    frame["target_date"] = frame["target_date"].astype(str)
    frame["current_bracket"] = frame["current_bracket"].astype(str)
    frame["decision_dt"] = pd.to_datetime(frame["decision_snapshot_ts_utc"], utc=True, errors="raise")
    frame["overshoot"] = frame["overshoot"].astype(int)
    frame["p_over_core"] = frame["p_over_core"].clip(EPS, 1 - EPS)
    frame["base_logit"] = np.log(frame["p_over_core"] / (1 - frame["p_over_core"]))
    histories, lineage = load_iem_histories()
    _, latitudes = station_contract()
    rows = [
        actual_transition_row(
            histories[str(row["city"])],
            row["decision_dt"],
            latitudes[str(row["city"])],
            row,
        )
        for _, row in frame.iterrows()
    ]
    enriched = pd.concat([frame.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    return enriched.sort_values(["target_date", "city", "current_bracket", "decision_dt"]).reset_index(drop=True), lineage


def state_weights(frame: pd.DataFrame) -> np.ndarray:
    keys = ["city", "target_date", "current_bracket"]
    row_count = frame.groupby(keys)["overshoot"].transform("size").to_numpy(float)
    states = frame[keys].drop_duplicates().groupby("target_date").size().to_dict()
    dates = frame["target_date"].nunique()
    weights = np.array([1.0 / (dates * states[date] * count) for date, count in zip(frame["target_date"], row_count, strict=True)])
    return weights / weights.sum()


def transform(train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    train_parts: list[np.ndarray] = []
    test_parts: list[np.ndarray] = []
    names: list[str] = []
    metadata: dict[str, Any] = {}
    for feature in FEATURES:
        a = pd.to_numeric(train[feature], errors="coerce")
        b = pd.to_numeric(test[feature], errors="coerce")
        median = float(a.median()) if a.notna().any() else 0.0
        av, bv = a.fillna(median).to_numpy(float), b.fillna(median).to_numpy(float)
        mean, scale = float(av.mean()), float(av.std())
        if scale < 1e-9:
            continue
        train_parts.append((av - mean) / scale)
        test_parts.append((bv - mean) / scale)
        names.append(feature)
        metadata[feature] = {"median": median, "mean": mean, "scale": scale}
    return np.column_stack(train_parts), np.column_stack(test_parts), {"columns": names, "transforms": metadata}


def fit_predict(train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, dict[str, Any]]:
    x_train, x_test, meta = transform(train, test)
    x_train = np.column_stack([np.ones(len(train)), x_train])
    x_test = np.column_stack([np.ones(len(test)), x_test])
    y = train["overshoot"].to_numpy(float)
    weights = state_weights(train)
    offset = train["base_logit"].to_numpy(float)

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        eta = offset + x_train @ beta
        probability = expit(eta)
        penalty = 0.01 * beta[0] ** 2 + L2 * np.sum(beta[1:] ** 2)
        loss = float(np.sum(weights * (np.logaddexp(0, eta) - y * eta)) + 0.5 * penalty)
        gradient = x_train.T @ (weights * (probability - y))
        gradient[0] += 0.01 * beta[0]
        gradient[1:] += L2 * beta[1:]
        return loss, gradient

    fit = minimize(
        lambda beta: objective(beta),
        np.zeros(x_train.shape[1]),
        jac=True,
        bounds=[(None, None), *[(0.0, None)] * (x_train.shape[1] - 1)],
        method="L-BFGS-B",
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    if not fit.success:
        raise RuntimeError(f"monotone residual fit failed: {fit.message}")
    prediction = expit(test["base_logit"].to_numpy(float) + x_test @ fit.x)
    columns = ["calibration_intercept", *meta["columns"]]
    meta.update({
        "columns": columns,
        "coefficients": fit.x.tolist(),
        "coefficient_map": dict(zip(columns, fit.x.tolist(), strict=True)),
        "l2": L2,
        "train_rows": len(train),
        "train_dates": train["target_date"].nunique(),
    })
    return np.clip(prediction, EPS, 1 - EPS), meta


def collapse_states(frame: pd.DataFrame, probability: str, threshold: str | None = None) -> pd.DataFrame:
    keys = ["city", "target_date", "current_bracket"]
    labels = frame.groupby(keys)["overshoot"].nunique()
    if int(labels.max()) != 1:
        raise ValueError("conflicting state-entry labels")
    aggregations: dict[str, tuple[str, str]] = {
        "overshoot": ("overshoot", "first"),
        "probability": (probability, "mean"),
    }
    if threshold:
        aggregations["threshold"] = (threshold, "mean")
    return frame.groupby(keys, as_index=False).agg(**aggregations)


def probability_metrics(frame: pd.DataFrame, probability: str) -> dict[str, Any]:
    states = collapse_states(frame, probability)
    y = states["overshoot"].to_numpy(float)
    p = states["probability"].clip(EPS, 1 - EPS).to_numpy(float)
    states = states.assign(
        brier=(y - p) ** 2,
        logloss=-(y * np.log(p) + (1 - y) * np.log(1 - p)),
    )
    daily = states.groupby("target_date", as_index=False).agg(
        brier=("brier", "mean"), logloss=("logloss", "mean")
    )
    return {"state_entries": len(states), "dates": len(daily), "brier": float(daily.brier.mean()), "logloss": float(daily.logloss.mean())}


def tail_summary(frame: pd.DataFrame, probability: str, threshold: str) -> dict[str, Any]:
    states = collapse_states(frame, probability, threshold)
    high = states["probability"].ge(states["threshold"])
    losses = states["overshoot"].eq(1)
    winners = ~losses
    selected_fraction = float(high.mean())
    overall_rate = float(losses.mean())
    high_rate = float(losses[high].mean()) if high.any() else 0.0
    return {
        "state_entries": len(states),
        "high_risk_entries": int(high.sum()),
        "selected_fraction": selected_fraction,
        "overshoots": int(losses.sum()),
        "overshoots_captured": int((high & losses).sum()),
        "overshoot_recall": float((high & losses).sum() / max(1, losses.sum())),
        "winner_false_positive_rate": float((high & winners).sum() / max(1, winners.sum())),
        "overall_overshoot_rate": overall_rate,
        "high_risk_overshoot_rate": high_rate,
        "lift": high_rate / overall_rate if overall_rate else 0.0,
    }


def selected_policy(frame: pd.DataFrame) -> dict[str, Any]:
    selected = frame[frame["frozen_baseline_selected"].astype(bool)].copy()
    if selected.empty:
        return {"entries": 0}
    cost = selected["ten_share_cost_per_share"].to_numpy(float)
    win = selected["label"].to_numpy(int).astype(bool)
    fixed_pnl = np.where(win, 10 * (1 - cost), -10 * cost)
    challenger_high = selected["p_challenger"].ge(selected["challenger_threshold"]).to_numpy()
    core_high = selected["p_over_core"].ge(selected["core_threshold"]).to_numpy()

    def overlay(high: np.ndarray) -> dict[str, Any]:
        shares = np.where(high, 5, 10)
        pnl = np.where(win, shares * (1 - cost), -shares * cost)
        loss_capital_saved = float(np.sum(np.where(high & ~win, 5 * cost, 0)))
        winner_profit_sacrificed = float(np.sum(np.where(high & win, 5 * (1 - cost), 0)))
        return {
            "downsize_entries": int(high.sum()),
            "downsize_losses": int((high & ~win).sum()),
            "downsize_winners": int((high & win).sum()),
            "pnl": float(pnl.sum()),
            "pnl_delta_vs_fixed10": float(pnl.sum() - fixed_pnl.sum()),
            "loss_capital_saved": loss_capital_saved,
            "winner_profit_sacrificed": winner_profit_sacrificed,
            "net_tail_value": loss_capital_saved - winner_profit_sacrificed,
        }

    return {
        "entries": len(selected),
        "dates": selected["target_date"].nunique(),
        "losses": int((~win).sum()),
        "fixed10_pnl": float(fixed_pnl.sum()),
        "challenger_overlay": overlay(challenger_high),
        "core_risk_overlay": overlay(core_high),
    }


def expanding_oof(frame: pd.DataFrame) -> pd.DataFrame:
    dates = sorted(frame["target_date"].unique())
    rows: list[pd.DataFrame] = []
    for index, target_date in enumerate(dates):
        if index < WARMUP_DATES:
            continue
        train = frame[frame["target_date"].lt(target_date)]
        test = frame[frame["target_date"].eq(target_date)].copy()
        test["p_challenger"], _ = fit_predict(train, test)
        train_scored = train.copy()
        train_scored["p_challenger"], _ = fit_predict(train, train)
        train_selected = train_scored[train_scored["frozen_baseline_selected"].astype(bool)]
        threshold_source = train_selected if len(train_selected) >= 10 else train_scored
        test["challenger_threshold"] = float(threshold_source["p_challenger"].quantile(TAIL_QUANTILE))
        core_source = train_selected if len(train_selected) >= 10 else train_scored
        test["core_threshold"] = float(core_source["p_over_core"].quantile(TAIL_QUANTILE))
        rows.append(test)
    return pd.concat(rows, ignore_index=True)


def daily_probability_delta(frame: pd.DataFrame) -> pd.DataFrame:
    challenger = collapse_states(frame, "p_challenger")
    core = collapse_states(frame, "p_over_core")
    keys = ["city", "target_date", "current_bracket"]
    merged = challenger.merge(core[keys + ["probability"]], on=keys, suffixes=("_challenger", "_core"))
    y = merged["overshoot"].to_numpy(float)
    pc = merged["probability_challenger"].clip(EPS, 1 - EPS).to_numpy(float)
    pb = merged["probability_core"].clip(EPS, 1 - EPS).to_numpy(float)
    merged["brier_delta"] = (y - pc) ** 2 - (y - pb) ** 2
    merged["logloss_delta"] = -(y * np.log(pc) + (1 - y) * np.log(1 - pc)) + y * np.log(pb) + (1 - y) * np.log(1 - pb)
    return merged.groupby("target_date", as_index=False).agg(brier_delta=("brier_delta", "mean"), logloss_delta=("logloss_delta", "mean"))


def bootstrap(daily: pd.DataFrame) -> dict[str, Any]:
    rng = np.random.default_rng(SEED)
    indices = rng.integers(0, len(daily), size=(BOOTSTRAP_REPS, len(daily)))
    output: dict[str, Any] = {"dates": len(daily)}
    for metric in ["brier_delta", "logloss_delta"]:
        values = daily[metric].to_numpy(float)
        draws = values[indices].mean(axis=1)
        output[metric] = {"mean": float(values.mean()), "ci95": [float(np.quantile(draws, .025)), float(np.quantile(draws, .975))]}
    return output


def materiality_checks(
    challenger_tail: dict[str, Any],
    core_tail: dict[str, Any],
    challenger_policy: dict[str, Any],
    core_policy: dict[str, Any],
) -> dict[str, bool]:
    absolute = (
        challenger_tail["lift"] >= 1.5
        and challenger_policy["downsize_losses"] >= 1
        and challenger_policy["net_tail_value"] > 0
    )
    incremental_tail = (
        challenger_tail["overshoots_captured"]
        >= core_tail["overshoots_captured"] + 1
        and challenger_tail["lift"] >= core_tail["lift"] + 0.10
    )
    incremental_policy = (
        challenger_policy["net_tail_value"]
        >= core_policy["net_tail_value"] + 0.50
    )
    return {
        "absolute_tail_pass": absolute,
        "incremental_tail_vs_core_pass": incremental_tail,
        "incremental_policy_vs_core_pass": incremental_policy,
    }


def main() -> int:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if not prereg.get("frozen_before_formal_run"):
        raise RuntimeError("preregistration is not frozen")
    frame, lineage = build_frame()
    if len(frame) != 1349 or frame["target_date"].nunique() != 31:
        raise RuntimeError("frozen denominator drift")
    oof = expanding_oof(frame)
    dates = sorted(frame["target_date"].unique())
    forward_dates = dates[-FORWARD_DATES:]
    development = oof[~oof["target_date"].isin(forward_dates)].copy()
    forward = oof[oof["target_date"].isin(forward_dates)].copy()
    final_train = frame[~frame["target_date"].isin(forward_dates)]
    _, frozen_fit = fit_predict(final_train, forward)

    out_dir = resolve_run_output(RESEARCH_ID, run_id=RUN_ID, explicit_output=None)
    prepare_new_run_output(out_dir)
    frame.to_csv(out_dir / "actual_transition_feature_ledger.csv", index=False)
    oof.to_csv(out_dir / "expanding_oof_predictions.csv", index=False)
    (out_dir / "frozen_model.json").write_text(json.dumps(json_ready(frozen_fit), indent=2) + "\n", encoding="utf-8")
    daily = daily_probability_delta(forward)
    daily.to_csv(out_dir / "forward_daily_probability_delta.csv", index=False)

    payload = {
        "schema_version": RESEARCH_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_id": RESEARCH_ID,
        "run_id": RUN_ID,
        "denominator": {
            "checkpoints": len(frame),
            "state_entries": len(collapse_states(frame, "p_over_core")),
            "cities": frame["city"].nunique(),
            "dates": len(dates),
            "date_min": dates[0],
            "date_max": dates[-1],
            "selected_entries": int(frame["frozen_baseline_selected"].sum()),
            "selected_losses": int(frame.loc[frame["frozen_baseline_selected"].astype(bool), "overshoot"].sum()),
        },
        "coverage": {
            "complete_actual_transition": float(frame["actual_transition_status"].eq("ok_report_time_proxy").mean()),
            **{feature: float(pd.to_numeric(frame[feature], errors="coerce").notna().mean()) for feature in FEATURES},
        },
        "lineage": lineage,
        "development_dates": sorted(development["target_date"].unique()),
        "forward_dates": forward_dates,
        "development": {
            "tail_challenger": tail_summary(development, "p_challenger", "challenger_threshold"),
            "tail_core": tail_summary(development, "p_over_core", "core_threshold"),
            "policy": selected_policy(development),
            "probability_challenger": probability_metrics(development, "p_challenger"),
            "probability_core": probability_metrics(development, "p_over_core"),
        },
        "forward": {
            "tail_challenger": tail_summary(forward, "p_challenger", "challenger_threshold"),
            "tail_core": tail_summary(forward, "p_over_core", "core_threshold"),
            "policy": selected_policy(forward),
            "probability_challenger": probability_metrics(forward, "p_challenger"),
            "probability_core": probability_metrics(forward, "p_over_core"),
            "probability_delta_bootstrap": bootstrap(daily),
        },
        "frozen_fit": frozen_fit,
        "limitations": [
            "Historical METAR report timestamps are PIT by observation time but original collector first-seen latency is unavailable.",
            "Gust values are sparse and missingness is not treated as no gust.",
            "Hemisphere warm-direction is a coarse transport prior, not a city terrain model.",
            "Only six frozen selected losses exist; sizing PnL is descriptive and cannot authorize live changes.",
        ],
        "live_action": "none",
        "artifacts": {"artifact_dir": str(out_dir), "preregistration": str(PREREG.relative_to(ROOT))},
    }
    ft = payload["forward"]["tail_challenger"]
    fc = payload["forward"]["tail_core"]
    fp = payload["forward"]["policy"]["challenger_overlay"]
    fcp = payload["forward"]["policy"]["core_risk_overlay"]
    checks = materiality_checks(ft, fc, fp, fcp)
    material = all(checks.values())
    payload["materiality_checks"] = checks
    payload["materiality_rule_pass"] = material
    payload["verdict"] = "continue_zero_notional_forward" if material else "no_incremental_tail_value"
    RESULT.write_text(json.dumps(json_ready(payload), indent=2) + "\n", encoding="utf-8")

    def tail_line(label: str, item: dict[str, Any]) -> str:
        return f"| {label} | {item['high_risk_entries']}/{item['state_entries']} | {item['overshoots_captured']}/{item['overshoots']} | {item['overshoot_recall']:.1%} | {item['lift']:.2f}x | {item['winner_false_positive_rate']:.1%} |"

    lines = [
        "# Core Carry actual-transport tail challenger v1",
        "",
        f"**结论：`{payload['verdict']}`；不改 live。** 本报告以 tail capture 和仓位损益为主，不以第四位小数的 proper-score 差异作策略结论。",
        "",
        "## 真正补入的字段",
        "",
        "- 逐报 METAR 露点 1h/3h 趋势；风向、持续性、3h 转向及南北半球暖输送分量；",
        "- 云量当前值/3h 变化、降水现象/小时降水、阵风 excess、3h 气压变化；",
        "- 以上均要求 observation time 不晚于 decision time；再与 exact-bracket forecast exit margin、fresh-high 剩余太阳热量交互。",
        "",
        "## 历史 forward：最高风险约 20% 是否抓到 overshoot",
        "",
        "| 排序 | 高风险 states | 捕获 overshoot | recall | lift | winner 被标高风险 |",
        "|---|---:|---:|---:|---:|---:|",
        tail_line("actual-transition challenger", payload["forward"]["tail_challenger"]),
        tail_line("frozen Core risk", payload["forward"]["tail_core"]),
        "",
        "## 10→5 股风险预算 overlay",
        "",
        f"- Forward 固定 10 股：{payload['forward']['policy']['entries']} 笔、{payload['forward']['policy']['losses']} 负、PnL `${payload['forward']['policy']['fixed10_pnl']:+.2f}`。",
        f"- actual-transition：降仓 {fp['downsize_entries']} 笔，其中抓到 {fp['downsize_losses']} 个 loss；节省 loss capital `${fp['loss_capital_saved']:.2f}`，牺牲 winner profit `${fp['winner_profit_sacrificed']:.2f}`，净 tail value `${fp['net_tail_value']:+.2f}`，PnL 变化 `${fp['pnl_delta_vs_fixed10']:+.2f}`。",
        f"- Core-risk 同覆盖基准：PnL 变化 `${payload['forward']['policy']['core_risk_overlay']['pnl_delta_vs_fixed10']:+.2f}`。",
        f"- 增量检查：tail capture vs Core={'PASS' if checks['incremental_tail_vs_core_pass'] else 'FAIL'}；overlay tail value vs Core={'PASS' if checks['incremental_policy_vs_core_pass'] else 'FAIL'}。绝对 lift 不能替代这两个增量条件。",
        "",
        "## 概率分数只作 secondary",
        "",
        f"- Challenger/Core Brier：{payload['forward']['probability_challenger']['brier']:.6f}/{payload['forward']['probability_core']['brier']:.6f}。",
        f"- Challenger/Core logloss：{payload['forward']['probability_challenger']['logloss']:.6f}/{payload['forward']['probability_core']['logloss']:.6f}。",
        "",
        "## 证据边界与动作",
        "",
        "- 这是已看过历史窗口的 diagnostic forward，不是新的 clean forward；selected loss 只有 6 个，不能直接改 live sizing。",
        "- 阵风缺失不能当作无阵风；南北半球暖风分量只是粗 transport prior，尚不等于 city terrain。",
        f"- materiality rule={'PASS' if material else 'FAIL'}。本次 actual-transition 与 Core 的 forward tail/policy 逐项相同，因此不保留这个 entry-time tail policy。字段继续用于 case interpretation；下一研究对象应是入场后新 METAR 触发的 event-driven invalidation，而不是再做同一 snapshot 的全局 residual。",
        "",
        f"大型产物：`{out_dir}`",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(json_ready({"verdict": payload["verdict"], "materiality_rule_pass": material, "forward": payload["forward"], "report": str(REPORT.relative_to(ROOT)), "artifact_dir": str(out_dir)}), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
