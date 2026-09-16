#!/usr/bin/env python3
"""Sequential d1 overshoot hazard model.

The model is anchored at the observed current bracket and estimates:

    h0 = P(final reaches d1)
    h1 = P(final reaches d2+ | final reaches d1)

which yields the coherent three-state distribution:

    P(stall current) = 1 - h0
    P(exact d1)      = h0 * (1 - h1)
    P(overshoot d2+) = h0 * h1

All model evaluation is date-expanding OOF.  The fixed d1 strategy denominator
is the first city-day state with d1 YES mid >= 0.80.  Live rows are scored only
after the historical model is frozen and never participate in fitting.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from scipy.stats import fisher_exact

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.city_calendar import CITY_TIMEZONE  # noqa: E402
from weather_data_feed.market_brackets import parse_market_bracket  # noqa: E402
from weather_data_feed_service.legacy_weather_predict.paper_snapshot import (  # noqa: E402
    CITY_MODEL,
)


ATLAS = (
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
    / "intraday_weather_regime_state_rows.csv"
)
FACTORY = (
    ROOT
    / "docs/analysis/2026-06/generated/reheat_feature_factory_v1"
    / "reheat_feature_rows.csv"
)
LIVE_RAW = ROOT / "runtime/weather_edge_v1/d1_yes_high_mid_live_v1/shadow_events.jsonl"
FROZEN_FIRST = (
    ROOT
    / "docs/analysis/2026-07/generated/d1_yes_high_mid_regime_v1"
    / "historical_first_signals.csv"
)
OUTPUT = ROOT / "docs/analysis/2026-07/generated/d1_overshoot_hazard_v1"
DB_PATH = ROOT / "runtime/weather.db"

KEY = ["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"]
CLASSES = ["stall_current", "exact_d1", "overshoot_d2plus"]
KNOWN_FORECAST_POLLUTION = {"2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"}
LIVE_INCIDENT_KEYS = {
    ("Singapore", "2026-07-19"),
    ("Beijing", "2026-07-19"),
    ("Busan", "2026-07-19"),
    ("Chongqing", "2026-07-19"),
}
FORWARD_START = "2026-06-21"
HISTORICAL_END = "2026-07-07"
MIN_TRAIN_DATES = 12
MODEL_C = 0.1
BOOTSTRAP_DRAWS = 4000
SEED = 20260723

MARKET_NUMERIC = ["market_h0_logit", "market_h1_logit"]
PATH_NUMERIC = [
    "decision_hour_local",
    "forecast_gap_to_running_native",
    "forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "forecast_ceiling_margin_to_d2",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "decline_native",
    "relative_humidity_pct",
    "dewpoint_depression_f",
    "wind_speed_kt",
    "sky_cover_code",
    "obs_age_min",
    "obs_count_to_decision",
    "d1_required_gap_native",
    "d2_required_gap_native",
    "rungs_above_d1",
    "market_current_plus_overround",
]
REGIME_CATEGORICAL = [
    "unit",
    "city_family",
    "solar_window",
    "day_regime",
    "intraday_state",
    "running_max_state",
    "moisture_cloud_regime",
    "wind_regime",
    "forecast_source",
    "forecast_clock_source",
]
MODEL_SPECS = {
    "market_calibrated": (MARKET_NUMERIC, []),
    "physics_path": (PATH_NUMERIC, []),
    "physics_only": (PATH_NUMERIC, REGIME_CATEGORICAL),
    "market_plus_path": (MARKET_NUMERIC + PATH_NUMERIC, []),
    "market_plus_path_regime": (
        MARKET_NUMERIC + PATH_NUMERIC,
        REGIME_CATEGORICAL,
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clean_label(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).replace("°C", "").replace("°F", "").replace("°", "").strip()
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else str(number)


def _sort_key(label: str) -> tuple[float, float]:
    parsed = parse_market_bracket(label)
    if parsed is None:
        return (math.inf, math.inf)
    low = -math.inf if parsed.bottom else float(parsed.low) if parsed.low is not None else math.inf
    high = math.inf if parsed.top else float(parsed.high) if parsed.high is not None else math.inf
    return (low, high)


def _bracket_low(label: str | None) -> float | None:
    if label is None:
        return None
    parsed = parse_market_bracket(label)
    return None if parsed is None or parsed.low is None else float(parsed.low)


def _settlement_threshold(label: str | None, unit: Any) -> float | None:
    """Lowest continuous native value that rounds into a displayed bracket."""
    low = _bracket_low(label)
    if low is None:
        return None
    # Celsius exact/top brackets are labels on the rounded settlement lattice.
    # Converted station values such as 27.78C therefore settle in the 28
    # bracket; using 28.0 as the reach threshold overstates the required heat.
    if str(unit or "").upper() == "C":
        return low - 0.5
    return low


def _logit(value: pd.Series | np.ndarray | float) -> Any:
    return np.log(np.clip(value, 1e-6, 1 - 1e-6) / np.clip(1 - value, 1e-6, 1 - 1e-6))


def _weather_fee(price: pd.Series | float) -> pd.Series | float:
    return 0.05 * price * (1.0 - price)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _positive_probability(model: Pipeline, rows: pd.DataFrame) -> np.ndarray:
    probabilities = model.predict_proba(rows)
    classes = list(model.named_steps["model"].classes_)
    return probabilities[:, classes.index(1)]


def _pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    transformers: list[tuple[str, Any, list[str]]] = []
    if numeric:
        transformers.append(
            (
                "numeric",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                        ("scale", StandardScaler()),
                    ]
                ),
                numeric,
            )
        )
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical,
            )
        )
    return Pipeline(
        [
            ("features", ColumnTransformer(transformers)),
            (
                "model",
                LogisticRegression(
                    C=MODEL_C,
                    solver="liblinear",
                    max_iter=1000,
                    random_state=SEED,
                ),
            ),
        ]
    )


@dataclass
class SequentialHazard:
    numeric: list[str]
    categorical: list[str]
    h0: Pipeline | None = None
    h1: Pipeline | None = None

    def fit(self, rows: pd.DataFrame) -> "SequentialHazard":
        self.h0 = _pipeline(self.numeric, self.categorical)
        self.h0.fit(rows, rows["reach_d1"].astype(int))
        reached = rows[rows["reach_d1"].eq(1)].copy()
        if reached["reach_d2"].nunique() < 2:
            raise ValueError("conditional d2 head has fewer than two classes")
        self.h1 = _pipeline(self.numeric, self.categorical)
        self.h1.fit(reached, reached["reach_d2"].astype(int))
        return self

    def predict(self, rows: pd.DataFrame) -> pd.DataFrame:
        if self.h0 is None or self.h1 is None:
            raise RuntimeError("model not fitted")
        h0 = np.clip(_positive_probability(self.h0, rows), 1e-6, 1 - 1e-6)
        h1 = np.clip(_positive_probability(self.h1, rows), 1e-6, 1 - 1e-6)
        return pd.DataFrame(
            {
                "p_stall": 1.0 - h0,
                "p_exact": h0 * (1.0 - h1),
                "p_overshoot": h0 * h1,
                "hazard_reach_d1": h0,
                "hazard_reach_d2_given_d1": h1,
            },
            index=rows.index,
        )


def _yes_mid(bid: Any, ask: Any) -> float | None:
    bid_value, ask_value = _finite(bid), _finite(ask)
    if bid_value is not None and ask_value is not None:
        return (bid_value + ask_value) / 2.0
    return bid_value if bid_value is not None else ask_value


def _market_from_rungs(
    rungs: list[dict[str, Any]],
    current_label: str,
    d1_label: str,
) -> dict[str, Any] | None:
    ordered = sorted(rungs, key=lambda row: _sort_key(str(row["bracket"])))
    labels = [_clean_label(row["bracket"]) for row in ordered]
    current = _clean_label(current_label)
    d1 = _clean_label(d1_label)
    if current not in labels or d1 not in labels:
        return None
    current_index, d1_index = labels.index(current), labels.index(d1)
    if d1_index != current_index + 1 or d1_index + 1 >= len(ordered):
        return None
    mids = [_yes_mid(row.get("yes_bid"), row.get("yes_ask")) for row in ordered]
    current_plus = mids[current_index:]
    if any(value is None for value in current_plus):
        return {
            "market_score_ready": False,
            "current_rung_index": current_index,
            "d1_rung_index": d1_index,
            "d2_bracket_market": labels[d1_index + 1],
        }
    total = float(sum(float(value) for value in current_plus))
    if total <= 0:
        return None
    p_stall = float(mids[current_index]) / total
    p_exact = float(mids[d1_index]) / total
    p_over = float(sum(float(value) for value in mids[d1_index + 1 :])) / total
    d1_row = ordered[d1_index]
    return {
        "market_score_ready": True,
        "market_p_stall": p_stall,
        "market_p_exact": p_exact,
        "market_p_overshoot": p_over,
        "market_h0": 1.0 - p_stall,
        "market_h1": p_over / max(p_exact + p_over, 1e-9),
        "market_current_plus_overround": total,
        "market_below_current_mass": float(
            sum(float(value) for value in mids[:current_index] if value is not None)
        ),
        "d1_yes_ladder_bid": _finite(d1_row.get("yes_bid")),
        "d1_yes_ladder_ask": _finite(d1_row.get("yes_ask")),
        "current_rung_index": current_index,
        "d1_rung_index": d1_index,
        "d2_bracket_market": labels[d1_index + 1],
        "d1_bracket_low": _bracket_low(labels[d1_index]),
        "d2_bracket_low": _bracket_low(labels[d1_index + 1]),
        "rungs_above_d1": len(ordered) - d1_index - 1,
        "ladder_rungs": len(ordered),
    }


def build_historical_states(atlas_path: Path, factory_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    atlas = pd.read_csv(atlas_path, low_memory=False)
    fixed_forecast_columns = [
        f"{model}_{field}"
        for model in ["gfs", "ecmwf"]
        for field in [
            "forecast_max_native",
            "forecast_peak_hour_local",
            "forecast_peak_time_local",
            "forecast_values_hash",
            "forecast_run_time_utc",
            "forecast_run_policy",
        ]
    ]
    atlas = atlas.drop(
        columns=[column for column in fixed_forecast_columns if column in atlas.columns]
    )
    factory_columns = [
        *KEY,
        "bracket",
        "bracket_low",
        "bracket_high",
        "outcome",
        "target_yes_bid",
        "target_yes_ask",
        "current_bracket",
        "d1_no_bracket",
        "d2_no_bracket",
        "decision_last_obs_utc",
        "obs_count_day",
        "obs_count_to_decision",
    ]
    factory = pd.read_csv(factory_path, usecols=factory_columns, low_memory=False)
    factory["bracket"] = factory["bracket"].map(_clean_label)
    factory = factory.sort_values(KEY + ["bracket_low", "bracket_high", "outcome"])
    rung_rows = factory.drop_duplicates(KEY + ["bracket"], keep="first").copy()
    rung_rows["yes_bid"] = pd.to_numeric(rung_rows["target_yes_bid"], errors="coerce")
    rung_rows["yes_ask"] = pd.to_numeric(rung_rows["target_yes_ask"], errors="coerce")
    market_rows: list[dict[str, Any]] = []
    for key, group in rung_rows.groupby(KEY, sort=False, dropna=False):
        base = group.iloc[0]
        result = _market_from_rungs(
            group[["bracket", "yes_bid", "yes_ask"]].to_dict("records"),
            str(base["current_bracket"]),
            str(base["d1_no_bracket"]),
        )
        if result is not None:
            result.update(dict(zip(KEY, key)))
            market_rows.append(result)
    market = pd.DataFrame(market_rows)
    states = atlas.merge(market, on=KEY, how="left", validate="one_to_one")
    source_feature_columns = [
        "decision_last_obs_utc",
        "obs_count_day",
        "obs_count_to_decision",
        *fixed_forecast_columns,
    ]
    dual_frames = []
    source_forecast_file_hashes: dict[str, str] = {}
    source_forecast_state_groups = 0
    source_forecast_conflict_groups = 0
    for source_file in sorted(atlas["source_files"].dropna().astype(str).unique()):
        source_path = ROOT / source_file
        if not source_path.exists():
            continue
        source_forecast_file_hashes[source_file] = _sha256(source_path)
        dual = pd.read_csv(
            source_path,
            usecols=KEY + source_feature_columns,
            low_memory=False,
        )
        grouped_all = dual.groupby(KEY, dropna=False)
        conflicts = (
            grouped_all[fixed_forecast_columns]
            .nunique(dropna=True)
            .gt(1)
            .any(axis=1)
        )
        source_forecast_state_groups += len(conflicts)
        source_forecast_conflict_groups += int(conflicts.sum())
        dual = grouped_all[source_feature_columns].first().reset_index()
        dual["source_files"] = source_file
        dual_frames.append(dual)
    if not dual_frames:
        raise ValueError("no source-aligned fixed-model forecast rows found")
    source_aligned_forecasts = pd.concat(dual_frames, ignore_index=True)
    states = states.merge(
        source_aligned_forecasts,
        on=KEY + ["source_files"],
        how="left",
        validate="one_to_one",
    )
    states["forecast_source_raw_mixed"] = states["forecast_source"]
    states["forecast_max_native_raw_mixed"] = states["forecast_max_native"]
    states["forecast_peak_hour_local_raw_mixed"] = states["forecast_peak_hour_local"]
    states["forecast_peak_delta_hours_local_raw_mixed"] = states[
        "forecast_peak_delta_hours_local"
    ]
    states["forecast_assigned_model"] = states["city"].map(CITY_MODEL).fillna("gfs")
    assigned_ecmwf = states["forecast_assigned_model"].eq("ecmwf")
    for field in [
        "forecast_max_native",
        "forecast_peak_hour_local",
        "forecast_peak_time_local",
        "forecast_values_hash",
        "forecast_run_time_utc",
        "forecast_run_policy",
    ]:
        states[field] = np.where(
            assigned_ecmwf,
            states[f"ecmwf_{field}"],
            states[f"gfs_{field}"],
        )
    states["forecast_source"] = np.where(
        pd.to_numeric(states["forecast_max_native"], errors="coerce").notna(),
        "fixed_city_model_" + states["forecast_assigned_model"].astype(str),
        "fixed_city_model_missing",
    )
    states["label"] = np.select(
        [
            pd.to_numeric(states["current_bracket_held"], errors="coerce").eq(1),
            pd.to_numeric(states["d1_hit"], errors="coerce").eq(1),
            pd.to_numeric(states["skip_over_d1"], errors="coerce").eq(1),
        ],
        CLASSES,
        default="other",
    )
    states["reach_d1"] = states["label"].isin(CLASSES[1:]).astype(int)
    states["reach_d2"] = states["label"].eq(CLASSES[2]).astype(int)
    states["mechanism_available"] = (
        states["label"].isin(CLASSES)
        & states["d1_no_bid"].notna()
        & states["d1_no_ask"].notna()
        & states["target_date"].le(HISTORICAL_END)
    )
    states["model_eligible"] = (
        states["mechanism_available"]
        & ~states["target_date"].isin(KNOWN_FORECAST_POLLUTION)
    )
    states["market_eval_eligible"] = (
        states["model_eligible"] & states["market_score_ready"].eq(True)
    )
    decision = pd.to_datetime(states["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    last_obs = pd.to_datetime(states["decision_last_obs_utc"], utc=True, errors="coerce")
    states["obs_age_min"] = (decision - last_obs).dt.total_seconds() / 60.0
    states["forecast_peak_delta_hours_local_raw"] = pd.to_numeric(
        states["forecast_peak_delta_hours_local_raw_mixed"], errors="coerce"
    )
    states["forecast_peak_delta_hours_local"] = math.nan
    expected_peak_delta = (
        pd.to_numeric(states["decision_hour_local"], errors="coerce")
        - pd.to_numeric(states["forecast_peak_hour_local"], errors="coerce")
    )
    states["forecast_peak_delta_inconsistent"] = (
        states["forecast_peak_delta_hours_local_raw"].notna()
        & expected_peak_delta.notna()
        & states["forecast_peak_delta_hours_local_raw"].sub(expected_peak_delta).abs().gt(1.01)
    )
    states.loc[expected_peak_delta.notna(), "forecast_peak_delta_hours_local"] = (
        expected_peak_delta[expected_peak_delta.notna()]
    )
    states["forecast_clock_source"] = np.where(
        expected_peak_delta.notna(),
        "fixed_city_model_single_runs",
        "fixed_city_model_missing",
    )
    states["forecast_gap_to_running_native"] = (
        pd.to_numeric(states["forecast_max_native"], errors="coerce")
        - pd.to_numeric(states["running_native"], errors="coerce")
    )
    states["d1_settlement_threshold_native"] = [
        _settlement_threshold(label, unit)
        for label, unit in zip(states["d1_no_bracket"], states["unit"])
    ]
    states["d2_settlement_threshold_native"] = [
        _settlement_threshold(label, unit)
        for label, unit in zip(states["d2_no_bracket"], states["unit"])
    ]
    states["d1_required_gap_native"] = (
        pd.to_numeric(states["d1_settlement_threshold_native"], errors="coerce")
        - pd.to_numeric(states["running_native"], errors="coerce")
    )
    states["d2_required_gap_native"] = (
        pd.to_numeric(states["d2_settlement_threshold_native"], errors="coerce")
        - pd.to_numeric(states["running_native"], errors="coerce")
    )
    states["forecast_ceiling_margin_to_d2"] = (
        pd.to_numeric(states["forecast_max_native"], errors="coerce")
        - pd.to_numeric(states["d2_settlement_threshold_native"], errors="coerce")
    )
    states["market_h0_logit"] = _logit(pd.to_numeric(states["market_h0"], errors="coerce"))
    states["market_h1_logit"] = _logit(pd.to_numeric(states["market_h1"], errors="coerce"))
    states["d1_yes_mid_trigger"] = 1.0 - (
        pd.to_numeric(states["d1_no_ask"], errors="coerce")
        + pd.to_numeric(states["d1_no_bid"], errors="coerce")
    ) / 2.0
    states["d1_yes_ask_exec"] = 1.0 - pd.to_numeric(states["d1_no_bid"], errors="coerce")
    states["fee"] = _weather_fee(states["d1_yes_ask_exec"])
    states["cost"] = states["d1_yes_ask_exec"] + states["fee"]
    states["win"] = states["label"].eq(CLASSES[1]).astype(int)
    states["pnl"] = states["win"] - states["cost"]
    audit = {
        "atlas_rows": len(atlas),
        "factory_rows": len(factory),
        "market_states": len(market),
        "market_score_ready": int(states["market_score_ready"].eq(True).sum()),
        "three_state_labels": int(states["label"].isin(CLASSES).sum()),
        "mechanism_available": int(states["mechanism_available"].sum()),
        "model_eligible_after_pollution_exclusion": int(states["model_eligible"].sum()),
        "market_eval_eligible": int(states["market_eval_eligible"].sum()),
        "excluded_pollution_mechanism_rows": int(
            (
                states["mechanism_available"]
                & states["target_date"].isin(KNOWN_FORECAST_POLLUTION)
            ).sum()
        ),
        "forecast_peak_delta_inconsistent_mechanism_rows": int(
            (states["mechanism_available"] & states["forecast_peak_delta_inconsistent"]).sum()
        ),
        "fixed_city_model_forecast_mechanism_rows": int(
            (
                states["mechanism_available"]
                & pd.to_numeric(states["forecast_max_native"], errors="coerce").notna()
            ).sum()
        ),
        "source_forecast_file_hashes": source_forecast_file_hashes,
        "source_forecast_state_groups": source_forecast_state_groups,
        "source_forecast_conflict_groups": source_forecast_conflict_groups,
        "celsius_d2_threshold_half_step_rows": int(
            (
                states["mechanism_available"]
                & states["unit"].eq("C")
                & states["d2_settlement_threshold_native"].notna()
            ).sum()
        ),
        "label_counts_all": states["label"].value_counts().to_dict(),
    }
    return states, audit


def expanding_oof(rows: pd.DataFrame) -> pd.DataFrame:
    eligible = rows[rows["model_eligible"]].copy().sort_values(KEY)
    dates = sorted(eligible["target_date"].unique())
    predictions: list[pd.DataFrame] = []
    for date_index, date in enumerate(dates):
        if date_index < MIN_TRAIN_DATES:
            continue
        train_all = eligible[eligible["target_date"].lt(date)]
        train_market = train_all[train_all["market_score_ready"].eq(True)]
        test_all = eligible[eligible["target_date"].eq(date)]
        # Market models require the exact complete-ladder denominator. Physical
        # models can also score the wider frozen strategy cohort.
        test_market = eligible[
            eligible["target_date"].eq(date) & eligible["market_score_ready"].eq(True)
        ]
        if not test_market.empty:
            raw = test_market[KEY + ["label", "reach_d1", "reach_d2"]].copy()
            raw["model"] = "market_raw"
            raw["p_stall"] = test_market["market_p_stall"].to_numpy()
            raw["p_exact"] = test_market["market_p_exact"].to_numpy()
            raw["p_overshoot"] = test_market["market_p_overshoot"].to_numpy()
            raw["hazard_reach_d1"] = test_market["market_h0"].to_numpy()
            raw["hazard_reach_d2_given_d1"] = test_market["market_h1"].to_numpy()
            raw["train_dates"] = date_index
            predictions.append(raw)
        for model_name, (numeric, categorical) in MODEL_SPECS.items():
            needs_market = bool(set(numeric) & set(MARKET_NUMERIC))
            train = train_market if needs_market else train_all
            test = test_market if needs_market else test_all
            if test.empty:
                continue
            estimator = SequentialHazard(numeric, categorical).fit(train)
            block = test[KEY + ["label", "reach_d1", "reach_d2"]].copy()
            block["model"] = model_name
            predicted = estimator.predict(test)
            for column in predicted:
                block[column] = predicted[column].to_numpy()
            block["train_dates"] = date_index
            predictions.append(block)
    return pd.concat(predictions, ignore_index=True)


def _row_scores(frame: pd.DataFrame) -> pd.DataFrame:
    scored = frame.copy()
    class_index = {name: index for index, name in enumerate(CLASSES)}
    probability_columns = ["p_stall", "p_exact", "p_overshoot"]
    probabilities = np.clip(scored[probability_columns].to_numpy(float), 1e-9, 1.0)
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    actual = scored["label"].map(class_index).to_numpy(int)
    one_hot = np.eye(3)[actual]
    scored["logloss"] = -np.log(probabilities[np.arange(len(scored)), actual])
    scored["brier"] = ((probabilities - one_hot) ** 2).sum(axis=1)
    scored["exact_brier"] = (probabilities[:, 1] - one_hot[:, 1]) ** 2
    scored["overshoot_brier"] = (probabilities[:, 2] - one_hot[:, 2]) ** 2
    return scored


def _bootstrap_mean(values: np.ndarray, seed: int) -> tuple[float, float]:
    values = values[np.isfinite(values)]
    if len(values) < 3:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(values), size=(BOOTSTRAP_DRAWS, len(values)))
    means = values[indices].mean(axis=1)
    return tuple(float(value) for value in np.quantile(means, [0.025, 0.975]))


def summarize_probability_models(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    scored = _row_scores(predictions)
    common_keys = scored.loc[scored["model"].eq("market_raw"), KEY].drop_duplicates()
    scored = scored.merge(common_keys, on=KEY, how="inner", validate="many_to_one")
    summaries = []
    for model, group in scored.groupby("model"):
        daily = group.groupby("target_date")[["logloss", "brier"]].mean()
        try:
            over_auc = roc_auc_score(group["label"].eq(CLASSES[2]), group["p_overshoot"])
        except ValueError:
            over_auc = math.nan
        summaries.append(
            {
                "model": model,
                "rows": len(group),
                "dates": group["target_date"].nunique(),
                "logloss_date_equal": daily["logloss"].mean(),
                "logloss_row_mean": group["logloss"].mean(),
                "brier_date_equal": daily["brier"].mean(),
                "brier_row_mean": group["brier"].mean(),
                "exact_brier": group["exact_brier"].mean(),
                "overshoot_brier": group["overshoot_brier"].mean(),
                "overshoot_auc": over_auc,
            }
        )
    summary = pd.DataFrame(summaries).sort_values("logloss_date_equal")
    baseline = scored[scored["model"].eq("market_raw")][KEY + ["logloss", "brier"]].rename(
        columns={"logloss": "market_logloss", "brier": "market_brier"}
    )
    deltas = []
    for model in sorted(set(scored["model"]) - {"market_raw"}):
        joined = scored[scored["model"].eq(model)].merge(baseline, on=KEY, validate="one_to_one")
        joined["logloss_delta"] = joined["logloss"] - joined["market_logloss"]
        joined["brier_delta"] = joined["brier"] - joined["market_brier"]
        daily = joined.groupby("target_date")[["logloss_delta", "brier_delta"]].mean()
        ll_ci = _bootstrap_mean(daily["logloss_delta"].to_numpy(), SEED + len(deltas))
        br_ci = _bootstrap_mean(daily["brier_delta"].to_numpy(), SEED + 100 + len(deltas))
        deltas.append(
            {
                "model": model,
                "rows": len(joined),
                "dates": joined["target_date"].nunique(),
                "logloss_delta_vs_market": daily["logloss_delta"].mean(),
                "logloss_delta_ci_low": ll_ci[0],
                "logloss_delta_ci_high": ll_ci[1],
                "brier_delta_vs_market": daily["brier_delta"].mean(),
                "brier_delta_ci_low": br_ci[0],
                "brier_delta_ci_high": br_ci[1],
            }
        )
    return summary, pd.DataFrame(deltas).sort_values("logloss_delta_vs_market")


def first_signal_rows(frozen_path: Path) -> pd.DataFrame:
    """Load the frozen 218-row strategy cohort without regenerating it."""
    first = pd.read_csv(frozen_path, low_memory=False)
    first["label"] = first["loss_mode"].map(
        {
            "win_exact_d1": CLASSES[1],
            "overshoot_d2_or_higher": CLASSES[2],
            "stall_at_current": CLASSES[0],
        }
    )
    if first["label"].isna().any():
        raise ValueError("frozen first-signal cohort contains an unmapped outcome")
    first["d1_yes_mid_trigger"] = pd.to_numeric(first["d1_yes_mid"], errors="raise")
    first["d1_yes_ask_exec"] = pd.to_numeric(first["d1_yes_ask"], errors="raise")
    first["win"] = first["label"].eq(CLASSES[1]).astype(int)
    for column in ["cost", "pnl"]:
        first[column] = pd.to_numeric(first[column], errors="raise")
    if len(first) != 218 or first[["city", "target_date"]].duplicated().any():
        raise ValueError("frozen cohort is not the expected 218 unique city-days")
    return first


def _roi_ci(rows: pd.DataFrame, seed: int) -> tuple[float, float, float]:
    if rows.empty or rows["cost"].sum() <= 0:
        return (math.nan, math.nan, math.nan)
    point = float(rows["pnl"].sum() / rows["cost"].sum())
    daily = rows.groupby("target_date")[["pnl", "cost"]].sum()
    if len(daily) < 3:
        return (point, math.nan, math.nan)
    rng = np.random.default_rng(seed)
    values = daily.to_numpy(float)
    boot = np.empty(BOOTSTRAP_DRAWS)
    for index in range(BOOTSTRAP_DRAWS):
        sample = values[rng.integers(0, len(values), len(values))]
        boot[index] = sample[:, 0].sum() / sample[:, 1].sum()
    low, high = np.quantile(boot, [0.025, 0.975])
    return point, float(low), float(high)


def evaluate_first_signals(
    first: pd.DataFrame, predictions: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base_columns = KEY + [
        "label",
        "win",
        "cost",
        "pnl",
        "d1_yes_mid_trigger",
        "d1_yes_ask_exec",
    ]
    joined = predictions.merge(first[base_columns], on=KEY + ["label"], validate="many_to_one")
    joined["model_edge"] = joined["p_exact"] - joined["cost"]
    scored = _row_scores(joined)
    common_keys = scored.loc[scored["model"].eq("market_raw"), KEY].drop_duplicates()
    common_scored = scored.merge(common_keys, on=KEY, how="inner", validate="many_to_one")
    model_summary = []
    policy_summary = []
    risk_summary = []
    for model, group in scored.groupby("model"):
        common_group = common_scored[common_scored["model"].eq(model)]
        daily = common_group.groupby("target_date")[["logloss", "brier"]].mean()
        try:
            auc = roc_auc_score(
                common_group["label"].eq(CLASSES[2]), common_group["p_overshoot"]
            )
        except ValueError:
            auc = math.nan
        model_summary.append(
            {
                "model": model,
                "rows": len(common_group),
                "dates": common_group["target_date"].nunique(),
                "logloss_date_equal": daily["logloss"].mean(),
                "brier_date_equal": daily["brier"].mean(),
                "overshoot_auc": auc,
            }
        )
        for threshold in [0.0, 0.01, 0.02, 0.03, 0.05]:
            selected = group[group["model_edge"].gt(threshold)].copy()
            roi, low, high = _roi_ci(selected, SEED + len(policy_summary))
            policy_summary.append(
                {
                    "model": model,
                    "edge_threshold": threshold,
                    "rows": len(selected),
                    "dates": selected["target_date"].nunique(),
                    "wins": int(selected["win"].sum()),
                    "cost": selected["cost"].sum(),
                    "pnl": selected["pnl"].sum(),
                    "roi": roi,
                    "roi_ci_low": low,
                    "roi_ci_high": high,
                }
            )
        train = group[group["target_date"].lt(FORWARD_START)]
        if train.empty:
            continue
        cutoff = float(train["p_overshoot"].quantile(0.75))
        for period, period_rows in [
            ("train", train),
            ("historical_forward", group[group["target_date"].ge(FORWARD_START)]),
        ]:
            removed = period_rows[period_rows["p_overshoot"].ge(cutoff)]
            retained = period_rows[period_rows["p_overshoot"].lt(cutoff)].copy()
            all_roi, _, _ = _roi_ci(
                period_rows, SEED + 900 + len(risk_summary)
            )
            roi, low, high = _roi_ci(retained, SEED + 1000 + len(risk_summary))
            risk_summary.append(
                {
                    "model": model,
                    "period": period,
                    "frozen_p75_cutoff": cutoff,
                    "rows": len(period_rows),
                    "removed_rows": len(removed),
                    "removed_overshoots": int(removed["label"].eq(CLASSES[2]).sum()),
                    "removed_stalls": int(removed["label"].eq(CLASSES[0]).sum()),
                    "removed_winners": int(removed["label"].eq(CLASSES[1]).sum()),
                    "retained_rows": len(retained),
                    "all_roi": all_roi,
                    "retained_roi": roi,
                    "retained_roi_delta": roi - all_roi
                    if math.isfinite(roi) and math.isfinite(all_roi)
                    else math.nan,
                    "retained_roi_ci_low": low,
                    "retained_roi_ci_high": high,
                }
            )
    return (
        scored,
        pd.DataFrame(model_summary).sort_values("logloss_date_equal"),
        pd.concat(
            [
                pd.DataFrame(policy_summary).assign(summary_type="edge_policy"),
                pd.DataFrame(risk_summary).assign(summary_type="overshoot_p75_filter"),
            ],
            ignore_index=True,
            sort=False,
        ),
    )


def _snapshot_rungs(path: Path, city: str, target_date: str) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    records = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if str(row.get("city")) != city:
                continue
            event_date = str(row.get("event_date") or row.get("market_local_date") or "")
            if event_date != target_date:
                continue
            records.append(row)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        label = _clean_label(row.get("bracket"))
        if label is not None:
            grouped.setdefault(label, []).append(row)
    rungs = []
    for label, rows in grouped.items():
        bid_candidates: list[float] = []
        ask_candidates: list[float] = []
        for row in rows:
            summary = row.get("summary") or {}
            bid, ask = _finite(summary.get("best_bid")), _finite(summary.get("best_ask"))
            if str(row.get("outcome")).lower() == "yes":
                if bid is not None:
                    bid_candidates.append(bid)
                if ask is not None:
                    ask_candidates.append(ask)
            else:
                if ask is not None:
                    bid_candidates.append(1.0 - ask)
                if bid is not None:
                    ask_candidates.append(1.0 - bid)
        rungs.append(
            {
                "bracket": label,
                "yes_bid": max(bid_candidates) if bid_candidates else None,
                "yes_ask": min(ask_candidates) if ask_candidates else None,
            }
        )
    return rungs


def _sky_cover_code(value: Any) -> float | None:
    text = str(value or "").upper()
    mapping = {"CAVOK": 0.0, "CLR": 0.0, "SKC": 0.0, "FEW": 1.0, "SCT": 2.0, "BKN": 3.0, "OVC": 4.0}
    matches = [code for token, code in mapping.items() if token in text]
    return max(matches) if matches else None


def _live_forecast_asof(
    conn: sqlite3.Connection, city: str, target_date: str, decision_ts: str
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT decision_snapshot_ts_utc, forecast_max_native,
               forecast_peak_time_local, forecast_source
        FROM fact_signal_candidates
        WHERE city=? AND event_date=?
          AND decision_snapshot_ts_utc IS NOT NULL
          AND datetime(decision_snapshot_ts_utc)<=datetime(?)
          AND forecast_max_native IS NOT NULL
        ORDER BY decision_snapshot_ts_utc DESC
        LIMIT 1
        """,
        (city, target_date, decision_ts),
    ).fetchone()
    if row is None:
        return {}
    return {
        "forecast_asof_ts_utc": row[0],
        "forecast_max_native": row[1],
        "forecast_peak_time_local": row[2],
        "forecast_source": row[3],
    }


