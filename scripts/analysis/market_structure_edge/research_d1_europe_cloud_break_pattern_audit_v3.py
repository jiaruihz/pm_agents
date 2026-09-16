#!/usr/bin/env python3
"""Audit whether the D1 Europe cloud-break family is signal or city selection."""

from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_full_ladder_no_source_confidence_city_v2"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_europe_cloud_break_pattern_audit_v3"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-28-d1-europe-cloud-break-pattern-audit-v3.md"
)
FAMILY = "europe_cloud_break"
FAMILY_CITIES = {"Amsterdam", "Helsinki", "Madrid", "Munich", "Warsaw"}
TAXONOMY_CREATED_AT = "2026-06-24T20:24:38+08:00"
STRICT_POST_TAXONOMY_DATE = "2026-06-25"
SEED = 2026072831
MODEL_KEYS = [
    "ecmwf_ifs025",
    "ecmwf_aifs025_single",
    "gfs_global",
    "icon_seamless",
    "jma_seamless",
]
EPS = 1e-6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=50_000)
    parser.add_argument("--partition-draws", type=int, default=200_000)
    return parser.parse_args()


def roi(frame: pd.DataFrame) -> float:
    cost = float(frame["cost"].sum())
    return float(frame["pnl"].sum()) / cost if cost else math.nan


def two_sided_p(samples: np.ndarray) -> float:
    return float(
        min(1.0, 2.0 * min(np.mean(samples <= 0), np.mean(samples >= 0)))
    )


def attach_equal_all5_scores(candidates: pd.DataFrame) -> pd.DataFrame:
    output = candidates.copy()
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
    return output


def block_roi_samples(
    frame: pd.DataFrame, *, draws: int, seed: int
) -> np.ndarray:
    daily = frame.groupby("target_date")[["pnl", "cost"]].sum()
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(daily), size=(draws, len(daily)))
    pnl = daily["pnl"].to_numpy()[indices].sum(axis=1)
    cost = daily["cost"].to_numpy()[indices].sum(axis=1)
    return pnl / cost


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


def block_score_delta_samples(
    rows: pd.DataFrame,
    score: str,
    market_score: str,
    *,
    draws: int,
    seed: int,
) -> np.ndarray:
    daily = rows.groupby("target_date")[[score, market_score]].agg(
        ["sum", "size"]
    )
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(daily), size=(draws, len(daily)))
    return (
        daily[(score, "sum")].to_numpy()[indices].sum(axis=1)
        / daily[(score, "size")].to_numpy()[indices].sum(axis=1)
        - daily[(market_score, "sum")].to_numpy()[indices].sum(axis=1)
        / daily[(market_score, "size")].to_numpy()[indices].sum(axis=1)
    )


def group_masks(rows: pd.DataFrame) -> dict[str, pd.Series]:
    family = rows["city_family"].eq(FAMILY)
    return {
        FAMILY: family,
        "eu_non_family": rows["region"].eq("EU") & ~family,
        "eu_all": rows["region"].eq("EU"),
        "c_unit_non_family": rows["market_unit"].eq("C") & ~family,
        "c_unit_all": rows["market_unit"].eq("C"),
        "f_unit_all": rows["market_unit"].eq("F"),
        "all_non_family": ~family,
    }


