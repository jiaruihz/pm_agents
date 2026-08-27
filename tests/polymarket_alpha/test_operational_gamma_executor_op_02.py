from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import ast
import hashlib
import json
import sys

import pytest
from src.polymarket_alpha.pilot.operational import (
    OperationalPilotAuthorization,
    OperationalPilotBudget,
    build_gamma_events_endpoint_policy,
)
from src.polymarket_alpha.contracts import canonical_json, content_sha256, stable_record_id
from src.polymarket_alpha.security import CapabilityDenied, build_mac_local_market_proxy_profile


SCRIPT_DIR = Path(__file__).parents[2] / "scripts" / "ops"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import polymarket_alpha_gamma_read_only_pilot as gamma  # noqa: E402


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
PUBLIC_IP = "1.1.1.1"


def _resolver(host: str) -> tuple[str, ...]:
    assert host == gamma.GAMMA_HOST
    return (PUBLIC_IP,)


def _exchange(
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    body: bytes = b'[{"id":"event-1"}]',
    peer_ip: str = PUBLIC_IP,
    completed_at: datetime = NOW + timedelta(seconds=1),
):
    def exchange(host: str, target: str, addresses: tuple[str, ...], timeout: float, maximum: int):
        assert host == gamma.GAMMA_HOST
        assert target == "/events?closed=false&limit=3&offset=0"
        assert addresses == (PUBLIC_IP,)
        assert timeout > 0
        assert maximum > 0
        return gamma.ExchangeResponse(status, headers or {}, body, peer_ip, completed_at)

    return exchange


def _proxy_exchange(
    *,
    body: bytes = b'[{"id":"event-1"}]',
    peer_ip: str = "127.0.0.1",
    tls_server_name: str = gamma.GAMMA_HOST,
    connect_authority: str = f"{gamma.GAMMA_HOST}:443",
    connect_status: int = 200,
    connection_mode: str = "EXPLICIT_PROXY",
    proxy_profile_id: str | None = None,
    target_resolution: str = "PROXY",
):
    def exchange(host, target, profile, timeout, maximum):
        assert host == gamma.GAMMA_HOST
        assert target == "/events?closed=false&limit=3&offset=0"
        assert profile.proxy_host == "127.0.0.1"
        assert profile.proxy_port == 7896
        assert timeout > 0
        assert maximum > 0
        return gamma.ExchangeResponse(
            200,
            {},
            body,
            peer_ip,
            NOW + timedelta(seconds=1),
            connection_mode=connection_mode,
            proxy_profile_id=proxy_profile_id or profile.profile_id,
            proxy_dns_answers=("127.0.0.1",),
            connect_authority=connect_authority,
            connect_status=connect_status,
            tls_server_name=tls_server_name,
            target_resolution=target_resolution,
        )

    return exchange


def _authorization(
    policy=None,
    budget: OperationalPilotBudget | None = None,
) -> OperationalPilotAuthorization:
    policy = policy or build_gamma_events_endpoint_policy(
        created_at=NOW,
        max_event_limit=5,
        policy_run_id="read_only_operational_pilot_v1",
    )
    budget = budget or gamma.FIRST_PILOT_BUDGET
    return OperationalPilotAuthorization(
        record_id=stable_record_id("operational_authorization", "gamma-test"),
        run_id=stable_record_id("operational_authorization_run", "gamma-test"),
        created_at=NOW - timedelta(seconds=1),
        source="test",
        source_version="test",
        provenance=(),
        extensions={},
        authorization_id="owner-authorized-20260827",
        preflight_manifest_id="preflight-test",
        preflight_manifest_sha256="a" * 64,
        endpoint_policy_sha256=policy.canonical_sha256,
        budget_sha256=content_sha256(budget),
        authorized_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=10),
    )


def _run(tmp_path: Path, **kwargs: object) -> dict[str, object]:
    isolated_name = f"{tmp_path.name}-{hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]}"
    budget = kwargs.get("budget", gamma.FIRST_PILOT_BUDGET)
    policy = kwargs.get("endpoint_policy") or build_gamma_events_endpoint_policy(
        created_at=NOW,
        max_event_limit=5,
        policy_run_id="read_only_operational_pilot_v1",
    )
    defaults: dict[str, object] = {
        "owner_authorization_id": "owner-authorized-20260827",
        "authorization": _authorization(policy, budget),
        "artifact_root": Path("/tmp/polymarket-alpha-pilot") / isolated_name / "run-1",
        "limit": 3,
        "requested_at": NOW,
        "resolver": _resolver,
        "exchange": _exchange(),
        "environment": {},
        "budget": budget,
        "endpoint_policy": policy,
    }
    defaults.update(kwargs)
    return gamma.execute_gamma_events(**defaults)  # type: ignore[arg-type]


