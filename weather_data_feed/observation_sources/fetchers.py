from __future__ import annotations

import hashlib
import csv
import gzip
import io
import json
import math
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from weather_data_feed.models import CityConfig, ObservationRecord
from weather_data_feed.physical_features import metar_physical_features
from weather_data_feed.observation_sources.aliases import normalize_source_name
from weather_data_feed.observation_sources.aviationweather import (
    parse_aviationweather_records,
    parse_awc_cache_csv_records,
)
from weather_data_feed.observation_sources.iem import IEM_ASOS_API, build_iem_asos_params, parse_iem_asos_records
from weather_data_feed.observation_sources.metar import (
    parse_metar_report_time,
    parse_metar_temp_c,
    parse_tgftp_header_time,
)
from weather_data_feed.observation_sources.router import ObservationSourceRequest, ObservationSourceResult


METAR_API = "https://aviationweather.gov/api/data/metar"
AWC_METARS_CACHE_CSV_GZ = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"
CHECKWX_URL = "https://www.checkwx.com/weather/{icao}/metar"
NOAA_TGFTP_STATION_TXT = "https://tgftp.nws.noaa.gov/data/observations/metar/stations/{icao}.TXT"
WEATHER_GOV_LATEST_OBS = "https://api.weather.gov/stations/{icao}/observations/latest"
WRH_API_KEY_JS = "https://www.weather.gov/source/wrh/apiKey.js"
SYNOPTIC_TIMESERIES_API = "https://api.synopticdata.com/v2/stations/timeseries"
WEATHER_COM_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
WEATHER_COM_CURRENT_OBS = "https://api.weather.com/v3/wx/observations/current"
WEATHER_COM_HISTORICAL_OBS = "https://api.weather.com/v1/location/{location}/observations/historical.json"

CHECKWX_OBS_RE = re.compile(r"Observed.*?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", re.IGNORECASE | re.DOTALL)
ICAO_RE = re.compile(r"^[A-Z0-9]{4}$")
WRH_SITE_RE = re.compile(r"[?&]site=([A-Z0-9]{4})\b", re.IGNORECASE)
SYNOPTIC_TOKEN_RE = re.compile(r"['\"]([a-f0-9]{32})['\"]")
METAR_RMK_T_RE = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")

_SYNOPTIC_TOKEN_CACHE: str | None = None
_AWC_CACHE_TEXT: str | None = None
_AWC_CACHE_LOCK = threading.Lock()
_IEM_RAW_TEXT_CACHE: dict[tuple[str, str, str], str] = {}
_IEM_RAW_TEXT_LOCK = threading.Lock()


def relative_humidity_pct(temp_c: float | None, dewpoint_c: float | None) -> float | None:
    """Derive RH from temperature/dewpoint when a source omits it."""
    if temp_c is None or dewpoint_c is None:
        return None
    try:
        temp = float(temp_c)
        dewpoint = float(dewpoint_c)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(temp) or not math.isfinite(dewpoint):
        return None
    a, b = 17.625, 243.04
    rh = 100.0 * math.exp(a * dewpoint / (b + dewpoint) - a * temp / (b + temp))
    return max(0.0, min(100.0, rh))


