from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from weather_clock_contract import local_wall_time_to_utc


METAR_TEMP_RE = re.compile(r"\s(M?\d{2})/(M?\d{2}|//)")
METAR_REPORT_TIME_RE = re.compile(r"\b[A-Z0-9]{4}\s+(\d{2})(\d{2})(\d{2})Z\b")


def parse_metar_temp_c(raw: str) -> float | None:
    match = METAR_TEMP_RE.search(f" {raw}")
    if not match:
        return None
    token = match.group(1)
    return float(-int(token[1:]) if token.startswith("M") else int(token))


def parse_tgftp_header_time(text: str) -> datetime | None:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    try:
        local = datetime.strptime(first, "%Y/%m/%d %H:%M")
        return local_wall_time_to_utc(
            local, timezone_name="Etc/UTC", field="tgftp_header_utc_wall_time"
        )
    except ValueError:
        return None


def parse_metar_report_time(raw: str, reference_utc: datetime | None) -> datetime | None:
    match = METAR_REPORT_TIME_RE.search(raw)
    if not match or reference_utc is None:
        return reference_utc
    day, hour, minute = (int(match.group(i)) for i in (1, 2, 3))
    candidate = reference_utc.replace(day=1, hour=hour, minute=minute, second=0, microsecond=0)
    try:
        candidate = candidate.replace(day=day)
    except ValueError:
        return reference_utc
    if candidate - reference_utc > timedelta(days=15):
        prev_month = 12 if candidate.month == 1 else candidate.month - 1
        year = candidate.year - 1 if candidate.month == 1 else candidate.year
        try:
            candidate = candidate.replace(year=year, month=prev_month)
        except ValueError:
            return reference_utc
    elif reference_utc - candidate > timedelta(days=15):
        next_month = 1 if candidate.month == 12 else candidate.month + 1
        year = candidate.year + 1 if candidate.month == 12 else candidate.year
        try:
            candidate = candidate.replace(year=year, month=next_month)
        except ValueError:
            return reference_utc
    return candidate
