"""Offline-only P1 captured resolution source intake tests."""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.polymarket_alpha.contracts import (
    CaptureScope,
    ResolutionAdjudicationStatus,
    ResolutionOutcome,
    bytes_sha256,
)
from src.polymarket_alpha.learning.resolution import build_market_resolution
from src.polymarket_alpha.learning.resolution_intake import (
    CapturedResolutionSource,
    ResolutionIntakeError,
    ResolutionIntakeRequest,
    intake_captured_resolution,
)
from src.polymarket_alpha.security import audit_source_tree
from tests.polymarket_alpha.test_decision_ledger_p0_09b import NOW, _contract


PAYLOAD = b"Agency final resolution: YES."


def _source(**changes: object) -> CapturedResolutionSource:
    values: dict[str, object] = {
        "raw_bytes": PAYLOAD,
        "declared_raw_sha256": bytes_sha256(PAYLOAD),
        "declared_content_length_bytes": len(PAYLOAD),
        "source_name": " Agency ",
        "source_url_or_source_id": "https://agency.example/final",
        "media_type": "text/plain",
        "artifact_locator": "artifact://offline/agency-final-v1",
        "capture_scope": CaptureScope.FULL_DOCUMENT,
        "captured_at": NOW - timedelta(minutes=4),
        "effective_as_of": NOW - timedelta(minutes=9),
        "declared_resolution_source": "agency",
        "declared_precedence_source": "agency",
        "parser_version": "fixture-resolution-parser-v1",
        "parser_assertion": "caller parsed final YES from captured bulletin",
    }
    values.update(changes)
    return CapturedResolutionSource.model_validate(values)


def _request(**changes: object) -> ResolutionIntakeRequest:
    values: dict[str, object] = {
        "source": _source(),
        "market_id": "market-1",
        "expected_condition_id": "condition-1",
        "parsed_condition_id": "condition-1",
        "outcome": ResolutionOutcome.YES,
        "adjudication_status": ResolutionAdjudicationStatus.FINAL,
        "resolved_at": NOW - timedelta(minutes=8),
        "source_observed_at": NOW - timedelta(minutes=3),
        "created_at": NOW,
        "run_id": "resolution-intake-fixture",
    }
    values.update(changes)
    return ResolutionIntakeRequest.model_validate(values)


@pytest.mark.parametrize("outcome", [ResolutionOutcome.YES, ResolutionOutcome.NO])
def test_intake_builds_deterministic_replayable_artifact_and_resolution(outcome) -> None:
    request = _request(outcome=outcome)
    first = intake_captured_resolution(request=request, rule_contract=_contract())
    second = intake_captured_resolution(request=request, rule_contract=_contract())
    assert first == second
    assert first.source_artifact.content_sha256 == bytes_sha256(PAYLOAD)
    assert first.source_artifact.content_length_bytes == len(PAYLOAD)
    assert first.resolution.outcome == outcome
    assert first.resolution.condition_id == "condition-1"
    assert first.resolution.source_artifact_id == first.source_artifact.record_id
    assert first.source_artifact.extensions["resolution_intake"] == {
        "declared_resolution_source": "agency",
        "declared_precedence_source": "agency",
        "parser_version": "fixture-resolution-parser-v1",
        "parser_assertion": "caller parsed final YES from captured bulletin",
        "parsed_condition_id": "condition-1",
        "outcome": outcome.value,
        "adjudication_status": "FINAL",
    }


def test_parser_assertion_is_hash_bound_to_the_resolution_lineage() -> None:
    first = intake_captured_resolution(request=_request(), rule_contract=_contract())
    changed = _request(
        source=_source(parser_assertion="second parser assertion over the same bytes")
    )
    second = intake_captured_resolution(request=changed, rule_contract=_contract())
    assert first.source_artifact.record_id != second.source_artifact.record_id
    assert first.source_artifact.canonical_sha256 != second.source_artifact.canonical_sha256
    assert first.resolution.record_id != second.resolution.record_id


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("declared_raw_sha256", "b" * 64, "declared raw SHA-256"),
        ("declared_content_length_bytes", len(PAYLOAD) + 1, "declared content length"),
    ],
)
def test_intake_rejects_raw_bytes_hash_or_length_tampering(field, value, message) -> None:
    with pytest.raises(ValueError, match=message):
        _source(**{field: value})


def test_intake_revalidates_model_copy_tampering() -> None:
    request = _request()
    tampered_source = request.source.model_copy(update={"declared_raw_sha256": "b" * 64})
    tampered_request = request.model_copy(update={"source": tampered_source})
    with pytest.raises(ResolutionIntakeError, match="invalid frozen ResolutionIntakeRequest"):
        intake_captured_resolution(request=tampered_request, rule_contract=_contract())


