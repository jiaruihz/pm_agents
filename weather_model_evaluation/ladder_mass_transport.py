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


SCHEMA_VERSION = "ladder_mass_transport_v1"
RUN_ID = "ladder_mass_transport_20260809"
DEV_START, DEV_END = "2026-07-11", "2026-07-21"
VALIDATION_START, VALIDATION_END = "2026-07-22", "2026-07-28"
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
CATEGORICAL = ("city", "lifecycle")


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


def _read_sampled_snapshots(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        WITH base AS (
          SELECT s.*,
                 CAST(unixepoch(s.source_snapshot_ts_utc)/:sample_seconds AS INTEGER) AS time_bin,
                 ROW_NUMBER() OVER (
                   PARTITION BY s.city,s.target_date,s.event_identity,
                                CAST(unixepoch(s.source_snapshot_ts_utc)/:sample_seconds AS INTEGER)
                   ORDER BY s.source_snapshot_ts_utc,s.ladder_snapshot_id
                 ) AS sample_rank
          FROM tmax_v2_ladder_snapshots s
          WHERE s.target_date BETWEEN :start AND :end
            AND s.completeness_status='complete'
            AND s.lineage_status='pit_verified_capture'
        )
        SELECT ladder_snapshot_id,city,target_date,event_slug,event_identity,
               source_snapshot_ts_utc,available_at_utc,market_unit,market_timezone,
               market_utc_offset_seconds,rung_count,absolute_ladder_signature
        FROM base WHERE sample_rank=1
        ORDER BY city,target_date,event_identity,source_snapshot_ts_utc
        """,
        conn,
        params={"sample_seconds": SAMPLE_SECONDS, "start": DEV_START, "end": VALIDATION_END},
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


def _read_forecasts(conn: sqlite3.Connection) -> pd.DataFrame:
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
        """, conn, params=(DEV_START, VALIDATION_END))


def _read_observations(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT tmax_v2_observation_id,city,target_date,temp_f,obs_ts_utc,
               available_at_utc,source_kind,lineage_status
        FROM tmax_v2_observation_event_lineage_enriched
        WHERE target_date BETWEEN ? AND ?
          AND lineage_status='pit_verified_first_seen'
        ORDER BY city,target_date,available_at_utc
        """, conn, params=(DEV_START, VALIDATION_END))


def _read_settlements(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT city,target_date,bracket,
               MAX(CASE WHEN final_price>=0.999 THEN 1.0
                        WHEN final_price<=0.001 THEN 0.0 ELSE final_price END) AS win
        FROM settlement_outcomes
        WHERE target_date BETWEEN ? AND ? AND settlement_status='settled'
        GROUP BY city,target_date,bracket
        """, conn, params=(DEV_START, VALIDATION_END))


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
            f["available_ts"] = pd.to_datetime(f["available_at_utc"], utc=True)
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
            o["available_ts"] = pd.to_datetime(o["available_at_utc"], utc=True)
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


