from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.strategies.weather_edge_v1.tools.profile_resolver import load_profiles, resolve_daily_plan
from src.strategies.weather_edge_v1.tools.source_probe import probe_city_sources


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WATCH_DIR = ROOT / "plan" / "watch"
AVIATION_WEATHER_BASE = "https://aviationweather.gov/api/data"
OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_MODEL_MAP: Dict[str, List[str]] = {
    "asia": ["ecmwf_ifs04", "icon_global", "jma_seamless", "gfs_seamless"],
    "europe": ["ecmwf_ifs04", "icon_global", "gfs_seamless"],
    "north_america": ["ecmwf_ifs04", "gfs_seamless", "icon_global"],
    "default": ["ecmwf_ifs04", "gfs_seamless", "icon_global"],
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _load_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _dump_json_file(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_session(proxy_url: str = "") -> requests.Session:
    session = requests.Session()
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=3,
                connect=3,
                read=3,
                backoff_factor=0.4,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=frozenset(["GET"]),
            )
        ),
    )
    proxy = proxy_url.strip()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
        session.trust_env = False
    return session


def _convert_c_to_unit(value_c: Optional[float], unit: str) -> Optional[float]:
    if value_c is None:
        return None
    if unit.upper() == "F":
        return (value_c * 9.0 / 5.0) + 32.0
    return value_c


def _bucket_bounds(meta: Dict[str, Any], unit: str) -> Tuple[Optional[float], Optional[float]]:
    bucket_min = meta.get("bucket_min")
    bucket_max = meta.get("bucket_max")
    bucket_value = meta.get("bucket_value")
    if bucket_min is None and bucket_value is not None:
        bucket_min = bucket_value
    if bucket_max is None and bucket_value is not None:
        bucket_max = bucket_value
    if bucket_min is None and bucket_max is None:
        return None, None
    lo = _safe_float(bucket_min if bucket_min is not None else bucket_max, 0.0)
    hi = _safe_float(bucket_max if bucket_max is not None else bucket_min, 0.0)
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _edge_to_bucket(value: Optional[float], meta: Dict[str, Any], unit: str) -> Optional[float]:
    if value is None:
        return None
    lo, hi = _bucket_bounds(meta, unit)
    if lo is None or hi is None:
        return None
    if lo <= value <= hi:
        return 0.0
    return min(abs(value - lo), abs(value - hi))


def _estimate_main_range(hourly_values: List[float], daily_max: Optional[float]) -> Tuple[Optional[int], Optional[int]]:
    if daily_max is None:
        return None, None
    peak_values = [value for value in hourly_values if (daily_max - value) <= 0.8]
    if not peak_values:
        peak_values = [daily_max]
    return math.floor(min(peak_values)), math.ceil(max(peak_values))


def _direction_from_shift(shift: Optional[float], unit: str) -> str:
    if shift is None:
        return "unknown"
    stable_band = 0.6 if unit.upper() == "C" else 1.0
    if shift >= stable_band:
        return "warmer"
    if shift <= -stable_band:
        return "cooler"
    return "stable"


def _range_shift_bins(latest_forecast: Dict[str, Any], baseline_forecast: Dict[str, Any]) -> int:
    latest_lo = latest_forecast.get("main_range_low")
    latest_hi = latest_forecast.get("main_range_high")
    base_lo = baseline_forecast.get("main_range_low")
    base_hi = baseline_forecast.get("main_range_high")
    if latest_lo is None or latest_hi is None:
        return 0
    if base_lo is None or base_hi is None:
        return 0
    latest_center = (_safe_float(latest_lo, 0.0) + _safe_float(latest_hi, 0.0)) / 2.0
    base_center = (_safe_float(base_lo, 0.0) + _safe_float(base_hi, 0.0)) / 2.0
    return int(round(abs(latest_center - base_center)))


def _peak_hour_shift_minutes(latest_forecast: Dict[str, Any], baseline_forecast: Dict[str, Any]) -> int:
    latest_peak = str(latest_forecast.get("peak_hour_local") or "").strip()
    base_peak = str(baseline_forecast.get("peak_hour_local") or "").strip()
    if len(latest_peak) < 5 or len(base_peak) < 5:
        return 0
    latest_hour, latest_min = latest_peak.split(":")[:2]
    base_hour, base_min = base_peak.split(":")[:2]
    latest_total = int(latest_hour) * 60 + int(latest_min)
    base_total = int(base_hour) * 60 + int(base_min)
    return abs(latest_total - base_total)


