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

import research_tmax_distribution_p0_anchor_scorecard_v1 as p0  # noqa: E402
import research_tmax_distribution_p1_fusion_scorecard_v1 as p1  # noqa: E402
import research_tmax_distribution_p2_ev_shadow_v1 as p2  # noqa: E402
import research_tmax_distribution_p3_feature_ablation_v1 as p3  # noqa: E402
import research_tmax_distribution_p4_observed_label_extension_v1 as p4  # noqa: E402
from src.strategies.weather_edge_v1.tools import regime_routed_no_stable as regime_policy  # noqa: E402
from src.strategies.weather_edge_v1.tools.execution_pipeline import stable_hash  # noqa: E402
from weather_data_feed.market_brackets import parse_market_bracket  # noqa: E402


STRATEGY_INSTANCE = "tmax_distribution_edge_live_candidate_v1"
STRATEGY_ID = "tmax_dist_clean_edge02_tiny_live_v1"
STRATEGY_FAMILY = "reheat_risk.tmax_distribution_edge"
MODEL_SPEC = "loo_no_city_source"
MODEL_METHOD = f"{MODEL_SPEC}_blend"
CLOB_BOOK_API = "https://clob.polymarket.com/book"
FIRST_LOCK_NO_CURRENT_YES_EXPRESSIONS = ["current_no", "d1_no", "d2_no", "d1_yes", "d2_yes"]

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
DATA_FEED_RUNTIME_ROOT = Path(os.environ.get("WEATHER_DATA_FEED_RUNTIME_ROOT", "/Volumes/jrs/weather_data_feed_service_runtime"))
HIGH_FREQUENCY_LATEST_CANDIDATES = [
    DATA_FEED_RUNTIME_ROOT / "output/high_frequency_observations/latest.json",
    Path("~/projects/weather_data_feed_service_runtime/output/high_frequency_observations/latest.json").expanduser(),
]
SOURCE_EVENTS_LATEST_CANDIDATES = [
    DATA_FEED_RUNTIME_ROOT / "output/source_events/latest.json",
    Path("~/projects/weather_data_feed_service_runtime/output/source_events/latest.json").expanduser(),
]
FORECAST_ENRICHMENT_LATEST_CANDIDATES = [
    DATA_FEED_RUNTIME_ROOT / "output/forecast_enrichment/latest.json",
    Path("~/projects/weather_data_feed_service_runtime/output/forecast_enrichment/latest.json").expanduser(),
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


def finite_or_none(value: Any) -> float | None:
    out = to_float(value, math.nan)
    return out if math.isfinite(out) else None


def taker_fee(price: float, fee_rate: float) -> float:
    if not math.isfinite(price):
        return math.nan
    return float(fee_rate) * price * (1.0 - price)


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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def city_day_position_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            safe_str(row.get("strategy_id")) or STRATEGY_ID,
            safe_str(row.get("city")).casefold(),
            safe_str(row.get("target_date")),
        ]
    )


def submitted_city_day_keys(path: Path, *, statuses: set[str] | None = None) -> set[str]:
    keys: set[str] = set()
    for row in read_jsonl(path):
        if statuses is not None and safe_str(row.get("status")) not in statuses:
            continue
        strategy_id = safe_str(row.get("strategy_id"))
        strategy_instance = safe_str(row.get("strategy_instance"))
        if strategy_id and strategy_id != STRATEGY_ID:
            continue
        if not strategy_id and strategy_instance and strategy_instance != STRATEGY_INSTANCE:
            continue
        key = safe_str(row.get("city_day_position_key")) or city_day_position_key(row)
        if key:
            keys.add(key)
    return keys


def submitted_live_city_day_keys(path: Path) -> set[str]:
    return submitted_city_day_keys(path, statuses={"submitted"})


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


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def market_city_key(value: Any) -> str:
    text = safe_str(value)
    aliases = {
        "Hong Kong": "HongKong",
        "New York": "NYC",
        "Los Angeles": "LA",
        "San Francisco": "SanFrancisco",
        "Tel Aviv": "TelAviv",
    }
    return aliases.get(text, text.replace(" ", ""))


def arith_round(value: float) -> int | None:
    if not math.isfinite(value):
        return None
    return int(math.floor(float(value) + 0.5) if value >= 0 else math.ceil(float(value) - 0.5))


def native_temp_from_source(row: dict[str, Any], unit: str) -> float:
    unit = unit.upper()
    if unit == "F":
        temp_f = to_float(row.get("temp_f"), math.nan)
        if math.isfinite(temp_f):
            return temp_f
        temp_c = to_float(row.get("temp_c"), math.nan)
        return temp_c * 9.0 / 5.0 + 32.0 if math.isfinite(temp_c) else math.nan
    temp_c = to_float(row.get("temp_c"), math.nan)
    if math.isfinite(temp_c):
        return temp_c
    temp_f = to_float(row.get("temp_f"), math.nan)
    return (temp_f - 32.0) * 5.0 / 9.0 if math.isfinite(temp_f) else math.nan


