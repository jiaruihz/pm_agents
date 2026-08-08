"""Date-safe challenger for D-1 single-YES forecast-update repricing.

The input is the candidate-level output of
``research_lmvm_forecast_innovation_v2.py``.  Models predict either the future
bid move or the fully executable taker markout.  Model, horizon, and entry
threshold are selected only on expanding target-date OOF predictions; the
last block of dates is opened once as a secondary holdout.

This module is research-only.  It writes model/evidence artifacts and never
touches a strategy runtime, order journal, or exchange API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import joblib
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


SEED = 20260809
HORIZONS = (15, 30, 60, 120)
MIN_TOTAL_DATES = 30
MIN_SELECTION_DATES = 8
MIN_SELECTION_SIGNALS = 20
OOF_BLOCK_DATES = 3


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    feature_set: str
    estimator: str
    target_mode: str
    execution_style: str


MODEL_SPECS = (
    ModelSpec("R1_weather_ridge_gross", "weather", "ridge", "gross_move", "taker"),
    ModelSpec("R2_market_ridge_gross", "market", "ridge", "gross_move", "taker"),
    ModelSpec("R3_joint_ridge_gross", "joint", "ridge", "gross_move", "taker"),
    ModelSpec("R4_joint_hgb_gross", "joint", "hgb", "gross_move", "taker"),
    ModelSpec("R5_joint_ridge_net", "joint", "ridge", "net_markout", "taker"),
    ModelSpec("R6_joint_hgb_net", "joint", "hgb", "net_markout", "taker"),
    ModelSpec("R7_maker_weather_ridge_gross", "weather", "ridge", "gross_move", "maker_conditional"),
    ModelSpec("R8_maker_joint_ridge_gross", "joint", "ridge", "gross_move", "maker_conditional"),
    ModelSpec("R9_maker_joint_hgb_gross", "joint", "hgb", "gross_move", "maker_conditional"),
    ModelSpec("R10_maker_joint_hgb_net", "joint", "hgb", "net_markout", "maker_conditional"),
)

WEATHER_NUMERIC = (
    "model_probability_before",
    "model_probability_after",
    "model_probability_delta",
    "model_prob",
    "forecast_max_f",
    "decision_hour_sin",
    "decision_hour_cos",
    "rung_count",
)
MARKET_NUMERIC = (
    "market_probability_before",
    "market_probability_after",
    "market_probability_delta",
    "market_prob",
    "entry_bid",
    "entry_ask",
    "entry_spread",
    "entry_fee_per_share",
    "estimated_exit_fee_per_share",
    "execution_hurdle",
    "log_entry_bid_size",
    "log_entry_ask_size",
    "decision_hour_sin",
    "decision_hour_cos",
    "rung_count",
)
CATEGORICAL = ("city", "forecast_source", "forecast_model")


def weather_fee(price: pd.Series | np.ndarray | float) -> Any:
    return 0.05 * price * (1.0 - price)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unknown"


def prepare_candidates(rows: pd.DataFrame) -> pd.DataFrame:
    required = {
        "policy", "lead_days", "target_date", "snapshot_epoch", "city",
        "entry_bid", "entry_ask", "entry_fee_per_share",
        "forecast_innovation_score", "model_probability_delta",
        "market_probability_delta",
    }
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"candidate input missing required columns: {missing}")
    frame = rows[
        rows["policy"].eq("forecast_innovation_argmax")
        & rows["lead_days"].eq(1)
    ].copy()
    frame["target_date"] = frame["target_date"].astype(str)
    frame["entry_spread"] = frame["entry_ask"] - frame["entry_bid"]
    frame["estimated_exit_fee_per_share"] = weather_fee(frame["entry_bid"])
    frame["execution_hurdle"] = (
        frame["entry_spread"]
        + frame["entry_fee_per_share"]
        + frame["estimated_exit_fee_per_share"]
    )
    for column in (
        "entry_bid_size", "entry_ask_size", "decision_hour_local",
        "forecast_source", "forecast_model", "forecast_max_f",
        "model_edge_after_entry_fee", "rung_count", "model_prob", "market_prob",
        "model_probability_before", "model_probability_after",
        "market_probability_before", "market_probability_after",
    ):
        if column not in frame:
            frame[column] = np.nan
    for canonical, legacy in (
        ("model_probability_after", "model_prob"),
        ("market_probability_after", "market_prob"),
    ):
        if frame[canonical].notna().sum() == 0:
            frame[canonical] = pd.to_numeric(frame[legacy], errors="coerce")
    frame["log_entry_bid_size"] = np.log1p(
        pd.to_numeric(frame["entry_bid_size"], errors="coerce").clip(lower=0)
    )
    frame["log_entry_ask_size"] = np.log1p(
        pd.to_numeric(frame["entry_ask_size"], errors="coerce").clip(lower=0)
    )
    hour = pd.to_numeric(frame["decision_hour_local"], errors="coerce")
    frame["decision_hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    frame["decision_hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    for column in CATEGORICAL:
        frame[column] = frame[column].fillna("<missing>").astype(str)
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame.sort_values(["target_date", "snapshot_epoch", "city"]).reset_index(drop=True)


def feature_columns(spec: ModelSpec) -> tuple[list[str], list[str]]:
    if spec.feature_set == "weather":
        numeric = list(WEATHER_NUMERIC)
    elif spec.feature_set == "market":
        numeric = list(MARKET_NUMERIC)
    elif spec.feature_set == "joint":
        numeric = list(
            dict.fromkeys(
                (*WEATHER_NUMERIC, *MARKET_NUMERIC, "forecast_innovation_score", "model_edge_after_entry_fee")
            )
        )
    else:
        raise ValueError(spec.feature_set)
    return numeric, list(CATEGORICAL)


def build_model(spec: ModelSpec) -> Pipeline:
    numeric, categorical = feature_columns(spec)
    numeric_pipe = Pipeline(
        [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]
    )
    categorical_pipe = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5, sparse_output=False)),
        ]
    )
    preprocess = ColumnTransformer(
        [("numeric", numeric_pipe, numeric), ("categorical", categorical_pipe, categorical)],
        remainder="drop",
        sparse_threshold=0.0,
    )
    if spec.estimator == "ridge":
        estimator = Ridge(alpha=10.0)
    elif spec.estimator == "hgb":
        estimator = HistGradientBoostingRegressor(
            learning_rate=0.04,
            max_iter=120,
            max_depth=3,
            min_samples_leaf=30,
            l2_regularization=10.0,
            random_state=SEED,
        )
    else:
        raise ValueError(spec.estimator)
    return Pipeline([("preprocess", preprocess), ("model", estimator)])


def label_column(spec: ModelSpec, horizon: int) -> str:
    if spec.target_mode == "gross_move":
        return f"h{horizon}_gross_bid_move"
    return f"h{horizon}_{spec.execution_style}_net_markout_per_share"


def add_targets(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for horizon in HORIZONS:
        result[f"h{horizon}_gross_bid_move"] = result[f"h{horizon}_bid"] - result["entry_bid"]
        result[f"h{horizon}_taker_net_markout_per_share"] = result[
            f"h{horizon}_net_markout_per_share"
        ]
        result[f"h{horizon}_maker_conditional_net_markout_per_share"] = (
            result[f"h{horizon}_bid"]
            - result[f"h{horizon}_exit_fee_per_share"]
            - result["entry_bid"]
        )
    return result


def date_equal_weights(rows: pd.DataFrame) -> np.ndarray:
    counts = rows.groupby("target_date")["target_date"].transform("size").astype(float)
    return (1.0 / counts).to_numpy()


def fit_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    spec: ModelSpec,
    horizon: int,
) -> np.ndarray:
    label = label_column(spec, horizon)
    usable = train[train[label].notna()].copy()
    if usable["target_date"].nunique() < 3 or len(usable) < 30:
        return np.full(len(test), np.nan)
    numeric, categorical = feature_columns(spec)
    columns = numeric + categorical
    model = build_model(spec)
    model.fit(
        usable[columns],
        usable[label].astype(float),
        model__sample_weight=date_equal_weights(usable),
    )
    raw = model.predict(test[columns])
    if spec.target_mode == "gross_move":
        hurdle = (
            test["execution_hurdle"]
            if spec.execution_style == "taker"
            else test["estimated_exit_fee_per_share"]
        )
        return raw - hurdle.to_numpy(float)
    return raw


def fit_frozen_model(rows: pd.DataFrame, spec: ModelSpec, horizon: int) -> Pipeline:
    label = label_column(spec, horizon)
    usable = rows[rows[label].notna()].copy()
    numeric, categorical = feature_columns(spec)
    model = build_model(spec)
    model.fit(
        usable[numeric + categorical],
        usable[label].astype(float),
        model__sample_weight=date_equal_weights(usable),
    )
    return model


def score_frozen_rows(
    rows: pd.DataFrame,
    model: Pipeline,
    spec: ModelSpec,
    threshold: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = prepare_candidates(rows)
    numeric, categorical = feature_columns(spec)
    raw = model.predict(frame[numeric + categorical])
    if spec.target_mode == "gross_move":
        hurdle = (
            frame["execution_hurdle"]
            if spec.execution_style == "taker"
            else frame["estimated_exit_fee_per_share"]
        )
        frame["predicted_net_markout"] = raw - hurdle.to_numpy(float)
    else:
        frame["predicted_net_markout"] = raw
    frame["entry_threshold"] = float(threshold)
    frame["threshold_cross"] = frame["predicted_net_markout"].ge(float(threshold))
    selected = first_threshold_cross(frame, float(threshold))
    return frame, selected


def expanding_oof(
    development: pd.DataFrame,
    spec: ModelSpec,
    horizon: int,
    min_train_dates: int,
) -> pd.DataFrame:
    dates = sorted(development["target_date"].unique())
    predictions: list[pd.DataFrame] = []
    for index in range(min_train_dates, len(dates), OOF_BLOCK_DATES):
        train_dates = dates[:index]
        test_dates = dates[index : index + OOF_BLOCK_DATES]
        train = development[development["target_date"].isin(train_dates)]
        test = development[development["target_date"].isin(test_dates)].copy()
        test["predicted_net_markout"] = fit_predict(train, test, spec, horizon)
        test["model_id"] = spec.model_id
        test["horizon_min"] = horizon
        test["max_train_date"] = train_dates[-1]
        predictions.append(test)
    return pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame()


def first_threshold_cross(rows: pd.DataFrame, threshold: float) -> pd.DataFrame:
    qualified = rows[rows["predicted_net_markout"].ge(threshold)].copy()
    if qualified.empty:
        return qualified
    return (
        qualified.sort_values(["target_date", "city", "snapshot_epoch"])
        .groupby(["target_date", "city"], as_index=False, sort=False)
        .head(1)
    )


def roi_stats(
    rows: pd.DataFrame,
    horizon: int,
    execution_style: str,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    shares = f"h{horizon}_executable_shares"
    markout = f"h{horizon}_{execution_style}_net_markout_per_share"
    usable = rows[rows[markout].notna() & rows[shares].gt(0)].copy()
    usable["strategy_pnl_usd"] = usable[markout] * usable[shares]
    entry_per_share = (
        usable["entry_ask"] + usable["entry_fee_per_share"]
        if execution_style == "taker"
        else usable["entry_bid"]
    )
    usable["entry_cost_usd"] = entry_per_share * usable[shares]
    by_date = usable.groupby("target_date", as_index=False).agg(
        pnl=("strategy_pnl_usd", "sum"),
        cost=("entry_cost_usd", "sum"),
        signals=("strategy_pnl_usd", "size"),
    )
    cost = float(by_date["cost"].sum()) if len(by_date) else 0.0
    point = float(by_date["pnl"].sum() / cost) if cost else math.nan
    boot: list[float] = []
    if len(by_date) >= 3 and draws:
        values = by_date[["pnl", "cost"]].to_numpy(float)
        rng = np.random.default_rng(seed)
        for _ in range(draws):
            sample = values[rng.integers(0, len(values), size=len(values))]
            denominator = float(sample[:, 1].sum())
            if denominator:
                boot.append(float(sample[:, 0].sum() / denominator))
    return {
        "signals": int(len(usable)),
        "target_dates": int(len(by_date)),
        "cost_usd": cost,
        "pnl_usd": float(by_date["pnl"].sum()) if len(by_date) else 0.0,
        "roi": point,
        "ci_low": float(np.percentile(boot, 2.5)) if boot else math.nan,
        "ci_high": float(np.percentile(boot, 97.5)) if boot else math.nan,
        "positive_signal_rate": float((usable["strategy_pnl_usd"] > 0).mean()) if len(usable) else math.nan,
        "positive_date_rate": float((by_date["pnl"] > 0).mean()) if len(by_date) else math.nan,
    }


def threshold_grid(predictions: pd.Series) -> list[tuple[str, float]]:
    clean = predictions.dropna().astype(float)
    if clean.empty:
        return []
    values = [("zero", 0.0)]
    for quantile in (0.50, 0.70, 0.80, 0.90, 0.95):
        values.append((f"q{int(quantile * 100)}", float(clean.quantile(quantile))))
    unique: dict[float, str] = {}
    for name, value in values:
        unique.setdefault(round(value, 10), name)
    return [(name, value) for value, name in unique.items()]


def select_configuration(
    predictions: pd.DataFrame, draws: int
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (model_id, horizon), group in predictions.groupby(["model_id", "horizon_min"]):
        spec = next(item for item in MODEL_SPECS if item.model_id == model_id)
        for threshold_name, threshold in threshold_grid(group["predicted_net_markout"]):
            selected = first_threshold_cross(group, threshold)
            stats = roi_stats(
                selected,
                int(horizon),
                spec.execution_style,
                draws,
                SEED + int(horizon),
            )
            rows.append(
                {
                    "model_id": model_id,
                    "execution_style": spec.execution_style,
                    "horizon_min": int(horizon),
                    "threshold_name": threshold_name,
                    "threshold": threshold,
                    **stats,
                }
            )
    tournament = pd.DataFrame(rows)
    eligible = tournament[
        tournament["signals"].ge(MIN_SELECTION_SIGNALS)
        & tournament["target_dates"].ge(MIN_SELECTION_DATES)
    ].copy()
    if eligible.empty:
        raise ValueError("no development configuration meets minimum signals/dates")
    freeze_eligible = eligible[
        eligible["roi"].gt(0)
        & eligible["positive_date_rate"].ge(0.50)
    ].copy()
    selection_pool = freeze_eligible if not freeze_eligible.empty else eligible
    selection_pool["selection_score"] = selection_pool["ci_low"].fillna(-np.inf)
    best = selection_pool.sort_values(
        ["selection_score", "roi", "target_dates", "signals"], ascending=False
    ).iloc[0].to_dict()
    best["development_freeze_eligible"] = bool(not freeze_eligible.empty)
    return tournament, best


def run_tournament(
    rows: pd.DataFrame,
    *,
    min_train_dates: int = 15,
    holdout_fraction: float = 0.20,
    draws: int = 2_000,
) -> dict[str, Any]:
    frame = add_targets(prepare_candidates(rows))
    dates = sorted(frame["target_date"].unique())
    coverage_dates = {
        horizon: int(frame.loc[frame[f"h{horizon}_net_markout_per_share"].notna(), "target_date"].nunique())
        for horizon in HORIZONS
    }
    eligible_horizons = [
        horizon for horizon, count in coverage_dates.items() if count >= MIN_TOTAL_DATES
    ]
    if len(dates) < MIN_TOTAL_DATES or not eligible_horizons:
        return {
            "status": "blocked_insufficient_target_dates",
            "required_target_dates": MIN_TOTAL_DATES,
            "available_target_dates": len(dates),
            "markout_target_dates_by_horizon": coverage_dates,
            "available_rows": len(frame),
            "date_start": dates[0] if dates else None,
            "date_end": dates[-1] if dates else None,
        }
    holdout_count = max(6, int(math.ceil(len(dates) * holdout_fraction)))
    development_dates = dates[:-holdout_count]
    holdout_dates = dates[-holdout_count:]
    if len(development_dates) <= min_train_dates + MIN_SELECTION_DATES:
        raise ValueError("not enough development dates after frozen holdout split")
    development = frame[frame["target_date"].isin(development_dates)].copy()
    holdout = frame[frame["target_date"].isin(holdout_dates)].copy()

    oof_parts: list[pd.DataFrame] = []
    for spec in MODEL_SPECS:
        for horizon in eligible_horizons:
            part = expanding_oof(development, spec, horizon, min_train_dates)
            if not part.empty:
                oof_parts.append(part)
    oof = pd.concat(oof_parts, ignore_index=True)
    tournament, selected = select_configuration(oof, draws)
    spec = next(item for item in MODEL_SPECS if item.model_id == selected["model_id"])
    horizon = int(selected["horizon_min"])
    holdout_scored = holdout.copy()
    holdout_scored["predicted_net_markout"] = fit_predict(
        development, holdout_scored, spec, horizon
    )
    holdout_scored["model_id"] = spec.model_id
    holdout_scored["horizon_min"] = horizon
    selected_holdout = first_threshold_cross(holdout_scored, float(selected["threshold"]))
    holdout_stats = roi_stats(
        selected_holdout,
        horizon,
        spec.execution_style,
        draws,
        SEED + 9000 + horizon,
    )
    baseline = (
        holdout.sort_values(["target_date", "city", "snapshot_epoch"])
        .groupby(["target_date", "city"], as_index=False, sort=False)
        .head(1)
    )
    baseline_stats = roi_stats(
        baseline,
        horizon,
        spec.execution_style,
        draws,
        SEED + 10000 + horizon,
    )

    label = f"h{horizon}_{spec.execution_style}_net_markout_per_share"
    calibration_rows = holdout_scored[
        holdout_scored["predicted_net_markout"].notna()
        & holdout_scored[label].notna()
    ]
    calibration = {
        "mean_predicted_net_markout": float(calibration_rows["predicted_net_markout"].mean()) if len(calibration_rows) else math.nan,
        "mean_actual_net_markout": float(calibration_rows[label].mean()) if len(calibration_rows) else math.nan,
        "mae": float((calibration_rows["predicted_net_markout"] - calibration_rows[label]).abs().mean()) if len(calibration_rows) else math.nan,
    }
    freeze_ok = (
        holdout_stats["signals"] >= MIN_SELECTION_SIGNALS
        and holdout_stats["target_dates"] >= 6
        and math.isfinite(holdout_stats["roi"])
        and holdout_stats["roi"] > 0
        and holdout_stats["roi"] > baseline_stats["roi"]
        and holdout_stats["positive_date_rate"] >= 0.50
        and selected["roi"] > 0
        and selected["positive_date_rate"] >= 0.50
    )
    frozen_model = fit_frozen_model(frame, spec, horizon) if freeze_ok else None
    status = (
        "freeze_candidate"
        if freeze_ok and spec.execution_style == "taker"
        else "maker_probe_candidate"
        if freeze_ok
        else "challenger_rejected"
    )
    return {
        "status": status,
        "frame": frame,
        "oof_predictions": oof,
        "tournament": tournament,
        "development_dates": development_dates,
        "holdout_dates": holdout_dates,
        "selected": selected,
        "holdout_predictions": holdout_scored,
        "selected_holdout": selected_holdout,
        "holdout": holdout_stats,
        "baseline": baseline_stats,
        "calibration": calibration,
        "model_spec": asdict(spec),
        "frozen_model": frozen_model,
        "multiple_testing_arms": len(MODEL_SPECS) * len(eligible_horizons),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def write_outputs(result: dict[str, Any], input_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    if result["status"] == "blocked_insufficient_target_dates":
        summary = {
            **result,
            "generated_at_utc": generated_at,
            "input_path": str(input_path),
            "input_sha256": _sha256(input_path),
            "code_revision": _git_sha(),
            "production": {"live_action": "none", "orders_changed": 0},
        }
        (output_dir / "summary.json").write_text(
            json.dumps(_json_safe(summary), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (output_dir / "report.md").write_text(
            "# LMVM repricing challenger\n\n"
            "significance=BLOCKED\n\ncalibration=BLOCKED\n\n"
            "baseline=NOT_RUN\n\nforward=NOT_RUN\n\n"
            "production: live_action=none; orders_changed=0\n\n"
            f"只有 {result['available_target_dates']} 个 target dates / {result['available_rows']} rows，"
            f"低于合同要求的 {result['required_target_dates']} 个日期。\n",
            encoding="utf-8",
        )
        return

    for key, name in (
        ("oof_predictions", "development_oof_predictions.csv"),
        ("tournament", "development_tournament.csv"),
        ("holdout_predictions", "holdout_predictions.csv"),
        ("selected_holdout", "selected_holdout_signals.csv"),
    ):
        result[key].to_csv(output_dir / name, index=False)
    summary = {
        "schema_version": "lmvm_repricing_challenger_v1",
        "generated_at_utc": generated_at,
        "input_path": str(input_path),
        "input_sha256": _sha256(input_path),
        "code_revision": _git_sha(),
        "status": result["status"],
        "denominator_scope": "forecast_innovation_argmax; D-1; PIT snapshot candidate; first threshold cross per city-target_date",
        "development_dates": result["development_dates"],
        "holdout_dates": result["holdout_dates"],
        "model_spec": result["model_spec"],
        "selected": result["selected"],
        "holdout": result["holdout"],
        "baseline": result["baseline"],
        "calibration": result["calibration"],
        "multiple_testing_arms": result["multiple_testing_arms"],
        "freeze_rule": "development OOF ROI>0 and secondary holdout ROI>0, holdout beats mechanical baseline, both positive-date rates>=50%, with >=20 signals across >=6 holdout dates; fresh forward still required",
        "production": {"live_action": "none", "orders_changed": 0},
    }
    (output_dir / "summary.json").write_text(
        json.dumps(_json_safe(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    candidate = {
        "schema_version": "lmvm_repricing_candidate_model_v1",
        "status": result["status"],
        "model_spec": result["model_spec"],
        "horizon_min": int(result["selected"]["horizon_min"]),
        "entry_threshold_predicted_net_markout": float(result["selected"]["threshold"]),
        "entry_policy": "first threshold cross per city-target_date",
        "feature_clock": "decision snapshot only",
        "execution_assumption": (
            "taker ask entry; future executable bid exit; both Weather fees"
            if result["model_spec"]["execution_style"] == "taker"
            else "conditional maker fill at entry best bid; future executable bid exit with Weather taker fee; maker fill probability not assumed"
        ),
        "development_end": result["development_dates"][-1],
        "holdout_start": result["holdout_dates"][0],
        "input_sha256": summary["input_sha256"],
        "code_revision": summary["code_revision"],
    }
    (output_dir / "candidate_model.json").write_text(
        json.dumps(_json_safe(candidate), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if result["status"] in {"freeze_candidate", "maker_probe_candidate"}:
        stem = "frozen_model" if result["status"] == "freeze_candidate" else "maker_probe_model"
        model_path = output_dir / f"{stem}.joblib"
        joblib.dump(result["frozen_model"], model_path)
        frozen_payload = {
            **candidate,
            "model_path": model_path.name,
            "model_sha256": _sha256(model_path),
            "fit_scope": "all recovered historical rows after secondary-holdout gate; for fresh forward only",
        }
        (output_dir / f"{stem}.json").write_text(
            json.dumps(_json_safe(frozen_payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    holdout = result["holdout"]
    baseline = result["baseline"]
    selected = result["selected"]
    calibration = result["calibration"]
    report = f"""# LMVM D-1 repricing challenger v1

