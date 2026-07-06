#!/usr/bin/env python3
"""Evaluate regime routed decision-hour and shadow-route expansions.

Research replay only. This does not modify live policy.
"""

from __future__ import annotations

import json
import math
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_regime_routed_expression_router_v3_live_like_entry_v1 as live_entry_v1  # noqa: E402
import research_regime_routed_no_expression_v1 as expression_v1  # noqa: E402
import research_regime_routed_v3_route_specific_timing_wf_v1 as timing_v1  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/regime_time_route_expansion_v1"
OUT_SUMMARY = OUT_DIR / "summary.json"
OUT_POLICY = OUT_DIR / "policy_summary.csv"
OUT_DAILY = OUT_DIR / "daily_summary.csv"
OUT_SELECTED = OUT_DIR / "selected_rows.csv"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-06-regime-time-route-expansion-v1.md"

FORWARD_START = "2026-06-21"
SETTLED_END = "2026-06-26"
BASE_NOTIONAL_USD = 9.0
MIN_ORDER_SHARES = 5.0
MIN_SOFT_WEIGHT_TO_ASK_RATIO = 1.0

ROUTES = {
    "fresh_capped": {"fresh_runway_current_no", "capped_d2_no"},
    "false_fade_only": {"false_fade_reheat_current_no"},
    "cheap_stale_only": {"cheap_stale_tail_current_no"},
    "shadow_tail_false": {"false_fade_reheat_current_no", "cheap_stale_tail_current_no"},
    "no_pullback_yes": {
        "fresh_runway_current_no",
        "capped_d2_no",
        "false_fade_reheat_current_no",
        "cheap_stale_tail_current_no",
    },
}

