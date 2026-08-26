from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from src.polymarket_alpha.contracts import (
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    RuleGate,
    ThresholdOperator,
    ThresholdSpec,
    bytes_sha256,
    rule_sha256,
    stable_record_id,
)
from src.polymarket_alpha.rules import (
    CorpusSourceIdentity,
    ParseStatus,
    RuleCompilationRequest,
    RuleContractCompiler,
    RuleSourceEvidence,
    StructuredRuleParse,
    corpus_from_legacy,
    evaluate_gate_a,
    evaluate_gate_b,
    structured_parse_from_legacy,
)
from src.polymarket_alpha.storage import AlphaRepository
from src.strategies.rule_lawyer.contract_corpus import build_contract_corpus


UTC = timezone.utc
NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
RAW_RULE = (
    "This market resolves Yes if Example Agency publishes a final bulletin "
    "confirming at least 10 qualifying events by 5 PM America/New_York on "
    "August 31, 2026. The final bulletin takes precedence over preliminary reports."
)
RULE_HASH = rule_sha256(RAW_RULE)
FIXTURES = Path(__file__).with_name("fixtures")
CORE_FIELDS = (
    "subject_entity",
    "entity_match_rule",
    "yes_trigger",
    "threshold",
    "deadline",
    "resolution_sources",
    "source_precedence",
)


def _evidence(
    *,
    adjudication_use: str = "binding",
    field_names: tuple[str, ...] = CORE_FIELDS,
) -> RuleSourceEvidence:
    return RuleSourceEvidence(
        field_names=field_names,
        source_artifact_id=f"source_artifact:{bytes_sha256(RAW_RULE.encode())}",
        source_content_sha256=bytes_sha256(RAW_RULE.encode()),
        artifact_text=RAW_RULE,
        quote_start=0,
        quote_end=len(RAW_RULE),
        legal_role="binding_resolution_rule",
        adjudication_use=adjudication_use,
        observed_at=NOW,
    )


def _parsed(
    *,
    clarity: str = "0.93",
    ambiguities: tuple[str, ...] = (),
) -> StructuredRuleParse:
    return StructuredRuleParse(
        subject_entity="Example Agency",
        entity_match_rule="The agency named in the binding market rule",
        yes_trigger="A final bulletin confirms at least ten qualifying events",
        threshold=ThresholdSpec(
            operator=ThresholdOperator.GTE,
            value=Decimal("10"),
            unit="qualifying events",
        ),
        deadline=NOW + timedelta(days=5),
        timezone="America/New_York",
        resolution_sources=("Example Agency final bulletin",),
        source_precedence=("final bulletin", "preliminary report"),
        initial_or_final="FINAL",
        qualifying_examples=("A final bulletin reports 10 events",),
        non_qualifying_examples=("A preliminary report estimates 10 events",),
        ambiguities=ambiguities,
        clarity_score=Decimal(clarity),
    )


def _request(
    *,
    parsed: StructuredRuleParse | None = None,
    parse_status: ParseStatus = ParseStatus.PARSED,
    expected_rule_hash: str = RULE_HASH,
    evidence: tuple[RuleSourceEvidence, ...] | None = None,
    corpus=None,
    run_id: str = "rule-run-1",
    compiled_at: datetime = NOW,
) -> RuleCompilationRequest:
    return RuleCompilationRequest(
        run_id=run_id,
        market_id="market-1",
        market_snapshot_id="market_snapshot:fixture-1",
        raw_rule_text=RAW_RULE,
        expected_rule_hash=expected_rule_hash,
        parse_status=parse_status,
        parsed=_parsed() if parsed is None and parse_status == ParseStatus.PARSED else parsed,
        parser_version="legacy-rule-parse-v1",
        compiled_at=compiled_at,
        source_evidence=(_evidence(),) if evidence is None else evidence,
        corpus=corpus,
    )


