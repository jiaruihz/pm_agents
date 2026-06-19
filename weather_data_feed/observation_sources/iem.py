from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


IEM_ASOS_API = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
DEFAULT_IEM_REPORT_TYPES = ("1", "2", "3", "4")


def _coerce_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def iem_request_dates(tz: ZoneInfo, local_date: Any) -> tuple[datetime, datetime]:
    local_start = datetime.combine(_coerce_date(local_date), datetime.min.time(), tzinfo=tz)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def build_iem_asos_params(
    icao: str,
    start_utc: datetime,
    end_utc: datetime,
    *,
    columns: tuple[str, ...] = ("tmpc",),
    report_types: tuple[str, ...] = DEFAULT_IEM_REPORT_TYPES,
) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = [("station", str(icao).upper())]
    for column in columns:
        params.append(("data", column))
    params.extend(
        [
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
        ]
    )
    for report_type in report_types:
        params.append(("report_type", report_type))
    return params


def build_iem_local_day_params(
    icao: str,
    tz: ZoneInfo,
    local_date: Any,
    *,
    columns: tuple[str, ...] = ("tmpc",),
    extra_end_days: int = 1,
) -> list[tuple[str, str]]:
    start_utc, end_utc = iem_request_dates(tz, local_date)
    return build_iem_asos_params(icao, start_utc, end_utc + timedelta(days=extra_end_days), columns=columns)


def _csv_rows(text: str) -> list[dict[str, str]]:
    rows = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(rows))))


def parse_iem_asos_records(
    text: str,
    tz: ZoneInfo,
    local_date: Any,
    *,
    temp_column: str = "tmpc",
) -> list[tuple[datetime, float, dict[str, str]]]:
    records: list[tuple[datetime, float, dict[str, str]]] = []
    for row in _csv_rows(text):
        raw_temp = row.get(temp_column)
        raw_ts = row.get("valid")
        if raw_temp in {None, "", "M"} or not raw_ts:
            continue
        try:
            dt = datetime.fromisoformat(str(raw_ts).replace(" ", "T")).replace(tzinfo=timezone.utc)
            temp = float(raw_temp)
        except ValueError:
            continue
        if dt.astimezone(tz).date().isoformat() == str(local_date):
            records.append((dt, temp, row))
    return sorted(records, key=lambda row: row[0])


def parse_iem_asos_temperature_obs(text: str, tz: ZoneInfo, local_date: Any) -> list[tuple[datetime, float]]:
    return [(dt, temp) for dt, temp, _row in parse_iem_asos_records(text, tz, local_date)]
