from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.strategies.weather_city_probability_shadow.core import (
    CONFIG_SCHEMA_VERSION,
    OUTPUT_SCHEMA_FINGERPRINT,
    OUTPUT_SCHEMA_VERSION,
    InputNotReady,
    ShadowRuntime,
    migrate_evaluation_row,
    resolve_journal_catalog,
)
from src.strategies.weather_city_probability_shadow.helsinki import (
    _official_helsinki_as_of,
)
from src.strategies.weather_city_probability_shadow.tokyo import (
    _jma_history,
    _market_prices,
    _official_history,
)
from weather_data_feed.input_catalog import JsonlInputCatalog
from weather_data_feed_service.high_frequency_observations import (
    PRODUCER_SCHEMA_FINGERPRINT,
    PRODUCER_SCHEMA_VERSION,
)


UTC = timezone.utc


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _config(output_dir: Path, profiles: list[dict]) -> dict:
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "output_schema_fingerprint": OUTPUT_SCHEMA_FINGERPRINT,
        "execution_mode": "zero_notional_shadow",
        "orders_submitted": 0,
        "output_dir": str(output_dir),
        "profiles": profiles,
    }


def test_input_catalog_uses_decision_clock_for_cross_day_physical_shard(tmp_path: Path) -> None:
    physical = tmp_path / "2026-07-31" / "observations.jsonl"
    _write_jsonl(physical, [{
        "city": "Tokyo",
        "target_date": "2026-08-01",
        "fetched_at_utc": "2026-07-31T15:05:00Z",
    }])
    rows = JsonlInputCatalog().rows_from_day_shards(
        tmp_path,
        filename="observations.jsonl",
        as_of=datetime(2026, 7, 31, 15, 6, tzinfo=UTC),
        predicate=lambda row: row.get("target_date") == "2026-08-01",
    )
    assert len(rows) == 1
    assert rows[0].physical_path == str(physical)


def test_city_official_loaders_find_target_date_in_prior_utc_shard(tmp_path: Path) -> None:
    physical = tmp_path / "2026-07-31" / "observations.jsonl"
    rows = [
        {
            "city": city,
            "target_date": "2026-08-01",
            "status": "ok",
            "fetched_at_utc": fetched,
            "last_obs_utc": observed,
            "running_max_c": 18 if city == "Helsinki" else 28,
            "current_temp_c": 18 if city == "Helsinki" else 28,
        }
        for city, fetched, observed in (
            ("Tokyo", "2026-07-31T15:10:00Z", "2026-07-31T15:00:00Z"),
            ("Helsinki", "2026-07-31T21:10:00Z", "2026-07-31T21:00:00Z"),
        )
    ]
    _write_jsonl(physical, rows)
    tokyo = _official_history(
        tmp_path,
        "2026-08-01",
        datetime(2026, 7, 31, 15, 11, tzinfo=UTC),
    )
    helsinki = _official_helsinki_as_of(
        tmp_path,
        "2026-08-01",
        datetime(2026, 7, 31, 21, 11, tzinfo=UTC),
    )
    assert tokyo[-1]["_input_ref"]["physical_path"] == str(physical)
    assert helsinki and helsinki["_input_ref"]["physical_path"] == str(physical)


def test_jma_history_excludes_late_first_seen_rows(tmp_path: Path) -> None:
    path = tmp_path / "jma.jsonl"
    base = {
        "city": "Tokyo",
        "source": "jma_amedas",
        "source_status": "ok",
        "target_date": "2026-08-01",
        "observation_time_utc": "2026-08-01T01:00:00Z",
        "temp_c": 32.1,
    }
    _write_jsonl(path, [
        {**base, "source_first_seen_at_utc": "2026-08-01T01:07:00Z", "payload_hash": "pit"},
        {
            **base,
            "observation_time_utc": "2026-08-01T00:50:00Z",
            "source_first_seen_at_utc": "2026-08-01T01:20:00Z",
            "payload_hash": "late",
        },
    ])
    rows = _jma_history(
        path,
        "2026-08-01",
        datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 8, 1, 1, 8, tzinfo=UTC),
    )
    assert [row["payload_hash"] for row in rows] == ["pit"]


def test_one_sided_tokyo_prices_are_interval_censored_not_exception() -> None:
    prices = _market_prices({"summary": {"best_bid": 0.999, "best_ask": None}})
    assert prices["quote_state"] == "one_sided_bid_only"
    assert prices["market_probability_status"] == "interval_censored"
    assert prices["no_mid"] is None
    assert prices["yes_ask"] == pytest.approx(0.001)


