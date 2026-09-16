#!/usr/bin/env python3
"""Frozen historical holdout for the D1 Europe distance-2 single-NO branch."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DISCOVERY = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_full_ladder_no_source_confidence_city_v2/selected_policy_rows.csv"
)
DEFAULT_DISCOVERY_CANDIDATES = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_full_ladder_no_source_confidence_city_v2/candidate_source_probs.csv"
)
DEFAULT_FORWARD = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_europe_distance2_blind_forward_v7/v2_replay/selected_policy_rows.csv"
)
DEFAULT_FORWARD_CANDIDATES = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_europe_distance2_blind_forward_v7/v2_replay/"
    "candidate_source_probs.csv"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_europe_distance2_blind_forward_v7/frozen_scorecard"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-28-d1-europe-distance2-blind-forward-v7.md"
)
POLICIES = (
    "equal_all5",
    "market_only_selection",
    "mechanical_half_distance2",
)
MODEL_PROBABILITY_COLUMNS = (
    "p_no_ecmwf_ifs025",
    "p_no_ecmwf_aifs025_single",
    "p_no_gfs_global",
    "p_no_icon_seamless",
    "p_no_jma_seamless",
)
EUROPE_CITIES = {
    "Amsterdam",
    "Ankara",
    "Helsinki",
    "Istanbul",
    "London",
    "Madrid",
    "Milan",
    "Munich",
    "Paris",
    "Warsaw",
}
EUROPE_CLOUD_BREAK = {
    "Amsterdam",
    "Helsinki",
    "Madrid",
    "Munich",
    "Warsaw",
}
SEED = 2026072841


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument(
        "--discovery-candidates",
        type=Path,
        default=DEFAULT_DISCOVERY_CANDIDATES,
    )
    parser.add_argument("--forward", type=Path, default=DEFAULT_FORWARD)
    parser.add_argument(
        "--forward-candidates",
        type=Path,
        default=DEFAULT_FORWARD_CANDIDATES,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=10_000)
    return parser.parse_args()


def roi(rows: pd.DataFrame) -> float:
    return float(rows["pnl"].sum() / rows["cost"].sum())


def two_sided_p(samples: np.ndarray) -> float:
    return float(
        min(1.0, 2.0 * min(np.mean(samples <= 0), np.mean(samples >= 0)))
    )


def block_roi_samples(
    rows: pd.DataFrame, *, draws: int, seed: int
) -> np.ndarray:
    daily = rows.groupby("target_date")[["pnl", "cost"]].sum()
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(daily), size=(draws, len(daily)))
    return (
        daily["pnl"].to_numpy()[indices].sum(axis=1)
        / daily["cost"].to_numpy()[indices].sum(axis=1)
    )


def paired_roi_delta_samples(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> np.ndarray:
    left = candidate.groupby("target_date")[["pnl", "cost"]].sum()
    right = baseline.groupby("target_date")[["pnl", "cost"]].sum()
    joined = left.join(right, lsuffix="_left", rsuffix="_right", how="inner")
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(joined), size=(draws, len(joined)))
    return (
        joined["pnl_left"].to_numpy()[indices].sum(axis=1)
        / joined["cost_left"].to_numpy()[indices].sum(axis=1)
        - joined["pnl_right"].to_numpy()[indices].sum(axis=1)
        / joined["cost_right"].to_numpy()[indices].sum(axis=1)
    )


def block_score_delta_samples(
    rows: pd.DataFrame,
    left: str,
    right: str,
    *,
    draws: int,
    seed: int,
) -> np.ndarray:
    daily = rows.groupby("target_date")[[left, right]].agg(["sum", "size"])
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(daily), size=(draws, len(daily)))
    return (
        daily[(left, "sum")].to_numpy()[indices].sum(axis=1)
        / daily[(left, "size")].to_numpy()[indices].sum(axis=1)
        - daily[(right, "sum")].to_numpy()[indices].sum(axis=1)
        / daily[(right, "size")].to_numpy()[indices].sum(axis=1)
    )


def load_window(path: Path, window: str) -> pd.DataFrame:
    rows = pd.read_csv(path)
    rows = rows[rows["policy"].isin(POLICIES)].copy()
    rows["window"] = window
    expected = rows.groupby("policy")["snapshot_key"].nunique()
    if len(expected) != len(POLICIES) or expected.nunique() != 1:
        raise ValueError(f"policy denominator mismatch in {path}: {expected}")
    return rows


def load_probability_window(path: Path, window: str) -> pd.DataFrame:
    rows = pd.read_csv(path)
    rows["window"] = window
    rows["brier"] = (
        rows[list(MODEL_PROBABILITY_COLUMNS)].mean(axis=1) - rows["no_win"]
    ) ** 2
    rows["market_brier"] = (rows["market_p_no"] - rows["no_win"]) ** 2
    model_probability = rows[list(MODEL_PROBABILITY_COLUMNS)].mean(axis=1)
    model_probability = model_probability.clip(1e-6, 1 - 1e-6)
    market_probability = rows["market_p_no"].clip(1e-6, 1 - 1e-6)
    rows["logloss"] = -(
        rows["no_win"] * np.log(model_probability)
        + (1 - rows["no_win"]) * np.log(1 - model_probability)
    )
    rows["market_logloss"] = -(
        rows["no_win"] * np.log(market_probability)
        + (1 - rows["no_win"]) * np.log(1 - market_probability)
    )
    return rows


def group_rows(rows: pd.DataFrame, group: str) -> pd.DataFrame:
    cities = EUROPE_CITIES if group == "europe_all" else EUROPE_CLOUD_BREAK
    return rows[rows["city"].isin(cities)].copy()


def performance_scorecard(
    rows: pd.DataFrame, *, draws: int
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for window_index, window in enumerate(
        ("discovery", "blind_forward", "combined")
    ):
        window_rows = rows if window == "combined" else rows[rows["window"] == window]
        for group_index, group in enumerate(
            ("europe_all", "europe_cloud_break")
        ):
            grouped = group_rows(window_rows, group)
            for policy_index, policy in enumerate(POLICIES):
                selected = grouped[grouped["policy"] == policy]
                samples = block_roi_samples(
                    selected,
                    draws=draws,
                    seed=SEED
                    + window_index * 100
                    + group_index * 10
                    + policy_index,
                )
                records.append(
                    {
                        "window": window,
                        "group": group,
                        "policy": policy,
                        "start_date": selected["target_date"].min(),
                        "end_date": selected["target_date"].max(),
                        "baskets": int(selected["snapshot_key"].nunique()),
                        "target_dates": int(selected["target_date"].nunique()),
                        "cities": int(selected["city"].nunique()),
                        "cost": float(selected["cost"].sum()),
                        "pnl": float(selected["pnl"].sum()),
                        "fee_adjusted_roi": roi(selected),
                        "roi_ci_low": float(np.quantile(samples, 0.025)),
                        "roi_ci_high": float(np.quantile(samples, 0.975)),
                        "roi_p": two_sided_p(samples),
                    }
                )
    return pd.DataFrame(records)


def paired_scorecard(rows: pd.DataFrame, *, draws: int) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for window_index, window in enumerate(
        ("discovery", "blind_forward", "combined")
    ):
        window_rows = rows if window == "combined" else rows[rows["window"] == window]
        for group_index, group in enumerate(
            ("europe_all", "europe_cloud_break")
        ):
            grouped = group_rows(window_rows, group)
            candidate = grouped[grouped["policy"] == "equal_all5"]
            keys = set(candidate["snapshot_key"])
            for baseline_index, baseline_policy in enumerate(
                ("market_only_selection", "mechanical_half_distance2")
            ):
                baseline = grouped[
                    (grouped["policy"] == baseline_policy)
                    & grouped["snapshot_key"].isin(keys)
                ]
                samples = paired_roi_delta_samples(
                    candidate,
                    baseline,
                    draws=draws,
                    seed=SEED
                    + 1000
                    + window_index * 100
                    + group_index * 10
                    + baseline_index,
                )
                records.append(
                    {
                        "window": window,
                        "group": group,
                        "candidate_policy": "equal_all5",
                        "baseline_policy": baseline_policy,
                        "baskets": len(keys),
                        "target_dates": int(candidate["target_date"].nunique()),
                        "candidate_roi": roi(candidate),
                        "baseline_roi": roi(baseline),
                        "roi_delta": roi(candidate) - roi(baseline),
                        "delta_ci_low": float(np.quantile(samples, 0.025)),
                        "delta_ci_high": float(np.quantile(samples, 0.975)),
                        "delta_p": two_sided_p(samples),
                    }
                )
    return pd.DataFrame(records)


def probability_scorecard(
    rows: pd.DataFrame, *, draws: int
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for window_index, window in enumerate(
        ("discovery", "blind_forward", "combined")
    ):
        window_rows = (
            rows
            if window == "combined"
            else rows[rows["window"] == window]
        )
        for group_index, group in enumerate(
            ("europe_all", "europe_cloud_break")
        ):
            grouped = group_rows(window_rows, group)
            brier_samples = block_score_delta_samples(
                grouped,
                "brier",
                "market_brier",
                draws=draws,
                seed=SEED + 2000 + window_index * 100 + group_index * 10,
            )
            logloss_samples = block_score_delta_samples(
                grouped,
                "logloss",
                "market_logloss",
                draws=draws,
                seed=SEED + 2100 + window_index * 100 + group_index * 10,
            )
            records.append(
                {
                    "window": window,
                    "group": group,
                    "baskets": int(grouped["snapshot_key"].nunique()),
                    "target_dates": int(grouped["target_date"].nunique()),
                    "brier": float(grouped["brier"].mean()),
                    "market_brier": float(grouped["market_brier"].mean()),
                    "brier_delta": float(
                        grouped["brier"].mean() - grouped["market_brier"].mean()
                    ),
                    "brier_delta_ci_low": float(
                        np.quantile(brier_samples, 0.025)
                    ),
                    "brier_delta_ci_high": float(
                        np.quantile(brier_samples, 0.975)
                    ),
                    "logloss": float(grouped["logloss"].mean()),
                    "market_logloss": float(grouped["market_logloss"].mean()),
                    "logloss_delta": float(
                        grouped["logloss"].mean()
                        - grouped["market_logloss"].mean()
                    ),
                    "logloss_delta_ci_low": float(
                        np.quantile(logloss_samples, 0.025)
                    ),
                    "logloss_delta_ci_high": float(
                        np.quantile(logloss_samples, 0.975)
                    ),
                }
            )
    return pd.DataFrame(records)


def city_forward_scorecard(rows: pd.DataFrame, *, draws: int) -> pd.DataFrame:
    forward = rows[
        (rows["window"] == "blind_forward")
        & (rows["policy"] == "equal_all5")
        & rows["city"].isin(EUROPE_CITIES)
    ]
    records: list[dict[str, Any]] = []
    for index, (city, grouped) in enumerate(forward.groupby("city")):
        samples = block_roi_samples(
            grouped, draws=draws, seed=SEED + 3000 + index
        )
        records.append(
            {
                "city": city,
                "predefined_five_city": city in EUROPE_CLOUD_BREAK,
                "baskets": len(grouped),
                "target_dates": int(grouped["target_date"].nunique()),
                "fee_adjusted_roi": roi(grouped),
                "roi_ci_low": float(np.quantile(samples, 0.025)),
                "roi_ci_high": float(np.quantile(samples, 0.975)),
            }
        )
    return pd.DataFrame(records).sort_values(
        "fee_adjusted_roi", ascending=False
    )


def percent(value: float) -> str:
    return f"{value:.2%}"


def city_markdown(rows: pd.DataFrame) -> str:
    lines = [
        "| city | baskets | target_dates | fee-adjusted ROI |",
        "|---|---:|---:|---:|",
    ]
    for row in rows.itertuples(index=False):
        lines.append(
            f"| {row.city} | {int(row.baskets)} | "
            f"{int(row.target_dates)} | {percent(row.fee_adjusted_roi)} |"
        )
    return "\n".join(lines)


def render_report(
    performance: pd.DataFrame,
    paired: pd.DataFrame,
    probability: pd.DataFrame,
    cities: pd.DataFrame,
) -> str:
    def perf(window: str, group: str, policy: str) -> pd.Series:
        return performance[
            (performance["window"] == window)
            & (performance["group"] == group)
            & (performance["policy"] == policy)
        ].iloc[0]

    def pair(window: str, group: str, baseline: str) -> pd.Series:
        return paired[
            (paired["window"] == window)
            & (paired["group"] == group)
            & (paired["baseline_policy"] == baseline)
        ].iloc[0]

    def prob(window: str, group: str) -> pd.Series:
        return probability[
            (probability["window"] == window)
            & (probability["group"] == group)
        ].iloc[0]

    discovery_eu = perf("discovery", "europe_all", "equal_all5")
    forward_eu = perf("blind_forward", "europe_all", "equal_all5")
    discovery_five = perf(
        "discovery", "europe_cloud_break", "equal_all5"
    )
    forward_five = perf(
        "blind_forward", "europe_cloud_break", "equal_all5"
    )
    forward_eu_market = pair(
        "blind_forward", "europe_all", "market_only_selection"
    )
    forward_eu_mechanical = pair(
        "blind_forward", "europe_all", "mechanical_half_distance2"
    )
    forward_five_market = pair(
        "blind_forward", "europe_cloud_break", "market_only_selection"
    )
    forward_five_mechanical = pair(
        "blind_forward", "europe_cloud_break", "mechanical_half_distance2"
    )
    forward_eu_prob = prob("blind_forward", "europe_all")
    forward_five_prob = prob("blind_forward", "europe_cloud_break")
    predefined = cities[cities["predefined_five_city"]]
    other = cities[~cities["predefined_five_city"]]

    return f"""# D1 欧洲 distance=2 单腿 NO：历史盲区间 forward v7

