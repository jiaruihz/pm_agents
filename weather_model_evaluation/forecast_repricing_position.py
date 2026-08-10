"""Position-aware full-ladder policy for D-1 forecast repricing.

This module turns the forecast-repricing research panel into a runnable,
zero-notional position policy.  Entry predicts a rung's 60 minute move
relative to the common ladder move.  The continuation head observes the
complete 30 minute ladder and chooses whether the held rung should be sold at
that checkpoint or held until the 60 minute hard timeout.

The module contains no exchange client and grants no live authority.  Runtime
callers may reuse ``score_runtime_entry`` and ``score_runtime_position`` to
emit standard candidates/intents through the shared execution handoff.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


SCHEMA_VERSION = "forecast_repricing_position_policy_v2"
LEGACY_SCHEMA_VERSION = "forecast_repricing_position_policy_v1"
MODEL_ID = "forecast_repricing_full_ladder_position_v1"
ENTRY_HORIZON_MIN = 60
CONTINUATION_CHECKPOINT_MIN = 30
HARD_EXIT_MIN = 60
COMPLETION_BUFFER_PER_SET = 0.01
COMPLETION_REQUESTED_SHARES = 5.0
EPSILON = 1e-6

IDENTITY_COLUMNS = (
    "forecast_event_id",
    "city",
    "target_date",
    "condition_id",
    "bracket",
)

MARKET_BASELINE_FEATURES = (
    "market_probability_after",
    "signed_mode_distance",
    "left_neighbor_log_ratio",
    "right_neighbor_log_ratio",
    "neighbor_curvature",
    "entry_bid",
    "entry_ask",
    "entry_spread",
    "log_entry_bid_size",
    "log_entry_ask_size",
)

INTERACTION_FEATURES = MARKET_BASELINE_FEATURES + (
    "model_probability_before",
    "model_probability_after",
    "weather_shock",
    "weather_mode_shift",
    "forecast_transport_l1",
    "rung_relative_markout",
    "neighbor_propagation",
    "neighbor_lead_lag",
    "shock_x_mode_distance",
    "shock_x_neighbor_propagation",
    "shock_x_mode_x_neighbor_propagation",
)

MICROSTRUCTURE_FEATURES = INTERACTION_FEATURES + (
    "entry_relative_spread",
    "entry_depth_imbalance",
    "entry_bid_depth_vs_ladder",
    "entry_ask_depth_vs_ladder",
    "rung_spread_vs_ladder",
    "neighbor_spread_mean",
    "neighbor_bid_depth_mean",
    "neighbor_ask_depth_mean",
    "ladder_bid_sum",
    "ladder_ask_sum",
    "ladder_mid_sum",
    "ladder_probability_hhi",
    "ladder_probability_entropy",
    "market_mode_probability",
    "market_mode_margin",
    "absolute_mode_distance",
    "decision_hour_sin",
    "decision_hour_cos",
)

CONTINUATION_FEATURES = INTERACTION_FEATURES + (
    "checkpoint_bid",
    "checkpoint_relative_markout",
    "checkpoint_signed_mode_distance",
    "checkpoint_neighbor_propagation",
    "checkpoint_neighbor_lead_lag",
    "checkpoint_shock_x_mode_distance",
    "checkpoint_shock_x_neighbor_propagation",
    "checkpoint_shock_x_mode_x_neighbor_propagation",
    "entry_predicted_relative_markout",
)


@dataclass(frozen=True)
class RuntimePositionDecision:
    action: str
    reason: str
    predicted_incremental_exit_value: float | None
    observed_relative_markout: float | None
    neighbor_propagation: float | None
    feature_values: Mapping[str, float | None]


@dataclass(frozen=True)
class RuntimePendingOrderDecision:
    action: str
    reason: str
    selected_condition_id: str | None
    predicted_touch_probability: float | None
    predicted_touch_conditional_pnl: float | None
    predicted_fill_adjusted_pnl: float | None


def weather_fee(price: float | pd.Series | np.ndarray) -> Any:
    return 0.05 * price * (1.0 - price)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bracket_order(value: Any) -> float:
    text = str(value or "").strip().lower()
    numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
    if not numbers:
        return math.inf
    parsed = [float(item) for item in numbers]
    center = sum(parsed) / len(parsed)
    if any(token in text for token in ("below", "under", "<=")):
        center -= 0.25
    if any(token in text for token in ("above", "over", "+", ">=")):
        center += 0.25
    return center


def _event_median(frame: pd.DataFrame, column: str) -> pd.Series:
    return frame.groupby("forecast_event_id", sort=False)[column].transform("median")


def _neighbor_mean(frame: pd.DataFrame, column: str) -> pd.Series:
    groups = frame.groupby("forecast_event_id", sort=False)
    return pd.concat([groups[column].shift(1), groups[column].shift(-1)], axis=1).mean(
        axis=1, skipna=True
    )


def _mode_rank(frame: pd.DataFrame, probability: str, output: str) -> pd.DataFrame:
    indexes = (
        frame.groupby("forecast_event_id", sort=False)[probability]
        .idxmax()
        .dropna()
        .astype(int)
    )
    if indexes.empty:
        result = frame.copy()
        result[output] = np.nan
        return result
    modes = frame.loc[indexes, ["forecast_event_id", "ladder_rank"]].rename(
        columns={"ladder_rank": output}
    )
    return frame.merge(modes, on="forecast_event_id", how="left", validate="many_to_one")


def add_full_ladder_position_features(rows: pd.DataFrame) -> pd.DataFrame:
    """Materialize entry and 30-minute continuation features on all rungs."""

    required = {
        "forecast_event_id",
        "city",
        "target_date",
        "condition_id",
        "bracket",
        "model_probability_before",
        "model_probability_after",
        "market_probability_before",
        "market_probability_after",
        "entry_bid",
        "entry_ask",
    }
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"forecast repricing position input missing columns: {missing}")
    frame = rows.copy()
    frame["target_date"] = frame["target_date"].astype(str)
    for column in (
        "model_probability_before",
        "model_probability_after",
        "market_probability_before",
        "market_probability_after",
        "entry_bid",
        "entry_ask",
        "entry_bid_size",
        "entry_ask_size",
        "entry_fee_per_share",
        "decision_hour_local",
        "h30_bid",
        "h60_bid",
        "h30_maker_bid_touch",
        "h60_maker_bid_touch",
        "touch_completion_cost_1tick_per_hedge_leg",
        "touch_completion_margin_1tick_per_hedge_leg",
        "touch_completion_depth_shares",
    ):
        if column not in frame:
            frame[column] = np.nan
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "lead_days" in frame:
        frame = frame.loc[pd.to_numeric(frame["lead_days"], errors="coerce").eq(1)].copy()
    frame["native_order"] = frame["bracket"].map(_bracket_order)
    frame = frame.sort_values(
        ["target_date", "forecast_event_id", "native_order", "condition_id"],
        kind="mergesort",
    ).reset_index(drop=True)
    frame["ladder_rank"] = frame.groupby("forecast_event_id", sort=False).cumcount().astype(float)
    frame = _mode_rank(frame, "market_probability_after", "market_mode_rank")
    frame = _mode_rank(frame, "model_probability_before", "weather_mode_rank_before")
    frame = _mode_rank(frame, "model_probability_after", "weather_mode_rank_after")
    frame["signed_mode_distance"] = frame["ladder_rank"] - frame["market_mode_rank"]
    frame["weather_mode_shift"] = (
        frame["weather_mode_rank_after"] - frame["weather_mode_rank_before"]
    )

    groups = frame.groupby("forecast_event_id", sort=False)
    center = frame["market_probability_after"].clip(lower=0.0)
    left = groups["market_probability_after"].shift(1).clip(lower=0.0)
    right = groups["market_probability_after"].shift(-1).clip(lower=0.0)
    frame["left_neighbor_log_ratio"] = np.log((left + EPSILON) / (center + EPSILON))
    frame["right_neighbor_log_ratio"] = np.log((right + EPSILON) / (center + EPSILON))
    frame["neighbor_curvature"] = (left + right) / 2.0 - center

    frame["weather_shock"] = (
        frame["model_probability_after"] - frame["model_probability_before"]
    )
    market_move = frame["market_probability_after"] - frame["market_probability_before"]
    frame["rung_relative_markout"] = market_move - market_move.groupby(
        frame["forecast_event_id"], sort=False
    ).transform("median")
    frame["neighbor_propagation"] = _neighbor_mean(frame, "rung_relative_markout")
    frame["neighbor_lead_lag"] = (
        frame["neighbor_propagation"] - frame["rung_relative_markout"]
    )
    frame["forecast_transport_l1"] = frame["weather_shock"].abs().groupby(
        frame["forecast_event_id"], sort=False
    ).transform("sum") / 2.0
    frame["shock_x_mode_distance"] = frame["weather_shock"] * frame["signed_mode_distance"]
    frame["shock_x_neighbor_propagation"] = (
        frame["weather_shock"] * frame["neighbor_propagation"]
    )
    frame["shock_x_mode_x_neighbor_propagation"] = (
        frame["weather_shock"]
        * frame["signed_mode_distance"]
        * frame["neighbor_propagation"]
    )
    frame["entry_spread"] = frame["entry_ask"] - frame["entry_bid"]
    frame["log_entry_bid_size"] = np.log1p(frame["entry_bid_size"].clip(lower=0.0))
    frame["log_entry_ask_size"] = np.log1p(frame["entry_ask_size"].clip(lower=0.0))
    frame["entry_relative_spread"] = frame["entry_spread"] / frame[
        "entry_bid"
    ].clip(lower=0.001)
    total_depth = frame["entry_bid_size"] + frame["entry_ask_size"]
    frame["entry_depth_imbalance"] = (
        frame["entry_bid_size"] - frame["entry_ask_size"]
    ) / total_depth.replace(0.0, np.nan)
    event_bid_depth = frame.groupby("forecast_event_id", sort=False)[
        "entry_bid_size"
    ].transform("sum")
    event_ask_depth = frame.groupby("forecast_event_id", sort=False)[
        "entry_ask_size"
    ].transform("sum")
    frame["entry_bid_depth_vs_ladder"] = frame["entry_bid_size"] / event_bid_depth.replace(
        0.0, np.nan
    )
    frame["entry_ask_depth_vs_ladder"] = frame["entry_ask_size"] / event_ask_depth.replace(
        0.0, np.nan
    )
    event_spread = frame.groupby("forecast_event_id", sort=False)["entry_spread"].transform(
        "median"
    )
    frame["rung_spread_vs_ladder"] = frame["entry_spread"] / event_spread.replace(
        0.0, np.nan
    )
    frame["neighbor_spread_mean"] = _neighbor_mean(frame, "entry_spread")
    frame["neighbor_bid_depth_mean"] = _neighbor_mean(frame, "entry_bid_size")
    frame["neighbor_ask_depth_mean"] = _neighbor_mean(frame, "entry_ask_size")
    frame["ladder_bid_sum"] = frame.groupby("forecast_event_id", sort=False)[
        "entry_bid"
    ].transform("sum")
    frame["ladder_ask_sum"] = frame.groupby("forecast_event_id", sort=False)[
        "entry_ask"
    ].transform("sum")
    frame["ladder_mid_sum"] = frame.groupby("forecast_event_id", sort=False)[
        "market_probability_after"
    ].transform("sum")
    normalized_market = frame["market_probability_after"] / frame[
        "ladder_mid_sum"
    ].replace(0.0, np.nan)
    frame["ladder_probability_hhi"] = normalized_market.pow(2).groupby(
        frame["forecast_event_id"], sort=False
    ).transform("sum")
    frame["ladder_probability_entropy"] = (
        -normalized_market.clip(lower=EPSILON)
        * np.log(normalized_market.clip(lower=EPSILON))
    ).groupby(frame["forecast_event_id"], sort=False).transform("sum")
    frame["market_mode_probability"] = frame.groupby(
        "forecast_event_id", sort=False
    )["market_probability_after"].transform("max")
    ranked_probability = frame.groupby("forecast_event_id", sort=False)[
        "market_probability_after"
    ].rank(method="first", ascending=False)
    runner_up = frame["market_probability_after"].where(ranked_probability.eq(2)).groupby(
        frame["forecast_event_id"], sort=False
    ).transform("max")
    frame["market_mode_margin"] = frame["market_mode_probability"] - runner_up
    frame["absolute_mode_distance"] = frame["signed_mode_distance"].abs()
    hour = frame["decision_hour_local"].fillna(12.0) % 24.0
    frame["decision_hour_sin"] = np.sin(2.0 * np.pi * hour / 24.0)
    frame["decision_hour_cos"] = np.cos(2.0 * np.pi * hour / 24.0)
    frame["entry_fee_per_share"] = frame["entry_fee_per_share"].fillna(
        weather_fee(frame["entry_ask"])
    )
    event_hedge_cost = (frame["entry_ask"] + frame["entry_fee_per_share"]).groupby(
        frame["forecast_event_id"], sort=False
    ).transform("sum")
    event_rungs = frame.groupby("forecast_event_id", sort=False)[
        "condition_id"
    ].transform("count")
    frame["entry_completion_cost_1tick_per_hedge_leg"] = (
        frame["entry_bid"]
        + event_hedge_cost
        - frame["entry_ask"]
        - frame["entry_fee_per_share"]
        + 0.001 * (event_rungs - 1)
    )
    frame["entry_completion_margin_1tick_per_hedge_leg"] = (
        1.0 - frame["entry_completion_cost_1tick_per_hedge_leg"]
    )
    frame["entry_completion_depth_shares"] = frame.groupby(
        "forecast_event_id", sort=False
    )["entry_ask_size"].transform("min")

    for horizon in (30, 60):
        bid = f"h{horizon}_bid"
        move = f"h{horizon}_bid_move"
        common = f"h{horizon}_common_bid_move"
        relative = f"h{horizon}_relative_bid_move"
        frame[move] = frame[bid] - frame["entry_bid"]
        frame[common] = _event_median(frame, move)
        frame[relative] = frame[move] - frame[common]

    frame["maker_conditional_h60_pnl"] = (
        frame["h60_bid"] - weather_fee(frame["h60_bid"]) - frame["entry_bid"]
    )
    frame["maker_touch_60"] = frame["h60_maker_bid_touch"].where(
        frame["h60_bid"].notna()
    )

    checkpoint_probability = frame["h30_bid"] / frame.groupby(
        "forecast_event_id", sort=False
    )["h30_bid"].transform("sum").replace(0.0, np.nan)
    frame["checkpoint_probability"] = checkpoint_probability
    frame = _mode_rank(frame, "checkpoint_probability", "checkpoint_mode_rank")
    frame["checkpoint_signed_mode_distance"] = (
        frame["ladder_rank"] - frame["checkpoint_mode_rank"]
    )
    frame["checkpoint_bid"] = frame["h30_bid"]
    frame["checkpoint_relative_markout"] = frame["h30_relative_bid_move"]
    frame["checkpoint_neighbor_propagation"] = _neighbor_mean(
        frame, "checkpoint_relative_markout"
    )
    frame["checkpoint_neighbor_lead_lag"] = (
        frame["checkpoint_neighbor_propagation"]
        - frame["checkpoint_relative_markout"]
    )
    frame["checkpoint_shock_x_mode_distance"] = (
        frame["weather_shock"] * frame["checkpoint_signed_mode_distance"]
    )
    frame["checkpoint_shock_x_neighbor_propagation"] = (
        frame["weather_shock"] * frame["checkpoint_neighbor_propagation"]
    )
    frame["checkpoint_shock_x_mode_x_neighbor_propagation"] = (
        frame["weather_shock"]
        * frame["checkpoint_signed_mode_distance"]
        * frame["checkpoint_neighbor_propagation"]
    )
    frame["continuation_net_value"] = (
        frame["h60_bid"]
        - weather_fee(frame["h60_bid"])
        - frame["h30_bid"]
        + weather_fee(frame["h30_bid"])
    )
    return frame.replace([np.inf, -np.inf], np.nan)


def _pipeline(alpha: float = 100.0) -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            ("ridge", Ridge(alpha=alpha)),
        ]
    )


def _classifier_pipeline(c: float = 0.1) -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            (
                "logistic",
                LogisticRegression(
                    C=c,
                    max_iter=2000,
                    random_state=20260810,
                ),
            ),
        ]
    )


def _weights(frame: pd.DataFrame) -> np.ndarray:
    dates = frame.groupby("target_date")["forecast_event_id"].transform("nunique")
    rungs = frame.groupby(["target_date", "forecast_event_id"])["condition_id"].transform("count")
    weights = 1.0 / dates.clip(lower=1) / rungs.clip(lower=1)
    return (weights / weights.mean()).to_numpy(float)


def _fit(frame: pd.DataFrame, features: Sequence[str], label: str) -> Pipeline:
    usable = frame.loc[frame[label].notna()].copy()
    if usable.empty:
        raise ValueError(f"no rows available for label {label}")
    model = _pipeline()
    model.fit(usable[list(features)], usable[label], ridge__sample_weight=_weights(usable))
    return model


def _predict(model: Pipeline, frame: pd.DataFrame, features: Sequence[str]) -> np.ndarray:
    return model.predict(frame[list(features)])


def _fit_classifier(
    frame: pd.DataFrame,
    features: Sequence[str],
    label: str,
    *,
    family: str = "linear",
) -> Any:
    usable = frame.loc[frame[label].notna()].copy()
    if usable[label].nunique() < 2:
        raise ValueError(f"classification label {label} has fewer than two classes")
    if family == "linear":
        model = _classifier_pipeline()
        model.fit(
            usable[list(features)],
            usable[label].astype(int),
            logistic__sample_weight=_weights(usable),
        )
    elif family == "hist_gradient_boosting":
        model = HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=120,
            max_leaf_nodes=15,
            min_samples_leaf=100,
            l2_regularization=10.0,
            random_state=20260810,
        )
        model.fit(
            usable[list(features)],
            usable[label].astype(int),
            sample_weight=_weights(usable),
        )
    else:
        raise ValueError(f"unsupported classifier family: {family}")
    return model


def _predict_probability(
    model: Any, frame: pd.DataFrame, features: Sequence[str]
) -> np.ndarray:
    return model.predict_proba(frame[list(features)])[:, 1]


def _fit_touch_value_model(
    frame: pd.DataFrame,
    features: Sequence[str],
    *,
    family: str,
) -> Any:
    if family == "linear":
        return _fit(frame, features, "maker_conditional_h60_pnl")
    if family == "hist_gradient_boosting":
        model = HistGradientBoostingRegressor(
            learning_rate=0.05,
            max_iter=120,
            max_leaf_nodes=15,
            min_samples_leaf=50,
            l2_regularization=10.0,
            loss="absolute_error",
            random_state=20260811,
        )
        model.fit(
            frame[list(features)],
            frame["maker_conditional_h60_pnl"],
            sample_weight=_weights(frame),
        )
        return model
    raise ValueError(f"unsupported touch value family: {family}")


def _expanding_oof_touch_and_value(
    frame: pd.DataFrame,
    features: Sequence[str],
    *,
    min_train_dates: int,
    block_dates: int = 3,
    family: str = "linear",
) -> pd.DataFrame:
    scoreable = frame.loc[
        frame["maker_touch_60"].notna()
        & frame["maker_conditional_h60_pnl"].notna()
    ].copy()
    dates = sorted(scoreable["target_date"].unique())
    outputs: list[pd.DataFrame] = []
    for start in range(min_train_dates, len(dates), block_dates):
        test_dates = dates[start : start + block_dates]
        train = scoreable.loc[scoreable["target_date"].isin(dates[:start])]
        test = scoreable.loc[scoreable["target_date"].isin(test_dates)].copy()
        touched_train = train.loc[train["maker_touch_60"].eq(1.0)]
        if (
            train.empty
            or test.empty
            or train["maker_touch_60"].nunique() < 2
            or len(touched_train) < 20
        ):
            continue
        touch_model = _fit_classifier(
            train, features, "maker_touch_60", family=family
        )
        value_model = _fit_touch_value_model(
            touched_train, features, family=family
        )
        test["predicted_touch_probability"] = _predict_probability(
            touch_model, test, features
        )
        test["predicted_touch_conditional_pnl"] = _predict(
            value_model, test, features
        )
        test["predicted_fill_adjusted_pnl"] = (
            test["predicted_touch_probability"]
            * test["predicted_touch_conditional_pnl"]
        )
        test["model_family"] = family
        outputs.append(test)
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()


def _expanding_oof(
    frame: pd.DataFrame,
    features: Sequence[str],
    label: str,
    *,
    min_train_dates: int,
    block_dates: int = 3,
) -> pd.DataFrame:
    dates = sorted(frame.loc[frame[label].notna(), "target_date"].unique())
    outputs: list[pd.DataFrame] = []
    for start in range(min_train_dates, len(dates), block_dates):
        test_dates = dates[start : start + block_dates]
        train = frame.loc[frame["target_date"].isin(dates[:start]) & frame[label].notna()]
        test = frame.loc[frame["target_date"].isin(test_dates) & frame[label].notna()].copy()
        if train.empty or test.empty:
            continue
        model = _fit(train, features, label)
        test["prediction"] = _predict(model, test, features)
        outputs.append(test)
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()


def _date_block_delta(
    frame: pd.DataFrame,
    challenger: str,
    baseline: str,
    label: str,
    *,
    draws: int,
    seed: int,
) -> dict[str, float | int]:
    work = frame.dropna(subset=[challenger, baseline, label]).copy()
    work["delta"] = (work[challenger] - work[label]) ** 2 - (work[baseline] - work[label]) ** 2
    by_date = work.groupby("target_date")["delta"].mean()
    if by_date.empty:
        return {"rows": 0, "target_dates": 0, "mean": math.nan, "ci_low": math.nan, "ci_high": math.nan}
    rng = np.random.default_rng(seed)
    values = by_date.to_numpy(float)
    samples = np.array([rng.choice(values, len(values), replace=True).mean() for _ in range(draws)])
    return {
        "rows": int(len(work)),
        "target_dates": int(len(by_date)),
        "mean": float(values.mean()),
        "ci_low": float(np.quantile(samples, 0.025)),
        "ci_high": float(np.quantile(samples, 0.975)),
    }


def _select_entries(
    frame: pd.DataFrame,
    prediction: str,
    *,
    execution_style: str,
    threshold: float,
) -> pd.DataFrame:
    work = frame.copy()
    projected_bid = (work["entry_bid"] + work[prediction]).clip(0.001, 0.999)
    if execution_style == "taker":
        work["predicted_entry_net_value"] = (
            projected_bid
            - weather_fee(projected_bid)
            - work["entry_ask"]
            - work["entry_fee_per_share"]
        )
    elif execution_style == "maker_fill_gated":
        work["predicted_entry_net_value"] = (
            work[prediction] - weather_fee(projected_bid)
        )
    else:
        raise ValueError(f"unsupported execution_style: {execution_style}")
    selected = work.loc[work["predicted_entry_net_value"] > threshold].sort_values(
        ["target_date", "forecast_event_id", "predicted_entry_net_value"],
        ascending=[True, True, False],
    )
    selected = selected.drop_duplicates("forecast_event_id", keep="first")
    return selected.sort_values(
        ["target_date", "city", "snapshot_epoch", "predicted_entry_net_value"],
        ascending=[True, True, True, False],
    ).drop_duplicates(["target_date", "city"], keep="first")


def _select_maker_threshold(
    oof: pd.DataFrame,
    development: pd.DataFrame,
) -> tuple[float, pd.DataFrame]:
    keys = list(IDENTITY_COLUMNS)
    source = development[
        keys
        + [
            "snapshot_epoch",
            "entry_bid",
            "entry_ask",
            "entry_fee_per_share",
            "h60_bid",
        ]
    ].drop_duplicates(keys)
    scored = oof.merge(source, on=keys, how="left", validate="one_to_one")
    projected_bid = (
        scored["entry_bid"] + scored["interaction_prediction"]
    ).clip(0.001, 0.999)
    scored["predicted_entry_net_value"] = (
        scored["interaction_prediction"] - weather_fee(projected_bid)
    )
    quantiles = (0.0, 0.50, 0.70, 0.80, 0.90, 0.95, 0.975, 0.99)
    thresholds = {0.0}
    thresholds.update(
        float(scored["predicted_entry_net_value"].quantile(value))
        for value in quantiles[1:]
    )
    records = []
    for threshold in sorted(thresholds):
        selected = _select_entries(
            scored.rename(columns={"interaction_prediction": "entry_prediction"}),
            "entry_prediction",
            execution_style="maker_fill_gated",
            threshold=threshold,
        ).dropna(subset=["h60_bid"])
        selected["maker_conditional_pnl"] = (
            selected["h60_bid"]
            - weather_fee(selected["h60_bid"])
            - selected["entry_bid"]
        )
        cost = float(selected["entry_bid"].sum())
        pnl = float(selected["maker_conditional_pnl"].sum())
        records.append(
            {
                "threshold": threshold,
                "positions": int(len(selected)),
                "target_dates": int(selected["target_date"].nunique()),
                "conditional_cost": cost,
                "conditional_pnl": pnl,
                "conditional_roi": pnl / cost if cost > 0 else math.nan,
            }
        )
    table = pd.DataFrame(records)
    eligible = table.loc[
        table["positions"].ge(20)
        & table["target_dates"].ge(6)
        & table["conditional_roi"].gt(0.0)
    ]
    if eligible.empty:
        return math.inf, table
    selected = eligible.sort_values(
        ["conditional_roi", "positions"], ascending=[False, False]
    ).iloc[0]
    return float(selected["threshold"]), table


def _select_antitoxic_entries(
    frame: pd.DataFrame,
    *,
    touch_probability_min: float,
    touch_conditional_pnl_min: float,
) -> pd.DataFrame:
    work = frame.copy()
    work["predicted_fill_adjusted_pnl"] = (
        work["predicted_touch_probability"]
        * work["predicted_touch_conditional_pnl"]
    )
    selected = work.loc[
        work["predicted_touch_probability"].ge(touch_probability_min)
        & work["predicted_touch_conditional_pnl"].gt(touch_conditional_pnl_min)
        & work["predicted_fill_adjusted_pnl"].gt(0.0)
    ].sort_values(
        ["target_date", "forecast_event_id", "predicted_fill_adjusted_pnl"],
        ascending=[True, True, False],
    )
    selected = selected.drop_duplicates("forecast_event_id", keep="first")
    return selected.sort_values(
        ["target_date", "city", "snapshot_epoch", "predicted_fill_adjusted_pnl"],
        ascending=[True, True, True, False],
    ).drop_duplicates(["target_date", "city"], keep="first")


def _proxy_fill_stats(
    selected: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    work = selected.dropna(
        subset=["maker_touch_60", "maker_conditional_h60_pnl", "entry_bid"]
    ).copy()
    touched = work.loc[work["maker_touch_60"].eq(1.0)].copy()
    if touched.empty:
        return {
            "selected_quotes": int(len(work)),
            "selected_target_dates": int(work["target_date"].nunique()),
            "proxy_fills": 0,
            "proxy_fill_target_dates": 0,
            "proxy_fill_rate": 0.0,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "conditional_roi": math.nan,
            "ci_low": math.nan,
            "ci_high": math.nan,
            "toxic_proxy_fill_rate": math.nan,
            "one_tick_each_side_roi": math.nan,
            "negative_fill_2x_roi": math.nan,
            "positive_pnl_top_trade_share": math.nan,
        }
    cost = float(touched["entry_bid"].sum())
    pnl = float(touched["maker_conditional_h60_pnl"].sum())
    by_date = touched.groupby("target_date").agg(
        pnl=("maker_conditional_h60_pnl", "sum"),
        cost=("entry_bid", "sum"),
    )
    values = by_date[["pnl", "cost"]].to_numpy(float)
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(draws):
        sample = values[rng.integers(0, len(values), size=len(values))]
        boot.append(sample[:, 0].sum() / sample[:, 1].sum())
    adverse_weight = np.where(touched["maker_conditional_h60_pnl"].lt(0.0), 2.0, 1.0)
    adverse_cost = float((touched["entry_bid"] * adverse_weight).sum())
    adverse_pnl = float((touched["maker_conditional_h60_pnl"] * adverse_weight).sum())
    positive = touched.loc[touched["maker_conditional_h60_pnl"].gt(0.0), "maker_conditional_h60_pnl"]
    return {
        "selected_quotes": int(len(work)),
        "selected_target_dates": int(work["target_date"].nunique()),
        "proxy_fills": int(len(touched)),
        "proxy_fill_target_dates": int(touched["target_date"].nunique()),
        "proxy_fill_rate": float(len(touched) / len(work)) if len(work) else math.nan,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "conditional_roi": pnl / cost if cost > 0 else math.nan,
        "ci_low": float(np.quantile(boot, 0.025)),
        "ci_high": float(np.quantile(boot, 0.975)),
        "toxic_proxy_fill_rate": float(
            touched["maker_conditional_h60_pnl"].lt(0.0).mean()
        ),
        "one_tick_each_side_roi": float(
            (pnl - 0.002 * len(touched)) / cost
        ) if cost > 0 else math.nan,
        "negative_fill_2x_roi": adverse_pnl / adverse_cost if adverse_cost > 0 else math.nan,
        "positive_pnl_top_trade_share": (
            float(positive.max() / positive.sum()) if not positive.empty else math.nan
        ),
    }


def _select_antitoxic_thresholds(
    oof: pd.DataFrame,
    *,
    draws: int,
) -> tuple[float, float, pd.DataFrame]:
    touch_grid = sorted(
        {
            float(oof["predicted_touch_probability"].quantile(q))
            for q in (0.50, 0.75)
        }
    )
    positive_value = oof.loc[
        oof["predicted_touch_conditional_pnl"].gt(0.0),
        "predicted_touch_conditional_pnl",
    ]
    value_grid = sorted(
        {0.0}
        | {
            float(positive_value.quantile(q))
            for q in (0.75,)
            if not positive_value.empty
        }
    )
    records: list[dict[str, Any]] = []
    for index, (touch_min, value_min) in enumerate(
        (pair for touch in touch_grid for pair in ((touch, value) for value in value_grid))
    ):
        selected = _select_antitoxic_entries(
            oof,
            touch_probability_min=touch_min,
            touch_conditional_pnl_min=value_min,
        )
        records.append(
            {
                "variant": index + 1,
                "touch_probability_min": touch_min,
                "touch_conditional_pnl_min": value_min,
                **_proxy_fill_stats(selected, draws=draws, seed=20260820 + index),
            }
        )
    table = pd.DataFrame(records)
    eligible = table.loc[
        table["proxy_fills"].ge(20)
        & table["proxy_fill_target_dates"].ge(6)
        & table["one_tick_each_side_roi"].gt(0.0)
        & table["negative_fill_2x_roi"].gt(0.0)
    ]
    if eligible.empty:
        return math.inf, math.inf, table
    winner = eligible.sort_values(
        ["negative_fill_2x_roi", "proxy_fills"], ascending=[False, False]
    ).iloc[0]
    return (
        float(winner["touch_probability_min"]),
        float(winner["touch_conditional_pnl_min"]),
        table,
    )


def _touch_classifier_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    work = frame.dropna(
        subset=["maker_touch_60", "predicted_touch_probability"]
    )
    if work.empty:
        return {"rows": 0, "target_dates": 0, "brier": math.nan, "roc_auc": math.nan}
    labels = work["maker_touch_60"].astype(int)
    return {
        "rows": int(len(work)),
        "target_dates": int(work["target_date"].nunique()),
        "base_rate": float(labels.mean()),
        "brier": float(brier_score_loss(labels, work["predicted_touch_probability"])),
        "roc_auc": (
            float(roc_auc_score(labels, work["predicted_touch_probability"]))
            if labels.nunique() == 2
            else math.nan
        ),
    }


def _select_completion_quotes(
    frame: pd.DataFrame,
    *,
    buffer_per_set: float = COMPLETION_BUFFER_PER_SET,
    requested_shares: float = COMPLETION_REQUESTED_SHARES,
) -> pd.DataFrame:
    eligible = frame.loc[
        frame["entry_completion_margin_1tick_per_hedge_leg"].ge(buffer_per_set)
        & frame["entry_completion_depth_shares"].ge(requested_shares)
    ].sort_values(
        [
            "target_date",
            "forecast_event_id",
            "entry_completion_margin_1tick_per_hedge_leg",
        ],
        ascending=[True, True, False],
    )
    eligible = eligible.drop_duplicates("forecast_event_id", keep="first")
    return eligible.sort_values(
        [
            "target_date",
            "city",
            "snapshot_epoch",
            "entry_completion_margin_1tick_per_hedge_leg",
        ],
        ascending=[True, True, True, False],
    ).drop_duplicates(["target_date", "city"], keep="first")


def _completion_stats(
    selected: pd.DataFrame,
    *,
    draws: int,
    seed: int,
    requested_shares: float = COMPLETION_REQUESTED_SHARES,
) -> dict[str, Any]:
    work = selected.copy()
    touch = work.loc[
        work["maker_touch_60"].eq(1.0)
        & work["touch_completion_margin_1tick_per_hedge_leg"].notna()
        & work["touch_completion_depth_shares"].ge(requested_shares)
    ].copy()
    if touch.empty:
        return {
            "selected_quotes": int(len(work)),
            "selected_target_dates": int(work["target_date"].nunique()),
            "proxy_fills_with_complete_hedge": 0,
            "proxy_fill_target_dates": 0,
            "proxy_fill_rate": 0.0,
            "locked_cost_usd": 0.0,
            "locked_pnl_usd": 0.0,
            "locked_roi": math.nan,
            "ci_low": math.nan,
            "ci_high": math.nan,
            "negative_locked_margin_rate": math.nan,
        }
    touch["locked_cost"] = (
        touch["touch_completion_cost_1tick_per_hedge_leg"] * requested_shares
    )
    touch["locked_pnl"] = (
        touch["touch_completion_margin_1tick_per_hedge_leg"] * requested_shares
    )
    by_date = touch.groupby("target_date").agg(
        pnl=("locked_pnl", "sum"), cost=("locked_cost", "sum")
    )
    values = by_date[["pnl", "cost"]].to_numpy(float)
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(draws):
        sample = values[rng.integers(0, len(values), size=len(values))]
        boot.append(sample[:, 0].sum() / sample[:, 1].sum())
    cost = float(touch["locked_cost"].sum())
    pnl = float(touch["locked_pnl"].sum())
    return {
        "selected_quotes": int(len(work)),
        "selected_target_dates": int(work["target_date"].nunique()),
        "proxy_fills_with_complete_hedge": int(len(touch)),
        "proxy_fill_target_dates": int(touch["target_date"].nunique()),
        "proxy_fill_rate": float(len(touch) / len(work)) if len(work) else math.nan,
        "locked_cost_usd": cost,
        "locked_pnl_usd": pnl,
        "locked_roi": pnl / cost if cost > 0 else math.nan,
        "ci_low": float(np.quantile(boot, 0.025)),
        "ci_high": float(np.quantile(boot, 0.975)),
        "negative_locked_margin_rate": float(
            touch["touch_completion_margin_1tick_per_hedge_leg"].lt(0.0).mean()
        ),
    }


def _position_stats(
    frame: pd.DataFrame,
    pnl: str,
    *,
    cost_column: str,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    work = frame.dropna(subset=[pnl, cost_column]).copy()
    if work.empty:
        return {
            "positions": 0,
            "target_dates": 0,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "roi": math.nan,
            "ci_low": math.nan,
            "ci_high": math.nan,
        }
    work["cost"] = work[cost_column]
    by_date = work.groupby("target_date").agg(pnl=(pnl, "sum"), cost=("cost", "sum"))
    rng = np.random.default_rng(seed)
    values = by_date[["pnl", "cost"]].to_numpy(float)
    rois = []
    for _ in range(draws):
        sample = values[rng.integers(0, len(values), size=len(values))]
        rois.append(sample[:, 0].sum() / sample[:, 1].sum())
    return {
        "positions": int(len(work)),
        "target_dates": int(len(by_date)),
        "cost_usd": float(work["cost"].sum()),
        "pnl_usd": float(work[pnl].sum()),
        "roi": float(work[pnl].sum() / work["cost"].sum()),
        "ci_low": float(np.quantile(rois, 0.025)),
        "ci_high": float(np.quantile(rois, 0.975)),
    }


def _paired_position_delta(
    frame: pd.DataFrame,
    candidate_pnl: str,
    baseline_pnl: str,
    *,
    cost_column: str,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    """Compare exit policies on identical positions and target-date blocks."""

    work = frame.dropna(subset=[candidate_pnl, baseline_pnl, cost_column]).copy()
    if work.empty:
        return {
            "positions": 0,
            "target_dates": 0,
            "candidate_roi": math.nan,
            "baseline_roi": math.nan,
            "roi_delta": math.nan,
            "ci_low": math.nan,
            "ci_high": math.nan,
        }
    by_date = work.groupby("target_date").agg(
        candidate_pnl=(candidate_pnl, "sum"),
        baseline_pnl=(baseline_pnl, "sum"),
        cost=(cost_column, "sum"),
    )
    values = by_date[["candidate_pnl", "baseline_pnl", "cost"]].to_numpy(float)

    def delta(sample: np.ndarray) -> float:
        cost = sample[:, 2].sum()
        return float((sample[:, 0].sum() - sample[:, 1].sum()) / cost)

    rng = np.random.default_rng(seed)
    boot = [
        delta(values[rng.integers(0, len(values), size=len(values))])
        for _ in range(draws)
    ]
    cost = float(work[cost_column].sum())
    return {
        "positions": int(len(work)),
        "target_dates": int(len(by_date)),
        "candidate_roi": float(work[candidate_pnl].sum() / cost),
        "baseline_roi": float(work[baseline_pnl].sum() / cost),
        "roi_delta": float((work[candidate_pnl].sum() - work[baseline_pnl].sum()) / cost),
        "ci_low": float(np.quantile(boot, 0.025)),
        "ci_high": float(np.quantile(boot, 0.975)),
    }


def train_position_policy(
    rows: pd.DataFrame,
    *,
    min_train_dates: int = 15,
    holdout_fraction: float = 0.20,
    draws: int = 2000,
) -> dict[str, Any]:
    frame = add_full_ladder_position_features(rows)
    dates = sorted(frame.loc[frame["h60_relative_bid_move"].notna(), "target_date"].unique())
    if len(dates) < min_train_dates + 6:
        return {
            "status": "blocked_insufficient_dates",
            "frame": frame,
            "available_target_dates": len(dates),
            "required_target_dates": min_train_dates + 6,
        }
    holdout_count = max(6, int(math.ceil(len(dates) * holdout_fraction)))
    development_dates = dates[:-holdout_count]
    holdout_dates = dates[-holdout_count:]
    development = frame.loc[frame["target_date"].isin(development_dates)].copy()
    holdout = frame.loc[frame["target_date"].isin(holdout_dates)].copy()
    if development["maker_touch_60"].notna().sum() == 0:
        return {
            "status": "blocked_missing_future_ask_touch_evidence",
            "frame": frame,
            "available_target_dates": len(dates),
            "required_target_dates": min_train_dates + 6,
        }
    label = "h60_relative_bid_move"

    baseline_oof = _expanding_oof(
        development,
        MARKET_BASELINE_FEATURES,
        label,
        min_train_dates=min_train_dates,
    )
    challenger_oof = _expanding_oof(
        development,
        INTERACTION_FEATURES,
        label,
        min_train_dates=min_train_dates,
    )
    oof_keys = list(IDENTITY_COLUMNS)
    oof = challenger_oof[oof_keys + [label, "prediction"]].rename(
        columns={"prediction": "interaction_prediction"}
    ).merge(
        baseline_oof[oof_keys + ["prediction"]].rename(
            columns={"prediction": "market_prediction"}
        ),
        on=oof_keys,
        how="inner",
        validate="one_to_one",
    )
    oof_delta = _date_block_delta(
        oof,
        "interaction_prediction",
        "market_prediction",
        label,
        draws=draws,
        seed=20260810,
    )

    entry_model = _fit(development, INTERACTION_FEATURES, label)
    market_model = _fit(development, MARKET_BASELINE_FEATURES, label)
    holdout["entry_predicted_relative_markout"] = _predict(
        entry_model, holdout, INTERACTION_FEATURES
    )
    holdout["market_predicted_relative_markout"] = _predict(
        market_model, holdout, MARKET_BASELINE_FEATURES
    )
    holdout_delta = _date_block_delta(
        holdout,
        "entry_predicted_relative_markout",
        "market_predicted_relative_markout",
        label,
        draws=draws,
        seed=20260811,
    )

    development_for_exit = development.copy()
    development_for_exit["entry_predicted_relative_markout"] = _predict(
        entry_model, development_for_exit, INTERACTION_FEATURES
    )
    exit_model = _fit(
        development_for_exit,
        CONTINUATION_FEATURES,
        "continuation_net_value",
    )
    maker_threshold, legacy_threshold_table = _select_maker_threshold(oof, development)
    legacy_entries = _select_entries(
        holdout,
        "entry_predicted_relative_markout",
        execution_style="maker_fill_gated",
        threshold=maker_threshold,
    ).copy()

    family_oof: dict[str, pd.DataFrame] = {}
    threshold_tables: list[pd.DataFrame] = []
    family_selection: list[dict[str, Any]] = []
    for family in ("linear", "hist_gradient_boosting"):
        candidate_oof = _expanding_oof_touch_and_value(
            development,
            MICROSTRUCTURE_FEATURES,
            min_train_dates=min_train_dates,
            family=family,
        )
        if candidate_oof.empty:
            continue
        family_oof[family] = candidate_oof
        probability_min, value_min, family_table = _select_antitoxic_thresholds(
            candidate_oof, draws=draws
        )
        family_table = family_table.copy()
        family_table["model_family"] = family
        threshold_tables.append(family_table)
        eligible_row = family_table.loc[
            family_table["touch_probability_min"].eq(probability_min)
            & family_table["touch_conditional_pnl_min"].eq(value_min)
        ]
        family_selection.append(
            {
                "model_family": family,
                "touch_probability_min": probability_min,
                "touch_conditional_pnl_min": value_min,
                "selected_negative_fill_2x_roi": (
                    float(eligible_row.iloc[0]["negative_fill_2x_roi"])
                    if not eligible_row.empty
                    else math.nan
                ),
                **_touch_classifier_metrics(candidate_oof),
            }
        )
    if not family_oof:
        return {
            "status": "blocked_insufficient_touch_oof",
            "frame": frame,
            "available_target_dates": len(dates),
            "required_target_dates": min_train_dates + 6,
        }
    family_selection_table = pd.DataFrame(family_selection)
    finite_families = family_selection_table.loc[
        np.isfinite(family_selection_table["touch_probability_min"])
    ]
    if finite_families.empty:
        selected_family = str(
            family_selection_table.sort_values("brier", ascending=True).iloc[0][
                "model_family"
            ]
        )
    else:
        selected_family = str(
            finite_families.sort_values(
                ["selected_negative_fill_2x_roi", "brier"],
                ascending=[False, True],
            ).iloc[0]["model_family"]
        )
    chosen = family_selection_table.loc[
        family_selection_table["model_family"].eq(selected_family)
    ].iloc[0]
    touch_probability_min = float(chosen["touch_probability_min"])
    touch_conditional_pnl_min = float(chosen["touch_conditional_pnl_min"])
    threshold_table = pd.concat(threshold_tables, ignore_index=True)
    antitoxic_oof = family_oof[selected_family]
    scoreable_development = development.loc[
        development["maker_touch_60"].notna()
        & development["maker_conditional_h60_pnl"].notna()
    ]
    touched_development = scoreable_development.loc[
        scoreable_development["maker_touch_60"].eq(1.0)
    ]
    touch_model = _fit_classifier(
        scoreable_development,
        MICROSTRUCTURE_FEATURES,
        "maker_touch_60",
        family=selected_family,
    )
    touch_value_model = _fit_touch_value_model(
        touched_development,
        MICROSTRUCTURE_FEATURES,
        family=selected_family,
    )
    holdout["predicted_touch_probability"] = _predict_probability(
        touch_model, holdout, MICROSTRUCTURE_FEATURES
    )
    holdout["predicted_touch_conditional_pnl"] = _predict(
        touch_value_model, holdout, MICROSTRUCTURE_FEATURES
    )
    holdout["predicted_fill_adjusted_pnl"] = (
        holdout["predicted_touch_probability"]
        * holdout["predicted_touch_conditional_pnl"]
    )
    entries = _select_antitoxic_entries(
        holdout,
        touch_probability_min=touch_probability_min,
        touch_conditional_pnl_min=touch_conditional_pnl_min,
    ).copy()
    for column in (
        "predicted_continuation_net_value",
        "position_action_at_30m",
        "position_exit_horizon_min",
        "dynamic_exit_bid",
        "dynamic_exit_fee",
        "dynamic_position_pnl",
        "fixed_30_pnl",
        "fixed_60_pnl",
    ):
        if column not in entries:
            entries[column] = np.nan
    if not entries.empty:
        entries["predicted_continuation_net_value"] = _predict(
            exit_model, entries, CONTINUATION_FEATURES
        )
        entries["position_action_at_30m"] = np.where(
            entries["predicted_continuation_net_value"] > 0.0, "HOLD", "EXIT"
        )
        entries["position_exit_horizon_min"] = np.where(
            entries["position_action_at_30m"].eq("HOLD"), 60, 30
        )
        entries["dynamic_exit_bid"] = np.where(
            entries["position_action_at_30m"].eq("HOLD"),
            entries["h60_bid"],
            entries["h30_bid"],
        )
        entries["dynamic_exit_fee"] = weather_fee(entries["dynamic_exit_bid"])
        entries["dynamic_position_pnl"] = (
            entries["dynamic_exit_bid"]
            - entries["dynamic_exit_fee"]
            - entries["entry_bid"]
        )
        for horizon in (30, 60):
            entries[f"fixed_{horizon}_pnl"] = (
                entries[f"h{horizon}_bid"]
                - weather_fee(entries[f"h{horizon}_bid"])
                - entries["entry_bid"]
            )

    completion_development = _select_completion_quotes(development)
    completion_holdout = _select_completion_quotes(holdout)
    completion_development_stats = _completion_stats(
        completion_development, draws=draws, seed=20260823
    )
    completion_holdout_stats = _completion_stats(
        completion_holdout, draws=draws, seed=20260824
    )
    completion_development_gate = bool(
        completion_development_stats["proxy_fills_with_complete_hedge"] >= 10
        and completion_development_stats["proxy_fill_target_dates"] >= 6
        and (completion_development_stats["locked_roi"] or -math.inf) > 0.0
        and completion_development_stats["negative_locked_margin_rate"] == 0.0
    )

    bundle = {
        "schema_version": SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "entry_features": list(INTERACTION_FEATURES),
        "market_baseline_features": list(MARKET_BASELINE_FEATURES),
        "continuation_features": list(CONTINUATION_FEATURES),
        "entry_model": entry_model,
        "market_baseline_model": market_model,
        "touch_model": touch_model,
        "touch_value_model": touch_value_model,
        "touch_features": list(MICROSTRUCTURE_FEATURES),
        "touch_model_family": selected_family,
        "continuation_model": exit_model,
        "entry_horizon_min": ENTRY_HORIZON_MIN,
        "continuation_checkpoint_min": CONTINUATION_CHECKPOINT_MIN,
        "hard_exit_min": HARD_EXIT_MIN,
        "execution_style": "maker_antitoxic_trade_through_proxy_v1",
        "entry_threshold_net_value": maker_threshold,
        "touch_probability_min": touch_probability_min,
        "touch_conditional_pnl_min": touch_conditional_pnl_min,
        "maker_quote_ttl_min": 60,
        "primary_policy": "full_ladder_completion_v1",
        "research_verdict": (
            "development_gate_pass"
            if completion_development_gate
            else "historical_gate_fail_zero_notional_probe_only"
        ),
        "completion_policy": {
            "buffer_per_set": COMPLETION_BUFFER_PER_SET,
            "requested_shares": COMPLETION_REQUESTED_SHARES,
            "hedge_slippage_per_leg": 0.001,
            "maker_fee_per_share": 0.0,
            "hedge_execution": "immediate_taker_all_other_yes_after_actual_fill",
        },
        "continuation_threshold_net_value": 0.0,
        "development_end": development_dates[-1],
        "holdout_start": holdout_dates[0],
    }
    return {
        "status": (
            "runnable_zero_notional_full_ladder_completion"
            if completion_development_gate
            else "runnable_zero_notional_completion_probe_historical_fail"
        ),
        "frame": frame,
        "oof": oof,
        "antitoxic_oof": antitoxic_oof,
        "holdout": holdout,
        "positions": entries,
        "legacy_positions": legacy_entries,
        "completion_development": completion_development,
        "completion_holdout": completion_holdout,
        "completion_development_stats": completion_development_stats,
        "completion_holdout_stats": completion_holdout_stats,
        "completion_development_gate": completion_development_gate,
        "bundle": bundle,
        "development_dates": development_dates,
        "holdout_dates": holdout_dates,
        "oof_delta": oof_delta,
        "holdout_delta": holdout_delta,
        "maker_threshold_selection": threshold_table,
        "model_family_selection": family_selection_table,
        "legacy_maker_threshold_selection": legacy_threshold_table,
        "touch_classifier_oof": _touch_classifier_metrics(antitoxic_oof),
        "touch_classifier_holdout": _touch_classifier_metrics(holdout),
        "proxy_fill_holdout": _proxy_fill_stats(
            entries, draws=draws, seed=20260821
        ),
        "legacy_proxy_fill_holdout": _proxy_fill_stats(
            legacy_entries, draws=draws, seed=20260822
        ),
        "dynamic": _position_stats(
            entries,
            "dynamic_position_pnl",
            cost_column="entry_bid",
            draws=draws,
            seed=20260812,
        ),
        "fixed_30": _position_stats(
            entries,
            "fixed_30_pnl",
            cost_column="entry_bid",
            draws=draws,
            seed=20260813,
        ),
        "fixed_60": _position_stats(
            entries,
            "fixed_60_pnl",
            cost_column="entry_bid",
            draws=draws,
            seed=20260814,
        ),
        "dynamic_vs_fixed_60_same_rows": _paired_position_delta(
            entries,
            "dynamic_position_pnl",
            "fixed_60_pnl",
            cost_column="entry_bid",
            draws=draws,
            seed=20260815,
        ),
        "denominator": {
            "input_rows": int(len(rows)),
            "d1_rungs": int(len(frame)),
            "forecast_events": int(frame["forecast_event_id"].nunique()),
            "target_dates": int(frame["target_date"].nunique()),
            "h30_scoreable_rungs": int(frame["h30_bid"].notna().sum()),
            "h60_scoreable_rungs": int(frame["h60_bid"].notna().sum()),
            "h60_touch_scoreable_rungs": int(frame["maker_touch_60"].notna().sum()),
            "h60_trade_through_proxy_rungs": int(frame["maker_touch_60"].eq(1.0).sum()),
        },
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def write_position_policy_outputs(
    result: Mapping[str, Any], input_path: Path, output_dir: Path
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if str(result["status"]).startswith("blocked_"):
        summary = {
            "schema_version": SCHEMA_VERSION,
            "status": result["status"],
            "available_target_dates": result["available_target_dates"],
            "required_target_dates": result["required_target_dates"],
            "input_path": str(input_path),
            "input_sha256": _sha256(input_path),
            "production": {"live_action": "none", "orders_changed": 0},
        }
        (output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return summary

    result["oof"].to_csv(output_dir / "development_oof_relative_markout.csv", index=False)
    result["antitoxic_oof"].to_csv(
        output_dir / "development_oof_antitoxic_scores.csv", index=False
    )
    result["holdout"].to_csv(output_dir / "secondary_holdout_rung_scores.csv", index=False)
    result["positions"].to_csv(output_dir / "secondary_holdout_positions.csv", index=False)
    result["legacy_positions"].to_csv(
        output_dir / "secondary_holdout_legacy_positions.csv", index=False
    )
    result["completion_development"].to_csv(
        output_dir / "development_completion_quotes.csv", index=False
    )
    result["completion_holdout"].to_csv(
        output_dir / "secondary_holdout_completion_quotes.csv", index=False
    )
    result["maker_threshold_selection"].to_csv(
        output_dir / "development_antitoxic_threshold_selection.csv", index=False
    )
    result["legacy_maker_threshold_selection"].to_csv(
        output_dir / "development_legacy_maker_threshold_selection.csv", index=False
    )
    result["model_family_selection"].to_csv(
        output_dir / "development_model_family_selection.csv", index=False
    )
    artifact_path = output_dir / "position_policy.joblib"
    joblib.dump(result["bundle"], artifact_path)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": result["status"],
        "input_path": str(input_path),
        "input_sha256": _sha256(input_path),
        "denominator_scope": (
            "reconstructed D-1 forecast-event x complete-ladder rung; direct "
            "30/60m bid+ask windows; maker fillability is trade-through proxy, "
            "never an inferred actual fill"
        ),
        "denominator": result["denominator"],
        "development_dates": result["development_dates"],
        "secondary_holdout_dates": result["holdout_dates"],
        "entry_incremental_vs_market_oof": result["oof_delta"],
        "entry_incremental_vs_market_holdout": result["holdout_delta"],
        "dynamic_position": result["dynamic"],
        "fixed_30": result["fixed_30"],
        "fixed_60": result["fixed_60"],
        "dynamic_vs_fixed_60_same_rows": result["dynamic_vs_fixed_60_same_rows"],
        "touch_classifier_oof": result["touch_classifier_oof"],
        "touch_classifier_holdout": result["touch_classifier_holdout"],
        "proxy_fill_holdout": result["proxy_fill_holdout"],
        "legacy_proxy_fill_holdout": result["legacy_proxy_fill_holdout"],
        "completion_development_gate": result["completion_development_gate"],
        "completion_development": result["completion_development_stats"],
        "completion_holdout": result["completion_holdout_stats"],
        "maker_threshold_selection": result["maker_threshold_selection"].to_dict(
            orient="records"
        ),
        "model_family_selection": result["model_family_selection"].to_dict(
            orient="records"
        ),
        "artifact": {
            "path": str(artifact_path),
            "sha256": _sha256(artifact_path),
            "model_id": MODEL_ID,
        },
        "signal_funnel": {
            "raw_rungs": result["denominator"]["input_rows"],
            "d1_rungs": result["denominator"]["d1_rungs"],
            "forecast_events": result["denominator"]["forecast_events"],
            "selected_positions": result["dynamic"]["positions"],
        },
        "evidence_funnel": {
            "h30_scoreable_rungs": result["denominator"]["h30_scoreable_rungs"],
            "h60_scoreable_rungs": result["denominator"]["h60_scoreable_rungs"],
            "h60_touch_scoreable_rungs": result["denominator"]["h60_touch_scoreable_rungs"],
            "h60_trade_through_proxy_rungs": result["denominator"]["h60_trade_through_proxy_rungs"],
            "selected_trade_through_proxies": result["proxy_fill_holdout"]["proxy_fills"],
            "completion_selected_quotes": result["completion_holdout_stats"]["selected_quotes"],
            "completion_proxy_fills": result["completion_holdout_stats"][
                "proxy_fills_with_complete_hedge"
            ],
            "actual_fills": 0,
        },
        "production": {"live_action": "none", "orders_changed": 0},
    }
    safe = _json_safe(summary)
    (output_dir / "summary.json").write_text(
        json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    dynamic = safe["dynamic_position"]
    holdout_delta = safe["entry_incremental_vs_market_holdout"]
    proxy = safe["proxy_fill_holdout"]
    legacy_proxy = safe["legacy_proxy_fill_holdout"]
    touch = safe["touch_classifier_holdout"]
    completion_dev = safe["completion_development"]
    completion_holdout = safe["completion_holdout"]
    report = f"""# Forecast repricing full-ladder position policy