significance={'PASS_POINT_ONLY' if result['status'] in {'freeze_candidate', 'maker_probe_candidate'} else 'FAIL'}; holdout ROI={holdout['roi']:.2%}, target-date bootstrap 95% CI=[{holdout['ci_low']:.2%}, {holdout['ci_high']:.2%}]

calibration=holdout predicted mean {calibration['mean_predicted_net_markout']:+.4f} vs actual {calibration['mean_actual_net_markout']:+.4f}; MAE={calibration['mae']:.4f}

baseline=matching `{result['model_spec']['execution_style']}` first mechanical innovation candidate per city-day ROI {baseline['roi']:.2%}; challenger delta {holdout['roi'] - baseline['roi']:+.2%}

forward=secondary chronological holdout {result['holdout_dates'][0]}..{result['holdout_dates'][-1]}; fresh frozen forward still required

production: live_action=none; orders_changed=0

## 固定设计

- 分母：D-1 `forecast_innovation_argmax` 更新事件；execution style=`{result['model_spec']['execution_style']}`，{int(selected['horizon_min'])}m 首个容差内 bid 退出。
- 训练：{len(result['development_dates'])} 个 development target dates；严格 expanding-date OOF。
- 盲开：最后 {len(result['holdout_dates'])} 个日期只在模型、horizon 和阈值冻结后评分。
- 赛马：{result['multiple_testing_arms']} 个 model×horizon arms；阈值只在 development OOF 选择。
- 防重叠：每个 city×target_date 只取第一次预测穿越阈值的信号。

