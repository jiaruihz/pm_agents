import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.strategies.weather_city_probability_shadow.tokyo import (
    TokyoMarketAnchorAdapter,
    _jma_history,
    _market_prices,
    _official_history,
    _previous_weather_probability,
)


ROOT = Path(__file__).resolve().parents[2]


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_jma_history_uses_earliest_exact_first_seen(tmp_path):
    path = tmp_path / "jma.jsonl"
    base = {
        "city": "Tokyo", "source": "jma_amedas", "source_status": "ok",
        "target_date": "2026-08-01", "observation_time_utc": "2026-08-01T01:00:00Z",
        "temp_c": 32.1,
    }
    _write_jsonl(path, [
        {**base, "source_first_seen_at_utc": "2026-08-01T01:07:00Z", "payload_hash": "first"},
        {**base, "source_first_seen_at_utc": "2026-08-01T01:09:00Z", "payload_hash": "late_backfill"},
    ])
    rows = _jma_history(path, "2026-08-01", datetime(2026, 8, 1, 1, tzinfo=timezone.utc))
    assert len(rows) == 1
    assert rows[0]["payload_hash"] == "first"


def test_official_history_excludes_snapshots_fetched_after_decision(tmp_path):
    path = tmp_path / "2026-08-01" / "observations.jsonl"
    base = {
        "city": "Tokyo", "target_date": "2026-08-01",
        "last_obs_utc": "2026-08-01T01:00:00Z", "current_temp_c": 32,
        "running_max_c": 32,
    }
    _write_jsonl(path, [
        {**base, "fetched_at_utc": "2026-08-01T01:05:00Z", "raw_metar": "PIT"},
        {**base, "fetched_at_utc": "2026-08-01T01:20:00Z", "raw_metar": "FUTURE"},
    ])
    rows = _official_history(
        tmp_path, "2026-08-01", datetime(2026, 8, 1, 1, 10, tzinfo=timezone.utc)
    )
    assert [row["raw_metar"] for row in rows] == ["PIT"]


def test_no_book_reconstructs_complementary_yes_prices():
    prices = _market_prices({"summary": {"best_ask": .42, "best_bid": .40}})
    assert prices["no_mid"] == pytest.approx(.41)
    assert prices["yes_mid"] == pytest.approx(.59)
    assert prices["yes_ask"] == pytest.approx(.60)


def test_previous_weather_probability_reads_authoritative_decision_bundle(tmp_path):
    path = tmp_path / "decision_bundles.jsonl"
    _write_jsonl(path, [{
        "signal_candidate": {
            "city": "Tokyo",
            "target_date": "2026-08-01",
            "bracket": "35",
            "side": "YES",
        },
        "model_output": {
            "metadata": {
                "source_obs_ts_utc": "2026-08-01T05:00:00+00:00",
                "weather_probability_stay": 0.63,
            }
        },
    }])
    value = _previous_weather_probability(
        [path],
        "2026-08-01",
        35,
        datetime(2026, 8, 1, 5, 10, tzinfo=timezone.utc),
    )
    assert value == pytest.approx(0.63)


