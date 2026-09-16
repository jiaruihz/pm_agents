"""Pure P1 resolution-head selection and isolated prediction backfill planning.

This module deliberately has no repository, transport, process, filesystem, or
network dependency.  It turns already-captured immutable contracts into a
replayable plan; persistence remains the storage owner's responsibility.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from pydantic import Field, field_validator, model_validator

from ..contracts import (
    AlphaContract,
    CommonEnvelope,
    MarketResolution,
    OrderbookSnapshot,
    PredictionRecord,
    PredictionResolutionLink,
    PredictionScore,
    ProbabilityEstimate,
    ResolutionAdjudicationStatus,
    ResolutionOutcome,
    ReviewDecision,
    RuleContract,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256
from .resolution import LearningResolutionError, build_prediction_resolution_link
from .scoring import LearningScoringError, ScoringPolicy, score_prediction


LEARNING_BACKFILL_VERSION = "p1_learning_backfill_v1"


class ResolutionSelectionError(ValueError):
    """Raised when correction history cannot yield one safe resolution head."""


class BackfillFailureCode(StrEnum):
    DUPLICATE_PREDICTION = "DUPLICATE_PREDICTION"
    INVALID_BUNDLE = "INVALID_BUNDLE"
    LINK_REJECTED = "LINK_REJECTED"
    SCORE_REJECTED = "SCORE_REJECTED"


class ResolutionSelectionPolicy(AlphaContract):
    """Versioned policy for choosing an append-only correction-chain head."""

    version: str
    require_final_head: bool = True

    @field_validator("version")
    @classmethod
    def version_is_present(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("selection policy version must not be blank")
        return value

    @property
    def policy_sha256(self) -> str:
        return content_sha256(
            {
                "resolution_selection_policy_version": self.version,
                "require_final_head": self.require_final_head,
            }
        )


class ResolutionReference(AlphaContract):
    resolution_id: str
    resolution_sha256: str

    @field_validator("resolution_id")
    @classmethod
    def id_is_present(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("resolution_id must not be blank")
        return value

    @field_validator("resolution_sha256")
    @classmethod
    def hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)


class ResolutionSelectionReceipt(CommonEnvelope):
    selection_id: str
    market_id: str
    condition_id: str | None = None
    rule_hash: str
    contract_revision_id: str
    selected_at: datetime
    resolution_references: tuple[ResolutionReference, ...]
    selected_resolution_id: str
    selected_resolution_sha256: str
    selected_resolution: MarketResolution
    selected_outcome: ResolutionOutcome
    selected_adjudication_status: ResolutionAdjudicationStatus
    selection_policy_version: str
    require_final_head: bool
    selection_policy_sha256: str

    @field_validator(
        "rule_hash", "selected_resolution_sha256", "selection_policy_sha256"
    )
    @classmethod
    def hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator(
        "selection_id", "market_id", "contract_revision_id",
        "selected_resolution_id", "selection_policy_version",
    )
    @classmethod
    def text_is_present(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("selection receipt text fields must not be blank")
        return value

    @field_validator("selected_at")
    @classmethod
    def selected_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def receipt_is_consistent(self) -> "ResolutionSelectionReceipt":
        if self.selection_id != self.record_id:
            raise ValueError("selection_id must equal record_id")
        if not self.resolution_references:
            raise ValueError("resolution selection requires references")
        if tuple(sorted(ref.resolution_id for ref in self.resolution_references)) != tuple(
            ref.resolution_id for ref in self.resolution_references
        ):
            raise ValueError("resolution references must be sorted by resolution_id")
        if len({ref.resolution_id for ref in self.resolution_references}) != len(self.resolution_references):
            raise ValueError("resolution references must be unique")
        if (
            self.selected_resolution.record_id != self.selected_resolution_id
            or self.selected_resolution.canonical_sha256 != self.selected_resolution_sha256
        ):
            raise ValueError("selected resolution id/hash must bind the embedded head")
        head = self.selected_resolution
        if (
            self.market_id != head.market_id
            or self.condition_id != head.condition_id
            or self.rule_hash != head.rule_hash
            or self.contract_revision_id != head.contract_revision_id
            or self.selected_outcome != head.outcome
            or self.selected_adjudication_status != head.adjudication_status
            or self.selected_at < head.created_at
        ):
            raise ValueError("selection receipt fields must reproduce the embedded head")
        selected_refs = tuple(
            ref for ref in self.resolution_references
            if ref.resolution_id == self.selected_resolution_id
        )
        if len(selected_refs) != 1 or selected_refs[0].resolution_sha256 != self.selected_resolution_sha256:
            raise ValueError("selected head must have one exact resolution reference")
        expected_policy = content_sha256(
            {
                "resolution_selection_policy_version": self.selection_policy_version,
                "require_final_head": self.require_final_head,
            }
        )
        if self.selection_policy_sha256 != expected_policy:
            raise ValueError("selection policy hash mismatch")
        expected_id = stable_record_id(
            "resolution_selection",
            self.market_id,
            tuple((ref.resolution_id, ref.resolution_sha256) for ref in self.resolution_references),
            self.selected_resolution_id,
            self.selected_resolution_sha256,
            self.selection_policy_version,
            self.selection_policy_sha256,
            self.selected_at,
        )
        if self.selection_id != expected_id:
            raise ValueError("selection id is not deterministic")
        return self


class PredictionBackfillInput(AlphaContract):
    """One complete, frozen P0 lineage required to create P1 facts."""

    prediction: PredictionRecord
    decision: ReviewDecision
    probability_estimate: ProbabilityEstimate
    rule_contract: RuleContract
    orderbook: OrderbookSnapshot
    market_type: str
    fee_amount: Decimal = Field(default=Decimal("0"), ge=0)
    fee_model_version: str = "no_fee_v1"

    @field_validator("market_type", "fee_model_version")
    @classmethod
    def text_is_present(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("backfill input text fields must not be blank")
        return value


class PredictionBackfillSuccess(AlphaContract):
    prediction_id: str
    link: PredictionResolutionLink
    score: PredictionScore

    @model_validator(mode="after")
    def lineage_is_consistent(self) -> "PredictionBackfillSuccess":
        if self.prediction_id != self.link.prediction_id or self.prediction_id != self.score.prediction_id:
            raise ValueError("backfill success prediction ids must agree")
        if self.link.record_id != self.score.link_id or self.link.canonical_sha256 != self.score.link_sha256:
            raise ValueError("backfill success link/score hashes must agree")
        if self.link.resolution_id != self.score.resolution_id:
            raise ValueError("backfill success link/score resolution ids must agree")
        return self


class PredictionBackfillFailure(AlphaContract):
    prediction_id: str
    code: BackfillFailureCode

    @field_validator("prediction_id")
    @classmethod
    def prediction_id_is_bounded(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 128:
            raise ValueError("prediction_id must be a bounded non-blank identifier")
        return value


class PredictionBackfillPlan(CommonEnvelope):
    plan_id: str
    selection_id: str
    selection_sha256: str
    successes: tuple[PredictionBackfillSuccess, ...]
    failures: tuple[PredictionBackfillFailure, ...]
    scoring_policy_version: str
    log_loss_epsilon: Decimal = Field(gt=0, lt=1)
    scoring_policy_sha256: str

    @field_validator("selection_sha256", "scoring_policy_sha256")
    @classmethod
    def hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("plan_id", "selection_id", "scoring_policy_version")
    @classmethod
    def text_is_present(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("backfill plan text fields must not be blank")
        return value

    @model_validator(mode="after")
    def plan_is_deterministically_ordered(self) -> "PredictionBackfillPlan":
        if self.plan_id != self.record_id:
            raise ValueError("plan_id must equal record_id")
        ids = tuple(item.prediction_id for item in self.successes)
        if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
            raise ValueError("backfill successes must have unique sorted prediction ids")
        failure_keys = tuple((item.prediction_id, item.code.value) for item in self.failures)
        if failure_keys != tuple(sorted(failure_keys)):
            raise ValueError("backfill failures must be sorted")
        if set(ids) & {item.prediction_id for item in self.failures}:
            raise ValueError("a prediction cannot both succeed and fail")
        expected_policy = content_sha256(
            {
                "scoring_policy_version": self.scoring_policy_version,
                "log_loss_epsilon": self.log_loss_epsilon,
            }
        )
        if self.scoring_policy_sha256 != expected_policy:
            raise ValueError("backfill scoring policy hash mismatch")
        expected_id = stable_record_id(
            "prediction_backfill_plan",
            self.selection_id,
            self.selection_sha256,
            tuple(
                (
                    item.link.record_id,
                    item.link.canonical_sha256,
                    item.score.record_id,
                    item.score.canonical_sha256,
                )
                for item in self.successes
            ),
            tuple((item.prediction_id, item.code.value) for item in self.failures),
            self.scoring_policy_version,
            self.scoring_policy_sha256,
            self.created_at,
        )
        if self.plan_id != expected_id:
            raise ValueError("backfill plan id is not deterministic")
        return self


def _frozen(value, cls, error_cls=ResolutionSelectionError):
    if not isinstance(value, cls):
        raise error_cls(f"expected {cls.__name__}")
    try:
        rebuilt = cls.model_validate(value.model_dump(mode="python"))
    except (TypeError, ValueError) as exc:
        raise error_cls(f"invalid frozen {cls.__name__}: {exc}") from exc
    original_hash = (
        value.canonical_sha256
        if isinstance(value, CommonEnvelope)
        else content_sha256(value)
    )
    rebuilt_hash = (
        rebuilt.canonical_sha256
        if isinstance(rebuilt, CommonEnvelope)
        else content_sha256(rebuilt)
    )
    if rebuilt_hash != original_hash:
        raise error_cls(f"{cls.__name__} canonical replay mismatch")
    return rebuilt


def select_resolution_head(
    resolutions: Iterable[MarketResolution], *, market_id: str,
    policy: ResolutionSelectionPolicy, selected_at: datetime, run_id: str,
) -> ResolutionSelectionReceipt:
    """Select exactly one correction-chain head, or reject the whole history."""

    market_id = market_id.strip()
    if not market_id:
        raise ResolutionSelectionError("market_id must not be blank")
    policy = _frozen(policy, ResolutionSelectionPolicy)
    selected_at = ensure_utc(selected_at)
    items = tuple(_frozen(item, MarketResolution) for item in resolutions)
    if not items:
        raise ResolutionSelectionError("resolution selection requires at least one resolution")
    by_id = {item.record_id: item for item in items}
    if len(by_id) != len(items):
        raise ResolutionSelectionError("resolution ids must be unique")
    first = items[0]
    identity = (
        first.market_id, first.condition_id, first.rule_hash, first.contract_revision_id,
        first.rule_contract_id, first.rule_contract_sha256,
    )
    if first.market_id != market_id:
        raise ResolutionSelectionError("resolution market does not match requested market")
    for item in items:
        if item.market_id != market_id or (
            item.market_id, item.condition_id, item.rule_hash, item.contract_revision_id,
            item.rule_contract_id, item.rule_contract_sha256,
        ) != identity:
            raise ResolutionSelectionError("resolution history has inconsistent market/condition/rule/revision")
        if item.created_at > selected_at:
            raise ResolutionSelectionError("resolution cannot be selected before it was created")

    parents: dict[str, str | None] = {}
    child_count = {item.record_id: 0 for item in items}
    for item in items:
        parent_id, parent_hash = item.supersedes_resolution_id, item.supersedes_resolution_sha256
        if parent_id is None:
            parents[item.record_id] = None
            continue
        parent = by_id.get(parent_id)
        if parent is None:
            raise ResolutionSelectionError("resolution supersedes a missing prior record")
        if parent.canonical_sha256 != parent_hash:
            raise ResolutionSelectionError("resolution supersedes hash does not match prior record")
        if parent.source_observed_at > item.source_observed_at or parent.created_at > item.created_at:
            raise ResolutionSelectionError("resolution correction clocks run backward")
        parents[item.record_id] = parent_id
        child_count[parent_id] += 1
    roots = tuple(record_id for record_id, parent in parents.items() if parent is None)
    if len(roots) != 1:
        raise ResolutionSelectionError("resolution history must have exactly one root")
    forks = tuple(record_id for record_id, count in child_count.items() if count > 1)
    if forks:
        raise ResolutionSelectionError("resolution history must not fork")
    # A finite chain with exactly one root and no fork has one head iff every
    # record is reachable from the root.  Explicit traversal makes cycle and
    # disconnected-chain failures deterministic and clear.
    visited: set[str] = set()
    current = roots[0]
    while current not in visited:
        visited.add(current)
        children = tuple(record_id for record_id, parent in parents.items() if parent == current)
        if not children:
            break
        current = children[0]
    if len(visited) != len(items):
        raise ResolutionSelectionError("resolution history contains a cycle or disconnected chain")
    head = by_id[current]
    if policy.require_final_head and head.adjudication_status == ResolutionAdjudicationStatus.PENDING_DISPUTE:
        raise ResolutionSelectionError("resolution head is PENDING_DISPUTE")
    references = tuple(
        ResolutionReference(resolution_id=item.record_id, resolution_sha256=item.canonical_sha256)
        for item in sorted(items, key=lambda item: item.record_id)
    )
    selection_id = stable_record_id(
        "resolution_selection", market_id, tuple((ref.resolution_id, ref.resolution_sha256) for ref in references),
        head.record_id, head.canonical_sha256, policy.version, policy.policy_sha256, selected_at,
    )
    return ResolutionSelectionReceipt(
        record_id=selection_id, selection_id=selection_id, run_id=run_id, created_at=selected_at,
        source="alpha_p1_resolution_selector", source_version=LEARNING_BACKFILL_VERSION,
        provenance=(), extensions={}, market_id=market_id, condition_id=head.condition_id,
        rule_hash=head.rule_hash, contract_revision_id=head.contract_revision_id,
        selected_at=selected_at, resolution_references=references,
        selected_resolution_id=head.record_id, selected_resolution_sha256=head.canonical_sha256,
        selected_resolution=head,
        selected_outcome=head.outcome, selected_adjudication_status=head.adjudication_status,
        selection_policy_version=policy.version, selection_policy_sha256=policy.policy_sha256,
        require_final_head=policy.require_final_head,
    )


def _safe_prediction_id(bundle: object, index: int) -> str:
    prediction = getattr(bundle, "prediction", None)
    value = getattr(prediction, "record_id", None)
    if isinstance(value, str) and value.strip() and len(value.strip()) <= 128:
        return value.strip()
    return f"invalid_prediction:{index:06d}"


def _failure(prediction_id: str, code: BackfillFailureCode) -> PredictionBackfillFailure:
    return PredictionBackfillFailure(prediction_id=prediction_id, code=code)


def plan_prediction_backfill(
    selection: ResolutionSelectionReceipt, bundles: Iterable[PredictionBackfillInput], *,
    scoring_policy: ScoringPolicy, planned_at: datetime, run_id: str,
) -> PredictionBackfillPlan:
    """Create independent link/score facts, isolating malformed prediction lineages."""

    selection = _frozen(selection, ResolutionSelectionReceipt)
    policy = _frozen(scoring_policy, ScoringPolicy, LearningScoringError)
    planned_at = ensure_utc(planned_at)
    if selection.created_at > planned_at or selection.selected_at > planned_at:
        raise ResolutionSelectionError("selection cannot be planned before it was created")
    selected = selection.selected_resolution
    if (
        selected.adjudication_status != ResolutionAdjudicationStatus.FINAL
        or selected.outcome not in {ResolutionOutcome.YES, ResolutionOutcome.NO}
    ):
        raise ResolutionSelectionError("selected resolution head must be FINAL YES or NO before scoring")
    raw = tuple(bundles)
    keyed = sorted(((_safe_prediction_id(item, index), index, item) for index, item in enumerate(raw)), key=lambda item: (item[0], item[1]))
    counts: dict[str, int] = {}
    for prediction_id, _, _ in keyed:
        counts[prediction_id] = counts.get(prediction_id, 0) + 1
    successes: list[PredictionBackfillSuccess] = []
    failures: list[PredictionBackfillFailure] = []
    for prediction_id, _, raw_bundle in keyed:
        if counts[prediction_id] > 1:
            failures.append(_failure(prediction_id, BackfillFailureCode.DUPLICATE_PREDICTION))
            continue
        try:
            bundle = _frozen(raw_bundle, PredictionBackfillInput, LearningResolutionError)
        except (LearningResolutionError, TypeError, ValueError):
            failures.append(_failure(prediction_id, BackfillFailureCode.INVALID_BUNDLE))
            continue
        try:
            resolution_ref = next(
                ref for ref in selection.resolution_references
                if ref.resolution_id == selection.selected_resolution_id
            )
            if resolution_ref.resolution_sha256 != selection.selected_resolution_sha256:
                raise ResolutionSelectionError("selection head reference hash mismatch")
            resolution = _frozen(selection.selected_resolution, MarketResolution)
            if resolution.market_id != bundle.prediction.market_id or resolution.rule_hash != bundle.rule_contract.rule_hash:
                raise ResolutionSelectionError("bundle resolution lineage mismatch")
            link = build_prediction_resolution_link(
                prediction=bundle.prediction, decision=bundle.decision,
                probability_estimate=bundle.probability_estimate, rule_contract=bundle.rule_contract,
                resolution=resolution, orderbook=bundle.orderbook, market_type=bundle.market_type,
                linked_at=planned_at, run_id=run_id, fee_amount=bundle.fee_amount,
                fee_model_version=bundle.fee_model_version,
            )
        except (LearningResolutionError, ResolutionSelectionError, StopIteration, TypeError, ValueError):
            failures.append(_failure(prediction_id, BackfillFailureCode.LINK_REJECTED))
            continue
        try:
            score = score_prediction(link=link, resolution=resolution, policy=policy, scored_at=planned_at, run_id=run_id)
        except (LearningScoringError, TypeError, ValueError):
            failures.append(_failure(prediction_id, BackfillFailureCode.SCORE_REJECTED))
            continue
        successes.append(PredictionBackfillSuccess(prediction_id=prediction_id, link=link, score=score))
    successes.sort(key=lambda item: item.prediction_id)
    failures.sort(key=lambda item: (item.prediction_id, item.code.value))
    plan_id = stable_record_id(
        "prediction_backfill_plan", selection.record_id, selection.canonical_sha256,
        tuple((item.link.record_id, item.link.canonical_sha256, item.score.record_id, item.score.canonical_sha256) for item in successes),
        tuple((item.prediction_id, item.code.value) for item in failures),
        policy.version, policy.policy_sha256, planned_at,
    )
    return PredictionBackfillPlan(
        record_id=plan_id, plan_id=plan_id, run_id=run_id, created_at=planned_at,
        source="alpha_p1_prediction_backfill", source_version=LEARNING_BACKFILL_VERSION,
        provenance=(), extensions={}, selection_id=selection.record_id,
        selection_sha256=selection.canonical_sha256, successes=tuple(successes),
        failures=tuple(failures), scoring_policy_version=policy.version,
        log_loss_epsilon=policy.probability_epsilon,
        scoring_policy_sha256=policy.policy_sha256,
    )


def contracts_for_backfill_persistence(
    selection: ResolutionSelectionReceipt,
    plan: PredictionBackfillPlan,
) -> tuple[CommonEnvelope, ...]:
    """Return one ordered atomic repository group without importing storage.

    The resolution's SourceArtifact, RuleContract, and any superseded prior
    resolution must already exist in the caller-owned repository. Exact replay
    is safe through the repository's normal immutable contract boundary.
    """

    selection = _frozen(selection, ResolutionSelectionReceipt)
    plan = _frozen(plan, PredictionBackfillPlan)
    if (
        plan.selection_id != selection.record_id
        or plan.selection_sha256 != selection.canonical_sha256
    ):
        raise ResolutionSelectionError("backfill plan is bound to another selection")
    facts: list[CommonEnvelope] = [selection.selected_resolution, selection]
    for success in plan.successes:
        if (
            success.link.resolution_id != selection.selected_resolution_id
            or success.link.resolution_sha256 != selection.selected_resolution_sha256
            or success.score.resolution_id != selection.selected_resolution_id
        ):
            raise ResolutionSelectionError(
                "backfill success is bound to another resolution selection"
            )
        facts.extend((success.link, success.score))
    facts.append(plan)
    record_ids = tuple(item.record_id for item in facts)
    if len(record_ids) != len(set(record_ids)):
        raise ResolutionSelectionError("backfill persistence group has duplicate record ids")
    return tuple(facts)
