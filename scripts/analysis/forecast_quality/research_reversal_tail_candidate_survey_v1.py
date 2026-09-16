#!/usr/bin/env python3
"""Survey existing reversal/tail research heads into one candidate map.

This is not a new selector optimizer.  It reads prior durable artifacts and
summarizes which reversal/tail ideas are worth forwarding as shadow tags.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-01-reversal-tail-candidate-survey-v1.md"
OUT_CSV = ROOT / "docs/analysis/2026-07/generated/reversal_tail_candidate_survey_v1/candidates.csv"

V3_SUMMARY = ROOT / "docs/analysis/2026-07/generated/reversal_archetype_selector_v3/archetype_summary.csv"
MISROUTE_SLICE = ROOT / "docs/analysis/2026-06/generated/current_high_yes_from_regime_no_misroutes_v0/slice_summary.csv"
LOW_PRICE_ROWS = ROOT / "docs/analysis/2026-06/generated/low_price_yes_reheat_reversal_v1/scored_rows.csv"
HIGH_PRICE_SUMMARY = ROOT / "docs/analysis/2026-06/generated/high_price_forecast_bias_reversal_cases_v1/high_price_reversal_summary.csv"
ROUTE_SUMMARY = ROOT / "docs/analysis/2026-06/generated/regime_routed_expression_router_v3_live_like_entry_v1/route_summary.csv"
EVENT_ROWS = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv"


def pct(v: Any) -> str:
    try:
        x = float(v)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"{100 * x:+.1f}%"


def num(v: Any, digits: int = 2) -> str:
    try:
        x = float(v)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"{x:.{digits}f}"


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]]) -> str:
    if df.empty:
        return "_No rows._"
    pct_cols = {
        "roi",
        "ci_low",
        "ci_high",
        "baseline_roi",
        "excess_roi",
        "win_rate",
        "holdout_roi",
        "recent_roi",
        "reversal_rate",
        "opposite_roi",
    }
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df.iterrows():
        vals: list[str] = []
        for key, _label in cols:
            val = row.get(key, "")
            if key in pct_cols:
                vals.append(pct(val))
            elif key in {"rows", "dates", "cities"} and pd.notna(val):
                vals.append(str(int(val)))
            elif key in {"avg_ask"}:
                vals.append(num(val, 3))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def add_candidate(out: list[dict[str, Any]], **kwargs: Any) -> None:
    out.append(kwargs)


def low_price_selector_summary(df: pd.DataFrame, name: str, mask: pd.Series, period: str) -> dict[str, Any]:
    g = df[mask & df["period"].eq(period)].copy()
    if g.empty:
        return {
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "win_rate": float("nan"),
            "avg_ask": float("nan"),
            "roi": float("nan"),
        }
    return {
        "rows": int(len(g)),
        "dates": int(g["target_date"].nunique()),
        "cities": int(g["city"].nunique()),
        "win_rate": float(g["target_yes_wins"].mean()),
        "avg_ask": float(g["target_yes_ask"].mean()),
        "roi": float(g["target_yes_pnl"].sum() / g["target_yes_ask"].sum()),
        "selector": name,
    }


def bootstrap_roi_ci(daily: pd.DataFrame, *, pnl_col: str = "pnl", cost_col: str = "cost") -> tuple[float, float]:
    if len(daily) < 3:
        return (float("nan"), float("nan"))
    rng = pd.Series(range(3000)).sample(frac=1.0, random_state=20260701).to_numpy()
    costs = daily[cost_col].to_numpy(float)
    pnls = daily[pnl_col].to_numpy(float)
    vals: list[float] = []
    gen = __import__("numpy").random.default_rng(20260701)
    for _ in rng:
        idx = gen.integers(0, len(daily), len(daily))
        cost = costs[idx].sum()
        if cost > 0:
            vals.append(float(pnls[idx].sum() / cost))
    if not vals:
        return (float("nan"), float("nan"))
    q = __import__("numpy").quantile(vals, [0.025, 0.975])
    return (float(q[0]), float(q[1]))


def expanded_pullback_summary(period: str) -> dict[str, Any]:
    df = pd.read_csv(EVENT_ROWS, low_memory=False)
    for col in [
        "current_yes_ask",
        "current_yes_payoff",
        "current_bracket_no_ask",
        "current_bracket_no_payoff",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    g = df[
        df["intraday_state"].eq("pullback_uncertain")
        & df["current_yes_ask"].between(0.50, 0.90)
        & df["current_bracket_no_ask"].ge(0.40)
        & df["current_yes_payoff"].notna()
        & df["current_bracket_no_payoff"].notna()
    ].copy()
    if period == "holdout":
        g = g[g["target_date"].astype(str).ge("2026-06-13")]
    elif period == "recent":
        g = g[g["target_date"].astype(str).ge("2026-06-21")]
    elif period == "train":
        g = g[g["target_date"].astype(str).lt("2026-06-13")]
    if g.empty:
        return {
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "avg_ask": float("nan"),
            "win_rate": float("nan"),
            "roi": float("nan"),
            "ci_low": float("nan"),
            "ci_high": float("nan"),
            "baseline_roi": float("nan"),
            "excess_roi": float("nan"),
        }
    h = g.sort_values(["target_date", "city", "decision_snapshot_ts_utc"]).drop_duplicates(["target_date", "city"])
    h["cost"] = 5.0
    h["pnl"] = h["current_yes_payoff"] * (5.0 / h["current_yes_ask"]) - 5.0
    h["baseline_cost"] = 5.0
    h["baseline_pnl"] = h["current_bracket_no_payoff"] * (5.0 / h["current_bracket_no_ask"]) - 5.0
    daily = h.groupby("target_date", as_index=False).agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    ci = bootstrap_roi_ci(daily)
    roi = float(h["pnl"].sum() / h["cost"].sum())
    baseline_roi = float(h["baseline_pnl"].sum() / h["baseline_cost"].sum())
    return {
        "rows": int(len(h)),
        "dates": int(h["target_date"].nunique()),
        "cities": int(h["city"].nunique()),
        "avg_ask": float(h["current_yes_ask"].mean()),
        "win_rate": float(h["current_yes_payoff"].mean()),
        "roi": roi,
        "ci_low": ci[0],
        "ci_high": ci[1],
        "baseline_roi": baseline_roi,
        "excess_roi": roi - baseline_roi,
    }


def build_candidates() -> pd.DataFrame:
    out: list[dict[str, Any]] = []

    v3 = pd.read_csv(V3_SUMMARY)
    best = v3[(v3["period"].eq("full")) & (v3["archetype"].eq("capped_or_low_runway_current_hold"))].iloc[0]
    holdout = v3[(v3["period"].eq("holdout")) & (v3["archetype"].eq("capped_or_low_runway_current_hold"))].iloc[0]
    recent = v3[(v3["period"].eq("recent")) & (v3["archetype"].eq("capped_or_low_runway_current_hold"))].iloc[0]
    add_candidate(
        out,
        rank=1,
        candidate="high_current_no_reverse_current_yes",
        expression="buy_current_yes",
        source_artifact="reversal_archetype_selector_v3",
        rows=best["rows"],
        dates=best["dates"],
        cities=best["cities"],
        avg_ask=best["avg_ask"],
        win_rate=best["win_rate"],
        roi=best["roi"],
        ci_low=best["roi_ci_low"],
        ci_high=best["roi_ci_high"],
        baseline_roi=best["baseline_roi"],
        excess_roi=best["excess_roi"],
        holdout_roi=holdout["roi"],
        recent_roi=recent["roi"],
        status="shadow_tag",
        read="Best current executable reversal shape, but 7% ROI and recent miss are not enough for live.",
    )

    broad = v3[(v3["period"].eq("full")) & (v3["archetype"].eq("current_no_overconfidence_all"))].iloc[0]
    broad_h = v3[(v3["period"].eq("holdout")) & (v3["archetype"].eq("current_no_overconfidence_all"))].iloc[0]
    broad_r = v3[(v3["period"].eq("recent")) & (v3["archetype"].eq("current_no_overconfidence_all"))].iloc[0]
    add_candidate(
        out,
        rank=2,
        candidate="broad_current_no_overconfidence_inverse",
        expression="buy_current_yes",
        source_artifact="reversal_archetype_selector_v3",
        rows=broad["rows"],
        dates=broad["dates"],
        cities=broad["cities"],
        avg_ask=broad["avg_ask"],
        win_rate=broad["win_rate"],
        roi=broad["roi"],
        ci_low=broad["roi_ci_low"],
        ci_high=broad["roi_ci_high"],
        baseline_roi=broad["baseline_roi"],
        excess_roi=broad["excess_roi"],
        holdout_roi=broad_h["roi"],
        recent_roi=broad_r["roi"],
        status="diagnostic_baseline",
        read="Useful as old-strategy wrong-way detector; standalone ROI is too low.",
    )

    pull_exp = expanded_pullback_summary("full")
    pull_exp_h = expanded_pullback_summary("holdout")
    pull_exp_r = expanded_pullback_summary("recent")
    add_candidate(
        out,
        rank=3,
        candidate="expanded_pullback_uncertain_current_high_yes",
        expression="buy_current_high_yes",
        source_artifact="current_yes_peak_yes_execution_timing_v1 matrix",
        rows=pull_exp["rows"],
        dates=pull_exp["dates"],
        cities=pull_exp["cities"],
        avg_ask=pull_exp["avg_ask"],
        win_rate=pull_exp["win_rate"],
        roi=pull_exp["roi"],
        ci_low=pull_exp["ci_low"],
        ci_high=pull_exp["ci_high"],
        baseline_roi=pull_exp["baseline_roi"],
        excess_roi=pull_exp["excess_roi"],
        holdout_roi=pull_exp_h["roi"],
        recent_roi=pull_exp_r["roi"],
        status="mechanism_candidate_too_thin",
        read="Expanded Jeddah-style pullback test; promising point estimate but only 12 city-date rows.",
    )

    mis = pd.read_csv(MISROUTE_SLICE)
    pull_yes = mis[(mis["slice"].eq("pullback_uncertain")) & (mis["side"].eq("yes"))].iloc[0]
    pull_no = mis[(mis["slice"].eq("pullback_uncertain")) & (mis["side"].eq("no"))].iloc[0]
    add_candidate(
        out,
        rank=4,
        candidate="pullback_uncertain_current_high_yes",
        expression="buy_current_high_yes",
        source_artifact="current_high_yes_from_regime_no_misroutes_v0",
        rows=pull_yes["rows"],
        dates=pull_yes["dates"],
        cities=pull_yes["cities"],
        avg_ask=pull_yes["avg_ask"],
        win_rate=pull_yes["win_rate"],
        roi=pull_yes["roi"],
        ci_low=pull_yes["roi_ci_low"],
        ci_high=pull_yes["roi_ci_high"],
        baseline_roi=pull_no["roi"],
        excess_roi=pull_yes["roi"] - pull_no["roi"],
        holdout_roi=float("nan"),
        recent_roi=float("nan"),
        status="mechanism_candidate_too_thin",
        read="Mechanism is clean but only 7 rows; rerun on expanded PIT expression matrix before live.",
    )

    low = pd.read_csv(LOW_PRICE_ROWS, low_memory=False)
    mask = (
        low["target_yes_ask"].le(0.25)
        & low["distance_bucket"].isin(["d1", "d2"])
        & low["reheat_adjusted_edge"].ge(0.08)
    )
    low_train = low_price_selector_summary(low, "d1d2_adjusted_edge_ge_0.08", mask, "train")
    low_holdout = low_price_selector_summary(low, "d1d2_adjusted_edge_ge_0.08", mask, "holdout")
    add_candidate(
        out,
        rank=5,
        candidate="low_price_yes_reheat_reversal_d1d2",
        expression="buy_low_price_target_yes",
        source_artifact="low_price_yes_reheat_reversal_v1",
        rows=low_train["rows"] + low_holdout["rows"],
        dates=low["target_date"][mask].nunique(),
        cities=low["city"][mask].nunique(),
        avg_ask=float(low.loc[mask, "target_yes_ask"].mean()),
        win_rate=float(low.loc[mask, "target_yes_wins"].mean()),
        roi=float(low.loc[mask, "target_yes_pnl"].sum() / low.loc[mask, "target_yes_ask"].sum()),
        ci_low=float("nan"),
        ci_high=float("nan"),
        baseline_roi=float("nan"),
        excess_roi=float("nan"),
        holdout_roi=low_holdout["roi"],
        recent_roi=float("nan"),
        status="zero_notional_shadow",
        read="Lottery-like convexity; holdout positive but top-day and baseline stability are unresolved.",
    )

    hp = pd.read_csv(HIGH_PRICE_SUMMARY)
    hp_no = hp[(hp["threshold"].eq(">=0.70")) & (hp["expression"].eq("current_bracket_no"))].iloc[0]
    add_candidate(
        out,
        rank=6,
        candidate="high_price_current_no_case_mining",
        expression="buy_opposite_current_yes",
        source_artifact="high_price_forecast_bias_reversal_cases_v1",
        rows=hp_no["rows"],
        dates=hp_no["dates"],
        cities=hp_no["cities"],
        avg_ask=hp_no["avg_opposite_ask"],
        win_rate=hp_no["cheap_opposite_hit_rate"],
        roi=hp_no["opposite_roi"],
        ci_low=hp_no["opposite_roi_ci_low"],
        ci_high=hp_no["opposite_roi_ci_high"],
        baseline_roi=hp_no["token_roi"],
        excess_roi=hp_no["opposite_roi"] - hp_no["token_roi"],
        holdout_roi=float("nan"),
        recent_roi=float("nan"),
        status="case_mining_only",
        read="High-price NO often loses, but opposite side only breaks even before PIT narrowing.",
    )

    route = pd.read_csv(ROUTE_SUMMARY)
    cheap = route[
        route["entry_policy"].eq("fixed_noon_priority")
        & route["slice"].eq("cheap_stale_tail_current_no")
        & route["window"].eq("forward_2026_06_21_plus")
    ].iloc[0]
    add_candidate(
        out,
        rank=7,
        candidate="cheap_stale_tail_current_no",
        expression="buy_current_no",
        source_artifact="regime_routed_expression_router_v3_live_like_entry_v1",
        rows=cheap["rows"],
        dates=cheap["dates"],
        cities=cheap["cities"],
        avg_ask=cheap["avg_ask"],
        win_rate=cheap["win_rate"],
        roi=cheap["roi"],
        ci_low=float("nan"),
        ci_high=float("nan"),
        baseline_roi=float("nan"),
        excess_roi=float("nan"),
        holdout_roi=cheap["roi"],
        recent_roi=cheap["roi"],
        status="too_thin_do_not_chase",
        read="Point estimate is high but forward sample is only 2 rows; keep as label, not a rule.",
    )

    return pd.DataFrame(out).sort_values("rank")


def render_report(candidates: pd.DataFrame) -> str:
    prompt = (
        "When original strategy wants to buy high current_bracket NO, tag a shadow inverse if "
        "current_bracket_no_ask>=0.70, current_high_yes_ask<=0.50, and PIT state is capped/low-runway "
        "or otherwise conflicted.  Same denominator backtest: original current-NO baseline ROI -26.4%; "
        "inverse current-YES ROI +7.4%; excess +33.8%.  This is a shadow diagnostic, not live approval."
    )
    cols = [
        ("rank", "#"),
        ("candidate", "candidate"),
        ("expression", "expression"),
        ("rows", "rows"),
        ("dates", "dates"),
        ("cities", "cities"),
        ("avg_ask", "ask"),
        ("win_rate", "win"),
        ("roi", "ROI"),
        ("ci_low", "CI low"),
        ("ci_high", "CI high"),
        ("baseline_roi", "baseline"),
        ("excess_roi", "excess"),
        ("holdout_roi", "holdout"),
        ("recent_roi", "recent"),
        ("status", "status"),
    ]
    lines = [
        "# Reversal / Tail Candidate Survey v1",
        "",
        "Generated: 2026-07-01",
        "",
        "## Prompt For Original Strategy Shadow Run",
        "",
        prompt,
        "",
        "## Verdict",
        "",
        "`high_current_no_reverse_current_yes` is the only broad current executable direction worth forwarding now, but it is a shadow tag, not a live strategy.  The cleanest sharper mechanism is `expanded_pullback_uncertain_current_high_yes`, which improves point ROI but remains too thin.  The low-price tail ideas are lottery-like with unresolved concentration.",
        "",
        "## Candidate Map",
        "",
        md_table(candidates, cols),
        "",
        "## Read",
        "",
        "- The current 7.4% ROI is low; the reason to keep the line is the same-row direction flip: current-NO baseline -26.4% versus inverse current-YES +7.4%.",
        "- `expanded_pullback_uncertain_current_high_yes` is mechanically cleaner than the broad v3 slice: current-high YES +21.5% versus same-row NO baseline -39.0%, but only 12 city-date rows.",
        "- The older 7-row `pullback_uncertain_current_high_yes` case study remains useful as mechanism evidence, not as a standalone rule.",
        "- Low-price YES has genuine convexity, but prior reports show top-day concentration and weak forward baseline excess.  It belongs in zero-notional telemetry, not live.",
        "- High-price NO case mining confirms the failure mode exists, but raw opposite current YES is roughly breakeven until narrowed by PIT state.",
        "- Do not connect this to the regime-routed runner as a live expression selector.  Run it as independent shadow labels over the old strategy's decisions.",
        "",
        "## Artifacts Read",
        "",
        f"- `{V3_SUMMARY.relative_to(ROOT)}`",
        f"- `{MISROUTE_SLICE.relative_to(ROOT)}`",
        f"- `{LOW_PRICE_ROWS.relative_to(ROOT)}`",
        f"- `{HIGH_PRICE_SUMMARY.relative_to(ROOT)}`",
        f"- `{ROUTE_SUMMARY.relative_to(ROOT)}`",
        f"- `{EVENT_ROWS.relative_to(ROOT)}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    candidates = build_candidates()
    candidates.to_csv(OUT_CSV, index=False)
    OUT_MD.write_text(render_report(candidates), encoding="utf-8")
    print(f"wrote {OUT_MD.relative_to(ROOT)}")
    print(f"wrote {OUT_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
