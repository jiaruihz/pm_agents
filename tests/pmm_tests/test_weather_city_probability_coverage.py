from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.strategies.weather_city_probability_shadow.coverage import (
    ObservationCoverageAdapter,
)
from src.strategies.weather_city_probability_shadow.core import (
    AUTHORITATIVE_CONFIG_SCHEMA_VERSION,
    AUTHORITATIVE_OUTPUT_SCHEMA_FINGERPRINT,
    AUTHORITATIVE_OUTPUT_SCHEMA_VERSION,
    InputNotReady,
    ShadowRuntime,
)
from weather_city_runtime import DecisionContractJournalSink


UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
KNMI_PRODUCTION_JOURNAL = (
    "/Volumes/jrs/weather_data_feed_service_runtime/output/knmi_open_data"
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _profile(city: str, source: str, journal: Path) -> dict:
    return {
        "enabled": True,
        "city": city,
        "adapter": "observation_coverage_v1",
        "framework_id": "weather_city_intraday_runtime_v1",
        "strategy_family": "weather.city_intraday_probability",
        "profile_id": f"{city.lower()}_coverage_v1",
        "source": source,
        "source_journal": str(journal),
        "max_source_age_seconds": 600,
        "blocker_reason": "city_probability_model_not_deployed",
        "emit_paper_intents": False,
    }


def test_amsterdam_production_profile_uses_notification_journal() -> None:
    config = json.loads(
        (ROOT / "configs/weather/city_probability_runtime_v3.json").read_text()
    )
    profile = next(row for row in config["profiles"] if row["city"] == "Amsterdam")

    assert profile["source"] == "knmi"
    assert profile["source_journal"] == KNMI_PRODUCTION_JOURNAL
    contract = next(
        row
        for row in config["producer_contracts"]
        if row["journal_path"] == KNMI_PRODUCTION_JOURNAL
    )
    assert contract["schema_version"] == "weather_knmi_open_data_payload_v1"
    assert contract["schema_fingerprint"] == (
        "401ae97fea4cd0f75399a50944949e658b5b17df80a06ac6d6056531b254c883"
    )
    remaining_heat = next(
        row
        for row in config["profiles"]
        if row["profile_id"]
        == "amsterdam_knmi_remaining_heat_v9_ecmwf_day1_clean_forward"
    )
    assert remaining_heat["expression_sides"] == ["YES", "NO"]
    assert remaining_heat["position_scope"] == "city_date_bracket_model"
    offset = next(
        row
        for row in config["profiles"]
        if row["adapter"] == "amsterdam_knmi_market_offset_probability_v3"
    )
    assert offset["position_scope"] == "city_date_bracket_model"
    assert offset["selection_shares"] == 5.0
    assert offset["min_model_probability"] == 0.55
    assert offset["pre_event_reference_journal"].endswith(
        "/knmi_first_seen_ladder_v1/pre_event_references.jsonl"
    )


def test_amsterdam_revision_is_typed_and_never_becomes_candidate(tmp_path: Path) -> None:
    fixture = json.loads(
        (
            ROOT
            / "tests/fixtures/weather_city_intraday_phase0/amsterdam_interval_revision_pair.json"
        ).read_text()
    )
    journal = tmp_path / "source.jsonl"
    _write_jsonl(journal, fixture["records"])

    with pytest.raises(InputNotReady) as caught:
        ObservationCoverageAdapter().score(
            _profile("Amsterdam", "knmi", journal),
            datetime(2026, 7, 31, 2, 45, tzinfo=UTC),
        )

    blocker = caught.value
    assert blocker.reason == "city_probability_model_not_deployed"
    assert blocker.details["payload_kind"] == "measurement_interval_revision"
    assert blocker.details["event_roles"] == ["new_content", "revision"]
    assert blocker.details["revision_parent_event_ids"] == [
        "16102d7ad19fd3f52fd0f1060eced776028629412182d43facd911b7ce65b24d"
    ]
    assert blocker.details["market_expression_status"] == "not_mapped"


def test_korea_runways_are_one_point_group_checkpoint(tmp_path: Path) -> None:
    journal = tmp_path / "source.jsonl"
    base = {
        "schema_version": "weather_high_frequency_observation_v1",
        "city": "Seoul",
        "source": "amos_runway",
        "source_status": "ok",
        "target_date": "2026-08-02",
        "observation_time_utc": "2026-08-02T02:56:00Z",
        "available_at_utc": "2026-08-02T02:56:19Z",
        "material_state_change": True,
    }
    _write_jsonl(
        journal,
        [
            {
                **base,
                "event_role": "new_content",
                "information_event_id": "preferred",
                "runway": "15R/33L",
                "is_preferred_temperature_runway": True,
                "temp_c": 31.5,
            },
            {
                **base,
                "event_role": "revision",
                "information_event_id": "secondary",
                "revision_of_event_id": "preferred",
                "runway": "16L/34R",
                "is_preferred_temperature_runway": False,
                "temp_c": 31.3,
            },
        ],
    )

    with pytest.raises(InputNotReady) as caught:
        ObservationCoverageAdapter().score(
            _profile("Seoul", "amos_runway", journal),
            datetime(2026, 8, 2, 2, 57, tzinfo=UTC),
        )

    details = caught.value.details
    assert details["payload_kind"] == "point_group_revision"
    assert details["information_event_ids"] == ["preferred", "secondary"]
    assert details["source_temp_c"] == 31.5
    assert details["source_temp_min_c"] == 31.3
    assert details["source_temp_max_c"] == 31.5


def test_coverage_profiles_write_only_deduplicated_blockers(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    rows = []
    for city, event_id, temp in (
        ("Amsterdam", "ams", 18.6),
        ("Busan", "pus", 38.2),
        ("Seoul", "sel", 31.5),
    ):
        rows.append(
            {
                "schema_version": "weather_high_frequency_observation_v1",
                "city": city,
                "source": "knmi" if city == "Amsterdam" else "amos_runway",
                "source_status": "ok",
                "target_date": "2026-08-02",
                "observation_time_utc": "2026-08-02T02:56:00Z",
                "available_at_utc": "2026-08-02T02:56:19Z",
                "material_state_change": True,
                "information_event_id": event_id,
                "temp_c": temp,
            }
        )
    _write_jsonl(source, rows)
    output = tmp_path / "runtime"
    profiles = [
        _profile("Amsterdam", "knmi", source),
        _profile("Busan", "amos_runway", source),
        _profile("Seoul", "amos_runway", source),
    ]
    config = {
        "schema_version": AUTHORITATIVE_CONFIG_SCHEMA_VERSION,
        "framework_id": "weather_city_intraday_runtime_v1",
        "strategy_family": "weather.city_intraday_probability",
        "output_schema_version": AUTHORITATIVE_OUTPUT_SCHEMA_VERSION,
        "output_schema_fingerprint": AUTHORITATIVE_OUTPUT_SCHEMA_FINGERPRINT,
        "execution_mode": "zero_notional_shadow",
        "orders_submitted": 0,
        "output_dir": str(output),
        "profiles": profiles,
    }
    sink = DecisionContractJournalSink(
        output, cities=["Amsterdam", "Busan", "Seoul"]
    )
    runtime = ShadowRuntime(
        config,
        {"observation_coverage_v1": ObservationCoverageAdapter()},
        decision_sink=sink,
    )

    first = runtime.run_once(datetime(2026, 8, 2, 2, 57, tzinfo=UTC))
    second = runtime.run_once(datetime(2026, 8, 2, 2, 58, tzinfo=UTC))

    assert first["checkpoint_blockers"] == 3
    assert first["decision_contract_output"]["written_blockers"] == 3
    assert second["decision_contract_output"]["written_blockers"] == 0
    assert not (output / "decision_bundles.jsonl").exists()
    assert not (output / "trade_intents.jsonl").exists()
    assert not (output / "runtime_errors.jsonl").exists()
    assert len((output / "checkpoint_blockers.jsonl").read_text().splitlines()) == 3
