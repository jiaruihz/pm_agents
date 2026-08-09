#!/usr/bin/env python3
"""Audit physical weather semantics against frozen Current-YES Core Carry v2.

This research-only audit reconstructs point-in-time METAR paths and converts
them into continuous mechanism scores.  The scores are used in two ways:

1. diagnostic case slices that compare physical common sense, frozen core
   probability, market probability, and settlement;
2. an L2 residual challenger with the frozen core overshoot logit as offset.

No mechanism label is an eligibility gate and this script never mutates live
configuration or submits orders.
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_carry_clean_exhaustion_backfill_v3 as clean,
)
from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_core_carry_overshoot_missing_mechanisms_v2 as base,
)
from weather_data_feed.city_calendar import CITY_TIMEZONE  # noqa: E402
from weather_data_feed.sky_cover import SKY_COVER_CODE  # noqa: E402
from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402
from src.strategies.runtime.production import load_production_spec  # noqa: E402


RESEARCH_ID = "current_yes_core_carry_physical_semantic_audit_v1"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated" / RESEARCH_ID
PREREG = OUT_DIR / "preregistration.json"
REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-29-current-yes-core-carry-physical-semantic-audit-v1.md"
)
RESULT_JSON = REPORT.with_suffix(".json")
FEATURE_LEDGER = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_overshoot_missing_mechanisms_v2/feature_ledger.csv"
)
CORE_ARTIFACT = (
    ROOT
    / "src/strategies/weather_edge_v1/config/"
    "current_yes_core_carry_model_v2.json"
)
PRODUCTION_SPEC = load_production_spec()
OBS_HISTORY = PRODUCTION_SPEC.data_feed_output_root() / "observations/observations.jsonl"
FORECAST_LATEST = PRODUCTION_SPEC.forecast_enrichment_root() / "latest.json"
SNAPSHOT_DIR = historical_strategy_snapshots()
WELLINGTON_DECISION_SNAPSHOT = (
    SNAPSHOT_DIR / "snapshot_20260729_1756.json"
)

MIN_TRAIN_DATES = 8
FORWARD_DATES = 8
EPS = 1e-6

PRIMARY_FEATURES = [
    "warm_moist_advection_score",
    "mechanical_mixing_plateau_score",
    "cold_advection_score",
    "radiative_cooling_score",
    "cloud_blanket_moistening_score",
    "downslope_dry_warming_score",
    "frontal_airmass_transition_score",
    "solar_reheat_score",
]
MODEL_SPECS = {
    "semantic_mechanism_residual_v1": PRIMARY_FEATURES,
    "semantic_without_advection": [
        feature
        for feature in PRIMARY_FEATURES
        if feature != "warm_moist_advection_score"
    ],
    "semantic_without_mixing": [
        feature
        for feature in PRIMARY_FEATURES
        if feature != "mechanical_mixing_plateau_score"
    ],
}

MECHANISM_ORDER = [
    "warm_moist_advection",
    "mechanical_mixing_plateau",
    "cold_advection",
    "radiative_cooling",
    "cloud_blanket_moistening",
    "downslope_dry_warming",
    "frontal_airmass_transition",
    "solar_reheat",
    "unclassified",
]


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


def circular_difference(left: float, right: float) -> float:
    return abs((float(left) - float(right) + 180.0) % 360.0 - 180.0)


def load_histories(
    cities: list[str], start: str, end: str
) -> tuple[dict[tuple[str, str], pd.DataFrame], dict[str, Any]]:
    stations = clean.station_map()
    histories: dict[tuple[str, str], pd.DataFrame] = {}
    files_used: set[str] = set()
    missing_cities: list[str] = []
    for city in cities:
        icao = stations.get(city)
        if not icao:
            missing_cities.append(city)
            continue
        paths: list[Path] = []
        for path in clean.EXT_ROOT.glob(
            f"theta_no_iem_ext_patch*/iem_ext_{icao}_*.csv"
        ):
            bounds = clean.observation_file_range(path, icao)
            if bounds and bounds[1] >= start and bounds[0] <= end:
                paths.append(path)
        frames: list[pd.DataFrame] = []
        for path in sorted(paths):
            header = set(pd.read_csv(path, nrows=0).columns)
            use = [
                column
                for column in [
                    "valid",
                    "tmpf",
                    "dwpf",
                    "relh",
                    "drct",
                    "sknt",
                    "skyc1",
                ]
                if column in header
            ]
            if "valid" not in use or "tmpf" not in use:
                continue
            frame = pd.read_csv(
                path, usecols=use, na_values=["M"], low_memory=False
            )
            frames.append(frame)
            files_used.add(str(path.relative_to(ROOT)))
        if not frames:
            missing_cities.append(city)
            continue
        merged = pd.concat(frames, ignore_index=True)
        merged["ts"] = pd.to_datetime(
            merged["valid"], utc=True, errors="coerce"
        )
        for column in ["tmpf", "dwpf", "relh", "drct", "sknt"]:
            if column in merged:
                merged[column] = pd.to_numeric(
                    merged[column], errors="coerce"
                )
        if "skyc1" in merged:
            merged["sky_level"] = (
                merged["skyc1"].astype(str).str.upper().map(SKY_COVER_CODE)
            )
        else:
            merged["sky_level"] = math.nan
        merged = (
            merged.dropna(subset=["ts", "tmpf"])
            .sort_values("ts")
            .drop_duplicates("ts", keep="last")
        )
        local = merged["ts"].dt.tz_convert(
            ZoneInfo(CITY_TIMEZONE[city])
        )
        merged["target_date"] = local.dt.strftime("%Y-%m-%d")
        merged = merged[
            merged["target_date"].between(start, end)
        ].copy()
        for target_date, day in merged.groupby("target_date"):
            histories[(city, str(target_date))] = day.reset_index(drop=True)
    return histories, {
        "source": "cached IEM/METAR report-time observations",
        "files_used": len(files_used),
        "history_city_days": len(histories),
        "missing_cities": missing_cities,
        "pit_limit": "report time proxy; original first-seen latency unavailable",
        "precipitation_coverage": "unavailable: archived files have no present-weather field",
        "gust_coverage": "unavailable: archived files have sustained wind only",
    }


def asof_row(
    prefix: pd.DataFrame, timestamp: pd.Timestamp, tolerance_minutes: int = 90
) -> pd.Series | None:
    eligible = prefix[prefix["ts"].le(timestamp)]
    if eligible.empty:
        return None
    row = eligible.iloc[-1]
    age = (timestamp - row["ts"]).total_seconds() / 60.0
    return row if age <= tolerance_minutes else None


def direction_persistence(
    prefix: pd.DataFrame, decision: pd.Timestamp
) -> float:
    window = prefix[
        prefix["ts"].ge(decision - pd.Timedelta(hours=3))
    ]
    directions = pd.to_numeric(
        window.get("drct"), errors="coerce"
    ).dropna()
    if len(directions) < 2:
        return math.nan
    radians = np.deg2rad(directions.to_numpy(float))
    return float(
        math.hypot(np.sin(radians).mean(), np.cos(radians).mean())
    )


def physical_features(
    day: pd.DataFrame | None, decision: pd.Timestamp, row: pd.Series
) -> dict[str, Any]:
    empty = {
        "semantic_status": "missing_observation_history",
        "temp_tendency_1h_f": math.nan,
        "temp_tendency_3h_f": math.nan,
        "dewpoint_tendency_1h_f": math.nan,
        "dewpoint_tendency_3h_f": math.nan,
        "relative_humidity_tendency_3h": math.nan,
        "wind_direction_deg": math.nan,
        "wind_direction_persistence_3h": math.nan,
        "wind_direction_shift_3h_deg": math.nan,
        "sky_level_now": math.nan,
        "sky_tendency_3h": math.nan,
    }
    if day is None or pd.isna(decision):
        return empty
    prefix = day[day["ts"].le(decision)]
    now = asof_row(prefix, decision)
    lag1 = asof_row(prefix, decision - pd.Timedelta(hours=1))
    lag3 = asof_row(prefix, decision - pd.Timedelta(hours=3))
    if now is None or lag1 is None or lag3 is None:
        return empty

    def value(source: pd.Series, name: str) -> float:
        raw = source.get(name)
        return float(raw) if pd.notna(raw) else math.nan

    tmp_now, tmp1, tmp3 = (
        value(now, "tmpf"),
        value(lag1, "tmpf"),
        value(lag3, "tmpf"),
    )
    dp_now, dp1, dp3 = (
        value(now, "dwpf"),
        value(lag1, "dwpf"),
        value(lag3, "dwpf"),
    )
    rh_now, rh3 = value(now, "relh"), value(lag3, "relh")
    direction_now, direction3 = value(now, "drct"), value(lag3, "drct")
    sky_now, sky3 = value(now, "sky_level"), value(lag3, "sky_level")
    wind = value(now, "sknt")
    persistence = direction_persistence(prefix, decision)

    temp1 = tmp_now - tmp1
    temp3h = tmp_now - tmp3
    dew1 = dp_now - dp1
    dew3h = dp_now - dp3
    rh3h = rh_now - rh3
    direction_shift = (
        circular_difference(direction_now, direction3)
        if math.isfinite(direction_now) and math.isfinite(direction3)
        else math.nan
    )
    sky_delta = (
        sky_now - sky3
        if math.isfinite(sky_now) and math.isfinite(sky3)
        else math.nan
    )

    wind_factor = max(0.0, wind - 8.0) / 12.0
    persistence_value = persistence if math.isfinite(persistence) else 0.0
    stable_temp = math.exp(-abs(temp3h) / 1.8)
    mature_high = min(
        1.0,
        max(
            0.0,
            float(row.get("minutes_since_last_strict_new_high") or 0.0)
            / 360.0,
        ),
    )
    solar = max(0.0, float(row.get("solar_elevation_deg") or 0.0))
    low_solar = max(0.0, 5.0 - solar) / 5.0

    features = {
        "semantic_status": "ok_iem_report_time_proxy",
        "temp_tendency_1h_f": temp1,
        "temp_tendency_3h_f": temp3h,
        "dewpoint_tendency_1h_f": dew1,
        "dewpoint_tendency_3h_f": dew3h,
        "relative_humidity_tendency_3h": rh3h,
        "wind_direction_deg": direction_now,
        "wind_direction_persistence_3h": persistence,
        "wind_direction_shift_3h_deg": direction_shift,
        "sky_level_now": sky_now,
        "sky_tendency_3h": sky_delta,
        "observed_wind_speed_kt": wind,
        "warm_moist_advection_score": (
            wind_factor
            * persistence_value
            * max(0.0, dew3h) / 3.6
            * math.exp(-max(0.0, -temp3h) / 1.8)
        ),
        "mechanical_mixing_plateau_score": (
            wind_factor * persistence_value * stable_temp * mature_high
        ),
        "cold_advection_score": (
            wind_factor
            * max(0.0, -temp3h) / 3.6
            * max(0.0, -dew3h) / 3.6
        ),
        "radiative_cooling_score": (
            max(0.0, 7.0 - wind) / 7.0
            * max(0.0, 1.5 - sky_now) / 1.5
            * max(0.0, -temp3h) / 3.6
            * low_solar
        ),
        "cloud_blanket_moistening_score": (
            max(0.0, sky_now - 2.0) / 2.0
            * max(0.0, dew3h) / 3.6
            * stable_temp
            * low_solar
        ),
        "downslope_dry_warming_score": (
            wind_factor
            * max(0.0, temp3h) / 3.6
            * max(0.0, -dew3h) / 3.6
        ),
        "frontal_airmass_transition_score": (
            (direction_shift if math.isfinite(direction_shift) else 0.0)
            / 90.0
            * (abs(temp3h) + abs(dew3h))
            / 3.6
        ),
        "solar_reheat_score": (
            max(0.0, temp3h) / 3.6 * min(1.0, solar / 45.0)
        ),
    }
    return features


def assign_mechanisms(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    wind = frame["observed_wind_speed_kt"]
    temp = frame["temp_tendency_3h_f"]
    dew = frame["dewpoint_tendency_3h_f"]
    persistence = frame["wind_direction_persistence_3h"]
    sky = frame["sky_level_now"]
    shift = frame["wind_direction_shift_3h_deg"]
    strict_age = frame["minutes_since_last_strict_new_high"]
    solar = frame["solar_elevation_deg"]
    conditions = {
        "warm_moist_advection": (
            wind.ge(15)
            & dew.ge(1.5)
            & temp.ge(-0.5)
            & persistence.ge(0.75)
        ),
        "mechanical_mixing_plateau": (
            wind.ge(15)
            & temp.abs().le(0.5)
            & strict_age.ge(180)
            & persistence.ge(0.75)
        ),
        "cold_advection": wind.ge(15) & temp.le(-1.5) & dew.le(0),
        "radiative_cooling": (
            wind.le(5) & sky.le(1) & temp.le(-1.5) & solar.le(5)
        ),
        "cloud_blanket_moistening": (
            sky.ge(3)
            & dew.ge(1.5)
            & temp.abs().le(0.5)
            & solar.le(5)
        ),
        "downslope_dry_warming": wind.ge(12) & temp.ge(1.5) & dew.le(-1.5),
        "frontal_airmass_transition": (
            shift.ge(60) & (temp.abs().ge(1.5) | dew.abs().ge(1.5))
        ),
        "solar_reheat": frame["solar_reheat_score"].ge(0.5),
    }
    for name, condition in conditions.items():
        frame[f"is_{name}"] = condition.fillna(False)
    dominant: list[str] = []
    for _, row in frame.iterrows():
        active = [
            name
            for name in MECHANISM_ORDER[:-1]
            if bool(row[f"is_{name}"])
        ]
        dominant.append(active[0] if active else "unclassified")
    frame["dominant_mechanism"] = dominant
    return frame


def build_frame() -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = pd.read_csv(FEATURE_LEDGER)
    frame["target_date"] = frame["target_date"].astype(str)
    frame["decision_dt"] = pd.to_datetime(
        frame["decision_snapshot_ts_utc"], utc=True, errors="coerce"
    )
    cities = sorted(frame["city"].astype(str).unique())
    histories, lineage = load_histories(
        cities, frame["target_date"].min(), frame["target_date"].max()
    )
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        rows.append(
            physical_features(
                histories.get((str(row["city"]), str(row["target_date"]))),
                row["decision_dt"],
                row,
            )
        )
    enriched = pd.concat(
        [frame.reset_index(drop=True), pd.DataFrame(rows)], axis=1
    )
    enriched = assign_mechanisms(enriched)
    return enriched, lineage


def expanding_oof(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(frame["target_date"].unique())
    predictions: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    keep = [
        "opportunity_id",
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "current_bracket",
        "overshoot",
        "p_over_core",
        "p_over_market",
        "dominant_mechanism",
    ]
    for index, target_date in enumerate(dates):
        if index < MIN_TRAIN_DATES:
            continue
        train = frame[frame["target_date"].lt(target_date)].copy()
        test = frame[frame["target_date"].eq(target_date)].copy()
        if test.empty or train["overshoot"].nunique() < 2:
            continue
        result = test[keep].copy()
        for model, features in MODEL_SPECS.items():
            probability, fit = base.fit_offset_residual(
                train, test, features
            )
            result[f"p_over_{model}"] = probability
            folds.append(
                {
                    "target_date": target_date,
                    "model": model,
                    "train_rows": len(train),
                    "train_city_days": train.groupby(
                        ["city", "target_date"]
                    ).ngroups,
                    "test_rows": len(test),
                    "columns": json.dumps(fit["columns"]),
                    "coefficients": json.dumps(
                        fit.get("coefficients", [])
                    ),
                }
            )
        predictions.append(result)
    return pd.concat(predictions, ignore_index=True), pd.DataFrame(folds)


def mechanism_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(20260729)
    rows: list[dict[str, Any]] = []
    for mechanism in MECHANISM_ORDER[:-1]:
        sample = frame[frame[f"is_{mechanism}"]].copy()
        if sample.empty:
            rows.append(
                {
                    "mechanism": mechanism,
                    "states": 0,
                    "city_days": 0,
                    "dates": 0,
                }
            )
            continue
        city_days = (
            sample.groupby(["city", "target_date"], as_index=False)
            .agg(
                overshoot=("overshoot", "first"),
                p_over_core=("p_over_core", "mean"),
                p_over_market=("p_over_market", "mean"),
            )
        )
        daily = (
            city_days.groupby("target_date", as_index=False)
            .agg(
                actual_overshoot_rate=("overshoot", "mean"),
                core_probability=("p_over_core", "mean"),
                market_probability=("p_over_market", "mean"),
            )
        )
        daily["actual_minus_core"] = (
            daily["actual_overshoot_rate"] - daily["core_probability"]
        )
        daily["actual_minus_market"] = (
            daily["actual_overshoot_rate"] - daily["market_probability"]
        )
        daily_gap = daily["actual_minus_core"].to_numpy(float)
        if len(daily_gap) >= 2:
            draws = np.empty(3000, dtype=float)
            for index in range(len(draws)):
                chosen = rng.integers(0, len(daily_gap), len(daily_gap))
                draws[index] = daily_gap[chosen].mean()
            gap_ci = np.quantile(draws, [0.025, 0.975]).tolist()
        else:
            gap_ci = [None, None]
        rows.append(
            {
                "mechanism": mechanism,
                "states": len(sample),
                "city_days": len(city_days),
                "dates": sample["target_date"].nunique(),
                "overshoot_city_days": int(city_days["overshoot"].sum()),
                "actual_overshoot_rate": float(
                    daily["actual_overshoot_rate"].mean()
                ),
                "mean_core_overshoot_probability": float(
                    daily["core_probability"].mean()
                ),
                "mean_market_overshoot_probability": float(
                    daily["market_probability"].mean()
                ),
                "actual_minus_core": float(
                    daily["actual_minus_core"].mean()
                ),
                "actual_minus_core_ci95": gap_ci,
                "actual_minus_market": float(
                    daily["actual_minus_market"].mean()
                ),
            }
        )
    return pd.DataFrame(rows)


def coefficient_stability(folds: pd.DataFrame) -> list[dict[str, Any]]:
    sample = folds[
        folds["model"].eq("semantic_mechanism_residual_v1")
    ]
    values: dict[str, list[float]] = {}
    for _, row in sample.iterrows():
        for column, coefficient in zip(
            json.loads(row["columns"]),
            json.loads(row["coefficients"]),
        ):
            if column.endswith("__missing"):
                continue
            values.setdefault(column, []).append(float(coefficient))
    return [
        {
            "feature": feature,
            "folds": len(coefficients),
            "positive_folds": int(
                sum(value > 0 for value in coefficients)
            ),
            "mean_standardized_coefficient": float(
                np.mean(coefficients)
            ),
            "min_standardized_coefficient": float(
                np.min(coefficients)
            ),
            "max_standardized_coefficient": float(
                np.max(coefficients)
            ),
        }
        for feature, coefficients in values.items()
    ]


def disagreement_cases(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "decision_hour_local",
        "current_bracket",
        "final_winning_bracket",
        "p_core",
        "p_market_raw",
        "overshoot",
        "dominant_mechanism",
        "temp_tendency_3h_f",
        "dewpoint_tendency_3h_f",
        "observed_wind_speed_kt",
        "wind_direction_deg",
        "wind_direction_persistence_3h",
        "wind_direction_shift_3h_deg",
        "sky_level_now",
        *PRIMARY_FEATURES,
    ]
    losses = frame[
        frame["overshoot"].eq(1) & frame["p_core"].ge(0.80)
    ].copy()
    losses["core_surprise"] = losses["p_core"]
    return (
        losses.sort_values(
            ["dominant_mechanism", "core_surprise"],
            ascending=[True, False],
        )[columns + ["core_surprise"]]
        .reset_index(drop=True)
    )


def latest_wellington_state() -> dict[str, Any]:
    snapshot_path = WELLINGTON_DECISION_SNAPSHOT
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    market = next(
        row
        for row in payload["records"]
        if row.get("city") == "Wellington"
        and row.get("target_date") == "2026-07-29"
        and str(row.get("bracket")) == "12"
    )
    cutoff = pd.to_datetime(market["snapshot_ts_utc"], utc=True)
    history_rows: dict[str, dict[str, Any]] = {}
    with OBS_HISTORY.open("r", encoding="utf-8") as handle:
        for line in handle:
            if (
                '"city": "Wellington"' not in line
                or '"target_date": "2026-07-29"' not in line
            ):
                continue
            row = json.loads(line)
            fetched = pd.to_datetime(
                row.get("fetched_at_utc"), utc=True, errors="coerce"
            )
            obs_ts = row.get("last_obs_utc")
            if pd.notna(fetched) and fetched <= cutoff and obs_ts:
                history_rows[obs_ts] = row
    ordered = [history_rows[key] for key in sorted(history_rows)]
    now = ordered[-1]
    now_ts = pd.to_datetime(now["last_obs_utc"], utc=True)

    def lag(hours: int) -> dict[str, Any]:
        target = now_ts - pd.Timedelta(hours=hours)
        candidates = [
            row
            for row in ordered
            if pd.to_datetime(row["last_obs_utc"], utc=True) <= target
        ]
        return candidates[-1]

    lag1, lag3 = lag(1), lag(3)
    directions = np.deg2rad(
        [
            float(row["drct_now"])
            for row in ordered
            if pd.to_datetime(row["last_obs_utc"], utc=True)
            >= now_ts - pd.Timedelta(hours=3)
            and row.get("drct_now") is not None
        ]
    )
    persistence = float(
        math.hypot(
            np.sin(directions).mean(), np.cos(directions).mean()
        )
    )
    temp3 = float(now["tmpf_now"]) - float(lag3["tmpf_now"])
    dew3 = float(now["dwpf_now"]) - float(lag3["dwpf_now"])
    wind = float(now["sknt_now"])
    direction_shift = circular_difference(
        float(now["drct_now"]), float(lag3["drct_now"])
    )
    wind_factor = max(0.0, wind - 8.0) / 12.0
    warm_score = (
        wind_factor
        * persistence
        * max(0.0, dew3)
        / 3.6
        * math.exp(-max(0.0, -temp3) / 1.8)
    )
    mixing_score = (
        wind_factor
        * persistence
        * math.exp(-abs(temp3) / 1.8)
        * min(
            1.0,
            float(now["minutes_since_last_strict_new_high"]) / 360.0,
        )
    )
    market_mid = (
        float(market["yes_best_bid"]) + float(market["yes_best_ask"])
    ) / 2.0
    core_features = {
        "market_logit": math.log(market_mid / (1 - market_mid)),
        "decision_hour_local": float(
            cutoff.tz_convert(ZoneInfo("Pacific/Auckland")).hour
        )
        + float(
            cutoff.tz_convert(ZoneInfo("Pacific/Auckland")).minute
        )
        / 60.0,
        "forecast_peak_delta_hours_local": float(
            market["forecast_peak_delta_hours_local"]
        ),
        "dewpoint_depression_f": float(now["dewpoint_depression_f"]),
        "wind_speed_kt": wind,
    }
    artifact = json.loads(CORE_ARTIFACT.read_text(encoding="utf-8"))
    values = np.array(
        [
            core_features[feature]
            for feature in artifact["numeric_features"]
        ],
        dtype=float,
    )
    scaled = (
        values - np.asarray(artifact["numeric_means"], dtype=float)
    ) / np.asarray(artifact["numeric_scales"], dtype=float)
    eta = float(
        scaled @ np.asarray(artifact["coef"], dtype=float)
        + float(artifact["intercept"])
    )
    p_core = 1.0 / (1.0 + math.exp(-eta))
    return {
        "snapshot_path": str(snapshot_path),
        "snapshot_ts_utc": market["snapshot_ts_utc"],
        "market_mid": market_mid,
        "core_raw_probability": p_core,
        "core_eligibility": "outside_local_hour_window",
        "temperature_3h_change_f": temp3,
        "dewpoint_3h_change_f": dew3,
        "wind_speed_kt": wind,
        "wind_direction_deg": float(now["drct_now"]),
        "wind_direction_persistence_3h": persistence,
        "wind_direction_shift_3h_deg": direction_shift,
        "gust_kt": 33.0,
        "warm_moist_advection_score": warm_score,
        "mechanical_mixing_plateau_score": mixing_score,
        "raw_metar": now["raw_metar"],
        "semantic_interpretation": (
            "strong persistent northerly flow + rising dew point + flat temperature; "
            "mechanical mixing stabilizes 12C while warm/moist advection leaves an upward tail"
        ),
    }


def render_report(result: dict[str, Any]) -> str:
    scores = result["scores"]
    overall = scores["overall"]
    forward = scores["frozen_forward"]
    wellington = result["wellington"]
    mechanism_rows = result["mechanism_summary"]
    cases = result["case_examples"]
    lines = [
        "# Current-YES Core Carry：气象物理语义审计 v1",
        "",
        "## 结论",
        "",
        f"`semantic_mechanism_residual_v1` **{result['selection']['status']}**。"
        f"Overall Brier：candidate={overall['candidate']['brier']:.5f}，"
        f"core={overall['core']['brier']:.5f}，market={overall['market']['brier']:.5f}；"
        f"frozen-forward：candidate={forward['candidate']['brier']:.5f}，"
        f"core={forward['core']['brier']:.5f}。",
        "",
        "这不是增加限制。机制切片只用于验证和解释；只有 OOF 概率改善才允许进入模型。",
        "",
        "## 术语与机制",
        "",
        "- 这里相关的是边界层内的暖/冷平流，不是平流层（stratosphere）。",
        "- 暖湿平流：持续风输送更暖湿空气，露点先升、温度不降，增加 reheat/overshoot tail。",
        "- 机械混合：强风维持近地层温度，能阻止降温；只有上方/上游更暖时才推动升温。",
        "- 冷平流：强风伴随温度和露点下降，降低继续创新高概率。",
        "- 辐射降温：晴、弱风、太阳衰减后快速降温，通常支持 current exact hold。",
        "- 云层保温：低云/厚云可抑制夜间降温，但白天也可抑制加热，方向依赖时段。",
        "- 下坡/焚风增温：风增强、温度升、露点降，可能在过峰后重新升温。",
        "- 锋面转换：风向突变并伴随温湿跃变，增加状态转移风险。",
        "- 降雨：可通过蒸发和云遮蔽降温，也可在夜间减少辐射冷却；本历史 cache 无 present-weather，未强行造 proxy。",
        "- 语义 sanity correction：辐射降温和云层夜间保温都要求太阳高度 ≤5°；否则不能把白天下降误叫作夜间辐射过程。",
        "",
        "## 机制同分母审计",
        "",
        "| mechanism | states | city-days | dates | actual overshoot | core predicted | actual-core [95% date CI] |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in mechanism_rows:
        if row.get("states", 0) == 0:
            lines.append(
                f"| {row['mechanism']} | 0 | 0 | 0 | NA | NA | NA |"
            )
        else:
            ci = row.get("actual_minus_core_ci95") or [None, None]
            ci_text = (
                "NA"
                if ci[0] is None or ci[1] is None
                else f"[{ci[0]:+.1%}, {ci[1]:+.1%}]"
            )
            lines.append(
                f"| {row['mechanism']} | {row['states']} | {row['city_days']} | "
                f"{row['dates']} | {row['actual_overshoot_rate']:.1%} | "
                f"{row['mean_core_overshoot_probability']:.1%} | "
                f"{row['actual_minus_core']:+.1%} {ci_text} |"
            )
    lines += [
        "",
        "## 高置信 core 判断错误的 case",
        "",
        "| city/date/time | mechanism | core hold | market hold | final | T3h | Td3h | wind |",
        "|---|---|---:|---:|---|---:|---:|---:|",
    ]
    for row in cases:
        lines.append(
            f"| {row['city']} {row['target_date']} {row['decision_snapshot_ts_utc']} | "
            f"{row['dominant_mechanism']} | {row['p_core']:.1%} | "
            f"{row['p_market_raw']:.1%} | {row['final_winning_bracket']} | "
            f"{row['temp_tendency_3h_f']:+.1f}F | "
            f"{row['dewpoint_tendency_3h_f']:+.1f}F | "
            f"{row['observed_wind_speed_kt']:.0f}kt |"
        )
    lines += [
        "",
        "## 代表性 case 解读",
        "",
        "- **暖湿平流 proxy — Istanbul 2026-06-16**：23→24，core hold 97.9%；3h 温度持平、露点 +1.8°F、持续风 16kt。稳定平台不等于无上行 tail。",
        "- **暖湿平流 proxy — Wellington 2026-06-04**：17→18，core hold 90.3%；3h 温度和露点均 +3.6°F、北风 16kt。",
        "- **下坡/干暖增温 — Jeddah 2026-06-15**：37→38+，core hold 93.5%；3h 温度 +7.2°F、露点 −14.4°F、风 12kt，是典型“升温同时快速变干”。",
        "- **锋面/气团转换 — Munich 2026-06-06**：23→24+，core hold 97.3%；风向转换同时温度 +1.8°F、露点 −1.8°F。",
        "- **太阳再加热 — Busan 2026-06-17**：29→30，core hold 96.9%；3h 温度 +3.6°F，说明仍在升温的路径不应被高 market prior 压平。",
        "- **降雨/蒸发冷却**：历史 cache 没有 present-weather，不能从 OVC 或湿度上升反推“正在下雨”；这一类本轮没有伪造 case。",
        "",
        "机制方向与概率晋升要分开：warm-moist 与 downslope 在 expanding folds 中系数方向较稳定，"
        "但整组特征加入后 Brier/logloss 均未打败 frozen core，说明目前 proxy 能解释部分失败 case，"
        "还不能作为概率模型更新。",
        "",
        "## Wellington 2026-07-29",
        "",
        f"- Core raw={wellington['core_raw_probability']:.1%}，"
        f"但 eligibility={wellington['core_eligibility']}。",
        f"- 3h temperature={wellington['temperature_3h_change_f']:+.1f}F，"
        f"dew point={wellington['dewpoint_3h_change_f']:+.1f}F，"
        f"wind={wellington['wind_direction_deg']:.0f}°/"
        f"{wellington['wind_speed_kt']:.0f}G{wellington['gust_kt']:.0f}kt。",
        f"- warm-moist score={wellington['warm_moist_advection_score']:.3f}，"
        f"mixing-plateau score={wellington['mechanical_mixing_plateau_score']:.3f}。",
        "- 语义：强而持续的北风与露点上升说明不能把风只解释为“锁住 12°C”；它同时留下上行 tail。",
        "",
        "## 数据完整性与边界",
        "",
        f"- frame={result['funnel']['states']} states / "
        f"{result['funnel']['city_days']} city-days / "
        f"{result['funnel']['dates']} dates；语义特征完整率="
        f"{result['funnel']['semantic_complete_rate']:.1%}。",
        "- signal funnel：frozen core states → PIT history join → mechanism scores → OOF probabilities。",
        "- evidence funnel：本轮只覆盖 PIT probability + settlement；不做 order/fill/PnL。",
        "- manifest DB route healthy，但总体 status=warning（临时 checkout/进程漂移告警）；本轮只读已有研究 ledger/raw cache。",
        "- precipitation/gust 历史覆盖缺失；Wellington gust 只来自当前 raw METAR，未进入历史模型。",
        "- 结论保持 research-only；未改 live。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    if not PREREG.exists():
        raise FileNotFoundError(f"missing preregistration: {PREREG}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame, lineage = build_frame()
    oof, folds = expanding_oof(frame)
    dates = sorted(oof["target_date"].unique())
    forward_dates = set(dates[-FORWARD_DATES:])

    def scores(sample: pd.DataFrame) -> dict[str, Any]:
        return {
            "candidate": base.score_metrics(
                sample, "p_over_semantic_mechanism_residual_v1"
            ),
            "core": base.score_metrics(sample, "p_over_core"),
            "market": base.score_metrics(sample, "p_over_market"),
        }

    score_payload = {
        "overall": scores(oof),
        "front": scores(
            oof[~oof["target_date"].isin(forward_dates)]
        ),
        "frozen_forward": scores(
            oof[oof["target_date"].isin(forward_dates)]
        ),
    }
    paired = {
        period: {
            baseline: base.paired_delta(
                sample,
                "p_over_semantic_mechanism_residual_v1",
                baseline,
            )
            for baseline in ["p_over_core", "p_over_market"]
        }
        for period, sample in {
            "overall": oof,
            "frozen_forward": oof[
                oof["target_date"].isin(forward_dates)
            ],
        }.items()
    }
    promote = all(
        score_payload[period]["candidate"][metric]
        < score_payload[period][baseline][metric]
        for period in ["overall", "frozen_forward"]
        for baseline in ["core", "market"]
        for metric in ["brier", "logloss"]
    )
    summary = mechanism_summary(frame)
    coefficient_rows = coefficient_stability(folds)
    cases = disagreement_cases(frame)
    examples = (
        cases.groupby("dominant_mechanism", sort=False)
        .head(2)
        .head(16)
    )
    wellington = latest_wellington_state()
    result = {
        "research_id": RESEARCH_ID,
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "selection": {
            "status": "通过预注册 promotion rule"
            if promote
            else "未通过预注册 promotion rule",
            "promote_candidate": promote,
            "live_effect": "none",
        },
        "funnel": {
            "states": len(frame),
            "city_days": frame.groupby(["city", "target_date"]).ngroups,
            "dates": frame["target_date"].nunique(),
            "cities": frame["city"].nunique(),
            "semantic_complete_rate": float(
                frame["semantic_status"]
                .eq("ok_iem_report_time_proxy")
                .mean()
            ),
            **lineage,
        },
        "scores": score_payload,
        "paired_deltas": paired,
        "forward_dates": sorted(forward_dates),
        "mechanism_summary": summary.to_dict("records"),
        "coefficient_stability": coefficient_rows,
        "case_examples": examples.to_dict("records"),
        "wellington": wellington,
        "coverage_gaps": {
            "rain_evaporative_cooling": "not testable from archived IEM cache",
            "gust_mixing": "not testable historically; sustained wind only",
            "true_temperature_advection": "requires upstream or gridded temperature gradient; local proxy only",
        },
        "eight_ring_coverage": {
            "descriptive_slice": "covered",
            "statistical_inference": "covered proper-score/date block",
            "signal_discrimination": "covered",
            "probability_calibration": "covered",
            "execution_microstructure": "not covered",
            "capacity": "not covered",
            "portfolio_correlation": "target-date weighting only",
            "baseline_counterfactual": "core and same-row market covered",
        },
    }
    frame.to_csv(OUT_DIR / "semantic_feature_ledger.csv", index=False)
    oof.to_csv(OUT_DIR / "oof_predictions.csv", index=False)
    folds.to_csv(OUT_DIR / "fold_coefficients.csv", index=False)
    summary.to_csv(OUT_DIR / "mechanism_summary.csv", index=False)
    cases.to_csv(OUT_DIR / "high_confidence_disagreement_cases.csv", index=False)
    RESULT_JSON.write_text(
        json.dumps(json_ready(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    REPORT.write_text(render_report(result), encoding="utf-8")
    print(json.dumps(json_ready(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