def build_live_rows(
    raw_path: Path, city_family: dict[str, str], db_path: Path
) -> pd.DataFrame:
    raw = _read_jsonl(raw_path)
    first = [row for row in raw if row.get("event_type") is None and row.get("is_first_per_city_date")]
    settlements = {
        (str(row.get("city")), str(row.get("target_date"))): row
        for row in raw
        if row.get("event_type") == "settlement"
    }
    rows = []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    for event in first:
        city, target_date = str(event["city"]), str(event["target_date"])
        key = (city, target_date)
        bounded = "+" not in str(event.get("d1_bracket") or "")
        if not bounded or key in LIVE_INCIDENT_KEYS or key not in settlements:
            continue
        snapshot_path = Path(str(event.get("orderbook_file") or ""))
        if not snapshot_path.exists():
            continue
        rungs = _snapshot_rungs(snapshot_path, city, target_date)
        market = _market_from_rungs(rungs, str(event["current_bracket"]), str(event["d1_bracket"]))
        if market is None or not market.get("market_score_ready"):
            continue
        ordered_labels = [row["bracket"] for row in sorted(rungs, key=lambda row: _sort_key(row["bracket"]))]
        settled = _clean_label(settlements[key].get("settled_bracket"))
        current, d1 = _clean_label(event["current_bracket"]), _clean_label(event["d1_bracket"])
        if settled == current:
            label = CLASSES[0]
        elif settled == d1:
            label = CLASSES[1]
        elif settled in ordered_labels and ordered_labels.index(settled) > ordered_labels.index(d1):
            label = CLASSES[2]
        else:
            continue
        timestamp = pd.Timestamp(event["cycle_ts_utc"])
        local = timestamp.tz_convert(ZoneInfo(CITY_TIMEZONE.get(city, "UTC")))
        unit = event.get("unit")
        d1_threshold = _settlement_threshold(d1, unit)
        d2_threshold = _settlement_threshold(str(market["d2_bracket_market"]), unit)
        forecast = _live_forecast_asof(conn, city, target_date, str(event["cycle_ts_utc"]))
        forecast_max = _finite(forecast.get("forecast_max_native"))
        peak_delta = math.nan
        if forecast.get("forecast_peak_time_local"):
            peak_local = pd.Timestamp(str(forecast["forecast_peak_time_local"]))
            peak_delta = (
                local.tz_localize(None) - peak_local
            ).total_seconds() / 3600.0
        forecast_asof = pd.to_datetime(
            forecast.get("forecast_asof_ts_utc"), utc=True, errors="coerce"
        )
        forecast_age_hours = (
            (timestamp - forecast_asof).total_seconds() / 3600.0
            if not pd.isna(forecast_asof)
            else math.nan
        )
        row = {
            "city": city,
            "target_date": target_date,
            "decision_hour_local": local.hour,
            "decision_snapshot_ts_utc": event["cycle_ts_utc"],
            "label": label,
            "reach_d1": int(label != CLASSES[0]),
            "reach_d2": int(label == CLASSES[2]),
            "unit": unit,
            "running_max_native": event.get("running_max_native"),
            "city_family": city_family.get(city, "unknown"),
            "solar_window": (
                "late_morning"
                if local.hour <= 11
                else "solar_peak_window"
                if local.hour <= 14
                else "afternoon_decay_window"
                if local.hour <= 17
                else "evening_tail"
            ),
            "day_regime": "live_unknown",
            "intraday_state": "live_unknown",
            "running_max_state": "live_unknown",
            "moisture_cloud_regime": "live_unknown",
            "wind_regime": "live_unknown",
            "forecast_source": forecast.get("forecast_source") or "live_missing",
            "forecast_clock_source": (
                "fact_signal_candidates_asof"
                if forecast
                else "live_missing"
            ),
            "forecast_asof_ts_utc": forecast.get("forecast_asof_ts_utc"),
            "forecast_age_hours": forecast_age_hours,
            "forecast_gap_to_running_native": (
                math.nan
                if forecast_max is None
                else forecast_max - float(event["running_max_native"])
            ),
            "forecast_peak_delta_hours_local": peak_delta,
            "forecast_peak_hour_spread": math.nan,
            "forecast_ceiling_margin_to_d2": (
                math.nan
                if forecast_max is None or d2_threshold is None
                else forecast_max - d2_threshold
            ),
            "temp_trend_1h_f": event.get("d_tmpf_1h"),
            "temp_trend_3h_f": event.get("d_tmpf_3h"),
            "minutes_since_running_max": event.get("minutes_since_running_max"),
            "decline_native": 0.0,
            "relative_humidity_pct": event.get("relative_humidity_pct"),
            "dewpoint_depression_f": math.nan,
            "wind_speed_kt": event.get("wind_speed_kt"),
            "sky_cover_code": _sky_cover_code(event.get("sky_code_now")),
            "obs_age_min": event.get("obs_age_min"),
            "obs_count_to_decision": math.nan,
            "d1_settlement_threshold_native": d1_threshold,
            "d2_settlement_threshold_native": d2_threshold,
            "d1_required_gap_native": (
                None
                if d1_threshold is None
                else d1_threshold - float(event["running_max_native"])
            ),
            "d2_required_gap_native": (
                None
                if d2_threshold is None
                else d2_threshold - float(event["running_max_native"])
            ),
            "rungs_above_d1": market["rungs_above_d1"],
            "market_current_plus_overround": market["market_current_plus_overround"],
            "d1_yes_mid_trigger": event.get("d1_yes_mid"),
            "d1_yes_ask_exec": event.get("d1_yes_direct_ask"),
            "cost": event.get("entry_cost_with_fee"),
            "win": int(label == CLASSES[1]),
        }
        row["pnl"] = row["win"] - float(row["cost"])
        row.update(market)
        row["market_h0_logit"] = float(_logit(row["market_h0"]))
        row["market_h1_logit"] = float(_logit(row["market_h1"]))
        rows.append(row)
    conn.close()
    return pd.DataFrame(rows)


