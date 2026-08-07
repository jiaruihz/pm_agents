#!/usr/bin/env python3
"""Transition-confirmation residual challenger for frozen Core Carry.

The archived opportunity ledger is a fixed PIT denominator.  Core's hold logit
is an immutable offset; this module learns one regularized continuous residual
and never changes strategy eligibility or production configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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

from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)
from weather_data_feed.market_brackets import parse_market_bracket  # noqa: E402


RESEARCH_ID = "current_yes_core_carry_transition_confirmation_challenger_v1"
RUN_ID = "historical_parent_1349_state_entry_offset_ridge_20260807"
INPUT = ROOT / (
    "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_overshoot_risk_sizing_overlay_v1/"
    "opportunity_ledger.csv"
)
PREREG = ROOT / (
    "docs/analysis/2026-08/"
    "2026-08-07-current-yes-core-carry-transition-confirmation-"
    "challenger-preregistration.json"
)
REPORT = ROOT / (
    "docs/analysis/2026-08/"
    "2026-08-07-current-yes-core-carry-transition-confirmation-"
    "challenger-v1.md"
)
RESULT = REPORT.with_suffix(".json")

SEED = 20260807
WARMUP_DATES = 8
FORWARD_DATES = 8
BOOTSTRAP_REPS = 5000
L2 = 0.5
EPS = 1e-6

FEATURE_COLUMNS = [
    "upper_exit_distance_ticks",
    "forecast_upper_exit_margin_ticks",
    "fresh_high_remaining_heat",
    "dip_rebound_score",
    "forecast_exit_during_pullback",
    "forecast_reheat_runway",
    "confirmed_reheat_runway",
    "peak_ahead_heat",
    "plateau_confirmation",
    "wind_boost_transition_risk",
]
FORECAST_UNDERPREDICTION_FEATURE = "forecast_underprediction_ticks"


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


def upper_exit_geometry(
    unit: str,
    bracket_label: str,
    running_native: float,
    forecast_max_native: float,
) -> tuple[float, float, float]:
    """Return running distance and forecast margin to the next exact bracket.

    Celsius markets are mapped to their underlying integer-F observation
    lattice. Fahrenheit ranges already use integer-F ticks.
    """
    bracket = parse_market_bracket(str(bracket_label))
    if bracket is None or bracket.high is None or bracket.top or bracket.bottom:
        raise ValueError(f"expected bounded exact bracket: {bracket_label!r}")
    unit = str(unit).upper()
    if unit == "C":
        threshold_f = math.ceil((float(bracket.high) + 0.5) * 9.0 / 5.0 + 32.0 - EPS)
        running_f = math.floor(float(running_native) * 9.0 / 5.0 + 32.0 + 0.5)
        forecast_f = float(forecast_max_native) * 9.0 / 5.0 + 32.0
        return (
            float(threshold_f - running_f),
            float(forecast_f - threshold_f),
            float(running_f - forecast_f),
        )
    if unit == "F":
        threshold_f = float(bracket.high) + 1.0
        return (
            float(threshold_f - float(running_native)),
            float(forecast_max_native) - threshold_f,
            float(running_native) - float(forecast_max_native),
        )
    raise ValueError(f"unsupported temperature unit: {unit!r}")


def add_transition_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add deterministic pre-settlement features without reading the label."""
    output = frame.copy()
    geometry = [
        upper_exit_geometry(unit, bracket, running, forecast)
        for unit, bracket, running, forecast in zip(
            output["unit"],
            output["current_bracket"],
            output["running_native"],
            output["forecast_max_native"],
            strict=True,
        )
    ]
    output["upper_exit_distance_ticks"] = np.clip(
        [item[0] for item in geometry], -2.0, 10.0
    )
    output["forecast_upper_exit_margin_ticks"] = np.clip(
        [item[1] for item in geometry], -10.0, 10.0
    )
    output[FORECAST_UNDERPREDICTION_FEATURE] = np.clip(
        [item[2] for item in geometry], 0.0, 10.0
    )

    numeric = lambda name: pd.to_numeric(output[name], errors="coerce")
    daylight_h = numeric("daylight_remaining_minutes").clip(0, 900) / 60.0
    solar_fraction = numeric("solar_elevation_deg").clip(0, 90) / 90.0
    solar_heat = solar_fraction * np.sqrt(daylight_h)
    age_h = numeric("minutes_since_last_strict_new_high").clip(0, 1440) / 60.0
    fresh_high = np.exp(-age_h / 1.5)
    pullback = (-numeric("temp_trend_1h_f")).clip(0, 10)
    multi_hour_warming = (numeric("temp_trend_3h_f") / 3.0).clip(0, 10)
    acceleration = numeric("temp_curve_acceleration_f").clip(0, 6)
    reheat_flag = numeric("reheating_transition_num").fillna(0).clip(0, 1)
    rebound = multi_hour_warming + 0.5 * acceleration + reheat_flag
    forecast_exit_risk = output["forecast_upper_exit_margin_ticks"].clip(lower=0)

    output["fresh_high_remaining_heat"] = (fresh_high * solar_heat).clip(0, 6)
    output["dip_rebound_score"] = (pullback * rebound * solar_heat).clip(0, 40)
    output["forecast_exit_during_pullback"] = (
        forecast_exit_risk * (1.0 + pullback) * solar_heat
    ).clip(0, 40)
    output["forecast_reheat_runway"] = (
        numeric("forecast_reheat_after_now_f").clip(0, 8) * solar_heat
    ).clip(0, 30)
    output["confirmed_reheat_runway"] = (
        reheat_flag
        * (1.0 + numeric("temp_trend_1h_f").clip(0, 8))
        * solar_heat
    ).clip(0, 30)
    output["peak_ahead_heat"] = (
        numeric("forecast_peak_delta_hours_local").clip(0, 12) * solar_heat
    ).clip(0, 30)
    same_high_count = numeric("same_running_max_obs_count").clip(1, 40)
    output["plateau_confirmation"] = (
        np.log1p(same_high_count)
        * np.log1p(1.0 + age_h)
        * (1.0 + pullback)
        / (1.0 + rebound + forecast_exit_risk)
    ).clip(0, 30)

    core = numeric("p_core_no_obs_age").clip(EPS, 1 - EPS)
    no_wind = numeric("p_core_no_wind").clip(EPS, 1 - EPS)
    wind_contribution = np.log(core / (1 - core)) - np.log(no_wind / (1 - no_wind))
    transition_risk = (
        forecast_exit_risk
        + output["dip_rebound_score"]
        + output["fresh_high_remaining_heat"] * rebound
    ).clip(0, 40)
    output["wind_boost_transition_risk"] = (
        wind_contribution.clip(0, 3) * transition_risk
    ).clip(0, 40)
    return output


