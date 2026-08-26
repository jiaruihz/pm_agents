"""Build typed, append-only changes from released catalog snapshots.

This module is deliberately a pure comparison boundary.  It accepts frozen
``MarketSnapshot`` values and optional caller-supplied family fingerprints;
it does not infer families from labels, read storage, or acquire market data.
"""

from __future__ import annotations

from datetime import datetime

from ..contracts import (
    MarketChangeEvent,
    MarketChangeType,
    MarketSnapshot,
    MarketStatus,
    ProvenanceRef,
    stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256


DETECTOR_SOURCE = "polymarket_alpha.change_detector"
DETECTOR_VERSION = "p0_04_v1"

# Deliberately excludes slug, volume and liquidity.  Slugs are aliases, and
# volume/liquidity are observational metrics rather than structural metadata.
_METADATA_FIELDS = (
    "end_at",
    "identity.condition_id",
    "identity.event_id",
    "identity.no_token_id",
    "identity.yes_token_id",
    "question",
    "tags",
    "title",
)


def _family_pair(
    previous: str | None, current: str | None
) -> tuple[str | None, str | None]:
    """Validate the explicitly supplied, immutable family lineage pair."""

    if (previous is None) != (current is None):
        raise ValueError(
            "family fingerprint lineage must provide both previous and current values"
        )
    if previous is None:
        return None, None
    return validate_sha256(previous), validate_sha256(current)  # type: ignore[arg-type]


def _semantic_value(snapshot: MarketSnapshot, field: str) -> object:
    if field == "end_at":
        return snapshot.end_at
    if field == "question":
        return snapshot.question
    if field == "title":
        return snapshot.title
    if field == "tags":
        # Tags are an unordered classification set; their source-array order
        # must not create a revision event.  Preserve exact tag text otherwise.
        return tuple(sorted(set(snapshot.tags)))
    _, attribute = field.split(".", maxsplit=1)
    return getattr(snapshot.identity, attribute)


def _reject_unreleased_status(snapshot: MarketSnapshot, *, role: str) -> None:
    if snapshot.status == MarketStatus.SUPERSEDED:
        raise ValueError(f"{role} snapshot has unreachable SUPERSEDED status")


def detect_market_change(
    previous: MarketSnapshot | None,
    current: MarketSnapshot,
    *,
    run_id: str,
    detected_at: datetime,
    source_version: str = DETECTOR_VERSION,
    previous_family_fingerprint: str | None = None,
    current_family_fingerprint: str | None = None,
) -> MarketChangeEvent | None:
    """Return one deterministic event, or ``None`` for no logical change.

    An exact retry with the same run/clock creates identical canonical event
    bytes and id.  A later detector attempt receives a distinct append-only
    id, avoiding a same-id/different-envelope conflict in the repository.  The
    current snapshot's observation clock is the effective time and must
    already be a released, aware contract timestamp.
    """

    detected = ensure_utc(detected_at)
    _reject_unreleased_status(current, role="current")
    prev_family, curr_family = _family_pair(
        previous_family_fingerprint, current_family_fingerprint
    )

    if previous is None:
        change_types = (MarketChangeType.NEW,)
        changed_fields = ("__new__",)
        previous_id = None
        previous_hash = None
        previous_status = None
        previous_rule_hash = None
        provenance = (
            ProvenanceRef(
                source_artifact_id=current.record_id,
                relation="current_market_snapshot",
                content_sha256=current.canonical_sha256,
                source_observed_at=current.source_observed_at,
            ),
        )
    else:
        _reject_unreleased_status(previous, role="previous")
        if previous.identity.market_id != current.identity.market_id:
            raise ValueError("market_id mismatch across snapshot revisions")

        types: set[MarketChangeType] = set()
        fields: set[str] = set()
        if previous.rule_hash != current.rule_hash:
            types.add(MarketChangeType.RULE_CHANGED)
            fields.add("rule_hash")
        if previous.status != current.status:
            types.add(MarketChangeType.LIFECYCLE_CHANGED)
            fields.add("status")
            if current.status == MarketStatus.CLOSED:
                types.add(MarketChangeType.CLOSED)
            if current.status == MarketStatus.RESOLVED:
                types.add(MarketChangeType.RESOLVED)
        for field in _METADATA_FIELDS:
            if _semantic_value(previous, field) != _semantic_value(current, field):
                types.add(MarketChangeType.METADATA_CHANGED)
                fields.add(field)
        if prev_family != curr_family:
            types.add(MarketChangeType.FAMILY_CHANGED)
            fields.add("family_fingerprint")

        if not types:
            return None
        change_types = tuple(sorted(types, key=lambda item: item.value))
        changed_fields = tuple(sorted(fields))
        previous_id = previous.record_id
        previous_hash = previous.canonical_sha256
        previous_status = previous.status
        previous_rule_hash = previous.rule_hash
        provenance = (
            ProvenanceRef(
                source_artifact_id=previous.record_id,
                relation="previous_market_snapshot",
                content_sha256=previous.canonical_sha256,
                source_observed_at=previous.source_observed_at,
            ),
            ProvenanceRef(
                source_artifact_id=current.record_id,
                relation="current_market_snapshot",
                content_sha256=current.canonical_sha256,
                source_observed_at=current.source_observed_at,
            ),
        )

    # The payload identity carries the logical revision plus the detector
    # attempt envelope.  The before/after snapshot pair remains the stable
    # semantic-dedupe key, while a new run/clock cannot collide with an
    # already-persisted append-only event carrying different canonical bytes.
    record_id = stable_record_id(
        "market_change",
        current.identity.market_id,
        previous_id,
        previous_hash,
        current.record_id,
        current.canonical_sha256,
        tuple(item.value for item in change_types),
        changed_fields,
        prev_family,
        curr_family,
        run_id,
        detected,
        source_version,
    )
    effective = current.source_observed_at
    extensions = (
        {
            "family_lineage": {
                "current_fingerprint": curr_family,
                "previous_fingerprint": prev_family,
            }
        }
        if prev_family is not None
        else {}
    )
    return MarketChangeEvent(
        record_id=record_id,
        change_event_id=record_id,
        run_id=run_id,
        created_at=detected,
        source=DETECTOR_SOURCE,
        source_version=source_version,
        provenance=provenance,
        extensions=extensions,
        market_id=current.identity.market_id,
        previous_snapshot_id=previous_id,
        current_snapshot_id=current.record_id,
        previous_snapshot_sha256=previous_hash,
        current_snapshot_sha256=current.canonical_sha256,
        previous_status=previous_status,
        current_status=current.status,
        previous_rule_hash=previous_rule_hash,
        current_rule_hash=current.rule_hash,
        change_types=change_types,
        changed_fields=changed_fields,
        effective_at=effective,
        detected_at=detected,
    )
