from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, is_dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Mapping


EXECUTION_SCHEMA_VERSION = "weather_execution_v1"
_LIVE_CONTROL_FIELDS = frozenset(
    {
        "authorization_ref",
        "confirm_live",
        "dry_run",
        "execution_mode",
        "live_enabled",
        "pause_state",
        "run_purpose",
    }
)
_TRANSIENT_CONFIG_FIELDS = frozenset(
    {
        "book_max_age_sec",
        "created_at_utc",
        "data_epoch_ref",
        "data_epoch_ts_utc",
        "fair_value",
        "model_token_probability",
        "next_data_update_due_utc",
        "strategy_price_cap",
        "strategy_price_floor",
        "total_shares",
    }
)


class ExecutionContractError(ValueError):
    """Raised when a pure execution contract is malformed or unsafe."""


def _required(value: Any, name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ExecutionContractError(f"missing required field: {name}")
    return text


def _decimal(value: Decimal | str | int, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ExecutionContractError(f"{name} must be a Decimal, integer, or decimal string")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExecutionContractError(f"invalid decimal for {name}: {value!r}") from exc


def _optional_decimal(value: Decimal | str | int | None, name: str) -> Decimal | None:
    return None if value is None else _decimal(value, name)


def _declared_fraction_decimal(value: Any, name: str) -> Decimal:
    """Preserve a declared fractional literal while avoiding binary float arithmetic."""
    if isinstance(value, bool):
        raise ExecutionContractError(f"{name} must be a decimal fraction")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExecutionContractError(f"invalid decimal fraction for {name}: {value!r}") from exc


def _freeze_json(value: Any, name: str = "value") -> Any:
    if value is None or isinstance(value, (str, int, float, bool, Decimal)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item, f"{name}.{key}") for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, name) for item in value)
    raise ExecutionContractError(f"{name} is not JSON-serializable: {type(value).__name__}")


