#!/usr/bin/env python3
"""PIT reversal archetype selector for weather temperature expressions.

This script intentionally avoids realized forecast error or final-max-derived
features in selection rules.  It studies whether high-confidence market states
reverse more often in city/source bias and complex weather regimes.
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
EVENT_ROWS = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv"
HIST_SUMMARY = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/city_model_error_summary.csv"
DB_PATH = ROOT / "runtime/weather.db"
GATE_JSON = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/reversal_archetype_selector_v3"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-01-reversal-archetype-selector-v3.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-01-reversal-archetype-selector-v3.json"

STAKE_USD = 5.0
TRAIN_END_EXCLUSIVE = "2026-06-13"
RECENT_START = "2026-06-21"
RNG_SEED = 20260701


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


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 60) -> str:
    if df.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    pct_cols = {"win_rate", "roi", "roi_ci_low", "roi_ci_high", "baseline_roi", "excess_roi", "excess_ci_low", "excess_ci_high"}
    money_cols = {"cost", "pnl", "baseline_pnl", "excess_pnl", "max_daily_loss", "daily_p10_pnl", "daily_p90_pnl", "pnl_per_active_day"}
    int_cols = {"rows", "dates", "cities", "losing_days", "roi_le_minus_50_days"}
    for _, row in df.head(max_rows).iterrows():
        vals: list[str] = []
        for key, _label in cols:
            val = row.get(key, "")
            if key in pct_cols or key.endswith("_roi") or "ci_" in key:
                vals.append(pct(val))
            elif key in money_cols or key.endswith("_pnl"):
                vals.append(money(val))
            elif key in int_cols and pd.notna(val):
                vals.append(str(int(val)))
            elif isinstance(val, float):
                vals.append(f"{val:.2f}" if math.isfinite(val) else "")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


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


def load_db_snapshot() -> dict[str, Any]:
    out: dict[str, Any] = {
        "db_path": str(DB_PATH.relative_to(ROOT)),
        "db_mtime": pd.Timestamp(DB_PATH.stat().st_mtime, unit="s", tz="UTC").isoformat() if DB_PATH.exists() else None,
        "gate_pass": None,
    }
    if GATE_JSON.exists():
        gate = json.loads(GATE_JSON.read_text())
        out["gate_pass"] = bool(gate.get("gate_pass"))
        out["live_real_fill_ids"] = gate.get("fact_trades_live_real", {}).get("fill_ids")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    out["fact_trades_rows"] = conn.execute("SELECT COUNT(*) FROM fact_trades").fetchone()[0]
    out["fact_signal_candidates_rows"] = conn.execute("SELECT COUNT(*) FROM fact_signal_candidates").fetchone()[0]
    out["fact_built_at_utc"] = conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0]
    settlement = conn.execute("SELECT COALESCE(settlement_status, 'NULL'), COUNT(*) FROM fact_trades GROUP BY settlement_status").fetchall()
    out["settlement_status_counts"] = {str(k): int(v) for k, v in settlement}
    conn.close()
    return out


def load_rows() -> pd.DataFrame:
    needed = [
        "city",
        "target_date",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "unit",
        "forecast_source",
        "forecast_clock_source",
        "forecast_gap_to_running_native",
        "forecast_peak_delta_hours_local",
        "forecast_peak_hour_spread",
        "current_bracket",
        "current_bracket_held",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "current_yes_ask",
        "current_yes_payoff",
        "current_bracket_no_ask",
        "current_bracket_no_payoff",
        "d1_no_ask",
        "d1_no_bid",
        "d1_no_payoff",
        "d1_hit",
        "d2_no_ask",
        "d2_no_bid",
        "d2_no_payoff",
        "d2_hit",
        "lottery_yes_ask",
        "lottery_yes_payoff",
    ]
    df = pd.read_csv(EVENT_ROWS, usecols=needed, low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    df["row_forecast_model"] = df.apply(forecast_model_from_row, axis=1)

    hist = pd.read_csv(HIST_SUMMARY)
    hist["row_source_bias_regime"] = hist.apply(classify_bias, axis=1)
    hist["tail_skew"] = hist["pct_actual_ge_forecast_plus_1"] - hist["pct_forecast_ge_actual_plus_1"]
    lookup = hist[[
        "city",
        "model",
        "bias",
        "mae",
        "p10",
        "p50",
        "p90",
        "pct_actual_ge_forecast_plus_1",
        "pct_forecast_ge_actual_plus_1",
        "tail_skew",
        "row_source_bias_regime",
    ]]
    df = df.merge(lookup, left_on=["city", "row_forecast_model"], right_on=["city", "model"], how="left")
    df["row_source_bias_regime"] = df["row_source_bias_regime"].fillna("unclassified")

    numeric_cols = [
        "forecast_gap_to_running_native",
        "forecast_peak_delta_hours_local",
        "forecast_peak_hour_spread",
        "current_yes_ask",
        "current_yes_payoff",
        "current_bracket_no_ask",
        "current_bracket_no_payoff",
        "d1_no_ask",
        "d1_no_bid",
        "d1_no_payoff",
        "d1_hit",
        "d2_no_ask",
        "d2_no_bid",
        "d2_no_payoff",
        "d2_hit",
        "lottery_yes_ask",
        "lottery_yes_payoff",
        "tail_skew",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["d1_yes_proxy_ask"] = 1.0 - df["d1_no_bid"]
    df["d1_yes_proxy_payoff"] = pd.to_numeric(df["d1_hit"], errors="coerce")
    df["d2_yes_proxy_ask"] = 1.0 - df["d2_no_bid"]
    df["d2_yes_proxy_payoff"] = pd.to_numeric(df["d2_hit"], errors="coerce")
    return df


def bootstrap_roi_ci(df: pd.DataFrame, n_boot: int = 3000) -> tuple[float, float]:
    daily = df.groupby("target_date", as_index=False).agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    if len(daily) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(RNG_SEED)
    costs = daily["cost"].to_numpy(float)
    pnls = daily["pnl"].to_numpy(float)
    vals: list[float] = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(daily), len(daily))
        cost = costs[idx].sum()
        if cost > 0:
            vals.append(float(pnls[idx].sum() / cost))
    return tuple(np.quantile(vals, [0.025, 0.975]).tolist())


def bootstrap_excess_ci(df: pd.DataFrame, n_boot: int = 3000) -> tuple[float, float]:
    daily = df.groupby("target_date", as_index=False).agg(
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
        baseline_cost=("baseline_cost", "sum"),
        baseline_pnl=("baseline_pnl", "sum"),
    )
    if len(daily) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(RNG_SEED)
    vals: list[float] = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(daily), len(daily))
        g = daily.iloc[idx]
        vals.append(float(g["pnl"].sum() / g["cost"].sum() - g["baseline_pnl"].sum() / g["baseline_cost"].sum()))
    return tuple(np.quantile(vals, [0.025, 0.975]).tolist())


def materialize_trade(df: pd.DataFrame, ask_col: str, payoff_col: str, baseline_ask_col: str | None, baseline_payoff_col: str | None) -> pd.DataFrame:
    g = df.copy()
    g["ask"] = pd.to_numeric(g[ask_col], errors="coerce")
    g["payoff"] = pd.to_numeric(g[payoff_col], errors="coerce")
    valid = g["ask"].gt(0) & g["ask"].lt(1) & g["payoff"].isin([0.0, 1.0])
    if baseline_ask_col and baseline_payoff_col:
        g["baseline_ask"] = pd.to_numeric(g[baseline_ask_col], errors="coerce")
        g["baseline_payoff"] = pd.to_numeric(g[baseline_payoff_col], errors="coerce")
        valid &= g["baseline_ask"].gt(0) & g["baseline_ask"].lt(1) & g["baseline_payoff"].isin([0.0, 1.0])
    else:
        g["baseline_ask"] = np.nan
        g["baseline_payoff"] = np.nan
    g = g[valid].copy()
    g["cost"] = STAKE_USD
    g["pnl"] = g["payoff"] * (STAKE_USD / g["ask"]) - STAKE_USD
    g["baseline_cost"] = STAKE_USD
    g["baseline_pnl"] = np.where(
        g["baseline_ask"].notna(),
        g["baseline_payoff"] * (STAKE_USD / g["baseline_ask"]) - STAKE_USD,
        0.0,
    )
    return g


def first_city_date(g: pd.DataFrame) -> pd.DataFrame:
    return (
        g.sort_values(["target_date", "city", "decision_snapshot_ts_utc", "current_bracket"])
        .drop_duplicates(["city", "target_date"], keep="first")
        .copy()
    )


def summarize(g: pd.DataFrame) -> dict[str, Any]:
    if g.empty:
        return {
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "win_rate": float("nan"),
            "roi": float("nan"),
            "baseline_roi": float("nan"),
            "excess_roi": float("nan"),
        }
    cost = float(g["cost"].sum())
    pnl = float(g["pnl"].sum())
    base_cost = float(g["baseline_cost"].sum())
    base_pnl = float(g["baseline_pnl"].sum())
    daily = g.groupby("target_date", as_index=False).agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    ci = bootstrap_roi_ci(g)
    ex_ci = bootstrap_excess_ci(g) if g["baseline_ask"].notna().any() else (float("nan"), float("nan"))
    return {
        "rows": int(len(g)),
        "dates": int(g["target_date"].nunique()),
        "cities": int(g["city"].nunique()),
        "win_rate": float(g["payoff"].mean()),
        "avg_ask": float(g["ask"].mean()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else float("nan"),
        "roi_ci_low": float(ci[0]),
        "roi_ci_high": float(ci[1]),
        "baseline_pnl": base_pnl,
        "baseline_roi": base_pnl / base_cost if base_cost and g["baseline_ask"].notna().any() else float("nan"),
        "excess_pnl": pnl - base_pnl if g["baseline_ask"].notna().any() else float("nan"),
        "excess_roi": (pnl / cost - base_pnl / base_cost) if cost and base_cost and g["baseline_ask"].notna().any() else float("nan"),
        "excess_ci_low": float(ex_ci[0]),
        "excess_ci_high": float(ex_ci[1]),
        "losing_days": int((daily["pnl"] < 0).sum()),
        "roi_le_minus_50_days": int((daily["roi"] <= -0.50).sum()),
        "max_daily_loss": float(daily["pnl"].min()),
        "daily_p10_pnl": float(daily["pnl"].quantile(0.10)),
        "daily_p90_pnl": float(daily["pnl"].quantile(0.90)),
        "pnl_per_active_day": float(pnl / g["target_date"].nunique()) if g["target_date"].nunique() else float("nan"),
    }


def period_frame(g: pd.DataFrame, period: str) -> pd.DataFrame:
    if period == "train":
        return g[g["target_date"] < TRAIN_END_EXCLUSIVE].copy()
    if period == "holdout":
        return g[g["target_date"] >= TRAIN_END_EXCLUSIVE].copy()
    if period == "recent":
        return g[g["target_date"] >= RECENT_START].copy()
    return g.copy()


def archetype_specs(df: pd.DataFrame) -> list[dict[str, Any]]:
    high_no = df["current_bracket_no_ask"].ge(0.70) & df["current_yes_ask"].le(0.50)
    high_yes = df["current_yes_ask"].ge(0.70) & df["current_bracket_no_ask"].le(0.50)
    cold_or_tight = df["row_source_bias_regime"].isin(["cold_overforecast_clean", "cold_overforecast_noisy", "balanced_tight"])
    hot_tail = df["row_source_bias_regime"].isin(["hot_underforecast_clean", "hot_underforecast_noisy"])
    complex_weather = (
        df["row_source_bias_regime"].isin(["two_sided_noisy", "mild_or_mixed"])
        | df["moisture_cloud_regime"].isin(["humid_overcast_suppression", "humid_convective_risk", "cloud_suppression"])
        | df["wind_regime"].isin(["windy_mixing_noise", "moderate_wind"])
    )
    capped_or_low_runway = (
        df["day_regime"].isin(["day_forecast_capped", "day_forecast_busted"])
        | pd.to_numeric(df["forecast_gap_to_running_native"], errors="coerce").le(1.0)
    )
    open_tail = df["day_regime"].isin(["day_open_runway", "day_marginal_runway"]) & df["intraday_state"].isin(
        ["active_warming", "fresh_high", "false_fade_risk", "reheating_after_dip"]
    )

    return [
        {
            "archetype": "current_no_overconfidence_all",
            "expression": "buy_current_yes",
            "executable": True,
            "mask": high_no,
            "ask_col": "current_yes_ask",
            "payoff_col": "current_yes_payoff",
            "baseline_ask_col": "current_bracket_no_ask",
            "baseline_payoff_col": "current_bracket_no_payoff",
            "mechanism": "Market prices current bracket NO high while current YES is still cheap.",
        },
        {
            "archetype": "cold_or_tight_source_current_hold",
            "expression": "buy_current_yes",
            "executable": True,
            "mask": high_no & cold_or_tight,
            "ask_col": "current_yes_ask",
            "payoff_col": "current_yes_payoff",
            "baseline_ask_col": "current_bracket_no_ask",
            "baseline_payoff_col": "current_bracket_no_payoff",
            "mechanism": "Historical city/source forecast is overhot or tight, so high NO may overstate tail escape.",
        },
        {
            "archetype": "complex_weather_current_hold",
            "expression": "buy_current_yes",
            "executable": True,
            "mask": high_no & complex_weather,
            "ask_col": "current_yes_ask",
            "payoff_col": "current_yes_payoff",
            "baseline_ask_col": "current_bracket_no_ask",
            "baseline_payoff_col": "current_bracket_no_payoff",
            "mechanism": "Humid/cloud/wind/noisy regimes make deterministic forecast-following less reliable.",
        },
        {
            "archetype": "capped_or_low_runway_current_hold",
            "expression": "buy_current_yes",
            "executable": True,
            "mask": high_no & capped_or_low_runway,
            "ask_col": "current_yes_ask",
            "payoff_col": "current_yes_payoff",
            "baseline_ask_col": "current_bracket_no_ask",
            "baseline_payoff_col": "current_bracket_no_payoff",
            "mechanism": "PIT forecast runway to running high is low despite high current NO.",
        },
        {
            "archetype": "current_yes_overconfidence_tail_escape",
            "expression": "buy_current_bracket_no",
            "executable": True,
            "mask": high_yes & (hot_tail | open_tail),
            "ask_col": "current_bracket_no_ask",
            "payoff_col": "current_bracket_no_payoff",
            "baseline_ask_col": "current_yes_ask",
            "baseline_payoff_col": "current_yes_payoff",
            "mechanism": "Market prices current YES high, but hot-underforecast or open-runway state supports tail escape.",
        },
        {
            "archetype": "hot_underforecast_higher_yes_tail",
            "expression": "buy_higher_tail_yes",
            "executable": True,
            "mask": hot_tail & open_tail & df["lottery_yes_ask"].between(0.05, 0.50, inclusive="both"),
            "ask_col": "lottery_yes_ask",
            "payoff_col": "lottery_yes_payoff",
            "baseline_ask_col": None,
            "baseline_payoff_col": None,
            "mechanism": "Historical source underforecasts hot tail and intraday state still has runway; buy hotter bracket YES.",
        },
        {
            "archetype": "d1_no_overconfidence_fade_proxy",
            "expression": "buy_d1_yes_proxy",
            "executable": False,
            "mask": df["d1_no_ask"].ge(0.70)
            & df["d1_yes_proxy_ask"].between(0.05, 0.50, inclusive="both")
            & (hot_tail | open_tail | complex_weather),
            "ask_col": "d1_yes_proxy_ask",
            "payoff_col": "d1_yes_proxy_payoff",
            "baseline_ask_col": "d1_no_ask",
            "baseline_payoff_col": "d1_no_payoff",
            "mechanism": "Diagnostic only: d1 NO is high, but tail/complex state may make d1 YES underpriced. YES ask is proxied from NO bid.",
        },
        {
            "archetype": "d2_no_overconfidence_fade_proxy",
            "expression": "buy_d2_yes_proxy",
            "executable": False,
            "mask": df["d2_no_ask"].ge(0.70)
            & df["d2_yes_proxy_ask"].between(0.05, 0.50, inclusive="both")
            & (hot_tail | open_tail | complex_weather),
            "ask_col": "d2_yes_proxy_ask",
            "payoff_col": "d2_yes_proxy_payoff",
            "baseline_ask_col": "d2_no_ask",
            "baseline_payoff_col": "d2_no_payoff",
            "mechanism": "Diagnostic only: d2 NO is high, but tail/complex state may make d2 YES underpriced. YES ask is proxied from NO bid.",
        },
    ]


def evaluate(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    records: list[dict[str, Any]] = []
    daily_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    city_records: list[dict[str, Any]] = []
    for spec in archetype_specs(rows):
        raw = materialize_trade(
            rows[spec["mask"]].copy(),
            spec["ask_col"],
            spec["payoff_col"],
            spec["baseline_ask_col"],
            spec["baseline_payoff_col"],
        )
        picked = first_city_date(raw)
        picked["archetype"] = spec["archetype"]
        picked["expression"] = spec["expression"]
        picked["executable"] = spec["executable"]
        picked["mechanism"] = spec["mechanism"]
        trade_frames.append(picked)

        for period in ["full", "train", "holdout", "recent"]:
            g = period_frame(picked, period)
            rec = {
                "archetype": spec["archetype"],
                "expression": spec["expression"],
                "executable": bool(spec["executable"]),
                "period": period,
                "mechanism": spec["mechanism"],
                **summarize(g),
            }
            records.append(rec)

        daily = picked.groupby("target_date", as_index=False).agg(rows=("pnl", "size"), cost=("cost", "sum"), pnl=("pnl", "sum"))
        daily["roi"] = daily["pnl"] / daily["cost"]
        daily["archetype"] = spec["archetype"]
        daily_frames.append(daily)

        for city, g in picked.groupby("city"):
            if len(g) >= 2:
                city_records.append({"archetype": spec["archetype"], "city": city, **summarize(g)})

    summary = pd.DataFrame(records)
    daily_out = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    city = pd.DataFrame(city_records).sort_values(["archetype", "pnl"], ascending=[True, False]) if city_records else pd.DataFrame()
    return summary, daily_out, trades, city


def render_report(db: dict[str, Any], rows: pd.DataFrame, summary: pd.DataFrame, daily: pd.DataFrame, trades: pd.DataFrame, city: pd.DataFrame) -> str:
    full = summary[summary["period"].eq("full")].copy()
    holdout = summary[summary["period"].eq("holdout")].copy()
    executable_full = full[full["executable"].eq(True)].copy()
    best_exec = executable_full.sort_values(["roi", "rows"], ascending=False).head(1)
    if not best_exec.empty:
        b = best_exec.iloc[0]
        headline = (
            f"最佳 executable archetype `{b['archetype']}`：{int(b['rows'])} rows/{int(b['dates'])} dates，"
            f"ROI {pct(b['roi'])} CI {pct(b['roi_ci_low'])}..{pct(b['roi_ci_high'])}，"
            f"baseline ROI {pct(b['baseline_roi'])}，excess {pct(b['excess_roi'])}。"
        )
    else:
        headline = "没有 executable archetype 形成有效样本。"
    passing = executable_full[
        (executable_full["rows"] >= 20)
        & (executable_full["dates"] >= 10)
        & (executable_full["roi_ci_low"] > 0)
        & ((executable_full["excess_ci_low"] > 0) | executable_full["excess_ci_low"].isna())
    ]
    conclusion = "shadow_candidate" if not passing.empty else "inconclusive"
    verdict = (
        "`shadow_candidate` only for telemetry if an archetype has full-window positive CI and baseline excess; no live action because holdout remains thin."
        if conclusion == "shadow_candidate"
        else "`inconclusive`: broadened archetype search finds mechanisms worth logging, but no executable archetype clears significance+baseline+forward."
    )
    return "\n".join(
        [
            "# Reversal Archetype Selector v3",
            "",
            "Generated: 2026-07-01",
            "",
            "## Verdict",
            "",
            headline,
            "",
            verdict,
            "",
            "significance="
            + ("PASS" if not passing.empty else "FAIL")
            + " baseline="
            + ("PASS" if not passing.empty else "FAIL")
            + " forward=FAIL conclusion="
            + conclusion,
            "",
            "## 数据快照",
            "",
            f"- DB: `{db.get('db_path')}`, fact_built_at_utc `{db.get('fact_built_at_utc')}`, CLOB gate_pass={db.get('gate_pass')}.",
            f"- Expression matrix: {len(rows)} rows, {rows['target_date'].nunique()} dates, {rows['target_date'].min()}..{rows['target_date'].max()}, {rows['city'].nunique()} cities.",
            "- Selection features are PIT/historical only: price conflict, forecast gap to running high, day/intraday/moisture/wind regime, city/source historical forecast bias.",
            "- Excluded from selection: `forecast_error_native`, final max, final winning bracket, payoff labels.",
            "",
            "## Archetype Summary",
            "",
            md_table(
                summary.sort_values(["period", "executable", "roi"], ascending=[True, False, False]),
                [
                    ("period", "period"),
                    ("archetype", "archetype"),
                    ("expression", "expression"),
                    ("executable", "exec"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("avg_ask", "avg ask"),
                    ("win_rate", "win"),
                    ("roi", "ROI"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                    ("baseline_roi", "baseline"),
                    ("excess_roi", "excess"),
                    ("losing_days", "loss days"),
                    ("roi_le_minus_50_days", "<=-50% days"),
                    ("max_daily_loss", "max loss"),
                    ("pnl_per_active_day", "pnl/day"),
                ],
                max_rows=80,
            ),
            "",
            "## Holdout Only",
            "",
            md_table(
                holdout.sort_values(["executable", "roi"], ascending=[False, False]),
                [
                    ("archetype", "archetype"),
                    ("expression", "expression"),
                    ("executable", "exec"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("win_rate", "win"),
                    ("roi", "ROI"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                    ("baseline_roi", "baseline"),
                    ("excess_roi", "excess"),
                    ("losing_days", "loss days"),
                    ("max_daily_loss", "max loss"),
                ],
                max_rows=40,
            ),
            "",
            "## City Contribution",
            "",
            md_table(
                city,
                [
                    ("archetype", "archetype"),
                    ("city", "city"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("win_rate", "win"),
                    ("roi", "ROI"),
                    ("pnl", "pnl"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                ],
                max_rows=80,
            ),
            "",
            "## Interpretation",
            "",
            "- Losing days are expected for all binary-token reversal sleeves; they are reported as risk, not used as a hard rejection.",
            "- The broad current-NO-overconfidence shape remains the main executable expression, but PIT-only variants are not yet stable enough.",
            "- Source/city bias and complex weather are useful tags for forward logging. They do not yet identify a confirmed live selector.",
            "- d1/d2 YES rows are proxy diagnostics because YES ask is inferred from NO bid; they must not be promoted without real YES ask/depth.",
            "",
            "## Artifacts",
            "",
            f"- Script: `scripts/analysis/forecast_quality/research_reversal_archetype_selector_v3.py`",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Summary CSV: `{(OUT_DIR / 'archetype_summary.csv').relative_to(ROOT)}`",
            f"- Trades CSV: `{(OUT_DIR / 'archetype_trades.csv').relative_to(ROOT)}`",
            "",
        ]
    )


def build() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db = load_db_snapshot()
    rows = load_rows()
    summary, daily, trades, city = evaluate(rows)
    summary.to_csv(OUT_DIR / "archetype_summary.csv", index=False)
    daily.to_csv(OUT_DIR / "archetype_daily.csv", index=False)
    trades.to_csv(OUT_DIR / "archetype_trades.csv", index=False)
    city.to_csv(OUT_DIR / "archetype_city_contribution.csv", index=False)

    full_exec = summary[(summary["period"].eq("full")) & (summary["executable"].eq(True))].copy()
    best = full_exec.sort_values(["roi", "rows"], ascending=False).head(1).to_dict("records")
    passing = full_exec[
        (full_exec["rows"] >= 20)
        & (full_exec["dates"] >= 10)
        & (full_exec["roi_ci_low"] > 0)
        & ((full_exec["excess_ci_low"] > 0) | full_exec["excess_ci_low"].isna())
    ]
    payload = {
        "generated_at": "2026-07-01",
        "head": "reversal_archetype_selector_v3",
        "db_snapshot": db,
        "expression_matrix": {
            "rows": int(len(rows)),
            "dates": int(rows["target_date"].nunique()),
            "min_target_date": str(rows["target_date"].min()),
            "max_target_date": str(rows["target_date"].max()),
            "cities": int(rows["city"].nunique()),
        },
        "best_executable_full": best[0] if best else {},
        "conclusion": "shadow_candidate" if not passing.empty else "inconclusive",
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(render_report(db, rows, summary, daily, trades, city), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


if __name__ == "__main__":
    build()
