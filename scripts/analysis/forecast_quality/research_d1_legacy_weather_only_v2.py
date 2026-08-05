#!/usr/bin/env python3
"""Train D-1 weather-only v2 challengers on legacy development evidence.

This is explicitly not the clean run-aware forward.  Long historical daily
cache trains A-E.  The first 18 reconstructed D-1 target dates select the F
ensemble/spread overlay, and the remaining dates are an untouched legacy
holdout.  Market is evaluated on identical rows but never enters a weather
model.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.special import ndtr

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_d1_cross_city_hierarchy_v1 as base  # noqa: E402


DEFAULT_OUT = ROOT / "docs/analysis/2026-08/generated/d1_legacy_weather_only_v2"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-08/2026-08-05-d1-legacy-weather-only-v2.md"
KERNEL_SD_F = 0.75
EPS = 1e-8


def _unit_values(values_f: np.ndarray, unit: str) -> np.ndarray:
    return (values_f - 32.0) * 5.0 / 9.0 if unit == "C" else values_f


def empirical_vector(
    state: dict[str, Any],
    values_f: np.ndarray,
    *,
    kernel_sd_f: float,
) -> np.ndarray:
    values = _unit_values(np.asarray(values_f, dtype=float), state["market_unit"])
    kernel = kernel_sd_f * (5.0 / 9.0 if state["market_unit"] == "C" else 1.0)
    probabilities: list[float] = []
    for bracket in state["brackets"]:
        if bracket.bottom:
            probability = float(np.mean(ndtr((float(bracket.high) + 0.5 - values) / kernel)))
        elif bracket.top:
            probability = float(np.mean(1.0 - ndtr((float(bracket.low) - 0.5 - values) / kernel)))
        else:
            upper = ndtr((float(bracket.high) + 0.5 - values) / kernel)
            lower = ndtr((float(bracket.low) - 0.5 - values) / kernel)
            probability = float(np.mean(upper - lower))
        probabilities.append(probability)
    vector = np.clip(np.asarray(probabilities), EPS, None)
    return vector / vector.sum()


def select_hierarchy_lambdas(train: pd.DataFrame) -> dict[str, float]:
    fit = train.loc[train["date"] < "2026-01-01"].copy()
    validation = train.loc[train["date"] >= "2026-01-01"].copy()
    if fit.empty or validation.empty:
        raise ValueError("history cannot support pre-2026 hierarchy validation")
    global_stats = fit.groupby("model")["error_f_actual_minus_forecast"].agg(["mean", "std"])
    city_stats = fit.groupby(["city", "model"])["error_f_actual_minus_forecast"].agg(["count", "mean", "std"])
    candidates: list[dict[str, float]] = []
    for center_lambda in (10.0, 30.0, 60.0, 120.0, 240.0):
        for scale_lambda in (60.0, 120.0, 240.0, 480.0):
            losses: list[float] = []
            for row in validation.itertuples(index=False):
                key = (row.city, row.model)
                if key not in city_stats.index or row.model not in global_stats.index:
                    continue
                city = city_stats.loc[key]
                glob = global_stats.loc[row.model]
                n = float(city["count"])
                center_weight = n / (n + center_lambda)
                scale_weight = n / (n + scale_lambda)
                center = float(glob["mean"]) + center_weight * (float(city["mean"]) - float(glob["mean"]))
                city_sd = max(float(city["std"]), 0.5)
                global_sd = max(float(glob["std"]), 0.5)
                scale = global_sd * math.exp(scale_weight * math.log(city_sd / global_sd))
                error = float(row.error_f_actual_minus_forecast)
                losses.append(math.log(scale) + 0.5 * ((error - center) / scale) ** 2)
            candidates.append(
                {
                    "center_lambda": center_lambda,
                    "scale_lambda": scale_lambda,
                    "validation_nll": float(np.mean(losses)),
                }
            )
    return min(candidates, key=lambda row: row["validation_nll"])


def fit_long_history(history: pd.DataFrame, test_start: str) -> dict[str, Any]:
    train = history.loc[
        history["is_best_model"]
        & (history["date"] < test_start)
        & history["month_num"].isin([5, 6, 7, 8])
    ].copy()
    selected = select_hierarchy_lambdas(train)
    global_errors = {
        model: group["error_f_actual_minus_forecast"].to_numpy(dtype=float)
        for model, group in train.groupby("model")
    }
    city_stats = train.groupby(["city", "model"])["error_f_actual_minus_forecast"].agg(["count", "mean", "std"])
    specs: dict[str, Any] = {}
    for (city, model), city_row in city_stats.iterrows():
        errors = global_errors[model]
        global_mean = float(np.mean(errors))
        global_sd = max(float(np.std(errors, ddof=1)), 0.5)
        n = float(city_row["count"])
        center_weight = n / (n + selected["center_lambda"])
        scale_weight = n / (n + selected["scale_lambda"])
        center = global_mean + center_weight * (float(city_row["mean"]) - global_mean)
        city_sd = max(float(city_row["std"]), 0.5)
        scale = global_sd * math.exp(scale_weight * math.log(city_sd / global_sd))
        standardized_shape = (errors - global_mean) / global_sd
        specs[city] = {
            "model": model,
            "global_mean": global_mean,
            "global_sd": global_sd,
            "global_errors": errors,
            "partial_center": center,
            "partial_scale": scale,
            "partial_errors": center + scale * standardized_shape,
            "train_rows": int(n),
        }
    climatology = (
        train[["city", "date", "actual_max_f"]]
        .drop_duplicates(["city", "date"])
        .assign(month=lambda frame: pd.to_datetime(frame["date"]).dt.month)
    )
    return {"train": train, "specs": specs, "climatology": climatology, "lambda_selection": selected}


def attach_multi_model(states: list[dict[str, Any]], forecasts: pd.DataFrame) -> None:
    grouped = forecasts.groupby("snapshot_key")["forecast_max_f"].agg(["median", "mean", "min", "max"])
    grouped["spread"] = grouped["max"] - grouped["min"]
    for state in states:
        row = grouped.loc[state["snapshot_key"]]
        state["ensemble_median_f"] = float(row["median"])
        state["ensemble_mean_f"] = float(row["mean"])
        state["model_spread_f"] = float(row["spread"])


def model_vectors(
    state: dict[str, Any],
    fitted: dict[str, Any],
    *,
    ensemble_weight: float,
    spread_beta: float,
) -> dict[str, np.ndarray]:
    spec = fitted["specs"][state["city"]]
    month = int(pd.Timestamp(state["target_date"]).month)
    climate = fitted["climatology"]
    climate_values = climate.loc[
        (climate["city"] == state["city"]) & (climate["month"] == month),
        "actual_max_f",
    ].to_numpy(dtype=float)
    if len(climate_values) < 20:
        climate_values = climate.loc[climate["city"] == state["city"], "actual_max_f"].to_numpy(dtype=float)
    if len(climate_values) == 0:
        raise ValueError(f"missing climatology training rows for city={state['city']}")
    assigned = float(state["forecast_max_f"])
    blended = (1.0 - ensemble_weight) * assigned + ensemble_weight * float(state["ensemble_median_f"])
    scale_multiplier = math.sqrt(
        1.0 + spread_beta * (float(state["model_spread_f"]) / max(spec["global_sd"], 0.5)) ** 2
    )
    return {
        "A_climatology": empirical_vector(state, climate_values, kernel_sd_f=KERNEL_SD_F),
        "B_pooled_normal_zero_bias": base.probability_vector(state, 0.0, spec["global_sd"]),
        "C_bias_corrected_pooled_normal": base.probability_vector(state, spec["global_mean"], spec["global_sd"]),
        "D_coherent_pooled_empirical": empirical_vector(
            state,
            assigned + spec["global_errors"],
            kernel_sd_f=KERNEL_SD_F,
        ),
        "E_partial_hierarchy_v2": empirical_vector(
            state,
            assigned + spec["partial_errors"],
            kernel_sd_f=KERNEL_SD_F,
        ),
        "F_ensemble_spread": empirical_vector(
            state,
            blended
            + spec["partial_center"]
            + (spec["partial_errors"] - spec["partial_center"]) * scale_multiplier,
            kernel_sd_f=KERNEL_SD_F,
        ),
        "market": state["market_probs"],
    }


def score(
    states: list[dict[str, Any]],
    fitted: dict[str, Any],
    *,
    ensemble_weight: float,
    spread_beta: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for state in states:
        for arm, vector in model_vectors(
            state,
            fitted,
            ensemble_weight=ensemble_weight,
            spread_beta=spread_beta,
        ).items():
            logloss, brier, rps, winner_probability, top1 = base.score_vector(vector, state["winner_index"])
            rows.append(
                {
                    "snapshot_key": state["snapshot_key"],
                    "city": state["city"],
                    "target_date": state["target_date"],
                    "arm": arm,
                    "logloss": logloss,
                    "brier": brier,
                    "rps": rps,
                    "winner_probability": winner_probability,
                    "top1_accuracy": top1,
                    "model_spread_f": state["model_spread_f"],
                    "probabilities_json": json.dumps(vector.tolist(), separators=(",", ":")),
                    "winner_index": int(state["winner_index"]),
                }
            )
    return pd.DataFrame(rows)


def date_equal(frame: pd.DataFrame) -> pd.DataFrame:
    metrics = ["logloss", "brier", "rps", "winner_probability", "top1_accuracy"]
    daily = frame.groupby(["target_date", "arm"], as_index=False)[metrics].mean()
    summary = daily.groupby("arm", as_index=False)[metrics].mean()
    counts = frame.groupby("arm").agg(states=("snapshot_key", "size"), dates=("target_date", "nunique"), cities=("city", "nunique")).reset_index()
    return summary.merge(counts, on="arm")


def select_f_overlay(dev_states: list[dict[str, Any]], fitted: dict[str, Any]) -> dict[str, float]:
    candidates: list[dict[str, float]] = []
    for weight in (0.0, 0.25, 0.5, 0.75, 1.0):
        for beta in (0.0, 0.25, 0.5, 1.0):
            frame = score(dev_states, fitted, ensemble_weight=weight, spread_beta=beta)
            value = float(
                frame.loc[frame["arm"] == "F_ensemble_spread"]
                .groupby("target_date")["logloss"]
                .mean()
                .mean()
            )
            candidates.append({"ensemble_weight": weight, "spread_beta": beta, "dev_logloss": value})
    return min(candidates, key=lambda row: row["dev_logloss"])


def bootstrap_delta(frame: pd.DataFrame, left: str, right: str, metric: str) -> dict[str, Any]:
    daily = frame.loc[frame["arm"].isin([left, right])].groupby(["target_date", "arm"])[metric].mean().unstack().dropna()
    deltas = (daily[left] - daily[right]).to_numpy(dtype=float)
    rng = np.random.default_rng(20260805)
    draws = rng.choice(deltas, size=(20000, len(deltas)), replace=True).mean(axis=1)
    return {
        "left": left,
        "right": right,
        "metric": metric,
        "dates": len(deltas),
        "delta": float(np.mean(deltas)),
        "ci_low": float(np.quantile(draws, 0.025)),
        "ci_high": float(np.quantile(draws, 0.975)),
    }


def calibration(frame: pd.DataFrame) -> pd.DataFrame:
    bins = [-0.001, 0.05, 0.10, 0.20, 0.40, 0.60, 1.001]
    rows: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        for rung_index, probability in enumerate(json.loads(row.probabilities_json)):
            rows.append(
                {
                    "arm": row.arm,
                    "snapshot_key": row.snapshot_key,
                    "predicted_probability": float(probability),
                    "outcome": int(rung_index == row.winner_index),
                }
            )
    source = pd.DataFrame(rows)
    source["probability_bin"] = pd.cut(source["predicted_probability"], bins=bins, right=True)
    return source.groupby(["arm", "probability_bin"], observed=True).agg(
        rungs=("snapshot_key", "size"),
        mean_predicted=("predicted_probability", "mean"),
        actual_frequency=("outcome", "mean"),
    ).reset_index()


def probability_diagnostics(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for arm, group in frame.groupby("arm"):
        expanded: list[tuple[float, int]] = []
        bottom_predicted: list[float] = []
        bottom_actual: list[int] = []
        top_predicted: list[float] = []
        top_actual: list[int] = []
        for row in group.itertuples(index=False):
            vector = np.asarray(json.loads(row.probabilities_json), dtype=float)
            outcomes = np.arange(len(vector)) == int(row.winner_index)
            expanded.extend(zip(vector.tolist(), outcomes.astype(int).tolist()))
            bottom_predicted.append(float(vector[0]))
            bottom_actual.append(int(row.winner_index == 0))
            top_predicted.append(float(vector[-1]))
            top_actual.append(int(row.winner_index == len(vector) - 1))
        expanded_frame = pd.DataFrame(expanded, columns=["probability", "outcome"])
        expanded_frame["bin"] = pd.cut(
            expanded_frame["probability"],
            bins=[-0.001, 0.05, 0.10, 0.20, 0.40, 0.60, 1.001],
        )
        grouped = expanded_frame.groupby("bin", observed=True).agg(
            n=("outcome", "size"), predicted=("probability", "mean"), actual=("outcome", "mean")
        )
        ece = float(((grouped["predicted"] - grouped["actual"]).abs() * grouped["n"]).sum() / grouped["n"].sum())
        high = expanded_frame.loc[expanded_frame["probability"] >= 0.40]
        rows.append(
            {
                "arm": arm,
                "rung_ece": ece,
                "bottom_predicted": float(np.mean(bottom_predicted)),
                "bottom_actual": float(np.mean(bottom_actual)),
                "top_predicted": float(np.mean(top_predicted)),
                "top_actual": float(np.mean(top_actual)),
                "high_probability_rungs": int(len(high)),
                "high_probability_mean": float(high["probability"].mean()) if len(high) else None,
                "high_probability_actual": float(high["outcome"].mean()) if len(high) else None,
                "winner_probability_le_001": int((group["winner_probability"] <= 0.01).sum()),
            }
        )
    return pd.DataFrame(rows)


def render_report(summary: dict[str, Any], scores: pd.DataFrame, deltas: pd.DataFrame) -> str:
    rows = {row.arm: row for row in scores.itertuples(index=False)}
    lines = [
        "# D-1 legacy weather-only v2 训练与 holdout 报告",
        "",
        "weather-only:",
        f"significance={summary['weather_gate']['significance']}",
        f"calibration={summary['weather_gate']['calibration']}",
        "pooled_baseline=B_pooled_normal_zero_bias",
        "forward=legacy_reconstructed_holdout_not_clean_run_aware_forward",
        "",
        "market residual:",
        "baseline=market_same_rows",
        "forward=not_run_by_contract",
        "execution=not_run_by_contract",
        "",
        "production:",
        "live_action=none",
        "orders_changed=0",
        "",
        "## 结论",
        "",
        summary["conclusion"],
        "",
        f"legacy holdout 为 {summary['holdout']['states']} states / {summary['holdout']['dates']} target dates / {summary['holdout']['cities']} cities。它可以否定坏结构和选择开发方向，但不能替代新 collector 的 frozen forward。",
        "",
        "## 同分母 holdout score",
        "",
        "| arm | logloss | Brier | RPS | winner P | top-1 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm in ("market", "A_climatology", "B_pooled_normal_zero_bias", "C_bias_corrected_pooled_normal", "D_coherent_pooled_empirical", "E_partial_hierarchy_v2", "F_ensemble_spread"):
        row = rows[arm]
        lines.append(f"| {arm} | {row.logloss:.4f} | {row.brier:.4f} | {row.rps:.4f} | {row.winner_probability:.3f} | {row.top1_accuracy:.1%} |")
    lines += [
        "",
        "G（physical-width）没有进入本轮：旧 reconstruction 没有同 clock 的 rain/convective/cloud/wind 完整特征。缺特征记 coverage blocker，不用事后天气或 hard filter 补洞。",
        "",
        "## 训练合同",
        "",
        f"- long history：{summary['training']['rows']} rows / {summary['training']['cities']} cities；lineage=`legacy_daily_cache_non_strict_pit_training_prior`。",
        f"- partial hierarchy：center λ={summary['partial_hierarchy']['center_lambda']:.0f}，scale λ={summary['partial_hierarchy']['scale_lambda']:.0f}；只在历史 validation 选。",
        f"- F overlay：ensemble weight={summary['f_overlay']['ensemble_weight']:.2f}，spread beta={summary['f_overlay']['spread_beta']:.2f}；只在前 {summary['development']['dates']} 个 reconstructed dates 选。",
        f"- holdout：{summary['holdout']['start']}..{summary['holdout']['end']}；没有调 λ、weight 或 beta。",
        "- market 只作同 rows baseline，没有进入 A-F；market residual 和交易表达均未运行。",
        "- F 中 run revision / lead / run age 在旧 reconstruction 中不可用；本轮 F 实际只检验 ensemble median 与 spread。开发集选择 `spread beta=0`，现有 spread 没有增量价值。",
        "",
        "## Calibration 与 tail",
        "",
        f"- F rung ECE={summary['diagnostics']['F_ensemble_spread']['rung_ece']:.4f}，pooled={summary['diagnostics']['B_pooled_normal_zero_bias']['rung_ece']:.4f}，market={summary['diagnostics']['market']['rung_ece']:.4f}。",
        f"- F bottom tail：预测 {summary['diagnostics']['F_ensemble_spread']['bottom_predicted']:.1%} / 实际 {summary['diagnostics']['F_ensemble_spread']['bottom_actual']:.1%}；top tail：预测 {summary['diagnostics']['F_ensemble_spread']['top_predicted']:.1%} / 实际 {summary['diagnostics']['F_ensemble_spread']['top_actual']:.1%}。",
        f"- F 给真实 winner ≤1% 概率的 state={summary['diagnostics']['F_ensemble_spread']['winner_probability_le_001']}；仍需在更大 clean forward 上检查 city 灾难尾。",
        "",
        "## 下一步",
        "",
        "把本轮最好的 weather-only 结构作为 challenger 固定下来；新 exact-run collector 的首个完整 target date 之后，按相同代码重估/验证。只有 clean run-aware forward 通过 weather gate，才运行 market residual。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forecasts", type=Path, default=base.DEFAULT_FORECASTS)
    parser.add_argument("--baskets", type=Path, default=base.DEFAULT_BASKETS)
    parser.add_argument("--history", type=Path, default=base.DEFAULT_HISTORY)
    parser.add_argument("--db", type=Path, default=base.DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    forecasts = pd.read_csv(args.forecasts, dtype={"target_date": str})
    baskets = pd.read_csv(args.baskets, dtype={"target_date": str})
    history = pd.read_csv(args.history, dtype={"date": str})
    history["is_best_model"] = history["is_best_model"].astype(str).str.lower().isin(["true", "1"])
    history["month_num"] = pd.to_datetime(history["date"]).dt.month
    assignments = base.model_assignments(history)
    states, funnels = base.build_states(forecasts, baskets, assignments, base.load_settlements(args.db))
    states = [state for state in states if state["policy"] == base.PRIMARY_POLICY]
    attach_multi_model(states, forecasts)
    target_dates = sorted({state["target_date"] for state in states})
    split = min(18, len(target_dates) - 5)
    development_dates = set(target_dates[:split])
    holdout_dates = set(target_dates[split:])
    development_states = [state for state in states if state["target_date"] in development_dates]
    holdout_states = [state for state in states if state["target_date"] in holdout_dates]
    fitted = fit_long_history(history, min(target_dates))
    overlay = select_f_overlay(development_states, fitted)
    scored = score(
        holdout_states,
        fitted,
        ensemble_weight=overlay["ensemble_weight"],
        spread_beta=overlay["spread_beta"],
    )
    score_summary = date_equal(scored)
    diagnostics = probability_diagnostics(scored)
    deltas = pd.DataFrame(
        [
            bootstrap_delta(scored, left, right, metric)
            for metric in ("logloss", "brier", "rps")
            for left, right in (
                ("F_ensemble_spread", "B_pooled_normal_zero_bias"),
                ("F_ensemble_spread", "E_partial_hierarchy_v2"),
                ("F_ensemble_spread", "market"),
                ("E_partial_hierarchy_v2", "B_pooled_normal_zero_bias"),
            )
        ]
    )
    scores_by_arm = score_summary.set_index("arm")
    f_vs_b = deltas.loc[(deltas["metric"] == "logloss") & (deltas["left"] == "F_ensemble_spread") & (deltas["right"] == "B_pooled_normal_zero_bias")].iloc[0]
    f_vs_market = deltas.loc[(deltas["metric"] == "logloss") & (deltas["left"] == "F_ensemble_spread") & (deltas["right"] == "market")].iloc[0]
    significance = "improves_pooled" if f_vs_b.ci_high < 0 else "not_significant_vs_pooled"
    conclusion = (
        f"F ensemble/spread holdout logloss={scores_by_arm.loc['F_ensemble_spread', 'logloss']:.4f}；"
        f"相对 zero-bias pooled Δ={f_vs_b.delta:+.4f}（95% CI {f_vs_b.ci_low:+.4f}..{f_vs_b.ci_high:+.4f}）。"
        f"相对 market Δ={f_vs_market.delta:+.4f}（{f_vs_market.ci_low:+.4f}..{f_vs_market.ci_high:+.4f}）。"
    )
    summary = {
        "schema_version": "d1_legacy_weather_only_v2",
        "training": {
            "lineage": "legacy_daily_cache_non_strict_pit_training_prior",
            "rows": int(len(fitted["train"])),
            "cities": int(fitted["train"]["city"].nunique()),
        },
        "development": {"dates": len(development_dates), "states": len(development_states)},
        "holdout": {
            "dates": len(holdout_dates),
            "states": len(holdout_states),
            "cities": len({state["city"] for state in holdout_states}),
            "start": min(holdout_dates),
            "end": max(holdout_dates),
            "lineage": "single_run_reconstructed_conservative_12h_lag",
        },
        "partial_hierarchy": fitted["lambda_selection"],
        "f_overlay": overlay,
        "funnels": funnels,
        "weather_gate": {
            "significance": significance,
            "calibration": "legacy_holdout_only_not_clean_forward",
            "market_residual": "not_run_by_contract",
        },
        "physical_width_status": "blocked_missing_same_clock_legacy_features",
        "conclusion": conclusion,
        "scores": score_summary.to_dict("records"),
        "diagnostics": {row["arm"]: row for row in diagnostics.to_dict("records")},
        "paired_deltas": deltas.to_dict("records"),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    scored.to_csv(args.out / "holdout_scored_states.csv", index=False)
    score_summary.to_csv(args.out / "holdout_score_summary.csv", index=False)
    deltas.to_csv(args.out / "holdout_paired_date_bootstrap.csv", index=False)
    calibration(scored).to_csv(args.out / "holdout_calibration.csv", index=False)
    diagnostics.to_csv(args.out / "holdout_probability_diagnostics.csv", index=False)
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(render_report(summary, score_summary, deltas), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