def aviationweather_sky_code(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    covers: list[str] = []
    clouds = raw.get("clouds")
    if isinstance(clouds, list):
        for layer in clouds:
            if isinstance(layer, dict):
                cover = str(layer.get("cover") or "").upper().strip()
                if cover:
                    covers.append(cover)
    for key in ("cover", "sky_cover", "sky_cover_2", "sky_cover_3", "sky_cover_4"):
        cover = str(raw.get(key) or "").upper().strip()
        if cover:
            covers.append(cover)
    severity = {"CLR": 0, "SKC": 0, "CAVOK": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "VV": 4}
    ranked = [cover for cover in covers if cover in severity]
    return max(ranked, key=lambda cover: severity[cover]) if ranked else ""


@dataclass(frozen=True)
class FetchSettings:
    timeout_sec: float = 3.0
    proxy_candidates: tuple[str | None, ...] = (None,)
    user_agent: str = "pm-agent-weather-data-feed/1.0"
    http_client: httpx.Client | None = None


class ObservationFetchError(RuntimeError):
    pass


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def arith_round(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def c_to_f(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


def f_to_c(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def parse_metar_rmk_temp_c(raw_metar: str) -> float | None:
    match = METAR_RMK_T_RE.search(str(raw_metar or ""))
    if not match:
        return None
    sign = -1 if match.group(1) == "1" else 1
    return sign * (int(match.group(2)) / 10.0)


def metar_temp_metadata(raw_metar: str) -> dict[str, Any]:
    main_temp_c = parse_metar_temp_c(raw_metar)
    rmk_temp_c = parse_metar_rmk_temp_c(raw_metar)
    out: dict[str, Any] = {
        "main_temp_c": main_temp_c,
        "rmk_temp_c": rmk_temp_c,
    }
    if main_temp_c is not None:
        out["main_round_f"] = arith_round(c_to_f(main_temp_c))
    if rmk_temp_c is not None:
        out["rmk_round_f"] = arith_round(c_to_f(rmk_temp_c))
    return out


def weather_com_api_key() -> str:
    return os.environ.get("TIMING_MONITOR_WEATHER_COM_API_KEY", "").strip() or os.environ.get("WEATHER_COM_API_KEY", "").strip() or WEATHER_COM_API_KEY


def weather_com_country_for_station(station: str) -> str:
    overrides = os.environ.get("TIMING_MONITOR_WEATHER_COM_COUNTRY_OVERRIDES", "")
    station_key = str(station or "").upper()
    for item in overrides.split(","):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        if key.strip().upper() == station_key:
            return value.strip().upper()
    country = os.environ.get("TIMING_MONITOR_WEATHER_COM_COUNTRY", "").strip().upper()
    if country:
        return country
    if station_key.startswith(("K", "P")):
        return "US"
    if station_key.startswith("Z"):
        return "CN"
    if station_key.startswith("RJ"):
        return "JP"
    if station_key.startswith("RK"):
        return "KR"
    if station_key.startswith("VHH"):
        return "HK"
    if station_key.startswith("WSS"):
        return "SG"
    if station_key.startswith("RP"):
        return "PH"
    if station_key.startswith("SA"):
        return "AR"
    if station_key.startswith("SB"):
        return "BR"
    if station_key.startswith("MM"):
        return "MX"
    if station_key.startswith("C"):
        return "CA"
    return "US"


def weather_com_headers(station: str, *, history: bool) -> dict[str, str]:
    if history:
        referer = f"https://www.wunderground.com/history/daily/{station}"
    else:
        referer = f"https://www.wunderground.com/weather/{station}"
    return {
        "User-Agent": "Mozilla/5.0 pm-agent-weather-latency-research",
        "Origin": "https://www.wunderground.com",
        "Referer": referer,
    }


def source_station_id(value: str) -> str:
    raw = str(value or "").strip().upper()
    if ICAO_RE.match(raw):
        return raw
    match = WRH_SITE_RE.search(str(value or ""))
    return match.group(1).upper() if match else ""


def source_age_sec(report_ts_utc: str | None, now_utc: datetime) -> float | None:
    dt = parse_dt(report_ts_utc)
    if not dt:
        return None
    return round((now_utc - dt).total_seconds(), 3)


def infer_cadence_min(records: list[ObservationRecord]) -> float | None:
    dts = sorted(parse_dt(row.obs_ts_utc) for row in records)
    dts = [dt for dt in dts if dt is not None]
    if len(dts) < 2:
        return None
    gaps = [
        (right - left).total_seconds() / 60.0
        for left, right in zip(dts, dts[1:])
        if (right - left).total_seconds() > 0
    ]
    return round(median(gaps), 1) if gaps else None


def one_hour_observation_changes(records: list[ObservationRecord]) -> dict[str, float | None]:
    """Return PIT changes from the closest earlier observation 30-90m back."""

    empty = {
        "cloud_cover_change_1h_code": None,
        "ceiling_change_1h_ft": None,
        "wind_speed_change_1h_kt": None,
        "wind_dir_1h_prior_deg": None,
        "wind_dir_change_1h_deg": None,
        "dewpoint_change_1h_f": None,
        "relative_humidity_change_1h_pct": None,
    }
    ordered_records = sorted(
        (record for record in records if parse_dt(record.obs_ts_utc) is not None),
        key=lambda record: parse_dt(record.obs_ts_utc) or datetime.min.replace(tzinfo=timezone.utc),
    )
    if len(ordered_records) < 2:
        return empty
    latest = ordered_records[-1]
    latest_dt = parse_dt(latest.obs_ts_utc)
    if latest_dt is None:
        return empty
    candidates: list[tuple[float, ObservationRecord]] = []
    for record in ordered_records[:-1]:
        dt = parse_dt(record.obs_ts_utc)
        if dt is None:
            continue
        gap = (latest_dt - dt).total_seconds() / 60.0
        if 30 <= gap <= 90:
            candidates.append((abs(gap - 60.0), record))
    if not candidates:
        return empty
    prior = min(candidates, key=lambda item: item[0])[1]
    severity = {"CLR": 0, "SKC": 0, "CAVOK": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "VV": 4}
    sky_now = severity.get(str(latest.sky_code or "").upper())
    sky_prior = severity.get(str(prior.sky_code or "").upper())
    ceiling_now = _float_or_none(latest.metadata.get("ceiling_ft_agl"))
    ceiling_prior = _float_or_none(prior.metadata.get("ceiling_ft_agl"))
    wind_dir_now = _float_or_none(latest.metadata.get("wind_dir_deg"))
    wind_dir_prior = _float_or_none(prior.metadata.get("wind_dir_deg"))
    wind_dir_change = None
    if wind_dir_now is not None and wind_dir_prior is not None:
        wind_dir_change = (wind_dir_now - wind_dir_prior + 180.0) % 360.0 - 180.0
    dewpoint_change = None
    if latest.dewpoint_c is not None and prior.dewpoint_c is not None:
        dewpoint_change = (float(latest.dewpoint_c) - float(prior.dewpoint_c)) * 9.0 / 5.0
    humidity_change = None
    if latest.relh is not None and prior.relh is not None:
        humidity_change = float(latest.relh) - float(prior.relh)
    return {
        "cloud_cover_change_1h_code": sky_now - sky_prior if sky_now is not None and sky_prior is not None else None,
        "ceiling_change_1h_ft": ceiling_now - ceiling_prior if ceiling_now is not None and ceiling_prior is not None else None,
        "wind_speed_change_1h_kt": latest.wind_kt - prior.wind_kt if latest.wind_kt is not None and prior.wind_kt is not None else None,
        "wind_dir_1h_prior_deg": wind_dir_prior,
        "wind_dir_change_1h_deg": wind_dir_change,
        "dewpoint_change_1h_f": dewpoint_change,
        "relative_humidity_change_1h_pct": humidity_change,
    }


def observation_path_features(
    records: list[ObservationRecord],
    *,
    as_of_utc: datetime | str | None = None,
) -> dict[str, Any]:
    """Return continuous PIT path clocks from one station's visible history.

    Equal observations at the running maximum deliberately do not reset the
    strict-high clock.  This separates a mature equal-high plateau from a new
    high without changing the legacy last-equal-high fields.
    """

    as_of = parse_dt(as_of_utc) if isinstance(as_of_utc, str) else as_of_utc
    if as_of is not None:
        as_of = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)

    ordered: list[tuple[datetime, ObservationRecord]] = []
    for record in records:
        dt = parse_dt(record.obs_ts_utc)
        ingest_dt = parse_dt(record.ingest_ts_utc)
        if dt is None:
            continue
        if as_of is not None and (dt > as_of or (ingest_dt is not None and ingest_dt > as_of)):
            continue
        ordered.append((dt, record))
    ordered.sort(key=lambda item: item[0])
    if not ordered:
        return {
            "observation_history_span_minutes": None,
            "first_running_max_obs_utc": "",
            "last_running_max_obs_utc": "",
            "minutes_since_first_running_max": None,
            "minutes_since_last_running_max": None,
            "minutes_since_last_strict_new_high": None,
            "same_running_max_obs_count": 0,
            "running_max_clock_left_censored": None,
            "clear_sky_regime_minutes": None,
            "clear_sky_regime_temp_change_f": None,
            "clear_sky_regime_left_censored": None,
            "precip_free_regime_minutes": None,
            "precip_free_regime_temp_change_f": None,
            "precip_free_regime_left_censored": None,
            "first_precip_obs_utc": "",
            "last_precip_obs_utc": "",
            "precip_obs_count": 0,
            "first_thunderstorm_obs_utc": "",
            "last_thunderstorm_obs_utc": "",
            "thunderstorm_obs_count": 0,
            "minutes_since_last_precip_obs": None,
        }

    if as_of is None:
        as_of = ordered[-1][0]

    temperature_rows = [
        (dt, record, _float_or_none(record.temp_c))
        for dt, record in ordered
        if _float_or_none(record.temp_c) is not None
    ]
    running_max = max((temp for _dt, _record, temp in temperature_rows), default=None)
    running_hits = [
        (dt, record)
        for dt, record, temp in temperature_rows
        if running_max is not None and temp is not None and abs(temp - running_max) < 1e-9
    ]

    strict_high_dt = None
    high_so_far = -math.inf
    for dt, _record, temp in temperature_rows:
        if temp is not None and temp > high_so_far + 1e-9:
            high_so_far = temp
            strict_high_dt = dt

    def minutes_since(dt: datetime | None) -> float | None:
        return None if dt is None else round((as_of - dt).total_seconds() / 60.0, 3)

    clear_codes = {"CLR", "SKC", "NSC", "CAVOK", "FEW"}

    def clear_sky(record: ObservationRecord) -> bool:
        return str(record.sky_code or "").upper().strip() in clear_codes

    def observed_weather(record: ObservationRecord) -> dict[str, Any]:
        return metar_physical_features(
            record.raw_text,
            record.metadata.get("present_weather")
            or record.metadata.get("present_weather_codes")
            or record.metadata.get("wx_string"),
        )

    def precip_free(record: ObservationRecord) -> bool:
        physical = observed_weather(record)
        return not bool(physical["precip_observed"])

    def suffix_regime(predicate: Any) -> tuple[float | None, float | None, bool | None]:
        if not predicate(ordered[-1][1]):
            return None, None, False
        start_index = len(ordered) - 1
        while start_index > 0 and predicate(ordered[start_index - 1][1]):
            start_index -= 1
        start_dt, start_record = ordered[start_index]
        latest_record = ordered[-1][1]
        start_temp = _float_or_none(start_record.temp_c)
        latest_temp = _float_or_none(latest_record.temp_c)
        temp_change_f = (
            None
            if start_temp is None or latest_temp is None
            else round((latest_temp - start_temp) * 9.0 / 5.0, 3)
        )
        return minutes_since(start_dt), temp_change_f, start_index == 0

    clear_minutes, clear_temp_change, clear_censored = suffix_regime(clear_sky)
    dry_minutes, dry_temp_change, dry_censored = suffix_regime(precip_free)
    precip_rows = [(dt, record) for dt, record in ordered if not precip_free(record)]
    thunderstorm_rows = [
        (dt, record)
        for dt, record in ordered
        if bool(observed_weather(record)["thunderstorm_observed"])
    ]
    first_precip_dt = precip_rows[0][0] if precip_rows else None
    last_precip_dt = precip_rows[-1][0] if precip_rows else None
    first_thunderstorm_dt = thunderstorm_rows[0][0] if thunderstorm_rows else None
    last_thunderstorm_dt = thunderstorm_rows[-1][0] if thunderstorm_rows else None
    first_hit_dt = running_hits[0][0] if running_hits else None
    last_hit_dt = running_hits[-1][0] if running_hits else None
    first_hit_index = next(
        (index for index, (dt, _record) in enumerate(ordered) if first_hit_dt is not None and dt == first_hit_dt),
        None,
    )
    return {
        "observation_history_span_minutes": round((ordered[-1][0] - ordered[0][0]).total_seconds() / 60.0, 3),
        "first_running_max_obs_utc": first_hit_dt.isoformat() if first_hit_dt else "",
        "last_running_max_obs_utc": last_hit_dt.isoformat() if last_hit_dt else "",
        "minutes_since_first_running_max": minutes_since(first_hit_dt),
        "minutes_since_last_running_max": minutes_since(last_hit_dt),
        "minutes_since_last_strict_new_high": minutes_since(strict_high_dt),
        "same_running_max_obs_count": len(running_hits),
        "running_max_clock_left_censored": first_hit_index == 0 if first_hit_index is not None else None,
        "clear_sky_regime_minutes": clear_minutes,
        "clear_sky_regime_temp_change_f": clear_temp_change,
        "clear_sky_regime_left_censored": clear_censored,
        "precip_free_regime_minutes": dry_minutes,
        "precip_free_regime_temp_change_f": dry_temp_change,
        "precip_free_regime_left_censored": dry_censored,
        "first_precip_obs_utc": first_precip_dt.isoformat() if first_precip_dt else "",
        "last_precip_obs_utc": last_precip_dt.isoformat() if last_precip_dt else "",
        "precip_obs_count": len(precip_rows),
        "first_thunderstorm_obs_utc": first_thunderstorm_dt.isoformat() if first_thunderstorm_dt else "",
        "last_thunderstorm_obs_utc": last_thunderstorm_dt.isoformat() if last_thunderstorm_dt else "",
        "thunderstorm_obs_count": len(thunderstorm_rows),
        "minutes_since_last_precip_obs": minutes_since(last_precip_dt),
    }


def _settings(settings: FetchSettings | None) -> FetchSettings:
    return settings or FetchSettings()


def _http_get(url: str, *, params: Any = None, settings: FetchSettings | None = None, headers: dict[str, str] | None = None) -> httpx.Response:
    cfg = _settings(settings)
    last = None
    merged_headers = {"User-Agent": cfg.user_agent}
    if headers:
        merged_headers.update(headers)
    if cfg.http_client is not None:
        response = cfg.http_client.get(
            url,
            params=params,
            headers=merged_headers,
            timeout=cfg.timeout_sec,
        )
        response.raise_for_status()
        return response
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
            last = f"{proxy or 'direct'}: {type(exc).__name__}: {exc}"
    raise ObservationFetchError(f"fetch failed {url}: {last}")


def _target(request: ObservationSourceRequest) -> tuple[ZoneInfo, Any]:
    return ZoneInfo(request.timezone_name), datetime.fromisoformat(request.target_date).date()


def _record(
    request: ObservationSourceRequest,
    *,
    source_key: str,
    obs_dt: datetime,
    ingest_dt: datetime,
    temp_c: float,
    raw: Any = None,
    dewpoint_c: float | None = None,
    relh: float | None = None,
    wind_kt: float | None = None,
    sky_code: str = "",
    latency_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> ObservationRecord:
    raw_text = ""
    if isinstance(raw, dict):
        raw_text = str(raw.get("rawOb") or raw.get("raw_text") or raw.get("rawMessage") or "")
    elif raw is not None:
        raw_text = str(raw)
    relh_value = relh if relh is not None else relative_humidity_pct(temp_c, dewpoint_c)
    structured = raw if isinstance(raw, dict) else {}
    physical = metar_physical_features(
        raw_text,
        structured.get("present_weather")
        or structured.get("wxString")
        or structured.get("wx_phrase")
        or structured.get("wxPhraseLong"),
    )
    structured_wind_dir = _float_or_none(
        structured.get("wdir")
        or structured.get("wind_dir_deg")
        or structured.get("windDirection")
        or structured.get("winddir")
    )
    return ObservationRecord(
        source_key=source_key,
        city=request.city,
        target_date=request.target_date,
        station_or_feed=request.station_or_feed,
        obs_ts_utc=obs_dt.astimezone(timezone.utc).isoformat(),
        ingest_ts_utc=ingest_dt.astimezone(timezone.utc).isoformat(),
        temp_c=float(temp_c),
        dewpoint_c=dewpoint_c,
        relh=relh_value,
        wind_kt=wind_kt,
        sky_code=sky_code,
        raw_text=raw_text,
        source_latency_ms=latency_ms,
        metadata={
            **physical,
            "wind_dir_deg": structured_wind_dir if structured_wind_dir is not None else physical.get("metar_wind_dir_deg"),
            **(metadata or {}),
        },
    )


def _result(
    request: ObservationSourceRequest,
    *,
    source_key: str,
    status: str,
    records: list[ObservationRecord],
    fetch_start: datetime,
    fetch_end: datetime,
    error: str = "",
    metadata: dict[str, Any] | None = None,
) -> ObservationSourceResult:
    return ObservationSourceResult(
        source_key=source_key,
        status=status,
        records=tuple(sorted(records, key=lambda row: row.obs_ts_utc)),
        fetched_at_utc=fetch_end.isoformat(),
        latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3),
        error=error,
        metadata={
            "station": request.station_or_feed,
            "source_fetch_start_utc": fetch_start.isoformat(),
            "source_fetch_end_utc": fetch_end.isoformat(),
            **(metadata or {}),
        },
    )


def fetch_aviationweather_metar(request: ObservationSourceRequest, settings: FetchSettings | None = None, *, hours: float = 30.0) -> ObservationSourceResult:
    source_key = "aviationweather_metar"
    tz, local_date = _target(request)
    fetch_start = datetime.now(timezone.utc)
    data = _http_get(METAR_API, params={"ids": request.station_or_feed, "format": "json", "hours": str(hours)}, settings=settings).json()
    fetch_end = datetime.now(timezone.utc)
    parsed = parse_aviationweather_records(data, tz, local_date) if isinstance(data, list) else []
    records = [
        _record(
            request,
            source_key=source_key,
            obs_dt=dt,
            ingest_dt=fetch_end,
            temp_c=temp,
            raw=raw,
            dewpoint_c=_float_or_none(raw.get("dewp")) if isinstance(raw, dict) else None,
            wind_kt=_float_or_none(raw.get("wspd")) if isinstance(raw, dict) else None,
            sky_code=aviationweather_sky_code(raw),
            latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3),
        )
        for dt, temp, raw in parsed
    ]
    return _result(request, source_key=source_key, status="ok" if records else "empty", records=records, fetch_start=fetch_start, fetch_end=fetch_end, metadata={"raw_payload_hash": stable_hash(data)})


def fetch_aviationweather_cache_csv(request: ObservationSourceRequest, settings: FetchSettings | None = None) -> ObservationSourceResult:
    global _AWC_CACHE_TEXT
    source_key = "aviationweather_cache_csv"
    tz, local_date = _target(request)
    fetch_start = datetime.now(timezone.utc)
    if _AWC_CACHE_TEXT is None:
        # source-events fans out by city. Download the global cache once per run.
        with _AWC_CACHE_LOCK:
            if _AWC_CACHE_TEXT is None:
                raw_bytes = _http_get(AWC_METARS_CACHE_CSV_GZ, settings=settings).content
                try:
                    _AWC_CACHE_TEXT = gzip.decompress(raw_bytes).decode("utf-8", errors="replace")
                except gzip.BadGzipFile:
                    _AWC_CACHE_TEXT = raw_bytes.decode("utf-8", errors="replace")
    fetch_end = datetime.now(timezone.utc)
    parsed = parse_awc_cache_csv_records(_AWC_CACHE_TEXT, request.station_or_feed, tz, local_date)
    records = [
        _record(
            request,
            source_key=source_key,
            obs_dt=dt,
            ingest_dt=fetch_end,
            temp_c=temp,
            raw=raw,
            dewpoint_c=_float_or_none(raw.get("dewpoint_c") or raw.get("dewp")),
            wind_kt=_float_or_none(raw.get("wind_speed_kt") or raw.get("wspd")),
            sky_code=aviationweather_sky_code(raw),
            latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3),
        )
        for dt, temp, raw in parsed
    ]
    return _result(request, source_key=source_key, status="ok" if records else "empty", records=records, fetch_start=fetch_start, fetch_end=fetch_end, metadata={"raw_payload_hash": stable_hash(_AWC_CACHE_TEXT)})


def fetch_iem_asos(request: ObservationSourceRequest, settings: FetchSettings | None = None) -> ObservationSourceResult:
    source_key = "iem_asos"
    tz, local_date = _target(request)
    local_start = datetime.combine(local_date, datetime.min.time(), tzinfo=tz)
    start_utc = local_start.astimezone(timezone.utc) - timedelta(hours=2)
    end_utc = datetime.now(timezone.utc) + timedelta(hours=1)
    params = build_iem_asos_params(request.station_or_feed, start_utc, end_utc, columns=("tmpc", "dwpc", "relh", "sknt"))
    fetch_start = datetime.now(timezone.utc)
    text = _http_get("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py", params=params, settings=settings).text
    fetch_end = datetime.now(timezone.utc)
    parsed = parse_iem_asos_records(text, tz, local_date)
    records = [
        _record(
            request,
            source_key=source_key,
            obs_dt=dt,
            ingest_dt=fetch_end,
            temp_c=temp,
            raw=raw,
            dewpoint_c=_float_or_none(raw.get("dwpc")),
            relh=_float_or_none(raw.get("relh")),
            wind_kt=_float_or_none(raw.get("sknt")),
            latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3),
        )
        for dt, temp, raw in parsed
    ]
    return _result(request, source_key=source_key, status="ok" if records else "empty", records=records, fetch_start=fetch_start, fetch_end=fetch_end, metadata={"raw_payload_hash": stable_hash(text)})


