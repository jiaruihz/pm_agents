from __future__ import annotations

from datetime import datetime, timezone

from src.strategies.rule_lawyer.dispute_strategy import (
    apply_cluster_cap,
    build_rule_review_queue,
    contract_verdict_blockers,
    proposal_is_prospective,
    review_available_for_snapshot,
    selected_market_settlement_payout,
    score_binance_verdict_snapshot,
    score_semantic_review_snapshot,
    score_sports_verdict_snapshot,
    snapshot_case_features,
    snapshot_fee_rate,
    update_shadow_ledger,
)


def test_snapshot_case_features_and_fee_rate() -> None:
    snapshot = {
        "title": "Player assists O/U 4.5",
        "proposal": {"proposal_ts": 100, "dispute_ts": 160, "proposed_binary": 0},
        "issue_features": {
            "track": "mechanical",
            "mechanism": "sports_stat_scope",
            "source_class": "designated_web_source",
            "outcome_structure": "threshold",
            "has_designated_source": True,
            "has_credible_reporting": False,
            "has_precise_time": False,
            "has_numeric_condition": True,
            "has_ambiguity_marker": False,
            "source_domains": ["nba.com"],
        },
        "clob_market": {"fd": {"r": 0.05, "e": 1, "to": True}},
    }
    features = snapshot_case_features(snapshot)
    assert features["market_theme_v1"] == "sports_esports"
    assert features["proposed_side"] == "NO"
    assert features["top_source_domain"] == "nba.com"
    assert snapshot_fee_rate(snapshot) == 0.05
    snapshot["clob_market"] = {}
    snapshot["reverse_token"] = "token"
    snapshot["fee_rates"] = {"token": {"base_fee": 0}}
    assert snapshot_fee_rate(snapshot) == 0.0


def test_cluster_cap_keeps_only_highest_edge_expression() -> None:
    rows = [
        {
            "candidate_id": "low",
            "opportunity_cluster_id": "sports_event:a",
            "net_edge_per_share": 0.12,
            "eligible_shadow": True,
            "blockers": [],
        },
        {
            "candidate_id": "high",
            "opportunity_cluster_id": "sports_event:a",
            "net_edge_per_share": 0.18,
            "eligible_shadow": True,
            "blockers": [],
        },
    ]
    apply_cluster_cap(rows)
    assert not rows[0]["eligible_shadow"]
    assert rows[0]["blockers"] == ["lower_edge_sibling_in_opportunity_cluster"]
    assert rows[1]["eligible_shadow"]


def test_prospective_proposal_is_p4_not_rule_repricing() -> None:
    snapshot = {
        "proposal": {"proposal_ts": 100},
        "market_status": {"game_start_time": "1970-01-01T00:03:20Z"},
    }
    assert proposal_is_prospective(snapshot)
    snapshot["proposal"]["proposal_ts"] = 300
    assert not proposal_is_prospective(snapshot)
    snapshot = {
        "proposal": {"proposal_ts": 100},
        "market_status": {"end_date": "1970-01-01T00:03:20Z"},
    }
    assert proposal_is_prospective(snapshot)


def test_manual_review_cannot_backfill_an_earlier_book_snapshot() -> None:
    review = {"reviewed_at_utc": "2026-01-01T00:05:00Z"}
    assert not review_available_for_snapshot(
        review, {"captured_at_utc": "2026-01-01T00:04:59Z"}
    )
    assert review_available_for_snapshot(
        review, {"captured_at_utc": "2026-01-01T00:05:00Z"}
    )


def test_binance_verdict_policy_requires_verified_reverse_and_five_cent_edge() -> None:
    snapshot = {
        "case_id": "c1",
        "market_id": "1",
        "opportunity_cluster_id": "market:1",
        "title": "Bitcoin Up or Down",
        "captured_at_utc": "2026-01-01T00:00:00+00:00",
        "capture_delay_seconds": 30,
        "proposal": {"proposal_ts": 100},
        "market_status": {},
        "outcomes": ["Down", "Up"],
        "reverse_index": 1,
        "reverse_token": "up-token",
        "reverse_executable_vwap": {"25": 0.80},
        "books": {"up-token": {"timestamp": str(int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))}},
        "clob_market": {"fd": {"r": 0.07}},
        "rule_verdict": {
            "status": "verified",
            "winning_outcome": "Up",
            "resolver_id": "binance_spot_1h_candle_v1",
        },
    }
    candidate = score_binance_verdict_snapshot(
        snapshot, scored_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
    )
    assert candidate["eligible_shadow"]
    assert candidate["p_reverse"] == 1.0
    assert candidate["net_edge_per_share"] > 0.18


