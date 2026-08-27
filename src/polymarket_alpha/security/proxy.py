"""Pure contracts for one explicit, controlled local HTTP CONNECT proxy.

The proxy profile is a versioned capability artifact, not an environment
variable or caller-supplied URL.  This module performs no DNS, socket, TLS or
HTTP I/O; the bounded operational adapter supplies observations separately.
"""

from __future__ import annotations

from datetime import datetime
from ipaddress import ip_address
from typing import Literal

from pydantic import Field, field_validator, model_validator

from src.polymarket_alpha.contracts import (
    ALPHA_CONTRACT_VERSION,
    AlphaContract,
    CommonEnvelope,
    content_sha256,
    stable_record_id,
)
from src.polymarket_alpha.contracts.base import ensure_utc, validate_sha256

from .transport import AuthorizationResult, Decision, HttpMethod


MAC_LOCAL_MARKET_PROXY_PROFILE_ID = "mac_local_market_proxy_v1"
MAC_LOCAL_MARKET_PROXY_HOST = "127.0.0.1"
MAC_LOCAL_MARKET_PROXY_PORT = 7896
GAMMA_PUBLIC_HOST = "gamma-api.polymarket.com"


class ExplicitProxyProfile(CommonEnvelope):
    """Sealed allowlist for a local unauthenticated CONNECT proxy."""

    profile_id: Literal["mac_local_market_proxy_v1"]
    scheme: Literal["http"] = "http"
    proxy_host: Literal["127.0.0.1"] = MAC_LOCAL_MARKET_PROXY_HOST
    proxy_port: Literal[7896] = MAC_LOCAL_MARKET_PROXY_PORT
    allowed_connect_hosts: tuple[str, ...] = (GAMMA_PUBLIC_HOST,)
    allowed_connect_port: Literal[443] = 443
    proxy_auth_allowed: Literal[False] = False
    environment_proxy_inheritance: Literal[False] = False
    target_resolution: Literal["PROXY"] = "PROXY"

    @field_validator("allowed_connect_hosts")
    @classmethod
    def connect_hosts_are_exact(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != (GAMMA_PUBLIC_HOST,):
            raise ValueError("proxy profile allows only the bounded Gamma public host")
        return value

    def allows(self, host: str, port: int) -> bool:
        return host in self.allowed_connect_hosts and port == self.allowed_connect_port


class ProxyConnectionObservation(AlphaContract):
    """Observed local-proxy connection plus target CONNECT/TLS facts."""

    proxy_profile_id: str
    proxy_profile_sha256: str
    proxy_dns_answers: tuple[str, ...]
    proxy_connected_ip: str
    connect_authority: str
    connect_status: int = Field(ge=100, le=599)
    target_host: str
    target_port: int = Field(ge=1, le=65535)
    target_resolution: Literal["PROXY"] = "PROXY"
    tls_server_name: str
    target_status_code: int = Field(ge=100, le=599)
    completed_at: datetime

    @field_validator("proxy_profile_sha256")
    @classmethod
    def profile_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("completed_at")
    @classmethod
    def completed_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def proxy_and_target_are_consistent(self) -> "ProxyConnectionObservation":
        if not self.proxy_dns_answers:
            raise ValueError("proxy DNS answers must be recorded")
        parsed = tuple(ip_address(item) for item in self.proxy_dns_answers)
        connected = ip_address(self.proxy_connected_ip)
        if connected not in parsed:
            raise ValueError("proxy_connected_ip must be one of proxy_dns_answers")
        if not all(item.is_loopback for item in parsed):
            raise ValueError("the controlled proxy must resolve only to loopback")
        if self.connect_status != 200:
            raise ValueError("the proxy CONNECT response must be exactly 200")
        expected_authority = f"{self.target_host}:{self.target_port}"
        if self.connect_authority != expected_authority:
            raise ValueError("CONNECT authority does not match the observed target")
        if self.tls_server_name != self.target_host:
            raise ValueError("TLS server name must match the authorized target host")
        return self


class ProxySecurityReceipt(CommonEnvelope):
    """Immutable binding of target authorization to one proxy observation."""

    decision: Literal[Decision.ALLOW] = Decision.ALLOW
    request_sha256: str
    target_authorization_sha256: str
    policy_sha256: str
    proxy_profile_id: str
    proxy_profile_sha256: str
    proxy_host: str
    proxy_port: int
    proxy_dns_answers: tuple[str, ...]
    proxy_connected_ip: str
    connect_authority: str
    connect_status: Literal[200] = 200
    target_host: str
    target_path: str
    target_method: HttpMethod
    target_resolution: Literal["PROXY"] = "PROXY"
    tls_server_name: str
    target_status_code: int = Field(ge=100, le=599)
    requested_at: datetime
    completed_at: datetime

    @field_validator(
        "request_sha256",
        "target_authorization_sha256",
        "policy_sha256",
        "proxy_profile_sha256",
    )
    @classmethod
    def hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("requested_at", "completed_at")
    @classmethod
    def times_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def completion_is_ordered(self) -> "ProxySecurityReceipt":
        if self.completed_at < self.requested_at:
            raise ValueError("proxy completion cannot precede the request")
        return self


def build_mac_local_market_proxy_profile(*, created_at: datetime) -> ExplicitProxyProfile:
    """Return the only released proxy capability; no arbitrary URL input."""

    created_at = ensure_utc(created_at)
    identity = {
        "profile_id": MAC_LOCAL_MARKET_PROXY_PROFILE_ID,
        "proxy_host": MAC_LOCAL_MARKET_PROXY_HOST,
        "proxy_port": MAC_LOCAL_MARKET_PROXY_PORT,
        "allowed_connect_hosts": (GAMMA_PUBLIC_HOST,),
        "allowed_connect_port": 443,
    }
    return ExplicitProxyProfile(
        schema_version=ALPHA_CONTRACT_VERSION,
        record_id=stable_record_id("explicit_proxy_profile", identity),
        run_id=stable_record_id("security_run", "explicit_proxy", created_at),
        created_at=created_at,
        source="alpha_explicit_proxy_policy",
        source_version="op-r1-v1",
        provenance=(),
        extensions={},
        profile_id=MAC_LOCAL_MARKET_PROXY_PROFILE_ID,
    )


def seal_proxy_security_receipt(
    *,
    authorization: AuthorizationResult,
    profile: ExplicitProxyProfile,
    observation: ProxyConnectionObservation,
) -> ProxySecurityReceipt:
    """Bind an allowed target request to the exact proxy/TLS observation."""

    target = authorization.authorized_request
    if authorization.receipt.decision != Decision.ALLOW:
        raise ValueError("proxy receipt requires an allowed target authorization")
    if observation.proxy_profile_id != profile.profile_id:
        raise ValueError("proxy observation profile id mismatch")
    if observation.proxy_profile_sha256 != profile.canonical_sha256:
        raise ValueError("proxy observation profile hash mismatch")
    if observation.proxy_connected_ip != profile.proxy_host:
        raise ValueError("proxy observation did not connect to the sealed proxy host")
    if observation.proxy_dns_answers != (profile.proxy_host,):
        raise ValueError("numeric loopback proxy must have one exact address")
    if not profile.allows(observation.target_host, observation.target_port):
        raise ValueError("proxy target is not allowlisted by the sealed profile")
    if observation.target_host != target.host:
        raise ValueError("proxy target does not match target authorization")
    authorization_sha256 = content_sha256(authorization)
    identity = {
        "request_sha256": target.request_sha256,
        "authorization_sha256": authorization_sha256,
        "profile_sha256": profile.canonical_sha256,
        "observation": observation,
    }
    return ProxySecurityReceipt(
        schema_version=ALPHA_CONTRACT_VERSION,
        record_id=stable_record_id("proxy_security_receipt", identity),
        run_id=authorization.receipt.run_id,
        created_at=observation.completed_at,
        source="alpha_explicit_proxy_transport",
        source_version="op-r1-v1",
        provenance=(),
        extensions={},
        request_sha256=target.request_sha256,
        target_authorization_sha256=authorization_sha256,
        policy_sha256=target.policy_sha256,
        proxy_profile_id=profile.profile_id,
        proxy_profile_sha256=profile.canonical_sha256,
        proxy_host=profile.proxy_host,
        proxy_port=profile.proxy_port,
        proxy_dns_answers=observation.proxy_dns_answers,
        proxy_connected_ip=observation.proxy_connected_ip,
        connect_authority=observation.connect_authority,
        target_host=target.host,
        target_path=target.path,
        target_method=target.method,
        tls_server_name=observation.tls_server_name,
        target_status_code=observation.target_status_code,
        requested_at=target.requested_at,
        completed_at=observation.completed_at,
    )