def _csv_rows(text: str) -> list[dict[str, str]]:
    rows = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(rows))))


def _iem_asos_raw_records(text: str, tz: ZoneInfo, local_date: Any, *, family: str) -> list[tuple[datetime, float, dict[str, str], dict[str, Any]]]:
    records: list[tuple[datetime, float, dict[str, str], dict[str, Any]]] = []
    for row in _csv_rows(text):
        raw_ts = row.get("valid")
        raw_metar = row.get("metar") or ""
        if not raw_ts or not raw_metar:
            continue
        source_family = "madishf" if "MADISHF" in raw_metar.upper() else "routine"
        if family == "madishf" and source_family != "madishf":
            continue
        if family == "routine" and source_family == "madishf":
            continue
        try:
            dt = datetime.fromisoformat(str(raw_ts).replace(" ", "T")).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt.astimezone(tz).date().isoformat() != str(local_date):
            continue
        metadata = metar_temp_metadata(raw_metar)
        tmpf = row.get("tmpf")
        temp_f = None
        if tmpf not in {None, "", "M"}:
            try:
                temp_f = float(tmpf)
            except ValueError:
                temp_f = None
        if temp_f is not None:
            temp_c = f_to_c(temp_f)
        elif metadata.get("rmk_temp_c") is not None:
            temp_c = float(metadata["rmk_temp_c"])
            temp_f = c_to_f(temp_c)
        elif metadata.get("main_temp_c") is not None:
            temp_c = float(metadata["main_temp_c"])
            temp_f = c_to_f(temp_c)
        else:
            continue
        metadata.update(
            {
                "source_family": source_family,
                "temp_f": temp_f,
                "temp_round_f": arith_round(temp_f),
            }
        )
        records.append((dt, temp_c, row, metadata))
    return sorted(records, key=lambda item: item[0])


