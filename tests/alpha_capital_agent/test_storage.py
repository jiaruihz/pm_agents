from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest

from src.alpha_capital_agent.policy import market20_v1_policy
from src.alpha_capital_agent.scheduler import (
    KeysetPage,
    ScanLane,
    build_scan_policy,
    run_keyset_scan,
)
from src.alpha_capital_agent.storage import (
    ACA_MIGRATION_ID,
    ACA_MIGRATION_SQL,
    ACA_SCHEMA_VERSION,
    ACA_V2_MIGRATION_SQL,
    ACA_V2_SCHEMA_VERSION,
    ACA_V3_SCHEMA_VERSION,
    AcaContractConflictError,
    AcaStoredContractCorruptionError,
    CapitalAgentRepository,
)
from src.alpha_capital_agent.universe import (
    AdmissionTrigger,
    EligibilityHistory,
    EligibilityTrigger,
    MarketabilityFacts,
    create_admission_episode,
    evaluate_eligibility,
)
from src.polymarket_alpha.contracts import (
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    canonical_datetime,
    canonical_json,
    stable_record_id,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 29, tzinfo=UTC)
RULES = (
    "This market resolves YES if an official final source verifies the stated "
    "outcome before the deadline, and otherwise resolves to NO under the rules."
)


def _evaluation(at: datetime = NOW):
    identity = MarketIdentity(
        event_id="event",
        market_id="market",
        condition_id="condition",
        yes_token_id="yes-token",
        no_token_id="no-token",
    )
    fields = {
        "run_id": "snapshot-run",
        "created_at": NOW,
        "source": "fixture",
        "source_version": "v1",
        "identity": identity,
        "title": "Storage fixture",
        "question": "Will the storage fixture resolve YES?",
        "slug": "storage-fixture",
        "status": MarketStatus.ACTIVE,
        "end_at": NOW + timedelta(days=10),
        "rules_raw": RULES,
        "volume": Decimal("5000"),
        "liquidity": Decimal("1000"),
        "source_observed_at": NOW,
        "ingested_at": NOW,
    }
    snapshot_id = stable_record_id("storage_fixture_snapshot", fields)
    snapshot = MarketSnapshot(record_id=snapshot_id, **fields)
    facts = MarketabilityFacts(
        market_id="market",
        snapshot_id=snapshot.record_id,
        snapshot_sha256=snapshot.canonical_sha256,
        observed_at=at,
        upstream_updated_at=at,
        accepting_orders=True,
        enable_order_book=True,
        binary_paired=True,
        public_link_available=True,
        restricted=False,
        best_bid=Decimal("0.45"),
        best_ask=Decimal("0.50"),
        spread=Decimal("0.05"),
        liquidity=Decimal("1000"),
        volume=Decimal("5000"),
        executable_depth=Decimal("100"),
        source_artifact_ids=("artifact",),
    )
    return evaluate_eligibility(
        snapshot=snapshot,
        facts=facts,
        policy=market20_v1_policy(),
        history=EligibilityHistory(),
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="storage-evaluation",
    )


