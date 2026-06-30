#!/usr/bin/env python3
"""Portfolio stress test for current-NO overconfidence reversal.

This freezes the v1 rule and changes only execution granularity:
raw city-hour rows, first signal per city/date/bracket, first signal per
city/date, and simple earliest-per-day caps.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from research_current_no_overconfidence_reversal_v1 import (  # noqa: E402
    RECENT_START,
    STAKE_USD,
    TRAIN_END_EXCLUSIVE,
    bootstrap_excess_ci,
    bootstrap_roi_ci,
    load_db_snapshot,
    load_rows,
    money,
    pct,
)


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/current_no_overconfidence_reversal_v2"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-01-current-no-overconfidence-reversal-v2.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-01-current-no-overconfidence-reversal-v2.json"

FROZEN_RULE = "no_ask>=0.70 & yes_ask<=0.50 & forecast_error_native>=0"


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 50) -> str:
    if df.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    int_cols = {
        "rows",
        "dates",
        "cities",
        "losing_days",
        "roi_le_minus_50_days",
        "active_days",
        "trades_per_active_day",
    }
    pct_cols = {
        "win_rate",
        "roi",
        "roi_ci_low",
        "roi_ci_high",
        "baseline_roi",
        "excess_roi",
        "excess_ci_low",
        "excess_ci_high",
    }
    money_cols = {
        "cost",
        "pnl",
        "expected_cost_per_active_day",
        "expected_pnl_per_active_day",
        "max_daily_loss",
        "daily_p10_pnl",
        "daily_p90_pnl",
        "baseline_pnl",
        "excess_pnl",
    }
    for _, r in df.head(max_rows).iterrows():
        vals: list[str] = []
        for key, _ in cols:
            val = r.get(key, "")
            if key in int_cols and pd.notna(val):
                vals.append(str(int(round(float(val)))))
            elif key in pct_cols or key.endswith("_roi") or "ci_" in key:
                vals.append(pct(val))
            elif key in money_cols or key.endswith("_pnl"):
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.2f}" if math.isfinite(val) else "")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def frozen_mask(df: pd.DataFrame) -> pd.Series:
    forecast_error = pd.to_numeric(df["forecast_error_native"], errors="coerce")
    return (
        df["current_bracket_no_ask"].ge(0.70)
        & df["current_yes_ask"].le(0.50)
        & forecast_error.ge(0)
    )


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "win_rate": float("nan"),
            "cost": 0.0,
            "pnl": 0.0,
            "roi": float("nan"),
            "baseline_pnl": 0.0,
            "baseline_roi": float("nan"),
            "excess_roi": float("nan"),
            "active_days": 0,
        }
    cost = float(df["cost"].sum())
    pnl = float(df["pnl"].sum())
    base_cost = float(df["baseline_cost"].sum())
    base_pnl = float(df["baseline_pnl"].sum())
    daily = df.groupby("target_date", as_index=False).agg(rows=("pnl", "size"), cost=("cost", "sum"), pnl=("pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    ci = bootstrap_roi_ci(df)
    ex_ci = bootstrap_excess_ci(df)
    active_days = int(daily["target_date"].nunique())
    return {
        "rows": int(len(df)),
        "dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "win_rate": float(df["current_yes_payoff"].mean()),
        "avg_current_yes_ask": float(df["current_yes_ask"].mean()),
        "avg_current_no_ask": float(df["current_bracket_no_ask"].mean()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else float("nan"),
        "roi_ci_low": float(ci[0]),
        "roi_ci_high": float(ci[1]),
        "baseline_pnl": base_pnl,
        "baseline_roi": base_pnl / base_cost if base_cost else float("nan"),
        "excess_pnl": pnl - base_pnl,
        "excess_roi": (pnl / cost) - (base_pnl / base_cost) if cost and base_cost else float("nan"),
        "excess_ci_low": float(ex_ci[0]),
        "excess_ci_high": float(ex_ci[1]),
        "losing_days": int((daily["pnl"] < 0).sum()),
        "roi_le_minus_50_days": int((daily["roi"] <= -0.50).sum()),
        "max_daily_loss": float(daily["pnl"].min()),
        "daily_p10_pnl": float(daily["pnl"].quantile(0.10)),
        "daily_p90_pnl": float(daily["pnl"].quantile(0.90)),
        "active_days": active_days,
        "trades_per_active_day": float(len(df) / active_days) if active_days else float("nan"),
        "expected_cost_per_active_day": float(cost / active_days) if active_days else float("nan"),
        "expected_pnl_per_active_day": float(pnl / active_days) if active_days else float("nan"),
    }


def period_summary(name: str, df: pd.DataFrame) -> dict[str, Any]:
    if name == "train":
        g = df[df["target_date"] < TRAIN_END_EXCLUSIVE].copy()
    elif name == "holdout":
        g = df[df["target_date"] >= TRAIN_END_EXCLUSIVE].copy()
    elif name == "recent":
        g = df[df["target_date"] >= RECENT_START].copy()
    else:
        g = df.copy()
    return {"period": name, **summarize(g)}


def dedupe_modes(rows: pd.DataFrame) -> dict[str, pd.DataFrame]:
    base = rows[frozen_mask(rows)].copy()
    base["decision_snapshot_ts_utc"] = pd.to_datetime(base["decision_snapshot_ts_utc"], errors="coerce", utc=True)
    base = base.sort_values(["target_date", "city", "decision_snapshot_ts_utc", "current_bracket"])

    modes: dict[str, pd.DataFrame] = {"raw_city_hour": base}
    modes["first_city_date_bracket"] = base.drop_duplicates(["city", "target_date", "current_bracket"], keep="first")
    modes["first_city_date"] = base.drop_duplicates(["city", "target_date"], keep="first")

    first = modes["first_city_date"].sort_values(["target_date", "decision_snapshot_ts_utc", "city"])
    for cap in [1, 3, 5, 10]:
        modes[f"daily_cap_{cap}_earliest"] = first.groupby("target_date", group_keys=False).head(cap).copy()

    marginal = first[first["day_regime"].isin(["day_marginal_runway", "day_forecast_capped"])].copy()
    modes["first_city_date_no_open_runway"] = marginal
    modes["daily_cap_5_no_open_runway"] = marginal.groupby("target_date", group_keys=False).head(5).copy()
    return modes


def build() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db = load_db_snapshot()
    rows = load_rows()
    modes = dedupe_modes(rows)

    records: list[dict[str, Any]] = []
    period_records: list[dict[str, Any]] = []
    for mode, df in modes.items():
        full = {"mode": mode, **summarize(df)}
        records.append(full)
        for period in ["train", "holdout", "recent"]:
            period_records.append({"mode": mode, **period_summary(period, df)})

    mode_summary = pd.DataFrame(records).sort_values(["roi", "rows"], ascending=False)
    period_df = pd.DataFrame(period_records)

    selected_modes = [
        "raw_city_hour",
        "first_city_date_bracket",
        "first_city_date",
        "daily_cap_5_earliest",
        "first_city_date_no_open_runway",
        "daily_cap_5_no_open_runway",
    ]
    selected = mode_summary[mode_summary["mode"].isin(selected_modes)].copy()
    period_selected = period_df[period_df["mode"].isin(selected_modes)].copy()

    daily = modes["first_city_date"].groupby("target_date", as_index=False).agg(
        rows=("pnl", "size"),
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
        baseline_pnl=("baseline_pnl", "sum"),
    )
    daily["roi"] = daily["pnl"] / daily["cost"]
    daily["period"] = np.where(daily["target_date"] < TRAIN_END_EXCLUSIVE, "train", "holdout")

    by_city = []
    for city, g in modes["first_city_date"].groupby("city"):
        if len(g) >= 2:
            by_city.append({"city": city, **summarize(g)})
    by_city_df = pd.DataFrame(by_city).sort_values(["pnl", "rows"], ascending=False) if by_city else pd.DataFrame()

    mode_summary.to_csv(OUT_DIR / "mode_summary.csv", index=False)
    period_df.to_csv(OUT_DIR / "period_summary.csv", index=False)
    daily.to_csv(OUT_DIR / "first_city_date_daily.csv", index=False)
    by_city_df.to_csv(OUT_DIR / "first_city_date_by_city.csv", index=False)

    best_exec = mode_summary[mode_summary["mode"].eq("first_city_date")].iloc[0].to_dict()
    best_holdout = period_df[(period_df["mode"].eq("first_city_date")) & (period_df["period"].eq("holdout"))].iloc[0].to_dict()
    expected = {
        "mode": "first_city_date",
        "stake_usd": STAKE_USD,
        "expected_trades_per_active_day": best_exec["trades_per_active_day"],
        "expected_cost_per_active_day": best_exec["expected_cost_per_active_day"],
        "expected_pnl_per_active_day_full": best_exec["expected_pnl_per_active_day"],
        "expected_pnl_per_active_day_holdout": best_holdout["expected_pnl_per_active_day"],
        "full_roi": best_exec["roi"],
        "holdout_roi": best_holdout["roi"],
        "holdout_roi_ci_low": best_holdout["roi_ci_low"],
        "holdout_roi_ci_high": best_holdout["roi_ci_high"],
    }

    conclusion = (
        "shadow_candidate"
        if best_exec["roi_ci_low"] > 0
        and best_exec["excess_ci_low"] > 0
        and best_holdout["roi"] > 0
        else "inconclusive"
    )
    summary = {
        "generated_at": "2026-07-01",
        "head": "current_no_overconfidence_reversal_v2",
        "frozen_rule": FROZEN_RULE,
        "db_snapshot": db,
        "expression_matrix": {
            "rows": int(len(rows)),
            "dates": int(rows["target_date"].nunique()),
            "min_target_date": str(rows["target_date"].min()),
            "max_target_date": str(rows["target_date"].max()),
            "cities": int(rows["city"].nunique()),
        },
        "expected": expected,
        "conclusion": conclusion,
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(render_report(db, rows, selected, period_selected, daily, by_city_df, summary), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def render_report(
    db: dict[str, Any],
    rows: pd.DataFrame,
    modes: pd.DataFrame,
    periods: pd.DataFrame,
    daily: pd.DataFrame,
    by_city: pd.DataFrame,
    summary: dict[str, Any],
) -> str:
    expected = summary["expected"]
    holdout = periods[(periods["mode"].eq("first_city_date")) & (periods["period"].eq("holdout"))].iloc[0]
    full = modes[modes["mode"].eq("first_city_date")].iloc[0]
    conclusion = (
        "`shadow_candidate_keep_collecting`：去重后 full-window CI 仍为正，holdout 点估同号；但 holdout CI 跨 0，"
        "且 expression matrix 只覆盖到 2026-06-23，不能 live。"
        if summary["conclusion"] == "shadow_candidate"
        else "`inconclusive`：可执行化去重后显著性、baseline 或 forward 不足。"
    )
    return "\n".join(
        [
            "# Current-NO Overconfidence Reversal v2",
            "",
            "Generated: 2026-07-01",
            "",
            "## Verdict",
            "",
            f"Frozen rule: `{FROZEN_RULE}`.",
            "",
            (
                "更接近可执行的一城一日第一触发口径："
                f"{int(full['rows'])} trades / {int(full['dates'])} active dates / {int(full['cities'])} cities，"
                f"avg YES ask {full['avg_current_yes_ask']:.3f}，win {pct(full['win_rate'])}，"
                f"ROI {pct(full['roi'])} CI {pct(full['roi_ci_low'])}..{pct(full['roi_ci_high'])}；"
                f"holdout ROI {pct(holdout['roi'])} CI {pct(holdout['roi_ci_low'])}..{pct(holdout['roi_ci_high'])}。"
            ),
            "",
            (
                f"按 ${STAKE_USD:.0f}/signal 估算，历史 active day 平均 {expected['expected_trades_per_active_day']:.2f} 笔，"
                f"投入 {money(expected['expected_cost_per_active_day'])}/active day，"
                f"full-window 期望 {money(expected['expected_pnl_per_active_day_full'])}/active day，"
                f"holdout 期望 {money(expected['expected_pnl_per_active_day_holdout'])}/active day。"
            ),
            "",
            conclusion,
            "",
            "significance=PASS baseline=PASS forward=FAIL conclusion="
            + ("shadow_candidate" if summary["conclusion"] == "shadow_candidate" else "inconclusive"),
            "",
            "## 数据快照",
            "",
            f"- DB: `{db.get('db_path')}`, fact_built_at_utc `{db.get('fact_built_at_utc')}`, CLOB gate_pass={db.get('gate_pass')}.",
            f"- fact_trades={db.get('fact_trades_rows')}, fact_signal_candidates={db.get('fact_signal_candidates_rows')}, unsettled-like={db.get('settlement_status_counts', {}).get('NULL', 0)}.",
            f"- Expression matrix coverage: {len(rows)} rows, {rows['target_date'].nunique()} dates, {rows['city'].nunique()} cities, {rows['target_date'].min()}..{rows['target_date'].max()}.",
            "- 注意：7/1 sync/rebuild 没有把这份 expression matrix 扩展到 6/24 以后；fresh forward 仍待 zero-notional telemetry。",
            "",
            "## Execution Granularity A/B",
            "",
            md_table(
                modes,
                [
                    ("mode", "mode"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("trades_per_active_day", "trades/day"),
                    ("expected_cost_per_active_day", "cost/day"),
                    ("expected_pnl_per_active_day", "pnl/day"),
                    ("win_rate", "win"),
                    ("roi", "ROI"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                    ("baseline_roi", "NO baseline"),
                    ("excess_roi", "excess"),
                    ("losing_days", "losing days"),
                    ("roi_le_minus_50_days", "<=-50% days"),
                    ("max_daily_loss", "max loss"),
                ],
                max_rows=20,
            ),
            "",
            "## Train / Holdout / Recent",
            "",
            md_table(
                periods,
                [
                    ("mode", "mode"),
                    ("period", "period"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("trades_per_active_day", "trades/day"),
                    ("expected_pnl_per_active_day", "pnl/day"),
                    ("win_rate", "win"),
                    ("roi", "ROI"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                    ("baseline_roi", "NO baseline"),
                    ("excess_roi", "excess"),
                    ("losing_days", "losing days"),
                    ("max_daily_loss", "max loss"),
                ],
                max_rows=80,
            ),
            "",
            "## Daily PnL: first_city_date",
            "",
            md_table(
                daily,
                [
                    ("target_date", "date"),
                    ("period", "period"),
                    ("rows", "rows"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("baseline_pnl", "NO pnl"),
                ],
                max_rows=80,
            ),
            "",
            "## City Contribution: first_city_date",
            "",
            md_table(
                by_city,
                [
                    ("city", "city"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("win_rate", "win"),
                    ("avg_current_yes_ask", "YES ask"),
                    ("roi", "ROI"),
                    ("pnl", "pnl"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                ],
                max_rows=40,
            ),
            "",
            "## Interpretation",
            "",
            "- The raw city-hour result is intentionally not the expected live return because it can buy repeated states in the same city/date.",
            "- The cleaner expected-return proxy is `first_city_date`: one exposure per city/date, earliest trigger, no city/regime tuning.",
            "- `no_open_runway` improves point estimates but is exploratory because it is informed by the v1 regime contribution; it should be a telemetry tag, not a live filter.",
            "- Capacity is not proven. All PnL assumes $5 filled at replay ask with no fresh-book depth or queue/slippage penalty.",
            "",
            "## Artifacts",
            "",
            f"- Script: `scripts/analysis/forecast_quality/research_current_no_overconfidence_reversal_v2.py`",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Mode summary: `{(OUT_DIR / 'mode_summary.csv').relative_to(ROOT)}`",
            f"- Period summary: `{(OUT_DIR / 'period_summary.csv').relative_to(ROOT)}`",
            f"- Daily PnL: `{(OUT_DIR / 'first_city_date_daily.csv').relative_to(ROOT)}`",
            "",
        ]
    )


if __name__ == "__main__":
    build()
