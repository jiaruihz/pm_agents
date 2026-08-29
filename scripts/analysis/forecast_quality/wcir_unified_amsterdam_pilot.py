#!/usr/bin/env python3
"""Build the WCIR unified-data Amsterdam next-print research pilot.

This is a research-only, zero-notional runner.  It never imports an order
client and it never writes to a runtime or production configuration path.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_modeling.forecast_path import add_fixed_lead_forecast_path_features
from weather_modeling.amsterdam_feature_builder_v2 import AmsterdamFeatureBuilderV2


UTC = timezone.utc
SUPPORT = np.arange(-10, 11, dtype=int)
EPS = 1e-7
RNG_SEED = 20260828

DEFAULT_HISTORICAL = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/research/"
    "amsterdam_knmi_10m_remaining_heat_v1/weather_checkpoints.csv.gz"
)
DEFAULT_FORECAST = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/research/"
    "amsterdam_ecmwf_previous_day1_path_v1/forecast_hourly.csv.gz"
)
DEFAULT_EVENTS = ROOT / "reviews/wcir_next_print/stage_03/evidence/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz"
DEFAULT_ALIGNED = ROOT / "reviews/wcir_next_print/stage_02/evidence/EVENT_ALIGNED_BOOK_ROWS_2026-08-26.jsonl.gz"
DEFAULT_TRUTHS = ROOT / "reviews/wcir_next_print/stage_02/evidence/FROZEN_EXECUTABLE_BOOK_TRUTHS_2026-08-26.jsonl.gz"
DEFAULT_OUTPUT = ROOT / "reviews/wcir_unified_data_amsterdam_pilot_v1"

CORE_FEATURES = [
    "latest_fast_native_value",
    "last_official_native_value",
    "official_running_max",
    "fast_minus_last_official",
    "fast_minus_running_max",
    "recent_slope",
    "recent_acceleration",
    "path_volatility",
    "running_fast_max",
    "time_since_high_minutes",
    "pullback_depth",
    "reheat_strength",
    "distance_to_up_native_boundary",
    "distance_to_down_native_boundary",
    "local_time_sin",
    "local_time_cos",
]

GAM_FEATURES = [
    ("fast_minus_last_official", True),
    ("recent_slope", True),
    ("reheat_strength", True),
    ("pullback_depth", False),
]


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def stable_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=lambda item: item.isoformat() if hasattr(item, "isoformat") else str(item),
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode()).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path, *, rows: int | None = None) -> dict[str, Any]:
    result = {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    if rows is not None:
        result["row_count"] = int(rows)
    return result


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def half_up(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    return np.floor(numeric + 0.5)


def weighted_quantities(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("target_date")["target_date"].transform("size")
    return (1.0 / counts.to_numpy(float)) * frame["target_date"].nunique()


def make_historical_panel(path: Path, forecast_path: Path | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    columns = [
        "target_date", "observed_at_utc", "ta_c", "tx_c", "solar_w_m2",
        "cloud_okta", "precip_mm_h", "humidity_pct", "wind_speed_mps",
        "official_report_time_utc", "latest_official_temp_c",
        "official_running_max_c", "collection_mode", "feature_collection_mode",
        "decision_ts_utc", "weather_checkpoint_key", "market_join_status",
        "d1_bracket_c", "decision_minute_local",
    ]
    raw = pd.read_csv(path, usecols=columns)
    raw["target_date"] = raw["target_date"].astype(str)
    raw["observed_at"] = pd.to_datetime(raw["observed_at_utc"], utc=True, errors="coerce")
    raw["official_report_at"] = pd.to_datetime(raw["official_report_time_utc"], utc=True, errors="coerce")
    raw = raw.sort_values(["target_date", "observed_at", "weather_checkpoint_key"]).reset_index(drop=True)
    raw["latest_fast_native_value"] = pd.to_numeric(raw["ta_c"], errors="coerce")
    raw["last_official_native_value"] = half_up(raw["latest_official_temp_c"])
    raw["official_running_max"] = half_up(raw["official_running_max_c"])

    next_report: dict[tuple[str, pd.Timestamp], tuple[pd.Timestamp, float]] = {}
    for target_date, day in raw.groupby("target_date", sort=False):
        states = (
            day.dropna(subset=["official_report_at", "last_official_native_value"])
            .drop_duplicates("official_report_at", keep="last")
            .sort_values("official_report_at")
        )
        for index in range(len(states) - 1):
            current = states.iloc[index]
            following = states.iloc[index + 1]
            next_report[(target_date, current["official_report_at"])] = (
                following["official_report_at"], float(following["last_official_native_value"])
            )
    linked = [
        next_report.get((row.target_date, row.official_report_at), (pd.NaT, np.nan))
        for row in raw.itertuples()
    ]
    raw["next_official_observed_at"] = [row[0] for row in linked]
    raw["next_official_native_value"] = [row[1] for row in linked]
    raw["official_print_group_id"] = [
        stable_hash({"city": "Amsterdam", "target_date": d, "observed_at": str(t)})
        if pd.notna(t) else None
        for d, t in zip(raw["target_date"], raw["next_official_observed_at"])
    ]
    raw["next_official_delta_native_tick"] = (
        raw["next_official_native_value"] - raw["last_official_native_value"]
    )
    raw["label_within_support"] = raw["next_official_delta_native_tick"].between(SUPPORT[0], SUPPORT[-1])

    local = raw["observed_at"].dt.tz_convert("Europe/Amsterdam")
    minute = local.dt.hour * 60 + local.dt.minute
    raw["local_time_sin"] = np.sin(2 * np.pi * minute / 1440)
    raw["local_time_cos"] = np.cos(2 * np.pi * minute / 1440)
    raw["fast_minus_last_official"] = raw["latest_fast_native_value"] - raw["last_official_native_value"]
    raw["fast_minus_running_max"] = raw["latest_fast_native_value"] - raw["official_running_max"]
    raw["distance_to_up_native_boundary"] = np.ceil(raw["latest_fast_native_value"]) - raw["latest_fast_native_value"]
    raw["distance_to_down_native_boundary"] = raw["latest_fast_native_value"] - np.floor(raw["latest_fast_native_value"])

    raw = AmsterdamFeatureBuilderV2.add_path_features(
        raw,
        group_column="target_date",
        require_available_at=False,
    )
    raw["tx_minus_ta"] = pd.to_numeric(raw["tx_c"], errors="coerce") - raw["latest_fast_native_value"]
    if forecast_path is not None and forecast_path.is_file():
        raw = add_fixed_lead_forecast_path_features(raw, pd.read_csv(forecast_path))
    else:
        raw["forecast_now_c"] = np.nan
    raw["availability_class"] = "HISTORICAL_FINAL_ARCHIVE"
    raw["weather_label_eligible"] = raw["next_official_delta_native_tick"].notna() & raw["label_within_support"]
    raw["strict_pit_eligible"] = False
    raw["decision_vintage_id"] = [
        stable_hash({"city": "Amsterdam", "target_date": d, "observed_at": str(o), "prior_official": str(p)})
        for d, o, p in zip(raw["target_date"], raw["observed_at"], raw["official_report_at"])
    ]
    panel = raw.loc[raw["weather_label_eligible"]].copy()
    panel["next_official_delta_native_tick"] = panel["next_official_delta_native_tick"].astype(int)
    audit = {
        "raw_rows": len(raw),
        "raw_target_dates": raw["target_date"].nunique(),
        "earliest_target_date": raw["target_date"].min(),
        "latest_target_date": raw["target_date"].max(),
        "weather_label_eligible_rows": len(panel),
        "weather_label_eligible_dates": panel["target_date"].nunique(),
        "captured_pit_rows": int(raw["decision_ts_utc"].notna().sum()),
        "historical_final_only_rows": int(raw["decision_ts_utc"].isna().sum()),
        "duplicate_checkpoint_keys": int(raw.duplicated("weather_checkpoint_key", keep=False).sum()),
        "revision_rows": 0,
        "expected_interval_count": int(raw["target_date"].nunique() * 144),
        "actual_interval_count": len(raw),
        "missing_interval_count": int(raw["target_date"].nunique() * 144 - len(raw)),
        "collection_modes": raw["collection_mode"].value_counts(dropna=False).to_dict(),
        "non_backfillable": ["first_seen_at", "available_at", "revision arrival order", "decision-time executable book"],
        "backfillable": ["final KNMI ta/tx", "routine EHAM official print", "ordinal next-print label", "observation-time path features"],
        "forecast_rows": int(raw["forecast_now_c"].notna().sum()),
    }
    return panel, audit


def make_captured_panel(events_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    all_rows = read_jsonl_gz(events_path)
    rows = [row for row in all_rows if row.get("city") == "Amsterdam" and row.get("next_official_linked")]
    frame = pd.DataFrame(rows)
    frame["target_date"] = frame["target_date"].astype(str)
    frame["observed_at"] = pd.to_datetime(frame["source_obs_ts_utc"], utc=True)
    frame["latest_fast_native_value"] = pd.to_numeric(frame["source_temp_c"], errors="coerce")
    frame["last_official_native_value"] = pd.to_numeric(frame["latest_metar_round_c"], errors="coerce")
    frame["official_running_max"] = pd.to_numeric(frame["metar_running_max_round_c"], errors="coerce")
    frame["next_official_native_value"] = pd.to_numeric(frame["official_round_c"], errors="coerce")
    frame["next_official_delta_native_tick"] = (
        frame["next_official_native_value"] - frame["last_official_native_value"]
    ).astype(int)
    local = frame["observed_at"].dt.tz_convert("Europe/Amsterdam")
    minute = local.dt.hour * 60 + local.dt.minute
    frame["local_time_sin"] = np.sin(2 * np.pi * minute / 1440)
    frame["local_time_cos"] = np.cos(2 * np.pi * minute / 1440)
    frame["fast_minus_last_official"] = frame["latest_fast_native_value"] - frame["last_official_native_value"]
    frame["fast_minus_running_max"] = frame["latest_fast_native_value"] - frame["official_running_max"]
    frame["distance_to_up_native_boundary"] = np.ceil(frame["latest_fast_native_value"]) - frame["latest_fast_native_value"]
    frame["distance_to_down_native_boundary"] = frame["latest_fast_native_value"] - np.floor(frame["latest_fast_native_value"])
    frame = frame.sort_values(["target_date", "observed_at", "event_id"]).reset_index(drop=True)
    group = frame.groupby("target_date", sort=False)
    gap_minutes = group["observed_at"].diff().dt.total_seconds().div(60)
    source_diff = group["latest_fast_native_value"].diff()
    frame["recent_slope"] = source_diff.where(gap_minutes.le(20))
    frame["recent_acceleration"] = group["recent_slope"].diff().where(gap_minutes.le(20))
    frame["path_volatility"] = group["latest_fast_native_value"].transform(lambda s: s.rolling(6, min_periods=2).std())
    frame["running_fast_max"] = group["latest_fast_native_value"].cummax()
    frame["pullback_depth"] = frame["running_fast_max"] - frame["latest_fast_native_value"]
    rolling_low = group["latest_fast_native_value"].transform(lambda s: s.rolling(6, min_periods=1).min())
    frame["reheat_strength"] = frame["latest_fast_native_value"] - rolling_low
    high_time = frame["observed_at"].where(frame["latest_fast_native_value"].eq(frame["running_fast_max"])).groupby(frame["target_date"]).ffill()
    frame["time_since_high_minutes"] = (frame["observed_at"] - high_time).dt.total_seconds().div(60)
    frame["availability_class"] = "CAPTURED_PIT_ARCHIVE"
    frame["weather_label_eligible"] = True
    frame["strict_pit_eligible"] = True
    frame["decision_vintage_id"] = frame["event_id"]
    frame["official_print_group_id"] = frame["information_event_id"]
    city_audit: dict[str, Any] = {}
    for city in ("Amsterdam", "Helsinki", "Tokyo", "Busan", "Seoul"):
        city_rows = [row for row in all_rows if row.get("city") == city]
        dates = sorted({str(row.get("target_date")) for row in city_rows})
        keys = [str(row.get("event_key")) for row in city_rows]
        city_audit[city] = {
            "evidence_scope": "FROZEN_NEXT_REPORT_EVENTS Stage-3 captured opportunity archive",
            "raw_weather_earliest_date": dates[0] if dates else None,
            "raw_weather_latest_date": dates[-1] if dates else None,
            "raw_unique_target_dates": len(dates),
            "actual_row_count": len(city_rows),
            "expected_interval_count": None,
            "missing_count": None,
            "duplicate_count": len(keys) - len(set(keys)),
            "revision_count": sum(1 for row in city_rows if row.get("event_role") == "revision"),
            "captured_pit_dates": len(dates),
            "historical_final_only_dates": 0,
            "next_print_label_eligible_events": sum(bool(row.get("next_official_linked")) for row in city_rows),
            "legacy_profile_evaluation_events": len(city_rows),
            "legacy_denominator_note": "Frozen opportunity archive is narrower than raw source archive by date cutoff, material-event dedupe, official linkage and city contract.",
            "backfillable_fields": ["historical final weather", "final official label"],
            "non_backfillable_fields": ["original first-seen", "original revision arrival order", "missing decision book"],
        }
        if city == "Tokyo":
            city_audit[city]["state_semantics"] = {
                "pre_cross": "DERIVED_STATE_NOT_JMA_SOURCE_LABEL",
                "state_entry": "DERIVED_STATE_NOT_JMA_SOURCE_LABEL",
                "overshoot": "DERIVED_STATE_NOT_JMA_SOURCE_LABEL",
            }
    return frame, city_audit


@dataclass
class OrdinalThresholdModel:
    features: list[str]
    support: np.ndarray
    imputer: Any = None
    scaler: Any = None
    models: list[Any] | None = None

    def fit(self, frame: pd.DataFrame, sample_weight: np.ndarray) -> "OrdinalThresholdModel":
        x = frame[self.features].to_numpy(float)
        y = frame["next_official_delta_native_tick"].to_numpy(int)
        self.imputer = SimpleImputer(strategy="median").fit(x)
        x = self.imputer.transform(x)
        self.scaler = StandardScaler().fit(x, sample_weight=sample_weight)
        x = self.scaler.transform(x)
        self.models = []
        for threshold in self.support[:-1]:
            binary = (y <= threshold).astype(int)
            if binary.min() == binary.max():
                self.models.append(float(binary[0]))
                continue
            estimator = SGDClassifier(
                loss="log_loss", penalty="l2", alpha=0.002, max_iter=200,
                tol=None, random_state=RNG_SEED + int(threshold), average=True,
            )
            estimator.fit(x, binary, sample_weight=sample_weight)
            self.models.append(estimator)
        return self

    def predict_pmf(self, frame: pd.DataFrame) -> np.ndarray:
        assert self.models is not None
        x = self.scaler.transform(self.imputer.transform(frame[self.features].to_numpy(float)))
        cdf = []
        for model in self.models:
            if isinstance(model, float):
                cdf.append(np.full(len(frame), model))
            else:
                cdf.append(model.predict_proba(x)[:, 1])
        cdf_array = np.maximum.accumulate(np.vstack(cdf).T, axis=1)
        cdf_array = np.clip(cdf_array, EPS, 1 - EPS)
        pmf = np.column_stack([cdf_array[:, 0], np.diff(cdf_array, axis=1), 1 - cdf_array[:, -1]])
        pmf = np.clip(pmf, EPS, None)
        return pmf / pmf.sum(axis=1, keepdims=True)


@dataclass
class MonotonicAdditiveModel:
    features: list[tuple[str, bool]]
    support: np.ndarray
    intercept: float = 0.0
    components: list[Any] | None = None
    fills: list[float] | None = None
    residual_scale: float = 1.0

    def fit(self, frame: pd.DataFrame, sample_weight: np.ndarray) -> "MonotonicAdditiveModel":
        y = frame["next_official_delta_native_tick"].to_numpy(float)
        self.intercept = float(np.average(y, weights=sample_weight))
        fitted = np.full(len(frame), self.intercept)
        self.components = [None] * len(self.features)
        self.fills = []
        for name, _ in self.features:
            values = frame[name].to_numpy(float)
            self.fills.append(float(np.nanmedian(values)) if np.isfinite(values).any() else 0.0)
        for _ in range(3):
            for index, (name, increasing) in enumerate(self.features):
                old = 0.0 if self.components[index] is None else self.components[index].predict(
                    np.nan_to_num(frame[name].to_numpy(float), nan=self.fills[index])
                )
                residual = y - (fitted - old)
                x = frame[name].to_numpy(float)
                fill = self.fills[index]
                x = np.nan_to_num(x, nan=fill)
                component = IsotonicRegression(increasing=increasing, out_of_bounds="clip").fit(
                    x, residual, sample_weight=sample_weight
                )
                new = component.predict(x)
                fitted += new - old
                self.components[index] = component
        self.residual_scale = max(0.45, float(np.sqrt(np.average((y - fitted) ** 2, weights=sample_weight))))
        return self

    def predict_pmf(self, frame: pd.DataFrame) -> np.ndarray:
        assert self.components is not None and self.fills is not None
        mean = np.full(len(frame), self.intercept)
        for component, fill, (name, _) in zip(self.components, self.fills, self.features):
            x = frame[name].to_numpy(float)
            mean += component.predict(np.nan_to_num(x, nan=fill))
        z = (self.support[None, :] - mean[:, None]) / self.residual_scale
        pmf = np.exp(-0.5 * z * z)
        pmf = np.clip(pmf, EPS, None)
        return pmf / pmf.sum(axis=1, keepdims=True)


def temperature_scale(pmf: np.ndarray, temperature: float) -> np.ndarray:
    logits = np.log(np.clip(pmf, EPS, 1.0)) / temperature
    logits -= logits.max(axis=1, keepdims=True)
    result = np.exp(logits)
    return result / result.sum(axis=1, keepdims=True)


def fit_temperature(pmf: np.ndarray, labels: np.ndarray) -> float:
    indices = np.searchsorted(SUPPORT, labels)
    def objective(value: float) -> float:
        scaled = temperature_scale(pmf, value)
        return float(-np.mean(np.log(np.clip(scaled[np.arange(len(labels)), indices], EPS, 1))))
    return float(minimize_scalar(objective, bounds=(0.5, 3.0), method="bounded").x)


def baseline_pmf(
    train_labels: np.ndarray,
    train_predicted_delta: np.ndarray,
    predicted_delta: np.ndarray,
) -> np.ndarray:
    residual = np.clip(
        train_labels - np.rint(train_predicted_delta), SUPPORT[0], SUPPORT[-1]
    ).astype(int)
    counts = Counter(residual.tolist())
    output = np.zeros((len(predicted_delta), len(SUPPORT)), dtype=float)
    total = sum(counts.values()) + len(SUPPORT)
    residual_probability = {value: (counts.get(value, 0) + 1) / total for value in SUPPORT}
    for row_index, prediction in enumerate(np.rint(predicted_delta).astype(int)):
        for residual_value, probability in residual_probability.items():
            value = int(np.clip(prediction + residual_value, SUPPORT[0], SUPPORT[-1]))
            output[row_index, value - SUPPORT[0]] += probability
    return output / output.sum(axis=1, keepdims=True)


def binary_calibration(probability: np.ndarray, label: np.ndarray) -> dict[str, Any]:
    probability = np.clip(probability, EPS, 1 - EPS)
    logit = np.log(probability / (1 - probability)).reshape(-1, 1)
    if np.unique(label).size < 2:
        return {"intercept": None, "slope": None, "reliability_ece_10": None}
    estimator = LogisticRegression(C=1e6, max_iter=2000).fit(logit, label)
    bins = pd.qcut(probability, q=min(10, len(np.unique(probability))), duplicates="drop")
    table = pd.DataFrame({"p": probability, "y": label, "bin": bins}).groupby("bin", observed=True).agg(p=("p", "mean"), y=("y", "mean"), n=("y", "size"))
    ece = float(np.average(np.abs(table["p"] - table["y"]), weights=table["n"]))
    return {
        "intercept": float(estimator.intercept_[0]),
        "slope": float(estimator.coef_[0, 0]),
        "reliability_ece_10": ece,
    }


def prediction_frame(frame: pd.DataFrame, pmf: np.ndarray, model_id: str, fold: int | str) -> pd.DataFrame:
    result = frame[[
        "decision_vintage_id", "official_print_group_id", "target_date", "observed_at",
        "next_official_delta_native_tick", "last_official_native_value", "official_running_max",
    ]].copy()
    result["model_id"] = model_id
    result["fold"] = fold
    for index, value in enumerate(SUPPORT):
        result[f"p_delta_{value:+d}"] = pmf[:, index]
    result["expected_delta_tick"] = pmf @ SUPPORT
    result["p_up"] = pmf[:, SUPPORT > 0].sum(axis=1)
    result["p_down"] = pmf[:, SUPPORT < 0].sum(axis=1)
    result["p_unchanged"] = pmf[:, SUPPORT == 0].sum(axis=1)
    thresholds = (frame["official_running_max"] - frame["last_official_native_value"]).to_numpy(float)
    result["p_new_running_max"] = [pmf[i, SUPPORT > threshold].sum() for i, threshold in enumerate(thresholds)]
    result["predictive_entropy"] = -(pmf * np.log(np.clip(pmf, EPS, 1))).sum(axis=1)
    return result


def score_rows(frame: pd.DataFrame, pmf: np.ndarray) -> tuple[pd.DataFrame, dict[str, float]]:
    labels = frame["next_official_delta_native_tick"].to_numpy(int)
    indices = labels - SUPPORT[0]
    cdf = np.cumsum(pmf, axis=1)
    observed_cdf = (SUPPORT[None, :] >= labels[:, None]).astype(float)
    rows = pd.DataFrame({
        "target_date": frame["target_date"].to_numpy(),
        "rps": np.sum((cdf[:, :-1] - observed_cdf[:, :-1]) ** 2, axis=1) / (len(SUPPORT) - 1),
        "logloss": -np.log(np.clip(pmf[np.arange(len(frame)), indices], EPS, 1)),
        "brier_up": (pmf[:, SUPPORT > 0].sum(axis=1) - (labels > 0)) ** 2,
        "brier_down": (pmf[:, SUPPORT < 0].sum(axis=1) - (labels < 0)) ** 2,
        "brier_unchanged": (pmf[:, SUPPORT == 0].sum(axis=1) - (labels == 0)) ** 2,
    })
    daily = rows.groupby("target_date").mean(numeric_only=True)
    summary = {name: float(daily[name].mean()) for name in daily.columns}
    summary.update({"rows": int(len(frame)), "target_dates": int(frame["target_date"].nunique())})
    return rows, summary


def bootstrap_delta(candidate: pd.DataFrame, baseline: pd.DataFrame, metric: str, reps: int = 2000) -> dict[str, Any]:
    left = candidate.groupby("target_date")[metric].mean()
    right = baseline.groupby("target_date")[metric].mean()
    dates = sorted(set(left.index) & set(right.index))
    delta = np.array([left[d] - right[d] for d in dates], dtype=float)
    rng = np.random.default_rng(RNG_SEED)
    samples = np.array([rng.choice(delta, len(delta), replace=True).mean() for _ in range(reps)])
    return {
        "metric": metric,
        "candidate_minus_baseline": float(delta.mean()),
        "ci95": [float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))],
        "target_dates": len(dates),
        "bootstrap_unit": "target_date",
        "reps": reps,
    }


def make_folds(dates: list[str]) -> list[tuple[list[str], list[str]]]:
    if len(dates) <= 365:
        raise ValueError(
            "expanding OOF requires more than 365 independent target dates; "
            f"received {len(dates)}"
        )
    start = max(365, len(dates) // 2)
    remaining = dates[start:]
    blocks = [list(block) for block in np.array_split(np.array(remaining, dtype=object), 4) if len(block)]
    folds = []
    for block in blocks:
        test = [str(value) for value in block]
        train = [date for date in dates if date < test[0]]
        folds.append((train, test))
    return folds


def ensure_safe_output(path: Path) -> Path:
    resolved = path.resolve()
    allowed_root = (ROOT / "reviews").resolve()
    if resolved != allowed_root and allowed_root not in resolved.parents:
        raise ValueError(
            f"research output must stay under {allowed_root}; received {resolved}"
        )
    forbidden = {
        (ROOT / "runtime").resolve(),
        (ROOT / "src/strategies/runtime").resolve(),
    }
    if any(resolved == item or item in resolved.parents for item in forbidden):
        raise ValueError(f"runtime/production output is forbidden: {resolved}")
    return resolved


def train_models(panel: pd.DataFrame, outer_dates: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any]]:
    development = panel.loc[~panel["target_date"].isin(outer_dates)].copy()
    outer = panel.loc[panel["target_date"].isin(outer_dates)].copy()
    dates = sorted(development["target_date"].unique())
    folds = make_folds(dates)
    oof_parts: list[pd.DataFrame] = []
    prior_raw: list[np.ndarray] = []
    prior_labels: list[np.ndarray] = []
    fold_manifest = []
    for fold_id, (train_dates, test_dates) in enumerate(folds):
        train = development.loc[development["target_date"].isin(train_dates)]
        test = development.loc[development["target_date"].isin(test_dates)]
        model = OrdinalThresholdModel(CORE_FEATURES, SUPPORT).fit(train, weighted_quantities(train))
        raw_pmf = model.predict_pmf(test)
        if prior_raw:
            temperature = fit_temperature(np.vstack(prior_raw), np.concatenate(prior_labels))
        else:
            temperature = 1.0
        selected_pmf = temperature_scale(raw_pmf, temperature)
        part = prediction_frame(test, selected_pmf, "ams_next_print_m1_ordinal_logit", fold_id)
        part["calibration_temperature"] = temperature
        oof_parts.append(part)
        prior_raw.append(raw_pmf)
        prior_labels.append(test["next_official_delta_native_tick"].to_numpy(int))
        fold_manifest.append({
            "fold": fold_id, "train_start": train_dates[0], "train_end": train_dates[-1],
            "test_start": test_dates[0], "test_end": test_dates[-1],
            "train_dates": len(train_dates), "test_dates": len(test_dates),
            "train_rows": len(train), "test_rows": len(test), "calibration_temperature": temperature,
        })
    oof = pd.concat(oof_parts, ignore_index=True)
    final_temperature = fit_temperature(np.vstack(prior_raw), np.concatenate(prior_labels))

    m1 = OrdinalThresholdModel(CORE_FEATURES, SUPPORT).fit(development, weighted_quantities(development))
    m2 = MonotonicAdditiveModel(GAM_FEATURES, SUPPORT).fit(development, weighted_quantities(development))
    m1_outer_pmf = temperature_scale(m1.predict_pmf(outer), final_temperature)
    m2_outer_pmf = m2.predict_pmf(outer)
    outer_predictions = prediction_frame(outer, m1_outer_pmf, "ams_next_print_m1_ordinal_logit", "outer")
    m2_predictions = prediction_frame(outer, m2_outer_pmf, "ams_next_print_m2_monotonic_additive", "outer")

    train_labels = development["next_official_delta_native_tick"].to_numpy(int)
    baseline_specs = {
        "B0_persistence": (
            np.zeros(len(development)), np.zeros(len(outer)), np.ones(len(development), dtype=bool)
        ),
        "B1_recent_slope": (
            np.rint(np.nan_to_num(development["recent_slope"].to_numpy(float), nan=0.0)),
            np.rint(np.nan_to_num(outer["recent_slope"].to_numpy(float), nan=0.0)),
            np.ones(len(development), dtype=bool),
        ),
        "B2_latest_fast_rounded": (
            half_up(development["latest_fast_native_value"]).to_numpy(float) - development["last_official_native_value"].to_numpy(float),
            half_up(outer["latest_fast_native_value"]).to_numpy(float) - outer["last_official_native_value"].to_numpy(float),
            np.ones(len(development), dtype=bool),
        ),
        "B3_forecast_only": (
            half_up(development["forecast_now_c"]).to_numpy(float) - development["last_official_native_value"].to_numpy(float),
            half_up(outer["forecast_now_c"]).to_numpy(float) - outer["last_official_native_value"].to_numpy(float),
            development["forecast_now_c"].notna().to_numpy(),
        ),
    }
    score_tables: dict[str, pd.DataFrame] = {}
    summaries: dict[str, Any] = {}
    m1_rows, summaries["M1"] = score_rows(outer, m1_outer_pmf)
    m2_rows, summaries["M2"] = score_rows(outer, m2_outer_pmf)
    score_tables["M1"] = m1_rows
    score_tables["M2"] = m2_rows
    for name, (train_point, point, train_mask) in baseline_specs.items():
        if not np.isfinite(point).all() or not train_mask.any():
            continue
        pmf = baseline_pmf(train_labels[train_mask], train_point[train_mask], point)
        rows, summary = score_rows(outer, pmf)
        score_tables[name] = rows
        summaries[name] = summary
    best_baseline = min(score_tables.keys() - {"M1", "M2"}, key=lambda name: summaries[name]["rps"])
    comparisons = {
        name: bootstrap_delta(score_tables["M1"], score_tables[name], "rps")
        for name in score_tables.keys() - {"M1", "M2"}
    }
    comparisons["M2_vs_best_baseline"] = bootstrap_delta(score_tables["M2"], score_tables[best_baseline], "rps")
    label_by_date = outer.groupby("target_date")["next_official_delta_native_tick"].mean()
    rotated = label_by_date.shift(1).fillna(label_by_date.iloc[-1]).to_dict()
    negative_frame = outer.copy()
    negative_frame["next_official_delta_native_tick"] = negative_frame["target_date"].map(rotated).round().clip(SUPPORT[0], SUPPORT[-1]).astype(int)
    negative_rows, negative_summary = score_rows(negative_frame, m1_outer_pmf)
    best_delta = comparisons[best_baseline]
    if best_delta["candidate_minus_baseline"] < 0 and best_delta["ci95"][1] < 0:
        verdict = "PASS"
    elif best_delta["candidate_minus_baseline"] < 0:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "FAIL"
    labels = outer["next_official_delta_native_tick"].to_numpy(int)
    results = {
        "primary_metric": "date_equal_ranked_probability_score",
        "outer_validation": {"start": outer_dates[0], "end": outer_dates[-1], "target_dates": len(outer_dates), "rows": len(outer)},
        "summaries": summaries,
        "best_simple_baseline": best_baseline,
        "paired_comparisons": comparisons,
        "negative_control": {"kind": "one-target-date label rotation", "m1_rps": negative_summary["rps"], "same_order_gain": negative_summary["rps"] <= summaries[best_baseline]["rps"]},
        "model_verdict": verdict,
        "calibration": {"method": "expanding OOF temperature scaling", "final_temperature": final_temperature},
        "calibration_heads": {
            "up": binary_calibration(m1_outer_pmf[:, SUPPORT > 0].sum(axis=1), (labels > 0).astype(int)),
            "down": binary_calibration(m1_outer_pmf[:, SUPPORT < 0].sum(axis=1), (labels < 0).astype(int)),
            "unchanged": binary_calibration(m1_outer_pmf[:, SUPPORT == 0].sum(axis=1), (labels == 0).astype(int)),
            "new_running_max": binary_calibration(
                np.array([
                    m1_outer_pmf[i, SUPPORT > threshold].sum()
                    for i, threshold in enumerate((outer["official_running_max"] - outer["last_official_native_value"]).to_numpy(float))
                ]),
                (outer["next_official_native_value"].to_numpy(float) > outer["official_running_max"].to_numpy(float)).astype(int),
            ),
        },
        "unavailable_registry_members": {
            "B4_legacy_next_print_proxy": "legacy Amsterdam artifacts target final-bracket survival, not ordinal next routine official print",
            "MKT0_market_only": "captured market prior is not a complete next-print ordinal distribution on the Amsterdam same-row denominator",
            "FUSION1": "not fitted because MKT0 denominator is insufficient; no fallback or synthetic prior used",
        },
    }
    artifact = {
        "schema_version": "wcir_amsterdam_next_print_ordinal_artifact_v1",
        "model_id": "ams_next_print_m1_ordinal_logit",
        "target": "next_official_delta_native_tick",
        "official_native_tick_c": 1.0,
        "support": SUPPORT.tolist(),
        "features": CORE_FEATURES,
        "training_cutoff": development["target_date"].max(),
        "historical_outer_validation": [outer_dates[0], outer_dates[-1]],
        "calibration_temperature": final_temperature,
        "model": m1,
        "challenger": m2,
        "fold_manifest": fold_manifest,
        "strict_pit_training": False,
        "training_availability_class": "HISTORICAL_FINAL_ARCHIVE",
    }
    return oof, pd.concat([outer_predictions, m2_predictions], ignore_index=True), results, artifact


def score_captured(frame: pd.DataFrame, artifact: dict[str, Any]) -> pd.DataFrame:
    pmf = temperature_scale(artifact["model"].predict_pmf(frame), artifact["calibration_temperature"])
    return prediction_frame(frame, pmf, artifact["model_id"], "captured_pit_replay")


def evaluate_captured_pit(
    historical: pd.DataFrame,
    captured: pd.DataFrame,
    predictions: pd.DataFrame,
    results: dict[str, Any],
) -> None:
    probability_columns = [f"p_delta_{value:+d}" for value in SUPPORT]
    model_pmf = predictions[probability_columns].to_numpy(float)
    model_rows, model_summary = score_rows(captured, model_pmf)
    train_point = (
        half_up(historical["latest_fast_native_value"]).to_numpy(float)
        - historical["last_official_native_value"].to_numpy(float)
    )
    captured_point = (
        half_up(captured["latest_fast_native_value"]).to_numpy(float)
        - captured["last_official_native_value"].to_numpy(float)
    )
    baseline = baseline_pmf(
        historical["next_official_delta_native_tick"].to_numpy(int),
        train_point,
        captured_point,
    )
    baseline_rows, baseline_summary = score_rows(captured, baseline)
    delta = bootstrap_delta(model_rows, baseline_rows, "rps")
    results["historical_outer_verdict"] = results["model_verdict"]
    results["captured_pit_validation"] = {
        "scope": "accepted Stage-3 Amsterdam captured-PIT next-report events; previously reviewed, not pristine prospective",
        "model": model_summary,
        "B2_latest_fast_rounded": baseline_summary,
        "M1_minus_B2": delta,
    }
    if delta["candidate_minus_baseline"] > 0 and delta["ci95"][0] > 0:
        results["model_verdict"] = "FAIL"
        results["model_verdict_reason"] = "M1 is significantly worse than B2 on captured-PIT dates despite historical-final outer improvement."
    elif delta["ci95"][0] <= 0 <= delta["ci95"][1]:
        results["model_verdict"] = "INCONCLUSIVE"
        results["model_verdict_reason"] = "Captured-PIT paired RPS delta crosses zero."


def executable_replay(
    captured: pd.DataFrame,
    captured_predictions: pd.DataFrame,
    aligned_path: Path,
    truths_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    aligned = read_jsonl_gz(aligned_path)
    truths = {row["truth_id"]: row for row in read_jsonl_gz(truths_path)}
    events = {row["event_id"]: row for row in captured.to_dict("records")}
    predictions = {row["decision_vintage_id"]: row for row in captured_predictions.to_dict("records")}
    by_event: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in aligned:
        if row["event_id"] in events:
            by_event[row["event_id"]][row["checkpoint"]] = row
    output = []
    horizons = {5: "official_plus_5s", 15: "official_plus_15s", 30: "official_plus_30s", 60: "official_plus_60s", 120: "official_plus_120s"}
    for event_id, event in events.items():
        checkpoints = by_event.get(event_id, {})
        entry = next((checkpoints.get(name) for name in ("source_t0", "pre_official") if checkpoints.get(name, {}).get("book_valid")), None)
        prediction = predictions.get(event_id)
        threshold = float(event["official_running_max"] - event["last_official_native_value"])
        p_cross = None if prediction is None else float(prediction["p_new_running_max"])
        for shares in (1.0, 5.0):
            entry_truth = truths.get(entry.get("book_truth_id")) if entry else None
            entry_sweep = next((row for row in (entry_truth or {}).get("sweeps", []) if row["side"] == "buy" and float(row["shares"]) == shares), None)
            entry_eligible = bool(entry and entry_sweep and entry_sweep.get("fully_executable"))
            entry_cost_per_share = (float(entry_sweep["effective_value_usd"]) / shares) if entry_eligible else None
            selector_pass = bool(entry_eligible and p_cross is not None and p_cross - entry_cost_per_share >= 0.02)
            for horizon, checkpoint_name in horizons.items():
                exit_row = checkpoints.get(checkpoint_name)
                exit_truth = truths.get(exit_row.get("book_truth_id")) if exit_row and exit_row.get("book_valid") else None
                exit_sweep = next((row for row in (exit_truth or {}).get("sweeps", []) if row["side"] == "sell" and float(row["shares"]) == shares), None)
                markout_eligible = bool(entry_eligible and exit_sweep and exit_sweep.get("fully_executable"))
                pnl = (
                    float(exit_sweep["effective_value_usd"]) - float(entry_sweep["effective_value_usd"])
                    if selector_pass and markout_eligible else None
                )
                output.append({
                    "event_id": event_id, "target_date": event["target_date"], "official_print_group_id": event["official_print_group_id"],
                    "source_detect_ts_utc": event.get("source_detect_ts_utc"), "decision_ready_ts_utc": event.get("ts_utc"),
                    "official_first_seen_at_utc": event.get("official_first_seen_at_utc"), "token_id": event.get("token_id"),
                    "p_new_running_max": p_cross, "running_max_threshold_delta": threshold,
                    "entry_checkpoint": None if entry is None else entry["checkpoint"],
                    "entry_book_snapshot_id": None if entry is None else entry.get("book_snapshot_id"),
                    "entry_eligible": entry_eligible, "entry_failure_reason": None if entry_eligible else ("missing_or_invalid_entry_book" if entry is None else "insufficient_depth"),
                    "selector": "weather_increment_edge_0p02", "selector_pass": selector_pass,
                    "shares": shares, "horizon_seconds": horizon, "markout_eligible": markout_eligible,
                    "exit_book_snapshot_id": None if exit_row is None else exit_row.get("book_snapshot_id"),
                    "counterfactual_net_markout_usd": pnl, "trade_class": "zero_notional_executable_replay",
                    "orders": 0, "fills": 0, "notional": 0,
                })
    rows = pd.DataFrame(output)
    selected = rows[rows["selector_pass"]]
    funnel = {
        "raw_source_opportunities": len(captured),
        "weather_label_eligible": int(captured["weather_label_eligible"].sum()),
        "model_scored": int(captured_predictions["decision_vintage_id"].nunique()),
        "market_prior_eligible": 0,
        "positive_weather_increment": int((captured_predictions["p_new_running_max"] > 0.5).sum()),
        "executable_entry_eligible_1share": int(rows.loc[rows["shares"].eq(1), "entry_eligible"].groupby(rows["event_id"]).max().sum()),
        "executable_entry_eligible_5share": int(rows.loc[rows["shares"].eq(5), "entry_eligible"].groupby(rows["event_id"]).max().sum()),
        "selector_pass_1share": int(rows.loc[rows["shares"].eq(1), "selector_pass"].groupby(rows["event_id"]).max().sum()),
        "selector_pass_5share": int(rows.loc[rows["shares"].eq(5), "selector_pass"].groupby(rows["event_id"]).max().sum()),
        "markout_eligible_by_horizon": {
            str(h): int(rows.loc[rows["horizon_seconds"].eq(h), "markout_eligible"].sum()) for h in horizons
        },
        "settled_counterfactuals": 0,
    }
    report = {
        "status": "INCONCLUSIVE",
        "reason": "Amsterdam executable archive has too few same-row valid entry/exit checkpoints; market-only ordinal prior is unavailable.",
        "selected_intents": int(selected[["event_id", "shares"]].drop_duplicates().shape[0]),
        "unique_target_dates": int(selected["target_date"].nunique()) if len(selected) else 0,
        "net_markout_by_size_horizon": [
            {"shares": float(shares), "horizon_seconds": int(h), "eligible_rows": int(len(group)), "net_markout_usd": float(group["counterfactual_net_markout_usd"].dropna().sum())}
            for (shares, h), group in selected.groupby(["shares", "horizon_seconds"])
        ],
        "actual_orders": 0, "actual_fills": 0, "actual_notional": 0,
        "not_actual_fill_or_pnl": True,
    }
    return rows, funnel, report


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def write_docs(output: Path, historical_audit: dict[str, Any], city_audit: dict[str, Any], results: dict[str, Any], replay: dict[str, Any], funnel: dict[str, Any], generated: str) -> None:
    (output / "UNIFIED_DATA_MANAGEMENT_CONTRACT.md").write_text(f"""# WCIR unified data management contract

