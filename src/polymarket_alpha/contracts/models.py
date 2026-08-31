"""Versioned Polymarket Alpha P0 domain contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, field_validator, model_validator

from .base import (
    AlphaContract,
    CommonEnvelope,
    canonical_data,
    bytes_sha256,
    content_sha256,
    ensure_utc,
    normalize_rule_text,
    rule_sha256,
    stable_record_id,
    validate_sha256,
)


def _relative_artifact_locator(value: str) -> str:
    value = value.strip()
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("artifact locator must be a safe relative POSIX path")
    return str(path)


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


class MarketChangeType(StrEnum):
    NEW = "NEW"
    RULE_CHANGED = "RULE_CHANGED"
    LIFECYCLE_CHANGED = "LIFECYCLE_CHANGED"
    CLOSED = "CLOSED"
    RESOLVED = "RESOLVED"
    METADATA_CHANGED = "METADATA_CHANGED"
    FAMILY_CHANGED = "FAMILY_CHANGED"


class MarketChangeEvent(CommonEnvelope):
    change_event_id: str
    market_id: str
    previous_snapshot_id: str | None = None
    current_snapshot_id: str
    previous_snapshot_sha256: str | None = None
    current_snapshot_sha256: str
    previous_status: MarketStatus | None = None
    current_status: MarketStatus
    previous_rule_hash: str | None = None
    current_rule_hash: str
    change_types: tuple[MarketChangeType, ...]
    changed_fields: tuple[str, ...]
    effective_at: datetime
    detected_at: datetime

    @field_validator(
        "previous_snapshot_sha256",
        "current_snapshot_sha256",
        "previous_rule_hash",
        "current_rule_hash",
    )
    @classmethod
    def hashes_are_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("effective_at", "detected_at")
    @classmethod
    def times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("change_types")
    @classmethod
    def change_types_are_canonical(
        cls, value: tuple[MarketChangeType, ...]
    ) -> tuple[MarketChangeType, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("change_types must be non-empty and unique")
        if value != tuple(sorted(value, key=lambda item: item.value)):
            raise ValueError("change_types must use canonical lexical ordering")
        return value

    @field_validator("changed_fields")
    @classmethod
    def changed_fields_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if not normalized or any(not item for item in normalized):
            raise ValueError("changed_fields must be non-empty")
        if len(normalized) != len(set(normalized)) or normalized != tuple(sorted(normalized)):
            raise ValueError("changed_fields must be unique and lexically sorted")
        return normalized

    @model_validator(mode="after")
    def change_lineage_is_consistent(self) -> "MarketChangeEvent":
        if self.change_event_id != self.record_id:
            raise ValueError("change_event_id must equal record_id")
        if not re.fullmatch(r"market_change:[0-9a-f]{64}", self.record_id):
            raise ValueError("MarketChangeEvent id must use market_change namespace")
        if self.detected_at < self.effective_at:
            raise ValueError("detected_at cannot precede effective_at")
        previous_values = (
            self.previous_snapshot_id,
            self.previous_snapshot_sha256,
            self.previous_status,
            self.previous_rule_hash,
        )
        is_new = MarketChangeType.NEW in self.change_types
        if is_new:
            if self.change_types != (MarketChangeType.NEW,):
                raise ValueError("NEW must be emitted as a standalone first-observation event")
            if any(item is not None for item in previous_values):
                raise ValueError("NEW event cannot claim a previous snapshot")
        elif any(item is None for item in previous_values):
            raise ValueError("non-NEW event requires complete previous snapshot lineage")
        if self.previous_snapshot_id == self.current_snapshot_id:
            raise ValueError("a logical change requires a new snapshot revision")
        if MarketChangeType.CLOSED in self.change_types and self.current_status != MarketStatus.CLOSED:
            raise ValueError("CLOSED change requires CLOSED current_status")
        if MarketChangeType.RESOLVED in self.change_types and self.current_status != MarketStatus.RESOLVED:
            raise ValueError("RESOLVED change requires RESOLVED current_status")
        lifecycle_changed = MarketChangeType.LIFECYCLE_CHANGED in self.change_types
        if not is_new and (self.previous_status != self.current_status) != lifecycle_changed:
            raise ValueError("status change and LIFECYCLE_CHANGED must be emitted together")
        if (
            MarketChangeType.CLOSED in self.change_types
            or MarketChangeType.RESOLVED in self.change_types
        ) and not lifecycle_changed:
            raise ValueError("CLOSED/RESOLVED requires LIFECYCLE_CHANGED")
        if self.current_status == MarketStatus.SUPERSEDED:
            raise ValueError("SUPERSEDED is unreachable until a source-of-truth is released")
        if MarketChangeType.RULE_CHANGED in self.change_types:
            if self.previous_rule_hash == self.current_rule_hash:
                raise ValueError("RULE_CHANGED requires a changed normalized rule hash")
        elif self.previous_rule_hash is not None and self.previous_rule_hash != self.current_rule_hash:
            raise ValueError("changed rule hash requires RULE_CHANGED")
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


class BookCapturePurpose(StrEnum):
    SENSING = "SENSING"
    FORMAL_REVIEW = "FORMAL_REVIEW"


class BookCaptureStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    STALE = "STALE"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class BookCaptureDemand(CommonEnvelope):
    demand_id: str
    identity: MarketIdentity
    purpose: BookCapturePurpose
    trigger_artifact_id: str
    trigger_artifact_sha256: str
    blind_result_id: str | None = None
    requested_at: datetime
    valid_until: datetime
    max_staleness_seconds: int = Field(gt=0)
    target_sizes: tuple[Decimal, ...]

    @field_validator("trigger_artifact_sha256")
    @classmethod
    def trigger_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("requested_at", "valid_until")
    @classmethod
    def times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("target_sizes")
    @classmethod
    def target_sizes_are_canonical(cls, value: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        if not value or any(item <= 0 for item in value):
            raise ValueError("target_sizes must be non-empty and positive")
        if len(value) != len(set(value)) or value != tuple(sorted(value)):
            raise ValueError("target_sizes must be unique and sorted ascending")
        return value

    @model_validator(mode="after")
    def demand_purpose_and_lineage_are_consistent(self) -> "BookCaptureDemand":
        if self.demand_id != self.record_id:
            raise ValueError("demand_id must equal record_id")
        if not re.fullmatch(r"book_demand:[0-9a-f]{64}", self.record_id):
            raise ValueError("BookCaptureDemand id must use book_demand namespace")
        if self.valid_until <= self.requested_at:
            raise ValueError("valid_until must be after requested_at")
        if self.purpose == BookCapturePurpose.FORMAL_REVIEW:
            if self.blind_result_id is None:
                raise ValueError("FORMAL_REVIEW demand requires an accepted blind_result_id")
            if not re.fullmatch(r"research_result:[0-9a-f]{64}", self.blind_result_id):
                raise ValueError("blind_result_id must use research_result namespace")
            if self.trigger_artifact_id != self.blind_result_id:
                raise ValueError("FORMAL_REVIEW trigger must be the accepted blind result")
        elif self.blind_result_id is not None:
            raise ValueError("SENSING demand cannot be triggered by a blind result")
        elif not re.fullmatch(r"market_change:[0-9a-f]{64}", self.trigger_artifact_id):
            raise ValueError("SENSING trigger must be a MarketChangeEvent")
        return self


class BookCaptureReceipt(CommonEnvelope):
    receipt_id: str
    demand_id: str
    demand_sha256: str
    market_id: str
    purpose: BookCapturePurpose
    status: BookCaptureStatus
    capture_owner: str
    received_at: datetime
    orderbook_snapshot_id: str | None = None
    orderbook_snapshot_sha256: str | None = None
    capture_group_id: str | None = None
    source_observed_at: datetime | None = None
    error_code: str | None = None

    @field_validator("demand_sha256", "orderbook_snapshot_sha256")
    @classmethod
    def hashes_are_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("received_at", "source_observed_at")
    @classmethod
    def times_are_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @model_validator(mode="after")
    def receipt_state_is_consistent(self) -> "BookCaptureReceipt":
        if self.receipt_id != self.record_id:
            raise ValueError("receipt_id must equal record_id")
        if not re.fullmatch(r"book_receipt:[0-9a-f]{64}", self.record_id):
            raise ValueError("BookCaptureReceipt id must use book_receipt namespace")
        snapshot_fields = (
            self.orderbook_snapshot_id,
            self.orderbook_snapshot_sha256,
            self.capture_group_id,
            self.source_observed_at,
        )
        if self.status == BookCaptureStatus.ACCEPTED:
            if any(item is None for item in snapshot_fields):
                raise ValueError("ACCEPTED receipt requires complete paired-book lineage")
            if self.error_code is not None:
                raise ValueError("ACCEPTED receipt cannot contain an error_code")
        elif self.status == BookCaptureStatus.STALE:
            if any(item is None for item in snapshot_fields) or not self.error_code:
                raise ValueError("STALE receipt requires book lineage and an error_code")
        else:
            if any(item is not None for item in snapshot_fields):
                raise ValueError("non-capture receipt cannot claim book lineage")
            if not self.error_code or not self.error_code.strip():
                raise ValueError("non-accepted receipt requires an error_code")
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


# Gate R WP1 contracts.  These deliberately live beside Candidate rather than
# in a second orchestration schema: the generic contract ledger remains the
# only persistence owner.
class CandidateEligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    MARKET_CLOSED = "MARKET_CLOSED"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"
    DEADLINE_ELAPSED = "DEADLINE_ELAPSED"
    DUPLICATE = "DUPLICATE"
    REFRESH_REQUIRED = "REFRESH_REQUIRED"
    INVALIDATED = "INVALIDATED"
    ARCHIVED = "ARCHIVED"


class RiskTier(StrEnum):
    R1_SIMPLE = "R1_SIMPLE"
    R2_REVIEW = "R2_REVIEW"
    R3_CRITICAL = "R3_CRITICAL"
    D_MODEL_ONLY = "D_MODEL_ONLY"
    D_DETERMINISTIC = "D_DETERMINISTIC"


class TriageRoutingAction(StrEnum):
    TRY_DIRECT_COMPILE = "TRY_DIRECT_COMPILE"
    REQUEST_GLM53 = "REQUEST_GLM53"
    HUMAN_RULE_REVIEW = "HUMAN_RULE_REVIEW"
    DEFER_NONTERMINAL = "DEFER_NONTERMINAL"


class CandidateSnapshotSeal(CommonEnvelope):
    seal_id: str
    candidate_id: str
    candidate_revision_id: str
    canonical_market_revision_id: str
    canonical_rule_source_artifact_id: str
    rule_source_sha256: str
    rule_source_byte_length: int = Field(gt=0)
    lifecycle_state: CandidateState
    eligibility: CandidateEligibility
    eligibility_as_of_utc: datetime
    eligibility_policy_id: str
    eligibility_policy_version: str
    allowed_projection_input_ids: tuple[str, ...]
    seal_sha256: str

    @field_validator(
        "seal_id",
        "candidate_id",
        "candidate_revision_id",
        "canonical_market_revision_id",
        "canonical_rule_source_artifact_id",
        "eligibility_policy_id",
        "eligibility_policy_version",
    )
    @classmethod
    def wp1_required_ids(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("snapshot identifiers must not be blank")
        return value

    @field_validator("rule_source_sha256", "seal_sha256")
    @classmethod
    def wp1_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("eligibility_as_of_utc")
    @classmethod
    def wp1_clock(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("allowed_projection_input_ids")
    @classmethod
    def wp1_inputs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("allowed projection input ids must be non-empty")
        if value != tuple(sorted(set(value))):
            raise ValueError("allowed projection input ids must be unique and sorted")
        return value

    @model_validator(mode="after")
    def wp1_identity(self) -> "CandidateSnapshotSeal":
        if self.seal_id != self.record_id:
            raise ValueError("seal_id must equal record_id")
        expected = content_sha256({
            "candidate_id": self.candidate_id,
            "candidate_revision_id": self.candidate_revision_id,
            "canonical_market_revision_id": self.canonical_market_revision_id,
            "canonical_rule_source_artifact_id": self.canonical_rule_source_artifact_id,
            "rule_source_sha256": self.rule_source_sha256,
            "rule_source_byte_length": self.rule_source_byte_length,
            "lifecycle_state": self.lifecycle_state,
            "eligibility": self.eligibility,
            "eligibility_as_of_utc": self.eligibility_as_of_utc,
            "eligibility_policy_id": self.eligibility_policy_id,
            "eligibility_policy_version": self.eligibility_policy_version,
            "allowed_projection_input_ids": self.allowed_projection_input_ids,
        })
        if self.seal_sha256 != expected:
            raise ValueError("seal_sha256 does not bind the snapshot payload")
        if self.record_id != stable_record_id("candidate_snapshot", {
            "candidate_id": self.candidate_id,
            "candidate_revision_id": self.candidate_revision_id,
            "canonical_market_revision_id": self.canonical_market_revision_id,
            "canonical_rule_source_artifact_id": self.canonical_rule_source_artifact_id,
            "rule_source_sha256": self.rule_source_sha256,
            "rule_source_byte_length": self.rule_source_byte_length,
            "lifecycle_state": self.lifecycle_state,
            "eligibility": self.eligibility,
            "eligibility_as_of_utc": self.eligibility_as_of_utc,
            "eligibility_policy_id": self.eligibility_policy_id,
            "eligibility_policy_version": self.eligibility_policy_version,
            "allowed_projection_input_ids": self.allowed_projection_input_ids,
        }):
            raise ValueError("snapshot id must be content-derived from the sealed payload")
        return self


class RuleDryRunReceipt(CommonEnvelope):
    receipt_id: str
    candidate_snapshot_id: str
    candidate_snapshot_sha256: str
    compiler_version: str
    complete: bool
    direct_compile_possible: bool
    authoritative_source_count: int = Field(ge=0)
    complexity_score: int = Field(ge=0)
    ambiguity_codes: tuple[str, ...] = ()
    reason_codes: tuple[str, ...]

    @field_validator("candidate_snapshot_sha256")
    @classmethod
    def dry_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("ambiguity_codes", "reason_codes")
    @classmethod
    def dry_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value) or len(value) != len(set(value)):
            raise ValueError("reason codes must be unique non-blank")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def dry_identity(self) -> "RuleDryRunReceipt":
        if self.receipt_id != self.record_id or not self.reason_codes:
            raise ValueError("dry-run receipt requires matching id and reason codes")
        if self.direct_compile_possible and not self.complete:
            raise ValueError("direct compile cannot be possible for an incomplete dry-run")
        expected_id = stable_record_id("rule_dry_run", (
            self.candidate_snapshot_id,
            self.candidate_snapshot_sha256,
            self.compiler_version,
            self.complete,
            self.direct_compile_possible,
            self.authoritative_source_count,
            self.complexity_score,
            tuple(sorted(self.ambiguity_codes)),
            tuple(sorted(self.reason_codes)),
        ))
        if self.record_id != expected_id:
            raise ValueError("dry-run receipt id must be content-derived")
        return self


class TriageRoutingDecision(CommonEnvelope):
    routing_decision_id: str
    candidate_snapshot_id: str
    candidate_snapshot_sha256: str
    triage_receipt_id: str | None = None
    triage_receipt_sha256: str | None = None
    rule_dry_run_receipt_id: str
    rule_dry_run_receipt_sha256: str
    risk_tier: RiskTier
    risk_reason_codes: tuple[str, ...]
    sample_policy_id: str
    sample_seed: str
    sampled: bool
    future_policy_sampled: bool
    future_sample_policy_id: str
    sample_context_id: str
    triage_attempt_id: str | None = None
    action: TriageRoutingAction
    future_policy_action: TriageRoutingAction
    refresh_after_utc: datetime | None = None

    @field_validator("candidate_snapshot_sha256", "triage_receipt_sha256", "rule_dry_run_receipt_sha256")
    @classmethod
    def route_hashes(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("refresh_after_utc")
    @classmethod
    def route_clock(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @model_validator(mode="after")
    def route_identity(self) -> "TriageRoutingDecision":
        if self.routing_decision_id != self.record_id:
            raise ValueError("routing_decision_id must equal record_id")
        if bool(self.triage_receipt_id) != bool(self.triage_receipt_sha256):
            raise ValueError("triage receipt id/hash must occur together")
        if bool(self.triage_receipt_id) != bool(self.triage_attempt_id):
            raise ValueError("triage receipt and attempt id must occur together")
        if not self.risk_reason_codes or len(self.risk_reason_codes) != len(set(self.risk_reason_codes)):
            raise ValueError("risk reason codes must be non-empty and unique")
        if not self.sample_seed.strip() or not self.sample_context_id.strip():
            raise ValueError("sample seed and context must not be blank")
        if self.sampled != (self.action == TriageRoutingAction.REQUEST_GLM53):
            raise ValueError("sampled must describe the actual GLM-5.3 route")
        if self.future_policy_sampled != (
            self.future_policy_action == TriageRoutingAction.REQUEST_GLM53
        ):
            raise ValueError("future_policy_sampled conflicts with counterfactual route")
        if self.future_sample_policy_id != "future_triage_sampling_v1":
            raise ValueError("unsupported future routing sample policy")
        if self.action == TriageRoutingAction.DEFER_NONTERMINAL and self.refresh_after_utc is None:
            raise ValueError("nonterminal defer requires refresh_after_utc")
        pilot_mode = self.sample_policy_id == "gate_r_8_case_100pct_glm53_v1"
        if not pilot_mode and self.sample_policy_id != "future_triage_sampling_v1":
            raise ValueError("unsupported routing sample policy")
        expected_id = stable_record_id("triage_routing", (
            self.candidate_snapshot_id,
            self.candidate_snapshot_sha256,
            self.triage_receipt_id,
            self.rule_dry_run_receipt_id,
            self.risk_tier,
            self.sample_seed,
            self.sample_context_id,
            pilot_mode,
            self.action,
            self.future_policy_action,
        ))
        if self.record_id != expected_id:
            raise ValueError("routing decision id must be content-derived")
        return self


class DisagreementReceipt(CommonEnvelope):
    receipt_id: str
    candidate_snapshot_id: str
    candidate_snapshot_sha256: str
    first_attempt_id: str
    first_attempt_sha256: str
    second_attempt_id: str
    second_attempt_sha256: str
    difference_codes: tuple[str, ...]
    route_decision_id: str
    route_decision_sha256: str
    routing_policy_version: str
    sample_seed: str

    @field_validator(
        "candidate_snapshot_sha256",
        "first_attempt_sha256",
        "second_attempt_sha256",
        "route_decision_sha256",
    )
    @classmethod
    def disagreement_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def disagreement_identity(self) -> "DisagreementReceipt":
        if self.receipt_id != self.record_id or self.first_attempt_id == self.second_attempt_id:
            raise ValueError("receipt id must match and attempts must be distinct")
        if (
            not self.first_attempt_id.strip()
            or not self.second_attempt_id.strip()
            or not self.route_decision_id.strip()
        ):
            raise ValueError("attempt and route ids must not be blank")
        if len(self.difference_codes) != len(set(self.difference_codes)):
            raise ValueError("difference codes must be unique")
        expected_id = stable_record_id(
            "triage_disagreement",
            self.candidate_snapshot_id,
            self.first_attempt_id,
            self.first_attempt_sha256,
            self.second_attempt_id,
            self.second_attempt_sha256,
            self.difference_codes,
            self.route_decision_id,
        )
        if self.record_id != expected_id:
            raise ValueError("disagreement receipt id must be content-derived")
        return self


# Gate R WP2 records deliberately live in the shared contract module.  They are
# append-only proposals and review receipts; none of them is a RuleContract or
# a Rule A decision.
class IndependentReviewDisposition(StrEnum):
    ADVANCE = "ADVANCE"
    REVIEW = "REVIEW"
    DEFER = "DEFER"


class RuleReviewAction(StrEnum):
    APPROVE = "APPROVE"
    PATCH = "PATCH"
    DEFER = "DEFER"
    REQUEST_CORRECTION = "REQUEST_CORRECTION"


class IndependentReviewAttemptStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    QUARANTINED = "QUARANTINED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class IndependentReviewBudget(AlphaContract):
    max_total_tokens: int = Field(gt=0)
    max_cost_usd_micros: int = Field(ge=0)
    max_duration_ms: int = Field(gt=0)


class RuleParseFieldProposal(AlphaContract):
    field_name: str
    proposed_value: str
    source_segment_id: str
    exact_quote: str
    proposal_confidence_milli: int = Field(ge=0, le=1000)
    unresolved: bool

    @field_validator("field_name", "proposed_value", "source_segment_id", "exact_quote")
    @classmethod
    def proposal_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("rule proposal text must not be blank")
        return value


class IndependentSemanticReview(CommonEnvelope):
    review_id: str
    attempt_id: str
    candidate_snapshot_id: str
    candidate_snapshot_sha256: str
    projection_id: str
    projection_sha256: str
    routing_decision_id: str
    routing_decision_sha256: str
    work_order_id: str
    work_order_sha256: str
    rule_source_artifact_id: str
    rule_source_sha256: str
    provider: str
    requested_model: str
    reported_model: str | None = None
    prompt_version: str
    schema_version_provider: str
    prompt_sha256: str
    provider_wrapper_sha256: str
    provider_return_sha256: str
    usage: dict[str, int] = Field(default_factory=dict)
    cost_usd_micros: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    topic: str
    entities: tuple[str, ...]
    relevant_clocks: tuple[str, ...]
    source_type_hints: tuple[str, ...]
    researchability: str
    ambiguities: tuple[str, ...] = ()
    independent_disposition_proposal: IndependentReviewDisposition

    @field_validator(
        "candidate_snapshot_sha256",
        "projection_sha256",
        "routing_decision_sha256",
        "work_order_sha256",
        "rule_source_sha256",
        "prompt_sha256",
        "provider_wrapper_sha256",
        "provider_return_sha256",
    )
    @classmethod
    def review_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator(
        "review_id", "attempt_id", "candidate_snapshot_id", "projection_id",
        "routing_decision_id", "work_order_id", "rule_source_artifact_id",
        "provider", "requested_model", "prompt_version", "schema_version_provider",
        "topic", "researchability",
    )
    @classmethod
    def review_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("review fields must not be blank")
        return value

    @field_validator("reported_model")
    @classmethod
    def optional_review_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None and value.strip() else None

    @field_validator("entities", "relevant_clocks", "source_type_hints", "ambiguities")
    @classmethod
    def review_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(item.strip() for item in value)
        if any(not item for item in cleaned) or len(cleaned) != len(set(cleaned)):
            raise ValueError("review lists must be unique nonblank strings")
        return cleaned

    @field_validator("usage")
    @classmethod
    def review_usage(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not key.strip() or count < 0 for key, count in value.items()):
            raise ValueError("usage must be nonnegative")
        return value

    @model_validator(mode="after")
    def review_identity(self) -> "IndependentSemanticReview":
        if self.review_id != self.record_id:
            raise ValueError("review_id must equal record_id")
        if self.record_id != stable_record_id(
            "independent_semantic_review",
            self.attempt_id,
            self.candidate_snapshot_id,
            self.candidate_snapshot_sha256,
            self.projection_id,
            self.projection_sha256,
            self.routing_decision_id,
            self.routing_decision_sha256,
            self.work_order_id,
            self.work_order_sha256,
            self.prompt_sha256,
            self.provider,
            self.requested_model,
        ):
            raise ValueError("independent review id must bind the sealed logical attempt")
        return self


class StructuredRuleParseProposal(CommonEnvelope):
    proposal_id: str
    attempt_id: str
    candidate_snapshot_id: str
    candidate_snapshot_sha256: str
    projection_id: str
    projection_sha256: str
    routing_decision_id: str
    routing_decision_sha256: str
    work_order_id: str
    work_order_sha256: str
    rule_source_artifact_id: str
    rule_source_sha256: str
    provider: str
    requested_model: str
    reported_model: str | None = None
    prompt_version: str
    schema_version_provider: str
    prompt_sha256: str
    provider_wrapper_sha256: str
    provider_return_sha256: str
    usage: dict[str, int] = Field(default_factory=dict)
    cost_usd_micros: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    proposals: tuple[RuleParseFieldProposal, ...]

    @field_validator(
        "candidate_snapshot_sha256", "projection_sha256", "routing_decision_sha256",
        "work_order_sha256", "rule_source_sha256", "prompt_sha256",
        "provider_wrapper_sha256", "provider_return_sha256",
    )
    @classmethod
    def proposal_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator(
        "proposal_id", "attempt_id", "candidate_snapshot_id", "projection_id",
        "routing_decision_id", "work_order_id", "rule_source_artifact_id",
        "provider", "requested_model", "prompt_version", "schema_version_provider",
    )
    @classmethod
    def proposal_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("proposal binding fields must not be blank")
        return value

    @field_validator("reported_model")
    @classmethod
    def optional_proposal_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None and value.strip() else None

    @field_validator("usage")
    @classmethod
    def proposal_usage(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not key.strip() or count < 0 for key, count in value.items()):
            raise ValueError("usage must be nonnegative")
        return value

    @model_validator(mode="after")
    def proposal_identity(self) -> "StructuredRuleParseProposal":
        if self.proposal_id != self.record_id or not self.proposals:
            raise ValueError("proposal id must match and proposals must not be empty")
        names = tuple(item.field_name for item in self.proposals)
        if len(names) != len(set(names)):
            raise ValueError("each field may have one proposal")
        if self.record_id != stable_record_id(
            "structured_rule_parse_proposal",
            self.attempt_id,
            self.candidate_snapshot_id,
            self.candidate_snapshot_sha256,
            self.projection_id,
            self.projection_sha256,
            self.routing_decision_id,
            self.routing_decision_sha256,
            self.work_order_id,
            self.work_order_sha256,
            self.prompt_sha256,
            self.provider,
            self.requested_model,
        ):
            raise ValueError("proposal id must bind the sealed logical attempt")
        return self


class IndependentReviewAttemptReceipt(CommonEnvelope):
    receipt_id: str
    attempt_id: str
    candidate_snapshot_id: str
    candidate_snapshot_sha256: str
    work_order_id: str
    work_order_sha256: str
    prompt_sha256: str
    provider_wrapper_sha256: str | None = None
    provider_return_sha256: str | None = None
    provider: str
    requested_model: str
    status: IndependentReviewAttemptStatus
    reason_codes: tuple[str, ...]
    usage: dict[str, int] = Field(default_factory=dict)
    cost_usd_micros: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)

    @field_validator(
        "candidate_snapshot_sha256", "work_order_sha256", "prompt_sha256",
        "provider_wrapper_sha256", "provider_return_sha256",
    )
    @classmethod
    def attempt_hashes(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator(
        "receipt_id", "attempt_id", "candidate_snapshot_id", "work_order_id",
        "provider", "requested_model",
    )
    @classmethod
    def attempt_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("attempt receipt fields must not be blank")
        return value

    @field_validator("reason_codes")
    @classmethod
    def attempt_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(sorted(item.strip() for item in value))
        if not cleaned or any(not item for item in cleaned) or len(cleaned) != len(set(cleaned)):
            raise ValueError("attempt reason codes must be unique nonblank strings")
        return cleaned

    @field_validator("usage")
    @classmethod
    def attempt_usage(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not key.strip() or count < 0 for key, count in value.items()):
            raise ValueError("usage must be nonnegative")
        return value

    @model_validator(mode="after")
    def attempt_identity(self) -> "IndependentReviewAttemptReceipt":
        if self.receipt_id != self.record_id:
            raise ValueError("attempt receipt id must equal record_id")
        expected = stable_record_id(
            "independent_review_attempt",
            self.attempt_id,
            self.candidate_snapshot_id,
            self.candidate_snapshot_sha256,
            self.work_order_id,
            self.work_order_sha256,
            self.provider,
            self.requested_model,
        )
        if self.record_id != expected:
            raise ValueError("attempt receipt id must bind the logical attempt")
        if self.status == IndependentReviewAttemptStatus.ACCEPTED and (
            self.provider_wrapper_sha256 is None or self.provider_return_sha256 is None
        ):
            raise ValueError("accepted attempt requires provider artifact hashes")
        return self


class RuleInterpretationPatch(AlphaContract):
    field_path: str
    old_value_hash: str
    new_value: str
    source_segment_id: str
    source_artifact_id: str
    source_content_sha256: str
    exact_quote: str
    quote_start: int = Field(ge=0)
    quote_end: int = Field(gt=0)

    @field_validator("field_path", "new_value", "source_segment_id", "source_artifact_id", "exact_quote")
    @classmethod
    def patch_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("patch fields must not be blank")
        return value

    @field_validator("old_value_hash", "source_content_sha256")
    @classmethod
    def patch_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def patch_span(self) -> "RuleInterpretationPatch":
        if self.quote_end - self.quote_start != len(self.exact_quote):
            raise ValueError("patch quote offsets must match the exact quote")
        return self


class RuleReviewApproval(CommonEnvelope):
    review_receipt_id: str
    rule_contract_draft_id: str
    before_hash: str
    before_draft: dict[str, str]
    reviewer_id: str
    reviewed_at: datetime
    action: RuleReviewAction
    reason_codes: tuple[str, ...]
    review_note: str = ""
    patches: tuple[RuleInterpretationPatch, ...] = ()
    after_draft_id: str | None = None
    after_hash: str | None = None
    after_draft: dict[str, str] | None = None

    @field_validator("before_hash", "after_hash")
    @classmethod
    def approval_hashes(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("reviewed_at")
    @classmethod
    def approval_clock(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def approval_identity(self) -> "RuleReviewApproval":
        if self.review_receipt_id != self.record_id or not self.reason_codes:
            raise ValueError("approval receipt id must match and include reasons")
        if content_sha256(self.before_draft) != self.before_hash:
            raise ValueError("before draft does not match before_hash")
        if len(self.reason_codes) != len(set(self.reason_codes)) or any(not code.strip() for code in self.reason_codes):
            raise ValueError("approval reasons must be unique nonblank strings")
        if self.action == RuleReviewAction.PATCH and (
            not self.patches or self.after_hash is None or self.after_draft_id is None or self.after_draft is None
        ):
            raise ValueError("patch approval requires patches and resealed after draft")
        if self.action != RuleReviewAction.PATCH and (
            self.patches or self.after_hash is not None or self.after_draft_id is not None or self.after_draft is not None
        ):
            raise ValueError("only PATCH may carry a replacement draft")
        if self.action == RuleReviewAction.PATCH:
            fields = tuple(patch.field_path for patch in self.patches)
            if len(fields) != len(set(fields)):
                raise ValueError("a field may be patched only once per approval")
            replay = dict(self.before_draft)
            for patch in self.patches:
                if patch.field_path not in replay:
                    raise ValueError("patch field is absent from before draft")
                old_hash = hashlib.sha256(replay[patch.field_path].encode("utf-8")).hexdigest()
                if old_hash != patch.old_value_hash:
                    raise ValueError("patch old value hash is stale")
                replay[patch.field_path] = patch.new_value
            if replay != self.after_draft or content_sha256(replay) != self.after_hash:
                raise ValueError("after draft is not the ordered patch result")
            if self.after_draft_id != stable_record_id(
                "rule_contract_draft", self.rule_contract_draft_id, self.after_hash
            ):
                raise ValueError("after draft id must bind the patched draft")
        expected_id = stable_record_id(
            "rule_review_approval",
            self.rule_contract_draft_id,
            self.before_hash,
            self.action,
            self.reason_codes,
            self.patches,
            self.after_draft_id,
            self.after_hash,
        )
        if self.record_id != expected_id:
            raise ValueError("approval receipt id must be content-derived")
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


class SourceArtifact(CommonEnvelope):
    artifact_id: str
    source_name: str
    source_url_or_source_id: str
    media_type: str
    captured_at: datetime
    effective_as_of: datetime
    capture_scope: CaptureScope
    hash_scope: HashScope | None = None
    content_sha256: str | None = None
    content_length_bytes: int | None = Field(default=None, gt=0)
    artifact_locator: str | None = None
    replayability: Replayability

    @field_validator("captured_at", "effective_as_of")
    @classmethod
    def times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("content_sha256")
    @classmethod
    def content_hash_is_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("source_name", "source_url_or_source_id", "media_type")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source artifact text fields must not be blank")
        return value

    @model_validator(mode="after")
    def capture_semantics_are_consistent(self) -> "SourceArtifact":
        if self.artifact_id != self.record_id:
            raise ValueError("artifact_id must equal record_id")
        if not re.fullmatch(r"source_artifact:[0-9a-f]{64}", self.record_id):
            raise ValueError("SourceArtifact id must use source_artifact namespace")
        if self.effective_as_of > self.captured_at:
            raise ValueError("effective_as_of cannot be after captured_at")
        if self.capture_scope == CaptureScope.REFERENCE_ONLY:
            if any(
                item is not None
                for item in (
                    self.hash_scope,
                    self.content_sha256,
                    self.content_length_bytes,
                    self.artifact_locator,
                )
            ):
                raise ValueError("REFERENCE_ONLY cannot claim captured content")
            if self.replayability != Replayability.REFERENCE_ONLY:
                raise ValueError("REFERENCE_ONLY artifact is not content-replayable")
        else:
            if self.hash_scope is None or self.content_sha256 is None:
                raise ValueError("captured artifact requires hash_scope and content_sha256")
            if self.content_length_bytes is None:
                raise ValueError("captured artifact requires positive content_length_bytes")
            if self.artifact_locator is None or not self.artifact_locator.strip():
                raise ValueError("captured artifact requires immutable artifact_locator")
            if self.capture_scope == CaptureScope.FULL_DOCUMENT:
                if self.hash_scope == HashScope.CLAIM_EXCERPT:
                    raise ValueError("FULL_DOCUMENT cannot use CLAIM_EXCERPT hash scope")
                if self.replayability != Replayability.FULL:
                    raise ValueError("FULL_DOCUMENT must be fully replayable")
            elif self.capture_scope == CaptureScope.EXCERPT_ONLY:
                if self.hash_scope not in {HashScope.CLAIM_EXCERPT, HashScope.NORMALIZED_TEXT}:
                    raise ValueError("EXCERPT_ONLY requires excerpt/text hash scope")
                if self.replayability != Replayability.EXCERPT:
                    raise ValueError("EXCERPT_ONLY replayability must be EXCERPT")
        return self


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


# Gate R WP3 artifacts are deliberately small, offline contracts.  They do not
# inherit CommonEnvelope because a plan is a sealed input artifact rather than
# a lifecycle event; every identity below is content-derived by its compiler.
class BlindPlanArtifactBinding(AlphaContract):
    artifact_id: str
    artifact_sha256: str

    @field_validator("artifact_id")
    @classmethod
    def nonblank_artifact_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("artifact_id must not be blank")
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def artifact_hash(cls, value: str) -> str:
        return validate_sha256(value)


class BlindPlanQuestion(AlphaContract):
    question_id: str
    claim_type: str
    neutral_question_text: str
    required_answer_type: str
    evidence_target: str
    time_scope: str
    required: bool
    dependency_ids: tuple[str, ...] = ()
    provenance_template_id: str
    provenance_evidence_ids: tuple[str, ...] = ()

    @field_validator(
        "question_id", "claim_type", "neutral_question_text", "required_answer_type",
        "evidence_target", "time_scope", "provenance_template_id",
    )
    @classmethod
    def plan_question_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("plan question text must not be blank")
        return value

    @model_validator(mode="after")
    def plan_question_is_blind(self) -> "BlindPlanQuestion":
        if blind_leak_reasons(self.model_dump(mode="python")):
            raise ValueError("plan question contains Blind leakage")
        if self.provenance_template_id not in _APPROVED_BLIND_QUESTION_TEMPLATES:
            raise ValueError("question provenance template is not allowlisted")
        if not self.question_id.startswith("blind_plan_question:"):
            raise ValueError("question id must use blind_plan_question namespace")
        if len(self.dependency_ids) != len(set(self.dependency_ids)) or self.question_id in self.dependency_ids:
            raise ValueError("question dependencies must be unique and cannot reference self")
        if len(self.provenance_evidence_ids) != len(set(self.provenance_evidence_ids)):
            raise ValueError("question evidence provenance must be unique")
        payload = {
            "claim_type": self.claim_type,
            "neutral_question_text": self.neutral_question_text,
            "required_answer_type": self.required_answer_type,
            "evidence_target": self.evidence_target,
            "time_scope": self.time_scope,
            "required": self.required,
            "dependency_ids": self.dependency_ids,
            "provenance_template_id": self.provenance_template_id,
            "provenance_evidence_ids": self.provenance_evidence_ids,
        }
        if self.question_id != stable_record_id("blind_plan_question", payload):
            raise ValueError("question id must be content-derived")
        return self


class BlindPlanLeakageReceipt(AlphaContract):
    receipt_id: str
    receipt_sha256: str
    scanner_version: str
    scanned_input_sha256: str
    status: Literal["PASS"]
    checked_paths: tuple[str, ...]

    @field_validator("receipt_sha256", "scanned_input_sha256")
    @classmethod
    def leakage_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def leakage_identity(self) -> "BlindPlanLeakageReceipt":
        if not self.scanner_version.strip() or not self.checked_paths:
            raise ValueError("leakage receipt requires scanner version and checked paths")
        payload = {
            "scanner_version": self.scanner_version,
            "scanned_input_sha256": self.scanned_input_sha256,
            "status": self.status,
            "checked_paths": self.checked_paths,
        }
        if self.receipt_id != stable_record_id("blind_plan_leakage_receipt", payload):
            raise ValueError("leakage receipt id must be content-derived")
        if self.receipt_sha256 != content_sha256(payload):
            raise ValueError("leakage receipt hash must bind its payload")
        return self


class BlindResearchQuestionSet(AlphaContract):
    schema_version: str
    question_set_id: str
    question_set_sha256: str
    candidate_snapshot_id: str
    candidate_snapshot_sha256: str
    blind_projection_id: str
    blind_projection_sha256: str
    blind_packet_id: str
    blind_packet_sha256: str
    rule_contract_id: str
    rule_contract_sha256: str
    compiler_policy_id: str
    compiler_policy_version: str
    research_as_of_utc: datetime
    pit_cutoff_utc: datetime
    allowed_input_artifacts: tuple[BlindPlanArtifactBinding, ...]
    leakage_scan_receipt_id: str
    questions: tuple[BlindPlanQuestion, ...]
    created_at_utc: datetime

    @field_validator(
        "question_set_sha256", "candidate_snapshot_sha256", "blind_projection_sha256",
        "blind_packet_sha256", "rule_contract_sha256",
    )
    @classmethod
    def qs_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("research_as_of_utc", "pit_cutoff_utc", "created_at_utc")
    @classmethod
    def qs_times(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def qs_is_complete(self) -> "BlindResearchQuestionSet":
        if self.schema_version != "gate_r_wp3_v1":
            raise ValueError("unsupported Blind question-set schema")
        if not self.questions or not self.allowed_input_artifacts:
            raise ValueError("question set requires questions and frozen input artifacts")
        if not (self.pit_cutoff_utc <= self.research_as_of_utc <= self.created_at_utc):
            raise ValueError("Blind planning clocks must order cutoff, research-as-of, creation")
        if len({item.question_id for item in self.questions}) != len(self.questions):
            raise ValueError("question ids must be unique")
        if len({item.artifact_id for item in self.allowed_input_artifacts}) != len(self.allowed_input_artifacts):
            raise ValueError("input artifact ids must be unique")
        if self.allowed_input_artifacts != tuple(sorted(self.allowed_input_artifacts, key=lambda item: item.artifact_id)):
            raise ValueError("input artifact bindings must be sorted")
        if not all(value.strip() for value in (
            self.candidate_snapshot_id, self.blind_projection_id, self.blind_packet_id,
            self.rule_contract_id, self.compiler_policy_id, self.compiler_policy_version,
            self.leakage_scan_receipt_id,
        )):
            raise ValueError("question-set binding fields must not be blank")
        payload = {
            "candidate_snapshot_id": self.candidate_snapshot_id,
            "candidate_snapshot_sha256": self.candidate_snapshot_sha256,
            "blind_projection_id": self.blind_projection_id,
            "blind_projection_sha256": self.blind_projection_sha256,
            "blind_packet_id": self.blind_packet_id,
            "blind_packet_sha256": self.blind_packet_sha256,
            "rule_contract_id": self.rule_contract_id,
            "rule_contract_sha256": self.rule_contract_sha256,
            "compiler_policy_id": self.compiler_policy_id,
            "compiler_policy_version": self.compiler_policy_version,
            "research_as_of_utc": self.research_as_of_utc,
            "pit_cutoff_utc": self.pit_cutoff_utc,
            "allowed_input_artifacts": self.allowed_input_artifacts,
            "leakage_scan_receipt_id": self.leakage_scan_receipt_id,
            "questions": self.questions,
            "created_at_utc": self.created_at_utc,
        }
        if self.question_set_id != stable_record_id("blind_question_set", payload):
            raise ValueError("question set id must be content-derived")
        if self.question_set_sha256 != content_sha256(payload):
            raise ValueError("question set hash must bind its complete payload")
        return self


class SourcePlan(AlphaContract):
    schema_version: str
    source_plan_id: str
    source_plan_sha256: str
    question_set_id: str
    question_set_sha256: str
    rule_contract_id: str
    rule_contract_sha256: str
    source_policy_id: str
    source_policy_version: str
    pit_cutoff_utc: datetime
    allowed_source_classes: tuple[str, ...]
    allowed_domains: tuple[str, ...]
    forbidden_source_classes: tuple[str, ...]
    forbidden_domains: tuple[str, ...]
    primary_source_requirements: tuple[str, ...]
    fallback_policy: str
    source_independence_policy: str
    capture_preference: str
    minimum_claim_coverage: int = Field(ge=1)
    question_ids: tuple[str, ...]
    critical_claim_ids: tuple[str, ...]
    critical_claim_types: tuple[str, ...]
    max_sources: int = Field(ge=1)
    max_searches: int = Field(ge=0)
    max_elapsed_minutes: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    stop_conditions: tuple[str, ...]
    freshness_policy: str
    availability_policy: str
    created_at_utc: datetime

    @field_validator("source_plan_sha256", "question_set_sha256", "rule_contract_sha256")
    @classmethod
    def sp_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("pit_cutoff_utc", "created_at_utc")
    @classmethod
    def sp_times(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def source_plan_is_bounded_and_blind(self) -> "SourcePlan":
        if self.schema_version != "gate_r_wp3_v1":
            raise ValueError("unsupported Blind source-plan schema")
        if not self.allowed_source_classes or not self.primary_source_requirements:
            raise ValueError("source plan requires allowed classes and primary requirements")
        if not self.question_ids or not self.critical_claim_ids or not self.critical_claim_types or not self.stop_conditions:
            raise ValueError("source plan requires critical claims and stop conditions")
        if self.minimum_claim_coverage > len(self.critical_claim_ids):
            raise ValueError("minimum claim coverage exceeds critical claims")
        tuple_fields = (
            self.allowed_source_classes, self.allowed_domains, self.forbidden_source_classes,
            self.forbidden_domains, self.primary_source_requirements, self.question_ids,
            self.critical_claim_ids, self.critical_claim_types, self.stop_conditions,
        )
        if any(len(values) != len(set(values)) or any(not value.strip() for value in values) for values in tuple_fields):
            raise ValueError("source-plan tuple fields must contain unique nonblank values")
        if not set(self.critical_claim_ids).issubset(self.question_ids):
            raise ValueError("critical claims must be members of the QuestionSet")
        if not set(self.critical_claim_types).issubset(self.primary_source_requirements):
            raise ValueError("critical claim types require primary-source policies")
        if set(self.allowed_source_classes).intersection(self.forbidden_source_classes):
            raise ValueError("source class cannot be both allowed and forbidden")
        if set(self.allowed_domains).intersection(self.forbidden_domains):
            raise ValueError("source domain cannot be both allowed and forbidden")
        forbidden_source = re.compile(r"polymarket|gamma|\bclob\b|mirror|market[_ -]?venue", re.I)
        if any(forbidden_source.search(value) for value in self.allowed_source_classes + self.allowed_domains):
            raise ValueError("venue, CLOB, Gamma and mirror sources are forbidden")
        rendered = self.model_dump(mode="python")
        provider_safe_rendered = {key: value for key, value in rendered.items()
            if key not in {"forbidden_source_classes", "forbidden_domains"}}
        if blind_leak_reasons(provider_safe_rendered):
            raise ValueError("source plan contains Blind leakage")
        payload = {
            key: value for key, value in rendered.items()
            if key not in {"source_plan_id", "source_plan_sha256"}
        }
        if self.source_plan_id != stable_record_id("blind_source_plan", payload):
            raise ValueError("source plan id must be content-derived")
        if self.source_plan_sha256 != content_sha256(payload):
            raise ValueError("source plan hash must bind its complete payload")
        return self


class BlindResearchPlanSeal(AlphaContract):
    plan_seal_id: str
    plan_seal_sha256: str
    question_set_id: str
    question_set_sha256: str
    source_plan_id: str
    source_plan_sha256: str
    created_at_utc: datetime

    @field_validator("plan_seal_sha256", "question_set_sha256", "source_plan_sha256")
    @classmethod
    def plan_seal_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("created_at_utc")
    @classmethod
    def plan_seal_time(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def plan_seal_identity(self) -> "BlindResearchPlanSeal":
        payload = {
            "question_set_id": self.question_set_id,
            "question_set_sha256": self.question_set_sha256,
            "source_plan_id": self.source_plan_id,
            "source_plan_sha256": self.source_plan_sha256,
            "created_at_utc": self.created_at_utc,
        }
        if self.plan_seal_id != stable_record_id("blind_research_plan_seal", payload):
            raise ValueError("plan seal id must be content-derived")
        if self.plan_seal_sha256 != content_sha256(payload):
            raise ValueError("plan seal hash must bind its payload")
        return self


class BlindWorkOrderPromptSeal(AlphaContract):
    schema_version: str
    work_order_id: str
    research_job_id: str
    attempt_policy_id: str
    candidate_snapshot_id: str
    candidate_snapshot_sha256: str
    rule_contract_id: str
    rule_contract_sha256: str
    blind_packet_id: str
    blind_packet_sha256: str
    question_set_id: str
    question_set_sha256: str
    source_plan_id: str
    source_plan_sha256: str
    plan_seal_id: str
    plan_seal_sha256: str
    output_schema_id: str
    output_schema_sha256: str
    provider_policy_id: str
    provider_policy_version: str
    content_type: Literal["text/plain"]
    encoding: Literal["utf-8"]
    newline_mode: Literal["LF"]
    byte_length: int = Field(gt=0)
    prompt_sha256: str
    preview_sha256: str
    created_at_utc: datetime
    expires_at_utc: datetime
    seal_sha256: str

    @field_validator(
        "candidate_snapshot_sha256", "rule_contract_sha256", "blind_packet_sha256",
        "question_set_sha256", "source_plan_sha256", "plan_seal_sha256", "output_schema_sha256",
        "prompt_sha256", "preview_sha256", "seal_sha256",
    )
    @classmethod
    def prompt_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("created_at_utc", "expires_at_utc")
    @classmethod
    def prompt_times(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def prompt_expiry_is_future(self) -> "BlindWorkOrderPromptSeal":
        if self.schema_version != "gate_r_wp3_v1":
            raise ValueError("unsupported Blind prompt-seal schema")
        if self.expires_at_utc <= self.created_at_utc:
            raise ValueError("prompt expiry must be after creation")
        required = (
            self.research_job_id, self.attempt_policy_id, self.candidate_snapshot_id,
            self.rule_contract_id, self.blind_packet_id, self.question_set_id,
            self.source_plan_id, self.plan_seal_id, self.output_schema_id,
            self.provider_policy_id, self.provider_policy_version,
        )
        if any(not value.strip() for value in required):
            raise ValueError("prompt-seal binding fields must not be blank")
        identity = {
            "research_job_id": self.research_job_id,
            "attempt_policy_id": self.attempt_policy_id,
            "candidate_snapshot_id": self.candidate_snapshot_id,
            "candidate_snapshot_sha256": self.candidate_snapshot_sha256,
            "rule_contract_id": self.rule_contract_id,
            "rule_contract_sha256": self.rule_contract_sha256,
            "blind_packet_id": self.blind_packet_id,
            "blind_packet_sha256": self.blind_packet_sha256,
            "question_set_id": self.question_set_id,
            "question_set_sha256": self.question_set_sha256,
            "source_plan_id": self.source_plan_id,
            "source_plan_sha256": self.source_plan_sha256,
            "plan_seal_id": self.plan_seal_id,
            "plan_seal_sha256": self.plan_seal_sha256,
            "output_schema_id": self.output_schema_id,
            "output_schema_sha256": self.output_schema_sha256,
            "provider_policy_id": self.provider_policy_id,
            "provider_policy_version": self.provider_policy_version,
            "content_type": self.content_type,
            "encoding": self.encoding,
            "newline_mode": self.newline_mode,
            "byte_length": self.byte_length,
            "prompt_sha256": self.prompt_sha256,
            "preview_sha256": self.preview_sha256,
            "created_at_utc": self.created_at_utc,
            "expires_at_utc": self.expires_at_utc,
        }
        if self.work_order_id != stable_record_id("blind_work_order", identity):
            raise ValueError("work order id must bind all prompt metadata")
        seal_payload = {"work_order_id": self.work_order_id, **identity}
        if self.seal_sha256 != content_sha256(seal_payload):
            raise ValueError("prompt seal hash must bind all prompt metadata")
        return self


# Gate R WP4 intentionally models only locally captured, immutable bytes.  These
# records contain no provider client, browser, or transport capability.
class ExportApprovalAction(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ExactFileCopyPolicy(StrEnum):
    COPY_ATTESTED_NOT_CRYPTOGRAPHICALLY_OBSERVED = "COPY_ATTESTED_NOT_CRYPTOGRAPHICALLY_OBSERVED"


class ManualCaptureScope(StrEnum):
    FULL = "FULL"
    EXCERPT = "EXCERPT"
    REFERENCE = "REFERENCE"


class SourceRepresentation(StrEnum):
    ORIGINAL_BYTES = "ORIGINAL_BYTES"
    SAVED_HTML = "SAVED_HTML"
    RENDERED_PDF = "RENDERED_PDF"
    TEXT_EXPORT = "TEXT_EXPORT"
    SCREENSHOT = "SCREENSHOT"
    NONE = "NONE"


class JsonAppendixParseStatus(StrEnum):
    PARSED = "PARSED"
    MALFORMED = "MALFORMED"
    ABSENT = "ABSENT"


class ExportApprovalReceipt(AlphaContract):
    """One human decision for one exact WP3 prompt seal; never reusable."""

    approval_receipt_id: str
    work_order_id: str
    prompt_sha256: str
    approver_id: str
    approved_at_utc: datetime
    expires_at_utc: datetime
    action: ExportApprovalAction
    review_check_codes: tuple[str, ...]
    copy_policy: ExactFileCopyPolicy
    prompt_patch_id: str | None = None
    parent_approval_receipt_id: str | None = None
    parent_approval_sha256: str | None = None
    parent_work_order_id: str | None = None
    parent_prompt_sha256: str | None = None
    approval_sha256: str

    @field_validator("prompt_sha256", "approval_sha256", "parent_approval_sha256", "parent_prompt_sha256")
    @classmethod
    def _approval_hashes(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("approved_at_utc", "expires_at_utc")
    @classmethod
    def _approval_clocks(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _approval_identity(self) -> "ExportApprovalReceipt":
        if not self.work_order_id.strip() or not self.approver_id.strip() or not self.review_check_codes:
            raise ValueError("approval requires work order, approver, and review checks")
        if self.expires_at_utc <= self.approved_at_utc:
            raise ValueError("approval expiry must follow approval")
        if len(self.review_check_codes) != len(set(self.review_check_codes)) or any(not x.strip() for x in self.review_check_codes):
            raise ValueError("review check codes must be unique and nonblank")
        lineage = (
            self.prompt_patch_id, self.parent_approval_receipt_id,
            self.parent_approval_sha256, self.parent_work_order_id,
            self.parent_prompt_sha256,
        )
        if any(value is not None for value in lineage) != all(value is not None for value in lineage):
            raise ValueError("prompt patch lineage fields must occur together")
        if self.prompt_patch_id is not None and (
            self.parent_work_order_id == self.work_order_id or self.parent_prompt_sha256 == self.prompt_sha256
        ):
            raise ValueError("a patched prompt requires a newly sealed work order and prompt")
        payload = self.model_dump(mode="python", exclude={"approval_receipt_id", "approval_sha256"})
        if self.approval_receipt_id != stable_record_id("export_approval_receipt", payload):
            raise ValueError("approval receipt id must be content-derived")
        if self.approval_sha256 != content_sha256(payload):
            raise ValueError("approval receipt hash must be content-derived")
        return self


class SourceCapture(AlphaContract):
    """Metadata plus caller-supplied frozen source bytes, validated locally."""

    source_capture_id: str
    source_key: str
    canonical_url: str
    title: str
    publisher: str
    source_class: str
    primary_or_secondary: Literal["PRIMARY", "SECONDARY"]
    published_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None
    effective_at_utc: datetime | None = None
    accessed_at_utc: datetime
    first_available_at_utc: datetime
    pit_cutoff_utc: datetime
    pit_available: bool
    capture_scope: ManualCaptureScope
    representation: SourceRepresentation
    content_type: str | None = None
    content_length_bytes: int | None = Field(default=None, ge=1)
    content_sha256: str | None = None
    artifact_locator: str | None = None
    claim_ids: tuple[str, ...]
    quote_locator_or_excerpt: str | None = None
    redirect_chain: tuple[str, ...] = ()
    archive_or_version_identity: str | None = None
    content_bytes: bytes | None = Field(default=None, exclude=True)

    @field_validator(
        "accessed_at_utc", "first_available_at_utc", "pit_cutoff_utc",
        "published_at_utc", "updated_at_utc", "effective_at_utc",
    )
    @classmethod
    def _source_clock(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @field_validator("content_sha256")
    @classmethod
    def _source_hash(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("artifact_locator")
    @classmethod
    def _source_locator(cls, value: str | None) -> str | None:
        return _relative_artifact_locator(value) if value is not None else None

    @model_validator(mode="after")
    def _source_capture_identity(self) -> "SourceCapture":
        if not all(x.strip() for x in (self.source_key, self.canonical_url, self.title, self.publisher, self.source_class)) or not self.claim_ids:
            raise ValueError("source capture requires canonical identity and claim ids")
        if len(self.claim_ids) != len(set(self.claim_ids)) or any(not x.strip() for x in self.claim_ids):
            raise ValueError("claim ids must be unique and nonblank")
        if any(not x.strip() for x in self.redirect_chain):
            raise ValueError("redirect chain entries must be nonblank")
        if self.effective_at_utc and self.effective_at_utc > self.accessed_at_utc:
            raise ValueError("effective time cannot follow access")
        if self.published_at_utc and self.published_at_utc > self.accessed_at_utc:
            raise ValueError("publication cannot follow access")
        if self.first_available_at_utc > self.accessed_at_utc:
            raise ValueError("first availability cannot follow access")
        if self.pit_available != (self.first_available_at_utc <= self.pit_cutoff_utc):
            raise ValueError("PIT availability must be derived from first availability and cutoff")
        captured = self.capture_scope != ManualCaptureScope.REFERENCE
        if not captured:
            if any(x is not None for x in (self.content_type, self.content_length_bytes, self.content_sha256, self.artifact_locator, self.content_bytes)) or self.representation != SourceRepresentation.NONE:
                raise ValueError("REFERENCE capture cannot claim content bytes or representation")
        else:
            if self.representation == SourceRepresentation.NONE or any(x is None for x in (self.content_type, self.content_length_bytes, self.content_sha256, self.artifact_locator)):
                raise ValueError("FULL/EXCERPT capture requires replayable local metadata")
            if self.content_bytes is not None and (
                len(self.content_bytes) != self.content_length_bytes or bytes_sha256(self.content_bytes) != self.content_sha256
            ):
                raise ValueError("source content hash/length must be recomputed from supplied bytes")
            if self.capture_scope == ManualCaptureScope.EXCERPT and not self.quote_locator_or_excerpt:
                raise ValueError("EXCERPT capture requires quote locator or excerpt")
        payload = self.model_dump(mode="python", exclude={"source_capture_id", "content_bytes"})
        if self.source_capture_id != stable_record_id("source_capture", payload):
            raise ValueError("source capture id must be content-derived")
        return self


class SourceCaptureManifest(AlphaContract):
    manifest_id: str
    manifest_sha256: str
    captures: tuple[SourceCapture, ...]
    pit_cutoff_utc: datetime
    critical_claim_ids: tuple[str, ...]
    created_at_utc: datetime

    @field_validator("manifest_sha256")
    @classmethod
    def _manifest_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("created_at_utc", "pit_cutoff_utc")
    @classmethod
    def _manifest_clock(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _manifest_identity(self) -> "SourceCaptureManifest":
        if not self.captures or len({x.source_capture_id for x in self.captures}) != len(self.captures):
            raise ValueError("source manifest requires unique captures")
        if self.captures != tuple(sorted(self.captures, key=lambda x: x.source_capture_id)):
            raise ValueError("source captures must be sorted")
        if len({x.source_key for x in self.captures}) != len(self.captures):
            raise ValueError("source capture keys must be unique")
        if len(self.critical_claim_ids) != len(set(self.critical_claim_ids)) or any(not item.strip() for item in self.critical_claim_ids):
            raise ValueError("critical claim ids must be unique nonblank strings")
        if any(capture.pit_cutoff_utc != self.pit_cutoff_utc for capture in self.captures):
            raise ValueError("all captures must share the manifest PIT cutoff")
        payload = {"captures": self.captures, "pit_cutoff_utc": self.pit_cutoff_utc,
            "critical_claim_ids": self.critical_claim_ids, "created_at_utc": self.created_at_utc}
        if self.manifest_id != stable_record_id("source_capture_manifest", payload) or self.manifest_sha256 != content_sha256(payload):
            raise ValueError("source manifest identity/hash must be content-derived")
        return self


class ResearchReturnCaptureSeal(CommonEnvelope):
    return_seal_id: str
    research_job_id: str
    attempt_id: str
    attempt_sha256: str
    work_order_id: str
    prompt_sha256: str
    approval_receipt_id: str
    provider_ui: str
    displayed_model: str
    session_mode: str
    operator_id: str
    started_at_utc: datetime
    completed_at_utc: datetime
    captured_at_utc: datetime
    raw_transcript_locator: str
    raw_transcript_sha256: str
    raw_transcript_byte_length: int = Field(gt=0)
    raw_response_locator: str
    raw_response_sha256: str
    raw_response_byte_length: int = Field(gt=0)
    json_appendix_locator: str | None = None
    json_appendix_sha256: str | None = None
    json_appendix_byte_length: int | None = Field(default=None, ge=1)
    json_parse_status: JsonAppendixParseStatus
    source_manifest_id: str
    source_manifest_sha256: str
    copy_attestation: ExactFileCopyPolicy
    observed_tool_usage: tuple[str, ...]
    return_seal_sha256: str
    raw_transcript_bytes: bytes | None = Field(default=None, exclude=True)
    raw_response_bytes: bytes | None = Field(default=None, exclude=True)
    json_appendix_bytes: bytes | None = Field(default=None, exclude=True)

    @field_validator("started_at_utc", "completed_at_utc", "captured_at_utc")
    @classmethod
    def _return_clock(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("attempt_sha256", "prompt_sha256", "raw_transcript_sha256", "raw_response_sha256", "json_appendix_sha256", "source_manifest_sha256", "return_seal_sha256")
    @classmethod
    def _return_hash(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("raw_transcript_locator", "raw_response_locator", "json_appendix_locator")
    @classmethod
    def _return_locator(cls, value: str | None) -> str | None:
        return _relative_artifact_locator(value) if value is not None else None

    @model_validator(mode="after")
    def _return_identity(self) -> "ResearchReturnCaptureSeal":
        if not all(x.strip() for x in (self.research_job_id, self.attempt_id, self.work_order_id, self.approval_receipt_id, self.provider_ui, self.displayed_model, self.session_mode, self.operator_id, self.source_manifest_id)):
            raise ValueError("return capture bindings must not be blank")
        if not (self.started_at_utc <= self.completed_at_utc <= self.captured_at_utc):
            raise ValueError("return capture clocks must be ordered")
        for raw, digest, length, label in ((self.raw_transcript_bytes, self.raw_transcript_sha256, self.raw_transcript_byte_length, "transcript"), (self.raw_response_bytes, self.raw_response_sha256, self.raw_response_byte_length, "response")):
            if raw is not None and (not isinstance(raw, bytes) or len(raw) != length or bytes_sha256(raw) != digest):
                raise ValueError(f"raw {label} hash/length must be recomputed from supplied bytes")
        appendix_fields = (self.json_appendix_locator, self.json_appendix_sha256, self.json_appendix_byte_length)
        appendix_metadata = all(value is not None for value in appendix_fields)
        if any(value is not None for value in appendix_fields) != appendix_metadata:
            raise ValueError("JSON appendix metadata must be complete or absent")
        if self.json_appendix_bytes is not None and not appendix_metadata:
            raise ValueError("JSON appendix bytes require complete metadata")
        if self.json_appendix_bytes is not None and (
            len(self.json_appendix_bytes) != self.json_appendix_byte_length
            or bytes_sha256(self.json_appendix_bytes) != self.json_appendix_sha256
        ):
            raise ValueError("JSON appendix hash/length must be recomputed")
        if self.json_parse_status == JsonAppendixParseStatus.PARSED and not appendix_metadata:
            raise ValueError("PARSED JSON appendix requires captured metadata")
        payload = self.model_dump(mode="python", exclude={"return_seal_id", "return_seal_sha256", "raw_transcript_bytes", "raw_response_bytes", "json_appendix_bytes"})
        logical_id = stable_record_id("research_return_capture", self.research_job_id,
            self.attempt_id, self.attempt_sha256, self.work_order_id)
        if self.return_seal_id != self.record_id or self.record_id != logical_id:
            raise ValueError("return seal id must bind the logical attempt")
        if self.return_seal_sha256 != content_sha256(payload):
            raise ValueError("return seal hash must bind captured content")
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


class MarketComparisonStatus(StrEnum):
    """Disposition of one immutable Blind-versus-book comparison."""

    READY = "READY"
    BOOK_REFRESH_REQUIRED = "BOOK_REFRESH_REQUIRED"
    NON_ADVANCING = "NON_ADVANCING"


class MarketComparison(CommonEnvelope):
    """Content-addressed executable comparison; it never changes Blind belief."""

    comparison_id: str
    comparison_sha256: str
    accepted_blind_result_id: str
    accepted_blind_result_sha256: str
    blind_p_yes_low: Decimal = Field(ge=0, le=1)
    blind_p_yes_mid: Decimal = Field(ge=0, le=1)
    blind_p_yes_high: Decimal = Field(ge=0, le=1)
    blind_as_of_utc: datetime
    rule_contract_id: str
    rule_contract_sha256: str
    rule_hash: str
    book_receipt_id: str
    book_receipt_sha256: str
    orderbook_snapshot_id: str
    orderbook_snapshot_sha256: str
    book_capture_at_utc: datetime
    comparison_as_of_utc: datetime
    policy_size: Decimal = Field(gt=0)
    yes_bid: Decimal | None = Field(default=None, ge=0, le=1)
    yes_ask: Decimal | None = Field(default=None, ge=0, le=1)
    no_bid: Decimal | None = Field(default=None, ge=0, le=1)
    no_ask: Decimal | None = Field(default=None, ge=0, le=1)
    yes_buy_vwap: Decimal | None = Field(default=None, ge=0, le=1)
    no_buy_vwap: Decimal | None = Field(default=None, ge=0, le=1)
    fee_slippage_cost_policy_id: str
    fee_slippage_cost_policy_version: str
    fee_rate: Decimal = Field(ge=0, le=1)
    slippage_buffer: Decimal = Field(ge=0, le=1)
    yes_edge_low: Decimal | None = None
    yes_edge_mid: Decimal | None = None
    yes_edge_high: Decimal | None = None
    no_edge_low: Decimal | None = None
    no_edge_mid: Decimal | None = None
    no_edge_high: Decimal | None = None
    stale: bool
    insufficient_depth: bool
    one_sided: bool
    crossed_outcome: bool
    status: MarketComparisonStatus
    reason_codes: tuple[str, ...]

    @field_validator(
        "comparison_sha256", "accepted_blind_result_sha256", "rule_contract_sha256",
        "rule_hash", "book_receipt_sha256", "orderbook_snapshot_sha256",
    )
    @classmethod
    def comparison_hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("blind_as_of_utc", "book_capture_at_utc", "comparison_as_of_utc")
    @classmethod
    def comparison_clocks_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("comparison_id", "accepted_blind_result_id", "rule_contract_id", "book_receipt_id", "orderbook_snapshot_id", "fee_slippage_cost_policy_id", "fee_slippage_cost_policy_version")
    @classmethod
    def comparison_text_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("MarketComparison identity/policy fields must not be blank")
        return value

    @model_validator(mode="after")
    def comparison_is_complete_and_content_derived(self) -> "MarketComparison":
        # Subclasses own their complete semantic validator.  This preserves the
        # released V1 payload byte-for-byte while allowing additive contracts.
        if type(self) is not MarketComparison:
            return self
        if self.comparison_id != self.record_id:
            raise ValueError("comparison_id must equal record_id")
        if not (self.blind_p_yes_low <= self.blind_p_yes_mid <= self.blind_p_yes_high):
            raise ValueError("Blind interval must be ordered")
        if self.book_capture_at_utc > self.comparison_as_of_utc:
            raise ValueError("comparison cannot precede book capture")
        if self.blind_as_of_utc > self.comparison_as_of_utc:
            raise ValueError("comparison cannot precede Blind as-of")
        values = (self.yes_bid, self.yes_ask, self.no_bid, self.no_ask, self.yes_buy_vwap, self.no_buy_vwap)
        edges = (self.yes_edge_low, self.yes_edge_mid, self.yes_edge_high, self.no_edge_low, self.no_edge_mid, self.no_edge_high)
        observed_one_sided = any(value is None for value in (self.yes_bid, self.yes_ask, self.no_bid, self.no_ask))
        if self.one_sided != observed_one_sided:
            raise ValueError("one_sided must be derived from paired top-of-book quotes")
        observed_crossed = False
        if not observed_one_sided:
            assert self.yes_bid is not None and self.yes_ask is not None
            assert self.no_bid is not None and self.no_ask is not None
            observed_crossed = (
                self.yes_bid >= self.yes_ask
                or self.no_bid >= self.no_ask
                or self.yes_bid + self.no_bid > Decimal("1")
                or self.yes_ask + self.no_ask < Decimal("1")
            )
        if self.crossed_outcome != observed_crossed:
            raise ValueError("crossed_outcome must be derived from paired quotes")
        executable = not (self.stale or self.insufficient_depth or self.one_sided or self.crossed_outcome)
        if executable:
            if any(value is None for value in values) or any(value is None for value in edges):
                raise ValueError("READY comparison requires paired prices, policy depth and edge intervals")
            assert self.yes_bid is not None and self.yes_ask is not None and self.no_bid is not None and self.no_ask is not None
            assert self.yes_edge_low is not None and self.yes_edge_mid is not None and self.yes_edge_high is not None
            assert self.no_edge_low is not None and self.no_edge_mid is not None and self.no_edge_high is not None
            if self.yes_bid >= self.yes_ask or self.no_bid >= self.no_ask:
                raise ValueError("READY comparison cannot use crossed leg quotes")
            if not (self.yes_edge_low <= self.yes_edge_mid <= self.yes_edge_high and self.no_edge_low <= self.no_edge_mid <= self.no_edge_high):
                raise ValueError("edge intervals must be ordered")
            assert self.yes_buy_vwap is not None and self.no_buy_vwap is not None
            yes_cost = self.yes_buy_vwap * (Decimal("1") + self.fee_rate) + self.slippage_buffer
            no_cost = self.no_buy_vwap * (Decimal("1") + self.fee_rate) + self.slippage_buffer
            expected_edges = (
                self.blind_p_yes_low - yes_cost,
                self.blind_p_yes_mid - yes_cost,
                self.blind_p_yes_high - yes_cost,
                (Decimal("1") - self.blind_p_yes_high) - no_cost,
                (Decimal("1") - self.blind_p_yes_mid) - no_cost,
                (Decimal("1") - self.blind_p_yes_low) - no_cost,
            )
            if edges != expected_edges:
                raise ValueError("edge intervals must be deterministically recomputed")
            if self.status != MarketComparisonStatus.READY:
                raise ValueError("executable comparison must be READY")
        else:
            expected_status = (
                MarketComparisonStatus.BOOK_REFRESH_REQUIRED
                if self.stale or self.one_sided or self.insufficient_depth
                else MarketComparisonStatus.NON_ADVANCING
            )
            if self.status != expected_status:
                raise ValueError("unusable comparison status must match refresh/block semantics")
            if any(value is not None for value in edges):
                raise ValueError("unusable comparison cannot fabricate edge intervals")
        expected_reasons: list[str] = []
        if executable:
            expected_reasons.append("EXECUTABLE_PAIRED_BOOK")
        else:
            if self.stale:
                expected_reasons.append("BOOK_REFRESH_REQUIRED")
            if self.insufficient_depth:
                expected_reasons.append("INSUFFICIENT_POLICY_DEPTH")
            if self.one_sided:
                expected_reasons.append("ONE_SIDED_BOOK")
            if self.crossed_outcome:
                expected_reasons.append("CROSS_OUTCOME_INCONSISTENT")
        if self.reason_codes != tuple(sorted(expected_reasons)):
            raise ValueError("reason_codes must exactly match the derived comparison state")
        payload = self.model_dump(mode="python", exclude={"record_id", "comparison_id", "comparison_sha256"})
        expected_hash = content_sha256(payload)
        expected_id = stable_record_id("market_comparison", payload)
        if self.comparison_sha256 != expected_hash or self.record_id != expected_id:
            raise ValueError("MarketComparison id/hash must be recomputed from complete content")
        return self


class MarketComparisonV2(MarketComparison):
    """Additive fill-level executable-cost contract.

    V1 remains parseable as :class:`MarketComparison`.  New compilers emit this
    contract so a comparison seals the exact theoretical fills and fee model
    used to derive each all-in edge.
    """

    yes_buy_fills: tuple[BookLevel, ...] = ()
    no_buy_fills: tuple[BookLevel, ...] = ()
    yes_taker_fee: Decimal | None = Field(default=None, ge=0)
    no_taker_fee: Decimal | None = Field(default=None, ge=0)
    yes_all_in_buy_price: Decimal | None = Field(default=None, ge=0)
    no_all_in_buy_price: Decimal | None = Field(default=None, ge=0)
    fee_model_version: str

    @field_validator("fee_model_version")
    @classmethod
    def fee_model_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("fee model version must not be blank")
        return value

    @model_validator(mode="after")
    def comparison_v2_is_complete_and_content_derived(self) -> "MarketComparisonV2":
        # Local import avoids a contracts -> books -> contracts import cycle.
        from ..books.executable_cost import (
            EXECUTABLE_COST_VERSION,
            executable_sweep,
        )

        if self.comparison_id != self.record_id:
            raise ValueError("comparison_id must equal record_id")
        if self.fee_model_version != EXECUTABLE_COST_VERSION:
            raise ValueError("unsupported executable fee model version")
        if not (self.blind_p_yes_low <= self.blind_p_yes_mid <= self.blind_p_yes_high):
            raise ValueError("Blind interval must be ordered")
        if self.book_capture_at_utc > self.comparison_as_of_utc:
            raise ValueError("comparison cannot precede book capture")
        if self.blind_as_of_utc > self.comparison_as_of_utc:
            raise ValueError("comparison cannot precede Blind as-of")

        values = (
            self.yes_bid,
            self.yes_ask,
            self.no_bid,
            self.no_ask,
            self.yes_buy_vwap,
            self.no_buy_vwap,
        )
        edges = (
            self.yes_edge_low,
            self.yes_edge_mid,
            self.yes_edge_high,
            self.no_edge_low,
            self.no_edge_mid,
            self.no_edge_high,
        )
        costs = (
            self.yes_taker_fee,
            self.no_taker_fee,
            self.yes_all_in_buy_price,
            self.no_all_in_buy_price,
        )
        observed_one_sided = any(
            value is None
            for value in (self.yes_bid, self.yes_ask, self.no_bid, self.no_ask)
        )
        if self.one_sided != observed_one_sided:
            raise ValueError("one_sided must be derived from paired top-of-book quotes")
        observed_crossed = False
        if not observed_one_sided:
            assert self.yes_bid is not None and self.yes_ask is not None
            assert self.no_bid is not None and self.no_ask is not None
            observed_crossed = (
                self.yes_bid >= self.yes_ask
                or self.no_bid >= self.no_ask
                or self.yes_bid + self.no_bid > Decimal("1")
                or self.yes_ask + self.no_ask < Decimal("1")
            )
        if self.crossed_outcome != observed_crossed:
            raise ValueError("crossed_outcome must be derived from paired quotes")

        executable = not (
            self.stale
            or self.insufficient_depth
            or self.one_sided
            or self.crossed_outcome
        )
        if executable:
            if (
                any(value is None for value in values)
                or any(value is None for value in edges)
                or any(value is None for value in costs)
                or not self.yes_buy_fills
                or not self.no_buy_fills
            ):
                raise ValueError(
                    "READY V2 comparison requires paired prices, fills, fees and edges"
                )
            assert self.yes_ask is not None and self.no_ask is not None
            if (
                self.yes_buy_fills[0].price != self.yes_ask
                or self.no_buy_fills[0].price != self.no_ask
            ):
                raise ValueError("first executable fill must equal the sealed best ask")
            yes_sweep = executable_sweep(
                self.yes_buy_fills,
                target_size=self.policy_size,
                side="BUY",
                fee_rate=self.fee_rate,
                slippage_buffer=self.slippage_buffer,
            )
            no_sweep = executable_sweep(
                self.no_buy_fills,
                target_size=self.policy_size,
                side="BUY",
                fee_rate=self.fee_rate,
                slippage_buffer=self.slippage_buffer,
            )
            if (
                not yes_sweep.fully_executable
                or not no_sweep.fully_executable
                or yes_sweep.fills != self.yes_buy_fills
                or no_sweep.fills != self.no_buy_fills
            ):
                raise ValueError("V2 fills must exactly execute the policy target size")
            if (
                yes_sweep.vwap != self.yes_buy_vwap
                or no_sweep.vwap != self.no_buy_vwap
                or yes_sweep.taker_fee != self.yes_taker_fee
                or no_sweep.taker_fee != self.no_taker_fee
                or yes_sweep.effective_price != self.yes_all_in_buy_price
                or no_sweep.effective_price != self.no_all_in_buy_price
            ):
                raise ValueError("V2 executable costs must be recomputed from sealed fills")
            assert self.yes_all_in_buy_price is not None
            assert self.no_all_in_buy_price is not None
            expected_edges = (
                self.blind_p_yes_low - self.yes_all_in_buy_price,
                self.blind_p_yes_mid - self.yes_all_in_buy_price,
                self.blind_p_yes_high - self.yes_all_in_buy_price,
                (Decimal("1") - self.blind_p_yes_high) - self.no_all_in_buy_price,
                (Decimal("1") - self.blind_p_yes_mid) - self.no_all_in_buy_price,
                (Decimal("1") - self.blind_p_yes_low) - self.no_all_in_buy_price,
            )
            if edges != expected_edges:
                raise ValueError("edge intervals must be deterministically recomputed")
            if self.status != MarketComparisonStatus.READY:
                raise ValueError("executable comparison must be READY")
        else:
            expected_status = (
                MarketComparisonStatus.BOOK_REFRESH_REQUIRED
                if self.stale or self.one_sided or self.insufficient_depth
                else MarketComparisonStatus.NON_ADVANCING
            )
            if self.status != expected_status:
                raise ValueError("unusable comparison status must match refresh/block semantics")
            if any(value is not None for value in edges):
                raise ValueError("unusable comparison cannot fabricate edge intervals")
            if self.yes_buy_fills or self.no_buy_fills or any(
                value is not None for value in costs
            ):
                raise ValueError("unusable V2 comparison cannot carry executable costs")

        expected_reasons: list[str] = []
        if executable:
            expected_reasons.append("EXECUTABLE_PAIRED_BOOK")
        else:
            if self.stale:
                expected_reasons.append("BOOK_REFRESH_REQUIRED")
            if self.insufficient_depth:
                expected_reasons.append("INSUFFICIENT_POLICY_DEPTH")
            if self.one_sided:
                expected_reasons.append("ONE_SIDED_BOOK")
            if self.crossed_outcome:
                expected_reasons.append("CROSS_OUTCOME_INCONSISTENT")
        if self.reason_codes != tuple(sorted(expected_reasons)):
            raise ValueError("reason_codes must exactly match the derived comparison state")

        payload = self.model_dump(
            mode="python",
            exclude={"record_id", "comparison_id", "comparison_sha256"},
        )
        expected_hash = content_sha256(payload)
        expected_id = stable_record_id("market_comparison", payload)
        if self.comparison_sha256 != expected_hash or self.record_id != expected_id:
            raise ValueError("MarketComparison id/hash must be recomputed from complete content")
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


class ResearchResultEnvelope(CommonEnvelope):
    result_id: str
    packet_stage: PacketStage
    packet_id: str
    packet_sha256: str
    probability_estimate: ProbabilityEstimate
    evidence: tuple[ClaimEvidence, ...]
    source_artifacts: tuple[SourceArtifact, ...]
    completed_at: datetime
    producer: str
    producer_version: str

    @field_validator("packet_sha256")
    @classmethod
    def packet_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("completed_at")
    @classmethod
    def completed_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("producer", "producer_version")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("research producer fields must not be blank")
        return value

    @model_validator(mode="after")
    def research_result_is_bound_and_replayable(self) -> "ResearchResultEnvelope":
        if self.result_id != self.record_id:
            raise ValueError("result_id must equal record_id")
        if not re.fullmatch(r"research_result:[0-9a-f]{64}", self.record_id):
            raise ValueError("ResearchResultEnvelope id must use research_result namespace")
        if not self.evidence or not self.source_artifacts:
            raise ValueError("research result requires claim evidence and source artifacts")
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        artifact_ids = tuple(item.artifact_id for item in self.source_artifacts)
        if evidence_ids != tuple(sorted(evidence_ids)) or len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence must be unique and sorted by evidence_id")
        if artifact_ids != tuple(sorted(artifact_ids)) or len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("source_artifacts must be unique and sorted by artifact_id")
        if self.probability_estimate.run_id != self.run_id:
            raise ValueError("probability estimate and result run_id must match")
        if any(item.run_id != self.run_id for item in self.evidence):
            raise ValueError("claim evidence and result run_id must match")
        artifact_by_id = {item.artifact_id: item for item in self.source_artifacts}
        if set(artifact_by_id) != {item.source_artifact_id for item in self.evidence}:
            raise ValueError("source_artifacts must exactly cover referenced claim artifacts")
        for item in self.evidence:
            artifact = artifact_by_id.get(item.source_artifact_id)
            if artifact is None:
                raise ValueError("every claim must reference an included immutable source artifact")
            if (
                item.source_url_or_source_id != artifact.source_url_or_source_id
                or item.capture_scope != artifact.capture_scope
                or item.hash_scope != artifact.hash_scope
                or item.content_sha256 != artifact.content_sha256
                or item.replayability != artifact.replayability
            ):
                raise ValueError("claim capture semantics must match its SourceArtifact")
        if self.packet_stage == PacketStage.BLIND:
            if not re.fullmatch(r"blind_packet:[0-9a-f]{64}", self.packet_id):
                raise ValueError("BLIND result requires a blind_packet id")
            if self.probability_estimate.estimate_stage != EstimateStage.BLIND:
                raise ValueError("BLIND result requires a BLIND probability estimate")
            forbidden_origins = {
                EvidenceOrigin.WALLET,
                EvidenceOrigin.MARKET,
                EvidenceOrigin.OPERATOR_COMMENTARY,
            }
            if any(item.origin in forbidden_origins for item in self.evidence):
                raise ValueError("market, wallet and operator evidence cannot enter BLIND result")
            leaks = blind_leak_reasons(self.model_dump(mode="python"))
            if leaks:
                raise ValueError(f"BLIND result contains market-derived semantics: {leaks}")
        else:
            if not re.fullmatch(r"market_packet:[0-9a-f]{64}", self.packet_id):
                raise ValueError("MARKET_AWARE result requires a market_packet id")
            if self.probability_estimate.estimate_stage == EstimateStage.BLIND:
                raise ValueError("MARKET_AWARE result requires a market-aware probability estimate")
            if self.extensions.get("probability_update") == "NONE":
                baseline = self.extensions.get("blind_probability_interval")
                try:
                    bound = tuple(Decimal(str(value)) for value in baseline) if isinstance(baseline, list) else ()
                except Exception:
                    bound = ()
                if bound != (self.probability_estimate.p_event_yes_low, self.probability_estimate.p_event_yes_mid, self.probability_estimate.p_event_yes_high):
                    raise ValueError("market assessment cannot overwrite the bound Blind probability interval")
        return self


class ResearchImportStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"


class ResearchImportReason(StrEnum):
    ACCEPTED = "ACCEPTED"
    SCHEMA_VERSION_MISMATCH = "SCHEMA_VERSION_MISMATCH"
    PACKET_STAGE_MISMATCH = "PACKET_STAGE_MISMATCH"
    PACKET_ID_MISMATCH = "PACKET_ID_MISMATCH"
    PACKET_HASH_MISMATCH = "PACKET_HASH_MISMATCH"
    RESULT_HASH_MISMATCH = "RESULT_HASH_MISMATCH"
    SOURCE_ARTIFACT_MISSING = "SOURCE_ARTIFACT_MISSING"
    SOURCE_ARTIFACT_HASH_MISMATCH = "SOURCE_ARTIFACT_HASH_MISMATCH"
    BLIND_SEMANTIC_LEAK = "BLIND_SEMANTIC_LEAK"
    VALIDATION_FAILED = "VALIDATION_FAILED"


class ResearchImportReceipt(CommonEnvelope):
    import_receipt_id: str
    packet_stage: PacketStage
    packet_id: str
    packet_sha256: str
    submitted_artifact_id: str
    submitted_result_sha256: str
    status: ResearchImportStatus
    reasons: tuple[ResearchImportReason, ...]
    imported_at: datetime
    importer_version: str
    accepted_result_id: str | None = None
    accepted_result_sha256: str | None = None
    quarantine_artifact_id: str | None = None

    @field_validator("packet_sha256", "submitted_result_sha256", "accepted_result_sha256")
    @classmethod
    def hashes_are_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("imported_at")
    @classmethod
    def imported_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def import_disposition_is_consistent(self) -> "ResearchImportReceipt":
        if self.import_receipt_id != self.record_id:
            raise ValueError("import_receipt_id must equal record_id")
        if not re.fullmatch(r"research_import_receipt:[0-9a-f]{64}", self.record_id):
            raise ValueError("ResearchImportReceipt id must use research_import_receipt namespace")
        if not self.reasons or len(self.reasons) != len(set(self.reasons)):
            raise ValueError("import reasons must be non-empty and unique")
        packet_namespace = "blind_packet" if self.packet_stage == PacketStage.BLIND else "market_packet"
        if not re.fullmatch(rf"{packet_namespace}:[0-9a-f]{{64}}", self.packet_id):
            raise ValueError("packet_id namespace must match packet_stage")
        if not re.fullmatch(r"source_artifact:[0-9a-f]{64}", self.submitted_artifact_id):
            raise ValueError("submitted_artifact_id must use source_artifact namespace")
        if self.reasons != tuple(sorted(self.reasons, key=lambda item: item.value)):
            raise ValueError("import reasons must use canonical lexical ordering")
        if self.status == ResearchImportStatus.ACCEPTED:
            if self.reasons != (ResearchImportReason.ACCEPTED,):
                raise ValueError("ACCEPTED receipt requires only the ACCEPTED reason")
            if self.accepted_result_id is None or self.accepted_result_sha256 is None:
                raise ValueError("ACCEPTED receipt requires accepted result id and hash")
            if not re.fullmatch(r"research_result:[0-9a-f]{64}", self.accepted_result_id):
                raise ValueError("accepted_result_id must use research_result namespace")
            if self.quarantine_artifact_id is not None:
                raise ValueError("ACCEPTED receipt cannot reference quarantine")
        else:
            if ResearchImportReason.ACCEPTED in self.reasons:
                raise ValueError("non-accepted receipt cannot contain ACCEPTED reason")
            if self.accepted_result_id is not None or self.accepted_result_sha256 is not None:
                raise ValueError("non-accepted receipt cannot claim an accepted result")
            if self.status == ResearchImportStatus.QUARANTINED:
                if self.quarantine_artifact_id is None:
                    raise ValueError("QUARANTINED receipt requires quarantine_artifact_id")
            elif self.quarantine_artifact_id is not None:
                raise ValueError("REJECTED receipt cannot claim a quarantine artifact")
        return self


class ResearchJobStatus(StrEnum):
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    RETRY_PENDING = "RETRY_PENDING"
    COMPLETED = "COMPLETED"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"


class ResearchReturnDisposition(StrEnum):
    RETURNED = "RETURNED"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"


class ResearchTransitionReason(StrEnum):
    LEASE_GRANTED = "LEASE_GRANTED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    ATTEMPT_FAILED = "ATTEMPT_FAILED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    RESULT_ACCEPTED = "RESULT_ACCEPTED"
    RESULT_QUARANTINED = "RESULT_QUARANTINED"
    MANUAL_CANCELLED = "MANUAL_CANCELLED"
    PACKET_INVALIDATED = "PACKET_INVALIDATED"
    RULE_REVISION_INVALIDATED = "RULE_REVISION_INVALIDATED"
    JOB_TTL_EXPIRED = "JOB_TTL_EXPIRED"


class ResearchJob(CommonEnvelope):
    """Immutable root for one provider-neutral research execution request."""

    job_id: str
    packet_stage: PacketStage
    packet_id: str
    packet_sha256: str
    rule_contract_id: str
    rule_contract_sha256: str
    brief_artifact_locator: str
    brief_bytes_sha256: str
    provider_policy_id: str
    source_policy_id: str
    max_attempts: int = Field(ge=1, le=10)
    available_at: datetime
    expires_at: datetime
    initial_status: ResearchJobStatus = ResearchJobStatus.QUEUED

    @field_validator(
        "packet_sha256", "rule_contract_sha256", "brief_bytes_sha256"
    )
    @classmethod
    def job_hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("available_at", "expires_at")
    @classmethod
    def job_times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator(
        "provider_policy_id", "source_policy_id"
    )
    @classmethod
    def job_text_is_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("research job text fields must not be blank")
        return value

    @field_validator("brief_artifact_locator")
    @classmethod
    def job_locator_is_relative(cls, value: str) -> str:
        return _relative_artifact_locator(value)

    @model_validator(mode="after")
    def job_identity_and_window_are_consistent(self) -> "ResearchJob":
        if self.job_id != self.record_id or not re.fullmatch(
            r"research_job:[0-9a-f]{64}", self.record_id
        ):
            raise ValueError("ResearchJob id must use research_job namespace")
        packet_namespace = (
            "blind_packet" if self.packet_stage == PacketStage.BLIND else "market_packet"
        )
        if not re.fullmatch(rf"{packet_namespace}:[0-9a-f]{{64}}", self.packet_id):
            raise ValueError("ResearchJob packet namespace must match packet_stage")
        if not re.fullmatch(r"rule_contract:[0-9a-f]{64}", self.rule_contract_id):
            raise ValueError("ResearchJob requires a RuleContract id")
        if self.expires_at <= self.available_at:
            raise ValueError("ResearchJob expires_at must follow available_at")
        if self.initial_status != ResearchJobStatus.QUEUED:
            raise ValueError("ResearchJob must start QUEUED")
        return self


class ResearchAttempt(CommonEnvelope):
    attempt_id: str
    job_id: str
    job_sha256: str
    attempt_number: int = Field(ge=1)
    worker_id: str
    leased_at: datetime
    lease_expires_at: datetime

    @field_validator("job_sha256")
    @classmethod
    def attempt_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("worker_id")
    @classmethod
    def worker_is_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("worker_id must not be blank")
        return value

    @field_validator("leased_at", "lease_expires_at")
    @classmethod
    def attempt_times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def attempt_identity_and_lease_are_consistent(self) -> "ResearchAttempt":
        if self.attempt_id != self.record_id or not re.fullmatch(
            r"research_attempt:[0-9a-f]{64}", self.record_id
        ):
            raise ValueError("ResearchAttempt id must use research_attempt namespace")
        if not re.fullmatch(r"research_job:[0-9a-f]{64}", self.job_id):
            raise ValueError("ResearchAttempt requires a ResearchJob id")
        if self.lease_expires_at <= self.leased_at:
            raise ValueError("ResearchAttempt lease must have positive duration")
        return self


class ResearchWorkOrder(CommonEnvelope):
    work_order_id: str
    job_id: str
    job_sha256: str
    attempt_id: str
    attempt_sha256: str
    packet_stage: PacketStage
    packet_id: str
    packet_sha256: str
    brief_artifact_locator: str
    brief_bytes_sha256: str
    provider_policy_id: str
    source_policy_id: str
    issued_at: datetime
    expires_at: datetime

    @field_validator(
        "job_sha256", "attempt_sha256", "packet_sha256", "brief_bytes_sha256"
    )
    @classmethod
    def work_order_hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def work_order_times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator(
        "provider_policy_id", "source_policy_id"
    )
    @classmethod
    def work_order_text_is_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("research work-order text fields must not be blank")
        return value

    @field_validator("brief_artifact_locator")
    @classmethod
    def work_order_locator_is_relative(cls, value: str) -> str:
        return _relative_artifact_locator(value)

    @model_validator(mode="after")
    def work_order_identity_and_window_are_consistent(self) -> "ResearchWorkOrder":
        if self.work_order_id != self.record_id or not re.fullmatch(
            r"research_work_order:[0-9a-f]{64}", self.record_id
        ):
            raise ValueError("ResearchWorkOrder id must use research_work_order namespace")
        if not re.fullmatch(r"research_job:[0-9a-f]{64}", self.job_id):
            raise ValueError("ResearchWorkOrder requires a ResearchJob id")
        if not re.fullmatch(r"research_attempt:[0-9a-f]{64}", self.attempt_id):
            raise ValueError("ResearchWorkOrder requires a ResearchAttempt id")
        packet_namespace = (
            "blind_packet" if self.packet_stage == PacketStage.BLIND else "market_packet"
        )
        if not re.fullmatch(rf"{packet_namespace}:[0-9a-f]{{64}}", self.packet_id):
            raise ValueError("ResearchWorkOrder packet namespace must match packet_stage")
        if self.expires_at <= self.issued_at:
            raise ValueError("ResearchWorkOrder expires_at must follow issued_at")
        return self


class ResearchReturnReceipt(CommonEnvelope):
    return_receipt_id: str
    job_id: str
    job_sha256: str
    attempt_id: str
    attempt_sha256: str
    work_order_id: str
    work_order_sha256: str
    disposition: ResearchReturnDisposition
    returned_artifact_locator: str | None = None
    returned_bytes_sha256: str | None = None
    returned_byte_length: int | None = Field(default=None, ge=1)
    failure_code: str | None = None
    received_at: datetime

    @field_validator(
        "job_sha256", "attempt_sha256", "work_order_sha256", "returned_bytes_sha256"
    )
    @classmethod
    def return_hashes_are_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("received_at")
    @classmethod
    def returned_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("returned_artifact_locator")
    @classmethod
    def return_locator_is_relative(cls, value: str | None) -> str | None:
        return _relative_artifact_locator(value) if value is not None else None

    @model_validator(mode="after")
    def return_disposition_is_consistent(self) -> "ResearchReturnReceipt":
        if self.return_receipt_id != self.record_id or not re.fullmatch(
            r"research_return_receipt:[0-9a-f]{64}", self.record_id
        ):
            raise ValueError(
                "ResearchReturnReceipt id must use research_return_receipt namespace"
            )
        if not re.fullmatch(r"research_job:[0-9a-f]{64}", self.job_id):
            raise ValueError("ResearchReturnReceipt requires a ResearchJob id")
        if not re.fullmatch(r"research_attempt:[0-9a-f]{64}", self.attempt_id):
            raise ValueError("ResearchReturnReceipt requires a ResearchAttempt id")
        if not re.fullmatch(r"research_work_order:[0-9a-f]{64}", self.work_order_id):
            raise ValueError("ResearchReturnReceipt requires a ResearchWorkOrder id")
        artifact_values = (
            self.returned_artifact_locator,
            self.returned_bytes_sha256,
            self.returned_byte_length,
        )
        if self.disposition in (
            ResearchReturnDisposition.RETURNED,
            ResearchReturnDisposition.QUARANTINED,
        ):
            if any(item is None for item in artifact_values):
                raise ValueError("returned/quarantined receipt requires returned artifact bytes")
        elif any(item is not None for item in artifact_values):
            raise ValueError("FAILED receipt cannot claim returned artifact bytes")
        if self.disposition == ResearchReturnDisposition.RETURNED:
            if self.failure_code is not None:
                raise ValueError("RETURNED receipt cannot have a failure_code")
        elif not self.failure_code or not self.failure_code.strip():
            raise ValueError("failed/quarantined receipt requires failure_code")
        return self


class ResearchJobTransition(CommonEnvelope):
    transition_id: str
    job_id: str
    job_sha256: str
    attempt_id: str | None = None
    from_status: ResearchJobStatus
    to_status: ResearchJobStatus
    reason: ResearchTransitionReason
    cause_record_id: str | None = None
    cause_record_sha256: str | None = None
    effective_at: datetime

    @field_validator("job_sha256", "cause_record_sha256")
    @classmethod
    def transition_hashes_are_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("effective_at")
    @classmethod
    def transition_time_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def transition_is_append_only_and_legal(self) -> "ResearchJobTransition":
        if self.transition_id != self.record_id or not re.fullmatch(
            r"research_job_transition:[0-9a-f]{64}", self.record_id
        ):
            raise ValueError(
                "ResearchJobTransition id must use research_job_transition namespace"
            )
        if not re.fullmatch(r"research_job:[0-9a-f]{64}", self.job_id):
            raise ValueError("ResearchJobTransition requires a ResearchJob id")
        if self.attempt_id is not None and not re.fullmatch(
            r"research_attempt:[0-9a-f]{64}", self.attempt_id
        ):
            raise ValueError("attempt_id must use research_attempt namespace")
        if (self.cause_record_id is None) != (self.cause_record_sha256 is None):
            raise ValueError("transition cause id/hash must be supplied together")
        allowed = {
            (ResearchJobStatus.QUEUED, ResearchJobStatus.LEASED): {
                ResearchTransitionReason.LEASE_GRANTED
            },
            (ResearchJobStatus.RETRY_PENDING, ResearchJobStatus.LEASED): {
                ResearchTransitionReason.LEASE_GRANTED
            },
            (ResearchJobStatus.LEASED, ResearchJobStatus.RETRY_PENDING): {
                ResearchTransitionReason.LEASE_EXPIRED,
                ResearchTransitionReason.ATTEMPT_FAILED,
            },
            (ResearchJobStatus.LEASED, ResearchJobStatus.COMPLETED): {
                ResearchTransitionReason.RESULT_ACCEPTED
            },
            (ResearchJobStatus.LEASED, ResearchJobStatus.QUARANTINED): {
                ResearchTransitionReason.RESULT_QUARANTINED
            },
            (ResearchJobStatus.LEASED, ResearchJobStatus.FAILED): {
                ResearchTransitionReason.RETRY_EXHAUSTED
            },
        }
        if self.to_status == ResearchJobStatus.CANCELLED:
            legal = self.from_status in {
                ResearchJobStatus.QUEUED,
                ResearchJobStatus.LEASED,
                ResearchJobStatus.RETRY_PENDING,
            } and self.reason == ResearchTransitionReason.MANUAL_CANCELLED
        elif self.to_status == ResearchJobStatus.INVALIDATED:
            legal = self.from_status in {
                ResearchJobStatus.QUEUED,
                ResearchJobStatus.LEASED,
                ResearchJobStatus.RETRY_PENDING,
            } and self.reason in {
                ResearchTransitionReason.PACKET_INVALIDATED,
                ResearchTransitionReason.RULE_REVISION_INVALIDATED,
            }
        elif self.to_status == ResearchJobStatus.FAILED and self.reason == ResearchTransitionReason.JOB_TTL_EXPIRED:
            legal = self.from_status in {
                ResearchJobStatus.QUEUED,
                ResearchJobStatus.LEASED,
                ResearchJobStatus.RETRY_PENDING,
            }
        else:
            legal = self.reason in allowed.get((self.from_status, self.to_status), set())
        if not legal:
            raise ValueError("illegal ResearchJob transition")
        attempt_reasons = {
            ResearchTransitionReason.LEASE_GRANTED,
            ResearchTransitionReason.LEASE_EXPIRED,
            ResearchTransitionReason.ATTEMPT_FAILED,
            ResearchTransitionReason.RETRY_EXHAUSTED,
            ResearchTransitionReason.RESULT_ACCEPTED,
            ResearchTransitionReason.RESULT_QUARANTINED,
        }
        if self.reason in attempt_reasons and self.attempt_id is None:
            raise ValueError("attempt-related transition requires attempt_id")
        if self.reason != ResearchTransitionReason.MANUAL_CANCELLED and self.cause_record_id is None:
            raise ValueError("non-manual transition requires a hash-bound cause record")
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


class ResolutionOutcome(StrEnum):
    YES = "YES"
    NO = "NO"
    INVALID = "INVALID"


class ResolutionAdjudicationStatus(StrEnum):
    FINAL = "FINAL"
    PENDING_DISPUTE = "PENDING_DISPUTE"


class ScoringEligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    EXCLUDED_INVALID = "EXCLUDED_INVALID"
    EXCLUDED_PENDING_DISPUTE = "EXCLUDED_PENDING_DISPUTE"


class MarketResolution(CommonEnvelope):
    """Immutable settlement assertion backed by a replayable source artifact."""

    resolution_id: str
    market_id: str
    condition_id: str | None = None
    outcome: ResolutionOutcome
    adjudication_status: ResolutionAdjudicationStatus
    resolved_at: datetime
    source_observed_at: datetime
    source_artifact_id: str
    source_artifact_sha256: str
    rule_contract_id: str
    rule_contract_sha256: str
    rule_hash: str
    contract_revision_id: str
    parser_version: str
    supersedes_resolution_id: str | None = None
    supersedes_resolution_sha256: str | None = None

    @field_validator(
        "source_artifact_sha256",
        "rule_contract_sha256",
        "rule_hash",
        "supersedes_resolution_sha256",
    )
    @classmethod
    def resolution_hashes_are_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("resolved_at", "source_observed_at")
    @classmethod
    def resolution_times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("market_id", "source_artifact_id", "rule_contract_id", "contract_revision_id", "parser_version")
    @classmethod
    def resolution_text_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("resolution text fields must not be blank")
        return value

    @model_validator(mode="after")
    def resolution_lineage_is_consistent(self) -> "MarketResolution":
        if self.resolution_id != self.record_id:
            raise ValueError("resolution_id must equal record_id")
        if self.resolved_at > self.source_observed_at or self.source_observed_at > self.created_at:
            raise ValueError("resolution clocks must be resolved_at <= observed_at <= created_at")
        if (self.supersedes_resolution_id is None) != (
            self.supersedes_resolution_sha256 is None
        ):
            raise ValueError("superseded resolution id and hash must be set together")
        if self.supersedes_resolution_id == self.resolution_id:
            raise ValueError("a resolution cannot supersede itself")
        return self


class SimulationEntryBasis(AlphaContract):
    """Frozen offline fill assumption; never evidence of an actual order or fill."""

    direction: Literal["YES", "NO"]
    token_id: str
    quantity: Decimal = Field(gt=0)
    entry_vwap: Decimal = Field(ge=0, le=1)
    gross_cost: Decimal = Field(ge=0)
    fee_amount: Decimal = Field(ge=0)
    fee_model_version: str
    orderbook_snapshot_id: str
    orderbook_snapshot_sha256: str

    @field_validator("orderbook_snapshot_sha256")
    @classmethod
    def entry_book_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("token_id", "fee_model_version", "orderbook_snapshot_id")
    @classmethod
    def entry_text_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("simulation entry fields must not be blank")
        return value

    @model_validator(mode="after")
    def entry_cost_is_exact(self) -> "SimulationEntryBasis":
        if self.gross_cost != self.quantity * self.entry_vwap:
            raise ValueError("gross_cost must equal quantity * entry_vwap")
        return self


class PredictionResolutionLink(CommonEnvelope):
    """Append-only binding from one frozen prediction to one resolution assertion."""

    link_id: str
    market_id: str
    prediction_id: str
    prediction_sha256: str
    decision_id: str
    decision_sha256: str
    probability_estimate_id: str
    probability_estimate_sha256: str
    rule_contract_id: str
    rule_contract_sha256: str
    resolution_id: str
    resolution_sha256: str
    linked_at: datetime
    scoring_eligibility: ScoringEligibility
    exclusion_reason: str | None = None
    predicted_probability: Decimal = Field(ge=0, le=1)
    market_baseline_probability: Decimal | None = Field(default=None, ge=0, le=1)
    market_type: str
    rule_clarity: Decimal = Field(ge=0, le=1)
    entry_basis: SimulationEntryBasis | None = None

    @field_validator(
        "prediction_sha256",
        "decision_sha256",
        "probability_estimate_sha256",
        "rule_contract_sha256",
        "resolution_sha256",
    )
    @classmethod
    def link_hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("linked_at")
    @classmethod
    def linked_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator(
        "market_id",
        "prediction_id",
        "decision_id",
        "probability_estimate_id",
        "rule_contract_id",
        "resolution_id",
        "market_type",
    )
    @classmethod
    def link_text_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prediction-resolution link fields must not be blank")
        return value

    @model_validator(mode="after")
    def link_semantics_are_consistent(self) -> "PredictionResolutionLink":
        if self.link_id != self.record_id:
            raise ValueError("link_id must equal record_id")
        if self.scoring_eligibility == ScoringEligibility.ELIGIBLE:
            if self.exclusion_reason is not None:
                raise ValueError("eligible links cannot carry an exclusion reason")
        elif self.exclusion_reason is None or not self.exclusion_reason.strip():
            raise ValueError("excluded links require an explicit reason")
        return self


class PredictionScore(CommonEnvelope):
    """Versioned derived score; source prediction and resolution remain untouched."""

    score_id: str
    link_id: str
    link_sha256: str
    prediction_id: str
    resolution_id: str
    outcome: Literal["YES", "NO"]
    outcome_label: Literal[0, 1]
    predicted_probability: Decimal = Field(ge=0, le=1)
    market_baseline_probability: Decimal | None = Field(default=None, ge=0, le=1)
    brier_score: Decimal = Field(ge=0)
    log_loss: Decimal = Field(ge=0)
    market_baseline_brier_score: Decimal | None = Field(default=None, ge=0)
    market_baseline_log_loss: Decimal | None = Field(default=None, ge=0)
    simulated_pnl: Decimal | None = None
    market_type: str
    rule_clarity: Decimal = Field(ge=0, le=1)
    entry_vwap: Decimal | None = Field(default=None, ge=0, le=1)
    scoring_policy_version: str
    log_loss_epsilon: Decimal = Field(gt=0, lt=1)
    scoring_policy_sha256: str

    @field_validator("link_sha256", "scoring_policy_sha256")
    @classmethod
    def score_hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("link_id", "prediction_id", "resolution_id", "market_type", "scoring_policy_version")
    @classmethod
    def score_text_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prediction score fields must not be blank")
        return value

    @model_validator(mode="after")
    def score_semantics_are_consistent(self) -> "PredictionScore":
        if self.score_id != self.record_id:
            raise ValueError("score_id must equal record_id")
        if (self.outcome == "YES") != (self.outcome_label == 1):
            raise ValueError("outcome label must encode YES=1 and NO=0")
        baseline = (
            self.market_baseline_probability,
            self.market_baseline_brier_score,
            self.market_baseline_log_loss,
        )
        if any(value is None for value in baseline) and any(
            value is not None for value in baseline
        ):
            raise ValueError("market baseline probability and scores must be set together")
        if (self.entry_vwap is None) != (self.simulated_pnl is None):
            raise ValueError("entry_vwap and simulated_pnl must be set together")
        return self


class CalibrationDimension(StrEnum):
    MARKET_TYPE = "MARKET_TYPE"
    RULE_CLARITY = "RULE_CLARITY"
    ENTRY_PRICE = "ENTRY_PRICE"


class ScoreReference(AlphaContract):
    score_id: str
    score_sha256: str

    @field_validator("score_sha256")
    @classmethod
    def score_reference_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)


class CalibrationSlice(AlphaContract):
    slice_key: str
    count: int = Field(gt=0)
    mean_predicted_probability: Decimal = Field(ge=0, le=1)
    observed_yes_rate: Decimal = Field(ge=0, le=1)
    mean_brier_score: Decimal = Field(ge=0)
    mean_log_loss: Decimal = Field(ge=0)
    mean_market_baseline_brier_score: Decimal | None = Field(default=None, ge=0)
    mean_market_baseline_log_loss: Decimal | None = Field(default=None, ge=0)
    mean_simulated_pnl: Decimal | None = None


class CalibrationReport(CommonEnvelope):
    report_id: str
    dimension: CalibrationDimension
    score_references: tuple[ScoreReference, ...]
    slices: tuple[CalibrationSlice, ...]
    excluded_score_ids: tuple[str, ...] = ()
    calibration_policy_version: str
    rule_clarity_boundaries: tuple[Decimal, ...]
    entry_price_boundaries: tuple[Decimal, ...]
    calibration_policy_sha256: str

    @field_validator("calibration_policy_sha256")
    @classmethod
    def calibration_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def calibration_report_is_consistent(self) -> "CalibrationReport":
        if self.report_id != self.record_id:
            raise ValueError("report_id must equal record_id")
        score_ids = tuple(item.score_id for item in self.score_references)
        if not score_ids or len(score_ids) != len(set(score_ids)):
            raise ValueError("calibration report requires unique score references")
        if tuple(sorted(score_ids)) != score_ids:
            raise ValueError("score references must be sorted by score_id")
        slice_keys = tuple(item.slice_key for item in self.slices)
        if not slice_keys or len(slice_keys) != len(set(slice_keys)):
            raise ValueError("calibration report requires unique non-empty slices")
        if tuple(sorted(slice_keys)) != slice_keys:
            raise ValueError("calibration slices must be sorted by slice_key")
        if tuple(sorted(set(self.excluded_score_ids))) != self.excluded_score_ids:
            raise ValueError("excluded score ids must be unique and sorted")
        if not set(self.excluded_score_ids).issubset(score_ids):
            raise ValueError("excluded score ids must remain hash-bound score references")
        for name, boundaries in (
            ("rule_clarity_boundaries", self.rule_clarity_boundaries),
            ("entry_price_boundaries", self.entry_price_boundaries),
        ):
            if (
                len(boundaries) < 2
                or boundaries[0] != Decimal("0")
                or boundaries[-1] != Decimal("1")
                or tuple(sorted(set(boundaries))) != boundaries
            ):
                raise ValueError(f"{name} must be unique, sorted, and span 0 through 1")
        return self


CONTRACT_MODELS = (
    MarketIdentity,
    MarketAlias,
    MarketSnapshot,
    MarketChangeEvent,
    BookLevel,
    BookLeg,
    TargetDepthMetrics,
    OrderbookSnapshot,
    BookCaptureDemand,
    BookCaptureReceipt,
    RecallHit,
    CandidateCard,
    CandidateTransition,
    ThresholdSpec,
    RuleContract,
    SourceArtifact,
    ClaimEvidence,
    BlindResearchQuestion,
    BlindCandidateProjection,
    BlindRuleView,
    BlindResearchPacket,
    MarketResearchPacket,
    ProbabilityEstimate,
    ResearchResultEnvelope,
    ResearchImportReceipt,
    ReviewDecision,
    PredictionRecord,
)

P1_CONTRACT_MODELS = (
    MarketResolution,
    PredictionResolutionLink,
    PredictionScore,
    CalibrationReport,
)

P1_AUTOMATION_CONTRACT_MODELS = (
    ResearchJob,
    ResearchAttempt,
    ResearchWorkOrder,
    ResearchReturnReceipt,
    ResearchJobTransition,
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


def p1_contract_schema_bundle() -> dict[str, Any]:
    return {model.__name__: model.model_json_schema() for model in P1_CONTRACT_MODELS}


def p1_automation_contract_schema_bundle() -> dict[str, Any]:
    return {
        model.__name__: model.model_json_schema()
        for model in P1_AUTOMATION_CONTRACT_MODELS
    }


def p1_automation_contract_schema_fingerprint() -> str:
    encoded = json.dumps(
        p1_automation_contract_schema_bundle(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def p1_contract_schema_fingerprint() -> str:
    encoded = json.dumps(
        p1_contract_schema_bundle(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
