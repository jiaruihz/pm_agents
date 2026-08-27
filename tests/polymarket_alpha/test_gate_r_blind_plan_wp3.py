from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.polymarket_alpha.contracts import (
    BlindCandidateProjection, BlindPlanQuestion, BlindResearchPacket,
    BlindResearchPlanSeal, BlindResearchQuestion, BlindResearchQuestionSet,
    BlindRuleView, BlindWorkOrderPromptSeal, SourcePlan,
    CandidateEligibility, CandidateSnapshotSeal, CandidateState, RuleContract, RuleGate,
    content_sha256, stable_record_id,
)
from src.polymarket_alpha.research import (
    BlindPlanError, BlindPlanLeakageError, BlindResearchPlanCompiler, SourcePlanPolicy,
    seal_blind_work_order_prompt, verify_blind_work_order_prompt,
)


NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)
SHA = "a" * 64


def _rule(trigger: str = "A final public bulletin confirms the defined event") -> RuleContract:
    return RuleContract(record_id="rule_contract:" + "b" * 64, run_id="fixture", created_at=NOW,
        source="fixture", source_version="v1", market_id="market-private", rule_hash=SHA,
        contract_revision_id="rule_revision:" + "c" * 64, subject_entity="Example Agency",
        entity_match_rule="named agency", yes_trigger=trigger, deadline=NOW + timedelta(days=1), timezone="UTC",
        resolution_sources=("Example Agency bulletin",), source_precedence=("final bulletin",),
        initial_or_final="FINAL", clarity_score=Decimal("0.9"), rule_gate=RuleGate.PASS, parser_version="v1")


def _snapshot(rule_source_sha256: str = "1" * 64) -> CandidateSnapshotSeal:
    payload = dict(candidate_id="candidate:" + "d" * 64, candidate_revision_id="candidate_revision:" + "e" * 64,
        canonical_market_revision_id="market_snapshot:" + "f" * 64,
        canonical_rule_source_artifact_id="rule", rule_source_sha256=rule_source_sha256,
        rule_source_byte_length=9, lifecycle_state=CandidateState.RECALLED,
        eligibility=CandidateEligibility.ELIGIBLE, eligibility_as_of_utc=NOW,
        eligibility_policy_id="eligibility", eligibility_policy_version="v1", allowed_projection_input_ids=("rule",))
    record_id = stable_record_id("candidate_snapshot", payload)
    return CandidateSnapshotSeal(record_id=record_id, seal_id=record_id, run_id="fixture", created_at=NOW,
        source="fixture", source_version="v1", provenance=(), extensions={}, seal_sha256=content_sha256(payload), **payload)


def _projection_packet(rule: RuleContract, snapshot: CandidateSnapshotSeal):
    text = f"What primary evidence directly addresses this rule condition: {rule.yes_trigger}?"
    question_id = stable_record_id("blind_question", {
        "template_id": "RULE_TRIGGER_EVIDENCE", "rule_hash": rule.rule_hash, "evidence_ids": (),
    })
    q = BlindResearchQuestion(question_id=question_id, template_id="RULE_TRIGGER_EVIDENCE",
        generated_from_rule_contract_hash=rule.rule_hash, text=text)
    blind_candidate_id = stable_record_id("blind_candidate", {
        "candidate_id": snapshot.candidate_id, "rule_hash": rule.rule_hash,
        "contract_revision_id": rule.contract_revision_id, "questions": (q.question_id,), "evidence": (),
    })
    projection = BlindCandidateProjection(record_id=blind_candidate_id, blind_candidate_id=blind_candidate_id,
        run_id=stable_record_id("blind_run", "fixture"), created_at=NOW,
        source="blind_projection_builder", source_version="v1",
        provenance=(), extensions={}, rule_contract_hash=rule.rule_hash, neutral_proposition="The agency satisfies the defined condition.",
        subject_entity="Example Agency", deadline=rule.deadline, research_questions=(q,), evidence=())
    view = BlindRuleView.from_rule_contract(rule)
    packet_id = stable_record_id("blind_packet", {
        "projection_sha256": projection.canonical_sha256, "blind_rule_sha256": content_sha256(view),
    })
    packet = BlindResearchPacket(record_id=packet_id, run_id=projection.run_id,
        created_at=NOW, source="blind_packet_builder", source_version="v1", provenance=(), extensions={}, projection=projection, blind_rule=view)
    return projection, packet


