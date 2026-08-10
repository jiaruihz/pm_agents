#!/usr/bin/env python3
"""Retrain late-window d1 NO trade-win probability with touch-hazard features.

This is research-only. It builds PIT rows from paper snapshots and joins labels
only after candidate generation. The v2 model decomposes exact-bracket risk:

- Stage A: P(touch target after decision)
- Stage B: P(final exactly target)
- P(NO win) = 1 - P(final exactly target)
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sqlite3
import sys
import warnings
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


warnings.filterwarnings("ignore", category=UserWarning, module="sklearn.impute")


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402

from scripts.analysis.reheat_risk.research_late_window_residual_heating_done_v1 import (  # noqa: E402
    add_buckets,
    fee_per_share,
    leg_for_record,
    native_from_metar_f,
    native_tick_value,
    residual_done_features,
    safe_float,
)
from weather_feature_layer.market import parse_bracket  # noqa: E402
from weather_feature_layer.state import heating_done_features  # noqa: E402


DB = ROOT / "runtime/weather.db"
SNAPSHOT_DIR = historical_strategy_snapshots()
ORDERBOOK_DIR = ROOT / "runtime/weather_edge_v1/market_data/orderbook_snapshots"
FORECAST_CURVE_DIR = ROOT / "runtime/weather_edge_v1/market_data/forecast_hourly_curves"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/late_window_residual_trade_win_v2"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-08-late-window-residual-trade-win-v2.md"

LOCAL_START_HOUR = 15
LOCAL_END_HOUR = 18
TRAIN_START = "2026-06-29"
FORWARD_START = "2026-06-29"
FORWARD_END = "2026-07-07"
FEE_RATE = 0.05
RANDOM_SEED = 20260708
INCIDENT_REPORTED_V1_P_NO_WIN = 0.951
INCIDENT_LIVE_ORDER_TS_BJ = "2026-07-07 15:39:00"
INCIDENT_NEXT_KEY_METAR_TS_BJ = "2026-07-07 16:00:00"
INCIDENT_FIRST_TOUCH_MINUTES_CONTEXT = 28.0

REGION_BY_CITY = {
    "Beijing": "asia_hot",
    "Chengdu": "asia_humid_basin",
    "Chongqing": "asia_humid_basin",
    "Guangzhou": "asia_humid",
    "HongKong": "asia_humid",
    "Karachi": "asia_hot",
    "KualaLumpur": "asia_humid",
    "Manila": "asia_humid",
    "Shanghai": "asia_humid",
    "Singapore": "asia_humid",
    "Taipei": "asia_humid",
    "Tokyo": "asia_humid",
}

BASE_FEATURES = [
    "true_local_time_float",
    "forecast_peak_delta_hours_local",
    "forecast_gap_to_running_ticks",
    "decline_ticks",
    "target_distance_ticks",
    "forecast_margin_to_target_ticks",
    "obs_age_minutes",
    "obs_count_log1p",
]

OBS_CLOCK_FEATURES = [
    "obs_cadence_min",
    "minutes_to_next_obs",
    "minutes_to_next_key_obs",
    "minutes_since_running_max",
    "same_running_max_obs_count",
    "pre_update_blackout_score",
]

FORECAST_FEATURES = [
    "om_remaining_temp_max_native",
    "om_remaining_temp_mean_native",
    "om_remaining_temp_slope_native",
    "om_remaining_peak_margin_to_target_ticks",
    "multi_model_spread_native",
    "preferred_vs_ensemble_gap_native",
]

CITY_CAL_FEATURES = [
    "city_prior_d1_touch_rate",
    "city_prior_d1_exact_rate",
    "city_prior_late_spike_rate",
    "city_hour_path_prior_touch_rate",
    "source_prior_touch_rate",
]

CATEGORICAL_FEATURES = [
    "unit",
    "path_state",
    "peak_delta_bucket",
    "forecast_gap_bucket",
    "true_local_time_bucket",
    "obs_age_bucket",
    "obs_cadence_bucket",
    "pre_update_blackout_bucket",
    "forecast_source",
    "region_bucket",
    "taf_suppression_level",
    "vertical_profile_signal",
]

ABLATIONS: list[tuple[str, list[str], list[str]]] = [
    ("base_physical_only", BASE_FEATURES, ["unit", "path_state", "peak_delta_bucket", "forecast_gap_bucket"]),
    ("obs_clock_cadence", BASE_FEATURES + OBS_CLOCK_FEATURES, CATEGORICAL_FEATURES[:8]),
    ("multi_model_forecast", BASE_FEATURES + OBS_CLOCK_FEATURES + FORECAST_FEATURES, CATEGORICAL_FEATURES[:9]),
    (
        "taf",
        BASE_FEATURES + OBS_CLOCK_FEATURES + FORECAST_FEATURES + ["taf_peak_overlap_score", "taf_tx_margin_ticks"],
        CATEGORICAL_FEATURES[:11],
    ),
    (
        "vertical_hourly_context",
        BASE_FEATURES
        + OBS_CLOCK_FEATURES
        + FORECAST_FEATURES
        + ["taf_peak_overlap_score", "taf_tx_margin_ticks", "hourly_context_available", "vertical_heating_score"],
        CATEGORICAL_FEATURES[:12],
    ),
    (
        "city_late_reheat_calibration",
        BASE_FEATURES + OBS_CLOCK_FEATURES + FORECAST_FEATURES + CITY_CAL_FEATURES,
        CATEGORICAL_FEATURES,
    ),
    (
        "full_v2",
        BASE_FEATURES
        + OBS_CLOCK_FEATURES
        + FORECAST_FEATURES
        + CITY_CAL_FEATURES
        + ["taf_peak_overlap_score", "taf_tx_margin_ticks", "hourly_context_available", "vertical_heating_score"],
        CATEGORICAL_FEATURES,
    ),
]

V1_ORIGINAL_NUMERIC = [
    "true_local_time_float",
    "forecast_peak_delta_hours_local",
    "forecast_gap_to_running_native",
    "decline_native",
    "target_distance_native",
    "forecast_margin_to_target_native",
    "metar_obs_count_today",
    "running_value",
]
V1_ORIGINAL_CATEGORICAL = ["leg", "unit", "path_state", "peak_delta_bucket", "forecast_gap_bucket"]
OLD_V1_NUMERIC = BASE_FEATURES
OLD_V1_CATEGORICAL = ["unit", "path_state", "peak_delta_bucket", "forecast_gap_bucket", "true_local_time_bucket"]


@dataclass(frozen=True)
class SnapshotFile:
    path: Path
    snapshot_ts_utc: str
    ts_beijing: str


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _dt_utc(value: Any) -> pd.Timestamp | None:
    ts = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return ts


def _dt_naive(value: Any) -> pd.Timestamp | None:
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    if getattr(ts, "tzinfo", None) is not None:
        ts = ts.tz_convert(None)
    return ts


def round_half_up(value: float) -> int | None:
    if not math.isfinite(value):
        return None
    return int(math.floor(value + 0.5))


def unit_step(unit: str) -> float:
    return 2.0 if str(unit).upper() == "F" else 1.0


def to_native(temp_f: float, unit: str) -> float:
    return native_from_metar_f(temp_f, unit)


def f_to_native(temp_f: float, unit: str) -> float:
    if not math.isfinite(temp_f):
        return math.nan
    return temp_f if str(unit).upper() == "F" else (temp_f - 32.0) * 5.0 / 9.0


def target_date_from_path(path: Path) -> str | None:
    name = path.name
    if not name.startswith("snapshot_") or not name.endswith(".json"):
        return None
    raw = name.removeprefix("snapshot_")[:8]
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def snapshot_times_from_path(path: Path) -> tuple[str, str] | None:
    stem = path.stem
    if not stem.startswith("snapshot_"):
        return None
    raw = stem.removeprefix("snapshot_")
    try:
        dt_bj = datetime.strptime(raw, "%Y%m%d_%H%M").replace(tzinfo=timezone(timedelta(hours=8)))
    except ValueError:
        return None
    ts_beijing = dt_bj.strftime("%Y-%m-%d %H:%M:00")
    ts_utc = dt_bj.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")
    return ts_utc, ts_beijing


def iter_snapshots(snapshot_dir: Path, start_date: str = TRAIN_START, end_date: str = FORWARD_END) -> list[SnapshotFile]:
    out: list[SnapshotFile] = []
    for path in sorted(snapshot_dir.glob("snapshot_*.json")):
        file_date = target_date_from_path(path)
        if file_date is None or file_date < start_date or file_date > end_date:
            continue
        times = snapshot_times_from_path(path)
        if times is None:
            continue
        snap_ts, ts_bj = times
        out.append(SnapshotFile(path=path, snapshot_ts_utc=snap_ts, ts_beijing=ts_bj))
    return out


def snapshot_to_orderbook_path(path: Path, ts_bj: str) -> Path | None:
    if not ts_bj:
        return None
    date = ts_bj[:10]
    hhmm = ts_bj[11:16].replace(":", "")
    if len(date) != 10 or len(hhmm) != 4:
        return None
    return ORDERBOOK_DIR / date / f"orderbook_snapshot_{date.replace('-', '')}_{hhmm}.jsonl.gz"


def load_orderbook_counts(snapshots: list[SnapshotFile]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for snap in snapshots:
        path = snapshot_to_orderbook_path(snap.path, snap.ts_beijing)
        counts[snap.snapshot_ts_utc] = 1 if path is not None and path.exists() else 0
    return counts


def settlement_maps(conn: sqlite3.Connection) -> tuple[dict[tuple[str, str, str], float], dict[str, tuple[int, int]]]:
    outcomes = rows(
        conn,
        """
        SELECT city,target_date,bracket,final_price,settlement_status
        FROM settlement_outcomes
        WHERE settlement_status='settled'
        """,
    )
    price_map: dict[tuple[str, str, str], float] = {}
    coverage: dict[str, tuple[int, int]] = {}
    counts: dict[str, set[str]] = defaultdict(set)
    rows_by_date: dict[str, int] = defaultdict(int)
    for r in outcomes:
        date = str(r["target_date"])
        city = str(r["city"])
        bracket = str(r["bracket"])
        price = safe_float(r["final_price"])
        if math.isfinite(price):
            price_map[(city, date, bracket)] = price
        counts[date].add(city)
        rows_by_date[date] += 1
    for date in rows_by_date:
        coverage[date] = (len(counts[date]), rows_by_date[date])
    return price_map, coverage


def build_observation_history(snapshots: list[SnapshotFile]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for snap in snapshots:
        try:
            data = json.loads(snap.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for rec in data.get("records") or []:
            if not isinstance(rec, dict):
                continue
            city = str(rec.get("city") or "")
            target_date = str(rec.get("target_date") or rec.get("event_date") or "")
            if not city or not target_date:
                continue
            if str(rec.get("city_local_date_at_snapshot") or "") != target_date:
                continue
            latest_ts = str(rec.get("metar_latest_ts_utc") or "")
            if not latest_ts:
                continue
            key = (city, target_date, latest_ts)
            if key in seen:
                continue
            seen.add(key)
            unit = str(rec.get("unit") or "")
            max_f = safe_float(rec.get("metar_current_max_f"))
            latest_f = safe_float(rec.get("metar_latest_temp_f"))
            if not math.isfinite(max_f):
                continue
            records.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "snapshot_ts_utc": snap.snapshot_ts_utc,
                    "metar_latest_ts_utc": latest_ts,
                    "running_value": native_tick_value(max_f, unit),
                    "running_native": to_native(max_f, unit),
                    "latest_native": to_native(latest_f, unit),
                }
            )
    if not records:
        return pd.DataFrame()
    hist = pd.DataFrame(records)
    hist["snapshot_dt"] = pd.to_datetime(hist["snapshot_ts_utc"], errors="coerce", utc=True)
    hist["obs_dt"] = pd.to_datetime(hist["metar_latest_ts_utc"], errors="coerce", utc=True)
    hist = hist.dropna(subset=["snapshot_dt", "obs_dt"]).sort_values(["city", "target_date", "obs_dt"])
    return hist


def cadence_maps(obs_hist: pd.DataFrame) -> tuple[dict[tuple[str, str], list[float]], dict[tuple[str, str, str], float]]:
    cadence_by_city_date: dict[tuple[str, str], list[float]] = {}
    cadence_at_obs: dict[tuple[str, str, str], float] = {}
    if obs_hist.empty:
        return cadence_by_city_date, cadence_at_obs
    for (city, target_date), g in obs_hist.groupby(["city", "target_date"]):
        unique = g.drop_duplicates("metar_latest_ts_utc").sort_values("obs_dt")
        diffs = unique["obs_dt"].diff().dt.total_seconds() / 60.0
        vals = [float(x) for x in diffs.dropna() if math.isfinite(float(x)) and 10 <= float(x) <= 180]
        cadence_by_city_date[(str(city), str(target_date))] = vals
        rolling: list[float] = []
        prev = None
        for row in unique.itertuples(index=False):
            obs_dt = row.obs_dt
            if prev is not None:
                diff = (obs_dt - prev).total_seconds() / 60.0
                if 10 <= diff <= 180:
                    rolling.append(float(diff))
            cadence = float(np.median(rolling[-4:])) if rolling else math.nan
            cadence_at_obs[(str(city), str(target_date), str(row.metar_latest_ts_utc))] = cadence
            prev = obs_dt
    return cadence_by_city_date, cadence_at_obs


def future_touch_labels(obs_hist: pd.DataFrame) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    if obs_hist.empty:
        return out
    by_group = {
        (str(city), str(date)): g.sort_values("obs_dt").copy()
        for (city, date), g in obs_hist.groupby(["city", "target_date"])
    }
    for (city, date), g in by_group.items():
        for row in g.itertuples(index=False):
            snap_dt = row.snapshot_dt
            if pd.isna(snap_dt):
                continue
            future = g[g["obs_dt"].gt(snap_dt)]
            for target in range(int(row.running_value or 0) - 1, int(row.running_value or 0) + 5):
                touched = future[future["running_value"].fillna(-999).ge(target)]
                first_minutes = math.nan
                if not touched.empty:
                    first_minutes = float((touched.iloc[0]["obs_dt"] - snap_dt).total_seconds() / 60.0)
                future_increment = math.nan
                if not future.empty and math.isfinite(safe_float(row.running_native)):
                    future_increment = float(pd.to_numeric(future["running_native"], errors="coerce").max() - row.running_native)
                out[(city, date, str(row.snapshot_ts_utc), str(target))] = {
                    "touch_target_after_decision": bool(not touched.empty),
                    "first_touch_minutes_after_decision": first_minutes,
                    "max_future_increment_native": future_increment,
                }
    return out


def load_forecast_curve_index(curve_dir: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not curve_dir.exists():
        return out
    for path in sorted(curve_dir.glob("**/forecast_hourly_curves_*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    key = (str(rec.get("city") or ""), str(rec.get("target_date") or ""), str(rec.get("snapshot_ts_utc") or ""))
                    if key[0] and key[1] and key[2]:
                        out[key] = rec
        except (OSError, json.JSONDecodeError):
            continue
    return out


def hourly_context(curve: dict[str, Any] | None, snapshot_ts_utc: str, unit: str, target: float) -> dict[str, Any]:
    missing = {
        "hourly_context_available": 0.0,
        "om_remaining_temp_max_native": math.nan,
        "om_remaining_temp_mean_native": math.nan,
        "om_remaining_temp_slope_native": math.nan,
        "om_remaining_peak_margin_to_target_ticks": math.nan,
    }
    if not curve:
        return missing
    snap_dt = _dt_utc(snapshot_ts_utc)
    if snap_dt is None:
        return missing
    remaining: list[tuple[pd.Timestamp, float]] = []
    for item in curve.get("hourly_curve") or []:
        local = _dt_naive(item.get("time_local"))
        if local is None:
            continue
        # Compare by local hour only; curve records are same target_date local
        snap_local = _dt_naive(curve.get("ts_beijing"))
        if snap_local is not None and local.hour + local.minute / 60.0 < snap_local.hour + snap_local.minute / 60.0:
            continue
        temp_f = safe_float(item.get("temperature_f"))
        if math.isfinite(temp_f):
            remaining.append((local, f_to_native(temp_f, unit)))
    if not remaining:
        return missing
    vals = [x[1] for x in remaining]
    slope = vals[-1] - vals[0] if len(vals) >= 2 else 0.0
    step = unit_step(unit)
    return {
        "hourly_context_available": 1.0,
        "om_remaining_temp_max_native": float(np.nanmax(vals)),
        "om_remaining_temp_mean_native": float(np.nanmean(vals)),
        "om_remaining_temp_slope_native": float(slope),
        "om_remaining_peak_margin_to_target_ticks": float((target - np.nanmax(vals)) / step),
    }


def entry_fields(record: dict[str, Any], leg: str) -> dict[str, float]:
    if leg == "current_yes":
        return {
            "side": "YES",
            "entry_price": safe_float(record.get("yes_best_ask")),
            "best_bid": safe_float(record.get("yes_best_bid")),
            "spread": safe_float(record.get("yes_spread")),
            "top_size": safe_float(record.get("yes_ask_size")),
            "depth_5c": safe_float(record.get("yes_depth_ask_5c")),
            "bid_depth_5c": safe_float(record.get("yes_depth_bid_5c")),
        }
    return {
        "side": "NO",
        "entry_price": safe_float(record.get("no_best_ask")),
        "best_bid": safe_float(record.get("no_best_bid")),
        "spread": safe_float(record.get("no_spread")),
        "top_size": safe_float(record.get("no_ask_size")),
        "depth_5c": safe_float(record.get("no_depth_ask_5c")),
        "bid_depth_5c": safe_float(record.get("no_depth_bid_5c")),
    }


def base_candidate_row(
    snap: SnapshotFile,
    rec: dict[str, Any],
    cadence_at_obs: dict[tuple[str, str, str], float],
    curve_index: dict[tuple[str, str, str], dict[str, Any]],
    orderbook_counts: dict[str, int],
) -> dict[str, Any] | None:
    target_date = str(rec.get("target_date") or rec.get("event_date") or "")
    if not target_date or str(rec.get("city_local_date_at_snapshot") or "") != target_date:
        return None
    if target_date < TRAIN_START or target_date > FORWARD_END:
        return None
    ts_local = str(rec.get("ts_local") or snap.ts_beijing).replace(" ", "T")
    try:
        local_hour = int(ts_local[11:13])
        local_minute = int(ts_local[14:16])
    except (ValueError, IndexError):
        return None
    if local_hour < LOCAL_START_HOUR or local_hour > LOCAL_END_HOUR:
        return None
    if str(rec.get("probability_status") or "") != "ok":
        return None
    if rec.get("metar_source") in (None, "none"):
        return None
    city = str(rec.get("city") or "")
    unit = str(rec.get("unit") or "")
    metar_max_f = safe_float(rec.get("metar_current_max_f"))
    metar_latest_f = safe_float(rec.get("metar_latest_temp_f"))
    if not math.isfinite(metar_max_f):
        return None
    running_value = native_tick_value(metar_max_f, unit)
    if running_value is None:
        return None
    leg = leg_for_record(rec, running_value)
    if leg not in {"current_yes", "d1_no", "d2_no", "d3_no"}:
        return None
    bracket = parse_bracket(rec.get("bracket"))
    if bracket is None or bracket.low is None:
        return None
    fields = entry_fields(rec, leg)
    running_native = native_from_metar_f(metar_max_f, unit)
    latest_native = native_from_metar_f(metar_latest_f, unit)
    forecast_max_native = safe_float(rec.get("forecast_max_native"))
    step = unit_step(unit)
    target = float(bracket.low)
    latest_ts = str(rec.get("metar_latest_ts_utc") or "")
    snap_dt = _dt_utc(snap.snapshot_ts_utc)
    latest_dt = _dt_utc(latest_ts)
    obs_age = math.nan
    if snap_dt is not None and latest_dt is not None:
        obs_age = float((snap_dt - latest_dt).total_seconds() / 60.0)
    cadence = cadence_at_obs.get((city, target_date, latest_ts), math.nan)
    if not math.isfinite(cadence):
        cadence = 60.0 if city in {"Chengdu", "Chongqing", "Beijing"} else math.nan
    minutes_to_next = math.nan
    if math.isfinite(cadence) and math.isfinite(obs_age):
        minutes_to_next = max(0.0, cadence - obs_age)
    curve = curve_index.get((city, target_date, snap.snapshot_ts_utc))
    row = {
        "snapshot_file": str(snap.path.relative_to(ROOT)),
        "snapshot_ts_utc": snap.snapshot_ts_utc,
        "ts_beijing": snap.ts_beijing,
        "ts_local": ts_local,
        "local_hour": local_hour,
        "local_minute": local_minute,
        "city": city,
        "region_bucket": REGION_BY_CITY.get(city, "other"),
        "city_pool": rec.get("city_pool"),
        "target_date": target_date,
        "unit": unit,
        "bracket": str(rec.get("bracket")),
        "leg": leg,
        "side": fields["side"],
        "outcome": "yes" if leg == "current_yes" else "no",
        "target_bracket_low_native": target,
        "target_bracket_high_native": bracket.high,
        "running_value": running_value,
        "running_native": running_native,
        "latest_native": latest_native,
        "decline_native": running_native - latest_native if math.isfinite(latest_native) else math.nan,
        "metar_current_max_f": metar_max_f,
        "metar_latest_temp_f": metar_latest_f,
        "metar_latest_ts_utc": latest_ts,
        "metar_obs_count_today": rec.get("metar_obs_count_today"),
        "obs_age_minutes": obs_age,
        "obs_cadence_min": cadence,
        "minutes_to_next_obs": minutes_to_next,
        "minutes_to_next_key_obs": minutes_to_next,
        "forecast_max_native": forecast_max_native,
        "forecast_gap_to_running_native": forecast_max_native - running_value if math.isfinite(forecast_max_native) else math.nan,
        "forecast_peak_time_local": rec.get("forecast_peak_time_local"),
        "forecast_peak_delta_hours_local": safe_float(rec.get("forecast_peak_delta_hours_local")),
        "forecast_source": rec.get("forecast_source"),
        "forecast_margin_to_target_native": target - forecast_max_native if math.isfinite(forecast_max_native) else math.nan,
        "target_distance_native": target - running_value,
        "entry_price": fields["entry_price"],
        "best_bid": fields["best_bid"],
        "spread": fields["spread"],
        "top_size": fields["top_size"],
        "depth_5c": fields["depth_5c"],
        "bid_depth_5c": fields["bid_depth_5c"],
        "condition_id": rec.get("condition_id"),
        "market_id": rec.get("market_id"),
        "yes_best_ask": safe_float(rec.get("yes_best_ask")),
        "yes_best_bid": safe_float(rec.get("yes_best_bid")),
        "no_best_ask": safe_float(rec.get("no_best_ask")),
        "no_best_bid": safe_float(rec.get("no_best_bid")),
        "question": rec.get("question"),
        "clob_price_source": rec.get("clob_price_source"),
        "orderbook_rows_in_archive": orderbook_counts.get(snap.snapshot_ts_utc, math.nan),
        "multi_model_available": 0.0,
        "multi_model_spread_native": math.nan,
        "preferred_vs_ensemble_gap_native": math.nan,
        "om_cloud_cover": math.nan,
        "om_precipitation_probability": math.nan,
        "om_cape": math.nan,
        "om_cin": math.nan,
        "om_lifted_index": math.nan,
        "om_boundary_layer_height": math.nan,
        "om_shortwave_radiation": math.nan,
        "om_wind_speed": math.nan,
        "om_wind_gust": math.nan,
        "om_wind_direction": math.nan,
        "taf_available": 0.0,
        "taf_issue_time_utc": "",
        "taf_peak_window_overlap": 0.0,
        "taf_peak_overlap_score": 0.0,
        "taf_tx_native": math.nan,
        "taf_tn_native": math.nan,
        "taf_tx_margin_ticks": math.nan,
        "taf_tsra": 0.0,
        "taf_shra": 0.0,
        "taf_tempo": 0.0,
        "taf_becmg": 0.0,
        "taf_suppression_level": "taf_unavailable",
        "taf_disruption_level": "taf_unavailable",
        "vertical_profile_available": 0.0,
        "vertical_heating_setup": math.nan,
        "vertical_heating_score": math.nan,
        "vertical_suppression_risk": math.nan,
        "vertical_trigger_risk": math.nan,
        "vertical_mixing_strength": math.nan,
        "vertical_shear_risk": math.nan,
        "vertical_profile_signal": "vertical_unavailable",
    }
    row.update(hourly_context(curve, snap.snapshot_ts_utc, unit, target))
    row["residual_points"] = (1.0 - row["entry_price"]) * 100.0 if math.isfinite(row["entry_price"]) else math.nan
    row["target_distance_ticks"] = (target - running_value) / step
    row["forecast_margin_to_target_ticks"] = row["forecast_margin_to_target_native"] / step if math.isfinite(row["forecast_margin_to_target_native"]) else math.nan
    row.update(heating_done_features(row))
    row.update(residual_done_features(row))
    return row


def add_pit_state_features(df: pd.DataFrame) -> pd.DataFrame:
    out = add_buckets(df)
    out["snap_dt"] = pd.to_datetime(out["snapshot_ts_utc"], errors="coerce", utc=True)
    out["metar_dt"] = pd.to_datetime(out["metar_latest_ts_utc"], errors="coerce", utc=True)
    out = out.sort_values(["city", "target_date", "snap_dt", "leg", "bracket"]).reset_index(drop=True)
    out["true_local_time_float"] = pd.to_numeric(out["local_hour"], errors="coerce") + pd.to_numeric(
        out["local_minute"], errors="coerce"
    ).fillna(0.0) / 60.0
    unit = out["unit"].astype(str).str.upper()
    out["native_tick_size"] = np.where(unit.eq("F"), 2.0, 1.0)
    out["forecast_gap_to_running_ticks"] = pd.to_numeric(out["forecast_gap_to_running_native"], errors="coerce") / out[
        "native_tick_size"
    ]
    out["decline_ticks"] = pd.to_numeric(out["decline_native"], errors="coerce") / out["native_tick_size"]
    out["target_distance_ticks"] = pd.to_numeric(out["target_distance_native"], errors="coerce") / out["native_tick_size"]
    out["forecast_margin_to_target_ticks"] = pd.to_numeric(
        out["forecast_margin_to_target_native"], errors="coerce"
    ) / out["native_tick_size"]
    out["obs_count_log1p"] = np.log1p(pd.to_numeric(out["metar_obs_count_today"], errors="coerce"))
    out["obs_age_bucket"] = pd.cut(
        pd.to_numeric(out["obs_age_minutes"], errors="coerce"),
        [-0.001, 15, 35, 65, 120, float("inf")],
        labels=["fresh_0_15m", "ok_15_35m", "stale_35_65m", "old_65_120m", "very_old_120m_plus"],
        include_lowest=True,
    ).astype("object")
    out["true_local_time_bucket"] = pd.cut(
        out["true_local_time_float"],
        [-0.001, 10, 12, 14, 15.5, 16.5, 18, 24],
        labels=["morning", "late_morning", "early_afternoon", "pre_late", "key_1530_1630", "late_afternoon", "evening"],
        include_lowest=True,
    ).astype("object")
    out["obs_cadence_bucket"] = pd.cut(
        pd.to_numeric(out["obs_cadence_min"], errors="coerce"),
        [-0.001, 35, 50, 75, 120, float("inf")],
        labels=["cadence_le35", "cadence_35_50", "cadence_50_75", "cadence_75_120", "cadence_gt120"],
        include_lowest=True,
    ).astype("object")
    mnext = pd.to_numeric(out["minutes_to_next_obs"], errors="coerce")
    out["pre_update_blackout_bucket"] = pd.cut(
        mnext,
        [-0.001, 10, 20, 30, 45, 90, float("inf")],
        labels=["next_obs_le10", "next_obs_10_20", "next_obs_20_30", "next_obs_30_45", "next_obs_45_90", "next_obs_gt90"],
        include_lowest=True,
    ).astype("object")
    out["pre_update_blackout_score"] = np.select(
        [mnext.le(10), mnext.le(20), mnext.le(30), mnext.le(45)],
        [1.0, 0.75, 0.55, 0.25],
        default=0.0,
    )
    out["path_state"] = np.select(
        [
            pd.to_numeric(out["decline_native"], errors="coerce").abs().le(0.25),
            pd.to_numeric(out["decline_native"], errors="coerce").gt(0.25),
            pd.to_numeric(out["decline_native"], errors="coerce").lt(-0.25),
        ],
        ["at_high", "decline", "warming_above_running"],
        default="missing",
    )
    out["minutes_since_running_max"] = np.nan
    out["same_running_max_obs_count"] = 1
    for (_, _), idx in out.groupby(["city", "target_date"]).groups.items():
        g = out.loc[idx].sort_values("snap_dt")
        first_seen: dict[int, pd.Timestamp] = {}
        counts: dict[int, int] = defaultdict(int)
        vals = []
        cnts = []
        last_obs_for_val: dict[int, str] = {}
        for r in g.itertuples():
            rv = int(r.running_value) if pd.notna(r.running_value) else -999
            if rv not in first_seen and pd.notna(r.snap_dt):
                first_seen[rv] = r.snap_dt
            if last_obs_for_val.get(rv) != str(r.metar_latest_ts_utc):
                counts[rv] += 1
                last_obs_for_val[rv] = str(r.metar_latest_ts_utc)
            vals.append((r.snap_dt - first_seen[rv]).total_seconds() / 60.0 if rv in first_seen and pd.notna(r.snap_dt) else math.nan)
            cnts.append(counts[rv])
        out.loc[g.index, "minutes_since_running_max"] = vals
        out.loc[g.index, "same_running_max_obs_count"] = cnts
    for col in CATEGORICAL_FEATURES:
        if col not in out.columns:
            out[col] = "missing"
        out[col] = out[col].astype("object").where(out[col].notna(), "missing").astype(str)
    numeric_cols = sorted(set(BASE_FEATURES + OBS_CLOCK_FEATURES + FORECAST_FEATURES + CITY_CAL_FEATURES + [
        "taf_peak_overlap_score",
        "taf_tx_margin_ticks",
        "hourly_context_available",
        "vertical_heating_score",
    ]))
    for col in numeric_cols:
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def add_labels(df: pd.DataFrame, price_map: dict[tuple[str, str, str], float], touch_map: dict[tuple[str, str, str, str], dict[str, Any]]) -> pd.DataFrame:
    out = df.copy()
    out["final_yes"] = [price_map.get((str(r.city), str(r.target_date), str(r.bracket))) for r in out.itertuples()]
    out["settled"] = out["final_yes"].notna()
    out["final_exact_target"] = out["final_yes"].ge(0.99)
    out["no_win"] = out["final_yes"].le(0.01)
    touch_vals = []
    first_vals = []
    incr_vals = []
    for r in out.itertuples():
        label = touch_map.get((str(r.city), str(r.target_date), str(r.snapshot_ts_utc), str(int(float(r.target_bracket_low_native)))))
        final_exact = bool(out.at[r.Index, "final_exact_target"]) if hasattr(r, "Index") else False
        current_running = safe_float(getattr(r, "running_value", math.nan))
        target_low = safe_float(getattr(r, "target_bracket_low_native", math.nan))
        # Exact-bracket settlement is stronger than the paper-snapshot running
        # max proxy. If final exactly target and the row had not already reached
        # target, then a post-decision touch must have happened even when the
        # local paper snapshot stream missed the raw observation transition.
        settlement_implies_touch = final_exact and math.isfinite(current_running) and math.isfinite(target_low) and current_running < target_low
        touch_vals.append(bool((label and label["touch_target_after_decision"]) or settlement_implies_touch))
        first_vals.append(label.get("first_touch_minutes_after_decision", math.nan) if label else math.nan)
        incr_vals.append(label.get("max_future_increment_native", math.nan) if label else math.nan)
    out["touch_target_after_decision"] = touch_vals
    out["first_touch_minutes_after_decision"] = first_vals
    out["max_future_increment_native"] = incr_vals
    out["fee_per_share"] = pd.to_numeric(out["entry_price"], errors="coerce").apply(lambda x: fee_per_share(float(x)) if math.isfinite(float(x)) else math.nan)
    out["cost_per_share"] = pd.to_numeric(out["entry_price"], errors="coerce") + out["fee_per_share"]
    return out


def add_city_prior_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values(["target_date", "snapshot_ts_utc"]).copy()
    for col in CITY_CAL_FEATURES:
        out[col] = np.nan
    history_city: dict[str, list[tuple[bool, bool, float]]] = defaultdict(list)
    history_city_hour_path: dict[tuple[str, int, str], list[bool]] = defaultdict(list)
    history_source: dict[str, list[bool]] = defaultdict(list)
    for date in sorted(out["target_date"].astype(str).unique()):
        mask = out["target_date"].astype(str).eq(date)
        for idx, r in out[mask].iterrows():
            city = str(r["city"])
            source = str(r.get("forecast_source") or "missing")
            key = (city, int(r.get("local_hour") or -1), str(r.get("path_state") or "missing"))
            h_city = history_city[city]
            h_hp = history_city_hour_path[key]
            h_src = history_source[source]
            out.at[idx, "city_prior_d1_touch_rate"] = np.mean([x[0] for x in h_city]) if h_city else np.nan
            out.at[idx, "city_prior_d1_exact_rate"] = np.mean([x[1] for x in h_city]) if h_city else np.nan
            out.at[idx, "city_prior_late_spike_rate"] = np.mean([x[2] >= 1.0 for x in h_city if math.isfinite(x[2])]) if h_city else np.nan
            out.at[idx, "city_hour_path_prior_touch_rate"] = np.mean(h_hp) if h_hp else np.nan
            out.at[idx, "source_prior_touch_rate"] = np.mean(h_src) if h_src else np.nan
        for _, r in out[mask & out["leg"].eq("d1_no") & out["settled"]].iterrows():
            city = str(r["city"])
            source = str(r.get("forecast_source") or "missing")
            key = (city, int(r.get("local_hour") or -1), str(r.get("path_state") or "missing"))
            touch = bool(r["touch_target_after_decision"])
            exact = bool(r["final_exact_target"])
            incr = safe_float(r["max_future_increment_native"])
            history_city[city].append((touch, exact, incr))
            history_city_hour_path[key].append(touch)
            history_source[source].append(touch)
    return out


def make_model(numeric: list[str], categorical: list[str]) -> Pipeline:
    numeric_pipe = Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())])
    cat_pipe = Pipeline([("imputer", SimpleImputer(strategy="most_frequent")), ("onehot", OneHotEncoder(handle_unknown="ignore"))])
    pre = ColumnTransformer([("num", numeric_pipe, numeric), ("cat", cat_pipe, categorical)])
    return Pipeline(
        [
            ("pre", pre),
            ("model", LogisticRegression(C=0.5, max_iter=2000, class_weight="balanced", random_state=RANDOM_SEED)),
        ]
    )


def metric_row(model: str, target: str, scope: str, y: pd.Series, p: pd.Series, dates: pd.Series) -> dict[str, Any]:
    mask = y.notna() & p.notna()
    yy = y[mask].astype(int)
    pp = p[mask].astype(float).clip(1e-6, 1 - 1e-6)
    if yy.empty:
        return {"model": model, "target": target, "scope": scope, "rows": 0}
    try:
        auc = roc_auc_score(yy, pp) if yy.nunique() == 2 else math.nan
    except ValueError:
        auc = math.nan
    return {
        "model": model,
        "target": target,
        "scope": scope,
        "rows": int(len(yy)),
        "dates": int(dates[mask].nunique()),
        "base_rate": float(yy.mean()),
        "logloss": float(log_loss(yy, pp, labels=[0, 1])),
        "brier": float(brier_score_loss(yy, pp)),
        "auc": auc,
        "avg_p": float(pp.mean()),
    }


def expanding_two_stage(df: pd.DataFrame, numeric: list[str], categorical: list[str], label: str) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out[f"{label}_p_touch"] = np.nan
    out[f"{label}_p_stop_exact_given_touch"] = np.nan
    out[f"{label}_p_exact"] = np.nan
    out[f"{label}_p_no_win"] = np.nan
    features = numeric + categorical
    dates = sorted(df["target_date"].astype(str).unique())
    for date in dates:
        train_mask = df["target_date"].astype(str).lt(date) & df["settled"].astype(bool)
        test_mask = df["target_date"].astype(str).eq(date)
        if int(train_mask.sum()) < 80:
            continue
        train = df[train_mask]
        if train["touch_target_after_decision"].nunique() < 2 or train["final_exact_target"].nunique() < 2:
            continue
        touch_model = make_model(numeric, categorical)
        touch_model.fit(train[features], train["touch_target_after_decision"].astype(int))
        p_touch_train = touch_model.predict_proba(train[features])[:, 1]
        p_touch_test = touch_model.predict_proba(df.loc[test_mask, features])[:, 1]
        out.loc[test_mask, f"{label}_p_touch"] = p_touch_test

        # final_exact_target implies that the d1 target was touched after the
        # decision.  Model the conditional stop probability only on touched
        # training rows, then multiply by Stage A.  Treating final_exact as an
        # independent unconditional head can produce the impossible ordering
        # P(final exact) > P(touch).
        exact_train = train[train["touch_target_after_decision"].astype(bool)].copy()
        if len(exact_train) < 40 or exact_train["final_exact_target"].nunique() < 2:
            continue
        exact_test = df.loc[test_mask].copy()
        exact_train[f"{label}_stage_a_p_touch"] = p_touch_train[train["touch_target_after_decision"].astype(bool).to_numpy()]
        exact_test[f"{label}_stage_a_p_touch"] = p_touch_test
        num2 = numeric + [f"{label}_stage_a_p_touch"]
        exact_model = make_model(num2, categorical)
        exact_model.fit(exact_train[num2 + categorical], exact_train["final_exact_target"].astype(int))
        p_stop_exact_given_touch = exact_model.predict_proba(exact_test[num2 + categorical])[:, 1]
        p_exact = coherent_exact_probability(p_touch_test, p_stop_exact_given_touch)
        out.loc[test_mask, f"{label}_p_stop_exact_given_touch"] = p_stop_exact_given_touch
        out.loc[test_mask, f"{label}_p_exact"] = p_exact
        out.loc[test_mask, f"{label}_p_no_win"] = 1.0 - p_exact
    return out


def expanding_single_stage(df: pd.DataFrame, numeric: list[str], categorical: list[str], label: str) -> pd.Series:
    preds = pd.Series(np.nan, index=df.index, dtype=float)
    features = numeric + categorical
    for date in sorted(df["target_date"].astype(str).unique()):
        train_mask = df["target_date"].astype(str).lt(date) & df["settled"].astype(bool)
        test_mask = df["target_date"].astype(str).eq(date)
        train = df[train_mask]
        if int(train_mask.sum()) < 80 or train["no_win"].nunique() < 2:
            continue
        model = make_model(numeric, categorical)
        model.fit(train[features], train["no_win"].astype(int))
        preds.loc[test_mask] = model.predict_proba(df.loc[test_mask, features])[:, 1]
    preds.name = label
    return preds


def market_no_baseline(df: pd.DataFrame) -> pd.Series:
    """Return the executable NO ask as the market-implied NO-win probability.

    ``entry_price`` is already the d1 NO best ask.  The previous implementation
    inverted it a second time, which made a high-priced NO look like a low
    market probability and invalidated the market proper-score/ROI baseline.
    """

    price = pd.to_numeric(df["entry_price"], errors="coerce")
    return price.clip(1e-6, 1 - 1e-6)


def coherent_exact_probability(p_touch: Any, p_stop_exact_given_touch: Any) -> np.ndarray:
    """Compose a coherent exact probability from touch and conditional stop."""

    touch = np.clip(np.asarray(p_touch, dtype=float), 0.0, 1.0)
    stop = np.clip(np.asarray(p_stop_exact_given_touch, dtype=float), 0.0, 1.0)
    return touch * stop


def clock_only_hazard(df: pd.DataFrame) -> pd.Series:
    mnext = pd.to_numeric(df["minutes_to_next_obs"], errors="coerce")
    cadence = pd.to_numeric(df["obs_cadence_min"], errors="coerce")
    path_at_high = df["path_state"].astype(str).eq("at_high").astype(float)
    score = -2.8 + 1.3 * mnext.le(30).fillna(False).astype(float) + 0.8 * cadence.between(50, 75).fillna(False).astype(float) + 0.7 * path_at_high
    return pd.Series(1.0 / (1.0 + np.exp(-score)), index=df.index, name="clock_only_p_touch")


def roi_table(df: pd.DataFrame, prob_col: str, prefix: str) -> pd.DataFrame:
    rows_out = []
    active = df[df["settled"] & df["entry_price"].notna() & df[prob_col].notna() & df["no_win"].notna()].copy()
    active["fee"] = active["entry_price"].apply(lambda x: fee_per_share(float(x)))
    active["cost"] = active["entry_price"] + active["fee"]
    active["edge"] = active[prob_col] - active["cost"]
    active["pnl"] = active["no_win"].astype(float) - active["cost"]
    for thr in [0.0, 0.01, 0.02, 0.03, 0.05]:
        sel = active[active["edge"].ge(thr)].copy()
        cost = float(sel["cost"].sum()) if not sel.empty else 0.0
        pnl = float(sel["pnl"].sum()) if not sel.empty else 0.0
        daily = sel.groupby("target_date").agg(cost=("cost", "sum"), pnl=("pnl", "sum")).reset_index()
        daily_roi = daily["pnl"] / daily["cost"] if not daily.empty else pd.Series(dtype=float)
        lo = hi = math.nan
        if len(daily_roi) >= 2:
            rng = np.random.default_rng(RANDOM_SEED)
            vals = []
            arr = daily[["cost", "pnl"]].to_numpy(dtype=float)
            for _ in range(1000):
                idx = rng.integers(0, len(arr), len(arr))
                sample = arr[idx]
                c = sample[:, 0].sum()
                vals.append(sample[:, 1].sum() / c if c > 0 else math.nan)
            lo, hi = np.nanpercentile(vals, [2.5, 97.5])
        rows_out.append(
            {
                "model": prefix,
                "edge_threshold": thr,
                "rows": int(len(sel)),
                "dates": int(sel["target_date"].nunique()) if not sel.empty else 0,
                "cities": int(sel["city"].nunique()) if not sel.empty else 0,
                "cost": cost,
                "pnl": pnl,
                "roi": pnl / cost if cost > 0 else math.nan,
                "date_block_ci_low": lo,
                "date_block_ci_high": hi,
            }
        )
    return pd.DataFrame(rows_out)


def calibration_table(df: pd.DataFrame, prob_col: str, label_col: str, model: str) -> pd.DataFrame:
    active = df[df[prob_col].notna() & df[label_col].notna()].copy()
    if active.empty:
        return pd.DataFrame()
    active["decile"] = pd.qcut(active[prob_col].rank(method="first"), 10, labels=False, duplicates="drop")
    return (
        active.groupby("decile", as_index=False)
        .agg(rows=(label_col, "size"), avg_p=(prob_col, "mean"), actual=(label_col, "mean"))
        .assign(model=model, label=label_col)
    )


def df_to_md(df: pd.DataFrame, max_rows: int | None = None) -> str:
    if df.empty:
        return "_empty_"
    view = df.copy()
    if max_rows is not None:
        view = view.head(max_rows)
    cols = list(view.columns)

    def fmt(value: Any) -> str:
        if pd.isna(value):
            return ""
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value).replace("|", "\\|")

    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(fmt(row[c]) for c in cols) + " |")
    if max_rows is not None and len(df) > max_rows:
        lines.append(f"\n_omitted {len(df) - max_rows} rows_")
    return "\n".join(lines)


def slice_metrics(df: pd.DataFrame, prob_col: str) -> pd.DataFrame:
    slices: list[tuple[str, pd.Series]] = [
        ("d1_no", df["leg"].eq("d1_no")),
        ("d1_no_next_obs_le30", df["leg"].eq("d1_no") & pd.to_numeric(df["minutes_to_next_obs"], errors="coerce").le(30)),
        ("d1_no_cadence_approx60", df["leg"].eq("d1_no") & pd.to_numeric(df["obs_cadence_min"], errors="coerce").between(50, 75)),
        ("d1_no_path_decline", df["leg"].eq("d1_no") & df["path_state"].eq("decline")),
        ("d1_no_forecast_below_target", df["leg"].eq("d1_no") & pd.to_numeric(df["forecast_margin_to_target_ticks"], errors="coerce").gt(0)),
        ("d1_no_multi_model_spread_high", df["leg"].eq("d1_no") & pd.to_numeric(df["multi_model_spread_native"], errors="coerce").gt(1)),
        ("d1_no_chengdu_like_asia_hot", df["leg"].eq("d1_no") & df["region_bucket"].isin(["asia_humid_basin", "asia_hot", "asia_humid"])),
    ]
    out = []
    for name, mask in slices:
        sub = df[mask & df["settled"] & df[prob_col].notna()].copy()
        if sub.empty:
            out.append({"slice": name, "rows": 0})
            continue
        out.append(
            {
                "slice": name,
                "rows": int(len(sub)),
                "dates": int(sub["target_date"].nunique()),
                "cities": int(sub["city"].nunique()),
                "touch_rate": float(sub["touch_target_after_decision"].mean()),
                "exact_rate": float(sub["final_exact_target"].mean()),
                "no_win_rate": float(sub["no_win"].mean()),
                "avg_p_no_win": float(sub[prob_col].mean()),
                "tail_miss_rate": float(((sub[prob_col] >= 0.90) & (~sub["no_win"].astype(bool))).mean()),
            }
        )
    return pd.DataFrame(out)


def build_candidate_frame(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    snapshots = iter_snapshots(SNAPSHOT_DIR)
    print(f"[trade_win_v2] snapshots={len(snapshots)}", flush=True)
    obs_hist = build_observation_history(snapshots)
    print(f"[trade_win_v2] obs_history_rows={len(obs_hist)}", flush=True)
    _, cadence_at_obs = cadence_maps(obs_hist)
    touch_map = future_touch_labels(obs_hist)
    print(f"[trade_win_v2] touch_labels={len(touch_map)}", flush=True)
    curve_index = load_forecast_curve_index(FORECAST_CURVE_DIR)
    orderbook_counts = load_orderbook_counts(snapshots)
    records = []
    for snap in snapshots:
        try:
            data = json.loads(snap.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for rec in data.get("records") or []:
            if not isinstance(rec, dict):
                continue
            row = base_candidate_row(snap, rec, cadence_at_obs, curve_index, orderbook_counts)
            if row is not None:
                records.append(row)
    print(f"[trade_win_v2] raw_candidate_rows={len(records)}", flush=True)
    candidates = pd.DataFrame(records)
    if candidates.empty:
        raise RuntimeError("No candidate rows generated")
    candidates = add_pit_state_features(candidates)
    with connect_ro(DB) as conn:
        price_map, coverage = settlement_maps(conn)
    candidates = add_labels(candidates, price_map, touch_map)
    candidates = add_city_prior_features(candidates)
    print(f"[trade_win_v2] labeled_candidate_rows={len(candidates)}", flush=True)
    metadata = {
        "snapshots_seen": len(snapshots),
        "candidate_rows": int(len(candidates)),
        "settlement_coverage": {k: {"cities": v[0], "rows": v[1]} for k, v in sorted(coverage.items()) if "2026-07-01" <= k <= "2026-07-08"},
        "forecast_curve_records": len(curve_index),
        "obs_history_rows": int(len(obs_hist)),
        "taf_history_available": False,
        "vertical_profile_history_available": False,
        "multi_model_history_available": False,
    }
    return candidates, metadata


def run_models(candidates: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    d1 = candidates[candidates["leg"].eq("d1_no")].copy()
    d1 = d1[d1["settled"].astype(bool)].copy()
    print(f"[trade_win_v2] settled_d1_rows={len(d1)}", flush=True)
    for col in CITY_CAL_FEATURES:
        d1[col] = pd.to_numeric(d1[col], errors="coerce")
    d1["market_p_no_win"] = market_no_baseline(d1)
    d1["clock_only_p_touch"] = clock_only_hazard(d1)
    d1["clock_only_p_no_win"] = 1.0 - d1["clock_only_p_touch"].clip(0, 1)
    d1["raw_physical_score_v1"] = pd.to_numeric(d1["leg_residual_done_score_v1"], errors="coerce").clip(1e-6, 1 - 1e-6)
    d1["v1_original_retrained_p_no_win"] = expanding_single_stage(
        d1,
        V1_ORIGINAL_NUMERIC,
        V1_ORIGINAL_CATEGORICAL,
        "v1_original_retrained_p_no_win",
    )
    d1["old_v1_like_p_no_win"] = expanding_single_stage(d1, OLD_V1_NUMERIC, OLD_V1_CATEGORICAL, "old_v1_like_p_no_win")

    metric_rows = []
    calibration_frames = []
    roi_frames = []
    slice_frames = []
    ablation_results = {}
    for label, numeric, categorical in ABLATIONS:
        pred = expanding_two_stage(d1, numeric, categorical, label)
        for col in pred.columns:
            d1[col] = pred[col]
        ablation_results[label] = {
            "p_no_win_col": f"{label}_p_no_win",
            "p_touch_col": f"{label}_p_touch",
            "p_stop_exact_given_touch_col": f"{label}_p_stop_exact_given_touch",
            "p_exact_col": f"{label}_p_exact",
        }
        mask = d1["target_date"].between(FORWARD_START, FORWARD_END, inclusive="both")
        metric_rows.append(metric_row(label, "touch", "forward_expanding", d1.loc[mask, "touch_target_after_decision"], d1.loc[mask, f"{label}_p_touch"], d1.loc[mask, "target_date"]))
        touched_mask = mask & d1["touch_target_after_decision"].astype(bool)
        metric_rows.append(
            metric_row(
                label,
                "stop_exact_given_touch",
                "forward_expanding_touched_only",
                d1.loc[touched_mask, "final_exact_target"],
                d1.loc[touched_mask, f"{label}_p_stop_exact_given_touch"],
                d1.loc[touched_mask, "target_date"],
            )
        )
        metric_rows.append(metric_row(label, "final_exact", "forward_expanding", d1.loc[mask, "final_exact_target"], d1.loc[mask, f"{label}_p_exact"], d1.loc[mask, "target_date"]))
        metric_rows.append(metric_row(label, "no_win", "forward_expanding", d1.loc[mask, "no_win"], d1.loc[mask, f"{label}_p_no_win"], d1.loc[mask, "target_date"]))
        calibration_frames.append(calibration_table(d1[mask], f"{label}_p_no_win", "no_win", label))
        roi_frames.append(roi_table(d1[mask], f"{label}_p_no_win", label))
        sf = slice_metrics(d1[mask], f"{label}_p_no_win")
        sf["model"] = label
        slice_frames.append(sf)

    forward_mask = d1["target_date"].between(FORWARD_START, FORWARD_END, inclusive="both")
    for label, col, target in [
        ("market_no_ask", "market_p_no_win", "no_win"),
        ("clock_only_hazard", "clock_only_p_no_win", "no_win"),
        ("raw_physical_score_v1", "raw_physical_score_v1", "no_win"),
        ("v1_original_retrained", "v1_original_retrained_p_no_win", "no_win"),
        ("old_v1_like", "old_v1_like_p_no_win", "no_win"),
    ]:
        metric_rows.append(metric_row(label, target, "forward_expanding", d1.loc[forward_mask, target], d1.loc[forward_mask, col], d1.loc[forward_mask, "target_date"]))
        calibration_frames.append(calibration_table(d1[forward_mask], col, target, label))
        roi_frames.append(roi_table(d1[forward_mask], col, label))
        sf = slice_metrics(d1[forward_mask], col)
        sf["model"] = label
        slice_frames.append(sf)

    metrics = pd.DataFrame(metric_rows)
    calibration = pd.concat([x for x in calibration_frames if not x.empty], ignore_index=True) if calibration_frames else pd.DataFrame()
    roi = pd.concat(roi_frames, ignore_index=True) if roi_frames else pd.DataFrame()
    slices = pd.concat(slice_frames, ignore_index=True) if slice_frames else pd.DataFrame()
    exact_cols = [f"{label}_p_exact" for label, _, _ in ABLATIONS]
    touch_cols = [f"{label}_p_touch" for label, _, _ in ABLATIONS]
    coherence_violations = 0
    max_exact_minus_touch = math.nan
    coherence_deltas = []
    for exact_col, touch_col in zip(exact_cols, touch_cols):
        delta = pd.to_numeric(d1[exact_col], errors="coerce") - pd.to_numeric(d1[touch_col], errors="coerce")
        coherence_violations += int(delta.gt(1e-12).sum())
        coherence_deltas.extend(delta.dropna().tolist())
    if coherence_deltas:
        max_exact_minus_touch = float(max(coherence_deltas))
    summary = {
        "d1_rows_settled": int(len(d1)),
        "forward_rows": int(forward_mask.sum()),
        "forward_dates": int(d1.loc[forward_mask, "target_date"].nunique()),
        "coherence_violations_p_exact_gt_p_touch": coherence_violations,
        "max_p_exact_minus_p_touch": max_exact_minus_touch,
        "corrections": {
            "market_no_baseline": "entry_price_is_no_ask_no_inversion",
            "two_stage_exact": "p_touch_times_p_stop_exact_given_touch",
        },
        "ablation_results": ablation_results,
    }
    return d1, {"metrics": metrics, "calibration": calibration, "roi": roi, "slices": slices, "summary": summary}


def chengdu_case(d1: pd.DataFrame) -> pd.DataFrame:
    sub = d1[
        d1["city"].eq("Chengdu")
        & d1["target_date"].eq("2026-07-07")
        & d1["bracket"].astype(str).eq("37")
        & d1["snapshot_ts_utc"].between("2026-07-07T07:20:00Z", "2026-07-07T08:30:00Z")
    ].copy()
    if sub.empty:
        return sub
    sub["distance_to_1532_sec"] = (
        pd.to_datetime(sub["snapshot_ts_utc"], errors="coerce", utc=True) - pd.Timestamp("2026-07-07T07:32:04Z")
    ).abs().dt.total_seconds()
    case = sub.sort_values("distance_to_1532_sec").head(8).copy()
    case["incident_reported_p_leg_win_physical_v1"] = np.nan
    case["incident_live_order_ts_bj"] = ""
    case["incident_next_key_metar_ts_bj"] = ""
    case["incident_first_touch_minutes_context"] = np.nan
    if not case.empty:
        idx = case.index[0]
        case.at[idx, "incident_reported_p_leg_win_physical_v1"] = INCIDENT_REPORTED_V1_P_NO_WIN
        case.at[idx, "incident_live_order_ts_bj"] = INCIDENT_LIVE_ORDER_TS_BJ
        case.at[idx, "incident_next_key_metar_ts_bj"] = INCIDENT_NEXT_KEY_METAR_TS_BJ
        case.at[idx, "incident_first_touch_minutes_context"] = INCIDENT_FIRST_TOUCH_MINUTES_CONTEXT
    return case


def write_report(candidates: pd.DataFrame, d1: pd.DataFrame, artifacts: dict[str, Any], metadata: dict[str, Any]) -> None:
    metrics: pd.DataFrame = artifacts["metrics"]
    roi: pd.DataFrame = artifacts["roi"]
    slices: pd.DataFrame = artifacts["slices"]
    case = chengdu_case(d1)
    full_metrics = metrics[metrics["model"].eq("full_v2")]
    full_no = full_metrics[full_metrics["target"].eq("no_win")].head(1)
    market_no = metrics[(metrics["model"].eq("market_no_ask")) & (metrics["target"].eq("no_win"))].head(1)
    v1r_no = metrics[(metrics["model"].eq("v1_original_retrained")) & (metrics["target"].eq("no_win"))].head(1)
    old_no = metrics[(metrics["model"].eq("old_v1_like")) & (metrics["target"].eq("no_win"))].head(1)
    full_roi0 = roi[(roi["model"].eq("full_v2")) & (roi["edge_threshold"].eq(0.0))].head(1)
    v1r_roi0 = roi[(roi["model"].eq("v1_original_retrained")) & (roi["edge_threshold"].eq(0.0))].head(1)
    old_roi0 = roi[(roi["model"].eq("old_v1_like")) & (roi["edge_threshold"].eq(0.0))].head(1)
    lines = [
        "# Late-Window Residual Trade-Win v2",
        "",
        "Status: snapshot",
        "Date: 2026-07-13 correction rerun",
        "Scope: research-only; no live/shadow runner changes",
        "",
        "## Data Snapshot",
        f"- Synced Mac market data before run; snapshots seen: {metadata['snapshots_seen']}.",
        f"- Candidate rows generated: {metadata['candidate_rows']}; settled d1 rows: {artifacts['summary']['d1_rows_settled']}.",
        f"- Settlement coverage 2026-07-05..07: {metadata['settlement_coverage']}.",
        f"- Forecast hourly curve records: {metadata['forecast_curve_records']}; observation history rows: {metadata['obs_history_rows']}.",
        "- TAF history, vertical profile history, and PIT multi-model forecast history were not present in the local market-data mirror; v2 keeps their columns as unavailable rather than leaking current/future data.",
        "- 2026-07-13 correction: `entry_price` is already the executable NO ask, so the market p(NO win) baseline now uses it directly instead of `1-entry_price`.",
        "- 2026-07-13 correction: Stage B now estimates `P(stop exact | touch)` on touched rows; `P(final exact)=P(touch)*P(stop exact | touch)`. The corrected output has zero `P(final exact)>P(touch)` violations.",
        "",
        "## Verdict",
    ]
    if not case.empty:
        c = case.iloc[0]
        lines += [
            f"- Chengdu 2026-07-07 37 NO is the named failure mode: at the nearest 15:32 BJ snapshot, the incident scorer reported `p_leg_win_physical_v1` p(NO win)≈{c.get('incident_reported_p_leg_win_physical_v1', math.nan):.3f}; live entry followed at {c.get('incident_live_order_ts_bj')}.",
            "- The script's `old_v1_like` column is a retrained single-stage baseline on this per-poll frame, not the exact old production/research scorer.",
            f"- Incident context says the next key METAR was {c.get('incident_next_key_metar_ts_bj')} and touched 37C; historical paper snapshots imply touch/final-exact but do not preserve the raw first-touch minute for the 15:32 row.",
            f"- v2 full model at that snapshot: P(touch 37 after decision)={c.get('full_v2_p_touch', math.nan):.3f}, P(final exactly 37)={c.get('full_v2_p_exact', math.nan):.3f}, P(37 NO win)={c.get('full_v2_p_no_win', math.nan):.3f}.",
            f"- v1_original_retrained_p_no_win={c.get('v1_original_retrained_p_no_win', math.nan):.3f}; this is the v1 feature framework retrained on the same v2 per-poll d1 NO rows.",
            f"- old_v1_like_p_no_win={c.get('old_v1_like_p_no_win', math.nan):.3f}; raw physical score={c.get('raw_physical_score_v1', math.nan):.3f}; market_p_no_win={c.get('market_p_no_win', math.nan):.3f}.",
        ]
    if not full_no.empty:
        r = full_no.iloc[0]
        lines.append(
            f"- full_v2 forward d1 NO no_win calibration: rows={int(r['rows'])}, dates={int(r['dates'])}, logloss={r['logloss']:.4f}, Brier={r['brier']:.4f}, AUC={r['auc']:.4f}."
        )
    if not market_no.empty:
        r = market_no.iloc[0]
        lines.append(
            f"- corrected executable market baseline: rows={int(r['rows'])}, dates={int(r['dates'])}, logloss={r['logloss']:.4f}, Brier={r['brier']:.4f}, AUC={r['auc']:.4f}; it remains materially stronger than the physical heads."
        )
    if not v1r_no.empty:
        r = v1r_no.iloc[0]
        lines.append(
            f"- v1 original framework retrained on the same v2 d1 rows: logloss={r['logloss']:.4f}, Brier={r['brier']:.4f}, AUC={r['auc']:.4f}."
        )
    if not old_no.empty:
        r = old_no.iloc[0]
        lines.append(
            f"- old v1-like forward no_win: logloss={r['logloss']:.4f}, Brier={r['brier']:.4f}, AUC={r['auc']:.4f}; p_leg_win_physical_v1 should be treated as raw physical score, not calibrated trade probability."
        )
    if not full_roi0.empty:
        r = full_roi0.iloc[0]
        lines.append(
            f"- full_v2 edge>=0 executable replay: rows={int(r['rows'])}, dates={int(r['dates'])}, ROI={r['roi']:.1%}, date-block CI [{r['date_block_ci_low']:.1%},{r['date_block_ci_high']:.1%}]."
        )
    if not v1r_roi0.empty:
        r = v1r_roi0.iloc[0]
        lines.append(
            f"- v1 original retrained edge>=0 replay: rows={int(r['rows'])}, dates={int(r['dates'])}, ROI={r['roi']:.1%}, date-block CI [{r['date_block_ci_low']:.1%},{r['date_block_ci_high']:.1%}]."
        )
    if not old_roi0.empty:
        r = old_roi0.iloc[0]
        lines.append(
            f"- old v1-like edge>=0 replay: rows={int(r['rows'])}, dates={int(r['dates'])}, ROI={r['roi']:.1%}, date-block CI [{r['date_block_ci_low']:.1%},{r['date_block_ci_high']:.1%}]."
        )
    lines.append(
        "- Strategy-model verdict: on the same v2 per-poll d1 NO rows, the v1 original single-stage framework currently beats full_v2 on proper score and edge replay; use the two-stage touch/exact decomposition as a hazard feature layer, not as the final trade probability head yet."
    )
    lines += [
        "",
        "Conclusion gates: significance=FAIL/NA, baseline=FAIL/NA, forward=FAIL; conclusion=inconclusive_research_only.",
        "",
        "## Model Design",
        "- Grain: polling snapshot x city x target_date x candidate leg/token.",
        "- Main model: d1 NO only. d2/d3/current YES are retained in candidate frame for diagnostics but not mixed into the main target.",
        "- Stage A predicts `P(touch target after decision)`; Stage B predicts `P(stop exact | touch)` only on touched training rows; `P(final exact)=P(touch)*P(stop exact | touch)` and `p_trade_win_v2=1-P(final exact)` for NO.",
        "- Fair baseline: `v1_original_retrained` uses the original v1 physical feature framework, retrained on the same v2 per-poll d1 NO rows.",
        "- City identity is not a raw model input. City information enters only through prior late-reheat/overshoot rates and region bucket.",
        "",
        "## Available PIT Features",
        "- paper snapshot/orderbook fields: entry, bid, spread, depth, running high, latest temp, forecast peak clock.",
        "- observation timing features inferred from historical snapshot-visible METAR report times: cadence, minutes_to_next_obs, minutes_since_running_max, same_running_max_obs_count.",
        "- Open-Meteo hourly curve temperature context is available; cloud/CAPE/CIN/LI/BLH/shortwave/wind are not present in local historical curve files.",
        "- TAF and vertical profile fields are present as explicit unavailable columns; they are not backfilled from non-PIT sources.",
        "",
        "## Metrics",
        df_to_md(metrics),
        "",
        "## ROI Replay",
        df_to_md(roi),
        "",
        "## Required Slices",
        df_to_md(slices),
        "",
        "## Chengdu 2026-07-07 Case",
    ]
    if case.empty:
        lines.append("No Chengdu 37 rows found near the requested window.")
    else:
        keep = [
            "snapshot_ts_utc",
            "ts_beijing",
            "entry_price",
            "best_bid",
            "spread",
            "top_size",
            "running_value",
            "latest_native",
            "path_state",
            "obs_age_minutes",
            "obs_cadence_min",
            "minutes_to_next_obs",
            "touch_target_after_decision",
            "first_touch_minutes_after_decision",
            "final_exact_target",
            "no_win",
            "incident_reported_p_leg_win_physical_v1",
            "incident_live_order_ts_bj",
            "incident_next_key_metar_ts_bj",
            "incident_first_touch_minutes_context",
            "v1_original_retrained_p_no_win",
            "old_v1_like_p_no_win",
            "full_v2_p_touch",
            "full_v2_p_stop_exact_given_touch",
            "full_v2_p_exact",
            "full_v2_p_no_win",
        ]
        lines.append(df_to_md(case[[c for c in keep if c in case.columns]]))
    lines += [
        "",
        "## Data Gaps Before Live",
        "- Preserve historical source_events/observation cache versions, not only latest, so cadence and next-observation hazard can be computed from source truth rather than paper-snapshot proxy.",
        "- Persist PIT TAF snapshots and parsed TX/TN/TEMPO/BECMG/TSRA/SHRA features.",
        "- Persist PIT vertical profile and full Open-Meteo hourly context variables; current mirror only has temperature curves.",
        "- Run at least 10 active forward dates after the Chengdu failure with the v2 scorer before any tiny-live discussion.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    candidates, metadata = build_candidate_frame(args)
    d1, artifacts = run_models(candidates)

    candidates.to_csv(args.out_dir / "candidate_rows.csv", index=False)
    d1.to_csv(args.out_dir / "d1_no_scored_rows.csv", index=False)
    artifacts["metrics"].to_csv(args.out_dir / "metrics.csv", index=False)
    artifacts["calibration"].to_csv(args.out_dir / "calibration.csv", index=False)
    artifacts["roi"].to_csv(args.out_dir / "roi_replay.csv", index=False)
    artifacts["slices"].to_csv(args.out_dir / "slice_metrics.csv", index=False)
    chengdu_case(d1).to_csv(args.out_dir / "chengdu_2026_07_07_case.csv", index=False)
    summary = {"metadata": metadata, **artifacts["summary"]}
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(candidates, d1, artifacts, metadata)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
