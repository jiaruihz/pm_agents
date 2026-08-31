"""Separate append-only SQLite repository for ACA plan and scheduler facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import sqlite3
from typing import Any

from src.polymarket_alpha.contracts import (
    ALPHA_CONTRACT_VERSION,
    CommonEnvelope,
    canonical_datetime,
    canonical_json,
    content_sha256,
)
from src.polymarket_alpha.contracts.base import ensure_utc

from .scheduler import (
    CadenceWorkOrder,
    ScanLane,
    UniverseScanCursor,
    UniverseScanPolicy,
    UniverseScanRunReceipt,
    build_cadence_work_order,
    latest_due_cadence_slot,
)
from .universe import (
    EligibilityHistoryCheckpoint,
    EligibilityTransition,
    MarketAdmissionEpisode,
    MarketabilityObservation,
    NextEvaluation,
)


ACA_SCHEMA_VERSION = "aca_v1.0"
ACA_MIGRATION_ID = "aca_0001_append_only_core"
ACA_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS aca_schema_manifest (
    schema_version TEXT PRIMARY KEY,
    migration_id TEXT NOT NULL,
    sql_sha256 TEXT NOT NULL CHECK(length(sql_sha256) = 64),
    applied_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aca_contract_record (
    record_id TEXT PRIMARY KEY,
    contract_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64),
    created_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS aca_contract_type_created_idx
ON aca_contract_record(contract_type, created_at_utc, record_id);
CREATE TABLE IF NOT EXISTS aca_marketability_observation (
    observation_id TEXT PRIMARY KEY REFERENCES aca_contract_record(record_id),
    market_id TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    prior_state TEXT NOT NULL,
    qualifies INTEGER NOT NULL CHECK(qualifies IN (0,1)),
    near_eligible INTEGER NOT NULL CHECK(near_eligible IN (0,1)),
    fingerprint TEXT NOT NULL CHECK(length(fingerprint) = 64)
);
CREATE TABLE IF NOT EXISTS aca_eligibility_transition (
    transition_id TEXT PRIMARY KEY REFERENCES aca_contract_record(record_id),
    market_id TEXT NOT NULL,
    observation_id TEXT NOT NULL REFERENCES aca_marketability_observation(observation_id),
    prior_state TEXT NOT NULL,
    new_state TEXT NOT NULL,
    effective_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aca_next_evaluation (
    schedule_id TEXT PRIMARY KEY REFERENCES aca_contract_record(record_id),
    market_id TEXT NOT NULL,
    state TEXT NOT NULL,
    tier TEXT NOT NULL,
    due_at_utc TEXT NOT NULL,
    priority INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS aca_next_evaluation_due_idx
ON aca_next_evaluation(due_at_utc, priority, market_id);
CREATE TABLE IF NOT EXISTS aca_scan_cursor (
    cursor_id TEXT PRIMARY KEY REFERENCES aca_contract_record(record_id),
    lane TEXT NOT NULL,
    watermark_updated_at_utc TEXT,
    watermark_market_id TEXT,
    coverage_complete INTEGER NOT NULL CHECK(coverage_complete IN (0,1)),
    observed_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS aca_scan_cursor_lane_idx
ON aca_scan_cursor(lane, observed_at_utc, cursor_id);
CREATE TABLE IF NOT EXISTS aca_market_admission_episode (
    episode_id TEXT PRIMARY KEY REFERENCES aca_contract_record(record_id),
    market_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    observation_id TEXT NOT NULL REFERENCES aca_marketability_observation(observation_id),
    candidate_seed_id TEXT NOT NULL UNIQUE,
    admitted_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aca_capital_plan (
    plan_id TEXT PRIMARY KEY REFERENCES aca_contract_record(record_id),
    account_id TEXT NOT NULL,
    decision_as_of_utc TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL CHECK(length(policy_sha256) = 64),
    mode TEXT NOT NULL CHECK(mode = 'READ_ONLY_SHADOW'),
    execution_capability TEXT NOT NULL CHECK(execution_capability = 'NO_ORDER')
);
"""

