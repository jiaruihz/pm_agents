from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import ast
import hashlib
import json
import sys

import pytest
from src.polymarket_alpha.pilot.operational import OperationalPilotAuthorization
from src.polymarket_alpha.contracts import stable_record_id


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


def _authorization() -> OperationalPilotAuthorization:
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
        authorized_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=10),
    )


def _run(tmp_path: Path, **kwargs: object) -> dict[str, object]:
    isolated_name = f"{tmp_path.name}-{hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]}"
    defaults: dict[str, object] = {
        "owner_authorization_id": "owner-authorized-20260827",
        "authorization": _authorization(),
        "artifact_root": Path("/tmp/polymarket-alpha-pilot") / isolated_name / "run-1",
        "limit": 3,
        "requested_at": NOW,
        "resolver": _resolver,
        "exchange": _exchange(),
        "environment": {},
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


@pytest.mark.parametrize("limit", [0, 1, 2, 6, 100])
def test_limit_bypasses_are_not_expressible(tmp_path: Path, limit: int) -> None:
    with pytest.raises(gamma.GammaPilotDenied, match="3-5"):
        _run(tmp_path, limit=limit)


def test_redirect_oversize_dns_peer_mismatch_and_timeout_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(gamma.GammaPilotDenied, match="redirect"):
        _run(tmp_path, exchange=_exchange(status=302, headers={"location": "https://evil.example"}))
    with pytest.raises(gamma.GammaPilotDenied, match="exceeds"):
        _run(tmp_path, exchange=_exchange(body=b"x" * 11), max_body_bytes=10)
    with pytest.raises(gamma.GammaPilotDenied, match="DNS/peer"):
        _run(tmp_path, exchange=_exchange(peer_ip="8.8.8.8"))
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
    with pytest.raises(gamma.GammaPilotDenied, match="valid JSON"):
        _run(tmp_path, exchange=_exchange(body=b"not-json"))
    with pytest.raises(gamma.GammaPilotDenied, match="1..requested_limit"):
        _run(tmp_path, exchange=_exchange(body=b"[]"))
    with pytest.raises(gamma.GammaPilotDenied, match="1..requested_limit"):
        _run(tmp_path, exchange=_exchange(body=b"[{},{},{},{}]"))
    body = b'[{"markets":[{"id":"m1"},{"marketId":2},{"id":"m1"}]}]'
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
            resolver=_resolver,
            exchange=_exchange(),
            environment={},
        )


def test_proxy_environment_presence_is_rejected_before_authorization(tmp_path: Path) -> None:
    with pytest.raises(gamma.GammaPilotDenied, match="proxy environment"):
        _run(tmp_path, environment={"HTTPS_PROXY": "http://proxy.invalid"})


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
