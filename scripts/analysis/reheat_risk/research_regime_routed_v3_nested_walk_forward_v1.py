#!/usr/bin/env python3
"""Nested walk-forward review for regime-routed expression router v3.

The goal is not to find a better threshold.  It is to test whether the manual
research choices made so far survive a strict "select on prior dates, evaluate
on the next date" protocol.
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
LIVE_LIKE_DETAILS = (
    ROOT / "docs/analysis/2026-06/generated/regime_routed_expression_router_v3_live_like_entry_v1/trade_details.csv"
)
BEST_ASK_DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_expression_router_v3/trade_details.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_v3_nested_walk_forward_v1"
OUT_SUMMARY = OUT_DIR / "summary.json"
OUT_CANDIDATES = OUT_DIR / "candidate_static_summary.csv"
OUT_WF = OUT_DIR / "walk_forward_summary.csv"
OUT_DECISIONS = OUT_DIR / "walk_forward_decisions.csv"
OUT_RECENT = OUT_DIR / "recent_decision_rows.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-29-regime-routed-v3-nested-walk-forward-v1.md"

FORWARD_START = "2026-06-21"
MIN_VAL_DATES = 5
MIN_VAL_ROWS = 20
TRAILING_DATES = 10

ROUTE_SETS = {
    "all_v3": None,
    "core_no": {"fresh_runway_current_no", "capped_d2_no", "false_fade_reheat_current_no"},
    "fresh_capped": {"fresh_runway_current_no", "capped_d2_no"},
    "fresh_only": {"fresh_runway_current_no"},
    "capped_only": {"capped_d2_no"},
    "no_pullback_yes": {"fresh_runway_current_no", "capped_d2_no", "false_fade_reheat_current_no", "cheap_stale_tail_current_no"},
    "no_cheap_tail": {"fresh_runway_current_no", "capped_d2_no", "false_fade_reheat_current_no", "pullback_uncertain_current_high_yes"},
}


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


def load_base_details() -> pd.DataFrame:
    live = pd.read_csv(LIVE_LIKE_DETAILS, low_memory=False)
    live["entry_policy_family"] = "live_like_no_best_ask"
    best = pd.read_csv(BEST_ASK_DETAILS, low_memory=False)
    best["entry_policy"] = "old_best_ask"
    best["entry_policy_family"] = "leaky_best_ask_reference"
    cols = sorted(set(live.columns).intersection(best.columns))
    df = pd.concat([live[cols], best[cols]], ignore_index=True)
    df["target_date"] = df["target_date"].astype(str)
    df = df[df["router_payoff"].notna()].copy()
    return df


def build_candidate_rows(base: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for route_set_name, allowed in ROUTE_SETS.items():
        if allowed is None:
            routed = base.copy()
        else:
            routed = base[base["router_route"].isin(allowed)].copy()
        for sizing in ["full_size", "soft_balanced"]:
            out = routed.copy()
            out["route_set"] = route_set_name
            out["sizing_policy"] = sizing
            if sizing == "full_size":
                out["candidate_cost_usd"] = pd.to_numeric(out["router_cost_usd"], errors="coerce")
                out["candidate_pnl_usd"] = pd.to_numeric(out["router_pnl_usd"], errors="coerce")
            else:
                out["candidate_cost_usd"] = pd.to_numeric(out["router_weighted_cost_usd"], errors="coerce")
                out["candidate_pnl_usd"] = pd.to_numeric(out["router_weighted_pnl_usd"], errors="coerce")
            out["candidate_id"] = (
                out["entry_policy"].astype(str)
                + "__"
                + out["route_set"].astype(str)
                + "__"
                + out["sizing_policy"].astype(str)
            )
            frames.append(out)
    return pd.concat(frames, ignore_index=True)


def summarize_group(group: pd.DataFrame, **labels: Any) -> dict[str, Any]:
    cost = float(group["candidate_cost_usd"].sum())
    pnl = float(group["candidate_pnl_usd"].sum())
    return {
        **labels,
        "rows": int(len(group)),
        "dates": int(group["target_date"].nunique()) if len(group) else 0,
        "cities": int(group["city"].nunique()) if len(group) else 0,
        "wins": int(pd.to_numeric(group["router_payoff"], errors="coerce").sum()) if len(group) else 0,
        "win_rate": float(pd.to_numeric(group["router_payoff"], errors="coerce").mean()) if len(group) else None,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "avg_ask": float(pd.to_numeric(group["router_ask"], errors="coerce").mean()) if len(group) else None,
    }


def candidate_static_summary(candidates: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for candidate_id, group in candidates.groupby("candidate_id", dropna=False):
        first = group.iloc[0]
        rows.append(
            summarize_group(
                group,
                candidate_id=candidate_id,
                entry_policy=first["entry_policy"],
                entry_policy_family=first["entry_policy_family"],
                route_set=first["route_set"],
                sizing_policy=first["sizing_policy"],
                window="all",
            )
        )
        rows.append(
            summarize_group(
                group[group["target_date"].ge(FORWARD_START)],
                candidate_id=candidate_id,
                entry_policy=first["entry_policy"],
                entry_policy_family=first["entry_policy_family"],
                route_set=first["route_set"],
                sizing_policy=first["sizing_policy"],
                window=f"forward_{FORWARD_START}_plus",
            )
        )
    return pd.DataFrame(rows).sort_values(["entry_policy_family", "window", "roi"], ascending=[True, True, False])


def menu_filter(candidates: pd.DataFrame, menu: str) -> pd.DataFrame:
    if menu == "live_strict_full":
        return candidates[
            candidates["entry_policy_family"].eq("live_like_no_best_ask")
            & candidates["sizing_policy"].eq("full_size")
        ].copy()
    if menu == "live_like_with_soft":
        return candidates[candidates["entry_policy_family"].eq("live_like_no_best_ask")].copy()
    if menu == "research_including_best_ask":
        return candidates.copy()
    raise ValueError(menu)


def validation_dates(all_dates: list[str], idx: int, selector: str) -> list[str]:
    prior = all_dates[:idx]
    if selector == "expanding_prior":
        return prior
    if selector == "trailing_10_dates":
        return prior[-TRAILING_DATES:]
    raise ValueError(selector)


def run_walk_forward(candidates: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    all_dates = sorted(candidates["target_date"].unique().tolist())
    decisions: list[dict[str, Any]] = []
    for menu in ["live_strict_full", "live_like_with_soft", "research_including_best_ask"]:
        menu_df = menu_filter(candidates, menu)
        for val_selector in ["expanding_prior", "trailing_10_dates"]:
            for idx, test_date in enumerate(all_dates):
                val_dates = validation_dates(all_dates, idx, val_selector)
                if len(val_dates) < MIN_VAL_DATES:
                    continue
                val = menu_df[menu_df["target_date"].isin(val_dates)]
                test = menu_df[menu_df["target_date"].eq(test_date)]
                if val.empty or test.empty:
                    continue
                val_summary = (
                    val.groupby("candidate_id", as_index=False)
                    .agg(
                        val_rows=("city", "size"),
                        val_dates=("target_date", "nunique"),
                        val_cost_usd=("candidate_cost_usd", "sum"),
                        val_pnl_usd=("candidate_pnl_usd", "sum"),
                    )
                )
                val_summary = val_summary[val_summary["val_rows"].ge(MIN_VAL_ROWS) & val_summary["val_dates"].ge(MIN_VAL_DATES)].copy()
                val_summary = val_summary[val_summary["val_cost_usd"].gt(0)].copy()
                if val_summary.empty:
                    continue
                val_summary["val_roi"] = val_summary["val_pnl_usd"] / val_summary["val_cost_usd"]
                chosen = val_summary.sort_values(["val_roi", "val_dates", "val_rows", "candidate_id"], ascending=[False, False, False, True]).iloc[0]
                candidate_id = chosen["candidate_id"]
                test_group = test[test["candidate_id"].eq(candidate_id)]
                if test_group.empty:
                    decisions.append(
                        {
                            "menu": menu,
                            "validation_selector": val_selector,
                            "test_date": test_date,
                            "candidate_id": candidate_id,
                            "entry_policy": None,
                            "entry_policy_family": None,
                            "route_set": None,
                            "sizing_policy": None,
                            "val_rows": int(chosen["val_rows"]),
                            "val_dates": int(chosen["val_dates"]),
                            "val_roi": float(chosen["val_roi"]),
                            "test_rows": 0,
                            "test_cost_usd": 0.0,
                            "test_pnl_usd": 0.0,
                            "test_roi": None,
                            "test_wins": 0,
                            "test_win_rate": None,
                        }
                    )
                    continue
                first = test_group.iloc[0]
                test_cost = float(test_group["candidate_cost_usd"].sum())
                test_pnl = float(test_group["candidate_pnl_usd"].sum())
                decisions.append(
                    {
                        "menu": menu,
                        "validation_selector": val_selector,
                        "test_date": test_date,
                        "candidate_id": candidate_id,
                        "entry_policy": first["entry_policy"],
                        "entry_policy_family": first["entry_policy_family"],
                        "route_set": first["route_set"],
                        "sizing_policy": first["sizing_policy"],
                        "val_rows": int(chosen["val_rows"]),
                        "val_dates": int(chosen["val_dates"]),
                        "val_roi": float(chosen["val_roi"]),
                        "test_rows": int(len(test_group)),
                        "test_cost_usd": test_cost,
                        "test_pnl_usd": test_pnl,
                        "test_roi": test_pnl / test_cost if test_cost else None,
                        "test_wins": int(pd.to_numeric(test_group["router_payoff"], errors="coerce").sum()),
                        "test_win_rate": float(pd.to_numeric(test_group["router_payoff"], errors="coerce").mean()),
                    }
                )
    decision_df = pd.DataFrame(decisions)
    summary_rows = []
    for (menu, selector, window), group in decision_df.assign(
        window=lambda d: np.where(d["test_date"].ge(FORWARD_START), f"forward_{FORWARD_START}_plus", "all_walk_forward")
    ).groupby(["menu", "validation_selector", "window"], dropna=False):
        cost = float(group["test_cost_usd"].sum())
        pnl = float(group["test_pnl_usd"].sum())
        summary_rows.append(
            {
                "menu": menu,
                "validation_selector": selector,
                "window": window,
                "test_days": int(group["test_date"].nunique()),
                "test_rows": int(group["test_rows"].sum()),
                "cost_usd": cost,
                "pnl_usd": pnl,
                "roi": pnl / cost if cost else None,
                "avg_val_roi": float(group["val_roi"].mean()) if len(group) else None,
                "chosen_candidates": ";".join(
                    f"{k}:{v}" for k, v in group["candidate_id"].value_counts().sort_index().items()
                ),
                "chosen_route_sets": ";".join(
                    f"{k}:{v}" for k, v in group["route_set"].fillna("none").value_counts().sort_index().items()
                ),
                "chosen_sizing": ";".join(
                    f"{k}:{v}" for k, v in group["sizing_policy"].fillna("none").value_counts().sort_index().items()
                ),
            }
        )
    return pd.DataFrame(summary_rows), decision_df


def render_md(payload: dict[str, Any], static: pd.DataFrame, wf: pd.DataFrame, decisions: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate"):
                    vals.append(pct(val))
                elif col.endswith("usd"):
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    recent = decisions[decisions["test_date"].ge(FORWARD_START)].copy()
    return "\n".join(
        [
            "# Regime-Routed V3 Nested Walk-Forward V1",
            "",
            "## Conclusion",
            "",
            "This is the first nested walk-forward audit for the manual choices made around v3: entry policy, route sleeves, and sizing.  Each test date is evaluated only after a candidate is selected from prior dates.  The result does not validate the strategy; it shows that the candidate menu is highly selection-sensitive and still weak on the 2026-06-21+ window.",
            "",
            f"Verdict: `{payload['verdict']['conclusion']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## Walk-Forward Summary",
            "",
            table(
                wf,
                [
                    "menu",
                    "validation_selector",
                    "window",
                    "test_days",
                    "test_rows",
                    "pnl_usd",
                    "roi",
                    "avg_val_roi",
                    "chosen_route_sets",
                    "chosen_sizing",
                ],
            ),
            "",
            "## Recent Decisions",
            "",
            table(
                recent,
                [
                    "menu",
                    "validation_selector",
                    "test_date",
                    "candidate_id",
                    "val_roi",
                    "test_rows",
                    "test_pnl_usd",
                    "test_roi",
                ],
                limit=80,
            ),
            "",
            "## Static Candidate Leaders",
            "",
            table(
                static.sort_values("roi", ascending=False),
                [
                    "candidate_id",
                    "entry_policy_family",
                    "route_set",
                    "sizing_policy",
                    "window",
                    "rows",
                    "dates",
                    "pnl_usd",
                    "roi",
                ],
                limit=30,
            ),
            "",
            "## Notes",
            "",
            "- `live_strict_full` excludes best-ask and soft sizing.",
            "- `live_like_with_soft` excludes best-ask but lets validation choose full vs soft sizing.",
            "- `research_including_best_ask` is a diagnostic menu only; if it wins, that is evidence of selection/leakage risk, not a live candidate.",
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = load_base_details()
    candidates = build_candidate_rows(base)
    static = candidate_static_summary(candidates)
    wf, decisions = run_walk_forward(candidates)
    recent = decisions[decisions["test_date"].ge(FORWARD_START)].copy()
    payload = {
        "generated_at_utc": now_utc(),
        "strategy": "regime_routed_v3_nested_walk_forward_v1",
        "source": {
            "live_like_details": str(LIVE_LIKE_DETAILS.relative_to(ROOT)),
            "best_ask_details": str(BEST_ASK_DETAILS.relative_to(ROOT)),
            "base_rows": int(len(base)),
            "candidate_rows": int(len(candidates)),
            "date_min": str(base["target_date"].min()) if len(base) else None,
            "date_max": str(base["target_date"].max()) if len(base) else None,
            "min_val_dates": MIN_VAL_DATES,
            "min_val_rows": MIN_VAL_ROWS,
            "trailing_dates": TRAILING_DATES,
        },
        "menus": {
            "live_strict_full": "live-like entry policies only; full-size only",
            "live_like_with_soft": "live-like entry policies; validation may choose full-size or soft-balanced",
            "research_including_best_ask": "diagnostic menu including old best_ask; not live-eligible",
        },
        "walk_forward_summary": finite(wf.to_dict(orient="records")),
        "recent_decisions": finite(recent.to_dict(orient="records")),
        "outputs": {
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "candidate_static_summary": str(OUT_CANDIDATES.relative_to(ROOT)),
            "walk_forward_summary": str(OUT_WF.relative_to(ROOT)),
            "walk_forward_decisions": str(OUT_DECISIONS.relative_to(ROOT)),
            "recent_decision_rows": str(OUT_RECENT.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "significance": "FAIL_OR_UNSTABLE",
            "baseline": "NA_NESTED_WALK_FORWARD",
            "forward": "FAIL_2026_06_21_PLUS",
            "conclusion": "inconclusive_shadow_only",
            "live_ready": False,
        },
    }
    static.to_csv(OUT_CANDIDATES, index=False)
    wf.to_csv(OUT_WF, index=False)
    decisions.to_csv(OUT_DECISIONS, index=False)
    recent.to_csv(OUT_RECENT, index=False)
    OUT_SUMMARY.write_text(json.dumps(finite(payload), indent=2, sort_keys=True) + "\n")
    OUT_MD.write_text(render_md(payload, static, wf, decisions) + "\n")
    print(json.dumps(finite(payload), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
