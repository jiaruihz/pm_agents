from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.polymarket_alpha.artifacts import ArtifactConflictError, ArtifactPathError, ArtifactStore
from src.polymarket_alpha.contracts import (
    CaptureScope, EvidenceOrigin, EvidenceSupport, ExactFileCopyPolicy,
    ExportApprovalAction, HashScope, ManualCaptureScope, Replayability,
    ResearchAttempt, ResearchImportStatus, SourceCapture, SourceRepresentation,
    SourceTier,
)
from src.polymarket_alpha.research.draft import DraftClaim, DraftEstimate, DraftSource, ResearchDraft
from src.polymarket_alpha.research.manual_handoff import (
    CaptureDisposition, ManualHandoffError, bind_return_to_draft,
    build_source_capture, build_source_manifest, capture_manual_return,
    create_export_approval, export_exact_prompt, import_bound_return,
    persist_capture_artifacts, verify_source_capture_bytes,
)
from tests.polymarket_alpha.test_gate_r_blind_plan_wp3 import _compile as _plan, _seal


NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def _capture(raw: bytes = b"The final bulletin is published.", *, scope=ManualCaptureScope.EXCERPT,
             claim_ids=("claim:critical",), pit_cutoff=NOW):
    return build_source_capture(
        source_key="final", canonical_url="https://authority.example/record", title="Record",
        publisher="Authority", source_class="OFFICIAL_PRIMARY", primary_or_secondary="PRIMARY",
        accessed_at_utc=NOW, first_available_at_utc=NOW - timedelta(days=1),
        pit_cutoff_utc=pit_cutoff, pit_available=True, capture_scope=scope,
        representation=SourceRepresentation.TEXT_EXPORT, content_type="text/plain",
        artifact_locator="sources/record.txt", claim_ids=claim_ids, content_bytes=raw,
        quote_locator_or_excerpt=("final bulletin" if scope == ManualCaptureScope.EXCERPT else None),
    )


def _attempt(job_id: str) -> ResearchAttempt:
    attempt_id = "research_attempt:" + "b" * 64
    return ResearchAttempt(record_id=attempt_id, attempt_id=attempt_id, run_id="wp4",
        created_at=NOW, source="fixture", source_version="v1", job_id=job_id,
        job_sha256="c" * 64, attempt_number=1, worker_id="human",
        leased_at=NOW, lease_expires_at=NOW + timedelta(hours=2))


def _draft(*, claim="Agency issued a final bulletin.") -> ResearchDraft:
    source = DraftSource(source_key="final", source_name="Authority",
        source_url_or_source_id="https://authority.example/record", media_type="text/plain",
        captured_at=NOW + timedelta(minutes=10), effective_as_of=NOW - timedelta(minutes=1),
        capture_scope=CaptureScope.EXCERPT_ONLY, hash_scope=HashScope.CLAIM_EXCERPT,
        artifact_locator="sources/record.txt", replayability=Replayability.EXCERPT)
    evidence = DraftClaim(source_key="final", claim=claim,
        supports_yes_or_no=EvidenceSupport.YES, source_tier=SourceTier.T0,
        accessed_at=NOW, effective_as_of=NOW - timedelta(minutes=1),
        primary_or_secondary="PRIMARY", quotation_or_paraphrase_location="paragraph 1",
        confidence=Decimal("0.9"), origin=EvidenceOrigin.PRIMARY_SOURCE,
        excerpt_context="final bulletin")
    estimate = DraftEstimate(model_type="manual", p_event_yes_low=Decimal("0.2"),
        p_event_yes_mid=Decimal("0.3"), p_event_yes_high=Decimal("0.4"),
        uncertainty_drivers=("timing",), assumptions=("official source",), model_version="v1")
    return ResearchDraft(sources=(source,), claims=(evidence,), estimate=estimate,
        completed_at=NOW + timedelta(minutes=10), producer="gpt-pro-manual", producer_version="v1")