def test_repository_migration_atomic_save_replay_and_latest_queries(tmp_path: Path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.db")
    first_manifest = repository.migrate()
    assert repository.migrate() == first_manifest
    evaluation = _evaluation()
    contracts = [evaluation.observation]
    if evaluation.transition is not None:
        contracts.append(evaluation.transition)
    contracts.append(evaluation.next_evaluation)
    hashes = repository.save_contracts_atomic(tuple(contracts))
    assert repository.save_contracts_atomic(tuple(contracts)) == hashes
    assert repository.get_contract(evaluation.observation.record_id) is not None
    assert repository.latest_next_evaluations() == (evaluation.next_evaluation,)

    scan_policy = build_scan_policy(
        policy_name="storage-scan",
        run_id="storage-scan-policy",
        created_at=NOW,
    )
    scan = run_keyset_scan(
        fetch_page=lambda cursor, limit: KeysetPage.of(
            ({"id": "market", "updatedAt": NOW.isoformat()},), None
        ),
        lane=ScanLane.GAMMA_DELTA,
        policy=scan_policy,
        prior_cursor=None,
        scheduled_for=NOW,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
        run_id="storage-scan",
    )
    repository.persist_successful_scan(
        scan_policy, scan.cursor, scan.receipt, scan.selected_items
    )
    claimed = repository.claim_scan_work(owner="storage", now=NOW + timedelta(seconds=2), lease_seconds=30)
    repository.ack_scan_work(work_id=claimed[0].work_id, owner="storage", now=NOW + timedelta(seconds=3))
    assert repository.latest_cursor(ScanLane.GAMMA_DELTA) == scan.cursor


def test_v3_migration_preserves_released_manifests_and_adds_tables(
    tmp_path: Path,
) -> None:
    import hashlib

    path = tmp_path / "aca-v1.db"
    original_applied_at = "2026-08-29T00:00:00Z"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(ACA_MIGRATION_SQL)
        connection.execute(
            "INSERT INTO aca_schema_manifest VALUES (?, ?, ?, ?)",
            (
                ACA_SCHEMA_VERSION,
                ACA_MIGRATION_ID,
                hashlib.sha256(ACA_MIGRATION_SQL.encode("utf-8")).hexdigest(),
                original_applied_at,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    manifest = CapitalAgentRepository(path).migrate()
    assert manifest["schema_version"] == ACA_V3_SCHEMA_VERSION
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT applied_at_utc FROM aca_schema_manifest "
            "WHERE schema_version=?",
            (ACA_SCHEMA_VERSION,),
        ).fetchone() == (original_applied_at,)
        assert {
            row[0]
            for row in connection.execute(
                "SELECT schema_version FROM aca_schema_manifest"
            )
        } == {
            ACA_SCHEMA_VERSION,
            ACA_V2_SCHEMA_VERSION,
            ACA_V3_SCHEMA_VERSION,
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "aca_scan_inbox_run",
            "aca_scan_work_item",
            "aca_scan_committed_cursor",
            "aca_eligibility_history_current",
            "aca_eligibility_history_checkpoint",
            "aca_next_evaluation_current",
            "aca_evaluation_work_item",
            "aca_cadence_work_item",
            "aca_cadence_policy_current",
        } <= tables
    finally:
        connection.close()


def test_v3_migration_backfills_latest_due_schedule_as_claimable_work(
    tmp_path: Path,
) -> None:
    path = tmp_path / "aca-v2-with-schedule.db"
    evaluation = _evaluation()
    observation = evaluation.observation
    schedule = evaluation.next_evaluation
    connection = sqlite3.connect(path)
    try:
        connection.executescript(ACA_MIGRATION_SQL + ACA_V2_MIGRATION_SQL)
        for contract in (observation, schedule):
            connection.execute(
                "INSERT INTO aca_contract_record VALUES (?,?,?,?,?,?)",
                (
                    contract.record_id,
                    type(contract).__name__,
                    contract.schema_version,
                    canonical_json(contract),
                    contract.canonical_sha256,
                    canonical_datetime(contract.created_at),
                ),
            )
        connection.execute(
            "INSERT INTO aca_marketability_observation VALUES (?,?,?,?,?,?,?)",
            (
                observation.record_id,
                observation.market_id,
                canonical_datetime(observation.observed_at),
                observation.prior_state.value,
                int(observation.qualifies),
                int(observation.near_eligible),
                observation.marketability_fingerprint,
            ),
        )
        connection.execute(
            "INSERT INTO aca_next_evaluation VALUES (?,?,?,?,?,?)",
            (
                schedule.record_id,
                schedule.market_id,
                schedule.state.value,
                schedule.tier.value,
                canonical_datetime(schedule.due_at),
                schedule.priority,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    repository = CapitalAgentRepository(path)
    repository.migrate()
    assert repository.latest_next_evaluations() == (schedule,)
    claimed = repository.claim_due_evaluations(
        owner="migration-worker", now=schedule.due_at, lease_seconds=30
    )
    assert len(claimed) == 1
    assert claimed[0].schedule == schedule


def test_repository_persists_admission_episode_without_rewriting_prior_facts(
    tmp_path: Path,
) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.db")
    first = _evaluation()
    # The first qualifying observation is pending hysteresis, but a held
    # position is admitted through an explicit override episode.
    episode = create_admission_episode(
        observation=first.observation,
        policy=market20_v1_policy(),
        trigger=AdmissionTrigger.HELD_POSITION_BOOTSTRAP,
        admitted_at=first.observation.observed_at,
        run_id=first.observation.run_id,
    )
    repository.save_contracts_atomic((first.observation, episode))
    assert repository.get_contract(first.observation.record_id) is not None
    assert repository.get_contract(episode.record_id) is not None
    conn = sqlite3.connect(tmp_path / "aca.db")
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM aca_marketability_observation"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM aca_market_admission_episode"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_repository_rejects_same_id_different_content_and_detects_tamper(
    tmp_path: Path,
) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.db")
    evaluation = _evaluation()
    repository.save_contract(evaluation.observation)
    conflicting = evaluation.observation.model_copy(
        update={"extensions": {"conflict": "different-content"}}
    )
    with pytest.raises(AcaContractConflictError):
        repository.save_contract(conflicting)

    conn = sqlite3.connect(tmp_path / "aca.db")
    try:
        conn.execute(
            "UPDATE aca_contract_record SET canonical_json='{}' WHERE record_id=?",
            (evaluation.observation.record_id,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(AcaStoredContractCorruptionError, match="hash mismatch"):
        repository.get_contract_json(evaluation.observation.record_id)


def test_atomic_group_rolls_back_when_projection_lineage_is_missing(
    tmp_path: Path,
) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.db")
    evaluation = _evaluation()
    assert evaluation.transition is not None
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_contract(evaluation.transition)
    conn = sqlite3.connect(tmp_path / "aca.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM aca_contract_record").fetchone()[0] == 0
    finally:
        conn.close()
