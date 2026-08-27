"""P1-A03 adapter boundary tests: offline, deterministic, and fixture-only."""

from datetime import timedelta
import json
from pathlib import Path
import sqlite3

import pytest

from src.polymarket_alpha.contracts import BlindResearchPacket, RuleContract
from src.polymarket_alpha.pilot import run_offline_fixture_pilot
from src.polymarket_alpha.research import build_research_brief
from src.polymarket_alpha.research.automation import (
    BlindWorkOrderLeakError,
    DeterministicFakeExecutor,
    FakeExecutorPolicyError,
    ProviderReturnPayload,
    ResearchBindingError,
    ResearchReturnError,
    ResearchReturnLateError,
    adapt_provider_return,
    build_research_job,
    lease_research_job,
)
from src.polymarket_alpha.storage import AlphaRepository


def _one(db: Path, name: str, model):
    conn = sqlite3.connect(db)
    try:
        raw = conn.execute(
            "SELECT canonical_json FROM alpha_contract_record WHERE contract_type=?", (name,)
        ).fetchone()[0]
    finally:
        conn.close()
    return model.model_validate(json.loads(raw))


def _fixture(tmp_path: Path):
    db = tmp_path / "alpha.db"
    repo = AlphaRepository(db)
    run_offline_fixture_pilot(repo)
    packet = _one(db, "BlindResearchPacket", BlindResearchPacket)
    rule = _one(db, "RuleContract", RuleContract)
    created = packet.created_at + timedelta(minutes=1)
    brief = build_research_brief(packet, created_at=created).canonical_bytes()
    job = build_research_job(
        packet=packet, brief_bytes=brief, brief_artifact_locator="research/briefs/blind.json",
        rule_contract=rule, provider_policy_id="fixture-provider-v1",
        source_policy_id="fixture-sources-v1", max_attempts=2,
        available_at=created + timedelta(minutes=1), expires_at=created + timedelta(hours=1),
        run_id="p1-a03-test", created_at=created,
    )
    grant = lease_research_job(
        job=job, attempt_number=1, worker_id="fixture-worker", leased_at=job.available_at,
        lease_duration=timedelta(minutes=5), run_id="p1-a03-test",
    )
    return packet, brief, job, grant


def test_build_lease_fake_return_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    _packet, _brief, job, grant = _fixture(tmp_path)
    fake = DeterministicFakeExecutor("fixture-provider-v1", "fixture-sources-v1")
    payload = fake.execute(
        work_order=grant.work_order, provider_draft_bytes=b'{"draft":"fixture"}',
        source_bytes={"official": b"fixture source"},
    )
    returned_at = grant.attempt.leased_at + timedelta(seconds=1)
    first = adapt_provider_return(
        artifact_root=tmp_path, job=job, attempt=grant.attempt, work_order=grant.work_order,
        payload=payload, received_at=returned_at, run_id="p1-a03-test",
    )
    second = adapt_provider_return(
        artifact_root=tmp_path, job=job, attempt=grant.attempt, work_order=grant.work_order,
        payload=payload, received_at=returned_at, run_id="p1-a03-test",
    )
    assert first == second
    assert first.receipt.returned_bytes_sha256
    assert (tmp_path / first.receipt.returned_artifact_locator).read_bytes() == payload.provider_draft_bytes
    assert first.source_artifacts["official"].bytes_sha256
    assert (tmp_path / first.manifest_locator).is_file()

    with pytest.raises(ResearchReturnError, match="immutable provider return conflict"):
        adapt_provider_return(
            artifact_root=tmp_path,
            job=job,
            attempt=grant.attempt,
            work_order=grant.work_order,
            payload=fake.execute(
                work_order=grant.work_order,
                provider_draft_bytes=b'{"draft":"changed"}',
                source_bytes={"official": b"fixture source"},
            ),
            received_at=returned_at,
            run_id="p1-a03-test",
        )