def score_live(
    historical: pd.DataFrame,
    live: pd.DataFrame,
    first_oof: pd.DataFrame,
) -> pd.DataFrame:
    if live.empty:
        return live
    outputs = []
    raw = live[KEY + ["label", "win", "cost", "pnl"]].copy()
    raw["model"] = "market_raw"
    raw["p_stall"] = live["market_p_stall"].to_numpy()
    raw["p_exact"] = live["market_p_exact"].to_numpy()
    raw["p_overshoot"] = live["market_p_overshoot"].to_numpy()
    outputs.append(raw)
    train_all = historical[historical["model_eligible"]].copy()
    train_market = train_all[train_all["market_score_ready"].eq(True)]
    for model_name, (numeric, categorical) in MODEL_SPECS.items():
        needs_market = bool(set(numeric) & set(MARKET_NUMERIC))
        train = train_market if needs_market else train_all
        estimator = SequentialHazard(numeric, categorical).fit(train)
        block = live[KEY + ["label", "win", "cost", "pnl"]].copy()
        block["model"] = model_name
        predicted = estimator.predict(live)
        for column in predicted:
            block[column] = predicted[column].to_numpy()
        outputs.append(block)
    result = pd.concat(outputs, ignore_index=True)
    result["model_edge"] = result["p_exact"] - result["cost"]
    cutoffs = (
        first_oof[first_oof["target_date"].lt(FORWARD_START)]
        .groupby("model")["p_overshoot"]
        .quantile(0.75)
        .to_dict()
    )
    result["frozen_p75_cutoff"] = result["model"].map(cutoffs)
    result["p75_filter_remove"] = result["p_overshoot"].ge(result["frozen_p75_cutoff"])
    return _row_scores(result)


