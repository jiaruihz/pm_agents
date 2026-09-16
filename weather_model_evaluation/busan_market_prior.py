"""Busan exact-NO market-prior expression and fixed-denominator evaluator.

The physical Busan model remains a weather-only probability head.  This
module adds a separate executable expression head that treats the contemporaneous
PIT market probability as a prior and applies a bounded weather correction in
logit space.  It never trains on settlement labels at scoring time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from .probability import (
    binary_loss_values,
    binary_score,
    date_block_bootstrap_delta,
)


SCHEMA_VERSION = "busan_market_prior_expression_v1"
RUNTIME_ARTIFACT_SCHEMA_VERSION = "busan_online_market_prior_artifact_v1"
MODEL_ID = "busan_intraday_exact_no_market_prior_residual"
ONLINE_MODEL_ID = "busan_intraday_exact_no_online_market_prior_residual"
DEFAULT_WEATHER_WEIGHT = 0.125
DEFAULT_WEATHER_COLUMN = "p_factorized_random_forest_full_weather"
EPSILON = 1e-5
REQUIRED_COLUMNS = {
    "city",
    "target_date",
    "decision_ts_utc",
    "checkpoint_id",
    "target_id",
    "label_no",
    "market_p_no",
    "no_ask",
    "no_ask_size",
    "no_book_ts_utc",
    "routine_running_max_market_value",
}
CONFIRMATION_FEATURES = (
    "source_margin_to_rung_c",
    "source_running_margin_to_rung_c",
    "distance_below_source_running_max_c",
    "minutes_since_source_running_max",
    "current_cross_retained",
    "observation_history_count",
    "minutes_to_next_routine",
    "local_hour",
    "path_15m_slope_c_per_hour",
    "path_60m_slope_c_per_hour",
    "hours_to_forecast_peak",
    "forecast_ceiling_margin_c",
    "relative_humidity_pct",
    "dewpoint_depression_c",
    "forecast_cloud_cover_remaining_3h_mean_pct",
    "forecast_precip_probability_remaining_3h_max_pct",
    "forecast_wind_speed_remaining_3h_max_kt",
    "physical_prior_logit",
)


@dataclass(frozen=True)
class BusanMarketPriorEvaluation:
    predictions: pd.DataFrame
    trades: pd.DataFrame
    summary: dict[str, Any]


@dataclass(frozen=True)
class BusanOnlineMarketPriorEvaluation:
    predictions: pd.DataFrame
    trades: pd.DataFrame
    weight_history: pd.DataFrame
    summary: dict[str, Any]


def _date_equal_sample_weight(frame: pd.DataFrame) -> np.ndarray:
    return (
        1.0 / frame.groupby("target_date")["target_date"].transform("size")
    ).to_numpy(float)


def _physical_feature_frame(
    frame: pd.DataFrame,
    feature_names: Iterable[str],
) -> pd.DataFrame:
    decision = pd.to_datetime(
        frame["decision_ts_utc"], utc=True, errors="raise", format="mixed"
    )
    local = decision.dt.tz_convert("Asia/Seoul")
    day = local.dt.dayofyear
    values = {
        "local_hour": local.dt.hour.astype(float),
        "running_max_market_value": frame[
            "routine_running_max_market_value"
        ].astype(float),
        "day_of_year_sin": np.sin(2.0 * np.pi * day / 366.0),
        "day_of_year_cos": np.cos(2.0 * np.pi * day / 366.0),
    }
    missing = sorted(set(feature_names) - set(values))
    if missing:
        raise ValueError(f"unsupported Busan physical features: {missing}")
    return pd.DataFrame({name: values[name] for name in feature_names})


def build_busan_runtime_artifact(
    state_frame: pd.DataFrame,
    frozen_forward_frame: pd.DataFrame,
    physical_artifact: dict[str, Any],
    *,
    confirmation_train_end: str = "2026-08-03",
    expression_weight: float = 0.25,
    expression_weight_trained_through: str = "2026-08-11",
    parity_tolerance: float = 1e-12,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit and parity-lock the historical Busan physical probability stack.

    The confirmation head is reconstructed from the exact frozen tournament
    contract.  The function refuses to produce a runtime artifact unless both
    the confirmation and composed weather probabilities reproduce the archived
    forward rows within numerical tolerance.
    """

    required_state = {
        "target_date",
        "label_next_routine_confirms",
        *CONFIRMATION_FEATURES,
    }
    required_forward = {
        "target_date",
        "decision_ts_utc",
        "routine_running_max_market_value",
        "p_confirm_random_forest_full_weather",
        "p_factorized_random_forest_full_weather",
        *CONFIRMATION_FEATURES,
    }
    missing_state = sorted(required_state - set(state_frame.columns))
    missing_forward = sorted(required_forward - set(frozen_forward_frame.columns))
    if missing_state or missing_forward:
        raise ValueError(
            "Busan runtime artifact inputs are incomplete: "
            f"state={missing_state} forward={missing_forward}"
        )
    if not 0.0 <= float(expression_weight) <= 1.0:
        raise ValueError("expression_weight must be in [0, 1]")
    if physical_artifact.get("city") != "Busan":
        raise ValueError("physical artifact city must be Busan")
    physical_model = physical_artifact.get("physical_model")
    physical_features = tuple(physical_artifact.get("physical_features") or ())
    if physical_model is None or not physical_features:
        raise ValueError("physical artifact is missing model/features")

    train = state_frame.loc[
        state_frame["target_date"].astype(str).le(confirmation_train_end)
        & state_frame["label_next_routine_confirms"].notna()
    ].copy()
    if train.empty:
        raise ValueError("Busan confirmation training slice is empty")
    confirmation_model = Pipeline(
        [
            (
                "impute",
                SimpleImputer(
                    strategy="median", keep_empty_features=True, add_indicator=True
                ),
            ),
            (
                "model",
                RandomForestClassifier(
                    n_estimators=400,
                    max_depth=3,
                    min_samples_leaf=8,
                    max_features=0.7,
                    random_state=8404,
                    n_jobs=1,
                ),
            ),
        ]
    )
    confirmation_model.fit(
        train[list(CONFIRMATION_FEATURES)],
        train["label_next_routine_confirms"].astype(float),
        model__sample_weight=_date_equal_sample_weight(train),
    )

    validation = frozen_forward_frame.copy()
    confirmation_probability = confirmation_model.predict_proba(
        validation[list(CONFIRMATION_FEATURES)]
    )[:, 1]
    physical_probability = physical_model.predict_proba(
        _physical_feature_frame(validation, physical_features)
    )[:, 1]
    composed_probability = confirmation_probability + (
        1.0 - confirmation_probability
    ) * physical_probability
    confirmation_error = np.abs(
        confirmation_probability
        - validation["p_confirm_random_forest_full_weather"].to_numpy(float)
    )
    composed_error = np.abs(
        composed_probability
        - validation["p_factorized_random_forest_full_weather"].to_numpy(float)
    )
    parity = {
        "rows": int(len(validation)),
        "target_dates": int(validation["target_date"].nunique()),
        "date_start": str(validation["target_date"].astype(str).min()),
        "date_end": str(validation["target_date"].astype(str).max()),
        "confirmation_max_abs_error": float(confirmation_error.max()),
        "composed_max_abs_error": float(composed_error.max()),
        "tolerance": float(parity_tolerance),
        "pass": bool(
            confirmation_error.max() <= parity_tolerance
            and composed_error.max() <= parity_tolerance
        ),
    }
    if not parity["pass"]:
        raise RuntimeError(f"Busan runtime artifact parity failed: {parity}")

    artifact = {
        "schema_version": RUNTIME_ARTIFACT_SCHEMA_VERSION,
        "model_id": ONLINE_MODEL_ID,
        "online_adapter": "busan_online_market_prior_v1",
        "city": "Busan",
        "target": "final exact-rung NO settlement probability",
        "confirmation_model": confirmation_model,
        "confirmation_features": list(CONFIRMATION_FEATURES),
        "confirmation_train_start": str(train["target_date"].astype(str).min()),
        "confirmation_train_end": str(train["target_date"].astype(str).max()),
        "confirmation_train_dates": int(train["target_date"].nunique()),
        "confirmation_train_rows": int(len(train)),
        "physical_model": physical_model,
        "physical_features": list(physical_features),
        "physical_train_end": str(physical_artifact.get("artifact_train_end")),
        "expression_weight": float(expression_weight),
        "expression_weight_trained_through": expression_weight_trained_through,
        "market_feature_role": "prior_offset",
        "market_feature_clock": "decision_current",
        "candidate_grain_version": "busan_pending_confirmation_state_v1",
        "parity": parity,
    }
    return artifact, parity


