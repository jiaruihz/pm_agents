"""P0-07 rule compiler inputs, receipts, and gate traces.

These models are local to the rule workstream.  The shared RuleContract remains
owned by P0-01; P0-07 only consumes it and adds auditable compilation traces.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from src.polymarket_alpha.contracts import (
    AlphaContract,
    CommonEnvelope,
    RuleContract,
    ThresholdSpec,
    bytes_sha256,
)
from src.polymarket_alpha.contracts.base import ensure_utc, validate_sha256


class ParseStatus(StrEnum):
    PARSED = "PARSED"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


class CompilationStatus(StrEnum):
    COMPILED = "COMPILED"
    BLOCKED = "BLOCKED"


class RuleGateStage(StrEnum):
    A = "A"
    B = "B"


class RuleSourceEvidence(AlphaContract):
    """Frozen source text with exact quote offsets for compiled rule fields."""

    field_names: tuple[str, ...]
    source_artifact_id: str
    source_content_sha256: str
    artifact_text: str
    quote_start: int = Field(ge=0)
    quote_end: int = Field(gt=0)
    legal_role: str
    adjudication_use: Literal["binding", "excluded", "review_required"] = "binding"
    observed_at: datetime

    @field_validator("field_names")
    @classmethod
    def field_names_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if not normalized or any(not item for item in normalized):
            raise ValueError("source evidence must identify at least one compiled field")
        if len(normalized) != len(set(normalized)):
            raise ValueError("source evidence field names must be unique")
        return normalized

    @field_validator("source_content_sha256")
    @classmethod
    def source_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("source_artifact_id", "legal_role")
    @classmethod
    def required_text_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source evidence identifiers must not be blank")
        return value

    @model_validator(mode="after")
    def source_hash_and_quote_are_exact(self) -> "RuleSourceEvidence":
        if bytes_sha256(self.artifact_text.encode("utf-8")) != self.source_content_sha256:
            raise ValueError("source_content_sha256 does not match frozen artifact_text")
        if self.quote_end > len(self.artifact_text) or self.quote_start >= self.quote_end:
            raise ValueError("quote offsets must select a non-empty source span")
        if not self.artifact_text[self.quote_start : self.quote_end].strip():
            raise ValueError("source quote must not be blank")
        return self

    @property
    def quote(self) -> str:
        return self.artifact_text[self.quote_start : self.quote_end]


class CorpusSourceIdentity(AlphaContract):
    source_path: str
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    schema_sha256: str

    @field_validator("source_path")
    @classmethod
    def source_path_is_absolute(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("dispute corpus source_path must be absolute")
        return str(path)

    @field_validator("schema_sha256")
    @classmethod
    def schema_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)


class RuleCorpusSnapshot(AlphaContract):
    contract_corpus_sha256: str
    as_of: datetime
    source_identity: CorpusSourceIdentity
    source_artifact_id: str
    fragments: tuple[RuleSourceEvidence, ...]
    adjudication_blockers: tuple[str, ...] = ()
    deterministic_verdict_allowed: bool

    @field_validator("contract_corpus_sha256")
    @classmethod
    def corpus_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("adjudication_blockers")
    @classmethod
    def blockers_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("adjudication blockers must be unique")
        return value

    @model_validator(mode="after")
    def corpus_has_lineage_and_consistent_gate(self) -> "RuleCorpusSnapshot":
        if not self.fragments:
            raise ValueError("contract corpus must contain frozen source fragments")
        artifact_ids = [item.source_artifact_id for item in self.fragments]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("contract corpus fragment artifact ids must be unique")
        if self.deterministic_verdict_allowed == bool(self.adjudication_blockers):
            raise ValueError("corpus deterministic flag conflicts with adjudication blockers")
        return self


class StructuredRuleParse(AlphaContract):
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

    @field_validator("deadline")
    @classmethod
    def deadline_is_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @field_validator("subject_entity", "entity_match_rule", "yes_trigger", "timezone")
    @classmethod
    def required_text_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("structured rule fields must not be blank")
        return value

    @field_validator(
        "resolution_sources",
        "source_precedence",
        "qualifying_examples",
        "non_qualifying_examples",
        "ambiguities",
    )
    @classmethod
    def tuple_values_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("structured rule tuple values must be nonblank and unique")
        return normalized

    @model_validator(mode="after")
    def sources_and_precedence_are_explicit(self) -> "StructuredRuleParse":
        if not self.resolution_sources or not self.source_precedence:
            raise ValueError("resolution sources and precedence must be explicit")
        return self


class RuleCompilationRequest(AlphaContract):
    run_id: str
    market_id: str
    market_snapshot_id: str
    raw_rule_text: str
    expected_rule_hash: str
    parse_status: ParseStatus
    parsed: StructuredRuleParse | None = None
    parser_version: str
    compiled_at: datetime
    source_evidence: tuple[RuleSourceEvidence, ...] = ()
    corpus: RuleCorpusSnapshot | None = None

    @field_validator("expected_rule_hash")
    @classmethod
    def expected_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("compiled_at")
    @classmethod
    def compiled_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("run_id", "market_id", "market_snapshot_id", "parser_version")
    @classmethod
    def identifiers_are_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("compilation identifiers must not be blank")
        return value

    @field_validator("raw_rule_text")
    @classmethod
    def raw_rule_text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("raw rule text must not be blank")
        return value

    @model_validator(mode="after")
    def parsed_payload_matches_status(self) -> "RuleCompilationRequest":
        if self.parse_status == ParseStatus.PARSED and self.parsed is None:
            raise ValueError("PARSED status requires structured parse payload")
        if self.parse_status != ParseStatus.PARSED and self.parsed is not None:
            raise ValueError("failed parser status cannot carry a structured parse")
        return self


class RuleCompilationReceipt(CommonEnvelope):
    market_id: str
    market_snapshot_id: str
    input_rule_hash: str
    contract_corpus_sha256: str | None = None
    compiler_version: str
    status: CompilationStatus
    rule_contract_id: str | None = None
    reasons: tuple[str, ...]
    source_evidence_ids: tuple[str, ...] = ()
    source_evidence: tuple[RuleSourceEvidence, ...] = ()

    @field_validator("input_rule_hash", "contract_corpus_sha256")
    @classmethod
    def hashes_are_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("market_id", "market_snapshot_id", "compiler_version")
    @classmethod
    def identifiers_are_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("compilation receipt identifiers must not be blank")
        return value

    @model_validator(mode="after")
    def result_shape_matches_status(self) -> "RuleCompilationReceipt":
        if not self.reasons or len(self.reasons) != len(set(self.reasons)):
            raise ValueError("compilation receipt reasons must be non-empty and unique")
        if self.status == CompilationStatus.COMPILED and self.rule_contract_id is None:
            raise ValueError("compiled receipt requires RuleContract id")
        if self.status == CompilationStatus.BLOCKED and self.rule_contract_id is not None:
            raise ValueError("blocked receipt cannot claim a RuleContract")
        if self.source_evidence_ids != tuple(
            sorted({item.source_artifact_id for item in self.source_evidence})
        ):
            raise ValueError("source evidence ids must match the frozen quote trace")
        return self


class RuleCompilationOutcome(AlphaContract):
    contract: RuleContract | None
    receipt: RuleCompilationReceipt

    @model_validator(mode="after")
    def contract_and_receipt_agree(self) -> "RuleCompilationOutcome":
        if self.contract is None:
            if self.receipt.status != CompilationStatus.BLOCKED:
                raise ValueError("missing contract requires blocked receipt")
        elif (
            self.receipt.status != CompilationStatus.COMPILED
            or self.receipt.rule_contract_id != self.contract.record_id
        ):
            raise ValueError("compiled contract and receipt do not agree")
        return self


class RuleGateDecision(CommonEnvelope):
    stage: RuleGateStage
    market_id: str
    rule_contract_id: str
    rule_hash: str
    contract_revision_id: str
    compiler_version: str
    decision: Literal[
        "PASS",
        "WATCH_RULE",
        "REJECT_RULE",
        "PASS_WITH_RULE_RISK",
        "BLOCK",
    ]
    reasons: tuple[str, ...]
    input_artifact_ids: tuple[str, ...]
    evaluated_at: datetime
    gate_a_decision_id: str | None = None

    @field_validator("rule_hash")
    @classmethod
    def rule_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator(
        "market_id",
        "rule_contract_id",
        "contract_revision_id",
        "compiler_version",
    )
    @classmethod
    def identifiers_are_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("gate decision identifiers must not be blank")
        return value

    @field_validator("evaluated_at")
    @classmethod
    def evaluated_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def stage_decision_shape_is_valid(self) -> "RuleGateDecision":
        allowed = (
            {"PASS", "WATCH_RULE", "REJECT_RULE"}
            if self.stage == RuleGateStage.A
            else {"PASS", "PASS_WITH_RULE_RISK", "BLOCK"}
        )
        if self.decision not in allowed:
            raise ValueError("gate decision is invalid for its stage")
        if not self.reasons or len(self.reasons) != len(set(self.reasons)):
            raise ValueError("gate decision reasons must be non-empty and unique")
        if (
            not self.input_artifact_ids
            or any(not item.strip() for item in self.input_artifact_ids)
            or len(self.input_artifact_ids) != len(set(self.input_artifact_ids))
        ):
            raise ValueError("gate decision input artifacts must be non-empty and unique")
        if (self.stage == RuleGateStage.B) != (self.gate_a_decision_id is not None):
            raise ValueError("only Gate B must reference its Gate A decision")
        return self