def fetch_iem_asos_latest_family(
    request: ObservationSourceRequest,
    settings: FetchSettings | None = None,
    *,
    source_key: str,
    family: str,
) -> ObservationSourceResult:
    tz, local_date = _target(request)
    local_start = datetime.combine(local_date, datetime.min.time(), tzinfo=tz)
    start_utc = local_start.astimezone(timezone.utc) - timedelta(hours=2)
    end_utc = datetime.now(timezone.utc) + timedelta(hours=1)
    params = build_iem_asos_params(request.station_or_feed, start_utc, end_utc, columns=("tmpf", "metar"), report_types=("1", "2"))
    fetch_start = datetime.now(timezone.utc)
    cache_key = (request.station_or_feed, start_utc.strftime("%Y%m%d%H"), end_utc.strftime("%Y%m%d%H"))
    with _IEM_RAW_TEXT_LOCK:
        text = _IEM_RAW_TEXT_CACHE.get(cache_key)
        if text is None:
            text = _http_get(IEM_ASOS_API, params=params, settings=settings).text
            _IEM_RAW_TEXT_CACHE[cache_key] = text
    fetch_end = datetime.now(timezone.utc)
    parsed = _iem_asos_raw_records(text, tz, local_date, family=family)
    records = [
        _record(
            request,
            source_key=source_key,
            obs_dt=dt,
            ingest_dt=fetch_end,
            temp_c=temp_c,
            raw=row.get("metar") or "",
            latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3),
            metadata=metadata,
        )
        for dt, temp_c, row, metadata in parsed
    ]
    return _result(
        request,
        source_key=source_key,
        status="ok" if records else "empty",
        records=records,
        fetch_start=fetch_start,
        fetch_end=fetch_end,
        metadata={"raw_payload_hash": stable_hash(text), "iem_family": family},
    )