def _compile(**changes):
    rule = changes.pop("rule", _rule())
    snapshot = changes.pop("snapshot", _snapshot())
    projection, packet = changes.pop("projection_packet", _projection_packet(rule, snapshot))
    return BlindResearchPlanCompiler().compile(candidate_snapshot=snapshot, projection=projection,
        packet=packet, rule_contract=rule, input_artifact_sha256s=changes.pop("artifacts", {"rule": snapshot.rule_source_sha256}),
        research_as_of_utc=changes.pop("as_of", NOW), pit_cutoff_utc=changes.pop("cutoff", NOW),
        created_at_utc=changes.pop("created", NOW), **changes), rule, projection, packet


def _seal(built, rule, packet, *, provider_policy_version="v1"):
    return seal_blind_work_order_prompt(plan=built, candidate_snapshot=_snapshot(), packet=packet,
        rule_contract=rule, research_job_id="research_job:" + "a" * 64, attempt_policy_id="manual-v1",
        output_schema_id="blind-return-v1", output_schema_sha256="7" * 64,
        provider_policy_id="manual-provider", provider_policy_version=provider_policy_version,
        created_at_utc=NOW, expires_at_utc=NOW + timedelta(hours=1))


def test_atomic_deterministic_plan_and_prompt_exact_bytes() -> None:
    first, rule, _, packet = _compile()
    second, _, _, _ = _compile()
    assert first.question_set.question_set_id == second.question_set.question_set_id
    assert first.source_plan.source_plan_sha256 == second.source_plan.source_plan_sha256
    sealed = _seal(first, rule, packet)
    assert sealed.prompt_bytes.endswith(b"\n") and b"\r" not in sealed.prompt_bytes
    assert len(sealed.prompt_bytes) == sealed.seal.byte_length
    assert sealed.export_bytes() == sealed.prompt_bytes
    assert sealed.seal.prompt_sha256 == __import__("hashlib").sha256(sealed.prompt_bytes).hexdigest()
    verify_blind_work_order_prompt(seal=sealed.seal, prompt_bytes=sealed.prompt_bytes, verified_at_utc=NOW)
    with pytest.raises(BlindPlanError):
        verify_blind_work_order_prompt(seal=sealed.seal, prompt_bytes=sealed.prompt_bytes.replace(b"\n", b"\r\n"), verified_at_utc=NOW)


@pytest.mark.parametrize("trigger", [
    "The bid is favorable", "Buy YES now", "slug: private", "https://example.invalid/x",
    "A wallet indicates this", "RecallHit private reason", "GLM output says so", "operator free text",
])
def test_nested_and_adversarial_provenance_fails_closed(trigger: str) -> None:
    # Existing packet validation rejects most forms; the WP3 compiler additionally
    # rejects terms that older packet contracts did not classify as a market leak.
    with pytest.raises((ValueError, BlindPlanLeakageError)):
        _compile(rule=_rule(trigger))


def test_cross_binding_and_policy_invalidation_fail_closed() -> None:
    built, rule, projection, packet = _compile()
    with pytest.raises(BlindPlanError, match="allowlist"):
        _compile(artifacts={"other": "6" * 64})
    bad_policy = SourcePlanPolicy(**{**{name: getattr(__import__("src.polymarket_alpha.research", fromlist=["DEFAULT_SOURCE_PLAN_POLICY"]).DEFAULT_SOURCE_PLAN_POLICY, name)
        for name in __import__("src.polymarket_alpha.research", fromlist=["DEFAULT_SOURCE_PLAN_POLICY"]).DEFAULT_SOURCE_PLAN_POLICY.__dataclass_fields__}, "allowed_domains": ("polymarket.example",)})
    with pytest.raises(BlindPlanLeakageError):
        _compile(source_policy=bad_policy)
    mismatched = built.source_plan.model_copy(update={"question_set_sha256": "0" * 64})
    broken = type(built)(built.question_set, mismatched, built.leakage_receipt, built.plan_seal)
    with pytest.raises(BlindPlanError, match="cross-hash"):
        seal_blind_work_order_prompt(plan=broken, candidate_snapshot=_snapshot(), packet=packet, rule_contract=rule,
            research_job_id="job", attempt_policy_id="attempt", output_schema_id="schema", output_schema_sha256="7" * 64,
            provider_policy_id="provider", provider_policy_version="v1", created_at_utc=NOW, expires_at_utc=NOW + timedelta(hours=1))


def test_rule_cutoff_and_input_artifact_change_identity() -> None:
    one, *_ = _compile()
    later = NOW + timedelta(minutes=1)
    two, *_ = _compile(cutoff=later, as_of=later, created=later)
    changed_snapshot = _snapshot("8" * 64)
    three, *_ = _compile(snapshot=changed_snapshot, artifacts={"rule": "8" * 64})
    assert one.question_set.question_set_id != two.question_set.question_set_id
    assert one.question_set.question_set_id != three.question_set.question_set_id