POLICIES = [
    ("baseline_fresh_capped_10_14", "fresh_capped", {10, 11, 12, 13, 14}),
    ("fresh_capped_extend_10_17", "fresh_capped", {10, 11, 12, 13, 14, 15, 16, 17}),
    ("false_fade_only_10_14", "false_fade_only", {10, 11, 12, 13, 14}),
    ("false_fade_only_10_17", "false_fade_only", {10, 11, 12, 13, 14, 15, 16, 17}),
    ("cheap_stale_only_10_14", "cheap_stale_only", {10, 11, 12, 13, 14}),
    ("cheap_stale_only_10_17", "cheap_stale_only", {10, 11, 12, 13, 14, 15, 16, 17}),
    ("shadow_tail_false_10_17", "shadow_tail_false", {10, 11, 12, 13, 14, 15, 16, 17}),
    ("no_pullback_yes_10_17", "no_pullback_yes", {10, 11, 12, 13, 14, 15, 16, 17}),
]


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
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def money(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"${val:+.2f}"


def num(frame: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[col], errors="coerce")


def build_all_routed_for_hours(hours: set[int]) -> pd.DataFrame:
    original_hours = expression_v1.DECISION_HOURS
    original_stable_hours = expression_v1._stable.DECISION_HOURS
    try:
        expression_v1.DECISION_HOURS = set(hours)
        expression_v1._stable.DECISION_HOURS = set(hours)
        return timing_v1.build_all_routed_candidates()
    finally:
        expression_v1.DECISION_HOURS = original_hours
        expression_v1._stable.DECISION_HOURS = original_stable_hours


def select_policy(all_routed: pd.DataFrame, *, label: str, route_set: str, hours: set[int]) -> pd.DataFrame:
    allowed_routes = ROUTES[route_set]
    work = all_routed.copy()
    work = work[work["router_route"].isin(allowed_routes)].copy()
    work = work[num(work, "decision_hour_local").isin(list(hours))].copy()
    if work.empty:
        return work
    work = work[timing_v1.route_row_allowed(work, "route_price_disciplined_v1")].copy()
    if work.empty:
        return work
    work = timing_v1.entry_policy_rank(work, "route_price_disciplined_v1")
    selected = (
        work.sort_values(["target_date", "city", "entry_rank_1", "entry_rank_2", "entry_rank_3", "decision_hour_num"])
        .drop_duplicates(["target_date", "city"], keep="first")
        .reset_index(drop=True)
    )
    selected = timing_v1.add_row_sizing(selected)
    selected["policy"] = label
    selected["route_set"] = route_set
    selected["hour_set"] = f"{min(hours)}-{max(hours)}"
    return add_costs(selected)


def add_costs(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    ask = num(out, "router_ask")
    payoff = num(out, "router_payoff")
    weight = num(out, "row_risk_soft_v1").fillna(0).clip(0, 1)
    ask_size = num(out, "router_ask_size")

    out["research_weight"] = weight
    out["research_cost_usd"] = BASE_NOTIONAL_USD * weight
    out["research_shares"] = out["research_cost_usd"] / ask
    out["research_pnl_usd"] = (payoff - ask) * out["research_shares"]
    out["soft_weight_to_ask_ratio"] = weight / ask
    out["exec_soft_shares"] = out["research_shares"]
    out["exec_shares"] = np.minimum(out["exec_soft_shares"], ask_size)
    out.loc[out["exec_shares"].isna(), "exec_shares"] = out.loc[out["exec_shares"].isna(), "exec_soft_shares"]
    out["exec_cost_usd"] = out["exec_shares"] * ask
    out["exec_pnl_usd"] = (payoff - ask) * out["exec_shares"]
    out["exec_gate_pass"] = (
        ask.ge(expression_v1.ASK_MIN)
        & out["soft_weight_to_ask_ratio"].ge(MIN_SOFT_WEIGHT_TO_ASK_RATIO)
        & out["exec_shares"].ge(MIN_ORDER_SHARES)
    )
    return out


def date_bootstrap_roi(frame: pd.DataFrame, cost_col: str, pnl_col: str, *, seed: int = 20260706) -> tuple[float | None, float | None]:
    clean = frame[frame["router_payoff"].notna()].copy()
    if clean.empty or clean["target_date"].nunique() < 3:
        return None, None
    daily = clean.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    rng = np.random.default_rng(seed)
    idx = np.arange(len(daily))
    costs = daily["cost"].to_numpy(dtype=float)
    pnls = daily["pnl"].to_numpy(dtype=float)
    rois: list[float] = []
    for _ in range(5000):
        sample = rng.choice(idx, size=len(idx), replace=True)
        cost = float(costs[sample].sum())
        if cost:
            rois.append(float(pnls[sample].sum() / cost))
    if not rois:
        return None, None
    low, high = np.quantile(rois, [0.025, 0.975])
    return float(low), float(high)


def summarize_slice(frame: pd.DataFrame, *, policy: str, window: str, cost_col: str, pnl_col: str, sizing: str) -> dict[str, Any]:
    clean = frame[frame["router_payoff"].notna()].copy()
    cost = float(clean[cost_col].sum()) if not clean.empty else 0.0
    pnl = float(clean[pnl_col].sum()) if not clean.empty else 0.0
    low, high = date_bootstrap_roi(clean, cost_col, pnl_col)
    return {
        "policy": policy,
        "window": window,
        "sizing": sizing,
        "rows": int(len(clean)),
        "dates": int(clean["target_date"].nunique()) if not clean.empty else 0,
        "cities": int(clean["city"].nunique()) if not clean.empty else 0,
        "wins": int(num(clean, "router_payoff").sum()) if not clean.empty else 0,
        "win_rate": float(num(clean, "router_payoff").mean()) if not clean.empty else None,
        "avg_ask": float(num(clean, "router_ask").mean()) if not clean.empty else None,
        "avg_weight": float(num(clean, "research_weight").mean()) if not clean.empty else None,
        "avg_hour": float(num(clean, "decision_hour_local").mean()) if not clean.empty else None,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "roi_ci_low": low,
        "roi_ci_high": high,
    }


def policy_summary(selected: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for policy, group in selected.groupby("policy", dropna=False):
        windows = [
            ("all_settled", group["router_payoff"].notna()),
            (f"forward_{FORWARD_START}_to_{SETTLED_END}", group["target_date"].between(FORWARD_START, SETTLED_END)),
        ]
        for window, mask in windows:
            g = group[mask].copy()
            rows.append(summarize_slice(g, policy=str(policy), window=window, cost_col="research_cost_usd", pnl_col="research_pnl_usd", sizing="research_row_soft"))
            rows.append(summarize_slice(g[g["exec_gate_pass"]], policy=str(policy), window=window, cost_col="exec_cost_usd", pnl_col="exec_pnl_usd", sizing="exec_gate_current"))
    return pd.DataFrame(rows).sort_values(["sizing", "window", "policy"]).reset_index(drop=True)


def add_incremental_summary(summary: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    baseline = selected[selected["policy"].eq("baseline_fresh_capped_10_14")].copy()
    extended = selected[selected["policy"].eq("fresh_capped_extend_10_17")].copy()
    baseline_keys = set(zip(baseline["target_date"].astype(str), baseline["city"].astype(str)))
    incremental = extended[
        [
            (str(date), str(city)) not in baseline_keys
            for date, city in zip(extended["target_date"], extended["city"])
        ]
    ].copy()
    incremental["policy"] = "incremental_fresh_capped_added_by_15_17"
    if incremental.empty:
        return summary
    extra = policy_summary(pd.concat([selected, incremental], ignore_index=True))
    return extra


def daily_summary(selected: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (policy, target_date, sizing), group in selected.groupby(["policy", "target_date", "route_set"], dropna=False):
        clean = group[group["router_payoff"].notna()].copy()
        if clean.empty:
            continue
        for mode, cost_col, pnl_col, subset in [
            ("research_row_soft", "research_cost_usd", "research_pnl_usd", clean),
            ("exec_gate_current", "exec_cost_usd", "exec_pnl_usd", clean[clean["exec_gate_pass"]]),
        ]:
            if subset.empty:
                continue
            cost = float(subset[cost_col].sum())
            pnl = float(subset[pnl_col].sum())
            rows.append(
                {
                    "policy": policy,
                    "target_date": target_date,
                    "route_set": sizing,
                    "sizing": mode,
                    "rows": int(len(subset)),
                    "wins": int(num(subset, "router_payoff").sum()),
                    "win_rate": float(num(subset, "router_payoff").mean()),
                    "avg_ask": float(num(subset, "router_ask").mean()),
                    "avg_hour": float(num(subset, "decision_hour_local").mean()),
                    "cost_usd": cost,
                    "pnl_usd": pnl,
                    "roi": pnl / cost if cost else None,
                    "route_mix": ",".join(f"{k}:{v}" for k, v in subset["router_route"].value_counts().sort_index().items()),
                    "loss_cities": ",".join(
                        subset.loc[num(subset, "router_payoff").eq(0), "city"].astype(str).sort_values().tolist()
                    ),
                }
            )
    return pd.DataFrame(rows).sort_values(["sizing", "policy", "target_date"]).reset_index(drop=True)


def render_table(df: pd.DataFrame, cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        vals: list[str] = []
        for col in cols:
            val = row.get(col)
            if col.endswith("roi") or col.endswith("rate") or col.endswith("ci_low") or col.endswith("ci_high"):
                vals.append(pct(val))
            elif col.endswith("usd"):
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
            else:
                vals.append("" if pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_md(payload: dict[str, Any], summary: pd.DataFrame, daily: pd.DataFrame) -> str:
    forward = summary[summary["window"].eq(f"forward_{FORWARD_START}_to_{SETTLED_END}")].copy()
    all_settled = summary[summary["window"].eq("all_settled")].copy()
    return "\n".join(
        [
            "# Regime Time/Route Expansion V1",
            "",
            "## Conclusion",
            "",
            f"Verdict: `{payload['verdict']}`.",
            "",
            "This is a research/shadow replay. It evaluates route-price-disciplined entries with row-risk sizing, plus a current live-like execution gate (`base_notional_usd=9`, `min_order_shares=5`, `row_risk_soft_v1 / ask >= 1.0`). It does not change live policy.",
            "",
            "## Forward Summary",
            "",
            render_table(
                forward,
                [
                    "policy",
                    "sizing",
                    "rows",
                    "dates",
                    "cities",
                    "win_rate",
                    "avg_ask",
                    "avg_hour",
                    "cost_usd",
                    "pnl_usd",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                ],
            ),
            "",
            "## All Settled Summary",
            "",
            render_table(
                all_settled,
                [
                    "policy",
                    "sizing",
                    "rows",
                    "dates",
                    "cities",
                    "win_rate",
                    "avg_ask",
                    "avg_hour",
                    "cost_usd",
                    "pnl_usd",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                ],
            ),
            "",
            "## Forward Daily",
            "",
            render_table(
                daily[daily["target_date"].astype(str).between(FORWARD_START, SETTLED_END)],
                ["policy", "sizing", "target_date", "rows", "win_rate", "avg_ask", "avg_hour", "pnl_usd", "roi", "route_mix", "loss_cities"],
            ),
            "",
            "## Data Notes",
            "",
            f"- Generated at `{payload['generated_at_utc']}`.",
            f"- Historical atlas rows through `{payload['data']['atlas_max_target_date']}`; settled payoff window used here ends at `{SETTLED_END}`.",
            "- `research_row_soft` is opportunity sizing and can be below current minimum executable size.",
            "- `exec_gate_current` applies the current tiny-live order-size and price-quality gates, but still uses historical top-of-book snapshots, not queue simulation.",
            "",
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_10_17 = build_all_routed_for_hours({10, 11, 12, 13, 14, 15, 16, 17})
    selected_frames = []
    for label, route_set, hours in POLICIES:
        selected_frames.append(select_policy(all_10_17, label=label, route_set=route_set, hours=hours))
    selected = pd.concat([f for f in selected_frames if not f.empty], ignore_index=True)
    baseline = selected[selected["policy"].eq("baseline_fresh_capped_10_14")].copy()
    extended = selected[selected["policy"].eq("fresh_capped_extend_10_17")].copy()
    baseline_keys = set(zip(baseline["target_date"].astype(str), baseline["city"].astype(str)))
    incremental = extended[
        [
            (str(date), str(city)) not in baseline_keys
            for date, city in zip(extended["target_date"], extended["city"])
        ]
    ].copy()
    incremental["policy"] = "incremental_fresh_capped_added_by_15_17"
    selected_with_incremental = pd.concat([selected, incremental], ignore_index=True) if not incremental.empty else selected

    summary = policy_summary(selected_with_incremental)
    daily = daily_summary(selected_with_incremental)
    selected_with_incremental.to_csv(OUT_SELECTED, index=False)
    summary.to_csv(OUT_POLICY, index=False)
    daily.to_csv(OUT_DAILY, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "forward_start": FORWARD_START,
        "settled_end": SETTLED_END,
        "base_notional_usd": BASE_NOTIONAL_USD,
        "min_order_shares": MIN_ORDER_SHARES,
        "min_soft_weight_to_ask_ratio": MIN_SOFT_WEIGHT_TO_ASK_RATIO,
        "data": {
            "atlas_max_target_date": str(pd.read_csv(expression_v1.ATLAS_ROWS, usecols=["target_date"])["target_date"].astype(str).max()),
            "all_routed_rows_10_17": int(len(all_10_17)),
            "selected_rows": int(len(selected_with_incremental)),
        },
        "verdict": "do_not_promote_time_or_shadow_route_expansion_from_current_evidence",
    }
    OUT_SUMMARY.write_text(json.dumps(finite(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, summary, daily), encoding="utf-8")

    print(json.dumps(finite(payload), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