def _freeze_mapping(value: Mapping[str, Any] | None, name: str) -> Mapping[str, Any]:
    return MappingProxyType({} if value is None else {str(key): _freeze_json(item, f"{name}.{key}") for key, item in value.items()})


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if is_dataclass(value):
        return {item.name: _json_value(getattr(value, item.name)) for item in fields(value)}
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _identity_hash(kind: str, payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(f"{kind}|{canonical_json(payload)}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class JsonContract:
    def to_json(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass(frozen=True)
class ExecutionConstraints(JsonContract):
    price_floor: Decimal | str | int | None = None
    price_cap: Decimal | str | int | None = None
    required_edge: Decimal | str | int | None = None
    required_depth: Decimal | str | int | None = None
    book_max_age_sec: Decimal | str | int | None = None
    minimum_shares: Decimal | str | int | None = None
    maximum_shares: Decimal | str | int | None = None
    deadline_utc: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "price_floor",
            "price_cap",
            "required_edge",
            "required_depth",
            "book_max_age_sec",
            "minimum_shares",
            "maximum_shares",
        ):
            object.__setattr__(self, name, _optional_decimal(getattr(self, name), name))
        if self.price_floor is not None and self.price_cap is not None and self.price_floor > self.price_cap:
            raise ExecutionContractError("price_floor cannot exceed price_cap")
        if self.minimum_shares is not None and self.minimum_shares <= 0:
            raise ExecutionContractError("minimum_shares must be positive")
        if self.maximum_shares is not None and self.maximum_shares <= 0:
            raise ExecutionContractError("maximum_shares must be positive")
        if self.minimum_shares is not None and self.maximum_shares is not None and self.minimum_shares > self.maximum_shares:
            raise ExecutionContractError("minimum_shares cannot exceed maximum_shares")


@dataclass(frozen=True)
class BookLevel(JsonContract):
    price: Decimal | str | int
    size: Decimal | str | int

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", _decimal(self.price, "price"))
        object.__setattr__(self, "size", _decimal(self.size, "size"))
        if self.price <= 0 or self.price >= 1:
            raise ExecutionContractError("book level price must be between 0 and 1")
        if self.size <= 0:
            raise ExecutionContractError("book level size must be positive")


@dataclass(frozen=True)
class MarketBook(JsonContract):
    token_id: str
    status: str
    fetched_at_utc: str
    venue_timestamp_utc: str | None
    book_epoch_ref: str
    tick_size: Decimal | str | int
    tick_size_source: str
    minimum_order_shares: Decimal | str | int
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "token_id", _required(self.token_id, "token_id"))
        object.__setattr__(self, "status", _required(self.status, "status"))
        object.__setattr__(self, "fetched_at_utc", _required(self.fetched_at_utc, "fetched_at_utc"))
        object.__setattr__(self, "book_epoch_ref", _required(self.book_epoch_ref, "book_epoch_ref"))
        object.__setattr__(self, "tick_size_source", _required(self.tick_size_source, "tick_size_source"))
        object.__setattr__(self, "tick_size", _decimal(self.tick_size, "tick_size"))
        object.__setattr__(self, "minimum_order_shares", _decimal(self.minimum_order_shares, "minimum_order_shares"))
        object.__setattr__(self, "bids", tuple(self.bids))
        object.__setattr__(self, "asks", tuple(self.asks))
        if self.tick_size <= 0:
            raise ExecutionContractError("tick_size must be positive")
        if self.minimum_order_shares <= 0:
            raise ExecutionContractError("minimum_order_shares must be positive")
        if any(not isinstance(level, BookLevel) for level in self.bids + self.asks):
            raise ExecutionContractError("bids and asks must contain BookLevel values")
        if any(left.price < right.price for left, right in zip(self.bids, self.bids[1:])):
            raise ExecutionContractError("bids must be ordered high to low")
        if any(left.price > right.price for left, right in zip(self.asks, self.asks[1:])):
            raise ExecutionContractError("asks must be ordered low to high")

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "MarketBook":
        return cls(
            token_id=payload["token_id"],
            status=payload["status"],
            fetched_at_utc=payload["fetched_at_utc"],
            venue_timestamp_utc=payload.get("venue_timestamp_utc"),
            book_epoch_ref=payload["book_epoch_ref"],
            tick_size=payload["tick_size"],
            tick_size_source=payload["tick_size_source"],
            minimum_order_shares=payload["minimum_order_shares"],
            bids=tuple(BookLevel(**level) for level in payload["bids"]),
            asks=tuple(BookLevel(**level) for level in payload["asks"]),
        )


@dataclass(frozen=True)
class FeeSchedule(JsonContract):
    venue: str
    fee_schedule_ref: str
    fee_schedule_fetched_at_utc: str
    fee_formula_id: str
    taker_fee_parameters: Mapping[str, Any] = field(default_factory=dict)
    maker_fee_parameters: Mapping[str, Any] = field(default_factory=dict)
    maker_rebate_program: str | None = None

    def __post_init__(self) -> None:
        for name in ("venue", "fee_schedule_ref", "fee_schedule_fetched_at_utc", "fee_formula_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "taker_fee_parameters", _freeze_mapping(self.taker_fee_parameters, "taker_fee_parameters"))
        object.__setattr__(self, "maker_fee_parameters", _freeze_mapping(self.maker_fee_parameters, "maker_fee_parameters"))


@dataclass(frozen=True)
class VenueCapabilities(JsonContract):
    venue: str
    protocol_version: str
    client_version: str
    collateral_asset: str
    supported_order_types: tuple[str, ...]
    post_only_order_types: tuple[str, ...]
    price_precision: int
    size_precision: int
    amount_precision_by_order_type: Mapping[str, int]
    gtd_security_threshold_sec: int
    capabilities_fetched_at_utc: str
    fee_schedule_ref: str

    def __post_init__(self) -> None:
        for name in ("venue", "protocol_version", "client_version", "collateral_asset", "capabilities_fetched_at_utc", "fee_schedule_ref"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "supported_order_types", tuple(_required(value, "supported_order_type") for value in self.supported_order_types))
        object.__setattr__(self, "post_only_order_types", tuple(_required(value, "post_only_order_type") for value in self.post_only_order_types))
        object.__setattr__(self, "amount_precision_by_order_type", MappingProxyType({str(key): int(value) for key, value in self.amount_precision_by_order_type.items()}))
        if not set(self.post_only_order_types).issubset(self.supported_order_types):
            raise ExecutionContractError("post_only_order_types must be supported")
        if min(self.price_precision, self.size_precision, self.gtd_security_threshold_sec) < 0:
            raise ExecutionContractError("venue precision and GTD threshold cannot be negative")


@dataclass(frozen=True)
class ExecutionLegProfile(JsonContract):
    role: str
    execution_policy: str
    order_lifecycle_policy: str
    maker_only: bool
    share_fraction: float = 1.0
    reprice_policy: str = "none"
    price_cap_policy: str = "none"
    max_reprices: int | None = 0
    quote_policy: str = ""
    lifecycle_policy: str = ""
    venue_policy: str = "limit_order_v1"
    required_venue_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("role", "execution_policy", "order_lifecycle_policy"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if not 0 < float(self.share_fraction) <= 1:
            raise ExecutionContractError("share_fraction must be in (0, 1]")
        if self.max_reprices is not None and self.max_reprices < 0:
            raise ExecutionContractError("max_reprices cannot be negative")
        object.__setattr__(self, "quote_policy", self.quote_policy or self.execution_policy)
        object.__setattr__(self, "lifecycle_policy", self.lifecycle_policy or self.order_lifecycle_policy)
        object.__setattr__(self, "required_venue_capabilities", tuple(self.required_venue_capabilities))


@dataclass(frozen=True)
class ExecutionProfile(JsonContract):
    name: str
    legs: tuple[ExecutionLegProfile, ...]
    cancel_buffer_sec: int
    planner_supported: bool = True
    allocation_policy: str = "all_taker"
    parameter_requirements: tuple[str, ...] = ()
    allowed_parameters: tuple[str, ...] = ()
    required_venue_capabilities: tuple[str, ...] = ()
    fee_model_version: str = "venue_snapshot_v1"
    tick_model_version: str = "venue_snapshot_v1"
    refresh_sec: int | None = None
    ttl_sec: int | None = None
    data_epoch_policy: str = "cancel"
    fixed_parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "name"))
        object.__setattr__(self, "legs", tuple(self.legs))
        if not self.legs or any(not isinstance(leg, ExecutionLegProfile) for leg in self.legs):
            raise ExecutionContractError("profile requires one or more ExecutionLegProfile values")
        if self.cancel_buffer_sec < 0:
            raise ExecutionContractError("cancel_buffer_sec cannot be negative")
        if self.refresh_sec is not None and self.refresh_sec <= 0:
            raise ExecutionContractError("refresh_sec must be positive")
        if self.ttl_sec is not None and self.ttl_sec <= 0:
            raise ExecutionContractError("ttl_sec must be positive")
        if self.data_epoch_policy not in {"cancel", "revalidate_and_refresh"}:
            raise ExecutionContractError("unsupported data_epoch_policy")
        if self.allocation_policy not in {
            "all_taker",
            "all_maker",
            "fixed_weight_split",
            "explicit_leg_shares",
            "depth_adaptive_taker_then_maker",
        }:
            raise ExecutionContractError(f"unsupported allocation_policy: {self.allocation_policy}")
        if self.allocation_policy == "all_taker" and any(leg.maker_only for leg in self.legs):
            raise ExecutionContractError("all_taker profile cannot contain maker-only legs")
        if self.allocation_policy == "all_maker" and any(not leg.maker_only for leg in self.legs):
            raise ExecutionContractError("all_maker profile cannot contain taker legs")
        if self.allocation_policy == "fixed_weight_split":
            share_fraction_total = sum(
                (_declared_fraction_decimal(leg.share_fraction, "share_fraction") for leg in self.legs),
                Decimal("0"),
            )
            if share_fraction_total != Decimal("1"):
                raise ExecutionContractError("fixed_weight_split share fractions must sum to exactly 1")
        object.__setattr__(self, "parameter_requirements", tuple(self.parameter_requirements))
        object.__setattr__(self, "allowed_parameters", tuple(self.allowed_parameters))
        object.__setattr__(self, "required_venue_capabilities", tuple(self.required_venue_capabilities))
        object.__setattr__(
            self,
            "fixed_parameters",
            _freeze_mapping(self.fixed_parameters, "fixed_parameters"),
        )

    @property
    def execution_policy(self) -> str:
        return self.legs[0].execution_policy if len(self.legs) == 1 else ""

    @property
    def order_lifecycle_policy(self) -> str:
        return self.legs[0].order_lifecycle_policy if len(self.legs) == 1 else ""

    @property
    def maker_only(self) -> bool:
        return bool(self.legs) and all(leg.maker_only for leg in self.legs)

    def fixed_behavior(self, profile_parameters: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        parameters = _freeze_mapping(profile_parameters, "profile_parameters")
        if _LIVE_CONTROL_FIELDS.intersection(parameters):
            raise ExecutionContractError("profile parameters cannot control execution mode")
        unknown = set(parameters) - set(self.allowed_parameters)
        missing = set(self.parameter_requirements) - set(parameters)
        if unknown:
            raise ExecutionContractError(f"profile {self.name} received undeclared parameters: {sorted(unknown)}")
        if missing:
            raise ExecutionContractError(f"profile {self.name} is missing required parameters: {sorted(missing)}")
        behavior = {
            "allocation_policy": self.allocation_policy,
            "cancel_buffer_sec": self.cancel_buffer_sec,
            "fee_model_version": self.fee_model_version,
            "legs": tuple(leg.to_json() for leg in self.legs),
            "profile_parameters": parameters,
            "required_venue_capabilities": self.required_venue_capabilities,
            "tick_model_version": self.tick_model_version,
        }
        # Preserve established config identities for profiles using the original
        # defaults; only profiles that opt into runtime cadence semantics carry
        # these fields in their identity.
        if self.data_epoch_policy != "cancel":
            behavior["data_epoch_policy"] = self.data_epoch_policy
        if self.refresh_sec is not None:
            behavior["refresh_sec"] = self.refresh_sec
        if self.ttl_sec is not None:
            behavior["ttl_sec"] = self.ttl_sec
        if self.fixed_parameters:
            behavior["fixed_parameters"] = self.fixed_parameters
        return MappingProxyType(behavior)


@dataclass(frozen=True)
class ExecutionIntent(JsonContract):
    execution_schema_version: str
    signal_id: str
    opportunity_id: str
    comparison_group_id: str
    strategy_id: str
    strategy_instance: str
    config_id: str
    execution_profile: str
    resolved_execution_profile: str
    execution_config_id: str
    plan_dedupe_key: str
    live_exposure_key: str
    token_id: str
    venue_side: str
    outcome_side: str
    signal_side: str
    total_shares: Decimal | str | int
    created_at_utc: str
    constraints: ExecutionConstraints = field(default_factory=ExecutionConstraints)
    model_token_probability: Decimal | str | int | None = None
    fair_value: Decimal | str | int | None = None
    strategy_price_floor: Decimal | str | int | None = None
    strategy_price_cap: Decimal | str | int | None = None
    required_edge: Decimal | str | int | None = None
    required_depth: Decimal | str | int | None = None
    book_max_age_sec: Decimal | str | int | None = None
    data_source: str | None = None
    data_epoch_ref: str | None = None
    data_epoch_ts_utc: str | None = None
    next_data_update_due_utc: str | None = None
    cancel_buffer_sec: int | None = None
    leg_share_overrides: Mapping[str, Decimal | str | int] = field(default_factory=dict)
    profile_parameters: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "execution_schema_version",
            "signal_id",
            "opportunity_id",
            "comparison_group_id",
            "strategy_id",
            "strategy_instance",
            "config_id",
            "execution_profile",
            "resolved_execution_profile",
            "execution_config_id",
            "plan_dedupe_key",
            "live_exposure_key",
            "token_id",
            "signal_side",
            "created_at_utc",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        venue_side = _required(self.venue_side, "venue_side").upper()
        outcome_side = _required(self.outcome_side, "outcome_side").upper()
        if venue_side not in {"BUY", "SELL"}:
            raise ExecutionContractError("venue_side must be BUY or SELL")
        if outcome_side not in {"YES", "NO"}:
            raise ExecutionContractError("outcome_side must be YES or NO")
        object.__setattr__(self, "venue_side", venue_side)
        object.__setattr__(self, "outcome_side", outcome_side)
        object.__setattr__(self, "total_shares", _decimal(self.total_shares, "total_shares"))
        if self.total_shares <= 0:
            raise ExecutionContractError("total_shares must be positive")
        if not isinstance(self.constraints, ExecutionConstraints):
            raise ExecutionContractError("constraints must be ExecutionConstraints")
        for name in (
            "model_token_probability",
            "fair_value",
            "strategy_price_floor",
            "strategy_price_cap",
            "required_edge",
            "required_depth",
            "book_max_age_sec",
        ):
            object.__setattr__(self, name, _optional_decimal(getattr(self, name), name))
        if self.strategy_price_floor is not None and self.strategy_price_cap is not None and self.strategy_price_floor > self.strategy_price_cap:
            raise ExecutionContractError("strategy_price_floor cannot exceed strategy_price_cap")
        if self.cancel_buffer_sec is not None and self.cancel_buffer_sec < 0:
            raise ExecutionContractError("cancel_buffer_sec cannot be negative")
        leg_overrides = {str(key): _decimal(value, f"leg_share_overrides.{key}") for key, value in self.leg_share_overrides.items()}
        if any(value <= 0 for value in leg_overrides.values()):
            raise ExecutionContractError("leg share overrides must be positive")
        object.__setattr__(self, "leg_share_overrides", MappingProxyType(leg_overrides))
        object.__setattr__(self, "profile_parameters", _freeze_mapping(self.profile_parameters, "profile_parameters"))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, "metadata"))
        if _LIVE_CONTROL_FIELDS.intersection(self.profile_parameters) or _LIVE_CONTROL_FIELDS.intersection(self.metadata):
            raise ExecutionContractError("intent metadata and profile parameters cannot control execution mode")

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ExecutionIntent":
        values = dict(payload)
        values["constraints"] = ExecutionConstraints(**values.get("constraints", {}))
        return cls(**values)


