from __future__ import annotations

import json
import math
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from weather_model_evaluation.korea_airport_forward import (
    KoreaAirportForwardConfig,
    _attach_execution_evidence_status,
    _clock_audit,
    _json_safe,
    _prepare_output_dir,
    _replay_binary,
    attach_busan_checkpoint_clocks,
    build_source_basis_audit,
    crossno_model_branch_identity,
    load_busan_decision_bundles,
    load_condition_settlements,
)
from weather_model_evaluation.korea_city_exact_no import (
    _base_checkpoint_row,
    load_settlement_winners,
)


def test_airport_forward_is_crossno_model_branch_not_strategy_family() -> None:
    identity = crossno_model_branch_identity(("Seoul", "Busan"))

    assert identity["parent_strategy_family"] == "cross_no_v2_metar"
    assert identity["parent_runtime_strategy_id"] == "cross_no_v2_metar_v1"
    assert identity["branch_role"] == "city_specific_probability_model_challenger"
    assert identity["incumbent_decision_form"] == "rule_based_source_cross"
    assert identity["comparison_scope"] == "same_row_market_only_in_this_forward"
    assert identity["incumbent_rule_replay"] == "not_run"
    assert identity["artifact_experiment_role"] == (
        "forward_evaluator_not_independent_strategy"
    )
    assert identity["production_replacement_status"] == "none"
    assert identity["city_model_ids"] == {
        "Seoul": "seoul_intraday_remaining_heat_distribution",
        "Busan": "busan_intraday_exact_no_online_market_prior_residual",
    }


def _basis_row(
    *,
    target_date: str,
    rung: int,
    winner: str | None,
    running_max: float,
    lag_seconds: float = 2.0,
) -> dict:
    stamp = f"{target_date}T03:00:00Z"
    return {
        "city": "Busan",
        "target_date": target_date,
        "routine_rung": rung,
        "source_event_key": f"{target_date}|{rung}",
        "source_observation_ts_utc": stamp,
        "source_first_seen_ts_utc": stamp,
        "source_available_at_utc": stamp,
        "feature_book_available_at_utc": stamp,
        "forecast_available_at_utc": stamp,
        "collector_emitted_at_utc": stamp,
        "decision_ts_utc": stamp,
        "winner_bracket": winner,
        "max_runway_running_max_c": running_max,
        "preferred_running_max_c": running_max,
        "book_status": "scorable",
        "source_to_book_lag_seconds": lag_seconds,
        "effective_no_ask": 0.6,
        "effective_no_ask_size": 10.0,
        "feature_book_snapshot_id": f"book-{target_date}",
        "raw_ref": f"fixture#{target_date}",
    }


def test_source_basis_keeps_confirmed_false_and_unsettled_denominators() -> None:
    frame = pd.DataFrame(
        [
            _basis_row(
                target_date="2026-08-12", rung=29, winner="30", running_max=29.6
            ),
            _basis_row(
                target_date="2026-08-13", rung=30, winner="30", running_max=30.6
            ),
            _basis_row(
                target_date="2026-08-14", rung=31, winner=None, running_max=31.7
            ),
            _basis_row(
                target_date="2026-08-15", rung=32, winner="33", running_max=32.4
            ),
        ]
    )

    summary, records = build_source_basis_audit(
        frame, city="Busan", station_id="RKPK", fresh_book_seconds=10.0
    )

    group = summary["anchors"]["group_max"]
    assert group["cross_entries"] == 3
    assert group["settled_cross_entries"] == 2
    assert group["confirmed_entries"] == 1
    assert group["terminal_false_entries"] == 1
    assert group["fresh_executable_confirmed_entries"] == 1
    assert group["fresh_executable_terminal_false_entries"] == 1
    assert set(records["basis_status"]) == {
        "confirmed",
        "terminal_false",
        "settlement_missing",
    }


