#!/usr/bin/env python3
"""Re-gate the "climbing NO" narrow state on the forecast peak-clock runway.

User thesis (2026-06-22): on a day whose daily high is in the afternoon, when we
are still well before the forecast peak and the temperature is climbing, buy the
NO of the *current* running-max bracket, betting it gets punched through.

This reads the already-materialised opportunity-grain rows produced by
``research_current_yes_no_side_overround_ev_v1.py`` (current-YES v8 replay joined
to real current-bracket NO ask + reheat factory forecast peak clock).  It does
NOT recompute fills/PnL; it slices the same settled denominator and runs the
three gates on a pre-committed narrow state.

Pre-committed PRIMARY hypothesis (declared before seeing any CI):
    afternoon-peak day (forecast_peak_hour_local in 13..19)
    AND forecast peak still >= 2h ahead (peak_hour - decision_hour >= 2)
    -> buy current-bracket NO.

The remaining user filters (just-crossed new high, 1h/3h warming, forecast max >
bracket upper) are reported as a nested ladder for transparency only; the gate
verdict is judged on the PRIMARY slice alone.  Runway-threshold sensitivity is
reported so the primary is not a knife-edge selection.
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
ENRICHED = ROOT / "docs/analysis/2026-06/generated/current_yes_no_side_overround_ev_v1/enriched_rows.csv"
SRC_SUMMARY = ROOT / "docs/analysis/2026-06/generated/current_yes_no_side_overround_ev_v1/summary.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_climbing_no_peak_runway_v1"
OUT_JSON = OUT_DIR / "summary.json"

SEED = 20260622
BOOTSTRAP_REPS = 5000


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def block_bootstrap_roi(df: pd.DataFrame, profit_col: str, cost_col: str, reps: int = BOOTSTRAP_REPS) -> dict[str, Any]:
    clean = df[df[cost_col].fillna(0) > 0].copy()
    dates = sorted(clean["target_date"].dropna().unique())
    if len(dates) < 3 or clean.empty:
        return {"ci_low": None, "ci_high": None, "reps": 0, "active_dates": len(dates)}
    grouped = {d: (float(g[profit_col].sum()), float(g[cost_col].sum())) for d, g in clean.groupby("target_date")}
    rng = np.random.default_rng(SEED)
    vals: list[float] = []
    for _ in range(reps):
        draw = rng.choice(dates, size=len(dates), replace=True)
        profit = sum(grouped[d][0] for d in draw)
        cost = sum(grouped[d][1] for d in draw)
        if cost > 0:
            vals.append(profit / cost)
    if not vals:
        return {"ci_low": None, "ci_high": None, "reps": 0, "active_dates": len(dates)}
    return {
        "ci_low": float(np.quantile(vals, 0.025)),
        "ci_high": float(np.quantile(vals, 0.975)),
        "reps": len(vals),
        "active_dates": len(dates),
    }


def summarize(df: pd.DataFrame, name: str) -> dict[str, Any]:
    if df.empty:
        return {"slice": name, "n": 0}
    no_cost = float(df["no_ask"].sum())
    no_profit = float(df["no_profit_per_share"].sum())
    yes_cost = float(df["yes_current_ask"].sum())
    yes_profit = float(df["yes_profit_per_share"].sum())
    no_ci = block_bootstrap_roi(df, "no_profit_per_share", "no_ask")
    return {
        "slice": name,
        "n": int(len(df)),
        "active_dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "sample_start": str(df["target_date"].min()),
        "sample_end": str(df["target_date"].max()),
        "avg_no_ask": float(df["no_ask"].mean()),
        "avg_overround_ask": float(df["overround_ask"].mean()),
        "no_win_rate": float(df["label_no_wins"].mean()),
        "no_edge_pp": float(df["no_edge_pp"].mean()),
        "no_roi": no_profit / no_cost if no_cost else None,
        "no_roi_ci_low": no_ci["ci_low"],
        "no_roi_ci_high": no_ci["ci_high"],
        "yes_roi": yes_profit / yes_cost if yes_cost else None,
        "yes_win_rate": float(df["label_yes_wins"].mean()),
    }


def split_forward(df: pd.DataFrame) -> dict[str, Any]:
    """Re-derive a slice-local train/holdout by target_date (last 30% = holdout)."""
    dates = sorted(df["target_date"].dropna().unique())
    if len(dates) < 4:
        return {"train": {"n": int(len(df))}, "holdout": {"n": 0}, "note": "too few dates for forward split"}
    split_idx = max(1, int(len(dates) * 0.7))
    train_dates = set(dates[:split_idx])
    train = df[df["target_date"].isin(train_dates)]
    holdout = df[~df["target_date"].isin(train_dates)]
    return {
        "split_date": str(dates[split_idx]),
        "train": summarize(train, "train"),
        "holdout": summarize(holdout, "holdout"),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(ENRICHED)
    df = df[df["contains_running_bool"].astype(str).str.lower().isin(["true", "1"]) & df["label_no_wins"].notna()].copy()
    df["peak_runway_h"] = df["factory_forecast_peak_hour_local"] - df["decision_hour_local"]
    df["fcst_gt_bracket_high"] = df["factory_forecast_max_f"] > df["bracket_high"]

    afternoon_peak = df["factory_forecast_peak_hour_local"].between(13, 19)
    runway2 = df["peak_runway_h"] >= 2
    new_high = df["factory_minutes_since_running_max"] <= 30
    warming = (df["d_tmpf_1h"] > 0) & (df["d_tmpf_3h"] > 0.5)
    fcst_gt = df["fcst_gt_bracket_high"]

    primary = df[afternoon_peak & runway2].copy()

    ladder = {
        "L0_afternoon_peak": summarize(df[afternoon_peak], "afternoon_peak"),
        "L1_primary_runway_ge_2h": summarize(primary, "afternoon_peak + runway>=2h [PRIMARY]"),
        "L2_plus_new_high_le_30m": summarize(df[afternoon_peak & runway2 & new_high], "+ new_high<=30m"),
        "L3_plus_warming_1h_3h": summarize(df[afternoon_peak & runway2 & new_high & warming], "+ warming 1h&3h"),
        "L4_plus_forecast_gt_bracket_high": summarize(
            df[afternoon_peak & runway2 & new_high & warming & fcst_gt], "+ forecast_max>bracket_high"
        ),
    }

    runway_sensitivity = {
        f"runway_ge_{thr}h": summarize(df[afternoon_peak & (df["peak_runway_h"] >= thr)], f"runway>={thr}h")
        for thr in (1.0, 1.5, 2.0, 2.5, 3.0)
    }

    no_ask_within_primary = {}
    for lo, hi, lab in [(0.0, 0.6, "<0.60"), (0.6, 0.8, "[0.60,0.80)"), (0.8, 1.01, ">=0.80")]:
        sl = primary[(primary["no_ask"] >= lo) & (primary["no_ask"] < hi)]
        no_ask_within_primary[lab] = summarize(sl, f"primary no_ask {lab}")

    forward = split_forward(primary)

    overall = ladder["L1_primary_runway_ge_2h"]
    holdout = forward.get("holdout", {})
    sig_pass = (
        overall.get("no_roi_ci_low") is not None
        and overall["no_roi_ci_low"] > 0
        and overall.get("no_roi_ci_high") is not None
        and overall["no_roi_ci_high"] > 0
    )
    baseline_pass = sig_pass and (overall.get("no_roi") or 0) > 0
    forward_pass = holdout.get("no_roi") is not None and holdout["no_roi"] > 0
    low_sample = overall.get("active_dates", 0) < 10 or overall.get("n", 0) < 30

    if low_sample:
        level = "inconclusive (low_sample)"
    elif sig_pass and baseline_pass and forward_pass:
        level = "shadow_candidate"  # forward here is pseudo-OOS within history, not live OOS
    else:
        level = "inconclusive"

    src = json.loads(SRC_SUMMARY.read_text()) if SRC_SUMMARY.exists() else {}
    result = {
        "generated_at_utc": now_utc(),
        "thesis": "afternoon-peak day + forecast peak still >=2h ahead -> buy current-bracket NO (betting bracket gets punched through)",
        "denominator": "current-YES v8 replay settled rows with current-bracket NO ask (same as overround_ev_v1)",
        "data_snapshot_inherited_from": str(SRC_SUMMARY.relative_to(ROOT)),
        "inherited_db_mtime_local": src.get("data_self_check", {}).get("db_mtime_local"),
        "inherited_fact_built_at_utc": src.get("data_self_check", {}).get("fact_trades_max_built_at_utc"),
        "inherited_clob_gate_pass": src.get("clob_gate", {}).get("gate_pass"),
        "decision_hour_floor_note": "denominator decision_hour_local min is 13:00; literal 'near noon' (11-12) is NOT in this opportunity set. 'runway>=2h' tests the *intent* (well before peak), not the literal clock.",
        "primary_three_gate": {
            "significance": "PASS" if sig_pass else "FAIL",
            "baseline": "PASS" if baseline_pass else "FAIL",
            "forward": "PASS" if forward_pass else "FAIL",
            "level": level,
            "low_sample": low_sample,
        },
        "primary_forward_split": forward,
        "ladder_transparency": ladder,
        "runway_threshold_sensitivity": runway_sensitivity,
        "no_ask_buckets_within_primary": no_ask_within_primary,
        "multiple_testing_note": "Many nested slices were inspected before committing. PRIMARY = runway>=2h was chosen by mechanism (user filter #2), not by best ROI cell. No multiplicity correction applied; treat single-slice CI as optimistic.",
    }
    OUT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({
        "out_json": str(OUT_JSON.relative_to(ROOT)),
        "primary": {k: overall.get(k) for k in ("n", "active_dates", "no_roi", "no_roi_ci_low", "no_roi_ci_high", "no_win_rate", "avg_no_ask")},
        "holdout_no_roi": holdout.get("no_roi"),
        "three_gate": result["primary_three_gate"],
        "runway_sensitivity_roi": {k: v.get("no_roi") for k, v in runway_sensitivity.items()},
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
