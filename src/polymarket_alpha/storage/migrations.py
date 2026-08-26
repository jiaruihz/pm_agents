"""Transactional, additive migrations for the Alpha P0 SQLite namespace."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import sqlite3
from typing import Iterator

from ..contracts import ALPHA_CONTRACT_VERSION, content_sha256


ALPHA_SCHEMA_VERSION = ALPHA_CONTRACT_VERSION
MIGRATION_ID = "alpha_p0_0001"

# Every object is alpha-namespaced so a shared legacy research database is never
# altered.  This migration is intentionally additive; rollback is a reader pin,
# never a DROP or DELETE operation.
MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS alpha_schema_migrations (
    migration_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    sql_sha256 TEXT NOT NULL CHECK(length(sql_sha256) = 64),
    applied_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alpha_schema_manifest (
    schema_version TEXT PRIMARY KEY,
    migration_id TEXT NOT NULL REFERENCES alpha_schema_migrations(migration_id),
    contract_version TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256) = 64),
    applied_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alpha_contract_record (
    record_id TEXT PRIMARY KEY,
    contract_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64),
    created_at_utc TEXT NOT NULL,
    UNIQUE(contract_type, canonical_sha256)
);
CREATE TABLE IF NOT EXISTS alpha_event (
    event_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS alpha_market (
    market_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES alpha_event(event_id),
    condition_id TEXT,
    yes_token_id TEXT NOT NULL,
    no_token_id TEXT NOT NULL,
    CHECK(yes_token_id <> no_token_id)
);
CREATE TABLE IF NOT EXISTS alpha_event_market (
    event_id TEXT NOT NULL REFERENCES alpha_event(event_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    PRIMARY KEY(event_id, market_id)
);
CREATE TABLE IF NOT EXISTS alpha_market_alias (
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    source TEXT NOT NULL, alias_type TEXT NOT NULL,
    alias_value TEXT NOT NULL, effective_from_utc TEXT NOT NULL,
    effective_to_utc TEXT,
    PRIMARY KEY(market_id, source, alias_type, alias_value, effective_from_utc)
);
CREATE TABLE IF NOT EXISTS alpha_raw_artifact (
    artifact_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64)
);
CREATE TABLE IF NOT EXISTS alpha_market_snapshot_revision (
    snapshot_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    revision_sha256 TEXT NOT NULL CHECK(length(revision_sha256) = 64),
    UNIQUE(market_id, revision_sha256)
);
CREATE TABLE IF NOT EXISTS alpha_market_token_map (
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id), token_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('YES','NO')), PRIMARY KEY(market_id, side), UNIQUE(market_id, token_id)
);
CREATE TABLE IF NOT EXISTS alpha_change_event (
    change_event_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id)
);
CREATE TABLE IF NOT EXISTS alpha_orderbook_snapshot (
    snapshot_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id)
);
CREATE TABLE IF NOT EXISTS alpha_orderbook_leg (
    snapshot_id TEXT NOT NULL REFERENCES alpha_orderbook_snapshot(snapshot_id),
    side TEXT NOT NULL CHECK(side IN ('YES','NO')), token_id TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, side)
);
CREATE TABLE IF NOT EXISTS alpha_recall_hit (
    recall_hit_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id), recaller TEXT NOT NULL, recaller_version TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64),
    UNIQUE(recaller, recaller_version, canonical_sha256)
);
CREATE TABLE IF NOT EXISTS alpha_candidate (
    candidate_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    current_card_id TEXT NOT NULL REFERENCES alpha_contract_record(record_id),
    state TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64)
);
CREATE TABLE IF NOT EXISTS alpha_candidate_revision (
    card_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    candidate_id TEXT NOT NULL REFERENCES alpha_candidate(candidate_id),
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64),
    UNIQUE(candidate_id, canonical_sha256)
);
CREATE TABLE IF NOT EXISTS alpha_candidate_recall_hit (
    candidate_id TEXT NOT NULL REFERENCES alpha_candidate(candidate_id),
    recall_hit_id TEXT NOT NULL REFERENCES alpha_recall_hit(recall_hit_id),
    PRIMARY KEY(candidate_id, recall_hit_id)
);
CREATE TABLE IF NOT EXISTS alpha_candidate_transition (
    transition_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    candidate_id TEXT NOT NULL REFERENCES alpha_candidate(candidate_id),
    from_state TEXT NOT NULL, to_state TEXT NOT NULL, event_type TEXT NOT NULL,
    CHECK(event_type <> 'STATE_TRANSITION' OR from_state <> to_state)
);
CREATE TABLE IF NOT EXISTS alpha_rule_contract_revision (
    rule_contract_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id), rule_hash TEXT NOT NULL CHECK(length(rule_hash) = 64),
    UNIQUE(market_id, rule_hash)
);
CREATE TABLE IF NOT EXISTS alpha_rule_gate_decision (
    gate_decision_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    rule_contract_id TEXT REFERENCES alpha_rule_contract_revision(rule_contract_id)
);
CREATE TABLE IF NOT EXISTS alpha_research_packet (
    packet_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id), packet_stage TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alpha_research_result (
    result_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id), packet_id TEXT REFERENCES alpha_research_packet(packet_id)
);
CREATE TABLE IF NOT EXISTS alpha_evidence_item (
    evidence_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id), content_sha256 TEXT CHECK(content_sha256 IS NULL OR length(content_sha256) = 64)
);
CREATE TABLE IF NOT EXISTS alpha_review_decision (
    decision_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id), market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    rule_hash TEXT NOT NULL CHECK(length(rule_hash) = 64)
);
CREATE TABLE IF NOT EXISTS alpha_prediction_record (
    prediction_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL, decision_id TEXT NOT NULL REFERENCES alpha_review_decision(decision_id),
    probability_estimate_id TEXT NOT NULL REFERENCES alpha_contract_record(record_id),
    packet_id TEXT NOT NULL REFERENCES alpha_research_packet(packet_id),
    orderbook_snapshot_id TEXT NOT NULL REFERENCES alpha_orderbook_snapshot(snapshot_id),
    position_state TEXT NOT NULL CHECK(position_state IN ('NO_POSITION','SIMULATED'))
);
CREATE TABLE IF NOT EXISTS alpha_run_artifact_link (
    run_id TEXT NOT NULL, artifact_id TEXT NOT NULL REFERENCES alpha_contract_record(record_id),
    relation TEXT NOT NULL, PRIMARY KEY(run_id, artifact_id, relation)
);
"""


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def schema_manifest() -> dict[str, str]:
    """Return the pinned manifest used by readers and migrations."""
    return {"schema_version": ALPHA_SCHEMA_VERSION, "migration_id": MIGRATION_ID, "sql_sha256": _sha(MIGRATION_SQL)}


