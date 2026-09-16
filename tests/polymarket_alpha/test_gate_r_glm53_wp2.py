"""Focused offline safety checks for Gate R WP2."""

from datetime import datetime, timezone
import hashlib

import pytest

from pydantic import ValidationError

from src.polymarket_alpha.contracts import (
    IndependentReviewAttemptStatus, RuleInterpretationPatch, RuleReviewAction,
    RuleReviewApproval, content_sha256, stable_record_id,
)
from src.polymarket_alpha.rules import (
    CanonicalRuleSegment, RuleBindingError, apply_rule_review,
    bind_rule_parse_proposal,
)
from src.polymarket_alpha.triage import (
    IndependentReviewError, IndependentReviewQuarantine, build_glm53_work_order,
    build_semantic_triage_projection, import_independent_review,
)
from src.polymarket_alpha.storage import AlphaRepository, ContractConflictError
from tests.polymarket_alpha.test_gate_r_routing_wp1 import NOW, _dry, _route, _snapshot, _triage


def _order():
    snapshot = _snapshot()
    _, triage_receipt = _triage(snapshot)
    route = _route(snapshot, _dry(snapshot), triage=_triage(snapshot))
    projection, _ = build_semantic_triage_projection([{
        "id": "internal-market", "question": "Will City have rain?",
        "description": "Resolves based on official city weather bulletin.",
        "endDate": "2027-01-01T00:00:00Z",
    }], run_id="wp2", created_at=NOW, candidate_snapshot_id=snapshot.record_id,
        candidate_snapshot_sha256=snapshot.canonical_sha256)
    return snapshot, route, projection, build_glm53_work_order(snapshot=snapshot, projection=projection,
        route=route, attempt_id="attempt:glm53", safe_rule_segments={"segment:one": "official rules"},
        canonical_rule_source_bytes=b"official rules",
        missing_field_codes=("SOURCE_PRECEDENCE_INCOMPLETE",))


def _payload():
    return {"independent_semantic_review": {
        "topic": "weather", "entities": ["City"], "relevant_clocks": ["deadline"],
        "source_type_hints": ["OFFICIAL"], "researchability": "HIGH", "ambiguities": [],
        "independent_disposition_proposal": "REVIEW",
    }, "structured_rule_parse_proposal": {"proposals": [{
        "field_name": "yes_trigger", "proposed_value": "official rules",
        "source_segment_id": "segment:one", "exact_quote": "official rules",
        "proposal_confidence_milli": 700, "unresolved": False,
    }]}}


def test_glm53_prompt_excludes_glm47_and_market_origins_recursively():
    _, _, _, order = _order()
    text = order.prompt_bytes.decode()
    assert "glm-4.7" not in text.lower()
    assert "internal-market" not in text
    with pytest.raises(IndependentReviewError):
        build_glm53_work_order(snapshot=_order()[0], projection=_order()[2], route=_order()[1],
            attempt_id="x", safe_rule_segments={"segment:one": "safe"},
            canonical_rule_source_bytes=b"official rules",
            missing_field_codes=("SAFE", "price=0.9"))


@pytest.mark.parametrize("mutate", ["glm47", "duplicate", "schema"])
def test_provider_result_quarantines_leaks_and_schema_conflicts(mutate):
    _, _, _, order = _order()
    payload = _payload()
    if mutate == "glm47":
        payload["independent_semantic_review"]["topic"] = "GLM-4.7 says advance"
    elif mutate == "duplicate":
        payload["structured_rule_parse_proposal"]["proposals"].append(dict(payload["structured_rule_parse_proposal"]["proposals"][0]))
    else:
        payload["independent_semantic_review"]["price"] = "bad"
    with pytest.raises((IndependentReviewError, IndependentReviewQuarantine)):
        import_independent_review(work_order=order, provider_payload=payload, provider_wrapper_bytes=b"receipt",
            provider="fake", requested_model="glm-5.3", reported_model="glm-5.3", usage={}, cost_usd_micros=0,
            duration_ms=1, run_id="wp2", imported_at=NOW)