def test_runtime_journals_input_not_ready_as_deduplicated_checkpoint(tmp_path: Path) -> None:
    class Waiting:
        def score(self, profile, now):
            raise InputNotReady(
                "awaiting_official_observation",
                city="Tokyo",
                target_date="2026-08-01",
                decision_ts_utc="2026-07-31T15:05:00+00:00",
                details={"physical_shard": "2026-07-31"},
            )

    runtime = ShadowRuntime(
        _config(tmp_path, [{"adapter": "waiting", "city": "Tokyo"}]),
        {"waiting": Waiting()},
    )
    first = runtime.run_once(datetime(2026, 7, 31, 15, 5, tzinfo=UTC))
    second = runtime.run_once(datetime(2026, 7, 31, 15, 6, tzinfo=UTC))
    rows = [json.loads(line) for line in (tmp_path / "checkpoints.jsonl").read_text().splitlines()]
    assert first["checkpoint_blockers"] == second["checkpoint_blockers"] == 1
    assert len(rows) == 1
    assert rows[0]["blocker_reason"] == "awaiting_official_observation"
    assert rows[0]["runtime_identity"]["loaded_module_sha256"]
    assert rows[0]["schema_fingerprint"] == OUTPUT_SCHEMA_FINGERPRINT
    assert not (tmp_path / "errors.jsonl").exists()


def test_legacy_v1_migration_is_explicit_about_missing_runtime_identity() -> None:
    migrated = migrate_evaluation_row({
        "schema_version": "weather_city_probability_shadow_v1",
        "city": "Tokyo",
        "market_probability": 0.4,
        "model_probability": 0.5,
    })
    assert migrated["schema_version"] == OUTPUT_SCHEMA_VERSION
    assert migrated["source_schema_version"] == "weather_city_probability_shadow_v1"
    assert migrated["migration_status"] == "legacy_runtime_identity_unavailable"
    assert migrated["runtime_identity"] is None
    assert migrated["evaluation_status"] == "scored"


def test_runtime_rejects_old_config_schema_before_writing(tmp_path: Path) -> None:
    config = _config(tmp_path, [])
    config["schema_version"] = "weather_city_probability_shadow_config_v1"
    with pytest.raises(ValueError, match="config schema"):
        ShadowRuntime(config, {})


def test_committed_v2_config_matches_runtime_schema_contract() -> None:
    config = json.loads(
        (Path(__file__).resolve().parents[2] / "configs/weather/city_probability_shadow_v2.json")
        .read_text(encoding="utf-8")
    )
    assert config["schema_version"] == CONFIG_SCHEMA_VERSION
    assert config["output_schema_version"] == OUTPUT_SCHEMA_VERSION
    assert config["output_schema_fingerprint"] == OUTPUT_SCHEMA_FINGERPRINT
    assert config["output_dir"].endswith("city_probability_shadow_v2")
    catalog = resolve_journal_catalog(config)
    assert [path.parent.name for path in catalog["evaluations"]] == [
        "city_probability_shadow_v2",
    ]
    helsinki = next(profile for profile in config["profiles"] if profile["city"] == "Helsinki")
    assert "observation_cache" not in helsinki
    assert helsinki["observation_journal_dir"].endswith("/output/observations")


def test_runtime_deduplicates_positions_across_legacy_journal_catalog(tmp_path: Path) -> None:
    output = tmp_path / "v2"
    legacy = tmp_path / "v1" / "paper_intents.jsonl"
    position_key = "Tokyo|2026-08-01|35|model"
    _write_jsonl(legacy, [{"position_key": position_key}])
    config = _config(output, [{
        "adapter": "fixed",
        "city": "Tokyo",
        "position_scope": "city_date_bracket_model",
    }])
    config["journal_catalog"] = {
        "evaluations": [str(output / "evaluations.jsonl")],
        "paper_intents": [str(output / "paper_intents.jsonl"), str(legacy)],
        "checkpoints": [str(output / "checkpoints.jsonl")],
        "errors": [str(output / "errors.jsonl")],
    }

    class Fixed:
        def score(self, profile, now):
            from src.strategies.weather_city_probability_shadow.core import CityScore
            return [CityScore(
                city="Tokyo", target_date="2026-08-01",
                decision_ts_utc="2026-08-01T06:00:00+00:00",
                source_obs_ts_utc="2026-08-01T06:00:00+00:00",
                current_bracket=35, market_side="YES", market_probability=0.5,
                market_entry_price=0.5, model_probability=0.9, model_id="model",
                feature_coverage=1.0, missing_features=[], features={}, market={}, lineage={},
            )]

    summary = ShadowRuntime(config, {"fixed": Fixed()}).run_once(
        datetime(2026, 8, 1, 6, tzinfo=UTC)
    )
    assert summary["new_evaluations"] == 1
    assert summary["new_paper_intents"] == 0
    assert not (output / "paper_intents.jsonl").exists()


