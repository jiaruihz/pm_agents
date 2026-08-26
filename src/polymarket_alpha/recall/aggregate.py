"""Deterministic RecallHit dedupe and CandidateCard revision builder."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from ..contracts import (
    ALPHA_CONTRACT_VERSION,
    AlphaContract,
    CandidateCard,
    CandidateEventType,
    CandidateState,
    RecallHit,
    ResearchPriority,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256
from .registry import ProviderRegistry


class ProviderBatch(AlphaContract):
    provider_id: str
    hits: tuple[RecallHit, ...] = ()


class RecallAggregationConfig(AlphaContract):
    aggregator_version: str = "p0-06a-v1"
    core_score_threshold: Decimal = Field(default=Decimal("1"), ge=0)
    micro_score_threshold: Decimal = Field(default=Decimal("0.5"), ge=0)
    core_provider_diversity: int = Field(default=2, ge=1)

    @model_validator(mode="after")
    def thresholds_are_ordered(self) -> "RecallAggregationConfig":
        if self.micro_score_threshold > self.core_score_threshold:
            raise ValueError("micro threshold cannot exceed core threshold")
        return self


class RecallAggregationRequest(AlphaContract):
    run_id: str
    created_at: datetime
    as_of: datetime
    include_book_providers: bool = False
    batches: tuple[ProviderBatch, ...] = ()
    prior_candidates: tuple[CandidateCard, ...] = ()
    prior_hits: tuple[RecallHit, ...] = ()
    prior_projection_input_hashes: dict[str, str] = Field(default_factory=dict)
    merged_projection_input_hashes: dict[str, str] = Field(default_factory=dict)

    @field_validator("created_at", "as_of")
    @classmethod
    def times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("prior_projection_input_hashes", "merged_projection_input_hashes")
    @classmethod
    def projection_hashes_are_valid(cls, value: dict[str, str]) -> dict[str, str]:
        return {key: validate_sha256(item) for key, item in value.items()}

    @model_validator(mode="after")
    def inputs_are_unique(self) -> "RecallAggregationRequest":
        candidate_ids = [item.candidate_id for item in self.prior_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("prior candidates must be unique")
        batch_ids = [item.provider_id for item in self.batches]
        if len(batch_ids) != len(set(batch_ids)):
            raise ValueError("provider batches must be unique")
        prior_hit_ids = [item.record_id for item in self.prior_hits]
        if len(prior_hit_ids) != len(set(prior_hit_ids)):
            raise ValueError("prior hit record ids must be unique")
        return self


class RejectedRecall(AlphaContract):
    provider_id: str
    recall_hit_id: str
    reason: str


class LateHitImpactType(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    PRE_BLIND_REFRESH = "PRE_BLIND_REFRESH"
    NO_BLIND_INPUT_CHANGE = "NO_BLIND_INPUT_CHANGE"
    RESEARCH_REFRESH_REQUIRED = "RESEARCH_REFRESH_REQUIRED"
    IMPACT_UNDETERMINED = "IMPACT_UNDETERMINED"


class LateHitImpact(AlphaContract):
    impact: LateHitImpactType
    candidate_id: str
    new_recall_hit_ids: tuple[str, ...]
    prior_projection_input_sha256: str | None = None
    merged_projection_input_sha256: str | None = None
    required_event: CandidateEventType | None = None
    safe_to_advance: bool


class CandidateMergeResult(AlphaContract):
    candidate: CandidateCard
    accepted_recall_hit_ids: tuple[str, ...]
    duplicate_recall_hit_ids: tuple[str, ...]
    impact: LateHitImpact


class RecallAggregationOutcome(AlphaContract):
    request_sha256: str
    results: tuple[CandidateMergeResult, ...]
    rejected: tuple[RejectedRecall, ...]
    active_provider_ids: tuple[str, ...]


_BLIND_FROZEN_OR_LATER = frozenset(
    {
        CandidateState.BLIND_PROJECTION_FROZEN,
        CandidateState.BLIND_PACKET_FROZEN,
        CandidateState.BLIND_RESULT_ACCEPTED,
        CandidateState.BOOK_SNAPSHOT_ACCEPTED,
        CandidateState.MARKET_PACKET_FROZEN,
        CandidateState.MARKET_RESULT_ACCEPTED,
        CandidateState.RULE_B_PASSED,
        CandidateState.RULE_B_RISK,
        CandidateState.RULE_B_BLOCKED,
        CandidateState.RANKED,
        CandidateState.WATCHLISTED,
        CandidateState.REJECTED,
        CandidateState.SIMULATION_RECORDED,
    }
)


def recall_dedupe_key(hit: RecallHit) -> str:
    """Hash source/business semantics while ignoring retry envelope identity."""

    return content_sha256(
        {
            "market_id": hit.market_id,
            "source": hit.source,
            "recaller": hit.recaller,
            "recaller_version": hit.recaller_version,
            "reason_codes": tuple(sorted(hit.reason_codes)),
            "features": hit.features,
            "raw_score": hit.raw_score,
            "observed_at": hit.observed_at,
            "valid_until": hit.valid_until,
            "historical_only": hit.historical_only,
        }
    )


def _priority(
    score: Decimal,
    provider_diversity: int,
    config: RecallAggregationConfig,
) -> ResearchPriority:
    if score >= config.core_score_threshold or provider_diversity >= config.core_provider_diversity:
        return ResearchPriority.CORE
    if score >= config.micro_score_threshold:
        return ResearchPriority.MICRO
    return ResearchPriority.WATCH


def _impact(
    candidate_id: str,
    existing: CandidateCard | None,
    new_hit_ids: tuple[str, ...],
    request: RecallAggregationRequest,
) -> LateHitImpact:
    if existing is None or not new_hit_ids:
        return LateHitImpact(
            impact=LateHitImpactType.NOT_APPLICABLE,
            candidate_id=candidate_id,
            new_recall_hit_ids=new_hit_ids,
            safe_to_advance=True,
        )
    if existing.state not in _BLIND_FROZEN_OR_LATER:
        return LateHitImpact(
            impact=LateHitImpactType.PRE_BLIND_REFRESH,
            candidate_id=candidate_id,
            new_recall_hit_ids=new_hit_ids,
            safe_to_advance=True,
        )
    before = request.prior_projection_input_hashes.get(candidate_id)
    after = request.merged_projection_input_hashes.get(candidate_id)
    if before is None or after is None:
        return LateHitImpact(
            impact=LateHitImpactType.IMPACT_UNDETERMINED,
            candidate_id=candidate_id,
            new_recall_hit_ids=new_hit_ids,
            safe_to_advance=False,
        )
    if before == after:
        return LateHitImpact(
            impact=LateHitImpactType.NO_BLIND_INPUT_CHANGE,
            candidate_id=candidate_id,
            new_recall_hit_ids=new_hit_ids,
            prior_projection_input_sha256=before,
            merged_projection_input_sha256=after,
            safe_to_advance=True,
        )
    return LateHitImpact(
        impact=LateHitImpactType.RESEARCH_REFRESH_REQUIRED,
        candidate_id=candidate_id,
        new_recall_hit_ids=new_hit_ids,
        prior_projection_input_sha256=before,
        merged_projection_input_sha256=after,
        required_event=CandidateEventType.RESEARCH_REFRESH_REQUIRED,
        safe_to_advance=False,
    )


class RecallAggregator:
    def __init__(
        self,
        registry: ProviderRegistry,
        config: RecallAggregationConfig | None = None,
    ) -> None:
        self.registry = registry
        self.config = config or RecallAggregationConfig()

    def aggregate(self, request: RecallAggregationRequest) -> RecallAggregationOutcome:
        rejected: list[RejectedRecall] = []
        incoming: list[RecallHit] = []
        for batch in request.batches:
            for hit in batch.hits:
                reason = self.registry.validate_hit(
                    batch.provider_id,
                    hit,
                    include_book=request.include_book_providers,
                )
                if reason is None and hit.historical_only:
                    reason = "HISTORICAL_ONLY"
                if reason is None and hit.valid_until is not None and hit.valid_until <= request.as_of:
                    reason = "RECALL_EXPIRED"
                if reason is not None:
                    rejected.append(
                        RejectedRecall(
                            provider_id=batch.provider_id,
                            recall_hit_id=hit.record_id,
                            reason=reason,
                        )
                    )
                else:
                    incoming.append(hit)

        existing_by_market = {item.market_id: item for item in request.prior_candidates}
        prior_by_id = {item.record_id: item for item in request.prior_hits}
        for candidate in request.prior_candidates:
            missing = set(candidate.recall_hit_ids) - set(prior_by_id)
            if missing:
                raise ValueError(
                    f"prior hit payloads required to rebuild {candidate.candidate_id}: {sorted(missing)}"
                )

        all_hits = list(request.prior_hits) + incoming
        by_market: dict[str, list[RecallHit]] = {}
        for hit in all_hits:
            by_market.setdefault(hit.market_id, []).append(hit)

        results: list[CandidateMergeResult] = []
        for market_id in sorted(by_market):
            hits = by_market[market_id]
            chosen: dict[str, RecallHit] = {}
            duplicates: list[str] = []
            for hit in sorted(hits, key=lambda item: item.record_id):
                key = recall_dedupe_key(hit)
                if key in chosen:
                    duplicates.append(hit.record_id)
                else:
                    chosen[key] = hit
            accepted = tuple(sorted(chosen.values(), key=lambda item: item.record_id))
            if not accepted:
                continue
            candidate_id = stable_record_id("candidate", market_id)
            existing = existing_by_market.get(market_id)
            if existing is not None and existing.candidate_id != candidate_id:
                raise ValueError("prior candidate id does not match canonical market identity")
            prior_ids = set(existing.recall_hit_ids) if existing is not None else set()
            accepted_ids = tuple(item.record_id for item in accepted)
            new_ids = tuple(sorted(set(accepted_ids) - prior_ids))
            impact = _impact(candidate_id, existing, new_ids, request)
            score = sum((item.raw_score for item in accepted), Decimal("0"))
            recaller_diversity = len({item.recaller for item in accepted})
            rationale = tuple(
                sorted(
                    {
                        f"{item.recaller.value}:{reason}"
                        for item in accepted
                        for reason in item.reason_codes
                    }
                )
            )
            state = existing.state if existing is not None else CandidateState.CANDIDATE_MERGED
            if impact.impact in {
                LateHitImpactType.PRE_BLIND_REFRESH,
                LateHitImpactType.RESEARCH_REFRESH_REQUIRED,
            }:
                state = CandidateState.CANDIDATE_MERGED
            selected_at = max(item.observed_at for item in accepted)
            card_identity = {
                "candidate_id": candidate_id,
                "recall_hit_ids": accepted_ids,
                "recall_score": score,
                "state": state,
                "priority": _priority(score, recaller_diversity, self.config),
                "selected_at": selected_at,
                "aggregator_version": self.config.aggregator_version,
            }
            card = CandidateCard(
                schema_version=ALPHA_CONTRACT_VERSION,
                record_id=stable_record_id("candidate_card", card_identity),
                run_id=stable_record_id("candidate_run", card_identity),
                created_at=max(item.created_at for item in accepted),
                source="recall_aggregator",
                source_version=self.config.aggregator_version,
                provenance=(),
                extensions={},
                candidate_id=candidate_id,
                market_id=market_id,
                recall_hit_ids=accepted_ids,
                recall_score=score,
                dedup_group=market_id,
                selected_at=selected_at,
                state=state,
                research_priority=_priority(score, recaller_diversity, self.config),
                selection_rationale=rationale,
            )
            results.append(
                CandidateMergeResult(
                    candidate=card,
                    accepted_recall_hit_ids=accepted_ids,
                    duplicate_recall_hit_ids=tuple(sorted(duplicates)),
                    impact=impact,
                )
            )

        return RecallAggregationOutcome(
            request_sha256=content_sha256(request),
            results=tuple(results),
            rejected=tuple(sorted(rejected, key=lambda item: (item.provider_id, item.recall_hit_id))),
            active_provider_ids=tuple(
                item.provider_id
                for item in self.registry.active(include_book=request.include_book_providers)
            ),
        )