def dump_busan_runtime_artifact(
    artifact: dict[str, Any],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, path)


def _clip_probability(value: np.ndarray | Iterable[float] | float) -> np.ndarray:
    probability = np.asarray(value, dtype=float)
    if not np.isfinite(probability).all():
        raise ValueError("probabilities must be finite")
    if ((probability < 0.0) | (probability > 1.0)).any():
        raise ValueError("probabilities must be in [0, 1]")
    return np.clip(probability, EPSILON, 1.0 - EPSILON)


def logit_shrunk_probability(
    market_probability: np.ndarray | Iterable[float] | float,
    weather_probability: np.ndarray | Iterable[float] | float,
    *,
    weather_weight: float = DEFAULT_WEATHER_WEIGHT,
) -> np.ndarray:
    """Return market prior plus a bounded weather innovation in logit space."""

    weight = float(weather_weight)
    if not 0.0 <= weight <= 1.0:
        raise ValueError("weather_weight must be in [0, 1]")
    market = _clip_probability(market_probability)
    weather = _clip_probability(weather_probability)
    # Preserve the two nested-model endpoints exactly.  Besides making the
    # contract explicit, this prevents floating-point round trips through
    # logit/expit from turning a true zero candidate-minus-market delta into a
    # tiny negative value that can be mistaken for statistical evidence.
    if weight == 0.0:
        return market.copy()
    if weight == 1.0:
        return weather.copy()
    market_logit = np.log(market / (1.0 - market))
    weather_logit = np.log(weather / (1.0 - weather))
    posterior_logit = market_logit + weight * (weather_logit - market_logit)
    return 1.0 / (1.0 + np.exp(-posterior_logit))


