import json
from pathlib import Path


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "weather_execution"
FIXTURE_VERSION = "weather_execution_phase0_v1"


def load_json(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_phase0_fixture_manifest_matches_fixture_files_and_required_shape():
    manifest = load_json("baseline_manifest.json")
    fixture_names = sorted(path.name for path in FIXTURE_DIR.glob("*.json") if path.name != "baseline_manifest.json")

    assert manifest["schema_version"] == "weather_execution_phase0_baseline_v1"
    assert manifest["fixtures"] == fixture_names
    assert {"captured_at_utc", "fixture_window", "git", "id_authority", "side_authority", "profile_inventory", "canonical_baseline_counts", "tests"} <= manifest.keys()

    for name in fixture_names:
        fixture = load_json(name)
        assert {"fixture_version", "source", "case", "input"} <= fixture.keys()
        assert fixture["fixture_version"] == FIXTURE_VERSION
        assert bool(fixture["source"])
        assert bool(fixture["case"])
        assert isinstance(fixture["input"], dict)
        assert "expected" in fixture or "expected_children" in fixture


def test_phase0_fixture_key_old_runner_behaviors_are_locked():
    d1 = load_json("d1_static_and_capped_chase.json")["expected"]
    assert d1["chase"]["next_price"] == 0.915
    assert d1["chase"]["replacement_requires_order_state"] is True
    assert d1["static"]["replacement_plan_count"] == 0

    core = load_json("core_carry_capped_chase.json")["expected_children"]
    assert [(child["role"], child["shares"], child["limit_price"]) for child in core] == [
        ("taker", 5.0, 0.84),
        ("maker", 5.0, 0.81),
    ]
    assert core[1]["maker_price_cap"] == 0.82

    heat = load_json("heat_death_chase_and_fallback.json")["expected"]
    assert heat["maker_child"]["maker_price_cap"] == 0.97
    assert heat["fallback"] == {
        "action": "h1_maker_taker_fallback",
        "maker_only": False,
        "limit_price": 0.965,
        "cancel_before_order_id": "fixture-maker-order",
    }

    low = load_json("low_price_repost_and_fallback.json")["expected"]
    assert (low["action"], low["maker_only"], low["limit_price"]) == (
        "maker_lifecycle_repost_lower",
        True,
        0.011,
    )

    fast = load_json("fast_source_gtd_and_retry.json")["expected"]
    assert fast["order_type"] == "GTD"
    assert [(attempt["status"], attempt["limit_price"]) for attempt in fast["attempts"]] == [
        ("submit_failed", 0.66),
        ("submitted", 0.63),
    ]
    assert fast["result"]["live_order_posted"] is True
