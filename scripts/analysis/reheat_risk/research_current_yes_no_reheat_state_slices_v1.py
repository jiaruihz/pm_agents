#!/usr/bin/env python3
"""Research fixed no-reheat state slices for current-YES.

This script deliberately avoids training a new probability model.  It asks a
cleaner question: which observable no-reheat states, crossed with executable
YES ask buckets, have positive market-lag expectancy?
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/current_yes_future_break_hazard_v3/factory/reheat_feature_rows.csv"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_no_reheat_state_slices_v1"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-23-current-yes-no-reheat-state-slices-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-23-current-yes-no-reheat-state-slices-v1.md"

SEED = 20260623
TRAIN_END = "2026-05-31"
HOLDOUT_START = "2026-06-01"
FORWARD_TAIL_START = "2026-06-18"

PRICE_BUCKETS: list[tuple[str, float, float]] = [
    ("all_ask_35_97", 0.35, 0.97),
    ("ask_35_50", 0.35, 0.50),
    ("ask_50_60", 0.50, 0.60),
    ("ask_60_70", 0.60, 0.70),
    ("ask_70_80", 0.70, 0.80),
    ("ask_80_90", 0.80, 0.90),
    ("ask_90_97", 0.90, 0.97),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--feature-rows", default=str(FEATURE_ROWS))
    parser.add_argument("--gate-json", default=str(GATE))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        out = float(value)
        return None if not math.isfinite(out) else out
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f * 100:+.{digits}f}%"


def num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f:.{digits}f}"


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def query_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def data_self_check(db_path: Path, gate_path: Path) -> dict[str, Any]:
    conn = connect_ro(db_path)
    try:
        out = {
            "fact_trades_freshness": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, MAX(order_ts_utc) AS max_order_ts_utc, "
                "MAX(fill_ts_utc) AS max_fill_ts_utc, MAX(target_date) AS max_target_date, "
                "MAX(fact_built_at_utc) AS max_fact_built_at_utc FROM fact_trades",
            )[0],
            "fact_trades_by_class": query_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY rows DESC",
            ),
            "fact_trades_by_settlement": query_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY COALESCE(settlement_status, '') ORDER BY rows DESC",
            ),
            "fact_signal_candidates": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, MIN(event_date) AS min_event_date, MAX(event_date) AS max_event_date, "
                "MAX(fact_built_at_utc) AS max_fact_built_at_utc FROM fact_signal_candidates",
            )[0],
            "settlement_outcomes": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, MIN(target_date) AS min_target_date, MAX(target_date) AS max_target_date "
                "FROM settlement_outcomes",
            )[0],
            "orders_fills": query_rows(
                conn,
                "SELECT COUNT(*) AS fact_rows, COUNT(DISTINCT order_id) AS distinct_orders, "
                "SUM(CASE WHEN cost_usd > 0 THEN 1 ELSE 0 END) AS positive_cost_rows FROM fact_trades",
            )[0],
        }
    finally:
        conn.close()

    if gate_path.exists():
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        out["clob_fill_coverage_gate"] = {
            "gate_pass": gate.get("gate_pass"),
            "fail_reasons": gate.get("fail_reasons", []),
            "db_fill_cost_minus_fact_cost": gate.get("db_fill_cost_minus_fact_cost"),
        }
    else:
        out["clob_fill_coverage_gate"] = {"gate_pass": None, "missing": True}
    return out


def boolish(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    out = numeric.fillna(0).astype(float) > 0
    text_mask = numeric.isna()
    if text_mask.any():
        out.loc[text_mask] = series.loc[text_mask].astype(str).str.lower().isin({"true", "1", "1.0", "yes"})
    return out


def local_hour_float(ts: pd.Timestamp, timezone_name: str) -> float:
    if pd.isna(ts):
        return np.nan
    try:
        from zoneinfo import ZoneInfo

        local = pd.Timestamp(ts).floor("s").to_pydatetime().astimezone(ZoneInfo(str(timezone_name)))
    except Exception:
        return np.nan
    return local.hour + local.minute / 60.0 + local.second / 3600.0


def add_plateau_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["decision_snapshot_dt"] = pd.to_datetime(out["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    out["decision_last_obs_dt"] = pd.to_datetime(out["decision_last_obs_utc"], utc=True, errors="coerce")
    out["minutes_since_running_max_num"] = pd.to_numeric(out["minutes_since_running_max"], errors="coerce")
    first_max_dt = out["decision_last_obs_dt"] - pd.to_timedelta(out["minutes_since_running_max_num"], unit="m")
    out["running_max_first_obs_hour_local"] = [
        local_hour_float(ts, tz) for ts, tz in zip(first_max_dt, out["timezone"], strict=False)
    ]
    out["decision_minus_running_max_hour"] = (
        pd.to_numeric(out["decision_hour_local"], errors="coerce")
        - pd.to_numeric(out["running_max_first_obs_hour_local"], errors="coerce")
    )

    key_cols = ["city", "target_date", "running_value", "decision_last_obs_utc"]
    obs = (
        out.sort_values(["city", "target_date", "running_value", "decision_last_obs_dt", "decision_snapshot_dt"])
        .drop_duplicates(key_cols, keep="last")
        .copy()
    )
    obs["_at_high"] = pd.to_numeric(obs["decline_native"], errors="coerce").le(0.25)
    plateau_count: dict[int, int] = {}
    plateau_duration: dict[int, float] = {}
    for _key, group in obs.groupby(["city", "target_date", "running_value"], sort=False):
        run_count = 0
        streak_start: pd.Timestamp | None = None
        for idx, row in group.sort_values("decision_last_obs_dt").iterrows():
            obs_dt = row["decision_last_obs_dt"]
            if bool(row["_at_high"]):
                run_count += 1
                if streak_start is None:
                    streak_start = obs_dt
                duration = np.nan if pd.isna(obs_dt) or pd.isna(streak_start) else max(0.0, (obs_dt - streak_start).total_seconds() / 60.0)
            else:
                run_count = 0
                streak_start = None
                duration = 0.0
            plateau_count[idx] = run_count
            plateau_duration[idx] = duration
    obs["plateau_obs_count_at_high"] = pd.Series(plateau_count)
    obs["plateau_duration_min"] = pd.Series(plateau_duration)
    out = out.merge(obs[key_cols + ["plateau_obs_count_at_high", "plateau_duration_min"]], on=key_cols, how="left")
    out["plateau_obs_count_at_high"] = pd.to_numeric(out["plateau_obs_count_at_high"], errors="coerce").fillna(0)
    out["plateau_duration_min"] = pd.to_numeric(out["plateau_duration_min"], errors="coerce").fillna(0.0)
    return out


def load_current_yes_rows(feature_path: Path) -> pd.DataFrame:
    df = pd.read_csv(feature_path)
    df = df[(df["outcome"].astype(str).str.lower() == "yes") & (df["bracket"].astype(str) == df["current_bracket"].astype(str))]
    df = df.drop_duplicates(["city", "target_date", "decision_snapshot_ts_utc", "current_bracket"]).copy()
    df = df[df["current_yes_ask"].notna() & df["current_bracket_held"].notna()].copy()
    df["target_date"] = df["target_date"].astype(str)
    df["label_survive"] = boolish(df["current_bracket_held"]).astype(int)
    df["label_future_break"] = 1 - df["label_survive"]
    df["period"] = np.where(df["target_date"].le(TRAIN_END), "train", "holdout")
    df["forward_tail"] = df["target_date"].ge(FORWARD_TAIL_START)
    df["decision_obs_age_min"] = (
        pd.to_datetime(df["decision_snapshot_ts_utc"], utc=True, errors="coerce")
        - pd.to_datetime(df["decision_last_obs_utc"], utc=True, errors="coerce")
    ).dt.total_seconds() / 60.0
    for col in [
        "decision_hour_local",
        "current_yes_ask",
        "current_yes_ask_size",
        "decline_native",
        "minutes_since_running_max",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "gfs_forecast_gap_to_running_native",
        "ecmwf_forecast_gap_to_running_native",
        "forecast_peak_hour_spread",
        "forecast_peak_models_agree_le_1h",
        "current_yes_spread",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["min_forecast_peak_delta_hours_local"] = df[
        ["gfs_forecast_peak_delta_hours_local", "ecmwf_forecast_peak_delta_hours_local"]
    ].min(axis=1)
    df["max_forecast_peak_delta_hours_local"] = df[
        ["gfs_forecast_peak_delta_hours_local", "ecmwf_forecast_peak_delta_hours_local"]
    ].max(axis=1)
    df["min_forecast_gap_to_running_native"] = df[
        ["gfs_forecast_gap_to_running_native", "ecmwf_forecast_gap_to_running_native"]
    ].min(axis=1)
    df["max_forecast_gap_to_running_native"] = df[
        ["gfs_forecast_gap_to_running_native", "ecmwf_forecast_gap_to_running_native"]
    ].max(axis=1)
    df = add_plateau_features(df)
    df["trade_pnl_per_share"] = df["label_survive"] - df["current_yes_ask"]
    return df


def date_bootstrap_roi(frame: pd.DataFrame, reps: int = 4000) -> list[float | None]:
    by_date = frame.groupby("target_date")[["current_yes_ask", "trade_pnl_per_share"]].sum()
    if by_date.shape[0] < 2:
        return [None, None]
    rng = np.random.default_rng(SEED)
    vals = by_date.to_numpy(dtype=float)
    out = []
    for _ in range(reps):
        sample = vals[rng.integers(0, len(vals), size=len(vals))]
        cost = float(sample[:, 0].sum())
        out.append(float(sample[:, 1].sum() / cost) if cost else np.nan)
    arr = np.asarray(out)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return [None, None]
    return [float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))]


def date_bootstrap_excess(state: pd.DataFrame, baseline: pd.DataFrame, reps: int = 4000) -> list[float | None]:
    if state["target_date"].nunique() < 2 or baseline["target_date"].nunique() < 2:
        return [None, None]
    state_by_date = state.groupby("target_date")[["current_yes_ask", "trade_pnl_per_share"]].sum()
    base_by_date = baseline.groupby("target_date")[["current_yes_ask", "trade_pnl_per_share"]].sum()
    dates = sorted(set(state_by_date.index) | set(base_by_date.index))
    s = state_by_date.reindex(dates, fill_value=0.0).to_numpy(dtype=float)
    b = base_by_date.reindex(dates, fill_value=0.0).to_numpy(dtype=float)
    rng = np.random.default_rng(SEED + 17)
    out = []
    for _ in range(reps):
        idx = rng.integers(0, len(dates), size=len(dates))
        s_sample = s[idx]
        b_sample = b[idx]
        s_cost = float(s_sample[:, 0].sum())
        b_cost = float(b_sample[:, 0].sum())
        if s_cost <= 0 or b_cost <= 0:
            continue
        out.append(float(s_sample[:, 1].sum() / s_cost - b_sample[:, 1].sum() / b_cost))
    arr = np.asarray(out)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return [None, None]
    return [float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))]


def summarize(name: str, bucket: str, frame: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "state": name,
            "price_bucket": bucket,
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "roi_ci95": [None, None],
            "baseline_roi": None,
            "excess_roi": None,
            "excess_roi_ci95": [None, None],
            "win_rate": None,
            "future_break_rate": None,
            "avg_ask": None,
            "avg_minutes_since_max": None,
            "avg_plateau_obs": None,
            "avg_plateau_duration": None,
        }
    cost = float(frame["current_yes_ask"].sum())
    pnl = float(frame["trade_pnl_per_share"].sum())
    b_cost = float(baseline["current_yes_ask"].sum()) if len(baseline) else 0.0
    b_pnl = float(baseline["trade_pnl_per_share"].sum()) if len(baseline) else 0.0
    roi = pnl / cost if cost else None
    b_roi = b_pnl / b_cost if b_cost else None
    return {
        "state": name,
        "price_bucket": bucket,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "cost": cost,
        "pnl": pnl,
        "roi": roi,
        "roi_ci95": date_bootstrap_roi(frame),
        "baseline_rows": int(len(baseline)),
        "baseline_dates": int(baseline["target_date"].nunique()) if len(baseline) else 0,
        "baseline_roi": b_roi,
        "excess_roi": roi - b_roi if roi is not None and b_roi is not None else None,
        "excess_roi_ci95": date_bootstrap_excess(frame, baseline) if len(baseline) else [None, None],
        "win_rate": float(frame["label_survive"].mean()),
        "future_break_rate": float(frame["label_future_break"].mean()),
        "avg_ask": float(frame["current_yes_ask"].mean()),
        "avg_minutes_since_max": float(pd.to_numeric(frame["minutes_since_running_max"], errors="coerce").mean()),
        "avg_plateau_obs": float(pd.to_numeric(frame["plateau_obs_count_at_high"], errors="coerce").mean()),
        "avg_plateau_duration": float(pd.to_numeric(frame["plateau_duration_min"], errors="coerce").mean()),
        "forward_tail_rows": int(frame["forward_tail"].sum()),
    }


def state_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    hour = pd.to_numeric(df["decision_hour_local"], errors="coerce")
    decline = pd.to_numeric(df["decline_native"], errors="coerce")
    minutes = pd.to_numeric(df["minutes_since_running_max"], errors="coerce")
    plateau_obs = pd.to_numeric(df["plateau_obs_count_at_high"], errors="coerce")
    plateau_dur = pd.to_numeric(df["plateau_duration_min"], errors="coerce")
    trend3 = pd.to_numeric(df["temp_trend_3h_f"], errors="coerce")
    min_peak_delta = pd.to_numeric(df["min_forecast_peak_delta_hours_local"], errors="coerce")
    max_gap = pd.to_numeric(df["max_forecast_gap_to_running_native"], errors="coerce")

    at_high = decline.le(0.25)
    fade = decline.ge(0.5)
    stalled_ge2 = at_high & plateau_obs.ge(2)
    stalled_ge30 = at_high & plateau_dur.ge(30)
    after_peak = min_peak_delta.le(0)
    no_forecast_gap = max_gap.le(0.5)
    no_warming = trend3.le(2.0)
    late = hour.ge(15)

    return {
        "all_current_yes_tradable": pd.Series(True, index=df.index),
        "fresh_high_unconfirmed": at_high & (plateau_obs.le(1) | minutes.lt(30)),
        "stalled_high_ge2obs": stalled_ge2,
        "stalled_high_ge30m": stalled_ge30,
        "after_peak_stalled": stalled_ge30 & after_peak,
        "late_after_peak_stalled": stalled_ge30 & after_peak & late,
        "stalled_no_warming": stalled_ge2 & no_warming,
        "strict_no_reheat_candidate": stalled_ge2 & stalled_ge30 & after_peak & no_warming & no_forecast_gap & hour.ge(14),
        "fade_all_decline_ge_0_5": fade,
        "mature_fade_candidate": fade & minutes.ge(90) & late & after_peak & no_warming & no_forecast_gap,
        "early_false_fade_risk": fade & hour.le(14) & (min_peak_delta.gt(0) | max_gap.gt(0.5)),
    }


def bucket_mask(df: pd.DataFrame, low: float, high: float) -> pd.Series:
    ask = pd.to_numeric(df["current_yes_ask"], errors="coerce")
    return ask.ge(low) & ask.lt(high)


def build_report(payload: dict[str, Any], out_md: Path) -> None:
    best = payload["best_holdout_slices"]
    all_rows = payload["rows"]
    lines = [
        "# Current-YES No-Reheat State Slices v1",
        "",
        "Status: research-only",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: fixed `no_reheat_state_slice_v1` asks whether observable current-YES states, not a trained probability model, identify market-lag YES opportunities.",
        "",
        "## 一句话结论",
        "",
        payload["headline"],
        "",
        "## 数据快照",
        "",
        f"- Feature rows: `{payload['inputs']['feature_rows']}`.",
        f"- Feature target-date range: `{payload['data_snapshot']['feature_min_target_date']}`..`{payload['data_snapshot']['feature_max_target_date']}`.",
        f"- Current-YES rows: {payload['data_snapshot']['current_yes_rows']} rows / {payload['data_snapshot']['current_yes_dates']} dates / {payload['data_snapshot']['current_yes_cities']} cities.",
        f"- Holdout window: `{HOLDOUT_START}`..`{payload['data_snapshot']['feature_max_target_date']}`.",
        f"- CLOB fill coverage gate: `{payload['data_self_check'].get('clob_fill_coverage_gate', {}).get('gate_pass')}`.",
        "",
        "注意：这是 opportunity/orderbook replay + settlement label，不是 live_real PnL。它用于找状态切片，不改变 live。",
        "",
        "## 数据完整性自检",
        "",
        "```json",
        json.dumps(payload["data_self_check"], indent=2, ensure_ascii=False),
        "```",
        "",
        "## Best Holdout Slices",
        "",
        "| state | ask bucket | rows | dates | win | ROI | CI | baseline ROI | excess | excess CI | avg ask |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in best[:12]:
        lines.append(
            "| {state} | {bucket} | {rows} | {dates} | {win} | {roi} | {ci} | {base} | {excess} | {excess_ci} | {ask} |".format(
                state=row["state"],
                bucket=row["price_bucket"],
                rows=row["rows"],
                dates=row["dates"],
                win=pct(row["win_rate"]),
                roi=pct(row["roi"]),
                ci=f"[{pct(row['roi_ci95'][0])}, {pct(row['roi_ci95'][1])}]",
                base=pct(row["baseline_roi"]),
                excess=pct(row["excess_roi"]),
                excess_ci=f"[{pct(row['excess_roi_ci95'][0])}, {pct(row['excess_roi_ci95'][1])}]",
                ask=num(row["avg_ask"]),
            )
        )
    lines.extend(
        [
            "",
            "筛选说明：表里只展示 holdout 中 `rows>=30`、`dates>=8` 的状态×价格桶，按 ROI 和 excess 排序。baseline 是同一 ask bucket 的全部 current-YES tradable 行。",
            "",
            "## State Summary",
            "",
            "| state | rows | dates | win | ROI | CI | baseline ROI | excess | future break | avg ask |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in [r for r in all_rows if r["period"] == "holdout" and r["price_bucket"] == "all_ask_35_97"]:
        lines.append(
            "| {state} | {rows} | {dates} | {win} | {roi} | {ci} | {base} | {excess} | {break_rate} | {ask} |".format(
                state=row["state"],
                rows=row["rows"],
                dates=row["dates"],
                win=pct(row["win_rate"]),
                roi=pct(row["roi"]),
                ci=f"[{pct(row['roi_ci95'][0])}, {pct(row['roi_ci95'][1])}]",
                base=pct(row["baseline_roi"]),
                excess=pct(row["excess_roi"]),
                break_rate=pct(row["future_break_rate"]),
                ask=num(row["avg_ask"]),
            )
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"significance={payload['verdict']['significance']} / baseline={payload['verdict']['baseline']} / forward={payload['verdict']['forward']} / conclusion={payload['verdict']['conclusion']}",
            "",
            payload["verdict"]["text"],
            "",
            "## 8-Ring Coverage",
            "",
            "- Covered: fixed state slicing, price buckets, same-price baseline, date-cluster bootstrap, future-break labels, target-date correlation.",
            "- Not covered enough for live: real forward shadow fills, maker/taker execution, capacity beyond top ask, full settled labels after the feature layer max date.",
            "",
            "## Outputs",
            "",
            f"- slice summary CSV: `{payload['outputs']['slice_summary_csv']}`",
            f"- state rows CSV: `{payload['outputs']['state_rows_csv']}`",
            f"- json: `{payload['outputs']['json']}`",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    feature_path = Path(args.feature_rows)
    gate_path = Path(args.gate_json)
    out_dir = Path(args.out_dir)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_dir.mkdir(parents=True, exist_ok=True)

    self_check = data_self_check(db_path, gate_path)
    rows = load_current_yes_rows(feature_path)
    tradable = rows[
        rows["decision_hour_local"].between(10, 21)
        & rows["current_yes_ask"].between(0.35, 0.97, inclusive="left")
        & rows["current_yes_ask_size"].fillna(0).ge(5.0)
    ].copy()
    masks = state_masks(tradable)
    summary_rows: list[dict[str, Any]] = []
    state_row_parts: list[pd.DataFrame] = []
    for period in ["all", "train", "holdout"]:
        period_frame = tradable if period == "all" else tradable[tradable["period"] == period]
        period_masks = state_masks(period_frame)
        for bucket, low, high in PRICE_BUCKETS:
            baseline = period_frame[bucket_mask(period_frame, low, high)]
            for state, mask in period_masks.items():
                sub = period_frame[mask & bucket_mask(period_frame, low, high)].copy()
                item = summarize(state, bucket, sub, baseline)
                item["period"] = period
                item["price_low"] = low
                item["price_high"] = high
                summary_rows.append(item)
                if period == "holdout" and bucket == "all_ask_35_97" and not sub.empty:
                    state_row_parts.append(
                        sub.assign(no_reheat_state=state)[
                            [
                                "no_reheat_state",
                                "city",
                                "target_date",
                                "decision_snapshot_ts_utc",
                                "decision_hour_local",
                                "current_bracket",
                                "current_yes_ask",
                                "label_survive",
                                "label_future_break",
                                "decline_native",
                                "minutes_since_running_max",
                                "plateau_obs_count_at_high",
                                "plateau_duration_min",
                                "temp_trend_3h_f",
                                "min_forecast_peak_delta_hours_local",
                                "max_forecast_gap_to_running_native",
                            ]
                        ]
                    )

    summary = pd.DataFrame(summary_rows)
    summary_csv = out_dir / "no_reheat_state_slice_summary.csv"
    state_rows_csv = out_dir / "holdout_state_rows.csv"
    summary.to_csv(summary_csv, index=False)
    pd.concat(state_row_parts, ignore_index=True).to_csv(state_rows_csv, index=False) if state_row_parts else pd.DataFrame().to_csv(state_rows_csv, index=False)

    holdout_candidates = summary[
        (summary["period"] == "holdout")
        & (summary["rows"] >= 30)
        & (summary["dates"] >= 8)
        & summary["roi"].notna()
    ].copy()
    holdout_candidates["roi_sort"] = pd.to_numeric(holdout_candidates["roi"], errors="coerce")
    holdout_candidates["excess_sort"] = pd.to_numeric(holdout_candidates["excess_roi"], errors="coerce")
    holdout_candidates = holdout_candidates.sort_values(["roi_sort", "excess_sort", "rows"], ascending=False)
    best = holdout_candidates.head(20).drop(columns=["roi_sort", "excess_sort"]).to_dict("records")

    confirmed = holdout_candidates[
        holdout_candidates["roi_ci95"].apply(lambda x: isinstance(x, list) and x[0] is not None and x[0] > 0)
        & holdout_candidates["excess_roi_ci95"].apply(lambda x: isinstance(x, list) and x[0] is not None and x[0] > 0)
    ]
    if confirmed.empty:
        verdict = {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "NA",
            "conclusion": "inconclusive",
            "text": (
                "固定 no-reheat 状态切片在 holdout 上没有同时通过 ROI CI 和 same-price baseline excess CI。"
                "当前可以用这些状态做 telemetry/shadow 分层，但不能证明某个封顶切片已经可 live。"
            ),
        }
        headline = "没有找到可直接 promotion 的 market-lag no-reheat YES 切片；最好的点估计集中在低/中 ask 的 stalled/fade 状态，但 CI 或 baseline excess 不过门。"
    else:
        top = confirmed.iloc[0].to_dict()
        verdict = {
            "significance": "PASS",
            "baseline": "PASS",
            "forward": "NA",
            "conclusion": "shadow_candidate",
            "text": (
                f"固定切片 `{top['state']}` / `{top['price_bucket']}` 在 holdout 上 ROI {pct(top['roi'])}，"
                f"excess {pct(top['excess_roi'])}，但仍需要 forward shadow 才能讨论 live。"
            ),
        }
        headline = f"找到 shadow_candidate：`{top['state']}` / `{top['price_bucket']}`，但还缺 forward live-equivalent 验证。"

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "no_reheat_state_slice_v1",
        "inputs": {
            "feature_rows": str(feature_path.relative_to(ROOT)) if feature_path.is_absolute() else str(feature_path),
            "db": str(db_path.relative_to(ROOT)) if db_path.is_absolute() else str(db_path),
        },
        "data_self_check": self_check,
        "data_snapshot": {
            "feature_min_target_date": str(rows["target_date"].min()) if len(rows) else None,
            "feature_max_target_date": str(rows["target_date"].max()) if len(rows) else None,
            "current_yes_rows": int(len(rows)),
            "current_yes_dates": int(rows["target_date"].nunique()),
            "current_yes_cities": int(rows["city"].nunique()),
            "tradable_rows": int(len(tradable)),
            "tradable_dates": int(tradable["target_date"].nunique()),
            "tradable_cities": int(tradable["city"].nunique()),
        },
        "headline": headline,
        "verdict": verdict,
        "best_holdout_slices": json_ready(best),
        "rows": json_ready(summary_rows),
        "outputs": {
            "slice_summary_csv": str(summary_csv.relative_to(ROOT)),
            "state_rows_csv": str(state_rows_csv.relative_to(ROOT)),
            "json": str(out_json.relative_to(ROOT)),
            "markdown": str(out_md.relative_to(ROOT)),
        },
    }
    out_json.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    build_report(json_ready(payload), out_md)
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "best": payload["best_holdout_slices"][:5]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
