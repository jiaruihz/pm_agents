#!/usr/bin/env python3
"""Overlay city/source forecast-bias fit onto NO/YES regime strategies.

This is intentionally an overlay, not a new gate. It asks whether existing
current-NO, higher-NO, and current-YES expressions line up with the historical
city/source bias regimes.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]

CITY_FIT = ROOT / "docs/analysis/2026-06/generated/city_strategy_fit_by_forecast_bias_v1/city_strategy_fit_by_forecast_bias.csv"
V3_DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_expression_router_v3/trade_details.csv"
CURRENT_YES_EVENTS = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv"

OUT_DIR = ROOT / "docs/analysis/2026-06/generated/strategy_regime_city_fit_overlay_v1"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-30-strategy-regime-city-fit-overlay-v1.md"

FORWARD_START = "2026-06-21"
STAKE_USD = 5.0


def pct(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"{100*v:+.1f}%"


def fmt(x: Any, digits: int = 2) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"{v:.{digits}f}"


def money(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"${v:+.2f}"


def load_city_fit() -> pd.DataFrame:
    if not CITY_FIT.exists():
        raise SystemExit(
            f"missing {CITY_FIT}; run research_city_strategy_fit_by_forecast_bias_v1.py first"
        )
    cols = [
        "city",
        "source_bias_regime",
        "best_model",
        "runway_current_bracket_no_fit",
        "forecast_capped_higher_no_fit",
        "current_high_yes_or_peak_fade_fit",
    ]
    return pd.read_csv(CITY_FIT, usecols=cols)


def with_city_fit(df: pd.DataFrame, fit: pd.DataFrame) -> pd.DataFrame:
    out = df.merge(fit, on="city", how="left")
    out["source_bias_regime"] = out["source_bias_regime"].fillna("unclassified")
    for col in [
        "runway_current_bracket_no_fit",
        "forecast_capped_higher_no_fit",
        "current_high_yes_or_peak_fade_fit",
    ]:
        out[col] = out[col].fillna("unclassified")
    return out


def summarize(
    df: pd.DataFrame,
    group_cols: list[str],
    *,
    cost_col: str,
    pnl_col: str,
    payoff_col: str | None = None,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=group_cols + ["rows"])
    d = df.copy()
    d[cost_col] = pd.to_numeric(d[cost_col], errors="coerce")
    d[pnl_col] = pd.to_numeric(d[pnl_col], errors="coerce")
    if payoff_col:
        d[payoff_col] = pd.to_numeric(d[payoff_col], errors="coerce")
    d = d.dropna(subset=[cost_col, pnl_col])
    if d.empty:
        return pd.DataFrame(columns=group_cols + ["rows"])
    agg = d.groupby(group_cols, dropna=False).agg(
        rows=(pnl_col, "size"),
        dates=("target_date", "nunique"),
        cities=("city", "nunique"),
        cost=(cost_col, "sum"),
        pnl=(pnl_col, "sum"),
        avg_cost=(cost_col, "mean"),
    )
    if payoff_col:
        agg["win_rate"] = d.groupby(group_cols, dropna=False)[payoff_col].mean()
    else:
        agg["win_rate"] = d.groupby(group_cols, dropna=False)[pnl_col].apply(lambda s: (s > 0).mean())
    out = agg.reset_index()
    out["roi"] = out["pnl"] / out["cost"]
    out = out.sort_values(["cost", "rows"], ascending=False)
    return out


def load_selected_strategy(fit: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_csv(V3_DETAILS, low_memory=False)
    keep = df["router_expression"].astype(str).ne("none")
    df = df[keep].copy()
    df["target_date"] = df["target_date"].astype(str)
    df["strategy_family"] = "regime_routed_v3_selected"
    df["expression_group"] = np.select(
        [
            df["router_expression"].eq("current_bracket_no"),
            df["router_expression"].eq("d2_no"),
            df["router_expression"].eq("current_high_yes"),
        ],
        ["current_bracket_no", "higher_no_d2", "current_high_yes"],
        default=df["router_expression"].astype(str),
    )
    df["cost"] = pd.to_numeric(df["router_cost_usd"], errors="coerce")
    df["pnl"] = pd.to_numeric(df["router_pnl_usd"], errors="coerce")
    df["weighted_cost"] = pd.to_numeric(df["router_weighted_cost_usd"], errors="coerce")
    df["weighted_pnl"] = pd.to_numeric(df["router_weighted_pnl_usd"], errors="coerce")
    df["payoff"] = pd.to_numeric(df["router_payoff"], errors="coerce")
    return with_city_fit(df, fit)


def load_expression_matrix(fit: pd.DataFrame) -> pd.DataFrame:
    src = pd.read_csv(CURRENT_YES_EVENTS, low_memory=False)
    src["target_date"] = src["target_date"].astype(str)
    rows: list[pd.DataFrame] = []
    specs = [
        ("current_yes", "current_yes_ask", "current_yes_payoff", "current_yes_roi"),
        ("current_bracket_no", "current_bracket_no_ask", "current_bracket_no_payoff", "current_bracket_no_roi"),
        ("higher_no_d1", "d1_no_ask", "d1_no_payoff", "d1_no_roi"),
        ("higher_no_d2", "d2_no_ask", "d2_no_payoff", "d2_no_roi"),
    ]
    common_cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "unit",
        "day_regime",
        "intraday_state",
        "running_max_state",
        "composite_regime",
        "period",
        "rule",
    ]
    for expression, ask_col, payoff_col, roi_col in specs:
        if ask_col not in src.columns or payoff_col not in src.columns:
            continue
        d = src[common_cols + [ask_col, payoff_col, roi_col]].copy()
        d = d.rename(columns={ask_col: "ask", payoff_col: "payoff", roi_col: "unit_roi"})
        d["expression_group"] = expression
        d["strategy_family"] = "current_yes_event_expression_matrix"
        d["ask"] = pd.to_numeric(d["ask"], errors="coerce")
        d["payoff"] = pd.to_numeric(d["payoff"], errors="coerce")
        d = d[(d["ask"] > 0) & (d["ask"] < 1.0) & d["payoff"].isin([0.0, 1.0])].copy()
        d["cost"] = STAKE_USD
        d["pnl"] = d["payoff"] * (STAKE_USD / d["ask"]) - STAKE_USD
        rows.append(d)
    matrix = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return with_city_fit(matrix, fit)


def add_fit_alignment(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["city_fit_alignment"] = "neutral_or_unknown"
    d.loc[
        d["expression_group"].eq("current_bracket_no")
        & d["runway_current_bracket_no_fit"].isin(["strong", "medium"]),
        "city_fit_alignment",
    ] = "aligned"
    d.loc[
        d["expression_group"].isin(["higher_no_d1", "higher_no_d2"])
        & d["forecast_capped_higher_no_fit"].isin(["strong", "medium"]),
        "city_fit_alignment",
    ] = "aligned"
    d.loc[
        d["expression_group"].eq("current_high_yes")
        & d["current_high_yes_or_peak_fade_fit"].isin(["strong", "medium"]),
        "city_fit_alignment",
    ] = "aligned"
    d.loc[
        d["expression_group"].eq("current_yes")
        & d["current_high_yes_or_peak_fade_fit"].isin(["strong", "medium"]),
        "city_fit_alignment",
    ] = "aligned"

    d.loc[
        d["expression_group"].eq("current_bracket_no")
        & d["runway_current_bracket_no_fit"].isin(["weak", "shadow_only"]),
        "city_fit_alignment",
    ] = "against_fit"
    d.loc[
        d["expression_group"].isin(["higher_no_d1", "higher_no_d2"])
        & d["forecast_capped_higher_no_fit"].isin(["weak", "shadow_only"]),
        "city_fit_alignment",
    ] = "against_fit"
    d.loc[
        d["expression_group"].isin(["current_yes", "current_high_yes"])
        & d["current_high_yes_or_peak_fade_fit"].isin(["weak", "shadow_only"]),
        "city_fit_alignment",
    ] = "against_fit"
    return d


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 30) -> str:
    if df.empty:
        return "_No rows._"
    d = df.head(max_rows).copy()
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, r in d.iterrows():
        vals = []
        for key, _ in cols:
            val = r.get(key, "")
            if key.endswith("roi") or key == "win_rate":
                vals.append(pct(val))
            elif key in {"cost", "pnl"}:
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(fmt(val))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_report(
    selected: pd.DataFrame,
    matrix: pd.DataFrame,
    selected_expr: pd.DataFrame,
    selected_fit: pd.DataFrame,
    selected_day: pd.DataFrame,
    matrix_expr_fit: pd.DataFrame,
    matrix_day_fit: pd.DataFrame,
    forward_selected: pd.DataFrame,
) -> str:
    return "\n".join(
        [
            "# Strategy Regime City-Fit Overlay v1",
            "",
            "Generated: 2026-06-30",
            "",
            "## Verdict",
            "",
            "需要继续完善城市分类和 regime 分布。当前证据支持把 `city/source forecast-bias fit` 作为 selection/sizing feature 接入 replay/shadow，但不支持直接做 live hard gate。",
            "",
            "主要结论：",
            "",
            "- 城市分类本身不是 alpha。raw expression matrix 里 `current_bracket_no` 即使在 hot-underforecast 城市也亏，说明必须叠加 day_regime / intraday_state / price 才能交易。",
            "- 在 selected strategy rows 里，`current_bracket_no × hot_underforecast_clean` 表现最好；但 `hot_underforecast_noisy` 反而弱，说明要区分 clean/noisy，不应把 hot 城市一起扩大。",
            "- `higher_no_d1/d2` 和 `current_yes/current_high_yes` 更适合继续在 cold-overforecast / balanced-tight 城市做 shadow；但 raw matrix 里 current YES 在多类城市都正，可能有 market/base-rate 成分，不能直接说是 forecast-bias alpha。",
            "- `two_sided_noisy` 城市需要继续 shadow 或降 size，不能因为某个 route 点估好就扩大。",
            "",
            "## Evidence Layers",
            "",
            f"- Selected strategy rows: `{V3_DETAILS.relative_to(ROOT)}` ({len(selected)} rows).",
            f"- Expression payoff matrix: `{CURRENT_YES_EVENTS.relative_to(ROOT)}` expanded to current YES / current NO / d1 NO / d2 NO ({len(matrix)} expression rows).",
            f"- City fit map: `{CITY_FIT.relative_to(ROOT)}`.",
            "",
            "## Selected Strategy: Expression Summary",
            "",
            md_table(
                selected_expr,
                [
                    ("expression_group", "expression"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("win_rate", "win"),
                ],
            ),
            "",
            "## Selected Strategy: City-Fit Alignment",
            "",
            md_table(
                selected_fit,
                [
                    ("expression_group", "expression"),
                    ("city_fit_alignment", "fit"),
                    ("source_bias_regime", "city regime"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("win_rate", "win"),
                ],
                max_rows=40,
            ),
            "",
            "## Selected Strategy: Day Regime",
            "",
            md_table(
                selected_day,
                [
                    ("expression_group", "expression"),
                    ("day_regime", "day_regime"),
                    ("city_fit_alignment", "fit"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("win_rate", "win"),
                ],
                max_rows=50,
            ),
            "",
            "## Expression Matrix: City-Fit Summary",
            "",
            "This is not a selected live strategy. It asks what each expression would have done on the same current-YES event rows.",
            "",
            md_table(
                matrix_expr_fit,
                [
                    ("expression_group", "expression"),
                    ("city_fit_alignment", "fit"),
                    ("source_bias_regime", "city regime"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("win_rate", "win"),
                ],
                max_rows=60,
            ),
            "",
            "## Expression Matrix: Day Regime x Fit",
            "",
            md_table(
                matrix_day_fit,
                [
                    ("expression_group", "expression"),
                    ("day_regime", "day_regime"),
                    ("city_fit_alignment", "fit"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("win_rate", "win"),
                ],
                max_rows=80,
            ),
            "",
            f"## Forward Window Since {FORWARD_START}",
            "",
            md_table(
                forward_selected,
                [
                    ("expression_group", "expression"),
                    ("city_fit_alignment", "fit"),
                    ("source_bias_regime", "city regime"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("win_rate", "win"),
                ],
                max_rows=50,
            ),
            "",
            "## Next Implementation",
            "",
            "- Add `source_bias_regime` and route-specific fit columns to shadow logs for current NO, higher NO, and current YES.",
            "- Replay route policies with city-fit as soft prior/size multiplier, not as a hard city allowlist.",
            "- For expansion: prioritize `hot_underforecast_clean`, not all hot cities, for current-NO shadow.",
            "- For higher-NO/current-YES: prioritize cold-overforecast and balanced-tight city shadow, then require route-specific forward evidence before live sizing.",
            "- Keep `two_sided_noisy` as telemetry or reduced size until expression-specific forward evidence improves.",
            "",
            "## Artifacts",
            "",
            f"- Selected rows with fit: `{(OUT_DIR / 'selected_strategy_rows_with_city_fit.csv').relative_to(ROOT)}`",
            f"- Expression matrix rows with fit: `{(OUT_DIR / 'expression_matrix_rows_with_city_fit.csv').relative_to(ROOT)}`",
            f"- Summaries: `{OUT_DIR.relative_to(ROOT)}/*.csv`",
            "",
        ]
    )


def build(_: argparse.Namespace) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fit = load_city_fit()
    selected = add_fit_alignment(load_selected_strategy(fit))
    matrix = add_fit_alignment(load_expression_matrix(fit))

    selected_expr = summarize(
        selected,
        ["expression_group"],
        cost_col="cost",
        pnl_col="pnl",
        payoff_col="payoff",
    )
    selected_fit = summarize(
        selected,
        ["expression_group", "city_fit_alignment", "source_bias_regime"],
        cost_col="cost",
        pnl_col="pnl",
        payoff_col="payoff",
    )
    selected_day = summarize(
        selected,
        ["expression_group", "day_regime", "city_fit_alignment"],
        cost_col="cost",
        pnl_col="pnl",
        payoff_col="payoff",
    )
    matrix_expr_fit = summarize(
        matrix,
        ["expression_group", "city_fit_alignment", "source_bias_regime"],
        cost_col="cost",
        pnl_col="pnl",
        payoff_col="payoff",
    )
    matrix_day_fit = summarize(
        matrix,
        ["expression_group", "day_regime", "city_fit_alignment"],
        cost_col="cost",
        pnl_col="pnl",
        payoff_col="payoff",
    )
    forward_selected = summarize(
        selected[selected["target_date"] >= FORWARD_START],
        ["expression_group", "city_fit_alignment", "source_bias_regime"],
        cost_col="cost",
        pnl_col="pnl",
        payoff_col="payoff",
    )

    selected.to_csv(OUT_DIR / "selected_strategy_rows_with_city_fit.csv", index=False)
    matrix.to_csv(OUT_DIR / "expression_matrix_rows_with_city_fit.csv", index=False)
    selected_expr.to_csv(OUT_DIR / "selected_expression_summary.csv", index=False)
    selected_fit.to_csv(OUT_DIR / "selected_expression_city_fit_summary.csv", index=False)
    selected_day.to_csv(OUT_DIR / "selected_expression_day_regime_summary.csv", index=False)
    matrix_expr_fit.to_csv(OUT_DIR / "expression_matrix_city_fit_summary.csv", index=False)
    matrix_day_fit.to_csv(OUT_DIR / "expression_matrix_day_regime_fit_summary.csv", index=False)
    forward_selected.to_csv(OUT_DIR / "selected_forward_since_20260621_summary.csv", index=False)

    OUT_MD.write_text(
        render_report(
            selected,
            matrix,
            selected_expr,
            selected_fit,
            selected_day,
            matrix_expr_fit,
            matrix_day_fit,
            forward_selected,
        ),
        encoding="utf-8",
    )
    return {
        "selected_rows": len(selected),
        "matrix_rows": len(matrix),
        "report": str(OUT_MD),
        "out_dir": str(OUT_DIR),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    result = build(args)
    print(result)


if __name__ == "__main__":
    main()