def summarize_groups(
    selected_all5: pd.DataFrame,
    selected_market: pd.DataFrame,
    selected_mechanical: pd.DataFrame,
    candidates: pd.DataFrame,
    *,
    draws: int,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for index, (name, mask) in enumerate(group_masks(selected_all5).items()):
        frame = selected_all5[mask].copy()
        keys = set(frame["snapshot_key"])
        market = selected_market[selected_market["snapshot_key"].isin(keys)]
        mechanical = selected_mechanical[
            selected_mechanical["snapshot_key"].isin(keys)
        ]
        candidate_rows = candidates[candidates["snapshot_key"].isin(keys)]
        samples = block_roi_samples(
            frame, draws=draws, seed=SEED + index
        )
        market_delta = paired_roi_delta_samples(
            frame, market, draws=draws, seed=SEED + 100 + index
        )
        mechanical_delta = paired_roi_delta_samples(
            frame, mechanical, draws=draws, seed=SEED + 200 + index
        )
        brier_delta = block_score_delta_samples(
            candidate_rows,
            "brier",
            "market_brier",
            draws=draws,
            seed=SEED + 300 + index,
        )
        logloss_delta = block_score_delta_samples(
            candidate_rows,
            "logloss",
            "market_logloss",
            draws=draws,
            seed=SEED + 400 + index,
        )
        early = frame[frame["target_date"] < STRICT_POST_TAXONOMY_DATE]
        post = frame[frame["target_date"] >= STRICT_POST_TAXONOMY_DATE]
        post_samples = (
            block_roi_samples(
                post, draws=draws, seed=SEED + 500 + index
            )
            if not post.empty
            else np.array([math.nan])
        )
        records.append(
            {
                "group": name,
                "baskets": len(frame),
                "target_dates": int(frame["target_date"].nunique()),
                "cities": int(frame["city"].nunique()),
                "mean_no_ask": float(frame["no_best_ask"].mean()),
                "win_rate": float(frame["payout"].gt(0).mean()),
                "train_mean_mae_f": float(frame["train_mean_mae_f"].mean()),
                "fee_adjusted_roi": roi(frame),
                "roi_ci_low": float(np.quantile(samples, 0.025)),
                "roi_ci_high": float(np.quantile(samples, 0.975)),
                "market_selection_roi": roi(market),
                "roi_delta_vs_market": roi(frame) - roi(market),
                "market_delta_ci_low": float(
                    np.quantile(market_delta, 0.025)
                ),
                "market_delta_ci_high": float(
                    np.quantile(market_delta, 0.975)
                ),
                "mechanical_roi": roi(mechanical),
                "roi_delta_vs_mechanical": roi(frame) - roi(mechanical),
                "mechanical_delta_ci_low": float(
                    np.quantile(mechanical_delta, 0.025)
                ),
                "mechanical_delta_ci_high": float(
                    np.quantile(mechanical_delta, 0.975)
                ),
                "brier_delta_vs_market": float(
                    candidate_rows["brier"].mean()
                    - candidate_rows["market_brier"].mean()
                ),
                "brier_delta_ci_low": float(
                    np.quantile(brier_delta, 0.025)
                ),
                "brier_delta_ci_high": float(
                    np.quantile(brier_delta, 0.975)
                ),
                "logloss_delta_vs_market": float(
                    candidate_rows["logloss"].mean()
                    - candidate_rows["market_logloss"].mean()
                ),
                "logloss_delta_ci_low": float(
                    np.quantile(logloss_delta, 0.025)
                ),
                "logloss_delta_ci_high": float(
                    np.quantile(logloss_delta, 0.975)
                ),
                "pre_taxonomy_roi": roi(early),
                "post_taxonomy_baskets": len(post),
                "post_taxonomy_dates": int(post["target_date"].nunique()),
                "post_taxonomy_roi": roi(post),
                "post_taxonomy_ci_low": float(
                    np.quantile(post_samples, 0.025)
                ),
                "post_taxonomy_ci_high": float(
                    np.quantile(post_samples, 0.975)
                ),
            }
        )
    return pd.DataFrame(records)


def summarize_contrasts(
    selected_all5: pd.DataFrame, *, draws: int
) -> pd.DataFrame:
    masks = group_masks(selected_all5)
    family = selected_all5[masks[FAMILY]]
    controls = [
        "eu_non_family",
        "c_unit_non_family",
        "f_unit_all",
        "all_non_family",
    ]
    records: list[dict[str, Any]] = []
    for index, name in enumerate(controls):
        control = selected_all5[masks[name]]
        samples = paired_roi_delta_samples(
            family,
            control,
            draws=draws,
            seed=SEED + 700 + index,
        )
        records.append(
            {
                "contrast": f"{FAMILY}_minus_{name}",
                "family_roi": roi(family),
                "control_roi": roi(control),
                "roi_delta": roi(family) - roi(control),
                "delta_ci_low": float(np.quantile(samples, 0.025)),
                "delta_ci_high": float(np.quantile(samples, 0.975)),
                "delta_p": two_sided_p(samples),
            }
        )
    return pd.DataFrame(records)


def city_combination_null(
    selected_all5: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, float]]:
    city_agg = (
        selected_all5.groupby("city")[["pnl", "cost"]].sum().sort_index()
    )
    family_roi = roi(
        selected_all5[selected_all5["city"].isin(FAMILY_CITIES)]
    )

    def enumerate_rois(cities: list[str]) -> np.ndarray:
        values = city_agg.loc[cities, ["pnl", "cost"]].to_numpy()
        output = np.empty(math.comb(len(cities), len(FAMILY_CITIES)))
        for index, combination in enumerate(
            itertools.combinations(range(len(cities)), len(FAMILY_CITIES))
        ):
            totals = values[list(combination)].sum(axis=0)
            output[index] = totals[0] / totals[1]
        return output

    all_rois = enumerate_rois(city_agg.index.astype(str).tolist())
    eu_cities = sorted(
        selected_all5.loc[
            selected_all5["region"].eq("EU"), "city"
        ].unique()
    )
    eu_rois = enumerate_rois(eu_cities)
    summary = pd.DataFrame(
        [
            {
                "null": "all_43_choose_5",
                "combinations": len(all_rois),
                "observed_family_roi": family_roi,
                "share_at_least_observed": float(
                    np.mean(all_rois >= family_roi)
                ),
                "observed_percentile": float(
                    np.mean(all_rois <= family_roi)
                ),
                "null_median": float(np.median(all_rois)),
                "null_p95": float(np.quantile(all_rois, 0.95)),
                "null_p99": float(np.quantile(all_rois, 0.99)),
            },
            {
                "null": "eu_10_choose_5",
                "combinations": len(eu_rois),
                "observed_family_roi": family_roi,
                "share_at_least_observed": float(
                    np.mean(eu_rois >= family_roi)
                ),
                "observed_percentile": float(
                    np.mean(eu_rois <= family_roi)
                ),
                "null_median": float(np.median(eu_rois)),
                "null_p95": float(np.quantile(eu_rois, 0.95)),
                "null_p99": float(np.quantile(eu_rois, 0.99)),
            },
        ]
    )
    return summary, {"family_roi": family_roi}