def load_prediction_frame(path: Path) -> pd.DataFrame:
    """Load a CSV artifact even when its content-addressed path has no suffix."""

    with path.open("rb") as handle:
        magic = handle.read(2)
    compression = "gzip" if magic == b"\x1f\x8b" else None
    return pd.read_csv(path, compression=compression, low_memory=False)


def _prepare_same_market_rows(
    frame: pd.DataFrame,
    *,
    weather_column: str,
) -> tuple[pd.DataFrame, dict[str, int]]:
    missing = sorted((REQUIRED_COLUMNS | {weather_column}) - set(frame.columns))
    if missing:
        raise ValueError(f"missing Busan expression columns: {missing}")
    work = frame.copy()
    city = work["city"].astype(str).str.casefold().eq("busan")
    target = work["target_id"].astype(str).str.match(r"^final_exact_.+_NO$")
    work["decision_ts_utc"] = pd.to_datetime(
        work["decision_ts_utc"], utc=True, errors="coerce", format="mixed"
    )
    work["no_book_ts_utc"] = pd.to_datetime(
        work["no_book_ts_utc"], utc=True, errors="coerce", format="mixed"
    )
    for column in (
        weather_column,
        "market_p_no",
        "label_no",
        "no_ask",
        "no_ask_size",
    ):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    identity = (
        city
        & target
        & work["checkpoint_id"].notna()
        & work["checkpoint_id"].astype(str).str.strip().ne("")
    )
    clocks = work["decision_ts_utc"].notna() & work["no_book_ts_utc"].notna()
    causal = clocks & work["no_book_ts_utc"].ge(work["decision_ts_utc"])
    settled = work["label_no"].isin([0.0, 1.0])
    probabilities = work[weather_column].between(
        0.0, 1.0, inclusive="both"
    ) & work["market_p_no"].between(0.0, 1.0, inclusive="both")
    same_market = identity & causal & settled & probabilities
    executable = (
        same_market
        & work["no_ask"].between(0.0, 1.0, inclusive="both")
        & work["no_ask_size"].gt(0.0)
    )
    counts = {
        "input_rows": int(len(work)),
        "busan_exact_no_identity_rows": int(identity.sum()),
        "clock_complete_rows": int((identity & clocks).sum()),
        "causal_book_rows": int((identity & causal).sum()),
        "settled_rows": int((identity & causal & settled).sum()),
        "same_market_probability_rows": int(same_market.sum()),
        "executable_ask_rows": int(executable.sum()),
        "noncausal_book_rows": int((identity & clocks & ~causal).sum()),
    }
    selected = work.loc[same_market].copy()
    selected["executable_ask"] = executable.loc[selected.index].to_numpy()
    selected["label_no"] = selected["label_no"].astype(int)
    selected["target_date"] = selected["target_date"].astype(str)
    return selected.sort_values(
        ["target_date", "decision_ts_utc", "checkpoint_id"], kind="stable"
    ).reset_index(drop=True), counts


