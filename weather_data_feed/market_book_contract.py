"""Clock and lineage contract for Polymarket order-book captures.

The exchange clock describes the book returned by CLOB.  The collector clocks
describe when that payload could actually become visible to local strategy
code.  They are intentionally separate and must never be reconstructed from a
snapshot filename or a collection-cycle start timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from weather_data_feed.information_events import canonical_json_hash


ORDERBOOK_CAPTURE_SCHEMA_VERSION = "weather_orderbook_capture_v3"
LEGACY_MISSING_RESPONSE_CLOCK = "legacy_missing_response_clock"
COLLECTOR_EXACT_RESPONSE_CLOCK = "collector_exact_response_clock"


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def exchange_timestamp_utc(value: Any) -> str | None:
    """Normalize the CLOB millisecond epoch without inventing missing values."""

    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    # CLOB currently emits epoch milliseconds.  Accept seconds as a defensive
    # parser rule, but preserve the raw value alongside the normalized clock.
    seconds = numeric / 1000.0 if numeric > 10_000_000_000 else numeric
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def materialize_orderbook_capture(
    *,
    token_id: str,
    raw_book: Mapping[str, Any],
    request_started_at_utc: str,
    response_received_at_utc: str,
    parsed_at_utc: str,
    request_batch_capture_id: str,
) -> dict[str, Any]:
    """Return the strict clocks/identity for one book in a batch response."""

    raw_exchange_timestamp = raw_book.get("timestamp")
    raw_payload_hash = canonical_json_hash(dict(raw_book))
    exchange_hash = raw_book.get("hash")
    capture_id = canonical_json_hash(
        {
            "token_id": str(token_id),
            "request_batch_capture_id": request_batch_capture_id,
            "response_received_at_utc": response_received_at_utc,
            "raw_payload_hash": raw_payload_hash,
        }
    )
    try:
        request_duration_ms = round(
            (
                datetime.fromisoformat(response_received_at_utc.replace("Z", "+00:00"))
                - datetime.fromisoformat(request_started_at_utc.replace("Z", "+00:00"))
            ).total_seconds()
            * 1000.0,
            3,
        )
    except ValueError:
        request_duration_ms = None
    return {
        "schema_version": ORDERBOOK_CAPTURE_SCHEMA_VERSION,
        "token_id": str(token_id),
        "exchange_book_ts_raw": raw_exchange_timestamp,
        "exchange_book_ts_utc": exchange_timestamp_utc(raw_exchange_timestamp),
        "exchange_book_hash": str(exchange_hash) if exchange_hash not in (None, "") else None,
        "request_started_at_utc": request_started_at_utc,
        "response_received_at_utc": response_received_at_utc,
        "parsed_at_utc": parsed_at_utc,
        "request_duration_ms": request_duration_ms,
        # Compatibility alias.  In v3 it means response received, never request
        # start.  Historical v1/v2 rows retain their original ambiguous value.
        "fetched_at_utc": response_received_at_utc,
        "detected_at_utc": response_received_at_utc,
        "first_seen_at_utc": response_received_at_utc,
        "available_at_utc": response_received_at_utc,
        "request_batch_capture_id": request_batch_capture_id,
        "book_capture_id": capture_id,
        "raw_payload_hash": raw_payload_hash,
        "clock_lineage_status": COLLECTOR_EXACT_RESPONSE_CLOCK,
        "event_time_pit_scorable": True,
    }


def classify_orderbook_clock(row: Mapping[str, Any]) -> dict[str, Any]:
    """Classify new and historical rows without filling historical clocks."""

    request_started = row.get("request_started_at_utc")
    response_received = row.get("response_received_at_utc")
    parsed = row.get("parsed_at_utc")
    if request_started and response_received and parsed:
        return {
            "request_started_at_utc": str(request_started),
            "response_received_at_utc": str(response_received),
            "parsed_at_utc": str(parsed),
            "clock_lineage_status": COLLECTOR_EXACT_RESPONSE_CLOCK,
            "event_time_pit_scorable": True,
            "clock_lineage_blockers": [],
        }
    return {
        "request_started_at_utc": None,
        "response_received_at_utc": None,
        "parsed_at_utc": None,
        "clock_lineage_status": LEGACY_MISSING_RESPONSE_CLOCK,
        "event_time_pit_scorable": False,
        "clock_lineage_blockers": [
            "request_started_at_utc_missing",
            "response_received_at_utc_missing",
            "parsed_at_utc_missing",
        ],
    }
