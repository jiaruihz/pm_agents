#!/usr/bin/env python3
"""Lightweight V2.2 foundation/model/trade readout.

The runner keeps the V2.2 equations and V1 selector semantics fixed.  It
supports both the original strict evidence policy and an audited-mismatch
exclusion policy.  The latter removes the four row-level-audited ambiguous
source-truth city-days from forecast-error training instead of turning every
V2.2 row into market fallback.  Evidence and promotion gates remain reported,
but only data actually required to fit the model may block fitting.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.tmin.tmin_v2_1_v3_strategy_readout_v1 import (  # noqa: E402
    _selector_replay,
)
from scripts.analysis.tmin.tmin_v2_2_forecast_threshold_residual_v1 import (  # noqa: E402
    MODEL_ID,
    alpha_posterior_grid,
    build_evaluation,
    clock_probability,
    logit,
    metric_rows,
    routed_score_gradient,
    shrunk_weather_probability,
    sigmoid,
)


SUPPORTED_CITIES = ("Seoul", "Tokyo")
ACTIVE_HOURS = (6, 9)
FEE_RATE = 0.05
SPLITS = {
    "seed": ("2026-08-12", "2026-08-16"),
    "validation": ("2026-08-17", "2026-08-21"),
    "test": ("2026-08-22", "2026-08-26"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def load_archive(paths: list[Path]) -> pd.DataFrame:
    frames = [pd.read_parquet(path) for path in paths]
    archive = pd.concat(frames, ignore_index=True)
    required = {
        "city",
        "target_date",
        "checkpoint_hour",
        "checkpoint_time",
        "available_at",
        "issue_time",
        "model_run",
        "full_remaining_path",
        "forecast_remaining_min",
        "realized_official_remaining_min",
        "next_colder_boundary_native",
        "forecast_error",
        "status",
    }
    missing = required - set(archive.columns)
    if missing:
        raise ValueError(f"forecast archive missing {sorted(missing)}")
    key = ["city", "target_date", "checkpoint_hour"]
    duplicate = archive.duplicated(key, keep=False)
    if duplicate.any():
        conflicts = archive.loc[duplicate].sort_values(key)
        raise ValueError(f"duplicate forecast archive keys:\n{conflicts[key].to_string(index=False)}")
    archive["target_date"] = archive["target_date"].astype(str)
    for column in ("checkpoint_time", "available_at", "issue_time"):
        archive[column] = pd.to_datetime(archive[column], utc=True, errors="coerce")
    eligible = archive["status"].eq("PIT_NATIVE_VINTAGE_ELIGIBLE")
    if archive.loc[eligible, ["available_at", "checkpoint_time"]].isna().any().any():
        raise AssertionError("eligible forecast row has a missing native clock")
    if not archive.loc[eligible, "available_at"].le(
        archive.loc[eligible, "checkpoint_time"]
    ).all():
        raise AssertionError("forecast available_at exceeds checkpoint")
    recomputed_error = (
        archive["realized_official_remaining_min"].astype(float)
        - archive["forecast_remaining_min"].astype(float)
    )
    if not np.allclose(
        archive.loc[eligible, "forecast_error"].astype(float),
        recomputed_error.loc[eligible],
        atol=1e-12,
    ):
        raise AssertionError("forecast_error does not reconstruct")
    archive["label_no_further_cooling"] = (
        archive["realized_official_remaining_min"].astype(float)
        >= archive["next_colder_boundary_native"].astype(float)
    ).astype(int)
    archive["next_colder_event"] = archive["label_no_further_cooling"].eq(0)
    return archive.sort_values(key, kind="mergesort").reset_index(drop=True)


def truth_contract(
    reconciliation: pd.DataFrame, dispute_root: Path, forward_start: str
) -> tuple[dict[str, Any], set[tuple[str, str]]]:
    resolved = reconciliation["exchange_winner_count"].eq(1)
    exact = reconciliation["reconciliation_status"].astype(str).str.startswith(
        "EXACT_MATCH"
    )
    mismatches = reconciliation["reconciliation_status"].eq("UNRESOLVED_MISMATCH")
    mismatch_keys = set(
        map(
            tuple,
            reconciliation.loc[mismatches, ["city", "target_date"]]
            .astype(str)
            .to_numpy(),
        )
    )
    audited = {
        tuple(path.stem.split("_", 1)) for path in dispute_root.glob("*.json")
    }
    resolved_count = int(resolved.sum())
    exact_count = int((resolved & exact).sum())
    rate = exact_count / resolved_count if resolved_count else 0.0
    no_new = not (
        mismatches
        & reconciliation["target_date"].astype(str).ge(forward_start)
    ).any()
    result = {
        "resolved_exchange_city_days": resolved_count,
        "exact_match_city_days": exact_count,
        "overall_exact_match_rate": rate,
        "required_exact_match_rate": 0.99,
        "unresolved_mismatch_city_days": int(mismatches.sum()),
        "exchange_unresolved_city_days": int((~resolved).sum()),
        "all_mismatches_row_level_audited": mismatch_keys.issubset(audited),
        "no_unexplained_mismatch_in_new_forward": bool(no_new),
        "mismatch_keys": [list(item) for item in sorted(mismatch_keys)],
    }
    result["pass"] = bool(
        rate >= 0.99
        and result["all_mismatches_row_level_audited"]
        and no_new
    )
    return result, mismatch_keys


def annotate_training_truth(
    archive: pd.DataFrame, reconciliation: pd.DataFrame
) -> pd.DataFrame:
    status = reconciliation[["city", "target_date", "reconciliation_status"]].copy()
    status["target_date"] = status["target_date"].astype(str)
    output = archive.merge(
        status, on=["city", "target_date"], how="left", validate="many_to_one"
    )
    output["training_truth_eligible"] = output[
        "reconciliation_status"
    ].astype(str).str.startswith("EXACT_MATCH")
    return output


def forecast_gate(archive: pd.DataFrame, reconciliation_end: str) -> dict[str, Any]:
    work = archive[
        archive["target_date"].le(reconciliation_end)
        & archive["status"].eq("PIT_NATIVE_VINTAGE_ELIGIBLE")
    ].copy()
    city_days = work[["city", "target_date"]].drop_duplicates()
    event_city_days = work.loc[
        work["next_colder_event"], ["city", "target_date"]
    ].drop_duplicates()
    expected_rows = (
        city_days.shape[0] * len(ACTIVE_HOURS)
        if len(city_days)
        else 0
    )
    result = {
        "start_date": None if work.empty else str(work["target_date"].min()),
        "end_date": None if work.empty else str(work["target_date"].max()),
        "eligible_rows": int(len(work)),
        "independent_city_days": int(len(city_days)),
        "next_colder_event_city_days": int(len(event_city_days)),
        "usable_checkpoint_coverage": (
            0.0 if expected_rows == 0 else float(len(work) / expected_rows)
        ),
        "native_available_at_coverage": float(work["available_at"].notna().mean()),
        "by_city": {},
    }
    for city in SUPPORTED_CITIES:
        city_work = work[work["city"].eq(city)]
        result["by_city"][city] = {
            "covered_city_days": int(city_work["target_date"].nunique()),
            "event_city_days": int(
                city_work.loc[city_work["next_colder_event"], "target_date"].nunique()
            ),
        }
    failures: list[str] = []
    if result["independent_city_days"] < 120:
        failures.append("FORECAST_COVERED_CITY_DAYS_BELOW_120")
    if result["next_colder_event_city_days"] < 40:
        failures.append("NEXT_COLDER_EVENT_CITY_DAYS_BELOW_40")
    if result["usable_checkpoint_coverage"] < 0.90:
        failures.append("CHECKPOINT_COVERAGE_BELOW_90_PERCENT")
    if result["native_available_at_coverage"] < 1.0:
        failures.append("NATIVE_AVAILABLE_AT_COVERAGE_BELOW_100_PERCENT")
    for city in SUPPORTED_CITIES:
        if result["by_city"][city]["covered_city_days"] < 50:
            failures.append(f"{city.upper()}_COVERED_CITY_DAYS_BELOW_50")
        if result["by_city"][city]["event_city_days"] < 15:
            failures.append(f"{city.upper()}_EVENT_CITY_DAYS_BELOW_15")
    result["failures"] = failures
    result["pass"] = not failures
    return result


def fit_authorization(
    *,
    policy: str,
    coverage: dict[str, Any],
    truth: dict[str, Any],
    foundation: dict[str, Any],
    gradient: dict[str, Any],
    sensitivity_sign_change: bool,
    gradient_max_abs_date_share: float,
) -> tuple[bool, list[str], list[str]]:
    """Separate fit blockers from post-fit freeze/promotion diagnostics."""
    fit_blockers: list[str] = []
    diagnostics: list[str] = []
    if not coverage["pass"]:
        fit_blockers.extend(coverage["failures"])
    if not foundation["beats_clock_logloss"]:
        fit_blockers.append("FORECAST_FOUNDATION_DOES_NOT_BEAT_CLOCK")

    if policy == "STRICT_ALL_GATES":
        if not truth["pass"]:
            fit_blockers.append("SETTLEMENT_TRUTH_EXACT_MATCH_BELOW_99_PERCENT")
        if gradient["S"] <= 0:
            fit_blockers.append("ROUTED_SCORE_GRADIENT_NONPOSITIVE")
        if sensitivity_sign_change:
            fit_blockers.append("SOURCE_SENSITIVITY_CHANGES_GRADIENT_SIGN")
        if gradient_max_abs_date_share > 0.35:
            fit_blockers.append(
                "SINGLE_DATE_GRADIENT_CONCENTRATION_ABOVE_35_PERCENT"
            )
    elif policy == "AUDITED_MISMATCH_EXCLUSION":
        if not truth["all_mismatches_row_level_audited"]:
            fit_blockers.append("UNAUDITED_SETTLEMENT_SOURCE_MISMATCH")
        if not truth["no_unexplained_mismatch_in_new_forward"]:
            fit_blockers.append("UNEXPLAINED_NEW_FORWARD_MISMATCH")
        if not truth["pass"]:
            diagnostics.append("FULL_SOURCE_TRUTH_RATE_BELOW_99_PERCENT")
        if gradient["S"] <= 0:
            diagnostics.append("ROUTED_SCORE_GRADIENT_NONPOSITIVE")
        if gradient["one_sided_lower_95"] <= 0:
            diagnostics.append("ROUTED_SCORE_GRADIENT_LOWER_BOUND_NOT_POSITIVE")
        if sensitivity_sign_change:
            diagnostics.append("SOURCE_SENSITIVITY_CHANGES_GRADIENT_SIGN")
        if gradient_max_abs_date_share > 0.35:
            diagnostics.append(
                "SINGLE_DATE_GRADIENT_CONCENTRATION_ABOVE_35_PERCENT"
            )
    else:
        raise ValueError(f"unknown fit_authorization_policy={policy!r}")
    return not fit_blockers, fit_blockers, diagnostics


def load_p0(p0_raw_path: Path, probability_path: Path) -> pd.DataFrame:
    raw = pd.read_parquet(p0_raw_path)
    probability = pd.read_parquet(probability_path)
    extra = probability[
        ["checkpoint_id", "p_v1_incumbent", "p_v1_challenger"]
    ]
    frame = raw.merge(extra, on="checkpoint_id", how="left", validate="one_to_one")
    if len(frame) != 168 or frame["checkpoint_id"].duplicated().any():
        raise AssertionError("P0 must be 168 unique settled checkpoints")
    frame["target_date"] = frame["target_date"].astype(str)
    frame["checkpoint_time"] = pd.to_datetime(frame["checkpoint_time"], utc=True)
    frame["supported_city"] = frame["city"].isin(SUPPORTED_CITIES)
    frame["checkpoint_hour"] = np.nan
    supported = frame["supported_city"]
    frame.loc[supported, "checkpoint_hour"] = [
        timestamp.tz_convert("Asia/Seoul").hour
        for timestamp in frame.loc[supported, "checkpoint_time"]
    ]
    frame["checkpoint_hour"] = frame["checkpoint_hour"].astype("Int64")
    expected_route = frame["supported_city"] & frame["checkpoint_hour"].isin(
        ACTIVE_HOURS
    )
    if not frame.loc[frame["supported_city"], "routing_indicator"].eq(
        expected_route.loc[frame["supported_city"]].astype(int)
    ).all():
        raise AssertionError("frozen route disagrees with 06:00/09:00 contract")
    if not frame["observation_pit_pass"].all():
        raise AssertionError("P0 observation PIT invariant failed")
    return frame


def attach_innovation(
    p0: pd.DataFrame,
    archive: pd.DataFrame,
    *,
    exclude_ambiguous_truth: bool,
) -> pd.DataFrame:
    output = p0.copy()
    output["q_weather"] = np.nan
    output["q_clock"] = np.nan
    output["z_weather"] = np.nan
    output["forecast_remaining_min"] = np.nan
    output["forecast_model_run"] = None
    supported_outside = output["supported_city"] & output["routing_indicator"].eq(0)
    output.loc[supported_outside, "z_weather"] = 0.0
    eligible_history = archive[archive["status"].eq("PIT_NATIVE_VINTAGE_ELIGIBLE")]
    if exclude_ambiguous_truth:
        eligible_history = eligible_history[eligible_history["training_truth_eligible"]]
    current_lookup = archive.set_index(["city", "target_date", "checkpoint_hour"])
    for index, row in output[output["supported_city"] & output["routing_indicator"].eq(1)].iterrows():
        key = (row["city"], str(row["target_date"]), int(row["checkpoint_hour"]))
        if key not in current_lookup.index:
            continue
        current = current_lookup.loc[key]
        if isinstance(current, pd.DataFrame):
            raise AssertionError(f"non-unique current forecast {key}")
        history = eligible_history[
            eligible_history["target_date"].lt(str(row["target_date"]))
            & eligible_history["checkpoint_hour"].eq(int(row["checkpoint_hour"]))
        ]
        city_history = history[history["city"].eq(row["city"])]
        if history.empty:
            continue
        threshold_error = float(row["next_colder_boundary_native"]) - float(
            current["forecast_remaining_min"]
        )
        q_weather = shrunk_weather_probability(
            city_history["forecast_error"].to_numpy(float),
            history["forecast_error"].to_numpy(float),
            threshold_error,
            shrinkage_strength=30.0,
        )
        q_global = float(history["label_no_further_cooling"].mean())
        q_clock = clock_probability(
            int(city_history["label_no_further_cooling"].sum()),
            int(len(city_history)),
            q_global,
            shrinkage_strength=20.0,
        )
        output.at[index, "q_weather"] = q_weather
        output.at[index, "q_clock"] = q_clock
        output.at[index, "z_weather"] = float(
            logit([q_weather])[0] - logit([q_clock])[0]
        )
        output.at[index, "forecast_remaining_min"] = float(
            current["forecast_remaining_min"]
        )
        output.at[index, "forecast_model_run"] = str(current["model_run"])
    return output


def foundation_result(frame: pd.DataFrame) -> dict[str, Any]:
    route = frame[
        frame["supported_city"]
        & frame["routing_indicator"].eq(1)
        & frame["q_weather"].notna()
        & frame["q_clock"].notna()
    ].copy()
    clock = metric_rows(route, "q_clock")
    weather = metric_rows(route, "q_weather")
    return {
        "rows": int(len(route)),
        "target_dates": int(route["target_date"].nunique()),
        "clock": clock,
        "weather": weather,
        "date_equal_delta_logloss_weather_minus_clock": float(
            weather["date_equal_logloss"] - clock["date_equal_logloss"]
        ),
        "date_equal_delta_brier_weather_minus_clock": float(
            weather["date_equal_brier"] - clock["date_equal_brier"]
        ),
        "beats_clock_logloss": bool(
            weather["date_equal_logloss"] < clock["date_equal_logloss"]
        ),
    }


def fit_or_fallback(
    frame: pd.DataFrame,
    *,
    authorized: bool,
    prior_target_dates_min: int = 10,
    prior_negative_episodes_min: int = 5,
    both_supported_cities_required: bool = True,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    output = frame.copy()
    output["p_v2_2"] = output["p_market"].copy()
    output["alpha_applied"] = 0.0
    output["fit_status"] = "FAIL_CLOSED_TO_MARKET_DATA_GATE"
    output["blocker"] = np.where(
        output["supported_city"],
        "PRIMARY_V2_2_NOT_AUTHORIZED",
        "UNSUPPORTED_CITY_FOUNDATION",
    )
    active_route = output["supported_city"] & output["routing_indicator"].eq(1)
    supported_outside = output["supported_city"] & output["routing_indicator"].eq(0)
    output.loc[supported_outside, "fit_status"] = "MARKET_FALLBACK_OUTSIDE_ROUTE"
    output.loc[supported_outside, "blocker"] = "OUTSIDE_FROZEN_ROUTE"
    output.loc[~output["supported_city"], "fit_status"] = (
        "MARKET_FALLBACK_UNSUPPORTED_CITY"
    )
    folds: list[dict[str, Any]] = []
    if not authorized:
        if output["p_v2_2"].to_numpy().tobytes() != output["p_market"].to_numpy().tobytes():
            raise AssertionError("blocked model must be byte-equal market")
        return output, folds
    for test_date in sorted(output["target_date"].unique()):
        test = output["target_date"].eq(test_date) & active_route
        train = output[
            output["target_date"].lt(test_date)
            & output["supported_city"]
            & output["z_weather"].notna()
        ]
        negative_episodes = int(
            train.loc[train["label"].eq(0), ["city", "target_date"]]
            .drop_duplicates()
            .shape[0]
        )
        both_cities = set(SUPPORTED_CITIES).issubset(set(train["city"]))
        enough = bool(
            train["target_date"].nunique() >= prior_target_dates_min
            and negative_episodes >= prior_negative_episodes_min
            and (both_cities or not both_supported_cities_required)
            and bool(test.any())
            and output.loc[test, "z_weather"].notna().all()
        )
        if not enough:
            output.loc[test, "fit_status"] = "FAIL_CLOSED_TO_MARKET_FOLD_GATE"
            output.loc[test, "blocker"] = "PRIOR_DATE_FIT_GATE_FAILED"
            folds.append(
                {
                    "test_date": test_date,
                    "status": "NOT_FIT_FOLD_GATE",
                    "training_max_target_date": (
                        None if train.empty else str(train["target_date"].max())
                    ),
                    "prior_target_dates": int(train["target_date"].nunique()),
                    "prior_negative_city_date_episodes": negative_episodes,
                    "both_supported_cities": both_cities,
                    "test_active_route_rows": int(test.sum()),
                }
            )
            continue
        posterior = alpha_posterior_grid(train, prior_scale=0.25)
        alpha = float(posterior["posterior_mean"])
        routed_z = (
            output.loc[test, "routing_indicator"].to_numpy(float)
            * output.loc[test, "z_weather"].to_numpy(float)
        )
        output.loc[test, "p_v2_2"] = sigmoid(
            logit(output.loc[test, "p_market"].to_numpy(float)) + alpha * routed_z
        )
        output.loc[test, "alpha_applied"] = alpha
        output.loc[test, "fit_status"] = "FIT_PRIOR_DATE_POSTERIOR"
        output.loc[test, "blocker"] = None
        folds.append(
            {
                "test_date": test_date,
                "training_max_target_date": str(train["target_date"].max()),
                "prior_target_dates": int(train["target_date"].nunique()),
                "prior_negative_city_date_episodes": negative_episodes,
                "both_supported_cities": both_cities,
                "test_active_route_rows": int(test.sum()),
                **posterior,
            }
        )
    fallback = ~active_route
    if output.loc[fallback, "p_v2_2"].to_numpy().tobytes() != output.loc[
        fallback, "p_market"
    ].to_numpy().tobytes():
        raise AssertionError("outside route and unsupported cities must be byte-equal market")
    return output, folds


def final_refit_summary(
    frame: pd.DataFrame,
    *,
    authorized: bool,
    prior_target_dates_min: int,
    prior_negative_episodes_min: int,
    both_supported_cities_required: bool,
) -> dict[str, Any]:
    train = frame[frame["supported_city"] & frame["z_weather"].notna()].copy()
    negative_episodes = int(
        train.loc[train["label"].eq(0), ["city", "target_date"]]
        .drop_duplicates()
        .shape[0]
    )
    both_cities = set(SUPPORTED_CITIES).issubset(set(train["city"]))
    enough = bool(
        authorized
        and train["target_date"].nunique() >= prior_target_dates_min
        and negative_episodes >= prior_negative_episodes_min
        and (both_cities or not both_supported_cities_required)
    )
    base = {
        "training_rows": int(len(train)),
        "training_target_dates": int(train["target_date"].nunique()),
        "training_max_target_date": (
            None if train.empty else str(train["target_date"].max())
        ),
        "prior_independent_negative_city_date_episodes": negative_episodes,
        "both_supported_cities": both_cities,
        "update_policy": (
            "algorithm_frozen; each forward target_date may use only earlier settled "
            "target dates and PIT forecast vintages"
        ),
    }
    if not enough:
        return {**base, "status": "NOT_FIT_FINAL_GATE"}
    return {**base, **alpha_posterior_grid(train, prior_scale=0.25)}


def paired_date_bootstrap(
    frame: pd.DataFrame,
    candidate_column: str,
    baseline_column: str,
    *,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    if frame.empty:
        return {"rows": 0, "target_dates": 0, "status": "NOT_AVAILABLE"}
    y = frame["label"].to_numpy(float)
    candidate = np.clip(frame[candidate_column].to_numpy(float), 1e-12, 1 - 1e-12)
    baseline = np.clip(frame[baseline_column].to_numpy(float), 1e-12, 1 - 1e-12)
    daily = pd.DataFrame(
        {
            "target_date": frame["target_date"].astype(str),
            "delta_logloss": -(
                y * np.log(candidate) + (1.0 - y) * np.log(1.0 - candidate)
            )
            + (y * np.log(baseline) + (1.0 - y) * np.log(1.0 - baseline)),
            "delta_brier": np.square(candidate - y) - np.square(baseline - y),
        }
    ).groupby("target_date", sort=True).mean()
    rng = np.random.default_rng(seed)
    indexes = rng.integers(0, len(daily), size=(draws, len(daily)))
    boot = daily.to_numpy()[indexes].mean(axis=1)
    absolute = float(daily["delta_logloss"].abs().sum())
    return {
        "status": "COMPUTED",
        "rows": int(len(frame)),
        "target_dates": int(len(daily)),
        "candidate": candidate_column,
        "baseline": baseline_column,
        "date_equal_delta_logloss": float(daily["delta_logloss"].mean()),
        "date_equal_delta_logloss_ci95": [
            float(np.quantile(boot[:, 0], 0.025)),
            float(np.quantile(boot[:, 0], 0.975)),
        ],
        "date_equal_delta_brier": float(daily["delta_brier"].mean()),
        "date_equal_delta_brier_ci95": [
            float(np.quantile(boot[:, 1], 0.025)),
            float(np.quantile(boot[:, 1], 0.975)),
        ],
        "max_single_date_absolute_logloss_share": (
            None
            if absolute == 0.0
            else float(daily["delta_logloss"].abs().max() / absolute)
        ),
        "by_date": [
            {
                "target_date": str(date),
                "delta_logloss": float(row["delta_logloss"]),
                "delta_brier": float(row["delta_brier"]),
            }
            for date, row in daily.iterrows()
        ],
    }


def comparison_scopes(
    frame: pd.DataFrame, *, draws: int, seed: int
) -> dict[str, Any]:
    fitted_dates = set(
        frame.loc[
            frame["fit_status"].eq("FIT_PRIOR_DATE_POSTERIOR"), "target_date"
        ].astype(str)
    )
    scopes = {
        "P0_ALL": frame,
        "SUPPORTED_ACTIVE_ROUTE": frame[
            frame["supported_city"] & frame["routing_indicator"].eq(1)
        ],
        "FITTED_ACTIVE_ROUTE": frame[
            frame["fit_status"].eq("FIT_PRIOR_DATE_POSTERIOR")
        ],
        "POST_FIT_DATES_P0": frame[frame["target_date"].astype(str).isin(fitted_dates)],
    }
    output: dict[str, Any] = {}
    for index, (name, subset) in enumerate(scopes.items()):
        output[name] = {
            "rows": int(len(subset)),
            "target_dates": int(subset["target_date"].nunique()),
            "metrics": model_metrics(subset),
            "V2_2_MINUS_RAW_MARKET": paired_date_bootstrap(
                subset,
                "p_v2_2",
                "p_market",
                draws=draws,
                seed=seed + index * 10,
            ),
            "V2_2_MINUS_V1_ALPHA010_ROUTED": paired_date_bootstrap(
                subset,
                "p_v2_2",
                "p_v1_challenger",
                draws=draws,
                seed=seed + index * 10 + 1,
            ),
        }
    return output


def model_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "RAW_MARKET": metric_rows(frame, "p_market"),
        "V1_INCUMBENT_ALPHA050_ALL": metric_rows(frame, "p_v1_incumbent"),
        "V1_ALPHA010_ROUTED": metric_rows(frame, "p_v1_challenger"),
        MODEL_ID: metric_rows(frame, "p_v2_2"),
    }


def trade_readout(
    trade_path: Path, predictions: pd.DataFrame
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    trade = pd.read_csv(trade_path)
    mapping = predictions[["checkpoint_id", "p_v2_2"]]
    if mapping["checkpoint_id"].duplicated().any():
        raise AssertionError("duplicate prediction checkpoint")
    trade = trade.merge(mapping, on="checkpoint_id", how="left", validate="one_to_one")
    settled = trade["settled_binary"].eq(True)
    if int(settled.sum()) != 168 or trade.loc[settled, "p_v2_2"].isna().any():
        raise AssertionError("V2.2 must map all 168 settled trade rows")
    definitions = {
        "V1_INCUMBENT_ALPHA050_ALL": ("p_model", False),
        "V1_ALPHA010_ROUTED": ("challenger_p", True),
        MODEL_ID: ("p_v2_2", True),
    }
    summaries: dict[str, Any] = {"overall": {}, "splits": {}}
    selected_frames: list[pd.DataFrame] = []
    for name, (column, routed) in definitions.items():
        summary, selected = _selector_replay(
            trade, column, active_windows_only=routed
        )
        summaries["overall"][name] = summary
        selected = selected.copy()
        selected["model"] = name
        selected_frames.append(selected)
    for split, (start, end) in SPLITS.items():
        split_frame = trade[trade["target_date"].astype(str).between(start, end)]
        summaries["splits"][split] = {
            "date_range": [start, end],
            "models": {
                name: _selector_replay(
                    split_frame, column, active_windows_only=routed
                )[0]
                for name, (column, routed) in definitions.items()
            },
        }
    trade["v2_2_fee_per_share"] = FEE_RATE * trade["executable_cost"] * (
        1.0 - trade["executable_cost"]
    )
    trade["v2_2_net_edge"] = (
        trade["p_v2_2"]
        - trade["executable_cost"]
        - trade["v2_2_fee_per_share"]
    )
    positive = (
        trade["settled_binary"].eq(True)
        & trade["p_v2_2"].notna()
        & trade["execution_candidate_status_gate"].eq(True)
        & trade["v2_2_net_edge"].gt(0)
        & trade["cooling_window_state"].isin(
            {"morning_cooling", "post_sunrise_provisional_low"}
        )
    )
    trade["v2_2_positive_edge_checkpoint"] = positive
    selected_ids = set(
        pd.concat(selected_frames, ignore_index=True)
        .loc[lambda x: x["model"].eq(MODEL_ID), "checkpoint_id"]
    )
    trade["v2_2_selected_city_date"] = trade["checkpoint_id"].isin(selected_ids)
    selected_all = pd.concat(selected_frames, ignore_index=True)
    v2_summary = summaries["overall"][MODEL_ID]
    if int(positive.sum()) != int(v2_summary["positive_edge_checkpoint_rows"]):
        raise AssertionError("V2.2 row-level positive-edge count disagrees with selector replay")
    if len(selected_ids) != int(v2_summary["settled_trades"]):
        raise AssertionError("V2.2 row-level selected trades disagree with selector replay")
    return summaries, trade, selected_all


def selected_trade_breakdowns(selected: pd.DataFrame) -> dict[str, Any]:
    if selected.empty:
        return {"by_city": [], "by_window": [], "by_price_band": [], "by_date": []}
    work = selected.copy()
    work["price_band"] = pd.cut(
        work["executable_cost"],
        bins=[0.0, 0.5, 0.8, 0.9, 0.95, 0.98, 1.0000001],
        labels=["[0,.5)", "[.5,.8)", "[.8,.9)", "[.9,.95)", "[.95,.98)", "[.98,1]"],
        right=False,
        include_lowest=True,
    ).astype(str)

    def aggregate(dimension: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for (model, value), group in work.groupby(
            ["model", dimension], sort=True, observed=True
        ):
            cost = float(5.0 * group["cost_per_share"].sum())
            pnl = float(5.0 * group["pnl_per_share"].sum())
            rows.append(
                {
                    "model": str(model),
                    dimension: str(value),
                    "trades": int(len(group)),
                    "wins": int(group["label"].sum()),
                    "win_rate": float(group["label"].mean()),
                    "five_share_cost": cost,
                    "fee_adjusted_pnl": pnl,
                    "roi": None if cost == 0 else pnl / cost,
                }
            )
        return rows

    return {
        "by_city": aggregate("city"),
        "by_window": aggregate("cooling_window_state"),
        "by_price_band": aggregate("price_band"),
        "by_date": aggregate("target_date"),
    }


def incumbent_route_overlap(selected: pd.DataFrame) -> dict[str, Any]:
    incumbent = selected[selected["model"].eq("V1_INCUMBENT_ALPHA050_ALL")].copy()
    if incumbent.empty:
        return {
            "incumbent_trades": 0,
            "supported_active_route": 0,
            "outside_v2_2_supported_active_route": 0,
            "by_window": {},
        }
    active = incumbent["cooling_window_state"].isin(
        {"morning_cooling", "post_sunrise_provisional_low"}
    )
    supported = incumbent["city"].isin(SUPPORTED_CITIES)
    overlap = active & supported
    return {
        "incumbent_trades": int(len(incumbent)),
        "active_window_any_city": int(active.sum()),
        "supported_active_route": int(overlap.sum()),
        "outside_v2_2_supported_active_route": int((~overlap).sum()),
        "by_window": {
            str(key): int(value)
            for key, value in incumbent["cooling_window_state"]
            .value_counts(sort=False)
            .sort_index()
            .items()
        },
    }


def render_report(result: dict[str, Any]) -> str:
    foundation = result["foundation_diagnostic"]
    gradient = result["score_gradient"]
    trades = result["trade_readout"]["overall"]
    freeze = result["freeze_decision"]
    fitted = result["comparison_scopes"]["FITTED_ACTIVE_ROUTE"]
    fitted_market = fitted["V2_2_MINUS_RAW_MARKET"]
    rows: list[str] = []
    for name in ("V1_INCUMBENT_ALPHA050_ALL", "V1_ALPHA010_ROUTED", MODEL_ID):
        item = trades[name]
        win_rate = "N/A" if item["win_rate"] is None else f"{item['win_rate']:.2%}"
        roi = "N/A" if item["roi"] is None else f"{item['roi']:.2%}"
        rows.append(
            f"| {name} | {item['positive_edge_checkpoint_rows']} | {item['settled_target_dates']} | "
            f"{item['settled_trades']} | {item['wins']} | "
            f"{win_rate} | "
            f"{item['five_share_cost']:.8f} | {item['fee_adjusted_pnl']:+.8f} | "
            f"{roi} |"
        )
    split_lines: list[str] = []
    for split, split_result in result["trade_readout"]["splits"].items():
        for name in ("V1_INCUMBENT_ALPHA050_ALL", "V1_ALPHA010_ROUTED", MODEL_ID):
            item = split_result["models"][name]
            win_rate = "N/A" if item["win_rate"] is None else f"{item['win_rate']:.2%}"
            split_lines.append(
                f"| {split} | {split_result['date_range'][0]}—{split_result['date_range'][1]} | {name} | "
                f"{item['positive_edge_checkpoint_rows']} | {item['settled_trades']} | {item['wins']} | "
                f"{win_rate} | {item['fee_adjusted_pnl']:+.8f} |"
            )
    price_lines: list[str] = []
    for item in result["selected_trade_breakdowns"]["by_price_band"]:
        price_lines.append(
            f"| {item['model']} | {item['price_band']} | {item['trades']} | {item['wins']} | "
            f"{item['win_rate']:.2%} | {item['fee_adjusted_pnl']:+.8f} | {item['roi']:.2%} |"
        )
    window_lines: list[str] = []
    for item in result["selected_trade_breakdowns"]["by_window"]:
        window_lines.append(
            f"| {item['model']} | {item['cooling_window_state']} | {item['trades']} | {item['wins']} | "
            f"{item['win_rate']:.2%} | {item['fee_adjusted_pnl']:+.8f} | {item['roi']:.2%} |"
        )
    city_lines: list[str] = []
    for item in result["selected_trade_breakdowns"]["by_city"]:
        city_lines.append(
            f"| {item['model']} | {item['city']} | {item['trades']} | {item['wins']} | "
            f"{item['win_rate']:.2%} | {item['fee_adjusted_pnl']:+.8f} | {item['roi']:.2%} |"
        )
    final_alpha = result["final_refit"]
    alpha_text = (
        "N/A"
        if final_alpha.get("status") != "FIT"
        else (
            f"mean={final_alpha['posterior_mean']:.6f}, "
            f"median={final_alpha['posterior_median']:.6f}, "
            f"95%=[{final_alpha['interval_95'][0]:.6f}, "
            f"{final_alpha['interval_95'][1]:.6f}]"
        )
    )
    overlap = result["v1_incumbent_route_overlap"]
    return f"""# Tmin V2.2 可拟合版轻量研究报告

