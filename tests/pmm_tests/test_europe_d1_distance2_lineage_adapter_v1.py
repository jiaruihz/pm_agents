from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.ops.materialize_weather_city_runtime_canonical_v1 import (
    main as canonical_materialize_main,
)
from weather_city_runtime import DecisionBundle, TemporaryCanonicalBridge, TradeIntent


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ops/weather_europe_d1_distance2_lineage_adapter_v1.py"
SPEC = importlib.util.spec_from_file_location("europe_d1_lineage_adapter", SCRIPT)
assert SPEC and SPEC.loader
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def _book_row(
    *,
    batch: str,
    token: str,
    condition: str,
    bracket: str,
    available: str,
    ask: float,
) -> dict:
    return {
        "schema_version": "weather_orderbook_capture_v3",
        "outcome": "no",
        "status": "ok",
        "city": "Paris",
        "event_date": "2026-09-02",
        "bracket": bracket,
        "condition_id": condition,
        "market_id": f"market-{bracket}",
        "token_id": token,
        "request_batch_capture_id": batch,
        "book_capture_id": f"capture-{token}",
        "raw_payload_hash": f"hash-{token}",
        "clock_lineage_status": "collector_exact_response_clock",
        "available_at_utc": available,
        "fetched_at_utc": available,
        "summary": {
            "best_bid": ask - 0.01,
            "best_ask": ask,
            "ask_size": 20.0,
        },
    }


def _inputs(tmp_path: Path, *, paired_clock: bool = True) -> tuple[Path, Path, str]:
    sha = "a" * 40
    config = tmp_path / "policy.json"
    config.write_text(
        json.dumps(
            {
                "frozen_forward": {
                    "formal_forward_start_utc": "2026-08-30T16:42:11Z"
                }
            }
        ),
        encoding="utf-8",
    )
    book_path = tmp_path / "books.jsonl.gz"
    first_available = "2026-09-01T10:00:00.000Z"
    second_available = (
        first_available if paired_clock else "2026-09-01T10:00:01.000Z"
    )
    books = [
        _book_row(
            batch="batch-1",
            token="token-low",
            condition="condition-low",
            bracket="20",
            available=first_available,
            ask=0.98,
        ),
        _book_row(
            batch="batch-1" if paired_clock else "batch-2",
            token="token-high",
            condition="condition-high",
            bracket="26",
            available=second_available,
            ask=0.99,
        ),
    ]
    with gzip.open(book_path, "wt", encoding="utf-8") as handle:
        for row in books:
            handle.write(json.dumps(row) + "\n")
    legs = []
    for name, book in zip(
        ("low_distance2_no", "high_distance2_no"), books, strict=True
    ):
        ask = float(book["summary"]["best_ask"])
        fee = 0.05 * ask * (1 - ask)
        legs.append(
            {
                "leg": name,
                "allocation_weight": 0.5,
                "distance_from_nearest_endpoint": 2,
                "bracket": book["bracket"],
                "condition_id": book["condition_id"],
                "token_id": book["token_id"],
                "book_fetched_at_utc": book["fetched_at_utc"],
                "no_best_bid": book["summary"]["best_bid"],
                "no_best_ask": ask,
                "no_ask_size": 20.0,
                "market_p_exact": 1 - (ask + ask - 0.01) / 2,
                "cost_per_share": ask + fee,
            }
        )
    basket = {
        "strategy_instance": subject.SOURCE_STRATEGY_INSTANCE,
        "execution_mode": "zero_notional_shadow",
        "orders_submitted": 0,
        "actual_notional_usd": 0.0,
        "repo_sha": sha,
        "city": "Paris",
        "target_date": "2026-09-02",
        "city_date_key": "Paris|2026-09-02",
        "book_snapshot_path": str(book_path),
        "book_available_at_utc": first_available if paired_clock else None,
        "decision_asof_utc": first_available,
        "decision_status": (
            "would_shadow_entry" if paired_clock else "paired_book_clock_unavailable"
        ),
        "request_batch_capture_id": "batch-1" if paired_clock else None,
        "paired_book_clock_complete": paired_clock,
        "paired_book_executable": paired_clock,
        "market_tail_eligible": paired_clock,
        "joint_market_probability": 0.0175 if paired_clock else None,
        "joint_market_probability_max": 0.1,
        "legs": legs,
    }
    source = tmp_path / "baskets.jsonl"
    source.write_text(json.dumps(basket) + "\n", encoding="utf-8")
    return source, config, sha


def _args(tmp_path: Path, source: Path, config: Path, sha: str) -> argparse.Namespace:
    return argparse.Namespace(
        source_baskets=source,
        config=config,
        output_dir=tmp_path / "output",
        source_repo_sha=[sha],
    )