@dataclass(frozen=True)
class ChildOrderPlan(JsonContract):
    intent: ExecutionIntent
    child_role: str
    requested_shares: Decimal | str | int
    execution_policy: str
    order_lifecycle_policy: str
    maker_only: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "child_role", _required(self.child_role, "child_role"))
        object.__setattr__(self, "requested_shares", _decimal(self.requested_shares, "requested_shares"))
        if self.requested_shares <= 0:
            raise ExecutionContractError("requested_shares must be positive")


@dataclass(frozen=True)
class RestingOrderState(JsonContract):
    order_id: str | None
    client_order_id: str
    expected_venue_order_id: str | None
    root_order_id: str | None
    source_order_id: str | None
    plan_id: str
    token_id: str
    venue_side: str
    outcome_side: str
    requested_shares: Decimal | str | int
    matched_shares: Decimal | str | int
    remaining_shares: Decimal | str | int
    posted_price: Decimal | str | int
    status: str
    created_at_utc: str
    maker_only: bool
    execution_profile: str
    execution_policy: str
    order_lifecycle_policy: str
    reprice_count: int
    data_epoch_ref: str | None = None
    authoritative_state_version: str | None = None
    lifecycle_owner: str | None = None
    cancel_confirmed: bool = False
    raw_venue_status: str | None = None
    order_state_provenance: str | None = None

    def __post_init__(self) -> None:
        for name in ("client_order_id", "plan_id", "token_id", "status", "created_at_utc", "execution_profile", "execution_policy", "order_lifecycle_policy"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        for name in ("requested_shares", "matched_shares", "remaining_shares", "posted_price"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name))
        if self.remaining_shares < 0 or self.matched_shares < 0 or self.requested_shares <= 0:
            raise ExecutionContractError("resting order shares are invalid")
        if self.matched_shares + self.remaining_shares > self.requested_shares:
            raise ExecutionContractError("matched plus remaining shares exceed requested shares")
        if self.reprice_count < 0:
            raise ExecutionContractError("reprice_count cannot be negative")
        if self.raw_venue_status is not None:
            object.__setattr__(self, "raw_venue_status", _required(self.raw_venue_status, "raw_venue_status"))
        if self.order_state_provenance is not None:
            object.__setattr__(self, "order_state_provenance", _required(self.order_state_provenance, "order_state_provenance"))