def load_ledger() -> pd.DataFrame:
    frame = pd.read_csv(INPUT, low_memory=False)
    frame["target_date"] = frame["target_date"].astype(str)
    frame["current_bracket"] = frame["current_bracket"].astype(str)
    frame["decision_snapshot_dt"] = pd.to_datetime(
        frame["decision_snapshot_ts_utc"], utc=True, errors="raise"
    )
    frame["label"] = frame["label"].astype(int)
    frame["p_core_hold"] = frame["p_core_no_obs_age"].clip(EPS, 1 - EPS)
    frame["p_market_hold"] = frame["market_mid"].clip(EPS, 1 - EPS)
    frame["base_logit"] = np.log(frame["p_core_hold"] / (1 - frame["p_core_hold"]))
    frame = add_transition_features(frame)
    return frame.sort_values(
        ["target_date", "city", "current_bracket", "decision_snapshot_dt"]
    ).reset_index(drop=True)


def state_entry_weights(frame: pd.DataFrame) -> np.ndarray:
    """Equal target dates, equal state entries per date, equal rows per state."""
    keys = ["city", "target_date", "current_bracket"]
    state_rows = frame.groupby(keys)["label"].transform("size").to_numpy(float)
    states_per_date = (
        frame[keys]
        .drop_duplicates()
        .groupby("target_date")["city"]
        .size()
        .to_dict()
    )
    date_count = frame["target_date"].nunique()
    weights = np.array(
        [
            1.0 / (date_count * states_per_date[target_date] * row_count)
            for target_date, row_count in zip(
                frame["target_date"], state_rows, strict=True
            )
        ],
        dtype=float,
    )
    return weights / weights.sum()


