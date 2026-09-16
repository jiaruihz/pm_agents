"""Immutable, Decimal-only inputs for the read-only capital allocator.

The contracts in this module are sealed inputs or versioned policy.  They do
not fetch an account, infer missing orders, fabricate book depth, or expose an
order capability.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from src.polymarket_alpha.contracts.base import (
    AlphaContract,
    CommonEnvelope,
    content_sha256,
    ensure_utc,
    stable_record_id,
    validate_sha256,
)


ZERO = Decimal("0")
ONE = Decimal("1")
CAPITAL_SOURCE = "alpha_capital_agent.capital"
CAPITAL_VERSION = "aca_capital_v1"
CAPITAL_POLICY_V1_RELEASED_AT = datetime(2026, 8, 29, tzinfo=timezone.utc)


class OutcomeDirection(StrEnum):
    YES = "YES"
    NO = "NO"


class AccountCompleteness(StrEnum):
    AUTHENTICATED_COMPLETE = "AUTHENTICATED_COMPLETE"
    PUBLIC_ONLY = "PUBLIC_ONLY"
    INCOMPLETE = "INCOMPLETE"

    # Compatibility spelling retained for the first offline prototype.
    COMPLETE = "AUTHENTICATED_COMPLETE"


class ExecutableLevel(AlphaContract):
    """One executable level; endpoint prices are rejected fail-closed."""

    price: Decimal = Field(gt=ZERO, lt=ONE)
    size: Decimal = Field(gt=ZERO)


def _validate_refs(value: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    normalized = tuple(item.strip() for item in value)
    if not normalized or any(not item for item in normalized):
        raise ValueError(f"{label} requires non-empty lineage")
    if normalized != tuple(sorted(set(normalized))):
        raise ValueError(f"{label} lineage must be unique and sorted")
    return normalized


class AccountSnapshotSeal(AlphaContract):
    """Authenticated account snapshot whose coverage is explicit."""

    account_id: str
    decision_as_of: datetime
    free_cash: Decimal = Field(ge=ZERO)
    reserved_cash: Decimal = Field(default=ZERO, ge=ZERO)
    completeness: AccountCompleteness
    input_refs: tuple[str, ...]
    snapshot_hash: str | None = None

    @field_validator("account_id")
    @classmethod
    def account_is_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("account_id must not be blank")
        return value

    @field_validator("decision_as_of")
    @classmethod
    def clock_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("input_refs")
    @classmethod
    def refs_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_refs(value, label="account snapshot")

    @field_validator("snapshot_hash")
    @classmethod
    def hash_is_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def supplied_hash_matches_content(self) -> "AccountSnapshotSeal":
        derived = content_sha256(self.model_dump(exclude={"snapshot_hash"}))
        if self.snapshot_hash is not None and self.snapshot_hash != derived:
            raise ValueError("snapshot_hash does not match sealed account content")
        return self

    @property
    def content_hash(self) -> str:
        return content_sha256(self.model_dump(exclude={"snapshot_hash"}))


class PositionExposure(AlphaContract):
    position_id: str
    market_id: str
    event_id: str
    cluster_id: str
    maturity: str
    direction: OutcomeDirection
    shares: Decimal = Field(gt=ZERO)
    mark_price: Decimal = Field(ge=ZERO, le=ONE)
    q_cons: Decimal = Field(ge=ZERO, le=ONE)
    release_days: Decimal = Field(ge=ZERO)
    research_fresh_until: datetime
    sell_levels: tuple[ExecutableLevel, ...] = ()
    redeemable: bool = False
    locked: bool = False
    input_refs: tuple[str, ...]

    @field_validator(
        "position_id", "market_id", "event_id", "cluster_id", "maturity"
    )
    @classmethod
    def identifiers_are_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("position identifiers must not be blank")
        return value

    @field_validator("research_fresh_until")
    @classmethod
    def freshness_clock_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("input_refs")
    @classmethod
    def refs_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_refs(value, label="position")

    @model_validator(mode="after")
    def executable_book_is_canonical(self) -> "PositionExposure":
        prices = tuple(level.price for level in self.sell_levels)
        if prices != tuple(sorted(set(prices), reverse=True)):
            raise ValueError("sell levels must have unique descending prices")
        if self.locked and self.sell_levels:
            raise ValueError("locked position cannot claim executable sell depth")
        return self

    @property
    def mark_notional(self) -> Decimal:
        return self.shares * self.mark_price

    def research_is_fresh(self, at: datetime) -> bool:
        return ensure_utc(at) <= self.research_fresh_until

    @property
    def content_hash(self) -> str:
        return content_sha256(self)


class OpportunityRecord(AlphaContract):
    opportunity_id: str
    market_id: str
    event_id: str
    cluster_id: str
    maturity: str
    direction: OutcomeDirection
    q_cons: Decimal = Field(ge=ZERO, le=ONE)
    release_days: Decimal = Field(ge=ZERO)
    research_fresh_until: datetime
    buy_levels: tuple[ExecutableLevel, ...]
    input_refs: tuple[str, ...]
    input_hash: str | None = None

    @field_validator(
        "opportunity_id", "market_id", "event_id", "cluster_id", "maturity"
    )
    @classmethod
    def identifiers_are_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("opportunity identifiers must not be blank")
        return value

    @field_validator("research_fresh_until")
    @classmethod
    def freshness_clock_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("input_refs")
    @classmethod
    def refs_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_refs(value, label="opportunity")

    @field_validator("input_hash")
    @classmethod
    def hash_is_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def executable_book_and_hash_are_canonical(self) -> "OpportunityRecord":
        prices = tuple(level.price for level in self.buy_levels)
        if not prices:
            raise ValueError("opportunity requires executable buy depth")
        if prices != tuple(sorted(set(prices))):
            raise ValueError("buy levels must have unique ascending prices")
        derived = content_sha256(self.model_dump(exclude={"input_hash"}))
        if self.input_hash is not None and self.input_hash != derived:
            raise ValueError("input_hash does not match opportunity content")
        return self

    def research_is_fresh(self, at: datetime) -> bool:
        return ensure_utc(at) <= self.research_fresh_until

    @property
    def content_hash(self) -> str:
        return content_sha256(self.model_dump(exclude={"input_hash"}))


class ReplacementHistory(AlphaContract):
    """Caller-carried hysteresis state for one old/new pair."""

    position_id: str
    opportunity_id: str
    consecutive_confirmations: int = Field(default=0, ge=0)
    first_confirmation_at: datetime | None = None
    last_confirmation_at: datetime | None = None
    last_executed_at: datetime | None = None
    last_execution_ref: str | None = None

    @field_validator("position_id", "opportunity_id")
    @classmethod
    def ids_are_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("replacement history ids must not be blank")
        return value

    @field_validator(
        "first_confirmation_at", "last_confirmation_at", "last_executed_at"
    )
    @classmethod
    def clocks_are_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @model_validator(mode="after")
    def confirmation_state_is_consistent(self) -> "ReplacementHistory":
        if self.consecutive_confirmations == 0:
            if self.first_confirmation_at is not None or self.last_confirmation_at is not None:
                raise ValueError("zero confirmations cannot retain confirmation clocks")
        elif self.first_confirmation_at is None or self.last_confirmation_at is None:
            raise ValueError("positive confirmations require both confirmation clocks")
        elif self.last_confirmation_at < self.first_confirmation_at:
            raise ValueError("last confirmation cannot precede first confirmation")
        if (self.last_executed_at is None) != (self.last_execution_ref is None):
            raise ValueError("external execution clock and receipt ref must be paired")
        if self.last_execution_ref is not None and not self.last_execution_ref.strip():
            raise ValueError("external execution receipt ref must not be blank")
        if (
            self.last_executed_at is not None
            and self.first_confirmation_at is not None
            and self.first_confirmation_at <= self.last_executed_at
        ):
            raise ValueError("new confirmation streak must begin after prior execution")
        return self

    @property
    def pair_key(self) -> tuple[str, str]:
        return self.position_id, self.opportunity_id

    @property
    def content_hash(self) -> str:
        return content_sha256(self)


def record_external_replacement_execution(
    history: ReplacementHistory,
    *,
    executed_at: datetime,
    execution_receipt_ref: str,
) -> ReplacementHistory:
    """Reset confirmation state from caller-supplied external fill evidence.

    ACA cannot create this evidence and never calls an order API.  The helper
    only makes the cooldown transition explicit once an independent execution
    owner supplies a receipt reference.
    """

    executed = ensure_utc(executed_at)
    receipt_ref = execution_receipt_ref.strip()
    if not receipt_ref:
        raise ValueError("external execution receipt ref must not be blank")
    if history.consecutive_confirmations < 2 or history.last_confirmation_at is None:
        raise ValueError("replacement execution requires a confirmed review history")
    if executed < history.last_confirmation_at:
        raise ValueError("external execution cannot predate the confirmed review")
    return ReplacementHistory(
        position_id=history.position_id,
        opportunity_id=history.opportunity_id,
        consecutive_confirmations=0,
        last_executed_at=executed,
        last_execution_ref=receipt_ref,
    )


class CapitalPolicy(CommonEnvelope):
    policy_id: str
    policy_name: str
    cash_buffer: Decimal = Field(ge=ZERO, le=ONE)
    market_cap: Decimal = Field(ge=ZERO, le=ONE)
    event_cap: Decimal = Field(ge=ZERO, le=ONE)
    cluster_cap: Decimal = Field(ge=ZERO, le=ONE)
    maturity_cap: Decimal = Field(ge=ZERO, le=ONE)
    depth_participation: Decimal = Field(gt=ZERO, le=ONE)
    kelly_fraction: Decimal = Field(gt=ZERO, le=ONE)
    entry_edge_floor: Decimal = Field(ge=ZERO, le=ONE)
    replacement_return_floor: Decimal = Field(ge=ZERO, le=ONE)
    model_buffer: Decimal = Field(ge=ZERO, le=ONE)
    unpriced_buffer: Decimal = Field(ge=ZERO, le=ONE)
    replacement_confirmations: int = Field(ge=2)
    confirmation_interval_seconds: int = Field(gt=0)
    replacement_cooldown_seconds: int = Field(gt=0)
    max_replacements_per_plan: Literal[1] = 1
    mode: Literal["READ_ONLY_SHADOW"] = "READ_ONLY_SHADOW"
    execution_capability: Literal["NO_ORDER"] = "NO_ORDER"

    @model_validator(mode="after")
    def policy_identity_holds(self) -> "CapitalPolicy":
        if self.policy_id != self.record_id:
            raise ValueError("policy_id must equal record_id")
        if not self.record_id.startswith("capital_policy:"):
            raise ValueError("capital policy id must use capital_policy namespace")
        return self

    @property
    def content_hash(self) -> str:
        return self.canonical_sha256


def build_capital_policy(
    *,
    run_id: str,
    created_at: datetime,
    cash_buffer: Decimal = Decimal("0.25"),
    market_cap: Decimal = Decimal("0.03"),
    event_cap: Decimal = Decimal("0.08"),
    cluster_cap: Decimal = Decimal("0.15"),
    maturity_cap: Decimal = Decimal("0.25"),
    depth_participation: Decimal = Decimal("0.10"),
    kelly_fraction: Decimal = Decimal("0.10"),
    entry_edge_floor: Decimal = Decimal("0.03"),
    replacement_return_floor: Decimal = Decimal("0.03"),
    model_buffer: Decimal = ZERO,
    unpriced_buffer: Decimal = ZERO,
    replacement_confirmations: int = 2,
    confirmation_interval_seconds: int = 15 * 60,
    replacement_cooldown_seconds: int = 6 * 60 * 60,
) -> CapitalPolicy:
    created_at = ensure_utc(created_at)
    parameters = {
        "cash_buffer": cash_buffer,
        "market_cap": market_cap,
        "event_cap": event_cap,
        "cluster_cap": cluster_cap,
        "maturity_cap": maturity_cap,
        "depth_participation": depth_participation,
        "kelly_fraction": kelly_fraction,
        "entry_edge_floor": entry_edge_floor,
        "replacement_return_floor": replacement_return_floor,
        "model_buffer": model_buffer,
        "unpriced_buffer": unpriced_buffer,
        "replacement_confirmations": replacement_confirmations,
        "confirmation_interval_seconds": confirmation_interval_seconds,
        "replacement_cooldown_seconds": replacement_cooldown_seconds,
        "max_replacements_per_plan": 1,
        "mode": "READ_ONLY_SHADOW",
        "execution_capability": "NO_ORDER",
    }
    record_id = stable_record_id(
        "capital_policy",
        "capital_replacement_v1",
        run_id,
        created_at,
        parameters,
    )
    return CapitalPolicy(
        record_id=record_id,
        policy_id=record_id,
        run_id=run_id,
        created_at=created_at,
        source=CAPITAL_SOURCE,
        source_version=CAPITAL_VERSION,
        policy_name="capital_replacement_v1",
        **parameters,
    )


def default_capital_policy() -> CapitalPolicy:
    return build_capital_policy(
        run_id="aca-capital-policy-v1",
        created_at=CAPITAL_POLICY_V1_RELEASED_AT,
    )
