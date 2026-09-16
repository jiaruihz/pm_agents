#!/usr/bin/env python3
"""Robustness audit for low-price YES tail p_cal/station-basis selectors.

This script does not retrain models and does not propose a live change.  It
stress-tests the already frozen p_cal v1 artifacts:

* leave-one-city / leave-one-region / leave-one-date robustness
* selected-vs-complement comparison on the same v1 denominator
* city and region contribution concentration
* station-basis bucket diagnostics
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
PCAL_DETAILS = ROOT / "docs/analysis/2026-07/generated/low_price_yes_tail_pcal_v1/details.csv"
BIAS_ROWS = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_tail_robustness_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-02-low-price-yes-tail-robustness-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-02-low-price-yes-tail-robustness-v1.json"

HOLDOUT_START = "2026-06-21"
RNG_SEED = 20260702

FOCUS_STRATEGIES = [
    "v1_edge20_baseline_all",
    "v1_edge20_v1_edge20_no_city_ev_ge_0.2",
    "v1_edge20_v1_edge20_no_city_ev_ge_0.5",
    "v1_edge20_v1_edge20_city_diag_ev_ge_0.2",
    "v1_edge20_v1_edge20_city_diag_ev_ge_0.5",
    "station_hot_tail_high",
    "station_bias_p90_high",
    "station_bias_p90_high_and_no_city_ev_ge_0",
]


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: Any) -> str:
    try:
        x = float(value)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"{100 * x:+.1f}%"


def money(value: Any) -> str:
    try:
        x = float(value)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"${x:+.2f}"


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 60) -> str:
    if df.empty:
        return "_No rows._"
    pct_cols = {
        "roi",
        "win_rate",
        "roi_ci_low",
        "roi_ci_high",
        "top5_removed_roi",
        "top10_removed_roi",
        "min_leave_city_roi",
        "min_leave_region_roi",
        "min_leave_date_roi",
        "median_leave_city_roi",
        "median_leave_region_roi",
        "median_leave_date_roi",
        "positive_leave_city_frac",
        "positive_leave_region_frac",
        "positive_leave_date_frac",
        "roi_delta_vs_complement",
    }
    money_cols = {"pnl", "cost", "max_daily_loss", "pnl_per_active_day"}
    int_cols = {"rows", "dates", "cities", "regions", "wins", "losing_days", "roi_le_minus_50_days"}
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df.head(max_rows).iterrows():
        vals: list[str] = []
        for key, _ in cols:
            val = row.get(key)
            if key in pct_cols or key.endswith("_roi") or key.endswith("_frac") or "roi_ci" in key:
                vals.append(pct(val))
            elif key in money_cols or key.endswith("_pnl") or key.endswith("_loss"):
                vals.append(money(val))
            elif key in int_cols and pd.notna(val):
                vals.append(str(int(val)))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "")
            else:
                vals.append("" if val is None or pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def bootstrap_ci(daily: pd.DataFrame, n_boot: int = 5000) -> tuple[float | None, float | None]:
    if len(daily) < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    costs = daily["cost"].to_numpy(float)
    pnls = daily["pnl"].to_numpy(float)
    vals: list[float] = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(daily), len(daily))
        cost = float(costs[idx].sum())
        if cost > 0:
            vals.append(float(pnls[idx].sum() / cost))
    lo, hi = np.quantile(vals, [0.025, 0.975])
    return (float(lo), float(hi))


def region_map() -> pd.DataFrame:
    bias = pd.read_csv(BIAS_ROWS, low_memory=False, usecols=["city", "region", "city_pool"])
    rows = []
    for city, g in bias.dropna(subset=["city"]).groupby("city"):
        region = g["region"].dropna().mode()
        city_pool = g["city_pool"].dropna().mode()
        rows.append(
            {
                "city": city,
                "region": str(region.iloc[0]) if not region.empty else "unknown",
                "city_pool": str(city_pool.iloc[0]) if not city_pool.empty else "unknown",
            }
        )
    return pd.DataFrame(rows)


def load_rows() -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = pd.read_csv(PCAL_DETAILS, low_memory=False)
    raw = raw[raw["dataset"].eq("v1_edge20")].copy()
    nums = [
        "ask",
        "payoff",
        "pnl",
        "cost",
        "win",
        "p_cal",
        "p_cal_ev",
        "bias_p90",
        "hot_tail_pct",
        "bias_mean",
    ]
    for col in nums:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw["target_date"] = raw["target_date"].astype(str)
    raw["period2"] = np.select(
        [
            raw["period"].eq("forward"),
            raw["period"].eq("historical") & raw["target_date"].ge(HOLDOUT_START),
        ],
        ["forward_2026_06_27_30", "holdout_2026_06_21_26"],
        default="train_pre_2026_06_21",
    )
    raw = raw.merge(region_map(), on="city", how="left")
    raw["region"] = raw["region"].fillna("unknown")

    baseline = raw[raw["strategy"].eq("v1_edge20_baseline_all")].drop_duplicates(["candidate_id"]).copy()
    train = baseline[baseline["period2"].eq("train_pre_2026_06_21")]
    p90_q66 = float(train["bias_p90"].quantile(0.66))
    hot_q66 = float(train["hot_tail_pct"].quantile(0.66))
    derived = []
    for strategy, mask in {
        "station_hot_tail_high": baseline["hot_tail_pct"] > hot_q66,
        "station_bias_p90_high": baseline["bias_p90"] > p90_q66,
        "station_bias_p90_high_and_no_city_ev_ge_0": (baseline["bias_p90"] > p90_q66) & (baseline["p_cal_ev"] >= 0.0),
    }.items():
        g = baseline[mask].copy()
        g["strategy"] = strategy
        derived.append(g)

    focus = raw[raw["strategy"].isin(FOCUS_STRATEGIES[:5])].copy()
    details = pd.concat([focus, *derived], ignore_index=True, sort=False)
    details = details[details["strategy"].isin(FOCUS_STRATEGIES)].copy()
    return details, baseline


def summarize(frame: pd.DataFrame, strategy: str, period: str) -> dict[str, Any]:
    out: dict[str, Any] = {"strategy": strategy, "period": period}
    if frame.empty:
        out.update({"rows": 0, "dates": 0, "cities": 0, "regions": 0})
        return out
    daily = frame.groupby("target_date", as_index=False).agg(
        rows=("pnl", "size"),
        wins=("win", "sum"),
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
    )
    daily["roi"] = daily["pnl"] / daily["cost"]
    ci_low, ci_high = bootstrap_ci(daily)
    sorted_pnl = frame.sort_values("pnl", ascending=False)

    def top_removed(n: int) -> float | None:
        rem = sorted_pnl.iloc[n:]
        if rem.empty or float(rem["cost"].sum()) <= 0:
            return None
        return float(rem["pnl"].sum() / rem["cost"].sum())

    out.update(
        {
            "rows": int(len(frame)),
            "dates": int(frame["target_date"].nunique()),
            "cities": int(frame["city"].nunique()),
            "regions": int(frame["region"].nunique()),
            "wins": int(frame["win"].sum()),
            "win_rate": float(frame["win"].mean()),
            "avg_ask": float(frame["ask"].mean()),
            "cost": float(frame["cost"].sum()),
            "pnl": float(frame["pnl"].sum()),
            "roi": float(frame["pnl"].sum() / frame["cost"].sum()),
            "roi_ci_low": ci_low,
            "roi_ci_high": ci_high,
            "top5_removed_roi": top_removed(5),
            "top10_removed_roi": top_removed(10),
            "losing_days": int((daily["pnl"] < 0).sum()),
            "roi_le_minus_50_days": int((daily["roi"] <= -0.50).sum()),
            "max_daily_loss": float(daily["pnl"].min()),
            "pnl_per_active_day": float(frame["pnl"].sum() / frame["target_date"].nunique()),
        }
    )
    return out


def stress_group(frame: pd.DataFrame, group_col: str) -> dict[str, Any]:
    if frame.empty or frame[group_col].nunique() <= 1:
        return {}
    vals = []
    for group_value in sorted(frame[group_col].dropna().unique()):
        rem = frame[frame[group_col] != group_value]
        if rem.empty or float(rem["cost"].sum()) <= 0:
            continue
        vals.append(
            {
                "left_out": group_value,
                "roi": float(rem["pnl"].sum() / rem["cost"].sum()),
                "rows": int(len(rem)),
            }
        )
    if not vals:
        return {}
    s = pd.DataFrame(vals)
    min_row = s.sort_values("roi").iloc[0]
    return {
        f"min_leave_{group_col}_roi": float(min_row["roi"]),
        f"min_leave_{group_col}_value": str(min_row["left_out"]),
        f"median_leave_{group_col}_roi": float(s["roi"].median()),
        f"positive_leave_{group_col}_frac": float((s["roi"] > 0).mean()),
    }


def summarize_all(details: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for strategy, g in details.groupby("strategy", sort=False):
        for period in ["train_pre_2026_06_21", "holdout_2026_06_21_26", "forward_2026_06_27_30"]:
            frame = g[g["period2"].eq(period)]
            row = summarize(frame, strategy, period)
            row.update(stress_group(frame, "city"))
            row.update(stress_group(frame, "region"))
            row.update(stress_group(frame, "target_date"))
            rows.append(row)
    return pd.DataFrame(rows)


def contributions(details: pd.DataFrame, group_col: str) -> pd.DataFrame:
    rows = []
    for strategy, sg in details.groupby("strategy", sort=False):
        for period, pg in sg.groupby("period2", sort=False):
            total_pnl = float(pg["pnl"].sum())
            g = pg.groupby(group_col, dropna=False).agg(
                rows=("pnl", "size"),
                dates=("target_date", "nunique"),
                cities=("city", "nunique"),
                cost=("cost", "sum"),
                pnl=("pnl", "sum"),
                wins=("win", "sum"),
                win_rate=("win", "mean"),
                avg_ask=("ask", "mean"),
            )
            g["roi"] = g["pnl"] / g["cost"]
            g["pnl_share"] = g["pnl"] / total_pnl if total_pnl else np.nan
            g = g.reset_index()
            g["strategy"] = strategy
            g["period"] = period
            rows.append(g)
    return pd.concat(rows, ignore_index=True, sort=False) if rows else pd.DataFrame()


def complement_summary(details: pd.DataFrame, baseline: pd.DataFrame) -> pd.DataFrame:
    rows = []
    base_keys_by_period = {
        period: set(g["candidate_id"])
        for period, g in baseline.groupby("period2")
    }
    for strategy, sg in details.groupby("strategy", sort=False):
        if strategy == "v1_edge20_baseline_all":
            continue
        for period in ["train_pre_2026_06_21", "holdout_2026_06_21_26", "forward_2026_06_27_30"]:
            selected = sg[sg["period2"].eq(period)]
            base_period = baseline[baseline["period2"].eq(period)]
            selected_keys = set(selected["candidate_id"])
            base_keys = base_keys_by_period.get(period, set())
            complement = base_period[base_period["candidate_id"].isin(base_keys - selected_keys)]
            sel_sum = summarize(selected, strategy, period)
            comp_sum = summarize(complement, f"{strategy}_complement", period)
            rows.append(
                {
                    "strategy": strategy,
                    "period": period,
                    "selected_rows": sel_sum.get("rows"),
                    "selected_roi": sel_sum.get("roi"),
                    "complement_rows": comp_sum.get("rows"),
                    "complement_roi": comp_sum.get("roi"),
                    "roi_delta_vs_complement": (
                        float(sel_sum["roi"] - comp_sum["roi"])
                        if pd.notna(sel_sum.get("roi")) and pd.notna(comp_sum.get("roi"))
                        else np.nan
                    ),
                }
            )
    return pd.DataFrame(rows)


def render_report(summary: pd.DataFrame, city_contrib: pd.DataFrame, region_contrib: pd.DataFrame, comp: pd.DataFrame) -> str:
    focus = summary[summary["strategy"].isin(FOCUS_STRATEGIES)].copy()
    city_top = city_contrib[
        city_contrib["period"].eq("train_pre_2026_06_21")
        & city_contrib["strategy"].isin(["v1_edge20_baseline_all", "v1_edge20_v1_edge20_city_diag_ev_ge_0.5", "station_bias_p90_high"])
    ].sort_values("pnl", ascending=False)
    region_focus = region_contrib[
        region_contrib["period"].isin(["train_pre_2026_06_21", "holdout_2026_06_21_26"])
        & region_contrib["strategy"].isin(["v1_edge20_baseline_all", "v1_edge20_v1_edge20_city_diag_ev_ge_0.5", "station_bias_p90_high"])
    ].sort_values(["strategy", "period", "pnl"], ascending=[True, True, False])

    lines = [
        "# Low-Price YES Tail Robustness v1",
        "",
        f"Generated: {now_utc()}",
        "",
        "## Verdict",
        "",
        "`inconclusive` for a new selector.  This audit makes the shape clearer: the broad v1 sleeve is still the only thing worth running tiny, while the attractive p_cal/city-fixed lines are not yet a mechanism we can promote.",
        "",
        "Main read:",
        "",
        "- `v1_edge20_baseline_all` is not pretty, but it is the broadest and least pathologically selected denominator.",
        "- `city_diag_ev>=0.5` has very high ROI and even survives simple leave-one-city stress in train/holdout, but it uses raw city identity and has only 8 forward rows; it stays diagnostic-only until fresh forward and leave-region/leave-city retraining tests exist.",
        "- `station_bias_p90_high` is the most interesting mechanism tag: it has better train ROI than v1, but in holdout it underperforms its complement and forward is too winner-dependent.",
        "- No result here justifies changing live.  The correct next move is telemetry plus fresh-forward validation.",
        "",
        "Gate summary: `significance=FAIL`, `baseline=PARTIAL`, `forward=FAIL/NA`, `conclusion=inconclusive`.",
        "",
        "## Evidence Window",
        "",
        f"- Input: `{rel(PCAL_DETAILS)}` from p_cal v1.",
        f"- Region map: `{rel(BIAS_ROWS)}`.",
        "- Unit: one selected city-date-bracket BUY_YES decision row, fixed `$1` cost.",
        "- Periods: train `<2026-06-21`, holdout `2026-06-21..2026-06-26`, forward `2026-06-27..2026-06-30`.",
        "",
        "## Selector Stress Summary",
        "",
        md_table(
            focus,
            [
                ("strategy", "strategy"),
                ("period", "period"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("regions", "regions"),
                ("win_rate", "win"),
                ("avg_ask", "ask"),
                ("roi", "ROI"),
                ("roi_ci_low", "CI low"),
                ("roi_ci_high", "CI high"),
                ("top5_removed_roi", "top5 rm"),
                ("top10_removed_roi", "top10 rm"),
                ("min_leave_city_roi", "min leave city"),
                ("min_leave_city_value", "city"),
                ("min_leave_region_roi", "min leave region"),
                ("min_leave_region_value", "region"),
            ],
            max_rows=80,
        ),
        "",
        "## Selected vs Complement",
        "",
        "This asks whether a selector improves the current v1 denominator, instead of only showing standalone ROI.",
        "",
        md_table(
            comp[comp["strategy"].isin(FOCUS_STRATEGIES)].sort_values(["strategy", "period"]),
            [
                ("strategy", "strategy"),
                ("period", "period"),
                ("selected_rows", "selected"),
                ("selected_roi", "selected ROI"),
                ("complement_rows", "complement"),
                ("complement_roi", "complement ROI"),
                ("roi_delta_vs_complement", "delta"),
            ],
            max_rows=80,
        ),
        "",
        "## City Concentration",
        "",
        md_table(
            city_top,
            [
                ("strategy", "strategy"),
                ("period", "period"),
                ("city", "city"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("win_rate", "win"),
                ("avg_ask", "ask"),
                ("roi", "ROI"),
                ("pnl", "PnL"),
                ("pnl_share", "PnL share"),
            ],
            max_rows=40,
        ),
        "",
        "## Region Contribution",
        "",
        md_table(
            region_focus,
            [
                ("strategy", "strategy"),
                ("period", "period"),
                ("region", "region"),
                ("rows", "rows"),
                ("cities", "cities"),
                ("win_rate", "win"),
                ("avg_ask", "ask"),
                ("roi", "ROI"),
                ("pnl", "PnL"),
                ("pnl_share", "PnL share"),
            ],
            max_rows=60,
        ),
        "",
        "## Interpretation",
        "",
        "- The station-basis hypothesis is still better than the old source story, but not yet a tradable selector: train lift is real-looking, holdout lift is not.",
        "- p_cal with city fixed effects is useful as an alarm bell and ranking diagnostic.  Because it uses raw city identity, promotion needs leave-one-city/region retraining plus fresh-forward proof, not just leave-one-city PnL stress on already selected rows.",
        "- The best next research is not another historical threshold sweep.  It is to log as-of station-basis, bracket distance, p_cal, spread/depth, and then evaluate only rows generated after the telemetry patch.",
        "",
        "## Artifacts",
        "",
        f"- Script: `{rel(Path(__file__))}`",
        f"- JSON summary: `{rel(OUT_JSON)}`",
        f"- Summary: `{rel(OUT_DIR / 'summary.csv')}`",
        f"- Complement: `{rel(OUT_DIR / 'complement.csv')}`",
        f"- City contributions: `{rel(OUT_DIR / 'city_contributions.csv')}`",
        f"- Region contributions: `{rel(OUT_DIR / 'region_contributions.csv')}`",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    details, baseline = load_rows()
    summary = summarize_all(details)
    city_contrib = contributions(details, "city")
    region_contrib = contributions(details, "region")
    comp = complement_summary(details, baseline)

    details.to_csv(OUT_DIR / "details.csv", index=False)
    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    city_contrib.to_csv(OUT_DIR / "city_contributions.csv", index=False)
    region_contrib.to_csv(OUT_DIR / "region_contributions.csv", index=False)
    comp.to_csv(OUT_DIR / "complement.csv", index=False)

    OUT_MD.write_text(render_report(summary, city_contrib, region_contrib, comp), encoding="utf-8")
    payload = {
        "generated_at_utc": now_utc(),
        "verdict": "inconclusive",
        "strategy_family": "low_price_yes_tail_robustness_v1",
        "outputs": {
            "markdown": rel(OUT_MD),
            "summary_csv": rel(OUT_DIR / "summary.csv"),
            "details_csv": rel(OUT_DIR / "details.csv"),
            "city_contributions_csv": rel(OUT_DIR / "city_contributions.csv"),
            "region_contributions_csv": rel(OUT_DIR / "region_contributions.csv"),
            "complement_csv": rel(OUT_DIR / "complement.csv"),
        },
        "summary": summary.to_dict(orient="records"),
        "complement": comp.to_dict(orient="records"),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {rel(OUT_MD)}")
    print(
        summary[
            summary["strategy"].isin(["v1_edge20_baseline_all", "station_bias_p90_high", "v1_edge20_v1_edge20_city_diag_ev_ge_0.5"])
            & summary["period"].isin(["train_pre_2026_06_21", "holdout_2026_06_21_26", "forward_2026_06_27_30"])
        ][["strategy", "period", "rows", "roi", "top10_removed_roi", "min_leave_city_roi", "min_leave_region_roi"]].to_string(index=False)
    )


if __name__ == "__main__":
    main()
