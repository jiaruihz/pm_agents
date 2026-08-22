"""Consumer demand contract for shared Polymarket market-data capture.

The contract does not start a collector or write raw data. Strategy-specific
schedulers declare token demand; a capture owner may coalesce overlapping
demands so one token/epoch has one canonical writer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from src.platform.market_data.identity import canonical_json_hash


CAPTURE_DEMAND_SCHEMA_VERSION = "polymarket_capture_demand_v1"
CAPTURE_ASSIGNMENT_SCHEMA_VERSION = "polymarket_capture_assignment_v1"
PRIORITY_RANK = {"cold": 0, "P2": 1, "P1": 2, "P0": 3}
TRANSPORTS = frozenset({"REST", "WS", "REST_WS"})


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("capture-demand clocks must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class CaptureDemand:
    demand_id: str
    consumer_id: str
    strategy_key: str
    condition_id: str
    token_id: str
    reason: str
    priority: str
    requested_at_utc: str
    expires_at_utc: str
    desired_transport: str
    requested_checkpoints_seconds: tuple[int, ...] = ()
    trigger_event_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = CAPTURE_DEMAND_SCHEMA_VERSION

    def __post_init__(self) -> None:
        required = {
            "demand_id": self.demand_id,
            "consumer_id": self.consumer_id,
            "strategy_key": self.strategy_key,
            "condition_id": self.condition_id,
            "token_id": self.token_id,
            "reason": self.reason,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"missing capture-demand fields: {missing}")
        if self.priority not in PRIORITY_RANK:
            raise ValueError(f"unsupported capture priority: {self.priority}")
        if self.desired_transport not in TRANSPORTS:
            raise ValueError(f"unsupported capture transport: {self.desired_transport}")
        requested = _parse_utc(self.requested_at_utc)
        expires = _parse_utc(self.expires_at_utc)
        if expires <= requested:
            raise ValueError("capture demand must expire after it is requested")
        checkpoints = tuple(sorted(set(int(value) for value in self.requested_checkpoints_seconds)))
        if any(value < 0 for value in checkpoints):
            raise ValueError("capture checkpoints must be non-negative")
        object.__setattr__(self, "requested_checkpoints_seconds", checkpoints)
        object.__setattr__(self, "metadata", dict(self.metadata))
        expected = self.identity(
            consumer_id=self.consumer_id,
            strategy_key=self.strategy_key,
            condition_id=self.condition_id,
            token_id=self.token_id,
            reason=self.reason,
            trigger_event_id=self.trigger_event_id,
        )
        if self.demand_id != expected:
            raise ValueError("demand_id does not match canonical identity")

    @staticmethod
    def identity(
        *,
        consumer_id: str,
        strategy_key: str,
        condition_id: str,
        token_id: str,
        reason: str,
        trigger_event_id: str | None,
    ) -> str:
        return canonical_json_hash(
            {
                "schema_version": CAPTURE_DEMAND_SCHEMA_VERSION,
                "consumer_id": consumer_id,
                "strategy_key": strategy_key,
                "condition_id": condition_id,
                "token_id": token_id,
                "reason": reason,
                "trigger_event_id": trigger_event_id,
            }
        )

    @classmethod
    def create(cls, **values: Any) -> "CaptureDemand":
        identity_fields = {
            name: values.get(name)
            for name in (
                "consumer_id",
                "strategy_key",
                "condition_id",
                "token_id",
                "reason",
                "trigger_event_id",
            )
        }
        return cls(demand_id=cls.identity(**identity_fields), **values)

    def is_active(self, at_utc: str) -> bool:
        at = _parse_utc(at_utc)
        return _parse_utc(self.requested_at_utc) <= at < _parse_utc(self.expires_at_utc)

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["metadata"] = dict(self.metadata)
        return row


@dataclass(frozen=True)
class CaptureAssignment:
    assignment_id: str
    token_id: str
    condition_ids: tuple[str, ...]
    priority: str
    desired_transport: str
    active_demand_ids: tuple[str, ...]
    consumer_ids: tuple[str, ...]
    expires_at_utc: str
    requested_checkpoints_seconds: tuple[int, ...]
    schema_version: str = CAPTURE_ASSIGNMENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def coalesce_capture_demands(
    demands: Iterable[CaptureDemand], *, at_utc: str
) -> tuple[CaptureAssignment, ...]:
    """Merge only overlapping active token demands; unrelated universes remain separate."""

    grouped: dict[str, list[CaptureDemand]] = {}
    for demand in demands:
        if demand.is_active(at_utc):
            grouped.setdefault(demand.token_id, []).append(demand)
    assignments: list[CaptureAssignment] = []
    for token_id, rows in sorted(grouped.items()):
        priority = max(rows, key=lambda row: PRIORITY_RANK[row.priority]).priority
        transports = {row.desired_transport for row in rows}
        transport = (
            "REST_WS"
            if "REST_WS" in transports or transports == {"REST", "WS"}
            else max(transports)
        )
        demand_ids = tuple(sorted(row.demand_id for row in rows))
        expires_at = max(row.expires_at_utc for row in rows)
        checkpoints = tuple(
            sorted({value for row in rows for value in row.requested_checkpoints_seconds})
        )
        assignment_id = canonical_json_hash(
            {
                "schema_version": CAPTURE_ASSIGNMENT_SCHEMA_VERSION,
                "token_id": token_id,
                "active_demand_ids": demand_ids,
                "as_of_utc": at_utc,
            }
        )
        assignments.append(
            CaptureAssignment(
                assignment_id=assignment_id,
                token_id=token_id,
                condition_ids=tuple(sorted({row.condition_id for row in rows})),
                priority=priority,
                desired_transport=transport,
                active_demand_ids=demand_ids,
                consumer_ids=tuple(sorted({row.consumer_id for row in rows})),
                expires_at_utc=expires_at,
                requested_checkpoints_seconds=checkpoints,
            )
        )
    return tuple(assignments)
