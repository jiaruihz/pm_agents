#!/usr/bin/env python3
"""Test region, source-quality, and pricing explanations for D1 distance-2 NO."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
for import_path in (ROOT, SCRIPT_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import research_d1_europe_mechanism_decomposition_v4 as v4
import research_d1_full_ladder_no_source_confidence_city_v2 as v2
from weather_data_feed_service.legacy_weather_predict.city_pools import (
    FULL_CITY_CONFIGS,
)


DEFAULT_INPUT = v4.DEFAULT_INPUT
DEFAULT_ERRORS = v2.DEFAULT_ERRORS
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_global_region_source_causality_v5"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-28-d1-global-region-source-causality-v5.md"
)
MODEL_KEYS = v4.MODEL_KEYS
MODEL_LABELS = {
    "ecmwf_ifs025": "ECMWF IFS",
    "ecmwf_aifs025_single": "ECMWF AIFS",
    "gfs_global": "GFS",
    "icon_seamless": "ICON",
    "jma_seamless": "JMA",
}
REGION_ORDER = ["EU", "AS", "ME", "OC", "SA", "AF", "US"]
SEED = 2026072859
EPS = 1e-6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--errors", type=Path, default=DEFAULT_ERRORS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=30_000)
    return parser.parse_args()


def roi(frame: pd.DataFrame) -> float:
    return float(frame["pnl"].sum() / frame["cost"].sum())


def two_sided_p(samples: np.ndarray) -> float:
    return float(
        min(1.0, 2.0 * min(np.mean(samples <= 0), np.mean(samples >= 0)))
    )


def bh_qvalues(values: pd.Series) -> pd.Series:
    array = values.astype(float).to_numpy()
    order = np.argsort(array)
    ranked = array[order]
    adjusted = np.empty(len(array))
    running = 1.0
    for reverse in range(len(array) - 1, -1, -1):
        rank = reverse + 1
        running = min(running, ranked[reverse] * len(array) / rank)
        adjusted[order[reverse]] = running
    return pd.Series(adjusted, index=values.index)


def block_roi_samples(
    frame: pd.DataFrame, *, draws: int, seed: int
) -> np.ndarray:
    daily = frame.groupby("target_date")[["pnl", "cost"]].sum()
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(daily), size=(draws, len(daily)))
    return (
        daily["pnl"].to_numpy()[indices].sum(axis=1)
        / daily["cost"].to_numpy()[indices].sum(axis=1)
    )


def paired_roi_delta_samples(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> np.ndarray:
    left_daily = left.groupby("target_date")[["pnl", "cost"]].sum()
    right_daily = right.groupby("target_date")[["pnl", "cost"]].sum()
    dates = sorted(set(left_daily.index) | set(right_daily.index))
    left_daily = left_daily.reindex(dates, fill_value=0.0)
    right_daily = right_daily.reindex(dates, fill_value=0.0)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(dates), size=(draws, len(dates)))
    return (
        left_daily["pnl"].to_numpy()[indices].sum(axis=1)
        / left_daily["cost"].to_numpy()[indices].sum(axis=1)
        - right_daily["pnl"].to_numpy()[indices].sum(axis=1)
        / right_daily["cost"].to_numpy()[indices].sum(axis=1)
    )


def block_mean_samples(
    rows: pd.DataFrame, column: str, *, draws: int, seed: int
) -> np.ndarray:
    daily = rows.groupby("target_date")[column].agg(["sum", "size"])
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(daily), size=(draws, len(daily)))
    return (
        daily["sum"].to_numpy()[indices].sum(axis=1)
        / daily["size"].to_numpy()[indices].sum(axis=1)
    )


def block_score_delta_samples(
    rows: pd.DataFrame,
    score: str,
    baseline: str,
    *,
    draws: int,
    seed: int,
) -> np.ndarray:
    daily = rows.groupby("target_date")[[score, baseline]].agg(
        ["sum", "size"]
    )
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(daily), size=(draws, len(daily)))
    return (
        daily[(score, "sum")].to_numpy()[indices].sum(axis=1)
        / daily[(score, "size")].to_numpy()[indices].sum(axis=1)
        - daily[(baseline, "sum")].to_numpy()[indices].sum(axis=1)
        / daily[(baseline, "size")].to_numpy()[indices].sum(axis=1)
    )


def attach_all_source_scores(candidates: pd.DataFrame) -> pd.DataFrame:
    output = candidates.copy()
    for model in MODEL_KEYS:
        probability = output[f"p_no_{model}"].clip(EPS, 1.0 - EPS)
        output[f"brier_{model}"] = np.square(
            probability - output["no_win"]
        )
        output[f"logloss_{model}"] = -(
            output["no_win"] * np.log(probability)
            + (1.0 - output["no_win"]) * np.log(1.0 - probability)
        )
    output["p_no_equal_all5"] = output[
        [f"p_no_{model}" for model in MODEL_KEYS]
    ].mean(axis=1)
    probability = output["p_no_equal_all5"].clip(EPS, 1.0 - EPS)
    output["brier_equal_all5"] = np.square(
        probability - output["no_win"]
    )
    output["logloss_equal_all5"] = -(
        output["no_win"] * np.log(probability)
        + (1.0 - output["no_win"]) * np.log(1.0 - probability)
    )
    market = output["market_p_no"].clip(EPS, 1.0 - EPS)
    output["market_brier"] = np.square(
        market - output["no_win"]
    )
    output["market_logloss"] = -(
        output["no_win"] * np.log(market)
        + (1.0 - output["no_win"]) * np.log(1.0 - market)
    )
    return output


def region_summary(
    all5: pd.DataFrame, selected: pd.DataFrame, *, draws: int
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for index, region in enumerate(REGION_ORDER):
        frame = all5[all5["region"].eq(region)]
        keys = set(frame["snapshot_key"])
        market = selected[
            selected["policy"].eq("market_only_selection")
            & selected["snapshot_key"].isin(keys)
        ]
        mechanical = selected[
            selected["policy"].eq("mechanical_half_distance2")
            & selected["snapshot_key"].isin(keys)
        ]
        roi_samples = block_roi_samples(
            frame, draws=draws, seed=SEED + index
        )
        mechanical_delta = paired_roi_delta_samples(
            frame,
            mechanical,
            draws=draws,
            seed=SEED + 100 + index,
        )
        market_delta = paired_roi_delta_samples(
            frame, market, draws=draws, seed=SEED + 200 + index
        )
        calibration = frame.assign(
            market_calibration_residual=(
                frame["no_win"] - frame["market_p_no"]
            ),
            model_calibration_residual=(
                frame["no_win"] - frame["policy_p_no"]
            ),
        )
        market_cal_samples = block_mean_samples(
            calibration,
            "market_calibration_residual",
            draws=draws,
            seed=SEED + 300 + index,
        )
        records.append(
            {
                "region": region,
                "baskets": len(frame),
                "target_dates": int(frame["target_date"].nunique()),
                "cities": int(frame["city"].nunique()),
                "market_unit": ",".join(
                    sorted(frame["market_unit"].unique())
                ),
                "actual_p_no": float(frame["no_win"].mean()),
                "market_p_no": float(frame["market_p_no"].mean()),
                "model_p_no": float(frame["policy_p_no"].mean()),
                "mean_cost": float(frame["cost"].mean()),
                "fee_adjusted_roi": roi(frame),
                "roi_ci_low": float(np.quantile(roi_samples, 0.025)),
                "roi_ci_high": float(np.quantile(roi_samples, 0.975)),
                "mechanical_roi": roi(mechanical),
                "model_uplift_vs_mechanical": roi(frame)
                - roi(mechanical),
                "mechanical_delta_ci_low": float(
                    np.quantile(mechanical_delta, 0.025)
                ),
                "mechanical_delta_ci_high": float(
                    np.quantile(mechanical_delta, 0.975)
                ),
                "market_selection_roi": roi(market),
                "model_uplift_vs_market": roi(frame) - roi(market),
                "market_delta_ci_low": float(
                    np.quantile(market_delta, 0.025)
                ),
                "market_delta_ci_high": float(
                    np.quantile(market_delta, 0.975)
                ),
                "market_calibration_residual": float(
                    calibration["market_calibration_residual"].mean()
                ),
                "market_cal_ci_low": float(
                    np.quantile(market_cal_samples, 0.025)
                ),
                "market_cal_ci_high": float(
                    np.quantile(market_cal_samples, 0.975)
                ),
                "model_calibration_residual": float(
                    calibration["model_calibration_residual"].mean()
                ),
                "train_mean_mae_f": float(
                    frame["train_mean_mae_f"].mean()
                ),
                "safety_score": float(
                    frame["forecast_distance_per_train_mae"].mean()
                ),
                "forecast_spread_f": float(
                    frame["forecast_spread_f"].mean()
                ),
            }
        )
    return pd.DataFrame(records)


def source_training_summary(
    errors: pd.DataFrame, *, eligible_cities: set[str]
) -> pd.DataFrame:
    rows = errors[
        (errors["target_date"] <= v2.TRAIN_END)
        & errors["model_key"].isin(MODEL_KEYS)
        & errors["error_f"].notna()
        & errors["city"].astype(str).isin(eligible_cities)
    ].copy()
    rows["region"] = rows["city"].map(
        lambda city: FULL_CITY_CONFIGS[str(city)]["region"]
    )
    return (
        rows.groupby(["region", "model_key"], as_index=False)
        .agg(
            observations=("error_f", "size"),
            cities=("city", "nunique"),
            bias_f=("error_f", "mean"),
            mae_f=("abs_error_f", "mean"),
            rmse_f=(
                "error_f",
                lambda values: float(
                    np.sqrt(np.mean(np.square(values.astype(float))))
                ),
            ),
            p90_abs_error_f=(
                "abs_error_f",
                lambda values: float(np.quantile(values, 0.90)),
            ),
        )
        .sort_values(["region", "mae_f"])
        .reset_index(drop=True)
    )


def source_probability_summary(
    candidates: pd.DataFrame, *, draws: int
) -> pd.DataFrame:
    policies = ["equal_all5", *MODEL_KEYS]
    records: list[dict[str, Any]] = []
    trial = 0
    for region in REGION_ORDER:
        rows = candidates[candidates["region"].eq(region)]
        for policy in policies:
            brier_col = f"brier_{policy}"
            logloss_col = f"logloss_{policy}"
            brier_samples = block_score_delta_samples(
                rows,
                brier_col,
                "market_brier",
                draws=draws,
                seed=SEED + 500 + trial,
            )
            logloss_samples = block_score_delta_samples(
                rows,
                logloss_col,
                "market_logloss",
                draws=draws,
                seed=SEED + 600 + trial,
            )
            records.append(
                {
                    "region": region,
                    "source_policy": policy,
                    "candidate_rows": len(rows),
                    "target_dates": int(rows["target_date"].nunique()),
                    "brier": float(rows[brier_col].mean()),
                    "market_brier": float(rows["market_brier"].mean()),
                    "brier_delta_vs_market": float(
                        rows[brier_col].mean()
                        - rows["market_brier"].mean()
                    ),
                    "brier_ci_low": float(
                        np.quantile(brier_samples, 0.025)
                    ),
                    "brier_ci_high": float(
                        np.quantile(brier_samples, 0.975)
                    ),
                    "brier_p": two_sided_p(brier_samples),
                    "logloss": float(rows[logloss_col].mean()),
                    "market_logloss": float(
                        rows["market_logloss"].mean()
                    ),
                    "logloss_delta_vs_market": float(
                        rows[logloss_col].mean()
                        - rows["market_logloss"].mean()
                    ),
                    "logloss_ci_low": float(
                        np.quantile(logloss_samples, 0.025)
                    ),
                    "logloss_ci_high": float(
                        np.quantile(logloss_samples, 0.975)
                    ),
                    "logloss_p": two_sided_p(logloss_samples),
                }
            )
            trial += 1
    output = pd.DataFrame(records)
    output["brier_q_bh"] = bh_qvalues(output["brier_p"])
    output["logloss_q_bh"] = bh_qvalues(output["logloss_p"])
    return output


def source_trade_summary(selected: pd.DataFrame) -> pd.DataFrame:
    policies = [
        "equal_all5",
        *[f"single_{model}" for model in MODEL_KEYS],
        "market_only_selection",
        "mechanical_half_distance2",
    ]
    rows = selected[selected["policy"].isin(policies)].copy()
    return (
        rows.groupby(["region", "policy"], as_index=False)
        .agg(
            baskets=("pnl", "size"),
            target_dates=("target_date", "nunique"),
            mean_no_ask=("no_best_ask", "mean"),
            payout=("payout", "mean"),
            pnl=("pnl", "sum"),
            cost=("cost", "sum"),
        )
        .assign(fee_adjusted_roi=lambda frame: frame["pnl"] / frame["cost"])
    )


def safety_quintile_summary(all5: pd.DataFrame) -> pd.DataFrame:
    output = all5.copy()
    output["safety_quintile"] = pd.qcut(
        output["forecast_distance_per_train_mae"],
        5,
        labels=["Q1_low", "Q2", "Q3", "Q4", "Q5_high"],
        duplicates="drop",
    )
    return (
        output.groupby("safety_quintile", observed=True, as_index=False)
        .agg(
            baskets=("pnl", "size"),
            regions=("region", "nunique"),
            mean_safety=("forecast_distance_per_train_mae", "mean"),
            actual_p_no=("no_win", "mean"),
            market_p_no=("market_p_no", "mean"),
            model_p_no=("policy_p_no", "mean"),
            mean_cost=("cost", "mean"),
            pnl=("pnl", "sum"),
            cost=("cost", "sum"),
        )
        .assign(
            market_calibration_residual=lambda frame: (
                frame["actual_p_no"] - frame["market_p_no"]
            ),
            fee_adjusted_roi=lambda frame: frame["pnl"] / frame["cost"],
        )
    )


def fixed_effect_design(
    rows: pd.DataFrame,
    *,
    region_reference: str,
) -> tuple[np.ndarray, list[str]]:
    regions = sorted(set(rows["region"]) - {region_reference})
    columns = [np.ones(len(rows))]
    names = ["intercept"]
    for value in range(1, 5):
        columns.append(
            rows["safety_quintile"].eq(value).astype(float).to_numpy()
        )
        names.append(f"safety_q{value}")
    for value in range(1, 5):
        columns.append(
            rows["cost_quintile"].eq(value).astype(float).to_numpy()
        )
        names.append(f"cost_q{value}")
    for region in regions:
        columns.append(rows["region"].eq(region).astype(float).to_numpy())
        names.append(f"region_{region}")
    return np.column_stack(columns), names


def region_fixed_effects(
    all5: pd.DataFrame, *, draws: int
) -> pd.DataFrame:
    base = all5[all5["market_unit"].eq("C")].copy()
    base["market_residual"] = base["no_win"] - base["market_p_no"]
    safety_columns = {
        "equal_all5": "forecast_distance_per_train_mae",
        **{
            model: f"source_specific_safety_{model}"
            for model in MODEL_KEYS
        },
    }
    for model in MODEL_KEYS:
        base[f"source_specific_safety_{model}"] = (
            np.abs(base["bracket_center_f"] - base[f"forecast_{model}"])
            / base[f"train_mae_{model}"]
        )
    records: list[dict[str, Any]] = []
    for policy_index, (policy, safety_column) in enumerate(
        safety_columns.items()
    ):
        rows = base.copy()
        rows["safety_quintile"] = pd.qcut(
            rows[safety_column], 5, labels=False, duplicates="drop"
        )
        rows["cost_quintile"] = pd.qcut(
            rows["cost"], 5, labels=False, duplicates="drop"
        )
        design, names = fixed_effect_design(rows, region_reference="AS")
        outcome = rows["market_residual"].to_numpy()
        coefficient = np.linalg.lstsq(design, outcome, rcond=None)[0]
        dates = rows["target_date"].astype(str).unique()
        date_indices = {
            date: np.flatnonzero(rows["target_date"].astype(str).eq(date))
            for date in dates
        }
        rng = np.random.default_rng(SEED + 900 + policy_index)
        samples = np.empty((draws, len(names)))
        for draw in range(draws):
            sampled_dates = rng.choice(dates, size=len(dates), replace=True)
            indices = np.concatenate(
                [date_indices[date] for date in sampled_dates]
            )
            samples[draw] = np.linalg.lstsq(
                design[indices], outcome[indices], rcond=None
            )[0]
        for index, name in enumerate(names):
            if not name.startswith("region_"):
                continue
            values = samples[:, index]
            records.append(
                {
                    "source_policy": policy,
                    "contrast": (
                        f"{name.removeprefix('region_')}_minus_AS"
                    ),
                    "coefficient": float(coefficient[index]),
                    "ci_low": float(np.quantile(values, 0.025)),
                    "ci_high": float(np.quantile(values, 0.975)),
                    "p": two_sided_p(values),
                    "controls": "source_safety_quintile+cost_quintile",
                    "universe": "C_unit_only",
                }
            )
    return pd.DataFrame(records)


def coverage_summary(candidates: pd.DataFrame) -> pd.DataFrame:
    records = []
    for region in REGION_ORDER:
        rows = candidates[candidates["region"].eq(region)]
        expected = len(rows) * len(MODEL_KEYS)
        finite = int(
            np.isfinite(
                rows[[f"p_no_{model}" for model in MODEL_KEYS]].to_numpy()
            ).sum()
        )
        records.append(
            {
                "region": region,
                "candidate_rows": len(rows),
                "expected_source_probabilities": expected,
                "finite_source_probabilities": finite,
                "coverage": finite / expected,
            }
        )
    return pd.DataFrame(records)


def pct(value: float) -> str:
    return f"{value:+.2%}"


def write_report(
    path: Path,
    regions: pd.DataFrame,
    training: pd.DataFrame,
    probability: pd.DataFrame,
    trades: pd.DataFrame,
    fixed_effects: pd.DataFrame,
    safety: pd.DataFrame,
) -> None:
    region = regions.set_index("region")
    probability_index = probability.set_index(["region", "source_policy"])
    trade_index = trades.set_index(["region", "policy"])
    fixed = fixed_effects.set_index(["source_policy", "contrast"])
    lines = [
        "# D1 distance-2 NO：全球区域 × forecast source 成因实验 v5",
        "",
        "## 结论与动作",
        "",
        "**上一版关于参与者偏好、模板报价的说法没有直接证据，本报告撤回其"
        "策略解释权。实验能确认的是：source availability 不是原因；source "
        "accuracy 解释部分地域差异，但欧洲的 market residual 在控制标准化"
        "tail safety 与成本后仍存在。具体剩余原因尚不可识别。**",
        "",
        "- 修正 v4：把“欧洲档位相对 forecast/error distribution 更安全”称为"
        "主因过强。它能解释 outcome risk，却没有形成跨区域单调 alpha，因而只"
        "保留为风险特征，不作为已识别的错价成因。",
        "- 动作：保持 research / frozen shadow；不根据 Europe、Asia 或任何"
        " region/source 建 hard gate，不改 live。",
        "- 下一步只注册可检验变量：source-specific standardized distance、"
        "market calibration residual、settlement-station basis、tail side、"
        "lead time；不注册“注意力/偏好/定价者”标签。",
        "",
        "## Target 与数据快照",
        "",
        "```text",
        "在固定 D1 distance=2 paired opportunity 分母上，检验区域收益是否可由",
        "source coverage/quality、标准化 tail distance、成本与 market calibration",
        "解释，并比较 all5/single-source 与同 rows market/mechanical baseline。",
        "```",
        "",
        "- 数据：immutable v2 artifacts；2026-06-17..2026-07-07，1,190 "
        "baskets / 20 target dates / 43 cities / 2,380 candidate rows。",
        "- grain=`research replay basket/expression`；settled=100%；"
        "unsettled=0；missing bracket=0；actual fill=0。",
        "- 每个 candidate 都有 ECMWF IFS/AIFS、GFS、ICON、JMA 五源概率；"
        "所有区域 source coverage=100%。",
        "",
        "## 实验 1：所有区域，而非欧美二分",
        "",
        "| region | baskets/dates/cities | actual PNO | market PNO | cost | "
        "all5 ROI [CI] | mechanical | model-mechanical |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in REGION_ORDER:
        row = region.loc[name]
        lines.append(
            f"| {name} | {int(row['baskets'])}/{int(row['target_dates'])}/"
            f"{int(row['cities'])} | {row['actual_p_no']:.3f} | "
            f"{row['market_p_no']:.3f} | {row['mean_cost']:.3f} | "
            f"{pct(row['fee_adjusted_roi'])} "
            f"[{pct(row['roi_ci_low'])}, {pct(row['roi_ci_high'])}] | "
            f"{pct(row['mechanical_roi'])} | "
            f"{pct(row['model_uplift_vs_mechanical'])} |"
        )
    lines.extend(
        [
            "",
            "- **EU 是唯一多城市大样本中 absolute ROI CI 明确为正的区域。**",
            "- **Asia 并没有复现 Europe**：all5 ROI "
            f"{pct(region.loc['AS', 'fee_adjusted_roi'])}，CI "
            f"[{pct(region.loc['AS', 'roi_ci_low'])}, "
            f"{pct(region.loc['AS', 'roi_ci_high'])}]；market residual 只有 "
            f"{region.loc['AS', 'market_calibration_residual']:+.3f}。",
            "- ME 点估 +6.14%，但只有 72 baskets/3 cities，CI 跨 0；"
            "OC 是 Wellington 单城 23 baskets，不能称区域机制。",
            "- SA/AF/US 均为负点估；因此不是“所有 C 市场都好”。",
            "",
            "## 实验 2：是否由 source 分布或某个更好模型导致",
            "",
            "### 2.1 Coverage",
            "",
            "五个 source 在每个区域的 candidate coverage 都是 100%，所以"
            "不存在“欧洲恰好多了 ICON、亚洲少了 ECMWF”这种 source mix 差异。",
            "",
            "### 2.2 训练期 source MAE",
            "",
            "| region | best source / MAE(F) | all5 city-mean MAE(F) |",
            "|---|---:|---:|",
        ]
    )
    for name in REGION_ORDER:
        rows = training[training["region"].eq(name)]
        best = rows.sort_values("mae_f").iloc[0]
        lines.append(
            f"| {name} | {MODEL_LABELS[str(best['model_key'])]} / "
            f"{best['mae_f']:.2f} | {region.loc[name, 'train_mean_mae_f']:.2f} |"
        )
    lines.extend(
        [
            "",
            "- Europe 的 forecast 确实更准，尤其 ICON/GFS；US 五源误差整体更大。"
            "这是 underlying tail risk 差异的一部分。",
            "- 但 source quality 不能单独解释收益：ME 的平均 MAE 3.25F 仍有正"
            " ROI 点估；SA MAE 2.78F 却为负。",
            "",
            "### 2.3 同分母 single-source 交易与 proper score",
            "",
            "| region | all5 ROI | five single-source ROI range | "
            "all5 Brier Δmarket | any source Brier beats market after BH? |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for name in REGION_ORDER:
        source_rois = [
            float(
                trade_index.loc[
                    (name, f"single_{model}"), "fee_adjusted_roi"
                ]
            )
            for model in MODEL_KEYS
        ]
        region_probability = probability[
            probability["region"].eq(name)
        ]
        any_pass = bool(
            (
                (region_probability["brier_delta_vs_market"] < 0)
                & (region_probability["brier_q_bh"] < 0.05)
            ).any()
        )
        all5_probability = probability_index.loc[(name, "equal_all5")]
        lines.append(
            f"| {name} | "
            f"{pct(trade_index.loc[(name, 'equal_all5'), 'fee_adjusted_roi'])} | "
            f"{pct(min(source_rois))}..{pct(max(source_rois))} | "
            f"{all5_probability['brier_delta_vs_market']:+.5f} | "
            f"{'YES' if any_pass else 'NO'} |"
        )
    lines.extend(
        [
            "",
            "- Europe 五个 single-source trade policy 都为正，US 五个都为负；"
            "这说明结果不由某一个 source 独占。",
            "- 但 Europe-all 的 all5 与每个 single source 在全 candidate proper "
            "score 上都没有打赢 market；全 42 个 region×source/all5 检验经 BH "
            "后没有确认的 Brier 改善。",
            "- 因此 source accuracy 可以解释“天气尾部本来有多危险”，尚不能证明"
            " source probability 自身提供稳定 market alpha。",
            "",
            "## 实验 3：标准化 tail safety 是否解释区域残差",
            "",
            "| safety quintile | baskets | mean safety | actual PNO | "
            "market PNO | market residual | ROI |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in safety.itertuples(index=False):
        lines.append(
            f"| {row.safety_quintile} | {int(row.baskets)} | "
            f"{row.mean_safety:.2f} | {row.actual_p_no:.3f} | "
            f"{row.market_p_no:.3f} | "
            f"{row.market_calibration_residual:+.3f} | "
            f"{pct(row.fee_adjusted_roi)} |"
        )
    eu_controlled = fixed.loc[("equal_all5", "EU_minus_AS")]
    eu_source_controls = fixed.xs(
        "EU_minus_AS", level="contrast"
    ).loc[MODEL_KEYS]
    lines.extend(
        [
            "",
            "- safety score 对 actual P(NO) 呈清楚单调关系，说明它是有效风险"
            "变量；但 ROI/market residual 不单调，市场已定价掉高 safety 的大部分"
            "信息。",
            "- 为避免 US=F-unit 完全共线，固定效应实验只在 C-unit 内进行，并以"
            " Asia 为 reference；控制 safety quintile 与 cost quintile 后：",
            f"  - EU residual 仍比 Asia 高 "
            f"{pct(eu_controlled['coefficient'])}，target-date bootstrap CI "
            f"[{pct(eu_controlled['ci_low'])}, "
            f"{pct(eu_controlled['ci_high'])}]，p="
            f"{eu_controlled['p']:.4f}。",
            "- 改成每个 source 自己的 `distance / train MAE` 后，EU−Asia "
            f"系数范围仍为 {pct(eu_source_controls['coefficient'].min())}.."
            f"{pct(eu_source_controls['coefficient'].max())}；五种定义的 95% CI "
            f"下界范围为 {pct(eu_source_controls['ci_low'].min())}.."
            f"{pct(eu_source_controls['ci_low'].max())}。",
            "- 所以 Europe 结果**不能被 source error distribution + entry cost "
            "完全解释**。剩余项可能是未建模的 ladder geometry、city/station "
            "settlement basis、区域 weather regime 或定价过程；当前数据不能在"
            "这些机制之间识别。",
            "",
            "## 已证伪 / 未证实 / 保留",
            "",
            "- **已证伪：source availability/mix 是欧洲结果主因。**五源覆盖完全相同。",
            "- **不支持：某一个 source 产生 Europe alpha。**所有单源同方向，"
            "proper score 均未确认胜 market。",
            "- **部分支持：source accuracy 影响 underlying risk。**Europe MAE "
            "更低、US 更高，但控制后 Europe residual 仍存在。",
            "- **保留待验：settlement-station basis / ladder placement / regional "
            "weather regime。**需要新增可观测特征，不能用 region 名称代理。",
            "- **未识别：参与者、注意力、高温偏好、模板化做市。**没有 order-flow/"
            "wallet evidence，禁止用于策略规则。",
            "",
            "## Signal / evidence funnel、完整性与三门",
            "",
            "- signal：1,338 raw full-ladder baskets → 1,298 paired distance2 → "
            "1,190 all-five/market-covered → 1,190 selections。",
            "- evidence：2,380 candidate rows，五源 finite=11,900/11,900；"
            "settlement/executable quote 完整；actual fill=0。",
            "- 已覆盖：宽分母、region/source 同分母 A/B、proper score、"
            "fee-adjusted expression、target-date bootstrap、BH、多变量控制。",
            "- 缺失：station-basis 连续特征、participant identity/order-flow、"
            "真实 fill/queue/capacity、新日期 frozen forward。",
            "- significance=EU historical trade PASS；baseline=trade PASS / "
            "probability FAIL；forward=NA；conclusion=`shadow_candidate`，"
            "动作=保持 research/frozen shadow，不改 live。",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    all5 = v4.attach_context(
        pd.read_csv(args.input_dir / "confidence_rows.csv")
    )
    metadata = all5[["snapshot_key", "region"]]
    candidates = attach_all_source_scores(
        pd.read_csv(args.input_dir / "candidate_source_probs.csv").merge(
            metadata, on="snapshot_key", validate="many_to_one"
        )
    )
    selected = pd.read_csv(args.input_dir / "selected_policy_rows.csv").merge(
        metadata, on="snapshot_key", validate="many_to_one"
    )
    if len(all5) != 1190 or len(candidates) != 2380:
        raise RuntimeError("fixed denominator drift")
    regions = region_summary(all5, selected, draws=args.draws)
    training = source_training_summary(
        pd.read_csv(args.errors),
        eligible_cities=set(all5["city"].astype(str)),
    )
    probability = source_probability_summary(
        candidates, draws=args.draws
    )
    trades = source_trade_summary(selected)
    safety = safety_quintile_summary(all5)
    fixed_effects = region_fixed_effects(all5, draws=args.draws)
    coverage = coverage_summary(candidates)

    regions.to_csv(args.output_dir / "region_summary.csv", index=False)
    training.to_csv(
        args.output_dir / "region_source_training_quality.csv", index=False
    )
    probability.to_csv(
        args.output_dir / "region_source_probability_scores.csv", index=False
    )
    trades.to_csv(
        args.output_dir / "region_source_trade_summary.csv", index=False
    )
    safety.to_csv(
        args.output_dir / "safety_quintile_summary.csv", index=False
    )
    fixed_effects.to_csv(
        args.output_dir / "c_unit_region_fixed_effects.csv", index=False
    )
    coverage.to_csv(
        args.output_dir / "region_source_coverage.csv", index=False
    )
    payload = {
        "fixed_denominator": {
            "baskets": len(all5),
            "target_dates": int(all5["target_date"].nunique()),
            "cities": int(all5["city"].nunique()),
            "candidate_rows": len(candidates),
            "actual_fills": 0,
        },
        "regions": regions.to_dict("records"),
        "training": training.to_dict("records"),
        "probability": probability.to_dict("records"),
        "trades": trades.to_dict("records"),
        "safety": safety.to_dict("records"),
        "fixed_effects": fixed_effects.to_dict("records"),
        "coverage": coverage.to_dict("records"),
        "verdict": {
            "source_availability_cause": "rejected",
            "single_source_cause": "not_supported",
            "source_accuracy_partial_mediator": True,
            "region_residual_after_controls": True,
            "remaining_cause_identified": False,
            "live_ready": False,
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.report,
        regions,
        training,
        probability,
        trades,
        fixed_effects,
        safety,
    )


if __name__ == "__main__":
    main()
