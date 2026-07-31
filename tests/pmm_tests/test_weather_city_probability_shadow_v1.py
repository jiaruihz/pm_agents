import json
from datetime import datetime, timezone

import pytest

from src.strategies.weather_city_probability_shadow.core import CityScore, ShadowRuntime


class Adapter:
    def score(self, profile, now):
        return [CityScore(
            city="Test", target_date="2026-07-31", decision_ts_utc=now.isoformat(),
            source_obs_ts_utc="2026-07-31T10:00:00+00:00", current_bracket=20,
            market_side="NO", market_probability=.5, market_entry_price=.5,
            model_probability=.6, model_id="m1", feature_coverage=.5,
            missing_features=["radiation"], features={"radiation": None},
            market={"token_id":"paper"}, lineage={"source":"fixture"},
        )]


def test_zero_notional_runtime_is_deduplicated(tmp_path):
    config = {"execution_mode":"zero_notional_shadow", "orders_submitted":0,
              "output_dir":str(tmp_path), "profiles":[{"adapter":"fixture","city":"Test"}]}
    runtime = ShadowRuntime(config, {"fixture":Adapter()})
    first = runtime.run_once(datetime(2026,7,31,10,tzinfo=timezone.utc))
    second = runtime.run_once(datetime(2026,7,31,10,1,tzinfo=timezone.utc))
    assert first["new_evaluations"] == 1 and first["new_paper_intents"] == 1
    assert second["new_evaluations"] == 0 and second["new_paper_intents"] == 0
    evaluation = json.loads((tmp_path/"evaluations.jsonl").read_text().splitlines()[0])
    intent = json.loads((tmp_path/"paper_intents.jsonl").read_text().splitlines()[0])
    assert evaluation["orders_submitted"] == 0
    assert intent["notional_usd"] == 0 and intent["shares"] == 0


def test_nonzero_execution_configuration_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        ShadowRuntime({"execution_mode":"live", "orders_submitted":1,
                       "output_dir":str(tmp_path), "profiles":[]}, {})


def test_weather_fee_formula():
    assert ShadowRuntime.fee_per_share(.5) == pytest.approx(.0125)
