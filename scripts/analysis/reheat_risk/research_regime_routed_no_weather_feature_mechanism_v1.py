#!/usr/bin/env python3
"""Weather-feature mechanism review for regime-routed NO.

This uses the same selected denominator as wind-context v2 and explains how
the existing regime/soft-weight stack actually uses weather inputs.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_wind_context_v2/selected_with_wind_context.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_weather_feature_mechanism_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_POLICY = OUT_DIR / "policy_ablation.csv"
OUT_GROUPS = OUT_DIR / "feature_group_summary.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-26-regime-routed-no-weather-feature-mechanism-v1.md"

BASE_NOTIONAL_USD = 5.0
MIN_ORDER_SHARES = 5.0


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def md_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        values = []
        for col in cols:
            value = row.get(col)
            if isinstance(value, float):
                if col.endswith("roi") or col in {"hit_rate", "avg_weight"}:
                    values.append(pct(value))
                else:
                    values.append(f"{value:+.2f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def summarize(frame: pd.DataFrame, weight_col: str) -> dict[str, Any]:
    clean = frame[frame["payoff"].notna()].copy()
    weight = pd.to_numeric(clean[weight_col], errors="coerce").fillna(0).clip(lower=0)
    ask = pd.to_numeric(clean["ask"], errors="coerce")
    executed = (BASE_NOTIONAL_USD * weight / ask).ge(MIN_ORDER_SHARES)
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
        "hit_rate": float(pd.to_numeric(clean["payoff"], errors="coerce").mean()),
        "cost_usd": round(cost, 6),
        "pnl_usd": round(pnl, 6),
        "roi": pnl / cost if cost else None,
        "exec_cost_usd": round(exec_cost, 6),
        "exec_pnl_usd": round(exec_pnl, 6),
        "exec_roi": exec_pnl / exec_cost if exec_cost else None,
        "avg_weight": float(weight.mean()) if len(weight) else None,
    }


def group_summary(frame: pd.DataFrame, group_col: str, *, min_rows: int = 8) -> pd.DataFrame:
    rows = []
    for value, group in frame.groupby(group_col, dropna=False):
        if len(group) < min_rows:
            continue
        full = summarize(group, "full_size")
        soft = summarize(group, "soft_balanced")
        rows.append(
            {
                "feature": group_col,
                "bucket": str(value),
                "rows": full["rows"],
                "dates": full["dates"],
                "cities": full["cities"],
                "hit_rate": full["hit_rate"],
                "full_roi": full["roi"],
                "soft_exec_rows": soft["exec_rows"],
                "soft_exec_roi": soft["exec_roi"],
                "avg_soft_weight": soft["avg_weight"],
            }
        )
    return pd.DataFrame(rows)


def add_bins(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["rh_bucket"] = pd.cut(
        pd.to_numeric(out["relative_humidity_pct"], errors="coerce"),
        bins=[-np.inf, 45, 60, 75, 85, np.inf],
        labels=["rh_le45", "rh_45_60", "rh_60_75", "rh_75_85", "rh_gt85"],
    ).astype(str)
    out["dewpoint_dep_bucket"] = pd.cut(
        pd.to_numeric(out["dewpoint_depression_f"], errors="coerce"),
        bins=[-np.inf, 8, 15, 25, np.inf],
        labels=["dewdep_lt8", "dewdep_8_15", "dewdep_15_25", "dewdep_gt25"],
    ).astype(str)
    out["sky_bucket"] = pd.cut(
        pd.to_numeric(out["sky_cover_code"], errors="coerce"),
        bins=[-np.inf, 1, 2, 3, np.inf],
        labels=["sky_clear_or_few", "sky_scattered", "sky_broken", "sky_overcast"],
    ).astype(str)
    out["wind_speed_bucket"] = pd.cut(
        pd.to_numeric(out["wind_speed_kt"], errors="coerce"),
        bins=[-np.inf, 5, 10, 18, np.inf],
        labels=["wind_lt5", "wind_5_10", "wind_10_18", "wind_ge18"],
    ).astype(str)
    out["trend1_bucket"] = pd.cut(
        pd.to_numeric(out["temp_trend_1h_f"], errors="coerce"),
        bins=[-np.inf, -1, 0, 1, 3, np.inf],
        labels=["t1_cooling", "t1_flat_down", "t1_flat_up", "t1_warming", "t1_fast_warming"],
    ).astype(str)
    out["trend3_bucket"] = pd.cut(
        pd.to_numeric(out["temp_trend_3h_f"], errors="coerce"),
        bins=[-np.inf, -2, 0, 2, 5, np.inf],
        labels=["t3_cooling", "t3_flat_down", "t3_slow_warming", "t3_warming", "t3_fast_warming"],
    ).astype(str)
    return out


def main() -> int:
    if not DETAILS.exists():
        raise FileNotFoundError(f"missing input: {DETAILS}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(DETAILS, low_memory=False)
    df = add_bins(raw)
    weather = pd.to_numeric(df["weather_multiplier"], errors="coerce").replace(0, np.nan)
    df["soft_without_weather_multiplier"] = (pd.to_numeric(df["soft_balanced"], errors="coerce") / weather).clip(0, 1).fillna(0)
    df["soft_wind_context"] = pd.to_numeric(df.get("soft_wind_context"), errors="coerce").fillna(0)
    df["soft_wind_only"] = pd.to_numeric(df.get("soft_wind_only"), errors="coerce").fillna(0)

    policies = pd.DataFrame(
        [
            summarize(df, "full_size"),
            summarize(df, "soft_without_weather_multiplier"),
            summarize(df, "soft_balanced"),
            summarize(df, "soft_wind_only"),
            summarize(df, "soft_wind_context"),
        ]
    )
    group_cols = [
        "moisture_cloud_regime",
        "wind_regime",
        "coastal_flow_state",
        "rh_bucket",
        "dewpoint_dep_bucket",
        "sky_bucket",
        "wind_speed_bucket",
        "trend1_bucket",
        "trend3_bucket",
        "intraday_state",
        "running_max_state",
    ]
    groups = pd.concat([group_summary(df, col) for col in group_cols], ignore_index=True)
    groups = groups.sort_values(["feature", "rows"], ascending=[True, False])
    policies.to_csv(OUT_POLICY, index=False)
    groups.to_csv(OUT_GROUPS, index=False)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": int(len(df)),
        "date_min": str(df["target_date"].min()),
        "date_max": str(df["target_date"].max()),
        "cities": int(df["city"].nunique()),
        "policy_ablation": policies.to_dict("records"),
        "outputs": {
            "policy_ablation": str(OUT_POLICY.relative_to(ROOT)),
            "feature_group_summary": str(OUT_GROUPS.relative_to(ROOT)),
            "report_md": str(OUT_MD.relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")

    notable = (
        groups.assign(abs_roi_gap=lambda x: (pd.to_numeric(x["full_roi"], errors="coerce") - pd.to_numeric(policies.iloc[0]["roi"])).abs())
        .sort_values(["abs_roi_gap", "rows"], ascending=[False, False])
        .head(20)
        .drop(columns=["abs_roi_gap"])
        .to_dict("records")
    )
    text = "\n".join(
        [
            "# Regime-Routed NO Weather Feature Mechanism V1",
            "",
            f"Generated: `{payload['generated_at_utc']}`",
            "",
            "## Verdict",
            "",
            "现有版本不是没用风、云层、露点和升温动量，但用法偏粗：这些字段先变成 `intraday_state`、`moisture_cloud_regime`、`wind_regime`、`running_max_state`，再进入 `soft_balanced`。真钱 sizing 里真正直接扣权重的是 `weather_multiplier`，目前主要惩罚 humid city family、mature_fade 和 unknown weather；它没有把风向、海风、具体云层/露点强度做成连续权重。",
            "",
            "所以结论是：基础字段已经在，但天气机制层还不够完整。风向/沿海上下文适合作为 shared feature + shadow sizing 先记录；云层/湿度/露点/风速更应该做连续校准，而不是继续加一堆 hard gate。",
            "",
            f"Denominator: `{payload['rows']}` rows, `{payload['cities']}` cities, `{payload['date_min']}`..`{payload['date_max']}`.",
            "",
            "## Policy Ablation",
            "",
            md_table(
                policies.to_dict("records"),
                [
                    "weight_policy",
                    "rows",
                    "dates",
                    "cities",
                    "exec_rows",
                    "exec_dates",
                    "hit_rate",
                    "roi",
                    "exec_roi",
                    "avg_weight",
                ],
            ),
            "",
            "## Most Informative Slices",
            "",
            md_table(
                notable,
                [
                    "feature",
                    "bucket",
                    "rows",
                    "dates",
                    "cities",
                    "hit_rate",
                    "full_roi",
                    "soft_exec_rows",
                    "soft_exec_roi",
                    "avg_soft_weight",
                ],
            ),
            "",
            "## How Current Model Uses These Inputs",
            "",
            "- `temp_trend_1h_f/temp_trend_3h_f` feed `intraday_state`; this is already live-parity gated.",
            "- `relative_humidity_pct/sky_cover_code/dewpoint_depression_f` feed `moisture_cloud_regime`; direct sizing impact is currently weak unless the weather label is unknown.",
            "- `wind_speed_kt` feeds `wind_regime`; direct sizing impact is currently weak unless the weather label is unknown.",
            "- `wind_dir_deg/wind_sector/geo_context/coastal_flow_state` are now shared context features and runner shadow telemetry, not live sizing.",
            "",
            "## Boundary",
            "",
            "- This report is explanatory, not a new trading rule.",
            "- The next clean step is frozen replay with live-available fields only, then decide whether any continuous weather context belongs in `soft_balanced`.",
            "",
        ]
    )
    OUT_MD.write_text(text, encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
