"""Pure compatibility planning helpers used while dormant weather runners dual-run in tests."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .contracts import (
    ChildOrderPlan,
    ExecutionConstraints,
    ExecutionIntent,
    make_execution_config_id,
    make_live_exposure_key,
    make_plan_dedupe_key,
)
from .profiles import execution_config_id_for_profile, resolve_execution_profile


class LegacyPlanCompatibilityError(ValueError):
    """A legacy plan cannot be represented without guessing execution semantics."""


def _required(plan: Mapping[str, Any], name: str) -> str:
    value = plan.get(name)
    text = "" if value is None else str(value).strip()
    if not text:
        raise LegacyPlanCompatibilityError(f"missing legacy plan field: {name}")
    return text


def _decimal_text(value: Any, name: str) -> str:
    if isinstance(value, bool) or value is None:
        raise LegacyPlanCompatibilityError(f"invalid legacy plan decimal field: {name}")
    try:
        return format(Decimal(str(value)), "f")
    except (InvalidOperation, ValueError) as exc:
        raise LegacyPlanCompatibilityError(f"invalid legacy plan decimal field: {name}") from exc


def _optional_decimal_text(value: Any, name: str) -> str | None:
    return None if value in (None, "") else _decimal_text(value, name)


@dataclass(frozen=True)
class LegacyPlanCompatibility:
    """Shared contracts paired with the IDs of their authoritative legacy plans."""

    intents: tuple[ExecutionIntent, ...]
    children: tuple[ChildOrderPlan, ...]
    legacy_plan_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.intents or len(self.intents) != len(self.children) or len(self.intents) != len(self.legacy_plan_ids):
            raise LegacyPlanCompatibilityError("compatibility output requires one intent/child/legacy ID per child")


# Keep the runner-specific names stable while sharing one output contract.
D1LegacyPlanCompatibility = LegacyPlanCompatibility
LowPriceLegacyPlanCompatibility = LegacyPlanCompatibility
HeatDeathLegacyPlanCompatibility = LegacyPlanCompatibility
CoreCarryLegacyPlanCompatibility = LegacyPlanCompatibility


@dataclass(frozen=True)
class LegacyPlanFieldDifference:
    """One in-memory field difference between legacy and standardized plans."""

    plan_index: int
    field: str
    legacy_value: str | bool | None
    standardized_value: str | bool | None


def build_d1_legacy_plan_compatibility(
    *,
    legacy_plans: Sequence[Mapping[str, Any]],
    event: Mapping[str, Any],
) -> D1LegacyPlanCompatibility:
    """Convert authoritative legacy D1 plans to contracts without executing or replanning them.

    ``legacy_plans`` must come from the runner's existing ``build_live_plan(s)``.
    This function deliberately consumes their already-selected quote, cap, TTL and
    data-update fields instead of recomputing D1 strategy policy.
    """

    if not legacy_plans:
        raise LegacyPlanCompatibilityError("legacy D1 compatibility requires at least one plan")
    intents: list[ExecutionIntent] = []
    children: list[ChildOrderPlan] = []
    legacy_ids: list[str] = []
    for plan in legacy_plans:
        role = _required(plan, "child_order_role")
        if role not in {"taker", "maker"}:
            raise LegacyPlanCompatibilityError(f"unsupported legacy D1 child role: {role}")
        configured_profile = _required(plan, "execution_profile")
        resolution = resolve_execution_profile(configured_profile)
        maker_only = bool(plan.get("maker_only", False))
        if maker_only != (role == "maker"):
            raise LegacyPlanCompatibilityError("legacy D1 child role and maker_only disagree")
        venue_side = _required(plan, "order_side").upper()
        if venue_side != "BUY" or _required(plan, "signal_side") != "BUY_YES":
            raise LegacyPlanCompatibilityError("legacy D1 compatibility only supports BUY_YES plans")
        legacy_plan_id = _required(plan, "plan_id")
        signal_id = _required(plan, "signal_id")
        opportunity_id = str(plan.get("opportunity_id") or signal_id)
        strategy_id = _required(plan, "strategy_id")
        strategy_instance = _required(plan, "strategy_instance")
        config_id = str(plan.get("config_id") or "legacy_d1_yes_high_mid_v1")
        token_id = _required(plan, "token_id")
        shares = _decimal_text(plan.get("size"), "size")
        price_cap = _decimal_text(plan.get("max_live_price") if maker_only else plan.get("limit_price"), "price_cap")
        deadline = str(plan.get("expires_at_utc") or "") or None
        data_source = plan.get("data_update_source") or event.get("obs_source")
        intent = ExecutionIntent(
            execution_schema_version="weather_execution_v1",
            signal_id=signal_id,
            opportunity_id=opportunity_id,
            comparison_group_id=_required(plan, "comparison_group_id"),
            strategy_id=strategy_id,
            strategy_instance=strategy_instance,
            config_id=config_id,
            execution_profile=resolution.execution_profile,
            resolved_execution_profile=resolution.resolved_execution_profile,
            execution_config_id=execution_config_id_for_profile(configured_profile),
            plan_dedupe_key=make_plan_dedupe_key(
                strategy_id=strategy_id,
                strategy_instance=strategy_instance,
                config_id=config_id,
                opportunity_id=opportunity_id,
                execution_profile=resolution.resolved_execution_profile,
                child_role=role,
            ),
            live_exposure_key=make_live_exposure_key(
                authorized_scope=strategy_instance,
                opportunity_id=opportunity_id,
                token_id=token_id,
                venue_side="BUY",
                outcome_side="YES",
            ),
            token_id=token_id,
            venue_side="BUY",
            outcome_side="YES",
            signal_side="BUY_YES",
            total_shares=shares,
            created_at_utc=_required(plan, "created_at_utc"),
            constraints=ExecutionConstraints(
                price_cap=price_cap,
                minimum_shares=_optional_decimal_text(plan.get("fixed_order_shares"), "fixed_order_shares"),
                maximum_shares=_optional_decimal_text(plan.get("max_order_shares"), "max_order_shares"),
                deadline_utc=deadline,
            ),
            model_token_probability=_optional_decimal_text(plan.get("model_token_probability"), "model_token_probability"),
            strategy_price_cap=price_cap,
            data_source=None if data_source in (None, "") else str(data_source),
            data_epoch_ref=plan.get("data_epoch_ref"),
            data_epoch_ts_utc=plan.get("data_epoch_ts_utc"),
            next_data_update_due_utc=plan.get("next_data_update_due_utc"),
            cancel_buffer_sec=None if plan.get("cancel_buffer_sec") is None else int(plan["cancel_buffer_sec"]),
            metadata={
                "legacy_plan_id": legacy_plan_id,
                "legacy_record_type": str(plan.get("record_type") or ""),
                "legacy_order_ttl_min": _optional_decimal_text(plan.get("order_ttl_min"), "order_ttl_min"),
                "legacy_limit_price": _decimal_text(plan.get("limit_price"), "limit_price"),
                "legacy_maker_price_cap": _optional_decimal_text(plan.get("maker_price_cap"), "maker_price_cap"),
            },
        )
        child = ChildOrderPlan(
            intent=intent,
            child_role=role,
            requested_shares=shares,
            execution_policy=_required(plan, "execution_policy"),
            order_lifecycle_policy=_required(plan, "order_lifecycle_policy"),
            maker_only=maker_only,
        )
        intents.append(intent)
        children.append(child)
        legacy_ids.append(legacy_plan_id)
    return D1LegacyPlanCompatibility(tuple(intents), tuple(children), tuple(legacy_ids))


def build_low_price_legacy_plan_compatibility(
    *,
    legacy_plan: Mapping[str, Any],
    configured_execution_profile: str,
) -> LowPriceLegacyPlanCompatibility:
    """Convert one accepted low-price entry plan without replanning its execution.

    The legacy entry plan deliberately contains no TTL/deadline or lifecycle
    policy.  Those absences are carried as explicit metadata rather than
    inferred from the runner's separate lifecycle loop.
    """

    status = _required(legacy_plan, "status").lower()
    if status != "accepted":
        raise LegacyPlanCompatibilityError(f"low-price compatibility requires an accepted legacy plan, got: {status}")
    role = _required(legacy_plan, "child_order_role")
    if role != "maker_first" or not bool(legacy_plan.get("maker_only")):
        raise LegacyPlanCompatibilityError("low-price compatibility only supports planned maker_first plans")
    venue_side = _required(legacy_plan, "order_side").upper()
    signal_side = _required(legacy_plan, "signal_side")
    if venue_side != "BUY" or signal_side != "BUY_YES":
        raise LegacyPlanCompatibilityError("low-price compatibility only supports BUY_YES plans")

    resolution = resolve_execution_profile(configured_execution_profile)
    legacy_plan_id = _required(legacy_plan, "plan_id")
    signal_id = _required(legacy_plan, "signal_id")
    strategy_id = _required(legacy_plan, "strategy_id")
    strategy_instance = _required(legacy_plan, "strategy_instance")
    config_id = _required(legacy_plan, "combo")
    token_id = _required(legacy_plan, "token_id")
    shares = _decimal_text(legacy_plan.get("size"), "size")
    maker_price = _decimal_text(legacy_plan.get("maker_limit_price"), "maker_limit_price")
    limit_price = _decimal_text(legacy_plan.get("limit_price"), "limit_price")
    if Decimal(maker_price) != Decimal(limit_price):
        raise LegacyPlanCompatibilityError("low-price maker_limit_price and limit_price disagree")
    required_edge = _optional_decimal_text(legacy_plan.get("required_quote_edge"), "required_quote_edge")
    deadline = str(legacy_plan.get("expires_at_utc") or "") or None
    order_ttl_min = _optional_decimal_text(legacy_plan.get("order_ttl_min"), "order_ttl_min")
    lifecycle_policy = str(legacy_plan.get("order_lifecycle_policy") or "") or None

    intent = ExecutionIntent(
        execution_schema_version="weather_execution_v1",
        signal_id=signal_id,
        opportunity_id=signal_id,
        comparison_group_id=signal_id,
        strategy_id=strategy_id,
        strategy_instance=strategy_instance,
        config_id=config_id,
        execution_profile=resolution.execution_profile,
        resolved_execution_profile=resolution.resolved_execution_profile,
        execution_config_id=execution_config_id_for_profile(configured_execution_profile),
        plan_dedupe_key=make_plan_dedupe_key(
            strategy_id=strategy_id,
            strategy_instance=strategy_instance,
            config_id=config_id,
            opportunity_id=signal_id,
            execution_profile=resolution.resolved_execution_profile,
            child_role=role,
        ),
        live_exposure_key=make_live_exposure_key(
            authorized_scope=strategy_instance,
            opportunity_id=signal_id,
            token_id=token_id,
            venue_side="BUY",
            outcome_side="YES",
        ),
        token_id=token_id,
        venue_side="BUY",
        outcome_side="YES",
        signal_side="BUY_YES",
        total_shares=shares,
        created_at_utc=_required(legacy_plan, "created_at_utc"),
        constraints=ExecutionConstraints(
            price_cap=maker_price,
            required_edge=required_edge,
            minimum_shares=_optional_decimal_text(legacy_plan.get("fixed_order_shares"), "fixed_order_shares"),
            maximum_shares=_optional_decimal_text(legacy_plan.get("max_order_shares"), "max_order_shares"),
            deadline_utc=deadline,
        ),
        model_token_probability=_optional_decimal_text(
            legacy_plan.get("model_token_probability"), "model_token_probability"
        ),
        strategy_price_cap=maker_price,
        required_edge=required_edge,
        data_source=str(legacy_plan.get("obs_source") or "") or None,
        data_epoch_ref=str(legacy_plan.get("data_epoch_ref") or "") or None,
        data_epoch_ts_utc=str(legacy_plan.get("data_epoch_ts_utc") or "") or None,
        next_data_update_due_utc=str(legacy_plan.get("next_data_update_due_utc") or "") or None,
        cancel_buffer_sec=legacy_plan.get("cancel_buffer_sec"),
        metadata={
            "legacy_plan_id": legacy_plan_id,
            "legacy_profile": _required(legacy_plan, "profile"),
            "legacy_record_type": str(legacy_plan.get("record_type") or ""),
            "legacy_maker_limit_price": maker_price,
            "legacy_taker_limit_price": _optional_decimal_text(
                legacy_plan.get("taker_limit_price"), "taker_limit_price"
            ),
            "legacy_taker_fallback_status": str(legacy_plan.get("taker_fallback_status") or ""),
            "legacy_taker_fallback_notional_usd": _optional_decimal_text(
                legacy_plan.get("taker_fallback_notional_usd"), "taker_fallback_notional_usd"
            ),
            "legacy_order_ttl_min": order_ttl_min,
            "legacy_order_lifecycle_policy": lifecycle_policy,
            "legacy_lifecycle_embedded": bool(deadline or order_ttl_min or lifecycle_policy),
        },
    )
    child = ChildOrderPlan(
        intent=intent,
        child_role=role,
        requested_shares=shares,
        execution_policy=_required(legacy_plan, "execution_policy"),
        order_lifecycle_policy=lifecycle_policy or "legacy_plan_no_embedded_lifecycle_v1",
        maker_only=True,
    )
    return LowPriceLegacyPlanCompatibility((intent,), (child,), (legacy_plan_id,))


def build_heat_death_legacy_plan_compatibility(
    *,
    legacy_plans: Sequence[Mapping[str, Any]],
) -> HeatDeathLegacyPlanCompatibility:
    """Convert authoritative heat-death entry plans without entering lifecycle flow.

    The runner's lifecycle replacement/cancel plans carry source-order chains.
    They are intentionally rejected here because they are not new opportunities
    and representing them as such would guess at replacement semantics.
    """

    if not legacy_plans:
        raise LegacyPlanCompatibilityError("heat-death compatibility requires at least one legacy entry plan")
    intents: list[ExecutionIntent] = []
    children: list[ChildOrderPlan] = []
    legacy_ids: list[str] = []
    lifecycle_fields = (
        "execution_action",
        "cancel_before_order_id",
        "source_order_id",
        "source_plan_id",
        "source_execution_id",
        "replacement_requires_order_state",
        "cancel_only",
    )
    for plan in legacy_plans:
        if any(plan.get(field) not in (None, "", False) for field in lifecycle_fields):
            raise LegacyPlanCompatibilityError("heat-death entry bridge rejects lifecycle replacement/cancel plans")
        if _required(plan, "status").lower() != "accepted":
            raise LegacyPlanCompatibilityError("heat-death compatibility requires accepted legacy entry plans")
        role = _required(plan, "child_order_role")
        if role not in {"single", "taker", "maker"}:
            raise LegacyPlanCompatibilityError(f"unsupported heat-death entry child role: {role}")
        maker_only = bool(plan.get("maker_only"))
        if maker_only != (role == "maker"):
            raise LegacyPlanCompatibilityError("heat-death child role and maker_only disagree")
        venue_side = _required(plan, "order_side").upper()
        signal_side = _required(plan, "signal_side")
        if venue_side != "BUY" or signal_side != "BUY_YES":
            raise LegacyPlanCompatibilityError("heat-death compatibility only supports BUY_YES plans")

        configured_profile = _required(plan, "execution_profile")
        resolution = resolve_execution_profile(configured_profile)
        legacy_plan_id = _required(plan, "plan_id")
        signal_id = _required(plan, "signal_id")
        opportunity_id = str(plan.get("opportunity_id") or signal_id)
        comparison_group_id = _required(plan, "comparison_group_id")
        strategy_id = _required(plan, "strategy_id")
        strategy_instance = _required(plan, "strategy_instance")
        config_id = _required(plan, "config_id")
        token_id = _required(plan, "token_id")
        shares = _decimal_text(plan.get("size"), "size")
        limit_price = _decimal_text(plan.get("limit_price"), "limit_price")
        maker_price_cap = _optional_decimal_text(plan.get("maker_price_cap"), "maker_price_cap")
        if maker_only and maker_price_cap is None:
            raise LegacyPlanCompatibilityError("heat-death maker entry requires maker_price_cap")
        price_cap = maker_price_cap if maker_only else limit_price
        if maker_only and Decimal(limit_price) > Decimal(price_cap):
            raise LegacyPlanCompatibilityError("heat-death maker limit_price exceeds maker_price_cap")
        lifecycle_policy = _required(plan, "order_lifecycle_policy")
        maker_fallback_eligible = maker_only and lifecycle_policy == "maker_chase_then_taker_fallback_v1"

        intent = ExecutionIntent(
            execution_schema_version="weather_execution_v1",
            signal_id=signal_id,
            opportunity_id=opportunity_id,
            comparison_group_id=comparison_group_id,
            strategy_id=strategy_id,
            strategy_instance=strategy_instance,
            config_id=config_id,
            execution_profile=resolution.execution_profile,
            resolved_execution_profile=resolution.resolved_execution_profile,
            execution_config_id=execution_config_id_for_profile(configured_profile),
            plan_dedupe_key=make_plan_dedupe_key(
                strategy_id=strategy_id,
                strategy_instance=strategy_instance,
                config_id=config_id,
                opportunity_id=opportunity_id,
                execution_profile=resolution.resolved_execution_profile,
                child_role=role,
            ),
            live_exposure_key=make_live_exposure_key(
                authorized_scope=strategy_instance,
                opportunity_id=opportunity_id,
                token_id=token_id,
                venue_side="BUY",
                outcome_side="YES",
            ),
            token_id=token_id,
            venue_side="BUY",
            outcome_side="YES",
            signal_side="BUY_YES",
            total_shares=shares,
            created_at_utc=_required(plan, "created_at_utc"),
            constraints=ExecutionConstraints(
                price_cap=price_cap,
                minimum_shares=_optional_decimal_text(plan.get("fixed_order_shares"), "fixed_order_shares"),
                maximum_shares=_optional_decimal_text(plan.get("max_order_shares"), "max_order_shares"),
                deadline_utc=str(plan.get("expires_at_utc") or "") or None,
            ),
            strategy_price_cap=price_cap,
            data_source=str(plan.get("obs_source") or "") or None,
            metadata={
                "legacy_plan_id": legacy_plan_id,
                "legacy_profile": _required(plan, "profile"),
                "legacy_record_type": str(plan.get("record_type") or ""),
                "legacy_limit_price": limit_price,
                "legacy_maker_price_cap": maker_price_cap,
                "legacy_order_lifecycle_policy": lifecycle_policy,
                "legacy_maker_lifecycle_deadline_utc": str(plan.get("maker_lifecycle_deadline_utc") or "") or None,
                "legacy_maker_fallback_eligible": maker_fallback_eligible,
                "legacy_maker_fallback_price_cap": price_cap if maker_fallback_eligible else None,
            },
        )
        child = ChildOrderPlan(
            intent=intent,
            child_role=role,
            requested_shares=shares,
            execution_policy=_required(plan, "execution_policy"),
            order_lifecycle_policy=lifecycle_policy,
            maker_only=maker_only,
        )
        intents.append(intent)
        children.append(child)
        legacy_ids.append(legacy_plan_id)
    return HeatDeathLegacyPlanCompatibility(tuple(intents), tuple(children), tuple(legacy_ids))


def _core_carry_profile_identity(configured_profile: str) -> tuple[str, str, str, str]:
    """Return an opaque identity for an unregistered legacy profile.

    Core-carry has not yet registered a Phase 1 execution profile.  The
    identity is therefore comparator-only and cannot supply runtime behavior.
    """

    try:
        resolution = resolve_execution_profile(configured_profile)
    except ValueError:
        execution_config_id = make_execution_config_id(
            resolved_execution_profile=configured_profile,
            fixed_behavior={
                "compatibility_mode": "opaque_legacy_profile_v1",
                "legacy_execution_profile": configured_profile,
            },
        )
        return configured_profile, configured_profile, execution_config_id, "opaque_unregistered_legacy_profile_v1"
    return (
        resolution.execution_profile,
        resolution.resolved_execution_profile,
        execution_config_id_for_profile(configured_profile),
        "registered_profile_v1",
    )


def build_core_carry_legacy_plan_compatibility(
    *,
    legacy_plans: Sequence[Mapping[str, Any]],
) -> CoreCarryLegacyPlanCompatibility:
    """Convert core-carry entry plans to comparator-only shared contracts.

    Lifecycle replacements and cancels are intentionally out of scope: they
    have source-order semantics and must not be represented as new entries.
    """

    if not legacy_plans:
        raise LegacyPlanCompatibilityError("core-carry compatibility requires at least one legacy entry plan")
    intents: list[ExecutionIntent] = []
    children: list[ChildOrderPlan] = []
    legacy_ids: list[str] = []
    lifecycle_fields = (
        "execution_action",
        "cancel_before_order_id",
        "source_order_id",
        "source_plan_id",
        "source_execution_id",
        "replacement_requires_order_state",
        "cancel_only",
    )
    for plan in legacy_plans:
        if any(plan.get(field) not in (None, "", False) for field in lifecycle_fields):
            raise LegacyPlanCompatibilityError("core-carry entry bridge rejects lifecycle replacement/cancel plans")
        if _required(plan, "status").lower() != "accepted":
            raise LegacyPlanCompatibilityError("core-carry compatibility requires accepted legacy entry plans")
        role = _required(plan, "child_order_role")
        if role not in {"taker", "maker", "single"}:
            raise LegacyPlanCompatibilityError(f"unsupported core-carry entry child role: {role}")
        maker_only = bool(plan.get("maker_only"))
        if maker_only != (role == "maker"):
            raise LegacyPlanCompatibilityError("core-carry child role and maker_only disagree")
        venue_side = _required(plan, "order_side").upper()
        signal_side = _required(plan, "signal_side")
        if venue_side != "BUY" or signal_side != "BUY_YES":
            raise LegacyPlanCompatibilityError("core-carry compatibility only supports BUY_YES plans")

        configured_profile = _required(plan, "execution_profile")
        execution_profile, resolved_profile, execution_config_id, profile_resolution = _core_carry_profile_identity(
            configured_profile
        )
        legacy_plan_id = _required(plan, "plan_id")
        signal_id = _required(plan, "signal_id")
        opportunity_id = str(plan.get("opportunity_id") or signal_id)
        comparison_group_id = _required(plan, "comparison_group_id")
        strategy_id = _required(plan, "strategy_id")
        strategy_instance = _required(plan, "strategy_instance")
        config_id = _required(plan, "config_id")
        token_id = _required(plan, "token_id")
        shares = _decimal_text(plan.get("size"), "size")
        if Decimal(shares) <= 0:
            raise LegacyPlanCompatibilityError("core-carry compatibility requires positive entry shares")
        limit_price = _decimal_text(plan.get("limit_price"), "limit_price")
        maker_price_cap = _decimal_text(plan.get("maker_price_cap"), "maker_price_cap")
        if maker_only and Decimal(limit_price) > Decimal(maker_price_cap):
            raise LegacyPlanCompatibilityError("core-carry maker limit_price exceeds maker_price_cap")
        price_cap = maker_price_cap if maker_only else limit_price
        lifecycle_policy = _required(plan, "order_lifecycle_policy")
        deadline = str(plan.get("expires_at_utc") or "") or None

        intent = ExecutionIntent(
            execution_schema_version="weather_execution_v1",
            signal_id=signal_id,
            opportunity_id=opportunity_id,
            comparison_group_id=comparison_group_id,
            strategy_id=strategy_id,
            strategy_instance=strategy_instance,
            config_id=config_id,
            execution_profile=execution_profile,
            resolved_execution_profile=resolved_profile,
            execution_config_id=execution_config_id,
            plan_dedupe_key=make_plan_dedupe_key(
                strategy_id=strategy_id,
                strategy_instance=strategy_instance,
                config_id=config_id,
                opportunity_id=opportunity_id,
                execution_profile=resolved_profile,
                child_role=role,
            ),
            live_exposure_key=make_live_exposure_key(
                authorized_scope=strategy_instance,
                opportunity_id=opportunity_id,
                token_id=token_id,
                venue_side="BUY",
                outcome_side="YES",
            ),
            token_id=token_id,
            venue_side="BUY",
            outcome_side="YES",
            signal_side="BUY_YES",
            total_shares=shares,
            created_at_utc=_required(plan, "created_at_utc"),
            constraints=ExecutionConstraints(
                price_cap=price_cap,
                minimum_shares=_optional_decimal_text(plan.get("fixed_order_shares"), "fixed_order_shares"),
                maximum_shares=_optional_decimal_text(plan.get("max_order_shares"), "max_order_shares"),
                deadline_utc=deadline,
            ),
            model_token_probability=_optional_decimal_text(
                plan.get("model_token_probability"), "model_token_probability"
            ),
            strategy_price_cap=price_cap,
            data_epoch_ts_utc=str(plan.get("source_report_ts_utc") or "") or None,
            metadata={
                "legacy_plan_id": legacy_plan_id,
                "legacy_limit_price": limit_price,
                "legacy_maker_price_cap": maker_price_cap,
                "legacy_blocker": str(plan.get("blocker") or ""),
                "legacy_order_lifecycle_policy": lifecycle_policy,
                "legacy_profile_resolution": profile_resolution,
            },
        )
        child = ChildOrderPlan(
            intent=intent,
            child_role=role,
            requested_shares=shares,
            execution_policy=_required(plan, "execution_policy"),
            order_lifecycle_policy=lifecycle_policy,
            maker_only=maker_only,
        )
        intents.append(intent)
        children.append(child)
        legacy_ids.append(legacy_plan_id)
    return CoreCarryLegacyPlanCompatibility(tuple(intents), tuple(children), tuple(legacy_ids))


def compare_core_carry_legacy_plan_fields(
    *,
    legacy_plans: Sequence[Mapping[str, Any]],
    compatibility: CoreCarryLegacyPlanCompatibility,
) -> tuple[LegacyPlanFieldDifference, ...]:
    """Compare the entry fields that must remain identical during opt-in parity."""

    differences: list[LegacyPlanFieldDifference] = []
    if len(legacy_plans) != len(compatibility.children):
        differences.append(
            LegacyPlanFieldDifference(
                plan_index=-1,
                field="child_count",
                legacy_value=str(len(legacy_plans)),
                standardized_value=str(len(compatibility.children)),
            )
        )
    for index, (plan, intent, child) in enumerate(zip(legacy_plans, compatibility.intents, compatibility.children)):
        field_values = (
            ("child_order_role", _required(plan, "child_order_role"), child.child_role),
            ("shares", _decimal_text(plan.get("size"), "size"), format(child.requested_shares, "f")),
            ("limit_price", _decimal_text(plan.get("limit_price"), "limit_price"), intent.metadata["legacy_limit_price"]),
            (
                "maker_price_cap",
                _decimal_text(plan.get("maker_price_cap"), "maker_price_cap"),
                intent.metadata["legacy_maker_price_cap"],
            ),
            ("maker_only", bool(plan.get("maker_only")), child.maker_only),
            ("deadline_utc", str(plan.get("expires_at_utc") or "") or None, intent.constraints.deadline_utc),
            ("order_lifecycle_policy", _required(plan, "order_lifecycle_policy"), child.order_lifecycle_policy),
            ("blocker", str(plan.get("blocker") or ""), intent.metadata["legacy_blocker"]),
        )
        for field, legacy_value, standardized_value in field_values:
            if legacy_value != standardized_value:
                differences.append(
                    LegacyPlanFieldDifference(
                        plan_index=index,
                        field=field,
                        legacy_value=legacy_value,
                        standardized_value=standardized_value,
                    )
                )
    return tuple(differences)
