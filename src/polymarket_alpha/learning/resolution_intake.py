"""Offline intake seam for caller-captured P1 resolution source evidence.

This module deliberately does not fetch, parse, store, or interpret a remote
resolution source.  It only binds bytes already captured by a caller to that
caller's parser assertion and to the frozen RuleContract that authorizes it.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..contracts import (
    CaptureScope,
    HashScope,
    MarketResolution,
    Replayability,
    ResolutionAdjudicationStatus,
    ResolutionOutcome,
    RuleContract,
    SourceArtifact,
    bytes_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256
from .resolution import LearningResolutionError, build_market_resolution


RESOLUTION_INTAKE_VERSION = "p1_resolution_intake_v1"


class ResolutionIntakeError(LearningResolutionError):
    """Raised when a caller-supplied captured resolution source is unsafe."""


class CapturedResolutionSource(BaseModel):
    """A frozen assertion over bytes captured before this pure offline call.

    ``parser_assertion`` is intentionally audit metadata, not an instruction to
    execute a parser.  This seam does not claim to understand arbitrary market
    rules or independently derive an outcome from the bytes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    raw_bytes: bytes = Field(min_length=1)
    declared_raw_sha256: str
    declared_content_length_bytes: int = Field(gt=0)
    source_name: str
    source_url_or_source_id: str
    media_type: str
    artifact_locator: str
    capture_scope: CaptureScope
    captured_at: datetime
    effective_as_of: datetime
    declared_resolution_source: str
    declared_precedence_source: str
    parser_version: str
    parser_assertion: str

    @field_validator(
        "source_name", "source_url_or_source_id", "media_type", "artifact_locator",
        "declared_resolution_source", "declared_precedence_source",
        "parser_version", "parser_assertion",
    )
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("resolution source text fields must not be blank")
        return value

    @field_validator("declared_raw_sha256")
    @classmethod
    def valid_raw_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("captured_at", "effective_as_of")
    @classmethod
    def utc_times(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def captured_content_is_consistent(self) -> "CapturedResolutionSource":
        if self.capture_scope == CaptureScope.REFERENCE_ONLY:
            raise ValueError("resolution intake requires replayable captured bytes")
        if self.declared_content_length_bytes != len(self.raw_bytes):
            raise ValueError("declared content length does not match raw bytes")
        if self.declared_raw_sha256 != bytes_sha256(self.raw_bytes):
            raise ValueError("declared raw SHA-256 does not match raw bytes")
        if self.effective_as_of > self.captured_at:
            raise ValueError("effective_as_of cannot be after captured_at")
        return self


class ResolutionIntakeRequest(BaseModel):
    """Caller-supplied parsed settlement assertion and its captured evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    source: CapturedResolutionSource
    market_id: str
    expected_condition_id: str
    parsed_condition_id: str
    outcome: ResolutionOutcome
    adjudication_status: ResolutionAdjudicationStatus
    resolved_at: datetime
    source_observed_at: datetime
    created_at: datetime
    run_id: str
    supersedes: MarketResolution | None = None

    @field_validator("market_id", "expected_condition_id", "parsed_condition_id", "run_id")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("resolution binding text fields must not be blank")
        return value

    @field_validator("resolved_at", "source_observed_at", "created_at")
    @classmethod
    def utc_times(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def assertion_is_fail_closed(self) -> "ResolutionIntakeRequest":
        if self.expected_condition_id != self.parsed_condition_id:
            raise ValueError("parsed condition id must equal expected condition id")
        if self.resolved_at > self.source_observed_at or self.source_observed_at > self.created_at:
            raise ValueError("resolution clocks must be resolved <= observed <= created")
        # An unresolved dispute may carry a provisional binary assertion, but
        # cannot label the market INVALID. INVALID is itself a final outcome.
        if (
            self.adjudication_status == ResolutionAdjudicationStatus.PENDING_DISPUTE
            and self.outcome == ResolutionOutcome.INVALID
        ):
            raise ValueError("PENDING_DISPUTE cannot assert INVALID")
        return self


class ResolutionIntakeResult(BaseModel):
    """Immutable pair to persist atomically by a higher-level repository owner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_artifact: SourceArtifact
    resolution: MarketResolution


def _frozen(value: BaseModel, cls: type[BaseModel]) -> BaseModel:
    if not isinstance(value, cls):
        raise ResolutionIntakeError(f"expected {cls.__name__}")
    try:
        rebuilt = cls.model_validate(value.model_dump(mode="python"))
    except ValueError as exc:
        raise ResolutionIntakeError(f"invalid frozen {cls.__name__}: {exc}") from exc
    if hasattr(value, "canonical_sha256") and rebuilt.canonical_sha256 != value.canonical_sha256:
        raise ResolutionIntakeError(f"{cls.__name__} canonical replay mismatch")
    return rebuilt


_SPACE = re.compile(r"\s+")


def _source_key(value: str) -> str:
    """NFC/case/whitespace normalization followed by exact equality only."""

    return _SPACE.sub(" ", unicodedata.normalize("NFC", value).strip()).casefold()


def _assert_authorized_source(*, source: CapturedResolutionSource, contract: RuleContract) -> None:
    resolution_declared = _source_key(source.declared_resolution_source)
    declared = _source_key(source.declared_precedence_source)
    resolution_sources = tuple(_source_key(item) for item in contract.resolution_sources)
    precedence = tuple(_source_key(item) for item in contract.source_precedence)
    if not resolution_declared or resolution_sources.count(resolution_declared) != 1:
        raise ResolutionIntakeError("declared resolution source is not uniquely authorized by RuleContract")
    if not declared or precedence.count(declared) != 1:
        raise ResolutionIntakeError("declared precedence source is not uniquely authorized by RuleContract")
    source_keys = {_source_key(source.source_name), _source_key(source.source_url_or_source_id)}
    if resolution_declared not in source_keys:
        raise ResolutionIntakeError("source name or URL must exactly match declared resolution source")


def _artifact_for(*, request: ResolutionIntakeRequest) -> SourceArtifact:
    captured = request.source
    hash_scope = (
        HashScope.RAW_BYTES
        if captured.capture_scope == CaptureScope.FULL_DOCUMENT
        else HashScope.CLAIM_EXCERPT
    )
    replayability = (
        Replayability.FULL
        if captured.capture_scope == CaptureScope.FULL_DOCUMENT
        else Replayability.EXCERPT
    )
    artifact_id = stable_record_id(
        "source_artifact", "resolution_intake", request.run_id,
        captured.source_name, captured.source_url_or_source_id, captured.declared_raw_sha256,
        captured.declared_content_length_bytes, captured.capture_scope.value, captured.captured_at,
        captured.effective_as_of, captured.artifact_locator,
        captured.declared_resolution_source, captured.declared_precedence_source,
        captured.parser_version,
        captured.parser_assertion, request.parsed_condition_id, request.outcome.value,
        request.adjudication_status.value,
    )
    return SourceArtifact(
        record_id=artifact_id,
        artifact_id=artifact_id,
        run_id=request.run_id,
        created_at=request.created_at,
        source="alpha_p1_resolution_intake",
        source_version=RESOLUTION_INTAKE_VERSION,
        provenance=(),
        # The source bytes and the caller's interpretation are different
        # facts, but both must remain hash-bound. Keeping the assertion in the
        # artifact envelope prevents a persisted MarketResolution from losing
        # the exact parser claim that produced its outcome.
        extensions={
            "resolution_intake": {
                "declared_resolution_source": captured.declared_resolution_source,
                "declared_precedence_source": captured.declared_precedence_source,
                "parser_version": captured.parser_version,
                "parser_assertion": captured.parser_assertion,
                "parsed_condition_id": request.parsed_condition_id,
                "outcome": request.outcome.value,
                "adjudication_status": request.adjudication_status.value,
            }
        },
        source_name=captured.source_name,
        source_url_or_source_id=captured.source_url_or_source_id,
        media_type=captured.media_type,
        captured_at=captured.captured_at,
        effective_as_of=captured.effective_as_of,
        capture_scope=captured.capture_scope,
        hash_scope=hash_scope,
        content_sha256=captured.declared_raw_sha256,
        content_length_bytes=captured.declared_content_length_bytes,
        artifact_locator=captured.artifact_locator,
        replayability=replayability,
    )


def intake_captured_resolution(
    *, request: ResolutionIntakeRequest, rule_contract: RuleContract,
) -> ResolutionIntakeResult:
    """Bind a caller-captured source to an immutable MarketResolution.

    No network, filesystem, database, process, or parser capability is used.
    The caller remains responsible for capture and parsing; this function only
    verifies the captured bytes, source authorization, and binding invariants.
    """

    frozen_request = _frozen(request, ResolutionIntakeRequest)
    contract = _frozen(rule_contract, RuleContract)
    assert isinstance(frozen_request, ResolutionIntakeRequest)
    assert isinstance(contract, RuleContract)
    if frozen_request.market_id != contract.market_id:
        raise ResolutionIntakeError("market id must bind to RuleContract")
    if frozen_request.created_at < contract.created_at:
        raise ResolutionIntakeError("intake creation cannot precede RuleContract")
    _assert_authorized_source(source=frozen_request.source, contract=contract)
    artifact = _artifact_for(request=frozen_request)
    try:
        resolution = build_market_resolution(
            source_artifact=artifact,
            rule_contract=contract,
            market_id=frozen_request.market_id,
            condition_id=frozen_request.expected_condition_id,
            outcome=frozen_request.outcome,
            adjudication_status=frozen_request.adjudication_status,
            resolved_at=frozen_request.resolved_at,
            source_observed_at=frozen_request.source_observed_at,
            created_at=frozen_request.created_at,
            run_id=frozen_request.run_id,
            parser_version=frozen_request.source.parser_version,
            supersedes=frozen_request.supersedes,
        )
    except LearningResolutionError as exc:
        raise ResolutionIntakeError(str(exc)) from exc
    return ResolutionIntakeResult(source_artifact=artifact, resolution=resolution)