## 结论

欧洲分支有后续，但新数据把原结论拆成了两部分：

- **固定五城 `europe_cloud_break` 没有复现。** discovery ROI 从
  {percent(discovery_five.fee_adjusted_roi)} 降到
  {percent(forward_five.fee_adjusted_roi)}，不能再把这五城当作已找到的城市池。
- **泛欧洲 `Europe-all` 仍保留正点估计，但证据不足。** 新窗口 ROI
  {percent(forward_eu.fee_adjusted_roi)}，相对 market-only 高
  {percent(forward_eu_market.roi_delta)}，但置信区间都跨 0，且概率评分没有打败市场。
- 因此动作是：**保留 Europe-all 为 frozen research/shadow 方向；五城 family 降级为
  dormant；不改 live，不根据本窗口新增 London/Paris/Milan allowlist。**

这里研究的不是“同时买最低档和最高档 NO”。策略表达是：在距离两端各两档的两个
NO 中，用五模型 bias-corrected 分布只选一腿。两端极值 NO basket 是另一条分支，
不能把这里的欧洲结果移植过去。

## 数据与冻结口径

- discovery：{discovery_eu.start_date} 至 {discovery_eu.end_date}。
- historical blind holdout：{forward_eu.start_date} 至
  {forward_eu.end_date}；原始文件先前存在，但旧镜像权限导致未进入 discovery，
  因而这是历史盲区间，不冒充实时 prospective forward。