def _handoff(*, appendix: bytes | None = None, response: bytes = b"Independent research response",
             capture_pit_cutoff=NOW):
    plan, rule, _, packet = _plan()
    prompt = _seal(plan, rule, packet)
    approval = create_export_approval(seal=prompt.seal, prompt_bytes=prompt.prompt_bytes,
        approver_id="human", approved_at_utc=NOW, expires_at_utc=NOW + timedelta(hours=1),
        action=ExportApprovalAction.APPROVE, review_check_codes=("BLIND", "EXACT_BYTES"),
        copy_policy=ExactFileCopyPolicy.COPY_ATTESTED_NOT_CRYPTOGRAPHICALLY_OBSERVED)
    attempt = _attempt(prompt.seal.research_job_id)
    binding = capture_manual_return(seal=prompt.seal, approval=approval, attempt=attempt,
        source_plan=plan.source_plan,
        provider_ui="web", displayed_model="gpt-pro", session_mode="fresh", operator_id="human",
        started_at_utc=NOW + timedelta(minutes=1), completed_at_utc=NOW + timedelta(minutes=10),
        captured_at_utc=NOW + timedelta(minutes=11), raw_transcript_bytes=b"Fresh isolated transcript",
        raw_transcript_locator="returns/transcript.txt", raw_response_bytes=response,
        raw_response_locator="returns/response.txt",
        json_appendix_bytes=(appendix if appendix is not None else _draft().model_dump_json().encode()),
        json_appendix_locator="returns/appendix.json",
        captures=(_capture(claim_ids=plan.source_plan.critical_claim_ids,
                           pit_cutoff=capture_pit_cutoff),),
        observed_tool_usage=("search",))
    return binding, prompt, approval, attempt, packet, plan.source_plan


def test_source_capture_recomputes_bytes_and_manifest_is_deterministic() -> None:
    first, second = _capture(), _capture()
    assert first.source_capture_id == second.source_capture_id
    assert first.content_length_bytes == len(first.content_bytes or b"")
    manifest = build_source_manifest(captures=(first,), critical_claim_ids=("claim:critical",), created_at_utc=NOW)
    assert manifest.manifest_id
    replayed = SourceCapture.model_validate(first.model_dump(mode="python"))
    assert replayed.content_bytes is None
    verify_source_capture_bytes(capture=replayed, content_bytes=first.content_bytes)


def test_reference_cannot_claim_bytes_and_tampered_digest_fails() -> None:
    reference = build_source_capture(source_key="ref", canonical_url="https://authority.example/ref",
        title="Reference", publisher="Authority", source_class="OFFICIAL_PRIMARY",
        primary_or_secondary="PRIMARY", accessed_at_utc=NOW,
        first_available_at_utc=NOW - timedelta(days=1), pit_cutoff_utc=NOW,
        pit_available=True, capture_scope=ManualCaptureScope.REFERENCE,
        representation=SourceRepresentation.NONE, claim_ids=("claim:ref",))
    assert reference.content_sha256 is None
    with pytest.raises(ManualHandoffError):
        build_source_capture(source_key="bad", canonical_url="https://authority.example/bad",
            title="Bad", publisher="Authority", source_class="OFFICIAL_PRIMARY",
            primary_or_secondary="PRIMARY", accessed_at_utc=NOW,
            first_available_at_utc=NOW - timedelta(days=1), pit_cutoff_utc=NOW,
            pit_available=True, capture_scope=ManualCaptureScope.FULL,
            representation=SourceRepresentation.ORIGINAL_BYTES, content_type="text/plain",
            artifact_locator="sources/bad.txt", claim_ids=("claim:bad",), content_bytes=None)


