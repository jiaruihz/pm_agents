"""Shared capture identity and clock contracts for weather source collectors."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from weather_data_feed.information_events import canonical_json_hash
from weather_clock_contract import (
    SOURCE_CLOCK_ORDER,
    parse_utc as parse_strict_utc,
    utc_text as canonical_utc_text,
    validate_clock_order,
)


SOURCE_CAPTURE_LINEAGE_SCHEMA_VERSION = "weather_source_capture_lineage_v1"


def parse_utc(value: Any) -> datetime | None:
    return parse_strict_utc(value, allow_none=True)


def utc_text(value: datetime) -> str:
    return canonical_utc_text(value)


def producer_build_id(repo_root: Path | None = None) -> tuple[str | None, str]:
    """Return the running build identity without inventing one when unavailable."""
    explicit = os.environ.get("WEATHER_DATA_FEED_BUILD_ID") or os.environ.get("GIT_COMMIT")
    if explicit:
        return explicit, "environment"
    root = repo_root or Path(__file__).resolve().parents[1]
    try:
        value = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, "unavailable"
    return (value or None), ("git_head" if value else "unavailable")


def capture_batch_id(
    *,
    producer: str,
    captured_at_utc: str,
    scope: Mapping[str, Any],
    raw_payload_hashes: Iterable[str] = (),
) -> str:
    """Build one stable identity shared by every row from a collector batch."""
    return canonical_json_hash(
        {
            "producer": producer,
            "captured_at_utc": captured_at_utc,
            "scope": dict(scope),
            "raw_payload_hashes": sorted({str(value) for value in raw_payload_hashes if value}),
        }
    )


def build_source_capture_lineage(
    *,
    producer: str,
    producer_build: str | None,
    capture_id: str,
    batch_capture_id: str,
    raw_payload_hash: str | None,
    source_event_ts_utc: str | None = None,
    issued_at_utc: str | None = None,
    source_fetch_start_utc: str | None = None,
    source_fetch_end_utc: str | None = None,
    detected_at_utc: str | None = None,
    first_seen_at_utc: str | None = None,
    available_at_utc: str | None = None,
    ingested_at_utc: str | None = None,
    lineage_status: str,
) -> dict[str, Any]:
    """Validate and return the common source-capture header.

    Provider event/issue clocks are descriptive and never substitute for the
    collector's first-seen or available clocks.
    """
    if not producer or not capture_id or not batch_capture_id or not lineage_status:
        raise ValueError("producer, capture_id, batch_capture_id, and lineage_status are required")
    validate_clock_order(
        {
            "source_event_ts_utc": source_event_ts_utc,
            "issued_at_utc": issued_at_utc,
            "source_fetch_start_utc": source_fetch_start_utc,
            "source_fetch_end_utc": source_fetch_end_utc,
            "detected_at_utc": detected_at_utc,
            "first_seen_at_utc": first_seen_at_utc,
            "available_at_utc": available_at_utc,
            "ingested_at_utc": ingested_at_utc,
        },
        SOURCE_CLOCK_ORDER,
    )
    return {
        "source_capture_lineage_schema_version": SOURCE_CAPTURE_LINEAGE_SCHEMA_VERSION,
        "producer": producer,
        "producer_build_id": producer_build,
        "producer_build_lineage_status": "known" if producer_build else "unavailable",
        "capture_id": capture_id,
        "batch_capture_id": batch_capture_id,
        "raw_payload_hash": raw_payload_hash,
        "source_event_ts_utc": source_event_ts_utc,
        "issued_at_utc": issued_at_utc,
        "source_fetch_start_utc": source_fetch_start_utc,
        "source_fetch_end_utc": source_fetch_end_utc,
        "detected_at_utc": detected_at_utc,
        "first_seen_at_utc": first_seen_at_utc,
        "available_at_utc": available_at_utc,
        "ingested_at_utc": ingested_at_utc,
        "source_lineage_status": lineage_status,
    }