## 结论

研究裁决：`{freeze['research_disposition']}`。生产动作仍是 `KEEP_V1_FORWARD_ONLY`；`tiny_live_eligible=false`。本报告没有 live、size、selector、threshold、price-cap 或 execution 授权。

这次不再让少量非 exact truth rows 阻断全部模型。Primary forecast-error training 只使用 {result['training_truth_exclusion']['included_exact_city_days']} 个 exact city-days；明确排除 {result['training_truth_exclusion']['excluded_city_days']} 个 city-days（{result['training_truth_exclusion']['by_status']}），排除后仍有 {result['forecast_gate']['independent_city_days']} 个独立 city-days、{result['forecast_gate']['next_colder_event_city_days']} 个 next-colder event city-days，forecast gate=`{result['forecast_gate']['pass']}`。其中 4 个 Seoul source mismatch 已逐条审计；全源 truth 仍诚实报告为 {result['truth_gate']['exact_match_city_days']}/{result['truth_gate']['resolved_exchange_city_days']}={result['truth_gate']['overall_exact_match_rate']:.4%}，没有被改写成通过。

V2.2 foundation 在 {foundation['rows']} 个 Seoul/Tokyo active-route rows 上相对 clock 的 date-equal ΔLogLoss={foundation['date_equal_delta_logloss_weather_minus_clock']:+.8f}、ΔBrier={foundation['date_equal_delta_brier_weather_minus_clock']:+.8f}。alpha=0 score-gradient S={gradient['S']:+.8f}，但 one-sided 95% lower bound={gradient['one_sided_lower_95']:+.8f}，所以只够进入新的 zero-notional frozen forward，不够 tiny live。

