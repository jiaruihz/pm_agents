from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import (
    ExecutionContractError,
    ExecutionLegProfile,
    ExecutionProfile,
    JsonContract,
    make_execution_config_id,
)


@dataclass(frozen=True)
class ProfileResolution(JsonContract):
    execution_profile: str
    resolved_execution_profile: str
    profile: ExecutionProfile


_PROFILES = {
    "taker_now_v1": ExecutionProfile(
        name="taker_now_v1",
        legs=(
            ExecutionLegProfile(
                role="single",
                execution_policy="taker_top_ask_v1",
                order_lifecycle_policy="taker_now",
                maker_only=False,
            ),
        ),
        cancel_buffer_sec=0,
        allocation_policy="all_taker",
    ),
    "single_side_maker_v1": ExecutionProfile(
        name="single_side_maker_v1",
        legs=(
            ExecutionLegProfile(
                role="single",
                execution_policy="maker_queue_v2",
                order_lifecycle_policy="maker_until_data_update",
                maker_only=True,
            ),
        ),
        cancel_buffer_sec=90,
        allocation_policy="all_maker",
    ),
    "d1_taker_only_v1": ExecutionProfile(
        name="d1_taker_only_v1",
        legs=(
            ExecutionLegProfile(
                role="taker",
                execution_policy="d1_yes_high_mid_taker_v1",
                order_lifecycle_policy="taker_now",
                maker_only=False,
            ),
        ),
        cancel_buffer_sec=0,
        planner_supported=False,
        allocation_policy="all_taker",
    ),
    "d1_taker_plus_maker_static_v1": ExecutionProfile(
        name="d1_taker_plus_maker_static_v1",
        legs=(
            ExecutionLegProfile(
                role="taker",
                execution_policy="d1_yes_high_mid_taker_v1",
                order_lifecycle_policy="taker_now",
                maker_only=False,
                share_fraction=0.5,
            ),
            ExecutionLegProfile(
                role="maker",
                execution_policy="d1_yes_high_mid_maker_v1",
                order_lifecycle_policy="maker_until_data_update",
                maker_only=True,
                share_fraction=0.5,
                price_cap_policy="initial_mid",
            ),
        ),
        cancel_buffer_sec=90,
        planner_supported=False,
        allocation_policy="fixed_weight_split",
    ),
    "d1_taker_plus_maker_chase_to_mid_v1": ExecutionProfile(
        name="d1_taker_plus_maker_chase_to_mid_v1",
        legs=(
            ExecutionLegProfile(
                role="taker",
                execution_policy="d1_yes_high_mid_taker_v1",
                order_lifecycle_policy="taker_now",
                maker_only=False,
                share_fraction=0.5,
            ),
            ExecutionLegProfile(
                role="maker",
                execution_policy="d1_yes_high_mid_maker_v1",
                order_lifecycle_policy="maker_until_data_update",
                maker_only=True,
                share_fraction=0.5,
                reprice_policy="follow_best_bid",
                price_cap_policy="initial_mid",
                max_reprices=3,
            ),
        ),
        cancel_buffer_sec=90,
        planner_supported=False,
        allocation_policy="fixed_weight_split",
    ),
    "split_taker_maker_chase_v1": ExecutionProfile(
        name="split_taker_maker_chase_v1",
        legs=(
            ExecutionLegProfile(
                role="taker",
                execution_policy="current_yes_heat_death_taker_probe_v1",
                order_lifecycle_policy="taker_now",
                maker_only=False,
                share_fraction=0.5,
            ),
            ExecutionLegProfile(
                role="maker",
                execution_policy="current_yes_heat_death_maker_probe_v1",
                order_lifecycle_policy="maker_chase_then_taker_fallback_v1",
                maker_only=True,
                share_fraction=0.5,
            ),
        ),
        cancel_buffer_sec=0,
        planner_supported=False,
        allocation_policy="fixed_weight_split",
    ),
    "split_taker_maker_chase_capped_no_fallback_v1": ExecutionProfile(
        name="split_taker_maker_chase_capped_no_fallback_v1",
        legs=(
            ExecutionLegProfile(
                role="taker",
                execution_policy="current_yes_residual_carry_taker_v1",
                order_lifecycle_policy="taker_now",
                maker_only=False,
            ),
            ExecutionLegProfile(
                role="maker",
                execution_policy="current_yes_residual_carry_maker_v1",
                order_lifecycle_policy="maker_chase_until_observation_or_ttl_v1",
                maker_only=True,
                reprice_policy="follow_best_bid",
                price_cap_policy="minimum_initial_mid_model_probability",
                max_reprices=None,
            ),
        ),
        cancel_buffer_sec=0,
        planner_supported=False,
        allocation_policy="explicit_leg_shares",
        refresh_sec=15,
        ttl_sec=900,
        data_epoch_policy="revalidate_and_refresh",
    ),
    "split_taker_maker_edge_capped_no_fallback_v2": ExecutionProfile(
        name="split_taker_maker_edge_capped_no_fallback_v2",
        legs=(
            ExecutionLegProfile(
                role="taker",
                execution_policy="current_yes_residual_carry_taker_v1",
                order_lifecycle_policy="taker_now",
                maker_only=False,
            ),
            ExecutionLegProfile(
                role="maker",
                execution_policy="current_yes_residual_carry_maker_v2",
                order_lifecycle_policy="maker_staged_chase_until_pre_data_update_or_ttl_v2",
                maker_only=True,
                reprice_policy="deadline_staged_follow_best_bid",
                price_cap_policy="model_probability_retained_edge_and_taker_improvement",
                max_reprices=None,
            ),
        ),
        cancel_buffer_sec=90,
        planner_supported=False,
        allocation_policy="explicit_leg_shares",
        refresh_sec=15,
        ttl_sec=900,
        data_epoch_policy="cancel",
        fixed_parameters={
            "minimum_taker_improvement_ticks": 1,
            "retained_edge": "0.01",
            "stage_midpoint_after_sec": 300,
            "stage_near_ask_after_sec": 600,
        },
    ),
    "split_taker_maker_edge_capped_no_fallback_v3": ExecutionProfile(
        name="split_taker_maker_edge_capped_no_fallback_v3",
        legs=(
            ExecutionLegProfile(
                role="taker",
                execution_policy="current_yes_residual_carry_taker_v1",
                order_lifecycle_policy="taker_now",
                maker_only=False,
            ),
            ExecutionLegProfile(
                role="maker",
                execution_policy="current_yes_residual_carry_maker_v2",
                order_lifecycle_policy="maker_staged_chase_until_pre_data_update_or_ttl_v2",
                maker_only=True,
                reprice_policy="deadline_staged_follow_best_bid",
                price_cap_policy="model_probability_retained_edge_and_taker_improvement",
                max_reprices=2,
            ),
        ),
        cancel_buffer_sec=90,
        planner_supported=False,
        allocation_policy="explicit_leg_shares",
        refresh_sec=15,
        ttl_sec=900,
        data_epoch_policy="cancel",
        fixed_parameters={
            "minimum_taker_improvement_ticks": 1,
            "retained_edge": "0.01",
            "stage_midpoint_after_sec": 300,
            "stage_near_ask_after_sec": 600,
            "clock_basis": "next_source_report_not_collector_availability",
            "post_update_live_rearm": False,
            "post_update_shadow_revalidation": True,
        },
    ),
    "split_taker_maker_event_validated_staged_no_fallback_v4": ExecutionProfile(
        name="split_taker_maker_event_validated_staged_no_fallback_v4",
        legs=(
            ExecutionLegProfile(
                role="taker",
                execution_policy="current_yes_residual_carry_taker_v1",
                order_lifecycle_policy="taker_now",
                maker_only=False,
            ),
            ExecutionLegProfile(
                role="maker",
                execution_policy="current_yes_residual_carry_maker_v2",
                order_lifecycle_policy="maker_event_validated_staged_until_update_or_ttl_v3",
                maker_only=True,
                reprice_policy="deadline_staged_exact_target",
                price_cap_policy="model_probability_retained_edge_and_taker_improvement",
                max_reprices=2,
            ),
        ),
        cancel_buffer_sec=90,
        planner_supported=False,
        allocation_policy="explicit_leg_shares",
        refresh_sec=15,
        ttl_sec=900,
        data_epoch_policy="cancel",
        fixed_parameters={
            "minimum_taker_improvement_ticks": 1,
            "retained_edge": "0.01",
            "stage_midpoint_after_sec": 300,
            "stage_near_ask_after_sec": 600,
            "clock_basis": "next_source_report_not_collector_availability",
            "state_epoch_components": (
                "observation+forecast_curve+exact_bracket_token"
            ),
            "replacement_price_policy": "exact_stage_target_never_down",
            "replacement_max_quote_drift_ticks": 1,
            "post_update_live_rearm": False,
            "post_update_shadow_revalidation": True,
        },
    ),
    "split_taker_two_maker_event_validated_no_fallback_v5": ExecutionProfile(
        name="split_taker_two_maker_event_validated_no_fallback_v5",
        legs=(
            ExecutionLegProfile(
                role="taker",
                execution_policy="current_yes_residual_carry_taker_v1",
                order_lifecycle_policy="taker_now",
                maker_only=False,
            ),
            ExecutionLegProfile(
                role="maker_staged",
                execution_policy="current_yes_residual_carry_staged_maker_v3",
                order_lifecycle_policy="maker_event_validated_staged_until_update_or_ttl_v3",
                maker_only=True,
                reprice_policy="deadline_staged_exact_target",
                price_cap_policy="model_probability_retained_edge_and_taker_improvement",
                max_reprices=2,
            ),
            ExecutionLegProfile(
                role="maker_pullback",
                execution_policy="current_yes_residual_carry_pullback_maker_v1",
                order_lifecycle_policy="maker_event_validated_static_pullback_until_update_or_ttl_v1",
                maker_only=True,
                reprice_policy="none",
                price_cap_policy="entry_ask_minus_fixed_offset_and_model_edge_cap",
                max_reprices=0,
            ),
        ),
        cancel_buffer_sec=90,
        planner_supported=False,
        allocation_policy="explicit_leg_shares",
        refresh_sec=15,
        ttl_sec=900,
        data_epoch_policy="cancel",
        fixed_parameters={
            "minimum_taker_improvement_ticks": 1,
            "retained_edge": "0.01",
            "pullback_offset": "0.02",
            "stage_midpoint_after_sec": 300,
            "stage_near_ask_after_sec": 600,
            "clock_basis": "next_source_report_not_collector_availability",
            "state_epoch_components": (
                "observation+forecast_curve+exact_bracket_token"
            ),
            "replacement_price_policy": "exact_stage_target_never_down",
            "replacement_max_quote_drift_ticks": 1,
            "post_update_live_rearm": False,
            "post_update_shadow_revalidation": True,
            "maker_experiment_id": "core_carry_staged_vs_pullback_maker_ab_20260813",
        },
    ),
    "split_taker_two_maker_event_rearmed_no_fallback_v6": ExecutionProfile(
        name="split_taker_two_maker_event_rearmed_no_fallback_v6",
        legs=(
            ExecutionLegProfile(
                role="taker",
                execution_policy="current_yes_residual_carry_taker_v1",
                order_lifecycle_policy="taker_now",
                maker_only=False,
            ),
            ExecutionLegProfile(
                role="maker_staged",
                execution_policy="current_yes_residual_carry_staged_maker_v3",
                order_lifecycle_policy="maker_event_validated_staged_until_update_or_ttl_v3",
                maker_only=True,
                reprice_policy="deadline_staged_exact_target",
                price_cap_policy="model_probability_retained_edge_and_taker_improvement",
                max_reprices=2,
            ),
            ExecutionLegProfile(
                role="maker_pullback",
                execution_policy="current_yes_residual_carry_pullback_maker_v1",
                order_lifecycle_policy="maker_event_validated_static_pullback_until_update_or_ttl_v1",
                maker_only=True,
                reprice_policy="none",
                price_cap_policy="entry_ask_minus_fixed_offset_and_model_edge_cap",
                max_reprices=0,
            ),
        ),
        cancel_buffer_sec=90,
        planner_supported=False,
        allocation_policy="explicit_leg_shares",
        refresh_sec=15,
        ttl_sec=900,
        data_epoch_policy="cancel",
        fixed_parameters={
            "minimum_taker_improvement_ticks": 1,
            "retained_edge": "0.01",
            "pullback_offset": "0.02",
            "stage_midpoint_after_sec": 300,
            "stage_near_ask_after_sec": 600,
            "clock_basis": "next_source_report_not_collector_availability",
            "state_epoch_components": (
                "observation+forecast_curve+exact_bracket_token"
            ),
            "replacement_price_policy": "exact_stage_target_never_down",
            "replacement_max_quote_drift_ticks": 1,
            "post_update_live_rearm": True,
            "post_update_rearm_max_age_sec": 3600,
            "post_update_shadow_revalidation": True,
            "maker_experiment_id": "core_carry_staged_vs_pullback_maker_rearm_ab_20260813",
        },
    ),
}