def test_adapter_emits_exact_contracts_and_is_idempotent(tmp_path: Path) -> None:
    source, config, sha = _inputs(tmp_path)
    args = _args(tmp_path, source, config, sha)

    first = subject.run_once(args)
    second = subject.run_once(args)

    assert first["status"] == "ok"
    assert first["accepted_frozen_baskets"] == 1
    assert first["verified_book_legs"] == 2
    assert first["decision_bundles"] == 2
    assert first["trade_intents"] == 2
    assert first["orders_submitted"] == 0
    assert first["actual_notional_usd"] == 0.0
    assert second["new_decision_bundles"] == 0
    assert second["new_trade_intents"] == 0

    bundles = subject._read_jsonl(args.output_dir / "decision_bundles.jsonl")
    intents = subject._read_jsonl(args.output_dir / "trade_intents.jsonl")
    parsed = [DecisionBundle.from_dict(row) for row in bundles]
    parsed_intents = [TradeIntent.from_dict(row) for row in intents]
    assert all(bundle.signal_candidate.selected for bundle in parsed)
    assert {bundle.signal_candidate.side for bundle in parsed} == {"NO"}
    assert all(intent.requested_size == 0 for intent in parsed_intents)
    assert all(intent.mode == "zero_notional" for intent in parsed_intents)


def test_adapter_journals_materialize_candidates_and_intents_without_execution(
    tmp_path: Path,
) -> None:
    source, config, sha = _inputs(tmp_path)
    args = _args(tmp_path, source, config, sha)
    subject.run_once(args)
    db = tmp_path / "canonical.db"
    TemporaryCanonicalBridge(db).append([])
    report = tmp_path / "materialization.json"
    materialize_args = [
        "--bundles", str(args.output_dir / "decision_bundles.jsonl"),
        "--state", str(tmp_path / "candidate-state.json"),
        "--trade-intents", str(args.output_dir / "trade_intents.jsonl"),
        "--trade-intent-state", str(tmp_path / "intent-state.json"),
        "--db", str(db),
        "--expected-db", str(db),
        "--apply",
        "--report", str(report),
    ]

    assert canonical_materialize_main(materialize_args) == 0
    first = json.loads(report.read_text())
    assert first["canonical_reconciliation"]["inserted_candidates"] == 2
    assert first["trade_intent_reconciliation"]["inserted_trade_intents"] == 2
    assert first["input_trade_intent_rows"] == 2
    assert canonical_materialize_main(materialize_args) == 0
    second = json.loads(report.read_text())
    assert second["input_bundle_rows"] == 0
    assert second["input_trade_intent_rows"] == 0

    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM weather_information_events "
            "WHERE event_kind = 'market_book_snapshot'"
        ).fetchone()[0] == 2
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM fact_signal_candidates AS candidate
            JOIN weather_information_events AS event
              ON event.information_event_id = candidate.trigger_event_id
            JOIN weather_state_checkpoints AS checkpoint
              ON checkpoint.state_checkpoint_id = candidate.state_checkpoint_id
            JOIN weather_model_outputs AS output
              ON output.checkpoint_id = candidate.state_checkpoint_id
             AND output.trigger_event_id = candidate.trigger_event_id
             AND output.target_id = candidate.target_id
            """
        ).fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM weather_trade_intents").fetchone()[0] == 2
        assert {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("plans", "orders", "fills")
        } == {"plans": 0, "orders": 0, "fills": 0}
    finally:
        conn.close()


def test_adapter_preserves_mixed_clock_pair_as_blocked_lineage(tmp_path: Path) -> None:
    source, config, sha = _inputs(tmp_path, paired_clock=False)
    args = _args(tmp_path, source, config, sha)

    result = subject.run_once(args)

    assert result["verified_book_legs"] == 2
    assert result["decision_bundles"] == 2
    assert result["selected_signal_candidates"] == 0
    assert result["trade_intents"] == 0
    bundles = subject._read_jsonl(args.output_dir / "decision_bundles.jsonl")
    parsed = [DecisionBundle.from_dict(row) for row in bundles]
    assert all(bundle.signal_candidate.candidate_status == "blocked" for bundle in parsed)
    assert all(
        bundle.signal_candidate.blocker_reason == "paired_book_clock_unavailable"
        for bundle in parsed
    )


def test_adapter_excludes_non_frozen_repo_without_mutating_source(tmp_path: Path) -> None:
    source, config, sha = _inputs(tmp_path)
    args = _args(tmp_path, source, config, "b" * 40)
    before = source.read_bytes()

    result = subject.run_once(args)

    assert result["accepted_frozen_baskets"] == 0
    assert result["excluded_non_frozen_repo_rows"] == 1
    assert result["decision_bundles"] == 0
    assert source.read_bytes() == before


def test_adapter_rejects_leg_fetched_clock_that_differs_from_archived_book(
    tmp_path: Path,
) -> None:
    source, config, sha = _inputs(tmp_path)
    basket = json.loads(source.read_text(encoding="utf-8"))
    basket["legs"][0]["book_fetched_at_utc"] = "2026-09-01T10:00:02.000Z"
    source.write_text(json.dumps(basket) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="archived orderbook evidence differs"):
        subject.run_once(_args(tmp_path, source, config, sha))
