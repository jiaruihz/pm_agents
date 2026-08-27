from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from src.polymarket_alpha.contracts import (
    BlindResearchPacket,
    PacketStage,
    ResearchAttempt,
    ResearchJob,
    ResearchJobStatus,
    ResearchJobTransition,
    ResearchReturnDisposition,
    ResearchReturnReceipt,
    ResearchTransitionReason,
    ResearchWorkOrder,
    RuleContract,
    bytes_sha256,
    p1_automation_contract_schema_fingerprint,
    stable_record_id,
)
from src.polymarket_alpha.pilot import run_offline_fixture_pilot
from src.polymarket_alpha.pilot.offline import PILOT_NOW
from src.polymarket_alpha.storage import (
    P1_RESEARCH_AUTOMATION_MIGRATION_ID,
    AlphaRepository,
    ContractConflictError,
    migrate,
    p1_research_automation_manifest,
)


def _load_one(db: Path, contract_type: str, model):
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT canonical_json FROM alpha_contract_record WHERE contract_type=?",
        (contract_type,),
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    return model.model_validate(json.loads(rows[0][0]))


def _env(record_id: str, *, run_id: str, created_at):
    return {
        "record_id": record_id,
        "run_id": run_id,
        "created_at": created_at,
        "source": "p1-a02-fixture",
        "source_version": "v1",
        "provenance": (),
        "extensions": {},
    }


def _job_fixture(tmp_path: Path, *, max_attempts: int = 2):
    db = tmp_path / "alpha.db"
    repo = AlphaRepository(db)
    run_offline_fixture_pilot(repo)
    packet = _load_one(db, "BlindResearchPacket", BlindResearchPacket)
    rule = _load_one(db, "RuleContract", RuleContract)
    available = PILOT_NOW + timedelta(hours=1)
    expires = available + timedelta(hours=1)
    brief_bytes = b"neutral blind research brief"
    job_id = stable_record_id(
        "research_job",
        packet.record_id,
        packet.canonical_sha256,
        "provider-policy-v1",
        "blind-source-policy-v1",
    )
    job = ResearchJob(
        **_env(job_id, run_id="p1-a02-job", created_at=available),
        job_id=job_id,
        packet_stage=PacketStage.BLIND,
        packet_id=packet.record_id,
        packet_sha256=packet.canonical_sha256,
        rule_contract_id=rule.record_id,
        rule_contract_sha256=rule.canonical_sha256,
        brief_artifact_locator="research/jobs/blind/brief.md",
        brief_bytes_sha256=bytes_sha256(brief_bytes),
        provider_policy_id="provider-policy-v1",
        source_policy_id="blind-source-policy-v1",
        max_attempts=max_attempts,
        available_at=available,
        expires_at=expires,
    )
    return db, repo, job


def _lease(job: ResearchJob, number: int, leased_at):
    attempt_id = stable_record_id("research_attempt", job.job_id, number)
    attempt = ResearchAttempt(
        **_env(attempt_id, run_id=f"attempt-{number}", created_at=leased_at),
        attempt_id=attempt_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_number=number,
        worker_id="fixture-worker",
        leased_at=leased_at,
        lease_expires_at=leased_at + timedelta(minutes=10),
    )
    work_id = stable_record_id("research_work_order", attempt_id)
    work = ResearchWorkOrder(
        **_env(work_id, run_id=f"work-{number}", created_at=leased_at),
        work_order_id=work_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt.attempt_id,
        attempt_sha256=attempt.canonical_sha256,
        packet_stage=job.packet_stage,
        packet_id=job.packet_id,
        packet_sha256=job.packet_sha256,
        brief_artifact_locator=job.brief_artifact_locator,
        brief_bytes_sha256=job.brief_bytes_sha256,
        provider_policy_id=job.provider_policy_id,
        source_policy_id=job.source_policy_id,
        issued_at=leased_at,
        expires_at=attempt.lease_expires_at,
    )
    transition_id = stable_record_id("research_job_transition", job.job_id, "lease", number)
    transition = ResearchJobTransition(
        **_env(transition_id, run_id=f"lease-transition-{number}", created_at=leased_at),
        transition_id=transition_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt.attempt_id,
        from_status=(ResearchJobStatus.QUEUED if number == 1 else ResearchJobStatus.RETRY_PENDING),
        to_status=ResearchJobStatus.LEASED,
        reason=ResearchTransitionReason.LEASE_GRANTED,
        cause_record_id=attempt.record_id,
        cause_record_sha256=attempt.canonical_sha256,
        effective_at=leased_at,
    )
    return attempt, work, transition


