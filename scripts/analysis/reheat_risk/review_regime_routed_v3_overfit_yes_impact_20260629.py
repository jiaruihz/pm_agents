#!/usr/bin/env python3
"""Overfit and YES-route impact review for regime-routed v3 route-price candidate."""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_DIR = ROOT / "scripts/analysis/reheat_risk"
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))

import research_regime_routed_v3_route_specific_timing_wf_v1 as timing_wf  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_v3_overfit_yes_impact_20260629"
OUT_JSON = OUT_DIR / "summary.json"
OUT_CAP_GRID = OUT_DIR / "price_cap_grid_summary.csv"
OUT_CAP_WF = OUT_DIR / "price_cap_grid_walk_forward.csv"
OUT_YES_DIFF = OUT_DIR / "yes_route_impact_rows.csv"
OUT_CANDIDATE_RANKS = OUT_DIR / "candidate_rank_summary.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-29-regime-routed-v3-overfit-yes-impact.md"

FORWARD_START = "2026-06-21"
BASE_CANDIDATE = "route_price_disciplined_v1__no_pullback_yes__row_risk_soft_v1"
ALL_WITH_YES_CANDIDATE = "route_price_disciplined_v1__all_v3__row_risk_soft_v1"
BASE_CAPS = {
    "fresh_runway_current_no": 0.55,
    "capped_d2_no": 0.62,
    "false_fade_reheat_current_no": 0.65,
    "cheap_stale_tail_current_no": 0.40,
}
NO_PULLBACK_ROUTES = set(BASE_CAPS)
ALL_V3_ROUTES = NO_PULLBACK_ROUTES | {"pullback_uncertain_current_high_yes"}
STAKE_USD = 5.0
MIN_VAL_DATES = 5
MIN_VAL_ROWS = 20


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


def summarize(frame: pd.DataFrame, *, cost_col: str = "candidate_cost_usd", pnl_col: str = "candidate_pnl_usd") -> dict[str, Any]:
    clean = frame[frame["router_payoff"].notna()].copy()
    cost = float(pd.to_numeric(clean[cost_col], errors="coerce").sum()) if len(clean) else 0.0
    pnl = float(pd.to_numeric(clean[pnl_col], errors="coerce").sum()) if len(clean) else 0.0
    return {
        "rows": int(len(clean)),
        "dates": int(clean["target_date"].nunique()) if len(clean) else 0,
        "cities": int(clean["city"].nunique()) if len(clean) else 0,
        "wins": int(pd.to_numeric(clean["router_payoff"], errors="coerce").sum()) if len(clean) else 0,
        "win_rate": float(pd.to_numeric(clean["router_payoff"], errors="coerce").mean()) if len(clean) else None,
        "avg_ask": float(pd.to_numeric(clean["router_ask"], errors="coerce").mean()) if len(clean) else None,
        "avg_weight": float(pd.to_numeric(clean.get("candidate_weight"), errors="coerce").mean()) if "candidate_weight" in clean else None,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
    }


def date_bootstrap_roi(frame: pd.DataFrame, *, n: int = 5000, seed: int = 20260629) -> tuple[float | None, float | None]:
    clean = frame[frame["router_payoff"].notna()].copy()
    if clean.empty or clean["target_date"].nunique() < 3:
        return None, None
    daily = clean.groupby("target_date", as_index=False).agg(
        cost=("candidate_cost_usd", "sum"),
        pnl=("candidate_pnl_usd", "sum"),
    )
    rng = np.random.default_rng(seed)
    idx = np.arange(len(daily))
    costs = daily["cost"].to_numpy(dtype=float)
    pnls = daily["pnl"].to_numpy(dtype=float)
    rois: list[float] = []
    for _ in range(n):
        sample = rng.choice(idx, size=len(idx), replace=True)
        cost = float(costs[sample].sum())
        if cost:
            rois.append(float(pnls[sample].sum() / cost))
    if not rois:
        return None, None
    low, high = np.quantile(rois, [0.025, 0.975])
    return float(low), float(high)


