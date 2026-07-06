#!/usr/bin/env python3
"""Evaluate regime current-NO escape-margin filters and salvage overlay boundary.

Research only. This script does not modify live policy.
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
IN_SELECTED = ROOT / "docs/analysis/2026-07/generated/regime_time_route_expansion_v1/selected_rows.csv"
IN_ACCEPTED = ROOT / "runtime/weather_edge_v1/regime_routed_no_tiny_live_v1/accepted_candidates.jsonl"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/regime_margin_salvage_v1"
OUT_SUMMARY_CSV = OUT_DIR / "margin_sweep_summary.csv"
OUT_LIVE_CSV = OUT_DIR / "live_accepted_margin_effect.csv"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-06-regime-margin-salvage-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-06-regime-margin-salvage-v1.md"

FORWARD_START = "2026-06-21"
FORWARD_END = "2026-06-26"
THRESHOLDS = [0.0, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        val = float(value)
        return val if math.isfinite(val) else None
    return value


def pct(value: Any) -> str:
    if value is None:
        return "NA"
    val = float(value)
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def money(value: Any) -> str:
    if value is None:
        return "NA"
    val = float(value)
    if not math.isfinite(val):
        return "NA"
    return f"${val:+.2f}"


def parse_bracket_high(value: Any) -> float:
    text = str(value)
    if "-" in text:
        return float(text.split("-")[-1])
    return float(text.replace("+", ""))


def add_margin(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["current_no_escape_threshold_native_replay"] = out["current_bracket"].map(parse_bracket_high) + 0.5
    out["current_no_escape_margin_native_replay"] = (
        pd.to_numeric(out["forecast_max_native"], errors="coerce")
        - out["current_no_escape_threshold_native_replay"]
    )
    return out


def apply_margin_gate(frame: pd.DataFrame, threshold: float) -> pd.Series:
    is_fresh = frame["router_route"].eq("fresh_runway_current_no")
    margin = pd.to_numeric(frame["current_no_escape_margin_native_replay"], errors="coerce")
    return (~is_fresh) | margin.gt(threshold)


def summarize(frame: pd.DataFrame, *, threshold: float, window: str, current_index: set[int]) -> dict[str, Any]:
    kept = frame[apply_margin_gate(frame, threshold)].copy()
    removed = frame[~frame.index.isin(kept.index) & frame.index.isin(current_index)].copy()
    cost = float(kept["exec_cost_usd"].sum()) if not kept.empty else 0.0
    pnl = float(kept["exec_pnl_usd"].sum()) if not kept.empty else 0.0
    return {
        "window": window,
        "min_margin_native_strict_gt": threshold,
        "rows": int(len(kept)),
        "dates": int(kept["target_date"].nunique()) if not kept.empty else 0,
        "cities": int(kept["city"].nunique()) if not kept.empty else 0,
        "fresh_rows": int(kept["router_route"].eq("fresh_runway_current_no").sum()) if not kept.empty else 0,
        "additional_removed_vs_current": int(len(removed)),
        "removed_wins": int(pd.to_numeric(removed["router_payoff"], errors="coerce").sum()) if not removed.empty else 0,
        "removed_pnl_usd": float(removed["exec_pnl_usd"].sum()) if not removed.empty else 0.0,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
    }


def render_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        vals: list[str] = []
        for col in cols:
            val = row.get(col)
            if col == "roi":
                vals.append(pct(val))
            elif col.endswith("_usd"):
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
            else:
                vals.append("" if val is None else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def load_live_accepted() -> pd.DataFrame:
    if not IN_ACCEPTED.exists():
        return pd.DataFrame()
    rows = [json.loads(line) for line in IN_ACCEPTED.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        return pd.DataFrame()
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        is_fresh = row.get("route_leg") == "fresh_runway_current_no"
        margin = row.get("current_no_escape_margin_native") if is_fresh else None
        out: dict[str, Any] = {
            "city": row.get("city"),
            "target_date": row.get("target_date"),
            "bracket": row.get("bracket"),
            "route_leg": row.get("route_leg"),
            "forecast_max_native": row.get("forecast_max_native"),
            "current_no_escape_threshold_native": row.get("current_no_escape_threshold_native"),
            "current_no_escape_margin_native": margin,
            "live_order_shares": row.get("live_order_shares"),
        }
        for threshold in THRESHOLDS:
            out[f"kept_gt_{threshold:g}"] = (not is_fresh) or (margin is not None and float(margin) > threshold)
        out_rows.append(out)
    return pd.DataFrame(out_rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    selected = pd.read_csv(IN_SELECTED)
    selected = add_margin(selected)
    baseline = selected[
        selected["policy"].eq("baseline_fresh_capped_10_14")
        & selected["exec_gate_pass"].astype(bool)
        & selected["router_payoff"].notna()
    ].copy()

    # Current live code already uses strict margin > 0 for fresh current-NO.
    current = baseline[apply_margin_gate(baseline, 0.0)].copy()
    current_index = set(current.index)
    base_cost = float(current["exec_cost_usd"].sum())
    base_pnl = float(current["exec_pnl_usd"].sum())
    base_roi = base_pnl / base_cost if base_cost else None

    rows: list[dict[str, Any]] = []
    for window, frame in [
        ("all_settled", baseline),
        (f"forward_{FORWARD_START}_to_{FORWARD_END}", baseline[baseline["target_date"].between(FORWARD_START, FORWARD_END)]),
    ]:
        window_current = frame[apply_margin_gate(frame, 0.0)]
        window_current_index = set(window_current.index)
        window_base_cost = float(window_current["exec_cost_usd"].sum())
        window_base_pnl = float(window_current["exec_pnl_usd"].sum())
        window_base_roi = window_base_pnl / window_base_cost if window_base_cost else None
        for threshold in THRESHOLDS:
            summary = summarize(frame, threshold=threshold, window=window, current_index=window_current_index)
            summary["delta_pnl_vs_current_usd"] = (
                None if window_base_pnl is None or summary["pnl_usd"] is None else summary["pnl_usd"] - window_base_pnl
            )
            summary["delta_roi_vs_current"] = (
                None
                if window_base_roi is None or summary["roi"] is None
                else summary["roi"] - window_base_roi
            )
            rows.append(summary)

    summary_df = pd.DataFrame(rows)
    summary_df.to_csv(OUT_SUMMARY_CSV, index=False)
    live_df = load_live_accepted()
    live_df.to_csv(OUT_LIVE_CSV, index=False)

    all_rows = summary_df[summary_df["window"].eq("all_settled")].to_dict("records")
    forward_rows = summary_df[summary_df["window"].ne("all_settled")].to_dict("records")
    focus_thresholds = {0.0, 0.25, 0.5, 1.0}
    md_all = [r for r in all_rows if float(r["min_margin_native_strict_gt"]) in focus_thresholds]
    md_forward = [r for r in forward_rows if float(r["min_margin_native_strict_gt"]) in focus_thresholds]

    payload = {
        "generated_at_utc": now_utc(),
        "input_selected_rows": str(IN_SELECTED.relative_to(ROOT)),
        "current_policy": {
            "baseline_policy": "baseline_fresh_capped_10_14",
            "exec_gate": "exec_gate_current",
            "fresh_current_no_margin_gate": "current live equivalent: current_no_escape_margin_native > 0",
            "current_rows": int(len(current)),
            "current_dates": int(current["target_date"].nunique()),
            "current_cost_usd": base_cost,
            "current_pnl_usd": base_pnl,
            "current_roi": base_roi,
            "current_max_target_date": str(current["target_date"].max()),
        },
        "today_live_accepted_rows": int(len(live_df)),
        "today_live_threshold_effect": live_df.to_dict("records") if not live_df.empty else [],
        "salvage_overlay_verdict": {
            "status": "not_yet_backtested",
            "reason": "manual residual sells are not in the regime live order journal; historical PIT exit replay must use observation report time plus contemporaneous executable orderbook bid.",
            "beijing_user_reported_sell": {
                "buy_no_price": 0.33,
                "buy_no_shares": 8.0,
                "sell_no_price": 0.06,
                "sell_no_shares": 8.0,
                "recovered_usd": 0.48,
                "realized_loss_usd_before_fees": -2.16,
            },
        },
        "verdict": "margin_filter_positive_in_full_sample_but_forward_fragile; salvage_exit_requires_separate_pit_overlay_replay",
    }
    OUT_JSON.write_text(json.dumps(finite(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    live_rows = live_df.to_dict("records") if not live_df.empty else []
    md = "\n".join(
        [
            "# Regime Margin And Salvage V1",
            "",
            "## Conclusion",
            "",
            "Current `fresh_runway_current_no` already requires `current_no_escape_margin_native > 0`. Raising it would have blocked today's Chengdu at any threshold above `0.1` native units; `>0.25` blocks Chengdu only among current accepted live rows, while `>0.5` also blocks CapeTown's 0.5-margin row.",
            "",
            "Historical replay is mixed: stricter margins improve the all-settled point estimate, but the small forward window gets worse because two low-margin winners are removed. This is not enough to promote a live hard filter; the cleaner action is shadow-tag `margin_bucket` and require more fresh forward evidence before changing live.",
            "",
            "Residual/salvage exit is a separate overlay. Today's Beijing manual sell recovered cash, but the regime journal does not contain that sell, and historical proof needs PIT official-report trigger plus executable exit bid replay. Do not fold it into regime live until the residual-capture research supplies that exit model.",
            "",
            "## All Settled Margin Sweep",
            "",
            render_table(
                md_all,
                [
                    "min_margin_native_strict_gt",
                    "rows",
                    "additional_removed_vs_current",
                    "removed_wins",
                    "removed_pnl_usd",
                    "pnl_usd",
                    "roi",
                    "delta_pnl_vs_current_usd",
                    "delta_roi_vs_current",
                ],
            ),
            "",
            "## Forward Margin Sweep",
            "",
            render_table(
                md_forward,
                [
                    "min_margin_native_strict_gt",
                    "rows",
                    "additional_removed_vs_current",
                    "removed_wins",
                    "removed_pnl_usd",
                    "pnl_usd",
                    "roi",
                    "delta_pnl_vs_current_usd",
                    "delta_roi_vs_current",
                ],
            ),
            "",
            "## Current Live Accepted Rows",
            "",
            render_table(
                live_rows,
                [
                    "city",
                    "target_date",
                    "bracket",
                    "route_leg",
                    "current_no_escape_margin_native",
                    "kept_gt_0.25",
                    "kept_gt_0.5",
                    "kept_gt_1",
                ],
            ),
            "",
            "## Data Notes",
            "",
            f"- Generated at `{payload['generated_at_utc']}` after Mac market sync and `run_stack.sh` rebuild.",
            "- Evidence layer: `regime_time_route_expansion_v1/selected_rows.csv` for historical live-like replay, plus `accepted_candidates.jsonl` for current live accepted rows.",
            "- Margin is measured in market native units: C markets use °C, F markets use °F.",
            "- `>` is strict because the current runner uses `margin.gt(min_current_no_escape_margin_native)`.",
            "- Salvage overlay is intentionally not scored here because manual UI exits are outside the canonical regime order journal.",
            "",
        ]
    )
    OUT_MD.write_text(md, encoding="utf-8")
    print(json.dumps(finite(payload), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
