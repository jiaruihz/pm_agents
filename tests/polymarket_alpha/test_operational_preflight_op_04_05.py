from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError
import pytest

from src.polymarket_alpha.contracts import MarketIdentity, content_sha256, stable_record_id
from src.polymarket_alpha.pilot import (
    FIRST_PILOT_BUDGET,
    BudgetEventKind,
    OperationalPilotManifest,
    OperationalPilotAuthorization,
    OwnerHealthObservation,
    PilotBudgetEvent,
    PilotBudgetLedger,
    PilotGateStatus,
    build_first_pilot_endpoint_policy,
    authorize_operational_preflight,
    compare_weather_isolation,
    prepare_fixture_backed_operational_preflight,
    validate_rollback_rehearsal,
)
from src.polymarket_alpha.pilot.operational import (
    _prepare_operational_preflight as prepare_operational_preflight,
)
from src.polymarket_alpha.pilot.operational_fixtures import load_frozen_fixture_set
from src.polymarket_alpha.security import (
    AlphaReadOnlyTransport,
    CapabilityDenied,
    HttpMethod,
    TransportMode,
    TransportRequest,
    audit_source_tree,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _identities(count: int = 3) -> tuple[MarketIdentity, ...]:
    return tuple(
        MarketIdentity(
            event_id=f"event-{index}",
            market_id=f"market-{index}",
            condition_id=f"condition-{index}",
            yes_token_id=f"yes-{index}",
            no_token_id=f"no-{index}",
        )
        for index in range(count)
    )


def _fixture_identities(count: int = 3) -> tuple[tuple[str, MarketIdentity], ...]:
    return tuple(
        (f"fixture-{index}", identity)
        for index, identity in enumerate(_identities(count))
    )


def _budget_event(
    kind: BudgetEventKind,
    index: int,
    *,
    at: datetime | None = None,
    token_count: int = 0,
    artifact_bytes: int = 0,
) -> PilotBudgetEvent:
    request_sha = f"{index % 10}" * 64
    kwargs: dict[str, object] = {
        "event_id": stable_record_id("pilot_budget_event", kind, index),
        "kind": kind,
        "occurred_at": at or NOW,
    }
    if kind == BudgetEventKind.MARKET:
        kwargs["market_id"] = f"market-{index}"
    elif kind == BudgetEventKind.NETWORK_REQUEST:
        kwargs["request_sha256"] = request_sha
    elif kind == BudgetEventKind.BOOK_DEMAND:
        kwargs.update(
            market_id=f"market-{index}",
            request_sha256=request_sha,
            token_count=token_count or 2,
        )
    else:
        kwargs["artifact_bytes"] = artifact_bytes
    return PilotBudgetEvent(**kwargs)


def _owner(at: datetime, *, process_hash: str = SHA_B, errors: int = 0, latency: int = 80) -> OwnerHealthObservation:
    return OwnerHealthObservation(
        release_sha256=SHA_A,
        process_identity_sha256=process_hash,
        config_sha256=SHA_C,
        alpha_demands=0,
        weather_demands=10,
        errors=errors,
        p95_latency_ms=latency,
        observed_at=at,
    )


def test_preflight_freezes_exact_policy_budget_and_no_io_claims() -> None:
    identities = _identities()
    result = prepare_operational_preflight(
        fixture_identities=(
            ("fixture-c", identities[2]),
            ("fixture-a", identities[0]),
            ("fixture-b", identities[1]),
        ),
        artifact_root="/tmp/polymarket-alpha-pilot/run-1",
        prepared_at=NOW,
    )
    assert result.manifest.gate_status == PilotGateStatus.PREPARED_NOT_AUTHORIZED
    assert result.manifest.network_io_authorized is False
    assert result.network_io_performed is False
    assert result.owner_demand_submitted is False
    assert result.production_state_mutated is False
    assert result.manifest.budget == FIRST_PILOT_BUDGET
    assert result.manifest.endpoint_policy_sha256 == result.endpoint_policy.canonical_sha256
    assert result.budget_summary.distinct_markets == 3
    assert result.budget_summary.network_requests == 4
    assert result.budget_summary.book_demands == 3
    assert len(result.planned_authorizations) == 1
    assert len(result.planned_owner_demand_sha256s) == 3
    assert {item.authorized_request.rule_id for item in result.planned_authorizations} == {
        "gamma_events_public_bounded",
    }
    assert all(item.authorized_request.host != "clob.polymarket.com" for item in result.planned_authorizations)
    assert all(item.receipt.decision.value == "ALLOW" for item in result.planned_authorizations)


def test_preflight_is_deterministic_and_identity_order_independent() -> None:
    kwargs = {
        "artifact_root": "/tmp/polymarket-alpha-pilot/run-1",
        "prepared_at": NOW,
    }
    pairs = _fixture_identities()
    first = prepare_operational_preflight(fixture_identities=pairs, **kwargs)
    second = prepare_operational_preflight(fixture_identities=tuple(reversed(pairs)), **kwargs)
    assert first == second
    assert content_sha256(first) == content_sha256(second)


def test_preflight_requires_three_to_five_paired_fixtures() -> None:
    with pytest.raises(ValueError, match="3-5"):
        prepare_operational_preflight(
            fixture_identities=_fixture_identities(2),
            artifact_root="/tmp/polymarket-alpha-pilot/run-1",
            prepared_at=NOW,
        )
    with pytest.raises(ValueError, match="unique"):
        prepare_operational_preflight(
            fixture_identities=(("same", _identities()[0]), ("same", _identities()[1]), ("c", _identities()[2])),
            artifact_root="/tmp/polymarket-alpha-pilot/run-1",
            prepared_at=NOW,
        )


def test_offline_manifest_cannot_claim_authorization_or_unsafe_artifact_root() -> None:
    result = prepare_operational_preflight(
        fixture_identities=_fixture_identities(),
        artifact_root="/tmp/polymarket-alpha-pilot/run-1",
        prepared_at=NOW,
    )
    payload = result.manifest.model_dump(mode="python")
    with pytest.raises(ValidationError, match="cannot claim"):
        OperationalPilotManifest.model_validate(
            {**payload, "network_io_authorized": True, "owner_authorization_id": "owner-ok"}
        )
    with pytest.raises(ValidationError, match="dedicated pilot child"):
        OperationalPilotManifest.model_validate({**payload, "artifact_root": "/tmp"})


def test_owner_authorization_is_separate_hash_bound_and_time_bounded() -> None:
    preflight = prepare_operational_preflight(
        fixture_identities=_fixture_identities(),
        artifact_root="/tmp/polymarket-alpha-pilot/run-authorized",
        prepared_at=NOW,
    )
    authorization = authorize_operational_preflight(
        preflight,
        owner_authorization_id="user-thread-approval-2026-08-27",
        authorized_at=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=31),
    )
    assert isinstance(authorization, OperationalPilotAuthorization)
    assert authorization.preflight_manifest_id == preflight.manifest.record_id
    assert authorization.preflight_manifest_sha256 == preflight.manifest.canonical_sha256
    assert authorization.network_io_authorized
    assert authorization.owner_demand_authorized
    assert authorization.production_config_changes_authorized is False
    assert authorization.current_runtime_db_writes_authorized is False
    assert authorization.execution_capability == "NO_ORDER"
    retry = authorize_operational_preflight(
        preflight,
        owner_authorization_id="user-thread-approval-2026-08-27",
        authorized_at=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=31),
    )
    assert retry == authorization
    with pytest.raises(ValidationError, match="runtime budget"):
        authorize_operational_preflight(
            preflight,
            owner_authorization_id="user-thread-approval-2026-08-27",
            authorized_at=NOW,
            expires_at=NOW + timedelta(minutes=31),
        )
    with pytest.raises(ValueError, match="must not be blank"):
        authorize_operational_preflight(
            preflight,
            owner_authorization_id=" ",
            authorized_at=NOW,
            expires_at=NOW + timedelta(minutes=30),
        )


