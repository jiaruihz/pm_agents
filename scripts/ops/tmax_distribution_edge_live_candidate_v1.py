#!/usr/bin/env python3
"""Realtime candidate bridge for tmax_distribution_edge.

Default behavior is safe: materialize current candidates, refresh CLOB asks,
write executor-ready trade plans, and run the common executor in paper mode.
It does not place live orders unless --live and --confirm-live are both passed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "scripts/analysis/reheat_risk"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_intraday_weather_regime_atlas_v1 as atlas  # noqa: E402
import research_tmax_distribution_p0_anchor_scorecard_v1 as p0  # noqa: E402
import research_tmax_distribution_p1_fusion_scorecard_v1 as p1  # noqa: E402
import research_tmax_distribution_p2_ev_shadow_v1 as p2  # noqa: E402
import research_tmax_distribution_p3_feature_ablation_v1 as p3  # noqa: E402
import research_tmax_distribution_p4_observed_label_extension_v1 as p4  # noqa: E402
from src.strategies.weather_edge_v1.tools.execution_pipeline import stable_hash  # noqa: E402


STRATEGY_INSTANCE = "tmax_distribution_edge_live_candidate_v1"
STRATEGY_ID = "tmax_dist_clean_edge02_tiny_live_v1"
STRATEGY_FAMILY = "reheat_risk.tmax_distribution_edge"
MODEL_SPEC = "loo_no_city_source"
MODEL_METHOD = f"{MODEL_SPEC}_blend"
CLOB_BOOK_API = "https://clob.polymarket.com/book"

RUNTIME_DEFAULT = ROOT / "runtime/weather_edge_v1/tmax_distribution_edge_live_candidate_v1"
SNAPSHOT_DIR_CANDIDATES = [
    Path("~/projects/weather_data_feed_service_runtime/targeted_output/paper_snapshots").expanduser(),
    ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots",
]
OBS_CACHE_CANDIDATES = [
    Path("~/projects/weather_data_feed_service_runtime/output/observations/latest.json").expanduser(),
    ROOT / "runtime/weather_edge_v1/market_data/observations/latest.json",
    ROOT / "runtime/weather_edge_v1/observations/latest.json",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime | None:
    text = safe_str(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def json_ready(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (np.integer, np.floating)):
        return json_ready(value.item())
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    return value


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(json_ready(row), ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(json_ready(payload), ensure_ascii=False, sort_keys=True) + "\n")


def latest_snapshot_path(explicit: str = "", snapshot_dir: str = "") -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    candidates = [Path(snapshot_dir).expanduser()] if snapshot_dir else SNAPSHOT_DIR_CANDIDATES
    newest: tuple[float, Path] | None = None
    for directory in candidates:
        if not directory.exists():
            continue
        for path in directory.glob("snapshot_*.json"):
            mtime = path.stat().st_mtime
            if newest is None or mtime > newest[0]:
                newest = (mtime, path)
    return newest[1] if newest else None


def observation_cache_path(explicit: str = "") -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    for path in OBS_CACHE_CANDIDATES:
        if path.exists():
            return path
    return None


def load_snapshot(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = [r for r in payload.get("records", []) if isinstance(r, dict)]
    return payload, records


def load_observations(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = [r for r in payload.get("records", []) if isinstance(r, dict)]
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in records:
        city = safe_str(row.get("city"))
        target_date = safe_str(row.get("target_date"))
        if city and target_date:
            out[(city, target_date)] = row
    return out


def sky_code_value(value: Any) -> float:
    text = safe_str(value).upper()
    if not text:
        return math.nan
    mapping = {"CLR": 0.0, "SKC": 0.0, "FEW": 1.0, "SCT": 2.0, "BKN": 3.0, "OVC": 4.0, "VV": 4.0}
    for key, score in mapping.items():
        if key in text:
            return score
    return to_float(value, math.nan)


def native_from_obs(obs: dict[str, Any], unit: str) -> tuple[float, float]:
    unit = unit.upper()
    current_c = to_float(obs.get("current_temp_c"), math.nan)
    running_c = to_float(obs.get("running_max_c"), math.nan)
    current_f = to_float(obs.get("tmpf_now"), math.nan)
    if not math.isfinite(current_f) and math.isfinite(current_c):
        current_f = current_c * 9.0 / 5.0 + 32.0
    if unit == "F":
        running_f = running_c * 9.0 / 5.0 + 32.0 if math.isfinite(running_c) else math.nan
        return current_f, running_f
    return current_c, running_c


def record_interval(row: dict[str, Any]) -> tuple[float, float] | None:
    return p0._interval(row.get("bracket"))


def bracket_sort_key(item: tuple[dict[str, Any], tuple[float, float]]) -> tuple[float, float]:
    row, interval = item
    lo, hi = interval
    return (lo if math.isfinite(lo) else -9999.0, hi if math.isfinite(hi) else 9999.0)


def first_price_level(book: dict[str, Any], side: str) -> tuple[float, float]:
    levels = book.get(f"{side}s") if isinstance(book, dict) else []
    out: list[tuple[float, float]] = []
    for item in levels or []:
        price = to_float(item.get("price"), math.nan)
        size = to_float(item.get("size"), math.nan)
        if math.isfinite(price) and math.isfinite(size) and price > 0 and size > 0:
            out.append((price, size))
    if not out:
        return math.nan, 0.0
    out = sorted(out, reverse=(side == "bid"))
    return out[0]


def row_price(row: dict[str, Any], side: str) -> tuple[float, float]:
    side = side.lower()
    ask = to_float(row.get(f"{side}_best_ask"), math.nan)
    size = to_float(row.get(f"{side}_ask_size"), math.nan)
    if math.isfinite(ask):
        return ask, size if math.isfinite(size) else 0.0
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    return first_price_level(raw, "ask")


def row_bid(row: dict[str, Any], side: str) -> tuple[float, float]:
    side = side.lower()
    bid = to_float(row.get(f"{side}_best_bid"), math.nan)
    size = to_float(row.get(f"{side}_bid_size"), math.nan)
    if math.isfinite(bid):
        return bid, size if math.isfinite(size) else 0.0
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    return first_price_level(raw, "bid")


def build_state_rows(snapshot: dict[str, Any], records: list[dict[str, Any]], observations: dict[tuple[str, str], dict[str, Any]]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    audits: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in records:
        city = safe_str(row.get("city"))
        target_date = safe_str(row.get("target_date") or row.get("event_date"))
        if not city or not target_date:
            continue
        if safe_str(row.get("market_local_date")) and safe_str(row.get("market_local_date")) != target_date:
            continue
        grouped.setdefault((city, target_date), []).append(row)

    rows: list[dict[str, Any]] = []
    snapshot_ts = safe_str(snapshot.get("ts_utc") or snapshot.get("snapshot_ts_utc"))
    for (city, target_date), city_rows in grouped.items():
        obs = observations.get((city, target_date))
        if not obs or safe_str(obs.get("status")) != "ok":
            audits.append({"city": city, "target_date": target_date, "status": "observation_missing_or_not_ok"})
            continue
        unit = safe_str(city_rows[0].get("unit")).upper() or safe_str(obs.get("unit")).upper()
        if unit not in {"C", "F"}:
            audits.append({"city": city, "target_date": target_date, "status": "missing_unit"})
            continue
        current_native, running_native = native_from_obs(obs, unit)
        if not math.isfinite(current_native) or not math.isfinite(running_native):
            audits.append({"city": city, "target_date": target_date, "status": "observation_bad_temperature"})
            continue
        with_intervals = [(row, iv) for row in city_rows if (iv := record_interval(row)) is not None]
        with_intervals = sorted(with_intervals, key=bracket_sort_key)
        current_pos = None
        for idx, (_row, interval) in enumerate(with_intervals):
            lo, hi = interval
            if running_native >= lo - 1e-9 and running_native <= hi + 1e-9:
                current_pos = idx
                break
        if current_pos is None or current_pos + 2 >= len(with_intervals):
            audits.append({"city": city, "target_date": target_date, "status": "cannot_map_current_d1_d2", "running_native": running_native})
            continue
        current_record, current_iv = with_intervals[current_pos]
        d1_record, d1_iv = with_intervals[current_pos + 1]
        d2_record, d2_iv = with_intervals[current_pos + 2]

        current_yes_ask, current_yes_size = row_price(current_record, "yes")
        current_no_ask, current_no_size = row_price(current_record, "no")
        current_no_bid, _ = row_bid(current_record, "no")
        d1_no_ask, d1_no_size = row_price(d1_record, "no")
        d1_no_bid, _ = row_bid(d1_record, "no")
        d2_no_ask, d2_no_size = row_price(d2_record, "no")
        d2_no_bid, _ = row_bid(d2_record, "no")
        if not all(math.isfinite(x) and x > 0 for x in [current_yes_ask, current_no_ask, d1_no_ask, d2_no_ask]):
            audits.append({"city": city, "target_date": target_date, "status": "missing_local_ladder_ask"})
            continue

        forecast_max_f = to_float(current_record.get("forecast_max_f"), math.nan)
        forecast_max_native = to_float(current_record.get("forecast_max_native"), math.nan)
        if not math.isfinite(forecast_max_native) and math.isfinite(forecast_max_f):
            forecast_max_native = (forecast_max_f - 32.0) * 5.0 / 9.0 if unit == "C" else forecast_max_f
        decision_hour = to_float(str(current_record.get("ts_local") or "")[11:13], math.nan)
        if not math.isfinite(decision_hour):
            local_ts = safe_str(current_record.get("ts_local"))
            decision_hour = to_float(local_ts[11:13], math.nan) if len(local_ts) >= 13 else math.nan
        if not math.isfinite(decision_hour):
            decision_hour = to_float(current_record.get("decision_hour_local"), math.nan)
        forecast_peak_hour = to_float(current_record.get("forecast_peak_hour_local"), math.nan)
        forecast_peak_delta = to_float(current_record.get("forecast_peak_delta_hours_local"), math.nan)
        if not math.isfinite(forecast_peak_delta) and math.isfinite(decision_hour) and math.isfinite(forecast_peak_hour):
            forecast_peak_delta = decision_hour - forecast_peak_hour
        source = safe_str(current_record.get("forecast_source") or current_record.get("model"))
        forecast_gap = forecast_max_native - running_native if math.isfinite(forecast_max_native) else math.nan
        gfs_gap = forecast_gap if source.lower() == "gfs" else math.nan
        ecmwf_gap = forecast_gap if source.lower() in {"ecmwf", "ecmwf_ifs"} else math.nan

        row = {
            "city": city,
            "target_date": target_date,
            "decision_hour_local": decision_hour,
            "decision_snapshot_ts_utc": snapshot_ts,
            "unit": unit,
            "timezone": safe_str(current_record.get("timezone_name") or obs.get("timezone_name")),
            "current_bracket": safe_str(current_record.get("bracket")),
            "d1_no_bracket": safe_str(d1_record.get("bracket")),
            "d2_no_bracket": safe_str(d2_record.get("bracket")),
            "current_yes_ask": current_yes_ask,
            "current_yes_ask_size": current_yes_size,
            "current_bracket_no_ask": current_no_ask,
            "current_no_ask": current_no_ask,
            "current_no_ask_size": current_no_size,
            "current_no_bid": current_no_bid,
            "d1_no_ask": d1_no_ask,
            "d1_no_ask_size": d1_no_size,
            "d1_no_bid": d1_no_bid,
            "d2_no_ask": d2_no_ask,
            "d2_no_ask_size": d2_no_size,
            "d2_no_bid": d2_no_bid,
            "current_yes_token_id": safe_str(current_record.get("yes_token_id")),
            "current_no_token_id": safe_str(current_record.get("no_token_id")),
            "d1_no_token_id": safe_str(d1_record.get("no_token_id")),
            "d2_no_token_id": safe_str(d2_record.get("no_token_id")),
            "current_market_id": safe_str(current_record.get("market_id")),
            "d1_market_id": safe_str(d1_record.get("market_id")),
            "d2_market_id": safe_str(d2_record.get("market_id")),
            "event_slug": safe_str(current_record.get("event_slug")),
            "question": safe_str(current_record.get("question")),
            "forecast_source": source or "unknown",
            "forecast_max_f": forecast_max_f,
            "forecast_max_native": forecast_max_native,
            "forecast_peak_hour_local": forecast_peak_hour,
            "forecast_peak_delta_hours_local": forecast_peak_delta,
            "forecast_peak_hour_spread": to_float(current_record.get("forecast_peak_hour_spread"), math.nan),
            "forecast_gap_to_running_native": forecast_gap,
            "gfs_gap_to_running_native": gfs_gap,
            "ecmwf_gap_to_running_native": ecmwf_gap,
            "running_native": running_native,
            "current_native": current_native,
            "decline_native": running_native - current_native,
            "running_value": running_native,
            "current_temp_c": to_float(obs.get("current_temp_c"), math.nan),
            "running_max_c": to_float(obs.get("running_max_c"), math.nan),
            "tmpf_now": to_float(obs.get("tmpf_now"), math.nan),
            "dwpf_now": to_float(obs.get("dwpf_now"), math.nan),
            "dewpoint_depression_f": to_float(obs.get("dewpoint_depression_f"), math.nan),
            "relative_humidity_pct": to_float(obs.get("relh_now"), math.nan),
            "wind_speed_kt": to_float(obs.get("sknt_now"), math.nan),
            "sky_cover_code": sky_code_value(obs.get("sky_code_now")),
            "temp_trend_1h_f": to_float(obs.get("d_tmpf_1h"), math.nan),
            "temp_trend_3h_f": to_float(obs.get("d_tmpf_3h"), math.nan),
            "minutes_since_running_max": to_float(obs.get("minutes_since_running_max"), math.nan),
            "running_max_obs_utc": safe_str(obs.get("running_max_obs_utc")),
            "obs_age_min": to_float(obs.get("age_min"), math.nan),
            "obs_source": safe_str(obs.get("source")),
            "snapshot_path": safe_str(current_record.get("source_snapshot_path")),
            "actual_bucket": "current",
        }
        rows.append(row)

    if not rows:
        return pd.DataFrame(), audits
    out = pd.DataFrame(rows)
    out = atlas.add_regime_labels(out)
    out = add_distribution_features(out)
    return out, audits


def add_distribution_features(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in df.to_dict("records"):
        s = pd.Series(item)
        current_iv = p0._interval(s.get("current_bracket"))
        d1_iv = p0._interval(s.get("d1_no_bracket"))
        d2_iv = p0._interval(s.get("d2_no_bracket"))
        if current_iv is None or d1_iv is None or d2_iv is None:
            continue
        market = p0._market_distribution(s)
        if market is None:
            continue
        forecast_anchor = p0._soft_anchor_distribution(p0._as_float(s.get("forecast_max_native")), current_iv, d1_iv, d2_iv)
        running_anchor = p0._soft_anchor_distribution(p0._as_float(s.get("running_native")), current_iv, d1_iv, d2_iv)
        row = dict(item)
        hour = p0._as_float(item.get("decision_hour_local"))
        row["hour_bucket"] = p0._hour_bucket(item.get("decision_hour_local"))
        if hour is not None:
            row["decision_hour_sin"] = math.sin(2.0 * math.pi * hour / 24.0)
            row["decision_hour_cos"] = math.cos(2.0 * math.pi * hour / 24.0)
        peak_delta = p0._as_float(item.get("forecast_peak_delta_hours_local"))
        row["forecast_peak_delta_abs"] = abs(peak_delta) if peak_delta is not None else None
        current_upper = p1._safe_upper(current_iv)
        d1_upper = p1._safe_upper(d1_iv)
        d2_upper = p1._safe_upper(d2_iv)
        current_mid = p1._safe_mid(current_iv)
        d1_mid = p1._safe_mid(d1_iv)
        d2_mid = p1._safe_mid(d2_iv)
        row["forecast_minus_running_native"] = p1._delta(item.get("forecast_max_native"), item.get("running_native"))
        row["forecast_minus_current_native"] = p1._delta(item.get("forecast_max_native"), item.get("current_native"))
        row["running_minus_current_native"] = p1._delta(item.get("running_native"), item.get("current_native"))
        row["forecast_to_current_upper_native"] = p1._delta(item.get("forecast_max_native"), current_upper)
        row["forecast_to_d1_upper_native"] = p1._delta(item.get("forecast_max_native"), d1_upper)
        row["forecast_to_d2_upper_native"] = p1._delta(item.get("forecast_max_native"), d2_upper)
        row["forecast_to_current_mid_native"] = p1._delta(item.get("forecast_max_native"), current_mid)
        row["forecast_to_d1_mid_native"] = p1._delta(item.get("forecast_max_native"), d1_mid)
        row["forecast_to_d2_mid_native"] = p1._delta(item.get("forecast_max_native"), d2_mid)
        row["running_to_current_upper_native"] = p1._delta(item.get("running_native"), current_upper)
        row["current_to_current_upper_native"] = p1._delta(item.get("current_native"), current_upper)
        if current_mid is not None:
            row["running_position_in_current_native"] = p1._delta(item.get("running_native"), current_mid)
            row["current_position_in_current_native"] = p1._delta(item.get("current_native"), current_mid)
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
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out = p3._add_boundary_features(out)
    return out


def fit_predict_live(live_df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    hist, counters = p4._load_rows_extended()
    specs = p3._feature_specs(hist)
    p1.MODEL_SPECS = specs
    train_pre = hist[hist["target_date"] < p1.TRAIN_CUTOFF].copy()
    selection = p1._select_model(train_pre, MODEL_SPEC)
    c_value = selection["selected"]["c"]
    alpha = selection["selected"]["cv_blend_alpha"]
    min_target = str(live_df["target_date"].min())
    fit_df = hist[hist["target_date"] < min_target].copy()
    if fit_df.empty:
        fit_df = train_pre
    needed = list(specs[MODEL_SPEC]["numeric"]) + list(specs[MODEL_SPEC]["categorical"])
    for col in needed:
        if col not in live_df.columns:
            live_df[col] = "unknown" if col in specs[MODEL_SPEC]["categorical"] else np.nan
    pred = p1._fit_predict(fit_df, live_df, MODEL_SPEC, c_value)
    pred = p1._blend_predictions(pred, 1.0, f"{MODEL_SPEC}_model")
    pred = p1._blend_predictions(pred, alpha, MODEL_METHOD)
    meta = {
        "model_spec": MODEL_SPEC,
        "model_method": MODEL_METHOD,
        "selected_c": c_value,
        "selected_alpha": alpha,
        "fit_rows": int(len(fit_df)),
        "fit_date_range": [str(fit_df["target_date"].min()), str(fit_df["target_date"].max())],
        "hist_counters": counters,
    }
    return pred, meta


def ask_and_token(row: pd.Series, expression: str) -> tuple[float, float, str, str, str]:
    if expression == "current_yes":
        return (
            float(row["current_yes_ask"]),
            to_float(row.get("current_yes_ask_size"), 0.0),
            safe_str(row.get("current_yes_token_id")),
            safe_str(row.get("current_market_id")),
            safe_str(row.get("current_bracket")),
        )
    if expression == "current_no":
        return (
            float(row["current_bracket_no_ask"]),
            to_float(row.get("current_no_ask_size"), 0.0),
            safe_str(row.get("current_no_token_id")),
            safe_str(row.get("current_market_id")),
            safe_str(row.get("current_bracket")),
        )
    if expression == "d1_no":
        return (
            float(row["d1_no_ask"]),
            to_float(row.get("d1_no_ask_size"), 0.0),
            safe_str(row.get("d1_no_token_id")),
            safe_str(row.get("d1_market_id")),
            safe_str(row.get("d1_no_bracket")),
        )
    if expression == "d2_no":
        return (
            float(row["d2_no_ask"]),
            to_float(row.get("d2_no_ask_size"), 0.0),
            safe_str(row.get("d2_no_token_id")),
            safe_str(row.get("d2_market_id")),
            safe_str(row.get("d2_no_bracket")),
        )
    raise ValueError(expression)


def win_prob(pred_row: pd.Series, expression: str) -> float:
    p_current = float(pred_row[f"{MODEL_METHOD}_p_current"])
    p_d1 = float(pred_row[f"{MODEL_METHOD}_p_d1"])
    p_d2 = float(pred_row[f"{MODEL_METHOD}_p_d2"])
    if expression == "current_yes":
        return p_current
    if expression == "current_no":
        return 1.0 - p_current
    if expression == "d1_no":
        return 1.0 - p_d1
    if expression == "d2_no":
        return 1.0 - p_d2
    raise ValueError(expression)


def build_candidates(live_df: pd.DataFrame, pred: pd.DataFrame, args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    keys = ["city", "target_date", "decision_hour_local", "actual_bucket"]
    df = live_df.merge(pred, on=keys, how="inner", validate="one_to_one")
    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item in df.to_dict("records"):
        row = pd.Series(item)
        trend3h = to_float(row.get("temp_trend_3h_f"), math.nan)
        if args.exclude_trend3h_flat and not math.isfinite(trend3h):
            blocked.append({**candidate_base(item), "decision_status": "blocked", "block_reason": "trend3h_missing"})
            continue
        if args.exclude_trend3h_flat and args.trend3h_flat_low <= trend3h < args.trend3h_flat_high:
            blocked.append({**candidate_base(item), "decision_status": "blocked", "block_reason": "trend3h_flat"})
            continue
        best: dict[str, Any] | None = None
        for expression in p2.EXPRESSIONS:
            ask, ask_size, token_id, market_id, bracket = ask_and_token(row, expression)
            p_win = win_prob(row, expression)
            edge = p_win - ask
            base = {
                **candidate_base(item),
                "chosen_expression": expression,
                "signal_side": "BUY_YES" if expression == "current_yes" else "BUY_NO",
                "ask": ask,
                "ask_size": ask_size,
                "p_win": p_win,
                "model_edge": edge,
                "model_roi": edge / ask if ask > 0 else None,
                "token_id": token_id,
                "market_id": market_id,
                "bracket": bracket,
            }
            reason = ""
            if not token_id:
                reason = "missing_token_id"
            elif ask < args.ask_floor:
                reason = "below_ask_floor"
            elif ask > args.ask_ceiling:
                reason = "above_ask_ceiling"
            elif edge < args.edge_threshold:
                reason = "below_edge_threshold"
            if reason:
                blocked.append({**base, "decision_status": "blocked", "block_reason": reason})
                continue
            if best is None or (edge, base["model_roi"]) > (best["model_edge"], best["model_roi"]):
                best = base
        if best is None:
            continue
        best["decision_status"] = "candidate_selected_pre_fresh_book"
        best["candidate_id"] = stable_hash(
            {
                "strategy_id": STRATEGY_ID,
                "city": best["city"],
                "target_date": best["target_date"],
                "decision_snapshot_ts_utc": best["decision_snapshot_ts_utc"],
                "token_id": best["token_id"],
                "chosen_expression": best["chosen_expression"],
            }
        )
        candidates.append(best)
    return candidates, blocked


def candidate_base(item: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "city",
        "target_date",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "event_slug",
        "question",
        "unit",
        "forecast_source",
        "forecast_max_native",
        "forecast_peak_hour_local",
        "forecast_peak_delta_hours_local",
        "current_bracket",
        "d1_no_bracket",
        "d2_no_bracket",
        "current_native",
        "running_native",
        "decline_native",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
        "day_regime",
        "intraday_state",
        "running_max_state",
        "wind_regime",
        "moisture_cloud_regime",
        "solar_window",
        "city_family",
        "obs_age_min",
        "obs_source",
        "running_max_obs_utc",
    ]
    out = {k: item.get(k) for k in fields if k in item}
    out.update(
        {
            "record_type": "tmax_distribution_edge_candidate_event",
            "strategy_instance": STRATEGY_INSTANCE,
            "strategy_id": STRATEGY_ID,
            "strategy_family": STRATEGY_FAMILY,
            "model_method": MODEL_METHOD,
            "zero_notional": True,
            "no_order_placed": True,
        }
    )
    return out


def fetch_book(token_id: str, *, timeout_sec: float, retries: int) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(max(1, int(retries) + 1)):
        try:
            with httpx.Client(timeout=timeout_sec, trust_env=True) as client:
                response = client.get(CLOB_BOOK_API, params={"token_id": token_id})
                response.raise_for_status()
                data = response.json()
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= int(retries):
                raise
            time.sleep(min(2.0, 0.35 * (attempt + 1)))
    else:
        raise RuntimeError(f"book fetch failed: {last_error}")
    if not isinstance(data, dict):
        raise RuntimeError("CLOB book response is not an object")
    return data


def book_levels(book: dict[str, Any], side: str) -> list[tuple[float, float]]:
    levels = book.get(f"{side}s") or []
    out = []
    for item in levels:
        price = to_float(item.get("price"), math.nan)
        size = to_float(item.get("size"), math.nan)
        if math.isfinite(price) and math.isfinite(size) and price > 0 and size > 0:
            out.append((price, size))
    return sorted(out, reverse=(side == "bid"))


def fresh_quote(candidate: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    try:
        book = fetch_book(str(candidate["token_id"]), timeout_sec=args.clob_timeout_sec, retries=args.clob_retries)
    except Exception as exc:  # noqa: BLE001
        return {"status": "rejected", "reason": "fresh_book_fetch_failed", "error": f"{type(exc).__name__}: {exc}"}
    asks = book_levels(book, "ask")
    bids = book_levels(book, "bid")
    if not asks:
        return {"status": "rejected", "reason": "fresh_book_no_ask", "best_bid": bids[0][0] if bids else 0.0}
    fresh_ask, fresh_size = asks[0]
    best_bid = bids[0][0] if bids else 0.0
    p_win = float(candidate["p_win"])
    snapshot_ask = float(candidate["ask"])
    fee = args.fee_rate * fresh_ask * (1.0 - fresh_ask)
    fee_adjusted_edge = p_win - fresh_ask - fee
    if fresh_size + 1e-9 < args.fixed_shares:
        return {
            "status": "rejected",
            "reason": "fresh_ask_size_below_fixed_shares",
            "best_bid": best_bid,
            "fresh_ask": fresh_ask,
            "fresh_ask_size": fresh_size,
        }
    if fresh_ask < args.ask_floor - 1e-9:
        return {"status": "rejected", "reason": "fresh_ask_below_floor", "best_bid": best_bid, "fresh_ask": fresh_ask, "fresh_ask_size": fresh_size}
    if fresh_ask > args.ask_ceiling + 1e-9:
        return {"status": "rejected", "reason": "fresh_ask_above_ceiling", "best_bid": best_bid, "fresh_ask": fresh_ask, "fresh_ask_size": fresh_size}
    if fresh_ask > snapshot_ask + args.max_fresh_ask_drift + 1e-9:
        return {
            "status": "rejected",
            "reason": "fresh_ask_exceeds_snapshot_drift",
            "best_bid": best_bid,
            "fresh_ask": fresh_ask,
            "fresh_ask_size": fresh_size,
            "snapshot_ask": snapshot_ask,
            "max_fresh_ask": snapshot_ask + args.max_fresh_ask_drift,
        }
    if fee_adjusted_edge < args.edge_threshold - 1e-9:
        return {
            "status": "rejected",
            "reason": "fee_adjusted_edge_below_threshold",
            "best_bid": best_bid,
            "fresh_ask": fresh_ask,
            "fresh_ask_size": fresh_size,
            "fee": fee,
            "fee_adjusted_edge": fee_adjusted_edge,
            "required_edge": args.edge_threshold,
        }
    return {
        "status": "accepted",
        "best_bid": best_bid,
        "fresh_ask": fresh_ask,
        "fresh_ask_size": fresh_size,
        "fresh_available_notional": fresh_ask * fresh_size,
        "limit_price": fresh_ask,
        "fee": fee,
        "fee_adjusted_edge": fee_adjusted_edge,
        "snapshot_ask": snapshot_ask,
        "fresh_ask_drift": fresh_ask - snapshot_ask,
    }


def build_plan(candidate: dict[str, Any], quote: dict[str, Any], args: argparse.Namespace, *, live_enabled: bool) -> dict[str, Any]:
    price = float(quote["limit_price"])
    size = float(args.fixed_shares)
    base = {
        "signal_id": candidate["candidate_id"],
        "strategy": "weather_edge_v1",
        "strategy_instance": STRATEGY_INSTANCE,
        "source_strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "strategy_family": STRATEGY_FAMILY,
        "probability_source": MODEL_METHOD,
        "decision_mode": "tmax_distribution_edge_clean_edge02",
        "execution_mode": "fresh_book_guarded_taker",
        "profile": "clean_edge02",
        "combo": f"{candidate['chosen_expression']}_edge02",
        "city": candidate["city"],
        "city_pool": "tmax_distribution_edge",
        "target_date": candidate["target_date"],
        "market_id": candidate["market_id"],
        "event_slug": candidate.get("event_slug", ""),
        "question": candidate.get("question", ""),
        "bracket": candidate["bracket"],
        "token_id": candidate["token_id"],
        "signal_side": candidate["signal_side"],
        "order_side": "BUY",
        "market_price": round(price, 6),
        "best_bid": round(float(quote.get("best_bid") or 0.0), 6),
        "best_ask": round(float(quote["fresh_ask"]), 6),
        "spread": round(max(0.0, float(quote["fresh_ask"]) - float(quote.get("best_bid") or 0.0)), 6),
        "limit_price": round(price, 6),
        "quote_status": "accepted",
        "quote_reason": "tmax_distribution_edge_fresh_book_guarded_taker",
        "quote_edge": round(float(candidate["p_win"]) - price, 6),
        "required_quote_edge": round(float(args.edge_threshold), 6),
        "model_token_probability": round(float(candidate["p_win"]), 6),
        "quote_best_bid": round(float(quote.get("best_bid") or 0.0), 6),
        "quote_best_ask": round(float(quote["fresh_ask"]), 6),
        "quote_spread": round(max(0.0, float(quote["fresh_ask"]) - float(quote.get("best_bid") or 0.0)), 6),
        "quote_tick_size": 0.01,
        "quote_mode": "fresh_book_guarded_taker",
        "child_order_role": "single",
        "maker_only": False,
        "notional_fraction": 1.0,
        "size_multiplier": 1.0,
        "order_notional_cap": round(size * price, 6),
        "execution_policy": "tmax_distribution_edge_taker_v1",
        "tick_size": 0.01,
        "min_quote_edge": round(float(args.edge_threshold), 6),
        "sizing_mode": "fixed_shares",
        "fixed_order_shares": size,
        "max_order_shares": size,
        "size": size,
        "notional": round(size * price, 6),
        "edge": round(float(candidate["model_edge"]), 6),
        "min_edge": round(float(args.edge_threshold), 6),
        "shadow_decision": STRATEGY_ID,
        "shadow_reason": "clean_edge02_realtime_materialized_fresh_book_checked",
        "paper_enabled": True,
        "live_enabled": bool(live_enabled),
        "snapshot_ts_utc": candidate.get("decision_snapshot_ts_utc", ""),
        "source_snapshot_path": candidate.get("snapshot_path", ""),
        "decision_local_time": str(candidate.get("decision_hour_local", "")),
        "decision_timezone": "",
        "running_max_obs_utc": candidate.get("running_max_obs_utc", ""),
        "obs_age_min": to_float(candidate.get("obs_age_min"), 0.0),
        "minutes_since_running_max": to_float(candidate.get("minutes_since_running_max"), 0.0),
        "forecast_peak_delta_hours_local": candidate.get("forecast_peak_delta_hours_local"),
        "fresh_ask_size": quote.get("fresh_ask_size"),
        "fresh_available_notional": quote.get("fresh_available_notional"),
        "fresh_ask_drift": quote.get("fresh_ask_drift"),
        "fee_adjusted_edge": quote.get("fee_adjusted_edge"),
    }
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": stable_hash(base),
        "created_at_utc": utc_now(),
        "status": "accepted",
        "risk_status": "passed",
        "risk_reason": "",
        **base,
    }


def execute_plans(args: argparse.Namespace, runtime_dir: Path, plans_path: Path) -> dict[str, Any]:
    cmd = [
        str(ROOT / ".venv/bin/python" if (ROOT / ".venv/bin/python").exists() else "python3"),
        "scripts/ops/weather_order_executor.py",
        "--plans",
        str(plans_path),
        "--paper-out",
        str(runtime_dir / "paper_orders.jsonl"),
        "--live-out",
        str(runtime_dir / "live_orders.jsonl"),
        "--allow-taker",
        "--no-telegram",
    ]
    if args.live:
        cmd.extend(["--live", "--confirm-live"])
    proc = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180, check=False)
    parsed = None
    try:
        start = proc.stdout.find("{")
        end = proc.stdout.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(proc.stdout[start : end + 1])
    except Exception:
        parsed = None
    return {
        "executor_cmd": cmd,
        "executor_returncode": proc.returncode,
        "executor_output_tail": proc.stdout[-8000:],
        "executor_result": parsed,
    }


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    runtime_dir = Path(args.runtime_dir)
    snapshot_path = latest_snapshot_path(args.snapshot, args.snapshot_dir)
    if snapshot_path is None or not snapshot_path.exists():
        raise FileNotFoundError("no snapshot file found")
    snapshot, records = load_snapshot(snapshot_path)
    snapshot_ts = parse_utc(snapshot.get("ts_utc") or snapshot.get("snapshot_ts_utc"))
    now = datetime.now(timezone.utc)
    snapshot_age_min = (now - snapshot_ts).total_seconds() / 60.0 if snapshot_ts else math.nan
    if math.isfinite(snapshot_age_min) and snapshot_age_min > args.max_snapshot_age_min:
        raise RuntimeError(f"snapshot stale: age_min={snapshot_age_min:.1f} max={args.max_snapshot_age_min:.1f}")
    obs_path = observation_cache_path(args.observation_cache)
    observations = load_observations(obs_path)
    state_df, audits = build_state_rows(snapshot, records, observations)
    plans: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = list(audits)
    model_meta: dict[str, Any] = {}
    if not state_df.empty:
        pred, model_meta = fit_predict_live(state_df)
        candidates, candidate_blocked = build_candidates(state_df, pred, args)
        blocked.extend(candidate_blocked)
    accepted_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        quote = fresh_quote(candidate, args)
        enriched = {**candidate, "fresh_quote": quote}
        if quote.get("status") != "accepted":
            blocked.append({**enriched, "decision_status": "blocked", "block_reason": quote.get("reason", "fresh_quote_rejected")})
            continue
        enriched.update(
            {
                "decision_status": "accepted_fresh_book",
                "fresh_best_bid": quote.get("best_bid"),
                "fresh_best_ask": quote.get("fresh_ask"),
                "fresh_ask_size": quote.get("fresh_ask_size"),
                "limit_price": quote.get("limit_price"),
                "fee_adjusted_edge": quote.get("fee_adjusted_edge"),
            }
        )
        accepted_candidates.append(enriched)
    accepted_candidates = sorted(
        accepted_candidates,
        key=lambda r: (str(r.get("target_date")), float(r.get("decision_hour_local") or 99), str(r.get("city"))),
    )[: max(0, int(args.max_orders))]
    for candidate in accepted_candidates:
        quote = candidate["fresh_quote"]
        plans.append(build_plan(candidate, quote, args, live_enabled=bool(args.live)))

    candidate_path = runtime_dir / "latest_candidates.json"
    blocked_path = runtime_dir / "latest_blocked.json"
    plans_path = runtime_dir / "trade_plans.jsonl"
    write_json(candidate_path, {"generated_at_utc": utc_now(), "events": accepted_candidates})
    write_json(blocked_path, {"generated_at_utc": utc_now(), "events": blocked})
    write_jsonl(plans_path, plans)
    executor_result = execute_plans(args, runtime_dir, plans_path) if args.execute and plans else None
    summary = {
        "generated_at_utc": utc_now(),
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "execution_mode": "live_enabled" if args.live else "paper_executor_only",
        "no_live_order_placed": not bool(args.live),
        "snapshot": rel(snapshot_path),
        "snapshot_ts_utc": snapshot.get("ts_utc") or snapshot.get("snapshot_ts_utc"),
        "snapshot_age_min": snapshot_age_min,
        "observation_cache": rel(obs_path) if obs_path else None,
        "snapshot_records": len(records),
        "state_rows": int(len(state_df)),
        "pre_fresh_candidates": len(candidates),
        "accepted_candidates": len(accepted_candidates),
        "blocked_events": len(blocked),
        "blocked_reason_counts": dict(
            Counter(str(row.get("block_reason") or row.get("status") or "unknown") for row in blocked)
        ),
        "plans": len(plans),
        "ask_floor": args.ask_floor,
        "ask_ceiling": args.ask_ceiling,
        "edge_threshold": args.edge_threshold,
        "fixed_shares": args.fixed_shares,
        "exclude_trend3h_flat": bool(args.exclude_trend3h_flat),
        "model_meta": model_meta,
        "latest_candidates": rel(candidate_path),
        "latest_blocked": rel(blocked_path),
        "trade_plans": rel(plans_path),
        "executor": executor_result,
    }
    write_json(runtime_dir / "latest_summary.json", summary)
    append_jsonl(runtime_dir / "summary_history.jsonl", summary)
    print(json.dumps(json_ready(summary), ensure_ascii=False, sort_keys=True))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "loop"], nargs="?", default="run")
    parser.add_argument("--runtime-dir", default=str(RUNTIME_DEFAULT))
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--snapshot-dir", default="")
    parser.add_argument("--observation-cache", default="")
    parser.add_argument("--max-snapshot-age-min", type=float, default=60.0)
    parser.add_argument("--edge-threshold", type=float, default=0.02)
    parser.add_argument("--ask-floor", type=float, default=0.20)
    parser.add_argument("--ask-ceiling", type=float, default=0.99)
    parser.add_argument("--fixed-shares", type=float, default=5.0)
    parser.add_argument("--max-orders", type=int, default=1)
    parser.add_argument("--exclude-trend3h-flat", action="store_true", default=True)
    parser.add_argument("--allow-trend3h-flat", action="store_false", dest="exclude_trend3h_flat")
    parser.add_argument("--trend3h-flat-low", type=float, default=-0.5)
    parser.add_argument("--trend3h-flat-high", type=float, default=0.5)
    parser.add_argument("--max-fresh-ask-drift", type=float, default=0.02)
    parser.add_argument("--fee-rate", type=float, default=0.05)
    parser.add_argument("--clob-timeout-sec", type=float, default=8.0)
    parser.add_argument("--clob-retries", type=int, default=2)
    parser.add_argument("--execute", action="store_true", help="Run weather_order_executor after writing plans. Paper-only unless --live is also set.")
    parser.add_argument("--live", action="store_true", help="Enable live order placement through executor. Requires explicit --confirm-live.")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=900.0)
    args = parser.parse_args()
    if args.live and not args.confirm_live:
        raise SystemExit("--live requires --confirm-live")
    return args


def main() -> int:
    args = parse_args()
    if args.command == "run":
        run_once(args)
        return 0
    while True:
        try:
            run_once(args)
        except Exception as exc:  # noqa: BLE001
            runtime_dir = Path(args.runtime_dir)
            payload = {
                "generated_at_utc": utc_now(),
                "strategy_instance": STRATEGY_INSTANCE,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "no_live_order_placed": True,
            }
            append_jsonl(runtime_dir / "summary_history.jsonl", payload)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
        time.sleep(float(args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