significance={'PASS' if holdout_delta['ci_high'] is not None and holdout_delta['ci_high'] < 0 else 'FAIL'}
baseline=market-level relative-markout M0; holdout MSE delta={holdout_delta['mean']}
forward=NA; secondary reconstructed holdout only
execution=best-bid maker quote; 60m ask trade-through proxy; anti-toxicity gate; dynamic full-ladder re-score; actual fills=0

production: live_action=none; orders_changed=0

## Primary completion expression

单腿 maker 的 trade-through 样本在 development 中呈系统性负 markout，因此 primary 改为 conditional
full-ladder completion：只在一档 best-bid maker fill 后，其余全部 YES 档以当前 ask+taker fee+每腿1 tick
仍可把总成本压在 `$1 - {COMPLETION_BUFFER_PER_SET}` 以下时保留挂单；真实 fill 后立即补齐其余档。

| period | quotes | touch+complete hedge | dates | locked ROI | 95% CI | negative margin rate |
|---|---:|---:|---:|---:|---:|---:|
| development | {completion_dev['selected_quotes']} | {completion_dev['proxy_fills_with_complete_hedge']} | {completion_dev['proxy_fill_target_dates']} | {completion_dev['locked_roi']} | [{completion_dev['ci_low']}, {completion_dev['ci_high']}] | {completion_dev['negative_locked_margin_rate']} |
| secondary holdout | {completion_holdout['selected_quotes']} | {completion_holdout['proxy_fills_with_complete_hedge']} | {completion_holdout['proxy_fill_target_dates']} | {completion_holdout['locked_roi']} | [{completion_holdout['ci_low']}, {completion_holdout['ci_high']}] | {completion_holdout['negative_locked_margin_rate']} |