def test_job_lease_return_and_completion_are_atomic_append_only_and_replayable(tmp_path) -> None:
    db, repo, job = _job_fixture(tmp_path)
    attempt, work, leased = _lease(job, 1, job.available_at + timedelta(minutes=1))
    repo.save_contracts_atomic((job, attempt, work, leased))
    assert repo.get_research_job_status(job.job_id) == ResearchJobStatus.LEASED

    returned_bytes = b'{"claims":[],"probability":null}'
    returned_at = attempt.leased_at + timedelta(minutes=2)
    receipt_id = stable_record_id("research_return_receipt", work.work_order_id)
    receipt = ResearchReturnReceipt(
        **_env(receipt_id, run_id="return-1", created_at=returned_at),
        return_receipt_id=receipt_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt.attempt_id,
        attempt_sha256=attempt.canonical_sha256,
        work_order_id=work.work_order_id,
        work_order_sha256=work.canonical_sha256,
        disposition=ResearchReturnDisposition.RETURNED,
        returned_artifact_locator="research/jobs/blind/return.json",
        returned_bytes_sha256=bytes_sha256(returned_bytes),
        returned_byte_length=len(returned_bytes),
        received_at=returned_at,
    )
    completed_id = stable_record_id("research_job_transition", job.job_id, "complete")
    completed = ResearchJobTransition(
        **_env(completed_id, run_id="complete-1", created_at=returned_at),
        transition_id=completed_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt.attempt_id,
        from_status=ResearchJobStatus.LEASED,
        to_status=ResearchJobStatus.COMPLETED,
        reason=ResearchTransitionReason.RESULT_ACCEPTED,
        cause_record_id=receipt.record_id,
        cause_record_sha256=receipt.canonical_sha256,
        effective_at=returned_at,
    )
    expected = repo.save_contracts_atomic((receipt, completed))
    assert repo.save_contracts_atomic((receipt, completed)) == expected
    assert repo.get_research_job_status(job.job_id) == ResearchJobStatus.COMPLETED

    copy = tmp_path / "copy.db"
    shutil.copy2(db, copy)
    copied = AlphaRepository(copy)
    assert copied.get_research_job_status(job.job_id) == ResearchJobStatus.COMPLETED
    conn = sqlite3.connect(copy)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT count(*) FROM alpha_research_job_transition_v1").fetchone()[0] == 2
    conn.close()


def test_expired_lease_reclaims_sequential_attempt_without_duplicate_active_state(tmp_path) -> None:
    _db, repo, job = _job_fixture(tmp_path)
    attempt1, work1, leased1 = _lease(job, 1, job.available_at + timedelta(minutes=1))
    repo.save_contracts_atomic((job, attempt1, work1, leased1))
    expired_at = attempt1.lease_expires_at
    expired_id = stable_record_id("research_job_transition", job.job_id, "expired", 1)
    expired = ResearchJobTransition(
        **_env(expired_id, run_id="expired-1", created_at=expired_at),
        transition_id=expired_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt1.attempt_id,
        from_status=ResearchJobStatus.LEASED,
        to_status=ResearchJobStatus.RETRY_PENDING,
        reason=ResearchTransitionReason.LEASE_EXPIRED,
        cause_record_id=attempt1.record_id,
        cause_record_sha256=attempt1.canonical_sha256,
        effective_at=expired_at,
    )
    repo.save_contract(expired)
    attempt2, work2, leased2 = _lease(job, 2, expired_at + timedelta(seconds=1))
    repo.save_contracts_atomic((attempt2, work2, leased2))
    assert repo.get_research_job_status(job.job_id) == ResearchJobStatus.LEASED

    attempt3_id = stable_record_id("research_attempt", job.job_id, 3)
    attempt3 = attempt2.model_copy(
        update={
            "record_id": attempt3_id,
            "attempt_id": attempt3_id,
            "attempt_number": 3,
            "created_at": attempt2.created_at + timedelta(seconds=1),
            "leased_at": attempt2.leased_at + timedelta(seconds=1),
            "lease_expires_at": attempt2.lease_expires_at + timedelta(seconds=1),
        }
    )
    with pytest.raises(ContractConflictError, match="max_attempts"):
        repo.save_contract(attempt3)
    assert repo.get_contract(attempt3_id) is None


def test_second_attempt_cannot_be_precreated_while_first_lease_is_active(tmp_path) -> None:
    _db, repo, job = _job_fixture(tmp_path)
    attempt1, work1, leased1 = _lease(job, 1, job.available_at + timedelta(minutes=1))
    repo.save_contracts_atomic((job, attempt1, work1, leased1))
    attempt2, _work2, _leased2 = _lease(job, 2, attempt1.leased_at + timedelta(minutes=1))
    with pytest.raises(ContractConflictError, match="QUEUED or RETRY_PENDING"):
        repo.save_contract(attempt2)
    assert repo.get_contract(attempt2.record_id) is None


