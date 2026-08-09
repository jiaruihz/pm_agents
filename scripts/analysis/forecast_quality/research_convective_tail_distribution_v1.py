#!/usr/bin/env python3
"""Falsify a Convective / Tail Distribution residual on the full HeadA universe.

The signal denominator is every legacy HeadA 5--20c YES candidate, selected or
not. Probability evaluation uses deterministic first-in-two-hour hybrid PIT
ladder states (immutable May/June snapshots plus canonical tmax_v2 after the
July cutover) with at least 80% direct two-sided rung coverage. Models use
expanding seven-target-date OOF folds and never train on a test target date.

This runner is research-only.  It does not emit TradeIntent, plans, or orders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.convective_tail_distribution import (
    BASE_FEATURES,
    CHALLENGERS,
    FeatureTransform,
    apply_challenger,
    fit_challenger,
    fit_feature_transform,
    normalize_market,
    score_from_artifact,
    transform_features,
)
from weather_model_evaluation.ladder_snapshot_history import load_history


DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_OUT = Path(
    "/Volumes/jrs-archive/pm_agents/research/artifact_store/"
    "convective_tail_distribution_v1/2026-08-09-may-retrain"
)
DEFAULT_HISTORY_SNAPSHOTS = Path(
    "/Volumes/jrs-archive/pm_agents/runtime/weather_edge_v1/market_data/"
    "paper_snapshots"
)
DEFAULT_SINGLE_RUNS = Path(
    "/Volumes/jrs-archive/pm_agents/runtime/weather_edge_v1/market_data/cache/"
    "open_meteo_single_runs_forecast"
)
DEFAULT_ATLAS_STATES = (
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/"
    "intraday_weather_regime_state_rows.csv"
)
DEFAULT_CORE_FROZEN_LEDGER = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_overshoot_risk_sizing_overlay_v1/opportunity_ledger.csv"
)
DEFAULT_CORE_RUNTIME_SCORES = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "current_yes_core_carry_tiny_live_v2/pre_live_scores.jsonl"
)
FEE_RATE = 0.05
MIN_TRAIN_DATES = 5
MIN_QUOTE_FRACTION = 0.80
OFFSET_WEATHER_WEIGHT = 0.30
BOOTSTRAP_DRAWS = 5000
CHALLENGER_REFIT_BLOCK_DATES = 7
SEED = 20260809
EPS = 5e-4
FEATURES = [
    "city_bias_pit_f",
    "source_bias_pit_f",
    "peak_pop_pct",
    "peak_cloud_pct",
    "peak_wind_kt",
    "relative_humidity_pct",
    "forecast_dispersion_f",
    "warming_innovation_f",
    "instant_innovation_f",
    "forecast_remaining_warming_f",
    "local_hour_sin",
    "local_hour_cos",
]
ATTRIBUTION_MODELS = (
    "c1_market_power_intercept_only",
    "c2_adjacent_kernel_intercept_only",
)
MODEL_NAMES = ("market", "weather", "offset", *CHALLENGERS, *ATTRIBUTION_MODELS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--start-date", default="2026-05-06")
    parser.add_argument("--end-date", default="2026-07-28")
    parser.add_argument("--history-start-date", default="2026-05-19")
    parser.add_argument("--history-cutover-date", default="2026-07-15")
    parser.add_argument("--history-snapshot-dir", type=Path, default=DEFAULT_HISTORY_SNAPSHOTS)
    parser.add_argument("--single-run-dir", type=Path, default=DEFAULT_SINGLE_RUNS)
    parser.add_argument("--atlas-states", type=Path, default=DEFAULT_ATLAS_STATES)
    parser.add_argument("--history-workers", type=int, default=12)
    parser.add_argument("--draws", type=int, default=BOOTSTRAP_DRAWS)
    parser.add_argument("--core-frozen-ledger", type=Path, default=DEFAULT_CORE_FROZEN_LEDGER)
    parser.add_argument("--core-runtime-scores", type=Path, default=DEFAULT_CORE_RUNTIME_SCORES)
    return parser.parse_args()


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def fee(price: float) -> float:
    return FEE_RATE * price * (1.0 - price)


def bracket_center(value: Any) -> float:
    nums = [float(x) for x in re.findall(r"(?<!\d)-?\d+(?:\.\d+)?", str(value))]
    if not nums:
        return math.nan
    if len(nums) >= 2 and "-" in str(value):
        return (nums[0] + nums[1]) / 2.0
    return nums[0]


def native_to_f(value: float, unit: str) -> float:
    return value if str(unit).upper() == "F" else value * 9.0 / 5.0 + 32.0


def interpolate(curve: list[dict[str, Any]], target_date: str, local_hour: float, field: str) -> float:
    points: list[tuple[float, float]] = []
    for item in curve:
        local = str(item.get("valid_time_local") or item.get("time_local") or "")
        value = item.get(field)
        if local[:10] != target_date or value is None:
            continue
        try:
            points.append((int(local[11:13]) + int(local[14:16]) / 60.0, float(value)))
        except (TypeError, ValueError):
            continue
    points.sort()
    if not points:
        return math.nan
    for hour, value in points:
        if abs(hour - local_hour) < 1e-9:
            return value
    for (lh, lv), (rh, rv) in zip(points, points[1:]):
        if lh < local_hour < rh:
            weight = (local_hour - lh) / (rh - lh)
            return lv + weight * (rv - lv)
    return math.nan


def peak_context(curve: list[dict[str, Any]], target_date: str) -> dict[str, float]:
    rows = [x for x in curve if str(x.get("valid_time_local") or x.get("time_local") or "")[:10] == target_date]
    temps = np.array([float(x.get("temperature_f", math.nan)) for x in rows], dtype=float)
    if not rows or not np.isfinite(temps).any():
        return {k: math.nan for k in ("forecast_max_f", "peak_pop_pct", "peak_cloud_pct", "peak_wind_kt")}
    peak = int(np.nanargmax(temps))
    window = rows[max(0, peak - 2) : min(len(rows), peak + 3)]
    def vals(field: str) -> np.ndarray:
        return np.array([float(x.get(field, math.nan)) for x in window], dtype=float)
    pop, cloud, wind = vals("precipitation_probability_pct"), vals("cloud_cover_pct"), vals("wind_speed_10m_kt")
    return {
        "forecast_max_f": float(np.nanmax(temps)),
        "peak_pop_pct": float(np.nanmax(pop)) if np.isfinite(pop).any() else math.nan,
        "peak_cloud_pct": float(np.nanmean(cloud)) if np.isfinite(cloud).any() else math.nan,
        "peak_wind_kt": float(np.nanmean(wind)) if np.isfinite(wind).any() else math.nan,
    }


def relative_humidity(temp_f: float, dewpoint_f: float) -> float:
    if not np.isfinite(temp_f) or not np.isfinite(dewpoint_f):
        return math.nan
    temp_c, dew_c = (temp_f - 32.0) * 5.0 / 9.0, (dewpoint_f - 32.0) * 5.0 / 9.0
    return float(100.0 * math.exp((17.625 * dew_c) / (243.04 + dew_c) - (17.625 * temp_c) / (243.04 + temp_c)))


def load_heada(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT candidate_id, condition_id, market_id, city, event_date AS target_date,
               bracket, unit, forecast_source, decision_snapshot_ts_utc,
               decision_entry_price, yes_depth_ask_5c, edge,
               COALESCE(eligible, 0) AS baseline_selected,
               settlement_status, final_yes
        FROM fact_signal_candidates
        WHERE candidate_grain_version = 'v1_legacy_daily'
          AND side = 'BUY_YES'
          AND decision_entry_price BETWEEN 0.05 AND 0.20
          AND COALESCE(decision_window_missing, 0) = 0
          AND event_date BETWEEN ? AND ?
        ORDER BY event_date, city, bracket
        """,
        conn,
        params=(start, end),
    )


