"""P1-A05 synthetic scheduler, budget, dedupe and retry tests."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from src.polymarket_alpha.contracts import ResearchJobStatus
from src.polymarket_alpha.research import (
    ResearchScheduleRequest,
    ScheduleDisposition,
    SyntheticSchedulerPolicy,
    lease_research_retry,
    record_research_attempt_failure,
    run_synthetic_schedule_tick,
)
from src.polymarket_alpha.research.automation import ResearchLeaseError
from src.polymarket_alpha.security import audit_source_tree

from test_research_orchestrator_p1_a04 import _setup


def _policy(**changes) -> SyntheticSchedulerPolicy:
    values = dict(
        scheduler_id="alpha-offline-synthetic",
        policy_version="v1",
        enabled=True,
        max_jobs_per_tick=2,
        max_jobs_per_day=3,
        max_estimated_artifact_bytes_per_day=10_000,
        max_attempts=2,
        job_ttl=timedelta(hours=1),
        lease_duration=timedelta(minutes=5),
        dedupe_window=timedelta(minutes=30),
        max_clock_skew=timedelta(seconds=5),
    )
    values.update(changes)
    return SyntheticSchedulerPolicy(**values)


def _request(packet, rule, now, **changes) -> ResearchScheduleRequest:
    values = dict(
        packet=packet,
        rule_contract=rule,
        due_at=now,
        candidate_fresh_until=now + timedelta(hours=1),
        priority=10,
        estimated_artifact_bytes=1_000,
    )
    values.update(changes)
    return ResearchScheduleRequest(**values)


def test_tick_is_replayable_and_later_tick_dedupes(tmp_path: Path) -> None:
    repository, packet, rule, _unused = _setup(tmp_path)
    # Use a distinct clock/policy so the scheduler owns a different job identity
    # from the direct orchestrator fixture created by _setup.
    now = packet.created_at + timedelta(hours=2)
    policy = _policy()
    request = _request(packet, rule, now)
    first = run_synthetic_schedule_tick(
        repository=repository,
        artifact_root=tmp_path,
        requests=(request,),
        policy=policy,
        scheduled_for=now,
        observed_at=now,
        prior_receipts=(),
        provider_policy_id="scheduler-fixture-provider",
        source_policy_id="scheduler-fixture-sources",
        worker_id="scheduler-worker",
        run_id="p1-a05-run",
    )
    second = run_synthetic_schedule_tick(
        repository=repository,
        artifact_root=tmp_path,
        requests=(request,),
        policy=policy,
        scheduled_for=now,
        observed_at=now,
        prior_receipts=(first.receipt,),
        provider_policy_id="scheduler-fixture-provider",
        source_policy_id="scheduler-fixture-sources",
        worker_id="scheduler-worker",
        run_id="p1-a05-run",
    )
    assert second == first
    assert first.receipt.items[0].disposition == ScheduleDisposition.LEASED
    assert repository.get_research_job_status(first.prepared[0].job.job_id) == ResearchJobStatus.LEASED

    later = run_synthetic_schedule_tick(
        repository=repository,
        artifact_root=tmp_path,
        requests=(request,),
        policy=policy,
        scheduled_for=now + timedelta(minutes=10),
        observed_at=now + timedelta(minutes=10),
        prior_receipts=(first.receipt,),
        provider_policy_id="scheduler-fixture-provider",
        source_policy_id="scheduler-fixture-sources",
        worker_id="scheduler-worker",
        run_id="p1-a05-later",
    )
    assert later.receipt.items[0].disposition == ScheduleDisposition.DEDUPED
    assert not later.prepared


def test_not_due_duplicate_does_not_block_due_request_for_same_packet(tmp_path: Path) -> None:
    repository, packet, rule, _unused = _setup(tmp_path)
    now = packet.created_at + timedelta(hours=2)
    future = _request(
        packet,
        rule,
        now,
        due_at=now + timedelta(minutes=1),
        candidate_fresh_until=now + timedelta(hours=1),
        priority=20,
    )
    due = _request(packet, rule, now, priority=10)
    result = run_synthetic_schedule_tick(
        repository=repository,
        artifact_root=tmp_path,
        requests=(future, due),
        policy=_policy(),
        scheduled_for=now,
        observed_at=now,
        prior_receipts=(),
        provider_policy_id="scheduler-fixture-provider",
        source_policy_id="scheduler-fixture-sources",
        worker_id="scheduler-worker",
        run_id="p1-a05-due-selection",
    )
    assert tuple(item.disposition for item in result.receipt.items) == (
        ScheduleDisposition.NOT_DUE,
        ScheduleDisposition.LEASED,
    )
    assert len(result.prepared) == 1


@pytest.mark.parametrize(
    ("policy_changes", "request_changes", "observed_delta", "expected"),
    [
        ({"enabled": False}, {}, timedelta(0), ScheduleDisposition.DISABLED),
        ({}, {"due_at": None}, timedelta(0), ScheduleDisposition.NOT_DUE),
        ({}, {"candidate_fresh_until": None}, timedelta(0), ScheduleDisposition.STALE),
        ({}, {}, timedelta(minutes=1), ScheduleDisposition.CLOCK_SKEW),
        ({"max_jobs_per_tick": 0}, {}, timedelta(0), ScheduleDisposition.RUN_BUDGET_EXHAUSTED),
        ({"max_jobs_per_day": 0}, {}, timedelta(0), ScheduleDisposition.DAY_JOB_BUDGET_EXHAUSTED),
        ({"max_estimated_artifact_bytes_per_day": 999}, {}, timedelta(0), ScheduleDisposition.DAY_BYTE_BUDGET_EXHAUSTED),
    ],
)
def test_disable_clock_staleness_and_budgets_skip_without_job(
    tmp_path: Path, policy_changes, request_changes, observed_delta, expected
) -> None:
    repository, packet, rule, _unused = _setup(tmp_path)
    now = packet.created_at + timedelta(hours=2)
    if request_changes.get("due_at") is None and "due_at" in request_changes:
        request_changes["due_at"] = now + timedelta(minutes=1)
    if (
        request_changes.get("candidate_fresh_until") is None
        and "candidate_fresh_until" in request_changes
    ):
        request_changes["candidate_fresh_until"] = now - timedelta(seconds=1)
        request_changes["due_at"] = now - timedelta(minutes=1)
    result = run_synthetic_schedule_tick(
        repository=repository,
        artifact_root=tmp_path,
        requests=(_request(packet, rule, now, **request_changes),),
        policy=_policy(**policy_changes),
        scheduled_for=now,
        observed_at=now + observed_delta,
        prior_receipts=(),
        provider_policy_id="scheduler-fixture-provider",
        source_policy_id="scheduler-fixture-sources",
        worker_id="scheduler-worker",
        run_id=f"p1-a05-{expected.value.lower()}",
    )
    assert result.receipt.items[0].disposition == expected
    assert not result.prepared


def test_partial_provider_failure_retries_once_then_exhausts(tmp_path: Path) -> None:
    repository, packet, rule, _unused = _setup(tmp_path)
    now = packet.created_at + timedelta(hours=2)
    run = run_synthetic_schedule_tick(
        repository=repository,
        artifact_root=tmp_path,
        requests=(_request(packet, rule, now),),
        policy=_policy(max_attempts=2),
        scheduled_for=now,
        observed_at=now,
        prior_receipts=(),
        provider_policy_id="scheduler-fixture-provider",
        source_policy_id="scheduler-fixture-sources",
        worker_id="scheduler-worker",
        run_id="p1-a05-retry",
    )
    first = run.prepared[0]
    _receipt1, transition1 = record_research_attempt_failure(
        repository=repository,
        prepared=first,
        failed_at=first.lease.attempt.leased_at + timedelta(seconds=1),
        failure_code="FIXTURE_PROVIDER_FAILURE",
        run_id="p1-a05-retry",
    )
    assert transition1.to_status == ResearchJobStatus.RETRY_PENDING
    second = lease_research_retry(
        repository=repository,
        job=first.job,
        attempt_number=2,
        worker_id="scheduler-worker",
        leased_at=transition1.effective_at + timedelta(seconds=1),
        lease_duration=timedelta(minutes=5),
        run_id="p1-a05-retry",
    )
    _receipt2, transition2 = record_research_attempt_failure(
        repository=repository,
        prepared=second,
        failed_at=second.lease.attempt.leased_at + timedelta(seconds=1),
        failure_code="FIXTURE_PROVIDER_FAILURE",
        run_id="p1-a05-retry",
    )
    assert transition2.to_status == ResearchJobStatus.FAILED
    assert repository.get_research_job_status(first.job.job_id) == ResearchJobStatus.FAILED


def test_retry_after_job_ttl_fails_closed(tmp_path: Path) -> None:
    repository, packet, rule, _unused = _setup(tmp_path)
    now = packet.created_at + timedelta(hours=2)
    run = run_synthetic_schedule_tick(
        repository=repository,
        artifact_root=tmp_path,
        requests=(_request(packet, rule, now),),
        policy=_policy(job_ttl=timedelta(minutes=2), lease_duration=timedelta(minutes=1)),
        scheduled_for=now,
        observed_at=now,
        prior_receipts=(),
        provider_policy_id="scheduler-fixture-provider",
        source_policy_id="scheduler-fixture-sources",
        worker_id="scheduler-worker",
        run_id="p1-a05-ttl",
    )
    first = run.prepared[0]
    _receipt, transition = record_research_attempt_failure(
        repository=repository,
        prepared=first,
        failed_at=first.job.expires_at,
        failure_code="TTL_FAILURE",
        run_id="p1-a05-ttl",
    )
    assert transition.reason.value == "JOB_TTL_EXPIRED"
    assert transition.to_status == ResearchJobStatus.FAILED
    assert repository.get_research_job_status(first.job.job_id) == ResearchJobStatus.FAILED
    with pytest.raises(ResearchLeaseError, match="availability window"):
        lease_research_retry(
            repository=repository,
            job=first.job,
            attempt_number=2,
            worker_id="scheduler-worker",
            leased_at=first.job.expires_at,
            lease_duration=timedelta(minutes=1),
            run_id="p1-a05-ttl",
        )


def test_scheduler_has_no_network_daemon_or_execution_capability() -> None:
    audit = audit_source_tree("src/polymarket_alpha/research/scheduler.py")
    assert audit.passed, audit.findings
