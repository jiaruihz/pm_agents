#!/usr/bin/env python3
"""Sensitivity test for regime-routed NO wind and settlement escape features.

This is not a new live rule.  It keeps the existing regime-routed NO selected
trade denominator and asks what changes if two mechanism features are used as
soft sizing inputs:

- settlement escape margin: does forecast clear the payoff-relevant integer
  settlement bucket, not just the running max?
- wind/mixing risk: high wind is not a direction by itself, but it makes thin
  settlement margins less trustworthy.

It also reports `soft_wind_only`, a deliberately light wind-speed-only haircut,
so wind risk can be evaluated without letting settlement margin dominate.
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_DIR = ROOT / "scripts/analysis/reheat_risk"
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))

import research_regime_routed_no_expression_v1 as base  # noqa: E402


SELECTED = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_expression_v1/selected_trade_details.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_wind_escape_sensitivity_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_VARIANTS = OUT_DIR / "variant_summary.csv"
OUT_WIND = OUT_DIR / "wind_regime_summary.csv"
OUT_MARGIN = OUT_DIR / "margin_bucket_summary.csv"
OUT_DAILY = OUT_DIR / "daily_summary.csv"
OUT_CASES = OUT_DIR / "thin_windy_cases.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-26-regime-routed-no-wind-escape-sensitivity-v1.md"

MAIN_VARIANT = "routed_capped_d2_no_relaxed70_best_ask"
BASE_NOTIONAL_USD = 5.0
MIN_ORDER_SHARES = 5.0


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def parse_bracket(value: Any) -> tuple[float | None, float | None]:
    if pd.isna(value):
        return None, None
    text = str(value).replace("°", "").replace("F", "").replace("C", "").strip()
    nums = re.findall(r"-?\d+(?:\.\d+)?", text)
    if not nums:
        return None, None
    low = float(nums[0])
    high = float(nums[-1]) if len(nums) > 1 else low
    if high < low:
        low, high = high, low
    return low, high


def settlement_margin(row: pd.Series) -> float:
    forecast = pd.to_numeric(row.get("forecast_max_native"), errors="coerce")
    if not math.isfinite(float(forecast)):
        return math.nan
    route = str(row.get("route_leg") or "")
    if route == "runway_current_no":
        _low, high = parse_bracket(row.get("current_bracket"))
        if high is None:
            return math.nan
        # Market brackets are inclusive integer settlement buckets.  If the
        # current bracket is 35C or 68-69F, the upward escape threshold is 36C
        # or 70F respectively.
        return float(forecast) - (float(high) + 1.0)
    if route.startswith("capped_"):
        bracket_col = "d2_no_bracket" if "d2" in route else "d1_no_bracket"
        low, _high = parse_bracket(row.get(bracket_col))
        if low is None:
            return math.nan
        # For capped higher-NO routes the desired safety is staying below the
        # bought higher bracket's lower edge.
        return float(low) - float(forecast)
    return math.nan


def native_delta_to_f(frame: pd.DataFrame, values: pd.Series) -> pd.Series:
    unit = frame["unit"].astype(str).str.upper()
    return np.where(unit.eq("C"), pd.to_numeric(values, errors="coerce") * 9.0 / 5.0, pd.to_numeric(values, errors="coerce"))


def add_overlay(frame: pd.DataFrame) -> pd.DataFrame:
    out = base.add_soft_weights(frame).copy()
    out["settlement_margin_native"] = out.apply(settlement_margin, axis=1)
    out["settlement_margin_f_equiv"] = native_delta_to_f(out, out["settlement_margin_native"])
    margin_f = pd.to_numeric(out["settlement_margin_f_equiv"], errors="coerce")
    wind = pd.to_numeric(out["wind_speed_kt"], errors="coerce")

    # Continuous-ish haircut: thin or negative payoff margin lowers size, but
    # does not automatically delete the signal.
    out["escape_margin_multiplier"] = ((margin_f + 0.5) / 2.5).clip(0.25, 1.0).fillna(0.70)
    out["wind_only_multiplier"] = np.select([wind.ge(18), wind.ge(10)], [0.80, 0.95], default=1.00)
    out["wind_escape_multiplier"] = np.select(
        [
            wind.ge(18) & margin_f.lt(1.0),
            wind.ge(18) & margin_f.lt(2.0),
            wind.ge(18),
            wind.ge(10) & margin_f.lt(1.0),
            wind.ge(10),
        ],
        [0.55, 0.75, 0.90, 0.80, 0.95],
        default=1.00,
    )
    out["soft_wind_only"] = (
        pd.to_numeric(out["soft_balanced"], errors="coerce")
        * pd.to_numeric(out["wind_only_multiplier"], errors="coerce")
    ).clip(0.0, 1.0)
    out["soft_wind_escape"] = (
        pd.to_numeric(out["soft_balanced"], errors="coerce")
        * pd.to_numeric(out["escape_margin_multiplier"], errors="coerce")
        * pd.to_numeric(out["wind_escape_multiplier"], errors="coerce")
    ).clip(0.0, 1.0)
    out["margin_bucket"] = pd.cut(
        margin_f,
        [-np.inf, -1.0, 0.0, 1.0, 2.0, np.inf],
        labels=["<-1F", "-1..0F", "0..1F", "1..2F", "2F+"],
    ).astype(str)
    return out


def summarize(frame: pd.DataFrame, weight_col: str) -> dict[str, Any]:
    clean = frame[frame["payoff"].notna()].copy()
    if clean.empty:
        return {
            "weight_policy": weight_col,
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "exec_rows": 0,
            "exec_dates": 0,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "roi": None,
            "hit_rate": None,
            "avg_weight": None,
        }
    weight = pd.to_numeric(clean[weight_col], errors="coerce").fillna(0.0).clip(lower=0.0)
    ask = pd.to_numeric(clean["ask"], errors="coerce")
    shares = BASE_NOTIONAL_USD * weight / ask
    executed = shares.ge(MIN_ORDER_SHARES)
    cost = float((pd.to_numeric(clean["stake_cost_usd"], errors="coerce") * weight).sum())
    pnl = float((pd.to_numeric(clean["stake_profit_usd"], errors="coerce") * weight).sum())
    exec_cost = float((pd.to_numeric(clean.loc[executed, "stake_cost_usd"], errors="coerce") * weight[executed]).sum())
    exec_pnl = float((pd.to_numeric(clean.loc[executed, "stake_profit_usd"], errors="coerce") * weight[executed]).sum())
    return {
        "weight_policy": weight_col,
        "rows": int(len(clean)),
        "dates": int(clean["target_date"].nunique()),
        "cities": int(clean["city"].nunique()),
        "exec_rows": int(executed.sum()),
        "exec_dates": int(clean.loc[executed, "target_date"].nunique()),
        "cost_usd": round(cost, 6),
        "pnl_usd": round(pnl, 6),
        "roi": pnl / cost if cost else None,
        "exec_cost_usd": round(exec_cost, 6),
        "exec_pnl_usd": round(exec_pnl, 6),
        "exec_roi": exec_pnl / exec_cost if exec_cost else None,
        "hit_rate": float(pd.to_numeric(clean["payoff"], errors="coerce").mean()),
        "avg_weight": float(weight.mean()),
        "avg_exec_weight": float(weight[executed].mean()) if executed.any() else None,
    }


def summarize_group(frame: pd.DataFrame, group_col: str, weight_col: str) -> pd.DataFrame:
    rows = []
    for key, group in frame.groupby(group_col, dropna=False):
        row = summarize(group, weight_col)
        row[group_col] = str(key)
        rows.append(row)
    return pd.DataFrame(rows)


def weighted_daily(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for date, group in frame.groupby("target_date"):
        row = {
            "target_date": str(date),
            "rows": int(len(group)),
            "cities": int(group["city"].nunique()),
            "windy_rows": int(group["wind_regime"].astype(str).eq("windy_mixing_noise").sum()),
        }
        for col in ["soft_balanced", "soft_wind_only", "soft_wind_escape"]:
            stats = summarize(group, col)
            row[f"{col}_exec_rows"] = stats["exec_rows"]
            row[f"{col}_exec_cost_usd"] = stats["exec_cost_usd"]
            row[f"{col}_exec_pnl_usd"] = stats["exec_pnl_usd"]
            row[f"{col}_exec_roi"] = stats["exec_roi"]
            row[f"{col}_cost_usd"] = stats["cost_usd"]
            row[f"{col}_pnl_usd"] = stats["pnl_usd"]
            row[f"{col}_roi"] = stats["roi"]
        rows.append(row)
    return pd.DataFrame(rows)


def md_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        vals = []
        for col in cols:
            val = row.get(col)
            if isinstance(val, float):
                if col in {"roi", "hit_rate", "avg_weight", "avg_exec_weight"} or col.endswith("_roi"):
                    vals.append(pct(val))
                elif "usd" in col:
                    vals.append(f"{val:+.2f}")
                else:
                    vals.append(f"{val:.3f}")
            elif val is None:
                vals.append("NA")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(SELECTED, low_memory=False)
    selected = raw[raw["variant"].eq(MAIN_VARIANT)].copy()
    enriched = add_overlay(selected)

    summary_rows = [
        summarize(enriched, "full_size"),
        summarize(enriched, "soft_balanced"),
        summarize(enriched, "soft_wind_only"),
        summarize(enriched, "soft_wind_escape"),
    ]
    variants = pd.DataFrame(summary_rows)
    wind = pd.concat(
        [
            summarize_group(enriched, "wind_regime", "soft_balanced").assign(weight_policy="soft_balanced"),
            summarize_group(enriched, "wind_regime", "soft_wind_only").assign(weight_policy="soft_wind_only"),
            summarize_group(enriched, "wind_regime", "soft_wind_escape").assign(weight_policy="soft_wind_escape"),
        ],
        ignore_index=True,
    )
    margin = pd.concat(
        [
            summarize_group(enriched, "margin_bucket", "soft_balanced").assign(weight_policy="soft_balanced"),
            summarize_group(enriched, "margin_bucket", "soft_wind_only").assign(weight_policy="soft_wind_only"),
            summarize_group(enriched, "margin_bucket", "soft_wind_escape").assign(weight_policy="soft_wind_escape"),
        ],
        ignore_index=True,
    )
    daily = weighted_daily(enriched)
    thin_windy = enriched[
        enriched["wind_regime"].astype(str).eq("windy_mixing_noise")
        & pd.to_numeric(enriched["settlement_margin_f_equiv"], errors="coerce").lt(1.0)
    ].copy()
    case_cols = [
        "target_date",
        "city",
        "decision_hour_local",
        "route_leg",
        "expression",
        "current_bracket",
        "d2_no_bracket",
        "ask",
        "payoff",
        "stake_profit_usd",
        "unit",
        "running_native",
        "forecast_max_native",
        "settlement_margin_native",
        "settlement_margin_f_equiv",
        "wind_speed_kt",
        "wind_regime",
        "soft_balanced",
        "soft_wind_escape",
    ]
    thin_windy = thin_windy[[col for col in case_cols if col in thin_windy.columns]].sort_values(
        ["target_date", "city"]
    )

    variants.to_csv(OUT_VARIANTS, index=False)
    wind.to_csv(OUT_WIND, index=False)
    margin.to_csv(OUT_MARGIN, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    thin_windy.to_csv(OUT_CASES, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "regime_routed_no_wind_escape_sensitivity_v1",
        "input": str(SELECTED.relative_to(ROOT)),
        "variant": MAIN_VARIANT,
        "base_notional_usd": BASE_NOTIONAL_USD,
        "min_order_shares": MIN_ORDER_SHARES,
        "rows": int(len(enriched)),
        "date_min": str(enriched["target_date"].min()) if len(enriched) else None,
        "date_max": str(enriched["target_date"].max()) if len(enriched) else None,
        "summaries": summary_rows,
        "thin_windy_rows": int(len(thin_windy)),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "variant_summary": str(OUT_VARIANTS.relative_to(ROOT)),
            "wind_summary": str(OUT_WIND.relative_to(ROOT)),
            "margin_summary": str(OUT_MARGIN.relative_to(ROOT)),
            "daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "thin_windy_cases": str(OUT_CASES.relative_to(ROOT)),
            "report_md": str(OUT_MD.relative_to(ROOT)),
        },
        "boundary": {
            "interpretation": "sensitivity_only_not_live_rule",
            "full_size": "original selected rows at fixed $5, no soft sizing",
            "soft_balanced": "existing soft policy recomputed from current research code",
            "soft_wind_only": "soft_balanced multiplied by a light wind-speed-only haircut: >=18kt 0.80, >=10kt 0.95",
            "soft_wind_escape": "soft_balanced multiplied by settlement escape margin and wind/mixing haircut",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    top_wind_rows = (
        wind.sort_values(["wind_regime", "weight_policy"])[
            [
                "wind_regime",
                "weight_policy",
                "rows",
                "dates",
                "exec_rows",
                "cost_usd",
                "pnl_usd",
                "roi",
                "exec_cost_usd",
                "exec_pnl_usd",
                "exec_roi",
                "hit_rate",
                "avg_weight",
            ]
        ]
        .to_dict("records")
    )
    margin_rows = (
        margin.sort_values(["margin_bucket", "weight_policy"])[
            [
                "margin_bucket",
                "weight_policy",
                "rows",
                "dates",
                "exec_rows",
                "cost_usd",
                "pnl_usd",
                "roi",
                "exec_cost_usd",
                "exec_pnl_usd",
                "exec_roi",
                "hit_rate",
                "avg_weight",
            ]
        ]
        .to_dict("records")
    )
    text = "\n".join(
        [
            "# Regime-Routed NO Wind/Escape Sensitivity V1",
            "",
            f"Generated: `{payload['generated_at_utc']}`",
            "",
            "## Verdict",
            "",
            "This is a sensitivity replay, not a live approval.  It keeps the existing `routed_capped_d2_no_relaxed70_best_ask` denominator and asks whether wind/mixing risk and settlement-grid escape margin should be soft sizing features.",
            "",
            "Main read: `windy_mixing_noise` means `wind_speed_kt >= 18`.  It is not bullish or bearish by itself; it makes near-integer forecast edges less reliable.  Therefore it should interact with settlement margin, not act as a standalone city/day veto.",
            "",
            "## Same-Denominator Summary",
            "",
            md_table(
                summary_rows,
                [
                    "weight_policy",
                    "rows",
                    "dates",
                    "cities",
                    "exec_rows",
                    "exec_dates",
                    "cost_usd",
                    "pnl_usd",
                    "roi",
                    "exec_cost_usd",
                    "exec_pnl_usd",
                    "exec_roi",
                    "hit_rate",
                    "avg_weight",
                ],
            ),
            "",
            "## Wind Regime",
            "",
            md_table(
                top_wind_rows,
                [
                    "wind_regime",
                    "weight_policy",
                    "rows",
                    "dates",
                    "exec_rows",
                    "cost_usd",
                    "pnl_usd",
                    "roi",
                    "exec_cost_usd",
                    "exec_pnl_usd",
                    "exec_roi",
                    "hit_rate",
                    "avg_weight",
                ],
            ),
            "",
            "## Settlement Margin Buckets",
            "",
            md_table(
                margin_rows,
                [
                    "margin_bucket",
                    "weight_policy",
                    "rows",
                    "dates",
                    "exec_rows",
                    "cost_usd",
                    "pnl_usd",
                    "roi",
                    "exec_cost_usd",
                    "exec_pnl_usd",
                    "exec_roi",
                    "hit_rate",
                    "avg_weight",
                ],
            ),
            "",
            "## Boundary",
            "",
            "- `exec_rows` is an approximation of the current live minimum-share rule: `$5 * weight / ask >= 5 shares`.",
            "- The overlay is deliberately soft.  It does not delete all thin-margin trades; it reduces notional when the forecast is close to the payoff-relevant integer bucket, especially under high wind.",
            "- This does not satisfy the full three-gate live standard.  It is a mechanism check to decide what to add to the live/replay feature builder next.",
            "",
        ]
    )
    OUT_MD.write_text(text, encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
