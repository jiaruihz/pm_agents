"""Deterministic Gate R WP1 eligibility, dry-run routing and comparison.

This module intentionally has no transport dependencies.  It accepts already
validated contracts and returns append-only, content-addressed records only.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
from typing import Any, Mapping

from ..contracts import (
    CandidateCard, CandidateEligibility, CandidateSnapshotSeal, CandidateState,
    DisagreementReceipt, RiskTier, RuleDryRunReceipt, TriageRoutingAction,
    TriageRoutingDecision, canonical_json, content_sha256, stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256
from ..protocol import CandidateLifecycleProjection
from .semantic import SemanticTriageDecision, SemanticTriageDisposition, SemanticTriageReceipt


ROUTING_POLICY_ID = "deterministic_triage_routing_v1"
ROUTING_POLICY_VERSION = "v1"
PILOT_SAMPLE_POLICY_ID = "gate_r_8_case_100pct_glm53_v1"
FUTURE_SAMPLE_POLICY_ID = "future_triage_sampling_v1"
_CRITICAL_DRY_RUN_CODES = frozenset({
    "SOURCE_IDENTITY_INCOMPLETE",
    "SOURCE_PRECEDENCE_INCOMPLETE",
    "SUBJECTIVE_ADJUDICATION",
    "CORRECTION_SEMANTICS",
    "SUPERSESSION_SEMANTICS",
    "DISPUTE_SEMANTICS",
    "MULTIPLE_TIMEZONE_CONVENTIONS",
    "DEADLINE_CONFLICT",
    "DETERMINISTIC_FACT_CONFLICT",
})


def deterministic_eligibility(
    projection: CandidateLifecycleProjection,
    *, deadline_utc: datetime | None, as_of_utc: datetime, duplicate: bool = False,
) -> CandidateEligibility:
    """Resolve hard lifecycle facts before a model can be called."""
    as_of_utc = ensure_utc(as_of_utc)
    if duplicate:
        return CandidateEligibility.DUPLICATE
    if projection.archived:
        return CandidateEligibility.ARCHIVED
    if projection.terminal_event is not None:
        mapping = {
            "MARKET_CLOSED": CandidateEligibility.MARKET_CLOSED,
            "RESOLVED": CandidateEligibility.RESOLVED,
            "SUPERSEDED": CandidateEligibility.SUPERSEDED,
        }
        return mapping.get(projection.terminal_event.value, CandidateEligibility.SUPERSEDED)
    if projection.refresh_required:
        return CandidateEligibility.REFRESH_REQUIRED
    if projection.invalidated or not projection.active:
        return CandidateEligibility.INVALIDATED
    if deadline_utc is not None and ensure_utc(deadline_utc) <= as_of_utc:
        return CandidateEligibility.DEADLINE_ELAPSED
    return CandidateEligibility.ELIGIBLE


def build_candidate_snapshot_seal(
    *, candidate: CandidateCard, candidate_revision_id: str, market_revision_id: str,
    rule_source_artifact_id: str, rule_source_bytes: bytes,
    lifecycle: CandidateLifecycleProjection, deadline_utc: datetime | None,
    as_of_utc: datetime, allowed_projection_input_ids: tuple[str, ...], run_id: str,
    policy_id: str = "candidate_eligibility_v1", policy_version: str = "v1",
    duplicate: bool = False,
) -> CandidateSnapshotSeal:
    """Freeze every identity-bearing input that can authorize downstream work."""
    as_of_utc = ensure_utc(as_of_utc)
    if candidate_revision_id != candidate.record_id:
        raise ValueError("candidate revision must be the canonical CandidateCard record")
    if lifecycle.candidate_id != candidate.candidate_id:
        raise ValueError("lifecycle candidate does not match snapshot candidate")
    if as_of_utc < candidate.selected_at:
        raise ValueError("snapshot cannot precede Candidate selection")
    if lifecycle.last_at is not None and lifecycle.last_at > as_of_utc:
        raise ValueError("snapshot cannot precede the latest lifecycle event")
    inputs = tuple(sorted(set(allowed_projection_input_ids)))
    if inputs != allowed_projection_input_ids:
        raise ValueError("allowed projection input ids must already be unique and sorted")
    eligibility = deterministic_eligibility(lifecycle, deadline_utc=deadline_utc, as_of_utc=as_of_utc, duplicate=duplicate)
    rule_hash = hashlib.sha256(rule_source_bytes).hexdigest()
    payload: dict[str, Any] = {
        "candidate_id": candidate.candidate_id, "candidate_revision_id": candidate_revision_id,
        "canonical_market_revision_id": market_revision_id,
        "canonical_rule_source_artifact_id": rule_source_artifact_id,
        "rule_source_sha256": rule_hash, "rule_source_byte_length": len(rule_source_bytes),
        "lifecycle_state": lifecycle.current_state, "eligibility": eligibility,
        "eligibility_as_of_utc": as_of_utc, "eligibility_policy_id": policy_id,
        "eligibility_policy_version": policy_version, "allowed_projection_input_ids": inputs,
    }
    seal_sha = content_sha256(payload)
    seal_id = stable_record_id("candidate_snapshot", payload)
    return CandidateSnapshotSeal(record_id=seal_id, seal_id=seal_id, run_id=run_id,
        created_at=as_of_utc, source="alpha_candidate_snapshot", source_version="gate_r_wp1",
        **payload, seal_sha256=seal_sha)


def build_rule_dry_run_receipt(*, snapshot: CandidateSnapshotSeal, complete: bool,
    direct_compile_possible: bool, authoritative_source_count: int, complexity_score: int,
    ambiguity_codes: tuple[str, ...], reason_codes: tuple[str, ...], compiler_version: str,
    run_id: str, created_at: datetime) -> RuleDryRunReceipt:
    created_at = ensure_utc(created_at)
    if created_at < snapshot.created_at:
        raise ValueError("rule dry-run cannot precede the Candidate snapshot")
    key = (snapshot.record_id, snapshot.canonical_sha256, compiler_version, complete,
           direct_compile_possible, authoritative_source_count, complexity_score,
           tuple(sorted(ambiguity_codes)), tuple(sorted(reason_codes)))
    receipt_id = stable_record_id("rule_dry_run", key)
    return RuleDryRunReceipt(record_id=receipt_id, receipt_id=receipt_id, run_id=run_id,
        created_at=created_at, source="alpha_rule_dry_run", source_version=compiler_version,
        candidate_snapshot_id=snapshot.record_id, candidate_snapshot_sha256=snapshot.canonical_sha256,
        compiler_version=compiler_version, complete=complete,
        direct_compile_possible=direct_compile_possible,
        authoritative_source_count=authoritative_source_count, complexity_score=complexity_score,
        ambiguity_codes=tuple(sorted(ambiguity_codes)), reason_codes=tuple(sorted(reason_codes)))


def _stable_sample(snapshot_id: str, seed: str, rate: int, context: str) -> bool:
    if not seed.strip():
        raise ValueError("sample seed must not be blank")
    if not context.strip():
        raise ValueError("sample context must not be blank")
    if rate < 0 or rate > 100:
        raise ValueError("sample rate must be between 0 and 100")
    digest = content_sha256(
        {"snapshot_id": snapshot_id, "seed": seed, "context": context}
    )
    return int(digest[:8], 16) % 100 < rate


def route_triage(*, snapshot: CandidateSnapshotSeal, dry_run: RuleDryRunReceipt,
    triage_decision: SemanticTriageDecision | None, triage_receipt: SemanticTriageReceipt | None,
    sample_seed: str, run_id: str, created_at: datetime, pilot_mode: bool = True,
    family_cohort_index: int | None = None,
) -> TriageRoutingDecision:
    """Route strictly from typed inputs; pilot route and future route are both sealed."""
    created_at = ensure_utc(created_at)
    if dry_run.candidate_snapshot_id != snapshot.record_id or dry_run.candidate_snapshot_sha256 != snapshot.canonical_sha256:
        raise ValueError("dry-run is not bound to snapshot")
    if created_at < snapshot.created_at or created_at < dry_run.created_at:
        raise ValueError("routing decision cannot precede sealed inputs")
    ineligible = snapshot.eligibility != CandidateEligibility.ELIGIBLE
    if ineligible:
        tier, reasons, future = RiskTier.D_DETERMINISTIC, (snapshot.eligibility.value,), TriageRoutingAction.DEFER_NONTERMINAL
        sample_context = f"deterministic:{snapshot.eligibility.value}"
    else:
        if triage_decision is None or triage_receipt is None:
            raise ValueError("eligible snapshot requires validated GLM-4.7 decision and receipt")
        expected_snapshot = (snapshot.record_id, snapshot.canonical_sha256)
        if (
            (triage_decision.candidate_snapshot_id, triage_decision.candidate_snapshot_sha256)
            != expected_snapshot
            or (triage_receipt.candidate_snapshot_id, triage_receipt.candidate_snapshot_sha256)
            != expected_snapshot
        ):
            raise ValueError("GLM-4.7 decision and receipt must bind the current snapshot")
        if triage_decision.projection_id != triage_receipt.projection_id:
            raise ValueError("GLM-4.7 decision and receipt target different projections")
        if (
            triage_decision.attempt_id is None
            or triage_decision.attempt_id != triage_receipt.attempt_id
        ):
            raise ValueError("GLM-4.7 decision and receipt must bind one attempt")
        if (
            triage_receipt.imported_at < triage_decision.created_at
            or created_at < triage_receipt.imported_at
        ):
            raise ValueError("GLM-4.7 and routing clocks are not ordered")
        if triage_decision.eligibility.value != "ELIGIBLE":
            raise ValueError("model eligibility cannot override snapshot eligibility")
        if family_cohort_index is None or family_cohort_index < 0:
            raise ValueError("eligible routing requires a nonnegative family_cohort_index")
        family = triage_decision.result.topic_family.strip()
        if not family:
            raise ValueError("eligible routing requires a market family")
        family_cohort = family_cohort_index // 50
        sample_context = (
            f"family={family}|cohort={family_cohort}|index={family_cohort_index}"
        )
        critical_codes = tuple(sorted(
            set(dry_run.reason_codes).intersection(_CRITICAL_DRY_RUN_CODES)
        ))
        if triage_decision.effective_disposition == SemanticTriageDisposition.DEFER:
            future_sampled = _stable_sample(
                snapshot.record_id, sample_seed, 20, sample_context
            )
            tier, reasons = RiskTier.D_MODEL_ONLY, ("MODEL_DEFER",)
            future = (
                TriageRoutingAction.REQUEST_GLM53
                if future_sampled
                else TriageRoutingAction.DEFER_NONTERMINAL
            )
        elif (not dry_run.complete or not dry_run.direct_compile_possible or dry_run.authoritative_source_count != 1
              or dry_run.complexity_score >= 3 or critical_codes):
            reasons = tuple(sorted({"CRITICAL_RULE_DRY_RUN", *critical_codes}))
            tier, future = RiskTier.R3_CRITICAL, TriageRoutingAction.REQUEST_GLM53
        elif (triage_decision.result.disposition == SemanticTriageDisposition.REVIEW
              or triage_decision.result.confidence_milli < 800 or dry_run.ambiguity_codes
              or dry_run.complexity_score >= 2):
            tier, reasons, future = RiskTier.R2_REVIEW, ("REVIEW_REQUIRED",), TriageRoutingAction.REQUEST_GLM53
        else:
            tier, reasons = RiskTier.R1_SIMPLE, ("SIMPLE_DIRECT_COMPILE",)
            random_sample = _stable_sample(
                snapshot.record_id, sample_seed, 10, sample_context
            )
            cohort_minimum = family_cohort_index % 50 == 0
            future = (
                TriageRoutingAction.REQUEST_GLM53
                if random_sample or cohort_minimum
                else TriageRoutingAction.TRY_DIRECT_COMPILE
            )
    action = (TriageRoutingAction.REQUEST_GLM53 if pilot_mode and not ineligible else future)
    sampled = action == TriageRoutingAction.REQUEST_GLM53
    future_sampled = future == TriageRoutingAction.REQUEST_GLM53
    refresh = created_at + timedelta(days=1) if action == TriageRoutingAction.DEFER_NONTERMINAL else None
    key = (snapshot.record_id, snapshot.canonical_sha256, getattr(triage_receipt, "record_id", None),
           dry_run.record_id, tier, sample_seed, sample_context, pilot_mode, action, future)
    record_id = stable_record_id("triage_routing", key)
    return TriageRoutingDecision(record_id=record_id, routing_decision_id=record_id, run_id=run_id,
        created_at=created_at, source="alpha_deterministic_triage_routing", source_version=ROUTING_POLICY_VERSION,
        candidate_snapshot_id=snapshot.record_id, candidate_snapshot_sha256=snapshot.canonical_sha256,
        triage_receipt_id=None if triage_receipt is None else triage_receipt.record_id,
        triage_receipt_sha256=None if triage_receipt is None else triage_receipt.canonical_sha256,
        rule_dry_run_receipt_id=dry_run.record_id, rule_dry_run_receipt_sha256=dry_run.canonical_sha256,
        risk_tier=tier, risk_reason_codes=reasons,
        sample_policy_id=PILOT_SAMPLE_POLICY_ID if pilot_mode else FUTURE_SAMPLE_POLICY_ID,
        sample_seed=sample_seed, sampled=sampled,
        future_policy_sampled=future_sampled,
        future_sample_policy_id=FUTURE_SAMPLE_POLICY_ID,
        sample_context_id=sample_context,
        triage_attempt_id=None if triage_receipt is None else triage_receipt.attempt_id,
        action=action, future_policy_action=future,
        refresh_after_utc=refresh)


def compare_typed_reviews(*, snapshot: CandidateSnapshotSeal, first_attempt_id: str,
    first_attempt_sha256: str, first: Mapping[str, Any], second_attempt_id: str,
    second_attempt_sha256: str, second: Mapping[str, Any], route: TriageRoutingDecision,
    run_id: str, created_at: datetime) -> DisagreementReceipt:
    """Compare only agreed frozen scalar/list fields; it never adjudicates truth."""
    created_at = ensure_utc(created_at)
    validate_sha256(first_attempt_sha256); validate_sha256(second_attempt_sha256)
    allowed = ("topic_family", "subject_entities", "deadline_interpretation", "resolution_source_type", "researchability", "ambiguity_codes", "disposition")
    if set(first) != set(allowed) or set(second) != set(allowed):
        raise ValueError("review comparator accepts only frozen typed fields")
    if (
        route.candidate_snapshot_id != snapshot.record_id
        or route.candidate_snapshot_sha256 != snapshot.canonical_sha256
    ):
        raise ValueError("route does not bind the comparison snapshot")
    if route.action != TriageRoutingAction.REQUEST_GLM53:
        raise ValueError("comparison requires an authorized GLM-5.3 route")
    if (
        not first_attempt_id.strip()
        or not second_attempt_id.strip()
        or first_attempt_id == second_attempt_id
    ):
        raise ValueError("comparison requires two distinct nonblank attempts")
    if route.triage_attempt_id != first_attempt_id:
        raise ValueError("first comparison attempt must match the GLM-4.7 route")
    codes = tuple(f"DIFF_{field.upper()}" for field in allowed if first.get(field) != second.get(field))
    receipt_id = stable_record_id("triage_disagreement", snapshot.record_id, first_attempt_id,
        first_attempt_sha256, second_attempt_id, second_attempt_sha256, codes, route.record_id)
    return DisagreementReceipt(record_id=receipt_id, receipt_id=receipt_id, run_id=run_id,
        created_at=created_at, source="alpha_typed_disagreement_comparator", source_version=ROUTING_POLICY_VERSION,
        candidate_snapshot_id=snapshot.record_id, candidate_snapshot_sha256=snapshot.canonical_sha256,
        first_attempt_id=first_attempt_id, first_attempt_sha256=first_attempt_sha256,
        second_attempt_id=second_attempt_id, second_attempt_sha256=second_attempt_sha256,
        difference_codes=codes, route_decision_id=route.record_id,
        route_decision_sha256=route.canonical_sha256,
        routing_policy_version=ROUTING_POLICY_VERSION, sample_seed=route.sample_seed)