def build_panel(db: Path) -> tuple[pd.DataFrame, dict[str,Any]]:
    conn=_connect(db)
    try:
        snapshots=_read_sampled_snapshots(conn)
        snapshots["snapshot_ts"]=pd.to_datetime(snapshots["source_snapshot_ts_utc"],utc=True)
        forecasts=_read_forecasts(conn); observations=_read_observations(conn)
        snapshots=_asof_weather(snapshots,forecasts,observations)
        rungs=_read_rungs(conn,snapshots["ladder_snapshot_id"].tolist())
        settlements=_read_settlements(conn)
    finally: conn.close()
    frame=_prepare_ladder(snapshots,rungs)
    frame=_add_weather_features(frame)
    frame=_add_targets(frame,snapshots)
    frame=frame.merge(settlements,on=["city","target_date","bracket"],how="left")
    fixed=frame[frame["yes_direct"] & frame[f"h{PRIMARY_HORIZON}_relative_markout"].notna()].copy()
    fixed["feature_book_snapshot_id"]=fixed["ladder_snapshot_id"]
    fixed["future_evaluation_snapshot_id"]=fixed[f"h{PRIMARY_HORIZON}_snapshot_id"]
    coverage={
        "raw_sampled_snapshots":int(snapshots.ladder_snapshot_id.nunique()),
        "raw_dates":int(snapshots.target_date.nunique()),"raw_cities":int(snapshots.city.nunique()),
        "raw_rungs":int(len(frame)),"fixed_rows":int(len(fixed)),
        "fixed_snapshots":int(fixed.ladder_snapshot_id.nunique()),"fixed_dates":int(fixed.target_date.nunique()),
        "fixed_cities":int(fixed.city.nunique()),"missing_forecast_rows":int(fixed.forecast_peak_f.isna().sum()),
        "missing_observation_rows":int(fixed.observed_max_f.isna().sum()),
        "missing_settlement_rows":int(fixed.win.isna().sum()),
        "m4_status":"blocked_no_historical_ws_materializer_rest_parity_or_trade_prints",
        "m4_raw_policy_window":"2026-08-08 onward; outside development/validation",
    }
    return fixed.reset_index(drop=True),coverage


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
            for horizon in HORIZONS:
                label=f"h{horizon}_relative_markout"
                for index,date in enumerate(dates):
                    train_dates=dates[:index]
                    if len(train_dates)<MIN_OOF_TRAIN_DATES: continue
                    train=dev[dev.target_date.isin(train_dates)&dev[label].notna()]; test=dev[(dev.target_date==date)&dev[label].notna()]
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
    dev=rows[rows.target_date.between(DEV_START,DEV_END)].copy(); val=rows[rows.target_date.between(VALIDATION_START,VALIDATION_END)].copy(); outputs=[]; models={}
    chosen_alpha={block:(choice.alpha if block==choice.block else float(select_development_alpha(dev,block))) for block in FEATURE_BLOCKS}
    for block,alpha in chosen_alpha.items():
        for horizon in HORIZONS:
            label=f"h{horizon}_relative_markout"; train=dev[dev[label].notna()]; test=val[val[label].notna()].copy(); model,pred=_fit_predict(train,test,block,alpha,label); models[f"{block}:markout:{horizon}"]=model
            part=test.copy(); part["block"]=block; part["alpha"]=alpha; part["horizon"]=horizon; part["markout_prediction"]=pred
            direction=f"h{horizon}_direction"; dmodel,dpred=_fit_predict(train,test,block,alpha,direction,True); part["direction_probability"]=dpred; models[f"{block}:direction:{horizon}"]=dmodel
            settled_train=train[train.win.notna()]; settled_test=test[test.win.notna()].copy(); smodel,spred=_fit_predict(settled_train,settled_test,block,alpha,"win",True); settled_test["settlement_probability"]=_normalized_settlement_probability(settled_test,spred); models[f"{block}:settlement"]=smodel
            part=part.merge(settled_test[["ladder_snapshot_id","condition_id","settlement_probability"]],on=["ladder_snapshot_id","condition_id"],how="left")
            outputs.append(part)
    scored=pd.concat(outputs,ignore_index=True)
    metrics=metric_table(scored,draws,seed)
    deltas=delta_table(scored,choice.block,draws,seed+1000)
    return scored,metrics,deltas,models


def select_development_alpha(dev:pd.DataFrame,block:str)->float:
    # Non-candidate controls use the same fixed alpha grid but are selected on development only.
    dates=sorted(dev.target_date.unique()); scores=[]; label=f"h{PRIMARY_HORIZON}_relative_markout"
    for alpha in RIDGE_ALPHAS:
        losses=[]
        for i,date in enumerate(dates):
            if i<MIN_OOF_TRAIN_DATES:continue
            train=dev[dev.target_date.isin(dates[:i])&dev[label].notna()]; test=dev[(dev.target_date==date)&dev[label].notna()]
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


def leave_one_city_out(rows:pd.DataFrame,choice:FrozenChoice)->pd.DataFrame:
    dev=rows[rows.target_date.between(DEV_START,DEV_END)]; val=rows[rows.target_date.between(VALIDATION_START,VALIDATION_END)]; label=f"h{PRIMARY_HORIZON}_relative_markout"; records=[]
    for city in sorted(val.city.unique()):
        train=dev[(dev.city!=city)&dev[label].notna()]; test=val[(val.city==city)&val[label].notna()]
        if test.empty:continue
        _,cand=_fit_predict(train,test,choice.block,choice.alpha,label); _,base=_fit_predict(train,test,"M0_market_level",select_development_alpha(dev,"M0_market_level"),label)
        records.append({"city":city,"rows":len(test),"dates":test.target_date.nunique(),"candidate_mse":float(np.mean((cand-test[label])**2)),"m0_mse":float(np.mean((base-test[label])**2)),"mse_delta":float(np.mean((cand-test[label])**2-(base-test[label])**2))})
    return pd.DataFrame(records)


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


