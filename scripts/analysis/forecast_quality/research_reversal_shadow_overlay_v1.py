#!/usr/bin/env python3
"""Overlay reversal shadow tags on prior regime-routed NO decisions.

This is a practice/research script, not a live runner change.  It answers:
when the original strategy wanted current-bracket NO, what would the same
snapshot have done if tagged as a reversal and bought current-high YES?
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/reversal_shadow_overlay_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-01-reversal-shadow-overlay-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-01-reversal-shadow-overlay-v1.json"

ORIGINAL_NO = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_expression_v1/selected_trade_details.csv"
ROUTER_V3_LIVE_LIKE = ROOT / "docs/analysis/2026-06/generated/regime_routed_expression_router_v3_live_like_entry_v1/trade_details.csv"
EVENT_ROWS = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv"

STAKE_USD = 5.0
RNG_SEED = 20260701


def pct(v: Any) -> str:
    try:
        x = float(v)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"{100 * x:+.1f}%"


def money(v: Any) -> str:
    try:
        x = float(v)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"${x:+.2f}"


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 50) -> str:
    if df.empty:
        return "_No rows._"
    pct_cols = {"shadow_roi", "original_no_roi", "excess_roi", "shadow_ci_low", "shadow_ci_high", "win_rate", "holdout_roi", "recent_roi"}
    money_cols = {"shadow_pnl", "original_no_pnl", "excess_pnl", "max_daily_loss", "pnl_per_day"}
    int_cols = {"rows", "dates", "cities", "losing_days", "roi_le_minus_50_days"}
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df.head(max_rows).iterrows():
        vals: list[str] = []
        for key, _label in cols:
            val = row.get(key, "")
            if key in pct_cols:
                vals.append(pct(val))
            elif key in money_cols:
                vals.append(money(val))
            elif key in int_cols and pd.notna(val):
                vals.append(str(int(val)))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def bootstrap_roi_ci(daily: pd.DataFrame, n_boot: int = 3000) -> tuple[float, float]:
    if len(daily) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(RNG_SEED)
    costs = daily["shadow_cost"].to_numpy(float)
    pnls = daily["shadow_pnl"].to_numpy(float)
    vals: list[float] = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(daily), len(daily))
        cost = costs[idx].sum()
        if cost > 0:
            vals.append(float(pnls[idx].sum() / cost))
    q = np.quantile(vals, [0.025, 0.975])
    return (float(q[0]), float(q[1]))


def load_frame(path: Path, dataset: str) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    df["dataset"] = dataset
    df["target_date"] = df["target_date"].astype(str)
    if "entry_policy" not in df.columns:
        df["entry_policy"] = df.get("selector", "all")
    if "router_expression" not in df.columns:
        df["router_expression"] = df.get("expression", "")
    if "router_route" not in df.columns:
        df["router_route"] = df.get("route_leg", "")
    numeric = [
        "current_yes_ask",
        "current_yes_payoff",
        "current_bracket_no_ask",
        "current_bracket_no_payoff",
        "current_no_ask",
        "forecast_gap_to_running_native",
        "minutes_since_running_max",
        "forecast_peak_delta_hours_local",
    ]
    for col in numeric:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "current_bracket_no_ask" not in df.columns and "current_no_ask" in df.columns:
        df["current_bracket_no_ask"] = df["current_no_ask"]
    return df


def label_rows(df: pd.DataFrame) -> pd.DataFrame:
    current_no_selected = df["expression"].astype(str).eq("current_bracket_no")
    high_no = df["current_bracket_no_ask"].ge(0.70) & df["current_yes_ask"].le(0.50)
    capped_or_low = (
        df["day_regime"].astype(str).isin(["day_forecast_capped", "day_forecast_busted"])
        | df["forecast_gap_to_running_native"].le(1.0)
    )
    pullback = (
        df["intraday_state"].astype(str).eq("pullback_uncertain")
        & df["current_yes_ask"].between(0.50, 0.90, inclusive="both")
        & df["current_bracket_no_ask"].ge(0.40)
    )

    labels: list[pd.DataFrame] = []
    specs = [
        (
            "high_current_no_reverse_current_yes",
            current_no_selected & high_no & capped_or_low,
            "High current NO, cheap current YES, and capped/low-runway PIT state.",
        ),
        (
            "expanded_pullback_uncertain_current_high_yes",
            current_no_selected & pullback,
            "Already printed/pulled-back high where same-bracket hold/revisit can kill NO.",
        ),
    ]
    for label, mask, reason in specs:
        g = df[mask].copy()
        if g.empty:
            continue
        g["shadow_label"] = label
        g["shadow_reason"] = reason
        g["shadow_expression"] = "buy_current_high_yes"
        g["shadow_ask"] = g["current_yes_ask"]
        g["shadow_payoff"] = g["current_yes_payoff"]
        g["shadow_cost"] = STAKE_USD
        g["shadow_pnl"] = g["shadow_payoff"] * (STAKE_USD / g["shadow_ask"]) - STAKE_USD
        g["original_no_ask"] = g["current_bracket_no_ask"]
        g["original_no_payoff"] = g["current_bracket_no_payoff"]
        g["original_no_cost"] = STAKE_USD
        g["original_no_pnl"] = g["original_no_payoff"] * (STAKE_USD / g["original_no_ask"]) - STAKE_USD
        g["excess_pnl"] = g["shadow_pnl"] - g["original_no_pnl"]
        labels.append(g)
    if not labels:
        return pd.DataFrame()
    out = pd.concat(labels, ignore_index=True)
    out = out[
        out["shadow_ask"].between(0.01, 0.99, inclusive="both")
        & out["original_no_ask"].between(0.01, 0.99, inclusive="both")
        & out["shadow_payoff"].notna()
        & out["original_no_payoff"].notna()
    ].copy()
    return out


def first_city_date(df: pd.DataFrame) -> pd.DataFrame:
    keys = ["dataset", "entry_policy", "shadow_label", "target_date", "city"]
    sort_cols = ["dataset", "entry_policy", "shadow_label", "target_date", "city", "decision_snapshot_ts_utc"]
    return df.sort_values(sort_cols).drop_duplicates(keys).copy()


def summarize(df: pd.DataFrame, period: str) -> pd.DataFrame:
    if period == "train":
        g = df[df["target_date"] < "2026-06-13"].copy()
    elif period == "holdout":
        g = df[df["target_date"] >= "2026-06-13"].copy()
    elif period == "recent":
        g = df[df["target_date"] >= "2026-06-21"].copy()
    else:
        g = df.copy()
    rows: list[dict[str, Any]] = []
    group_cols = ["dataset", "entry_policy", "shadow_label"]
    for keys, h in g.groupby(group_cols, dropna=False):
        dataset, entry_policy, label = keys
        daily = h.groupby("target_date", as_index=False).agg(
            shadow_cost=("shadow_cost", "sum"),
            shadow_pnl=("shadow_pnl", "sum"),
            original_no_cost=("original_no_cost", "sum"),
            original_no_pnl=("original_no_pnl", "sum"),
        )
        ci = bootstrap_roi_ci(daily)
        cost = float(h["shadow_cost"].sum())
        original_cost = float(h["original_no_cost"].sum())
        shadow_pnl = float(h["shadow_pnl"].sum())
        original_pnl = float(h["original_no_pnl"].sum())
        rows.append(
            {
                "period": period,
                "dataset": dataset,
                "entry_policy": entry_policy,
                "shadow_label": label,
                "rows": int(len(h)),
                "dates": int(h["target_date"].nunique()),
                "cities": int(h["city"].nunique()),
                "avg_shadow_ask": float(h["shadow_ask"].mean()),
                "avg_original_no_ask": float(h["original_no_ask"].mean()),
                "win_rate": float(h["shadow_payoff"].mean()),
                "shadow_pnl": shadow_pnl,
                "shadow_roi": shadow_pnl / cost if cost else float("nan"),
                "shadow_ci_low": ci[0],
                "shadow_ci_high": ci[1],
                "original_no_pnl": original_pnl,
                "original_no_roi": original_pnl / original_cost if original_cost else float("nan"),
                "excess_pnl": shadow_pnl - original_pnl,
                "excess_roi": shadow_pnl / cost - original_pnl / original_cost if cost and original_cost else float("nan"),
                "losing_days": int((daily["shadow_pnl"] < 0).sum()),
                "roi_le_minus_50_days": int(((daily["shadow_pnl"] / daily["shadow_cost"]) <= -0.5).sum()),
                "max_daily_loss": float(daily["shadow_pnl"].min()),
                "pnl_per_day": shadow_pnl / h["target_date"].nunique() if h["target_date"].nunique() else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def event_matrix_sanity() -> pd.DataFrame:
    """Same labels on the full expression matrix, not restricted to original selected rows."""
    df = load_frame(EVENT_ROWS, "event_matrix")
    df["expression"] = "current_bracket_no"
    labelled = label_rows(df)
    return first_city_date(labelled)


def render_report(summary: pd.DataFrame, details: pd.DataFrame, matrix_details: pd.DataFrame) -> str:
    full = summary[summary["period"].eq("full")].sort_values(["dataset", "entry_policy", "shadow_label"])
    holdout = summary[summary["period"].eq("holdout")].sort_values(["dataset", "entry_policy", "shadow_label"])
    recent = summary[summary["period"].eq("recent")].sort_values(["dataset", "entry_policy", "shadow_label"])
    cols = [
        ("dataset", "dataset"),
        ("entry_policy", "policy"),
        ("shadow_label", "label"),
        ("rows", "rows"),
        ("dates", "dates"),
        ("cities", "cities"),
        ("avg_shadow_ask", "YES ask"),
        ("win_rate", "win"),
        ("shadow_roi", "shadow ROI"),
        ("shadow_ci_low", "CI low"),
        ("shadow_ci_high", "CI high"),
        ("original_no_roi", "orig NO ROI"),
        ("excess_roi", "excess"),
        ("losing_days", "loss days"),
        ("max_daily_loss", "max loss"),
    ]
    prompt = (
        "If original current-NO strategy emits a current_bracket_no candidate, attach zero-notional "
        "`reversal_shadow` fields.  For `high_current_no_reverse_current_yes`: require "
        "current_bracket_no_ask>=0.70, current_high_yes_ask<=0.50, and day_forecast_capped/"
        "day_forecast_busted or forecast_gap_to_running_native<=1.0.  For "
        "`expanded_pullback_uncertain_current_high_yes`: require intraday_state=pullback_uncertain, "
        "current_high_yes_ask in [0.50,0.90], and current_bracket_no_ask>=0.40.  Record opposite "
        "current_high YES ask/payoff; do not place orders."
    )
    lines = [
        "# Reversal Shadow Overlay v1",
        "",
        "Generated: 2026-07-01",
        "",
        "## Practical Prompt",
        "",
        prompt,
        "",
        "## Verdict",
        "",
        "`expanded_pullback_uncertain_current_high_yes` is the sharper practical label on original/current-router rows, but remains shadow-only because selected-row sample is thin.  The broad high-current-NO inverse is useful as a wrong-way diagnostic, not as a standalone trade.",
        "",
        "## Review: Real Issue vs Overfit Risk",
        "",
        "### Real Issues",
        "",
        "- The original current-bracket NO expression has a genuine wrong-way failure mode when the market prices current NO high while PIT weather state is capped or low-runway. On the broad event-matrix denominator, the same rows have current-NO baseline ROI -26.4% versus inverse current-YES ROI +7.4%, so the direction flip is informative even though the inverse itself is not yet strong.",
        "- This should be recorded at the original strategy decision point. The runner must keep the original current-NO candidate and attach zero-notional `reversal_shadow_*` fields with the opposite current-high YES token/market/ask lineage.",
        "- The feature boundary is valid: the tag uses PIT prices and PIT weather state only. It must not use final max, settlement winner, or realized forecast error as a live selector.",
        "",
        "### Overfit / Fragile Parts",
        "",
        "- The +7.4% inverse ROI is not enough for live approval; CI crosses zero and recent slices are weak.",
        "- The thresholds `current_bracket_no_ask>=0.70` and `current_high_yes_ask<=0.50` are useful telemetry cut points, not proven optimal execution thresholds.",
        "- `expanded_pullback_uncertain_current_high_yes` is mechanically cleaner but only 12 city-date rows in the full matrix and 4 rows on selected runner rows. Treat it as a sharper label to monitor, not a trading rule.",
        "- Current-high YES has exact-bracket risk: it wins only if final settlement remains in the current high bracket. Overshoot to d1/d2 can still make the inverse lose.",
        "",
        "### Current Implementation Decision",
        "",
        "Keep the main strategy unchanged. Add/maintain zero-notional `reversal_shadow_*` tags only:",
        "",
        "- `high_current_no_reverse_current_yes` for high current-NO / cheap current-high YES plus capped or low-runway PIT state.",
        "- `expanded_pullback_uncertain_current_high_yes` for pullback-uncertain current-NO rows where the current-high YES is explicitly priced.",
        "",
        "The live runner should never place an order from this tag. It should write `reversal_shadow_live_order_allowed=false`, `reversal_shadow_notional_usd=0`, and enough token/market lineage to settle the shadow expression later.",
        "",
        "## Full Window: Original Selected Rows",
        "",
        md_table(full, cols),
        "",
        "## Holdout",
        "",
        md_table(holdout, cols),
        "",
        "## Recent",
        "",
        md_table(recent, cols),
        "",
        "## Full Matrix Sanity",
        "",
        "Same labels on the broader expression matrix, first city-date per label.  This checks whether selected-row results are just router selection artifacts.",
        "",
        md_table(
            summarize(matrix_details, "full").sort_values(["shadow_label"]),
            cols,
        ),
        "",
        "## Top Shadow Rows",
        "",
        md_table(
            details.sort_values("excess_pnl", ascending=False)[
                [
                    "dataset",
                    "entry_policy",
                    "shadow_label",
                    "city",
                    "target_date",
                    "decision_hour_local",
                    "day_regime",
                    "intraday_state",
                    "running_max_state",
                    "shadow_ask",
                    "shadow_pnl",
                    "original_no_ask",
                    "original_no_pnl",
                    "excess_pnl",
                ]
            ].head(20),
            [
                ("dataset", "dataset"),
                ("entry_policy", "policy"),
                ("shadow_label", "label"),
                ("city", "city"),
                ("target_date", "date"),
                ("decision_hour_local", "hour"),
                ("day_regime", "day"),
                ("intraday_state", "state"),
                ("running_max_state", "runmax"),
                ("shadow_ask", "YES ask"),
                ("shadow_pnl", "YES pnl"),
                ("original_no_ask", "NO ask"),
                ("original_no_pnl", "NO pnl"),
                ("excess_pnl", "excess"),
            ],
            max_rows=20,
        ),
        "",
        "## Artifacts",
        "",
        f"- Details CSV: `{(OUT_DIR / 'shadow_details.csv').relative_to(ROOT)}`",
        f"- Summary CSV: `{(OUT_DIR / 'summary.csv').relative_to(ROOT)}`",
        f"- Script: `scripts/analysis/forecast_quality/research_reversal_shadow_overlay_v1.py`",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frames = [
        load_frame(ORIGINAL_NO, "regime_routed_no_expression_v1"),
        load_frame(ROUTER_V3_LIVE_LIKE, "regime_routed_expression_router_v3_live_like"),
    ]
    labelled = pd.concat([label_rows(f) for f in frames], ignore_index=True)
    picked = first_city_date(labelled)
    matrix_picked = event_matrix_sanity()
    summary = pd.concat([summarize(picked, p) for p in ["full", "train", "holdout", "recent"]], ignore_index=True)
    picked.to_csv(OUT_DIR / "shadow_details.csv", index=False)
    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    matrix_picked.to_csv(OUT_DIR / "matrix_shadow_details.csv", index=False)
    payload = {
        "details_rows": int(len(picked)),
        "matrix_details_rows": int(len(matrix_picked)),
        "summary_rows": int(len(summary)),
        "date_min": str(picked["target_date"].min()) if not picked.empty else None,
        "date_max": str(picked["target_date"].max()) if not picked.empty else None,
        "artifacts": {
            "details": str((OUT_DIR / "shadow_details.csv").relative_to(ROOT)),
            "summary": str((OUT_DIR / "summary.csv").relative_to(ROOT)),
            "matrix_details": str((OUT_DIR / "matrix_shadow_details.csv").relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUT_MD.write_text(render_report(summary, picked, matrix_picked), encoding="utf-8")
    print(f"wrote {OUT_MD.relative_to(ROOT)}")
    print(f"wrote {(OUT_DIR / 'summary.csv').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