def test_blind_brief_injection_and_return_binding_or_timeout_fail_closed(tmp_path: Path) -> None:
    packet, brief, job, grant = _fixture(tmp_path)
    injected = brief.replace(b'"instructions"', b'"wallet_hint":"x","instructions"')
    with pytest.raises(BlindWorkOrderLeakError):
        build_research_job(
            packet=packet, brief_bytes=injected, brief_artifact_locator="research/briefs/blind.json",
            rule_contract=_one(tmp_path / "alpha.db", "RuleContract", RuleContract),
            provider_policy_id="fixture-provider-v1", source_policy_id="fixture-sources-v1",
            max_attempts=2, available_at=job.available_at, expires_at=job.expires_at,
            run_id="p1-a03-test", created_at=job.created_at,
        )
    payload = DeterministicFakeExecutor("fixture-provider-v1", "fixture-sources-v1").execute(
        work_order=grant.work_order, provider_draft_bytes=b"{}", source_bytes={"a": b"x"},
    )
    with pytest.raises(ResearchReturnLateError):
        adapt_provider_return(
            artifact_root=tmp_path, job=job, attempt=grant.attempt, work_order=grant.work_order,
            payload=payload, received_at=grant.attempt.lease_expires_at + timedelta(seconds=1),
            run_id="p1-a03-test",
        )
    with pytest.raises(FakeExecutorPolicyError):
        DeterministicFakeExecutor("other", "fixture-sources-v1").execute(
            work_order=grant.work_order, provider_draft_bytes=b"{}", source_bytes={},
        )
    bad_order = grant.work_order.model_copy(update={"job_sha256": "0" * 64})
    with pytest.raises(ResearchBindingError):
        adapt_provider_return(
            artifact_root=tmp_path, job=job, attempt=grant.attempt, work_order=bad_order,
            payload=payload, received_at=grant.attempt.leased_at, run_id="p1-a03-test",
        )


def test_different_bytes_at_same_explicit_locator_conflict(tmp_path: Path) -> None:
    _packet, _brief, job, grant = _fixture(tmp_path)
    fake = DeterministicFakeExecutor("fixture-provider-v1", "fixture-sources-v1")
    at = grant.attempt.leased_at + timedelta(seconds=1)
    adapt_provider_return(
        artifact_root=tmp_path, job=job, attempt=grant.attempt, work_order=grant.work_order,
        payload=fake.execute(work_order=grant.work_order, provider_draft_bytes=b"one", source_bytes={}),
        received_at=at, run_id="p1-a03-test", returned_artifact_locator="returns/fixed.json",
    )
    with pytest.raises(ResearchReturnError, match="immutable provider return conflict"):
        adapt_provider_return(
            artifact_root=tmp_path, job=job, attempt=grant.attempt, work_order=grant.work_order,
            payload=fake.execute(work_order=grant.work_order, provider_draft_bytes=b"two", source_bytes={}),
            received_at=at, run_id="p1-a03-test", returned_artifact_locator="returns/fixed.json",
        )


def test_lease_number_and_return_sources_fail_before_any_artifact_write(tmp_path: Path) -> None:
    _packet, _brief, job, grant = _fixture(tmp_path)
    with pytest.raises(Exception, match="attempt one"):
        lease_research_job(
            job=job,
            attempt_number=2,
            worker_id="fixture-worker",
            leased_at=job.available_at,
            lease_duration=timedelta(minutes=1),
            run_id="bad-lease",
        )
    payload = ProviderReturnPayload(
        provider_draft_bytes=b"draft", source_bytes={"../escape": b"x"}
    )
    with pytest.raises(ResearchReturnError, match="safe names"):
        adapt_provider_return(
            artifact_root=tmp_path,
            job=job,
            attempt=grant.attempt,
            work_order=grant.work_order,
            payload=payload,
            received_at=grant.attempt.leased_at,
            run_id="bad-source",
        )
    assert not (tmp_path / "_sealed/research_returns").exists()


def test_fake_executor_rejects_non_mapping_sources(tmp_path: Path) -> None:
    _packet, _brief, _job, grant = _fixture(tmp_path)
    with pytest.raises(FakeExecutorPolicyError, match="mapping"):
        DeterministicFakeExecutor("fixture-provider-v1", "fixture-sources-v1").execute(
            work_order=grant.work_order,
            provider_draft_bytes=b"{}",
            source_bytes=[],  # type: ignore[arg-type]
        )
