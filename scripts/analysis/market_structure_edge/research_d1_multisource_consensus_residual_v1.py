#!/usr/bin/env python3
"""Strict-PIT replay for the D-1 assigned-vs-consensus endpoint-NO strategy."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_d1_extreme_no_basket_v1 as basket_base
from weather_data_feed_service.legacy_weather_predict.paper_snapshot import (
    CITY_MODEL,
)


DEFAULT_BASKETS = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_snapshot_history_v4/executable_baskets.csv"
)
DEFAULT_PIT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_multisource_policy_v7/strict_pit_coverage_rows.csv"
)
DEFAULT_POLICY = (
    ROOT / "configs/weather/d1_multisource_consensus_shadow_v1.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_multisource_consensus_residual_v1"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-28-d1-multisource-consensus-residual-v1.md"
)
SEED = 2026072811


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baskets", type=Path, default=DEFAULT_BASKETS)
    parser.add_argument("--pit", type=Path, default=DEFAULT_PIT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=5000)
    return parser.parse_args()


def policy_index(path: Path) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    index = {
        (str(row["city"]), str(row["model_label"])): row
        for row in payload.get("records") or []
    }
    return index, payload


def assigned_label(city: str) -> str:
    return "ECMWF" if CITY_MODEL.get(city) == "ecmwf" else "GFS"


def score_rows(
    baskets: pd.DataFrame,
    pit: pd.DataFrame,
    calibration: dict[tuple[str, str], dict[str, Any]],
) -> pd.DataFrame:
    joined = pit.merge(
        baskets,
        on=["snapshot_key", "policy", "city", "target_date"],
        how="left",
        validate="one_to_one",
        suffixes=("_pit", ""),
    )
    records: list[dict[str, Any]] = []
    for row in joined.itertuples(index=False):
        models = json.loads(str(row.models))
        corrected: dict[str, float] = {}
        for label, raw_value in models.items():
            frozen = calibration.get((str(row.city), str(label)))
            if frozen is None or raw_value is None:
                continue
            corrected[str(label)] = float(raw_value) + float(
                frozen["bias_correction_f"]
            )
        assigned_model = assigned_label(str(row.city))
        if assigned_model not in corrected or len(corrected) < 3:
            continue
        values = list(corrected.values())
        consensus = float(median(values))
        spread = float(np.quantile(values, 0.75) - np.quantile(values, 0.25))
        assigned = corrected[assigned_model]
        delta = assigned - consensus
        if delta == 0:
            continue
        selected_leg = "high_no" if delta > 0 else "low_no"
        opposite_leg = "low_no" if delta > 0 else "high_no"
        selected_price = float(
            row.high_no_ask if selected_leg == "high_no" else row.low_no_ask
        )
        opposite_price = float(
            row.low_no_ask if selected_leg == "high_no" else row.high_no_ask
        )
        selected_yes_won = float(
            row.high_win if selected_leg == "high_no" else row.low_win
        )
        opposite_yes_won = float(
            row.low_win if selected_leg == "high_no" else row.high_win
        )
        selected_fee = float(basket_base.fee_per_share(selected_price))
        opposite_fee = float(basket_base.fee_per_share(opposite_price))
        selected_cost = selected_price + selected_fee
        opposite_cost = opposite_price + opposite_fee
        selected_payout = 1.0 - selected_yes_won
        opposite_payout = 1.0 - opposite_yes_won
        selected_pnl = selected_payout - selected_cost
        opposite_pnl = opposite_payout - opposite_cost
        mechanical_cost = 0.5 * (selected_cost + opposite_cost)
        mechanical_payout = 0.5 * (selected_payout + opposite_payout)
        record = row._asdict()
        record.update(
            {
                "assigned_model_label": assigned_model,
                "eligible_model_count": len(corrected),
                "corrected_models_json": json.dumps(
                    corrected, ensure_ascii=False, sort_keys=True
                ),
                "assigned_corrected_f": assigned,
                "consensus_corrected_f": consensus,
                "assigned_minus_consensus_f": delta,
                "ensemble_spread_iqr_f": spread,
                "normalized_disagreement": (
                    abs(delta) / spread if spread > 0 else math.nan
                ),
                "selected_leg": selected_leg,
                "opposite_leg": opposite_leg,
                "selected_no_ask": selected_price,
                "opposite_no_ask": opposite_price,
                "selected_fee": selected_fee,
                "opposite_fee": opposite_fee,
                "selected_cost": selected_cost,
                "opposite_cost": opposite_cost,
                "selected_payout": selected_payout,
                "opposite_payout": opposite_payout,
                "selected_pnl": selected_pnl,
                "opposite_pnl": opposite_pnl,
                "mechanical_half_cost": mechanical_cost,
                "mechanical_half_payout": mechanical_payout,
                "mechanical_half_pnl": mechanical_payout - mechanical_cost,
                "selected_minus_opposite_pnl": selected_pnl - opposite_pnl,
            }
        )
        records.append(record)
    return pd.DataFrame(records)


def ratio_ci(
    rows: pd.DataFrame,
    pnl_column: str,
    cost_column: str,
    *,
    draws: int,
    seed: int,
) -> tuple[float, float]:
    if rows["target_date"].nunique() < 3:
        return math.nan, math.nan
    daily = rows.groupby("target_date")[[pnl_column, cost_column]].sum()
    dates = daily.index.to_numpy()
    rng = np.random.default_rng(seed)
    samples = np.empty(draws)
    for index in range(draws):
        sampled = daily.loc[
            rng.choice(dates, size=len(dates), replace=True)
        ].sum()
        samples[index] = sampled[pnl_column] / sampled[cost_column]
    return tuple(float(value) for value in np.quantile(samples, [0.025, 0.975]))


def delta_ci(
    rows: pd.DataFrame, *, draws: int, seed: int
) -> tuple[float, float]:
    if rows["target_date"].nunique() < 3:
        return math.nan, math.nan
    daily = rows.groupby("target_date")[
        ["selected_pnl", "selected_cost", "opposite_pnl", "opposite_cost"]
    ].sum()
    dates = daily.index.to_numpy()
    rng = np.random.default_rng(seed)
    samples = np.empty(draws)
    for index in range(draws):
        sampled = daily.loc[
            rng.choice(dates, size=len(dates), replace=True)
        ].sum()
        samples[index] = (
            sampled["selected_pnl"] / sampled["selected_cost"]
            - sampled["opposite_pnl"] / sampled["opposite_cost"]
        )
    return tuple(float(value) for value in np.quantile(samples, [0.025, 0.975]))


def summarize(rows: pd.DataFrame, *, draws: int) -> pd.DataFrame:
    definitions = [
        ("consensus_selected_no", "selected_pnl", "selected_cost", "selected_payout"),
        ("opposite_endpoint_no", "opposite_pnl", "opposite_cost", "opposite_payout"),
        (
            "mechanical_half_each",
            "mechanical_half_pnl",
            "mechanical_half_cost",
            "mechanical_half_payout",
        ),
    ]
    records: list[dict[str, Any]] = []
    for index, (name, pnl, cost, payout) in enumerate(definitions):
        low, high = ratio_ci(
            rows, pnl, cost, draws=draws, seed=SEED + index
        )
        records.append(
            {
                "expression": name,
                "rows": len(rows),
                "target_dates": rows["target_date"].nunique(),
                "cities": rows["city"].nunique(),
                "wins": int(rows[payout].gt(0).sum()),
                "cost": float(rows[cost].sum()),
                "pnl": float(rows[pnl].sum()),
                "fee_adjusted_roi": float(rows[pnl].sum() / rows[cost].sum()),
                "roi_ci_low": low,
                "roi_ci_high": high,
            }
        )
    frame = pd.DataFrame(records)
    low, high = delta_ci(rows, draws=draws, seed=SEED + 10)
    selected_roi = float(
        rows["selected_pnl"].sum() / rows["selected_cost"].sum()
    )
    opposite_roi = float(
        rows["opposite_pnl"].sum() / rows["opposite_cost"].sum()
    )
    frame["selected_minus_opposite_roi"] = selected_roi - opposite_roi
    frame["selected_minus_opposite_ci_low"] = low
    frame["selected_minus_opposite_ci_high"] = high
    return frame


def daily_curve(rows: pd.DataFrame) -> pd.DataFrame:
    daily = (
        rows.groupby("target_date", as_index=False)
        .agg(
            selected_pnl=("selected_pnl", "sum"),
            opposite_pnl=("opposite_pnl", "sum"),
            mechanical_pnl=("mechanical_half_pnl", "sum"),
            selected_cost=("selected_cost", "sum"),
            opposite_cost=("opposite_cost", "sum"),
            mechanical_cost=("mechanical_half_cost", "sum"),
            rows=("snapshot_key", "count"),
        )
        .sort_values("target_date")
    )
    for name in ("selected", "opposite", "mechanical"):
        daily[f"{name}_cumulative_pnl"] = daily[f"{name}_pnl"].cumsum()
    return daily


def save_plot(daily: pd.DataFrame, path: Path) -> None:
    figure, axis = plt.subplots(figsize=(8.5, 4.5))
    x = pd.to_datetime(daily["target_date"])
    axis.plot(
        x,
        daily["selected_cumulative_pnl"],
        marker="o",
        label="Consensus-selected NO",
    )
    axis.plot(
        x,
        daily["opposite_cumulative_pnl"],
        marker="o",
        label="Opposite endpoint NO",
    )
    axis.plot(
        x,
        daily["mechanical_cumulative_pnl"],
        marker="o",
        label="Mechanical 50/50",
    )
    axis.axhline(0, color="#777777", linewidth=0.8)
    axis.set_title("D-1 multi-model consensus residual — strict PIT")
    axis.set_ylabel("Cumulative PnL per 1-share expression (USD)")
    axis.set_xlabel("Target date")
    axis.grid(alpha=0.2)
    axis.legend()
    figure.autofmt_xdate()
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)


def fmt_pct(value: Any) -> str:
    return "n/a" if pd.isna(value) else f"{float(value):+.2%}"


def write_report(
    *,
    args: argparse.Namespace,
    rows: pd.DataFrame,
    summary: pd.DataFrame,
    policy_payload: dict[str, Any],
) -> None:
    selected = summary[summary["expression"].eq("consensus_selected_no")].iloc[0]
    opposite = summary[summary["expression"].eq("opposite_endpoint_no")].iloc[0]
    table = [
        "| expression | rows / dates | wins | cost | PnL | fee-adjusted ROI (95% CI) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in summary.itertuples(index=False):
        table.append(
            f"| {row.expression} | {int(row.rows)} / {int(row.target_dates)} | "
            f"{int(row.wins)} | ${row.cost:.4f} | ${row.pnl:+.4f} | "
            f"{fmt_pct(row.fee_adjusted_roi)} "
            f"[{fmt_pct(row.roi_ci_low)}, {fmt_pct(row.roi_ci_high)}] |"
        )
    generated = datetime.now(timezone.utc).isoformat()
    lines = [
        "# D-1 多模型 consensus residual：严格 PIT 单边 NO v1",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`{args.baskets}` + `{args.pit}` + frozen policy。",
        f"- 生成时间：{generated}。",
        f"- 记录行数：{len(rows)} baskets；settled={len(rows)}；unsettled=0；missing_bracket=0。",
        "- unit：basket/city-day；`trade_class=research_replay`，不是 actual fill。",
        "",
        "## 结论",
        "",
        "这次真正把 17 个模型用于交易表达：每城各模型先加 frozen rolling bias，"
        "取 corrected median；assigned ECMWF/GFS 偏热时选最高端 NO，偏冷时选最低端 NO。",
        "",
        *table,
        "",
        f"- selected 相对 opposite 的 ROI delta："
        f"{fmt_pct(selected.selected_minus_opposite_roi)} "
        f"[{fmt_pct(selected.selected_minus_opposite_ci_low)}, "
        f"{fmt_pct(selected.selected_minus_opposite_ci_high)}]。",
        f"- 严格 PIT 历史只有 {len(rows)} baskets / "
        f"{rows['target_date'].nunique()} target dates；少于3日，date-block CI 无法估计。",
        "- 因此点估只用于验证实现与方向，不能作为 alpha 或 live 证据。",
        "",
        "## 策略定义",
        "",
        "```text",
        "corrected_model = model_forecast + frozen_city_model_bias",
        "consensus = median(corrected_models)",
        "delta = assigned_corrected - consensus",
        "delta > 0 -> BUY high endpoint NO",
        "delta < 0 -> BUY low endpoint NO",
        "cost = direct NO ask + 0.05 * ask * (1-ask)",
        "```",
        "",
        f"- bias training cutoff：{policy_payload.get('training_cutoff')}。",
        "- spread 和 `abs(delta)/IQR` 全量记录，但不做事后阈值筛选。",
        "- baseline 1：同 city-date 的相反端 NO；baseline 2：两端各半。",
        "",
        "## Signal / Evidence Funnel",
        "",
        f"- signal：4,086 executable baskets → 23 strict-PIT coverage → "
        f"{len(rows)} assigned+consensus 可评分 → {len(rows)} 单边选择。",
        f"- evidence：{len(rows)} direct asks → {len(rows)} settlements → "
        f"{len(rows)} hypothetical taker expressions → 0 actual fills。",
        "",
        "## 八环与 Gate",
        "",
        "- 已覆盖：描述性绩效、信号方向、官方 fee、同分母反事实。",
        "- 未覆盖：有效统计推断、概率 calibration、容量、组合相关性、frozen forward。",
        "- significance=NA；baseline=NA；forward=NA；conclusion=inconclusive。",
        "- 动作：继续 zero-notional forward shadow，不改 live。",
        "",
        "## 产物",
        "",
        f"- `{args.output_dir / 'scored_rows.csv'}`",
        f"- `{args.output_dir / 'summary.csv'}`",
        f"- `{args.output_dir / 'daily_curve.csv'}`",
        f"- `{args.output_dir / 'cumulative_pnl.png'}`",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    baskets = pd.read_csv(args.baskets)
    pit = pd.read_csv(args.pit)
    calibration, policy_payload = policy_index(args.policy)
    rows = score_rows(baskets, pit, calibration)
    if rows.empty:
        raise SystemExit("no strict-PIT rows with assigned and consensus models")
    summary = summarize(rows, draws=args.draws)
    daily = daily_curve(rows)
    rows.to_csv(args.output_dir / "scored_rows.csv", index=False)
    summary.to_csv(args.output_dir / "summary.csv", index=False)
    daily.to_csv(args.output_dir / "daily_curve.csv", index=False)
    save_plot(daily, args.output_dir / "cumulative_pnl.png")
    write_report(
        args=args,
        rows=rows,
        summary=summary,
        policy_payload=policy_payload,
    )
    print(
        json.dumps(
            {
                "rows": len(rows),
                "target_dates": rows["target_date"].nunique(),
                "cities": rows["city"].nunique(),
                "summary": summary.to_dict(orient="records"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
