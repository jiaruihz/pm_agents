"""GLM-OP-04 acceptance tests: resumable operational coordinator + CLI.

One synthetic Gamma fixture market is driven through every stage with an
explicit stop/resume at each filesystem handoff.  No network, no production
paths, no order or signing capability.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

from src.platform.market_data.capture_contract import materialize_orderbook_capture
from src.polymarket_alpha.decision import RankConfig
from src.polymarket_alpha.operational import (
    DEMAND_OUTBOX_LOCATOR,
    BookStageInputs,
    CoordinatorBlocked,
    CoordinatorState,
    GammaIngestStageInputs,
    GammaResponseReceipt,
    OperationalStage,
    OwnerBookLegSubmission,
    BlindResumeStageInputs,
    BlindStageInputs,
    MarketResumeStageInputs,
    ScanStageInputs,
    run_blind_resume_stage,
    run_blind_stage,
    run_book_stage,
    run_gamma_ingest_stage,
    run_market_resume_stage,
    run_scan_stage,
)
from src.polymarket_alpha.storage import AlphaRepository
from tests.polymarket_alpha.test_research_handoff_pipeline import (
    _blind_submission,
    _market_submission,
)
from tests.polymarket_alpha.test_rule_gates_p0_07 import (
    RAW_RULE,
    _parsed,
    _request as _rule_request,
)

_SPEC = importlib.util.spec_from_file_location(
    "gamma_payloads", Path(__file__).with_name("fixtures") / "gamma_payloads.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_gamma = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_gamma)
market_payload = _gamma.market_payload

UTC = timezone.utc
GAMMA_OBSERVED = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
INGESTED = GAMMA_OBSERVED + timedelta(minutes=1)
SCAN_AS_OF = GAMMA_OBSERVED + timedelta(minutes=5)
GATE_A_AT = GAMMA_OBSERVED + timedelta(minutes=6)
PACKET_AT = GAMMA_OBSERVED + timedelta(minutes=6, seconds=30)
HANDOFF_AT = GAMMA_OBSERVED + timedelta(minutes=6, seconds=31)
BLIND_IMPORTED_AT = GAMMA_OBSERVED + timedelta(hours=1, minutes=5)
DEMAND_REQUESTED_AT = BLIND_IMPORTED_AT + timedelta(seconds=30)
DEMAND_VALID_UNTIL = DEMAND_REQUESTED_AT + timedelta(minutes=10)
BOOK_RESPONSE_AT = DEMAND_REQUESTED_AT + timedelta(seconds=30)
BOOK_RECEIVED_AT = BOOK_RESPONSE_AT + timedelta(seconds=30)
MARKET_PACKET_AT = BOOK_RECEIVED_AT + timedelta(seconds=30)
MARKET_HANDOFF_AT = MARKET_PACKET_AT + timedelta(seconds=1)
MARKET_IMPORTED_AT = MARKET_PACKET_AT + timedelta(minutes=1, seconds=30)
GATE_B_AT = MARKET_IMPORTED_AT + timedelta(seconds=30)
DECISION_AT = GATE_B_AT + timedelta(seconds=30)
RUN_ID = "op-coordinator-run"
MAX_STALENESS = 600


def _repository(root: Path) -> AlphaRepository:
    repository = AlphaRepository(root / "alpha.db")
    repository.migrate()
    return repository


def _gamma_inputs() -> GammaIngestStageInputs:
    market = market_payload(rules=RAW_RULE)
    body = json.dumps(
        [{"id": "event-op", "title": "Operational Fixture Event", "markets": [market]}],
        sort_keys=True,
    ).encode("utf-8")
    import hashlib

    receipt = GammaResponseReceipt(
        request_method="GET",
        endpoint_host="gamma-api.polymarket.com",
        endpoint_path="/events",
        http_status=200,
        response_bytes_sha256=hashlib.sha256(body).hexdigest(),
        response_byte_length=len(body),
        response_received_at=GAMMA_OBSERVED,
    )
    return GammaIngestStageInputs(
        raw_response=body,
        response_receipt=receipt,
        run_id=RUN_ID,
        observed_at=GAMMA_OBSERVED,
        ingested_at=INGESTED,
        page_budget=5,
        requested_limit=5,
    )


def test_multi_market_gamma_is_rejected_before_any_catalog_write(tmp_path: Path) -> None:
    markets = _gamma.multi_market_event_payloads()[:2]
    body = json.dumps(
        [{"id": "event-multi", "title": "Multi", "markets": markets}],
        sort_keys=True,
    ).encode("utf-8")
    import hashlib

    receipt = GammaResponseReceipt(
        request_method="GET",
        endpoint_host="gamma-api.polymarket.com",
        endpoint_path="/events",
        http_status=200,
        response_bytes_sha256=hashlib.sha256(body).hexdigest(),
        response_byte_length=len(body),
        response_received_at=GAMMA_OBSERVED,
    )
    repository = _repository(tmp_path)
    with pytest.raises(CoordinatorBlocked, match="caller limit"):
        run_gamma_ingest_stage(
            repository,
            tmp_path,
            GammaIngestStageInputs(
                raw_response=body,
                response_receipt=receipt,
                run_id="multi-market-rejected",
                observed_at=GAMMA_OBSERVED,
                ingested_at=INGESTED,
                page_budget=5,
            ),
        )
    conn = sqlite3.connect(tmp_path / "alpha.db")
    assert conn.execute("SELECT count(*) FROM alpha_contract_record").fetchone()[0] == 0
    conn.close()
    assert not (tmp_path / "state" / "gamma_ingested.json").exists()
def _coordinator_rule_request():
    return _rule_request(
        run_id="op-coordinator-rule",
        compiled_at=GATE_A_AT,
    ).model_copy(update={"market_id": "12345"})


def _run_through_blind(root: Path):
    repository = _repository(root)
    ingest = run_gamma_ingest_stage(repository, root, _gamma_inputs())
    scan = run_scan_stage(
        repository,
        root,
        ScanStageInputs(run_id=RUN_ID + "-scan", as_of=SCAN_AS_OF),
    )
    blind = run_blind_stage(
        repository,
        root,
        BlindStageInputs(
            rule_request=_coordinator_rule_request(),
            gate_evaluated_at=GATE_A_AT,
            packet_created_at=PACKET_AT,
            handoff_created_at=HANDOFF_AT,
        ),
    )
    return repository, ingest, scan, blind


def _write_blind_result(root: Path, packet) -> dict[str, bytes]:
    submitted, sources = _blind_submission(packet)
    inbox = root / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / "blind-result.json").write_bytes(submitted)
    return sources


def _book_inputs(demand, *, received_at=BOOK_RECEIVED_AT):
    identity = demand.identity
    raw = {
        "timestamp": str(int(BOOK_RESPONSE_AT.timestamp() * 1000)),
        "hash": "exchange-hash",
        "bids": [{"price": "0.40", "size": "25"}],
        "asks": [{"price": "0.50", "size": "20"}, {"price": "0.55", "size": "100"}],
    }

    def leg(token_id: str) -> tuple[dict, OwnerBookLegSubmission]:
        capture = materialize_orderbook_capture(
            token_id=token_id,
            raw_book=raw,
            request_started_at_utc=(
                BOOK_RESPONSE_AT - timedelta(milliseconds=100)
            )
            .isoformat()
            .replace("+00:00", "Z"),
            response_received_at_utc=BOOK_RESPONSE_AT.isoformat().replace("+00:00", "Z"),
            parsed_at_utc=(BOOK_RESPONSE_AT + timedelta(milliseconds=1))
            .isoformat()
            .replace("+00:00", "Z"),
            request_batch_capture_id="op-coordinator-batch",
        )
        return capture, OwnerBookLegSubmission(
            locator=f"inbox/books/{token_id}.json",
            raw_book_bytes=json.dumps(raw, sort_keys=True).encode("utf-8"),
            capture=capture,
            raw_artifact_id=f"owner_book_artifact:{capture['book_capture_id']}",
        )

    yes_capture, yes_leg = leg(identity.yes_token_id)
    no_capture, no_leg = leg(identity.no_token_id)
    receipt = {
        "capture_owner": "weather_market_books",
        "alpha_demand_id": demand.demand_id,
        "condition_id": identity.condition_id,
        "yes_token_id": identity.yes_token_id,
        "no_token_id": identity.no_token_id,
        "request_batch_capture_id": "op-coordinator-batch",
        "yes_book_capture_id": yes_capture["book_capture_id"],
        "no_book_capture_id": no_capture["book_capture_id"],
    }
    return BookStageInputs(
        owner_receipt=receipt,
        yes_submission=yes_leg,
        no_submission=no_leg,
        received_at=received_at,
        packet_created_at=MARKET_PACKET_AT,
        handoff_created_at=MARKET_HANDOFF_AT,
    )



def test_full_fixture_path_stops_and_resumes_at_every_handoff(tmp_path: Path) -> None:
    root = tmp_path
    repository = _repository(root)

    ingest = run_gamma_ingest_stage(repository, root, _gamma_inputs())
    assert ingest.state is not None and ingest.state.stage == OperationalStage.GAMMA_INGESTED
    assert len(ingest.state.snapshot_ids) == 1

    scan = run_scan_stage(
        repository, root, ScanStageInputs(run_id=RUN_ID + "-scan", as_of=SCAN_AS_OF)
    )
    assert scan.state.candidate_id is not None

    blind = run_blind_stage(
        repository,
        root,
        BlindStageInputs(
            rule_request=_coordinator_rule_request(),
            gate_evaluated_at=GATE_A_AT,
            packet_created_at=PACKET_AT,
            handoff_created_at=HANDOFF_AT,
        ),
    )
    assert (root / "outbox/blind.packet.json").is_file()
    assert (root / "state/BLIND_PACKET_FROZEN.json").is_file()

    # stop/resume: a brand-new repository object proves state resumes from disk
    fresh_repository = _repository(root)
    sources = _write_blind_result(root, blind.blind.blind_packet)
    resumed = run_blind_resume_stage(
        fresh_repository,
        root,
        BlindResumeStageInputs(
            result_locator="inbox/blind-result.json",
            receipt_locator="receipts/blind.json",
            source_contents=sources,
            imported_at=BLIND_IMPORTED_AT,
            demand_requested_at=DEMAND_REQUESTED_AT,
            demand_valid_until=DEMAND_VALID_UNTIL,
            max_staleness_seconds=MAX_STALENESS,
            target_sizes=(Decimal("10"),),
        ),
    )
    assert resumed.state is not None and resumed.outbox_receipt is not None
    assert (root / DEMAND_OUTBOX_LOCATOR).is_file()
    outbox_lines = (root / DEMAND_OUTBOX_LOCATOR).read_text().splitlines()
    assert len(outbox_lines) == 1

    book_repository = _repository(root)
    book = run_book_stage(
        book_repository,
        root,
        _book_inputs(_load_demand(book_repository, resumed.state)),
    )
    assert book.state is not None and book.bridge.accepted
    assert (root / "outbox/market.packet.json").is_file()

    market_repository = _repository(root)
    submitted, market_sources = _market_submission(
        _load_market_packet(market_repository, book.state)
    )
    raw = json.loads(submitted)
    # bind the market estimate to the accepted blind candidate and set clocks
    stored = market_repository.get_contract(resumed.state.blind_result_id or "")
    assert stored is not None
    raw["probability_estimate"]["blind_candidate_id"] = stored["probability_estimate"][
        "blind_candidate_id"
    ]
    raw["completed_at"] = MARKET_IMPORTED_AT.isoformat()
    from src.polymarket_alpha.contracts import ResearchResultEnvelope, canonical_json

    market_bytes = canonical_json(ResearchResultEnvelope.model_validate(raw)).encode("utf-8")
    (root / "inbox").mkdir(exist_ok=True)
    (root / "inbox/market-result.json").write_bytes(market_bytes)

    final = run_market_resume_stage(
        market_repository,
        root,
        MarketResumeStageInputs(
            result_locator="inbox/market-result.json",
            receipt_locator="receipts/market.json",
            source_contents=market_sources,
            imported_at=MARKET_IMPORTED_AT,
            gate_b_evaluated_at=GATE_B_AT,
            decision_as_of=DECISION_AT,
            rank_config=RankConfig(
                version="op-coordinator-v1",
                watch_threshold=Decimal("0.05"),
                simulate_threshold=Decimal("0.10"),
            ),
        ),
    )
    assert final.state is not None and final.state.stage == OperationalStage.FINALIZED
    assert final.outcome.final is not None
    ranked = final.outcome.final.ranked
    assert ranked is not None
    assert ranked.decision.execution == "NO_ORDER"
    decision = market_repository.get_contract(final.state.decision_id or "")
    prediction = market_repository.get_contract(final.state.prediction_id or "")
    assert decision is not None and prediction is not None


def _load_demand(repository: AlphaRepository, state: CoordinatorState):
    from src.polymarket_alpha.contracts import BookCaptureDemand

    payload = repository.get_contract(state.demand_id or "")
    assert payload is not None
    return BookCaptureDemand.model_validate(payload)


def _load_market_packet(repository: AlphaRepository, state: CoordinatorState):
    from src.polymarket_alpha.contracts import MarketResearchPacket

    payload = repository.get_contract(state.market_packet_id or "")
    assert payload is not None
    return MarketResearchPacket.model_validate(payload)


def test_every_stage_replay_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path
    repository, _ingest, _scan, blind = _run_through_blind(root)
    sources = _write_blind_result(root, blind.blind.blind_packet)
    inputs = BlindResumeStageInputs(
        result_locator="inbox/blind-result.json",
        receipt_locator="receipts/blind.json",
        source_contents=sources,
        imported_at=BLIND_IMPORTED_AT,
        demand_requested_at=DEMAND_REQUESTED_AT,
        demand_valid_until=DEMAND_VALID_UNTIL,
        max_staleness_seconds=MAX_STALENESS,
        target_sizes=(Decimal("10"),),
    )
    first = run_blind_resume_stage(repository, root, inputs)
    second = run_blind_resume_stage(repository, root, inputs)
    assert first.state is not None and second.state is not None
    assert first.state.to_payload() == second.state.to_payload()
    assert second.outbox_receipt is not None and second.outbox_receipt.replayed
    assert len((root / DEMAND_OUTBOX_LOCATOR).read_text().splitlines()) == 1

    book_inputs = _book_inputs(_load_demand(repository, first.state))
    book_first = run_book_stage(repository, root, book_inputs)
    book_second = run_book_stage(repository, root, book_inputs)
    assert book_first.state is not None and book_second.state is not None
    assert book_first.state.to_payload() == book_second.state.to_payload()


def test_hash_tampered_state_manifest_blocks_resume(tmp_path: Path) -> None:
    root = tmp_path
    _repo, _ingest, _scan, blind = _run_through_blind(root)
    tampered_root = tmp_path / "tampered-state"
    (tampered_root / "state").mkdir(parents=True)
    payload = json.loads((root / "state/BLIND_PACKET_FROZEN.json").read_bytes())
    candidate_id = payload["candidate_id"]
    payload["hashes"][candidate_id] = "f" * 64
    (tampered_root / "state/BLIND_PACKET_FROZEN.json").write_text(json.dumps(payload))
    with pytest.raises(CoordinatorBlocked, match="hash does not match"):
        run_blind_resume_stage(
            _repository(root),
            tampered_root,
            BlindResumeStageInputs(
                result_locator="inbox/blind-result.json",
                receipt_locator="receipts/blind.json",
                source_contents=_write_blind_result(root, blind.blind.blind_packet),
                imported_at=BLIND_IMPORTED_AT,
                demand_requested_at=DEMAND_REQUESTED_AT,
                demand_valid_until=DEMAND_VALID_UNTIL,
                max_staleness_seconds=MAX_STALENESS,
                target_sizes=(Decimal("10"),),
            ),
        )


def test_quarantined_blind_result_never_writes_formal_review_demand(tmp_path: Path) -> None:
    root = tmp_path
    _repo, _ingest, _scan, blind = _run_through_blind(root)
    submitted, sources = _blind_submission(blind.blind.blind_packet)
    bad = json.loads(submitted)
    bad["packet_id"] = "blind_packet:" + "a" * 64
    from src.polymarket_alpha.contracts import ResearchResultEnvelope, canonical_json

    (root / "inbox").mkdir()
    (root / "inbox/blind-result.json").write_bytes(
        canonical_json(ResearchResultEnvelope.model_validate(bad)).encode("utf-8")
    )
    result = run_blind_resume_stage(
        _repository(root),
        root,
        BlindResumeStageInputs(
            result_locator="inbox/blind-result.json",
            receipt_locator="receipts/blind.json",
            source_contents=sources,
            imported_at=BLIND_IMPORTED_AT,
            demand_requested_at=DEMAND_REQUESTED_AT,
            demand_valid_until=DEMAND_VALID_UNTIL,
            max_staleness_seconds=MAX_STALENESS,
            target_sizes=(Decimal("10"),),
        ),
    )
    assert result.state is None and result.outbox_receipt is None
    assert not (root / DEMAND_OUTBOX_LOCATOR).exists()
    assert not (root / "state/BLIND_RESULT_ACCEPTED.json").exists()


def test_unpaired_and_expired_books_never_freeze_a_market_packet(tmp_path: Path) -> None:
    root = tmp_path
    repository, _ingest, _scan, blind = _run_through_blind(root)
    sources = _write_blind_result(root, blind.blind.blind_packet)
    resumed = run_blind_resume_stage(
        repository,
        root,
        BlindResumeStageInputs(
            result_locator="inbox/blind-result.json",
            receipt_locator="receipts/blind.json",
            source_contents=sources,
            imported_at=BLIND_IMPORTED_AT,
            demand_requested_at=DEMAND_REQUESTED_AT,
            demand_valid_until=DEMAND_VALID_UNTIL,
            max_staleness_seconds=MAX_STALENESS,
            target_sizes=(Decimal("10"),),
        ),
    )
    assert resumed.state is not None
    demand = _load_demand(repository, resumed.state)

    unpaired = _book_inputs(demand)
    unpaired = BookStageInputs(
        owner_receipt=unpaired.owner_receipt,
        yes_submission=unpaired.yes_submission,
        no_submission=None,
        received_at=unpaired.received_at,
        packet_created_at=unpaired.packet_created_at,
        handoff_created_at=unpaired.handoff_created_at,
    )
    missing = run_book_stage(repository, root, unpaired)
    assert missing.state is None and not missing.bridge.accepted

    expired = run_book_stage(
        repository,
        root,
        _book_inputs(demand, received_at=DEMAND_VALID_UNTIL + timedelta(seconds=1)),
    )
    assert expired.state is None and not expired.bridge.accepted
    assert not (root / "outbox/market.packet.json").exists()
    assert not (root / "state/MARKET_PACKET_FROZEN.json").exists()


def test_rule_binding_mismatch_blocks_the_market_resume(tmp_path: Path) -> None:
    root = tmp_path
    repository, _ingest, _scan, blind = _run_through_blind(root)
    sources = _write_blind_result(root, blind.blind.blind_packet)
    resumed = run_blind_resume_stage(
        repository,
        root,
        BlindResumeStageInputs(
            result_locator="inbox/blind-result.json",
            receipt_locator="receipts/blind.json",
            source_contents=sources,
            imported_at=BLIND_IMPORTED_AT,
            demand_requested_at=DEMAND_REQUESTED_AT,
            demand_valid_until=DEMAND_VALID_UNTIL,
            max_staleness_seconds=MAX_STALENESS,
            target_sizes=(Decimal("10"),),
        ),
    )
    assert resumed.state is not None
    book = run_book_stage(repository, root, _book_inputs(_load_demand(repository, resumed.state)))
    assert book.state is not None

    submitted, market_sources = _market_submission(
        _load_market_packet(repository, book.state)
    )
    mismatched = json.loads(submitted)
    mismatched["packet_id"] = "market_packet:" + "a" * 64  # result no longer binds the packet
    mismatched["packet_sha256"] = "a" * 64
    from src.polymarket_alpha.contracts import ResearchResultEnvelope, canonical_json

    (root / "inbox").mkdir(exist_ok=True)
    (root / "inbox/market-result.json").write_bytes(
        canonical_json(ResearchResultEnvelope.model_validate(mismatched)).encode("utf-8")
    )
    result = run_market_resume_stage(
        repository,
        root,
        MarketResumeStageInputs(
            result_locator="inbox/market-result.json",
            receipt_locator="receipts/market.json",
            source_contents=market_sources,
            imported_at=MARKET_IMPORTED_AT,
            gate_b_evaluated_at=GATE_B_AT,
            decision_as_of=DECISION_AT,
            rank_config=RankConfig(
                version="op-coordinator-v1",
                watch_threshold=Decimal("0.05"),
                simulate_threshold=Decimal("0.10"),
            ),
        ),
    )
    assert result.state is None and result.outcome.final is None
    assert not (root / "state/FINALIZED.json").exists()
    # a mismatched Market result must not leave any decision/prediction rows
    import sqlite3

    conn = sqlite3.connect(f"file:{root / 'alpha.db'}?mode=ro", uri=True)
    try:
        for record_type in ("ReviewDecision", "PredictionRecord"):
            count = conn.execute(
                "SELECT COUNT(*) FROM alpha_contract_record WHERE contract_type = ?",
                (record_type,),
            ).fetchone()[0]
            assert count == 0, record_type
    finally:
        conn.close()


def test_gate_b_block_branch_blocks_finalization_without_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path
    repository, _ingest, _scan, blind = _run_through_blind(root)
    sources = _write_blind_result(root, blind.blind.blind_packet)
    resumed = run_blind_resume_stage(
        repository,
        root,
        BlindResumeStageInputs(
            result_locator="inbox/blind-result.json",
            receipt_locator="receipts/blind.json",
            source_contents=sources,
            imported_at=BLIND_IMPORTED_AT,
            demand_requested_at=DEMAND_REQUESTED_AT,
            demand_valid_until=DEMAND_VALID_UNTIL,
            max_staleness_seconds=MAX_STALENESS,
            target_sizes=(Decimal("10"),),
        ),
    )
    assert resumed.state is not None
    assert run_book_stage(
        repository, root, _book_inputs(_load_demand(repository, resumed.state))
    ).state is not None

    from src.polymarket_alpha.pipeline import review as review_module
    from src.polymarket_alpha.operational import load_state
    from src.polymarket_alpha.rules import evaluate_gate_b

    real_gate_b = evaluate_gate_b

    def blocked_gate_b(*args, **kwargs):
        decision = real_gate_b(*args, **kwargs)
        return decision.model_copy(update={"decision": "BLOCK", "reasons": ("SEAM_TEST_BLOCK",)})

    monkeypatch.setattr(review_module, "evaluate_gate_b", blocked_gate_b)

    market_packet = _load_market_packet(
        repository, load_state(root, OperationalStage.MARKET_PACKET_FROZEN)
    )
    submitted, market_sources = _market_submission(market_packet)
    raw = json.loads(submitted)
    stored = repository.get_contract(resumed.state.blind_result_id or "")
    assert stored is not None
    raw["probability_estimate"]["blind_candidate_id"] = stored["probability_estimate"][
        "blind_candidate_id"
    ]
    raw["completed_at"] = MARKET_IMPORTED_AT.isoformat()
    from src.polymarket_alpha.contracts import ResearchResultEnvelope, canonical_json

    (root / "inbox").mkdir(exist_ok=True)
    (root / "inbox/market-result.json").write_bytes(
        canonical_json(ResearchResultEnvelope.model_validate(raw)).encode("utf-8")
    )
    result = run_market_resume_stage(
        repository,
        root,
        MarketResumeStageInputs(
            result_locator="inbox/market-result.json",
            receipt_locator="receipts/market.json",
            source_contents=market_sources,
            imported_at=MARKET_IMPORTED_AT,
            gate_b_evaluated_at=GATE_B_AT,
            decision_as_of=DECISION_AT,
            rank_config=RankConfig(
                version="op-coordinator-v1",
                watch_threshold=Decimal("0.05"),
                simulate_threshold=Decimal("0.10"),
            ),
        ),
    )
    assert result.outcome.final is not None and result.outcome.final.ranked is None
    assert result.state is None
    assert not (root / "state/FINALIZED.json").exists()


def test_book_stage_requires_the_announced_outbox_line(tmp_path: Path) -> None:
    root = tmp_path
    repository, _ingest, _scan, blind = _run_through_blind(root)
    sources = _write_blind_result(root, blind.blind.blind_packet)
    resumed = run_blind_resume_stage(
        repository,
        root,
        BlindResumeStageInputs(
            result_locator="inbox/blind-result.json",
            receipt_locator="receipts/blind.json",
            source_contents=sources,
            imported_at=BLIND_IMPORTED_AT,
            demand_requested_at=DEMAND_REQUESTED_AT,
            demand_valid_until=DEMAND_VALID_UNTIL,
            max_staleness_seconds=MAX_STALENESS,
            target_sizes=(Decimal("10"),),
        ),
    )
    assert resumed.state is not None
    (root / DEMAND_OUTBOX_LOCATOR).unlink()  # simulate an append that never landed
    with pytest.raises(CoordinatorBlocked, match="outbox"):
        run_book_stage(repository, root, _book_inputs(_load_demand(repository, resumed.state)))


def test_stages_require_their_predecessor_state(tmp_path: Path) -> None:
    root = tmp_path
    repository = _repository(root)
    with pytest.raises(CoordinatorBlocked, match="GAMMA_INGESTED"):
        run_scan_stage(repository, root, ScanStageInputs(run_id="early", as_of=SCAN_AS_OF))
    run_gamma_ingest_stage(repository, root, _gamma_inputs())
    with pytest.raises(CoordinatorBlocked, match="CANDIDATE_SCANNED"):
        run_blind_stage(
            repository,
            root,
            BlindStageInputs(
                rule_request=_coordinator_rule_request(),
                gate_evaluated_at=GATE_A_AT,
                packet_created_at=PACKET_AT,
                handoff_created_at=HANDOFF_AT,
            ),
        )


def test_wrong_rule_hash_is_blocked_at_the_blind_stage(tmp_path: Path) -> None:
    root = tmp_path
    repository = _repository(root)
    run_gamma_ingest_stage(repository, root, _gamma_inputs())
    run_scan_stage(repository, root, ScanStageInputs(run_id=RUN_ID + "-scan", as_of=SCAN_AS_OF))
    with pytest.raises(CoordinatorBlocked, match="rule hash"):
        run_blind_stage(
            repository,
            root,
            BlindStageInputs(
                rule_request=_coordinator_rule_request().model_copy(
                    update={"expected_rule_hash": "f" * 64}
                ),
                gate_evaluated_at=GATE_A_AT,
                packet_created_at=PACKET_AT,
                handoff_created_at=HANDOFF_AT,
            ),
        )


# --- CLI ---------------------------------------------------------------------


def _pilot_dir(name: str) -> Path:
    base = Path("/tmp/polymarket-alpha-pilot")
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{name}-{datetime.now().strftime('%H%M%S%f')}"
    path.mkdir(parents=True)
    return path


CLI_ROOT = Path(__file__).resolve().parents[2] / "scripts/ops/polymarket_alpha_operational_coordinator.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("op_coordinator_cli", CLI_ROOT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_refuses_proxy_env_and_production_paths(monkeypatch) -> None:
    cli = _load_cli()
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    with pytest.raises(SystemExit, match="proxy"):
        cli.refuse_unsafe_environment()
    for key in cli.PROXY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    cli.refuse_unsafe_environment()
    with pytest.raises(SystemExit, match="production"):
        cli.require_pilot_path("/Volumes/jrs/pm_agents/runtime", label="--artifact-root", must_exist=True)
    with pytest.raises(SystemExit, match="polymarket-alpha-pilot"):
        cli.require_pilot_path(str(Path("/Users/deepsleep/projects/pm_agents/tmp")), label="--artifact-root", must_exist=True)


def test_cli_rejects_symlink_escape_for_root_db_and_inputs() -> None:
    cli = _load_cli()
    pilot = _pilot_dir("cli-symlink-boundary")
    allowed_root = pilot.parent
    root_alias = allowed_root / f"{pilot.name}-root-alias"
    db_alias = pilot / "alpha.db"
    input_alias = pilot / "manifest-link.json"
    root_alias.symlink_to(Path("/tmp"))
    db_alias.symlink_to(Path("/tmp/outside-alpha.db"))
    input_alias.symlink_to(Path("/etc/hosts"))
    try:
        with pytest.raises(SystemExit, match="symlink"):
            cli.require_pilot_path(
                str(root_alias), label="--artifact-root", must_exist=True
            )
        with pytest.raises(SystemExit, match="symlink"):
            cli._require_alpha_db_path(str(db_alias), artifact_root=pilot.resolve())
        with pytest.raises(SystemExit, match="symlink"):
            cli._require_readable_input_path(str(input_alias), label="--manifest")
    finally:
        root_alias.unlink(missing_ok=True)
        shutil.rmtree(pilot)


def test_cli_runs_the_full_offline_fixture_path(monkeypatch, tmp_path: Path) -> None:
    cli = _load_cli()
    pilot = _pilot_dir("cli-e2e")
    try:
        manifests = pilot / "manifests"
        manifests.mkdir()
        for key in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy",
        ):
            monkeypatch.delenv(key, raising=False)

        inputs = _gamma_inputs()
        (pilot / "gamma-response.json").write_bytes(inputs.raw_response)
        ingest_manifest = {
            "response_path": str(pilot / "gamma-response.json"),
            "receipt": {
                "request_method": inputs.response_receipt.request_method,
                "endpoint_host": inputs.response_receipt.endpoint_host,
                "endpoint_path": inputs.response_receipt.endpoint_path,
                "http_status": inputs.response_receipt.http_status,
                "response_bytes_sha256": inputs.response_receipt.response_bytes_sha256,
                "response_byte_length": inputs.response_receipt.response_byte_length,
                "response_received_at": inputs.response_receipt.response_received_at.isoformat(),
            },
            "run_id": RUN_ID,
            "observed_at": GAMMA_OBSERVED.isoformat(),
            "ingested_at": INGESTED.isoformat(),
            "page_budget": 5,
            "requested_limit": 5,
        }
        (manifests / "ingest.json").write_text(json.dumps(ingest_manifest))
        assert cli.main([
            "--artifact-root", str(pilot),
            "--alpha-db", str(pilot / "alpha.db"),
            "ingest-gamma", "--manifest", str(manifests / "ingest.json"),
        ]) == 0

        (manifests / "scan.json").write_text(
            json.dumps({"run_id": RUN_ID + "-scan", "as_of": SCAN_AS_OF.isoformat()})
        )
        assert cli.main([
            "--artifact-root", str(pilot),
            "--alpha-db", str(pilot / "alpha.db"),
            "scan", "--manifest", str(manifests / "scan.json"),
        ]) == 0

        rule_payload = _coordinator_rule_request().model_dump(mode="json")
        (manifests / "blind.json").write_text(
            json.dumps(
                {
                    "rule_request": rule_payload,
                    "gate_evaluated_at": GATE_A_AT.isoformat(),
                    "packet_created_at": PACKET_AT.isoformat(),
                    "handoff_created_at": HANDOFF_AT.isoformat(),
                }
            )
        )
        assert cli.main([
            "--artifact-root", str(pilot),
            "--alpha-db", str(pilot / "alpha.db"),
            "blind", "--manifest", str(manifests / "blind.json"),
        ]) == 0

        repository = AlphaRepository(pilot / "alpha.db")
        repository.migrate()
        state = cli_state(pilot, OperationalStage.BLIND_PACKET_FROZEN)
        packet_payload = repository.get_contract(state.blind_packet_id or "")
        assert packet_payload is not None
        from src.polymarket_alpha.contracts import BlindResearchPacket

        packet = BlindResearchPacket.model_validate(packet_payload)
        submitted, sources = _blind_submission(packet)
        (pilot / "inbox").mkdir()
        (pilot / "inbox/blind-result.json").write_bytes(submitted)
        source_manifest = {
            name: _write_source(pilot, name, content) for name, content in sources.items()
        }
        (manifests / "blind-resume.json").write_text(
            json.dumps(
                {
                    "result_locator": "inbox/blind-result.json",
                    "receipt_locator": "receipts/blind.json",
                    "source_contents": source_manifest,
                    "imported_at": BLIND_IMPORTED_AT.isoformat(),
                    "demand_requested_at": DEMAND_REQUESTED_AT.isoformat(),
                    "demand_valid_until": DEMAND_VALID_UNTIL.isoformat(),
                    "max_staleness_seconds": MAX_STALENESS,
                    "target_sizes": ["10"],
                }
            )
        )
        assert cli.main([
            "--artifact-root", str(pilot),
            "--alpha-db", str(pilot / "alpha.db"),
            "blind-resume", "--manifest", str(manifests / "blind-resume.json"),
        ]) == 0
        assert (pilot / DEMAND_OUTBOX_LOCATOR).is_file()

        # book stage via CLI manifest with raw book bytes on disk
        from src.polymarket_alpha.operational import load_state

        accepted_state = load_state(pilot, OperationalStage.BLIND_RESULT_ACCEPTED)
        demand_payload = repository.get_contract(accepted_state.demand_id or "")
        assert demand_payload is not None
        identity = demand_payload["identity"]
        books_dir = pilot / "books"
        books_dir.mkdir(exist_ok=True)

        def _cli_leg(token_id: str, side: str) -> dict:
            raw_book = {
                "timestamp": str(int(BOOK_RESPONSE_AT.timestamp() * 1000)),
                "hash": "exchange-hash",
                "bids": [{"price": "0.40", "size": "25"}],
                "asks": [{"price": "0.50", "size": "20"}, {"price": "0.55", "size": "100"}],
            }
            raw_path = books_dir / f"{side}.json"
            raw_path.write_text(json.dumps(raw_book, sort_keys=True))
            capture = materialize_orderbook_capture(
                token_id=token_id,
                raw_book=raw_book,
                request_started_at_utc=(
                    BOOK_RESPONSE_AT - timedelta(milliseconds=100)
                )
                .isoformat()
                .replace("+00:00", "Z"),
                response_received_at_utc=BOOK_RESPONSE_AT.isoformat().replace("+00:00", "Z"),
                parsed_at_utc=(BOOK_RESPONSE_AT + timedelta(milliseconds=1))
                .isoformat()
                .replace("+00:00", "Z"),
                request_batch_capture_id="cli-batch",
            )
            return {
                "locator": f"books/{side}.json",
                "raw_book_path": str(raw_path),
                "capture": capture,
                "raw_artifact_id": f"owner_book_artifact:{capture['book_capture_id']}",
            }

        yes_leg = _cli_leg(identity["yes_token_id"], "yes")
        no_leg = _cli_leg(identity["no_token_id"], "no")
        (manifests / "book.json").write_text(
            json.dumps(
                {
                    "owner_receipt": {
                        "capture_owner": "weather_market_books",
                        "alpha_demand_id": demand_payload["demand_id"],
                        "condition_id": identity["condition_id"],
                        "yes_token_id": identity["yes_token_id"],
                        "no_token_id": identity["no_token_id"],
                        "request_batch_capture_id": "cli-batch",
                        "yes_book_capture_id": yes_leg["capture"]["book_capture_id"],
                        "no_book_capture_id": no_leg["capture"]["book_capture_id"],
                    },
                    "yes_leg": yes_leg,
                    "no_leg": no_leg,
                    "received_at": BOOK_RECEIVED_AT.isoformat(),
                    "packet_created_at": MARKET_PACKET_AT.isoformat(),
                    "handoff_created_at": MARKET_HANDOFF_AT.isoformat(),
                }
            )
        )
        assert cli.main([
            "--artifact-root", str(pilot),
            "--alpha-db", str(pilot / "alpha.db"),
            "book", "--manifest", str(manifests / "book.json"),
        ]) == 0
        assert (pilot / "outbox/market.packet.json").is_file()

        # market-resume stage via CLI manifest
        market_state = load_state(pilot, OperationalStage.MARKET_PACKET_FROZEN)
        market_packet_payload = repository.get_contract(market_state.market_packet_id or "")
        assert market_packet_payload is not None
        from src.polymarket_alpha.contracts import (
            MarketResearchPacket,
            ResearchResultEnvelope,
            canonical_json,
        )

        market_packet = MarketResearchPacket.model_validate(market_packet_payload)
        market_submitted, market_sources = _market_submission(market_packet)
        market_raw = json.loads(market_submitted)
        blind_stored = repository.get_contract(accepted_state.blind_result_id or "")
        assert blind_stored is not None
        market_raw["probability_estimate"]["blind_candidate_id"] = blind_stored[
            "probability_estimate"
        ]["blind_candidate_id"]
        market_raw["completed_at"] = MARKET_IMPORTED_AT.isoformat()
        market_bytes = canonical_json(
            ResearchResultEnvelope.model_validate(market_raw)
        ).encode("utf-8")
        (pilot / "inbox").mkdir(exist_ok=True)
        (pilot / "inbox/market-result.json").write_bytes(market_bytes)
        market_source_manifest = {
            name: _write_source(pilot, name, content)
            for name, content in market_sources.items()
        }
        (manifests / "market-resume.json").write_text(
            json.dumps(
                {
                    "result_locator": "inbox/market-result.json",
                    "receipt_locator": "receipts/market.json",
                    "source_contents": market_source_manifest,
                    "imported_at": MARKET_IMPORTED_AT.isoformat(),
                    "gate_b_evaluated_at": GATE_B_AT.isoformat(),
                    "decision_as_of": DECISION_AT.isoformat(),
                    "rank_config": {
                        "version": "op-coordinator-v1",
                        "watch_threshold": "0.05",
                        "simulate_threshold": "0.10",
                    },
                }
            )
        )
        assert cli.main([
            "--artifact-root", str(pilot),
            "--alpha-db", str(pilot / "alpha.db"),
            "market-resume", "--manifest", str(manifests / "market-resume.json"),
        ]) == 0
        assert (pilot / "state/FINALIZED.json").is_file()
    finally:
        shutil.rmtree(pilot, ignore_errors=True)


def _write_source(pilot: Path, name: str, content: bytes) -> str:
    sources = pilot / "sources"
    sources.mkdir(exist_ok=True)
    path = sources / (name.replace(":", "_") + ".txt")
    path.write_bytes(content)
    return str(path)


def cli_state(pilot: Path, stage: OperationalStage) -> CoordinatorState:
    from src.polymarket_alpha.operational import load_state

    return load_state(pilot, stage)
