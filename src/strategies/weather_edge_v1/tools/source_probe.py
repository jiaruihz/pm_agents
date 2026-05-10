from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.strategies.weather_edge_v1.tools.profile_resolver import _safe_yaml_load, load_profiles


ROOT = Path(__file__).resolve().parent.parent
CITY_DIR = ROOT / "city"
AVIATION_WEATHER_BASE = "https://aviationweather.gov/api/data"
OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

LOCAL_OFFICIAL_SOURCE_MAP: Dict[str, List[Dict[str, Any]]] = {
    "seoul": [
        {
            "source_key": "korea_amo_realtime",
            "family": "korea_amo",
            "page_type": "official_airport_page",
            "role": "local_official",
            "url": "https://global.amo.go.kr/amosobsnew/AmosRealTimeImage.do",
            "keywords": ["RKSI", "INCHEON", "항공기상정보"],
        }
    ],
    "tokyo": [
        {
            "source_key": "jma_haneda_index",
            "family": "jma",
            "page_type": "official_airport_page",
            "role": "local_official",
            "url": "https://www.data.jma.go.jp/haneda-airport/index.html",
            "keywords": ["RJTT", "HANEDA", "TOKYO AVIATION WEATHER SERVICE CENTER"],
        }
    ],
    "osaka": [
        {
            "source_key": "jma_kansai_index",
            "family": "jma",
            "page_type": "official_airport_page",
            "role": "local_official",
            "url": "https://www.data.jma.go.jp/kansai-airport/index.html",
            "keywords": ["RJBB", "KANSAI", "AVIATION WEATHER SERVICE CENTER"],
        }
    ],
}

UNRESOLVED_FAMILIES = {"weather.com", "accuweather", "meteoblue"}


@dataclass
class SourceTarget:
    city_key: str
    station_code: str
    station_name: str
    local_date: str
    family: str
    page_type: str
    role: str
    source_key: str
    url: str
    probe_kind: str
    expected_keywords: List[str]
    notes: str = ""


def _build_session() -> requests.Session:
    session = requests.Session()
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=2,
                connect=2,
                read=2,
                backoff_factor=0.4,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=frozenset(["GET"]),
            )
        ),
    )
    session.headers.update({"User-Agent": "Mozilla/5.0 pm-agent weather-source-probe"})
    return session


