#!/usr/bin/env python3
"""Low-price YES lottery METAR/regime v2 research.

This is a later intraday research head for the low-price YES lottery idea.  It
uses the intraday weather regime atlas as the feature/expression matrix and
evaluates whether waiting for METAR/regime evidence improves the 5c-20c tail YES
selector.

The row grain for strategy results is first trigger per city-date-rule.  That
keeps the backtest close to a shadow/live selector instead of repeatedly buying
the same city-date at multiple hourly snapshots.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
ATLAS_PATH = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv"
V1_SUMMARY_PATH = ROOT / "docs/analysis/2026-07/generated/low_price_yes_lottery_selector_refinement_v1/summary.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_lottery_metar_regime_v2"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-02-low-price-yes-lottery-metar-regime-v2.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-02-low-price-yes-lottery-metar-regime-v2.json"

HOLDOUT_START = "2026-06-21"
RECENT_START = "2026-06-08"
ASK_MIN = 0.05
ASK_MAX = 0.20
STAKE_USD = 1.0
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


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 30) -> str:
    if df.empty:
        return "_No rows._"
    pct_cols = {"win_rate", "roi", "roi_ci_low", "roi_ci_high", "top_trade_removed_roi"}
    money_cols = {"cost", "pnl", "avg_cost", "pnl_per_active_day", "cost_per_active_day", "max_daily_loss"}
    int_cols = {"rows", "dates", "cities", "wins", "losing_days", "roi_le_minus_50_days"}
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df.head(max_rows).iterrows():
        vals: list[str] = []
        for key, _label in cols:
            val = row.get(key)
            if key in pct_cols or key.endswith("_roi") or "roi_ci" in key or key.endswith("_win_rate"):
                vals.append(pct(val))
            elif key in money_cols or key.endswith("_pnl") or key.endswith("_loss") or "pnl_per_active_day" in key:
                vals.append(money(val))
            elif (key in int_cols or key.endswith("_rows") or key.endswith("_dates") or key.endswith("_cities") or key.endswith("_wins") or "losing_days" in key) and pd.notna(val):
                vals.append(str(int(val)))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "")
            else:
                vals.append("" if val is None or pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def block_bootstrap_ci(daily: pd.DataFrame, n_boot: int = 5000) -> tuple[float | None, float | None]:
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


def load_atlas() -> pd.DataFrame:
    df = pd.read_csv(ATLAS_PATH, low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    df["decision_snapshot_sort"] = pd.to_datetime(df["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    numeric_cols = [
        "decision_hour_local",
        "lottery_yes_ask",
        "lottery_yes_payoff",
        "lottery_yes_ask_size",
        "lottery_yes_spread",
        "forecast_gap_to_running_native",
        "gfs_gap_to_running_native",
        "ecmwf_gap_to_running_native",
        "forecast_error_native",
        "forecast_peak_delta_hours_local",
        "relative_humidity_pct",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
        "running_native",
        "final_max_native",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def lottery_base(df: pd.DataFrame) -> pd.DataFrame:
    out = df[df["lottery_yes_ask"].between(ASK_MIN, ASK_MAX) & df["lottery_yes_payoff"].isin([0.0, 1.0])].copy()
    out["ask"] = out["lottery_yes_ask"].astype(float)
    out["payoff"] = out["lottery_yes_payoff"].astype(float)
    out["win"] = out["payoff"].eq(1.0).astype(int)
    out["score_open_or_marginal"] = out["day_regime"].isin(["day_open_runway", "day_marginal_runway"]).astype(int)
    out["score_forecast_gap_ge1"] = out["forecast_gap_to_running_native"].ge(1.0).astype(int)
    out["score_late_morning"] = out["solar_window"].eq("late_morning").astype(int)
    out["score_fade_or_fresh_high"] = out["intraday_state"].isin(["false_fade_risk", "fresh_high"]).astype(int)
    out["score_humid_convective"] = out["moisture_cloud_regime"].eq("humid_convective_risk").astype(int)
    out["score_light_wind"] = out["wind_regime"].eq("light_wind").astype(int)
    out["score_not_capped_busted"] = (~out["day_regime"].isin(["day_forecast_capped", "day_forecast_busted"])).astype(int)
    out["regime_score"] = out[
        [
            "score_open_or_marginal",
            "score_forecast_gap_ge1",
            "score_late_morning",
            "score_fade_or_fresh_high",
            "score_humid_convective",
            "score_light_wind",
            "score_not_capped_busted",
        ]
    ].sum(axis=1)
    return out


RuleFn = Callable[[pd.DataFrame], pd.Series]


def rules() -> dict[str, RuleFn]:
    return {
        "all_low_price_05_20": lambda d: pd.Series(True, index=d.index),
        "day_open_runway": lambda d: d["day_regime"].eq("day_open_runway"),
        "open_or_marginal_runway": lambda d: d["day_regime"].isin(["day_open_runway", "day_marginal_runway"]),
        "non_capped_non_busted": lambda d: ~d["day_regime"].isin(["day_forecast_capped", "day_forecast_busted"]),
        "active_warming": lambda d: d["intraday_state"].eq("active_warming"),
        "open_runway_active_warming": lambda d: d["day_regime"].eq("day_open_runway") & d["intraday_state"].eq("active_warming"),
        "false_fade_risk": lambda d: d["intraday_state"].eq("false_fade_risk"),
        "open_runway_false_fade": lambda d: d["day_regime"].eq("day_open_runway") & d["intraday_state"].eq("false_fade_risk"),
        "humid_convective": lambda d: d["moisture_cloud_regime"].eq("humid_convective_risk"),
        "southern_maritime": lambda d: d["city_family"].eq("southern_or_maritime"),
        "forecast_gap_ge_1": lambda d: d["forecast_gap_to_running_native"].ge(1.0),
        "late_morning": lambda d: d["solar_window"].eq("late_morning"),
        "open_late_lightwind": lambda d: d["day_regime"].eq("day_open_runway")
        & d["solar_window"].eq("late_morning")
        & d["wind_regime"].eq("light_wind"),
        "open_gap_ge1_late": lambda d: d["day_regime"].eq("day_open_runway")
        & d["forecast_gap_to_running_native"].ge(1.0)
        & d["solar_window"].eq("late_morning"),
        "open_gap_ge1_humid": lambda d: d["day_regime"].eq("day_open_runway")
        & d["forecast_gap_to_running_native"].ge(1.0)
        & d["moisture_cloud_regime"].eq("humid_convective_risk"),
    }


def pick_first_trigger(df: pd.DataFrame, rule_name: str, mask: pd.Series) -> pd.DataFrame:
    g = df[mask].copy()
    if g.empty:
        return g
    g["rule"] = rule_name
    # Earliest point-in-time trigger per city-date; lower ask only tie-breaks same timestamp.
    return (
        g.sort_values(["target_date", "city", "decision_snapshot_sort", "ask", "lottery_yes_bracket"])
        .drop_duplicates(["target_date", "city"])
        .reset_index(drop=True)
    )


def add_pnl(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["cost"] = STAKE_USD
    out["shares"] = STAKE_USD / out["ask"]
    out["pnl"] = out["payoff"] * out["shares"] - STAKE_USD
    return out


def score_bucket_summary(base: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for threshold in range(0, 8):
        picked = add_pnl(pick_first_trigger(base, f"regime_score_ge_{threshold}", base["regime_score"].ge(threshold)))
        periods = {
            "full": picked,
            "holdout_2026_06_21_plus": picked[picked["target_date"] >= HOLDOUT_START],
            "recent_2026_06_08_plus": picked[picked["target_date"] >= RECENT_START],
        }
        for period, frame in periods.items():
            rows.append(summarize(frame, period, f"regime_score_ge_{threshold}"))
    return pd.DataFrame(rows)


def summarize(g: pd.DataFrame, period: str, rule_name: str) -> dict[str, Any]:
    out: dict[str, Any] = {"rule": rule_name, "period": period}
    if g.empty:
        out.update({"rows": 0, "dates": 0, "cities": 0})
        return out
    daily = g.groupby("target_date", as_index=False).agg(rows=("pnl", "size"), wins=("win", "sum"), cost=("cost", "sum"), pnl=("pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    ci_low, ci_high = block_bootstrap_ci(daily)
    top_removed = g.sort_values("pnl", ascending=False).iloc[1:]
    top_removed_roi = float(top_removed["pnl"].sum() / top_removed["cost"].sum()) if float(top_removed["cost"].sum()) > 0 else None
    out.update(
        {
            "rows": int(len(g)),
            "dates": int(g["target_date"].nunique()),
            "cities": int(g["city"].nunique()),
            "wins": int(g["win"].sum()),
            "win_rate": float(g["win"].mean()),
            "avg_ask": float(g["ask"].mean()),
            "cost": float(g["cost"].sum()),
            "pnl": float(g["pnl"].sum()),
            "roi": float(g["pnl"].sum() / g["cost"].sum()),
            "roi_ci_low": ci_low,
            "roi_ci_high": ci_high,
            "losing_days": int((daily["pnl"] < 0).sum()),
            "roi_le_minus_50_days": int((daily["roi"] <= -0.50).sum()),
            "max_daily_loss": float(daily["pnl"].min()),
            "top_trade_removed_roi": top_removed_roi,
            "cost_per_active_day": float(g["cost"].sum() / g["target_date"].nunique()),
            "pnl_per_active_day": float(g["pnl"].sum() / g["target_date"].nunique()),
        }
    )
    if out["rows"] < 30 or out["dates"] < 10:
        out["support_flag"] = "thin"
    elif ci_low is not None and ci_low <= 0 <= ci_high:
        out["support_flag"] = "ci_crosses_zero"
    else:
        out["support_flag"] = "ok"
    return out


def evaluate(base: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    details: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    daily_frames: list[pd.DataFrame] = []

    for rule_name, fn in rules().items():
        picked = add_pnl(pick_first_trigger(base, rule_name, fn(base)))
        if not picked.empty:
            details.append(picked)
        periods = {
            "full": picked,
            "train_pre_2026_06_21": picked[picked["target_date"] < HOLDOUT_START],
            "holdout_2026_06_21_plus": picked[picked["target_date"] >= HOLDOUT_START],
            "recent_2026_06_08_plus": picked[picked["target_date"] >= RECENT_START],
        }
        for period, frame in periods.items():
            summaries.append(summarize(frame, period, rule_name))
            if not frame.empty:
                d = frame.groupby("target_date", as_index=False).agg(rows=("pnl", "size"), wins=("win", "sum"), cost=("cost", "sum"), pnl=("pnl", "sum"))
                d["roi"] = d["pnl"] / d["cost"]
                d["rule"] = rule_name
                d["period"] = period
                daily_frames.append(d)

    return (
        pd.concat(details, ignore_index=True, sort=False) if details else pd.DataFrame(),
        pd.DataFrame(summaries),
        pd.concat(daily_frames, ignore_index=True, sort=False) if daily_frames else pd.DataFrame(),
    )


def contribution_table(details: pd.DataFrame, rule_name: str, dimensions: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    g = details[details["rule"].eq(rule_name)].copy()
    for dim in dimensions:
        if dim not in g.columns:
            continue
        for level, x in g.groupby(dim, dropna=False):
            if len(x) < 3:
                continue
            rows.append(
                {
                    "rule": rule_name,
                    "dimension": dim,
                    "level": str(level),
                    "rows": int(len(x)),
                    "dates": int(x["target_date"].nunique()),
                    "cities": int(x["city"].nunique()),
                    "win_rate": float(x["win"].mean()),
                    "avg_ask": float(x["ask"].mean()),
                    "pnl": float(x["pnl"].sum()),
                    "roi": float(x["pnl"].sum() / x["cost"].sum()),
                }
            )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["dimension", "roi"], ascending=[True, False]).reset_index(drop=True)


def load_v1_reference() -> pd.DataFrame:
    if not V1_SUMMARY_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(V1_SUMMARY_PATH)
    return df[
        df["selector"].eq("no_dust_edge20_ask05_20")
        & df["sizing"].eq("payout25_cap5")
        & df["period"].isin(["historical", "historical_recent", "forward"])
    ].copy()


def render_report(
    atlas: pd.DataFrame,
    base: pd.DataFrame,
    details: pd.DataFrame,
    summary: pd.DataFrame,
    score_buckets: pd.DataFrame,
    contrib: pd.DataFrame,
    v1_ref: pd.DataFrame,
) -> str:
    pivot = summary.pivot_table(
        index="rule",
        columns="period",
        values=["rows", "dates", "cities", "avg_ask", "win_rate", "roi", "roi_ci_low", "roi_ci_high", "top_trade_removed_roi", "cost_per_active_day", "pnl_per_active_day", "max_daily_loss", "losing_days", "roi_le_minus_50_days"],
        aggfunc="first",
    )
    pivot.columns = [f"{period}_{metric}" for metric, period in pivot.columns]
    pivot = pivot.reset_index()

    main_cols = [
        "rule",
        "full_rows",
        "full_dates",
        "full_cities",
        "full_win_rate",
        "full_avg_ask",
        "full_roi",
        "full_roi_ci_low",
        "full_roi_ci_high",
        "full_top_trade_removed_roi",
        "holdout_2026_06_21_plus_rows",
        "holdout_2026_06_21_plus_roi",
        "recent_2026_06_08_plus_rows",
        "recent_2026_06_08_plus_roi",
        "full_pnl_per_active_day",
        "full_max_daily_loss",
    ]
    compact = pivot[[c for c in main_cols if c in pivot.columns]].sort_values(
        ["holdout_2026_06_21_plus_roi", "full_roi"], ascending=False
    )

    score_pivot = score_buckets.pivot_table(
        index="rule",
        columns="period",
        values=["rows", "dates", "cities", "avg_ask", "win_rate", "roi", "roi_ci_low", "roi_ci_high", "top_trade_removed_roi", "max_daily_loss"],
        aggfunc="first",
    )
    score_pivot.columns = [f"{period}_{metric}" for metric, period in score_pivot.columns]
    score_pivot = score_pivot.reset_index().sort_values("full_rows", ascending=False)

    best_rule = "open_late_lightwind"
    best_full = summary[summary["rule"].eq(best_rule) & summary["period"].eq("full")].iloc[0]
    best_holdout = summary[summary["rule"].eq(best_rule) & summary["period"].eq("holdout_2026_06_21_plus")].iloc[0]
    all_full = summary[summary["rule"].eq("all_low_price_05_20") & summary["period"].eq("full")].iloc[0]
    best_daily = details[details["rule"].eq(best_rule)].groupby("target_date", as_index=False).agg(rows=("pnl", "size"), wins=("win", "sum"), pnl=("pnl", "sum"), cost=("cost", "sum"))
    best_daily["roi"] = best_daily["pnl"] / best_daily["cost"]

    v1_lines: list[str] = []
    if not v1_ref.empty:
        v1 = v1_ref.copy()
        v1["strategy"] = "v1_early_forecast_edge"
        v1_lines = [
            "## v1 Reference",
            "",
            "This v2 is not an upgrade over the current early-entry v1 selector.  The closest v1 reference is `edge>=0.20 && ask 0.05..0.20`, one city-date, `sizing=min($5, ask*25 shares)`.",
            "",
            md_table(
                v1.rename(columns={"period": "window"}),
                [
                    ("window", "window"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("win_rate", "win"),
                    ("avg_ask", "avg ask"),
                    ("roi", "ROI"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                    ("top_trade_removed_roi", "top-trade removed"),
                    ("max_daily_loss", "max daily loss"),
                ],
            ),
            "",
            "The v1 row source is earlier forecast/model edge.  The v2 row source is later intraday atlas state.  They are related strategy families, but not the same denominator.",
            "",
        ]

    contrib_focus = contrib[contrib["rule"].isin(["all_low_price_05_20", best_rule])].copy()
    contrib_focus = contrib_focus.sort_values(["rule", "dimension", "roi"], ascending=[True, True, False])

    lines = [
        "# Low-Price YES Lottery METAR/Regime v2",
        "",
        f"Generated: {now_utc()}",
        "",
        "## Verdict",
        "",
        "`inconclusive` for live or size-up.  Waiting for intraday METAR/regime evidence did not improve the low-price YES lottery idea in a robust way.",
        "",
        "The narrow tag that looked best by point estimate is:",
        "",
        "```text",
        "BUY hotter-tail YES",
        "ask 0.05..0.20",
        "first trigger per city-date",
        "day_regime == day_open_runway",
        "solar_window == late_morning",
        "wind_regime == light_wind",
        "paper sizing for research: $1/order",
        "```",
        "",
        "It is only a case label, not a selector.  Full-window point ROI is positive, but holdout is negative and the CI crosses zero.  The wider bucket check below is the more important result.",
        "",
        "```text",
        "significance=FAIL",
        "baseline=FAIL versus v1 early-entry selector",
        "forward/holdout=FAIL",
        "conclusion=inconclusive; keep shadow telemetry only",
        "```",
        "",
        "## Evidence Window",
        "",
        f"- Atlas file: `{rel(ATLAS_PATH)}`.",
        f"- Atlas rows: {len(atlas):,}, target_date {atlas['target_date'].min()}..{atlas['target_date'].max()}.",
        f"- Low-price evaluable rows: {len(base):,}, target_date {base['target_date'].min()}..{base['target_date'].max()}, ask `{ASK_MIN:.2f}..{ASK_MAX:.2f}`.",
        f"- Strategy grain: first trigger per `rule + city + target_date`; PnL assumes fixed `${STAKE_USD:.0f}` cost per selected row.",
        f"- Holdout split: `{HOLDOUT_START}` onward. Recent split: `{RECENT_START}` onward.",
        "- This is historical/shadow research from expression/regime rows, not `live_real` PnL.",
        "",
        "## Backtest Summary",
        "",
        md_table(
            compact,
            [
                ("rule", "rule"),
                ("full_rows", "rows"),
                ("full_dates", "dates"),
                ("full_cities", "cities"),
                ("full_win_rate", "win"),
                ("full_avg_ask", "avg ask"),
                ("full_roi", "ROI"),
                ("full_roi_ci_low", "CI low"),
                ("full_roi_ci_high", "CI high"),
                ("full_top_trade_removed_roi", "top-trade removed"),
                ("holdout_2026_06_21_plus_rows", "holdout rows"),
                ("holdout_2026_06_21_plus_roi", "holdout ROI"),
                ("recent_2026_06_08_plus_rows", "recent rows"),
                ("recent_2026_06_08_plus_roi", "recent ROI"),
                ("full_pnl_per_active_day", "$1/order PnL/day"),
                ("full_max_daily_loss", "max daily loss"),
            ],
            max_rows=20,
        ),
        "",
        "## Wider Bucket Check",
        "",
        "To avoid over-reading a tiny hard gate, v2 also uses a loose additive regime score: open/marginal day, forecast runway >= 1, late morning, false-fade/fresh-high, humid-convective, light wind, and not capped/busted.",
        "",
        md_table(
            score_pivot,
            [
                ("rule", "score bucket"),
                ("full_rows", "rows"),
                ("full_dates", "dates"),
                ("full_cities", "cities"),
                ("full_win_rate", "win"),
                ("full_avg_ask", "avg ask"),
                ("full_roi", "ROI"),
                ("full_roi_ci_low", "CI low"),
                ("full_roi_ci_high", "CI high"),
                ("full_top_trade_removed_roi", "top-trade removed"),
                ("holdout_2026_06_21_plus_rows", "holdout rows"),
                ("holdout_2026_06_21_plus_roi", "holdout ROI"),
                ("recent_2026_06_08_plus_rows", "recent rows"),
                ("recent_2026_06_08_plus_roi", "recent ROI"),
                ("full_max_daily_loss", "max daily loss"),
            ],
        ),
        "",
        "This is why the narrow tag is not actionable: `score>=4` still has 108 rows but ROI is -15.8%; `score>=5` turns positive at 67 rows but holdout is -30.6%; `score>=6` is already only 22 rows and fails holdout completely.",
        "",
        "## Best v2 Candidate Details",
        "",
        md_table(
            summary[summary["rule"].eq(best_rule)].sort_values("period"),
            [
                ("period", "period"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("wins", "wins"),
                ("win_rate", "win"),
                ("avg_ask", "avg ask"),
                ("cost", "cost"),
                ("pnl", "PnL"),
                ("roi", "ROI"),
                ("roi_ci_low", "CI low"),
                ("roi_ci_high", "CI high"),
                ("losing_days", "losing days"),
                ("roi_le_minus_50_days", "<= -50% days"),
                ("max_daily_loss", "max daily loss"),
                ("top_trade_removed_roi", "top-trade removed"),
            ],
        ),
        "",
        f"At `$1/order`, `{best_rule}` averages {float(best_full['rows']) / float(best_full['dates']):.2f} trades per active day and {money(best_full['pnl_per_active_day'])} per active day in-sample.  Holdout is {int(best_holdout['rows'])} rows with {pct(best_holdout['roi'])} ROI, so it is not live-confirmed.",
        "",
        "Daily PnL for the best v2 tag:",
        "",
        md_table(
            best_daily.sort_values("target_date"),
            [("target_date", "date"), ("rows", "rows"), ("wins", "wins"), ("cost", "cost"), ("pnl", "PnL"), ("roi", "ROI")],
            max_rows=60,
        ),
        "",
        *v1_lines,
        "## Interpretation",
        "",
        f"- The broad intraday version is worse: `all_low_price_05_20` has {int(all_full['rows'])} rows, win {pct(all_full['win_rate'])}, avg ask {float(all_full['avg_ask']):.3f}, ROI {pct(all_full['roi'])}.",
        "- The broad score test is the main answer to the over-filtering concern: there is no wide positive bucket.  Positive point estimates appear only after the denominator gets thin.",
        f"- The plausible mechanism pocket `{best_rule}` improves full-window ROI to {pct(best_full['roi'])}, but holdout stays negative and daily losses are frequent.",
        "- The positive v2 pockets look like weather complexity / late light-wind runway tags, not a clean forecast-bias alpha.  Current evidence cannot separate true alpha from a few city-date lottery hits.",
        "- This argues against moving the live v1 selector later just to wait for METAR/regime.  The v1 edge appears to be mostly forecast/model/market mispricing at early snapshots; the intraday regime layer is better used as shadow attribution for now.",
        "",
        "## Contributions",
        "",
        md_table(
            contrib_focus,
            [
                ("rule", "rule"),
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
        "## Next Practical Step",
        "",
        "Keep the current low-price YES live test at small notional.  Add v2 shadow telemetry fields to the journal when convenient: `day_regime`, `intraday_state`, `solar_window`, `wind_regime`, `moisture_cloud_regime`, `forecast_gap_to_running_native`, and whether the row matches `open_late_lightwind`.  Do not use this as a live gate unless forward rows show positive holdout after settlement.",
        "",
        "## Artifacts",
        "",
        f"- Script: `{rel(Path(__file__))}`",
        f"- JSON summary: `{rel(OUT_JSON)}`",
        f"- Details: `{rel(OUT_DIR / 'details.csv')}`",
        f"- Summary: `{rel(OUT_DIR / 'summary.csv')}`",
        f"- Daily: `{rel(OUT_DIR / 'daily.csv')}`",
        f"- Contributions: `{rel(OUT_DIR / 'contributions.csv')}`",
        f"- Score buckets: `{rel(OUT_DIR / 'score_buckets.csv')}`",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atlas = load_atlas()
    base = lottery_base(atlas)
    details, summary, daily = evaluate(base)
    score_buckets = score_bucket_summary(base)
    contrib = contribution_table(
        details,
        "all_low_price_05_20",
        ["city", "forecast_source", "day_regime", "intraday_state", "solar_window", "wind_regime", "moisture_cloud_regime", "city_family"],
    )
    contrib_best = contribution_table(
        details,
        "open_late_lightwind",
        ["city", "forecast_source", "day_regime", "intraday_state", "solar_window", "wind_regime", "moisture_cloud_regime", "city_family"],
    )
    contributions = pd.concat([contrib, contrib_best], ignore_index=True, sort=False)
    v1_ref = load_v1_reference()

    details.to_csv(OUT_DIR / "details.csv", index=False)
    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    daily.to_csv(OUT_DIR / "daily.csv", index=False)
    contributions.to_csv(OUT_DIR / "contributions.csv", index=False)
    score_buckets.to_csv(OUT_DIR / "score_buckets.csv", index=False)

    report = render_report(atlas, base, details, summary, score_buckets, contributions, v1_ref)
    OUT_MD.write_text(report, encoding="utf-8")

    payload = {
        "generated_at_utc": now_utc(),
        "verdict": "inconclusive",
        "strategy_family": "low_price_yes_lottery_metar_regime_v2",
        "atlas_path": rel(ATLAS_PATH),
        "outputs": {
            "markdown": rel(OUT_MD),
            "summary_csv": rel(OUT_DIR / "summary.csv"),
            "details_csv": rel(OUT_DIR / "details.csv"),
            "daily_csv": rel(OUT_DIR / "daily.csv"),
            "contributions_csv": rel(OUT_DIR / "contributions.csv"),
            "score_buckets_csv": rel(OUT_DIR / "score_buckets.csv"),
        },
        "evidence_window": {
            "atlas_rows": int(len(atlas)),
            "atlas_min_target_date": str(atlas["target_date"].min()),
            "atlas_max_target_date": str(atlas["target_date"].max()),
            "evaluable_low_price_rows": int(len(base)),
            "evaluable_min_target_date": str(base["target_date"].min()) if not base.empty else None,
            "evaluable_max_target_date": str(base["target_date"].max()) if not base.empty else None,
            "ask_min": ASK_MIN,
            "ask_max": ASK_MAX,
            "stake_usd": STAKE_USD,
            "holdout_start": HOLDOUT_START,
            "recent_start": RECENT_START,
        },
        "summary": summary.to_dict(orient="records"),
        "score_buckets": score_buckets.to_dict(orient="records"),
        "v1_reference": v1_ref.to_dict(orient="records"),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    best = summary[summary["rule"].eq("open_late_lightwind") & summary["period"].eq("full")].iloc[0]
    holdout = summary[summary["rule"].eq("open_late_lightwind") & summary["period"].eq("holdout_2026_06_21_plus")].iloc[0]
    print(f"wrote {rel(OUT_MD)}")
    print(f"best_shadow=open_late_lightwind full_rows={int(best['rows'])} full_roi={best['roi']:+.3f} holdout_rows={int(holdout['rows'])} holdout_roi={holdout['roi']:+.3f}")


if __name__ == "__main__":
    main()
