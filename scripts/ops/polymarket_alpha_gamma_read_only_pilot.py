#!/usr/bin/env python3
"""Bounded, capability-authorized Gamma public-read pilot executor.

This is deliberately a one-route operational adapter.  It cannot be pointed
at arbitrary URLs, does not inherit proxy/auth settings, and has no CLOB or
execution capability.  Tests inject DNS and HTTP seams; invoking the CLI is
the only code path that can open a public TLS socket.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
from typing import Callable, Mapping

from src.polymarket_alpha.contracts import canonical_json, content_sha256
from src.polymarket_alpha.pilot.operational import (
    FIRST_PILOT_BUDGET,
    OperationalPilotAuthorization,
    build_first_pilot_endpoint_policy,
)
from src.polymarket_alpha.security import (
    AlphaReadOnlyTransport,
    ConnectionObservation,
    HttpMethod,
    TransportMode,
    TransportRequest,
)


GAMMA_HOST = "gamma-api.polymarket.com"
GAMMA_PATH = "/events"
MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_TIMEOUT_SECONDS = 10.0
_PROXY_ENV = frozenset({"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"})
_AUTH_HEADER_PREFIXES = ("authorization", "cookie", "proxy-authorization", "x-api-key", "x-signature")


class GammaPilotDenied(RuntimeError):
    """A fail-closed pilot boundary rejection."""


@dataclass(frozen=True)
class ExchangeResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    peer_ip: str
    completed_at: datetime


Resolver = Callable[[str], tuple[str, ...]]
Exchange = Callable[[str, str, tuple[str, ...], float, int], ExchangeResponse]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GammaPilotDenied("clock must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _pilot_root(artifact_root: str | Path) -> Path:
    raw = Path(artifact_root)
    if not raw.is_absolute() or ".." in raw.parts:
        raise GammaPilotDenied("artifact root must be an absolute dedicated /tmp child")
    allowed = (Path("/tmp/polymarket-alpha-pilot"), Path("/private/tmp/polymarket-alpha-pilot"))
    if not any(raw != parent and parent in raw.parents for parent in allowed):
        raise GammaPilotDenied("artifact root must be a dedicated child of /tmp/polymarket-alpha-pilot")
    # macOS exposes /tmp as an OS-owned symlink to /private/tmp. Normalize only
    # that known indirection, then inspect every caller-controlled component
    # before returning a path. Path.resolve() alone would hide such symlinks.
    if raw.parts[:3] == ("/", "tmp", "polymarket-alpha-pilot"):
        normalized = Path("/private/tmp").joinpath(*raw.parts[2:])
    else:
        normalized = raw
    controlled_root = Path("/private/tmp/polymarket-alpha-pilot")
    current = controlled_root
    for component in normalized.parts[len(controlled_root.parts) :]:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        if current.is_symlink():
            raise GammaPilotDenied("artifact root must not contain caller-controlled symlinks")
        if not current.is_dir():
            raise GammaPilotDenied("artifact root components must be directories")
        if info.st_mode & 0o022:
            raise GammaPilotDenied("dedicated pilot artifact directories must not be group/world writable")
    resolved = normalized.resolve(strict=False)
    if not any(resolved != parent and parent in resolved.parents for parent in allowed):
        raise GammaPilotDenied("artifact root must resolve below the dedicated pilot root")
    return resolved


def _mkdir_private(path: Path) -> None:
    """Create a pilot-only directory without accepting a symlink component."""

    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            info = current.lstat()
        if current.is_symlink() or not current.is_dir():
            raise GammaPilotDenied("artifact directory must not contain symlinks")
        if info.st_mode & 0o022:
            # Existing world/group writable locations are tolerated only for
            # the OS-owned /tmp ancestors; the dedicated root is private.
            if current.name == "polymarket-alpha-pilot" or current.parent.name == "polymarket-alpha-pilot":
                raise GammaPilotDenied("dedicated pilot artifact directories must not be group/world writable")


def _safe_proxy_environment(environment: Mapping[str, str]) -> None:
    present = {key.upper() for key in environment if key.upper() in _PROXY_ENV}
    if present:
        raise GammaPilotDenied(f"proxy environment is forbidden: {','.join(sorted(present))}")


def _resolve_public(host: str) -> tuple[str, ...]:
    answers = tuple(sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}))
    if not answers:
        raise GammaPilotDenied("DNS returned no addresses")
    return answers


def _read_http_response(tls_socket: ssl.SSLSocket, *, max_body_bytes: int) -> tuple[int, dict[str, str], bytes]:
    received = bytearray()
    header_limit = 32_768
    while b"\r\n\r\n" not in received:
        chunk = tls_socket.recv(4096)
        if not chunk:
            raise GammaPilotDenied("connection closed before HTTP headers")
        received.extend(chunk)
        if len(received) > header_limit:
            raise GammaPilotDenied("HTTP headers exceed pilot limit")
    raw_headers, body = bytes(received).split(b"\r\n\r\n", 1)
    lines = raw_headers.split(b"\r\n")
    try:
        version, status, _reason = lines[0].decode("ascii").split(" ", 2)
        if version != "HTTP/1.1" or not status.isdigit():
            raise ValueError
    except (UnicodeDecodeError, ValueError) as exc:
        raise GammaPilotDenied("invalid HTTP status line") from exc
    headers: dict[str, str] = {}
    for line in lines[1:]:
        try:
            key, value = line.decode("iso-8859-1").split(":", 1)
        except (UnicodeDecodeError, ValueError) as exc:
            raise GammaPilotDenied("invalid HTTP header") from exc
        normalized = key.strip().lower()
        if not normalized or normalized in headers:
            raise GammaPilotDenied("duplicate or blank HTTP header")
        headers[normalized] = value.strip()
    if "transfer-encoding" in headers:
        raise GammaPilotDenied("transfer-encoding is not supported by the bounded pilot")
    declared: int | None = None
    if "content-length" in headers:
        try:
            declared = int(headers["content-length"])
        except ValueError as exc:
            raise GammaPilotDenied("invalid content-length") from exc
        if declared < 0 or declared > max_body_bytes:
            raise GammaPilotDenied("response body exceeds pilot limit")
    if declared is not None:
        if len(body) > declared:
            raise GammaPilotDenied("response body exceeds declared content-length")
        while len(body) < declared:
            chunk = tls_socket.recv(min(65_536, declared - len(body)))
            if not chunk:
                raise GammaPilotDenied("response body is shorter than declared content-length")
            body += chunk
        if len(body) != declared:
            raise GammaPilotDenied("response body length does not match content-length")
    else:
        while True:
            chunk = tls_socket.recv(65536)
            if not chunk:
                break
            body += chunk
            if len(body) > max_body_bytes:
                raise GammaPilotDenied("response body exceeds pilot limit")
    if len(body) > max_body_bytes:
        raise GammaPilotDenied("response body exceeds pilot limit")
    return int(status), headers, body


def _exchange_tls(host: str, request_target: str, addresses: tuple[str, ...], timeout: float, max_body_bytes: int) -> ExchangeResponse:
    """Narrow stdlib-only TLS exchange; the host is fixed by the caller."""

    if host != GAMMA_HOST:
        raise GammaPilotDenied("unexpected TLS host")
    context = ssl.create_default_context()
    last_error: OSError | None = None
    for address in addresses:
        try:
            with socket.create_connection((address, 443), timeout=timeout) as connected:
                peer_ip = str(connected.getpeername()[0])
                with context.wrap_socket(connected, server_hostname=GAMMA_HOST) as secured:
                    request = (
                        f"GET {request_target} HTTP/1.1\r\n"
                        f"Host: {GAMMA_HOST}\r\n"
                        "Accept: application/json\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode("ascii")
                    secured.sendall(request)
                    status, headers, body = _read_http_response(secured, max_body_bytes=max_body_bytes)
                    return ExchangeResponse(status, headers, body, peer_ip, _utc_now())
        except (OSError, ssl.SSLError) as exc:
            last_error = exc
    raise GammaPilotDenied("TLS connection failed") from last_error


def _immutable_write(path: Path, payload: bytes) -> None:
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_fd = os.open(path.parent, directory_flags)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path.name, flags, 0o600, dir_fd=directory_fd)
    except FileExistsError:
        try:
            read_flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                read_flags |= os.O_NOFOLLOW
            existing_fd = os.open(path.name, read_flags, dir_fd=directory_fd)
            with os.fdopen(existing_fd, "rb") as handle:
                existing = handle.read()
            if existing != payload:
                raise GammaPilotDenied("immutable artifact conflict")
            return
        except OSError as exc:
            raise GammaPilotDenied("existing artifact is not a safe regular file") from exc
        finally:
            os.close(directory_fd)
    except BaseException:
        os.close(directory_fd)
        raise
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.unlink(path.name, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
            raise
    os.close(directory_fd)


def _summarize_gamma_events(body: bytes, *, requested_limit: int) -> tuple[int, int]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GammaPilotDenied("Gamma response is not valid JSON") from exc
    if not isinstance(payload, list) or not payload or len(payload) > requested_limit:
        raise GammaPilotDenied("Gamma response must contain 1..requested_limit events")
    market_ids: set[str] = set()
    for event in payload:
        if not isinstance(event, dict):
            raise GammaPilotDenied("Gamma event response entries must be objects")
        markets = event.get("markets", [])
        if markets is None:
            markets = []
        if not isinstance(markets, list):
            raise GammaPilotDenied("Gamma event markets must be a list")
        for market in markets:
            if not isinstance(market, dict):
                raise GammaPilotDenied("Gamma market entries must be objects")
            market_id = market.get("id") or market.get("marketId")
            if isinstance(market_id, (str, int)) and str(market_id).strip():
                market_ids.add(str(market_id).strip())
    return len(payload), len(market_ids)


def execute_gamma_events(
    *,
    owner_authorization_id: str,
    authorization: OperationalPilotAuthorization,
    artifact_root: str | Path,
    limit: int,
    requested_at: datetime,
    transport: AlphaReadOnlyTransport | None = None,
    resolver: Resolver = _resolve_public,
    exchange: Exchange = _exchange_tls,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_body_bytes: int = MAX_RESPONSE_BYTES,
) -> dict[str, object]:
    """Execute exactly the bounded public Gamma events GET, or fail closed."""

    if not owner_authorization_id.strip():
        raise GammaPilotDenied("explicit owner_authorization_id is required")
    if authorization.authorization_id != owner_authorization_id:
        raise GammaPilotDenied("owner_authorization_id does not match sealed authorization")
    if not 3 <= limit <= FIRST_PILOT_BUDGET.max_markets_per_scan:
        raise GammaPilotDenied("limit must be within the first-pilot 3-5 market bound")
    if timeout_seconds <= 0 or max_body_bytes <= 0 or max_body_bytes > FIRST_PILOT_BUDGET.max_artifact_bytes_total:
        raise GammaPilotDenied("invalid bounded timeout or response size")
    requested_at = _ensure_utc(requested_at)
    if not authorization.network_io_authorized or requested_at < authorization.authorized_at or requested_at >= authorization.expires_at:
        raise GammaPilotDenied("sealed operational authorization is not active for this request")
    supplied_environment = dict(os.environ if environment is None else environment)
    _safe_proxy_environment(supplied_environment)
    root = _pilot_root(artifact_root)
    _mkdir_private(root)
    _mkdir_private(root / "raw")
    _mkdir_private(root / "receipts")

    request = TransportRequest(
        method=HttpMethod.GET,
        url=f"https://{GAMMA_HOST}{GAMMA_PATH}?closed=false&limit={limit}&offset=0",
        headers={"Accept": "application/json"},
        requested_at=requested_at,
    )
    frozen_transport = transport or AlphaReadOnlyTransport(
        TransportMode.READ_ONLY, build_first_pilot_endpoint_policy(created_at=requested_at)
    )
    transport_authorization = frozen_transport.authorize(request, environment={})
    if transport_authorization.authorized_request.canonical_url != request.url:
        raise GammaPilotDenied("fixed request did not canonicalize exactly")
    dns_answers = resolver(GAMMA_HOST)
    if not dns_answers:
        raise GammaPilotDenied("DNS returned no answers")
    try:
        response = exchange(
            GAMMA_HOST,
            f"{GAMMA_PATH}?closed=false&limit={limit}&offset=0",
            tuple(dns_answers),
            timeout_seconds,
            max_body_bytes,
        )
    except (GammaPilotDenied, OSError, ssl.SSLError, TimeoutError) as error:
        failure = {
            "schema": "polymarket_alpha_gamma_read_only_failure_v1",
            "status": "FAILED_BEFORE_HTTP_RESPONSE",
            "owner_authorization_id": owner_authorization_id,
            "authorization_record_id": authorization.record_id,
            "preflight_manifest_id": authorization.preflight_manifest_id,
            "preflight_manifest_sha256": authorization.preflight_manifest_sha256,
            "policy_sha256": transport_authorization.authorized_request.policy_sha256,
            "request_sha256": transport_authorization.authorized_request.request_sha256,
            "host": GAMMA_HOST,
            "path": GAMMA_PATH,
            "method": "GET",
            "requested_at": requested_at.isoformat(),
            "dns_answers": tuple(dns_answers),
            "http_response_received": False,
            "error_type": error.__class__.__name__,
            "error": str(error),
            "proxy_environment_cleared": True,
            "auth_headers_present": False,
        }
        failure_bytes = canonical_json(failure).encode("utf-8")
        failure_path = (
            root
            / "receipts"
            / f"{transport_authorization.authorized_request.request_sha256}.failure.json"
        )
        _immutable_write(failure_path, failure_bytes)
        raise GammaPilotDenied(
            f"Gamma exchange failed; receipt={failure_path}"
        ) from error
    completed_at = _ensure_utc(response.completed_at)
    if completed_at < requested_at:
        raise GammaPilotDenied("completion precedes request")
    if completed_at > authorization.expires_at:
        raise GammaPilotDenied("request completed after sealed authorization expired")
    lowered_headers = {key.lower(): value for key, value in response.headers.items()}
    if any(key.lower().startswith(_AUTH_HEADER_PREFIXES) for key in lowered_headers):
        raise GammaPilotDenied("authenticated response header is forbidden")
    if response.status_code != 200 or "location" in lowered_headers or 300 <= response.status_code < 400:
        raise GammaPilotDenied("non-200 or redirect response is forbidden")
    if len(response.body) > max_body_bytes:
        raise GammaPilotDenied("response body exceeds pilot limit")
    event_count, market_count = _summarize_gamma_events(response.body, requested_limit=limit)
    try:
        observation = ConnectionObservation(
            dns_answers=tuple(dns_answers),
            connected_ip=response.peer_ip,
            status_code=response.status_code,
            completed_at=completed_at,
        )
    except ValueError as exc:
        raise GammaPilotDenied("DNS/peer observation failed capability validation") from exc
    security_receipt = frozen_transport.complete(transport_authorization, observation)
    raw_sha256 = hashlib.sha256(response.body).hexdigest()
    raw_path = root / "raw" / f"{transport_authorization.authorized_request.request_sha256}.bin"
    _immutable_write(raw_path, response.body)
    receipt = {
        "schema": "polymarket_alpha_gamma_read_only_pilot_v1",
        "owner_authorization_id": owner_authorization_id,
        "authorization_record_id": authorization.record_id,
        "preflight_manifest_id": authorization.preflight_manifest_id,
        "preflight_manifest_sha256": authorization.preflight_manifest_sha256,
        "authorization_expires_at": authorization.expires_at.isoformat(),
        "policy_sha256": transport_authorization.authorized_request.policy_sha256,
        "request_sha256": transport_authorization.authorized_request.request_sha256,
        "host": GAMMA_HOST,
        "path": GAMMA_PATH,
        "method": "GET",
        "requested_at": requested_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "dns_answers": tuple(dns_answers),
        "peer_ip": response.peer_ip,
        "http_status": response.status_code,
        "raw_bytes_sha256": raw_sha256,
        "raw_bytes_length": len(response.body),
        "event_count": event_count,
        "nested_distinct_market_count": market_count,
        "raw_artifact_locator": str(raw_path),
        "redirect_count": 0,
        "proxy_environment_cleared": True,
        "auth_headers_present": False,
        "security_receipt_sha256": content_sha256(security_receipt),
    }
    receipt_bytes = canonical_json(receipt).encode("utf-8")
    receipt_path = root / "receipts" / f"{transport_authorization.authorized_request.request_sha256}.json"
    _immutable_write(receipt_path, receipt_bytes)
    return {**receipt, "receipt_artifact_locator": str(receipt_path), "receipt_sha256": content_sha256(receipt)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-authorization-id", required=True)
    parser.add_argument("--authorization-json", required=True, help="sealed OperationalPilotAuthorization JSON")
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--limit", required=True, type=int)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    authorization = OperationalPilotAuthorization.model_validate_json(
        Path(args.authorization_json).read_text(encoding="utf-8")
    )
    receipt = execute_gamma_events(
        owner_authorization_id=args.owner_authorization_id,
        authorization=authorization,
        artifact_root=args.artifact_root,
        limit=args.limit,
        requested_at=_utc_now(),
        timeout_seconds=args.timeout_seconds,
    )
    print(canonical_json(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