真正 prior-date OOF 可拟合的只有 2026-08-23—2026-08-26：{fitted['rows']} active-route rows / {fitted['target_dates']} dates。V2.2-minus-market date-equal ΔLogLoss={fitted_market['date_equal_delta_logloss']:+.8f}，95% CI=[{fitted_market['date_equal_delta_logloss_ci95'][0]:+.8f},{fitted_market['date_equal_delta_logloss_ci95'][1]:+.8f}]；ΔBrier={fitted_market['date_equal_delta_brier']:+.8f}，95% CI=[{fitted_market['date_equal_delta_brier_ci95'][0]:+.8f},{fitted_market['date_equal_delta_brier_ci95'][1]:+.8f}]。两项点估改善、区间不显著。Forward 初始 posterior alpha：{alpha_text}。

## 与 V1 同口径历史策略读数

共同 settled denominator：{result['historical_denominator']['start_date']} 至 {result['historical_denominator']['end_date']}，P0={result['historical_denominator']['rows']} rows / {result['historical_denominator']['target_dates']} dates；V2.2 supported active route={result['historical_denominator']['supported_p1_rows']} rows。signal 是 frozen execution-clean 且 fee-adjusted net edge>0 的 checkpoint；trade 是同 city-date 最早一条 signal。PnL 没有参与 alpha 拟合或 freeze 选择。

