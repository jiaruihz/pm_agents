"""P1-A06 offline source-specific resolution adapter tests."""

from __future__ import annotations

from datetime import timedelta
import json

import pytest

from src.polymarket_alpha.contracts import (
    CaptureScope,
    ResolutionAdjudicationStatus,
    ResolutionOutcome,
    bytes_sha256,
)
from src.polymarket_alpha.learning import (
    CapturedOfficialResolution,
    OfficialJsonResolutionAdapter,
    OfficialKeyValueResolutionAdapter,
    ResolutionAdapterError,
    ResolutionAdapterFailureCode,
    ResolutionSourcePolicy,
    adapt_captured_official_resolution,
)
from src.polymarket_alpha.security import audit_source_tree
from tests.polymarket_alpha.test_decision_ledger_p0_09b import NOW, _contract


RESOLVED_AT = NOW - timedelta(minutes=10)


def _policy(*, shape: str = "json") -> ResolutionSourcePolicy:
    return ResolutionSourcePolicy(
        policy_id=f"agency-{shape}-resolution-v1",
        source_name="Agency",
        source_url_or_source_id="https://agency.example/final",
        media_type=("application/json" if shape == "json" else "text/plain"),
        declared_resolution_source="Agency",
        declared_precedence_source="Agency",
        parser_version=f"agency-{shape}-parser-v1",
    )


