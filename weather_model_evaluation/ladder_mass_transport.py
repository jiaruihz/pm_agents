"""Canonical PIT ladder-mass-transport evaluation.

This module is called by ``lmvm_repricing_challenger.py``.  It keeps the
short-horizon rung-relative markout and final settlement heads separate and
never writes strategy/runtime state.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
import gc
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from scripts.analysis.market_structure_edge.market_ladder_kink_v1 import (
    bracket_center,
    lifecycle_label,
)
from weather_data_feed.ladder_snapshot_history import (
    history_cache_dates,
    load_history_cache_date,
    materialize_history_cache,
)


SCHEMA_VERSION = "ladder_mass_transport_v1"
RUN_ID = "ladder_mass_transport_time_aligned_20260809"
TRAIN_START, TRAIN_END = "2026-05-19", "2026-07-10"
DEV_START, DEV_END = "2026-07-11", "2026-07-21"
VALIDATION_START, VALIDATION_END = "2026-07-22", "2026-07-28"
RECENT_FORWARD_START, RECENT_FORWARD_END = "2026-07-29", "2026-08-08"
FORWARD_START, FORWARD_END = "2026-08-11", "2026-08-17"
HORIZONS = (30, 60)
PRIMARY_HORIZON = 60
SAMPLE_SECONDS = 30 * 60
FUTURE_TOLERANCE_MIN = 12
WEATHER_SIGMA_F = 2.0
RIDGE_ALPHAS = (1.0, 10.0, 100.0)
MIN_OOF_TRAIN_DATES = 5
FEE_RATE = 0.05
EPS = 1e-6


M0 = (
    "yes_mid", "spread", "log_bid_size", "log_ask_size", "log_depth_bid_5c",
    "log_depth_ask_5c", "market_p", "rank_fraction", "distance_to_mode",
    "local_hours", "ecology_spread_median", "ecology_depth_median",
    "quote_lifetime_min",
)
M1 = M0 + (
    "weather_p", "forecast_peak_f", "observed_max_f", "forecast_peak_shock_f",
    "observed_max_shock_f", "forecast_age_min", "observation_age_min",
    "weather_probability_shock",
)
M2 = M1 + (
    "mode_shift", "entropy", "entropy_delta", "skew", "skew_delta",
    "hot_tail_mass", "hot_tail_mass_delta", "multimodality",
    "multimodality_delta", "adjacent_log_ratio_left", "adjacent_log_ratio_right",
    "local_curvature", "mass_transport_l1", "mass_transport_signed",
    "rung_move_prev", "rung_relative_move_prev", "neighbor_lead_lag",
)
M3 = M2 + (
    "weather_event_age_min", "weather_market_gap", "response_gap",
    "response_completion", "repricing_time_estimate_min", "lag_pressure_30",
    "lag_pressure_60",
)
STATIC_KINK = M0 + ("kink_score",)
FEATURE_BLOCKS = {
    "M0_market_level": M0,
    "M1_weather_market": M1,
    "M2_ladder_transition": M2,
    "M3_weather_response_lag": M3,
    "static_kink_control": STATIC_KINK,
}
CATEGORICAL = ("city", "lifecycle", "snapshot_source")


def _compact_panel_columns() -> list[str]:
    columns = [
        "ladder_snapshot_id", "feature_book_snapshot_id",
        "future_evaluation_snapshot_id", "city", "target_date",
        "event_identity", "snapshot_ts", "snapshot_source", "source_path",
        "market_unit", "bracket", "condition_id", "bracket_center",
        "yes_direct", "no_direct", "yes_bid", "yes_ask", "yes_mid",
        "yes_bid_size", "yes_ask_size", "yes_depth_bid_5c",
        "yes_depth_ask_5c", "no_bid", "no_ask", "no_bid_size",
        "no_ask_size", "forecast_capture_id", "forecast_values_hash",
        "forecast_event_ts", "observation_event_ts", "win",
    ]
    columns.extend(CATEGORICAL)
    for block in FEATURE_BLOCKS.values():
        columns.extend(block)
    for horizon in HORIZONS:
        columns.extend(
            [
                f"h{horizon}_snapshot_id", f"h{horizon}_mid_move",
                f"h{horizon}_common_move", f"h{horizon}_relative_markout",
                f"h{horizon}_direction", f"h{horizon}_yes_bid",
                f"h{horizon}_yes_ask", f"h{horizon}_yes_bid_size",
                f"h{horizon}_yes_ask_size", f"h{horizon}_no_bid",
                f"h{horizon}_no_ask", f"h{horizon}_no_bid_size",
                f"h{horizon}_no_ask_size",
            ]
        )
    return list(dict.fromkeys(columns))


@dataclass(frozen=True)
class FrozenChoice:
    block: str
    alpha: float
    horizon: int = PRIMARY_HORIZON


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _artifact_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _chunks(values: list[str], size: int = 800) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _read_sampled_snapshots(
    conn: sqlite3.Connection, start: str, end: str
) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        WITH base AS (
          SELECT s.*,
                 CAST(unixepoch(s.source_snapshot_ts_utc)/:sample_seconds AS INTEGER) AS time_bin,
                 ROW_NUMBER() OVER (
                   PARTITION BY s.city,s.target_date,s.event_identity,
                                CAST(unixepoch(s.source_snapshot_ts_utc)/:sample_seconds AS INTEGER)
                   ORDER BY CASE WHEN s.source_system='weather_market_books_full_ladder' THEN 0 ELSE 1 END,
                            s.source_snapshot_ts_utc,s.ladder_snapshot_id
                 ) AS sample_rank
          FROM tmax_v2_ladder_snapshots s
          WHERE s.target_date BETWEEN :start AND :end
            AND s.completeness_status='complete'
            AND s.lineage_status='pit_verified_capture'
        )
        SELECT ladder_snapshot_id,source_path,city,target_date,event_slug,event_identity,
               source_snapshot_ts_utc,available_at_utc,market_unit,market_timezone,
               market_utc_offset_seconds,rung_count,absolute_ladder_signature
        FROM base WHERE sample_rank=1
        ORDER BY city,target_date,event_identity,source_snapshot_ts_utc
        """,
        conn,
        params={"sample_seconds": SAMPLE_SECONDS, "start": start, "end": end},
    )


