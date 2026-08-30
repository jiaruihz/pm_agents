"""Airport and official high-frequency observation enrichment sources.

These rows are research/enrichment inputs. They are not settlement truth unless
the city rules explicitly name the same official station as the settlement
source.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from weather_data_feed.observation_sources import FetchSettings, ObservationSourceRequest, fetch_observation_source
from weather_data_feed.observation_sources.fetchers import arith_round, c_to_f, parse_dt as parse_source_dt
from weather_data_feed.runway_sources import RunwayFetchSettings, fetch_amos_runway
from weather_clock_contract import (
    local_wall_time_to_utc,
    parse_utc,
    parse_utc_or_none,
)


SINGAPORE_MSS_TEMP_URL = "https://api.data.gov.sg/v1/environment/air-temperature"
SINGAPORE_MSS_TEMP_V2_URL = "https://api-open.data.gov.sg/v2/real-time/api/air-temperature"
JMA_AMEDAS_BASE = "https://www.jma.go.jp"
HKO_BASE_URL = "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather"
COWIN_BASE_URL = "https://cowin.hku.hk"
MGM_BASE_URL = "https://servis.mgm.gov.tr/web"
IMS_OBSERVATIONS_URL = "https://ims.gov.il/en/hourly_observations_full"
FMI_BASE_URL = "https://opendata.fmi.fi/wfs"
FMI_WEATHER_PARAMETERS = (
    "t2m", "ws_10min", "wg_10min", "wd_10min", "rh", "td", "r_1h",
    "ri_10min", "snow_aws", "p_sea", "vis", "n_man", "wawa",
)
FMI_RADIATION_PARAMETERS = (
    "GLOB_1MIN", "DIFF_1MIN", "LWIN_1MIN", "LWOUT_1MIN", "REFL_1MIN",
    "SUND_1MIN",
)
KNMI_API_BASE = "https://api.dataplatform.knmi.nl/open-data/v1"
KNMI_DATASET = "10-minute-in-situ-meteorological-observations"
KNMI_VERSION = "1.0"
CWA_OBSERVATIONS_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001"
NCM_API_BASE = "https://api-mm.ncm.gov.sa"
AEROWEB_BASE = "https://aviation.meteo.fr"
METEOFRANCE_OBS_URL = "https://public-api.meteofrance.fr/public/DPObs/v2/station/infrahoraire-6m"
KNMI_EDR_BASE = "https://api.dataplatform.knmi.nl/edr/v1/collections/10-minute-in-situ-meteorological-observations"
IMS_API_BASE = "https://api.ims.gov.il/v1/envista"
AEMET_API_BASE = "https://opendata.aemet.es/opendata/api"
DWD_MUNICH_10M_URL = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/now/10minutenwerte_TU_01262_now.zip"
ECCC_SWOB_LATEST_BASE = "https://dd.weather.gc.ca/today/observations/swob-ml/latest"
BOM_AWS_BASE = "https://reg.bom.gov.au/fwo"

US_HFMETAR_CITIES: dict[str, dict[str, Any]] = {
    "New York": {"station": "KLGA", "label": "LaGuardia MADIS HFMETAR", "timezone_name": "America/New_York"},
    "Los Angeles": {"station": "KLAX", "label": "LAX MADIS HFMETAR", "timezone_name": "America/Los_Angeles"},
    "San Francisco": {"station": "KSFO", "label": "SFO MADIS HFMETAR", "timezone_name": "America/Los_Angeles"},
    "Denver": {"station": "KBKF", "label": "Buckley MADIS HFMETAR", "timezone_name": "America/Denver"},
    "Austin": {"station": "KAUS", "label": "Austin Bergstrom MADIS HFMETAR", "timezone_name": "America/Chicago"},
    "Houston": {"station": "KHOU", "label": "Houston Hobby MADIS HFMETAR", "timezone_name": "America/Chicago"},
    "Chicago": {"station": "KORD", "label": "O'Hare MADIS HFMETAR", "timezone_name": "America/Chicago"},
    "Dallas": {"station": "KDAL", "label": "Dallas Love Field MADIS HFMETAR", "timezone_name": "America/Chicago"},
    "Miami": {"station": "KMIA", "label": "Miami Intl MADIS HFMETAR", "timezone_name": "America/New_York"},
    "Atlanta": {"station": "KATL", "label": "Atlanta Hartsfield MADIS HFMETAR", "timezone_name": "America/New_York"},
    "Seattle": {"station": "KSEA", "label": "SeaTac MADIS HFMETAR", "timezone_name": "America/Los_Angeles"},
    "Boston": {"station": "KBOS", "label": "Boston Logan MADIS HFMETAR", "timezone_name": "America/New_York"},
    "Minneapolis": {"station": "KMSP", "label": "Minneapolis-St Paul MADIS HFMETAR", "timezone_name": "America/Chicago"},
    "Phoenix": {"station": "KPHX", "label": "Phoenix Sky Harbor MADIS HFMETAR", "timezone_name": "America/Phoenix"},
}

HIGH_FREQUENCY_CITY_SOURCES: dict[str, dict[str, dict[str, Any]]] = {
    "amos_runway": {
        "Seoul": {
            "station": "RKSI",
            "label": "Incheon AMOS runway air temperature",
            "timezone_name": "Asia/Seoul",
            "primary_runway": "15L",
            "preferred_temperature_runway": "15R/33L",
        },
        "Busan": {"station": "RKPK", "label": "Gimhae AMOS runway air temperature", "timezone_name": "Asia/Seoul"},
    },
    "noaa_madis_hfmetar": US_HFMETAR_CITIES,
    "singapore_mss": {
        "Singapore": {"station": "S24", "label": "Upper Changi Road North 1min MSS", "timezone_name": "Asia/Singapore", "icao": "WSSS"},
    },
    "jma_amedas": {
        "Tokyo": {"station": "44166", "label": "Haneda AMeDAS 10min", "timezone_name": "Asia/Tokyo", "icao": "RJTT"},
    },
    "hko_obs": {
        "Hong Kong": {"station": "HK Observatory", "label": "HK Observatory 1min", "timezone_name": "Asia/Hong_Kong", "icao": "HKO"},
        "Shenzhen": {"station": "Lau Fau Shan", "label": "Lau Fau Shan HKO 1min", "timezone_name": "Asia/Shanghai", "icao": "LFS"},
    },
    "cowin_obs": {
        "Hong Kong": {"station": "6087", "label": "CoWIN 6087 1min", "timezone_name": "Asia/Hong_Kong", "icao": "COWIN6087"},
    },
    "cwa": {
        "Taipei": {"station": "466920", "label": "CWA Taipei Station 10min", "timezone_name": "Asia/Taipei", "icao": "466920"},
    },
    "mgm": {
        "Ankara": {"station": "17128", "label": "Ankara Esenboga MGM", "timezone_name": "Europe/Istanbul", "icao": "LTAC"},
        "Istanbul": {"station": "17058", "label": "Istanbul Airport MGM", "timezone_name": "Europe/Istanbul", "icao": "LTFM"},
    },
    "ims_lod": {
        "Tel Aviv": {"station": "225", "label": "Lod Airport IMS", "timezone_name": "Asia/Jerusalem", "icao": "LLBG"},
    },
    "ncm_jeddah": {
        "Jeddah": {"station": "36", "label": "Jeddah NCM / Mataar Municipality", "timezone_name": "Asia/Riyadh", "icao": "OEJN"},
    },
    "aeroweb": {
        "Paris": {"station": "LFPB", "label": "Paris Le Bourget AEROWEB", "timezone_name": "Europe/Paris", "icao": "LFPB"},
    },
    "fmi": {
        "Helsinki": {"station": "100968", "label": "Helsinki-Vantaa FMI 10min", "timezone_name": "Europe/Helsinki", "icao": "EFHK"},
    },
    "knmi": {
        "Amsterdam": {"station": "0-20000-0-06240", "label": "Schiphol KNMI 10min", "timezone_name": "Europe/Amsterdam", "icao": "EHAM"},
    },
    "meteofrance_6m": {
        "Paris": {"station": "95088001", "label": "Le Bourget Meteo-France 6min", "timezone_name": "Europe/Paris", "icao": "LFPB"},
    },
    "dwd_10m": {
        "Munich": {"station": "01262", "label": "Munich Airport DWD 10min", "timezone_name": "Europe/Berlin", "icao": "EDDM"},
    },
    "aemet_10m": {
        "Madrid": {"station": "3129", "label": "Madrid Barajas AEMET", "timezone_name": "Europe/Madrid", "icao": "LEMD"},
    },
    "ims_1m": {
        "Tel Aviv": {"station": "225", "label": "Lod Airport IMS 1min API", "timezone_name": "Asia/Jerusalem", "icao": "LLBG"},
    },
    "metservice_1m": {
        "Wellington": {"station": "93110", "label": "Wellington Airport MetService 1min", "timezone_name": "Pacific/Auckland", "icao": "NZWN"},
    },
    "eccc_swob": {
        "Toronto": {"station": "CYYZ", "label": "Toronto Pearson ECCC SWOB", "timezone_name": "America/Toronto", "icao": "CYYZ", "report_type": "MAN"},
    },
    "bom_aws": {
        "Sydney": {"station": "94767", "label": "Sydney Airport BoM AWS", "timezone_name": "Australia/Sydney", "icao": "YSSY", "product_id": "IDN60901"},
        "Melbourne": {"station": "94866", "label": "Melbourne Airport BoM AWS", "timezone_name": "Australia/Melbourne", "icao": "YMML", "product_id": "IDV60901"},
        "Brisbane": {"station": "94578", "label": "Brisbane Airport BoM AWS", "timezone_name": "Australia/Brisbane", "icao": "YBBN", "product_id": "IDQ60901"},
        "Perth": {"station": "94151", "label": "Perth Airport BoM AWS", "timezone_name": "Australia/Perth", "icao": "YPPH", "product_id": "IDW60901"},
        "Adelaide": {"station": "94146", "label": "Adelaide Airport BoM AWS", "timezone_name": "Australia/Adelaide", "icao": "YPAD", "product_id": "IDS60901"},
        "Canberra": {"station": "94926", "label": "Canberra Airport BoM AWS", "timezone_name": "Australia/Sydney", "icao": "YSCB", "product_id": "IDN60903"},
        "Hobart": {"station": "94619", "label": "Hobart Airport BoM AWS", "timezone_name": "Australia/Hobart", "icao": "YMHB", "product_id": "IDT60901"},
        "Darwin": {"station": "94120", "label": "Darwin Airport BoM AWS", "timezone_name": "Australia/Darwin", "icao": "YPDN", "product_id": "IDD60901"},
        "Cairns": {"station": "94287", "label": "Cairns Airport BoM AWS", "timezone_name": "Australia/Brisbane", "icao": "YBCS", "product_id": "IDQ60801"},
        "Gold Coast": {"station": "94592", "label": "Gold Coast Airport BoM AWS", "timezone_name": "Australia/Brisbane", "icao": "YBCG", "product_id": "IDQ60901"},
    },
}


@dataclass(frozen=True)
class HighFrequencyFetchSettings:
    timeout_sec: float = 8.0
    proxy_candidates: tuple[str | None, ...] = (None,)
    user_agent: str = "pm-agent-weather-data-feed-high-frequency/1.0"
    verify_tls: bool = True


@dataclass(frozen=True)
class HighFrequencyFetchResult:
    source_key: str
    city: str
    status: str
    fetched_at_utc: str
    latency_ms: float
    records: tuple[dict[str, Any], ...] = ()
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class HighFrequencyFetchError(RuntimeError):
    pass


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def safe_float(value: Any) -> float | None:
    if value in (None, "", "-", "--", "M", "///"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and -100.0 < out < 1000.0 else None


def safe_pressure_hpa(value: Any) -> float | None:
    if value in (None, "", "-", "--", "M", "///"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and 800.0 < out < 1200.0 else None


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    dt = parse_source_dt(str(value))
    return dt.astimezone(timezone.utc) if dt else None


def _target_date(city_meta: dict[str, Any], now_utc: datetime) -> str:
    tz_name = str(city_meta.get("timezone_name") or "UTC")
    try:
        from zoneinfo import ZoneInfo

        return now_utc.astimezone(ZoneInfo(tz_name)).date().isoformat()
    except Exception:
        return now_utc.date().isoformat()


def _http_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    settings: HighFrequencyFetchSettings | None = None,
    headers: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
) -> httpx.Response:
    cfg = settings or HighFrequencyFetchSettings()
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
                auth=auth,
                proxy=proxy,
                timeout=cfg.timeout_sec,
                trust_env=False,
                verify=cfg.verify_tls,
            )
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001
            last_error = f"{proxy or 'direct'}: {type(exc).__name__}: {exc}"
    raise HighFrequencyFetchError(f"fetch failed {url}: {last_error}")


def _base_record(
    *,
    source: str,
    city: str,
    meta: dict[str, Any],
    target_date: str,
    obs_dt: datetime,
    fetched_at: datetime,
    temp_c: float,
    raw: Any,
    source_kind: str,
    source_note: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    temp_f = c_to_f(float(temp_c))
    row = {
        "schema_version": "weather_high_frequency_observation_v1",
        "source": source,
        "source_kind": source_kind,
        "city": city,
        "station": str(meta.get("station") or meta.get("icao") or ""),
        "station_label": str(meta.get("label") or ""),
        "icao": str(meta.get("icao") or ""),
        "target_date": target_date,
        "observation_time_utc": obs_dt.astimezone(timezone.utc).isoformat(),
        "local_detect_ts_utc": fetched_at.astimezone(timezone.utc).isoformat(),
        "fetched_at_utc": fetched_at.astimezone(timezone.utc).isoformat(),
        "temp_c": round(float(temp_c), 3),
        "temp_f": round(temp_f, 3),
        "temp_round_f": arith_round(temp_f),
        "raw_payload_hash": stable_hash(raw),
        "source_note": source_note,
    }
    if extra:
        row.update(extra)
    row["payload_hash"] = stable_hash(
        {
            "source": row.get("source"),
            "city": row.get("city"),
            "station": row.get("station"),
            "observation_time_utc": row.get("observation_time_utc"),
            "temp_c": row.get("temp_c"),
            "raw_payload_hash": row.get("raw_payload_hash"),
        }
    )
    return row


def _result(source: str, city: str, status: str, records: list[dict[str, Any]], start: datetime, end: datetime, *, error: str = "", metadata: dict[str, Any] | None = None) -> HighFrequencyFetchResult:
    return HighFrequencyFetchResult(
        source_key=source,
        city=city,
        status=status,
        fetched_at_utc=end.isoformat(),
        latency_ms=round((end - start).total_seconds() * 1000.0, 3),
        records=tuple(records),
        error=error,
        metadata=metadata or {},
    )


def parse_singapore_mss_payload(payload: dict[str, Any], *, city: str = "Singapore", target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["singapore_mss"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    payload_root = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    stations = {
        str(row.get("id") or ""): row
        for row in (
            (payload_root.get("metadata") or {}).get("stations")
            or payload_root.get("stations")
            or []
        )
    }
    station = stations.get("S24") or {}
    rows: list[dict[str, Any]] = []
    items = payload_root.get("items") or payload_root.get("readings") or []
    for item in items:
        obs_dt = parse_dt(item.get("timestamp"))
        if not obs_dt:
            continue
        readings = item.get("readings") or item.get("data") or []
        for reading in readings:
            if (reading.get("station_id") or reading.get("stationId")) != "S24":
                continue
            temp = safe_float(reading.get("value"))
            if temp is None:
                continue
            meta_row = {**meta, "label": station.get("name") or meta.get("label")}
            rows.append(
                _base_record(
                    source="singapore_mss",
                    city=city,
                    meta=meta_row,
                    target_date=target_date,
                    obs_dt=obs_dt,
                    fetched_at=fetched,
                    temp_c=temp,
                    raw={"item": item, "reading": reading, "station": station},
                    source_kind="official_reference_station",
                    source_note="Singapore MSS S24 air temperature near Changi; not runway sensor",
                    extra={"station_id": "S24"},
                )
            )
    return sorted(rows, key=lambda row: row["observation_time_utc"])


def fetch_singapore_mss(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    start = datetime.now(timezone.utc)
    payload = _http_get(SINGAPORE_MSS_TEMP_URL, settings=settings).json()
    end = datetime.now(timezone.utc)
    records = parse_singapore_mss_payload(payload, city=city, target_date=target_date, fetched_at=end)
    if not records:
        payload_v2 = _http_get(SINGAPORE_MSS_TEMP_V2_URL, settings=settings).json()
        end = datetime.now(timezone.utc)
        records = parse_singapore_mss_payload(payload_v2, city=city, target_date=target_date, fetched_at=end)
        payload = {"v1": payload, "v2": payload_v2}
    return _result("singapore_mss", city, "ok" if records else "empty", records, start, end, metadata={"raw_payload_hash": stable_hash(payload)})


def _jma_obs_time_from_key(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if len(raw) != 14 or not raw.isdigit():
        return None
    try:
        local = datetime.strptime(raw, "%Y%m%d%H%M%S")
        return local_wall_time_to_utc(
            local, timezone_name="Asia/Tokyo", field="jma_amedas_wall_time"
        )
    except ValueError:
        return None


def _jma_pair_value(row: dict[str, Any], key: str) -> float | None:
    pair = row.get(key) or []
    return safe_float(pair[0] if isinstance(pair, list) and pair else None)


def _jma_pair_quality(row: dict[str, Any], key: str) -> int | None:
    pair = row.get(key) or []
    if not isinstance(pair, list) or len(pair) < 2 or pair[1] is None:
        return None
    try:
        return int(pair[1])
    except (TypeError, ValueError):
        return None


def _jma_direction_deg(value: float | None) -> float | None:
    """Convert JMA's 16-point direction code to meteorological degrees."""

    if value is None:
        return None
    code = int(value)
    if code < 1 or code > 16:
        return None
    return float((code % 16) * 22.5)


