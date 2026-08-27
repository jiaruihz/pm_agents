"""Pure compiler from provider-friendly research drafts to Alpha contracts.

External researchers supply ordinary source metadata, claims and *actual bytes*.
This module owns the one-way compilation into canonical Alpha identities; it
never accepts provider-declared identifiers or content hashes as authority.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
from types import MappingProxyType
from typing import Mapping, NamedTuple
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..contracts import (
    BlindResearchPacket, CaptureScope, ClaimEvidence, EstimateStage,
    EvidenceOrigin, EvidenceSupport, HashScope, MarketResearchPacket,
    PacketStage, ProbabilityEstimate, Replayability, ResearchResultEnvelope,
    SourceArtifact, SourceTier, blind_leak_reasons, bytes_sha256, stable_record_id,
)
from ..contracts.base import ensure_utc


DRAFT_COMPILER_VERSION = "p1_research_draft_v1"
ResearchPacketValue = BlindResearchPacket | MarketResearchPacket


class ResearchDraftError(ValueError):
    """A draft cannot safely be compiled into a canonical research result."""


class _DraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActualSourceBytes(_DraftModel):
    source_key: str = Field(min_length=1, max_length=128)
    content: bytes = Field(min_length=1)

    @field_validator("content", mode="before")
    @classmethod
    def _immutable_bytes_only(cls, value: object) -> bytes:
        if not isinstance(value, bytes):
            raise ValueError("actual source content must be immutable bytes")
        return value


class ArtifactBytesBinding(_DraftModel):
    """Canonical artifact id paired with the caller-provided immutable bytes."""

    artifact_id: str = Field(min_length=1)
    content: bytes = Field(min_length=1)


class DraftSource(_DraftModel):
    source_key: str = Field(min_length=1, max_length=128)
    source_name: str = Field(min_length=1)
    source_url_or_source_id: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    captured_at: datetime
    effective_as_of: datetime
    capture_scope: CaptureScope
    hash_scope: HashScope | None = None
    artifact_locator: str | None = None
    replayability: Replayability

    @field_validator("captured_at", "effective_as_of")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _capture_policy(self) -> "DraftSource":
        if self.effective_as_of > self.captured_at:
            raise ValueError("effective_as_of cannot be after captured_at")
        if self.capture_scope == CaptureScope.REFERENCE_ONLY:
            if self.hash_scope is not None or self.artifact_locator is not None or self.replayability != Replayability.REFERENCE_ONLY:
                raise ValueError("REFERENCE_ONLY cannot declare captured content")
        else:
            if self.hash_scope is None or not self.artifact_locator or not self.artifact_locator.strip():
                raise ValueError("captured source requires hash scope and immutable locator")
            if self.capture_scope == CaptureScope.FULL_DOCUMENT and (
                self.hash_scope == HashScope.CLAIM_EXCERPT or self.replayability != Replayability.FULL
            ):
                raise ValueError("FULL_DOCUMENT requires RAW_BYTES/NORMALIZED_TEXT and FULL replay")
            if self.capture_scope == CaptureScope.EXCERPT_ONLY and (
                self.hash_scope not in {HashScope.CLAIM_EXCERPT, HashScope.NORMALIZED_TEXT}
                or self.replayability != Replayability.EXCERPT
            ):
                raise ValueError("EXCERPT_ONLY requires excerpt/text hash scope and EXCERPT replay")
        return self


class DraftClaim(_DraftModel):
    source_key: str = Field(min_length=1, max_length=128)
    claim: str = Field(min_length=1)
    supports_yes_or_no: EvidenceSupport
    source_tier: SourceTier
    published_at: datetime | None = None
    accessed_at: datetime
    effective_as_of: datetime
    primary_or_secondary: str
    quotation_or_paraphrase_location: str = Field(min_length=1)
    confidence: Decimal = Field(ge=0, le=1)
    origin: EvidenceOrigin
    excerpt_context: str | None = None

    @field_validator("published_at", "accessed_at", "effective_as_of")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @field_validator("primary_or_secondary")
    @classmethod
    def _primary_secondary(cls, value: str) -> str:
        if value not in {"PRIMARY", "SECONDARY"}:
            raise ValueError("primary_or_secondary must be PRIMARY or SECONDARY")
        return value

    @model_validator(mode="after")
    def _clock_order(self) -> "DraftClaim":
        if self.effective_as_of > self.accessed_at or (self.published_at and self.published_at > self.accessed_at):
            raise ValueError("claim clocks are not point-in-time consistent")
        return self


class DraftEstimate(_DraftModel):
    model_type: str = Field(min_length=1)
    p_event_yes_low: Decimal = Field(ge=0, le=1)
    p_event_yes_mid: Decimal = Field(ge=0, le=1)
    p_event_yes_high: Decimal = Field(ge=0, le=1)
    p_market_yes_low: Decimal | None = Field(default=None, ge=0, le=1)
    p_market_yes_mid: Decimal | None = Field(default=None, ge=0, le=1)
    p_market_yes_high: Decimal | None = Field(default=None, ge=0, le=1)
    uncertainty_drivers: tuple[str, ...] = Field(min_length=1)
    assumptions: tuple[str, ...] = Field(min_length=1)
    model_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> "DraftEstimate":
        if not self.p_event_yes_low <= self.p_event_yes_mid <= self.p_event_yes_high:
            raise ValueError("event probability interval must be ordered")
        values = (self.p_market_yes_low, self.p_market_yes_mid, self.p_market_yes_high)
        if any(value is not None for value in values) and (any(value is None for value in values) or not values[0] <= values[1] <= values[2]):
            raise ValueError("market probability interval must be complete and ordered")
        return self


class ResearchDraft(_DraftModel):
    sources: tuple[DraftSource, ...] = Field(min_length=1)
    claims: tuple[DraftClaim, ...] = Field(min_length=1)
    estimate: DraftEstimate
    completed_at: datetime
    producer: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)

    @field_validator("completed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _keys_cover_claims(self) -> "ResearchDraft":
        keys = tuple(item.source_key for item in self.sources)
        if len(keys) != len(set(keys)):
            raise ValueError("source keys must be unique")
        if {item.source_key for item in self.claims} != set(keys):
            raise ValueError("draft sources must exactly cover claim source keys")
        return self


class CompiledResearchDraft(NamedTuple):
    result: ResearchResultEnvelope
    artifact_bytes: tuple[ArtifactBytesBinding, ...]

    @property
    def source_contents(self) -> Mapping[str, bytes]:
        """Read-only input mapping accepted directly by ``import_research_result``."""

        return MappingProxyType({item.artifact_id: item.content for item in self.artifact_bytes})


def _normalized_text_bytes(raw: bytes) -> bytes:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ResearchDraftError("NORMALIZED_TEXT source bytes must be UTF-8") from error
    return unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _content_hash(scope: HashScope, raw: bytes) -> str:
    return hashlib.sha256(_normalized_text_bytes(raw) if scope == HashScope.NORMALIZED_TEXT else raw).hexdigest()


def _freeze_packet(packet: ResearchPacketValue) -> ResearchPacketValue:
    if not isinstance(packet, (BlindResearchPacket, MarketResearchPacket)):
        raise ResearchDraftError("expected a released BlindResearchPacket or MarketResearchPacket")
    rebuilt = type(packet).model_validate(packet.model_dump(mode="python"))
    if rebuilt.canonical_sha256 != packet.canonical_sha256:
        raise ResearchDraftError("packet canonical replay mismatch")
    return rebuilt


def _freeze_draft(draft: ResearchDraft) -> ResearchDraft:
    if not isinstance(draft, ResearchDraft):
        raise ResearchDraftError("draft must be a ResearchDraft DTO")
    try:
        return ResearchDraft.model_validate(draft.model_dump(mode="python"))
    except (TypeError, ValueError) as error:
        raise ResearchDraftError(f"draft canonical replay failed: {error}") from error


def _require_market_blind_lineage(packet: MarketResearchPacket, blind: ResearchResultEnvelope | None) -> ResearchResultEnvelope:
    if not isinstance(blind, ResearchResultEnvelope):
        raise ResearchDraftError("Market compilation requires the exact accepted Blind result")
    rebuilt = ResearchResultEnvelope.model_validate(blind.model_dump(mode="python"))
    if rebuilt.canonical_sha256 != blind.canonical_sha256:
        raise ResearchDraftError("accepted Blind result canonical replay mismatch")
    refs = tuple(ref for ref in packet.provenance if ref.relation == "accepted_blind_result")
    if (
        rebuilt.packet_stage != PacketStage.BLIND
        or rebuilt.result_id != packet.blind_result_id
        or rebuilt.packet_id != packet.blind_packet_id
        or len(refs) != 1
        or refs[0].source_artifact_id != rebuilt.result_id
        or refs[0].content_sha256 != rebuilt.canonical_sha256
        or rebuilt.evidence != packet.blind_evidence
        or rebuilt.completed_at > packet.created_at
    ):
        raise ResearchDraftError("accepted Blind result does not bind Market packet provenance/evidence")
    return rebuilt


def compile_research_draft(
    *, packet: ResearchPacketValue, draft: ResearchDraft, actual_sources: tuple[ActualSourceBytes, ...],
    run_id: str, created_at: datetime, accepted_blind_result: ResearchResultEnvelope | None = None,
) -> CompiledResearchDraft:
    """Compile an untrusted provider draft without accepting its IDs or hashes."""

    frozen = _freeze_packet(packet)
    draft = _freeze_draft(draft)
    created_at = ensure_utc(created_at)
    if not run_id.strip():
        raise ResearchDraftError("run_id must not be blank")
    if created_at < frozen.created_at or draft.completed_at < frozen.created_at or draft.completed_at > created_at:
        raise ResearchDraftError("result clocks must follow packet and not follow compile clock")
    blind = isinstance(frozen, BlindResearchPacket)
    prior = None if blind else _require_market_blind_lineage(frozen, accepted_blind_result)
    actual_by_key: dict[str, bytes] = {}
    frozen_actuals: list[ActualSourceBytes] = []
    for item in actual_sources:
        if not isinstance(item, ActualSourceBytes):
            raise ResearchDraftError("actual source bindings must use ActualSourceBytes DTOs")
        try:
            item = ActualSourceBytes.model_validate(item.model_dump(mode="python"))
        except (TypeError, ValueError) as error:
            raise ResearchDraftError(f"actual source replay failed: {error}") from error
        if item.source_key in actual_by_key:
            raise ResearchDraftError("actual source keys must be unique")
        actual_by_key[item.source_key] = item.content
        frozen_actuals.append(item)
    expected_bytes = {item.source_key for item in draft.sources if item.capture_scope != CaptureScope.REFERENCE_ONLY}
    if set(actual_by_key) != expected_bytes:
        raise ResearchDraftError("actual source bytes must cover every captured source exactly once")
    if blind:
        leak_payload = draft.model_dump(mode="python")
        leaks = blind_leak_reasons(leak_payload)
        forbidden = {EvidenceOrigin.MARKET, EvidenceOrigin.WALLET, EvidenceOrigin.OPERATOR_COMMENTARY}
        if leaks or any(item.origin in forbidden for item in draft.claims):
            raise ResearchDraftError(f"Blind draft contains market-derived semantics: {leaks}")
        if any("polymarket" in item.source_url_or_source_id.lower() or "gamma" in item.source_url_or_source_id.lower() or "clob" in item.source_url_or_source_id.lower() for item in draft.sources):
            raise ResearchDraftError("Blind draft cannot use Polymarket/Gamma/CLOB sources")
        if any(item is not None for item in (draft.estimate.p_market_yes_low, draft.estimate.p_market_yes_mid, draft.estimate.p_market_yes_high)):
            raise ResearchDraftError("Blind draft cannot include market probabilities")

    artifacts_by_key: dict[str, SourceArtifact] = {}
    for source in draft.sources:
        if source.captured_at > draft.completed_at:
            raise ResearchDraftError("source capture cannot follow result completion")
        raw = actual_by_key.get(source.source_key)
        digest = None if raw is None else _content_hash(source.hash_scope, raw)  # type: ignore[arg-type]
        artifact_id = stable_record_id("source_artifact", "research_draft", source.model_dump(mode="python"), digest, len(raw or b""), run_id)
        artifacts_by_key[source.source_key] = SourceArtifact(
            record_id=artifact_id, run_id=run_id, created_at=created_at, source="research_draft_compiler", source_version=DRAFT_COMPILER_VERSION,
            provenance=(), extensions={}, artifact_id=artifact_id, source_name=source.source_name,
            source_url_or_source_id=source.source_url_or_source_id, media_type=source.media_type,
            captured_at=source.captured_at, effective_as_of=source.effective_as_of, capture_scope=source.capture_scope,
            hash_scope=source.hash_scope, content_sha256=digest, content_length_bytes=(None if raw is None else len(raw)),
            artifact_locator=source.artifact_locator, replayability=source.replayability,
        )
    evidence: list[ClaimEvidence] = []
    for claim in draft.claims:
        artifact = artifacts_by_key[claim.source_key]
        if claim.accessed_at > draft.completed_at or claim.effective_as_of > draft.completed_at or (claim.published_at and claim.published_at > draft.completed_at):
            raise ResearchDraftError("claim clocks cannot follow result completion")
        if artifact.capture_scope == CaptureScope.EXCERPT_ONLY:
            raw_text = _normalized_text_bytes(actual_by_key[claim.source_key]).decode("utf-8")
            if not claim.excerpt_context or _normalized_text_bytes(claim.excerpt_context.encode("utf-8")).decode("utf-8") not in raw_text:
                raise ResearchDraftError("EXCERPT_ONLY claim context must be frozen in actual source bytes")
        elif artifact.capture_scope == CaptureScope.FULL_DOCUMENT and claim.excerpt_context is not None:
            # Context is permitted for FULL_DOCUMENT but cannot carry a different source.
            if claim.excerpt_context not in _normalized_text_bytes(actual_by_key[claim.source_key]).decode("utf-8"):
                raise ResearchDraftError("excerpt_context must bind actual source bytes")
        evidence_id = stable_record_id("evidence", "research_draft", artifact.artifact_id, claim.model_dump(mode="python"), run_id)
        evidence.append(ClaimEvidence(
            record_id=evidence_id, run_id=run_id, created_at=created_at, source="research_draft_compiler", source_version=DRAFT_COMPILER_VERSION,
            provenance=(), extensions={}, evidence_id=evidence_id, claim=claim.claim, supports_yes_or_no=claim.supports_yes_or_no,
            source_tier=claim.source_tier, source_name=artifact.source_name, source_url_or_source_id=artifact.source_url_or_source_id,
            published_at=claim.published_at, accessed_at=claim.accessed_at, effective_as_of=claim.effective_as_of,
            primary_or_secondary=claim.primary_or_secondary, quotation_or_paraphrase_location=claim.quotation_or_paraphrase_location,
            confidence=claim.confidence, origin=claim.origin, source_artifact_id=artifact.artifact_id,
            capture_scope=artifact.capture_scope, hash_scope=artifact.hash_scope, content_sha256=artifact.content_sha256,
            excerpt_context=claim.excerpt_context, replayability=artifact.replayability,
        ))
    evidence_tuple = tuple(sorted(evidence, key=lambda item: item.evidence_id))
    artifacts = tuple(sorted(artifacts_by_key.values(), key=lambda item: item.artifact_id))
    blind_candidate_id = frozen.projection.blind_candidate_id if blind else prior.probability_estimate.blind_candidate_id  # type: ignore[union-attr]
    estimate_id = stable_record_id("probability_estimate", frozen.record_id, frozen.canonical_sha256, draft.estimate.model_dump(mode="python"), run_id)
    estimate = ProbabilityEstimate(
        record_id=estimate_id, run_id=run_id, created_at=created_at, source="research_draft_compiler", source_version=DRAFT_COMPILER_VERSION,
        provenance=(), extensions={}, market_id=None if blind else frozen.market_id, blind_candidate_id=blind_candidate_id,
        estimate_stage=EstimateStage.BLIND if blind else EstimateStage.FINAL, model_type=draft.estimate.model_type,
        p_event_yes_low=draft.estimate.p_event_yes_low, p_event_yes_mid=draft.estimate.p_event_yes_mid, p_event_yes_high=draft.estimate.p_event_yes_high,
        p_market_yes_low=None if blind else draft.estimate.p_market_yes_low, p_market_yes_mid=None if blind else draft.estimate.p_market_yes_mid,
        p_market_yes_high=None if blind else draft.estimate.p_market_yes_high, uncertainty_drivers=draft.estimate.uncertainty_drivers,
        assumptions=draft.estimate.assumptions, model_version=draft.estimate.model_version,
    )
    if not blind and any(value is None for value in (estimate.p_market_yes_low, estimate.p_market_yes_mid, estimate.p_market_yes_high)):
        raise ResearchDraftError("Market draft requires a complete market probability interval")
    result_id = stable_record_id("research_result", frozen.record_id, frozen.canonical_sha256, estimate.canonical_sha256, tuple(item.canonical_sha256 for item in evidence_tuple), tuple(item.canonical_sha256 for item in artifacts), draft.completed_at, draft.producer, draft.producer_version, run_id)
    result = ResearchResultEnvelope(
        record_id=result_id, run_id=run_id, created_at=created_at, source="research_draft_compiler", source_version=DRAFT_COMPILER_VERSION,
        provenance=(), extensions={}, result_id=result_id, packet_stage=frozen.packet_stage, packet_id=frozen.record_id,
        packet_sha256=frozen.canonical_sha256, probability_estimate=estimate, evidence=evidence_tuple, source_artifacts=artifacts,
        completed_at=draft.completed_at, producer=draft.producer, producer_version=draft.producer_version,
    )
    bindings = tuple(sorted(
        (ArtifactBytesBinding(artifact_id=artifacts_by_key[item.source_key].artifact_id, content=item.content)
         for item in frozen_actuals),
        key=lambda item: item.artifact_id,
    ))
    return CompiledResearchDraft(result, bindings)


__all__ = [
    "DRAFT_COMPILER_VERSION", "ActualSourceBytes", "ArtifactBytesBinding", "CompiledResearchDraft", "DraftClaim", "DraftEstimate",
    "DraftSource", "ResearchDraft", "ResearchDraftError", "compile_research_draft",
]
