#!/usr/bin/env python3
"""Live-like entry sensitivity for regime-routed expression router v3.

This replay removes the day-level best-ask selector.  It compares deterministic
entry policies that could be declared before the trading day:

* first_eligible: first city-day candidate that passes route/liquidity;
* fixed_noon_priority: fixed local-hour priority 12, 11, 13, 10, 14.
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

import research_regime_routed_expression_router_v3 as router_v3  # noqa: E402
import research_regime_routed_no_expression_v1 as expression_v1  # noqa: E402
import research_regime_routed_no_mechanism_split_v2 as mechanism_v2  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_expression_router_v3_live_like_entry_v1"
OUT_SUMMARY = OUT_DIR / "summary.json"
OUT_POLICY = OUT_DIR / "policy_summary.csv"
OUT_DAILY = OUT_DIR / "daily_summary.csv"
OUT_ROUTE = OUT_DIR / "route_summary.csv"
OUT_DETAILS = OUT_DIR / "trade_details.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-29-regime-routed-expression-router-v3-live-like-entry-v1.md"

FORWARD_START = "2026-06-21"
STAKE_USD = 5.0
VARIANT = "routed_capped_d2_no_relaxed70_live_like"
ASK_MAX = expression_v1.ASK_CAPS["relaxed70"]
NOON_PRIORITY = {12: 0, 11: 1, 13: 2, 10: 3, 14: 4}


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


def money(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"${val:+.2f}"


def finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        val = float(value)
        return val if math.isfinite(val) else None
    return value


def base_candidates(states: pd.DataFrame, *, require_payoff: bool = True) -> pd.DataFrame:
    frame = expression_v1.routed_candidates(states, "d2_no")
    frame = expression_v1.apply_liquidity(frame, ASK_MAX, require_payoff=require_payoff)
    frame["variant"] = VARIANT
    frame["ask_max"] = ASK_MAX
    return frame.reset_index(drop=True)


def select_live_like(frame: pd.DataFrame, policy: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    out["decision_hour_num"] = pd.to_numeric(out["decision_hour_local"], errors="coerce")
    if policy == "first_eligible":
        out["policy_rank"] = out["decision_hour_num"]
        sort_cols = ["target_date", "city", "policy_rank", "ask"]
    elif policy == "fixed_noon_priority":
        out["policy_rank"] = out["decision_hour_num"].map(NOON_PRIORITY).fillna(99)
        sort_cols = ["target_date", "city", "policy_rank", "decision_hour_num", "ask"]
    else:
        raise ValueError(policy)
    selected = (
        out.sort_values(sort_cols)
        .drop_duplicates(["target_date", "city"], keep="first")
        .reset_index(drop=True)
    )
    selected["entry_policy"] = policy
    selected["selector"] = policy
    return selected


def build_policy_details(states: pd.DataFrame, policy: str) -> pd.DataFrame:
    selected = select_live_like(base_candidates(states), policy)
    labeled = mechanism_v2.add_mechanism_labels(selected)
    routed = router_v3.build_v3_details(labeled)
    routed["entry_policy"] = policy
    routed["entry_policy_family"] = "live_like_no_best_ask"
    return routed


def summarize(frame: pd.DataFrame, label: str, *, window: str = "all") -> dict[str, Any]:
    row = router_v3.summarize(frame, label, window=window)
    row["entry_policy"] = label
    return row


def daily_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (policy, target_date), group in frame.groupby(["entry_policy", "target_date"], dropna=False):
        cost = float(group["router_cost_usd"].sum())
        pnl = float(group["router_pnl_usd"].sum())
        rows.append(
            {
                "entry_policy": policy,
                "target_date": target_date,
                "rows": int(len(group)),
                "cities": int(group["city"].nunique()),
                "wins": int(pd.to_numeric(group["router_payoff"], errors="coerce").sum()),
                "win_rate": float(pd.to_numeric(group["router_payoff"], errors="coerce").mean()),
                "avg_ask": float(pd.to_numeric(group["router_ask"], errors="coerce").mean()),
                "cost_usd": cost,
                "pnl_usd": pnl,
                "roi": pnl / cost if cost else None,
                "route_mix": ",".join(
                    f"{k}:{v}" for k, v in group["router_route"].value_counts().sort_index().items()
                ),
                "loss_cities": ",".join(
                    group.loc[pd.to_numeric(group["router_payoff"], errors="coerce").eq(0), "city"]
                    .astype(str)
                    .sort_values()
                    .tolist()
                ),
            }
        )
    return pd.DataFrame(rows).sort_values(["entry_policy", "target_date"]).reset_index(drop=True)


def route_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (policy, route, window), group in frame.groupby(["entry_policy", "router_route", "router_window"], dropna=False):
        row = router_v3.summarize(group, route, window=str(window))
        row["entry_policy"] = policy
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["entry_policy", "window", "rows"], ascending=[True, True, False])


def render_md(payload: dict[str, Any], policy_summary: pd.DataFrame, daily: pd.DataFrame, route: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
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

    recent = daily[daily["target_date"].astype(str).ge(FORWARD_START)].copy()
    return "\n".join(
        [
            "# Regime-Routed Expression Router V3 Live-Like Entry V1",
            "",
            "## Conclusion",
            "",
            "This replay removes the day-level `best_ask` selector.  `first_eligible` is the strictest causal policy; `fixed_noon_priority` is a pre-declared local-hour priority and does not select by price.",
            "",
            f"Verdict: `{payload['verdict']['conclusion']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## Policy Summary",
            "",
            table(
                policy_summary,
                [
                    "entry_policy",
                    "window",
                    "rows",
                    "dates",
                    "cities",
                    "win_rate",
                    "avg_ask",
                    "pnl_usd",
                    "roi",
                    "weighted_pnl_usd",
                    "weighted_roi",
                    "weighted_roi_ci_low",
                    "weighted_roi_ci_high",
                    "daily_roi_eq_minus100",
                    "worst_day_pnl_usd",
                ],
            ),
            "",
            "## Recent Daily",
            "",
            table(
                recent,
                ["entry_policy", "target_date", "rows", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "route_mix", "loss_cities"],
            ),
            "",
            "## Route Summary",
            "",
            table(
                route,
                ["entry_policy", "slice", "window", "rows", "dates", "win_rate", "avg_ask", "pnl_usd", "roi", "weighted_roi"],
            ),
            "",
            "## Data Notes",
            "",
            f"- Source atlas: `{payload['source']['atlas_rows']}`",
            f"- Settled performance range: `{payload['source']['settled_date_min']}`..`{payload['source']['settled_date_max']}`",
            "- 6/27..6/28 remain unresolved for ROI in the current fact layer.",
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    states = expression_v1.load_states()
    details = pd.concat(
        [build_policy_details(states, "first_eligible"), build_policy_details(states, "fixed_noon_priority")],
        ignore_index=True,
    )
    details = details[details["router_payoff"].notna()].copy()

    summary_rows: list[dict[str, Any]] = []
    for policy, group in details.groupby("entry_policy"):
        summary_rows.append(summarize(group, policy))
        summary_rows.append(summarize(group[group["target_date"].astype(str).ge(FORWARD_START)], policy, window=f"forward_{FORWARD_START}_plus"))
        summary_rows.append(summarize(group[group["target_date"].astype(str).lt(FORWARD_START)], policy, window=f"train_to_2026_06_20"))
    policy_summary = pd.DataFrame(summary_rows)
    daily = daily_summary(details)
    route = route_summary(details)

    payload = {
        "generated_at_utc": now_utc(),
        "strategy": "regime_routed_expression_router_v3_live_like_entry_v1",
        "source": {
            "atlas_rows": str(expression_v1.ATLAS_ROWS.relative_to(ROOT)),
            "atlas_state_rows": int(len(states)),
            "atlas_date_min": str(states["target_date"].min()) if len(states) else None,
            "atlas_date_max": str(states["target_date"].max()) if len(states) else None,
            "settled_date_min": str(details["target_date"].min()) if len(details) else None,
            "settled_date_max": str(details["target_date"].max()) if len(details) else None,
            "ask_max": ASK_MAX,
            "stake_usd": STAKE_USD,
            "forward_start": FORWARD_START,
        },
        "policies": {
            "first_eligible": "earliest local decision hour after route/liquidity passes",
            "fixed_noon_priority": "predeclared local-hour priority 12,11,13,10,14; no price sorting before hour priority",
        },
        "policy_summary": finite(policy_summary.to_dict(orient="records")),
        "recent_daily": finite(daily[daily["target_date"].astype(str).ge(FORWARD_START)].to_dict(orient="records")),
        "route_summary": finite(route.to_dict(orient="records")),
        "outputs": {
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "policy_summary": str(OUT_POLICY.relative_to(ROOT)),
            "daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "route_summary": str(OUT_ROUTE.relative_to(ROOT)),
            "trade_details": str(OUT_DETAILS.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "significance": "MIXED_OR_FAIL_ON_FULL_SIZE_CI",
            "baseline": "NA_ENTRY_SENSITIVITY",
            "forward": "FAIL_RECENT_2026_06_21_PLUS_NEGATIVE_OR_THIN",
            "conclusion": "inconclusive_shadow_only",
            "live_ready": False,
        },
    }

    details.to_csv(OUT_DETAILS, index=False)
    policy_summary.to_csv(OUT_POLICY, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    route.to_csv(OUT_ROUTE, index=False)
    OUT_SUMMARY.write_text(json.dumps(finite(payload), indent=2, sort_keys=True) + "\n")
    OUT_MD.write_text(render_md(payload, policy_summary, daily, route) + "\n")
    print(json.dumps(finite(payload), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