def _bundle(
    *, candidate_id: str, bracket: str, selected: bool, status: str = "scored"
) -> dict:
    available = "2026-08-12T02:00:01Z"
    decision = "2026-08-12T02:00:02Z"
    blocked = status != "scored"
    return {
        "bundle_record_id": f"bundle-{candidate_id}-{selected}",
        "information_event": {
            "city": "Busan",
            "content_key": f"event-{candidate_id}",
            "source_event_ts_utc": "2026-08-12T02:00:00Z",
            "available_at_utc": available,
        },
        "model_output": {"metadata": {}},
        "signal_candidate": {
            "candidate_id": candidate_id,
            "checkpoint_id": f"checkpoint-{candidate_id}",
            "trigger_event_id": f"event-{candidate_id}",
            "city": "Busan",
            "target_date": "2026-08-12",
            "decision_ts_utc": decision,
            "bracket": bracket,
            "condition_id": f"condition-{bracket}" if not blocked else None,
            "market_id": f"market-{bracket}" if not blocked else None,
            "token_id": f"token-{bracket}" if not blocked else None,
            "side": "NO",
            "candidate_status": status,
            "blocker_reason": "fixture_blocker" if blocked else None,
            "market_evidence_status": "available" if not blocked else "missing",
            "market_p": 0.6 if not blocked else None,
            "p_model": 0.7 if not blocked else None,
            "executable_cost": 0.65 if not blocked else None,
            "selected": selected,
            "metadata": {
                "legacy_would_enter": selected,
                "edge_after_fee": 0.05 if selected else -0.05,
            },
            "model_id": "busan_fixture",
            "model_artifact_id": "artifact-sha",
            "feature_book_snapshot_id": "feature-book",
            "execution_book_snapshot_id": "execution-book",
            "input_refs": [
                {
                    "role": "checkpoint",
                    "physical_path": "/fixture/checkpoints",
                    "record_identity": f"event-{candidate_id}",
                    "schema_version": "korea_amos_first_seen_state_v1",
                }
            ],
        },
    }


def test_busan_bundle_loader_deduplicates_candidate_and_prefers_selected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decision_bundles.jsonl"
    payloads = [
        _bundle(candidate_id="same", bracket="29", selected=False),
        _bundle(candidate_id="same", bracket="29", selected=True),
        _bundle(
            candidate_id="blocked", bracket="30", selected=False, status="blocked"
        ),
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in payloads), encoding="utf-8"
    )

    frame, identity = load_busan_decision_bundles(
        path,
        start_date="2026-08-12",
        end_date="2026-08-12",
        expected_artifact_sha256="artifact-sha",
    )

    assert identity["busan_window_journal_rows"] == 3
    assert identity["duplicate_candidate_rows"] == 2
    assert identity["unique_candidate_rows"] == 2
    selected = frame.set_index("candidate_id").loc["same"]
    assert bool(selected["policy_selected"])
    assert bool(selected["would_enter"])
    assert frame["artifact_identity_match"].all()
    assert frame["causal_clock"].all()
    assert int(frame["is_raw_state_entry"].sum()) == 2


def test_condition_settlement_loader_uses_condition_identity_only(
    tmp_path: Path,
) -> None:
    db = tmp_path / "weather.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE settlements (
          target_date TEXT, condition_id TEXT, market_id TEXT, bracket TEXT,
          final_price REAL, settlement_status TEXT, created_at_utc TEXT
        );
        CREATE TABLE fact_signal_candidates (fact_built_at_utc TEXT);
        INSERT INTO fact_signal_candidates VALUES ('2026-08-13T00:00:00Z');
        INSERT INTO settlements VALUES
          ('2026-08-12', 'condition-29', 'market-29', '29', 0.0,
           'settled', '2026-08-12T17:00:00Z'),
          ('2026-08-12', 'other-condition', 'market-29', '29', 1.0,
           'settled', '2026-08-12T17:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    frame, identity = load_condition_settlements(
        db, condition_ids=["condition-29"]
    )

    assert len(frame) == 1
    assert frame.iloc[0]["condition_id"] == "condition-29"
    assert frame.iloc[0]["label_no"] == 1.0
    assert identity["join_key"] == "condition_id_only"
    assert identity["settled_condition_ids"] == 1


def test_condition_settlement_loader_blocks_non_binary_final_price(
    tmp_path: Path,
) -> None:
    db = tmp_path / "weather.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE settlements (
          target_date TEXT, condition_id TEXT, market_id TEXT, bracket TEXT,
          final_price REAL, settlement_status TEXT, created_at_utc TEXT
        );
        CREATE TABLE fact_signal_candidates (fact_built_at_utc TEXT);
        INSERT INTO settlements VALUES
          ('2026-08-12', 'condition-29', 'market-29', '29', 0.5,
           'settled', '2026-08-12T17:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    frame, identity = load_condition_settlements(
        db, condition_ids=["condition-29"]
    )

    assert not bool(frame.iloc[0]["settlement_binary_label_valid"])
    assert pd.isna(frame.iloc[0]["label_no"])
    assert identity["invalid_binary_label_condition_ids"] == 1


def test_bundle_loader_does_not_collapse_missing_candidate_ids(tmp_path: Path) -> None:
    path = tmp_path / "decision_bundles.jsonl"
    payloads = [
        _bundle(candidate_id="", bracket="29", selected=False),
        _bundle(candidate_id="", bracket="30", selected=False),
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in payloads), encoding="utf-8"
    )

    frame, identity = load_busan_decision_bundles(
        path,
        start_date="2026-08-12",
        end_date="2026-08-12",
        expected_artifact_sha256="artifact-sha",
    )

    assert len(frame) == 2
    assert frame["candidate_dedupe_key"].nunique() == 2
    assert identity["missing_candidate_id_rows"] == 2