def run(db:Path,output_dir:Path,draws:int=3000,seed:int=20260809)->dict[str,Any]:
    output_dir.mkdir(parents=True,exist_ok=True)
    panel,coverage=build_panel(db); panel.to_csv(output_dir/"fixed_panel.csv.gz",index=False,compression="gzip")
    oof=development_oof(panel); oof.to_csv(output_dir/"development_oof.csv.gz",index=False,compression="gzip")
    choice,tournament=select_development(oof,draws,seed); tournament.to_csv(output_dir/"development_tournament.csv",index=False)
    scored,metrics,deltas,models=frozen_validation(panel,choice,draws,seed); scored.to_csv(output_dir/"frozen_validation_scores.csv.gz",index=False,compression="gzip"); metrics.to_csv(output_dir/"frozen_validation_metrics.csv",index=False); deltas.to_csv(output_dir/"frozen_validation_deltas.csv",index=False)
    city,regime=city_and_regime_tables(scored,choice.block,panel[panel.target_date.between(DEV_START,DEV_END)]); city.to_csv(output_dir/"validation_by_city.csv",index=False); regime.to_csv(output_dir/"validation_by_liquidity_regime.csv",index=False)
    loco=leave_one_city_out(panel,choice); loco.to_csv(output_dir/"leave_one_city_out.csv",index=False)
    primary=deltas[deltas.horizon.eq(PRIMARY_HORIZON)]; markout_pass=bool(len(primary)==3 and primary.markout_mse_delta_ci_high.lt(0).all()); direction_pass=bool(primary.direction_brier_delta.le(0).all() and primary.direction_logloss_delta.le(0).all()); status="probability_pass" if markout_pass and direction_pass else "BRANCH_EXHAUSTED"
    freeze={"schema_version":SCHEMA_VERSION,"run_id":RUN_ID,"status":status,"choice":{"block":choice.block,"alpha":choice.alpha,"horizon":choice.horizon},"development":[DEV_START,DEV_END],"frozen_historical_validation":[VALIDATION_START,VALIDATION_END],"true_untouched_forward":[FORWARD_START,FORWARD_END],"validation_open_count":1,"feature_blocks":{key:list(value) for key,value in FEATURE_BLOCKS.items()},"categorical_city_effect":"L2-shrunk one-hot nuisance effect; no city selector","m4_status":coverage["m4_status"],"execution_evaluation":"required_only_if_probability_pass","notional_usd":0.0,"orders_enabled":False,"live_enabled":False}
    joblib.dump(models[f"{choice.block}:markout:{PRIMARY_HORIZON}"],output_dir/"candidate_markout_model.joblib"); freeze["model_sha256"]=_artifact_sha256(output_dir/"candidate_markout_model.joblib")
    (output_dir/"model_freeze.json").write_text(json.dumps(freeze,indent=2,ensure_ascii=False)+"\n")
    summary={"schema_version":SCHEMA_VERSION,"generated_at_utc":datetime.now(UTC).isoformat(),"status":status,"coverage":coverage,"choice":freeze["choice"],"development_tournament":tournament.to_dict("records"),"validation_metrics":metrics.to_dict("records"),"validation_deltas":deltas.to_dict("records"),"leave_one_city_out":{"cities":len(loco),"cities_candidate_better":int(loco.mse_delta.lt(0).sum()),"median_delta":float(loco.mse_delta.median())},"m4":{"status":coverage["m4_status"],"trade_print_intensity":None,"cancel_replenishment":None,"queue_turnover":None,"participant_concentration":None},"execution":{"status":"NOT_RUN_PROBABILITY_GATE_FAILED" if status=="BRANCH_EXHAUSTED" else "PENDING"},"production":{"shadow_started":False,"notional_usd":0.0,"orders":0,"fills":0}}
    (output_dir/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False,default=str)+"\n")
    return summary