def fetch_noaa_tgftp_station_txt(request: ObservationSourceRequest, settings: FetchSettings | None = None) -> ObservationSourceResult:
    source_key = "noaa_tgftp_station_txt"
    fetch_start = datetime.now(timezone.utc)
    text = _http_get(NOAA_TGFTP_STATION_TXT.format(icao=request.station_or_feed), settings=settings).text
    fetch_end = datetime.now(timezone.utc)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    raw_metar = lines[1] if len(lines) > 1 else ""
    header_dt = parse_tgftp_header_time(text)
    report_dt = parse_metar_report_time(raw_metar, header_dt)
    temp = parse_metar_temp_c(raw_metar) if raw_metar else None
    records = []
    if report_dt and temp is not None:
        records.append(_record(request, source_key=source_key, obs_dt=report_dt, ingest_dt=fetch_end, temp_c=temp, raw=raw_metar, latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3)))
    return _result(request, source_key=source_key, status="ok" if records else "missing_metar", records=records, fetch_start=fetch_start, fetch_end=fetch_end, metadata={"source_file_ts_utc": header_dt.isoformat() if header_dt else "", "raw_payload_hash": stable_hash(text)})


def fetch_checkwx_html(request: ObservationSourceRequest, settings: FetchSettings | None = None) -> ObservationSourceResult:
    source_key = "checkwx_html"
    fetch_start = datetime.now(timezone.utc)
    text = _http_get(CHECKWX_URL.format(icao=request.station_or_feed), settings=settings).text
    fetch_end = datetime.now(timezone.utc)
    raw_match = re.search(rf"{request.station_or_feed}\s+\d{{6}}Z[^<]+", text)
    raw_metar = raw_match.group(0).strip() if raw_match else ""
    observed_match = CHECKWX_OBS_RE.search(text)
    report_dt = parse_dt(observed_match.group(1)) if observed_match else parse_metar_report_time(raw_metar, fetch_end)
    temp = parse_metar_temp_c(raw_metar) if raw_metar else None
    records = []
    if report_dt and temp is not None:
        records.append(_record(request, source_key=source_key, obs_dt=report_dt, ingest_dt=fetch_end, temp_c=temp, raw=raw_metar, latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3)))
    return _result(request, source_key=source_key, status="ok" if records else "missing_metar", records=records, fetch_start=fetch_start, fetch_end=fetch_end, metadata={"raw_payload_hash": stable_hash(text)})