def _market() -> MarketSnapshot:
    return MarketSnapshot(
        record_id=stable_record_id("market_snapshot", "market-1", RULE_HASH),
        run_id="catalog-fixture",
        created_at=NOW,
        source="fixture_catalog",
        source_version="v1",
        identity=MarketIdentity(
            event_id="event-1",
            market_id="market-1",
            condition_id="condition-1",
            yes_token_id="yes-token",
            no_token_id="no-token",
        ),
        title="Fixture market",
        question="Will Example Agency report at least ten events?",
        status=MarketStatus.ACTIVE,
        end_at=NOW + timedelta(days=5),
        rules_raw=RAW_RULE,
        source_observed_at=NOW,
        ingested_at=NOW,
    )


def _ancillary(description: str) -> str:
    return (
        f"q: title: Fixture, description: {description} market_id: 123 "
        "res_data: p1: 0, p2: 1,initializer:0xcreator"
    )


def _corpus(*, with_operational_notice: bool = False):
    description = "A final bulletin reporting ten qualifying events resolves Yes."
    text = _ancillary(description)
    bulletin = None
    if with_operational_notice:
        bulletin = {
            "creator": "0x0000000000000000000000000000000000000001",
            "ancillary_text": text,
            "updates": [
                {
                    "timestamp": 100,
                    "text": "We're aware of the dispute. If a clarification is to be issued, it will be at 3 PM ET.",
                }
            ],
        }
    payload = build_contract_corpus(
        ancillary_text=text,
        market={"description": description},
        observed_at_utc=NOW.isoformat(),
        observed_at_ts=200,
        bulletin=bulletin,
    )
    return corpus_from_legacy(
        payload,
        source_identity=CorpusSourceIdentity(
            source_path="/fixture/dispute.db",
            device=7,
            inode=11,
            schema_sha256="d" * 64,
        ),
        source_artifact_id=f"source_artifact:{payload['contract_corpus_sha256']}",
    )


def test_compiler_emits_deterministic_contract_and_exact_quote_trace() -> None:
    compiler = RuleContractCompiler()
    first = compiler.compile(_request())
    second = compiler.compile(_request())

    assert first == second
    assert first.contract is not None
    assert first.contract.rule_hash == RULE_HASH
    assert first.contract.rule_gate == RuleGate.PASS
    assert first.contract.threshold is not None
    assert first.contract.threshold.value == Decimal("10")
    assert first.contract.deadline == NOW + timedelta(days=5)
    assert first.contract.timezone == "America/New_York"
    assert first.contract.initial_or_final == "FINAL"
    assert first.receipt.source_evidence[0].quote == RAW_RULE
    assert first.receipt.source_evidence[0].source_content_sha256 == bytes_sha256(
        RAW_RULE.encode()
    )


def test_cross_run_compilation_keeps_logical_revision_but_revises_instances() -> None:
    compiler = RuleContractCompiler()
    first = compiler.compile(_request(run_id="rule-run-1", compiled_at=NOW))
    replay = compiler.compile(_request(run_id="rule-run-1", compiled_at=NOW))
    later = compiler.compile(
        _request(run_id="rule-run-2", compiled_at=NOW + timedelta(minutes=5))
    )

    assert first == replay
    assert first.contract is not None and later.contract is not None
    assert first.contract.contract_revision_id == later.contract.contract_revision_id
    assert first.contract.record_id != later.contract.record_id
    assert first.contract.canonical_sha256 != later.contract.canonical_sha256
    assert first.receipt.record_id != later.receipt.record_id

    gate_a_first = evaluate_gate_a(
        first.contract,
        first.receipt,
        run_id="gate-run-1",
        evaluated_at=NOW,
    )
    gate_a_later = evaluate_gate_a(
        later.contract,
        later.receipt,
        run_id="gate-run-2",
        evaluated_at=NOW + timedelta(minutes=6),
    )
    assert gate_a_first.contract_revision_id == gate_a_later.contract_revision_id
    assert gate_a_first.record_id != gate_a_later.record_id