def test_happy_path_authorizes_fixed_route_and_writes_immutable_artifacts(tmp_path: Path) -> None:
    receipt = _run(tmp_path)
    assert receipt["host"] == "gamma-api.polymarket.com"
    assert receipt["path"] == "/events"
    assert receipt["method"] == "GET"
    assert receipt["http_status"] == 200
    assert receipt["event_count"] == 1
    assert receipt["nested_distinct_market_count"] == 0
    assert receipt["dns_answers"] == (PUBLIC_IP,)
    assert receipt["peer_ip"] == PUBLIC_IP
    assert receipt["redirect_count"] == 0
    assert receipt["proxy_environment_cleared"] is True
    assert receipt["auth_headers_present"] is False
    assert Path(str(receipt["raw_artifact_locator"])).read_bytes() == b'[{"id":"event-1"}]'
    assert Path(str(receipt["receipt_artifact_locator"])).is_file()
    security_path = Path(str(receipt["security_receipt_artifact_locator"]))
    assert security_path.is_file()
    assert hashlib.sha256(security_path.read_bytes()).hexdigest() == receipt["security_receipt_sha256"]


def test_expanded_sealed_page_policy_supports_bounded_offset(tmp_path: Path) -> None:
    budget = OperationalPilotBudget(
        max_markets_per_scan=50,
        max_tokens_per_batch=100,
        max_demands_per_minute=50,
        max_network_requests_total=4,
        max_artifact_bytes_total=5_000_000,
        max_runtime_minutes=30,
    )
    policy = build_gamma_events_endpoint_policy(
        created_at=NOW,
        max_event_limit=20,
        policy_run_id="live50-test",
        allowed_offsets=(0, 20),
    )

    def exchange(host: str, target: str, addresses: tuple[str, ...], timeout: float, maximum: int):
        assert host == gamma.GAMMA_HOST
        assert target == "/events?closed=false&limit=20&offset=20"
        assert addresses == (PUBLIC_IP,)
        return gamma.ExchangeResponse(
            200,
            {},
            b'[{"id":"event-20"}]',
            PUBLIC_IP,
            NOW + timedelta(seconds=1),
        )

    receipt = _run(
        tmp_path,
        limit=20,
        offset=20,
        minimum_event_limit=1,
        budget=budget,
        endpoint_policy=policy,
        max_body_bytes=5_000_000,
        exchange=exchange,
    )
    assert receipt["offset"] == 20
    assert receipt["event_count"] == 1

    with pytest.raises(CapabilityDenied, match="offset"):
        _run(
            tmp_path / "wrong-offset",
            limit=20,
            offset=40,
            minimum_event_limit=1,
            budget=budget,
            endpoint_policy=policy,
            max_body_bytes=5_000_000,
            exchange=exchange,
        )


def test_authorization_binds_exact_policy_and_budget(tmp_path: Path) -> None:
    approved_policy = build_gamma_events_endpoint_policy(
        created_at=NOW,
        max_event_limit=5,
        policy_run_id="approved-policy",
    )
    different_policy = build_gamma_events_endpoint_policy(
        created_at=NOW,
        max_event_limit=5,
        policy_run_id="different-policy",
    )
    authorization = _authorization(approved_policy, gamma.FIRST_PILOT_BUDGET)
    with pytest.raises(gamma.GammaPilotDenied, match="policy does not match"):
        _run(
            tmp_path / "policy",
            authorization=authorization,
            endpoint_policy=different_policy,
        )

    different_budget = OperationalPilotBudget(
        max_markets_per_scan=5,
        max_tokens_per_batch=10,
        max_demands_per_minute=5,
        max_network_requests_total=1,
        max_artifact_bytes_total=50_000_000,
        max_runtime_minutes=30,
    )
    with pytest.raises(gamma.GammaPilotDenied, match="budget does not match"):
        _run(
            tmp_path / "budget",
            authorization=authorization,
            endpoint_policy=approved_policy,
            budget=different_budget,
        )


