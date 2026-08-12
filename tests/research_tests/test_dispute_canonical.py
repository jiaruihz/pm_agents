from __future__ import annotations

import json
import sqlite3
import pytest

from src.platform.market_data.capture_contract import materialize_orderbook_capture
from src.platform.market_data.market_group import binary_market_group_snapshot
from src.strategies.rule_lawyer.canonical import materialize


def _write_jsonl(path, rows) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def test_incremental_materializer_preserves_source_lineage(tmp_path) -> None:
    source = tmp_path / "forward"
    source.mkdir()
    book = materialize_orderbook_capture(
        token_id="yes-token",
        raw_book={"timestamp": "1786492800000", "bids": [], "asks": []},
        request_started_at_utc="2026-08-12T00:00:00.000Z",
        response_received_at_utc="2026-08-12T00:00:00.100Z",
        parsed_at_utc="2026-08-12T00:00:00.101Z",
        request_batch_capture_id="batch-1",
    )
    no_book = materialize_orderbook_capture(
        token_id="no-token",
        raw_book={"timestamp": "1786492800000", "bids": [], "asks": []},
        request_started_at_utc="2026-08-12T00:00:00.000Z",
        response_received_at_utc="2026-08-12T00:00:00.100Z",
        parsed_at_utc="2026-08-12T00:00:00.101Z",
        request_batch_capture_id="batch-1",
    )
    group = binary_market_group_snapshot(
        group_id="condition:0xabc",
        market_id="100",
        condition_id="0xabc",
        outcomes=("Yes", "No"),
        token_ids=("yes-token", "no-token"),
        captured_at_utc="2026-08-12T00:00:00Z",
        available_at_utc="2026-08-12T00:00:00.100Z",
        book_snapshot_ids={
            "yes-token": book["book_capture_id"],
            "no-token": no_book["book_capture_id"],
        },
        capture_batch_id="batch-1",
    ).to_dict()
    _write_jsonl(
        source / "events.jsonl",
        [
            {
                "case_id": "case-1",
                "first_observed_at_utc": "2026-08-12T00:00:00Z",
                "raw_request": {"id": "request-1", "disputeTimestamp": "1"},
            }
        ],
    )
    _write_jsonl(
        source / "snapshots.jsonl",
        [
            {
                "case_id": "case-1",
                "market_id": "100",
                "condition_id": "0xabc",
                "title": "Example",
                "captured_at_utc": "2026-08-12T00:00:00Z",
                "proposal": {"request_class": "unsettled"},
                "contract_corpus": {
                    "contract_corpus_sha256": "contract-1",
                    "fragments": [
                        {
                            "fragment_id": "fragment-1",
                            "origin": "ancillary",
                            "legal_role": "binding_request",
                            "content_sha256": "content-1",
                        }
                    ],
                },
                "book_captures": {"yes-token": book, "no-token": no_book},
                "market_group_snapshot": group,
                "rule_verdict": {"status": "unverified"},
            }
        ],
    )
    _write_jsonl(
        source / "signals.jsonl",
        [
            {
                "candidate_id": "candidate-1",
                "case_id": "case-1",
                "policy_id": "policy-1",
                "blockers": ["no_edge"],
                "snapshot_ts_utc": "2026-08-12T00:00:01Z",
            }
        ],
    )
    _write_jsonl(
        source / "trade_intents.jsonl",
        [
            {
                "intent_id": "intent-1",
                "candidate_id": "candidate-1",
                "mode": "zero_notional",
                "requested_size": 0,
                "token_id": "yes-token",
                "venue_side": "BUY",
            }
        ],
    )
    _write_jsonl(
        source / "paper_plans.jsonl",
        [{
            "plan_id": "plan-1", "intent_id": "intent-1", "candidate_id": "candidate-1",
            "token_id": "yes-token", "side": "BUY", "hypothetical_shares": 25,
            "limit_price": 0.4, "book_snapshot_id": book["book_capture_id"],
            "created_at_utc": "2026-08-12T00:00:01Z", "mode": "zero_notional",
        }],
    )
    _write_jsonl(
        source / "paper_orders.jsonl",
        [{
            "order_id": "order-1", "plan_id": "plan-1", "intent_id": "intent-1",
            "token_id": "yes-token", "status": "accepted", "actual_notional": 0,
            "accepted_at_utc": "2026-08-12T00:00:01Z",
        }],
    )
    _write_jsonl(
        source / "paper_fills.jsonl",
        [{
            "fill_id": "fill-1", "order_id": "order-1", "plan_id": "plan-1",
            "intent_id": "intent-1", "candidate_id": "candidate-1", "token_id": "yes-token",
            "hypothetical_shares": 25, "fill_price": 0.4,
            "hypothetical_fee_per_share": 0, "actual_shares": 0, "actual_cost": 0,
            "observed_at_utc": "2026-08-12T00:00:01Z", "book_snapshot_id": book["book_capture_id"],
        }],
    )
    db = tmp_path / "dispute.db"
    first = materialize(source, db)
    assert first["table_counts"]["dispute_cases"] == 1
    assert first["table_counts"]["market_book_snapshots"] == 2
    assert first["table_counts"]["market_group_expressions"] == 2
    assert first["table_counts"]["fact_signal_candidates"] == 1
    assert first["table_counts"]["trade_intents"] == 1
    assert first["table_counts"]["paper_fills"] == 1

    second = materialize(source, db)
    assert sum(item["rows_ingested"] for item in second["sources"].values()) == 0
    with sqlite3.connect(db) as conn:
        source_path, source_offset = conn.execute(
            "SELECT source_path,source_byte_offset FROM fact_signal_candidates"
        ).fetchone()
    assert source_path.endswith("signals.jsonl")
    assert source_offset == 0

    with (source / "signals.jsonl").open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "candidate_id": "candidate-2",
                    "case_id": "case-1",
                    "policy_id": "policy-1",
                    "blockers": [],
                    "snapshot_ts_utc": "2026-08-12T00:00:02Z",
                },
                sort_keys=True,
            )
            + "\n"
        )
    appended = materialize(source, db)
    assert appended["sources"]["signals.jsonl"]["rows_ingested"] == 1
    assert appended["table_counts"]["fact_signal_candidates"] == 2


