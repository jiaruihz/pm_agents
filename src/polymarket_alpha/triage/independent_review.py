"""Offline-safe GLM-5.3 work-order boundary for Gate R WP2.

This module never invokes a provider.  It creates an allowlist-only prompt and
imports caller-supplied bytes as untrusted proposals.  The separate binder is
the only component that can turn a quoted rule proposal into compiler input.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Mapping, Sequence

from ..contracts import (
    CandidateSnapshotSeal, IndependentReviewAttemptReceipt,
    IndependentReviewAttemptStatus, IndependentReviewBudget,
    IndependentReviewDisposition, IndependentSemanticReview, RuleParseFieldProposal,
    StructuredRuleParseProposal, TriageRoutingAction, TriageRoutingDecision,
    bytes_sha256, canonical_json, content_sha256, stable_record_id,
)
from ..contracts.base import ensure_utc
from .semantic import SemanticTriageProjection


GLM53_REVIEW_VERSION = "alpha_glm53_independent_review_v1"


class IndependentReviewError(ValueError):
    """The work order or provider proposal is unsafe, stale, or malformed."""


class IndependentReviewQuarantine(IndependentReviewError):
    """An attempt must remain recorded but cannot advance."""

    def __init__(self, message: str, *, receipt: IndependentReviewAttemptReceipt | None = None):
        super().__init__(message)
        self.receipt = receipt


_FORBIDDEN_KEY = re.compile(
    r"(?:glm.?4|semantic_triage|triage_receipt|rationale|price|probab|"
    r"odds|bid|ask|book|wallet|recallhit|recall_reason|market_id|slug|url|"
    r"candidate_direction|order|gpt|private|rulecontract|rule_a)", re.I
)
_FORBIDDEN_TEXT = re.compile(
    r"(?:https?://|glm.?4|fair[- ]?value|mispric|\bprice\b|\b(?:buy|sell)\b|"
    r"\b(?:yes|no)\s+(?:side|position|trade)|\b\d+(?:\.\d+)?%\b)", re.I
)
_ALLOWED_REVIEW_KEYS = frozenset({
    "topic", "entities", "relevant_clocks", "source_type_hints", "researchability",
    "ambiguities", "independent_disposition_proposal",
})
_ALLOWED_PROPOSAL_KEYS = frozenset({"field_name", "proposed_value", "source_segment_id", "exact_quote", "proposal_confidence_milli", "unresolved"})


def _scan(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _FORBIDDEN_KEY.search(str(key)):
                raise IndependentReviewError(f"forbidden origin or market semantic at {path}.{key}")
            _scan(nested, path=f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _scan(nested, path=f"{path}[{index}]")
    elif isinstance(value, str) and _FORBIDDEN_TEXT.search(value):
        raise IndependentReviewError(f"forbidden text origin or market semantic at {path}")


@dataclass(frozen=True)
class Glm53WorkOrder:
    work_order_id: str
    work_order_sha256: str
    attempt_id: str
    snapshot_id: str
    snapshot_sha256: str
    projection_id: str
    projection_sha256: str
    route_id: str
    route_sha256: str
    rule_source_artifact_id: str
    rule_source_sha256: str
    safe_rule_segments_sha256: str
    prompt_version: str
    schema_version: str
    prompt_sha256: str
    budget: IndependentReviewBudget
    prompt_bytes: bytes


@dataclass(frozen=True)
class ImportedIndependentReview:
    review: IndependentSemanticReview
    proposal: StructuredRuleParseProposal
    attempt_receipt: IndependentReviewAttemptReceipt
    provider_wrapper_sha256: str
    provider_return_sha256: str


def build_glm53_work_order(*, snapshot: CandidateSnapshotSeal,
    projection: SemanticTriageProjection, route: TriageRoutingDecision,
    attempt_id: str, safe_rule_segments: Mapping[str, str],
    canonical_rule_source_bytes: bytes,
    missing_field_codes: Sequence[str] = (), prompt_version: str = "v1",
    schema_version: str = "v1",
    budget: IndependentReviewBudget | None = None) -> Glm53WorkOrder:
    """Build a deterministic provider-neutral work order from safe inputs only."""
    if not attempt_id.strip():
        raise IndependentReviewError("attempt id is required")
    if route.action != TriageRoutingAction.REQUEST_GLM53:
        raise IndependentReviewError("GLM-5.3 requires an authorized routing decision")
    if route.candidate_snapshot_id != snapshot.record_id or route.candidate_snapshot_sha256 != snapshot.canonical_sha256:
        raise IndependentReviewError("route does not bind snapshot")
    if projection.candidate_snapshot_id != snapshot.record_id or projection.candidate_snapshot_sha256 != snapshot.canonical_sha256:
        raise IndependentReviewError("projection does not bind snapshot")
    if len(projection.items) != 1:
        raise IndependentReviewError("GLM-5.3 work order requires one frozen candidate projection")
    if bytes_sha256(canonical_rule_source_bytes) != snapshot.rule_source_sha256:
        raise IndependentReviewError("canonical rule source bytes do not match snapshot")
    segments = tuple({"source_segment_id": key, "text": value} for key, value in sorted(safe_rule_segments.items()))
    if not segments or any(not item["source_segment_id"].strip() or not item["text"].strip() for item in segments):
        raise IndependentReviewError("safe rule segments must be nonblank")
    source_text = canonical_rule_source_bytes.decode("utf-8")
    for segment in segments:
        if source_text.count(segment["text"]) != 1:
            raise IndependentReviewError("each safe rule segment must uniquely match the sealed canonical source")
    safe = {
        "projection": projection.model_dump(mode="json", exclude={"candidate_snapshot_id", "candidate_snapshot_sha256", "invalidation_parent_id"}),
        "rule_segments": segments,
        "missing_field_codes": tuple(sorted(set(missing_field_codes))),
        "output": {
            "independent_semantic_review": sorted(_ALLOWED_REVIEW_KEYS),
            "structured_rule_parse_proposal": sorted(_ALLOWED_PROPOSAL_KEYS),
        },
    }
    _scan(safe)
    prompt_bytes = canonical_json(safe).encode("utf-8")
    resolved_budget = budget or IndependentReviewBudget(
        max_total_tokens=8_000, max_cost_usd_micros=2_000_000, max_duration_ms=120_000
    )
    identity = {
        "attempt_id": attempt_id.strip(), "snapshot_id": snapshot.record_id,
        "snapshot_sha256": snapshot.canonical_sha256,
        "projection_id": projection.projection_id,
        "projection_sha256": projection.canonical_sha256,
        "route_id": route.record_id, "route_sha256": route.canonical_sha256,
        "rule_source_artifact_id": snapshot.canonical_rule_source_artifact_id,
        "rule_source_sha256": snapshot.rule_source_sha256,
        "safe_rule_segments_sha256": content_sha256(segments),
        "prompt_version": prompt_version, "schema_version": schema_version,
        "prompt_sha256": bytes_sha256(prompt_bytes), "budget": resolved_budget,
    }
    work_order_id = stable_record_id("glm53_work_order", identity)
    work_order_sha256 = content_sha256({"work_order_id": work_order_id, **identity})
    return Glm53WorkOrder(work_order_id=work_order_id, work_order_sha256=work_order_sha256,
        attempt_id=attempt_id.strip(), snapshot_id=snapshot.record_id,
        snapshot_sha256=snapshot.canonical_sha256, projection_id=projection.projection_id,
        projection_sha256=projection.canonical_sha256, route_id=route.record_id,
        route_sha256=route.canonical_sha256,
        rule_source_artifact_id=snapshot.canonical_rule_source_artifact_id,
        rule_source_sha256=snapshot.rule_source_sha256,
        safe_rule_segments_sha256=content_sha256(segments), prompt_version=prompt_version,
        schema_version=schema_version, prompt_sha256=bytes_sha256(prompt_bytes),
        budget=resolved_budget, prompt_bytes=prompt_bytes)


def _attempt_receipt(*, work_order: Glm53WorkOrder, provider: str,
    requested_model: str, status: IndependentReviewAttemptStatus,
    reason_codes: Sequence[str], provider_wrapper_sha256: str | None,
    provider_return_sha256: str | None, usage: Mapping[str, int] | None,
    cost_usd_micros: int | None, duration_ms: int | None, run_id: str,
    imported_at: datetime) -> IndependentReviewAttemptReceipt:
    receipt_id = stable_record_id(
        "independent_review_attempt", work_order.attempt_id, work_order.snapshot_id,
        work_order.snapshot_sha256, work_order.work_order_id, work_order.work_order_sha256,
        provider, requested_model,
    )
    return IndependentReviewAttemptReceipt(
        record_id=receipt_id, receipt_id=receipt_id, run_id=run_id,
        created_at=imported_at, source="external_glm53_attempt",
        source_version=GLM53_REVIEW_VERSION, attempt_id=work_order.attempt_id,
        candidate_snapshot_id=work_order.snapshot_id,
        candidate_snapshot_sha256=work_order.snapshot_sha256,
        work_order_id=work_order.work_order_id,
        work_order_sha256=work_order.work_order_sha256,
        prompt_sha256=work_order.prompt_sha256,
        provider_wrapper_sha256=provider_wrapper_sha256,
        provider_return_sha256=provider_return_sha256, provider=provider,
        requested_model=requested_model, status=status,
        reason_codes=tuple(reason_codes), usage=dict(usage or {}),
        cost_usd_micros=cost_usd_micros, duration_ms=duration_ms,
    )


def import_independent_review(*, work_order: Glm53WorkOrder, provider_payload: Mapping[str, Any],
    provider_wrapper_bytes: bytes, provider: str, requested_model: str,
    reported_model: str | None, usage: Mapping[str, int] | None, cost_usd_micros: int | None,
    duration_ms: int | None, run_id: str, imported_at: datetime) -> ImportedIndependentReview:
    """Strictly validate an external return.  It cannot see or override GLM-4.7."""
    imported_at = ensure_utc(imported_at)
    if not provider.strip() or not requested_model.strip():
        raise IndependentReviewError("provider and requested model are required")
    if not provider_wrapper_bytes:
        raise IndependentReviewError("provider wrapper bytes are required")
    wrapper_hash = bytes_sha256(provider_wrapper_bytes)
    return_hash = bytes_sha256(canonical_json(provider_payload).encode("utf-8"))
    usage_values = dict(usage or {})

    def quarantine(message: str, reason: str,
        status: IndependentReviewAttemptStatus = IndependentReviewAttemptStatus.QUARANTINED,
    ) -> IndependentReviewQuarantine:
        return IndependentReviewQuarantine(message, receipt=_attempt_receipt(
            work_order=work_order, provider=provider, requested_model=requested_model,
            status=status, reason_codes=(reason,), provider_wrapper_sha256=wrapper_hash,
            provider_return_sha256=return_hash, usage=usage_values,
            cost_usd_micros=cost_usd_micros, duration_ms=duration_ms,
            run_id=run_id, imported_at=imported_at,
        ))

    if any(count < 0 or not key.strip() for key, count in usage_values.items()):
        raise quarantine("provider usage metadata is invalid", "INVALID_USAGE")
    total_tokens = usage_values.get("total_tokens")
    if total_tokens is None:
        total_tokens = sum(
            count for key, count in usage_values.items()
            if key.endswith("_tokens") and key != "total_tokens"
        )
    if (
        total_tokens > work_order.budget.max_total_tokens
        or cost_usd_micros is None or cost_usd_micros > work_order.budget.max_cost_usd_micros
        or duration_ms is None or duration_ms > work_order.budget.max_duration_ms
    ):
        raise quarantine("provider attempt exceeded or omitted a sealed budget metric",
            "BUDGET_EXCEEDED", IndependentReviewAttemptStatus.BUDGET_EXCEEDED)
    try:
        _scan(provider_payload)
    except IndependentReviewError as error:
        raise quarantine(str(error), "SEMANTIC_LEAK") from error
    if set(provider_payload) != {"independent_semantic_review", "structured_rule_parse_proposal"}:
        raise quarantine("provider return must contain exactly two namespaces", "SCHEMA_MISMATCH")
    review_data = provider_payload["independent_semantic_review"]
    proposal_data = provider_payload["structured_rule_parse_proposal"]
    if not isinstance(review_data, Mapping) or set(review_data) != _ALLOWED_REVIEW_KEYS:
        raise quarantine("independent review schema mismatch", "SCHEMA_MISMATCH")
    if not isinstance(proposal_data, Mapping) or set(proposal_data) != {"proposals"}:
        raise quarantine("rule proposal envelope mismatch", "SCHEMA_MISMATCH")
    proposals_raw = proposal_data["proposals"]
    if not isinstance(proposals_raw, list) or not proposals_raw:
        raise quarantine("rule proposal coverage is absent", "PROPOSAL_ABSENT")
    if any(not isinstance(item, Mapping) or set(item) != _ALLOWED_PROPOSAL_KEYS for item in proposals_raw):
        raise quarantine("rule proposal item schema mismatch", "SCHEMA_MISMATCH")
    try:
        parsed_proposals = tuple(RuleParseFieldProposal.model_validate(item) for item in proposals_raw)
    except Exception as error:
        raise quarantine("rule proposal validation failed", "PROPOSAL_INVALID") from error
    names = tuple(item.field_name for item in parsed_proposals)
    if len(names) != len(set(names)):
        raise quarantine("one-to-one field proposal coverage violated", "DUPLICATE_FIELD")
    logical_identity = (
        work_order.attempt_id, work_order.snapshot_id, work_order.snapshot_sha256,
        work_order.projection_id, work_order.projection_sha256, work_order.route_id,
        work_order.route_sha256, work_order.work_order_id, work_order.work_order_sha256,
        work_order.prompt_sha256, provider, requested_model,
    )
    review_id = stable_record_id("independent_semantic_review", *logical_identity)
    try:
        review = IndependentSemanticReview(record_id=review_id, review_id=review_id, run_id=run_id,
            created_at=imported_at, source="external_glm53_independent_review", source_version=GLM53_REVIEW_VERSION,
            attempt_id=work_order.attempt_id, candidate_snapshot_id=work_order.snapshot_id,
            candidate_snapshot_sha256=work_order.snapshot_sha256, projection_id=work_order.projection_id,
            projection_sha256=work_order.projection_sha256, routing_decision_id=work_order.route_id,
            routing_decision_sha256=work_order.route_sha256,
            work_order_id=work_order.work_order_id, work_order_sha256=work_order.work_order_sha256,
            rule_source_artifact_id=work_order.rule_source_artifact_id,
            rule_source_sha256=work_order.rule_source_sha256,
            provider=provider, requested_model=requested_model, reported_model=reported_model,
            prompt_version=work_order.prompt_version, schema_version_provider=work_order.schema_version,
            prompt_sha256=work_order.prompt_sha256, provider_wrapper_sha256=wrapper_hash,
            provider_return_sha256=return_hash,
            usage=dict(usage or {}), cost_usd_micros=cost_usd_micros, duration_ms=duration_ms,
            **dict(review_data))
        proposal_id = stable_record_id("structured_rule_parse_proposal", *logical_identity)
        proposal = StructuredRuleParseProposal(record_id=proposal_id, proposal_id=proposal_id, run_id=run_id,
            created_at=imported_at, source="external_glm53_rule_parse", source_version=GLM53_REVIEW_VERSION,
            attempt_id=work_order.attempt_id, candidate_snapshot_id=work_order.snapshot_id,
            candidate_snapshot_sha256=work_order.snapshot_sha256, projection_id=work_order.projection_id,
            projection_sha256=work_order.projection_sha256, routing_decision_id=work_order.route_id,
            routing_decision_sha256=work_order.route_sha256,
            work_order_id=work_order.work_order_id, work_order_sha256=work_order.work_order_sha256,
            rule_source_artifact_id=work_order.rule_source_artifact_id,
            rule_source_sha256=work_order.rule_source_sha256,
            provider=provider, requested_model=requested_model, reported_model=reported_model,
            prompt_version=work_order.prompt_version,
            schema_version_provider=work_order.schema_version,
            prompt_sha256=work_order.prompt_sha256, provider_wrapper_sha256=wrapper_hash,
            provider_return_sha256=return_hash, usage=usage_values,
            cost_usd_micros=cost_usd_micros, duration_ms=duration_ms,
            proposals=parsed_proposals)
    except Exception as error:
        raise quarantine("provider return failed typed validation", "TYPED_VALIDATION_FAILED") from error
    attempt_receipt = _attempt_receipt(work_order=work_order, provider=provider,
        requested_model=requested_model, status=IndependentReviewAttemptStatus.ACCEPTED,
        reason_codes=("VALIDATED",), provider_wrapper_sha256=wrapper_hash,
        provider_return_sha256=return_hash, usage=usage_values,
        cost_usd_micros=cost_usd_micros, duration_ms=duration_ms,
        run_id=run_id, imported_at=imported_at)
    return ImportedIndependentReview(review=review, proposal=proposal,
        attempt_receipt=attempt_receipt, provider_wrapper_sha256=wrapper_hash,
        provider_return_sha256=return_hash)
