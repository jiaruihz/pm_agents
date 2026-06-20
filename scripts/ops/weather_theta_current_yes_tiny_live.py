#!/usr/bin/env python3
"""Tiny-live runner for the no-reheat current-bucket YES rule.

This is an independent live branch for the frozen v9 rule:

  BUY_YES current running-max bracket
  local hour 13-15, decline_c >= 0.5
  yes_ask >= 0.55, p_yes_win >= 0.5, p_yes_win - yes_ask >= 0.05
  fresh CLOB ask rechecked before execution, fresh_ask <= snapshot_ask + 0.02
  d1 NO sibling quote visible
  $3/order and $3/city-day cap

It writes standard weather_edge_trade_plan JSONL rows and can hand them to the
existing weather_order_executor.  Default mode is plan-only; live submission
requires both --live and --confirm-live.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import signal
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.current_yes_model import (
    DEFAULT_FADE_GATE,
    FadeGateSpec,
    fade_gate_reject_reason,
    score_artifact as score_rows,
)
from src.strategies.weather_edge_v1.tools.execution_pipeline import read_jsonl, stable_hash
from src.strategies.weather_edge_v1.tools.live_state import read_live_state
from weather_data_feed import (
    ObservationClockConfig,
    bracket_contains,
    city_timezone_name,
    index_observation_cache,
    load_observation_cache,
    load_source_profiles,
    observation_clock_guard,
    parse_label_dict,
    station_timezone,
    timezone_label,
)
from weather_data_feed.observation_sources import build_iem_local_day_params, parse_iem_asos_records


STRATEGY_INSTANCE = os.environ.get("THETA_CURRENT_YES_STRATEGY_INSTANCE", "theta_current_yes_tiny_live_v1")
STRATEGY_ID = "theta_current_yes_no_reheat_live5_v1"
LEGACY_SHARED_STRATEGY_INSTANCE = "theta_current_yes_tiny_live_v1"
TRAIN_FEATURES = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
MODEL_ARTIFACT = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
FADE_CONFIRMED_MODEL_ARTIFACT = ROOT / "docs/analysis/2026-06/generated/theta_current_yes_fade_confirmed_model_v1/fade_confirmed_model.json"
STATION_SUMMARY = ROOT / "docs/analysis/2026-06/generated/theta_no_wu_obs_patch_v1/summary.json"
RUNTIME_DIR = ROOT / os.environ.get("THETA_CURRENT_YES_RUNTIME_DIR", "runtime/weather_edge_v1/theta_current_yes_tiny_live_v1")
PLAN_OUT = RUNTIME_DIR / "trade_plans.jsonl"
SUMMARY_OUT = RUNTIME_DIR / "latest_summary.json"
HISTORY_OUT = RUNTIME_DIR / "summary_history.jsonl"
PAPER_OUT = RUNTIME_DIR / "paper_orders.jsonl"
FORWARD_TELEMETRY_OUT = RUNTIME_DIR / "forward_telemetry.jsonl"
LIVE_OUT = ROOT / "runtime/weather_edge_v1/live" / f"{STRATEGY_INSTANCE}_orders.jsonl"

METAR_API = "https://aviationweather.gov/api/data/metar"
IEM_ASOS_API = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
CLOB_BOOK_API = "https://clob.polymarket.com/book"
FORECAST_PEAK_CACHE_DIR = RUNTIME_DIR / "forecast_peak_cache"
HTTP_TIMEOUT_SEC = float(os.environ.get("THETA_CURRENT_YES_HTTP_TIMEOUT_SEC", "4"))
HTTP_FETCH_BUDGET_SEC = float(os.environ.get("THETA_CURRENT_YES_HTTP_FETCH_BUDGET_SEC", "8"))
FORECAST_PEAK_FETCH_BUDGET_SEC = float(os.environ.get("THETA_CURRENT_YES_FORECAST_PEAK_FETCH_BUDGET_SEC", "6"))
OBS_STALE_CADENCE_GRACE_MIN = 15.0
OBS_MAX_DYNAMIC_AGE_MIN = 90.0
METAR_LIKE_OBSERVATION_CACHE_SOURCES = {
    "aviationweather_metar",
    "aviationweather_cache_csv",
    "noaa_tgftp_station_txt",
    "weather_gov_latest",
}
OBSERVATION_CACHE_FALLBACK_STATUSES = {
    "observation_cache_missing",
    "observation_cache_not_ok",
    "fetch_failed",
    "empty",
    "missing_temp",
    "observation_cache_bad_ts",
    "observation_cache_bad_temp",
}

OPEN_METEO_MODEL_BY_SOURCE = {
    "open_meteo_live_gfs": "gfs",
    "open_meteo_live_ecmwf": "ecmwf",
    "gfs": "gfs",
    "ecmwf": "ecmwf",
}

CITY_COORDS = {
    "Amsterdam": (52.31, 4.76),
    "Ankara": (40.13, 32.99),
    "Atlanta": (33.64, -84.43),
    "Austin": (30.19, -97.67),
    "Beijing": (40.08, 116.58),
    "BuenosAires": (-34.82, -58.53),
    "Busan": (35.18, 128.94),
    "CapeTown": (-33.96, 18.6),
    "Chengdu": (30.58, 103.95),
    "Chicago": (41.98, -87.91),
    "Chongqing": (29.72, 106.64),
    "Dallas": (32.85, -96.85),
    "Denver": (39.72, -104.75),
    "Guangzhou": (23.39, 113.3),
    "Helsinki": (60.32, 24.97),
    "Houston": (29.65, -95.28),
    "Istanbul": (41.26, 28.74),
    "Jeddah": (21.68, 39.16),
    "Karachi": (24.91, 67.16),
    "KualaLumpur": (2.75, 101.71),
    "LA": (33.94, -118.41),
    "London": (51.5, 0.05),
    "Lucknow": (26.76, 80.89),
    "Madrid": (40.47, -3.56),
    "Manila": (14.51, 121.02),
    "MexicoCity": (19.44, -99.07),
    "Miami": (25.8, -80.29),
    "Milan": (45.63, 8.73),
    "Munich": (48.35, 11.79),
    "NYC": (40.78, -73.87),
    "PanamaCity": (8.97, -79.56),
    "Paris": (48.97, 2.44),
    "SanFrancisco": (37.62, -122.38),
    "SaoPaulo": (-23.43, -46.47),
    "Seattle": (47.45, -122.31),
    "Shanghai": (31.14, 121.81),
    "Singapore": (1.36, 103.99),
    "Taipei": (25.07, 121.55),
    "TelAviv": (32.01, 34.89),
    "Tokyo": (35.55, 139.78),
    "Warsaw": (52.17, 20.97),
    "Wellington": (-41.33, 174.81),
    "Wuhan": (30.78, 114.21),
}

BASE_FEATURES = [
    "decision_hour_local",
    "month",
    "decline_c",
    "decline_native",
    "decline_band",
    "gap_running_to_d1_low_native",
    "gap_current_to_d1_low_native",
    "running_value",
    "current_native",
    "running_native",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relh_now",
    "sknt_now",
    "sky_now",
    "d_tmpf_1h",
    "d_tmpf_3h",
    "d_dwpf_3h",
    "d_relh_3h",
]
PRICE_FEATURES = ["yes_current_ask", "log_yes_size", "d1_no_ask", "ask_gap_d1_no_minus_yes"]
CAT_FEATURES = ["city", "unit"]
MODEL_FEATURES = BASE_FEATURES + PRICE_FEATURES + CAT_FEATURES

SKY_CODE = {"CLR": 0, "SKC": 0, "NSC": 0, "NCD": 0, "CAVOK": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "VV": 4}
MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


@dataclass(frozen=True)
class Station:
    city: str
    icao: str
    unit: str
    utc_offset: int
    timezone_name: str | None = None


def fade_gate_spec_from_args(args: argparse.Namespace) -> FadeGateSpec:
    return FadeGateSpec(
        min_decline_c=float(getattr(args, "fade_confirmed_min_decline_c", DEFAULT_FADE_GATE.min_decline_c)),
        min_ask=float(getattr(args, "fade_confirmed_min_ask", DEFAULT_FADE_GATE.min_ask)),
        min_p_yes=float(getattr(args, "fade_confirmed_min_p", DEFAULT_FADE_GATE.min_p_yes)),
        min_edge=float(getattr(args, "fade_confirmed_min_edge", DEFAULT_FADE_GATE.min_edge)),
        min_local_hour=int(getattr(args, "min_local_hour", DEFAULT_FADE_GATE.min_local_hour)),
        max_local_hour=int(getattr(args, "max_local_hour", DEFAULT_FADE_GATE.max_local_hour)),
    )


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def parse_env_value(raw_value: str) -> str:
    try:
        return shlex.split(raw_value, comments=False, posix=True)[0] if raw_value.strip() else ""
    except Exception:
        return raw_value.strip().strip("\"'")


def proxy_candidates() -> list[str | None]:
    candidates: list[str | None] = []
    for key in ("WEATHER_PREDICT_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        value = os.environ.get(key)
        if value:
            candidates.append(value)
    env_path = Path(os.environ.get("WEATHER_PREDICT_DIR", "/home/jiarui/projects/weather-predict")) / ".env"
    if env_path.exists():
        values: dict[str, str] = {}
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if key in {"WEATHER_PREDICT_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"}:
                values[key] = parse_env_value(raw_value)
        for key in ("WEATHER_PREDICT_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
            if values.get(key):
                candidates.append(values[key])
    candidates.extend(["http://127.0.0.1:7897", "http://127.0.0.1:7890", None])
    out: list[str | None] = []
    for item in candidates:
        if item not in out:
            out.append(item)
    return out


PROXIES = proxy_candidates()


def _raise_http_fetch_timeout(_signum: int, _frame: Any) -> None:
    raise TimeoutError("http fetch deadline exceeded")


def http_get_with_deadline(url: str, *, params: Any, proxy: str | None, timeout_sec: float) -> httpx.Response:
    old_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_http_fetch_timeout)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, timeout_sec))
    try:
        timeout = httpx.Timeout(timeout_sec, connect=timeout_sec, read=timeout_sec, write=timeout_sec, pool=timeout_sec)
        return httpx.get(url, params=params, proxy=proxy, timeout=timeout)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def fetch_json(
    url: str,
    params: dict | None = None,
    max_rounds: int = 1,
    *,
    timeout_sec: float = HTTP_TIMEOUT_SEC,
    budget_sec: float = HTTP_FETCH_BUDGET_SEC,
) -> Any:
    last = None
    started = time.monotonic()
    for rnd in range(max_rounds):
        for proxy in PROXIES:
            remaining = budget_sec - (time.monotonic() - started)
            if remaining <= 0:
                raise RuntimeError(f"fetch timed out {url}: {last}")
            try:
                r = http_get_with_deadline(url, params=params, proxy=proxy, timeout_sec=min(timeout_sec, remaining))
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # noqa: BLE001
                last = f"{proxy}: {type(exc).__name__}: {exc}"
                if budget_sec - (time.monotonic() - started) <= 0:
                    raise RuntimeError(f"fetch timed out {url}: {last}")
                time.sleep(min(0.4 + rnd, max(0.0, budget_sec - (time.monotonic() - started))))
    raise RuntimeError(f"fetch failed {url}: {last}")


def forecast_values_hash(times: list[Any], temps: list[Any]) -> str:
    payload = [
        [str(ts), None if temp is None else round(float(temp), 3)]
        for ts, temp in zip(times or [], temps or [], strict=False)
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def forecast_details_from_open_meteo(payload: dict[str, Any], *, source_model: str) -> dict[str, Any] | None:
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    times = hourly.get("time", []) or []
    temps = hourly.get("temperature_2m", []) or []
    pairs: list[tuple[str, float]] = []
    for ts, temp in zip(times, temps, strict=False):
        if temp is None:
            continue
        try:
            pairs.append((str(ts), float(temp)))
        except Exception:
            continue
    if not pairs:
        return None
    max_f = max(temp for _, temp in pairs)
    peak_local_time = min(ts for ts, temp in pairs if abs(temp - max_f) < 1e-9)
    utc_offset_seconds = int(payload.get("utc_offset_seconds") or 0)
    try:
        local_dt = datetime.fromisoformat(peak_local_time)
        peak_utc = (local_dt - timedelta(seconds=utc_offset_seconds)).replace(tzinfo=timezone.utc)
        peak_time_utc = peak_utc.isoformat().replace("+00:00", "Z")
        peak_hour_utc = peak_utc.hour
    except Exception:
        peak_time_utc = None
        peak_hour_utc = None
    return {
        "forecast_max_f": max_f,
        "forecast_peak_hour_local": int(peak_local_time[11:13]),
        "forecast_peak_time_local": peak_local_time,
        "forecast_peak_hour_utc": peak_hour_utc,
        "forecast_peak_time_utc": peak_time_utc,
        "forecast_hourly_count": len(pairs),
        "forecast_values_hash": forecast_values_hash(times, temps),
        "forecast_peak_source": f"open_meteo_live_{source_model}",
        "forecast_timezone": payload.get("timezone"),
        "forecast_utc_offset_seconds": utc_offset_seconds,
        "forecast_peak_fetch_status": "derived",
    }


def forecast_model_from_record(record: dict[str, Any]) -> str:
    raw = safe_str(record.get("forecast_source")) or safe_str(record.get("model")) or "gfs"
    return OPEN_METEO_MODEL_BY_SOURCE.get(raw, raw if raw in {"gfs", "ecmwf"} else "gfs")


def fetch_live_forecast_peak_details(city: str, target_date: str, model: str) -> dict[str, Any]:
    coords = CITY_COORDS.get(city)
    if coords is None:
        return {"forecast_peak_fetch_status": "missing_city_coords"}
    if model not in {"gfs", "ecmwf"}:
        return {"forecast_peak_fetch_status": "unsupported_model", "forecast_peak_source": f"open_meteo_live_{model}"}
    cache_path = FORECAST_PEAK_CACHE_DIR / f"{city}_{target_date}_{model}.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            details = forecast_details_from_open_meteo(cached, source_model=model)
            if details:
                details["forecast_peak_fetch_status"] = "cache"
                details["forecast_peak_cache_path"] = str(cache_path)
                return details
        except Exception:
            pass
    lat, lon = coords
    payload = fetch_json(
        f"https://api.open-meteo.com/v1/{model}",
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "start_date": target_date,
            "end_date": target_date,
        },
        max_rounds=2,
        budget_sec=FORECAST_PEAK_FETCH_BUDGET_SEC,
    )
    FORECAST_PEAK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    details = forecast_details_from_open_meteo(payload, source_model=model)
    if not details:
        return {"forecast_peak_fetch_status": "empty_forecast_payload", "forecast_peak_cache_path": str(cache_path)}
    details["forecast_peak_fetch_status"] = "fetched"
    details["forecast_peak_cache_path"] = str(cache_path)
    return details


def forecast_peak_fields_from_record(record: dict[str, Any], *, city: str, target_date: str, unit: str, now_local: datetime) -> dict[str, Any]:
    out = {
        "forecast_source": safe_str(record.get("forecast_source")),
        "forecast_max_f": record.get("forecast_max_f"),
        "forecast_max_native": record.get("forecast_max_native"),
        "forecast_peak_hour_local": record.get("forecast_peak_hour_local"),
        "forecast_peak_time_local": record.get("forecast_peak_time_local"),
        "forecast_peak_hour_utc": record.get("forecast_peak_hour_utc"),
        "forecast_peak_time_utc": record.get("forecast_peak_time_utc"),
        "forecast_hourly_count": record.get("forecast_hourly_count"),
        "forecast_values_hash": record.get("forecast_values_hash"),
        "forecast_peak_source": record.get("forecast_peak_source"),
        "forecast_timezone": record.get("forecast_timezone"),
        "forecast_utc_offset_seconds": record.get("forecast_utc_offset_seconds"),
        "forecast_peak_fetch_status": "snapshot_native" if record.get("forecast_peak_hour_local") is not None and record.get("forecast_values_hash") else "snapshot_missing",
        "forecast_peak_cache_path": "",
    }
    if out["forecast_peak_fetch_status"] == "snapshot_missing":
        has_forecast_source = any(record.get(key) not in (None, "") for key in ("forecast_source", "forecast_max_f", "model"))
        if has_forecast_source:
            try:
                fetched = fetch_live_forecast_peak_details(city, target_date, forecast_model_from_record(record))
                out.update({k: v for k, v in fetched.items() if v is not None})
            except Exception as exc:  # noqa: BLE001
                out["forecast_peak_fetch_status"] = f"fetch_failed:{type(exc).__name__}"
                out["forecast_peak_error"] = str(exc)[:300]
        else:
            out["forecast_peak_fetch_status"] = "snapshot_missing_no_forecast_source"
    if out.get("forecast_max_f") is not None and out.get("forecast_max_native") in (None, ""):
        max_f = to_float(out.get("forecast_max_f"), np.nan)
        if math.isfinite(max_f):
            out["forecast_max_native"] = (max_f - 32.0) * 5.0 / 9.0 if unit == "C" else max_f
    if out.get("forecast_peak_delta_hours_local") in (None, "") and out.get("forecast_peak_hour_local") not in (None, ""):
        peak = to_float(out.get("forecast_peak_hour_local"), np.nan)
        if math.isfinite(peak):
            out["forecast_peak_delta_hours_local"] = now_local.hour + now_local.minute / 60.0 - peak
    return out


def fetch_text(
    url: str,
    params: list[tuple[str, Any]],
    max_rounds: int = 1,
    *,
    timeout_sec: float = HTTP_TIMEOUT_SEC,
    budget_sec: float = HTTP_FETCH_BUDGET_SEC,
) -> str:
    last = None
    started = time.monotonic()
    for rnd in range(max_rounds):
        for proxy in PROXIES:
            remaining = budget_sec - (time.monotonic() - started)
            if remaining <= 0:
                raise RuntimeError(f"fetch timed out {url}: {last}")
            try:
                r = http_get_with_deadline(url, params=params, proxy=proxy, timeout_sec=min(timeout_sec, remaining))
                r.raise_for_status()
                return r.text
            except Exception as exc:  # noqa: BLE001
                last = f"{proxy}: {type(exc).__name__}: {exc}"
                if budget_sec - (time.monotonic() - started) <= 0:
                    raise RuntimeError(f"fetch timed out {url}: {last}")
                time.sleep(min(0.4 + rnd, max(0.0, budget_sec - (time.monotonic() - started))))
    raise RuntimeError(f"fetch failed {url}: {last}")


def book_levels(book: Any, side: str) -> list[tuple[float, float]]:
    rows = book.get(f"{side}s") if isinstance(book, dict) else []
    out: list[tuple[float, float]] = []
    for item in rows or []:
        try:
            price = float(item.get("price"))
            size = float(item.get("size"))
        except Exception:
            continue
        if price > 0 and size > 0:
            out.append((price, size))
    return sorted(out, reverse=(side == "bid"))


def fresh_taker_quote(row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    book = fetch_json(CLOB_BOOK_API, {"token_id": str(row["token_id"])}, max_rounds=1)
    asks = book_levels(book, "ask")
    bids = book_levels(book, "bid")
    if not asks:
        return {"status": "rejected", "reason": "fresh_book_no_ask", "best_bid": bids[0][0] if bids else 0.0}
    fresh_ask, fresh_ask_size = asks[0]
    p_yes = float(row["p_yes_win"])
    snapshot_ask = float(row["yes_current_ask"])
    max_by_cushion = snapshot_ask + float(args.max_taker_cushion)
    max_price = min(max_by_cushion, 0.999)
    if fresh_ask > max_price + 1e-9:
        return {
            "status": "rejected",
            "reason": "fresh_ask_exceeds_cushion",
            "best_bid": bids[0][0] if bids else 0.0,
            "fresh_ask": fresh_ask,
            "fresh_ask_size": fresh_ask_size,
            "fresh_available_notional": fresh_ask * fresh_ask_size,
            "max_taker_price": max_price,
            "edge_at_fresh_ask": p_yes - fresh_ask,
        }
    executable = [(price, size) for price, size in asks if price <= max_price + 1e-9]
    executable_notional = sum(price * size for price, size in executable)
    worst_executable_ask = max((price for price, _size in executable), default=fresh_ask)
    limit_price = min(worst_executable_ask + float(args.cross_tick_buffer), max_price)
    required_edge = required_fresh_quote_edge(row, args)
    edge_at_limit = p_yes - limit_price
    if edge_at_limit + 1e-9 < required_edge:
        return {
            "status": "rejected",
            "reason": "fresh_edge_below_required",
            "best_bid": bids[0][0] if bids else 0.0,
            "fresh_ask": fresh_ask,
            "fresh_ask_size": fresh_ask_size,
            "fresh_available_notional": executable_notional,
            "max_taker_price": max_price,
            "limit_price": limit_price,
            "edge_at_fresh_ask": p_yes - fresh_ask,
            "edge_at_limit": edge_at_limit,
            "required_quote_edge": required_edge,
        }
    return {
        "status": "accepted",
        "best_bid": bids[0][0] if bids else 0.0,
        "fresh_ask": fresh_ask,
        "fresh_ask_size": fresh_ask_size,
        "fresh_available_notional": executable_notional,
        "max_taker_price": max_price,
        "limit_price": limit_price,
        "edge_at_fresh_ask": p_yes - fresh_ask,
        "edge_at_limit": edge_at_limit,
        "required_quote_edge": required_edge,
        "expected_profit_usd": float(args.max_order_notional) * (p_yes / limit_price - 1.0),
        "derived_min_edge_after_full_cushion": required_edge,
        "cushion_paid_vs_snapshot": limit_price - snapshot_ask,
    }


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


def snapshot_dir() -> Path:
    candidates: list[Path] = []
    for key in ("THETA_CURRENT_YES_SNAPSHOT_DIR", "WEATHER_PREDICT_PAPER_SNAPSHOT_DIR"):
        if os.environ.get(key):
            candidates.append(Path(str(os.environ[key])).expanduser())
    if os.environ.get("WEATHER_PREDICT_DIR"):
        candidates.append(Path(str(os.environ["WEATHER_PREDICT_DIR"])).expanduser() / "output/paper_snapshots")
    candidates.append(Path("/home/jiarui/projects/weather_data_feed_service_runtime/output/paper_snapshots"))
    candidates.append(Path("/home/jiarui/projects/weather-predict/output/paper_snapshots"))
    candidates.append(ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots")
    for path in candidates:
        if path.exists():
            return path
    return candidates[-1]


def latest_snapshot() -> Path | None:
    files = sorted(snapshot_dir().glob("snapshot_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def observation_cache_candidates() -> list[Path]:
    candidates: list[Path] = []
    for key in ("THETA_CURRENT_YES_OBSERVATION_CACHE", "WEATHER_DATA_FEED_OBSERVATION_CACHE"):
        if os.environ.get(key):
            candidates.append(Path(str(os.environ[key])).expanduser())
    candidates.append(Path("/home/jiarui/projects/weather_data_feed_service_runtime/output/observations/latest.json"))
    candidates.append(ROOT / "runtime/weather_edge_v1/market_data/observations/latest.json")
    candidates.append(ROOT / "runtime/weather_edge_v1/observations/latest.json")
    out: list[Path] = []
    for path in candidates:
        if path not in out:
            out.append(path)
    return out


def latest_observation_cache_path(explicit: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    for path in observation_cache_candidates():
        if path.exists():
            return path
    return None


def load_latest_observation_cache(explicit: str | None = None) -> tuple[dict[str, Any] | None, Path | None, str]:
    path = latest_observation_cache_path(explicit)
    if path is None or not path.exists():
        return None, path, "missing"
    try:
        return load_observation_cache(path), path, "ok"
    except Exception as exc:  # noqa: BLE001
        return None, path, f"error:{type(exc).__name__}: {exc}"


def snapshot_freshness_time(snapshot: dict[str, Any], snapshot_path: Path) -> tuple[datetime, str]:
    for key in ("generated_at_utc", "snapshot_generated_at_utc", "snapshot_fetched_at_utc"):
        parsed = parse_utc(snapshot.get(key))
        if parsed is not None:
            return parsed, key
    logical_ts = parse_utc(snapshot.get("ts_utc"))
    try:
        file_mtime = datetime.fromtimestamp(snapshot_path.stat().st_mtime, timezone.utc)
    except OSError:
        file_mtime = None
    if logical_ts is None:
        return (file_mtime or datetime.now(timezone.utc)), "file_mtime_utc"
    if file_mtime is not None and (file_mtime - logical_ts).total_seconds() > 300:
        return file_mtime, "file_mtime_utc"
    return logical_ts, "ts_utc"


def required_fresh_quote_edge(row: dict[str, Any], args: argparse.Namespace) -> float:
    if safe_str(row.get("entry_profile")) == "peak_forming_micro":
        return max(0.0, float(getattr(args, "peak_forming_min_edge", 0.02)))
    return max(0.0, 0.05 - float(args.max_taker_cushion))


def load_stations() -> dict[str, Station]:
    data = json.loads(STATION_SUMMARY.read_text(encoding="utf-8"))
    stations = {
        str(item["city"]): Station(
            city=str(item["city"]),
            icao=str(item["icao"]).upper(),
            unit=str(item["unit"]).upper(),
            utc_offset=int(item["utc_offset"]),
            timezone_name=safe_str(item.get("timezone")) or city_timezone_name(str(item["city"])),
        )
        for item in data["stations"]
    }
    for city, profile in load_source_profiles().items():
        if city in stations:
            continue
        if not profile.live_eligible:
            continue
        if profile.primary_source not in {"aviationweather_metar", "aviationweather"}:
            continue
        station_code = safe_str(profile.official_station_or_feed).upper()
        if not station_code or len(station_code) != 4:
            continue
        stations[city] = Station(
            city=city,
            icao=station_code,
            unit=(safe_str(profile.unit) or "C").upper(),
            utc_offset=0,
            timezone_name=profile.timezone_name or city_timezone_name(city),
        )
    return stations


def station_map_missing_audit(
    city: str,
    records_by_target_date: dict[str, list[dict[str, Any]]],
    source_profiles: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = (source_profiles or {}).get(city)
    reason = "source_profile_missing"
    if profile is not None:
        if not profile.live_eligible:
            reason = "source_profile_not_live_eligible"
        elif profile.primary_source not in {"aviationweather_metar", "aviationweather"}:
            reason = f"unsupported_primary_source:{profile.primary_source}"
        elif not safe_str(profile.official_station_or_feed):
            reason = "source_profile_missing_station"
        else:
            reason = "station_not_loaded"
    return {
        "city": city,
        "target_date": "",
        "status": "station_map_missing",
        "reason": reason,
        "available_target_dates": sorted(records_by_target_date),
    }


def load_model_artifact(path: Path = MODEL_ARTIFACT) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fade_confirmed_model_artifact_path(args: argparse.Namespace) -> Path:
    raw = safe_str(getattr(args, "fade_confirmed_model_artifact", "")) or str(FADE_CONFIRMED_MODEL_ARTIFACT)
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def apply_probability_branch_scores(
    current: pd.DataFrame,
    *,
    args: argparse.Namespace,
    base_artifact: dict[str, Any],
    fade_artifact: dict[str, Any] | None,
) -> pd.DataFrame:
    out = current.copy()
    base_p = score_rows(out[MODEL_FEATURES], base_artifact)
    out["p_yes_win_base_current_yes_model"] = base_p
    out["p_yes_win"] = base_p
    out["probability_source"] = "theta_current_yes_v8_train_logistic"
    out["model_version"] = "theta_current_yes_v8_train_logistic"
    out["probability_branch"] = "base_current_yes_model"

    if fade_artifact is None:
        out["p_yes_win_fade_confirmed_specialist"] = np.nan
        out["fade_confirmed_specialist_delta"] = np.nan
        return out

    fade_p = score_rows(out[MODEL_FEATURES], fade_artifact)
    out["p_yes_win_fade_confirmed_specialist"] = fade_p
    out["fade_confirmed_specialist_delta"] = fade_p - base_p
    mode = safe_str(getattr(args, "fade_confirmed_model_mode", "base")) or "base"
    if mode == "specialist":
        fade_mask = out["decline_c"].astype(float).ge(fade_gate_spec_from_args(args).min_decline_c)
        out.loc[fade_mask, "p_yes_win"] = fade_p[fade_mask.to_numpy()]
        out.loc[fade_mask, "probability_source"] = "theta_current_yes_fade_confirmed_specialist_v1"
        out.loc[fade_mask, "model_version"] = "theta_current_yes_fade_confirmed_logistic_v1"
        out.loc[fade_mask, "probability_branch"] = "fade_confirmed_specialist_v1"
    return out


def parse_label(label: str, question: str = "") -> dict[str, Any] | None:
    return parse_label_dict(label, question, include_label=True)


def round_half_up(x: float) -> int:
    return math.floor(float(x) + 0.5)


def aviationweather_obs(icao: str, tz: ZoneInfo, local_date) -> list[dict[str, Any]]:
    data = fetch_json(METAR_API, {"ids": icao, "format": "json", "hours": "30"})
    out = []
    for rec in data:
        ts = parse_utc(rec.get("reportTime"))
        if ts is None or ts.astimezone(tz).date() != local_date:
            continue
        temp_c = rec.get("temp")
        if temp_c is None:
            continue
        cover = safe_str(rec.get("cover")).upper()
        clouds = rec.get("clouds") if isinstance(rec.get("clouds"), list) else []
        if not cover and clouds and isinstance(clouds[0], dict):
            cover = safe_str(clouds[0].get("cover")).upper()
        raw_ob = safe_str(rec.get("rawOb")).upper()
        if not cover and "CAVOK" in raw_ob:
            cover = "CAVOK"
        out.append(
            {
                "ts": ts,
                "tmpc": to_float(temp_c, np.nan),
                "dwpc": to_float(rec.get("dewp"), np.nan),
                "relh": to_float(rec.get("relh"), np.nan),
                "drct": to_float(rec.get("wdir"), np.nan),
                "sknt": to_float(rec.get("wspd"), np.nan),
                "sky": SKY_CODE.get(cover, np.nan),
            }
        )
    return out


def iem_obs(icao: str, tz: ZoneInfo, local_date) -> list[dict[str, Any]]:
    params = build_iem_local_day_params(
        icao,
        tz,
        local_date,
        columns=("tmpc", "dwpc", "relh", "sknt", "skyc1"),
        extra_end_days=1,
    )
    text = fetch_text(IEM_ASOS_API, params)
    out = []
    for ts, tmpc, row in parse_iem_asos_records(text, tz, local_date):
        sky_raw = safe_str(row.get("skyc1")).upper()
        out.append(
            {
                "ts": ts,
                "tmpc": tmpc,
                "dwpc": to_float(row.get("dwpc"), np.nan),
                "relh": to_float(row.get("relh"), np.nan),
                "sknt": to_float(row.get("sknt"), np.nan),
                "sky": SKY_CODE.get(sky_raw, np.nan),
            }
        )
    return out


def fetch_obs(
    station: Station,
    now: datetime,
    *,
    max_obs_age_min: float,
    pre_update_blackout_min: float,
) -> dict[str, Any]:
    tz = station_timezone(station)
    local_date = now.astimezone(tz).date()
    try:
        obs = aviationweather_obs(station.icao, tz, local_date)
        source = "aviationweather_metar"
    except Exception as exc:  # noqa: BLE001
        obs = []
        source = f"aviationweather_failed:{type(exc).__name__}"
    if len(obs) < 6:
        try:
            obs = iem_obs(station.icao, tz, local_date)
            source = "iem_asos"
        except Exception as exc:  # noqa: BLE001
            return {"status": "obs_fetch_failed", "source": source, "error": f"{type(exc).__name__}: {exc}", "n_obs": len(obs)}
    status, common, obs = observation_clock_guard(
        obs,
        now,
        station=station,
        source=source,
        config=ObservationClockConfig(
            max_obs_age_min=max_obs_age_min,
            pre_update_blackout_min=pre_update_blackout_min,
        ),
    )
    if status != "ok":
        return {"status": status, **common}
    last = obs[-1]
    temps = [float(x["tmpc"]) for x in obs if math.isfinite(float(x["tmpc"]))]
    if not temps:
        return {"status": "missing_temp", **common}
    running_max_c = max(temps)
    running_max_hits = [
        row["ts"]
        for row in obs
        if row.get("ts") <= now
        and math.isfinite(to_float(row.get("tmpc"), np.nan))
        and abs(to_float(row.get("tmpc"), np.nan) - running_max_c) < 1e-9
    ]
    running_max_obs_utc = max(running_max_hits) if running_max_hits else None
    minutes_since_running_max = (
        (now - running_max_obs_utc).total_seconds() / 60.0
        if running_max_obs_utc is not None
        else np.nan
    )

    def asof(minutes: int, key: str) -> float:
        target = now - timedelta(minutes=minutes)
        prev = [x for x in obs if x["ts"] <= target]
        if not prev:
            return np.nan
        return to_float(prev[-1].get(key), np.nan)

    tmpc_now = to_float(last.get("tmpc"), np.nan)
    dwpc_now = to_float(last.get("dwpc"), np.nan)
    tmpf_now = tmpc_now * 9.0 / 5.0 + 32.0 if math.isfinite(tmpc_now) else np.nan
    dwpf_now = dwpc_now * 9.0 / 5.0 + 32.0 if math.isfinite(dwpc_now) else np.nan
    tmpc_1h = asof(60, "tmpc")
    tmpc_3h = asof(180, "tmpc")
    dwpc_3h = asof(180, "dwpc")
    relh_3h = asof(180, "relh")
    sky_now = to_float(last.get("sky"), np.nan)
    sky_1h = asof(60, "sky")
    sky_3h = asof(180, "sky")
    return {
        "status": "ok",
        **common,
        "running_max_c": running_max_c,
        "running_max_obs_utc": running_max_obs_utc.isoformat() if running_max_obs_utc else "",
        "minutes_since_running_max": minutes_since_running_max,
        "current_temp_c": tmpc_now,
        "decline_c": running_max_c - tmpc_now,
        "tmpf_now": tmpf_now,
        "dwpf_now": dwpf_now,
        "dewpoint_depression_f": tmpf_now - dwpf_now if math.isfinite(tmpf_now) and math.isfinite(dwpf_now) else np.nan,
        "relh_now": to_float(last.get("relh"), np.nan),
        "drct_now": to_float(last.get("drct"), np.nan),
        "sknt_now": to_float(last.get("sknt"), np.nan),
        "sky_now": sky_now,
        "sky_1h": sky_1h,
        "sky_3h": sky_3h,
        "d_sky_1h": sky_now - sky_1h if math.isfinite(sky_now) and math.isfinite(sky_1h) else np.nan,
        "d_sky_3h": sky_now - sky_3h if math.isfinite(sky_now) and math.isfinite(sky_3h) else np.nan,
        "d_tmpf_1h": (tmpc_now - tmpc_1h) * 9.0 / 5.0 if math.isfinite(tmpc_now) and math.isfinite(tmpc_1h) else np.nan,
        "d_tmpf_3h": (tmpc_now - tmpc_3h) * 9.0 / 5.0 if math.isfinite(tmpc_now) and math.isfinite(tmpc_3h) else np.nan,
        "d_dwpf_3h": (dwpc_now - dwpc_3h) * 9.0 / 5.0 if math.isfinite(dwpc_now) and math.isfinite(dwpc_3h) else np.nan,
        "d_relh_3h": to_float(last.get("relh"), np.nan) - relh_3h if math.isfinite(to_float(last.get("relh"), np.nan)) and math.isfinite(relh_3h) else np.nan,
    }


def snapshot_records(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records = [r for r in data.get("records", []) if isinstance(r, dict)]
    return data, records


def effective_obs_age_limit(max_obs_age_min: float, cadence_min: float | None) -> float:
    if cadence_min is None or not math.isfinite(float(cadence_min)) or float(cadence_min) <= 0:
        return float(max_obs_age_min)
    return min(
        OBS_MAX_DYNAMIC_AGE_MIN,
        max(float(max_obs_age_min), float(cadence_min) + OBS_STALE_CADENCE_GRACE_MIN),
    )


def snapshot_metar_obs(
    city_records: list[dict[str, Any]],
    station: Station,
    now: datetime,
    *,
    max_obs_age_min: float,
    pre_update_blackout_min: float,
) -> dict[str, Any]:
    candidates = [
        row
        for row in city_records
        if row.get("metar_latest_ts_utc") not in (None, "")
        and row.get("metar_latest_temp_f") not in (None, "")
        and row.get("metar_current_max_f") not in (None, "")
    ]
    if not candidates:
        return {"status": "snapshot_metar_missing", "source": "paper_snapshot_metar", "n_obs": 0}
    record = max(candidates, key=lambda row: to_float(row.get("metar_obs_count_today"), 0.0))
    last_obs = parse_utc(record.get("metar_latest_ts_utc"))
    if last_obs is None:
        return {"status": "snapshot_metar_bad_ts", "source": "paper_snapshot_metar", "n_obs": 0}
    n_obs = int(max(0.0, to_float(record.get("metar_obs_count_today"), 0.0)))
    if n_obs < ObservationClockConfig().min_obs_asof:
        return {"status": "insufficient_obs_asof", "source": "paper_snapshot_metar", "n_obs": n_obs, "timezone": station.timezone_name}
    age_min = (now - last_obs).total_seconds() / 60.0
    cadence_min = 60.0
    effective_max_obs_age_min = effective_obs_age_limit(max_obs_age_min, cadence_min)
    minutes_to_next = cadence_min - age_min
    common = {
        "source": "paper_snapshot_metar",
        "n_obs": n_obs,
        "age_min": round(age_min, 1),
        "max_obs_age_min": max_obs_age_min,
        "effective_max_obs_age_min": round(effective_max_obs_age_min, 1),
        "last_obs_utc": last_obs.isoformat(),
        "timezone": station.timezone_name,
        "cadence_min": cadence_min,
        "minutes_to_next_obs": round(minutes_to_next, 1),
    }
    if age_min > effective_max_obs_age_min:
        return {"status": "stale_obs", **common}
    if 0.0 <= minutes_to_next <= pre_update_blackout_min:
        return {"status": "pre_metar_update_blackout", "pre_update_blackout_min": pre_update_blackout_min, **common}
    latest_f = to_float(record.get("metar_latest_temp_f"), np.nan)
    running_f = to_float(record.get("metar_current_max_f"), np.nan)
    if not math.isfinite(latest_f) or not math.isfinite(running_f):
        return {"status": "snapshot_metar_bad_temp", **common}
    tmpc_now = (latest_f - 32.0) * 5.0 / 9.0
    running_max_c = (running_f - 32.0) * 5.0 / 9.0
    return {
        "status": "ok",
        **common,
        "running_max_c": running_max_c,
        "running_max_obs_utc": last_obs.isoformat(),
        "minutes_since_running_max": np.nan,
        "current_temp_c": tmpc_now,
        "decline_c": running_max_c - tmpc_now,
        "tmpf_now": latest_f,
        "dwpf_now": np.nan,
        "dewpoint_depression_f": np.nan,
        "relh_now": np.nan,
        "drct_now": np.nan,
        "sknt_now": np.nan,
        "sky_now": np.nan,
        "sky_1h": np.nan,
        "sky_3h": np.nan,
        "d_sky_1h": np.nan,
        "d_sky_3h": np.nan,
        "d_tmpf_1h": np.nan,
        "d_tmpf_3h": np.nan,
        "d_dwpf_3h": np.nan,
        "d_relh_3h": np.nan,
    }


def observation_cache_obs(
    observation_cache: dict[str, Any],
    city: str,
    target_date: str,
    station: Station,
    now: datetime,
    *,
    max_obs_age_min: float,
    pre_update_blackout_min: float,
) -> dict[str, Any]:
    record = index_observation_cache(observation_cache).get((city, target_date))
    if not record:
        return {"status": "observation_cache_missing", "source": "weather_data_feed_observation_cache", "n_obs": 0}
    source = safe_str(record.get("source")) or "weather_data_feed_observation_cache"
    n_obs = int(max(0.0, to_float(record.get("n_obs") or record.get("record_count"), 0.0)))
    if record.get("status") != "ok":
        return {
            "status": safe_str(record.get("status")) or "observation_cache_not_ok",
            "source": source,
            "n_obs": n_obs,
            "error": safe_str(record.get("error")),
            "timezone": station.timezone_name,
            "last_obs_utc": safe_str(record.get("last_obs_utc")),
        }
    last_obs = parse_utc(record.get("last_obs_utc"))
    if last_obs is None:
        return {"status": "observation_cache_bad_ts", "source": source, "n_obs": n_obs, "timezone": station.timezone_name}
    age_min = (now - last_obs).total_seconds() / 60.0
    cadence_min = to_float(record.get("cadence_min") or record.get("estimated_cadence_min"), np.nan)
    if not math.isfinite(cadence_min) and source in METAR_LIKE_OBSERVATION_CACHE_SOURCES:
        cadence_min = 60.0
    cadence_value = None if not math.isfinite(cadence_min) else cadence_min
    effective_max_obs_age_min = effective_obs_age_limit(max_obs_age_min, cadence_value)
    minutes_to_next = cadence_value - age_min if cadence_value is not None else np.nan
    common = {
        "source": source,
        "n_obs": n_obs,
        "age_min": round(age_min, 1),
        "max_obs_age_min": max_obs_age_min,
        "effective_max_obs_age_min": round(effective_max_obs_age_min, 1),
        "last_obs_utc": last_obs.isoformat(),
        "timezone": station.timezone_name,
        "cadence_min": None if cadence_value is None else round(cadence_value, 1),
        "minutes_to_next_obs": None if cadence_value is None else round(minutes_to_next, 1),
        "observation_cache_generated_at_utc": safe_str(observation_cache.get("generated_at_utc")),
        "observation_cache_fetched_at_utc": safe_str(record.get("fetched_at_utc")),
    }
    if age_min > effective_max_obs_age_min:
        return {"status": "stale_obs", **common}
    if cadence_value is not None and 0.0 <= minutes_to_next <= pre_update_blackout_min:
        return {"status": "pre_metar_update_blackout", "pre_update_blackout_min": pre_update_blackout_min, **common}
    current_temp_c = to_float(record.get("current_temp_c"), np.nan)
    running_max_c = to_float(record.get("running_max_c"), np.nan)
    if not math.isfinite(current_temp_c) or not math.isfinite(running_max_c):
        return {"status": "observation_cache_bad_temp", **common}
    return {
        "status": "ok",
        **common,
        "running_max_c": running_max_c,
        "running_max_obs_utc": safe_str(record.get("running_max_obs_utc")) or last_obs.isoformat(),
        "minutes_since_running_max": to_float(record.get("minutes_since_running_max"), np.nan),
        "current_temp_c": current_temp_c,
        "decline_c": to_float(record.get("decline_c"), running_max_c - current_temp_c),
        "tmpf_now": to_float(record.get("tmpf_now"), current_temp_c * 9.0 / 5.0 + 32.0),
        "dwpf_now": to_float(record.get("dwpf_now"), np.nan),
        "dewpoint_depression_f": to_float(record.get("dewpoint_depression_f"), np.nan),
        "relh_now": to_float(record.get("relh_now"), np.nan),
        "drct_now": to_float(record.get("drct_now"), np.nan),
        "sknt_now": to_float(record.get("sknt_now"), np.nan),
        "sky_now": to_float(record.get("sky_now"), np.nan),
        "sky_1h": to_float(record.get("sky_1h"), np.nan),
        "sky_3h": to_float(record.get("sky_3h"), np.nan),
        "d_sky_1h": to_float(record.get("d_sky_1h"), np.nan),
        "d_sky_3h": to_float(record.get("d_sky_3h"), np.nan),
        "d_tmpf_1h": to_float(record.get("d_tmpf_1h"), np.nan),
        "d_tmpf_3h": to_float(record.get("d_tmpf_3h"), np.nan),
        "d_dwpf_3h": to_float(record.get("d_dwpf_3h"), np.nan),
        "d_relh_3h": to_float(record.get("d_relh_3h"), np.nan),
    }


def record_target_date(record: dict[str, Any]) -> str:
    return safe_str(record.get("target_date")) or safe_str(record.get("event_date"))


def record_market_local_date(record: dict[str, Any]) -> str:
    for key in ("market_local_date", "city_local_date", "local_event_date", "target_local_date"):
        value = safe_str(record.get(key))
        if value:
            return value
    return ""


def date_delta_days(target_date: str, local_date: str) -> int | None:
    try:
        return (datetime.fromisoformat(target_date).date() - datetime.fromisoformat(local_date).date()).days
    except ValueError:
        return None


def records_by_city(records: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    seen_markets: set[str] = set()
    for record in records:
        city = safe_str(record.get("city"))
        target_date = record_target_date(record)
        market_id = safe_str(record.get("market_id")) or safe_str(record.get("condition_id"))
        if not city or not target_date or not market_id or market_id in seen_markets:
            continue
        seen_markets.add(market_id)
        out.setdefault(city, {}).setdefault(target_date, []).append(record)
    return out


def select_current_local_market_records(
    city: str,
    records_by_target_date: dict[str, list[dict[str, Any]]],
    *,
    local_date: str,
    local_time: str,
    timezone_name: str,
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
    if local_date in records_by_target_date:
        return local_date, records_by_target_date[local_date], None

    mapped = [
        (target_date, rows)
        for target_date, rows in records_by_target_date.items()
        if any(record_market_local_date(row) == local_date for row in rows)
    ]
    if mapped:
        target_date, rows = sorted(mapped, key=lambda item: item[0])[0]
        return target_date, rows, None

    available_target_dates = sorted(records_by_target_date)
    available_market_local_dates = sorted(
        {
            record_market_local_date(row)
            for rows in records_by_target_date.values()
            for row in rows
            if record_market_local_date(row)
        }
    )
    nearest_target_date = ""
    nearest_delta_days = None
    deltas = [
        (abs(delta), target_date, delta)
        for target_date in available_target_dates
        for delta in [date_delta_days(target_date, local_date)]
        if delta is not None
    ]
    if deltas:
        _, nearest_target_date, nearest_delta_days = sorted(deltas)[0]
    audit = {
        "city": city,
        "target_date": nearest_target_date,
        "status": "no_current_local_date_market",
        "local_date": local_date,
        "local_time": local_time,
        "timezone": timezone_name,
        "available_target_dates": available_target_dates,
        "available_target_date_count": len(available_target_dates),
        "available_market_local_dates": available_market_local_dates,
        "nearest_target_date": nearest_target_date,
        "nearest_target_date_delta_days": nearest_delta_days,
    }
    return None, [], audit


def build_current_rows(
    snapshot: dict[str, Any],
    records: list[dict[str, Any]],
    stations: dict[str, Station],
    now: datetime,
    *,
    source_profiles: dict[str, Any] | None = None,
    observation_cache: dict[str, Any] | None = None,
    max_obs_age_min: float,
    pre_update_blackout_min: float,
    min_gap_to_next_bracket_c: float,
    min_local_hour: int = 13,
    max_local_hour: int = 15,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    grouped = records_by_city(records)
    rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for city, records_by_target_date in grouped.items():
        station = stations.get(city)
        if station is None:
            audits.append(station_map_missing_audit(city, records_by_target_date, source_profiles))
            continue
        tz = station_timezone(station)
        local_now = now.astimezone(tz)
        local_date = local_now.date().isoformat()
        timezone_name = timezone_label(tz)
        local_time = local_now.isoformat(timespec="seconds")
        target_date, city_records, date_audit = select_current_local_market_records(
            city,
            records_by_target_date,
            local_date=local_date,
            local_time=local_time,
            timezone_name=timezone_name,
        )
        if date_audit is not None or target_date is None:
            if date_audit is not None:
                audits.append(date_audit)
            continue
        hour = local_now.hour
        obs = {"status": "observation_cache_missing"}
        if observation_cache is not None:
            obs = observation_cache_obs(
                observation_cache,
                city,
                target_date,
                station,
                now,
                max_obs_age_min=max_obs_age_min,
                pre_update_blackout_min=pre_update_blackout_min,
            )
        if obs.get("status") in OBSERVATION_CACHE_FALLBACK_STATUSES:
            obs = snapshot_metar_obs(
                city_records,
                station,
                now,
                max_obs_age_min=max_obs_age_min,
                pre_update_blackout_min=pre_update_blackout_min,
            )
        if obs.get("status") != "ok":
            audits.append({"city": city, "target_date": target_date, "status": obs.get("status"), "obs": obs, "hour_local": hour})
            continue
        unit = station.unit
        current_native = obs["current_temp_c"] * 9.0 / 5.0 + 32.0 if unit == "F" else obs["current_temp_c"]
        running_native = obs["running_max_c"] * 9.0 / 5.0 + 32.0 if unit == "F" else obs["running_max_c"]
        running_value = round_half_up(running_native)
        parsed_records = []
        for record in city_records:
            parsed = parse_label(safe_str(record.get("bracket")), safe_str(record.get("question")))
            if parsed is None or parsed.get("bottom"):
                continue
            parsed_records.append((record, parsed))
        current = [(r, p) for r, p in parsed_records if bracket_contains(p, running_value)]
        if not current:
            audits.append({"city": city, "target_date": target_date, "status": "no_current_bracket", "running_value": running_value})
            continue
        current_record, current_parsed = sorted(current, key=lambda rp: (rp[1].get("top", False), to_float(rp[0].get("yes_best_ask"), 9.0)))[0]
        d1 = [
            (r, p)
            for r, p in parsed_records
            if p.get("low") is not None and float(p["low"]) > running_value
        ]
        if not d1:
            audits.append({"city": city, "target_date": target_date, "status": "no_d1_sibling", "running_value": running_value})
            continue
        d1_record, d1_parsed = sorted(d1, key=lambda rp: float(rp[1]["low"]))[0]
        gap_running = float(d1_parsed["low"]) - running_native
        gap_running_c = gap_running * 5.0 / 9.0 if unit == "F" else gap_running
        if gap_running_c <= min_gap_to_next_bracket_c + 1e-9:
            audits.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "status": "too_close_to_next_bracket",
                    "running_value": running_value,
                    "current_bracket": safe_str(current_record.get("bracket")),
                    "d1_bracket": safe_str(d1_record.get("bracket")),
                    "gap_running_to_d1_low_native": round(gap_running, 3),
                    "gap_running_to_d1_low_c": round(gap_running_c, 3),
                    "min_gap_to_next_bracket_c": min_gap_to_next_bracket_c,
                    "hour_local": hour,
                }
            )
            continue
        yes_ask = to_float(current_record.get("yes_best_ask"), 0.0)
        yes_size = to_float(current_record.get("yes_ask_size"), 0.0)
        no_ask = to_float(d1_record.get("no_best_ask"), 0.0)
        no_size = to_float(d1_record.get("no_ask_size"), 0.0)
        price_source = "snapshot_orderbook"
        if yes_ask <= 0:
            yes_ask = to_float(current_record.get("market_yes_price"), 0.0)
            price_source = "market_yes_price_fallback"
        if no_ask <= 0:
            d1_yes_price = to_float(d1_record.get("market_yes_price"), 0.0)
            if d1_yes_price > 0:
                no_ask = max(0.0, 1.0 - d1_yes_price)
                price_source = (
                    "market_yes_price_fallback"
                    if price_source == "market_yes_price_fallback"
                    else "snapshot_orderbook_with_d1_market_fallback"
                )
        if yes_ask <= 0 or no_ask <= 0:
            audits.append({"city": city, "target_date": target_date, "status": "missing_ask", "yes_ask": yes_ask, "d1_no_ask": no_ask})
            continue
        forecast_fields = forecast_peak_fields_from_record(
            current_record,
            city=city,
            target_date=target_date,
            unit=unit,
            now_local=local_now,
        )
        row = {
            "city": city,
            "target_date": target_date,
            "unit": unit,
            "timezone": timezone_name,
            "local_date": local_date,
            "local_time": local_time,
            "decision_hour_local": hour,
            "month": int(str(target_date)[5:7]),
            "decline_c": obs["decline_c"],
            "decline_native": running_native - current_native,
            "decline_band": (running_native - current_native) / 2.0 if unit == "F" else running_native - current_native,
            "gap_running_to_d1_low_native": gap_running,
            "gap_running_to_d1_low_c": gap_running_c,
            "gap_current_to_d1_low_native": gap_running + (running_native - current_native),
            "running_value": running_value,
            "current_native": current_native,
            "running_native": running_native,
            "yes_current_ask": yes_ask,
            "yes_current_size": yes_size,
            "d1_no_ask": no_ask,
            "d1_no_size": no_size,
            "snapshot_price_source": price_source,
            "ask_gap_d1_no_minus_yes": no_ask - yes_ask,
            "log_yes_size": math.log1p(max(0.0, yes_size)),
            "log_no_size": math.log1p(max(0.0, no_size)),
            "current_bracket": safe_str(current_record.get("bracket")),
            "d1_no_bracket": safe_str(d1_record.get("bracket")),
            "condition_id": safe_str(current_record.get("condition_id")),
            "market_id": safe_str(current_record.get("market_id")),
            "event_slug": safe_str(current_record.get("event_slug")),
            "question": safe_str(current_record.get("question")),
            "token_id": safe_str(current_record.get("yes_token_id")),
            "snapshot_ts_utc": snapshot.get("ts_utc"),
            "snapshot_path": str(latest_snapshot() or ""),
            "obs": obs,
            **forecast_fields,
            **{
                k: obs.get(k, np.nan)
                for k in (
                    "tmpf_now",
                    "dwpf_now",
                    "dewpoint_depression_f",
                    "relh_now",
                    "drct_now",
                    "sknt_now",
                    "sky_now",
                    "sky_1h",
                    "sky_3h",
                    "d_sky_1h",
                    "d_sky_3h",
                    "d_tmpf_1h",
                    "d_tmpf_3h",
                    "d_dwpf_3h",
                    "d_relh_3h",
                )
            },
        }
        rows.append(row)
    return pd.DataFrame(rows), audits


def inferred_entry_profile_for_strategy_instance(strategy_instance: str) -> str:
    if "fade_confirmed" in strategy_instance:
        return "fade_confirmed"
    if "peak_forming" in strategy_instance:
        return "peak_forming_micro"
    return ""


def inferred_entry_profile_for_row(row: dict[str, Any]) -> str:
    explicit = safe_str(row.get("entry_profile"))
    if explicit:
        return explicit
    row_instance = safe_str(row.get("strategy_instance"))
    profile = inferred_entry_profile_for_strategy_instance(row_instance)
    if profile:
        return profile
    text = " ".join(
        safe_str(row.get(key)).lower()
        for key in ("combo", "shadow_decision", "shadow_reason", "decision_mode")
    )
    if "peak_forming" in text or "micro_probe" in text:
        return "peak_forming_micro"
    if "fade_confirmed" in text or "confirmed_v9" in text:
        return "fade_confirmed"
    return ""


def prior_row_matches_strategy(row: dict[str, Any], strategy_instance: str) -> bool:
    row_instance = safe_str(row.get("strategy_instance"))
    if row_instance == strategy_instance:
        return True
    profile = inferred_entry_profile_for_strategy_instance(strategy_instance)
    return (
        bool(profile)
        and row_instance == LEGACY_SHARED_STRATEGY_INSTANCE
        and inferred_entry_profile_for_row(row) == profile
    )


def prior_city_day_notional(strategy_instance: str) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    live_dirs = [ROOT / "runtime/weather_edge_v1/live", ROOT / "runtime/weather_edge_v1/remote_pm_agent/live"]
    for live_dir in live_dirs:
        if not live_dir.exists():
            continue
        for path in live_dir.glob("*.jsonl"):
            for row in read_jsonl(path):
                if not prior_row_matches_strategy(row, strategy_instance) or safe_str(row.get("status")) != "submitted":
                    continue
                key = (safe_str(row.get("city")), safe_str(row.get("target_date")))
                out[key] = out.get(key, 0.0) + max(0.0, to_float(row.get("posted_notional", row.get("notional")), 0.0))
    return out


def strategy_signal_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    token_or_market = safe_str(row.get("token_id")) or safe_str(row.get("market_id"))
    bracket = safe_str(row.get("current_bracket") or row.get("bracket"))
    return (
        safe_str(row.get("city")),
        safe_str(row.get("target_date")),
        token_or_market,
        bracket,
    )


def prior_strategy_signal_keys(strategy_instance: str) -> set[tuple[str, str, str, str]]:
    out: set[tuple[str, str, str, str]] = set()
    live_dirs = [ROOT / "runtime/weather_edge_v1/live", ROOT / "runtime/weather_edge_v1/remote_pm_agent/live"]
    for live_dir in live_dirs:
        if not live_dir.exists():
            continue
        for path in live_dir.glob("*.jsonl"):
            for row in read_jsonl(path):
                if not prior_row_matches_strategy(row, strategy_instance) or safe_str(row.get("status")) != "submitted":
                    continue
                key = strategy_signal_key(row)
                if all(key):
                    out.add(key)
    return out


def observation_epoch_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    obs = row.get("obs") if isinstance(row.get("obs"), dict) else {}
    bracket = safe_str(row.get("current_bracket") or row.get("bracket"))
    running_max_obs_utc = safe_str(row.get("running_max_obs_utc") or obs.get("running_max_obs_utc"))
    return (
        safe_str(row.get("city")),
        safe_str(row.get("target_date")),
        bracket,
        running_max_obs_utc,
    )


def prior_observation_epoch_keys(strategy_instance: str) -> set[tuple[str, str, str, str]]:
    out: set[tuple[str, str, str, str]] = set()
    live_dirs = [ROOT / "runtime/weather_edge_v1/live", ROOT / "runtime/weather_edge_v1/remote_pm_agent/live"]
    for live_dir in live_dirs:
        if not live_dir.exists():
            continue
        for path in live_dir.glob("*.jsonl"):
            for row in read_jsonl(path):
                if not prior_row_matches_strategy(row, strategy_instance) or safe_str(row.get("status")) != "submitted":
                    continue
                key = observation_epoch_key(row)
                if all(key):
                    out.add(key)
    return out


def order_expiry_fields(row: dict[str, Any]) -> dict[str, Any]:
    obs = row.get("obs") if isinstance(row.get("obs"), dict) else {}
    minutes_to_next = to_float(obs.get("minutes_to_next_obs") or row.get("minutes_to_next_obs"), np.nan)
    if math.isfinite(minutes_to_next) and minutes_to_next >= 0:
        ttl_min = min(90.0, max(10.0, minutes_to_next + 5.0))
        expiry_policy = "observation_clock_next_obs_plus_5m_max_90m_v1"
    else:
        ttl_min = 60.0
        expiry_policy = "fixed_60m_missing_observation_clock_v1"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_min)
    return {
        "expires_at_utc": expires_at.isoformat(timespec="seconds"),
        "order_ttl_min": round(ttl_min, 6),
        "expiry_policy": expiry_policy,
    }


def build_plan(row: dict[str, Any], *, notional: float, live_enabled: bool) -> dict[str, Any]:
    snapshot_price = float(row["yes_current_ask"])
    price = float(row.get("taker_limit_price") or snapshot_price)
    fresh_ask = float(row.get("fresh_best_ask") or snapshot_price)
    fresh_bid = float(row.get("fresh_best_bid") or 0.0)
    derived_min_edge = float(row.get("derived_min_edge_after_full_cushion") or 0.03)
    size = round(notional / price, 6)
    entry_profile = safe_str(row.get("entry_profile")) or "fade_confirmed"
    if entry_profile == "peak_forming_micro":
        combo = "current_yes_peak_forming_micro_v1"
        shadow_decision = "tiny_live_peak_forming_micro_v1"
        shadow_reason = "forecast_peak_current_high_micro_probe"
        entry_price_window = "0.50-0.97"
    else:
        combo = "current_yes_fade_confirmed_v9"
        shadow_decision = "tiny_live_confirmed_v9"
        shadow_reason = "significance_baseline_forward_execution_pass"
        entry_price_window = "0.55-1.00"
    base = {
        "strategy": "weather_edge_v1",
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "strategy_family": "theta_current_yes",
        "probability_source": safe_str(row.get("probability_source")) or "theta_current_yes_v8_train_logistic",
        "decision_mode": "current_running_max_yes_no_reheat",
        "entry_profile": entry_profile,
        "execution_mode": "tiny_live_taker",
        "profile": "weather_plus_price_train_period_frozen",
        "combo": combo,
        "city": row["city"],
        "city_pool": "source_aligned_theta",
        "target_date": row["target_date"],
        "market_id": row["market_id"],
        "event_slug": row["event_slug"],
        "question": row["question"],
        "bracket": row["current_bracket"],
        "token_id": row["token_id"],
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "market_price": round(price, 6),
        "best_bid": round(fresh_bid, 6),
        "best_ask": round(fresh_ask, 6),
        "spread": round(max(0.0, fresh_ask - fresh_bid), 6) if fresh_bid > 0 and fresh_ask > 0 else 0.0,
        "limit_price": round(price, 6),
        "quote_status": "accepted",
        "quote_reason": "theta_current_yes_fresh_book_guarded_taker",
        "quote_edge": round(float(row["p_yes_win"]) - price, 6),
        "required_quote_edge": round(derived_min_edge, 6),
        "model_token_probability": round(float(row["p_yes_win"]), 6),
        "quote_best_bid": round(fresh_bid, 6),
        "quote_best_ask": round(fresh_ask, 6),
        "quote_spread": round(max(0.0, fresh_ask - fresh_bid), 6) if fresh_bid > 0 and fresh_ask > 0 else 0.0,
        "quote_tick_size": 0.001,
        "quote_mode": "fresh_book_guarded_taker",
        "child_order_role": "single",
        "maker_only": False,
        "notional_fraction": 1.0,
        "size_multiplier": 1.0,
        "order_notional_cap": round(notional, 6),
        "size": size,
        "notional": round(size * price, 6),
        "execution_policy": "theta_current_yes_taker_v1",
        "tick_size": 0.001,
        "entry_price_window": entry_price_window,
        "sizing_mode": "notional",
        "fixed_order_shares": 0.0,
        "max_order_shares": size,
        "edge": round(float(row["p_yes_win"]) - price, 6),
        "min_edge": round(derived_min_edge, 6),
        "model_p_yes_used": round(float(row["p_yes_win"]), 6),
        "market_implied_p_yes": round(price, 6),
        "shadow_decision": shadow_decision,
        "shadow_reason": shadow_reason,
        "obs_source": safe_str((row.get("obs") or {}).get("source")),
        "model_version": safe_str(row.get("model_version")) or "theta_current_yes_v8_train_logistic",
        "probability_branch": safe_str(row.get("probability_branch")) or "base_current_yes_model",
        "p_yes_win_base_current_yes_model": row.get("p_yes_win_base_current_yes_model"),
        "p_yes_win_fade_confirmed_specialist": row.get("p_yes_win_fade_confirmed_specialist"),
        "fade_confirmed_specialist_delta": row.get("fade_confirmed_specialist_delta"),
        "paper_enabled": True,
        "live_enabled": bool(live_enabled),
        "snapshot_yes_ask": round(snapshot_price, 6),
        "fresh_best_ask": round(fresh_ask, 6),
        "fresh_best_bid": round(fresh_bid, 6),
        "available_notional_at_ask": round(float(row.get("fresh_available_notional") or row["available_notional_at_ask"]), 6),
        "taker_max_price": round(float(row.get("taker_max_price") or price), 6),
        "taker_cushion_paid_vs_snapshot": round(float(row.get("taker_cushion_paid_vs_snapshot") or 0.0), 6),
        "derived_min_edge_after_full_cushion": round(derived_min_edge, 6),
        "expected_profit_usd_model": round(float(row.get("expected_profit_usd") or 0.0), 6),
        "d1_no_ask": round(float(row["d1_no_ask"]), 6),
        "d1_no_bracket": row["d1_no_bracket"],
        "decline_c": round(float(row["decline_c"]), 6),
        "obs_age_min": round(float((row.get("obs") or {}).get("age_min") or 0.0), 6),
        "minutes_to_next_obs": round(float((row.get("obs") or {}).get("minutes_to_next_obs") or -1.0), 6),
        "running_max_obs_utc": safe_str((row.get("obs") or {}).get("running_max_obs_utc")),
        "minutes_since_running_max": round(float((row.get("obs") or {}).get("minutes_since_running_max") or -1.0), 6),
        "gap_running_to_d1_low_c": round(float(row.get("gap_running_to_d1_low_c") or 0.0), 6),
        "forecast_source": safe_str(row.get("forecast_source")),
        "forecast_max_f": row.get("forecast_max_f"),
        "forecast_max_native": row.get("forecast_max_native"),
        "forecast_peak_hour_local": row.get("forecast_peak_hour_local"),
        "forecast_peak_time_local": row.get("forecast_peak_time_local"),
        "forecast_peak_hour_utc": row.get("forecast_peak_hour_utc"),
        "forecast_peak_time_utc": row.get("forecast_peak_time_utc"),
        "forecast_peak_source": row.get("forecast_peak_source"),
        "forecast_values_hash": row.get("forecast_values_hash"),
        "forecast_timezone": row.get("forecast_timezone"),
        "forecast_utc_offset_seconds": row.get("forecast_utc_offset_seconds"),
        "forecast_hourly_count": row.get("forecast_hourly_count"),
        "forecast_peak_fetch_status": row.get("forecast_peak_fetch_status"),
        "forecast_peak_cache_path": row.get("forecast_peak_cache_path"),
        "forecast_peak_delta_hours_local": forecast_peak_delta(row),
        "decision_hour_local": int(row["decision_hour_local"]),
        "decision_local_time": safe_str(row.get("local_time")),
        "decision_timezone": safe_str(row.get("timezone")),
        "snapshot_ts_utc": safe_str(row.get("snapshot_ts_utc")),
        "source_snapshot_path": safe_str(row.get("snapshot_path")),
        **order_expiry_fields(row),
    }
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": stable_hash(base),
        "signal_id": stable_hash({"strategy_instance": STRATEGY_INSTANCE, "city": row["city"], "target_date": row["target_date"], "bracket": row["current_bracket"]}),
        "created_at_utc": now_utc(),
        "status": "accepted",
        "risk_status": "passed",
        "risk_reason": "",
        **base,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def entry_profile_enabled(profile: str, args: argparse.Namespace) -> bool:
    mode = safe_str(getattr(args, "entry_profile_mode", "both")) or "both"
    return mode == "both" or mode == profile


def native_temp_to_c(value: float, unit: str) -> float:
    return (value - 32.0) * 5.0 / 9.0 if safe_str(unit).upper() == "F" else value


def peak_forming_metar_veto_reason(row: dict[str, Any], args: argparse.Namespace) -> str:
    if getattr(args, "disable_peak_forming_metar_veto", False):
        return ""

    obs = row.get("obs") if isinstance(row.get("obs"), dict) else {}
    minutes_since_running_max = to_float(
        row.get("minutes_since_running_max") or obs.get("minutes_since_running_max"),
        np.nan,
    )
    min_minutes = float(getattr(args, "peak_forming_min_minutes_since_running_max", 10.0))
    if math.isfinite(minutes_since_running_max) and minutes_since_running_max < min_minutes:
        return "snapshot_rule_peak_forming_fresh_running_max"

    return ""


def classify_entry_profile(row: dict[str, Any], args: argparse.Namespace) -> tuple[str, str]:
    fade_gate = fade_gate_spec_from_args(args)
    decline = to_float(row.get("decline_c"), 0.0)
    ask = to_float(row.get("yes_current_ask"), 0.0)
    p_yes = to_float(row.get("p_yes_win"), 0.0)
    edge = to_float(row.get("ev"), -999.0)
    if not safe_str(row.get("token_id")):
        return "snapshot_rule_missing_token", ""

    if decline >= fade_gate.min_decline_c:
        reject_reason = fade_gate_reject_reason(
            {"yes_current_ask": ask, "p_yes_win": p_yes, "ev": edge},
            fade_gate,
        )
        if reject_reason:
            return reject_reason, ""
        if not entry_profile_enabled("fade_confirmed", args):
            return "snapshot_rule_fade_confirmed_disabled", ""
        return "snapshot_rule_passed", "fade_confirmed"

    if not getattr(args, "enable_peak_forming_live", False):
        return "snapshot_rule_decline_lt_0_5", ""
    if decline > float(getattr(args, "peak_forming_max_decline_c", 0.25)):
        return "snapshot_rule_peak_forming_decline_gt_max", ""
    if ask < float(getattr(args, "peak_forming_min_ask", 0.50)):
        return "snapshot_rule_peak_forming_ask_lt_min", ""
    if ask > float(getattr(args, "peak_forming_max_ask", 0.97)):
        return "snapshot_rule_peak_forming_ask_gt_max", ""
    if p_yes < float(getattr(args, "peak_forming_min_p", 0.60)):
        return "snapshot_rule_peak_forming_p_lt_min", ""
    if edge < float(getattr(args, "peak_forming_min_edge", 0.02)):
        return "snapshot_rule_peak_forming_edge_lt_min", ""
    metar_veto = peak_forming_metar_veto_reason(row, args)
    if metar_veto:
        return metar_veto, ""
    if not entry_profile_enabled("peak_forming_micro", args):
        return "snapshot_rule_peak_forming_disabled", ""
    return "snapshot_rule_passed", "peak_forming_micro"


def first_rule_reject_reason(row: dict[str, Any], args: argparse.Namespace) -> str:
    status, _profile = classify_entry_profile(row, args)
    return status


def source_profile_fields(city: str, profiles: dict[str, Any]) -> dict[str, Any]:
    profile = profiles.get(city)
    if profile is None:
        return {
            "source_profile_found": False,
            "source_profile_class": "",
            "source_profile_primary_source": "",
            "source_profile_station_or_feed": "",
            "source_profile_live_eligible": None,
        }
    return {
        "source_profile_found": True,
        "source_profile_class": profile.settlement_source_class,
        "source_profile_primary_source": profile.primary_source,
        "source_profile_station_or_feed": profile.official_station_or_feed,
        "source_profile_configured_icao": profile.configured_icao,
        "source_profile_mapping_rule": profile.mapping_rule,
        "source_profile_live_eligible": profile.live_eligible,
        "source_profile_blocked_reason": profile.blocked_reason,
        "source_profile_alignment_days": profile.alignment_days,
        "source_profile_alignment_rate": profile.alignment_rate,
    }


def forecast_peak_delta(row: dict[str, Any]) -> float | None:
    raw = row.get("forecast_peak_delta_hours_local")
    try:
        if raw not in (None, ""):
            return float(raw)
    except Exception:
        pass
    peak = to_float(row.get("forecast_peak_hour_local"), np.nan)
    hour = to_float(row.get("decision_hour_local"), np.nan)
    if math.isfinite(peak) and math.isfinite(hour):
        return hour - peak
    return None


def current_yes_forward_telemetry_row(
    row: dict[str, Any],
    *,
    decision_status: str,
    args: argparse.Namespace,
    run_id: str,
    snapshot_path: Path,
    snapshot_age_min: float,
    source_profiles: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    obs = row.get("obs") or {}
    limit_price = to_float(row.get("taker_limit_price"), to_float(row.get("yes_current_ask"), 0.0))
    fresh_ask = to_float(row.get("fresh_best_ask"), to_float(row.get("fresh_ask"), 0.0))
    fresh_bid = to_float(row.get("fresh_best_bid"), to_float(row.get("best_bid"), 0.0))
    p_yes = to_float(row.get("p_yes_win"), np.nan)
    expected_profit = (
        float(args.max_order_notional) * (p_yes / limit_price - 1.0)
        if math.isfinite(p_yes) and limit_price > 0
        else None
    )
    base = {
        "record_type": "theta_current_yes_forward_telemetry",
        "telemetry_version": 1,
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "created_at_utc": now_utc(),
        "telemetry_run_id": run_id,
        "decision_status": decision_status,
        "entry_profile": safe_str(row.get("entry_profile")),
        "city": safe_str(row.get("city")),
        "target_date": safe_str(row.get("target_date")),
        "local_date": safe_str(row.get("local_date")),
        "current_bracket": safe_str(row.get("current_bracket")),
        "d1_no_bracket": safe_str(row.get("d1_no_bracket")),
        "token_id": safe_str(row.get("token_id")),
        "market_id": safe_str(row.get("market_id")),
        "event_slug": safe_str(row.get("event_slug")),
        "decision_local_time": safe_str(row.get("local_time")),
        "decision_timezone": safe_str(row.get("timezone")),
        "decision_hour_local": int(to_float(row.get("decision_hour_local"), -1)),
        "snapshot_ts_utc": safe_str(row.get("snapshot_ts_utc")),
        "snapshot_path": str(snapshot_path),
        "snapshot_age_min": round(float(snapshot_age_min), 6),
        "obs_source": safe_str(obs.get("source")),
        "obs_age_min": to_float(obs.get("age_min"), np.nan),
        "obs_cadence_min": obs.get("cadence_min"),
        "minutes_to_next_obs": obs.get("minutes_to_next_obs"),
        "last_obs_utc": safe_str(obs.get("last_obs_utc")),
        "running_max_obs_utc": safe_str(obs.get("running_max_obs_utc")),
        "minutes_since_running_max": to_float(obs.get("minutes_since_running_max"), np.nan),
        "current_temp_c": to_float(obs.get("current_temp_c"), np.nan),
        "running_max_c": to_float(obs.get("running_max_c"), np.nan),
        "decline_c": to_float(row.get("decline_c"), np.nan),
        "tmpf_now": to_float(row.get("tmpf_now") or obs.get("tmpf_now"), np.nan),
        "dwpf_now": to_float(row.get("dwpf_now") or obs.get("dwpf_now"), np.nan),
        "dewpoint_depression_f": to_float(row.get("dewpoint_depression_f") or obs.get("dewpoint_depression_f"), np.nan),
        "relh_now": to_float(row.get("relh_now") or obs.get("relh_now"), np.nan),
        "drct_now": to_float(row.get("drct_now") or obs.get("drct_now"), np.nan),
        "sknt_now": to_float(row.get("sknt_now") or obs.get("sknt_now"), np.nan),
        "sky_now": to_float(row.get("sky_now") or obs.get("sky_now"), np.nan),
        "sky_1h": to_float(row.get("sky_1h") or obs.get("sky_1h"), np.nan),
        "sky_3h": to_float(row.get("sky_3h") or obs.get("sky_3h"), np.nan),
        "d_sky_1h": to_float(row.get("d_sky_1h") or obs.get("d_sky_1h"), np.nan),
        "d_sky_3h": to_float(row.get("d_sky_3h") or obs.get("d_sky_3h"), np.nan),
        "d_tmpf_1h": to_float(row.get("d_tmpf_1h") or obs.get("d_tmpf_1h"), np.nan),
        "d_tmpf_3h": to_float(row.get("d_tmpf_3h") or obs.get("d_tmpf_3h"), np.nan),
        "d_dwpf_3h": to_float(row.get("d_dwpf_3h") or obs.get("d_dwpf_3h"), np.nan),
        "d_relh_3h": to_float(row.get("d_relh_3h") or obs.get("d_relh_3h"), np.nan),
        "gap_running_to_d1_low_c": to_float(row.get("gap_running_to_d1_low_c"), np.nan),
        "yes_current_ask": to_float(row.get("yes_current_ask"), np.nan),
        "yes_current_size": to_float(row.get("yes_current_size"), np.nan),
        "available_notional_at_ask": to_float(row.get("available_notional_at_ask"), np.nan),
        "d1_no_ask": to_float(row.get("d1_no_ask"), np.nan),
        "p_yes_win": p_yes,
        "p_yes_win_base_current_yes_model": to_float(row.get("p_yes_win_base_current_yes_model"), np.nan),
        "p_yes_win_fade_confirmed_specialist": to_float(row.get("p_yes_win_fade_confirmed_specialist"), np.nan),
        "fade_confirmed_specialist_delta": to_float(row.get("fade_confirmed_specialist_delta"), np.nan),
        "probability_source": safe_str(row.get("probability_source")),
        "probability_branch": safe_str(row.get("probability_branch")),
        "model_version": safe_str(row.get("model_version")),
        "snapshot_edge": to_float(row.get("ev"), np.nan),
        "fresh_best_bid": fresh_bid,
        "fresh_best_ask": fresh_ask,
        "fresh_ask_size": to_float(row.get("fresh_ask_size"), np.nan),
        "fresh_available_notional": to_float(row.get("fresh_available_notional"), np.nan),
        "taker_limit_price": limit_price,
        "taker_max_price": to_float(row.get("taker_max_price"), np.nan),
        "edge_at_fresh_ask": to_float(row.get("edge_at_fresh_ask"), np.nan),
        "edge_at_limit": to_float(row.get("edge_at_limit"), np.nan),
        "expected_profit_usd_model": expected_profit,
        "forecast_source": safe_str(row.get("forecast_source")),
        "forecast_max_f": row.get("forecast_max_f"),
        "forecast_max_native": row.get("forecast_max_native"),
        "forecast_peak_hour_local": row.get("forecast_peak_hour_local"),
        "forecast_peak_time_local": row.get("forecast_peak_time_local"),
        "forecast_peak_hour_utc": row.get("forecast_peak_hour_utc"),
        "forecast_peak_time_utc": row.get("forecast_peak_time_utc"),
        "forecast_peak_source": row.get("forecast_peak_source"),
        "forecast_values_hash": row.get("forecast_values_hash"),
        "forecast_timezone": row.get("forecast_timezone"),
        "forecast_utc_offset_seconds": row.get("forecast_utc_offset_seconds"),
        "forecast_hourly_count": row.get("forecast_hourly_count"),
        "forecast_peak_fetch_status": row.get("forecast_peak_fetch_status"),
        "forecast_peak_cache_path": row.get("forecast_peak_cache_path"),
        "forecast_peak_error": row.get("forecast_peak_error"),
        "forecast_peak_delta_hours_local": forecast_peak_delta(row),
        "config": {
            "max_order_notional": float(args.max_order_notional),
            "max_city_day_notional": float(args.max_city_day_notional),
            "min_available_notional": float(args.min_available_notional),
            "max_taker_cushion": float(args.max_taker_cushion),
            "cross_tick_buffer": float(args.cross_tick_buffer),
            "max_obs_age_min": float(args.max_obs_age_min),
            "pre_metar_update_blackout_min": float(args.pre_metar_update_blackout_min),
            "min_gap_to_next_bracket_c": float(args.min_gap_to_next_bracket_c),
            "allow_missing_forecast_peak": bool(getattr(args, "allow_missing_forecast_peak", False)),
            "entry_profile_mode": safe_str(getattr(args, "entry_profile_mode", "both")) or "both",
            "fade_confirmed_model_mode": safe_str(getattr(args, "fade_confirmed_model_mode", "base")) or "base",
            "fade_confirmed_model_artifact": display_path(fade_confirmed_model_artifact_path(args)),
            "min_forecast_peak_delta_hours": float(getattr(args, "min_forecast_peak_delta_hours", -1.999)),
            "enable_peak_forming_live": bool(getattr(args, "enable_peak_forming_live", False)),
            "peak_forming_max_decline_c": float(getattr(args, "peak_forming_max_decline_c", 0.25)),
            "peak_forming_min_ask": float(getattr(args, "peak_forming_min_ask", 0.50)),
            "peak_forming_max_ask": float(getattr(args, "peak_forming_max_ask", 0.97)),
            "peak_forming_min_p": float(getattr(args, "peak_forming_min_p", 0.60)),
            "peak_forming_min_edge": float(getattr(args, "peak_forming_min_edge", 0.02)),
            "peak_forming_min_forecast_delta_hours": float(getattr(args, "peak_forming_min_forecast_delta_hours", -1.0)),
            "disable_peak_forming_metar_veto": bool(getattr(args, "disable_peak_forming_metar_veto", False)),
            "peak_forming_min_minutes_since_running_max": float(getattr(args, "peak_forming_min_minutes_since_running_max", 10.0)),
            "peak_forming_forecast_bust_margin_c": float(getattr(args, "peak_forming_forecast_bust_margin_c", 0.1)),
            "peak_forming_cloud_clearing_min_drop": float(getattr(args, "peak_forming_cloud_clearing_min_drop", 2.0)),
            "peak_forming_warming_trend_min_d_tmpf_3h": float(getattr(args, "peak_forming_warming_trend_min_d_tmpf_3h", 1.5)),
            "min_local_hour": int(args.min_local_hour),
            "max_local_hour": int(args.max_local_hour),
        },
    }
    base.update(source_profile_fields(safe_str(row.get("city")), source_profiles))
    if extra:
        base.update(extra)
    return json_ready(base)


def current_yes_audit_telemetry_row(
    audit: dict[str, Any],
    *,
    args: argparse.Namespace,
    run_id: str,
    snapshot: dict[str, Any],
    snapshot_path: Path,
    snapshot_age_min: float,
    source_profiles: dict[str, Any],
) -> dict[str, Any]:
    obs = audit.get("obs") or {}
    city = safe_str(audit.get("city"))
    row = {
        "record_type": "theta_current_yes_forward_telemetry",
        "telemetry_version": 1,
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "created_at_utc": now_utc(),
        "telemetry_run_id": run_id,
        "decision_status": safe_str(audit.get("status")) or "audit_blocked",
        "city": city,
        "target_date": safe_str(audit.get("target_date")),
        "local_date": safe_str(audit.get("local_date")),
        "available_target_dates": audit.get("available_target_dates") or [],
        "available_target_date_count": audit.get("available_target_date_count"),
        "available_market_local_dates": audit.get("available_market_local_dates") or [],
        "nearest_target_date": safe_str(audit.get("nearest_target_date")),
        "nearest_target_date_delta_days": audit.get("nearest_target_date_delta_days"),
        "current_bracket": safe_str(audit.get("current_bracket")),
        "d1_no_bracket": safe_str(audit.get("d1_bracket") or audit.get("d1_no_bracket")),
        "decision_local_time": safe_str(audit.get("local_time")),
        "decision_timezone": safe_str(audit.get("timezone") or obs.get("timezone")),
        "decision_hour_local": int(to_float(audit.get("hour_local"), -1)),
        "snapshot_ts_utc": safe_str(snapshot.get("ts_utc")),
        "snapshot_path": str(snapshot_path),
        "snapshot_age_min": round(float(snapshot_age_min), 6),
        "obs_source": safe_str(obs.get("source")),
        "obs_age_min": to_float(obs.get("age_min"), np.nan),
        "obs_cadence_min": obs.get("cadence_min"),
        "minutes_to_next_obs": obs.get("minutes_to_next_obs"),
        "last_obs_utc": safe_str(obs.get("last_obs_utc")),
        "running_max_obs_utc": safe_str(obs.get("running_max_obs_utc")),
        "minutes_since_running_max": to_float(obs.get("minutes_since_running_max"), np.nan),
        "current_temp_c": to_float(obs.get("current_temp_c"), np.nan),
        "running_max_c": to_float(obs.get("running_max_c"), np.nan),
        "decline_c": to_float(obs.get("decline_c"), np.nan),
        "gap_running_to_d1_low_c": to_float(audit.get("gap_running_to_d1_low_c"), np.nan),
        "p_yes_win": None,
        "snapshot_edge": None,
        "config": {
            "max_order_notional": float(args.max_order_notional),
            "max_city_day_notional": float(args.max_city_day_notional),
            "min_available_notional": float(args.min_available_notional),
            "max_taker_cushion": float(args.max_taker_cushion),
            "cross_tick_buffer": float(args.cross_tick_buffer),
            "max_obs_age_min": float(args.max_obs_age_min),
            "pre_metar_update_blackout_min": float(args.pre_metar_update_blackout_min),
            "min_gap_to_next_bracket_c": float(args.min_gap_to_next_bracket_c),
            "allow_missing_forecast_peak": bool(getattr(args, "allow_missing_forecast_peak", False)),
            "entry_profile_mode": safe_str(getattr(args, "entry_profile_mode", "both")) or "both",
            "fade_confirmed_model_mode": safe_str(getattr(args, "fade_confirmed_model_mode", "base")) or "base",
            "fade_confirmed_model_artifact": display_path(fade_confirmed_model_artifact_path(args)),
            "min_forecast_peak_delta_hours": float(getattr(args, "min_forecast_peak_delta_hours", -1.999)),
            "enable_peak_forming_live": bool(getattr(args, "enable_peak_forming_live", False)),
            "peak_forming_max_decline_c": float(getattr(args, "peak_forming_max_decline_c", 0.25)),
            "peak_forming_min_ask": float(getattr(args, "peak_forming_min_ask", 0.50)),
            "peak_forming_max_ask": float(getattr(args, "peak_forming_max_ask", 0.97)),
            "peak_forming_min_p": float(getattr(args, "peak_forming_min_p", 0.60)),
            "peak_forming_min_edge": float(getattr(args, "peak_forming_min_edge", 0.02)),
            "peak_forming_min_forecast_delta_hours": float(getattr(args, "peak_forming_min_forecast_delta_hours", -1.0)),
            "min_local_hour": int(args.min_local_hour),
            "max_local_hour": int(args.max_local_hour),
        },
    }
    row.update(source_profile_fields(city, source_profiles))
    if audit.get("error"):
        row["error"] = safe_str(audit.get("error"))
    if audit.get("reason"):
        row["reason"] = safe_str(audit.get("reason"))
    return json_ready(row)


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        val = float(value)
        return None if not math.isfinite(val) else val
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "runtime/weather_edge_v1/live").mkdir(parents=True, exist_ok=True)
    decision_now = parse_utc(getattr(args, "now_utc", "")) or datetime.now(timezone.utc)
    state = read_live_state(ROOT / "runtime/weather_edge_v1/live_cycle")
    if state.get("paused"):
        result = {"generated_at_utc": now_utc(), "status": "paused", "reason": state.get("reason", "")}
        SUMMARY_OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        append_jsonl(HISTORY_OUT, result)
        return result

    snap_path = Path(args.snapshot) if args.snapshot else latest_snapshot()
    if snap_path is None:
        raise RuntimeError("no paper snapshot found")
    snapshot, records = snapshot_records(snap_path)
    snapshot_ts = parse_utc(snapshot.get("ts_utc")) or datetime.now(timezone.utc)
    snapshot_freshness_ts, snapshot_freshness_source = snapshot_freshness_time(snapshot, snap_path)
    snapshot_file_mtime = datetime.fromtimestamp(snap_path.stat().st_mtime, timezone.utc)
    age_min = (datetime.now(timezone.utc) - snapshot_freshness_ts).total_seconds() / 60.0
    telemetry_run_id = stable_hash(
        {
            "strategy_instance": STRATEGY_INSTANCE,
            "snapshot_ts_utc": snapshot.get("ts_utc"),
            "snapshot_freshness_ts_utc": snapshot_freshness_ts.isoformat(),
            "snapshot": str(snap_path),
            "created_at_utc": now_utc(),
        }
    )
    if age_min > args.max_snapshot_age_min:
        result = {
            "generated_at_utc": now_utc(),
            "status": "stale_snapshot",
            "snapshot": str(snap_path),
            "snapshot_ts_utc": snapshot.get("ts_utc"),
            "snapshot_freshness_ts_utc": snapshot_freshness_ts.isoformat(),
            "snapshot_freshness_source": snapshot_freshness_source,
            "snapshot_file_mtime_utc": snapshot_file_mtime.isoformat(),
            "age_min": round(age_min, 1),
            "max_snapshot_age_min": args.max_snapshot_age_min,
        }
        SUMMARY_OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        append_jsonl(HISTORY_OUT, result)
        return result

    observation_cache, observation_cache_path, observation_cache_status = load_latest_observation_cache(
        safe_str(getattr(args, "observation_cache", ""))
    )
    model_artifact = load_model_artifact()
    fade_model_path = fade_confirmed_model_artifact_path(args)
    needs_fade_model = (safe_str(getattr(args, "entry_profile_mode", "both")) or "both") != "peak_forming_micro"
    fade_model_artifact = load_model_artifact(fade_model_path) if needs_fade_model and fade_model_path.exists() else None
    source_profiles = load_source_profiles()
    current, audits = build_current_rows(
        snapshot,
        records,
        load_stations(),
        decision_now,
        source_profiles=source_profiles,
        observation_cache=observation_cache,
        max_obs_age_min=args.max_obs_age_min,
        pre_update_blackout_min=args.pre_metar_update_blackout_min,
        min_gap_to_next_bracket_c=args.min_gap_to_next_bracket_c,
        min_local_hour=args.min_local_hour,
        max_local_hour=args.max_local_hour,
    )
    candidates: list[dict[str, Any]] = []
    telemetry_rows: list[dict[str, Any]] = [
        current_yes_audit_telemetry_row(
            audit,
            args=args,
            run_id=telemetry_run_id,
            snapshot=snapshot,
            snapshot_path=snap_path,
            snapshot_age_min=age_min,
            source_profiles=source_profiles,
        )
        for audit in audits
    ]
    if not current.empty:
        current = apply_probability_branch_scores(
            current,
            args=args,
            base_artifact=model_artifact,
            fade_artifact=fade_model_artifact,
        )
        current["ev"] = current["p_yes_win"] - current["yes_current_ask"]
        current["available_notional_at_ask"] = current["yes_current_ask"] * current["yes_current_size"]
        classifications = current.apply(lambda item: classify_entry_profile(item.to_dict(), args), axis=1)
        statuses = classifications.apply(lambda item: item[0])
        current["entry_profile"] = classifications.apply(lambda item: item[1])
        for idx, status in statuses.items():
            if status != "snapshot_rule_passed":
                telemetry_rows.append(
                    current_yes_forward_telemetry_row(
                        current.loc[idx].to_dict(),
                        decision_status=status,
                        args=args,
                        run_id=telemetry_run_id,
                        snapshot_path=snap_path,
                        snapshot_age_min=age_min,
                        source_profiles=source_profiles,
                    )
                )
        selected = current[statuses.eq("snapshot_rule_passed")].copy()
        prior = prior_city_day_notional(STRATEGY_INSTANCE)
        prior_epochs = prior_observation_epoch_keys(STRATEGY_INSTANCE)
        prior_signal_keys = prior_strategy_signal_keys(STRATEGY_INSTANCE)
        for _, row in selected.sort_values(["ev", "available_notional_at_ask"], ascending=[False, False]).iterrows():
            key = (str(row["city"]), str(row["target_date"]))
            row_dict = row.to_dict()
            signal_key = strategy_signal_key(row_dict)
            if all(signal_key) and signal_key in prior_signal_keys:
                audits.append(
                    {
                        "city": key[0],
                        "target_date": key[1],
                        "status": "strategy_signal_cap",
                        "token_or_market": signal_key[2],
                        "bracket": signal_key[3],
                    }
                )
                telemetry_rows.append(
                    current_yes_forward_telemetry_row(
                        row_dict,
                        decision_status="strategy_signal_cap",
                        args=args,
                        run_id=telemetry_run_id,
                        snapshot_path=snap_path,
                        snapshot_age_min=age_min,
                        source_profiles=source_profiles,
                        extra={"strategy_signal_key": "|".join(signal_key)},
                    )
                )
                continue
            epoch_key = observation_epoch_key(row_dict)
            if all(epoch_key) and epoch_key in prior_epochs:
                audits.append(
                    {
                        "city": key[0],
                        "target_date": key[1],
                        "status": "observation_epoch_cap",
                        "bracket": epoch_key[2],
                        "running_max_obs_utc": epoch_key[3],
                    }
                )
                telemetry_rows.append(
                    current_yes_forward_telemetry_row(
                        row_dict,
                        decision_status="observation_epoch_cap",
                        args=args,
                        run_id=telemetry_run_id,
                        snapshot_path=snap_path,
                        snapshot_age_min=age_min,
                        source_profiles=source_profiles,
                        extra={"observation_epoch_key": "|".join(epoch_key)},
                    )
                )
                continue
            try:
                taker_quote = fresh_taker_quote(row_dict, args)
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                audits.append({"city": key[0], "target_date": key[1], "status": "fresh_book_fetch_failed", "error": error})
                telemetry_rows.append(
                    current_yes_forward_telemetry_row(
                        row_dict,
                        decision_status="fresh_book_fetch_failed",
                        args=args,
                        run_id=telemetry_run_id,
                        snapshot_path=snap_path,
                        snapshot_age_min=age_min,
                        source_profiles=source_profiles,
                        extra={"fresh_book_error": error},
                    )
                )
                continue
            if taker_quote.get("status") != "accepted":
                audits.append({"city": key[0], "target_date": key[1], "status": taker_quote.get("reason", "fresh_book_rejected"), **taker_quote})
                row_dict.update(
                    {
                        "fresh_best_bid": taker_quote.get("best_bid", 0.0),
                        "fresh_best_ask": taker_quote.get("fresh_ask", 0.0),
                        "fresh_ask_size": taker_quote.get("fresh_ask_size", 0.0),
                        "fresh_available_notional": taker_quote.get("fresh_available_notional", 0.0),
                        "taker_max_price": taker_quote.get("max_taker_price", 0.0),
                        "edge_at_fresh_ask": taker_quote.get("edge_at_fresh_ask", np.nan),
                        "edge_at_limit": taker_quote.get("edge_at_limit", np.nan),
                        "required_quote_edge": taker_quote.get("required_quote_edge", np.nan),
                    }
                )
                telemetry_rows.append(
                    current_yes_forward_telemetry_row(
                        row_dict,
                        decision_status=safe_str(taker_quote.get("reason")) or "fresh_book_rejected",
                        args=args,
                        run_id=telemetry_run_id,
                        snapshot_path=snap_path,
                        snapshot_age_min=age_min,
                        source_profiles=source_profiles,
                    )
                )
                continue
            row_dict.update(
                {
                    "fresh_best_bid": taker_quote.get("best_bid", 0.0),
                    "fresh_best_ask": taker_quote["fresh_ask"],
                    "fresh_ask_size": taker_quote["fresh_ask_size"],
                    "fresh_available_notional": taker_quote["fresh_available_notional"],
                    "taker_max_price": taker_quote["max_taker_price"],
                    "taker_limit_price": taker_quote["limit_price"],
                    "edge_at_fresh_ask": taker_quote["edge_at_fresh_ask"],
                    "edge_at_limit": taker_quote["edge_at_limit"],
                    "required_quote_edge": taker_quote.get(
                        "required_quote_edge",
                        taker_quote.get("derived_min_edge_after_full_cushion", required_fresh_quote_edge(row_dict, args)),
                    ),
                    "expected_profit_usd": taker_quote["expected_profit_usd"],
                    "derived_min_edge_after_full_cushion": taker_quote["derived_min_edge_after_full_cushion"],
                    "taker_cushion_paid_vs_snapshot": taker_quote["cushion_paid_vs_snapshot"],
                }
            )
            if prior.get(key, 0.0) + args.max_order_notional > args.max_city_day_notional + 1e-9:
                audits.append({"city": key[0], "target_date": key[1], "status": "city_day_cap", "prior_notional": prior.get(key, 0.0)})
                telemetry_rows.append(
                    current_yes_forward_telemetry_row(
                        row_dict,
                        decision_status="city_day_cap",
                        args=args,
                        run_id=telemetry_run_id,
                        snapshot_path=snap_path,
                        snapshot_age_min=age_min,
                        source_profiles=source_profiles,
                        extra={"prior_notional": prior.get(key, 0.0)},
                    )
                )
                continue
            if len(candidates) >= args.max_orders:
                audits.append({"city": key[0], "target_date": key[1], "status": "max_orders_reached"})
                telemetry_rows.append(
                    current_yes_forward_telemetry_row(
                        row_dict,
                        decision_status="max_orders_reached",
                        args=args,
                        run_id=telemetry_run_id,
                        snapshot_path=snap_path,
                        snapshot_age_min=age_min,
                        source_profiles=source_profiles,
                    )
                )
                continue
            candidates.append(row_dict)
            telemetry_rows.append(
                current_yes_forward_telemetry_row(
                    row_dict,
                    decision_status="planned",
                    args=args,
                    run_id=telemetry_run_id,
                    snapshot_path=snap_path,
                    snapshot_age_min=age_min,
                    source_profiles=source_profiles,
                )
            )
            prior[key] = prior.get(key, 0.0) + args.max_order_notional
            if all(epoch_key):
                prior_epochs.add(epoch_key)
            if all(signal_key):
                prior_signal_keys.add(signal_key)

    live_enabled = bool(args.live and args.confirm_live)
    plans = [build_plan(row, notional=args.max_order_notional, live_enabled=live_enabled) for row in candidates]
    write_jsonl(PLAN_OUT, plans)
    for telemetry_row in telemetry_rows:
        append_jsonl(FORWARD_TELEMETRY_OUT, telemetry_row)
    result = {
        "generated_at_utc": now_utc(),
        "status": "planned",
        "strategy_instance": STRATEGY_INSTANCE,
        "snapshot": str(snap_path),
        "snapshot_dir": str(snapshot_dir()),
        "snapshot_ts_utc": snapshot.get("ts_utc"),
        "snapshot_freshness_ts_utc": snapshot_freshness_ts.isoformat(),
        "snapshot_freshness_source": snapshot_freshness_source,
        "snapshot_file_mtime_utc": snapshot_file_mtime.isoformat(),
        "snapshot_age_min": round(age_min, 1),
        "decision_now_utc": decision_now.isoformat(),
        "observation_cache_path": str(observation_cache_path) if observation_cache_path else "",
        "observation_cache_status": observation_cache_status,
        "observation_cache_generated_at_utc": safe_str((observation_cache or {}).get("generated_at_utc")),
        "current_rows": int(0 if current.empty else len(current)),
        "candidate_rows": len(candidates),
        "plans": len(plans),
        "live_enabled": live_enabled,
        "caps": {
            "max_order_notional": args.max_order_notional,
            "max_city_day_notional": args.max_city_day_notional,
            "min_available_notional": args.min_available_notional,
            "max_taker_cushion": args.max_taker_cushion,
            "cross_tick_buffer": args.cross_tick_buffer,
            "max_obs_age_min": args.max_obs_age_min,
            "pre_metar_update_blackout_min": args.pre_metar_update_blackout_min,
            "min_gap_to_next_bracket_c": args.min_gap_to_next_bracket_c,
            "allow_missing_forecast_peak": bool(getattr(args, "allow_missing_forecast_peak", False)),
            "entry_profile_mode": safe_str(getattr(args, "entry_profile_mode", "both")) or "both",
            "fade_confirmed_model_mode": safe_str(getattr(args, "fade_confirmed_model_mode", "base")) or "base",
            "fade_confirmed_model_artifact": display_path(fade_model_path),
            "fade_confirmed_model_loaded": fade_model_artifact is not None,
            "min_forecast_peak_delta_hours": float(getattr(args, "min_forecast_peak_delta_hours", -1.999)),
            "enable_peak_forming_live": bool(getattr(args, "enable_peak_forming_live", False)),
            "peak_forming_max_decline_c": float(getattr(args, "peak_forming_max_decline_c", 0.25)),
            "peak_forming_min_ask": float(getattr(args, "peak_forming_min_ask", 0.50)),
            "peak_forming_max_ask": float(getattr(args, "peak_forming_max_ask", 0.97)),
            "peak_forming_min_p": float(getattr(args, "peak_forming_min_p", 0.60)),
            "peak_forming_min_edge": float(getattr(args, "peak_forming_min_edge", 0.02)),
            "peak_forming_min_forecast_delta_hours": float(getattr(args, "peak_forming_min_forecast_delta_hours", -1.0)),
            "disable_peak_forming_metar_veto": bool(getattr(args, "disable_peak_forming_metar_veto", False)),
            "peak_forming_min_minutes_since_running_max": float(getattr(args, "peak_forming_min_minutes_since_running_max", 10.0)),
            "peak_forming_forecast_bust_margin_c": float(getattr(args, "peak_forming_forecast_bust_margin_c", 0.1)),
            "peak_forming_cloud_clearing_min_drop": float(getattr(args, "peak_forming_cloud_clearing_min_drop", 2.0)),
            "peak_forming_warming_trend_min_d_tmpf_3h": float(getattr(args, "peak_forming_warming_trend_min_d_tmpf_3h", 1.5)),
            "min_local_hour": args.min_local_hour,
            "max_local_hour": args.max_local_hour,
            "max_orders": args.max_orders,
        },
        "plan_out": str(PLAN_OUT),
        "forward_telemetry_out": str(FORWARD_TELEMETRY_OUT),
        "forward_telemetry_rows": len(telemetry_rows),
        "live_out": str(LIVE_OUT),
        "candidates": [
            {
                "city": r["city"],
                "target_date": r["target_date"],
                "entry_profile": r.get("entry_profile"),
                "hour": int(r["decision_hour_local"]),
                "bracket": r["current_bracket"],
                "ask": round(float(r["yes_current_ask"]), 4),
                "fresh_ask": round(float(r["fresh_best_ask"]), 4),
                "limit_price": round(float(r["taker_limit_price"]), 4),
                "p_yes_win": round(float(r["p_yes_win"]), 4),
                "p_yes_win_base_current_yes_model": round(float(r.get("p_yes_win_base_current_yes_model") or np.nan), 4),
                "p_yes_win_fade_confirmed_specialist": round(float(r.get("p_yes_win_fade_confirmed_specialist") or np.nan), 4),
                "probability_branch": r.get("probability_branch"),
                "ev": round(float(r["ev"]), 4),
                "edge_at_limit": round(float(r["edge_at_limit"]), 4),
                "expected_profit_usd": round(float(r["expected_profit_usd"]), 4),
                "taker_cushion_paid_vs_snapshot": round(float(r["taker_cushion_paid_vs_snapshot"]), 4),
                "available_notional_at_ask": round(float(r["available_notional_at_ask"]), 4),
                "fresh_available_notional": round(float(r["fresh_available_notional"]), 4),
                "decline_c": round(float(r["decline_c"]), 3),
                "obs_age_min": round(float((r.get("obs") or {}).get("age_min") or 0.0), 1),
                "minutes_to_next_obs": round(float((r.get("obs") or {}).get("minutes_to_next_obs") or -1.0), 1),
                "minutes_since_running_max": round(float((r.get("obs") or {}).get("minutes_since_running_max") or -1.0), 1),
                "relh_now": round(float(r.get("relh_now") or np.nan), 1),
                "drct_now": round(float(r.get("drct_now") or np.nan), 1),
                "sknt_now": round(float(r.get("sknt_now") or np.nan), 1),
                "sky_now": round(float(r.get("sky_now") or np.nan), 1),
                "sky_1h": round(float(r.get("sky_1h") or np.nan), 1),
                "d_sky_1h": round(float(r.get("d_sky_1h") or np.nan), 1),
                "d_tmpf_3h": round(float(r.get("d_tmpf_3h") or np.nan), 1),
                "gap_running_to_d1_low_c": round(float(r.get("gap_running_to_d1_low_c") or 0.0), 3),
                "forecast_peak_hour_local": r.get("forecast_peak_hour_local"),
                "forecast_peak_delta_hours_local": forecast_peak_delta(r),
                "forecast_peak_fetch_status": r.get("forecast_peak_fetch_status"),
            }
            for r in candidates
        ],
        "audit_counts": dict(pd.Series([safe_str(a.get("status")) for a in audits]).value_counts()) if audits else {},
    }
    executor_result = None
    if args.live:
        if not args.confirm_live:
            raise RuntimeError("--live requires --confirm-live")
        cmd = [
            sys.executable,
            "scripts/ops/weather_order_executor.py",
            "--plans",
            str(PLAN_OUT),
            "--paper-out",
            str(PAPER_OUT),
            "--live-out",
            str(LIVE_OUT),
            "--live",
            "--confirm-live",
            "--allow-taker",
            "--cancel-expired",
        ]
        if args.no_telegram:
            cmd.append("--no-telegram")
        proc = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        result["executor_cmd"] = cmd
        result["executor_returncode"] = proc.returncode
        result["executor_output"] = proc.stdout[-8000:]
        try:
            start = proc.stdout.find("{")
            end = proc.stdout.rfind("}")
            executor_result = json.loads(proc.stdout[start : end + 1]) if start >= 0 and end > start else None
        except Exception:
            executor_result = None
        result["executor_result"] = executor_result
    result = json_ready(result)
    SUMMARY_OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_jsonl(HISTORY_OUT, result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "loop"])
    parser.add_argument("--snapshot")
    parser.add_argument("--observation-cache", default="")
    parser.add_argument("--now-utc", default="")
    parser.add_argument("--max-order-notional", type=float, default=3.0)
    parser.add_argument("--max-city-day-notional", type=float, default=3.0)
    parser.add_argument("--min-available-notional", type=float, default=0.0)
    parser.add_argument("--max-taker-cushion", type=float, default=0.02)
    parser.add_argument("--cross-tick-buffer", type=float, default=0.001)
    parser.add_argument("--max-orders", type=int, default=20)
    parser.add_argument("--max-snapshot-age-min", type=float, default=45.0)
    parser.add_argument("--max-obs-age-min", type=float, default=20.0)
    parser.add_argument("--pre-metar-update-blackout-min", type=float, default=6.0)
    parser.add_argument("--min-gap-to-next-bracket-c", type=float, default=0.0)
    parser.add_argument("--allow-missing-forecast-peak", action="store_true")
    parser.add_argument("--entry-profile-mode", choices=["both", "fade_confirmed", "peak_forming_micro"], default="both")
    parser.add_argument("--fade-confirmed-model-mode", choices=["base", "specialist"], default="base")
    parser.add_argument("--fade-confirmed-model-artifact", default=str(FADE_CONFIRMED_MODEL_ARTIFACT.relative_to(ROOT)))
    parser.add_argument("--min-forecast-peak-delta-hours", type=float, default=-1.999)
    parser.add_argument("--enable-peak-forming-live", action="store_true")
    parser.add_argument("--peak-forming-max-decline-c", type=float, default=0.25)
    parser.add_argument("--peak-forming-min-ask", type=float, default=0.50)
    parser.add_argument("--peak-forming-max-ask", type=float, default=0.97)
    parser.add_argument("--peak-forming-min-p", type=float, default=0.60)
    parser.add_argument("--peak-forming-min-edge", type=float, default=0.02)
    parser.add_argument("--peak-forming-min-forecast-delta-hours", type=float, default=-1.0)
    parser.add_argument("--disable-peak-forming-metar-veto", action="store_true")
    parser.add_argument("--peak-forming-min-minutes-since-running-max", type=float, default=10.0)
    parser.add_argument("--peak-forming-forecast-bust-margin-c", type=float, default=0.1)
    parser.add_argument("--peak-forming-clear-sky-max-code", type=float, default=1.0)
    parser.add_argument("--peak-forming-prior-cloud-min-code", type=float, default=3.0)
    parser.add_argument("--peak-forming-cloud-clearing-min-drop", type=float, default=2.0)
    parser.add_argument("--peak-forming-warming-trend-min-d-tmpf-3h", type=float, default=1.5)
    parser.add_argument("--min-local-hour", type=int, default=13)
    parser.add_argument("--max-local-hour", type=int, default=15)
    parser.add_argument("--interval-seconds", type=float, default=900.0)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ModuleNotFoundError:
        pass
    args = parse_args()
    if args.command == "run":
        print(json.dumps(run_once(args), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    while True:
        try:
            result = run_once(args)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        except Exception as exc:  # noqa: BLE001
            err = {"generated_at_utc": now_utc(), "status": "error", "error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(err, ensure_ascii=False, sort_keys=True), flush=True)
            append_jsonl(HISTORY_OUT, err)
        time.sleep(max(30.0, float(args.interval_seconds)))


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
