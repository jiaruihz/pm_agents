import json
from datetime import datetime, timezone

import pytest

from src.strategies.weather_city_probability_shadow.core import (
    CONFIG_SCHEMA_VERSION,
    OUTPUT_SCHEMA_FINGERPRINT,
    OUTPUT_SCHEMA_VERSION,
    CityScore,
    ShadowRuntime,
)


def _config(output_dir, profiles):
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "output_schema_fingerprint": OUTPUT_SCHEMA_FINGERPRINT,
        "execution_mode": "zero_notional_shadow",
        "orders_submitted": 0,
        "output_dir": str(output_dir),
        "profiles": profiles,
    }


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
    config = _config(tmp_path, [{"adapter":"fixture","city":"Test"}])
    runtime = ShadowRuntime(config, {"fixture":Adapter()})
    first = runtime.run_once(datetime(2026,7,31,10,tzinfo=timezone.utc))
    second = runtime.run_once(datetime(2026,7,31,10,1,tzinfo=timezone.utc))
    assert first["new_evaluations"] == 1 and first["new_paper_intents"] == 1
    assert second["new_evaluations"] == 0 and second["new_paper_intents"] == 0
    evaluation = json.loads((tmp_path/"evaluations.jsonl").read_text().splitlines()[0])
    intent = json.loads((tmp_path/"paper_intents.jsonl").read_text().splitlines()[0])
    assert evaluation["orders_submitted"] == 0
    assert evaluation["schema_version"] == OUTPUT_SCHEMA_VERSION
    assert evaluation["schema_fingerprint"] == OUTPUT_SCHEMA_FINGERPRINT
    assert evaluation["record_kind"] == "evaluation"
    assert evaluation["runtime_identity"]["loaded_module_sha256"]
    assert intent["notional_usd"] == 0 and intent["shares"] == 0
    assert intent["record_kind"] == "paper_intent"


def test_nonzero_execution_configuration_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        ShadowRuntime({
            **_config(tmp_path, []),
            "execution_mode": "live",
            "orders_submitted": 1,
        }, {})


def test_weather_fee_formula():
    assert ShadowRuntime.fee_per_share(.5) == pytest.approx(.0125)


def test_not_scorable_checkpoint_is_journaled_without_intent_or_error(tmp_path):
    class OneSided:
        def score(self, profile, now):
            return [CityScore(
                city="Helsinki", target_date="2026-07-31",
                decision_ts_utc=now.isoformat(),
                source_obs_ts_utc="2026-07-31T13:10:00+00:00",
                current_bracket=26, market_side="NO", market_probability=None,
                market_entry_price=.001, model_probability=None, model_id="offset_v1",
                feature_coverage=.9, missing_features=["weather_market_logit_gap"],
                features={"weather_market_logit_gap": None},
                market={"quote_state": "one_sided_near_binary"}, lineage={},
                evaluation_status="not_scorable",
                not_scorable_reason="one_sided_market_probability_interval",
            )]

    config = _config(tmp_path, [{"adapter":"one","city":"Helsinki"}])
    summary = ShadowRuntime(config, {"one": OneSided()}).run_once(
        datetime(2026, 7, 31, 13, 11, tzinfo=timezone.utc)
    )
    assert summary["evaluated"] == 1
    assert summary["scored"] == 0
    assert summary["not_scorable"] == 1
    assert summary["errors"] == 0
    assert summary["new_paper_intents"] == 0
    row = json.loads((tmp_path / "evaluations.jsonl").read_text().splitlines()[0])
    assert row["evaluation_status"] == "not_scorable"
    assert row["edge_after_fee"] is None
    assert row["would_enter"] is False


def test_profile_threshold_and_bracket_scope_allow_only_one_side(tmp_path):
    class BothSides:
        def score(self, profile, now):
            common = dict(
                city="Tokyo", target_date="2026-08-01", decision_ts_utc=now.isoformat(),
                source_obs_ts_utc="2026-08-01T01:00:00+00:00", current_bracket=34,
                market_probability=.4, market_entry_price=.4, model_probability=.5,
                model_id="v6", feature_coverage=1.0, missing_features=[], features={},
                market={}, lineage={},
            )
            return [CityScore(market_side=side, **common) for side in ("YES", "NO")]

    config = _config(tmp_path, [{
        "adapter": "both", "city": "Tokyo", "edge_threshold": .05,
        "position_scope": "city_date_bracket_model",
    }])
    summary = ShadowRuntime(config, {"both": BothSides()}).run_once(
        datetime(2026, 8, 1, 1, tzinfo=timezone.utc)
    )
    assert summary["new_evaluations"] == 2
    assert summary["new_paper_intents"] == 1
    rows = [json.loads(line) for line in (tmp_path / "evaluations.jsonl").read_text().splitlines()]
    assert all(row["edge_threshold"] == .05 and row["would_enter"] for row in rows)
    intent = json.loads((tmp_path / "paper_intents.jsonl").read_text().splitlines()[0])
    assert intent["position_key"] == "Tokyo|2026-08-01|34|v6"