Generated: `{generated}`. Research only; orders/fills/notional remain `0/0/0`.

The permanent layers are `HISTORICAL_FINAL_ARCHIVE`, `CAPTURED_PIT_ARCHIVE`, and `EXECUTABLE_MARKET_ARCHIVE`. Historical-final rows may train a weather model but cannot prove original first-seen, revision order, or executable lead. Market availability never controls weather-label eligibility.

Eligibility is split into `WEATHER_LABEL_ELIGIBLE`, `MARKET_PRIOR_ELIGIBLE`, `EXECUTABLE_ENTRY_ELIGIBLE`, and `MARKOUT_ELIGIBLE`. Missing market data produces `research PnL = NULL`; it is not policy abstention.

All derived states read only data available at the decision vintage. `pre-cross`, `state-entry`, and `overshoot` are derived states, never raw JMA labels.
""", encoding="utf-8")
    lines = ["# City data coverage and backfill audit", "", f"Generated: `{generated}`.", ""]
    for city, item in city_audit.items():
        lines += [f"## {city}", "", f"Captured opportunity scope: {item['actual_row_count']} rows / {item['raw_unique_target_dates']} dates ({item['raw_weather_earliest_date']}..{item['raw_weather_latest_date']}).", ""]
    lines += ["Amsterdam historical-final archive: " + f"{historical_audit['raw_rows']} rows / {historical_audit['raw_target_dates']} dates ({historical_audit['earliest_target_date']}..{historical_audit['latest_target_date']}). It has zero original decision timestamps, so it is not strict PIT.", ""]
    (output / "CITY_DATA_COVERAGE_AND_BACKFILL_AUDIT.md").write_text("\n".join(lines), encoding="utf-8")
    (output / "AMSTERDAM_LABEL_AND_SOURCE_CONTRACT.md").write_text("""# Amsterdam label and source contract