def test_exact_quote_binder_rejects_fabricated_duplicate_and_partial_coverage():
    snapshot, _, _, order = _order()
    imported = import_independent_review(work_order=order, provider_payload=_payload(), provider_wrapper_bytes=b"receipt",
        provider="fake", requested_model="glm-5.3", reported_model="glm-5.3", usage={}, cost_usd_micros=0,
        duration_ms=1, run_id="wp2", imported_at=NOW)
    segment = CanonicalRuleSegment(source_segment_id="segment:one",
        candidate_snapshot_id=snapshot.record_id,
        candidate_snapshot_sha256=snapshot.canonical_sha256,
        source_artifact_id=snapshot.canonical_rule_source_artifact_id,
        source_artifact_sha256=snapshot.rule_source_sha256,
        source_bytes=b"official rules", text="official rules")
    receipt = bind_rule_parse_proposal(proposal=imported.proposal, segments={segment.source_segment_id: segment}, required_fields=("yes_trigger",))
    assert receipt.bound[0].exact_quote == "official rules"
    with pytest.raises(RuleBindingError, match="partial"):
        bind_rule_parse_proposal(proposal=imported.proposal, segments={segment.source_segment_id: segment}, required_fields=("yes_trigger", "deadline"))


def test_binder_rejects_cross_candidate_and_fabricated_or_duplicate_quotes():
    snapshot, _, _, order = _order()
    imported = import_independent_review(work_order=order, provider_payload=_payload(), provider_wrapper_bytes=b"receipt",
        provider="fake", requested_model="glm-5.3", reported_model="glm-5.3", usage={}, cost_usd_micros=0,
        duration_ms=1, run_id="wp2", imported_at=NOW)
    cross = _snapshot(market_revision_suffix="d")
    wrong = CanonicalRuleSegment(source_segment_id="segment:one",
        candidate_snapshot_id=cross.record_id, candidate_snapshot_sha256=cross.canonical_sha256,
        source_artifact_id=cross.canonical_rule_source_artifact_id,
        source_artifact_sha256=cross.rule_source_sha256,
        source_bytes=b"official rules", text="official rules")
    with pytest.raises(RuleBindingError, match="frozen Candidate"):
        bind_rule_parse_proposal(proposal=imported.proposal,
            segments={wrong.source_segment_id: wrong}, required_fields=("yes_trigger",))

    absent = CanonicalRuleSegment(source_segment_id="segment:one",
        candidate_snapshot_id=snapshot.record_id, candidate_snapshot_sha256=snapshot.canonical_sha256,
        source_artifact_id=snapshot.canonical_rule_source_artifact_id,
        source_artifact_sha256=snapshot.rule_source_sha256,
        source_bytes=b"different source", text="different source")
    with pytest.raises(RuleBindingError, match="absent"):
        bind_rule_parse_proposal(proposal=imported.proposal,
            segments={absent.source_segment_id: absent}, required_fields=("yes_trigger",))

    duplicate = CanonicalRuleSegment(source_segment_id="segment:one",
        candidate_snapshot_id=snapshot.record_id, candidate_snapshot_sha256=snapshot.canonical_sha256,
        source_artifact_id=snapshot.canonical_rule_source_artifact_id,
        source_artifact_sha256=snapshot.rule_source_sha256,
        source_bytes=b"official rules / official rules", text="official rules / official rules")
    with pytest.raises(RuleBindingError, match="ambiguous duplicate"):
        bind_rule_parse_proposal(proposal=imported.proposal,
            segments={duplicate.source_segment_id: duplicate}, required_fields=("yes_trigger",))


def test_human_patch_is_source_and_hash_bound_and_stale_patch_fails_closed():
    snapshot, _, _, _ = _order()
    draft = {"yes_trigger": "old"}
    draft_hash = content_sha256(draft)
    segment = CanonicalRuleSegment(source_segment_id="segment:one",
        candidate_snapshot_id=snapshot.record_id, candidate_snapshot_sha256=snapshot.canonical_sha256,
        source_artifact_id=snapshot.canonical_rule_source_artifact_id,
        source_artifact_sha256=snapshot.rule_source_sha256,
        source_bytes=b"official rules", text="official rules")
    patch = RuleInterpretationPatch(field_path="yes_trigger", old_value_hash=hashlib.sha256(b"old").hexdigest(),
        new_value="new", source_segment_id="segment:one",
        source_artifact_id=segment.source_artifact_id,
        source_content_sha256=hashlib.sha256(segment.source_bytes).hexdigest(),
        exact_quote="official rules", quote_start=0, quote_end=len("official rules"))
    result, approval = apply_rule_review(draft_id="draft:one", draft=draft, draft_hash=draft_hash,
        action=RuleReviewAction.PATCH, reviewer_id="human", reason_codes=("FIX",),
        reviewed_at=NOW, patches=(patch,), canonical_segments={"segment:one": segment})
    assert result["yes_trigger"] == "new" and approval.after_hash
    fabricated = patch.model_copy(update={"exact_quote": "made-up-value", "quote_end": len("made-up-value")})
    with pytest.raises(RuleBindingError, match="exactly once"):
        apply_rule_review(draft_id="draft:one", draft=draft, draft_hash=draft_hash,
            action=RuleReviewAction.PATCH, reviewer_id="human", reason_codes=("FIX",),
            reviewed_at=NOW, patches=(fabricated,), canonical_segments={"segment:one": segment})
    with pytest.raises(RuleBindingError, match="stale"):
        apply_rule_review(draft_id="draft:one", draft=draft, draft_hash="0" * 64,
            action=RuleReviewAction.APPROVE, reviewer_id="human", reason_codes=("OK",), reviewed_at=NOW)


