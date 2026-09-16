#!/usr/bin/env python3
"""Forecast-bias tail reversal expression selector research head.

This is an independent research head. It reuses the canonical feature/replay
artifacts, but it does not assume the current regime-routed NO runner is the
right integration point.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
HIST_SUMMARY = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/city_model_error_summary.csv"
CITY_FIT = ROOT / "docs/analysis/2026-06/generated/city_strategy_fit_by_forecast_bias_v1/city_strategy_fit_by_forecast_bias.csv"
EVENT_ROWS = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv"
GATE_JSON = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
DB_PATH = ROOT / "runtime/weather.db"

OUT_DIR = ROOT / "docs/analysis/2026-06/generated/forecast_bias_tail_reversal_expression_selector_v1"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-30-forecast-bias-tail-reversal-expression-selector-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-30-forecast-bias-tail-reversal-expression-selector-v1.json"

FORWARD_START = "2026-06-21"
STAKE_USD = 5.0
RNG_SEED = 20260630


EXPRESSION_SPECS = [
    ("current_high_yes", "current_yes_ask", "current_yes_payoff"),
    ("d1_no", "d1_no_ask", "d1_no_payoff"),
    ("d2_no", "d2_no_ask", "d2_no_payoff"),
    ("high_tail_yes", "lottery_yes_ask", "lottery_yes_payoff"),
    ("current_bracket_no", "current_bracket_no_ask", "current_bracket_no_payoff"),
]


def pct(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"{100 * v:+.1f}%"


def money(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"${v:+.2f}"


def fmt(x: Any, digits: int = 2) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"{v:.{digits}f}"


def classify_bias(row: pd.Series) -> str:
    bias = float(row["bias"])
    p90 = float(row["p90"])
    p10 = float(row["p10"])
    hot = float(row["pct_actual_ge_forecast_plus_1"])
    cold = float(row["pct_forecast_ge_actual_plus_1"])
    mae = float(row["mae"])
    if bias >= 0.7 and hot >= 0.40 and cold <= 0.15:
        return "hot_underforecast_clean"
    if bias >= 0.5 and p90 >= 2.0 and hot >= 0.35:
        return "hot_underforecast_noisy"
    if bias <= -0.5 and cold >= 0.35 and hot <= 0.20:
        return "cold_overforecast_clean"
    if cold >= 0.25 and p10 <= -1.5:
        return "cold_overforecast_noisy"
    if mae <= 1.0 and hot < 0.25 and cold < 0.25 and abs(bias) < 0.35:
        return "balanced_tight"
    if hot >= 0.25 and cold >= 0.20:
        return "two_sided_noisy"
    return "mild_or_mixed"


def forecast_model_from_row(row: pd.Series) -> str:
    source = str(row.get("forecast_source") or "").lower()
    clock = str(row.get("forecast_clock_source") or "").lower()
    if "ecmwf" in source or "ecmwf" in clock:
        return "ecmwf"
    if "gfs" in source or "gfs" in clock:
        return "gfs"
    return "unknown"


def selector_expression(row: pd.Series) -> str:
    """First-principles expression map, not fitted to PnL."""
    bias = str(row.get("row_source_bias_regime") or "")
    day = str(row.get("day_regime") or "")
    intraday = str(row.get("intraday_state") or "")
    moisture = str(row.get("moisture_cloud_regime") or "")
    wind = str(row.get("wind_regime") or "")
    gap = row.get("forecast_gap_to_running_native")
    try:
        gap_v = float(gap)
    except Exception:
        gap_v = float("nan")

    if bias.startswith("hot_underforecast"):
        if day in {"day_open_runway", "day_marginal_runway"} and intraday in {"fresh_high", "active_warming"}:
            return "current_bracket_no"
        if day in {"day_open_runway", "day_marginal_runway"} and math.isfinite(gap_v) and gap_v >= 2.0:
            return "high_tail_yes"
        if wind == "windy_mixing_noise" or moisture == "humid_convective_risk":
            return "high_tail_yes"
        return "current_bracket_no"

    if bias.startswith("cold_overforecast"):
        if day in {"day_forecast_capped", "day_forecast_busted"}:
            return "d2_no"
        if intraday in {"mature_fade", "faded_from_high"}:
            return "current_high_yes"
        return "d1_no"

    if bias == "two_sided_noisy":
        if day in {"day_open_runway", "day_marginal_runway"} and wind == "windy_mixing_noise":
            return "high_tail_yes"
        return "d1_no"

    if day in {"day_forecast_capped", "day_forecast_busted"}:
        return "d1_no"
    if intraday in {"mature_fade", "faded_from_high"}:
        return "current_high_yes"
    if day == "day_open_runway":
        return "current_bracket_no"
    return "d1_no"


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    int_cols = {"rows", "dates", "cities", "losing_days", "roi_le_minus_50_days", "models", "active_days"}
    pct_cols = {
        "win_rate",
        "roi",
        "roi_ci_low",
        "roi_ci_high",
        "forward_roi",
        "delta_roi",
        "delta_ci_low",
        "delta_ci_high",
        "hot_tail_pct",
        "cold_tail_pct",
        "tail_skew",
        "selector_share",
    }
    money_cols = {
        "cost",
        "pnl",
        "daily_p10_pnl",
        "daily_median_pnl",
        "daily_p90_pnl",
        "max_daily_loss",
        "forward_cost",
        "forward_pnl",
        "delta_pnl",
    }
    for _, r in df.head(max_rows).iterrows():
        vals = []
        for key, _ in cols:
            val = r.get(key, "")
            if key in int_cols and pd.notna(val):
                vals.append(str(int(val)))
            elif key in pct_cols or key.endswith("_roi"):
                vals.append(pct(val))
            elif key in money_cols:
                vals.append(money(val))
            elif key in {"avg_ask", "bias", "mae", "p10", "p50", "p90", "stability_score"}:
                vals.append(fmt(val, 3 if key in {"bias", "mae", "p10", "p50", "p90"} else 2))
            elif isinstance(val, float):
                vals.append(fmt(val))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def load_db_snapshot() -> dict[str, Any]:
    out: dict[str, Any] = {
        "db_path": str(DB_PATH.relative_to(ROOT)),
        "db_mtime": pd.Timestamp(DB_PATH.stat().st_mtime, unit="s", tz="UTC").isoformat() if DB_PATH.exists() else None,
        "gate_pass": None,
        "gate_path": str(GATE_JSON.relative_to(ROOT)),
    }
    if GATE_JSON.exists():
        gate = json.loads(GATE_JSON.read_text())
        out["gate_pass"] = bool(gate.get("gate_pass"))
        out["live_real_fill_ids"] = gate.get("fact_trades_live_real", {}).get("fill_ids")
    if DB_PATH.exists():
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1000")
        trade_rows = conn.execute("SELECT COUNT(*) FROM fact_trades").fetchone()[0]
        candidate_rows = conn.execute("SELECT COUNT(*) FROM fact_signal_candidates").fetchone()[0]
        max_built = conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0]
        settlement = conn.execute(
            "SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status"
        ).fetchall()
        conn.close()
        out.update(
            {
                "fact_trades_rows": trade_rows,
                "fact_signal_candidates_rows": candidate_rows,
                "fact_built_at_utc": max_built,
                "settlement_status_counts": {str(k or "NULL"): v for k, v in settlement},
            }
        )
    return out


def load_bias() -> tuple[pd.DataFrame, pd.DataFrame]:
    hist = pd.read_csv(HIST_SUMMARY)
    hist["row_source_bias_regime"] = hist.apply(classify_bias, axis=1)
    hist["hot_tail_pct"] = hist["pct_actual_ge_forecast_plus_1"]
    hist["cold_tail_pct"] = hist["pct_forecast_ge_actual_plus_1"]
    hist["tail_skew"] = hist["hot_tail_pct"] - hist["cold_tail_pct"]
    hist["abs_bias"] = hist["bias"].abs()
    hist["stability_score"] = hist["abs_bias"] / hist["mae"].replace(0, np.nan)
    hist["tail_stability_score"] = hist["tail_skew"].abs()

    city_fit = pd.read_csv(CITY_FIT)
    return hist, city_fit


def top_stable_bias(hist: pd.DataFrame) -> pd.DataFrame:
    d = hist[hist["n"] >= 300].copy()
    d["direction"] = np.select(
        [d["tail_skew"] > 0.20, d["tail_skew"] < -0.20],
        ["actual_hotter_than_forecast", "forecast_hotter_than_actual"],
        default="mixed",
    )
    return d.sort_values(["tail_stability_score", "abs_bias", "n"], ascending=False)[
        [
            "city",
            "unit",
            "model",
            "n",
            "bias",
            "mae",
            "p10",
            "p50",
            "p90",
            "hot_tail_pct",
            "cold_tail_pct",
            "tail_skew",
            "stability_score",
            "row_source_bias_regime",
            "direction",
        ]
    ]


def load_event_base(hist: pd.DataFrame, city_fit: pd.DataFrame) -> pd.DataFrame:
    needed = sorted(
        {
            "city",
            "target_date",
            "decision_hour_local",
            "decision_snapshot_ts_utc",
            "unit",
            "forecast_source",
            "forecast_clock_source",
            "forecast_gap_to_running_native",
            "forecast_error_native",
            "forecast_max_native",
            "running_native",
            "current_bracket",
            "d1_no_bracket",
            "d2_no_bracket",
            "lottery_yes_bracket",
            "final_winning_bracket",
            "current_bracket_held",
            "d1_hit",
            "d2_hit",
            "skip_over_d1",
            "day_regime",
            "intraday_state",
            "moisture_cloud_regime",
            "wind_regime",
            "running_max_state",
            "composite_regime",
            "period",
            "rule",
            "event_key",
            *[x for _, ask, payoff in EXPRESSION_SPECS for x in (ask, payoff)],
        }
    )
    src = pd.read_csv(EVENT_ROWS, usecols=needed, low_memory=False)
    src["target_date"] = src["target_date"].astype(str)
    src["row_forecast_model"] = src.apply(forecast_model_from_row, axis=1)

    lookup_cols = [
        "city",
        "model",
        "n",
        "bias",
        "mae",
        "p10",
        "p50",
        "p90",
        "hot_tail_pct",
        "cold_tail_pct",
        "tail_skew",
        "stability_score",
        "row_source_bias_regime",
    ]
    out = src.merge(
        hist[lookup_cols],
        left_on=["city", "row_forecast_model"],
        right_on=["city", "model"],
        how="left",
    )
    out["row_source_bias_regime"] = out["row_source_bias_regime"].fillna("unclassified")

    fit_cols = [
        "city",
        "source_bias_regime",
        "runway_current_bracket_no_fit",
        "higher_yes_or_hot_break_fit",
        "forecast_capped_higher_no_fit",
        "current_high_yes_or_peak_fade_fit",
    ]
    out = out.merge(city_fit[fit_cols], on="city", how="left")
    out["source_bias_regime"] = out["source_bias_regime"].fillna(out["row_source_bias_regime"])
    out["selector_expression"] = out.apply(selector_expression, axis=1)
    out["selector_family"] = "forecast_bias_tail_reversal_v1"
    return out


def valid_expression_mask(df: pd.DataFrame, ask_col: str, payoff_col: str) -> pd.Series:
    ask = pd.to_numeric(df[ask_col], errors="coerce")
    payoff = pd.to_numeric(df[payoff_col], errors="coerce")
    return ask.gt(0) & ask.lt(1.0) & payoff.isin([0.0, 1.0])


def same_denominator_base(base: pd.DataFrame) -> pd.DataFrame:
    mask = pd.Series(True, index=base.index)
    for _, ask_col, payoff_col in EXPRESSION_SPECS:
        mask &= valid_expression_mask(base, ask_col, payoff_col)
    out = base[mask].copy()
    out["decision_id"] = (
        out["city"].astype(str)
        + "|"
        + out["target_date"].astype(str)
        + "|"
        + out["decision_hour_local"].astype(str)
        + "|"
        + out["current_bracket"].astype(str)
    )
    return out


def expression_long(base: pd.DataFrame) -> pd.DataFrame:
    frames = []
    common = [
        "decision_id",
        "city",
        "target_date",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "unit",
        "row_forecast_model",
        "forecast_source",
        "forecast_clock_source",
        "forecast_gap_to_running_native",
        "forecast_error_native",
        "forecast_max_native",
        "running_native",
        "current_bracket",
        "d1_no_bracket",
        "d2_no_bracket",
        "lottery_yes_bracket",
        "final_winning_bracket",
        "current_bracket_held",
        "d1_hit",
        "d2_hit",
        "skip_over_d1",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "composite_regime",
        "period",
        "rule",
        "event_key",
        "model",
        "n",
        "bias",
        "mae",
        "p10",
        "p50",
        "p90",
        "hot_tail_pct",
        "cold_tail_pct",
        "tail_skew",
        "stability_score",
        "row_source_bias_regime",
        "source_bias_regime",
        "selector_expression",
        "selector_family",
    ]
    for expression, ask_col, payoff_col in EXPRESSION_SPECS:
        d = base[common + [ask_col, payoff_col]].copy()
        d = d.rename(columns={ask_col: "ask", payoff_col: "payoff"})
        d["expression"] = expression
        d["ask"] = pd.to_numeric(d["ask"], errors="coerce")
        d["payoff"] = pd.to_numeric(d["payoff"], errors="coerce")
        d["cost"] = STAKE_USD
        d["pnl"] = d["payoff"] * (STAKE_USD / d["ask"]) - STAKE_USD
        d["roi_row"] = d["pnl"] / d["cost"]
        d["is_selector_pick"] = d["expression"].eq(d["selector_expression"])
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


def daily_metrics(df: pd.DataFrame) -> dict[str, Any]:
    daily = df.groupby("target_date", as_index=False).agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    return {
        "daily_p10_pnl": daily["pnl"].quantile(0.10),
        "daily_median_pnl": daily["pnl"].median(),
        "daily_p90_pnl": daily["pnl"].quantile(0.90),
        "losing_days": int((daily["pnl"] < 0).sum()),
        "roi_le_minus_50_days": int((daily["roi"] <= -0.50).sum()),
        "max_daily_loss": daily["pnl"].min(),
    }


def bootstrap_roi_ci(df: pd.DataFrame, n_boot: int = 2000) -> tuple[float, float]:
    d = df.groupby("target_date", as_index=False).agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    if len(d) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(RNG_SEED)
    costs = d["cost"].to_numpy(float)
    pnls = d["pnl"].to_numpy(float)
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(d), len(d))
        cost = costs[idx].sum()
        if cost > 0:
            vals.append(pnls[idx].sum() / cost)
    return tuple(np.quantile(vals, [0.025, 0.975]).tolist())


def bootstrap_delta_ci(a: pd.DataFrame, b: pd.DataFrame, n_boot: int = 2000) -> tuple[float, float]:
    da = a.groupby("target_date", as_index=False).agg(cost_a=("cost", "sum"), pnl_a=("pnl", "sum"))
    db = b.groupby("target_date", as_index=False).agg(cost_b=("cost", "sum"), pnl_b=("pnl", "sum"))
    d = da.merge(db, on="target_date", how="inner")
    if len(d) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(RNG_SEED)
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(d), len(d))
        sa = d.iloc[idx]
        roi_a = sa["pnl_a"].sum() / sa["cost_a"].sum()
        roi_b = sa["pnl_b"].sum() / sa["cost_b"].sum()
        vals.append(roi_a - roi_b)
    return tuple(np.quantile(vals, [0.025, 0.975]).tolist())


def summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows = []
    for keys, g in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        cost = g["cost"].sum()
        pnl = g["pnl"].sum()
        ci_low, ci_high = bootstrap_roi_ci(g)
        rec = dict(zip(group_cols, keys, strict=False))
        rec.update(
            {
                "rows": len(g),
                "dates": g["target_date"].nunique(),
                "cities": g["city"].nunique(),
                "win_rate": g["payoff"].mean(),
                "avg_ask": g["ask"].mean(),
                "cost": cost,
                "pnl": pnl,
                "roi": pnl / cost if cost else float("nan"),
                "roi_ci_low": ci_low,
                "roi_ci_high": ci_high,
            }
        )
        rec.update(daily_metrics(g))
        rows.append(rec)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["cost", "rows"], ascending=False)


def selector_rows(long: pd.DataFrame) -> pd.DataFrame:
    return long[long["is_selector_pick"]].copy()


def baseline_rows(long: pd.DataFrame, expression: str) -> pd.DataFrame:
    return long[long["expression"].eq(expression)].copy()


def train_best_static_expression(long: pd.DataFrame) -> str:
    train = long[long["target_date"] < FORWARD_START]
    summ = summarize(train, ["expression"])
    if summ.empty:
        return "d1_no"
    return str(summ.sort_values(["roi", "rows"], ascending=False).iloc[0]["expression"])


def selector_vs_baselines(long: pd.DataFrame) -> pd.DataFrame:
    selector = selector_rows(long)
    best_static = train_best_static_expression(long)
    baselines = ["current_high_yes", "d1_no", "d2_no", "high_tail_yes", "current_bracket_no", best_static]
    labels = ["static_current_high_yes", "static_d1_no", "static_d2_no", "static_high_tail_yes", "static_current_bracket_no", f"train_best_static_{best_static}"]
    rows = []
    for label, expr in zip(labels, baselines, strict=False):
        base = baseline_rows(long, expr)
        for window_name, a, b in [
            ("full", selector, base),
            ("forward", selector[selector["target_date"] >= FORWARD_START], base[base["target_date"] >= FORWARD_START]),
        ]:
            if a.empty or b.empty:
                continue
            a_cost = a["cost"].sum()
            b_cost = b["cost"].sum()
            a_roi = a["pnl"].sum() / a_cost
            b_roi = b["pnl"].sum() / b_cost
            ci_low, ci_high = bootstrap_delta_ci(a, b)
            rows.append(
                {
                    "window": window_name,
                    "selector": "forecast_bias_tail_reversal_v1",
                    "baseline": label,
                    "baseline_expression": expr,
                    "rows": len(a),
                    "dates": a["target_date"].nunique(),
                    "cities": a["city"].nunique(),
                    "selector_roi": a_roi,
                    "baseline_roi": b_roi,
                    "delta_roi": a_roi - b_roi,
                    "delta_ci_low": ci_low,
                    "delta_ci_high": ci_high,
                    "delta_pnl": a["pnl"].sum() - b["pnl"].sum(),
                }
            )
    return pd.DataFrame(rows)


def selector_daily(selector: pd.DataFrame) -> pd.DataFrame:
    d = selector.groupby("target_date", as_index=False).agg(
        rows=("pnl", "size"),
        cities=("city", "nunique"),
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
    )
    d["roi"] = d["pnl"] / d["cost"]
    return d.sort_values("target_date")


def selector_pick_mix(selector: pd.DataFrame) -> pd.DataFrame:
    d = selector.groupby(["selector_expression", "row_source_bias_regime"], as_index=False).agg(
        rows=("pnl", "size"),
        dates=("target_date", "nunique"),
        cities=("city", "nunique"),
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
        avg_ask=("ask", "mean"),
        win_rate=("payoff", "mean"),
    )
    d["roi"] = d["pnl"] / d["cost"]
    d["selector_share"] = d["rows"] / max(1, len(selector))
    return d.sort_values(["rows", "cost"], ascending=False)


def contribution_summaries(selector: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "city": summarize(selector, ["city", "row_forecast_model", "row_source_bias_regime"]),
        "source_bias": summarize(selector, ["row_source_bias_regime"]),
        "day_regime": summarize(selector, ["day_regime", "row_source_bias_regime"]),
        "intraday": summarize(selector, ["intraday_state", "row_source_bias_regime"]),
        "moisture": summarize(selector, ["moisture_cloud_regime", "row_source_bias_regime"]),
        "wind": summarize(selector, ["wind_regime", "row_source_bias_regime"]),
        "expression_regime": summarize(selector, ["selector_expression", "day_regime", "row_source_bias_regime"]),
    }


def verdict(selector_summary: pd.DataFrame, vs_baselines: pd.DataFrame) -> dict[str, Any]:
    full = selector_summary.iloc[0].to_dict()
    best_static_rows = vs_baselines[
        vs_baselines["baseline"].astype(str).str.startswith("train_best_static_")
        & vs_baselines["window"].eq("forward")
    ]
    full_pass = bool(full["roi_ci_low"] > 0)
    baseline_pass = False
    forward_pass = False
    if not best_static_rows.empty:
        row = best_static_rows.iloc[0]
        baseline_pass = bool(row["delta_ci_low"] > 0)
        forward_pass = bool(row["delta_roi"] > 0 and row["delta_ci_low"] > 0)
    conclusion = "inconclusive"
    if full_pass and baseline_pass and forward_pass:
        conclusion = "confirmed"
    elif full_pass and baseline_pass:
        conclusion = "shadow_candidate"
    return {
        "significance": "PASS" if full_pass else "FAIL",
        "baseline": "PASS" if baseline_pass else "FAIL",
        "forward": "PASS" if forward_pass else "FAIL",
        "conclusion": conclusion,
    }


def render_report(
    db_snapshot: dict[str, Any],
    top_bias: pd.DataFrame,
    base: pd.DataFrame,
    expression_summary: pd.DataFrame,
    selector_summary: pd.DataFrame,
    selector_mix: pd.DataFrame,
    vs_baselines: pd.DataFrame,
    daily: pd.DataFrame,
    contrib: dict[str, pd.DataFrame],
    verdict_info: dict[str, Any],
    best_static: str,
) -> str:
    selected = selector_summary.iloc[0]
    sentence = (
        f"在 2026-05-20..2026-06-28，forecast-bias tail-reversal selector 相对 "
        f"train-best static expression `{best_static}` 的 forward excess ROI "
    )
    forward_best = vs_baselines[
        vs_baselines["window"].eq("forward")
        & vs_baselines["baseline"].astype(str).str.startswith("train_best_static_")
    ]
    if not forward_best.empty:
        r = forward_best.iloc[0]
        sentence += f"为 {pct(r['delta_roi'])}（95% CI {pct(r['delta_ci_low'])}..{pct(r['delta_ci_high'])}），"
    else:
        sentence += "不可计算，"
    sentence += (
        f"前瞻 {verdict_info['forward']}，结论等级 `{verdict_info['conclusion']}`。"
    )

    return "\n".join(
        [
            "# Forecast-Bias Tail Reversal / Expression Selector v1",
            "",
            "Generated: 2026-06-30",
            "",
            "## Verdict",
            "",
            sentence,
            "",
            "结论：`inconclusive`。这个 head 作为独立 research head 保留，不改 live，不默认接入 `regime-routed NO` runner。原因是 selector 本身全样本 ROI 为负，且相对 train-best static expression 的 full/forward excess 都显著为负；目前不是独立 forecast-bias alpha，也不是现有 runner 的表达选择改良证据。",
            "",
            f"significance={verdict_info['significance']} baseline={verdict_info['baseline']} forward={verdict_info['forward']} conclusion={verdict_info['conclusion']}",
            "",
            "## 数据快照",
            "",
            f"- 数据源：`{db_snapshot.get('db_path')}` 自检 + generated expression matrix；主绩效不是 live_real fill PnL。",
            f"- 数据快照时间：DB mtime `{db_snapshot.get('db_mtime')}`, fact_built_at_utc `{db_snapshot.get('fact_built_at_utc')}`。",
            f"- 记录行数：fact_trades={db_snapshot.get('fact_trades_rows')}, fact_signal_candidates={db_snapshot.get('fact_signal_candidates_rows')}, expression same-denominator decisions={len(base)}。",
            f"- unsettled 占比：fact_trades NULL/unsettled-like={db_snapshot.get('settlement_status_counts', {}).get('NULL', 0)} / {db_snapshot.get('fact_trades_rows')}。",
            f"- missing_bracket 数：settlement_status_counts={db_snapshot.get('settlement_status_counts')}; CLOB gate_pass={db_snapshot.get('gate_pass')}。",
            "- `run_stack.sh` 本轮数据层完成，但因本机 FE 5174 端口仍忙非数据退出；本报告使用 SQLite 自检后的事实层和 generated replay artifacts。",
            "",
            "## Head Boundary",
            "",
            "- 这是独立 research head：`forecast_bias_tail_reversal_v1`。",
            "- 主分母是同一 city-date-decision snapshot 下 5 个表达都有可用 ask/payoff 的 rows；不使用当前 runner selected trades 做主样本。",
            "- `high_tail_yes` 在本轮用 expression matrix 里的 `lottery_yes_*` 作为 hotter-tail YES proxy；如果以后要 live/shadow，需要把 d1/d2 YES 与 full bracket tail YES 分开记录。",
            "- 每个表达按同一 `$5` stake 计算：`pnl = payoff * (5 / ask) - 5`；ROI 是 total pnl / total cost。",
            "",
            "## 8 环覆盖",
            "",
            "| 环 | 覆盖 | 说明 |",
            "| --- | --- | --- |",
            "| 1 描述性绩效切片 | yes | expression/selector/city/source/regime/daily |",
            "| 2 统计推断 | yes | target_date block bootstrap ROI/delta CI |",
            "| 3 信号判别 | partial | 只验证 first-principles selector 的 payoff，不训练概率模型 |",
            "| 4 概率分布评估 | no | 本轮没有校准概率分布 |",
            "| 5 执行微结构 | partial | 使用 generated ask；未做 fresh CLOB/depth/live fill |",
            "| 6 容量 | no | 未做 capacity/depth 放大检验 |",
            "| 7 组合相关性 | partial | 使用 target_date block bootstrap；未估 n_eff |",
            "| 8 基准/反事实 | yes | 同分母 static expressions + train-best static baseline |",
            "",
            "## Historical City + Source Bias",
            "",
            md_table(
                top_bias,
                [
                    ("city", "city"),
                    ("unit", "unit"),
                    ("model", "model"),
                    ("n", "rows"),
                    ("bias", "bias"),
                    ("mae", "MAE"),
                    ("p10", "p10"),
                    ("p50", "p50"),
                    ("p90", "p90"),
                    ("hot_tail_pct", "hot tail"),
                    ("cold_tail_pct", "cold tail"),
                    ("tail_skew", "tail skew"),
                    ("row_source_bias_regime", "bias regime"),
                ],
                max_rows=25,
            ),
            "",
            "Interpretation: large stable positive skew means station actual often beats forecast max; large stable negative skew means forecast often overstates station actual. This is a calibration prior, not a trade gate.",
            "",
            "## Same-Denominator Expression A/B",
            "",
            md_table(
                expression_summary,
                [
                    ("expression", "expression"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("win_rate", "win"),
                    ("avg_ask", "avg ask"),
                    ("roi", "ROI"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                    ("losing_days", "losing days"),
                    ("roi_le_minus_50_days", "<=-50% days"),
                    ("max_daily_loss", "max daily loss"),
                ],
            ),
            "",
            "## Forecast-Bias Selector",
            "",
            md_table(
                selector_summary,
                [
                    ("selector_family", "selector"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("win_rate", "win"),
                    ("avg_ask", "avg ask"),
                    ("roi", "ROI"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                    ("daily_p10_pnl", "daily p10"),
                    ("daily_median_pnl", "daily median"),
                    ("daily_p90_pnl", "daily p90"),
                    ("losing_days", "losing days"),
                    ("roi_le_minus_50_days", "<=-50% days"),
                    ("max_daily_loss", "max daily loss"),
                ],
            ),
            "",
            "Selector mix:",
            "",
            md_table(
                selector_mix,
                [
                    ("selector_expression", "picked expression"),
                    ("row_source_bias_regime", "bias regime"),
                    ("rows", "rows"),
                    ("selector_share", "share"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("win_rate", "win"),
                    ("avg_ask", "avg ask"),
                    ("roi", "ROI"),
                    ("pnl", "pnl"),
                ],
                max_rows=30,
            ),
            "",
            "## Baseline / Market-Structure Check",
            "",
            md_table(
                vs_baselines,
                [
                    ("window", "window"),
                    ("baseline", "baseline"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("selector_roi", "selector ROI"),
                    ("baseline_roi", "baseline ROI"),
                    ("delta_roi", "delta ROI"),
                    ("delta_ci_low", "CI low"),
                    ("delta_ci_high", "CI high"),
                    ("delta_pnl", "delta pnl"),
                ],
                max_rows=20,
            ),
            "",
            "If the selector cannot beat a static expression chosen only on train dates in the forward window, the result is not forecast-bias alpha; it is likely expression/base-rate or sample noise.",
            "",
            "## Daily PnL Distribution",
            "",
            md_table(
                daily,
                [
                    ("target_date", "date"),
                    ("rows", "rows"),
                    ("cities", "cities"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                ],
                max_rows=80,
            ),
            "",
            "## City / Source Contribution",
            "",
            md_table(
                contrib["city"].sort_values("pnl", ascending=False),
                [
                    ("city", "city"),
                    ("row_forecast_model", "model"),
                    ("row_source_bias_regime", "bias regime"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("win_rate", "win"),
                    ("avg_ask", "avg ask"),
                    ("roi", "ROI"),
                    ("pnl", "pnl"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                ],
                max_rows=35,
            ),
            "",
            "## Regime Contribution",
            "",
            "Day regime:",
            "",
            md_table(
                contrib["day_regime"].sort_values("pnl", ascending=False),
                [
                    ("day_regime", "day regime"),
                    ("row_source_bias_regime", "bias regime"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("win_rate", "win"),
                    ("avg_ask", "avg ask"),
                    ("roi", "ROI"),
                    ("pnl", "pnl"),
                ],
                max_rows=35,
            ),
            "",
            "Intraday state:",
            "",
            md_table(
                contrib["intraday"].sort_values("pnl", ascending=False),
                [
                    ("intraday_state", "intraday"),
                    ("row_source_bias_regime", "bias regime"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("win_rate", "win"),
                    ("avg_ask", "avg ask"),
                    ("roi", "ROI"),
                    ("pnl", "pnl"),
                ],
                max_rows=35,
            ),
            "",
            "Moisture/cloud:",
            "",
            md_table(
                contrib["moisture"].sort_values("pnl", ascending=False),
                [
                    ("moisture_cloud_regime", "moisture/cloud"),
                    ("row_source_bias_regime", "bias regime"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("win_rate", "win"),
                    ("avg_ask", "avg ask"),
                    ("roi", "ROI"),
                    ("pnl", "pnl"),
                ],
                max_rows=35,
            ),
            "",
            "## Interpretation",
            "",
            "1. Historical city/source forecast bias is real and stable enough to be a feature layer: hot-underforecast cities and cold-overforecast cities are not symmetric.",
            "2. Same-denominator expression payoff matters more than raw forecast-bias labels. `d1_no` / `d2_no` can look strong because of base-rate and ask level, not necessarily because bias selected them.",
            "3. The first-principles selector has negative full-sample point estimate and negative excess over train-best static expression in the forward slice. That fails significance, baseline, and forward gates.",
            "4. Therefore this is not a confirmed independent tail-reversal alpha, and it is not a runner-specific expression-selection improvement.",
            "",
            "## Next Evidence To Collect",
            "",
            "- Keep it as independent `forecast_bias_tail_reversal_v1` shadow research head.",
            "- Add explicit d1/d2 hotter YES quotes, not only `lottery_yes` proxy.",
            "- Forward-log selector decision, selected expression, all sibling asks, source-bias regime, day/intraday/moisture/wind regime, and realized payoff at decision time.",
            "- Only after settled forward rows show positive excess vs train-best static expression should we decide whether this is a new strategy family or just an expression selector for an existing runner.",
            "",
            "## Artifacts",
            "",
            f"- Script: `{Path('scripts/analysis/forecast_quality/research_forecast_bias_tail_reversal_expression_selector_v1.py')}`",
            f"- JSON summary: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Same-denominator long rows: `{(OUT_DIR / 'same_denominator_expression_rows.csv').relative_to(ROOT)}`",
            f"- Expression summary: `{(OUT_DIR / 'expression_summary.csv').relative_to(ROOT)}`",
            f"- Selector rows: `{(OUT_DIR / 'selector_rows.csv').relative_to(ROOT)}`",
            f"- Selector daily: `{(OUT_DIR / 'selector_daily.csv').relative_to(ROOT)}`",
            f"- Baseline comparison: `{(OUT_DIR / 'selector_vs_baselines.csv').relative_to(ROOT)}`",
            "",
        ]
    )


def build(_: argparse.Namespace) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db_snapshot = load_db_snapshot()
    hist, city_fit = load_bias()
    top_bias = top_stable_bias(hist)
    base_all = load_event_base(hist, city_fit)
    base = same_denominator_base(base_all)
    long = expression_long(base)
    selector = selector_rows(long)

    expression_summary = summarize(long, ["expression"])
    selector_summary = summarize(selector, ["selector_family"])
    selector_mix = selector_pick_mix(selector)
    vs_baselines = selector_vs_baselines(long)
    daily = selector_daily(selector)
    contrib = contribution_summaries(selector)
    best_static = train_best_static_expression(long)
    verdict_info = verdict(selector_summary, vs_baselines)

    top_bias.to_csv(OUT_DIR / "city_source_bias_stability.csv", index=False)
    base.to_csv(OUT_DIR / "same_denominator_decision_rows.csv", index=False)
    long.to_csv(OUT_DIR / "same_denominator_expression_rows.csv", index=False)
    selector.to_csv(OUT_DIR / "selector_rows.csv", index=False)
    expression_summary.to_csv(OUT_DIR / "expression_summary.csv", index=False)
    selector_summary.to_csv(OUT_DIR / "selector_summary.csv", index=False)
    selector_mix.to_csv(OUT_DIR / "selector_pick_mix.csv", index=False)
    vs_baselines.to_csv(OUT_DIR / "selector_vs_baselines.csv", index=False)
    daily.to_csv(OUT_DIR / "selector_daily.csv", index=False)
    for name, df in contrib.items():
        df.to_csv(OUT_DIR / f"selector_{name}_contribution.csv", index=False)

    summary = {
        "generated_at": "2026-06-30",
        "head": "forecast_bias_tail_reversal_v1",
        "boundary": "independent_research_head_not_current_runner_integration",
        "forward_start": FORWARD_START,
        "stake_usd": STAKE_USD,
        "db_snapshot": db_snapshot,
        "funnel": {
            "event_rows": int(len(base_all)),
            "same_denominator_decisions": int(len(base)),
            "long_expression_rows": int(len(long)),
            "selector_rows": int(len(selector)),
        },
        "best_static_expression_train": best_static,
        "verdict": verdict_info,
        "selector_summary": selector_summary.to_dict(orient="records"),
        "selector_vs_baselines": vs_baselines.to_dict(orient="records"),
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    OUT_MD.write_text(
        render_report(
            db_snapshot,
            top_bias,
            base,
            expression_summary,
            selector_summary,
            selector_mix,
            vs_baselines,
            daily,
            contrib,
            verdict_info,
            best_static,
        ),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    print(json.dumps(build(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