def test_tokyo_adapter_ignores_stale_book_after_scoring_window(tmp_path):
    official_path = tmp_path / "official" / "2026-08-01" / "observations.jsonl"
    _write_jsonl(official_path, [{
        "city": "Tokyo", "target_date": "2026-08-01", "status": "ok",
        "fetched_at_utc": "2026-08-01T10:45:00Z",
        "last_obs_utc": "2026-08-01T10:40:00Z", "current_temp_c": 35.0,
        "running_max_c": 35.0,
    }])
    book_dir = tmp_path / "books"
    _write_jsonl(book_dir / "2026-08-01.jsonl", [{
        "city": "Tokyo", "source": "jma_amedas", "outcome": "no",
        "book_status": "ok", "target_date": "2026-08-01",
        "book_fetched_at_utc": "2026-08-01T10:50:00Z",
        "source_obs_ts_utc": "2026-08-01T10:40:00Z",
        "reference_market_value": 30, "bracket": "35°C",
        "question": "Will the highest temperature in Tokyo be 35°C?",
        "summary": {"best_ask": .42, "best_bid": .40},
    }])
    profile = {
        "forward_start_utc": "2026-08-01T00:00:00Z",
        "max_book_age_seconds": 180,
        "book_dir": str(book_dir),
        "source_journal": str(tmp_path / "missing-jma.jsonl"),
        "observation_journal_dir": str(tmp_path / "official"),
    }

    scores = TokyoMarketAnchorAdapter().score(
        profile, datetime(2026, 8, 1, 13, 0, tzinfo=timezone.utc)
    )

    assert scores == []