@dataclass(frozen=True)
class LifecycleContext(JsonContract):
    now_utc: str
    market_book: MarketBook | None = None
    data_epoch_ref: str | None = None
    deadline_utc: str | None = None
    facts: Mapping[str, Any] = field(default_factory=dict)
    lifecycle_owner: str | None = None
    thesis_valid: bool = False
    token_unchanged: bool = True
    book_fresh: bool = False
    price_cap_valid: bool = False
    depth_valid: bool = False
    fee_adjusted_edge_valid: bool = False
    maker_price_cap: Decimal | str | int | None = None
    taker_price_cap: Decimal | str | int | None = None
    repost_price: Decimal | str | int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "now_utc", _required(self.now_utc, "now_utc"))
        object.__setattr__(self, "facts", _freeze_mapping(self.facts, "facts"))
        for name in ("maker_price_cap", "taker_price_cap", "repost_price"):
            object.__setattr__(self, name, _optional_decimal(getattr(self, name), name))


@dataclass(frozen=True)
class LifecycleDecision(JsonContract):
    action: str
    reason: str
    replacement_shares: Decimal | str | int | None = None
    replacement_price: Decimal | str | int | None = None
    lifecycle_action_id: str | None = None
    root_order_id: str | None = None
    source_order_id: str | None = None
    authoritative_state_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _required(self.action, "action"))
        object.__setattr__(self, "reason", _required(self.reason, "reason"))
        object.__setattr__(self, "replacement_shares", _optional_decimal(self.replacement_shares, "replacement_shares"))
        object.__setattr__(self, "replacement_price", _optional_decimal(self.replacement_price, "replacement_price"))