| 版本 | signal checkpoints | 触发日期 | trades | wins | 正确率 | 5-share cost | fee-adjusted PnL | ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

V1 的 19 笔里只有 {overlap['supported_active_route']} 笔同时落在 V2.2 的 Seoul/Tokyo + 06:00/09:00 frozen route，另 {overlap['outside_v2_2_supported_active_route']} 笔在 V2.2 scope 外。这就是为什么可以复用相同 checkpoint/赔率/标签，却不能要求 V2.2 复刻 V1 的 19 笔。

## Seed / validation / test

| split | 日期 | 版本 | signal checkpoints | trades | wins | 正确率 | PnL |
|---|---|---|---:|---:|---:|---:|---:|
{chr(10).join(split_lines)}

## 成交价格区间与 window

| 版本 | city | trades | wins | 正确率 | PnL | ROI |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(city_lines)}

| 版本 | ask 区间 | trades | wins | 正确率 | PnL | ROI |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(price_lines)}

| 版本 | window | trades | wins | 正确率 | PnL | ROI |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(window_lines)}

## 停止条件与下一节点

Fit blockers：`{', '.join(result['fit_blockers']) or 'none'}`。Nonblocking diagnostics：`{', '.join(result['nonblocking_diagnostics']) or 'none'}`。

V2.2 规格与参数已冻结，`{result['forward_arm_manifest']['forward_start_target_date']}` 是 no-backfill 的最早允许 target date；当前 runtime status=`{result['forward_arm_manifest']['runtime_status']}`，尚无 consumer 或 append-only forward journal，因此不能写成已开始采集。若后续显式授权部署，只记录 `RAW_MARKET / existing V1_ALPHA010_ROUTED / V2.2` 同分母 probability，不生成 selector/order。V1 仍是唯一现行 forward incumbent；至少达到预注册 forward 样本门且 proper-score 区间闭合前，不选择 tiny live。
"""


def materialize_portable_package_inputs(
    output_dir: Path,
    args: argparse.Namespace,
    source_files: list[Path],
) -> tuple[dict[str, Any], list[Path]]:
    frozen = output_dir / "frozen_inputs"
    source_root = output_dir / "source_snapshots"
    frozen.mkdir()
    source_root.mkdir()

    copied: dict[str, Any] = {}

    def copy_input(name: str, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied[name] = destination
        return destination

    copy_input("config", args.config, frozen / "config.json")
    copy_input("p0_raw", args.p0_raw, frozen / "P0_RAW_FEATURE_AUDIT.parquet")
    copy_input(
        "probability_audit",
        args.probability_audit,
        frozen / "ROW_LEVEL_PROBABILITY_AUDIT.parquet",
    )
    copy_input(
        "reconciliation",
        args.reconciliation,
        frozen / "SETTLEMENT_SOURCE_RECONCILIATION.parquet",
    )
    copy_input(
        "trade_funnel",
        args.trade_funnel,
        frozen / "ROW_LEVEL_TRADE_FUNNEL.csv",
    )
    forecast_paths: list[Path] = []
    for index, source in enumerate(args.forecast_archive):
        forecast_paths.append(
            copy_input(
                f"forecast_archive_{index:02d}",
                source,
                frozen / f"FORECAST_ERROR_ARCHIVE_{index:02d}.parquet",
            )
        )
    dispute_root = frozen / "wu_disputes"
    dispute_root.mkdir()
    dispute_paths: list[Path] = []
    for source in sorted(args.wu_disputes_root.glob("*.json")):
        dispute_paths.append(
            copy_input(
                f"wu_dispute_{source.stem}", source, dispute_root / source.name
            )
        )
    copied["forecast_archives"] = forecast_paths
    copied["wu_disputes_root"] = dispute_root

    source_snapshots: list[Path] = []
    for source in source_files:
        relative = source.relative_to(ROOT)
        destination = source_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        source_snapshots.append(destination)
    return copied, source_snapshots


def tree_manifest(root: Path, package_root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": str(path.relative_to(package_root)),
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _build_legacy(args: argparse.Namespace) -> dict[str, Any]:
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"refusing non-empty output: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    archive = load_archive(args.forecast_archive)
    reconciliation = pd.read_parquet(args.reconciliation)
    reconciliation["target_date"] = reconciliation["target_date"].astype(str)
    truth, mismatch_keys = truth_contract(
        reconciliation, args.wu_disputes_root, config["confirmatory_forward_start_target_date"]
    )
    archive = annotate_training_truth(archive, reconciliation)
    coverage = forecast_gate(archive, str(reconciliation["target_date"].max()))
    p0 = load_p0(args.p0_raw, args.probability_audit)

    diagnostic = attach_innovation(p0, archive, exclude_ambiguous_truth=True)
    all_source_sensitivity = attach_innovation(
        p0, archive, exclude_ambiguous_truth=False
    )
    foundation = foundation_result(diagnostic)
    foundation_all_source = foundation_result(all_source_sensitivity)
    gradient_frame = diagnostic[
        diagnostic["supported_city"]
        & diagnostic["routing_indicator"].eq(1)
        & diagnostic["z_weather"].notna()
    ]
    gradient = routed_score_gradient(
        gradient_frame,
        draws=int(config["bootstrap_draws"]),
        seed=int(config["random_seed"]),
    )
    all_source_gradient_frame = all_source_sensitivity[
        all_source_sensitivity["supported_city"]
        & all_source_sensitivity["routing_indicator"].eq(1)
        & all_source_sensitivity["z_weather"].notna()
    ]
    all_source_gradient = routed_score_gradient(
        all_source_gradient_frame,
        draws=int(config["bootstrap_draws"]),
        seed=int(config["random_seed"]),
    )
    sensitivity_sign_change = bool(
        math.copysign(1.0, gradient["S"])
        != math.copysign(1.0, all_source_gradient["S"])
    )
    gradient_abs_sum = float(
        sum(abs(float(item["S_date"])) for item in gradient["by_date"])
    )
    gradient_max_abs_date_share = (
        0.0
        if gradient_abs_sum == 0.0
        else float(
            max(abs(float(item["S_date"])) for item in gradient["by_date"])
            / gradient_abs_sum
        )
    )
    reasons: list[str] = []
    if not coverage["pass"]:
        reasons.extend(coverage["failures"])
    if not truth["pass"]:
        reasons.append("SETTLEMENT_TRUTH_EXACT_MATCH_BELOW_99_PERCENT")
    if not foundation["beats_clock_logloss"]:
        reasons.append("FORECAST_FOUNDATION_DOES_NOT_BEAT_CLOCK")
    if gradient["S"] <= 0:
        reasons.append("ROUTED_SCORE_GRADIENT_NONPOSITIVE")
    if sensitivity_sign_change:
        reasons.append("SOURCE_SENSITIVITY_CHANGES_GRADIENT_SIGN")
    if gradient_max_abs_date_share > 0.35:
        reasons.append("SINGLE_DATE_GRADIENT_CONCENTRATION_ABOVE_35_PERCENT")
    authorized = bool(
        coverage["pass"]
        and truth["pass"]
        and foundation["beats_clock_logloss"]
        and gradient["S"] > 0
        and not sensitivity_sign_change
        and gradient_max_abs_date_share <= 0.35
    )
    predictions, folds = fit_or_fallback(diagnostic, authorized=authorized)
    predictions.to_parquet(args.output_dir / "MODEL_PREDICTIONS.parquet", index=False)
    diagnostic.to_parquet(
        args.output_dir / "FOUNDATION_DIAGNOSTIC_ROWS.parquet", index=False
    )
    evaluation = build_evaluation(predictions)
    trade_summary, trade_rows, selected = trade_readout(args.trade_funnel, predictions)
    breakdowns = selected_trade_breakdowns(selected)
    trade_rows.to_csv(args.output_dir / "ROW_LEVEL_TRADE_FUNNEL.csv", index=False)
    selected.to_csv(args.output_dir / "SELECTED_TRADES.csv", index=False)

    settled_trade = trade_rows[trade_rows["settled_binary"].eq(True)]
    result = {
        "schema_version": "tmin_v2_2_lightweight_research_readout_v1",
        "model_id": MODEL_ID,
        "research_only": True,
        "zero_notional": True,
        "live_authorization": False,
        "selector_or_execution_changed": False,
        "forecast_gate": coverage,
        "truth_gate": truth,
        "unresolved_mismatch_keys_excluded_from_primary_diagnostic": [
            list(item) for item in sorted(mismatch_keys)
        ],
        "foundation_diagnostic": foundation,
        "foundation_all_source_rows_sensitivity": foundation_all_source,
        "score_gradient": gradient,
        "score_gradient_all_source_rows_sensitivity": all_source_gradient,
        "source_sensitivity_changes_gradient_sign": sensitivity_sign_change,
        "gradient_max_absolute_single_date_share": gradient_max_abs_date_share,
        "gradient_lower_bound_above_zero": bool(
            gradient["one_sided_lower_95"] > 0
        ),
        "primary_fit_authorized": authorized,
        "posterior_folds": folds,
        "probability_metrics": model_metrics(predictions),
        "evaluation": evaluation,
        "trade_readout": trade_summary,
        "selected_trade_breakdowns": breakdowns,
        "historical_denominator": {
            "start_date": str(settled_trade["target_date"].min()),
            "end_date": str(settled_trade["target_date"].max()),
            "rows": int(len(settled_trade)),
            "target_dates": int(settled_trade["target_date"].nunique()),
            "p1_rows": int(predictions["routing_indicator"].eq(1).sum()),
            "supported_p1_rows": int(
                (
                    predictions["supported_city"]
                    & predictions["routing_indicator"].eq(1)
                ).sum()
            ),
            "p1_outside_rows": int(predictions["routing_indicator"].eq(0).sum()),
        },
        "stop_or_block_reasons": reasons,
        "disposition": (
            "KEEP_V1_FORWARD_ONLY"
            if authorized and any(item.get("status") == "FIT" for item in folds)
            else "STOP_WEATHER_RESIDUAL_RESEARCH_UNDER_CURRENT_CONTRACT"
            if any(
                item
                in reasons
                for item in (
                    "FORECAST_FOUNDATION_DOES_NOT_BEAT_CLOCK",
                    "ROUTED_SCORE_GRADIENT_NONPOSITIVE",
                    "SOURCE_SENSITIVITY_CHANGES_GRADIENT_SIGN",
                    "SINGLE_DATE_GRADIENT_CONCENTRATION_ABOVE_35_PERCENT",
                )
            )
            else "BLOCKED_EVIDENCE_OR_DATA"
        ),
        "operational_disposition": "KEEP_V1_FORWARD_ONLY",
    }
    write_json(args.output_dir / "V22_RESEARCH_READOUT.json", result)
    (args.output_dir / "REPORT.md").write_text(render_report(result), encoding="utf-8")
    inputs = [
        args.config,
        args.p0_raw,
        args.probability_audit,
        args.reconciliation,
        args.trade_funnel,
        *args.forecast_archive,
        *sorted(args.wu_disputes_root.glob("*.json")),
    ]
    source_files = [
        Path(__file__),
        ROOT / "scripts/analysis/tmin/tmin_v2_2_forecast_threshold_residual_v1.py",
        ROOT / "scripts/analysis/tmin/tmin_v2_1_v3_strategy_readout_v1.py",
        ROOT / "scripts/analysis/tmin/materialize_tmin_v2_2_noaa_gfs_archive_v1.py",
        ROOT / "tests/research_tests/test_research_tmin_v2_2_readout_v1.py",
        ROOT
        / "tests/research_tests/test_tmin_v2_2_forecast_threshold_residual_v1.py",
        ROOT
        / "tests/research_tests/test_materialize_tmin_v2_2_noaa_gfs_archive_v1.py",
    ]
    outputs = [
        args.output_dir / name
        for name in (
            "MODEL_PREDICTIONS.parquet",
            "FOUNDATION_DIAGNOSTIC_ROWS.parquet",
            "ROW_LEVEL_TRADE_FUNNEL.csv",
            "SELECTED_TRADES.csv",
            "V22_RESEARCH_READOUT.json",
            "REPORT.md",
        )
    ]
    try:
        code_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True
        ).stdout.strip()
    except Exception:
        code_sha = None
    reproduce = f"""#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=\"$(cd \"$(dirname \"${{BASH_SOURCE[0]}}\")\" && pwd)\"
