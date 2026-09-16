#!/usr/bin/env python3
"""Diagnose why the frozen regime-routed v3 candidate failed after 2026-06-20."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
INPUT = (
    ROOT
    / "docs/analysis/2026-06/generated/"
    / "regime_routed_expression_router_v3_live_like_entry_v1/trade_details.csv"
)
OUT_DIR = (
    ROOT
    / "docs/analysis/2026-06/generated/"
    / "regime_routed_v3_frozen_forward_failure_diagnosis_20260629"
)
REPORT = (
    ROOT
    / "docs/analysis/2026-06/"
    / "2026-06-29-regime-routed-v3-frozen-forward-failure-diagnosis.md"
)

ROUTES = [
    "fresh_runway_current_no",
    "capped_d2_no",
    "false_fade_reheat_current_no",
    "cheap_stale_tail_current_no",
]
FEATURES = [
    "router_ask",
    "forecast_error_native",
    "remaining_heat_native",
    "forecast_peak_delta_hours_local",
    "relative_humidity_pct",
    "wind_speed_kt",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
]


def pct(x: float | None) -> str:
    if x is None or pd.isna(x):
        return "NA"
    return f"{x * 100:+.1f}%"


def money(x: float | None) -> str:
    if x is None or pd.isna(x):
        return "NA"
    return f"${x:+.2f}"


def summarize(df: pd.DataFrame) -> dict:
    cost = float(df["router_cost_usd"].sum())
    pnl = float(df["router_pnl_usd"].sum())
    wcost = float(df["router_weighted_cost_usd"].sum())
    wpnl = float(df["router_weighted_pnl_usd"].sum())
    return {
        "rows": int(len(df)),
        "dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "wins": int(df["router_payoff"].sum()),
        "win_rate": float(df["router_payoff"].mean()) if len(df) else None,
        "avg_ask": float(df["router_ask"].mean()) if len(df) else None,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "weighted_cost_usd": wcost,
        "weighted_pnl_usd": wpnl,
        "weighted_roi": wpnl / wcost if wcost else None,
    }


def summarize_by_route(df: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    for route, g in df.groupby("router_route", sort=True):
        item = summarize(g)
        item["router_route"] = route
        rows.append(item)
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(INPUT)
    raw["target_date"] = pd.to_datetime(raw["target_date"]).dt.date.astype(str)

    df = raw[
        (raw["entry_policy"] == "fixed_noon_priority")
        & raw["router_cost_usd"].notna()
        & raw["router_route"].isin(ROUTES)
        & (raw["target_date"] <= "2026-06-26")
    ].copy()

    splits: dict[str, dict] = {}
    for split in ["2026-06-21", "2026-06-23", "2026-06-24"]:
        train = df[df["target_date"] < split]
        fwd = df[df["target_date"] >= split]
        splits[split] = {
            "train": summarize(train),
            "forward": summarize(fwd),
            "train_by_route": summarize_by_route(train),
            "forward_by_route": summarize_by_route(fwd),
        }

    train_621 = df[df["target_date"] < "2026-06-21"]
    fwd_621 = df[df["target_date"] >= "2026-06-21"]
    feature_shift = []
    for col in FEATURES:
        if col not in df.columns:
            continue
        feature_shift.append(
            {
                "feature": col,
                "train_mean": float(train_621[col].mean()),
                "forward_mean": float(fwd_621[col].mean()),
                "delta": float(fwd_621[col].mean() - train_621[col].mean()),
            }
        )

    daily = []
    for date, g in fwd_621.groupby("target_date", sort=True):
        item = summarize(g)
        item["target_date"] = date
        daily.append(item)

    result = {
        "input": str(INPUT.relative_to(ROOT)),
        "denominator": (
            "fixed_noon_priority rows from the live-like entry sensitivity output, "
            "limited to no_pullback_yes routes and target_date <= 2026-06-26"
        ),
        "date_range": {
            "min": str(df["target_date"].min()),
            "max": str(df["target_date"].max()),
        },
        "routes": ROUTES,
        "splits": splits,
        "feature_shift_2026_06_21": feature_shift,
        "daily_forward_2026_06_21": daily,
        "verdict": "inconclusive_shadow_only",
    }

    (OUT_DIR / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    split = result["splits"]["2026-06-21"]
    lines = [
        "# Regime-Routed V3 Frozen Forward Failure Diagnosis",
        "",
        "## Conclusion",
        "",
        (
            "6/21+ 的失败不能归因于单纯的训练/验证日期身份，也不能靠全局 entry timing "
            "改动直接解释。固定 6/21 前已经会选出的 live-like candidate "
            "`fixed_noon_priority + no_pullback_yes` 后，6/21-6/26 forward 仍为负；"
            "坏点主要集中在 route 机制层，尤其是 `fresh_runway_current_no` 和 "
            "`capped_d2_no`。"
        ),
        "",
        "Verdict: `inconclusive_shadow_only`，live_ready=`False`。",
        "",
        "## Evidence Snapshot",
        "",
        f"- Source: `{result['input']}`",
        f"- Denominator: {result['denominator']}",
        f"- Settled target dates used: `{result['date_range']['min']}`..`{result['date_range']['max']}`",
        "",
        "## Frozen Split At 2026-06-21",
        "",
        "| window | rows | dates | cities | win_rate | avg_ask | ROI | weighted ROI |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ["train", "forward"]:
        s = split[name]
        lines.append(
            f"| {name} | {s['rows']} | {s['dates']} | {s['cities']} | "
            f"{pct(s['win_rate'])} | {s['avg_ask']:.3f} | {pct(s['roi'])} | {pct(s['weighted_roi'])} |"
        )

    lines += [
        "",
        "## Route Breakdown",
        "",
        "| window | route | rows | dates | win_rate | avg_ask | ROI | weighted ROI |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for window, key in [("train", "train_by_route"), ("forward", "forward_by_route")]:
        for r in split[key]:
            lines.append(
                f"| {window} | `{r['router_route']}` | {r['rows']} | {r['dates']} | "
                f"{pct(r['win_rate'])} | {r['avg_ask']:.3f} | {pct(r['roi'])} | {pct(r['weighted_roi'])} |"
            )

    lines += [
        "",
        "## Feature Drift",
        "",
        "| feature | train mean | forward mean | delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in feature_shift:
        lines.append(
            f"| `{row['feature']}` | {row['train_mean']:.3f} | "
            f"{row['forward_mean']:.3f} | {row['delta']:+.3f} |"
        )

    lines += [
        "",
        "## Forward Daily",
        "",
        "| target_date | rows | win_rate | ROI | weighted ROI | PnL | weighted PnL |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for d in daily:
        lines.append(
            f"| {d['target_date']} | {d['rows']} | {pct(d['win_rate'])} | {pct(d['roi'])} | "
            f"{pct(d['weighted_roi'])} | {money(d['pnl_usd'])} | {money(d['weighted_pnl_usd'])} |"
        )

    lines += [
        "",
        "## Interpretation",
        "",
        "- `entry timing` 是必须继续研究的执行层问题，因为旧 `best_ask` 有后视择时；但去掉 `best_ask` 后，6/21+ 仍弱，说明它不是唯一根因。",
        "- 6/21 前训练样本里四个 route 都为正；6/21+ forward 中 `fresh_runway_current_no` 和 `capped_d2_no` 同时转负，且 forward 的 ask 更高、forecast overestimate 更大、remaining heat 更低、running max 更老。",
        "- 6/23+ 看起来修复只是 split-sensitive：少量 `cheap_stale_tail` / `false_fade` 抵消了损失，但核心 fresh route 仍弱。",
        "- 下一步应做 route-specific entry timing candidate menu，再用 nested walk-forward 选择；不能只看一个全局 ROI 或挑 6/23+ 作为新切分。",
    ]

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
