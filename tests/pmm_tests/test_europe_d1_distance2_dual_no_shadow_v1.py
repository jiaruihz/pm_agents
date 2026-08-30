import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.ops.weather_europe_d1_distance2_dual_no_shadow_v1 import (
    build_cycle,
    load_versions,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_jsonl_gz(path: Path, rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def config_payload() -> dict:
    return {
        "target_lead_days": 1,
        "local_entry_hour_start": 12,
        "local_entry_hour_end": 24,
        "distance_from_nearest_endpoint": 2,
        "allocation": {
            "low_distance2_no": 0.5,
            "high_distance2_no": 0.5,
        },
        "frozen_forward": {
            "joint_market_probability_max": 0.10,
        },
        "feature_model_labels": ["ECMWF", "GFS", "ICON"],
        "cities": {
            "Amsterdam": {
                "timezone": "Europe/Amsterdam",
                "market_unit": "C",
            }
        },
    }


def test_load_versions_reads_only_dated_shards(tmp_path: Path) -> None:
    dated = tmp_path / "2026-07-28"
    dated.mkdir()
    row = {
        "city": "Amsterdam",
        "forecast_target_date": "2026-07-29",
        "model_label": "ECMWF",
        "available_at_utc": "2026-07-28T15:00:00Z",
    }
    write_jsonl(dated / "forecast_versions.jsonl", [row])
    write_jsonl(
        tmp_path / "forecast_versions.jsonl",
        [{**row, "model_label": "ROOT_DUPLICATE"}],
    )

    history = load_versions(
        tmp_path, datetime(2026, 7, 28, 16, tzinfo=timezone.utc)
    )

    assert set(history) == {("Amsterdam", "2026-07-29", "ECMWF")}


def test_load_versions_bounds_partitions_for_current_target_dates(
    tmp_path: Path,
) -> None:
    recent = tmp_path / "2026-07-28"
    recent.mkdir()
    old = tmp_path / "2026-07-20"
    old.mkdir()
    recent_row = {
        "city": "Amsterdam",
        "forecast_target_date": "2026-07-29",
        "model_label": "ECMWF",
        "available_at_utc": "2026-07-28T15:00:00Z",
    }
    write_jsonl(recent / "forecast_versions.jsonl", [recent_row])
    write_jsonl(
        old / "forecast_versions.jsonl",
        [{**recent_row, "model_label": "OLD_PARTITION"}],
    )

    history = load_versions(
        tmp_path,
        datetime(2026, 7, 28, 16, tzinfo=timezone.utc),
        target_dates={"2026-07-29"},
    )

    assert set(history) == {("Amsterdam", "2026-07-29", "ECMWF")}


def test_build_cycle_selects_paired_distance_two_without_orders(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_payload()), encoding="utf-8")
    feature_policy = tmp_path / "feature_policy.json"
    feature_policy.write_text(
        json.dumps(
            {
                "training_cutoff": "2026-07-07",
                "records": [
                    {
                        "city": "Amsterdam",
                        "model_label": model,
                        "bias_correction_f": 0,
                        "residual_std_f": 2,
                    }
                    for model in ("ECMWF", "GFS", "ICON")
                ],
            }
        ),
        encoding="utf-8",
    )
    versions = tmp_path / "forecast_versions.jsonl"
    write_jsonl(
        versions,
        [
            {
                "city": "Amsterdam",
                "forecast_target_date": "2026-07-29",
                "model_label": model,
                "forecast_max_f": value,
                "available_at_utc": "2026-07-28T15:00:00Z",
            }
            for model, value in (
                ("ECMWF", 82),
                ("GFS", 84),
                ("ICON", 83),
            )
        ],
    )
    with versions.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "city": "Amsterdam",
                    "forecast_target_date": "2026-07-29",
                    "model_label": "ECMWF",
                    "forecast_max_f": 100,
                    "available_at_utc": "2026-07-28T16:00:01Z",
                }
            )
            + "\n"
        )
    books = tmp_path / "books.jsonl.gz"
    write_jsonl_gz(
        books,
        [
            {
                "city": city,
                "event_date": "2026-07-29",
                "outcome": "no",
                "bracket": str(bracket),
                "fetched_at_utc": "2026-07-28T16:00:00Z",
                "available_at_utc": "2026-07-28T16:00:00Z",
                "request_batch_capture_id": "batch-1",
                "event_time_pit_scorable": True,
                "status": "ok",
                "summary": {
                    "best_bid": 0.90,
                    "best_ask": 0.90,
                    "ask_size": 10,
                },
            }
            for city in ("Amsterdam", "Tokyo")
            for bracket in range(20, 29)
        ],
    )

    payload = build_cycle(
        book_path=books,
        versions_path=versions,
        config_path=config,
        feature_policy_path=feature_policy,
        repo_sha="abc123",
    )

    assert payload["orders_submitted"] == 0
    assert payload["actual_notional_usd"] == 0.0
    assert payload["signal_funnel"]["fixed_europe_city_dates"] == 1
    assert len(payload["records"]) == 1
    basket = payload["records"][0]
    assert basket["city"] == "Amsterdam"
    assert basket["decision_status"] == "would_shadow_entry"
    assert basket["paired_book_clock_complete"] is True
    assert basket["request_batch_capture_id"] == "batch-1"
    assert basket["book_available_at_utc"] == "2026-07-28T16:00:00Z"
    assert basket["joint_market_probability_max"] == 0.10
    assert basket["joint_market_probability"] == pytest.approx(0.10)
    assert basket["market_tail_eligible"] is True
    assert basket["weather_features_used_for_eligibility"] is False
    assert basket["weather_features"]["feature_status"] == "available"
    ecmwf = next(
        row
        for row in basket["weather_features"]["model_rows"]
        if row["model_label"] == "ECMWF"
    )
    assert ecmwf["forecast_max_f"] == 82
    assert ecmwf["available_at_utc"] < basket["decision_asof_utc"]
    assert [leg["bracket"] for leg in basket["legs"]] == ["22", "26"]
    assert [leg["rung_index"] for leg in basket["legs"]] == [2, 6]
    assert [leg["allocation_weight"] for leg in basket["legs"]] == [0.5, 0.5]


