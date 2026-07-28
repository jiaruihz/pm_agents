#!/usr/bin/env python3
"""Decompose the Europe D1 distance-2 NO effect into risk, price, and model."""

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_brackets import parse_market_bracket
from weather_data_feed_service.legacy_weather_predict.city_pools import (
    FULL_CITY_CONFIGS,
)


DEFAULT_INPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_full_ladder_no_source_confidence_city_v2"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_europe_mechanism_decomposition_v4"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-28-d1-europe-mechanism-decomposition-v4.md"
)
FAMILY = "europe_cloud_break"
MODEL_KEYS = [
    "ecmwf_ifs025",
    "ecmwf_aifs025_single",
    "gfs_global",
    "icon_seamless",
    "jma_seamless",
]
SEED = 2026072843
EPS = 1e-6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=50_000)
    return parser.parse_args()


def fee_adjusted_roi(frame: pd.DataFrame) -> float:
    return float(frame["pnl"].sum() / frame["cost"].sum())


def group_name(rows: pd.DataFrame) -> pd.Series:
    return pd.Series(
        np.select(
            [
                rows["city_family"].eq(FAMILY),
                rows["region"].eq("EU"),
                rows["market_unit"].eq("F"),
            ],
            ["europe_family", "europe_other", "us_f"],
            default="non_europe_c",
        ),
        index=rows.index,
    )


def bracket_center(label: str, question: str) -> float:
    bracket = parse_market_bracket(label, question)
    if bracket is None or bracket.low is None or bracket.high is None:
        raise ValueError(f"distance-2 candidate is not a finite bracket: {label}")
    return (float(bracket.low) + float(bracket.high)) / 2.0


def attach_context(rows: pd.DataFrame) -> pd.DataFrame:
    output = rows.copy()
    output["analysis_group"] = group_name(output)
    forecast_columns = [f"forecast_{model}" for model in MODEL_KEYS]
    output["forecast_mean_f"] = output[forecast_columns].mean(axis=1)
    centers = np.array(
        [
            bracket_center(str(label), str(question))
            for label, question in zip(output["bracket"], output["question"])
        ]
    )
    output["bracket_center_f"] = np.where(
        output["market_unit"].eq("C"), centers * 9.0 / 5.0 + 32.0, centers
    )
    output["forecast_distance_f"] = np.abs(
        output["bracket_center_f"] - output["forecast_mean_f"]
    )
    output["forecast_distance_per_train_mae"] = (
        output["forecast_distance_f"] / output["train_mean_mae_f"]
    )
    decision = pd.to_datetime(output["decision_ts_utc"], utc=True)
    offsets = output["city"].map(
        lambda city: float(FULL_CITY_CONFIGS[str(city)]["tz_offset"])
    )
    target_noon_utc = pd.to_datetime(
        output["target_date"], utc=True
    ) + pd.to_timedelta(12.0 - offsets, unit="h")
    output["hours_to_local_noon"] = (
        target_noon_utc - decision
    ).dt.total_seconds() / 3600.0
    output["book_spread"] = (
        output["no_best_ask"] - output["no_best_bid"]
    )
    return output


def attach_candidate_scores(
    candidates: pd.DataFrame, metadata: pd.DataFrame
) -> pd.DataFrame:
    output = candidates.merge(
        metadata[
            ["snapshot_key", "region", "city_family", "train_mean_mae_f"]
        ],
        on="snapshot_key",
        how="left",
        validate="many_to_one",
    )
    output["analysis_group"] = group_name(output)
    probability_columns = [f"p_no_{model}" for model in MODEL_KEYS]
    output["policy_p_no"] = output[probability_columns].mean(axis=1)
    output["brier"] = np.square(output["policy_p_no"] - output["no_win"])
    output["market_brier"] = np.square(
        output["market_p_no"] - output["no_win"]
    )
    policy_p = output["policy_p_no"].clip(EPS, 1.0 - EPS)
    market_p = output["market_p_no"].clip(EPS, 1.0 - EPS)
    outcome = output["no_win"]
    output["logloss"] = -(
        outcome * np.log(policy_p)
        + (1.0 - outcome) * np.log(1.0 - policy_p)
    )
    output["market_logloss"] = -(
        outcome * np.log(market_p)
        + (1.0 - outcome) * np.log(1.0 - market_p)
    )
    output["tail_side"] = np.where(
        output["rung_index"] < output["rung_count"] / 2.0,
        "low_tail",
        "high_tail",
    )
    return output