## 选中版本

`{selected['model_id']}`，horizon={int(selected['horizon_min'])}m，predicted-net threshold={selected['threshold']:+.4f}。

| period | signals | dates | cost | PnL | ROI | positive dates | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| development OOF | {int(selected['signals'])} | {int(selected['target_dates'])} | ${selected['cost_usd']:.2f} | ${selected['pnl_usd']:+.2f} | {selected['roi']:.2%} | {selected['positive_date_rate']:.1%} | [{selected['ci_low']:.2%}, {selected['ci_high']:.2%}] |
| secondary holdout | {holdout['signals']} | {holdout['target_dates']} | ${holdout['cost_usd']:.2f} | ${holdout['pnl_usd']:+.2f} | {holdout['roi']:.2%} | {holdout['positive_date_rate']:.1%} | [{holdout['ci_low']:.2%}, {holdout['ci_high']:.2%}] |
| mechanical first-update baseline | {baseline['signals']} | {baseline['target_dates']} | ${baseline['cost_usd']:.2f} | ${baseline['pnl_usd']:+.2f} | {baseline['roi']:.2%} | {baseline['positive_date_rate']:.1%} | [{baseline['ci_low']:.2%}, {baseline['ci_high']:.2%}] |

## 判定

`{result['status']}`。`freeze_candidate` 只表示 taker 规则值得锁定后积累 clean forward；`maker_probe_candidate` 只表示 conditional quote return 值得冻结并采集真实 queue/fill，绝不等于已实现 ROI。两者都不是 confirmed alpha，也不允许直接 live。
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-train-dates", type=int, default=15)
    parser.add_argument("--holdout-fraction", type=float, default=0.20)
    parser.add_argument("--draws", type=int, default=2_000)
    parser.add_argument(
        "--score-model-dir",
        type=Path,
        help="score candidate rows with an existing frozen/probe artifact instead of training",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = pd.read_csv(args.candidate_csv, low_memory=False)
    if args.score_model_dir is not None:
        json_candidates = (
            args.score_model_dir / "maker_probe_model.json",
            args.score_model_dir / "frozen_model.json",
        )
        meta_path = next((path for path in json_candidates if path.exists()), None)
        if meta_path is None:
            raise FileNotFoundError(f"no model metadata under {args.score_model_dir}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        model_path = args.score_model_dir / str(meta["model_path"])
        if _sha256(model_path) != str(meta["model_sha256"]):
            raise ValueError("model artifact SHA-256 mismatch")
        spec = ModelSpec(**meta["model_spec"])
        scored, selected = score_frozen_rows(
            rows,
            joblib.load(model_path),
            spec,
            float(meta["entry_threshold_predicted_net_markout"]),
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        scored.to_csv(args.output_dir / "forward_scores.csv", index=False)
        selected.to_csv(args.output_dir / "forward_candidates.csv", index=False)
        payload = {
            "schema_version": "lmvm_repricing_forward_score_v1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "model_meta": str(meta_path),
            "model_sha256": meta["model_sha256"],
            "input_path": str(args.candidate_csv),
            "input_sha256": _sha256(args.candidate_csv),
            "score_rows": len(scored),
            "threshold_cross_rows": int(scored["threshold_cross"].sum()),
            "first_cross_candidates": len(selected),
            "execution_style": spec.execution_style,
            "production": {"live_action": "none", "orders_changed": 0},
        }
        (args.output_dir / "forward_summary.json").write_text(
            json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    result = run_tournament(
        rows,
        min_train_dates=args.min_train_dates,
        holdout_fraction=args.holdout_fraction,
        draws=args.draws,
    )
    write_outputs(result, args.candidate_csv, args.output_dir)
    print(json.dumps({"status": result["status"], "output_dir": str(args.output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
