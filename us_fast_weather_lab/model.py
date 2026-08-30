"""Content sniffing, METAR normalization, and cross-format event identity."""

from __future__ import annotations

import gzip
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from us_fast_weather_lab.storage import canonical_json, sha256_bytes, stable_id


REPORT_RE = re.compile(
    r"(?P<raw>(?:(?P<kind>METAR|SPECI)\s+)?(?P<station>K[A-Z0-9]{3})\s+"
    r"(?P<time>\d{6}Z)\s+(?P<body>.*?))(?=(?:\s*=)|(?:\n(?:(?:METAR|SPECI)\s+)?K[A-Z0-9]{3}\s+\d{6}Z)|\Z)",
    re.IGNORECASE | re.DOTALL,
)
TEMP_RE = re.compile(r"(?<!\d)(M?\d{2})/(M?\d{2}|//)(?!\d)")
WIND_RE = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(?:G\d{2,3})?KT\b")
ALT_RE = re.compile(r"\bA(\d{4})\b")


def sniff_payload(payload: bytes, content_type: str = "", content_encoding: str = "") -> tuple[str, bytes]:
    raw = payload
    if raw[:2] == b"\x1f\x8b" or "gzip" in content_encoding.lower():
        try:
            raw = gzip.decompress(raw)
        except OSError:
            return "gzip_invalid", payload
    stripped = raw.lstrip()
    lower_type = content_type.lower()
    if raw[:4] == b"BUFR" or "bufr" in lower_type:
        return "bufr", raw
    if stripped.startswith(b"<") or "xml" in lower_type:
        return "iwxxm_or_xml", raw
    if stripped.startswith((b"{", b"[")) or "json" in lower_type or "geo+json" in lower_type:
        return "json", raw
    text = raw.decode("utf-8", "ignore")
    if REPORT_RE.search(text) or "METAR" in text or "SPECI" in text:
        return "tac_or_wmo_text", raw
    return "unknown", raw


def _signed_temperature(token: str) -> float | None:
    if token == "//":
        return None
    return float(-int(token[1:]) if token.startswith("M") else int(token))


def _observation_datetime(group: str, reference_ns: int) -> str:
    ref = datetime.fromtimestamp(reference_ns / 1_000_000_000, tz=timezone.utc)
    day, hour, minute = int(group[:2]), int(group[2:4]), int(group[4:6])
    candidates: list[datetime] = []
    for month_shift in (-1, 0, 1):
        year = ref.year
        month = ref.month + month_shift
        if month < 1:
            year -= 1
            month += 12
        if month > 12:
            year += 1
            month -= 12
        try:
            candidates.append(datetime(year, month, day, hour, minute, tzinfo=timezone.utc))
        except ValueError:
            continue
    if not candidates:
        raise ValueError(f"invalid METAR time group: {group}")
    return min(candidates, key=lambda item: abs((item - ref).total_seconds())).isoformat()


def normalize_raw_report(raw: str) -> str:
    value = raw.replace("\r", " ").replace("\n", " ").strip().rstrip("=").strip()
    return " ".join(value.split())


