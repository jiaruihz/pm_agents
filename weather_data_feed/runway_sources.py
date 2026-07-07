"""Runway-level airport observation sources.

Runway rows are an enhancement layer for airport microclimate research. They
are not settlement truth and should be joined to METAR/WU/official source rows
by city, station, and observation time before being used in strategy research.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any

import httpx


AMSC_AWOS_API = "https://www.amsc.net.cn/gateway/api/saas/rest/amc/AwosController/getWindPlate"
AMOS_API = "https://global.amo.go.kr/amosobsnew/AmosRealTimeImage.do"

AMSC_AWOS_AIRPORTS: dict[str, dict[str, str]] = {
    "Beijing": {"station": "ZBAA", "label": "Beijing Capital", "configured_runway_target": "01"},
    "Shanghai": {"station": "ZSPD", "label": "Shanghai Pudong", "configured_runway_target": "35R"},
    "Guangzhou": {"station": "ZGGG", "label": "Guangzhou Baiyun", "configured_runway_target": "02L"},
    "Chengdu": {"station": "ZUUU", "label": "Chengdu Shuangliu", "configured_runway_target": "02L"},
    "Chongqing": {"station": "ZUCK", "label": "Chongqing Jiangbei", "configured_runway_target": "02L"},
    "Wuhan": {"station": "ZHHH", "label": "Wuhan Tianhe", "configured_runway_target": "04"},
    "Qingdao": {"station": "ZSQD", "label": "Qingdao Jiaodong", "configured_runway_target": "34"},
}

AMOS_AIRPORTS: dict[str, dict[str, str]] = {
    "Seoul": {"station": "RKSI", "stn_id": "113", "label": "Incheon Intl"},
    "Busan": {"station": "RKPK", "stn_id": "153", "label": "Gimhae Intl"},
}


@dataclass(frozen=True)
class RunwayFetchSettings:
    timeout_sec: float = 8.0
    proxy_candidates: tuple[str | None, ...] = (None,)
    user_agent: str = "pm-agent-weather-data-feed-runway/1.0"
    amsc_session_id: str = ""
    amsc_cookie: str = ""
    verify_tls: bool = True


@dataclass(frozen=True)
class RunwayFetchResult:
    source_key: str
    status: str
    fetched_at_utc: str
    latency_ms: float
    records: tuple[dict[str, Any], ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class RunwayFetchError(RuntimeError):
    pass


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _settings(settings: RunwayFetchSettings | None) -> RunwayFetchSettings:
    env_session_id = os.environ.get("WEATHER_DATA_FEED_AMSC_SESSION_ID", "").strip() or os.environ.get("POLYWEATHER_AMSC_SESSION_ID", "").strip()
    env_cookie = os.environ.get("WEATHER_DATA_FEED_AMSC_COOKIE", "").strip() or os.environ.get("POLYWEATHER_AMSC_COOKIE", "").strip()
    env_verify_tls = os.environ.get("WEATHER_DATA_FEED_RUNWAY_VERIFY_TLS", "").strip().lower()
    verify_tls = env_verify_tls not in {"0", "false", "no"}
    if settings is not None:
        should_return_settings = (
            (settings.amsc_session_id or settings.amsc_cookie or (not env_session_id and not env_cookie))
            and settings.verify_tls == verify_tls
        )
        if should_return_settings:
            return settings
        return RunwayFetchSettings(
            timeout_sec=settings.timeout_sec,
            proxy_candidates=settings.proxy_candidates,
            user_agent=settings.user_agent,
            amsc_session_id=env_session_id or settings.amsc_session_id,
            amsc_cookie=env_cookie or settings.amsc_cookie,
            verify_tls=verify_tls,
        )
    return RunwayFetchSettings(
        amsc_session_id=env_session_id,
        amsc_cookie=env_cookie,
        verify_tls=verify_tls,
    )


def _http_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    settings: RunwayFetchSettings | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    cfg = _settings(settings)
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
                verify=cfg.verify_tls,
            )
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001
            last_error = f"{proxy or 'direct'}: {type(exc).__name__}: {exc}"
    raise RunwayFetchError(f"fetch failed {url}: {last_error}")


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "--", "null", "None", "M"}:
        return None
    try:
        out = float(text)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and -80.0 < out < 80.0 else None


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _normalize_runway(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _split_runway_pair(label: str) -> tuple[str, str]:
    parts = [_normalize_runway(part) for part in str(label or "").split("/") if part.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    runway = _normalize_runway(label) or "--"
    return runway, runway


def _target_endpoint_temp(
    runway_pair: tuple[str, str],
    target: str,
    *,
    tdz_temp_c: float | None,
    end_temp_c: float | None,
) -> tuple[float | None, str]:
    normalized_target = _normalize_runway(target)
    first = _normalize_runway(runway_pair[0])
    second = _normalize_runway(runway_pair[1])
    if normalized_target == first:
        return (tdz_temp_c if tdz_temp_c is not None else end_temp_c), "tdz" if tdz_temp_c is not None else "end_fallback"
    if normalized_target == second:
        return (end_temp_c if end_temp_c is not None else tdz_temp_c), "end" if end_temp_c is not None else "tdz_fallback"
    return None, ""


def _wind_dir(*candidates: Any) -> int | None:
    for value in candidates:
        parsed = safe_float(value)
        if parsed is not None and 0 <= parsed <= 360:
            return int(round(parsed))
    return None


def _parse_rvr(value: Any) -> int | None:
    text = str(value or "").strip().upper()
    if not text or text in {"--", "-", "NULL", "NONE"}:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    parsed = float(match.group(0))
    return int(round(parsed)) if math.isfinite(parsed) else None


def parse_amsc_wind_plate_payload(
    payload: dict[str, Any],
    *,
    city: str,
    station: str,
    target_date: str = "",
) -> list[dict[str, Any]]:
    """Parse AMSC getWindPlate runway-point air-temperature payload."""

    if not isinstance(payload, dict) or payload.get("errCode") is not None:
        return []
    if payload.get("code") not in (None, 200, "200"):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    meta = AMSC_AWOS_AIRPORTS.get(city, {"station": station, "label": station, "configured_runway_target": ""})
    configured_target = meta.get("configured_runway_target", "")
    rows: list[dict[str, Any]] = []
    for key, raw_row in data.items():
        if not isinstance(raw_row, dict):
            continue
        runway_label = str(raw_row.get("RNO") or key or "").strip()
        if not runway_label:
            continue
        tdz_temp_c = safe_float(raw_row.get("TDZ_TEMP"))
        mid_temp_c = safe_float(raw_row.get("MID_TEMP"))
        end_temp_c = safe_float(raw_row.get("END_TEMP"))
        point_values = [value for value in (tdz_temp_c, mid_temp_c, end_temp_c) if value is not None]
        if not point_values:
            continue
        obs_dt = parse_dt(raw_row.get("OTIME"))
        runway_pair = _split_runway_pair(runway_label)
        target_temp_c, target_position = _target_endpoint_temp(
            runway_pair,
            configured_target,
            tdz_temp_c=tdz_temp_c,
            end_temp_c=end_temp_c,
        )
        point_temp_c = target_temp_c if target_temp_c is not None else max(point_values)
        wind_speed = safe_float(raw_row.get("TDZ_WIND_F10") or raw_row.get("END_WIND_F10"))
        raw_rvr = raw_row.get("TDZ_RVR_1A") or raw_row.get("END_RVR_1A") or raw_row.get("TDZ_RVR_10A") or raw_row.get("END_RVR_10A")
        raw_mor = raw_row.get("TDZ_MOR_1A") or raw_row.get("END_MOR_1A") or raw_row.get("TDZ_MOR_10A") or raw_row.get("END_MOR_10A")
        rows.append(
            {
                "schema_version": "weather_runway_observation_v1",
                "source": "amsc_awos",
                "city": city,
                "station": station,
                "target_date": target_date,
                "runway": f"{runway_pair[0]}/{runway_pair[1]}",
                "observation_time_utc": obs_dt.isoformat() if obs_dt else "",
                "tdz_temp_c": tdz_temp_c,
                "mid_temp_c": mid_temp_c,
                "end_temp_c": end_temp_c,
                "point_temp_c": point_temp_c,
                "configured_runway_target": configured_target,
                "is_configured_runway_target": target_temp_c is not None,
                "configured_runway_position": target_position,
                "runway_temp_min_c": min(point_values),
                "runway_temp_max_c": max(point_values),
                "runway_temp_avg_c": round(sum(point_values) / len(point_values), 3),
                "wind_dir": _wind_dir(raw_row.get("TDZ_WIND_D10"), raw_row.get("END_WIND_D10")),
                "wind_speed": wind_speed,
                "rvr": _parse_rvr(raw_rvr),
                "mor": safe_float(raw_mor),
                "humidity": safe_float(raw_row.get("TDZ_HUMID") or raw_row.get("END_HUMID") or raw_row.get("MID_HUMID")),
                "raw_metar": str(raw_row.get("METAR") or ""),
                "raw_payload_hash": stable_hash(raw_row),
                "source_note": "AMSC AWOS runway-point air temperature; not pavement temperature",
            }
        )
    return sorted(rows, key=lambda row: (str(row.get("observation_time_utc")), str(row.get("runway"))))


def _amsc_headers(settings: RunwayFetchSettings | None = None) -> dict[str, str]:
    cfg = _settings(settings)
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": os.environ.get("AMSC_AWOS_REFERER", "https://www.amsc.net.cn/"),
    }
    if cfg.amsc_session_id:
        headers["sessionId"] = cfg.amsc_session_id
        headers["app"] = "AMS"
    elif cfg.amsc_cookie:
        headers["Cookie"] = cfg.amsc_cookie
    return headers


def fetch_amsc_awos_runway(
    city: str,
    *,
    settings: RunwayFetchSettings | None = None,
    target_date: str = "",
) -> RunwayFetchResult:
    meta = AMSC_AWOS_AIRPORTS.get(city)
    if not meta:
        now = datetime.now(timezone.utc)
        return RunwayFetchResult("amsc_awos", "unsupported_city", now.isoformat(), 0.0)
    station = meta["station"]
    fetch_start = datetime.now(timezone.utc)
    payload = _http_get(
        os.environ.get("AMSC_AWOS_BASE_URL", "").strip() or AMSC_AWOS_API,
        params={"cccc": station},
        settings=settings,
        headers=_amsc_headers(settings),
    ).json()
    fetch_end = datetime.now(timezone.utc)
    err_code = payload.get("errCode") if isinstance(payload, dict) else None
    if err_code is not None:
        err_msg = str(payload.get("errMsg") or payload.get("message") or "")
        status = "auth_failed" if "登录" in err_msg or "login" in err_msg.lower() or err_code in {-12013, "-12013"} else "source_error"
        return RunwayFetchResult(
            source_key="amsc_awos",
            status=status,
            fetched_at_utc=fetch_end.isoformat(),
            latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3),
            records=(),
            payload={"city": city, "station": station, "error_payload": payload},
            error=f"AMSC AWOS {status}: errCode={err_code} errMsg={err_msg}",
            metadata={"city": city, "station": station, "raw_payload_hash": stable_hash(payload), "record_count": 0},
        )
    records = parse_amsc_wind_plate_payload(payload, city=city, station=station, target_date=target_date)
    for row in records:
        row["source_fetch_start_utc"] = fetch_start.isoformat()
        row["source_fetch_end_utc"] = fetch_end.isoformat()
        row["local_detect_ts_utc"] = fetch_end.isoformat()
    return RunwayFetchResult(
        source_key="amsc_awos",
        status="ok" if records else "empty",
        fetched_at_utc=fetch_end.isoformat(),
        latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3),
        records=tuple(records),
        payload={"city": city, "station": station, "records": records},
        metadata={"city": city, "station": station, "raw_payload_hash": stable_hash(payload), "record_count": len(records)},
    )


def _html_lines(text: str) -> list[str]:
    normalized = unescape(str(text or ""))
    normalized = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", normalized)
    normalized = re.sub(r"(?i)</\s*(?:td|th)\s*>", "\n", normalized)
    normalized = re.sub(r"(?i)</\s*(?:tr|div|p|li|h\d|table)\s*>", "\n", normalized)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    normalized = normalized.replace("\xa0", " ")
    return [re.sub(r"\s+", " ", line).strip() for line in normalized.splitlines() if line.strip()]


def _is_runway_token(value: str) -> bool:
    text = str(value or "").strip()
    return bool(re.match(r"^\d{2}[LRC]?$", text, re.I) or re.match(r"^[NS]\s+[LR]$", text, re.I))


def _extract_amos_observation_time(text: str, station: str) -> tuple[str, str]:
    station_pattern = re.escape(str(station or "").strip().upper())
    patterns = [
        rf"\({station_pattern}\)\s*(\d{{4}})년\s*(\d{{1,2}})월\s*(\d{{1,2}})일\s*(\d{{1,2}}):(\d{{2}})\s*KST",
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*(\d{1,2}):(\d{2})\s*KST",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if not match:
            continue
        try:
            year, month, day, hour, minute = [int(part) for part in match.groups()[:5]]
            local_dt = datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=9)))
            return local_dt.astimezone(timezone.utc).isoformat(), local_dt.strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            continue
    return "", ""


def _parse_amos_metar_temp(raw_metar: str) -> float | None:
    match = re.search(r"\b(M?\d{2})/(M?\d{2}|//)\b", raw_metar)
    if not match:
        return None
    token = match.group(1)
    return float(-int(token[1:]) if token.startswith("M") else int(token))


def parse_amos_runway_html(
    text: str,
    *,
    city: str,
    station: str,
    target_date: str = "",
) -> list[dict[str, Any]]:
    lines = _html_lines(text)
    obs_time_utc, obs_time_local = _extract_amos_observation_time(text, station)
    metar_match = re.search(rf"METAR\s+{re.escape(station)}\s.*?=", text or "", re.DOTALL)
    raw_metar = re.sub(r"\s+", " ", metar_match.group(0)).strip() if metar_match else ""
    metar_temp_c = _parse_amos_metar_temp(raw_metar)
    runway_rows: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        token = lines[i].strip()
        if not (_is_runway_token(token) and i + 3 < len(lines) and lines[i + 1].upper() == "AVG"):
            i += 1
            continue
        row: dict[str, Any] = {"runway": _normalize_runway(token)}
        i += 4
        while i < len(lines):
            label = lines[i].strip()
            upper = label.upper()
            if _is_runway_token(label) and i + 3 < len(lines) and lines[i + 1].upper() == "AVG":
                break
            if upper.startswith("TEMP") and i + 1 < len(lines):
                row["temp_c"] = safe_float(lines[i + 1])
                i += 2
                continue
            if upper.startswith("DEW") and i + 1 < len(lines):
                row["dewpoint_c"] = safe_float(lines[i + 1])
                i += 2
                continue
            if upper == "WS" and i + 3 < len(lines):
                row["wind_speed"] = safe_float(lines[i + 1])
                i += 4
                continue
            if upper == "WD" and i + 3 < len(lines):
                row["wind_dir"] = _wind_dir(lines[i + 1])
                i += 4
                continue
            if upper == "RVR" and i + 1 < len(lines):
                row["rvr"] = _parse_rvr(lines[i + 1])
                i += 2
                continue
            if upper == "MOR" and i + 1 < len(lines):
                row["mor"] = safe_float(lines[i + 1])
                i += 2
                continue
            i += 1
        runway_rows.append(row)
    records: list[dict[str, Any]] = []
    for idx in range(0, len(runway_rows) - 1, 2):
        first = runway_rows[idx]
        second = runway_rows[idx + 1]
        runway = f"{_normalize_runway(first.get('runway'))}/{_normalize_runway(second.get('runway'))}"
        temps = [safe_float(first.get("temp_c")), safe_float(second.get("temp_c"))]
        temps = [value for value in temps if value is not None]
        if not temps:
            continue
        records.append(
            {
                "schema_version": "weather_runway_observation_v1",
                "source": "amos",
                "city": city,
                "station": station,
                "target_date": target_date,
                "runway": runway,
                "observation_time_utc": obs_time_utc,
                "observation_time_local": obs_time_local,
                "point_temp_c": round(sum(temps) / len(temps), 3),
                "target_runway_temp_c": round(sum(temps) / len(temps), 3),
                "runway_temp_min_c": min(temps),
                "runway_temp_max_c": max(temps),
                "runway_temp_avg_c": round(sum(temps) / len(temps), 3),
                "dewpoint_c": first.get("dewpoint_c") or second.get("dewpoint_c"),
                "wind_dir": first.get("wind_dir") or second.get("wind_dir"),
                "wind_speed": first.get("wind_speed") or second.get("wind_speed"),
                "rvr": first.get("rvr") or second.get("rvr"),
                "mor": first.get("mor") or second.get("mor"),
                "raw_metar": raw_metar,
                "metar_temp_c": metar_temp_c,
                "raw_payload_hash": stable_hash({"first": first, "second": second, "raw_metar": raw_metar}),
                "source_note": "AMOS runway-level air temperature; not pavement temperature",
            }
        )
    return records


def fetch_amos_runway(
    city: str,
    *,
    settings: RunwayFetchSettings | None = None,
    target_date: str = "",
) -> RunwayFetchResult:
    meta = AMOS_AIRPORTS.get(city)
    if not meta:
        now = datetime.now(timezone.utc)
        return RunwayFetchResult("amos", "unsupported_city", now.isoformat(), 0.0)
    station = meta["station"]
    fetch_start = datetime.now(timezone.utc)
    params = {"stnId": meta.get("stn_id", "")} if meta.get("stn_id") else None
    text = _http_get(os.environ.get("AMOS_BASE_URL", "").strip() or AMOS_API, params=params, settings=settings).text
    fetch_end = datetime.now(timezone.utc)
    records = parse_amos_runway_html(text, city=city, station=station, target_date=target_date)
    for row in records:
        row["source_fetch_start_utc"] = fetch_start.isoformat()
        row["source_fetch_end_utc"] = fetch_end.isoformat()
        row["local_detect_ts_utc"] = fetch_end.isoformat()
    return RunwayFetchResult(
        source_key="amos",
        status="ok" if records else "empty",
        fetched_at_utc=fetch_end.isoformat(),
        latency_ms=round((fetch_end - fetch_start).total_seconds() * 1000.0, 3),
        records=tuple(records),
        payload={"city": city, "station": station, "records": records},
        metadata={"city": city, "station": station, "raw_payload_hash": stable_hash(text), "record_count": len(records)},
    )


def supported_runway_cities(source: str | None = None) -> dict[str, dict[str, str]]:
    if source == "amsc_awos":
        return dict(AMSC_AWOS_AIRPORTS)
    if source == "amos":
        return dict(AMOS_AIRPORTS)
    return {**AMSC_AWOS_AIRPORTS, **AMOS_AIRPORTS}
