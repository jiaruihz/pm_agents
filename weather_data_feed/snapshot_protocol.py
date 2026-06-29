from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping

from weather_data_feed.city_calendar import city_local_date
from weather_data_feed.models import MarketSnapshotRecord


SNAPSHOT_SCHEMA_VERSION = "weather_data_feed_snapshot_v1"

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_SLUG_EVENT_DATE_RE = re.compile(r"(?:^|-)on-([a-z]+)-(\d{1,2})-(\d{4})(?:$|-)")
_QUESTION_EVENT_DATE_RE = re.compile(r"\bon\s+([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?\b")

REQUIRED_SNAPSHOT_FIELDS = (
    "city",
    "target_date",
    "market_local_date",
    "city_local_date_at_snapshot",
    "snapshot_ts_utc",
)


def first_present(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _date_from_parts(month_text: str, day_text: str, year_text: str | int | None) -> str:
    month = _MONTHS.get(str(month_text or "").strip().lower())
    if month is None:
        return ""
    try:
        year = int(year_text) if year_text is not None and str(year_text).strip() else 0
        day = int(day_text)
    except (TypeError, ValueError):
        return ""
    if year <= 0:
        return ""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def parse_market_event_date(row: Mapping[str, Any], *, target_year: str | int | None = None) -> str:
    """Parse the market's date from slug/question text when present."""
    slug = first_present(row, "event_slug", "market_slug", "slug")
    match = _SLUG_EVENT_DATE_RE.search(slug.lower())
    if match:
        return _date_from_parts(match.group(1), match.group(2), match.group(3))

    question = first_present(row, "question")
    match = _QUESTION_EVENT_DATE_RE.search(question)
    if not match:
        return ""
    year = match.group(3) or target_year
    return _date_from_parts(match.group(1), match.group(2), year)


def normalize_snapshot_record(row: Mapping[str, Any], *, snapshot_ts_utc: str | datetime | None = None) -> dict[str, Any]:
    city = first_present(row, "city")
    target_date = first_present(row, "target_date", "event_date")
    snapshot_ts = first_present(row, "snapshot_ts_utc", "snapshot_fetched_at_utc", "ts_utc")
    if snapshot_ts_utc is not None:
        snapshot_ts = snapshot_ts_utc.isoformat() if isinstance(snapshot_ts_utc, datetime) else str(snapshot_ts_utc)

    market_local_date = first_present(row, "market_local_date", "local_event_date", "target_local_date")
    if not market_local_date:
        market_local_date = target_date

    local_at_snapshot = first_present(row, "city_local_date_at_snapshot", "city_local_date")
    if not local_at_snapshot and city and snapshot_ts:
        local_at_snapshot = city_local_date(city, snapshot_ts).isoformat()

    normalized = {
        **dict(row),
        "schema_version": first_present(row, "schema_version") or SNAPSHOT_SCHEMA_VERSION,
        "city": city,
        "target_date": target_date,
        "market_local_date": market_local_date,
        "city_local_date_at_snapshot": local_at_snapshot,
        "snapshot_ts_utc": snapshot_ts,
        "market_id": first_present(row, "market_id"),
        "condition_id": first_present(row, "condition_id"),
        "token_id": first_present(row, "token_id", "clob_token_id"),
        "bracket": first_present(row, "bracket", "outcome", "label"),
        "unit": first_present(row, "unit"),
    }
    validate_snapshot_record(normalized)
    return normalized


def validate_snapshot_record(row: Mapping[str, Any]) -> None:
    missing = [field for field in REQUIRED_SNAPSHOT_FIELDS if not first_present(row, field)]
    if missing:
        raise ValueError(f"snapshot record missing required field(s): {', '.join(missing)}")


def market_snapshot_record(row: Mapping[str, Any], *, snapshot_ts_utc: str | datetime | None = None) -> MarketSnapshotRecord:
    normalized = normalize_snapshot_record(row, snapshot_ts_utc=snapshot_ts_utc)
    return MarketSnapshotRecord(
        city=normalized["city"],
        target_date=normalized["target_date"],
        market_local_date=normalized["market_local_date"],
        city_local_date_at_snapshot=normalized["city_local_date_at_snapshot"],
        snapshot_ts_utc=normalized["snapshot_ts_utc"],
        market_id=normalized.get("market_id", ""),
        condition_id=normalized.get("condition_id", ""),
        token_id=normalized.get("token_id", ""),
        bracket=normalized.get("bracket", ""),
        unit=normalized.get("unit", ""),
    )