- Fast source: KNMI Open Data notification at Schiphol; `ta` is point temperature, `tx` is the preceding 10-minute interval maximum, and revisions are append-only payload versions. `tn` is not present in the frozen input and fails closed.
- Pilot label: the next distinct EHAM routine official print after the decision vintage, expressed on the official 1°C lattice. The model target is `next_official_delta_native_tick` relative to the latest routine official print.
- Historical final archive provides observation-time KNMI and routine official values but no original first-seen. It is valid for weather-only development sensitivity, not strict PIT or lead-window claims.
- Captured PIT archive preserves source detect, decision-ready, next-official first-seen, and event identity.

## Provenance corrigendum

Stage-1 `wcir_amsterdam_next_print_v1` names `knmi_schiphol_10m` as the official source, while the accepted Stage-3 event builder and current Amsterdam runtime lineage use later EHAM routine METAR/WU state as the next official print. This pilot follows the executable/reaction lineage actually replayed by Stage 3 and does not rewrite Stage-1 evidence. The difference is explicit and must be reconciled before any cross-city canonical schema promotion.
""", encoding="utf-8")
    comparison = results["paired_comparisons"][results["best_simple_baseline"]]
    (output / "CALIBRATION_REPORT.md").write_text(f"""# Amsterdam calibration report

