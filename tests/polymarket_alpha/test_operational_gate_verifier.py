from __future__ import annotations

import ast
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from src.polymarket_alpha.books import build_owner_capture_demands
from src.polymarket_alpha.contracts import (
    BookCaptureDemand,
    BookCapturePurpose,
    BookCaptureReceipt,
    BookCaptureStatus,
    BookLeg,
    BookLevel,
    MarketIdentity,
    MarketResearchPacket,
    OrderbookSnapshot,
    content_sha256,
    stable_record_id,
)
from src.polymarket_alpha.governance.operational_gate import (
    REQUIRED_SECURITY_CANARIES,
    FormalBookEvidence,
    FrozenRawArtifact,
    GammaReadEvidence,
    NoOrderLineageEvidence,
    OperationalGateDisposition,
    OperationalGateFailure,
    OperationalPilotGateEvidence,
    SecurityCanaryEvidence,
    verify_operational_pilot_gate,
)
from src.polymarket_alpha.pilot import (
    OwnerHealthObservation,
    PilotBudgetEvent,
    BudgetEventKind,
    authorize_operational_preflight,
    compare_weather_isolation,
    validate_rollback_rehearsal,
)
from src.polymarket_alpha.pilot.offline import PILOT_NOW, run_offline_fixture_pilot
from src.polymarket_alpha.pilot.operational import _prepare_operational_preflight
from src.polymarket_alpha.rules import RuleGateDecision, RuleGateStage
from src.polymarket_alpha.security import (
    AlphaReadOnlyTransport,
    HttpMethod,
    ProxyConnectionObservation,
    TransportMode,
    TransportRequest,
    build_mac_local_market_proxy_profile,
    seal_proxy_security_receipt,
)
from src.polymarket_alpha.storage import AlphaRepository


AT = PILOT_NOW


def _envelope(record_id: str, at=AT) -> dict[str, object]:
    return {
        "record_id": record_id,
        "run_id": "operational-gate-fixture",
        "created_at": at,
        "source": "operational_gate_fixture",
        "source_version": "v1",
        "provenance": (),
        "extensions": {},
    }


def _identity(number: int) -> MarketIdentity:
    return MarketIdentity(
        event_id="pilot-event" if number == 1 else f"event-{number}",
        market_id="pilot-market" if number == 1 else f"market-{number}",
        condition_id="pilot-condition" if number == 1 else f"condition-{number}",
        yes_token_id=f"yes-{number}" if number != 1 else "pilot-yes",
        no_token_id=f"no-{number}" if number != 1 else "pilot-no",
    )


def _synthetic_book(identity: MarketIdentity, number: int) -> FormalBookEvidence:
    blind_id = stable_record_id("research_result", identity.market_id, "blind")
    demand_id = stable_record_id("book_demand", identity.market_id, "formal")
    demand = BookCaptureDemand(
        **_envelope(demand_id, AT + timedelta(minutes=7)),
        demand_id=demand_id,
        identity=identity,
        purpose=BookCapturePurpose.FORMAL_REVIEW,
        trigger_artifact_id=blind_id,
        trigger_artifact_sha256=sha256(blind_id.encode()).hexdigest(),
        blind_result_id=blind_id,
        requested_at=AT + timedelta(minutes=7),
        valid_until=AT + timedelta(minutes=12),
        max_staleness_seconds=120,
        target_sizes=(Decimal("10"),),
    )
    observed = AT + timedelta(minutes=7, seconds=20)
    snapshot_id = stable_record_id("orderbook_snapshot", identity.market_id, number)
    snapshot = OrderbookSnapshot(
        **_envelope(snapshot_id, observed),
        identity=identity,
        capture_group_id=f"capture-group-{number}",
        captured_at=observed,
        source_observed_at=observed,
        yes_leg=BookLeg(
            token_id=identity.yes_token_id,
            bids=(BookLevel(price=Decimal("0.40"), size=Decimal("30")),),
            asks=(BookLevel(price=Decimal("0.50"), size=Decimal("30")),),
        ),
        no_leg=BookLeg(
            token_id=identity.no_token_id,
            bids=(BookLevel(price=Decimal("0.50"), size=Decimal("30")),),
            asks=(BookLevel(price=Decimal("0.60"), size=Decimal("30")),),
        ),
        stale=False,
        raw_artifact_ids=(f"raw-yes-{number}", f"raw-no-{number}"),
    )
    receipt_id = stable_record_id("book_receipt", demand_id, snapshot_id)
    receipt = BookCaptureReceipt(
        **_envelope(receipt_id, AT + timedelta(minutes=8)),
        receipt_id=receipt_id,
        demand_id=demand_id,
        demand_sha256=demand.canonical_sha256,
        market_id=identity.market_id,
        purpose=BookCapturePurpose.FORMAL_REVIEW,
        status=BookCaptureStatus.ACCEPTED,
        capture_owner="weather_market_books",
        received_at=AT + timedelta(minutes=8),
        orderbook_snapshot_id=snapshot.record_id,
        orderbook_snapshot_sha256=snapshot.canonical_sha256,
        capture_group_id=snapshot.capture_group_id,
        source_observed_at=observed,
    )
    owner = tuple(row.to_dict() for row in build_owner_capture_demands(demand).owner_demands)
    return FormalBookEvidence(
        demand=demand,
        owner_demands=owner,
        outbox_bundle_sha256=content_sha256(owner),
        receipt=receipt,
        snapshot=snapshot,
    )