def test_persistent_budget_ledger_blocks_second_distinct_request(tmp_path: Path) -> None:
    budget = OperationalPilotBudget(
        max_markets_per_scan=5,
        max_tokens_per_batch=10,
        max_demands_per_minute=5,
        max_network_requests_total=1,
        max_artifact_bytes_total=50_000_000,
        max_runtime_minutes=30,
    )
    policy = build_gamma_events_endpoint_policy(
        created_at=NOW,
        max_event_limit=5,
        policy_run_id="one-request-only",
        allowed_offsets=(0, 20),
    )
    first = _run(
        tmp_path,
        budget=budget,
        endpoint_policy=policy,
    )
    assert first["offset"] == 0

    def exchange(*_args):
        raise AssertionError("budget denial must precede the second exchange")

    with pytest.raises(gamma.GammaPilotDenied, match="network request budget exceeded"):
        _run(
            tmp_path,
            budget=budget,
            endpoint_policy=policy,
            offset=20,
            exchange=exchange,
        )


def test_cli_loads_sealed_policy_budget_and_offset(tmp_path: Path, monkeypatch) -> None:
    budget = gamma.FIRST_PILOT_BUDGET
    policy = build_gamma_events_endpoint_policy(
        created_at=NOW,
        max_event_limit=5,
        policy_run_id="cli-offset",
        allowed_offsets=(0, 20),
    )
    authorization = _authorization(policy, budget)
    authorization_path = tmp_path / "authorization.json"
    policy_path = tmp_path / "policy.json"
    budget_path = tmp_path / "budget.json"
    authorization_path.write_text(canonical_json(authorization))
    policy_path.write_text(canonical_json(policy))
    budget_path.write_text(canonical_json(budget))
    captured: dict[str, object] = {}

    def fake_execute(**kwargs):
        captured.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(gamma, "execute_gamma_events", fake_execute)
    assert gamma.main(
        [
            "--owner-authorization-id",
            authorization.authorization_id,
            "--authorization-json",
            str(authorization_path),
            "--endpoint-policy-json",
            str(policy_path),
            "--budget-json",
            str(budget_path),
            "--artifact-root",
            "/tmp/polymarket-alpha-pilot/cli-offset-test",
            "--limit",
            "5",
            "--offset",
            "20",
            "--minimum-event-limit",
            "1",
            "--max-body-bytes",
            "12345",
        ]
    ) == 0
    assert captured["offset"] == 20
    assert captured["minimum_event_limit"] == 1
    assert captured["max_body_bytes"] == 12345
    assert captured["endpoint_policy"] == policy
    assert captured["budget"] == budget

@pytest.mark.parametrize("limit", [0, 1, 2, 6, 100])
def test_limit_bypasses_are_not_expressible(tmp_path: Path, limit: int) -> None:
    with pytest.raises(gamma.GammaPilotDenied, match="3-5"):
        _run(tmp_path, limit=limit)


def test_redirect_oversize_dns_peer_mismatch_and_timeout_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(gamma.GammaPilotDenied, match="receipt="):
        _run(tmp_path / "redirect", exchange=_exchange(status=302, headers={"location": "https://evil.example"}))
    with pytest.raises(gamma.GammaPilotDenied, match="receipt="):
        _run(tmp_path / "oversize", exchange=_exchange(body=b"x" * 11), max_body_bytes=10)
    with pytest.raises(gamma.GammaPilotDenied, match="receipt="):
        _run(tmp_path / "peer", exchange=_exchange(peer_ip="8.8.8.8"))
    with pytest.raises(gamma.GammaPilotDenied, match="invalid bounded"):
        _run(tmp_path, timeout_seconds=0)


def test_exchange_failure_is_immutably_receipted_without_claiming_http_response(tmp_path: Path) -> None:
    def failed_exchange(*_args: object) -> gamma.ExchangeResponse:
        raise gamma.GammaPilotDenied("TLS connection failed")

    with pytest.raises(gamma.GammaPilotDenied, match="receipt=") as captured:
        _run(tmp_path, exchange=failed_exchange)
    failure_path = Path(str(captured.value).split("receipt=", 1)[1])
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["status"] == "FAILED_BEFORE_HTTP_RESPONSE"
    assert failure["http_response_received"] is False
    assert failure["dns_answers"] == [PUBLIC_IP]