def test_deterministic_policy_uses_current_book_age_not_initial_capture_delay() -> None:
    snapshot = {
        "case_id": "c2",
        "market_id": "2",
        "captured_at_utc": "2026-01-02T00:00:00+00:00",
        "capture_delay_seconds": 86_400,
        "proposal": {"proposal_ts": 100},
        "market_status": {},
        "outcomes": ["Down", "Up"],
        "reverse_index": 1,
        "reverse_token": "up-token",
        "reverse_executable_vwap": {"25": 0.80},
        "books": {"up-token": {"timestamp": str(int(datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp() * 1000))}},
        "clob_market": {"fd": {"r": 0.0}},
        "rule_verdict": {
            "status": "verified",
            "winning_outcome": "Up",
            "resolver_id": "binance_spot_1h_candle_v1",
        },
    }
    candidate = score_binance_verdict_snapshot(
        snapshot, scored_at=datetime(2026, 1, 2, 0, 1, tzinfo=timezone.utc)
    )
    assert candidate["eligible_shadow"]
    assert candidate["book_age_seconds"] == 60


def test_semantic_review_policy_requires_verified_current_reverse() -> None:
    snapshot = {
        "case_id": "c3",
        "market_id": "3",
        "captured_at_utc": "2026-01-03T00:00:00+00:00",
        "capture_delay_seconds": 10_000,
        "proposal": {"proposal_ts": 100},
        "market_status": {},
        "outcomes": ["Yes", "No"],
        "reverse_index": 1,
        "reverse_token": "no-token",
        "reverse_executable_vwap": {"25": 0.90},
        "books": {"no-token": {"timestamp": str(int(datetime(2026, 1, 3, tzinfo=timezone.utc).timestamp() * 1000))}},
        "fee_rates": {"no-token": {"base_fee": 0}},
        "clob_market": {},
        "issue_features": {"mechanism": "semantic_rule_scope"},
        "rule_verdict": {
            "status": "verified",
            "winning_outcome": "No",
            "confidence": 0.97,
            "resolver_id": "semantic_official_timeline_manual_v1",
        },
    }
    candidate = score_semantic_review_snapshot(
        snapshot, scored_at=datetime(2026, 1, 3, 0, 1, tzinfo=timezone.utc)
    )
    assert candidate["eligible_shadow"]
    assert candidate["p_reverse"] == 0.97
    assert 0.06 < candidate["net_edge_per_share"] < 0.08
    snapshot["market_status"] = {"end_date": "2026-01-04T00:00:00Z"}
    blocked = score_semantic_review_snapshot(
        snapshot, scored_at=datetime(2026, 1, 3, 0, 1, tzinfo=timezone.utc)
    )
    assert not blocked["eligible_shadow"]
    assert "proposal_before_objective_deadline_p4" in blocked["blockers"]


