from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.weather_current_yes_core_carry_pre_live_v1 import (
    checkpoint_clock,
    checkpoint_eligible,
    observation_freshness_valid,
    read_snapshot_decisions,
    submitted_family_city_days,
)
from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (
    DEFAULT_ARTIFACT,
    LEGACY_V1_ARTIFACT,
    evaluate_entry,
    load_artifact,
    official_weather_fee_per_share,
    score_probability,
    walk_ask_ladder,
)


GOLDEN = (
    ROOT
    / "docs/analysis/2026-07/generated/current_yes_core_carry_freeze_pre_live_v4/model_golden_rows.json"
)
GOLDEN_V2 = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_no_obs_age_freeze_pre_live_v5/model_golden_rows.json"
)


def test_legacy_v1_artifact_matches_golden_probabilities() -> None:
    artifact = load_artifact(LEGACY_V1_ARTIFACT)
    rows = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(rows) == 12
    for row in rows:
        assert score_probability(row["features"], artifact) == pytest.approx(
            row["expected_probability"], abs=1e-12
        )


def test_active_v2_artifact_matches_golden_and_excludes_observation_age() -> None:
    artifact = load_artifact()
    rows = json.loads(GOLDEN_V2.read_text(encoding="utf-8"))
    assert artifact["artifact_version"] == "current_yes_core_carry_model_v2"
    assert "obs_age_min" not in artifact["numeric_features"]
    assert artifact["entry_policy"]["observation_age_probability_feature"] is False
    for row in rows:
        assert score_probability(row["features"], artifact) == pytest.approx(
            row["expected_probability"], abs=1e-12
        )


def test_five_share_ladder_uses_depth_and_per_level_fee() -> None:
    result = walk_ask_ladder(
        [{"price": "0.90", "size": "2"}, {"price": "0.91", "size": "4"}],
        5,
    )
    assert result["executable"] is True
    assert result["principal_vwap"] == pytest.approx((2 * 0.90 + 3 * 0.91) / 5)
    expected_fee = 2 * official_weather_fee_per_share(
        0.90
    ) + 3 * official_weather_fee_per_share(0.91)
    assert result["fee"] == pytest.approx(expected_fee)


def test_live_fractional_hour_is_bucketed_like_historical_and_missing_is_imputed() -> None:
    artifact = load_artifact()
    state = {
        "current_yes_bid": 0.89,
        "current_yes_ask": 0.90,
        "decision_hour_local": 13.5,
        "forecast_peak_delta_hours_local": None,
        "dewpoint_depression_f": None,
        "wind_speed_kt": None,
        "obs_age_min": None,
        "checkpoint_eligible": True,
    }
    result = evaluate_entry(state, [{"price": 0.90, "size": 10}], artifact)
    assert result["features"]["decision_hour_local"] == 13
    assert result["model_probability_hold"] is not None
    assert set(result["imputed_features"]) == {
        "forecast_peak_delta_hours_local",
        "dewpoint_depression_f",
        "wind_speed_kt",
    }


def test_checkpoint_is_half_hour_once_per_hour_then_city_day_lock() -> None:
    before = {
        "city": "Busan",
        "target_date": "2026-07-23",
        "decision_snapshot_ts_utc": "2026-07-23T04:29:00Z",
    }
    at = {**before, "decision_snapshot_ts_utc": "2026-07-23T04:30:00Z"}
    assert checkpoint_clock(before) == (13, 29)
    assert checkpoint_eligible(before, set(), set()) == (
        False,
        "before_local_half_hour_checkpoint",
    )
    eligible, reason = checkpoint_eligible(at, set(), set())
    assert eligible is True
    assert reason == "first_unscored_checkpoint_at_or_after_local_half_hour"
    assert checkpoint_eligible(at, {"Busan|2026-07-23|13"}, set()) == (
        False,
        "local_hour_already_scored",
    )
    assert checkpoint_eligible(at, set(), {"Busan|2026-07-23"}) == (
        False,
        "city_day_locked_after_first_positive_ev",
    )


def test_observation_age_is_validity_evidence_not_probability_input() -> None:
    row = {
        "obs_status": "ok",
        "station_gap_state": "within_expected_cadence",
        "obs_age_min": 5.0,
        "expected_report_cadence": 30.0,
        "source_report_ts_utc": "2026-07-23T10:20:00Z",
    }
    assert observation_freshness_valid(row) == (True, "freshness_lineage_valid")
    assert observation_freshness_valid(
        {**row, "station_gap_state": "stale_beyond_expected_cadence"}
    ) == (False, "observation_outside_expected_cadence")
    artifact = load_artifact()
    state = {
        "current_yes_bid": 0.89,
        "current_yes_ask": 0.90,
        "decision_hour_local": 14,
        "forecast_peak_delta_hours_local": 1.0,
        "dewpoint_depression_f": 12.0,
        "wind_speed_kt": 8.0,
        "obs_age_min": 2.0,
        "checkpoint_eligible": True,
    }
    fresh = evaluate_entry(state, [{"price": 0.90, "size": 10}], artifact)
    stale = evaluate_entry(
        {**state, "obs_age_min": 55.0},
        [{"price": 0.90, "size": 10}],
        artifact,
    )
    assert fresh["model_probability_hold"] == stale["model_probability_hold"]


def test_artifact_is_zero_notional_pre_live_only() -> None:
    artifact = json.loads(DEFAULT_ARTIFACT.read_text(encoding="utf-8"))
    assert artifact["lifecycle"] == "pre_live_frozen_zero_notional_only"
    source = (
        ROOT / "scripts/ops/weather_current_yes_core_carry_pre_live_v1.py"
    ).read_text(encoding="utf-8")
    assert "py_clob_client" not in source
    assert "create_order" not in source
    assert "post_order" not in source


def test_family_dedupe_reads_only_submitted_city_days(tmp_path: Path) -> None:
    journal = tmp_path / "live_orders.jsonl"
    journal.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "status": "submitted",
                        "city": "Busan",
                        "target_date": "2026-07-23",
                    }
                ),
                json.dumps(
                    {
                        "status": "cancelled",
                        "city": "Ankara",
                        "target_date": "2026-07-23",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert submitted_family_city_days((journal,)) == {"Busan|2026-07-23"}


def test_same_snapshot_collector_replay_is_deduplicated(tmp_path: Path) -> None:
    ledger = tmp_path / "state_decisions.jsonl"
    row = {
        "shadow_decision_id": "stable-id",
        "snapshot_file": "snapshot_1.json",
        "city": "Busan",
    }
    ledger.write_text(
        json.dumps(row) + "\n" + json.dumps({**row, "created_at_utc": "later"}) + "\n",
        encoding="utf-8",
    )
    rows = read_snapshot_decisions(ledger, "snapshot_1.json")
    assert len(rows) == 1
    assert rows[0]["created_at_utc"] == "later"
