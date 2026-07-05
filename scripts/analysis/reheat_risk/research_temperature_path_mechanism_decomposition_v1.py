#!/usr/bin/env python3
"""Temperature path mechanism decomposition v1.

Research-only foundation layer for trend3h/path labels.  This script is not a
strategy optimizer.  It decomposes broad, reusable temperature-path states and
then checks how those states map to physical outcomes and existing expressions.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "runtime/weather.db"
GATE_JSON = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
ATLAS_ROWS = (
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
    / "intraday_weather_regime_state_rows.csv"
)
HEADB_ROWS = (
    ROOT
    / "docs/analysis/2026-07/generated/metar_reversal_tmax_regime_overlay_v1"
    / "headb_enriched_trades.csv"
)
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/temperature_path_mechanism_decomposition_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-05-temperature-path-mechanism-decomposition-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-05-temperature-path-mechanism-decomposition-v1.json"

RNG_SEED = 20260705
N_BOOT = 5000
RECENT_START = "2026-06-21"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def pct(value: object, signed: bool = False) -> str:
    if value is None or pd.isna(value):
        return ""
    sign = "+" if signed else ""
    return f"{float(value) * 100:{sign}.1f}%"


def num(value: object, digits: int = 3, signed: bool = False) -> str:
    if value is None or pd.isna(value):
        return ""
    sign = "+" if signed else ""
    return f"{float(value):{sign}.{digits}f}"


def money(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):+.2f}"


def to_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0).astype(float).ne(0.0)
    text = series.astype(str).str.lower().str.strip()
    return text.isin({"true", "1", "yes", "y"})


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def db_snapshot() -> dict[str, object]:
    with connect_ro() as conn:
        fsc = dict(
            conn.execute(
                """
                SELECT COUNT(*) AS rows, MIN(event_date) AS min_date,
                       MAX(event_date) AS max_date, MAX(fact_built_at_utc) AS built_at
                FROM fact_signal_candidates
                """
            ).fetchone()
        )
        trades = dict(
            conn.execute(
                """
                SELECT COUNT(*) AS rows, MIN(target_date) AS min_date,
                       MAX(target_date) AS max_date, MAX(fact_built_at_utc) AS built_at
                FROM fact_trades
                """
            ).fetchone()
        )
        settlements = [
            dict(row)
            for row in conn.execute(
                """
                SELECT target_date, COUNT(*) AS rows,
                       SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_rows
                FROM settlement_outcomes
                GROUP BY target_date
                ORDER BY target_date DESC
                LIMIT 8
                """
            ).fetchall()
        ]
    gate: dict[str, object] = {"gate_pass": None, "path": display_path(GATE_JSON)}
    if GATE_JSON.exists():
        raw = json.loads(GATE_JSON.read_text(encoding="utf-8"))
        gate.update(
            {
                "gate_pass": raw.get("gate_pass"),
                "fail_reasons": raw.get("fail_reasons"),
                "fact_trades_live_real": raw.get("fact_trades_live_real"),
                "db_vs_primary_cache": raw.get("db_vs_primary_cache"),
            }
        )
    return {
        "generated_at_utc": now_utc(),
        "fact_signal_candidates": fsc,
        "fact_trades": trades,
        "recent_settlement_outcomes": settlements,
        "clob_gate": gate,
    }


def trend_bucket(series: pd.Series) -> pd.Series:
    trend = pd.to_numeric(series, errors="coerce")
    return pd.cut(
        trend,
        bins=[-np.inf, -0.5, 0.5, 2.0, np.inf],
        labels=["cooling_lt_neg0_5", "flat_abs_lt0_5", "warming_0_5_to_2", "strong_warming_ge2"],
        right=False,
    ).astype("object").fillna("unknown")


@dataclass(frozen=True)
class Mechanism:
    name: str
    definition: str
    mechanism_read: str
    recommended_use: str
    broad_min_rows: int
    broad_min_dates: int
    headb_min_rows: int
    mask_fn: Callable[[pd.DataFrame], pd.Series]


def mechanism_defs() -> list[Mechanism]:
    return [
        Mechanism(
            "all_labeled_states",
            "Rows with settlement/current/d1/d2 labels present.",
            "Baseline denominator; not a path state.",
            "baseline_only",
            0,
            0,
            0,
            lambda d: pd.Series(True, index=d.index),
        ),
        Mechanism(
            "trend3h_cooling_lt_neg0_5",
            "temp_trend_3h_f < -0.5F",
            "Sustained cooling/fade path; current bracket survival should rise and upside pass-through should fall.",
            "shared_context_feature",
            500,
            20,
            25,
            lambda d: pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").lt(-0.5),
        ),
        Mechanism(
            "trend3h_flat_abs_lt0_5",
            "-0.5F <= temp_trend_3h_f < +0.5F",
            "Three-hour path is stalled/noisy; useful as a separate state, not as 'positive'.",
            "shared_context_feature",
            300,
            15,
            20,
            lambda d: pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").ge(-0.5)
            & pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").lt(0.5),
        ),
        Mechanism(
            "trend3h_warming_ge0_5",
            "temp_trend_3h_f >= +0.5F",
            "Clean sustained warming state; better foundation than a one-hour uptick.",
            "shared_context_feature",
            500,
            20,
            25,
            lambda d: pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").ge(0.5),
        ),
        Mechanism(
            "trend3h_strong_warming_ge2",
            "temp_trend_3h_f >= +2.0F",
            "Strong accumulated warming; higher upside-break risk but often already visible to price.",
            "shared_context_feature",
            500,
            20,
            25,
            lambda d: pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").ge(2.0),
        ),
        Mechanism(
            "legacy_trend3h_positive_gt0",
            "temp_trend_3h_f > 0F",
            "Compatibility alias for earlier HeadB overlay; broad but includes tiny positive noise.",
            "compatibility_alias_prefer_bucket_ge0_5",
            500,
            20,
            25,
            lambda d: pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").gt(0.0),
        ),
        Mechanism(
            "sustained_warming_1h3h",
            "temp_trend_1h_f >= +0.5F AND temp_trend_3h_f >= +0.5F",
            "Recent observation is still rising and the three-hour path confirms it; clean HeadB reheat confirmation.",
            "shared_context_feature_headb_telemetry",
            300,
            15,
            20,
            lambda d: pd.to_numeric(d["temp_trend_1h_f"], errors="coerce").ge(0.5)
            & pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").ge(0.5),
        ),
        Mechanism(
            "one_hour_warm_without_3h",
            "temp_trend_1h_f >= +0.5F AND temp_trend_3h_f < +0.5F",
            "One-hour bounce without sustained path confirmation; separates noisy reheat from real runway.",
            "diagnostic_context_feature",
            300,
            15,
            15,
            lambda d: pd.to_numeric(d["temp_trend_1h_f"], errors="coerce").ge(0.5)
            & pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").lt(0.5),
        ),
        Mechanism(
            "runway_sustained_warming",
            "temp_trend_3h_f >= +0.5F AND day_regime in {day_open_runway, day_marginal_runway}",
            "Observed warming plus forecast space above running max; good shared d1/upside context.",
            "shared_context_feature",
            300,
            15,
            20,
            lambda d: pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").ge(0.5)
            & d["day_regime"].isin(["day_open_runway", "day_marginal_runway"]),
        ),
        Mechanism(
            "solar_runway_sustained_warming",
            "runway_sustained_warming AND solar_window in {late_morning, solar_peak_window}",
            "Thermal runway is still physically plausible during the solar heating window.",
            "shared_context_feature_shadow_only_if_used_for_selection",
            200,
            12,
            15,
            lambda d: pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").ge(0.5)
            & d["day_regime"].isin(["day_open_runway", "day_marginal_runway"])
            & d["solar_window"].isin(["late_morning", "solar_peak_window"]),
        ),
        Mechanism(
            "late_reheat_after_dip",
            "temp_trend_3h_f >= +0.5F AND intraday_state in {false_fade_risk, reheating_after_dip}",
            "Market/fade conflict setup: a pullback or fade state is being contradicted by renewed warming.",
            "headb_specific_shadow_telemetry",
            100,
            10,
            10,
            lambda d: pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").ge(0.5)
            & d["intraday_state"].isin(["false_fade_risk", "reheating_after_dip"]),
        ),
        Mechanism(
            "humid_or_cloud_warming",
            "temp_trend_3h_f >= +0.5F AND moisture_cloud_regime in humid/cloud suppression families",
            "Warming through humid/cloud context; mechanism is real but less clean because convection/cloud breaks can flip it.",
            "diagnostic_context_feature",
            200,
            12,
            15,
            lambda d: pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").ge(0.5)
            & d["moisture_cloud_regime"].isin(
                ["humid_convective_risk", "humid_overcast_suppression", "cloud_suppression"]
            ),
        ),
        Mechanism(
            "mature_cooling_or_fade",
            "temp_trend_3h_f < -0.5F AND intraday_state in {mature_fade, flat_or_cooling}",
            "Cleaner fade/capped-day context; useful sibling state for current YES survive and NO carry work.",
            "shared_context_feature",
            200,
            12,
            10,
            lambda d: pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").lt(-0.5)
            & d["intraday_state"].isin(["mature_fade", "flat_or_cooling"]),
        ),
        Mechanism(
            "plateau_flat_path",
            "abs(temp_trend_3h_f) < 0.5F AND intraday_state in {fresh_high, plateau_near_high, pullback_uncertain}",
            "Near-high plateau rather than active heating; often expression-specific, not a universal direction signal.",
            "diagnostic_context_feature",
            100,
            10,
            10,
            lambda d: pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").ge(-0.5)
            & pd.to_numeric(d["temp_trend_3h_f"], errors="coerce").lt(0.5)
            & d["intraday_state"].isin(["fresh_high", "plateau_near_high", "pullback_uncertain"]),
        ),
    ]


def add_path_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in [
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "forecast_peak_delta_hours_local",
        "forecast_gap_to_running_native",
        "remaining_heat_native",
        "minutes_since_running_max",
        "current_yes_ask",
        "current_bracket_no_ask",
        "d1_no_ask",
        "d2_no_ask",
    ]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in ["day_regime", "intraday_state", "moisture_cloud_regime", "wind_regime", "solar_window", "city_family"]:
        if col in out.columns:
            out[col] = out[col].astype("object").fillna("unknown")
    out["trend_1h_bucket"] = trend_bucket(out["temp_trend_1h_f"])
    out["trend_3h_bucket"] = trend_bucket(out["temp_trend_3h_f"])
    out["trend3h_positive_any"] = out["temp_trend_3h_f"].gt(0.0)
    out["trend3h_warming_clean"] = out["temp_trend_3h_f"].ge(0.5)
    out["trend3h_flat_clean"] = out["temp_trend_3h_f"].ge(-0.5) & out["temp_trend_3h_f"].lt(0.5)
    out["exclude_trend3h_flat"] = out["temp_trend_3h_f"].notna() & ~out["trend3h_flat_clean"]
    return out


def load_atlas() -> tuple[pd.DataFrame, pd.DataFrame]:
    cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "current_bracket_held",
        "d1_hit",
        "d2_hit",
        "skip_over_d1",
        "future_break_any",
        "current_yes_ask",
        "current_yes_payoff",
        "current_yes_roi",
        "current_bracket_no_ask",
        "current_bracket_no_payoff",
        "current_bracket_no_roi",
        "d1_no_ask",
        "d1_no_payoff",
        "d1_no_roi",
        "d2_no_ask",
        "d2_no_payoff",
        "d2_no_roi",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
        "forecast_peak_delta_hours_local",
        "forecast_gap_to_running_native",
        "remaining_heat_native",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "solar_window",
        "city_family",
        "forecast_source",
    ]
    header = pd.read_csv(ATLAS_ROWS, nrows=0).columns
    df = pd.read_csv(ATLAS_ROWS, usecols=[c for c in cols if c in header], low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    df["decision_hour_local"] = pd.to_numeric(df["decision_hour_local"], errors="coerce").astype("Int64")
    for col in ["current_bracket_held", "d1_hit", "d2_hit", "skip_over_d1"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["future_break_any"] = to_bool(df["future_break_any"])
    df = add_path_features(df)
    labeled = df[df["current_bracket_held"].notna() & df["d1_hit"].notna() & df["d2_hit"].notna()].copy()
    return df, labeled


def block_bootstrap_mean_ci(rows: pd.DataFrame, value_col: str, weight_col: str | None = None) -> tuple[float | None, float | None]:
    if rows.empty or rows["target_date"].nunique() < 3:
        return None, None
    if weight_col is None:
        daily = rows.groupby("target_date", as_index=False).agg(value_sum=(value_col, "sum"), weight_sum=(value_col, "count"))
        values = daily["value_sum"].to_numpy(dtype=float)
        weights = daily["weight_sum"].to_numpy(dtype=float)
    else:
        tmp = rows[[value_col, weight_col, "target_date"]].dropna().copy()
        if tmp.empty:
            return None, None
        daily = tmp.groupby("target_date", as_index=False).agg(value_sum=(value_col, "sum"), weight_sum=(weight_col, "sum"))
        values = daily["value_sum"].to_numpy(dtype=float)
        weights = daily["weight_sum"].to_numpy(dtype=float)
    if len(values) < 3:
        return None, None
    rng = np.random.default_rng(RNG_SEED)
    boot: list[float] = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(values), len(values))
        denom = weights[idx].sum()
        if denom > 0:
            boot.append(float(values[idx].sum() / denom))
    if not boot:
        return None, None
    return float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))


def roi_ci(rows: pd.DataFrame, roi_col: str) -> tuple[float | None, float | None]:
    if rows.empty or rows["target_date"].nunique() < 3:
        return None, None
    daily = rows.groupby("target_date", as_index=False).agg(pnl=(roi_col, "sum"), rows=(roi_col, "count"))
    return block_bootstrap_mean_ci(daily.rename(columns={"pnl": "value", "rows": "weight"}), "value", "weight")


def support_label(rows: int, dates: int, min_rows: int, min_dates: int) -> str:
    if rows >= min_rows and dates >= min_dates:
        return "support_ok"
    if rows >= max(50, min_rows // 2) and dates >= max(5, min_dates // 2):
        return "usable_but_watch_sample"
    return "thin_do_not_select"


def headb_min_dates(mech: Mechanism) -> int:
    if mech.headb_min_rows <= 0:
        return 0
    return min(10, max(5, mech.broad_min_dates))


def summarize_physics(df: pd.DataFrame, mechanisms: list[Mechanism]) -> pd.DataFrame:
    recs: list[dict[str, object]] = []
    for mech in mechanisms:
        mask = mech.mask_fn(df).fillna(False)
        sub = df[mask].copy()
        comp = df[~mask].copy()
        fb_ci = block_bootstrap_mean_ci(sub.assign(future_break_float=sub["future_break_any"].astype(float)), "future_break_float")
        d1_ci = block_bootstrap_mean_ci(sub, "d1_hit")
        recs.append(
            {
                "mechanism": mech.name,
                "rows": int(len(sub)),
                "dates": int(sub["target_date"].nunique()) if len(sub) else 0,
                "cities": int(sub["city"].nunique()) if len(sub) else 0,
                "row_share": float(len(sub) / len(df)) if len(df) else np.nan,
                "support": support_label(len(sub), sub["target_date"].nunique(), mech.broad_min_rows, mech.broad_min_dates),
                "avg_trend_1h_f": float(sub["temp_trend_1h_f"].mean()) if len(sub) else np.nan,
                "avg_trend_3h_f": float(sub["temp_trend_3h_f"].mean()) if len(sub) else np.nan,
                "future_break_rate": float(sub["future_break_any"].mean()) if len(sub) else np.nan,
                "future_break_ci_low": fb_ci[0],
                "future_break_ci_high": fb_ci[1],
                "current_hold_rate": float(sub["current_bracket_held"].mean()) if len(sub) else np.nan,
                "d1_hit_rate": float(sub["d1_hit"].mean()) if len(sub) else np.nan,
                "d1_hit_ci_low": d1_ci[0],
                "d1_hit_ci_high": d1_ci[1],
                "d2_hit_rate": float(sub["d2_hit"].mean()) if len(sub) else np.nan,
                "skip_over_d1_rate": float(sub["skip_over_d1"].mean()) if "skip_over_d1" in sub and len(sub) else np.nan,
                "day_open_or_marginal_rate": float(sub["day_regime"].isin(["day_open_runway", "day_marginal_runway"]).mean())
                if len(sub)
                else np.nan,
                "future_break_delta_vs_complement": float(sub["future_break_any"].mean() - comp["future_break_any"].mean())
                if len(sub) and len(comp)
                else np.nan,
                "d1_hit_delta_vs_complement": float(sub["d1_hit"].mean() - comp["d1_hit"].mean())
                if len(sub) and len(comp)
                else np.nan,
                "current_hold_delta_vs_complement": float(sub["current_bracket_held"].mean() - comp["current_bracket_held"].mean())
                if len(sub) and len(comp)
                else np.nan,
            }
        )
    return pd.DataFrame(recs)


EXPRESSIONS = [
    ("current_yes", "current_yes_ask", "current_yes_payoff", "current_yes_roi"),
    ("current_bracket_no", "current_bracket_no_ask", "current_bracket_no_payoff", "current_bracket_no_roi"),
    ("d1_no", "d1_no_ask", "d1_no_payoff", "d1_no_roi"),
    ("d2_no", "d2_no_ask", "d2_no_payoff", "d2_no_roi"),
]


def summarize_expression(df: pd.DataFrame, mechanisms: list[Mechanism]) -> pd.DataFrame:
    recs: list[dict[str, object]] = []
    for mech in mechanisms:
        mask = mech.mask_fn(df).fillna(False)
        for expression, ask_col, payoff_col, roi_col in EXPRESSIONS:
            sub = df[mask & df[ask_col].notna() & df[roi_col].notna()].copy()
            comp = df[(~mask) & df[ask_col].notna() & df[roi_col].notna()].copy()
            ci_low, ci_high = roi_ci(sub, roi_col)
            top5_removed = np.nan
            if len(sub) > 5:
                top = sub[roi_col].sort_values(ascending=False).head(5).sum()
                top5_removed = float((sub[roi_col].sum() - top) / (len(sub) - 5))
            recent = sub[sub["target_date"].ge(RECENT_START)]
            recs.append(
                {
                    "mechanism": mech.name,
                    "expression": expression,
                    "rows": int(len(sub)),
                    "dates": int(sub["target_date"].nunique()) if len(sub) else 0,
                    "cities": int(sub["city"].nunique()) if len(sub) else 0,
                    "support": support_label(len(sub), sub["target_date"].nunique(), mech.broad_min_rows, mech.broad_min_dates),
                    "avg_ask": float(sub[ask_col].mean()) if len(sub) else np.nan,
                    "win_rate": float(sub[payoff_col].mean()) if len(sub) else np.nan,
                    "roi": float(sub[roi_col].mean()) if len(sub) else np.nan,
                    "roi_ci_low": ci_low,
                    "roi_ci_high": ci_high,
                    "top5_removed_roi": top5_removed,
                    "recent_rows": int(len(recent)),
                    "recent_roi": float(recent[roi_col].mean()) if len(recent) else np.nan,
                    "complement_roi": float(comp[roi_col].mean()) if len(comp) else np.nan,
                    "roi_delta_vs_complement": float(sub[roi_col].mean() - comp[roi_col].mean())
                    if len(sub) and len(comp)
                    else np.nan,
                }
            )
    return pd.DataFrame(recs)


def load_headb() -> pd.DataFrame:
    if not HEADB_ROWS.exists():
        return pd.DataFrame()
    df = pd.read_csv(HEADB_ROWS, low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    df["decision_hour_local"] = pd.to_numeric(df["decision_hour_local"], errors="coerce").astype("Int64")
    df = add_path_features(df)
    if "win" in df.columns:
        df["win"] = to_bool(df["win"])
    return df


def summarize_headb(df: pd.DataFrame, mechanisms: list[Mechanism]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    recs: list[dict[str, object]] = []
    for strategy, strat_df in df.groupby("strategy", sort=False):
        for mech in mechanisms:
            mask = mech.mask_fn(strat_df).fillna(False)
            sub = strat_df[mask].copy()
            comp = strat_df[~mask].copy()
            if sub.empty:
                recs.append(
                    {
                        "strategy": strategy,
                        "mechanism": mech.name,
                        "rows": 0,
                        "dates": 0,
                        "cities": 0,
                        "support": "thin_do_not_select",
                    }
                )
                continue
            roi_col = "cash_roi_exec_fee" if "cash_roi_exec_fee" in sub.columns else "exec_fee_pnl_per_1"
            sub[roi_col] = pd.to_numeric(sub[roi_col], errors="coerce")
            comp[roi_col] = pd.to_numeric(comp[roi_col], errors="coerce") if len(comp) else pd.Series(dtype=float)
            ci_low, ci_high = roi_ci(sub.dropna(subset=[roi_col]), roi_col)
            top5_removed = np.nan
            if sub[roi_col].notna().sum() > 5:
                vals = sub[roi_col].dropna().sort_values(ascending=False)
                top5_removed = float((vals.sum() - vals.head(5).sum()) / (len(vals) - 5))
            recent = sub[sub["target_date"].ge(RECENT_START)]
            recs.append(
                {
                    "strategy": strategy,
                    "mechanism": mech.name,
                    "rows": int(len(sub)),
                    "dates": int(sub["target_date"].nunique()),
                    "cities": int(sub["city"].nunique()),
                    "support": support_label(len(sub), sub["target_date"].nunique(), mech.headb_min_rows, headb_min_dates(mech)),
                    "win_rate": float(sub["win"].mean()) if "win" in sub else np.nan,
                    "avg_entry_stress": float(pd.to_numeric(sub.get("entry_stress"), errors="coerce").mean()),
                    "roi": float(sub[roi_col].mean()),
                    "roi_ci_low": ci_low,
                    "roi_ci_high": ci_high,
                    "top5_removed_roi": top5_removed,
                    "recent_rows": int(len(recent)),
                    "recent_roi": float(recent[roi_col].mean()) if len(recent) else np.nan,
                    "complement_roi": float(comp[roi_col].mean()) if len(comp) else np.nan,
                    "roi_delta_vs_complement": float(sub[roi_col].mean() - comp[roi_col].mean())
                    if len(comp) and comp[roi_col].notna().any()
                    else np.nan,
                }
            )
    return pd.DataFrame(recs)


def mechanism_dictionary(mechanisms: list[Mechanism]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "mechanism": m.name,
                "definition": m.definition,
                "mechanism_read": m.mechanism_read,
                "recommended_use": m.recommended_use,
                "broad_min_rows": m.broad_min_rows,
                "broad_min_dates": m.broad_min_dates,
                "headb_min_rows": m.headb_min_rows,
            }
            for m in mechanisms
        ]
    )


def md_table(df: pd.DataFrame, cols: list[str], max_rows: int | None = None) -> str:
    view = df.loc[:, cols].copy()
    if max_rows is not None:
        view = view.head(max_rows)
    pct_cols = {
        "row_share",
        "future_break_rate",
        "future_break_ci_low",
        "future_break_ci_high",
        "current_hold_rate",
        "d1_hit_rate",
        "d1_hit_ci_low",
        "d1_hit_ci_high",
        "d2_hit_rate",
        "skip_over_d1_rate",
        "day_open_or_marginal_rate",
        "future_break_delta_vs_complement",
        "d1_hit_delta_vs_complement",
        "current_hold_delta_vs_complement",
        "win_rate",
        "roi",
        "roi_ci_low",
        "roi_ci_high",
        "top5_removed_roi",
        "recent_roi",
        "complement_roi",
        "roi_delta_vs_complement",
    }
    num_cols = {"avg_trend_1h_f", "avg_trend_3h_f", "avg_ask", "avg_entry_stress"}
    int_cols = {"rows", "dates", "cities", "recent_rows"}
    for col in view.columns:
        if col in pct_cols:
            signed = col.endswith("delta_vs_complement") or col in {"roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi", "recent_roi", "complement_roi"}
            view[col] = view[col].map(lambda x: pct(x, signed=signed))
        elif col in num_cols:
            view[col] = view[col].map(lambda x: num(x, 3))
        elif col in int_cols:
            view[col] = view[col].map(lambda x: "" if pd.isna(x) else str(int(x)))
    view = view.fillna("").astype(str)
    lines = [
        "| " + " | ".join(view.columns) + " |",
        "| " + " | ".join(["---"] * len(view.columns)) + " |",
    ]
    for row in view.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def write_outputs(
    snapshot: dict[str, object],
    full_atlas: pd.DataFrame,
    labeled: pd.DataFrame,
    dictionary: pd.DataFrame,
    physics: pd.DataFrame,
    expression: pd.DataFrame,
    headb: pd.DataFrame,
) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dictionary.to_csv(OUT_DIR / "mechanism_dictionary.csv", index=False)
    physics.to_csv(OUT_DIR / "mechanism_physics_summary.csv", index=False)
    expression.to_csv(OUT_DIR / "mechanism_expression_summary.csv", index=False)
    if not headb.empty:
        headb.to_csv(OUT_DIR / "headb_mechanism_summary.csv", index=False)

    by_bucket = (
        labeled.groupby(["trend_3h_bucket", "day_regime"], dropna=False)
        .agg(
            rows=("city", "size"),
            dates=("target_date", "nunique"),
            future_break_rate=("future_break_any", "mean"),
            current_hold_rate=("current_bracket_held", "mean"),
            d1_hit_rate=("d1_hit", "mean"),
            d2_hit_rate=("d2_hit", "mean"),
        )
        .reset_index()
        .sort_values(["trend_3h_bucket", "rows"], ascending=[True, False])
    )
    by_bucket.to_csv(OUT_DIR / "trend3h_bucket_by_day_regime.csv", index=False)

    serializable_snapshot = json.loads(json.dumps(snapshot, default=str))
    OUT_JSON.write_text(
        json.dumps(
            {
                "snapshot": serializable_snapshot,
                "atlas": {
                    "path": display_path(ATLAS_ROWS),
                    "rows": int(len(full_atlas)),
                    "min_target_date": str(full_atlas["target_date"].min()) if len(full_atlas) else None,
                    "max_target_date": str(full_atlas["target_date"].max()) if len(full_atlas) else None,
                    "labeled_rows": int(len(labeled)),
                    "labeled_min_target_date": str(labeled["target_date"].min()) if len(labeled) else None,
                    "labeled_max_target_date": str(labeled["target_date"].max()) if len(labeled) else None,
                },
                "outputs": {
                    "mechanism_dictionary": display_path(OUT_DIR / "mechanism_dictionary.csv"),
                    "mechanism_physics_summary": display_path(OUT_DIR / "mechanism_physics_summary.csv"),
                    "mechanism_expression_summary": display_path(OUT_DIR / "mechanism_expression_summary.csv"),
                    "headb_mechanism_summary": display_path(OUT_DIR / "headb_mechanism_summary.csv"),
                    "trend3h_bucket_by_day_regime": display_path(OUT_DIR / "trend3h_bucket_by_day_regime.csv"),
                },
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    key_physics_order = [
        "all_labeled_states",
        "trend3h_cooling_lt_neg0_5",
        "trend3h_flat_abs_lt0_5",
        "trend3h_warming_ge0_5",
        "trend3h_strong_warming_ge2",
        "legacy_trend3h_positive_gt0",
        "sustained_warming_1h3h",
        "one_hour_warm_without_3h",
        "runway_sustained_warming",
        "solar_runway_sustained_warming",
        "late_reheat_after_dip",
        "humid_or_cloud_warming",
        "mature_cooling_or_fade",
        "plateau_flat_path",
    ]
    physics_view = physics.set_index("mechanism").loc[key_physics_order].reset_index()
    expression_focus_mechs = [
        "all_labeled_states",
        "trend3h_flat_abs_lt0_5",
        "trend3h_warming_ge0_5",
        "sustained_warming_1h3h",
        "runway_sustained_warming",
        "late_reheat_after_dip",
        "mature_cooling_or_fade",
    ]
    expression_view = expression[expression["mechanism"].isin(expression_focus_mechs)].copy()
    expression_view = expression_view.sort_values(["mechanism", "expression"])
    headb_view = pd.DataFrame()
    if not headb.empty:
        headb_view = headb[
            headb["mechanism"].isin(
                [
                    "all_labeled_states",
                    "legacy_trend3h_positive_gt0",
                    "trend3h_warming_ge0_5",
                    "sustained_warming_1h3h",
                    "runway_sustained_warming",
                    "solar_runway_sustained_warming",
                    "late_reheat_after_dip",
                    "humid_or_cloud_warming",
                ]
            )
            & headb["strategy"].astype(str).str.contains("d1_yes", na=False)
        ].copy()
        headb_view = headb_view.sort_values(["strategy", "mechanism"])

    fsc = snapshot["fact_signal_candidates"]
    trades = snapshot["fact_trades"]
    gate = snapshot["clob_gate"]

    lines = [
        "# Temperature Path Mechanism Decomposition v1",
        "",
        "Generated: " + str(snapshot["generated_at_utc"]),
        "",
        "Scope: research foundation only.  No live config, runner, order, or sizing behavior changed.",
        "",
        "## Verdict",
        "",
        "The clean way to carry yesterday's `trend3h_positive` finding forward is to promote it into a shared path feature family, not to freeze it as a trading filter.",
        "",
        "Use these names going forward:",
        "",
        "```text",
        "trend3h_bucket: cooling_lt_neg0_5 / flat_abs_lt0_5 / warming_0_5_to_2 / strong_warming_ge2",
        "trend3h_warming_ge0_5: clean sustained warming",
        "sustained_warming_1h3h: 1h uptick confirmed by 3h path",
        "one_hour_warm_without_3h: noisy/spiky reheat warning",
        "runway_sustained_warming: sustained warming + open/marginal forecast space",
        "late_reheat_after_dip: HeadB-style false-fade/reheat conflict",
        "exclude_trend3h_flat: route-specific tmax/NO-side selector variant, not a universal mechanism",
        "```",
        "",
        "Read: `trend3h_positive` is real enough to keep as shared telemetry/context, especially for HeadB reheat confirmation, but it is not a standalone live gate.  Mechanism labels must feed probability heads or shadow telemetry first; expression selection still needs ask/depth/fresh-forward proof.",
        "",
        "## Data Snapshot",
        "",
        f"- `fact_signal_candidates`: {fsc['rows']} rows, {fsc['min_date']}..{fsc['max_date']}, built {fsc['built_at']}.",
        f"- `fact_trades`: {trades['rows']} rows, {trades['min_date']}..{trades['max_date']}, built {trades['built_at']}.",
        f"- CLOB fill coverage gate: `{gate.get('gate_pass')}`; fail_reasons={gate.get('fail_reasons')}.",
        f"- Atlas state rows: {len(full_atlas)} rows, {full_atlas['target_date'].min()}..{full_atlas['target_date'].max()}, {full_atlas['city'].nunique()} cities.",
        f"- Labelled mechanism rows: {len(labeled)} rows, {labeled['target_date'].min()}..{labeled['target_date'].max()}, {labeled['target_date'].nunique()} dates.",
        "",
        "Note: the latest canonical fact layer is fresher than the intraday atlas.  The current atlas feature layer has state rows through 2026-07-03; rows after that need a refreshed observed-path feature factory before they should enter this mechanism report.",
        "",
        "## Mechanism Dictionary",
        "",
        md_table(
            dictionary[
                dictionary["mechanism"].isin(
                    [
                        "trend3h_cooling_lt_neg0_5",
                        "trend3h_flat_abs_lt0_5",
                        "trend3h_warming_ge0_5",
                        "sustained_warming_1h3h",
                        "one_hour_warm_without_3h",
                        "runway_sustained_warming",
                        "solar_runway_sustained_warming",
                        "late_reheat_after_dip",
                        "humid_or_cloud_warming",
                        "mature_cooling_or_fade",
                        "plateau_flat_path",
                    ]
                )
            ],
            ["mechanism", "definition", "recommended_use"],
        ),
        "",
        "## Physical Outcome",
        "",
        "This table asks whether the path label maps to the actual temperature path, before any trading expression.",
        "",
        md_table(
            physics_view,
            [
                "mechanism",
                "rows",
                "dates",
                "support",
                "future_break_rate",
                "current_hold_rate",
                "d1_hit_rate",
                "d2_hit_rate",
                "future_break_delta_vs_complement",
                "d1_hit_delta_vs_complement",
                "current_hold_delta_vs_complement",
            ],
        ),
        "",
        "## Expression Sanity",
        "",
        "Atlas expression rows are broad same-snapshot diagnostics.  They are not live fills and do not include fresh-book fill feasibility.",
        "",
        md_table(
            expression_view,
            [
                "mechanism",
                "expression",
                "rows",
                "dates",
                "avg_ask",
                "win_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "roi_delta_vs_complement",
                "recent_rows",
                "recent_roi",
            ],
        ),
        "",
        "## HeadB Check",
        "",
        "HeadB remains the METAR rich-current / runway d1 YES reversal family.  This section only checks whether the path expressions improve its shadow denominator; it does not import HeadA maker-first or TP20 assumptions.",
        "",
    ]
    if headb_view.empty:
        lines.append("No HeadB enriched rows were available for this run.")
    else:
        lines.append(
            md_table(
                headb_view,
                [
                    "strategy",
                    "mechanism",
                    "rows",
                    "dates",
                    "support",
                    "win_rate",
                    "avg_entry_stress",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "roi_delta_vs_complement",
                    "recent_rows",
                    "recent_roi",
                ],
            )
        )
    lines.extend(
        [
            "",
            "## Clean Expressions To Reuse",
            "",
            "1. `trend3h_bucket` is the canonical foundation field.  It is a context label with four states; it should be recorded on every strategy-head row.",
            "2. `trend3h_warming_ge0_5` is the clean replacement for loose `trend3h_positive` when the intended mechanism is sustained warming.",
            "3. `sustained_warming_1h3h` is the professional HeadB confirmation: HeadB already asks for a 1h uptick, and this prevents a single METAR jump from masquerading as real reheat.",
            "4. `one_hour_warm_without_3h` should be carried as a warning/diagnostic label, not a selector.",
            "5. `runway_sustained_warming` is the shared regime-compatible expression: observed warming plus forecast space.  It belongs in tmax/regime probability heads, not as a hard gate.",
            "6. `late_reheat_after_dip` is the HeadB-specific conflict label.  It is mechanism-clear but thinner, so it stays shadow telemetry until fresh-forward rows accumulate.",
            "7. `exclude_trend3h_flat` is only a route-specific selector variant for the tmax clean-edge work.  Do not call it `no_trend3h_flat` without spelling out that it means removing flat 3h rows.",
            "",
            "## Contract Read",
            "",
            "```text",
            "significance=NA for mechanism foundation labels",
            "baseline=NA for context labels; expression rows include same-snapshot sanity only",
            "forward=FAIL/NA for live action because atlas coverage after 2026-07-03 is not yet refreshed and HeadB fresh-forward rows remain sparse",
            "conclusion=inconclusive_for_live, promote_as_shared_context_feature_and_shadow_telemetry",
            "```",
            "",
            "8-ring coverage: [1] descriptive slices covered, [2] date-block CIs included for expression/physical summaries, [3] signal discrimination partially covered by physical outcome rates, [4] probability calibration not covered, [5] execution microstructure not covered here, [6] capacity not covered, [7] date clustering handled by block bootstrap, [8] market-expression sanity included but not sufficient for live.",
            "",
            "## Artifacts",
            "",
            f"- Script: `{display_path(ROOT / 'scripts/analysis/reheat_risk/research_temperature_path_mechanism_decomposition_v1.py')}`",
            f"- JSON: `{display_path(OUT_JSON)}`",
            f"- Mechanism dictionary: `{display_path(OUT_DIR / 'mechanism_dictionary.csv')}`",
            f"- Physical summary: `{display_path(OUT_DIR / 'mechanism_physics_summary.csv')}`",
            f"- Expression summary: `{display_path(OUT_DIR / 'mechanism_expression_summary.csv')}`",
            f"- HeadB summary: `{display_path(OUT_DIR / 'headb_mechanism_summary.csv')}`",
            f"- Trend bucket x regime: `{display_path(OUT_DIR / 'trend3h_bucket_by_day_regime.csv')}`",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    snapshot = db_snapshot()
    full_atlas, labeled = load_atlas()
    mechanisms = mechanism_defs()
    dictionary = mechanism_dictionary(mechanisms)
    physics = summarize_physics(labeled, mechanisms)
    expression = summarize_expression(labeled, mechanisms)
    headb_rows = load_headb()
    headb = summarize_headb(headb_rows, mechanisms)
    write_outputs(snapshot, full_atlas, labeled, dictionary, physics, expression, headb)
    print(f"wrote {display_path(OUT_MD)}")
    print(f"wrote {display_path(OUT_JSON)}")
    print(f"wrote {display_path(OUT_DIR)}")


if __name__ == "__main__":
    main()
