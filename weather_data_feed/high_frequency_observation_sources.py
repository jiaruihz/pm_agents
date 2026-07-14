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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from weather_data_feed.observation_sources import FetchSettings, ObservationSourceRequest, fetch_observation_source
from weather_data_feed.observation_sources.fetchers import arith_round, c_to_f, parse_dt as parse_source_dt
from weather_data_feed.runway_sources import RunwayFetchSettings, fetch_amos_runway


SINGAPORE_MSS_TEMP_URL = "https://api.data.gov.sg/v1/environment/air-temperature"
SINGAPORE_MSS_TEMP_V2_URL = "https://api-open.data.gov.sg/v2/real-time/api/air-temperature"
JMA_AMEDAS_BASE = "https://www.jma.go.jp"
HKO_BASE_URL = "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather"
COWIN_BASE_URL = "https://cowin.hku.hk"
MGM_BASE_URL = "https://servis.mgm.gov.tr/web"
IMS_OBSERVATIONS_URL = "https://ims.gov.il/en/hourly_observations_full"
FMI_BASE_URL = "https://opendata.fmi.fi/wfs"
KNMI_API_BASE = "https://api.dataplatform.knmi.nl/open-data/v1"
KNMI_DATASET = "10-minute-in-situ-meteorological-observations"
KNMI_VERSION = "1.0"
CWA_OBSERVATIONS_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001"
NCM_API_BASE = "https://api-mm.ncm.gov.sa"
AEROWEB_BASE = "https://aviation.meteo.fr"

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
        "Amsterdam": {"station": "06240", "label": "Schiphol KNMI 10min", "timezone_name": "Europe/Amsterdam", "icao": "EHAM"},
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
        return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_jma_amedas_payload(payload: dict[str, Any], *, city: str = "Tokyo", target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["jma_amedas"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for key in sorted(payload):
        row = payload.get(key) or {}
        temp_pair = row.get("temp") or []
        temp = safe_float(temp_pair[0] if isinstance(temp_pair, list) and temp_pair else None)
        obs_dt = _jma_obs_time_from_key(key)
        if temp is None or obs_dt is None:
            continue
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
                extra={"station_code": meta["station"]},
            )
        )
    return rows


def fetch_jma_amedas(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["jma_amedas"][city]
    start = datetime.now(timezone.utc)
    latest_text = _http_get(f"{JMA_AMEDAS_BASE}/bosai/amedas/data/latest_time.txt", settings=settings).text.strip()
    latest_dt = datetime.fromisoformat(latest_text)
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
        return datetime.strptime(raw, "%Y%m%d%H%M").replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
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
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return dt.astimezone(timezone.utc)


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
    now = datetime.now(timezone(timedelta(hours=8)))
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


def _local_iso_to_utc(value: Any, offset_hours: int) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=offset_hours)))
    return dt.astimezone(timezone.utc)


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
        obs_dt = _local_iso_to_utc(raw.get("veriZamani"), 3)
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
        obs_dt = _local_iso_to_utc(latest_time, 3)
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


def parse_fmi_xml(xml_text: str, *, city: str = "Helsinki", target_date: str = "", fetched_at: datetime | None = None) -> list[dict[str, Any]]:
    meta = HIGH_FREQUENCY_CITY_SOURCES["fmi"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    latest_values: dict[str, float] = {}
    obs_dt: datetime | None = None
    for block in re.split(r"<om:observedProperty\s", xml_text):
        param_match = re.search(r"param=(\w+)", block)
        if not param_match:
            continue
        pairs = re.findall(r"<wml2:MeasurementTVP>.*?<wml2:time>(.*?)</wml2:time>\s*<wml2:value>(.*?)</wml2:value>", block, re.DOTALL)
        if not pairs:
            continue
        latest_time, latest_val = pairs[-1]
        value = safe_float(latest_val)
        if value is None:
            continue
        latest_values[param_match.group(1)] = value
        obs_dt = parse_dt(latest_time) or obs_dt
    temp = latest_values.get("t2m")
    if temp is None or obs_dt is None:
        return []
    wind_ms = latest_values.get("ws_10min")
    return [
        _base_record(
            source="fmi",
            city=city,
            meta=meta,
            target_date=target_date,
            obs_dt=obs_dt,
            fetched_at=fetched,
            temp_c=temp,
            raw={"latest_values": latest_values},
            source_kind="official_airport_station",
            source_note="FMI Helsinki-Vantaa 10-minute airport observation; not runway sensor",
            extra={
                "wind_speed_kt": round(wind_ms * 1.94384, 3) if wind_ms is not None else None,
                "pressure_hpa": latest_values.get("p_sea"),
            },
        )
    ]


def fetch_fmi(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    meta = HIGH_FREQUENCY_CITY_SOURCES["fmi"][city]
    end_time = datetime.now(timezone.utc)
    start_time = end_time.replace(minute=end_time.minute // 10 * 10, second=0, microsecond=0) - timedelta(minutes=20)
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "getFeature",
        "storedquery_id": "fmi::observations::weather::timevaluepair",
        "place": "helsinki-vantaa_airport",
        "parameters": "t2m,ws_10min,p_sea",
        "starttime": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endtime": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    start = datetime.now(timezone.utc)
    text = _http_get(FMI_BASE_URL, params=params, settings=settings).text
    end = datetime.now(timezone.utc)
    records = parse_fmi_xml(text, city=city, target_date=target_date, fetched_at=end)
    return _result("fmi", city, "ok" if records else "empty", records, start, end, metadata={"station": meta["station"], "raw_payload_hash": stable_hash(text)})


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


def fetch_knmi(city: str, *, settings: HighFrequencyFetchSettings | None = None, target_date: str = "") -> HighFrequencyFetchResult:
    start = datetime.now(timezone.utc)
    if not os.environ.get("KNMI_API_KEY", "").strip():
        return _result("knmi", city, "auth_required", [], start, datetime.now(timezone.utc), error="KNMI_API_KEY not configured")
    return _result("knmi", city, "not_implemented", [], start, datetime.now(timezone.utc), error="KNMI NetCDF fetch requires API key and netCDF parser wiring")


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