def test_build_cycle_keeps_unexecutable_pair_in_evidence_denominator(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_payload()), encoding="utf-8")
    feature_policy = tmp_path / "feature_policy.json"
    feature_policy.write_text("{}", encoding="utf-8")
    versions = tmp_path / "forecast_versions.jsonl"
    versions.write_text("", encoding="utf-8")
    books = tmp_path / "books.jsonl.gz"
    write_jsonl_gz(
        books,
        [
            {
                "city": "Amsterdam",
                "event_date": "2026-07-29",
                "outcome": "no",
                "bracket": str(bracket),
                "fetched_at_utc": "2026-07-28T16:00:00Z",
                "available_at_utc": "2026-07-28T16:00:00Z",
                "request_batch_capture_id": "batch-1",
                "event_time_pit_scorable": True,
                "status": "ok",
                "summary": {
                    "best_bid": 0.88,
                    "best_ask": None if bracket == 22 else 0.90,
                    "ask_size": None if bracket == 22 else 10,
                },
            }
            for bracket in range(20, 29)
        ],
    )

    payload = build_cycle(
        book_path=books,
        versions_path=versions,
        config_path=config,
        feature_policy_path=feature_policy,
    )

    assert len(payload["records"]) == 1
    assert payload["records"][0]["decision_status"] == "paired_book_unexecutable"
    assert payload["records"][0]["weather_features"]["feature_status"] == (
        "insufficient_models"
    )
    assert payload["evidence_funnel"]["paired_executable_city_dates"] == 0
    assert payload["evidence_funnel"]["actual_orders"] == 0


