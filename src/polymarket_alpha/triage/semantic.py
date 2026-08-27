"""Price-blind semantic triage boundary for external model sidecars.

This module deliberately owns no model client, network transport, subprocess,
browser, signing path, or order capability.  It freezes a small allowlist-only
projection, validates an untrusted structured return, and seals immutable
artifacts.  A separate operator-side process may invoke an approved model and
then call this importer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import Field, field_validator, model_validator

from ..artifacts import ArtifactStore
from ..contracts import (
    AlphaContract,
    CommonEnvelope,
    ProvenanceRef,
    bytes_sha256,
    canonical_json,
    stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256
from ..storage import AlphaRepository


GLM_SEMANTIC_TRIAGE_VERSION = "alpha_glm_semantic_triage_v1"

_FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "slug",
        "url",
        "market_url",
        "condition_id",
        "question_id",
        "token_id",
        "clob_token_ids",
        "outcomes",
        "outcome_prices",
        "price",
        "probability",
        "best_bid",
        "best_ask",
        "bid",
        "ask",
        "spread",
        "depth",
        "book",
        "candidate_direction",
        "order_side",
        "wallet_direction",
        "recall_reason",
    }
)
_FORBIDDEN_PROVIDER_KEY_FRAGMENTS = (
    "probability",
    "fair_value",
    "edge",
    "mispricing",
    "trade",
    "position",
    "order_side",
    "candidate_direction",
)
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"https?://(?:www\.)?polymarket\.com", re.IGNORECASE),
    re.compile(r"https?://(?:gamma-api|clob)\.polymarket\.com", re.IGNORECASE),
)
_FORBIDDEN_PROVIDER_TEXT_PATTERN = re.compile(
    r"\b(?:probability|fair[- ]?value|mispric(?:e|ing)|odds|trading action)\b"
    r"|\b(?:buy|sell|long|short)\s+(?:yes|no|shares?|position|contract)\b"
    r"|\d+(?:\.\d+)?%\s+(?:chance|probability|odds)\b",
    re.IGNORECASE,
)


class TriageIsolationError(ValueError):
    """The provider projection contains forbidden market semantics."""


class TriageResultError(ValueError):
    """The untrusted provider result is incomplete, mismatched, or unsafe."""


class SemanticTriageDisposition(StrEnum):
    ADVANCE = "ADVANCE"
    REVIEW = "REVIEW"
    DEFER = "DEFER"


class SemanticTriageEligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    DEADLINE_ELAPSED = "DEADLINE_ELAPSED"


class Researchability(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ResolutionSourceType(StrEnum):
    OFFICIAL = "OFFICIAL"
    CONSENSUS = "CONSENSUS"
    ORACLE = "ORACLE"
    OTHER = "OTHER"
    UNSPECIFIED = "UNSPECIFIED"


def _required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("value must not be blank")
    return value


def _unique_text(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(item.strip() for item in values)
    if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
        raise ValueError("tuple values must be nonblank and unique")
    return normalized


def _scan_forbidden(value: Any, *, provider_result: bool, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key).strip().lower().replace("-", "_")
            if key in _FORBIDDEN_INPUT_KEYS:
                raise TriageIsolationError(f"forbidden field at {path}.{raw_key}")
            if provider_result and any(
                key == fragment
                or key.startswith(f"{fragment}_")
                or key.endswith(f"_{fragment}")
                for fragment in _FORBIDDEN_PROVIDER_KEY_FRAGMENTS
            ):
                raise TriageResultError(f"forbidden provider field at {path}.{raw_key}")
            _scan_forbidden(nested, provider_result=provider_result, path=f"{path}.{raw_key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _scan_forbidden(nested, provider_result=provider_result, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        for pattern in _FORBIDDEN_VALUE_PATTERNS:
            if pattern.search(value):
                error = TriageResultError if provider_result else TriageIsolationError
                raise error(f"forbidden Polymarket source at {path}")
        if provider_result and _FORBIDDEN_PROVIDER_TEXT_PATTERN.search(value):
            raise TriageResultError(f"forbidden estimation or trading semantics at {path}")


class SemanticTriageInput(AlphaContract):
    item_id: str
    neutral_proposition: str
    rule_text: str
    deadline_utc: datetime | None = None
    event_family_titles: tuple[str, ...] = ()

    _item_required = field_validator("item_id", "neutral_proposition", "rule_text")(_required_text)
    _families_unique = field_validator("event_family_titles")(_unique_text)

    @field_validator("deadline_utc")
    @classmethod
    def deadline_is_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None


class SemanticTriageProjection(CommonEnvelope):
    projection_id: str
    policy_id: str = "price_blind_semantic_triage_v1"
    projection_policy_version: str = "v1"
    candidate_snapshot_id: str | None = None
    candidate_snapshot_sha256: str | None = None
    invalidation_parent_id: str | None = None
    items: tuple[SemanticTriageInput, ...]

    @model_validator(mode="after")
    def projection_is_isolated_and_unique(self) -> "SemanticTriageProjection":
        if self.projection_id != self.record_id:
            raise ValueError("projection_id must equal record_id")
        if not self.items:
            raise ValueError("semantic triage projection must contain items")
        ids = tuple(item.item_id for item in self.items)
        if len(ids) != len(set(ids)):
            raise ValueError("semantic triage item ids must be unique")
        _scan_forbidden(self.model_dump(mode="python"), provider_result=False)
        if bool(self.candidate_snapshot_id) != bool(self.candidate_snapshot_sha256):
            raise ValueError("candidate snapshot id/hash must occur together")
        if self.candidate_snapshot_sha256 is not None:
            validate_sha256(self.candidate_snapshot_sha256)
            if len(self.items) != 1:
                raise ValueError("a CandidateSnapshot-bound projection must contain one item")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self).encode("utf-8")


class SemanticTriageBinding(AlphaContract):
    item_id: str
    market_id: str
    source_market_sha256: str

    _binding_text = field_validator("item_id", "market_id")(_required_text)

    @field_validator("source_market_sha256")
    @classmethod
    def source_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)


class SemanticTriageItemResult(AlphaContract):
    item_id: str
    topic_family: str
    subject_entities: tuple[str, ...]
    deadline_interpretation: str
    resolution_source_type: ResolutionSourceType
    researchability: Researchability
    ambiguity_codes: tuple[str, ...] = ()
    duplicate_group_hint: str | None = None
    disposition: SemanticTriageDisposition
    confidence_milli: int = Field(ge=0, le=1000)
    reason_codes: tuple[str, ...]

    _required_fields = field_validator(
        "item_id", "topic_family", "deadline_interpretation"
    )(_required_text)
    _unique_entities = field_validator(
        "subject_entities", "ambiguity_codes", "reason_codes"
    )(_unique_text)

    @field_validator("duplicate_group_hint")
    @classmethod
    def duplicate_hint_is_not_blank(cls, value: str | None) -> str | None:
        return _required_text(value) if value is not None else None

    @model_validator(mode="after")
    def output_is_bounded(self) -> "SemanticTriageItemResult":
        if not self.subject_entities or not self.reason_codes:
            raise ValueError("triage result requires entities and reason codes")
        _scan_forbidden(self.model_dump(mode="python"), provider_result=True)
        return self


class SemanticTriageProviderResult(AlphaContract):
    projection_id: str
    items: tuple[SemanticTriageItemResult, ...]


class ProviderReturnMetadata(AlphaContract):
    provider: str
    requested_model: str
    reported_model: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    total_cost_usd_micros: int | None = Field(default=None, ge=0)
    usage: dict[str, int] = Field(default_factory=dict)

    _provider_text = field_validator("provider", "requested_model")(_required_text)

    @field_validator("reported_model")
    @classmethod
    def reported_model_not_blank(cls, value: str | None) -> str | None:
        return _required_text(value) if value is not None else None

    @field_validator("usage")
    @classmethod
    def usage_is_nonnegative(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not key.strip() or count < 0 for key, count in value.items()):
            raise ValueError("usage keys must be nonblank and counts nonnegative")
        return value


class SemanticTriageDecision(CommonEnvelope):
    decision_id: str
    projection_id: str
    item_id: str
    market_id: str
    result: SemanticTriageItemResult
    eligibility: SemanticTriageEligibility
    effective_disposition: SemanticTriageDisposition
    candidate_snapshot_id: str | None = None
    candidate_snapshot_sha256: str | None = None
    attempt_id: str | None = None
    model_only_terminal_rejection: bool = False

    @model_validator(mode="after")
    def decision_binding_is_exact(self) -> "SemanticTriageDecision":
        if self.decision_id != self.record_id:
            raise ValueError("decision_id must equal record_id")
        if self.item_id != self.result.item_id:
            raise ValueError("decision item does not match result item")
        expected = (
            SemanticTriageDisposition.DEFER
            if self.eligibility == SemanticTriageEligibility.DEADLINE_ELAPSED
            else self.result.disposition
        )
        if self.effective_disposition != expected:
            raise ValueError("effective disposition conflicts with deterministic eligibility")
        if self.model_only_terminal_rejection:
            raise ValueError("semantic triage cannot terminally reject a candidate")
        if bool(self.candidate_snapshot_id) != bool(self.candidate_snapshot_sha256):
            raise ValueError("candidate snapshot id/hash must occur together")
        if self.candidate_snapshot_sha256 is not None:
            validate_sha256(self.candidate_snapshot_sha256)
            if self.attempt_id is None or not self.attempt_id.strip():
                raise ValueError("snapshot-bound triage decision requires attempt_id")
        elif self.attempt_id is not None:
            raise ValueError("attempt_id requires a candidate snapshot binding")
        return self


class SemanticTriageReceipt(CommonEnvelope):
    receipt_id: str
    projection_id: str
    projection_sha256: str
    prompt_sha256: str
    provider_wrapper_sha256: str
    provider_result_sha256: str
    imported_at: datetime
    provider: ProviderReturnMetadata
    item_count: int = Field(gt=0)
    provider_dispositions: dict[str, int]
    dispositions: dict[str, int]
    execution: str = "NO_ORDER"
    candidate_snapshot_id: str | None = None
    candidate_snapshot_sha256: str | None = None
    attempt_id: str | None = None

    @field_validator("imported_at")
    @classmethod
    def imported_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator(
        "projection_sha256", "prompt_sha256", "provider_wrapper_sha256", "provider_result_sha256"
    )
    @classmethod
    def hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def receipt_is_consistent(self) -> "SemanticTriageReceipt":
        if self.receipt_id != self.record_id:
            raise ValueError("receipt_id must equal record_id")
        if self.execution != "NO_ORDER":
            raise ValueError("semantic triage receipt must remain NO_ORDER")
        expected = {item.value for item in SemanticTriageDisposition}
        for counts in (self.provider_dispositions, self.dispositions):
            if set(counts) != expected or any(count < 0 for count in counts.values()):
                raise ValueError("dispositions must contain exact nonnegative triage counts")
            if sum(counts.values()) != self.item_count:
                raise ValueError("disposition counts must equal item_count")
        if bool(self.candidate_snapshot_id) != bool(self.candidate_snapshot_sha256):
            raise ValueError("candidate snapshot id/hash must occur together")
        if self.candidate_snapshot_sha256 is not None:
            validate_sha256(self.candidate_snapshot_sha256)
            if self.attempt_id is None or not self.attempt_id.strip():
                raise ValueError("snapshot-bound triage receipt requires attempt_id")
        elif self.attempt_id is not None:
            raise ValueError("attempt_id requires a candidate snapshot binding")
        return self


@dataclass(frozen=True, slots=True)
class SemanticTriageImport:
    provider_result: SemanticTriageProviderResult
    decisions: tuple[SemanticTriageDecision, ...]
    receipt: SemanticTriageReceipt


def _parse_deadline(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise TriageIsolationError("market deadline must be an ISO string")
    rendered = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(rendered)
    except ValueError as error:
        raise TriageIsolationError("market deadline is not valid ISO-8601") from error
    return ensure_utc(parsed)


def build_semantic_triage_projection(
    markets: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    created_at: datetime,
    candidate_snapshot_id: str | None = None,
    candidate_snapshot_sha256: str | None = None,
    invalidation_parent_id: str | None = None,
) -> tuple[SemanticTriageProjection, tuple[SemanticTriageBinding, ...]]:
    """Whitelist semantic fields from raw catalog markets and blind identity."""

    created_at = ensure_utc(created_at)
    inputs: list[SemanticTriageInput] = []
    bindings: list[SemanticTriageBinding] = []
    seen_market_ids: set[str] = set()
    for market in markets:
        market_id = _required_text(str(market.get("id", "")))
        if market_id in seen_market_ids:
            raise TriageIsolationError(f"duplicate market_id in triage batch: {market_id}")
        seen_market_ids.add(market_id)
        proposition = _required_text(str(market.get("question", "")))
        rule_text = _required_text(str(market.get("description", "")))
        event_titles = tuple(
            title
            for title in (
                str(event.get("title", "")).strip()
                for event in market.get("events", ())
                if isinstance(event, Mapping)
            )
            if title
        )
        try:
            raw_market_bytes = json.dumps(
                market,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise TriageIsolationError("raw market is not stable JSON data") from error
        source_hash = bytes_sha256(raw_market_bytes)
        item_id = stable_record_id(
            "semantic_item", proposition, rule_text, market.get("endDate"), event_titles
        )
        inputs.append(
            SemanticTriageInput(
                item_id=item_id,
                neutral_proposition=proposition,
                rule_text=rule_text,
                deadline_utc=_parse_deadline(market.get("endDate")),
                event_family_titles=event_titles,
            )
        )
        bindings.append(
            SemanticTriageBinding(
                item_id=item_id,
                market_id=market_id,
                source_market_sha256=source_hash,
            )
        )
    projection_identity: list[Any] = [run_id, inputs]
    if candidate_snapshot_id is not None:
        projection_identity.append({
            "candidate_snapshot_id": candidate_snapshot_id,
            "candidate_snapshot_sha256": candidate_snapshot_sha256,
            "invalidation_parent_id": invalidation_parent_id,
            "projection_policy_version": "v1",
        })
    projection_id = stable_record_id("semantic_projection", *projection_identity)
    projection = SemanticTriageProjection(
        record_id=projection_id,
        projection_id=projection_id,
        run_id=run_id,
        created_at=created_at,
        source="alpha_semantic_projection",
        source_version=GLM_SEMANTIC_TRIAGE_VERSION,
        candidate_snapshot_id=candidate_snapshot_id,
        candidate_snapshot_sha256=candidate_snapshot_sha256,
        invalidation_parent_id=invalidation_parent_id,
        items=tuple(inputs),
    )
    return projection, tuple(bindings)


def semantic_triage_json_schema() -> dict[str, Any]:
    """Return the exact provider payload schema, excluding local envelope fields."""

    schema = SemanticTriageProviderResult.model_json_schema()
    schema["additionalProperties"] = False
    return schema


def import_semantic_triage_result(
    *,
    projection: SemanticTriageProjection,
    bindings: Sequence[SemanticTriageBinding],
    provider_payload: Mapping[str, Any],
    prompt_bytes: bytes,
    provider_wrapper_bytes: bytes,
    provider: ProviderReturnMetadata,
    artifact_root: Path,
    imported_at: datetime,
    repository: AlphaRepository | None = None,
    attempt_id: str | None = None,
) -> SemanticTriageImport:
    """Validate, bind and append-only seal one provider return."""

    imported_at = ensure_utc(imported_at)
    if projection.candidate_snapshot_id is not None and (
        attempt_id is None or not attempt_id.strip()
    ):
        raise TriageResultError("snapshot-bound provider return requires attempt_id")
    if projection.candidate_snapshot_id is None and attempt_id is not None:
        raise TriageResultError("attempt_id requires a candidate snapshot binding")
    try:
        result = SemanticTriageProviderResult.model_validate(provider_payload)
    except Exception as error:
        raise TriageResultError("provider payload does not match semantic triage schema") from error
    if result.projection_id != projection.projection_id:
        raise TriageResultError("provider result targets a different projection")
    expected_ids = tuple(item.item_id for item in projection.items)
    actual_ids = tuple(item.item_id for item in result.items)
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected_ids):
        raise TriageResultError("provider result must return every projected item exactly once")
    binding_map = {item.item_id: item for item in bindings}
    if set(binding_map) != set(expected_ids) or len(binding_map) != len(tuple(bindings)):
        raise TriageResultError("internal market bindings do not match the projection")

    ordered_results = {item.item_id: item for item in result.items}
    projected_items = {item.item_id: item for item in projection.items}
    provider_wrapper_sha256 = bytes_sha256(provider_wrapper_bytes)
    decisions: list[SemanticTriageDecision] = []
    for item_id in expected_ids:
        item = ordered_results[item_id]
        projected = projected_items[item_id]
        binding = binding_map[item_id]
        eligibility = (
            SemanticTriageEligibility.DEADLINE_ELAPSED
            if projected.deadline_utc is not None and projected.deadline_utc <= imported_at
            else SemanticTriageEligibility.ELIGIBLE
        )
        effective_disposition = (
            SemanticTriageDisposition.DEFER
            if eligibility == SemanticTriageEligibility.DEADLINE_ELAPSED
            else item.disposition
        )
        decision_identity: list[Any] = [
            projection.projection_id,
            item_id,
            item,
            provider_wrapper_sha256,
            eligibility,
            effective_disposition,
        ]
        if attempt_id is not None:
            decision_identity.append({
                "attempt_id": attempt_id,
                "candidate_snapshot_id": projection.candidate_snapshot_id,
                "candidate_snapshot_sha256": projection.candidate_snapshot_sha256,
            })
        decision_id = stable_record_id("semantic_decision", *decision_identity)
        decisions.append(
            SemanticTriageDecision(
                record_id=decision_id,
                decision_id=decision_id,
                run_id=projection.run_id,
                created_at=imported_at,
                source="external_glm_semantic_triage",
                source_version=GLM_SEMANTIC_TRIAGE_VERSION,
                provenance=(
                    ProvenanceRef(
                        source_artifact_id=projection.projection_id,
                        relation="semantic_projection",
                        content_sha256=projection.canonical_sha256,
                        source_observed_at=projection.created_at,
                    ),
                ),
                projection_id=projection.projection_id,
                item_id=item_id,
                market_id=binding.market_id,
                result=item,
                eligibility=eligibility,
                effective_disposition=effective_disposition,
                candidate_snapshot_id=projection.candidate_snapshot_id,
                candidate_snapshot_sha256=projection.candidate_snapshot_sha256,
                attempt_id=attempt_id,
            )
        )

    provider_dispositions = {
        status.value: sum(item.disposition == status for item in result.items)
        for status in SemanticTriageDisposition
    }
    dispositions = {
        status.value: sum(item.effective_disposition == status for item in decisions)
        for status in SemanticTriageDisposition
    }
    provider_result_bytes = canonical_json(result).encode("utf-8")
    receipt_identity: list[Any] = [
        projection.projection_id,
        provider_wrapper_sha256,
        bytes_sha256(provider_result_bytes),
    ]
    if attempt_id is not None:
        receipt_identity.append({
            "attempt_id": attempt_id,
            "candidate_snapshot_id": projection.candidate_snapshot_id,
            "candidate_snapshot_sha256": projection.candidate_snapshot_sha256,
        })
    receipt_id = stable_record_id("semantic_receipt", *receipt_identity)
    receipt = SemanticTriageReceipt(
        record_id=receipt_id,
        receipt_id=receipt_id,
        run_id=projection.run_id,
        created_at=imported_at,
        source="alpha_semantic_triage_importer",
        source_version=GLM_SEMANTIC_TRIAGE_VERSION,
        projection_id=projection.projection_id,
        projection_sha256=projection.canonical_sha256,
        prompt_sha256=bytes_sha256(prompt_bytes),
        provider_wrapper_sha256=provider_wrapper_sha256,
        provider_result_sha256=bytes_sha256(provider_result_bytes),
        imported_at=imported_at,
        provider=provider,
        item_count=len(result.items),
        provider_dispositions=provider_dispositions,
        dispositions=dispositions,
        candidate_snapshot_id=projection.candidate_snapshot_id,
        candidate_snapshot_sha256=projection.candidate_snapshot_sha256,
        attempt_id=attempt_id,
    )

    store = ArtifactStore(Path(artifact_root))
    prefix = f"semantic_triage/{receipt.receipt_id}"
    store.write_immutable(f"{prefix}/projection.json", projection.canonical_bytes())
    store.write_immutable(
        f"{prefix}/bindings.json",
        canonical_json({"bindings": tuple(bindings)}).encode("utf-8"),
    )
    store.write_immutable(f"{prefix}/prompt.txt", prompt_bytes)
    store.write_immutable(f"{prefix}/provider-wrapper.json", provider_wrapper_bytes)
    store.write_immutable(f"{prefix}/provider-result.json", provider_result_bytes)
    store.write_immutable(
        f"{prefix}/decisions.json",
        canonical_json({"decisions": decisions}).encode("utf-8"),
    )
    store.write_immutable(
        f"{prefix}/receipt.json", canonical_json(receipt).encode("utf-8")
    )
    if repository is not None:
        repository.save_contracts_atomic((*decisions, receipt))
    return SemanticTriageImport(result, tuple(decisions), receipt)
