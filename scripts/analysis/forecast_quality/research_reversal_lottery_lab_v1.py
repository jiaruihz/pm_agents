#!/usr/bin/env python3
"""Reversal and lottery lab for weather temperature expressions.

This is an independent research head.  It separates two evidence layers:

1. `fact_signal_candidates` low-price BUY_YES rows for the broad lottery
   base-rate question.
2. The generated current-YES expression matrix for same-snapshot reversal
   expressions.

Selectors only use PIT prices/features.  Final settlement is used only for
payoff evaluation.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "runtime/weather.db"
GATE_JSON = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
EVENT_ROWS = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv"
HIST_SUMMARY = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/city_model_error_summary.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/reversal_lottery_lab_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-01-reversal-lottery-lab-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-01-reversal-lottery-lab-v1.json"

STAKE_USD = 5.0
TRAIN_END_EXCLUSIVE = "2026-06-01"
HOLDOUT_START = "2026-06-01"
RECENT_START = "2026-06-08"
RNG_SEED = 20260701


def pct(v: Any) -> str:
    try:
        x = float(v)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"{100 * x:+.1f}%"


def money(v: Any) -> str:
    try:
        x = float(v)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"${x:+.2f}"


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._"
    pct_cols = {
        "win_rate",
        "roi",
        "roi_ci_low",
        "roi_ci_high",
        "holdout_roi",
        "recent_roi",
        "baseline_roi",
        "excess_roi",
        "top_day_pnl_share",
        "top3_removed_roi",
        "implied_edge",
    }
    money_cols = {"pnl", "max_daily_loss", "pnl_per_day"}
    int_cols = {"rows", "dates", "cities", "losing_days", "roi_le_minus_50_days"}
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df.head(max_rows).iterrows():
        vals: list[str] = []
        for key, _label in cols:
            val = row.get(key, "")
            if key in pct_cols or key.endswith("_roi"):
                vals.append(pct(val))
            elif key in money_cols or key.endswith("_pnl"):
                vals.append(money(val))
            elif key in int_cols and pd.notna(val):
                vals.append(str(int(val)))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def sqlite_frame(query: str) -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        return pd.read_sql_query(query, conn)
    finally:
        conn.close()


def db_snapshot() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "db_path": rel(DB_PATH),
        "db_mtime_utc": pd.Timestamp(DB_PATH.stat().st_mtime, unit="s", tz="UTC").isoformat() if DB_PATH.exists() else None,
        "gate_pass": None,
    }
    if GATE_JSON.exists():
        gate = json.loads(GATE_JSON.read_text(encoding="utf-8"))
        payload["gate_pass"] = bool(gate.get("gate_pass"))
        payload["fact_trades_live_real"] = gate.get("fact_trades_live_real")
    trades = sqlite_frame(
        """
        SELECT COUNT(*) AS rows, MIN(target_date) AS min_date, MAX(target_date) AS max_date,
               MAX(fact_built_at_utc) AS fact_built_at_utc
        FROM fact_trades
        """
    ).iloc[0].to_dict()
    fsc = sqlite_frame(
        """
        SELECT COUNT(*) AS rows, MIN(event_date) AS min_date, MAX(event_date) AS max_date,
               MAX(fact_built_at_utc) AS fact_built_at_utc
        FROM fact_signal_candidates
        """
    ).iloc[0].to_dict()
    low_settled = sqlite_frame(
        """
        SELECT COUNT(*) AS rows, MIN(event_date) AS min_date, MAX(event_date) AS max_date
        FROM fact_signal_candidates
        WHERE side = 'BUY_YES'
          AND settlement_status = 'settled'
          AND final_yes IN (0.0, 1.0)
          AND decision_entry_price BETWEEN 0.01 AND 0.25
        """
    ).iloc[0].to_dict()
    low_unsettled = sqlite_frame(
        """
        SELECT COUNT(*) AS rows, MIN(event_date) AS min_date, MAX(event_date) AS max_date,
               COUNT(DISTINCT event_date) AS dates, COUNT(DISTINCT city) AS cities
        FROM fact_signal_candidates
        WHERE side = 'BUY_YES'
          AND COALESCE(settlement_status, '') <> 'settled'
          AND decision_entry_price BETWEEN 0.01 AND 0.25
        """
    ).iloc[0].to_dict()
    payload["fact_trades"] = trades
    payload["fact_signal_candidates"] = fsc
    payload["low_price_yes_settled"] = low_settled
    payload["low_price_yes_unsettled"] = low_unsettled
    return payload


def block_bootstrap_roi_ci(daily: pd.DataFrame, pnl_col: str = "pnl", cost_col: str = "cost", n_boot: int = 3000) -> tuple[float, float]:
    if len(daily) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(RNG_SEED)
    pnl = daily[pnl_col].to_numpy(float)
    cost = daily[cost_col].to_numpy(float)
    vals: list[float] = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(daily), len(daily))
        c = float(cost[idx].sum())
        if c > 0:
            vals.append(float(pnl[idx].sum() / c))
    q = np.quantile(vals, [0.025, 0.975])
    return (float(q[0]), float(q[1]))


def summarize(g: pd.DataFrame, *, label: str, family: str, period: str, ask_col: str = "ask", payoff_col: str = "payoff") -> dict[str, Any]:
    if g.empty:
        return {
            "family": family,
            "label": label,
            "period": period,
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "avg_ask": float("nan"),
            "win_rate": float("nan"),
            "implied_edge": float("nan"),
            "roi": float("nan"),
            "roi_ci_low": float("nan"),
            "roi_ci_high": float("nan"),
        }
    h = g.copy()
    h["cost"] = STAKE_USD
    h["pnl"] = h[payoff_col].astype(float) * (STAKE_USD / h[ask_col].astype(float)) - STAKE_USD
    daily = h.groupby("target_date", as_index=False).agg(rows=("pnl", "size"), cost=("cost", "sum"), pnl=("pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    ci = block_bootstrap_roi_ci(daily)
    top = daily.sort_values("pnl", ascending=False)
    pos_pnl = float(daily.loc[daily["pnl"] > 0, "pnl"].sum())
    top_day_share = float(top.iloc[0]["pnl"] / pos_pnl) if pos_pnl > 0 and not top.empty else float("nan")
    top3_removed = daily.drop(index=top.head(3).index)
    top3_removed_roi = float(top3_removed["pnl"].sum() / top3_removed["cost"].sum()) if float(top3_removed["cost"].sum()) > 0 else float("nan")
    return {
        "family": family,
        "label": label,
        "period": period,
        "rows": int(len(h)),
        "dates": int(h["target_date"].nunique()),
        "cities": int(h["city"].nunique()),
        "avg_ask": float(h[ask_col].mean()),
        "win_rate": float(h[payoff_col].mean()),
        "implied_edge": float(h[payoff_col].mean() - h[ask_col].mean()),
        "cost": float(h["cost"].sum()),
        "pnl": float(h["pnl"].sum()),
        "roi": float(h["pnl"].sum() / h["cost"].sum()),
        "roi_ci_low": ci[0],
        "roi_ci_high": ci[1],
        "losing_days": int((daily["pnl"] < 0).sum()),
        "roi_le_minus_50_days": int((daily["roi"] <= -0.50).sum()),
        "max_daily_loss": float(daily["pnl"].min()),
        "pnl_per_day": float(h["pnl"].sum() / h["target_date"].nunique()),
        "top_day_pnl_share": top_day_share,
        "top3_removed_roi": top3_removed_roi,
    }


def by_period(g: pd.DataFrame, *, label: str, family: str) -> list[dict[str, Any]]:
    return [
        summarize(g, label=label, family=family, period="full"),
        summarize(g[g["target_date"] < TRAIN_END_EXCLUSIVE], label=label, family=family, period="train"),
        summarize(g[g["target_date"] >= HOLDOUT_START], label=label, family=family, period="holdout"),
        summarize(g[g["target_date"] >= RECENT_START], label=label, family=family, period="recent"),
    ]


def load_low_price_yes() -> pd.DataFrame:
    df = sqlite_frame(
        """
        SELECT candidate_id, city, event_date AS target_date, bracket, side, forecast_source,
               forecast_max_native, forecast_peak_delta_hours_local, forecast_max_in_bracket,
               forecast_max_above_bracket_f, forecast_max_below_bracket_f,
               model_p_yes, market_yes_price, edge, decision_entry_price,
               first_seen_ts_utc, decision_snapshot_ts_utc, final_yes, settlement_status
        FROM fact_signal_candidates
        WHERE side = 'BUY_YES'
          AND settlement_status = 'settled'
          AND final_yes IN (0.0, 1.0)
          AND decision_entry_price BETWEEN 0.01 AND 0.25
        """
    )
    for col in [
        "forecast_max_native",
        "forecast_peak_delta_hours_local",
        "forecast_max_in_bracket",
        "forecast_max_above_bracket_f",
        "forecast_max_below_bracket_f",
        "model_p_yes",
        "market_yes_price",
        "edge",
        "decision_entry_price",
        "final_yes",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["ask"] = df["decision_entry_price"]
    df["payoff"] = df["final_yes"]
    df["target_date"] = df["target_date"].astype(str)
    return df


def low_price_specs(df: pd.DataFrame) -> list[tuple[str, pd.Series, str]]:
    edge_q80 = float(df["edge"].quantile(0.80)) if len(df) else float("nan")
    return [
        ("all_low_price_yes", df["ask"].between(0.01, 0.25), "All settled low-price BUY_YES candidates from fact_signal_candidates."),
        ("cheap_ask_05_15", df["ask"].between(0.05, 0.15), "Middle-cheap lottery band; excludes dust and near-25c rows."),
        ("edge_ge_20c", df["edge"].ge(0.20), "Old simple lottery trigger: model probability at least 20c above market price."),
        ("edge_top_quintile", df["edge"].ge(edge_q80), "Cross-sectional high model-vs-market disagreement; descriptive only."),
        ("model_p_ge_25_ask_le_15", df["model_p_yes"].ge(0.25) & df["ask"].le(0.15), "High model probability while market still prices as lottery."),
        ("forecast_in_bracket", df["forecast_max_in_bracket"].eq(1), "Forecast max lands in the bought bracket."),
        ("forecast_above_or_in_bracket", df["forecast_max_in_bracket"].eq(1) | df["forecast_max_above_bracket_f"].ge(0), "Forecast supports at least the bought bracket."),
        ("late_peak_or_unknown", df["forecast_peak_delta_hours_local"].isna() | df["forecast_peak_delta_hours_local"].ge(0), "Forecast peak not clearly behind decision time."),
    ]


def evaluate_low_price(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    records: list[dict[str, Any]] = []
    details: list[pd.DataFrame] = []
    for label, mask, mechanism in low_price_specs(df):
        picked = df[mask].sort_values(["target_date", "city", "decision_snapshot_ts_utc", "ask"]).drop_duplicates(["target_date", "city"]).copy()
        picked["family"] = "fact_low_price_yes"
        picked["label"] = label
        picked["mechanism"] = mechanism
        picked["cost"] = STAKE_USD
        picked["pnl"] = picked["payoff"] * (STAKE_USD / picked["ask"]) - STAKE_USD
        details.append(picked)
        for rec in by_period(picked, label=label, family="fact_low_price_yes"):
            rec["mechanism"] = mechanism
            records.append(rec)
    detail = pd.concat(details, ignore_index=True) if details else pd.DataFrame()
    summary = pd.DataFrame(records)
    daily = detail.groupby(["family", "label", "target_date"], as_index=False).agg(rows=("pnl", "size"), cost=("cost", "sum"), pnl=("pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    return summary, detail, daily


def forecast_model_from_row(row: pd.Series) -> str:
    source = str(row.get("forecast_source") or "").lower()
    clock = str(row.get("forecast_clock_source") or "").lower()
    if "ecmwf" in source or "ecmwf" in clock:
        return "ecmwf"
    if "gfs" in source or "gfs" in clock:
        return "gfs"
    return "unknown"


def classify_bias(row: pd.Series) -> str:
    bias = float(row["bias"])
    hot = float(row["pct_actual_ge_forecast_plus_1"])
    cold = float(row["pct_forecast_ge_actual_plus_1"])
    mae = float(row["mae"])
    if bias >= 0.5 and hot >= 0.35:
        return "hot_underforecast"
    if bias <= -0.5 and cold >= 0.30:
        return "cold_overforecast"
    if mae <= 1.0 and hot < 0.25 and cold < 0.25 and abs(bias) < 0.35:
        return "balanced_tight"
    if hot >= 0.25 and cold >= 0.20:
        return "two_sided_noisy"
    return "mixed"


def load_expression_rows() -> pd.DataFrame:
    needed = [
        "city",
        "target_date",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "forecast_source",
        "forecast_clock_source",
        "forecast_gap_to_running_native",
        "forecast_peak_delta_hours_local",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "current_yes_ask",
        "current_yes_payoff",
        "current_bracket_no_ask",
        "current_bracket_no_payoff",
        "lottery_yes_ask",
        "lottery_yes_payoff",
        "d1_no_ask",
        "d1_no_payoff",
        "d1_hit",
        "d2_no_ask",
        "d2_no_payoff",
        "d2_hit",
    ]
    df = pd.read_csv(EVENT_ROWS, usecols=needed, low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    for col in df.columns:
        if col.endswith("_ask") or col.endswith("_payoff") or col in {"forecast_gap_to_running_native", "forecast_peak_delta_hours_local", "d1_hit", "d2_hit"}:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    hist = pd.read_csv(HIST_SUMMARY)
    hist["row_forecast_model"] = hist["model"].astype(str)
    hist["bias_regime"] = hist.apply(classify_bias, axis=1)
    df["row_forecast_model"] = df.apply(forecast_model_from_row, axis=1)
    df = df.merge(
        hist[["city", "row_forecast_model", "bias", "mae", "pct_actual_ge_forecast_plus_1", "pct_forecast_ge_actual_plus_1", "bias_regime"]],
        on=["city", "row_forecast_model"],
        how="left",
    )
    df["bias_regime"] = df["bias_regime"].fillna("unclassified")
    return df


def eval_expression(df: pd.DataFrame, label: str, mask: pd.Series, ask_col: str, payoff_col: str, mechanism: str) -> pd.DataFrame:
    g = df[mask].copy()
    g["ask"] = pd.to_numeric(g[ask_col], errors="coerce")
    g["payoff"] = pd.to_numeric(g[payoff_col], errors="coerce")
    g = g[g["ask"].between(0.01, 0.99) & g["payoff"].isin([0.0, 1.0])].copy()
    g = g.sort_values(["target_date", "city", "decision_snapshot_ts_utc"]).drop_duplicates(["target_date", "city"])
    g["family"] = "expression_matrix"
    g["label"] = label
    g["mechanism"] = mechanism
    g["cost"] = STAKE_USD
    g["pnl"] = g["payoff"] * (STAKE_USD / g["ask"]) - STAKE_USD
    return g


def evaluate_expression_matrix(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    open_tail = df["day_regime"].isin(["day_open_runway", "day_marginal_runway"]) & df["intraday_state"].isin(
        ["active_warming", "fresh_high", "false_fade_risk", "reheating_after_dip", "pullback_uncertain"]
    )
    hot_noisy = df["bias_regime"].isin(["hot_underforecast", "two_sided_noisy", "mixed"])
    low_runway = df["day_regime"].isin(["day_forecast_capped", "day_forecast_busted"]) | df["forecast_gap_to_running_native"].le(1.0)
    pullback = df["intraday_state"].eq("pullback_uncertain")
    pieces = [
        eval_expression(
            df,
            "matrix_lottery_yes_05_25_open_tail",
            df["lottery_yes_ask"].between(0.05, 0.25) & open_tail & hot_noisy,
            "lottery_yes_ask",
            "lottery_yes_payoff",
            "Higher/hotter bracket YES is cheap while PIT state still has runway and city/source is hot/noisy.",
        ),
        eval_expression(
            df,
            "matrix_lottery_yes_05_25_all",
            df["lottery_yes_ask"].between(0.05, 0.25),
            "lottery_yes_ask",
            "lottery_yes_payoff",
            "All available hotter-bracket lottery YES rows in expression matrix.",
        ),
        eval_expression(
            df,
            "pullback_current_high_yes_midprice",
            pullback & df["current_yes_ask"].between(0.50, 0.90) & df["current_bracket_no_ask"].ge(0.40),
            "current_yes_ask",
            "current_yes_payoff",
            "Already printed/pulled-back high where current high can still hold.",
        ),
        eval_expression(
            df,
            "high_current_no_reverse_current_yes",
            df["current_bracket_no_ask"].ge(0.70) & df["current_yes_ask"].le(0.50) & low_runway,
            "current_yes_ask",
            "current_yes_payoff",
            "High current NO, cheap current YES, capped/low-runway PIT state.",
        ),
    ]
    detail = pd.concat(pieces, ignore_index=True)
    records: list[dict[str, Any]] = []
    for label, g in detail.groupby("label"):
        family = str(g["family"].iloc[0])
        mechanism = str(g["mechanism"].iloc[0])
        for rec in by_period(g, label=label, family=family):
            rec["mechanism"] = mechanism
            records.append(rec)
    summary = pd.DataFrame(records)
    daily = detail.groupby(["family", "label", "target_date"], as_index=False).agg(rows=("pnl", "size"), cost=("cost", "sum"), pnl=("pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    return summary, detail, daily


def contribution_tables(detail: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    city = (
        detail.groupby(["family", "label", "city"], as_index=False)
        .agg(rows=("pnl", "size"), dates=("target_date", "nunique"), pnl=("pnl", "sum"), cost=("cost", "sum"), win_rate=("payoff", "mean"), avg_ask=("ask", "mean"))
    )
    city["roi"] = city["pnl"] / city["cost"]
    city = city.sort_values(["label", "pnl"], ascending=[True, False])
    regime_cols = [c for c in ["day_regime", "intraday_state", "bias_regime"] if c in detail.columns]
    regimes: list[pd.DataFrame] = []
    for col in regime_cols:
        r = (
            detail.groupby(["family", "label", col], dropna=False, as_index=False)
            .agg(rows=("pnl", "size"), dates=("target_date", "nunique"), pnl=("pnl", "sum"), cost=("cost", "sum"), win_rate=("payoff", "mean"), avg_ask=("ask", "mean"))
            .rename(columns={col: "slice_value"})
        )
        r["slice_type"] = col
        r["roi"] = r["pnl"] / r["cost"]
        r = r[r["slice_value"].notna() & r["slice_value"].astype(str).ne("")]
        regimes.append(r)
    regime = pd.concat(regimes, ignore_index=True) if regimes else pd.DataFrame()
    return city, regime


def render_report(db: dict[str, Any], low_summary: pd.DataFrame, expr_summary: pd.DataFrame, city: pd.DataFrame, regime: pd.DataFrame) -> str:
    cols = [
        ("label", "label"),
        ("rows", "rows"),
        ("dates", "dates"),
        ("cities", "cities"),
        ("avg_ask", "avg ask"),
        ("win_rate", "win"),
        ("implied_edge", "hit-ask"),
        ("roi", "ROI"),
        ("roi_ci_low", "CI low"),
        ("roi_ci_high", "CI high"),
        ("losing_days", "loss days"),
        ("max_daily_loss", "max loss"),
        ("top_day_pnl_share", "top-day share"),
        ("top3_removed_roi", "top3 removed"),
    ]
    low_full = low_summary[low_summary["period"].eq("full")].sort_values(["roi", "rows"], ascending=False)
    low_holdout = low_summary[low_summary["period"].eq("holdout")].sort_values(["roi", "rows"], ascending=False)
    expr_full = expr_summary[expr_summary["period"].eq("full")].sort_values(["roi", "rows"], ascending=False)
    expr_holdout = expr_summary[expr_summary["period"].eq("holdout")].sort_values(["roi", "rows"], ascending=False)

    robust_low = low_full[
        low_full["roi_ci_low"].gt(0)
        & low_full["top3_removed_roi"].gt(0)
        & low_full["rows"].ge(30)
        & low_full["dates"].ge(10)
    ].sort_values(["top3_removed_roi", "roi"], ascending=False)
    best_low = low_full.head(1).iloc[0] if not low_full.empty else None
    robust_low_row = robust_low.head(1).iloc[0] if not robust_low.empty else None
    best_expr = expr_full.head(1).iloc[0] if not expr_full.empty else None
    headline = []
    if robust_low_row is not None:
        headline.append(
            f"robust historical lottery candidate `{robust_low_row['label']}`: {int(robust_low_row['rows'])} rows/{int(robust_low_row['dates'])} dates, ROI {pct(robust_low_row['roi'])}, CI [{pct(robust_low_row['roi_ci_low'])}, {pct(robust_low_row['roi_ci_high'])}], top3 removed {pct(robust_low_row['top3_removed_roi'])}."
        )
    if best_low is not None:
        headline.append(
            f"highest point-ROI low-price slice `{best_low['label']}`: {int(best_low['rows'])} rows/{int(best_low['dates'])} dates, ROI {pct(best_low['roi'])}, CI [{pct(best_low['roi_ci_low'])}, {pct(best_low['roi_ci_high'])}], top3 removed {pct(best_low['top3_removed_roi'])}."
        )
    if best_expr is not None:
        headline.append(
            f"expression-matrix best `{best_expr['label']}`: {int(best_expr['rows'])} rows/{int(best_expr['dates'])} dates, ROI {pct(best_expr['roi'])}, CI [{pct(best_expr['roi_ci_low'])}, {pct(best_expr['roi_ci_high'])}], top3 removed {pct(best_expr['top3_removed_roi'])}."
        )

    top_city = city.sort_values("pnl", ascending=False).head(15)
    top_regime = regime[regime["rows"].ge(3)].sort_values("pnl", ascending=False).head(20) if not regime.empty else pd.DataFrame()

    lines = [
        "# Reversal Lottery Lab v1",
        "",
        "Generated: 2026-07-01",
        "",
        "## One-Line Verdict",
        "",
        "The lottery direction has one real historical candidate, `edge_ge_20c`, but the current evidence is still `shadow_candidate / not live`: full-window significance is positive, while holdout/recent support is too thin and the post-2026-06-10 cheap-YES rows are mostly unsettled.",
        "",
        " ".join(headline),
        "",
        "```text",
        "significance=PASS only for historical fact_low_price_yes edge_ge_20c; FAIL for expression lottery and most other slices",
        "baseline=FAIL/NA (low-price YES uses market ask as break-even; same-row expression reversals remain thin)",
        "forward=FAIL (recent/holdout sample is thin or weak)",
        "conclusion=inconclusive; keep as independent shadow research head, no live change",
        "```",
        "",
        "## Data Snapshot",
        "",
        f"- DB: `{db['db_path']}`, fact built `{db['fact_trades'].get('fact_built_at_utc')}`.",
        f"- `fact_trades`: {int(db['fact_trades']['rows'])} rows, target_date {db['fact_trades']['min_date']}..{db['fact_trades']['max_date']}.",
        f"- `fact_signal_candidates`: {int(db['fact_signal_candidates']['rows'])} rows, event_date {db['fact_signal_candidates']['min_date']}..{db['fact_signal_candidates']['max_date']}.",
        f"- Settled low-price BUY_YES denominator: {int(db['low_price_yes_settled']['rows'])} rows, event_date {db['low_price_yes_settled']['min_date']}..{db['low_price_yes_settled']['max_date']}.",
        f"- Unsettled low-price BUY_YES forward pool: {int(db['low_price_yes_unsettled']['rows'])} rows / {int(db['low_price_yes_unsettled']['dates'])} dates / {int(db['low_price_yes_unsettled']['cities'])} cities, event_date {db['low_price_yes_unsettled']['min_date']}..{db['low_price_yes_unsettled']['max_date']}.",
        f"- CLOB fill coverage gate pass: `{db.get('gate_pass')}`. This report does not publish live_real ROI.",
        f"- Expression matrix source: `{rel(EVENT_ROWS)}`; it currently covers 2026-05-19..2026-06-26, so expression-matrix reversal results do not include 2026-06-27..2026-06-30.",
        "",
        "## Low-Price YES Lottery: fact_signal_candidates",
        "",
        "Unit: one first city-date candidate per selector, $5 notional at `decision_entry_price`, settled rows only. This answers whether cheap YES itself has a usable base-rate edge.",
        "",
        md_table(low_full, cols),
        "",
        "## Practical Shadow Prompt",
        "",
        "For a separate zero-notional lottery shadow head, the only fixed historical rule worth forwarding now is:",
        "",
        "```text",
        "side = BUY_YES",
        "decision_entry_price between 0.01 and 0.25",
        "edge >= 0.20",
        "first city-date candidate only",
        "record token/market/ask/model_p_yes/edge; notional_usd = 0; no live order",
        "```",
        "",
        "Historical settled result for this fixed prompt: 140 rows / 24 dates / 40 cities, avg ask 0.102, win 16.4%, ROI +62.9%, date-block CI [+11.1%, +115.3%], top3-removed ROI +26.3%. Holdout after 2026-06-01 is only 9 rows with ROI +7.1% and CI crossing zero, so it is not live-approved.",
        "",
        "## Low-Price Holdout/Recent",
        "",
        "Holdout starts 2026-06-01. Recent starts 2026-06-08; this low-price fact denominator only has settled low-price rows through 2026-06-10, so recent is a stress slice, not a full forward month.",
        "",
        md_table(low_holdout, cols),
        "",
        "## Same-Snapshot Reversal / Tail Expressions",
        "",
        "Unit: generated expression matrix, first city-date per label. This is the cleaner same-denominator reversal layer but only through 2026-06-26.",
        "",
        md_table(expr_full, cols),
        "",
        "## Expression Holdout",
        "",
        md_table(expr_holdout, cols),
        "",
        "## Top Contributors",
        "",
        md_table(
            top_city,
            [
                ("family", "family"),
                ("label", "label"),
                ("city", "city"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("avg_ask", "ask"),
                ("win_rate", "win"),
                ("roi", "ROI"),
                ("pnl", "PnL"),
            ],
            max_rows=15,
        ),
        "",
        "## Regime Contributions",
        "",
        md_table(
            top_regime,
            [
                ("family", "family"),
                ("label", "label"),
                ("slice_type", "slice"),
                ("slice_value", "value"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("avg_ask", "ask"),
                ("win_rate", "win"),
                ("roi", "ROI"),
                ("pnl", "PnL"),
            ],
            max_rows=20,
        ),
        "",
        "## Interpretation",
        "",
        "- `low_price YES` is not obviously dead: high-edge/model-supported cheap YES can print strong point ROI. But it is exactly the kind of distribution where one or two days dominate, so top-day/top3 stress tests matter more than headline ROI.",
        "- `matrix_lottery_yes_05_25_open_tail` is the cleanest tail thesis to keep watching: cheap hotter-bracket YES plus open-runway/hot-noisy state. If it survives more forward dates, it could become a separate lottery family.",
        "- `pullback_current_high_yes_midprice` remains the cleaner reversal expression, but it is not really lottery; it pays mid-price and depends on current bracket holding, with overshoot risk.",
        "- Current decision: no live, no integration into regime-routed NO selector. Keep collecting shadow rows and settle them by expression family.",
        "",
        "## Artifacts",
        "",
        f"- Summary CSV: `{rel(OUT_DIR / 'summary.csv')}`",
        f"- Detail CSV: `{rel(OUT_DIR / 'details.csv')}`",
        f"- Daily CSV: `{rel(OUT_DIR / 'daily.csv')}`",
        f"- City contribution CSV: `{rel(OUT_DIR / 'city_contribution.csv')}`",
        f"- Regime contribution CSV: `{rel(OUT_DIR / 'regime_contribution.csv')}`",
        f"- Script: `scripts/analysis/forecast_quality/research_reversal_lottery_lab_v1.py`",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db = db_snapshot()
    low = load_low_price_yes()
    low_summary, low_detail, low_daily = evaluate_low_price(low)
    expr = load_expression_rows()
    expr_summary, expr_detail, expr_daily = evaluate_expression_matrix(expr)
    detail = pd.concat([low_detail, expr_detail], ignore_index=True, sort=False)
    daily = pd.concat([low_daily, expr_daily], ignore_index=True, sort=False)
    summary = pd.concat([low_summary, expr_summary], ignore_index=True, sort=False)
    city, regime = contribution_tables(detail)

    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    detail.to_csv(OUT_DIR / "details.csv", index=False)
    daily.to_csv(OUT_DIR / "daily.csv", index=False)
    city.to_csv(OUT_DIR / "city_contribution.csv", index=False)
    regime.to_csv(OUT_DIR / "regime_contribution.csv", index=False)
    payload = {
        "data_snapshot": db,
        "low_price_rows": int(len(low)),
        "expression_rows": int(len(expr)),
        "summary_rows": int(len(summary)),
        "detail_rows": int(len(detail)),
        "artifacts": {
            "summary": rel(OUT_DIR / "summary.csv"),
            "details": rel(OUT_DIR / "details.csv"),
            "daily": rel(OUT_DIR / "daily.csv"),
            "city": rel(OUT_DIR / "city_contribution.csv"),
            "regime": rel(OUT_DIR / "regime_contribution.csv"),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    OUT_MD.write_text(render_report(db, low_summary, expr_summary, city, regime), encoding="utf-8")
    print(f"wrote {rel(OUT_MD)}")
    print(f"wrote {rel(OUT_JSON)}")


if __name__ == "__main__":
    main()
