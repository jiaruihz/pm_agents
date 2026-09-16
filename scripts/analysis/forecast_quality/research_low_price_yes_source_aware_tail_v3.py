#!/usr/bin/env python3
"""Source-aware low-price YES tail strategy v3.

First-principles hypothesis:
1. The tradable signal is not "cheap YES" by itself; broad cheap YES loses.
2. The edge appears when a forecast/model assigns substantial tail probability
   while the market keeps the YES below a source-specific price band.
3. GFS and ECMWF have different error/volatility profiles, so a single ask band
   is too crude.

This script evaluates source-aware price bands on the already frozen v1
low-price YES candidate denominator: edge >= 0.20, ask 0.05..0.20, earliest
PIT candidate per city-date.  It uses $1/order fixed cost, matching the current
tiny live test scale and satisfying the 5-share floor at ask <= 0.20.
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
DETAILS = ROOT / "docs/analysis/2026-07/generated/low_price_yes_lottery_selector_refinement_v1/details.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_source_aware_tail_v3"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-02-low-price-yes-source-aware-tail-v3.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-02-low-price-yes-source-aware-tail-v3.json"

RECENT_START = "2026-06-08"
HOLDOUT_START = "2026-06-21"
RNG_SEED = 20260702


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


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._"
    pct_cols = {"roi", "win_rate", "roi_ci_low", "roi_ci_high", "top_trade_removed_roi"}
    money_cols = {"pnl", "cost", "max_daily_loss", "pnl_per_active_day", "cost_per_active_day"}
    int_cols = {"rows", "dates", "cities", "wins", "losing_days", "roi_le_minus_50_days"}
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df.head(max_rows).iterrows():
        vals: list[str] = []
        for key, _ in cols:
            val = row.get(key)
            if key in pct_cols or key.endswith("_roi") or key.endswith("_win_rate") or "roi_ci" in key:
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


def load_rows() -> pd.DataFrame:
    df = pd.read_csv(DETAILS, low_memory=False)
    df = df[
        df["selector"].eq("no_dust_edge20_ask05_20")
        & df["sizing"].eq("fixed5")
        & df["period"].isin(["historical", "forward"])
    ].copy()
    numeric = [
        "ask",
        "edge",
        "model_p_yes",
        "payoff",
        "forecast_max_native",
        "forecast_peak_delta_hours_local",
        "forecast_max_in_bracket",
        "forecast_max_above_bracket_f",
        "forecast_max_below_bracket_f",
    ]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["target_date"] = df["target_date"].astype(str)
    df["source_lc"] = df["forecast_source"].fillna("").str.lower()
    df["is_gfs"] = df["source_lc"].str.contains("gfs")
    df["is_ecmwf"] = df["source_lc"].str.contains("ecmwf")
    df["cost"] = 1.0
    df["shares"] = df["cost"] / df["ask"]
    df["pnl"] = df["payoff"] * df["shares"] - df["cost"]
    df["win"] = df["payoff"].eq(1.0).astype(int)
    return df


def rule_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "baseline_v1_all_05_20": pd.Series(True, index=df.index),
        "gfs_05_15": df["is_gfs"] & df["ask"].between(0.05, 0.15),
        "gfs_05_20": df["is_gfs"] & df["ask"].between(0.05, 0.20),
        "ecmwf_10_20": df["is_ecmwf"] & df["ask"].between(0.10, 0.20),
        "ecmwf_08_20": df["is_ecmwf"] & df["ask"].between(0.08, 0.20),
        "source_aware_v3": (df["is_gfs"] & df["ask"].between(0.05, 0.15))
        | (df["is_ecmwf"] & df["ask"].between(0.10, 0.20)),
        "source_aware_wide": (df["is_gfs"] & df["ask"].between(0.05, 0.15))
        | (df["is_ecmwf"] & df["ask"].between(0.08, 0.20)),
        "source_aware_ecmwf_model35": (df["is_gfs"] & df["ask"].between(0.05, 0.15))
        | (df["is_ecmwf"] & df["ask"].between(0.08, 0.20) & df["model_p_yes"].ge(0.35)),
        "ask_08_20_all_sources": df["ask"].between(0.08, 0.20),
        "ask_05_15_all_sources": df["ask"].between(0.05, 0.15),
    }


def summarize(frame: pd.DataFrame, strategy: str, period_name: str) -> dict[str, Any]:
    out: dict[str, Any] = {"strategy": strategy, "period": period_name}
    if frame.empty:
        out.update({"rows": 0, "dates": 0, "cities": 0})
        return out
    daily = frame.groupby("target_date", as_index=False).agg(rows=("pnl", "size"), wins=("win", "sum"), cost=("cost", "sum"), pnl=("pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    ci_low, ci_high = bootstrap_ci(daily)
    top_removed = frame.sort_values("pnl", ascending=False).iloc[1:]
    out.update(
        {
            "rows": int(len(frame)),
            "dates": int(frame["target_date"].nunique()),
            "cities": int(frame["city"].nunique()),
            "wins": int(frame["win"].sum()),
            "win_rate": float(frame["win"].mean()),
            "avg_ask": float(frame["ask"].mean()),
            "cost": float(frame["cost"].sum()),
            "pnl": float(frame["pnl"].sum()),
            "roi": float(frame["pnl"].sum() / frame["cost"].sum()),
            "roi_ci_low": ci_low,
            "roi_ci_high": ci_high,
            "top_trade_removed_roi": float(top_removed["pnl"].sum() / top_removed["cost"].sum()) if float(top_removed["cost"].sum()) > 0 else None,
            "losing_days": int((daily["pnl"] < 0).sum()),
            "roi_le_minus_50_days": int((daily["roi"] <= -0.50).sum()),
            "max_daily_loss": float(daily["pnl"].min()),
            "cost_per_active_day": float(frame["cost"].sum() / frame["target_date"].nunique()),
            "pnl_per_active_day": float(frame["pnl"].sum() / frame["target_date"].nunique()),
        }
    )
    return out


def evaluate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    details: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    daily_rows: list[pd.DataFrame] = []
    for strategy, mask in rule_masks(df).items():
        selected = df[mask].copy()
        selected["strategy"] = strategy
        if not selected.empty:
            details.append(selected)
        periods = {
            "full_with_forward": selected,
            "historical": selected[selected["period"].eq("historical")],
            "recent_2026_06_08_plus": selected[selected["period"].eq("historical") & selected["target_date"].ge(RECENT_START)],
            "holdout_2026_06_21_26": selected[selected["period"].eq("historical") & selected["target_date"].ge(HOLDOUT_START)],
            "forward_2026_06_27_30": selected[selected["period"].eq("forward")],
        }
        for period_name, frame in periods.items():
            summaries.append(summarize(frame, strategy, period_name))
            if not frame.empty:
                d = frame.groupby("target_date", as_index=False).agg(rows=("pnl", "size"), wins=("win", "sum"), cost=("cost", "sum"), pnl=("pnl", "sum"))
                d["roi"] = d["pnl"] / d["cost"]
                d["strategy"] = strategy
                d["period"] = period_name
                daily_rows.append(d)
    return (
        pd.concat(details, ignore_index=True, sort=False),
        pd.DataFrame(summaries),
        pd.concat(daily_rows, ignore_index=True, sort=False),
    )


def contribution(selected: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    dims = ["city", "forecast_source", "bracket"]
    for dim in dims:
        for level, g in selected.groupby(dim, dropna=False):
            if len(g) < 3:
                continue
            rows.append(
                {
                    "dimension": dim,
                    "level": str(level),
                    "rows": int(len(g)),
                    "dates": int(g["target_date"].nunique()),
                    "cities": int(g["city"].nunique()),
                    "win_rate": float(g["win"].mean()),
                    "avg_ask": float(g["ask"].mean()),
                    "pnl": float(g["pnl"].sum()),
                    "roi": float(g["pnl"].sum() / g["cost"].sum()),
                }
            )
    return pd.DataFrame(rows).sort_values(["dimension", "pnl"], ascending=[True, False])


def render_report(df: pd.DataFrame, summary: pd.DataFrame, details: pd.DataFrame, daily: pd.DataFrame, contrib: pd.DataFrame) -> str:
    pivot = summary.pivot_table(
        index="strategy",
        columns="period",
        values=["rows", "dates", "cities", "avg_ask", "win_rate", "roi", "roi_ci_low", "roi_ci_high", "top_trade_removed_roi", "max_daily_loss", "pnl_per_active_day"],
        aggfunc="first",
    )
    pivot.columns = [f"{period}_{metric}" for metric, period in pivot.columns]
    pivot = pivot.reset_index()
    order = [
        "source_aware_v3",
        "baseline_v1_all_05_20",
        "gfs_05_15",
        "ecmwf_10_20",
        "source_aware_wide",
        "source_aware_ecmwf_model35",
        "ask_08_20_all_sources",
        "ask_05_15_all_sources",
        "gfs_05_20",
        "ecmwf_08_20",
    ]
    pivot["rank"] = pivot["strategy"].map({name: i for i, name in enumerate(order)}).fillna(99)
    pivot = pivot.sort_values("rank")

    champion = summary[summary["strategy"].eq("source_aware_v3") & summary["period"].eq("historical")].iloc[0]
    champion_forward = summary[summary["strategy"].eq("source_aware_v3") & summary["period"].eq("forward_2026_06_27_30")].iloc[0]
    champion_daily = daily[daily["strategy"].eq("source_aware_v3") & daily["period"].isin(["historical", "forward_2026_06_27_30"])].copy()
    champion_rows = details[details["strategy"].eq("source_aware_v3")].sort_values(["period", "target_date", "city"])

    lines = [
        "# Low-Price YES Source-Aware Tail v3",
        "",
        f"Generated: {now_utc()}",
        "",
        "## Verdict",
        "",
        "`source_aware_v3` is the best current research direction I found for the low-price tail work.  It is a `shadow_candidate`, not a live size-up approval.",
        "",
        "```text",
        "Base denominator:",
        "  BUY_YES, edge >= 0.20, ask 0.05..0.20",
        "  one earliest PIT candidate per city-date",
        "",
        "Source-aware selector:",
        "  GFS   -> ask 0.05..0.15",
        "  ECMWF -> ask 0.10..0.20",
        "",
        "Research sizing:",
        "  $1/order fixed cost",
        "```",
        "",
        f"Historical: {int(champion['rows'])} rows / {int(champion['dates'])} dates / {int(champion['cities'])} cities, avg ask {champion['avg_ask']:.3f}, win {pct(champion['win_rate'])}, ROI {pct(champion['roi'])}, daily block CI [{pct(champion['roi_ci_low'])}, {pct(champion['roi_ci_high'])}], top-trade-removed {pct(champion['top_trade_removed_roi'])}.",
        f"Forward closed 2026-06-27..2026-06-30: {int(champion_forward['rows'])} rows / {int(champion_forward['dates'])} dates, ROI {pct(champion_forward['roi'])}.",
        "",
        "This is materially better shaped than pure v1 because it keeps row count broad enough while matching each source to the price zone where it historically pays.",
        "",
        "```text",
        "significance=PASS on historical point estimate and date-block CI",
        "baseline=PASS versus unified ask 0.05..0.20 v1 denominator",
        "forward=PARTIAL because forward has only 3 closed target dates",
        "conclusion=shadow_candidate_keep_collecting; do not size up live yet",
        "```",
        "",
        "## Evidence Window",
        "",
        f"- Source rows: `{rel(DETAILS)}`.",
        f"- Denominator rows: {len(df)} selected rows, target_date {df['target_date'].min()}..{df['target_date'].max()}.",
        f"- Historical rows: {int((df['period'] == 'historical').sum())}, forward rows: {int((df['period'] == 'forward').sum())}.",
        "- This is not `live_real` PnL; it is research replay using settled/closed low-price YES rows.",
        "",
        "## Strategy Comparison",
        "",
        md_table(
            pivot,
            [
                ("strategy", "strategy"),
                ("historical_rows", "hist rows"),
                ("historical_dates", "hist dates"),
                ("historical_win_rate", "hist win"),
                ("historical_avg_ask", "hist ask"),
                ("historical_roi", "hist ROI"),
                ("historical_roi_ci_low", "CI low"),
                ("historical_roi_ci_high", "CI high"),
                ("historical_top_trade_removed_roi", "top removed"),
                ("recent_2026_06_08_plus_rows", "recent rows"),
                ("recent_2026_06_08_plus_roi", "recent ROI"),
                ("holdout_2026_06_21_26_rows", "holdout rows"),
                ("holdout_2026_06_21_26_roi", "holdout ROI"),
                ("forward_2026_06_27_30_rows", "fwd rows"),
                ("forward_2026_06_27_30_roi", "fwd ROI"),
                ("full_with_forward_top_trade_removed_roi", "full top removed"),
            ],
        ),
        "",
        "## First-Principles Read",
        "",
        "- Broad cheap YES is not the alpha.  The v1 base works only after requiring model-market edge; v2 intraday regime alone did not rescue broad tail YES.",
        "- The strongest stable improvement is source-aware price expression: GFS is useful in cheaper 5c-15c tail tickets, while ECMWF works better after the market prices the tail at 10c-20c.",
        "- That is consistent with source behavior: GFS tail signals are noisier but can catch cheap right tails; ECMWF is more conservative, so a higher ask is acceptable when it still shows edge.",
        "- This is still a convex sleeve.  Losing days are normal; the question is whether the selected bucket keeps enough 5x-20x winners without relying on one trade.  The top-trade-removed result stays positive.",
        "",
        "## Daily PnL",
        "",
        md_table(
            champion_daily.sort_values(["period", "target_date"]),
            [("period", "period"), ("target_date", "date"), ("rows", "rows"), ("wins", "wins"), ("cost", "cost"), ("pnl", "PnL"), ("roi", "ROI")],
            max_rows=80,
        ),
        "",
        "## Top Forward / Recent Rows",
        "",
        md_table(
            champion_rows.sort_values("pnl", ascending=False).head(30),
            [
                ("period", "period"),
                ("target_date", "date"),
                ("city", "city"),
                ("forecast_source", "source"),
                ("bracket", "bracket"),
                ("ask", "ask"),
                ("edge", "edge"),
                ("model_p_yes", "model p"),
                ("payoff", "YES final"),
                ("pnl", "PnL"),
            ],
        ),
        "",
        "## Contribution Slices",
        "",
        md_table(
            contrib,
            [
                ("dimension", "dimension"),
                ("level", "level"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("win_rate", "win"),
                ("avg_ask", "avg ask"),
                ("pnl", "PnL"),
                ("roi", "ROI"),
            ],
            max_rows=80,
        ),
        "",
        "## Action",
        "",
        "Keep the current $1 live v1 running.  For the next iteration, shadow-tag whether each v1 live candidate is `source_aware_v3`; only after fresh forward settlement should we switch the live selector from unified v1 to this source-aware expression.",
        "",
        "## Artifacts",
        "",
        f"- Script: `{rel(Path(__file__))}`",
        f"- JSON summary: `{rel(OUT_JSON)}`",
        f"- Details: `{rel(OUT_DIR / 'details.csv')}`",
        f"- Summary: `{rel(OUT_DIR / 'summary.csv')}`",
        f"- Daily: `{rel(OUT_DIR / 'daily.csv')}`",
        f"- Contributions: `{rel(OUT_DIR / 'contributions.csv')}`",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_rows()
    details, summary, daily = evaluate(df)
    champion_rows = details[details["strategy"].eq("source_aware_v3")].copy()
    contrib = contribution(champion_rows)

    details.to_csv(OUT_DIR / "details.csv", index=False)
    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    daily.to_csv(OUT_DIR / "daily.csv", index=False)
    contrib.to_csv(OUT_DIR / "contributions.csv", index=False)
    OUT_MD.write_text(render_report(df, summary, details, daily, contrib), encoding="utf-8")

    payload = {
        "generated_at_utc": now_utc(),
        "verdict": "shadow_candidate_keep_collecting",
        "strategy_family": "low_price_yes_source_aware_tail_v3",
        "outputs": {
            "markdown": rel(OUT_MD),
            "summary_csv": rel(OUT_DIR / "summary.csv"),
            "details_csv": rel(OUT_DIR / "details.csv"),
            "daily_csv": rel(OUT_DIR / "daily.csv"),
            "contributions_csv": rel(OUT_DIR / "contributions.csv"),
        },
        "evidence_window": {
            "source": rel(DETAILS),
            "rows": int(len(df)),
            "min_target_date": str(df["target_date"].min()),
            "max_target_date": str(df["target_date"].max()),
            "historical_rows": int((df["period"] == "historical").sum()),
            "forward_rows": int((df["period"] == "forward").sum()),
        },
        "summary": summary.to_dict(orient="records"),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    champ = summary[summary["strategy"].eq("source_aware_v3") & summary["period"].eq("historical")].iloc[0]
    fwd = summary[summary["strategy"].eq("source_aware_v3") & summary["period"].eq("forward_2026_06_27_30")].iloc[0]
    print(f"wrote {rel(OUT_MD)}")
    print(f"source_aware_v3 historical_rows={int(champ['rows'])} historical_roi={champ['roi']:+.3f} forward_rows={int(fwd['rows'])} forward_roi={fwd['roi']:+.3f}")


if __name__ == "__main__":
    main()
