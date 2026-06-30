#!/usr/bin/env python3
"""Compare running regime-routed strategy with row-level city-fit soft sizing.

Current running policy proxy:
    regime_routed_no_route_price_disciplined_tiny_live_v1
    = router_weighted_cost/pnl from regime_routed_expression_router_v3.

Overlay policy:
    current running weighted notional * city/source forecast-bias multiplier.

This is a research/shadow comparison only. It does not change live.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
HIST_SUMMARY = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/city_model_error_summary.csv"
V3_DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_expression_router_v3/trade_details.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/strategy_city_fit_soft_sizing_v2"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-30-strategy-city-fit-soft-sizing-v2.md"

FORWARD_START = "2026-06-21"
RUNNING_INSTANCE = "regime_routed_no_route_price_disciplined_tiny_live_v1"


def pct(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"{100*v:+.1f}%"


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


def classify_bias(row: pd.Series) -> str:
    bias = float(row["bias"])
    p90 = float(row["p90"])
    p10 = float(row["p10"])
    hot = float(row["pct_actual_ge_forecast_plus_1"])
    cold = float(row["pct_forecast_ge_actual_plus_1"])
    mae = float(row["mae"])
    if bias >= 0.7 and hot >= 0.40 and cold <= 0.15:
        return "hot_underforecast_clean"
    if bias >= 0.5 and p90 >= 2.0 and hot >= 0.35:
        return "hot_underforecast_noisy"
    if bias <= -0.5 and cold >= 0.35 and hot <= 0.20:
        return "cold_overforecast_clean"
    if cold >= 0.25 and p10 <= -1.5:
        return "cold_overforecast_noisy"
    if mae <= 1.0 and hot < 0.25 and cold < 0.25 and abs(bias) < 0.35:
        return "balanced_tight"
    if hot >= 0.25 and cold >= 0.20:
        return "two_sided_noisy"
    return "mild_or_mixed"


def forecast_model_from_row(row: pd.Series) -> str:
    source = str(row.get("forecast_source") or "").lower()
    clock = str(row.get("forecast_clock_source") or "").lower()
    if "ecmwf" in source or "ecmwf" in clock:
        return "ecmwf"
    if "gfs" in source or "gfs" in clock:
        return "gfs"
    return "unknown"


def city_fit_multiplier(expression_group: str, regime: str) -> float:
    """Mechanism prior, not fitted to PnL."""
    if expression_group == "current_bracket_no":
        table = {
            "hot_underforecast_clean": 1.15,
            "hot_underforecast_noisy": 0.85,
            "balanced_tight": 0.90,
            "mild_or_mixed": 0.80,
            "cold_overforecast_noisy": 0.75,
            "cold_overforecast_clean": 0.65,
            "two_sided_noisy": 0.60,
            "unclassified": 0.80,
        }
    elif expression_group in {"higher_no_d1", "higher_no_d2"}:
        table = {
            "cold_overforecast_clean": 1.15,
            "cold_overforecast_noisy": 1.05,
            "balanced_tight": 1.00,
            "mild_or_mixed": 0.90,
            "hot_underforecast_noisy": 0.75,
            "hot_underforecast_clean": 0.65,
            "two_sided_noisy": 0.70,
            "unclassified": 0.85,
        }
    else:  # current_high_yes or other YES fade-like heads
        table = {
            "cold_overforecast_clean": 1.10,
            "cold_overforecast_noisy": 1.05,
            "balanced_tight": 1.00,
            "mild_or_mixed": 0.90,
            "hot_underforecast_noisy": 0.80,
            "hot_underforecast_clean": 0.65,
            "two_sided_noisy": 0.70,
            "unclassified": 0.85,
        }
    return table.get(regime, 0.80)


def load_bias_lookup() -> pd.DataFrame:
    hist = pd.read_csv(HIST_SUMMARY)
    hist["row_source_bias_regime"] = hist.apply(classify_bias, axis=1)
    keep = [
        "city",
        "model",
        "unit",
        "n",
        "bias",
        "mae",
        "p10",
        "p50",
        "p90",
        "pct_actual_ge_forecast_plus_1",
        "pct_forecast_ge_actual_plus_1",
        "row_source_bias_regime",
    ]
    return hist[keep].copy()


def load_selected_rows() -> pd.DataFrame:
    df = pd.read_csv(V3_DETAILS, low_memory=False)
    df = df[df["router_expression"].astype(str).ne("none")].copy()
    df["target_date"] = df["target_date"].astype(str)
    df["expression_group"] = np.select(
        [
            df["router_expression"].eq("current_bracket_no"),
            df["router_expression"].eq("d2_no"),
            df["router_expression"].eq("current_high_yes"),
        ],
        ["current_bracket_no", "higher_no_d2", "current_high_yes"],
        default=df["router_expression"].astype(str),
    )
    df["row_forecast_model"] = df.apply(forecast_model_from_row, axis=1)
    return df


def enrich_rows(df: pd.DataFrame, lookup: pd.DataFrame) -> pd.DataFrame:
    enriched = df.merge(
        lookup,
        left_on=["city", "row_forecast_model"],
        right_on=["city", "model"],
        how="left",
    )
    enriched["row_source_bias_regime"] = enriched["row_source_bias_regime"].fillna("unclassified")
    enriched["city_fit_multiplier_v2"] = [
        city_fit_multiplier(expr, regime)
        for expr, regime in zip(enriched["expression_group"], enriched["row_source_bias_regime"], strict=False)
    ]
    enriched["running_policy"] = RUNNING_INSTANCE
    enriched["running_cost_usd"] = pd.to_numeric(enriched["router_weighted_cost_usd"], errors="coerce")
    enriched["running_pnl_usd"] = pd.to_numeric(enriched["router_weighted_pnl_usd"], errors="coerce")
    enriched["city_fit_cost_usd"] = enriched["running_cost_usd"] * enriched["city_fit_multiplier_v2"]
    enriched["city_fit_pnl_usd"] = enriched["running_pnl_usd"] * enriched["city_fit_multiplier_v2"]
    return enriched


def summarize(
    df: pd.DataFrame,
    group_cols: list[str],
    *,
    cost_col: str,
    pnl_col: str,
    prefix: str,
) -> pd.DataFrame:
    d = df.dropna(subset=[cost_col, pnl_col]).copy()
    if d.empty:
        return pd.DataFrame(columns=group_cols + [f"{prefix}_rows"])
    out = d.groupby(group_cols, dropna=False).agg(
        rows=(pnl_col, "size"),
        dates=("target_date", "nunique"),
        cities=("city", "nunique"),
        cost=(cost_col, "sum"),
        pnl=(pnl_col, "sum"),
        avg_multiplier=("city_fit_multiplier_v2", "mean"),
    ).reset_index()
    out["roi"] = out["pnl"] / out["cost"]
    return out.sort_values(["cost", "rows"], ascending=False)


def policy_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for policy, cost_col, pnl_col in [
        ("running_current_row_risk_soft", "running_cost_usd", "running_pnl_usd"),
        ("city_fit_soft_overlay_v2", "city_fit_cost_usd", "city_fit_pnl_usd"),
    ]:
        d = df.dropna(subset=[cost_col, pnl_col]).copy()
        daily = d.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
        daily["roi"] = daily["pnl"] / daily["cost"]
        rows.append(
            {
                "policy": policy,
                "rows": len(d),
                "dates": d["target_date"].nunique(),
                "cities": d["city"].nunique(),
                "cost": d[cost_col].sum(),
                "pnl": d[pnl_col].sum(),
                "roi": d[pnl_col].sum() / d[cost_col].sum(),
                "avg_daily_cost": daily["cost"].mean(),
                "losing_days": int((daily["pnl"] < 0).sum()),
                "roi_le_minus_50_days": int((daily["roi"] <= -0.5).sum()),
                "max_daily_loss": daily["pnl"].min(),
                "best_day": daily["pnl"].max(),
                "forward_rows": int((d["target_date"] >= FORWARD_START).sum()),
                "forward_cost": d.loc[d["target_date"] >= FORWARD_START, cost_col].sum(),
                "forward_pnl": d.loc[d["target_date"] >= FORWARD_START, pnl_col].sum(),
            }
        )
    out = pd.DataFrame(rows)
    out["forward_roi"] = out["forward_pnl"] / out["forward_cost"]
    return out


def daily_policy_summary(df: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for policy, cost_col, pnl_col in [
        ("running_current_row_risk_soft", "running_cost_usd", "running_pnl_usd"),
        ("city_fit_soft_overlay_v2", "city_fit_cost_usd", "city_fit_pnl_usd"),
    ]:
        d = df.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"), rows=(pnl_col, "size"))
        d["policy"] = policy
        d["roi"] = d["pnl"] / d["cost"]
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


def daily_delta_summary(daily: pd.DataFrame) -> pd.DataFrame:
    wide = daily.pivot(index="target_date", columns="policy", values=["cost", "pnl", "roi", "rows"])
    wide.columns = [f"{metric}_{policy}" for metric, policy in wide.columns]
    wide = wide.reset_index()
    wide["delta_cost"] = wide["cost_city_fit_soft_overlay_v2"] - wide["cost_running_current_row_risk_soft"]
    wide["delta_pnl"] = wide["pnl_city_fit_soft_overlay_v2"] - wide["pnl_running_current_row_risk_soft"]
    wide["delta_roi"] = wide["roi_city_fit_soft_overlay_v2"] - wide["roi_running_current_row_risk_soft"]
    return wide.sort_values("target_date")


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, r in df.head(max_rows).iterrows():
        vals = []
        for key, _ in cols:
            val = r.get(key, "")
            if key in {"rows", "dates", "cities", "losing_days", "roi_le_minus_50_days"} or key.startswith("rows_"):
                vals.append(str(int(val)))
            elif key == "roi" or key.startswith("roi_") or key.endswith("_roi") or key in {"forward_roi", "delta_roi"}:
                vals.append(pct(val))
            elif key in {
                "cost",
                "pnl",
                "max_daily_loss",
                "best_day",
                "forward_cost",
                "forward_pnl",
                "avg_daily_cost",
                "pnl_running_current_row_risk_soft",
                "pnl_city_fit_soft_overlay_v2",
                "delta_pnl",
                "cost_running_current_row_risk_soft",
                "cost_city_fit_soft_overlay_v2",
                "delta_cost",
            }:
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(fmt(val))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_report(
    rows: pd.DataFrame,
    pol: pd.DataFrame,
    expr_run: pd.DataFrame,
    expr_fit: pd.DataFrame,
    route_fit: pd.DataFrame,
    regime_fit: pd.DataFrame,
    forward_fit: pd.DataFrame,
    daily: pd.DataFrame,
    daily_delta: pd.DataFrame,
) -> str:
    comparison = pol.copy()
    comparison["forward_roi"] = comparison["forward_pnl"] / comparison["forward_cost"]
    return "\n".join(
        [
            "# Strategy City-Fit Soft Sizing v2",
            "",
            "Generated: 2026-06-30",
            "",
            "## Verdict",
            "",
            f"对比对象是当前在跑的 tiny-live forward probe：`{RUNNING_INSTANCE}`。本报告只做 replay/shadow sizing 对比，不改 live。",
            "",
            "结论：city/source forecast-bias fit 适合作为 soft sizing prior，但当前证据仍是 `inconclusive_shadow_only`。同分母 283 笔上，总 ROI 小幅改善，但 2026-06-21 之后 forward 变弱；它改善组合形态的方向主要来自降低逆 city-fit 的 notional，而不是发现了新的独立 alpha。",
            "",
            "## Policy Comparison",
            "",
            md_table(
                comparison,
                [
                    ("policy", "policy"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("avg_daily_cost", "avg/day"),
                    ("losing_days", "losing days"),
                    ("roi_le_minus_50_days", "<=-50% days"),
                    ("max_daily_loss", "max loss"),
                    ("forward_cost", "forward cost"),
                    ("forward_pnl", "forward pnl"),
                    ("forward_roi", "forward ROI"),
                ],
            ),
            "",
            "## Daily Comparison",
            "",
            md_table(
                daily_delta,
                [
                    ("target_date", "date"),
                    ("rows_running_current_row_risk_soft", "rows"),
                    ("cost_running_current_row_risk_soft", "running cost"),
                    ("pnl_running_current_row_risk_soft", "running pnl"),
                    ("roi_running_current_row_risk_soft", "running ROI"),
                    ("cost_city_fit_soft_overlay_v2", "city-fit cost"),
                    ("pnl_city_fit_soft_overlay_v2", "city-fit pnl"),
                    ("roi_city_fit_soft_overlay_v2", "city-fit ROI"),
                    ("delta_pnl", "delta pnl"),
                ],
                max_rows=80,
            ),
            "",
            "## Running Strategy By Expression",
            "",
            md_table(
                expr_run,
                [
                    ("expression_group", "expression"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("avg_multiplier", "avg city mult"),
                ],
            ),
            "",
            "## City-Fit Overlay By Expression",
            "",
            md_table(
                expr_fit,
                [
                    ("expression_group", "expression"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("avg_multiplier", "avg city mult"),
                ],
            ),
            "",
            "## Route x Source-Bias Regime",
            "",
            md_table(
                route_fit,
                [
                    ("router_route", "route"),
                    ("row_source_bias_regime", "source-bias regime"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("avg_multiplier", "avg mult"),
                ],
                max_rows=80,
            ),
            "",
            "## Day Regime x Source-Bias Regime",
            "",
            md_table(
                regime_fit,
                [
                    ("day_regime", "day_regime"),
                    ("row_source_bias_regime", "source-bias regime"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("avg_multiplier", "avg mult"),
                ],
                max_rows=80,
            ),
            "",
            f"## Forward Since {FORWARD_START}",
            "",
            md_table(
                forward_fit,
                [
                    ("expression_group", "expression"),
                    ("row_source_bias_regime", "source-bias regime"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                    ("avg_multiplier", "avg mult"),
                ],
                max_rows=60,
            ),
            "",
            "## Mechanism Rules",
            "",
            "- For `current_bracket_no`, `hot_underforecast_clean` gets multiplier 1.15; `hot_underforecast_noisy` is reduced to 0.85; cold-overforecast and two-sided noisy are reduced more.",
            "- For `higher_no_d2`, cold-overforecast gets 1.05-1.15; hot-underforecast is reduced to 0.65-0.75.",
            "- For `current_high_yes`, cold-overforecast gets 1.05-1.10; hot-underforecast is reduced.",
            "- These values are mechanism priors, not PnL-fitted thresholds. They should be shadow logged before any live sizing change.",
            "",
            "## Data Notes",
            "",
            "- Row-level city fit uses each row's actual forecast source: `open_meteo_live_ecmwf` -> ECMWF, `gfs_seamless/open_meteo_live_gfs` -> GFS.",
            "- Current running policy is proxied by `router_weighted_cost_usd/router_weighted_pnl_usd` from `regime_routed_expression_router_v3`, matching the row-risk-soft design used by the live runner.",
            "- This is replay on generated research rows, not CLOB fill-realized PnL.",
            "",
            "## Artifacts",
            "",
            f"- Rows: `{(OUT_DIR / 'strategy_city_fit_soft_sizing_rows.csv').relative_to(ROOT)}`",
            f"- Policy summary: `{(OUT_DIR / 'policy_comparison.csv').relative_to(ROOT)}`",
            f"- Daily summary: `{(OUT_DIR / 'daily_policy_comparison.csv').relative_to(ROOT)}`",
            "",
        ]
    )


def build(_: argparse.Namespace) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lookup = load_bias_lookup()
    rows = enrich_rows(load_selected_rows(), lookup)

    pol = policy_summary(rows)
    daily = daily_policy_summary(rows)
    daily_delta = daily_delta_summary(daily)
    expr_run = summarize(rows, ["expression_group"], cost_col="running_cost_usd", pnl_col="running_pnl_usd", prefix="running")
    expr_fit = summarize(rows, ["expression_group"], cost_col="city_fit_cost_usd", pnl_col="city_fit_pnl_usd", prefix="city_fit")
    route_fit = summarize(
        rows,
        ["router_route", "row_source_bias_regime"],
        cost_col="city_fit_cost_usd",
        pnl_col="city_fit_pnl_usd",
        prefix="city_fit",
    )
    regime_fit = summarize(
        rows,
        ["day_regime", "row_source_bias_regime"],
        cost_col="city_fit_cost_usd",
        pnl_col="city_fit_pnl_usd",
        prefix="city_fit",
    )
    forward_fit = summarize(
        rows[rows["target_date"] >= FORWARD_START],
        ["expression_group", "row_source_bias_regime"],
        cost_col="city_fit_cost_usd",
        pnl_col="city_fit_pnl_usd",
        prefix="city_fit",
    )

    rows.to_csv(OUT_DIR / "strategy_city_fit_soft_sizing_rows.csv", index=False)
    pol.to_csv(OUT_DIR / "policy_comparison.csv", index=False)
    daily.to_csv(OUT_DIR / "daily_policy_comparison.csv", index=False)
    daily_delta.to_csv(OUT_DIR / "daily_policy_delta_comparison.csv", index=False)
    expr_run.to_csv(OUT_DIR / "running_expression_summary.csv", index=False)
    expr_fit.to_csv(OUT_DIR / "city_fit_expression_summary.csv", index=False)
    route_fit.to_csv(OUT_DIR / "city_fit_route_source_bias_summary.csv", index=False)
    regime_fit.to_csv(OUT_DIR / "city_fit_day_regime_source_bias_summary.csv", index=False)
    forward_fit.to_csv(OUT_DIR / "city_fit_forward_since_20260621_summary.csv", index=False)

    OUT_MD.write_text(
        render_report(rows, pol, expr_run, expr_fit, route_fit, regime_fit, forward_fit, daily, daily_delta),
        encoding="utf-8",
    )
    return {"rows": len(rows), "report": str(OUT_MD), "out_dir": str(OUT_DIR)}


def main() -> None:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    print(build(args))


if __name__ == "__main__":
    main()