这不是无风险承诺：maker fill→hedge 的延迟、queue position、partial fill 与多腿 depth 仍会产生 legging risk；
但它不再依赖被成交的单腿随后上涨。

## Runnable policy

`forecast revision -> full-ladder fillability + conditional-toxicity score -> POST_MAKER -> every ladder refresh KEEP/CANCEL -> actual fill gates position -> full-ladder EXIT/HOLD`。

- entry：点差、bid/ask depth、ladder concentration、mode/邻档结构与 weather shock 联合估计 trade-through 概率和“若被打到后”的 60m net value；只挂当时 best bid。
- pending：每个完整 ladder checkpoint 重算；score 变坏或最优档移动就 `CANCEL_MAKER`。quote cross 只记 `POSSIBLE_FILL`，必须用自有 order/fill 证据开仓。
- exit：30m 使用完整 ladder 的 observed relative markout、邻档传播与 mode distance 预测 30→60m incremental executable value。
- 不使用失败的 max/min selector；无正 net value 时明确 `NO_TRADE`。

## Trade-through / adverse-selection holdout

| policy | quotes | proxy fills | fill proxy rate | conditional ROI | toxic proxy rate | 1 tick/side ROI | negative fills 2x ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| anti-toxic | {proxy['selected_quotes']} | {proxy['proxy_fills']} | {proxy['proxy_fill_rate']} | {proxy['conditional_roi']} | {proxy['toxic_proxy_fill_rate']} | {proxy['one_tick_each_side_roi']} | {proxy['negative_fill_2x_roi']} |
| prior conditional selector | {legacy_proxy['selected_quotes']} | {legacy_proxy['proxy_fills']} | {legacy_proxy['proxy_fill_rate']} | {legacy_proxy['conditional_roi']} | {legacy_proxy['toxic_proxy_fill_rate']} | {legacy_proxy['one_tick_each_side_roi']} | {legacy_proxy['negative_fill_2x_roi']} |