def _parse_taf_temp_token(token: str) -> Optional[int]:
    text = str(token or "").strip().upper()
    if not text:
        return None
    if text.startswith("M"):
        return -_safe_int(text[1:], 0)
    return _safe_int(text, 0)


def _parse_taf_summary(raw_taf: str) -> Dict[str, Any]:
    text = str(raw_taf or "").strip()
    if not text:
        return {}
    tx_match = re.search(r"\bTX(M?\d{2})/(\d{4})Z\b", text)
    tn_match = re.search(r"\bTN(M?\d{2})/(\d{4})Z\b", text)
    return {
        "raw_taf": text,
        "forecast_max_temp_c": _parse_taf_temp_token(tx_match.group(1)) if tx_match else None,
        "forecast_max_marker_utc": tx_match.group(2) if tx_match else "",
        "forecast_min_temp_c": _parse_taf_temp_token(tn_match.group(1)) if tn_match else None,
        "forecast_min_marker_utc": tn_match.group(2) if tn_match else "",
    }


def _open_meteo_model_family(city_key: str, timezone_name: str) -> List[str]:
    tz = str(timezone_name or "").lower()
    key = str(city_key or "").lower()
    if tz.startswith("asia/") or key in {"seoul", "tokyo", "osaka", "taipei", "hong_kong", "shanghai", "singapore", "dubai", "doha"}:
        return list(OPEN_METEO_MODEL_MAP["asia"])
    if tz.startswith("europe/"):
        return list(OPEN_METEO_MODEL_MAP["europe"])
    if tz.startswith("america/"):
        return list(OPEN_METEO_MODEL_MAP["north_america"])
    return list(OPEN_METEO_MODEL_MAP["default"])


def _extract_open_meteo_model_day(data: Dict[str, Any], local_date: str) -> Dict[str, Any]:
    daily = data.get("daily", {}) or {}
    hourly = data.get("hourly", {}) or {}
    daily_dates = daily.get("time", []) or []
    hourly_times = hourly.get("time", []) or []
    hourly_temps_c = hourly.get("temperature_2m", []) or []

    if local_date not in daily_dates:
        return {"status": "target_date_missing"}
    idx = daily_dates.index(local_date)
    max_temp_c = _safe_float((daily.get("temperature_2m_max", []) or [None])[idx], None)  # type: ignore[arg-type]
    min_temp_c = _safe_float((daily.get("temperature_2m_min", []) or [None])[idx], None)  # type: ignore[arg-type]
    precip = _safe_float((daily.get("precipitation_probability_max", []) or [None])[idx], None)  # type: ignore[arg-type]

    local_hourly_c: List[float] = []
    peak_hour_local = ""
    for raw_time, raw_temp in zip(hourly_times, hourly_temps_c):
        if not str(raw_time).startswith(local_date):
            continue
        temp_c = _safe_float(raw_temp, 0.0)
        local_hourly_c.append(temp_c)
        if max_temp_c is not None and abs(temp_c - max_temp_c) <= 0.05 and not peak_hour_local:
            peak_hour_local = str(raw_time).split("T", 1)[1]

    return {
        "status": "ok",
        "max_temp_c": max_temp_c,
        "min_temp_c": min_temp_c,
        "precipitation_probability_max": precip,
        "peak_hour_local": peak_hour_local,
        "hourly_temps_c": local_hourly_c,
    }


def _minutes_from_hhmm(value: str) -> Optional[int]:
    text = str(value or "").strip()
    if len(text) < 5 or ":" not in text:
        return None
    hour, minute = text.split(":")[:2]
    return (_safe_int(hour, -1) * 60 + _safe_int(minute, -1)) if _safe_int(hour, -1) >= 0 else None