def load_state_rows(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        WITH outcomes AS (
            SELECT city, target_date, bracket,
                   MAX(CASE WHEN final_price >= 0.999 THEN 1.0
                            WHEN final_price <= 0.001 THEN 0.0 ELSE final_price END) AS win
            FROM settlement_outcomes
            WHERE settlement_status = 'settled'
            GROUP BY city, target_date, bracket
        ), snapshot_quality AS (
            SELECT ladder_snapshot_id,
                   SUM(CASE WHEN yes_direct_bid BETWEEN 0.001 AND 0.999
                             AND yes_direct_ask BETWEEN 0.001 AND 0.999
                             AND yes_direct_ask >= yes_direct_bid THEN 1 ELSE 0 END) AS direct_two_sided
                   ,MAX(yes_book_fetched_at_utc) AS book_distribution_available_at_utc
            FROM tmax_v2_ladder_rung_quotes
            GROUP BY ladder_snapshot_id
        ), eligible AS (
            SELECT e.*, q.book_distribution_available_at_utc AS ladder_available_at_utc,
                   l.market_unit, l.market_timezone, l.market_utc_offset_seconds,
                   l.rung_count, 1.0*q.direct_two_sided/l.rung_count AS quote_fraction,
                   floor(
                     (julianday(datetime(q.book_distribution_available_at_utc, printf('%+d seconds', l.market_utc_offset_seconds)))
                      - julianday(e.target_date))*12.0
                   )*2.0 AS lifecycle_2h
            FROM tmax_v2_canonical_state_effective e
            JOIN tmax_v2_ladder_snapshots l USING (ladder_snapshot_id)
            JOIN snapshot_quality q USING (ladder_snapshot_id)
            WHERE e.pit_status='pit_verified'
              AND e.target_date BETWEEN ? AND ?
              AND 1.0*q.direct_two_sided/l.rung_count >= 0.80
        ), chosen AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY city,target_date,lifecycle_2h ORDER BY ladder_available_at_utc,tmax_state_id
            ) AS lifecycle_rank
            FROM eligible
        )
        SELECT e.tmax_state_id, e.city, e.target_date,
               e.decision_ts_utc AS feature_decision_ts_utc,
               e.ladder_available_at_utc AS decision_ts_utc,
               e.ladder_snapshot_id, e.forecast_capture_id, e.observation_event_id,
               e.market_unit, e.market_timezone, e.market_utc_offset_seconds, e.rung_count,
               e.quote_fraction, e.lifecycle_2h,
               o.obs_ts_utc, o.temp_f AS observed_temp_f,
               f.available_at_utc AS forecast_available_at_utc,
               f.forecast_source, f.forecast_model, f.normalized_hourly_curve_json,
               r.rung_quote_id, r.absolute_bracket_identity AS bracket,
               r.condition_id, r.yes_direct_bid AS yes_bid, r.yes_direct_ask AS yes_ask,
               r.yes_direct_ask_size AS yes_ask_size,
               r.yes_direct_depth_ask_5c AS yes_depth_ask_5c,
               r.yes_book_fetched_at_utc, q.win
        FROM chosen e
        JOIN tmax_v2_observation_event_lineage o
          ON o.tmax_v2_observation_id = e.observation_event_id
        JOIN tmax_v2_forecast_captures f USING (forecast_capture_id)
        JOIN tmax_v2_ladder_rung_quotes r USING (ladder_snapshot_id)
        LEFT JOIN outcomes q
          ON q.city=e.city AND q.target_date=e.target_date
         AND q.bracket=r.absolute_bracket_identity
        WHERE e.lifecycle_rank=1
          AND f.normalized_hourly_curve_json IS NOT NULL
        """,
        conn,
        params=(start, end),
    )


def load_legacy_state_rows(
    conn: sqlite3.Connection,
    snapshot_dir: Path,
    start: str,
    end: str,
    workers: int,
    cache_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Project immutable paper snapshots into the canonical rung contract.

    The legacy product predates tmax_v2.  Capture time is still PIT, direct
    books are native CLOB fields, and the embedded forecast is the value the
    old runner actually saw.  Only target-day two-hour checkpoints enter the
    probability denominator; pre-target snapshots remain represented in the
    raw HeadA coverage audit.
    """
    state_cache = cache_dir / "legacy_target_day_states.csv.gz"
    rung_cache = cache_dir / "legacy_target_day_rungs.csv.gz"
    coverage_cache = cache_dir / "legacy_history_coverage.json"
    if state_cache.exists() and rung_cache.exists() and coverage_cache.exists():
        states = pd.read_csv(state_cache)
        rungs = pd.read_csv(rung_cache, low_memory=False)
        states, rungs = apply_legacy_book_availability_clock(states, rungs)
        return states, rungs, json.loads(coverage_cache.read_text(encoding="utf-8"))

    snapshots, rungs, coverage = load_history(
        snapshot_dir,
        start,
        end,
        sample_seconds=7200,
        workers=workers,
    )
    if snapshots.empty:
        return pd.DataFrame(), pd.DataFrame(), coverage
    snapshots["decision_ts"] = pd.to_datetime(snapshots["source_snapshot_ts_utc"], utc=True)
    offsets = pd.to_numeric(snapshots["market_utc_offset_seconds"], errors="coerce").fillna(0)
    snapshots["local_ts"] = snapshots["decision_ts"] + pd.to_timedelta(offsets, unit="s")
    snapshots["local_hours"] = (
        snapshots["local_ts"].dt.tz_localize(None) - pd.to_datetime(snapshots["target_date"])
    ).dt.total_seconds() / 3600.0
    snapshots = snapshots[snapshots["local_hours"].between(0.0, 23.999)].copy()
    snapshots["lifecycle_2h"] = np.floor(snapshots["local_hours"] / 2.0) * 2.0
    snapshots = snapshots.sort_values(["city", "target_date", "lifecycle_2h", "decision_ts"])
    snapshots = snapshots.drop_duplicates(["city", "target_date", "event_identity", "lifecycle_2h"], keep="first")

    rungs["direct_two_sided"] = (
        rungs["yes_bid"].between(0.001, 0.999)
        & rungs["yes_ask"].between(0.001, 0.999)
        & rungs["yes_ask"].ge(rungs["yes_bid"])
    )
    quality = rungs.groupby("ladder_snapshot_id").agg(
        direct_two_sided=("direct_two_sided", "sum"),
        direct_rungs=("bracket", "size"),
    )
    quality["quote_fraction"] = quality["direct_two_sided"] / quality["direct_rungs"]
    snapshots = snapshots.merge(
        quality[["quote_fraction"]], left_on="ladder_snapshot_id", right_index=True, how="left"
    )
    snapshots = snapshots[snapshots["quote_fraction"].ge(MIN_QUOTE_FRACTION)].copy()
    selected_ids = set(snapshots["ladder_snapshot_id"].astype(str))
    rungs = rungs[rungs["ladder_snapshot_id"].astype(str).isin(selected_ids)].copy()

    outcomes = pd.read_sql_query(
        """SELECT city,target_date,bracket,
                  MAX(CASE WHEN final_price >= 0.999 THEN 1.0
                           WHEN final_price <= 0.001 THEN 0.0 ELSE final_price END) AS win
           FROM settlement_outcomes
           WHERE settlement_status='settled' AND target_date BETWEEN ? AND ?
           GROUP BY city,target_date,bracket""",
        conn,
        params=(start, end),
    )
    meta_columns = [
        "ladder_snapshot_id", "city", "target_date", "source_snapshot_ts_utc",
        "market_unit", "market_timezone", "market_utc_offset_seconds", "rung_count",
        "quote_fraction", "lifecycle_2h", "local_hours", "forecast_capture_id",
        "forecast_model", "forecast_peak_f", "forecast_peak_shock_f", "forecast_age_min",
        "observed_max_f", "observed_max_shock_f", "legacy_latest_obs_ts_utc",
        "legacy_metar_source", "source_path",
    ]
    states = snapshots[meta_columns].rename(
        columns={
            "ladder_snapshot_id": "tmax_state_id",
            "source_snapshot_ts_utc": "decision_ts_utc",
            "forecast_peak_f": "legacy_forecast_max_f",
            "legacy_latest_obs_ts_utc": "obs_ts_utc",
        }
    )
    states["feature_decision_ts_utc"] = states["decision_ts_utc"]
    states["forecast_available_at_utc"] = states["decision_ts_utc"]
    states["forecast_source"] = states["forecast_model"]
    states["observation_event_id"] = states["obs_ts_utc"].map(
        lambda value: "legacy_obs:" + str(value) if pd.notna(value) else None
    )
    states["observed_temp_f"] = states["observed_max_f"]
    states["normalized_hourly_curve_json"] = None
    states["state_source"] = "immutable_paper_snapshot"

    rungs = rungs.rename(columns={"ladder_snapshot_id": "tmax_state_id"})
    raw = rungs.merge(states, on="tmax_state_id", how="inner", validate="many_to_one")
    raw = raw.merge(outcomes, on=["city", "target_date", "bracket"], how="left")
    raw["rung_quote_id"] = raw.apply(
        lambda row: "legacy_rung:"
        + hashlib.sha256(
            f"{row['tmax_state_id']}|{row['bracket']}|{row['condition_id']}".encode()
        ).hexdigest(),
        axis=1,
    )
    coverage.update(
        {
            "target_day_quote80_states": int(len(states)),
            "target_day_quote80_dates": int(states["target_date"].nunique()),
            "target_day_quote80_cities": int(states["city"].nunique()),
            "target_day_quote90_fraction": float(states["quote_fraction"].ge(0.90).mean()),
        }
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    states, raw = apply_legacy_book_availability_clock(states, raw)
    states.to_csv(state_cache, index=False, compression="gzip")
    raw.to_csv(rung_cache, index=False, compression="gzip")
    coverage_cache.write_text(json.dumps(coverage, indent=2) + "\n", encoding="utf-8")
    return states, raw, coverage


def apply_legacy_book_availability_clock(
    states: pd.DataFrame,
    raw: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The ladder exists only after the final direct book fetch completes."""
    states = states.copy()
    raw = raw.copy()
    yes = pd.to_datetime(raw["yes_book_fetched_at_utc"], utc=True, errors="coerce")
    no = pd.to_datetime(raw["no_book_fetched_at_utc"], utc=True, errors="coerce")
    raw["book_available_ts"] = pd.concat([yes, no], axis=1).max(axis=1)
    available = raw.groupby("tmax_state_id")["book_available_ts"].max()
    fallback = pd.to_datetime(states["decision_ts_utc"], utc=True, errors="coerce")
    state_available = states["tmax_state_id"].map(available)
    states["decision_ts_utc"] = state_available.fillna(fallback).map(
        lambda value: value.isoformat().replace("+00:00", "Z") if pd.notna(value) else None
    )
    raw["decision_ts_utc"] = raw["tmax_state_id"].map(states.set_index("tmax_state_id")["decision_ts_utc"])
    decision = pd.to_datetime(raw["decision_ts_utc"], utc=True, errors="coerce")
    offsets = pd.to_numeric(raw["market_utc_offset_seconds"], errors="coerce").fillna(0)
    local = decision + pd.to_timedelta(offsets, unit="s")
    local_hours = (local.dt.tz_localize(None) - pd.to_datetime(raw["target_date"])).dt.total_seconds() / 3600.0
    raw["lifecycle_2h"] = np.floor(local_hours / 2.0) * 2.0
    states["lifecycle_2h"] = states["tmax_state_id"].map(
        raw.drop_duplicates("tmax_state_id").set_index("tmax_state_id")["lifecycle_2h"]
    )
    return states, raw.drop(columns=["book_available_ts"])


def load_observation_context(conn: sqlite3.Connection, start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    lineage = pd.read_sql_query(
        """SELECT city,target_date,obs_ts_utc,available_at_utc,temp_f
           FROM tmax_v2_observation_event_lineage
           WHERE lineage_status='pit_verified_first_seen' AND target_date BETWEEN ? AND ?""",
        conn,
        params=(start, end),
    )
    weather = pd.read_sql_query(
        """SELECT city,target_date,obs_ts_utc,temp_f,dewpoint_f
           FROM weather_observation_events WHERE target_date BETWEEN ? AND ?""",
        conn,
        params=(start, end),
    )
    return lineage, weather


def prepare_states(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = raw.copy()
    raw["decision_ts"] = pd.to_datetime(raw["decision_ts_utc"], utc=True)
    raw["yes_mid"] = (pd.to_numeric(raw["yes_bid"], errors="coerce") + pd.to_numeric(raw["yes_ask"], errors="coerce")) / 2.0
    raw["direct_two_sided"] = raw["yes_bid"].between(0.001, 0.999) & raw["yes_ask"].between(0.001, 0.999) & raw["yes_ask"].ge(raw["yes_bid"])
    raw["book_age_min"] = (
        raw["decision_ts"] - pd.to_datetime(raw["yes_book_fetched_at_utc"], utc=True, errors="coerce")
    ).dt.total_seconds() / 60.0
    meta = raw.drop_duplicates("tmax_state_id").copy()
    age = raw[raw["direct_two_sided"]].groupby("tmax_state_id")["book_age_min"].median()
    meta["book_age_median_min"] = meta["tmax_state_id"].map(age)
    offsets = pd.to_numeric(meta["market_utc_offset_seconds"], errors="coerce").fillna(0)
    meta["local_ts"] = meta["decision_ts"] + pd.to_timedelta(offsets, unit="s")
    meta["local_hours"] = (meta["local_ts"].dt.tz_localize(None) - pd.to_datetime(meta["target_date"])).dt.total_seconds() / 3600.0
    meta = meta[meta["quote_fraction"].ge(MIN_QUOTE_FRACTION)].sort_values("decision_ts")
    if "state_source" not in meta:
        meta["state_source"] = "unknown"
    meta = meta.drop_duplicates(
        ["city", "target_date", "lifecycle_2h", "state_source"], keep="first"
    )
    chosen = meta
    selected = raw[raw["tmax_state_id"].isin(chosen["tmax_state_id"])].drop(
        columns=["local_hours", "book_age_median_min"], errors="ignore"
    ).merge(
        chosen[["tmax_state_id", "local_hours", "book_age_median_min"]], on="tmax_state_id", how="left"
    )
    selected["bracket_center_native"] = selected["bracket"].map(bracket_center)
    selected["bracket_center_f"] = [native_to_f(v, u) for v, u in zip(selected["bracket_center_native"], selected["market_unit"], strict=True)]
    return selected, chosen


def add_features(states: pd.DataFrame, lineage: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    meta = states.drop_duplicates("tmax_state_id").copy()
    lineage = lineage.copy()
    lineage["available_ts"] = pd.to_datetime(lineage["available_at_utc"], utc=True)
    lineage["obs_ts"] = pd.to_datetime(lineage["obs_ts_utc"], utc=True)
    weather_key = weather.drop_duplicates(["city", "target_date", "obs_ts_utc"]).set_index(["city", "target_date", "obs_ts_utc"])
    lineage_groups = {key: frame.sort_values(["available_ts", "obs_ts"]) for key, frame in lineage.groupby(["city", "target_date"])}
    records: list[dict[str, Any]] = []
    for row in meta.to_dict("records"):
        curve = json.loads(row["normalized_hourly_curve_json"])
        context = peak_context(curve, row["target_date"])
        decision = pd.Timestamp(row["feature_decision_ts_utc"])
        local_hour = float(row["local_hours"])
        obs_ts = pd.Timestamp(row["obs_ts_utc"])
        obs_local_hour = local_hour - (decision - obs_ts).total_seconds() / 3600.0
        model_now = interpolate(curve, row["target_date"], obs_local_hour, "temperature_f")
        eligible_obs = lineage_groups.get((row["city"], row["target_date"]), pd.DataFrame())
        eligible_obs = eligible_obs[(eligible_obs["available_ts"] <= decision) & (eligible_obs["obs_ts"] <= decision)] if not eligible_obs.empty else eligible_obs
        first = eligible_obs.sort_values("obs_ts").iloc[0] if not eligible_obs.empty else None
        warming = math.nan
        if first is not None:
            first_local_hour = local_hour - (decision - first["obs_ts"]).total_seconds() / 3600.0
            model_first = interpolate(curve, row["target_date"], first_local_hour, "temperature_f")
            if np.isfinite(model_first) and np.isfinite(model_now):
                warming = (float(row["observed_temp_f"]) - float(first["temp_f"])) - (model_now - model_first)
        dewpoint = math.nan
        key = (row["city"], row["target_date"], row["obs_ts_utc"])
        if key in weather_key.index:
            dewpoint = float(weather_key.loc[key]["dewpoint_f"])
        record = dict(row)
        record.update(context)
        record.update({
            "forecast_temperature_at_obs_f": model_now,
            "instant_innovation_f": float(row["observed_temp_f"]) - model_now if np.isfinite(model_now) else math.nan,
            "warming_innovation_f": warming,
            "forecast_remaining_warming_f": context["forecast_max_f"] - model_now if np.isfinite(model_now) else math.nan,
            "relative_humidity_pct": relative_humidity(float(row["observed_temp_f"]), dewpoint),
            "local_hour_sin": math.sin(2.0 * math.pi * local_hour / 24.0),
            "local_hour_cos": math.cos(2.0 * math.pi * local_hour / 24.0),
        })
        records.append(record)
    out = pd.DataFrame(records).sort_values(["city", "target_date", "decision_ts_utc"])
    out["forecast_dispersion_f"] = 0.0
    out["forecast_vintages_24h"] = 1
    for _, group in out.groupby(["city", "target_date"]):
        times = pd.to_datetime(group["feature_decision_ts_utc"], utc=True)
        values = group["forecast_max_f"].to_numpy(float)
        for position, index in enumerate(group.index):
            keep = (times.iloc[: position + 1] >= times.iloc[position] - pd.Timedelta(hours=24)).to_numpy()
            vintage = values[: position + 1][keep]
            out.loc[index, "forecast_vintages_24h"] = len(vintage)
            out.loc[index, "forecast_dispersion_f"] = float(np.std(vintage, ddof=1)) if len(vintage) >= 2 else 0.0
    return out


def _load_single_run_curve(
    cache_dir: Path,
    city: str,
    target_date: str,
    model: str,
    decision_ts: pd.Timestamp,
) -> list[tuple[float, float]]:
    candidates = sorted(cache_dir.glob(f"{model}_{city}_{target_date}_run_*.json"))
    eligible: list[Path] = []
    for path in candidates:
        match = re.search(r"_run_(\d{8}T\d{4})Z\.json$", path.name)
        if not match:
            continue
        run_ts = pd.to_datetime(match.group(1), format="%Y%m%dT%H%M", utc=True)
        # A conservative six-hour publication lag keeps the historical run PIT.
        if run_ts + pd.Timedelta(hours=6) <= decision_ts:
            eligible.append(path)
    if not eligible:
        return []
    try:
        payload = json.loads(eligible[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    values = hourly.get("temperature_2m") or []
    points: list[tuple[float, float]] = []
    for time_text, value in zip(times, values, strict=False):
        if str(time_text)[:10] != target_date:
            continue
        try:
            points.append((int(str(time_text)[11:13]) + int(str(time_text)[14:16]) / 60.0, float(value)))
        except (TypeError, ValueError):
            continue
    return sorted(points)


def _curve_value(points: list[tuple[float, float]], hour: float) -> float:
    if not points or not np.isfinite(hour):
        return math.nan
    for x, value in points:
        if abs(x - hour) < 1e-9:
            return value
    for (left, lv), (right, rv) in zip(points, points[1:]):
        if left < hour < right:
            return lv + (rv - lv) * (hour - left) / (right - left)
    return math.nan


def load_atlas_states(path: Path, start: str, end: str) -> pd.DataFrame:
    columns = [
        "city", "target_date", "decision_snapshot_ts_utc", "tmpf_now", "running_max_f",
        "relative_humidity_pct", "wind_speed_kt", "sky_cover_code",
    ]
    frame = pd.read_csv(path, usecols=columns)
    frame = frame[frame["target_date"].between(start, end)].copy()
    frame["atlas_ts"] = pd.to_datetime(frame["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    return frame.dropna(subset=["atlas_ts"]).sort_values(["city", "target_date", "atlas_ts"])


def add_legacy_features(states: pd.DataFrame, atlas: pd.DataFrame, single_run_dir: Path) -> pd.DataFrame:
    """Attach only weather observations/runs available by each legacy checkpoint."""
    meta = states.drop_duplicates("tmax_state_id").copy()
    meta["decision_ts"] = pd.to_datetime(meta["feature_decision_ts_utc"], utc=True)
    matched: list[pd.DataFrame] = []
    atlas_groups = {
        key: group.sort_values("atlas_ts")
        for key, group in atlas.groupby(["city", "target_date"], sort=False)
    }
    for key, group in meta.groupby(["city", "target_date"], sort=False):
        left = group.sort_values("decision_ts")
        right = atlas_groups.get(key)
        if right is None or right.empty:
            for column in (
                "atlas_ts", "tmpf_now", "running_max_f", "relative_humidity_pct",
                "wind_speed_kt", "sky_cover_code",
            ):
                left[column] = pd.NaT if column == "atlas_ts" else math.nan
            matched.append(left)
            continue
        matched.append(
            pd.merge_asof(
                left,
                right[[
                    "atlas_ts", "tmpf_now", "running_max_f", "relative_humidity_pct",
                    "wind_speed_kt", "sky_cover_code",
                ]].sort_values("atlas_ts"),
                left_on="decision_ts",
                right_on="atlas_ts",
                direction="backward",
                tolerance=pd.Timedelta(hours=2),
            )
        )
    out = pd.concat(matched, ignore_index=True).sort_values(["city", "target_date", "decision_ts"])
    out["forecast_max_f"] = pd.to_numeric(out["legacy_forecast_max_f"], errors="coerce")
    out["peak_pop_pct"] = math.nan
    # Legacy observations encode CLR/FEW/SCT/BKN/OVC on a stable 0--4 scale.
    out["peak_cloud_pct"] = pd.to_numeric(out["sky_cover_code"], errors="coerce") * 25.0
    out["peak_wind_kt"] = pd.to_numeric(out["wind_speed_kt"], errors="coerce")
    out["forecast_temperature_at_obs_f"] = math.nan
    out["instant_innovation_f"] = math.nan
    out["warming_innovation_f"] = math.nan
    out["forecast_remaining_warming_f"] = math.nan
    out["forecast_curve_vintage"] = "d_minus_1_12z_single_run_six_hour_lag"
    key_decisions: dict[tuple[str, str, str], pd.Timestamp] = {}
    for row in out.dropna(subset=["tmpf_now"]).to_dict("records"):
        key = (str(row["city"]), str(row["target_date"]), str(row["forecast_model"]))
        decision = pd.Timestamp(row["decision_ts"])
        if key not in key_decisions or decision < key_decisions[key]:
            key_decisions[key] = decision

    def read_curve(item: tuple[tuple[str, str, str], pd.Timestamp]) -> tuple[tuple[str, str, str], list[tuple[float, float]]]:
        key, decision = item
        return key, _load_single_run_curve(single_run_dir, key[0], key[1], key[2], decision)

    with ThreadPoolExecutor(max_workers=12) as executor:
        curve_cache = dict(executor.map(read_curve, key_decisions.items(), chunksize=8))
    first_observation: dict[tuple[str, str], tuple[float, float]] = {}
    for index, row in out.iterrows():
        if pd.isna(row.get("tmpf_now")):
            continue
        decision = pd.Timestamp(row["decision_ts"])
        key = (str(row["city"]), str(row["target_date"]), str(row["forecast_model"]))
        model_now = _curve_value(curve_cache.get(key, []), float(row["local_hours"]))
        if not np.isfinite(model_now):
            continue
        out.loc[index, "forecast_temperature_at_obs_f"] = model_now
        out.loc[index, "instant_innovation_f"] = float(row["tmpf_now"]) - model_now
        out.loc[index, "forecast_remaining_warming_f"] = float(row["forecast_max_f"]) - model_now
        day_key = (str(row["city"]), str(row["target_date"]))
        if day_key not in first_observation:
            first_observation[day_key] = (float(row["tmpf_now"]), model_now)
        first_temp, first_model = first_observation[day_key]
        out.loc[index, "warming_innovation_f"] = (
            (float(row["tmpf_now"]) - first_temp) - (model_now - first_model)
        )
    out["forecast_dispersion_f"] = 0.0
    out["forecast_vintages_24h"] = 1
    for _, group in out.groupby(["city", "target_date"]):
        times = group["decision_ts"]
        values = group["forecast_max_f"].to_numpy(float)
        for position, index in enumerate(group.index):
            keep = (times.iloc[: position + 1] >= times.iloc[position] - pd.Timedelta(hours=24)).to_numpy()
            vintage = values[: position + 1][keep]
            out.loc[index, "forecast_vintages_24h"] = len(vintage)
            out.loc[index, "forecast_dispersion_f"] = float(np.std(vintage, ddof=1)) if len(vintage) >= 2 else 0.0
    out["local_hour_sin"] = np.sin(2.0 * np.pi * out["local_hours"] / 24.0)
    out["local_hour_cos"] = np.cos(2.0 * np.pi * out["local_hours"] / 24.0)
    return out


def add_outcome_and_bias(features: pd.DataFrame, rungs: pd.DataFrame) -> pd.DataFrame:
    winners = rungs[rungs["win"].ge(0.999)].sort_values("bracket_center_f").drop_duplicates("tmax_state_id")
    winner_map = winners.set_index("tmax_state_id")["bracket_center_f"]
    features["settlement_center_f"] = features["tmax_state_id"].map(winner_map)
    features["forecast_error_f"] = features["settlement_center_f"] - features["forecast_max_f"]
    features["city_bias_pit_f"] = math.nan
    features["source_bias_pit_f"] = math.nan
    for date in sorted(features["target_date"].unique()):
        prior = features[features["target_date"].lt(date)].dropna(subset=["forecast_error_f"])
        test = features[features["target_date"].eq(date)]
        global_bias = float(prior["forecast_error_f"].mean()) if len(prior) else 0.0
        city_map = prior.groupby("city")["forecast_error_f"].mean()
        source_map = prior.groupby("forecast_model")["forecast_error_f"].mean()
        features.loc[test.index, "city_bias_pit_f"] = test["city"].map(city_map).fillna(global_bias)
        features.loc[test.index, "source_bias_pit_f"] = test["forecast_model"].map(source_map).fillna(global_bias)
    return features


def adapter_parity(
    legacy_features: pd.DataFrame,
    legacy_rungs: pd.DataFrame,
    canonical_features: pd.DataFrame,
    canonical_rungs: pd.DataFrame,
) -> pd.DataFrame:
    """Compare old/new adapters at the same city/date/two-hour lifecycle."""
    if legacy_features.empty or canonical_features.empty:
        return pd.DataFrame()
    left = legacy_features.sort_values("decision_ts_utc").drop_duplicates(
        ["city", "target_date", "lifecycle_2h"], keep="first"
    )
    right = canonical_features.sort_values("decision_ts_utc").drop_duplicates(
        ["city", "target_date", "lifecycle_2h"], keep="first"
    )
    pairs = left.merge(
        right,
        on=["city", "target_date", "lifecycle_2h"],
        suffixes=("_legacy", "_canonical"),
    )
    legacy_groups = {
        str(key): group for key, group in legacy_rungs.groupby("tmax_state_id")
    }
    canonical_groups = {
        str(key): group for key, group in canonical_rungs.groupby("tmax_state_id")
    }
    rows: list[dict[str, Any]] = []
    for pair in pairs.to_dict("records"):
        legacy = legacy_groups[str(pair["tmax_state_id_legacy"])].copy()
        canonical = canonical_groups[str(pair["tmax_state_id_canonical"])].copy()
        merged = legacy[["condition_id", "yes_mid", "direct_two_sided"]].merge(
            canonical[["condition_id", "yes_mid", "direct_two_sided"]],
            on="condition_id",
            suffixes=("_legacy", "_canonical"),
        )
        merged = merged[merged["direct_two_sided_legacy"] & merged["direct_two_sided_canonical"]]
        l1 = math.nan
        if len(merged) >= 3:
            old = normalize_market(merged["yes_mid_legacy"].to_numpy(float))
            new = normalize_market(merged["yes_mid_canonical"].to_numpy(float))
            l1 = float(np.abs(old - new).sum())
        rows.append(
            {
                "city": pair["city"],
                "target_date": pair["target_date"],
                "lifecycle_2h": pair["lifecycle_2h"],
                "legacy_state_id": pair["tmax_state_id_legacy"],
                "canonical_state_id": pair["tmax_state_id_canonical"],
                "shared_direct_rungs": len(merged),
                "normalized_market_l1": l1,
                "forecast_peak_delta_f": float(pair["forecast_max_f_legacy"] - pair["forecast_max_f_canonical"]),
                "decision_time_delta_min": abs(
                    (
                        pd.Timestamp(pair["decision_ts_utc_legacy"])
                        - pd.Timestamp(pair["decision_ts_utc_canonical"])
                    ).total_seconds()
                    / 60.0
                ),
            }
        )
    return pd.DataFrame(rows)


def distribution(centers: np.ndarray, mean_f: float, sigma_f: float) -> np.ndarray:
    order = np.argsort(centers)
    sorted_centers = centers[order]
    cuts = (sorted_centers[:-1] + sorted_centers[1:]) / 2.0
    bounds = np.concatenate(([-np.inf], cuts, [np.inf]))
    probs_sorted = np.diff(norm.cdf(bounds, loc=mean_f, scale=max(sigma_f, 0.5)))
    probs = np.empty_like(probs_sorted)
    probs[order] = probs_sorted
    return np.clip(probs, EPS, None) / np.clip(probs, EPS, None).sum()


def expanding_oof(features: pd.DataFrame, rungs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rung_groups = {key: frame.sort_values("bracket_center_f") for key, frame in rungs.groupby("tmax_state_id")}
    pred_rows: list[dict[str, Any]] = []
    state_rows: list[dict[str, Any]] = []
    dates = sorted(features["target_date"].unique())
    active_features = [column for column in FEATURES if features[column].notna().any()]
    for date in dates:
        train_dates = [d for d in dates if d < date]
        if len(train_dates) < MIN_TRAIN_DATES:
            continue
        train = features[features["target_date"].isin(train_dates)].dropna(subset=["forecast_error_f"])
        test = features[features["target_date"].eq(date)]
        if train.empty or test.empty:
            continue
        mean_model = make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(), Ridge(alpha=20.0))
        mean_model.fit(train[active_features], train["forecast_error_f"])
        train_pred = mean_model.predict(train[active_features])
        scale_target = np.log(np.abs(train["forecast_error_f"].to_numpy() - train_pred) + 0.5)
        scale_model = make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(), Ridge(alpha=30.0))
        scale_model.fit(train[active_features], scale_target)
        mean_error = mean_model.predict(test[active_features])
        sigma = np.clip(np.exp(scale_model.predict(test[active_features])) * 1.253, 0.8, 8.0)
        for test_row, correction, sigma_f in zip(test.to_dict("records"), mean_error, sigma, strict=True):
            group = rung_groups[test_row["tmax_state_id"]].copy()
            centers = group["bracket_center_f"].to_numpy(float)
            market = group["yes_mid"].where(group["direct_two_sided"], EPS).fillna(EPS).clip(EPS, 1.0 - EPS).to_numpy(float)
            market = market / market.sum()
            weather = distribution(centers, test_row["forecast_max_f"] + correction, float(sigma_f))
            offset = np.exp((1.0 - OFFSET_WEATHER_WEIGHT) * np.log(market) + OFFSET_WEATHER_WEIGHT * np.log(weather))
            offset /= offset.sum()
            actual = group["win"].fillna(0.0).to_numpy(float)
            if actual.sum() < 0.999:
                continue
            actual /= actual.sum()
            for idx, (_, rung) in enumerate(group.iterrows()):
                forecast_native = test_row["forecast_max_f"] if str(rung["market_unit"]).upper() == "F" else (test_row["forecast_max_f"] - 32.0) * 5.0 / 9.0
                pred_rows.append({
                    "tmax_state_id": test_row["tmax_state_id"], "city": test_row["city"],
                    "target_date": date, "decision_ts_utc": test_row["decision_ts_utc"],
                    "lifecycle_2h": test_row["lifecycle_2h"], "forecast_model": test_row["forecast_model"],
                    "bracket": rung["bracket"], "condition_id": rung["condition_id"], "rank": idx,
                    "settlement_native_bracket_distance": float(rung["bracket_center_native"] - forecast_native),
                    "win": actual[idx], "p_market": market[idx], "p_weather": weather[idx], "p_offset": offset[idx],
                    "yes_ask": rung["yes_ask"], "yes_ask_size": rung["yes_ask_size"],
                    "yes_depth_ask_5c": rung["yes_depth_ask_5c"], "direct_two_sided": bool(rung["direct_two_sided"]),
                })
            state_rows.append({
                **{k: test_row[k] for k in ["tmax_state_id", "city", "target_date", "decision_ts_utc", "lifecycle_2h", "forecast_model", "quote_fraction"]},
                "predicted_center_f": test_row["forecast_max_f"] + correction, "predicted_sigma_f": float(sigma_f),
                "peak_pop_pct": test_row["peak_pop_pct"], "book_age_median_min": test_row["book_age_median_min"],
                "train_dates": len(train_dates),
            })
    return pd.DataFrame(pred_rows), pd.DataFrame(state_rows)


def _state_payloads(
    feature_rows: pd.DataFrame,
    rung_groups: dict[str, pd.DataFrame],
) -> tuple[list[dict[str, Any]], list[np.ndarray], list[np.ndarray], list[np.ndarray]]:
    rows: list[dict[str, Any]] = []
    markets: list[np.ndarray] = []
    outcomes: list[np.ndarray] = []
    distances: list[np.ndarray] = []
    for row in feature_rows.to_dict("records"):
        group = rung_groups.get(str(row["tmax_state_id"]))
        if group is None:
            continue
        actual = group["win"].fillna(0.0).to_numpy(float)
        if actual.sum() < 0.999:
            continue
        actual /= actual.sum()
        market = normalize_market(
            group["yes_mid"].where(group["direct_two_sided"], EPS).fillna(EPS).to_numpy(float)
        )
        forecast_native = (
            float(row["forecast_max_f"])
            if str(group.iloc[0]["market_unit"]).upper() == "F"
            else (float(row["forecast_max_f"]) - 32.0) * 5.0 / 9.0
        )
        rows.append(row)
        markets.append(market)
        outcomes.append(actual)
        distances.append(group["bracket_center_native"].to_numpy(float) - forecast_native)
    return rows, markets, outcomes, distances


def challenger_oof(
    features: pd.DataFrame,
    rungs: pd.DataFrame,
    base_predictions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run every preregistered challenger on the same expanding-date states."""
    rung_groups = {
        str(key): frame.sort_values("bracket_center_f").reset_index(drop=True)
        for key, frame in rungs.groupby("tmax_state_id")
    }
    dates = sorted(features["target_date"].unique())
    oof_dates = sorted(base_predictions["target_date"].unique())
    probability_rows: list[dict[str, Any]] = []
    fit_rows: list[dict[str, Any]] = []
    eligible_oof_ids = set(base_predictions["tmax_state_id"].astype(str))
    for block_start in range(0, len(oof_dates), CHALLENGER_REFIT_BLOCK_DATES):
        test_dates = oof_dates[block_start : block_start + CHALLENGER_REFIT_BLOCK_DATES]
        fold_start = test_dates[0]
        train_dates = [value for value in dates if value < fold_start]
        if len(train_dates) < MIN_TRAIN_DATES:
            continue
        train_frame = features[features["target_date"].isin(train_dates)]
        test_frame = features[features["target_date"].isin(test_dates)]
        train_rows, train_market, train_outcome, train_distance = _state_payloads(train_frame, rung_groups)
        test_rows, test_market, _, test_distance = _state_payloads(test_frame, rung_groups)
        if not train_rows or not test_rows:
            continue
        transform = fit_feature_transform(train_rows)
        x_train = transform_features(train_rows, transform)
        x_test = transform_features(test_rows, transform)
        date_counts = pd.Series([row["target_date"] for row in train_rows]).value_counts()
        weights = np.array([1.0 / float(date_counts[row["target_date"]]) for row in train_rows], dtype=float)
        fitted: dict[str, np.ndarray] = {}
        for challenger in CHALLENGERS:
            parameters, fit = fit_challenger(
                challenger,
                x_train,
                train_market,
                train_outcome,
                train_distance,
                weights,
            )
            fitted[challenger] = parameters
            fit_rows.append(
                {
                    "test_date_start": test_dates[0],
                    "test_date_end": test_dates[-1],
                    "test_dates": len(test_dates),
                    "challenger": challenger,
                    "train_dates": len(train_dates),
                    "train_states": len(train_rows),
                    "parameter_count": len(parameters),
                    **fit,
                }
            )
        intercept_parameters, intercept_fit = fit_challenger(
            "c1_market_power_temperature",
            np.empty((len(train_rows), 0)),
            train_market,
            train_outcome,
            train_distance,
            weights,
        )
        fit_rows.append({
            "test_date_start": test_dates[0],
            "test_date_end": test_dates[-1],
            "test_dates": len(test_dates),
            "challenger": "c1_market_power_intercept_only",
            "train_dates": len(train_dates),
            "train_states": len(train_rows),
            "parameter_count": len(intercept_parameters),
            "attribution_only": True,
            **intercept_fit,
        })
        c2_intercept_parameters, c2_intercept_fit = fit_challenger(
            "c2_adjacent_kernel_diffusion",
            np.empty((len(train_rows), 0)),
            train_market,
            train_outcome,
            train_distance,
            weights,
        )
        fit_rows.append({
            "test_date_start": test_dates[0],
            "test_date_end": test_dates[-1],
            "test_dates": len(test_dates),
            "challenger": "c2_adjacent_kernel_intercept_only",
            "train_dates": len(train_dates),
            "train_states": len(train_rows),
            "parameter_count": len(c2_intercept_parameters),
            "attribution_only": True,
            **c2_intercept_fit,
        })
        for index, row in enumerate(test_rows):
            state_id = str(row["tmax_state_id"])
            if state_id not in eligible_oof_ids:
                continue
            group = rung_groups[state_id]
            for challenger, parameters in fitted.items():
                artifact = {
                    "selected_challenger": challenger,
                    "feature_transform": transform.to_dict(),
                    "parameters": parameters.tolist(),
                }
                probabilities = score_from_artifact(
                    artifact,
                    row,
                    test_market[index],
                    test_distance[index],
                )
                for rank, probability in enumerate(probabilities):
                    probability_rows.append(
                        {
                            "tmax_state_id": row["tmax_state_id"],
                            "rank": rank,
                            "challenger": challenger,
                            "probability": float(probability),
                        }
                    )
            intercept_probability = apply_challenger(
                "c1_market_power_temperature",
                test_market[index],
                [],
                intercept_parameters,
                test_distance[index],
            )
            for rank, probability in enumerate(intercept_probability):
                probability_rows.append({
                    "tmax_state_id": row["tmax_state_id"],
                    "rank": rank,
                    "challenger": "c1_market_power_intercept_only",
                    "probability": float(probability),
                })
            c2_intercept_probability = apply_challenger(
                "c2_adjacent_kernel_diffusion",
                test_market[index],
                [],
                c2_intercept_parameters,
                test_distance[index],
            )
            for rank, probability in enumerate(c2_intercept_probability):
                probability_rows.append({
                    "tmax_state_id": row["tmax_state_id"],
                    "rank": rank,
                    "challenger": "c2_adjacent_kernel_intercept_only",
                    "probability": float(probability),
                })
    challenger_predictions = pd.DataFrame(probability_rows)
    merged = base_predictions.copy()
    for challenger in (*CHALLENGERS, *ATTRIBUTION_MODELS):
        values = challenger_predictions[challenger_predictions["challenger"].eq(challenger)].rename(
            columns={"probability": f"p_{challenger}"}
        )
        merged = merged.merge(
            values[["tmax_state_id", "rank", f"p_{challenger}"]],
            on=["tmax_state_id", "rank"],
            how="left",
            validate="one_to_one",
        )
    missing = merged[[f"p_{name}" for name in (*CHALLENGERS, *ATTRIBUTION_MODELS)]].isna().any().any()
    if missing:
        raise RuntimeError("challenger predictions do not cover the base OOF denominator")
    return merged, pd.DataFrame(fit_rows)


def select_shadow_challenger(
    scorecard: pd.DataFrame,
    fit_diagnostics: pd.DataFrame,
) -> tuple[str, str]:
    market = scorecard[scorecard["model"].eq("market")].iloc[0]
    for challenger in CHALLENGERS:
        row = scorecard[scorecard["model"].eq(challenger)].iloc[0]
        if (
            float(row["brier"]) < float(market["brier"])
            and float(row["logloss"]) < float(market["logloss"])
            and float(row["rps"]) - float(market["rps"]) <= 0.001
        ):
            return challenger, "first_preregistered_probability_qualifier"
    convergence = fit_diagnostics.groupby("challenger")["success"].all()
    converged = [name for name in CHALLENGERS if bool(convergence.get(name, False))]
    if not converged:
        raise RuntimeError("no preregistered challenger converged on every expanding-date fold")
    ranked = scorecard[scorecard["model"].isin(converged)].copy()
    ranked["probability_rank"] = (
        ranked["brier"].rank(method="min")
        + ranked["logloss"].rank(method="min")
        + ranked["rps"].rank(method="min")
    )
    selected = str(ranked.sort_values(["probability_rank", "model"]).iloc[0]["model"])
    return selected, "best_fully_converged_preregistered_unconfirmed_shadow"


def fit_final_artifact(
    features: pd.DataFrame,
    rungs: pd.DataFrame,
    selected_challenger: str,
    selection_reason: str,
) -> dict[str, Any]:
    rung_groups = {
        str(key): frame.sort_values("bracket_center_f").reset_index(drop=True)
        for key, frame in rungs.groupby("tmax_state_id")
    }
    rows, markets, outcomes, distances = _state_payloads(features, rung_groups)
    transform = fit_feature_transform(rows)
    matrix = transform_features(rows, transform)
    date_counts = pd.Series([row["target_date"] for row in rows]).value_counts()
    weights = np.array([1.0 / float(date_counts[row["target_date"]]) for row in rows], dtype=float)
    parameters, fit = fit_challenger(
        selected_challenger,
        matrix,
        markets,
        outcomes,
        distances,
        weights,
    )
    settled = features.dropna(subset=["forecast_error_f"])
    global_bias = float(settled["forecast_error_f"].mean()) if len(settled) else 0.0
    return {
        "schema_version": "weather_convective_tail_distribution_model_v1",
        "strategy_family": "weather.convective_tail_distribution",
        "model_version": "convective_tail_distribution_v1",
        "execution_mode": "zero_notional_shadow_only",
        "selected_challenger": selected_challenger,
        "selection_reason": selection_reason,
        "training_target_date_start": min(row["target_date"] for row in rows),
        "training_target_date_end": max(row["target_date"] for row in rows),
        "training_target_dates": len(set(row["target_date"] for row in rows)),
        "training_states": len(rows),
        "feature_transform": transform.to_dict(),
        "parameters": parameters.tolist(),
        "fit": fit,
        "bias_lookup": {
            "global_f": global_bias,
            "city_f": {str(k): float(v) for k, v in settled.groupby("city")["forecast_error_f"].mean().items()},
            "source_f": {str(k): float(v) for k, v in settled.groupby("forecast_model")["forecast_error_f"].mean().items()},
        },
        "probability_epsilon": EPS,
        "notional_usd": 0,
        "promotion_gate": {"new_pit_target_dates": 30, "tail_tickets": 80, "fresh_book_coverage": 0.90},
    }


def scores(pred: pd.DataFrame, states: pd.DataFrame, draws: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    records: list[dict[str, Any]] = []
    for state_id, group in pred.groupby("tmax_state_id"):
        group = group.sort_values("rank")
        y = group["win"].to_numpy(float)
        meta = states[states["tmax_state_id"].eq(state_id)].iloc[0]
        record = {k: meta[k] for k in ["tmax_state_id", "city", "target_date", "lifecycle_2h", "forecast_model", "quote_fraction", "peak_pop_pct", "book_age_median_min"]}
        for model in MODEL_NAMES:
            p = np.clip(group[f"p_{model}"].to_numpy(float), EPS, 1.0)
            p /= p.sum()
            record[f"brier_{model}"] = float(np.sum((p - y) ** 2))
            record[f"logloss_{model}"] = float(-np.sum(y * np.log(p)))
            record[f"rps_{model}"] = float(np.sum((np.cumsum(p)[:-1] - np.cumsum(y)[:-1]) ** 2) / max(1, len(p) - 1))
        records.append(record)
    per_state = pd.DataFrame(records)
    summary: list[dict[str, Any]] = []
    rng = np.random.default_rng(SEED)
    for model in MODEL_NAMES:
        row = {"model": model, "states": len(per_state), "dates": per_state["target_date"].nunique(), "cities": per_state["city"].nunique()}
        for metric in ("brier", "logloss", "rps"):
            row[metric] = float(per_state[f"{metric}_{model}"].mean())
            if model != "market":
                delta = per_state[["target_date"]].copy()
                delta["value"] = per_state[f"{metric}_{model}"] - per_state[f"{metric}_market"]
                daily = delta.groupby("target_date")["value"].mean().to_numpy(float)
                samples = daily[rng.integers(0, len(daily), size=(draws, len(daily)))].mean(axis=1)
                row[f"{metric}_delta_vs_market"] = float(delta["value"].mean())
                row[f"{metric}_delta_ci_low"] = float(np.quantile(samples, 0.025))
                row[f"{metric}_delta_ci_high"] = float(np.quantile(samples, 0.975))
        cal = pred[["win", f"p_{model}"]].rename(columns={f"p_{model}": "p"}).copy()
        cal["bin"] = pd.cut(cal["p"], np.linspace(0, 1, 11), include_lowest=True)
        bins = cal.groupby("bin", observed=True).agg(rows=("win", "size"), mean_p=("p", "mean"), win_rate=("win", "mean")).reset_index()
        row["calibration_ece"] = float(np.average(np.abs(bins["mean_p"] - bins["win_rate"]), weights=bins["rows"]))
        summary.append(row)
    source = per_state.groupby("forecast_model", as_index=False).agg(states=("tmax_state_id", "size"), dates=("target_date", "nunique"))
    for model in MODEL_NAMES[1:]:
        grouped = per_state.assign(
            brier_delta=per_state[f"brier_{model}"] - per_state["brier_market"],
            logloss_delta=per_state[f"logloss_{model}"] - per_state["logloss_market"],
            rps_delta=per_state[f"rps_{model}"] - per_state["rps_market"],
        ).groupby("forecast_model")[["brier_delta", "logloss_delta", "rps_delta"]].mean()
        for col in grouped:
            source = source.merge(grouped[[col]].rename(columns={col: f"{model}_{col}"}), left_on="forecast_model", right_index=True, how="left")
    return per_state, pd.DataFrame(summary), source


def calibration_bins(pred: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for model in MODEL_NAMES:
        work = pred[["win", f"p_{model}"]].rename(columns={f"p_{model}": "probability"}).copy()
        work["bin"] = pd.cut(work["probability"], np.linspace(0, 1, 11), include_lowest=True).astype(str)
        out = work.groupby("bin", as_index=False).agg(rows=("win", "size"), mean_probability=("probability", "mean"), win_rate=("win", "mean"))
        out["model"] = model
        rows.append(out)
    return pd.concat(rows, ignore_index=True)


def weather_attribution_scorecard(
    per_state: pd.DataFrame,
    full_model: str,
    intercept_model: str,
    draws: int,
) -> pd.DataFrame:
    rng = np.random.default_rng(20260829)
    rows: list[dict[str, Any]] = []
    for metric in ("brier", "logloss", "rps"):
        values = per_state[["target_date"]].copy()
        values["delta"] = (
            per_state[f"{metric}_{full_model}"] - per_state[f"{metric}_{intercept_model}"]
        )
        daily = values.groupby("target_date")["delta"].mean().to_numpy(float)
        sampled = daily[rng.integers(0, len(daily), size=(draws, len(daily)))].mean(axis=1)
        rows.append(
            {
                "comparison": f"{full_model}_minus_{intercept_model}",
                "metric": metric,
                "states": len(per_state),
                "dates": per_state["target_date"].nunique(),
                "delta": float(values["delta"].mean()),
                "ci_low": float(np.quantile(sampled, 0.025)),
                "ci_high": float(np.quantile(sampled, 0.975)),
            }
        )
    return pd.DataFrame(rows)


def distribution_shape_diagnostics(pred: pd.DataFrame, selected_model: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for state_id, group in pred.groupby("tmax_state_id"):
        market = group["p_market"].to_numpy(float)
        model = group[f"p_{selected_model}"].to_numpy(float)
        distance = group["settlement_native_bracket_distance"].to_numpy(float)
        market_mean = float(np.sum(market * distance))
        model_mean = float(np.sum(model * distance))
        market_variance = float(np.sum(market * (distance - market_mean) ** 2))
        model_variance = float(np.sum(model * (distance - model_mean) ** 2))
        rows.append({
            "tmax_state_id": state_id,
            "city": group.iloc[0]["city"],
            "target_date": group.iloc[0]["target_date"],
            "forecast_model": group.iloc[0]["forecast_model"],
            "market_entropy": float(-np.sum(market * np.log(np.clip(market, EPS, 1.0)))),
            "model_entropy": float(-np.sum(model * np.log(np.clip(model, EPS, 1.0)))),
            "market_variance_native": market_variance,
            "model_variance_native": model_variance,
            "variance_ratio": model_variance / market_variance if market_variance > 0 else math.nan,
            "hotter_tail_mass_residual": float(model[distance > 0].sum() - market[distance > 0].sum()),
        })
    out = pd.DataFrame(rows)
    out["distribution_action"] = np.where(out["variance_ratio"].gt(1.0), "widen", "sharpen")
    return out


def slice_scorecard(per_state: pd.DataFrame, selected_model: str) -> pd.DataFrame:
    masks: dict[str, pd.Series] = {
        "all": pd.Series(True, index=per_state.index),
        "pop_ge_50_baseline": per_state["peak_pop_pct"].ge(50),
        "pop_lt_50_complement": per_state["peak_pop_pct"].lt(50),
        "fresh_full90": per_state["quote_fraction"].ge(0.90) & per_state["book_age_median_min"].le(5),
        "partial_or_stale": ~(per_state["quote_fraction"].ge(0.90) & per_state["book_age_median_min"].le(5)),
    }
    for lifecycle in sorted(per_state["lifecycle_2h"].dropna().unique()):
        masks[f"lifecycle_{lifecycle:g}"] = per_state["lifecycle_2h"].eq(lifecycle)
    rows = []
    for name, mask in masks.items():
        group = per_state[mask]
        if group.empty:
            continue
        rows.append({
            "slice": name, "states": len(group), "dates": group["target_date"].nunique(), "cities": group["city"].nunique(),
            "selected_model": selected_model,
            "brier_delta_vs_market": float((group[f"brier_{selected_model}"] - group["brier_market"]).mean()),
            "logloss_delta_vs_market": float((group[f"logloss_{selected_model}"] - group["logloss_market"]).mean()),
            "rps_delta_vs_market": float((group[f"rps_{selected_model}"] - group["rps_market"]).mean()),
            "mean_quote_fraction": float(group["quote_fraction"].mean()),
            "median_book_age_min": float(group["book_age_median_min"].median()),
        })
    return pd.DataFrame(rows)


def expression_ledger(
    pred: pd.DataFrame,
    heada: pd.DataFrame,
    selected_model: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidate_conditions = set(heada["condition_id"].dropna().astype(str))
    rows: list[dict[str, Any]] = []
    for state_id, group in pred.groupby("tmax_state_id"):
        group = group.sort_values("rank").reset_index(drop=True)
        anchors = group[group["condition_id"].astype(str).isin(candidate_conditions) & group["direct_two_sided"]]
        for policy, width in (("single_yes", 1), ("adjacent_hot_strip", 2), ("bounded_hot_tail_basket", 3)):
            choices: list[dict[str, Any]] = []
            for idx in anchors.index:
                leg = group.iloc[idx : idx + width]
                if len(leg) != width or not leg["direct_two_sided"].all() or leg["yes_ask"].isna().any():
                    continue
                cost = float(sum(float(p) + fee(float(p)) for p in leg["yes_ask"]))
                predicted = float(leg[f"p_{selected_model}"].sum())
                choices.append({"leg": leg, "cost": cost, "predicted": predicted, "edge": predicted - cost})
            if not choices:
                continue
            choice = max(choices, key=lambda x: x["edge"])
            leg = choice["leg"]
            rows.append({
                "policy": policy, "probability_model": selected_model,
                "tmax_state_id": state_id, "city": group.iloc[0]["city"],
                "target_date": group.iloc[0]["target_date"], "decision_ts_utc": group.iloc[0]["decision_ts_utc"],
                "lifecycle_2h": group.iloc[0]["lifecycle_2h"], "forecast_model": group.iloc[0]["forecast_model"], "legs": len(leg),
                "brackets": "|".join(leg["bracket"].astype(str)), "predicted_probability": choice["predicted"],
                "cost_per_expression": choice["cost"], "predicted_edge": choice["edge"],
                "payout": float(leg["win"].sum()), "pnl_per_expression": float(leg["win"].sum()) - choice["cost"],
                "top_ask_capacity_shares": float(leg["yes_ask_size"].min()),
                "depth_5c_capacity_shares": float(leg["yes_depth_ask_5c"].min()),
            })
    ledger = pd.DataFrame(rows)
    if ledger.empty:
        return ledger, pd.DataFrame()
    eligible = ledger[ledger["predicted_edge"].gt(0)].sort_values("decision_ts_utc")
    tickets = eligible.drop_duplicates(["policy", "city", "target_date"], keep="first").copy()
    return ledger, tickets


def trade_summary(tickets: pd.DataFrame, draws: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summaries, daily_rows, stability = [], [], []
    rng = np.random.default_rng(SEED + 9)
    if tickets.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    for policy, group in tickets.groupby("policy"):
        daily = group.groupby("target_date", as_index=False).agg(pnl=("pnl_per_expression", "sum"), cost=("cost_per_expression", "sum"), tickets=("city", "size"))
        daily["policy"] = policy
        daily_rows.append(daily)
        best_date = str(daily.loc[daily["pnl"].idxmax(), "target_date"])
        without = group[group["target_date"].ne(best_date)]
        values = daily[["pnl", "cost"]].to_numpy(float)
        sampled = values[rng.integers(0, len(values), size=(draws, len(values)))].sum(axis=1)
        rois = sampled[:, 0] / sampled[:, 1]
        summaries.append({
            "policy": policy, "tickets": len(group), "dates": group["target_date"].nunique(), "cities": group["city"].nunique(),
            "wins": int(group["payout"].sum()), "win_rate": float(group["payout"].mean()),
            "cost": float(group["cost_per_expression"].sum()), "pnl": float(group["pnl_per_expression"].sum()),
            "roi": float(group["pnl_per_expression"].sum() / group["cost_per_expression"].sum()),
            "roi_ci_low": float(np.quantile(rois, 0.025)), "roi_ci_high": float(np.quantile(rois, 0.975)),
            "best_date": best_date, "best_date_pnl_share": float(daily["pnl"].max() / group["pnl_per_expression"].sum()) if group["pnl_per_expression"].sum() else math.nan,
            "roi_without_best_date": float(without["pnl_per_expression"].sum() / without["cost_per_expression"].sum()) if len(without) else math.nan,
            "median_top_ask_capacity_shares": float(group["top_ask_capacity_shares"].median()),
            "median_depth_5c_capacity_shares": float(group["depth_5c_capacity_shares"].median()),
            "five_share_coverage": float(group["top_ask_capacity_shares"].ge(5).mean()),
            "ten_share_coverage": float(group["top_ask_capacity_shares"].ge(10).mean()),
        })
        for source, source_group in group.groupby("target_date"):
            stability.append({"policy": policy, "slice": "target_date", "value": source, "tickets": len(source_group), "pnl": source_group["pnl_per_expression"].sum(), "cost": source_group["cost_per_expression"].sum()})
        for source, source_group in group.groupby("forecast_model"):
            stability.append({"policy": policy, "slice": "forecast_model", "value": source, "tickets": len(source_group), "pnl": source_group["pnl_per_expression"].sum(), "cost": source_group["cost_per_expression"].sum()})
    return pd.DataFrame(summaries), pd.concat(daily_rows, ignore_index=True), pd.DataFrame(stability)


def load_core_live_real_daily(conn: sqlite3.Connection) -> pd.DataFrame:
    """Load settled live evidence for coverage audit only, never as replay baseline."""
    frame = pd.read_sql_query(
        """SELECT target_date, COUNT(*) AS fills, SUM(cost_usd) AS cost,
                  SUM(pnl_usd_at_fill) AS pnl
           FROM fact_trades
           WHERE instance_id='current_yes_core_carry_tiny_live_v2'
             AND trade_class='live_real'
             AND settled=1 AND pnl_usd_at_fill IS NOT NULL
           GROUP BY target_date ORDER BY target_date""",
        conn,
    )
    frame["evidence_class"] = "live_real_settled_fills"
    frame["valid_as_convective_replay_baseline"] = False
    return frame


def load_core_runtime_score_dates(path: Path) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            rows.append(
                {
                    "target_date": str(item.get("target_date") or ""),
                    "artifact_hash": str(item.get("artifact_hash") or ""),
                    "model_version": str(item.get("model_version") or ""),
                }
            )
    return pd.DataFrame(rows)


def load_core_frozen_daily(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        usecols=["target_date", "label", "taker_cost", "frozen_baseline_selected"],
    )
    selected = frame["frozen_baseline_selected"].astype(str).str.lower().isin({"1", "true", "yes"})
    frame["core_cost"] = np.where(selected, pd.to_numeric(frame["taker_cost"], errors="coerce"), 0.0)
    frame["core_pnl"] = np.where(
        selected,
        pd.to_numeric(frame["label"], errors="coerce") - frame["core_cost"],
        0.0,
    )
    frame["core_tickets"] = selected.astype(int)
    return frame.groupby("target_date", as_index=False).agg(
        core_pnl=("core_pnl", "sum"),
        core_cost=("core_cost", "sum"),
        core_tickets=("core_tickets", "sum"),
    )


def temporal_audit(
    heada: pd.DataFrame,
    chosen: pd.DataFrame,
    oof_states: pd.DataFrame,
    core_frozen_ledger: Path,
    core_runtime_scores: Path,
    core_live_daily: pd.DataFrame,
) -> pd.DataFrame:
    oof_dates = set(oof_states["target_date"].astype(str))
    frozen = pd.read_csv(core_frozen_ledger, usecols=["target_date"])
    runtime = load_core_runtime_score_dates(core_runtime_scores)

    def row(
        series: str,
        evidence_class: str,
        dates: set[str],
        usable_for_correlation: bool,
        reason: str,
        versions: str = "",
        artifacts: int | str = "",
    ) -> dict[str, Any]:
        overlap = sorted(dates & oof_dates)
        return {
            "series": series,
            "evidence_class": evidence_class,
            "start_date": min(dates) if dates else "",
            "end_date": max(dates) if dates else "",
            "target_dates": len(dates),
            "overlap_with_convective_oof_dates": len(overlap),
            "overlap_dates": ",".join(overlap),
            "usable_for_correlation": usable_for_correlation,
            "invalid_reason": reason,
            "model_versions": versions,
            "artifact_hashes": artifacts,
        }

    heada_dates = set(heada["target_date"].astype(str))
    pit_dates = set(chosen["target_date"].astype(str))
    frozen_dates = set(frozen["target_date"].astype(str))
    runtime_dates = set(runtime["target_date"].astype(str))
    live_dates = set(core_live_daily["target_date"].astype(str))
    return pd.DataFrame(
        [
            row("convective_raw_heada", "candidate_universe", heada_dates, False, "scope audit row"),
            row("convective_pit_ladder", "pit_state", pit_dates, False, "scope audit row"),
            row("convective_expanding_oof", "pit_replay", oof_dates, False, "target series"),
            row(
                "core_frozen_historical_replay",
                "frozen_signal_replay",
                frozen_dates,
                len(frozen_dates & oof_dates) >= 5,
                "same-date frozen replay available" if len(frozen_dates & oof_dates) >= 5 else "fewer than five shared dates",
            ),
            row(
                "core_runtime_scores",
                "runtime_score_telemetry",
                runtime_dates,
                False,
                "score telemetry is not a portfolio return series and model/version changed inside overlap",
                versions="|".join(sorted(x for x in runtime["model_version"].unique() if x)),
                artifacts=int(runtime["artifact_hash"].replace("", np.nan).nunique()),
            ),
            row(
                "core_live_real_settled",
                "live_real_settled_fills",
                live_dates,
                False,
                "mixed live-fill versus replay denominator; no-trade dates omitted; Core version changed inside overlap",
            ),
        ]
    )


def portfolio_correlation(
    daily: pd.DataFrame,
    audit: pd.DataFrame,
    core_frozen_daily: pd.DataFrame,
    oof_states: pd.DataFrame,
) -> pd.DataFrame:
    frozen = audit[audit["series"].eq("core_frozen_historical_replay")].iloc[0]
    runtime = audit[audit["series"].eq("core_runtime_scores")].iloc[0]
    live = audit[audit["series"].eq("core_live_real_settled")].iloc[0]
    oof_dates = sorted(set(oof_states["target_date"].astype(str)))
    rows: list[dict[str, Any]] = []
    for policy in sorted(daily["policy"].unique()):
        convective = daily[daily["policy"].eq(policy)][["target_date", "pnl"]]
        calendar = pd.DataFrame({"target_date": oof_dates}).merge(
            core_frozen_daily, on="target_date", how="inner"
        ).merge(convective, on="target_date", how="left")
        calendar["pnl"] = calendar["pnl"].fillna(0.0)
        correlation = (
            float(calendar["pnl"].corr(calendar["core_pnl"]))
            if len(calendar) >= 5 and calendar["pnl"].std() > 0 and calendar["core_pnl"].std() > 0
            else math.nan
        )
        rows.append(
            {
                "policy": policy,
                "pearson_daily_return_correlation": correlation,
                "decision_status": "identified_from_frozen_same_date_replay" if np.isfinite(correlation) else "not_identifiable",
                "valid_frozen_core_overlap_dates": len(calendar),
                "runtime_score_calendar_overlap_dates": int(runtime["overlap_with_convective_oof_dates"]),
                "invalid_live_fill_overlap_dates": int(live["overlap_with_convective_oof_dates"]),
                "invalid_live_fill_overlap": str(live["overlap_dates"]),
                "reason": "frozen Core replay and Convective OOF aligned on the full shared target-date calendar; no-trade days are zero",
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    conn = connect_ro(args.db)
    try:
        heada = load_heada(conn, args.start_date, args.end_date)
        canonical_raw = load_state_rows(conn, args.history_cutover_date, args.end_date)
        legacy_states_raw, legacy_raw, legacy_coverage = load_legacy_state_rows(
            conn,
            args.history_snapshot_dir,
            args.history_start_date,
            args.end_date,
            args.history_workers,
            args.output_dir / "adapter_cache",
        )
        lineage, weather = load_observation_context(conn, args.history_cutover_date, args.end_date)
        core_daily = load_core_live_real_daily(conn)
    finally:
        conn.close()
    if not canonical_raw.empty:
        canonical_raw["state_source"] = "tmax_v2_canonical"
    canonical_rungs, canonical_chosen = prepare_states(canonical_raw)
    canonical_features = add_features(canonical_rungs, lineage, weather)
    canonical_features["state_source"] = "tmax_v2_canonical"

    legacy_rungs_all, legacy_chosen_all = prepare_states(legacy_raw)
    atlas = load_atlas_states(args.atlas_states, args.history_start_date, args.end_date)
    legacy_features_all = add_legacy_features(legacy_rungs_all, atlas, args.single_run_dir)
    legacy_features_all["state_source"] = "immutable_paper_snapshot"
    parity = adapter_parity(
        legacy_features_all,
        legacy_rungs_all,
        canonical_features,
        canonical_rungs,
    )

    legacy_feature_mask = legacy_features_all["target_date"].lt(args.history_cutover_date)
    legacy_state_ids = set(legacy_features_all.loc[legacy_feature_mask, "tmax_state_id"].astype(str))
    legacy_features = legacy_features_all[legacy_feature_mask].copy()
    legacy_rungs = legacy_rungs_all[legacy_rungs_all["tmax_state_id"].astype(str).isin(legacy_state_ids)].copy()
    legacy_chosen = legacy_chosen_all[legacy_chosen_all["tmax_state_id"].astype(str).isin(legacy_state_ids)].copy()
    features = pd.concat([legacy_features, canonical_features], ignore_index=True, sort=False)
    rungs = pd.concat([legacy_rungs, canonical_rungs], ignore_index=True, sort=False)
    chosen = pd.concat([legacy_chosen, canonical_chosen], ignore_index=True, sort=False)
    features = add_outcome_and_bias(features, rungs)
    pred, oof_states = expanding_oof(features, rungs)
    pred, challenger_fits = challenger_oof(features, rungs, pred)
    per_state, scorecard, source_scorecard = scores(pred, oof_states, args.draws)
    selected_challenger, selection_reason = select_shadow_challenger(scorecard, challenger_fits)
    model_artifact = fit_final_artifact(features, rungs, selected_challenger, selection_reason)
    selected_score = scorecard[scorecard["model"].eq(selected_challenger)].iloc[0]
    attribution_baseline = {
        "c1_market_power_temperature": "c1_market_power_intercept_only",
        "c2_adjacent_kernel_diffusion": "c2_adjacent_kernel_intercept_only",
    }.get(selected_challenger, "c1_market_power_intercept_only")
    intercept_score = scorecard[scorecard["model"].eq(attribution_baseline)].iloc[0]
    attribution = weather_attribution_scorecard(
        per_state,
        selected_challenger,
        attribution_baseline,
        args.draws,
    )
    calibration = calibration_bins(pred)
    shape_diagnostics = distribution_shape_diagnostics(pred, selected_challenger)
    slices = slice_scorecard(per_state, selected_challenger)
    expr, tickets = expression_ledger(pred, heada, selected_challenger)
    trade, daily, stability = trade_summary(tickets, args.draws)
    audit = temporal_audit(
        heada,
        chosen,
        oof_states,
        args.core_frozen_ledger,
        args.core_runtime_scores,
        core_daily,
    )
    core_frozen_daily = load_core_frozen_daily(args.core_frozen_ledger)
    correlation = portfolio_correlation(daily, audit, core_frozen_daily, oof_states)
    matched_conditions = set(pred["condition_id"].dropna().astype(str)) & set(heada["condition_id"].dropna().astype(str))
    humidity_coverage = float(features["relative_humidity_pct"].notna().mean()) if len(features) else 0.0
    pop_coverage = float(features["peak_pop_pct"].notna().mean()) if len(features) else 0.0
    cloud_coverage = float(features["peak_cloud_pct"].notna().mean()) if len(features) else 0.0
    wind_coverage = float(features["peak_wind_kt"].notna().mean()) if len(features) else 0.0
    evidence_dates = set(chosen["target_date"].astype(str))
    raw_dates = set(heada["target_date"].astype(str))
    missing_evidence_dates = sorted(raw_dates - evidence_dates)
    parity_summary = {
        "matched_lifecycle_states": int(len(parity)),
        "matched_target_dates": int(parity["target_date"].nunique()) if len(parity) else 0,
        "market_l1_median": float(parity["normalized_market_l1"].median()) if len(parity) else math.nan,
        "forecast_peak_abs_delta_f_median": float(parity["forecast_peak_delta_f"].abs().median()) if len(parity) else math.nan,
        "decision_time_delta_min_median": float(parity["decision_time_delta_min"].median()) if len(parity) else math.nan,
    }
    summary = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "research_id": "convective_tail_distribution_v1",
        "execution_mode": "research_only_no_orders",
        "denominator": {
            "raw_heada_candidates": len(heada), "target_dates": heada["target_date"].nunique(), "cities": heada["city"].nunique(),
            "baseline_selected": int(heada["baseline_selected"].sum()), "baseline_unselected": int((1 - heada["baseline_selected"]).sum()),
            "pit_lifecycle_states_quote80": len(chosen), "pit_lifecycle_dates": chosen["target_date"].nunique(),
            "oof_states": len(oof_states), "oof_dates": oof_states["target_date"].nunique(),
            "matched_heada_conditions_in_oof": len(matched_conditions),
            "raw_dates_without_probability_evidence": len(missing_evidence_dates),
            "raw_dates_without_probability_evidence_list": missing_evidence_dates,
        },
        "history_adapter": {
            "contract": "legacy immutable paper snapshots before 2026-07-15; tmax_v2 canonical on/after cutover",
            "legacy_history_start": args.history_start_date,
            "canonical_cutover": args.history_cutover_date,
            "legacy_coverage": legacy_coverage,
            "overlap_parity": parity_summary,
        },
        "feature_coverage": {
            "relative_humidity_pct": humidity_coverage,
            "humidity_contract_status": "missing_from_historical_pit_curve_and_observation_lineage" if humidity_coverage == 0 else "available",
            "peak_pop_pct": pop_coverage,
            "peak_cloud_pct": cloud_coverage,
            "peak_wind_kt": wind_coverage,
            "forecast_dispersion_two_plus_vintages": float(features["forecast_vintages_24h"].ge(2).mean()),
            "warming_innovation": float(features["warming_innovation_f"].notna().mean()),
            "legacy_pop_contract": "unavailable; explicit missing indicator, never backfilled from future weather",
            "legacy_cloud_contract": "PIT METAR sky-cover category mapped 0/25/50/75/100; canonical rows use forecast cloud percent",
            "legacy_warming_contract": "PIT observation versus D-1 12Z single-run expected warming from first target-day observation",
        },
        "model_contract": {
            "weather_only": "expanding Ridge center + expanding residual scale, normal mass on settlement-native ladder",
            "market_offset": f"log pool market/weather with fixed weather weight {OFFSET_WEATHER_WEIGHT}",
            "registered_challengers": list(CHALLENGERS),
            "all_registered_challengers_executed": True,
            "selected_shadow_challenger": selected_challenger,
            "selection_reason": selection_reason,
            "oof_widen_state_fraction": float(shape_diagnostics["distribution_action"].eq("widen").mean()),
            "oof_hotter_tail_mass_positive_fraction": float(shape_diagnostics["hotter_tail_mass_residual"].gt(0).mean()),
            "oof_mean_variance_ratio": float(shape_diagnostics["variance_ratio"].mean()),
            "pop_gate": "none; POP is continuous and POP>=50 is descriptive baseline only",
        },
        "promotion_gate": {"new_pit_target_dates_required": 30, "tail_tickets_required": 80, "fresh_book_coverage_required": 0.90},
        "weather_feature_attribution": {
            "baseline": attribution_baseline,
            "full_minus_intercept_brier": float(selected_score["brier"] - intercept_score["brier"]),
            "full_minus_intercept_logloss": float(selected_score["logloss"] - intercept_score["logloss"]),
            "full_minus_intercept_rps": float(selected_score["rps"] - intercept_score["rps"]),
            "interpretation": "negative means continuous weather features add value beyond global market-temperature calibration",
        },
        "core_carry_correlation": {
            "status": (
                "identified_from_frozen_same_date_replay"
                if len(correlation) and correlation["pearson_daily_return_correlation"].notna().any()
                else "not_identifiable"
            ),
            "valid_frozen_core_overlap_dates": int(
                audit.loc[
                    audit["series"].eq("core_frozen_historical_replay"),
                    "overlap_with_convective_oof_dates",
                ].iloc[0]
            ),
            "invalid_live_fill_overlap_dates": int(
                audit.loc[
                    audit["series"].eq("core_live_real_settled"),
                    "overlap_with_convective_oof_dates",
                ].iloc[0]
            ),
            "by_expression": (
                correlation[["policy", "pearson_daily_return_correlation"]].to_dict("records")
                if len(correlation)
                else []
            ),
            "reason": "frozen same-date replay aligned on the full shared calendar; runtime/live fills excluded",
        },
    }
    for name, frame in {
        "heada_denominator.csv": heada, "state_features.csv": features, "rung_predictions.csv": pred,
        "per_state_scores.csv": per_state, "model_scorecard.csv": scorecard, "source_scorecard.csv": source_scorecard,
        "challenger_fit_diagnostics.csv": challenger_fits,
        "weather_attribution_scorecard.csv": attribution,
        "calibration_bins.csv": calibration, "slice_scorecard.csv": slices,
        "distribution_shape_diagnostics.csv": shape_diagnostics,
        "expression_ledger.csv": expr, "tickets.csv": tickets, "trade_scorecard.csv": trade,
        "daily_portfolio.csv": daily, "stability.csv": stability, "core_carry_daily.csv": core_daily,
        "core_carry_frozen_daily.csv": core_frozen_daily,
        "research_window_audit.csv": audit, "portfolio_correlation.csv": correlation,
        "adapter_parity.csv": parity,
    }.items():
        frame.to_csv(args.output_dir / name, index=False)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_dir / "model_artifact.json").write_text(
        json.dumps(model_artifact, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
