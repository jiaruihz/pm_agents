"""Shared capture identity and clock contracts for weather source collectors."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from weather_data_feed.information_events import canonical_json_hash


SOURCE_CAPTURE_LINEAGE_SCHEMA_VERSION = "weather_source_capture_lineage_v1"


def parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


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
    start = parse_utc(source_fetch_start_utc)
    end = parse_utc(source_fetch_end_utc)
    detected = parse_utc(detected_at_utc)
    first_seen = parse_utc(first_seen_at_utc)
    available = parse_utc(available_at_utc)
    ingested = parse_utc(ingested_at_utc)
    if start and end and start > end:
        raise ValueError("source_fetch_start_utc cannot be after source_fetch_end_utc")
    if end and detected and end > detected:
        raise ValueError("source_fetch_end_utc cannot be after detected_at_utc")
    if detected and available and detected > available:
        raise ValueError("detected_at_utc cannot be after available_at_utc")
    if first_seen and available and first_seen > available:
        raise ValueError("first_seen_at_utc cannot be after available_at_utc")
    if available and ingested and available > ingested:
        raise ValueError("available_at_utc cannot be after ingested_at_utc")
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