def _load_offline_lineage(tmp_path, identity: MarketIdentity) -> tuple[FormalBookEvidence, NoOrderLineageEvidence]:
    repository = AlphaRepository(tmp_path / "alpha.db")
    result = run_offline_fixture_pilot(repository)
    demand = BookCaptureDemand.model_validate(
        repository.get_contract(next(item for item in result.persisted_record_ids if item.startswith("book_demand:")))
    )
    snapshot = OrderbookSnapshot.model_validate(repository.get_contract(result.ranked.prediction.orderbook_snapshot_id))
    receipt = BookCaptureReceipt.model_validate(
        repository.get_contract(next(item for item in result.persisted_record_ids if item.startswith("book_receipt:")))
    )
    packet = MarketResearchPacket.model_validate(repository.get_contract(result.market_packet_id))
    gate_b = next(
        RuleGateDecision.model_validate(repository.get_contract(item))
        for item in result.persisted_record_ids
        if item.startswith("rule_gate_decision:")
        and RuleGateDecision.model_validate(repository.get_contract(item)).stage == RuleGateStage.B
    )
    assert demand.identity == identity
    owner = tuple(row.to_dict() for row in build_owner_capture_demands(demand).owner_demands)
    return (
        FormalBookEvidence(
            demand=demand,
            owner_demands=owner,
            outbox_bundle_sha256=content_sha256(owner),
            receipt=receipt,
            snapshot=snapshot,
        ),
        NoOrderLineageEvidence(
            market_packet=packet,
            gate_b=gate_b,
            decision=result.ranked.decision,
            prediction=result.ranked.prediction,
        ),
    )


def _health(at, *, alpha: int, weather: int, errors: int = 1, latency: int = 100) -> OwnerHealthObservation:
    return OwnerHealthObservation(
        release_sha256="a" * 64,
        process_identity_sha256="b" * 64,
        config_sha256="c" * 64,
        alpha_demands=alpha,
        weather_demands=weather,
        errors=errors,
        p95_latency_ms=latency,
        observed_at=at,
    )


