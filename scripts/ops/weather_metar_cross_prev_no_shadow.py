#!/usr/bin/env python3
"""METAR crossing shadow bot for previous-temperature NO taker tests.

Signal:
  When the official station running max first crosses threshold T, the exact
  T-1 bucket can no longer settle YES. This script checks whether the NO book
  for that bucket still has taker liquidity and records the opportunity.

Shadow only: no orders are placed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

OPS = Path(__file__).resolve().parent
ROOT = OPS.parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import weather_station_basis_shadow as source  # noqa: E402
from weather_data_feed.source_policy import (  # noqa: E402
    CityConfig,
    build_city_policy,
    load_city_configs,
)
from weather_data_feed.observation_sources import (  # noqa: E402
    normalize_source_name,
    parse_metar_report_time,
    parse_metar_temp_c,
    parse_tgftp_header_time,
    stable_hash,
)
from src.strategies.runtime.production import load_production_spec  # noqa: E402


DATA_ROOT = Path(os.environ.get("METAR_CROSS_DATA_ROOT") or os.environ.get("DATA_PROJECT_DIR") or ROOT)
OUT_DIR = DATA_ROOT / "runtime/weather_edge_v1/metar_cross_prev_no_shadow"
DEFAULT_SOURCE_EVENTS_LATEST = Path(
    os.environ.get("METAR_CROSS_SOURCE_EVENTS_PATH")
    or load_production_spec().source_events_root() / "latest.json"
)
FAST_HTTP_TIMEOUT_SEC = float(os.environ.get("METAR_CROSS_HTTP_TIMEOUT_SEC", "3.0"))
FAST_PROXY_MODE = os.environ.get("METAR_CROSS_PROXY_MODE", "direct").strip().lower()
WEATHER_PROXY_MODE = os.environ.get("METAR_CROSS_WEATHER_PROXY_MODE", "direct").strip().lower()
MARKET_PROXY_MODE = os.environ.get("METAR_CROSS_MARKET_PROXY_MODE", FAST_PROXY_MODE).strip().lower()
EXPLICIT_WEATHER_PROXY = os.environ.get("METAR_CROSS_WEATHER_PROXY", "").strip()
EXPLICIT_MARKET_PROXY = os.environ.get("METAR_CROSS_MARKET_PROXY", "").strip()
WRH_API_KEY_JS = "https://www.weather.gov/source/wrh/apiKey.js"
SYNOPTIC_TIMESERIES_API = "https://api.synopticdata.com/v2/stations/timeseries"
NOAA_TGFTP_STATION_TXT = "https://tgftp.nws.noaa.gov/data/observations/metar/stations/{icao}.TXT"
SYNOPTIC_TOKEN_RE = re.compile(r"['\"]([a-f0-9]{32})['\"]")
SYNOPTIC_TOKEN_CACHE: str | None = None


def proxy_candidates(mode: str, explicit_proxy: str = "") -> list[str | None]:
    if explicit_proxy:
        return [explicit_proxy]
    if mode == "all":
        return source.PROXIES
    if mode == "first":
        return source.PROXIES[:1]
    return [None]


WEATHER_PROXY_CANDIDATES = proxy_candidates(WEATHER_PROXY_MODE, EXPLICIT_WEATHER_PROXY)
MARKET_PROXY_CANDIDATES = proxy_candidates(MARKET_PROXY_MODE, EXPLICIT_MARKET_PROXY)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def market_value(temp_c: float, unit: str) -> int:
    if unit == "F":
        return source.round_half_up(temp_c * 9.0 / 5.0 + 32.0)
    return source.round_half_up(temp_c)


def crossed_prev_no_brackets(previous_value: int | None, current_value: int) -> list[int]:
    if previous_value is None or current_value <= previous_value:
        return []
    return [threshold - 1 for threshold in range(previous_value + 1, current_value + 1)]


def parsed_label_matches_no_target(parsed: dict[str, Any] | None, target: int) -> bool:
    if parsed is None or parsed.get("top"):
        return False
    low = parsed.get("low")
    high = parsed.get("high")
    if low is None:
        return high is not None and target <= float(high)
    if high is None:
        return False
    return float(low) <= target <= float(high)


def parsed_label_is_dead_for_running_value(parsed: dict[str, Any] | None, target: int, running_value: int) -> bool:
    """Only trade NO when the whole bracket is below the observed running max."""
    if parsed is None or parsed.get("top"):
        return False
    high = parsed.get("high")
    if high is None:
        return False
    try:
        high_int = int(float(high))
    except (TypeError, ValueError):
        return False
    return high_int == target and high_int < running_value


def load_state() -> dict[str, Any]:
    return read_json(OUT_DIR / "state.json", {"last_running_value": {}, "fired": []})


def save_state(state: dict[str, Any]) -> None:
    write_json(OUT_DIR / "state.json", state)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def elapsed_sec(start: datetime | None, end: datetime | None = None) -> float | None:
    if start is None:
        return None
    return round(((end or datetime.now(timezone.utc)) - start).total_seconds(), 3)


def load_source_by_city(*, json_text: str = "", file_path: str = "") -> dict[str, str]:
    payload: Any = {}
    if file_path:
        payload = read_json(Path(file_path), {})
    if json_text:
        payload = json.loads(json_text)
    if not isinstance(payload, dict):
        raise ValueError("source-by-city payload must be a JSON object")
    allowed = {"aviationweather_metar", "aviationweather_cache_csv", "synopticdata_timeseries", "noaa_tgftp_station_txt"}
    out = {}
    for city, source_name in payload.items():
        value = str(source_name).strip()
        if value not in allowed:
            raise ValueError(f"unsupported source for {city}: {value}")
        out[str(city)] = value
    return out


def source_for_city(cfg: CityConfig, default_source: str, source_by_city: dict[str, str] | None) -> str:
    return (source_by_city or {}).get(cfg.city, default_source)


def report_minute(value: str | None) -> float | None:
    dt = parse_dt(value)
    if dt is None:
        return None
    return dt.minute + dt.second / 60.0


def update_report_minute_state(state: dict[str, Any], city: str, obs_last_obs_utc: str | None) -> None:
    minute = report_minute(obs_last_obs_utc)
    if minute is None:
        return
    state.setdefault("last_report_minute_by_city", {})[city] = round(minute, 3)
    state.setdefault("last_report_ts_by_city", {})[city] = obs_last_obs_utc


def circular_minute_distance(a: float, b: float) -> float:
    raw = abs((a % 60.0) - (b % 60.0))
    return min(raw, 60.0 - raw)


def in_learned_update_window(now_utc: datetime, state: dict[str, Any], *, window_min: float) -> bool:
    minute = now_utc.minute + now_utc.second / 60.0
    for raw in (state.get("last_report_minute_by_city") or {}).values():
        try:
            if circular_minute_distance(minute, float(raw)) <= window_min:
                return True
        except (TypeError, ValueError):
            continue
    return False


def parse_report_minutes(raw: str) -> list[float]:
    out = []
    for item in str(raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        value = float(item)
        if value < 0 or value >= 60:
            raise ValueError(f"report minute out of range: {item}")
        out.append(value)
    return out


def minute_after_target(now_minute: float, target_minute: float) -> float:
    return (now_minute - target_minute) % 60.0


def minute_in_pre_chase_window(now_minute: float, target_minute: float, *, pre_window_min: float, chase_window_min: float) -> bool:
    after = minute_after_target(now_minute, target_minute)
    return after <= chase_window_min or after >= 60.0 - pre_window_min


def city_report_minutes(state: dict[str, Any], city: str, fallback_report_minutes: list[float]) -> list[float]:
    raw = (state.get("last_report_minute_by_city") or {}).get(city)
    if raw is None:
        return fallback_report_minutes
    try:
        return [float(raw)]
    except (TypeError, ValueError):
        return fallback_report_minutes


def in_city_update_window(
    now_utc: datetime,
    state: dict[str, Any],
    city: str,
    *,
    fallback_report_minutes: list[float],
    pre_window_min: float,
    chase_window_min: float,
) -> bool:
    now_minute = now_utc.minute + now_utc.second / 60.0
    for target in city_report_minutes(state, city, fallback_report_minutes):
        if minute_in_pre_chase_window(
            now_minute,
            target,
            pre_window_min=pre_window_min,
            chase_window_min=chase_window_min,
        ):
            return True
    return False


def last_report_ts_for_city(state: dict[str, Any], city: str) -> str | None:
    value = (state.get("last_report_ts_by_city") or {}).get(city)
    return None if value is None else str(value)


def city_base_delay_sec(city: str, base_interval_sec: float) -> float:
    if base_interval_sec <= 1:
        return max(0.1, base_interval_sec)
    bucket = sum(ord(ch) for ch in city) % 1000
    multiplier = 0.5 + bucket / 1000.0
    return round(max(1.0, base_interval_sec * multiplier), 3)


class RuntimeCache:
    def __init__(self, *, market_ttl_sec: float) -> None:
        self.market_ttl_sec = market_ttl_sec
        self.market_cache: dict[str, dict[str, Any]] = {}
        self.live_place_fn: Any | None = None

    def get_market_bundle(self, cfg: CityConfig, local_date: Any, *, now_utc: datetime) -> dict[str, Any]:
        event_slug = source.event_slug(cfg.slug, local_date)
        cached = self.market_cache.get(event_slug)
        if cached:
            fetched_at = parse_dt(cached.get("market_fetched_utc"))
            if fetched_at and (now_utc - fetched_at).total_seconds() <= self.market_ttl_sec:
                return {**cached, "market_cache_hit": True, "event_slug": event_slug}
        fetch_start = datetime.now(timezone.utc)
        events = source.fetch_json(
            f"{source.GAMMA}/events",
            {"slug": event_slug},
            max_rounds=1,
            timeout_sec=FAST_HTTP_TIMEOUT_SEC,
            proxy_candidates=MARKET_PROXY_CANDIDATES,
        )
        fetch_end = datetime.now(timezone.utc)
        markets = events[0].get("markets") if events else []
        desc = str(markets[0].get("description") or "") if markets else ""
        match = source.WU_URL_RE.search(desc)
        bundle = {
            "event_slug": event_slug,
            "markets": markets or [],
            "rules_icao": match.group(1) if match else None,
            "market_fetched_utc": fetch_end.isoformat(),
            "gamma_fetch_latency_sec": round((fetch_end - fetch_start).total_seconds(), 3),
            "market_cache_hit": False,
        }
        self.market_cache[event_slug] = bundle
        return bundle


def find_no_market(markets: list[dict[str, Any]], target_bracket: int) -> tuple[dict[str, Any], dict[str, Any]] | tuple[None, None]:
    for market in markets:
        label = str(market.get("groupItemTitle") or "")
        parsed = source.parse_label(label, str(market.get("question") or ""))
        if parsed_label_matches_no_target(parsed, target_bracket):
            return market, parsed
    return None, None


def find_dead_no_market(markets: list[dict[str, Any]], target_bracket: int, running_value: int) -> tuple[dict[str, Any], dict[str, Any]] | tuple[None, None]:
    for market in markets:
        label = str(market.get("groupItemTitle") or "")
        parsed = source.parse_label(label, str(market.get("question") or ""))
        if parsed_label_is_dead_for_running_value(parsed, target_bracket, running_value):
            return market, parsed
    return None, None


def parse_metar_records(data: list[dict[str, Any]], tz: ZoneInfo, local_date: Any) -> list[tuple[datetime, float]]:
    obs = []
    for rec in data:
        temp = rec.get("temp")
        ts = rec.get("reportTime")
        if temp is None or not ts:
            continue
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(tz).date() == local_date:
            obs.append((dt, float(temp)))
    return sorted(obs)


def in_update_window(now_utc: datetime, *, window_min: float) -> bool:
    minute = now_utc.minute + now_utc.second / 60.0
    distance_to_half_hour = min(abs(minute - 0.0), abs(minute - 30.0), abs(minute - 60.0))
    return distance_to_half_hour <= window_min


def aviationweather_metar_day_fast(icao: str, tz: ZoneInfo, local_date: Any) -> dict[str, Any]:
    data = source.fetch_json(
        source.METAR_API,
        {"ids": icao, "format": "json", "hours": "30"},
        max_rounds=1,
        timeout_sec=FAST_HTTP_TIMEOUT_SEC,
        proxy_candidates=WEATHER_PROXY_CANDIDATES,
    )
    obs = parse_metar_records(data, tz, local_date)
    if not obs:
        return {"status": "no_recent_obs", "source": "aviationweather_metar_day_fast", "n_obs": 0}
    last_dt, last_temp = obs[-1]
    now = datetime.now(timezone.utc)
    age_min = (now - last_dt).total_seconds() / 60.0
    return {
        "status": "ok",
        "source": "aviationweather_metar_day_fast",
        "n_obs": len(obs),
        "age_min": round(age_min, 1),
        "running_max_c": max(temp for _, temp in obs),
        "current_temp_c": last_temp,
        "last_obs_utc": last_dt.isoformat(),
    }


def recent_metar_summary(icao: str, tz: ZoneInfo, local_date: Any, *, hours: float) -> dict[str, Any]:
    data = source.fetch_json(
        source.METAR_API,
        {"ids": icao, "format": "json", "hours": str(hours)},
        max_rounds=1,
        timeout_sec=FAST_HTTP_TIMEOUT_SEC,
        proxy_candidates=WEATHER_PROXY_CANDIDATES,
    )
    obs = parse_metar_records(data, tz, local_date)
    if not obs:
        return {"status": "no_recent_obs", "source": "aviationweather_metar_recent", "n_obs": 0}
    last_dt, last_temp = obs[-1]
    now = datetime.now(timezone.utc)
    age_min = (now - last_dt).total_seconds() / 60.0
    return {
        "status": "ok",
        "source": "aviationweather_metar_recent",
        "n_obs": len(obs),
        "age_min": round(age_min, 1),
        "running_max_c": max(temp for _, temp in obs),
        "current_temp_c": last_temp,
        "last_obs_utc": last_dt.isoformat(),
    }


def noaa_tgftp_station_txt_latest(icao: str, previous_value: Any) -> dict[str, Any]:
    text = source.fetch_text(
        NOAA_TGFTP_STATION_TXT.format(icao=icao),
        max_rounds=1,
        timeout_sec=FAST_HTTP_TIMEOUT_SEC,
        proxy_candidates=WEATHER_PROXY_CANDIDATES,
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    raw_metar = lines[1] if len(lines) > 1 else ""
    header_dt = parse_tgftp_header_time(text)
    report_dt = parse_metar_report_time(raw_metar, header_dt)
    temp_c = parse_metar_temp_c(raw_metar) if raw_metar else None
    if not raw_metar or report_dt is None or temp_c is None:
        return {"status": "missing_metar", "source": "noaa_tgftp_station_txt", "n_obs": 0}
    now = datetime.now(timezone.utc)
    age_min = (now - report_dt).total_seconds() / 60.0
    running_max_c = temp_c
    if previous_value is None:
        # The TGFTP station text endpoint only exposes the latest METAR. A new
        # day or empty state can seed from this value, but cannot infer earlier
        # intraday highs until the process has observed them.
        running_max_c = temp_c
    return {
        "status": "ok",
        "source": "noaa_tgftp_station_txt",
        "n_obs": 1,
        "age_min": round(age_min, 1),
        "running_max_c": running_max_c,
        "current_temp_c": temp_c,
        "last_obs_utc": report_dt.isoformat(),
        "raw_metar": raw_metar,
        "source_file_ts_utc": header_dt.isoformat() if header_dt else None,
    }


def synoptic_token() -> str:
    global SYNOPTIC_TOKEN_CACHE
    if SYNOPTIC_TOKEN_CACHE:
        return SYNOPTIC_TOKEN_CACHE
    explicit = os.environ.get("METAR_CROSS_SYNOP_TOKEN", "").strip() or os.environ.get("SYNOPTIC_TOKEN", "").strip()
    if explicit:
        SYNOPTIC_TOKEN_CACHE = explicit
        return SYNOPTIC_TOKEN_CACHE
    text = source.fetch_text(
        WRH_API_KEY_JS,
        max_rounds=1,
        timeout_sec=FAST_HTTP_TIMEOUT_SEC,
        proxy_candidates=WEATHER_PROXY_CANDIDATES,
    )
    match = SYNOPTIC_TOKEN_RE.search(text)
    if not match:
        raise RuntimeError("weather.gov Synoptic token not found")
    SYNOPTIC_TOKEN_CACHE = match.group(1)
    return SYNOPTIC_TOKEN_CACHE


def synoptic_summary(icao: str, tz: ZoneInfo, local_date: Any, *, minutes: int) -> dict[str, Any]:
    params = {
            "STID": icao,
            "recent": str(minutes),
            "vars": "air_temp",
            "units": "temp|C",
            "obtimezone": "utc",
            "token": synoptic_token(),
            "output": "json",
    }
    last = None
    for proxy in WEATHER_PROXY_CANDIDATES:
        try:
            response = httpx.get(
                SYNOPTIC_TIMESERIES_API,
                params=params,
                headers={
                    "User-Agent": "Mozilla/5.0 pm-agent-weather-latency-research",
                    "Referer": f"https://www.weather.gov/wrh/timeseries?site={icao}",
                    "Origin": "https://www.weather.gov",
                },
                proxy=proxy,
                timeout=FAST_HTTP_TIMEOUT_SEC,
            )
            response.raise_for_status()
            payload = response.json()
            break
        except Exception as exc:  # noqa: BLE001
            last = f"{proxy}: {type(exc).__name__}"
    else:
        raise RuntimeError(f"fetch failed {SYNOPTIC_TIMESERIES_API}: {last}")
    stations = payload.get("STATION") or []
    if not stations:
        return {"status": "no_recent_obs", "source": "synopticdata_timeseries", "n_obs": 0}
    obs_payload = stations[0].get("OBSERVATIONS") or {}
    times = obs_payload.get("date_time") or []
    temps = obs_payload.get("air_temp_set_1") or obs_payload.get("air_temp") or []
    if not isinstance(times, list):
        times = [times]
    if not isinstance(temps, list):
        temps = [temps]
    obs: list[tuple[datetime, float]] = []
    for raw_ts, raw_temp in zip(times, temps):
        if raw_ts in (None, "") or raw_temp in (None, ""):
            continue
        try:
            dt = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            temp = float(raw_temp)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(tz).date() == local_date:
            obs.append((dt, temp))
    obs = sorted(obs)
    if not obs:
        return {"status": "no_recent_obs", "source": "synopticdata_timeseries", "n_obs": 0}
    last_dt, last_temp = obs[-1]
    now = datetime.now(timezone.utc)
    age_min = (now - last_dt).total_seconds() / 60.0
    return {
        "status": "ok",
        "source": "synopticdata_timeseries",
        "n_obs": len(obs),
        "age_min": round(age_min, 1),
        "running_max_c": max(temp for _, temp in obs),
        "current_temp_c": last_temp,
        "last_obs_utc": last_dt.isoformat(),
    }


def observation_summary(cfg: CityConfig, tz: ZoneInfo, local_date: Any, *, previous_value: Any, recent_hours: float, obs_source: str) -> dict[str, Any]:
    if obs_source == "synopticdata_timeseries":
        minutes = 1800 if previous_value is None else max(60, int(recent_hours * 60))
        return synoptic_summary(cfg.official_icao, tz, local_date, minutes=minutes)
    if obs_source == "noaa_tgftp_station_txt":
        if previous_value is None:
            seed = aviationweather_metar_day_fast(cfg.official_icao, tz, local_date)
            if seed.get("status") == "ok":
                seed["source"] = "aviationweather_metar_day_fast_seed_for_noaa_tgftp_station_txt"
                return seed
        return noaa_tgftp_station_txt_latest(cfg.official_icao, previous_value)
    if previous_value is None:
        return aviationweather_metar_day_fast(cfg.official_icao, tz, local_date)
    return recent_metar_summary(cfg.official_icao, tz, local_date, hours=recent_hours)


def load_source_event_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"source-events file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"source-events records missing or invalid: {path}")
    return [row for row in records if isinstance(row, dict)]


def source_event_lookup(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in load_source_event_records(path):
        city = str(row.get("city") or "")
        source_name = normalize_source_name(str(row.get("source") or ""))
        if not city or not source_name:
            continue
        key = (city, source_name)
        existing = by_key.get(key)
        if existing is None or str(row.get("local_detect_ts_utc") or "") > str(existing.get("local_detect_ts_utc") or ""):
            by_key[key] = row
    return by_key


def observation_summary_from_source_event(
    cfg: CityConfig,
    tz: ZoneInfo,
    local_date: Any,
    *,
    obs_source: str,
    source_events: dict[tuple[str, str], dict[str, Any]],
    source_events_path: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    source_name = normalize_source_name(obs_source)
    row = source_events.get((cfg.city, source_name))
    if row is None:
        return {
            "status": "source_event_missing",
            "source": source_name,
            "n_obs": 0,
            "error": f"missing source event in {source_events_path}",
            "payload_hash": stable_hash({"city": cfg.city, "source": source_name, "status": "source_event_missing"}),
            "source_input": "data_feed_source_events",
            "source_events_path": str(source_events_path),
        }
    target_date = str(row.get("target_date") or "")
    if target_date and target_date != local_date.isoformat():
        return {
            **row,
            "status": "source_event_wrong_date",
            "error": f"source event target_date={target_date}, expected {local_date.isoformat()}",
            "source_input": "data_feed_source_events",
            "source_events_path": str(source_events_path),
        }
    temp_c = row.get("temp_c")
    if row.get("status") != "ok":
        return {
            **row,
            "status": row.get("status") or "source_event_not_ok",
            "source_input": "data_feed_source_events",
            "source_events_path": str(source_events_path),
        }
    if temp_c is None:
        return {
            **row,
            "status": "source_event_missing_temp",
            "source_input": "data_feed_source_events",
            "source_events_path": str(source_events_path),
        }
    report_ts = str(row.get("source_report_ts_utc") or row.get("obs_ts_utc") or row.get("last_obs_utc") or "")
    report_dt = parse_dt(report_ts)
    if report_dt is None:
        report_dt = now_utc
    age_min = (now_utc - report_dt).total_seconds() / 60.0
    current_temp_c = float(temp_c)
    # source-events is a latest-observation feed. The caller carries forward
    # per-city running max from state; on cold start this seeds but does not
    # infer earlier intraday highs.
    return {
        **row,
        "status": "ok",
        "source": source_name,
        "source_input": "data_feed_source_events",
        "source_events_path": str(source_events_path),
        "n_obs": row.get("record_count") or 1,
        "age_min": round(age_min, 1),
        "running_max_c": current_temp_c,
        "current_temp_c": current_temp_c,
        "last_obs_utc": report_dt.isoformat(),
    }


def detect_latency_sec(ts_utc: str | None, obs_last_obs_utc: str | None) -> float | None:
    if not ts_utc or not obs_last_obs_utc:
        return None
    try:
        ts = datetime.fromisoformat(ts_utc)
        obs = datetime.fromisoformat(obs_last_obs_utc)
    except ValueError:
        return None
    return round((ts - obs).total_seconds(), 3)


def extract_order_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("order_id", "orderID", "id"):
        value = payload.get(key)
        if value:
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("order_id", "orderID", "id"):
            value = data.get(key)
            if value:
                return str(value)
    return ""


def build_live_fok_place_fn() -> Any:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds, MarketOrderArgsV2, OrderType
        from py_clob_client_v2.constants import POLYGON
        import py_clob_client_v2.http_helpers.helpers as clob_http_helpers

        market_order_args_cls = MarketOrderArgsV2
        clob_v2 = True
    except ModuleNotFoundError:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, MarketOrderArgs, OrderType
        from py_clob_client.constants import POLYGON
        import py_clob_client.http_helpers.helpers as clob_http_helpers

        market_order_args_cls = MarketOrderArgs
        clob_v2 = False
    clob_proxy = next((proxy for proxy in MARKET_PROXY_CANDIDATES if proxy), None)
    if clob_proxy:
        clob_http_helpers._http_client = httpx.Client(http2=True, proxy=clob_proxy, timeout=FAST_HTTP_TIMEOUT_SEC)  # noqa: SLF001
    host = os.getenv("CLOB_BASE_URL", "").strip() or os.getenv("PM_API_BASE_URL", "").strip() or "https://clob.polymarket.com"
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip() or os.getenv("PM", "").strip()
    if not private_key:
        raise RuntimeError("missing POLYGON_WALLET_PRIVATE_KEY or PM")
    chain_id = int(os.getenv("CLOB_CHAIN_ID", str(POLYGON)))
    funder = os.getenv("PM_ADDRESS", "").strip()
    signature_type_raw = int(os.getenv("CLOB_SIGNATURE_TYPE", "-1"))
    try:
        signer_addr = ClobClient(host, chain_id=chain_id, key=private_key).get_address()
    except Exception:
        signer_addr = ""
    signature_type = signature_type_raw
    if signature_type < 0:
        signature_type = 1 if funder and signer_addr and funder.lower() != signer_addr.lower() else 0
    api_key = os.getenv("CLOB_API_KEY", "").strip()
    api_secret = os.getenv("CLOB_SECRET", "").strip()
    api_pass = os.getenv("CLOB_PASS_PHRASE", "").strip()
    creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass) if api_key and api_secret and api_pass else None
    client = ClobClient(host, chain_id=chain_id, key=private_key, creds=creds, signature_type=signature_type, funder=funder or None)
    if creds is None:
        if clob_v2:
            client.set_api_creds(client.derive_api_key())
        else:
            client.set_api_creds(client.create_or_derive_api_creds())

    def place(row: dict[str, Any]) -> dict[str, Any]:
        signed_order = client.create_market_order(
            market_order_args_cls(
                token_id=str(row["token_id"]),
                amount=float(row["submitted_notional_usd"]),
                price=float(row["limit_price"]),
                side="BUY",
                order_type=OrderType.FOK,
            )
        )
        if clob_v2:
            response = client.post_order(signed_order, order_type=OrderType.FOK)
        else:
            response = client.post_order(signed_order, orderType=OrderType.FOK)
        return {
            "place": response,
            "order_id": extract_order_id(response),
            "clob_client": "py_clob_client_v2" if clob_v2 else "py_clob_client",
            "order_type": "FOK",
            "order_mode": "market_buy_amount",
            "signature_type": signature_type,
            "funder": funder,
            "signer": signer_addr,
            "clob_proxy_enabled": bool(clob_proxy),
        }

    return place


def floor_to_places(value: float, places: int) -> float:
    factor = 10**places
    return math.floor(float(value) * factor + 1e-12) / factor


def plan_buy_amount(best_ask: float, best_ask_size: float, max_notional_per_trade: float) -> tuple[float, float]:
    spend_cap = min(float(max_notional_per_trade), float(best_ask) * float(best_ask_size))
    submitted_notional = floor_to_places(spend_cap, 2)
    if submitted_notional <= 0 or best_ask <= 0:
        return 0.0, 0.0
    order_size = floor_to_places(submitted_notional / float(best_ask), 5)
    return order_size, submitted_notional


def spent_notional(path: Path, *, target_date: str | None = None, city: str | None = None) -> float:
    total = 0.0
    if not path.exists():
        return total
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if target_date and row.get("target_date") != target_date:
            continue
        if city and row.get("city") != city:
            continue
        if row.get("live_attempted") is not True:
            continue
        total += float(row.get("submitted_notional_usd") or 0.0)
    return round(total, 6)


def cycle_once(
    configs: list[CityConfig],
    *,
    max_ask: float,
    dry_run: bool,
    recent_hours: float,
    obs_source: str,
    source_by_city: dict[str, str] | None,
    signal_input: str = "source-events",
    source_events_path: Path = DEFAULT_SOURCE_EVENTS_LATEST,
    live: bool,
    confirm_live: bool,
    max_notional_per_trade: float,
    max_notional_per_city_day: float,
    max_notional_total_day: float,
    max_workers: int = 12,
    runtime_cache: RuntimeCache | None = None,
) -> dict[str, int]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if runtime_cache is None:
        runtime_cache = RuntimeCache(market_ttl_sec=300.0)
    state = load_state()
    fired = {tuple(item) for item in state.get("fired", [])}
    last_running = dict(state.get("last_running_value") or {})
    now_utc = datetime.now(timezone.utc)
    counts = {"cities": 0, "crossings": 0, "opportunities": 0, "live_attempts": 0, "live_submitted": 0, "errors": 0}
    live_enabled = bool(live and confirm_live and not dry_run)
    source_events: dict[tuple[str, str], dict[str, Any]] = {}
    if signal_input == "source-events":
        source_events = source_event_lookup(source_events_path)
    elif signal_input != "live-fetch":
        raise ValueError(f"unknown signal_input {signal_input!r}")

    def fetch_observation(cfg: CityConfig) -> dict[str, Any]:
        tz = ZoneInfo(cfg.timezone_name)
        local_date = now_utc.astimezone(tz).date()
        date_key = f"{cfg.city}|{local_date.isoformat()}"
        cycle_row: dict[str, Any] = {
            "ts_utc": now_utc.isoformat(),
            "city": cfg.city,
            "target_date": local_date.isoformat(),
            "official_icao": cfg.official_icao,
            "unit": cfg.unit,
            "settlement_source_class": cfg.settlement_source_class,
            "settlement_source": cfg.settlement_source,
            "live_observation_source": cfg.live_observation_source,
            "mapping_rule": cfg.mapping_rule,
            "rules_recheck_required": cfg.rules_recheck_required,
            "registry_class": cfg.registry_class,
        }
        previous_value = last_running.get(date_key)
        requested_obs_source = source_for_city(cfg, obs_source, source_by_city)
        cycle_row["obs_source_requested"] = requested_obs_source
        try:
            if signal_input == "source-events":
                met = observation_summary_from_source_event(
                    cfg,
                    tz,
                    local_date,
                    obs_source=requested_obs_source,
                    source_events=source_events,
                    source_events_path=source_events_path,
                    now_utc=now_utc,
                )
            else:
                met = observation_summary(
                    cfg,
                    tz,
                    local_date,
                    previous_value=previous_value,
                    recent_hours=recent_hours,
                    obs_source=requested_obs_source,
                )
        except Exception as exc:  # noqa: BLE001
            return {
                "cfg": cfg,
                "local_date": local_date,
                "date_key": date_key,
                "previous_value": previous_value,
                "cycle_row": cycle_row,
                "met": None,
                "error": str(exc),
            }
        return {
            "cfg": cfg,
            "local_date": local_date,
            "date_key": date_key,
            "previous_value": previous_value,
            "cycle_row": cycle_row,
            "met": met,
            "error": None,
        }

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {executor.submit(fetch_observation, cfg): cfg for cfg in configs}
        observed_rows = [future.result() for future in as_completed(futures)]

    for observed in observed_rows:
        counts["cities"] += 1
        cfg = observed["cfg"]
        local_date = observed["local_date"]
        date_key = observed["date_key"]
        previous_value = observed["previous_value"]
        cycle_row = observed["cycle_row"]
        if observed["error"]:
            counts["errors"] += 1
            append_jsonl(OUT_DIR / "cycles.jsonl", {**cycle_row, "status": "metar_fetch_failed", "error": observed["error"]})
            continue
        met = observed["met"]
        cycle_row.update(
            {
                "metar_status": met.get("status"),
                "obs_source": met.get("source"),
                "signal_input": signal_input,
                "source_events_path": str(source_events_path) if signal_input == "source-events" else "",
                "obs_source_input": met.get("source_input"),
                "obs_payload_hash": met.get("payload_hash"),
                "obs_last_obs_utc": met.get("last_obs_utc"),
                "obs_age_min": met.get("age_min"),
                "n_obs": met.get("n_obs"),
                "raw_metar": met.get("raw_metar"),
            }
        )
        if met.get("status") != "ok":
            append_jsonl(OUT_DIR / "cycles.jsonl", {**cycle_row, "status": met.get("status", "metar_not_ok")})
            continue

        recent_value = market_value(float(met["running_max_c"]), cfg.unit)
        current_value = recent_value if previous_value is None else max(int(previous_value), recent_value)
        crossings = crossed_prev_no_brackets(previous_value, current_value)
        cycle_row.update(
            {
                "status": "ok_no_cross" if not crossings else "ok_cross",
                "running_value": current_value,
                "recent_running_value": recent_value,
                "previous_running_value": previous_value,
                "crossed_prev_no_brackets": crossings,
                "running_max_c": met.get("running_max_c"),
                "current_temp_c": met.get("current_temp_c"),
            }
        )
        append_jsonl(OUT_DIR / "cycles.jsonl", cycle_row)
        last_running[date_key] = current_value
        update_report_minute_state(state, cfg.city, met.get("last_obs_utc"))
        if not crossings:
            continue

        try:
            market_bundle = runtime_cache.get_market_bundle(cfg, local_date, now_utc=datetime.now(timezone.utc))
        except RuntimeError as exc:
            counts["errors"] += 1
            event_slug = source.event_slug(cfg.slug, local_date)
            append_jsonl(OUT_DIR / "opportunities.jsonl", {**cycle_row, "status": "gamma_fetch_failed", "event_slug": event_slug, "error": str(exc)})
            continue
        event_slug = market_bundle["event_slug"]
        markets = market_bundle.get("markets") or []
        if not markets:
            append_jsonl(OUT_DIR / "opportunities.jsonl", {**cycle_row, "status": "no_event", "event_slug": event_slug})
            continue
        rules_icao = market_bundle.get("rules_icao")
        if rules_icao != cfg.official_icao:
            append_jsonl(
                OUT_DIR / "opportunities.jsonl",
                {**cycle_row, "status": "RULES_STATION_MISMATCH", "event_slug": event_slug, "rules_icao": rules_icao},
            )
            continue

        for target_bracket in crossings:
            fire_key = (cfg.city, local_date.isoformat(), target_bracket)
            if fire_key in fired:
                continue
            fired.add(fire_key)
            counts["crossings"] += 1
            market, parsed = find_dead_no_market(markets, target_bracket, current_value)
            base_row = {
                **cycle_row,
                "status": "cross_detected",
                "event_slug": event_slug,
                "target_no_bracket": target_bracket,
                "dead_bracket_guard": "parsed_high_must_equal_target_and_be_below_running_value",
                "rules_icao": rules_icao,
                "market_question": None if market is None else market.get("question"),
                "market_label": None if market is None else market.get("groupItemTitle"),
                "parsed_label": parsed,
                "market_cache_hit": market_bundle.get("market_cache_hit"),
                "market_fetched_utc": market_bundle.get("market_fetched_utc"),
                "gamma_fetch_latency_sec": market_bundle.get("gamma_fetch_latency_sec"),
            }
            if market is None:
                append_jsonl(OUT_DIR / "opportunities.jsonl", {**base_row, "book_status": "dead_no_market_not_found_or_ambiguous"})
                continue
            try:
                token_ids = json.loads(market["clobTokenIds"])
                yes_token_id = token_ids[0]
                no_token_id = token_ids[1]
            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                append_jsonl(OUT_DIR / "opportunities.jsonl", {**base_row, "book_status": "missing_no_token", "error": str(exc)})
                continue
            yes_book_summary = {}
            try:
                book_fetch_start_utc = datetime.now(timezone.utc)
                no_book = source.fetch_json(
                    f"{source.CLOB}/book",
                    {"token_id": no_token_id},
                    max_rounds=1,
                    timeout_sec=FAST_HTTP_TIMEOUT_SEC,
                    proxy_candidates=MARKET_PROXY_CANDIDATES,
                )
                no_book_fetched_utc = datetime.now(timezone.utc)
            except RuntimeError as exc:
                append_jsonl(OUT_DIR / "opportunities.jsonl", {**base_row, "book_status": "book_fetch_failed", "token_id": no_token_id, "error": str(exc)})
                continue
            no_book_summary = source.book_summary(no_book)
            if live_enabled:
                yes_book_summary = {"skipped": "speed_path_live_no_pre_order_yes_book"}
            else:
                try:
                    yes_book_summary = source.book_summary(
                        source.fetch_json(
                            f"{source.CLOB}/book",
                            {"token_id": yes_token_id},
                            max_rounds=1,
                            timeout_sec=FAST_HTTP_TIMEOUT_SEC,
                            proxy_candidates=MARKET_PROXY_CANDIDATES,
                        )
                    )
                except RuntimeError as exc:
                    yes_book_summary = {"fetch_error": str(exc)}
            yes_best_bid = yes_book_summary.get("best_bid")
            synthetic_no_cost = None if yes_best_bid is None else round(1.0 - float(yes_best_bid), 6)
            cycle_ts = parse_dt(cycle_row.get("ts_utc"))
            book_audit = {
                "yes_token_id": yes_token_id,
                "no_token_id": no_token_id,
                "no_book": no_book_summary,
                "yes_book": yes_book_summary,
                "synthetic_no_cost_if_mint_and_sell_yes_bid": synthetic_no_cost,
                "detected_after_report_sec": detect_latency_sec(cycle_row.get("ts_utc"), cycle_row.get("obs_last_obs_utc")),
                "book_fetch_start_utc": book_fetch_start_utc.isoformat(),
                "no_book_fetched_utc": no_book_fetched_utc.isoformat(),
                "book_fetch_latency_sec": round((no_book_fetched_utc - book_fetch_start_utc).total_seconds(), 3),
                "cross_to_book_fetch_start_sec": elapsed_sec(cycle_ts, book_fetch_start_utc),
                "cross_to_no_book_fetched_sec": elapsed_sec(cycle_ts, no_book_fetched_utc),
            }
            best = source.best_ask_from_book(no_book)
            if best is None:
                append_jsonl(OUT_DIR / "opportunities.jsonl", {**base_row, "book_status": "no_asks", "token_id": no_token_id, **book_audit})
                continue
            ask, size = best
            order_size, submitted_notional = plan_buy_amount(float(ask), float(size), float(max_notional_per_trade))
            city_day_spent = spent_notional(OUT_DIR / "orders.jsonl", target_date=local_date.isoformat(), city=cfg.city)
            total_day_spent = spent_notional(OUT_DIR / "orders.jsonl", target_date=local_date.isoformat())
            live_blockers: list[str] = []
            if ask > max_ask:
                live_blockers.append("ask_above_max")
            if order_size <= 0:
                live_blockers.append("zero_order_size")
            if submitted_notional > max_notional_per_trade + 1e-9:
                live_blockers.append("trade_notional_above_cap")
            if city_day_spent + submitted_notional > max_notional_per_city_day + 1e-9:
                live_blockers.append("city_day_notional_cap")
            if total_day_spent + submitted_notional > max_notional_total_day + 1e-9:
                live_blockers.append("total_day_notional_cap")
            if live and not confirm_live:
                live_blockers.append("confirm_live_missing")
            opportunity = {
                **base_row,
                "book_status": "ok",
                "token_id": no_token_id,
                "best_ask": ask,
                "best_ask_size": size,
                "max_ask": max_ask,
                "taker_eligible": not live_blockers,
                "gross_profit_if_wins_per_share": round(1.0 - ask, 6),
                "dry_run": dry_run,
                "live_requested": live,
                "live_enabled": live_enabled,
                "live_blockers": live_blockers,
                "max_notional_per_trade": max_notional_per_trade,
                "max_notional_per_city_day": max_notional_per_city_day,
                "max_notional_total_day": max_notional_total_day,
                "city_day_spent_before": city_day_spent,
                "total_day_spent_before": total_day_spent,
                "planned_size": order_size,
                "planned_notional_usd": submitted_notional,
                **book_audit,
            }
            if not live_blockers:
                counts["opportunities"] += 1
            if live_enabled and not live_blockers:
                order_row = {
                    **opportunity,
                    "order_side": "BUY",
                    "limit_price": ask,
                    "size": order_size,
                    "submitted_notional_usd": submitted_notional,
                    "live_attempted": True,
                    "live_attempt_ts_utc": datetime.now(timezone.utc).isoformat(),
                }
                order_row["cross_to_live_attempt_sec"] = elapsed_sec(cycle_ts, parse_dt(order_row["live_attempt_ts_utc"]))
                counts["live_attempts"] += 1
                try:
                    if runtime_cache.live_place_fn is None:
                        runtime_cache.live_place_fn = build_live_fok_place_fn()
                    response = runtime_cache.live_place_fn(order_row)
                    order_row["exchange_response"] = response
                    order_row["live_submit_status"] = "submitted"
                    order_row["order_id"] = response.get("order_id")
                    counts["live_submitted"] += 1
                except Exception as exc:  # noqa: BLE001
                    order_row["live_submit_status"] = "submit_failed"
                    order_row["error"] = f"{type(exc).__name__}: {exc}"
                    counts["errors"] += 1
                append_jsonl(OUT_DIR / "orders.jsonl", order_row)
            append_jsonl(OUT_DIR / "opportunities.jsonl", opportunity)

    state["last_running_value"] = last_running
    state["fired"] = [list(item) for item in sorted(fired)]
    if not dry_run:
        save_state(state)
    return counts


def report() -> int:
    rows = []
    path = OUT_DIR / "opportunities.jsonl"
    if path.exists():
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    eligible = [row for row in rows if row.get("taker_eligible")]
    latencies = []
    for row in rows:
        if row.get("detected_after_report_sec") is not None:
            latencies.append(float(row["detected_after_report_sec"]))
            continue
        fallback_latency = detect_latency_sec(row.get("ts_utc"), row.get("obs_last_obs_utc"))
        if fallback_latency is not None:
            latencies.append(float(fallback_latency))
    print(
        json.dumps(
            {
                "opportunity_rows": len(rows),
                "taker_eligible_rows": len(eligible),
                "latest_ts_utc": max((row.get("ts_utc") or "" for row in rows), default=""),
                "by_status": {status: sum(1 for row in rows if row.get("book_status") == status or row.get("status") == status) for status in sorted({row.get("book_status") or row.get("status") for row in rows})},
                "detected_after_report_sec": {
                    "min": min(latencies) if latencies else None,
                    "max": max(latencies) if latencies else None,
                    "avg": round(sum(latencies) / len(latencies), 3) if latencies else None,
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def print_city_policy(*, include_station_diff: bool, only_cities: set[str] | None = None) -> int:
    print(json.dumps(build_city_policy(include_station_diff=include_station_diff, only_cities=only_cities), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow test for METAR crossing -> previous bucket NO taker opportunities.")
    parser.add_argument("command", choices=["cycle", "loop", "report", "city-policy"], nargs="?", default="cycle")
    parser.add_argument("--cities", nargs="*", help="Optional city allowlist, e.g. Shanghai Tokyo.")
    parser.add_argument("--include-station-diff", action="store_true", help="Also include WU official-station-diff cities with >=97% alignment.")
    parser.add_argument("--max-ask", type=float, default=0.995)
    parser.add_argument(
        "--obs-source",
        choices=["aviationweather_metar", "synopticdata_timeseries", "noaa_tgftp_station_txt"],
        default=os.environ.get("METAR_CROSS_OBS_SOURCE", "aviationweather_metar"),
    )
    parser.add_argument("--live", action="store_true", help="Actually submit FOK BUY NO orders when all guards pass.")
    parser.add_argument("--confirm-live", action="store_true", help="Required with --live.")
    parser.add_argument("--max-notional-per-trade", type=float, default=float(os.environ.get("METAR_CROSS_MAX_NOTIONAL_PER_TRADE", "10")))
    parser.add_argument("--max-notional-per-city-day", type=float, default=float(os.environ.get("METAR_CROSS_MAX_NOTIONAL_PER_CITY_DAY", "10")))
    parser.add_argument("--max-notional-total-day", type=float, default=float(os.environ.get("METAR_CROSS_MAX_NOTIONAL_TOTAL_DAY", "50")))
    parser.add_argument("--source-by-city-json", default=os.environ.get("METAR_CROSS_SOURCE_BY_CITY_JSON", ""), help="JSON object mapping city name to obs source.")
    parser.add_argument("--source-by-city-file", default=os.environ.get("METAR_CROSS_SOURCE_BY_CITY_FILE", ""), help="Path to JSON object mapping city name to obs source.")
    parser.add_argument(
        "--signal-input",
        choices=["source-events", "live-fetch"],
        default=os.environ.get("METAR_CROSS_SIGNAL_INPUT", "source-events"),
        help="Read crossing signal weather rows from data-feed source-events by default; live-fetch is an explicit debug fallback.",
    )
    parser.add_argument(
        "--source-events-path",
        type=Path,
        default=DEFAULT_SOURCE_EVENTS_LATEST,
        help="Path to weather_data_feed_service output/source_events/latest.json.",
    )
    parser.add_argument("--interval-sec", type=float, default=None, help="Alias for --base-interval-sec.")
    parser.add_argument("--base-interval-sec", type=float, default=20.0)
    parser.add_argument("--burst-interval-sec", type=float, default=2.0)
    parser.add_argument("--burst-window-min", type=float, default=10.0)
    parser.add_argument("--scheduler-mode", choices=["batch", "per_city"], default=os.environ.get("METAR_CROSS_SCHEDULER_MODE", "batch"))
    parser.add_argument("--city-hot-pre-window-min", type=float, default=float(os.environ.get("METAR_CROSS_CITY_HOT_PRE_WINDOW_MIN", "1.0")))
    parser.add_argument("--city-hot-chase-window-min", type=float, default=float(os.environ.get("METAR_CROSS_CITY_HOT_CHASE_WINDOW_MIN", "10.0")))
    parser.add_argument("--fallback-report-minutes", default=os.environ.get("METAR_CROSS_FALLBACK_REPORT_MINUTES", "0,30,53"))
    parser.add_argument("--learned-burst-window-min", type=float, default=float(os.environ.get("METAR_CROSS_LEARNED_BURST_WINDOW_MIN", "6.0")))
    parser.add_argument("--market-cache-ttl-sec", type=float, default=float(os.environ.get("METAR_CROSS_MARKET_CACHE_TTL_SEC", "1800")))
    parser.add_argument("--prebuild-live-client", action=argparse.BooleanOptionalAction, default=os.environ.get("METAR_CROSS_PREBUILD_LIVE_CLIENT", "1") != "0")
    parser.add_argument("--max-workers", type=int, default=12)
    parser.add_argument("--recent-hours", type=float, default=2.0, help="After baseline, poll only recent METAR records and carry forward running max from state.")
    parser.add_argument("--dry-run", action="store_true", help="Do not update state.json; useful for smoke tests.")
    args = parser.parse_args()
    if args.interval_sec is not None:
        args.base_interval_sec = args.interval_sec

    if args.command == "report":
        return report()
    if args.command == "city-policy":
        return print_city_policy(include_station_diff=args.include_station_diff, only_cities=set(args.cities or []) or None)

    configs = load_city_configs(include_station_diff=args.include_station_diff, only_cities=set(args.cities or []) or None)
    if not configs:
        raise SystemExit("no eligible city configs")
    source_by_city = load_source_by_city(json_text=args.source_by_city_json, file_path=args.source_by_city_file)
    fallback_report_minutes = parse_report_minutes(args.fallback_report_minutes)
    runtime_cache = RuntimeCache(market_ttl_sec=args.market_cache_ttl_sec)
    if args.live and args.confirm_live and not args.dry_run and args.prebuild_live_client:
        runtime_cache.live_place_fn = build_live_fok_place_fn()
    print(
        json.dumps(
            {
                "command": args.command,
                "cities": [cfg.city for cfg in configs],
                "out_dir": str(OUT_DIR),
                "max_ask": args.max_ask,
                "dry_run": args.dry_run,
                "obs_source": args.obs_source,
                "signal_input": args.signal_input,
                "source_events_path": str(args.source_events_path),
                "source_by_city_count": len(source_by_city),
                "source_by_city": source_by_city,
                "live": args.live,
                "confirm_live": args.confirm_live,
                "max_notional_per_trade": args.max_notional_per_trade,
                "max_notional_per_city_day": args.max_notional_per_city_day,
                "max_notional_total_day": args.max_notional_total_day,
                "base_interval_sec": args.base_interval_sec,
                "burst_interval_sec": args.burst_interval_sec,
                "burst_window_min": args.burst_window_min,
                "scheduler_mode": args.scheduler_mode,
                "city_hot_pre_window_min": args.city_hot_pre_window_min,
                "city_hot_chase_window_min": args.city_hot_chase_window_min,
                "fallback_report_minutes": fallback_report_minutes,
                "learned_burst_window_min": args.learned_burst_window_min,
                "market_cache_ttl_sec": args.market_cache_ttl_sec,
                "prebuild_live_client": args.prebuild_live_client,
                "max_workers": args.max_workers,
                "http_timeout_sec": FAST_HTTP_TIMEOUT_SEC,
                "proxy_mode": FAST_PROXY_MODE,
                "weather_proxy_mode": WEATHER_PROXY_MODE,
                "market_proxy_mode": MARKET_PROXY_MODE,
                "explicit_weather_proxy": bool(EXPLICIT_WEATHER_PROXY),
                "explicit_market_proxy": bool(EXPLICIT_MARKET_PROXY),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if args.scheduler_mode == "per_city":
        scheduler_start = datetime.now(timezone.utc)
        next_due_by_city = {
            cfg.city: scheduler_start + timedelta(seconds=city_base_delay_sec(cfg.city, args.base_interval_sec) % args.base_interval_sec)
            for cfg in configs
        }
        by_city = {cfg.city: cfg for cfg in configs}
        while True:
            loop_now = datetime.now(timezone.utc)
            due_configs = [by_city[city] for city, due_at in next_due_by_city.items() if loop_now >= due_at]
            if not due_configs:
                sleep_until = min(next_due_by_city.values())
                time.sleep(max(0.1, min(1.0, (sleep_until - loop_now).total_seconds())))
                continue
            state_before = load_state()
            before_report_ts = {cfg.city: last_report_ts_for_city(state_before, cfg.city) for cfg in due_configs}
            counts = cycle_once(
                due_configs,
                max_ask=args.max_ask,
                dry_run=args.dry_run,
                recent_hours=args.recent_hours,
                obs_source=args.obs_source,
                source_by_city=source_by_city,
                signal_input=args.signal_input,
                source_events_path=args.source_events_path,
                live=args.live,
                confirm_live=args.confirm_live,
                max_notional_per_trade=args.max_notional_per_trade,
                max_notional_per_city_day=args.max_notional_per_city_day,
                max_notional_total_day=args.max_notional_total_day,
                max_workers=args.max_workers,
                runtime_cache=runtime_cache,
            )
            schedule_now = datetime.now(timezone.utc)
            state_after = load_state()
            for cfg in due_configs:
                after_report_ts = last_report_ts_for_city(state_after, cfg.city)
                report_changed = after_report_ts is not None and after_report_ts != before_report_ts.get(cfg.city)
                hot = in_city_update_window(
                    schedule_now,
                    state_after,
                    cfg.city,
                    fallback_report_minutes=fallback_report_minutes,
                    pre_window_min=args.city_hot_pre_window_min,
                    chase_window_min=args.city_hot_chase_window_min,
                )
                interval = city_base_delay_sec(cfg.city, args.base_interval_sec) if report_changed or not hot else args.burst_interval_sec
                next_due_by_city[cfg.city] = schedule_now + timedelta(seconds=interval)
            print(
                json.dumps(
                    {
                        "ts_utc": schedule_now.isoformat(),
                        "scheduler_mode": "per_city",
                        "scheduled_cities": [cfg.city for cfg in due_configs],
                        "next_due_min_utc": min(next_due_by_city.values()).isoformat(),
                        **counts,
                    },
                    sort_keys=True,
                )
            )
            if args.command == "cycle":
                return 0

    while True:
        counts = cycle_once(
            configs,
            max_ask=args.max_ask,
            dry_run=args.dry_run,
            recent_hours=args.recent_hours,
            obs_source=args.obs_source,
            source_by_city=source_by_city,
            signal_input=args.signal_input,
            source_events_path=args.source_events_path,
            live=args.live,
            confirm_live=args.confirm_live,
            max_notional_per_trade=args.max_notional_per_trade,
            max_notional_per_city_day=args.max_notional_per_city_day,
            max_notional_total_day=args.max_notional_total_day,
            max_workers=args.max_workers,
            runtime_cache=runtime_cache,
        )
        print(json.dumps({"ts_utc": datetime.now(timezone.utc).isoformat(), **counts}, sort_keys=True))
        if args.command == "cycle":
            return 0
        sleep_now = datetime.now(timezone.utc)
        state = load_state()
        generic_burst = in_update_window(sleep_now, window_min=args.burst_window_min)
        learned_burst = in_learned_update_window(sleep_now, state, window_min=args.learned_burst_window_min)
        sleep_sec = args.burst_interval_sec if generic_burst or learned_burst else args.base_interval_sec
        time.sleep(sleep_sec)


if __name__ == "__main__":
    raise SystemExit(main())