def _load_yaml(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        data = _safe_yaml_load(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _city_yaml(city_key: str) -> Dict[str, Any]:
    path = CITY_DIR / f"{city_key.upper()}.yml"
    if not path.exists():
        raise FileNotFoundError(path)
    return _load_yaml(path)


def _clean_list(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _extract_city_sources(city_key: str) -> List[Dict[str, Any]]:
    payload = _city_yaml(city_key)
    sources = dict(payload.get("sources") or {})
    out: List[Dict[str, Any]] = []
    for key in ("primary_settlement", "primary_trading_anchor"):
        item = sources.get(key)
        if isinstance(item, dict):
            row = dict(item)
            row["role_key"] = key
            out.append(row)
    for key in ("consensus_sources", "supporting_sources"):
        for item in _clean_list(sources.get(key)):
            row = dict(item)
            row["role_key"] = key
            out.append(row)
    return out


def _unresolved_target(city_key: str, local_date: str, station_code: str, station_name: str, family: str, page_type: str, role: str) -> SourceTarget:
    return SourceTarget(
        city_key=city_key,
        station_code=station_code,
        station_name=station_name,
        local_date=local_date,
        family=family,
        page_type=page_type,
        role=role,
        source_key=f"{family}_{page_type}",
        url="",
        probe_kind="unresolved",
        expected_keywords=[station_code, station_name],
        notes="需要人工搜索或单独补 URL 模板",
    )


def build_source_targets(city_key: str, local_date: str) -> List[SourceTarget]:
    bundle = load_profiles(city_key=city_key, local_date=local_date)
    station = bundle.station
    station_code = str(station.get("station_code") or "")
    station_name = str(station.get("station_name") or station_code)
    lat = float(station.get("lat") or 0.0)
    lon = float(station.get("lon") or 0.0)
    timezone_name = str(station.get("timezone") or "UTC")

    targets: List[SourceTarget] = [
        SourceTarget(
            city_key=city_key,
            station_code=station_code,
            station_name=station_name,
            local_date=local_date,
            family="official_aviation_weather",
            page_type="stationinfo_api",
            role="official_api",
            source_key="aviationweather_stationinfo",
            url=f"{AVIATION_WEATHER_BASE}/stationinfo?ids={station_code}&format=json",
            probe_kind="aviationweather_stationinfo",
            expected_keywords=[station_code],
        ),
        SourceTarget(
            city_key=city_key,
            station_code=station_code,
            station_name=station_name,
            local_date=local_date,
            family="official_aviation_weather",
            page_type="metar_api",
            role="official_api",
            source_key="aviationweather_metar",
            url=f"{AVIATION_WEATHER_BASE}/metar?ids={station_code}&format=json",
            probe_kind="aviationweather_metar",
            expected_keywords=[station_code],
        ),
        SourceTarget(
            city_key=city_key,
            station_code=station_code,
            station_name=station_name,
            local_date=local_date,
            family="official_aviation_weather",
            page_type="taf_api",
            role="official_api",
            source_key="aviationweather_taf",
            url=f"{AVIATION_WEATHER_BASE}/taf?ids={station_code}&format=json",
            probe_kind="aviationweather_taf",
            expected_keywords=[station_code],
        ),
        SourceTarget(
            city_key=city_key,
            station_code=station_code,
            station_name=station_name,
            local_date=local_date,
            family="open_meteo",
            page_type="forecast_api",
            role="model_api",
            source_key="open_meteo_forecast",
            url=(
                f"{OPEN_METEO_BASE}?latitude={lat}&longitude={lon}"
                f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
                f"&hourly=temperature_2m&forecast_days=7&timezone={timezone_name}"
            ),
            probe_kind="open_meteo_forecast",
            expected_keywords=[local_date],
        ),
    ]

    for item in _extract_city_sources(city_key):
        family = str(item.get("family") or "")
        page_type = str(item.get("page_type") or "")
        role = str(item.get("role") or item.get("role_key") or "")
        if family == "wunderground" and page_type == "history_daily":
            targets.append(
                SourceTarget(
                    city_key=city_key,
                    station_code=station_code,
                    station_name=station_name,
                    local_date=local_date,
                    family=family,
                    page_type=page_type,
                    role=role,
                    source_key="wunderground_history_daily",
                    url=f"https://www.wunderground.com/history/daily/{station_code}/date/{local_date}",
                    probe_kind="wu_html",
                    expected_keywords=[station_code, local_date],
                )
            )
        elif family == "wunderground" and page_type == "hourly_forecast":
            targets.append(
                SourceTarget(
                    city_key=city_key,
                    station_code=station_code,
                    station_name=station_name,
                    local_date=local_date,
                    family=family,
                    page_type=page_type,
                    role=role,
                    source_key="wunderground_hourly",
                    url=f"https://www.wunderground.com/hourly/{station_code}/date/{local_date}",
                    probe_kind="wu_html",
                    expected_keywords=[station_code, local_date],
                )
            )
        elif family == "checkwx":
            targets.append(
                SourceTarget(
                    city_key=city_key,
                    station_code=station_code,
                    station_name=station_name,
                    local_date=local_date,
                    family=family,
                    page_type="metar_html",
                    role=role,
                    source_key="checkwx_metar",
                    url=f"https://www.checkwx.com/weather/{station_code}/metar",
                    probe_kind="checkwx_html",
                    expected_keywords=[station_code, "METAR"],
                )
            )
            targets.append(
                SourceTarget(
                    city_key=city_key,
                    station_code=station_code,
                    station_name=station_name,
                    local_date=local_date,
                    family=family,
                    page_type="taf_html",
                    role=role,
                    source_key="checkwx_taf",
                    url=f"https://www.checkwx.com/weather/{station_code}/taf",
                    probe_kind="checkwx_html",
                    expected_keywords=[station_code, "TAF"],
                )
            )
        elif family in UNRESOLVED_FAMILIES:
            targets.append(_unresolved_target(city_key, local_date, station_code, station_name, family, page_type, role))

    for item in LOCAL_OFFICIAL_SOURCE_MAP.get(city_key, []):
        targets.append(
            SourceTarget(
                city_key=city_key,
                station_code=station_code,
                station_name=station_name,
                local_date=local_date,
                family=str(item.get("family") or ""),
                page_type=str(item.get("page_type") or ""),
                role=str(item.get("role") or ""),
                source_key=str(item.get("source_key") or ""),
                url=str(item.get("url") or ""),
                probe_kind="html_keywords",
                expected_keywords=[str(x) for x in item.get("keywords", [])],
            )
        )

    deduped: List[SourceTarget] = []
    seen: set[tuple[str, str]] = set()
    for row in targets:
        key = (row.source_key, row.url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _html_title(text: str) -> str:
    match = re.search(r"<title>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _contains_keywords(text: str, keywords: Iterable[str]) -> bool:
    upper = text.upper()
    return all(keyword.upper() in upper for keyword in keywords if keyword)


def _probe_aviationweather_stationinfo(session: requests.Session, target: SourceTarget) -> Dict[str, Any]:
    resp = session.get(target.url, timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    row = rows[0] if isinstance(rows, list) and rows else {}
    if not isinstance(row, dict):
        row = {}
    return {
        "status": "ok" if row else "empty",
        "http_status": resp.status_code,
        "final_url": str(resp.url),
        "station_match": str(row.get("icaoId") or "") == target.station_code,
        "title": str(row.get("site") or ""),
        "extracted": {
            "icao_id": row.get("icaoId"),
            "iata_id": row.get("iataId"),
            "site": row.get("site"),
            "lat": row.get("lat"),
            "lon": row.get("lon"),
        },
    }


def _probe_aviationweather_metar(session: requests.Session, target: SourceTarget) -> Dict[str, Any]:
    resp = session.get(target.url, timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    row = rows[0] if isinstance(rows, list) and rows else {}
    if not isinstance(row, dict):
        row = {}
    return {
        "status": "ok" if row else "empty",
        "http_status": resp.status_code,
        "final_url": str(resp.url),
        "station_match": str(row.get("icaoId") or "") == target.station_code,
        "title": str(row.get("name") or target.station_name),
        "extracted": {
            "report_time": row.get("reportTime"),
            "temp_c": row.get("temp"),
            "dewp_c": row.get("dewp"),
            "wind_dir": row.get("wdir"),
            "wind_speed_kt": row.get("wspd"),
            "raw_metar": row.get("rawOb"),
        },
    }


def _probe_aviationweather_taf(session: requests.Session, target: SourceTarget) -> Dict[str, Any]:
    resp = session.get(target.url, timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    row = rows[0] if isinstance(rows, list) and rows else {}
    if not isinstance(row, dict):
        row = {}
    return {
        "status": "ok" if row else "empty",
        "http_status": resp.status_code,
        "final_url": str(resp.url),
        "station_match": str(row.get("icaoId") or "") == target.station_code,
        "title": str(row.get("name") or target.station_name),
        "extracted": {
            "issue_time": row.get("issueTime"),
            "valid_from": row.get("validTimeFrom"),
            "valid_to": row.get("validTimeTo"),
            "raw_taf": row.get("rawTAF"),
        },
    }


def _probe_open_meteo(session: requests.Session, target: SourceTarget) -> Dict[str, Any]:
    resp = session.get(target.url, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    daily = payload.get("daily", {}) or {}
    dates = daily.get("time", []) or []
    idx = dates.index(target.local_date) if target.local_date in dates else -1
    extracted: Dict[str, Any] = {}
    if idx >= 0:
        extracted = {
            "forecast_date": target.local_date,
            "max_temp_c": (daily.get("temperature_2m_max", []) or [None])[idx],
            "min_temp_c": (daily.get("temperature_2m_min", []) or [None])[idx],
            "precip_prob_max": (daily.get("precipitation_probability_max", []) or [None])[idx],
        }
    return {
        "status": "ok" if idx >= 0 else "target_date_missing",
        "http_status": resp.status_code,
        "final_url": str(resp.url),
        "station_match": True,
        "title": "Open-Meteo Forecast API",
        "extracted": extracted,
    }


def _probe_wu_html(session: requests.Session, target: SourceTarget) -> Dict[str, Any]:
    resp = session.get(target.url, timeout=20)
    resp.raise_for_status()
    text = resp.text
    title = _html_title(text)
    canonical_match = re.search(r'rel="canonical"\s+href="([^"]+)"', text, flags=re.IGNORECASE)
    return {
        "status": "ok" if title else "missing_title",
        "http_status": resp.status_code,
        "final_url": str(resp.url),
        "station_match": _contains_keywords(text, [target.station_code]),
        "title": title,
        "extracted": {
            "canonical_url": canonical_match.group(1) if canonical_match else "",
            "target_date_present": target.local_date in text,
        },
    }


def _probe_checkwx_html(session: requests.Session, target: SourceTarget) -> Dict[str, Any]:
    resp = session.get(target.url, timeout=20)
    resp.raise_for_status()
    text = resp.text
    title = _html_title(text)
    observed_match = re.search(r"Observed.*?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", text, flags=re.IGNORECASE | re.DOTALL)
    raw_match = re.search(rf"{target.station_code}\s+\d{{6}}Z[^<]+", text)
    return {
        "status": "ok" if title else "missing_title",
        "http_status": resp.status_code,
        "final_url": str(resp.url),
        "station_match": _contains_keywords(text, [target.station_code]),
        "title": title,
        "extracted": {
            "observed_at": observed_match.group(1) if observed_match else "",
            "raw_text": raw_match.group(0).strip() if raw_match else "",
        },
    }


def _probe_html_keywords(session: requests.Session, target: SourceTarget) -> Dict[str, Any]:
    resp = session.get(target.url, timeout=20)
    resp.raise_for_status()
    text = resp.text
    title = _html_title(text)
    return {
        "status": "ok" if title else "missing_title",
        "http_status": resp.status_code,
        "final_url": str(resp.url),
        "station_match": _contains_keywords(text, target.expected_keywords),
        "title": title,
        "extracted": {
            "keywords_checked": target.expected_keywords,
        },
    }


def probe_source(target: SourceTarget, session: Optional[requests.Session] = None) -> Dict[str, Any]:
    if target.probe_kind == "unresolved":
        return {
            "city_key": target.city_key,
            "station_code": target.station_code,
            "source_key": target.source_key,
            "family": target.family,
            "page_type": target.page_type,
            "role": target.role,
            "url": target.url,
            "status": "unresolved",
            "http_status": None,
            "final_url": "",
            "station_match": False,
            "title": "",
            "extracted": {},
            "notes": target.notes,
        }

    owned_session = session is None
    session = session or _build_session()
    try:
        if target.probe_kind == "aviationweather_stationinfo":
            result = _probe_aviationweather_stationinfo(session, target)
        elif target.probe_kind == "aviationweather_metar":
            result = _probe_aviationweather_metar(session, target)
        elif target.probe_kind == "aviationweather_taf":
            result = _probe_aviationweather_taf(session, target)
        elif target.probe_kind == "open_meteo_forecast":
            result = _probe_open_meteo(session, target)
        elif target.probe_kind == "wu_html":
            result = _probe_wu_html(session, target)
        elif target.probe_kind == "checkwx_html":
            result = _probe_checkwx_html(session, target)
        elif target.probe_kind == "html_keywords":
            result = _probe_html_keywords(session, target)
        else:
            result = {
                "status": "unsupported_probe_kind",
                "http_status": None,
                "final_url": "",
                "station_match": False,
                "title": "",
                "extracted": {},
            }
    except Exception as exc:
        result = {
            "status": "error",
            "http_status": None,
            "final_url": target.url,
            "station_match": False,
            "title": "",
            "extracted": {},
            "notes": f"{type(exc).__name__}: {exc}",
        }
    if owned_session:
        session.close()
    return {
        "city_key": target.city_key,
        "station_code": target.station_code,
        "source_key": target.source_key,
        "family": target.family,
        "page_type": target.page_type,
        "role": target.role,
        "url": target.url,
        **result,
    }


def probe_city_sources(city_key: str, local_date: str) -> List[Dict[str, Any]]:
    session = _build_session()
    try:
        targets = build_source_targets(city_key, local_date)
        return [probe_source(target, session=session) for target in targets]
    finally:
        session.close()


def render_markdown_table(rows: List[Dict[str, Any]]) -> str:
    lines = [
        "| city | station | source | status | station_match | key_fields | notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        extracted = dict(row.get("extracted") or {})
        compact = json.dumps(extracted, ensure_ascii=False, sort_keys=True)
        compact = compact.replace("\n", " ")
        if len(compact) > 90:
            compact = compact[:87] + "..."
        notes = str(row.get("notes") or "")
        if len(notes) > 60:
            notes = notes[:57] + "..."
        lines.append(
            "| {city} | {station} | {source} | {status} | {match} | `{fields}` | {notes} |".format(
                city=row.get("city_key", ""),
                station=row.get("station_code", ""),
                source=row.get("source_key", ""),
                status=row.get("status", ""),
                match="yes" if row.get("station_match") else "no",
                fields=compact.replace("|", "/"),
                notes=notes.replace("|", "/"),
            )
        )
    return "\n".join(lines)
