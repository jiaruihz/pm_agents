"""Forecast enrichment adapters and feature builders.

This module is intentionally data-layer only: it fetches and normalizes
forecast-side evidence, but does not decide whether to trade.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


OPEN_METEO_FORECAST_API = "https://api.open-meteo.com/v1/forecast"
AVIATIONWEATHER_TAF_API = "https://aviationweather.gov/api/data/taf"

OPEN_METEO_MULTI_MODEL_SPECS: dict[str, dict[str, Any]] = {
    "ecmwf_ifs025": {"label": "ECMWF", "provider": "ECMWF", "tier": "global", "resolution_km": 25},
    "ecmwf_aifs025_single": {"label": "ECMWF AIFS", "provider": "ECMWF", "tier": "ai_global", "resolution_km": 25},
    "gfs_seamless": {"label": "GFS", "provider": "NOAA", "tier": "global", "resolution_km": None},
    "gfs_global": {"label": "GFS Global", "provider": "NOAA", "tier": "global", "resolution_km": 13},
    "ncep_hrrr_conus": {"label": "HRRR", "provider": "NOAA", "tier": "short_range_north_america", "resolution_km": 3},
    "ncep_nbm_conus": {"label": "NBM", "provider": "NOAA", "tier": "regional_north_america", "resolution_km": 2.5},
    "ncep_nam_conus": {"label": "NAM", "provider": "NOAA", "tier": "short_range_north_america", "resolution_km": 3},
    "ncep_gfs_graphcast025": {"label": "GFS GraphCast", "provider": "Google/NOAA", "tier": "ai_global", "resolution_km": 25},
    "ncep_aigfs025": {"label": "AI-GFS", "provider": "NOAA", "tier": "ai_global", "resolution_km": 25},
    "icon_seamless": {"label": "ICON", "provider": "DWD", "tier": "global", "resolution_km": None},
    "icon_eu": {"label": "ICON-EU", "provider": "DWD", "tier": "regional_europe", "resolution_km": 7},
    "icon_d2": {"label": "ICON-D2", "provider": "DWD", "tier": "short_range_europe", "resolution_km": 2.2},
    "gem_seamless": {"label": "GEM", "provider": "ECCC", "tier": "global", "resolution_km": None},
    "gem_global": {"label": "GDPS", "provider": "ECCC", "tier": "global", "resolution_km": 15},
    "gem_regional": {"label": "RDPS", "provider": "ECCC", "tier": "regional_north_america", "resolution_km": 10},
    "gem_hrdps_continental": {"label": "HRDPS", "provider": "ECCC", "tier": "short_range_north_america", "resolution_km": 2.5},
    "jma_seamless": {"label": "JMA", "provider": "JMA", "tier": "global", "resolution_km": None},
    "meteofrance_arome_france_hd": {"label": "AROME HD", "provider": "Meteo-France", "tier": "short_range_europe", "resolution_km": 1.3},
}

OPEN_METEO_MULTI_MODEL_ORDER: tuple[str, ...] = tuple(OPEN_METEO_MULTI_MODEL_SPECS)

OPEN_METEO_CONTEXT_HOURLY_FIELDS: tuple[str, ...] = (
    "temperature_2m",
    "shortwave_radiation",
    "dew_point_2m",
    "pressure_msl",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_speed_180m",
    "wind_direction_180m",
    "precipitation_probability",
    "cloud_cover",
    "cape",
    "convective_inhibition",
    "lifted_index",
    "boundary_layer_height",
)

OPEN_METEO_CONTEXT_DAILY_FIELDS: tuple[str, ...] = (
    "temperature_2m_max",
    "apparent_temperature_max",
    "sunrise",
    "sunset",
    "sunshine_duration",
)


@dataclass(frozen=True)
class ForecastFetchSettings:
    timeout_sec: float = 8.0
    proxy_candidates: tuple[str | None, ...] = (None,)
    user_agent: str = "pm-agent-weather-data-feed-forecast/1.0"


@dataclass(frozen=True)
class ForecastFetchResult:
    source_key: str
    status: str
    fetched_at_utc: str
    latency_ms: float
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ForecastFetchError(RuntimeError):
    pass


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _http_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    settings: ForecastFetchSettings | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    cfg = settings or ForecastFetchSettings()
    merged_headers = {"User-Agent": cfg.user_agent}
    if headers:
        merged_headers.update(headers)
    last_error = ""
    for proxy in cfg.proxy_candidates or (None,):
        try:
            response = httpx.get(
                url,
                params=params,
                headers=merged_headers,
                proxy=proxy,
                timeout=cfg.timeout_sec,
                trust_env=False,
            )
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001
            last_error = f"{proxy or 'direct'}: {type(exc).__name__}: {exc}"
    raise ForecastFetchError(f"fetch failed {url}: {last_error}")


def _result(
    source_key: str,
    *,
    status: str,
    fetch_start: datetime,
    fetch_end: datetime,
    payload: dict[str, Any] | None = None,
    error: str = "",
    metadata: dict[str, Any] | None = None,
) -> ForecastFetchResult:
    return ForecastFetchResult(
        source_key=source_key,
        status=status,
        fetched_at_utc=fetch_end.isoformat(),
        latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3),
        payload=payload or {},
        error=error,
        metadata={
            "source_fetch_start_utc": fetch_start.isoformat(),
            "source_fetch_end_utc": fetch_end.isoformat(),
            **(metadata or {}),
        },
    )


def _parse_daily_multi_model(payload: dict[str, Any]) -> dict[str, Any]:
    daily = payload.get("daily") or {}
    dates = [str(value) for value in daily.get("time") or []]
    by_date: dict[str, dict[str, Any]] = {}
    model_metadata: dict[str, dict[str, Any]] = {}
    for date_idx, target_date in enumerate(dates):
        models: dict[str, Any] = {}
        for model_key, spec in OPEN_METEO_MULTI_MODEL_SPECS.items():
            values = daily.get(f"temperature_2m_max_{model_key}") or []
            if date_idx >= len(values):
                continue
            value = safe_float(values[date_idx])
            if value is None:
                continue
            label = str(spec["label"])
            models[label] = round(value, 3)
            model_metadata[label] = {**spec, "open_meteo_model": model_key}
        values = [float(value) for value in models.values() if safe_float(value) is not None]
        by_date[target_date] = {
            "models": models,
            "model_count": len(values),
            "model_min": min(values) if values else None,
            "model_max": max(values) if values else None,
            "model_mean": round(sum(values) / len(values), 4) if values else None,
            "model_spread": round(max(values) - min(values), 4) if values else None,
        }
    return {"dates": dates, "daily": by_date, "model_metadata": model_metadata}


def _parse_hourly_multi_model(payload: dict[str, Any]) -> dict[str, Any]:
    hourly = payload.get("hourly") or {}
    times = [str(value) for value in hourly.get("time") or []]
    by_model: dict[str, list[float | None]] = {}
    hashes: dict[str, str] = {}
    for model_key, spec in OPEN_METEO_MULTI_MODEL_SPECS.items():
        values = hourly.get(f"temperature_2m_{model_key}") or []
        if not isinstance(values, list) or not values:
            continue
        parsed = [safe_float(value) for value in values[: len(times)]]
        if any(value is not None for value in parsed):
            label = str(spec["label"])
            by_model[label] = parsed
            hashes[label] = stable_hash(list(zip(times, parsed)))
    return {"times": times, "by_model": by_model, "values_hash_by_model": hashes}


def fetch_open_meteo_multi_model(
    latitude: float,
    longitude: float,
    *,
    forecast_days: int = 3,
    temperature_unit: str = "fahrenheit",
    settings: ForecastFetchSettings | None = None,
    models: tuple[str, ...] = OPEN_METEO_MULTI_MODEL_ORDER,
) -> ForecastFetchResult:
    """Fetch per-model Open-Meteo temperature forecasts.

    The raw response is preserved in metadata by hash only; normalized daily and
    hourly model arrays live in the payload so downstream replay can stay PIT.
    """

    fetch_start = datetime.now(timezone.utc)
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m",
        "daily": "temperature_2m_max",
        "models": ",".join(models),
        "temperature_unit": temperature_unit,
        "timezone": "auto",
        "forecast_days": str(forecast_days),
    }
    data = _http_get(OPEN_METEO_FORECAST_API, params=params, settings=settings).json()
    fetch_end = datetime.now(timezone.utc)
    daily = _parse_daily_multi_model(data)
    hourly = _parse_hourly_multi_model(data)
    payload = {
        "source": "open_meteo_multi_model",
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": data.get("timezone"),
        "timezone_abbreviation": data.get("timezone_abbreviation"),
        "utc_offset_seconds": data.get("utc_offset_seconds"),
        "unit": temperature_unit,
        "daily": daily["daily"],
        "daily_dates": daily["dates"],
        "hourly_times": hourly["times"],
        "hourly_temperature_by_model": hourly["by_model"],
        "hourly_values_hash_by_model": hourly["values_hash_by_model"],
        "model_metadata": daily["model_metadata"],
    }
    return _result(
        "open_meteo_multi_model",
        status="ok" if daily["daily"] or hourly["by_model"] else "empty",
        fetch_start=fetch_start,
        fetch_end=fetch_end,
        payload=payload,
        metadata={"raw_payload_hash": stable_hash(data), "request_params": params},
    )


def fetch_open_meteo_weather_context(
    latitude: float,
    longitude: float,
    *,
    forecast_days: int = 3,
    past_days: int = 1,
    temperature_unit: str = "fahrenheit",
    settings: ForecastFetchSettings | None = None,
) -> ForecastFetchResult:
    fetch_start = datetime.now(timezone.utc)
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join(OPEN_METEO_CONTEXT_HOURLY_FIELDS),
        "daily": ",".join(OPEN_METEO_CONTEXT_DAILY_FIELDS),
        "temperature_unit": temperature_unit,
        "timezone": "auto",
        "forecast_days": str(forecast_days),
        "past_days": str(past_days),
    }
    data = _http_get(OPEN_METEO_FORECAST_API, params=params, settings=settings).json()
    fetch_end = datetime.now(timezone.utc)
    payload = {
        "source": "open_meteo_weather_context",
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": data.get("timezone"),
        "timezone_abbreviation": data.get("timezone_abbreviation"),
        "utc_offset_seconds": data.get("utc_offset_seconds"),
        "unit": temperature_unit,
        "hourly": data.get("hourly") or {},
        "daily": data.get("daily") or {},
    }
    return _result(
        "open_meteo_weather_context",
        status="ok" if payload["hourly"] else "empty",
        fetch_start=fetch_start,
        fetch_end=fetch_end,
        payload=payload,
        metadata={"raw_payload_hash": stable_hash(data), "request_params": params},
    )


def target_day_hourly_summary(hourly: dict[str, Any], target_date: str) -> dict[str, Any]:
    times = [str(value) for value in hourly.get("time") or []]
    temps = hourly.get("temperature_2m") or []
    values: list[tuple[str, float]] = []
    for idx, ts in enumerate(times):
        if not ts.startswith(target_date) or idx >= len(temps):
            continue
        value = safe_float(temps[idx])
        if value is not None:
            values.append((ts, value))
    if not values:
        return {"target_date": target_date, "hourly_count": 0}
    max_temp = max(value for _ts, value in values)
    peak_times = [ts for ts, value in values if abs(value - max_temp) < 1e-9]
    peak_hours = [int(ts.split("T", 1)[1][:2]) for ts in peak_times if "T" in ts]
    return {
        "target_date": target_date,
        "hourly_count": len(values),
        "forecast_max": max_temp,
        "first_peak_hour_local": min(peak_hours) if peak_hours else None,
        "last_peak_hour_local": max(peak_hours) if peak_hours else None,
        "first_peak_time_local": peak_times[0] if peak_times else "",
        "last_peak_time_local": peak_times[-1] if peak_times else "",
        "values_hash": stable_hash(values),
    }


def _wind_components(speed: Any, direction_deg: Any) -> tuple[float | None, float | None]:
    speed_val = safe_float(speed)
    direction = safe_float(direction_deg)
    if speed_val is None or direction is None:
        return None, None
    rad = math.radians(direction)
    return -speed_val * math.sin(rad), -speed_val * math.cos(rad)


def build_vertical_profile_signal(
    hourly: dict[str, Any],
    *,
    target_date: str,
    local_hour: int,
    first_peak_hour: int,
    last_peak_hour: int,
) -> dict[str, Any]:
    times = [str(value) for value in hourly.get("time") or []]
    if not times:
        return {"available": False, "status": "missing_hourly_times"}
    start_hour = max(int(local_hour), max(0, int(first_peak_hour) - 2))
    end_hour = min(23, int(last_peak_hour) + 1)
    indexes = [
        idx
        for idx, ts in enumerate(times)
        if ts.startswith(target_date) and start_hour <= int(ts.split("T", 1)[1][:2]) <= end_hour
    ]
    if not indexes:
        indexes = [idx for idx, ts in enumerate(times) if ts.startswith(target_date)]
    if not indexes:
        return {"available": False, "status": "missing_target_date_hours"}

    def series(name: str) -> list[Any]:
        values = hourly.get(name) or []
        return [values[idx] if idx < len(values) else None for idx in indexes]

    def max_value(name: str) -> float | None:
        values = [safe_float(value) for value in series(name)]
        values = [value for value in values if value is not None]
        return max(values) if values else None

    def min_value(name: str) -> float | None:
        values = [safe_float(value) for value in series(name)]
        values = [value for value in values if value is not None]
        return min(values) if values else None

    cape_max = max_value("cape")
    cin_min = min_value("convective_inhibition")
    lifted_index_min = min_value("lifted_index")
    boundary_layer_height_max = max_value("boundary_layer_height")
    shear_values: list[float] = []
    speed_10m = hourly.get("wind_speed_10m") or []
    direction_10m = hourly.get("wind_direction_10m") or []
    speed_180m = hourly.get("wind_speed_180m") or []
    direction_180m = hourly.get("wind_direction_180m") or []
    for idx in indexes:
        u10, v10 = _wind_components(
            speed_10m[idx] if idx < len(speed_10m) else None,
            direction_10m[idx] if idx < len(direction_10m) else None,
        )
        u180, v180 = _wind_components(
            speed_180m[idx] if idx < len(speed_180m) else None,
            direction_180m[idx] if idx < len(direction_180m) else None,
        )
        if None not in (u10, v10, u180, v180):
            shear_values.append(math.sqrt((u180 - u10) ** 2 + (v180 - v10) ** 2))
    shear_10m_180m_max = max(shear_values) if shear_values else None

    suppression_risk = "low"
    if (cape_max is not None and cape_max >= 700) or (cin_min is not None and cin_min <= -50):
        suppression_risk = "high"
    elif (cape_max is not None and cape_max >= 150) or (cin_min is not None and cin_min <= -15):
        suppression_risk = "medium"

    trigger_risk = "low"
    if cape_max is not None and cape_max >= 550 and lifted_index_min is not None and lifted_index_min <= -1.5:
        trigger_risk = "high"
    elif cape_max is not None and cape_max >= 120 and lifted_index_min is not None and lifted_index_min <= 0.5:
        trigger_risk = "medium"

    mixing_strength = "weak"
    if boundary_layer_height_max is not None and boundary_layer_height_max >= 1400:
        mixing_strength = "strong"
    elif boundary_layer_height_max is not None and boundary_layer_height_max >= 700:
        mixing_strength = "medium"

    shear_risk = "low"
    if shear_10m_180m_max is not None and shear_10m_180m_max >= 8:
        shear_risk = "high"
    elif shear_10m_180m_max is not None and shear_10m_180m_max >= 4:
        shear_risk = "medium"

    heating_score = 0
    heating_score += {"high": -2, "medium": -1, "low": 0}[suppression_risk]
    heating_score += {"high": -2, "medium": -1, "low": 0}[trigger_risk]
    heating_score += {"strong": 2, "medium": 1, "weak": -1}[mixing_strength]
    if shear_risk == "high":
        heating_score -= 1
    heating_setup = "neutral"
    if heating_score >= 2:
        heating_setup = "supportive"
    elif heating_score <= -2:
        heating_setup = "suppressed"

    available = any(
        value is not None
        for value in (cape_max, cin_min, lifted_index_min, boundary_layer_height_max, shear_10m_180m_max)
    )
    return {
        "available": available,
        "source": "open_meteo_weather_context",
        "window_start": times[indexes[0]],
        "window_end": times[indexes[-1]],
        "cape_max": cape_max,
        "cin_min": cin_min,
        "lifted_index_min": lifted_index_min,
        "boundary_layer_height_max": boundary_layer_height_max,
        "shear_10m_180m_max": shear_10m_180m_max,
        "suppression_risk": suppression_risk,
        "trigger_risk": trigger_risk,
        "mixing_strength": mixing_strength,
        "shear_risk": shear_risk,
        "heating_setup": heating_setup,
        "heating_score": heating_score,
    }


def fetch_aviationweather_taf(
    station: str,
    *,
    settings: ForecastFetchSettings | None = None,
    hours: int = 24,
) -> ForecastFetchResult:
    fetch_start = datetime.now(timezone.utc)
    params = {"ids": station.upper(), "format": "json", "hours": str(hours)}
    data = _http_get(AVIATIONWEATHER_TAF_API, params=params, settings=settings).json()
    fetch_end = datetime.now(timezone.utc)
    latest = data[0] if isinstance(data, list) and data else {}
    payload = {
        "source": "aviationweather_taf",
        "station": station.upper(),
        "station_name": latest.get("name") or station.upper(),
        "issue_time": latest.get("issueTime"),
        "valid_time_from": latest.get("validTimeFrom"),
        "valid_time_to": latest.get("validTimeTo"),
        "raw_taf": latest.get("rawTAF") or latest.get("rawTaf") or latest.get("raw_text") or "",
    }
    return _result(
        "aviationweather_taf",
        status="ok" if payload["raw_taf"] else "empty",
        fetch_start=fetch_start,
        fetch_end=fetch_end,
        payload=payload,
        metadata={"raw_payload_hash": stable_hash(data), "request_params": params},
    )


def _infer_taf_utc(issue_dt: datetime, day: int, hour: int, minute: int = 0) -> datetime:
    normalized_hour = hour % 24
    day_offset = hour // 24
    candidate = datetime(issue_dt.year, issue_dt.month, day, normalized_hour, minute, tzinfo=timezone.utc)
    if day_offset:
        candidate += timedelta(days=day_offset)
    if candidate < issue_dt - timedelta(days=20):
        if issue_dt.month == 12:
            candidate = datetime(issue_dt.year + 1, 1, day, normalized_hour, minute, tzinfo=timezone.utc)
        else:
            candidate = datetime(issue_dt.year, issue_dt.month + 1, day, normalized_hour, minute, tzinfo=timezone.utc)
        candidate += timedelta(days=day_offset)
    elif candidate > issue_dt + timedelta(days=20):
        if issue_dt.month == 1:
            candidate = datetime(issue_dt.year - 1, 12, day, normalized_hour, minute, tzinfo=timezone.utc)
        else:
            candidate = datetime(issue_dt.year, issue_dt.month - 1, day, normalized_hour, minute, tzinfo=timezone.utc)
        candidate += timedelta(days=day_offset)
    return candidate


def _parse_taf_period(token: str, issue_dt: datetime) -> tuple[datetime | None, datetime | None]:
    match = re.match(r"^(\d{2})(\d{2})/(\d{2})(\d{2})$", token)
    if not match:
        return None, None
    start = _infer_taf_utc(issue_dt, int(match.group(1)), int(match.group(2)))
    end = _infer_taf_utc(issue_dt, int(match.group(3)), int(match.group(4)))
    if end <= start:
        end += timedelta(days=1)
    return start, end


def build_taf_signal(
    taf_payload: dict[str, Any],
    *,
    target_date: str,
    utc_offset_seconds: int,
    first_peak_hour: int,
    last_peak_hour: int,
) -> dict[str, Any]:
    raw_taf = re.sub(r"\s+", " ", str(taf_payload.get("raw_taf") or "").upper().strip())
    if not raw_taf:
        return {"available": False, "status": "missing_raw_taf"}
    issue_dt = parse_dt(str(taf_payload.get("issue_time") or "")) or datetime.now(timezone.utc)
    valid_match = re.search(r"\b(\d{2})(\d{2})/(\d{2})(\d{2})\b", raw_taf)
    if not valid_match:
        return {"available": False, "status": "missing_valid_period", "raw_taf": raw_taf}
    valid_start, valid_end = _parse_taf_period(valid_match.group(0), issue_dt)
    if valid_start is None or valid_end is None:
        return {"available": False, "status": "invalid_valid_period", "raw_taf": raw_taf}

    tokens = raw_taf.split()
    segment_starts = [
        idx
        for idx, token in enumerate(tokens)
        if re.match(r"^FM\d{6}$", token) or token in {"TEMPO", "BECMG", "PROB30", "PROB40"}
    ]
    base_start = 0
    for idx, token in enumerate(tokens):
        if token == valid_match.group(0):
            base_start = idx + 1
            break

    segments: list[dict[str, Any]] = []
    first_segment = segment_starts[0] if segment_starts else len(tokens)
    if base_start < first_segment:
        segments.append({"type": "BASE", "start_utc": valid_start, "end_utc": valid_end, "tokens": tokens[base_start:first_segment]})

    for pos, start_idx in enumerate(segment_starts):
        end_idx = segment_starts[pos + 1] if pos + 1 < len(segment_starts) else len(tokens)
        token = tokens[start_idx]
        seg_type = token
        start_utc = valid_start
        end_utc = valid_end
        payload_start = start_idx + 1
        fm = re.match(r"^FM(\d{2})(\d{2})(\d{2})$", token)
        if fm:
            seg_type = "FM"
            start_utc = _infer_taf_utc(issue_dt, int(fm.group(1)), int(fm.group(2)), int(fm.group(3)))
            end_utc = valid_end
        elif token in {"TEMPO", "BECMG"} and payload_start < len(tokens):
            seg_type = token
            start_utc, end_utc = _parse_taf_period(tokens[payload_start], issue_dt)
            payload_start += 1
        elif token in {"PROB30", "PROB40"}:
            seg_type = token
            if payload_start < len(tokens) and tokens[payload_start] == "TEMPO":
                seg_type = f"{token} TEMPO"
                payload_start += 1
            if payload_start < len(tokens):
                start_utc, end_utc = _parse_taf_period(tokens[payload_start], issue_dt)
                payload_start += 1
        if start_utc is None or end_utc is None:
            continue
        if end_utc <= start_utc:
            end_utc = start_utc + timedelta(hours=1)
        segments.append({"type": seg_type, "start_utc": start_utc, "end_utc": end_utc, "tokens": tokens[payload_start:end_idx]})

    local_tz = timezone(timedelta(seconds=int(utc_offset_seconds or 0)))
    peak_start = datetime.strptime(f"{target_date} {max(0, int(first_peak_hour) - 2):02d}:00", "%Y-%m-%d %H:%M").replace(tzinfo=local_tz)
    peak_end = datetime.strptime(f"{target_date} {min(23, int(last_peak_hour) + 1):02d}:00", "%Y-%m-%d %H:%M").replace(tzinfo=local_tz)
    precip_rank = {"low": 0, "medium": 1, "high": 2}
    suppression_level = "low"
    disruption_level = "low"
    low_ceiling_ft: int | None = None
    ceiling_cover = ""
    wind_regimes: list[str] = []
    active_segments: list[dict[str, Any]] = []

    def precip_level(items: list[str]) -> str:
        joined = " ".join(items)
        if re.search(r"\b(?:-|\+)?(?:TSRA|TS|VCTS|SHRA|SHSN|SHGS)\b", joined):
            return "high"
        if re.search(r"\b(?:-|\+)?(?:RA|DZ|SN)\b", joined):
            return "medium"
        return "low"

    for segment in segments:
        start_local = segment["start_utc"].astimezone(local_tz)
        end_local = segment["end_utc"].astimezone(local_tz)
        overlap_start = max(start_local, peak_start)
        overlap_end = min(end_local, peak_end)
        if overlap_end <= overlap_start:
            continue
        active_segments.append(segment)
        level = precip_level(segment["tokens"])
        if precip_rank[level] > precip_rank[suppression_level]:
            suppression_level = level
        joined = " ".join(segment["tokens"])
        for cover, base in re.findall(r"\b(FEW|SCT|BKN|OVC)(\d{3})\b", joined):
            if cover not in {"BKN", "OVC"}:
                continue
            base_ft = int(base) * 100
            if low_ceiling_ft is None or base_ft < low_ceiling_ft:
                low_ceiling_ft = base_ft
                ceiling_cover = cover
        if low_ceiling_ft is not None and low_ceiling_ft <= 4000 and suppression_level == "low":
            suppression_level = "medium"
        for direction, _speed in re.findall(r"\b(\d{3}|VRB)(\d{2,3})(?:G\d{2,3})?KT\b", joined):
            if direction == "VRB":
                regime = "variable"
            else:
                deg = int(direction)
                if 135 <= deg <= 225:
                    regime = "southerly"
                elif deg >= 315 or deg <= 45:
                    regime = "northerly"
                else:
                    regime = "cross"
            if regime not in wind_regimes:
                wind_regimes.append(regime)
        if segment["type"] in {"TEMPO", "BECMG", "PROB30", "PROB40", "PROB30 TEMPO", "PROB40 TEMPO"}:
            disruption_level = "medium" if disruption_level == "low" else disruption_level
        if segment["type"] in {"PROB30 TEMPO", "PROB40 TEMPO"} or level == "high":
            disruption_level = "high"

    return {
        "available": True,
        "source": "aviationweather_taf",
        "raw_taf": raw_taf,
        "issue_time": taf_payload.get("issue_time"),
        "valid_time_from": taf_payload.get("valid_time_from"),
        "valid_time_to": taf_payload.get("valid_time_to"),
        "peak_window": f"{peak_start.strftime('%H:%M')}-{peak_end.strftime('%H:%M')}",
        "active_segment_count": len(active_segments),
        "segments": [
            {
                "type": segment["type"],
                "start_local": segment["start_utc"].astimezone(local_tz).strftime("%H:%M"),
                "end_local": segment["end_utc"].astimezone(local_tz).strftime("%H:%M"),
                "tokens": segment["tokens"],
            }
            for segment in active_segments
        ],
        "low_ceiling_ft": low_ceiling_ft,
        "ceiling_cover": ceiling_cover,
        "wind_regimes": wind_regimes,
        "wind_shift": len(wind_regimes) >= 2 or "variable" in wind_regimes,
        "suppression_level": suppression_level,
        "disruption_level": disruption_level,
    }