Primary model verdict: **{results['model_verdict']}**. Outer validation uses the last {results['outer_validation']['target_dates']} available historical target dates and date-equal RPS. M1 minus {results['best_simple_baseline']} RPS is {comparison['candidate_minus_baseline']:.6f}, target-date bootstrap 95% CI [{comparison['ci95'][0]:.6f}, {comparison['ci95'][1]:.6f}]. Calibration is expanding-OOF temperature scaling; the historical-final archive is not strict PIT.
""", encoding="utf-8")
    (output / "EXECUTABLE_REPLAY_POLICY.md").write_text("""# Executable replay policy

Decision time is the recorded Stage-3 `ts_utc`; entry is the first valid `source_t0` or later `pre_official` checkpoint strictly before official first-seen. Entry uses direct token buy sweeps and exits use direct token sell sweeps, exact recorded fees, at 5/15/30/60/120 seconds after official first-seen. Missing, stale, identity-invalid, or insufficient-depth books yield operational abstain and research PnL NULL. Primary size is 5 shares; 1 share is sensitivity. Selector threshold is frozen at weather transition probability minus executable cost >= 0.02. This is counterfactual replay, never a fill or actual PnL.
""", encoding="utf-8")
    (output / "ECONOMIC_REPLAY_REPORT.md").write_text(f"""# Amsterdam economic replay report