Touch classifier holdout base rate={touch.get('base_rate')}，Brier={touch.get('brier')}，ROC-AUC={touch.get('roc_auc')}。

## Secondary holdout

| policy | positions | dates | ROI | 95% CI |
|---|---:|---:|---:|---:|
| dynamic full-ladder exit | {dynamic['positions']} | {dynamic['target_dates']} | {dynamic['roi']} | [{dynamic['ci_low']}, {dynamic['ci_high']}] |
| fixed 30m diagnostic | {safe['fixed_30']['positions']} | {safe['fixed_30']['target_dates']} | {safe['fixed_30']['roi']} | [{safe['fixed_30']['ci_low']}, {safe['fixed_30']['ci_high']}] |
| fixed 60m diagnostic | {safe['fixed_60']['positions']} | {safe['fixed_60']['target_dates']} | {safe['fixed_60']['roi']} | [{safe['fixed_60']['ci_low']}, {safe['fixed_60']['ci_high']}] |

同一 positions 上 dynamic-fixed60 ROI delta={safe['dynamic_vs_fixed_60_same_rows']['roi_delta']}，
95% CI=[{safe['dynamic_vs_fixed_60_same_rows']['ci_low']}, {safe['dynamic_vs_fixed_60_same_rows']['ci_high']}]。

