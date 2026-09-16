from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError
import pytest

from src.polymarket_alpha.security import (
    AlphaReadOnlyTransport,
    CapabilityDenied,
    ConnectionObservation,
    Decision,
    EndpointRule,
    HttpMethod,
    ReadOnlyPolicyArtifact,
    TransportMode,
    TransportRequest,
)


NOW = datetime(2026, 8, 26, 5, 0, tzinfo=timezone.utc)
POLICY_FIXTURE = Path(__file__).with_name("fixtures") / "security" / "read_only_policy_v1.json"


def _policy() -> ReadOnlyPolicyArtifact:
    return ReadOnlyPolicyArtifact.model_validate_json(POLICY_FIXTURE.read_text(encoding="utf-8"))


def _request(
    *,
    method: HttpMethod = HttpMethod.GET,
    url: str = "https://gamma-api.polymarket.com/events?limit=100&closed=false",
    headers: dict[str, str] | None = None,
    json_body: object = None,
) -> TransportRequest:
    return TransportRequest(
        method=method,
        url=url,
        headers=headers or {},
        json_body=json_body,
        requested_at=NOW,
    )


def test_offline_transport_fails_closed_with_denial_receipt() -> None:
    transport = AlphaReadOnlyTransport(TransportMode.OFFLINE)
    with pytest.raises(CapabilityDenied) as caught:
        transport.authorize(_request())
    receipt = caught.value.receipt
    assert receipt.mode == TransportMode.OFFLINE
    assert receipt.decision == Decision.DENY
    assert receipt.policy_sha256 is None
    assert receipt.dns_answers == ()


def test_read_only_get_is_exactly_authorized_and_canonicalized() -> None:
    transport = AlphaReadOnlyTransport(TransportMode.READ_ONLY, _policy())
    result = transport.authorize(
        _request(headers={"Accept": "application/json"}),
        environment={"SSL_CERT_FILE": "/etc/ssl/cert.pem"},
    )
    assert result.receipt.decision == Decision.ALLOW
    assert result.receipt.matched_rule_id == "gamma_events_public"
    assert result.receipt.environment_keys == ("SSL_CERT_FILE",)
    assert result.authorized_request.canonical_url == (
        "https://gamma-api.polymarket.com/events?closed=false&limit=100"
    )
    assert result.authorized_request.headers == (("accept", "application/json"),)


@pytest.mark.parametrize(
    "url",
    [
        "http://gamma-api.polymarket.com/events?closed=false&limit=100",
        "https://user@gamma-api.polymarket.com/events?closed=false&limit=100",
        "https://gamma-api.polymarket.com:444/events?closed=false&limit=100",
        "https://gamma-api.polymarket.com/%65vents?closed=false&limit=100",
        "https://gamma-api.polymarket.com/a/../events?closed=false&limit=100",
        "https://gamma-api.polymarket.com//events?closed=false&limit=100",
        "https://gamma-api.polymarket.com/events?closed=false&closed=true&limit=100",
        "https://gamma-api.polymarket.com/events?closed=false&limit=100&secret=x",
        "https://gamma-api.polymarket.com/events?closed=false",
        "https://gamma-api.polymarket.com/events?closed=false&limit=100#fragment",
        "https://evil.example/events?closed=false&limit=100",
    ],
)
def test_url_host_path_query_and_method_bypasses_are_denied(url: str) -> None:
    transport = AlphaReadOnlyTransport(TransportMode.READ_ONLY, _policy())
    with pytest.raises(CapabilityDenied) as caught:
        transport.authorize(_request(url=url))
    assert caught.value.receipt.decision == Decision.DENY


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer secret"},
        {"Cookie": "session=secret"},
        {"X-API-Key": "secret"},
        {"X-Unknown": "value"},
        {"Accept": "ok\r\nAuthorization: secret"},
    ],
)
def test_authenticated_unknown_and_injected_headers_are_denied(headers: dict[str, str]) -> None:
    transport = AlphaReadOnlyTransport(TransportMode.READ_ONLY, _policy())
    with pytest.raises(CapabilityDenied):
        transport.authorize(_request(headers=headers))