Verdict: **{replay['status']}**. The captured-PIT denominator has {funnel['raw_source_opportunities']} source opportunities; executable entry coverage is {funnel['executable_entry_eligible_1share']} events at 1 share and {funnel['executable_entry_eligible_5share']} at 5 shares. Market-only ordinal prior coverage is zero, so weather-vs-market uplift is not estimable. Actual orders/fills/notional are 0/0/0.
""", encoding="utf-8")


def build_schema() -> dict[str, Any]:
    return {
        "schema_version": "wcir_unified_data_schema_v1",
        "tables": {
            "weather_observations": ["observation_id", "city", "station_id", "source_id", "source_class", "observed_at", "issued_at", "first_seen_at", "available_at", "availability_class", "native_value", "native_unit", "quality_flag", "revision_id", "supersedes_observation_id", "raw_payload_hash"],
            "official_prints": ["official_print_id", "city", "target_date", "official_period_start", "official_period_end", "official_observed_at", "official_first_seen_at", "native_value", "native_tick", "revision_id", "settlement_relevance_status"],
            "decision_vintages": ["decision_vintage_id", "city", "target_date", "source_event_id", "next_official_print_id", "official_print_group_id", "decision_ready_at", "feature_cutoff_at", "lead_seconds"],
            "derived_weather_features": ["feature_name", "feature_value", "source_observation_ids", "feature_available_at", "feature_version"],
            "market_book_checkpoints": ["market_id", "condition_id", "token_id", "checkpoint_role", "checkpoint_at", "book_version_id", "book_valid", "book_age", "failure_reason", "bid", "ask", "depth", "sweep_1", "sweep_5", "fee_config"],
            "model_predictions": ["model_id", "artifact_hash", "decision_vintage_id", "prediction_created_at", "training_cutoff", "P(delta_tick=k)", "expected_delta_tick", "p_up", "p_down", "p_unchanged", "p_new_running_max", "predictive_entropy"],
        },
        "eligibility": ["WEATHER_LABEL_ELIGIBLE", "MARKET_PRIOR_ELIGIBLE", "EXECUTABLE_ENTRY_ELIGIBLE", "MARKOUT_ELIGIBLE"],
    }


def package(output: Path, timestamp: str) -> tuple[Path, str]:
    excluded = {"EVIDENCE_MANIFEST.json"}
    entries = []
    for path in sorted(p for p in output.rglob("*") if p.is_file() and not p.name.endswith((".zip", ".zip.sha256"))):
        if path.name in excluded:
            continue
        entries.append({"path": str(path.relative_to(output)), "size_bytes": path.stat().st_size, "sha256": sha256(path)})
    manifest = {
        "schema_version": "wcir_amsterdam_pilot_evidence_manifest_v1",
        "strict_entry_set": True,
        "generated_at_utc": utc_now(),
        "entries": entries,
    }
    write_json(output / "EVIDENCE_MANIFEST.json", manifest)
    verify_evidence_manifest(output)
    zip_path = output / f"wcir-unified-data-amsterdam-pilot-v1-{timestamp}.zip"
    members = [output / "EVIDENCE_MANIFEST.json"] + [output / row["path"] for row in entries]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(members):
            archive.write(path, arcname=path.relative_to(output))
    digest = sha256(zip_path)
    (Path(str(zip_path) + ".sha256")).write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    return zip_path, digest


def verify_evidence_manifest(output: Path) -> None:
    manifest_path = output / "EVIDENCE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {row["path"]: row for row in manifest["entries"]}
    actual = {
        str(path.relative_to(output)): path
        for path in output.rglob("*")
        if path.is_file()
        and path.name != "EVIDENCE_MANIFEST.json"
        and not path.name.endswith((".zip", ".zip.sha256"))
    }
    if set(actual) != set(expected):
        raise RuntimeError(
            f"evidence entry-set drift: missing={sorted(set(expected)-set(actual))} "
            f"extra={sorted(set(actual)-set(expected))}"
        )
    for relative, path in actual.items():
        row = expected[relative]
        if path.stat().st_size != row["size_bytes"] or sha256(path) != row["sha256"]:
            raise RuntimeError(f"evidence hash drift: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--forecast", type=Path, default=DEFAULT_FORECAST)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--aligned-books", type=Path, default=DEFAULT_ALIGNED)
    parser.add_argument("--book-truths", type=Path, default=DEFAULT_TRUTHS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--package", action="store_true")
    args = parser.parse_args()
    for path in (args.historical, args.forecast, args.events, args.aligned_books, args.book_truths):
        if not path.is_file():
            raise FileNotFoundError(path)
    output = ensure_safe_output(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "evidence").mkdir(exist_ok=True)
    generated = utc_now()

    historical, historical_audit = make_historical_panel(args.historical, args.forecast)
    captured, city_audit = make_captured_panel(args.events)
    city_audit["Amsterdam"].update({
        "historical_final_only_dates": historical_audit["raw_target_dates"],
        "expected_interval_count": historical_audit["expected_interval_count"],
        "missing_count": historical_audit["missing_interval_count"],
    })
    outer_dates = sorted(historical["target_date"].unique())[-20:]
    oof, outer, model_results, artifact = train_models(historical, outer_dates)
    artifact_path = output / "evidence" / "amsterdam_next_print_m1.joblib"
    joblib.dump(artifact, artifact_path, compress=3)
    captured_predictions = score_captured(captured, artifact)
    evaluate_captured_pit(historical, captured, captured_predictions, model_results)
    replay_rows, replay_funnel, replay_report = executable_replay(
        captured, captured_predictions, args.aligned_books, args.book_truths
    )

    oof.to_parquet(output / "OOF_PREDICTIONS.parquet", index=False, compression="zstd")
    outer.to_parquet(output / "HISTORICAL_OUTER_PREDICTIONS.parquet", index=False, compression="zstd")
    replay_rows.to_parquet(output / "EXECUTABLE_REPLAY_ROWS.parquet", index=False, compression="zstd")
    write_json(output / "UNIFIED_DATA_SCHEMA.json", build_schema())
    write_json(output / "CITY_DATA_COVERAGE_AND_BACKFILL_AUDIT.json", {"historical_amsterdam": historical_audit, "captured_opportunity_archive": city_audit})
    write_json(output / "AMSTERDAM_CANONICAL_PANEL_MANIFEST.json", {
        "historical_input": identity(args.historical, rows=historical_audit["raw_rows"]),
        "forecast_input": identity(args.forecast),
        "captured_input": identity(args.events, rows=sum(v["actual_row_count"] for v in city_audit.values())),
        "denominator_scope": "Amsterdam historical-final 10-minute KNMI checkpoints linked to next distinct routine EHAM official print",
        "panel_rows": len(historical), "panel_target_dates": historical["target_date"].nunique(),
        "captured_pit_rows": len(captured), "captured_pit_dates": captured["target_date"].nunique(),
        "strict_pit_training": False, "historical_first_seen_fabricated": False,
    })
    write_json(output / "AMSTERDAM_ELIGIBILITY_FUNNEL.json", {
        "historical_raw": historical_audit["raw_rows"],
        "historical_weather_label_eligible": len(historical),
        "historical_strict_pit_eligible": 0,
        "captured_raw": len(captured), "captured_weather_label_eligible": int(captured["weather_label_eligible"].sum()),
        **replay_funnel,
    })
    feature_manifest = {
        "feature_version": "wcir_amsterdam_next_print_core_features_v1",
        "primary_features": CORE_FEATURES,
        "challenger_monotonic_features": [{"name": n, "increasing": inc} for n, inc in GAM_FEATURES],
        "available_but_not_primary": ["ta", "tx", "tx_minus_ta", "solar", "cloud", "humidity", "wind", "rain"],
        "unavailable_fail_closed": ["tn", "source_age in HISTORICAL_FINAL_ARCHIVE", "true time_to_next_official in HISTORICAL_FINAL_ARCHIVE"],
        "future_data_used": False,
    }
    write_json(output / "AMSTERDAM_FEATURE_MANIFEST.json", feature_manifest)
    write_json(output / "MODEL_REGISTRY.json", {
        "baselines": ["B0_persistence", "B1_recent_slope", "B2_latest_fast_rounded", "B3_forecast_only_fixed_previous_day1", "B4_legacy_proxy_target_mismatch"],
        "primary": "M1_strongly_regularized_ordinal_logistic", "challenger": "M2_piecewise_linear_monotonic_additive",
        "market": ["MKT0_unavailable_ordinal_same_row", "FUSION1_not_fitted_fail_closed"],
        "no_roi_model_selection": True,
    })
    split_manifest = {
        "unit": "target_date block; official_print_group_id never crosses folds",
        "outer_dates": outer_dates,
        "outer_class": "HISTORICAL_OUTER_VALIDATION",
        "development_class": "EXPLORED_DEVELOPMENT",
        "captured_2026_08_class": "EXPLORED_DEVELOPMENT_due_to_prior_stage3_review",
        "prospective_frozen_start": generated,
        "random_row_split": False,
        "folds": artifact["fold_manifest"],
    }
    write_json(output / "SPLIT_AND_CONTAMINATION_MANIFEST.json", split_manifest)
    write_json(output / "MODEL_COMPARISON.json", model_results)
    write_json(output / "EXECUTABLE_REPLAY_FUNNEL.json", replay_funnel)
    write_json(output / "MARKET_ONLY_VS_WEATHER_UPLIFT.json", {
        "status": "NOT_ESTIMABLE", "reason": "zero Amsterdam rows have a complete same-row ordinal market prior and executable entry/exit denominator",
        "weather_minus_market_uplift": None, "no_synthetic_market_prior": True,
    })
    model_manifest = {
        "artifact": identity(artifact_path), "model_id": artifact["model_id"],
        "feature_manifest_hash": stable_hash(feature_manifest), "training_cutoff": artifact["training_cutoff"],
        "historical_validation_date_range": artifact["historical_outer_validation"],
        "model_verdict": model_results["model_verdict"], "live_eligible": False,
    }
    write_json(output / "FROZEN_MODEL_ARTIFACT_MANIFEST.json", model_manifest)
    shadow_enabled = model_results["model_verdict"] in {"PASS", "INCONCLUSIVE"}
    shadow_config = {
        "mode": "zero_notional", "enabled_for_deployment": False, "score_all_candidates": shadow_enabled,
        "negative_control_only": model_results["model_verdict"] == "FAIL",
        "model_id": artifact["model_id"], "artifact_sha256": model_manifest["artifact"]["sha256"],
        "selector_threshold": 0.02, "residual_cap": None, "fusion_beta": None,
        "sizes": [1, 5], "fee_config": "recorded_book_truth_exact_fee",
        "latency_policy": "recorded_decision_ready_ts_then_first_fresh_preofficial_book",
        "ttl_seconds": 1200, "book_freshness_policy": "stage2 materialized_book_validity_contract",
        "orders_allowed": False, "fills_expected": False, "notional_limit": 0,
    }
    write_json(output / "FROZEN_SHADOW_CONFIG.json", shadow_config)
    epoch = {
        "forward_epoch_id": f"wcir_amsterdam_next_print_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "created_at_utc": generated,
        "status": "CREATED_NOT_DEPLOYED" if shadow_enabled else "NOT_STARTED_MODEL_FAIL",
        "model_artifact_sha256": model_manifest["artifact"]["sha256"],
        "config_hash": stable_hash(shadow_config), "hot_tuning_allowed": False,
        "new_predictions": 0, "orders": 0, "fills": 0, "notional": 0,
    }
    write_json(output / "FORWARD_EPOCH_MANIFEST.json", epoch)
    write_json(output / "ZERO_NOTIONAL_AUDIT.json", {"orders": 0, "fills": 0, "notional": 0, "production_config_changed": False, "order_path_changed": False})
    write_docs(output, historical_audit, city_audit, model_results, replay_report, replay_funnel, generated)
    write_json(output / "evidence" / "CAPTURED_PIT_PREDICTIONS.json", {
        "rows": len(captured_predictions), "target_dates": captured_predictions["target_date"].nunique(),
        "records_hash": stable_hash(captured_predictions.round(10).to_dict("records")),
    })
    shutil.copy2(Path(__file__), output / "evidence" / Path(__file__).name)
    (output / "REPRODUCE_WCIR_AMSTERDAM_PILOT.sh").write_text(
        "#!/bin/sh\nset -eu\n.venv/bin/python scripts/analysis/forecast_quality/wcir_unified_amsterdam_pilot.py --package\n",
        encoding="utf-8",
    )
    os.chmod(output / "REPRODUCE_WCIR_AMSTERDAM_PILOT.sh", 0o755)
    (output / "TEST_COMMANDS_AND_RAW_OUTPUT.txt").write_text(
        "Pending final independent review and test seal. This placeholder is replaced after the final test run.\n",
        encoding="utf-8",
    )
    packet = f"""# GPT Pro review packet — WCIR Amsterdam pilot v1

