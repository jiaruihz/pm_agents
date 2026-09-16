from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.strategies.weather_city_probability_shadow.core import (
    AUTHORITATIVE_CONFIG_SCHEMA_VERSION,
    AUTHORITATIVE_OUTPUT_SCHEMA_FINGERPRINT,
    AUTHORITATIVE_OUTPUT_SCHEMA_VERSION,
    CityScore,
    ShadowRuntime,
)
from weather_city_runtime import DecisionContractJournalSink


def _config(output: Path) -> dict:
    return {
        "schema_version": AUTHORITATIVE_CONFIG_SCHEMA_VERSION,
        "framework_id": "weather_city_intraday_runtime_v1",
        "strategy_family": "weather.city_intraday_probability",
        "output_schema_version": AUTHORITATIVE_OUTPUT_SCHEMA_VERSION,
        "output_schema_fingerprint": AUTHORITATIVE_OUTPUT_SCHEMA_FINGERPRINT,
        "execution_mode": "zero_notional_shadow",
        "orders_submitted": 0,
        "output_dir": str(output),
        "profiles": [{"adapter": "fixture", "city": "Helsinki"}],
    }


class _Adapter:
    def score(self, profile, now):
        return [CityScore(
            city="Helsinki",
            target_date="2026-08-02",
            decision_ts_utc=now.isoformat(),
            source_obs_ts_utc="2026-08-02T10:00:00+00:00",
            current_bracket=22,
            market_side="NO",
            market_probability=0.4,
            market_entry_price=0.4,
            model_probability=0.8,
            model_id="helsinki_fixture",
            feature_coverage=1.0,
            missing_features=[],
            features={"remaining_heat": 0.2},
            market={
                "condition_id": "condition",
                "market_id": "market",
                "token_id": "no-token",
                "outcome": "NO",
                "book_snapshot_id": "book",
            },
            lineage={
                "source": "fmi",
                "source_first_seen_at_utc": "2026-08-02T10:00:02+00:00",
                "source_payload_hash": "payload",
                "model_artifact_sha256": "artifact",
                "profile_id": "helsinki_fixture_profile",
                "book_snapshot_id": "book",
            },
        )]


def test_authoritative_runtime_writes_only_shared_contracts(tmp_path: Path) -> None:
    output = tmp_path / "runtime_v3"
    sink = DecisionContractJournalSink(output, cities=["Helsinki"])
    runtime = ShadowRuntime(
        _config(output), {"fixture": _Adapter()}, decision_sink=sink
    )

    first = runtime.run_once(datetime(2026, 8, 2, 10, 1, tzinfo=timezone.utc))
    second = runtime.run_once(datetime(2026, 8, 2, 10, 2, tzinfo=timezone.utc))

    assert first["decision_contract_output"] == {
        "written_bundles": 2,
        "written_intents": 1,
        "written_blockers": 0,
        "written_intent_blockers": 0,
        "conversion_errors": 0,
    }
    assert second["decision_contract_output"] == {
        "written_bundles": 0,
        "written_intents": 0,
        "written_blockers": 0,
        "written_intent_blockers": 0,
        "conversion_errors": 0,
    }
    bundles = [json.loads(line) for line in (output / "decision_bundles.jsonl").read_text().splitlines()]
    intents = [json.loads(line) for line in (output / "trade_intents.jsonl").read_text().splitlines()]
    assert {row["signal_candidate"]["selected"] for row in bundles} == {False, True}
    assert len({row["signal_candidate"]["candidate_id"] for row in bundles}) == 1
    assert intents[0]["requested_size"] == 0.0
    assert intents[0]["mode"] == "zero_notional"
    assert first["orders_submitted"] == 0
    assert first["legacy_journals"] == "deprecated_read_only"
    assert not (output / "evaluations.jsonl").exists()
    assert not (output / "paper_intents.jsonl").exists()
    assert not (output / "checkpoints.jsonl").exists()


def test_sink_filters_other_cities_and_preserves_checkpoint_blocker(tmp_path: Path) -> None:
    sink = DecisionContractJournalSink(tmp_path / "vnext", cities=["Helsinki"])
    tokyo = sink.record_checkpoint_blocker({
        "city": "Tokyo", "checkpoint_id": "tokyo", "blocker_reason": "waiting"
    })
    helsinki = sink.record_checkpoint_blocker({
        "city": "Helsinki",
        "target_date": "2026-08-02",
        "decision_ts_utc": "2026-08-01T21:00:00+00:00",
        "checkpoint_id": "helsinki",
        "blocker_reason": "awaiting_official_observation",
        "details": {"physical_shard": "2026-08-01"},
    })
    duplicate = sink.record_checkpoint_blocker({
        "city": "Helsinki", "checkpoint_id": "helsinki", "blocker_reason": "waiting"
    })

    assert tokyo.written_blockers == 0
    assert helsinki.written_blockers == 1
    assert duplicate.written_blockers == 0
    row = json.loads((tmp_path / "vnext/checkpoint_blockers.jsonl").read_text())
    assert row["blocker_reason"] == "awaiting_official_observation"


def test_decision_output_config_cannot_grant_live_or_use_nonactive_authority(tmp_path: Path) -> None:
    config = {
        "decision_contract_output": {
            "enabled": True,
            "authority": "active",
            "execution_mode": "live",
            "cities": ["Helsinki"],
            "output_dir": str(tmp_path / "vnext"),
        }
    }
    try:
        DecisionContractJournalSink.from_config(
            config, legacy_output_dir=tmp_path / "legacy"
        )
    except ValueError as exc:
        assert "zero_notional" in str(exc)
    else:
        raise AssertionError("live decision output must fail")

    config["decision_contract_output"]["execution_mode"] = "zero_notional"
    config["decision_contract_output"]["authority"] = "dual"
    try:
        DecisionContractJournalSink.from_config(config)
    except ValueError as exc:
        assert "authority must be active" in str(exc)
    else:
        raise AssertionError("dual authority must fail")


def test_committed_runtime_v3_is_sole_zero_notional_authority() -> None:
    root = Path(__file__).resolve().parents[2]
    config = json.loads(
        (root / "configs/weather/city_probability_runtime_v3.json").read_text()
    )
    declaration = config["decision_contract_output"]
    assert declaration["enabled"] is True
    assert declaration["authority"] == "active"
    assert declaration["execution_mode"] == "zero_notional"
    assert declaration["cities"] == [
        "Amsterdam", "Busan", "Helsinki", "Seoul", "Tokyo"
    ]
    assert config["framework_id"] == "weather_city_intraday_runtime_v1"
    assert config["strategy_family"] == "weather.city_intraday_probability"
    assert declaration["output_dir"] == config["output_dir"]
    assert config["legacy_runtime"]["lifecycle_status"] == "deprecated_read_only"
    assert config["orders_submitted"] == 0
