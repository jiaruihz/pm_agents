from __future__ import annotations

import argparse
import json

from src.platform.market_data.capture_contract import materialize_orderbook_capture
from src.strategies.rule_lawyer.clarification_adjudicator import (
    PACKET_ONLY_EXECUTION_ISOLATION,
    PROMPT_VERSION,
)
from src.strategies.rule_lawyer.clarification_forward import (
    apply_update_cluster_cap,
    clarification_packets_from_snapshot,
    score_clarification_card,
)
from scripts.ops.polymarket_dispute_forward import clarification_worker_command
from scripts.ops.polymarket_clarification_forward import run_once


def snapshot_fixture() -> dict:
    return {
        "case_id": "request:1",
        "market_id": "123",
        "title": "Did X happen?",
        "captured_at_utc": "2026-08-12T12:01:00+00:00",
        "outcomes": ["Yes", "No"],
        "tokens": ["yes-token", "no-token"],
        "proposal": {"proposed_binary": 0, "request_class": "unsettled"},
        "opportunity_cluster_id": "market:123",
        "contract_corpus": {
            "contract_corpus_sha256": "corpus-1",
            "fragments": [
                {
                    "fragment_id": "rules",
                    "legal_role": "binding_resolution_rule",
                    "text": "Resolves Yes if X happened.",
                },
                {
                    "fragment_id": "update",
                    "legal_role": "onchain_clarification",
                    "text": "Official evidence confirms X happened.",
                    "effective_at_utc": "2026-08-12T12:00:00+00:00",
                },
            ],
        },
        "books": {
            "yes-token": {
                "timestamp": 1786536060000,
                "asks": [{"price": "0.50", "size": "30"}],
            }
        },
        "book_captures": {
            "yes-token": {"book_capture_id": "clarification-book-1"}
        },
        "clob_market": {"fd": {"r": 0.05}},
        "fee_rates": {},
    }


def card_fixture(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "determination": "Outcome0",
        "confidence": 0.98,
        "predicate": "X happened",
        "qualifying_fact": "X happened",
        "rule_quote": "Resolves Yes if X happened.",
        "update_quote": "Official evidence confirms X happened.",
        "counterargument": "proposal said No",
        "rationale": "The update closes the predicate.",
        "fundamental_intent_assessment": "consistent",
        "contract_correction_or_refund": False,
        "proof_closed": True,
    }


def test_snapshot_packet_and_zero_notional_executable_signal() -> None:
    snapshot = snapshot_fixture()
    packets = clarification_packets_from_snapshot(snapshot)
    assert len(packets) == 1
    packet = packets[0]
    assert packet["proposal_outcome"] == "No"
    assert packet["contract_corpus_sha256"] == "corpus-1"

    signal = score_clarification_card(
        packet,
        card_fixture(packet["case_id"]),
        snapshot,
        observed_at_utc="2026-08-12T12:01:00+00:00",
    )
    assert signal["eligible_shadow"] is True
    assert signal["selected_outcome"] == "Yes"
    assert signal["ask_vwap"] == 0.5
    assert signal["zero_notional"] is True
    assert signal["execution_book_snapshot_id"] == "clarification-book-1"
    assert signal["snapshot_ts_utc"] == "2026-08-12T12:01:00+00:00"
    assert signal["conservative_policy_edge_per_share"] > 0.4


def test_stale_update_fails_closed() -> None:
    snapshot = snapshot_fixture()
    packet = clarification_packets_from_snapshot(snapshot)[0]
    signal = score_clarification_card(
        packet,
        card_fixture(packet["case_id"]),
        snapshot,
        observed_at_utc="2026-08-12T12:10:00+00:00",
    )
    assert signal["eligible_shadow"] is False
    assert "update_to_executable_quote_over_300s" in signal["blockers"]


