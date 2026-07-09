from src.strategies.runtime.specs import load_instance_specs, params_hash


def test_load_returns_known_instances():
    specs = load_instance_specs()
    ids = {s.strategy_instance for s in specs}
    assert len(specs) >= 15
    assert "tmax_distribution_edge_first_lock_no_current_yes_shadow_v1" in ids
    assert "low_price_yes_lottery_tiny_live_v1" in ids


def test_first_lock_spec_round_trips_exactly():
    specs = {s.strategy_instance: s for s in load_instance_specs()}
    s = specs["tmax_distribution_edge_first_lock_no_current_yes_shadow_v1"]
    assert s.lifecycle_status == "shadow"
    assert s.execution_mode == "zero_notional_shadow"
    assert s.start_script == (
        "scripts/ops/start_tmax_distribution_edge_first_lock_no_current_yes_shadow_v1.sh"
    )
    assert s.tmux_session == "tmax_distribution_edge_first_lock_no_current_yes_shadow_v1"
    # artifact_files must survive as tuples, not lists
    assert ("summary_history", "summary_history.jsonl") in s.artifact_files


def test_params_hash_is_stable():
    specs = load_instance_specs()
    assert params_hash(specs[0]) == params_hash(specs[0])
    assert len(params_hash(specs[0])) == 12