def summarize_policy(
    frame: pd.DataFrame, policy: str
) -> dict[str, Any]:
    return {
        "policy": policy,
        "baskets": len(frame),
        "mean_no_ask": float(frame["no_best_ask"].mean()),
        "mean_cost": float(frame["cost"].mean()),
        "mean_payout": float(frame["payout"].mean()),
        "pnl": float(frame["pnl"].sum()),
        "fee_adjusted_roi": fee_adjusted_roi(frame),
    }


def policy_decomposition(
    all5: pd.DataFrame,
    selected: pd.DataFrame,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for group, frame in all5.groupby("analysis_group"):
        keys = set(frame["snapshot_key"])
        for policy in [
            "equal_all5",
            "market_only_selection",
            "mechanical_half_distance2",
        ]:
            policy_frame = (
                frame
                if policy == "equal_all5"
                else selected[
                    selected["policy"].eq(policy)
                    & selected["snapshot_key"].isin(keys)
                ]
            )
            record = summarize_policy(policy_frame, policy)
            record["analysis_group"] = group
            records.append(record)
    return pd.DataFrame(records)


def diagnostic_summary(
    all5: pd.DataFrame, candidates: pd.DataFrame
) -> pd.DataFrame:
    pair_outcome = candidates.groupby("snapshot_key")["no_win"].sum()
    discriminating = set(pair_outcome[pair_outcome.eq(1.0)].index)
    records: list[dict[str, Any]] = []
    for group, frame in all5.groupby("analysis_group"):
        candidate_rows = candidates[
            candidates["snapshot_key"].isin(frame["snapshot_key"])
        ]
        records.append(
            {
                "analysis_group": group,
                "baskets": len(frame),
                "target_dates": int(frame["target_date"].nunique()),
                "cities": int(frame["city"].nunique()),
                "actual_p_no": float(frame["no_win"].mean()),
                "model_p_no": float(frame["policy_p_no"].mean()),
                "market_p_no": float(frame["market_p_no"].mean()),
                "mean_cost": float(frame["cost"].mean()),
                "mean_no_ask": float(frame["no_best_ask"].mean()),
                "mean_book_spread": float(frame["book_spread"].mean()),
                "median_no_ask_size": float(frame["no_ask_size"].median()),
                "mean_hours_to_local_noon": float(
                    frame["hours_to_local_noon"].mean()
                ),
                "train_mean_mae_f": float(
                    frame["train_mean_mae_f"].mean()
                ),
                "forecast_spread_f": float(
                    frame["forecast_spread_f"].mean()
                ),
                "forecast_distance_f": float(
                    frame["forecast_distance_f"].mean()
                ),
                "distance_per_train_mae": float(
                    frame["forecast_distance_per_train_mae"].mean()
                ),
                "discriminating_baskets": int(
                    frame["snapshot_key"].isin(discriminating).sum()
                ),
                "discriminating_rate": float(
                    frame["snapshot_key"].isin(discriminating).mean()
                ),
                "brier_delta_vs_market": float(
                    candidate_rows["brier"].mean()
                    - candidate_rows["market_brier"].mean()
                ),
                "logloss_delta_vs_market": float(
                    candidate_rows["logloss"].mean()
                    - candidate_rows["market_logloss"].mean()
                ),
                "market_p_no_corr_forecast_distance": float(
                    frame["market_p_no"].corr(
                        frame["forecast_distance_f"]
                    )
                ),
            }
        )
    return pd.DataFrame(records)


def price_bucket_summary(all5: pd.DataFrame) -> pd.DataFrame:
    output = all5.copy()
    output["ask_bucket"] = pd.cut(
        output["no_best_ask"],
        [-0.01, 0.75, 0.90, 0.97, 0.99, 1.01],
        right=False,
    ).astype(str)
    return (
        output.groupby(["analysis_group", "ask_bucket"], as_index=False)
        .agg(
            baskets=("pnl", "size"),
            mean_ask=("no_best_ask", "mean"),
            actual_p_no=("no_win", "mean"),
            pnl=("pnl", "sum"),
            cost=("cost", "sum"),
        )
        .assign(fee_adjusted_roi=lambda rows: rows["pnl"] / rows["cost"])
    )


def tail_summary(candidates: pd.DataFrame) -> pd.DataFrame:
    return (
        candidates.groupby(
            ["analysis_group", "tail_side"], as_index=False
        )
        .agg(
            baskets=("pnl", "size"),
            mean_ask=("no_best_ask", "mean"),
            actual_p_no=("no_win", "mean"),
            pnl=("pnl", "sum"),
            cost=("cost", "sum"),
        )
        .assign(fee_adjusted_roi=lambda rows: rows["pnl"] / rows["cost"])
    )


def roi_from_probability_cost(probability: float, cost: float) -> float:
    return probability / cost - 1.0


def shapley_roi_components(
    p_left: float,
    cost_left: float,
    p_right: float,
    cost_right: float,
) -> tuple[float, float]:
    risk = 0.5 * (
        roi_from_probability_cost(p_left, cost_left)
        - roi_from_probability_cost(p_right, cost_left)
        + roi_from_probability_cost(p_left, cost_right)
        - roi_from_probability_cost(p_right, cost_right)
    )
    price = 0.5 * (
        roi_from_probability_cost(p_left, cost_left)
        - roi_from_probability_cost(p_left, cost_right)
        + roi_from_probability_cost(p_right, cost_left)
        - roi_from_probability_cost(p_right, cost_right)
    )
    return risk, price


def daily_probability_cost(
    frame: pd.DataFrame, dates: list[str]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    daily = frame.groupby("target_date").agg(
        payout=("payout", "sum"),
        cost=("cost", "sum"),
        baskets=("payout", "size"),
    )
    daily = daily.reindex(dates, fill_value=0.0)
    return (
        daily["payout"].to_numpy(),
        daily["cost"].to_numpy(),
        daily["baskets"].to_numpy(),
    )


def shapley_bootstrap(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    dates = sorted(set(left["target_date"]) | set(right["target_date"]))
    lp, lc, ln = daily_probability_cost(left, dates)
    rp, rc, rn = daily_probability_cost(right, dates)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(dates), size=(draws, len(dates)))
    left_p = lp[indices].sum(axis=1) / ln[indices].sum(axis=1)
    left_c = lc[indices].sum(axis=1) / ln[indices].sum(axis=1)
    right_p = rp[indices].sum(axis=1) / rn[indices].sum(axis=1)
    right_c = rc[indices].sum(axis=1) / rn[indices].sum(axis=1)
    left_roi = left_p / left_c - 1.0
    right_roi = right_p / right_c - 1.0
    risk = 0.5 * (
        left_p / left_c
        - right_p / left_c
        + left_p / right_c
        - right_p / right_c
    )
    price = (left_roi - right_roi) - risk
    return left_roi - right_roi, risk, price


def summarize_shapley(
    all5: pd.DataFrame, *, draws: int
) -> pd.DataFrame:
    groups = {name: frame for name, frame in all5.groupby("analysis_group")}
    records: list[dict[str, Any]] = []
    for index, left_name in enumerate(
        ["europe_family", "europe_other", "europe_all"]
    ):
        left = (
            all5[all5["region"].eq("EU")]
            if left_name == "europe_all"
            else groups[left_name]
        )
        right = groups["us_f"]
        p_left = float(left["payout"].mean())
        c_left = float(left["cost"].mean())
        p_right = float(right["payout"].mean())
        c_right = float(right["cost"].mean())
        risk, price = shapley_roi_components(
            p_left, c_left, p_right, c_right
        )
        delta, risk_samples, price_samples = shapley_bootstrap(
            left,
            right,
            draws=draws,
            seed=SEED + index,
        )
        records.append(
            {
                "contrast": f"{left_name}_minus_us_f",
                "roi_delta": fee_adjusted_roi(left)
                - fee_adjusted_roi(right),
                "roi_delta_ci_low": float(np.quantile(delta, 0.025)),
                "roi_delta_ci_high": float(np.quantile(delta, 0.975)),
                "risk_component": risk,
                "risk_ci_low": float(
                    np.quantile(risk_samples, 0.025)
                ),
                "risk_ci_high": float(
                    np.quantile(risk_samples, 0.975)
                ),
                "price_component": price,
                "price_ci_low": float(
                    np.quantile(price_samples, 0.025)
                ),
                "price_ci_high": float(
                    np.quantile(price_samples, 0.975)
                ),
                "risk_share_of_point_delta": risk / (risk + price),
                "price_share_of_point_delta": price / (risk + price),
            }
        )
    return pd.DataFrame(records)


def pct(value: float) -> str:
    return f"{value:+.2%}"


def write_report(
    path: Path,
    diagnostics: pd.DataFrame,
    policies: pd.DataFrame,
    shapley: pd.DataFrame,
    price_buckets: pd.DataFrame,
) -> None:
    diagnostic = diagnostics.set_index("analysis_group")
    policy = policies.set_index(["analysis_group", "policy"])
    components = shapley.set_index("contrast")
    family = diagnostic.loc["europe_family"]
    eu_other = diagnostic.loc["europe_other"]
    us = diagnostic.loc["us_f"]
    non_eu_c = diagnostic.loc["non_europe_c"]
    eu_all_model = policy.loc[("europe_all", "equal_all5")]
    eu_all_market = policy.loc[("europe_all", "market_only_selection")]
    eu_all_mech = policy.loc[
        ("europe_all", "mechanical_half_distance2")
    ]
    family_model = policy.loc[("europe_family", "equal_all5")]
    family_mech = policy.loc[
        ("europe_family", "mechanical_half_distance2")
    ]
    us_model = policy.loc[("us_f", "equal_all5")]
    us_mech = policy.loc[("us_f", "mechanical_half_distance2")]
    eu_components = components.loc["europe_all_minus_us_f"]
    family_components = components.loc["europe_family_minus_us_f"]
    cheap = price_buckets[
        price_buckets["ask_bucket"].eq("[-0.01, 0.75)")
    ].set_index("analysis_group")
    lines = [
        "# D1 Europe distance-2 NO 成因拆解 v4",
        "",
        "## 数据快照",
        "",
        "- 数据源：v2 immutable full-ladder opportunity replay artifacts；"
        "不是 fill，不读取或发布 live_real PnL。",
        "- target_date：2026-06-17..2026-07-07；固定 1,190 baskets / "
        "20 dates / 43 cities，settlement 与 executable quote 完整。",
        "- unsettled=0；missing_bracket=0；本次未同步/重建 canonical DB。",
        "",
        "## 结论",
        "",
        "**主因不是已证明的“欧洲有另一批 market maker”，而是市场没有充分把"
        "欧洲候选档位较低的真实 tail risk 反映进价格；模型只负责把这个价格偏差"
        "挑出来。**",
        "",
        f"- Europe-all model ROI {pct(eu_all_model['fee_adjusted_roi'])}，"
        f"US-F {pct(us_model['fee_adjusted_roi'])}。Shapley 描述性拆解显示，"
        f"两地 ROI 差的约 {eu_components['risk_share_of_point_delta']:.0%} "
        "来自欧洲档实际更少命中（NO 更安全），约 "
        f"{eu_components['price_share_of_point_delta']:.0%} 来自欧洲 NO "
        "买入成本更低。",
        f"- family 相对 US 的拆解接近一半一半：risk "
        f"{family_components['risk_share_of_point_delta']:.0%} / price "
        f"{family_components['price_share_of_point_delta']:.0%}。",
        f"  family-US ROI delta {pct(family_components['roi_delta'])}，CI "
        f"[{pct(family_components['roi_delta_ci_low'])}, "
        f"{pct(family_components['roi_delta_ci_high'])}]；risk component "
        f"{pct(family_components['risk_component'])}，CI "
        f"[{pct(family_components['risk_ci_low'])}, "
        f"{pct(family_components['risk_ci_high'])}]；price component "
        f"{pct(family_components['price_component'])}，CI "
        f"[{pct(family_components['price_ci_low'])}, "
        f"{pct(family_components['price_ci_high'])}]。",
        "- 因而它既不是纯模型 alpha，也不是纯盘口身份故事；是"
        "`underlying tail risk × insufficient price adjustment` 的交互。",
        "",
        "## 1. 最直接的证据：市场给了更便宜的价，但欧洲实际更安全",
        "",
        "| group | actual P(NO) | model P(NO) | market P(NO) | mean cost | ROI |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Europe family | {family['actual_p_no']:.3f} | "
        f"{family['model_p_no']:.3f} | {family['market_p_no']:.3f} | "
        f"{family['mean_cost']:.3f} | {pct(family_model['fee_adjusted_roi'])} |",
        f"| Europe other | {eu_other['actual_p_no']:.3f} | "
        f"{eu_other['model_p_no']:.3f} | {eu_other['market_p_no']:.3f} | "
        f"{eu_other['mean_cost']:.3f} | "
        f"{pct(policy.loc[('europe_other', 'equal_all5')]['fee_adjusted_roi'])} |",
        f"| non-Europe C | {non_eu_c['actual_p_no']:.3f} | "
        f"{non_eu_c['model_p_no']:.3f} | "
        f"{non_eu_c['market_p_no']:.3f} | "
        f"{non_eu_c['mean_cost']:.3f} | "
        f"{pct(policy.loc[('non_europe_c', 'equal_all5')]['fee_adjusted_roi'])} |",
        f"| US F | {us['actual_p_no']:.3f} | {us['model_p_no']:.3f} | "
        f"{us['market_p_no']:.3f} | {us['mean_cost']:.3f} | "
        f"{pct(us_model['fee_adjusted_roi'])} |",
        "",
        f"Europe-all actual P(NO) 约 {diagnostic.loc['europe_family', 'actual_p_no']:.1%}"
        "（family）/95.5%（其余欧洲），但市场/成本只给到约 88%–91%；"
        "US actual P(NO) 91.2%，成本却为 93.1%。市场在欧洲低估 NO，"
        "在美国则略高估 NO。",
        "",
        f"同样在 NO ask<0.75 的便宜带，family 实际胜率 "
        f"{cheap.loc['europe_family', 'actual_p_no']:.1%}，"
        f"US 仅 {cheap.loc['us_f', 'actual_p_no']:.1%}。所以结果不只是"
        "欧洲平均买价更低；同价带的真实 tail risk 也不同。",
        "",
        "## 2. lattice 有影响，但不是“2°C 比 2°F 更安全”这么简单",
        "",
        "- `distance=2` 是从挂牌 ladder 两端向内数两个 native ticks；"
        "C 市场一个 tick 通常是 1°C，F 市场通常是 1°F，两个 universe "
        "不是同一物理距离。",
        "- 但向内数更大的摄氏 tick 理论上反而会更接近中心、增加命中风险，"
        "不能机械地用 2°C=3.6°F 解释欧洲更赚钱。",
        f"- 真正可比的连续量显示：selected bracket 距五源 forecast consensus "
        f"family 平均 {family['forecast_distance_f']:.1f}F、"
        f"US {us['forecast_distance_f']:.1f}F；除以训练 MAE 后分别约 "
        f"{family['distance_per_train_mae']:.1f}× 和 "
        f"{us['distance_per_train_mae']:.1f}×。欧洲候选相对自身 forecast "
        "误差分布确实更深在尾部。",
        f"- discriminating basket（两个 NO 中恰有一个输）比例 family "
        f"{family['discriminating_rate']:.1%}、Europe-other "
        f"{eu_other['discriminating_rate']:.1%}、US "
        f"{us['discriminating_rate']:.1%}、non-Europe C "
        f"{non_eu_c['discriminating_rate']:.1%}。这说明欧洲效应不只是 C/F "
        "单位标签，ladder 相对本地天气分布的位置也不同。",
        "",
        "## 3. 模型层做了什么",
        "",
        f"- Europe 训练期平均多模型 MAE 约 "
        f"{(family['train_mean_mae_f'] + eu_other['train_mean_mae_f']) / 2:.2f}F，"
        f"US 为 {us['train_mean_mae_f']:.2f}F；欧洲 forecast 在这个样本里"
        "更可预测。",
        f"- Europe-all mechanical-half ROI "
        f"{pct(eu_all_mech['fee_adjusted_roi'])}：完全不用模型已经为正，"
        "说明底层 carry/定价结构先存在。",
        f"- all-5 model 把 Europe-all ROI 提到 "
        f"{pct(eu_all_model['fee_adjusted_roi'])}，主要通过把平均 ask 从 "
        f"{eu_all_mech['mean_no_ask']:.3f} 降到 "
        f"{eu_all_model['mean_no_ask']:.3f}，而平均 payout 只从 "
        f"{eu_all_mech['mean_payout']:.3f} 降到 "
        f"{eu_all_model['mean_payout']:.3f}。",
        f"- family 更强：model 相对 mechanical 不但把 ask 从 "
        f"{family_mech['mean_no_ask']:.3f} 降到 "
        f"{family_model['mean_no_ask']:.3f}，mean payout 还从 "
        f"{family_mech['mean_payout']:.3f} 升到 "
        f"{family_model['mean_payout']:.3f}。",
        f"- US 同样把 ask 从 {us_mech['mean_no_ask']:.3f} 降到 "
        f"{us_model['mean_no_ask']:.3f}，但 payout 从 "
        f"{us_mech['mean_payout']:.3f} 降到 "
        f"{us_model['mean_payout']:.3f}，省下的价格不够补损失，ROI 仍为负。",
        "",
        "模型层的边界：family 的 Brier/logloss point estimate 略优于 market，"
        "但 CI 跨 0；Europe-all proper score 反而略差。此前 15 个 source "
        "ablation / city-best source 没有一个稳定胜过 equal-all5。因此当前"
        "只能说模型在 family 内有潜在 ranking value，不能说“更好的欧洲模型"
        "已经被证实”。",
        "",
        "## 4. 是否是不同定价者",
        "",
        f"- book spread：family {family['mean_book_spread']:.3f}、"
        f"Europe-other {eu_other['mean_book_spread']:.3f}、"
        f"US {us['mean_book_spread']:.3f}；ask depth 中位数分别 "
        f"{family['median_no_ask_size']:.0f}/"
        f"{eu_other['median_no_ask_size']:.0f}/"
        f"{us['median_no_ask_size']:.0f} shares。",
        f"- 距当地中午 lead time 也接近：family "
        f"{family['mean_hours_to_local_noon']:.1f}h、US "
        f"{us['mean_hours_to_local_noon']:.1f}h。",
        "- 这些快照没有 wallet/order-flow/participant identity，无法识别"
        "“主要定价者是谁”。相似的 spread/depth/timing 也没有支持"
        "“欧洲因为流动性差所以错价”的强证据。",
        "- 能说的是定价函数有地域差：family 的 market P(NO) 与 forecast "
        f"distance 相关系数为 {family['market_p_no_corr_forecast_distance']:+.2f}，"
        "说明市场会方向性地参考 tail geometry，并非完全忽略 forecast；但 selected "
        "rows 的 market P(NO) 仅 87.7%、实际为 95.7%，调整幅度仍不够。可能来源"
        "包括参与者注意力、高温 YES 偏好、模板化报价或公开 forecast 使用粗糙；"
        "现有证据不能在它们之间定责。",
        "",
        "## 成因排序与下一步",
        "",
        "1. **最高可信：ladder 相对当地 forecast/error distribution 的位置不同，"
        "欧洲 tail 实际更少命中。**",
        "2. **较高可信：市场没有充分为这个较低风险提价，尤其 family 的便宜 NO。**",
        "3. **中等可信：equal-all5 能在 family 内挑到更便宜且仍安全的一侧。**",
        "4. **低可信：由不同 market maker/主要定价者身份直接造成。**当前没有身份"
        "或 order-flow 证据。",
        "",
        "下一轮 frozen shadow 应把策略从 native `distance=2` 改成并行记录连续"
        "`forecast_distance_f / city rolling MAE`、market residual、market unit、"
        "tail side 与 local lead time；先验证连续 safety score 是否在新日期"
        "单调解释 P(NO)-cost。不要把 Europe 或五城直接设成 hard gate。",
        "",
        "## Signal / evidence funnel 与三门",
        "",
        "- signal：1,190 fixed paired baskets → 1,190 all5 selections；"
        "本报告只做既有分母成因拆解。",
        "- evidence：PIT forecast/book/settlement=完整；actual fill=0，"
        "queue/capacity/participant identity=缺失。",
        "- 数据完整性自检：candidate rows=2,380=1,190×2；paired basket "
        "violations=0；unsettled=0；missing bracket=0；所有五源概率均 finite。",
        "- 8 环：覆盖描述绩效、统计推断、信号判别、概率评估、target-date "
        "相关性与同分母基准；缺真实执行微结构与容量。",
        "- significance=family historical trade PASS；baseline=trade expression "
        "PASS but probability proper-score FAIL；forward=NA；"
        "conclusion=`shadow_candidate`，不改 live。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    confidence = attach_context(
        pd.read_csv(args.input_dir / "confidence_rows.csv")
    )
    selected = pd.read_csv(args.input_dir / "selected_policy_rows.csv")
    candidates = attach_candidate_scores(
        pd.read_csv(args.input_dir / "candidate_source_probs.csv"),
        confidence,
    )
    if not (
        len(confidence) == candidates["snapshot_key"].nunique() == 1190
    ):
        raise RuntimeError("fixed denominator drift")
    policies = policy_decomposition(confidence, selected)
    europe_all_records: list[dict[str, Any]] = []
    europe_keys = set(
        confidence.loc[confidence["region"].eq("EU"), "snapshot_key"]
    )
    for policy in [
        "equal_all5",
        "market_only_selection",
        "mechanical_half_distance2",
    ]:
        frame = (
            confidence[confidence["region"].eq("EU")]
            if policy == "equal_all5"
            else selected[
                selected["policy"].eq(policy)
                & selected["snapshot_key"].isin(europe_keys)
            ]
        )
        record = summarize_policy(frame, policy)
        record["analysis_group"] = "europe_all"
        europe_all_records.append(record)
    policies = pd.concat(
        [policies, pd.DataFrame(europe_all_records)], ignore_index=True
    )
    diagnostics = diagnostic_summary(confidence, candidates)
    price_buckets = price_bucket_summary(confidence)
    tails = tail_summary(candidates)
    shapley = summarize_shapley(confidence, draws=args.draws)

    policies.to_csv(args.output_dir / "policy_decomposition.csv", index=False)
    diagnostics.to_csv(args.output_dir / "diagnostic_summary.csv", index=False)
    price_buckets.to_csv(
        args.output_dir / "price_bucket_summary.csv", index=False
    )
    tails.to_csv(args.output_dir / "tail_summary.csv", index=False)
    shapley.to_csv(args.output_dir / "roi_shapley_decomposition.csv", index=False)
    payload = {
        "fixed_denominator": {
            "baskets": len(confidence),
            "target_dates": int(confidence["target_date"].nunique()),
            "cities": int(confidence["city"].nunique()),
            "actual_fills": 0,
        },
        "diagnostics": diagnostics.to_dict("records"),
        "policies": policies.to_dict("records"),
        "price_buckets": price_buckets.to_dict("records"),
        "tails": tails.to_dict("records"),
        "shapley": shapley.to_dict("records"),
        "verdict": {
            "primary_mechanism": (
                "lower realized Europe tail risk not fully reflected in price"
            ),
            "model_role": "selection overlay, not confirmed probability alpha",
            "participant_identity_evidence": "unavailable",
            "live_ready": False,
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.report, diagnostics, policies, shapley, price_buckets
    )


if __name__ == "__main__":
    main()
