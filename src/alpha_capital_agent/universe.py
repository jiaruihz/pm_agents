"""Deterministic market-universe eligibility and admission contracts.

The module is deliberately caller-driven and capability-free: it accepts
frozen Alpha market snapshots plus caller-supplied marketability facts and
returns immutable observations, transitions and admission episodes.  It does
not fetch, poll, sleep, start a daemon, inspect an account, or place orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from src.polymarket_alpha.contracts import (
    AlphaContract,
    CommonEnvelope,
    MarketSnapshot,
    MarketStatus,
    content_sha256,
    stable_record_id,
)
from src.polymarket_alpha.contracts.base import ensure_utc, validate_sha256


UNIVERSE_SOURCE = "alpha_capital_agent.universe"
UNIVERSE_VERSION = "aca_universe_v1"
_ONE = Decimal("1")
_ZERO = Decimal("0")


class UniverseState(StrEnum):
    DISCOVERED = "DISCOVERED"
    INELIGIBLE_DORMANT = "INELIGIBLE_DORMANT"
    NEAR_ELIGIBLE_WATCH = "NEAR_ELIGIBLE_WATCH"
    ELIGIBLE_UNRESEARCHED = "ELIGIBLE_UNRESEARCHED"
    RESEARCH_ADMITTED = "RESEARCH_ADMITTED"
    CANDIDATE_ACTIVE = "CANDIDATE_ACTIVE"
    CANDIDATE_STALE = "CANDIDATE_STALE"
    TERMINAL = "TERMINAL"


class EligibilityReasonCode(StrEnum):
    DATA_STALE = "DATA_STALE"
    INSUFFICIENT_EXECUTABLE_DEPTH = "INSUFFICIENT_EXECUTABLE_DEPTH"
    INSUFFICIENT_RULE_TEXT = "INSUFFICIENT_RULE_TEXT"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    LOW_VOLUME = "LOW_VOLUME"
    MISSING_EXECUTABLE_QUOTE = "MISSING_EXECUTABLE_QUOTE"
    MISSING_OR_NEAR_DEADLINE = "MISSING_OR_NEAR_DEADLINE"
    MISSING_PUBLIC_LINK = "MISSING_PUBLIC_LINK"
    NOT_ACTIVE_OPEN = "NOT_ACTIVE_OPEN"
    NOT_BINARY_PAIRED = "NOT_BINARY_PAIRED"
    NOT_ORDERBOOK_ACTIVE = "NOT_ORDERBOOK_ACTIVE"
    RESTRICTED = "RESTRICTED"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    ELIGIBLE = "ELIGIBLE"


class EligibilityTrigger(StrEnum):
    NEW_MARKET = "NEW_MARKET"
    DELTA_SCAN = "DELTA_SCAN"
    FULL_CENSUS = "FULL_CENSUS"
    DAILY_RECONCILIATION = "DAILY_RECONCILIATION"
    BOOK_EVENT = "BOOK_EVENT"
    LIFECYCLE_EVENT = "LIFECYCLE_EVENT"
    POLICY_CHANGE = "POLICY_CHANGE"
    HELD_POSITION = "HELD_POSITION"
    SCHEDULED_REEVALUATION = "SCHEDULED_REEVALUATION"


class EvaluationTier(StrEnum):
    REALTIME_HELD = "REALTIME_HELD"
    REALTIME_CANDIDATE = "REALTIME_CANDIDATE"
    NEAR_FIVE_MINUTE = "NEAR_FIVE_MINUTE"
    DORMANT_THIRTY_MINUTE = "DORMANT_THIRTY_MINUTE"
    STRUCTURAL_SIX_HOUR = "STRUCTURAL_SIX_HOUR"
    TERMINAL_DAILY = "TERMINAL_DAILY"


class AdmissionTrigger(StrEnum):
    FIRST_DISCOVERY = "FIRST_DISCOVERY"
    MARKETABILITY_CROSSOVER = "MARKETABILITY_CROSSOVER"
    RULE_REVISION = "RULE_REVISION"
    NEW_PUBLIC_EVIDENCE = "NEW_PUBLIC_EVIDENCE"
    PROBABILITY_TTL = "PROBABILITY_TTL"
    PRICE_DISPLACEMENT = "PRICE_DISPLACEMENT"
    HELD_POSITION_BOOTSTRAP = "HELD_POSITION_BOOTSTRAP"
    POLICY_VERSION_CHANGE = "POLICY_VERSION_CHANGE"


class EligibilityPolicy(CommonEnvelope):
    """Versioned deterministic pre-research eligibility policy."""

    policy_id: str
    policy_name: str
    min_liquidity_enter: Decimal = Field(ge=0)
    min_volume_enter: Decimal = Field(ge=0)
    max_spread_enter: Decimal = Field(gt=0, le=1)
    min_executable_depth_enter: Decimal = Field(default=_ZERO, ge=0)
    min_hours_to_deadline: int = Field(gt=0)
    min_question_chars: int = Field(gt=0)
    min_rule_text_chars: int = Field(gt=0)
    near_band_fraction: Decimal = Field(gt=0, lt=1)
    liquidity_exit_ratio: Decimal = Field(gt=0, le=1)
    volume_exit_ratio: Decimal = Field(gt=0, le=1)
    spread_exit_ratio: Decimal = Field(ge=1)
    entry_confirmations: int = Field(gt=0)
    confirmation_interval_seconds: int = Field(gt=0)
    entry_dwell_seconds: int = Field(gt=0)
    exit_confirmations: int = Field(ge=2)
    exit_confirmation_interval_seconds: int = Field(gt=0)
    near_interval_seconds: int = Field(gt=0)
    dormant_interval_seconds: int = Field(gt=0)
    structural_interval_seconds: int = Field(gt=0)
    terminal_interval_seconds: int = Field(gt=0)
    max_facts_age_seconds: int = Field(gt=0)
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @model_validator(mode="after")
    def policy_identity_and_intervals_hold(self) -> "EligibilityPolicy":
        if self.policy_id != self.record_id:
            raise ValueError("policy_id must equal record_id")
        if not self.record_id.startswith("eligibility_policy:"):
            raise ValueError("eligibility policy id must use eligibility_policy namespace")
        if self.near_interval_seconds > self.dormant_interval_seconds:
            raise ValueError("near interval cannot be slower than dormant interval")
        if self.dormant_interval_seconds > self.structural_interval_seconds:
            raise ValueError("dormant interval cannot be slower than structural interval")
        if self.entry_dwell_seconds < self.confirmation_interval_seconds:
            raise ValueError("entry dwell cannot be shorter than confirmation interval")
        if self.exit_confirmation_interval_seconds > self.near_interval_seconds:
            raise ValueError("exit confirmation cannot be slower than near watch cadence")
        return self


class MarketabilityFacts(AlphaContract):
    """Cheap marketability facts supplied by Gamma/the single book owner."""

    market_id: str
    snapshot_id: str
    snapshot_sha256: str
    observed_at: datetime
    upstream_updated_at: datetime | None = None
    accepting_orders: bool | None
    enable_order_book: bool | None
    binary_paired: bool
    public_link_available: bool
    restricted: bool | None = None
    best_bid: Decimal | None = Field(default=None, ge=0, le=1)
    best_ask: Decimal | None = Field(default=None, ge=0, le=1)
    spread: Decimal | None = Field(default=None, ge=0, le=1)
    liquidity: Decimal | None = Field(default=None, ge=0)
    volume: Decimal | None = Field(default=None, ge=0)
    executable_depth: Decimal | None = Field(default=None, ge=0)
    stale: bool = False
    source_artifact_ids: tuple[str, ...]

    @field_validator("snapshot_sha256")
    @classmethod
    def snapshot_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("observed_at", "upstream_updated_at")
    @classmethod
    def clocks_are_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @field_validator("market_id", "snapshot_id")
    @classmethod
    def identifiers_are_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("marketability identifiers must not be blank")
        return value

    @field_validator("source_artifact_ids")
    @classmethod
    def source_ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if not normalized or any(not item for item in normalized):
            raise ValueError("marketability facts require source artifact lineage")
        if normalized != tuple(sorted(set(normalized))):
            raise ValueError("source artifact ids must be unique and sorted")
        return normalized

    @model_validator(mode="after")
    def quote_is_internally_consistent(self) -> "MarketabilityFacts":
        if (
            self.upstream_updated_at is not None
            and self.upstream_updated_at > self.observed_at
        ):
            raise ValueError("upstream_updated_at cannot be after observed_at")
        if (
            self.best_bid is not None
            and self.best_ask is not None
            and self.best_bid <= self.best_ask
            and self.spread is not None
            and self.spread != self.best_ask - self.best_bid
        ):
            raise ValueError("spread must equal best_ask - best_bid")
        return self


class EligibilityHistory(AlphaContract):
    state: UniverseState = UniverseState.DISCOVERED
    consecutive_qualifying: int = Field(default=0, ge=0)
    first_qualifying_at: datetime | None = None
    last_confirmation_at: datetime | None = None
    consecutive_disqualifying: int = Field(default=0, ge=0)
    first_disqualifying_at: datetime | None = None
    last_disconfirmation_at: datetime | None = None
    last_observed_at: datetime | None = None
    last_marketability_fingerprint: str | None = None
    last_qualifies: bool = False

    @field_validator(
        "first_qualifying_at",
        "last_confirmation_at",
        "first_disqualifying_at",
        "last_disconfirmation_at",
        "last_observed_at",
    )
    @classmethod
    def history_clocks_are_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @field_validator("last_marketability_fingerprint")
    @classmethod
    def history_hash_is_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def qualifying_history_is_consistent(self) -> "EligibilityHistory":
        clocks = (self.first_qualifying_at, self.last_confirmation_at)
        if self.consecutive_qualifying == 0:
            if any(item is not None for item in clocks):
                raise ValueError("empty qualifying streak cannot retain qualifying clocks")
        else:
            if any(item is None for item in clocks) or not self.last_qualifies:
                raise ValueError("qualifying streak requires both clocks and last_qualifies")
            assert self.first_qualifying_at is not None
            assert self.last_confirmation_at is not None
            if self.last_confirmation_at < self.first_qualifying_at:
                raise ValueError("confirmation clock cannot precede first qualifying clock")
        disqualifying_clocks = (
            self.first_disqualifying_at,
            self.last_disconfirmation_at,
        )
        if self.consecutive_disqualifying == 0:
            if any(item is not None for item in disqualifying_clocks):
                raise ValueError(
                    "empty disqualifying streak cannot retain disqualifying clocks"
                )
        else:
            if any(item is None for item in disqualifying_clocks) or self.last_qualifies:
                raise ValueError(
                    "disqualifying streak requires both clocks and last_qualifies false"
                )
            assert self.first_disqualifying_at is not None
            assert self.last_disconfirmation_at is not None
            if self.last_disconfirmation_at < self.first_disqualifying_at:
                raise ValueError(
                    "disconfirmation clock cannot precede first disqualifying clock"
                )
        if self.consecutive_qualifying and self.consecutive_disqualifying:
            raise ValueError("qualifying and disqualifying streaks are mutually exclusive")
        if self.last_qualifies and self.consecutive_qualifying == 0:
            raise ValueError("last_qualifies requires a qualifying streak")
        return self


class MarketabilityObservation(CommonEnvelope):
    observation_id: str
    market_id: str
    snapshot_id: str
    snapshot_sha256: str
    policy_id: str
    policy_sha256: str
    trigger: EligibilityTrigger
    observed_at: datetime
    prior_state: UniverseState
    threshold_mode: Literal["ENTER", "STAY"]
    liquidity: Decimal | None = Field(default=None, ge=0)
    volume: Decimal | None = Field(default=None, ge=0)
    best_bid: Decimal | None = Field(default=None, ge=0, le=1)
    best_ask: Decimal | None = Field(default=None, ge=0, le=1)
    spread: Decimal | None = Field(default=None, ge=0, le=1)
    executable_depth: Decimal | None = Field(default=None, ge=0)
    thresholds: dict[str, Decimal]
    qualifies: bool
    near_eligible: bool
    reason_codes: tuple[EligibilityReasonCode, ...]
    marketability_fingerprint: str
    source_artifact_ids: tuple[str, ...]
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator(
        "snapshot_sha256", "policy_sha256", "marketability_fingerprint"
    )
    @classmethod
    def observation_hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("observed_at")
    @classmethod
    def observed_clock_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("reason_codes")
    @classmethod
    def reasons_are_canonical(
        cls, value: tuple[EligibilityReasonCode, ...]
    ) -> tuple[EligibilityReasonCode, ...]:
        if not value or value != tuple(sorted(set(value), key=lambda item: item.value)):
            raise ValueError("reason codes must be non-empty, unique and sorted")
        return value

    @model_validator(mode="after")
    def observation_identity_and_decision_hold(self) -> "MarketabilityObservation":
        if self.observation_id != self.record_id:
            raise ValueError("observation_id must equal record_id")
        if not self.record_id.startswith("marketability_observation:"):
            raise ValueError("observation id must use marketability_observation namespace")
        if self.qualifies != (self.reason_codes == (EligibilityReasonCode.ELIGIBLE,)):
            raise ValueError("qualifies must exactly match ELIGIBLE reason")
        if self.qualifies and self.near_eligible:
            raise ValueError("qualified observation cannot be near-ineligible")
        return self


class EligibilityHistoryCheckpoint(CommonEnvelope):
    """Append-only restart truth for one evaluated marketability observation."""

    checkpoint_id: str
    market_id: str
    observation_id: str
    observation_sha256: str
    observed_at: datetime
    history: EligibilityHistory
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("observation_sha256")
    @classmethod
    def checkpoint_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("market_id", "observation_id")
    @classmethod
    def checkpoint_identifiers_are_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("history checkpoint identifiers must not be blank")
        return value

    @field_validator("observed_at")
    @classmethod
    def checkpoint_clock_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def checkpoint_is_bound(self) -> "EligibilityHistoryCheckpoint":
        if self.checkpoint_id != self.record_id:
            raise ValueError("checkpoint_id must equal record_id")
        if not self.record_id.startswith("eligibility_history_checkpoint:"):
            raise ValueError(
                "history checkpoint id must use eligibility_history_checkpoint namespace"
            )
        if self.history.last_observed_at != self.observed_at:
            raise ValueError("history checkpoint clock must equal history last observation")
        return self


class EligibilityTransition(CommonEnvelope):
    transition_id: str
    market_id: str
    observation_id: str
    observation_sha256: str
    prior_state: UniverseState
    new_state: UniverseState
    trigger: EligibilityTrigger
    effective_at: datetime
    consecutive_qualifying: int = Field(ge=0)
    consecutive_disqualifying: int = Field(ge=0)
    reason_codes: tuple[EligibilityReasonCode, ...]
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("observation_sha256")
    @classmethod
    def observation_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("effective_at")
    @classmethod
    def transition_clock_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def transition_is_material_and_bound(self) -> "EligibilityTransition":
        if self.transition_id != self.record_id:
            raise ValueError("transition_id must equal record_id")
        if not self.record_id.startswith("eligibility_transition:"):
            raise ValueError("transition id must use eligibility_transition namespace")
        if self.prior_state == self.new_state:
            raise ValueError("eligibility transition must change state")
        return self


class NextEvaluation(CommonEnvelope):
    schedule_id: str
    market_id: str
    cause_observation_id: str
    cause_observation_sha256: str
    state: UniverseState
    tier: EvaluationTier
    due_at: datetime
    priority: int = Field(ge=0, le=1000)
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("cause_observation_sha256")
    @classmethod
    def cause_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("market_id", "cause_observation_id")
    @classmethod
    def schedule_identifiers_are_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("next evaluation identifiers must not be blank")
        return value

    @field_validator("due_at")
    @classmethod
    def due_clock_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def schedule_identity_holds(self) -> "NextEvaluation":
        if self.schedule_id != self.record_id:
            raise ValueError("schedule_id must equal record_id")
        if not self.record_id.startswith("next_evaluation:"):
            raise ValueError("schedule id must use next_evaluation namespace")
        if self.due_at <= self.created_at:
            raise ValueError("next evaluation must be due after creation")
        return self


class MarketAdmissionEpisode(CommonEnvelope):
    episode_id: str
    market_id: str
    eligibility_observation_id: str
    eligibility_observation_sha256: str
    policy_id: str
    policy_sha256: str
    trigger: AdmissionTrigger
    admitted_at: datetime
    candidate_seed_id: str
    held_position_override: bool = False
    prior_candidate_id: str | None = None
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("eligibility_observation_sha256", "policy_sha256")
    @classmethod
    def episode_hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("admitted_at")
    @classmethod
    def admission_clock_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def episode_identity_and_override_hold(self) -> "MarketAdmissionEpisode":
        if self.episode_id != self.record_id:
            raise ValueError("episode_id must equal record_id")
        if not self.record_id.startswith("market_admission_episode:"):
            raise ValueError("episode id must use market_admission_episode namespace")
        if self.held_position_override != (
            self.trigger == AdmissionTrigger.HELD_POSITION_BOOTSTRAP
        ):
            raise ValueError("held override must exactly match HELD_POSITION_BOOTSTRAP")
        return self


class BlindResultReuseReceipt(CommonEnvelope):
    reuse_receipt_id: str
    episode_id: str
    prior_blind_result_id: str
    prior_blind_result_sha256: str
    rule_hash: str
    fresh_until: datetime
    reused_at: datetime
    decision: Literal["REUSED"] = "REUSED"
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("prior_blind_result_sha256", "rule_hash")
    @classmethod
    def reuse_hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("fresh_until", "reused_at")
    @classmethod
    def reuse_clocks_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def reuse_is_fresh_and_bound(self) -> "BlindResultReuseReceipt":
        if self.reuse_receipt_id != self.record_id:
            raise ValueError("reuse_receipt_id must equal record_id")
        if self.reused_at >= self.fresh_until:
            raise ValueError("Blind result must be fresh at reuse time")
        return self


@dataclass(frozen=True, slots=True)
class EligibilityEvaluation:
    observation: MarketabilityObservation
    transition: EligibilityTransition | None
    next_evaluation: NextEvaluation
    history: EligibilityHistory
    persist_observation: bool


def build_eligibility_policy(
    *,
    policy_name: str,
    run_id: str,
    created_at: datetime,
    source_version: str = UNIVERSE_VERSION,
    min_liquidity_enter: Decimal,
    min_volume_enter: Decimal,
    max_spread_enter: Decimal,
    min_executable_depth_enter: Decimal = _ZERO,
    min_hours_to_deadline: int = 24,
    min_question_chars: int = 8,
    min_rule_text_chars: int = 80,
    near_band_fraction: Decimal = Decimal("0.20"),
    liquidity_exit_ratio: Decimal = Decimal("0.80"),
    volume_exit_ratio: Decimal = Decimal("0.80"),
    spread_exit_ratio: Decimal = Decimal("1.25"),
    entry_confirmations: int = 2,
    confirmation_interval_seconds: int = 300,
    entry_dwell_seconds: int = 600,
    exit_confirmations: int = 2,
    exit_confirmation_interval_seconds: int = 300,
    near_interval_seconds: int = 300,
    dormant_interval_seconds: int = 1800,
    structural_interval_seconds: int = 21600,
    terminal_interval_seconds: int = 86400,
    max_facts_age_seconds: int = 300,
) -> EligibilityPolicy:
    created = ensure_utc(created_at)
    identity = {
        "run_id": run_id,
        "created_at": created,
        "policy_name": policy_name,
        "source_version": source_version,
        "thresholds": {
            "min_liquidity_enter": min_liquidity_enter,
            "min_volume_enter": min_volume_enter,
            "max_spread_enter": max_spread_enter,
            "min_executable_depth_enter": min_executable_depth_enter,
            "min_hours_to_deadline": min_hours_to_deadline,
            "min_question_chars": min_question_chars,
            "min_rule_text_chars": min_rule_text_chars,
            "near_band_fraction": near_band_fraction,
            "liquidity_exit_ratio": liquidity_exit_ratio,
            "volume_exit_ratio": volume_exit_ratio,
            "spread_exit_ratio": spread_exit_ratio,
            "entry_confirmations": entry_confirmations,
            "confirmation_interval_seconds": confirmation_interval_seconds,
            "entry_dwell_seconds": entry_dwell_seconds,
            "exit_confirmations": exit_confirmations,
            "exit_confirmation_interval_seconds": exit_confirmation_interval_seconds,
            "near_interval_seconds": near_interval_seconds,
            "dormant_interval_seconds": dormant_interval_seconds,
            "structural_interval_seconds": structural_interval_seconds,
            "terminal_interval_seconds": terminal_interval_seconds,
            "max_facts_age_seconds": max_facts_age_seconds,
        },
    }
    policy_id = stable_record_id("eligibility_policy", identity)
    return EligibilityPolicy(
        record_id=policy_id,
        policy_id=policy_id,
        run_id=run_id,
        created_at=created,
        source=UNIVERSE_SOURCE,
        source_version=source_version,
        policy_name=policy_name,
        min_liquidity_enter=min_liquidity_enter,
        min_volume_enter=min_volume_enter,
        max_spread_enter=max_spread_enter,
        min_executable_depth_enter=min_executable_depth_enter,
        min_hours_to_deadline=min_hours_to_deadline,
        min_question_chars=min_question_chars,
        min_rule_text_chars=min_rule_text_chars,
        near_band_fraction=near_band_fraction,
        liquidity_exit_ratio=liquidity_exit_ratio,
        volume_exit_ratio=volume_exit_ratio,
        spread_exit_ratio=spread_exit_ratio,
        entry_confirmations=entry_confirmations,
        confirmation_interval_seconds=confirmation_interval_seconds,
        entry_dwell_seconds=entry_dwell_seconds,
        exit_confirmations=exit_confirmations,
        exit_confirmation_interval_seconds=exit_confirmation_interval_seconds,
        near_interval_seconds=near_interval_seconds,
        dormant_interval_seconds=dormant_interval_seconds,
        structural_interval_seconds=structural_interval_seconds,
        terminal_interval_seconds=terminal_interval_seconds,
        max_facts_age_seconds=max_facts_age_seconds,
    )


def _uses_stay_threshold(state: UniverseState) -> bool:
    return state in {
        UniverseState.ELIGIBLE_UNRESEARCHED,
        UniverseState.RESEARCH_ADMITTED,
        UniverseState.CANDIDATE_ACTIVE,
        UniverseState.CANDIDATE_STALE,
    }


def _near_ratio_below(value: Decimal | None, threshold: Decimal) -> Decimal | None:
    if value is None or threshold == 0 or value >= threshold:
        return _ZERO if value is not None else None
    return (threshold - value) / threshold


def _near_ratio_above(value: Decimal | None, threshold: Decimal) -> Decimal | None:
    if value is None or threshold == 0 or value <= threshold:
        return _ZERO if value is not None else None
    return (value - threshold) / threshold


def _marketability_decision(
    snapshot: MarketSnapshot,
    facts: MarketabilityFacts,
    policy: EligibilityPolicy,
    history: EligibilityHistory,
) -> tuple[
    bool,
    bool,
    tuple[EligibilityReasonCode, ...],
    Literal["ENTER", "STAY"],
    dict[str, Decimal],
    str,
]:
    stay = _uses_stay_threshold(history.state)
    threshold_mode: Literal["ENTER", "STAY"] = "STAY" if stay else "ENTER"
    liquidity_threshold = policy.min_liquidity_enter * (
        policy.liquidity_exit_ratio if stay else _ONE
    )
    volume_threshold = policy.min_volume_enter * (
        policy.volume_exit_ratio if stay else _ONE
    )
    spread_threshold = min(
        _ONE,
        policy.max_spread_enter * (policy.spread_exit_ratio if stay else _ONE),
    )
    depth_threshold = policy.min_executable_depth_enter
    thresholds = {
        "liquidity": liquidity_threshold,
        "volume": volume_threshold,
        "spread": spread_threshold,
        "executable_depth": depth_threshold,
    }
    reasons: set[EligibilityReasonCode] = set()
    observed = facts.observed_at
    if snapshot.status != MarketStatus.ACTIVE:
        reasons.add(EligibilityReasonCode.NOT_ACTIVE_OPEN)
    if facts.accepting_orders is not True or facts.enable_order_book is not True:
        reasons.add(EligibilityReasonCode.NOT_ORDERBOOK_ACTIVE)
    if not facts.binary_paired:
        reasons.add(EligibilityReasonCode.NOT_BINARY_PAIRED)
    if facts.restricted is True:
        reasons.add(EligibilityReasonCode.RESTRICTED)
    if not facts.public_link_available:
        reasons.add(EligibilityReasonCode.MISSING_PUBLIC_LINK)
    if snapshot.end_at is None or snapshot.end_at <= observed + timedelta(
        hours=policy.min_hours_to_deadline
    ):
        reasons.add(EligibilityReasonCode.MISSING_OR_NEAR_DEADLINE)
    if (
        len(snapshot.question.strip()) < policy.min_question_chars
        or len(snapshot.rules_normalized or "") < policy.min_rule_text_chars
    ):
        reasons.add(EligibilityReasonCode.INSUFFICIENT_RULE_TEXT)
    quote_valid = (
        facts.best_bid is not None
        and facts.best_ask is not None
        and facts.spread is not None
        and facts.best_bid <= facts.best_ask
        and facts.spread == facts.best_ask - facts.best_bid
    )
    if not quote_valid:
        reasons.add(EligibilityReasonCode.MISSING_EXECUTABLE_QUOTE)
    if facts.liquidity is None or facts.liquidity < liquidity_threshold:
        reasons.add(EligibilityReasonCode.LOW_LIQUIDITY)
    if facts.volume is None or facts.volume < volume_threshold:
        reasons.add(EligibilityReasonCode.LOW_VOLUME)
    if facts.spread is not None and facts.spread > spread_threshold:
        reasons.add(EligibilityReasonCode.SPREAD_TOO_WIDE)
    if (
        facts.executable_depth is None
        or facts.executable_depth <= _ZERO
        or facts.executable_depth < depth_threshold
    ):
        reasons.add(EligibilityReasonCode.INSUFFICIENT_EXECUTABLE_DEPTH)
    if facts.stale:
        reasons.add(EligibilityReasonCode.DATA_STALE)
    if (
        facts.upstream_updated_at is not None
        and (facts.observed_at - facts.upstream_updated_at).total_seconds()
        > policy.max_facts_age_seconds
    ):
        reasons.add(EligibilityReasonCode.DATA_STALE)
    qualifies = not reasons
    if qualifies:
        canonical_reasons = (EligibilityReasonCode.ELIGIBLE,)
        near = False
    else:
        canonical_reasons = tuple(sorted(reasons, key=lambda item: item.value))
        structural = {
            EligibilityReasonCode.INSUFFICIENT_RULE_TEXT,
            EligibilityReasonCode.MISSING_OR_NEAR_DEADLINE,
            EligibilityReasonCode.MISSING_PUBLIC_LINK,
            EligibilityReasonCode.NOT_ACTIVE_OPEN,
            EligibilityReasonCode.NOT_BINARY_PAIRED,
            EligibilityReasonCode.NOT_ORDERBOOK_ACTIVE,
            EligibilityReasonCode.RESTRICTED,
        }
        dynamic = reasons - structural
        near = not bool(reasons & structural) and bool(dynamic)
        distances: list[Decimal | None] = []
        if EligibilityReasonCode.LOW_LIQUIDITY in dynamic:
            distances.append(_near_ratio_below(facts.liquidity, liquidity_threshold))
        if EligibilityReasonCode.LOW_VOLUME in dynamic:
            distances.append(_near_ratio_below(facts.volume, volume_threshold))
        if EligibilityReasonCode.SPREAD_TOO_WIDE in dynamic:
            distances.append(_near_ratio_above(facts.spread, spread_threshold))
        if EligibilityReasonCode.INSUFFICIENT_EXECUTABLE_DEPTH in dynamic:
            distances.append(_near_ratio_below(facts.executable_depth, depth_threshold))
        # Missing/stale quotes stay in the fast watch lane.  Numeric failures
        # are near only when every breached threshold is within the band.
        numeric = [item for item in distances if item is not None]
        if any(item is None or item > policy.near_band_fraction for item in distances):
            near = False
        elif numeric and any(item > policy.near_band_fraction for item in numeric):
            near = False
    fingerprint_payload = {
        "market_id": snapshot.identity.market_id,
        "status": snapshot.status,
        "end_at": snapshot.end_at,
        "rule_hash": snapshot.rule_hash,
        "question": snapshot.question,
        "accepting_orders": facts.accepting_orders,
        "enable_order_book": facts.enable_order_book,
        "binary_paired": facts.binary_paired,
        "public_link_available": facts.public_link_available,
        "restricted": facts.restricted,
        "best_bid": facts.best_bid,
        "best_ask": facts.best_ask,
        "spread": facts.spread,
        "liquidity": facts.liquidity,
        "volume": facts.volume,
        "executable_depth": facts.executable_depth,
        "stale": facts.stale,
        "threshold_mode": threshold_mode,
        "thresholds": thresholds,
        "reasons": canonical_reasons,
    }
    return (
        qualifies,
        near,
        canonical_reasons,
        threshold_mode,
        thresholds,
        content_sha256(fingerprint_payload),
    )


def _next_state(
    *,
    current: UniverseState,
    snapshot_status: MarketStatus,
    qualifies: bool,
    near: bool,
    entry_ready: bool,
    exit_ready: bool,
) -> UniverseState:
    if snapshot_status in {MarketStatus.CLOSED, MarketStatus.RESOLVED, MarketStatus.SUPERSEDED}:
        return UniverseState.TERMINAL
    if current == UniverseState.CANDIDATE_ACTIVE:
        if not qualifies and exit_ready:
            return UniverseState.CANDIDATE_STALE
        return current
    if current == UniverseState.CANDIDATE_STALE:
        if qualifies and entry_ready:
            return UniverseState.CANDIDATE_ACTIVE
        return current
    if current in {
        UniverseState.ELIGIBLE_UNRESEARCHED,
        UniverseState.RESEARCH_ADMITTED,
    } and not qualifies and not exit_ready:
        return current
    if qualifies:
        if current in {
            UniverseState.ELIGIBLE_UNRESEARCHED,
            UniverseState.RESEARCH_ADMITTED,
            UniverseState.CANDIDATE_ACTIVE,
        }:
            return current
        return (
            UniverseState.ELIGIBLE_UNRESEARCHED
            if entry_ready
            else UniverseState.NEAR_ELIGIBLE_WATCH
        )
    return UniverseState.NEAR_ELIGIBLE_WATCH if near else UniverseState.INELIGIBLE_DORMANT


def _next_tier_and_interval(
    *,
    state: UniverseState,
    reasons: tuple[EligibilityReasonCode, ...],
    policy: EligibilityPolicy,
    held_position: bool,
) -> tuple[EvaluationTier, int, int]:
    if held_position:
        return EvaluationTier.REALTIME_HELD, policy.near_interval_seconds, 1000
    if state in {UniverseState.CANDIDATE_ACTIVE, UniverseState.CANDIDATE_STALE}:
        return EvaluationTier.REALTIME_CANDIDATE, policy.near_interval_seconds, 900
    if state in {
        UniverseState.ELIGIBLE_UNRESEARCHED,
        UniverseState.RESEARCH_ADMITTED,
    } and reasons != (EligibilityReasonCode.ELIGIBLE,):
        return EvaluationTier.NEAR_FIVE_MINUTE, policy.near_interval_seconds, 700
    if state == UniverseState.TERMINAL:
        return EvaluationTier.TERMINAL_DAILY, policy.terminal_interval_seconds, 10
    if state == UniverseState.NEAR_ELIGIBLE_WATCH:
        return EvaluationTier.NEAR_FIVE_MINUTE, policy.near_interval_seconds, 600
    structural = {
        EligibilityReasonCode.INSUFFICIENT_RULE_TEXT,
        EligibilityReasonCode.MISSING_OR_NEAR_DEADLINE,
        EligibilityReasonCode.MISSING_PUBLIC_LINK,
        EligibilityReasonCode.NOT_ACTIVE_OPEN,
        EligibilityReasonCode.NOT_BINARY_PAIRED,
        EligibilityReasonCode.NOT_ORDERBOOK_ACTIVE,
        EligibilityReasonCode.RESTRICTED,
    }
    if set(reasons) & structural:
        return EvaluationTier.STRUCTURAL_SIX_HOUR, policy.structural_interval_seconds, 100
    return EvaluationTier.DORMANT_THIRTY_MINUTE, policy.dormant_interval_seconds, 300


def evaluate_eligibility(
    *,
    snapshot: MarketSnapshot,
    facts: MarketabilityFacts,
    policy: EligibilityPolicy,
    history: EligibilityHistory,
    trigger: EligibilityTrigger,
    run_id: str,
    held_position: bool = False,
) -> EligibilityEvaluation:
    """Evaluate one market and return replayable observation/schedule facts."""

    if snapshot.identity.market_id != facts.market_id:
        raise ValueError("snapshot and marketability market_id mismatch")
    if snapshot.record_id != facts.snapshot_id:
        raise ValueError("facts are bound to a different market snapshot")
    if snapshot.canonical_sha256 != facts.snapshot_sha256:
        raise ValueError("facts snapshot hash mismatch")
    if facts.observed_at < snapshot.source_observed_at:
        raise ValueError("marketability facts cannot predate the market snapshot")
    if history.last_observed_at is not None:
        if facts.observed_at < history.last_observed_at:
            raise ValueError("eligibility observation clock moved backwards")
    (
        qualifies,
        near,
        reasons,
        threshold_mode,
        thresholds,
        fingerprint,
    ) = _marketability_decision(snapshot, facts, policy, history)
    if history.last_observed_at == facts.observed_at:
        if history.last_marketability_fingerprint != fingerprint:
            raise ValueError("same observation clock has conflicting marketability content")
        consecutive = history.consecutive_qualifying
        first_at = history.first_qualifying_at
        last_confirmation = history.last_confirmation_at
        consecutive_disqualifying = history.consecutive_disqualifying
        first_disqualifying_at = history.first_disqualifying_at
        last_disconfirmation_at = history.last_disconfirmation_at
    elif qualifies:
        consecutive_disqualifying = 0
        first_disqualifying_at = None
        last_disconfirmation_at = None
        if history.last_qualifies and history.first_qualifying_at is not None:
            first_at = history.first_qualifying_at
            last_confirmation = history.last_confirmation_at
            consecutive = history.consecutive_qualifying
            assert last_confirmation is not None
            if (
                facts.observed_at - last_confirmation
            ).total_seconds() >= policy.confirmation_interval_seconds:
                consecutive += 1
                last_confirmation = facts.observed_at
        else:
            consecutive = 1
            first_at = facts.observed_at
            last_confirmation = facts.observed_at
    else:
        consecutive = 0
        first_at = None
        last_confirmation = None
        if (
            not history.last_qualifies
            and history.first_disqualifying_at is not None
            and history.last_disconfirmation_at is not None
        ):
            consecutive_disqualifying = history.consecutive_disqualifying
            first_disqualifying_at = history.first_disqualifying_at
            last_disconfirmation_at = history.last_disconfirmation_at
            if (
                facts.observed_at - last_disconfirmation_at
            ).total_seconds() >= policy.exit_confirmation_interval_seconds:
                consecutive_disqualifying += 1
                last_disconfirmation_at = facts.observed_at
        else:
            consecutive_disqualifying = 1
            first_disqualifying_at = facts.observed_at
            last_disconfirmation_at = facts.observed_at
    entry_ready = False
    if qualifies and first_at is not None:
        dwell = (facts.observed_at - first_at).total_seconds()
        entry_ready = (
            consecutive >= policy.entry_confirmations
            and dwell >= policy.confirmation_interval_seconds
        ) or dwell >= policy.entry_dwell_seconds
    immediate_exit_reasons = {
        EligibilityReasonCode.INSUFFICIENT_RULE_TEXT,
        EligibilityReasonCode.MISSING_OR_NEAR_DEADLINE,
        EligibilityReasonCode.MISSING_PUBLIC_LINK,
        EligibilityReasonCode.NOT_ACTIVE_OPEN,
        EligibilityReasonCode.NOT_BINARY_PAIRED,
        EligibilityReasonCode.NOT_ORDERBOOK_ACTIVE,
        EligibilityReasonCode.RESTRICTED,
    }
    exit_ready = bool(set(reasons) & immediate_exit_reasons)
    if not qualifies and first_disqualifying_at is not None:
        disqualifying_dwell = (
            facts.observed_at - first_disqualifying_at
        ).total_seconds()
        exit_ready = exit_ready or (
            consecutive_disqualifying >= policy.exit_confirmations
            and disqualifying_dwell >= policy.exit_confirmation_interval_seconds
        )
    new_state = _next_state(
        current=history.state,
        snapshot_status=snapshot.status,
        qualifies=qualifies,
        near=near,
        entry_ready=entry_ready,
        exit_ready=exit_ready,
    )
    observation_fields = {
        "run_id": run_id,
        "created_at": facts.observed_at,
        "source": UNIVERSE_SOURCE,
        "source_version": UNIVERSE_VERSION,
        "provenance": (),
        "extensions": {"upstream_updated_at": facts.upstream_updated_at},
        "market_id": facts.market_id,
        "snapshot_id": facts.snapshot_id,
        "snapshot_sha256": facts.snapshot_sha256,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.canonical_sha256,
        "trigger": trigger,
        "observed_at": facts.observed_at,
        "prior_state": history.state,
        "threshold_mode": threshold_mode,
        "liquidity": facts.liquidity,
        "volume": facts.volume,
        "best_bid": facts.best_bid,
        "best_ask": facts.best_ask,
        "spread": facts.spread,
        "executable_depth": facts.executable_depth,
        "thresholds": thresholds,
        "qualifies": qualifies,
        "near_eligible": near,
        "reason_codes": reasons,
        "marketability_fingerprint": fingerprint,
        "source_artifact_ids": facts.source_artifact_ids,
        "execution": "NO_ORDER",
    }
    observation_id = stable_record_id("marketability_observation", observation_fields)
    observation = MarketabilityObservation(
        record_id=observation_id,
        observation_id=observation_id,
        **observation_fields,
    )
    transition: EligibilityTransition | None = None
    if new_state != history.state:
        transition_fields = {
            "run_id": run_id,
            "created_at": facts.observed_at,
            "source": UNIVERSE_SOURCE,
            "source_version": UNIVERSE_VERSION,
            "market_id": facts.market_id,
            "observation_id": observation.record_id,
            "observation_sha256": observation.canonical_sha256,
            "prior_state": history.state,
            "new_state": new_state,
            "trigger": trigger,
            "effective_at": facts.observed_at,
            "consecutive_qualifying": consecutive,
            "consecutive_disqualifying": consecutive_disqualifying,
            "reason_codes": reasons,
            "execution": "NO_ORDER",
        }
        transition_id = stable_record_id("eligibility_transition", transition_fields)
        transition = EligibilityTransition(
            record_id=transition_id,
            transition_id=transition_id,
            **transition_fields,
        )
    tier, interval_seconds, priority = _next_tier_and_interval(
        state=new_state,
        reasons=reasons,
        policy=policy,
        held_position=held_position,
    )
    schedule_fields = {
        "run_id": run_id,
        "created_at": facts.observed_at,
        "source": UNIVERSE_SOURCE,
        "source_version": UNIVERSE_VERSION,
        "market_id": facts.market_id,
        "cause_observation_id": observation.record_id,
        "cause_observation_sha256": observation.canonical_sha256,
        "state": new_state,
        "tier": tier,
        "due_at": facts.observed_at + timedelta(seconds=interval_seconds),
        "priority": priority,
        "execution": "NO_ORDER",
    }
    schedule_id = stable_record_id("next_evaluation", schedule_fields)
    next_evaluation = NextEvaluation(
        record_id=schedule_id,
        schedule_id=schedule_id,
        **schedule_fields,
    )
    updated_history = EligibilityHistory(
        state=new_state,
        consecutive_qualifying=consecutive,
        first_qualifying_at=first_at,
        last_confirmation_at=last_confirmation,
        consecutive_disqualifying=consecutive_disqualifying,
        first_disqualifying_at=first_disqualifying_at,
        last_disconfirmation_at=last_disconfirmation_at,
        last_observed_at=facts.observed_at,
        last_marketability_fingerprint=fingerprint,
        last_qualifies=qualifies,
    )
    persist_observation = (
        history.last_marketability_fingerprint != fingerprint
        or transition is not None
        or (qualifies and not entry_ready)
    )
    return EligibilityEvaluation(
        observation=observation,
        transition=transition,
        next_evaluation=next_evaluation,
        history=updated_history,
        persist_observation=persist_observation,
    )


def create_eligibility_history_checkpoint(
    *,
    observation: MarketabilityObservation,
    history: EligibilityHistory,
) -> EligibilityHistoryCheckpoint:
    """Seal exact restart state beside the immutable observation that caused it."""

    if history.last_observed_at != observation.observed_at:
        raise ValueError("history and observation clocks do not match")
    if history.last_marketability_fingerprint != observation.marketability_fingerprint:
        raise ValueError("history and observation fingerprints do not match")
    if history.last_qualifies != observation.qualifies:
        raise ValueError("history and observation qualification do not match")
    fields = {
        "run_id": observation.run_id,
        "created_at": observation.observed_at,
        "source": UNIVERSE_SOURCE,
        "source_version": UNIVERSE_VERSION,
        "market_id": observation.market_id,
        "observation_id": observation.record_id,
        "observation_sha256": observation.canonical_sha256,
        "observed_at": observation.observed_at,
        "history": history,
        "execution": "NO_ORDER",
    }
    checkpoint_id = stable_record_id("eligibility_history_checkpoint", fields)
    return EligibilityHistoryCheckpoint(
        record_id=checkpoint_id,
        checkpoint_id=checkpoint_id,
        **fields,
    )


def create_admission_episode(
    *,
    observation: MarketabilityObservation,
    policy: EligibilityPolicy,
    trigger: AdmissionTrigger,
    admitted_at: datetime,
    run_id: str,
    prior_candidate_id: str | None = None,
) -> MarketAdmissionEpisode:
    """Create a new immutable admission lineage without reviving an old one."""

    admitted = ensure_utc(admitted_at)
    if admitted != observation.observed_at:
        raise ValueError("admission clock must equal the triggering observation clock")
    if run_id != observation.run_id:
        raise ValueError("admission run_id must equal the triggering observation run_id")
    held_override = trigger == AdmissionTrigger.HELD_POSITION_BOOTSTRAP
    if not observation.qualifies and not held_override:
        raise ValueError("only eligible or held-position markets may be admitted")
    if observation.policy_id != policy.policy_id:
        raise ValueError("observation and admission policy id mismatch")
    if observation.policy_sha256 != policy.canonical_sha256:
        raise ValueError("observation and admission policy hash mismatch")
    episode_identity = {
        "market_id": observation.market_id,
        "eligibility_observation_id": observation.record_id,
        "eligibility_observation_sha256": observation.canonical_sha256,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.canonical_sha256,
        "trigger": trigger,
        "prior_candidate_id": prior_candidate_id,
    }
    episode_id = stable_record_id("market_admission_episode", episode_identity)
    candidate_seed_id = stable_record_id(
        "candidate_episode_seed", observation.market_id, episode_id
    )
    return MarketAdmissionEpisode(
        record_id=episode_id,
        episode_id=episode_id,
        run_id=run_id,
        created_at=admitted,
        source=UNIVERSE_SOURCE,
        source_version=UNIVERSE_VERSION,
        market_id=observation.market_id,
        eligibility_observation_id=observation.record_id,
        eligibility_observation_sha256=observation.canonical_sha256,
        policy_id=policy.policy_id,
        policy_sha256=policy.canonical_sha256,
        trigger=trigger,
        admitted_at=admitted,
        candidate_seed_id=candidate_seed_id,
        held_position_override=held_override,
        prior_candidate_id=prior_candidate_id,
        execution="NO_ORDER",
    )


def build_blind_result_reuse_receipt(
    *,
    episode: MarketAdmissionEpisode,
    prior_blind_result_id: str,
    prior_blind_result_sha256: str,
    rule_hash: str,
    fresh_until: datetime,
    reused_at: datetime,
    run_id: str,
) -> BlindResultReuseReceipt:
    reused = ensure_utc(reused_at)
    fresh = ensure_utc(fresh_until)
    fields = {
        "run_id": run_id,
        "created_at": reused,
        "source": UNIVERSE_SOURCE,
        "source_version": UNIVERSE_VERSION,
        "episode_id": episode.episode_id,
        "prior_blind_result_id": prior_blind_result_id,
        "prior_blind_result_sha256": validate_sha256(prior_blind_result_sha256),
        "rule_hash": validate_sha256(rule_hash),
        "fresh_until": fresh,
        "reused_at": reused,
        "decision": "REUSED",
        "execution": "NO_ORDER",
    }
    receipt_id = stable_record_id("blind_result_reuse", fields)
    return BlindResultReuseReceipt(
        record_id=receipt_id,
        reuse_receipt_id=receipt_id,
        **fields,
    )