def _evidence(tmp_path) -> OperationalPilotGateEvidence:
    identities = (_identity(1), _identity(2), _identity(3))
    preflight = _prepare_operational_preflight(
        fixture_identities=tuple((f"fixture-{index}", identity) for index, identity in enumerate(identities, 1)),
        artifact_root="/tmp/polymarket-alpha-pilot/gate-verifier-fixture",
        prepared_at=AT,
    )
    authorization = authorize_operational_preflight(
        preflight,
        owner_authorization_id="owner-operational-gate-fixture",
        authorized_at=AT,
        expires_at=AT + timedelta(minutes=20),
    )
    requested = AT + timedelta(seconds=1)
    request = TransportRequest(
        method=HttpMethod.GET,
        url="https://gamma-api.polymarket.com/events?closed=false&limit=3&offset=0",
        headers={"Accept": "application/json"},
        requested_at=requested,
    )
    target = AlphaReadOnlyTransport(TransportMode.READ_ONLY, preflight.endpoint_policy).authorize(
        request, environment={}
    )
    profile = build_mac_local_market_proxy_profile(created_at=AT)
    proxy = seal_proxy_security_receipt(
        authorization=target,
        profile=profile,
        observation=ProxyConnectionObservation(
            proxy_profile_id=profile.profile_id,
            proxy_profile_sha256=profile.canonical_sha256,
            proxy_dns_answers=(profile.proxy_host,),
            proxy_connected_ip=profile.proxy_host,
            connect_authority="gamma-api.polymarket.com:443",
            connect_status=200,
            target_host="gamma-api.polymarket.com",
            target_port=443,
            target_resolution="PROXY",
            tls_server_name="gamma-api.polymarket.com",
            target_status_code=200,
            completed_at=AT + timedelta(seconds=2),
        ),
    )
    raw = (
        '[{"id":"event-fixture-1","markets":['
        + ",".join('{"id":"' + item.market_id + '"}' for item in identities)
        + ']},{"id":"event-fixture-2","markets":[]},'
        + '{"id":"event-fixture-3","markets":[]}]'
    ).encode()
    raw_hash = sha256(raw).hexdigest()
    first_book, lineage = _load_offline_lineage(tmp_path, identities[0])
    books = (first_book, _synthetic_book(identities[1], 2), _synthetic_book(identities[2], 3))
    events = tuple(
        PilotBudgetEvent(
            event_id=stable_record_id("budget_event", "market", item.market_id),
            kind=BudgetEventKind.MARKET,
            occurred_at=AT + timedelta(seconds=3),
            market_id=item.market_id,
        )
        for item in identities
    ) + (
        PilotBudgetEvent(
            event_id=stable_record_id("budget_event", "network"),
            kind=BudgetEventKind.NETWORK_REQUEST,
            occurred_at=AT + timedelta(seconds=1),
            request_sha256=target.authorized_request.request_sha256,
        ),
        *tuple(
            PilotBudgetEvent(
                event_id=stable_record_id("budget_event", "book", item.demand.demand_id),
                kind=BudgetEventKind.BOOK_DEMAND,
                occurred_at=AT + timedelta(minutes=7, seconds=index),
                market_id=item.demand.identity.market_id,
                request_sha256=item.demand.canonical_sha256,
                token_count=2,
            )
            for index, item in enumerate(books)
        ),
        PilotBudgetEvent(
            event_id=stable_record_id("budget_event", "artifact"),
            kind=BudgetEventKind.ARTIFACT_WRITE,
            occurred_at=AT + timedelta(minutes=8),
            artifact_bytes=len(raw),
        ),
    )
    before = _health(AT + timedelta(minutes=6), alpha=0, weather=100)
    during = _health(AT + timedelta(minutes=8), alpha=3, weather=103)
    after = _health(AT + timedelta(minutes=10), alpha=3, weather=106)
    weather_receipt = compare_weather_isolation(
        before, during, after, max_error_delta=0, max_p95_latency_regression_ms=0
    )
    rollback_before = _health(AT + timedelta(minutes=12), alpha=3, weather=108)
    rollback_after = _health(AT + timedelta(minutes=13), alpha=3, weather=109)
    rollback = validate_rollback_rehearsal(
        owner_before=rollback_before,
        owner_after=rollback_after,
        alpha_demand_enabled_before=True,
        alpha_demand_enabled_after=False,
        owner_restart_count=0,
        production_config_write_count=0,
    )
    return OperationalPilotGateEvidence(
        preflight_manifest=preflight.manifest,
        endpoint_policy=preflight.endpoint_policy,
        authorization=authorization,
        gamma=GammaReadEvidence(
            authorization_id=authorization.authorization_id,
            preflight_manifest_id=preflight.manifest.record_id,
            preflight_manifest_sha256=preflight.manifest.canonical_sha256,
            policy_sha256=preflight.endpoint_policy.canonical_sha256,
            request_sha256=target.authorized_request.request_sha256,
            requested_at=requested,
            completed_at=AT + timedelta(seconds=2),
            host="gamma-api.polymarket.com",
            path="/events",
            method="GET",
            http_status=200,
            redirect_count=0,
            auth_headers_present=False,
            proxy_environment_cleared=True,
            connection_mode="EXPLICIT_PROXY",
            event_count=3,
            selected_market_ids=tuple(sorted(item.market_id for item in identities)),
            raw_bytes_sha256=raw_hash,
            raw_bytes_length=len(raw),
            proxy_security_receipt_sha256=content_sha256(proxy),
        ),
        raw_gamma=FrozenRawArtifact(
            locator="artifact://gamma-fixture",
            content=raw,
            declared_sha256=raw_hash,
            declared_length=len(raw),
        ),
        proxy_profile=profile,
        proxy_security_receipt=proxy,
        selected_markets=identities,
        books=books,
        budget_events=events,
        weather_before=before,
        weather_during=during,
        weather_after=after,
        weather_receipt=weather_receipt,
        max_error_delta=0,
        max_p95_latency_regression_ms=0,
        rollback_before=rollback_before,
        rollback_after=rollback_after,
        rollback_receipt=rollback,
        security_canaries=tuple(
            SecurityCanaryEvidence(
                canary_id=name,
                denied=True,
                receipt_payload={
                    "canary_id": name,
                    "capability": name,
                    "decision": "DENY",
                    "authorized": False,
                    "execution": "NO_ORDER",
                },
                receipt_sha256=content_sha256(
                    {
                        "canary_id": name,
                        "capability": name,
                        "decision": "DENY",
                        "authorized": False,
                        "execution": "NO_ORDER",
                    }
                ),
            )
            for name in sorted(REQUIRED_SECURITY_CANARIES)
        ),
        no_order_lineage=lineage,
    )