## Requested review

1. Accept or reject the three-layer data contract and the explicit Stage-1/Stage-3 Amsterdam official-source corrigendum.
2. Review M1 ordinal next-routine-official model on the frozen 20-date historical outer block.
3. Review the fail-closed decision not to fit FUSION1 or claim executable uplift with zero complete market-prior denominator.
4. Review the zero-notional, not-deployed forward epoch and decide whether continued Amsterdam collection is warranted.

## Direct answers

- Historical raw coverage: {historical_audit['raw_rows']} rows / {historical_audit['raw_target_dates']} dates, {historical_audit['earliest_target_date']}..{historical_audit['latest_target_date']}.
- Historical-final vs PIT: all historical training rows are `HISTORICAL_FINAL_ARCHIVE`; strict captured PIT is {len(captured)} events / {captured['target_date'].nunique()} dates.
- Target: ordinal next EHAM routine official print delta on the 1°C official lattice.
- Model verdict: {model_results['model_verdict']}; best simple baseline is {model_results['best_simple_baseline']}.
- Executable replay: {replay_report['status']}; 1-share entry coverage {replay_funnel['executable_entry_eligible_1share']}, 5-share {replay_funnel['executable_entry_eligible_5share']}.
- Market-only uplift: NOT ESTIMABLE; no synthetic price or midpoint substitution was used.
- Frozen artifact: `{model_manifest['artifact']['sha256']}`; forward epoch is created but not deployed.
- Orders/fills/notional: 0/0/0.

## Requested disposition

Choose one: `ACCEPT_AMSTERDAM_PILOT_FOR_ZERO_NOTIONAL_FORWARD`, `ACCEPT_WITH_BLOCKING_FIXES`, `REWORK_AMSTERDAM_DATA_OR_MODEL_CONTRACT`, `STOP_DUE_TO_UNCONTROLLED_LIVE_OR_DATA_RISK`.
"""
    (output / "GPT_PRO_REVIEW_PACKET.md").write_text(packet, encoding="utf-8")
    if args.package:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        zip_path, digest = package(output, timestamp)
        print(stable_json({"status": "complete", "zip": str(zip_path), "sha256": digest, "model_verdict": model_results["model_verdict"], "replay": replay_report["status"]}))
    else:
        print(stable_json({"status": "complete_no_package", "output": str(output), "model_verdict": model_results["model_verdict"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