def test_cross_run_contract_instances_persist_under_one_semantic_revision(
    tmp_path,
) -> None:
    compiler = RuleContractCompiler()
    first = compiler.compile(_request(run_id="rule-run-1", compiled_at=NOW))
    later = compiler.compile(
        _request(run_id="rule-run-2", compiled_at=NOW + timedelta(minutes=5))
    )
    assert first.contract is not None and later.contract is not None

    repo = AlphaRepository(tmp_path / "cross-run-rules.db")
    repo.save_contract(_market())
    repo.save_contract(first.contract)
    repo.save_contract(later.contract)
    conn = sqlite3.connect(tmp_path / "cross-run-rules.db")
    rows = conn.execute(
        "SELECT rule_contract_id, contract_revision_id "
        "FROM alpha_rule_contract_instance_v3 ORDER BY rule_contract_id"
    ).fetchall()
    assert len(rows) == 2
    assert {row[0] for row in rows} == {
        first.contract.record_id,
        later.contract.record_id,
    }
    assert {row[1] for row in rows} == {first.contract.contract_revision_id}
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_rule_contract_and_gate_golden_hashes_are_stable() -> None:
    golden = json.loads(
        (FIXTURES / "p0_07_rule_gate_golden.json").read_text(encoding="utf-8")
    )
    outcome = RuleContractCompiler().compile(_request())
    assert outcome.contract is not None
    contract = outcome.contract
    gate_a = evaluate_gate_a(
        contract,
        outcome.receipt,
        run_id="gate-run",
        evaluated_at=NOW,
    )
    gate_b = evaluate_gate_b(
        contract,
        gate_a,
        market_packet_id="market_packet:fixture-1",
        market_packet_rule_hash=contract.rule_hash,
        market_packet_contract_revision_id=contract.contract_revision_id,
        run_id="gate-run",
        evaluated_at=NOW + timedelta(minutes=1),
    )
    actual = {
        "contract": {
            "canonical_sha256": contract.canonical_sha256,
            "contract_revision_id": contract.contract_revision_id,
            "record_id": contract.record_id,
            "rule_gate": contract.rule_gate.value,
            "rule_hash": contract.rule_hash,
        },
        "gate_a": {
            "canonical_sha256": gate_a.canonical_sha256,
            "decision": gate_a.decision,
            "record_id": gate_a.record_id,
        },
        "gate_b": {
            "canonical_sha256": gate_b.canonical_sha256,
            "decision": gate_b.decision,
            "record_id": gate_b.record_id,
        },
        "receipt": {
            "canonical_sha256": outcome.receipt.canonical_sha256,
            "record_id": outcome.receipt.record_id,
            "status": outcome.receipt.status.value,
        },
    }
    assert actual == golden


@pytest.mark.parametrize(
    ("compilation_request", "reason"),
    [
        (_request(expected_rule_hash="a" * 64), "RULE_HASH_MISMATCH"),
        (
            _request(parsed=None, parse_status=ParseStatus.UNAVAILABLE),
            "PARSER_UNAVAILABLE",
        ),
        (
            _request(evidence=()),
            "MISSING_SOURCE_EVIDENCE:subject_entity",
        ),
        (
            _request(evidence=(_evidence(adjudication_use="excluded"),)),
            "MISSING_SOURCE_EVIDENCE:subject_entity",
        ),
    ],
)
def test_compiler_fail_closes_invalid_backend_hash_and_source_lineage(
    compilation_request: RuleCompilationRequest, reason: str
) -> None:
    outcome = RuleContractCompiler().compile(compilation_request)

    assert outcome.contract is None
    assert outcome.receipt.status.value == "BLOCKED"
    assert reason in outcome.receipt.reasons