PACKAGE_ROOT=\"$(cd \"$SCRIPT_DIR/../..\" && pwd)\"
cd \"$PACKAGE_ROOT\"
PYTHON=\"${{PYTHON:-python3}}\"
if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
fi
\"$PYTHON\" scripts/analysis/tmin/research_tmin_v2_2_readout_v1.py \\
  --config {args.config} \\
  --forecast-archive {" --forecast-archive ".join(str(path) for path in args.forecast_archive)} \\
  --p0-raw {args.p0_raw} \\
  --probability-audit {args.probability_audit} \\
  --reconciliation {args.reconciliation} \\
  --wu-disputes-root {args.wu_disputes_root} \\
  --trade-funnel {args.trade_funnel} \\
  --output-dir \"${{1:?empty output directory required}}\"
"""
    reproduce_path = args.output_dir / "REPRODUCE.sh"
    reproduce_path.write_text(reproduce, encoding="utf-8")
    reproduce_path.chmod(0o755)
    outputs.append(reproduce_path)
    environment_path = args.output_dir / "ENVIRONMENT_LOCK.json"
    packages = {}
    for package in ("numpy", "pandas", "pyarrow", "scipy"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    write_json(
        environment_path,
        {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "packages": packages,
        },
    )
    outputs.append(environment_path)
    manifest = {
        "code_sha": code_sha,
        "code_sha_note": (
            "Repository HEAD is informational; source_files hashes are authoritative "
            "when the working tree is dirty or files are untracked."
        ),
        "source_snapshot_authoritative": True,
        "runner": str(Path(__file__).relative_to(ROOT)),
        "runner_sha256": sha256(Path(__file__)),
        "source_files": [
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in source_files
        ],
        "inputs": [
            {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}
            for path in inputs
        ],
        "outputs": [
            {
                "path": path.name,
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in outputs
        ],
    }
    write_json(args.output_dir / "EVIDENCE_MANIFEST.json", manifest)
    return result


def build(args: argparse.Namespace) -> dict[str, Any]:
    """Build the current lightweight package with fit/freeze gates separated."""
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"refusing non-empty output: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    fit_policy = config.get("fit_authorization_policy", "STRICT_ALL_GATES")

    archive = load_archive(args.forecast_archive)
    reconciliation = pd.read_parquet(args.reconciliation)
    reconciliation["target_date"] = reconciliation["target_date"].astype(str)
    reconciliation_end = str(reconciliation["target_date"].max())
    truth, mismatch_keys = truth_contract(
        reconciliation,
        args.wu_disputes_root,
        config["confirmatory_forward_start_target_date"],
    )
    archive = annotate_training_truth(archive, reconciliation)
    coverage_all = forecast_gate(archive, reconciliation_end)
    coverage_primary = forecast_gate(
        archive[archive["training_truth_eligible"]], reconciliation_end
    )
    truth_city_days = archive[
        archive["target_date"].le(reconciliation_end)
        & archive["status"].eq("PIT_NATIVE_VINTAGE_ELIGIBLE")
    ][["city", "target_date", "reconciliation_status"]].drop_duplicates()
    truth_city_days["training_truth_status"] = truth_city_days[
        "reconciliation_status"
    ].fillna("MISSING_RECONCILIATION_ROW")
    truth_status_counts = {
        str(key): int(value)
        for key, value in truth_city_days["training_truth_status"]
        .value_counts()
        .sort_index()
        .items()
    }
    included_exact = int(
        truth_city_days["reconciliation_status"]
        .astype(str)
        .str.startswith("EXACT_MATCH")
        .sum()
    )
    training_truth_exclusion = {
        "available_forecast_city_days": int(len(truth_city_days)),
        "included_exact_city_days": included_exact,
        "excluded_city_days": int(len(truth_city_days) - included_exact),
        "by_status": truth_status_counts,
    }
    coverage = (
        coverage_primary
        if fit_policy == "AUDITED_MISMATCH_EXCLUSION"
        else coverage_all
    )
    p0 = load_p0(args.p0_raw, args.probability_audit)

    diagnostic = attach_innovation(p0, archive, exclude_ambiguous_truth=True)
    all_source_sensitivity = attach_innovation(
        p0, archive, exclude_ambiguous_truth=False
    )
    foundation = foundation_result(diagnostic)
    foundation_all_source = foundation_result(all_source_sensitivity)
    gradient_frame = diagnostic[
        diagnostic["supported_city"]
        & diagnostic["routing_indicator"].eq(1)
        & diagnostic["z_weather"].notna()
    ]
    gradient = routed_score_gradient(
        gradient_frame,
        draws=int(config["bootstrap_draws"]),
        seed=int(config["random_seed"]),
    )
    all_source_gradient_frame = all_source_sensitivity[
        all_source_sensitivity["supported_city"]
        & all_source_sensitivity["routing_indicator"].eq(1)
        & all_source_sensitivity["z_weather"].notna()
    ]
    all_source_gradient = routed_score_gradient(
        all_source_gradient_frame,
        draws=int(config["bootstrap_draws"]),
        seed=int(config["random_seed"]),
    )
    sensitivity_sign_change = bool(
        math.copysign(1.0, gradient["S"])
        != math.copysign(1.0, all_source_gradient["S"])
    )
    gradient_abs_sum = float(
        sum(abs(float(item["S_date"])) for item in gradient["by_date"])
    )
    gradient_max_abs_date_share = (
        0.0
        if gradient_abs_sum == 0.0
        else float(
            max(abs(float(item["S_date"])) for item in gradient["by_date"])
            / gradient_abs_sum
        )
    )
    authorized, fit_blockers, nonblocking = fit_authorization(
        policy=fit_policy,
        coverage=coverage,
        truth=truth,
        foundation=foundation,
        gradient=gradient,
        sensitivity_sign_change=sensitivity_sign_change,
        gradient_max_abs_date_share=gradient_max_abs_date_share,
    )

    fold_gate = config["fit_gate"]
    prior_dates_min = int(fold_gate["prior_target_dates_min"])
    prior_negative_min = int(
        fold_gate["prior_independent_negative_city_date_episodes_min"]
    )
    both_cities_required = bool(fold_gate["both_supported_cities_required"])
    predictions, folds = fit_or_fallback(
        diagnostic,
        authorized=authorized,
        prior_target_dates_min=prior_dates_min,
        prior_negative_episodes_min=prior_negative_min,
        both_supported_cities_required=both_cities_required,
    )
    final_refit = final_refit_summary(
        diagnostic,
        authorized=authorized,
        prior_target_dates_min=prior_dates_min,
        prior_negative_episodes_min=prior_negative_min,
        both_supported_cities_required=both_cities_required,
    )
    evaluation = build_evaluation(predictions)
    comparisons = comparison_scopes(
        predictions,
        draws=int(config["bootstrap_draws"]),
        seed=int(config["random_seed"]),
    )
    trade_summary, trade_rows, selected = trade_readout(
        args.trade_funnel, predictions
    )
    breakdowns = selected_trade_breakdowns(selected)
    overlap = incumbent_route_overlap(selected)

    fitted_comparison = comparisons["FITTED_ACTIVE_ROUTE"][
        "V2_2_MINUS_RAW_MARKET"
    ]
    if fitted_comparison.get("target_dates", 0) < 10:
        nonblocking.append("FITTED_ACTIVE_ROUTE_BELOW_10_TARGET_DATES")
    if fitted_comparison.get("date_equal_delta_logloss_ci95", [0.0, 0.0])[1] >= 0:
        nonblocking.append("FITTED_ACTIVE_LOGLOSS_CI_CROSSES_ZERO")
    if fitted_comparison.get("date_equal_delta_brier_ci95", [0.0, 0.0])[1] >= 0:
        nonblocking.append("FITTED_ACTIVE_BRIER_CI_CROSSES_ZERO")
    freeze_eligible = bool(
        authorized
        and final_refit.get("status") == "FIT"
        and fitted_comparison.get("target_dates", 0) > 0
        and fitted_comparison.get("date_equal_delta_logloss", 0.0) < 0
        and fitted_comparison.get("date_equal_delta_brier", 0.0) < 0
        and gradient["S"] > 0
        and not sensitivity_sign_change
    )
    freeze_decision = {
        "research_disposition": (
            "FREEZE_V2_2_PROBABILITY_CHALLENGER"
            if freeze_eligible
            else "KEEP_V1_FORWARD_ONLY"
        ),
        "operational_disposition": "KEEP_V1_FORWARD_ONLY",
        "tiny_live_eligible": False,
        "selection_uses_pnl": False,
        "artifact_status": "SPECIFICATION_FROZEN_RUNTIME_NOT_STARTED",
        "evidence_strength": (
            "POINT_ESTIMATE_ONLY_CI_NOT_CLOSED"
            if freeze_eligible
            else "NOT_ELIGIBLE_FOR_RESEARCH_FREEZE"
        ),
        "rationale": (
            "Prior-date OOF fitted rows improve both proper scores pointwise; "
            "freeze is only for prospective zero-notional falsification."
            if freeze_eligible
            else "Fit or point-estimate proper-score requirements failed."
        ),
    }
    forward_manifest = {
        "schema_version": "tmin_v2_2_probability_forward_arm_manifest_v1",
        "evidence_seal_timestamp_utc": config["evidence_seal_timestamp_utc"],
        "forward_start_target_date": config["confirmatory_forward_start_target_date"],
        "development_data_cutoff_target_date": config["data_cutoff_target_date"],
        "no_backfill": True,
        "runtime_status": "PREREGISTERED_NOT_STARTED",
        "runtime_consumer": None,
        "append_only_forward_journal": None,
        "activation_blocker": "ZERO_NOTIONAL_FORWARD_CONSUMER_NOT_DEPLOYED",
        "common_probability_denominator": (
            "all probability-eligible settled P0 rows; never execution-filtered"
        ),
        "arms": [
            {"arm": "RAW_MARKET", "role": "baseline"},
            {
                "arm": "V1_ALPHA010_ROUTED",
                "role": "existing_frozen_comparator",
            },
            {
                "arm": MODEL_ID,
                "role": "new_probability_only_challenger",
                "route": ["morning_cooling", "post_sunrise_provisional_low"],
                "supported_cities": list(SUPPORTED_CITIES),
                "outside_route": "byte-equal RAW_MARKET",
                "selector_or_order_generation": False,
                "initial_posterior": final_refit,
            },
        ],
        "readout_gate": config.get("forward_readout_gate"),
        "promotion_not_authorized": True,
        "live_authorization": False,
    }
    seal_local_date = pd.Timestamp(
        forward_manifest["evidence_seal_timestamp_utc"]
    ).tz_convert("Asia/Seoul").date()
    forward_date = pd.Timestamp(
        forward_manifest["forward_start_target_date"]
    ).date()
    if forward_date <= seal_local_date:
        raise AssertionError("forward start must be after the local evidence-seal date")

    settled_trade = trade_rows[trade_rows["settled_binary"].eq(True)]
    result = {
        "schema_version": "tmin_v2_2_frozen_candidate_readout_v1",
        "model_id": MODEL_ID,
        "research_only": True,
        "zero_notional": True,
        "live_authorization": False,
        "selector_or_execution_changed": False,
        "fit_authorization_policy": fit_policy,
        "forecast_gate": coverage,
        "forecast_gate_all_source_rows": coverage_all,
        "forecast_gate_primary_truth_excluded": coverage_primary,
        "training_truth_exclusion": training_truth_exclusion,
        "truth_gate": truth,
        "unresolved_mismatch_keys_excluded_from_primary_training": [
            list(item) for item in sorted(mismatch_keys)
        ],
        "foundation_diagnostic": foundation,
        "foundation_all_source_rows_sensitivity": foundation_all_source,
        "score_gradient": gradient,
        "score_gradient_all_source_rows_sensitivity": all_source_gradient,
        "source_sensitivity_changes_gradient_sign": sensitivity_sign_change,
        "gradient_max_absolute_single_date_share": gradient_max_abs_date_share,
        "gradient_lower_bound_above_zero": bool(
            gradient["one_sided_lower_95"] > 0
        ),
        "primary_fit_authorized": authorized,
        "fit_blockers": fit_blockers,
        "nonblocking_diagnostics": sorted(set(nonblocking)),
        "posterior_folds": folds,
        "final_refit": final_refit,
        "probability_metrics": model_metrics(predictions),
        "comparison_scopes": comparisons,
        "evaluation": evaluation,
        "trade_readout": trade_summary,
        "selected_trade_breakdowns": breakdowns,
        "v1_incumbent_route_overlap": overlap,
        "historical_denominator": {
            "start_date": str(settled_trade["target_date"].min()),
            "end_date": str(settled_trade["target_date"].max()),
            "rows": int(len(settled_trade)),
            "target_dates": int(settled_trade["target_date"].nunique()),
            "p1_rows": int(predictions["routing_indicator"].eq(1).sum()),
            "supported_p1_rows": int(
                (
                    predictions["supported_city"]
                    & predictions["routing_indicator"].eq(1)
                ).sum()
            ),
            "p1_outside_rows": int(predictions["routing_indicator"].eq(0).sum()),
        },
        "stop_or_block_reasons": fit_blockers,
        "freeze_decision": freeze_decision,
        "forward_arm_manifest": forward_manifest,
        "disposition": freeze_decision["research_disposition"],
        "operational_disposition": "KEEP_V1_FORWARD_ONLY",
    }

    predictions.to_parquet(args.output_dir / "MODEL_PREDICTIONS.parquet", index=False)
    diagnostic.to_parquet(
        args.output_dir / "FOUNDATION_DIAGNOSTIC_ROWS.parquet", index=False
    )
    trade_rows.to_csv(args.output_dir / "ROW_LEVEL_TRADE_FUNNEL.csv", index=False)
    selected.to_csv(args.output_dir / "SELECTED_TRADES.csv", index=False)
    write_json(args.output_dir / "V22_RESEARCH_READOUT.json", result)
    write_json(args.output_dir / "FROZEN_MODEL_ARTIFACT.json", final_refit)
    write_json(args.output_dir / "FORWARD_ARM_MANIFEST.json", forward_manifest)
    (args.output_dir / "REPORT.md").write_text(render_report(result), encoding="utf-8")
    review_packet = f"""# GPT Pro review packet — Tmin V2.2 fitted frozen candidate