def test_tokyo_adapter_scores_both_sides_from_pit_first_seen(tmp_path):
    source_path = tmp_path / "jma.jsonl"
    source_rows = []
    for minute, temp in zip(range(0, 70, 10), (30.0, 30.4, 30.8, 31.2, 31.6, 32.1, 31.9)):
        hour, minute_in_hour = divmod(minute, 60)
        observed = f"2026-08-01T{hour:02d}:{minute_in_hour:02d}:00Z"
        seen = f"2026-08-01T{hour:02d}:{minute_in_hour + 7:02d}:00Z"
        source_rows.append({
            "city": "Tokyo", "source": "jma_amedas", "source_status": "ok",
            "target_date": "2026-08-01", "observation_time_utc": observed,
            "source_first_seen_at_utc": seen, "temp_c": temp,
            "payload_hash": observed, "raw_payload_hash": observed + "-raw",
        })
    _write_jsonl(source_path, source_rows)
    official_path = tmp_path / "official" / "2026-08-01" / "observations.jsonl"
    _write_jsonl(official_path, [{
        "city": "Tokyo", "target_date": "2026-08-01",
        "fetched_at_utc": "2026-08-01T01:05:00Z",
        "last_obs_utc": "2026-08-01T01:00:00Z", "current_temp_c": 31.0,
        "running_max_c": 32.0, "wind_speed_kt": 5.0, "wind_dir_deg": 180.0,
        "dwpf_now": 75.2, "relative_humidity_pct": 60.0, "ceiling_ft_agl": 5000,
        "precip_observed": False,
        "raw_metar": "METAR RJTT 010100Z 18005KT 9999 FEW050 31/24 Q1004",
        "source": "aviationweather_metar",
    }])
    book_dir = tmp_path / "books"
    _write_jsonl(book_dir / "2026-08-01.jsonl", [{
        "city": "Tokyo", "source": "jma_amedas", "outcome": "no",
        "relative_offset": -1, "book_status": "ok", "target_date": "2026-08-01",
        "book_fetched_at_utc": "2026-08-01T01:08:00Z",
        "source_obs_ts_utc": "2026-08-01T01:00:00Z", "reference_market_value": 33,
        "bracket": "32°C", "question": "Will the highest temperature in Tokyo be 32°C?",
        "summary": {"best_ask": .42, "best_bid": .40}, "token_id": "paper",
    }, {
        "city": "Tokyo", "source": "jma_amedas", "outcome": "no",
        "relative_offset": 0, "book_status": "ok", "target_date": "2026-08-01",
        "book_fetched_at_utc": "2026-08-01T01:08:01Z",
        "source_obs_ts_utc": "2026-08-01T01:00:00Z", "reference_market_value": 33,
        "bracket": "33°C", "question": "Will the highest temperature in Tokyo be 33°C?",
        "summary": {"best_ask": .52, "best_bid": .50}, "token_id": "paper-33",
    }])
    weather_dir = ROOT / "docs/analysis/2026-07/generated/tokyo_current_break_binary_v5/models"
    offset_dir = ROOT / "docs/analysis/2026-07/generated/tokyo_market_anchor_binary_v6/models"
    profile = {
        "forward_start_utc": "2026-08-01T00:00:00Z", "max_book_age_seconds": 180,
        "book_dir": str(book_dir), "source_journal": str(source_path),
        "observation_journal_dir": str(tmp_path / "official"),
        "evaluation_journal": str(tmp_path / "evaluations.jsonl"),
        "artifacts": {
            "weather": {
                "path": str(weather_dir / "binary_multigrain_hgb_v5.joblib"),
                "sha256": "c329b133c35384c2ba04689e82d28f6e06efeae4db2a66a4c3fb7bcef98b3175",
                "spec_path": str(weather_dir / "binary_multigrain_hgb_v5.spec.json"),
                "spec_sha256": "31eaed86215cc287d3e97f4699e07e34b3cb50a0d69de11d692336902da2dc81",
            },
            "offset": {
                "path": str(offset_dir / "offset_physical_ridge1_v6.joblib"),
                "sha256": "c7ad6b9abff0433deb1fd4af21a17b1544ea482f46b96af7a49f5b97423b98df",
                "spec_path": str(offset_dir / "offset_physical_ridge1_v6.spec.json"),
                "spec_sha256": "67f78b5f6d0f152e93d052404ca795c0c41e810a1b74c29dc9e995757adeaa83",
            },
        },
    }
    scores = TokyoMarketAnchorAdapter().score(
        profile, datetime(2026, 8, 1, 1, 9, tzinfo=timezone.utc)
    )
    assert [score.market_side for score in scores] == ["YES", "NO"]
    assert all(score.current_bracket == 32 for score in scores)
    assert scores[0].model_probability + scores[1].model_probability == pytest.approx(1.0)
    assert scores[0].lineage["is_observed_state_entry"] is True
    assert scores[0].model_probability == pytest.approx(scores[0].market_probability)
    assert scores[0].model_id == "tokyo_state_entry_routed_market_residual_v7"
    assert scores[0].lineage["source_first_seen_at_utc"] == "2026-08-01T01:07:00Z"
    assert scores[0].lineage["official_snapshot_fetched_at_utc"] == "2026-08-01T01:05:00Z"
    assert scores[0].lineage["source_lattice_anchor"] == 33
    assert scores[0].lineage["official_lattice_anchor"] == 32
    assert scores[0].lineage["market_expression_anchor"] == 32

    overshoot_path = (
        ROOT
        / "docs/analysis/2026-08/generated/tokyo_overshoot_market_residual_v2"
        / "tokyo_overshoot_market_residual_v2.joblib"
    )
    overshoot_profile = {
        **profile,
        "model_id": "tokyo_overshoot_market_residual_v2",
        "probability_policy": "overshoot_market_residual_v2",
        "artifacts": {
            "overshoot": {
                "path": str(overshoot_path),
                "sha256": "298b89ee629b7bf6e3fb6a074fa737de98e47156edba81b02c08ea20e784265e",
                "spec_path": str(overshoot_path.with_suffix(".spec.json")),
                "spec_sha256": "59a7a7f1ee0a7defa1bf4405db27edbedddf6cdddb8825467b36d6037cbca2ab",
            }
        },
    }
    overshoot = TokyoMarketAnchorAdapter().score(
        overshoot_profile, datetime(2026, 8, 1, 1, 9, tzinfo=timezone.utc)
    )
    assert [score.market_side for score in overshoot] == ["YES", "NO"]
    assert overshoot[0].model_probability + overshoot[1].model_probability == pytest.approx(1.0)
    assert overshoot[1].lineage["probability_target"] == "leave_current_exact_bracket"
    assert overshoot[1].lineage["training_clock_class"] == "archive_reconstructed_plus_15m_price_proxy"
    assert overshoot[1].model_id == "tokyo_overshoot_market_residual_v2"
