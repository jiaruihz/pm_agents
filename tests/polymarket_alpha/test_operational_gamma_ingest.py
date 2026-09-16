"""GLM-OP-01 acceptance tests: captured Gamma /events response ingest.

All inputs are synthetic offline fixtures; no network, no production DB.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

from src.polymarket_alpha.operational import (
    GAMMA_EVENTS_HOST,
    GAMMA_EVENTS_PATH,
    GammaIngestFailureCode,
    GammaResponseReceipt,
    ingest_captured_events_response,
)
from src.polymarket_alpha.storage import AlphaRepository

_SPEC = importlib.util.spec_from_file_location(
    "gamma_payloads", Path(__file__).with_name("fixtures") / "gamma_payloads.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_fixtures = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_fixtures)
market_payload = _fixtures.market_payload

NOW = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
INGESTED = NOW + timedelta(minutes=1)
RUN_ID = "op-gamma-ingest-run"


def _events_body(*events: dict) -> bytes:
    return json.dumps(list(events), sort_keys=True).encode("utf-8")


def _event(event_id: str, markets: list[dict] | None, *, title: str = "Fixture Event") -> dict:
    body: dict = {"id": event_id, "title": title, "slug": f"event-{event_id}"}
    if markets is not None:
        body["markets"] = markets
    return body


def _receipt(body: bytes, **overrides: object) -> GammaResponseReceipt:
    values: dict = {
        "request_method": "GET",
        "endpoint_host": GAMMA_EVENTS_HOST,
        "endpoint_path": GAMMA_EVENTS_PATH,
        "http_status": 200,
        "response_bytes_sha256": hashlib.sha256(body).hexdigest(),
        "response_byte_length": len(body),
        "response_received_at": NOW,
    }
    values.update(overrides)
    return GammaResponseReceipt(**values)


def _repository(tmp_path: Path) -> AlphaRepository:
    repository = AlphaRepository(tmp_path / "alpha.db")
    repository.migrate()
    return repository


def _contract_count(tmp_path: Path) -> int:
    conn = sqlite3.connect(f"file:{tmp_path / 'alpha.db'}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM alpha_contract_record").fetchone()[0]
    finally:
        conn.close()


def _ingest(repository, body: bytes, *, budget: int = 20, receipt: GammaResponseReceipt | None = None, **overrides):
    return ingest_captured_events_response(
        repository,
        raw_response=body,
        response_receipt=receipt or _receipt(body),
        run_id=RUN_ID,
        observed_at=NOW,
        ingested_at=INGESTED,
        page_budget=budget,
        **overrides,
    )


def test_nested_events_flatten_with_parent_event_ids(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    body = _events_body(
        _event(
            "event-parent",
            [
                market_payload(
                    id="m-1",
                    question="Will A happen?",
                    conditionId="0xcond-m-1",
                    clobTokenIds=json.dumps(["tok-yes-1", "tok-no-1"]),
                ),
                market_payload(
                    id="m-2",
                    question="Will B happen?",
                    conditionId="0xcond-m-2",
                    clobTokenIds=json.dumps(["tok-yes-2", "tok-no-2"]),
                ),
            ],
        )
    )
    result = _ingest(repository, body)
    assert result.accepted and result.failure is None
    assert result.flattened_market_count == 2
    assert result.event_ids == ("event-parent",)
    catalog = result.catalog
    assert catalog is not None
    assert catalog.counts()["snapshots"] == 2
    for snapshot in catalog.snapshots:
        assert "event-parent" in snapshot.extensions["gamma_event_ids"]
        # the market fixture carries its own event 777; both must survive
        assert "777" in snapshot.extensions["gamma_event_ids"]
        assert snapshot.identity.event_id == "777"
    assert _contract_count(tmp_path) > 0


def test_multiple_events_each_keep_their_own_parent_identity(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    body = _events_body(
        _event(
            "event-a",
            [
                market_payload(
                    id="m-1",
                    question="Will A happen?",
                    conditionId="0xcond-m-1",
                    clobTokenIds=json.dumps(["tok-yes-1", "tok-no-1"]),
                )
            ],
        ),
        _event(
            "event-b",
            [
                market_payload(
                    id="m-2",
                    question="Will B happen?",
                    conditionId="0xcond-m-2",
                    clobTokenIds=json.dumps(["tok-yes-2", "tok-no-2"]),
                )
            ],
        ),
    )
    result = _ingest(repository, body)
    assert result.accepted
    assert sorted(result.event_ids) == ["event-a", "event-b"]
    by_market = {item.identity.market_id: item for item in result.catalog.snapshots}
    assert "event-a" in by_market["m-1"].extensions["gamma_event_ids"]
    assert "event-b" in by_market["m-2"].extensions["gamma_event_ids"]
    assert "event-a" not in by_market["m-2"].extensions["gamma_event_ids"]


def test_event_without_markets_is_accepted_with_zero_snapshots(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    body = _events_body(
        _event("event-empty", None),
        _event("event-zero", []),
    )
    result = _ingest(repository, body)
    assert result.accepted
    assert result.flattened_market_count == 0
    assert result.catalog is not None
    assert result.catalog.counts()["snapshots"] == 0
    assert result.catalog.counts()["pages"] == 1


def test_schema_drift_is_reported_but_market_still_normalized(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    drifted = market_payload(id="m-drift")
    drifted["brandNewTopLevelField"] = "unknown-to-adapter"
    body = _events_body(_event("event-drift", [drifted]))
    result = _ingest(repository, body)
    assert result.accepted
    catalog = result.catalog
    assert catalog is not None
    assert catalog.drift_receipts and catalog.drift_receipts[0].unknown_fields == (
        "brandNewTopLevelField",
    )
    assert catalog.counts()["snapshots"] == 1


def test_malformed_json_fails_closed_with_zero_writes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    result = _ingest(repository, b"{not-json")
    assert not result.accepted
    assert result.failure is not None
    assert result.failure.code == GammaIngestFailureCode.MALFORMED_JSON
    assert _contract_count(tmp_path) == 0


def test_non_list_payload_fails_closed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    result = _ingest(repository, json.dumps({"data": []}).encode())
    assert result.failure is not None
    assert result.failure.code == GammaIngestFailureCode.PAYLOAD_NOT_LIST
    assert _contract_count(tmp_path) == 0


def test_event_and_nested_market_shape_violations_fail_closed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    bad_event = _ingest(repository, _events_body(["not-a-mapping"]))
    assert bad_event.failure is not None
    assert bad_event.failure.code == GammaIngestFailureCode.EVENT_NOT_MAPPING

    bad_markets_field = _ingest(
        repository, _events_body({"id": "e", "markets": {"not": "a list"}})
    )
    assert bad_markets_field.failure is not None
    assert bad_markets_field.failure.code == GammaIngestFailureCode.MARKETS_FIELD_INVALID

    bad_nested = _ingest(repository, _events_body(_event("e", ["plain-string"])))
    assert bad_nested.failure is not None
    assert bad_nested.failure.code == GammaIngestFailureCode.NESTED_MARKET_NOT_MAPPING
    assert _contract_count(tmp_path) == 0


def test_receipt_hash_length_status_and_endpoint_mismatches_fail(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    body = _events_body(_event("e", [market_payload()]))
    wrong_hash = _ingest(
        repository,
        body,
        receipt=_receipt(body, response_bytes_sha256="f" * 64),
    )
    assert wrong_hash.failure is not None
    assert wrong_hash.failure.code == GammaIngestFailureCode.RECEIPT_HASH_MISMATCH

    wrong_length = _ingest(repository, body, receipt=_receipt(body, response_byte_length=1))
    assert wrong_length.failure is not None
    assert wrong_length.failure.code == GammaIngestFailureCode.RECEIPT_LENGTH_MISMATCH

    wrong_status = _ingest(repository, body, receipt=_receipt(body, http_status=503))
    assert wrong_status.failure is not None
    assert wrong_status.failure.code == GammaIngestFailureCode.HTTP_STATUS_NOT_OK

    wrong_endpoint = _ingest(
        repository, body, receipt=_receipt(body, endpoint_path="/markets")
    )
    assert wrong_endpoint.failure is not None
    assert wrong_endpoint.failure.code == GammaIngestFailureCode.ENDPOINT_IDENTITY_MISMATCH

    late_observed = ingest_captured_events_response(
        repository,
        raw_response=body,
        response_receipt=_receipt(body),
        run_id=RUN_ID,
        observed_at=NOW - timedelta(seconds=1),
        ingested_at=INGESTED,
        page_budget=5,
    )
    assert late_observed.failure is not None
    assert late_observed.failure.code == GammaIngestFailureCode.RECEIPT_CLOCK_MISMATCH
    assert _contract_count(tmp_path) == 0


def test_oversize_response_over_budget_fails_closed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    body = _events_body(
        _event("e", [market_payload(id=f"m-{i}") for i in range(3)])
    )
    result = _ingest(repository, body, budget=2)
    assert result.failure is not None
    assert result.failure.code == GammaIngestFailureCode.MARKET_COUNT_OVER_BUDGET
    assert _contract_count(tmp_path) == 0


def test_replaying_the_same_inputs_is_idempotent(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    body = _events_body(_event("event-parent", [market_payload()]))
    first = _ingest(repository, body)
    second = _ingest(repository, body)
    assert first.accepted and second.accepted
    assert first.catalog is not None and second.catalog is not None
    assert first.catalog.snapshot_ids == second.catalog.snapshot_ids
    assert first == second


def test_condition_and_token_identity_never_swap(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    reversed_market = market_payload(
        id="m-reversed",
        conditionId="0xcond-reversed",
        outcomes=json.dumps(["No", "Yes"]),
        clobTokenIds=json.dumps(["tok-no-rev", "tok-yes-rev"]),
    )
    body = _events_body(_event("event-rev", [reversed_market]))
    result = _ingest(repository, body)
    assert result.accepted
    snapshot = result.catalog.snapshots[0]
    assert snapshot.identity.yes_token_id == "tok-yes-rev"
    assert snapshot.identity.no_token_id == "tok-no-rev"
    assert snapshot.identity.condition_id == "0xcond-reversed"
    assert snapshot.identity.market_id == "m-reversed"


def test_budget_must_be_positive_and_bytes_required(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    body = _events_body()
    with pytest.raises(ValueError):
        _ingest(repository, body, budget=0)
    with pytest.raises(TypeError):
        _ingest(repository, "not-bytes")  # type: ignore[arg-type]