def test_late_success_return_must_be_quarantined(tmp_path) -> None:
    _db, repo, job = _job_fixture(tmp_path)
    attempt, work, leased = _lease(job, 1, job.available_at + timedelta(minutes=1))
    repo.save_contracts_atomic((job, attempt, work, leased))
    returned = b"late provider output"
    late = work.expires_at + timedelta(seconds=1)
    receipt_id = stable_record_id("research_return_receipt", work.work_order_id, "late")
    receipt = ResearchReturnReceipt(
        **_env(receipt_id, run_id="late-return", created_at=late),
        return_receipt_id=receipt_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt.attempt_id,
        attempt_sha256=attempt.canonical_sha256,
        work_order_id=work.work_order_id,
        work_order_sha256=work.canonical_sha256,
        disposition=ResearchReturnDisposition.RETURNED,
        returned_artifact_locator="research/jobs/blind/late.json",
        returned_bytes_sha256=bytes_sha256(returned),
        returned_byte_length=len(returned),
        received_at=late,
    )
    with pytest.raises(ContractConflictError, match="must be quarantined"):
        repo.save_contract(receipt)
    assert repo.get_contract(receipt.record_id) is None


def test_late_quarantined_return_is_preserved_without_advancing_new_job_state(tmp_path) -> None:
    _db, repo, job = _job_fixture(tmp_path)
    attempt, work, leased = _lease(job, 1, job.available_at + timedelta(minutes=1))
    repo.save_contracts_atomic((job, attempt, work, leased))
    expired_id = stable_record_id("research_job_transition", job.job_id, "late-expired")
    expired = ResearchJobTransition(
        **_env(expired_id, run_id="late-expired", created_at=attempt.lease_expires_at),
        transition_id=expired_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt.attempt_id,
        from_status=ResearchJobStatus.LEASED,
        to_status=ResearchJobStatus.RETRY_PENDING,
        reason=ResearchTransitionReason.LEASE_EXPIRED,
        cause_record_id=attempt.record_id,
        cause_record_sha256=attempt.canonical_sha256,
        effective_at=attempt.lease_expires_at,
    )
    repo.save_contract(expired)

    late_bytes = b"late output retained for quarantine audit"
    received = work.expires_at + timedelta(seconds=1)
    receipt_id = stable_record_id("research_return_receipt", work.work_order_id, "late-quarantine")
    receipt = ResearchReturnReceipt(
        **_env(receipt_id, run_id="late-quarantine", created_at=received),
        return_receipt_id=receipt_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt.attempt_id,
        attempt_sha256=attempt.canonical_sha256,
        work_order_id=work.work_order_id,
        work_order_sha256=work.canonical_sha256,
        disposition=ResearchReturnDisposition.QUARANTINED,
        returned_artifact_locator="research/jobs/blind/late-quarantine.json",
        returned_bytes_sha256=bytes_sha256(late_bytes),
        returned_byte_length=len(late_bytes),
        failure_code="LEASE_EXPIRED",
        received_at=received,
    )
    repo.save_contract(receipt)
    assert repo.get_contract(receipt.record_id) is not None
    assert repo.get_research_job_status(job.job_id) == ResearchJobStatus.RETRY_PENDING


def test_conflicting_transition_rolls_back_contract_and_projection(tmp_path) -> None:
    _db, repo, job = _job_fixture(tmp_path)
    attempt, work, leased = _lease(job, 1, job.available_at + timedelta(minutes=1))
    repo.save_contracts_atomic((job, attempt, work, leased))
    bad_id = stable_record_id("research_job_transition", job.job_id, "stale-from")
    bad = ResearchJobTransition(
        **_env(bad_id, run_id="stale", created_at=attempt.leased_at),
        transition_id=bad_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        from_status=ResearchJobStatus.QUEUED,
        to_status=ResearchJobStatus.CANCELLED,
        reason=ResearchTransitionReason.MANUAL_CANCELLED,
        effective_at=attempt.leased_at,
    )
    with pytest.raises(ContractConflictError, match="current status"):
        repo.save_contract(bad)
    assert repo.get_contract(bad_id) is None
    assert repo.get_research_job_status(job.job_id) == ResearchJobStatus.LEASED