def parse_jma_amedas_payload(payload: dict[str, Any], *, city: str = "Tokyo", target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["jma_amedas"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for key in sorted(payload):
        row = payload.get(key) or {}
        temp = _jma_pair_value(row, "temp")
        obs_dt = _jma_obs_time_from_key(key)
        if temp is None or obs_dt is None:
            continue
        wind_ms = _jma_pair_value(row, "wind")
        wind_direction_code = _jma_pair_value(row, "windDirection")
        gust_ms = _jma_pair_value(row, "gust")
        gust_direction_code = _jma_pair_value(row, "gustDirection")
        rows.append(
            _base_record(
                source="jma_amedas",
                city=city,
                meta=meta,
                target_date=target_date,
                obs_dt=obs_dt,
                fetched_at=fetched,
                temp_c=temp,
                raw={key: row},
                source_kind="official_airport_station",
                source_note="JMA AMeDAS Haneda airport station; not runway sensor",
                extra={
                    "station_code": meta["station"],
                    "wind_speed_ms": wind_ms,
                    "wind_speed_kt": (
                        round(wind_ms * 1.94384, 3)
                        if wind_ms is not None
                        else None
                    ),
                    "wind_direction_code": (
                        int(wind_direction_code)
                        if wind_direction_code is not None
                        else None
                    ),
                    "wind_dir_deg": _jma_direction_deg(wind_direction_code),
                    "wind_gust_ms": gust_ms,
                    "wind_gust_kt": (
                        round(gust_ms * 1.94384, 3)
                        if gust_ms is not None
                        else None
                    ),
                    "wind_gust_direction_code": (
                        int(gust_direction_code)
                        if gust_direction_code is not None
                        else None
                    ),
                    "wind_gust_dir_deg": _jma_direction_deg(
                        gust_direction_code
                    ),
                    "precipitation_10m_mm": _jma_pair_value(
                        row, "precipitation10m"
                    ),
                    "precipitation_1h_mm": _jma_pair_value(
                        row, "precipitation1h"
                    ),
                    "precipitation_3h_mm": _jma_pair_value(
                        row, "precipitation3h"
                    ),
                    "precipitation_24h_mm": _jma_pair_value(
                        row, "precipitation24h"
                    ),
                    "relative_humidity_pct": _jma_pair_value(row, "humidity"),
                    "jma_temp_quality_code": _jma_pair_quality(row, "temp"),
                    "jma_wind_quality_code": _jma_pair_quality(row, "wind"),
                    "jma_precipitation_10m_quality_code": _jma_pair_quality(
                        row, "precipitation10m"
                    ),
                    "jma_observation_number": row.get("observationNumber"),
                },
            )
        )
    return rows


def fetch_jma_amedas(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["jma_amedas"][city]
    start = datetime.now(timezone.utc)
    latest_text = _http_get(f"{JMA_AMEDAS_BASE}/bosai/amedas/data/latest_time.txt", settings=settings).text.strip()
    latest_dt = parse_utc(latest_text, field="jma_latest_time")
    assert latest_dt is not None
    latest_dt = latest_dt.astimezone(ZoneInfo("Asia/Tokyo"))
    bucket_hour = (latest_dt.hour // 3) * 3
    bucket_key = f"{latest_dt.strftime('%Y%m%d')}_{bucket_hour:02d}"
    payload = _http_get(f"{JMA_AMEDAS_BASE}/bosai/amedas/data/point/{meta['station']}/{bucket_key}.json", settings=settings).json()
    end = datetime.now(timezone.utc)
    records = parse_jma_amedas_payload(payload, city=city, target_date=target_date, fetched_at=end)
    return _result("jma_amedas", city, "ok" if records else "empty", records[-24:], start, end, metadata={"raw_payload_hash": stable_hash(payload)})


def _hko_obs_time_to_utc(value: Any) -> datetime | None:
    raw = str(value or "").strip()[:12]
    if len(raw) != 12 or not raw.isdigit():
        return None
    try:
        local = datetime.strptime(raw, "%Y%m%d%H%M")
        return local_wall_time_to_utc(
            local, timezone_name="Asia/Hong_Kong", field="hko_wall_time"
        )
    except ValueError:
        return None


def parse_hko_csv(text: str, *, city: str, target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["hko_obs"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        if str(raw.get("Automatic Weather Station") or "").strip() != meta["station"]:
            continue
        temp = safe_float(raw.get("Air Temperature(degree Celsius)"))
        obs_dt = _hko_obs_time_to_utc(raw.get("Date time"))
        if temp is None or obs_dt is None:
            continue
        rows.append(
            _base_record(
                source="hko_obs",
                city=city,
                meta=meta,
                target_date=target_date,
                obs_dt=obs_dt,
                fetched_at=fetched,
                temp_c=temp,
                raw=raw,
                source_kind="official_reference_station",
                source_note="HKO 1-minute regional weather station; not runway sensor",
            )
        )
    return rows


def fetch_hko_obs(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    start = datetime.now(timezone.utc)
    text = _http_get(f"{HKO_BASE_URL}/latest_1min_temperature.csv", settings=settings).text
    end = datetime.now(timezone.utc)
    records = parse_hko_csv(text, city=city, target_date=target_date, fetched_at=end)
    return _result("hko_obs", city, "ok" if records else "empty", records, start, end, metadata={"raw_payload_hash": stable_hash(text)})


def _cowin_obs_time_to_utc(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    aware = parse_utc_or_none(raw, field="cowin_observation_time")
    if aware is not None:
        return aware
    try:
        return local_wall_time_to_utc(
            raw,
            timezone_name="Asia/Hong_Kong",
            field="cowin_observation_wall_time",
        )
    except ValueError:
        return None


def parse_cowin_payload(payload: dict[str, Any], *, city: str = "Hong Kong", target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["cowin_obs"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for raw in payload.get("minutely") or []:
        temp = safe_float(raw.get("value1"))
        obs_dt = _cowin_obs_time_to_utc(raw.get("obstime"))
        if temp is None or obs_dt is None:
            continue
        rows.append(
            _base_record(
                source="cowin_obs",
                city=city,
                meta=meta,
                target_date=target_date,
                obs_dt=obs_dt,
                fetched_at=fetched,
                temp_c=temp,
                raw=raw,
                source_kind="community_reference_station",
                source_note="HKU CoWIN 1-minute station; not runway sensor",
            )
        )
    return rows


def fetch_cowin_obs(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    now = datetime.now(ZoneInfo("Asia/Hong_Kong"))
    params = {
        "station_id": "6087",
        "element_id": "temp",
        "start_dt": (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S"),
        "end_dt": now.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    start = datetime.now(timezone.utc)
    try:
        payload = _http_get(f"{COWIN_BASE_URL}/API/data/CoWIN/series?{urlencode(params)}", settings=settings).json()
    except HighFrequencyFetchError as exc:
        cfg = settings or HighFrequencyFetchSettings()
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc).upper() or cfg.verify_tls is False:
            raise
        insecure = HighFrequencyFetchSettings(timeout_sec=cfg.timeout_sec, proxy_candidates=cfg.proxy_candidates, user_agent=cfg.user_agent, verify_tls=False)
        payload = _http_get(f"{COWIN_BASE_URL}/API/data/CoWIN/series?{urlencode(params)}", settings=insecure).json()
    end = datetime.now(timezone.utc)
    records = parse_cowin_payload(payload, city=city, target_date=target_date, fetched_at=end)
    return _result("cowin_obs", city, "ok" if records else "empty", records[-24:], start, end, metadata={"raw_payload_hash": stable_hash(payload)})


def fetch_noaa_madis_hfmetar(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["noaa_madis_hfmetar"][city]
    start = datetime.now(timezone.utc)
    cfg = settings or HighFrequencyFetchSettings()
    result = fetch_observation_source(
        ObservationSourceRequest(
            city=city,
            station_or_feed=meta["station"],
            target_date=target_date,
            timezone_name=meta["timezone_name"],
            source_key="iem_asos_madishf_latest",
        ),
        settings=FetchSettings(timeout_sec=cfg.timeout_sec, proxy_candidates=cfg.proxy_candidates, user_agent=cfg.user_agent),
    )
    end = datetime.now(timezone.utc)
    rows = [
        _base_record(
            source="noaa_madis_hfmetar",
            city=city,
            meta=meta,
            target_date=target_date,
            obs_dt=parse_dt(record.obs_ts_utc) or end,
            fetched_at=end,
            temp_c=record.temp_c,
            raw=record.raw_text or record.metadata,
            source_kind="airport_high_frequency_metar",
            source_note="NOAA MADIS HFMETAR via IEM ASOS family split; airport station, not runway sensor",
            extra={
                "dewpoint_c": record.dewpoint_c,
                "wind_kt": record.wind_kt,
                "raw_metar": record.raw_text,
                **record.metadata,
            },
        )
        for record in result.records
    ]
    return _result("noaa_madis_hfmetar", city, "ok" if rows else result.status, rows[-24:], start, end, error=result.error, metadata=result.metadata)


def _valid_mgm_value(value: Any) -> float | None:
    out = safe_float(value)
    return out if out is not None and out > -9000 else None


def _local_iso_to_utc(value: Any, timezone_name: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    aware = parse_utc_or_none(raw, field="provider_observation_time")
    if aware is not None:
        return aware
    try:
        return local_wall_time_to_utc(
            raw, timezone_name=timezone_name, field="provider_observation_wall_time"
        )
    except ValueError:
        return None


def _zoned_iso_to_utc(value: Any, timezone_name: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    aware = parse_utc_or_none(raw, field="zoned_observation_time")
    if aware is not None:
        return aware
    try:
        return local_wall_time_to_utc(
            raw, timezone_name=timezone_name, field="zoned_observation_wall_time"
        )
    except ValueError:
        return None


def fetch_mgm(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["mgm"][city]
    start = datetime.now(timezone.utc)
    payload = _http_get(
        f"{MGM_BASE_URL}/sondurumlar",
        params={"istno": meta["station"], "_": str(int(start.timestamp() * 1000))},
        settings=settings,
        headers={"Origin": "https://www.mgm.gov.tr"},
    ).json()
    end = datetime.now(timezone.utc)
    raw = payload[0] if isinstance(payload, list) and payload else payload
    records: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        temp = _valid_mgm_value(raw.get("sicaklik"))
        obs_dt = _local_iso_to_utc(raw.get("veriZamani"), "Europe/Istanbul")
        if temp is not None and obs_dt:
            wind_kmh = _valid_mgm_value(raw.get("ruzgarHiz"))
            records.append(
                _base_record(
                    source="mgm",
                    city=city,
                    meta=meta,
                    target_date=target_date,
                    obs_dt=obs_dt,
                    fetched_at=end,
                    temp_c=temp,
                    raw=raw,
                    source_kind="official_airport_station",
                    source_note="Turkish MGM airport/current station observation; not runway sensor",
                    extra={
                        "humidity": _valid_mgm_value(raw.get("nem")),
                        "wind_speed_kt": round(wind_kmh / 1.852, 3) if wind_kmh is not None else None,
                        "wind_dir": _valid_mgm_value(raw.get("ruzgarYon")),
                        "pressure_hpa": _valid_mgm_value(raw.get("aktuelBasinc")),
                        "max_temp_c_so_far": _valid_mgm_value(raw.get("maxSicaklik")),
                    },
                )
            )
    return _result("mgm", city, "ok" if records else "empty", records, start, end, metadata={"raw_payload_hash": stable_hash(payload)})


def fetch_ims_lod(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["ims_lod"][city]
    start = datetime.now(timezone.utc)
    payload = _http_get(IMS_OBSERVATIONS_URL, settings=settings).json()
    end = datetime.now(timezone.utc)
    obs_map = (payload.get("data") or {}).get("hourly_observations_map") or {}
    records: list[dict[str, Any]] = []
    if obs_map:
        latest_time = sorted(obs_map)[-1]
        latest = (obs_map.get(latest_time) or {}).get(meta["station"]) or {}
        temp = safe_float(latest.get("TD"))
        obs_dt = _local_iso_to_utc(latest_time, "Europe/Istanbul")
        if temp is not None and obs_dt:
            wind_ms = safe_float(latest.get("WS"))
            records.append(
                _base_record(
                    source="ims_lod",
                    city=city,
                    meta=meta,
                    target_date=target_date,
                    obs_dt=obs_dt,
                    fetched_at=end,
                    temp_c=temp,
                    raw=latest,
                    source_kind="official_airport_station",
                    source_note="Israel IMS Lod airport 10-minute observation; not runway sensor",
                    extra={
                        "humidity": safe_float(latest.get("RH")),
                        "wind_speed_kt": round(wind_ms * 1.94384, 3) if wind_ms is not None else None,
                        "wind_dir": safe_float(latest.get("WD")),
                    },
                )
            )
    return _result("ims_lod", city, "ok" if records else "empty", records, start, end, metadata={"raw_payload_hash": stable_hash(payload)})


def _parse_fmi_parameter_series(xml_text: str) -> dict[str, dict[datetime, float]]:
    output: dict[str, dict[datetime, float]] = {}
    for block in re.split(r"<om:observedProperty\s", xml_text):
        param_match = re.search(r"param=([A-Za-z0-9_]+)", block[:1200])
        if not param_match:
            continue
        values = output.setdefault(param_match.group(1), {})
        pairs = re.findall(r"<wml2:MeasurementTVP>.*?<wml2:time>(.*?)</wml2:time>\s*<wml2:value>(.*?)</wml2:value>", block, re.DOTALL)
        for time_text, value_text in pairs:
            try:
                candidate = float(value_text)
            except (TypeError, ValueError):
                candidate = math.nan
            value = candidate if math.isfinite(candidate) else None
            timestamp = parse_dt(time_text)
            if value is not None and timestamp is not None:
                values[timestamp] = value
    return output


def parse_fmi_xml(
    xml_text: str,
    *,
    radiation_xml_text: str | None = None,
    city: str = "Helsinki",
    target_date: str = "",
    fetched_at: datetime | None = None,
) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["fmi"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    weather = _parse_fmi_parameter_series(xml_text)
    temperature_series = weather.get("t2m") or {}
    if not temperature_series:
        return []
    obs_dt = max(temperature_series)
    latest_values = {
        parameter: values[obs_dt]
        for parameter, values in weather.items()
        if obs_dt in values
    }
    radiation = (
        _parse_fmi_parameter_series(radiation_xml_text)
        if radiation_xml_text
        else {}
    )
    for parameter, values in radiation.items():
        if obs_dt in values:
            latest_values[parameter] = values[obs_dt]
    temp = latest_values["t2m"]
    wind_ms = latest_values.get("ws_10min")
    wind_gust_ms = latest_values.get("wg_10min")
    dewpoint = latest_values.get("td")
    return [
        _base_record(
            source="fmi",
            city=city,
            meta=meta,
            target_date=target_date,
            obs_dt=obs_dt,
            fetched_at=fetched,
            temp_c=temp,
            raw={
                "latest_values": latest_values,
                "weather_payload_hash": stable_hash(xml_text),
                "radiation_payload_hash": stable_hash(radiation_xml_text) if radiation_xml_text else None,
            },
            source_kind="official_airport_station",
            source_note="FMI Helsinki-Vantaa 10-minute airport observation; not runway sensor",
            extra={
                "rich_feature_contract_version": "helsinki_fmi_rich_v1",
                "fmi_rich_feature_status": "complete" if radiation_xml_text else "weather_only",
                "fmi_parameters_available": sorted(latest_values),
                "wind_speed_ms": wind_ms,
                "wind_speed_kt": round(wind_ms * 1.94384, 3) if wind_ms is not None else None,
                "wind_gust_ms": wind_gust_ms,
                "wind_gust_kt": round(wind_gust_ms * 1.94384, 3) if wind_gust_ms is not None else None,
                "wind_dir_deg": latest_values.get("wd_10min"),
                "relative_humidity_pct": latest_values.get("rh"),
                "dewpoint_c": dewpoint,
                "dewpoint_depression_c": None if dewpoint is None else temp - dewpoint,
                "pressure_hpa": latest_values.get("p_sea"),
                "precipitation_10m_mm": latest_values.get("ri_10min"),
                "precipitation_1h_mm": latest_values.get("r_1h"),
                "snow_depth_cm": latest_values.get("snow_aws"),
                "visibility_m": latest_values.get("vis"),
                "cloud_cover_okta": latest_values.get("n_man"),
                "present_weather_code": latest_values.get("wawa"),
                "global_radiation_wm2": latest_values.get("GLOB_1MIN"),
                "diffuse_radiation_wm2": latest_values.get("DIFF_1MIN"),
                "longwave_in_wm2": latest_values.get("LWIN_1MIN"),
                "longwave_out_wm2": latest_values.get("LWOUT_1MIN"),
                "reflected_radiation_wm2": latest_values.get("REFL_1MIN"),
                "sunshine_seconds": latest_values.get("SUND_1MIN"),
            },
        )
    ]


def fetch_fmi(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["fmi"][city]
    end_time = datetime.now(timezone.utc)
    start_time = end_time.replace(minute=end_time.minute // 10 * 10, second=0, microsecond=0) - timedelta(minutes=80)
    base_params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "getFeature",
        "fmisid": meta["station"],
        "timestep": "10",
        "starttime": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endtime": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    start = datetime.now(timezone.utc)
    weather_params = {
        **base_params,
        "storedquery_id": "fmi::observations::weather::timevaluepair",
        "parameters": ",".join(FMI_WEATHER_PARAMETERS),
    }
    radiation_params = {
        **base_params,
        "storedquery_id": "fmi::observations::radiation::timevaluepair",
        "parameters": ",".join(FMI_RADIATION_PARAMETERS),
    }
    text = _http_get(FMI_BASE_URL, params=weather_params, settings=settings).text
    radiation_text = _http_get(FMI_BASE_URL, params=radiation_params, settings=settings).text
    end = datetime.now(timezone.utc)
    records = parse_fmi_xml(text, radiation_xml_text=radiation_text, city=city, target_date=target_date, fetched_at=end)
    return _result("fmi", city, "ok" if records else "empty", records, start, end, metadata={"station": meta["station"], "weather_payload_hash": stable_hash(text), "radiation_payload_hash": stable_hash(radiation_text), "rich_feature_contract_version": "helsinki_fmi_rich_v1"})


def fetch_cwa(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["cwa"][city]
    token = os.environ.get("CWA_OPEN_DATA_AUTH", "").strip() or os.environ.get("CWA_OPEN_DATA_API_KEY", "").strip()
    start = datetime.now(timezone.utc)
    if not token:
        return _result("cwa", city, "auth_required", [], start, datetime.now(timezone.utc), error="CWA_OPEN_DATA_AUTH/CWA_OPEN_DATA_API_KEY not configured")
    payload = _http_get(CWA_OBSERVATIONS_URL, params={"Authorization": token, "format": "JSON", "StationId": meta["station"]}, settings=settings).json()
    end = datetime.now(timezone.utc)
    stations = (((payload.get("records") or {}).get("Station") or []) if isinstance(payload, dict) else [])
    records: list[dict[str, Any]] = []
    for raw in stations:
        temp = safe_float(((raw.get("WeatherElement") or {}).get("AirTemperature")))
        obs_dt = parse_dt(raw.get("ObsTime") or raw.get("obsTime"))
        if temp is None or obs_dt is None:
            continue
        records.append(_base_record(source="cwa", city=city, meta=meta, target_date=target_date, obs_dt=obs_dt, fetched_at=end, temp_c=temp, raw=raw, source_kind="official_reference_station", source_note="Taiwan CWA Taipei station; not runway sensor"))
    return _result("cwa", city, "ok" if records else "empty", records, start, end, metadata={"raw_payload_hash": stable_hash(payload)})


def fetch_ncm_jeddah(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["ncm_jeddah"][city]
    username = os.environ.get("NCM_API_USERNAME", "").strip()
    password = os.environ.get("NCM_API_PASSWORD", "").strip()
    start = datetime.now(timezone.utc)
    if not username or not password:
        return _result("ncm_jeddah", city, "auth_required", [], start, datetime.now(timezone.utc), error="NCM_API_USERNAME/NCM_API_PASSWORD not configured")
    now = datetime.now(timezone.utc)
    params = "t_2m:C,wind_speed_10m:ms,sfc_pressure:hPa,dew_point_2m:C,relative_humidity_2m:p"
    url = f"{NCM_API_BASE}/{now.strftime('%Y-%m-%dT%H:%M:%SZ')}/{params}/21.6702,39.1525/json?source=mix-obs"
    payload = _http_get(url, settings=settings, auth=(username, password)).json()
    end = datetime.now(timezone.utc)
    def first(key: str) -> float | None:
        arr = payload.get(key) if isinstance(payload, dict) else None
        return safe_float(arr[0]) if isinstance(arr, list) and arr else None
    temp = first("t_2m:C")
    wind_ms = first("wind_speed_10m:ms")
    records = []
    if temp is not None:
        records.append(_base_record(source="ncm_jeddah", city=city, meta=meta, target_date=target_date, obs_dt=now, fetched_at=end, temp_c=temp, raw=payload, source_kind="official_airport_station", source_note="Saudi NCM station observation through authenticated API; not runway sensor", extra={"wind_speed_kt": round(wind_ms * 1.94384, 3) if wind_ms is not None else None, "pressure_hpa": first("sfc_pressure:hPa"), "dewpoint_c": first("dew_point_2m:C"), "humidity": first("relative_humidity_2m:p")}))
    return _result("ncm_jeddah", city, "ok" if records else "empty", records, start, end, metadata={"raw_payload_hash": stable_hash(payload)})


def fetch_aeroweb(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    start = datetime.now(timezone.utc)
    if not os.environ.get("AEROWEB_USERNAME", "").strip() or not os.environ.get("AEROWEB_PASSWORD", "").strip():
        return _result("aeroweb", city, "auth_required", [], start, datetime.now(timezone.utc), error="AEROWEB_USERNAME/AEROWEB_PASSWORD not configured")
    return _result("aeroweb", city, "not_implemented", [], start, datetime.now(timezone.utc), error="AEROWEB login flow intentionally not run by high-frequency service yet")


def parse_knmi_coverage_json(payload: dict[str, Any], *, city: str = "Amsterdam", target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["knmi"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    coverages = payload.get("coverages") or ([payload] if payload.get("domain") and payload.get("ranges") else [])
    for coverage in coverages:
        axes = ((coverage.get("domain") or {}).get("axes") or {})
        times = ((axes.get("t") or {}).get("values") or [])
        ranges = coverage.get("ranges") or {}
        temp_range = ranges.get("ta") or ranges.get("t10") or {}
        values = temp_range.get("values") or []
        for index, raw_time in enumerate(times):
            temp = safe_float(values[index] if index < len(values) else None)
            obs_dt = parse_dt(raw_time)
            if temp is None or obs_dt is None:
                continue
            rows.append(
                _base_record(
                    source="knmi",
                    city=city,
                    meta=meta,
                    target_date=target_date,
                    obs_dt=obs_dt,
                    fetched_at=fetched,
                    temp_c=temp,
                    raw={"coverage": coverage, "time_index": index},
                    source_kind="official_airport_station",
                    source_note="KNMI Schiphol 10-minute station observation; not runway sensor",
                    extra={"wigos_station_id": coverage.get("eumetnet:locationId") or meta["station"]},
                )
            )
    return sorted(rows, key=lambda row: row["observation_time_utc"])


def fetch_knmi(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["knmi"][city]
    start = datetime.now(timezone.utc)
    token = os.environ.get("KNMI_API_KEY", "").strip()
    if not token:
        return _result("knmi", city, "auth_required", [], start, datetime.now(timezone.utc), error="KNMI_API_KEY not configured")
    now = datetime.now(timezone.utc)
    date_range = f"{(now - timedelta(minutes=40)).isoformat().replace('+00:00', 'Z')}/{now.isoformat().replace('+00:00', 'Z')}"
    payload = _http_get(
        f"{KNMI_EDR_BASE}/locations/{meta['station']}",
        params={"f": "CoverageJSON", "datetime": date_range, "parameter-name": "ta"},
        headers={"Authorization": token, "Accept": "application/prs.coverage+json"},
        settings=settings,
    ).json()
    end = datetime.now(timezone.utc)
    records = parse_knmi_coverage_json(payload, city=city, target_date=target_date, fetched_at=end)
    return _result("knmi", city, "ok" if records else "empty", records[-12:], start, end, metadata={"raw_payload_hash": stable_hash(payload)})


def parse_meteofrance_payload(payload: Any, *, city: str = "Paris", target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["meteofrance_6m"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    if isinstance(payload, list):
        raw_rows = payload
    elif isinstance(payload, dict):
        raw_rows = payload.get("features") or []
    else:
        raw_rows = []
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        raw = item.get("properties") if isinstance(item, dict) and isinstance(item.get("properties"), dict) else item
        if not isinstance(raw, dict):
            continue
        temp = safe_float(raw.get("t"))
        obs_dt = parse_dt(raw.get("validity_time") or raw.get("date") or raw.get("reference_time"))
        if temp is None or obs_dt is None:
            continue
        temp_c = temp - 273.15 if temp > 150 else temp
        rows.append(
            _base_record(
                source="meteofrance_6m",
                city=city,
                meta=meta,
                target_date=target_date,
                obs_dt=obs_dt,
                fetched_at=fetched,
                temp_c=temp_c,
                raw=item,
                source_kind="official_airport_station",
                source_note="Meteo-France Le Bourget 6-minute station observation; not runway sensor",
                extra={
                    "reference_time_utc": raw.get("reference_time"),
                    "source_insert_time_utc": raw.get("insert_time"),
                    "humidity": safe_float(raw.get("u")),
                    "wind_speed_ms": safe_float(raw.get("ff")),
                    "pressure_pa": safe_float(raw.get("pres")),
                },
            )
        )
    return sorted(rows, key=lambda row: row["observation_time_utc"])


def fetch_meteofrance_6m(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["meteofrance_6m"][city]
    start = datetime.now(timezone.utc)
    token = os.environ.get("METEOFRANCE_API_TOKEN", "").strip() or os.environ.get("METEOFRANCE_API_KEY", "").strip()
    if not token:
        return _result("meteofrance_6m", city, "auth_required", [], start, datetime.now(timezone.utc), error="METEOFRANCE_API_TOKEN/METEOFRANCE_API_KEY not configured")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    payload = _http_get(METEOFRANCE_OBS_URL, params={"id_station": meta["station"], "format": "json"}, headers=headers, settings=settings).json()
    end = datetime.now(timezone.utc)
    records = parse_meteofrance_payload(payload, city=city, target_date=target_date, fetched_at=end)
    return _result("meteofrance_6m", city, "ok" if records else "empty", records[-12:], start, end, metadata={"raw_payload_hash": stable_hash(payload)})


def parse_dwd_10m_zip(content: bytes, *, city: str = "Munich", target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["dwd_10m"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        product_names = [name for name in archive.namelist() if name.startswith("produkt_")]
        if not product_names:
            return []
        text = archive.read(product_names[0]).decode("latin-1")
    rows: list[dict[str, Any]] = []
    for raw in csv.DictReader(io.StringIO(text), delimiter=";"):
        normalized = {str(key or "").strip(): value.strip() if isinstance(value, str) else value for key, value in raw.items()}
        try:
            local = datetime.strptime(
                str(normalized.get("MESS_DATUM") or ""), "%Y%m%d%H%M"
            )
            obs_dt = local_wall_time_to_utc(
                local, timezone_name="Etc/UTC", field="dwd_mess_datum_utc"
            )
        except ValueError:
            continue
        temp = safe_float(normalized.get("TT_10"))
        if temp is None or temp <= -900:
            continue
        rows.append(
            _base_record(
                source="dwd_10m",
                city=city,
                meta=meta,
                target_date=target_date,
                obs_dt=obs_dt,
                fetched_at=fetched,
                temp_c=temp,
                raw=normalized,
                source_kind="official_airport_station",
                source_note="DWD Munich Airport 10-minute CDC observation; publication can lag hours",
                extra={"humidity": safe_float(normalized.get("RF_10")), "dewpoint_c": safe_float(normalized.get("TD_10"))},
            )
        )
    return sorted(rows, key=lambda row: row["observation_time_utc"])


def fetch_dwd_10m(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    start = datetime.now(timezone.utc)
    response = _http_get(DWD_MUNICH_10M_URL, settings=settings)
    end = datetime.now(timezone.utc)
    records = parse_dwd_10m_zip(response.content, city=city, target_date=target_date, fetched_at=end)
    return _result("dwd_10m", city, "ok" if records else "empty", records[-24:], start, end, metadata={"raw_payload_hash": stable_hash(response.content.hex())})


def parse_bom_aws_payload(payload: Any, *, city: str, target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["bom_aws"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    observations = payload.get("observations") if isinstance(payload, dict) else None
    raw_rows = observations.get("data") if isinstance(observations, dict) else []
    rows: list[dict[str, Any]] = []
    for raw in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(raw, dict):
            continue
        temp_c = safe_float(raw.get("air_temp"))
        timestamp = str(raw.get("aifstime_utc") or "").strip()
        try:
            local = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
            obs_dt = local_wall_time_to_utc(
                local, timezone_name="Etc/UTC", field="bom_aifstime_utc"
            )
        except ValueError:
            obs_dt = None
        if temp_c is None or obs_dt is None:
            continue
        rows.append(
            _base_record(
                source="bom_aws",
                city=city,
                meta=meta,
                target_date=target_date,
                obs_dt=obs_dt,
                fetched_at=fetched,
                temp_c=temp_c,
                raw=raw,
                source_kind="official_airport_station",
                source_note="Australian Bureau of Meteorology airport AWS public observation; research enrichment, not settlement truth unless market rules name the same station",
                extra={
                    "wmo": str(raw.get("wmo") or meta["station"]),
                    "local_observation_time": raw.get("local_date_time_full"),
                    "humidity": safe_float(raw.get("rel_hum")),
                    "dewpoint_c": safe_float(raw.get("dewpt")),
                    "wind_speed_kmh": safe_float(raw.get("wind_spd_kmh")),
                    "wind_gust_kmh": safe_float(raw.get("gust_kmh")),
                    "pressure_hpa": safe_pressure_hpa(raw.get("press_msl")),
                    "product_id": meta["product_id"],
                },
            )
        )
    return sorted(rows, key=lambda row: row["observation_time_utc"])


def fetch_bom_aws(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["bom_aws"][city]
    start = datetime.now(timezone.utc)
    product_id = str(meta["product_id"])
    station = str(meta["station"])
    url = f"{BOM_AWS_BASE}/{product_id}/{product_id}.{station}.json"
    payload = _http_get(url, headers={"Accept": "application/json"}, settings=settings).json()
    end = datetime.now(timezone.utc)
    records = parse_bom_aws_payload(payload, city=city, target_date=target_date, fetched_at=end)
    return _result(
        "bom_aws",
        city,
        "ok" if records else "empty",
        records[-24:],
        start,
        end,
        metadata={"url": url, "raw_payload_hash": stable_hash(payload)},
    )


def parse_aemet_payload(payload: Any, *, city: str = "Madrid", target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["aemet_10m"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for raw in payload if isinstance(payload, list) else []:
        temp = safe_float(raw.get("ta"))
        obs_dt = parse_dt(raw.get("fint"))
        if temp is None or obs_dt is None:
            continue
        rows.append(
            _base_record(
                source="aemet_10m",
                city=city,
                meta=meta,
                target_date=target_date,
                obs_dt=obs_dt,
                fetched_at=fetched,
                temp_c=temp,
                raw=raw,
                source_kind="official_airport_station",
                source_note="AEMET Madrid Barajas conventional observation; cadence must be measured",
                extra={"humidity": safe_float(raw.get("hr")), "pressure_hpa": safe_float(raw.get("pres")), "wind_speed_ms": safe_float(raw.get("vv")), "wind_dir": safe_float(raw.get("dv"))},
            )
        )
    return sorted(rows, key=lambda row: row["observation_time_utc"])


def fetch_aemet_10m(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["aemet_10m"][city]
    start = datetime.now(timezone.utc)
    token = os.environ.get("AEMET_API_KEY", "").strip()
    if not token:
        return _result("aemet_10m", city, "auth_required", [], start, datetime.now(timezone.utc), error="AEMET_API_KEY not configured")
    manifest = _http_get(f"{AEMET_API_BASE}/observacion/convencional/datos/estacion/{meta['station']}", params={"api_key": token}, settings=settings).json()
    data_url = str(manifest.get("datos") or "")
    if not data_url:
        return _result("aemet_10m", city, "empty", [], start, datetime.now(timezone.utc), error=f"AEMET response missing datos URL: {manifest.get('descripcion') or manifest.get('estado')}")
    payload = _http_get(data_url, settings=settings).json()
    end = datetime.now(timezone.utc)
    records = parse_aemet_payload(payload, city=city, target_date=target_date, fetched_at=end)
    return _result("aemet_10m", city, "ok" if records else "empty", records[-24:], start, end, metadata={"raw_payload_hash": stable_hash(payload)})


def parse_ims_1m_payload(payload: dict[str, Any], *, city: str = "Tel Aviv", target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["ims_1m"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for raw in payload.get("data") or []:
        obs_dt = _zoned_iso_to_utc(raw.get("datetime"), meta["timezone_name"])
        channels = {str(channel.get("name") or "").upper(): channel for channel in raw.get("channels") or []}
        temp_channel = channels.get("TD") or channels.get("TA") or {}
        temp = safe_float(temp_channel.get("value"))
        if temp is None or obs_dt is None:
            continue
        rows.append(
            _base_record(
                source="ims_1m",
                city=city,
                meta=meta,
                target_date=target_date,
                obs_dt=obs_dt,
                fetched_at=fetched,
                temp_c=temp,
                raw=raw,
                source_kind="official_airport_station",
                source_note="IMS Lod Airport authenticated 1-minute observation; naive timestamps use Asia/Jerusalem",
                extra={"humidity": safe_float((channels.get("RH") or {}).get("value")), "wind_speed_ms": safe_float((channels.get("WS") or {}).get("value")), "wind_dir": safe_float((channels.get("WD") or {}).get("value"))},
            )
        )
    return sorted(rows, key=lambda row: row["observation_time_utc"])


def fetch_ims_1m(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["ims_1m"][city]
    start = datetime.now(timezone.utc)
    token = os.environ.get("IMS_API_TOKEN", "").strip()
    if not token:
        return _result("ims_1m", city, "auth_required", [], start, datetime.now(timezone.utc), error="IMS_API_TOKEN not configured")
    now = datetime.now(timezone.utc)
    params = {"from": (now - timedelta(minutes=30)).strftime("%Y/%m/%d %H:%M"), "to": now.strftime("%Y/%m/%d %H:%M")}
    payload = _http_get(f"{IMS_API_BASE}/stations/{meta['station']}/data/", params=params, headers={"Authorization": f"ApiToken {token}"}, settings=settings).json()
    end = datetime.now(timezone.utc)
    records = parse_ims_1m_payload(payload, city=city, target_date=target_date, fetched_at=end)
    return _result("ims_1m", city, "ok" if records else "empty", records[-30:], start, end, metadata={"raw_payload_hash": stable_hash(payload)})


def parse_eccc_swob_xml(xml_text: str, *, city: str = "Toronto", target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["eccc_swob"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    root = ElementTree.fromstring(xml_text)
    values: dict[str, str] = {}
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "element" and element.get("name"):
            values[str(element.get("name"))] = str(element.get("value") or "")
    temp = safe_float(values.get("air_temp"))
    obs_dt = parse_dt(values.get("date_tm"))
    if temp is None or obs_dt is None:
        return []
    wind_kmh = safe_float(values.get("avg_wnd_spd_10m_pst2mts"))
    return [
        _base_record(
            source="eccc_swob",
            city=city,
            meta=meta,
            target_date=target_date,
            obs_dt=obs_dt,
            fetched_at=fetched,
            temp_c=temp,
            raw=values,
            source_kind="official_airport_station",
            source_note="ECCC/NAV CANADA SWOB airport observation; CYYZ public feed is hourly MAN",
            extra={"dewpoint_c": safe_float(values.get("dwpt_temp")), "humidity": safe_float(values.get("rel_hum")), "wind_speed_kt": round(wind_kmh / 1.852, 3) if wind_kmh is not None else None, "max_temp_c_past_1h": safe_float(values.get("max_air_temp_pst1hr"))},
        )
    ]


def fetch_eccc_swob(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["eccc_swob"][city]
    start = datetime.now(timezone.utc)
    text = _http_get(f"{ECCC_SWOB_LATEST_BASE}/{meta['station']}-{meta['report_type']}-swob.xml", settings=settings).text
    end = datetime.now(timezone.utc)
    records = parse_eccc_swob_xml(text, city=city, target_date=target_date, fetched_at=end)
    return _result("eccc_swob", city, "ok" if records else "empty", records, start, end, metadata={"raw_payload_hash": stable_hash(text)})


def fetch_metservice_1m(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    start = datetime.now(timezone.utc)
    if not os.environ.get("METSERVICE_API_KEY", "").strip():
        return _result("metservice_1m", city, "auth_required", [], start, datetime.now(timezone.utc), error="METSERVICE_API_KEY not configured; commercial trial/product access is required")
    return _result("metservice_1m", city, "contract_required", [], start, datetime.now(timezone.utc), error="MetService tenant endpoint/product contract must be configured after trial activation")


def fetch_amos_high_frequency(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    start = datetime.now(timezone.utc)
    cfg = settings or HighFrequencyFetchSettings()
    runway_result = fetch_amos_runway(city, settings=RunwayFetchSettings(timeout_sec=cfg.timeout_sec, proxy_candidates=cfg.proxy_candidates, user_agent=cfg.user_agent), target_date=target_date)
    end = datetime.now(timezone.utc)
    records: list[dict[str, Any]] = []
    for raw in runway_result.records:
        obs_dt = parse_dt(raw.get("observation_time_utc"))
        temp = safe_float(raw.get("point_temp_c") or raw.get("target_runway_temp_c"))
        if obs_dt is None or temp is None:
            continue
        meta = HIGH_FREQUENCY_CITY_SOURCES["amos_runway"][city]
        extra = {
            k: raw.get(k)
            for k in (
                "runway",
                "primary_runway",
                "preferred_temperature_runway",
                "is_preferred_temperature_runway",
                "runway_temp_min_c",
                "runway_temp_max_c",
                "runway_temp_avg_c",
                "dewpoint_c",
                "wind_dir",
                "wind_speed",
                "rvr",
                "mor",
                "raw_metar",
                "metar_temp_c",
            )
            if k in raw
        }
        records.append(
            _base_record(
                source="amos_runway",
                city=city,
                meta=meta,
                target_date=target_date,
                obs_dt=obs_dt,
                fetched_at=end,
                temp_c=temp,
                raw=raw,
                source_kind="runway_air_temperature",
                source_note="Korean AMOS runway-level air temperature; not pavement temperature",
                extra=extra,
            )
        )
    return _result("amos_runway", city, "ok" if records else runway_result.status, records, start, end, error=runway_result.error, metadata=runway_result.metadata)


FETCHERS = {
    "amos_runway": fetch_amos_high_frequency,
    "noaa_madis_hfmetar": fetch_noaa_madis_hfmetar,
    "singapore_mss": fetch_singapore_mss,
    "jma_amedas": fetch_jma_amedas,
    "hko_obs": fetch_hko_obs,
    "cowin_obs": fetch_cowin_obs,
    "cwa": fetch_cwa,
    "mgm": fetch_mgm,
    "ims_lod": fetch_ims_lod,
    "ncm_jeddah": fetch_ncm_jeddah,
    "aeroweb": fetch_aeroweb,
    "fmi": fetch_fmi,
    "knmi": fetch_knmi,
    "meteofrance_6m": fetch_meteofrance_6m,
    "dwd_10m": fetch_dwd_10m,
    "aemet_10m": fetch_aemet_10m,
    "ims_1m": fetch_ims_1m,
    "metservice_1m": fetch_metservice_1m,
    "eccc_swob": fetch_eccc_swob,
    "bom_aws": fetch_bom_aws,
}


def supported_high_frequency_sources() -> dict[str, dict[str, dict[str, Any]]]:
    return {source: dict(cities) for source, cities in HIGH_FREQUENCY_CITY_SOURCES.items()}


def fetch_high_frequency_observation(
    source: str,
    city: str,
    *,
    settings: HighFrequencyFetchSettings | None = None,
    now_utc: datetime | None = None,
) -> HighFrequencyFetchResult:
    source_key = str(source or "").strip().lower()
    city_meta = HIGH_FREQUENCY_CITY_SOURCES.get(source_key, {}).get(city)
    if city_meta is None:
        now = datetime.now(timezone.utc)
        return HighFrequencyFetchResult(source_key, city, "unsupported_city", now.isoformat(), 0.0, metadata={"city": city})
    fetcher = FETCHERS.get(source_key)
    if fetcher is None:
        now = datetime.now(timezone.utc)
        return HighFrequencyFetchResult(source_key, city, "unsupported_source", now.isoformat(), 0.0, metadata={"city": city})
    target_date = _target_date(city_meta, now_utc or datetime.now(timezone.utc))
    return fetcher(city, settings=settings, target_date=target_date)


def netcdf4_available() -> bool:
    try:
        import netCDF4  # noqa: F401

        return True
    except Exception:
        return False
