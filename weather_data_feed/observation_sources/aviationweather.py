from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from weather_data_feed.observation_sources.metar import parse_metar_report_time
from weather_clock_contract import parse_utc_or_none


def parse_dt(value: str | None) -> datetime | None:
    return parse_utc_or_none(value, field="aviationweather_timestamp")


def parse_aviationweather_records(
    data: list[dict[str, Any]],
    tz: ZoneInfo,
    local_date: Any,
) -> list[tuple[datetime, float, dict[str, Any]]]:
    records: list[tuple[datetime, float, dict[str, Any]]] = []
    for item in data:
        temp = item.get("temp")
        report_time = item.get("reportTime") or item.get("obsTime")
        if temp is None or not report_time:
            continue
        report_dt = parse_dt(str(report_time))
        if report_dt is None:
            continue
        raw_ob = str(item.get("rawOb") or item.get("raw_text") or "")
        obs_dt = parse_metar_report_time(raw_ob, report_dt) if raw_ob else report_dt
        if obs_dt is None:
            continue
        if obs_dt.astimezone(tz).date().isoformat() != str(local_date):
            continue
        records.append((obs_dt, float(temp), item))
    return sorted(records, key=lambda row: row[0])


def parse_awc_cache_csv_records(
    text: str,
    icao: str,
    tz: ZoneInfo,
    local_date: Any,
) -> list[tuple[datetime, float, dict[str, str]]]:
    records: list[tuple[datetime, float, dict[str, str]]] = []
    station = str(icao).upper()
    for row in csv.DictReader(io.StringIO(text)):
        row_station = (row.get("station_id") or row.get("station") or "").upper()
        if row_station != station:
            continue
        temp_raw = row.get("temp_c") or row.get("temp")
        report_time = row.get("observation_time") or row.get("valid") or row.get("reportTime")
        if not temp_raw or not report_time:
            continue
        report_dt = parse_dt(str(report_time))
        if report_dt is None:
            continue
        raw_text = row.get("raw_text") or row.get("rawOb") or ""
        obs_dt = parse_metar_report_time(raw_text, report_dt) if raw_text else report_dt
        if obs_dt is None:
            continue
        if obs_dt.astimezone(tz).date().isoformat() != str(local_date):
            continue
        try:
            temp = float(temp_raw)
        except ValueError:
            continue
        records.append((obs_dt, temp, row))
    return sorted(records, key=lambda row: row[0])
