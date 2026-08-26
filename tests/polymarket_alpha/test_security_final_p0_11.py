from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.polymarket_alpha.security.proof import (
    REQUIRED_CAPABILITIES,
    CanaryOutcome,
    CapabilityCanaryReceipt,
    EnvironmentPolicyReceipt,
    ProofDecision,
    build_final_offline_proof,
    source_audit_receipt,
)


NOW = datetime(2026, 8, 27, 0, 0, tzinfo=UTC)
ALPHA_ROOT = ROOT / "src" / "polymarket_alpha"


def _canary(capability: str, outcome: CanaryOutcome = CanaryOutcome.DENIED) -> CapabilityCanaryReceipt:
    return CapabilityCanaryReceipt.observed(
        capability=capability,
        outcome=outcome,
        detail="caller-observed offline denial",
        observed_at=NOW,
    )


def _passing_inputs() -> tuple[object, object, tuple[CapabilityCanaryReceipt, ...]]:
    return (
        source_audit_receipt(ALPHA_ROOT, observed_at=NOW),
        EnvironmentPolicyReceipt.observed(
            allowed_keys=("LANG", "PATH", "PYTHONPATH", "TZ"),
            observed_keys=("LANG", "PATH", "PYTHONPATH", "TZ"),
            observed_at=NOW,
        ),
        tuple(_canary(item) for item in sorted(REQUIRED_CAPABILITIES)),
    )


def _proof(*, canaries: tuple[CapabilityCanaryReceipt, ...] | None = None, environment: EnvironmentPolicyReceipt | None = None):
    source_audit, default_environment, default_canaries = _passing_inputs()
    return build_final_offline_proof(
        run_id="run-p0-11-final",
        created_at=NOW,
        source_version="p0-11-final-v1",
        source_audit=source_audit,
        environment=environment or default_environment,
        canaries=default_canaries if canaries is None else canaries,
    )


def test_clean_alpha_tree_and_all_explicit_denials_seal_offline_only() -> None:
    proof = _proof()

    assert proof.passed
    assert proof.decision == ProofDecision.PASS
    assert proof.readiness_scope == "OFFLINE_IMPLEMENTATION_ONLY"
    assert proof.failure_reasons == ()
    assert len(proof.proof_sha256) == 64


@pytest.mark.parametrize(
    ("source", "needle"),
    [
        ("import requests\n", "requests"),
        ("import httpx\n", "httpx"),
        ("import aiohttp\n", "aiohttp"),
        ("import websocket\n", "websocket"),
        ("import socket\n", "raw_socket"),
        ("import importlib\n", "importlib"),
        ("__import__('socket')\n", "builtin_import"),
        ("import runpy\n", "runpy"),
        ("eval('1 + 1')\n", "eval"),
        ("exec('value = 1')\n", "exec"),
        ("import subprocess\n", "subprocess"),
        ("import os\nos.system('never-run')\n", "os_system"),
        ("import py_clob_client\n", "known_execution_import"),
        ("from src.platform.execution import order_client\n", "known_execution_import"),
        ("from src.strategies.rule_lawyer.auto_order import submit\n", "signing_import"),
    ],
)
def test_injected_bypass_sources_make_static_full_tree_proof_fail(
    tmp_path: Path, source: str, needle: str
) -> None:
    tree = tmp_path / "polymarket_alpha"
    shutil.copytree(ALPHA_ROOT, tree)
    (tree / "injected_bypass.py").write_text(source, encoding="utf-8")
    source_audit = source_audit_receipt(tree, observed_at=NOW)
    _, environment, canaries = _passing_inputs()

    proof = build_final_offline_proof(
        run_id="run-injected",
        created_at=NOW,
        source_version="p0-11-final-v1",
        source_audit=source_audit,
        environment=environment,
        canaries=canaries,
    )

    assert not proof.passed
    assert "SOURCE_AUDIT_VIOLATIONS" in proof.failure_reasons
    assert needle  # labels each injected adversarial route in pytest output


