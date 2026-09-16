from __future__ import annotations

import csv
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from weather_dashboard.ingest.settlement_outcomes import (
    ensure_settlement_outcomes_schema,
    insert_settlement_outcome,
    make_settlement_outcome_id,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts/analysis/market_structure_edge/"
    "research_europe_d1_distance2_forward_v8.py"
)
SPEC = importlib.util.spec_from_file_location("europe_d1_distance2_forward_v8", SCRIPT)
assert SPEC and SPEC.loader
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def _manifest(*, low_bid: float | None = 0.02) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for position in range(7):
        yes_bid = 0.01
        yes_ask = 0.02
        if position == 2:
            yes_bid, yes_ask = low_bid, 0.03
        elif position == 4:
            yes_bid, yes_ask = 0.04, 0.05
        result.append(
            {
                "position": position,
                "low": None if position == 0 else float(20 + position),
                "high": None if position == 6 else float(20 + position),
                "bottom": position == 0,
                "top": position == 6,
                "label": str(20 + position),
                "condition_id": f"condition-{position}",
                "token_id": f"token-{position}",
                "yes_best_bid": yes_bid,
                "yes_best_ask": yes_ask,
                "yes_best_bid_size": 10.0 if yes_bid is not None else None,
                "yes_best_ask_size": 10.0,
            }
        )
    return result


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "policy.json"
    config.write_text(
        """{
          "strategy_instance": "test_europe_d1",
          "status": "frozen_zero_notional_shadow",
          "target_lead_days": 1,
          "local_entry_hour_start": 12,
          "local_entry_hour_end": 24,
          "distance_from_nearest_endpoint": 2,
          "allocation": {"low_distance2_no": 0.5, "high_distance2_no": 0.5},
          "cities": {
            "Paris": {"timezone": "Europe/Paris", "market_unit": "C"},
            "Madrid": {"timezone": "Europe/Madrid", "market_unit": "C"}
          }
        }\n""",
        encoding="utf-8",
    )
    checkpoints = tmp_path / "checkpoints.csv"
    fields = (
        "checkpoint_ts_utc",
        "city",
        "event_time_pit_scorable",
        "feature_book_snapshot_id",
        "rung_manifest",
        "source_contract",
        "source_path",
        "target_date",
    )
    rows = [
        {
            "checkpoint_ts_utc": "2026-08-09T10:05:00Z",
            "city": "Paris",
            "event_time_pit_scorable": True,
            "feature_book_snapshot_id": "first",
            "rung_manifest": repr(_manifest()),
            "source_contract": "canonical_market_books_v1",
            "source_path": "/first.json",
            "target_date": "2026-08-10",
        },
        {
            "checkpoint_ts_utc": "2026-08-09T10:10:00Z",
            "city": "Paris",
            "event_time_pit_scorable": True,
            "feature_book_snapshot_id": "later",
            "rung_manifest": repr(_manifest(low_bid=0.10)),
            "source_contract": "canonical_market_books_v1",
            "source_path": "/later.json",
            "target_date": "2026-08-10",
        },
        {
            "checkpoint_ts_utc": "2026-08-20T10:05:00Z",
            "city": "Madrid",
            "event_time_pit_scorable": True,
            "feature_book_snapshot_id": "blocked-first",
            "rung_manifest": repr(_manifest(low_bid=None)),
            "source_contract": "canonical_market_books_v1",
            "source_path": "/blocked.json",
            "target_date": "2026-08-21",
        },
        {
            "checkpoint_ts_utc": "2026-08-20T10:10:00Z",
            "city": "Madrid",
            "event_time_pit_scorable": True,
            "feature_book_snapshot_id": "executable-later",
            "rung_manifest": repr(_manifest()),
            "source_contract": "canonical_market_books_v1",
            "source_path": "/executable.json",
            "target_date": "2026-08-21",
        },
    ]
    with checkpoints.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return checkpoints, config