- 固定 `Europe-all`：Amsterdam、Ankara、Helsinki、Istanbul、London、Madrid、
  Milan、Munich、Paris、Warsaw。
- 固定五城：Amsterdam、Helsinki、Madrid、Munich、Warsaw。
- 每个 city-day 只取 D-1 首个 PIT ladder；所有策略使用完全相同的
  `snapshot_key` 分母；ROI 为 fee-adjusted；CI 按 `target_date` block bootstrap。
- 预注册要求为至少 15 个新 `target_date`；本次只有
  {int(forward_eu.target_dates)} 天，还差
  {15 - int(forward_eu.target_dates)} 天。

## 同分母结果

| 固定切片 | discovery baskets / dates | discovery ROI | blind baskets / dates | blind ROI | blind 95% CI |
|---|---:|---:|---:|---:|---:|
| Europe-all | {int(discovery_eu.baskets)} / {int(discovery_eu.target_dates)} | {percent(discovery_eu.fee_adjusted_roi)} | {int(forward_eu.baskets)} / {int(forward_eu.target_dates)} | {percent(forward_eu.fee_adjusted_roi)} | [{percent(forward_eu.roi_ci_low)}, {percent(forward_eu.roi_ci_high)}] |
| 固定五城 | {int(discovery_five.baskets)} / {int(discovery_five.target_dates)} | {percent(discovery_five.fee_adjusted_roi)} | {int(forward_five.baskets)} / {int(forward_five.target_dates)} | {percent(forward_five.fee_adjusted_roi)} | [{percent(forward_five.roi_ci_low)}, {percent(forward_five.roi_ci_high)}] |

