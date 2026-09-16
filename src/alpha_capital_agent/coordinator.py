"""Caller-driven ACA orchestration for offline shadow runs.

The coordinator joins pure decision functions with the append-only repository.
It deliberately contains no loop, clock, network client, account adapter, or
execution capability; an approved external owner must supply each frozen tick.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from src.polymarket_alpha.contracts import MarketSnapshot
from src.polymarket_alpha.contracts.base import ensure_utc

from .allocator import CapitalPlan, allocate_capital
from .capital import (
    AccountSnapshotSeal,
    CapitalPolicy,
    OpportunityRecord,
    PositionExposure,
    ReplacementHistory,
)
from .storage import (
    CapitalAgentRepository,
    DurableCadenceWorkItem,
    DurableEvaluationWorkItem,
)
from .scheduler import (
    IncrementalScanResult,
    KeysetPage,
    ScanLane,
    UniverseScanCursor,
    UniverseScanPolicy,
    run_keyset_scan,
)
from .universe import (
    AdmissionTrigger,
    EligibilityEvaluation,
    EligibilityHistory,
    EligibilityHistoryCheckpoint,
    EligibilityPolicy,
    EligibilityTrigger,
    MarketAdmissionEpisode,
    MarketabilityFacts,
    UniverseState,
    create_admission_episode,
    create_eligibility_history_checkpoint,
    evaluate_eligibility,
)


@dataclass(frozen=True, slots=True)
class UniverseStepReceipt:
    evaluation: EligibilityEvaluation
    history_checkpoint: EligibilityHistoryCheckpoint
    admission_episode: MarketAdmissionEpisode | None
    stored_sha256: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapitalStepReceipt:
    plan: CapitalPlan
    stored_sha256: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScanStepReceipt:
    result: IncrementalScanResult
    stored_sha256: tuple[str, ...]


def run_and_persist_scan(
    *,
    repository: CapitalAgentRepository,
    fetch_page: Callable[[str | None, int], KeysetPage],
    lane: ScanLane,
    policy: UniverseScanPolicy,
    prior_cursor: UniverseScanCursor | None = None,
    scheduled_for: datetime,
    started_at: datetime,
    completed_at: datetime,
    run_id: str,
    cadence_work: DurableCadenceWorkItem | None = None,
    cadence_owner: str | None = None,
    lease_checked_at: datetime | None = None,
) -> ScanStepReceipt:
    """Run one scan; successful selected work delays cursor publication until ack."""

    cadence_values = (cadence_work, cadence_owner, lease_checked_at)
    if any(item is None for item in cadence_values) and any(
        item is not None for item in cadence_values
    ):
        raise ValueError(
            "cadence work, owner and independent lease clock must be supplied together"
        )
    if cadence_work is not None:
        assert cadence_owner is not None
        assert lease_checked_at is not None
        lease_checked_at = ensure_utc(lease_checked_at)
        if cadence_work.owner != cadence_owner.strip():
            raise ValueError("cadence work is leased by another owner")
        if lease_checked_at < ensure_utc(completed_at):
            raise ValueError("cadence lease clock cannot predate scan completion")
        if (
            cadence_work.order.lane != lane
            or cadence_work.order.scan_policy_id != policy.scan_policy_id
            or cadence_work.order.scan_policy_sha256 != policy.canonical_sha256
        ):
            raise ValueError("cadence work does not bind lane/policy")
    effective_prior = prior_cursor if prior_cursor is not None else repository.latest_cursor(lane)

    result = run_keyset_scan(
        fetch_page=fetch_page,
        lane=lane,
        policy=policy,
        prior_cursor=effective_prior,
        scheduled_for=scheduled_for,
        started_at=started_at,
        completed_at=completed_at,
        run_id=run_id,
    )
    stored = ()
    if result.receipt.coverage_complete:
        stored = repository.persist_successful_scan(
            policy,
            result.cursor,
            result.receipt,
            result.selected_items,
            cadence_work_id=None if cadence_work is None else cadence_work.work_id,
            cadence_owner=cadence_owner,
            cadence_lease_checked_at=lease_checked_at,
        )
    else:
        # Failed/bounded attempts remain auditable append-only facts, but the
        # operational committed-cursor projection is intentionally untouched.
        stored = repository.save_contracts_atomic(
            (policy, result.cursor, result.receipt)
        )
        if cadence_work is not None:
            assert cadence_owner is not None
            assert lease_checked_at is not None
            repository.fail_cadence_work(
                work_id=cadence_work.work_id,
                owner=cadence_owner,
                now=lease_checked_at,
                error=result.receipt.termination.value,
            )
    return ScanStepReceipt(result=result, stored_sha256=stored)


def evaluate_and_persist_market(
    *,
    repository: CapitalAgentRepository,
    snapshot: MarketSnapshot,
    facts: MarketabilityFacts,
    policy: EligibilityPolicy,
    history: EligibilityHistory | None = None,
    trigger: EligibilityTrigger,
    run_id: str,
    held_position: bool = False,
    admission_trigger: AdmissionTrigger | None = None,
    prior_candidate_id: str | None = None,
    claimed_work: DurableEvaluationWorkItem | None = None,
    claimed_owner: str | None = None,
    lease_checked_at: datetime | None = None,
) -> UniverseStepReceipt:
    """Evaluate one frozen tick and atomically persist its complete lineage."""

    claimed_values = (claimed_work, claimed_owner, lease_checked_at)
    if any(item is None for item in claimed_values) and any(
        item is not None for item in claimed_values
    ):
        raise ValueError(
            "claimed work, owner and independent lease clock must be supplied together"
        )
    if claimed_work is not None:
        assert claimed_owner is not None
        assert lease_checked_at is not None
        lease_checked_at = ensure_utc(lease_checked_at)
        if history is not None:
            raise ValueError("claimed evaluation must use repository history")
        if claimed_work.owner != claimed_owner.strip():
            raise ValueError("evaluation work is leased by another owner")
        if claimed_work.schedule.market_id != facts.market_id:
            raise ValueError("evaluation work market does not match frozen facts")
        if facts.observed_at < claimed_work.schedule.due_at:
            raise ValueError("claimed evaluation facts predate schedule due time")
        if lease_checked_at < facts.observed_at:
            raise ValueError("evaluation lease clock cannot predate frozen facts")
        if facts.stale or (
            facts.upstream_updated_at is not None
            and (facts.observed_at - facts.upstream_updated_at).total_seconds()
            > policy.max_facts_age_seconds
        ):
            raise ValueError("claimed evaluation requires fresh marketability facts")
    effective_history = history or repository.current_eligibility_history(facts.market_id) or EligibilityHistory()
    evaluation = evaluate_eligibility(
        snapshot=snapshot,
        facts=facts,
        policy=policy,
        history=effective_history,
        trigger=trigger,
        run_id=run_id,
        held_position=held_position,
    )
    selected_trigger = admission_trigger or _automatic_admission_trigger(
        evaluation=evaluation,
        prior_history=effective_history,
        held_position=held_position,
    )
    episode = None
    if selected_trigger is not None:
        episode = create_admission_episode(
            observation=evaluation.observation,
            policy=policy,
            trigger=selected_trigger,
            admitted_at=evaluation.observation.observed_at,
            run_id=run_id,
            prior_candidate_id=prior_candidate_id,
        )
    checkpoint = create_eligibility_history_checkpoint(
        observation=evaluation.observation,
        history=evaluation.history,
    )

    # The schedule is hash-bound to its cause observation, so the coordinator
    # persists that observation even when its marketability fingerprint was not
    # material. Exact replay remains idempotent in the append-only repository.
    contracts = [evaluation.observation, checkpoint]
    if evaluation.transition is not None:
        contracts.append(evaluation.transition)
    contracts.append(evaluation.next_evaluation)
    if episode is not None:
        contracts.append(episode)
    stored = repository.save_universe_evaluation_atomic(
        contracts=tuple(contracts),
        market_id=facts.market_id,
        history=evaluation.history,
        updated_at=facts.observed_at,
        claimed_work_id=None if claimed_work is None else claimed_work.work_id,
        claimed_owner=claimed_owner,
        claimed_lease_checked_at=lease_checked_at,
    )
    return UniverseStepReceipt(
        evaluation=evaluation,
        history_checkpoint=checkpoint,
        admission_episode=episode,
        stored_sha256=stored,
    )


def evaluate_and_persist_claimed_market(
    *,
    repository: CapitalAgentRepository,
    work: DurableEvaluationWorkItem,
    owner: str,
    lease_checked_at: datetime,
    snapshot: MarketSnapshot,
    facts: MarketabilityFacts,
    policy: EligibilityPolicy,
    run_id: str,
    held_position: bool = False,
    admission_trigger: AdmissionTrigger | None = None,
    prior_candidate_id: str | None = None,
) -> UniverseStepReceipt:
    """Process one leased due schedule and ACK it with the new observation."""

    return evaluate_and_persist_market(
        repository=repository,
        snapshot=snapshot,
        facts=facts,
        policy=policy,
        history=None,
        trigger=EligibilityTrigger.SCHEDULED_REEVALUATION,
        run_id=run_id,
        held_position=held_position,
        admission_trigger=admission_trigger,
        prior_candidate_id=prior_candidate_id,
        claimed_work=work,
        claimed_owner=owner,
        lease_checked_at=lease_checked_at,
    )


def _automatic_admission_trigger(
    *,
    evaluation: EligibilityEvaluation,
    prior_history: EligibilityHistory,
    held_position: bool,
) -> AdmissionTrigger | None:
    if held_position and prior_history.state is UniverseState.DISCOVERED:
        return AdmissionTrigger.HELD_POSITION_BOOTSTRAP
    transition = evaluation.transition
    if transition is None or transition.new_state is not UniverseState.ELIGIBLE_UNRESEARCHED:
        return None
    if transition.prior_state is UniverseState.DISCOVERED:
        return AdmissionTrigger.FIRST_DISCOVERY
    return AdmissionTrigger.MARKETABILITY_CROSSOVER


def build_and_persist_capital_plan(
    *,
    repository: CapitalAgentRepository,
    snapshot: AccountSnapshotSeal,
    positions: tuple[PositionExposure, ...] | list[PositionExposure],
    opportunities: tuple[OpportunityRecord, ...] | list[OpportunityRecord],
    policy: CapitalPolicy,
    histories: tuple[ReplacementHistory, ...] | list[ReplacementHistory] = (),
    run_id: str = "shadow",
) -> CapitalStepReceipt:
    plan = allocate_capital(
        snapshot,
        positions,
        opportunities,
        policy,
        histories=histories,
        run_id=run_id,
    )
    stored = repository.save_contracts_atomic((policy, plan))
    return CapitalStepReceipt(plan=plan, stored_sha256=stored)
