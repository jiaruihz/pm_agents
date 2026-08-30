"""Project-wide causal clock helpers for weather research and runtime.

This module is the single strict parser for clocks that can affect a model,
selection, order, or execution claim.  Descriptive provider clocks remain
separate from collector availability clocks; a timestamp without an explicit
timezone is rejected instead of being silently treated as UTC.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


UTC = timezone.utc


def parse_utc(
    value: Any,
    *,
    field: str = "timestamp",
    allow_none: bool = False,
) -> datetime | None:
    """Parse one timezone-aware timestamp and normalize it to UTC."""

    if value in (None, ""):
        if allow_none:
            return None
        raise ValueError(f"{field} is required")
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(UTC)


def parse_utc_or_none(value: Any, *, field: str = "timestamp") -> datetime | None:
    """Parse an optional non-authoritative clock without accepting naive time.

    Cache/history readers may map malformed input to missing.  Code that
    creates a decision, signal, order, fill, or causal lineage must instead
    call :func:`parse_utc` so malformed clocks fail closed.
    """

    try:
        return parse_utc(value, field=field, allow_none=True)
    except ValueError:
        return None


def utc_text(value: Any, *, field: str = "timestamp", timespec: str = "microseconds") -> str:
    """Return one canonical UTC timestamp with a ``Z`` suffix."""

    parsed = parse_utc(value, field=field)
    assert parsed is not None
    return parsed.isoformat(timespec=timespec).replace("+00:00", "Z")


def validate_clock_order(
    values: Mapping[str, Any],
    ordered_pairs: Iterable[tuple[str, str]],
) -> None:
    """Validate every declared ``earlier <= later`` causal relationship.

    Missing optional clocks are ignored.  Present but malformed clocks fail
    closed.  Only explicit causal pairs are checked: for example a forecast
    valid time is not automatically compared with its collector availability.
    """

    parsed: dict[str, datetime | None] = {}
    for earlier_field, later_field in ordered_pairs:
        for field in (earlier_field, later_field):
            if field not in parsed:
                parsed[field] = parse_utc(
                    values.get(field), field=field, allow_none=True
                )
        earlier = parsed[earlier_field]
        later = parsed[later_field]
        if earlier is not None and later is not None and earlier > later:
            raise ValueError(f"{earlier_field} cannot be after {later_field}")


SOURCE_CLOCK_ORDER = (
    ("source_fetch_start_utc", "source_fetch_end_utc"),
    ("source_fetch_end_utc", "detected_at_utc"),
    ("source_event_ts_utc", "available_at_utc"),
    ("issued_at_utc", "available_at_utc"),
    ("observed_at_utc", "available_at_utc"),
    ("detected_at_utc", "available_at_utc"),
    ("first_seen_at_utc", "available_at_utc"),
    ("available_at_utc", "ingested_at_utc"),
)

DECISION_CLOCK_ORDER = (
    ("event_available_at_utc", "decision_ts_utc"),
    ("available_at_utc", "decision_ts_utc"),
    ("feature_book_available_at_utc", "decision_ts_utc"),
    ("selection_quote_available_at_utc", "decision_ts_utc"),
    ("decision_ts_utc", "order_submitted_at_utc"),
    ("order_submitted_at_utc", "order_acknowledged_at_utc"),
    ("order_acknowledged_at_utc", "fill_received_at_utc"),
    ("decision_ts_utc", "post_decision_book_received_at_utc"),
)

ORDERBOOK_CLOCK_ORDER = (
    ("request_started_at_utc", "response_received_at_utc"),
    ("response_received_at_utc", "parsed_at_utc"),
    ("response_received_at_utc", "available_at_utc"),
)


def local_wall_time_to_utc(
    value: str | datetime,
    *,
    timezone_name: str,
    field: str = "local_wall_time",
    fold: int | None = None,
) -> datetime:
    """Convert a provider-local wall clock through an IANA timezone.

    Fixed ``UTC +/- offset`` arithmetic is deliberately not supported because
    it breaks on DST and was the root cause of the historical Tmin date shift.
    Ambiguous DST folds require an explicit fold and nonexistent wall times
    fail closed.
    """

    if isinstance(value, datetime):
        local = value
    else:
        try:
            local = datetime.fromisoformat(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be ISO-8601 local wall time") from exc
    if local.tzinfo is not None and local.utcoffset() is not None:
        raise ValueError(f"{field} must be a timezone-naive provider wall time")
    try:
        zone = ZoneInfo(str(timezone_name))
    except Exception as exc:
        raise ValueError(f"unknown IANA timezone: {timezone_name!r}") from exc

    valid: dict[int, datetime] = {}
    for candidate_fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=candidate_fold)
        round_trip = aware.astimezone(UTC).astimezone(zone)
        if round_trip.replace(tzinfo=None) == local and round_trip.fold == candidate_fold:
            valid[candidate_fold] = aware
    if not valid:
        raise ValueError(f"{field} is a nonexistent local wall time in {timezone_name}")
    offsets = {aware.utcoffset() for aware in valid.values()}
    if len(offsets) > 1 and fold is None:
        raise ValueError(f"{field} is ambiguous in {timezone_name}; fold is required")
    selected_fold = 0 if fold is None else int(fold)
    if selected_fold not in valid:
        raise ValueError(f"invalid fold={fold!r} for {field} in {timezone_name}")
    return valid[selected_fold].astimezone(UTC)