def test_build_cycle_rejects_joint_market_probability_above_threshold(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    payload_config = config_payload()
    payload_config["allocation"] = {
        "low_distance2_no": 0.25,
        "high_distance2_no": 0.75,
    }
    config.write_text(json.dumps(payload_config), encoding="utf-8")
    feature_policy = tmp_path / "feature_policy.json"
    feature_policy.write_text("{}", encoding="utf-8")
    versions = tmp_path / "forecast_versions.jsonl"
    versions.write_text("", encoding="utf-8")
    books = tmp_path / "books.jsonl.gz"
    write_jsonl_gz(
        books,
        [
            {
                "city": "Amsterdam",
                "event_date": "2026-07-29",
                "outcome": "no",
                "bracket": str(bracket),
                "fetched_at_utc": "2026-07-28T16:00:00Z",
                "available_at_utc": "2026-07-28T16:00:00Z",
                "request_batch_capture_id": "batch-1",
                "event_time_pit_scorable": True,
                "status": "ok",
                "summary": {
                    "best_bid": 0.80 if bracket == 26 else 0.95,
                    "best_ask": 0.96,
                    "ask_size": 10,
                },
            }
            for bracket in range(20, 29)
        ],
    )

    payload = build_cycle(
        book_path=books,
        versions_path=versions,
        config_path=config,
        feature_policy_path=feature_policy,
    )

    basket = payload["records"][0]
    assert basket["joint_market_probability"] == pytest.approx(0.10125)
    assert basket["market_tail_eligible"] is False
    assert basket["decision_status"] == "market_joint_probability_above_threshold"
    assert payload["orders_submitted"] == 0
    assert basket["actual_notional_usd"] == 0.0


def test_build_cycle_marks_market_probability_unavailable(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_payload()), encoding="utf-8")
    feature_policy = tmp_path / "feature_policy.json"
    feature_policy.write_text("{}", encoding="utf-8")
    versions = tmp_path / "forecast_versions.jsonl"
    versions.write_text("", encoding="utf-8")
    books = tmp_path / "books.jsonl.gz"
    write_jsonl_gz(
        books,
        [
            {
                "city": "Amsterdam",
                "event_date": "2026-07-29",
                "outcome": "no",
                "bracket": str(bracket),
                "fetched_at_utc": "2026-07-28T16:00:00Z",
                "available_at_utc": "2026-07-28T16:00:00Z",
                "request_batch_capture_id": "batch-1",
                "event_time_pit_scorable": True,
                "status": "ok",
                "summary": {
                    "best_bid": None if bracket == 22 else 0.90,
                    "best_ask": 0.90,
                    "ask_size": 10,
                },
            }
            for bracket in range(20, 29)
        ],
    )

    payload = build_cycle(
        book_path=books,
        versions_path=versions,
        config_path=config,
        feature_policy_path=feature_policy,
    )

    basket = payload["records"][0]
    assert basket["paired_book_executable"] is True
    assert basket["joint_market_probability"] is None
    assert basket["market_tail_eligible"] is False
    assert basket["decision_status"] == "market_joint_probability_unavailable"


def test_build_cycle_rejects_invalid_market_tail_configuration(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    invalid = config_payload()
    invalid["frozen_forward"]["joint_market_probability_max"] = 1.01
    config.write_text(json.dumps(invalid), encoding="utf-8")
    books = tmp_path / "books.jsonl.gz"
    write_jsonl_gz(books, [])

    with pytest.raises(ValueError, match="joint_market_probability_max"):
        build_cycle(
            book_path=books,
            versions_path=tmp_path,
            config_path=config,
            feature_policy_path=tmp_path / "feature_policy.json",
        )


@pytest.mark.parametrize(
    "allocation",
    [
        {"low_distance2_no": 0.6, "high_distance2_no": 0.6},
        {"low_distance2_no": -0.1, "high_distance2_no": 1.1},
        {"low_distance2_no": "nan", "high_distance2_no": 1.0},
    ],
)
def test_build_cycle_rejects_invalid_allocation(
    tmp_path: Path,
    allocation: dict,
) -> None:
    config = tmp_path / "config.json"
    invalid = config_payload()
    invalid["allocation"] = allocation
    config.write_text(json.dumps(invalid), encoding="utf-8")
    books = tmp_path / "books.jsonl.gz"
    write_jsonl_gz(books, [])

    with pytest.raises(ValueError, match="allocation weights"):
        build_cycle(
            book_path=books,
            versions_path=tmp_path,
            config_path=config,
            feature_policy_path=tmp_path / "feature_policy.json",
        )


def test_build_cycle_rejects_mixed_capture_clock(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_payload()), encoding="utf-8")
    feature_policy = tmp_path / "feature_policy.json"
    feature_policy.write_text("{}", encoding="utf-8")
    versions = tmp_path / "forecast_versions.jsonl"
    versions.write_text("", encoding="utf-8")
    books = tmp_path / "books.jsonl.gz"
    write_jsonl_gz(
        books,
        [
            {
                "city": "Amsterdam",
                "event_date": "2026-07-29",
                "outcome": "no",
                "bracket": str(bracket),
                "fetched_at_utc": "2026-07-28T16:00:00Z",
                "available_at_utc": "2026-07-28T16:00:00Z",
                "request_batch_capture_id": (
                    "batch-2" if bracket == 26 else "batch-1"
                ),
                "event_time_pit_scorable": True,
                "status": "ok",
                "summary": {
                    "best_bid": 0.90,
                    "best_ask": 0.90,
                    "ask_size": 10,
                },
            }
            for bracket in range(20, 29)
        ],
    )

    payload = build_cycle(
        book_path=books,
        versions_path=versions,
        config_path=config,
        feature_policy_path=feature_policy,
    )

    basket = payload["records"][0]
    assert basket["paired_book_clock_complete"] is False
    assert basket["paired_book_executable"] is False
    assert basket["decision_status"] == "paired_book_clock_unavailable"
    assert basket["market_tail_eligible"] is False
