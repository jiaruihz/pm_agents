"""Versioned contracts for deterministic city-weather replay evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Mapping

from weather_clock_contract import (
    DECISION_CLOCK_ORDER,
    SOURCE_CLOCK_ORDER,
    parse_utc as parse_strict_utc,
    utc_text as canonical_utc_text,
    validate_clock_order,
)


EVENT_SCHEMA_VERSION = "weather_city_event_envelope_v1"
PREDICTION_SCHEMA_VERSION = "weather_city_prediction_row_v1"


def stable_json(value: Any) -> str:
    """Return the canonical JSON representation used by replay identities."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def parse_utc(value: Any) -> datetime:
    parsed = parse_strict_utc(value)
    assert parsed is not None
    return parsed


def utc_text(value: datetime | str) -> str:
    return canonical_utc_text(value)


@dataclass(frozen=True)
class EventEnvelope:
    """One immutable information event ordered by its PIT availability clock."""

    event_id: str
    city: str
    target_date: str
    payload_kind: str
    state_key: str
    source: str
    available_at_utc: str
    observed_at_utc: str | None
    first_seen_at_utc: str
    material_state_change: bool
    revision_of_event_id: str | None
    physical_ref: Mapping[str, Any]
    payload: Mapping[str, Any]
    schema_version: str = EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            not self.event_id
            or not self.city
            or not self.target_date
            or not self.state_key
        ):
            raise ValueError("event_id, city, target_date and state_key are required")
        parse_utc(self.available_at_utc)
        parse_utc(self.first_seen_at_utc)
        if self.observed_at_utc is not None:
            parse_utc(self.observed_at_utc)
        validate_clock_order(
            {
                "observed_at_utc": self.observed_at_utc,
                "first_seen_at_utc": self.first_seen_at_utc,
                "available_at_utc": self.available_at_utc,
            },
            SOURCE_CLOCK_ORDER,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


REQUIRED_PREDICTION_FIELDS = (
    "schema_version",
    "city",
    "target_date",
    "decision_ts_utc",
    "target_id",
    "target_kind",
    "p_model",
    "label",
    "split",
    "model_id",
    "feature_set_id",
    "pit_provenance",
    "checkpoint_id",
    "scorable_status",
    "coverage_status",
    "event_id",
    "event_payload_kind",
    "event_available_at_utc",
    "input_refs",
    "runtime_lineage",
)


def validate_prediction_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the thin cross-city prediction-table contract."""

    missing = [field for field in REQUIRED_PREDICTION_FIELDS if field not in row]
    if missing:
        raise ValueError(f"prediction row missing fields: {missing}")
    normalized = dict(row)
    if normalized["schema_version"] != PREDICTION_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported prediction schema: {normalized['schema_version']}"
        )
    normalized["decision_ts_utc"] = utc_text(normalized["decision_ts_utc"])
    normalized["event_available_at_utc"] = utc_text(
        normalized["event_available_at_utc"]
    )
    try:
        validate_clock_order(normalized, DECISION_CLOCK_ORDER)
    except ValueError as exc:
        if "event_available_at_utc" in str(exc):
            raise ValueError("event is not available at decision_ts_utc") from exc
        raise
    for field in ("p_model", "market_p"):
        value = normalized.get(field)
        if value is not None and not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"{field} must be in [0, 1]")
    label = normalized.get("label")
    if label is not None and int(label) not in (0, 1):
        raise ValueError("label must be 0/1 or null")
    if normalized["scorable_status"] == "scorable":
        if normalized["p_model"] is None or label is None:
            raise ValueError("scorable rows require p_model and label")
    if not isinstance(normalized["input_refs"], list):
        raise ValueError("input_refs must be a list")
    if not isinstance(normalized["runtime_lineage"], Mapping):
        raise ValueError("runtime_lineage must be an object")
    return normalized