### 相对基线

| 固定切片 | 基线 | candidate ROI | baseline ROI | Δ ROI | Δ 95% CI |
|---|---|---:|---:|---:|---:|
| Europe-all | market-only 选腿 | {percent(forward_eu_market.candidate_roi)} | {percent(forward_eu_market.baseline_roi)} | {percent(forward_eu_market.roi_delta)} | [{percent(forward_eu_market.delta_ci_low)}, {percent(forward_eu_market.delta_ci_high)}] |
| Europe-all | 两腿各半机械组合 | {percent(forward_eu_mechanical.candidate_roi)} | {percent(forward_eu_mechanical.baseline_roi)} | {percent(forward_eu_mechanical.roi_delta)} | [{percent(forward_eu_mechanical.delta_ci_low)}, {percent(forward_eu_mechanical.delta_ci_high)}] |
| 固定五城 | market-only 选腿 | {percent(forward_five_market.candidate_roi)} | {percent(forward_five_market.baseline_roi)} | {percent(forward_five_market.roi_delta)} | [{percent(forward_five_market.delta_ci_low)}, {percent(forward_five_market.delta_ci_high)}] |
| 固定五城 | 两腿各半机械组合 | {percent(forward_five_mechanical.candidate_roi)} | {percent(forward_five_mechanical.baseline_roi)} | {percent(forward_five_mechanical.roi_delta)} | [{percent(forward_five_mechanical.delta_ci_low)}, {percent(forward_five_mechanical.delta_ci_high)}] |