def test_ambiguity_precedence_and_low_clarity_do_not_pass_gate_a() -> None:
    compiler = RuleContractCompiler()
    ambiguous = compiler.compile(
        _request(parsed=_parsed(ambiguities=("finality definition unclear",)))
    )
    review_source = compiler.compile(
        _request(evidence=(_evidence(adjudication_use="review_required"),))
    )
    low = compiler.compile(_request(parsed=_parsed(clarity="0.40")))

    assert ambiguous.contract is not None
    assert ambiguous.contract.rule_gate == RuleGate.WATCH_RULE
    assert review_source.contract is not None
    assert review_source.contract.rule_gate == RuleGate.WATCH_RULE
    assert "SOURCE_PRECEDENCE_REVIEW_REQUIRED" in review_source.receipt.reasons
    assert low.contract is not None
    assert low.contract.rule_gate == RuleGate.REJECT_RULE
    assert evaluate_gate_a(
        ambiguous.contract,
        ambiguous.receipt,
        run_id="gate-run",
        evaluated_at=NOW,
    ).decision == "WATCH_RULE"


def test_rule_hash_and_corpus_hash_revision_independently_and_persist(tmp_path) -> None:
    compiler = RuleContractCompiler()
    first = compiler.compile(_request(corpus=_corpus()))
    second = compiler.compile(_request(corpus=_corpus(with_operational_notice=True)))
    compiler_revision = RuleContractCompiler(version="rule-contract-compiler-v2").compile(
        _request(corpus=_corpus())
    )
    assert (
        first.contract is not None
        and second.contract is not None
        and compiler_revision.contract is not None
    )
    assert first.contract.rule_hash == second.contract.rule_hash == RULE_HASH
    assert first.contract.contract_corpus_sha256 != second.contract.contract_corpus_sha256
    assert first.contract.record_id != second.contract.record_id

    repo = AlphaRepository(tmp_path / "rule-revisions.db")
    repo.save_contract(_market())
    repo.save_contract(first.contract)
    conn = sqlite3.connect(tmp_path / "rule-revisions.db")
    conn.execute(
        "INSERT INTO alpha_rule_contract_revision VALUES (?, ?, ?)",
        (first.contract.record_id, first.contract.market_id, first.contract.rule_hash),
    )
    conn.execute(
        "DELETE FROM alpha_rule_contract_revision_v2 WHERE rule_contract_id = ?",
        (first.contract.record_id,),
    )
    conn.execute(
        "DELETE FROM alpha_rule_contract_instance_v3 WHERE rule_contract_id = ?",
        (first.contract.record_id,),
    )
    conn.commit()
    conn.close()

    # Replaying migration on a pre-v2 projection recovers corpus identity from
    # the immutable canonical RuleContract JSON rather than labeling it absent.
    repo.migrate()
    repo.save_contract(second.contract)
    repo.save_contract(compiler_revision.contract)
    conn = sqlite3.connect(tmp_path / "rule-revisions.db")
    rows = conn.execute(
        "SELECT rule_hash, contract_corpus_sha256, compiler_version "
        "FROM alpha_rule_contract_instance_v3 ORDER BY contract_revision_id"
    ).fetchall()
    assert len(rows) == 3
    assert {row[0] for row in rows} == {RULE_HASH}
    assert len({row[1] for row in rows}) == 2
    assert {row[2] for row in rows} == {
        "rule-contract-compiler-v1",
        "rule-contract-compiler-v2",
    }
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gate_b_enforces_same_hash_revision_compiler_and_gate_a(tmp_path) -> None:
    outcome = RuleContractCompiler().compile(_request())
    assert outcome.contract is not None
    contract = outcome.contract
    gate_a = evaluate_gate_a(
        contract,
        outcome.receipt,
        run_id="gate-run",
        evaluated_at=NOW,
    )
    passed = evaluate_gate_b(
        contract,
        gate_a,
        market_packet_id="market_packet:fixture-1",
        market_packet_rule_hash=contract.rule_hash,
        market_packet_contract_revision_id=contract.contract_revision_id,
        run_id="gate-run",
        evaluated_at=NOW + timedelta(minutes=1),
    )
    risky = evaluate_gate_b(
        contract,
        gate_a,
        market_packet_id="market_packet:fixture-1",
        market_packet_rule_hash=contract.rule_hash,
        market_packet_contract_revision_id=contract.contract_revision_id,
        run_id="gate-run",
        evaluated_at=NOW + timedelta(minutes=1),
        rule_risk_reasons=("SOURCE_FINALITY_PENDING",),
    )
    mismatched = evaluate_gate_b(
        contract,
        gate_a,
        market_packet_id="market_packet:fixture-2",
        market_packet_rule_hash="f" * 64,
        market_packet_contract_revision_id="rule_revision:wrong",
        run_id="gate-run",
        evaluated_at=NOW + timedelta(minutes=1),
    )

    assert gate_a.decision == "PASS"
    assert passed.decision == "PASS"
    assert risky.decision == "PASS_WITH_RULE_RISK"
    assert mismatched.decision == "BLOCK"
    assert set(mismatched.reasons) == {
        "MARKET_PACKET_RULE_HASH_MISMATCH",
        "MARKET_PACKET_RULE_REVISION_MISMATCH",
    }
    repo = AlphaRepository(tmp_path / "gate-decisions.db")
    repo.save_contract(_market())
    repo.save_contract(contract)
    repo.save_contract(outcome.receipt)
    repo.save_contract(gate_a)
    repo.save_contract(passed)
    conn = sqlite3.connect(tmp_path / "gate-decisions.db")
    assert conn.execute(
        "SELECT stage, decision FROM alpha_rule_gate_decision_v3 ORDER BY stage"
    ).fetchall() == [("A", "PASS"), ("B", "PASS")]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_legacy_parse_adapter_requires_explicit_timezone_entity_and_sources() -> None:
    payload = {
        "time_window": {
            "end_at_utc": "2026-08-31T21:00:00Z",
            "timezone_source": "America/New_York",
        },
        "trigger_minimum_conditions": ["Final bulletin reports at least ten events"],
        "entity_definitions": [
            {"entity": "Example Agency", "definition": "Named official agency"}
        ],
        "ambiguity_flags": [],
        "ambiguity_explanations": [],
        "yes_case_examples": ["Final count is ten"],
        "no_case_examples": ["Preliminary count is ten"],
        "clarity_score": 0.9,
    }
    parsed = structured_parse_from_legacy(
        payload,
        resolution_sources=("Example Agency final bulletin",),
        source_precedence=("final bulletin", "preliminary report"),
        threshold=ThresholdSpec(
            operator=ThresholdOperator.GTE, value=Decimal("10"), unit="events"
        ),
        initial_or_final="FINAL",
    )
    assert parsed.deadline == datetime(2026, 8, 31, 21, 0, tzinfo=UTC)
    assert parsed.timezone == "America/New_York"
    assert parsed.initial_or_final == "FINAL"
    with pytest.raises(ValueError, match="timezone"):
        structured_parse_from_legacy(
            {**payload, "time_window": {"end_at_utc": "2026-08-31T21:00:00Z"}},
            resolution_sources=("official",),
            source_precedence=("official",),
        )


def test_source_and_corpus_tampering_fail_validation() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        RuleSourceEvidence(
            field_names=("yes_trigger",),
            source_artifact_id="source_artifact:bad",
            source_content_sha256="a" * 64,
            artifact_text="different",
            quote_start=0,
            quote_end=9,
            legal_role="binding",
            observed_at=NOW,
        )
    description = "A final bulletin reporting ten qualifying events resolves Yes."
    payload = build_contract_corpus(
        ancillary_text=_ancillary(description),
        market={"description": description},
        observed_at_utc=NOW.isoformat(),
        observed_at_ts=200,
    )
    payload["contract_corpus_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="aggregate hash mismatch"):
        corpus_from_legacy(
            payload,
            source_identity=CorpusSourceIdentity(
                source_path="/fixture/dispute.db",
                device=7,
                inode=11,
                schema_sha256="d" * 64,
            ),
            source_artifact_id="source_artifact:tampered",
        )
    with pytest.raises(ValidationError, match="absolute"):
        CorpusSourceIdentity(
            source_path="relative/dispute.db",
            device=7,
            inode=11,
            schema_sha256="d" * 64,
        )