def test_semantic_verdict_must_review_current_official_clarification() -> None:
    snapshot = {
        "case_id": "clarified",
        "market_id": "4",
        "captured_at_utc": "2026-01-04T00:00:00+00:00",
        "proposal": {"proposal_ts": 100},
        "market_status": {},
        "outcomes": ["Yes", "No"],
        "reverse_index": 1,
        "reverse_token": "no-token",
        "reverse_executable_vwap": {"25": 0.50},
        "books": {"no-token": {"timestamp": str(int(datetime(2026, 1, 4, tzinfo=timezone.utc).timestamp() * 1000))}},
        "fee_rates": {"no-token": {"base_fee": 0}},
        "clob_market": {},
        "issue_features": {"mechanism": "general_semantic_boundary"},
        "contract_corpus": {
            "contract_corpus_sha256": "corpus-v2",
            "binding_map": {
                "adjudication_blockers": [
                    "official_clarification_requires_fundamental_intent_review"
                ]
            },
        },
        "rule_verdict": {
            "status": "verified",
            "winning_outcome": "No",
            "confidence": 0.97,
            "resolver_id": "semantic_visual_definition_v1",
        },
    }
    blocked = score_semantic_review_snapshot(
        snapshot, scored_at=datetime(2026, 1, 4, 0, 1, tzinfo=timezone.utc)
    )
    assert "verdict_does_not_reference_current_contract_corpus" in blocked["blockers"]
    snapshot["rule_verdict"].update(
        {
            "contract_corpus_sha256": "corpus-v2",
            "clarification_assessment": "consistent_with_fundamental_intent",
        }
    )
    allowed = score_semantic_review_snapshot(
        snapshot, scored_at=datetime(2026, 1, 4, 0, 1, tzinfo=timezone.utc)
    )
    assert allowed["eligible_shadow"]
    assert 0.46 < allowed["net_edge_per_share"] < 0.48


def test_contract_correction_is_not_trade_safe_without_consistency_assessment() -> None:
    snapshot = {
        "contract_corpus": {
            "contract_corpus_sha256": "corpus-correction",
            "binding_map": {
                "adjudication_blockers": [
                    "official_contract_correction_or_refund_requires_precedence_review"
                ]
            },
        }
    }
    verdict = {
        "contract_corpus_sha256": "corpus-correction",
        "contract_correction_assessment": "market_refund_or_rewrite_not_tradeable",
    }
    assert contract_verdict_blockers(snapshot, verdict) == [
        "official_contract_correction_or_refund_not_trade_safe"
    ]
    verdict["contract_correction_assessment"] = "consistent_with_fundamental_intent"
    assert contract_verdict_blockers(snapshot, verdict) == []


def test_shadow_ledger_opens_once_and_records_executable_markout(tmp_path) -> None:
    signal = {
        "candidate_id": "sig1",
        "eligible_shadow": True,
        "zero_notional": True,
        "snapshot_ts_utc": "2026-01-03T00:00:00+00:00",
        "policy_id": "semantic_verified_reverse_shadow_v1",
        "case_id": "c3",
        "market_id": "3",
        "opportunity_cluster_id": "market:3",
        "title": "Example",
        "reverse_outcome": "No",
        "reverse_token": "no-token",
        "quantity": 25,
        "reverse_ask_vwap": 0.90,
        "modeled_fee_per_share": 0,
        "net_edge_per_share": 0.10,
        "rule_verdict": {"status": "verified"},
    }
    (tmp_path / "signals.jsonl").write_text(__import__("json").dumps(signal) + "\n")
    (tmp_path / "paper_fills.jsonl").write_text(
        __import__("json").dumps(
            {
                "fill_id": "fill1",
                "order_id": "order1",
                "intent_id": "intent1",
                "candidate_id": "sig1",
            }
        )
        + "\n"
    )
    snapshot = {
        "case_id": "c3",
        "captured_at_utc": "2026-01-03T00:05:00+00:00",
        "proposal": {"request_class": "unsettled"},
        "reverse_executable_bid_vwap": {"25": 0.92},
        "reverse_token": "no-token",
        "books": {"no-token": {"bids": [{"price": "0.92", "size": "25"}]}},
        "clob_market": {"fd": {"r": 0}},
    }
    (tmp_path / "snapshots.jsonl").write_text(__import__("json").dumps(snapshot) + "\n")
    summary = update_shadow_ledger(
        tmp_path, updated_at=datetime(2026, 1, 3, 0, 5, tzinfo=timezone.utc)
    )
    assert summary["positions_opened"] == 1
    assert summary["markouts_written"] == 2
    second = update_shadow_ledger(
        tmp_path, updated_at=datetime(2026, 1, 3, 0, 6, tzinfo=timezone.utc)
    )
    assert second["positions_opened"] == 0
    assert second["markouts_written"] == 0


