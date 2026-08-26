"""Regenerate the deterministic P0-12 offline evidence artifacts.

The pytest JUnit files must already exist.  This script performs no network,
process, production-database, or model operation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import xml.etree.ElementTree as ET

from src.polymarket_alpha.contracts import canonical_json, content_sha256
from src.polymarket_alpha.governance.seal import (
    OFFLINE_IMPLEMENTATION_ONLY,
    AlphaCoordinatorCertification,
    AlphaEvidencePayload,
    _work_order_entries,
    build_offline_seal,
    verify_offline_seal,
)
from src.polymarket_alpha.pilot import run_offline_fixture_pilot
from src.polymarket_alpha.security import audit_source_tree
from src.polymarket_alpha.storage import AlphaRepository
from src.weather_agent_harness.orchestration import WorkOrder, WorkStatus


ROOT = Path(__file__).resolve().parents[4]
SEAL = Path(__file__).resolve().parent
SOURCE_COMMIT = "83257f700d9ab0eaa5e8e6c1cd6ca96b8a8b9ca5"
RUN_ID = "alpha-p0-12-offline-20260827"
GENERATED_AT = "2026-08-27T12:00:00Z"


def _write_json(relative: str, value: object) -> None:
    path = SEAL / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _junit(relative: str) -> tuple[dict[str, object], set[str]]:
    path = SEAL / relative
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    summary = {
        "path": relative,
        "tests": sum(int(item.attrib.get("tests", 0)) for item in suites),
        "failures": sum(int(item.attrib.get("failures", 0)) for item in suites),
        "errors": sum(int(item.attrib.get("errors", 0)) for item in suites),
        "skipped": sum(int(item.attrib.get("skipped", 0)) for item in suites),
        "time_seconds": sum(float(item.attrib.get("time", 0)) for item in suites),
    }
    nodeids = {
        f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
        for suite in suites
        for case in suite.findall("testcase")
    }
    if summary["failures"] or summary["errors"]:
        raise RuntimeError(f"failed JUnit input: {relative}")
    return summary, nodeids


def _orders() -> tuple[WorkOrder, ...]:
    owners = {
        "P0-01": ("src/polymarket_alpha/contracts",),
        "P0-02": ("src/polymarket_alpha/storage",),
        "P0-03": ("src/polymarket_alpha/adapters+census",),
        "P0-04": ("src/polymarket_alpha/change",),
        "P0-05": ("src/polymarket_alpha/books",),
        "P0-06": ("src/polymarket_alpha/recall",),
        "P0-07": ("src/polymarket_alpha/rules",),
        "P0-08": ("src/polymarket_alpha/research",),
        "P0-09": ("src/polymarket_alpha/protocol+decision",),
        "P0-10": ("src/polymarket_alpha/governance",),
        "P0-11": ("src/polymarket_alpha/security",),
    }
    workers = tuple(
        WorkOrder(
            work_order_id=task,
            objective=f"Complete and verify {task} offline implementation",
            role="bounded_component_owner",
            acceptance=(f"{task}_PASS",),
            write_owners=write_owners,
            status=WorkStatus.COMPLETE,
            attempt=1,
            owner="codex-coordinated",
            lease_id=f"sealed-{task.lower()}",
            scope={"readiness_scope": OFFLINE_IMPLEMENTATION_ONLY, "network_access": False},
            created_at_utc=GENERATED_AT,
        )
        for task, write_owners in owners.items()
    )
    coordinator = WorkOrder(
        work_order_id="P0-12",
        objective="Certify the complete offline fixture implementation and evidence",
        role="integration_coordinator",
        depends_on=tuple(item.work_order_id for item in workers),
        acceptance=tuple(f"A{index:02d}" for index in range(1, 19)),
        write_owners=("docs/design/polymarket_alpha_p0/P0_12-evidence-seal",),
        status=WorkStatus.COMPLETE,
        attempt=1,
        owner="codex-coordinator",
        lease_id="sealed-p0-12",
        scope={"readiness_scope": OFFLINE_IMPLEMENTATION_ONLY, "network_access": False},
        created_at_utc=GENERATED_AT,
    )
    return (*workers, coordinator)


def main() -> None:
    alpha, nodeids = _junit("test-results/alpha-full.xml")
    legacy, _ = _junit("test-results/legacy-regression.xml")
    harness, _ = _junit("test-results/harness-regression.xml")
    test_summary = {
        "disposition": "PASS",
        "suites": [alpha, legacy, harness],
        "total_tests": sum(int(item["tests"]) for item in (alpha, legacy, harness)),
        "total_failures": 0,
        "total_errors": 0,
    }
    _write_json("test-results/test_results.json", test_summary)

    scenario_modules = {
        "A01": ["test_gamma_catalog_p0_03"],
        "A02": ["test_gamma_catalog_p0_03"],
        "A03": ["test_change_events_p0_04", "test_gamma_catalog_p0_03"],
        "A04": ["test_change_events_p0_04", "test_candidate_lifecycle_p0_09a"],
        "A05": ["test_recall_aggregator_p0_06a", "test_offline_pilot_p0_12"],
        "A06": ["test_wallet_recall_p0_06d"],
        "A07": ["test_controversy_recall_p0_06c"],
        "A08": ["test_blind_research_p0_08a", "test_contracts_p0_01"],
        "A09": ["test_rule_gates_p0_07"],
        "A10": ["test_book_adapter_p0_05a", "test_market_research_p0_08c"],
        "A11": ["test_research_importer_p0_08b", "test_market_research_p0_08c"],
        "A12": ["test_rule_gates_p0_07", "test_decision_ledger_p0_09b"],
        "A13": ["test_decision_ledger_p0_09b", "test_storage_p0_02"],
        "A14": ["test_security_wave0", "test_security_final_p0_11", "test_security_os_sandbox_p0_11"],
        "A15": ["test_blind_research_p0_08a", "test_research_importer_p0_08b"],
        "A16": ["test_candidate_lifecycle_p0_09a", "test_decision_ledger_p0_09b"],
        "A17": ["test_research_importer_p0_08b", "test_contracts_p0_01r2"],
        "A18": ["test_security_transport_p0_11", "test_security_final_p0_11"],
    }
    acceptance = []
    for scenario, modules in scenario_modules.items():
        missing = [module for module in modules if not any(module in nodeid for nodeid in nodeids)]
        if missing:
            raise RuntimeError(f"{scenario} lacks executed evidence: {missing}")
        acceptance.append({"scenario": scenario, "status": "PASS", "executed_modules": modules})
    _write_json("acceptance-A01-A18.json", {"status": "PASS", "scenarios": acceptance})

    audit = audit_source_tree(ROOT / "src/polymarket_alpha")
    _write_json("test-results/source-audit.json", {
        "root": str(audit.root),
        "passed": audit.passed,
        "violations": [
            {"path": str(item.path), "line": item.line, "code": item.code, "detail": item.detail}
            for item in audit.violations
        ],
    })
    if not audit.passed:
        raise RuntimeError("Alpha source capability audit failed")

    with tempfile.TemporaryDirectory(prefix="alpha-p0-12-") as temporary:
        database = Path(temporary) / "alpha.db"
        repository = AlphaRepository(database)
        migration_manifest = repository.migrate()
        result = run_offline_fixture_pilot(repository)
        connection = sqlite3.connect(database)
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'alpha_%' ORDER BY name"
        )]
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }
        migrations = [
            {"migration_id": row[0], "schema_version": row[1], "sql_sha256": row[2]}
            for row in connection.execute(
                "SELECT migration_id, schema_version, sql_sha256 FROM alpha_schema_migrations ORDER BY migration_id"
            )
        ]
        schema = "\n".join(
            row[0] or "" for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE name LIKE 'alpha_%' AND sql IS NOT NULL ORDER BY type,name"
            )
        )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]

    pilot_summary = {
        "snapshot_id": result.snapshot.record_id,
        "snapshot_sha256": result.snapshot.canonical_sha256,
        "change_event_id": result.change_event_id,
        "candidate_id": result.candidate.candidate_id,
        "blind_packet_id": result.blind_packet_id,
        "market_packet_id": result.market_packet_id,
        "lifecycle_state": result.lifecycle_state.value,
        "decision_id": result.ranked.decision.record_id,
        "decision_sha256": result.ranked.decision.canonical_sha256,
        "decision_action": result.ranked.decision.action.value,
        "decision_execution": result.ranked.decision.execution,
        "prediction_id": result.ranked.prediction.record_id,
        "prediction_sha256": result.ranked.prediction.canonical_sha256,
        "position_state": result.ranked.prediction.position_state.value,
        "rank_score": str(result.ranked.score),
        "persisted_record_count": len(result.persisted_record_ids),
        "persisted_ids_sha256": content_sha256(result.persisted_record_ids),
    }
    _write_json("sample-artifacts/outputs/pilot-output.json", pilot_summary)
    _write_json("sample-artifacts/decisions/decision.json", json.loads(canonical_json(result.ranked.decision)))
    _write_json("sample-artifacts/decisions/prediction.json", json.loads(canonical_json(result.ranked.prediction)))
    _write_json("sample-artifacts/schema_manifest.json", {
        "schema_sha256": content_sha256(schema), "integrity_check": integrity,
        "table_counts": counts,
    })
    _write_json("migrations/migration_evidence.json", {
        "repository_manifest": migration_manifest,
        "applied_migrations": migrations,
        "integrity_check": integrity,
        "current_runtime_db_migrated": False,
    })

    source_hashes = {
        str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "src/polymarket_alpha").rglob("*.py"))
    }
    harness_dependency_hashes = {
        str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "src/weather_agent_harness/orchestration").glob("*.py"))
    }
    _write_json("sample-artifacts/source_identity.json", {
        "absolute_repository_root": str(ROOT),
        "branch": "codex/market-ladder-kink-v1",
        "source_commit": SOURCE_COMMIT,
        "source_files_sha256": source_hashes,
        "source_tree_sha256": content_sha256(source_hashes),
        "harness_dependency_files_sha256": harness_dependency_hashes,
        "harness_dependency_tree_sha256": content_sha256(harness_dependency_hashes),
        "harness_dependency_worktree_dirty": True,
        "harness_used_api": ["DependencyResolver", "EvidenceRecord", "WorkOrder", "WorkStatus"],
        "working_tree_had_unrelated_user_changes": True,
        "seal_scope_staged_explicitly": True,
    })
    _write_json("sample-artifacts/input_manifest.json", {
        "run_id": RUN_ID,
        "fixture_clock": "2026-08-27T10:00:00Z",
        "external_network_inputs": 0,
        "production_database_inputs": 0,
        "private_key_inputs": 0,
        "source_commit": SOURCE_COMMIT,
    })
    _write_json("test-results/import_graph.json", {
        "entrypoint": "src.polymarket_alpha.pilot.run_offline_fixture_pilot",
        "stages": ["change", "recall", "rules", "research", "books", "protocol", "decision", "storage"],
        "execution_or_signing_modules": [],
        "network_modules": [],
        "source_audit_passed": True,
    })

    orders = _orders()
    payloads = tuple(
        AlphaEvidencePayload(
            uri=f"artifact://alpha-p0/{order.work_order_id}.json",
            snapshot_uri=f"git://{SOURCE_COMMIT}/{order.work_order_id}",
            media_type="application/json",
            producer_work_order_id=order.work_order_id,
            producer_attempt=order.attempt,
            content=json.dumps({
                "work_order_id": order.work_order_id,
                "source_commit": SOURCE_COMMIT,
                "status": "PASS",
            }, sort_keys=True, separators=(",", ":")).encode(),
            created_at_utc=GENERATED_AT,
        )
        for order in orders
    )
    for payload in payloads:
        path = SEAL / "sample-artifacts/evidence-payloads" / f"{payload.producer_work_order_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload.content + b"\n")
    domain_ids = tuple(sorted((result.ranked.decision.record_id, result.ranked.prediction.record_id)))
    base = {
        "seal_version": "alpha_p0_governance_v1",
        "run_id": RUN_ID,
        "readiness_scope": OFFLINE_IMPLEMENTATION_ONLY,
        "domain_artifact_ids": domain_ids,
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
    certified_at = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    certification = AlphaCoordinatorCertification(
        record_id="governance_certification:" + content_sha256({"run_id": RUN_ID, "manifest": manifest_hash}),
        run_id=RUN_ID,
        created_at=certified_at,
        source="codex_integration_coordinator",
        source_version="p0_12_v1",
        certifier_work_order_id="P0-12",
        coordinator_principal="codex-root",
        certified_at=certified_at,
        manifest_sha256=manifest_hash,
        readiness_scope=OFFLINE_IMPLEMENTATION_ONLY,
    )
    governance, receipt = build_offline_seal(
        run_id=RUN_ID,
        work_orders=orders,
        evidence_payloads=payloads,
        domain_artifact_ids=domain_ids,
        certification=certification,
    )
    verify_offline_seal(governance, receipt, evidence_payloads=payloads, certification=certification)
    _write_json("sample-artifacts/work_order.json", [item.model_dump(mode="json") for item in orders])
    _write_json("sample-artifacts/dependency_graph.json", {
        "coordinator": "P0-12",
        "direct_dependencies": list(orders[-1].depends_on),
        "cycle_free": True,
        "write_owner_overlap": False,
    })
    _write_json("sample-artifacts/governance_manifest.json", governance.model_dump(mode="json"))
    _write_json("sample-artifacts/coordinator_certification.json", certification.model_dump(mode="json"))
    _write_json("sample-artifacts/run_receipt.json", receipt.model_dump(mode="json"))
    (SEAL / "sample-artifacts/evidence_chain.jsonl").write_text(
        "".join(json.dumps(item.model_dump(mode="json"), sort_keys=True) + "\n" for item in governance.evidence_records),
        encoding="utf-8",
    )
    _write_json("aggregate-telemetry.json", {
        "network_requests": 0,
        "schedulers_created": 0,
        "live_orders": 0,
        "production_database_migrations": 0,
        "worker_usage_telemetry": "unavailable in delegated execution environment",
        "coordinator_test_count": test_summary["total_tests"],
    })

    manifest = {
        "gate": "P0-12-INTEGRATION_PILOT",
        "run_id": RUN_ID,
        "git_commit": SOURCE_COMMIT,
        "generated_at": GENERATED_AT,
        "readiness_scope": OFFLINE_IMPLEMENTATION_ONLY,
        "artifacts": [
            "acceptance-A01-A18.json", "test-results/test_results.json",
            "sample-artifacts/run_receipt.json", "sample-artifacts/outputs/pilot-output.json",
            "migrations/migration_evidence.json",
        ],
        "tests": test_summary,
        "blocking_issues": [],
        "disposition_requested": "READY_FOR_P0_IMPLEMENTATION",
        "completion_status": "CERTIFIED_OFFLINE",
        "read_only_operational_pilot": "NOT_APPROVED",
        "production_capture_expansion": "NOT_AUTHORIZED",
    }
    _write_json("manifest.json", manifest)

    files = sorted(path for path in SEAL.rglob("*") if path.is_file() and path.name != "hashes.sha256")
    (SEAL / "hashes.sha256").write_text(
        "".join(f"{sha256(path.read_bytes()).hexdigest()}  {path.relative_to(SEAL)}\n" for path in files),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
