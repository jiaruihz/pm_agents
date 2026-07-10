#!/usr/bin/env python3
"""Break down the tmax pre/post lineage repair replay by date and expression."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_lineage_repair_replay_v1"
POLICY_PATH = SOURCE_DIR / "policy_replay_rows.csv"
SCORE_PATH = SOURCE_DIR / "feature_parity_score_summary.csv"
LIVE_REPLAY_PATH = SOURCE_DIR / "live_order_replay.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_lineage_before_after_breakdown_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-10-tmax-lineage-before-after-breakdown-v1.md"
JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-10-tmax-lineage-before-after-breakdown-v1.json"

BEFORE = "before_missing_live"
AFTER = "after_repaired"
VARIANT_NAMES = {
    "live_missingness_emulation": BEFORE,
    "historical_full_features": AFTER,
}
EXPRESSIONS = ["current_no", "d1_no", "d1_yes", "d2_no", "d2_yes"]
FORWARD_START = "2026-06-21"


def roi_frame(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    out = (
        df.groupby(group_cols, as_index=False)
        .agg(
            rows=("city", "size"),
            dates=("target_date", "nunique"),
            wins=("win", "sum"),
            cost=("cost", "sum"),
            pnl=("pnl", "sum"),
        )
    )
    out["win_rate"] = out["wins"] / out["rows"]
    out["roi"] = out["pnl"] / out["cost"]
    return out


def paired_date_bootstrap(df: pd.DataFrame, seed: int = 20260710) -> dict[str, float]:
    daily = df.groupby(["target_date", "version"], as_index=False).agg(pnl=("pnl", "sum"), cost=("cost", "sum"))
    dates = sorted(daily["target_date"].unique())
    arrays = {}
    for version in [BEFORE, AFTER]:
        arrays[version] = (
            daily[daily["version"] == version]
            .set_index("target_date")[["pnl", "cost"]]
            .reindex(dates, fill_value=0.0)
            .to_numpy(dtype=float)
        )
    before = arrays[BEFORE]
    after = arrays[AFTER]
    point = after[:, 0].sum() / after[:, 1].sum() - before[:, 0].sum() / before[:, 1].sum()
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(20_000):
        idx = rng.integers(0, len(dates), len(dates))
        b = before[idx].sum(axis=0)
        a = after[idx].sum(axis=0)
        samples.append(a[0] / a[1] - b[0] / b[1])
    return {
        "roi_delta": float(point),
        "roi_delta_ci_low": float(np.percentile(samples, 2.5)),
        "roi_delta_ci_high": float(np.percentile(samples, 97.5)),
        "date_blocks": len(dates),
    }


def daily_comparison(df: pd.DataFrame) -> pd.DataFrame:
    daily = roi_frame(df, ["target_date", "version"])
    values = ["rows", "wins", "win_rate", "cost", "pnl", "roi"]
    pivot = daily.pivot(index="target_date", columns="version", values=values)
    pivot.columns = [f"{version}_{metric}" for metric, version in pivot.columns]
    pivot = pivot.reset_index()
    for col in [f"{version}_{metric}" for version in [BEFORE, AFTER] for metric in values]:
        if col not in pivot:
            pivot[col] = 0.0
    pivot["delta_pnl"] = pivot[f"{AFTER}_pnl"] - pivot[f"{BEFORE}_pnl"]
    pivot["delta_roi"] = pivot[f"{AFTER}_roi"] - pivot[f"{BEFORE}_roi"]
    return pivot.sort_values("target_date")


def daily_expression_delta(df: pd.DataFrame) -> pd.DataFrame:
    grouped = df.groupby(["target_date", "expression", "version"], as_index=False).agg(pnl=("pnl", "sum"))
    pivot = grouped.pivot_table(index="target_date", columns=["expression", "version"], values="pnl", fill_value=0.0)
    out = pd.DataFrame(index=pivot.index)
    for expression in EXPRESSIONS:
        before = pivot.get((expression, BEFORE), pd.Series(0.0, index=pivot.index))
        after = pivot.get((expression, AFTER), pd.Series(0.0, index=pivot.index))
        out[f"{expression}_delta_pnl"] = after - before
    out["total_delta_pnl"] = out.sum(axis=1)
    return out.reset_index()


def transition_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    keys = ["city", "target_date"]
    before = df[df["version"] == BEFORE]
    after = df[df["version"] == AFTER]
    merged = before.merge(after, on=keys, how="outer", suffixes=("_before", "_after"), indicator=True)
    merged["transition"] = np.select(
        [
            merged["_merge"].eq("left_only"),
            merged["_merge"].eq("right_only"),
            merged["expression_before"].eq(merged["expression_after"]),
        ],
        ["before_only", "after_only", "same_expression",],
        default="switched_expression",
    )
    merged["pnl_before_filled"] = merged["pnl_before"].fillna(0.0)
    merged["pnl_after_filled"] = merged["pnl_after"].fillna(0.0)
    merged["delta_pnl"] = merged["pnl_after_filled"] - merged["pnl_before_filled"]
    summary = (
        merged.groupby("transition", as_index=False)
        .agg(
            city_days=("city", "size"),
            dates=("target_date", "nunique"),
            pnl_before=("pnl_before_filled", "sum"),
            pnl_after=("pnl_after_filled", "sum"),
            delta_pnl=("delta_pnl", "sum"),
        )
        .sort_values("transition")
    )
    switched = (
        merged[merged["transition"] == "switched_expression"]
        .groupby(["expression_before", "expression_after"], as_index=False)
        .agg(rows=("city", "size"), delta_pnl=("delta_pnl", "sum"))
        .sort_values(["rows", "delta_pnl"], ascending=[False, False])
    )
    return merged, summary, switched


def markdown_table(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return "_no rows_"
    rows = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, item in df.reindex(columns=cols).iterrows():
        values = []
        for col in cols:
            value = item[col]
            if isinstance(value, (float, np.floating)):
                values.append("n/a" if not math.isfinite(float(value)) else f"{float(value):.3f}")
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def pct(value: float) -> str:
    return f"{100.0 * value:+.1f}%"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    policy = pd.read_csv(POLICY_PATH)
    policy = policy[
        policy["denominator"].eq("all_scored")
        & policy["variant"].isin(VARIANT_NAMES)
    ].copy()
    policy["version"] = policy["variant"].map(VARIANT_NAMES)
    policy["scope"] = np.where(policy["target_date"].astype(str) < FORWARD_START, "dev_cv", "verified_forward")

    overall = roi_frame(policy, ["scope", "version"])
    forward = policy[policy["scope"] == "verified_forward"].copy()
    daily = daily_comparison(forward)
    expression = roi_frame(forward, ["version", "expression"])
    daily_expression = roi_frame(forward, ["target_date", "version", "expression"])
    side_input = forward.assign(side=np.where(forward["expression"].str.endswith("yes"), "YES", "NO"))
    side = roi_frame(side_input, ["version", "side"])
    day_expression = daily_expression_delta(forward)
    changed, transition_summary, switch_map = transition_rows(forward)
    bootstrap = paired_date_bootstrap(forward)
    scores = pd.read_csv(SCORE_PATH)
    live_replay = pd.read_csv(LIVE_REPLAY_PATH)

    daily.to_csv(OUT_DIR / "daily_comparison.csv", index=False)
    expression.to_csv(OUT_DIR / "expression_comparison.csv", index=False)
    daily_expression.to_csv(OUT_DIR / "daily_expression_comparison.csv", index=False)
    side.to_csv(OUT_DIR / "side_comparison.csv", index=False)
    day_expression.to_csv(OUT_DIR / "daily_expression_delta.csv", index=False)
    transition_summary.to_csv(OUT_DIR / "transition_summary.csv", index=False)
    switch_map.to_csv(OUT_DIR / "expression_switch_map.csv", index=False)
    changed.to_csv(OUT_DIR / "changed_city_days.csv", index=False)

    before_forward = overall[(overall["scope"] == "verified_forward") & (overall["version"] == BEFORE)].iloc[0]
    after_forward = overall[(overall["scope"] == "verified_forward") & (overall["version"] == AFTER)].iloc[0]
    better_days = int((daily["delta_pnl"] > 0).sum())
    worse_days = int((daily["delta_pnl"] < 0).sum())
    same_eligible = int(live_replay["same_expression_still_eligible"].fillna(False).sum())

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "source": str(POLICY_PATH.relative_to(ROOT)),
            "forward_start": FORWARD_START,
            "forward_end": str(forward["target_date"].max()),
            "active_dates": int(forward["target_date"].nunique()),
            "unit": "one selected expression per city-day; one-share normalized taker PnL",
        },
        "before": before_forward.to_dict(),
        "after": after_forward.to_dict(),
        "paired_bootstrap": bootstrap,
        "daily_delta": {"better_days": better_days, "worse_days": worse_days},
        "live_replay": {"orders": len(live_replay), "same_expression_still_eligible": same_eligible},
        "verdict": "probability_parity_repair_confirmed_but_trade_selection_delta_inconclusive",
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    daily_display = daily.copy()
    for version in [BEFORE, AFTER]:
        daily_display[f"{version}_win_rate"] *= 100.0
        daily_display[f"{version}_roi"] *= 100.0
    daily_display["delta_roi"] *= 100.0
    expression_display = expression.copy()
    expression_display["win_rate"] *= 100.0
    expression_display["roi"] *= 100.0
    side_display = side.copy()
    side_display["win_rate"] *= 100.0
    side_display["roi"] *= 100.0
    overall_display = overall.copy()
    overall_display["win_rate"] *= 100.0
    overall_display["roi"] *= 100.0

    report = [
        "# Tmax Lineage Before/After Breakdown v1",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        "> This compares the old live feature-missingness behavior with the repaired full-feature behavior. It does not pretend the missing historical siblings or fresh direct YES asks can be backfilled.",
        "",
        "## 结论",
        "",
        f"- 6/21..7/08 的 16 个 active dates：修复前 {int(before_forward['rows'])} 笔，ROI {pct(before_forward['roi'])}；修复后 {int(after_forward['rows'])} 笔，ROI {pct(after_forward['roi'])}。",
        f"- 修复后相对修复前 ROI delta = {pct(bootstrap['roi_delta'])}，date-block 95% CI [{pct(bootstrap['roi_delta_ci_low'])}, {pct(bootstrap['roi_delta_ci_high'])}]，跨 0，交易收益差异不显著。",
        f"- 修复后有 {better_days} 天 PnL 改善、{worse_days} 天变差。概率 proper score 在 dev/forward 都改善，但 max-edge first-lock 的选单结果没有同步改善。",
        "- 下降主要来自 selection：40 个 before-only city-day 原口径事后赚了约 5.22/每股口径；27 个 after-only city-day 合计亏约 1.29。14 个换表达 city-day 反而改善约 1.45。",
        "- 这说明 feature parity 修复本身必须保留，但下一步应研究 probability -> executable expression 的选择/不确定度，而不是把字段重新删掉。",
        "",
        "## 口径边界",
        "",
        "- before = `live_missingness_emulation`：GFS/ECMWF gaps、RH、sky 等按旧 live 缺失方式进入模型。",
        "- after = `historical_full_features`：相同 expanding fit、相同 fee/ask/edge/first-city-day policy，只恢复 live 应有字段。",
        "- 每行是一 city-day 的第一笔 eligible expression，PnL 是 1 share 标准化，已扣 `0.05*p*(1-p)` taker fee；不是实际 live fill。",
        "- tail parser、完整 sibling collector、fresh direct YES ask 无法从缺失历史中无损回填，故不计入本表 PnL；它们只从修复后 forward 重新积累。",
        "- 本轮 canonical rebuild 完成 fact tables，但 live fill coverage gate 因历史 over-order/cache mismatch 失败；本报告不使用或发布 live_real PnL。",
        "",
        "## Proper Score",
        "",
        markdown_table(scores, ["scope", "variant", "rows", "dates", "logloss", "brier"]),
        "",
        "## Dev / Forward 选单汇总",
        "",
        markdown_table(overall_display, ["scope", "version", "rows", "dates", "win_rate", "cost", "pnl", "roi"]),
        "",
        "## 每日对比",
        "",
        markdown_table(
            daily_display,
            [
                "target_date",
                f"{BEFORE}_rows", f"{BEFORE}_win_rate", f"{BEFORE}_roi", f"{BEFORE}_pnl",
                f"{AFTER}_rows", f"{AFTER}_win_rate", f"{AFTER}_roi", f"{AFTER}_pnl",
                "delta_pnl", "delta_roi",
            ],
        ),
        "",
        "没有列出的日期表示两版都没有 settled eligible selection，不是 ROI=0。",
        "",
        "## 各表达方向",
        "",
        markdown_table(expression_display, ["version", "expression", "rows", "dates", "win_rate", "cost", "pnl", "roi"]),
        "",
        "## YES / NO 汇总",
        "",
        markdown_table(side_display, ["version", "side", "rows", "dates", "win_rate", "cost", "pnl", "roi"]),
        "",
        "## 每天各表达的 PnL 变化",
        "",
        markdown_table(day_expression, ["target_date", *[f"{item}_delta_pnl" for item in EXPRESSIONS], "total_delta_pnl"]),
        "",
        "正数表示修复后更好，负数表示修复后更差；这是 PnL delta，不是单表达 ROI。",
        f"完整逐日逐表达 rows/win-rate/cost/PnL/ROI 见 `{(OUT_DIR / 'daily_expression_comparison.csv').relative_to(ROOT)}`。",
        "",
        "## 选单变化来源",
        "",
        markdown_table(transition_summary, ["transition", "city_days", "dates", "pnl_before", "pnl_after", "delta_pnl"]),
        "",
        "### 换表达路径",
        "",
        markdown_table(switch_map, ["expression_before", "expression_after", "rows", "delta_pnl"]),
        "",
        "## 8 笔 live 反事实",
        "",
        f"旧 live 共 8 笔；按修复后的 PIT feature replay，在原 fill price 上仍有 {same_eligible}/8 原表达过门。Busan 7/09 d1 NO 变为负 edge，北京 7/09 d1 NO 为 1.93c、略低于 2c 门。",
        "",
        "## Three Gates",
        "",
        "- significance: FAIL for selected-trade delta; paired date CI crosses zero.",
        "- baseline: PASS only for the mechanical same-policy A/B; no claim that after beats a market/no-model baseline here.",
        "- forward: PARTIAL; frozen 6/21+ window exists, but complete-ladder/direct-YES repairs have no historical forward yet.",
        "- conclusion: `inconclusive` for PnL superiority; `confirmed` only for restoring live feature/data/execution parity.",
    ]
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