def load_candidate_rows() -> pd.DataFrame:
    path = timing_wf.OUT_SELECTED
    if not path.exists():
        timing_wf.main()
    df = pd.read_csv(path, low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    return df


def candidate_rank_summary(candidates: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    settled = candidates[candidates["router_payoff"].notna()].copy()
    for cid, group in settled.groupby("candidate_id"):
        if len(group) < 20 or group["target_date"].nunique() < 5:
            continue
        all_sum = summarize(group)
        fwd = group[group["target_date"].ge(FORWARD_START)].copy()
        fwd_sum = summarize(fwd)
        rows.append(
            {
                "candidate_id": cid,
                "entry_policy": str(group["entry_policy"].iloc[0]),
                "route_set": str(group["route_set"].iloc[0]),
                "sizing_policy": str(group["sizing_policy"].iloc[0]),
                "all_rows": all_sum["rows"],
                "all_dates": all_sum["dates"],
                "all_roi": all_sum["roi"],
                "forward_rows": fwd_sum["rows"],
                "forward_dates": fwd_sum["dates"],
                "forward_roi": fwd_sum["roi"],
                "avg_ask": all_sum["avg_ask"],
                "avg_weight": all_sum["avg_weight"],
            }
        )
    out = pd.DataFrame(rows)
    out["all_roi_rank"] = out["all_roi"].rank(method="min", ascending=False)
    out["forward_roi_rank"] = out["forward_roi"].rank(method="min", ascending=False)
    return out.sort_values("all_roi_rank").reset_index(drop=True)


def select_with_caps(all_routed: pd.DataFrame, caps: dict[str, float], *, include_yes: bool = False) -> pd.DataFrame:
    routes = ALL_V3_ROUTES if include_yes else NO_PULLBACK_ROUTES
    work = all_routed[all_routed["router_route"].isin(routes)].copy()
    if work.empty:
        return work
    cap_series = work["router_route"].map(caps)
    if include_yes:
        cap_series = cap_series.fillna(0.65)
    work = work[pd.to_numeric(work["router_ask"], errors="coerce").le(cap_series.astype(float))].copy()
    if work.empty:
        return work
    selected = (
        work.sort_values(["target_date", "city", "decision_hour_num", "router_ask"])
        .drop_duplicates(["target_date", "city"], keep="first")
        .reset_index(drop=True)
    )
    selected["entry_policy"] = "cap_grid_first_eligible"
    selected["route_set"] = "all_v3" if include_yes else "no_pullback_yes"
    selected = timing_wf.add_row_sizing(selected)
    selected["candidate_weight"] = pd.to_numeric(selected["row_risk_soft_v1"], errors="coerce").fillna(0)
    selected["candidate_cost_usd"] = pd.to_numeric(selected["router_cost_usd"], errors="coerce") * selected["candidate_weight"]
    selected["candidate_pnl_usd"] = pd.to_numeric(selected["router_pnl_usd"], errors="coerce") * selected["candidate_weight"]
    return selected


def cap_grid_summary(all_routed: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    fresh_vals = [0.50, 0.55, 0.60]
    capped_vals = [0.57, 0.62, 0.67]
    false_vals = [0.60, 0.65, 0.70]
    cheap_vals = [0.35, 0.40, 0.45]
    for fresh, capped, false, cheap in product(fresh_vals, capped_vals, false_vals, cheap_vals):
        caps = {
            "fresh_runway_current_no": fresh,
            "capped_d2_no": capped,
            "false_fade_reheat_current_no": false,
            "cheap_stale_tail_current_no": cheap,
        }
        selected = select_with_caps(all_routed, caps, include_yes=False)
        settled = selected[selected["router_payoff"].notna()].copy()
        train = settled[settled["target_date"].lt(FORWARD_START)].copy()
        fwd = settled[settled["target_date"].ge(FORWARD_START)].copy()
        all_sum = summarize(settled)
        train_sum = summarize(train)
        fwd_sum = summarize(fwd)
        rows.append(
            {
                "cap_id": f"fresh{fresh:.2f}_capped{capped:.2f}_false{false:.2f}_cheap{cheap:.2f}",
                "fresh_cap": fresh,
                "capped_cap": capped,
                "false_cap": false,
                "cheap_cap": cheap,
                "all_rows": all_sum["rows"],
                "all_dates": all_sum["dates"],
                "all_roi": all_sum["roi"],
                "train_rows": train_sum["rows"],
                "train_dates": train_sum["dates"],
                "train_roi": train_sum["roi"],
                "forward_rows": fwd_sum["rows"],
                "forward_dates": fwd_sum["dates"],
                "forward_roi": fwd_sum["roi"],
                "avg_ask": all_sum["avg_ask"],
                "avg_weight": all_sum["avg_weight"],
                "is_base_caps": caps == BASE_CAPS,
            }
        )
    out = pd.DataFrame(rows)
    out["all_roi_rank"] = out["all_roi"].rank(method="min", ascending=False)
    out["train_roi_rank"] = out["train_roi"].rank(method="min", ascending=False)
    out["forward_roi_rank"] = out["forward_roi"].rank(method="min", ascending=False)
    return out.sort_values("all_roi_rank").reset_index(drop=True)


def cap_grid_walk_forward(all_routed: pd.DataFrame, grid: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for _, row in grid.iterrows():
        caps = {
            "fresh_runway_current_no": float(row["fresh_cap"]),
            "capped_d2_no": float(row["capped_cap"]),
            "false_fade_reheat_current_no": float(row["false_cap"]),
            "cheap_stale_tail_current_no": float(row["cheap_cap"]),
        }
        selected = select_with_caps(all_routed, caps, include_yes=False)
        if selected.empty:
            continue
        selected = selected[selected["router_payoff"].notna()].copy()
        selected["cap_id"] = row["cap_id"]
        frames.append(selected)
    all_selected = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    dates = sorted(all_selected["target_date"].unique().tolist())
    decisions: list[dict[str, Any]] = []
    for idx, test_date in enumerate(dates):
        prior = dates[:idx]
        if len(prior) < MIN_VAL_DATES:
            continue
        val = all_selected[all_selected["target_date"].isin(prior)].copy()
        test = all_selected[all_selected["target_date"].eq(test_date)].copy()
        val_sum = (
            val.groupby("cap_id", as_index=False)
            .agg(
                val_rows=("city", "size"),
                val_dates=("target_date", "nunique"),
                val_cost=("candidate_cost_usd", "sum"),
                val_pnl=("candidate_pnl_usd", "sum"),
            )
        )
        val_sum = val_sum[val_sum["val_rows"].ge(MIN_VAL_ROWS) & val_sum["val_dates"].ge(MIN_VAL_DATES) & val_sum["val_cost"].gt(0)].copy()
        if val_sum.empty:
            continue
        val_sum["val_roi"] = val_sum["val_pnl"] / val_sum["val_cost"]
        chosen = val_sum.sort_values(["val_roi", "val_dates", "val_rows", "cap_id"], ascending=[False, False, False, True]).iloc[0]
        tg = test[test["cap_id"].eq(chosen["cap_id"])].copy()
        if tg.empty:
            continue
        cost = float(tg["candidate_cost_usd"].sum())
        pnl = float(tg["candidate_pnl_usd"].sum())
        decisions.append(
            {
                "test_date": test_date,
                "cap_id": str(chosen["cap_id"]),
                "val_roi": float(chosen["val_roi"]),
                "test_rows": int(len(tg)),
                "test_cost_usd": cost,
                "test_pnl_usd": pnl,
                "test_roi": pnl / cost if cost else None,
                "window": "forward_2026_06_21_plus" if test_date >= FORWARD_START else "all_walk_forward",
            }
        )
    return pd.DataFrame(decisions)


def yes_route_impact(candidates: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    no_df = candidates[candidates["candidate_id"].eq(BASE_CANDIDATE) & candidates["router_payoff"].notna()].copy()
    yes_df = candidates[candidates["candidate_id"].eq(ALL_WITH_YES_CANDIDATE) & candidates["router_payoff"].notna()].copy()
    rows: list[dict[str, Any]] = []
    key_cols = ["target_date", "city"]
    merged = yes_df.merge(
        no_df[key_cols + ["router_route", "decision_hour_local", "router_payoff", "router_ask", "candidate_cost_usd", "candidate_pnl_usd"]].rename(
            columns={
                "router_route": "no_route",
                "decision_hour_local": "no_hour",
                "router_payoff": "no_payoff",
                "router_ask": "no_ask",
                "candidate_cost_usd": "no_cost",
                "candidate_pnl_usd": "no_pnl",
            }
        ),
        on=key_cols,
        how="left",
    )
    for _, row in merged.iterrows():
        if row["router_route"] == row.get("no_route"):
            effect = "same_route"
        elif row["router_route"] == "pullback_uncertain_current_high_yes" and pd.notna(row.get("no_route")):
            effect = "yes_replaced_no"
        elif row["router_route"] == "pullback_uncertain_current_high_yes":
            effect = "yes_added_city_day"
        else:
            effect = "other_route_difference"
        if effect == "same_route":
            continue
        rows.append(
            {
                "target_date": row["target_date"],
                "city": row["city"],
                "effect": effect,
                "yes_route": row["router_route"],
                "yes_hour": row["decision_hour_local"],
                "yes_payoff": row["router_payoff"],
                "yes_ask": row["router_ask"],
                "yes_cost": row["candidate_cost_usd"],
                "yes_pnl": row["candidate_pnl_usd"],
                "no_route": row.get("no_route"),
                "no_hour": row.get("no_hour"),
                "no_payoff": row.get("no_payoff"),
                "no_ask": row.get("no_ask"),
                "no_cost": row.get("no_cost"),
                "no_pnl": row.get("no_pnl"),
                "pnl_delta_vs_no": row["candidate_pnl_usd"] - (row.get("no_pnl") if pd.notna(row.get("no_pnl")) else 0.0),
                "cost_delta_vs_no": row["candidate_cost_usd"] - (row.get("no_cost") if pd.notna(row.get("no_cost")) else 0.0),
            }
        )
    impact_rows = pd.DataFrame(rows)
    all_no = summarize(no_df)
    all_yes = summarize(yes_df)
    fwd_no = summarize(no_df[no_df["target_date"].ge(FORWARD_START)])
    fwd_yes = summarize(yes_df[yes_df["target_date"].ge(FORWARD_START)])
    impact = {
        "no_pullback_all": all_no,
        "all_v3_with_yes_all": all_yes,
        "no_pullback_forward": fwd_no,
        "all_v3_with_yes_forward": fwd_yes,
        "all_roi_delta": (all_yes["roi"] or 0) - (all_no["roi"] or 0),
        "forward_roi_delta": (fwd_yes["roi"] or 0) - (fwd_no["roi"] or 0),
        "difference_rows": int(len(impact_rows)),
        "difference_pnl_delta": float(impact_rows["pnl_delta_vs_no"].sum()) if not impact_rows.empty else 0.0,
    }
    return impact_rows, impact


def render_md(payload: dict[str, Any], cap_grid: pd.DataFrame, cap_wf: pd.DataFrame, ranks: pd.DataFrame, yes_rows: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate") or col.endswith("delta") or col.startswith("ci_"):
                    vals.append(pct(val))
                elif col.endswith("usd") or col.endswith("pnl") or col.endswith("cost"):
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    base_grid = cap_grid[cap_grid["is_base_caps"]].iloc[0].to_dict()
    wf_summary_rows: list[dict[str, Any]] = []
    for window, group in cap_wf.groupby("window", dropna=False):
        cost = float(group["test_cost_usd"].sum())
        pnl = float(group["test_pnl_usd"].sum())
        ci_low, ci_high = date_bootstrap_roi(
            group.rename(
                columns={
                    "test_date": "target_date",
                    "test_cost_usd": "candidate_cost_usd",
                    "test_pnl_usd": "candidate_pnl_usd",
                }
            ).assign(router_payoff=1)
        )
        wf_summary_rows.append(
            {
                "window": window,
                "test_days": int(group["test_date"].nunique()),
                "test_rows": int(group["test_rows"].sum()),
                "pnl_usd": pnl,
                "roi": pnl / cost if cost else None,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "chosen_caps": ";".join(f"{k}:{v}" for k, v in group["cap_id"].value_counts().sort_index().items()),
            }
        )
    wf_summary = pd.DataFrame(wf_summary_rows)

    yes_impact = payload["yes_impact"]
    return "\n".join(
        [
            "# Regime-Routed V3 Overfit And YES Impact Review",
            "",
            "## Conclusion",
            "",
            (
                "Overfit risk is real, but the latest evidence is not a simple rejection.  The price-discipline "
                "candidate is mechanically sensible and the local cap neighborhood survives a small nested "
                "walk-forward.  The problem is that support is thin and the broader strategy menu was built after "
                "many manual choices, so static ROI is still too optimistic for live approval."
            ),
            "",
            "Verdict: `inconclusive_shadow_only`，live_ready=`False`。",
            "",
            "## Data Snapshot",
            "",
            f"- Source: `{payload['source']}`",
            f"- Candidate menu rows: `{payload['candidate_policy_rows']}`",
            f"- Settled unique routed rows: `{payload['unique_settled_routed_rows']}`",
            f"- Forward starts: `{FORWARD_START}`; settlement-backed ROI only through `2026-06-26`.",
            "",
            "## Static Selection Risk",
            "",
            f"- Candidate menu size after support filter: `{payload['candidate_rank_count']}` candidates.",
            f"- Base candidate static all-ROI rank: `{payload['base_candidate_rank']['all_roi_rank']}` / `{payload['candidate_rank_count']}`.",
            f"- Base candidate forward-ROI rank: `{payload['base_candidate_rank']['forward_roi_rank']}` / `{payload['candidate_rank_count']}`.",
            "",
            table(
                ranks.sort_values("all_roi_rank"),
                ["candidate_id", "all_rows", "all_dates", "all_roi", "forward_rows", "forward_dates", "forward_roi", "avg_ask", "avg_weight"],
                limit=12,
            ),
            "",
            "## Price-Cap Grid",
            "",
            (
                f"Base caps rank by all ROI: `{int(base_grid['all_roi_rank'])}` / `{len(cap_grid)}`; "
                f"rank by forward ROI: `{int(base_grid['forward_roi_rank'])}` / `{len(cap_grid)}`."
            ),
            "",
            table(
                cap_grid.sort_values("all_roi_rank"),
                ["cap_id", "all_rows", "all_dates", "all_roi", "train_roi", "forward_rows", "forward_dates", "forward_roi", "avg_ask", "avg_weight", "is_base_caps"],
                limit=12,
            ),
            "",
            "## Cap-Grid Nested WF",
            "",
            table(wf_summary, ["window", "test_days", "test_rows", "pnl_usd", "roi", "ci_low", "ci_high", "chosen_caps"]),
            "",
            "## YES Impact",
            "",
            (
                f"Adding pullback YES changes all-ROI by {pct(yes_impact['all_roi_delta'])}, "
                f"and 6/21+ forward ROI by {pct(yes_impact['forward_roi_delta'])}."
            ),
            "",
            table(
                pd.DataFrame(
                    [
                        {"slice": "no_pullback_all", **yes_impact["no_pullback_all"]},
                        {"slice": "all_v3_with_yes_all", **yes_impact["all_v3_with_yes_all"]},
                        {"slice": "no_pullback_forward", **yes_impact["no_pullback_forward"]},
                        {"slice": "all_v3_with_yes_forward", **yes_impact["all_v3_with_yes_forward"]},
                    ]
                ),
                ["slice", "rows", "dates", "cities", "win_rate", "avg_ask", "avg_weight", "pnl_usd", "roi"],
            ),
            "",
            "YES route difference rows:",
            "",
            table(
                yes_rows,
                [
                    "target_date",
                    "city",
                    "effect",
                    "yes_route",
                    "yes_payoff",
                    "yes_ask",
                    "yes_pnl",
                    "no_route",
                    "no_payoff",
                    "no_ask",
                    "no_pnl",
                    "pnl_delta_vs_no",
                ],
            ),
            "",
            "## Interpretation",
            "",
            "- `route_price_disciplined_v1` is not disproven; it is a useful shadow expression of execution discipline.",
            "- The overfit risk comes from choosing among many timing/route/size variants after seeing history, plus hand-picked price caps.",
            "- The cap-grid neighborhood says price discipline is directionally plausible.  Its nested WF is positive, but still too thin and date-sensitive for live approval.",
            "- Pullback YES should stay separate: in this candidate family it changes 3 city-days and nets +2 rows, slightly helps all-history, but slightly hurts 6/21+ and does not solve tail risk.",
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates = load_candidate_rows()
    all_routed = timing_wf.build_all_routed_candidates()
    ranks = candidate_rank_summary(candidates)
    cap_grid = cap_grid_summary(all_routed)
    cap_wf = cap_grid_walk_forward(all_routed, cap_grid)
    yes_rows, yes_impact = yes_route_impact(candidates)

    ranks.to_csv(OUT_CANDIDATE_RANKS, index=False)
    cap_grid.to_csv(OUT_CAP_GRID, index=False)
    cap_wf.to_csv(OUT_CAP_WF, index=False)
    yes_rows.to_csv(OUT_YES_DIFF, index=False)

    base_rank = ranks[ranks["candidate_id"].eq(BASE_CANDIDATE)].iloc[0].to_dict()
    base_rows = candidates[candidates["candidate_id"].eq(BASE_CANDIDATE) & candidates["router_payoff"].notna()].copy()
    base_fwd = base_rows[base_rows["target_date"].ge(FORWARD_START)].copy()
    base_low, base_high = date_bootstrap_roi(base_rows)
    fwd_low, fwd_high = date_bootstrap_roi(base_fwd)
    payload = {
        "generated_at_utc": now_utc(),
        "source": str(timing_wf.OUT_SELECTED.relative_to(ROOT)),
        "candidate_policy_rows": int(len(candidates)),
        "unique_settled_routed_rows": int(all_routed["router_payoff"].notna().sum()),
        "candidate_rank_count": int(len(ranks)),
        "base_candidate": BASE_CANDIDATE,
        "base_candidate_rank": finite(base_rank),
        "base_candidate_ci": {
            "all_ci_low": base_low,
            "all_ci_high": base_high,
            "forward_ci_low": fwd_low,
            "forward_ci_high": fwd_high,
        },
        "cap_grid_count": int(len(cap_grid)),
        "base_cap_grid_row": finite(cap_grid[cap_grid["is_base_caps"]].iloc[0].to_dict()),
        "cap_grid_walk_forward": finite(cap_wf.to_dict("records")),
        "yes_impact": finite(yes_impact),
        "verdict": "inconclusive_shadow_only",
    }
    OUT_JSON.write_text(json.dumps(finite(payload), indent=2), encoding="utf-8")
    OUT_MD.write_text(render_md(payload, cap_grid, cap_wf, ranks, yes_rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
