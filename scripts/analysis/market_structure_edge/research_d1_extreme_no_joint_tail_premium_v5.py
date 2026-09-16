#!/usr/bin/env python3
"""Test joint-tail insurance premium for the D-1 two-extreme-NO basket.

This deliberately excludes weather/forecast features.  It asks whether the
same-snapshot market probability of either outer bracket winning has a stable
favorite/long-shot calibration residual.  A one-feature Platt model is fit on
strictly earlier target dates and evaluated expanding-OOF.  The second output
measures whether basket losses cluster by target date, which matters for the
"steady carry" interpretation even when marginal ROI is near zero.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression

import research_d1_extreme_no_basket_v1 as base
import research_d1_extreme_no_tail_probability_v3 as probability


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_snapshot_history_v4/executable_baskets.csv"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_joint_tail_premium_v5"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-27-d1-extreme-no-joint-tail-premium-v5.md"
)
MIN_TRAIN_DATES = 10
MODEL_C = 0.1
SEED = 2026072750
BUFFERS = {
    "platt_ev_buffer_0bp": 0.0,
    "platt_ev_buffer_50bp": 0.005,
    "platt_ev_buffer_100bp": 0.010,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=5000)
    return parser.parse_args()


def load_baskets(path: Path) -> pd.DataFrame:
    rows = pd.read_csv(path)
    required = {
        "policy",
        "city",
        "target_date",
        "market_tail_probability",
        "break_even_tail_probability",
        "tail_hit",
        "cost",
        "pnl",
    }
    missing = sorted(required - set(rows.columns))
    if missing:
        raise RuntimeError(f"missing required columns: {missing}")
    rows["target_date"] = rows["target_date"].astype(str)
    for column in (
        "market_tail_probability",
        "break_even_tail_probability",
        "tail_hit",
        "cost",
        "pnl",
    ):
        rows[column] = pd.to_numeric(rows[column], errors="raise")
    rows["market_p_tail"] = rows["market_tail_probability"].clip(
        1e-6, 1 - 1e-6
    )
    rows["market_logit"] = logit(rows["market_p_tail"].to_numpy(float))
    return rows


def expanding_platt(
    rows: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    scored_parts: list[pd.DataFrame] = []
    audit: list[dict[str, Any]] = []
    for policy, policy_rows in rows.groupby("policy"):
        for target_date in sorted(policy_rows["target_date"].unique()):
            train = policy_rows[policy_rows["target_date"].lt(target_date)]
            test = policy_rows[
                policy_rows["target_date"].eq(target_date)
            ].copy()
            train_dates = int(train["target_date"].nunique())
            positives = int(train["tail_hit"].sum())
            negatives = int(len(train) - positives)
            record: dict[str, Any] = {
                "policy": policy,
                "target_date": target_date,
                "train_rows": int(len(train)),
                "train_dates": train_dates,
                "train_tail_hits": positives,
                "test_rows": int(len(test)),
                "intercept": math.nan,
                "market_logit_slope": math.nan,
                "scored": 0,
                "skip_reason": None,
            }
            if train_dates < MIN_TRAIN_DATES:
                record["skip_reason"] = "insufficient_prior_dates"
                audit.append(record)
                continue
            if positives == 0 or negatives == 0:
                record["skip_reason"] = "single_class_prior"
                audit.append(record)
                continue
            model = LogisticRegression(
                C=MODEL_C,
                solver="lbfgs",
                max_iter=1000,
                random_state=SEED,
            )
            model.fit(
                train[["market_logit"]],
                train["tail_hit"].astype(int),
            )
            test["platt_p_tail"] = model.predict_proba(
                test[["market_logit"]]
            )[:, 1]
            test["train_rows"] = len(train)
            test["train_dates"] = train_dates
            test["train_tail_hits"] = positives
            test["platt_intercept"] = float(model.intercept_[0])
            test["platt_market_logit_slope"] = float(model.coef_[0, 0])
            scored_parts.append(test)
            record.update(
                {
                    "intercept": float(model.intercept_[0]),
                    "market_logit_slope": float(model.coef_[0, 0]),
                    "scored": int(len(test)),
                }
            )
            audit.append(record)
    if not scored_parts:
        return pd.DataFrame(), pd.DataFrame(audit)
    scored = pd.concat(scored_parts, ignore_index=True)
    scored["platt_p_tail"] = scored["platt_p_tail"].clip(
        1e-6, 1 - 1e-6
    )
    label = scored["tail_hit"].astype(float)
    for prefix, column in (
        ("market", "market_p_tail"),
        ("platt", "platt_p_tail"),
    ):
        prediction = scored[column]
        scored[f"{prefix}_logloss"] = -(
            label * np.log(prediction)
            + (1.0 - label) * np.log(1.0 - prediction)
        )
        scored[f"{prefix}_brier"] = np.square(prediction - label)
    scored["logloss_delta"] = (
        scored["platt_logloss"] - scored["market_logloss"]
    )
    scored["brier_delta"] = (
        scored["platt_brier"] - scored["market_brier"]
    )
    return scored, pd.DataFrame(audit)


def probability_summary(
    rows: pd.DataFrame, policy: str, *, draws: int, seed: int
) -> dict[str, Any]:
    group = rows[rows["policy"].eq(policy)]
    ll_ci = probability.date_mean_ci(
        group, "logloss_delta", draws=draws, seed=seed
    )
    br_ci = probability.date_mean_ci(
        group, "brier_delta", draws=draws, seed=seed + 1
    )
    daily = group.groupby("target_date")
    return {
        "policy": policy,
        "rows": int(len(group)),
        "dates": int(group["target_date"].nunique()),
        "tail_hits": int(group["tail_hit"].sum()),
        "observed_tail_rate": float(group["tail_hit"].mean()),
        "market_mean_p_tail": float(group["market_p_tail"].mean()),
        "platt_mean_p_tail": float(group["platt_p_tail"].mean()),
        "market_logloss": float(daily["market_logloss"].mean().mean()),
        "platt_logloss": float(daily["platt_logloss"].mean().mean()),
        "logloss_delta": float(daily["logloss_delta"].mean().mean()),
        "logloss_delta_ci_low": ll_ci[0],
        "logloss_delta_ci_high": ll_ci[1],
        "market_brier": float(daily["market_brier"].mean().mean()),
        "platt_brier": float(daily["platt_brier"].mean().mean()),
        "brier_delta": float(daily["brier_delta"].mean().mean()),
        "brier_delta_ci_low": br_ci[0],
        "brier_delta_ci_high": br_ci[1],
        "latest_intercept": float(
            group.sort_values("target_date")["platt_intercept"].iloc[-1]
        ),
        "latest_market_logit_slope": float(
            group.sort_values("target_date")[
                "platt_market_logit_slope"
            ].iloc[-1]
        ),
    }


def trade_summaries(
    scored: pd.DataFrame, *, draws: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries: list[dict[str, Any]] = []
    selections: list[pd.DataFrame] = []
    for policy_index, (policy, policy_rows) in enumerate(
        scored.groupby("policy")
    ):
        for buffer_index, (selector, buffer) in enumerate(BUFFERS.items()):
            chosen = policy_rows[
                policy_rows["platt_p_tail"].lt(
                    policy_rows["break_even_tail_probability"] - buffer
                )
            ].copy()
            chosen["selector"] = selector
            chosen["probability_buffer"] = buffer
            record = base.summarize(
                chosen,
                f"{policy}|{selector}",
                draws=draws,
                seed=SEED + 100 + policy_index * 20 + buffer_index * 2,
            )
            record.update(
                {
                    "policy": policy,
                    "selector": selector,
                    "probability_buffer": buffer,
                }
            )
            summaries.append(record)
            selections.append(chosen)
    return (
        pd.DataFrame(summaries),
        pd.concat(selections, ignore_index=True),
    )


def clustering_ratio(daily: pd.DataFrame, probability_column: str) -> float:
    residual = daily["tail_hits"] - daily[f"{probability_column}_sum"]
    expected_variance = daily[f"{probability_column}_variance"].mean()
    if len(daily) < 2 or expected_variance <= 0:
        return math.nan
    return float(np.var(residual, ddof=1) / expected_variance)


def clustering_ci(
    daily: pd.DataFrame,
    probability_column: str,
    *,
    draws: int,
    seed: int,
) -> tuple[float, float]:
    if len(daily) < 3:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(draws):
        indices = rng.integers(0, len(daily), len(daily))
        values.append(
            clustering_ratio(
                daily.iloc[indices].reset_index(drop=True),
                probability_column,
            )
        )
    finite = np.asarray(values)[np.isfinite(values)]
    if len(finite) == 0:
        return (math.nan, math.nan)
    return tuple(
        float(value) for value in np.quantile(finite, [0.025, 0.975])
    )


def max_drawdown(daily_pnl: pd.Series) -> float:
    equity = daily_pnl.cumsum()
    drawdown = equity - np.maximum.accumulate(
        np.r_[0.0, equity.to_numpy(float)]
    )[1:]
    return float(drawdown.min()) if len(drawdown) else 0.0


def clustering_summary(
    rows: pd.DataFrame,
    *,
    policy: str,
    slice_name: str,
    draws: int,
    seed: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    group = rows[rows["policy"].eq(policy)].copy()
    if group.empty:
        return (
            {
                "policy": policy,
                "slice": slice_name,
                "baskets": 0,
                "dates": 0,
            },
            pd.DataFrame(),
        )
    group["market_variance"] = (
        group["market_p_tail"] * (1.0 - group["market_p_tail"])
    )
    group["platt_variance"] = (
        group["platt_p_tail"] * (1.0 - group["platt_p_tail"])
    )
    daily = (
        group.groupby("target_date")
        .agg(
            baskets=("tail_hit", "size"),
            tail_hits=("tail_hit", "sum"),
            pnl=("pnl", "sum"),
            cost=("cost", "sum"),
            market_sum=("market_p_tail", "sum"),
            platt_sum=("platt_p_tail", "sum"),
            market_variance=("market_variance", "sum"),
            platt_variance=("platt_variance", "sum"),
        )
        .reset_index()
        .sort_values("target_date")
    )
    negative = (-daily["pnl"].clip(upper=0)).sort_values(ascending=False)
    total_loss = float(negative.sum())
    market_ratio = clustering_ratio(daily, "market")
    platt_ratio = clustering_ratio(daily, "platt")
    market_ci = clustering_ci(
        daily, "market", draws=draws, seed=seed
    )
    platt_ci = clustering_ci(
        daily, "platt", draws=draws, seed=seed + 1
    )
    record = {
        "policy": policy,
        "slice": slice_name,
        "baskets": int(len(group)),
        "dates": int(len(daily)),
        "tail_hits": int(group["tail_hit"].sum()),
        "dates_with_2plus_tail_hits": int(daily["tail_hits"].ge(2).sum()),
        "max_tail_hits_one_date": int(daily["tail_hits"].max()),
        "loss_dates": int(daily["pnl"].lt(0).sum()),
        "worst_day_pnl": float(daily["pnl"].min()),
        "max_drawdown": max_drawdown(daily["pnl"]),
        "worst_3_dates_share_of_gross_losses": (
            float(negative.head(3).sum() / total_loss)
            if total_loss > 0
            else math.nan
        ),
        "market_overdispersion_ratio": market_ratio,
        "market_overdispersion_ci_low": market_ci[0],
        "market_overdispersion_ci_high": market_ci[1],
        "platt_overdispersion_ratio": platt_ratio,
        "platt_overdispersion_ci_low": platt_ci[0],
        "platt_overdispersion_ci_high": platt_ci[1],
    }
    daily["policy"] = policy
    daily["slice"] = slice_name
    return record, daily


def fmt_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):+.2%}"


def probability_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| policy | rows / dates | observed | raw market p | calibrated p | logloss Δ vs raw (95% CI) | Brier Δ vs raw (95% CI) | latest slope |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in frame.iterrows():
        lines.append(
            f"| {row['policy']} | {int(row['rows'])} / {int(row['dates'])} | "
            f"{fmt_pct(row['observed_tail_rate'])} | "
            f"{fmt_pct(row['market_mean_p_tail'])} | "
            f"{fmt_pct(row['platt_mean_p_tail'])} | "
            f"{row['logloss_delta']:+.5f} "
            f"[{row['logloss_delta_ci_low']:+.5f}, "
            f"{row['logloss_delta_ci_high']:+.5f}] | "
            f"{row['brier_delta']:+.5f} "
            f"[{row['brier_delta_ci_low']:+.5f}, "
            f"{row['brier_delta_ci_high']:+.5f}] | "
            f"{row['latest_market_logit_slope']:.3f} |"
        )
    return lines


def trade_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| policy | selector | baskets / dates | tail hit | break-even | ROI (95% CI) | excess vs market (95% CI) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in frame.iterrows():
        lines.append(
            f"| {row['policy']} | {row['selector']} | "
            f"{int(row['baskets'])} / {int(row['dates'])} | "
            f"{fmt_pct(row.get('tail_hit_rate'))} | "
            f"{fmt_pct(row.get('break_even_tail_rate'))} | "
            f"{fmt_pct(row.get('fee_adjusted_roi'))} "
            f"[{fmt_pct(row.get('roi_ci_low'))}, "
            f"{fmt_pct(row.get('roi_ci_high'))}] | "
            f"{fmt_pct(row.get('excess_roi_vs_market'))} "
            f"[{fmt_pct(row.get('excess_ci_low'))}, "
            f"{fmt_pct(row.get('excess_ci_high'))}] |"
        )
    return lines


def clustering_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| policy / slice | baskets / dates | dates ≥2 hits | max hits | worst day | max DD | worst 3 loss share | calibrated overdispersion (95% CI) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in frame.iterrows():
        lines.append(
            f"| {row['policy']} / {row['slice']} | "
            f"{int(row['baskets'])} / {int(row['dates'])} | "
            f"{int(row.get('dates_with_2plus_tail_hits', 0))} | "
            f"{int(row.get('max_tail_hits_one_date', 0))} | "
            f"${row.get('worst_day_pnl', math.nan):+.2f} | "
            f"${row.get('max_drawdown', math.nan):+.2f} | "
            f"{fmt_pct(row.get('worst_3_dates_share_of_gross_losses'))} | "
            f"{row.get('platt_overdispersion_ratio', math.nan):.2f} "
            f"[{row.get('platt_overdispersion_ci_low', math.nan):.2f}, "
            f"{row.get('platt_overdispersion_ci_high', math.nan):.2f}] |"
        )
    return lines


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    baskets = load_baskets(args.input)
    scored, audit = expanding_platt(baskets)
    if scored.empty:
        raise RuntimeError("no expanding-OOF rows scored")

    probability_rows = [
        probability_summary(
            scored,
            policy,
            draws=args.draws,
            seed=SEED + index * 10,
        )
        for index, policy in enumerate(sorted(scored["policy"].unique()))
    ]
    probability_frame = pd.DataFrame(probability_rows)
    trade_frame, selected = trade_summaries(scored, draws=args.draws)
    primary = selected[
        selected["selector"].eq("platt_ev_buffer_50bp")
    ].copy()

    clustering_records: list[dict[str, Any]] = []
    daily_parts: list[pd.DataFrame] = []
    for policy_index, policy in enumerate(sorted(scored["policy"].unique())):
        for slice_index, (slice_name, source) in enumerate(
            (("all_oof", scored), ("primary_50bp", primary))
        ):
            record, daily = clustering_summary(
                source,
                policy=policy,
                slice_name=slice_name,
                draws=args.draws,
                seed=SEED + 300 + policy_index * 20 + slice_index * 2,
            )
            clustering_records.append(record)
            if not daily.empty:
                daily_parts.append(daily)
    clustering_frame = pd.DataFrame(clustering_records)
    daily_frame = pd.concat(daily_parts, ignore_index=True)

    probability_pass = bool(
        (probability_frame["logloss_delta_ci_high"] < 0).all()
        and (probability_frame["brier_delta_ci_high"] < 0).all()
    )
    primary_summary = trade_frame[
        trade_frame["selector"].eq("platt_ev_buffer_50bp")
    ]
    trade_pass = bool(
        not primary_summary.empty
        and primary_summary["baskets"].gt(0).all()
        and primary_summary["roi_ci_low"].gt(0).all()
        and primary_summary["excess_ci_low"].gt(0).all()
    )
    verdict = (
        "shadow_candidate"
        if probability_pass and trade_pass
        else "inconclusive"
    )

    probability_frame.to_csv(
        args.output_dir / "probability_summary.csv", index=False
    )
    trade_frame.to_csv(args.output_dir / "trade_summary.csv", index=False)
    clustering_frame.to_csv(
        args.output_dir / "clustering_summary.csv", index=False
    )
    daily_frame.to_csv(
        args.output_dir / "daily_clustering_detail.csv", index=False
    )
    audit.to_csv(args.output_dir / "training_audit.csv", index=False)
    scored.to_csv(args.output_dir / "oof_scored_baskets.csv", index=False)
    selected.to_csv(args.output_dir / "selected_baskets.csv", index=False)

    payload = {
        "contract": {
            "target": "joint probability that either outer exact bracket wins",
            "features": ["same_snapshot_market_tail_logit"],
            "excluded_features": "all weather and forecast features",
            "model": f"expanding prior-date Platt logistic C={MODEL_C}",
            "minimum_prior_dates": MIN_TRAIN_DATES,
            "primary_trade_rule": (
                "platt_p_tail < fee-adjusted basket break-even tail - 50bp"
            ),
            "fee": "official Weather feeRate 0.05 on both direct-NO-ask legs",
            "validation": "target-date expanding OOF + target-date block bootstrap",
            "trade_class": "research_replay",
        },
        "funnel": {
            "input_executable_baskets": int(len(baskets)),
            "input_dates": int(baskets["target_date"].nunique()),
            "oof_scored_baskets": int(len(scored)),
            "oof_dates": int(scored["target_date"].nunique()),
            "primary_50bp_baskets": int(len(primary)),
            "primary_50bp_dates": int(primary["target_date"].nunique()),
            "actual_fills": 0,
        },
        "probability_summary": probability_rows,
        "trade_summary": trade_frame.to_dict("records"),
        "clustering_summary": clustering_records,
        "gates": {
            "probability_vs_raw_market": (
                "PASS" if probability_pass else "FAIL"
            ),
            "trade_significance": "PASS" if trade_pass else "FAIL",
            "frozen_forward": "NA_post_hoc_expanding_oof",
            "conclusion": verdict,
        },
        "verdict": verdict,
        "action": (
            "zero_notional_shadow_only"
            if verdict == "shadow_candidate"
            else "do_not_shadow_or_live"
        ),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(
            probability.json_ready(payload),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    output_label = (
        args.output_dir.relative_to(ROOT)
        if args.output_dir.is_relative_to(ROOT)
        else args.output_dir
    )

    lines = [
        "# D-1 两端 NO：联合 Tail Insurance Premium v5",
        "",
        "> 2026-07-27；research replay；zero notional；不改 live。",
        "",
        "## 结论",
        "",
        (
            "本轮不再使用 source error、native-lattice distance、run "
            "stability、ensemble disagreement 或 station basis。唯一输入是同一"
            "决策时刻盘口对“最低档或最高档命中”的联合隐含概率，检验市场是否"
            "长期对极端保险定价过贵。"
        ),
        "",
        (
            "结果直接说：没有发现新增 alpha。market-only 校准未显著改善 raw "
            "market 的 logloss/Brier；固定 50bp 主规则在两个时段分别为 "
            "-3.85% ROI（CI -9.90%..+3.20%）和 -3.33% "
            "（CI -11.57%..+4.62%）。前三个最差 target dates 分别贡献"
            "主规则总亏损的 55.0% 和 58.5%，不符合“平滑稳健 carry”。"
        ),
        "",
        "### 概率层",
        "",
    ]
    lines.extend(probability_table(probability_frame))
    lines += [
        "",
        "负 delta 才表示 market-only 校准器在同 rows 上优于 raw market。",
        "",
        "### 交易层",
        "",
    ]
    lines.extend(trade_table(trade_frame))
    lines += [
        "",
        "### “稳健赚钱”风险层：tail loss 是否同日聚集",
        "",
    ]
    lines.extend(clustering_table(clustering_frame))
    lines += [
        "",
        (
            "`overdispersion=1` 近似表示同日 tail-hit 波动与逐篮子独立概率相符；"
            "大于 1 表示亏损按天气日聚集。它是风险诊断，不是新的 eligibility "
            "filter。"
        ),
        "",
        "## 与旧研究的边界",
        "",
        (
            "- 2026-07-24 的 market-implied-tail-residual P0/P1 研究对象是"
            "单个 exact-bracket YES 的 lifecycle/price cell；本研究对象是"
            "最低档 NO + 最高档 NO 的联合两腿成本与联合 tail label。"
        ),
        (
            "- v3/v4 用 forecast geometry 预测 tail，本研究完全排除天气特征，"
            "只检验市场自身是否存在稳定 favorite/long-shot bias。"
        ),
        "",
        "## Signal / Evidence Funnel",
        "",
        "Signal funnel（basket）:",
        "",
        f"- fixed D-1 executable baskets：{len(baskets):,} / "
        f"{baskets['target_date'].nunique()} target dates。",
        f"- strictly-prior-date OOF scored：{len(scored):,} / "
        f"{scored['target_date'].nunique()} dates。",
        f"- fixed 50bp primary selector：{len(primary):,} / "
        f"{primary['target_date'].nunique()} dates。",
        "",
        "Evidence funnel（basket）:",
        "",
        "- direct NO ask + official fee + exact-bracket settlement：与 v4 同分母。",
        "- same-row raw market baseline：完整。",
        "- actual fill：0（research replay）。",
        "- frozen forward：NA；当前仍是 post-hoc expanding OOF。",
        "",
        "## 裁决",
        "",
        f"- probability gate：{'PASS' if probability_pass else 'FAIL'}。",
        f"- trade gate：{'PASS' if trade_pass else 'FAIL'}。",
        f"- conclusion：`{verdict}`；action：`{payload['action']}`。",
        "",
        "## 产物",
        "",
        f"- `{output_label}/probability_summary.csv`",
        f"- `{output_label}/trade_summary.csv`",
        f"- `{output_label}/clustering_summary.csv`",
        f"- `{output_label}/daily_clustering_detail.csv`",
        f"- `{output_label}/oof_scored_baskets.csv`",
        f"- `{output_label}/selected_baskets.csv`",
        f"- `{output_label}/training_audit.csv`",
        f"- `{output_label}/summary.json`",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(probability.json_ready(payload), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