# Alias values are (resolved profile name, fixture identity). Keep this empty
# until a historical name is proven fixture-identical to a registered bundle.
_PROFILE_ALIASES: dict[str, tuple[str, str]] = {}


def execution_profile_names() -> tuple[str, ...]:
    return tuple(name for name, profile in _PROFILES.items() if profile.planner_supported)


def get_execution_profile(name: str) -> ExecutionProfile:
    key = str(name or "").strip().lower()
    try:
        return _PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"unknown execution profile: {name!r}") from exc


def profile_fixture_identity(profile: ExecutionProfile) -> str:
    """Return the immutable behavior fingerprint used to guard aliases."""
    return make_execution_config_id(
        resolved_execution_profile=profile.name,
        fixed_behavior=profile.fixed_behavior(),
    )


def resolve_execution_profile(name: str) -> ProfileResolution:
    configured_name = str(name or "").strip()
    key = configured_name.lower()
    if not key:
        raise ValueError("unknown execution profile: ''")
    alias = _PROFILE_ALIASES.get(key)
    if alias is None:
        profile = get_execution_profile(key)
        return ProfileResolution(
            execution_profile=configured_name,
            resolved_execution_profile=profile.name,
            profile=profile,
        )

    resolved_name, expected_fixture_identity = alias
    profile = get_execution_profile(resolved_name)
    if profile_fixture_identity(profile) != expected_fixture_identity:
        raise ExecutionContractError(f"profile alias {configured_name!r} is not fixture-identical to {resolved_name!r}")
    return ProfileResolution(
        execution_profile=configured_name,
        resolved_execution_profile=profile.name,
        profile=profile,
    )


def execution_config_id_for_profile(
    name: str,
    *,
    profile_parameters: Mapping[str, Any] | None = None,
) -> str:
    resolution = resolve_execution_profile(name)
    return make_execution_config_id(
        resolved_execution_profile=resolution.resolved_execution_profile,
        fixed_behavior=resolution.profile.fixed_behavior(profile_parameters),
    )
