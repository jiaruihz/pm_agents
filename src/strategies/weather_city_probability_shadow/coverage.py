"""Coverage-only WCIR adapter for cities without a frozen probability head.

The adapter deliberately emits a structured checkpoint blocker instead of
inventing a probability, settlement bracket, or market expression.  It lets a
new city join the shared producer/clock/replay/runtime contract before its
city-specific model is ready.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import CityScore, InputNotReady


UTC = timezone.utc


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _tail_jsonl(path: Path, *, max_bytes: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    size = path.stat().st_size
    with path.open("rb") as handle:
        offset = max(0, size - max_bytes)
        handle.seek(offset)
        if offset:
            handle.readline()
        payload = handle.read()
    rows: list[dict[str, Any]] = []
    for raw in payload.splitlines():
        try:
            row = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _available_at(row: dict[str, Any]) -> datetime | None:
    for field in (
        "available_at_utc",
        "source_first_seen_at_utc",
        "first_seen_at_utc",
        "local_detect_ts_utc",
        "fetched_at_utc",
    ):
        value = _parse_ts(row.get(field))
        if value is not None:
            return value
    return None


def _observation_at(row: dict[str, Any]) -> datetime | None:
    return _parse_ts(row.get("observation_time_utc") or row.get("source_event_ts_utc"))


def _payload_kind(rows: list[dict[str, Any]]) -> str:
    if any(
        row.get("measurement_interval_start_utc")
        or row.get("measurement_interval_end_utc")
        or row.get("valid_from_utc")
        or row.get("valid_to_utc")
        for row in rows
    ):
        return "measurement_interval_revision"
    if len(rows) > 1 or any(row.get("runway") for row in rows):
        return "point_group_revision"
    return "point_observation"


class ObservationCoverageAdapter:
    """Journal the latest typed source event as a non-scorable checkpoint."""

    def score(self, profile: dict[str, Any], now: datetime) -> list[CityScore]:
        now = now.astimezone(UTC)
        city = str(profile["city"])
        source = str(profile["source"])
        source_path = Path(profile["source_journal"])
        rows = _tail_jsonl(
            source_path,
            max_bytes=int(profile.get("source_tail_bytes", 16 * 1024 * 1024)),
        )
        eligible = []
        for row in rows:
            if row.get("city") != city or row.get("source") != source:
                continue
            if row.get("schema_version") != "weather_high_frequency_observation_v1":
                continue
            if row.get("source_status") not in {None, "ok"}:
                continue
            available = _available_at(row)
            observed = _observation_at(row)
            if available is None or observed is None or available > now:
                continue
            if profile.get("material_state_change_only", True) and not row.get(
                "material_state_change", True
            ):
                continue
            eligible.append((observed, available, row))

        if not eligible:
            raise InputNotReady(
                "source_observation_not_available",
                city=city,
                target_date=str(profile.get("target_date") or "unknown"),
                decision_ts_utc=now.isoformat(),
                details={
                    "framework_id": profile.get("framework_id"),
                    "source": source,
                    "source_journal": str(source_path),
                    "expected_row_schema": "weather_high_frequency_observation_v1",
                },
            )

        latest_observed = max(item[0] for item in eligible)
        observation_rows = [
            row for observed, _available, row in eligible if observed == latest_observed
        ]
        latest_available = max(
            value for _observed, value, _row in eligible if _observed == latest_observed
        )
        latest_row = max(observation_rows, key=lambda row: _available_at(row) or latest_available)
        target_date = str(latest_row.get("target_date") or "unknown")
        age_seconds = max(0.0, (now - latest_available).total_seconds())
        max_age_seconds = float(profile.get("max_source_age_seconds", 900))
        reason = (
            "source_observation_stale"
            if age_seconds > max_age_seconds
            else str(profile.get("blocker_reason") or "city_probability_model_not_deployed")
        )
        event_ids = sorted(
            str(row.get("information_event_id"))
            for row in observation_rows
            if row.get("information_event_id")
        )
        temperatures = [
            float(row["temp_c"])
            for row in observation_rows
            if row.get("temp_c") is not None
        ]
        preferred = next(
            (
                float(row["temp_c"])
                for row in observation_rows
                if row.get("is_preferred_temperature_runway")
                and row.get("temp_c") is not None
            ),
            None,
        )
        details = {
            "framework_id": profile.get("framework_id"),
            "strategy_family": profile.get("strategy_family"),
            "profile_id": profile.get("profile_id"),
            "source": source,
            "station_id": latest_row.get("station_id") or latest_row.get("station"),
            "source_journal": str(source_path),
            "source_observation_ts_utc": latest_observed.isoformat(),
            "source_available_at_utc": latest_available.isoformat(),
            "max_source_age_seconds": max_age_seconds,
            "payload_kind": _payload_kind(observation_rows),
            "event_roles": sorted(
                {str(row.get("event_role") or "unknown") for row in observation_rows}
            ),
            "information_event_ids": event_ids,
            "revision_parent_event_ids": sorted(
                str(row.get("revision_of_event_id"))
                for row in observation_rows
                if row.get("revision_of_event_id")
            ),
            "measurement_interval_start_utc": latest_row.get(
                "measurement_interval_start_utc"
            )
            or latest_row.get("valid_from_utc"),
            "measurement_interval_end_utc": latest_row.get(
                "measurement_interval_end_utc"
            )
            or latest_row.get("valid_to_utc"),
            "source_temp_c": preferred
            if preferred is not None
            else (max(temperatures) if temperatures else None),
            "source_temp_min_c": min(temperatures) if temperatures else None,
            "source_temp_max_c": max(temperatures) if temperatures else None,
            "model_status": "not_deployed",
            "market_expression_status": "not_mapped",
        }
        raise InputNotReady(
            reason,
            city=city,
            target_date=target_date,
            decision_ts_utc=latest_available.isoformat(),
            details=details,
        )


__all__ = ["ObservationCoverageAdapter"]
