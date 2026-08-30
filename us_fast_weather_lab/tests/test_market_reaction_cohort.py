from pathlib import Path

import pytest
import yaml

from us_fast_weather_lab.market_reaction_cohort import load_market_reaction_cohort


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