def test_complete_evidence_passes_and_replays_deterministically(tmp_path) -> None:
    evidence = _evidence(tmp_path)
    first = verify_operational_pilot_gate(
        evidence, verified_at=AT + timedelta(minutes=14), run_id="gate-run"
    )
    second = verify_operational_pilot_gate(
        evidence, verified_at=AT + timedelta(minutes=14), run_id="gate-run"
    )
    assert first == second
    assert first.disposition == OperationalGateDisposition.PASS
    assert first.failure_codes == ()
    assert first.execution == "NO_ORDER"


@pytest.mark.parametrize(
    ("mutator", "failure"),
    [
        (
            lambda item: item.model_copy(
                update={
                    "gamma": item.gamma.model_copy(update={"policy_sha256": "f" * 64})
                }
            ),
            OperationalGateFailure.POLICY_BINDING,
        ),
        (
            lambda item: item.model_copy(update={"books": item.books[:-1]}),
            OperationalGateFailure.BOOK_COVERAGE,
        ),
        (
            lambda item: item.model_copy(
                update={"security_canaries": item.security_canaries[:-1]}
            ),
            OperationalGateFailure.SECURITY_CANARY,
        ),
        (
            lambda item: item.model_copy(
                update={
                    "weather_receipt": item.weather_receipt.model_copy(
                        update={"passed": False}
                    )
                }
            ),
            OperationalGateFailure.WEATHER_ISOLATION,
        ),
        (
            lambda item: item.model_copy(
                update={
                    "rollback_receipt": item.rollback_receipt.model_copy(
                        update={"owner_restart_count": 1, "passed": False}
                    )
                }
            ),
            OperationalGateFailure.ROLLBACK,
        ),
    ],
)
def test_tampered_or_incomplete_evidence_returns_rework(tmp_path, mutator, failure) -> None:
    certificate = verify_operational_pilot_gate(
        mutator(_evidence(tmp_path)),
        verified_at=AT + timedelta(minutes=14),
        run_id="gate-run",
    )
    assert certificate.disposition == OperationalGateDisposition.REWORK
    assert failure in certificate.failure_codes


def test_budget_overrun_and_no_order_lineage_tamper_fail_closed(tmp_path) -> None:
    evidence = _evidence(tmp_path)
    extra = PilotBudgetEvent(
        event_id=stable_record_id("budget_event", "late"),
        kind=BudgetEventKind.NETWORK_REQUEST,
        occurred_at=AT + timedelta(minutes=31),
        request_sha256="d" * 64,
    )
    budget_failure = verify_operational_pilot_gate(
        evidence.model_copy(update={"budget_events": (*evidence.budget_events, extra)}),
        verified_at=AT + timedelta(minutes=32),
        run_id="gate-run-budget",
    )
    assert OperationalGateFailure.BUDGET in budget_failure.failure_codes

    bad_prediction = evidence.no_order_lineage.prediction.model_copy(
        update={"decision_id": "wrong-decision"}
    )
    lineage_failure = verify_operational_pilot_gate(
        evidence.model_copy(
            update={
                "no_order_lineage": evidence.no_order_lineage.model_copy(
                    update={"prediction": bad_prediction}
                )
            }
        ),
        verified_at=AT + timedelta(minutes=14),
        run_id="gate-run-lineage",
    )
    assert OperationalGateFailure.NO_ORDER_LINEAGE in lineage_failure.failure_codes