def _summarize_model_runs(model_rows: Dict[str, Dict[str, Any]], market_unit: str) -> Dict[str, Any]:
    available = {k: v for k, v in model_rows.items() if v.get("status") == "ok"}
    max_values = [(name, _safe_float(row.get("max_temp_c"), None)) for name, row in available.items()]
    max_values = [(name, value) for name, value in max_values if value is not None]
    peak_values = [(name, str(row.get("peak_hour_local") or "")) for name, row in available.items() if row.get("peak_hour_local")]

    summary: Dict[str, Any] = {
        "available_model_count": len(available),
        "requested_model_count": len(model_rows),
        "models": model_rows,
        "status": "ok" if available else "data_gap",
    }
    if max_values:
        warmest = max(max_values, key=lambda item: item[1])
        coolest = min(max_values, key=lambda item: item[1])
        values = sorted(item[1] for item in max_values)
        summary["warmest_model"] = warmest[0]
        summary["warmest_max_temp_c"] = warmest[1]
        summary["coolest_model"] = coolest[0]
        summary["coolest_max_temp_c"] = coolest[1]
        summary["max_temp_spread_c"] = round(max(values) - min(values), 2)
        summary["consensus_max_temp_c"] = values[len(values) // 2]
        if market_unit.upper() == "F":
            summary["max_temp_spread_market_unit"] = round((summary["max_temp_spread_c"] * 9.0 / 5.0), 2)
            summary["consensus_max_temp_market_unit"] = round((summary["consensus_max_temp_c"] * 9.0 / 5.0) + 32.0, 2)
        else:
            summary["max_temp_spread_market_unit"] = summary["max_temp_spread_c"]
            summary["consensus_max_temp_market_unit"] = summary["consensus_max_temp_c"]
    else:
        summary["max_temp_spread_c"] = None
        summary["max_temp_spread_market_unit"] = None
        summary["consensus_max_temp_c"] = None
        summary["consensus_max_temp_market_unit"] = None

    if peak_values:
        minute_values = [(name, _minutes_from_hhmm(value)) for name, value in peak_values]
        minute_values = [(name, value) for name, value in minute_values if value is not None]
        if minute_values:
            sorted_minutes = sorted(item[1] for item in minute_values)
            min_item = min(minute_values, key=lambda item: item[1])
            max_item = max(minute_values, key=lambda item: item[1])
            median_minute = sorted_minutes[len(sorted_minutes) // 2]
            summary["peak_window_start_local"] = f"{min_item[1] // 60:02d}:{min_item[1] % 60:02d}"
            summary["peak_window_end_local"] = f"{max_item[1] // 60:02d}:{max_item[1] % 60:02d}"
            summary["peak_hour_spread_minutes"] = max_item[1] - min_item[1]
            summary["consensus_peak_hour_local"] = f"{median_minute // 60:02d}:{median_minute % 60:02d}"
    else:
        summary["peak_window_start_local"] = ""
        summary["peak_window_end_local"] = ""
        summary["peak_hour_spread_minutes"] = None
        summary["consensus_peak_hour_local"] = ""
    return summary


def _compact_validation_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source_key": str(row.get("source_key") or ""),
        "family": str(row.get("family") or ""),
        "page_type": str(row.get("page_type") or ""),
        "role": str(row.get("role") or ""),
        "status": str(row.get("status") or ""),
        "station_match": bool(row.get("station_match")),
        "title": str(row.get("title") or ""),
        "final_url": str(row.get("final_url") or row.get("url") or ""),
    }


def _validation_ok(row: Dict[str, Any]) -> bool:
    return str(row.get("status") or "") == "ok" and bool(row.get("station_match"))


def _summarize_secondary_validation(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    compact_rows = [_compact_validation_row(row) for row in rows if isinstance(row, dict)]
    by_key = {str(row.get("source_key") or ""): row for row in compact_rows if row.get("source_key")}
    local_rows = [
        row
        for row in compact_rows
        if str(row.get("family") or "") in {"korea_amo", "jma"}
    ]
    summary = {
        "rows": compact_rows,
        "wunderground_history_daily": by_key.get("wunderground_history_daily", {}),
        "wunderground_hourly": by_key.get("wunderground_hourly", {}),
        "checkwx_metar": by_key.get("checkwx_metar", {}),
        "checkwx_taf": by_key.get("checkwx_taf", {}),
        "local_official_rows": local_rows,
        "available_count": sum(1 for row in compact_rows if str(row.get("status") or "") == "ok"),
        "validation_flags": [],
    }
    flags: List[str] = []
    if summary["wunderground_hourly"] and not _validation_ok(summary["wunderground_hourly"]):
        flags.append("wu_hourly_validation_gap")
    if summary["wunderground_history_daily"] and not _validation_ok(summary["wunderground_history_daily"]):
        flags.append("wu_history_validation_gap")
    checkwx_metar_ok = _validation_ok(summary["checkwx_metar"])
    checkwx_taf_ok = _validation_ok(summary["checkwx_taf"])
    if summary["checkwx_metar"] or summary["checkwx_taf"]:
        if not checkwx_metar_ok and not checkwx_taf_ok:
            flags.append("checkwx_validation_gap")
    if local_rows and not any(_validation_ok(row) for row in local_rows):
        flags.append("local_official_validation_gap")
    summary["validation_flags"] = flags
    return summary


def _market_key(city_key: str, local_date: str) -> str:
    return f"{city_key.strip().lower()}|{local_date.strip()}"


def _resolve_watch_path(path: Path, local_date: str) -> Path:
    raw = str(path)
    if "{date}" in raw:
        return Path(raw.replace("{date}", local_date))
    if path.exists() and path.is_dir():
        return path / f"weather_watch_{local_date}.json"
    if path.suffix.lower() != ".json":
        return path / f"weather_watch_{local_date}.json"
    return path


def _resolve_watch_archive_path(path: Path, entry: Dict[str, Any]) -> Path:
    target_path = _resolve_watch_path(path, str(entry.get("local_date") or ""))
    archive_root = target_path.parent / "weather_watch_archive"
    city_key = str(entry.get("city_key") or "unknown").strip().lower() or "unknown"
    local_date = str(entry.get("local_date") or "unknown").strip() or "unknown"
    return archive_root / local_date / f"{city_key}.jsonl"


def _append_watch_archive(path: Path, entry: Dict[str, Any]) -> None:
    archive_path = _resolve_watch_archive_path(path, entry)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


@dataclass
class AirportWeatherTool:
    proxy_url: str = ""
    timeout_sec: float = 20.0

    def __post_init__(self) -> None:
        self.session = _build_session(self.proxy_url)

    def fetch_station_info(self, station_code: str) -> Dict[str, Any]:
        resp = self.session.get(
            f"{AVIATION_WEATHER_BASE}/stationinfo",
            params={"ids": station_code, "format": "json"},
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        rows = resp.json()
        if isinstance(rows, list) and rows:
            return rows[0] if isinstance(rows[0], dict) else {}
        return {}

    def fetch_metar(self, station_code: str) -> Dict[str, Any]:
        resp = self.session.get(
            f"{AVIATION_WEATHER_BASE}/metar",
            params={"ids": station_code, "format": "json"},
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        rows = resp.json()
        if isinstance(rows, list) and rows:
            return rows[0] if isinstance(rows[0], dict) else {}
        return {}

    def fetch_taf(self, station_code: str) -> Dict[str, Any]:
        resp = self.session.get(
            f"{AVIATION_WEATHER_BASE}/taf",
            params={"ids": station_code, "format": "json"},
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        rows = resp.json()
        if isinstance(rows, list) and rows:
            return rows[0] if isinstance(rows[0], dict) else {}
        return {}

    def fetch_forecast(self, *, lat: float, lon: float, timezone_name: str, local_date: str) -> Dict[str, Any]:
        resp = self.session.get(
            OPEN_METEO_BASE,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "hourly": "temperature_2m",
                "forecast_days": 7,
                "timezone": timezone_name,
            },
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        data = resp.json()

        daily = data.get("daily", {}) or {}
        hourly = data.get("hourly", {}) or {}
        daily_dates = daily.get("time", []) or []
        hourly_times = hourly.get("time", []) or []
        hourly_temps_c = hourly.get("temperature_2m", []) or []

        try:
            idx = daily_dates.index(local_date)
        except ValueError as exc:
            raise RuntimeError(f"target date {local_date} not found in forecast payload") from exc

        max_temp_c = _safe_float((daily.get("temperature_2m_max", []) or [None])[idx], None)  # type: ignore[arg-type]
        min_temp_c = _safe_float((daily.get("temperature_2m_min", []) or [None])[idx], None)  # type: ignore[arg-type]
        precip = _safe_float((daily.get("precipitation_probability_max", []) or [None])[idx], None)  # type: ignore[arg-type]

        local_hourly_c: List[float] = []
        peak_hour_local = ""
        for raw_time, raw_temp in zip(hourly_times, hourly_temps_c):
            if not str(raw_time).startswith(local_date):
                continue
            temp_c = _safe_float(raw_temp, 0.0)
            local_hourly_c.append(temp_c)

        if local_hourly_c and max_temp_c is not None:
            for raw_time, raw_temp in zip(hourly_times, hourly_temps_c):
                if not str(raw_time).startswith(local_date):
                    continue
                temp_c = _safe_float(raw_temp, 0.0)
                if abs(temp_c - max_temp_c) <= 0.05:
                    peak_hour_local = str(raw_time).split("T", 1)[1]
                    break

        return {
            "forecast_date": local_date,
            "max_temp_c": max_temp_c,
            "min_temp_c": min_temp_c,
            "precipitation_probability_max": precip,
            "hourly_temps_c": local_hourly_c,
            "peak_hour_local": peak_hour_local,
            "raw": data,
        }

    def fetch_model_forecasts(
        self,
        *,
        city_key: str,
        lat: float,
        lon: float,
        timezone_name: str,
        local_date: str,
        models: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        requested_models = list(models or _open_meteo_model_family(city_key, timezone_name))
        per_model: Dict[str, Dict[str, Any]] = {}
        for model_name in requested_models:
            try:
                resp = self.session.get(
                    OPEN_METEO_BASE,
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                        "hourly": "temperature_2m",
                        "forecast_days": 7,
                        "timezone": timezone_name,
                        "models": model_name,
                    },
                    timeout=self.timeout_sec,
                )
                resp.raise_for_status()
                payload = resp.json()
                parsed = _extract_open_meteo_model_day(payload, local_date)
                parsed["request_model"] = model_name
                per_model[model_name] = parsed
            except Exception as exc:
                per_model[model_name] = {
                    "status": "error",
                    "request_model": model_name,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        return _summarize_model_runs(per_model, "C")

    def fetch_secondary_validation(self, *, city_key: str, local_date: str) -> Dict[str, Any]:
        try:
            rows = probe_city_sources(city_key=city_key, local_date=local_date)
            return _summarize_secondary_validation(rows)
        except Exception as exc:
            return {
                "rows": [],
                "wunderground_history_daily": {},
                "wunderground_hourly": {},
                "checkwx_metar": {},
                "checkwx_taf": {},
                "local_official_rows": [],
                "available_count": 0,
                "validation_flags": ["secondary_validation_error"],
                "error": f"{type(exc).__name__}: {exc}",
            }

    def build_snapshot(
        self,
        *,
        city_key: str,
        local_date: str,
        market_meta: Optional[Dict[str, Any]] = None,
        latest_orderbook: Optional[Dict[str, Any]] = None,
        baseline_forecast: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        bundle = load_profiles(city_key=city_key, local_date=local_date)
        station = dict(bundle.station)
        market_meta = dict(market_meta or {})
        latest_orderbook = dict(latest_orderbook or {})
        station_code = str(station.get("station_code") or station.get("icao_id") or "").strip()
        if not station_code:
            raise RuntimeError(f"missing station_code for city={city_key}")

        station_info = self.fetch_station_info(station_code)
        lat = _safe_float(station.get("lat"), _safe_float(station_info.get("lat"), 0.0))
        lon = _safe_float(station.get("lon"), _safe_float(station_info.get("lon"), 0.0))
        if not lat and station_info.get("lat") is None:
            raise RuntimeError(f"missing latitude for station={station_code}")

        station["lat"] = lat
        station["lon"] = lon
        station["icao_id"] = station_info.get("icaoId") or station_code
        station["iata_id"] = station_info.get("iataId") or station.get("iata_id") or ""
        station["station_name"] = station_info.get("site") or station.get("station_name") or station_code

        observation_raw = self.fetch_metar(station_code)
        taf_raw = self.fetch_taf(station_code)
        market_unit = str(market_meta.get("unit") or station.get("market_unit") or "C").upper()
        latest_observation = {
            "station_code": station_code,
            "observed_at_utc": observation_raw.get("reportTime") or observation_raw.get("receiptTime") or "",
            "temp_c": observation_raw.get("temp"),
            "temp_f": _convert_c_to_unit(_safe_float(observation_raw.get("temp"), None), "F"),
            "dewp_c": observation_raw.get("dewp"),
            "wind_dir_deg": observation_raw.get("wdir"),
            "wind_speed_kt": observation_raw.get("wspd"),
            "visibility": observation_raw.get("visib"),
            "raw_metar": observation_raw.get("rawOb") or "",
            "flight_category": observation_raw.get("fltCat") or "",
        }
        taf_summary = {
            "station_code": station_code,
            "issue_time_utc": taf_raw.get("issueTime") or taf_raw.get("bulletinTime") or "",
            "valid_time_from_utc": taf_raw.get("validTimeFrom"),
            "valid_time_to_utc": taf_raw.get("validTimeTo"),
            **_parse_taf_summary(str(taf_raw.get("rawTAF") or "")),
        }
        secondary_validation = self.fetch_secondary_validation(city_key=city_key, local_date=local_date)

        multi_model_forecast = self.fetch_model_forecasts(
            city_key=city_key,
            lat=lat,
            lon=lon,
            timezone_name=str(station.get("timezone") or "UTC"),
            local_date=local_date,
        )
        try:
            forecast_raw = self.fetch_forecast(
                lat=lat,
                lon=lon,
                timezone_name=str(station.get("timezone") or "UTC"),
                local_date=local_date,
            )
            forecast_status = "ok"
        except Exception as exc:
            forecast_raw = {
                "forecast_date": local_date,
                "max_temp_c": multi_model_forecast.get("consensus_max_temp_c"),
                "min_temp_c": None,
                "precipitation_probability_max": None,
                "hourly_temps_c": [],
                "peak_hour_local": multi_model_forecast.get("consensus_peak_hour_local") or "",
                "raw": {},
                "error": f"{type(exc).__name__}: {exc}",
            }
            forecast_status = "multi_model_fallback" if multi_model_forecast.get("available_model_count") else "data_gap"

        max_temp_unit = _convert_c_to_unit(forecast_raw.get("max_temp_c"), market_unit)
        min_temp_unit = _convert_c_to_unit(forecast_raw.get("min_temp_c"), market_unit)
        hourly_values_unit = [
            _convert_c_to_unit(_safe_float(value, 0.0), market_unit) or 0.0
            for value in forecast_raw.get("hourly_temps_c", [])
        ]
        main_range_low, main_range_high = _estimate_main_range(hourly_values_unit, max_temp_unit)
        latest_forecast = {
            "forecast_date": local_date,
            "max_temp_c": forecast_raw.get("max_temp_c"),
            "max_temp_f": _convert_c_to_unit(forecast_raw.get("max_temp_c"), "F"),
            "min_temp_c": forecast_raw.get("min_temp_c"),
            "min_temp_f": _convert_c_to_unit(forecast_raw.get("min_temp_c"), "F"),
            "max_temp_market_unit": max_temp_unit,
            "min_temp_market_unit": min_temp_unit,
            "market_unit": market_unit,
            "main_range_low": main_range_low,
            "main_range_high": main_range_high,
            "peak_hour_local": forecast_raw.get("peak_hour_local") or "",
            "precipitation_probability_max": forecast_raw.get("precipitation_probability_max"),
            "forecast_status": forecast_status,
            "forecast_error": forecast_raw.get("error") or "",
            "taf_max_temp_c": taf_summary.get("forecast_max_temp_c"),
            "taf_issue_time_utc": taf_summary.get("issue_time_utc"),
            "multi_model_available_count": multi_model_forecast.get("available_model_count"),
            "multi_model_requested_count": multi_model_forecast.get("requested_model_count"),
            "multi_model_max_temp_spread_c": multi_model_forecast.get("max_temp_spread_c"),
            "multi_model_peak_window_start_local": multi_model_forecast.get("peak_window_start_local"),
            "multi_model_peak_window_end_local": multi_model_forecast.get("peak_window_end_local"),
            "multi_model_peak_hour_spread_minutes": multi_model_forecast.get("peak_hour_spread_minutes"),
            "multi_model_consensus_max_temp_c": multi_model_forecast.get("consensus_max_temp_c"),
            "multi_model_consensus_peak_hour_local": multi_model_forecast.get("consensus_peak_hour_local"),
        }
        latest_forecast["edge_to_bucket"] = _edge_to_bucket(max_temp_unit, market_meta, market_unit)
        if latest_forecast["edge_to_bucket"] is not None:
            latest_forecast["tail_distance_bins"] = int(math.floor(latest_forecast["edge_to_bucket"]))
        forecast_flags: List[str] = []
        model_spread_c = _safe_float(multi_model_forecast.get("max_temp_spread_c"), None)
        if model_spread_c is not None and model_spread_c >= 1.5:
            forecast_flags.append("multi_model_wide_temp_spread")
        peak_spread_minutes = _safe_float(multi_model_forecast.get("peak_hour_spread_minutes"), None)
        if peak_spread_minutes is not None and peak_spread_minutes >= 120:
            forecast_flags.append("multi_model_wide_peak_window")
        taf_max_c = _safe_float(taf_summary.get("forecast_max_temp_c"), None)
        if taf_max_c is None:
            forecast_flags.append("taf_missing_tx")
        if forecast_status != "ok":
            forecast_flags.append("primary_forecast_missing")
        for flag in secondary_validation.get("validation_flags", []) or []:
            text = str(flag).strip()
            if text:
                forecast_flags.append(text)
        latest_forecast["risk_flags"] = forecast_flags

        baseline = dict(baseline_forecast or {})
        if baseline:
            baseline_max = baseline.get("max_temp_market_unit")
            if baseline_max is None:
                baseline_max = baseline.get("max_temp_f") if market_unit == "F" else baseline.get("max_temp_c")
            latest_forecast["forecast_shift_vs_baseline"] = (
                None if baseline_max is None or max_temp_unit is None else max_temp_unit - _safe_float(baseline_max, 0.0)
            )
            latest_forecast["forecast_direction"] = _direction_from_shift(
                latest_forecast.get("forecast_shift_vs_baseline"),
                market_unit,
            )
            latest_forecast["main_range_shift_bins"] = _range_shift_bins(latest_forecast, baseline)
            latest_forecast["peak_hour_shift_minutes"] = _peak_hour_shift_minutes(latest_forecast, baseline)
        else:
            latest_forecast["forecast_shift_vs_baseline"] = None
            latest_forecast["forecast_direction"] = "baseline"
            latest_forecast["main_range_shift_bins"] = 0
            latest_forecast["peak_hour_shift_minutes"] = 0

        decision = resolve_daily_plan(
            city_key=city_key,
            local_date=local_date,
            baseline_forecast=baseline,
            latest_forecast=latest_forecast,
            latest_observation=latest_observation,
            latest_orderbook=latest_orderbook,
        )
        drift_reasons: List[str] = []
        sl_scheme = dict(bundle.trading.get("sl_scheme") or {})
        main_range_threshold = max(1, int(_safe_float(sl_scheme.get("invalidate_on_main_range_shift_bins"), 1)))
        peak_hour_threshold = max(1, int(_safe_float(sl_scheme.get("invalidate_on_peak_hour_shift_minutes"), 90)))
        forecast_shift_threshold = _safe_float(
            sl_scheme.get("invalidate_on_forecast_shift_f" if market_unit == "F" else "invalidate_on_forecast_shift_c"),
            2.0 if market_unit == "F" else 1.0,
        )
        if int(latest_forecast.get("main_range_shift_bins") or 0) >= main_range_threshold:
            drift_reasons.append(f"main_range_shift_bins>={main_range_threshold}")
        shift_vs_baseline = latest_forecast.get("forecast_shift_vs_baseline")
        if shift_vs_baseline is not None:
            if abs(_safe_float(shift_vs_baseline, 0.0)) >= forecast_shift_threshold:
                drift_reasons.append(f"forecast_shift_vs_baseline={_safe_float(shift_vs_baseline, 0.0):+.1f}{market_unit}")
        if int(latest_forecast.get("peak_hour_shift_minutes") or 0) >= peak_hour_threshold:
            drift_reasons.append(f"peak_hour_shift_minutes>={peak_hour_threshold}")

        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "city_key": city_key,
            "local_date": local_date,
            "station": station,
            "station_info": station_info,
            "market_meta": market_meta,
            "baseline_forecast": baseline,
            "latest_forecast": latest_forecast,
            "latest_observation": latest_observation,
            "taf_summary": taf_summary,
            "multi_model_forecast": multi_model_forecast,
            "secondary_validation": secondary_validation,
            "latest_orderbook": latest_orderbook,
            "daily_overrides": decision.get("daily_overrides", {}),
            "action_suggestion": decision.get("action_suggestion", {}),
            "drift_thresholds": {
                "main_range_shift_bins": main_range_threshold,
                "forecast_shift": forecast_shift_threshold,
                "forecast_shift_unit": market_unit,
                "peak_hour_shift_minutes": peak_hour_threshold,
            },
            "drift_alert": {
                "triggered": bool(drift_reasons),
                "reasons": drift_reasons,
            },
        }


def load_watch_baseline(path: Path) -> Dict[str, Any]:
    if path.exists() and path.is_dir():
        merged: Dict[str, Any] = {"generated_at_utc": "", "entries": []}
        all_entries: List[Dict[str, Any]] = []
        for child in sorted(path.glob("weather_watch_*.json")):
            child_payload = _load_json_file(child)
            for item in child_payload.get("entries", []):
                if isinstance(item, dict):
                    all_entries.append(item)
        merged["entries"] = all_entries
        return merged
    data = _load_json_file(path)
    entries = data.get("entries")
    if isinstance(entries, list):
        normalized = []
        for item in entries:
            if isinstance(item, dict):
                normalized.append(item)
        data["entries"] = normalized
    else:
        data["entries"] = []
    return data


def upsert_watch_entry(path: Path, entry: Dict[str, Any]) -> Dict[str, Any]:
    target_path = _resolve_watch_path(path, str(entry.get("local_date") or ""))
    payload = _load_json_file(target_path)
    if not isinstance(payload.get("entries"), list):
        payload["entries"] = []
    entries = payload.setdefault("entries", [])
    assert isinstance(entries, list)
    key = _market_key(str(entry.get("city_key") or ""), str(entry.get("local_date") or ""))
    replaced = False
    for idx, existing in enumerate(entries):
        existing_key = _market_key(str(existing.get("city_key") or ""), str(existing.get("local_date") or ""))
        if existing_key == key:
            entries[idx] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)
    payload["generated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _dump_json_file(target_path, payload)
    _append_watch_archive(path, entry)
    return load_watch_baseline(path if path.exists() and path.is_dir() else target_path)


def build_watch_entry(
    *,
    city_key: str,
    local_date: str,
    token_id: str = "",
    market_title: str = "",
    outcome: str = "No",
    bucket_min: Optional[float] = None,
    bucket_max: Optional[float] = None,
    bucket_value: Optional[float] = None,
    unit: str = "",
    best_bid: float = 0.0,
    best_ask: float = 0.0,
    baseline_file: Path = DEFAULT_WATCH_DIR,
    proxy_url: str = "",
) -> Dict[str, Any]:
    payload = load_watch_baseline(baseline_file)
    key = _market_key(city_key, local_date)
    baseline_forecast: Dict[str, Any] = {}
    for item in payload.get("entries", []):
        if _market_key(str(item.get("city_key") or ""), str(item.get("local_date") or "")) == key:
            baseline_forecast = dict(item.get("baseline_forecast") or {})
            break

    bundle = load_profiles(city_key=city_key, local_date=local_date)
    effective_unit = unit.strip().upper() or str(bundle.station.get("market_unit") or "C").upper()
    market_meta = {
        "token_id": token_id,
        "title": market_title,
        "outcome": outcome,
        "unit": effective_unit,
    }
    if bucket_min is not None:
        market_meta["bucket_min"] = bucket_min
    if bucket_max is not None:
        market_meta["bucket_max"] = bucket_max
    if bucket_value is not None:
        market_meta["bucket_value"] = bucket_value

    tool = AirportWeatherTool(proxy_url=proxy_url)
    snapshot = tool.build_snapshot(
        city_key=city_key,
        local_date=local_date,
        market_meta=market_meta,
        latest_orderbook={"best_bid": best_bid, "best_ask": best_ask},
        baseline_forecast=baseline_forecast,
    )

    baseline = baseline_forecast or dict(snapshot.get("latest_forecast") or {})
    entry = {
        "city_key": city_key,
        "local_date": local_date,
        "token_id": token_id,
        "market_title": market_title,
        "outcome": outcome,
        "market_meta": market_meta,
        "station": snapshot.get("station", {}),
        "baseline_forecast": baseline,
        "latest_snapshot": snapshot,
    }
    upsert_watch_entry(baseline_file, entry)
    return entry


def _main() -> int:
    parser = argparse.ArgumentParser(description="Fetch airport observation + forecast, update watch baseline file, and print snapshot.")
    parser.add_argument("--city-key", required=True)
    parser.add_argument("--local-date", required=True)
    parser.add_argument("--token-id", default="")
    parser.add_argument("--market-title", default="")
    parser.add_argument("--outcome", default="No")
    parser.add_argument("--bucket-min", type=float, default=None)
    parser.add_argument("--bucket-max", type=float, default=None)
    parser.add_argument("--bucket-value", type=float, default=None)
    parser.add_argument("--unit", default="")
    parser.add_argument("--best-bid", type=float, default=0.0)
    parser.add_argument("--best-ask", type=float, default=0.0)
    parser.add_argument("--baseline-file", default=str(DEFAULT_WATCH_DIR))
    parser.add_argument("--proxy-url", default="")
    args = parser.parse_args()

    entry = build_watch_entry(
        city_key=args.city_key,
        local_date=args.local_date,
        token_id=args.token_id,
        market_title=args.market_title,
        outcome=args.outcome,
        bucket_min=args.bucket_min,
        bucket_max=args.bucket_max,
        bucket_value=args.bucket_value,
        unit=args.unit,
        best_bid=args.best_bid,
        best_ask=args.best_ask,
        baseline_file=Path(args.baseline_file),
        proxy_url=args.proxy_url,
    )
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