def summarize_live_policy(rows: pd.DataFrame) -> pd.DataFrame:
    output = []
    for model, group in rows.groupby("model"):
        retained = group[~group["p75_filter_remove"].fillna(False)].copy()
        all_roi, _, _ = _roi_ci(group, SEED + len(output))
        retained_roi, low, high = _roi_ci(retained, SEED + 100 + len(output))
        removed = group[group["p75_filter_remove"].fillna(False)]
        output.append(
            {
                "model": model,
                "rows": len(group),
                "all_roi": all_roi,
                "removed_rows": len(removed),
                "removed_overshoots": int(removed["label"].eq(CLASSES[2]).sum()),
                "removed_stalls": int(removed["label"].eq(CLASSES[0]).sum()),
                "removed_winners": int(removed["label"].eq(CLASSES[1]).sum()),
                "retained_rows": len(retained),
                "retained_winners": int(retained["label"].eq(CLASSES[1]).sum()),
                "retained_roi": retained_roi,
                "retained_roi_delta": retained_roi - all_roi,
                "retained_roi_ci_low": low,
                "retained_roi_ci_high": high,
            }
        )
    return pd.DataFrame(output).sort_values("retained_roi", ascending=False)


def build_miss_mechanism_audit(
    states: pd.DataFrame,
    first: pd.DataFrame,
    first_scored: pd.DataFrame,
    live: pd.DataFrame,
    live_scored: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    feature_columns = [
        "forecast_gap_to_running_native",
        "forecast_peak_delta_hours_local",
        "forecast_ceiling_margin_to_d2",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "decline_native",
        "minutes_since_running_max",
        "obs_age_min",
        "intraday_state",
        "running_max_state",
        "forecast_assigned_model",
        "forecast_source",
    ]
    historical = first[KEY + ["label", "cost", "pnl"]].merge(
        states[KEY + feature_columns],
        on=KEY,
        how="left",
        validate="one_to_one",
    )
    historical["cohort"] = "historical_frozen"
    live_rows = live[
        KEY + ["label", "cost", "pnl"] + [
            column for column in feature_columns if column in live.columns
        ]
    ].copy()
    for column in feature_columns:
        if column not in live_rows:
            live_rows[column] = math.nan
    live_rows["cohort"] = "clean_live"

    for frame in [historical, live_rows]:
        frame["forecast_busted_active"] = (
            pd.to_numeric(frame["forecast_gap_to_running_native"], errors="coerce").lt(0)
            & pd.to_numeric(frame["temp_trend_3h_f"], errors="coerce").gt(0)
            & pd.to_numeric(frame["decline_native"], errors="coerce").le(0)
        )
        frame["high_clock_censored_by_obs_age"] = (
            pd.to_numeric(frame["decline_native"], errors="coerce").le(0)
            & (
                pd.to_numeric(frame["minutes_since_running_max"], errors="coerce")
                - pd.to_numeric(frame["obs_age_min"], errors="coerce")
            )
            .abs()
            .le(2)
        )
        frame["taipei_recurrent"] = frame["city"].eq("Taipei")

    def add_scores(
        frame: pd.DataFrame, predictions: pd.DataFrame
    ) -> pd.DataFrame:
        physical = predictions[predictions["model"].isin(["physics_path", "physics_only"])]
        pivot = physical.pivot_table(
            index=KEY,
            columns="model",
            values="p_overshoot",
            aggfunc="first",
        ).reset_index()
        pivot = pivot.rename(
            columns={
                "physics_path": "physics_path_p_overshoot",
                "physics_only": "physics_regime_p_overshoot",
            }
        )
        return frame.merge(pivot, on=KEY, how="left", validate="one_to_one")

    historical = add_scores(historical, first_scored)
    live_rows = add_scores(live_rows, live_scored)
    combined = pd.concat([historical, live_rows], ignore_index=True, sort=False)
    failures = combined[combined["label"].ne(CLASSES[1])].copy()

    def cohort_stats(frame: pd.DataFrame) -> dict[str, Any]:
        busted = frame[frame["forecast_busted_active"]]
        censored = frame[frame["high_clock_censored_by_obs_age"]]
        return {
            "rows": len(frame),
            "overshoots": int(frame["label"].eq(CLASSES[2]).sum()),
            "stalls": int(frame["label"].eq(CLASSES[0]).sum()),
            "forecast_busted_active_rows": len(busted),
            "forecast_busted_active_overshoots": int(
                busted["label"].eq(CLASSES[2]).sum()
            ),
            "forecast_busted_active_winners": int(
                busted["label"].eq(CLASSES[1]).sum()
            ),
            "high_clock_censored_rows": len(censored),
            "high_clock_censored_overshoots": int(
                censored["label"].eq(CLASSES[2]).sum()
            ),
        }

    taipei = historical[historical["city"].eq("Taipei")]
    other = historical[~historical["city"].eq("Taipei")]
    taipei_table = [
        [
            int(taipei["label"].eq(CLASSES[2]).sum()),
            int(taipei["label"].ne(CLASSES[2]).sum()),
        ],
        [
            int(other["label"].eq(CLASSES[2]).sum()),
            int(other["label"].ne(CLASSES[2]).sum()),
        ],
    ]
    forward_keys = first_scored.loc[
        first_scored["model"].eq("physics_path")
        & first_scored["target_date"].ge(FORWARD_START),
        KEY,
    ].drop_duplicates()
    historical_forward = historical.merge(
        forward_keys, on=KEY, how="inner", validate="one_to_one"
    )
    summary = {
        "historical_frozen": cohort_stats(historical),
        "historical_physics_oof_forward": cohort_stats(historical_forward),
        "clean_live": cohort_stats(live_rows),
        "failure_cases": {
            "rows": len(failures),
            "forecast_busted_active": int(failures["forecast_busted_active"].sum()),
            "taipei": int(failures["taipei_recurrent"].sum()),
            "union_busted_or_taipei": int(
                (
                    failures["forecast_busted_active"]
                    | failures["taipei_recurrent"]
                ).sum()
            ),
        },
        "taipei_vs_other_historical": {
            "table": taipei_table,
            "odds_ratio": float(fisher_exact(taipei_table, alternative="greater").statistic),
            "one_sided_pvalue_unadjusted": float(
                fisher_exact(taipei_table, alternative="greater").pvalue
            ),
        },
    }
    return failures, summary


def db_snapshot(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    fact = conn.execute(
        "SELECT MAX(fact_built_at_utc), COUNT(*), SUM(settlement_status<>'settled') FROM fact_trades"
    ).fetchone()
    candidates = conn.execute(
        "SELECT MAX(fact_built_at_utc), COUNT(*), MAX(decision_snapshot_ts_utc) FROM fact_signal_candidates"
    ).fetchone()
    conn.close()
    return {
        "fact_trades_built_at": fact[0],
        "fact_trades_rows": fact[1],
        "fact_trades_unsettled": fact[2],
        "fact_candidates_built_at": candidates[0],
        "fact_candidates_rows": candidates[1],
        "fact_candidates_max_decision_ts": candidates[2],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas", type=Path, default=ATLAS)
    parser.add_argument("--factory", type=Path, default=FACTORY)
    parser.add_argument("--live-raw", type=Path, default=LIVE_RAW)
    parser.add_argument("--frozen-first", type=Path, default=FROZEN_FIRST)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    args = parser.parse_args()

    states, state_audit = build_historical_states(args.atlas, args.factory)
    first = first_signal_rows(args.frozen_first)
    predictions = expanding_oof(states)
    model_summary, model_deltas = summarize_probability_models(predictions)
    first_scored, first_model_summary, first_policy = evaluate_first_signals(first, predictions)
    family = (
        states[["city", "city_family"]]
        .dropna()
        .drop_duplicates("city")
        .set_index("city")["city_family"]
        .to_dict()
    )
    live = build_live_rows(args.live_raw, family, args.db_path)
    live_scored = score_live(states, live, first_scored)
    live_policy = summarize_live_policy(live_scored)
    miss_cases, miss_summary = build_miss_mechanism_audit(
        states, first, first_scored, live, live_scored
    )

    summary = {
        "contract": {
            "model": "two-head sequential hazard",
            "h0": "P(final reaches d1)",
            "h1": "P(final reaches d2+ | final reaches d1)",
            "probabilities": CLASSES,
            "main_regularization_c": MODEL_C,
            "min_train_dates": MIN_TRAIN_DATES,
            "known_forecast_pollution_excluded": sorted(KNOWN_FORECAST_POLLUTION),
            "historical_end": HISTORICAL_END,
            "forecast_lineage": "source-file-aligned per-city fixed CITY_MODEL Single Runs",
            "peak_clock": "decision local hour minus forecast peak local hour; positive means passed",
            "celsius_settlement_threshold": "displayed bracket low minus 0.5C (half-up lattice)",
            "first_signal_trigger": "first city-day d1 YES mid >= 0.80",
            "forward_start": FORWARD_START,
            "fee": "0.05 * price * (1-price)",
        },
        "data": {
            "atlas": str(args.atlas.relative_to(ROOT)),
            "atlas_sha256": _sha256(args.atlas),
            "factory": str(args.factory.relative_to(ROOT)),
            "factory_sha256": _sha256(args.factory),
            "frozen_first": str(args.frozen_first.relative_to(ROOT)),
            "frozen_first_sha256": _sha256(args.frozen_first),
            "live_raw": str(args.live_raw.relative_to(ROOT)),
            "live_raw_sha256": _sha256(args.live_raw),
            "db": db_snapshot(args.db_path),
            **state_audit,
            "wide_dates": int(states.loc[states["model_eligible"], "target_date"].nunique()),
            "wide_cities": int(states.loc[states["model_eligible"], "city"].nunique()),
            "first_signals_all": len(first),
            "first_signal_dates": int(first["target_date"].nunique()),
            "first_signal_labels": first["label"].value_counts().to_dict(),
            "first_signals_complete_ladder": int(
                first[KEY]
                .merge(
                    states.loc[states["market_score_ready"].eq(True), KEY],
                    on=KEY,
                    how="inner",
                )
                .drop_duplicates()
                .shape[0]
            ),
            "first_signals_any_oof_rows": int(first_scored[KEY].drop_duplicates().shape[0]),
            "first_signals_any_oof_dates": int(first_scored["target_date"].nunique()),
            "first_signals_common_ladder_oof_rows": int(
                first_scored.loc[first_scored["model"].eq("market_raw"), KEY]
                .drop_duplicates()
                .shape[0]
            ),
            "first_signals_physics_oof_rows": int(
                first_scored.loc[first_scored["model"].eq("physics_only"), KEY]
                .drop_duplicates()
                .shape[0]
            ),
            "clean_live_rows": len(live),
            "clean_live_dates": int(live["target_date"].nunique()) if not live.empty else 0,
            "clean_live_labels": live["label"].value_counts().to_dict() if not live.empty else {},
            "clean_live_forecast_asof_rows": int(
                live["forecast_asof_ts_utc"].notna().sum()
            )
            if not live.empty
            else 0,
        },
        "wide_model_summary": model_summary.to_dict("records"),
        "wide_model_deltas_vs_market": model_deltas.to_dict("records"),
        "first_signal_model_summary": first_model_summary.to_dict("records"),
        "live_model_summary": (
            summarize_probability_models(live_scored)[0].to_dict("records")
            if not live_scored.empty
            else []
        ),
        "live_policy_summary": live_policy.to_dict("records"),
        "miss_mechanism_audit": miss_summary,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    states.to_csv(args.output_dir / "historical_state_rows.csv", index=False)
    first.to_csv(args.output_dir / "historical_first_signals.csv", index=False)
    predictions.to_csv(args.output_dir / "wide_oof_predictions.csv", index=False)
    model_summary.to_csv(args.output_dir / "wide_model_summary.csv", index=False)
    model_deltas.to_csv(args.output_dir / "wide_model_deltas_vs_market.csv", index=False)
    first_scored.to_csv(args.output_dir / "first_signal_oof_predictions.csv", index=False)
    first_model_summary.to_csv(args.output_dir / "first_signal_model_summary.csv", index=False)
    first_policy.to_csv(args.output_dir / "first_signal_policy_summary.csv", index=False)
    live.to_csv(args.output_dir / "clean_live_input_rows.csv", index=False)
    live_scored.to_csv(args.output_dir / "clean_live_predictions.csv", index=False)
    live_policy.to_csv(args.output_dir / "clean_live_policy_summary.csv", index=False)
    miss_cases.to_csv(args.output_dir / "failure_mechanism_cases.csv", index=False)
    (args.output_dir / "summary.json").write_text(
        json.dumps(_json_ready(summary), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(_json_ready(summary), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