def test_attempt_budget_quarantine_is_typed_and_wrapper_is_required():
    _, _, _, order = _order()
    with pytest.raises(IndependentReviewQuarantine) as caught:
        import_independent_review(work_order=order, provider_payload=_payload(), provider_wrapper_bytes=b"receipt",
            provider="fake", requested_model="glm-5.3", reported_model="glm-5.3",
            usage={"total_tokens": order.budget.max_total_tokens + 1}, cost_usd_micros=0,
            duration_ms=1, run_id="wp2", imported_at=NOW)
    assert caught.value.receipt is not None
    assert caught.value.receipt.status == IndependentReviewAttemptStatus.BUDGET_EXCEEDED
    with pytest.raises(IndependentReviewError, match="wrapper bytes"):
        import_independent_review(work_order=order, provider_payload=_payload(), provider_wrapper_bytes=b"",
            provider="fake", requested_model="glm-5.3", reported_model="glm-5.3", usage={},
            cost_usd_micros=0, duration_ms=1, run_id="wp2", imported_at=NOW)


def test_same_logical_attempt_replay_is_idempotent_and_conflicting_return_fails(tmp_path):
    _, _, _, order = _order()
    first = import_independent_review(work_order=order, provider_payload=_payload(), provider_wrapper_bytes=b"receipt",
        provider="fake", requested_model="glm-5.3", reported_model="glm-5.3", usage={}, cost_usd_micros=0,
        duration_ms=1, run_id="wp2", imported_at=NOW)
    repository = AlphaRepository(tmp_path / "alpha.db")
    repository.save_contract(first.review)
    assert repository.save_contract(first.review) == first.review.canonical_sha256
    changed_payload = _payload()
    changed_payload["independent_semantic_review"]["topic"] = "climate"
    second = import_independent_review(work_order=order, provider_payload=changed_payload, provider_wrapper_bytes=b"receipt-2",
        provider="fake", requested_model="glm-5.3", reported_model="glm-5.3", usage={}, cost_usd_micros=0,
        duration_ms=1, run_id="wp2", imported_at=NOW)
    assert second.review.record_id == first.review.record_id
    with pytest.raises(ContractConflictError):
        repository.save_contract(second.review)


def test_rule_review_contract_rejects_forged_after_draft():
    draft = {"yes_trigger": "old"}
    before_hash = content_sha256(draft)
    patch = RuleInterpretationPatch(field_path="yes_trigger", old_value_hash=hashlib.sha256(b"old").hexdigest(),
        new_value="new", source_segment_id="segment:one", source_artifact_id="source:rules",
        source_content_sha256=hashlib.sha256(b"official rules").hexdigest(),
        exact_quote="official rules", quote_start=0, quote_end=len("official rules"))
    after_hash = content_sha256({"yes_trigger": "new"})
    after_id = stable_record_id("rule_contract_draft", "draft:one", after_hash)
    receipt_id = stable_record_id("rule_review_approval", "draft:one", before_hash,
        RuleReviewAction.PATCH, ("FIX",), (patch,), after_id, after_hash)
    with pytest.raises(ValidationError, match="after draft"):
        RuleReviewApproval(record_id=receipt_id, review_receipt_id=receipt_id, run_id="wp2",
            created_at=NOW, source="human_rule_review", source_version="v1",
            rule_contract_draft_id="draft:one", before_hash=before_hash, before_draft=draft,
            reviewer_id="human", reviewed_at=NOW, action=RuleReviewAction.PATCH,
            reason_codes=("FIX",), patches=(patch,), after_draft_id=after_id,
            after_hash=after_hash, after_draft={"yes_trigger": "forged"})