def _read_rungs(conn: sqlite3.Connection, snapshot_ids: list[str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for chunk in _chunks(snapshot_ids):
        marks = ",".join("?" for _ in chunk)
        frames.append(pd.read_sql_query(
            f"""
            SELECT ladder_snapshot_id,absolute_bracket_identity AS bracket,condition_id,
                   yes_token_id,no_token_id,yes_direct_bid AS yes_bid,
                   yes_direct_ask AS yes_ask,yes_direct_bid_size AS yes_bid_size,
                   yes_direct_ask_size AS yes_ask_size,
                   yes_direct_depth_bid_5c AS yes_depth_bid_5c,
                   yes_direct_depth_ask_5c AS yes_depth_ask_5c,
                   no_direct_bid AS no_bid,no_direct_ask AS no_ask,
                   no_direct_bid_size AS no_bid_size,no_direct_ask_size AS no_ask_size,
                   no_direct_depth_bid_5c AS no_depth_bid_5c,
                   no_direct_depth_ask_5c AS no_depth_ask_5c,
                   yes_book_fetched_at_utc,no_book_fetched_at_utc
            FROM tmax_v2_ladder_rung_quotes
            WHERE ladder_snapshot_id IN ({marks})
            """, conn, params=chunk))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _read_forecasts(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT forecast_capture_id,city,target_date,forecast_model,forecast_values_hash,
               available_at_utc,effective_forecast_first_seen_at_utc AS first_seen_at_utc,
               effective_normalized_hourly_curve_json AS curve_json,lineage_status
        FROM tmax_v2_forecast_captures_enriched
        WHERE target_date BETWEEN ? AND ?
          AND lineage_status='pit_verified_capture'
          AND effective_normalized_hourly_curve_json IS NOT NULL
        ORDER BY city,target_date,available_at_utc
        """, conn, params=(start, end))


def _read_observations(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT tmax_v2_observation_id,city,target_date,temp_f,obs_ts_utc,
               available_at_utc,source_kind,lineage_status
        FROM tmax_v2_observation_event_lineage_enriched
        WHERE target_date BETWEEN ? AND ?
          AND lineage_status='pit_verified_first_seen'
        ORDER BY city,target_date,available_at_utc
        """, conn, params=(start, end))


def _read_settlements(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT city,target_date,bracket,
               MAX(CASE WHEN final_price>=0.999 THEN 1.0
                        WHEN final_price<=0.001 THEN 0.0 ELSE final_price END) AS win
        FROM settlement_outcomes
        WHERE target_date BETWEEN ? AND ? AND settlement_status='settled'
        GROUP BY city,target_date,bracket
        """, conn, params=(start, end))


def _curve_peak(value: Any) -> float:
    try:
        rows = json.loads(str(value))
        values = [float(row["temperature_f"]) for row in rows if row.get("temperature_f") is not None]
        return max(values) if values else math.nan
    except (TypeError, ValueError, json.JSONDecodeError):
        return math.nan


def _asof_weather(snapshots: pd.DataFrame, forecasts: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    output: list[pd.DataFrame] = []
    forecast_groups = {key: group.copy() for key, group in forecasts.groupby(["city", "target_date"])}
    observation_groups = {key: group.copy() for key, group in observations.groupby(["city", "target_date"])}
    for key, group in snapshots.groupby(["city", "target_date"], sort=False):
        work = group.sort_values("snapshot_ts").copy()
        times = work["snapshot_ts"].astype("int64").to_numpy()
        f = forecast_groups.get(key)
        if f is not None and not f.empty:
            f = f.copy()
            f["available_ts"] = pd.to_datetime(f["available_at_utc"], utc=True, format="mixed")
            f["forecast_peak_f"] = f["curve_json"].map(_curve_peak)
            f = f.sort_values("available_ts").drop_duplicates("forecast_values_hash", keep="first")
            f["forecast_peak_shock_f"] = f["forecast_peak_f"].diff()
            f_times = f["available_ts"].astype("int64").to_numpy()
            indices = np.searchsorted(f_times, times, side="right") - 1
            valid = indices >= 0
            for column in ("forecast_capture_id", "forecast_model", "forecast_values_hash", "forecast_peak_f", "forecast_peak_shock_f"):
                values = np.full(len(work), np.nan if column.endswith("_f") else None, dtype=object)
                if valid.any(): values[valid] = f.iloc[indices[valid]][column].to_numpy()
                work[column] = values
            event_times = np.full(len(work), np.datetime64("NaT"), dtype="datetime64[ns]")
            if valid.any(): event_times[valid] = f.iloc[indices[valid]]["available_ts"].dt.tz_localize(None).to_numpy()
            work["forecast_event_ts"] = pd.to_datetime(event_times, utc=True)
            work["forecast_age_min"] = (work["snapshot_ts"] - work["forecast_event_ts"]).dt.total_seconds()/60
        else:
            for column in ("forecast_capture_id", "forecast_model", "forecast_values_hash", "forecast_peak_f", "forecast_peak_shock_f", "forecast_event_ts", "forecast_age_min"):
                work[column] = np.nan
        o = observation_groups.get(key)
        if o is not None and not o.empty:
            o = o.copy()
            o["available_ts"] = pd.to_datetime(o["available_at_utc"], utc=True, format="mixed")
            o = o.sort_values("available_ts")
            o["observed_max_f"] = pd.to_numeric(o["temp_f"], errors="coerce").cummax()
            o["observed_max_shock_f"] = o["observed_max_f"].diff().clip(lower=0)
            o_times = o["available_ts"].astype("int64").to_numpy()
            indices = np.searchsorted(o_times, times, side="right") - 1
            valid = indices >= 0
            for column in ("tmax_v2_observation_id", "observed_max_f", "observed_max_shock_f"):
                values = np.full(len(work), np.nan if column.endswith("_f") else None, dtype=object)
                if valid.any(): values[valid] = o.iloc[indices[valid]][column].to_numpy()
                work[column] = values
            event_times = np.full(len(work), np.datetime64("NaT"), dtype="datetime64[ns]")
            if valid.any(): event_times[valid] = o.iloc[indices[valid]]["available_ts"].dt.tz_localize(None).to_numpy()
            work["observation_event_ts"] = pd.to_datetime(event_times, utc=True)
            work["observation_age_min"] = (work["snapshot_ts"] - work["observation_event_ts"]).dt.total_seconds()/60
        else:
            for column in ("tmax_v2_observation_id", "observed_max_f", "observed_max_shock_f", "observation_event_ts", "observation_age_min"):
                work[column] = np.nan
        output.append(work)
    return pd.concat(output, ignore_index=True)


def _f_center(center: pd.Series, unit: pd.Series) -> pd.Series:
    return np.where(unit.astype(str).str.upper().eq("C"), center * 9.0/5.0 + 32.0, center)


def _prepare_ladder(snapshots: pd.DataFrame, rungs: pd.DataFrame) -> pd.DataFrame:
    frame = snapshots.merge(rungs, on="ladder_snapshot_id", how="left", validate="one_to_many")
    frame["bracket_center"] = frame["bracket"].map(bracket_center)
    frame = frame.sort_values(["city", "target_date", "event_identity", "snapshot_ts", "bracket_center", "condition_id"]).reset_index(drop=True)
    yes_direct = frame["yes_bid"].between(.001,.999) & frame["yes_ask"].between(.001,.999) & frame["yes_ask"].ge(frame["yes_bid"])
    no_direct = frame["no_bid"].between(.001,.999) & frame["no_ask"].between(.001,.999) & frame["no_ask"].ge(frame["no_bid"])
    frame["yes_direct"] = yes_direct
    frame["no_direct"] = no_direct
    frame["yes_mid"] = np.where(yes_direct, (frame["yes_bid"]+frame["yes_ask"])/2, np.nan)
    frame["no_mid"] = np.where(no_direct, (frame["no_bid"]+frame["no_ask"])/2, np.nan)
    frame["spread"] = np.where(yes_direct, frame["yes_ask"]-frame["yes_bid"], np.nan)
    for source, target in (("yes_bid_size","log_bid_size"),("yes_ask_size","log_ask_size"),("yes_depth_bid_5c","log_depth_bid_5c"),("yes_depth_ask_5c","log_depth_ask_5c")):
        frame[target] = np.log1p(pd.to_numeric(frame[source], errors="coerce").clip(lower=0))
    groups = frame.groupby("ladder_snapshot_id", sort=False)
    frame["market_mass"] = groups["yes_mid"].transform("sum")
    frame["market_p"] = frame["yes_mid"] / frame["market_mass"]
    frame["bracket_rank"] = groups.cumcount()
    frame["rank_fraction"] = frame["bracket_rank"] / groups["bracket_rank"].transform("max").replace(0,1)
    mode_indices = frame.groupby("ladder_snapshot_id")["market_p"].idxmax().dropna().astype(int)
    modes = frame.loc[mode_indices, ["ladder_snapshot_id","bracket_rank"]].rename(columns={"bracket_rank":"mode_rank"})
    frame = frame.merge(modes, on="ladder_snapshot_id", how="left", validate="many_to_one")
    groups = frame.groupby("ladder_snapshot_id", sort=False)
    frame["distance_to_mode"] = frame["bracket_rank"]-frame["mode_rank"]
    frame["previous_p"] = groups["market_p"].shift(1)
    frame["next_p"] = groups["market_p"].shift(-1)
    frame["adjacent_log_ratio_left"] = np.log((frame["market_p"]+EPS)/(frame["previous_p"]+EPS))
    frame["adjacent_log_ratio_right"] = np.log((frame["market_p"]+EPS)/(frame["next_p"]+EPS))
    frame["local_curvature"] = .5*(np.log(frame["previous_p"]+EPS)+np.log(frame["next_p"]+EPS))-np.log(frame["market_p"]+EPS)
    frame["kink_score"] = frame["local_curvature"]
    frame["entropy_component"] = -frame["market_p"]*np.log(frame["market_p"].clip(EPS))
    snapshot_state = groups.agg(city=("city","first"),target_date=("target_date","first"),event_identity=("event_identity","first"),snapshot_ts=("snapshot_ts","first"),mode_rank=("mode_rank","first"),entropy=("entropy_component","sum"),mean_rank=("bracket_rank",lambda x: np.nan)).reset_index()
    extra=[]
    for snapshot_id, group in frame.groupby("ladder_snapshot_id", sort=False):
        p=group["market_p"].fillna(0).to_numpy(float); ranks=group["bracket_rank"].to_numpy(float); mode=float(group["mode_rank"].iloc[0])
        mean=float(np.sum(p*ranks)); sd=max(EPS,float(np.sqrt(np.sum(p*(ranks-mean)**2))))
        peaks=int(sum(p[i]>p[i-1] and p[i]>=p[i+1] for i in range(1,len(p)-1)))
        extra.append((snapshot_id,float(np.sum(p*((ranks-mean)/sd)**3)),float(np.sum(p[ranks>=mode+2])),peaks))
    extra=pd.DataFrame(extra,columns=["ladder_snapshot_id","skew","hot_tail_mass","multimodality"])
    snapshot_state=snapshot_state.drop(columns="mean_rank").merge(extra,on="ladder_snapshot_id")
    stream=["city","target_date","event_identity"]
    snapshot_state=snapshot_state.sort_values(stream+["snapshot_ts"])
    for col in ("mode_rank","entropy","skew","hot_tail_mass","multimodality"):
        snapshot_state[f"{col.replace('mode_rank','mode')}_delta"] = snapshot_state.groupby(stream)[col].diff()
    snapshot_state=snapshot_state.rename(columns={"mode_delta":"mode_shift"})
    frame=frame.merge(snapshot_state[["ladder_snapshot_id","mode_shift","entropy","entropy_delta","skew","skew_delta","hot_tail_mass","hot_tail_mass_delta","multimodality","multimodality_delta"]],on="ladder_snapshot_id",how="left")
    rung_stream=stream+["condition_id"]
    frame["prev_market_p"]=frame.groupby(rung_stream)["market_p"].shift(1)
    frame["prev_yes_mid"]=frame.groupby(rung_stream)["yes_mid"].shift(1)
    frame["rung_move_prev"]=frame["yes_mid"]-frame["prev_yes_mid"]
    frame["common_prev_move"]=frame.groupby("ladder_snapshot_id")["rung_move_prev"].transform("median")
    frame["rung_relative_move_prev"]=frame["rung_move_prev"]-frame["common_prev_move"]
    frame["transport_abs"]=(frame["market_p"]-frame["prev_market_p"]).abs()
    frame["transport_signed"]=(frame["market_p"]-frame["prev_market_p"])*frame["distance_to_mode"]
    frame["mass_transport_l1"]=.5*frame.groupby("ladder_snapshot_id")["transport_abs"].transform("sum")
    frame["mass_transport_signed"]=frame.groupby("ladder_snapshot_id")["transport_signed"].transform("sum")
    frame["neighbor_lead_lag"]=(frame.groupby("ladder_snapshot_id")["rung_relative_move_prev"].shift(1)+frame.groupby("ladder_snapshot_id")["rung_relative_move_prev"].shift(-1))/2
    frame["local_hours"]=(frame["snapshot_ts"].astype("int64")/1e9+frame["market_utc_offset_seconds"].fillna(0)-pd.to_datetime(frame["target_date"],utc=True).astype("int64")/1e9)/3600
    frame["lifecycle"]=lifecycle_label(frame["local_hours"])
    # Ecology is rolling and PIT: current state is excluded by shift(1).
    snap_ecology=frame.groupby("ladder_snapshot_id",as_index=False).agg(city=("city","first"),target_date=("target_date","first"),snapshot_ts=("snapshot_ts","first"),spread_median=("spread","median"),depth_median=("yes_depth_ask_5c","median"),mid_fingerprint=("yes_mid",lambda x: hashlib.sha256(np.nan_to_num(x.to_numpy(float),nan=-1).tobytes()).hexdigest()))
    snap_ecology=snap_ecology.sort_values(["city","target_date","snapshot_ts"])
    eco_group=snap_ecology.groupby(["city","target_date"],sort=False)
    snap_ecology["ecology_spread_median"]=eco_group["spread_median"].transform(lambda x:x.shift(1).rolling(12,min_periods=1).median())
    snap_ecology["ecology_depth_median"]=eco_group["depth_median"].transform(lambda x:x.shift(1).rolling(12,min_periods=1).median())
    changed=snap_ecology["mid_fingerprint"].ne(eco_group["mid_fingerprint"].shift(1))
    snap_ecology["quote_lifetime_min"]=snap_ecology.groupby(["city","target_date",changed.groupby([snap_ecology.city,snap_ecology.target_date]).cumsum()])["snapshot_ts"].transform(lambda x:(x-x.iloc[0]).dt.total_seconds()/60)
    frame=frame.merge(snap_ecology[["ladder_snapshot_id","ecology_spread_median","ecology_depth_median","quote_lifetime_min"]],on="ladder_snapshot_id",how="left")
    return frame


def _add_weather_features(frame: pd.DataFrame) -> pd.DataFrame:
    result=frame.copy()
    forecast=pd.to_numeric(result["forecast_peak_f"],errors="coerce")
    observed=pd.to_numeric(result["observed_max_f"],errors="coerce")
    result["weather_center_f"]=np.fmax(forecast,observed)
    result.loc[forecast.isna() & observed.isna(),"weather_center_f"]=np.nan
    center_f=_f_center(result["bracket_center"],result["market_unit"])
    result["weather_raw"]=norm.pdf((center_f-result["weather_center_f"])/WEATHER_SIGMA_F)
    result["weather_p"]=result["weather_raw"]/result.groupby("ladder_snapshot_id")["weather_raw"].transform("sum")
    stream=["city","target_date","event_identity","condition_id"]
    result["previous_weather_p"]=result.groupby(stream)["weather_p"].shift(1)
    result["weather_probability_shock"]=result["weather_p"]-result["previous_weather_p"]
    result["weather_event_ts"]=result[["forecast_event_ts","observation_event_ts"]].max(axis=1)
    result["weather_event_age_min"]=(result["snapshot_ts"]-result["weather_event_ts"]).dt.total_seconds()/60
    result["weather_market_gap"]=result["weather_p"]-result["market_p"]
    result["response_gap"]=result["weather_probability_shock"]-result["rung_relative_move_prev"]
    denom=result["weather_probability_shock"].abs()+EPS
    result["response_completion"]=(result["rung_relative_move_prev"].abs()/denom).clip(0,2)
    result["repricing_time_estimate_min"]=result["weather_event_age_min"]*(1-result["response_completion"].clip(0,1))
    result["lag_pressure_30"]=result["response_gap"]*np.exp(-result["weather_event_age_min"].clip(lower=0)/30)
    result["lag_pressure_60"]=result["response_gap"]*np.exp(-result["weather_event_age_min"].clip(lower=0)/60)
    return result


def _future_map(snapshots: pd.DataFrame, horizon: int) -> pd.DataFrame:
    records=[]
    for _,group in snapshots.groupby(["city","target_date","event_identity"],sort=False):
        group=group.sort_values("snapshot_ts"); times=group["snapshot_ts"].astype("int64").to_numpy(); ids=group["ladder_snapshot_id"].to_numpy()
        targets=times+horizon*60*1_000_000_000; indices=np.searchsorted(times,targets,side="left")
        for pos,index in enumerate(indices):
            if index<len(times) and 0 <= (times[index]-targets[pos])/60e9 <= FUTURE_TOLERANCE_MIN:
                records.append((ids[pos],ids[index]))
    return pd.DataFrame(records,columns=["ladder_snapshot_id",f"h{horizon}_snapshot_id"])


def _add_targets(frame: pd.DataFrame, snapshots: pd.DataFrame) -> pd.DataFrame:
    result=frame.copy()
    future_source=frame[["ladder_snapshot_id","condition_id","yes_mid","yes_bid","yes_ask","no_bid","no_ask","yes_bid_size","yes_ask_size","no_bid_size","no_ask_size"]].copy()
    for horizon in HORIZONS:
        mapping=_future_map(snapshots,horizon)
        result=result.merge(mapping,on="ladder_snapshot_id",how="left")
        future=future_source.rename(columns={"ladder_snapshot_id":f"h{horizon}_snapshot_id",**{col:f"h{horizon}_{col}" for col in future_source.columns if col not in ("ladder_snapshot_id","condition_id")}})
        result=result.merge(future,on=[f"h{horizon}_snapshot_id","condition_id"],how="left",validate="many_to_one")
        result[f"h{horizon}_mid_move"]=result[f"h{horizon}_yes_mid"]-result["yes_mid"]
        result[f"h{horizon}_common_move"]=result.groupby("ladder_snapshot_id")[f"h{horizon}_mid_move"].transform("median")
        result[f"h{horizon}_relative_markout"]=result[f"h{horizon}_mid_move"]-result[f"h{horizon}_common_move"]
        result[f"h{horizon}_direction"]=(result[f"h{horizon}_relative_markout"]>0).astype(float)
    return result


def _fixed_from_source(
    snapshots: pd.DataFrame,
    rungs: pd.DataFrame,
    settlements: pd.DataFrame,
) -> pd.DataFrame:
    """Build the model panel one target date at a time to cap memory use."""
    if snapshots.empty or rungs.empty:
        return pd.DataFrame(columns=_compact_panel_columns())
    snapshot_dates = snapshots.set_index("ladder_snapshot_id")["target_date"]
    rungs = rungs.copy()
    rungs["_target_date"] = rungs["ladder_snapshot_id"].map(snapshot_dates)
    outputs: list[pd.DataFrame] = []
    columns = _compact_panel_columns()
    for target_date in sorted(snapshots["target_date"].unique()):
        day_snapshots = snapshots[snapshots["target_date"].eq(target_date)].copy()
        day_rungs = rungs[rungs["_target_date"].eq(target_date)].drop(columns="_target_date").copy()
        frame = _prepare_ladder(day_snapshots, day_rungs)
        frame = _add_weather_features(frame)
        frame = _add_targets(frame, day_snapshots)
        frame = frame.merge(
            settlements[settlements["target_date"].eq(target_date)],
            on=["city", "target_date", "bracket"],
            how="left",
        )
        fixed = frame[
            frame["yes_direct"]
            & frame[f"h{PRIMARY_HORIZON}_relative_markout"].notna()
        ].copy()
        fixed["feature_book_snapshot_id"] = fixed["ladder_snapshot_id"]
        fixed["future_evaluation_snapshot_id"] = fixed[f"h{PRIMARY_HORIZON}_snapshot_id"]
        outputs.append(fixed.reindex(columns=columns))
        del day_snapshots, day_rungs, frame, fixed
        gc.collect()
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame(columns=columns)


def build_panel(
    db: Path,
    history_snapshot_dir: Path,
    history_cache_path: Path,
    *,
    history_workers: int = 8,
) -> tuple[pd.DataFrame, dict[str,Any]]:
    conn=_connect(db)
    try:
        canonical_snapshots=_read_sampled_snapshots(conn,DEV_START,VALIDATION_END)
        canonical_snapshots["snapshot_ts"]=pd.to_datetime(
            canonical_snapshots["source_snapshot_ts_utc"],utc=True,format="mixed"
        )
        forecasts=_read_forecasts(conn,DEV_START,VALIDATION_END)
        observations=_read_observations(conn,DEV_START,VALIDATION_END)
        canonical_snapshots=_asof_weather(canonical_snapshots,forecasts,observations)
        canonical_snapshots["snapshot_source"]="tmax_v2_canonical"
        canonical_rungs=_read_rungs(conn,canonical_snapshots["ladder_snapshot_id"].tolist())
        settlements=_read_settlements(conn,TRAIN_START,VALIDATION_END)
    finally: conn.close()

    history_coverage=materialize_history_cache(
        history_snapshot_dir, TRAIN_START, TRAIN_END, history_cache_path,
        sample_seconds=SAMPLE_SECONDS,
        workers=history_workers,
    )
    history_dates=history_cache_dates(history_cache_path)
    if not history_dates:
        raise ValueError("historical immutable paper snapshot adapter produced no ladders")
    historical_outputs=[]
    historical_sampled_snapshots=0
    historical_sampled_rungs=0
    historical_cities:set[str]=set()
    for target_date in history_dates:
        day_snapshots,day_rungs=load_history_cache_date(
            history_cache_path,target_date,sample_seconds=SAMPLE_SECONDS
        )
        historical_sampled_snapshots+=day_snapshots.ladder_snapshot_id.nunique()
        historical_sampled_rungs+=len(day_rungs)
        historical_cities.update(day_snapshots.city.dropna().astype(str))
        historical_outputs.append(_fixed_from_source(day_snapshots,day_rungs,settlements))
        del day_snapshots,day_rungs
        gc.collect()
    historical_fixed=pd.concat(historical_outputs,ignore_index=True)
    canonical_fixed = _fixed_from_source(
        canonical_snapshots, canonical_rungs, settlements
    )
    fixed = pd.concat([historical_fixed, canonical_fixed], ignore_index=True)
    coverage={
        "raw_sampled_snapshots":int(historical_sampled_snapshots+canonical_snapshots.ladder_snapshot_id.nunique()),
        "raw_dates":len(set(history_dates)|set(canonical_snapshots.target_date.dropna().astype(str))),
        "raw_cities":len(historical_cities|set(canonical_snapshots.city.dropna().astype(str))),
        "raw_rungs":int(historical_sampled_rungs+len(canonical_rungs)),"fixed_rows":int(len(fixed)),
        "fixed_snapshots":int(fixed.ladder_snapshot_id.nunique()),"fixed_dates":int(fixed.target_date.nunique()),
        "fixed_cities":int(fixed.city.nunique()),"missing_forecast_rows":int(fixed.forecast_peak_f.isna().sum()),
        "missing_observation_rows":int(fixed.observed_max_f.isna().sum()),
        "missing_settlement_rows":int(fixed.win.isna().sum()),
        "denominator_scope":{
            "historical_training":[TRAIN_START,TRAIN_END],
            "development_model_selection":[DEV_START,DEV_END],
            "frozen_historical_validation":[VALIDATION_START,VALIDATION_END],
            "historical_source":"immutable archived paper snapshots",
            "development_validation_source":"canonical tmax_v2 ladder snapshots/rung quotes",
        },
        "historical_snapshot_census":history_coverage,
        "fixed_rows_by_source":{
            str(key):int(value)
            for key,value in fixed.groupby("snapshot_source").size().items()
        },
        "fixed_snapshots_by_source":{
            str(key):int(value)
            for key,value in fixed.groupby("snapshot_source")["ladder_snapshot_id"].nunique().items()
        },
        "legacy_forecast_clock":"snapshot-first-seen interval-censored; pre-hash snapshots use forecast max/model-init/source proxy",
        "legacy_observation_clock":"first snapshot carrying a new METAR observation timestamp; no later observation backfill",
        "m4_status":"blocked_no_historical_ws_materializer_rest_parity_or_trade_prints",
        "m4_raw_policy_window":"2026-08-08 onward; outside development/validation",
    }
    return fixed.reset_index(drop=True),coverage


def build_recent_forward_panel(db:Path)->tuple[pd.DataFrame,dict[str,Any]]:
    """Materialize the post-validation, pre-untouched fixed denominator.

    This window is never used for feature, alpha, or selector selection.  It is
    scored only after the 2026-07-22..28 historical validation artifacts have
    been frozen.
    """
    conn=_connect(db)
    try:
        snapshots=_read_sampled_snapshots(conn,RECENT_FORWARD_START,RECENT_FORWARD_END)
        snapshots["snapshot_ts"]=pd.to_datetime(
            snapshots["source_snapshot_ts_utc"],utc=True,format="mixed"
        )
        forecasts=_read_forecasts(conn,RECENT_FORWARD_START,RECENT_FORWARD_END)
        observations=_read_observations(conn,RECENT_FORWARD_START,RECENT_FORWARD_END)
        snapshots=_asof_weather(snapshots,forecasts,observations)
        snapshots["snapshot_source"]="tmax_v2_canonical"
        rungs=_read_rungs(conn,snapshots["ladder_snapshot_id"].tolist())
        settlements=_read_settlements(conn,RECENT_FORWARD_START,RECENT_FORWARD_END)
    finally:
        conn.close()
    fixed=_fixed_from_source(snapshots,rungs,settlements).reset_index(drop=True)
    coverage={
        "window":[RECENT_FORWARD_START,RECENT_FORWARD_END],
        "raw_snapshots":int(snapshots.ladder_snapshot_id.nunique()),
        "raw_rungs":int(len(rungs)),
        "raw_dates":sorted(snapshots.target_date.dropna().astype(str).unique()),
        "raw_cities":int(snapshots.city.nunique()),
        "fixed_rows":int(len(fixed)),
        "fixed_snapshots":int(fixed.ladder_snapshot_id.nunique()),
        "fixed_dates":sorted(fixed.target_date.dropna().astype(str).unique()),
        "fixed_cities":int(fixed.city.nunique()),
        "missing_forecast_rows":int(fixed.forecast_peak_f.isna().sum()),
        "missing_observation_rows":int(fixed.observed_max_f.isna().sum()),
        "missing_settlement_rows":int(fixed.win.isna().sum()),
        "settled_dates":sorted(fixed.loc[fixed.win.notna(),"target_date"].unique()),
        "denominator_scope":"canonical tmax_v2 ladder snapshots/rung quotes; 30-minute sampled direct YES rows with a PIT 60m future snapshot",
    }
    return fixed,coverage


def _weights(rows: pd.DataFrame) -> np.ndarray:
    counts=rows.groupby("target_date")["target_date"].transform("size").astype(float)
    return (1/counts).to_numpy()


def _pipeline(block: str,alpha:float,classification:bool=False) -> Pipeline:
    numeric=list(FEATURE_BLOCKS[block]); categorical=list(CATEGORICAL)
    pre=ColumnTransformer([
        ("numeric",Pipeline([("impute",SimpleImputer(strategy="median",add_indicator=True)),("scale",StandardScaler())]),numeric),
        ("categorical",OneHotEncoder(handle_unknown="ignore",min_frequency=20),categorical),
    ])
    model=LogisticRegression(C=1/max(alpha,EPS),max_iter=1000,solver="liblinear") if classification else Ridge(alpha=alpha)
    return Pipeline([("pre",pre),("model",model)])


def _fit_predict(train:pd.DataFrame,test:pd.DataFrame,block:str,alpha:float,label:str,classification:bool=False)->tuple[Pipeline,np.ndarray]:
    model=_pipeline(block,alpha,classification); columns=list(FEATURE_BLOCKS[block])+list(CATEGORICAL)
    model.fit(train[columns],train[label].astype(float),model__sample_weight=_weights(train))
    pred=model.predict_proba(test[columns])[:,1] if classification else model.predict(test[columns])
    return model,pred


def development_oof(rows:pd.DataFrame)->pd.DataFrame:
    dev=rows[rows.target_date.between(DEV_START,DEV_END)].copy(); dates=sorted(dev.target_date.unique()); outputs=[]
    for block in FEATURE_BLOCKS:
        for alpha in RIDGE_ALPHAS:
            # Model/block selection is registered on the primary 60m head.
            # The 30m secondary head is fit only after the choice is frozen.
            for horizon in (PRIMARY_HORIZON,):
                label=f"h{horizon}_relative_markout"
                for date in dates:
                    train=rows[
                        rows.target_date.between(TRAIN_START,DEV_END)
                        & rows.target_date.lt(date)
                        & rows[label].notna()
                    ]
                    if train.target_date.nunique()<MIN_OOF_TRAIN_DATES: continue
                    test=dev[(dev.target_date==date)&dev[label].notna()]
                    if test.empty: continue
                    _,pred=_fit_predict(train,test,block,alpha,label)
                    part=test[["target_date","city","ladder_snapshot_id","condition_id",label]].copy(); part["prediction"]=pred; part["block"]=block; part["alpha"]=alpha; part["horizon"]=horizon; outputs.append(part)
    return pd.concat(outputs,ignore_index=True)


def _date_ci(frame:pd.DataFrame,column:str,draws:int,seed:int)->tuple[float,float,float]:
    daily=frame.groupby("target_date")[column].mean().to_numpy(float)
    point=float(np.mean(daily)) if len(daily) else math.nan
    if len(daily)<3:return point,math.nan,math.nan
    rng=np.random.default_rng(seed); sample=daily[rng.integers(0,len(daily),size=(draws,len(daily)))].mean(axis=1)
    low,high=np.quantile(sample,[.025,.975]); return point,float(low),float(high)


def select_development(oof:pd.DataFrame,draws:int,seed:int)->tuple[FrozenChoice,pd.DataFrame]:
    work=oof[oof.horizon.eq(PRIMARY_HORIZON)].copy(); label=f"h{PRIMARY_HORIZON}_relative_markout"; work["loss"]=(work.prediction-work[label])**2
    records=[]
    for (block,alpha),group in work.groupby(["block","alpha"]):
        point,low,high=_date_ci(group,"loss",draws,seed+len(records)); records.append({"block":block,"alpha":alpha,"mse":point,"ci_low":low,"ci_high":high,"rows":len(group),"dates":group.target_date.nunique()})
    table=pd.DataFrame(records).sort_values("mse"); candidate=table[table.block.isin(["M2_ladder_transition","M3_weather_response_lag"])].iloc[0]
    return FrozenChoice(str(candidate.block),float(candidate.alpha)),table


def _normalized_settlement_probability(rows:pd.DataFrame,raw:np.ndarray)->np.ndarray:
    out=pd.Series(raw,index=rows.index).clip(EPS,1-EPS); totals=out.groupby(rows.ladder_snapshot_id).transform("sum"); return (out/totals).to_numpy()


def frozen_validation(rows:pd.DataFrame,choice:FrozenChoice,draws:int,seed:int)->tuple[pd.DataFrame,pd.DataFrame,pd.DataFrame,dict[str,Pipeline]]:
    train_pool=rows[rows.target_date.between(TRAIN_START,DEV_END)].copy(); dev=rows[rows.target_date.between(DEV_START,DEV_END)].copy(); val=rows[rows.target_date.between(VALIDATION_START,VALIDATION_END)].copy(); outputs=[]; models={}
    chosen_alpha={block:(choice.alpha if block==choice.block else float(select_development_alpha(rows,block))) for block in FEATURE_BLOCKS}
    for block,alpha in chosen_alpha.items():
        for horizon in HORIZONS:
            label=f"h{horizon}_relative_markout"; train=train_pool[train_pool[label].notna()]; test=val[val[label].notna()].copy(); model,pred=_fit_predict(train,test,block,alpha,label); models[f"{block}:markout:{horizon}"]=model
            part=test.copy(); part["block"]=block; part["alpha"]=alpha; part["horizon"]=horizon; part["markout_prediction"]=pred
            direction=f"h{horizon}_direction"; dmodel,dpred=_fit_predict(train,test,block,alpha,direction,True); part["direction_probability"]=dpred; models[f"{block}:direction:{horizon}"]=dmodel
            settled_train=train[train.win.notna()]; settled_test=test[test.win.notna()].copy(); smodel,spred=_fit_predict(settled_train,settled_test,block,alpha,"win",True); settled_test["settlement_probability"]=_normalized_settlement_probability(settled_test,spred); models[f"{block}:settlement"]=smodel
            part=part.merge(settled_test[["ladder_snapshot_id","condition_id","settlement_probability"]],on=["ladder_snapshot_id","condition_id"],how="left")
            outputs.append(part)
    scored=pd.concat(outputs,ignore_index=True)
    metrics=metric_table(scored,draws,seed)
    deltas=delta_table(scored,choice.block,draws,seed+1000)
    return scored,metrics,deltas,models


def score_frozen_window(
    rows:pd.DataFrame,
    choice:FrozenChoice,
    start_date:str,
    end_date:str,
    fixed_alphas:dict[str,float],
    *,
    candidate_primary_model:Pipeline|None=None,
)->tuple[pd.DataFrame,dict[str,Pipeline]]:
    """Score a later window without reopening any model-selection decision."""
    train_pool=rows[rows.target_date.between(TRAIN_START,DEV_END)].copy()
    test_pool=rows[rows.target_date.between(start_date,end_date)].copy()
    if test_pool.empty:
        raise ValueError(f"frozen score window has no rows: {start_date}..{end_date}")
    missing_blocks=sorted(set(FEATURE_BLOCKS)-set(fixed_alphas))
    if missing_blocks:
        raise ValueError(f"frozen alpha map missing blocks: {missing_blocks}")
    outputs=[]
    models={}
    for block in FEATURE_BLOCKS:
        alpha=float(fixed_alphas[block])
        for horizon in HORIZONS:
            label=f"h{horizon}_relative_markout"
            train=train_pool[train_pool[label].notna()]
            test=test_pool[test_pool[label].notna()].copy()
            if block==choice.block and horizon==PRIMARY_HORIZON and candidate_primary_model is not None:
                model=candidate_primary_model
                columns=list(FEATURE_BLOCKS[block])+list(CATEGORICAL)
                pred=model.predict(test[columns])
            else:
                model,pred=_fit_predict(train,test,block,alpha,label)
            models[f"{block}:markout:{horizon}"]=model
            part=test.copy()
            part["block"]=block
            part["alpha"]=alpha
            part["horizon"]=horizon
            part["markout_prediction"]=pred
            direction=f"h{horizon}_direction"
            dmodel,dpred=_fit_predict(train,test,block,alpha,direction,True)
            part["direction_probability"]=dpred
            models[f"{block}:direction:{horizon}"]=dmodel
            settled_train=train[train.win.notna()]
            settled_test=test[test.win.notna()].copy()
            if settled_test.empty:
                part["settlement_probability"]=np.nan
            else:
                smodel,spred=_fit_predict(settled_train,settled_test,block,alpha,"win",True)
                settled_test["settlement_probability"]=_normalized_settlement_probability(settled_test,spred)
                models[f"{block}:settlement"]=smodel
                part=part.merge(
                    settled_test[["ladder_snapshot_id","condition_id","settlement_probability"]],
                    on=["ladder_snapshot_id","condition_id"],how="left",
                )
            outputs.append(part)
    return pd.concat(outputs,ignore_index=True),models


def select_development_alpha(rows:pd.DataFrame,block:str)->float:
    # Non-candidate controls use the same fixed alpha grid but are selected on development only.
    dev=rows[rows.target_date.between(DEV_START,DEV_END)].copy()
    dates=sorted(dev.target_date.unique()); scores=[]; label=f"h{PRIMARY_HORIZON}_relative_markout"
    for alpha in RIDGE_ALPHAS:
        losses=[]
        for date in dates:
            train=rows[
                rows.target_date.between(TRAIN_START,DEV_END)
                & rows.target_date.lt(date)
                & rows[label].notna()
            ]
            if train.target_date.nunique()<MIN_OOF_TRAIN_DATES:continue
            test=dev[(dev.target_date==date)&dev[label].notna()]
            _,pred=_fit_predict(train,test,block,alpha,label); losses.extend((pred-test[label].to_numpy())**2)
        scores.append((float(np.mean(losses)),alpha))
    return min(scores)[1]


def metric_table(scored:pd.DataFrame,draws:int,seed:int)->pd.DataFrame:
    records=[]
    for (block,horizon),group in scored.groupby(["block","horizon"]):
        label=f"h{horizon}_relative_markout"; direction=f"h{horizon}_direction"; g=group.copy()
        g["markout_loss"]=(g.markout_prediction-g[label])**2; g["direction_brier"]=(g.direction_probability-g[direction])**2
        p=g.direction_probability.clip(EPS,1-EPS); y=g[direction]; g["direction_logloss"]=-(y*np.log(p)+(1-y)*np.log(1-p))
        settled=[]
        for sid,s in g.dropna(subset=["settlement_probability","win"]).groupby("ladder_snapshot_id"):
            if np.isclose(s.win,1).sum()!=1:continue
            winner=s.win.to_numpy().argmax(); probs=s.settlement_probability.to_numpy(float); labels=s.win.to_numpy(float)
            settled.append((sid,s.target_date.iloc[0],float(np.square(probs-labels).sum()),float(-np.log(max(EPS,probs[winner])))))
        settlement=pd.DataFrame(settled,columns=["sid","target_date","settlement_brier","settlement_logloss"])
        auc=[]
        for _,d in g.groupby("target_date"):
            if d[direction].nunique()==2:auc.append(roc_auc_score(d[direction],d.direction_probability))
        records.append({"block":block,"horizon":horizon,"rows":len(g),"snapshots":g.ladder_snapshot_id.nunique(),"dates":g.target_date.nunique(),"cities":g.city.nunique(),"relative_markout_mse":g.groupby("target_date").markout_loss.mean().mean(),"relative_markout_mae":g.groupby("target_date").apply(lambda x:np.abs(x.markout_prediction-x[label]).mean(),include_groups=False).mean(),"direction_brier":g.groupby("target_date").direction_brier.mean().mean(),"direction_logloss":g.groupby("target_date").direction_logloss.mean().mean(),"direction_auc":float(np.mean(auc)),"settlement_brier":settlement.groupby("target_date").settlement_brier.mean().mean(),"settlement_logloss":settlement.groupby("target_date").settlement_logloss.mean().mean()})
    return pd.DataFrame(records)


def delta_table(scored:pd.DataFrame,candidate:str,draws:int,seed:int)->pd.DataFrame:
    records=[]; controls=["M0_market_level","M1_weather_market","static_kink_control"]
    for horizon in HORIZONS:
        label=f"h{horizon}_relative_markout"; direction=f"h{horizon}_direction"; cand=scored[(scored.block==candidate)&(scored.horizon==horizon)].copy()
        for control in controls:
            base=scored[(scored.block==control)&(scored.horizon==horizon)][["ladder_snapshot_id","condition_id","markout_prediction","direction_probability","settlement_probability"]].rename(columns={"markout_prediction":"base_markout","direction_probability":"base_direction","settlement_probability":"base_settlement_probability"})
            paired=cand.merge(base,on=["ladder_snapshot_id","condition_id"],validate="one_to_one")
            paired["markout_mse_delta"]=(paired.markout_prediction-paired[label])**2-(paired.base_markout-paired[label])**2
            paired["direction_brier_delta"]=(paired.direction_probability-paired[direction])**2-(paired.base_direction-paired[direction])**2
            p=paired.direction_probability.clip(EPS,1-EPS); q=paired.base_direction.clip(EPS,1-EPS); y=paired[direction]
            paired["direction_logloss_delta"]=-(y*np.log(p)+(1-y)*np.log(1-p))+y*np.log(q)+(1-y)*np.log(1-q)
            rec={"candidate":candidate,"control":control,"horizon":horizon,"rows":len(paired),"dates":paired.target_date.nunique()}
            for offset,col in enumerate(("markout_mse_delta","direction_brier_delta","direction_logloss_delta")):
                point,low,high=_date_ci(paired,col,draws,seed+len(records)*10+offset); rec[col]=point; rec[f"{col}_ci_low"]=low; rec[f"{col}_ci_high"]=high
            settlement_rows=[]
            for snapshot_id,s in paired.dropna(subset=["settlement_probability","win"]).groupby("ladder_snapshot_id"):
                if np.isclose(s.win,1).sum()!=1: continue
                base_probs=s.base_settlement_probability.to_numpy(float)
                cand_probs=s.settlement_probability.to_numpy(float); labels=s.win.to_numpy(float); winner=int(labels.argmax())
                settlement_rows.append({"target_date":s.target_date.iloc[0],"settlement_brier_delta":float(np.square(cand_probs-labels).sum()-np.square(base_probs-labels).sum()),"settlement_logloss_delta":float(-np.log(max(EPS,cand_probs[winner]))+np.log(max(EPS,base_probs[winner])))})
            settlement=pd.DataFrame(settlement_rows)
            for offset,col in enumerate(("settlement_brier_delta","settlement_logloss_delta"),start=3):
                point,low,high=_date_ci(settlement,col,draws,seed+len(records)*10+offset); rec[col]=point; rec[f"{col}_ci_low"]=low; rec[f"{col}_ci_high"]=high
            records.append(rec)
    return pd.DataFrame(records)


def city_and_regime_tables(scored:pd.DataFrame,candidate:str,development_rows:pd.DataFrame)->tuple[pd.DataFrame,pd.DataFrame]:
    work=scored[(scored.block==candidate)&(scored.horizon==PRIMARY_HORIZON)].copy(); base=scored[(scored.block=="M0_market_level")&(scored.horizon==PRIMARY_HORIZON)][["ladder_snapshot_id","condition_id","markout_prediction"]].rename(columns={"markout_prediction":"base_prediction"}); work=work.merge(base,on=["ladder_snapshot_id","condition_id"])
    label=f"h{PRIMARY_HORIZON}_relative_markout"; work["candidate_loss"]=(work.markout_prediction-work[label])**2; work["base_loss"]=(work.base_prediction-work[label])**2; work["loss_delta"]=work.candidate_loss-work.base_loss
    city=work.groupby("city",as_index=False).agg(rows=("condition_id","size"),snapshots=("ladder_snapshot_id","nunique"),dates=("target_date","nunique"),candidate_mse=("candidate_loss","mean"),m0_mse=("base_loss","mean"),mse_delta=("loss_delta","mean"),median_spread=("spread","median"),median_depth=("yes_depth_ask_5c","median"))
    spread_edges=development_rows.spread.quantile([.33,.67]).to_list(); depth_edges=development_rows.yes_depth_ask_5c.quantile([.33,.67]).to_list()
    work["liquidity_regime"]=pd.cut(work.spread,[-np.inf,*spread_edges,np.inf],labels=["tight","medium","wide"]).astype(str)+"_"+pd.cut(work.yes_depth_ask_5c,[-np.inf,*depth_edges,np.inf],labels=["thin","medium","deep"]).astype(str)
    regime=work.groupby("liquidity_regime",as_index=False).agg(rows=("condition_id","size"),snapshots=("ladder_snapshot_id","nunique"),dates=("target_date","nunique"),cities=("city","nunique"),candidate_mse=("candidate_loss","mean"),m0_mse=("base_loss","mean"),mse_delta=("loss_delta","mean"))
    return city,regime


def leave_one_city_out(scored:pd.DataFrame,candidate:str)->pd.DataFrame:
    """Report pooled validation sensitivity after omitting each city.

    This is deliberately reporting-only: it neither reopens model selection nor
    uses a validation city to create an allowlist.
    """
    label=f"h{PRIMARY_HORIZON}_relative_markout"
    cand=scored[(scored.block==candidate)&(scored.horizon==PRIMARY_HORIZON)].copy()
    base=scored[(scored.block=="M0_market_level")&(scored.horizon==PRIMARY_HORIZON)][
        ["ladder_snapshot_id","condition_id","markout_prediction"]
    ].rename(columns={"markout_prediction":"m0_prediction"})
    paired=cand.merge(base,on=["ladder_snapshot_id","condition_id"],validate="one_to_one")
    paired["candidate_loss"]=(paired.markout_prediction-paired[label])**2
    paired["m0_loss"]=(paired.m0_prediction-paired[label])**2
    records=[]
    for city in sorted(paired.city.dropna().unique()):
        kept=paired[paired.city!=city]
        records.append({
            "omitted_city":city,"rows":len(kept),"dates":kept.target_date.nunique(),
            "candidate_mse":float(kept.groupby("target_date").candidate_loss.mean().mean()),
            "m0_mse":float(kept.groupby("target_date").m0_loss.mean().mean()),
            "mse_delta":float(kept.groupby("target_date").apply(
                lambda x:(x.candidate_loss-x.m0_loss).mean(),include_groups=False
            ).mean()),
        })
    return pd.DataFrame(records)


def _fee_per_share(price:pd.Series|float)->pd.Series|float:
    return FEE_RATE*price*(1-price)


def _execution_ci(rows:pd.DataFrame,column:str,draws:int,seed:int)->dict[str,float]:
    daily=rows.groupby("target_date",as_index=False).agg(
        pnl=(column,"sum"),cost=(f"{column}_cost","sum")
    )
    point=float(daily.pnl.sum())
    roi=float(point/daily.cost.sum()) if daily.cost.sum()>0 else math.nan
    if len(daily)<3:
        return {"pnl_usd":point,"roi":roi,"ci_low":math.nan,"ci_high":math.nan}
    rng=np.random.default_rng(seed)
    indices=rng.integers(0,len(daily),size=(draws,len(daily)))
    samples=daily.pnl.to_numpy()[indices].sum(axis=1)
    low,high=np.quantile(samples,[.025,.975])
    return {"pnl_usd":point,"roi":roi,"ci_low":float(low),"ci_high":float(high)}


def _candidate_expressions(scored:pd.DataFrame,candidate:str)->tuple[pd.DataFrame,dict[str,int]]:
    rows=scored[(scored.block==candidate)&(scored.horizon==PRIMARY_HORIZON)].copy()
    records=[]
    for snapshot_id,group in rows.groupby("ladder_snapshot_id",sort=False):
        group=group.dropna(subset=["markout_prediction"])
        if len(group)<2:
            continue
        target=group.loc[group.markout_prediction.idxmax()]
        hedge=group.loc[group.markout_prediction.idxmin()]
        edge=float(target.markout_prediction-hedge.markout_prediction)
        label=f"h{PRIMARY_HORIZON}_relative_markout"
        records.append({
            "ladder_snapshot_id":snapshot_id,"city":target.city,
            "target_date":target.target_date,"event_identity":target.event_identity,
            "snapshot_ts":target.snapshot_ts,"feature_book_snapshot_id":target.feature_book_snapshot_id,
            "future_evaluation_snapshot_id":target.future_evaluation_snapshot_id,
            "target_condition_id":target.condition_id,"hedge_condition_id":hedge.condition_id,
            "target_bracket":target.bracket,"hedge_bracket":hedge.bracket,
            "predicted_pair_markout":edge,
            "actual_pair_relative_markout":float(target[label]-hedge[label]),
            "target_yes_bid":target.yes_bid,"target_yes_ask":target.yes_ask,
            "target_yes_bid_size":target.yes_bid_size,"target_yes_ask_size":target.yes_ask_size,
            "hedge_no_bid":hedge.no_bid,"hedge_no_ask":hedge.no_ask,
            "hedge_no_bid_size":hedge.no_bid_size,"hedge_no_ask_size":hedge.no_ask_size,
            "future_target_yes_bid":target[f"h{PRIMARY_HORIZON}_yes_bid"],
            "future_target_yes_ask":target[f"h{PRIMARY_HORIZON}_yes_ask"],
            "future_target_yes_bid_size":target[f"h{PRIMARY_HORIZON}_yes_bid_size"],
            "future_target_yes_ask_size":target[f"h{PRIMARY_HORIZON}_yes_ask_size"],
            "future_hedge_no_bid":hedge[f"h{PRIMARY_HORIZON}_no_bid"],
            "future_hedge_no_ask":hedge[f"h{PRIMARY_HORIZON}_no_ask"],
            "future_hedge_no_bid_size":hedge[f"h{PRIMARY_HORIZON}_no_bid_size"],
            "future_hedge_no_ask_size":hedge[f"h{PRIMARY_HORIZON}_no_ask_size"],
            "target_win":target.win,"hedge_win":hedge.win,
        })
    expressions=pd.DataFrame(records)
    funnel={"validation_snapshots":int(rows.ladder_snapshot_id.nunique()),"pair_constructed":len(expressions)}
    if expressions.empty:
        funnel.update({
            "positive_continuous_signal":0,
            "non_overlapping_first_cross":0,
            "all_four_style_quotes":0,
            "one_share_depth_all_legs":0,
        })
        return expressions,funnel
    expressions=expressions[expressions.predicted_pair_markout>0].copy()
    funnel["positive_continuous_signal"]=len(expressions)
    expressions["snapshot_ts"]=pd.to_datetime(expressions.snapshot_ts,utc=True,format="mixed")
    selected=[]
    for _,group in expressions.sort_values("snapshot_ts").groupby(
        ["city","target_date","event_identity"],sort=False
    ):
        next_entry=pd.Timestamp.min.tz_localize("UTC")
        for index,row in group.iterrows():
            if row.snapshot_ts>=next_entry:
                selected.append(index)
                next_entry=row.snapshot_ts+pd.Timedelta(minutes=PRIMARY_HORIZON)
    expressions=expressions.loc[selected].copy()
    funnel["non_overlapping_first_cross"]=len(expressions)
    quote_columns=[
        "target_yes_bid","target_yes_ask","hedge_no_bid","hedge_no_ask",
        "future_target_yes_bid","future_target_yes_ask",
        "future_hedge_no_bid","future_hedge_no_ask",
    ]
    size_columns=[
        "target_yes_bid_size","target_yes_ask_size","hedge_no_bid_size","hedge_no_ask_size",
        "future_target_yes_bid_size","future_target_yes_ask_size",
        "future_hedge_no_bid_size","future_hedge_no_ask_size",
    ]
    executable=expressions[expressions[quote_columns].notna().all(axis=1)].copy()
    funnel["all_four_style_quotes"]=len(executable)
    executable=executable[executable[size_columns].ge(1.0).all(axis=1)].copy()
    funnel["one_share_depth_all_legs"]=len(executable)
    return executable.reset_index(drop=True),funnel


def selector_direction_diagnostics(scored:pd.DataFrame,candidate:str)->dict[str,Any]:
    """Separate rank signal, crossing cost, fees, and a sign-flip control."""
    candidate_rows=scored[
        (scored.block==candidate)&(scored.horizon==PRIMARY_HORIZON)
    ].copy()
    label=f"h{PRIMARY_HORIZON}_relative_markout"
    direction=f"h{PRIMARY_HORIZON}_direction"
    prediction=candidate_rows.markout_prediction.to_numpy(float)
    actual=candidate_rows[label].to_numpy(float)
    daily_candidate=(candidate_rows.assign(
        loss=(candidate_rows.markout_prediction-candidate_rows[label])**2,
        reverse_loss=(-candidate_rows.markout_prediction-candidate_rows[label])**2,
        zero_loss=candidate_rows[label]**2,
    ).groupby("target_date")[["loss","reverse_loss","zero_loss"]].mean())

    def expression(sign:float)->dict[str,Any]:
        frame=scored.copy()
        mask=(frame.block==candidate)&(frame.horizon==PRIMARY_HORIZON)
        frame.loc[mask,"markout_prediction"]*=sign
        rows,funnel=_candidate_expressions(frame,candidate)
        if rows.empty:
            return {"funnel":funnel,"rows":0}
        entry=rows.target_yes_ask+rows.hedge_no_ask
        exit_=rows.future_target_yes_bid+rows.future_hedge_no_bid
        entry_fee=_fee_per_share(rows.target_yes_ask)+_fee_per_share(rows.hedge_no_ask)
        exit_fee=_fee_per_share(rows.future_target_yes_bid)+_fee_per_share(rows.future_hedge_no_bid)
        rows=rows.assign(
            gross_ask_to_bid=exit_-entry,
            fees=entry_fee+exit_fee,
            net_taker=exit_-entry-entry_fee-exit_fee,
        )
        daily=rows.groupby("target_date",as_index=False).agg(
            rows=("ladder_snapshot_id","size"),
            pure_relative=("actual_pair_relative_markout","sum"),
            gross_ask_to_bid=("gross_ask_to_bid","sum"),
            fees=("fees","sum"),
            net_taker=("net_taker","sum"),
        )
        def total_ci(column:str)->dict[str,float]:
            values=daily[column].to_numpy(float)
            if len(values)<3:
                return {"point":float(values.sum()),"ci_low":math.nan,"ci_high":math.nan}
            rng=np.random.default_rng(20260809+(0 if sign>0 else 1))
            samples=values[rng.integers(0,len(values),size=(3000,len(values)))].sum(axis=1)
            low,high=np.quantile(samples,[.025,.975])
            return {"point":float(values.sum()),"ci_low":float(low),"ci_high":float(high)}
        return {
            "funnel":funnel,
            "rows":int(len(rows)),
            "dates":int(rows.target_date.nunique()),
            "pure_relative_sum":float(rows.actual_pair_relative_markout.sum()),
            "pure_relative_mean":float(rows.actual_pair_relative_markout.mean()),
            "pure_relative_positive_dates":int((daily.pure_relative>0).sum()),
            "gross_ask_to_bid_pnl":float(rows.gross_ask_to_bid.sum()),
            "spread_crossing_cost":float((rows.actual_pair_relative_markout-rows.gross_ask_to_bid).sum()),
            "fees":float(rows.fees.sum()),
            "net_taker_pnl":float(rows.net_taker.sum()),
            "target_date_bootstrap":{
                column:total_ci(column)
                for column in ("pure_relative","gross_ask_to_bid","net_taker")
            },
            "daily":daily.to_dict("records"),
        }

    corr=float(np.corrcoef(prediction,actual)[0,1]) if len(prediction)>1 else math.nan
    return {
        "row_level":{
            "rows":int(len(candidate_rows)),
            "dates":int(candidate_rows.target_date.nunique()),
            "actual_positive_rate":float(candidate_rows[direction].mean()),
            "regression_sign_accuracy":float(np.mean((prediction>=0)==(actual>=0))),
            "direction_probability_accuracy":float(np.mean((candidate_rows.direction_probability>=.5)==candidate_rows[direction].astype(bool))),
            "always_nonpositive_accuracy":float(np.mean(candidate_rows[direction].eq(0))),
            "prediction_actual_correlation":corr,
            "candidate_date_equal_mse":float(daily_candidate.loss.mean()),
            "reverse_date_equal_mse":float(daily_candidate.reverse_loss.mean()),
            "zero_date_equal_mse":float(daily_candidate.zero_loss.mean()),
        },
        "frozen_selector":expression(1.0),
        "sign_flipped_selector_control":expression(-1.0),
    }


def evaluate_execution(
    scored:pd.DataFrame,candidate:str,output_dir:Path,draws:int,seed:int
)->dict[str,Any]:
    rows,funnel=_candidate_expressions(scored,candidate)
    if rows.empty:
        return {"status":"REJECTED_FOR_EXPRESSION","funnel":funnel,"reason":"no_one_share_four_style_quote_rows"}
    styles={
        "taker_entry_taker_exit":(
            rows.target_yes_ask,rows.hedge_no_ask,
            rows.future_target_yes_bid,rows.future_hedge_no_bid,True,True
        ),
        "maker_entry_taker_exit":(
            rows.target_yes_bid,rows.hedge_no_bid,
            rows.future_target_yes_bid,rows.future_hedge_no_bid,False,True
        ),
        "taker_entry_maker_exit":(
            rows.target_yes_ask,rows.hedge_no_ask,
            rows.future_target_yes_ask,rows.future_hedge_no_ask,True,False
        ),
        "maker_entry_maker_exit":(
            rows.target_yes_bid,rows.hedge_no_bid,
            rows.future_target_yes_ask,rows.future_hedge_no_ask,False,False
        ),
    }
    metrics=[]
    for index,(name,(entry_a,entry_b,exit_a,exit_b,entry_taker,exit_taker)) in enumerate(styles.items()):
        entry_fee=(_fee_per_share(entry_a)+_fee_per_share(entry_b)) if entry_taker else 0.0
        exit_fee=(_fee_per_share(exit_a)+_fee_per_share(exit_b)) if exit_taker else 0.0
        rows[f"{name}_cost"]=entry_a+entry_b+entry_fee
        rows[name]=exit_a+exit_b-exit_fee-rows[f"{name}_cost"]
        ci=_execution_ci(rows,name,draws,seed+index)
        ci.update({"style":name,"rows":len(rows),"dates":rows.target_date.nunique(),
                   "maker_fill_status":"not_inferred_future_touch_is_not_fill" if "maker" in name else "not_applicable"})
        metrics.append(ci)
    tt=rows.taker_entry_taker_exit
    for name in ("maker_entry_taker_exit","taker_entry_maker_exit","maker_entry_maker_exit"):
        gain=rows[name]-tt
        required=np.where(gain>0,np.maximum(-tt/gain,0),np.nan)
        rows[f"{name}_break_even_fill_rate"]=required
    rows["maker_exit_unfilled_taker_unwind_pnl"]=rows.taker_entry_taker_exit
    maker_entry_cost=rows.target_yes_bid+rows.hedge_no_bid
    maker_hold_payout=rows.target_win+(1-rows.hedge_win)
    rows["maker_exit_unfilled_hold_settlement_pnl"]=maker_hold_payout-maker_entry_cost
    rows.to_csv(output_dir/"execution_expressions.csv.gz",index=False,compression="gzip")
    daily=rows.groupby("target_date",as_index=False).agg(
        rows=("ladder_snapshot_id","size"),cities=("city","nunique"),
        taker_entry_taker_exit_pnl=("taker_entry_taker_exit","sum"),
        maker_entry_taker_exit_pnl=("maker_entry_taker_exit","sum"),
        taker_entry_maker_exit_pnl=("taker_entry_maker_exit","sum"),
        maker_entry_maker_exit_pnl=("maker_entry_maker_exit","sum"),
    )
    daily.to_csv(output_dir/"execution_by_target_date.csv",index=False)
    city=rows.groupby("city",as_index=False).agg(
        rows=("ladder_snapshot_id","size"),dates=("target_date","nunique"),
        taker_entry_taker_exit_pnl=("taker_entry_taker_exit","sum"),
        maker_entry_taker_exit_pnl=("maker_entry_taker_exit","sum"),
    )
    city.to_csv(output_dir/"execution_by_city.csv",index=False)
    tt_metric=next(item for item in metrics if item["style"]=="taker_entry_taker_exit")
    fill_rates={name:{
        "median":float(rows[f"{name}_break_even_fill_rate"].median()),
        "p90":float(rows[f"{name}_break_even_fill_rate"].quantile(.9)),
        "finite_rows":int(rows[f"{name}_break_even_fill_rate"].notna().sum()),
    } for name in ("maker_entry_taker_exit","taker_entry_maker_exit","maker_entry_maker_exit")}
    adverse=[]
    maker_legs={"maker_entry_taker_exit":2,"taker_entry_maker_exit":2,"maker_entry_maker_exit":4}
    for name,legs in maker_legs.items():
        for penalty in (0.0,.005,.01,.02):
            adverse.append({"style":name,"maker_adverse_selection_per_leg":penalty,
                            "pnl_usd":float((rows[name]-legs*penalty).sum())})
    # Same snapshots, generic market-making control: quote both outcomes on the
    # widest complementary rung and unwind both as taker after 60m.
    generic=[]
    source=scored[(scored.block==candidate)&(scored.horizon==PRIMARY_HORIZON)]
    for snapshot_id in rows.ladder_snapshot_id:
        group=source[source.ladder_snapshot_id==snapshot_id].copy()
        group["maker_discount"]=1-group.yes_bid-group.no_bid
        rung=group.loc[group.maker_discount.idxmax()]
        cost=rung.yes_bid+rung.no_bid
        proceeds=rung[f"h{PRIMARY_HORIZON}_yes_bid"]+rung[f"h{PRIMARY_HORIZON}_no_bid"]
        proceeds-=_fee_per_share(rung[f"h{PRIMARY_HORIZON}_yes_bid"])+_fee_per_share(rung[f"h{PRIMARY_HORIZON}_no_bid"])
        generic.append(float(proceeds-cost))
    generic_pnl=float(np.nansum(generic))
    concentration={
        "largest_abs_date_share":float(daily.taker_entry_taker_exit_pnl.abs().max()/daily.taker_entry_taker_exit_pnl.abs().sum()),
        "largest_abs_city_share":float(city.taker_entry_taker_exit_pnl.abs().max()/city.taker_entry_taker_exit_pnl.abs().sum()),
    }
    status="SHADOW_READY" if tt_metric["ci_low"]>0 else "REJECTED_FOR_EXPRESSION"
    tt_shortfall=max(0.0,-float(rows.taker_entry_taker_exit.sum()))
    mean_pair_improvement=tt_shortfall/len(rows)
    return {
        "status":status,"funnel":funnel,"shares_per_leg":1,
        "selector":"positive predicted max-minus-min M2 rung-relative markout; first non-overlapping cross per city-date-event",
        "hold_minutes":PRIMARY_HORIZON,"metrics":metrics,"break_even_fill_rates":fill_rates,
        "break_even_price_improvement":{
            "joint_pair_per_expression":mean_pair_improvement,
            "per_entry_leg_if_entry_only":mean_pair_improvement/2,
            "per_exit_leg_if_exit_only":mean_pair_improvement/2,
            "per_leg_if_four_leg_joint":mean_pair_improvement/4,
        },
        "adverse_selection":adverse,"generic_ladder_maker":{"style":"conditional maker entry+taker exit","pnl_usd":generic_pnl,"fill_status":"unknown"},
        "maker_exit_fallback":{"taker_unwind_pnl_usd":float(rows.maker_exit_unfilled_taker_unwind_pnl.sum()),
                               "hold_to_settlement_pnl_usd":float(rows.maker_exit_unfilled_hold_settlement_pnl.sum())},
        "concentration":concentration,
        "reason":None if status=="SHADOW_READY" else "only conditional maker prices can improve economics; historical fill readiness is unavailable",
    }


def finalize_saved_validation(
    output_dir:Path,db:Path,history_cache_path:Path|None=None,
    *,draws:int=3000,seed:int=20260809,
)->dict[str,Any]:
    """Finish a run from immutable panel/OOF/frozen-validation artifacts."""
    panel=pd.read_csv(output_dir/"fixed_panel.csv.gz",low_memory=False)
    oof=pd.read_csv(output_dir/"development_oof.csv.gz",low_memory=False)
    tournament=pd.read_csv(output_dir/"development_tournament.csv")
    scored=pd.read_csv(output_dir/"frozen_validation_scores.csv.gz",low_memory=False)
    metrics=pd.read_csv(output_dir/"frozen_validation_metrics.csv")
    deltas=pd.read_csv(output_dir/"frozen_validation_deltas.csv")
    selected=tournament[tournament.block.isin(["M2_ladder_transition","M3_weather_response_lag"])].sort_values("mse").iloc[0]
    choice=FrozenChoice(str(selected.block),float(selected.alpha))
    dev_panel=panel[panel.target_date.between(DEV_START,DEV_END)]
    city,regime=city_and_regime_tables(scored,choice.block,dev_panel)
    city.to_csv(output_dir/"validation_by_city.csv",index=False)
    regime.to_csv(output_dir/"validation_by_liquidity_regime.csv",index=False)
    loco=leave_one_city_out(scored,choice.block)
    loco.to_csv(output_dir/"leave_one_city_out.csv",index=False)
    label=f"h{PRIMARY_HORIZON}_relative_markout"
    train=panel[panel.target_date.between(TRAIN_START,DEV_END)&panel[label].notna()]
    validation=panel[panel.target_date.between(VALIDATION_START,VALIDATION_END)&panel[label].notna()]
    model,_=_fit_predict(train,validation,choice.block,choice.alpha,label)
    joblib.dump(model,output_dir/"candidate_markout_model.joblib")
    primary=deltas[deltas.horizon.eq(PRIMARY_HORIZON)]
    markout_pass=bool(len(primary)==3 and primary.markout_mse_delta_ci_high.lt(0).all())
    direction_pass=bool(primary.direction_brier_delta.le(0).all() and primary.direction_logloss_delta.le(0).all())
    probability_pass=markout_pass and direction_pass
    execution=(evaluate_execution(scored,choice.block,output_dir,draws,seed+5000)
               if probability_pass else {"status":"NOT_RUN_PROBABILITY_GATE_FAILED"})
    status=execution["status"] if probability_pass else "BRANCH_EXHAUSTED"

    work=scored[(scored.block==choice.block)&(scored.horizon==PRIMARY_HORIZON)].copy()
    base=scored[(scored.block=="M0_market_level")&(scored.horizon==PRIMARY_HORIZON)][
        ["ladder_snapshot_id","condition_id","markout_prediction","direction_probability"]
    ].rename(columns={"markout_prediction":"m0_prediction","direction_probability":"m0_direction_probability"})
    work=work.merge(base,on=["ladder_snapshot_id","condition_id"],validate="one_to_one")
    work["candidate_markout_loss"]=(work.markout_prediction-work[label])**2
    work["m0_markout_loss"]=(work.m0_prediction-work[label])**2
    work["markout_loss_delta"]=work.candidate_markout_loss-work.m0_markout_loss
    daily=work.groupby("target_date",as_index=False).agg(
        rows=("condition_id","size"),snapshots=("ladder_snapshot_id","nunique"),cities=("city","nunique"),
        candidate_markout_mse=("candidate_markout_loss","mean"),m0_markout_mse=("m0_markout_loss","mean"),
        markout_mse_delta=("markout_loss_delta","mean")
    )
    daily.to_csv(output_dir/"validation_by_target_date.csv",index=False)
    work.groupby(["target_date","city"],as_index=False).agg(
        rows=("condition_id","size"),snapshots=("ladder_snapshot_id","nunique"),
        candidate_markout_mse=("candidate_markout_loss","mean"),m0_markout_mse=("m0_markout_loss","mean"),
        markout_mse_delta=("markout_loss_delta","mean")
    ).to_csv(output_dir/"validation_by_city_date.csv",index=False)
    calibration=[]
    bins=np.linspace(0,1,11)
    for (block,horizon),group in scored.groupby(["block","horizon"]):
        dcol=f"h{horizon}_direction"; group=group.copy()
        group["bin"]=pd.cut(group.direction_probability,bins,include_lowest=True)
        for bucket,g in group.groupby("bin",observed=True):
            calibration.append({"head":"direction","block":block,"horizon":horizon,"bin":str(bucket),"rows":len(g),"mean_prediction":g.direction_probability.mean(),"observed_rate":g[dcol].mean()})
        group["settlement_bin"]=pd.cut(group.settlement_probability,bins,include_lowest=True)
        for bucket,g in group.dropna(subset=["settlement_probability","win"]).groupby("settlement_bin",observed=True):
            calibration.append({"head":"settlement","block":block,"horizon":horizon,"bin":str(bucket),"rows":len(g),"mean_prediction":g.settlement_probability.mean(),"observed_rate":g.win.mean()})
    pd.DataFrame(calibration).to_csv(output_dir/"validation_calibration_bins.csv",index=False)

    history_coverage={}
    if history_cache_path is not None and history_cache_path.exists():
        conn=sqlite3.connect(f"file:{history_cache_path}?mode=ro",uri=True)
        try:
            row=conn.execute("SELECT value FROM metadata WHERE key='coverage'").fetchone()
            history_coverage=json.loads(row[0]) if row else {}
        finally: conn.close()
    coverage={
        "fixed_rows":len(panel),"fixed_snapshots":panel.ladder_snapshot_id.nunique(),
        "fixed_dates":panel.target_date.nunique(),"fixed_cities":panel.city.nunique(),
        "dates_by_phase":{
            "historical_training":sorted(panel[panel.target_date.between(TRAIN_START,TRAIN_END)].target_date.unique()),
            "development":sorted(dev_panel.target_date.unique()),
            "frozen_validation":sorted(panel[panel.target_date.between(VALIDATION_START,VALIDATION_END)].target_date.unique()),
        },
        "rows_by_phase":{
            "historical_training":int(panel.target_date.between(TRAIN_START,TRAIN_END).sum()),
            "development":int(panel.target_date.between(DEV_START,DEV_END).sum()),
            "frozen_validation":int(panel.target_date.between(VALIDATION_START,VALIDATION_END).sum()),
        },
        "fixed_rows_by_source":{str(k):int(v) for k,v in panel.groupby("snapshot_source").size().items()},
        "historical_snapshot_census":history_coverage,
        "missing_forecast_rows":int(panel.forecast_peak_f.isna().sum()),
        "missing_observation_rows":int(panel.observed_max_f.isna().sum()),
        "missing_settlement_rows":int(panel.win.isna().sum()),
        "m4_status":"blocked_no_historical_ws_materializer_rest_parity_or_trade_prints",
        "denominator_scope":{
            "historical_training":[TRAIN_START,TRAIN_END],"development_model_selection":[DEV_START,DEV_END],
            "frozen_historical_validation":[VALIDATION_START,VALIDATION_END],"true_untouched_forward":[FORWARD_START,FORWARD_END],
        },
    }
    resolved=db.resolve(strict=True); stat=resolved.stat()
    freeze={
        "schema_version":SCHEMA_VERSION,"run_id":RUN_ID,"status":status,
        "choice":{"block":choice.block,"alpha":choice.alpha,"horizon":choice.horizon},
        "selector":execution.get("selector"),"shares_per_leg":execution.get("shares_per_leg"),
        "hold_minutes":execution.get("hold_minutes"),"entry_exit_styles":"four fixed styles in execution_expressions.csv.gz",
        "historical_training":[TRAIN_START,TRAIN_END],"development_model_selection":[DEV_START,DEV_END],
        "frozen_historical_validation":[VALIDATION_START,VALIDATION_END],"true_untouched_forward":[FORWARD_START,FORWARD_END],
        "validation_access":{"already_seen_in_superseded_narrow_run":True,"corrected_time_aligned_replay_count":1,"fully_untouched":False},
        "feature_blocks":{key:list(value) for key,value in FEATURE_BLOCKS.items()},
        "m4_status":coverage["m4_status"],"model_sha256":_artifact_sha256(output_dir/"candidate_markout_model.joblib"),
        "notional_usd":0.0,"orders_enabled":False,"live_enabled":False,
    }
    (output_dir/"model_freeze.json").write_text(json.dumps(freeze,indent=2,ensure_ascii=False,default=str)+"\n")
    summary={
        "schema_version":SCHEMA_VERSION,"generated_at_utc":datetime.now(UTC).isoformat(),"status":status,
        "probability_gate":{"passed":probability_pass,"markout_pass":markout_pass,"direction_pass":direction_pass},
        "coverage":coverage,"choice":freeze["choice"],"development_tournament":tournament.to_dict("records"),
        "validation_metrics":metrics.to_dict("records"),"validation_deltas":deltas.to_dict("records"),
        "leave_one_city_out":{"cities":len(loco),"all_omissions_candidate_better":bool(loco.mse_delta.lt(0).all()),"worst_delta":float(loco.mse_delta.max())},
        "execution":execution,"true_untouched_forward":{"window":[FORWARD_START,FORWARD_END],"status":"NA_not_started_as_of_2026-08-09","used_for_selection":False},
        "canonical_identity":{"db_argument":str(db),"db_realpath":str(resolved),"device":stat.st_dev,"inode":stat.st_ino,"size_bytes":stat.st_size,"mtime_utc":datetime.fromtimestamp(stat.st_mtime,UTC).isoformat()},
        "production":{"shadow_started":False,"notional_usd":0.0,"orders":0,"fills":0},
    }
    summary["artifact_hashes"]={name:_artifact_sha256(output_dir/name) for name in (
        "fixed_panel.csv.gz","development_oof.csv.gz","frozen_validation_scores.csv.gz",
        "candidate_markout_model.joblib","execution_expressions.csv.gz",
    ) if (output_dir/name).exists()}
    summary["code_hashes"]={"mass_transport_module":_artifact_sha256(Path(__file__)),"unified_runner":_artifact_sha256(Path(__file__).with_name("lmvm_repricing_challenger.py"))}
    (output_dir/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False,default=str)+"\n")
    return summary


def postprocess_artifact(output_dir:Path,draws:int=3000,seed:int=20260809,db:Path|None=None)->dict[str,Any]:
    """Add reporting-only tables without refitting or reopening validation."""
    scored=pd.read_csv(output_dir/"frozen_validation_scores.csv.gz",low_memory=False)
    panel=pd.read_csv(output_dir/"fixed_panel.csv.gz",low_memory=False)
    summary=json.loads((output_dir/"summary.json").read_text())
    candidate=str(summary["choice"]["block"])
    deltas=delta_table(scored,candidate,draws,seed+1000)
    deltas.to_csv(output_dir/"frozen_validation_deltas.csv",index=False)
    city,regime=city_and_regime_tables(scored,candidate,panel[panel.target_date.between(DEV_START,DEV_END)])
    city.to_csv(output_dir/"validation_by_city.csv",index=False); regime.to_csv(output_dir/"validation_by_liquidity_regime.csv",index=False)
    label=f"h{PRIMARY_HORIZON}_relative_markout"; direction=f"h{PRIMARY_HORIZON}_direction"
    work=scored[(scored.block==candidate)&(scored.horizon==PRIMARY_HORIZON)].copy()
    base=scored[(scored.block=="M0_market_level")&(scored.horizon==PRIMARY_HORIZON)][["ladder_snapshot_id","condition_id","markout_prediction","direction_probability"]].rename(columns={"markout_prediction":"m0_prediction","direction_probability":"m0_direction_probability"})
    work=work.merge(base,on=["ladder_snapshot_id","condition_id"],validate="one_to_one")
    work["candidate_markout_loss"]=(work.markout_prediction-work[label])**2; work["m0_markout_loss"]=(work.m0_prediction-work[label])**2; work["markout_loss_delta"]=work.candidate_markout_loss-work.m0_markout_loss
    work["candidate_direction_brier"]=(work.direction_probability-work[direction])**2; work["m0_direction_brier"]=(work.m0_direction_probability-work[direction])**2
    daily=work.groupby("target_date",as_index=False).agg(rows=("condition_id","size"),snapshots=("ladder_snapshot_id","nunique"),cities=("city","nunique"),candidate_markout_mse=("candidate_markout_loss","mean"),m0_markout_mse=("m0_markout_loss","mean"),markout_mse_delta=("markout_loss_delta","mean"),candidate_direction_brier=("candidate_direction_brier","mean"),m0_direction_brier=("m0_direction_brier","mean"))
    daily.to_csv(output_dir/"validation_by_target_date.csv",index=False)
    city_date=work.groupby(["target_date","city"],as_index=False).agg(rows=("condition_id","size"),snapshots=("ladder_snapshot_id","nunique"),candidate_markout_mse=("candidate_markout_loss","mean"),m0_markout_mse=("m0_markout_loss","mean"),markout_mse_delta=("markout_loss_delta","mean"))
    city_date.to_csv(output_dir/"validation_by_city_date.csv",index=False)
    calibration=[]
    bins=np.linspace(0,1,11)
    for (block,horizon),group in scored.groupby(["block","horizon"]):
        dcol=f"h{horizon}_direction"; group=group.copy(); group["bin"]=pd.cut(group.direction_probability,bins,include_lowest=True)
        for bucket,g in group.groupby("bin",observed=True): calibration.append({"head":"direction","block":block,"horizon":horizon,"bin":str(bucket),"rows":len(g),"mean_prediction":g.direction_probability.mean(),"observed_rate":g[dcol].mean()})
        group["settlement_bin"]=pd.cut(group.settlement_probability,bins,include_lowest=True)
        for bucket,g in group.dropna(subset=["settlement_probability","win"]).groupby("settlement_bin",observed=True): calibration.append({"head":"settlement","block":block,"horizon":horizon,"bin":str(bucket),"rows":len(g),"mean_prediction":g.settlement_probability.mean(),"observed_rate":g.win.mean()})
    pd.DataFrame(calibration).to_csv(output_dir/"validation_calibration_bins.csv",index=False)
    oof=pd.read_csv(output_dir/"development_oof.csv.gz")
    primary=oof[oof.horizon.eq(PRIMARY_HORIZON)].copy(); target=f"h{PRIMARY_HORIZON}_relative_markout"
    m0_alpha=float(pd.read_csv(output_dir/"development_tournament.csv").query("block=='M0_market_level'").sort_values("mse").iloc[0].alpha)
    baseline=primary[(primary.block=="M0_market_level")&(primary.alpha==m0_alpha)][["target_date","ladder_snapshot_id","condition_id","prediction"]].rename(columns={"prediction":"m0_prediction"})
    tests=[]
    for block in ("M2_ladder_transition","M3_weather_response_lag"):
        for alpha in RIDGE_ALPHAS:
            arm=primary[(primary.block==block)&(primary.alpha==alpha)].merge(baseline,on=["target_date","ladder_snapshot_id","condition_id"])
            arm["delta"]=(arm.prediction-arm[target])**2-(arm.m0_prediction-arm[target])**2
            daily_delta=arm.groupby("target_date").delta.mean().to_numpy(float); rng=np.random.default_rng(seed+len(tests)); boot=daily_delta[rng.integers(0,len(daily_delta),size=(draws,len(daily_delta)))].mean(axis=1); p=float((np.count_nonzero(boot>=0)+1)/(draws+1)); tests.append({"block":block,"alpha":alpha,"mse_delta_vs_m0":float(daily_delta.mean()),"one_sided_bootstrap_p":p})
    tests=sorted(tests,key=lambda x:x["one_sided_bootstrap_p"]); running=0.0
    for rank,row in enumerate(tests): running=max(running,min(1.0,row["one_sided_bootstrap_p"]*(len(tests)-rank))); row["holm_adjusted_p"]=running
    pd.DataFrame(tests).to_csv(output_dir/"development_multiple_testing.csv",index=False)
    summary["validation_deltas"]=deltas.to_dict("records"); summary["reporting_artifacts"]={"daily":"validation_by_target_date.csv","city_date":"validation_by_city_date.csv","calibration":"validation_calibration_bins.csv","multiple_testing":"development_multiple_testing.csv","liquidity_bins":"development_terciles"}
    summary["strategy_expression"]="NONE_probability_markout_gate_failed"
    summary["execution"]["break_even"]="NOT_APPLICABLE_execution_forbidden_before_probability_gate"
    summary["true_untouched_forward"]={"window":[FORWARD_START,FORWARD_END],"status":"NA_not_started_as_of_2026-08-09","used_for_selection":False}
    if db is not None:
        resolved=db.resolve(strict=True); stat=resolved.stat()
        summary["canonical_identity"]={"db_argument":str(db),"db_realpath":str(resolved),"device":stat.st_dev,"inode":stat.st_ino,"size_bytes":stat.st_size,"mtime_utc":datetime.fromtimestamp(stat.st_mtime,UTC).isoformat(),"observed_at_utc":datetime.now(UTC).isoformat(),"scope":"post_run_route_confirmation; fixed_panel hash freezes queried rows"}
    summary["artifact_hashes"]={name:_artifact_sha256(output_dir/name) for name in ("fixed_panel.csv.gz","development_oof.csv.gz","frozen_validation_scores.csv.gz","candidate_markout_model.joblib")}
    summary["code_hashes"]={"mass_transport_module":_artifact_sha256(Path(__file__)),"unified_runner":_artifact_sha256(Path(__file__).with_name("lmvm_repricing_challenger.py"))}
    (output_dir/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False,default=str)+"\n")
    freeze_path=output_dir/"model_freeze.json"; freeze=json.loads(freeze_path.read_text()); freeze["canonical_identity"]=summary.get("canonical_identity"); freeze["artifact_hashes"]=summary["artifact_hashes"]; freeze["code_hashes"]=summary["code_hashes"]; freeze["true_untouched_forward"]=summary["true_untouched_forward"]; freeze_path.write_text(json.dumps(freeze,indent=2,ensure_ascii=False,default=str)+"\n")
    return summary


def run_recent_forward(
    db:Path,
    parent_artifact_dir:Path,
    output_dir:Path,
    *,
    draws:int=3000,
    seed:int=20260809,
)->dict[str,Any]:
    """Score 2026-07-29..08-08 once with the already-frozen v1 choices."""
    output_dir.mkdir(parents=True,exist_ok=True)
    parent_freeze=json.loads((parent_artifact_dir/"model_freeze.json").read_text())
    parent_summary=json.loads((parent_artifact_dir/"summary.json").read_text())
    choice=FrozenChoice(
        str(parent_freeze["choice"]["block"]),
        float(parent_freeze["choice"]["alpha"]),
        int(parent_freeze["choice"]["horizon"]),
    )
    tournament=pd.read_csv(parent_artifact_dir/"development_tournament.csv")
    fixed_alphas={
        str(block):float(group.sort_values("mse").iloc[0].alpha)
        for block,group in tournament.groupby("block")
    }
    if fixed_alphas.get(choice.block)!=choice.alpha:
        raise ValueError("parent candidate alpha disagrees with frozen development tournament")
    candidate_model_path=parent_artifact_dir/"candidate_markout_model.joblib"
    expected_model_hash=str(parent_freeze.get("model_sha256") or "")
    actual_model_hash=_artifact_sha256(candidate_model_path)
    if expected_model_hash and expected_model_hash!=actual_model_hash:
        raise ValueError("parent frozen candidate model hash mismatch")

    parent_panel=pd.read_csv(parent_artifact_dir/"fixed_panel.csv.gz",low_memory=False)
    recent,coverage=build_recent_forward_panel(db)
    recent.to_csv(output_dir/"fixed_recent_forward_panel.csv.gz",index=False,compression="gzip")
    combined=pd.concat([parent_panel,recent],ignore_index=True,sort=False)
    scored,_=score_frozen_window(
        combined,choice,RECENT_FORWARD_START,RECENT_FORWARD_END,fixed_alphas,
        candidate_primary_model=joblib.load(candidate_model_path),
    )
    scored.to_csv(output_dir/"recent_forward_scores.csv.gz",index=False,compression="gzip")
    metrics=metric_table(scored,draws,seed)
    deltas=delta_table(scored,choice.block,draws,seed+1000)
    metrics.to_csv(output_dir/"recent_forward_metrics.csv",index=False)
    deltas.to_csv(output_dir/"recent_forward_deltas.csv",index=False)
    city,regime=city_and_regime_tables(
        scored,choice.block,parent_panel[parent_panel.target_date.between(DEV_START,DEV_END)]
    )
    city.to_csv(output_dir/"recent_forward_by_city.csv",index=False)
    regime.to_csv(output_dir/"recent_forward_by_liquidity_regime.csv",index=False)
    loco=leave_one_city_out(scored,choice.block)
    loco.to_csv(output_dir/"recent_forward_leave_one_city_out.csv",index=False)

    label=f"h{PRIMARY_HORIZON}_relative_markout"
    direction=f"h{PRIMARY_HORIZON}_direction"
    work=scored[(scored.block==choice.block)&(scored.horizon==PRIMARY_HORIZON)].copy()
    base=scored[(scored.block=="M0_market_level")&(scored.horizon==PRIMARY_HORIZON)][
        ["ladder_snapshot_id","condition_id","markout_prediction","direction_probability"]
    ].rename(columns={"markout_prediction":"m0_prediction","direction_probability":"m0_direction_probability"})
    work=work.merge(base,on=["ladder_snapshot_id","condition_id"],validate="one_to_one")
    work["candidate_markout_loss"]=(work.markout_prediction-work[label])**2
    work["m0_markout_loss"]=(work.m0_prediction-work[label])**2
    work["markout_mse_delta"]=work.candidate_markout_loss-work.m0_markout_loss
    work["candidate_direction_brier"]=(work.direction_probability-work[direction])**2
    work["m0_direction_brier"]=(work.m0_direction_probability-work[direction])**2
    daily=work.groupby("target_date",as_index=False).agg(
        rows=("condition_id","size"),snapshots=("ladder_snapshot_id","nunique"),cities=("city","nunique"),
        candidate_markout_mse=("candidate_markout_loss","mean"),m0_markout_mse=("m0_markout_loss","mean"),
        markout_mse_delta=("markout_mse_delta","mean"),candidate_direction_brier=("candidate_direction_brier","mean"),
        m0_direction_brier=("m0_direction_brier","mean"),
    )
    daily.to_csv(output_dir/"recent_forward_by_target_date.csv",index=False)
    work.groupby(["target_date","city"],as_index=False).agg(
        rows=("condition_id","size"),snapshots=("ladder_snapshot_id","nunique"),
        candidate_markout_mse=("candidate_markout_loss","mean"),m0_markout_mse=("m0_markout_loss","mean"),
        markout_mse_delta=("markout_mse_delta","mean"),
    ).to_csv(output_dir/"recent_forward_by_city_date.csv",index=False)

    selector_diagnostics=selector_direction_diagnostics(scored,choice.block)
    (output_dir/"selector_direction_diagnostics.json").write_text(
        json.dumps(selector_diagnostics,indent=2,ensure_ascii=False,default=str)+"\n"
    )
    execution=evaluate_execution(scored,choice.block,output_dir,draws,seed+5000)
    primary=deltas[deltas.horizon.eq(PRIMARY_HORIZON)]
    markout_pass=bool(len(primary)==3 and primary.markout_mse_delta_ci_high.lt(0).all())
    direction_pass=bool(
        len(primary)==3
        and primary.direction_brier_delta.le(0).all()
        and primary.direction_logloss_delta.le(0).all()
    )
    probability_pass=markout_pass and direction_pass
    status=(execution["status"] if probability_pass else "BRANCH_EXHAUSTED")
    resolved=db.resolve(strict=True)
    stat=resolved.stat()
    summary={
        "schema_version":"ladder_mass_transport_recent_forward_v1",
        "generated_at_utc":datetime.now(UTC).isoformat(),
        "status":status,
        "window":[RECENT_FORWARD_START,RECENT_FORWARD_END],
        "used_for_selection":False,
        "choice":parent_freeze["choice"],
        "fixed_alphas":fixed_alphas,
        "probability_gate":{"passed":probability_pass,"markout_pass":markout_pass,"direction_pass":direction_pass},
        "coverage":coverage,
        "metrics":metrics.to_dict("records"),
        "deltas":deltas.to_dict("records"),
        "selector_direction_diagnostics":selector_diagnostics,
        "execution":execution,
        "leave_one_city_out":{
            "cities":int(len(loco)),
            "cities_candidate_better":int(loco.mse_delta.lt(0).sum()),
            "all_omissions_candidate_better":bool(loco.mse_delta.lt(0).all()),
        },
        "parent_artifact":{
            "path":str(parent_artifact_dir),
            "run_id":parent_freeze.get("run_id"),
            "status":parent_summary.get("status"),
            "model_sha256":actual_model_hash,
            "fixed_panel_sha256":_artifact_sha256(parent_artifact_dir/"fixed_panel.csv.gz"),
        },
        "canonical_identity":{
            "db_argument":str(db),"db_realpath":str(resolved),"device":stat.st_dev,"inode":stat.st_ino,
            "size_bytes":stat.st_size,"mtime_utc":datetime.fromtimestamp(stat.st_mtime,UTC).isoformat(),
        },
        "true_untouched_forward":{"window":[FORWARD_START,FORWARD_END],"status":"not_started","used_for_selection":False},
        "production":{"shadow_started":False,"notional_usd":0.0,"orders":0,"fills":0},
    }
    summary["artifact_hashes"]={
        name:_artifact_sha256(output_dir/name)
        for name in (
            "fixed_recent_forward_panel.csv.gz","recent_forward_scores.csv.gz",
            "recent_forward_metrics.csv","recent_forward_deltas.csv",
            "selector_direction_diagnostics.json","execution_expressions.csv.gz",
        ) if (output_dir/name).exists()
    }
    summary["code_hashes"]={
        "mass_transport_module":_artifact_sha256(Path(__file__)),
        "unified_runner":_artifact_sha256(Path(__file__).with_name("lmvm_repricing_challenger.py")),
    }
    (output_dir/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False,default=str)+"\n")
    return summary


def run(
    db:Path,
    history_snapshot_dir:Path,
    output_dir:Path,
    draws:int=3000,
    seed:int=20260809,
    history_workers:int=8,
    history_cache_path:Path|None=None,
    resume_fixed_panel:bool=False,
)->dict[str,Any]:
    output_dir.mkdir(parents=True,exist_ok=True)
    if history_cache_path is None:
        history_cache_path=output_dir/"historical_snapshot_adapter.sqlite"
    if resume_fixed_panel and (output_dir/"fixed_panel.csv.gz").exists():
        panel=pd.read_csv(output_dir/"fixed_panel.csv.gz",low_memory=False)
        coverage={"m4_status":"blocked_no_historical_ws_materializer_rest_parity_or_trade_prints"}
    else:
        panel,coverage=build_panel(
            db,history_snapshot_dir,history_cache_path,
            history_workers=history_workers,
        ); panel.to_csv(output_dir/"fixed_panel.csv.gz",index=False,compression="gzip")
    oof=development_oof(panel); oof.to_csv(output_dir/"development_oof.csv.gz",index=False,compression="gzip")
    choice,tournament=select_development(oof,draws,seed); tournament.to_csv(output_dir/"development_tournament.csv",index=False)
    scored,metrics,deltas,models=frozen_validation(panel,choice,draws,seed); scored.to_csv(output_dir/"frozen_validation_scores.csv.gz",index=False,compression="gzip"); metrics.to_csv(output_dir/"frozen_validation_metrics.csv",index=False); deltas.to_csv(output_dir/"frozen_validation_deltas.csv",index=False)
    city,regime=city_and_regime_tables(scored,choice.block,panel[panel.target_date.between(DEV_START,DEV_END)]); city.to_csv(output_dir/"validation_by_city.csv",index=False); regime.to_csv(output_dir/"validation_by_liquidity_regime.csv",index=False)
    loco=leave_one_city_out(scored,choice.block); loco.to_csv(output_dir/"leave_one_city_out.csv",index=False)
    primary=deltas[deltas.horizon.eq(PRIMARY_HORIZON)]; markout_pass=bool(len(primary)==3 and primary.markout_mse_delta_ci_high.lt(0).all()); direction_pass=bool(primary.direction_brier_delta.le(0).all() and primary.direction_logloss_delta.le(0).all()); status="probability_pass" if markout_pass and direction_pass else "BRANCH_EXHAUSTED"
    freeze={"schema_version":SCHEMA_VERSION,"run_id":RUN_ID,"status":status,"choice":{"block":choice.block,"alpha":choice.alpha,"horizon":choice.horizon},"historical_training":[TRAIN_START,TRAIN_END],"development_model_selection":[DEV_START,DEV_END],"frozen_historical_validation":[VALIDATION_START,VALIDATION_END],"true_untouched_forward":[FORWARD_START,FORWARD_END],"validation_access":{"already_seen_in_superseded_narrow_run":True,"corrected_time_aligned_replay_count":1,"fully_untouched":False},"feature_blocks":{key:list(value) for key,value in FEATURE_BLOCKS.items()},"categorical_nuisance_effects":["city","lifecycle","snapshot_source"],"city_selector":False,"m4_status":coverage["m4_status"],"execution_evaluation":"required_only_if_probability_pass","notional_usd":0.0,"orders_enabled":False,"live_enabled":False}
    joblib.dump(models[f"{choice.block}:markout:{PRIMARY_HORIZON}"],output_dir/"candidate_markout_model.joblib"); freeze["model_sha256"]=_artifact_sha256(output_dir/"candidate_markout_model.joblib")
    (output_dir/"model_freeze.json").write_text(json.dumps(freeze,indent=2,ensure_ascii=False)+"\n")
    summary={"schema_version":SCHEMA_VERSION,"generated_at_utc":datetime.now(UTC).isoformat(),"status":status,"coverage":coverage,"choice":freeze["choice"],"development_tournament":tournament.to_dict("records"),"validation_metrics":metrics.to_dict("records"),"validation_deltas":deltas.to_dict("records"),"leave_one_city_out":{"cities":len(loco),"cities_candidate_better":int(loco.mse_delta.lt(0).sum()),"median_delta":float(loco.mse_delta.median())},"m4":{"status":coverage["m4_status"],"trade_print_intensity":None,"cancel_replenishment":None,"queue_turnover":None,"participant_concentration":None},"execution":{"status":"NOT_RUN_PROBABILITY_GATE_FAILED" if status=="BRANCH_EXHAUSTED" else "PENDING"},"production":{"shadow_started":False,"notional_usd":0.0,"orders":0,"fills":0}}
    (output_dir/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False,default=str)+"\n")
    return finalize_saved_validation(
        output_dir,db,history_cache_path,draws=draws,seed=seed
    )
