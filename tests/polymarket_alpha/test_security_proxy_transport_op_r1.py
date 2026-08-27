from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError
import pytest

from src.polymarket_alpha.pilot.operational import build_first_pilot_endpoint_policy
from src.polymarket_alpha.security import (
    AlphaReadOnlyTransport,
    ExplicitProxyProfile,
    HttpMethod,
    ProxyConnectionObservation,
    TransportMode,
    TransportRequest,
    build_mac_local_market_proxy_profile,
    seal_proxy_security_receipt,
)


NOW = datetime(2026, 8, 27, 6, 0, tzinfo=timezone.utc)


def _profile() -> ExplicitProxyProfile:
    return build_mac_local_market_proxy_profile(created_at=NOW)


def _authorization():
    transport = AlphaReadOnlyTransport(
        TransportMode.READ_ONLY,
        build_first_pilot_endpoint_policy(created_at=NOW),
    )
    return transport.authorize(
        TransportRequest(
            method=HttpMethod.GET,
            url="https://gamma-api.polymarket.com/events?closed=false&limit=3&offset=0",
            headers={"Accept": "application/json"},
            requested_at=NOW,
        ),
        environment={},
    )


def _observation(**updates: object) -> ProxyConnectionObservation:
    profile = _profile()
    values: dict[str, object] = {
        "proxy_profile_id": profile.profile_id,
        "proxy_profile_sha256": profile.canonical_sha256,
        "proxy_dns_answers": ("127.0.0.1",),
        "proxy_connected_ip": "127.0.0.1",
        "connect_authority": "gamma-api.polymarket.com:443",
        "connect_status": 200,
        "target_host": "gamma-api.polymarket.com",
        "target_port": 443,
        "target_resolution": "PROXY",
        "tls_server_name": "gamma-api.polymarket.com",
        "target_status_code": 200,
        "completed_at": NOW + timedelta(seconds=1),
    }
    values.update(updates)
    return ProxyConnectionObservation(**values)


def test_released_profile_is_exact_local_unauthenticated_and_deterministic() -> None:
    first = _profile()
    second = _profile()
    assert first == second
    assert first.proxy_host == "127.0.0.1"
    assert first.proxy_port == 7896
    assert first.allowed_connect_hosts == ("gamma-api.polymarket.com",)
    assert first.allowed_connect_port == 443
    assert first.proxy_auth_allowed is False
    assert first.environment_proxy_inheritance is False
    assert first.allows("gamma-api.polymarket.com", 443)
    assert not first.allows("clob.polymarket.com", 443)


@pytest.mark.parametrize(
    "update",
    [
        {"proxy_host": "127.0.0.2"},
        {"proxy_port": 8080},
        {"allowed_connect_hosts": ("evil.example",)},
        {"allowed_connect_port": 80},
        {"proxy_auth_allowed": True},
        {"environment_proxy_inheritance": True},
    ],
)
def test_profile_cannot_be_widened_or_pointed_at_an_arbitrary_proxy(update: dict[str, object]) -> None:
    payload = _profile().model_dump(mode="python")
    with pytest.raises(ValidationError):
        ExplicitProxyProfile.model_validate({**payload, **update})


@pytest.mark.parametrize(
    "update",
    [
        {"proxy_dns_answers": ("8.8.8.8",), "proxy_connected_ip": "8.8.8.8"},
        {"proxy_connected_ip": "127.0.0.2"},
        {"connect_authority": "evil.example:443"},
        {"connect_status": 407},
        {"tls_server_name": "evil.example"},
    ],
)
def test_proxy_observation_rejects_nonlocal_or_mismatched_connect_tls_facts(
    update: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _observation(**update)


def test_proxy_receipt_binds_target_authorization_profile_and_observation() -> None:
    profile = _profile()
    receipt = seal_proxy_security_receipt(
        authorization=_authorization(),
        profile=profile,
        observation=_observation(),
    )
    assert receipt.proxy_profile_sha256 == profile.canonical_sha256
    assert receipt.proxy_connected_ip == "127.0.0.1"
    assert receipt.connect_authority == "gamma-api.polymarket.com:443"
    assert receipt.target_host == "gamma-api.polymarket.com"
    assert receipt.target_path == "/events"
    assert receipt.target_resolution == "PROXY"
    assert receipt.tls_server_name == receipt.target_host

    with pytest.raises(ValueError, match="not allowlisted"):
        seal_proxy_security_receipt(
            authorization=_authorization(),
            profile=profile,
            observation=_observation(
                target_host="evil.example",
                connect_authority="evil.example:443",
                tls_server_name="evil.example",
            ),
        )


def test_proxy_contract_module_has_no_io_or_environment_access() -> None:
    source = Path("src/polymarket_alpha/security/proxy.py").read_text(encoding="utf-8")
    for forbidden in ("import socket", "import ssl", "os.environ", "requests", "httpx"):
        assert forbidden not in source