def test_initially_empty_source_establishes_prefix_on_first_append(tmp_path) -> None:
    source = tmp_path / "forward"
    source.mkdir()
    signals = source / "signals.jsonl"
    signals.write_text("")
    db = tmp_path / "dispute.db"
    materialize(source, db)
    row = {"candidate_id": "candidate-1", "blockers": []}
    signals.write_text(json.dumps(row) + "\n")
    materialize(source, db)
    with sqlite3.connect(db) as conn:
        prefix_bytes = conn.execute(
            "SELECT prefix_bytes FROM ingest_watermarks WHERE source_path=?",
            (str(signals.resolve()),),
        ).fetchone()[0]
    assert prefix_bytes > 0
    signals.write_text(json.dumps({**row, "candidate_id": "changed-001"}) + "\n")
    with pytest.raises(RuntimeError, match="prefix changed"):
        materialize(source, db)


def test_materializer_migrates_legacy_watermark_schema(tmp_path) -> None:
    source = tmp_path / "forward"
    source.mkdir()
    signals = source / "signals.jsonl"
    _write_jsonl(signals, [{"candidate_id": "candidate-1", "blockers": []}])
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE ingest_watermarks (
                 source_path TEXT PRIMARY KEY,
                 byte_offset INTEGER NOT NULL,
                 source_size INTEGER NOT NULL,
                 head_sha256 TEXT NOT NULL,
                 rows_ingested INTEGER NOT NULL,
                 updated_at_utc TEXT NOT NULL DEFAULT
                   (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
               )"""
        )
    result = materialize(source, db)
    assert result["table_counts"]["fact_signal_candidates"] == 1
    with sqlite3.connect(db) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(ingest_watermarks)")
        }
        versions = {
            row[0] for row in conn.execute("SELECT version FROM schema_version")
        }
    assert "prefix_bytes" in columns
    assert 2 in versions


def test_clarification_quote_and_signal_share_canonical_book_lineage(tmp_path) -> None:
    source = tmp_path / "forward"
    source.mkdir()
    capture = materialize_orderbook_capture(
        token_id="yes-token",
        raw_book={"timestamp": "1786492800000", "asks": [{"price": "0.3", "size": "25"}]},
        request_started_at_utc="2026-08-12T00:00:00.000Z",
        response_received_at_utc="2026-08-12T00:00:00.100Z",
        parsed_at_utc="2026-08-12T00:00:00.101Z",
        request_batch_capture_id="clarification-batch-1",
    )
    _write_jsonl(
        source / "clarification_quote_snapshots.jsonl",
        [{
            "schema_version": "clarification_quote_snapshot_v1",
            "case_id": "case-1",
            "token_id": "yes-token",
            "book_capture": capture,
            "raw_book": {"asks": [{"price": "0.3", "size": "25"}]},
        }],
    )
    _write_jsonl(
        source / "clarification_signals.jsonl",
        [{
            "candidate_id": "clarification-candidate-1",
            "case_id": "case-1",
            "policy_id": "official_clarification_court_v1",
            "selected_token": "yes-token",
            "selected_outcome": "Yes",
            "ask_vwap": 0.3,
            "execution_book_snapshot_id": capture["book_capture_id"],
            "eligible_shadow": True,
            "zero_notional": True,
            "blockers": [],
        }],
    )
    db = tmp_path / "dispute.db"
    result = materialize(source, db)
    assert result["table_counts"]["market_book_snapshots"] == 1
    assert result["table_counts"]["fact_signal_candidates"] == 1
    with sqlite3.connect(db) as conn:
        source_path, book_id = conn.execute(
            "SELECT source_path,execution_book_snapshot_id FROM fact_signal_candidates"
        ).fetchone()
    assert source_path.endswith("clarification_signals.jsonl")
    assert book_id == capture["book_capture_id"]