def latest_rows_by_city_date(path: Path | None) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    if path is None or not path.exists():
        return {}, {"path": str(path) if path else "", "status": "missing", "rows": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {}, {"path": str(path), "status": "read_error", "error": f"{type(exc).__name__}: {exc}", "rows": 0}
    records = [r for r in payload.get("records", []) if isinstance(r, dict)]
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in records:
        city = market_city_key(row.get("city"))
        target_date = safe_str(row.get("target_date"))
        if not city or not target_date:
            continue
        key = (city, target_date)
        row_dt = parse_utc(row.get("observation_time_utc") or row.get("source_report_ts_utc") or row.get("ts_utc"))
        row_detect = parse_utc(row.get("local_detect_ts_utc") or row.get("fetched_at_utc") or row.get("source_fetch_end_utc"))
        old = out.get(key)
        old_dt = parse_utc(old.get("observation_time_utc") or old.get("source_report_ts_utc") or old.get("ts_utc")) if old else None
        old_detect = parse_utc(old.get("local_detect_ts_utc") or old.get("fetched_at_utc") or old.get("source_fetch_end_utc")) if old else None
        if old is None or (row_dt or datetime.min.replace(tzinfo=timezone.utc)) > (old_dt or datetime.min.replace(tzinfo=timezone.utc)) or (
            row_dt == old_dt and (row_detect or datetime.min.replace(tzinfo=timezone.utc)) > (old_detect or datetime.min.replace(tzinfo=timezone.utc))
        ):
            out[key] = row
    return out, {"path": str(path), "status": "ok", "rows": len(records), "keys": len(out)}


def forecast_model_max_native(row: dict[str, Any], model: str, unit: str) -> float:
    multi = row.get("open_meteo_multi_model") if isinstance(row.get("open_meteo_multi_model"), dict) else {}
    target = multi.get("target_date") if isinstance(multi.get("target_date"), dict) else {}
    models = target.get("models") if isinstance(target.get("models"), dict) else {}
    value_f = to_float(models.get(model), math.nan)
    if not math.isfinite(value_f):
        return math.nan
    return value_f if unit.upper() == "F" else (value_f - 32.0) * 5.0 / 9.0


def source_relation_to_snapshot(detect_dt: datetime | None, snapshot_dt: datetime | None) -> str:
    if detect_dt is None or snapshot_dt is None:
        return "unknown"
    if detect_dt <= snapshot_dt:
        return "known_by_snapshot"
    return "newer_than_snapshot"


def enrich_source_context(state_df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if state_df.empty:
        return state_df, {"status": "empty_state"}
    hf_rows, hf_meta = latest_rows_by_city_date(first_existing(HIGH_FREQUENCY_LATEST_CANDIDATES))
    source_rows, source_meta = latest_rows_by_city_date(first_existing(SOURCE_EVENTS_LATEST_CANDIDATES))
    forecast_rows, forecast_meta = latest_rows_by_city_date(first_existing(FORECAST_ENRICHMENT_LATEST_CANDIDATES))
    enriched: list[dict[str, Any]] = []
    counters = Counter()
    for item in state_df.to_dict("records"):
        row = dict(item)
        city = market_city_key(row.get("city"))
        target_date = safe_str(row.get("target_date"))
        key = (city, target_date)
        unit = safe_str(row.get("unit")).upper()
        snapshot_dt = parse_utc(row.get("decision_snapshot_ts_utc"))
        running_native = to_float(row.get("running_native"), math.nan)
        current_native = to_float(row.get("current_native"), math.nan)
        d1_interval = p0._interval(row.get("d1_no_bracket"))
        d2_interval = p0._interval(row.get("d2_no_bracket"))

        hf = hf_rows.get(key)
        if hf:
            hf_temp = native_temp_from_source(hf, unit)
            hf_round = arith_round(hf_temp)
            running_round = arith_round(running_native)
            detect_dt = parse_utc(hf.get("local_detect_ts_utc") or hf.get("fetched_at_utc"))
            obs_dt = parse_utc(hf.get("observation_time_utc"))
            relation = source_relation_to_snapshot(detect_dt, snapshot_dt)
            counters[f"hf_{relation}"] += 1
            row.update(
                {
                    "high_freq_context_status": "ok",
                    "high_freq_source": safe_str(hf.get("source")),
                    "high_freq_source_kind": safe_str(hf.get("source_kind")),
                    "high_freq_source_status": safe_str(hf.get("source_status")),
                    "high_freq_station": safe_str(hf.get("station")),
                    "high_freq_detect_ts_utc": detect_dt.isoformat() if detect_dt else "",
                    "high_freq_obs_ts_utc": obs_dt.isoformat() if obs_dt else "",
                    "high_freq_relation_to_snapshot": relation,
                    "high_freq_obs_age_min_at_snapshot": (snapshot_dt - obs_dt).total_seconds() / 60.0 if snapshot_dt and obs_dt else math.nan,
                    "high_freq_detect_lag_min_vs_snapshot": (detect_dt - snapshot_dt).total_seconds() / 60.0 if snapshot_dt and detect_dt else math.nan,
                    "high_freq_temp_native": hf_temp,
                    "high_freq_temp_round_native": hf_round,
                    "high_freq_minus_current_native": hf_temp - current_native if math.isfinite(hf_temp) and math.isfinite(current_native) else math.nan,
                    "high_freq_minus_running_native": hf_temp - running_native if math.isfinite(hf_temp) and math.isfinite(running_native) else math.nan,
                    "high_freq_round_minus_running_round": hf_round - running_round if hf_round is not None and running_round is not None else math.nan,
                    "high_freq_implies_up": bool(hf_round is not None and running_round is not None and hf_round > running_round),
                    "high_freq_implies_d1_cross": bool(hf_round is not None and d1_interval is not None and hf_round >= d1_interval[0]),
                    "high_freq_implies_d2_cross": bool(hf_round is not None and d2_interval is not None and hf_round >= d2_interval[0]),
                }
            )
        else:
            counters["hf_missing"] += 1
            row.update({"high_freq_context_status": "missing", "high_freq_source": "", "high_freq_relation_to_snapshot": "missing"})

        src = source_rows.get(key)
        if src:
            src_temp = native_temp_from_source(src, unit)
            src_detect = parse_utc(src.get("local_detect_ts_utc") or src.get("source_fetch_end_utc") or src.get("ts_utc"))
            src_report = parse_utc(src.get("source_report_ts_utc") or src.get("ts_utc"))
            relation = source_relation_to_snapshot(src_detect, snapshot_dt)
            counters[f"source_event_{relation}"] += 1
            row.update(
                {
                    "source_event_context_status": "ok",
                    "source_event_source": safe_str(src.get("source") or src.get("live_observation_source")),
                    "source_event_station": safe_str(src.get("station")),
                    "source_event_changed_since_last": bool(src.get("changed_since_last")),
                    "source_event_detect_ts_utc": src_detect.isoformat() if src_detect else "",
                    "source_event_report_ts_utc": src_report.isoformat() if src_report else "",
                    "source_event_relation_to_snapshot": relation,
                    "source_event_temp_native": src_temp,
                    "source_event_temp_round_native": arith_round(src_temp),
                    "source_event_age_min_at_snapshot": (snapshot_dt - src_report).total_seconds() / 60.0 if snapshot_dt and src_report else math.nan,
                }
            )
        else:
            counters["source_event_missing"] += 1
            row.update({"source_event_context_status": "missing", "source_event_source": "", "source_event_relation_to_snapshot": "missing"})

        forecast = forecast_rows.get(key)
        if forecast:
            forecast_dt = parse_utc(forecast.get("snapshot_ts_utc") or forecast.get("generated_at_utc"))
            relation = source_relation_to_snapshot(forecast_dt, snapshot_dt)
            gfs_max = forecast_model_max_native(forecast, "GFS", unit)
            ecmwf_max = forecast_model_max_native(forecast, "ECMWF", unit)
            row.update(
                {
                    "forecast_enrichment_status": safe_str(forecast.get("status")) or "ok",
                    "forecast_enrichment_snapshot_ts_utc": forecast_dt.isoformat() if forecast_dt else "",
                    "forecast_enrichment_relation_to_snapshot": relation,
                    "gfs_forecast_max_native": gfs_max,
                    "ecmwf_forecast_max_native": ecmwf_max,
                    "gfs_gap_to_running_native": gfs_max - running_native if math.isfinite(gfs_max) else math.nan,
                    "ecmwf_gap_to_running_native": ecmwf_max - running_native if math.isfinite(ecmwf_max) else math.nan,
                }
            )
            counters[f"forecast_enrichment_{relation}"] += 1
        else:
            counters["forecast_enrichment_missing"] += 1
            row.update(
                {
                    "forecast_enrichment_status": "missing",
                    "forecast_enrichment_relation_to_snapshot": "missing",
                }
            )
        enriched.append(row)
    summary = {
        "status": "ok",
        "high_frequency": hf_meta,
        "source_events": source_meta,
        "forecast_enrichment": forecast_meta,
        "counters": dict(sorted(counters.items())),
    }
    return pd.DataFrame(enriched), summary


def sky_code_value(value: Any) -> float:
    text = safe_str(value).upper()
    if not text:
        return math.nan
    mapping = {"CLR": 0.0, "SKC": 0.0, "CAVOK": 0.0, "FEW": 1.0, "SCT": 2.0, "BKN": 3.0, "OVC": 4.0, "VV": 4.0}
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
    parsed = parse_market_bracket(safe_str(row.get("bracket")), safe_str(row.get("question")))
    if parsed is None:
        return None
    if parsed.bottom and parsed.high is not None:
        return (-math.inf, float(parsed.high) + 0.5)
    if parsed.top and parsed.low is not None:
        return (float(parsed.low) - 0.5, math.inf)
    if parsed.low is None or parsed.high is None:
        return None
    return (float(parsed.low) - 0.5, float(parsed.high) + 0.5)


def record_contains_running_value(row: dict[str, Any], running_native: float) -> bool:
    parsed = parse_market_bracket(safe_str(row.get("bracket")), safe_str(row.get("question")))
    running_value = arith_round(running_native)
    return bool(parsed is not None and running_value is not None and parsed.contains(float(running_value)))


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


def effective_yes_ask(
    direct_ask: float,
    direct_size: float,
    sibling_no_bid: float,
    sibling_no_bid_size: float,
) -> tuple[float, float, str, float]:
    choices: list[tuple[float, float, str]] = []
    if math.isfinite(direct_ask) and 0.0 < direct_ask < 1.0:
        choices.append((direct_ask, direct_size if math.isfinite(direct_size) else 0.0, "direct_yes_ask"))
    synthetic = 1.0 - sibling_no_bid if math.isfinite(sibling_no_bid) else math.nan
    if math.isfinite(synthetic) and 0.0 < synthetic < 1.0:
        choices.append(
            (
                synthetic,
                sibling_no_bid_size if math.isfinite(sibling_no_bid_size) else 0.0,
                "complement_no_bid",
            )
        )
    if not choices:
        return math.nan, 0.0, "missing", synthetic
    ask, size, source = min(choices, key=lambda item: item[0])
    return ask, size, source, synthetic


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
        for idx, (market_row, _interval) in enumerate(with_intervals):
            if record_contains_running_value(market_row, running_native):
                current_pos = idx
                break
        if current_pos is None:
            if with_intervals and running_native < with_intervals[0][1][0]:
                status = "below_market_ladder"
            elif with_intervals and running_native > with_intervals[-1][1][1]:
                status = "above_market_ladder"
            else:
                status = "cannot_map_current_bracket"
            audits.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "status": status,
                    "running_native": running_native,
                    "ladder_first_bracket": safe_str(with_intervals[0][0].get("bracket")) if with_intervals else "",
                    "ladder_last_bracket": safe_str(with_intervals[-1][0].get("bracket")) if with_intervals else "",
                }
            )
            continue
        if current_pos + 2 >= len(with_intervals):
            audits.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "status": "top_two_ladder_truncated",
                    "running_native": running_native,
                    "current_bracket": safe_str(with_intervals[current_pos][0].get("bracket")),
                    "remaining_brackets": len(with_intervals) - current_pos,
                }
            )
            continue
        current_record, current_iv = with_intervals[current_pos]
        d1_record, d1_iv = with_intervals[current_pos + 1]
        d2_record, d2_iv = with_intervals[current_pos + 2]

        current_yes_ask, current_yes_size = row_price(current_record, "yes")
        current_no_ask, current_no_size = row_price(current_record, "no")
        current_no_bid, _ = row_bid(current_record, "no")
        d1_yes_direct_ask, d1_yes_direct_size = row_price(d1_record, "yes")
        d1_yes_bid, _ = row_bid(d1_record, "yes")
        d1_no_ask, d1_no_size = row_price(d1_record, "no")
        d1_no_bid, d1_no_bid_size = row_bid(d1_record, "no")
        d1_yes_ask, d1_yes_size, d1_yes_quote_source, d1_yes_synthetic_ask = effective_yes_ask(
            d1_yes_direct_ask,
            d1_yes_direct_size,
            d1_no_bid,
            d1_no_bid_size,
        )
        d2_yes_direct_ask, d2_yes_direct_size = row_price(d2_record, "yes")
        d2_yes_bid, _ = row_bid(d2_record, "yes")
        d2_no_ask, d2_no_size = row_price(d2_record, "no")
        d2_no_bid, d2_no_bid_size = row_bid(d2_record, "no")
        d2_yes_ask, d2_yes_size, d2_yes_quote_source, d2_yes_synthetic_ask = effective_yes_ask(
            d2_yes_direct_ask,
            d2_yes_direct_size,
            d2_no_bid,
            d2_no_bid_size,
        )
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
        source_lower = source.lower()
        gfs_gap = forecast_gap if "gfs" in source_lower else math.nan
        ecmwf_gap = forecast_gap if "ecmwf" in source_lower else math.nan

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
            "d1_no_bid_size": d1_no_bid_size,
            "d1_yes_ask": d1_yes_ask,
            "d1_yes_ask_size": d1_yes_size,
            "d1_yes_ask_direct": d1_yes_direct_ask,
            "d1_yes_ask_synthetic": d1_yes_synthetic_ask,
            "d1_yes_quote_source": d1_yes_quote_source,
            "d1_yes_bid": d1_yes_bid,
            "d2_no_ask": d2_no_ask,
            "d2_no_ask_size": d2_no_size,
            "d2_no_bid": d2_no_bid,
            "d2_no_bid_size": d2_no_bid_size,
            "d2_yes_ask": d2_yes_ask,
            "d2_yes_ask_size": d2_yes_size,
            "d2_yes_ask_direct": d2_yes_direct_ask,
            "d2_yes_ask_synthetic": d2_yes_synthetic_ask,
            "d2_yes_quote_source": d2_yes_quote_source,
            "d2_yes_bid": d2_yes_bid,
            "current_question": safe_str(current_record.get("question")),
            "d1_question": safe_str(d1_record.get("question")),
            "d2_question": safe_str(d2_record.get("question")),
            "current_yes_token_id": safe_str(current_record.get("yes_token_id")),
            "current_no_token_id": safe_str(current_record.get("no_token_id")),
            "d1_yes_token_id": safe_str(d1_record.get("yes_token_id")),
            "d1_no_token_id": safe_str(d1_record.get("no_token_id")),
            "d2_yes_token_id": safe_str(d2_record.get("yes_token_id")),
            "d2_no_token_id": safe_str(d2_record.get("no_token_id")),
            "current_market_id": safe_str(current_record.get("market_id")),
            "d1_market_id": safe_str(d1_record.get("market_id")),
            "d2_market_id": safe_str(d2_record.get("market_id")),
            "current_event_slug": safe_str(current_record.get("event_slug") or current_record.get("market_slug")),
            "d1_event_slug": safe_str(d1_record.get("event_slug") or d1_record.get("market_slug")),
            "d2_event_slug": safe_str(d2_record.get("event_slug") or d2_record.get("market_slug")),
            "event_slug": safe_str(current_record.get("event_slug") or current_record.get("market_slug")),
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
    out = regime_policy.add_regime_labels(out)
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
    if expression == "d1_yes":
        return (
            float(row["d1_yes_ask"]),
            to_float(row.get("d1_yes_ask_size"), 0.0),
            safe_str(row.get("d1_yes_token_id")),
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
    if expression == "d2_yes":
        return (
            float(row["d2_yes_ask"]),
            to_float(row.get("d2_yes_ask_size"), 0.0),
            safe_str(row.get("d2_yes_token_id")),
            safe_str(row.get("d2_market_id")),
            safe_str(row.get("d2_no_bracket")),
        )
    raise ValueError(expression)


def expression_pricing_context(row: pd.Series, expression: str) -> dict[str, Any]:
    if expression == "d1_yes":
        return {
            "snapshot_quote_source": safe_str(row.get("d1_yes_quote_source")),
            "snapshot_direct_ask": finite_or_none(row.get("d1_yes_ask_direct")),
            "snapshot_synthetic_ask": finite_or_none(row.get("d1_yes_ask_synthetic")),
            "sibling_no_token_id": safe_str(row.get("d1_no_token_id")),
        }
    if expression == "d2_yes":
        return {
            "snapshot_quote_source": safe_str(row.get("d2_yes_quote_source")),
            "snapshot_direct_ask": finite_or_none(row.get("d2_yes_ask_direct")),
            "snapshot_synthetic_ask": finite_or_none(row.get("d2_yes_ask_synthetic")),
            "sibling_no_token_id": safe_str(row.get("d2_no_token_id")),
        }
    return {
        "snapshot_quote_source": "direct_token_ask",
        "snapshot_direct_ask": finite_or_none(ask_and_token(row, expression)[0]),
        "snapshot_synthetic_ask": None,
        "sibling_no_token_id": "",
    }


def expression_question(row: pd.Series, expression: str) -> str:
    if expression in {"current_yes", "current_no"}:
        return safe_str(row.get("current_question") or row.get("question"))
    if expression in {"d1_no", "d1_yes"}:
        return safe_str(row.get("d1_question") or row.get("question"))
    if expression in {"d2_no", "d2_yes"}:
        return safe_str(row.get("d2_question") or row.get("question"))
    raise ValueError(expression)


def expression_event_slug(row: pd.Series, expression: str) -> str:
    if expression in {"current_yes", "current_no"}:
        return safe_str(row.get("current_event_slug") or row.get("event_slug") or row.get("market_slug"))
    if expression in {"d1_no", "d1_yes"}:
        return safe_str(row.get("d1_event_slug") or row.get("event_slug") or row.get("market_slug"))
    if expression in {"d2_no", "d2_yes"}:
        return safe_str(row.get("d2_event_slug") or row.get("event_slug") or row.get("market_slug"))
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
    if expression == "d1_yes":
        return p_d1
    if expression == "d2_no":
        return 1.0 - p_d2
    if expression == "d2_yes":
        return p_d2
    raise ValueError(expression)


def probability_distribution_fields(item: dict[str, Any]) -> dict[str, Any]:
    """Return auditable four-bucket distribution fields for tmax decisions."""

    buckets = p0.BUCKETS

    def bucket_value(prefix: str, bucket: str) -> float:
        base = f"{prefix}_p_{bucket}"
        for key in [base, f"{base}_y", f"{base}_x"]:
            value = to_float(item.get(key), math.nan)
            if math.isfinite(value):
                return value
        return math.nan

    def collect(prefix: str) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for bucket in buckets:
            value = bucket_value(prefix, bucket)
            out[bucket] = round(value, 6) if math.isfinite(value) else None
        return out

    market = collect("market")
    raw_model = collect(f"{MODEL_SPEC}_model")
    blended = collect(MODEL_METHOD)
    fields: dict[str, Any] = {
        "tmax_probability_bucket_schema": "current_d1_d2_tail_v1",
        "tmax_probability_model_spec": MODEL_SPEC,
        "tmax_probability_model_method": MODEL_METHOD,
        "tmax_market_p_current": market["current"],
        "tmax_market_p_d1": market["d1"],
        "tmax_market_p_d2": market["d2"],
        "tmax_market_p_tail": market["tail"],
        "tmax_raw_model_p_current": raw_model["current"],
        "tmax_raw_model_p_d1": raw_model["d1"],
        "tmax_raw_model_p_d2": raw_model["d2"],
        "tmax_raw_model_p_tail": raw_model["tail"],
        "tmax_blend_p_current": blended["current"],
        "tmax_blend_p_d1": blended["d1"],
        "tmax_blend_p_d2": blended["d2"],
        "tmax_blend_p_tail": blended["tail"],
        "tmax_distribution": {
            "schema": "current_d1_d2_tail_v1",
            "bucket_meaning": {
                "current": safe_str(item.get("current_bracket")),
                "d1": safe_str(item.get("d1_no_bracket")),
                "d2": safe_str(item.get("d2_no_bracket")),
                "tail": f">{safe_str(item.get('d2_no_bracket'))}",
            },
            "market": market,
            "raw_model": raw_model,
            "blended": blended,
            "selected_method": MODEL_METHOD,
        },
    }
    return fields


def build_candidates(live_df: pd.DataFrame, pred: pd.DataFrame, args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    keys = ["city", "target_date", "decision_hour_local", "actual_bucket"]
    df = live_df.merge(pred, on=keys, how="inner", validate="one_to_one")
    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    active_expressions = set(args.active_expressions)
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
        for expression in sorted(set(p2.EXPRESSIONS) | {"d1_yes", "d2_yes"}):
            if expression not in active_expressions:
                blocked.append(
                    {
                        **candidate_base(item),
                        "chosen_expression": expression,
                        "decision_status": "blocked",
                        "block_reason": "expression_not_active",
                    }
                )
                continue
            ask, ask_size, token_id, market_id, bracket = ask_and_token(row, expression)
            p_win = win_prob(row, expression)
            pricing_context = expression_pricing_context(row, expression)
            gross_edge = p_win - ask
            fee = taker_fee(ask, args.fee_rate)
            fee_adjusted_edge = gross_edge - fee
            question = expression_question(row, expression)
            event_slug = expression_event_slug(row, expression)
            base = {
                **candidate_base(item),
                "policy_id": args.policy_id,
                "event_slug": event_slug,
                "market_slug": event_slug,
                "question": question,
                "chosen_expression": expression,
                "signal_side": "BUY_YES" if expression.endswith("_yes") else "BUY_NO",
                "ask": ask,
                "ask_size": ask_size,
                "p_win": p_win,
                "gross_edge": gross_edge,
                "taker_fee": fee,
                "fee_adjusted_edge": fee_adjusted_edge,
                "model_edge": fee_adjusted_edge,
                "gross_roi_model": gross_edge / ask if ask > 0 else None,
                "fee_adjusted_roi_model": fee_adjusted_edge / ask if ask > 0 else None,
                "model_roi": fee_adjusted_edge / ask if ask > 0 else None,
                "token_id": token_id,
                "market_id": market_id,
                "bracket": bracket,
                **pricing_context,
            }
            reason = ""
            if not token_id:
                reason = "missing_token_id"
            elif not math.isfinite(ask):
                reason = "missing_expression_ask"
            elif not math.isfinite(p_win):
                reason = "missing_expression_probability"
            elif ask < args.ask_floor:
                reason = "below_ask_floor"
            elif ask > args.ask_ceiling:
                reason = "above_ask_ceiling"
            elif fee_adjusted_edge < args.edge_threshold:
                reason = "below_edge_threshold"
            if reason:
                blocked.append({**base, "decision_status": "blocked", "block_reason": reason})
                continue
            rank = (fee_adjusted_edge, to_float(base.get("model_roi"), -math.inf))
            best_rank = (
                to_float(best.get("fee_adjusted_edge"), -math.inf),
                to_float(best.get("model_roi"), -math.inf),
            ) if best is not None else (-math.inf, -math.inf)
            if best is None or rank > best_rank:
                best = base
        if best is None:
            continue
        best["policy_id"] = args.policy_id
        best["decision_status"] = "candidate_selected_pre_fresh_book"
        combo = f"{best['chosen_expression']}_edge02"
        best["candidate_id"] = stable_hash(
            {
                "strategy_id": STRATEGY_ID,
                "city": best["city"],
                "target_date": best["target_date"],
                "combo": combo,
                "bracket": best["bracket"],
                "token_id": best["token_id"],
                "signal_side": best["signal_side"],
                "order_side": "BUY",
            }
        )
        best["city_day_position_key"] = city_day_position_key(best)
        best["opportunity_id"] = best["city_day_position_key"]
        candidates.append(best)
    return candidates, blocked


def candidate_base(item: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "city",
        "target_date",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "event_slug",
        "market_slug",
        "question",
        "unit",
        "forecast_source",
        "forecast_max_native",
        "gfs_forecast_max_native",
        "ecmwf_forecast_max_native",
        "gfs_gap_to_running_native",
        "ecmwf_gap_to_running_native",
        "forecast_enrichment_status",
        "forecast_enrichment_snapshot_ts_utc",
        "forecast_enrichment_relation_to_snapshot",
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
        "high_freq_context_status",
        "high_freq_source",
        "high_freq_source_kind",
        "high_freq_source_status",
        "high_freq_station",
        "high_freq_detect_ts_utc",
        "high_freq_obs_ts_utc",
        "high_freq_relation_to_snapshot",
        "high_freq_obs_age_min_at_snapshot",
        "high_freq_detect_lag_min_vs_snapshot",
        "high_freq_temp_native",
        "high_freq_temp_round_native",
        "high_freq_minus_current_native",
        "high_freq_minus_running_native",
        "high_freq_round_minus_running_round",
        "high_freq_implies_up",
        "high_freq_implies_d1_cross",
        "high_freq_implies_d2_cross",
        "source_event_context_status",
        "source_event_source",
        "source_event_station",
        "source_event_changed_since_last",
        "source_event_detect_ts_utc",
        "source_event_report_ts_utc",
        "source_event_relation_to_snapshot",
        "source_event_temp_native",
        "source_event_temp_round_native",
        "source_event_age_min_at_snapshot",
    ]
    out = {k: item.get(k) for k in fields if k in item}
    out.update(
        {
            "record_type": "tmax_distribution_edge_candidate_event",
            "strategy_instance": STRATEGY_INSTANCE,
            "strategy_id": STRATEGY_ID,
            "strategy_family": STRATEGY_FAMILY,
            "policy_id": safe_str(item.get("policy_id")) or "",
            "model_method": MODEL_METHOD,
            "zero_notional": True,
            "no_order_placed": True,
            "city_day_position_key": city_day_position_key(item),
        }
    )
    out.update(probability_distribution_fields(item))
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


def fetch_book_with_curl(token_id: str, *, timeout_sec: float) -> dict[str, Any]:
    proc = subprocess.run(
        [
            "curl",
            "-fsS",
            "--max-time",
            str(max(1.0, float(timeout_sec))),
            "--get",
            CLOB_BOOK_API,
            "--data-urlencode",
            f"token_id={token_id}",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl book fetch failed rc={proc.returncode}: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout)
    if not isinstance(data, dict):
        raise RuntimeError("curl CLOB book response is not an object")
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


def fetch_book_resilient(token_id: str, args: argparse.Namespace) -> dict[str, Any]:
    try:
        return fetch_book(token_id, timeout_sec=args.clob_timeout_sec, retries=args.clob_retries)
    except Exception as exc:  # noqa: BLE001
        httpx_error = f"{type(exc).__name__}: {exc}"
        try:
            return fetch_book_with_curl(token_id, timeout_sec=args.clob_timeout_sec)
        except Exception as curl_exc:  # noqa: BLE001
            raise RuntimeError(
                f"httpx={httpx_error}; curl={type(curl_exc).__name__}: {curl_exc}"
            ) from curl_exc


def fresh_quote(candidate: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    try:
        book = fetch_book_resilient(str(candidate["token_id"]), args)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "rejected",
            "reason": "fresh_book_fetch_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    asks = book_levels(book, "ask")
    bids = book_levels(book, "bid")
    ask_choices: list[tuple[float, float, str]] = []
    bid_choices: list[tuple[float, str]] = []
    if asks:
        ask_choices.append((asks[0][0], asks[0][1], "direct_token_ask"))
    if bids:
        bid_choices.append((bids[0][0], "direct_token_bid"))

    sibling_error = ""
    fresh_synthetic_ask = None
    sibling_no_token_id = safe_str(candidate.get("sibling_no_token_id"))
    if safe_str(candidate.get("signal_side")) == "BUY_YES" and sibling_no_token_id:
        try:
            sibling_book = fetch_book_resilient(sibling_no_token_id, args)
            sibling_bids = book_levels(sibling_book, "bid")
            sibling_asks = book_levels(sibling_book, "ask")
            if sibling_bids:
                fresh_synthetic_ask = 1.0 - sibling_bids[0][0]
            if sibling_asks:
                bid_choices.append((1.0 - sibling_asks[0][0], "complement_no_ask"))
        except Exception as exc:  # noqa: BLE001
            sibling_error = f"{type(exc).__name__}: {exc}"

    ask_choices = [item for item in ask_choices if math.isfinite(item[0]) and 0.0 < item[0] < 1.0]
    if not ask_choices:
        return {
            "status": "rejected",
            "reason": "fresh_book_no_effective_ask",
            "best_bid": max((item[0] for item in bid_choices), default=0.0),
            "fresh_synthetic_ask": fresh_synthetic_ask,
            "sibling_book_error": sibling_error,
        }
    fresh_ask, fresh_size, fresh_quote_source = min(ask_choices, key=lambda item: item[0])
    best_bid = max((item[0] for item in bid_choices), default=0.0)
    p_win = float(candidate["p_win"])
    snapshot_ask = float(candidate["ask"])
    fee = taker_fee(fresh_ask, args.fee_rate)
    fee_adjusted_edge = p_win - fresh_ask - fee
    if fresh_size + 1e-9 < args.fixed_shares:
        return {
            "status": "rejected",
            "reason": "fresh_ask_size_below_fixed_shares",
            "best_bid": best_bid,
            "fresh_ask": fresh_ask,
            "fresh_ask_size": fresh_size,
            "fresh_quote_source": fresh_quote_source,
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
        "fresh_quote_source": fresh_quote_source,
        "fresh_direct_ask": asks[0][0] if asks else None,
        "fresh_synthetic_ask": fresh_synthetic_ask,
        "sibling_book_error": sibling_error,
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
        "opportunity_id": candidate.get("opportunity_id") or candidate["candidate_id"],
        "city_day_position_key": candidate.get("city_day_position_key") or city_day_position_key(candidate),
        "strategy": "weather_edge_v1",
        "strategy_instance": STRATEGY_INSTANCE,
        "source_strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "strategy_family": STRATEGY_FAMILY,
        "policy_id": args.policy_id,
        "active_expressions": list(args.active_expressions),
        "probability_source": MODEL_METHOD,
        "decision_mode": args.policy_id,
        "execution_mode": "fresh_book_guarded_taker",
        "profile": args.policy_id,
        "combo": f"{candidate['chosen_expression']}_edge02",
        "city": candidate["city"],
        "city_pool": "tmax_distribution_edge",
        "target_date": candidate["target_date"],
        "market_id": candidate["market_id"],
        "event_slug": candidate.get("event_slug", ""),
        "market_slug": candidate.get("market_slug") or candidate.get("event_slug", ""),
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
        "quote_fee_adjusted_edge": round(float(quote.get("fee_adjusted_edge") or 0.0), 6),
        "required_quote_edge": round(float(args.edge_threshold), 6),
        "model_token_probability": round(float(candidate["p_win"]), 6),
        "tmax_probability_bucket_schema": candidate.get("tmax_probability_bucket_schema"),
        "tmax_probability_model_spec": candidate.get("tmax_probability_model_spec"),
        "tmax_probability_model_method": candidate.get("tmax_probability_model_method"),
        "tmax_market_p_current": candidate.get("tmax_market_p_current"),
        "tmax_market_p_d1": candidate.get("tmax_market_p_d1"),
        "tmax_market_p_d2": candidate.get("tmax_market_p_d2"),
        "tmax_market_p_tail": candidate.get("tmax_market_p_tail"),
        "tmax_raw_model_p_current": candidate.get("tmax_raw_model_p_current"),
        "tmax_raw_model_p_d1": candidate.get("tmax_raw_model_p_d1"),
        "tmax_raw_model_p_d2": candidate.get("tmax_raw_model_p_d2"),
        "tmax_raw_model_p_tail": candidate.get("tmax_raw_model_p_tail"),
        "tmax_blend_p_current": candidate.get("tmax_blend_p_current"),
        "tmax_blend_p_d1": candidate.get("tmax_blend_p_d1"),
        "tmax_blend_p_d2": candidate.get("tmax_blend_p_d2"),
        "tmax_blend_p_tail": candidate.get("tmax_blend_p_tail"),
        "tmax_distribution": candidate.get("tmax_distribution"),
        "quote_best_bid": round(float(quote.get("best_bid") or 0.0), 6),
        "quote_best_ask": round(float(quote["fresh_ask"]), 6),
        "quote_spread": round(max(0.0, float(quote["fresh_ask"]) - float(quote.get("best_bid") or 0.0)), 6),
        "quote_tick_size": 0.01,
        "quote_mode": "fresh_book_guarded_taker",
        "snapshot_quote_source": candidate.get("snapshot_quote_source", ""),
        "snapshot_direct_ask": candidate.get("snapshot_direct_ask"),
        "snapshot_synthetic_ask": candidate.get("snapshot_synthetic_ask"),
        "fresh_quote_source": quote.get("fresh_quote_source", ""),
        "fresh_direct_ask": quote.get("fresh_direct_ask"),
        "fresh_synthetic_ask": quote.get("fresh_synthetic_ask"),
        "sibling_no_token_id": candidate.get("sibling_no_token_id", ""),
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
        "gross_edge": round(float(candidate.get("gross_edge") or 0.0), 6),
        "fee_adjusted_edge": round(float(candidate.get("fee_adjusted_edge") or candidate["model_edge"]), 6),
        "candidate_taker_fee": round(float(candidate.get("taker_fee") or 0.0), 6),
        "min_edge": round(float(args.edge_threshold), 6),
        "shadow_decision": STRATEGY_ID,
        "shadow_reason": f"{args.policy_id}_realtime_materialized_fresh_book_checked",
        "paper_enabled": True,
        "live_enabled": bool(live_enabled),
        "snapshot_ts_utc": candidate.get("decision_snapshot_ts_utc", ""),
        "source_snapshot_path": candidate.get("snapshot_path", ""),
        "high_freq_context_status": candidate.get("high_freq_context_status", ""),
        "high_freq_source": candidate.get("high_freq_source", ""),
        "high_freq_source_kind": candidate.get("high_freq_source_kind", ""),
        "high_freq_source_status": candidate.get("high_freq_source_status", ""),
        "high_freq_station": candidate.get("high_freq_station", ""),
        "high_freq_detect_ts_utc": candidate.get("high_freq_detect_ts_utc", ""),
        "high_freq_obs_ts_utc": candidate.get("high_freq_obs_ts_utc", ""),
        "high_freq_relation_to_snapshot": candidate.get("high_freq_relation_to_snapshot", ""),
        "high_freq_obs_age_min_at_snapshot": candidate.get("high_freq_obs_age_min_at_snapshot"),
        "high_freq_detect_lag_min_vs_snapshot": candidate.get("high_freq_detect_lag_min_vs_snapshot"),
        "high_freq_temp_native": candidate.get("high_freq_temp_native"),
        "high_freq_temp_round_native": candidate.get("high_freq_temp_round_native"),
        "high_freq_minus_current_native": candidate.get("high_freq_minus_current_native"),
        "high_freq_minus_running_native": candidate.get("high_freq_minus_running_native"),
        "high_freq_round_minus_running_round": candidate.get("high_freq_round_minus_running_round"),
        "high_freq_implies_up": candidate.get("high_freq_implies_up"),
        "high_freq_implies_d1_cross": candidate.get("high_freq_implies_d1_cross"),
        "high_freq_implies_d2_cross": candidate.get("high_freq_implies_d2_cross"),
        "source_event_context_status": candidate.get("source_event_context_status", ""),
        "source_event_source": candidate.get("source_event_source", ""),
        "source_event_station": candidate.get("source_event_station", ""),
        "source_event_changed_since_last": candidate.get("source_event_changed_since_last"),
        "source_event_detect_ts_utc": candidate.get("source_event_detect_ts_utc", ""),
        "source_event_report_ts_utc": candidate.get("source_event_report_ts_utc", ""),
        "source_event_relation_to_snapshot": candidate.get("source_event_relation_to_snapshot", ""),
        "source_event_temp_native": candidate.get("source_event_temp_native"),
        "source_event_temp_round_native": candidate.get("source_event_temp_round_native"),
        "source_event_age_min_at_snapshot": candidate.get("source_event_age_min_at_snapshot"),
        "decision_local_time": str(candidate.get("decision_hour_local", "")),
        "decision_timezone": "",
        "running_max_obs_utc": candidate.get("running_max_obs_utc", ""),
        "obs_age_min": finite_or_none(candidate.get("obs_age_min")),
        "minutes_since_running_max": finite_or_none(candidate.get("minutes_since_running_max")),
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
    source_context_summary: dict[str, Any] = {"status": "not_run"}
    if not state_df.empty:
        state_df, source_context_summary = enrich_source_context(state_df)
    plans: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = list(audits)
    model_meta: dict[str, Any] = {}
    if not state_df.empty:
        pred, model_meta = fit_predict_live(state_df)
        candidates, candidate_blocked = build_candidates(state_df, pred, args)
        blocked.extend(candidate_blocked)
    accepted_candidates: list[dict[str, Any]] = []
    existing_city_day_keys: set[str] = set()
    if args.first_lock_city_day:
        existing_city_day_keys.update(submitted_city_day_keys(runtime_dir / "paper_orders.jsonl", statuses={"simulated_open"}))
    if args.live:
        existing_city_day_keys.update(submitted_live_city_day_keys(runtime_dir / "live_orders.jsonl"))
    batch_city_day_keys: set[str] = set()
    for candidate in candidates:
        position_key = safe_str(candidate.get("city_day_position_key")) or city_day_position_key(candidate)
        if args.first_lock_city_day and (position_key in existing_city_day_keys or position_key in batch_city_day_keys):
            blocked.append(
                {
                    **candidate,
                    "decision_status": "blocked",
                    "block_reason": "existing_city_day_first_lock_order",
                    "city_day_position_key": position_key,
                }
            )
            continue
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
        batch_city_day_keys.add(position_key)
    accepted_sorted = sorted(
        accepted_candidates,
        key=lambda r: (str(r.get("target_date")), float(r.get("decision_hour_local") or 99), str(r.get("city"))),
    )
    max_orders = max(0, int(args.max_orders))
    for candidate in accepted_sorted[max_orders:]:
        blocked.append(
            {
                **candidate,
                "decision_status": "blocked",
                "block_reason": "max_orders_per_cycle",
            }
        )
    accepted_candidates = accepted_sorted[:max_orders]
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
    live_orders_written = 0
    if isinstance(executor_result, dict) and isinstance(executor_result.get("executor_result"), dict):
        live_orders_written = int(executor_result["executor_result"].get("live_written") or 0)
    summary = {
        "generated_at_utc": utc_now(),
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "policy_id": args.policy_id,
        "execution_mode": "live_enabled" if args.live else "paper_executor_only",
        "live_enabled": bool(args.live),
        "live_orders_written": live_orders_written,
        "no_live_order_placed": live_orders_written == 0,
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
        "first_lock_city_day": bool(args.first_lock_city_day),
        "active_expressions": list(args.active_expressions),
        "model_meta": model_meta,
        "source_context": source_context_summary,
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
    global STRATEGY_INSTANCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "loop"], nargs="?", default="run")
    parser.add_argument("--runtime-dir", default=str(RUNTIME_DEFAULT))
    parser.add_argument("--strategy-instance", default=STRATEGY_INSTANCE)
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--snapshot-dir", default="")
    parser.add_argument("--observation-cache", default="")
    parser.add_argument("--max-snapshot-age-min", type=float, default=60.0)
    parser.add_argument("--edge-threshold", type=float, default=0.02)
    parser.add_argument("--ask-floor", type=float, default=0.20)
    parser.add_argument("--ask-ceiling", type=float, default=0.99)
    parser.add_argument("--policy-id", default="tmax_distribution_edge_clean_edge02")
    parser.add_argument("--active-expression", action="append", dest="active_expression", default=[])
    parser.add_argument("--first-lock-city-day", action="store_true")
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
    args.active_expressions = parse_active_expressions(args.active_expression)
    STRATEGY_INSTANCE = safe_str(args.strategy_instance) or STRATEGY_INSTANCE
    return args


def parse_active_expressions(values: list[str]) -> list[str]:
    allowed = set(p2.EXPRESSIONS) | {"d1_yes", "d2_yes"}
    if not values:
        return list(p2.EXPRESSIONS)
    out: list[str] = []
    for value in values:
        for part in str(value).replace(",", " ").split():
            expr = part.strip()
            if not expr:
                continue
            if expr not in allowed:
                raise SystemExit(f"unknown active expression: {expr}; allowed={sorted(allowed)}")
            if expr not in out:
                out.append(expr)
    if not out:
        raise SystemExit("at least one active expression is required")
    return out


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
