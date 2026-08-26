"""Versioned Polymarket Alpha P0 domain contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, field_validator, model_validator

from .base import (
    AlphaContract,
    CommonEnvelope,
    canonical_data,
    content_sha256,
    ensure_utc,
    normalize_rule_text,
    rule_sha256,
    validate_sha256,
)


class MarketStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"


class MarketIdentity(AlphaContract):
    event_id: str
    market_id: str
    condition_id: str | None = None
    yes_token_id: str
    no_token_id: str

    @field_validator("event_id", "market_id", "yes_token_id", "no_token_id")
    @classmethod
    def identity_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("canonical identity values must not be blank")
        return value

    @field_validator("condition_id")
    @classmethod
    def optional_identity_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("condition_id must be null or non-blank")
        return value

    @model_validator(mode="after")
    def token_ids_are_distinct(self) -> "MarketIdentity":
        if self.yes_token_id == self.no_token_id:
            raise ValueError("YES and NO token ids must be distinct")
        return self


class MarketAlias(AlphaContract):
    market_id: str
    source: str
    alias_type: Literal["SLUG", "URL", "LEGACY_ID"]
    alias_value: str
    effective_from: datetime
    effective_to: datetime | None = None

    @field_validator("effective_from", "effective_to")
    @classmethod
    def times_are_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @model_validator(mode="after")
    def interval_is_ordered(self) -> "MarketAlias":
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


class MarketSnapshot(CommonEnvelope):
    identity: MarketIdentity
    title: str
    question: str
    slug: str | None = None
    status: MarketStatus
    end_at: datetime | None = None
    tags: tuple[str, ...] = ()
    rules_raw: str
    rules_normalized: str | None = None
    rule_hash: str | None = None
    volume: Decimal | None = Field(default=None, ge=0)
    liquidity: Decimal | None = Field(default=None, ge=0)
    source_observed_at: datetime
    ingested_at: datetime

    @model_validator(mode="before")
    @classmethod
    def derive_rule_revision(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "rules_raw" not in value:
            return value
        result = dict(value)
        result.setdefault("rules_normalized", normalize_rule_text(result["rules_raw"]))
        result.setdefault("rule_hash", rule_sha256(result["rules_raw"]))
        return result

    @field_validator("end_at", "source_observed_at", "ingested_at")
    @classmethod
    def times_are_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @model_validator(mode="after")
    def rule_revision_is_exact(self) -> "MarketSnapshot":
        if self.rules_normalized != normalize_rule_text(self.rules_raw):
            raise ValueError("rules_normalized does not match stable normalization")
        if self.rule_hash != rule_sha256(self.rules_raw):
            raise ValueError("rule_hash does not match normalized raw rules")
        if self.rule_hash is None:
            raise ValueError("rule_hash is required")
        validate_sha256(self.rule_hash)
        if self.ingested_at < self.source_observed_at:
            raise ValueError("ingested_at cannot precede source_observed_at")
        return self


class BookLevel(AlphaContract):
    price: Decimal = Field(ge=0, le=1)
    size: Decimal = Field(gt=0)


class BookLeg(AlphaContract):
    token_id: str
    bids: tuple[BookLevel, ...] = ()
    asks: tuple[BookLevel, ...] = ()

    @model_validator(mode="after")
    def levels_are_sorted_and_unique(self) -> "BookLeg":
        bid_prices = [item.price for item in self.bids]
        ask_prices = [item.price for item in self.asks]
        if bid_prices != sorted(bid_prices, reverse=True):
            raise ValueError("bids must be sorted descending")
        if ask_prices != sorted(ask_prices):
            raise ValueError("asks must be sorted ascending")
        if len(set(bid_prices)) != len(bid_prices) or len(set(ask_prices)) != len(ask_prices):
            raise ValueError("book levels must have unique prices per side")
        return self


class TargetDepthMetrics(AlphaContract):
    target_size: Decimal = Field(gt=0)
    buy_vwap: Decimal | None = Field(default=None, ge=0, le=1)
    sell_vwap: Decimal | None = Field(default=None, ge=0, le=1)
    buy_insufficient_depth: bool
    sell_insufficient_depth: bool


class OrderbookSnapshot(CommonEnvelope):
    identity: MarketIdentity
    capture_group_id: str
    captured_at: datetime
    source_observed_at: datetime
    yes_leg: BookLeg
    no_leg: BookLeg
    yes_depth: tuple[TargetDepthMetrics, ...] = ()
    no_depth: tuple[TargetDepthMetrics, ...] = ()
    stale: bool
    quality_flags: tuple[str, ...] = ()
    raw_artifact_ids: tuple[str, ...]

    @field_validator("captured_at", "source_observed_at")
    @classmethod
    def times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def legs_match_identity(self) -> "OrderbookSnapshot":
        if self.yes_leg.token_id != self.identity.yes_token_id:
            raise ValueError("YES leg does not match canonical token mapping")
        if self.no_leg.token_id != self.identity.no_token_id:
            raise ValueError("NO leg does not match canonical token mapping")
        if not self.raw_artifact_ids:
            raise ValueError("paired book requires raw artifact lineage")
        return self


class RecallerType(StrEnum):
    NEW_CHANGED = "NEW_CHANGED"
    STRUCTURAL_METADATA = "STRUCTURAL_METADATA"
    CONTROVERSY = "CONTROVERSY"
    SPECIALIST_WALLET = "SPECIALIST_WALLET"
    BOOK_ANOMALY = "BOOK_ANOMALY"


class RecallHit(CommonEnvelope):
    market_id: str
    recaller: RecallerType
    recaller_version: str
    reason_codes: tuple[str, ...]
    features: dict[str, Any] = Field(default_factory=dict)
    raw_score: Decimal = Field(ge=0)
    observed_at: datetime
    valid_until: datetime | None = None
    historical_only: bool = False

    @field_validator("observed_at", "valid_until")
    @classmethod
    def observed_at_is_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @field_validator("reason_codes")
    @classmethod
    def reasons_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("reason_codes must be non-empty and unique")
        return value

    @field_validator("features")
    @classmethod
    def features_are_canonical(cls, value: dict[str, Any]) -> dict[str, Any]:
        canonical_data(value)
        return value

    @model_validator(mode="after")
    def freshness_interval_is_ordered(self) -> "RecallHit":
        if self.valid_until is not None and self.valid_until <= self.observed_at:
            raise ValueError("valid_until must be after observed_at")
        return self


class ResearchPriority(StrEnum):
    CORE = "CORE"
    MICRO = "MICRO"
    WATCH = "WATCH"


class CandidateState(StrEnum):
    RECALLED = "RECALLED"
    CANDIDATE_MERGED = "CANDIDATE_MERGED"
    RULE_A_PASSED = "RULE_A_PASSED"
    RULE_A_BLOCKED = "RULE_A_BLOCKED"
    BLIND_PROJECTION_FROZEN = "BLIND_PROJECTION_FROZEN"
    BLIND_PACKET_FROZEN = "BLIND_PACKET_FROZEN"
    BLIND_RESULT_ACCEPTED = "BLIND_RESULT_ACCEPTED"
    BOOK_SNAPSHOT_ACCEPTED = "BOOK_SNAPSHOT_ACCEPTED"
    MARKET_PACKET_FROZEN = "MARKET_PACKET_FROZEN"
    MARKET_RESULT_ACCEPTED = "MARKET_RESULT_ACCEPTED"
    RULE_B_PASSED = "RULE_B_PASSED"
    RULE_B_RISK = "RULE_B_RISK"
    RULE_B_BLOCKED = "RULE_B_BLOCKED"
    RANKED = "RANKED"
    WATCHLISTED = "WATCHLISTED"
    REJECTED = "REJECTED"
    SIMULATION_RECORDED = "SIMULATION_RECORDED"


class CandidateEventType(StrEnum):
    STATE_TRANSITION = "STATE_TRANSITION"
    RECALL_EXPIRED = "RECALL_EXPIRED"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    RESEARCH_REFRESH_REQUIRED = "RESEARCH_REFRESH_REQUIRED"
    RULE_REVISION_INVALIDATED = "RULE_REVISION_INVALIDATED"
    BOOK_REFRESH_REQUIRED = "BOOK_REFRESH_REQUIRED"
    MARKET_CLOSED = "MARKET_CLOSED"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class CandidateCard(CommonEnvelope):
    candidate_id: str
    market_id: str
    recall_hit_ids: tuple[str, ...]
    recall_score: Decimal = Field(ge=0)
    dedup_group: str
    selected_at: datetime
    state: CandidateState = CandidateState.RECALLED
    research_priority: ResearchPriority
    selection_rationale: tuple[str, ...]

    @field_validator("selected_at")
    @classmethod
    def selected_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("recall_hit_ids", "selection_rationale")
    @classmethod
    def non_empty_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("values must be non-empty and unique")
        return value

    @model_validator(mode="after")
    def candidate_identity_and_revision_are_distinct(self) -> "CandidateCard":
        if not re.fullmatch(r"candidate:[0-9a-f]{64}", self.candidate_id):
            raise ValueError("candidate_id must be a stable opaque candidate hash")
        if not re.fullmatch(r"candidate_card:[0-9a-f]{64}", self.record_id):
            raise ValueError("CandidateCard record_id must be an append-only revision hash")
        return self


class CandidateTransition(CommonEnvelope):
    candidate_id: str
    event_type: CandidateEventType
    from_state: CandidateState
    to_state: CandidateState
    reason: str
    actor: str
    at: datetime
    related_artifact_ids: tuple[str, ...]
    input_hash: str

    @field_validator("at")
    @classmethod
    def at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("input_hash")
    @classmethod
    def input_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def state_change_is_explicit(self) -> "CandidateTransition":
        if self.event_type == CandidateEventType.STATE_TRANSITION and self.from_state == self.to_state:
            raise ValueError("STATE_TRANSITION must change state")
        if not self.related_artifact_ids:
            raise ValueError("transition requires related artifact lineage")
        return self


class RuleGate(StrEnum):
    PASS = "PASS"
    WATCH_RULE = "WATCH_RULE"
    REJECT_RULE = "REJECT_RULE"


class ThresholdOperator(StrEnum):
    EQ = "EQ"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    BETWEEN = "BETWEEN"
    TEXTUAL = "TEXTUAL"


class ThresholdSpec(AlphaContract):
    operator: ThresholdOperator
    value: Decimal | str
    upper_value: Decimal | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def between_has_upper_bound(self) -> "ThresholdSpec":
        if self.operator == ThresholdOperator.BETWEEN and self.upper_value is None:
            raise ValueError("BETWEEN threshold requires upper_value")
        return self


class RuleContract(CommonEnvelope):
    market_id: str
    rule_hash: str
    contract_revision_id: str
    contract_corpus_sha256: str | None = None
    subject_entity: str
    entity_match_rule: str
    yes_trigger: str
    threshold: ThresholdSpec | None = None
    deadline: datetime | None = None
    timezone: str
    resolution_sources: tuple[str, ...]
    source_precedence: tuple[str, ...]
    initial_or_final: Literal["INITIAL", "FINAL", "BOTH", "UNSPECIFIED"]
    qualifying_examples: tuple[str, ...] = ()
    non_qualifying_examples: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()
    clarity_score: Decimal = Field(ge=0, le=1)
    rule_gate: RuleGate
    parser_version: str

    @field_validator("rule_hash", "contract_corpus_sha256")
    @classmethod
    def hashes_are_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("deadline")
    @classmethod
    def deadline_is_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None


class EvidenceSupport(StrEnum):
    YES = "YES"
    NO = "NO"
    NEUTRAL = "NEUTRAL"


class SourceTier(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class EvidenceOrigin(StrEnum):
    PRIMARY_SOURCE = "PRIMARY_SOURCE"
    SECONDARY_SOURCE = "SECONDARY_SOURCE"
    RULE_CONTRACT = "RULE_CONTRACT"
    WALLET = "WALLET"
    MARKET = "MARKET"
    OPERATOR_COMMENTARY = "OPERATOR_COMMENTARY"


class CaptureScope(StrEnum):
    FULL_DOCUMENT = "FULL_DOCUMENT"
    EXCERPT_ONLY = "EXCERPT_ONLY"
    REFERENCE_ONLY = "REFERENCE_ONLY"


class HashScope(StrEnum):
    RAW_BYTES = "RAW_BYTES"
    NORMALIZED_TEXT = "NORMALIZED_TEXT"
    CLAIM_EXCERPT = "CLAIM_EXCERPT"


class Replayability(StrEnum):
    FULL = "FULL"
    EXCERPT = "EXCERPT"
    REFERENCE_ONLY = "REFERENCE_ONLY"


class ClaimEvidence(CommonEnvelope):
    evidence_id: str
    entity_id: str | None = None
    claim: str
    supports_yes_or_no: EvidenceSupport
    source_tier: SourceTier
    source_name: str
    source_url_or_source_id: str
    published_at: datetime | None = None
    accessed_at: datetime
    effective_as_of: datetime
    primary_or_secondary: Literal["PRIMARY", "SECONDARY"]
    quotation_or_paraphrase_location: str
    confidence: Decimal = Field(ge=0, le=1)
    origin: EvidenceOrigin
    source_artifact_id: str
    capture_scope: CaptureScope
    hash_scope: HashScope | None = None
    content_sha256: str | None = None
    excerpt_context: str | None = None
    replayability: Replayability

    @field_validator("published_at", "accessed_at", "effective_as_of")
    @classmethod
    def evidence_times_are_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @field_validator("content_sha256")
    @classmethod
    def content_hash_is_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def source_capture_semantics_are_consistent(self) -> "ClaimEvidence":
        if self.evidence_id != self.record_id:
            raise ValueError("evidence_id must equal record_id")
        if self.effective_as_of > self.accessed_at:
            raise ValueError("effective_as_of cannot be after accessed_at")
        if self.published_at is not None and self.published_at > self.accessed_at:
            raise ValueError("published_at cannot be after accessed_at")
        if self.capture_scope == CaptureScope.REFERENCE_ONLY:
            if self.content_sha256 is not None or self.hash_scope is not None:
                raise ValueError("REFERENCE_ONLY cannot claim captured content hash")
            if self.replayability != Replayability.REFERENCE_ONLY:
                raise ValueError("REFERENCE_ONLY evidence is not content-replayable")
        else:
            if self.content_sha256 is None or self.hash_scope is None:
                raise ValueError("captured evidence requires hash_scope and content_sha256")
            if self.capture_scope == CaptureScope.FULL_DOCUMENT:
                if self.hash_scope == HashScope.CLAIM_EXCERPT:
                    raise ValueError("FULL_DOCUMENT cannot use CLAIM_EXCERPT hash scope")
                if self.replayability != Replayability.FULL:
                    raise ValueError("FULL_DOCUMENT must be fully replayable")
            if self.capture_scope == CaptureScope.EXCERPT_ONLY:
                if self.hash_scope not in {HashScope.CLAIM_EXCERPT, HashScope.NORMALIZED_TEXT}:
                    raise ValueError("EXCERPT_ONLY requires excerpt/text hash scope")
                if not self.excerpt_context or not self.excerpt_context.strip():
                    raise ValueError("EXCERPT_ONLY requires frozen excerpt_context")
                if self.replayability != Replayability.EXCERPT:
                    raise ValueError("EXCERPT_ONLY replayability must be EXCERPT")
        return self


_BLIND_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("polymarket", re.compile(r"polymarket|gamma[-_. ]?api|clob", re.I)),
    ("market_url", re.compile(r"https?://(?:www\.)?polymarket\.com/\S+|\bmarket\s+url\b", re.I)),
    ("slug", re.compile(r"\bslug\s*[:=]", re.I)),
    ("price", re.compile(r"\b(bid|ask|order\s*book|orderbook|odds|market\s+price|implied\s+probability)\b", re.I)),
    ("direction", re.compile(r"\b(buy|sell|long|short)\s+(yes|no)\b|\b(yes|no)\s+(is\s+)?(under|over)priced\b", re.I)),
    ("wallet", re.compile(r"\b(wallet|whale|smart\s*money|trader\s+position)\b", re.I)),
    (
        "operator_market_commentary",
        re.compile(r"\b(the\s+market\s+(?:thinks|giv(?:e|es)|prices)|priced\s+at)\b", re.I),
    ),
)

_OPAQUE_BLIND_ID_RE = re.compile(
    r"^(?:blind_candidate|blind_question|blind_packet|blind_run|evidence|source_artifact):[0-9a-f]{64}$"
)
_APPROVED_BLIND_QUESTION_TEMPLATES = frozenset(
    {
        "BASE_RATE",
        "DEADLINE_STATUS",
        "DISCONFIRMING_EVIDENCE",
        "ENTITY_STATUS",
        "RULE_TRIGGER_EVIDENCE",
        "SOURCE_CONFLICT",
    }
)
_QUESTION_ODDS_RE = re.compile(
    r"\b(?:odds?|probability|chance)\s*(?:of|is|at|=|:)?\s*\d+(?:\.\d+)?\s*%|\b\d+(?:\.\d+)?\s*%\s*(?:odds?|probability|chance)",
    re.I,
)


def _require_opaque_blind_id(value: str) -> str:
    value = value.strip()
    if not _OPAQUE_BLIND_ID_RE.fullmatch(value):
        raise ValueError("Blind identifiers must be namespaced opaque SHA-256 ids")
    return value


def blind_leak_reasons(value: Any, *, path: str = "$") -> tuple[str, ...]:
    """Recursively scan every nested string for market-derived semantics."""

    reasons: list[str] = []
    if isinstance(value, str):
        for name, pattern in _BLIND_LEAK_PATTERNS:
            if pattern.search(value):
                reasons.append(f"{path}:{name}")
    elif isinstance(value, dict):
        for key, item in value.items():
            reasons.extend(blind_leak_reasons(item, path=f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            reasons.extend(blind_leak_reasons(item, path=f"{path}[{index}]"))
    return tuple(reasons)


class BlindResearchQuestion(AlphaContract):
    question_id: str
    template_id: str
    generated_from_rule_contract_hash: str
    generated_from_evidence_ids: tuple[str, ...] = ()
    text: str

    @field_validator("generated_from_rule_contract_hash")
    @classmethod
    def rule_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("question_id")
    @classmethod
    def question_id_is_opaque(cls, value: str) -> str:
        value = _require_opaque_blind_id(value)
        if not value.startswith("blind_question:"):
            raise ValueError("question_id must use blind_question namespace")
        return value

    @field_validator("generated_from_evidence_ids")
    @classmethod
    def evidence_ids_are_opaque(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("generated_from_evidence_ids must be unique")
        for item in value:
            if not _require_opaque_blind_id(item).startswith("evidence:"):
                raise ValueError("question evidence ids must use evidence namespace")
        return value

    @model_validator(mode="after")
    def question_is_template_bounded(self) -> "BlindResearchQuestion":
        if self.template_id not in _APPROVED_BLIND_QUESTION_TEMPLATES:
            raise ValueError("blind questions must use an approved neutral template")
        if _QUESTION_ODDS_RE.search(self.text):
            raise ValueError("blind question cannot embed odds or market probability")
        leaks = blind_leak_reasons(self.model_dump(mode="python"))
        if leaks:
            raise ValueError(f"blind question contains market-derived semantics: {leaks}")
        return self


class BlindCandidateProjection(CommonEnvelope):
    blind_candidate_id: str
    rule_contract_hash: str
    neutral_proposition: str
    subject_entity: str
    deadline: datetime | None = None
    research_questions: tuple[BlindResearchQuestion, ...]
    evidence: tuple[ClaimEvidence, ...] = ()

    @field_validator("rule_contract_hash")
    @classmethod
    def rule_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("blind_candidate_id")
    @classmethod
    def blind_candidate_id_is_opaque(cls, value: str) -> str:
        value = _require_opaque_blind_id(value)
        if not value.startswith("blind_candidate:"):
            raise ValueError("blind_candidate_id must use blind_candidate namespace")
        return value

    @field_validator("deadline")
    @classmethod
    def deadline_is_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @model_validator(mode="after")
    def projection_has_no_market_or_wallet_semantics(self) -> "BlindCandidateProjection":
        if self.record_id != self.blind_candidate_id:
            raise ValueError("Blind projection record_id must equal blind_candidate_id")
        if not _require_opaque_blind_id(self.run_id).startswith("blind_run:"):
            raise ValueError("Blind projection run_id must use blind_run namespace")
        if self.source != "blind_projection_builder":
            raise ValueError("Blind projection source must be the controlled builder")
        if self.provenance:
            raise ValueError("Blind projection envelope cannot carry unprojected provenance")
        if not self.research_questions:
            raise ValueError("blind projection requires controlled research questions")
        forbidden_origins = {
            EvidenceOrigin.WALLET,
            EvidenceOrigin.MARKET,
            EvidenceOrigin.OPERATOR_COMMENTARY,
        }
        if any(item.origin in forbidden_origins for item in self.evidence):
            raise ValueError("wallet, market and operator commentary cannot enter Blind")
        for item in self.evidence:
            if item.record_id != item.evidence_id:
                raise ValueError("Blind evidence record_id must equal evidence_id")
            if not _require_opaque_blind_id(item.evidence_id).startswith("evidence:"):
                raise ValueError("Blind evidence must use opaque evidence ids")
            if re.search(r"polymarket|gamma[-_. ]?api|clob", item.source_url_or_source_id, re.I):
                raise ValueError("Polymarket/Gamma/CLOB sources are forbidden in Blind")
        dump = self.model_dump(mode="python")
        leaks = blind_leak_reasons(dump)
        if leaks:
            raise ValueError(f"blind projection contains market-derived semantics: {leaks}")
        return self


class BlindRuleView(AlphaContract):
    """Rule semantics allowed into Blind, with all market identity removed."""

    rule_hash: str
    subject_entity: str
    entity_match_rule: str
    yes_trigger: str
    threshold: ThresholdSpec | None = None
    deadline: datetime | None = None
    timezone: str
    resolution_sources: tuple[str, ...]
    source_precedence: tuple[str, ...]
    initial_or_final: Literal["INITIAL", "FINAL", "BOTH", "UNSPECIFIED"]
    qualifying_examples: tuple[str, ...] = ()
    non_qualifying_examples: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()
    clarity_score: Decimal = Field(ge=0, le=1)
    parser_version: str

    @field_validator("rule_hash")
    @classmethod
    def rule_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("deadline")
    @classmethod
    def deadline_is_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @model_validator(mode="after")
    def market_sources_are_forbidden(self) -> "BlindRuleView":
        leaks = blind_leak_reasons(
            {"resolution_sources": self.resolution_sources, "source_precedence": self.source_precedence}
        )
        if leaks:
            raise ValueError(f"Blind rule view contains forbidden market sources: {leaks}")
        return self

    @classmethod
    def from_rule_contract(cls, contract: RuleContract) -> "BlindRuleView":
        return cls.model_validate(
            contract.model_dump(
                mode="python",
                include={
                    "rule_hash",
                    "subject_entity",
                    "entity_match_rule",
                    "yes_trigger",
                    "threshold",
                    "deadline",
                    "timezone",
                    "resolution_sources",
                    "source_precedence",
                    "initial_or_final",
                    "qualifying_examples",
                    "non_qualifying_examples",
                    "ambiguities",
                    "clarity_score",
                    "parser_version",
                },
            )
        )


class PacketStage(StrEnum):
    BLIND = "BLIND"
    MARKET_AWARE = "MARKET_AWARE"


class BlindResearchPacket(CommonEnvelope):
    packet_stage: Literal[PacketStage.BLIND] = PacketStage.BLIND
    projection: BlindCandidateProjection
    blind_rule: BlindRuleView

    @model_validator(mode="after")
    def rule_revision_matches_projection(self) -> "BlindResearchPacket":
        if not _require_opaque_blind_id(self.record_id).startswith("blind_packet:"):
            raise ValueError("Blind packet record_id must use blind_packet namespace")
        if not _require_opaque_blind_id(self.run_id).startswith("blind_run:"):
            raise ValueError("Blind packet run_id must use blind_run namespace")
        if self.source != "blind_packet_builder":
            raise ValueError("Blind packet source must be the controlled builder")
        if self.provenance:
            raise ValueError("Blind packet envelope cannot carry unprojected provenance")
        if self.projection.rule_contract_hash != self.blind_rule.rule_hash:
            raise ValueError("Blind projection and BlindRuleView hashes must match")
        return self


class MarketResearchPacket(CommonEnvelope):
    packet_stage: Literal[PacketStage.MARKET_AWARE] = PacketStage.MARKET_AWARE
    candidate_id: str
    market_id: str
    blind_packet_id: str
    blind_result_id: str
    rule_contract: RuleContract
    blind_evidence: tuple[ClaimEvidence, ...]
    orderbook: OrderbookSnapshot

    @model_validator(mode="after")
    def market_identity_is_consistent(self) -> "MarketResearchPacket":
        if self.market_id != self.rule_contract.market_id:
            raise ValueError("Market packet and RuleContract market ids must match")
        if self.market_id != self.orderbook.identity.market_id:
            raise ValueError("Market packet and Orderbook market ids must match")
        return self


ResearchPacket = Annotated[
    BlindResearchPacket | MarketResearchPacket,
    Field(discriminator="packet_stage"),
]
RESEARCH_PACKET_ADAPTER = TypeAdapter(ResearchPacket)


class EstimateStage(StrEnum):
    BLIND = "BLIND"
    POST_RED_TEAM = "POST_RED_TEAM"
    FINAL = "FINAL"


class ProbabilityEstimate(CommonEnvelope):
    market_id: str | None = None
    blind_candidate_id: str
    estimate_stage: EstimateStage
    model_type: str
    p_event_yes_low: Decimal = Field(ge=0, le=1)
    p_event_yes_mid: Decimal = Field(ge=0, le=1)
    p_event_yes_high: Decimal = Field(ge=0, le=1)
    p_market_yes_low: Decimal | None = Field(default=None, ge=0, le=1)
    p_market_yes_mid: Decimal | None = Field(default=None, ge=0, le=1)
    p_market_yes_high: Decimal | None = Field(default=None, ge=0, le=1)
    uncertainty_drivers: tuple[str, ...]
    assumptions: tuple[str, ...]
    model_version: str

    @model_validator(mode="after")
    def probability_intervals_and_blindness_hold(self) -> "ProbabilityEstimate":
        if not (self.p_event_yes_low <= self.p_event_yes_mid <= self.p_event_yes_high):
            raise ValueError("event probability interval must be ordered")
        market_values = (
            self.p_market_yes_low,
            self.p_market_yes_mid,
            self.p_market_yes_high,
        )
        if self.estimate_stage == EstimateStage.BLIND:
            if self.market_id is not None or any(item is not None for item in market_values):
                raise ValueError("BLIND estimate cannot contain market identity/probability")
        else:
            if self.market_id is None or any(item is None for item in market_values):
                raise ValueError("market-aware estimate requires market identity/probability")
            assert all(item is not None for item in market_values)
            if not (market_values[0] <= market_values[1] <= market_values[2]):
                raise ValueError("market probability interval must be ordered")
        return self


class RuleGateB(StrEnum):
    PASS = "PASS"
    PASS_WITH_RULE_RISK = "PASS_WITH_RULE_RISK"
    BLOCK = "BLOCK"


class ReviewAction(StrEnum):
    REJECT_RULE = "REJECT_RULE"
    REJECT_EVIDENCE = "REJECT_EVIDENCE"
    REJECT_EDGE = "REJECT_EDGE"
    REJECT_LIQUIDITY = "REJECT_LIQUIDITY"
    WATCH = "WATCH"
    SIMULATE = "SIMULATE"


class DecisionDirection(StrEnum):
    YES = "YES"
    NO = "NO"
    NONE = "NONE"


class ReviewDecision(CommonEnvelope):
    market_id: str
    rule_hash: str
    rule_gate_b: RuleGateB
    action: ReviewAction
    direction: DecisionDirection
    conservative_probability: Decimal | None = Field(default=None, ge=0, le=1)
    net_edge: Decimal | None = None
    target_size: Decimal | None = Field(default=None, ge=0)
    execution: Literal["NO_ORDER"] = "NO_ORDER"
    blocking_reasons: tuple[str, ...] = ()
    input_artifact_ids: tuple[str, ...]

    @field_validator("rule_hash")
    @classmethod
    def rule_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)


class PositionState(StrEnum):
    NO_POSITION = "NO_POSITION"
    SIMULATED = "SIMULATED"


class PredictionRecord(CommonEnvelope):
    prediction_id: str
    market_id: str
    rule_hash: str
    packet_id: str
    probability_estimate_id: str
    orderbook_snapshot_id: str
    decision_id: str
    position_state: PositionState
    final_resolution: Literal["YES", "NO", "INVALID"] | None = None
    resolved_at: datetime | None = None
    simulated_pnl: Decimal | None = None
    review_notes: tuple[str, ...] = ()

    @field_validator("rule_hash")
    @classmethod
    def rule_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("resolved_at")
    @classmethod
    def resolved_at_is_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @model_validator(mode="after")
    def resolution_fields_are_consistent(self) -> "PredictionRecord":
        if self.prediction_id != self.record_id:
            raise ValueError("prediction_id must equal record_id")
        if (self.final_resolution is None) != (self.resolved_at is None):
            raise ValueError("final_resolution and resolved_at must be set together")
        return self


CONTRACT_MODELS = (
    MarketIdentity,
    MarketAlias,
    MarketSnapshot,
    BookLevel,
    BookLeg,
    TargetDepthMetrics,
    OrderbookSnapshot,
    RecallHit,
    CandidateCard,
    CandidateTransition,
    ThresholdSpec,
    RuleContract,
    ClaimEvidence,
    BlindResearchQuestion,
    BlindCandidateProjection,
    BlindRuleView,
    BlindResearchPacket,
    MarketResearchPacket,
    ProbabilityEstimate,
    ReviewDecision,
    PredictionRecord,
)


def contract_schema_bundle() -> dict[str, Any]:
    return {model.__name__: model.model_json_schema() for model in CONTRACT_MODELS}


def contract_schema_fingerprint() -> str:
    encoded = json.dumps(
        contract_schema_bundle(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