def test_approval_export_replay_expiry_reject_and_conflict(tmp_path) -> None:
    plan, rule, _, packet = _plan()
    prompt = _seal(plan, rule, packet)
    approval = create_export_approval(seal=prompt.seal, prompt_bytes=prompt.prompt_bytes,
        approver_id="human", approved_at_utc=NOW, expires_at_utc=NOW + timedelta(minutes=30),
        action=ExportApprovalAction.APPROVE, review_check_codes=("BLIND",),
        copy_policy=ExactFileCopyPolicy.COPY_ATTESTED_NOT_CRYPTOGRAPHICALLY_OBSERVED)
    first = export_exact_prompt(artifact_root=tmp_path, output_locator="exports/prompt.txt",
        seal=prompt.seal, prompt_bytes=prompt.prompt_bytes, approval=approval,
        exported_at_utc=NOW + timedelta(minutes=1))
    second = export_exact_prompt(artifact_root=tmp_path, output_locator="exports/prompt.txt",
        seal=prompt.seal, prompt_bytes=prompt.prompt_bytes, approval=approval,
        exported_at_utc=NOW + timedelta(minutes=1))
    assert first == second
    with pytest.raises(ManualHandoffError):
        export_exact_prompt(artifact_root=tmp_path, output_locator="exports/prompt.txt",
            seal=prompt.seal, prompt_bytes=prompt.prompt_bytes + b"x", approval=approval,
            exported_at_utc=NOW + timedelta(minutes=1))
    rejected = create_export_approval(seal=prompt.seal, prompt_bytes=prompt.prompt_bytes,
        approver_id="human", approved_at_utc=NOW, expires_at_utc=NOW + timedelta(minutes=30),
        action=ExportApprovalAction.REJECT, review_check_codes=("REJECT",),
        copy_policy=ExactFileCopyPolicy.COPY_ATTESTED_NOT_CRYPTOGRAPHICALLY_OBSERVED)
    with pytest.raises(ManualHandoffError):
        export_exact_prompt(artifact_root=tmp_path, output_locator="exports/rejected.txt",
            seal=prompt.seal, prompt_bytes=prompt.prompt_bytes, approval=rejected,
            exported_at_utc=NOW + timedelta(minutes=1))


def test_complete_capture_to_existing_draft_and_importer_roundtrip() -> None:
    binding, _, _, _, packet, source_plan = _handoff()
    assert binding.disposition == CaptureDisposition.ACCEPTED
    assert binding.critical_claim_ids == tuple(sorted(source_plan.critical_claim_ids))
    compiled = bind_return_to_draft(binding=binding, packet=packet, run_id="wp4",
        created_at=NOW + timedelta(minutes=12))
    imported = import_bound_return(binding=binding, packet=packet, compiled=compiled,
        imported_at=NOW + timedelta(minutes=13), run_id="wp4-import",
        submitted_artifact_locator="returns/submission.json")
    assert imported.receipt.status == ResearchImportStatus.ACCEPTED


def test_capture_pit_cutoff_must_match_sealed_source_plan() -> None:
    with pytest.raises(ManualHandoffError, match="PIT cutoff"):
        _handoff(capture_pit_cutoff=NOW + timedelta(seconds=1))


def test_malformed_leaking_and_insufficient_returns_do_not_advance() -> None:
    malformed, *_ = _handoff(appendix=b"not-json")
    assert malformed.disposition == CaptureDisposition.QUARANTINED
    leaking, *_ = _handoff(response=b"Polymarket odds show a price")
    assert leaking.disposition == CaptureDisposition.QUARANTINED
    plan, rule, _, packet = _plan()
    prompt = _seal(plan, rule, packet)
    approval = create_export_approval(seal=prompt.seal, prompt_bytes=prompt.prompt_bytes,
        approver_id="human", approved_at_utc=NOW, expires_at_utc=NOW + timedelta(hours=1),
        action=ExportApprovalAction.APPROVE, review_check_codes=("BLIND",),
        copy_policy=ExactFileCopyPolicy.COPY_ATTESTED_NOT_CRYPTOGRAPHICALLY_OBSERVED)
    reference = build_source_capture(source_key="final", canonical_url="https://authority.example/record",
        title="Record", publisher="Authority", source_class="OFFICIAL_PRIMARY",
        primary_or_secondary="PRIMARY", accessed_at_utc=NOW,
        first_available_at_utc=NOW - timedelta(days=1), pit_cutoff_utc=NOW,
        pit_available=True, capture_scope=ManualCaptureScope.REFERENCE,
        representation=SourceRepresentation.NONE, claim_ids=("claim:critical",))
    insufficient = capture_manual_return(seal=prompt.seal, approval=approval,
        attempt=_attempt(prompt.seal.research_job_id), source_plan=plan.source_plan,
        provider_ui="web", displayed_model="gpt-pro",
        session_mode="fresh", operator_id="human", started_at_utc=NOW + timedelta(minutes=1),
        completed_at_utc=NOW + timedelta(minutes=10), captured_at_utc=NOW + timedelta(minutes=11),
        raw_transcript_bytes=b"Fresh transcript", raw_transcript_locator="r/t.txt",
        raw_response_bytes=b"Research response", raw_response_locator="r/r.txt",
        json_appendix_bytes=_draft().model_dump_json().encode(), json_appendix_locator="r/a.json",
        captures=(reference,), observed_tool_usage=())
    assert insufficient.disposition == CaptureDisposition.INSUFFICIENT_EVIDENCE
    with pytest.raises(ManualHandoffError):
        bind_return_to_draft(binding=malformed, packet=packet, run_id="wp4",
            created_at=NOW + timedelta(minutes=12))