# Keep v1 above byte-for-byte stable: its digest is part of the released
# append-only contract.  Operational state deliberately lives in v2 tables.
ACA_V2_SCHEMA_VERSION = "aca_v2.0"
ACA_V2_MIGRATION_ID = "aca_0002_scheduler_durable_inbox"
ACA_V2_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS aca_scan_inbox_run (
    receipt_id TEXT PRIMARY KEY REFERENCES aca_contract_record(record_id),
    lane TEXT NOT NULL,
    cursor_id TEXT NOT NULL REFERENCES aca_contract_record(record_id),
    committed_at_utc TEXT
);
CREATE TABLE IF NOT EXISTS aca_scan_work_item (
    work_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES aca_scan_inbox_run(receipt_id),
    cursor_id TEXT NOT NULL REFERENCES aca_contract_record(record_id),
    market_id TEXT NOT NULL,
    item_index INTEGER NOT NULL CHECK(item_index >= 0),
    canonical_json TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64),
    status TEXT NOT NULL CHECK(status IN ('PENDING','LEASED','ACKED','DEAD_LETTER')),
    owner TEXT,
    lease_until_utc TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    last_error TEXT,
    created_at_utc TEXT NOT NULL,
    acked_at_utc TEXT,
    UNIQUE(receipt_id, item_index)
);
CREATE INDEX IF NOT EXISTS aca_scan_work_claim_idx
ON aca_scan_work_item(status, lease_until_utc, receipt_id, item_index);
CREATE TABLE IF NOT EXISTS aca_scan_committed_cursor (
    lane TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES aca_scan_inbox_run(receipt_id),
    cursor_id TEXT NOT NULL REFERENCES aca_contract_record(record_id),
    committed_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aca_eligibility_history_current (
    market_id TEXT PRIMARY KEY,
    canonical_json TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64),
    last_observed_at_utc TEXT,
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aca_eligibility_history_checkpoint (
    checkpoint_id TEXT PRIMARY KEY REFERENCES aca_contract_record(record_id),
    market_id TEXT NOT NULL,
    observation_id TEXT NOT NULL REFERENCES aca_marketability_observation(observation_id),
    observed_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS aca_eligibility_history_checkpoint_market_idx
ON aca_eligibility_history_checkpoint(market_id, observed_at_utc, checkpoint_id);
"""


ACA_V3_SCHEMA_VERSION = "aca_v3.0"
ACA_V3_MIGRATION_ID = "aca_0003_durable_due_and_cadence_work"
ACA_V3_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS aca_next_evaluation_current (
    market_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL UNIQUE REFERENCES aca_next_evaluation(schedule_id),
    due_at_utc TEXT NOT NULL,
    priority INTEGER NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aca_evaluation_work_item (
    schedule_id TEXT PRIMARY KEY REFERENCES aca_next_evaluation(schedule_id),
    market_id TEXT NOT NULL,
    due_at_utc TEXT NOT NULL,
    priority INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','LEASED','ACKED','DEAD_LETTER','SUPERSEDED')),
    owner TEXT,
    lease_until_utc TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    last_error TEXT,
    acked_at_utc TEXT,
    completion_observation_id TEXT REFERENCES aca_marketability_observation(observation_id),
    completion_observation_sha256 TEXT CHECK(completion_observation_sha256 IS NULL OR length(completion_observation_sha256) = 64)
);
CREATE INDEX IF NOT EXISTS aca_evaluation_work_due_idx
ON aca_evaluation_work_item(status, due_at_utc, priority, market_id);
INSERT OR IGNORE INTO aca_next_evaluation_current
SELECT n.market_id,n.schedule_id,n.due_at_utc,n.priority,r.created_at_utc
FROM aca_next_evaluation n
JOIN aca_contract_record r ON r.record_id=n.schedule_id
WHERE NOT EXISTS (
    SELECT 1 FROM aca_next_evaluation newer
    JOIN aca_contract_record newer_record ON newer_record.record_id=newer.schedule_id
    WHERE newer.market_id=n.market_id AND (
        newer_record.created_at_utc > r.created_at_utc OR
        (newer_record.created_at_utc = r.created_at_utc AND newer.schedule_id > n.schedule_id)
    )
);
INSERT OR IGNORE INTO aca_evaluation_work_item
(schedule_id,market_id,due_at_utc,priority,status)
SELECT n.schedule_id,n.market_id,n.due_at_utc,n.priority,
       CASE WHEN c.schedule_id=n.schedule_id THEN 'PENDING' ELSE 'SUPERSEDED' END
FROM aca_next_evaluation n
LEFT JOIN aca_next_evaluation_current c ON c.market_id=n.market_id;
CREATE TABLE IF NOT EXISTS aca_cadence_work_item (
    work_id TEXT PRIMARY KEY REFERENCES aca_contract_record(record_id),
    lane TEXT NOT NULL,
    resource TEXT NOT NULL,
    scan_policy_id TEXT NOT NULL REFERENCES aca_contract_record(record_id),
    cadence_due_at_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','LEASED','WAITING','ACKED','DEAD_LETTER','SUPERSEDED')),
    owner TEXT,
    lease_until_utc TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    last_error TEXT,
    scan_receipt_id TEXT UNIQUE REFERENCES aca_contract_record(record_id),
    acked_at_utc TEXT,
    UNIQUE(scan_policy_id,lane,cadence_due_at_utc)
);
CREATE INDEX IF NOT EXISTS aca_cadence_work_claim_idx
ON aca_cadence_work_item(status, resource, cadence_due_at_utc, lane);
CREATE TABLE IF NOT EXISTS aca_cadence_policy_current (
    scope_key TEXT PRIMARY KEY CHECK(scope_key = 'UNIVERSE'),
    scan_policy_id TEXT NOT NULL UNIQUE REFERENCES aca_contract_record(record_id),
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    updated_at_utc TEXT NOT NULL
);
"""


class AcaContractConflictError(ValueError):
    """A stable record id was replayed with different canonical content."""


class AcaStoredContractCorruptionError(RuntimeError):
    """Stored canonical content no longer matches its immutable hash."""


@dataclass(frozen=True, slots=True)
class DurableScanWorkItem:
    work_id: str
    receipt_id: str
    cursor_id: str
    market_id: str
    item: dict[str, Any]
    canonical_sha256: str
    attempts: int
    owner: str
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class DurableEvaluationWorkItem:
    work_id: str
    schedule: NextEvaluation
    attempts: int
    owner: str
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class DurableCadenceWorkItem:
    work_id: str
    order: CadenceWorkOrder
    attempts: int
    owner: str
    lease_until: datetime


class CapitalAgentRepository:
    def __init__(self, target: str | Path | sqlite3.Connection) -> None:
        self._target = target

    def _conn(self) -> tuple[sqlite3.Connection, bool]:
        if isinstance(self._target, sqlite3.Connection):
            return self._target, False
        return sqlite3.connect(str(self._target), timeout=10, isolation_level=None), True

    def migrate(self) -> dict[str, str]:
        digest = hashlib.sha256(ACA_MIGRATION_SQL.encode("utf-8")).hexdigest()
        v2_digest = hashlib.sha256(ACA_V2_MIGRATION_SQL.encode("utf-8")).hexdigest()
        v3_digest = hashlib.sha256(ACA_V3_MIGRATION_SQL.encode("utf-8")).hexdigest()
        conn, owns = self._conn()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(
                "BEGIN IMMEDIATE;\n"
                + ACA_MIGRATION_SQL
                + ACA_V2_MIGRATION_SQL
                + ACA_V3_MIGRATION_SQL
            )
            self._ensure_manifest(conn, ACA_SCHEMA_VERSION, ACA_MIGRATION_ID, digest)
            self._ensure_manifest(conn, ACA_V2_SCHEMA_VERSION, ACA_V2_MIGRATION_ID, v2_digest)
            self._ensure_manifest(
                conn,
                ACA_V3_SCHEMA_VERSION,
                ACA_V3_MIGRATION_ID,
                v3_digest,
            )
            conn.execute("COMMIT")
            return {
                "schema_version": ACA_V3_SCHEMA_VERSION,
                "migration_id": ACA_V3_MIGRATION_ID,
                "sql_sha256": v3_digest,
            }
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    @staticmethod
    def _ensure_manifest(conn: sqlite3.Connection, version: str, migration: str, digest: str) -> None:
        existing = conn.execute("SELECT migration_id, sql_sha256 FROM aca_schema_manifest WHERE schema_version=?", (version,)).fetchone()
        if existing is None:
            conn.execute("INSERT INTO aca_schema_manifest VALUES (?, ?, ?, ?)", (version, migration, digest, "2026-08-30T00:00:00Z"))
        elif tuple(existing) != (migration, digest):
            raise AcaStoredContractCorruptionError("ACA migration manifest does not match released SQL")

    def save_contract(self, contract: CommonEnvelope) -> str:
        return self.save_contracts_atomic((contract,))[0]

    def save_contracts_atomic(
        self, contracts: Sequence[CommonEnvelope]
    ) -> tuple[str, ...]:
        values = tuple(contracts)
        if not values:
            raise ValueError("contract group must not be empty")
        if any(not isinstance(item, CommonEnvelope) for item in values):
            raise TypeError("ACA repository accepts CommonEnvelope contracts only")
        if len({item.record_id for item in values}) != len(values):
            raise ValueError("contract group record ids must be unique")
        if any(item.schema_version != ALPHA_CONTRACT_VERSION for item in values):
            raise ValueError("unsupported canonical contract schema")
        self.migrate()
        conn, owns = self._conn()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            for contract in values:
                self._insert_contract(conn, contract)
            conn.execute("COMMIT")
            return tuple(item.canonical_sha256 for item in values)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    def _insert_contract(
        self, conn: sqlite3.Connection, contract: CommonEnvelope
    ) -> None:
        payload = canonical_json(contract)
        digest = contract.canonical_sha256
        existing = conn.execute(
            "SELECT canonical_sha256 FROM aca_contract_record WHERE record_id=?",
            (contract.record_id,),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != digest:
                raise AcaContractConflictError(
                    f"record_id {contract.record_id} has different canonical content"
                )
            self._insert_projection(conn, contract)
            return
        conn.execute(
            "INSERT INTO aca_contract_record VALUES (?, ?, ?, ?, ?, ?)",
            (
                contract.record_id,
                type(contract).__name__,
                contract.schema_version,
                payload,
                digest,
                canonical_datetime(contract.created_at),
            ),
        )
        self._insert_projection(conn, contract)

    def _insert_projection(
        self, conn: sqlite3.Connection, contract: CommonEnvelope
    ) -> None:
        if isinstance(contract, MarketabilityObservation):
            conn.execute(
                "INSERT OR IGNORE INTO aca_marketability_observation "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    contract.observation_id,
                    contract.market_id,
                    canonical_datetime(contract.observed_at),
                    contract.prior_state.value,
                    int(contract.qualifies),
                    int(contract.near_eligible),
                    contract.marketability_fingerprint,
                ),
            )
        elif isinstance(contract, EligibilityTransition):
            conn.execute(
                "INSERT OR IGNORE INTO aca_eligibility_transition "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    contract.transition_id,
                    contract.market_id,
                    contract.observation_id,
                    contract.prior_state.value,
                    contract.new_state.value,
                    canonical_datetime(contract.effective_at),
                ),
            )
        elif isinstance(contract, EligibilityHistoryCheckpoint):
            observation = conn.execute(
                "SELECT r.canonical_sha256, o.market_id, o.observed_at_utc "
                "FROM aca_marketability_observation o "
                "JOIN aca_contract_record r ON r.record_id=o.observation_id "
                "WHERE o.observation_id=?",
                (contract.observation_id,),
            ).fetchone()
            if observation is None:
                raise AcaContractConflictError(
                    "history checkpoint requires its immutable observation"
                )
            if tuple(observation) != (
                contract.observation_sha256,
                contract.market_id,
                canonical_datetime(contract.observed_at),
            ):
                raise AcaContractConflictError(
                    "history checkpoint does not bind the stored observation"
                )
            conn.execute(
                "INSERT OR IGNORE INTO aca_eligibility_history_checkpoint "
                "VALUES (?, ?, ?, ?)",
                (
                    contract.checkpoint_id,
                    contract.market_id,
                    contract.observation_id,
                    canonical_datetime(contract.observed_at),
                ),
            )
        elif isinstance(contract, NextEvaluation):
            self._project_next_evaluation(conn, contract)
        elif isinstance(contract, CadenceWorkOrder):
            policy = conn.execute(
                "SELECT canonical_sha256 FROM aca_contract_record WHERE record_id=?",
                (contract.scan_policy_id,),
            ).fetchone()
            if policy is None or str(policy[0]) != contract.scan_policy_sha256:
                raise AcaContractConflictError(
                    "cadence work does not bind its stored scan policy"
                )
            conn.execute(
                "INSERT OR IGNORE INTO aca_cadence_work_item "
                "(work_id,lane,resource,scan_policy_id,cadence_due_at_utc,status) "
                "VALUES (?,?,?,?,?,'PENDING')",
                (
                    contract.work_id,
                    contract.lane.value,
                    contract.resource.value,
                    contract.scan_policy_id,
                    canonical_datetime(contract.cadence_due_at),
                ),
            )
        elif isinstance(contract, UniverseScanCursor):
            conn.execute(
                "INSERT OR IGNORE INTO aca_scan_cursor VALUES (?, ?, ?, ?, ?, ?)",
                (
                    contract.cursor_id,
                    contract.lane.value,
                    None
                    if contract.watermark_updated_at is None
                    else canonical_datetime(contract.watermark_updated_at),
                    contract.watermark_market_id,
                    int(contract.coverage_complete),
                    canonical_datetime(contract.observed_at),
                ),
            )
        elif isinstance(contract, MarketAdmissionEpisode):
            conn.execute(
                "INSERT OR IGNORE INTO aca_market_admission_episode "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    contract.episode_id,
                    contract.market_id,
                    contract.trigger.value,
                    contract.eligibility_observation_id,
                    contract.candidate_seed_id,
                    canonical_datetime(contract.admitted_at),
                ),
            )
        elif type(contract).__name__ == "CapitalPlan":
            conn.execute(
                "INSERT OR IGNORE INTO aca_capital_plan VALUES (?, ?, ?, ?, ?, ?)",
                (
                    contract.record_id,
                    str(getattr(contract, "account_id")),
                    canonical_datetime(getattr(contract, "decision_as_of")),
                    validate_hash_text(getattr(contract, "policy_hash")),
                    str(getattr(contract, "mode")),
                    str(getattr(contract, "execution_capability")),
                ),
            )

    @staticmethod
    def _project_next_evaluation(
        conn: sqlite3.Connection, contract: NextEvaluation
    ) -> None:
        observation = conn.execute(
            "SELECT r.canonical_sha256,o.market_id,o.observed_at_utc "
            "FROM aca_marketability_observation o "
            "JOIN aca_contract_record r ON r.record_id=o.observation_id "
            "WHERE o.observation_id=?",
            (contract.cause_observation_id,),
        ).fetchone()
        if observation is None or tuple(observation) != (
            contract.cause_observation_sha256,
            contract.market_id,
            canonical_datetime(contract.created_at),
        ):
            raise AcaContractConflictError(
                "next evaluation does not bind its stored cause observation"
            )
        due = canonical_datetime(contract.due_at)
        created = canonical_datetime(contract.created_at)
        conn.execute(
            "INSERT OR IGNORE INTO aca_next_evaluation VALUES (?, ?, ?, ?, ?, ?)",
            (
                contract.schedule_id,
                contract.market_id,
                contract.state.value,
                contract.tier.value,
                due,
                contract.priority,
            ),
        )
        current = conn.execute(
            "SELECT c.schedule_id,c.updated_at_utc "
            "FROM aca_next_evaluation_current c WHERE c.market_id=?",
            (contract.market_id,),
        ).fetchone()
        new_key = (created, contract.schedule_id)
        current_key = None if current is None else (str(current[1]), str(current[0]))
        if current_key is None or new_key > current_key:
            if current is not None:
                conn.execute(
                    "UPDATE aca_evaluation_work_item SET status='SUPERSEDED',"
                    "owner=NULL,lease_until_utc=NULL "
                    "WHERE schedule_id=? AND status='PENDING'",
                    (str(current[0]),),
                )
            conn.execute(
                "INSERT INTO aca_next_evaluation_current "
                "VALUES (?,?,?,?,?) ON CONFLICT(market_id) DO UPDATE SET "
                "schedule_id=excluded.schedule_id,due_at_utc=excluded.due_at_utc,"
                "priority=excluded.priority,updated_at_utc=excluded.updated_at_utc",
                (
                    contract.market_id,
                    contract.schedule_id,
                    due,
                    contract.priority,
                    created,
                ),
            )
            status = "PENDING"
        elif current_key == new_key:
            status = "PENDING"
        else:
            status = "SUPERSEDED"
        conn.execute(
            "INSERT OR IGNORE INTO aca_evaluation_work_item "
            "(schedule_id,market_id,due_at_utc,priority,status) VALUES (?,?,?,?,?)",
            (
                contract.schedule_id,
                contract.market_id,
                due,
                contract.priority,
                status,
            ),
        )

    def get_contract_json(self, record_id: str) -> str | None:
        self.migrate()
        conn, owns = self._conn()
        try:
            row = conn.execute(
                "SELECT schema_version, canonical_json, canonical_sha256 "
                "FROM aca_contract_record WHERE record_id=?",
                (record_id,),
            ).fetchone()
            if row is None:
                return None
            schema_version, payload, expected = tuple(row)
            if schema_version != ALPHA_CONTRACT_VERSION:
                raise AcaStoredContractCorruptionError(
                    "stored contract has unsupported schema version"
                )
            try:
                parsed = json.loads(str(payload))
            except (TypeError, json.JSONDecodeError) as exc:
                raise AcaStoredContractCorruptionError(
                    "stored contract JSON is invalid"
                ) from exc
            if content_sha256(parsed) != expected:
                raise AcaStoredContractCorruptionError("stored contract hash mismatch")
            return str(payload)
        finally:
            if owns:
                conn.close()

    def get_contract(self, record_id: str) -> dict[str, Any] | None:
        payload = self.get_contract_json(record_id)
        return None if payload is None else json.loads(payload)

    def list_contracts(self, contract_type: str) -> tuple[dict[str, Any], ...]:
        self.migrate()
        conn, owns = self._conn()
        try:
            record_ids = tuple(
                str(row[0])
                for row in conn.execute(
                    "SELECT record_id FROM aca_contract_record "
                    "WHERE contract_type=? ORDER BY created_at_utc, record_id",
                    (contract_type,),
                ).fetchall()
            )
        finally:
            if owns:
                conn.close()
        values = tuple(self.get_contract(item) for item in record_ids)
        return tuple(item for item in values if item is not None)

    def latest_cursor(self, lane: ScanLane) -> UniverseScanCursor | None:
        self.migrate()
        conn, owns = self._conn()
        try:
            row = conn.execute(
                "SELECT cursor_id FROM aca_scan_committed_cursor WHERE lane=?",
                (lane.value,),
            ).fetchone()
        finally:
            if owns:
                conn.close()
        if row is None:
            return None
        payload = self.get_contract(str(row[0]))
        return None if payload is None else UniverseScanCursor.model_validate(payload)

    def materialize_due_cadence(
        self,
        *,
        policy: UniverseScanPolicy,
        as_of: datetime,
    ) -> tuple[CadenceWorkOrder, ...]:
        """Create at most one collapsed catch-up slot per available resource."""

        now = ensure_utc(as_of)
        self.migrate()
        conn, owns = self._conn()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            self._insert_contract(conn, policy)
            policy_clock = canonical_datetime(policy.created_at)
            current = conn.execute(
                "SELECT scan_policy_id,updated_at_utc FROM "
                "aca_cadence_policy_current WHERE scope_key='UNIVERSE'"
            ).fetchone()
            current_key = (
                None
                if current is None
                else (str(current[1]), str(current[0]))
            )
            policy_key = (policy_clock, policy.scan_policy_id)
            if current_key is not None and policy_key < current_key:
                conn.execute("COMMIT")
                return ()
            if current_key is None or policy_key > current_key:
                conn.execute(
                    "INSERT INTO aca_cadence_policy_current VALUES "
                    "('UNIVERSE',?,?,?) ON CONFLICT(scope_key) DO UPDATE SET "
                    "scan_policy_id=excluded.scan_policy_id,"
                    "enabled=excluded.enabled,updated_at_utc=excluded.updated_at_utc",
                    (policy.scan_policy_id, int(policy.enabled), policy_clock),
                )
                conn.execute(
                    "UPDATE aca_cadence_work_item SET status='SUPERSEDED',"
                    "owner=NULL,lease_until_utc=NULL "
                    "WHERE scan_policy_id != ? AND "
                    "(status IN ('PENDING','DEAD_LETTER') OR "
                    "(status='LEASED' AND lease_until_utc <= ?))",
                    (policy.scan_policy_id, canonical_datetime(now)),
                )
            if not policy.enabled:
                conn.execute(
                    "UPDATE aca_cadence_work_item SET status='SUPERSEDED',"
                    "owner=NULL,lease_until_utc=NULL "
                    "WHERE status IN ('PENDING','DEAD_LETTER') OR "
                    "(status='LEASED' AND lease_until_utc <= ?)",
                    (canonical_datetime(now),),
                )
                conn.execute("COMMIT")
                return ()
            orders: list[CadenceWorkOrder] = []
            for lane in (
                ScanLane.GAMMA_DELTA,
                ScanLane.DAILY_ANTI_ENTROPY,
                ScanLane.FULL_ACTIVE_CENSUS,
            ):
                last = conn.execute(
                    "SELECT cadence_due_at_utc FROM aca_cadence_work_item "
                    "WHERE scan_policy_id=? AND lane=? AND status='ACKED' "
                    "ORDER BY cadence_due_at_utc DESC LIMIT 1",
                    (policy.scan_policy_id, lane.value),
                ).fetchone()
                last_due = (
                    None
                    if last is None
                    else datetime.fromisoformat(str(last[0]).replace("Z", "+00:00"))
                )
                due = latest_due_cadence_slot(
                    policy=policy,
                    lane=lane,
                    as_of=now,
                    last_completed_due_at=last_due,
                )
                if due is None:
                    continue
                order = build_cadence_work_order(
                    policy=policy,
                    lane=lane,
                    cadence_due_at=due,
                )
                active = conn.execute(
                    "SELECT 1 FROM aca_cadence_work_item "
                    "WHERE resource=? AND (status IN ('PENDING','WAITING') OR "
                    "(status='LEASED' AND lease_until_utc > ?)) "
                    "LIMIT 1",
                    (order.resource.value, canonical_datetime(now)),
                ).fetchone()
                if active is not None:
                    continue
                existing = conn.execute(
                    "SELECT status FROM aca_cadence_work_item WHERE work_id=?",
                    (order.work_id,),
                ).fetchone()
                if existing is not None and str(existing[0]) in {
                    "DEAD_LETTER",
                    "SUPERSEDED",
                }:
                    continue
                self._insert_contract(conn, order)
                orders.append(order)
            conn.execute("COMMIT")
            return tuple(orders)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    def claim_cadence_work(
        self,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int,
        limit: int = 1,
    ) -> tuple[DurableCadenceWorkItem, ...]:
        if not owner.strip() or lease_seconds <= 0 or limit <= 0:
            raise ValueError(
                "owner, positive lease_seconds and positive limit are required"
            )
        stamp = canonical_datetime(now)
        lease_until = canonical_datetime(
            ensure_utc(now) + timedelta(seconds=lease_seconds)
        )
        self.migrate()
        conn, owns = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT w.work_id,w.resource FROM aca_cadence_work_item w "
                "JOIN aca_cadence_policy_current p "
                "ON p.scan_policy_id=w.scan_policy_id AND p.enabled=1 "
                "WHERE (w.status='PENDING' OR "
                "(w.status='LEASED' AND w.lease_until_utc <= ?)) "
                "AND NOT EXISTS (SELECT 1 FROM aca_cadence_work_item active "
                "WHERE active.resource=w.resource AND active.work_id != w.work_id "
                "AND (active.status='WAITING' OR "
                "(active.status='LEASED' AND active.lease_until_utc > ?))) "
                "ORDER BY w.cadence_due_at_utc,w.lane,w.work_id",
                (stamp, stamp),
            ).fetchall()
            selected: list[str] = []
            selected_resources: set[str] = set()
            for work_id, resource in rows:
                resource_key = str(resource)
                if resource_key in selected_resources:
                    continue
                selected.append(str(work_id))
                selected_resources.add(resource_key)
                if len(selected) == limit:
                    break
            work_ids = tuple(selected)
            for work_id in work_ids:
                changed = conn.execute(
                    "UPDATE aca_cadence_work_item SET status='LEASED',owner=?,"
                    "lease_until_utc=?,attempts=attempts+1,last_error=NULL "
                    "WHERE work_id=? AND (status='PENDING' OR "
                    "(status='LEASED' AND lease_until_utc <= ?))",
                    (owner.strip(), lease_until, work_id, stamp),
                ).rowcount
                if changed != 1:
                    raise AcaContractConflictError(
                        "cadence lease changed concurrently"
                    )
            values = self._load_cadence_work(conn, work_ids)
            conn.execute("COMMIT")
            return values
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    @staticmethod
    def _load_cadence_work(
        conn: sqlite3.Connection, work_ids: tuple[str, ...]
    ) -> tuple[DurableCadenceWorkItem, ...]:
        if not work_ids:
            return ()
        placeholders = ",".join("?" for _ in work_ids)
        rows = conn.execute(
            "SELECT w.work_id,r.canonical_json,r.canonical_sha256,w.attempts,"
            "w.owner,w.lease_until_utc FROM aca_cadence_work_item w "
            "JOIN aca_contract_record r ON r.record_id=w.work_id "
            f"WHERE w.work_id IN ({placeholders}) "
            "ORDER BY w.cadence_due_at_utc,w.lane,w.work_id",
            work_ids,
        ).fetchall()
        values: list[DurableCadenceWorkItem] = []
        for row in rows:
            payload = json.loads(str(row[1]))
            if content_sha256(payload) != str(row[2]):
                raise AcaStoredContractCorruptionError(
                    "cadence work contract hash mismatch"
                )
            order = CadenceWorkOrder.model_validate(payload)
            values.append(
                DurableCadenceWorkItem(
                    work_id=str(row[0]),
                    order=order,
                    attempts=int(row[3]),
                    owner=str(row[4]),
                    lease_until=datetime.fromisoformat(
                        str(row[5]).replace("Z", "+00:00")
                    ),
                )
            )
        return tuple(values)

    @staticmethod
    def _bind_cadence_scan_tx(
        conn: sqlite3.Connection,
        *,
        work_id: str,
        owner: str,
        receipt: UniverseScanRunReceipt,
        policy: UniverseScanPolicy,
        lease_checked_at: datetime,
    ) -> None:
        row = conn.execute(
            "SELECT lane,scan_policy_id,status,owner,lease_until_utc,scan_receipt_id "
            "FROM aca_cadence_work_item WHERE work_id=?",
            (work_id,),
        ).fetchone()
        if row is None:
            raise ValueError("unknown cadence work item")
        if str(row[5] or ""):
            if str(row[5]) != receipt.record_id or str(row[2]) not in {
                "WAITING",
                "ACKED",
            }:
                raise AcaContractConflictError(
                    "cadence work replay has a different scan receipt"
                )
            return
        checked_at = canonical_datetime(lease_checked_at)
        if checked_at < canonical_datetime(receipt.completed_at):
            raise ValueError("cadence lease clock cannot predate scan completion")
        if (
            str(row[0]) != receipt.lane.value
            or str(row[1]) != policy.scan_policy_id
            or receipt.scan_policy_id != policy.scan_policy_id
            or receipt.scan_policy_sha256 != policy.canonical_sha256
        ):
            raise AcaContractConflictError(
                "cadence work does not bind the completed scan"
            )
        if (
            str(row[2]) != "LEASED"
            or str(row[3]) != owner.strip()
            or str(row[4]) < checked_at
        ):
            raise ValueError("cadence work is not leased by this owner")
        conn.execute(
            "UPDATE aca_cadence_work_item SET status='WAITING',owner=NULL,"
            "lease_until_utc=NULL,scan_receipt_id=? WHERE work_id=?",
            (receipt.record_id, work_id),
        )

    def fail_cadence_work(
        self,
        *,
        work_id: str,
        owner: str,
        now: datetime,
        error: str,
        retryable: bool = True,
        max_attempts: int = 5,
    ) -> str:
        if not work_id.strip() or not owner.strip() or not error.strip() or max_attempts <= 0:
            raise ValueError("work, owner, error and positive max_attempts are required")
        stamp = canonical_datetime(now)
        self.migrate()
        conn, owns = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT attempts FROM aca_cadence_work_item "
                "WHERE work_id=? AND status='LEASED' AND owner=? "
                "AND lease_until_utc >= ?",
                (work_id, owner.strip(), stamp),
            ).fetchone()
            if row is None:
                raise ValueError("cadence work is not leased by this owner")
            status = (
                "PENDING"
                if retryable and int(row[0]) < max_attempts
                else "DEAD_LETTER"
            )
            conn.execute(
                "UPDATE aca_cadence_work_item SET status=?,owner=NULL,"
                "lease_until_utc=NULL,last_error=? WHERE work_id=?",
                (status, error.strip(), work_id),
            )
            conn.execute("COMMIT")
            return status
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    def persist_successful_scan(
        self,
        policy: CommonEnvelope,
        cursor: UniverseScanCursor,
        receipt: CommonEnvelope,
        selected_items: Sequence[Mapping[str, Any]],
        *,
        cadence_work_id: str | None = None,
        cadence_owner: str | None = None,
        cadence_lease_checked_at: datetime | None = None,
    ) -> tuple[str, ...]:
        """Atomically append a completed scan and enqueue its raw selected work.

        The caller must only pass a complete successful receipt.  A cursor is
        published separately, after every item for this receipt is acknowledged.
        """
        from .scheduler import UniverseScanPolicy, UniverseScanRunReceipt

        if not isinstance(policy, UniverseScanPolicy) or not isinstance(receipt, UniverseScanRunReceipt):
            raise TypeError("durable scan requires policy and scan receipt contracts")
        cadence_values = (
            cadence_work_id,
            cadence_owner,
            cadence_lease_checked_at,
        )
        if any(item is None for item in cadence_values) and any(
            item is not None for item in cadence_values
        ):
            raise ValueError(
                "cadence work, owner and independent lease clock must be supplied together"
            )
        if (
            not receipt.coverage_complete
            or not cursor.coverage_complete
            or receipt.output_cursor_id != cursor.cursor_id
            or receipt.output_cursor_sha256 != cursor.canonical_sha256
        ):
            raise ValueError("only a complete receipt may enter the durable inbox")
        if (
            receipt.lane != cursor.lane
            or receipt.scan_policy_id != policy.scan_policy_id
            or receipt.scan_policy_sha256 != policy.canonical_sha256
        ):
            raise ValueError("scan policy, receipt and cursor must agree")
        if (receipt.input_cursor_id, receipt.input_cursor_sha256) != (
            cursor.prior_cursor_id,
            cursor.prior_cursor_sha256,
        ):
            raise ValueError("scan receipt and output cursor prior lineage differ")
        prepared = tuple(self._prepare_work_item(receipt, cursor, index, item) for index, item in enumerate(selected_items))
        if receipt.selected_market_count != len(prepared):
            raise ValueError("scan receipt selected count does not match durable work")
        if len({item[3] for item in prepared}) != len(prepared):
            raise ValueError("durable scan work must contain unique market ids")
        self.migrate()
        conn, owns = self._conn()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute(
                "SELECT r.receipt_id FROM aca_scan_inbox_run r "
                "JOIN aca_scan_work_item w USING(receipt_id) "
                "WHERE r.lane=? AND r.committed_at_utc IS NULL AND w.status != 'ACKED' LIMIT 1",
                (cursor.lane.value,),
            ).fetchone()
            if pending is not None and str(pending[0]) != receipt.record_id:
                replay = conn.execute(
                    "SELECT committed_at_utc FROM aca_scan_inbox_run WHERE receipt_id=?",
                    (receipt.record_id,),
                ).fetchone()
                if replay is None or replay[0] is None:
                    raise ValueError("lane has unacknowledged durable scan work")
            for contract in (policy, cursor, receipt):
                self._insert_contract(conn, contract)
            existing = conn.execute(
                "SELECT lane,cursor_id,committed_at_utc FROM aca_scan_inbox_run "
                "WHERE receipt_id=?",
                (receipt.record_id,),
            ).fetchone()
            already_committed = existing is not None and existing[2] is not None
            if existing is None:
                conn.execute("INSERT INTO aca_scan_inbox_run(receipt_id,lane,cursor_id) VALUES (?,?,?)", (receipt.record_id, cursor.lane.value, cursor.cursor_id))
            elif tuple(existing[:2]) != (cursor.lane.value, cursor.cursor_id):
                raise AcaContractConflictError("scan receipt replay has different operational content")
            if not already_committed:
                committed = conn.execute(
                    "SELECT cursor_id FROM aca_scan_committed_cursor WHERE lane=?",
                    (cursor.lane.value,),
                ).fetchone()
                committed_cursor_id = None if committed is None else str(committed[0])
                if committed_cursor_id != receipt.input_cursor_id:
                    raise ValueError(
                        "scan input cursor is not the lane's committed cursor"
                    )
                if committed_cursor_id is not None:
                    committed_hash = conn.execute(
                        "SELECT canonical_sha256 FROM aca_contract_record "
                        "WHERE record_id=?",
                        (committed_cursor_id,),
                    ).fetchone()
                    if (
                        committed_hash is None
                        or str(committed_hash[0]) != receipt.input_cursor_sha256
                    ):
                        raise AcaStoredContractCorruptionError(
                            "scan input cursor hash is not committed lineage"
                        )
            for item in prepared:
                conn.execute(
                    "INSERT OR IGNORE INTO aca_scan_work_item(work_id,receipt_id,cursor_id,market_id,item_index,canonical_json,canonical_sha256,status,created_at_utc) VALUES (?,?,?,?,?,?,?,?,?)",
                    item,
                )
                stored = conn.execute("SELECT canonical_sha256 FROM aca_scan_work_item WHERE work_id=?", (item[0],)).fetchone()
                if stored is None or str(stored[0]) != item[6]:
                    raise AcaContractConflictError("scan work id replay has different canonical content")
            if cadence_work_id is not None:
                assert cadence_owner is not None
                assert cadence_lease_checked_at is not None
                self._bind_cadence_scan_tx(
                    conn,
                    work_id=cadence_work_id,
                    owner=cadence_owner,
                    receipt=receipt,
                    policy=policy,
                    lease_checked_at=cadence_lease_checked_at,
                )
            if not prepared and not already_committed:
                committed_at = (
                    receipt.completed_at
                    if cadence_lease_checked_at is None
                    else cadence_lease_checked_at
                )
                self._commit_receipt_if_acked(
                    conn, receipt.record_id, canonical_datetime(committed_at)
                )
            conn.execute("COMMIT")
            return tuple(contract.canonical_sha256 for contract in (policy, cursor, receipt))
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    @staticmethod
    def _prepare_work_item(receipt: CommonEnvelope, cursor: UniverseScanCursor, index: int, item: Mapping[str, Any]) -> tuple[str, str, str, str, int, str, str, str, str]:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ValueError("selected work item requires a non-empty string id")
        payload = canonical_json(dict(item))
        parsed = json.loads(payload)
        digest = content_sha256(parsed)
        work_id = f"aca_scan_work:{hashlib.sha256(canonical_json({'receipt_id': receipt.record_id, 'index': index, 'sha256': digest}).encode()).hexdigest()}"
        return (work_id, receipt.record_id, cursor.cursor_id, item["id"].strip(), index, payload, digest, "PENDING", canonical_datetime(receipt.completed_at))

    def claim_scan_work(self, *, owner: str, now: datetime, lease_seconds: int, limit: int = 1) -> tuple[DurableScanWorkItem, ...]:
        if not owner.strip() or lease_seconds <= 0 or limit <= 0:
            raise ValueError("owner, positive lease_seconds and positive limit are required")
        claimed_at = canonical_datetime(now)
        until = canonical_datetime(ensure_utc(now) + timedelta(seconds=lease_seconds))
        self.migrate(); conn, owns = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT work_id FROM aca_scan_work_item WHERE status='PENDING' OR (status='LEASED' AND lease_until_utc <= ?) ORDER BY receipt_id,item_index LIMIT ?", (claimed_at, limit)).fetchall()
            work_ids = tuple(str(row[0]) for row in rows)
            for work_id in work_ids:
                conn.execute("UPDATE aca_scan_work_item SET status='LEASED',owner=?,lease_until_utc=?,attempts=attempts+1,last_error=NULL WHERE work_id=?", (owner.strip(), until, work_id))
            values = self._load_work(conn, work_ids)
            conn.execute("COMMIT")
            return values
        except Exception:
            if conn.in_transaction: conn.execute("ROLLBACK")
            raise
        finally:
            if owns: conn.close()

    def ack_scan_work(self, *, work_id: str, owner: str, now: datetime) -> bool:
        if not work_id.strip() or not owner.strip():
            raise ValueError("work_id and owner are required")
        acked_at = canonical_datetime(now)
        self.migrate(); conn, owns = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT receipt_id,status,owner,lease_until_utc FROM aca_scan_work_item WHERE work_id=?", (work_id,)).fetchone()
            if row is None: raise ValueError("unknown scan work item")
            receipt_id, status, leased_owner, lease_until = map(str, row)
            if status != "ACKED":
                if status != "LEASED" or leased_owner != owner.strip() or lease_until < acked_at:
                    raise ValueError("scan work is not leased by this owner")
                conn.execute("UPDATE aca_scan_work_item SET status='ACKED',owner=NULL,lease_until_utc=NULL,acked_at_utc=? WHERE work_id=?", (acked_at, work_id))
            self._commit_receipt_if_acked(conn, receipt_id, acked_at)
            conn.execute("COMMIT"); return True
        except Exception:
            if conn.in_transaction: conn.execute("ROLLBACK")
            raise
        finally:
            if owns: conn.close()

    def fail_scan_work(
        self,
        *,
        work_id: str,
        owner: str,
        now: datetime,
        error: str,
        retryable: bool = True,
        max_attempts: int = 5,
    ) -> str:
        if (
            not work_id.strip()
            or not owner.strip()
            or not error.strip()
            or max_attempts <= 0
        ):
            raise ValueError(
                "work_id, owner, error and positive max_attempts are required"
            )
        stamp = canonical_datetime(now)
        self.migrate(); conn, owns = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT attempts FROM aca_scan_work_item "
                "WHERE work_id=? AND status='LEASED' AND owner=? "
                "AND lease_until_utc >= ?",
                (work_id, owner.strip(), stamp),
            ).fetchone()
            if row is None:
                raise ValueError("scan work is not leased by this owner")
            status = (
                "PENDING"
                if retryable and int(row[0]) < max_attempts
                else "DEAD_LETTER"
            )
            changed = conn.execute(
                "UPDATE aca_scan_work_item SET status=?,owner=NULL,"
                "lease_until_utc=NULL,last_error=? WHERE work_id=? "
                "AND status='LEASED' AND owner=?",
                (status, error.strip(), work_id, owner.strip()),
            ).rowcount
            if changed != 1: raise ValueError("scan work is not leased by this owner")
            conn.execute("COMMIT")
            return status
        except Exception:
            if conn.in_transaction: conn.execute("ROLLBACK")
            raise
        finally:
            if owns: conn.close()

    def _load_work(self, conn: sqlite3.Connection, ids: tuple[str, ...]) -> tuple[DurableScanWorkItem, ...]:
        if not ids: return ()
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT work_id,receipt_id,cursor_id,market_id,canonical_json,canonical_sha256,attempts,owner,lease_until_utc FROM aca_scan_work_item WHERE work_id IN ({placeholders}) ORDER BY receipt_id,item_index", ids).fetchall()
        values = []
        for row in rows:
            payload = json.loads(str(row[4]))
            if content_sha256(payload) != str(row[5]): raise AcaStoredContractCorruptionError("scan work JSON hash mismatch")
            values.append(DurableScanWorkItem(str(row[0]), str(row[1]), str(row[2]), str(row[3]), payload, str(row[5]), int(row[6]), str(row[7]), datetime.fromisoformat(str(row[8]).replace("Z", "+00:00"))))
        return tuple(values)

    @staticmethod
    def _commit_receipt_if_acked(conn: sqlite3.Connection, receipt_id: str, at: str) -> None:
        remaining = conn.execute("SELECT 1 FROM aca_scan_work_item WHERE receipt_id=? AND status != 'ACKED' LIMIT 1", (receipt_id,)).fetchone()
        if remaining is not None: return
        row = conn.execute(
            "SELECT lane,cursor_id,committed_at_utc FROM aca_scan_inbox_run "
            "WHERE receipt_id=?",
            (receipt_id,),
        ).fetchone()
        if row is None: raise AcaStoredContractCorruptionError("missing durable scan inbox run")
        lane, cursor_id = str(row[0]), str(row[1])
        if row[2] is not None:
            return
        receipt_row = conn.execute(
            "SELECT canonical_json FROM aca_contract_record WHERE record_id=?",
            (receipt_id,),
        ).fetchone()
        if receipt_row is None:
            raise AcaStoredContractCorruptionError("missing durable scan receipt")
        input_cursor_id = json.loads(str(receipt_row[0])).get("input_cursor_id")
        committed = conn.execute(
            "SELECT cursor_id FROM aca_scan_committed_cursor WHERE lane=?", (lane,)
        ).fetchone()
        current_cursor_id = None if committed is None else str(committed[0])
        if current_cursor_id != input_cursor_id:
            raise AcaStoredContractCorruptionError(
                "durable scan cursor chain is no longer contiguous"
            )
        conn.execute("UPDATE aca_scan_inbox_run SET committed_at_utc=COALESCE(committed_at_utc,?) WHERE receipt_id=?", (at, receipt_id))
        conn.execute("INSERT INTO aca_scan_committed_cursor(lane,receipt_id,cursor_id,committed_at_utc) VALUES (?,?,?,?) ON CONFLICT(lane) DO UPDATE SET receipt_id=excluded.receipt_id,cursor_id=excluded.cursor_id,committed_at_utc=excluded.committed_at_utc", (lane, receipt_id, cursor_id, at))
        conn.execute(
            "UPDATE aca_cadence_work_item SET status='ACKED',acked_at_utc=?,"
            "owner=NULL,lease_until_utc=NULL "
            "WHERE scan_receipt_id=? AND status='WAITING'",
            (at, receipt_id),
        )

    def claim_due_evaluations(
        self,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int,
        limit: int = 1,
    ) -> tuple[DurableEvaluationWorkItem, ...]:
        """Lease only the latest due schedule per market."""

        if not owner.strip() or lease_seconds <= 0 or limit <= 0:
            raise ValueError(
                "owner, positive lease_seconds and positive limit are required"
            )
        claimed_at = canonical_datetime(now)
        lease_until = canonical_datetime(
            ensure_utc(now) + timedelta(seconds=lease_seconds)
        )
        self.migrate()
        conn, owns = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE aca_evaluation_work_item SET status='SUPERSEDED',"
                "owner=NULL,lease_until_utc=NULL "
                "WHERE (status='PENDING' OR "
                "(status='LEASED' AND lease_until_utc <= ?)) "
                "AND NOT EXISTS (SELECT 1 FROM aca_next_evaluation_current c "
                "WHERE c.schedule_id=aca_evaluation_work_item.schedule_id)",
                (claimed_at,),
            )
            rows = conn.execute(
                "SELECT w.schedule_id FROM aca_evaluation_work_item w "
                "JOIN aca_next_evaluation_current c "
                "ON c.schedule_id=w.schedule_id "
                "WHERE w.due_at_utc <= ? AND "
                "(w.status='PENDING' OR "
                "(w.status='LEASED' AND w.lease_until_utc <= ?)) "
                "ORDER BY w.priority DESC,w.due_at_utc,w.market_id,w.schedule_id "
                "LIMIT ?",
                (claimed_at, claimed_at, limit),
            ).fetchall()
            schedule_ids = tuple(str(row[0]) for row in rows)
            for schedule_id in schedule_ids:
                changed = conn.execute(
                    "UPDATE aca_evaluation_work_item SET status='LEASED',owner=?,"
                    "lease_until_utc=?,attempts=attempts+1,last_error=NULL "
                    "WHERE schedule_id=? AND (status='PENDING' OR "
                    "(status='LEASED' AND lease_until_utc <= ?))",
                    (owner.strip(), lease_until, schedule_id, claimed_at),
                ).rowcount
                if changed != 1:
                    raise AcaContractConflictError(
                        "due evaluation lease changed concurrently"
                    )
            values = self._load_evaluation_work(conn, schedule_ids)
            conn.execute("COMMIT")
            return values
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    @staticmethod
    def _load_evaluation_work(
        conn: sqlite3.Connection, schedule_ids: tuple[str, ...]
    ) -> tuple[DurableEvaluationWorkItem, ...]:
        if not schedule_ids:
            return ()
        placeholders = ",".join("?" for _ in schedule_ids)
        rows = conn.execute(
            "SELECT w.schedule_id,r.canonical_json,r.canonical_sha256,"
            "w.attempts,w.owner,w.lease_until_utc "
            "FROM aca_evaluation_work_item w "
            "JOIN aca_contract_record r ON r.record_id=w.schedule_id "
            f"WHERE w.schedule_id IN ({placeholders}) "
            "ORDER BY w.priority DESC,w.due_at_utc,w.market_id,w.schedule_id",
            schedule_ids,
        ).fetchall()
        values: list[DurableEvaluationWorkItem] = []
        for row in rows:
            payload = json.loads(str(row[1]))
            if content_sha256(payload) != str(row[2]):
                raise AcaStoredContractCorruptionError(
                    "due evaluation schedule hash mismatch"
                )
            schedule = NextEvaluation.model_validate(payload)
            values.append(
                DurableEvaluationWorkItem(
                    work_id=str(row[0]),
                    schedule=schedule,
                    attempts=int(row[3]),
                    owner=str(row[4]),
                    lease_until=datetime.fromisoformat(
                        str(row[5]).replace("Z", "+00:00")
                    ),
                )
            )
        return tuple(values)

    def ack_evaluation_work(
        self,
        *,
        work_id: str,
        owner: str,
        now: datetime,
        completion_observation_id: str,
    ) -> bool:
        self.migrate()
        conn, owns = self._conn()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            self._ack_evaluation_work_tx(
                conn,
                work_id=work_id,
                owner=owner,
                now=canonical_datetime(now),
                completion_observation_id=completion_observation_id,
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    @staticmethod
    def _ack_evaluation_work_tx(
        conn: sqlite3.Connection,
        *,
        work_id: str,
        owner: str,
        now: str,
        completion_observation_id: str,
    ) -> None:
        if not work_id.strip() or not owner.strip() or not completion_observation_id.strip():
            raise ValueError("work, owner and completion observation are required")
        work = conn.execute(
            "SELECT market_id,due_at_utc,status,owner,lease_until_utc,"
            "completion_observation_id,completion_observation_sha256 "
            "FROM aca_evaluation_work_item WHERE schedule_id=?",
            (work_id,),
        ).fetchone()
        if work is None:
            raise ValueError("unknown evaluation work item")
        observation_row = conn.execute(
            "SELECT o.market_id,o.observed_at_utc,r.canonical_sha256,r.canonical_json "
            "FROM aca_marketability_observation o "
            "JOIN aca_contract_record r ON r.record_id=o.observation_id "
            "WHERE o.observation_id=?",
            (completion_observation_id,),
        ).fetchone()
        if observation_row is None:
            raise AcaContractConflictError(
                "evaluation ACK requires its stored completion observation"
            )
        observation_payload = json.loads(str(observation_row[3]))
        if content_sha256(observation_payload) != str(observation_row[2]):
            raise AcaStoredContractCorruptionError(
                "evaluation completion observation hash mismatch"
            )
        observation = MarketabilityObservation.model_validate(observation_payload)
        if (
            observation.market_id != str(work[0])
            or canonical_datetime(observation.observed_at) != str(observation_row[1])
            or str(observation_row[1]) < str(work[1])
        ):
            raise AcaContractConflictError(
                "evaluation completion does not satisfy its due schedule"
            )
        if now < canonical_datetime(observation.observed_at):
            raise ValueError(
                "evaluation lease clock cannot predate completion observation"
            )
        status = str(work[2])
        if status == "ACKED":
            if (str(work[5]), str(work[6])) != (
                completion_observation_id,
                observation.canonical_sha256,
            ):
                raise AcaContractConflictError(
                    "evaluation work replay has a different completion"
                )
            return
        if (
            status != "LEASED"
            or str(work[3]) != owner.strip()
            or str(work[4]) < now
        ):
            raise ValueError("evaluation work is not leased by this owner")
        current = conn.execute(
            "SELECT c.schedule_id,r.canonical_json "
            "FROM aca_next_evaluation_current c "
            "JOIN aca_contract_record r ON r.record_id=c.schedule_id "
            "WHERE c.market_id=?",
            (observation.market_id,),
        ).fetchone()
        if current is not None and str(current[0]) != work_id:
            current_payload = json.loads(str(current[1]))
            if current_payload.get("cause_observation_id") != completion_observation_id:
                raise AcaContractConflictError(
                    "evaluation work was superseded by unrelated newer facts"
                )
        conn.execute(
            "UPDATE aca_evaluation_work_item SET status='ACKED',owner=NULL,"
            "lease_until_utc=NULL,acked_at_utc=?,completion_observation_id=?,"
            "completion_observation_sha256=? WHERE schedule_id=?",
            (
                now,
                completion_observation_id,
                observation.canonical_sha256,
                work_id,
            ),
        )

    def fail_evaluation_work(
        self,
        *,
        work_id: str,
        owner: str,
        now: datetime,
        error: str,
        retryable: bool = True,
        max_attempts: int = 5,
    ) -> str:
        if not work_id.strip() or not owner.strip() or not error.strip() or max_attempts <= 0:
            raise ValueError("work, owner, error and positive max_attempts are required")
        stamp = canonical_datetime(now)
        self.migrate()
        conn, owns = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT attempts FROM aca_evaluation_work_item "
                "WHERE schedule_id=? AND status='LEASED' AND owner=? "
                "AND lease_until_utc >= ?",
                (work_id, owner.strip(), stamp),
            ).fetchone()
            if row is None:
                raise ValueError("evaluation work is not leased by this owner")
            status = (
                "PENDING"
                if retryable and int(row[0]) < max_attempts
                else "DEAD_LETTER"
            )
            conn.execute(
                "UPDATE aca_evaluation_work_item SET status=?,owner=NULL,"
                "lease_until_utc=NULL,last_error=? WHERE schedule_id=?",
                (status, error.strip(), work_id),
            )
            conn.execute("COMMIT")
            return status
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    def save_eligibility_history_current(self, *, market_id: str, history: Any, updated_at: datetime) -> None:
        if not market_id.strip(): raise ValueError("market_id is required")
        payload = canonical_json(history); digest = content_sha256(history); stamp = canonical_datetime(updated_at)
        self.migrate(); conn, owns = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._upsert_eligibility_history_current(
                conn,
                market_id=market_id.strip(),
                payload=payload,
                digest=digest,
                last_observed_at=None
                if history.last_observed_at is None
                else canonical_datetime(history.last_observed_at),
                updated_at=stamp,
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction: conn.execute("ROLLBACK")
            raise
        finally:
            if owns: conn.close()

    def save_universe_evaluation_atomic(
        self,
        *,
        contracts: Sequence[CommonEnvelope],
        market_id: str,
        history: Any,
        updated_at: datetime,
        claimed_work_id: str | None = None,
        claimed_owner: str | None = None,
        claimed_lease_checked_at: datetime | None = None,
    ) -> tuple[str, ...]:
        """Append evaluation facts and replace their restart projection together."""
        values = tuple(contracts)
        if not values or not market_id.strip() or any(not isinstance(item, CommonEnvelope) for item in values):
            raise ValueError("contracts and market_id are required")
        claimed_values = (
            claimed_work_id,
            claimed_owner,
            claimed_lease_checked_at,
        )
        if any(item is None for item in claimed_values) and any(
            item is not None for item in claimed_values
        ):
            raise ValueError(
                "claimed work, owner and independent lease clock must be supplied together"
            )
        observations = tuple(
            item for item in values if isinstance(item, MarketabilityObservation)
        )
        if claimed_work_id is not None and len(observations) != 1:
            raise ValueError(
                "claimed evaluation completion requires exactly one observation"
            )
        payload, digest, stamp = canonical_json(history), content_sha256(history), canonical_datetime(updated_at)
        if (
            claimed_lease_checked_at is not None
            and canonical_datetime(claimed_lease_checked_at) < stamp
        ):
            raise ValueError(
                "claimed evaluation lease clock cannot predate updated facts"
            )
        self.migrate(); conn, owns = self._conn()
        try:
            conn.execute("PRAGMA foreign_keys = ON"); conn.execute("BEGIN IMMEDIATE")
            for contract in values: self._insert_contract(conn, contract)
            self._upsert_eligibility_history_current(
                conn,
                market_id=market_id.strip(),
                payload=payload,
                digest=digest,
                last_observed_at=None
                if history.last_observed_at is None
                else canonical_datetime(history.last_observed_at),
                updated_at=stamp,
            )
            if claimed_work_id is not None:
                assert claimed_owner is not None
                assert claimed_lease_checked_at is not None
                self._ack_evaluation_work_tx(
                    conn,
                    work_id=claimed_work_id,
                    owner=claimed_owner,
                    now=canonical_datetime(claimed_lease_checked_at),
                    completion_observation_id=observations[0].record_id,
                )
            conn.execute("COMMIT"); return tuple(contract.canonical_sha256 for contract in values)
        except Exception:
            if conn.in_transaction: conn.execute("ROLLBACK")
            raise
        finally:
            if owns: conn.close()

    @staticmethod
    def _upsert_eligibility_history_current(
        conn: sqlite3.Connection,
        *,
        market_id: str,
        payload: str,
        digest: str,
        last_observed_at: str | None,
        updated_at: str,
    ) -> None:
        existing = conn.execute(
            "SELECT canonical_sha256,updated_at_utc "
            "FROM aca_eligibility_history_current WHERE market_id=?",
            (market_id,),
        ).fetchone()
        if existing is not None:
            existing_digest, existing_updated_at = str(existing[0]), str(existing[1])
            if updated_at < existing_updated_at:
                return
            if updated_at == existing_updated_at:
                if digest != existing_digest:
                    raise AcaContractConflictError(
                        "same eligibility projection clock has different history"
                    )
                return
        conn.execute(
            "INSERT INTO aca_eligibility_history_current VALUES (?,?,?,?,?) "
            "ON CONFLICT(market_id) DO UPDATE SET "
            "canonical_json=excluded.canonical_json,"
            "canonical_sha256=excluded.canonical_sha256,"
            "last_observed_at_utc=excluded.last_observed_at_utc,"
            "updated_at_utc=excluded.updated_at_utc",
            (market_id, payload, digest, last_observed_at, updated_at),
        )

    def current_eligibility_history(self, market_id: str) -> Any | None:
        from .universe import EligibilityHistory
        self.migrate(); conn, owns = self._conn()
        try: row = conn.execute("SELECT canonical_json,canonical_sha256 FROM aca_eligibility_history_current WHERE market_id=?", (market_id,)).fetchone()
        finally:
            if owns: conn.close()
        if row is None:
            return self.rebuild_eligibility_history_current(market_id)
        payload = json.loads(str(row[0]))
        if content_sha256(payload) != str(row[1]): raise AcaStoredContractCorruptionError("eligibility history hash mismatch")
        return EligibilityHistory.model_validate(payload)

    def rebuild_eligibility_history_current(self, market_id: str) -> Any | None:
        """Recreate the mutable projection from the latest immutable checkpoint."""
        from .universe import EligibilityHistoryCheckpoint
        if not market_id.strip(): raise ValueError("market_id is required")
        self.migrate(); conn, owns = self._conn()
        try:
            row = conn.execute(
                "SELECT r.canonical_json,r.canonical_sha256,"
                "o.market_id,ob.canonical_sha256,o.observed_at_utc,"
                "c.observation_id,ob.canonical_json "
                "FROM aca_eligibility_history_checkpoint c "
                "JOIN aca_contract_record r ON r.record_id=c.checkpoint_id "
                "JOIN aca_marketability_observation o "
                "ON o.observation_id=c.observation_id "
                "JOIN aca_contract_record ob ON ob.record_id=o.observation_id "
                "WHERE c.market_id=? "
                "ORDER BY c.observed_at_utc DESC,c.checkpoint_id DESC LIMIT 1",
                (market_id.strip(),),
            ).fetchone()
        finally:
            if owns: conn.close()
        if row is None: return None
        payload = json.loads(str(row[0]))
        if content_sha256(payload) != str(row[1]):
            raise AcaStoredContractCorruptionError("history checkpoint hash mismatch")
        checkpoint = EligibilityHistoryCheckpoint.model_validate(payload)
        try:
            observation_payload = json.loads(str(row[6]))
            observation = MarketabilityObservation.model_validate(observation_payload)
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            raise AcaStoredContractCorruptionError(
                "history checkpoint observation is corrupt"
            ) from exc
        if (
            checkpoint.market_id != market_id.strip()
            or checkpoint.market_id != str(row[2])
            or checkpoint.observation_id != str(row[5])
            or checkpoint.observation_id != observation.record_id
            or checkpoint.observation_sha256 != str(row[3])
            or content_sha256(observation_payload) != str(row[3])
            or canonical_datetime(checkpoint.observed_at) != str(row[4])
        ):
            raise AcaStoredContractCorruptionError(
                "history checkpoint observation binding mismatch"
            )
        self.save_eligibility_history_current(
            market_id=market_id,
            history=checkpoint.history,
            updated_at=checkpoint.observed_at,
        )
        return checkpoint.history

    def latest_next_evaluations(self) -> tuple[NextEvaluation, ...]:
        self.migrate()
        conn, owns = self._conn()
        try:
            rows = conn.execute(
                "SELECT c.market_id,r.canonical_json,r.canonical_sha256 "
                "FROM aca_next_evaluation_current c "
                "JOIN aca_contract_record r ON r.record_id=c.schedule_id "
                "ORDER BY c.market_id"
            ).fetchall()
        finally:
            if owns:
                conn.close()
        values: list[NextEvaluation] = []
        for market_id, raw, expected in rows:
            payload = json.loads(str(raw))
            if content_sha256(payload) != str(expected):
                raise AcaStoredContractCorruptionError(
                    "current next-evaluation contract hash mismatch"
                )
            item = NextEvaluation.model_validate(payload)
            if item.market_id != str(market_id):
                raise AcaStoredContractCorruptionError(
                    "current next-evaluation market binding mismatch"
                )
            values.append(item)
        return tuple(values)


def validate_hash_text(value: Any) -> str:
    text = str(value).strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError("expected a lowercase SHA-256 digest")
    return text
