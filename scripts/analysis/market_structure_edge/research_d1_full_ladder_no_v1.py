#!/usr/bin/env python3
"""Frozen-forward replay of model-selected D1 full-ladder NO rungs."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_brackets import parse_market_bracket


DEFAULT_LADDER = (
    ROOT
    / "docs/analysis/2026-07/generated/d1_full_ladder_no_v1/"
    "full_ladder_rows.csv"
)
DEFAULT_FORECASTS = (
    ROOT
    / "docs/analysis/2026-07/generated/d1_single_runs_backfill_v1/"
    "forecast_rows.csv"
)
DEFAULT_ERRORS = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "historical_forecast_enrichment_bias_v1/daily_error_rows.csv"
)
DEFAULT_OUTPUT = (
    ROOT / "docs/analysis/2026-07/generated/d1_full_ladder_no_v1"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/2026-07-28-d1-full-ladder-no-v1.md"
)
TRAIN_END = "2026-06-16"
MODEL_KEYS = (
    "ecmwf_ifs025",
    "ecmwf_aifs025_single",
    "gfs_global",
    "icon_seamless",
    "jma_seamless",
)
SEED = 2026072817


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ladder", type=Path, default=DEFAULT_LADDER)
    parser.add_argument("--forecasts", type=Path, default=DEFAULT_FORECASTS)
    parser.add_argument("--errors", type=Path, default=DEFAULT_ERRORS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=5000)
    return parser.parse_args()


def fee(price: float) -> float:
    return 0.05 * price * (1.0 - price)


def training_errors(path: Path) -> dict[tuple[str, str], np.ndarray]:
    rows = pd.read_csv(path)
    rows = rows[
        (rows["target_date"] <= TRAIN_END)
        & rows["model_key"].isin(MODEL_KEYS)
        & rows["error_f"].notna()
    ]
    result: dict[tuple[str, str], np.ndarray] = {}
    for (city, model), group in rows.groupby(["city", "model_key"]):
        values = group["error_f"].astype(float).to_numpy()
        if len(values) >= 20:
            result[(str(city), str(model))] = values
    return result


def simulated_outcomes(
    city: str,
    unit: str,
    forecasts: pd.DataFrame,
    errors: dict[tuple[str, str], np.ndarray],
) -> tuple[np.ndarray, int]:
    per_model: list[np.ndarray] = []
    for row in forecasts.itertuples(index=False):
        residuals = errors.get((city, str(row.model_key)))
        if residuals is None:
            continue
        values_f = float(row.forecast_max_f) + residuals
        values = (
            (values_f - 32.0) * 5.0 / 9.0
            if str(unit).upper() == "C"
            else values_f
        )
        per_model.append(np.round(values).astype(int))
    if len(per_model) < 3:
        return np.array([], dtype=int), len(per_model)
    # Each model contributes the same empirical-history weight.  Calibration
    # stays frozen at TRAIN_END and no holdout outcome enters the distribution.
    equal_length = min(map(len, per_model))
    return (
        np.concatenate([values[-equal_length:] for values in per_model]),
        len(per_model),
    )


def score_rungs(
    ladder: pd.DataFrame,
    forecasts: pd.DataFrame,
    errors: dict[tuple[str, str], np.ndarray],
) -> pd.DataFrame:
    forecast_groups = {
        key: group
        for key, group in forecasts.groupby(
            ["city", "target_date", "decision_time_utc"], sort=False
        )
    }
    records: list[dict[str, Any]] = []
    for key, group in ladder.groupby("snapshot_key", sort=False):
        first = group.iloc[0]
        forecast_group = forecast_groups.get(
            (
                str(first["city"]),
                str(first["target_date"]),
                str(first["decision_ts_utc"]),
            )
        )
        if forecast_group is None:
            continue
        outcomes, model_count = simulated_outcomes(
            str(first["city"]),
            str(first["market_unit"]),
            forecast_group,
            errors,
        )
        if outcomes.size == 0:
            continue
        for row in group.itertuples(index=False):
            if not bool(row.no_executable) or pd.isna(row.yes_win):
                continue
            bracket = parse_market_bracket(str(row.bracket), str(row.question))
            if bracket is None:
                continue
            p_yes = float(
                np.mean([bracket.contains(float(value)) for value in outcomes])
            )
            no_ask = float(row.no_best_ask)
            cost = no_ask + fee(no_ask)
            p_no = 1.0 - p_yes
            payout = 1.0 - float(row.yes_win)
            record = row._asdict()
            record.update(
                {
                    "eligible_model_count": model_count,
                    "simulation_count": len(outcomes),
                    "model_p_yes": p_yes,
                    "model_p_no": p_no,
                    "fee": fee(no_ask),
                    "cost": cost,
                    "model_edge": p_no - cost,
                    "payout": payout,
                    "pnl": payout - cost,
                }
            )
            records.append(record)
    return pd.DataFrame(records)


def select_expression(
    scored: pd.DataFrame, name: str, max_endpoint_distance: int | None
) -> pd.DataFrame:
    candidates = scored
    if max_endpoint_distance is not None:
        candidates = candidates[
            candidates["distance_from_nearest_endpoint"]
            <= max_endpoint_distance
        ]
    selected = (
        candidates.sort_values(
            ["snapshot_key", "model_edge", "cost"],
            ascending=[True, False, True],
        )
        .groupby("snapshot_key", as_index=False)
        .first()
    )
    selected["expression"] = name
    return selected


def select_distance_band(
    scored: pd.DataFrame,
    name: str,
    *,
    minimum_distance: int,
    maximum_distance: int,
) -> pd.DataFrame:
    candidates = scored[
        scored["distance_from_nearest_endpoint"].between(
            minimum_distance, maximum_distance
        )
    ]
    selected = (
        candidates.sort_values(
            ["snapshot_key", "model_edge", "cost"],
            ascending=[True, False, True],
        )
        .groupby("snapshot_key", as_index=False)
        .first()
    )
    selected["expression"] = name
    return selected


def mechanical_endpoints(scored: pd.DataFrame) -> pd.DataFrame:
    endpoints = scored[scored["distance_from_nearest_endpoint"] == 0]
    records: list[dict[str, Any]] = []
    for _, group in endpoints.groupby("snapshot_key"):
        if len(group) != 2:
            continue
        first = group.iloc[0].to_dict()
        first.update(
            {
                "expression": "mechanical_half_each_endpoint",
                "cost": float(group["cost"].mean()),
                "payout": float(group["payout"].mean()),
                "pnl": float(group["pnl"].mean()),
                "no_best_ask": float(group["no_best_ask"].mean()),
                "model_edge": float(group["model_edge"].mean()),
                "distance_from_nearest_endpoint": 0,
                "bracket": "half_each_endpoint",
            }
        )
        records.append(first)
    return pd.DataFrame(records)


def roi(frame: pd.DataFrame) -> float:
    return float(frame["pnl"].sum() / frame["cost"].sum())


def roi_ci(
    frame: pd.DataFrame, *, draws: int, seed: int
) -> tuple[float, float]:
    daily = frame.groupby("target_date")[["pnl", "cost"]].sum()
    dates = daily.index.to_numpy()
    rng = np.random.default_rng(seed)
    samples = np.empty(draws)
    for index in range(draws):
        sample = daily.loc[
            rng.choice(dates, size=len(dates), replace=True)
        ].sum()
        samples[index] = sample["pnl"] / sample["cost"]
    return tuple(
        float(value) for value in np.quantile(samples, [0.025, 0.975])
    )


def delta_ci(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> tuple[float, float]:
    left = candidate.groupby("target_date")[["pnl", "cost"]].sum()
    right = baseline.groupby("target_date")[["pnl", "cost"]].sum()
    joined = left.join(right, lsuffix="_candidate", rsuffix="_baseline")
    dates = joined.index.to_numpy()
    rng = np.random.default_rng(seed)
    samples = np.empty(draws)
    for index in range(draws):
        sample = joined.loc[
            rng.choice(dates, size=len(dates), replace=True)
        ].sum()
        samples[index] = (
            sample["pnl_candidate"] / sample["cost_candidate"]
            - sample["pnl_baseline"] / sample["cost_baseline"]
        )
    return tuple(
        float(value) for value in np.quantile(samples, [0.025, 0.975])
    )


def summarize(
    expressions: list[pd.DataFrame], *, draws: int
) -> pd.DataFrame:
    baseline = next(
        frame
        for frame in expressions
        if frame["expression"].iloc[0] == "endpoint_model_max_edge"
    )
    baseline_roi = roi(baseline)
    records: list[dict[str, Any]] = []
    for index, frame in enumerate(expressions):
        low, high = roi_ci(frame, draws=draws, seed=SEED + index)
        delta_low, delta_high = delta_ci(
            frame, baseline, draws=draws, seed=SEED + 100 + index
        )
        records.append(
            {
                "expression": frame["expression"].iloc[0],
                "baskets": len(frame),
                "target_dates": int(frame["target_date"].nunique()),
                "cities": int(frame["city"].nunique()),
                "no_wins": int(frame["payout"].gt(0).sum()),
                "win_rate": float(frame["payout"].gt(0).mean()),
                "mean_no_ask": float(frame["no_best_ask"].mean()),
                "median_no_ask": float(frame["no_best_ask"].median()),
                "cost": float(frame["cost"].sum()),
                "pnl": float(frame["pnl"].sum()),
                "fee_adjusted_roi": roi(frame),
                "roi_ci_low": low,
                "roi_ci_high": high,
                "roi_delta_vs_endpoint": roi(frame) - baseline_roi,
                "delta_ci_low": delta_low,
                "delta_ci_high": delta_high,
                "inner_rung_share": float(
                    frame["distance_from_nearest_endpoint"].gt(0).mean()
                ),
            }
        )
    return pd.DataFrame(records)


def write_report(
    path: Path,
    summary: pd.DataFrame,
    scored: pd.DataFrame,
    model_eligible_baskets: int,
    data_funnel: dict[str, int],
    expressions: list[pd.DataFrame],
) -> None:
    primary = next(
        frame
        for frame in expressions
        if frame["expression"].iloc[0] == "third_rung_model_max_edge"
    )
    early = primary[primary["target_date"] <= "2026-06-26"]
    late = primary[primary["target_date"] > "2026-06-26"]
    report_columns = [
        "expression",
        "baskets",
        "target_dates",
        "mean_no_ask",
        "pnl",
        "fee_adjusted_roi",
        "roi_ci_low",
        "roi_ci_high",
        "roi_delta_vs_endpoint",
        "inner_rung_share",
    ]
    table_lines = [
        "| " + " | ".join(report_columns) + " |",
        "|" + "|".join(["---"] * len(report_columns)) + "|",
    ]
    for row in summary[report_columns].to_dict("records"):
        table_lines.append(
            "| "
            + " | ".join(
                str(value)
                if isinstance(value, (str, int))
                else f"{float(value):.6f}"
                for value in row.values()
            )
            + " |"
        )
    lines = [
        "# D1 多模型全档位 NO：冻结 forward v1",
        "",
        "## 结论",
        "",
        "先验主规格是最外两档（两侧共四档）按冻结多模型分布的 "
        "`P(NO) - ask - fee` 选择一档；同时按用户要求做固定档距探索。"
        "最有价值的探索规格是距两端第 3 档（distance=2），仍只按事前 edge "
        "在左右两档中选择，不使用该 city-day 的结算结果择档。",
        "",
        *table_lines,
        "",
        "## 冻结口径",
        "",
        f"- calibration 截止：`{TRAIN_END}`；残差只来自此前日期。",
        "- forecast run：`floor_6h(decision_ts - 12h)`，是保守可用性重建。",
        f"- evidence：{model_eligible_baskets} model-eligible baskets；"
        f"{scored['snapshot_key'].nunique()} 个具备 distance=2 表达的固定比较分母 / "
        f"{scored['target_date'].nunique()} target dates / "
        f"{scored['city'].nunique()} cities。",
        "- 模型：ECMWF IFS、ECMWF AIFS、GFS Global、ICON、JMA；每篮至少 3 个。",
        "- 成本：NO ask + `0.05 * price * (1-price)` 官方 fee。",
        "",
        "## Signal / evidence funnel",
        "",
        f"- forecast archive：{data_funnel['forecast_jobs']} city-day versions / "
        f"{data_funnel['forecast_dates']} dates / {data_funnel['forecast_rows']} "
        "model rows（5 模型全覆盖）。",
        f"- full-ladder raw：{data_funnel['ladder_baskets']} baskets / "
        f"{data_funnel['ladder_dates']} dates / {data_funnel['ladder_rungs']} rungs。",
        f"- executable + settled：{data_funnel['executable_rungs']} / "
        f"{data_funnel['settled_rungs']} rungs。",
        f"- calibration eligible：{model_eligible_baskets} baskets / 43 cities；"
        "HongKong、Moscow、Seoul、Shenzhen 的冻结训练残差不足 20 条。",
        f"- fixed expression denominator：{scored['snapshot_key'].nunique()} "
        "baskets（另 2 个 ladder 没有 distance=2 档）。",
        "",
        "## 时间稳定性",
        "",
        f"- distance=2 early（<= 2026-06-26）：{len(early)} baskets，ROI {roi(early):.4%}。",
        f"- distance=2 late（> 2026-06-26）：{len(late)} baskets，ROI {roi(late):.4%}。",
        "",
        "## 解释边界",
        "",
        "- 这是 research replay，不是 live 授权。",
        "- distance=2 是在本次 holdout 看过多个固定档距后发现的 exploratory "
        "结果，必须在新日期 frozen forward 复验，不能把当前 CI 直接当确认。",
        "- Single Runs 保存模型初始化；历史 public first-seen 不可恢复，"
        "因此使用 12 小时保守 lag，不把它写成精确 first-seen。",
        "- JRS 原始盘当前 Python 权限失效，本次全档位证据使用 7 月 6 日迁盘前"
        "本地不可变镜像；forecast backfill 本身覆盖完整冻结窗口。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ladder = pd.read_csv(args.ladder)
    forecasts = pd.read_csv(args.forecasts)
    errors = training_errors(args.errors)
    scored_all = score_rungs(ladder, forecasts, errors)
    if scored_all.empty:
        raise RuntimeError("no scored full-ladder rows")
    model_eligible_baskets = int(scored_all["snapshot_key"].nunique())
    common_keys = set(
        scored_all.loc[
            scored_all["distance_from_nearest_endpoint"] == 2,
            "snapshot_key",
        ]
    )
    scored = scored_all[scored_all["snapshot_key"].isin(common_keys)].copy()
    data_funnel = {
        "forecast_jobs": int(
            forecasts[
                ["city", "target_date", "decision_time_utc"]
            ].drop_duplicates().shape[0]
        ),
        "forecast_dates": int(forecasts["target_date"].nunique()),
        "forecast_rows": len(forecasts),
        "ladder_baskets": int(ladder["snapshot_key"].nunique()),
        "ladder_dates": int(ladder["target_date"].nunique()),
        "ladder_rungs": len(ladder),
        "executable_rungs": int(ladder["no_executable"].sum()),
        "settled_rungs": int(ladder["yes_win"].notna().sum()),
    }
    expressions = [
        select_expression(scored, "endpoint_model_max_edge", 0),
        select_expression(scored, "outer_two_model_max_edge", 1),
        select_distance_band(
            scored,
            "second_rung_model_max_edge",
            minimum_distance=1,
            maximum_distance=1,
        ),
        select_distance_band(
            scored,
            "third_rung_model_max_edge",
            minimum_distance=2,
            maximum_distance=2,
        ),
        select_distance_band(
            scored,
            "inner_second_or_third_max_edge",
            minimum_distance=1,
            maximum_distance=2,
        ),
        select_expression(scored, "full_ladder_model_max_edge", None),
        mechanical_endpoints(scored),
    ]
    combined = pd.concat(expressions, ignore_index=True)
    summary = summarize(expressions, draws=args.draws)
    scored_all.to_csv(args.output_dir / "scored_rungs.csv", index=False)
    combined.to_csv(args.output_dir / "selected_rows.csv", index=False)
    summary.to_csv(args.output_dir / "summary.csv", index=False)
    daily = (
        combined.groupby(["target_date", "expression"], as_index=False)[
            ["pnl", "cost"]
        ]
        .sum()
        .sort_values(["target_date", "expression"])
    )
    daily["daily_roi"] = daily["pnl"] / daily["cost"]
    daily.to_csv(args.output_dir / "daily.csv", index=False)
    curve = daily.pivot(
        index="target_date", columns="expression", values="pnl"
    ).fillna(0.0)
    curve.cumsum().plot(figsize=(10, 5))
    plt.axhline(0.0, color="black", linewidth=0.8)
    plt.ylabel("Cumulative PnL per one-share basket")
    plt.tight_layout()
    plt.savefig(args.output_dir / "cumulative_pnl.png", dpi=160)
    plt.close()
    payload = {
        "train_end": TRAIN_END,
        "model_eligible_baskets": model_eligible_baskets,
        "data_funnel": data_funnel,
        "evidence_baskets": int(scored["snapshot_key"].nunique()),
        "target_dates": int(scored["target_date"].nunique()),
        "cities": int(scored["city"].nunique()),
        "scored_rungs": len(scored),
        "expressions": summary.to_dict("records"),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.report,
        summary,
        scored,
        model_eligible_baskets,
        data_funnel,
        expressions,
    )
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
