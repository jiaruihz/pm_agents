import json

import pytest

from src.strategies.weather_edge_v1.execution.contracts import (
    ExecutionContractError,
    ExecutionLegProfile,
    ExecutionProfile,
)
from src.strategies.weather_edge_v1.execution import profiles


def test_legacy_profile_names_and_behavior_are_preserved_and_json_safe():
    expected = {
        "taker_now_v1": ("all_taker", 0, (("single", "taker_top_ask_v1", "taker_now", False, 1.0),)),
        "single_side_maker_v1": ("all_maker", 90, (("single", "maker_queue_v2", "maker_until_data_update", True, 1.0),)),
        "d1_taker_only_v1": ("all_taker", 0, (("taker", "d1_yes_high_mid_taker_v1", "taker_now", False, 1.0),)),
        "d1_taker_plus_maker_static_v1": ("fixed_weight_split", 90, (("taker", "d1_yes_high_mid_taker_v1", "taker_now", False, 0.5), ("maker", "d1_yes_high_mid_maker_v1", "maker_until_data_update", True, 0.5))),
        "d1_taker_plus_maker_chase_to_mid_v1": ("fixed_weight_split", 90, (("taker", "d1_yes_high_mid_taker_v1", "taker_now", False, 0.5), ("maker", "d1_yes_high_mid_maker_v1", "maker_until_data_update", True, 0.5))),
        "split_taker_maker_chase_v1": ("fixed_weight_split", 0, (("taker", "current_yes_heat_death_taker_probe_v1", "taker_now", False, 0.5), ("maker", "current_yes_heat_death_maker_probe_v1", "maker_chase_then_taker_fallback_v1", True, 0.5))),
    }

    assert tuple(profiles._PROFILES) == tuple(expected)
    for name, (allocation_policy, cancel_buffer_sec, legs) in expected.items():
        profile = profiles.get_execution_profile(name)
        assert profile.allocation_policy == allocation_policy
        assert profile.cancel_buffer_sec == cancel_buffer_sec
        assert tuple((leg.role, leg.execution_policy, leg.order_lifecycle_policy, leg.maker_only, leg.share_fraction) for leg in profile.legs) == legs
        assert json.loads(json.dumps(profile.to_json()))["name"] == name


def test_resolution_preserves_configured_name_and_rejects_unknown_profile():
    resolution = profiles.resolve_execution_profile("D1_TAKER_PLUS_MAKER_STATIC_V1")
    assert resolution.execution_profile == "D1_TAKER_PLUS_MAKER_STATIC_V1"
    assert resolution.resolved_execution_profile == "d1_taker_plus_maker_static_v1"
    assert resolution.profile.name == resolution.resolved_execution_profile
    with pytest.raises(ValueError, match="unknown execution profile"):
        profiles.resolve_execution_profile("not-a-profile")


def test_alias_resolution_requires_fixture_identical_behavior(monkeypatch):
    target = profiles.get_execution_profile("taker_now_v1")
    monkeypatch.setitem(
        profiles._PROFILE_ALIASES,
        "legacy-taker-alias-v1",
        (target.name, profiles.profile_fixture_identity(target)),
    )
    assert profiles.resolve_execution_profile("legacy-taker-alias-v1").resolved_execution_profile == target.name

    monkeypatch.setitem(profiles._PROFILE_ALIASES, "invalid-alias-v1", (target.name, "not-identical"))
    with pytest.raises(ExecutionContractError, match="fixture-identical"):
        profiles.resolve_execution_profile("invalid-alias-v1")


def test_profile_validation_rejects_invalid_combinations_and_undeclared_parameters():
    maker_leg = ExecutionLegProfile(
        role="maker",
        execution_policy="maker-v1",
        order_lifecycle_policy="until-deadline-v1",
        maker_only=True,
    )
    with pytest.raises(ExecutionContractError, match="all_taker"):
        ExecutionProfile(name="invalid-v1", legs=(maker_leg,), cancel_buffer_sec=0, allocation_policy="all_taker")
    with pytest.raises(ExecutionContractError, match="share fractions"):
        ExecutionProfile(
            name="invalid-split-v1",
            legs=(maker_leg, maker_leg),
            cancel_buffer_sec=0,
            allocation_policy="fixed_weight_split",
        )
    with pytest.raises(ExecutionContractError, match="undeclared parameters"):
        profiles.get_execution_profile("taker_now_v1").fixed_behavior({"refresh_sec": 15})
    with pytest.raises(ExecutionContractError, match="cannot control execution mode"):
        profiles.get_execution_profile("taker_now_v1").fixed_behavior({"live_enabled": True})


def test_fixed_weight_split_uses_exact_decimal_semantics_for_declared_fractions():
    legs = tuple(
        ExecutionLegProfile(
            role=f"leg-{index}",
            execution_policy="policy-v1",
            order_lifecycle_policy="lifecycle-v1",
            maker_only=False,
            share_fraction=share_fraction,
        )
        for index, share_fraction in enumerate((0.1, 0.2, 0.7), start=1)
    )
    profile = ExecutionProfile(
        name="decimal-split-v1",
        legs=legs,
        cancel_buffer_sec=0,
        allocation_policy="fixed_weight_split",
    )
    assert tuple(leg.share_fraction for leg in profile.legs) == (0.1, 0.2, 0.7)

    with pytest.raises(ExecutionContractError, match="share fractions"):
        ExecutionProfile(
            name="non-unit-split-v1",
            legs=legs[:-1]
            + (
                ExecutionLegProfile(
                    role="leg-3",
                    execution_policy="policy-v1",
                    order_lifecycle_policy="lifecycle-v1",
                    maker_only=False,
                    share_fraction=0.6,
                ),
            ),
            cancel_buffer_sec=0,
            allocation_policy="fixed_weight_split",
        )


def test_execution_config_id_changes_only_with_fixed_profile_behavior():
    static_id = profiles.execution_config_id_for_profile("d1_taker_plus_maker_static_v1")
    chase_id = profiles.execution_config_id_for_profile("d1_taker_plus_maker_chase_to_mid_v1")
    assert static_id != chase_id
    assert static_id == profiles.execution_config_id_for_profile("D1_TAKER_PLUS_MAKER_STATIC_V1")