## Evidence boundary

Artifact 可由 zero-notional runner 加载并生成 POST_MAKER/KEEP_MAKER/CANCEL_MAKER/POSSIBLE_FILL 与条件 EXIT/HOLD。trade-through 只是 ask 跌穿历史 best bid 的成交机会代理；queue、partial fill、真实成交和 maker fee/rebate 仍未被证明。历史时钟仍为 reconstructed，actual fills=0，不能授权 live。
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")
    return safe


def load_position_policy(path: Path, *, expected_sha256: str | None = None) -> dict[str, Any]:
    if expected_sha256 and _sha256(path) != expected_sha256:
        raise ValueError("position policy SHA-256 mismatch")
    bundle = joblib.load(path)
    if bundle.get("schema_version") not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}:
        raise ValueError(f"unsupported position policy schema: {bundle.get('schema_version')}")
    return bundle


def score_runtime_entry(
    paired_rungs: Sequence[Mapping[str, Any]],
    bundle: Mapping[str, Any],
    *,
    event_identity: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    rows = []
    for rung in paired_rungs:
        rows.append(
            {
                **dict(event_identity),
                **dict(rung),
                "forecast_event_id": str(event_identity["forecast_event_id"]),
                "entry_bid": rung.get("yes_bid"),
                "entry_ask": rung.get("yes_ask"),
                "entry_bid_size": rung.get("yes_bid_size"),
                "entry_ask_size": rung.get("yes_ask_size"),
                "entry_fee_per_share": weather_fee(float(rung["yes_ask"])),
                "h30_bid": np.nan,
                "h60_bid": np.nan,
            }
        )
    frame = add_full_ladder_position_features(pd.DataFrame(rows))
    if bundle.get("primary_policy") == "full_ladder_completion_v1":
        completion = dict(bundle.get("completion_policy") or {})
        buffer_per_set = float(
            completion.get("buffer_per_set", COMPLETION_BUFFER_PER_SET)
        )
        requested_shares = float(
            completion.get("requested_shares", COMPLETION_REQUESTED_SHARES)
        )
        frame["predicted_touch_probability"] = np.nan
        frame["predicted_touch_conditional_pnl"] = frame[
            "entry_completion_margin_1tick_per_hedge_leg"
        ]
        frame["predicted_fill_adjusted_pnl"] = frame[
            "entry_completion_margin_1tick_per_hedge_leg"
        ]
        frame["predicted_entry_net_value"] = frame[
            "entry_completion_margin_1tick_per_hedge_leg"
        ]
        records = frame.to_dict(orient="records")
        eligible = frame.loc[
            frame["entry_completion_margin_1tick_per_hedge_leg"].ge(
                buffer_per_set
            )
            & frame["entry_completion_depth_shares"].ge(requested_shares)
        ]
        if eligible.empty:
            return records, None
        selected = eligible.sort_values(
            "entry_completion_margin_1tick_per_hedge_leg", ascending=False
        ).iloc[0]
        return records, selected.to_dict()
    frame["predicted_relative_markout"] = _predict(
        bundle["entry_model"], frame, bundle["entry_features"]
    )
    projected_bid = (frame["entry_bid"] + frame["predicted_relative_markout"]).clip(0.001, 0.999)
    frame["predicted_entry_net_value"] = (
        frame["predicted_relative_markout"] - weather_fee(projected_bid)
    )
    antitoxic = "touch_model" in bundle and "touch_value_model" in bundle
    if antitoxic:
        touch_features = bundle.get("touch_features") or MICROSTRUCTURE_FEATURES
        frame["predicted_touch_probability"] = _predict_probability(
            bundle["touch_model"], frame, touch_features
        )
        frame["predicted_touch_conditional_pnl"] = _predict(
            bundle["touch_value_model"], frame, touch_features
        )
        frame["predicted_fill_adjusted_pnl"] = (
            frame["predicted_touch_probability"]
            * frame["predicted_touch_conditional_pnl"]
        )
        frame["predicted_entry_net_value"] = frame["predicted_fill_adjusted_pnl"]
    records = frame.to_dict(orient="records")
    if antitoxic:
        eligible = frame.loc[
            frame["predicted_touch_probability"].ge(
                float(bundle["touch_probability_min"])
            )
            & frame["predicted_touch_conditional_pnl"].gt(
                float(bundle["touch_conditional_pnl_min"])
            )
            & frame["predicted_fill_adjusted_pnl"].gt(0.0)
        ]
        ranking = "predicted_fill_adjusted_pnl"
    else:
        eligible = frame.loc[
            frame["predicted_entry_net_value"]
            > float(bundle["entry_threshold_net_value"])
        ]
        ranking = "predicted_entry_net_value"
    if eligible.empty:
        return records, None
    selected = eligible.sort_values(ranking, ascending=False).iloc[0]
    return records, selected.to_dict()


def score_runtime_position(
    position: Mapping[str, Any],
    current_rungs: Sequence[Mapping[str, Any]],
    bundle: Mapping[str, Any],
    *,
    elapsed_minutes: float,
) -> RuntimePositionDecision:
    if bundle.get("primary_policy") == "full_ladder_completion_v1":
        completion = dict(bundle.get("completion_policy") or {})
        requested_shares = float(
            completion.get("requested_shares", COMPLETION_REQUESTED_SHARES)
        )
        slippage = float(completion.get("hedge_slippage_per_leg", 0.001))
        held_condition = str(position.get("condition_id"))
        current = {
            str(row.get("condition_id")): row for row in current_rungs
        }
        entry_conditions = {
            str(row.get("condition_id"))
            for row in position.get("entry_ladder") or []
        }
        if set(current) != entry_conditions or held_condition not in current:
            return RuntimePositionDecision(
                "EXIT", "incomplete_completion_ladder", None, None, None, {}
            )
        other = [row for condition, row in current.items() if condition != held_condition]
        if any(_finite(row.get("yes_ask")) is None for row in other):
            return RuntimePositionDecision(
                "EXIT", "completion_hedge_ask_missing", None, None, None, {}
            )
        maker_price = float(position.get("maker_limit_price") or position.get("entry_bid"))
        hedge_cost = sum(float(row["yes_ask"]) for row in other)
        hedge_fee = sum(weather_fee(float(row["yes_ask"])) for row in other)
        cost = maker_price + hedge_cost + hedge_fee + slippage * len(other)
        margin = 1.0 - cost
        depth = min(
            (_finite(row.get("yes_ask_size")) or 0.0 for row in other),
            default=0.0,
        )
        ready = margin > 0.0 and depth >= requested_shares
        return RuntimePositionDecision(
            "HEDGE" if ready else "EXIT",
            "complete_all_other_yes" if ready else "completion_hedge_unavailable",
            margin,
            None,
            None,
            {
                "completion_cost": cost,
                "completion_margin": margin,
                "completion_depth_shares": depth,
                "completion_requested_shares": requested_shares,
            },
        )
    if elapsed_minutes >= float(bundle["hard_exit_min"]):
        return RuntimePositionDecision("EXIT", "hard_timeout", None, None, None, {})
    if elapsed_minutes < float(bundle["continuation_checkpoint_min"]):
        return RuntimePositionDecision("HOLD", "before_continuation_checkpoint", None, None, None, {})

    current = {str(row.get("condition_id")): row for row in current_rungs}
    rows = []
    for entry in position.get("entry_ladder") or []:
        observed = current.get(str(entry.get("condition_id")))
        if observed is None:
            continue
        rows.append(
            {
                key: entry.get(key)
                for key in (
                    "forecast_event_id",
                    "city",
                    "target_date",
                    "condition_id",
                    "bracket",
                    "lead_days",
                    "snapshot_epoch",
                    "model_probability_before",
                    "model_probability_after",
                    "market_probability_before",
                    "market_probability_after",
                    "entry_bid",
                    "entry_ask",
                    "entry_bid_size",
                    "entry_ask_size",
                    "entry_fee_per_share",
                )
            }
        )
        rows[-1].update(
            {
                "h30_bid": observed.get("yes_bid"),
                "h60_bid": np.nan,
                "entry_predicted_relative_markout": entry.get("predicted_relative_markout"),
            }
        )
    if len(rows) != len(position.get("entry_ladder") or []):
        return RuntimePositionDecision("EXIT", "incomplete_current_ladder", None, None, None, {})
    frame = add_full_ladder_position_features(pd.DataFrame(rows))
    for column in bundle["continuation_features"]:
        if column not in frame:
            frame[column] = np.nan
    selected = frame.loc[
        frame["condition_id"].astype(str).eq(str(position["condition_id"]))
    ]
    if selected.empty:
        return RuntimePositionDecision("EXIT", "held_rung_missing", None, None, None, {})
    row = selected.iloc[[0]].copy()
    prediction = float(
        _predict(bundle["continuation_model"], row, bundle["continuation_features"])[0]
    )
    observed_relative = _finite(row.iloc[0].get("checkpoint_relative_markout"))
    propagation = _finite(row.iloc[0].get("checkpoint_neighbor_propagation"))
    reason = "positive_continuation_value" if prediction > float(bundle["continuation_threshold_net_value"]) else "continuation_exhausted"
    action = "HOLD" if reason == "positive_continuation_value" else "EXIT"
    feature_values = {
        key: _finite(row.iloc[0].get(key)) for key in bundle["continuation_features"]
    }
    return RuntimePositionDecision(
        action,
        reason,
        prediction,
        observed_relative,
        propagation,
        feature_values,
    )


def score_runtime_pending_order(
    pending: Mapping[str, Any],
    current_rungs: Sequence[Mapping[str, Any]],
    bundle: Mapping[str, Any],
    *,
    elapsed_minutes: float,
) -> RuntimePendingOrderDecision:
    """Re-score a resting maker quote from the latest complete ladder.

    A quote cross is only a possible fill in zero-notional mode.  The caller
    must use own-order/fill evidence before changing a pending quote into a
    position.
    """

    ttl = float(bundle.get("maker_quote_ttl_min", 60.0))
    if elapsed_minutes >= ttl:
        return RuntimePendingOrderDecision(
            "CANCEL_MAKER", "maker_quote_ttl", None, None, None, None
        )
    entry_ladder = list(pending.get("entry_ladder") or [])
    current = {str(row.get("condition_id")): row for row in current_rungs}
    if not entry_ladder or set(current) != {
        str(row.get("condition_id")) for row in entry_ladder
    }:
        return RuntimePendingOrderDecision(
            "CANCEL_MAKER", "incomplete_current_ladder", None, None, None, None
        )
    if bundle.get("primary_policy") == "full_ladder_completion_v1":
        completion = dict(bundle.get("completion_policy") or {})
        held_condition = str(pending.get("condition_id"))
        other = [row for condition, row in current.items() if condition != held_condition]
        requested_shares = float(
            completion.get("requested_shares", COMPLETION_REQUESTED_SHARES)
        )
        slippage = float(completion.get("hedge_slippage_per_leg", 0.001))
        maker_price = float(pending["maker_limit_price"])
        cost = (
            maker_price
            + sum(float(row["yes_ask"]) for row in other)
            + sum(weather_fee(float(row["yes_ask"])) for row in other)
            + slippage * len(other)
        )
        margin = 1.0 - cost
        depth = min(
            (_finite(row.get("yes_ask_size")) or 0.0 for row in other),
            default=0.0,
        )
        if (
            margin < float(
                completion.get("buffer_per_set", COMPLETION_BUFFER_PER_SET)
            )
            or depth < requested_shares
        ):
            return RuntimePendingOrderDecision(
                "CANCEL_MAKER",
                "completion_margin_or_depth_deteriorated",
                held_condition,
                None,
                margin,
                margin,
            )
        possible_fill = (
            float(current[held_condition]["yes_ask"]) <= maker_price
        )
        return RuntimePendingOrderDecision(
            "POSSIBLE_FILL" if possible_fill else "KEEP_MAKER",
            (
                "trade_through_completion_hedge_ready"
                if possible_fill
                else "completion_margin_and_depth_still_ready"
            ),
            held_condition,
            None,
            margin,
            margin,
        )
    mids = {
        condition: (
            float(row["yes_bid"]) + float(row["yes_ask"])
        ) / 2.0
        for condition, row in current.items()
    }
    mid_sum = sum(mids.values())
    if mid_sum <= 0:
        return RuntimePendingOrderDecision(
            "CANCEL_MAKER", "invalid_current_ladder", None, None, None, None
        )
    paired = []
    for entry in entry_ladder:
        condition = str(entry.get("condition_id"))
        observed = current[condition]
        paired.append(
            {
                key: entry.get(key)
                for key in (
                    "condition_id",
                    "bracket",
                    "model_probability_before",
                    "model_probability_after",
                    "market_probability_before",
                )
            }
        )
        paired[-1].update(
            {
                **dict(observed),
                "market_probability_after": mids[condition] / mid_sum,
            }
        )
    identity = {
        "forecast_event_id": str(entry_ladder[0].get("forecast_event_id")),
        "city": entry_ladder[0].get("city"),
        "target_date": entry_ladder[0].get("target_date"),
        "lead_days": entry_ladder[0].get("lead_days", 1),
        "snapshot_epoch": pending.get("decision_epoch"),
    }
    _, selected = score_runtime_entry(paired, bundle, event_identity=identity)
    if selected is None:
        return RuntimePendingOrderDecision(
            "CANCEL_MAKER", "antitoxic_score_deteriorated", None, None, None, None
        )
    selected_condition = str(selected.get("condition_id"))
    held_condition = str(pending.get("condition_id"))
    if selected_condition != held_condition:
        return RuntimePendingOrderDecision(
            "CANCEL_MAKER",
            "ladder_opportunity_moved",
            selected_condition,
            _finite(selected.get("predicted_touch_probability")),
            _finite(selected.get("predicted_touch_conditional_pnl")),
            _finite(selected.get("predicted_fill_adjusted_pnl")),
        )
    held = current[held_condition]
    possible_fill = float(held["yes_ask"]) <= float(pending["maker_limit_price"])
    return RuntimePendingOrderDecision(
        "POSSIBLE_FILL" if possible_fill else "KEEP_MAKER",
        (
            "trade_through_requires_order_fill_evidence"
            if possible_fill
            else "antitoxic_score_still_positive"
        ),
        selected_condition,
        _finite(selected.get("predicted_touch_probability")),
        _finite(selected.get("predicted_touch_conditional_pnl")),
        _finite(selected.get("predicted_fill_adjusted_pnl")),
    )