Review only whether `{MODEL_ID}` should remain a probability-only,
zero-notional frozen challenger. Do not recommend live, size, selector,
threshold, price-cap, or execution changes.

Authoritative results are `REPORT.md` and `V22_RESEARCH_READOUT.json`. Primary
forecast-error training uses only exact reconciled city-days; four
row-level-audited Seoul source-truth disputes and every other non-exact or
unreconciled city-day are excluded, while the all-source sensitivity is
retained. Fixed V1 checkpoint, market probability, settlement label, direct
ask, and selector evidence are reused unchanged.

Allowed decisions:

- `FREEZE_V2_2_PROBABILITY_CHALLENGER`
- `KEEP_V1_FORWARD_ONLY`
- `STOP_WEATHER_RESIDUAL_RESEARCH_UNDER_CURRENT_CONTRACT`

Current internal decision: `{freeze_decision['research_disposition']}`;
artifact status: `{freeze_decision['artifact_status']}`; forward runtime is
`{forward_manifest['runtime_status']}` and has not started. Operational state:
`KEEP_V1_FORWARD_ONLY`; `tiny_live_eligible=false`.
"""
    (args.output_dir / "GPT_PRO_REVIEW_PACKET.md").write_text(
        review_packet, encoding="utf-8"
    )
    research_record = {
        "schema_version": "pm_agents_research_record_v1",
        "record_id": (
            "research:weather:tmin_no_further_cooling:"
            "v2_2_fitted_frozen_candidate_20260829"
        ),
        "run_id": "v2_2_fitted_frozen_candidate_20260829",
        "domain": "weather",
        "family": "tmin_no_further_cooling",
        "skill": "weather-strategy-research",
        "lifecycle_status": "complete",
        "observed_at_utc": config["evidence_seal_timestamp_utc"],
        "question": {
            "hypothesis": (
                "A PIT forecast-threshold residual adds probability information "
                "to the same-checkpoint raw market on the frozen Seoul/Tokyo route."
            ),
            "decision_target": (
                "Whether to freeze V2.2 as a probability-only zero-notional "
                "challenger for prospective falsification."
            ),
            "scope": (
                "V1 P0 fixed checkpoints from 2026-08-12 through 2026-08-26; "
                "Seoul/Tokyo 06:00 and 09:00 active route; forecast error history "
                "2026-04-15 through 2026-08-20 exact-truth slice."
            ),
            "exclusions": [
                "live or tiny-live authorization",
                "selector threshold price-cap size or execution changes",
                "PnL-driven model selection",
                "target dates on or after 2026-08-28 for development",
            ],
        },
        "method": {
            "grain": "fixed PIT checkpoint row; target_date-blocked expanding OOF",
            "denominator_scope": (
                "P0 168 settled probability rows; P1 supported active route 50 "
                "rows; model is market fallback outside supported route."
            ),
            "evidence_layers": [
                "native PIT forecast vintage",
                "settlement-source reconciliation",
                "same-row raw market probability",
                "fixed direct ask and selector replay",
            ],
            "pit_or_asof_policy": (
                "Forecast available_at must be no later than checkpoint; each test "
                "date uses only earlier target dates; non-exact truth is excluded "
                "from primary residual history."
            ),
            "label_contract": (
                "Binary no-further-cooling exact-current-rung settlement label "
                "bound by condition identity."
            ),
            "primary_metrics": [
                "date-equal logloss delta versus raw market",
                "date-equal Brier delta versus raw market",
                "routed score-gradient at alpha zero",
            ],
            "baselines": [
                "RAW_MARKET",
                "V1_ALPHA010_ROUTED",
                "V1_INCUMBENT_ALPHA050_ALL",
                "clock probability",
            ],
            "forward_policy": (
                "No backfill; probability-only common denominator begins "
                "2026-08-31 after the evidence seal."
            ),
            "acceptance_gates": [
                "forecast foundation beats clock",
                "audited non-exact truth excluded from primary training",
                "prior dates at least 10 and negative city-date episodes at least 5",
                "both supported cities present",
                "fitted OOF proper-score point deltas both below zero for research freeze",
            ],
            "fee_and_execution_basis": (
                "Frozen V1 direct-ask 5-share fee replay for business readout only; "
                "not used for fit or freeze selection."
            ),
        },
        "inputs": [
            {
                "input_id": "p0_fixed",
                "kind": "parquet",
                "locator": "artifact://tmin-v2-2-fitted-candidate/frozen_inputs/P0_RAW_FEATURE_AUDIT.parquet",
                "identity": f"sha256:{sha256(args.p0_raw)}",
                "coverage": "168 rows, 15 target dates, 2026-08-12 through 2026-08-26",
                "observed_at_utc": config["evidence_seal_timestamp_utc"],
            },
            {
                "input_id": "forecast_archive",
                "kind": "parquet",
                "locator": "artifact://tmin-v2-2-fitted-candidate/frozen_inputs/FORECAST_ERROR_ARCHIVE_00.parquet",
                "identity": "sha256:" + "+".join(sha256(path) for path in args.forecast_archive),
                "coverage": "native PIT forecast vintages 2026-04-15 through 2026-08-26",
                "observed_at_utc": config["evidence_seal_timestamp_utc"],
            },
            {
                "input_id": "settlement_truth",
                "kind": "parquet",
                "locator": "artifact://tmin-v2-2-fitted-candidate/frozen_inputs/SETTLEMENT_SOURCE_RECONCILIATION.parquet",
                "identity": f"sha256:{sha256(args.reconciliation)}",
                "coverage": "226 exact, 4 audited mismatch, 3 exchange-rung unresolved city-days",
                "observed_at_utc": config["evidence_seal_timestamp_utc"],
            },
            {
                "input_id": "selector_replay",
                "kind": "csv",
                "locator": "artifact://tmin-v2-2-fitted-candidate/frozen_inputs/ROW_LEVEL_TRADE_FUNNEL.csv",
                "identity": f"sha256:{sha256(args.trade_funnel)}",
                "coverage": "fixed V1 checkpoint quote, fee, and selector evidence",
                "observed_at_utc": config["evidence_seal_timestamp_utc"],
            },
        ],
        "execution": {
            "producer": "scripts/analysis/tmin/research_tmin_v2_2_readout_v1.py",
            "code_identity": f"sha256:{sha256(Path(__file__))}",
            "config_locator": (
                "config/research/"
                "tmin_v2_2_forecast_threshold_residual_v1_frozen_candidate.json"
            ),
            "config_identity": f"sha256:{sha256(args.config)}",
            "reproduce_command": "REPRODUCE.sh EMPTY_OUTPUT_DIRECTORY",
        },
        "outputs": {
            "artifact_root_contract": "artifact://tmin-v2-2-fitted-candidate",
            "artifact_manifest": (
                "artifact://tmin-v2-2-fitted-candidate/EVIDENCE_MANIFEST.json"
            ),
            "canonical_machine_format": "parquet",
            "compact_summary_locator": (
                "artifact://tmin-v2-2-fitted-candidate/REPORT.md"
            ),
        },
        "knowledge": {
            "family_living_doc": "docs/WEATHER_TMIN_DISTRIBUTION_EDGE_STRATEGY.md",
            "registry_or_index": "docs/WEATHER_STRATEGY_REGISTRY.md",
            "dated_snapshot": (
                "docs/analysis/2026-08/"
                "2026-08-30-tmin-v2-2-fitted-frozen-candidate-v1.md"
            ),
            "durable_conclusion": (
                "V2.2 is fit-capable and has point-estimate OOF improvement on "
                "16 active-route rows across 4 dates, but uncertainty is not closed."
            ),
            "action": (
                "Freeze the V2.2 probability specification and artifact; 2026-08-31 "
                "is the earliest no-backfill start, but the forward runtime has not "
                "been deployed. Keep V1 as the only operational forward incumbent."
            ),
            "superseded_record_ids": [],
        },
    }
    write_json(args.output_dir / "research_record.json", research_record)

    source_files = [
        Path(__file__),
        ROOT / "scripts/analysis/tmin/tmin_v2_2_forecast_threshold_residual_v1.py",
        ROOT / "scripts/analysis/tmin/tmin_v2_1_v3_strategy_readout_v1.py",
        ROOT / "scripts/analysis/tmin/materialize_tmin_v2_2_noaa_gfs_archive_v1.py",
        ROOT / "tests/research_tests/test_research_tmin_v2_2_readout_v1.py",
        ROOT
        / "tests/research_tests/test_tmin_v2_2_forecast_threshold_residual_v1.py",
        ROOT
        / "tests/research_tests/test_materialize_tmin_v2_2_noaa_gfs_archive_v1.py",
    ]
    portable, source_snapshots = materialize_portable_package_inputs(
        args.output_dir, args, source_files
    )
    forecast_args = " \\\n".join(
        f'  --forecast-archive "$SCRIPT_DIR/frozen_inputs/{path.name}"'
        for path in portable["forecast_archives"]
    )
    reproduce = f"""#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