def test_op01_fixture_set_binds_directly_to_preflight_manifest() -> None:
    fixture_root = Path(__file__).with_name("fixtures") / "operational_pilot"
    frozen = load_frozen_fixture_set(fixture_root, source_observed_at=NOW)
    result = prepare_fixture_backed_operational_preflight(
        fixture_set=frozen,
        artifact_root="/tmp/polymarket-alpha-pilot/run-fixtures",
        prepared_at=NOW,
    )
    assert len(result.manifest.fixture_identity_bindings) == 4
    assert {item[1] for item in result.manifest.fixture_identity_bindings} == {
        "op01-1001",
        "op01-1002",
        "op01-1003",
        "op01-1004",
    }


@pytest.mark.parametrize(
    ("method", "url", "headers", "body", "environment"),
    [
        (HttpMethod.GET, "https://gamma-api.polymarket.com/markets?closed=false&limit=3&offset=0", {}, None, {}),
        (HttpMethod.GET, "https://gamma-api.polymarket.com/%65vents?closed=false&limit=3&offset=0", {}, None, {}),
        (HttpMethod.GET, "https://gamma-api.polymarket.com/events?closed=false&limit=3&offset=0", {"Authorization": "x"}, None, {}),
        (HttpMethod.GET, "https://gamma-api.polymarket.com/events?closed=false&limit=3&offset=0", {}, None, {"HTTPS_PROXY": "http://proxy"}),
        (HttpMethod.GET, "https://gamma-api.polymarket.com/events?closed=true&limit=3&offset=0", {}, None, {}),
        (HttpMethod.GET, "https://gamma-api.polymarket.com/events?closed=false&limit=0&offset=0", {}, None, {}),
        (HttpMethod.GET, "https://gamma-api.polymarket.com/events?closed=false&limit=6&offset=0", {}, None, {}),
        (HttpMethod.GET, "https://gamma-api.polymarket.com/events?closed=false&limit=999999999&offset=999999999", {}, None, {}),
        (HttpMethod.GET, "https://gamma-api.polymarket.com/events?closed=false&limit=nope&offset=0", {}, None, {}),
        (HttpMethod.GET, "https://gamma-api.polymarket.com/events?closed=false&limit=3&offset=1", {}, None, {}),
        (HttpMethod.POST, "https://clob.polymarket.com/books", {"Content-Type": "application/json"}, [{"token_id": "yes"}, {"token_id": "no"}], {}),
        (HttpMethod.POST, "https://clob.polymarket.com/books", {"Content-Type": "application/json"}, [{"token_id": "x", "side": "BUY"}], {}),
        (HttpMethod.POST, "https://clob.polymarket.com/order", {"Content-Type": "application/json"}, [{"token_id": "x"}], {}),
    ],
)
def test_first_pilot_policy_denies_endpoint_header_body_and_proxy_bypasses(
    method: HttpMethod,
    url: str,
    headers: dict[str, str],
    body: object,
    environment: dict[str, str],
) -> None:
    transport = AlphaReadOnlyTransport(
        TransportMode.READ_ONLY,
        build_first_pilot_endpoint_policy(created_at=NOW),
    )
    with pytest.raises(CapabilityDenied):
        transport.authorize(
            TransportRequest(
                method=method,
                url=url,
                headers=headers,
                json_body=body,
                requested_at=NOW,
            ),
            environment=environment,
        )


