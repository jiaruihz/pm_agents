"""Pure authorization boundary for future read-only Alpha network adapters.

This module never opens a socket.  It canonicalizes and authorizes a request,
emits an immutable decision receipt, and validates a separately supplied
connection observation.  The eventual operational adapter must run behind the
OS/process sandbox and may receive only ``AuthorizedRequest`` objects.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import ipaddress
import re
from typing import Any, Literal, Mapping

from pydantic import Field, field_validator, model_validator

from src.polymarket_alpha.contracts import (
    ALPHA_CONTRACT_VERSION,
    AlphaContract,
    CommonEnvelope,
    canonical_json,
    content_sha256,
    stable_record_id,
)
from src.polymarket_alpha.contracts.base import canonical_data, ensure_utc, validate_sha256


class TransportMode(StrEnum):
    OFFLINE = "OFFLINE"
    READ_ONLY = "READ_ONLY"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"


class BodyPolicy(StrEnum):
    NONE = "NONE"
    OBJECT_ARRAY = "OBJECT_ARRAY"


class Decision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"


_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_QUERY_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._~:-]*$")
_URL_RE = re.compile(r"^https://(?P<authority>[^/?#]+)(?P<path>/[^?#]*)?(?:\?(?P<query>[^#]*))?$")
_DANGEROUS_ENV_MARKERS = (
    "ALL_PROXY",
    "API_KEY",
    "AUTH",
    "COOKIE",
    "CREDENTIAL",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "SIGNATURE",
)
_DANGEROUS_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-api-key",
        "x-signature",
    }
)


def _validate_host(value: str) -> str:
    if value != value.lower() or not value.isascii() or not _HOST_RE.fullmatch(value):
        raise ValueError("host must be canonical lowercase ASCII without a port")
    if ".." in value or value.endswith("."):
        raise ValueError("host is not canonical")
    return value


def _validate_path(value: str) -> str:
    if not value.startswith("/") or "//" in value or "\\" in value or "%" in value:
        raise ValueError("path must be an unencoded canonical absolute path")
    if "?" in value or "#" in value or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("path contains noncanonical segments")
    return value


class EndpointRule(AlphaContract):
    rule_id: str
    method: HttpMethod
    host: str
    path: str
    allowed_query_keys: tuple[str, ...] = ()
    required_query_keys: tuple[str, ...] = ()
    body_policy: BodyPolicy = BodyPolicy.NONE
    allowed_body_fields: tuple[str, ...] = ()
    required_body_fields: tuple[str, ...] = ()
    max_batch_items: int = Field(default=0, ge=0, le=1000)
    max_body_bytes: int = Field(default=0, ge=0, le=1_000_000)
    allowed_request_headers: tuple[str, ...] = ("accept", "user-agent")

    @field_validator("host")
    @classmethod
    def host_is_canonical(cls, value: str) -> str:
        return _validate_host(value)

    @field_validator("path")
    @classmethod
    def path_is_canonical(cls, value: str) -> str:
        return _validate_path(value)

    @field_validator(
        "allowed_query_keys",
        "required_query_keys",
        "allowed_body_fields",
        "required_body_fields",
        "allowed_request_headers",
    )
    @classmethod
    def names_are_unique_and_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("policy names must be nonblank and unique")
        return normalized

    @model_validator(mode="after")
    def endpoint_shape_is_consistent(self) -> "EndpointRule":
        if not set(self.required_query_keys) <= set(self.allowed_query_keys):
            raise ValueError("required query keys must be allowed")
        if not set(self.required_body_fields) <= set(self.allowed_body_fields):
            raise ValueError("required body fields must be allowed")
        if _DANGEROUS_HEADERS & set(self.allowed_request_headers):
            raise ValueError("authenticated/cookie/signature headers cannot be allowed")
        if self.body_policy == BodyPolicy.NONE:
            if self.allowed_body_fields or self.required_body_fields or self.max_batch_items or self.max_body_bytes:
                raise ValueError("NONE body policy cannot declare body fields or budgets")
        else:
            if self.method != HttpMethod.POST:
                raise ValueError("request bodies are allowed only on precise POST rules")
            if not self.allowed_body_fields or not self.required_body_fields:
                raise ValueError("OBJECT_ARRAY requires explicit allowed/required fields")
            if self.max_batch_items <= 0 or self.max_body_bytes <= 0:
                raise ValueError("OBJECT_ARRAY requires positive item and byte budgets")
            if "content-type" not in self.allowed_request_headers:
                raise ValueError("body endpoints must explicitly allow content-type")
        return self


class ReadOnlyPolicyArtifact(CommonEnvelope):
    profile: Literal[TransportMode.READ_ONLY] = TransportMode.READ_ONLY
    endpoints: tuple[EndpointRule, ...]
    allowed_environment_keys: tuple[str, ...] = ()
    max_redirects: Literal[0] = 0

    @field_validator("allowed_environment_keys")
    @classmethod
    def environment_keys_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("environment allowlist must be nonblank and unique")
        for item in normalized:
            upper = item.upper()
            if any(marker in upper for marker in _DANGEROUS_ENV_MARKERS):
                raise ValueError(f"dangerous environment key cannot be allowed: {item}")
        return normalized

    @model_validator(mode="after")
    def endpoint_keys_are_unique(self) -> "ReadOnlyPolicyArtifact":
        if not self.endpoints:
            raise ValueError("read-only policy requires at least one endpoint")
        rule_ids = [item.rule_id for item in self.endpoints]
        route_keys = [(item.method, item.host, item.path) for item in self.endpoints]
        if len(rule_ids) != len(set(rule_ids)) or len(route_keys) != len(set(route_keys)):
            raise ValueError("endpoint rule ids and route keys must be unique")
        return self


class TransportRequest(AlphaContract):
    method: HttpMethod
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: Any = None
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def requested_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("headers")
    @classmethod
    def headers_are_simple(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
            raise ValueError("headers must be string pairs")
        return value

    @field_validator("json_body")
    @classmethod
    def body_is_canonical(cls, value: Any) -> Any:
        canonical_data(value)
        return value


class AuthorizedRequest(AlphaContract):
    request_sha256: str
    policy_sha256: str
    rule_id: str
    method: HttpMethod
    canonical_url: str
    host: str
    path: str
    query: tuple[tuple[str, str], ...]
    headers: tuple[tuple[str, str], ...]
    json_body: Any = None
    requested_at: datetime

    @field_validator("request_sha256", "policy_sha256")
    @classmethod
    def hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("requested_at")
    @classmethod
    def requested_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class ConnectionObservation(AlphaContract):
    dns_answers: tuple[str, ...]
    connected_ip: str
    status_code: int = Field(ge=100, le=599)
    redirect_url: str | None = None
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def completed_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def connection_is_public_and_observed(self) -> "ConnectionObservation":
        if not self.dns_answers:
            raise ValueError("DNS answers must be recorded")
        parsed = tuple(ipaddress.ip_address(item) for item in self.dns_answers)
        connected = ipaddress.ip_address(self.connected_ip)
        if connected not in parsed:
            raise ValueError("connected_ip must be one of the recorded DNS answers")
        if not all(item.is_global for item in parsed):
            raise ValueError("private, loopback, link-local, reserved, or unspecified targets are forbidden")
        if self.redirect_url is not None:
            raise ValueError("redirects are disabled; each new URL requires a new authorization")
        return self


class SecurityDecisionReceipt(CommonEnvelope):
    mode: TransportMode
    decision: Decision
    reason: str
    request_sha256: str
    policy_sha256: str | None = None
    matched_rule_id: str | None = None
    method: str
    host: str | None = None
    path: str | None = None
    environment_keys: tuple[str, ...] = ()
    dns_answers: tuple[str, ...] = ()
    connected_ip: str | None = None
    status_code: int | None = None
    requested_at: datetime
    completed_at: datetime | None = None

    @field_validator("request_sha256", "policy_sha256")
    @classmethod
    def hashes_are_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("requested_at", "completed_at")
    @classmethod
    def receipt_times_are_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @model_validator(mode="after")
    def completion_is_ordered(self) -> "SecurityDecisionReceipt":
        if self.completed_at is not None and self.completed_at < self.requested_at:
            raise ValueError("completion cannot precede request")
        return self


class AuthorizationResult(AlphaContract):
    authorized_request: AuthorizedRequest
    receipt: SecurityDecisionReceipt


class CapabilityDenied(RuntimeError):
    def __init__(self, receipt: SecurityDecisionReceipt) -> None:
        super().__init__(receipt.reason)
        self.receipt = receipt


def _parse_url(url: str) -> tuple[str, str, tuple[tuple[str, str], ...], str]:
    if not url.isascii() or "%" in url or "\\" in url:
        raise ValueError("URL must be unencoded canonical ASCII")
    match = _URL_RE.fullmatch(url)
    if match is None:
        raise ValueError("only canonical https URLs without fragments are allowed")
    authority = match.group("authority")
    if "@" in authority:
        raise ValueError("URL userinfo is forbidden")
    if authority.endswith(":443"):
        authority = authority[:-4]
    elif ":" in authority:
        raise ValueError("non-default ports are forbidden")
    host = _validate_host(authority.lower())
    path = _validate_path(match.group("path") or "/")
    query_text = match.group("query") or ""
    query: list[tuple[str, str]] = []
    seen: set[str] = set()
    if query_text:
        for component in query_text.split("&"):
            if not component or "=" not in component:
                raise ValueError("query components must use key=value")
            key, value = component.split("=", 1)
            key = key.lower()
            if not _QUERY_COMPONENT_RE.fullmatch(key) or not _QUERY_COMPONENT_RE.fullmatch(value):
                raise ValueError("query contains noncanonical characters")
            if key in seen:
                raise ValueError("duplicate query keys are forbidden")
            seen.add(key)
            query.append((key, value))
    query_tuple = tuple(sorted(query))
    canonical_url = f"https://{host}{path}"
    if query_tuple:
        canonical_url += "?" + "&".join(f"{key}={value}" for key, value in query_tuple)
    return host, path, query_tuple, canonical_url


def _safe_environment(policy: ReadOnlyPolicyArtifact, environment: Mapping[str, str]) -> tuple[str, ...]:
    allowed = set(policy.allowed_environment_keys)
    keys: list[str] = []
    for key, value in environment.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("environment must contain string pairs")
        upper = key.upper()
        if any(marker in upper for marker in _DANGEROUS_ENV_MARKERS):
            raise ValueError(f"proxy/auth/secret environment is forbidden: {key}")
        if key not in allowed:
            raise ValueError(f"environment key is not allowlisted: {key}")
        keys.append(key)
    return tuple(sorted(keys))


def _safe_headers(rule: EndpointRule, headers: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_key, raw_value in headers.items():
        key = raw_key.strip().lower()
        value = raw_value.strip()
        if not key or not value or "\n" in value or "\r" in value or key in seen:
            raise ValueError("headers must be unique, nonblank, and single-line")
        if key in _DANGEROUS_HEADERS or key not in set(rule.allowed_request_headers):
            raise ValueError(f"header is not allowlisted: {key}")
        seen.add(key)
        normalized.append((key, value))
    if rule.body_policy != BodyPolicy.NONE:
        header_map = dict(normalized)
        if header_map.get("content-type", "").lower() != "application/json":
            raise ValueError("body endpoint requires content-type application/json")
    return tuple(sorted(normalized))


def _safe_body(rule: EndpointRule, body: Any) -> Any:
    if rule.body_policy == BodyPolicy.NONE:
        if body is not None:
            raise ValueError("request body is forbidden for this endpoint")
        return None
    if not isinstance(body, (list, tuple)) or not body or len(body) > rule.max_batch_items:
        raise ValueError("body must be a nonempty bounded object array")
    normalized: list[dict[str, str]] = []
    allowed = set(rule.allowed_body_fields)
    required = set(rule.required_body_fields)
    for item in body:
        if not isinstance(item, Mapping):
            raise ValueError("each batch item must be an object")
        lowered = {str(key).lower(): value for key, value in item.items()}
        if len(lowered) != len(item) or not set(lowered) <= allowed or not required <= set(lowered):
            raise ValueError("batch item fields do not match endpoint schema")
        if any(not isinstance(value, str) or not value.strip() for value in lowered.values()):
            raise ValueError("batch item values must be nonblank strings")
        normalized.append({key: lowered[key].strip() for key in sorted(lowered)})
    if len(canonical_json(normalized).encode("utf-8")) > rule.max_body_bytes:
        raise ValueError("request body exceeds endpoint byte budget")
    return tuple(normalized)


class AlphaReadOnlyTransport:
    """Fail-closed request authorizer; intentionally contains no I/O adapter."""

    def __init__(self, mode: TransportMode, policy: ReadOnlyPolicyArtifact | None = None) -> None:
        if mode == TransportMode.READ_ONLY and policy is None:
            raise ValueError("READ_ONLY mode requires a sealed policy artifact")
        self.mode = mode
        self.policy = policy

    def _receipt(
        self,
        request: TransportRequest,
        *,
        decision: Decision,
        reason: str,
        request_hash: str,
        host: str | None = None,
        path: str | None = None,
        rule_id: str | None = None,
        environment_keys: tuple[str, ...] = (),
    ) -> SecurityDecisionReceipt:
        policy_hash = self.policy.canonical_sha256 if self.policy is not None else None
        identity = {
            "mode": self.mode,
            "decision": decision,
            "request": request_hash,
            "policy": policy_hash,
            "reason": reason,
        }
        return SecurityDecisionReceipt(
            schema_version=ALPHA_CONTRACT_VERSION,
            record_id=stable_record_id("security_receipt", identity),
            run_id=stable_record_id("security_run", request.requested_at),
            created_at=request.requested_at,
            source="alpha_read_only_transport",
            source_version="p0-11-v1",
            provenance=(),
            extensions={},
            mode=self.mode,
            decision=decision,
            reason=reason,
            request_sha256=request_hash,
            policy_sha256=policy_hash,
            matched_rule_id=rule_id,
            method=request.method.value,
            host=host,
            path=path,
            environment_keys=environment_keys,
            requested_at=request.requested_at,
        )

    def authorize(
        self,
        request: TransportRequest,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> AuthorizationResult:
        request_hash = content_sha256(request)
        if self.mode == TransportMode.OFFLINE:
            receipt = self._receipt(
                request,
                decision=Decision.DENY,
                reason="offline profile denies all network requests",
                request_hash=request_hash,
            )
            raise CapabilityDenied(receipt)
        assert self.policy is not None
        host: str | None = None
        path: str | None = None
        try:
            host, path, query, canonical_url = _parse_url(request.url)
            matching = [
                rule
                for rule in self.policy.endpoints
                if rule.method == request.method and rule.host == host and rule.path == path
            ]
            if len(matching) != 1:
                raise ValueError("host/path/method is not precisely allowlisted")
            rule = matching[0]
            query_keys = {key for key, _ in query}
            if not query_keys <= set(rule.allowed_query_keys):
                raise ValueError("query contains an unallowlisted key")
            if not set(rule.required_query_keys) <= query_keys:
                raise ValueError("query is missing a required key")
            env_keys = _safe_environment(self.policy, environment or {})
            headers = _safe_headers(rule, request.headers)
            body = _safe_body(rule, request.json_body)
            authorized = AuthorizedRequest(
                request_sha256=request_hash,
                policy_sha256=self.policy.canonical_sha256,
                rule_id=rule.rule_id,
                method=request.method,
                canonical_url=canonical_url,
                host=host,
                path=path,
                query=query,
                headers=headers,
                json_body=body,
                requested_at=request.requested_at,
            )
            receipt = self._receipt(
                request,
                decision=Decision.ALLOW,
                reason="request matches sealed read-only endpoint policy",
                request_hash=request_hash,
                host=host,
                path=path,
                rule_id=rule.rule_id,
                environment_keys=env_keys,
            )
            return AuthorizationResult(authorized_request=authorized, receipt=receipt)
        except ValueError as exc:
            receipt = self._receipt(
                request,
                decision=Decision.DENY,
                reason=str(exc),
                request_hash=request_hash,
                host=host,
                path=path,
            )
            raise CapabilityDenied(receipt) from exc

    def complete(
        self,
        authorization: AuthorizationResult,
        observation: ConnectionObservation,
    ) -> SecurityDecisionReceipt:
        if observation.completed_at < authorization.authorized_request.requested_at:
            raise ValueError("completion cannot precede request")
        receipt = authorization.receipt
        return receipt.model_copy(
            update={
                "dns_answers": observation.dns_answers,
                "connected_ip": observation.connected_ip,
                "status_code": observation.status_code,
                "completed_at": observation.completed_at,
            }
        )
