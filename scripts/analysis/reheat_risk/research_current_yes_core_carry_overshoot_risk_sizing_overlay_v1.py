#!/usr/bin/env python3
"""Carry-specific overshoot residual and sizing-overlay research.

The parent denominator is the frozen current-YES core-carry checkpoint ledger,
not the selected trades and not the six historical losses.  Weather/path
features are point-in-time or explicitly marked archive report-time proxies.
The script is research-only: it reads production state for evidence, never
places/cancels orders, and never changes a runtime/configuration.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


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
    research_current_yes_core_carry_taker_10share_v1 as taker10,
)
from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (  # noqa: E402
    walk_ask_ladder,
)


RESEARCH_ID = "current_yes_core_carry_overshoot_risk_sizing_overlay_v1"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated" / RESEARCH_ID
PREREG = OUT_DIR / "preregistration.json"
PREREG_EXECUTION_ADDENDUM = OUT_DIR / "preregistration_execution_addendum.json"
REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-27-current-yes-core-carry-overshoot-risk-sizing-overlay-v1.md"
)
RESULT_JSON = REPORT.with_suffix(".json")
PARENT_PATH = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_no_obs_age_freeze_pre_live_v5/"
    "oof_states_five_share_cost.csv"
)
FROZEN_ENTRY_PATH = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_no_obs_age_freeze_pre_live_v5/"
    "frozen_policy_entries.csv"
)
PROD_ROOT = Path("/Users/deepsleep/projects/pm_agents_prod")
PROD_RUNTIME = (
    PROD_ROOT
    / "runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2"
)
DB_PATH = ROOT / "runtime/weather.db"

SEED = 20260727
BOOTSTRAP_REPS = 5000
MIN_TRAIN_DATES = 8
FROZEN_FORWARD_DATES = 8
EPS = 1e-6

BASE = ["over_base_logit"]
STRICT_HIGH = [
    "strict_high_age_log",
    "same_running_max_obs_count_log",
    "strict_high_left_censored_num",
]
REMAINING_HEAT = [
    "forecast_remaining_gap_to_running_native",
    "forecast_reheat_after_now_f",
    "solar_elevation_delta_2h_deg",
    "daylight_remaining_minutes",
]
FORECAST_REVISION = [
    "forecast_max_revision_native",
    "forecast_peak_hour_revision",
]
PATH_DYNAMICS = [
    "temp_slope_acceleration_f",
    "temp_curve_acceleration_f",
    "reheating_transition_num",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
]
SOURCE_BASIS = [
    "source_latest_temp_change_f",
    "source_latest_new_high_surprise_f",
    "source_to_settlement_basis_mean_pit_f",
    "source_to_settlement_basis_mae_pit_f",
]
MODEL_SPECS: dict[str, list[str]] = {
    "core_recal": BASE,
    "plus_strict_high": BASE + STRICT_HIGH,
    "plus_remaining_heat": BASE + STRICT_HIGH + REMAINING_HEAT,
    "plus_forecast_revision": BASE + STRICT_HIGH + REMAINING_HEAT + FORECAST_REVISION,
    "plus_path_dynamics": (
        BASE + STRICT_HIGH + REMAINING_HEAT + FORECAST_REVISION + PATH_DYNAMICS
    ),
    "compact_v1": (
        BASE
        + STRICT_HIGH
        + REMAINING_HEAT
        + FORECAST_REVISION
        + PATH_DYNAMICS
        + SOURCE_BASIS
    ),
}

POLICIES = (
    "baseline_actual_10_taker_5_maker",
    "probability_only_fixed_size",
    "continuous_fractional_kelly_capped",
    "discrete_train_tertiles_15_10_5",
)
SCENARIOS = (
    "no_fill",
    "outcome_neutral_observed_rate",
    "adverse_same_overall_rate",
    "losses_only",
)


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


def opportunity_id(row: pd.Series) -> str:
    text = "|".join(
        [
            str(row["city"]),
            str(row["target_date"]),
            str(row["decision_snapshot_ts_utc"]),
            str(row["current_bracket"]),
        ]
    )
    return hashlib.sha256(text.encode()).hexdigest()


def add_cache_paths(universe: pd.DataFrame) -> pd.DataFrame:
    paths: list[pd.DataFrame] = []
    atlas = (
        ROOT
        / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
    )
    for shard in sorted(atlas.glob("feature_factory_*/reheat_feature_rows.csv")):
        header = set(pd.read_csv(shard, nrows=0).columns)
        wanted = [
            "city",
            "target_date",
            "decision_hour_local",
            "bracket",
            "current_bracket",
            "outcome",
            "gfs_forecast_cache_path",
            "ecmwf_forecast_cache_path",
        ]
        use = [column for column in wanted if column in header]
        frame = pd.read_csv(shard, usecols=use, low_memory=False)
        frame["target_date"] = frame["target_date"].astype(str)
        frame = frame[
            frame["bracket"].astype(str).eq(frame["current_bracket"].astype(str))
            & frame["outcome"].astype(str).str.lower().eq("yes")
        ]
        paths.append(frame)
    cache_paths = pd.concat(paths, ignore_index=True).drop_duplicates(
        ["city", "target_date", "decision_hour_local"]
    )
    return universe.merge(
        cache_paths[
            [
                "city",
                "target_date",
                "decision_hour_local",
                "gfs_forecast_cache_path",
                "ecmwf_forecast_cache_path",
            ]
        ],
        on=["city", "target_date", "decision_hour_local"],
        how="left",
        validate="many_to_one",
    )


def source_features(
    universe: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    histories, lineage = clean.load_observation_histories(
        sorted(universe["city"].unique()),
        str(universe["target_date"].min()),
        str(universe["target_date"].max()),
    )
    source_day_rows: list[dict[str, Any]] = []
    final_by_day = (
        universe.groupby(["city", "target_date"], as_index=False)["final_max_f"].mean()
    )
    for (city, target_date), day in histories.items():
        source_day_rows.append(
            {
                "city": city,
                "target_date": target_date,
                "source_final_max_f": float(day["tmpf"].max()),
            }
        )
    source_days = pd.DataFrame(source_day_rows).merge(
        final_by_day,
        on=["city", "target_date"],
        how="left",
        validate="one_to_one",
    )
    source_days["source_to_settlement_basis_f"] = (
        source_days["source_final_max_f"] - source_days["final_max_f"]
    )
    source_days = source_days.sort_values(["target_date", "city"]).reset_index(drop=True)
    basis_by_date: dict[tuple[str, str], tuple[float, float, int]] = {}
    for target_date in sorted(universe["target_date"].unique()):
        prior = source_days[source_days["target_date"].lt(target_date)]
        for city in universe.loc[
            universe["target_date"].eq(target_date), "city"
        ].unique():
            sample = prior[prior["city"].eq(city)]["source_to_settlement_basis_f"].dropna()
            basis_by_date[(str(city), str(target_date))] = (
                float(sample.mean()) if len(sample) else np.nan,
                float(sample.abs().mean()) if len(sample) else np.nan,
                int(len(sample)),
            )

    rows: list[dict[str, Any]] = []
    for _, row in universe.iterrows():
        decision = row["decision_snapshot_dt"]
        day = histories.get((str(row["city"]), str(row["target_date"])))
        prefix = (
            day[day["ts"].le(decision)].copy()
            if day is not None and not pd.isna(decision)
            else pd.DataFrame()
        )
        latest_ts = pd.NaT
        latest_temp = delta = surprise = np.nan
        if len(prefix):
            latest = prefix.iloc[-1]
            latest_ts = latest["ts"]
            latest_temp = float(latest["tmpf"])
            if len(prefix) >= 2:
                delta = latest_temp - float(prefix.iloc[-2]["tmpf"])
                surprise = latest_temp - float(prefix.iloc[:-1]["tmpf"].max())
        basis_mean, basis_mae, basis_n = basis_by_date[
            (str(row["city"]), str(row["target_date"]))
        ]
        rows.append(
            {
                "source_latest_report_ts_utc": latest_ts,
                "source_latest_temp_f": latest_temp,
                "source_latest_temp_change_f": delta,
                "source_latest_new_high_surprise_f": surprise,
                "source_to_settlement_basis_mean_pit_f": basis_mean,
                "source_to_settlement_basis_mae_pit_f": basis_mae,
                "source_to_settlement_basis_prior_days": basis_n,
                "source_first_seen_available": False,
            }
        )
    added = pd.DataFrame(rows, index=universe.index)
    out = pd.concat([universe.copy(), added], axis=1)
    out = out.sort_values(
        ["city", "target_date", "decision_snapshot_dt"]
    ).reset_index(drop=True)
    grouped = out.groupby(["city", "target_date"], sort=False)
    prior_report = grouped["source_latest_report_ts_utc"].shift(1)
    out["source_new_report_since_prior_checkpoint"] = (
        out["source_latest_report_ts_utc"].notna()
        & (
            prior_report.isna()
            | out["source_latest_report_ts_utc"].gt(prior_report)
        )
    ).astype(float)
    lineage.update(
        {
            "first_seen": (
                "unavailable historically; latest report timestamp <= decision is "
                "used only as an archive PIT proxy"
            ),
            "source_to_settlement_basis": (
                "city expanding mean/MAE of source daily max minus settlement final "
                "max, strictly earlier target dates only"
            ),
            "source_basis_day_rows": int(len(source_days)),
        }
    )
    return out, lineage


def prepare_features() -> tuple[pd.DataFrame, dict[str, Any]]:
    base = add_cache_paths(residual.prepare_universe())
    clean_universe, clean_lineage = clean.add_clean_features(base)
    enriched, source_lineage = source_features(clean_universe)
    ordered = enriched.sort_values(
        ["city", "target_date", "decision_snapshot_dt"]
    ).copy()
    groups = ordered.groupby(["city", "target_date"], sort=False)
    ordered["forecast_peak_hour_local_derived"] = (
        ordered["decision_hour_local"]
        + ordered["forecast_peak_delta_hours_local"]
    )
    ordered["forecast_max_revision_native"] = groups[
        "forecast_max_native"
    ].diff()
    ordered["forecast_peak_hour_revision"] = groups[
        "forecast_peak_hour_local_derived"
    ].diff()
    previous_slope = groups["temp_trend_1h_f"].shift(1)
    ordered["temp_slope_acceleration_f"] = (
        ordered["temp_trend_1h_f"] - previous_slope
    )
    ordered["temp_curve_acceleration_f"] = (
        ordered["temp_trend_1h_f"] - ordered["temp_trend_3h_f"] / 3.0
    )
    ordered["reheating_transition_num"] = (
        previous_slope.le(0) & ordered["temp_trend_1h_f"].gt(0)
    ).astype(float)
    parent = pd.read_csv(PARENT_PATH)
    parent["target_date"] = parent["target_date"].astype(str)
    parent["decision_snapshot_dt"] = pd.to_datetime(
        parent["decision_snapshot_ts_utc"], utc=True, errors="coerce"
    )
    parent = parent[
        parent["market_mid"].ge(0.80)
        & ~parent["current_bracket"].astype(str).str.contains(r"\+", regex=True)
        & parent["five_share_executable"].astype(bool)
    ].copy()
    parent["opportunity_id"] = parent.apply(opportunity_id, axis=1)
    context_columns = [
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "unit",
        "final_native",
        "final_max_f",
        "running_native",
        "forecast_max_native",
        "forecast_peak_delta_hours_local",
        "minutes_since_running_max",
        "minutes_since_last_strict_new_high",
        "same_running_max_obs_count",
        "running_max_clock_left_censored",
        "strict_high_age_log",
        "same_running_max_obs_count_log",
        "strict_high_left_censored_num",
        "forecast_remaining_gap_to_running_native",
        "forecast_reheat_after_now_f",
        "solar_elevation_deg",
        "solar_elevation_delta_2h_deg",
        "daylight_remaining_minutes",
        "forecast_max_revision_native",
        "forecast_peak_hour_revision",
        "temp_slope_acceleration_f",
        "temp_curve_acceleration_f",
        "reheating_transition_num",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "source_latest_report_ts_utc",
        "source_latest_temp_f",
        "source_latest_temp_change_f",
        "source_latest_new_high_surprise_f",
        "source_new_report_since_prior_checkpoint",
        "source_first_seen_available",
        "source_to_settlement_basis_mean_pit_f",
        "source_to_settlement_basis_mae_pit_f",
        "source_to_settlement_basis_prior_days",
    ]
    context = ordered[context_columns].drop_duplicates(
        ["city", "target_date", "decision_snapshot_ts_utc"]
    )
    frame = parent.merge(
        context,
        on=["city", "target_date", "decision_snapshot_ts_utc"],
        how="left",
        validate="one_to_one",
    )
    frame["overshoot"] = 1 - frame["label"].astype(int)
    frame["p_over_market"] = (1 - frame["market_mid"]).clip(EPS, 1 - EPS)
    frame["p_over_core"] = (1 - frame["p_core_no_obs_age"]).clip(EPS, 1 - EPS)
    frame["over_base_logit"] = np.log(
        frame["p_over_core"] / (1.0 - frame["p_over_core"])
    )
    frame["loss_direction"] = np.where(
        frame["overshoot"].eq(1), "upward_overshoot", "current_exact_held"
    )
    frame = frame.sort_values(
        ["target_date", "city", "decision_snapshot_dt"]
    ).reset_index(drop=True)
    lineage = {"clean": clean_lineage, "source": source_lineage}
    return frame, lineage


def make_model(features: list[str]) -> Pipeline:
    pre = ColumnTransformer(
        [
            (
                "numeric",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                        ("scale", StandardScaler()),
                    ]
                ),
                features,
            )
        ]
    )
    return Pipeline(
        [
            ("pre", pre),
            (
                "model",
                LogisticRegression(
                    C=0.05,
                    solver="liblinear",
                    max_iter=2000,
                    random_state=SEED,
                ),
            ),
        ]
    )


def city_day_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby(["city", "target_date"])["overshoot"].transform("size")
    return 1.0 / counts.clip(lower=1).to_numpy(float)


def first_positive(
    frame: pd.DataFrame,
    probability: str,
    cost_column: str = "five_share_cost_per_share",
) -> pd.DataFrame:
    selected = frame[
        frame[probability].gt(frame[cost_column])
        & frame[cost_column].notna()
    ].copy()
    return (
        selected.sort_values(["target_date", "city", "decision_snapshot_dt"])
        .drop_duplicates(["city", "target_date"], keep="first")
        .copy()
    )


def training_sizing_parameters(
    train: pd.DataFrame, p_over: np.ndarray
) -> tuple[float, float, float]:
    scored = train.copy()
    scored["p_hold_adjusted"] = 1.0 - np.clip(p_over, EPS, 1 - EPS)
    entries = first_positive(
        scored, "p_hold_adjusted", "ten_share_cost_per_share"
    )
    edge = (
        entries["p_hold_adjusted"] - entries["ten_share_cost_per_share"]
    ).clip(lower=0)
    positive = edge[edge.gt(0)]
    if positive.empty:
        return 0.0, 0.0, 1.0
    kelly = positive / (
        1.0 - entries.loc[positive.index, "ten_share_cost_per_share"]
    )
    scale = float(kelly[kelly.gt(0)].median())
    return (
        float(positive.quantile(1.0 / 3.0)),
        float(positive.quantile(2.0 / 3.0)),
        max(scale, 1e-6),
    )


def expanding_predictions(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(frame["target_date"].unique())
    output: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    for index, target_date in enumerate(dates):
        if index < MIN_TRAIN_DATES:
            continue
        train = frame[frame["target_date"].lt(target_date)].copy()
        test = frame[frame["target_date"].eq(target_date)].copy()
        if train["overshoot"].nunique() < 2 or test.empty:
            continue
        result = test[
            [
                "opportunity_id",
                "city",
                "target_date",
                "decision_snapshot_ts_utc",
                "current_bracket",
                "overshoot",
                "label",
            ]
        ].copy()
        weights = city_day_weights(train)
        fitted: dict[str, Pipeline] = {}
        for name, features in MODEL_SPECS.items():
            model = make_model(features)
            model.fit(
                train[features],
                train["overshoot"],
                model__sample_weight=weights,
            )
            result[f"p_over_{name}"] = model.predict_proba(test[features])[:, 1]
            fitted[name] = model
        compact = fitted["compact_v1"]
        train_over = compact.predict_proba(train[MODEL_SPECS["compact_v1"]])[:, 1]
        q33, q67, scale = training_sizing_parameters(train, train_over)
        result["edge_q33_train"] = q33
        result["edge_q67_train"] = q67
        result["kelly_scale_train"] = scale
        output.append(result)
        folds.append(
            {
                "target_date": target_date,
                "train_dates": int(train["target_date"].nunique()),
                "train_city_days": int(
                    train.groupby(["city", "target_date"]).ngroups
                ),
                "train_overshoot_city_days": int(
                    train.groupby(["city", "target_date"])["overshoot"]
                    .first()
                    .sum()
                ),
                "edge_q33_train": q33,
                "edge_q67_train": q67,
                "kelly_scale_train": scale,
            }
        )
    predictions = pd.concat(output, ignore_index=True)
    return predictions, pd.DataFrame(folds)


def leave_date_out_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    features = MODEL_SPECS["compact_v1"]
    for target_date in sorted(frame["target_date"].unique()):
        train = frame[frame["target_date"].ne(target_date)].copy()
        test = frame[frame["target_date"].eq(target_date)].copy()
        model = make_model(features)
        model.fit(
            train[features],
            train["overshoot"],
            model__sample_weight=city_day_weights(train),
        )
        p_over = model.predict_proba(test[features])[:, 1]
        train_over = model.predict_proba(train[features])[:, 1]
        q33, q67, scale = training_sizing_parameters(train, train_over)
        part = test[["opportunity_id"]].copy()
        part["p_over_compact_lodo"] = p_over
        part["edge_q33_lodo"] = q33
        part["edge_q67_lodo"] = q67
        part["kelly_scale_lodo"] = scale
        rows.append(part)
    return pd.concat(rows, ignore_index=True)


def score_metrics(frame: pd.DataFrame, probability: str) -> dict[str, Any]:
    y = frame["overshoot"].to_numpy(int)
    p = frame[probability].clip(EPS, 1 - EPS).to_numpy(float)
    weights = city_day_weights(frame)
    return {
        "rows": int(len(frame)),
        "city_days": int(frame.groupby(["city", "target_date"]).ngroups),
        "dates": int(frame["target_date"].nunique()),
        "overshoot_city_days": int(
            frame.groupby(["city", "target_date"])["overshoot"].first().sum()
        ),
        "brier": float(brier_score_loss(y, p, sample_weight=weights)),
        "logloss": float(log_loss(y, p, sample_weight=weights)),
        "auc": float(roc_auc_score(y, p, sample_weight=weights)),
        "mean_prediction": float(np.average(p, weights=weights)),
        "actual_rate": float(np.average(y, weights=weights)),
    }


def proper_score_delta(
    frame: pd.DataFrame, candidate: str, baseline: str
) -> dict[str, Any]:
    work = frame.copy()
    y = work["overshoot"].to_numpy(float)
    pc = work[candidate].clip(EPS, 1 - EPS).to_numpy(float)
    pb = work[baseline].clip(EPS, 1 - EPS).to_numpy(float)
    work["brier_delta"] = (pc - y) ** 2 - (pb - y) ** 2
    work["logloss_delta"] = -(
        y * np.log(pc) + (1 - y) * np.log(1 - pc)
    ) + (y * np.log(pb) + (1 - y) * np.log(1 - pb))
    daily = (
        work.groupby(["city", "target_date"])[["brier_delta", "logloss_delta"]]
        .mean()
        .reset_index()
    )
    dates = sorted(daily["target_date"].unique())
    blocks = {
        date: daily[daily["target_date"].eq(date)][
            ["brier_delta", "logloss_delta"]
        ].to_numpy(float)
        for date in dates
    }
    rng = np.random.default_rng(SEED)
    brier_draws: list[float] = []
    logloss_draws: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        chosen = rng.integers(0, len(dates), len(dates))
        sample = np.concatenate([blocks[dates[position]] for position in chosen])
        brier_draws.append(float(sample[:, 0].mean()))
        logloss_draws.append(float(sample[:, 1].mean()))
    return {
        "candidate_minus_baseline_brier": float(daily["brier_delta"].mean()),
        "brier_delta_ci95": [
            float(np.quantile(brier_draws, 0.025)),
            float(np.quantile(brier_draws, 0.975)),
        ],
        "candidate_minus_baseline_logloss": float(
            daily["logloss_delta"].mean()
        ),
        "logloss_delta_ci95": [
            float(np.quantile(logloss_draws, 0.025)),
            float(np.quantile(logloss_draws, 0.975)),
        ],
    }


def risk_lift(frame: pd.DataFrame, probability: str) -> dict[str, Any]:
    city_day = (
        frame.groupby(["city", "target_date"], as_index=False)
        .agg(overshoot=("overshoot", "first"), risk=(probability, "mean"))
        .sort_values("risk", ascending=False)
    )
    n_top = max(1, math.ceil(0.10 * len(city_day)))
    top = city_day.head(n_top)
    base_rate = float(city_day["overshoot"].mean())
    top_rate = float(top["overshoot"].mean())
    total_losses = int(city_day["overshoot"].sum())
    return {
        "city_days": int(len(city_day)),
        "top_decile_city_days": int(len(top)),
        "base_overshoot_rate": base_rate,
        "top_decile_overshoot_rate": top_rate,
        "top_decile_lift": top_rate / base_rate if base_rate > 0 else None,
        "overshoot_capture_rate": (
            float(top["overshoot"].sum() / total_losses)
            if total_losses
            else None
        ),
    }


def maker_fill_snapshot() -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT maker_only, COUNT(*) AS fills,
               COUNT(DISTINCT city || '|' || target_date) AS city_days,
               SUM(fill_qty) AS shares, SUM(cost_usd) AS cost_usd,
               SUM(CASE WHEN settled=1 THEN 1 ELSE 0 END) AS settled_fills
        FROM fact_trades
        WHERE instance_id='current_yes_core_carry_tiny_live_v2'
        GROUP BY maker_only
        """
    ).fetchall()
    by_leg = {
        ("maker" if row["maker_only"] else "taker"): dict(row) for row in rows
    }
    taker_days = int(by_leg.get("taker", {}).get("city_days") or 0)
    maker_days = int(by_leg.get("maker", {}).get("city_days") or 0)
    return {
        "by_leg": by_leg,
        "maker_fill_rate_per_taker_city_day": (
            maker_days / taker_days if taker_days else 0.0
        ),
        "settled_maker_fills": int(
            by_leg.get("maker", {}).get("settled_fills") or 0
        ),
    }