def test_response_shape_and_event_limit_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(gamma.GammaPilotDenied, match="receipt="):
        _run(tmp_path / "invalid-json", exchange=_exchange(body=b"not-json"))
    with pytest.raises(gamma.GammaPilotDenied, match="receipt="):
        _run(tmp_path / "empty", exchange=_exchange(body=b"[]"))
    with pytest.raises(gamma.GammaPilotDenied, match="receipt="):
        _run(tmp_path / "over-limit", exchange=_exchange(body=b"[{},{},{},{}]"))
    with pytest.raises(gamma.GammaPilotDenied, match="receipt="):
        _run(tmp_path / "missing-event-id", exchange=_exchange(body=b"[{}]"))
    body = b'[{"id":"event-1","markets":[{"id":"m1"},{"marketId":2},{"id":"m1"}]}]'
    receipt = _run(tmp_path, exchange=_exchange(body=body))
    assert receipt["nested_distinct_market_count"] == 2


class _FakeSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)

    def recv(self, _size: int) -> bytes:
        return next(self._chunks, b"")


def test_http_reader_rejects_truncated_or_surplus_content_length() -> None:
    short = _FakeSocket([b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nab"])
    with pytest.raises(gamma.GammaPilotDenied, match="shorter"):
        gamma._read_http_response(short, max_body_bytes=10)  # type: ignore[arg-type]
    long = _FakeSocket([b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nab"])
    with pytest.raises(gamma.GammaPilotDenied, match="exceeds declared"):
        gamma._read_http_response(long, max_body_bytes=10)  # type: ignore[arg-type]


def test_http_reader_decodes_bounded_chunked_and_rejects_complex_framing() -> None:
    good = _FakeSocket(
        [
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
            b"4\r\ntest\r\n3\r\n123\r\n0\r\n\r\n",
        ]
    )
    assert gamma._read_http_response(good, max_body_bytes=7)[2] == b"test123"  # type: ignore[arg-type]
    bad_payloads = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 1\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;x=y\r\na\r\n0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\na\r\n0\r\nX: y\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\na",
    )
    for payload in bad_payloads:
        with pytest.raises(gamma.GammaPilotDenied):
            gamma._read_http_response(_FakeSocket([payload]), max_body_bytes=7)  # type: ignore[arg-type]
    oversize = _FakeSocket(
        [b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n8\r\n12345678\r\n0\r\n\r\n"]
    )
    with pytest.raises(gamma.GammaPilotDenied, match="exceeds"):
        gamma._read_http_response(oversize, max_body_bytes=7)  # type: ignore[arg-type]


def test_exact_retry_is_idempotent_and_different_bytes_conflict(tmp_path: Path) -> None:
    first = _run(tmp_path)
    second = _run(tmp_path)
    assert first["raw_artifact_locator"] == second["raw_artifact_locator"]
    assert first["receipt_sha256"] == second["receipt_sha256"]
    with pytest.raises(gamma.GammaPilotDenied, match="immutable artifact conflict"):
        _run(tmp_path, exchange=_exchange(body=b'[{"id":"event-2"}]'))


@pytest.mark.parametrize("artifact_root", ["relative", "/tmp", "/tmp/polymarket-alpha-pilot", "/tmp/polymarket-alpha-pilot/../escape"])
def test_unsafe_artifact_roots_are_denied(tmp_path: Path, artifact_root: str) -> None:
    with pytest.raises(gamma.GammaPilotDenied, match="artifact root"):
        gamma.execute_gamma_events(
            owner_authorization_id="owner-authorized-20260827",
            authorization=_authorization(),
            artifact_root=artifact_root,
            limit=3,
            requested_at=NOW,
            budget=gamma.FIRST_PILOT_BUDGET,
            endpoint_policy=build_gamma_events_endpoint_policy(
                created_at=NOW,
                max_event_limit=5,
                policy_run_id="read_only_operational_pilot_v1",
            ),
            resolver=_resolver,
            exchange=_exchange(),
            environment={},
        )


def test_proxy_environment_presence_is_rejected_before_authorization(tmp_path: Path) -> None:
    with pytest.raises(gamma.GammaPilotDenied, match="proxy environment"):
        _run(tmp_path, environment={"HTTPS_PROXY": "http://proxy.invalid"})


def test_explicit_proxy_profile_does_not_resolve_target_locally_and_seals_dual_receipt(
    tmp_path: Path,
) -> None:
    profile = build_mac_local_market_proxy_profile(created_at=NOW)

    def forbidden_resolver(_host: str) -> tuple[str, ...]:
        raise AssertionError("proxy mode must not resolve the target locally")

    receipt = _run(
        tmp_path,
        proxy_profile=profile,
        proxy_exchange=_proxy_exchange(),
        resolver=forbidden_resolver,
    )
    assert receipt["connection_mode"] == "EXPLICIT_PROXY"
    assert receipt["dns_answers"] == ()
    assert receipt["proxy_profile_id"] == "mac_local_market_proxy_v1"
    assert receipt["proxy_profile_sha256"] == profile.canonical_sha256
    assert receipt["proxy_dns_answers"] == ("127.0.0.1",)
    assert receipt["proxy_connected_ip"] == "127.0.0.1"
    assert receipt["connect_authority"] == "gamma-api.polymarket.com:443"
    assert receipt["connect_status"] == 200
    assert receipt["target_resolution"] == "PROXY"
    assert receipt["tls_server_name"] == gamma.GAMMA_HOST
    assert receipt["proxy_security_receipt_sha256"]
    security_path = Path(str(receipt["security_receipt_artifact_locator"]))
    security = json.loads(security_path.read_text(encoding="utf-8"))
    assert security["proxy_profile_id"] == profile.profile_id
    assert security["connect_authority"] == "gamma-api.polymarket.com:443"


@pytest.mark.parametrize(
    "proxy_exchange",
    [
        _proxy_exchange(peer_ip="127.0.0.2"),
        _proxy_exchange(tls_server_name="evil.example"),
        _proxy_exchange(connect_authority="evil.example:443"),
        _proxy_exchange(connect_status=407),
        _proxy_exchange(connection_mode="DIRECT"),
        _proxy_exchange(proxy_profile_id="unsealed-profile"),
        _proxy_exchange(target_resolution="DIRECT_DNS"),
    ],
)
def test_proxy_observation_tampering_fails_closed(tmp_path: Path, proxy_exchange) -> None:
    profile = build_mac_local_market_proxy_profile(created_at=NOW)
    with pytest.raises(gamma.GammaPilotDenied, match="receipt=") as captured:
        _run(
            tmp_path,
            proxy_profile=profile,
            proxy_exchange=proxy_exchange,
        )
    failure_path = Path(str(captured.value).split("receipt=", 1)[1])
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["status"] == "FAILED_AFTER_HTTP_RESPONSE"
    assert failure["http_response_received"] is True


def test_connect_response_parser_rejects_auth_redirect_trailing_and_oversize() -> None:
    ok = _FakeSocket([b"HTTP/1.1 200 Connection established\r\nX-Proxy: local\r\n\r\n"])
    assert gamma._read_connect_response(ok)[0] == 200  # type: ignore[arg-type]
    for payload in (
        b"HTTP/1.1 407 Auth\r\nProxy-Authenticate: Basic\r\n\r\n",
        b"HTTP/1.1 302 Redirect\r\nLocation: https://evil.example\r\n\r\n",
        b"HTTP/1.1 200 OK\r\n\r\ntrailing",
    ):
        with pytest.raises(gamma.GammaPilotDenied):
            gamma._read_connect_response(_FakeSocket([payload]))  # type: ignore[arg-type]
    with pytest.raises(gamma.GammaPilotDenied, match="exceed"):
        gamma._read_connect_response(  # type: ignore[arg-type]
            _FakeSocket([b"HTTP/1.1 200 OK\r\nX: " + b"a" * 100]),
            max_header_bytes=32,
        )


def test_sealed_authorization_id_and_expiry_are_enforced(tmp_path: Path) -> None:
    with pytest.raises(gamma.GammaPilotDenied, match="does not match"):
        _run(tmp_path, owner_authorization_id="different-owner")
    expired = _authorization().model_copy(update={"expires_at": NOW})
    with pytest.raises(gamma.GammaPilotDenied, match="not active"):
        _run(tmp_path, authorization=expired)


def test_script_ast_has_no_generic_transport_or_execution_imports() -> None:
    tree = ast.parse((SCRIPT_DIR / "polymarket_alpha_gamma_read_only_pilot.py").read_text(encoding="utf-8"))
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    forbidden = {"requests", "httpx", "aiohttp", "websocket", "subprocess", "importlib"}
    assert not imports & forbidden
    source = (SCRIPT_DIR / "polymarket_alpha_gamma_read_only_pilot.py").read_text(encoding="utf-8")
    assert "order" not in source.lower().replace("border", "")
    assert "signing" not in source.lower()
    assert "--proxy-url" not in source
    assert "MAC_LOCAL_MARKET_PROXY_PROFILE_ID" in source