Europe-all 的正 ROI 主要不是来自可确认的模型选腿优势：相对机械两腿各半只高
{percent(forward_eu_mechanical.roi_delta)}。这更像“欧洲 distance=2 NO 本身仍可能有 carry”，
而不是“五模型已经稳定知道该买哪一端”。

## 概率质量

负 delta 才代表模型优于市场。

| 固定切片 | Brier Δ vs market | 95% CI | Logloss Δ vs market | 95% CI |
|---|---:|---:|---:|---:|
| Europe-all | {forward_eu_prob.brier_delta:.5f} | [{forward_eu_prob.brier_delta_ci_low:.5f}, {forward_eu_prob.brier_delta_ci_high:.5f}] | {forward_eu_prob.logloss_delta:.5f} | [{forward_eu_prob.logloss_delta_ci_low:.5f}, {forward_eu_prob.logloss_delta_ci_high:.5f}] |
| 固定五城 | {forward_five_prob.brier_delta:.5f} | [{forward_five_prob.brier_delta_ci_low:.5f}, {forward_five_prob.brier_delta_ci_high:.5f}] | {forward_five_prob.logloss_delta:.5f} | [{forward_five_prob.logloss_delta_ci_low:.5f}, {forward_five_prob.logloss_delta_ci_high:.5f}] |

两个切片的点估计都比市场差；这就是为什么不能拿正 ROI 直接升级 live。

## 城市变化

固定五城 blind ROI：

{city_markdown(predefined)}

其他欧洲城市 blind ROI：

{city_markdown(other)}

London/Paris/Milan 的点估计较好，但这是看完 forward 后才观察到的，不能反过来组成
新的“赢家城市池”。它只说明**宽泛 Europe 假设比固定五城故事更值得继续收集**。

## Gate 与后续

- significance gate：FAIL（ROI 与相对基线 CI 均跨 0）。
- baseline gate：FAIL（相对机械组合仅小幅正，概率 proper score 更差）。
- frozen-forward gate：INCOMPLETE（8/15 个新日期）。
- action：Europe-all 保留 frozen collector/shadow；固定五城不再作为主假设；满 15 个
  新日期后按同一代码、同一城市集合、同一分母重跑，期间不换城市、不调阈值。
"""


def main() -> None:
    args = parse_args()
    discovery = load_window(args.discovery, "discovery")
    forward = load_window(args.forward, "blind_forward")
    rows = pd.concat([discovery, forward], ignore_index=True)
    discovery_probability = load_probability_window(
        args.discovery_candidates, "discovery"
    )
    forward_probability = load_probability_window(
        args.forward_candidates, "blind_forward"
    )
    probability_rows = pd.concat(
        [discovery_probability, forward_probability], ignore_index=True
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    performance = performance_scorecard(rows, draws=args.draws)
    paired = paired_scorecard(rows, draws=args.draws)
    probability = probability_scorecard(probability_rows, draws=args.draws)
    cities = city_forward_scorecard(rows, draws=args.draws)

    performance.to_csv(args.output_dir / "performance_scorecard.csv", index=False)
    paired.to_csv(args.output_dir / "paired_scorecard.csv", index=False)
    probability.to_csv(
        args.output_dir / "probability_scorecard.csv", index=False
    )
    cities.to_csv(args.output_dir / "city_forward_scorecard.csv", index=False)

    forward_eu = performance[
        (performance["window"] == "blind_forward")
        & (performance["group"] == "europe_all")
        & (performance["policy"] == "equal_all5")
    ].iloc[0]
    forward_five = performance[
        (performance["window"] == "blind_forward")
        & (performance["group"] == "europe_cloud_break")
        & (performance["policy"] == "equal_all5")
    ].iloc[0]
    summary = {
        "strategy": "d1_distance2_single_no_equal_all5",
        "forward_start": forward_eu["start_date"],
        "forward_end": forward_eu["end_date"],
        "forward_dates": int(forward_eu["target_dates"]),
        "preregistered_minimum_dates": 15,
        "europe_all_forward_roi": float(forward_eu["fee_adjusted_roi"]),
        "five_city_forward_roi": float(forward_five["fee_adjusted_roi"]),
        "status": {
            "europe_all": "inconclusive_positive_keep_frozen",
            "europe_cloud_break": "forward_not_reproduced_dormant",
            "live": "not_authorized",
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    args.report.write_text(
        render_report(performance, paired, probability, cities),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