def test_budget_ledger_is_append_only_idempotent_and_counts_exactly() -> None:
    ledger = PilotBudgetLedger(FIRST_PILOT_BUDGET, started_at=NOW)
    event = _budget_event(BudgetEventKind.MARKET, 1)
    ledger.append(event)
    ledger.append(event)
    ledger.append(_budget_event(BudgetEventKind.NETWORK_REQUEST, 2))
    ledger.append(_budget_event(BudgetEventKind.BOOK_DEMAND, 3))
    ledger.append(_budget_event(BudgetEventKind.ARTIFACT_WRITE, 4, artifact_bytes=100))
    summary = ledger.summary()
    assert summary.distinct_markets == 1
    assert summary.network_requests == 1
    assert summary.book_demands == 1
    assert summary.artifact_bytes == 100
    assert len(summary.event_sha256s) == 4
    conflict = event.model_copy(update={"market_id": "different"})
    with pytest.raises(ValueError, match="collision"):
        ledger.append(conflict)


def test_budget_ledger_fails_closed_on_each_fixed_limit() -> None:
    market_ledger = PilotBudgetLedger(FIRST_PILOT_BUDGET, started_at=NOW)
    for index in range(5):
        market_ledger.append(_budget_event(BudgetEventKind.MARKET, index))
    with pytest.raises(ValueError, match="market scan"):
        market_ledger.append(_budget_event(BudgetEventKind.MARKET, 5))

    token_ledger = PilotBudgetLedger(FIRST_PILOT_BUDGET, started_at=NOW)
    with pytest.raises(ValueError, match="token batch"):
        token_ledger.append(_budget_event(BudgetEventKind.BOOK_DEMAND, 1, token_count=11))

    demand_ledger = PilotBudgetLedger(FIRST_PILOT_BUDGET, started_at=NOW)
    for index in range(5):
        demand_ledger.append(
            _budget_event(BudgetEventKind.BOOK_DEMAND, index, at=NOW + timedelta(seconds=index))
        )
    with pytest.raises(ValueError, match="demand rate"):
        demand_ledger.append(
            _budget_event(BudgetEventKind.BOOK_DEMAND, 5, at=NOW + timedelta(seconds=5))
        )

    request_ledger = PilotBudgetLedger(FIRST_PILOT_BUDGET, started_at=NOW)
    for index in range(30):
        request_ledger.append(
            PilotBudgetEvent(
                event_id=stable_record_id("pilot_budget_event", "request", index),
                kind=BudgetEventKind.NETWORK_REQUEST,
                occurred_at=NOW,
                request_sha256=stable_record_id("request", index).split(":", 1)[1],
            )
        )
    with pytest.raises(ValueError, match="network request"):
        request_ledger.append(
            PilotBudgetEvent(
                event_id=stable_record_id("pilot_budget_event", "request", 30),
                kind=BudgetEventKind.NETWORK_REQUEST,
                occurred_at=NOW,
                request_sha256=stable_record_id("request", 30).split(":", 1)[1],
            )
        )

    storage_ledger = PilotBudgetLedger(FIRST_PILOT_BUDGET, started_at=NOW)
    with pytest.raises(ValueError, match="artifact storage"):
        storage_ledger.append(
            _budget_event(
                BudgetEventKind.ARTIFACT_WRITE,
                1,
                artifact_bytes=FIRST_PILOT_BUDGET.max_artifact_bytes_total + 1,
            )
        )
    runtime_ledger = PilotBudgetLedger(FIRST_PILOT_BUDGET, started_at=NOW)
    with pytest.raises(ValueError, match="runtime"):
        runtime_ledger.append(
            _budget_event(
                BudgetEventKind.NETWORK_REQUEST,
                1,
                at=NOW + timedelta(minutes=31),
            )
        )