def test_quarantined_return_is_terminal_and_hash_bound(tmp_path) -> None:
    _db, repo, job = _job_fixture(tmp_path)
    attempt, work, leased = _lease(job, 1, job.available_at + timedelta(minutes=1))
    repo.save_contracts_atomic((job, attempt, work, leased))
    returned = b'{"unsafe":"venue-derived blind text"}'
    received = attempt.leased_at + timedelta(minutes=1)
    receipt_id = stable_record_id("research_return_receipt", work.work_order_id, "quarantine")
    receipt = ResearchReturnReceipt(
        **_env(receipt_id, run_id="quarantine", created_at=received),
        return_receipt_id=receipt_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt.attempt_id,
        attempt_sha256=attempt.canonical_sha256,
        work_order_id=work.work_order_id,
        work_order_sha256=work.canonical_sha256,
        disposition=ResearchReturnDisposition.QUARANTINED,
        returned_artifact_locator="research/jobs/blind/quarantine.json",
        returned_bytes_sha256=bytes_sha256(returned),
        returned_byte_length=len(returned),
        failure_code="BLIND_SEMANTIC_LEAK",
        received_at=received,
    )
    transition_id = stable_record_id("research_job_transition", job.job_id, "quarantine")
    transition = ResearchJobTransition(
        **_env(transition_id, run_id="quarantine-transition", created_at=received),
        transition_id=transition_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        attempt_id=attempt.attempt_id,
        from_status=ResearchJobStatus.LEASED,
        to_status=ResearchJobStatus.QUARANTINED,
        reason=ResearchTransitionReason.RESULT_QUARANTINED,
        cause_record_id=receipt.record_id,
        cause_record_sha256=receipt.canonical_sha256,
        effective_at=received,
    )
    repo.save_contracts_atomic((receipt, transition))
    assert repo.get_research_job_status(job.job_id) == ResearchJobStatus.QUARANTINED


def test_packet_invalidation_is_explicit_append_only_terminal_event(tmp_path) -> None:
    _db, repo, job = _job_fixture(tmp_path)
    repo.save_contract(job)
    invalidated_at = job.available_at + timedelta(minutes=1)
    transition_id = stable_record_id("research_job_transition", job.job_id, "packet-invalid")
    transition = ResearchJobTransition(
        **_env(transition_id, run_id="packet-invalid", created_at=invalidated_at),
        transition_id=transition_id,
        job_id=job.job_id,
        job_sha256=job.canonical_sha256,
        from_status=ResearchJobStatus.QUEUED,
        to_status=ResearchJobStatus.INVALIDATED,
        reason=ResearchTransitionReason.PACKET_INVALIDATED,
        cause_record_id=job.packet_id,
        cause_record_sha256=job.packet_sha256,
        effective_at=invalidated_at,
    )
    repo.save_contract(transition)
    assert repo.get_research_job_status(job.job_id) == ResearchJobStatus.INVALIDATED


def test_exact_transition_replay_repairs_drifted_current_state_projection(tmp_path) -> None:
    db, repo, job = _job_fixture(tmp_path)
    attempt, work, leased = _lease(job, 1, job.available_at + timedelta(minutes=1))
    repo.save_contracts_atomic((job, attempt, work, leased))
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE alpha_research_job_v1 SET current_status='QUEUED', current_transition_id=NULL "
        "WHERE job_id=?",
        (job.job_id,),
    )
    conn.commit()
    conn.close()
    assert repo.get_research_job_status(job.job_id) == ResearchJobStatus.QUEUED
    repo.save_contract(leased)
    assert repo.get_research_job_status(job.job_id) == ResearchJobStatus.LEASED


def test_job_and_return_locators_reject_traversal_before_storage(tmp_path) -> None:
    _db, _repo, job = _job_fixture(tmp_path)
    with pytest.raises(ValueError, match="safe relative"):
        ResearchJob.model_validate(
            {**job.model_dump(mode="python"), "brief_artifact_locator": "../escape.md"}
        )


def test_automation_contract_and_migration_goldens_are_sealed() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "p1_automation_contract_golden.json").read_text(
            encoding="utf-8"
        )
    )
    migration = p1_research_automation_manifest()
    assert fixture == {
        "p1_automation_contract_schema_sha256": p1_automation_contract_schema_fingerprint(),
        "migration_id": migration["migration_id"],
        "migration_sql_sha256": migration["sql_sha256"],
    }


def test_automation_migration_is_repeatable_and_tamper_fails_closed(tmp_path) -> None:
    db = tmp_path / "migration.db"
    assert migrate(db) == migrate(db)
    manifest = p1_research_automation_manifest()
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT sql_sha256 FROM alpha_schema_migrations WHERE migration_id=?",
        (P1_RESEARCH_AUTOMATION_MIGRATION_ID,),
    ).fetchone()[0] == manifest["sql_sha256"]
    conn.execute(
        "UPDATE alpha_schema_migrations SET sql_sha256=? WHERE migration_id=?",
        ("f" * 64, P1_RESEARCH_AUTOMATION_MIGRATION_ID),
    )
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="research automation"):
        migrate(db)
