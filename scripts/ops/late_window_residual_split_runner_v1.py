#!/usr/bin/env python3
"""Runtime scorer for late-window exact-bracket residual split heads.

Heads:
- residual_high_price_no: zero-notional shadow only. High-price BUY_NO
  residual capture at 95-99c.
- value_d1_no: tiny-live probe candidate. BUY_NO on d1 when physical
  p_leg_win clears executable cost.

The runner also writes a shadow-only exit-recovery overlay for prior
value_d1_no entries. It never sends exit orders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk.research_late_window_residual_calibrated_features_v1 import (  # noqa: E402
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    add_model_features,
    fit_full_model,
)
from scripts.analysis.reheat_risk.research_late_window_residual_heating_done_v1 import (  # noqa: E402
    entry_fields,
    fee_per_share,
    leg_for_record,
    native_from_metar_f,
    native_tick_value,
    residual_done_features,
    safe_float,
)
from src.strategies.weather_edge_v1.runtime import order_runtime  # noqa: E402
from src.strategies.runtime.production import load_production_spec  # noqa: E402
from weather_data_feed import (  # noqa: E402
    index_forecast_enrichment,
    index_observation_cache,
    load_forecast_enrichment,
    load_observation_cache,
)
from weather_feature_layer.market import parse_bracket  # noqa: E402
from weather_feature_layer.runtime_refs import attach_runtime_feature_frame_ref  # noqa: E402
from weather_feature_layer.state import heating_done_features  # noqa: E402


STRATEGY_ID = "late_window_residual_split_v1"
RESIDUAL_INSTANCE = "residual_high_price_no_shadow_v1"
VALUE_INSTANCE = "value_d1_no_tiny_probe_v1"
EXIT_INSTANCE = "value_d1_no_residual_exit_recovery_shadow_v1"
RUNTIME_DIR = Path(
    os.environ.get(
        "LATE_WINDOW_RESIDUAL_SPLIT_RUNTIME_DIR",
        str(ROOT / "runtime/weather_edge_v1/late_window_residual_split_v1"),
    )
)
PRODUCTION_SPEC = load_production_spec()
DEFAULT_SNAPSHOT_DIRS = [PRODUCTION_SPEC.strategy_paper_snapshot_dir()]
DEFAULT_OBSERVATION_CACHE_PATHS = [PRODUCTION_SPEC.observation_cache_path()]
DEFAULT_FORECAST_ENRICHMENT_PATHS = [PRODUCTION_SPEC.forecast_enrichment_root() / "latest.json"]
FEATURE_STORE_DEFAULT = ROOT / os.environ.get("WEATHER_FEATURE_STORE_DIR", "runtime/weather_feature_store")
TRAIN_ROWS = ROOT / "docs/analysis/2026-07/generated/late_window_residual_heating_done_v1/first_cross_rows.csv"

LATEST_OUT = RUNTIME_DIR / "latest_candidates.json"
SUMMARY_OUT = RUNTIME_DIR / "latest_summary.json"
HISTORY_OUT = RUNTIME_DIR / "summary_history.jsonl"
RESIDUAL_SHADOW_OUT = RUNTIME_DIR / "residual_high_price_no_shadow.jsonl"
VALUE_ACCEPTED_OUT = RUNTIME_DIR / "value_d1_no_accepted_candidates.jsonl"
VALUE_BLOCKED_OUT = RUNTIME_DIR / "value_d1_no_blocked_candidates.jsonl"
EXIT_SHADOW_OUT = RUNTIME_DIR / "value_d1_no_exit_recovery_shadow.jsonl"
PLAN_OUT = RUNTIME_DIR / "trade_plans.jsonl"
PAPER_OUT = RUNTIME_DIR / "paper_orders.jsonl"
LIVE_OUT = RUNTIME_DIR / "live_orders.jsonl"

LOCAL_START_HOUR = 15
LOCAL_END_HOUR = 18
WEATHER_FEE_RATE = 0.05
ACTIVE_ORDER_STATUSES = {"submitted", "simulated_open"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stable_hash(payload: Any, *, length: int = 24) -> str:
    raw = json.dumps(order_runtime.json_ready(payload), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def to_float(value: Any, default: float = math.nan) -> float:
    out = safe_float(value)
    return out if math.isfinite(out) else default


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def latest_snapshot_in_dir(snapshot_dir: Path) -> Path | None:
    paths = sorted(snapshot_dir.glob("snapshot_*.json")) if snapshot_dir.exists() else []
    return paths[-1] if paths else None


def default_snapshot_dir() -> Path:
    candidates: list[tuple[float, Path]] = []
    for snapshot_dir in DEFAULT_SNAPSHOT_DIRS:
        latest = latest_snapshot_in_dir(snapshot_dir)
        if latest:
            candidates.append((latest.stat().st_mtime, snapshot_dir))
    return max(candidates, key=lambda x: x[0])[1] if candidates else DEFAULT_SNAPSHOT_DIRS[-1]


def latest_snapshot_path(snapshot_dir: Path, explicit: str = "") -> Path:
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else ROOT / path
    latest = latest_snapshot_in_dir(snapshot_dir)
    if latest is None:
        raise FileNotFoundError(f"no paper snapshots under {snapshot_dir}")
    return latest


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_utc_dt(value: Any) -> datetime | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def first_existing_path(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def default_observation_cache_path() -> str:
    path = first_existing_path(DEFAULT_OBSERVATION_CACHE_PATHS)
    return str(path) if path else str(DEFAULT_OBSERVATION_CACHE_PATHS[0])


def default_forecast_enrichment_path() -> str:
    path = first_existing_path(DEFAULT_FORECAST_ENRICHMENT_PATHS)
    return str(path) if path else str(DEFAULT_FORECAST_ENRICHMENT_PATHS[0])


def load_observation_index(path_text: str) -> dict[tuple[str, str], dict[str, Any]]:
    if not path_text:
        return {}
    path = Path(path_text).expanduser()
    if not path.exists():
        return {}
    return index_observation_cache(load_observation_cache(path))


def load_forecast_enrichment_index(path_text: str) -> dict[tuple[str, str], dict[str, Any]]:
    if not path_text:
        return {}
    path = Path(path_text).expanduser()
    if not path.exists():
        return {}
    return index_forecast_enrichment(load_forecast_enrichment(path))


def local_clock(rec: dict[str, Any], payload: dict[str, Any]) -> tuple[int | None, int | None, str]:
    ts_local = str(rec.get("ts_local") or payload.get("ts_beijing") or "")
    try:
        hour = int(ts_local[11:13] if "T" in ts_local else ts_local[11:13])
        minute = int(ts_local[14:16])
        return hour, minute, ts_local
    except (TypeError, ValueError, IndexError):
        return None, None, ts_local


def snapshot_candidate_rows(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    payload = load_snapshot(path)
    rows: list[dict[str, Any]] = []
    no_books: list[dict[str, Any]] = []
    ts_utc = str(payload.get("ts_utc") or "")
    ts_bj = str(payload.get("ts_beijing") or "")
    for rec in payload.get("records") or []:
        if not isinstance(rec, dict):
            continue
        target_date = str(rec.get("target_date") or rec.get("event_date") or "")
        city_date = str(rec.get("city_local_date_at_snapshot") or "")
        city = str(rec.get("city") or "")
        bracket_text = str(rec.get("bracket") or "")
        unit = str(rec.get("unit") or "")
        hour, minute, ts_local = local_clock(rec, payload)
        if not city or not target_date or city_date != target_date:
            continue
        no_books.append(
            {
                "city": city,
                "target_date": target_date,
                "bracket": bracket_text,
                "no_token_id": str(rec.get("no_token_id") or ""),
                "no_best_bid": safe_float(rec.get("no_best_bid")),
                "no_bid_size": safe_float(rec.get("no_bid_size")),
                "no_depth_bid_5c": safe_float(rec.get("no_depth_bid_5c")),
                "no_best_ask": safe_float(rec.get("no_best_ask")),
                "no_ask_size": safe_float(rec.get("no_ask_size")),
                "market_id": str(rec.get("market_id") or ""),
                "condition_id": str(rec.get("condition_id") or ""),
                "question": str(rec.get("question") or ""),
                "snapshot_ts_utc": ts_utc,
                "snapshot_file": rel(path),
            }
        )
        if hour is None or minute is None or hour < LOCAL_START_HOUR or hour > LOCAL_END_HOUR:
            continue
        if str(rec.get("probability_status")) != "ok" or rec.get("metar_source") in (None, "none"):
            continue
        metar_max_f = safe_float(rec.get("metar_current_max_f"))
        metar_latest_f = safe_float(rec.get("metar_latest_temp_f"))
        if not math.isfinite(metar_max_f):
            continue
        running_value = native_tick_value(metar_max_f, unit)
        if running_value is None:
            continue
        leg = leg_for_record(rec, running_value)
        bracket = parse_bracket(bracket_text)
        if leg is None or bracket is None:
            continue
        fields = entry_fields(rec, leg)
        entry_price = safe_float(fields["entry_price"])
        if not math.isfinite(entry_price):
            continue
        running_native = native_from_metar_f(metar_max_f, unit)
        latest_native = native_from_metar_f(metar_latest_f, unit)
        forecast_max_native = safe_float(rec.get("forecast_max_native"))
        outcome = str(fields["outcome"])
        token_id = str(rec.get("yes_token_id") if outcome == "yes" else rec.get("no_token_id") or "")
        row: dict[str, Any] = {
            "snapshot_file": rel(path),
            "snapshot_ts_utc": ts_utc,
            "ts_beijing": ts_bj,
            "ts_local": ts_local,
            "local_hour": hour,
            "local_minute": minute,
            "city": city,
            "city_pool": rec.get("city_pool"),
            "target_date": target_date,
            "unit": unit,
            "bracket": bracket_text,
            "leg": leg,
            "outcome": outcome,
            "target_bracket_low_native": bracket.low,
            "target_bracket_high_native": bracket.high,
            "running_value": running_value,
            "running_native": running_native,
            "latest_native": latest_native,
            "decline_native": running_native - latest_native if math.isfinite(latest_native) else math.nan,
            "metar_current_max_f": metar_max_f,
            "metar_latest_temp_f": metar_latest_f,
            "metar_latest_ts_utc": rec.get("metar_latest_ts_utc"),
            "metar_obs_count_today": rec.get("metar_obs_count_today"),
            "forecast_max_native": forecast_max_native,
            "forecast_gap_to_running_native": forecast_max_native - running_value
            if math.isfinite(forecast_max_native)
            else math.nan,
            "forecast_peak_time_local": rec.get("forecast_peak_time_local"),
            "forecast_peak_delta_hours_local": safe_float(rec.get("forecast_peak_delta_hours_local")),
            "forecast_source": rec.get("forecast_source"),
            "entry_price": entry_price,
            "best_bid": safe_float(fields["best_bid"]),
            "spread": safe_float(fields["spread"]),
            "top_size": safe_float(fields["top_size"]),
            "depth_5c": safe_float(fields["depth_5c"]),
            "bid_depth_5c": safe_float(fields["bid_depth_5c"]),
            "residual_points": (1.0 - entry_price) * 100.0,
            "condition_id": str(rec.get("condition_id") or ""),
            "market_id": str(rec.get("market_id") or ""),
            "event_slug": str(rec.get("event_slug") or ""),
            "market_event_date": str(rec.get("event_date") or rec.get("market_local_date") or ""),
            "token_id": token_id,
            "yes_token_id": str(rec.get("yes_token_id") or ""),
            "no_token_id": str(rec.get("no_token_id") or ""),
            "yes_best_ask": safe_float(rec.get("yes_best_ask")),
            "yes_best_bid": safe_float(rec.get("yes_best_bid")),
            "no_best_ask": safe_float(rec.get("no_best_ask")),
            "no_best_bid": safe_float(rec.get("no_best_bid")),
            "question": str(rec.get("question") or ""),
        }
        row.update(heating_done_features(row))
        row.update(residual_done_features(row))
        rows.append(row)
    df = pd.DataFrame(rows)
    books = pd.DataFrame(no_books).drop_duplicates(["city", "target_date", "bracket", "no_token_id"], keep="last")
    return df, books, payload


def _forecast_enrichment_fields(row: dict[str, Any]) -> dict[str, Any]:
    multi = row.get("open_meteo_multi_model") if isinstance(row.get("open_meteo_multi_model"), dict) else {}
    target = multi.get("target_date") if isinstance(multi.get("target_date"), dict) else {}
    context = row.get("open_meteo_weather_context") if isinstance(row.get("open_meteo_weather_context"), dict) else {}
    hourly = context.get("target_day_hourly") if isinstance(context.get("target_day_hourly"), dict) else {}
    vertical = row.get("vertical_profile_signal") if isinstance(row.get("vertical_profile_signal"), dict) else {}
    taf = row.get("taf") if isinstance(row.get("taf"), dict) else {}
    taf_signal = taf.get("signal") if isinstance(taf.get("signal"), dict) else {}
    statuses = row.get("source_statuses") if isinstance(row.get("source_statuses"), dict) else {}
    return {
        "forecast_enrichment_status": row.get("status"),
        "forecast_enrichment_snapshot_ts_utc": row.get("snapshot_ts_utc"),
        "forecast_enrichment_source_statuses": json.dumps(statuses, sort_keys=True),
        "multi_model_count": safe_float(target.get("model_count")),
        "multi_model_max_f": safe_float(target.get("model_max")),
        "multi_model_min_f": safe_float(target.get("model_min")),
        "multi_model_mean_f": safe_float(target.get("model_mean")),
        "multi_model_spread_f": safe_float(target.get("model_spread")),
        "context_forecast_max_f": safe_float(hourly.get("forecast_max")),
        "context_first_peak_hour_local": safe_float(hourly.get("first_peak_hour_local")),
        "context_last_peak_hour_local": safe_float(hourly.get("last_peak_hour_local")),
        "vertical_available": bool(vertical.get("available")),
        "vertical_heating_setup": str(vertical.get("heating_setup") or ""),
        "vertical_heating_score": safe_float(vertical.get("heating_score")),
        "vertical_suppression_risk": str(vertical.get("suppression_risk") or ""),
        "vertical_trigger_risk": str(vertical.get("trigger_risk") or ""),
        "vertical_mixing_strength": str(vertical.get("mixing_strength") or ""),
        "taf_available": bool(taf_signal.get("available")),
        "taf_peak_window": str(taf_signal.get("peak_window") or ""),
        "taf_suppression_level": str(taf_signal.get("suppression_level") or ""),
        "taf_disruption_level": str(taf_signal.get("disruption_level") or ""),
        "taf_issue_time": str(taf_signal.get("issue_time") or ""),
        "taf_raw_hash": stable_hash(str(taf_signal.get("raw_taf") or ""), length=16) if taf_signal.get("raw_taf") else "",
    }


def attach_live_context(
    df: pd.DataFrame,
    *,
    obs_index: dict[tuple[str, str], dict[str, Any]],
    forecast_index: dict[tuple[str, str], dict[str, Any]],
) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    rows: list[dict[str, Any]] = []
    for record in df.to_dict(orient="records"):
        row = dict(record)
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        snapshot_dt = parse_utc_dt(row.get("snapshot_ts_utc"))
        obs = obs_index.get(key) or {}
        obs_dt = parse_utc_dt(obs.get("last_obs_utc") or row.get("metar_latest_ts_utc"))
        snapshot_metar_dt = parse_utc_dt(row.get("metar_latest_ts_utc"))
        fetched_dt = parse_utc_dt(obs.get("fetched_at_utc"))
        cadence = to_float(obs.get("cadence_min") or obs.get("estimated_cadence_min"))
        report_after_snapshot = bool(snapshot_dt is not None and obs_dt is not None and obs_dt > snapshot_dt + timedelta(minutes=1))
        if report_after_snapshot and snapshot_metar_dt is not None:
            obs_dt = snapshot_metar_dt
        obs_age = (
            (snapshot_dt - obs_dt).total_seconds() / 60.0
            if snapshot_dt is not None and obs_dt is not None
            else to_float(obs.get("age_min"))
        )
        if not math.isfinite(obs_age):
            obs_age = math.nan
        minutes_to_next = cadence - obs_age if math.isfinite(cadence) and math.isfinite(obs_age) else math.nan
        cache_after_snapshot = bool(fetched_dt is not None and snapshot_dt is not None and fetched_dt > snapshot_dt + timedelta(minutes=10))
        row.update(
            {
                "obs_status": obs.get("status"),
                "obs_source": obs.get("source"),
                "obs_station": obs.get("station"),
                "obs_last_obs_utc": obs.get("last_obs_utc") or row.get("metar_latest_ts_utc"),
                "obs_fetched_at_utc": obs.get("fetched_at_utc"),
                "obs_age_min": round(obs_age, 3) if math.isfinite(obs_age) else math.nan,
                "obs_cadence_min": round(cadence, 3) if math.isfinite(cadence) else math.nan,
                "minutes_to_next_obs": round(minutes_to_next, 3) if math.isfinite(minutes_to_next) else math.nan,
                "obs_current_temp_c": safe_float(obs.get("current_temp_c")),
                "obs_running_max_c": safe_float(obs.get("running_max_c")),
                "obs_decline_c": safe_float(obs.get("decline_c")),
                "obs_minutes_since_running_max": safe_float(obs.get("minutes_since_running_max")),
                "obs_n_obs": safe_float(obs.get("n_obs") or obs.get("record_count")),
                "obs_report_after_snapshot": report_after_snapshot,
                "obs_cache_after_snapshot": cache_after_snapshot,
                "obs_clock_source": "observation_cache" if obs else ("snapshot_metar_latest_ts" if row.get("metar_latest_ts_utc") else "missing"),
            }
        )
        forecast = forecast_index.get(key) or {}
        forecast_dt = parse_utc_dt(forecast.get("snapshot_ts_utc")) if forecast else None
        forecast_after_snapshot = bool(
            forecast_dt is not None and snapshot_dt is not None and forecast_dt > snapshot_dt + timedelta(minutes=10)
        )
        row["forecast_enrichment_after_snapshot"] = forecast_after_snapshot
        if forecast and not forecast_after_snapshot:
            row.update(_forecast_enrichment_fields(forecast))
        rows.append(row)
    return pd.DataFrame(rows)


def add_runtime_buckets(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    out["local_time_float"] = pd.to_numeric(out["local_hour"], errors="coerce") + (
        pd.to_numeric(out["local_minute"], errors="coerce").fillna(0.0) / 60.0
    )
    out["peak_delta_bucket"] = pd.cut(
        pd.to_numeric(out["forecast_peak_delta_hours_local"], errors="coerce"),
        bins=[-100, -1.0, -0.25, 0.5, 1.5, 100],
        labels=["peak_gt1h_ahead", "peak_1h_to_15m_ahead", "near_peak", "post_peak_0_5_1_5h", "post_peak_gt1_5h"],
    )
    out["forecast_gap_bucket"] = pd.cut(
        pd.to_numeric(out["forecast_gap_to_running_native"], errors="coerce"),
        bins=[-100, -1, 0, 1, 2, 100],
        labels=["busted_lt_minus1", "capped_minus1_0", "room_0_1", "room_1_2", "room_gt2"],
    )
    decline = pd.to_numeric(out["decline_native"], errors="coerce")
    out["path_state"] = "missing"
    out.loc[decline.abs().le(0.25), "path_state"] = "at_high"
    out.loc[decline.gt(0.25), "path_state"] = "decline"
    out.loc[decline.lt(-0.25), "path_state"] = "warming_above_running"
    return out


def load_probability_model(train_rows: Path) -> Any:
    if not train_rows.exists():
        raise FileNotFoundError(f"training rows missing: {train_rows}")
    train = pd.read_csv(train_rows)
    train = train[train["win"].notna()].copy()
    train["win"] = train["win"].astype(str).str.lower().isin({"1", "1.0", "true"})
    train = add_model_features(train)
    train = train.dropna(subset=["win"])
    if len(train) < 80 or train["win"].nunique() < 2:
        raise RuntimeError(f"not enough training rows for p_leg_win model: rows={len(train)}")
    return fit_full_model(train), len(train), sorted(train["target_date"].astype(str).unique())


def score_candidates(candidates: pd.DataFrame, model: Any) -> pd.DataFrame:
    if candidates.empty:
        return candidates.copy()
    out = add_runtime_buckets(candidates)
    out = add_model_features(out)
    out["p_leg_win_physical_v1"] = model.predict_proba(out[NUMERIC_FEATURES + CATEGORICAL_FEATURES])[:, 1]
    out["fee_per_share"] = out["entry_price"].apply(fee_per_share)
    out["cost_per_share"] = out["entry_price"] + out["fee_per_share"]
    out["model_edge_per_share"] = out["p_leg_win_physical_v1"] - out["cost_per_share"]
    out["available_depth_shares"] = out[["top_size", "depth_5c"]].max(axis=1, skipna=True)
    return out


def row_id(prefix: str, row: pd.Series | dict[str, Any]) -> str:
    payload = {
        "city": row.get("city"),
        "target_date": row.get("target_date"),
        "snapshot_ts_utc": row.get("snapshot_ts_utc"),
        "leg": row.get("leg"),
        "bracket": row.get("bracket"),
        "token_id": row.get("token_id"),
        "strategy_head": row.get("strategy_head"),
    }
    return f"{prefix}-{stable_hash(payload)}"


def event_payload(row: pd.Series, *, strategy_instance: str, strategy_head: str, status: str, reason: str) -> dict[str, Any]:
    payload = {k: order_runtime.json_ready(v) for k, v in row.to_dict().items()}
    payload.update(
        {
            "record_type": "late_window_residual_candidate",
            "strategy_id": STRATEGY_ID,
            "strategy_instance": strategy_instance,
            "strategy_head": strategy_head,
            "candidate_status": status,
            "candidate_reason": reason,
            "created_at_utc": utc_now(),
            "no_order_placed": strategy_head == "residual_high_price_no",
            "zero_notional": strategy_head == "residual_high_price_no",
        }
    )
    payload["candidate_id"] = row_id("lwres", payload)
    return attach_runtime_feature_frame_ref(
        payload,
        store_root=FEATURE_STORE_DEFAULT,
        feature_grain="late_window_residual_split_candidate",
        source_profile_id=strategy_instance,
        builder_version="late_window_residual_split_runner_v1",
        key_columns=("strategy_instance", "city", "target_date", "snapshot_ts_utc", "leg", "bracket", "token_id"),
    )


def select_residual_shadow(df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    return df[
        df["leg"].astype(str).isin(["d1_no", "d2_no", "d3_no"])
        & df["entry_price"].between(float(args.residual_min_price), float(args.residual_max_price), inclusive="both")
        & df["available_depth_shares"].fillna(0).ge(float(args.min_depth_shares))
    ].copy()


def select_value_d1(df: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame]:
    if df.empty:
        return df.copy(), df.copy()
    base = df[
        df["leg"].astype(str).eq("d1_no")
        & df["entry_price"].lt(float(args.value_max_entry_price))
        & df["available_depth_shares"].fillna(0).ge(float(args.min_depth_shares))
    ].copy()
    if base.empty:
        return base, base
    reasons: list[str] = []
    for _, row in base.iterrows():
        reason = ""
        if bool(args.require_obs_clock):
            obs_age = to_float(row.get("obs_age_min"))
            cadence = to_float(row.get("obs_cadence_min"))
            minutes_to_next = to_float(row.get("minutes_to_next_obs"))
            if row.get("obs_report_after_snapshot"):
                reason = "obs_report_after_snapshot_recompute_required"
            elif not math.isfinite(obs_age) or not math.isfinite(cadence):
                reason = "missing_obs_clock"
            elif obs_age > float(args.max_obs_age_min):
                reason = f"obs_age_gt_{float(args.max_obs_age_min):g}m"
            elif math.isfinite(minutes_to_next) and 0 <= minutes_to_next <= float(args.pre_update_blackout_min):
                reason = f"pre_update_blackout_next_obs_le_{float(args.pre_update_blackout_min):g}m"
        if not reason and to_float(row.get("model_edge_per_share")) < float(args.value_min_edge):
            reason = "p_leg_win_cost_edge_negative"
        reasons.append(reason)
    base["value_block_reason"] = reasons
    selected = base[base["value_block_reason"].astype(str).eq("")].copy()
    blocked = base[base["value_block_reason"].astype(str).ne("")].copy()
    return selected, blocked


def live_order_files() -> list[Path]:
    out: list[Path] = []
    root = ROOT / "runtime/weather_edge_v1"
    for path in sorted(root.glob("*/live_orders.jsonl")):
        if path not in out:
            out.append(path)
    if LIVE_OUT not in out:
        out.append(LIVE_OUT)
    return out


def live_exposure_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in live_order_files():
        for row in order_runtime.read_jsonl(path):
            if str(row.get("status") or "") not in ACTIVE_ORDER_STATUSES:
                continue
            item = dict(row)
            item["_live_order_file"] = rel(path)
            rows.append(item)
    return rows


def live_guard_reason(row: pd.Series, exposures: list[dict[str, Any]]) -> str:
    city = str(row.get("city") or "")
    target_date = str(row.get("target_date") or "")
    bracket = str(row.get("bracket") or "")
    token_id = str(row.get("token_id") or "")
    market_id = str(row.get("market_id") or "")
    condition_id = str(row.get("condition_id") or "")
    signal_side = "BUY_NO"
    for exposure in exposures:
        exp_city = str(exposure.get("city") or "")
        exp_target_date = str(exposure.get("target_date") or "")
        exp_token = str(exposure.get("token_id") or "")
        exp_side = str(exposure.get("signal_side") or "").upper()
        exp_bracket = str(exposure.get("bracket") or "")
        exp_market_id = str(exposure.get("market_id") or "")
        exp_condition_id = str(exposure.get("condition_id") or "")
        exp_strategy = str(exposure.get("strategy_instance") or exposure.get("source_strategy_instance") or "")
        if token_id and exp_token == token_id:
            return f"live_guard_same_token:{exp_strategy}:{exposure.get('_live_order_file')}"
        same_market = (
            (condition_id and exp_condition_id and condition_id == exp_condition_id)
            or (market_id and exp_market_id and market_id == exp_market_id)
            or (city and target_date and bracket and exp_city == city and exp_target_date == target_date and exp_bracket == bracket)
        )
        if same_market and exp_side and exp_side != signal_side:
            return f"live_guard_same_market_opposite_side:{exp_strategy}:{exposure.get('_live_order_file')}"
        if exp_city == city and exp_target_date == target_date and exp_side == "BUY_NO":
            return f"live_guard_city_day_existing_buy_no:{exp_strategy}:{exposure.get('_live_order_file')}"
    return ""


def apply_live_guards(selected: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if selected.empty:
        return selected.copy(), selected.copy()
    exposures = live_exposure_rows()
    guarded = selected.copy()
    guarded["live_guard_reason"] = [live_guard_reason(row, exposures) for _, row in guarded.iterrows()]
    blocked = guarded[guarded["live_guard_reason"].astype(str).ne("")].copy()
    passed = guarded[guarded["live_guard_reason"].astype(str).eq("")].copy()
    return passed, blocked


def existing_plan_keys() -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for path in (LIVE_OUT, PAPER_OUT):
        for row in order_runtime.read_jsonl(path):
            if row.get("strategy_head") not in (None, "value_d1_no") and row.get("strategy_instance") != VALUE_INSTANCE:
                continue
            token = str(row.get("token_id") or "")
            if token:
                keys.add((str(row.get("city") or ""), str(row.get("target_date") or ""), token))
    return keys


def build_plan(row: pd.Series, *, live_enabled: bool, shares: float, ttl_min: float) -> dict[str, Any]:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=float(ttl_min))
    signal_base = {
        "strategy_id": STRATEGY_ID,
        "strategy_instance": VALUE_INSTANCE,
        "strategy_head": "value_d1_no",
        "city": row.get("city"),
        "target_date": row.get("target_date"),
        "snapshot_ts_utc": row.get("snapshot_ts_utc"),
        "bracket": row.get("bracket"),
        "token_id": row.get("token_id"),
    }
    signal_id = "value-d1-no-" + stable_hash(signal_base)
    plan_id = "plan-" + stable_hash({**signal_base, "live_enabled": bool(live_enabled)})
    ask = safe_float(row.get("entry_price"))
    bid = safe_float(row.get("best_bid"))
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": plan_id,
        "signal_id": signal_id,
        "created_at_utc": utc_now(),
        "status": "accepted",
        "risk_status": "passed",
        "strategy": "weather_edge_v1",
        "strategy_id": STRATEGY_ID,
        "strategy_instance": VALUE_INSTANCE,
        "source_strategy_instance": VALUE_INSTANCE,
        "strategy_family": "late_window_residual_exact_bracket",
        "strategy_head": "value_d1_no",
        "probability_source": "p_leg_win_physical_v1_full_fit_shadow_feature",
        "decision_mode": "value_d1_no_p_leg_win_cost_edge",
        "execution_mode": "tiny_live_taker_probe_5shares",
        "profile": "value_d1_no",
        "combo": "value_d1_no_edge0_v1",
        "city": str(row.get("city") or ""),
        "city_pool": str(row.get("city_pool") or ""),
        "target_date": str(row.get("target_date") or ""),
        "market_id": str(row.get("market_id") or ""),
        "event_slug": str(row.get("event_slug") or ""),
        "market_slug": str(row.get("event_slug") or ""),
        "question": str(row.get("question") or ""),
        "market_event_date": str(row.get("market_event_date") or ""),
        "bracket": str(row.get("bracket") or ""),
        "token_id": str(row.get("token_id") or ""),
        "signal_side": "BUY_NO",
        "order_side": "BUY",
        "market_price": round(ask, 6),
        "best_bid": round(bid, 6) if math.isfinite(bid) else 0.0,
        "best_ask": round(ask, 6),
        "spread": round(max(0.0, ask - bid), 6) if math.isfinite(bid) else None,
        "limit_price": round(ask, 6),
        "quote_status": "accepted",
        "quote_reason": "value_d1_no_top_ask_taker_probe",
        "quote_edge": round(to_float(row.get("model_edge_per_share"), 0.0), 6),
        "required_quote_edge": 0.0,
        "model_token_probability": round(to_float(row.get("p_leg_win_physical_v1"), 0.0), 6),
        "quote_best_bid": round(bid, 6) if math.isfinite(bid) else 0.0,
        "quote_best_ask": round(ask, 6),
        "quote_spread": round(max(0.0, ask - bid), 6) if math.isfinite(bid) else None,
        "quote_tick_size": 0.001,
        "quote_mode": "top_ask_taker_live_probe",
        "child_order_role": "single",
        "maker_only": False,
        "execution_policy": "value_d1_no_taker_probe_v1",
        "tick_size": 0.001,
        "sizing_mode": "fixed_shares",
        "fixed_order_shares": round(float(shares), 6),
        "max_order_shares": round(float(shares), 6),
        "size": round(float(shares), 6),
        "notional": round(float(shares) * ask, 6),
        "order_notional_cap": round(float(shares) * ask, 6),
        "edge": round(to_float(row.get("model_edge_per_share"), 0.0), 6),
        "min_edge": 0.0,
        "paper_enabled": True,
        "live_enabled": bool(live_enabled),
        "shadow_decision": "value_d1_no_tiny_probe",
        "shadow_reason": "tiny_probe_candidate_live_requires_explicit_confirm_live",
        "model_version": "late_window_residual_physical_logit_v1",
        "expires_at_utc": expires_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "order_ttl_min": round(float(ttl_min), 6),
        "expiry_policy": "fixed_tiny_probe_ttl",
        "decision_snapshot_ts_utc": str(row.get("snapshot_ts_utc") or ""),
        "decision_hour_local": to_float(row.get("local_hour"), None),
        "decision_minute_local": to_float(row.get("local_minute"), None),
        "leg": str(row.get("leg") or ""),
        "entry_price": round(ask, 6),
        "fee_per_share": round(to_float(row.get("fee_per_share"), 0.0), 6),
        "cost_per_share": round(to_float(row.get("cost_per_share"), 0.0), 6),
        "p_leg_win_physical_v1": round(to_float(row.get("p_leg_win_physical_v1"), 0.0), 6),
        "model_edge_per_share": round(to_float(row.get("model_edge_per_share"), 0.0), 6),
        "forecast_peak_delta_hours_local": to_float(row.get("forecast_peak_delta_hours_local"), None),
        "forecast_gap_to_running_native": to_float(row.get("forecast_gap_to_running_native"), None),
        "obs_age_min": to_float(row.get("obs_age_min"), None),
        "obs_cadence_min": to_float(row.get("obs_cadence_min"), None),
        "minutes_to_next_obs": to_float(row.get("minutes_to_next_obs"), None),
        "obs_clock_source": str(row.get("obs_clock_source") or ""),
        "forecast_enrichment_status": str(row.get("forecast_enrichment_status") or ""),
        "multi_model_spread_f": to_float(row.get("multi_model_spread_f"), None),
        "vertical_heating_setup": str(row.get("vertical_heating_setup") or ""),
        "taf_peak_window": str(row.get("taf_peak_window") or ""),
        "path_state": str(row.get("path_state") or ""),
        "heating_done_score_v1": to_float(row.get("heating_done_score_v1"), None),
        "leg_residual_done_score_v1": to_float(row.get("leg_residual_done_score_v1"), None),
        "snapshot_file": str(row.get("snapshot_file") or ""),
    }


def write_plans(selected: pd.DataFrame, args: argparse.Namespace) -> list[dict[str, Any]]:
    keys = existing_plan_keys()
    plans: list[dict[str, Any]] = []
    if selected.empty:
        order_runtime.write_jsonl(PLAN_OUT, [])
        return plans
    live_enabled = bool(args.live and args.confirm_live)
    gross = 0.0
    for _, row in selected.sort_values(["target_date", "snapshot_ts_utc", "city", "model_edge_per_share"], ascending=[True, True, True, False]).iterrows():
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""), str(row.get("token_id") or ""))
        if key in keys:
            continue
        cost = float(args.shares) * to_float(row.get("entry_price"), 0.0)
        if gross + cost > float(args.max_daily_cost_usd) + 1e-9:
            continue
        plans.append(build_plan(row, live_enabled=live_enabled, shares=float(args.shares), ttl_min=float(args.order_ttl_min)))
        gross += cost
        keys.add(key)
        if len(plans) >= int(args.max_orders):
            break
    order_runtime.write_jsonl(PLAN_OUT, plans)
    return plans


def append_unique(path: Path, rows: list[dict[str, Any]], key: str) -> int:
    seen = {str(r.get(key) or "") for r in order_runtime.read_jsonl(path)}
    appended = 0
    for row in rows:
        value = str(row.get(key) or "")
        if value and value in seen:
            continue
        order_runtime.append_jsonl(path, row)
        if value:
            seen.add(value)
        appended += 1
    return appended


def exit_recovery_events(books: pd.DataFrame, scored: pd.DataFrame, args: argparse.Namespace) -> list[dict[str, Any]]:
    if books.empty:
        return []
    scored_by_token = {
        (str(r.city), str(r.target_date), str(r.bracket), str(r.no_token_id)): r
        for r in scored.itertuples()
        if str(getattr(r, "no_token_id", "") or "")
    }
    book_by_token = {
        (str(r.city), str(r.target_date), str(r.bracket), str(r.no_token_id)): r
        for r in books.itertuples()
        if str(getattr(r, "no_token_id", "") or "")
    }
    events: list[dict[str, Any]] = []
    for entry in order_runtime.read_jsonl(VALUE_ACCEPTED_OUT):
        if str(entry.get("strategy_head") or "") != "value_d1_no":
            continue
        key = (str(entry.get("city") or ""), str(entry.get("target_date") or ""), str(entry.get("bracket") or ""), str(entry.get("token_id") or ""))
        book = book_by_token.get(key)
        if book is None:
            continue
        bid = safe_float(getattr(book, "no_best_bid", math.nan))
        if not math.isfinite(bid) or bid < float(args.exit_min_bid):
            continue
        fee = fee_per_share(bid)
        exit_net = bid - fee
        scored_row = scored_by_token.get(key)
        p_leg_now = safe_float(getattr(scored_row, "p_leg_win_physical_v1", math.nan)) if scored_row is not None else math.nan
        scored_outcome = str(getattr(scored_row, "outcome", "") or "") if scored_row is not None else ""
        p_now = 1.0 - p_leg_now if scored_outcome == "yes" and math.isfinite(p_leg_now) else p_leg_now
        hold_value = p_now if math.isfinite(p_now) else None
        exit_shadow = bool(hold_value is not None and exit_net >= hold_value + float(args.exit_edge_buffer))
        reason = "exit_net_ge_model_hold_value" if exit_shadow else "monitor_only"
        event = {
            "record_type": "late_window_residual_exit_recovery_shadow",
            "strategy_id": STRATEGY_ID,
            "strategy_instance": EXIT_INSTANCE,
            "strategy_head": "value_d1_no_exit_recovery",
            "created_at_utc": utc_now(),
            "exit_shadow_id": "exit-" + stable_hash({"entry": entry.get("candidate_id"), "snapshot": getattr(book, "snapshot_ts_utc", ""), "bid": bid}),
            "source_entry_candidate_id": entry.get("candidate_id"),
            "city": key[0],
            "target_date": key[1],
            "bracket": key[2],
            "token_id": key[3],
            "snapshot_ts_utc": getattr(book, "snapshot_ts_utc", None),
            "snapshot_file": getattr(book, "snapshot_file", None),
            "entry_price": safe_float(entry.get("entry_price")),
            "entry_cost_per_share": safe_float(entry.get("cost_per_share")),
            "current_no_bid": bid,
            "current_no_bid_size": safe_float(getattr(book, "no_bid_size", math.nan)),
            "current_no_depth_bid_5c": safe_float(getattr(book, "no_depth_bid_5c", math.nan)),
            "exit_fee_per_share": fee,
            "exit_net_per_share": exit_net,
            "p_leg_win_now": hold_value,
            "exit_shadow": exit_shadow,
            "exit_reason": reason,
            "no_order_placed": True,
            "zero_notional": True,
            "question": getattr(book, "question", None),
        }
        events.append(event)
    return events


def summarize_payload(
    *,
    snapshot_path: Path,
    payload: dict[str, Any],
    raw_rows: int,
    residual_events: list[dict[str, Any]],
    value_events: list[dict[str, Any]],
    blocked_events: list[dict[str, Any]],
    exit_events: list[dict[str, Any]],
    plans: list[dict[str, Any]],
    model_rows: int,
    model_dates: list[str],
    executor_result: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "generated_at_utc": utc_now(),
        "strategy_id": STRATEGY_ID,
        "snapshot_file": rel(snapshot_path),
        "snapshot_ts_utc": payload.get("ts_utc"),
        "snapshot_ts_beijing": payload.get("ts_beijing"),
        "training_rows": model_rows,
        "training_date_min": min(model_dates) if model_dates else None,
        "training_date_max": max(model_dates) if model_dates else None,
        "raw_candidate_rows": raw_rows,
        "residual_high_price_no": {
            "mode": "zero_notional_shadow",
            "rows_this_cycle": len(residual_events),
            "avg_entry_price": avg([safe_float(r.get("entry_price")) for r in residual_events]),
            "avg_p_leg_win": avg([safe_float(r.get("p_leg_win_physical_v1")) for r in residual_events]),
        },
        "value_d1_no": {
            "mode": "tiny_probe_plan",
            "live_enabled_plans": sum(1 for p in plans if p.get("live_enabled")),
            "plans_written": len(plans),
            "accepted_this_cycle": len(value_events),
            "blocked_this_cycle": len(blocked_events),
            "avg_entry_price": avg([safe_float(r.get("entry_price")) for r in value_events]),
            "avg_p_leg_win": avg([safe_float(r.get("p_leg_win_physical_v1")) for r in value_events]),
            "avg_model_edge_per_share": avg([safe_float(r.get("model_edge_per_share")) for r in value_events]),
        },
        "exit_recovery_shadow": {
            "mode": "shadow_only_no_exit_orders",
            "events_this_cycle": len(exit_events),
            "exit_shadow_true": sum(1 for r in exit_events if r.get("exit_shadow")),
            "avg_exit_net_per_share": avg([safe_float(r.get("exit_net_per_share")) for r in exit_events]),
        },
        "paths": {
            "latest": rel(LATEST_OUT),
            "summary": rel(SUMMARY_OUT),
            "history": rel(HISTORY_OUT),
            "residual_shadow": rel(RESIDUAL_SHADOW_OUT),
            "value_accepted": rel(VALUE_ACCEPTED_OUT),
            "value_blocked": rel(VALUE_BLOCKED_OUT),
            "exit_shadow": rel(EXIT_SHADOW_OUT),
            "trade_plans": rel(PLAN_OUT),
            "paper_orders": rel(PAPER_OUT),
            "live_orders": rel(LIVE_OUT),
        },
        "executor_result": executor_result,
        "no_order_placed": not any(p.get("live_enabled") for p in plans),
    }


def avg(values: list[float]) -> float | None:
    clean = [v for v in values if math.isfinite(v)]
    return sum(clean) / len(clean) if clean else None


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    snapshot_dir = Path(args.snapshot_dir)
    snapshot_path = latest_snapshot_path(snapshot_dir, args.snapshot)
    raw_candidates, no_books, snapshot_payload = snapshot_candidate_rows(snapshot_path)
    raw_candidates = attach_live_context(
        raw_candidates,
        obs_index=load_observation_index(args.observation_cache),
        forecast_index=load_forecast_enrichment_index(args.forecast_enrichment),
    )
    model, model_rows, model_dates = load_probability_model(Path(args.training_rows))
    scored = score_candidates(raw_candidates, model)
    residual = select_residual_shadow(scored, args)
    value, blocked = select_value_d1(scored, args)
    value, live_guard_blocked = apply_live_guards(value)
    if not live_guard_blocked.empty:
        blocked = pd.concat([blocked, live_guard_blocked], ignore_index=True)

    residual_events = [
        event_payload(r, strategy_instance=RESIDUAL_INSTANCE, strategy_head="residual_high_price_no", status="shadow_only", reason="high_price_no_residual_shadow")
        for _, r in residual.iterrows()
    ]
    value_events = [
        event_payload(r, strategy_instance=VALUE_INSTANCE, strategy_head="value_d1_no", status="accepted", reason="p_leg_win_cost_edge_positive")
        for _, r in value.iterrows()
    ]
    blocked_events = [
        event_payload(
            r,
            strategy_instance=VALUE_INSTANCE,
            strategy_head="value_d1_no",
            status="blocked",
            reason=str(r.get("live_guard_reason") or r.get("value_block_reason") or "p_leg_win_cost_edge_negative"),
        )
        for _, r in blocked.iterrows()
    ]
    plans = write_plans(value, args)
    exit_events = exit_recovery_events(no_books, scored, args)

    appended = {
        "residual_shadow": append_unique(RESIDUAL_SHADOW_OUT, residual_events, "candidate_id"),
        "value_accepted": append_unique(VALUE_ACCEPTED_OUT, value_events, "candidate_id"),
        "value_blocked": append_unique(VALUE_BLOCKED_OUT, blocked_events, "candidate_id"),
        "exit_shadow": append_unique(EXIT_SHADOW_OUT, exit_events, "exit_shadow_id"),
    }
    executor_result = None
    if args.live:
        executor_result = order_runtime.run_weather_order_executor(
            root=ROOT,
            plans_path=PLAN_OUT,
            paper_out=PAPER_OUT,
            live_out=LIVE_OUT,
            live=bool(args.live),
            confirm_live=bool(args.confirm_live),
            allow_taker=True,
            cancel_expired=True,
            no_telegram=True,
            timeout_sec=180.0,
        )

    latest = {
        "generated_at_utc": utc_now(),
        "strategy_id": STRATEGY_ID,
        "snapshot_file": rel(snapshot_path),
        "snapshot_ts_utc": snapshot_payload.get("ts_utc"),
        "residual_high_price_no": residual_events,
        "value_d1_no_accepted": value_events,
        "value_d1_no_blocked": blocked_events,
        "exit_recovery_shadow": exit_events,
        "trade_plans": plans,
        "appended": appended,
    }
    summary = summarize_payload(
        snapshot_path=snapshot_path,
        payload=snapshot_payload,
        raw_rows=len(scored),
        residual_events=residual_events,
        value_events=value_events,
        blocked_events=blocked_events,
        exit_events=exit_events,
        plans=plans,
        model_rows=model_rows,
        model_dates=model_dates,
        executor_result=executor_result,
    )
    order_runtime.write_json(LATEST_OUT, latest)
    order_runtime.write_json(SUMMARY_OUT, summary)
    order_runtime.append_jsonl(HISTORY_OUT, summary)
    print(json.dumps(order_runtime.json_ready(summary), ensure_ascii=False, sort_keys=True))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=["run", "loop"], default="run")
    parser.add_argument("--snapshot-dir", default=str(default_snapshot_dir()))
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--observation-cache", default=default_observation_cache_path())
    parser.add_argument("--forecast-enrichment", default=default_forecast_enrichment_path())
    parser.add_argument("--training-rows", default=str(TRAIN_ROWS))
    parser.add_argument("--min-depth-shares", type=float, default=5.0)
    parser.add_argument("--residual-min-price", type=float, default=0.95)
    parser.add_argument("--residual-max-price", type=float, default=0.99)
    parser.add_argument("--value-max-entry-price", type=float, default=0.95)
    parser.add_argument("--value-min-edge", type=float, default=0.0)
    parser.add_argument("--require-obs-clock", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-obs-age-min", type=float, default=20.0)
    parser.add_argument("--pre-update-blackout-min", type=float, default=30.0)
    parser.add_argument("--shares", type=float, default=5.0)
    parser.add_argument("--max-orders", type=int, default=20)
    parser.add_argument("--max-daily-cost-usd", type=float, default=25.0)
    parser.add_argument("--order-ttl-min", type=float, default=45.0)
    parser.add_argument("--exit-min-bid", type=float, default=0.05)
    parser.add_argument("--exit-edge-buffer", type=float, default=0.0)
    parser.add_argument("--live", action="store_true", help="Execute plans through weather_order_executor.")
    parser.add_argument("--confirm-live", action="store_true", help="Required with --live.")
    parser.add_argument("--interval-seconds", type=float, default=900.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "run":
        run_once(args)
        return 0
    while True:
        try:
            run_once(args)
        except Exception as exc:  # noqa: BLE001
            order_runtime.append_jsonl(
                HISTORY_OUT,
                {
                    "generated_at_utc": utc_now(),
                    "strategy_id": STRATEGY_ID,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "no_order_placed": True,
                },
            )
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        time.sleep(float(args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