@pytest.mark.parametrize(
    "extra",
    [
        {"kind": BudgetEventKind.NETWORK_REQUEST, "request_sha256": SHA_A, "market_id": "market-1"},
        {"kind": BudgetEventKind.NETWORK_REQUEST, "request_sha256": SHA_A, "token_count": 1},
        {"kind": BudgetEventKind.ARTIFACT_WRITE, "artifact_bytes": 1, "market_id": "market-1"},
    ],
)
def test_budget_event_kinds_reject_unrelated_fields(extra: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PilotBudgetEvent(
            event_id=stable_record_id("pilot_budget_event", "invalid", extra),
            occurred_at=NOW,
            **extra,
        )


def test_weather_isolation_and_rollback_require_unchanged_owner() -> None:
    before = _owner(NOW)
    during = _owner(NOW + timedelta(minutes=1))
    after = _owner(NOW + timedelta(minutes=2))
    receipt = compare_weather_isolation(
        before,
        during,
        after,
        max_error_delta=0,
        max_p95_latency_regression_ms=0,
    )
    assert receipt.passed
    assert receipt.stable_owner_identity
    assert receipt.error_delta == 0

    changed = _owner(NOW + timedelta(minutes=2), process_hash="d" * 64)
    assert compare_weather_isolation(
        before,
        during,
        changed,
        max_error_delta=0,
        max_p95_latency_regression_ms=0,
    ).passed is False

    rollback = validate_rollback_rehearsal(
        owner_before=before,
        owner_after=after,
        alpha_demand_enabled_before=True,
        alpha_demand_enabled_after=False,
        owner_restart_count=0,
        production_config_write_count=0,
    )
    assert rollback.passed
    assert rollback.owner_before_sha256 == rollback.owner_after_sha256
    failed = validate_rollback_rehearsal(
        owner_before=before,
        owner_after=changed,
        alpha_demand_enabled_before=True,
        alpha_demand_enabled_after=False,
        owner_restart_count=1,
        production_config_write_count=0,
    )
    assert failed.passed is False


def test_operational_preflight_source_remains_no_live_capability_clean() -> None:
    audit = audit_source_tree("src/polymarket_alpha")
    assert audit.violations == ()