def transform_features(
    train: pd.DataFrame, test: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    train_columns: list[np.ndarray] = []
    test_columns: list[np.ndarray] = []
    names: list[str] = []
    transforms: dict[str, Any] = {}
    for column in FEATURE_COLUMNS:
        train_raw = pd.to_numeric(train[column], errors="coerce")
        test_raw = pd.to_numeric(test[column], errors="coerce")
        median = float(train_raw.median()) if train_raw.notna().any() else 0.0
        train_value = train_raw.fillna(median).to_numpy(float)
        test_value = test_raw.fillna(median).to_numpy(float)
        mean = float(train_value.mean())
        scale = float(train_value.std())
        if not math.isfinite(scale) or scale < 1e-9:
            continue
        train_columns.append((train_value - mean) / scale)
        test_columns.append((test_value - mean) / scale)
        names.append(column)
        transforms[column] = {"median": median, "mean": mean, "scale": scale}
        train_missing = train_raw.isna().to_numpy(float)
        missing_scale = float(train_missing.std())
        if missing_scale >= 1e-9:
            missing_name = f"{column}__missing"
            train_columns.append((train_missing - train_missing.mean()) / missing_scale)
            test_columns.append(
                (test_raw.isna().to_numpy(float) - train_missing.mean()) / missing_scale
            )
            names.append(missing_name)
            transforms[missing_name] = {
                "mean": float(train_missing.mean()),
                "scale": missing_scale,
            }
    if not train_columns:
        raise RuntimeError("no varying challenger features")
    return (
        np.column_stack(train_columns),
        np.column_stack(test_columns),
        {"columns": names, "transforms": transforms},
    )


def fit_predict(
    train: pd.DataFrame, test: pd.DataFrame
) -> tuple[np.ndarray, dict[str, Any]]:
    x_train, x_test, metadata = transform_features(train, test)
    x_train = np.column_stack([np.ones(len(train)), x_train])
    x_test = np.column_stack([np.ones(len(test)), x_test])
    y = train["label"].to_numpy(float)
    weights = state_entry_weights(train)
    offset = train["base_logit"].to_numpy(float)
    penalty = np.full(x_train.shape[1], L2)
    penalty[0] = 0.01

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        eta = offset + x_train @ beta
        probability = expit(eta)
        loss = float(
            np.sum(weights * (np.logaddexp(0, eta) - y * eta))
            + 0.5 * np.sum(penalty * beta * beta)
        )
        gradient = x_train.T @ (weights * (probability - y)) + penalty * beta
        return loss, gradient

    fit = minimize(
        lambda beta: objective(beta),
        np.zeros(x_train.shape[1]),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    if not fit.success:
        raise RuntimeError(f"challenger fit failed: {fit.message}")
    prediction = expit(test["base_logit"].to_numpy(float) + x_test @ fit.x)
    names = ["calibration_intercept", *metadata["columns"]]
    metadata.update(
        {
            "columns": names,
            "coefficients": fit.x.tolist(),
            "coefficient_map": dict(zip(names, fit.x.tolist(), strict=True)),
            "l2": L2,
            "optimizer_iterations": int(fit.nit),
            "train_rows": len(train),
            "train_dates": train["target_date"].nunique(),
        }
    )
    return np.clip(prediction, EPS, 1 - EPS), metadata


def collapse_state_entries(frame: pd.DataFrame, probability: str) -> pd.DataFrame:
    keys = ["city", "target_date", "current_bracket"]
    labels = frame.groupby(keys)["label"].nunique()
    if int(labels.max()) != 1:
        raise ValueError("one state entry has conflicting settlement labels")
    return frame.groupby(keys, as_index=False).agg(
        label=("label", "first"), probability=(probability, "mean")
    )


def probability_metrics(
    frame: pd.DataFrame, probability: str, *, grain: str = "state_entry"
) -> dict[str, Any]:
    if grain == "state_entry":
        scored = collapse_state_entries(frame, probability)
        units = len(scored)
    elif grain == "checkpoint":
        scored = frame[["target_date", "label", probability]].rename(
            columns={probability: "probability"}
        )
        units = len(scored)
    else:
        raise ValueError(grain)
    y = scored["label"].to_numpy(float)
    p = scored["probability"].clip(EPS, 1 - EPS).to_numpy(float)
    scored = scored.assign(
        brier=(y - p) ** 2,
        logloss=-(y * np.log(p) + (1 - y) * np.log(1 - p)),
    )
    daily = scored.groupby("target_date", as_index=False).agg(
        brier=("brier", "mean"),
        logloss=("logloss", "mean"),
        mean_probability=("probability", "mean"),
        actual_rate=("label", "mean"),
    )
    return {
        "grain": grain,
        "rows": len(frame),
        "units": units,
        "dates": len(daily),
        "brier": float(daily["brier"].mean()),
        "logloss": float(daily["logloss"].mean()),
        "mean_probability": float(daily["mean_probability"].mean()),
        "actual_rate": float(daily["actual_rate"].mean()),
    }


def daily_delta(frame: pd.DataFrame, candidate: str, baseline: str) -> pd.DataFrame:
    candidate_state = collapse_state_entries(frame, candidate)
    baseline_state = collapse_state_entries(frame, baseline)
    keys = ["city", "target_date", "current_bracket"]
    merged = candidate_state.merge(
        baseline_state[keys + ["probability"]],
        on=keys,
        suffixes=("_candidate", "_baseline"),
        validate="one_to_one",
    )
    y = merged["label"].to_numpy(float)
    candidate_p = merged["probability_candidate"].clip(EPS, 1 - EPS).to_numpy(float)
    baseline_p = merged["probability_baseline"].clip(EPS, 1 - EPS).to_numpy(float)
    merged = merged.assign(
        brier_delta=(y - candidate_p) ** 2 - (y - baseline_p) ** 2,
        logloss_delta=(
            -(y * np.log(candidate_p) + (1 - y) * np.log(1 - candidate_p))
            + y * np.log(baseline_p)
            + (1 - y) * np.log(1 - baseline_p)
        ),
    )
    return merged.groupby("target_date", as_index=False).agg(
        brier_delta=("brier_delta", "mean"),
        logloss_delta=("logloss_delta", "mean"),
        state_entries=("label", "size"),
    )


def bootstrap_delta(daily: pd.DataFrame) -> dict[str, Any]:
    rng = np.random.default_rng(SEED)
    indices = rng.integers(0, len(daily), size=(BOOTSTRAP_REPS, len(daily)))
    output: dict[str, Any] = {"dates": len(daily)}
    for metric in ["brier_delta", "logloss_delta"]:
        values = daily[metric].to_numpy(float)
        draws = values[indices].mean(axis=1)
        output[metric] = {
            "mean": float(values.mean()),
            "ci95": [
                float(np.quantile(draws, 0.025)),
                float(np.quantile(draws, 0.975)),
            ],
            "win_date_fraction": float((values < 0).mean()),
        }
    return output


def expanding_oof(frame: pd.DataFrame, forward_start: str) -> pd.DataFrame:
    dates = sorted(frame["target_date"].unique())
    rows: list[pd.DataFrame] = []
    for index, target_date in enumerate(dates):
        if index < WARMUP_DATES or target_date >= forward_start:
            continue
        train = frame[frame["target_date"] < target_date]
        test = frame[frame["target_date"] == target_date].copy()
        test["p_challenger"], _ = fit_predict(train, test)
        rows.append(test)
    return pd.concat(rows, ignore_index=True)


def format_delta(item: dict[str, Any]) -> str:
    return (
        f"{item['mean']:+.6f}（95% CI "
        f"[{item['ci95'][0]:+.6f}, {item['ci95'][1]:+.6f}]）"
    )


def main() -> int:
    global FEATURE_COLUMNS
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--challenger-run-id", default=RUN_ID)
    parser.add_argument("--challenger-output-dir", type=Path)
    parser.add_argument("--diagnostic-forecast-underprediction", action="store_true")
    parser.add_argument("--no-write-report", action="store_true")
    args, _ = parser.parse_known_args()
    if args.diagnostic_forecast_underprediction:
        FEATURE_COLUMNS = [*FEATURE_COLUMNS, FORECAST_UNDERPREDICTION_FEATURE]
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if not prereg.get("frozen_before_formal_run"):
        raise RuntimeError("preregistration is not frozen")
    frame = load_ledger()
    dates = sorted(frame["target_date"].unique())
    if len(dates) != WARMUP_DATES + 15 + FORWARD_DATES:
        raise RuntimeError(f"unexpected target-date denominator: {len(dates)}")
    state_entries = collapse_state_entries(frame, "p_core_hold")
    if len(frame) != 1349 or len(state_entries) != 801:
        raise RuntimeError(
            f"frozen denominator drift: rows={len(frame)}, states={len(state_entries)}"
        )

    forward_dates = dates[-FORWARD_DATES:]
    forward_start = forward_dates[0]
    development = expanding_oof(frame, forward_start)
    train = frame[frame["target_date"] < forward_start].copy()
    forward = frame[frame["target_date"].isin(forward_dates)].copy()
    forward["p_challenger"], frozen_fit = fit_predict(train, forward)

    out_dir = resolve_run_output(
        RESEARCH_ID,
        run_id=args.challenger_run_id,
        explicit_output=args.challenger_output_dir,
    )
    prepare_new_run_output(out_dir)
    development.to_csv(out_dir / "development_oof_predictions.csv", index=False)
    forward.to_csv(out_dir / "frozen_forward_predictions.csv", index=False)
    (out_dir / "frozen_model.json").write_text(
        json.dumps(json_ready(frozen_fit), indent=2) + "\n", encoding="utf-8"
    )

    comparisons: dict[str, Any] = {}
    for baseline in ["p_core_hold", "p_market_hold"]:
        daily = daily_delta(forward, "p_challenger", baseline)
        comparisons[baseline] = bootstrap_delta(daily)
        daily.to_csv(out_dir / f"forward_daily_delta_vs_{baseline}.csv", index=False)

    forward_metrics = {
        model: {
            "state_entry": probability_metrics(forward, probability),
            "checkpoint": probability_metrics(forward, probability, grain="checkpoint"),
        }
        for model, probability in {
            "challenger": "p_challenger",
            "core": "p_core_hold",
            "market": "p_market_hold",
        }.items()
    }
    development_metrics = {
        model: probability_metrics(development, probability)
        for model, probability in {
            "challenger": "p_challenger",
            "core": "p_core_hold",
            "market": "p_market_hold",
        }.items()
    }

    vs_core = comparisons["p_core_hold"]
    positive = all(
        vs_core[metric]["mean"] < 0 and vs_core[metric]["ci95"][1] < 0
        for metric in ["brier_delta", "logloss_delta"]
    )
    same_sign = all(
        vs_core[metric]["mean"] < 0
        for metric in ["brier_delta", "logloss_delta"]
    )
    verdict = (
        "historical_research_positive"
        if positive
        else ("inconclusive" if same_sign else "rejected")
    )

    feature_coverage = [
        {
            "feature": feature,
            "coverage": float(pd.to_numeric(frame[feature], errors="coerce").notna().mean()),
            "unique_non_null": int(pd.to_numeric(frame[feature], errors="coerce").nunique()),
        }
        for feature in FEATURE_COLUMNS
    ]
    pd.DataFrame(feature_coverage).to_csv(out_dir / "feature_coverage.csv", index=False)
    shifts = forward.assign(
        challenger_minus_core=forward["p_challenger"] - forward["p_core_hold"]
    )
    example_columns = [
        "opportunity_id",
        "city",
        "target_date",
        "current_bracket",
        "label",
        "p_market_hold",
        "p_core_hold",
        "p_challenger",
        "challenger_minus_core",
        *FEATURE_COLUMNS,
    ]
    examples = pd.concat(
        [shifts.nsmallest(10, "challenger_minus_core"), shifts.nlargest(10, "challenger_minus_core")]
    )[example_columns].drop_duplicates()
    examples.to_csv(out_dir / "largest_probability_shifts.csv", index=False)

    payload = {
        "schema_version": RESEARCH_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_id": RESEARCH_ID,
        "run_id": args.challenger_run_id,
        "denominator": {
            "rows": len(frame),
            "state_entries": len(state_entries),
            "city_days": frame.groupby(["city", "target_date"]).ngroups,
            "cities": frame["city"].nunique(),
            "dates": len(dates),
            "date_min": dates[0],
            "date_max": dates[-1],
            "input": str(INPUT.relative_to(ROOT)),
            "input_sha256": file_sha256(INPUT),
        },
        "funnel": {
            "signal": [
                {"stage": "fixed PIT checkpoint parent", "grain": "checkpoint", "rows": len(frame)},
                {"stage": "exact-bracket state entries", "grain": "state_entry", "rows": len(state_entries)},
                {"stage": "development expanding OOF", "grain": "checkpoint", "rows": len(development)},
                {"stage": "historical frozen forward", "grain": "checkpoint", "rows": len(forward)},
            ],
            "evidence": "The fixed parent already has model, same-state market and settlement coverage. No execution or fill filter is applied.",
        },
        "feature_columns": FEATURE_COLUMNS,
        "feature_coverage": feature_coverage,
        "development_dates": sorted(development["target_date"].unique()),
        "forward_dates": forward_dates,
        "development_metrics": development_metrics,
        "forward_metrics": forward_metrics,
        "forward_comparisons": comparisons,
        "frozen_fit": frozen_fit,
        "verdict": verdict,
        "decision_rule_pass": positive,
        "live_action": "none",
        "limitations": [
            *(
                [
                    "forecast_underprediction_ticks was added after inspecting later live failures; this run is exploratory diagnostic evidence, not the frozen preregistered v1 challenger."
                ]
                if args.diagnostic_forecast_underprediction
                else []
            ),
            "The ledger ends on 2026-07-08 and does not include the later Wellington, Lucknow or Manila live cases on the same fixed denominator.",
            "Historical first-seen cloud/rain transitions, dewpoint trend and wind-direction by terrain are unavailable, so they are not imputed or proxied.",
            "The last eight dates were frozen before this run but their outcomes were seen by the wider research program; this is not clean program-wide forward evidence.",
            "Probability quality alone does not authorize an eligibility, sizing, maker or live change.",
        ],
        "artifacts": {
            "artifact_dir": str(out_dir),
            "preregistration": str(PREREG.relative_to(ROOT)),
            "preregistration_sha256": file_sha256(PREREG),
        },
    }
    if not args.no_write_report:
        RESULT.write_text(json.dumps(json_ready(payload), indent=2) + "\n", encoding="utf-8")

    fm = forward_metrics
    lines = [
        "# Core Carry transition-confirmation challenger v1",
        "",
        f"**结论：`{verdict}`；没有修改 live。** 这是单一、预注册的 Core-offset residual，不是新增 gate。",
        "",
        "## 固定口径",
        "",
        f"- 分母：{len(frame):,} checkpoints、{len(state_entries)} 个 city-date-bracket state entries、{frame['city'].nunique()} 城、{len(dates)} 个 target dates（{dates[0]}–{dates[-1]}）。",
        f"- 开发窗：{development['target_date'].nunique()} 个 expanding OOF dates；历史 forward：{len(forward_dates)} dates（{forward_dates[0]}–{forward_dates[-1]}）。",
        "- 训练权重：target_date 等权 → date 内 state entry 等权 → state 内 checkpoint 等权；主评测同样按 target_date 等权。",
        f"- 模型：frozen Core logit offset + {len(FEATURE_COLUMNS)} 个连续物理/语义特征 + L2=0.5；K=1，无 forward 选型。",
        "",
        "## 加入的表达",
        "",
        "- settlement-native 上沿距离与 forecast 越档 margin；",
        "- fresh high、dip→rebound、forecast-exit 与短时 pullback 冲突；",
        "- 剩余 daylight/solar、forecast peak clock、forecast/observed reheat；",
        "- plateau confirmation；",
        "- Core 原有 wind logit boost × transition-risk，用连续 residual 学习是否应减弱无条件风贡献。",
        "",
        "## 历史 forward 结果（主口径：date-equal state entry）",
        "",
        "| 模型 | Brier | Logloss | 平均概率 | 实际 hold rate |",
        "|---|---:|---:|---:|---:|",
        f"| challenger | {fm['challenger']['state_entry']['brier']:.6f} | {fm['challenger']['state_entry']['logloss']:.6f} | {fm['challenger']['state_entry']['mean_probability']:.3f} | {fm['challenger']['state_entry']['actual_rate']:.3f} |",
        f"| frozen Core | {fm['core']['state_entry']['brier']:.6f} | {fm['core']['state_entry']['logloss']:.6f} | {fm['core']['state_entry']['mean_probability']:.3f} | {fm['core']['state_entry']['actual_rate']:.3f} |",
        f"| same-row market | {fm['market']['state_entry']['brier']:.6f} | {fm['market']['state_entry']['logloss']:.6f} | {fm['market']['state_entry']['mean_probability']:.3f} | {fm['market']['state_entry']['actual_rate']:.3f} |",
        "",
        f"- Challenger − Core Brier：{format_delta(vs_core['brier_delta'])}",
        f"- Challenger − Core logloss：{format_delta(vs_core['logloss_delta'])}",
        f"- Challenger − market Brier：{format_delta(comparisons['p_market_hold']['brier_delta'])}",
        f"- Challenger − market logloss：{format_delta(comparisons['p_market_hold']['logloss_delta'])}",
        "",
        "## 结论边界",
        "",
        "- 这次修正了旧 challenger 把同一 city-day 的不同 bracket 合并的问题；主 grain 现在是 city × target_date × current_bracket。",
        "- cloud/rain 首见转折、dewpoint trend、wind direction × terrain 历史字段在固定 parent 中不存在，本版明确不伪造。",
        "- 历史 forward 即使通过，也只能成为新 frozen-forward challenger；需要用 2026-08-07 之后新样本验证，不能直接替换 Core 或改 live。",
        "",
        "## 复现",
        "",
        "```bash",
        ".venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_taker_10share_v1.py --transition-confirmation-challenger --challenger-output-dir /tmp/core-carry-transition-confirmation-v1",
        "```",
        "",
        f"大文件产物：`{out_dir}`",
    ]
    if not args.no_write_report:
        REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            json_ready(
                {
                    "verdict": verdict,
                    "forward_metrics": forward_metrics,
                    "vs_core": vs_core,
                    "vs_market": comparisons["p_market_hold"],
                    "report": str(REPORT.relative_to(ROOT)),
                    "artifact_dir": str(out_dir),
                }
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
