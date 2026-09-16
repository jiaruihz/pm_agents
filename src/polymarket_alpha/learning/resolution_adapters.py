"""Offline source-specific adapters for caller-captured resolution bytes.

Adapters own deterministic parsing only.  They never fetch a source, infer a
fallback from Gamma lifecycle state, touch storage, or mutate a prediction.
Every successful parse is passed through the existing resolution-intake seam.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..contracts import (
    CaptureScope,
    MarketResolution,
    ResolutionAdjudicationStatus,
    ResolutionOutcome,
    RuleContract,
    bytes_sha256,
)
from ..contracts.base import ensure_utc, validate_sha256
from .resolution_intake import (
    CapturedResolutionSource,
    ResolutionIntakeError,
    ResolutionIntakeRequest,
    ResolutionIntakeResult,
    intake_captured_resolution,
)


RESOLUTION_ADAPTER_VERSION = "p1_resolution_adapter_v1"


class ResolutionAdapterFailureCode(StrEnum):
    CONTENT_BINDING_MISMATCH = "CONTENT_BINDING_MISMATCH"
    UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    MALFORMED_SOURCE = "MALFORMED_SOURCE"
    AMBIGUOUS_SOURCE = "AMBIGUOUS_SOURCE"
    CONDITION_MISMATCH = "CONDITION_MISMATCH"
    SUPERSESSION_MISMATCH = "SUPERSESSION_MISMATCH"
    INTAKE_REJECTED = "INTAKE_REJECTED"


class ResolutionAdapterError(ResolutionIntakeError):
    def __init__(self, code: ResolutionAdapterFailureCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code


class CapturedOfficialResolution(BaseModel):
    """Transport-free envelope for bytes already captured by a caller."""

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
    market_id: str
    expected_condition_id: str
    source_observed_at: datetime
    created_at: datetime
    run_id: str
    supersedes: MarketResolution | None = None

    @field_validator("declared_raw_sha256")
    @classmethod
    def hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("captured_at", "effective_as_of", "source_observed_at", "created_at")
    @classmethod
    def times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator(
        "source_name",
        "source_url_or_source_id",
        "media_type",
        "artifact_locator",
        "market_id",
        "expected_condition_id",
        "run_id",
    )
    @classmethod
    def text_is_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("captured resolution fields must not be blank")
        return value

    @model_validator(mode="after")
    def bytes_and_clocks_are_consistent(self) -> "CapturedOfficialResolution":
        if self.capture_scope == CaptureScope.REFERENCE_ONLY:
            raise ValueError("resolution adapter requires captured source bytes")
        if self.declared_content_length_bytes != len(self.raw_bytes):
            raise ValueError("declared content length does not match raw bytes")
        if self.declared_raw_sha256 != bytes_sha256(self.raw_bytes):
            raise ValueError("declared raw SHA-256 does not match raw bytes")
        if not (
            self.effective_as_of <= self.captured_at
            and self.captured_at <= self.source_observed_at
            and self.source_observed_at <= self.created_at
        ):
            raise ValueError("captured resolution clocks are inconsistent")
        return self


class ResolutionSourcePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    policy_id: str
    source_name: str
    source_url_or_source_id: str
    media_type: str
    declared_resolution_source: str
    declared_precedence_source: str
    parser_version: str

    @field_validator(
        "policy_id",
        "source_name",
        "source_url_or_source_id",
        "media_type",
        "declared_resolution_source",
        "declared_precedence_source",
        "parser_version",
    )
    @classmethod
    def text_is_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("resolution source policy fields must not be blank")
        return value


class ParsedResolutionAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    condition_id: str
    outcome: ResolutionOutcome
    adjudication_status: ResolutionAdjudicationStatus
    resolved_at: datetime
    supersedes_resolution_id: str | None = None

    @field_validator("condition_id")
    @classmethod
    def condition_is_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("parsed condition_id must not be blank")
        return value

    @field_validator("resolved_at")
    @classmethod
    def resolved_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class ResolutionSourceAdapter(Protocol):
    policy: ResolutionSourcePolicy

    def parse(self, raw_bytes: bytes) -> ParsedResolutionAssertion: ...


class OfficialJsonResolutionAdapter:
    """Strict JSON document adapter with a frozen field contract."""

    def __init__(self, policy: ResolutionSourcePolicy) -> None:
        self.policy = policy

    def parse(self, raw_bytes: bytes) -> ParsedResolutionAssertion:
        try:
            decoded = raw_bytes.decode("utf-8")
            payload = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResolutionAdapterError(
                ResolutionAdapterFailureCode.MALFORMED_SOURCE,
                "official JSON is not valid UTF-8 JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise ResolutionAdapterError(
                ResolutionAdapterFailureCode.MALFORMED_SOURCE,
                "official JSON must be one object",
            )
        allowed = {
            "condition_id",
            "outcome",
            "adjudication_status",
            "resolved_at",
            "supersedes_resolution_id",
        }
        if set(payload) - allowed:
            raise ResolutionAdapterError(
                ResolutionAdapterFailureCode.AMBIGUOUS_SOURCE,
                "official JSON contains unsupported settlement fields",
            )
        try:
            return ParsedResolutionAssertion.model_validate(payload)
        except ValueError as exc:
            raise ResolutionAdapterError(
                ResolutionAdapterFailureCode.MALFORMED_SOURCE,
                f"official JSON settlement assertion is invalid: {exc}",
            ) from exc


class OfficialKeyValueResolutionAdapter:
    """Strict line-oriented adapter for signed/plain official bulletins."""

    _FIELDS = {
        "CONDITION_ID": "condition_id",
        "OUTCOME": "outcome",
        "ADJUDICATION_STATUS": "adjudication_status",
        "RESOLVED_AT": "resolved_at",
        "SUPERSEDES_RESOLUTION_ID": "supersedes_resolution_id",
    }

    def __init__(self, policy: ResolutionSourcePolicy) -> None:
        self.policy = policy

    def parse(self, raw_bytes: bytes) -> ParsedResolutionAssertion:
        try:
            lines = raw_bytes.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ResolutionAdapterError(
                ResolutionAdapterFailureCode.MALFORMED_SOURCE,
                "official key-value bulletin is not UTF-8",
            ) from exc
        parsed: dict[str, str] = {}
        for raw_line in lines:
            if not raw_line.strip():
                continue
            if ":" not in raw_line:
                raise ResolutionAdapterError(
                    ResolutionAdapterFailureCode.MALFORMED_SOURCE,
                    "official key-value bulletin contains a non-field line",
                )
            key, value = (part.strip() for part in raw_line.split(":", 1))
            field = self._FIELDS.get(key)
            if field is None:
                raise ResolutionAdapterError(
                    ResolutionAdapterFailureCode.AMBIGUOUS_SOURCE,
                    f"unsupported official field: {key}",
                )
            if field in parsed:
                raise ResolutionAdapterError(
                    ResolutionAdapterFailureCode.AMBIGUOUS_SOURCE,
                    f"duplicate official field: {key}",
                )
            if not value:
                raise ResolutionAdapterError(
                    ResolutionAdapterFailureCode.MALFORMED_SOURCE,
                    f"blank official field: {key}",
                )
            parsed[field] = value
        try:
            return ParsedResolutionAssertion.model_validate(parsed)
        except ValueError as exc:
            raise ResolutionAdapterError(
                ResolutionAdapterFailureCode.MALFORMED_SOURCE,
                f"official key-value settlement assertion is invalid: {exc}",
            ) from exc


def _assert_policy(
    *, captured: CapturedOfficialResolution, policy: ResolutionSourcePolicy
) -> None:
    if (
        captured.source_name != policy.source_name
        or captured.source_url_or_source_id != policy.source_url_or_source_id
    ):
        raise ResolutionAdapterError(
            ResolutionAdapterFailureCode.UNSUPPORTED_SOURCE,
            "captured source identity does not match the frozen source policy",
        )
    if captured.media_type != policy.media_type:
        raise ResolutionAdapterError(
            ResolutionAdapterFailureCode.UNSUPPORTED_MEDIA_TYPE,
            "captured media type does not match the frozen source policy",
        )


def _assert_supersession(
    *, parsed: ParsedResolutionAssertion, captured: CapturedOfficialResolution
) -> None:
    expected = None if captured.supersedes is None else captured.supersedes.record_id
    if parsed.supersedes_resolution_id != expected:
        raise ResolutionAdapterError(
            ResolutionAdapterFailureCode.SUPERSESSION_MISMATCH,
            "parsed correction lineage does not match supplied superseded resolution",
        )


def adapt_captured_official_resolution(
    *,
    adapter: ResolutionSourceAdapter,
    captured: CapturedOfficialResolution,
    rule_contract: RuleContract,
) -> ResolutionIntakeResult:
    """Parse one captured source and pass it through canonical intake."""

    try:
        captured = CapturedOfficialResolution.model_validate(
            captured.model_dump(mode="python")
        )
        policy = ResolutionSourcePolicy.model_validate(
            adapter.policy.model_dump(mode="python")
        )
    except ValueError as exc:
        raise ResolutionAdapterError(
            ResolutionAdapterFailureCode.CONTENT_BINDING_MISMATCH,
            f"captured bytes or source policy are invalid: {exc}",
        ) from exc
    _assert_policy(captured=captured, policy=policy)
    parsed = adapter.parse(captured.raw_bytes)
    if parsed.condition_id != captured.expected_condition_id:
        raise ResolutionAdapterError(
            ResolutionAdapterFailureCode.CONDITION_MISMATCH,
            "parsed condition id does not match the expected canonical condition",
        )
    _assert_supersession(parsed=parsed, captured=captured)
    parser_assertion = json.dumps(
        {
            "adapter_version": RESOLUTION_ADAPTER_VERSION,
            "policy_id": policy.policy_id,
            "raw_sha256": captured.declared_raw_sha256,
            "condition_id": parsed.condition_id,
            "outcome": parsed.outcome.value,
            "adjudication_status": parsed.adjudication_status.value,
            "resolved_at": parsed.resolved_at.isoformat(),
            "supersedes_resolution_id": parsed.supersedes_resolution_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    source = CapturedResolutionSource(
        raw_bytes=captured.raw_bytes,
        declared_raw_sha256=captured.declared_raw_sha256,
        declared_content_length_bytes=captured.declared_content_length_bytes,
        source_name=captured.source_name,
        source_url_or_source_id=captured.source_url_or_source_id,
        media_type=captured.media_type,
        artifact_locator=captured.artifact_locator,
        capture_scope=captured.capture_scope,
        captured_at=captured.captured_at,
        effective_as_of=captured.effective_as_of,
        declared_resolution_source=policy.declared_resolution_source,
        declared_precedence_source=policy.declared_precedence_source,
        parser_version=policy.parser_version,
        parser_assertion=parser_assertion,
    )
    request = ResolutionIntakeRequest(
        source=source,
        market_id=captured.market_id,
        expected_condition_id=captured.expected_condition_id,
        parsed_condition_id=parsed.condition_id,
        outcome=parsed.outcome,
        adjudication_status=parsed.adjudication_status,
        resolved_at=parsed.resolved_at,
        source_observed_at=captured.source_observed_at,
        created_at=captured.created_at,
        run_id=captured.run_id,
        supersedes=captured.supersedes,
    )
    try:
        return intake_captured_resolution(request=request, rule_contract=rule_contract)
    except ResolutionIntakeError as exc:
        raise ResolutionAdapterError(
            ResolutionAdapterFailureCode.INTAKE_REJECTED, str(exc)
        ) from exc