def _score(
    frame: pd.DataFrame,
    probability_column: str,
) -> dict[str, float | int]:
    return binary_score(
        frame,
        frame[probability_column].to_numpy(float),
        label_column="label_no",
    )


def _paired_delta(
    frame: pd.DataFrame,
    candidate_column: str,
    baseline_column: str,
    *,
    metric: str,
    draws: int,
    seed: int,
) -> dict[str, float | int]:
    label = frame["label_no"].to_numpy(float)
    return date_block_bootstrap_delta(
        frame,
        binary_loss_values(label, frame[candidate_column], metric=metric),
        binary_loss_values(label, frame[baseline_column], metric=metric),
        draws=draws,
        seed=seed,
    )


def _weather_fee_per_share(price: float) -> float:
    return round(0.05 * price * (1.0 - price), 5)


def replay_first_positive_edge(
    predictions: pd.DataFrame,
    *,
    probability_column: str = "posterior_no_probability",
    minimum_edge: float = 0.0,
) -> pd.DataFrame:
    """Replay first fee-positive taker entry per target-date/routine rung."""

    if not np.isfinite(minimum_edge) or minimum_edge < 0.0:
        raise ValueError("minimum_edge must be finite and non-negative")
    rows = predictions.loc[predictions["executable_ask"]].copy()
    rows["fee_per_share"] = rows["no_ask"].map(_weather_fee_per_share)
    rows["effective_entry_price"] = rows["no_ask"] + rows["fee_per_share"]
    rows["edge"] = rows[probability_column] - rows["effective_entry_price"]
    rows = rows.loc[rows["edge"] > minimum_edge].copy()
    rows = rows.sort_values(
        ["target_date", "decision_ts_utc", "checkpoint_id"], kind="stable"
    ).drop_duplicates(
        ["target_date", "routine_running_max_market_value"], keep="first"
    )
    rows["shares"] = np.minimum(5.0, rows["no_ask_size"])
    rows["cost_usd"] = rows["shares"] * rows["effective_entry_price"]
    rows["pnl_usd"] = rows["shares"] * rows["label_no"] - rows["cost_usd"]
    return rows.reset_index(drop=True)


def summarize_trade_replay(
    trades: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    if trades.empty:
        return {
            "orders": 0,
            "target_dates": 0,
            "wins": 0,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "roi": None,
            "roi_ci95": None,
        }
    daily = trades.groupby("target_date", sort=True)[["pnl_usd", "cost_usd"]].sum()
    values = daily.to_numpy(float)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(values), size=(draws, len(values)))
    sampled = values[indices].sum(axis=1)
    roi_draws = sampled[:, 0] / sampled[:, 1]
    cost = float(trades["cost_usd"].sum())
    pnl = float(trades["pnl_usd"].sum())
    return {
        "orders": int(len(trades)),
        "target_dates": int(trades["target_date"].nunique()),
        "wins": int(trades["label_no"].sum()),
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost,
        "roi_ci95": [float(value) for value in np.quantile(roi_draws, [0.025, 0.975])],
    }


def evaluate_busan_market_prior(
    frame: pd.DataFrame,
    *,
    weather_column: str = DEFAULT_WEATHER_COLUMN,
    weather_weight: float = DEFAULT_WEATHER_WEIGHT,
    bootstrap_draws: int = 10_000,
    seed: int = 8412,
) -> BusanMarketPriorEvaluation:
    if bootstrap_draws <= 0:
        raise ValueError("bootstrap_draws must be positive")
    predictions, coverage = _prepare_same_market_rows(
        frame, weather_column=weather_column
    )
    if predictions.empty:
        raise ValueError("no same-market Busan rows available for evaluation")
    predictions["weather_no_probability"] = predictions[weather_column].astype(float)
    predictions["posterior_no_probability"] = logit_shrunk_probability(
        predictions["market_p_no"],
        predictions["weather_no_probability"],
        weather_weight=weather_weight,
    )
    predictions["market_feature_role"] = "prior_offset"
    predictions["market_feature_clock"] = "decision_current"
    predictions["expression_model_id"] = MODEL_ID
    predictions["weather_weight"] = float(weather_weight)

    scores = {
        "market": _score(predictions, "market_p_no"),
        "weather_only": _score(predictions, "weather_no_probability"),
        "market_prior_posterior": _score(predictions, "posterior_no_probability"),
    }
    paired = {
        metric: _paired_delta(
            predictions,
            "posterior_no_probability",
            "market_p_no",
            metric=metric,
            draws=bootstrap_draws,
            seed=seed + index,
        )
        for index, metric in enumerate(("logloss", "brier"))
    }
    trades = replay_first_positive_edge(predictions)
    trade_summary = summarize_trade_replay(
        trades, draws=bootstrap_draws, seed=seed + 10
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "city": "Busan",
        "target": "final exact-rung NO settlement probability",
        "formula": (
            "logit(p_post)=logit(p_market)+weather_weight*"
            "(logit(p_weather)-logit(p_market))"
        ),
        "weather_column": weather_column,
        "weather_weight": float(weather_weight),
        "denominator": coverage,
        "date_range": {
            "start": str(predictions["target_date"].min()),
            "end": str(predictions["target_date"].max()),
            "target_dates": int(predictions["target_date"].nunique()),
        },
        "scores": scores,
        "paired_candidate_minus_market": paired,
        "fee_adjusted_taker_replay": trade_summary,
    }
    return BusanMarketPriorEvaluation(predictions, trades, summary)