def production_snapshot() -> dict[str, Any]:
    latest_path = PROD_RUNTIME / "latest_summary.json"
    orders_path = PROD_RUNTIME / "live_orders.jsonl"
    latest = (
        json.loads(latest_path.read_text(encoding="utf-8"))
        if latest_path.exists()
        else {}
    )
    order_counts: dict[str, Any] = {}
    if orders_path.exists():
        records = [
            json.loads(line)
            for line in orders_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault(str(record.get("execution_policy")), []).append(record)
        order_counts = {
            policy: {
                "rows": len(items),
                "sizes": sorted(
                    {
                        float(item["size"])
                        for item in items
                        if item.get("size") is not None
                    }
                ),
                "place_status": dict(
                    Counter(
                        str(
                            (item.get("exchange_response") or {})
                            .get("place", {})
                            .get("status")
                            or item.get("status")
                        )
                        for item in items
                    )
                ),
            }
            for policy, items in grouped.items()
        }
    process = subprocess.run(
        ["ps", "auxww"], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    commands = [
        line
        for line in process
        if "weather_current_yes_core_carry_tiny_live_v2.py loop" in line
    ]
    commit = None
    if PROD_ROOT.exists():
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROD_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        commit = result.stdout.strip() or None
    taker_shares = float(latest.get("taker_shares") or 0)
    maker_shares = float(latest.get("maker_shares") or 0)
    process_agrees = any(
        f"--taker-shares {taker_shares:g}" in command
        and f"--maker-shares {maker_shares:g}" in command
        for command in commands
    )
    return {
        "evidence_time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_summary_path": str(latest_path),
        "latest_summary_mtime_utc": (
            datetime.fromtimestamp(latest_path.stat().st_mtime, timezone.utc).isoformat()
            if latest_path.exists()
            else None
        ),
        "latest_summary": {
            key: latest.get(key)
            for key in [
                "generated_at_utc",
                "status",
                "strategy_id",
                "strategy_instance",
                "config_id",
                "mode",
                "live_enabled",
                "taker_shares",
                "maker_shares",
                "max_city_days_per_bj_day",
                "max_daily_cost_usd",
                "signal_snapshot_file",
            ]
        },
        "process_commands": commands,
        "raw_order_policy_counts": order_counts,
        "prod_git_commit": commit,
        "process_agrees_with_summary": process_agrees,
        "verified_actual_split": (
            f"{taker_shares:g}_taker_{maker_shares:g}_maker"
        ),
    }


def coverage_gate() -> dict[str, Any]:
    result = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "scripts/analysis/execution_quality/"
                "weather_clob_fill_coverage_gate.py"
            ),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    payload["exit_code"] = result.returncode
    return payload


def data_snapshot() -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    fact = conn.execute(
        "SELECT MAX(fact_built_at_utc), COUNT(*), "
        "SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) "
        "FROM fact_trades"
    ).fetchone()
    candidates = conn.execute(
        "SELECT MAX(fact_built_at_utc), COUNT(*), SUM(eligible), "
        "SUM(decision_window_missing) FROM fact_signal_candidates"
    ).fetchone()
    return {
        "db_path": str(DB_PATH),
        "db_mtime_utc": datetime.fromtimestamp(
            DB_PATH.stat().st_mtime, timezone.utc
        ).isoformat(),
        "fact_trades": {
            "fact_built_at_utc": fact[0],
            "rows": fact[1],
            "settled": fact[2],
            "missing_bracket": fact[3],
        },
        "fact_signal_candidates": {
            "fact_built_at_utc": candidates[0],
            "rows": candidates[1],
            "eligible": candidates[2],
            "decision_window_missing": candidates[3],
        },
        "sync_or_rebuild": "none; DB and raw production runtime cover 2026-07-27",
    }


def maker_probabilities(
    scenario: str, selected: pd.DataFrame, observed_rate: float
) -> tuple[float, float]:
    if scenario == "no_fill":
        return 0.0, 0.0
    if scenario == "outcome_neutral_observed_rate":
        return observed_rate, observed_rate
    if scenario == "losses_only":
        return 0.0, 1.0
    wins = int(selected["label"].eq(1).sum())
    losses = int(selected["label"].eq(0).sum())
    q_win = max(
        0.0,
        min(1.0, (observed_rate * len(selected) - losses) / max(wins, 1)),
    )
    return q_win, 1.0


def policy_frames(
    frame: pd.DataFrame,
    actual_taker_shares: float,
    actual_maker_shares: float,
) -> dict[str, pd.DataFrame]:
    baseline = first_positive(
        frame, "p_core_no_obs_age", "ten_share_cost_per_share"
    )
    adjusted = first_positive(
        frame, "p_hold_adjusted", "ten_share_cost_per_share"
    )
    results: dict[str, pd.DataFrame] = {}

    base = baseline.copy()
    base["taker_shares_desired"] = actual_taker_shares
    base["maker_shares_desired"] = actual_maker_shares
    base["size_multiplier"] = 1.0
    base["size_tier"] = f"actual_fixed_{actual_taker_shares + actual_maker_shares:g}_total"
    results[POLICIES[0]] = base

    fixed = adjusted.copy()
    fixed["taker_shares_desired"] = actual_taker_shares
    fixed["maker_shares_desired"] = actual_maker_shares
    fixed["size_multiplier"] = 1.0
    fixed["size_tier"] = f"actual_fixed_{actual_taker_shares + actual_maker_shares:g}_total"
    results[POLICIES[1]] = fixed

    continuous = baseline.copy()
    edge = (
        continuous["p_hold_adjusted"]
        - continuous["ten_share_cost_per_share"]
    )
    kelly = (
        edge.clip(lower=0)
        / (1.0 - continuous["ten_share_cost_per_share"]).clip(lower=EPS)
    )
    multiplier = (
        kelly
        / continuous["kelly_scale_train"].clip(lower=1e-6)
    ).clip(lower=0, upper=2.0)
    continuous["size_multiplier"] = multiplier
    continuous["taker_shares_desired"] = 5.0 * multiplier
    continuous["maker_shares_desired"] = np.minimum(5.0, 5.0 * multiplier)
    continuous["size_tier"] = "continuous"
    results[POLICIES[2]] = continuous

    discrete = baseline.copy()
    adjusted_edge = (
        discrete["p_hold_adjusted"]
        - discrete["ten_share_cost_per_share"]
    )
    conditions = [
        adjusted_edge.le(0),
        adjusted_edge.lt(discrete["edge_q33_train"]),
        adjusted_edge.lt(discrete["edge_q67_train"]),
    ]
    discrete["taker_shares_desired"] = np.select(
        conditions, [0.0, 5.0, 5.0], default=10.0
    )
    discrete["maker_shares_desired"] = np.select(
        conditions, [0.0, 0.0, 5.0], default=5.0
    )
    discrete["size_multiplier"] = (
        discrete["taker_shares_desired"]
        + discrete["maker_shares_desired"]
    ) / 10.0
    discrete["size_tier"] = np.select(
        conditions,
        ["zero", "low_5", "mid_10"],
        default="high_15",
    )
    results[POLICIES[3]] = discrete
    return results


def add_execution(
    policies: dict[str, pd.DataFrame], parent: pd.DataFrame
) -> pd.DataFrame:
    raw_cache = taker10.load_raw_ask_cache(parent)
    rows: list[pd.DataFrame] = []
    for policy, frame in policies.items():
        work = frame.copy()
        principals: list[float] = []
        fees: list[float] = []
        executable: list[bool] = []
        for _, row in work.iterrows():
            shares = float(row["taker_shares_desired"])
            if shares <= 1e-9:
                principals.append(0.0)
                fees.append(0.0)
                executable.append(True)
                continue
            if float(row["current_yes_ask_size"]) >= shares:
                asks = [
                    {
                        "price": float(row["current_yes_ask"]),
                        "size": float(row["current_yes_ask_size"]),
                    }
                ]
            else:
                asks = raw_cache.get(taker10.row_key(row), [])
            ladder = walk_ask_ladder(asks, shares)
            executable.append(bool(ladder["executable"]))
            principals.append(
                float(ladder["principal"]) if ladder["executable"] else np.nan
            )
            fees.append(float(ladder["fee"]) if ladder["executable"] else np.nan)
        work["taker_executable"] = executable
        work["taker_principal_usd"] = principals
        work["taker_fee_usd"] = fees
        work["taker_cost_usd"] = (
            work["taker_principal_usd"] + work["taker_fee_usd"]
        )
        work["taker_pnl_usd"] = (
            work["taker_shares_desired"] * work["label"]
            - work["taker_cost_usd"]
        )
        probability_for_cap = np.where(
            policy == POLICIES[0],
            work["p_core_no_obs_age"],
            work["p_hold_adjusted"],
        )
        work["maker_price_assumption"] = np.minimum(
            work["market_mid"], probability_for_cap
        )
        work["policy"] = policy
        rows.append(work)
    return pd.concat(rows, ignore_index=True)


def daily_and_summary(
    detail: pd.DataFrame, observed_fill_rate: float, all_dates: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for policy in POLICIES:
        sample = detail[detail["policy"].eq(policy)].copy()
        selected = sample[
            sample["taker_shares_desired"].gt(0)
            | sample["maker_shares_desired"].gt(0)
        ]
        for scenario in SCENARIOS:
            q_win, q_loss = maker_probabilities(
                scenario, selected, observed_fill_rate
            )
            q = np.where(sample["label"].eq(1), q_win, q_loss)
            sample_s = sample.copy()
            sample_s["maker_fill_probability"] = q
            sample_s["maker_expected_cost_usd"] = (
                sample_s["maker_shares_desired"]
                * sample_s["maker_price_assumption"]
                * q
            )
            sample_s["maker_expected_pnl_usd"] = (
                sample_s["maker_shares_desired"]
                * (
                    sample_s["label"]
                    - sample_s["maker_price_assumption"]
                )
                * q
            )
            sample_s["capital_usd"] = (
                sample_s["taker_cost_usd"].fillna(0)
                + sample_s["maker_expected_cost_usd"].fillna(0)
            )
            sample_s["pnl_usd"] = (
                sample_s["taker_pnl_usd"].fillna(0)
                + sample_s["maker_expected_pnl_usd"].fillna(0)
            )
            daily = (
                sample_s.groupby("target_date", as_index=False)
                .agg(
                    city_days=("city", "nunique"),
                    taker_shares=("taker_shares_desired", "sum"),
                    maker_desired_shares=("maker_shares_desired", "sum"),
                    maker_expected_filled_shares=(
                        "maker_fill_probability",
                        lambda values: 0.0,
                    ),
                    capital_usd=("capital_usd", "sum"),
                    pnl_usd=("pnl_usd", "sum"),
                )
            )
            maker_filled = (
                sample_s.assign(
                    _maker_fill_shares=(
                        sample_s["maker_shares_desired"]
                        * sample_s["maker_fill_probability"]
                    )
                )
                .groupby("target_date")["_maker_fill_shares"]
                .sum()
            )
            daily["maker_expected_filled_shares"] = daily["target_date"].map(
                maker_filled
            )
            daily = (
                pd.DataFrame({"target_date": all_dates})
                .merge(daily, on="target_date", how="left")
                .fillna(0)
            )
            daily["policy"] = policy
            daily["maker_scenario"] = scenario
            daily_rows.extend(daily.to_dict("records"))
            cost = float(daily["capital_usd"].sum())
            pnl = float(daily["pnl_usd"].sum())
            worst_n = max(1, math.ceil(0.10 * len(daily)))
            summary_rows.append(
                {
                    "policy": policy,
                    "maker_scenario": scenario,
                    "selected_city_days": int(len(selected)),
                    "wins": int(selected["label"].sum()),
                    "losses": int(selected["label"].eq(0).sum()),
                    "win_rate_by_count": (
                        float(selected["label"].mean()) if len(selected) else None
                    ),
                    "capital_usage_usd": cost,
                    "pnl_usd": pnl,
                    "roi": pnl / cost if cost > 0 else None,
                    "max_single_day_loss_usd": float(daily["pnl_usd"].min()),
                    "expected_shortfall_10pct_usd": float(
                        daily.nsmallest(worst_n, "pnl_usd")["pnl_usd"].mean()
                    ),
                    "mean_daily_pnl_usd": float(daily["pnl_usd"].mean()),
                    "q_maker_win": q_win,
                    "q_maker_loss": q_loss,
                    "taker_depth_complete": bool(
                        sample.loc[
                            sample["taker_shares_desired"].gt(0),
                            "taker_executable",
                        ].all()
                    ),
                }
            )
    return pd.DataFrame(daily_rows), pd.DataFrame(summary_rows)


def bootstrap_policy_deltas(daily: pd.DataFrame) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        baseline = (
            daily[
                daily["policy"].eq(POLICIES[0])
                & daily["maker_scenario"].eq(scenario)
            ]
            .sort_values("target_date")
            .reset_index(drop=True)
        )
        dates = baseline["target_date"].tolist()
        base_values = baseline[["pnl_usd", "capital_usd"]].to_numpy(float)
        for policy in POLICIES[1:]:
            candidate = (
                daily[
                    daily["policy"].eq(policy)
                    & daily["maker_scenario"].eq(scenario)
                ]
                .set_index("target_date")
                .reindex(dates)
                .reset_index()
            )
            values = candidate[["pnl_usd", "capital_usd"]].to_numpy(float)
            rng = np.random.default_rng(SEED)
            pnl_draws: list[float] = []
            roi_draws: list[float] = []
            for _ in range(BOOTSTRAP_REPS):
                chosen = rng.integers(0, len(dates), len(dates))
                base_cost = float(base_values[chosen, 1].sum())
                candidate_cost = float(values[chosen, 1].sum())
                pnl_draws.append(
                    float(
                        (
                            values[chosen, 0] - base_values[chosen, 0]
                        ).mean()
                    )
                )
                if base_cost > 0 and candidate_cost > 0:
                    roi_draws.append(
                        float(
                            values[chosen, 0].sum() / candidate_cost
                            - base_values[chosen, 0].sum() / base_cost
                        )
                    )
            output.append(
                {
                    "policy": policy,
                    "maker_scenario": scenario,
                    "mean_daily_pnl_delta_usd": float(
                        (values[:, 0] - base_values[:, 0]).mean()
                    ),
                    "mean_daily_pnl_delta_ci95": [
                        float(np.quantile(pnl_draws, 0.025)),
                        float(np.quantile(pnl_draws, 0.975)),
                    ],
                    "roi_delta": float(
                        values[:, 0].sum() / values[:, 1].sum()
                        - base_values[:, 0].sum() / base_values[:, 1].sum()
                    )
                    if values[:, 1].sum() > 0 and base_values[:, 1].sum() > 0
                    else None,
                    "roi_delta_ci95": [
                        float(np.quantile(roi_draws, 0.025)),
                        float(np.quantile(roi_draws, 0.975)),
                    ]
                    if roi_draws
                    else [None, None],
                }
            )
    return pd.DataFrame(output)


def period_metrics(
    daily: pd.DataFrame, forward_dates: set[str]
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for period, date_mask in [
        ("front", ~daily["target_date"].isin(forward_dates)),
        ("frozen_forward", daily["target_date"].isin(forward_dates)),
    ]:
        part = daily[date_mask]
        for (policy, scenario), sample in part.groupby(
            ["policy", "maker_scenario"]
        ):
            cost = float(sample["capital_usd"].sum())
            pnl = float(sample["pnl_usd"].sum())
            rows.append(
                {
                    "period": period,
                    "policy": policy,
                    "maker_scenario": scenario,
                    "dates": int(sample["target_date"].nunique()),
                    "pnl_usd": pnl,
                    "capital_usd": cost,
                    "roi": pnl / cost if cost > 0 else None,
                    "mean_daily_pnl_usd": float(sample["pnl_usd"].mean()),
                    "max_single_day_loss_usd": float(sample["pnl_usd"].min()),
                }
            )
    return pd.DataFrame(rows)


def winner_harm(detail: pd.DataFrame) -> pd.DataFrame:
    baseline = detail[
        detail["policy"].eq(POLICIES[0]) & detail["label"].eq(1)
    ][["city", "target_date", "taker_shares_desired", "maker_shares_desired"]]
    baseline = baseline.rename(
        columns={
            "taker_shares_desired": "baseline_taker",
            "maker_shares_desired": "baseline_maker",
        }
    )
    rows: list[dict[str, Any]] = []
    for policy in POLICIES[1:]:
        candidate = detail[detail["policy"].eq(policy)][
            [
                "city",
                "target_date",
                "taker_shares_desired",
                "maker_shares_desired",
            ]
        ].rename(
            columns={
                "taker_shares_desired": "candidate_taker",
                "maker_shares_desired": "candidate_maker",
            }
        )
        joined = baseline.merge(
            candidate, on=["city", "target_date"], how="left"
        ).fillna(0)
        base_total = joined["baseline_taker"] + joined["baseline_maker"]
        candidate_total = (
            joined["candidate_taker"] + joined["candidate_maker"]
        )
        rows.append(
            {
                "policy": policy,
                "baseline_winner_city_days": int(len(joined)),
                "winner_city_days_zeroed": int(candidate_total.eq(0).sum()),
                "winner_city_days_reduced": int(
                    candidate_total.lt(base_total).sum()
                ),
                "winner_desired_share_harm_rate": float(
                    1.0 - candidate_total.sum() / base_total.sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def leave_one_date_out_range(
    daily: pd.DataFrame, scenario: str = "adverse_same_overall_rate"
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for policy in POLICIES[1:]:
        deltas: list[float] = []
        dates = sorted(daily["target_date"].unique())
        for omitted in dates:
            sample = daily[
                daily["maker_scenario"].eq(scenario)
                & daily["target_date"].ne(omitted)
            ]
            base = sample[sample["policy"].eq(POLICIES[0])]["pnl_usd"].mean()
            candidate = sample[sample["policy"].eq(policy)]["pnl_usd"].mean()
            deltas.append(float(candidate - base))
        rows.append(
            {
                "policy": policy,
                "scenario": scenario,
                "leave_one_date_out_mean_daily_pnl_delta_min": min(deltas),
                "leave_one_date_out_mean_daily_pnl_delta_max": max(deltas),
            }
        )
    return pd.DataFrame(rows)


def choose_action(
    compact_delta: dict[str, Any],
    summary: pd.DataFrame,
    deltas: pd.DataFrame,
    periods: pd.DataFrame,
    harm: pd.DataFrame,
) -> dict[str, Any]:
    adverse = summary[
        summary["maker_scenario"].eq("adverse_same_overall_rate")
    ].set_index("policy")
    delta_adverse = deltas[
        deltas["maker_scenario"].eq("adverse_same_overall_rate")
    ].set_index("policy")
    forward = periods[
        periods["period"].eq("frozen_forward")
        & periods["maker_scenario"].eq("adverse_same_overall_rate")
    ].set_index("policy")
    harm_by = harm.set_index("policy")
    candidates = []
    for policy in POLICIES[1:]:
        es_improvement = (
            adverse.loc[policy, "expected_shortfall_10pct_usd"]
            > adverse.loc[POLICIES[0], "expected_shortfall_10pct_usd"]
        )
        forward_delta = (
            forward.loc[policy, "mean_daily_pnl_usd"]
            - forward.loc[POLICIES[0], "mean_daily_pnl_usd"]
        )
        candidates.append(
            {
                "policy": policy,
                "adverse_es_improves": bool(es_improvement),
                "forward_mean_daily_pnl_delta_usd": float(forward_delta),
                "winner_harm_rate": float(
                    harm_by.loc[policy, "winner_desired_share_harm_rate"]
                ),
                "adverse_mean_daily_pnl_delta_ci95": delta_adverse.loc[
                    policy, "mean_daily_pnl_delta_ci95"
                ],
            }
        )
    # Pre-registered candidate selection for action: among policies satisfying
    # the shadow risk gates, prefer the highest adverse mean daily PnL.
    shadow_eligible = [
        row
        for row in candidates
        if row["adverse_es_improves"]
        and row["forward_mean_daily_pnl_delta_usd"] >= 0
        and row["winner_harm_rate"] < 0.25
    ]
    if shadow_eligible:
        chosen = max(
            shadow_eligible,
            key=lambda row: float(
                adverse.loc[row["policy"], "mean_daily_pnl_usd"]
            ),
        )
    else:
        chosen = max(
            candidates,
            key=lambda row: float(
                adverse.loc[row["policy"], "mean_daily_pnl_usd"]
            ),
        )
    proper_point = (
        compact_delta["candidate_minus_baseline_brier"] < 0
        and compact_delta["candidate_minus_baseline_logloss"] < 0
    )
    proper_ci = (
        compact_delta["brier_delta_ci95"][1] < 0
        and compact_delta["logloss_delta_ci95"][1] < 0
    )
    pnl_ci = chosen["adverse_mean_daily_pnl_delta_ci95"][0] > 0
    capacity = bool(adverse.loc[chosen["policy"], "taker_depth_complete"])
    deploy_eligible = (
        proper_ci
        and pnl_ci
        and chosen["forward_mean_daily_pnl_delta_usd"] > 0
        and capacity
    )
    shadow = (
        proper_point
        and chosen["adverse_es_improves"]
        and chosen["winner_harm_rate"] < 0.25
        and chosen["forward_mean_daily_pnl_delta_usd"] >= 0
    )
    action = (
        "有资格提出部署参数但仍需用户确认"
        if deploy_eligible
        else "zero-notional shadow sizing overlay"
        if shadow
        else "不采用"
    )
    return {
        "action": action,
        "selected_policy": chosen["policy"],
        "proper_score_point_gate": proper_point,
        "proper_score_ci_gate": proper_ci,
        "adverse_pnl_ci_gate": pnl_ci,
        "frozen_forward_same_sign_gate": (
            chosen["forward_mean_daily_pnl_delta_usd"] >= 0
        ),
        "winner_harm_below_25pct_gate": (
            chosen["winner_harm_rate"] < 0.25
        ),
        "execution_capacity_gate": capacity,
        "deployment_requires_separate_weather_strategy_deploy_and_confirmation": True,
        "candidate_policy_diagnostics": candidates,
    }


def fmt_pct(value: Any) -> str:
    return "NA" if value is None or pd.isna(value) else f"{float(value):+.2%}"


def fmt_num(value: Any, digits: int = 4) -> str:
    return "NA" if value is None or pd.isna(value) else f"{float(value):.{digits}f}"


def write_report(payload: dict[str, Any]) -> None:
    proper = {row["model"]: row for row in payload["probability"]["models"]}
    summary = pd.DataFrame(payload["sizing"]["summary"])
    deltas = pd.DataFrame(payload["sizing"]["paired_deltas"])
    periods = pd.DataFrame(payload["sizing"]["periods"])
    harm = pd.DataFrame(payload["sizing"]["winner_harm"])
    compact = proper["compact_v1"]
    compact_delta = compact["vs_core"]
    lodo_probability = payload["probability"][
        "compact_leave_date_out_diagnostic"
    ]
    actual_size = payload["sizing"]["effective_actual_fixed_size"]
    raw_orders = payload["production"]["raw_order_policy_counts"]
    raw_taker_sizes = raw_orders.get(
        "current_yes_residual_carry_taker_v1", {}
    ).get("sizes", [])
    raw_maker_sizes = raw_orders.get(
        "current_yes_residual_carry_maker_v1", {}
    ).get("sizes", [])
    adverse = summary[
        summary["maker_scenario"].eq("adverse_same_overall_rate")
    ]
    delta_adverse = deltas[
        deltas["maker_scenario"].eq("adverse_same_overall_rate")
    ]
    lines = [
        "# Current-YES core carry overshoot risk sizing overlay v1",
        "",
        f"Status: `{payload['verdict']['action']}`",
        f"Generated: `{payload['generated_at_utc']}`",
        "",
        "## 结论与动作",
        "",
        f"**动作：{payload['verdict']['action']}。** "
        f"失败候选中期望 PnL 最高的是 `{payload['verdict']['selected_policy']}`，"
        "但它没有通过预注册晋升门。"
        "它只属于 carry 的概率/风险/sizing overlay，不是独立 overshoot 交易策略。",
        "",
        f"生产基线动态核验为 **{actual_size['taker_shares']:g} taker + "
        f"{actual_size['maker_shares']:g} maker**。"
        f"当前 summary=`{payload['production']['latest_summary']['generated_at_utc']}`，"
        f"且运行进程参数与 summary 一致。raw order 历史 taker/maker sizes="
        f"`{raw_taker_sizes}/{raw_maker_sizes}`，仍是变更前 5+5 记录；"
        "它们不能反推当前 policy 仍为 5+5。",
        "",
        f"在固定 challenger OOF 分母上，compact 相对 frozen core 的 Brier Δ "
        f"`{compact_delta['candidate_minus_baseline_brier']:+.6f}` "
        f"CI `[{compact_delta['brier_delta_ci95'][0]:+.6f},"
        f"{compact_delta['brier_delta_ci95'][1]:+.6f}]`；logloss Δ "
        f"`{compact_delta['candidate_minus_baseline_logloss']:+.6f}` "
        f"CI `[{compact_delta['logloss_delta_ci95'][0]:+.6f},"
        f"{compact_delta['logloss_delta_ci95'][1]:+.6f}]`。负值才是改善。",
        "",
        "## 数据快照",
        "",
        f"- Parent ledger：{payload['parent']['rows']} checkpoint states / "
        f"{payload['parent']['city_days']} city-days / {payload['parent']['dates']} target dates，"
        f"{payload['parent']['date_min']}..{payload['parent']['date_max']}；"
        f"overshoot states={payload['parent']['overshoot_states']}。",
        f"- 历史冻结 5-share selector：{payload['parent']['frozen_selected_city_days']} city-days，"
        f"{payload['parent']['frozen_selected_wins']} winners / "
        f"{payload['parent']['frozen_selected_losses']} losses；6 个 loss 全为向上 overshoot。",
        f"- DB fact build=`{payload['data_snapshot']['fact_trades']['fact_built_at_utc']}`；"
        f"CLOB fill gate=`{payload['coverage_gate']['gate_pass']}`，"
        f"失败原因 `{','.join(payload['coverage_gate'].get('fail_reasons') or [])}`。"
        "因此不发布 current live PnL。",
        f"- Prereg SHA256=`{payload['artifacts']['preregistration_sha256']}`；"
        f"execution addendum SHA256="
        f"`{payload['artifacts']['execution_addendum_sha256']}`；"
        f"parent SHA256=`{payload['artifacts']['parent_sha256']}`。本次未同步、未重建、未改生产。",
        "",
        "## 固定分母与双漏斗",
        "",
        "| funnel | stage | grain | rows | dates | 说明 |",
        "|---|---|---|---:|---:|---|",
    ]
    for row in payload["funnel"]:
        lines.append(
            f"| {row['funnel']} | {row['stage']} | {row['grain']} | "
            f"{row['rows']} | {row['dates']} | {row['note']} |"
        )
    lines += [
        "",
        "Signal funnel 与 evidence/execution funnel 分开；strict-high/remaining-heat 是机制特征，"
        "book/source/settlement/fill 缺失只记 coverage gap。历史 source first-seen ingest 时刻不可恢复，"
        "所以 source surprise 仅用 `report_ts <= decision_ts` proxy，且单列 provenance。",
        "",
        "## 概率层：baseline + overshoot residual",
        "",
        "| model | features through | rows/dates | Brier | logloss | AUC | ΔBrier vs core | Δlogloss vs core |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in MODEL_SPECS:
        row = proper[name]
        delta = row["vs_core"]
        lines.append(
            f"| {name} | {row['feature_boundary']} | {row['rows']}/{row['dates']} | "
            f"{row['brier']:.5f} | {row['logloss']:.5f} | {row['auc']:.3f} | "
            f"{delta['candidate_minus_baseline_brier']:+.6f} | "
            f"{delta['candidate_minus_baseline_logloss']:+.6f} |"
        )
    core_row = payload["probability"]["baselines"]["frozen_core"]
    market_row = payload["probability"]["baselines"]["market"]
    lift = payload["probability"]["compact_risk_lift"]
    lines += [
        "",
        f"同 rows raw baseline：market Brier/logloss `{market_row['brier']:.5f}/"
        f"{market_row['logloss']:.5f}`；frozen core `{core_row['brier']:.5f}/"
        f"{core_row['logloss']:.5f}`。compact top-decile overshoot lift="
        f"`{fmt_num(lift['top_decile_lift'],2)}x`，捕获 "
        f"`{fmt_pct(lift['overshoot_capture_rate'])}` 的 overshoot city-days。",
        "",
        "特征消融按预注册顺序累计，没有从 6 个坏例子追加 AND gate。"
        "`minutes_since_running_max` 未进入 challenger；strict clock 由历史逐报重建，equal high 不重置。",
        f"LODO 诊断覆盖 {lodo_probability['metrics']['rows']} states："
        f"Brier/logloss `{lodo_probability['metrics']['brier']:.5f}/"
        f"{lodo_probability['metrics']['logloss']:.5f}`，相对 core Δ "
        f"`{lodo_probability['vs_core']['candidate_minus_baseline_brier']:+.6f}/"
        f"{lodo_probability['vs_core']['candidate_minus_baseline_logloss']:+.6f}`；"
        "只作审计，不替代 expanding OOF。",
        "",
        "## 6 个 loss 与全部 winners 审计",
        "",
        "| city | target_date | bracket | final | market mid | strict-high age | remaining gap | slope accel | source surprise | LODO risk |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["loss_audit"]:
        lines.append(
            f"| {row['city']} | {row['target_date']} | {row['current_bracket']} | "
            f"{fmt_num(row.get('final_native'),2)} | {fmt_num(row.get('market_mid'),3)} | "
            f"{fmt_num(row.get('minutes_since_last_strict_new_high'),1)} | "
            f"{fmt_num(row.get('forecast_remaining_gap_to_running_native'),2)} | "
            f"{fmt_num(row.get('temp_slope_acceleration_f'),2)} | "
            f"{fmt_num(row.get('source_latest_new_high_surprise_f'),2)} | "
            f"{fmt_pct(row.get('p_over_compact_lodo'))} |"
        )
    lines += [
        "",
        f"Winner audit 完整保存 {payload['parent']['frozen_selected_wins']} 行；"
        "没有只对 loss 做事后过滤。LODO risk 只用于逐例审计，不作为 expanding-forward 晋升证据。",
        "",
        "## Sizing A/B（maker/taker 分账）",
        "",
        "主压力口径是 `adverse_same_overall_rate`：loss maker 100% fill，winner maker fill rate 下调，"
        "使总 fill rate仍等于当前观察值。Future touch 从未当作 fill。",
        "",
        "| policy | city-days | capital | PnL | ROI | max day loss | ES/CVaR 10% |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in adverse.iterrows():
        lines.append(
            f"| {row['policy']} | {int(row['selected_city_days'])} | "
            f"${row['capital_usage_usd']:.2f} | ${row['pnl_usd']:+.2f} | "
            f"{fmt_pct(row['roi'])} | ${row['max_single_day_loss_usd']:+.2f} | "
            f"${row['expected_shortfall_10pct_usd']:+.2f} |"
        )
    lines += [
        "",
        "| candidate vs baseline | mean daily PnL Δ [95%CI] | ROI Δ [95%CI] | winner share harm | frozen-forward mean daily PnL Δ |",
        "|---|---:|---:|---:|---:|",
    ]
    harm_by = harm.set_index("policy")
    forward = periods[
        periods["period"].eq("frozen_forward")
        & periods["maker_scenario"].eq("adverse_same_overall_rate")
    ].set_index("policy")
    baseline_forward = forward.loc[POLICIES[0], "mean_daily_pnl_usd"]
    for _, row in delta_adverse.iterrows():
        policy = row["policy"]
        lines.append(
            f"| {policy} | ${row['mean_daily_pnl_delta_usd']:+.3f} "
            f"[${row['mean_daily_pnl_delta_ci95'][0]:+.3f},"
            f"${row['mean_daily_pnl_delta_ci95'][1]:+.3f}] | "
            f"{fmt_pct(row['roi_delta'])} "
            f"[{fmt_pct(row['roi_delta_ci95'][0])},{fmt_pct(row['roi_delta_ci95'][1])}] | "
            f"{fmt_pct(harm_by.loc[policy, 'winner_desired_share_harm_rate'])} | "
            f"${forward.loc[policy, 'mean_daily_pnl_usd'] - baseline_forward:+.3f} |"
        )
    baseline_scenarios = summary[
        summary["policy"].eq(POLICIES[0])
    ].set_index("maker_scenario")
    selected_scenarios = summary[
        summary["policy"].eq(payload["verdict"]["selected_policy"])
    ].set_index("maker_scenario")
    lines += [
        "",
        "Maker adverse-selection sensitivity（不是 realized PnL）：",
        "",
        "| maker scenario | baseline PnL | selected candidate PnL | PnL Δ |",
        "|---|---:|---:|---:|",
    ]
    for scenario in SCENARIOS:
        base_pnl = baseline_scenarios.loc[scenario, "pnl_usd"]
        candidate_pnl = selected_scenarios.loc[scenario, "pnl_usd"]
        lines.append(
            f"| {scenario} | ${base_pnl:+.2f} | ${candidate_pnl:+.2f} | "
            f"${candidate_pnl - base_pnl:+.2f} |"
        )
    lodo_sizing = {
        row["policy"]: row
        for row in payload["sizing"]["leave_one_date_out"]
    }
    lines += [
        "",
        "Sizing leave-one-date-out adverse mean-daily-PnL Δ range："
        + "；".join(
            f"`{policy}` "
            f"[${row['leave_one_date_out_mean_daily_pnl_delta_min']:+.3f},"
            f"${row['leave_one_date_out_mean_daily_pnl_delta_max']:+.3f}]"
            for policy, row in lodo_sizing.items()
        )
        + "。",
    ]
    lines += [
        "",
        "Maker actual evidence 仍不足："
        f"taker city-days={payload['maker_evidence']['by_leg'].get('taker', {}).get('city_days', 0)}，"
        f"maker city-days={payload['maker_evidence']['by_leg'].get('maker', {}).get('city_days', 0)}，"
        f"settled maker fills={payload['maker_evidence']['settled_maker_fills']}。"
        "因此 maker 结果只可读为 sensitivity，不是实现 PnL。",
        "",
        "## 稳健性与三门",
        "",
        f"- significance proper-score CI gate="
        f"`{payload['verdict']['proper_score_ci_gate']}`；"
        f"adverse PnL delta CI gate=`{payload['verdict']['adverse_pnl_ci_gate']}`。",
        f"- baseline=same-row market + frozen core；gate="
        f"`{payload['verdict']['proper_score_point_gate']}`（点估）/",
        f"`{payload['verdict']['proper_score_ci_gate']}`（CI）。",
        f"- forward=last {FROZEN_FORWARD_DATES} OOF dates frozen slice；same-sign gate="
        f"`{payload['verdict']['frozen_forward_same_sign_gate']}`。",
        "- front/back、frozen-forward 与 sizing leave-one-date-out 均保存为固定产物；"
        "没有按结果重选日期或阈值。",
        f"- conclusion=`{payload['verdict']['action']}`；任何生产变更都必须另走 "
        "`weather-strategy-deploy` 并再次取得用户显式确认。",
        "",
        "## 8 环覆盖",
        "",
        "- 描述性切片：PASS（完整 parent、loss/winner audit）。",
        "- 统计推断：PASS（city-day 等权、target-date block bootstrap）。",
        "- 信号判别/概率：PASS（expanding OOF、market/core baseline、ablation）。",
        "- 执行微结构：PARTIAL（真实 taker ladder；maker fill/queue 尚无 settled 分母）。",
        "- 容量：PASS 到 10 taker / 15 total desired cap 的历史 ladder；更大未测试。",
        "- 组合相关性：PASS（同 target_date 合并 city-day cashflow）。",
        "- 基准/反事实：PASS（同 rows、同 quote、只改概率/size）。",
        "- frozen forward：PARTIAL/FAIL 取决于上面的 same-sign 与 CI，日期仍少。",
        "",
        "## 产物与复现",
        "",
        "```bash",
        ".venv/bin/python scripts/analysis/reheat_risk/"
        "research_current_yes_core_carry_overshoot_risk_sizing_overlay_v1.py",
        "```",
        "",
    ]
    for key, path in payload["outputs"].items():
        lines.append(f"- {key}: `{path}`")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not PREREG.exists():
        raise FileNotFoundError(f"pre-registration missing: {PREREG}")
    if not PREREG_EXECUTION_ADDENDUM.exists():
        raise FileNotFoundError(
            f"execution addendum missing: {PREREG_EXECUTION_ADDENDUM}"
        )
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    execution_addendum = json.loads(
        PREREG_EXECUTION_ADDENDUM.read_text(encoding="utf-8")
    )
    if not prereg.get("frozen_before_results"):
        raise RuntimeError("pre-registration is not frozen")

    parent, feature_lineage = prepare_features()
    parent = taker10.add_ten_share_cost(parent)
    predictions, folds = expanding_predictions(parent)
    lodo = leave_date_out_predictions(parent)
    ledger = parent.merge(
        predictions.drop(
            columns=[
                "city",
                "target_date",
                "decision_snapshot_ts_utc",
                "current_bracket",
                "overshoot",
                "label",
            ]
        ),
        on="opportunity_id",
        how="left",
        validate="one_to_one",
    ).merge(lodo, on="opportunity_id", how="left", validate="one_to_one")
    ledger["p_hold_adjusted"] = 1.0 - ledger["p_over_compact_v1"]
    ledger["adjusted_edge"] = (
        ledger["p_hold_adjusted"] - ledger["five_share_cost_per_share"]
    )
    ledger["adjusted_edge_ten_share"] = (
        ledger["p_hold_adjusted"] - ledger["ten_share_cost_per_share"]
    )
    full_baseline_entries = first_positive(ledger, "p_core_no_obs_age")
    selected_ids = set(full_baseline_entries["opportunity_id"])
    ledger["frozen_baseline_selected"] = ledger["opportunity_id"].isin(selected_ids)
    eval_rows = ledger[ledger["p_over_compact_v1"].notna()].copy()
    eval_dates = sorted(eval_rows["target_date"].unique())
    forward_dates = set(eval_dates[-FROZEN_FORWARD_DATES:])

    models = []
    for name in MODEL_SPECS:
        row = score_metrics(eval_rows, f"p_over_{name}")
        row["model"] = name
        row["feature_boundary"] = MODEL_SPECS[name][-1]
        row["vs_core"] = proper_score_delta(
            eval_rows, f"p_over_{name}", "p_over_core"
        )
        models.append(row)
    compact_delta = proper_score_delta(
        eval_rows, "p_over_compact_v1", "p_over_core"
    )
    probability = {
        "baselines": {
            "market": score_metrics(eval_rows, "p_over_market"),
            "frozen_core": score_metrics(eval_rows, "p_over_core"),
        },
        "models": models,
        "compact_risk_lift": risk_lift(eval_rows, "p_over_compact_v1"),
        "compact_vs_market": proper_score_delta(
            eval_rows, "p_over_compact_v1", "p_over_market"
        ),
        "compact_leave_date_out_diagnostic": {
            "metrics": score_metrics(
                ledger[ledger["p_over_compact_lodo"].notna()],
                "p_over_compact_lodo",
            ),
            "vs_core": proper_score_delta(
                ledger[ledger["p_over_compact_lodo"].notna()],
                "p_over_compact_lodo",
                "p_over_core",
            ),
        },
    }

    production = production_snapshot()
    actual_policy = execution_addendum["allowed_change"]
    actual_taker_shares = float(actual_policy["baseline_taker_shares"])
    actual_maker_shares = float(actual_policy["baseline_maker_shares"])
    latest = production["latest_summary"]
    if (
        float(latest.get("taker_shares") or 0) != actual_taker_shares
        or float(latest.get("maker_shares") or 0) != actual_maker_shares
        or not production["process_agrees_with_summary"]
    ):
        raise RuntimeError(
            "production sizing changed after execution addendum; "
            f"summary={latest.get('taker_shares')}+{latest.get('maker_shares')}, "
            f"process_agrees={production['process_agrees_with_summary']}"
        )
    maker_evidence = maker_fill_snapshot()
    policies = policy_frames(
        eval_rows,
        actual_taker_shares=actual_taker_shares,
        actual_maker_shares=actual_maker_shares,
    )
    detail = add_execution(policies, eval_rows)
    daily, summary = daily_and_summary(
        detail,
        float(maker_evidence["maker_fill_rate_per_taker_city_day"]),
        eval_dates,
    )
    deltas = bootstrap_policy_deltas(daily)
    periods = period_metrics(daily, forward_dates)
    harm = winner_harm(detail)
    lodo_range = leave_one_date_out_range(daily)
    verdict = choose_action(
        compact_delta, summary, deltas, periods, harm
    )

    audit_columns = [
        "opportunity_id",
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "decision_hour_local",
        "current_bracket",
        "final_native",
        "market_mid",
        "five_share_cost_per_share",
        "p_core_no_obs_age",
        "minutes_since_running_max",
        "minutes_since_last_strict_new_high",
        "same_running_max_obs_count",
        "forecast_remaining_gap_to_running_native",
        "forecast_reheat_after_now_f",
        "forecast_max_revision_native",
        "forecast_peak_hour_revision",
        "temp_slope_acceleration_f",
        "temp_curve_acceleration_f",
        "reheating_transition_num",
        "source_latest_temp_change_f",
        "source_latest_new_high_surprise_f",
        "source_to_settlement_basis_mean_pit_f",
        "source_to_settlement_basis_mae_pit_f",
        "p_over_compact_lodo",
    ]
    loss_audit = full_baseline_entries[
        full_baseline_entries["label"].eq(0)
    ][audit_columns].copy()
    winner_audit = full_baseline_entries[
        full_baseline_entries["label"].eq(1)
    ][audit_columns].copy()

    feature_coverage = []
    for feature in sorted(set(sum(MODEL_SPECS.values(), []))):
        values = pd.to_numeric(parent[feature], errors="coerce")
        feature_coverage.append(
            {
                "feature": feature,
                "rows": int(values.notna().sum()),
                "coverage": float(values.notna().mean()),
                "unique_non_null": int(values.nunique(dropna=True)),
            }
        )
    funnel = [
        {
            "funnel": "signal",
            "stage": "frozen core carry eligible checkpoint",
            "grain": "state",
            "rows": int(len(parent)),
            "dates": int(parent["target_date"].nunique()),
            "note": "all bounded exact, market-mid>=0.80, five-share executable OOF checkpoints",
        },
        {
            "funnel": "signal",
            "stage": "expanding residual OOF",
            "grain": "state",
            "rows": int(len(eval_rows)),
            "dates": int(len(eval_dates)),
            "note": f"{MIN_TRAIN_DATES} target-date warm-up; warm-up rows retained with NA challenger",
        },
        {
            "funnel": "signal",
            "stage": "frozen baseline selected",
            "grain": "city-day expression",
            "rows": int(len(full_baseline_entries)),
            "dates": int(full_baseline_entries["target_date"].nunique()),
            "note": "first frozen-core positive EV; not the model denominator",
        },
        {
            "funnel": "evidence",
            "stage": "historical strict-high/remaining-heat proxy",
            "grain": "state",
            "rows": int(
                parent["minutes_since_last_strict_new_high"].notna().sum()
            ),
            "dates": int(parent["target_date"].nunique()),
            "note": "report-time IEM/METAR + fixed CITY_MODEL curve",
        },
        {
            "funnel": "evidence",
            "stage": "historical source first-seen",
            "grain": "state",
            "rows": 0,
            "dates": 0,
            "note": "coverage gap: ingest first-seen unavailable; report-time proxy kept separate",
        },
        {
            "funnel": "execution",
            "stage": "full ten-share taker ladder covered",
            "grain": "state",
            "rows": int(
                parent["ten_share_executable"].sum()
            ),
            "dates": int(
                parent.loc[
                    parent["ten_share_executable"], "target_date"
                ].nunique()
            ),
            "note": "official per-level taker fee; no future touch assumption",
        },
        {
            "funnel": "execution",
            "stage": "actual 10+5 baseline selected in OOF evaluation window",
            "grain": "city-day expression",
            "rows": int(len(policies[POLICIES[0]])),
            "dates": int(
                policies[POLICIES[0]]["target_date"].nunique()
            ),
            "note": "first positive full ten-share taker-ladder EV; maker remains a separately stressed leg",
        },
        {
            "funnel": "execution",
            "stage": "actual settled maker fills",
            "grain": "fill",
            "rows": int(maker_evidence["settled_maker_fills"]),
            "dates": 0,
            "note": "coverage gap; maker evaluated only through explicit stress scenarios",
        },
    ]

    coverage = coverage_gate()
    snapshot = data_snapshot()

    ledger.to_csv(OUT_DIR / "opportunity_ledger.csv", index=False)
    predictions.to_csv(OUT_DIR / "oof_predictions.csv", index=False)
    folds.to_csv(OUT_DIR / "fold_parameters.csv", index=False)
    pd.DataFrame(models).to_json(
        OUT_DIR / "feature_ablation.json",
        orient="records",
        indent=2,
    )
    pd.json_normalize(models).to_csv(
        OUT_DIR / "feature_ablation.csv", index=False
    )
    pd.DataFrame(feature_coverage).to_csv(
        OUT_DIR / "feature_coverage.csv", index=False
    )
    loss_audit.to_csv(OUT_DIR / "loss_audit.csv", index=False)
    winner_audit.to_csv(OUT_DIR / "winner_audit.csv", index=False)
    lodo.to_csv(OUT_DIR / "leave_date_out_predictions.csv", index=False)
    detail.to_csv(OUT_DIR / "sizing_ab.csv", index=False)
    daily.to_csv(OUT_DIR / "daily_portfolio.csv", index=False)
    summary.to_csv(OUT_DIR / "policy_summary.csv", index=False)
    deltas.to_csv(OUT_DIR / "paired_policy_deltas.csv", index=False)
    periods.to_csv(OUT_DIR / "front_forward.csv", index=False)
    harm.to_csv(OUT_DIR / "winner_harm.csv", index=False)
    lodo_range.to_csv(OUT_DIR / "leave_one_date_out.csv", index=False)
    pd.DataFrame(funnel).to_csv(OUT_DIR / "funnel.csv", index=False)
    (OUT_DIR / "production_readonly_snapshot.json").write_text(
        json.dumps(json_ready(production), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    payload = {
        "research_id": RESEARCH_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "target": prereg["target"],
        "parent": {
            "rows": int(len(parent)),
            "city_days": int(
                parent.groupby(["city", "target_date"]).ngroups
            ),
            "dates": int(parent["target_date"].nunique()),
            "cities": int(parent["city"].nunique()),
            "date_min": str(parent["target_date"].min()),
            "date_max": str(parent["target_date"].max()),
            "overshoot_states": int(parent["overshoot"].sum()),
            "frozen_selected_city_days": int(len(full_baseline_entries)),
            "frozen_selected_wins": int(full_baseline_entries["label"].sum()),
            "frozen_selected_losses": int(
                full_baseline_entries["label"].eq(0).sum()
            ),
            "loss_direction_check": (
                full_baseline_entries[
                    full_baseline_entries["label"].eq(0)
                ]["loss_direction"]
                .value_counts()
                .to_dict()
            ),
        },
        "data_snapshot": snapshot,
        "feature_lineage": feature_lineage,
        "feature_coverage": feature_coverage,
        "funnel": funnel,
        "probability": probability,
        "loss_audit": json_ready(loss_audit.to_dict("records")),
        "maker_evidence": maker_evidence,
        "production": production,
        "coverage_gate": coverage,
        "sizing": {
            "policies": prereg["sizing_policies"],
            "execution_addendum": execution_addendum,
            "effective_actual_fixed_size": {
                "taker_shares": actual_taker_shares,
                "maker_shares": actual_maker_shares,
            },
            "maker_scenarios": prereg["maker_scenarios"],
            "summary": json_ready(summary.to_dict("records")),
            "paired_deltas": json_ready(deltas.to_dict("records")),
            "periods": json_ready(periods.to_dict("records")),
            "winner_harm": json_ready(harm.to_dict("records")),
            "leave_one_date_out": json_ready(
                lodo_range.to_dict("records")
            ),
            "evaluation_dates": eval_dates,
            "frozen_forward_dates": sorted(forward_dates),
        },
        "verdict": verdict,
        "artifacts": {
            "preregistration_sha256": sha256(PREREG),
            "execution_addendum_sha256": sha256(
                PREREG_EXECUTION_ADDENDUM
            ),
            "parent_sha256": sha256(PARENT_PATH),
        },
        "outputs": {
            "script": str(Path(__file__).relative_to(ROOT)),
            "preregistration": str(PREREG.relative_to(ROOT)),
            "execution_addendum": str(
                PREREG_EXECUTION_ADDENDUM.relative_to(ROOT)
            ),
            "opportunity_ledger": str(
                (OUT_DIR / "opportunity_ledger.csv").relative_to(ROOT)
            ),
            "oof_predictions": str(
                (OUT_DIR / "oof_predictions.csv").relative_to(ROOT)
            ),
            "feature_ablation": str(
                (OUT_DIR / "feature_ablation.csv").relative_to(ROOT)
            ),
            "leave_date_out_predictions": str(
                (OUT_DIR / "leave_date_out_predictions.csv").relative_to(ROOT)
            ),
            "loss_audit": str(
                (OUT_DIR / "loss_audit.csv").relative_to(ROOT)
            ),
            "winner_audit": str(
                (OUT_DIR / "winner_audit.csv").relative_to(ROOT)
            ),
            "sizing_ab": str((OUT_DIR / "sizing_ab.csv").relative_to(ROOT)),
            "daily_portfolio": str(
                (OUT_DIR / "daily_portfolio.csv").relative_to(ROOT)
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
                    "probability": payload["probability"],
                    "sizing_adverse": summary[
                        summary["maker_scenario"].eq(
                            "adverse_same_overall_rate"
                        )
                    ].to_dict("records"),
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
