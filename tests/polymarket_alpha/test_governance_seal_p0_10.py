"""P0-10 offline governance adapter contracts and adversarial cases."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.polymarket_alpha.contracts import content_sha256
from src.polymarket_alpha.governance.seal import (
    OFFLINE_IMPLEMENTATION_ONLY,
    AlphaCompletionReceipt,
    AlphaCoordinatorCertification,
    AlphaEvidencePayload,
    AlphaGovernanceError,
    AlphaSealManifest,
    _work_order_entries,
    build_offline_seal,
    verify_offline_seal,
)
from src.weather_agent_harness.orchestration import DependencyResolver, WorkOrder, WorkStatus


AT = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
RUN_ID = "alpha-p0-offline-fixture-01"


def _orders(*, worker_scope: dict | None = None, coordinator: bool = True) -> tuple[WorkOrder, ...]:
    worker = WorkOrder(
        work_order_id="alpha_decision_ledger",
        objective="build frozen Alpha decision evidence",
        role="terra_worker",
        acceptance=("A01",),
        write_owners=("src/polymarket_alpha/decision",),
        status=WorkStatus.COMPLETE,
        attempt=1,
        owner="terra",
        lease_id="lease-worker",
        scope=worker_scope or {"readiness_scope": OFFLINE_IMPLEMENTATION_ONLY},
        created_at_utc="2026-08-27T09:00:00Z",
    )
    if not coordinator:
        return (worker,)
    return (
        worker,
        WorkOrder(
            work_order_id="alpha_integration_certifier",
            objective="independently certify frozen offline evidence",
            role="integration_coordinator",
            depends_on=(worker.work_order_id,),
            acceptance=("A18",),
            write_owners=("docs/design/polymarket_alpha_p0/P0-12-evidence-seal",),
            status=WorkStatus.COMPLETE,
            attempt=1,
            owner="coordinator",
            lease_id="lease-coordinator",
            scope={"readiness_scope": OFFLINE_IMPLEMENTATION_ONLY},
            created_at_utc="2026-08-27T09:00:00Z",
        ),
    )


def _payloads(orders: tuple[WorkOrder, ...]) -> tuple[AlphaEvidencePayload, ...]:
    return tuple(
        AlphaEvidencePayload(
            uri=f"artifact://{order.work_order_id}.json",
            snapshot_uri=f"snapshot://{order.work_order_id}.json",
            media_type="application/json",
            producer_work_order_id=order.work_order_id,
            producer_attempt=1,
            content=("{\"work_order\":\"" + order.work_order_id + "\"}").encode(),
            created_at_utc="2026-08-27T09:01:00Z",
        )
        for order in orders
    )


def _certification(
    orders: tuple[WorkOrder, ...], payloads: tuple[AlphaEvidencePayload, ...]
) -> AlphaCoordinatorCertification:
    base = {
        "seal_version": "alpha_p0_governance_v1",
        "run_id": RUN_ID,
        "readiness_scope": OFFLINE_IMPLEMENTATION_ONLY,
        "domain_artifact_ids": ("decision:fixture", "prediction:fixture"),
        "work_orders": _work_order_entries(orders),
        "evidence_records": tuple(sorted((item.to_record() for item in payloads), key=lambda item: item.uri)),
        "telemetry": {
            "work_order_count": len(orders),
            "evidence_record_count": len(payloads),
            "unique_write_owner_count": len({owner for order in orders for owner in order.write_owners}),
            "network_requests": 0,
            "schedulers_created": 0,
        },
    }
    manifest_hash = content_sha256(base)
    return AlphaCoordinatorCertification(
        record_id="governance_certification:" + content_sha256({"fixture": "v1"}),
        run_id=RUN_ID,
        created_at=AT,
        source="integration_coordinator",
        source_version="p0_10_fixture",
        certifier_work_order_id="alpha_integration_certifier",
        coordinator_principal="codex-coordinator",
        certified_at=AT,
        manifest_sha256=manifest_hash,
        readiness_scope=OFFLINE_IMPLEMENTATION_ONLY,
    )


def _sealed() -> tuple[AlphaSealManifest, AlphaCompletionReceipt, tuple[AlphaEvidencePayload, ...], AlphaCoordinatorCertification]:
    orders = _orders()
    payloads = _payloads(orders)
    certification = _certification(orders, payloads)
    manifest, receipt = build_offline_seal(
        run_id=RUN_ID,
        work_orders=orders,
        evidence_payloads=payloads,
        domain_artifact_ids=("decision:fixture", "prediction:fixture"),
        certification=certification,
    )
    return manifest, receipt, payloads, certification


def test_offline_seal_is_hash_bound_and_exactly_replayable() -> None:
    manifest, receipt, payloads, certification = _sealed()
    verify_offline_seal(manifest, receipt, evidence_payloads=payloads, certification=certification)
    second_manifest, second_receipt = build_offline_seal(
        run_id=RUN_ID,
        work_orders=_orders(),
        evidence_payloads=_payloads(_orders()),
        domain_artifact_ids=("prediction:fixture", "decision:fixture"),
        certification=_certification(_orders(), _payloads(_orders())),
    )
    assert manifest.manifest_sha256 == second_manifest.manifest_sha256
    assert receipt.receipt_sha256 == second_receipt.receipt_sha256
    assert receipt.completion_status == "CERTIFIED_OFFLINE"
    assert manifest.telemetry["network_requests"] == 0
    assert manifest.telemetry["schedulers_created"] == 0


def test_tampered_or_missing_evidence_fails_closed() -> None:
    manifest, receipt, payloads, certification = _sealed()
    tampered = (*payloads[:-1], AlphaEvidencePayload(**{**payloads[-1].__dict__, "content": b"tampered"}))
    with pytest.raises(AlphaGovernanceError, match="evidence bytes"):
        verify_offline_seal(manifest, receipt, evidence_payloads=tampered, certification=certification)
    with pytest.raises(AlphaGovernanceError, match="missing evidence"):
        build_offline_seal(
            run_id=RUN_ID,
            work_orders=_orders(),
            evidence_payloads=_payloads(_orders())[:-1],
            domain_artifact_ids=("decision:fixture",),
            certification=_certification(_orders(), _payloads(_orders())),
        )


def test_dag_cycle_and_write_owner_overlap_fail_closed() -> None:
    cycle = list(_orders())
    cycle[0] = cycle[0].model_copy(update={"depends_on": (cycle[1].work_order_id,)})
    with pytest.raises(AlphaGovernanceError, match="cycle"):
        build_offline_seal(
            run_id=RUN_ID, work_orders=tuple(cycle), evidence_payloads=_payloads(tuple(cycle)),
            domain_artifact_ids=("decision:fixture",), certification=_certification(_orders(), _payloads(_orders())),
        )
    overlapping = list(_orders())
    overlapping[1] = overlapping[1].model_copy(update={"write_owners": overlapping[0].write_owners})
    with pytest.raises(AlphaGovernanceError, match="write owner overlap"):
        build_offline_seal(
            run_id=RUN_ID, work_orders=tuple(overlapping), evidence_payloads=_payloads(tuple(overlapping)),
            domain_artifact_ids=("decision:fixture",), certification=_certification(_orders(), _payloads(_orders())),
        )


def test_worker_self_complete_cannot_certify_or_escalate_readiness() -> None:
    only_worker = _orders(coordinator=False)
    with pytest.raises(AlphaGovernanceError, match="integration_coordinator"):
        build_offline_seal(
            run_id=RUN_ID, work_orders=only_worker, evidence_payloads=_payloads(only_worker),
            domain_artifact_ids=("decision:fixture",), certification=_certification(_orders(), _payloads(_orders())),
        )
    escalated = _orders(worker_scope={"readiness_scope": "READ_ONLY_OPERATIONAL"})
    with pytest.raises(AlphaGovernanceError, match="readiness escalation"):
        build_offline_seal(
            run_id=RUN_ID, work_orders=escalated, evidence_payloads=_payloads(escalated),
            domain_artifact_ids=("decision:fixture",), certification=_certification(escalated, _payloads(escalated)),
        )

    orders = list(_orders())
    orders[1] = orders[1].model_copy(update={"depends_on": ()})
    with pytest.raises(AlphaGovernanceError, match="depend directly"):
        build_offline_seal(
            run_id=RUN_ID,
            work_orders=tuple(orders),
            evidence_payloads=_payloads(tuple(orders)),
            domain_artifact_ids=("decision:fixture",),
            certification=_certification(tuple(orders), _payloads(tuple(orders))),
        )


def test_existing_harness_dependency_contract_remains_usable() -> None:
    worker, coordinator = _orders()
    refreshed = DependencyResolver().refresh((worker.model_copy(update={"status": WorkStatus.PENDING}), coordinator.model_copy(update={"status": WorkStatus.PENDING})))
    assert refreshed[0].status == WorkStatus.READY
    assert refreshed[1].status == WorkStatus.PENDING


def test_governance_source_has_no_network_process_or_execution_imports() -> None:
    source = Path(__file__).parents[2] / "src/polymarket_alpha/governance/seal.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imports.intersection({"requests", "httpx", "aiohttp", "socket", "websocket", "subprocess", "importlib"})
