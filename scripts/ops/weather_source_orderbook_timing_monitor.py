#!/usr/bin/env python3
"""Monitor weather-source update timing against Polymarket orderbook changes.

This is a measurement process, not a trading script. It records:
- latest observation timestamp and temperature from each source
- whether the source payload changed since the prior poll
- nearby bracket YES/NO top-of-book snapshots

Use it to identify whether the bottleneck is our polling cadence, the public
weather source, or other traders receiving faster data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
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
from weather_data_feed import load_source_profiles  # noqa: E402
from weather_data_feed.source_policy import (  # noqa: E402
    CityConfig,
    build_city_policy,
    city_slug as source_policy_city_slug,
    load_city_configs,
)
from weather_metar_cross_prev_no_shadow import (  # noqa: E402
    market_value,
)


DATA_ROOT = Path(os.environ.get("TIMING_MONITOR_DATA_ROOT") or os.environ.get("DATA_PROJECT_DIR") or ROOT)
OUT_DIR = DATA_ROOT / "runtime/weather_edge_v1/source_orderbook_timing"
FAST_HTTP_TIMEOUT_SEC = float(os.environ.get("TIMING_MONITOR_HTTP_TIMEOUT_SEC", "3.0"))
LEGACY_PROXY_MODE = os.environ.get("TIMING_MONITOR_PROXY_MODE", "").strip().lower()
WEATHER_PROXY_MODE = os.environ.get("TIMING_MONITOR_WEATHER_PROXY_MODE", LEGACY_PROXY_MODE or "direct").strip().lower()
MARKET_PROXY_MODE = os.environ.get("TIMING_MONITOR_MARKET_PROXY_MODE", LEGACY_PROXY_MODE or "direct").strip().lower()
EXPLICIT_WEATHER_PROXY = os.environ.get("TIMING_MONITOR_WEATHER_PROXY", "").strip()
EXPLICIT_MARKET_PROXY = os.environ.get("TIMING_MONITOR_MARKET_PROXY", "").strip()
CHECKWX_URL = "https://www.checkwx.com/weather/{icao}/metar"
AWC_METARS_CACHE_CSV_GZ = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"
NOAA_TGFTP_STATION_TXT = "https://tgftp.nws.noaa.gov/data/observations/metar/stations/{icao}.TXT"
WEATHER_GOV_LATEST_OBS = "https://api.weather.gov/stations/{icao}/observations/latest"
WRH_API_KEY_JS = "https://www.weather.gov/source/wrh/apiKey.js"
SYNOPTIC_TIMESERIES_API = "https://api.synopticdata.com/v2/stations/timeseries"
METAR_TEMP_RE = re.compile(r"\s(M?\d{2})/(M?\d{2}|//)")
METAR_REPORT_TIME_RE = re.compile(r"\b[A-Z0-9]{4}\s+(\d{2})(\d{2})(\d{2})Z\b")
CHECKWX_OBS_RE = re.compile(r"Observed.*?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", re.IGNORECASE | re.DOTALL)
ICAO_RE = re.compile(r"^[A-Z0-9]{4}$")
WRH_SITE_RE = re.compile(r"[?&]site=([A-Z0-9]{4})\b", re.IGNORECASE)
SYNOPTIC_TOKEN_RE = re.compile(r"['\"]([a-f0-9]{32})['\"]")
SOURCE_ALIASES = {
    "aviationweather": "aviationweather_metar",
    "aviationweather_metar": "aviationweather_metar",
    "aviationweather_cache": "aviationweather_cache_csv",
    "aviationweather_cache_csv": "aviationweather_cache_csv",
    "awc_cache": "aviationweather_cache_csv",
    "checkwx": "checkwx_html",
    "checkwx_html": "checkwx_html",
    "iem": "iem_asos",
    "iem_asos": "iem_asos",
    "noaa_tgftp": "noaa_tgftp_station_txt",
    "noaa_tgftp_station_txt": "noaa_tgftp_station_txt",
    "tgftp": "noaa_tgftp_station_txt",
    "nws_api": "weather_gov_latest",
    "nws_api_latest": "weather_gov_latest",
    "weather_gov": "weather_gov_latest",
    "weather_gov_latest": "weather_gov_latest",
    "synoptic": "synopticdata_timeseries",
    "synopticdata": "synopticdata_timeseries",
    "synopticdata_timeseries": "synopticdata_timeseries",
    "weather_gov_wrh": "synopticdata_timeseries",
    "wrh_timeseries": "synopticdata_timeseries",
}
SYNOPTIC_TOKEN_CACHE: str | None = None


def proxy_candidates(mode: str, explicit_proxy: str = "") -> list[str | None]:
    if explicit_proxy:
        return [explicit_proxy]
    if mode == "all":
        return source.PROXIES
    if mode == "first":
        return source.PROXIES[:1]
    if mode == "none" or mode == "direct":
        return [None]
    raise ValueError(f"unknown proxy mode {mode}")


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


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_metar_temp_c(raw: str) -> float | None:
    match = METAR_TEMP_RE.search(f" {raw}")
    if not match:
        return None
    token = match.group(1)
    return float(-int(token[1:]) if token.startswith("M") else int(token))


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_tgftp_header_time(text: str) -> datetime | None:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    try:
        return datetime.strptime(first, "%Y/%m/%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_metar_report_time(raw: str, reference_utc: datetime | None) -> datetime | None:
    match = METAR_REPORT_TIME_RE.search(raw)
    if not match or reference_utc is None:
        return reference_utc
    day, hour, minute = (int(match.group(i)) for i in (1, 2, 3))
    candidate = reference_utc.replace(day=1, hour=hour, minute=minute, second=0, microsecond=0)
    try:
        candidate = candidate.replace(day=day)
    except ValueError:
        return reference_utc
    if candidate - reference_utc > timedelta(days=15):
        prev_month = 12 if candidate.month == 1 else candidate.month - 1
        year = candidate.year - 1 if candidate.month == 1 else candidate.year
        candidate = candidate.replace(year=year, month=prev_month)
    elif reference_utc - candidate > timedelta(days=15):
        next_month = 1 if candidate.month == 12 else candidate.month + 1
        year = candidate.year + 1 if candidate.month == 12 else candidate.year
        candidate = candidate.replace(year=year, month=next_month)
    return candidate


def source_age_sec(report_ts_utc: str | None, now_utc: datetime) -> float | None:
    dt = parse_dt(report_ts_utc)
    if not dt:
        return None
    return round((now_utc - dt).total_seconds(), 3)


def canonical_source_name(source_name: str) -> str:
    return SOURCE_ALIASES.get(source_name, source_name)


def expand_source_names(cfg: CityConfig, requested_sources: list[str]) -> list[str]:
    expanded: list[str] = []
    for raw_name in requested_sources:
        if raw_name == "source_profiles":
            expanded.extend([cfg.live_observation_source, *cfg.fallback_sources])
        elif raw_name == "profile_primary":
            expanded.append(cfg.live_observation_source)
        elif raw_name == "profile_fallbacks":
            expanded.extend(cfg.fallback_sources)
        else:
            expanded.append(raw_name)
    deduped: list[str] = []
    for raw_name in expanded:
        source_name = canonical_source_name(str(raw_name))
        if source_name and source_name not in deduped:
            deduped.append(source_name)
    return deduped


def source_station_id(value: str) -> str:
    raw = str(value or "").strip().upper()
    if ICAO_RE.match(raw):
        return raw
    match = WRH_SITE_RE.search(str(value or ""))
    return match.group(1).upper() if match else ""


def station_id_from_profile(profile: Any) -> str:
    for value in (profile.official_station_or_feed, profile.official_source, profile.configured_icao):
        station = source_station_id(value)
        if station:
            return station
    return ""


def research_live_source(profile: Any) -> str:
    if profile.primary_source:
        return profile.primary_source
    raw = " ".join([str(profile.official_source or ""), str(profile.official_station_or_feed or "")]).lower()
    if "weather.gov/wrh" in raw or profile.settlement_source_class in {"non_wu_source_by_rules"}:
        return "synopticdata_timeseries"
    return ""


def load_monitor_city_configs(
    *,
    include_station_diff: bool,
    include_research_cities: bool,
    only_cities: set[str] | None,
) -> list[CityConfig]:
    if not include_research_cities:
        return load_city_configs(include_station_diff=include_station_diff, only_cities=only_cities)
    policy_configs = {
        cfg.city: cfg
        for cfg in load_city_configs(include_station_diff=include_station_diff, only_cities=only_cities)
    }
    profiles = load_source_profiles()
    configs = dict(policy_configs)
    for city, profile in sorted(profiles.items()):
        if only_cities and city not in only_cities:
            continue
        if city in configs:
            continue
        station = station_id_from_profile(profile)
        if not station:
            continue
        live_source = research_live_source(profile)
        configs[city] = CityConfig(
            city=city,
            slug=source_policy_city_slug(city),
            unit=profile.unit,
            timezone_name=profile.timezone_name,
            official_icao=station,
            settlement_source_class=profile.settlement_source_class,
            settlement_source=profile.official_source,
            live_observation_source=live_source,
            fallback_sources=profile.fallback_sources,
            mapping_rule=profile.mapping_rule,
            registry_class="research_source_profile",
            alignment_days=profile.alignment_days or 0,
            alignment_rate=profile.alignment_rate or 0.0,
            rules_recheck_required=profile.rules_recheck_required,
            source_profile_note=profile.source_profile_note,
        )
    return sorted(configs.values(), key=lambda item: item.city)


def parse_aviationweather_records(data: list[dict[str, Any]], tz: ZoneInfo, local_date: Any) -> list[tuple[datetime, float, dict[str, Any]]]:
    records = []
    for rec in data:
        temp = rec.get("temp")
        ts = rec.get("reportTime")
        if temp is None or not ts:
            continue
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            temp_c = float(temp)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = parse_metar_report_time(str(rec.get("rawOb") or ""), dt) or dt
        if dt.astimezone(tz).date() == local_date:
            records.append((dt, temp_c, rec))
    return sorted(records, key=lambda item: item[0])


def parse_awc_cache_csv_records(text: str, icao: str, tz: ZoneInfo, local_date: Any) -> list[tuple[datetime, float, dict[str, str]]]:
    records: list[tuple[datetime, float, dict[str, str]]] = []
    for row in csv.DictReader(io.StringIO(text)):
        station_id = str(row.get("station_id") or row.get("icaoId") or row.get("id") or "").strip()
        if station_id != icao:
            continue
        temp_raw = row.get("temp_c") or row.get("temp") or row.get("temperature")
        ts_raw = row.get("observation_time") or row.get("reportTime") or row.get("obsTime")
        if not temp_raw or not ts_raw:
            continue
        try:
            dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            temp_c = float(temp_raw)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(tz).date() == local_date:
            records.append((dt, temp_c, row))
    return sorted(records, key=lambda item: item[0])


def fetch_aviationweather_latest(cfg: CityConfig, tz: ZoneInfo, local_date: Any) -> dict[str, Any]:
    data = source.fetch_json(
        source.METAR_API,
        {"ids": cfg.official_icao, "format": "json", "hours": "2"},
        max_rounds=1,
        timeout_sec=FAST_HTTP_TIMEOUT_SEC,
        proxy_candidates=WEATHER_PROXY_CANDIDATES,
    )
    records = parse_aviationweather_records(data, tz, local_date) if isinstance(data, list) else []
    if not records:
        return {"status": "empty", "source": "aviationweather_metar", "station": cfg.official_icao}
    latest_dt, latest_temp, raw = records[-1]
    return {
        "status": "ok",
        "source": "aviationweather_metar",
        "station": cfg.official_icao,
        "source_report_ts_utc": latest_dt.isoformat(),
        "temp_c": latest_temp,
        "raw_metar": raw.get("rawOb"),
        "raw_payload_hash": stable_hash(raw),
    }


def fetch_aviationweather_cache_csv_latest(cfg: CityConfig, tz: ZoneInfo, local_date: Any) -> dict[str, Any]:
    raw_bytes = source.fetch_bytes(
        AWC_METARS_CACHE_CSV_GZ,
        max_rounds=1,
        timeout_sec=FAST_HTTP_TIMEOUT_SEC,
        proxy_candidates=WEATHER_PROXY_CANDIDATES,
    )
    text = raw_bytes.decode("utf-8", errors="replace")
    records = parse_awc_cache_csv_records(text, cfg.official_icao, tz, local_date)
    if not records:
        return {
            "status": "empty",
            "source": "aviationweather_cache_csv",
            "station": cfg.official_icao,
            "raw_payload_hash": stable_hash(text),
        }
    latest_dt, latest_temp, raw = records[-1]
    return {
        "status": "ok",
        "source": "aviationweather_cache_csv",
        "station": cfg.official_icao,
        "source_report_ts_utc": latest_dt.isoformat(),
        "temp_c": latest_temp,
        "raw_metar": raw.get("raw_text") or raw.get("rawOb") or raw.get("raw_text"),
        "raw_payload_hash": stable_hash(raw),
    }


def fetch_checkwx_latest(cfg: CityConfig) -> dict[str, Any]:
    text = source.fetch_text(
        CHECKWX_URL.format(icao=cfg.official_icao),
        max_rounds=1,
        timeout_sec=FAST_HTTP_TIMEOUT_SEC,
        proxy_candidates=WEATHER_PROXY_CANDIDATES,
    )
    raw_match = re.search(rf"{cfg.official_icao}\s+\d{{6}}Z[^<]+", text)
    raw_metar = raw_match.group(0).strip() if raw_match else ""
    observed_match = CHECKWX_OBS_RE.search(text)
    return {
        "status": "ok" if raw_metar else "missing_metar",
        "source": "checkwx_html",
        "station": cfg.official_icao,
        "source_report_ts_utc": observed_match.group(1).replace("Z", "+00:00") if observed_match else "",
        "temp_c": parse_metar_temp_c(raw_metar) if raw_metar else None,
        "raw_metar": raw_metar,
        "raw_payload_hash": stable_hash(text),
    }


def fetch_noaa_tgftp_station_txt_latest(cfg: CityConfig) -> dict[str, Any]:
    text = source.fetch_text(
        NOAA_TGFTP_STATION_TXT.format(icao=cfg.official_icao),
        max_rounds=1,
        timeout_sec=FAST_HTTP_TIMEOUT_SEC,
        proxy_candidates=WEATHER_PROXY_CANDIDATES,
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    raw_metar = lines[1] if len(lines) > 1 else ""
    header_dt = parse_tgftp_header_time(text)
    report_dt = parse_metar_report_time(raw_metar, header_dt)
    return {
        "status": "ok" if raw_metar else "missing_metar",
        "source": "noaa_tgftp_station_txt",
        "station": cfg.official_icao,
        "source_report_ts_utc": report_dt.isoformat() if report_dt else "",
        "source_file_ts_utc": header_dt.isoformat() if header_dt else "",
        "temp_c": parse_metar_temp_c(raw_metar) if raw_metar else None,
        "raw_metar": raw_metar,
        "raw_payload_hash": stable_hash(text),
    }


def fetch_weather_gov_latest(cfg: CityConfig) -> dict[str, Any]:
    last = None
    for proxy in WEATHER_PROXY_CANDIDATES:
        try:
            response = httpx.get(
                WEATHER_GOV_LATEST_OBS.format(icao=cfg.official_icao),
                headers={"User-Agent": "pm-agent-weather-latency-research"},
                proxy=proxy,
                timeout=FAST_HTTP_TIMEOUT_SEC,
            )
            response.raise_for_status()
            payload = response.json()
            break
        except Exception as exc:  # noqa: BLE001
            last = f"{proxy}: {type(exc).__name__}"
    else:
        raise RuntimeError(f"fetch failed {WEATHER_GOV_LATEST_OBS.format(icao=cfg.official_icao)}: {last}")
    props = payload.get("properties") or {}
    temp_payload = props.get("temperature") or {}
    temp_c = temp_payload.get("value")
    return {
        "status": "ok" if props.get("timestamp") else "missing_observation",
        "source": "weather_gov_latest",
        "station": cfg.official_icao,
        "source_report_ts_utc": str(props.get("timestamp") or "").replace("Z", "+00:00"),
        "temp_c": None if temp_c is None else float(temp_c),
        "raw_metar": props.get("rawMessage") or "",
        "raw_payload_hash": stable_hash(payload),
    }


def synoptic_token() -> str:
    global SYNOPTIC_TOKEN_CACHE
    if SYNOPTIC_TOKEN_CACHE:
        return SYNOPTIC_TOKEN_CACHE
    explicit = os.environ.get("TIMING_MONITOR_SYNOP_TOKEN", "").strip() or os.environ.get("SYNOPTIC_TOKEN", "").strip()
    if explicit:
        SYNOPTIC_TOKEN_CACHE = explicit
        return SYNOPTIC_TOKEN_CACHE
    last = None
    for proxy in WEATHER_PROXY_CANDIDATES:
        try:
            response = httpx.get(
                WRH_API_KEY_JS,
                headers={
                    "User-Agent": "Mozilla/5.0 pm-agent-weather-latency-research",
                    "Referer": "https://www.weather.gov/wrh/timeseries",
                },
                proxy=proxy,
                timeout=FAST_HTTP_TIMEOUT_SEC,
            )
            response.raise_for_status()
            match = SYNOPTIC_TOKEN_RE.search(response.text)
            if not match:
                raise RuntimeError("token not found in weather.gov apiKey.js")
            SYNOPTIC_TOKEN_CACHE = match.group(1)
            return SYNOPTIC_TOKEN_CACHE
        except Exception as exc:  # noqa: BLE001
            last = f"{proxy}: {type(exc).__name__}"
    raise RuntimeError(f"fetch failed {WRH_API_KEY_JS}: {last}")


def synoptic_obs_lists(payload: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    station_rows = payload.get("STATION") or []
    if not station_rows:
        return [], []
    obs = station_rows[0].get("OBSERVATIONS") or {}
    times = obs.get("date_time") or []
    temps = obs.get("air_temp_set_1") or obs.get("air_temp") or []
    if not isinstance(times, list):
        times = [times]
    if not isinstance(temps, list):
        temps = [temps]
    return times, temps


def fetch_synopticdata_timeseries_latest(cfg: CityConfig, tz: ZoneInfo, local_date: Any) -> dict[str, Any]:
    token = synoptic_token()
    params = {
        "STID": cfg.official_icao,
        "recent": "240",
        "vars": "air_temp",
        "units": "temp|C",
        "obtimezone": "utc",
        "token": token,
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
                    "Referer": f"https://www.weather.gov/wrh/timeseries?site={cfg.official_icao}",
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
        raise RuntimeError(f"fetch failed {SYNOPTIC_TIMESERIES_API} STID={cfg.official_icao}: {last}")
    times, temps = synoptic_obs_lists(payload)
    records: list[tuple[datetime, float, dict[str, Any]]] = []
    for ts_raw, temp_raw in zip(times, temps):
        if ts_raw in (None, "") or temp_raw in (None, ""):
            continue
        try:
            dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            temp_c = float(temp_raw)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(tz).date() == local_date:
            records.append((dt, temp_c, {"date_time": ts_raw, "air_temp_set_1": temp_raw}))
    if not records:
        return {
            "status": "empty",
            "source": "synopticdata_timeseries",
            "station": cfg.official_icao,
            "raw_payload_hash": stable_hash(payload),
        }
    latest_dt, latest_temp, latest_raw = sorted(records, key=lambda item: item[0])[-1]
    station_row = (payload.get("STATION") or [{}])[0]
    return {
        "status": "ok",
        "source": "synopticdata_timeseries",
        "station": cfg.official_icao,
        "source_report_ts_utc": latest_dt.isoformat(),
        "temp_c": latest_temp,
        "raw_metar": "",
        "synoptic_station_id": station_row.get("ID"),
        "synoptic_station_name": station_row.get("NAME"),
        "raw_payload_hash": stable_hash({"latest": latest_raw, "station": station_row.get("STID"), "period": station_row.get("PERIOD_OF_RECORD")}),
    }


def fetch_iem_asos_latest(cfg: CityConfig, tz: ZoneInfo, local_date: Any) -> dict[str, Any]:
    local_start = datetime.combine(local_date, datetime.min.time(), tzinfo=tz)
    start_utc = local_start.astimezone(timezone.utc) - timedelta(hours=2)
    end_utc = datetime.now(timezone.utc) + timedelta(hours=1)
    params = [
        ("station", cfg.official_icao),
        ("data", "tmpc"),
        ("year1", str(start_utc.year)),
        ("month1", str(start_utc.month)),
        ("day1", str(start_utc.day)),
        ("year2", str(end_utc.year)),
        ("month2", str(end_utc.month)),
        ("day2", str(end_utc.day)),
        ("tz", "Etc/UTC"),
        ("format", "onlycomma"),
        ("latlon", "no"),
        ("elev", "no"),
        ("missing", "M"),
        ("trace", "T"),
        ("direct", "no"),
        ("report_type", "1"),
        ("report_type", "2"),
        ("report_type", "3"),
        ("report_type", "4"),
    ]
    text = source.fetch_text(
        source.IEM_ASOS_API,
        params,
        max_rounds=1,
        timeout_sec=FAST_HTTP_TIMEOUT_SEC,
        proxy_candidates=WEATHER_PROXY_CANDIDATES,
    )
    rows = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    records: list[tuple[datetime, float, dict[str, str]]] = []
    for row in csv.DictReader(io.StringIO("\n".join(rows))):
        raw_temp = row.get("tmpc")
        raw_ts = row.get("valid")
        if not raw_temp or raw_temp == "M" or not raw_ts:
            continue
        try:
            dt = datetime.fromisoformat(raw_ts.replace(" ", "T")).replace(tzinfo=timezone.utc)
            temp = float(raw_temp)
        except ValueError:
            continue
        if dt.astimezone(tz).date() == local_date:
            records.append((dt, temp, row))
    if not records:
        return {
            "status": "empty",
            "source": "iem_asos",
            "station": cfg.official_icao,
            "raw_payload_hash": stable_hash(text),
        }
    latest_dt, latest_temp, latest_row = sorted(records)[-1]
    return {
        "status": "ok",
        "source": "iem_asos",
        "station": cfg.official_icao,
        "source_report_ts_utc": latest_dt.isoformat(),
        "temp_c": latest_temp,
        "raw_metar": "",
        "raw_payload_hash": stable_hash({"latest_row": latest_row, "payload": text}),
    }


def source_snapshot(cfg: CityConfig, source_name: str, now_utc: datetime) -> dict[str, Any]:
    source_name = canonical_source_name(source_name)
    tz = ZoneInfo(cfg.timezone_name)
    local_date = now_utc.astimezone(tz).date()
    fetch_start_utc = datetime.now(timezone.utc)
    if source_name == "aviationweather_metar":
        row = fetch_aviationweather_latest(cfg, tz, local_date)
    elif source_name == "aviationweather_cache_csv":
        row = fetch_aviationweather_cache_csv_latest(cfg, tz, local_date)
    elif source_name == "checkwx_html":
        row = fetch_checkwx_latest(cfg)
    elif source_name == "noaa_tgftp_station_txt":
        row = fetch_noaa_tgftp_station_txt_latest(cfg)
    elif source_name == "weather_gov_latest":
        row = fetch_weather_gov_latest(cfg)
    elif source_name == "synopticdata_timeseries":
        row = fetch_synopticdata_timeseries_latest(cfg, tz, local_date)
    elif source_name == "iem_asos":
        row = fetch_iem_asos_latest(cfg, tz, local_date)
    else:
        raise ValueError(f"unknown source {source_name}")
    fetch_end_utc = datetime.now(timezone.utc)
    detected_after_report_sec = source_age_sec(row.get("source_report_ts_utc"), fetch_end_utc)
    row.update(
        {
            "ts_utc": fetch_end_utc.isoformat(),
            "local_detect_ts_utc": fetch_end_utc.isoformat(),
            "source_fetch_start_utc": fetch_start_utc.isoformat(),
            "source_fetch_end_utc": fetch_end_utc.isoformat(),
            "source_fetch_latency_sec": round((fetch_end_utc - fetch_start_utc).total_seconds(), 3),
            "city": cfg.city,
            "target_date": local_date.isoformat(),
            "unit": cfg.unit,
            "settlement_source_class": cfg.settlement_source_class,
            "settlement_source": cfg.settlement_source,
            "live_observation_source": cfg.live_observation_source,
            "mapping_rule": cfg.mapping_rule,
            "registry_class": cfg.registry_class,
            "source_age_sec": detected_after_report_sec,
            "detected_after_report_sec": detected_after_report_sec,
        }
    )
    row["payload_hash"] = stable_hash(
        {
            "source_report_ts_utc": row.get("source_report_ts_utc"),
            "temp_c": row.get("temp_c"),
            "raw_metar": row.get("raw_metar"),
            "raw_payload_hash": row.get("raw_payload_hash"),
        }
    )
    return row


def in_update_window(now_utc: datetime, *, window_min: float) -> bool:
    minute = now_utc.minute + now_utc.second / 60.0
    distance_to_half_hour = min(abs(minute - 0.0), abs(minute - 30.0), abs(minute - 60.0))
    return distance_to_half_hour <= window_min


def target_brackets_from_temp(temp_c: float | None, unit: str, radius: int) -> list[int]:
    if temp_c is None:
        return []
    value = market_value(float(temp_c), unit)
    return list(range(value - radius, value + radius + 1))


def find_markets_for_brackets(markets: list[dict[str, Any]], brackets: list[int]) -> list[tuple[int, dict[str, Any]]]:
    out = []
    for market in markets:
        parsed = source.parse_label(str(market.get("groupItemTitle") or ""), str(market.get("question") or ""))
        if parsed is None or parsed.get("top"):
            continue
        low = parsed.get("low")
        high = parsed.get("high")
        for bracket in brackets:
            if low is None and high is not None and bracket <= int(float(high)):
                out.append((bracket, market))
                break
            if low is not None and high is not None and float(low) <= bracket <= float(high):
                out.append((bracket, market))
                break
    return out


def fetch_orderbook_rows(cfg: CityConfig, now_utc: datetime, temp_c: float | None, *, radius: int) -> list[dict[str, Any]]:
    tz = ZoneInfo(cfg.timezone_name)
    local_date = now_utc.astimezone(tz).date()
    event_slug = source.event_slug(cfg.slug, local_date)
    events = source.fetch_json(
        f"{source.GAMMA}/events",
        {"slug": event_slug},
        max_rounds=1,
        timeout_sec=FAST_HTTP_TIMEOUT_SEC,
        proxy_candidates=MARKET_PROXY_CANDIDATES,
    )
    markets = events[0].get("markets") if events else []
    if not markets:
        return [{"ts_utc": now_utc.isoformat(), "city": cfg.city, "event_slug": event_slug, "status": "no_event"}]
    rows = []
    for bracket, market in find_markets_for_brackets(markets, target_brackets_from_temp(temp_c, cfg.unit, radius)):
        token_ids = json.loads(market["clobTokenIds"])
        for side, token_id in (("YES", token_ids[0]), ("NO", token_ids[1])):
            try:
                book_fetch_start_utc = datetime.now(timezone.utc)
                book = source.book_summary(
                    source.fetch_json(
                        f"{source.CLOB}/book",
                        {"token_id": token_id},
                        max_rounds=1,
                        timeout_sec=FAST_HTTP_TIMEOUT_SEC,
                        proxy_candidates=MARKET_PROXY_CANDIDATES,
                    )
                )
                book_fetch_end_utc = datetime.now(timezone.utc)
                status = "ok"
            except Exception as exc:  # noqa: BLE001
                book = {"error": f"{type(exc).__name__}: {exc}"}
                status = "book_fetch_failed"
                book_fetch_start_utc = None
                book_fetch_end_utc = None
            row = {
                "ts_utc": now_utc.isoformat(),
                "local_detect_ts_utc": now_utc.isoformat(),
                "city": cfg.city,
                "target_date": local_date.isoformat(),
                "event_slug": event_slug,
                "bracket": bracket,
                "market_label": market.get("groupItemTitle"),
                "side": side,
                "token_id": token_id,
                "status": status,
                "book_fetch_start_utc": None if book_fetch_start_utc is None else book_fetch_start_utc.isoformat(),
                "book_fetch_end_utc": None if book_fetch_end_utc is None else book_fetch_end_utc.isoformat(),
                "book_fetch_latency_sec": None
                if book_fetch_start_utc is None or book_fetch_end_utc is None
                else round((book_fetch_end_utc - book_fetch_start_utc).total_seconds(), 3),
                **book,
            }
            row["payload_hash"] = stable_hash({k: row.get(k) for k in ("best_bid", "best_bid_size", "best_ask", "best_ask_size", "bid_levels", "ask_levels")})
            rows.append(row)
    return rows


def mark_changed(state: dict[str, Any], key: str, payload_hash: str) -> bool:
    old = state.get(key)
    state[key] = payload_hash
    return old != payload_hash


def cycle_once(configs: list[CityConfig], *, sources: list[str], bracket_radius: int, max_workers: int = 12) -> dict[str, int]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = read_json(OUT_DIR / "state.json", {})
    now_utc = datetime.now(timezone.utc)
    counts = {"cities": 0, "source_rows": 0, "source_updates": 0, "book_rows": 0, "book_updates": 0, "errors": 0}
    source_jobs: list[tuple[CityConfig, str]] = []
    for cfg in configs:
        source_jobs.extend((cfg, source_name) for source_name in expand_source_names(cfg, sources))
    source_rows_by_city: dict[str, list[dict[str, Any]]] = {cfg.city: [] for cfg in configs}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {
            executor.submit(source_snapshot, cfg, source_name, now_utc): (cfg, source_name)
            for cfg, source_name in source_jobs
        }
        for future in as_completed(futures):
            cfg, source_name = futures[future]
            try:
                row = future.result()
            except Exception as exc:  # noqa: BLE001
                counts["errors"] += 1
                row = {
                    "ts_utc": now_utc.isoformat(),
                    "local_detect_ts_utc": now_utc.isoformat(),
                    "city": cfg.city,
                    "source": canonical_source_name(source_name),
                    "station": cfg.official_icao,
                    "status": "fetch_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            key = f"source|{cfg.city}|{source_name}"
            row["changed_since_last"] = mark_changed(state, key, row.get("payload_hash", ""))
            counts["source_rows"] += 1
            counts["source_updates"] += int(bool(row["changed_since_last"]))
            append_jsonl(OUT_DIR / "sources.jsonl", row)
            source_rows_by_city.setdefault(cfg.city, []).append(row)

    def temp_for_city(cfg: CityConfig) -> float | None:
        rows = source_rows_by_city.get(cfg.city, [])
        primary = canonical_source_name(cfg.live_observation_source)
        for row in rows:
            if primary and row.get("source") == primary and row.get("temp_c") is not None:
                return float(row["temp_c"])
        for row in rows:
            if row.get("status") == "ok" and row.get("temp_c") is not None:
                return float(row["temp_c"])
        return None

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {
            executor.submit(fetch_orderbook_rows, cfg, now_utc, temp_for_city(cfg), radius=bracket_radius): cfg
            for cfg in configs
        }
        for future in as_completed(futures):
            cfg = futures[future]
            counts["cities"] += 1
            try:
                for book_row in future.result():
                    key = f"book|{book_row.get('city')}|{book_row.get('token_id')}"
                    book_row["changed_since_last"] = mark_changed(state, key, book_row.get("payload_hash", ""))
                    counts["book_rows"] += 1
                    counts["book_updates"] += int(bool(book_row["changed_since_last"]))
                    append_jsonl(OUT_DIR / "books.jsonl", book_row)
            except Exception as exc:  # noqa: BLE001
                counts["errors"] += 1
                append_jsonl(OUT_DIR / "books.jsonl", {"ts_utc": now_utc.isoformat(), "city": cfg.city, "status": "event_or_book_fetch_failed", "error": f"{type(exc).__name__}: {exc}"})
    write_json(OUT_DIR / "state.json", state)
    return counts


def report() -> int:
    sources_path = OUT_DIR / "sources.jsonl"
    books_path = OUT_DIR / "books.jsonl"
    source_rows = [json.loads(line) for line in sources_path.read_text(encoding="utf-8").splitlines() if line.strip()] if sources_path.exists() else []
    book_rows = [json.loads(line) for line in books_path.read_text(encoding="utf-8").splitlines() if line.strip()] if books_path.exists() else []
    latest_sources = {}
    for row in source_rows:
        latest_sources[(row.get("city"), row.get("source"))] = row
    source_latencies: dict[str, dict[str, float | int | None]] = {}
    for source_name in sorted({str(row.get("source")) for row in source_rows if row.get("source")}):
        vals = [
            float(row["detected_after_report_sec"])
            for row in source_rows
            if row.get("source") == source_name and row.get("detected_after_report_sec") is not None
        ]
        source_latencies[source_name] = {
            "rows": len(vals),
            "min_sec": min(vals) if vals else None,
            "max_sec": max(vals) if vals else None,
            "avg_sec": round(sum(vals) / len(vals), 3) if vals else None,
        }
    print(
        json.dumps(
            {
                "source_rows": len(source_rows),
                "book_rows": len(book_rows),
                "latest_sources": list(latest_sources.values()),
                "source_latencies": source_latencies,
                "source_updates": sum(1 for row in source_rows if row.get("changed_since_last")),
                "book_updates": sum(1 for row in book_rows if row.get("changed_since_last")),
                "out_dir": str(OUT_DIR),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def source_policy(*, include_station_diff: bool, only_cities: set[str] | None = None) -> int:
    print(json.dumps(build_city_policy(include_station_diff=include_station_diff, only_cities=only_cities), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure source update timing vs weather market orderbook changes.")
    parser.add_argument("command", choices=["cycle", "loop", "report", "source-policy"], nargs="?", default="cycle")
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--include-station-diff", action="store_true")
    parser.add_argument("--include-research-cities", action="store_true")
    parser.add_argument("--sources", nargs="*", default=["profile_primary", "checkwx_html"])
    parser.add_argument("--bracket-radius", type=int, default=1)
    parser.add_argument("--base-interval-sec", type=float, default=20.0)
    parser.add_argument("--burst-interval-sec", type=float, default=2.0)
    parser.add_argument("--burst-window-min", type=float, default=10.0)
    parser.add_argument("--max-workers", type=int, default=12)
    args = parser.parse_args()

    if args.command == "report":
        return report()
    if args.command == "source-policy":
        return source_policy(include_station_diff=args.include_station_diff, only_cities=set(args.cities or []) or None)
    configs = load_monitor_city_configs(
        include_station_diff=args.include_station_diff,
        include_research_cities=args.include_research_cities,
        only_cities=set(args.cities or []) or None,
    )
    if not configs:
        raise SystemExit("no eligible city configs")
    print(
        json.dumps(
            {
                "command": args.command,
                "cities": [cfg.city for cfg in configs],
                "sources": args.sources,
                "out_dir": str(OUT_DIR),
                "base_interval_sec": args.base_interval_sec,
                "burst_interval_sec": args.burst_interval_sec,
                "burst_window_min": args.burst_window_min,
                "include_research_cities": args.include_research_cities,
                "max_workers": args.max_workers,
                "http_timeout_sec": FAST_HTTP_TIMEOUT_SEC,
                "weather_proxy_mode": WEATHER_PROXY_MODE,
                "market_proxy_mode": MARKET_PROXY_MODE,
                "explicit_weather_proxy": bool(EXPLICIT_WEATHER_PROXY),
                "explicit_market_proxy": bool(EXPLICIT_MARKET_PROXY),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    while True:
        cycle_start = datetime.now(timezone.utc)
        counts = cycle_once(configs, sources=args.sources, bracket_radius=args.bracket_radius, max_workers=args.max_workers)
        cycle_end = datetime.now(timezone.utc)
        counts["cycle_runtime_sec"] = round((cycle_end - cycle_start).total_seconds(), 3)
        print(json.dumps({"ts_utc": datetime.now(timezone.utc).isoformat(), **counts}, sort_keys=True))
        if args.command == "cycle":
            return 0
        sleep_sec = args.burst_interval_sec if in_update_window(datetime.now(timezone.utc), window_min=args.burst_window_min) else args.base_interval_sec
        time.sleep(sleep_sec)


if __name__ == "__main__":
    raise SystemExit(main())
