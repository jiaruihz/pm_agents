"""P1-A04 controlled offline orchestration acceptance tests."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from src.polymarket_alpha.contracts import (
    BlindResearchPacket,
    CaptureScope,
    EvidenceOrigin,
    EvidenceSupport,
    HashScope,
    Replayability,
    ResearchImportStatus,
    ResearchJobStatus,
    RuleContract,
    SourceTier,
    canonical_json,
)
from src.polymarket_alpha.pilot import run_offline_fixture_pilot
from src.polymarket_alpha.research import (
    ResearchBindingError,
    DeterministicFakeExecutor,
    DraftClaim,
    DraftEstimate,
    DraftSource,
    ResearchBaselineError,
    ResearchDraft,
    build_research_job,
    ResearchResumeError,
    prepare_research_execution,
    resume_research_execution,
)
from src.polymarket_alpha.security import audit_source_tree
from src.polymarket_alpha.storage import AlphaRepository


def _one(db: Path, name: str, model):
    conn = sqlite3.connect(db)
    try:
        raw = conn.execute(
            "SELECT canonical_json FROM alpha_contract_record WHERE contract_type=?",
            (name,),
        ).fetchone()[0]
    finally:
        conn.close()
    return model.model_validate(json.loads(raw))


def _setup(tmp_path: Path):
    db = tmp_path / "alpha.db"
    repository = AlphaRepository(db)
    run_offline_fixture_pilot(repository)
    packet = _one(db, "BlindResearchPacket", BlindResearchPacket)
    rule = _one(db, "RuleContract", RuleContract)
    created = packet.created_at + timedelta(minutes=1)
    prepared = prepare_research_execution(
        repository=repository,
        artifact_root=tmp_path,
        packet=packet,
        rule_contract=rule,
        provider_policy_id="fixture-provider-v1",
        source_policy_id="fixture-sources-v1",
        max_attempts=2,
        available_at=created + timedelta(minutes=1),
        expires_at=created + timedelta(hours=1),
        leased_at=created + timedelta(minutes=1),
        lease_duration=timedelta(minutes=10),
        worker_id="fixture-worker",
        run_id="p1-a04-run",
        created_at=created,
    )
    return repository, packet, rule, prepared


def _draft_bytes(packet: BlindResearchPacket) -> bytes:
    captured = packet.created_at + timedelta(minutes=2, seconds=10)
    source = DraftSource(
        source_key="official",
        source_name="Official agency",
        source_url_or_source_id="https://agency.example/final",
        media_type="text/plain",
        captured_at=captured,
        effective_as_of=captured - timedelta(seconds=1),
        capture_scope=CaptureScope.EXCERPT_ONLY,
        hash_scope=HashScope.CLAIM_EXCERPT,
        artifact_locator="provider-untrusted/locator.txt",
        replayability=Replayability.EXCERPT,
    )
    claim = DraftClaim(
        source_key="official",
        claim="The final bulletin confirms the condition.",
        supports_yes_or_no=EvidenceSupport.YES,
        source_tier=SourceTier.T0,
        accessed_at=captured,
        effective_as_of=captured - timedelta(seconds=1),
        primary_or_secondary="PRIMARY",
        quotation_or_paraphrase_location="paragraph 1",
        confidence=Decimal("0.9"),
        origin=EvidenceOrigin.PRIMARY_SOURCE,
        excerpt_context="final bulletin",
    )
    estimate = DraftEstimate(
        model_type="fixture",
        p_event_yes_low=Decimal("0.2"),
        p_event_yes_mid=Decimal("0.3"),
        p_event_yes_high=Decimal("0.4"),
        uncertainty_drivers=("timing",),
        assumptions=("official source",),
        model_version="fixture-v1",
    )
    draft = ResearchDraft(
        sources=(source,),
        claims=(claim,),
        estimate=estimate,
        completed_at=captured,
        producer="fixture-provider",
        producer_version="v1",
    )
    return canonical_json(draft).encode("utf-8")


def test_fake_provider_to_importer_completion_is_atomic_and_replayable(tmp_path: Path) -> None:
    repository, packet, rule, prepared = _setup(tmp_path)
    fake = DeterministicFakeExecutor("fixture-provider-v1", "fixture-sources-v1")
    payload = fake.execute(
        work_order=prepared.lease.work_order,
        provider_draft_bytes=_draft_bytes(packet),
        source_bytes={"official": b"The final bulletin confirms the condition."},
    )
    received = prepared.lease.attempt.leased_at + timedelta(minutes=1)
    processed = received + timedelta(minutes=1)
    first = resume_research_execution(
        repository=repository,
        artifact_root=tmp_path,
        packet=packet,
        rule_contract=rule,
        prepared=prepared,
        payload=payload,
        received_at=received,
        processed_at=processed,
        run_id="p1-a04-run",
    )
    assert first.import_outcome is not None
    assert first.import_outcome.receipt.status == ResearchImportStatus.ACCEPTED
    assert first.transition.to_status == ResearchJobStatus.COMPLETED
    assert repository.get_research_job_status(prepared.job.job_id) == ResearchJobStatus.COMPLETED
    assert first.compiled is not None
    assert first.compiled.result.source_artifacts[0].artifact_locator.startswith(
        "_sealed/research_returns/"
    )
    assert "provider-untrusted" not in first.compiled.result.source_artifacts[0].artifact_locator

    replay_prepared = prepare_research_execution(
        repository=repository,
        artifact_root=tmp_path,
        packet=packet,
        rule_contract=rule,
        provider_policy_id="fixture-provider-v1",
        source_policy_id="fixture-sources-v1",
        max_attempts=2,
        available_at=prepared.job.available_at,
        expires_at=prepared.job.expires_at,
        leased_at=prepared.lease.attempt.leased_at,
        lease_duration=prepared.lease.attempt.lease_expires_at - prepared.lease.attempt.leased_at,
        worker_id="fixture-worker",
        run_id="p1-a04-run",
        created_at=prepared.job.created_at,
    )
    second = resume_research_execution(
        repository=repository,
        artifact_root=tmp_path,
        packet=packet,
        rule_contract=rule,
        prepared=replay_prepared,
        payload=payload,
        received_at=received,
        processed_at=processed,
        run_id="p1-a04-run",
    )
    assert second == first
    assert repository.get_research_job_status(prepared.job.job_id) == ResearchJobStatus.COMPLETED


def test_malformed_draft_is_quarantined_without_canonical_result(tmp_path: Path) -> None:
    repository, packet, rule, prepared = _setup(tmp_path)
    payload = DeterministicFakeExecutor(
        "fixture-provider-v1", "fixture-sources-v1"
    ).execute(
        work_order=prepared.lease.work_order,
        provider_draft_bytes=b'{"not":"a research draft"}',
        source_bytes={},
    )
    outcome = resume_research_execution(
        repository=repository,
        artifact_root=tmp_path,
        packet=packet,
        rule_contract=rule,
        prepared=prepared,
        payload=payload,
        received_at=prepared.lease.attempt.leased_at + timedelta(seconds=1),
        processed_at=prepared.lease.attempt.leased_at + timedelta(seconds=2),
        run_id="p1-a04-run",
    )
    assert outcome.compiled is None
    assert outcome.import_outcome is None
    assert outcome.transition.to_status == ResearchJobStatus.QUARANTINED
    assert repository.get_research_job_status(prepared.job.job_id) == ResearchJobStatus.QUARANTINED


def test_resume_rejects_changed_rule_and_market_requires_blind_baseline(tmp_path: Path) -> None:
    repository, packet, rule, prepared = _setup(tmp_path)
    payload = DeterministicFakeExecutor(
        "fixture-provider-v1", "fixture-sources-v1"
    ).execute(
        work_order=prepared.lease.work_order,
        provider_draft_bytes=_draft_bytes(packet),
        source_bytes={"official": b"The final bulletin confirms the condition."},
    )
    with pytest.raises(ResearchResumeError, match="differs"):
        resume_research_execution(
            repository=repository,
            artifact_root=tmp_path,
            packet=packet,
            rule_contract=rule.model_copy(update={"rule_hash": "0" * 64}),
            prepared=prepared,
            payload=payload,
            received_at=prepared.lease.attempt.leased_at + timedelta(seconds=1),
            processed_at=prepared.lease.attempt.leased_at + timedelta(seconds=2),
            run_id="p1-a04-run",
        )
    with pytest.raises(ResearchResumeError, match="cannot precede"):
        resume_research_execution(
            repository=repository,
            artifact_root=tmp_path,
            packet=packet,
            rule_contract=rule,
            prepared=prepared,
            payload=payload,
            received_at=prepared.lease.attempt.leased_at + timedelta(seconds=2),
            processed_at=prepared.lease.attempt.leased_at + timedelta(seconds=1),
            run_id="p1-a04-run",
        )

    from test_market_research_p0_08c import _freeze

    market_packet = _freeze()
    with pytest.raises(ResearchBaselineError, match="requires"):
        prepare_research_execution(
            repository=repository,
            artifact_root=tmp_path,
            packet=market_packet,
            rule_contract=rule,
            provider_policy_id="fixture-provider-v1",
            source_policy_id="fixture-sources-v1",
            max_attempts=1,
            available_at=market_packet.created_at,
            expires_at=market_packet.created_at + timedelta(hours=1),
            leased_at=market_packet.created_at,
            lease_duration=timedelta(minutes=1),
            worker_id="fixture-worker",
            run_id="p1-a04-market",
            created_at=market_packet.created_at,
        )


def test_market_execution_seals_exact_accepted_blind_baseline(tmp_path: Path) -> None:
    from tests.polymarket_alpha.test_market_research_p0_08c import (
        _accepted_blind_result,
        _freeze,
    )

    repository = Mock(spec=AlphaRepository)
    packet = _freeze()
    accepted = _accepted_blind_result()[4]
    created = packet.created_at
    prepared = prepare_research_execution(
        repository=repository,
        artifact_root=tmp_path,
        packet=packet,
        rule_contract=packet.rule_contract,
        provider_policy_id="fixture-provider-v1",
        source_policy_id="fixture-sources-v1",
        max_attempts=1,
        available_at=created,
        expires_at=created + timedelta(hours=1),
        leased_at=created,
        lease_duration=timedelta(minutes=10),
        worker_id="fixture-worker",
        run_id="p1-a04-market-valid",
        created_at=created,
        accepted_blind_result=accepted,
    )
    brief = json.loads((tmp_path / prepared.brief_locator).read_bytes())
    assert brief["accepted_blind_result_payload"]["record_id"] == accepted.record_id

    brief["accepted_blind_result_payload"]["producer"] = "tampered-provider"
    tampered = canonical_json(brief).encode("utf-8")
    with pytest.raises(ResearchBindingError, match="frozen packet"):
        build_research_job(
            packet=packet,
            brief_bytes=tampered,
            brief_artifact_locator="_sealed/research_briefs/tampered.json",
            rule_contract=packet.rule_contract,
            provider_policy_id="fixture-provider-v1",
            source_policy_id="fixture-sources-v1",
            max_attempts=1,
            available_at=created,
            expires_at=created + timedelta(hours=1),
            run_id="p1-a04-market-tampered",
            created_at=created,
        )


def test_orchestrator_has_no_network_or_execution_capability() -> None:
    audit = audit_source_tree("src/polymarket_alpha/research/orchestrator.py")
    assert audit.passed, audit.findings