def randomized_family_scan(
    selected_all5: pd.DataFrame, *, draws: int
) -> tuple[pd.DataFrame, np.ndarray]:
    city_agg = (
        selected_all5.groupby("city")[["pnl", "cost"]].sum().sort_index()
    )
    values = city_agg.to_numpy()
    family_sizes = (
        selected_all5.groupby("city_family")["city"]
        .nunique()
        .sort_index()
        .astype(int)
        .tolist()
    )
    if sum(family_sizes) != len(city_agg):
        raise RuntimeError("family-size partition does not cover all cities")
    observed = roi(
        selected_all5[selected_all5["city_family"].eq(FAMILY)]
    )
    rng = np.random.default_rng(SEED + 900)
    maxima = np.empty(draws)
    for draw in range(draws):
        shuffled = values[rng.permutation(len(values))]
        offset = 0
        group_rois: list[float] = []
        for size in family_sizes:
            totals = shuffled[offset : offset + size].sum(axis=0)
            group_rois.append(float(totals[0] / totals[1]))
            offset += size
        maxima[draw] = max(group_rois)
    result = pd.DataFrame(
        [
            {
                "null": "random_city_partition_max_of_5_families",
                "draws": draws,
                "family_sizes": ",".join(map(str, family_sizes)),
                "observed_family_roi": observed,
                "share_max_at_least_observed": float(
                    np.mean(maxima >= observed)
                ),
                "max_null_median": float(np.median(maxima)),
                "max_null_p95": float(np.quantile(maxima, 0.95)),
                "max_null_p99": float(np.quantile(maxima, 0.99)),
            }
        ]
    )
    return result, maxima