def test_custom_outcome_index_cannot_be_confused_with_yes_no() -> None:
    snapshot = snapshot_fixture()
    snapshot["outcomes"] = ["Over", "Under"]
    snapshot["tokens"] = ["over-token", "under-token"]
    snapshot["books"] = {
        "under-token": {
            "timestamp": 1786536060000,
            "asks": [{"price": "0.50", "size": "30"}],
        }
    }
    packet = clarification_packets_from_snapshot(snapshot)[0]
    packet["outcomes"] = ["Over", "Under"]
    card = card_fixture(packet["case_id"])
    card["determination"] = "Outcome1"
    signal = score_clarification_card(
        packet,
        card,
        snapshot,
        observed_at_utc="2026-08-12T12:01:00+00:00",
    )
    assert signal["selected_outcome"] == "Under"
    assert signal["selected_token"] == "under-token"


def test_identical_update_cluster_allows_one_market() -> None:
    first = {
        "eligible_shadow": True,
        "update_cluster_id": "same",
        "market_id": "1",
        "conservative_policy_edge_per_share": 0.2,
        "blockers": [],
    }
    second = {
        "eligible_shadow": True,
        "update_cluster_id": "same",
        "market_id": "2",
        "conservative_policy_edge_per_share": 0.3,
        "blockers": [],
    }
    apply_update_cluster_cap([first, second])
    assert first["eligible_shadow"] is False
    assert second["eligible_shadow"] is True


def test_combined_loop_builds_bounded_court_command(tmp_path) -> None:
    command = clarification_worker_command(
        tmp_path,
        model="gpt-test",
        reasoning_effort="medium",
        max_new_cards=3,
    )
    assert "--codex" in command
    assert command[command.index("--max-new-cards") + 1] == "3"
    assert command[command.index("--output-root") + 1] == str(tmp_path)


def test_court_worker_persists_exact_quote_and_common_case_identity(tmp_path, monkeypatch) -> None:
    snapshot = snapshot_fixture()
    (tmp_path / "snapshots.jsonl").write_text(json.dumps(snapshot) + "\n")
    packet = clarification_packets_from_snapshot(snapshot)[0]
    artifact = {
        "schema_version": "clarification_forward_card_v1",
        "prompt_version": PROMPT_VERSION,
        "case_id": packet["case_id"],
        "input_sha256": packet["input_sha256"],
        "execution_isolation": PACKET_ONLY_EXECUTION_ISOLATION,
        "card": card_fixture(packet["case_id"]),
    }
    (tmp_path / "clarification_cards.jsonl").write_text(json.dumps(artifact) + "\n")
    raw_book = {
        "timestamp": 1786536060000,
        "asks": [{"price": "0.50", "size": "30"}],
    }
    capture = materialize_orderbook_capture(
        token_id="yes-token",
        raw_book=raw_book,
        request_started_at_utc="2026-08-12T12:00:59.900Z",
        response_received_at_utc="2026-08-12T12:01:00.000Z",
        parsed_at_utc="2026-08-12T12:01:00.001Z",
        request_batch_capture_id="court-batch-1",
    )
    monkeypatch.setattr(
        "scripts.ops.polymarket_clarification_forward.get_book_with_capture",
        lambda token, request_batch_capture_id: (raw_book, capture),
    )
    summary = run_once(
        argparse.Namespace(
            output_root=tmp_path,
            codex=False,
            model="gpt-test",
            reasoning_effort="medium",
            batch_size=4,
            parallel_batches=2,
            max_new_cards=8,
        )
    )
    assert summary["signals_written"] == 1
    signal = json.loads((tmp_path / "clarification_signals.jsonl").read_text())
    quote = json.loads((tmp_path / "clarification_quote_snapshots.jsonl").read_text())
    assert signal["case_id"] == snapshot["case_id"]
    assert signal["clarification_packet_id"] == packet["case_id"]
    assert signal["execution_book_snapshot_id"] == capture["book_capture_id"]
    assert quote["case_id"] == snapshot["case_id"]
