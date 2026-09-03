from pathlib import Path

from src.strategies.runtime.production import load_production_spec


W3_SHA = "18d55894137d08d9ac17355a4c9027bbbe6517ac"


def test_w3_zero_notional_runtime_is_registered_without_live_authority():
    spec = load_production_spec()
    runtime = next(
        row
        for row in spec.managed_runtimes
        if row.instance_id == "weather_mm_w3_event_fenced_paired_bid_shadow_v1"
    )
    release = spec.release("weather_mm_w3_event_fenced_paired_bid")

    assert runtime.desired_state == "running"
    assert runtime.execution_mode == "zero_notional_shadow"
    assert runtime.expected_live is False
    assert runtime.recovery_policy == "safe"
    assert runtime.dependencies == (
        "weather_jrs_context_keeper",
        "weather_data_feed_jrs",
        "weather_market_books",
    )
    assert runtime.expected_health_contract() == {
        "strategy_key": "weather_mm_w3_event_fenced_paired_bid_v1",
        "execution_mode": "zero_notional_shadow",
        "live_authority": False,
        "venue_call_allowed": False,
        "actual_notional": 0,
        "trade_intents": 0,
        "plans": 0,
        "orders": 0,
        "fills": 0,
        "exchange_calls": 0,
        "formal_forward_start_utc": "2026-09-04T00:00:00Z",
    }
    assert release.expected_repo_sha == W3_SHA
    assert release.checkout_root == Path(
        "/Users/deepsleep/.local/share/pm_agents/releases/"
        f"weather_mm_w3_event_fenced_paired_bid/{W3_SHA}"
    )
    assert release.runtime_bindings == ("operational_venv",)