@pytest.mark.parametrize(
    "environment",
    [
        {"HTTP_PROXY": "http://proxy.invalid"},
        {"HTTPS_PROXY": "http://proxy.invalid"},
        {"ALL_PROXY": "socks://proxy.invalid"},
        {"NO_PROXY": "*"},
        {"CLOB_API_KEY": "secret"},
        {"UNDECLARED": "value"},
    ],
)
def test_proxy_secret_and_unallowlisted_environment_is_denied(
    environment: dict[str, str],
) -> None:
    transport = AlphaReadOnlyTransport(TransportMode.READ_ONLY, _policy())
    with pytest.raises(CapabilityDenied):
        transport.authorize(_request(), environment=environment)


def test_precise_public_post_books_body_is_allowed() -> None:
    transport = AlphaReadOnlyTransport(TransportMode.READ_ONLY, _policy())
    result = transport.authorize(
        _request(
            method=HttpMethod.POST,
            url="https://clob.polymarket.com/books",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json_body=[{"token_id": "yes-token"}, {"token_id": "no-token"}],
        )
    )
    assert result.receipt.decision == Decision.ALLOW
    assert result.receipt.matched_rule_id == "clob_books_public_batch"
    assert result.authorized_request.json_body == (
        {"token_id": "yes-token"},
        {"token_id": "no-token"},
    )


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({"Content-Type": "text/plain"}, [{"token_id": "token"}]),
        ({"Content-Type": "application/json"}, []),
        ({"Content-Type": "application/json"}, [{"token_id": ""}]),
        ({"Content-Type": "application/json"}, [{"token_id": "token", "side": "BUY"}]),
        (
            {"Content-Type": "application/json"},
            [{"token_id": str(index)} for index in range(5)],
        ),
    ],
)
def test_post_body_and_header_schema_mismatch_is_denied(
    headers: dict[str, str], body: object
) -> None:
    transport = AlphaReadOnlyTransport(TransportMode.READ_ONLY, _policy())
    with pytest.raises(CapabilityDenied):
        transport.authorize(
            _request(
                method=HttpMethod.POST,
                url="https://clob.polymarket.com/books",
                headers=headers,
                json_body=body,
            )
        )


def test_connection_receipt_requires_public_dns_target_and_denies_redirect() -> None:
    transport = AlphaReadOnlyTransport(TransportMode.READ_ONLY, _policy())
    authorization = transport.authorize(_request())
    observation = ConnectionObservation(
        dns_answers=("1.1.1.1",),
        connected_ip="1.1.1.1",
        status_code=200,
        completed_at=NOW + timedelta(seconds=1),
    )
    receipt = transport.complete(authorization, observation)
    assert receipt.connected_ip == "1.1.1.1"
    assert receipt.status_code == 200
    with pytest.raises(ValidationError):
        ConnectionObservation(
            dns_answers=("127.0.0.1",),
            connected_ip="127.0.0.1",
            status_code=200,
            completed_at=NOW,
        )
    with pytest.raises(ValidationError):
        ConnectionObservation(
            dns_answers=("1.1.1.1",),
            connected_ip="8.8.8.8",
            status_code=200,
            completed_at=NOW,
        )
    with pytest.raises(ValidationError):
        ConnectionObservation(
            dns_answers=("1.1.1.1",),
            connected_ip="1.1.1.1",
            status_code=302,
            redirect_url="https://gamma-api.polymarket.com/events",
            completed_at=NOW,
        )


def test_policy_cannot_allow_auth_headers_proxy_env_or_broad_body() -> None:
    with pytest.raises(ValidationError):
        EndpointRule(
            rule_id="unsafe",
            method=HttpMethod.GET,
            host="example.org",
            path="/",
            allowed_request_headers=("authorization",),
        )
    base = _policy().model_dump(mode="python")
    with pytest.raises(ValidationError):
        ReadOnlyPolicyArtifact.model_validate(
            {**base, "allowed_environment_keys": ("HTTPS_PROXY",)}
        )