def _json_bytes(
    *,
    condition_id: str = "condition-1",
    outcome: str = "YES",
    status: str = "FINAL",
    supersedes: str | None = None,
) -> bytes:
    payload = {
        "condition_id": condition_id,
        "outcome": outcome,
        "adjudication_status": status,
        "resolved_at": RESOLVED_AT.isoformat(),
        "supersedes_resolution_id": supersedes,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _kv_bytes(
    *,
    condition_id: str = "condition-1",
    outcome: str = "YES",
    status: str = "FINAL",
    supersedes: str | None = None,
) -> bytes:
    lines = [
        f"CONDITION_ID: {condition_id}",
        f"OUTCOME: {outcome}",
        f"ADJUDICATION_STATUS: {status}",
        f"RESOLVED_AT: {RESOLVED_AT.isoformat()}",
    ]
    if supersedes is not None:
        lines.append(f"SUPERSEDES_RESOLUTION_ID: {supersedes}")
    return ("\n".join(lines) + "\n").encode()


def _captured(raw: bytes, *, shape: str = "json", supersedes=None, **changes):
    values = {
        "raw_bytes": raw,
        "declared_raw_sha256": bytes_sha256(raw),
        "declared_content_length_bytes": len(raw),
        "source_name": "Agency",
        "source_url_or_source_id": "https://agency.example/final",
        "media_type": "application/json" if shape == "json" else "text/plain",
        "artifact_locator": f"artifact://offline/agency-{shape}-resolution",
        "capture_scope": CaptureScope.FULL_DOCUMENT,
        "captured_at": NOW - timedelta(minutes=4),
        "effective_as_of": RESOLVED_AT,
        "market_id": "market-1",
        "expected_condition_id": "condition-1",
        "source_observed_at": NOW - timedelta(minutes=3),
        "created_at": NOW,
        "run_id": f"agency-{shape}-adapter-fixture",
        "supersedes": supersedes,
    }
    values.update(changes)
    return CapturedOfficialResolution.model_validate(values)


@pytest.mark.parametrize("shape", ["json", "kv"])
def test_two_frozen_source_shapes_emit_canonical_intake(shape: str) -> None:
    raw = _json_bytes() if shape == "json" else _kv_bytes()
    policy = _policy(shape=shape)
    adapter = (
        OfficialJsonResolutionAdapter(policy)
        if shape == "json"
        else OfficialKeyValueResolutionAdapter(policy)
    )
    captured = _captured(raw, shape=shape)
    first = adapt_captured_official_resolution(
        adapter=adapter, captured=captured, rule_contract=_contract()
    )
    second = adapt_captured_official_resolution(
        adapter=adapter, captured=captured, rule_contract=_contract()
    )
    assert first == second
    assert first.resolution.outcome == ResolutionOutcome.YES
    assert first.resolution.condition_id == "condition-1"
    assertion = json.loads(
        first.source_artifact.extensions["resolution_intake"]["parser_assertion"]
    )
    assert assertion["raw_sha256"] == bytes_sha256(raw)
    assert assertion["policy_id"] == policy.policy_id


def test_dispute_is_explicit_and_never_coerced_to_final() -> None:
    raw = _json_bytes(outcome="NO", status="PENDING_DISPUTE")
    result = adapt_captured_official_resolution(
        adapter=OfficialJsonResolutionAdapter(_policy()),
        captured=_captured(raw),
        rule_contract=_contract(),
    )
    assert result.resolution.outcome == ResolutionOutcome.NO
    assert (
        result.resolution.adjudication_status
        == ResolutionAdjudicationStatus.PENDING_DISPUTE
    )


def test_correction_must_bind_exact_superseded_resolution() -> None:
    adapter = OfficialJsonResolutionAdapter(_policy())
    prior = adapt_captured_official_resolution(
        adapter=adapter,
        captured=_captured(_json_bytes(outcome="NO")),
        rule_contract=_contract(),
    ).resolution
    correction_raw = _json_bytes(outcome="YES", supersedes=prior.record_id)
    correction = adapt_captured_official_resolution(
        adapter=adapter,
        captured=_captured(
            correction_raw,
            supersedes=prior,
            run_id="agency-json-correction",
            artifact_locator="artifact://offline/agency-json-correction",
        ),
        rule_contract=_contract(),
    )
    assert correction.resolution.supersedes_resolution_id == prior.record_id

    with pytest.raises(ResolutionAdapterError) as failure:
        adapt_captured_official_resolution(
            adapter=adapter,
            captured=_captured(correction_raw, supersedes=None),
            rule_contract=_contract(),
        )
    assert failure.value.code == ResolutionAdapterFailureCode.SUPERSESSION_MISMATCH


def test_duplicate_or_unknown_fields_are_ambiguous_not_best_effort() -> None:
    duplicate = _kv_bytes() + b"OUTCOME: NO\n"
    with pytest.raises(ResolutionAdapterError) as failure:
        adapt_captured_official_resolution(
            adapter=OfficialKeyValueResolutionAdapter(_policy(shape="kv")),
            captured=_captured(duplicate, shape="kv"),
            rule_contract=_contract(),
        )
    assert failure.value.code == ResolutionAdapterFailureCode.AMBIGUOUS_SOURCE

    unknown = json.loads(_json_bytes())
    unknown["market_closed"] = True
    raw = json.dumps(unknown, sort_keys=True).encode()
    with pytest.raises(ResolutionAdapterError) as failure:
        adapt_captured_official_resolution(
            adapter=OfficialJsonResolutionAdapter(_policy()),
            captured=_captured(raw),
            rule_contract=_contract(),
        )
    assert failure.value.code == ResolutionAdapterFailureCode.AMBIGUOUS_SOURCE


def test_source_condition_and_content_binding_fail_closed() -> None:
    raw = _json_bytes()
    adapter = OfficialJsonResolutionAdapter(_policy())
    with pytest.raises(ResolutionAdapterError) as failure:
        adapt_captured_official_resolution(
            adapter=adapter,
            captured=_captured(
                raw,
                source_name="Gamma",
                source_url_or_source_id="https://gamma-api.polymarket.com/markets/1",
            ),
            rule_contract=_contract(),
        )
    assert failure.value.code == ResolutionAdapterFailureCode.UNSUPPORTED_SOURCE

    wrong_condition = _json_bytes(condition_id="other-condition")
    with pytest.raises(ResolutionAdapterError) as failure:
        adapt_captured_official_resolution(
            adapter=adapter,
            captured=_captured(wrong_condition),
            rule_contract=_contract(),
        )
    assert failure.value.code == ResolutionAdapterFailureCode.CONDITION_MISMATCH

    captured = _captured(raw)
    tampered = captured.model_copy(update={"declared_raw_sha256": "f" * 64})
    with pytest.raises(ResolutionAdapterError) as failure:
        adapt_captured_official_resolution(
            adapter=adapter, captured=tampered, rule_contract=_contract()
        )
    assert failure.value.code == ResolutionAdapterFailureCode.CONTENT_BINDING_MISMATCH


def test_resolution_adapters_have_no_runtime_capabilities() -> None:
    audit = audit_source_tree("src/polymarket_alpha/learning/resolution_adapters.py")
    assert audit.passed, audit.violations