def test_clock_audit_turns_missing_collector_into_blocker() -> None:
    frame = pd.DataFrame(
        [
            {
                "source_observation_ts_utc": "2026-08-12T02:00:00Z",
                "source_first_seen_ts_utc": "2026-08-12T02:00:01Z",
                "source_available_at_utc": "2026-08-12T02:00:02Z",
                "feature_book_available_at_utc": "2026-08-12T02:00:03Z",
                "forecast_available_at_utc": "2026-08-12T01:00:00Z",
                "decision_ts_utc": "2026-08-12T02:00:04Z",
            }
        ]
    )

    audit = _clock_audit(frame)

    assert audit["missing_by_clock"]["collector"] == 1
    assert audit["missing_clock_rows"] == 1
    assert not audit["pass"]


def test_clock_audit_rejects_collector_after_decision() -> None:
    frame = pd.DataFrame(
        [
            {
                "source_observation_ts_utc": "2026-08-12T02:00:00Z",
                "source_first_seen_ts_utc": "2026-08-12T02:00:01Z",
                "source_available_at_utc": "2026-08-12T02:00:02Z",
                "feature_book_available_at_utc": "2026-08-12T02:00:03Z",
                "forecast_available_at_utc": "2026-08-12T01:00:00Z",
                "collector_emitted_at_utc": "2026-08-12T02:00:05Z",
                "decision_ts_utc": "2026-08-12T02:00:04Z",
            }
        ]
    )

    audit = _clock_audit(frame)

    assert audit["collector_emitted_after_decision"] == 1
    assert audit["ordering_violation_rows"] == 1
    assert not audit["ordering_pass"]


def test_clock_audit_rejects_forecast_after_runtime_decision() -> None:
    frame = pd.DataFrame(
        [
            {
                "source_observation_ts_utc": "2026-08-12T02:00:00Z",
                "source_first_seen_ts_utc": "2026-08-12T02:00:01Z",
                "source_available_at_utc": "2026-08-12T02:00:02Z",
                "feature_book_available_at_utc": "2026-08-12T02:00:03Z",
                "forecast_available_at_utc": "2026-08-12T02:00:06Z",
                "collector_emitted_at_utc": "2026-08-12T02:00:05Z",
                "decision_ts_utc": "2026-08-12T02:00:05Z",
            }
        ]
    )

    audit = _clock_audit(frame)

    assert audit["forecast_available_after_collector"] == 1
    assert audit["forecast_available_after_decision"] == 1
    assert audit["ordering_violation_rows"] == 1
    assert not audit["pass"]


def test_checkpoint_reconstruction_waits_for_collector_emission(
    tmp_path: Path,
) -> None:
    base = _base_checkpoint_row(
        {
            "city": "Busan",
            "target_date": "2026-08-12",
            "source_event_key": "event-1",
            "source_observation_ts_utc": "2026-08-12T02:00:00Z",
            "source_first_seen_ts_utc": "2026-08-12T02:00:01Z",
            "source_available_at_utc": "2026-08-12T02:00:02Z",
            "collector_emitted_at_utc": "2026-08-12T02:00:05Z",
            "forecast_context": {
                "forecast_available_at_utc": "2026-08-12T02:00:06Z"
            },
            "routine_running_max_market_value": 29,
            "source_temp_c": 29.6,
            "source_running_max_c": 29.6,
            "market_capture": {"books": []},
        },
        path=tmp_path / "checkpoint.jsonl",
        line_number=1,
    )

    assert base is not None
    assert base["decision_ts_utc"].isoformat() == "2026-08-12T02:00:06+00:00"
    assert base["forecast_available_at_utc"] == base["decision_ts_utc"]


def test_busan_checkpoint_join_blocks_backdated_runtime_decision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decision_bundles.jsonl"
    path.write_text(
        json.dumps(_bundle(candidate_id="clock", bracket="29", selected=False))
        + "\n",
        encoding="utf-8",
    )
    candidates, _identity = load_busan_decision_bundles(
        path,
        start_date="2026-08-12",
        end_date="2026-08-12",
        expected_artifact_sha256="artifact-sha",
    )
    checkpoints = pd.DataFrame(
        [
            {
                "source_event_key": "event-clock",
                "source_observation_ts_utc": "2026-08-12T02:00:00Z",
                "source_first_seen_ts_utc": "2026-08-12T02:00:01Z",
                "source_available_at_utc": "2026-08-12T02:00:01.5Z",
                "feature_book_available_at_utc": "2026-08-12T02:00:03Z",
                "forecast_available_at_utc": "2026-08-12T01:00:00Z",
                "collector_emitted_at_utc": "2026-08-12T02:00:04Z",
                "decision_ts_utc": "2026-08-12T02:00:03Z",
                "raw_ref": "checkpoint.jsonl#1",
                "book_status": "scorable",
                "source_to_book_lag_seconds": 1.5,
                "feature_book_snapshot_id": "raw-feature-book",
                "condition_id": "condition-29",
                "no_token_id": "token-29",
            }
        ]
    )

    joined = attach_busan_checkpoint_clocks(candidates, checkpoints)

    assert bool(joined.iloc[0]["checkpoint_lineage_match"])
    assert bool(joined.iloc[0]["runtime_decision_backdated"])
    assert not bool(joined.iloc[0]["runtime_four_clock_valid"])
    assert bool(joined.iloc[0]["evaluation_four_clock_valid"])


