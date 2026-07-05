"""HeadB METAR reversal daily backtest breakdown v1.

This is a reporting script, not a strategy selector. It expands the HeadB
historical results by target date so we can see whether the edge is steady or
carried by a few bursty reversal days.

Inputs are the existing generated expression matrices:
- metar_reversal_expression_matrix_v1/expression_matrix_rows.csv
- hotter_tail_reversal_shapes_v1/leg_rows.csv

Main output uses an executable-ish taker stress:
- d1 YES false-fade: entry = historical ask + 1c
- B4/rich-current: entry = historical ask + 1c
- weather taker fee = shares * 0.05 * p * (1-p)

For $1 trade notional, shares=1/p, so fee_usd = 0.05 * (1-p).
Raw/no-fee columns are kept to reconcile older reports.

Usage:
  .venv/bin/python scripts/analysis/reheat_risk/research_metar_reversal_daily_breakdown_v1.py
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
EXPR_ROWS = ROOT / "docs/analysis/2026-07/generated/metar_reversal_expression_matrix_v1/expression_matrix_rows.csv"
LEG_ROWS = ROOT / "docs/analysis/2026-07/generated/hotter_tail_reversal_shapes_v1/leg_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/metar_reversal_daily_breakdown_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-05-metar-reversal-daily-breakdown-v1.md"

RNG_SEED = 20260705
N_BOOT = 5000
TAKER_STRESS = 0.01
WEATHER_TAKER_FEE_RATE = 0.05
RECENT_START = "2026-06-21"


@dataclass(frozen=True)
class StrategyFrame:
    name: str
    description: str
    rows: pd.DataFrame


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def money(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return ""
    return f"{float(x):+.2f}"


def pct(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return ""
    return f"{100.0 * float(x):+.1f}%"


def pct_plain(x: float | int | None) -> str:
    if x is None or pd.isna(x):
        return ""
    return f"{100.0 * float(x):.1f}%"


def num(x: float | int | None, digits: int = 3) -> str:
    if x is None or pd.isna(x):
        return ""
    return f"{float(x):.{digits}f}"


def block_bootstrap_ci(daily: pd.DataFrame, roi_col: str, cost_col: str = "rows") -> tuple[float | None, float | None]:
    if daily["target_date"].nunique() < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    pnl = (daily[roi_col].to_numpy(dtype=float) * daily[cost_col].to_numpy(dtype=float))
    cost = daily[cost_col].to_numpy(dtype=float)
    vals: list[float] = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(daily), len(daily))
        denom = cost[idx].sum()
        if denom > 0:
            vals.append(float(pnl[idx].sum() / denom))
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def fee_per_1_notional(entry_price: pd.Series) -> pd.Series:
    # Polymarket weather taker fee: shares * feeRate * p * (1-p).
    # With $1 trade notional, shares = 1/p.
    return WEATHER_TAKER_FEE_RATE * (1.0 - entry_price.astype(float))


def finish_trade_rows(rows: pd.DataFrame, name: str, description: str) -> StrategyFrame:
    rows = rows.copy()
    rows["strategy"] = name
    rows["description"] = description
    rows["target_date"] = rows["target_date"].astype(str)
    rows["entry_stress"] = (rows["entry_raw"].astype(float) + TAKER_STRESS).clip(0.01, 0.99)
    rows["fee_usd_per_1_notional"] = fee_per_1_notional(rows["entry_stress"])
    rows["raw_pnl_per_1"] = rows["win"].astype(float) / rows["entry_raw"].astype(float) - 1.0
    rows["exec_no_fee_pnl_per_1"] = rows["win"].astype(float) / rows["entry_stress"].astype(float) - 1.0
    rows["exec_fee_pnl_per_1"] = rows["exec_no_fee_pnl_per_1"] - rows["fee_usd_per_1_notional"]
    rows["cash_roi_exec_fee"] = rows["exec_fee_pnl_per_1"] / (1.0 + rows["fee_usd_per_1_notional"])
    keep = [
        "strategy", "description", "city", "target_date", "decision_hour_local",
        "entry_raw", "entry_stress", "fee_usd_per_1_notional", "win",
        "raw_pnl_per_1", "exec_no_fee_pnl_per_1", "exec_fee_pnl_per_1",
        "cash_roi_exec_fee",
    ]
    extra_cols = [
        "current_high_yes_ask", "current_yes_ask", "forecast_gap_native",
        "forecast_steps", "temp_trend_1h_f", "forecast_peak_delta_hours_local",
        "steps", "leg_steps", "leg", "ask_size",
    ]
    keep.extend([c for c in extra_cols if c in rows.columns])
    return StrategyFrame(name=name, description=description, rows=rows[keep].reset_index(drop=True))


def build_false_fade(expr: pd.DataFrame) -> StrategyFrame:
    settled = expr[expr["settled"].astype(bool)].copy()
    mask = (
        (settled["temp_trend_1h_f"] >= 0.5)
        & (settled["forecast_gap_native"] >= 1.0)
        & (settled["forecast_peak_delta_hours_local"] <= 0.0)
        & (settled["d1_yes_ask"] <= 0.30)
        & (settled["current_high_yes_ask"] >= 0.40)
        & settled["d1_yes_ask"].notna()
    )
    rows = settled.loc[mask].copy()
    rows["entry_raw"] = rows["d1_yes_ask"].astype(float)
    rows["win"] = rows["d1_yes_win"].astype(bool)
    return finish_trade_rows(
        rows,
        "false_fade_reheat_conflict_d1_yes",
        "Narrow sibling trigger: warming + forecast runway + current still anchored; buy d1 YES.",
    )


def trigger_coverage(expr: pd.DataFrame, legs: pd.DataFrame) -> pd.DataFrame:
    ff_mask = (
        (expr["temp_trend_1h_f"] >= 0.5)
        & (expr["forecast_gap_native"] >= 1.0)
        & (expr["forecast_peak_delta_hours_local"] <= 0.0)
        & (expr["d1_yes_ask"] <= 0.30)
        & (expr["current_high_yes_ask"] >= 0.40)
        & expr["d1_yes_ask"].notna()
    )
    b4_mask = (
        legs["leg"].eq("d1_yes")
        & (legs["current_yes_ask"] >= 0.60)
        & (legs["temp_trend_1h_f"] >= 0.5)
        & (legs["forecast_steps"] >= 1)
        & (legs["forecast_peak_delta_hours_local"] <= 0.0)
        & legs["ask"].notna()
    )
    recs = []
    for strategy, frame in (
        ("false_fade_reheat_conflict_d1_yes", expr.loc[ff_mask].copy()),
        ("rich_current_b4_d1_yes", legs.loc[b4_mask].copy()),
    ):
        frame["target_date"] = frame["target_date"].astype(str)
        unsettled = frame[~frame["settled"].astype(bool)]
        recs.append(
            {
                "strategy": strategy,
                "trigger_rows_all_labels": len(frame),
                "trigger_rows_settled": int(frame["settled"].astype(bool).sum()),
                "trigger_rows_unsettled": len(unsettled),
                "trigger_date_min": str(frame["target_date"].min()) if len(frame) else "",
                "trigger_date_max": str(frame["target_date"].max()) if len(frame) else "",
                "unsettled_dates": ",".join(sorted(unsettled["target_date"].unique())) if len(unsettled) else "",
            }
        )
    return pd.DataFrame(recs)


def build_b4_d1(legs: pd.DataFrame) -> StrategyFrame:
    settled = legs[legs["settled"].astype(bool)].copy()
    mask = (
        settled["leg"].eq("d1_yes")
        & (settled["current_yes_ask"] >= 0.60)
        & (settled["temp_trend_1h_f"] >= 0.5)
        & (settled["forecast_steps"] >= 1)
        & (settled["forecast_peak_delta_hours_local"] <= 0.0)
        & settled["ask"].notna()
    )
    rows = settled.loc[mask].copy()
    rows["entry_raw"] = rows["ask"].astype(float)
    rows["win"] = rows["steps"] == rows["leg_steps"]
    return finish_trade_rows(
        rows,
        "rich_current_b4_d1_yes",
        "Broader HeadB shape: current YES rich, obs warming, forecast ladder at least one step hotter; buy d1 YES.",
    )


def build_b4_current_no(legs: pd.DataFrame) -> StrategyFrame:
    settled = legs[legs["settled"].astype(bool)].copy()
    mask = (
        settled["leg"].eq("current_bracket_no")
        & (settled["current_yes_ask"] >= 0.60)
        & (settled["temp_trend_1h_f"] >= 0.5)
        & (settled["forecast_steps"] >= 1)
        & (settled["forecast_peak_delta_hours_local"] <= 0.0)
        & settled["ask"].notna()
    )
    rows = settled.loc[mask].copy()
    rows["entry_raw"] = rows["ask"].astype(float)
    rows["win"] = rows["steps"] >= 1
    return finish_trade_rows(
        rows,
        "rich_current_b4_current_bracket_no",
        "Same B4 denominator, sibling expression: buy current bracket NO instead of d1 YES.",
    )


def daily_table(trades: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        trades.groupby(["strategy", "target_date"], sort=True)
        .agg(
            rows=("city", "size"),
            cities=("city", "nunique"),
            wins=("win", "sum"),
            avg_entry_raw=("entry_raw", "mean"),
            avg_entry_stress=("entry_stress", "mean"),
            avg_fee_per_1=("fee_usd_per_1_notional", "mean"),
            raw_pnl=("raw_pnl_per_1", "sum"),
            exec_no_fee_pnl=("exec_no_fee_pnl_per_1", "sum"),
            exec_fee_pnl=("exec_fee_pnl_per_1", "sum"),
            cash_roi_exec_fee=("cash_roi_exec_fee", "mean"),
        )
        .reset_index()
    )
    grouped["win_rate"] = grouped["wins"] / grouped["rows"]
    grouped["raw_roi"] = grouped["raw_pnl"] / grouped["rows"]
    grouped["exec_no_fee_roi"] = grouped["exec_no_fee_pnl"] / grouped["rows"]
    grouped["exec_fee_roi"] = grouped["exec_fee_pnl"] / grouped["rows"]
    grouped["losing_day"] = grouped["exec_fee_roi"] < 0
    grouped["le_minus50_day"] = grouped["exec_fee_roi"] <= -0.50
    return grouped


def summary_table(trades: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    recs: list[dict] = []
    for strategy, g in trades.groupby("strategy", sort=False):
        d = daily[daily["strategy"].eq(strategy)].copy()
        pnl_sorted = g["exec_fee_pnl_per_1"].sort_values(ascending=False)
        cost = float(len(g))
        top5_removed = None
        if len(g) > 5:
            top5_removed = float((g["exec_fee_pnl_per_1"].sum() - pnl_sorted.head(5).sum()) / (len(g) - 5))
        ci_lo, ci_hi = block_bootstrap_ci(d, "exec_fee_roi")
        no_fee_ci_lo, no_fee_ci_hi = block_bootstrap_ci(d, "exec_no_fee_roi")
        recent = g[g["target_date"] >= RECENT_START]
        recs.append(
            {
                "strategy": strategy,
                "rows": len(g),
                "dates": g["target_date"].nunique(),
                "cities": g["city"].nunique(),
                "win_rate": g["win"].mean(),
                "avg_entry_raw": g["entry_raw"].mean(),
                "avg_entry_stress": g["entry_stress"].mean(),
                "avg_fee_per_1": g["fee_usd_per_1_notional"].mean(),
                "raw_roi": g["raw_pnl_per_1"].mean(),
                "exec_no_fee_roi": g["exec_no_fee_pnl_per_1"].mean(),
                "exec_no_fee_ci_low": no_fee_ci_lo,
                "exec_no_fee_ci_high": no_fee_ci_hi,
                "exec_fee_roi": g["exec_fee_pnl_per_1"].mean(),
                "exec_fee_ci_low": ci_lo,
                "exec_fee_ci_high": ci_hi,
                "top5_removed_exec_fee_roi": top5_removed,
                "daily_pnl_sum": d["exec_fee_pnl"].sum(),
                "losing_days": int(d["losing_day"].sum()),
                "le_minus50_days": int(d["le_minus50_day"].sum()),
                "max_daily_loss": d["exec_fee_pnl"].min(),
                "worst_daily_roi": d["exec_fee_roi"].min(),
                "recent_rows": len(recent),
                "recent_dates": recent["target_date"].nunique(),
                "recent_exec_fee_roi": recent["exec_fee_pnl_per_1"].mean() if len(recent) else np.nan,
            }
        )
    return pd.DataFrame(recs)


def period_table(trades: pd.DataFrame) -> pd.DataFrame:
    rows = trades.copy()
    rows["period"] = np.select(
        [
            rows["target_date"] < "2026-06-01",
            (rows["target_date"] >= "2026-06-01") & (rows["target_date"] < RECENT_START),
            rows["target_date"] >= RECENT_START,
        ],
        ["2026-05", "2026-06-01..06-20", f">={RECENT_START}"],
        default="other",
    )
    return (
        rows.groupby(["strategy", "period"], sort=True)
        .agg(
            rows=("city", "size"),
            dates=("target_date", "nunique"),
            cities=("city", "nunique"),
            wins=("win", "sum"),
            avg_entry_stress=("entry_stress", "mean"),
            exec_fee_pnl=("exec_fee_pnl_per_1", "sum"),
        )
        .reset_index()
        .assign(
            win_rate=lambda x: x["wins"] / x["rows"],
            exec_fee_roi=lambda x: x["exec_fee_pnl"] / x["rows"],
        )
    )


def md_table(df: pd.DataFrame, cols: list[str], max_rows: int | None = None) -> str:
    view = df[cols].copy()
    if max_rows is not None:
        view = view.head(max_rows)
    for col in view.columns:
        if col in {
            "win_rate", "raw_roi", "exec_no_fee_roi", "exec_fee_roi", "cash_roi_exec_fee",
            "exec_fee_ci_low", "exec_fee_ci_high", "exec_no_fee_ci_low", "exec_no_fee_ci_high",
            "top5_removed_exec_fee_roi", "worst_daily_roi", "recent_exec_fee_roi",
        }:
            view[col] = view[col].map(pct)
        elif col in {"avg_entry_raw", "avg_entry_stress", "avg_fee_per_1"}:
            view[col] = view[col].map(lambda x: num(x, 3))
        elif col in {"raw_pnl", "exec_no_fee_pnl", "exec_fee_pnl", "daily_pnl_sum", "max_daily_loss"}:
            view[col] = view[col].map(money)
    str_view = view.astype(str)
    headers = list(str_view.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in str_view.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def write_report(
    expr: pd.DataFrame,
    legs: pd.DataFrame,
    strategies: list[StrategyFrame],
    trades: pd.DataFrame,
    daily: pd.DataFrame,
    summary: pd.DataFrame,
    periods: pd.DataFrame,
    coverage: pd.DataFrame,
) -> None:
    false_fade_daily = daily[daily["strategy"].eq("false_fade_reheat_conflict_d1_yes")].copy()
    b4_daily = daily[daily["strategy"].eq("rich_current_b4_d1_yes")].copy()
    current_no_daily = daily[daily["strategy"].eq("rich_current_b4_current_bracket_no")].copy()

    lines = [
        "# HeadB METAR Reversal Daily Breakdown v1",
        "",
        f"Generated: `{now_utc()}`",
        "",
        "## Verdict",
        "",
        "`metar_reversal` remains `shadow_candidate_keep_collecting`; do not start real-money live from this table alone.",
        "",
        "The daily shape is bursty: many trigger days lose the full stake, while a small number of one-step reversal days carry the positive total. This is the core reason HeadB needs more fresh-forward shadow and depth-aware replay before live.",
        "",
        "## Coverage And Cost Model",
        "",
        f"- expression matrix: `{EXPR_ROWS.relative_to(ROOT)}` rows={len(expr):,}, settled dates `{expr[expr['settled'].astype(bool)]['target_date'].min()}`..`{expr[expr['settled'].astype(bool)]['target_date'].max()}`.",
        f"- reversal shape leg matrix: `{LEG_ROWS.relative_to(ROOT)}` rows={len(legs):,}, settled dates `{legs[legs['settled'].astype(bool)]['target_date'].min()}`..`{legs[legs['settled'].astype(bool)]['target_date'].max()}`.",
        f"- executable-ish daily ROI uses `$1` per trigger, taker entry at `ask + {TAKER_STRESS:.2f}`, and weather taker fee `shares * {WEATHER_TAKER_FEE_RATE:.2f} * p * (1-p)`. For `$1` notional this is `{WEATHER_TAKER_FEE_RATE:.2f} * (1-p)` dollars.",
        "- `raw_roi` is kept only to reconcile older no-fee historical reports.",
        "- Although the underlying feature shards have some later unlabeled dates, the HeadB trigger masks have no unsettled trigger rows in the current generated matrices:",
        "",
        md_table(
            coverage,
            [
                "strategy", "trigger_rows_all_labels", "trigger_rows_settled",
                "trigger_rows_unsettled", "trigger_date_min", "trigger_date_max",
                "unsettled_dates",
            ],
        ),
        "",
        "## Summary",
        "",
        md_table(
            summary,
            [
                "strategy", "rows", "dates", "cities", "win_rate", "avg_entry_stress",
                "raw_roi", "exec_no_fee_roi", "exec_fee_roi", "exec_fee_ci_low",
                "exec_fee_ci_high", "top5_removed_exec_fee_roi", "losing_days",
                "le_minus50_days", "max_daily_loss", "recent_rows", "recent_exec_fee_roi",
            ],
        ),
        "",
        "## Period Split",
        "",
        md_table(
            periods,
            ["strategy", "period", "rows", "dates", "cities", "win_rate", "avg_entry_stress", "exec_fee_pnl", "exec_fee_roi"],
        ),
        "",
        "## Daily: false_fade_reheat_conflict -> d1 YES",
        "",
        md_table(
            false_fade_daily,
            [
                "target_date", "rows", "cities", "wins", "win_rate", "avg_entry_stress",
                "avg_fee_per_1", "exec_fee_pnl", "exec_fee_roi",
            ],
        ),
        "",
        "## Daily: rich_current B4 -> d1 YES",
        "",
        md_table(
            b4_daily,
            [
                "target_date", "rows", "cities", "wins", "win_rate", "avg_entry_stress",
                "avg_fee_per_1", "exec_fee_pnl", "exec_fee_roi",
            ],
        ),
        "",
        "## Same-Denominator Sibling: B4 -> current bracket NO",
        "",
        md_table(
            current_no_daily,
            [
                "target_date", "rows", "cities", "wins", "win_rate", "avg_entry_stress",
                "avg_fee_per_1", "exec_fee_pnl", "exec_fee_roi",
            ],
        ),
        "",
        "## Read",
        "",
        "- `false_fade` is the cleaner but thinner slice. Fee-adjusted/stressed ROI is positive overall, but June flips slightly negative and the 6/21+ window has only one loser. That is not live evidence.",
        "- `B4 d1 YES` has more rows and remains positive after +1c + weather fee, but top5-removed is negative. The shape exists historically, yet the edge is concentrated.",
        "- `B4 current bracket NO` is the same denominator and wins on the same dates, but worse carry than d1 YES. It buys overshoot protection that was not valuable enough historically.",
        "- Worst losing days are normal for this structure: when the one-step reversal does not happen, a trigger usually loses the entire $1 stake.",
        "- This table does not solve the current blocker: top-of-book depth at trigger time. Before live, rerun B4/false_fade with entry-time full depth, partial fills, and fresh shadow settlement.",
        "",
        "## Generated Artifacts",
        "",
        f"- `{(OUT_DIR / 'headb_daily_breakdown.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'headb_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'headb_periods.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'headb_trade_rows.csv').relative_to(ROOT)}`",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    expr = pd.read_csv(EXPR_ROWS, low_memory=False)
    legs = pd.read_csv(LEG_ROWS, low_memory=False)
    expr["target_date"] = expr["target_date"].astype(str)
    legs["target_date"] = legs["target_date"].astype(str)

    strategies = [build_false_fade(expr), build_b4_d1(legs), build_b4_current_no(legs)]
    trades = pd.concat([s.rows for s in strategies], ignore_index=True)
    daily = daily_table(trades)
    summary = summary_table(trades, daily)
    periods = period_table(trades)
    coverage = trigger_coverage(expr, legs)

    trades.to_csv(OUT_DIR / "headb_trade_rows.csv", index=False)
    daily.to_csv(OUT_DIR / "headb_daily_breakdown.csv", index=False)
    summary.to_csv(OUT_DIR / "headb_summary.csv", index=False)
    periods.to_csv(OUT_DIR / "headb_periods.csv", index=False)
    coverage.to_csv(OUT_DIR / "headb_trigger_coverage.csv", index=False)
    write_report(expr, legs, strategies, trades, daily, summary, periods, coverage)

    print(f"wrote {OUT_MD.relative_to(ROOT)}")
    print(summary[["strategy", "rows", "dates", "win_rate", "exec_fee_roi", "exec_fee_ci_low", "exec_fee_ci_high"]].to_string(index=False))


if __name__ == "__main__":
    main()
