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
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


SCHEMA_VERSION = "forecast_repricing_position_policy_v1"
MODEL_ID = "forecast_repricing_full_ladder_position_v1"
ENTRY_HORIZON_MIN = 60
CONTINUATION_CHECKPOINT_MIN = 30
HARD_EXIT_MIN = 60
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
        "h30_bid",
        "h60_bid",
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
    frame["entry_fee_per_share"] = frame["entry_fee_per_share"].fillna(
        weather_fee(frame["entry_ask"])
    )

    for horizon in (30, 60):
        bid = f"h{horizon}_bid"
        move = f"h{horizon}_bid_move"
        common = f"h{horizon}_common_bid_move"
        relative = f"h{horizon}_relative_bid_move"
        frame[move] = frame[bid] - frame["entry_bid"]
        frame[common] = _event_median(frame, move)
        frame[relative] = frame[move] - frame[common]

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
    maker_threshold, threshold_table = _select_maker_threshold(oof, development)
    entries = _select_entries(
        holdout,
        "entry_predicted_relative_markout",
        execution_style="maker_fill_gated",
        threshold=maker_threshold,
    ).copy()
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

    bundle = {
        "schema_version": SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "entry_features": list(INTERACTION_FEATURES),
        "market_baseline_features": list(MARKET_BASELINE_FEATURES),
        "continuation_features": list(CONTINUATION_FEATURES),
        "entry_model": entry_model,
        "market_baseline_model": market_model,
        "continuation_model": exit_model,
        "entry_horizon_min": ENTRY_HORIZON_MIN,
        "continuation_checkpoint_min": CONTINUATION_CHECKPOINT_MIN,
        "hard_exit_min": HARD_EXIT_MIN,
        "execution_style": "maker_fill_gated",
        "entry_threshold_net_value": maker_threshold,
        "continuation_threshold_net_value": 0.0,
        "development_end": development_dates[-1],
        "holdout_start": holdout_dates[0],
    }
    return {
        "status": "runnable_zero_notional_position_policy",
        "frame": frame,
        "oof": oof,
        "holdout": holdout,
        "positions": entries,
        "bundle": bundle,
        "development_dates": development_dates,
        "holdout_dates": holdout_dates,
        "oof_delta": oof_delta,
        "holdout_delta": holdout_delta,
        "maker_threshold_selection": threshold_table,
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
        "denominator": {
            "input_rows": int(len(rows)),
            "d1_rungs": int(len(frame)),
            "forecast_events": int(frame["forecast_event_id"].nunique()),
            "target_dates": int(frame["target_date"].nunique()),
            "h30_scoreable_rungs": int(frame["h30_bid"].notna().sum()),
            "h60_scoreable_rungs": int(frame["h60_bid"].notna().sum()),
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
    if result["status"] == "blocked_insufficient_dates":
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
    result["holdout"].to_csv(output_dir / "secondary_holdout_rung_scores.csv", index=False)
    result["positions"].to_csv(output_dir / "secondary_holdout_positions.csv", index=False)
    result["maker_threshold_selection"].to_csv(
        output_dir / "development_maker_threshold_selection.csv", index=False
    )
    artifact_path = output_dir / "position_policy.joblib"
    joblib.dump(result["bundle"], artifact_path)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": result["status"],
        "input_path": str(input_path),
        "input_sha256": _sha256(input_path),
        "denominator_scope": "reconstructed D-1 forecast-event x complete-ladder rung; 30/60m direct bid coverage only",
        "denominator": result["denominator"],
        "development_dates": result["development_dates"],
        "secondary_holdout_dates": result["holdout_dates"],
        "entry_incremental_vs_market_oof": result["oof_delta"],
        "entry_incremental_vs_market_holdout": result["holdout_delta"],
        "dynamic_position": result["dynamic"],
        "fixed_30": result["fixed_30"],
        "fixed_60": result["fixed_60"],
        "maker_threshold_selection": result["maker_threshold_selection"].to_dict(
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
    report = f"""# Forecast repricing full-ladder position policy

significance={'PASS' if holdout_delta['ci_high'] is not None and holdout_delta['ci_high'] < 0 else 'FAIL'}
baseline=market-level relative-markout M0; holdout MSE delta={holdout_delta['mean']}
forward=NA; secondary reconstructed holdout only
execution=maker-fill-gated entry; dynamic 30m continuation vs executable 30/60m bid and exit Weather fee

production: live_action=none; orders_changed=0

## Runnable policy

`forecast revision -> all-rung entry score -> POST_MAKER -> actual fill gates position -> 30m full-ladder continuation score -> EXIT or HOLD -> 60m hard exit`。

- entry：`weather shock × signed mode distance × neighbor propagation` 预测 60m rung-relative bid move；只发 maker intent，未成交不建立真实 position。
- exit：30m 使用完整 ladder 的 observed relative markout、邻档传播与 mode distance 预测 30→60m incremental executable value。
- 不使用失败的 max/min selector；无正 net value 时明确 `NO_TRADE`。

## Secondary holdout

| policy | positions | dates | ROI | 95% CI |
|---|---:|---:|---:|---:|
| dynamic full-ladder exit | {dynamic['positions']} | {dynamic['target_dates']} | {dynamic['roi']} | [{dynamic['ci_low']}, {dynamic['ci_high']}] |
| fixed 30m diagnostic | {safe['fixed_30']['positions']} | {safe['fixed_30']['target_dates']} | {safe['fixed_30']['roi']} | [{safe['fixed_30']['ci_low']}, {safe['fixed_30']['ci_high']}] |
| fixed 60m diagnostic | {safe['fixed_60']['positions']} | {safe['fixed_60']['target_dates']} | {safe['fixed_60']['roi']} | [{safe['fixed_60']['ci_low']}, {safe['fixed_60']['ci_high']}] |

## Evidence boundary

Artifact 可由 zero-notional runner 加载并生成 POST_MAKER/HOLD/EXIT；表中 ROI 是 maker 已成交条件值。历史时钟仍为 reconstructed，actual fills=0，不能授权 live。
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")
    return safe


def load_position_policy(path: Path, *, expected_sha256: str | None = None) -> dict[str, Any]:
    if expected_sha256 and _sha256(path) != expected_sha256:
        raise ValueError("position policy SHA-256 mismatch")
    bundle = joblib.load(path)
    if bundle.get("schema_version") != SCHEMA_VERSION:
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
    frame["predicted_relative_markout"] = _predict(
        bundle["entry_model"], frame, bundle["entry_features"]
    )
    projected_bid = (frame["entry_bid"] + frame["predicted_relative_markout"]).clip(0.001, 0.999)
    frame["predicted_entry_net_value"] = (
        frame["predicted_relative_markout"] - weather_fee(projected_bid)
    )
    records = frame.to_dict(orient="records")
    eligible = frame.loc[
        frame["predicted_entry_net_value"] > float(bundle["entry_threshold_net_value"])
    ]
    if eligible.empty:
        return records, None
    selected = eligible.sort_values("predicted_entry_net_value", ascending=False).iloc[0]
    return records, selected.to_dict()


def score_runtime_position(
    position: Mapping[str, Any],
    current_rungs: Sequence[Mapping[str, Any]],
    bundle: Mapping[str, Any],
    *,
    elapsed_minutes: float,
) -> RuntimePositionDecision:
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
