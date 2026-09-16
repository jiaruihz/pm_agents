from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/analysis/market_structure_edge/weather_city_intraday_phase0_census.py"
SPEC = importlib.util.spec_from_file_location("weather_city_intraday_phase0_census", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_frozen_deployed_samples_pass_contract_validator() -> None:
    fixture_dir = ROOT / "tests/fixtures/weather_city_intraday_phase0"
    fixtures = sorted(fixture_dir.glob("*.json"))
    assert len(fixtures) == 9
    for path in fixtures:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        assert MODULE.validate_fixture(fixture) == [], path.name
        assert MODULE.schema_fingerprint(fixture["records"]) == fixture["record_schema_fingerprint"]


def test_schema_fingerprint_is_order_stable_but_shape_sensitive() -> None:
    left = {"a": 1, "b": {"x": "value"}}
    reordered = {"b": {"x": "other"}, "a": 99}
    changed_shape = {"a": "1", "b": {"x": "value"}}
    assert MODULE.schema_fingerprint(left) == MODULE.schema_fingerprint(reordered)
    assert MODULE.schema_fingerprint(left) != MODULE.schema_fingerprint(changed_shape)


def test_revision_validator_rejects_broken_lineage() -> None:
    path = ROOT / "tests/fixtures/weather_city_intraday_phase0/amsterdam_interval_revision_pair.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["records"][1]["revision_of_event_id"] = "wrong-parent"
    assert "revision_lineage_broken" in MODULE.validate_fixture(fixture)


def test_one_sided_validator_rejects_two_sided_book() -> None:
    path = ROOT / "tests/fixtures/weather_city_intraday_phase0/helsinki_one_sided_book.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["records"]["summary"]["best_ask"] = 0.5
    assert "book_is_not_one_sided" in MODULE.validate_fixture(fixture)


def test_phase0_is_complete_but_phase_a_gate_is_closed() -> None:
    path = ROOT / "docs/analysis/2026-08/generated/city_intraday_phase0_census_v1/phase0_census.json"
    census = json.loads(path.read_text(encoding="utf-8"))
    assert census["phase0_complete"] is True
    assert census["artifact_hashes_all_match"] is True
    assert census["phase_a_implementation_gate_pass"] is False
    assert {blocker["id"] for blocker in census["blockers"]} >= {
        "P0-RUNTIME-IDENTITY",
        "P0-SCHEMA-VERSION",
        "P0-PARTITION-LOCATOR",
        "P0-ONE-SIDED",
        "P0-MULTI-ANCHOR",
        "P0-REVISION-EVENT",
    }