def test_same_logical_attempt_conflicting_return_hits_repository_conflict(tmp_path) -> None:
    from src.polymarket_alpha.storage import AlphaRepository, ContractConflictError

    first, *_ = _handoff(response=b"First clean response")
    second, *_ = _handoff(response=b"Second clean response")
    assert first.seal.record_id == second.seal.record_id
    repository = AlphaRepository(tmp_path / "alpha.db")
    repository.save_contract(first.seal)
    assert repository.save_contract(first.seal) == first.seal.canonical_sha256
    with pytest.raises(ContractConflictError):
        repository.save_contract(second.seal)


def test_persistence_preflight_prevents_partial_batch_and_source_policy(tmp_path) -> None:
    binding, *_ = _handoff()
    store = ArtifactStore(tmp_path)
    store.write_immutable(binding.seal.raw_response_locator, b"conflict")
    with pytest.raises(ArtifactConflictError):
        persist_capture_artifacts(artifact_root=tmp_path, binding=binding)
    with pytest.raises(ArtifactPathError):
        store.read(binding.seal.raw_transcript_locator)
    with pytest.raises(ManualHandoffError, match="forbidden"):
        build_source_capture(source_key="bad", canonical_url="https://polymarket.com/event/x",
            title="Bad", publisher="Venue", source_class="OFFICIAL_PRIMARY",
            primary_or_secondary="PRIMARY", accessed_at_utc=NOW,
            first_available_at_utc=NOW - timedelta(days=1), pit_cutoff_utc=NOW,
            pit_available=True, capture_scope=ManualCaptureScope.REFERENCE,
            representation=SourceRepresentation.NONE, claim_ids=("claim",))


def test_durable_metadata_cannot_be_persisted_without_original_bytes(tmp_path) -> None:
    binding, *_ = _handoff()
    durable_seal = type(binding.seal).model_validate(binding.seal.model_dump(mode="python"))
    durable_capture = SourceCapture.model_validate(binding.manifest.captures[0].model_dump(mode="python"))
    durable_manifest = build_source_manifest(captures=(durable_capture,),
        critical_claim_ids=binding.critical_claim_ids, created_at_utc=binding.manifest.created_at_utc)
    durable_binding = type(binding)(seal=durable_seal, manifest=durable_manifest,
        disposition=binding.disposition, critical_claim_ids=binding.critical_claim_ids)
    with pytest.raises(ManualHandoffError, match="in-memory raw return bytes"):
        persist_capture_artifacts(artifact_root=tmp_path, binding=durable_binding)


def test_non_utf8_return_is_sealed_as_malformed_and_quarantined() -> None:
    binding, *_ = _handoff(response=b"\xff\xfe")
    assert binding.disposition == CaptureDisposition.QUARANTINED
    assert binding.seal.json_parse_status.value == "MALFORMED"


def test_cross_attempt_and_forged_contracts_fail_closed() -> None:
    binding, prompt, approval, _, _, source_plan = _handoff()
    wrong = _attempt("research_job:" + "9" * 64)
    with pytest.raises(ManualHandoffError, match="does not bind"):
        capture_manual_return(seal=prompt.seal, approval=approval, attempt=wrong,
            source_plan=source_plan,
            provider_ui="web", displayed_model="gpt-pro", session_mode="fresh", operator_id="human",
            started_at_utc=NOW + timedelta(minutes=1), completed_at_utc=NOW + timedelta(minutes=2),
            captured_at_utc=NOW + timedelta(minutes=3), raw_transcript_bytes=b"t",
            raw_transcript_locator="x/t", raw_response_bytes=b"r", raw_response_locator="x/r",
            json_appendix_bytes=b"{}", json_appendix_locator="x/a", captures=(_capture(),),
            observed_tool_usage=())
    forged = binding.seal.model_dump(mode="python")
    forged["return_seal_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="captured content"):
        type(binding.seal).model_validate(forged)
