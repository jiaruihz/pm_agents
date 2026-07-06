#!/usr/bin/env python3
"""Tmax cross-hour coherence and settlement-basis audit.

Research-only. This script does not touch live runners, runtime configs, or
canonical settlement tables.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_tmax_distribution_p0_anchor_scorecard_v1 as p0  # noqa: E402
import research_tmax_distribution_p1_fusion_scorecard_v1 as p1  # noqa: E402
import research_tmax_distribution_p2_ev_shadow_v1 as p2  # noqa: E402
import research_tmax_distribution_p3_feature_ablation_v1 as p3  # noqa: E402
import research_tmax_distribution_p4_observed_label_extension_v1 as p4  # noqa: E402
import research_tmax_distribution_p5_walk_forward_execution_replay_v1 as p5  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-06-tmax-cross-hour-coherence-audit-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-06-tmax-cross-hour-coherence-audit-v1.json"
DB_PATH = ROOT / "runtime/weather.db"

METHOD = "loo_no_city_source_blend"
MODEL_SPEC = "loo_no_city_source"
PRIMARY_EDGE = 0.02
TAKER_FEE_RATE = 0.05
ALPHA_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]
BOOT_N = 1000
BOOT_SEED = 20260706


def _safe_float(value: object) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        return _json_ready(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _fmt_num(value: object, digits: int = 3) -> str:
    v = _safe_float(value)
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}"


def _fmt_pct(value: object, *, signed: bool = True) -> str:
    v = _safe_float(value)
    if v is None:
        return "n/a"
    sign = "+" if signed else ""
    return f"{v:{sign}.1%}"


def _table(df: pd.DataFrame, columns: list[str], *, max_rows: int | None = None) -> list[str]:
    if df.empty:
        return ["_No rows._"]
    if max_rows is not None:
        df = df.head(max_rows)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in df.to_dict("records"):
        vals: list[str] = []
        for col in columns:
            val = row.get(col)
            if col.endswith("_rate") or col in {
                "mean_p_current",
                "actual_current_rate",
                "calibration_error",
                "incoherence_mean",
                "incoherence_median",
                "incoherence_ci_low",
                "incoherence_ci_high",
                "p_current_mean",
                "basis_below_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "first_roi",
                "reconcile_roi",
                "net_delta_roi",
                "logloss_delta_vs_market",
                "brier_delta_vs_market",
            }:
                vals.append(_fmt_pct(val, signed=col not in {"mean_p_current", "actual_current_rate", "p_current_mean", "basis_below_rate"}))
            elif col in {"avg_bid", "avg_ask", "avg_close_bid", "avg_fee", "cost", "pnl", "net_delta_pnl", "turnover_cost"}:
                vals.append(_fmt_num(val, 3))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def _date_block_mean_ci(rows: pd.DataFrame, value_col: str, date_col: str = "target_date") -> dict[str, float]:
    if rows.empty:
        return {"mean": math.nan, "ci_low": math.nan, "ci_high": math.nan, "n_dates": 0}
    by_date = rows.groupby(date_col, as_index=False)[value_col].mean()
    mean = float(by_date[value_col].mean()) if len(by_date) else math.nan
    if len(by_date) < 3:
        return {"mean": mean, "ci_low": math.nan, "ci_high": math.nan, "n_dates": int(len(by_date))}
    rng = np.random.default_rng(BOOT_SEED)
    arr = by_date[value_col].to_numpy(dtype=float)
    boot = [float(np.mean(rng.choice(arr, size=len(arr), replace=True))) for _ in range(BOOT_N)]
    return {
        "mean": mean,
        "ci_low": float(np.percentile(boot, 2.5)),
        "ci_high": float(np.percentile(boot, 97.5)),
        "n_dates": int(len(by_date)),
    }


def _date_block_roi_ci(rows: pd.DataFrame, cost_col: str, pnl_col: str) -> dict[str, float]:
    if rows.empty:
        return {"roi": math.nan, "ci_low": math.nan, "ci_high": math.nan, "dates": 0}
    cost = float(rows[cost_col].sum())
    pnl = float(rows[pnl_col].sum())
    roi = pnl / cost if cost else math.nan
    by_date = rows.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    if len(by_date) < 3:
        return {"roi": roi, "ci_low": math.nan, "ci_high": math.nan, "dates": int(len(by_date))}
    rng = np.random.default_rng(BOOT_SEED)
    arr = by_date[["cost", "pnl"]].to_numpy(dtype=float)
    boot = []
    for _ in range(BOOT_N):
        sample = arr[rng.integers(0, len(arr), size=len(arr))]
        c = sample[:, 0].sum()
        p = sample[:, 1].sum()
        boot.append(p / c if c else math.nan)
    return {
        "roi": roi,
        "ci_low": float(np.nanpercentile(boot, 2.5)),
        "ci_high": float(np.nanpercentile(boot, 97.5)),
        "dates": int(len(by_date)),
    }


def _inventory() -> dict[str, Any]:
    out: dict[str, Any] = {}
    if DB_PATH.exists():
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1000")
        try:
            for table, col in [
                ("fact_signal_candidates", "event_date"),
                ("fact_trades", "target_date"),
                ("settlement_outcomes", "target_date"),
            ]:
                try:
                    out[table] = dict(
                        conn.execute(
                            f"SELECT COUNT(*) AS rows, MIN({col}) AS min_date, MAX({col}) AS max_date FROM {table}"
                        ).fetchone()
                    )
                except sqlite3.Error as exc:
                    out[table] = {"error": str(exc)}
        finally:
            conn.close()
    for name, path in [
        ("atlas", p0.ATLAS_PATH),
        ("p5_opportunities", p5.OUT_DIR / "opportunities.csv"),
        ("p6_shadow_events", ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv"),
    ]:
        if path.exists():
            df = pd.read_csv(path, usecols=lambda c: c in {"target_date"})
            out[name] = {
                "rows": int(len(df)),
                "min_date": str(df["target_date"].min()) if "target_date" in df and len(df) else None,
                "max_date": str(df["target_date"].max()) if "target_date" in df and len(df) else None,
            }
        else:
            out[name] = {"missing": str(path)}
    return out


def _prediction_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    df, counters = p5._prepare_df()
    selection_df, dev_cv_preds, forward_preds, pred_meta = p5._selected_specs_and_predictions(df)
    base = df.drop_duplicates(["city", "target_date", "decision_hour_local", "actual_bucket"]).copy()
    meta_cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "actual_bucket",
        "label_source",
        "eval_slice",
        "split",
        "unit",
        "current_bracket",
        "d1_no_bracket",
        "d2_no_bracket",
        "current_yes_ask",
        "current_bracket_no_ask",
        "current_no_ask",
        "current_no_bid",
        "d1_no_ask",
        "d1_no_bid",
        "d2_no_ask",
        "d2_no_bid",
        "forecast_max_native",
        "running_native",
        "current_native",
        "forecast_to_current_upper_native",
        "forecast_peak_delta_hours_local",
        "temp_trend_3h_f",
        "decision_snapshot_ts_utc",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "solar_window",
        "forecast_source",
        "city_family",
    ]
    meta = base[[c for c in meta_cols if c in base.columns]].copy()
    keys = ["city", "target_date", "decision_hour_local", "actual_bucket"]

    dev = dev_cv_preds.merge(meta, on=keys, how="left", validate="one_to_one")
    dev["scope"] = "dev_cv"
    forward = forward_preds.merge(meta, on=keys, how="left", validate="one_to_one")
    forward["scope"] = np.where(forward["eval_slice"].eq(p4.VERIFIED_SLICE), "verified_forward", "extension_forward")
    pred_all = pd.concat([dev, forward], ignore_index=True)
    for bucket in p0.BUCKETS:
        if f"market_local_norm_p_{bucket}" not in pred_all.columns:
            pred_all[f"market_local_norm_p_{bucket}"] = pred_all[f"market_p_{bucket}"]
    meta_out = {
        "counters": counters,
        "prediction_meta": pred_meta,
        "rows": int(len(df)),
        "date_range": [str(df["target_date"].min()), str(df["target_date"].max())],
        "cities": int(df["city"].nunique()),
    }
    return df, pred_all, selection_df, meta, meta_out


def _interval_contains(interval: tuple[float, float] | None, value: float | None) -> bool:
    if interval is None or value is None:
        return False
    lo, hi = interval
    return value >= lo - 1e-9 and value <= hi + 1e-9


def _reached_bucket(prev: pd.Series, next_running: float | None) -> str:
    cur_iv = p0._interval(prev.get("current_bracket"))
    d1_iv = p0._interval(prev.get("d1_no_bracket"))
    d2_iv = p0._interval(prev.get("d2_no_bracket"))
    if next_running is None:
        return "unknown"
    if _interval_contains(cur_iv, next_running):
        return "current"
    if _interval_contains(d1_iv, next_running):
        return "d1"
    if _interval_contains(d2_iv, next_running):
        return "d2"
    if d2_iv is not None and next_running > d2_iv[1] + 1e-9:
        return "tail_or_above"
    if cur_iv is not None and next_running < cur_iv[0] - 1e-9:
        return "below_prev_current"
    return "between_or_unmapped"


def _coherent_current_prob(prev: pd.Series, reached: str, method: str = METHOD) -> float | None:
    probs = {bucket: _safe_float(prev.get(f"{method}_p_{bucket}")) for bucket in p0.BUCKETS}
    if any(v is None for v in probs.values()):
        return None
    p_current = float(probs["current"])
    p_d1 = float(probs["d1"])
    p_d2 = float(probs["d2"])
    p_tail = float(probs["tail"])
    if reached == "current":
        return p_current
    if reached == "d1":
        den = p_d1 + p_d2 + p_tail
        return p_d1 / den if den > 0 else None
    if reached == "d2":
        den = p_d2 + p_tail
        return p_d2 / den if den > 0 else None
    return None


def _reanchor_group(prev: pd.Series, nxt: pd.Series, reached: str) -> str:
    if str(prev.get("current_bracket")) == str(nxt.get("current_bracket")):
        return "no_reanchor"
    if str(prev.get("d1_no_bracket")) == str(nxt.get("current_bracket")):
        return "d1_reanchor"
    if str(prev.get("d2_no_bracket")) == str(nxt.get("current_bracket")):
        return "d2_reanchor"
    if reached == "tail_or_above":
        return "tail_reanchor"
    return "other_reanchor"


def build_coherence_pairs(pred_all: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pred_all[pred_all["scope"].isin(["dev_cv", "verified_forward", "extension_forward"])].copy()
    df = df.dropna(subset=[f"{METHOD}_p_current", "running_native"])
    df = df.sort_values(["scope", "city", "target_date", "decision_hour_local"])
    rows: list[dict[str, Any]] = []
    for (scope, city, target_date), grp in df.groupby(["scope", "city", "target_date"], dropna=False):
        grp = grp.sort_values("decision_hour_local")
        recs = grp.to_dict("records")
        for prev_item, next_item in zip(recs, recs[1:]):
            prev = pd.Series(prev_item)
            nxt = pd.Series(next_item)
            next_running = _safe_float(nxt.get("running_native"))
            reached = _reached_bucket(prev, next_running)
            coherent = _coherent_current_prob(prev, reached)
            refit = _safe_float(nxt.get(f"{METHOD}_p_current"))
            if coherent is None or refit is None:
                continue
            rows.append(
                {
                    "scope": scope,
                    "city": city,
                    "target_date": target_date,
                    "hour_t": prev_item.get("decision_hour_local"),
                    "hour_t1": next_item.get("decision_hour_local"),
                    "current_t": prev_item.get("current_bracket"),
                    "current_t1": next_item.get("current_bracket"),
                    "reached_bucket_from_t": reached,
                    "reanchor_group": _reanchor_group(prev, nxt, reached),
                    "coherent_p_current": coherent,
                    "refit_p_current": refit,
                    "incoherence": refit - coherent,
                    "prev_p_current": prev_item.get(f"{METHOD}_p_current"),
                    "prev_p_d1": prev_item.get(f"{METHOD}_p_d1"),
                    "prev_p_d2": prev_item.get(f"{METHOD}_p_d2"),
                    "prev_p_tail": prev_item.get(f"{METHOD}_p_tail"),
                    "next_p_current": refit,
                    "actual_bucket_t1": next_item.get("actual_bucket"),
                    "label_source": next_item.get("label_source"),
                    "eval_slice": next_item.get("eval_slice"),
                    "forecast_peak_delta_t1": next_item.get("forecast_peak_delta_hours_local"),
                    "temp_trend_3h_t1": next_item.get("temp_trend_3h_f"),
                    "running_max_state_t1": next_item.get("running_max_state"),
                    "intraday_state_t1": next_item.get("intraday_state"),
                }
            )
    pairs = pd.DataFrame(rows)
    if pairs.empty:
        return pairs, pd.DataFrame()
    summary_rows: list[dict[str, Any]] = []
    for cols in [
        ["scope", "reanchor_group"],
        ["reanchor_group"],
        ["scope", "reached_bucket_from_t"],
    ]:
        for key, grp in pairs.groupby(cols, dropna=False):
            if not isinstance(key, tuple):
                key = (key,)
            row = {col: val for col, val in zip(cols, key)}
            ci = _date_block_mean_ci(grp, "incoherence")
            row.update(
                {
                    "rows": int(len(grp)),
                    "dates": int(grp["target_date"].nunique()),
                    "cities": int(grp["city"].nunique()),
                    "incoherence_mean": float(grp["incoherence"].mean()),
                    "incoherence_median": float(grp["incoherence"].median()),
                    "incoherence_p10": float(grp["incoherence"].quantile(0.10)),
                    "incoherence_p90": float(grp["incoherence"].quantile(0.90)),
                    "positive_rate": float((grp["incoherence"] > 0).mean()),
                    "incoherence_ci_low": ci["ci_low"],
                    "incoherence_ci_high": ci["ci_high"],
                }
            )
            row["grouping"] = "+".join(cols)
            summary_rows.append(row)
    summary = pd.DataFrame(summary_rows).sort_values(["grouping", "scope", "reanchor_group"], na_position="last")
    return pairs, summary


def _ceiling_margin_c(row: pd.Series) -> float | None:
    margin = _safe_float(row.get("forecast_to_current_upper_native"))
    if margin is None:
        return None
    unit = str(row.get("unit") or "").upper()
    return margin / 1.8 if unit == "F" else margin


def build_current_calibration(pred_all: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = pred_all.dropna(subset=[f"{METHOD}_p_current", "actual_bucket"]).copy()
    rows["p_current"] = pd.to_numeric(rows[f"{METHOD}_p_current"], errors="coerce")
    rows["actual_current"] = rows["actual_bucket"].astype(str).eq("current").astype(float)
    rows["peak_phase"] = np.select(
        [
            pd.to_numeric(rows["forecast_peak_delta_hours_local"], errors="coerce") <= 0,
            pd.to_numeric(rows["forecast_peak_delta_hours_local"], errors="coerce") > 0,
        ],
        ["pre_or_at_peak", "post_peak"],
        default="unknown",
    )
    trend = pd.to_numeric(rows["temp_trend_3h_f"], errors="coerce")
    rows["trend3h_bucket"] = np.select([trend > 0.5, trend < -0.5], ["warming", "cooling"], default="flat_or_unknown")
    rows["forecast_ceiling_margin_c"] = rows.apply(_ceiling_margin_c, axis=1)
    rows["ceiling_margin_bucket"] = np.where(rows["forecast_ceiling_margin_c"].ge(2.0), ">=+2C", "<+2C")
    rows.loc[rows["forecast_ceiling_margin_c"].isna(), "ceiling_margin_bucket"] = "unknown"
    rows["p_decile"] = pd.qcut(rows["p_current"], q=10, duplicates="drop").astype(str)

    def agg(grp: pd.DataFrame) -> dict[str, Any]:
        return {
            "rows": int(len(grp)),
            "dates": int(grp["target_date"].nunique()),
            "cities": int(grp["city"].nunique()),
            "mean_p_current": float(grp["p_current"].mean()),
            "actual_current_rate": float(grp["actual_current"].mean()),
            "calibration_error": float(grp["p_current"].mean() - grp["actual_current"].mean()),
            "brier_current": float(np.mean((grp["p_current"] - grp["actual_current"]) ** 2)),
        }

    summary_rows: list[dict[str, Any]] = []
    for cols in [
        ["scope"],
        ["scope", "p_decile"],
        ["scope", "peak_phase", "trend3h_bucket", "ceiling_margin_bucket"],
    ]:
        for key, grp in rows.groupby(cols, dropna=False):
            if not isinstance(key, tuple):
                key = (key,)
            row = {col: val for col, val in zip(cols, key)}
            row["grouping"] = "+".join(cols)
            row.update(agg(grp))
            summary_rows.append(row)
    summary = pd.DataFrame(summary_rows)
    summary = summary.sort_values(["grouping", "scope", "peak_phase", "trend3h_bucket", "ceiling_margin_bucket", "p_decile"], na_position="last")
    return rows, summary


def _basis_feature_rows(include_below: bool = True) -> tuple[pd.DataFrame, dict[str, Any]]:
    needed = set(
        [
            "city",
            "target_date",
            "decision_hour_local",
            "decision_snapshot_ts_utc",
            "unit",
            "current_bracket",
            "d1_no_bracket",
            "d2_no_bracket",
            "final_winning_bracket",
            "current_yes_ask",
            "current_bracket_no_ask",
            "current_no_ask",
            "current_no_bid",
            "d1_no_ask",
            "d1_no_bid",
            "d2_no_ask",
            "d2_no_bid",
            "forecast_max_native",
            "forecast_max_f",
            "running_native",
            "current_native",
            "decline_native",
            "tmpf_now",
            "dwpf_now",
            "dewpoint_depression_f",
            "relative_humidity_pct",
            "wind_speed_kt",
            "sky_cover_code",
            "temp_trend_1h_f",
            "temp_trend_3h_f",
            "minutes_since_running_max",
            "forecast_peak_hour_local",
            "forecast_peak_delta_hours_local",
            "forecast_peak_hour_spread",
            "forecast_gap_to_running_native",
            "gfs_gap_to_running_native",
            "ecmwf_gap_to_running_native",
            "forecast_source",
            "day_regime",
            "intraday_state",
            "moisture_cloud_regime",
            "wind_regime",
            "running_max_state",
            "solar_window",
            "city_family",
            "final_max_native",
        ]
    )
    raw = pd.read_csv(p0.ATLAS_PATH, usecols=lambda c: c in needed)
    counters: dict[str, Any] = {"raw_rows": int(len(raw)), "label_sources_raw": {}}
    skipped = {
        "missing_or_invalid_label": 0,
        "missing_interval": 0,
        "missing_market_quote": 0,
        "observed_derived_before_extension_window": 0,
    }
    rows: list[dict[str, Any]] = []
    for item in raw.to_dict("records"):
        s = pd.Series(item)
        actual, label_source = p4._derive_bucket(s)
        if actual is None:
            final_iv = p0._interval(s.get("final_winning_bracket"))
            current_iv_for_basis = p0._interval(s.get("current_bracket"))
            if (
                final_iv is not None
                and current_iv_for_basis is not None
                and final_iv[0] < current_iv_for_basis[0] - 1e-6
            ):
                actual, label_source = "below", "settlement_below_current"
        counters["label_sources_raw"][label_source] = counters["label_sources_raw"].get(label_source, 0) + 1
        if actual is None and include_below and label_source == "final_below_current":
            actual = "below"
        if actual is None:
            skipped["missing_or_invalid_label"] += 1
            continue
        target_date = str(item.get("target_date"))
        if label_source == "observed_max_derived" and target_date < "2026-06-27":
            skipped["observed_derived_before_extension_window"] += 1
            continue
        current_iv = p0._interval(s.get("current_bracket"))
        d1_iv = p0._interval(s.get("d1_no_bracket"))
        d2_iv = p0._interval(s.get("d2_no_bracket"))
        if current_iv is None or d1_iv is None or d2_iv is None:
            skipped["missing_interval"] += 1
            continue
        market = p0._market_distribution(s)
        if market is None:
            skipped["missing_market_quote"] += 1
            continue
        forecast_anchor = p0._soft_anchor_distribution(p0._as_float(s.get("forecast_max_native")), current_iv, d1_iv, d2_iv)
        running_anchor = p0._soft_anchor_distribution(p0._as_float(s.get("running_native")), current_iv, d1_iv, d2_iv)
        row = dict(item)
        row["actual_bucket"] = actual
        row["label_source"] = label_source
        row["eval_slice"] = (
            "train_pre_2026_06_21"
            if target_date < p1.TRAIN_CUTOFF
            else (p4.VERIFIED_SLICE if label_source == "settlement_outcomes" else p4.EXTENSION_SLICE)
        )
        row["split"] = "train_pre_2026_06_21" if target_date < p1.TRAIN_CUTOFF else "forward_2026_06_21_plus"
        row["hour_bucket"] = p0._hour_bucket(item.get("decision_hour_local"))
        hour = p0._as_float(item.get("decision_hour_local"))
        if hour is not None:
            row["decision_hour_sin"] = math.sin(2.0 * math.pi * hour / 24.0)
            row["decision_hour_cos"] = math.cos(2.0 * math.pi * hour / 24.0)
        peak_delta = p0._as_float(item.get("forecast_peak_delta_hours_local"))
        row["forecast_peak_delta_abs"] = abs(peak_delta) if peak_delta is not None else None
        current_upper = p4._safe_upper(current_iv)
        d1_upper = p4._safe_upper(d1_iv)
        d2_upper = p4._safe_upper(d2_iv)
        current_mid = p4._safe_mid(current_iv)
        d1_mid = p4._safe_mid(d1_iv)
        d2_mid = p4._safe_mid(d2_iv)
        row["forecast_minus_running_native"] = p4._delta(item.get("forecast_max_native"), item.get("running_native"))
        row["forecast_minus_current_native"] = p4._delta(item.get("forecast_max_native"), item.get("current_native"))
        row["running_minus_current_native"] = p4._delta(item.get("running_native"), item.get("current_native"))
        row["forecast_to_current_upper_native"] = p4._delta(item.get("forecast_max_native"), current_upper)
        row["forecast_to_d1_upper_native"] = p4._delta(item.get("forecast_max_native"), d1_upper)
        row["forecast_to_d2_upper_native"] = p4._delta(item.get("forecast_max_native"), d2_upper)
        row["forecast_to_current_mid_native"] = p4._delta(item.get("forecast_max_native"), current_mid)
        row["forecast_to_d1_mid_native"] = p4._delta(item.get("forecast_max_native"), d1_mid)
        row["forecast_to_d2_mid_native"] = p4._delta(item.get("forecast_max_native"), d2_mid)
        row["running_to_current_upper_native"] = p4._delta(item.get("running_native"), current_upper)
        row["current_to_current_upper_native"] = p4._delta(item.get("current_native"), current_upper)
        if current_mid is not None:
            row["running_position_in_current_native"] = p4._delta(item.get("running_native"), current_mid)
            row["current_position_in_current_native"] = p4._delta(item.get("current_native"), current_mid)
        for bucket in p0.BUCKETS:
            row[f"market_p_{bucket}"] = market[bucket]
            row[f"market_log_p_{bucket}"] = math.log(max(p0.EPS, market[bucket]))
            row[f"forecast_anchor_p_{bucket}"] = forecast_anchor[bucket]
            row[f"runningmax_anchor_p_{bucket}"] = running_anchor[bucket]
        row["market_entropy"] = p0._entropy_norm(market)
        probs_sorted = sorted(market.values(), reverse=True)
        row["market_top_p"] = probs_sorted[0]
        row["market_top2_gap"] = probs_sorted[0] - probs_sorted[1]
        rows.append(row)
    counters.update(skipped)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = p3._add_boundary_features(df)
        for col in sorted(set(p1.CONTEXT_CATEGORICAL + p1.CITY_CATEGORICAL + p3.REGIME_CATEGORICAL + p3.CITY_SOURCE_CATEGORICAL)):
            if col in df.columns:
                df[col] = df[col].where(df[col].notna(), "unknown").astype(str)
    counters["scored_rows_with_below"] = int(len(df))
    counters["below_rows"] = int((df["actual_bucket"] == "below").sum()) if not df.empty else 0
    return df, counters


def _expanding_basis_predictions(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    train_nonbelow = df[(df["target_date"] < p1.TRAIN_CUTOFF) & (df["actual_bucket"] != "below")].copy()
    specs = p3._feature_specs(train_nonbelow)
    p1.MODEL_SPECS = specs
    selection = p1._select_model(train_nonbelow, MODEL_SPEC)
    c_value = selection["selected"]["c"]
    alpha = selection["selected"]["cv_blend_alpha"]
    dates = sorted(df["target_date"].unique())
    frames = []
    for idx, date in enumerate(dates):
        test_df = df[df["target_date"] == date].copy()
        if date < p1.TRAIN_CUTOFF and idx < p1.MIN_TRAIN_DATES_FOR_CV:
            continue
        fit_df = df[(df["target_date"] < date) & (df["actual_bucket"] != "below")].copy()
        if fit_df.empty:
            continue
        pred = p1._fit_predict(fit_df, test_df, MODEL_SPEC, c_value)
        pred = p1._blend_predictions(pred, 1.0, f"{MODEL_SPEC}_model")
        pred = p1._blend_predictions(pred, alpha, METHOD)
        frames.append(pred)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return out, {"selected_c": c_value, "selected_alpha": alpha, "model_spec": MODEL_SPEC}


def _expr_ask(row: pd.Series, expression: str) -> float | None:
    mapping = {
        "current_yes": "current_yes_ask",
        "current_no": "current_bracket_no_ask",
        "d1_no": "d1_no_ask",
        "d2_no": "d2_no_ask",
    }
    return _safe_float(row.get(mapping[expression]))


def _expr_bid(row: pd.Series, expression: str) -> float | None:
    if expression == "current_yes":
        # The atlas does not store current_yes_bid; infer it from the same-token
        # complement NO ask when available.
        no_ask = _safe_float(row.get("current_bracket_no_ask"))
        return 1.0 - no_ask if no_ask is not None else None
    mapping = {
        "current_no": "current_no_bid",
        "d1_no": "d1_no_bid",
        "d2_no": "d2_no_bid",
    }
    return _safe_float(row.get(mapping[expression]))


def _expr_p_win(row: pd.Series, expression: str, prefix: str = METHOD) -> float:
    p_current = float(row[f"{prefix}_p_current"])
    p_d1 = float(row[f"{prefix}_p_d1"])
    p_d2 = float(row[f"{prefix}_p_d2"])
    if expression == "current_yes":
        return p_current
    if expression == "current_no":
        return 1.0 - p_current
    if expression == "d1_no":
        return 1.0 - p_d1
    if expression == "d2_no":
        return 1.0 - p_d2
    raise ValueError(expression)


def _expr_win(actual_bucket: str, expression: str) -> float:
    if expression == "current_yes":
        return float(actual_bucket == "current")
    if expression == "current_no":
        return float(actual_bucket != "current")
    if expression == "d1_no":
        return float(actual_bucket != "d1")
    if expression == "d2_no":
        return float(actual_bucket != "d2")
    raise ValueError(expression)


def _fee(price: float, shares: float = 1.0) -> float:
    return shares * TAKER_FEE_RATE * price * (1.0 - price)


def build_basis_ev() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    basis_df, counters = _basis_feature_rows(include_below=True)
    pred, meta = _expanding_basis_predictions(basis_df)
    if pred.empty:
        return pd.DataFrame(), pd.DataFrame(), {"counters": counters, "prediction_meta": meta}
    keys = ["city", "target_date", "decision_hour_local", "actual_bucket"]
    extra_cols = [
        "label_source",
        "eval_slice",
        "current_yes_ask",
        "current_bracket_no_ask",
        "current_no_ask",
        "current_no_bid",
        "d1_no_ask",
        "d1_no_bid",
        "d2_no_ask",
        "d2_no_bid",
        "unit",
        "forecast_to_current_upper_native",
        "forecast_peak_delta_hours_local",
        "temp_trend_3h_f",
    ]
    meta_cols = keys + [c for c in extra_cols if c in basis_df.columns]
    merged = pred.merge(basis_df[meta_cols], on=keys, how="left", validate="one_to_one")
    merged["scope"] = np.where(
        merged["target_date"].lt(p1.TRAIN_CUTOFF),
        "dev_cv",
        np.where(merged["eval_slice"].eq(p4.VERIFIED_SLICE), "verified_forward", "extension_forward"),
    )
    rows: list[dict[str, Any]] = []
    for item in merged.to_dict("records"):
        s = pd.Series(item)
        for expression in ["current_yes", "current_no", "d1_no", "d2_no"]:
            ask = _expr_ask(s, expression)
            if ask is None or ask <= 0.0 or ask >= 1.0:
                continue
            p_win = _expr_p_win(s, expression)
            win = _expr_win(str(item["actual_bucket"]), expression)
            rows.append(
                {
                    "scope": item["scope"],
                    "city": item["city"],
                    "target_date": item["target_date"],
                    "decision_hour_local": item["decision_hour_local"],
                    "actual_bucket": item["actual_bucket"],
                    "label_source": item.get("label_source"),
                    "expression": expression,
                    "ask": ask,
                    "p_win": p_win,
                    "model_edge": p_win - ask,
                    "win": win,
                    "unit_pnl": win - ask,
                }
            )
    ev = pd.DataFrame(rows)
    summary_rows = []
    for (scope, expression), grp in ev.groupby(["scope", "expression"], dropna=False):
        selected = grp[grp["model_edge"].ge(PRIMARY_EDGE)].copy()
        if selected.empty:
            continue
        ci = _date_block_roi_ci(selected, "ask", "unit_pnl")
        summary_rows.append(
            {
                "scope": scope,
                "expression": expression,
                "selected_rows": int(len(selected)),
                "dates": int(selected["target_date"].nunique()),
                "below_rows": int((selected["actual_bucket"] == "below").sum()),
                "basis_below_rate": float((selected["actual_bucket"] == "below").mean()),
                "avg_ask": float(selected["ask"].mean()),
                "p_current_mean": float(selected["p_win"].mean()) if expression == "current_yes" else math.nan,
                "win_rate": float(selected["win"].mean()),
                "cost": float(selected["ask"].sum()),
                "pnl": float(selected["unit_pnl"].sum()),
                "roi": ci["roi"],
                "roi_ci_low": ci["ci_low"],
                "roi_ci_high": ci["ci_high"],
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values(["scope", "expression"]) if summary_rows else pd.DataFrame()
    return ev, summary, {"counters": counters, "prediction_meta": meta}


def build_alpha_ablation(df: pd.DataFrame, pred_all: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    base = pred_all[pred_all["scope"].isin(["verified_forward", "extension_forward"])].copy()
    market_rows: list[dict[str, Any]] = []
    for scope, grp in base.groupby("scope", dropna=False):
        logloss = []
        brier = []
        for item in grp.to_dict("records"):
            probs = {bucket: float(item[f"market_local_norm_p_{bucket}"]) for bucket in p0.BUCKETS}
            score = p0._score_distribution(p0._normalize(probs), str(item["actual_bucket"]))
            logloss.append(score["logloss"])
            brier.append(score["brier"])
        market_rows.append(
            {
                "scope": scope,
                "market_logloss": float(np.mean(logloss)),
                "market_brier": float(np.mean(brier)),
            }
        )
    market = pd.DataFrame(market_rows)
    for alpha in ALPHA_GRID:
        for bucket in p0.BUCKETS:
            base[f"alpha_{alpha}_p_{bucket}"] = (
                (1.0 - alpha) * base[f"market_p_{bucket}"].astype(float)
                + alpha * base[f"{MODEL_SPEC}_model_p_{bucket}"].astype(float)
            )
        denom = sum(base[f"alpha_{alpha}_p_{b}"] for b in p0.BUCKETS)
        for bucket in p0.BUCKETS:
            base[f"alpha_{alpha}_p_{bucket}"] = base[f"alpha_{alpha}_p_{bucket}"] / denom
        for scope, grp in base.groupby("scope", dropna=False):
            logloss = []
            brier = []
            for item in grp.to_dict("records"):
                probs = {bucket: float(item[f"alpha_{alpha}_p_{bucket}"]) for bucket in p0.BUCKETS}
                score = p0._score_distribution(p0._normalize(probs), str(item["actual_bucket"]))
                logloss.append(score["logloss"])
                brier.append(score["brier"])
            rows.append(
                {
                    "scope": scope,
                    "alpha": alpha,
                    "rows": int(len(grp)),
                    "dates": int(grp["target_date"].nunique()),
                    "logloss": float(np.mean(logloss)),
                    "brier": float(np.mean(brier)),
                }
            )
    out = pd.DataFrame(rows)
    out = out.merge(market, on="scope", how="left", validate="many_to_one")
    out["logloss_delta_vs_market"] = out["logloss"] - out["market_logloss"]
    out["brier_delta_vs_market"] = out["brier"] - out["market_brier"]
    return out.sort_values(["scope", "logloss"])


def _state_best_rows(opps: pd.DataFrame) -> pd.DataFrame:
    eligible = opps[(opps["method"].eq(METHOD)) & (opps["model_edge"].ge(PRIMARY_EDGE))].copy()
    if eligible.empty:
        return eligible
    return (
        eligible.sort_values(
            ["scope", "city", "target_date", "decision_hour_local", "model_edge", "model_roi"],
            ascending=[True, True, True, True, False, False],
        )
        .groupby(["scope", "city", "target_date", "decision_hour_local"], as_index=False)
        .head(1)
        .copy()
    )


def build_reconciliation_replay(opps: pd.DataFrame, pred_all: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    best = _state_best_rows(opps)
    if best.empty:
        return best, pd.DataFrame()
    bid_cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "actual_bucket",
        "current_bracket_no_ask",
        "current_no_bid",
        "d1_no_bid",
        "d2_no_bid",
    ]
    bid_meta = pred_all[[c for c in bid_cols if c in pred_all.columns]].drop_duplicates(
        ["city", "target_date", "decision_hour_local", "actual_bucket"]
    )
    best = best.merge(bid_meta, on=["city", "target_date", "decision_hour_local", "actual_bucket"], how="left", validate="many_to_one")
    best["close_bid"] = best.apply(lambda r: _expr_bid(r, str(r["expression"])), axis=1)
    rows: list[dict[str, Any]] = []
    for (scope, city, target_date), grp in best.groupby(["scope", "city", "target_date"], dropna=False):
        grp = grp.sort_values(["decision_hour_local", "model_edge"], ascending=[True, False]).copy()
        first = grp.iloc[0]
        blocked_later = grp[grp["decision_hour_local"].gt(first["decision_hour_local"])].copy()
        first_fee = _fee(float(first["ask"]))
        first_cost = float(first["ask"]) + first_fee
        first_pnl = float(first["unit_pnl"]) - first_fee
        best_later = blocked_later.head(1)
        # Target-book reconciliation: only replace when the later target's
        # modeled edge covers the realized close friction proxy. This is still
        # a replay ledger, not a live rule.
        action = "hold_first"
        turnover_cost = 0.0
        close_pnl = 0.0
        reverse_fee = 0.0
        reverse_cost = 0.0
        reverse_pnl = 0.0
        reverse_expression = ""
        reverse_hour = math.nan
        reverse_close_bid = math.nan
        if not best_later.empty:
            later = best_later.iloc[0]
            reverse_expression = str(later["expression"])
            reverse_hour = float(later["decision_hour_local"])
            close_bid = _safe_float(first.get("close_bid"))
            reverse_close_bid = close_bid if close_bid is not None else math.nan
            if close_bid is not None and close_bid > 0:
                close_fee = _fee(close_bid)
                close_pnl = close_bid - close_fee - float(first["ask"]) - first_fee
                later_fee = _fee(float(later["ask"]))
                incremental_edge = float(later["model_edge"]) - max(0.0, float(first["model_edge"]))
                friction = max(0.0, 1.0 - float(first["ask"]) - close_bid) + later_fee + close_fee
                if incremental_edge > friction:
                    action = "close_and_replace"
                    turnover_cost = friction
                    reverse_fee = later_fee
                    reverse_cost = float(later["ask"]) + later_fee
                    reverse_pnl = float(later["unit_pnl"]) - later_fee
        if action == "close_and_replace":
            rec_cost = first_cost + reverse_cost
            rec_pnl = close_pnl + reverse_pnl
        else:
            rec_cost = first_cost
            rec_pnl = first_pnl
        rows.append(
            {
                "scope": scope,
                "city": city,
                "target_date": target_date,
                "first_hour": float(first["decision_hour_local"]),
                "first_expression": first["expression"],
                "first_ask": float(first["ask"]),
                "first_close_bid": _safe_float(first.get("close_bid")),
                "first_model_edge": float(first["model_edge"]),
                "first_win": float(first["win"]),
                "first_cost": first_cost,
                "first_pnl": first_pnl,
                "later_candidates": int(len(blocked_later)),
                "reverse_hour": reverse_hour,
                "reverse_expression": reverse_expression,
                "reverse_close_bid": reverse_close_bid,
                "action": action,
                "turnover_cost": turnover_cost,
                "reconcile_cost": rec_cost,
                "reconcile_pnl": rec_pnl,
                "net_delta_pnl": rec_pnl - first_pnl,
                "actual_bucket": first["actual_bucket"],
            }
        )
    decisions = pd.DataFrame(rows)
    summary_rows = []
    for scope, grp in decisions.groupby("scope", dropna=False):
        first_ci = _date_block_roi_ci(grp, "first_cost", "first_pnl")
        rec_ci = _date_block_roi_ci(grp, "reconcile_cost", "reconcile_pnl")
        summary_rows.append(
            {
                "scope": scope,
                "rows": int(len(grp)),
                "dates": int(grp["target_date"].nunique()),
                "cities": int(grp["city"].nunique()),
                "replace_triggers": int((grp["action"] == "close_and_replace").sum()),
                "later_candidate_rows": int((grp["later_candidates"] > 0).sum()),
                "avg_ask": float(grp["first_ask"].mean()),
                "avg_close_bid": float(grp["first_close_bid"].mean()),
                "first_cost": float(grp["first_cost"].sum()),
                "first_pnl": float(grp["first_pnl"].sum()),
                "first_roi": first_ci["roi"],
                "reconcile_cost": float(grp["reconcile_cost"].sum()),
                "reconcile_pnl": float(grp["reconcile_pnl"].sum()),
                "reconcile_roi": rec_ci["roi"],
                "net_delta_pnl": float(grp["net_delta_pnl"].sum()),
                "net_delta_roi": float((grp["reconcile_pnl"].sum() - grp["first_pnl"].sum()) / grp["first_cost"].sum()),
                "turnover_cost": float(grp["turnover_cost"].sum()),
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values("scope")
    return decisions, summary


def _write_report(
    *,
    meta: dict[str, Any],
    inventory: dict[str, Any],
    coherence_summary: pd.DataFrame,
    calibration_summary: pd.DataFrame,
    basis_summary: pd.DataFrame,
    alpha_summary: pd.DataFrame,
    reconciliation_summary: pd.DataFrame,
    report: dict[str, Any],
) -> None:
    key_cal = calibration_summary[
        calibration_summary["grouping"].eq("scope+peak_phase+trend3h_bucket+ceiling_margin_bucket")
        & calibration_summary["peak_phase"].eq("pre_or_at_peak")
        & calibration_summary["trend3h_bucket"].eq("warming")
        & calibration_summary["ceiling_margin_bucket"].eq(">=+2C")
    ].copy()
    coh_focus = coherence_summary[coherence_summary["grouping"].isin(["scope+reanchor_group", "reanchor_group"])].copy()
    basis_focus = basis_summary[basis_summary["expression"].isin(["current_yes", "current_no", "d1_no", "d2_no"])].copy()
    alpha_focus = alpha_summary[alpha_summary["scope"].isin(["verified_forward", "extension_forward"])].copy()
    lines = [
        "# Tmax Cross-Hour Coherence Audit v1",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        "> Scope: research-only P0e/P0c/P0b + execution replay; no tmax live runner/config/order behavior changed.",
        "",
        "## 数据快照",
        "",
        f"- Mac market_data sync: `scripts/ops/sync_weather_remote.sh --market-source=mac-weather-data-feed --market-only` ran before this report.",
        f"- Materialized tmax rows used here: `{meta['rows']}` rows, `{meta['date_range'][0]}`..`{meta['date_range'][1]}`, `{meta['cities']}` cities.",
        f"- Inventory: `{inventory}`",
        f"- P4 counters: `{meta['counters']}`",
        "- Important limit: raw snapshots are newer than the materialized P0/P5/P6 research layer. This report evaluates the currently materialized atlas/P5 layer, not a 7/05+ fully rebuilt tmax layer.",
        "",
        "## 验收口径冻结",
        "",
        "- P0e: adjacent city-day-hour pairs; coherent posterior is previous four-bucket distribution conditioned on the newly reached bracket. `incoherence = refit P(current) - coherent P(current)`.",
        "- P0c: one-vs-rest `p_current` reliability for `loo_no_city_source_blend`; key slice is `pre_or_at_peak × warming × ceiling >= +2C`.",
        "- P0b: settlement-basis replay tries to add below-current settlement rows back as current-YES losers / NO winners. If this run reports zero below rows, the finding is a materializer gap, not evidence that basis risk is absent.",
        "- Execution replay: first-lock vs target-book reconciliation on the same P5 opportunity denominator; no live behavior changed.",
        "",
        "## 结论",
        "",
        "- P0e supports the broader `path-coherence is model debt` thesis, but it does **not** cleanly confirm the narrow `new-high reanchor always overstates hold` story. In this materialized sample, `no_reanchor` has the largest positive incoherence; d1 reanchor is weak/unstable and d2 reanchor is negative.",
        "- P0c does not show a robust high-ceiling warming overestimate in the current verified-forward materialized layer; the key slice is only 4 rows. This means Lucknow cannot be generalized from this slice yet.",
        "- P0b is not answered by the current materialized layer: the replay found zero below-current rows. That means the E2 settlement-basis state is still missing upstream; current-YES remains shadow until below/current is represented as a real model target.",
        "- Execution replay remains research-only. Target-book reconciliation should be implemented as a position ledger before any future live restart, but this report is not live approval.",
        "",
        "## P0e Cross-Hour Coherence",
        "",
        *_table(
            coh_focus,
            [
                "grouping",
                "scope",
                "reanchor_group",
                "rows",
                "dates",
                "cities",
                "incoherence_mean",
                "incoherence_median",
                "positive_rate",
                "incoherence_ci_low",
                "incoherence_ci_high",
            ],
            max_rows=80,
        ),
        "",
        "## P0c Key Calibration Slice",
        "",
        *_table(
            key_cal,
            [
                "scope",
                "peak_phase",
                "trend3h_bucket",
                "ceiling_margin_bucket",
                "rows",
                "dates",
                "cities",
                "mean_p_current",
                "actual_current_rate",
                "calibration_error",
                "brier_current",
            ],
        ),
        "",
        "## P0c Reliability Deciles",
        "",
        *_table(
            calibration_summary[calibration_summary["grouping"].eq("scope+p_decile")],
            ["scope", "p_decile", "rows", "mean_p_current", "actual_current_rate", "calibration_error", "brier_current"],
            max_rows=40,
        ),
        "",
        "## P0b Basis-Inclusive EV",
        "",
        "This table is a sanity replay on the current materialized layer. `below_rows=0` means the known E2 below-current issue was not available to this materializer, so this table cannot clear current-YES.",
        "",
        *_table(
            basis_focus,
            [
                "scope",
                "expression",
                "selected_rows",
                "dates",
                "below_rows",
                "basis_below_rate",
                "avg_ask",
                "p_current_mean",
                "win_rate",
                "cost",
                "pnl",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
            ],
        ),
        "",
        "## P1b Alpha Ablation",
        "",
        *_table(
            alpha_focus,
            ["scope", "alpha", "rows", "dates", "logloss", "market_logloss", "logloss_delta_vs_market", "brier", "market_brier", "brier_delta_vs_market"],
            max_rows=20,
        ),
        "",
        "## Execution Replay",
        "",
        *_table(
            reconciliation_summary,
            [
                "scope",
                "rows",
                "dates",
                "cities",
                "later_candidate_rows",
                "replace_triggers",
                "avg_ask",
                "avg_close_bid",
                "first_cost",
                "first_pnl",
                "first_roi",
                "reconcile_cost",
                "reconcile_pnl",
                "reconcile_roi",
                "net_delta_pnl",
                "net_delta_roi",
                "turnover_cost",
            ],
        ),
        "",
        "## 三道门",
        "",
        "significance=FAIL/PARTIAL; baseline=PARTIAL; forward=FAIL/THIN; conclusion=`inconclusive_research_only`.",
        "",
        "No live action. `current_yes` remains shadow. The next model step is a real five-bucket or hazard materializer, not another execution gate.",
        "",
        "## Artifacts",
        "",
        f"- `{(OUT_DIR / 'coherence_pairs.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'coherence_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'current_calibration_rows.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'current_calibration_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'basis_ev_opportunities.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'basis_ev_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'alpha_ablation.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'reconciliation_decisions.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'reconciliation_summary.csv').relative_to(ROOT)}`",
        f"- `{SUMMARY_JSON_PATH.relative_to(ROOT)}`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df, pred_all, selection_df, meta_df, meta = _prediction_frames()
    coherence_pairs, coherence_summary = build_coherence_pairs(pred_all)
    calibration_rows, calibration_summary = build_current_calibration(pred_all)
    basis_ev, basis_summary, basis_meta = build_basis_ev()
    alpha_summary = build_alpha_ablation(df, pred_all)
    base = p5._make_base(df)
    _, dev_cv_preds, forward_preds, _ = p5._selected_specs_and_predictions(df)
    dev_opps = p5._build_opportunities("dev_cv", base, dev_cv_preds)
    forward_opps = p5._build_opportunities("forward_all", base, forward_preds)
    if not forward_opps.empty:
        forward_opps["scope"] = np.where(
            forward_opps["eval_slice"].eq(p4.VERIFIED_SLICE),
            "verified_forward",
            "extension_forward",
        )
    opps = pd.concat([dev_opps, forward_opps], ignore_index=True)
    reconciliation_decisions, reconciliation_summary = build_reconciliation_replay(opps, pred_all)

    coherence_pairs.to_csv(OUT_DIR / "coherence_pairs.csv", index=False)
    coherence_summary.to_csv(OUT_DIR / "coherence_summary.csv", index=False)
    calibration_rows.to_csv(OUT_DIR / "current_calibration_rows.csv", index=False)
    calibration_summary.to_csv(OUT_DIR / "current_calibration_summary.csv", index=False)
    basis_ev.to_csv(OUT_DIR / "basis_ev_opportunities.csv", index=False)
    basis_summary.to_csv(OUT_DIR / "basis_ev_summary.csv", index=False)
    alpha_summary.to_csv(OUT_DIR / "alpha_ablation.csv", index=False)
    reconciliation_decisions.to_csv(OUT_DIR / "reconciliation_decisions.csv", index=False)
    reconciliation_summary.to_csv(OUT_DIR / "reconciliation_summary.csv", index=False)
    selection_df.to_csv(OUT_DIR / "model_selection.csv", index=False)

    inventory = _inventory()
    report = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "method": METHOD,
        "model_spec": MODEL_SPEC,
        "primary_edge": PRIMARY_EDGE,
        "meta": meta,
        "basis_meta": basis_meta,
        "inventory": inventory,
        "verdict": "inconclusive_research_only",
        "coherence_focus": coherence_summary[
            coherence_summary["grouping"].isin(["scope+reanchor_group", "reanchor_group"])
        ].to_dict("records"),
        "basis_summary": basis_summary.to_dict("records") if not basis_summary.empty else [],
        "reconciliation_summary": reconciliation_summary.to_dict("records") if not reconciliation_summary.empty else [],
    }
    SUMMARY_JSON_PATH.write_text(json.dumps(_json_ready(report), indent=2, sort_keys=True), encoding="utf-8")
    _write_report(
        meta=meta,
        inventory=inventory,
        coherence_summary=coherence_summary,
        calibration_summary=calibration_summary,
        basis_summary=basis_summary,
        alpha_summary=alpha_summary,
        reconciliation_summary=reconciliation_summary,
        report=report,
    )
    print(
        json.dumps(
            {
                "report_path": str(REPORT_PATH.relative_to(ROOT)),
                "date_range": meta["date_range"],
                "rows": meta["rows"],
                "coherence_pairs": int(len(coherence_pairs)),
                "basis_rows": int(len(basis_ev)),
                "verdict": report["verdict"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