def fetch_weather_gov_latest(request: ObservationSourceRequest, settings: FetchSettings | None = None) -> ObservationSourceResult:
    source_key = "weather_gov_latest"
    fetch_start = datetime.now(timezone.utc)
    payload = _http_get(WEATHER_GOV_LATEST_OBS.format(icao=request.station_or_feed), settings=settings, headers={"User-Agent": "pm-agent-weather-data-feed/1.0"}).json()
    fetch_end = datetime.now(timezone.utc)
    props = payload.get("properties") or {}
    temp_payload = props.get("temperature") or {}
    report_dt = parse_dt(str(props.get("timestamp") or ""))
    temp = temp_payload.get("value")
    records = []
    if report_dt and temp is not None:
        records.append(_record(request, source_key=source_key, obs_dt=report_dt, ingest_dt=fetch_end, temp_c=float(temp), raw=props.get("rawMessage") or "", latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3)))
    return _result(request, source_key=source_key, status="ok" if records else "missing_observation", records=records, fetch_start=fetch_start, fetch_end=fetch_end, metadata={"raw_payload_hash": stable_hash(payload)})


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


def synoptic_token(settings: FetchSettings | None = None) -> str:
    global _SYNOPTIC_TOKEN_CACHE
    if _SYNOPTIC_TOKEN_CACHE:
        return _SYNOPTIC_TOKEN_CACHE
    explicit = (
        os.environ.get("TIMING_MONITOR_SYNOP_TOKEN", "").strip()
        or os.environ.get("METAR_CROSS_SYNOP_TOKEN", "").strip()
        or os.environ.get("SYNOPTIC_TOKEN", "").strip()
    )
    if explicit:
        _SYNOPTIC_TOKEN_CACHE = explicit
        return _SYNOPTIC_TOKEN_CACHE
    text = _http_get(
        WRH_API_KEY_JS,
        settings=settings,
        headers={"User-Agent": "Mozilla/5.0 pm-agent-weather-data-feed", "Referer": "https://www.weather.gov/wrh/timeseries"},
    ).text
    match = SYNOPTIC_TOKEN_RE.search(text)
    if not match:
        raise ObservationFetchError("token not found in weather.gov apiKey.js")
    _SYNOPTIC_TOKEN_CACHE = match.group(1)
    return _SYNOPTIC_TOKEN_CACHE


def fetch_synopticdata_timeseries(request: ObservationSourceRequest, settings: FetchSettings | None = None, *, minutes: int = 240) -> ObservationSourceResult:
    source_key = "synopticdata_timeseries"
    tz, local_date = _target(request)
    params = {
        "STID": request.station_or_feed,
        "recent": str(minutes),
        "vars": "air_temp",
        "units": "temp|C",
        "obtimezone": "utc",
        "token": synoptic_token(settings),
        "output": "json",
    }
    fetch_start = datetime.now(timezone.utc)
    payload = _http_get(
        SYNOPTIC_TIMESERIES_API,
        params=params,
        settings=settings,
        headers={
            "User-Agent": "Mozilla/5.0 pm-agent-weather-data-feed",
            "Referer": f"https://www.weather.gov/wrh/timeseries?site={request.station_or_feed}",
            "Origin": "https://www.weather.gov",
        },
    ).json()
    fetch_end = datetime.now(timezone.utc)
    times, temps = synoptic_obs_lists(payload)
    records = []
    for ts_raw, temp_raw in zip(times, temps):
        if ts_raw in (None, "") or temp_raw in (None, ""):
            continue
        dt = parse_dt(str(ts_raw))
        if dt is None:
            continue
        try:
            temp_c = float(temp_raw)
        except (TypeError, ValueError):
            continue
        if dt.astimezone(tz).date() == local_date:
            records.append(_record(request, source_key=source_key, obs_dt=dt, ingest_dt=fetch_end, temp_c=temp_c, raw={"date_time": ts_raw, "air_temp": temp_raw}, latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3)))
    station_row = (payload.get("STATION") or [{}])[0]
    return _result(request, source_key=source_key, status="ok" if records else "empty", records=records, fetch_start=fetch_start, fetch_end=fetch_end, metadata={"synoptic_station_id": station_row.get("ID"), "synoptic_station_name": station_row.get("NAME"), "raw_payload_hash": stable_hash(payload)})