def evaluate_weight_grid(
    frame: pd.DataFrame,
    *,
    weather_column: str = DEFAULT_WEATHER_COLUMN,
    weights: Iterable[float] = (0.0, 0.125, 0.25, 0.375, 0.5, 1.0),
    bootstrap_draws: int = 10_000,
    seed: int = 8412,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for index, weight in enumerate(weights):
        result = evaluate_busan_market_prior(
            frame,
            weather_column=weather_column,
            weather_weight=float(weight),
            bootstrap_draws=bootstrap_draws,
            seed=seed + 100 * index,
        )
        posterior = result.summary["scores"]["market_prior_posterior"]
        delta = result.summary["paired_candidate_minus_market"]
        trades = result.summary["fee_adjusted_taker_replay"]
        rows.append(
            {
                "weather_weight": float(weight),
                "rows": posterior["rows"],
                "target_dates": posterior["target_dates"],
                "logloss": posterior["logloss"],
                "brier": posterior["brier"],
                "logloss_delta_vs_market": delta["logloss"]["delta"],
                "logloss_delta_ci_low": delta["logloss"]["ci_low"],
                "logloss_delta_ci_high": delta["logloss"]["ci_high"],
                "brier_delta_vs_market": delta["brier"]["delta"],
                "brier_delta_ci_low": delta["brier"]["ci_low"],
                "brier_delta_ci_high": delta["brier"]["ci_high"],
                "orders": trades["orders"],
                "trade_dates": trades["target_dates"],
                "pnl_usd": trades["pnl_usd"],
                "roi": trades["roi"],
                "roi_ci_low": (
                    trades["roi_ci95"][0] if trades["roi_ci95"] else None
                ),
                "roi_ci_high": (
                    trades["roi_ci95"][1] if trades["roi_ci95"] else None
                ),
            }
        )
    return pd.DataFrame(rows)


def _select_weight(
    history: pd.DataFrame,
    *,
    weights: tuple[float, ...],
    metric: str,
) -> tuple[float, float]:
    if metric not in {"logloss", "brier"}:
        raise ValueError("selection metric must be logloss or brier")
    ranked: list[tuple[float, float]] = []
    for weight in weights:
        probability = logit_shrunk_probability(
            history["market_p_no"],
            history["weather_no_probability"],
            weather_weight=weight,
        )
        score = binary_score(
            history, probability, label_column="label_no"
        )[metric]
        ranked.append((float(score), float(weight)))
    score, weight = min(ranked, key=lambda item: (item[0], item[1]))
    return weight, score


def evaluate_online_busan_market_prior(
    development_frame: pd.DataFrame,
    evaluation_frame: pd.DataFrame,
    *,
    weather_column: str = DEFAULT_WEATHER_COLUMN,
    weights: Iterable[float] = (0.0, 0.125, 0.25, 0.375, 0.5, 1.0),
    selection_metric: str = "logloss",
    bootstrap_draws: int = 10_000,
    seed: int = 8712,
) -> BusanOnlineMarketPriorEvaluation:
    """Expanding-date OOF evaluation of the market/weather shrinkage weight.

    Every evaluation date is scored with a weight selected only from strictly
    earlier settled target dates.  The resulting final weight is the runnable
    state for the next unobserved date.
    """

    weight_grid = tuple(sorted({float(weight) for weight in weights}))
    if not weight_grid:
        raise ValueError("weight grid must not be empty")
    if weight_grid[0] < 0.0 or weight_grid[-1] > 1.0:
        raise ValueError("weight grid must stay in [0, 1]")
    development, development_coverage = _prepare_same_market_rows(
        development_frame, weather_column=weather_column
    )
    evaluation, evaluation_coverage = _prepare_same_market_rows(
        evaluation_frame, weather_column=weather_column
    )
    development["weather_no_probability"] = development[weather_column].astype(float)
    evaluation["weather_no_probability"] = evaluation[weather_column].astype(float)
    if development["target_date"].max() >= evaluation["target_date"].min():
        raise ValueError("development dates must be strictly before evaluation dates")

    history = development.copy()
    outputs: list[pd.DataFrame] = []
    selection_rows: list[dict[str, Any]] = []
    for target_date in sorted(evaluation["target_date"].unique()):
        weight, selection_score = _select_weight(
            history, weights=weight_grid, metric=selection_metric
        )
        day = evaluation.loc[evaluation["target_date"].eq(target_date)].copy()
        day["posterior_no_probability"] = logit_shrunk_probability(
            day["market_p_no"],
            day["weather_no_probability"],
            weather_weight=weight,
        )
        day["selected_weather_weight"] = weight
        day["weight_train_end"] = str(history["target_date"].max())
        day["weight_train_dates"] = int(history["target_date"].nunique())
        day["market_feature_role"] = "prior_offset"
        day["market_feature_clock"] = "decision_current"
        day["expression_model_id"] = ONLINE_MODEL_ID
        outputs.append(day)
        selection_rows.append(
            {
                "test_date": target_date,
                "selected_weather_weight": weight,
                "selection_metric": selection_metric,
                "selection_score": selection_score,
                "train_start": str(history["target_date"].min()),
                "train_end": str(history["target_date"].max()),
                "train_dates": int(history["target_date"].nunique()),
                "train_rows": int(len(history)),
            }
        )
        history = pd.concat([history, day], ignore_index=True)

    predictions = pd.concat(outputs, ignore_index=True)
    weight_history = pd.DataFrame(selection_rows)
    current_weight, current_selection_score = _select_weight(
        history, weights=weight_grid, metric=selection_metric
    )
    scores = {
        "market": _score(predictions, "market_p_no"),
        "weather_only": _score(predictions, "weather_no_probability"),
        "online_market_prior_posterior": _score(
            predictions, "posterior_no_probability"
        ),
    }
    paired = {
        metric: _paired_delta(
            predictions,
            "posterior_no_probability",
            "market_p_no",
            metric=metric,
            draws=bootstrap_draws,
            seed=seed + index,
        )
        for index, metric in enumerate(("logloss", "brier"))
    }
    trades = replay_first_positive_edge(predictions)
    trade_summary = summarize_trade_replay(
        trades, draws=bootstrap_draws, seed=seed + 10
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "model_id": ONLINE_MODEL_ID,
        "city": "Busan",
        "selection_policy": (
            "at each target date choose the lower date-equal prior-settlement "
            f"{selection_metric} weight; ties choose lower weather weight"
        ),
        "weight_grid": list(weight_grid),
        "development_denominator": development_coverage,
        "evaluation_denominator": evaluation_coverage,
        "evaluation_date_range": {
            "start": str(predictions["target_date"].min()),
            "end": str(predictions["target_date"].max()),
            "target_dates": int(predictions["target_date"].nunique()),
        },
        "scores": scores,
        "paired_candidate_minus_market": paired,
        "fee_adjusted_taker_replay": trade_summary,
        "next_date_state": {
            "trained_through": str(history["target_date"].max()),
            "train_dates": int(history["target_date"].nunique()),
            "train_rows": int(len(history)),
            "selected_weather_weight": current_weight,
            "selection_score": current_selection_score,
        },
    }
    return BusanOnlineMarketPriorEvaluation(
        predictions, trades, weight_history, summary
    )


__all__ = [
    "BusanMarketPriorEvaluation",
    "BusanOnlineMarketPriorEvaluation",
    "CONFIRMATION_FEATURES",
    "DEFAULT_WEATHER_COLUMN",
    "DEFAULT_WEATHER_WEIGHT",
    "MODEL_ID",
    "ONLINE_MODEL_ID",
    "RUNTIME_ARTIFACT_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "build_busan_runtime_artifact",
    "dump_busan_runtime_artifact",
    "evaluate_busan_market_prior",
    "evaluate_online_busan_market_prior",
    "evaluate_weight_grid",
    "load_prediction_frame",
    "logit_shrunk_probability",
    "replay_first_positive_edge",
    "summarize_trade_replay",
]