def test_missing_skipped_unknown_or_allowed_canary_cannot_pass() -> None:
    _, environment, canaries = _passing_inputs()
    missing = _proof(canaries=canaries[1:])
    skipped = _proof(
        canaries=tuple(
            _canary(item, CanaryOutcome.SKIPPED if item == "process_canary" else CanaryOutcome.DENIED)
            for item in sorted(REQUIRED_CAPABILITIES)
        )
    )
    unknown = _proof(canaries=canaries + (_canary("future_bypass"),))
    allowed = _proof(
        canaries=tuple(
            _canary(item, CanaryOutcome.ALLOWED if item == "network_canary" else CanaryOutcome.DENIED)
            for item in sorted(REQUIRED_CAPABILITIES)
        )
    )

    assert not missing.passed
    assert any(item.startswith("MISSING_CAPABILITY_") for item in missing.failure_reasons)
    assert not skipped.passed
    assert "CANARY_NOT_DENIED_process_canary_SKIPPED" in skipped.failure_reasons
    assert not unknown.passed
    assert "UNKNOWN_CAPABILITY_future_bypass" in unknown.failure_reasons
    assert not allowed.passed
    assert "CANARY_NOT_DENIED_network_canary_ALLOWED" in allowed.failure_reasons

    duplicate = _proof(canaries=canaries + (canaries[0],))
    assert not duplicate.passed
    assert "DUPLICATE_CAPABILITY_RECEIPT" in duplicate.failure_reasons
    # Failed evidence still produces a canonical, independently parseable
    # proof rather than throwing while trying to describe the failure.
    type(duplicate).model_validate(duplicate.model_dump(mode="python"))


def test_secret_or_proxy_environment_cannot_pass() -> None:
    dangerous = EnvironmentPolicyReceipt.observed(
        allowed_keys=("LANG",),
        observed_keys=("HTTP_PROXY", "LANG"),
        observed_at=NOW,
    )

    proof = _proof(environment=dangerous)

    assert not proof.passed
    assert "ENVIRONMENT_POLICY_FAILED" in proof.failure_reasons


def test_tampered_canary_or_proof_is_rejected_or_fails_closed() -> None:
    canary = _canary("requests")
    tampered = canary.model_copy(update={"detail": "changed after observation"})
    _, environment, canaries = _passing_inputs()
    replaced = tuple(tampered if item.capability == "requests" else item for item in canaries)

    proof = _proof(canaries=replaced)
    assert not proof.passed
    assert "TAMPERED_requests" in proof.failure_reasons
    type(proof).model_validate(proof.model_dump(mode="python"))

    good = _proof()
    with pytest.raises(ValidationError, match="proof_sha256"):
        type(good).model_validate(good.model_dump(mode="python") | {"decision": ProofDecision.FAIL})


def test_os_sandbox_canary_is_explicitly_not_run_without_sandbox_exec() -> None:
    # This is intentionally test-only: production proof.py never imports a
    # process or network module.  The raw socket attempt must fail inside the
    # macOS process boundary rather than merely being monkeypatched in Python.
    sandbox_exec = shutil.which("sandbox-exec")
    has_sandbox_exec = platform.system() == "Darwin" and sandbox_exec is not None
    network_outcome = CanaryOutcome.NOT_RUN
    process_outcome = CanaryOutcome.NOT_RUN
    if has_sandbox_exec:
        safe_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(ROOT)}
        network = subprocess.run(
            [
                sandbox_exec,
                "-p",
                "(version 1) (allow default) (deny network*)",
                sys.executable,
                "-c",
                "import socket,sys\n"
                "try:\n"
                " socket.create_connection(('1.1.1.1', 443), timeout=0.2)\n"
                "except PermissionError as exc:\n"
                " print('NETWORK_DENIED', exc.errno); sys.exit(0)\n"
                "except OSError as exc:\n"
                " print('WRONG_OS_ERROR', type(exc).__name__, exc.errno); sys.exit(2)\n"
                "sys.exit(3)\n",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=safe_env,
        )
        assert network.returncode == 0, network.stdout + network.stderr
        assert "NETWORK_DENIED 1" in network.stdout
        network_outcome = CanaryOutcome.DENIED

        process = subprocess.run(
            [sandbox_exec, "-p", "(version 1) (allow default) (deny process-exec*)", sys.executable, "-c", "pass"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=safe_env,
        )
        assert process.returncode == 71
        assert "Operation not permitted" in process.stderr
        process_outcome = CanaryOutcome.DENIED
    proof = _proof(
        canaries=tuple(
            _canary(
                item,
                network_outcome
                if item == "network_canary"
                else process_outcome
                if item == "process_canary"
                else CanaryOutcome.DENIED,
            )
            for item in sorted(REQUIRED_CAPABILITIES)
        )
    )

    if has_sandbox_exec:
        assert proof.passed
    else:
        assert not proof.passed
        assert "CANARY_NOT_DENIED_network_canary_NOT_RUN" in proof.failure_reasons
        assert "CANARY_NOT_DENIED_process_canary_NOT_RUN" in proof.failure_reasons