def fetch_weather_com_current(request: ObservationSourceRequest, settings: FetchSettings | None = None) -> ObservationSourceResult:
    source_key = "weather_com_current"
    params = {
        "apiKey": weather_com_api_key(),
        "language": "en-US",
        "units": "e",
        "format": "json",
        "icaoCode": request.station_or_feed,
    }
    fetch_start = datetime.now(timezone.utc)
    payload = _http_get(WEATHER_COM_CURRENT_OBS, params=params, settings=settings, headers=weather_com_headers(request.station_or_feed, history=False)).json()
    fetch_end = datetime.now(timezone.utc)
    ts_raw = payload.get("validTimeUtc")
    temp_f = payload.get("temperature")
    records = []
    if ts_raw is not None and temp_f is not None:
        try:
            report_dt = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
            temp_f_float = float(temp_f)
            metadata = {
                "temp_f": temp_f_float,
                "temp_round_f": arith_round(temp_f_float),
                "max_temp_f_24h": payload.get("temperatureMax24Hour"),
                "max_temp_f_since_7am": payload.get("temperatureMaxSince7Am"),
                "obs_name": payload.get("obsName"),
                "icao_code": payload.get("icaoCode"),
                "expire_time_utc": payload.get("expireTimeUtc"),
            }
            records.append(
                _record(
                    request,
                    source_key=source_key,
                    obs_dt=report_dt,
                    ingest_dt=fetch_end,
                    temp_c=f_to_c(temp_f_float),
                    raw=payload,
                    latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3),
                    metadata=metadata,
                )
            )
        except (TypeError, ValueError, OSError):
            records = []
    return _result(
        request,
        source_key=source_key,
        status="ok" if records else "missing_observation",
        records=records,
        fetch_start=fetch_start,
        fetch_end=fetch_end,
        metadata={"raw_payload_hash": stable_hash(payload)},
    )


def fetch_weather_com_history_hourly(request: ObservationSourceRequest, settings: FetchSettings | None = None) -> ObservationSourceResult:
    source_key = "weather_com_history_hourly"
    tz, local_date = _target(request)
    requested_native_unit = str(request.metadata.get("native_unit") or "F").strip().upper()
    if requested_native_unit not in {"C", "F"}:
        raise ValueError(f"unsupported weather.com native_unit={requested_native_unit!r}")
    country = str(request.metadata.get("weather_com_country") or "").strip().upper()
    if not country:
        country = weather_com_country_for_station(request.station_or_feed)
    location = f"{request.station_or_feed}:9:{country}"
    date_key = local_date.strftime("%Y%m%d")
    params = {
        "apiKey": weather_com_api_key(),
        "units": "m" if requested_native_unit == "C" else "e",
        "startDate": date_key,
        "endDate": date_key,
    }
    fetch_start = datetime.now(timezone.utc)
    payload = _http_get(
        WEATHER_COM_HISTORICAL_OBS.format(location=location),
        params=params,
        settings=settings,
        headers=weather_com_headers(request.station_or_feed, history=True),
    ).json()
    fetch_end = datetime.now(timezone.utc)
    records = []
    max_native_temp: float | None = None
    for raw in payload.get("observations") or []:
        ts_raw = raw.get("valid_time_gmt")
        native_temp = raw.get("temp")
        if ts_raw is None or native_temp is None:
            continue
        try:
            report_dt = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
            native_temp_float = float(native_temp)
        except (TypeError, ValueError, OSError):
            continue
        if report_dt.astimezone(tz).date() != local_date:
            continue
        max_native_temp = (
            native_temp_float
            if max_native_temp is None
            else max(max_native_temp, native_temp_float)
        )
        temp_c = native_temp_float if requested_native_unit == "C" else f_to_c(native_temp_float)
        records.append(
            _record(
                request,
                source_key=source_key,
                obs_dt=report_dt,
                ingest_dt=fetch_end,
                temp_c=temp_c,
                raw=raw,
                latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3),
                metadata={
                    "native_temp": native_temp_float,
                    "native_round": arith_round(native_temp_float),
                    "native_unit": requested_native_unit,
                    "max_native_temp_observed": max_native_temp,
                    "max_native_round_observed": arith_round(max_native_temp),
                    **(
                        {
                            "temp_f": native_temp_float,
                            "temp_round_f": arith_round(native_temp_float),
                            "max_temp_f_observed": max_native_temp,
                            "max_temp_round_f_observed": arith_round(max_native_temp),
                        }
                        if requested_native_unit == "F"
                        else {}
                    ),
                    "obs_name": raw.get("obs_name"),
                    "icao_code": raw.get("icao"),
                    "weather_com_location": location,
                },
            )
        )
    return _result(
        request,
        source_key=source_key,
        status="ok" if records else "empty",
        records=records,
        fetch_start=fetch_start,
        fetch_end=fetch_end,
        metadata={
            "raw_payload_hash": stable_hash(payload),
            "weather_com_location": location,
            "native_unit": requested_native_unit,
            "record_count_raw": len(payload.get("observations") or []),
        },
    )