def normalize_bracket(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def discrimination_rows(
    selected_all5: pd.DataFrame,
    selected_market: pd.DataFrame,
    candidates: pd.DataFrame,
) -> pd.DataFrame:
    pair_outcomes = candidates.groupby("snapshot_key")["no_win"].agg(
        ["size", "sum"]
    )
    discriminating = set(
        pair_outcomes[
            pair_outcomes["size"].eq(2) & pair_outcomes["sum"].eq(1)
        ].index
    )
    model = selected_all5[
        selected_all5["snapshot_key"].isin(discriminating)
    ][
        [
            "snapshot_key",
            "city",
            "target_date",
            "city_family",
            "region",
            "market_unit",
            "bracket",
        ]
    ].rename(columns={"bracket": "model_bracket"})
    market = selected_market[
        selected_market["snapshot_key"].isin(discriminating)
    ][["snapshot_key", "bracket"]].rename(
        columns={"bracket": "market_bracket"}
    )
    winner = candidates[
        candidates["snapshot_key"].isin(discriminating)
        & candidates["no_win"].eq(1)
    ][["snapshot_key", "bracket"]].rename(
        columns={"bracket": "winning_no_bracket"}
    )
    rows = model.merge(
        market, on="snapshot_key", validate="one_to_one"
    ).merge(winner, on="snapshot_key", validate="one_to_one")
    rows["model_correct"] = normalize_bracket(rows["model_bracket"]).eq(
        normalize_bracket(rows["winning_no_bracket"])
    )
    rows["market_correct"] = normalize_bracket(rows["market_bracket"]).eq(
        normalize_bracket(rows["winning_no_bracket"])
    )
    return rows


def summarize_discrimination(
    rows: pd.DataFrame, selected_all5: pd.DataFrame, *, draws: int
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    masks = group_masks(selected_all5)
    for index, (name, selected_mask) in enumerate(masks.items()):
        cities = set(selected_all5.loc[selected_mask, "city"])
        group = rows[rows["city"].isin(cities)]
        daily = group.groupby("target_date")[
            ["model_correct", "market_correct"]
        ].agg(["sum", "size"])
        rng = np.random.default_rng(SEED + 1000 + index)
        indices = rng.integers(
            0, len(daily), size=(draws, len(daily))
        )
        delta = (
            daily[("model_correct", "sum")].to_numpy()[indices].sum(axis=1)
            / daily[("model_correct", "size")].to_numpy()[indices].sum(axis=1)
            - daily[("market_correct", "sum")]
            .to_numpy()[indices]
            .sum(axis=1)
            / daily[("market_correct", "size")]
            .to_numpy()[indices]
            .sum(axis=1)
        )
        records.append(
            {
                "group": name,
                "discriminating_baskets": len(group),
                "target_dates": int(group["target_date"].nunique()),
                "model_correct": int(group["model_correct"].sum()),
                "model_accuracy": float(group["model_correct"].mean()),
                "market_correct": int(group["market_correct"].sum()),
                "market_accuracy": float(group["market_correct"].mean()),
                "accuracy_delta": float(
                    group["model_correct"].mean()
                    - group["market_correct"].mean()
                ),
                "accuracy_delta_ci_low": float(
                    np.quantile(delta, 0.025)
                ),
                "accuracy_delta_ci_high": float(
                    np.quantile(delta, 0.975)
                ),
            }
        )
    return pd.DataFrame(records)


def plot_randomization(
    maxima: np.ndarray, observed: float, path: Path
) -> None:
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    ax.hist(maxima * 100, bins=60, color="#5B8FF9", alpha=0.82)
    ax.axvline(
        observed * 100,
        color="#D62728",
        linewidth=2,
        label=f"observed {observed:.2%}",
    )
    ax.set_xlabel("maximum ROI after scanning five random city families (%)")
    ax.set_ylabel("random partitions")
    ax.set_title("City-label randomization: max-of-five selection correction")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def fmt_pct(value: float) -> str:
    return f"{value:+.2%}"


def write_report(
    path: Path,
    groups: pd.DataFrame,
    contrasts: pd.DataFrame,
    combination_null: pd.DataFrame,
    partition_null: pd.DataFrame,
    discrimination: pd.DataFrame,
) -> None:
    family = groups.set_index("group").loc[FAMILY]
    eu_other = groups.set_index("group").loc["eu_non_family"]
    c_other = groups.set_index("group").loc["c_unit_non_family"]
    family_eu = contrasts.set_index("contrast").loc[
        f"{FAMILY}_minus_eu_non_family"
    ]
    all_combo = combination_null.set_index("null").loc["all_43_choose_5"]
    eu_combo = combination_null.set_index("null").loc["eu_10_choose_5"]
    partition = partition_null.iloc[0]
    family_disc = discrimination.set_index("group").loc[FAMILY]
    eu_other_disc = discrimination.set_index("group").loc["eu_non_family"]
    lines = [
        "# D1 `europe_cloud_break` 五城模式审计 v3",
        "",
        "## 结论",
        "",
        "**不是纯随机凑出的五城，但现有证据更像“欧洲市场结构 + "
        "模型选边”的候选模式，不能归因成已经证实的 cloud-break 天气 alpha。**",
        "",
        f"- 五城 {int(family['baskets'])} baskets / "
        f"{int(family['target_dates'])} target dates，fee-adjusted ROI "
        f"{fmt_pct(family['fee_adjusted_roi'])}，target-date bootstrap CI "
        f"[{fmt_pct(family['roi_ci_low'])}, {fmt_pct(family['roi_ci_high'])}]。",
        f"- 但欧洲其余五城也有 {fmt_pct(eu_other['fee_adjusted_roi'])} ROI；"
        f"五城相对它们仅 +{family_eu['roi_delta']:.2%}，CI "
        f"[{fmt_pct(family_eu['delta_ci_low'])}, "
        f"{fmt_pct(family_eu['delta_ci_high'])}]，跨 0。",
        f"- 五城在全部 43 城任取 5 城中位于 "
        f"{all_combo['observed_percentile']:.2%} 分位，raw randomization "
        f"p={all_combo['share_at_least_observed']:.4f}；但只在 10 个欧洲城市"
        f"内比较仅位于 {eu_combo['observed_percentile']:.2%} 分位，"
        f"p={eu_combo['share_at_least_observed']:.3f}。",
        f"- 保持现有 5 个 family 的规模、随机打乱城市并每次挑 ROI 最高 family，"
        f"max-selection p={partition['share_max_at_least_observed']:.4f}。"
        "所以它不像全宇宙随机噪声，但很大一部分可由“欧洲本身较强”解释。",
        "",
        "动作：保留为 `frozen shadow hypothesis`，下一批新 target dates "
        "同时跑 `Europe-all` 与这五城；不要把五城直接变成 live allowlist，"
        "也不要再按样本删成 Amsterdam+Munich。",
        "",
        "## 它是不是事前定义",
        "",
        f"- taxonomy 首次进入 git：`{TAXONOMY_CREATED_AT}`。它来自 "
        "`current-bracket NO / no-reheat` 的描述性 climate taxonomy，"
        "不是为本次 D1 distance-2 NO 策略创建。",
        "- 本研究 target_date 为 2026-06-17..2026-07-07；所以对 6/17..6/24 "
        "不能声称严格事前，对 6/25 起可视为 taxonomy 定义后的时间切片。",
        f"- 严格 post-taxonomy 切片仍有 {int(family['post_taxonomy_baskets'])} "
        f"baskets / {int(family['post_taxonomy_dates'])} dates，ROI "
        f"{fmt_pct(family['post_taxonomy_roi'])}，CI "
        f"[{fmt_pct(family['post_taxonomy_ci_low'])}, "
        f"{fmt_pct(family['post_taxonomy_ci_high'])}]。",
        "- 但 `europe_cloud_break` 是在看完本轮 5 个 family 结果后被挑为赢家；"
        "上面的 max-of-five randomization 已校正横截面择优，尚未替代真正未来期。",
        "",
        "## 更像什么模式",
        "",
        "### 1. 广义欧洲 / 摄氏市场结构效应",
        "",
        f"- Europe family ROI {fmt_pct(family['fee_adjusted_roi'])}，"
        f"EU non-family {fmt_pct(eu_other['fee_adjusted_roi'])}，"
        f"C-unit non-family {fmt_pct(c_other['fee_adjusted_roi'])}。",
        f"- family 平均 NO ask {family['mean_no_ask']:.3f}，"
        f"EU non-family {eu_other['mean_no_ask']:.3f}；较便宜的 carry 是一部分来源。",
        f"- family 相对 mechanical-half 仍多 "
        f"{fmt_pct(family['roi_delta_vs_mechanical'])}，CI "
        f"[{fmt_pct(family['mechanical_delta_ci_low'])}, "
        f"{fmt_pct(family['mechanical_delta_ci_high'])}]，说明不只是两边都买的"
        "基础 carry。",
        "",
        "### 2. 模型在真正需要选边时，family 内表现不同",
        "",
        f"- 只有一个 distance-2 NO 会赢的 discriminating baskets：family "
        f"{int(family_disc['discriminating_baskets'])} 个，模型选对 "
        f"{int(family_disc['model_correct'])}/"
        f"{int(family_disc['discriminating_baskets'])}="
        f"{family_disc['model_accuracy']:.1%}，market 选对 "
        f"{int(family_disc['market_correct'])}/"
        f"{int(family_disc['discriminating_baskets'])}="
        f"{family_disc['market_accuracy']:.1%}。",
        f"- EU non-family 仅 {int(eu_other_disc['discriminating_baskets'])} 个，"
        f"模型 {eu_other_disc['model_accuracy']:.1%}、market "
        f"{eu_other_disc['market_accuracy']:.1%}。样本很小，说明 family 与其他"
        "欧洲城市的收益生成方式并不相同，但不足以确认气象机制。",
        "",
        "### 3. 不能叫 cloud-break alpha 的原因",
        "",
        f"- 五城训练期平均多模型 MAE {family['train_mean_mae_f']:.2f}F，"
        f"EU non-family {eu_other['train_mean_mae_f']:.2f}F：不是因为这五城"
        "forecast 普遍更准。",
        f"- family Brier delta vs market "
        f"{family['brier_delta_vs_market']:+.5f}，CI "
        f"[{family['brier_delta_ci_low']:+.5f}, "
        f"{family['brier_delta_ci_high']:+.5f}]；logloss delta "
        f"{family['logloss_delta_vs_market']:+.5f}，CI "
        f"[{family['logloss_delta_ci_low']:+.5f}, "
        f"{family['logloss_delta_ci_high']:+.5f}]。proper-score 均未确认打赢 market。",
        "- 当前 D1 signal 没有使用 cloud cover、cloud-break timing 或 remaining "
        "heating；family 名称只是旧 taxonomy 标签。因此收益不能反推为"
        "“云层破口”机制成立。",
        "",
        "## 研究口径",
        "",
        "- 固定分母：沿用 v2 的 1,190 paired baskets、每 basket 两个 "
        "distance-2 executable NO；本报告没有按结果删机会。",
        "- baseline：同 basket 的 market-only selection 与 "
        "mechanical-half distance2；fee 已计入。",
        "- 不确定性：按 target_date block bootstrap；组合枚举仅用于城市选择"
        "偶然性诊断，不能代替时间序列 forward。",
        "- evidence funnel：这是 immutable snapshot research replay，非 fill；"
        "未检验 queue、capacity、真实成交与滑点。",
        "",
        "## 冻结验证",
        "",
        "- 冻结两个互斥 hypothesis：`Europe-all` 与固定五城 "
        "`Amsterdam/Helsinki/Madrid/Munich/Warsaw`。",
        "- 继续固定 all-5 consensus、distance=2、前一天 snapshot 和同分母"
        " market/mechanical baselines；不得追加城市或阈值。",
        "- 至少积累 15 个新 target dates，再看：fee-adjusted ROI、相对 market/"
        "mechanical delta、Brier/logloss delta，以及 discriminating baskets "
        "选边准确率。proper score 不过，仍不升 live。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    confidence = pd.read_csv(args.input_dir / "confidence_rows.csv")
    candidates = attach_equal_all5_scores(
        pd.read_csv(args.input_dir / "candidate_source_probs.csv")
    )
    selected = pd.read_csv(args.input_dir / "selected_policy_rows.csv")
    if set(confidence.loc[confidence["city_family"].eq(FAMILY), "city"]) != FAMILY_CITIES:
        raise RuntimeError("europe_cloud_break membership drift")
    selected_all5 = confidence.copy()
    selected_market = selected[
        selected["policy"].eq("market_only_selection")
    ].copy()
    selected_mechanical = selected[
        selected["policy"].eq("mechanical_half_distance2")
    ].copy()
    if not (
        len(selected_all5)
        == len(selected_market)
        == len(selected_mechanical)
        == candidates["snapshot_key"].nunique()
    ):
        raise RuntimeError("fixed denominator mismatch")
    groups = summarize_groups(
        selected_all5,
        selected_market,
        selected_mechanical,
        candidates,
        draws=args.draws,
    )
    contrasts = summarize_contrasts(selected_all5, draws=args.draws)
    combination_null, combination_context = city_combination_null(
        selected_all5
    )
    partition_null, maxima = randomized_family_scan(
        selected_all5, draws=args.partition_draws
    )
    disc_rows = discrimination_rows(
        selected_all5, selected_market, candidates
    )
    disc_summary = summarize_discrimination(
        disc_rows, selected_all5, draws=args.draws
    )

    groups.to_csv(args.output_dir / "group_summary.csv", index=False)
    contrasts.to_csv(args.output_dir / "group_contrasts.csv", index=False)
    combination_null.to_csv(
        args.output_dir / "city_combination_randomization.csv", index=False
    )
    partition_null.to_csv(
        args.output_dir / "family_scan_randomization.csv", index=False
    )
    disc_rows.to_csv(
        args.output_dir / "discriminating_baskets.csv", index=False
    )
    disc_summary.to_csv(
        args.output_dir / "discrimination_summary.csv", index=False
    )
    plot_randomization(
        maxima,
        combination_context["family_roi"],
        args.output_dir / "family_scan_randomization.png",
    )
    payload = {
        "taxonomy_created_at": TAXONOMY_CREATED_AT,
        "strict_post_taxonomy_date": STRICT_POST_TAXONOMY_DATE,
        "fixed_denominator_baskets": len(selected_all5),
        "fixed_denominator_dates": int(
            selected_all5["target_date"].nunique()
        ),
        "fixed_denominator_cities": int(selected_all5["city"].nunique()),
        "groups": groups.to_dict("records"),
        "contrasts": contrasts.to_dict("records"),
        "combination_randomization": combination_null.to_dict("records"),
        "family_scan_randomization": partition_null.to_dict("records"),
        "discrimination": disc_summary.to_dict("records"),
        "verdict": {
            "status": "candidate_pattern_not_cloud_break_confirmed",
            "live_ready": False,
            "next_action": "freeze Europe-all and fixed-five forward shadows",
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.report,
        groups,
        contrasts,
        combination_null,
        partition_null,
        disc_summary,
    )


if __name__ == "__main__":
    main()
