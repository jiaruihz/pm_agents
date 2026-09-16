"""Pure final-gate verification for a read-only Alpha operational pilot.

This module does not read files, open sockets, schedule work, or persist a
certificate.  A coordinator supplies already-frozen receipts and bytes; the
verifier replays the released budget/isolation/rollback rules and checks the
cross-domain lineage before returning a hash-bound PASS or REWORK certificate.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from src.platform.market_data.capture_demand import CaptureDemand

from ..books import build_owner_capture_demands
from ..contracts import (
    ALPHA_CONTRACT_VERSION,
    AlphaContract,
    BookCaptureDemand,
    BookCapturePurpose,
    BookCaptureReceipt,
    BookCaptureStatus,
    CommonEnvelope,
    MarketIdentity,
    MarketResearchPacket,
    OrderbookSnapshot,
    PositionState,
    PredictionRecord,
    ReviewDecision,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256
from ..pilot.operational import (
    FIRST_PILOT_BUDGET,
    OperationalPilotAuthorization,
    OperationalPilotManifest,
    PilotGateStatus,
    OwnerHealthObservation,
    PilotBudgetEvent,
    PilotBudgetLedger,
    ReadOnlyPolicyArtifact,
    RollbackRehearsalReceipt,
    WeatherIsolationReceipt,
    compare_weather_isolation,
    validate_rollback_rehearsal,
)
from ..rules.models import RuleGateDecision, RuleGateStage
from ..security import ExplicitProxyProfile, ProxySecurityReceipt


CAPTURE_OWNER = "weather_market_books"
REQUIRED_SECURITY_CANARIES = frozenset(
    {
        "generic_http",
        "raw_socket",
        "websocket",
        "redirect",
        "ambient_proxy",
        "arbitrary_proxy",
        "encoded_path",
        "unexpected_body",
        "unexpected_header",
        "dynamic_import",
        "subprocess",
        "shell",
        "authorization_header",
        "private_key_material",
    }
)
CHECKED_GATES = (
    "AUTHORIZATION",
    "GAMMA_TRANSPORT",
    "SELECTED_MARKET_IDENTITY",
    "PAIRED_OWNER_BOOKS",
    "BUDGET",
    "WEATHER_ISOLATION",
    "SECURITY_CANARIES",
    "ROLLBACK",
    "NO_ORDER_LINEAGE",
)


class OperationalGateDisposition(StrEnum):
    PASS = "READ_ONLY_OPERATIONAL_PILOT_READY"
    REWORK = "REWORK_OPERATIONAL_BOUNDARY"


class OperationalGateFailure(StrEnum):
    INPUT_SCHEMA_INVALID = "INPUT_SCHEMA_INVALID"
    AUTHORIZATION_BINDING = "AUTHORIZATION_BINDING"
    AUTHORIZATION_WINDOW = "AUTHORIZATION_WINDOW"
    POLICY_BINDING = "POLICY_BINDING"
    GAMMA_RECEIPT = "GAMMA_RECEIPT"
    GAMMA_RAW_ARTIFACT = "GAMMA_RAW_ARTIFACT"
    PROXY_RECEIPT = "PROXY_RECEIPT"
    MARKET_SET = "MARKET_SET"
    BOOK_COVERAGE = "BOOK_COVERAGE"
    BOOK_LINEAGE = "BOOK_LINEAGE"
    BUDGET = "BUDGET"
    WEATHER_ISOLATION = "WEATHER_ISOLATION"
    SECURITY_CANARY = "SECURITY_CANARY"
    ROLLBACK = "ROLLBACK"
    NO_ORDER_LINEAGE = "NO_ORDER_LINEAGE"


class FrozenRawArtifact(AlphaContract):
    locator: str
    content: bytes
    declared_sha256: str
    declared_length: int = Field(ge=0)

    @field_validator("locator")
    @classmethod
    def locator_is_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("raw artifact locator must not be blank")
        return value

    @field_validator("declared_sha256")
    @classmethod
    def hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def bytes_match_declaration(self) -> "FrozenRawArtifact":
        from hashlib import sha256

        if len(self.content) != self.declared_length:
            raise ValueError("raw artifact length mismatch")
        if sha256(self.content).hexdigest() != self.declared_sha256:
            raise ValueError("raw artifact hash mismatch")
        return self


class GammaReadEvidence(AlphaContract):
    authorization_id: str
    preflight_manifest_id: str
    preflight_manifest_sha256: str
    policy_sha256: str
    request_sha256: str
    requested_at: datetime
    completed_at: datetime
    host: Literal["gamma-api.polymarket.com"]
    path: Literal["/events"]
    method: Literal["GET"]
    http_status: Literal[200]
    redirect_count: Literal[0]
    auth_headers_present: Literal[False]
    proxy_environment_cleared: Literal[True]
    connection_mode: Literal["EXPLICIT_PROXY"]
    event_count: int = Field(ge=1, le=5)
    selected_market_ids: tuple[str, ...]
    raw_bytes_sha256: str
    raw_bytes_length: int = Field(gt=0)
    proxy_security_receipt_sha256: str

    @field_validator(
        "preflight_manifest_sha256",
        "policy_sha256",
        "request_sha256",
        "raw_bytes_sha256",
        "proxy_security_receipt_sha256",
    )
    @classmethod
    def hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("requested_at", "completed_at")
    @classmethod
    def times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("selected_market_ids")
    @classmethod
    def market_ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not 3 <= len(value) <= 5 or value != tuple(sorted(set(value))):
            raise ValueError("Gamma evidence requires 3-5 sorted distinct selected markets")
        return value


class SecurityCanaryEvidence(AlphaContract):
    canary_id: str
    denied: bool
    receipt_payload: dict[str, Any]
    receipt_sha256: str

    @field_validator("receipt_sha256")
    @classmethod
    def receipt_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)


class FormalBookEvidence(AlphaContract):
    demand: BookCaptureDemand
    owner_demands: tuple[dict[str, Any], dict[str, Any]]
    outbox_bundle_sha256: str
    receipt: BookCaptureReceipt
    snapshot: OrderbookSnapshot

    @field_validator("outbox_bundle_sha256")
    @classmethod
    def outbox_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)


class NoOrderLineageEvidence(AlphaContract):
    market_packet: MarketResearchPacket
    gate_b: RuleGateDecision
    decision: ReviewDecision
    prediction: PredictionRecord


class OperationalPilotGateEvidence(AlphaContract):
    preflight_manifest: OperationalPilotManifest
    endpoint_policy: ReadOnlyPolicyArtifact
    authorization: OperationalPilotAuthorization
    gamma: GammaReadEvidence
    raw_gamma: FrozenRawArtifact
    proxy_profile: ExplicitProxyProfile
    proxy_security_receipt: ProxySecurityReceipt
    selected_markets: tuple[MarketIdentity, ...]
    books: tuple[FormalBookEvidence, ...]
    budget_events: tuple[PilotBudgetEvent, ...]
    weather_before: OwnerHealthObservation
    weather_during: OwnerHealthObservation
    weather_after: OwnerHealthObservation
    weather_receipt: WeatherIsolationReceipt
    max_error_delta: int = Field(ge=0)
    max_p95_latency_regression_ms: int = Field(ge=0)
    rollback_before: OwnerHealthObservation
    rollback_after: OwnerHealthObservation
    rollback_receipt: RollbackRehearsalReceipt
    security_canaries: tuple[SecurityCanaryEvidence, ...]
    no_order_lineage: NoOrderLineageEvidence


class OperationalPilotGateCertificate(CommonEnvelope):
    certificate_version: Literal["alpha_operational_gate_v1"] = "alpha_operational_gate_v1"
    disposition: OperationalGateDisposition
    evidence_sha256: str
    checked_gate_ids: tuple[str, ...]
    failure_codes: tuple[OperationalGateFailure, ...]
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("evidence_sha256")
    @classmethod
    def evidence_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def disposition_matches_failures(self) -> "OperationalPilotGateCertificate":
        if (self.disposition == OperationalGateDisposition.PASS) == bool(self.failure_codes):
            raise ValueError("gate disposition and failure codes are inconsistent")
        return self


def _append(failures: set[OperationalGateFailure], code: OperationalGateFailure) -> None:
    failures.add(code)


def _verify_authorization(evidence: OperationalPilotGateEvidence, failures: set[OperationalGateFailure]) -> None:
    manifest = evidence.preflight_manifest
    authorization = evidence.authorization
    gamma = evidence.gamma
    if (
        authorization.preflight_manifest_id != manifest.record_id
        or authorization.preflight_manifest_sha256 != manifest.canonical_sha256
        or gamma.authorization_id != authorization.authorization_id
        or gamma.preflight_manifest_id != manifest.record_id
        or gamma.preflight_manifest_sha256 != manifest.canonical_sha256
    ):
        _append(failures, OperationalGateFailure.AUTHORIZATION_BINDING)
    if (
        manifest.gate_status != PilotGateStatus.PREPARED_NOT_AUTHORIZED
        or manifest.network_io_authorized
        or manifest.owner_authorization_id is not None
        or manifest.production_config_changes_authorized
        or manifest.execution_capability != "NO_ORDER"
        or not authorization.network_io_authorized
        or not authorization.owner_demand_authorized
        or authorization.production_config_changes_authorized
        or authorization.current_runtime_db_writes_authorized
        or authorization.execution_capability != "NO_ORDER"
    ):
        _append(failures, OperationalGateFailure.AUTHORIZATION_BINDING)
    if not (
        authorization.authorized_at <= gamma.requested_at <= gamma.completed_at <= authorization.expires_at
    ):
        _append(failures, OperationalGateFailure.AUTHORIZATION_WINDOW)
    if (
        manifest.endpoint_policy_sha256 != evidence.endpoint_policy.canonical_sha256
        or gamma.policy_sha256 != evidence.endpoint_policy.canonical_sha256
    ):
        _append(failures, OperationalGateFailure.POLICY_BINDING)


def _verify_gamma(evidence: OperationalPilotGateEvidence, failures: set[OperationalGateFailure]) -> None:
    gamma = evidence.gamma
    raw = evidence.raw_gamma
    proxy = evidence.proxy_security_receipt
    profile = evidence.proxy_profile
    actual_raw_sha256 = sha256(raw.content).hexdigest()
    if (
        actual_raw_sha256 != raw.declared_sha256
        or len(raw.content) != raw.declared_length
        or raw.declared_sha256 != gamma.raw_bytes_sha256
        or raw.declared_length != gamma.raw_bytes_length
    ):
        _append(failures, OperationalGateFailure.GAMMA_RAW_ARTIFACT)
    if (
        gamma.host != "gamma-api.polymarket.com"
        or gamma.path != "/events"
        or gamma.method != "GET"
        or gamma.http_status != 200
        or gamma.redirect_count != 0
        or gamma.auth_headers_present
        or not gamma.proxy_environment_cleared
        or gamma.connection_mode != "EXPLICIT_PROXY"
    ):
        _append(failures, OperationalGateFailure.GAMMA_RECEIPT)
    try:
        payload = json.loads(raw.content)
        if not isinstance(payload, list) or len(payload) != gamma.event_count:
            raise ValueError
        observed_market_ids: set[str] = set()
        for event in payload:
            if (
                not isinstance(event, dict)
                or "markets" not in event
                or not isinstance(event["markets"], list)
            ):
                raise ValueError
            for market in event["markets"]:
                if not isinstance(market, dict):
                    raise ValueError
                market_id = market.get("id") or market.get("marketId")
                if isinstance(market_id, (str, int)) and str(market_id).strip():
                    observed_market_ids.add(str(market_id).strip())
        if not set(gamma.selected_market_ids) <= observed_market_ids:
            raise ValueError
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _append(failures, OperationalGateFailure.GAMMA_RAW_ARTIFACT)
    if (
        gamma.request_sha256 != proxy.request_sha256
        or gamma.proxy_security_receipt_sha256 != content_sha256(proxy)
        or proxy.policy_sha256 != gamma.policy_sha256
        or proxy.proxy_profile_id != profile.profile_id
        or proxy.proxy_profile_sha256 != profile.canonical_sha256
        or proxy.target_host != gamma.host
        or proxy.target_path != gamma.path
        or proxy.target_method.value != gamma.method
        or proxy.target_status_code != gamma.http_status
        or proxy.connect_authority != f"{gamma.host}:443"
        or proxy.tls_server_name != gamma.host
        or proxy.requested_at != gamma.requested_at
        or proxy.completed_at != gamma.completed_at
        or proxy.proxy_host != profile.proxy_host
        or proxy.proxy_port != profile.proxy_port
    ):
        _append(failures, OperationalGateFailure.PROXY_RECEIPT)


def _verify_market_set(evidence: OperationalPilotGateEvidence, failures: set[OperationalGateFailure]) -> None:
    selected = evidence.selected_markets
    market_ids = tuple(sorted(item.market_id for item in selected))
    if (
        not 3 <= len(selected) <= 5
        or len(set(market_ids)) != len(selected)
        or any(item.yes_token_id == item.no_token_id for item in selected)
        or market_ids != evidence.gamma.selected_market_ids
    ):
        _append(failures, OperationalGateFailure.MARKET_SET)
    preflight_ids = tuple(sorted(item[1] for item in evidence.preflight_manifest.fixture_identity_bindings))
    selected_hashes = tuple(
        sorted(
            content_sha256(item.model_dump(mode="json", exclude_none=False))
            for item in selected
        )
    )
    if (
        preflight_ids != market_ids
        or evidence.preflight_manifest.identity_sha256s != selected_hashes
    ):
        _append(failures, OperationalGateFailure.AUTHORIZATION_BINDING)


def _verify_books(evidence: OperationalPilotGateEvidence, failures: set[OperationalGateFailure]) -> None:
    selected = {item.market_id: item for item in evidence.selected_markets}
    if {item.demand.identity.market_id for item in evidence.books} != set(selected):
        _append(failures, OperationalGateFailure.BOOK_COVERAGE)
    for item in evidence.books:
        demand, receipt, snapshot = item.demand, item.receipt, item.snapshot
        try:
            actual_owner = tuple(CaptureDemand(**row).to_dict() for row in item.owner_demands)
            expected_owner = tuple(row.to_dict() for row in build_owner_capture_demands(demand).owner_demands)
        except (TypeError, ValueError):
            _append(failures, OperationalGateFailure.BOOK_LINEAGE)
            continue
        if actual_owner != expected_owner or content_sha256(actual_owner) != item.outbox_bundle_sha256:
            _append(failures, OperationalGateFailure.BOOK_LINEAGE)
        identity = selected.get(demand.identity.market_id)
        if (
            identity is None
            or demand.identity != identity
            or demand.purpose != BookCapturePurpose.FORMAL_REVIEW
            or receipt.status != BookCaptureStatus.ACCEPTED
            or receipt.capture_owner != CAPTURE_OWNER
            or receipt.demand_id != demand.demand_id
            or receipt.demand_sha256 != demand.canonical_sha256
            or receipt.market_id != demand.identity.market_id
            or receipt.purpose != demand.purpose
            or receipt.orderbook_snapshot_id != snapshot.record_id
            or receipt.orderbook_snapshot_sha256 != snapshot.canonical_sha256
            or receipt.capture_group_id != snapshot.capture_group_id
            or snapshot.identity != identity
            or snapshot.stale
            or receipt.source_observed_at is None
            or not demand.requested_at <= receipt.source_observed_at < demand.valid_until
            or receipt.received_at < receipt.source_observed_at
            or (receipt.received_at - receipt.source_observed_at).total_seconds()
            > demand.max_staleness_seconds
        ):
            _append(failures, OperationalGateFailure.BOOK_LINEAGE)


def _verify_budget(evidence: OperationalPilotGateEvidence, failures: set[OperationalGateFailure]) -> None:
    if evidence.preflight_manifest.budget != FIRST_PILOT_BUDGET:
        _append(failures, OperationalGateFailure.BUDGET)
    try:
        ledger = PilotBudgetLedger(FIRST_PILOT_BUDGET, started_at=evidence.authorization.authorized_at)
        for event in evidence.budget_events:
            ledger.append(event)
        summary = ledger.summary()
    except ValueError:
        _append(failures, OperationalGateFailure.BUDGET)
        return
    market_events = tuple(
        item for item in evidence.budget_events if item.kind.value == "MARKET"
    )
    network_events = tuple(
        item for item in evidence.budget_events if item.kind.value == "NETWORK_REQUEST"
    )
    demand_events = tuple(
        item for item in evidence.budget_events if item.kind.value == "BOOK_DEMAND"
    )
    artifact_events = tuple(
        item for item in evidence.budget_events if item.kind.value == "ARTIFACT_WRITE"
    )
    expected_markets = tuple(sorted(item.market_id for item in evidence.selected_markets))
    observed_markets = tuple(sorted(str(item.market_id) for item in market_events))
    expected_demands = tuple(
        sorted(
            (
                item.demand.identity.market_id,
                item.demand.canonical_sha256,
                2,
            )
            for item in evidence.books
        )
    )
    observed_demands = tuple(
        sorted(
            (str(item.market_id), str(item.request_sha256), item.token_count)
            for item in demand_events
        )
    )
    if (
        summary.distinct_markets != len(evidence.selected_markets)
        or observed_markets != expected_markets
        or len(network_events) != 1
        or network_events[0].request_sha256 != evidence.gamma.request_sha256
        or summary.book_demands != len(evidence.books)
        or observed_demands != expected_demands
        or len(artifact_events) != 1
        or artifact_events[0].artifact_bytes != evidence.raw_gamma.declared_length
    ):
        _append(failures, OperationalGateFailure.BUDGET)


def _verify_isolation_and_rollback(
    evidence: OperationalPilotGateEvidence,
    failures: set[OperationalGateFailure],
) -> None:
    try:
        isolation = compare_weather_isolation(
            evidence.weather_before,
            evidence.weather_during,
            evidence.weather_after,
            max_error_delta=evidence.max_error_delta,
            max_p95_latency_regression_ms=evidence.max_p95_latency_regression_ms,
        )
    except ValueError:
        _append(failures, OperationalGateFailure.WEATHER_ISOLATION)
    else:
        if isolation != evidence.weather_receipt or not isolation.passed:
            _append(failures, OperationalGateFailure.WEATHER_ISOLATION)
    try:
        rollback = validate_rollback_rehearsal(
            owner_before=evidence.rollback_before,
            owner_after=evidence.rollback_after,
            alpha_demand_enabled_before=True,
            alpha_demand_enabled_after=False,
            owner_restart_count=evidence.rollback_receipt.owner_restart_count,
            production_config_write_count=evidence.rollback_receipt.production_config_write_count,
        )
    except ValueError:
        _append(failures, OperationalGateFailure.ROLLBACK)
    else:
        if rollback != evidence.rollback_receipt or not rollback.passed:
            _append(failures, OperationalGateFailure.ROLLBACK)


def _verify_security(evidence: OperationalPilotGateEvidence, failures: set[OperationalGateFailure]) -> None:
    by_id = {item.canary_id: item for item in evidence.security_canaries}
    if (
        len(by_id) != len(evidence.security_canaries)
        or set(by_id) != REQUIRED_SECURITY_CANARIES
        or any(
            not item.denied
            or item.receipt_payload.get("canary_id") != item.canary_id
            or item.receipt_payload.get("capability") != item.canary_id
            or item.receipt_payload.get("decision") != "DENY"
            or item.receipt_payload.get("authorized") is not False
            or item.receipt_payload.get("execution") != "NO_ORDER"
            or content_sha256(item.receipt_payload) != item.receipt_sha256
            for item in evidence.security_canaries
        )
    ):
        _append(failures, OperationalGateFailure.SECURITY_CANARY)


def _verify_no_order(evidence: OperationalPilotGateEvidence, failures: set[OperationalGateFailure]) -> None:
    lineage = evidence.no_order_lineage
    packet, gate_b = lineage.market_packet, lineage.gate_b
    decision, prediction = lineage.decision, lineage.prediction
    book = next(
        (item for item in evidence.books if item.snapshot.record_id == packet.orderbook.record_id),
        None,
    )
    if (
        book is None
        or packet.orderbook != book.snapshot
        or packet.orderbook.canonical_sha256 != book.snapshot.canonical_sha256
        or gate_b.stage != RuleGateStage.B
        or gate_b.market_id != packet.market_id
        or gate_b.rule_hash != packet.rule_contract.rule_hash
        or decision.execution != "NO_ORDER"
        or decision.market_id != packet.market_id
        or decision.rule_hash != gate_b.rule_hash
        or gate_b.record_id not in decision.input_artifact_ids
        or packet.record_id not in decision.input_artifact_ids
        or packet.orderbook.record_id not in decision.input_artifact_ids
        or prediction.position_state not in {PositionState.NO_POSITION, PositionState.SIMULATED}
        or prediction.market_id != packet.market_id
        or prediction.rule_hash != gate_b.rule_hash
        or prediction.packet_id != packet.record_id
        or prediction.orderbook_snapshot_id != packet.orderbook.record_id
        or prediction.decision_id != decision.record_id
    ):
        _append(failures, OperationalGateFailure.NO_ORDER_LINEAGE)


def verify_operational_pilot_gate(
    evidence: OperationalPilotGateEvidence,
    *,
    verified_at: datetime,
    run_id: str,
) -> OperationalPilotGateCertificate:
    """Return a deterministic certificate; any failed check yields REWORK."""

    failures: set[OperationalGateFailure] = set()
    actual_raw_sha256 = sha256(evidence.raw_gamma.content).hexdigest()
    if (
        actual_raw_sha256 != evidence.raw_gamma.declared_sha256
        or len(evidence.raw_gamma.content) != evidence.raw_gamma.declared_length
    ):
        failures.add(OperationalGateFailure.GAMMA_RAW_ARTIFACT)
    schema_valid = True
    try:
        evidence = OperationalPilotGateEvidence.model_validate(
            evidence.model_dump(mode="python", exclude_none=False)
        )
    except ValueError:
        schema_valid = False
        failures.add(OperationalGateFailure.INPUT_SCHEMA_INVALID)
    verified_at = ensure_utc(verified_at)
    if schema_valid:
        _verify_authorization(evidence, failures)
        _verify_gamma(evidence, failures)
        _verify_market_set(evidence, failures)
        _verify_books(evidence, failures)
        _verify_budget(evidence, failures)
        _verify_isolation_and_rollback(evidence, failures)
        _verify_security(evidence, failures)
        _verify_no_order(evidence, failures)
    ordered_failures = tuple(sorted(failures, key=lambda item: item.value))
    disposition = (
        OperationalGateDisposition.PASS
        if not ordered_failures
        else OperationalGateDisposition.REWORK
    )
    evidence_payload = evidence.model_dump(mode="python", exclude_none=False)
    # Raw bytes were already rehashed by FrozenRawArtifact.  Bind their
    # declared hash/length/locator without asking the canonical JSON codec to
    # represent an arbitrary byte string.
    evidence_payload["raw_gamma"].pop("content", None)
    try:
        evidence_sha256 = content_sha256(evidence_payload)
    except (TypeError, ValueError):
        evidence_sha256 = sha256(repr(evidence_payload).encode("utf-8")).hexdigest()
    return OperationalPilotGateCertificate(
        schema_version=ALPHA_CONTRACT_VERSION,
        record_id=stable_record_id(
            "operational_gate_certificate",
            run_id,
            evidence_sha256,
            disposition,
            ordered_failures,
        ),
        run_id=run_id,
        created_at=verified_at,
        source="alpha_operational_gate_verifier",
        source_version="op_gate_v1",
        provenance=(),
        extensions={},
        disposition=disposition,
        evidence_sha256=evidence_sha256,
        checked_gate_ids=CHECKED_GATES,
        failure_codes=ordered_failures,
    )


__all__ = [
    "CHECKED_GATES",
    "REQUIRED_SECURITY_CANARIES",
    "FormalBookEvidence",
    "FrozenRawArtifact",
    "GammaReadEvidence",
    "NoOrderLineageEvidence",
    "OperationalGateDisposition",
    "OperationalGateFailure",
    "OperationalPilotGateCertificate",
    "OperationalPilotGateEvidence",
    "SecurityCanaryEvidence",
    "verify_operational_pilot_gate",
]
