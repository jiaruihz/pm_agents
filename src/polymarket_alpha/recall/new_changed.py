"""Pure pre-book recall from catalog change events.

This provider consumes only frozen P0 catalog snapshots and P0-04 change
events supplied by its caller.  It neither performs I/O nor interprets book,
wallet, or probability data.  A malformed lineage is represented as a
suppression and cannot yield a ``RecallHit``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import field_validator, model_validator

from ..contracts import (
    AlphaContract,
    MarketChangeEvent,
    MarketChangeType,
    MarketSnapshot,
    MarketStatus,
    ProvenanceRef,
    RecallHit,
    RecallerType,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc
from .registry import ProviderDescriptor


NEW_CHANGED_PROVIDER_ID = "new_changed"
NEW_CHANGED_RECALLER_VERSION = "new-changed-v1"
NEW_CHANGED_RAW_SCORE = Decimal("1")


class RecallSuppressionReason(StrEnum):
    """Reasons a pre-book event cannot be emitted as a research recall."""

    TERMINAL_MARKET = "TERMINAL_MARKET"
    POST_CUTOFF_EVENT = "POST_CUTOFF_EVENT"
    MISSING_CURRENT_SNAPSHOT = "MISSING_CURRENT_SNAPSHOT"
    MISSING_PREVIOUS_SNAPSHOT = "MISSING_PREVIOUS_SNAPSHOT"
    SNAPSHOT_ID_CONFLICT = "SNAPSHOT_ID_CONFLICT"
    EVENT_ID_CONFLICT = "EVENT_ID_CONFLICT"
    EVENT_LINEAGE_MISMATCH = "EVENT_LINEAGE_MISMATCH"
    UNSUPPORTED_CHANGE = "UNSUPPORTED_CHANGE"


class RecallSuppression(AlphaContract):
    provider_id: str
    change_event_id: str
    market_id: str
    reason: RecallSuppressionReason


class PrebookRecallRequest(AlphaContract):
    """Explicit, offline inputs shared by the P0-06B provider pair."""

    run_id: str
    as_of: datetime
    events: tuple[MarketChangeEvent, ...] = ()
    snapshots: tuple[MarketSnapshot, ...] = ()

    @field_validator("run_id")
    @classmethod
    def run_id_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("run_id must not be blank")
        return value

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class PrebookRecallOutcome(AlphaContract):
    provider_id: str
    recaller_version: str
    as_of: datetime
    request_sha256: str
    hits: tuple[RecallHit, ...]
    suppressions: tuple[RecallSuppression, ...]

    @model_validator(mode="after")
    def provider_output_is_closed(self) -> "PrebookRecallOutcome":
        specifications = {
            NEW_CHANGED_PROVIDER_ID: (
                RecallerType.NEW_CHANGED,
                frozenset({"NEW_MARKET", "RULE_REVISION", "LIFECYCLE_REVISION"}),
            ),
            "structural_metadata": (
                RecallerType.STRUCTURAL_METADATA,
                frozenset(
                    {
                        "METADATA_REVISION",
                        "DEADLINE_REVISION",
                        "IDENTITY_MAPPING_CHANGED",
                        "FAMILY_RELATION_CHANGED",
                    }
                ),
            ),
        }
        specification = specifications.get(self.provider_id)
        if specification is None:
            raise ValueError("pre-book outcome uses an unknown provider")
        recaller, allowed_reasons = specification
        for hit in self.hits:
            if (
                hit.source != self.provider_id
                or hit.source_version != self.recaller_version
                or hit.recaller != recaller
                or hit.recaller_version != self.recaller_version
                or not set(hit.reason_codes) <= allowed_reasons
            ):
                raise ValueError("pre-book hit violates the provider output contract")
        if any(item.provider_id != self.provider_id for item in self.suppressions):
            raise ValueError("pre-book suppressions must carry the provider identity")
        return self


def canonical_prebook_request_view(request: PrebookRecallRequest) -> dict[str, object]:
    """Order-free request view used for replay evidence only."""

    return {
        "run_id": request.run_id,
        "as_of": request.as_of,
        "events": [
            item.model_dump(mode="python")
            for item in sorted(
                request.events,
                key=lambda event: (event.record_id, event.canonical_sha256),
            )
        ],
        "snapshots": [
            item.model_dump(mode="python")
            for item in sorted(
                request.snapshots,
                key=lambda snapshot: (snapshot.record_id, snapshot.canonical_sha256),
            )
        ],
    }


def snapshot_index(
    snapshots: tuple[MarketSnapshot, ...],
) -> tuple[dict[str, MarketSnapshot], frozenset[str]]:
    """Index frozen revisions, retaining record-id collisions as unsafe."""

    indexed: dict[str, MarketSnapshot] = {}
    conflicts: set[str] = set()
    for snapshot in snapshots:
        existing = indexed.get(snapshot.record_id)
        if existing is None:
            indexed[snapshot.record_id] = snapshot
        elif existing.canonical_sha256 != snapshot.canonical_sha256:
            conflicts.add(snapshot.record_id)
    return indexed, frozenset(conflicts)


def event_index(
    events: tuple[MarketChangeEvent, ...],
) -> tuple[dict[str, MarketChangeEvent], frozenset[str]]:
    """Deduplicate exact retries and retain same-id content conflicts."""

    indexed: dict[str, MarketChangeEvent] = {}
    conflicts: set[str] = set()
    for event in sorted(events, key=lambda item: (item.record_id, item.canonical_sha256)):
        existing = indexed.get(event.record_id)
        if existing is None:
            indexed[event.record_id] = event
        elif existing.canonical_sha256 != event.canonical_sha256:
            conflicts.add(event.record_id)
    return indexed, frozenset(conflicts)


def event_snapshot_suppression(
    event: MarketChangeEvent,
    *,
    snapshots: dict[str, MarketSnapshot],
    conflicted_snapshot_ids: frozenset[str],
    as_of: datetime,
    provider_id: str,
) -> RecallSuppression | None:
    """Verify every claimed snapshot/provenance edge before use."""

    if event.effective_at > as_of or event.detected_at > as_of:
        return RecallSuppression(
            provider_id=provider_id,
            change_event_id=event.change_event_id,
            market_id=event.market_id,
            reason=RecallSuppressionReason.POST_CUTOFF_EVENT,
        )
    references = (event.current_snapshot_id, event.previous_snapshot_id)
    if any(item in conflicted_snapshot_ids for item in references if item is not None):
        return RecallSuppression(
            provider_id=provider_id,
            change_event_id=event.change_event_id,
            market_id=event.market_id,
            reason=RecallSuppressionReason.SNAPSHOT_ID_CONFLICT,
        )
    current = snapshots.get(event.current_snapshot_id)
    if current is None:
        return RecallSuppression(
            provider_id=provider_id,
            change_event_id=event.change_event_id,
            market_id=event.market_id,
            reason=RecallSuppressionReason.MISSING_CURRENT_SNAPSHOT,
        )
    if (
        current.canonical_sha256 != event.current_snapshot_sha256
        or current.identity.market_id != event.market_id
        or current.status != event.current_status
        or current.rule_hash != event.current_rule_hash
        or current.source_observed_at != event.effective_at
        or not _has_provenance(event, current, "current_market_snapshot")
    ):
        return RecallSuppression(
            provider_id=provider_id,
            change_event_id=event.change_event_id,
            market_id=event.market_id,
            reason=RecallSuppressionReason.EVENT_LINEAGE_MISMATCH,
        )
    if event.previous_snapshot_id is None:
        return None
    previous = snapshots.get(event.previous_snapshot_id)
    if previous is None:
        return RecallSuppression(
            provider_id=provider_id,
            change_event_id=event.change_event_id,
            market_id=event.market_id,
            reason=RecallSuppressionReason.MISSING_PREVIOUS_SNAPSHOT,
        )
    if (
        previous.canonical_sha256 != event.previous_snapshot_sha256
        or previous.identity.market_id != event.market_id
        or previous.status != event.previous_status
        or previous.rule_hash != event.previous_rule_hash
        or not _has_provenance(event, previous, "previous_market_snapshot")
    ):
        return RecallSuppression(
            provider_id=provider_id,
            change_event_id=event.change_event_id,
            market_id=event.market_id,
            reason=RecallSuppressionReason.EVENT_LINEAGE_MISMATCH,
        )
    return None


def _has_provenance(
    event: MarketChangeEvent, snapshot: MarketSnapshot, relation: str
) -> bool:
    return any(
        reference.source_artifact_id == snapshot.record_id
        and reference.content_sha256 == snapshot.canonical_sha256
        and reference.source_observed_at == snapshot.source_observed_at
        and reference.relation == relation
        for reference in event.provenance
    )


def event_is_terminal(event: MarketChangeEvent) -> bool:
    return event.current_status in {MarketStatus.CLOSED, MarketStatus.RESOLVED}


def build_prebook_hit(
    *,
    request: PrebookRecallRequest,
    event: MarketChangeEvent,
    snapshot: MarketSnapshot,
    provider_id: str,
    recaller: RecallerType,
    recaller_version: str,
    reason_codes: tuple[str, ...],
    raw_score: Decimal,
    extra_features: dict[str, object] | None = None,
) -> RecallHit:
    """Build a lineage-bound, deterministic and non-directional RecallHit."""

    features: dict[str, object] = {
        "change_event_id": event.change_event_id,
        "change_event_sha256": event.canonical_sha256,
        "current_snapshot_id": snapshot.record_id,
        "current_snapshot_sha256": snapshot.canonical_sha256,
        "current_status": snapshot.status.value,
        "change_types": tuple(item.value for item in event.change_types),
        "changed_fields": event.changed_fields,
        "rule_hash": snapshot.rule_hash,
        "end_at": snapshot.end_at,
    }
    if extra_features:
        features.update(extra_features)
    identity = {
        "provider_id": provider_id,
        "recaller": recaller.value,
        "recaller_version": recaller_version,
        "request_run_id": request.run_id,
        "as_of": request.as_of,
        "event_id": event.change_event_id,
        "event_sha256": event.canonical_sha256,
        "snapshot_sha256": snapshot.canonical_sha256,
        "reason_codes": reason_codes,
        "features": features,
    }
    return RecallHit(
        record_id=stable_record_id("recall_hit", identity),
        run_id=request.run_id,
        created_at=request.as_of,
        source=provider_id,
        source_version=recaller_version,
        provenance=(
            ProvenanceRef(
                source_artifact_id=event.change_event_id,
                relation="market_change_event",
                content_sha256=event.canonical_sha256,
                source_observed_at=event.effective_at,
            ),
            ProvenanceRef(
                source_artifact_id=snapshot.record_id,
                relation="current_market_snapshot",
                content_sha256=snapshot.canonical_sha256,
                source_observed_at=snapshot.source_observed_at,
            ),
        ),
        extensions={},
        market_id=event.market_id,
        recaller=recaller,
        recaller_version=recaller_version,
        reason_codes=reason_codes,
        features=features,
        raw_score=raw_score,
        observed_at=event.effective_at,
        valid_until=None,
        historical_only=False,
    )


class NewChangedRecaller:
    """Emit stable pre-book hits for new markets and nonterminal revisions."""

    provider_id: str = NEW_CHANGED_PROVIDER_ID
    recaller_version: str = NEW_CHANGED_RECALLER_VERSION

    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self.provider_id,
            recaller=RecallerType.NEW_CHANGED,
            recaller_version=self.recaller_version,
        )

    def recall(self, request: PrebookRecallRequest) -> PrebookRecallOutcome:
        snapshots, conflicts = snapshot_index(request.snapshots)
        events, event_conflicts = event_index(request.events)
        hits: list[RecallHit] = []
        suppressions: list[RecallSuppression] = []
        for event in sorted(events.values(), key=lambda item: item.record_id):
            if event.record_id in event_conflicts:
                suppressions.append(
                    RecallSuppression(
                        provider_id=self.provider_id,
                        change_event_id=event.change_event_id,
                        market_id=event.market_id,
                        reason=RecallSuppressionReason.EVENT_ID_CONFLICT,
                    )
                )
                continue
            suppression = event_snapshot_suppression(
                event,
                snapshots=snapshots,
                conflicted_snapshot_ids=conflicts,
                as_of=request.as_of,
                provider_id=self.provider_id,
            )
            if suppression is not None:
                suppressions.append(suppression)
                continue
            if event_is_terminal(event):
                suppressions.append(
                    RecallSuppression(
                        provider_id=self.provider_id,
                        change_event_id=event.change_event_id,
                        market_id=event.market_id,
                        reason=RecallSuppressionReason.TERMINAL_MARKET,
                    )
                )
                continue
            reason_codes = _new_changed_reasons(event)
            if not reason_codes:
                suppressions.append(
                    RecallSuppression(
                        provider_id=self.provider_id,
                        change_event_id=event.change_event_id,
                        market_id=event.market_id,
                        reason=RecallSuppressionReason.UNSUPPORTED_CHANGE,
                    )
                )
                continue
            hits.append(
                build_prebook_hit(
                    request=request,
                    event=event,
                    snapshot=snapshots[event.current_snapshot_id],
                    provider_id=self.provider_id,
                    recaller=RecallerType.NEW_CHANGED,
                    recaller_version=self.recaller_version,
                    reason_codes=reason_codes,
                    raw_score=NEW_CHANGED_RAW_SCORE,
                )
            )
        return PrebookRecallOutcome(
            provider_id=self.provider_id,
            recaller_version=self.recaller_version,
            as_of=request.as_of,
            request_sha256=content_sha256(canonical_prebook_request_view(request)),
            hits=tuple(sorted(hits, key=lambda item: item.record_id)),
            suppressions=tuple(
                sorted(
                    suppressions,
                    key=lambda item: (item.change_event_id, item.reason.value),
                )
            ),
        )


def _new_changed_reasons(event: MarketChangeEvent) -> tuple[str, ...]:
    mapping = {
        MarketChangeType.NEW: "NEW_MARKET",
        MarketChangeType.RULE_CHANGED: "RULE_REVISION",
        MarketChangeType.LIFECYCLE_CHANGED: "LIFECYCLE_REVISION",
    }
    return tuple(
        mapping[change_type]
        for change_type in event.change_types
        if change_type in mapping
    )
