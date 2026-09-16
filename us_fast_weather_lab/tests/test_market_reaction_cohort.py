from pathlib import Path

import pytest
import yaml

from us_fast_weather_lab.market_reaction_cohort import load_market_reaction_cohort, load_named_market_reaction_cohort


def _write_config(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "cohort.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_frozen_primary_identity_rejects_same_count_city_substitution(tmp_path):
    source = Path("us_fast_weather_lab/config/market_reaction_cohort.yaml")
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    payload["reaction_targets"][0]["city"] = "Phoenix"
    with pytest.raises(ValueError, match="frozen ten-city contract"):
        load_market_reaction_cohort(_write_config(tmp_path, payload))


def test_frozen_observation_only_identity_rejects_drift(tmp_path):
    source = Path("us_fast_weather_lab/config/market_reaction_cohort.yaml")
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    payload["observation_only"][0]["source_station"] = "KJFK"
    with pytest.raises(ValueError, match="frozen Boston/Minneapolis contract"):
        load_market_reaction_cohort(_write_config(tmp_path, payload))


def test_global_cohort_locks_reaction_and_observation_station_sets():
    cohort = load_named_market_reaction_cohort("europe_asia_core_v1")
    assert {(target.city, target.source_station) for target in cohort.reaction_targets} == {
        ("Helsinki", "EFHK"), ("Tokyo", "RJTT"), ("Amsterdam", "EHAM"),
        ("Busan", "RKPK"), ("Seoul", "RKSI"), ("Singapore", "WSSS"),
    }
    assert len(cohort.observation_only) == 24
    assert {"VHHH", "ZBAA", "ZSPD"}.issubset(cohort.all_source_stations)
    assert next(target for target in cohort.reaction_targets if target.city == "Helsinki").market_unit == "C"


@pytest.mark.parametrize("field", ["region", "comparison_class", "market_unit"])
def test_global_cohort_requires_and_freezes_semantic_metadata(tmp_path, field):
    path = Path(__file__).parents[1] / "config" / "market_reaction_cohort_europe_asia.yaml"
    payload = yaml.safe_load(path.read_text())
    payload["reaction_targets"][0].pop(field)
    with pytest.raises(ValueError, match="semantic metadata is required"):
        load_market_reaction_cohort(_write_config(tmp_path, payload))


def test_global_cohort_rejects_duplicate_observation_row(tmp_path):
    path = Path(__file__).parents[1] / "config" / "market_reaction_cohort_europe_asia.yaml"
    payload = yaml.safe_load(path.read_text())
    payload["observation_only"].append(dict(payload["observation_only"][0]))
    with pytest.raises(ValueError, match="global observation-only targets differ"):
        load_market_reaction_cohort(_write_config(tmp_path, payload))