@pytest.mark.parametrize("contract_name", ["question", "question_set", "source_plan", "plan_seal", "prompt_seal"])
def test_shared_contracts_reject_forged_content_identities(contract_name: str) -> None:
    built, rule, _, packet = _compile()
    sealed = _seal(built, rule, packet)
    values = {
        "question": (built.question_set.questions[0], BlindPlanQuestion, "question_id"),
        "question_set": (built.question_set, BlindResearchQuestionSet, "question_set_id"),
        "source_plan": (built.source_plan, SourcePlan, "source_plan_id"),
        "plan_seal": (built.plan_seal, BlindResearchPlanSeal, "plan_seal_id"),
        "prompt_seal": (sealed.seal, BlindWorkOrderPromptSeal, "work_order_id"),
    }
    contract, model, field = values[contract_name]
    payload = contract.model_dump(mode="python")
    payload[field] = "forged:identity"
    with pytest.raises(ValidationError, match="content-derived|bind all prompt metadata|namespace"):
        model.model_validate(payload)


def test_source_plan_semantics_reject_venue_and_unknown_critical_claim_even_with_resealed_id() -> None:
    built, *_ = _compile()
    payload = built.source_plan.model_dump(mode="python")
    payload["allowed_source_classes"] = ("MARKET_VENUE",)
    identity = {key: value for key, value in payload.items() if key not in {"source_plan_id", "source_plan_sha256"}}
    payload["source_plan_id"] = stable_record_id("blind_source_plan", identity)
    payload["source_plan_sha256"] = content_sha256(identity)
    with pytest.raises(ValidationError, match="both allowed and forbidden|forbidden"):
        SourcePlan.model_validate(payload)

    payload = built.source_plan.model_dump(mode="python")
    payload["critical_claim_ids"] = ("blind_plan_question:" + "f" * 64,)
    identity = {key: value for key, value in payload.items() if key not in {"source_plan_id", "source_plan_sha256"}}
    payload["source_plan_id"] = stable_record_id("blind_source_plan", identity)
    payload["source_plan_sha256"] = content_sha256(identity)
    with pytest.raises(ValidationError, match="members of the QuestionSet"):
        SourcePlan.model_validate(payload)


def test_compiler_rebuilds_question_template_and_snapshot_lineage() -> None:
    snapshot = _snapshot()
    rule = _rule()
    projection, packet = _projection_packet(rule, snapshot)
    origin = projection.research_questions[0].model_copy(update={"text": "What neutral facts are available?"})
    tampered_projection_id = stable_record_id("blind_candidate", {
        "candidate_id": snapshot.candidate_id, "rule_hash": rule.rule_hash,
        "contract_revision_id": rule.contract_revision_id, "questions": (origin.question_id,), "evidence": (),
    })
    tampered_projection = projection.model_copy(update={"record_id": tampered_projection_id,
        "blind_candidate_id": tampered_projection_id, "research_questions": (origin,)})
    tampered_packet_id = stable_record_id("blind_packet", {
        "projection_sha256": tampered_projection.canonical_sha256,
        "blind_rule_sha256": content_sha256(packet.blind_rule),
    })
    tampered_packet = packet.model_copy(update={"record_id": tampered_packet_id,
        "projection": tampered_projection})
    with pytest.raises(BlindPlanError, match="template provenance"):
        _compile(snapshot=snapshot, rule=rule, projection_packet=(tampered_projection, tampered_packet))

    other_snapshot = snapshot.model_copy(update={"candidate_id": "candidate:" + "9" * 64})
    with pytest.raises(BlindPlanError, match="sealed Candidate snapshot"):
        _compile(snapshot=other_snapshot, rule=rule, projection_packet=(projection, packet))


def test_prompt_policy_and_seal_metadata_are_identity_bound() -> None:
    built, rule, _, packet = _compile()
    first = _seal(built, rule, packet, provider_policy_version="v1")
    second = _seal(built, rule, packet, provider_policy_version="v2")
    assert first.prompt_bytes == second.prompt_bytes
    assert first.seal.work_order_id != second.seal.work_order_id
    assert first.seal.seal_sha256 != second.seal.seal_sha256
    forged = first.seal.model_copy(update={"seal_sha256": "0" * 64})
    with pytest.raises(BlindPlanError, match="metadata or identity"):
        verify_blind_work_order_prompt(seal=forged, prompt_bytes=first.prompt_bytes, verified_at_utc=NOW)
