"""Offline preparation contracts for the read-only operational pilot.

Nothing in this module performs I/O.  It freezes the exact endpoint policy,
reserves the first-pilot budget, and validates caller-supplied owner-health and
rollback observations.  A later explicitly authorized runner may consume the
artifacts produced here, but it must provide the actual network/process
observations separately.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Iterable, Literal

from pydantic import Field, field_validator, model_validator

from ..contracts import (
    ALPHA_CONTRACT_VERSION,
    AlphaContract,
    CommonEnvelope,
    MarketIdentity,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256
from ..security import (
    AlphaReadOnlyTransport,
    AuthorizationResult,
    EndpointRule,
    HttpMethod,
    QueryValueKind,
    QueryValueRule,
    ReadOnlyPolicyArtifact,
    TransportMode,
    TransportRequest,
)


OPERATIONAL_PREFLIGHT_VERSION = "op_preflight_v1"


class PilotGateStatus(StrEnum):
    PREPARED_NOT_AUTHORIZED = "PREPARED_NOT_AUTHORIZED"
    AUTHORIZED_READ_ONLY = "AUTHORIZED_READ_ONLY"


class BudgetEventKind(StrEnum):
    MARKET = "MARKET"
    NETWORK_REQUEST = "NETWORK_REQUEST"
    BOOK_DEMAND = "BOOK_DEMAND"
    ARTIFACT_WRITE = "ARTIFACT_WRITE"


class OperationalPilotBudget(AlphaContract):
    max_markets_per_scan: int = Field(gt=0, le=100)
    max_tokens_per_batch: int = Field(gt=0, le=1000)
    max_demands_per_minute: int = Field(gt=0, le=1000)
    max_network_requests_total: int = Field(gt=0, le=10000)
    max_artifact_bytes_total: int = Field(gt=0)
    max_runtime_minutes: int = Field(gt=0, le=1440)
    redirects: Literal["disabled"] = "disabled"
    proxy_environment: Literal["cleared"] = "cleared"
    current_runtime_db_writes: Literal[0] = 0


FIRST_PILOT_BUDGET = OperationalPilotBudget(
    max_markets_per_scan=5,
    max_tokens_per_batch=10,
    max_demands_per_minute=5,
    max_network_requests_total=30,
    max_artifact_bytes_total=50_000_000,
    max_runtime_minutes=30,
)


class OperationalPilotManifest(CommonEnvelope):
    gate_status: PilotGateStatus
    readiness_scope: Literal["READ_ONLY_OPERATIONAL_PILOT_PREFLIGHT"]
    owner_authorization_id: str | None = None
    market_fixture_ids: tuple[str, ...]
    identity_sha256s: tuple[str, ...]
    fixture_identity_bindings: tuple[tuple[str, str, str], ...]
    endpoint_policy_sha256: str
    budget: OperationalPilotBudget
    artifact_root: str
    network_io_authorized: bool
    production_config_changes_authorized: Literal[False] = False
    execution_capability: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("endpoint_policy_sha256")
    @classmethod
    def policy_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("market_fixture_ids", "identity_sha256s")
    @classmethod
    def ordered_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(value)) != value or len(value) != len(set(value)):
            raise ValueError("manifest identities must be sorted and unique")
        if any(not item.strip() for item in value):
            raise ValueError("manifest identities must not be blank")
        return value

    @field_validator("fixture_identity_bindings")
    @classmethod
    def bindings_are_ordered_and_unique(
        cls, value: tuple[tuple[str, str, str], ...]
    ) -> tuple[tuple[str, str, str], ...]:
        if tuple(sorted(value)) != value or len(value) != len(set(value)):
            raise ValueError("fixture identity bindings must be sorted and unique")
        for fixture_id, market_id, identity_hash in value:
            if not fixture_id.strip() or not market_id.strip():
                raise ValueError("fixture binding ids must not be blank")
            validate_sha256(identity_hash)
        return value

    @field_validator("artifact_root")
    @classmethod
    def artifact_root_is_explicit(cls, value: str) -> str:
        value = value.strip()
        if ".." in value.split("/") or not (
            value.startswith("/tmp/polymarket-alpha-pilot/")
            or value.startswith("/private/tmp/polymarket-alpha-pilot/")
        ):
            raise ValueError("artifact_root must be a dedicated pilot child under /tmp")
        return value

    @model_validator(mode="after")
    def authorization_is_consistent(self) -> "OperationalPilotManifest":
        if not 3 <= len(self.market_fixture_ids) <= self.budget.max_markets_per_scan:
            raise ValueError("manifest requires 3-5 bounded market fixtures")
        if len(self.market_fixture_ids) != len(self.identity_sha256s):
            raise ValueError("each market fixture requires one identity hash")
        if len(self.fixture_identity_bindings) != len(self.market_fixture_ids):
            raise ValueError("each fixture requires an explicit identity binding")
        if tuple(item[0] for item in self.fixture_identity_bindings) != self.market_fixture_ids:
            raise ValueError("fixture binding ids do not match manifest fixture ids")
        if tuple(sorted(item[2] for item in self.fixture_identity_bindings)) != self.identity_sha256s:
            raise ValueError("fixture binding hashes do not match manifest identity hashes")
        if len({item[1] for item in self.fixture_identity_bindings}) != len(
            self.fixture_identity_bindings
        ):
            raise ValueError("each fixture must bind a distinct canonical market")
        if self.gate_status == PilotGateStatus.PREPARED_NOT_AUTHORIZED:
            if self.network_io_authorized or self.owner_authorization_id is not None:
                raise ValueError("offline preflight cannot claim owner authorization")
        elif not self.network_io_authorized or not self.owner_authorization_id:
            raise ValueError("authorized pilot requires an explicit owner authorization id")
        return self


class PilotBudgetEvent(AlphaContract):
    event_id: str
    kind: BudgetEventKind
    occurred_at: datetime
    market_id: str | None = None
    request_sha256: str | None = None
    token_count: int = Field(default=0, ge=0)
    artifact_bytes: int = Field(default=0, ge=0)

    @field_validator("occurred_at")
    @classmethod
    def time_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("request_sha256")
    @classmethod
    def request_hash_is_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def kind_fields_are_consistent(self) -> "PilotBudgetEvent":
        if self.kind == BudgetEventKind.MARKET:
            if not self.market_id or self.request_sha256 or self.token_count or self.artifact_bytes:
                raise ValueError("MARKET event may contain only market_id")
        elif self.kind == BudgetEventKind.NETWORK_REQUEST:
            if self.market_id or not self.request_sha256 or self.token_count or self.artifact_bytes:
                raise ValueError("NETWORK_REQUEST requires only a request hash")
        elif self.kind == BudgetEventKind.BOOK_DEMAND:
            if not self.market_id or not self.request_sha256 or self.token_count <= 0 or self.artifact_bytes:
                raise ValueError("BOOK_DEMAND requires market, request hash and token count")
        elif self.kind == BudgetEventKind.ARTIFACT_WRITE:
            if self.market_id or self.artifact_bytes <= 0 or self.request_sha256 or self.token_count:
                raise ValueError("ARTIFACT_WRITE requires only positive bytes")
        return self


class PilotBudgetSummary(AlphaContract):
    distinct_markets: int
    network_requests: int
    book_demands: int
    artifact_bytes: int
    started_at: datetime
    ended_at: datetime
    event_sha256s: tuple[str, ...]

    @field_validator("started_at", "ended_at")
    @classmethod
    def times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def summary_is_ordered(self) -> "PilotBudgetSummary":
        if self.ended_at < self.started_at:
            raise ValueError("budget summary end cannot precede start")
        return self


class PilotBudgetLedger:
    """In-memory append-only reservation ledger with exact-retry idempotency."""

    def __init__(self, budget: OperationalPilotBudget, *, started_at: datetime) -> None:
        self.budget = budget
        self.started_at = ensure_utc(started_at)
        self._events: dict[str, PilotBudgetEvent] = {}

    def append(self, event: PilotBudgetEvent) -> None:
        existing = self._events.get(event.event_id)
        if existing is not None:
            if existing != event:
                raise ValueError("budget event id collision with different contents")
            return
        candidate = (*self._events.values(), event)
        self._validate(candidate)
        self._events[event.event_id] = event

    def _validate(self, events: Iterable[PilotBudgetEvent]) -> None:
        rows = tuple(events)
        if any(row.occurred_at < self.started_at for row in rows):
            raise ValueError("budget event precedes pilot start")
        if rows and max(row.occurred_at for row in rows) > self.started_at + timedelta(
            minutes=self.budget.max_runtime_minutes
        ):
            raise ValueError("pilot runtime budget exceeded")
        markets = {row.market_id for row in rows if row.kind == BudgetEventKind.MARKET}
        if len(markets) > self.budget.max_markets_per_scan:
            raise ValueError("market scan budget exceeded")
        requests = [row for row in rows if row.kind == BudgetEventKind.NETWORK_REQUEST]
        if len(requests) > self.budget.max_network_requests_total:
            raise ValueError("network request budget exceeded")
        demands = sorted(
            (row for row in rows if row.kind == BudgetEventKind.BOOK_DEMAND),
            key=lambda row: row.occurred_at,
        )
        if any(row.token_count > self.budget.max_tokens_per_batch for row in demands):
            raise ValueError("token batch budget exceeded")
        for index, row in enumerate(demands):
            window_end = row.occurred_at + timedelta(minutes=1)
            count = sum(row.occurred_at <= other.occurred_at < window_end for other in demands[index:])
            if count > self.budget.max_demands_per_minute:
                raise ValueError("book demand rate budget exceeded")
        artifact_bytes = sum(
            row.artifact_bytes for row in rows if row.kind == BudgetEventKind.ARTIFACT_WRITE
        )
        if artifact_bytes > self.budget.max_artifact_bytes_total:
            raise ValueError("artifact storage budget exceeded")

    def summary(self) -> PilotBudgetSummary:
        rows = tuple(sorted(self._events.values(), key=lambda row: (row.occurred_at, row.event_id)))
        ended_at = max((row.occurred_at for row in rows), default=self.started_at)
        return PilotBudgetSummary(
            distinct_markets=len(
                {row.market_id for row in rows if row.kind == BudgetEventKind.MARKET}
            ),
            network_requests=sum(row.kind == BudgetEventKind.NETWORK_REQUEST for row in rows),
            book_demands=sum(row.kind == BudgetEventKind.BOOK_DEMAND for row in rows),
            artifact_bytes=sum(
                row.artifact_bytes for row in rows if row.kind == BudgetEventKind.ARTIFACT_WRITE
            ),
            started_at=self.started_at,
            ended_at=ended_at,
            event_sha256s=tuple(sorted(content_sha256(row) for row in rows)),
        )


class OwnerHealthObservation(AlphaContract):
    release_sha256: str
    process_identity_sha256: str
    config_sha256: str
    alpha_demands: int = Field(ge=0)
    weather_demands: int = Field(ge=0)
    errors: int = Field(ge=0)
    p95_latency_ms: int = Field(ge=0)
    observed_at: datetime

    @field_validator("release_sha256", "process_identity_sha256", "config_sha256")
    @classmethod
    def hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class WeatherIsolationReceipt(AlphaContract):
    before_sha256: str
    during_sha256: str
    after_sha256: str
    stable_owner_identity: bool
    weather_demand_delta: int
    error_delta: int
    p95_latency_delta_ms: int
    max_error_delta: int = Field(ge=0)
    max_p95_latency_regression_ms: int = Field(ge=0)
    passed: bool

    @field_validator("before_sha256", "during_sha256", "after_sha256")
    @classmethod
    def hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)


class RollbackRehearsalReceipt(AlphaContract):
    owner_before_sha256: str
    owner_after_sha256: str
    alpha_demand_enabled_before: bool
    alpha_demand_enabled_after: bool
    owner_restart_count: int = Field(ge=0)
    production_config_write_count: int = Field(ge=0)
    passed: bool

    @field_validator("owner_before_sha256", "owner_after_sha256")
    @classmethod
    def hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)


class OperationalPreflightResult(AlphaContract):
    manifest: OperationalPilotManifest
    endpoint_policy: ReadOnlyPolicyArtifact
    planned_authorizations: tuple[AuthorizationResult, ...]
    planned_owner_demand_sha256s: tuple[str, ...]
    budget_summary: PilotBudgetSummary
    network_io_performed: Literal[False] = False
    owner_demand_submitted: Literal[False] = False
    production_state_mutated: Literal[False] = False

    @model_validator(mode="after")
    def bindings_are_exact(self) -> "OperationalPreflightResult":
        if self.manifest.endpoint_policy_sha256 != self.endpoint_policy.canonical_sha256:
            raise ValueError("manifest endpoint policy hash mismatch")
        policy_hashes = {
            item.authorized_request.policy_sha256 for item in self.planned_authorizations
        }
        if policy_hashes != {self.endpoint_policy.canonical_sha256}:
            raise ValueError("planned authorization is not bound to the frozen policy")
        if not self.planned_owner_demand_sha256s:
            raise ValueError("preflight requires bounded existing-owner demand reservations")
        for value in self.planned_owner_demand_sha256s:
            validate_sha256(value)
        return self


def build_first_pilot_endpoint_policy(*, created_at: datetime) -> ReadOnlyPolicyArtifact:
    created_at = ensure_utc(created_at)
    endpoints = (
        EndpointRule(
            rule_id="gamma_events_public_bounded",
            method=HttpMethod.GET,
            host="gamma-api.polymarket.com",
            path="/events",
            allowed_query_keys=("closed", "limit", "offset"),
            required_query_keys=("closed", "limit", "offset"),
            query_value_rules=(
                QueryValueRule(
                    key="closed",
                    kind=QueryValueKind.EXACT,
                    exact_values=("false",),
                ),
                QueryValueRule(
                    key="limit",
                    kind=QueryValueKind.INTEGER_RANGE,
                    minimum=1,
                    maximum=FIRST_PILOT_BUDGET.max_markets_per_scan,
                ),
                QueryValueRule(
                    key="offset",
                    kind=QueryValueKind.EXACT,
                    exact_values=("0",),
                ),
            ),
        ),
    )
    payload = {
        "schema_version": ALPHA_CONTRACT_VERSION,
        "record_id": stable_record_id("read_only_policy", endpoints, FIRST_PILOT_BUDGET),
        "run_id": stable_record_id("security_run", "read_only_operational_pilot_v1"),
        "created_at": created_at,
        "source": "alpha_operational_preflight",
        "source_version": OPERATIONAL_PREFLIGHT_VERSION,
        "provenance": (),
        "extensions": {},
        "profile": TransportMode.READ_ONLY,
        "endpoints": endpoints,
        "allowed_environment_keys": (),
        "max_redirects": 0,
    }
    return ReadOnlyPolicyArtifact(**payload)


def _prepare_operational_preflight(
    *,
    fixture_identities: Iterable[tuple[str, MarketIdentity]],
    artifact_root: str,
    prepared_at: datetime,
) -> OperationalPreflightResult:
    """Freeze an offline-only first-pilot input and request reservation plan."""

    prepared_at = ensure_utc(prepared_at)
    pairs = tuple(sorted(fixture_identities, key=lambda item: item[0]))
    if any(not fixture_id.strip() for fixture_id, _ in pairs):
        raise ValueError("fixture ids must not be blank")
    if len({fixture_id for fixture_id, _ in pairs}) != len(pairs):
        raise ValueError("fixture ids must be unique")
    if len({identity.market_id for _, identity in pairs}) != len(pairs):
        raise ValueError("canonical market ids must be unique")
    fixture_tuple = tuple(fixture_id for fixture_id, _ in pairs)
    identity_tuple = tuple(identity for _, identity in pairs)
    bindings = tuple(
        (fixture_id, identity.market_id, content_sha256(identity))
        for fixture_id, identity in pairs
    )
    policy = build_first_pilot_endpoint_policy(created_at=prepared_at)
    manifest_id = stable_record_id(
        "operational_manifest",
        fixture_tuple,
        identity_tuple,
        policy.canonical_sha256,
        FIRST_PILOT_BUDGET,
        prepared_at,
    )
    manifest = OperationalPilotManifest(
        record_id=manifest_id,
        run_id=stable_record_id("operational_run", manifest_id),
        created_at=prepared_at,
        source="alpha_operational_preflight",
        source_version=OPERATIONAL_PREFLIGHT_VERSION,
        provenance=(),
        extensions={},
        gate_status=PilotGateStatus.PREPARED_NOT_AUTHORIZED,
        readiness_scope="READ_ONLY_OPERATIONAL_PILOT_PREFLIGHT",
        market_fixture_ids=fixture_tuple,
        identity_sha256s=tuple(sorted(content_sha256(item) for item in identity_tuple)),
        fixture_identity_bindings=bindings,
        endpoint_policy_sha256=policy.canonical_sha256,
        budget=FIRST_PILOT_BUDGET,
        artifact_root=artifact_root,
        network_io_authorized=False,
    )
    ledger = PilotBudgetLedger(FIRST_PILOT_BUDGET, started_at=prepared_at)
    for identity in identity_tuple:
        event = PilotBudgetEvent(
            event_id=stable_record_id("pilot_budget_event", "market", identity.market_id),
            kind=BudgetEventKind.MARKET,
            occurred_at=prepared_at,
            market_id=identity.market_id,
        )
        ledger.append(event)

    requests = [
        TransportRequest(
            method=HttpMethod.GET,
            url=f"https://gamma-api.polymarket.com/events?closed=false&limit={len(identity_tuple)}&offset=0",
            headers={"Accept": "application/json"},
            requested_at=prepared_at,
        )
    ]
    transport = AlphaReadOnlyTransport(TransportMode.READ_ONLY, policy)
    authorizations = tuple(transport.authorize(request, environment={}) for request in requests)
    for authorization in authorizations:
        request_hash = authorization.authorized_request.request_sha256
        occurred_at = authorization.authorized_request.requested_at
        ledger.append(
            PilotBudgetEvent(
                event_id=stable_record_id("pilot_budget_event", "request", request_hash),
                kind=BudgetEventKind.NETWORK_REQUEST,
                occurred_at=occurred_at,
                request_sha256=request_hash,
            )
        )
    owner_demand_hashes: list[str] = []
    for index, identity in enumerate(identity_tuple, start=1):
        occurred_at = prepared_at + timedelta(seconds=index)
        demand_hash = content_sha256(
            {
                "capture_owner": "weather_market_books",
                "consumer": "polymarket_alpha",
                "purpose": "SENSING",
                "market_id": identity.market_id,
                "token_ids": (identity.yes_token_id, identity.no_token_id),
            }
        )
        owner_demand_hashes.append(demand_hash)
        # Reserve one bounded owner-side public /books request without making
        # the route directly executable from the Alpha process.
        ledger.append(
            PilotBudgetEvent(
                event_id=stable_record_id("pilot_budget_event", "owner_request", demand_hash),
                kind=BudgetEventKind.NETWORK_REQUEST,
                occurred_at=occurred_at,
                request_sha256=demand_hash,
            )
        )
        ledger.append(
            PilotBudgetEvent(
                event_id=stable_record_id("pilot_budget_event", "demand", demand_hash),
                kind=BudgetEventKind.BOOK_DEMAND,
                occurred_at=occurred_at,
                market_id=identity.market_id,
                request_sha256=demand_hash,
                token_count=2,
            )
        )
    return OperationalPreflightResult(
        manifest=manifest,
        endpoint_policy=policy,
        planned_authorizations=authorizations,
        planned_owner_demand_sha256s=tuple(sorted(owner_demand_hashes)),
        budget_summary=ledger.summary(),
    )


def prepare_fixture_backed_operational_preflight(
    *,
    fixture_set: "FrozenFixtureSet",
    artifact_root: str,
    prepared_at: datetime,
) -> OperationalPreflightResult:
    """Bind an accepted OP-01 fixture set to the OP-04/OP-05 preflight."""

    from .operational_fixtures import FixtureReceiptCode, FrozenFixtureSet

    if not isinstance(fixture_set, FrozenFixtureSet):
        raise TypeError("fixture_set must be a FrozenFixtureSet")
    failures = tuple(
        receipt
        for receipt in fixture_set.receipts
        if receipt.code != FixtureReceiptCode.ACCEPTED
    )
    if failures:
        codes = ",".join(sorted({item.code.value for item in failures}))
        raise ValueError(f"operational preflight rejects non-accepted fixture receipts: {codes}")
    if len(fixture_set.fixtures) != len(fixture_set.receipts):
        raise ValueError("operational preflight requires one accepted receipt per fixture")
    return _prepare_operational_preflight(
        fixture_identities=(
            (item.fixture_id, item.normalized.identity) for item in fixture_set.fixtures
        ),
        artifact_root=artifact_root,
        prepared_at=prepared_at,
    )


def compare_weather_isolation(
    before: OwnerHealthObservation,
    during: OwnerHealthObservation,
    after: OwnerHealthObservation,
    *,
    max_error_delta: int,
    max_p95_latency_regression_ms: int,
) -> WeatherIsolationReceipt:
    """Evaluate exact caller observations; this function never inspects production."""

    if not before.observed_at < during.observed_at < after.observed_at:
        raise ValueError("weather isolation observations must be ordered")
    if max_error_delta < 0 or max_p95_latency_regression_ms < 0:
        raise ValueError("weather isolation tolerances must not be negative")
    identities = {
        (item.release_sha256, item.process_identity_sha256, item.config_sha256)
        for item in (before, during, after)
    }
    stable = len(identities) == 1
    weather_delta = after.weather_demands - before.weather_demands
    error_delta = after.errors - before.errors
    latency_delta = max(during.p95_latency_ms, after.p95_latency_ms) - before.p95_latency_ms
    counters_ordered = (
        before.weather_demands <= during.weather_demands <= after.weather_demands
        and before.errors <= during.errors <= after.errors
    )
    passed = (
        stable
        and counters_ordered
        and error_delta <= max_error_delta
        and latency_delta <= max_p95_latency_regression_ms
    )
    return WeatherIsolationReceipt(
        before_sha256=content_sha256(before),
        during_sha256=content_sha256(during),
        after_sha256=content_sha256(after),
        stable_owner_identity=stable,
        weather_demand_delta=weather_delta,
        error_delta=error_delta,
        p95_latency_delta_ms=latency_delta,
        max_error_delta=max_error_delta,
        max_p95_latency_regression_ms=max_p95_latency_regression_ms,
        passed=passed,
    )


def validate_rollback_rehearsal(
    *,
    owner_before: OwnerHealthObservation,
    owner_after: OwnerHealthObservation,
    alpha_demand_enabled_before: bool,
    alpha_demand_enabled_after: bool,
    owner_restart_count: int,
    production_config_write_count: int,
) -> RollbackRehearsalReceipt:
    """Prove Alpha stopped without changing/restarting the existing owner."""

    if owner_after.observed_at <= owner_before.observed_at:
        raise ValueError("rollback observations must be ordered")
    owner_identity_before = content_sha256(
        (owner_before.release_sha256, owner_before.process_identity_sha256, owner_before.config_sha256)
    )
    owner_identity_after = content_sha256(
        (owner_after.release_sha256, owner_after.process_identity_sha256, owner_after.config_sha256)
    )
    passed = (
        alpha_demand_enabled_before
        and not alpha_demand_enabled_after
        and owner_identity_before == owner_identity_after
        and owner_restart_count == 0
        and production_config_write_count == 0
    )
    return RollbackRehearsalReceipt(
        owner_before_sha256=owner_identity_before,
        owner_after_sha256=owner_identity_after,
        alpha_demand_enabled_before=alpha_demand_enabled_before,
        alpha_demand_enabled_after=alpha_demand_enabled_after,
        owner_restart_count=owner_restart_count,
        production_config_write_count=production_config_write_count,
        passed=passed,
    )
