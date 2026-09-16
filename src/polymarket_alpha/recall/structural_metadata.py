"""Pure metadata and market-family recall for P0-06B.

Only change-event structure and explicitly frozen family fingerprints are
used.  This module is intentionally independent of books and any valuation
calculation.
"""

from __future__ import annotations

from decimal import Decimal

from ..contracts import MarketChangeEvent, MarketChangeType, RecallerType, content_sha256
from ..contracts.base import validate_sha256
from .new_changed import (
    PrebookRecallOutcome,
    PrebookRecallRequest,
    RecallSuppression,
    RecallSuppressionReason,
    build_prebook_hit,
    canonical_prebook_request_view,
    event_index,
    event_is_terminal,
    event_snapshot_suppression,
    snapshot_index,
)
from .registry import ProviderDescriptor


STRUCTURAL_METADATA_PROVIDER_ID = "structural_metadata"
STRUCTURAL_METADATA_RECALLER_VERSION = "structural-metadata-v1"
STRUCTURAL_METADATA_RAW_SCORE = Decimal("0.5")


class StructuralMetadataRecaller:
    """Emit only metadata/family consistency recalls from valid catalog events."""

    provider_id: str = STRUCTURAL_METADATA_PROVIDER_ID
    recaller_version: str = STRUCTURAL_METADATA_RECALLER_VERSION

    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self.provider_id,
            recaller=RecallerType.STRUCTURAL_METADATA,
            recaller_version=self.recaller_version,
        )

    def recall(self, request: PrebookRecallRequest) -> PrebookRecallOutcome:
        snapshots, conflicts = snapshot_index(request.snapshots)
        events, event_conflicts = event_index(request.events)
        hits = []
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
            reasons, family_features = _structural_reasons(event)
            if not reasons:
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
                    recaller=RecallerType.STRUCTURAL_METADATA,
                    recaller_version=self.recaller_version,
                    reason_codes=reasons,
                    raw_score=STRUCTURAL_METADATA_RAW_SCORE,
                    extra_features=family_features,
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


def _structural_reasons(event: MarketChangeEvent) -> tuple[tuple[str, ...], dict[str, object]]:
    reasons: list[str] = []
    features: dict[str, object] = {}
    if MarketChangeType.METADATA_CHANGED in event.change_types:
        reasons.append("METADATA_REVISION")
        if "end_at" in event.changed_fields:
            reasons.append("DEADLINE_REVISION")
        if any(field.startswith("identity.") for field in event.changed_fields):
            reasons.append("IDENTITY_MAPPING_CHANGED")
    if MarketChangeType.FAMILY_CHANGED in event.change_types:
        lineage = event.extensions.get("family_lineage")
        if not isinstance(lineage, dict):
            return (), {}
        previous = lineage.get("previous_fingerprint")
        current = lineage.get("current_fingerprint")
        if not isinstance(previous, str) or not isinstance(current, str):
            return (), {}
        try:
            previous = validate_sha256(previous)
            current = validate_sha256(current)
        except ValueError:
            return (), {}
        if previous == current or "family_fingerprint" not in event.changed_fields:
            return (), {}
        reasons.append("FAMILY_RELATION_CHANGED")
        features["family_lineage"] = {
            "previous_fingerprint": previous,
            "current_fingerprint": current,
        }
    return tuple(reasons), features