def test_selected_market_summary_cannot_replace_raw_gamma_lineage(tmp_path) -> None:
    evidence = _evidence(tmp_path)
    missing = b'[{"id":"event-fixture","markets":[]},{"id":"two","markets":[]},{"id":"three","markets":[]}]'
    raw = FrozenRawArtifact(
        locator="artifact://gamma-missing-markets",
        content=missing,
        declared_sha256=sha256(missing).hexdigest(),
        declared_length=len(missing),
    )
    gamma = evidence.gamma.model_copy(
        update={
            "raw_bytes_sha256": raw.declared_sha256,
            "raw_bytes_length": raw.declared_length,
        }
    )
    certificate = verify_operational_pilot_gate(
        evidence.model_copy(update={"raw_gamma": raw, "gamma": gamma}),
        verified_at=AT + timedelta(minutes=14),
        run_id="gate-run-raw",
    )
    assert OperationalGateFailure.GAMMA_RAW_ARTIFACT in certificate.failure_codes


def test_verifier_has_no_io_or_process_capability_imports() -> None:
    source = Path(__file__).parents[2] / "src/polymarket_alpha/governance/operational_gate.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imports & {
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "websocket",
        "subprocess",
        "importlib",
        "pathlib",
        "os",
    }


def test_model_copy_cannot_bypass_raw_or_literal_validation(tmp_path) -> None:
    evidence = _evidence(tmp_path)
    tampered_content = evidence.raw_gamma.content.replace(b"fixture-1", b"fixture-X")
    assert len(tampered_content) == len(evidence.raw_gamma.content)
    raw_tamper = evidence.raw_gamma.model_copy(update={"content": tampered_content})
    raw_certificate = verify_operational_pilot_gate(
        evidence.model_copy(update={"raw_gamma": raw_tamper}),
        verified_at=AT + timedelta(minutes=14),
        run_id="gate-run-model-copy-raw",
    )
    assert OperationalGateFailure.GAMMA_RAW_ARTIFACT in raw_certificate.failure_codes
    assert OperationalGateFailure.INPUT_SCHEMA_INVALID in raw_certificate.failure_codes

    gamma_tamper = evidence.gamma.model_copy(update={"http_status": 201})
    gamma_certificate = verify_operational_pilot_gate(
        evidence.model_copy(update={"gamma": gamma_tamper}),
        verified_at=AT + timedelta(minutes=14),
        run_id="gate-run-model-copy-gamma",
    )
    assert gamma_certificate.disposition == OperationalGateDisposition.REWORK
    assert OperationalGateFailure.INPUT_SCHEMA_INVALID in gamma_certificate.failure_codes


def test_budget_canary_and_snapshot_summaries_are_cross_bound(tmp_path) -> None:
    evidence = _evidence(tmp_path)
    market_index = next(
        index
        for index, item in enumerate(evidence.budget_events)
        if item.kind == BudgetEventKind.MARKET
    )
    forged_market = evidence.budget_events[market_index].model_copy(
        update={"market_id": "unselected-market"}
    )
    forged_events = list(evidence.budget_events)
    forged_events[market_index] = forged_market
    budget_certificate = verify_operational_pilot_gate(
        evidence.model_copy(update={"budget_events": tuple(forged_events)}),
        verified_at=AT + timedelta(minutes=14),
        run_id="gate-run-budget-binding",
    )
    assert OperationalGateFailure.BUDGET in budget_certificate.failure_codes

    first_canary = evidence.security_canaries[0]
    forged_canary = first_canary.model_copy(
        update={
            "receipt_payload": {
                **first_canary.receipt_payload,
                "decision": "ALLOW",
            }
        }
    )
    canaries = (forged_canary, *evidence.security_canaries[1:])
    canary_certificate = verify_operational_pilot_gate(
        evidence.model_copy(update={"security_canaries": canaries}),
        verified_at=AT + timedelta(minutes=14),
        run_id="gate-run-canary-binding",
    )
    assert OperationalGateFailure.SECURITY_CANARY in canary_certificate.failure_codes

    packet = evidence.no_order_lineage.market_packet
    altered_book = packet.orderbook.model_copy(update={"stale": True})
    altered_packet = packet.model_copy(update={"orderbook": altered_book})
    altered_lineage = evidence.no_order_lineage.model_copy(
        update={"market_packet": altered_packet}
    )
    lineage_certificate = verify_operational_pilot_gate(
        evidence.model_copy(update={"no_order_lineage": altered_lineage}),
        verified_at=AT + timedelta(minutes=14),
        run_id="gate-run-snapshot-binding",
    )
    assert OperationalGateFailure.NO_ORDER_LINEAGE in lineage_certificate.failure_codes