@dataclass(frozen=True)
class ExecutionAction(JsonContract):
    action_id: str
    action_type: str
    plan_id: str
    root_order_id: str | None
    created_at_utc: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("action_id", "action_type", "plan_id", "created_at_utc"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "payload", _freeze_mapping(self.payload, "payload"))


@dataclass(frozen=True)
class ExecutionRunContext(JsonContract):
    run_id: str
    execution_mode: str
    run_purpose: str
    dry_run: bool
    confirm_live: bool
    pause_state: str
    authorization_ref: str | None
    runtime_owner: str
    code_commit: str
    invoked_at_utc: str

    def __post_init__(self) -> None:
        for name in ("run_id", "execution_mode", "run_purpose", "pause_state", "runtime_owner", "code_commit", "invoked_at_utc"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.execution_mode not in {"snapshot_replay", "paper", "live"}:
            raise ExecutionContractError("execution_mode must be snapshot_replay, paper, or live")
        if self.run_purpose not in {"replay", "comparator", "shadow", "live_probe", "production"}:
            raise ExecutionContractError("unsupported run_purpose")
        self.validate_submission()

    def validate_submission(self) -> None:
        if self.execution_mode != "live":
            return
        if self.dry_run:
            raise ExecutionContractError("live execution cannot be dry_run")
        if not self.confirm_live:
            raise ExecutionContractError("live execution requires confirm_live")
        if self.pause_state != "unpaused":
            raise ExecutionContractError("live execution requires unpaused runtime")
        if not str(self.authorization_ref or "").strip():
            raise ExecutionContractError("live execution requires authorization_ref")


def make_execution_config_id(*, resolved_execution_profile: str, fixed_behavior: Mapping[str, Any]) -> str:
    """Hash only fixed execution behavior; transient opportunity evidence is rejected."""
    profile = _required(resolved_execution_profile, "resolved_execution_profile")
    if _TRANSIENT_CONFIG_FIELDS.intersection(fixed_behavior):
        forbidden = sorted(_TRANSIENT_CONFIG_FIELDS.intersection(fixed_behavior))
        raise ExecutionContractError(f"transient values cannot enter execution_config_id: {forbidden}")
    return _identity_hash("execution_config", {"resolved_execution_profile": profile, "fixed_behavior": fixed_behavior})


def make_plan_dedupe_key(
    *,
    strategy_id: str,
    strategy_instance: str,
    config_id: str,
    opportunity_id: str,
    execution_profile: str,
    child_role: str,
) -> str:
    return _identity_hash(
        "plan_dedupe",
        {
            "strategy_id": _required(strategy_id, "strategy_id"),
            "strategy_instance": _required(strategy_instance, "strategy_instance"),
            "config_id": _required(config_id, "config_id"),
            "opportunity_id": _required(opportunity_id, "opportunity_id"),
            "execution_profile": _required(execution_profile, "execution_profile"),
            "child_role": _required(child_role, "child_role"),
        },
    )


def make_live_exposure_key(
    *,
    authorized_scope: str,
    opportunity_id: str,
    token_id: str,
    venue_side: str,
    outcome_side: str,
) -> str:
    venue = _required(venue_side, "venue_side").upper()
    outcome = _required(outcome_side, "outcome_side").upper()
    if venue not in {"BUY", "SELL"} or outcome not in {"YES", "NO"}:
        raise ExecutionContractError("live exposure side must be BUY/SELL and YES/NO")
    return _identity_hash(
        "live_exposure",
        {
            "authorized_scope": _required(authorized_scope, "authorized_scope"),
            "opportunity_id": _required(opportunity_id, "opportunity_id"),
            "token_id": _required(token_id, "token_id"),
            "venue_side": venue,
            "outcome_side": outcome,
        },
    )