def test_freeze_locks_first_valid_ladder_even_when_quotes_are_unexecutable(tmp_path: Path) -> None:
    checkpoints, config = _write_inputs(tmp_path)
    candidates, summary = subject.build_frozen_candidates(
        checkpoints_path=checkpoints,
        config_path=config,
        start_date="2026-08-10",
        end_date="2026-08-21",
        validation_end="2026-08-19",
    )

    assert [(row["city"], row["feature_book_snapshot_id"]) for row in candidates] == [
        ("Paris", "first"),
        ("Madrid", "blocked-first"),
    ]
    assert candidates[0]["split"] == "recovery_validation"
    assert candidates[0]["paired_book_executable"] is True
    assert candidates[0]["legs"][0]["no_best_ask"] == 0.98
    assert candidates[1]["split"] == "locked_forward"
    assert candidates[1]["paired_book_executable"] is False
    assert summary["signal_evidence_funnel"]["locked_city_dates"] == 2
    assert summary["signal_evidence_funnel"]["paired_executable_city_dates"] == 1


def test_score_uses_official_fee_and_preserves_unexecutable_blocker(tmp_path: Path) -> None:
    checkpoints, config = _write_inputs(tmp_path)
    candidates, _ = subject.build_frozen_candidates(
        checkpoints_path=checkpoints,
        config_path=config,
        start_date="2026-08-10",
        end_date="2026-08-21",
        validation_end="2026-08-19",
    )
    settlements = {
        ("Paris", "2026-08-10", "22"): 0.0,
        ("Paris", "2026-08-10", "24"): 0.0,
    }

    scored, summary = subject.score_frozen_candidates(
        candidates=candidates,
        settlements=settlements,
        draws=100,
        seed=7,
    )

    assert len(scored) == 1
    expected_cost = 0.5 * (0.98 + 0.05 * 0.98 * 0.02) + 0.5 * (
        0.96 + 0.05 * 0.96 * 0.04
    )
    assert abs(scored[0]["cost"] - expected_cost) < 1e-12
    assert scored[0]["payout"] == 1.0
    assert scored[0]["pnl"] > 0.0
    assert scored[0]["joint_market_probability"] == pytest.approx(
        0.5 * 0.025 + 0.5 * 0.045
    )
    assert summary["blocker_counts"] == {"paired_book_unexecutable": 1}
    assert summary["score_summaries"]["recovery_validation"]["roi_ci95"][0] > 0.0