@pytest.mark.parametrize(
    ("source_changes", "contract_changes", "message"),
    [
        ({"declared_resolution_source": "agency.example"}, {}, "resolution source is not uniquely authorized"),
        ({"source_name": "agency archive", "declared_resolution_source": "agency"}, {}, "exactly match"),
        ({}, {"source_precedence": ("other",)}, "not uniquely authorized"),
    ],
)
def test_intake_requires_exact_authorized_source_precedence(source_changes, contract_changes, message) -> None:
    contract = type(_contract()).model_validate({**_contract().model_dump(mode="python"), **contract_changes})
    with pytest.raises(ResolutionIntakeError, match=message):
        intake_captured_resolution(request=_request(source=_source(**source_changes)), rule_contract=contract)


def test_resolution_source_and_precedence_labels_may_be_distinct() -> None:
    contract = type(_contract()).model_validate(
        {
            **_contract().model_dump(mode="python"),
            "resolution_sources": ("Example Agency final bulletin",),
            "source_precedence": ("final bulletin", "preliminary report"),
        }
    )
    request = _request(
        source=_source(
            source_name="Example Agency final bulletin",
            declared_resolution_source="Example Agency final bulletin",
            declared_precedence_source="final bulletin",
        )
    )
    result = intake_captured_resolution(request=request, rule_contract=contract)
    assert result.resolution.rule_hash == contract.rule_hash


def test_intake_rejects_reference_only_and_condition_or_clock_errors() -> None:
    with pytest.raises(ValueError, match="replayable captured bytes"):
        _source(capture_scope=CaptureScope.REFERENCE_ONLY)
    with pytest.raises(ValueError, match="condition id"):
        _request(parsed_condition_id="other-condition")
    with pytest.raises(ValueError, match="resolution clocks"):
        _request(resolved_at=NOW - timedelta(minutes=2), source_observed_at=NOW - timedelta(minutes=3))
    with pytest.raises(ValueError, match="timezone-aware"):
        _request(created_at=NOW.replace(tzinfo=None))


def test_intake_fail_closed_pending_invalid_and_market_binding() -> None:
    with pytest.raises(ValueError, match="PENDING_DISPUTE"):
        _request(outcome=ResolutionOutcome.INVALID, adjudication_status=ResolutionAdjudicationStatus.PENDING_DISPUTE)
    with pytest.raises(ResolutionIntakeError, match="market id"):
        intake_captured_resolution(request=_request(market_id="other-market"), rule_contract=_contract())


def test_intake_uses_existing_supersede_binding() -> None:
    contract = _contract()
    prior_artifact = intake_captured_resolution(request=_request(), rule_contract=contract).source_artifact
    prior = build_market_resolution(
        source_artifact=prior_artifact, rule_contract=contract, market_id="market-1", condition_id="condition-1",
        outcome=ResolutionOutcome.NO, adjudication_status=ResolutionAdjudicationStatus.FINAL,
        resolved_at=NOW - timedelta(minutes=9), source_observed_at=NOW - timedelta(minutes=3),
        created_at=NOW, run_id="prior", parser_version="prior-v1",
    )
    correction = intake_captured_resolution(request=_request(supersedes=prior), rule_contract=contract)
    assert correction.resolution.supersedes_resolution_id == prior.record_id
    other_contract = type(contract).model_validate({**contract.model_dump(mode="python"), "market_id": "other-market"})
    wrong = build_market_resolution(
        source_artifact=prior_artifact, rule_contract=other_contract, market_id="other-market", condition_id="condition-1",
        outcome=ResolutionOutcome.NO, adjudication_status=ResolutionAdjudicationStatus.FINAL,
        resolved_at=NOW - timedelta(minutes=9), source_observed_at=NOW - timedelta(minutes=3),
        created_at=NOW, run_id="wrong", parser_version="prior-v1",
    )
    with pytest.raises(ResolutionIntakeError, match="superseded resolution market/rule mismatch"):
        intake_captured_resolution(request=_request(supersedes=wrong), rule_contract=contract)
    other_rule_contract = type(contract).model_validate({**contract.model_dump(mode="python"), "rule_hash": "b" * 64})
    wrong_rule = build_market_resolution(
        source_artifact=prior_artifact, rule_contract=other_rule_contract, market_id="market-1", condition_id="condition-1",
        outcome=ResolutionOutcome.NO, adjudication_status=ResolutionAdjudicationStatus.FINAL,
        resolved_at=NOW - timedelta(minutes=9), source_observed_at=NOW - timedelta(minutes=3),
        created_at=NOW, run_id="wrong-rule", parser_version="prior-v1",
    )
    with pytest.raises(ResolutionIntakeError, match="superseded resolution market/rule mismatch"):
        intake_captured_resolution(request=_request(supersedes=wrong_rule), rule_contract=contract)


def test_resolution_intake_has_no_runtime_capabilities() -> None:
    audit = audit_source_tree("src/polymarket_alpha/learning/resolution_intake.py")
    assert audit.passed, audit.violations
