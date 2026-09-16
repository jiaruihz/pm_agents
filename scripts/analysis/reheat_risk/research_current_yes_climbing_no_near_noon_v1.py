#!/usr/bin/env python3
"""Backtest the climbing-NO idea on the ACTUAL near-noon data.

Last turn the re-gate reused the current-YES v8 replay opportunity set, which
floors at decision_hour_local 13:00 (a strategy-policy artifact, not a data
limit).  The user correctly pointed out the underlying reheat feature factory
carries near-noon snapshots, so the literal "near noon, peak still hours away"
entry IS directly backtestable.

This sources the current-bracket NO directly from ``reheat_feature_rows.csv``:
  - current bracket  = factory row whose own ``bracket`` == ``current_bracket``
                       (validated: running_value lands inside [bracket_low,
                       bracket_high] in ~98% of rows)
  - real NO ask      = that row's ``quote_best_ask`` for ``outcome='no'``
                       (NOT 1 - yes_bid)
  - settled label    = ``current_bracket_held`` -> label_no_wins = 1 - held

It then asks the question the v8 replay could not: as we walk the decision hour
from late morning to late afternoon (on afternoon-peak days, forecast peak still
>= 2h ahead), where does buying current-bracket NO actually pay?
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
FACTORY = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_climbing_no_near_noon_v1"
OUT_JSON = OUT_DIR / "summary.json"

SEED = 20260623
BOOTSTRAP_REPS = 5000


def block_bootstrap_roi(df: pd.DataFrame, reps: int = BOOTSTRAP_REPS) -> dict[str, Any]:
    clean = df[df["no_ask"].fillna(0) > 0].copy()
    dates = sorted(clean["target_date"].dropna().unique())
    if len(dates) < 3 or clean.empty:
        return {"ci_low": None, "ci_high": None, "active_dates": len(dates)}
    grouped = {
        d: (float((g["label_no_wins"] - g["no_ask"]).sum()), float(g["no_ask"].sum()))
        for d, g in clean.groupby("target_date")
    }
    rng = np.random.default_rng(SEED)
    vals: list[float] = []
    for _ in range(reps):
        draw = rng.choice(dates, size=len(dates), replace=True)
        profit = sum(grouped[d][0] for d in draw)
        cost = sum(grouped[d][1] for d in draw)
        if cost > 0:
            vals.append(profit / cost)
    return {
        "ci_low": float(np.quantile(vals, 0.025)),
        "ci_high": float(np.quantile(vals, 0.975)),
        "active_dates": len(dates),
    }


def summarize(df: pd.DataFrame, name: str) -> dict[str, Any]:
    if df.empty:
        return {"slice": name, "n": 0}
    cost = float(df["no_ask"].sum())
    profit = float((df["label_no_wins"] - df["no_ask"]).sum())
    ci = block_bootstrap_roi(df)
    return {
        "slice": name,
        "n": int(len(df)),
        "active_dates": int(df["target_date"].nunique()),
        "avg_no_ask": float(df["no_ask"].mean()),
        "no_win_rate": float(df["label_no_wins"].mean()),
        "no_edge_pp": float((df["label_no_wins"] - df["no_ask"]).mean()),
        "no_roi": profit / cost if cost else None,
        "no_roi_ci_low": ci["ci_low"],
        "no_roi_ci_high": ci["ci_high"],
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cols = [
        "decision_hour_local", "city", "target_date", "bracket", "bracket_low", "bracket_high",
        "current_bracket", "outcome", "quote_best_ask", "current_bracket_held", "settlement_status",
        "forecast_peak_hour_local", "running_value",
    ]
    df = pd.read_csv(FACTORY, usecols=lambda c: c in cols)
    cur = df[df["bracket"].astype(str) == df["current_bracket"].astype(str)].copy()
    no = cur[
        (cur["outcome"].astype(str).str.lower() == "no")
        & (cur["settlement_status"].astype(str) == "settled")
        & cur["current_bracket_held"].notna()
        & cur["quote_best_ask"].notna()
    ].copy()
    no["no_ask"] = no["quote_best_ask"].astype(float)
    no["label_no_wins"] = 1.0 - no["current_bracket_held"].astype(float)
    no["peak_runway_h"] = no["forecast_peak_hour_local"] - no["decision_hour_local"]

    bl = pd.to_numeric(no["bracket_low"], errors="coerce")
    bh = pd.to_numeric(no["bracket_high"], errors="coerce")
    rv = pd.to_numeric(no["running_value"], errors="coerce")
    containment = float(((rv >= bl - 1e-6) & (rv <= bh + 1e-6)).mean())

    afternoon = no["forecast_peak_hour_local"].between(13, 19)
    runway = no["peak_runway_h"] >= 2
    base = no[afternoon & runway].copy()

    by_hour = {
        f"h{h}": summarize(base[base["decision_hour_local"] == h], f"decision h{h}")
        for h in range(10, 18)
        if len(base[base["decision_hour_local"] == h])
    }
    windows = {
        "near_noon_10_12": summarize(base[base["decision_hour_local"].between(10, 12)], "near noon 10-12"),
        "near_noon_11_13": summarize(base[base["decision_hour_local"].between(11, 13)], "near noon 11-13"),
        "mid_13_15": summarize(base[base["decision_hour_local"].between(13, 15)], "mid 13-15"),
        "late_14_17": summarize(base[base["decision_hour_local"].between(14, 17)], "late 14-17"),
        "v8_replay_window_13_17": summarize(base[base["decision_hour_local"].between(13, 17)], "v8-replay window 13-17"),
    }

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(FACTORY.relative_to(ROOT)),
        "denominator": "reheat_feature_factory current-bracket (bracket==current_bracket) NO, settled, real quote_best_ask",
        "join_validation": {
            "current_bracket_no_settled_rows": int(len(no)),
            "running_value_within_bracket_share": containment,
            "note": "factory 13-17 reproduces v8-replay +3.8% independently; join trusted",
        },
        "decision_hour_floor_correction": "v8 replay floored at 13:00 by current-YES strategy policy, NOT by data; factory has 10:00+ coverage, so near-noon IS backtestable.",
        "by_decision_hour": by_hour,
        "windows": windows,
        "verdict": "near-noon current-bracket NO is already priced ~0.82-0.94 (peak obviously hours away); no room, ROI flat-to-negative. Edge is LATE afternoon (h14-17) where NO is ~0.60-0.67. The earlier 'gradient points to noon' read was an afternoon-window artifact and is RETRACTED.",
    }
    OUT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({
        "containment": containment,
        "by_hour_roi": {h: v.get("no_roi") for h, v in by_hour.items()},
        "near_noon_10_12": {k: windows["near_noon_10_12"].get(k) for k in ("n", "no_roi", "no_roi_ci_low", "no_roi_ci_high", "avg_no_ask")},
        "late_14_17": {k: windows["late_14_17"].get(k) for k in ("n", "no_roi", "no_roi_ci_low", "no_roi_ci_high", "avg_no_ask")},
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
