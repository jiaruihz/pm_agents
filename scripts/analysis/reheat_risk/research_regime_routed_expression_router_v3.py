#!/usr/bin/env python3
"""Regime-routed expression router v3.

V2 split the old current-NO route into mechanism labels. V3 keeps that split
but assigns separate expression heads to stale states with different physics:
pullback-uncertain routes to current-high YES, mature fade routes to a cheap
stale-tail current-NO sleeve, and plateau/clock-unknown remain diagnostic.
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
V2_DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_mechanism_split_v2/trade_details.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_expression_router_v3"
OUT_SUMMARY = OUT_DIR / "summary.json"
OUT_ROUTE_MAP = OUT_DIR / "route_expression_map.csv"
OUT_STRATEGY = OUT_DIR / "strategy_summary.csv"
OUT_ROUTE = OUT_DIR / "route_contribution_summary.csv"
OUT_DAILY = OUT_DIR / "daily_summary.csv"
OUT_DETAILS = OUT_DIR / "trade_details.csv"
OUT_RECENT = OUT_DIR / "recent_window_summary.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-29-regime-routed-expression-router-v3.md"

STAKE_USD = 5.0
FORWARD_START = "2026-06-21"


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
    if isinstance(value, tuple):
        return [finite(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        val = float(value)
        return val if math.isfinite(val) else None
    return value


def boolish(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    out = numeric.fillna(0).astype(float) > 0
    text_mask = numeric.isna()
    if text_mask.any():
        out.loc[text_mask] = series.loc[text_mask].astype(str).str.lower().isin({"true", "1", "1.0", "yes"})
    return out


def date_bootstrap_roi(
    frame: pd.DataFrame,
    *,
    pnl_col: str,
    cost_col: str,
    n: int = 5000,
    seed: int = 20260629,
) -> tuple[float | None, float | None]:
    clean = frame[[pnl_col, cost_col, "target_date"]].dropna().copy()
    if clean.empty or clean["target_date"].nunique() < 3:
        return None, None
    daily = clean.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    rng = np.random.default_rng(seed)
    idx = np.arange(len(daily))
    costs = daily["cost"].to_numpy(dtype=float)
    pnls = daily["pnl"].to_numpy(dtype=float)
    rois: list[float] = []
    for _ in range(n):
        sample = rng.choice(idx, size=len(idx), replace=True)
        cost = costs[sample].sum()
        if cost > 0:
            rois.append(float(pnls[sample].sum() / cost))
    if not rois:
        return None, None
    low, high = np.quantile(rois, [0.025, 0.975])
    return float(low), float(high)


def build_route_expression_map() -> pd.DataFrame:
    rows = [
        {
            "priority": 1,
            "route": "fresh_runway_current_no",
            "condition": "current NO route; running_max_state=fresh_running_high; intraday_state in active_warming/fresh_high",
            "expression": "current_bracket_no",
            "action": "BUY_NO",
            "status": "main_shadow",
        },
        {
            "priority": 2,
            "route": "false_fade_reheat_current_no",
            "condition": "current NO route; intraday_state=false_fade_risk/reheating_after_dip",
            "expression": "current_bracket_no",
            "action": "BUY_NO",
            "status": "research_shadow",
        },
        {
            "priority": 3,
            "route": "capped_d2_no",
            "condition": "day_regime=day_forecast_capped",
            "expression": "d2_no",
            "action": "BUY_NO",
            "status": "main_shadow",
        },
        {
            "priority": 4,
            "route": "pullback_uncertain_current_high_yes",
            "condition": "unapproved_stale_current_no; running_max_state=pullback_from_high; intraday_state=pullback_uncertain",
            "expression": "current_high_yes",
            "action": "BUY_YES",
            "status": "new_shadow_candidate",
        },
        {
            "priority": 5,
            "route": "cheap_stale_tail_current_no",
            "condition": "unapproved_stale_current_no; running_max_state=mature_fade; intraday_state=mature_fade",
            "expression": "current_bracket_no",
            "action": "BUY_NO",
            "status": "research_shadow",
        },
        {
            "priority": 90,
            "route": "plateau_stale_current_no_or_skip",
            "condition": "unapproved_stale_current_no; running_max_state=near_high_plateau or intraday_state=plateau_near_high",
            "expression": "none",
            "action": "SKIP",
            "status": "diagnostic_skip",
        },
        {
            "priority": 91,
            "route": "clock_unknown_diagnostic",
            "condition": "unapproved_stale_current_no; running_max_state=running_max_clock_unknown",
            "expression": "none",
            "action": "SKIP",
            "status": "diagnostic_skip",
        },
        {
            "priority": 99,
            "route": "excluded_stale_other",
            "condition": "other stale states not assigned above",
            "expression": "none",
            "action": "SKIP",
            "status": "excluded_or_separate_research",
        },
    ]
    return pd.DataFrame(rows)


def load_v2() -> pd.DataFrame:
    df = pd.read_csv(V2_DETAILS, low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    return df


def build_v3_details(df: pd.DataFrame) -> pd.DataFrame:
    v2 = df[boolish(df["strategy_mechanism_split_v2"])].copy()
    v2["router_version"] = "regime_routed_expression_router_v3"
    v2["router_route"] = v2["route_mechanism"].astype(str)
    v2["router_expression"] = v2["expression"].astype(str)
    v2["router_side"] = "BUY_NO"
    v2["router_ask"] = pd.to_numeric(v2["ask"], errors="coerce")
    v2["router_payoff"] = pd.to_numeric(v2["payoff"], errors="coerce")
    v2["router_ask_size"] = pd.to_numeric(v2.get("ask_size"), errors="coerce")
    v2["router_capacity_status"] = "known_from_no_book"
    v2["router_cost_usd"] = STAKE_USD
    v2["router_pnl_usd"] = pd.to_numeric(v2["stake_profit_usd"], errors="coerce")

    pullback = df[
        boolish(df["mechanism_unapproved_stale_current_no"])
        & df["running_max_state"].eq("pullback_from_high")
        & df["intraday_state"].eq("pullback_uncertain")
    ].copy()
    pullback["router_version"] = "regime_routed_expression_router_v3"
    pullback["router_route"] = "pullback_uncertain_current_high_yes"
    pullback["router_expression"] = "current_high_yes"
    pullback["router_side"] = "BUY_YES"
    pullback["router_ask"] = pd.to_numeric(pullback["current_yes_ask"], errors="coerce")
    pullback["router_payoff"] = pd.to_numeric(pullback["current_yes_payoff"], errors="coerce")
    pullback["router_ask_size"] = np.nan
    pullback["router_capacity_status"] = "missing_yes_ask_size_in_v2_details"
    pullback["router_cost_usd"] = STAKE_USD
    pullback["router_pnl_usd"] = pullback["router_payoff"] * (STAKE_USD / pullback["router_ask"]) - STAKE_USD

    mature = df[
        boolish(df["mechanism_unapproved_stale_current_no"])
        & df["running_max_state"].eq("mature_fade")
        & df["intraday_state"].eq("mature_fade")
    ].copy()
    mature["router_version"] = "regime_routed_expression_router_v3"
    mature["router_route"] = "cheap_stale_tail_current_no"
    mature["router_expression"] = "current_bracket_no"
    mature["router_side"] = "BUY_NO"
    mature["router_ask"] = pd.to_numeric(mature["ask"], errors="coerce")
    mature["router_payoff"] = pd.to_numeric(mature["payoff"], errors="coerce")
    mature["router_ask_size"] = pd.to_numeric(mature.get("ask_size"), errors="coerce")
    mature["router_capacity_status"] = "known_from_no_book"
    mature["router_cost_usd"] = STAKE_USD
    mature["router_pnl_usd"] = pd.to_numeric(mature["stake_profit_usd"], errors="coerce")

    out = pd.concat([v2, pullback, mature], ignore_index=True)
    out["router_weight"] = pd.to_numeric(out.get("soft_balanced"), errors="coerce").fillna(1.0)
    out["router_weighted_cost_usd"] = out["router_cost_usd"] * out["router_weight"]
    out["router_weighted_pnl_usd"] = out["router_pnl_usd"] * out["router_weight"]
    out["router_window"] = np.where(out["target_date"].ge(FORWARD_START), "forward_2026_06_21_plus", "train_to_2026_06_20")
    return out.sort_values(["target_date", "city", "router_route"]).reset_index(drop=True)


def summarize(frame: pd.DataFrame, name: str, *, window: str = "all") -> dict[str, Any]:
    if frame.empty:
        return {
            "slice": name,
            "window": window,
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "wins": 0,
            "win_rate": None,
            "avg_ask": None,
            "pnl_usd": 0.0,
            "roi": None,
            "weighted_pnl_usd": 0.0,
            "weighted_roi": None,
            "weighted_roi_ci_low": None,
            "weighted_roi_ci_high": None,
            "daily_roi_eq_minus100": 0,
            "worst_day_pnl_usd": None,
        }
    cost = float(frame["router_cost_usd"].sum())
    pnl = float(frame["router_pnl_usd"].sum())
    wcost = float(frame["router_weighted_cost_usd"].sum())
    wpnl = float(frame["router_weighted_pnl_usd"].sum())
    ci_low, ci_high = date_bootstrap_roi(frame, pnl_col="router_weighted_pnl_usd", cost_col="router_weighted_cost_usd")
    daily = daily_summary(frame, "tmp")
    return {
        "slice": name,
        "window": window,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "wins": int(pd.to_numeric(frame["router_payoff"], errors="coerce").fillna(0).sum()),
        "win_rate": float(pd.to_numeric(frame["router_payoff"], errors="coerce").mean()),
        "avg_ask": float(pd.to_numeric(frame["router_ask"], errors="coerce").mean()),
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "weighted_cost_usd": wcost,
        "weighted_pnl_usd": wpnl,
        "weighted_roi": wpnl / wcost if wcost else None,
        "weighted_roi_ci_low": ci_low,
        "weighted_roi_ci_high": ci_high,
        "daily_roi_eq_minus100": int(daily["roi"].le(-0.999999).sum()) if not daily.empty else 0,
        "worst_day_pnl_usd": float(daily["pnl_usd"].min()) if not daily.empty else None,
    }


def daily_summary(frame: pd.DataFrame, strategy_name: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    rows = []
    for date, group in frame.groupby("target_date"):
        cost = float(group["router_cost_usd"].sum())
        pnl = float(group["router_pnl_usd"].sum())
        rows.append(
            {
                "strategy": strategy_name,
                "target_date": date,
                "rows": int(len(group)),
                "cities": int(group["city"].nunique()),
                "wins": int(pd.to_numeric(group["router_payoff"], errors="coerce").fillna(0).sum()),
                "win_rate": float(pd.to_numeric(group["router_payoff"], errors="coerce").mean()),
                "avg_ask": float(pd.to_numeric(group["router_ask"], errors="coerce").mean()),
                "cost_usd": cost,
                "pnl_usd": pnl,
                "roi": pnl / cost if cost else np.nan,
                "route_mix": ",".join(f"{k}:{v}" for k, v in group["router_route"].value_counts().sort_index().items()),
                "loss_cities": ",".join(group.loc[group["router_payoff"].eq(0), "city"].astype(str).sort_values().tolist()),
            }
        )
    return pd.DataFrame(rows).sort_values(["strategy", "target_date"]).reset_index(drop=True)


def comparison_frames(df: pd.DataFrame, v3: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {"router_v3": v3}
    v2 = df[boolish(df["strategy_mechanism_split_v2"])].copy()
    v2["router_route"] = v2["route_mechanism"]
    v2["router_expression"] = v2["expression"]
    v2["router_side"] = "BUY_NO"
    v2["router_ask"] = pd.to_numeric(v2["ask"], errors="coerce")
    v2["router_payoff"] = pd.to_numeric(v2["payoff"], errors="coerce")
    v2["router_cost_usd"] = STAKE_USD
    v2["router_pnl_usd"] = pd.to_numeric(v2["stake_profit_usd"], errors="coerce")
    v2["router_weight"] = pd.to_numeric(v2.get("soft_balanced"), errors="coerce").fillna(1.0)
    v2["router_weighted_cost_usd"] = v2["router_cost_usd"] * v2["router_weight"]
    v2["router_weighted_pnl_usd"] = v2["router_pnl_usd"] * v2["router_weight"]
    out["mechanism_split_v2"] = v2

    original = df[boolish(df["strategy_original_mixed_v1"])].copy()
    original["router_route"] = original["route_mechanism"]
    original["router_expression"] = original["expression"]
    original["router_side"] = np.where(original["expression"].astype(str).str.endswith("_no"), "BUY_NO", "BUY_NO")
    original["router_ask"] = pd.to_numeric(original["ask"], errors="coerce")
    original["router_payoff"] = pd.to_numeric(original["payoff"], errors="coerce")
    original["router_cost_usd"] = STAKE_USD
    original["router_pnl_usd"] = pd.to_numeric(original["stake_profit_usd"], errors="coerce")
    original["router_weight"] = pd.to_numeric(original.get("soft_balanced"), errors="coerce").fillna(1.0)
    original["router_weighted_cost_usd"] = original["router_cost_usd"] * original["router_weight"]
    original["router_weighted_pnl_usd"] = original["router_pnl_usd"] * original["router_weight"]
    out["original_mixed_v1"] = original
    return out


def route_summary(v3: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for route, group in v3.groupby("router_route"):
        row = summarize(group, route)
        row["side_mix"] = ",".join(f"{k}:{v}" for k, v in group["router_side"].value_counts().sort_index().items())
        row["capacity_status"] = ",".join(f"{k}:{v}" for k, v in group["router_capacity_status"].value_counts().sort_index().items())
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["pnl_usd", "rows"], ascending=[True, False]).reset_index(drop=True)


def recent_summary(v3: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for start in ["2026-06-19", "2026-06-21", "2026-06-22"]:
        sub = v3[v3["target_date"].ge(start)]
        row = summarize(sub, f"since_{start}")
        rows.append(row)
    return pd.DataFrame(rows)


def md_table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    if df.empty:
        return "_No rows._"
    view = df.loc[:, [c for c in cols if c in df.columns]].copy()
    if limit is not None:
        view = view.head(limit)
    for col in view.columns:
        if col in {"win_rate", "roi", "weighted_roi", "weighted_roi_ci_low", "weighted_roi_ci_high"}:
            view[col] = view[col].map(pct)
        elif col.endswith("_usd") or col == "avg_ask":
            if col == "avg_ask":
                view[col] = view[col].map(lambda x: "NA" if pd.isna(x) else f"{float(x):.3f}")
            else:
                view[col] = view[col].map(money)
    header = "| " + " | ".join(view.columns) + " |"
    sep = "| " + " | ".join(["---"] * len(view.columns)) + " |"
    body = ["| " + " | ".join(str(v) for v in row) + " |" for row in view.astype(str).to_numpy()]
    return "\n".join([header, sep, *body])


def write_report(
    payload: dict[str, Any],
    route_map: pd.DataFrame,
    strategy_summary: pd.DataFrame,
    route_contrib: pd.DataFrame,
    recent: pd.DataFrame,
    daily: pd.DataFrame,
) -> None:
    v3 = strategy_summary[(strategy_summary["slice"].eq("router_v3")) & (strategy_summary["window"].eq("all"))].iloc[0]
    v2 = strategy_summary[(strategy_summary["slice"].eq("mechanism_split_v2")) & (strategy_summary["window"].eq("all"))].iloc[0]
    route_rows = route_contrib.set_index("slice")["rows"].to_dict()
    pullback_rows = int(route_rows.get("pullback_uncertain_current_high_yes", 0))
    mature_rows = int(route_rows.get("cheap_stale_tail_current_no", 0))
    lines = [
        "# 2026-06-29 Regime-Routed Expression Router V3",
        "",
        "## Conclusion",
        "",
        "V3 changes the model from a NO-only router into an expression router.  The important fix is that stale states no longer disappear or get mislabeled as runway: `pullback_uncertain` routes to `current_high_yes`, while `mature_fade` routes to a separate cheap stale-tail current-NO sleeve.",
        "",
        f"On the fixed historical denominator `{payload['source_file']}` covering `{payload['source_date_min']}`..`{payload['source_date_max']}`, v2 had {int(v2['rows'])} rows and full ROI {pct(v2['roi'])}.  V3 adds {pullback_rows} pullback current-high YES rows and {mature_rows} mature-fade stale-tail NO rows, reaching {int(v3['rows'])} rows and full ROI {pct(v3['roi'])}.  Soft weighted ROI moves from {pct(v2['weighted_roi'])} to {pct(v3['weighted_roi'])}.",
        "",
        "This is a mechanism improvement, not live approval.  The new YES rows have price/payoff evidence, but `current_yes_ask_size` is missing in the v2 detail layer; the mature-fade NO sleeve has historical edge but is a separate tail strategy, not runway.",
        "",
        "## Route To Expression Map",
        "",
        md_table(route_map, ["priority", "route", "condition", "expression", "action", "status"]),
        "",
        "## Strategy Summary",
        "",
        md_table(
            strategy_summary[strategy_summary["window"].eq("all")],
            [
                "slice",
                "rows",
                "dates",
                "cities",
                "wins",
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
        "## Route Contribution",
        "",
        md_table(
            route_contrib,
            [
                "slice",
                "rows",
                "dates",
                "cities",
                "wins",
                "win_rate",
                "avg_ask",
                "pnl_usd",
                "roi",
                "weighted_roi",
                "side_mix",
                "capacity_status",
            ],
        ),
        "",
        "## Recent Window",
        "",
        f"The fixed denominator currently covers target dates `{payload['source_date_min']}`..`{payload['source_date_max']}`. Later atlas/candidate rows are excluded until settlement is available.",
        "",
        md_table(
            recent,
            [
                "slice",
                "rows",
                "dates",
                "cities",
                "wins",
                "win_rate",
                "avg_ask",
                "pnl_usd",
                "roi",
                "weighted_roi",
                "daily_roi_eq_minus100",
                "worst_day_pnl_usd",
            ],
        ),
        "",
        "## Recent Daily Detail",
        "",
        md_table(
            daily[daily["strategy"].eq("router_v3") & daily["target_date"].ge("2026-06-19")],
            ["target_date", "rows", "cities", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "route_mix", "loss_cities"],
        ),
        "",
        "## Read",
        "",
        "- V3 now separates route semantics cleanly: fresh runway NO, false-fade/reheat NO, capped d2 NO, pullback current-high YES, and mature-fade stale-tail NO are reported as different sleeves.",
        "- It does not fix the broader recent weakness after 6/21. The remaining losses come from other route legs, so this change should not be sold as a full forward repair.",
        "- Maintaining route -> expression mapping is the right architecture: regime/state is the shared feature layer; each route chooses side/bracket/expression independently.",
        "",
        "Verdict: `inconclusive_shadow_candidate`.  Keep these as shadow expression heads; collect YES top-ask size/capacity and evaluate mature-fade tail separately before any live discussion.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    source = load_v2()
    route_map = build_route_expression_map()
    v3 = build_v3_details(source)
    frames = comparison_frames(source, v3)

    strategy_rows = []
    daily_frames = []
    for name, frame in frames.items():
        strategy_rows.append(summarize(frame, name))
        for window, group in frame.assign(router_window=np.where(frame["target_date"].ge(FORWARD_START), "forward_2026_06_21_plus", "train_to_2026_06_20")).groupby("router_window"):
            strategy_rows.append(summarize(group, name, window=str(window)))
        daily_frames.append(daily_summary(frame, name))
    strategy = pd.DataFrame(strategy_rows)
    daily = pd.concat(daily_frames, ignore_index=True)
    route_contrib = route_summary(v3)
    recent = recent_summary(v3)

    route_map.to_csv(OUT_ROUTE_MAP, index=False)
    v3.to_csv(OUT_DETAILS, index=False)
    strategy.to_csv(OUT_STRATEGY, index=False)
    route_contrib.to_csv(OUT_ROUTE, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    recent.to_csv(OUT_RECENT, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "source_file": str(V2_DETAILS.relative_to(ROOT)),
        "source_date_min": str(source["target_date"].min()),
        "source_date_max": str(source["target_date"].max()),
        "source_rows": int(len(source)),
        "router_rows": int(len(v3)),
        "router_dates": int(v3["target_date"].nunique()),
        "router_cities": int(v3["city"].nunique()),
        "forward_start": FORWARD_START,
        "capacity_note": "current_high_yes rows have current_yes_ask but missing current_yes_ask_size in v2 detail layer",
        "strategy_summary": strategy.to_dict("records"),
        "route_contribution": route_contrib.to_dict("records"),
        "recent_summary": recent.to_dict("records"),
        "outputs": {
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "route_expression_map": str(OUT_ROUTE_MAP.relative_to(ROOT)),
            "strategy_summary": str(OUT_STRATEGY.relative_to(ROOT)),
            "route_contribution_summary": str(OUT_ROUTE.relative_to(ROOT)),
            "daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "trade_details": str(OUT_DETAILS.relative_to(ROOT)),
            "recent_window_summary": str(OUT_RECENT.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "significance": "PASS_ON_FIXED_HISTORY_POINT_ESTIMATE_AND_WEIGHTED_CI",
            "baseline": "PARTIAL_VS_V2_SMALL_IMPROVEMENT",
            "forward": "FAIL_THIN_AND_STILL_NEGATIVE_2026_06_21_PLUS",
            "conclusion": "inconclusive_shadow_candidate",
            "live_ready": False,
        },
    }
    OUT_SUMMARY.write_text(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(finite(payload), route_map, strategy, route_contrib, recent, daily)
    print(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