def test_city_day_scope_selects_best_side_and_never_stacks_brackets(tmp_path: Path) -> None:
    output = tmp_path / "output"
    config = _config(output, [{
        "adapter": "ladder",
        "city": "Tokyo",
        "position_scope": "city_date_model",
    }])

    class Ladder:
        def score(self, profile, now):
            from src.strategies.weather_city_probability_shadow.core import CityScore
            common = dict(
                city="Tokyo", target_date="2026-08-01",
                decision_ts_utc="2026-08-01T06:00:00+00:00",
                source_obs_ts_utc="2026-08-01T06:00:00+00:00",
                market_probability=0.5, model_id="v7", feature_coverage=1.0,
                missing_features=[], features={}, market={}, lineage={},
            )
            return [
                CityScore(current_bracket=34, market_side="YES", market_entry_price=.4,
                          model_probability=.55, **common),
                CityScore(current_bracket=34, market_side="NO", market_entry_price=.4,
                          model_probability=.70, **common),
                CityScore(current_bracket=35, market_side="YES", market_entry_price=.4,
                          model_probability=.60, **common),
            ]

    summary = ShadowRuntime(config, {"ladder": Ladder()}).run_once(
        datetime(2026, 8, 1, 6, tzinfo=UTC)
    )
    assert summary["new_evaluations"] == 3
    assert summary["new_paper_intents"] == 1
    intent = json.loads((output / "paper_intents.jsonl").read_text())
    assert intent["market_side"] == "NO"
    assert intent["current_bracket"] == 34
    assert intent["position_key"] == "Tokyo|2026-08-01|v7"


def test_profile_can_emit_probability_telemetry_without_paper_intent(tmp_path: Path) -> None:
    output = tmp_path / "output"
    config = _config(output, [{
        "adapter": "fixed", "city": "Tokyo", "emit_paper_intents": False,
    }])

    class Fixed:
        def score(self, profile, now):
            from src.strategies.weather_city_probability_shadow.core import CityScore
            return [CityScore(
                city="Tokyo", target_date="2026-08-01",
                decision_ts_utc=now.isoformat(), source_obs_ts_utc=now.isoformat(),
                current_bracket=35, market_side="YES", market_probability=.5,
                market_entry_price=.4, model_probability=.9, model_id="v7",
                feature_coverage=1.0, missing_features=[], features={}, market={}, lineage={},
            )]

    summary = ShadowRuntime(config, {"fixed": Fixed()}).run_once(
        datetime(2026, 8, 1, 6, tzinfo=UTC)
    )
    assert summary["new_evaluations"] == 1
    assert summary["new_paper_intents"] == 0
    assert not (output / "paper_intents.jsonl").exists()


def test_runtime_handshakes_upstream_producer_identity(tmp_path: Path) -> None:
    journal = tmp_path / "high_frequency_observations.jsonl"
    latest = tmp_path / "latest.json"
    identity = {
        "runtime_instance_id": "producer-instance",
        "output_schema_version": PRODUCER_SCHEMA_VERSION,
        "output_schema_fingerprint": PRODUCER_SCHEMA_FINGERPRINT,
    }
    latest.write_text(json.dumps({
        "schema_version": PRODUCER_SCHEMA_VERSION,
        "schema_fingerprint": PRODUCER_SCHEMA_FINGERPRINT,
        "producer_identity": identity,
    }), encoding="utf-8")
    config = _config(tmp_path / "output", [{
        "adapter": "empty", "city": "Tokyo", "source_journal": str(journal)
    }])
    config.update({
        "require_producer_contracts": True,
        "producer_contracts": [{
            "journal_path": str(journal),
            "latest_path": str(latest),
            "schema_version": PRODUCER_SCHEMA_VERSION,
            "schema_fingerprint": PRODUCER_SCHEMA_FINGERPRINT,
        }],
    })

    class Empty:
        def score(self, profile, now):
            return []

    runtime = ShadowRuntime(config, {"empty": Empty()})
    assert runtime.runtime_identity["upstream_producer_identity"][str(journal.resolve())] == identity

    broken = json.loads(latest.read_text())
    broken["schema_fingerprint"] = "wrong"
    latest.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(RuntimeError, match="producer fingerprint handshake failed"):
        ShadowRuntime(config, {"empty": Empty()})