PYTHON="${{PYTHON:-python3}}"
if [[ -x "$SCRIPT_DIR/../../.venv/bin/python" ]]; then
  PYTHON="$SCRIPT_DIR/../../.venv/bin/python"
fi
"$PYTHON" -B "$SCRIPT_DIR/source_snapshots/scripts/analysis/tmin/research_tmin_v2_2_readout_v1.py" \
  --config "$SCRIPT_DIR/frozen_inputs/config.json" \
{forecast_args} \
  --p0-raw "$SCRIPT_DIR/frozen_inputs/P0_RAW_FEATURE_AUDIT.parquet" \
  --probability-audit "$SCRIPT_DIR/frozen_inputs/ROW_LEVEL_PROBABILITY_AUDIT.parquet" \
  --reconciliation "$SCRIPT_DIR/frozen_inputs/SETTLEMENT_SOURCE_RECONCILIATION.parquet" \
  --wu-disputes-root "$SCRIPT_DIR/frozen_inputs/wu_disputes" \
  --trade-funnel "$SCRIPT_DIR/frozen_inputs/ROW_LEVEL_TRADE_FUNNEL.csv" \
  --output-dir "${{1:?empty output directory required}}"
"""
    reproduce_path = args.output_dir / "REPRODUCE.sh"
    reproduce_path.write_text(reproduce, encoding="utf-8")
    reproduce_path.chmod(0o755)
    environment_path = args.output_dir / "ENVIRONMENT_LOCK.json"
    packages = {}
    for package in ("numpy", "pandas", "pyarrow", "scipy"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    write_json(
        environment_path,
        {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "packages": packages,
        },
    )
    outputs = [
        args.output_dir / name
        for name in (
            "MODEL_PREDICTIONS.parquet",
            "FOUNDATION_DIAGNOSTIC_ROWS.parquet",
            "ROW_LEVEL_TRADE_FUNNEL.csv",
            "SELECTED_TRADES.csv",
            "V22_RESEARCH_READOUT.json",
            "FROZEN_MODEL_ARTIFACT.json",
            "FORWARD_ARM_MANIFEST.json",
            "REPORT.md",
            "GPT_PRO_REVIEW_PACKET.md",
            "REPRODUCE.sh",
            "ENVIRONMENT_LOCK.json",
            "research_record.json",
        )
    ]
    try:
        code_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
    except Exception:
        code_sha = None
    manifest = {
        "code_sha": code_sha,
        "code_sha_note": (
            "HEAD is informational; package-local source snapshot hashes are "
            "authoritative for a dirty or untracked worktree."
        ),
        "source_snapshot_authoritative": True,
        "runner": (
            "source_snapshots/scripts/analysis/tmin/"
            "research_tmin_v2_2_readout_v1.py"
        ),
        "runner_sha256": sha256(source_snapshots[0]),
        "source_files": tree_manifest(
            args.output_dir / "source_snapshots", args.output_dir
        ),
        "inputs": tree_manifest(args.output_dir / "frozen_inputs", args.output_dir),
        "outputs": [
            {
                "path": path.name,
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in outputs
        ],
    }
    write_json(args.output_dir / "EVIDENCE_MANIFEST.json", manifest)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    base = ROOT / "reviews/tmin_model_layer_v2_1_v3_research_v1"
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config/research/tmin_v2_2_forecast_threshold_residual_v1.json",
    )
    parser.add_argument("--forecast-archive", type=Path, action="append", required=True)
    parser.add_argument(
        "--p0-raw",
        type=Path,
        default=ROOT
        / "reviews/tmin_v2_2_forecast_threshold_residual_v1_r2/P0_RAW_FEATURE_AUDIT.parquet",
    )
    parser.add_argument(
        "--probability-audit",
        type=Path,
        default=base / "ROW_LEVEL_PROBABILITY_AUDIT.parquet",
    )
    parser.add_argument(
        "--reconciliation",
        type=Path,
        default=base / "SETTLEMENT_SOURCE_RECONCILIATION.parquet",
    )
    parser.add_argument(
        "--wu-disputes-root",
        type=Path,
        default=base / "frozen_inputs/wu_disputes",
    )
    parser.add_argument(
        "--trade-funnel",
        type=Path,
        default=base / "frozen_inputs/base/ROW_LEVEL_TRADE_FUNNEL.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reviews/tmin_v2_2_lightweight_research_readout_v1",
    )
    return parser.parse_args(argv)


def main() -> int:
    build(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