def test_settlements_use_valid_manual_correction_over_non_settled_pm_history(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "weather.db"
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    ensure_settlement_outcomes_schema(connection)
    common = {
        "city": "Madrid",
        "target_date": "2026-08-27",
        "bracket": "23",
        "unit": "C",
        "condition_id": "condition-23",
        "market_id": "market-23",
        "token_id": "token-23",
        "question": "Madrid 23",
        "first_seen_at_utc": None,
        "pit_lineage_class": "late_backfill_first_seen_unknown",
    }
    insert_settlement_outcome(
        connection,
        {
            **common,
            "settlement_outcome_id": make_settlement_outcome_id(
                "pm_history", "Madrid", "2026-08-27", "23"
            ),
            "source_system": "pm_history",
            "source_path": "/old.json",
            "raw_final_price": 0.985,
            "final_price": 0.985,
            "settlement_status": "missing_bracket",
            "payload": "{}",
            "source_payload_hash": "old",
            "source_file_mtime_utc": "2026-08-27T00:00:00+00:00",
            "available_at_utc": "2026-08-27T00:00:00+00:00",
            "producer_build_id": "old",
        },
    )
    insert_settlement_outcome(
        connection,
        {
            **common,
            "settlement_outcome_id": make_settlement_outcome_id(
                "manual_backfill", "Madrid", "2026-08-27", "23"
            ),
            "source_system": "manual_backfill",
            "source_path": "/closed.json",
            "raw_final_price": 1.0,
            "final_price": 1.0,
            "settlement_status": "settled",
            "payload": json.dumps(
                {
                    "correction_reason": (
                        "closed_pm_history_replaces_non_settled_pm_history"
                    )
                }
            ),
            "source_payload_hash": "new",
            "source_file_mtime_utc": "2026-08-28T00:00:00+00:00",
            "available_at_utc": "2026-08-28T00:00:00+00:00",
            "producer_build_id": "correction-v1",
        },
    )
    connection.commit()
    connection.close()

    settlements, identity = subject._settlements(
        db_path, start_date="2026-08-27", end_date="2026-08-27"
    )

    assert settlements[("Madrid", "2026-08-27", "23")] == 1.0
    assert identity["manual_correction_count"] == 1
    assert identity["chosen_source_counts"] == {"manual_backfill": 1}


def test_prior_shadow_loader_rejects_overlap_and_records_hash(tmp_path: Path) -> None:
    path = tmp_path / "baskets.jsonl"
    base = {
        "city": "Paris",
        "target_date": "2026-08-09",
        "city_date_key": "Paris|2026-08-09",
        "orders_submitted": 0,
        "actual_notional_usd": 0.0,
        "weather_features_used_for_eligibility": False,
        "paired_book_executable": True,
        "legs": [
            {"distance_from_nearest_endpoint": 2},
            {"distance_from_nearest_endpoint": 2},
        ],
    }
    path.write_text(json.dumps(base) + "\n", encoding="utf-8")

    rows, manifest = subject._prior_shadow_candidates(
        path, frozen_start_date="2026-08-10"
    )
    assert rows[0]["split"] == "collected_forward"
    assert manifest["rows"] == 1
    assert len(manifest["sha256"]) == 64

    base["target_date"] = "2026-08-10"
    path.write_text(json.dumps(base) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="overlaps frozen evaluation window"):
        subject._prior_shadow_candidates(path, frozen_start_date="2026-08-10")


def test_historical_blind_loader_locks_hash_and_same_snapshot_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "selected_policy_rows.csv"
    fields = (
        "snapshot_key",
        "source_path",
        "city",
        "target_date",
        "decision_ts_utc",
        "distance_from_nearest_endpoint",
        "cost",
        "payout",
        "pnl",
        "policy",
    )
    city_days = [
        (city, f"2026-07-{day:02d}")
        for day in range(16, 24)
        for city in sorted(subject.EUROPE_CITIES)
    ][:42]
    rows: list[dict[str, object]] = []
    for index in range(84):
        city, target_date = city_days[index // 2]
        snapshot_number = index % 2
        key = f"snapshot-{index // 2}-{snapshot_number}"
        for policy in ("mechanical_half_distance2", "market_only_selection"):
            rows.append(
                {
                    "snapshot_key": key,
                    "source_path": f"/snapshot-{index}.json",
                    "city": city,
                    "target_date": target_date,
                    "decision_ts_utc": (
                        "2026-07-15T10:00:00Z"
                        if index // 2 == 0 or snapshot_number == 0
                        else "2026-07-15T11:00:00Z"
                    ),
                    "distance_from_nearest_endpoint": 2,
                    "cost": 0.98,
                    "payout": 1.0,
                    "pnl": 0.02,
                    "policy": policy,
                }
            )
    mechanical_rows = [
        row for row in rows if row["policy"] == "mechanical_half_distance2"
    ]
    market_rows = [row for row in rows if row["policy"] == "market_only_selection"]
    rows = mechanical_rows + list(reversed(market_rows))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    monkeypatch.setattr(subject, "HISTORICAL_BLIND_V7_SHA256", subject.sha256_file(path))

    mechanical, market, manifest = subject._historical_blind_v7_rows(path)

    assert len(mechanical) == len(market) == 42
    assert all(row["candidate_id"].endswith("-0") for row in mechanical)
    assert {row["candidate_id"] for row in market} == {
        row["candidate_id"] for row in mechanical
    }
    assert manifest["raw_mechanical_rows"] == 84
    assert manifest["duplicate_snapshot_rows_excluded"] == 42
    assert manifest["denominator_relation"] == "same_first_city_day_snapshot_keys"
    assert manifest["historical_blind_not_prospective"] is True
    assert {row["split"] for row in mechanical} == {"historical_blind"}


def _risk_row(
    candidate_id: str,
    *,
    split: str,
    target_date: str,
    city: str = "Paris",
    market_tail_probability: float,
    cost: float,
    payout: float,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "split": split,
        "city": city,
        "target_date": target_date,
        "checkpoint_ts_utc": f"{target_date}T10:00:00+00:00",
        "cost": cost,
        "payout": payout,
        "pnl": payout - cost,
        "joint_market_probability": market_tail_probability,
        "joint_tail_hit": int(payout < 1.0),
        "market_expected_payout": 1.0 - market_tail_probability,
        "legs": [],
    }


def test_market_tail_overlay_selects_on_development_only() -> None:
    rows = [
        _risk_row(
            "dev-safe-1",
            split="collected_forward",
            target_date="2026-08-01",
            market_tail_probability=0.05,
            cost=0.98,
            payout=1.0,
        ),
        _risk_row(
            "dev-safe-2",
            split="recovery_validation",
            target_date="2026-08-02",
            market_tail_probability=0.05,
            cost=0.98,
            payout=1.0,
        ),
        _risk_row(
            "dev-risky-loss",
            split="recovery_validation",
            target_date="2026-08-02",
            city="Munich",
            market_tail_probability=0.11,
            cost=0.90,
            payout=0.5,
        ),
        _risk_row(
            "forward-safe",
            split="locked_forward",
            target_date="2026-08-03",
            market_tail_probability=0.05,
            cost=0.98,
            payout=1.0,
        ),
        _risk_row(
            "forward-risky-loss",
            split="locked_forward",
            target_date="2026-08-03",
            city="Madrid",
            market_tail_probability=0.15,
            cost=0.90,
            payout=0.5,
        ),
    ]

    result = subject.evaluate_market_tail_risk_overlay(rows, draws=100, seed=7)

    assert result["selection"]["selected_maximum_tail_probability"] == pytest.approx(
        0.10
    )
    assert result["development"]["baseline"]["candidates"] == 3
    assert result["development"]["challenger"]["candidates"] == 2
    assert result["locked_forward"]["baseline"]["candidates"] == 2
    assert result["locked_forward"]["challenger"]["candidates"] == 1
    assert result["locked_forward"]["paired_challenger_minus_baseline"][
        "pnl_delta"
    ] == pytest.approx(0.4)

    rows[-1] = {
        **rows[-1],
        "payout": 1.0,
        "pnl": 0.1,
        "joint_tail_hit": 0,
    }
    changed_forward = subject.evaluate_market_tail_risk_overlay(
        rows, draws=100, seed=7
    )
    assert changed_forward["selection"][
        "selected_maximum_tail_probability"
    ] == pytest.approx(0.10)


def test_market_tail_overlay_rejects_duplicate_city_date_grain() -> None:
    rows = [
        _risk_row(
            "dev-a",
            split="collected_forward",
            target_date="2026-08-01",
            market_tail_probability=0.05,
            cost=0.98,
            payout=1.0,
        ),
        _risk_row(
            "dev-b",
            split="recovery_validation",
            target_date="2026-08-01",
            market_tail_probability=0.06,
            cost=0.97,
            payout=1.0,
        ),
        _risk_row(
            "forward",
            split="locked_forward",
            target_date="2026-08-02",
            market_tail_probability=0.05,
            cost=0.98,
            payout=1.0,
        ),
    ]

    with pytest.raises(ValueError, match="duplicate city-target_date grain"):
        subject.evaluate_market_tail_risk_overlay(rows, draws=10, seed=7)


def _exit_candidate() -> dict[str, object]:
    return {
        "candidate_id": "exit-a",
        "split": "collected_forward",
        "city": "Paris",
        "target_date": "2026-08-10",
        "entry_ts_utc": "2026-08-09T10:00:00+00:00",
        "cost": 0.90,
        "payout": 1.0,
        "pnl": 0.10,
        "joint_market_probability": 0.05,
        "legs": [
            {"token_id": "yes-low", "condition_id": "low", "bracket": "20", "no_best_bid": 0.90},
            {"token_id": "yes-high", "condition_id": "high", "bracket": "24", "no_best_bid": 0.90},
        ],
    }


def _direct_no(
    token_id: str, *, available: str, batch: str = "batch-1", bids: list[dict[str, float]] | None = None,
    outcome: str = "no", clock: str = "collector_exact_response_clock",
) -> dict[str, object]:
    return {
        "schema_version": "weather_orderbook_capture_v3",
        "source_lineage_status": "collector_exact_orderbook_response_v3",
        "status": "ok", "outcome": outcome, "token_id": token_id,
        "condition_id": "low" if token_id.endswith("low") else "high",
        "bracket": "20" if token_id.endswith("low") else "24",
        "event_time_pit_scorable": True, "clock_lineage_status": clock,
        "available_at_utc": available, "response_received_at_utc": available,
        "request_batch_capture_id": batch,
        "raw": {"bids": bids if bids is not None else [{"price": 0.90, "size": 1.0}]},
    }


def test_exit_replay_uses_first_same_batch_direct_no_full_ladder_and_keeps_blockers() -> None:
    candidate = _exit_candidate()
    rows = [
        (Path("first"), _direct_no("no-low", available="2026-08-09T10:15:00Z", bids=[{"price": .9, "size": .4}])),
        (Path("first"), _direct_no("no-high", available="2026-08-09T10:15:00Z")),
        # YES complement cannot complete the first batch.
        (Path("first"), _direct_no("no-low", available="2026-08-09T10:16:00Z", outcome="yes")),
        (Path("second"), _direct_no("no-low", available="2026-08-09T10:17:00Z", batch="batch-2", bids=[{"price": .91, "size": .5}])),
        (Path("second"), _direct_no("no-high", available="2026-08-09T10:17:00Z", batch="batch-2", bids=[{"price": .89, "size": .2}, {"price": .88, "size": .3}])),
        # Legacy clock is forbidden even though it is later in the window.
        (Path("legacy"), _direct_no("no-low", available="2026-08-09T10:18:00Z", batch="batch-3", clock="legacy_missing_response_clock")),
        (Path("legacy"), _direct_no("no-high", available="2026-08-09T10:18:00Z", batch="batch-3", clock="legacy_missing_response_clock")),
    ]
    capture, blockers = subject._first_full_exit_capture(candidate, horizon_minutes=15, book_rows=rows)

    assert capture["request_batch_capture_id"] == "batch-2"
    assert capture["exit_legs"][1]["net_proceeds"] < .445  # fee and lower second level applied
    assert blockers["insufficient_direct_no_bid_depth"] == 1
    assert blockers["not_direct_no_outcome"] == 1
    assert blockers["non_exact_collector_clock"] == 2


def test_exit_replay_accepts_legacy_entry_source_token_when_raw_side_is_explicit_no() -> None:
    candidate = _exit_candidate()
    candidate["legs"] = [
        {**candidate["legs"][0], "token_id": "no-low"},
        {**candidate["legs"][1], "token_id": "no-high"},
    ]
    rows = [
        (Path("same"), _direct_no("no-low", available="2026-08-09T10:15:00Z")),
        (Path("same"), _direct_no("no-high", available="2026-08-09T10:15:00Z")),
    ]

    capture, _ = subject._first_full_exit_capture(
        candidate, horizon_minutes=15, book_rows=rows
    )

    assert capture is not None
    assert all(
        leg["direct_no_token_matches_entry_source"]
        for leg in capture["exit_legs"]
    )


def test_selective_stop_uses_earliest_observable_trigger_otherwise_holds() -> None:
    base = {
        "candidate_id": "a",
        "split": "collected_forward",
        "city": "Paris",
        "target_date": "2026-08-10",
        "cost": 0.90,
        "pnl": 0.10,
        "joint_market_probability": 0.05,
        "entry_direct_no_bid_net_value": 0.90,
    }
    per_horizon = {}
    for horizon, value in ((15, 0.89), (30, 0.86), (60, 0.80)):
        per_horizon[str(horizon)] = {
            "rows": [
                {
                    **base,
                    "exit_pnl": value - 0.90,
                    "exit_capture": {
                        "available_at_utc": f"2026-08-09T10:{horizon:02d}:00+00:00",
                        "request_batch_capture_id": f"batch-{horizon}",
                        "net_exit_value": value,
                    },
                }
            ]
        }

    rows = subject._stop_policy_rows(
        {"a"},
        per_horizon=per_horizon,
        horizons=(15, 30, 60),
        drawdown_threshold=0.03,
    )
    hold_rows = subject._stop_policy_rows(
        {"a"},
        per_horizon=per_horizon,
        horizons=(15, 30, 60),
        drawdown_threshold=0.20,
    )

    assert rows[0]["trigger"]["horizon_minutes"] == 30
    assert rows[0]["stop_policy_pnl"] == pytest.approx(-0.04)
    assert len(rows[0]["observations_until_decision"]) == 2
    assert hold_rows[0]["trigger"] is None
    assert hold_rows[0]["stop_policy_pnl"] == pytest.approx(0.10)


def test_exit_replay_window_is_closed_and_filename_uses_shanghai_clock(tmp_path: Path) -> None:
    root = tmp_path / "batches"
    day = root / "2026-08-09"
    day.mkdir(parents=True)
    # 18:15:00 Asia/Shanghai is 10:15Z, the left closed boundary.
    on_boundary = day / "market_books_20260809_181500.jsonl.gz"
    after_boundary = day / "orderbook_snapshot_20260809_184701.jsonl.gz"
    for path in (on_boundary, after_boundary):
        with gzip.open(path, "wt") as handle:
            handle.write(json.dumps({"token_id": "x"}) + "\n")
    windows = [
        (
            datetime(2026, 8, 9, 10, 15, tzinfo=timezone.utc),
            datetime(2026, 8, 9, 10, 45, tzinfo=timezone.utc),
        )
    ]
    assert subject._book_file_paths(root, windows) == [on_boundary]

    candidate = _exit_candidate()
    early = _direct_no("no-low", available="2026-08-09T10:14:59Z")
    late = _direct_no("no-low", available="2026-08-09T10:45:00Z")
    high = _direct_no("no-high", available="2026-08-09T10:45:00Z")
    capture, _ = subject._first_full_exit_capture(
        candidate, horizon_minutes=15, book_rows=[(Path("x"), early), (Path("x"), late), (Path("x"), high)]
    )
    assert capture is not None
    assert capture["available_at_utc"].startswith("2026-08-09T10:45:00")


def test_exit_horizon_selection_uses_development_not_locked_labels(tmp_path: Path) -> None:
    root = tmp_path / "batches"
    candidates = [
        {**_exit_candidate(), "candidate_id": "dev", "target_date": "2026-08-10", "entry_ts_utc": "2026-08-09T10:00:00+00:00", "split": "collected_forward"},
        {**_exit_candidate(), "candidate_id": "locked", "target_date": "2026-08-11", "entry_ts_utc": "2026-08-10T10:00:00+00:00", "split": "locked_forward", "city": "Madrid"},
    ]
    for candidate in candidates:
        entry = datetime.fromisoformat(str(candidate["entry_ts_utc"]))
        for horizon, price in ((15, .80), (30, .96), (60, .90)):
            available = entry + subject.timedelta(minutes=horizon)
            local = available.astimezone(subject.ZoneInfo("Asia/Shanghai"))
            day = root / local.date().isoformat()
            day.mkdir(parents=True, exist_ok=True)
            filename = day / f"market_books_{local.strftime('%Y%m%d_%H%M%S')}.jsonl.gz"
            with gzip.open(filename, "wt") as handle:
                for leg in candidate["legs"]:
                    direct_no_token = str(leg["token_id"]).replace("yes-", "no-", 1)
                    handle.write(json.dumps(_direct_no(direct_no_token, available=available.isoformat().replace("+00:00", "Z"), batch=f"{candidate['candidate_id']}-{horizon}", bids=[{"price": price, "size": .5}])) + "\n")
    result = subject.evaluate_fixed_exit_replay(candidates, market_books_root=root, horizons=(15, 30, 60), draws=30, seed=4)
    assert result["common_coverage_horizon_selection"]["selected_horizon_minutes"] == 30

    changed_locked = [{**candidates[0]}, {**candidates[1], "payout": .2, "pnl": -.7}]
    changed = subject.evaluate_fixed_exit_replay(changed_locked, market_books_root=root, horizons=(15, 30, 60), draws=30, seed=4)
    assert changed["common_coverage_horizon_selection"]["selected_horizon_minutes"] == 30

    no_tail10 = [
        {**candidate, "joint_market_probability": 0.20}
        for candidate in candidates
    ]
    no_tail_result = subject.evaluate_fixed_exit_replay(
        no_tail10,
        market_books_root=root,
        horizons=(15, 30, 60),
        draws=30,
        seed=4,
    )
    assert (
        no_tail_result["common_coverage_horizon_selection"]
        ["market_tail_10pct_selective_stop_challenger"]["status"]
        == "BLOCKED_NO_TAIL10_COMMON_COVERAGE_IN_BOTH_SPLITS"
    )


def test_exit_research_record_refuses_to_overwrite_completed_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "research_record.json"
    subject.write_json(path, {"lifecycle_status": "complete"})

    with pytest.raises(ValueError, match="must be planned"):
        subject._complete_exit_research_record(
            path,
            payload={},
            args=object(),
            scored_sha256="unused",
            score_generated_at_utc="unused",
            config_sha256="unused",
        )