def test_sports_rule_verdict_can_select_proposal_side_and_fail_on_no_ask() -> None:
    snapshot = {
        "case_id": "c4",
        "market_id": "4",
        "captured_at_utc": "2026-01-04T00:00:00+00:00",
        "proposal": {"proposed_binary": 0},
        "outcomes": ["Odd", "Even"],
        "tokens": ["odd-token", "even-token"],
        "reverse_index": 0,
        "books": {
            "even-token": {
                "timestamp": str(int(datetime(2026, 1, 4, tzinfo=timezone.utc).timestamp() * 1000)),
                "asks": [{"price": "0.50", "size": "25"}],
            },
        },
        "fee_rates": {"even-token": {"base_fee": 0}},
        "clob_market": {},
        "issue_features": {"mechanism": "sports_stat_scope"},
        "rule_verdict": {
            "status": "verified",
            "winning_outcome": "Even",
            "confidence": 0.98,
            "resolver_id": "sports_official_period_split_manual_v1",
        },
    }
    candidate = score_sports_verdict_snapshot(
        snapshot, scored_at=datetime(2026, 1, 4, 0, 1, tzinfo=timezone.utc)
    )
    assert candidate["eligible_shadow"]
    assert candidate["verdict_relation_to_proposal"] == "proposal"
    assert candidate["selected_outcome"] == "Even"
    snapshot["books"]["even-token"] = {
        "timestamp": str(int(datetime(2026, 1, 4, tzinfo=timezone.utc).timestamp() * 1000)),
        "bids": [{"price": "0.95", "size": "25"}],
    }
    blocked = score_sports_verdict_snapshot(
        snapshot, scored_at=datetime(2026, 1, 4, 0, 1, tzinfo=timezone.utc)
    )
    assert not blocked["eligible_shadow"]
    assert "missing_25share_winning_outcome_ask_depth" in blocked["blockers"]


def test_market_settlement_uses_selected_outcome_and_requires_closed_market() -> None:
    snapshot = {
        "market_status": {"closed": False},
        "outcomes": ["Odd", "Even"],
        "outcome_prices": [0.0005, 0.9995],
    }
    assert selected_market_settlement_payout(snapshot, "Even") is None
    snapshot["market_status"]["closed"] = True
    assert selected_market_settlement_payout(snapshot, "Even") == 1.0
    assert selected_market_settlement_payout(snapshot, "Odd") == 0.0


def test_rule_review_queue_only_spends_research_when_five_cent_edge_is_possible() -> None:
    snapshot = {
        "case_id": "review-1",
        "market_id": "5",
        "opportunity_cluster_id": "market:5",
        "title": "Unresolved sports stat",
        "captured_at_utc": "2026-01-05T00:00:00+00:00",
        "proposal": {"proposal_ts": 100, "dispute_ts": 120, "request_class": "unsettled"},
        "market_status": {"closed": False},
        "outcomes": ["Yes", "No"],
        "tokens": ["yes", "no"],
        "reverse_index": 0,
        "books": {
            "yes": {
                "timestamp": str(int(datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp() * 1000)),
                "asks": [{"price": "0.80", "size": "25"}],
            },
            "no": {
                "timestamp": str(int(datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp() * 1000)),
                "asks": [{"price": "0.95", "size": "25"}],
            },
        },
        "clob_market": {"fd": {"r": 0.05}},
        "issue_features": {
            "track": "mechanical",
            "mechanism": "sports_stat_scope",
            "source_domains": ["example.com"],
        },
        "rule_verdict": {"status": "unverified"},
    }
    queue = build_rule_review_queue(
        [snapshot], scored_at=datetime(2026, 1, 5, 0, 1, tzinfo=timezone.utc)
    )
    assert queue["candidates"] == 1
    assert [row["outcome"] for row in queue["rows"][0]["research_choices"]] == ["Yes"]
    snapshot["books"]["yes"]["asks"][0]["price"] = "0.95"
    blocked = build_rule_review_queue(
        [snapshot], scored_at=datetime(2026, 1, 5, 0, 1, tzinfo=timezone.utc)
    )
    assert blocked["candidates"] == 0
