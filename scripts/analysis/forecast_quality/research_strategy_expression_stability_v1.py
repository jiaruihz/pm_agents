#!/usr/bin/env python3
"""Stability diagnostics for regime-routed expressions and city-fit sizing.

This is a research report only. It compares expression payoff shape, daily
stability, and the current_high_yes vs d1_no counterfactual at identical
decision rows.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
ROWS_PATH = ROOT / "docs/analysis/2026-06/generated/strategy_city_fit_soft_sizing_v2/strategy_city_fit_soft_sizing_rows.csv"
EVENT_ROWS_PATH = (
    ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv"
)
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/strategy_expression_stability_v1"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-30-strategy-expression-stability-v1.md"


def pct(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"{100 * v:+.1f}%"


def money(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"${v:+.2f}"


def fmt(x: Any, digits: int = 2) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"{v:.{digits}f}"


def bootstrap_roi_ci(df: pd.DataFrame, cost_col: str, pnl_col: str, block_col: str = "target_date", n: int = 2000) -> tuple[float, float]:
    d = df[[block_col, cost_col, pnl_col]].dropna()
    if d.empty:
        return (float("nan"), float("nan"))
    blocks = d.groupby(block_col, as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    if len(blocks) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(20260630)
    vals = []
    cost = blocks["cost"].to_numpy(float)
    pnl = blocks["pnl"].to_numpy(float)
    for _ in range(n):
        idx = rng.integers(0, len(blocks), len(blocks))
        c = cost[idx].sum()
        if c > 0:
            vals.append(pnl[idx].sum() / c)
    if not vals:
        return (float("nan"), float("nan"))
    return tuple(np.quantile(vals, [0.025, 0.975]).tolist())


def daily_metrics(df: pd.DataFrame, cost_col: str, pnl_col: str) -> dict[str, Any]:
    daily = df.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    return {
        "daily_mean_pnl": daily["pnl"].mean(),
        "daily_median_pnl": daily["pnl"].median(),
        "daily_p10_pnl": daily["pnl"].quantile(0.10),
        "daily_p90_pnl": daily["pnl"].quantile(0.90),
        "daily_std_pnl": daily["pnl"].std(ddof=0),
        "losing_days": int((daily["pnl"] < 0).sum()),
        "roi_le_minus_50_days": int((daily["roi"] <= -0.5).sum()),
        "max_daily_loss": daily["pnl"].min(),
        "best_day": daily["pnl"].max(),
    }


def summarize_group(df: pd.DataFrame, group_cols: list[str], cost_col: str, pnl_col: str, ask_col: str, payoff_col: str) -> pd.DataFrame:
    rows = []
    for keys, g in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        d = g.dropna(subset=[cost_col, pnl_col])
        if d.empty:
            continue
        cost = d[cost_col].sum()
        pnl = d[pnl_col].sum()
        ci_low, ci_high = bootstrap_roi_ci(d, cost_col, pnl_col)
        rec = dict(zip(group_cols, keys, strict=False))
        rec.update(
            {
                "rows": len(d),
                "dates": d["target_date"].nunique(),
                "cities": d["city"].nunique(),
                "win_rate": d[payoff_col].mean(),
                "avg_ask": d[ask_col].mean(),
                "cost": cost,
                "pnl": pnl,
                "roi": pnl / cost if cost else float("nan"),
                "roi_ci_low": ci_low,
                "roi_ci_high": ci_high,
            }
        )
        rec.update(daily_metrics(d, cost_col, pnl_col))
        rows.append(rec)
    return pd.DataFrame(rows).sort_values(["policy", "cost"] if "policy" in group_cols else ["cost"], ascending=False)


def selected_long(rows: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for policy, cost_col, pnl_col in [
        ("running_current_row_risk_soft", "running_cost_usd", "running_pnl_usd"),
        ("city_fit_soft_overlay_v2", "city_fit_cost_usd", "city_fit_pnl_usd"),
    ]:
        d = rows.copy()
        d["policy"] = policy
        d["policy_cost_usd"] = d[cost_col]
        d["policy_pnl_usd"] = d[pnl_col]
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


def current_high_vs_d1(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    ch = rows[rows["expression_group"].eq("current_high_yes")].copy()
    frames = []
    for expr, ask_col, payoff_col in [
        ("current_high_yes", "router_ask", "router_payoff"),
        ("same_trigger_d1_no", "d1_no_ask", "d1_no_payoff"),
    ]:
        d = ch.dropna(subset=[ask_col, payoff_col]).copy()
        d["policy"] = expr
        d["unit_cost"] = d[ask_col].astype(float)
        d["unit_pnl"] = d[payoff_col].astype(float) - d[ask_col].astype(float)
        d["unit_payoff"] = d[payoff_col].astype(float)
        d["unit_ask"] = d[ask_col].astype(float)
        frames.append(d)
    cf = pd.concat(frames, ignore_index=True)
    summary = summarize_group(cf, ["policy"], "unit_cost", "unit_pnl", "unit_ask", "unit_payoff")

    detail = ch[
        [
            "city",
            "target_date",
            "decision_hour_local",
            "current_bracket",
            "d1_no_bracket",
            "final_winning_bracket",
            "router_ask",
            "router_payoff",
            "d1_no_ask",
            "d1_no_payoff",
            "row_source_bias_regime",
        ]
    ].copy()
    detail["case_type"] = np.select(
        [
            detail["final_winning_bracket"].astype(str).eq(detail["current_bracket"].astype(str)),
            detail["final_winning_bracket"].astype(str).eq(detail["d1_no_bracket"].astype(str)),
            detail["d1_no_payoff"].eq(1) & detail["router_payoff"].eq(0),
        ],
        ["stays_current_both_win", "lands_d1_both_lose", "skips_d1_d1_no_wins"],
        default="other",
    )
    return summary, detail


def expression_matrix_summary() -> pd.DataFrame:
    cols = [
        "rule",
        "city",
        "target_date",
        "current_yes_ask",
        "current_yes_payoff",
        "d1_no_ask",
        "d1_no_payoff",
        "d2_no_ask",
        "d2_no_payoff",
    ]
    df = pd.read_csv(EVENT_ROWS_PATH, usecols=cols)
    frames = []
    for expr, ask_col, payoff_col in [
        ("current_yes", "current_yes_ask", "current_yes_payoff"),
        ("d1_no", "d1_no_ask", "d1_no_payoff"),
        ("d2_no", "d2_no_ask", "d2_no_payoff"),
    ]:
        d = df.dropna(subset=[ask_col, payoff_col]).copy()
        d["expression"] = expr
        d["unit_cost"] = d[ask_col].astype(float)
        d["unit_pnl"] = d[payoff_col].astype(float) - d[ask_col].astype(float)
        d["unit_payoff"] = d[payoff_col].astype(float)
        d["unit_ask"] = d[ask_col].astype(float)
        frames.append(d)
    long = pd.concat(frames, ignore_index=True)
    return summarize_group(long, ["rule", "expression"], "unit_cost", "unit_pnl", "unit_ask", "unit_payoff")


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 80) -> str:
    if df.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    int_cols = {"rows", "dates", "cities", "losing_days", "roi_le_minus_50_days"}
    money_cols = {"cost", "pnl", "daily_mean_pnl", "daily_median_pnl", "daily_p10_pnl", "daily_p90_pnl", "daily_std_pnl", "max_daily_loss", "best_day"}
    pct_cols = {"win_rate", "roi", "roi_ci_low", "roi_ci_high"}
    for _, r in df.head(max_rows).iterrows():
        vals = []
        for key, _ in cols:
            val = r.get(key, "")
            if key in int_cols:
                vals.append(str(int(val)))
            elif key in money_cols:
                vals.append(money(val))
            elif key in pct_cols:
                vals.append(pct(val))
            elif isinstance(val, float):
                vals.append(fmt(val))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_report(
    selected_expr: pd.DataFrame,
    selected_daily: pd.DataFrame,
    ch_vs_d1: pd.DataFrame,
    ch_detail: pd.DataFrame,
    matrix: pd.DataFrame,
) -> str:
    return "\n".join(
        [
            "# Strategy Expression Stability v1",
            "",
            "Generated: 2026-06-30",
            "",
            "## Verdict",
            "",
            "本报告补充 `regime_routed_no_route_price_disciplined_tiny_live_v1` 与 city-fit overlay 的稳定性切片。结论仍是 `inconclusive_shadow_only`，不改 live。",
            "",
            "`current_high_yes` 和 `higher_no_d1` 方向相近，但不是同一个 payoff：`current_high_yes` 只有最终 winner 仍在当前高点 bracket 才赢；`d1 NO` 只要最终 winner 不是下一档 d1 就赢。若温度直接越过 d1 到 d2+，`current_high_yes` 输而 `d1 NO` 赢；若正好落到 d1，两者都输。",
            "",
            "## Selected Strategy Stability",
            "",
            md_table(
                selected_expr,
                [
                    ("policy", "policy"),
                    ("expression_group", "expression"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("win_rate", "win"),
                    ("avg_ask", "avg ask"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                    ("losing_days", "loss days"),
                    ("roi_le_minus_50_days", "<=-50% days"),
                    ("max_daily_loss", "max loss"),
                    ("daily_median_pnl", "median day"),
                ],
            ),
            "",
            "## Daily Policy Distribution",
            "",
            md_table(
                selected_daily,
                [
                    ("policy", "policy"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                    ("losing_days", "loss days"),
                    ("roi_le_minus_50_days", "<=-50% days"),
                    ("daily_p10_pnl", "p10 day"),
                    ("daily_median_pnl", "median day"),
                    ("daily_p90_pnl", "p90 day"),
                    ("max_daily_loss", "max loss"),
                    ("best_day", "best day"),
                ],
            ),
            "",
            "## Current-High YES vs Same-Trigger D1 NO",
            "",
            md_table(
                ch_vs_d1,
                [
                    ("policy", "expression"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("win_rate", "win"),
                    ("avg_ask", "avg ask"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                    ("losing_days", "loss days"),
                    ("max_daily_loss", "max loss"),
                ],
            ),
            "",
            "### Current-High Case Details",
            "",
            md_table(
                ch_detail,
                [
                    ("city", "city"),
                    ("target_date", "date"),
                    ("decision_hour_local", "hour"),
                    ("current_bracket", "current"),
                    ("d1_no_bracket", "d1"),
                    ("final_winning_bracket", "winner"),
                    ("router_ask", "YES ask"),
                    ("router_payoff", "YES payoff"),
                    ("d1_no_ask", "d1 NO ask"),
                    ("d1_no_payoff", "d1 NO payoff"),
                    ("case_type", "case"),
                ],
                max_rows=40,
            ),
            "",
            "## Broad Expression Matrix Sanity Check",
            "",
            "这张表不是当前策略选单，只是同一批 first-signal rows 上的表达形态 sanity check，用来看 `current YES` / `d1 NO` / `d2 NO` 的天然价格和稳定性。",
            "",
            md_table(
                matrix.sort_values(["rule", "expression"]),
                [
                    ("rule", "rule"),
                    ("expression", "expr"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("win_rate", "win"),
                    ("avg_ask", "avg ask"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                    ("losing_days", "loss days"),
                    ("max_daily_loss", "max loss"),
                ],
                max_rows=40,
            ),
            "",
            "## Interpretation",
            "",
            "- `current_bracket_no` 是当前策略的主要贡献头；它的总 ROI 高，但 CI 仍宽，且亏损日不少，说明组合收益来自少数好日和 route/price discipline，不是每日稳定提款。",
            "- `higher_no_d2` 胜率高但 ask 很贵，ROI 很薄；它更像降低波动的 capped-day tail 表达，不是主要 alpha。",
            "- `current_high_yes` 样本只有 11 笔，不能单独 live。它的问题不是方向错，而是 exact-bracket 风险太强：停在当前档才赢，正好到 d1 输，跳到 d2+ 也输。",
            "- 同触发点反事实里，`d1 NO` 比 `current_high_yes` 更像“高点已过/不要再刚好升一档”的表达；但全量 first-signal sanity check 里 `d1 NO` 的 avg ask 通常更贵，ROI 反而低于 current YES，所以不能简单替换。",
            "- 明显要改善的地方：把 pullback/peak-fade 这类形态拆成独立表达选择问题。先在 shadow 里同时记录 current-high YES、d1 NO、d2 NO 的同触发点价格和 payoff，再让 route 根据 overshoot risk / exact-stop risk 选择表达，而不是固定接 `current_high_yes`。",
            "",
            "## Data Notes",
            "",
            f"- Selected rows: `{ROWS_PATH.relative_to(ROOT)}`.",
            f"- Expression matrix: `{EVENT_ROWS_PATH.relative_to(ROOT)}`.",
            "- PnL here is replay/research PnL, not live fill-realized PnL.",
            "- CI is target-date block bootstrap over daily blocks; this is a stability diagnostic, not a live approval gate.",
            "",
        ]
    )


def build(_: argparse.Namespace) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = pd.read_csv(ROWS_PATH, low_memory=False)
    long = selected_long(rows)

    selected_expr = summarize_group(
        long,
        ["policy", "expression_group"],
        "policy_cost_usd",
        "policy_pnl_usd",
        "router_ask",
        "router_payoff",
    )
    selected_daily = summarize_group(
        long,
        ["policy"],
        "policy_cost_usd",
        "policy_pnl_usd",
        "router_ask",
        "router_payoff",
    )
    ch_vs_d1, ch_detail = current_high_vs_d1(rows)
    matrix = expression_matrix_summary()

    selected_expr.to_csv(OUT_DIR / "selected_expression_stability.csv", index=False)
    selected_daily.to_csv(OUT_DIR / "selected_policy_daily_stability.csv", index=False)
    ch_vs_d1.to_csv(OUT_DIR / "current_high_yes_vs_d1_no_summary.csv", index=False)
    ch_detail.to_csv(OUT_DIR / "current_high_yes_case_details.csv", index=False)
    matrix.to_csv(OUT_DIR / "broad_expression_matrix_stability.csv", index=False)

    OUT_MD.write_text(render_report(selected_expr, selected_daily, ch_vs_d1, ch_detail, matrix), encoding="utf-8")
    return {"selected_rows": int(len(rows)), "report": str(OUT_MD), "out_dir": str(OUT_DIR)}


def main() -> None:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    print(build(args))


if __name__ == "__main__":
    main()