def test_binary_replay_requires_complete_execution_evidence() -> None:
    frame = pd.DataFrame(
        [
            {
                "candidate_id": "valid",
                "target_date": "2026-08-12",
                "bracket": "29",
                "decision_ts_utc": "2026-08-12T02:00:00Z",
                "is_raw_state_entry": True,
                "execution_evidence_status": "executable",
                "model_p_no": 0.8,
                "executable_cost": 0.6,
                "label_no": 1,
            },
            {
                "candidate_id": "invalid",
                "target_date": "2026-08-13",
                "bracket": "30",
                "decision_ts_utc": "2026-08-13T02:00:00Z",
                "is_raw_state_entry": True,
                "execution_evidence_status": "market_evidence_not_available",
                "model_p_no": 0.9,
                "executable_cost": 0.5,
                "label_no": 1,
            },
        ]
    )

    trades = _replay_binary(frame, probability_column="model_p_no")

    assert trades["candidate_id"].tolist() == ["valid"]


def test_execution_evidence_requires_no_side_fresh_book_and_snapshots() -> None:
    base = {
        "same_row_score_status": "scorable",
        "side": "NO",
        "market_evidence_status": "available",
        "raw_book_status": "scorable",
        "raw_source_to_book_lag_seconds": 2.0,
        "condition_id": "condition-29",
        "token_id": "token-29",
        "feature_book_snapshot_id": "feature-book",
        "execution_book_snapshot_id": "execution-book",
        "executable_cost": 0.65,
    }
    frame = pd.DataFrame(
        [base, {**base, "side": "YES"}, {**base, "feature_book_snapshot_id": ""}]
    )

    result = _attach_execution_evidence_status(frame, fresh_book_seconds=10.0)

    assert result["execution_evidence_status"].tolist() == [
        "executable",
        "side_not_no",
        "feature_book_snapshot_id_missing",
    ]


def test_forward_run_directory_is_append_only(tmp_path: Path) -> None:
    run_dir = tmp_path / "run=v1"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="not empty"):
        _prepare_output_dir(run_dir)


def test_json_safe_normalizes_nested_non_finite_values() -> None:
    value = _json_safe({"score": math.nan, "ci": [0.1, math.inf]})

    assert value == {"score": None, "ci": [0.1, None]}
    assert json.dumps(value, allow_nan=False)


def test_tmax_winner_loader_excludes_daily_minimum_market(tmp_path: Path) -> None:
    db = tmp_path / "weather.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE settlement_outcomes (
          city TEXT, target_date TEXT, bracket TEXT, source_system TEXT,
          source_payload_hash TEXT, producer_build_id TEXT, created_at_utc TEXT,
          settlement_status TEXT, final_price REAL, question TEXT
        );
        CREATE TABLE fact_signal_candidates (fact_built_at_utc TEXT);
        INSERT INTO fact_signal_candidates VALUES ('2026-08-13T00:00:00Z');
        INSERT INTO settlement_outcomes VALUES
          ('Seoul', '2026-08-12', '32', 'pm_history', 'max-hash', 'build',
           '2026-08-13T00:00:00Z', 'settled', 1.0,
           'Will the highest temperature in Seoul be 32C?'),
          ('Seoul', '2026-08-12', '22', 'polymarket_api', 'min-hash', 'build',
           '2026-08-13T00:00:00Z', 'settled', 1.0,
           'Will the lowest temperature in Seoul be 22C?');
        """
    )
    conn.commit()
    conn.close()

    winners, identity = load_settlement_winners(
        db, city="Seoul", start_date="2026-08-12", end_date="2026-08-12"
    )

    assert winners == {"2026-08-12": "32"}
    assert identity["market_extreme_kind"] == "daily_max"
    assert identity["excluded_non_tmax_winners"] == 1


def test_forward_config_rejects_city_pooling_aliases() -> None:
    config = KoreaAirportForwardConfig(
        cities=("Seoul", "Busan"),
        start_date="2026-08-12",
        end_date="2026-09-04",
    )
    assert config.cities == ("Seoul", "Busan")