def metar_event(raw: str, *, reference_ns: int, override: dict[str, Any] | None = None) -> dict[str, Any] | None:
    normalized = normalize_raw_report(raw)
    match = REPORT_RE.search(normalized)
    override = override or {}
    if not match:
        station = str(override.get("station_id") or "").upper()
        observation_time = override.get("observation_time")
        temperature = override.get("air_temperature_c")
        if not station or not observation_time or temperature is None:
            return None
        report_kind = str(override.get("report_kind") or "METAR").upper()
        body = normalized
    else:
        station = match.group("station").upper()
        observation_time = _observation_datetime(match.group("time"), reference_ns)
        report_kind = (
            override.get("force_report_kind")
            or match.group("kind")
            or override.get("report_kind")
            or "METAR"
        ).upper()
        body = match.group("body")
        temp_match = TEMP_RE.search(body)
        temperature = _signed_temperature(temp_match.group(1)) if temp_match else override.get("air_temperature_c")
    if temperature is None:
        return None
    temp_match = TEMP_RE.search(body)
    dewpoint = _signed_temperature(temp_match.group(2)) if temp_match else override.get("dewpoint_c")
    wind_match = WIND_RE.search(body)
    alt_match = ALT_RE.search(body)
    is_correction = bool(re.search(r"\bCOR\b", normalized)) or bool(override.get("is_correction"))
    fields = {
        "station_id": station,
        "observation_time": str(observation_time),
        "report_kind": report_kind,
        "is_correction": is_correction,
        "air_temperature_c": float(temperature),
        "dewpoint_c": dewpoint,
        "wind_direction_deg": None
        if not wind_match or wind_match.group(1) == "VRB"
        else float(wind_match.group(1)),
        "wind_speed_kt": float(wind_match.group(2)) if wind_match else override.get("wind_speed_kt"),
        "visibility_m": override.get("visibility_m"),
        "altimeter_hpa": round(int(alt_match.group(1)) / 100.0 * 33.8638866667, 3)
        if alt_match
        else override.get("altimeter_hpa"),
    }
    raw_report_id = sha256_bytes(normalized.encode("utf-8")) if normalized else None
    semantic_version_id = sha256_bytes(canonical_json(fields).encode("utf-8"))
    event_family_id = stable_id("event_family", [station, str(observation_time), report_kind])
    version_id = stable_id("observation", [event_family_id, semantic_version_id, raw_report_id])
    return {
        "observation_version_id": version_id,
        "event_family_id": event_family_id,
        "raw_report_id": raw_report_id,
        "semantic_version_id": semantic_version_id,
        **fields,
        "correction_marker": "COR" if is_correction else None,
        "normalized_raw_text": normalized or None,
        "normalized_fields_json": canonical_json(fields),
    }


def events_from_tac(payload: bytes, *, reference_ns: int, stations: set[str]) -> list[dict[str, Any]]:
    text = payload.decode("utf-8", "ignore")
    events: list[dict[str, Any]] = []
    for match in REPORT_RE.finditer(text):
        if match.group("station").upper() not in stations:
            continue
        event = metar_event(match.group("raw"), reference_ns=reference_ns)
        if event:
            events.append(event)
    return events


def _json_rows(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for row in value:
            if isinstance(row, dict):
                yield row
        return
    if not isinstance(value, dict):
        return
    if value.get("type") == "publication" and isinstance(value.get("data"), dict):
        yield dict(value["data"])
        return
    if isinstance(value.get("features"), list):
        for feature in value["features"]:
            if isinstance(feature, dict):
                props = dict(feature.get("properties") or {})
                props["geometry"] = feature.get("geometry")
                yield props
        return
    yield value


def events_from_json(payload: bytes, *, reference_ns: int, stations: set[str]) -> list[dict[str, Any]]:
    value = json.loads(payload.decode("utf-8"))
    events: list[dict[str, Any]] = []
    for row in _json_rows(value):
        raw = str(row.get("rawOb") or row.get("raw_text") or row.get("raw") or "")
        station = str(row.get("icaoId") or row.get("icao") or row.get("station") or row.get("station_id") or "").upper()
        if not station and raw:
            found = REPORT_RE.search(raw)
            station = found.group("station").upper() if found else ""
        if station not in stations:
            continue
        observation_time: str | None = None
        obs_value = (
            row.get("obsTime")
            or row.get("observation_time")
            or row.get("report_time")
            or row.get("datetime")
        )
        if isinstance(obs_value, (int, float)):
            observation_time = datetime.fromtimestamp(float(obs_value), timezone.utc).isoformat()
        elif obs_value:
            observation_time = str(obs_value).replace("Z", "+00:00")
        override = {
            "station_id": station,
            "observation_time": observation_time,
            "report_kind": (
                row.get("metarType")
                or row.get("report_type")
                or row.get("report_kind")
                or "METAR"
            ),
            "force_report_kind": row.get("force_report_kind"),
            "air_temperature_c": next(
                (
                    row[key]
                    for key in ("temp", "temperature", "temp_c", "air_temperature_c")
                    if row.get(key) is not None
                ),
                None,
            ),
            "dewpoint_c": next(
                (row[key] for key in ("dewp", "dewpoint", "dewp_c") if row.get(key) is not None),
                None,
            ),
            "wind_speed_kt": row.get("wspd") if row.get("wspd") is not None else row.get("wspd_kt"),
            "visibility_m": row.get("visib_m"),
            "altimeter_hpa": next(
                (row[key] for key in ("altim", "altim_hpa", "qnh_hpa") if row.get(key) is not None),
                None,
            ),
        }
        event = metar_event(raw, reference_ns=reference_ns, override=override)
        if event:
            events.append(event)
    return events