def fetch_observation_source(request: ObservationSourceRequest, settings: FetchSettings | None = None) -> ObservationSourceResult:
    source_key = normalize_source_name(request.source_key)
    normalized_request = ObservationSourceRequest(
        city=request.city,
        station_or_feed=source_station_id(request.station_or_feed) or request.station_or_feed,
        target_date=request.target_date,
        timezone_name=request.timezone_name,
        source_key=source_key,
        max_age_seconds=request.max_age_seconds,
        metadata=request.metadata,
    )
    if source_key == "aviationweather_metar":
        return fetch_aviationweather_metar(normalized_request, settings)
    if source_key == "aviationweather_cache_csv":
        return fetch_aviationweather_cache_csv(normalized_request, settings)
    if source_key == "iem_asos":
        return fetch_iem_asos(normalized_request, settings)
    if source_key == "iem_asos_latest_raw":
        return fetch_iem_asos_latest_family(normalized_request, settings, source_key=source_key, family="any")
    if source_key == "iem_asos_routine_latest":
        return fetch_iem_asos_latest_family(normalized_request, settings, source_key=source_key, family="routine")
    if source_key == "iem_asos_madishf_latest":
        return fetch_iem_asos_latest_family(normalized_request, settings, source_key=source_key, family="madishf")
    if source_key == "noaa_tgftp_station_txt":
        return fetch_noaa_tgftp_station_txt(normalized_request, settings)
    if source_key == "checkwx_html":
        return fetch_checkwx_html(normalized_request, settings)
    if source_key == "weather_gov_latest":
        return fetch_weather_gov_latest(normalized_request, settings)
    if source_key == "synopticdata_timeseries":
        minutes = int(normalized_request.metadata.get("recent_minutes") or 240)
        return fetch_synopticdata_timeseries(normalized_request, settings, minutes=minutes)
    if source_key == "weather_com_current":
        return fetch_weather_com_current(normalized_request, settings)
    if source_key == "weather_com_history_hourly":
        return fetch_weather_com_history_hourly(normalized_request, settings)
    raise ObservationFetchError(f"unknown observation source {source_key!r}")


def snapshot_observation_source(
    cfg: CityConfig,
    source_name: str,
    now_utc: datetime,
    *,
    settings: FetchSettings | None = None,
    recent_minutes: int = 240,
    include_record_rows: bool = False,
) -> dict[str, Any]:
    source_key = normalize_source_name(source_name)
    tz = ZoneInfo(cfg.timezone_name)
    local_date = now_utc.astimezone(tz).date()
    request = ObservationSourceRequest(
        city=cfg.city,
        station_or_feed=cfg.official_icao,
        target_date=local_date.isoformat(),
        timezone_name=cfg.timezone_name,
        source_key=source_key,
        metadata={"recent_minutes": recent_minutes},
    )
    result = fetch_observation_source(request, settings=settings)
    latest = result.records[-1] if result.records else None
    report_ts = latest.obs_ts_utc if latest else ""
    fetch_end = parse_dt(result.fetched_at_utc) or datetime.now(timezone.utc)
    row = {
        "status": result.status,
        "source": result.source_key,
        "station": request.station_or_feed,
        "source_report_ts_utc": report_ts,
        "temp_c": latest.temp_c if latest else None,
        "dewpoint_c": latest.dewpoint_c if latest else None,
        "relative_humidity_pct": latest.relh if latest else None,
        "wind_speed_kt": latest.wind_kt if latest else None,
        "sky_code_now": latest.sky_code if latest else "",
        "raw_metar": latest.raw_text if latest else "",
        "raw_payload_hash": result.metadata.get("raw_payload_hash", ""),
        "ts_utc": fetch_end.isoformat(),
        "local_detect_ts_utc": fetch_end.isoformat(),
        "source_fetch_start_utc": result.metadata.get("source_fetch_start_utc", ""),
        "source_fetch_end_utc": result.metadata.get("source_fetch_end_utc", result.fetched_at_utc),
        "source_fetch_latency_sec": None if result.latency_ms is None else round(result.latency_ms / 1000.0, 3),
        "city": cfg.city,
        "target_date": local_date.isoformat(),
        "unit": cfg.unit,
        "settlement_source_class": cfg.settlement_source_class,
        "settlement_source": cfg.settlement_source,
        "live_observation_source": cfg.live_observation_source,
        "mapping_rule": cfg.mapping_rule,
        "registry_class": cfg.registry_class,
        "source_age_sec": source_age_sec(report_ts, fetch_end),
        "detected_after_report_sec": source_age_sec(report_ts, fetch_end),
        "record_count": len(result.records),
        "estimated_cadence_min": infer_cadence_min(list(result.records)),
    }
    if latest:
        row.update(latest.metadata)
        row.update(one_hour_observation_changes(list(result.records)))
    for key, value in result.metadata.items():
        if key not in row and key not in {"source_fetch_start_utc", "source_fetch_end_utc"}:
            row[key] = value
    row["payload_hash"] = stable_hash(
        {
            "source_report_ts_utc": row.get("source_report_ts_utc"),
            "temp_c": row.get("temp_c"),
            "temp_f": row.get("temp_f"),
            "raw_metar": row.get("raw_metar"),
            "raw_payload_hash": row.get("raw_payload_hash"),
            "source_family": row.get("source_family"),
            "max_temp_f_since_7am": row.get("max_temp_f_since_7am"),
            "max_temp_f_observed": row.get("max_temp_f_observed"),
        }
    )
    if include_record_rows:
        record_rows: list[dict[str, Any]] = []
        for record in result.records:
            record_row = dict(row)
            record_row.update(
                {
                    "source_report_ts_utc": record.obs_ts_utc,
                    "temp_c": record.temp_c,
                    "dewpoint_c": record.dewpoint_c,
                    "relative_humidity_pct": record.relh,
                    "wind_speed_kt": record.wind_kt,
                    "sky_code_now": record.sky_code,
                    "raw_metar": record.raw_text,
                    "source_age_sec": source_age_sec(record.obs_ts_utc, fetch_end),
                    "detected_after_report_sec": source_age_sec(record.obs_ts_utc, fetch_end),
                    **record.metadata,
                }
            )
            record_row["payload_hash"] = stable_hash(
                {
                    "source_report_ts_utc": record.obs_ts_utc,
                    "temp_c": record.temp_c,
                    "raw_metar": record.raw_text,
                    "metadata": record.metadata,
                }
            )
            record_rows.append(record_row)
        row["_record_rows"] = record_rows
    return row


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, "", "M"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