@contextmanager
def _connection(target: str | Path | sqlite3.Connection) -> Iterator[tuple[sqlite3.Connection, bool]]:
    if isinstance(target, sqlite3.Connection):
        yield target, False
        return
    if not isinstance(target, (str, Path)):
        raise TypeError("target must be a caller-provided SQLite path or connection")
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        yield conn, True
    finally:
        conn.close()


def migrate(target: str | Path | sqlite3.Connection) -> dict[str, str]:
    """Apply the sole Alpha migration to *target*, atomically and idempotently."""
    manifest = schema_manifest()
    with _connection(target) as (conn, _owned):
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute("BEGIN IMMEDIATE")
            # ``sqlite3.Connection.executescript`` implicitly commits before it
            # runs, so execute statements individually to keep DDL transactional.
            for statement in MIGRATION_SQL.split(";\n"):
                if statement.strip():
                    conn.execute(statement)
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            conn.execute(
                "INSERT OR IGNORE INTO alpha_schema_migrations VALUES (?, ?, ?, ?)",
                (MIGRATION_ID, ALPHA_SCHEMA_VERSION, manifest["sql_sha256"], now),
            )
            manifest_sha256 = content_sha256(manifest)
            conn.execute(
                "INSERT OR IGNORE INTO alpha_schema_manifest VALUES (?, ?, ?, ?, ?)",
                (ALPHA_SCHEMA_VERSION, MIGRATION_ID, ALPHA_CONTRACT_VERSION, manifest_sha256, now),
            )
            row = conn.execute(
                "SELECT schema_version, sql_sha256 FROM alpha_schema_migrations WHERE migration_id = ?",
                (MIGRATION_ID,),
            ).fetchone()
            if row is None or tuple(row) != (ALPHA_SCHEMA_VERSION, manifest["sql_sha256"]):
                raise RuntimeError("incompatible Alpha migration already recorded")
            manifest_row = conn.execute(
                "SELECT migration_id, contract_version, manifest_sha256 FROM alpha_schema_manifest WHERE schema_version = ?",
                (ALPHA_SCHEMA_VERSION,),
            ).fetchone()
            if manifest_row is None or tuple(manifest_row) != (MIGRATION_ID, ALPHA_CONTRACT_VERSION, manifest_sha256):
                raise RuntimeError("incompatible Alpha schema manifest already recorded")
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return manifest
