from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal
import sqlite3
import threading

import pytest

from src.polymarket_alpha.contracts import (
    CommonEnvelope,
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    canonical_json,
)
from src.polymarket_alpha.storage import (
    AlphaRepository,
    ContractConflictError,
    RULE_CORPUS_REVISION_MIGRATION_ID,
    StoredContractCorruptionError,
    migrate,
    rule_corpus_revision_manifest,
    schema_manifest,
)


class StoredFixture(CommonEnvelope):
    value: str


NOW = datetime(2026, 8, 26, 6, 0, tzinfo=timezone.utc)


def _market_snapshot(record_id: str, *, yes_token_id: str = "yes-token") -> MarketSnapshot:
    return MarketSnapshot(
        record_id=record_id,
        run_id="storage-run",
        created_at=NOW,
        source="storage-fixture",
        source_version="v1",
        identity=MarketIdentity(
            event_id="event-1",
            market_id="market-1",
            condition_id="condition-1",
            yes_token_id=yes_token_id,
            no_token_id="no-token",
        ),
        title="Fixture market",
        question="Will the fixture event occur?",
        status=MarketStatus.ACTIVE,
        end_at=NOW + timedelta(days=1),
        rules_raw="Resolves YES if the official source confirms the event.",
        volume=Decimal("0"),
        liquidity=Decimal("0"),
        source_observed_at=NOW,
        ingested_at=NOW,
    )


def _legacy_digest(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT id, value FROM legacy_fixture ORDER BY id").fetchall()
    return hashlib.sha256(repr([tuple(row) for row in rows]).encode()).hexdigest()


def _legacy_schema(conn: sqlite3.Connection) -> tuple[tuple[str, str, str], ...]:
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'alpha_%' AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return tuple(tuple(row) for row in rows)


def test_additive_migration_preserves_legacy_and_is_idempotent(tmp_path):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    fixture = Path(__file__).with_name("fixtures") / "storage" / "legacy_fixture.sql"
    conn.executescript(fixture.read_text(encoding="utf-8"))
    before = _legacy_digest(conn)
    schema_before = _legacy_schema(conn)
    conn.commit()
    conn.close()

    assert migrate(db)["schema_version"] == "alpha_p0_v1.0"
    assert migrate(db) == schema_manifest()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    assert migrate(conn) == schema_manifest()
    assert _legacy_digest(conn) == before
    assert _legacy_schema(conn) == schema_before
    assert conn.execute("SELECT count(*) FROM legacy_fixture").fetchone()[0] == 2
    assert conn.execute("SELECT schema_version FROM alpha_schema_manifest").fetchone()[0] == "alpha_p0_v1.0"
    assert conn.execute(
        "SELECT sql_sha256 FROM alpha_schema_migrations WHERE migration_id = ?",
        (RULE_CORPUS_REVISION_MIGRATION_ID,),
    ).fetchone()[0] == rule_corpus_revision_manifest()["sql_sha256"]
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='alpha_rule_contract_revision_v2'"
    ).fetchone()[0] == "alpha_rule_contract_revision_v2"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_empty_and_interrupted_migration_rolls_back(tmp_path):
    db = tmp_path / "interrupted.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE alpha_schema_migrations(migration_id TEXT PRIMARY KEY, schema_version TEXT, sql_sha256 TEXT, applied_at_utc TEXT)")
    conn.execute("INSERT INTO alpha_schema_migrations VALUES ('alpha_p0_0001', 'wrong', 'x', 'now')")
    conn.commit()
    objects_before = conn.execute(
        "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    with pytest.raises(RuntimeError, match="incompatible"):
        migrate(conn)
    assert conn.execute("SELECT count(*) FROM alpha_schema_migrations").fetchone()[0] == 1
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='alpha_contract_record'").fetchone() is None
    assert conn.execute(
        "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall() == objects_before
    conn.close()


def test_repository_roundtrip_idempotence_and_concurrent_duplicate(tmp_path):
    db = tmp_path / "repo.db"
    item = StoredFixture(record_id="fixture:001", run_id="run-1", source="fixture", source_version="v1", value="canonical")
    repo = AlphaRepository(db)
    first = repo.save_contract(item)
    assert repo.save_contract(item) == first
    assert repo.get_contract(item.record_id) == json.loads(canonical_json(item))
    changed = item.model_copy(update={"value": "changed"})
    with pytest.raises(ContractConflictError):
        repo.save_contract(changed)

    barrier = threading.Barrier(2)
    results: list[str] = []
    errors: list[BaseException] = []
    concurrent = StoredFixture(record_id="fixture:002", run_id="run-1", source="fixture", source_version="v1", value="same")
    def write() -> None:
        try:
            barrier.wait()
            results.append(AlphaRepository(db).save_contract(concurrent))
        except BaseException as exc:  # assertion reports any sqlite locking regression
            errors.append(exc)
    threads = [threading.Thread(target=write) for _ in range(2)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert not errors
    assert results == [concurrent.canonical_sha256, concurrent.canonical_sha256]

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE alpha_contract_record SET canonical_json = ? WHERE record_id = ?",
        ('{"tampered":true}', item.record_id),
    )
    conn.commit()
    conn.close()
    with pytest.raises(StoredContractCorruptionError, match="hash mismatch"):
        repo.get_contract(item.record_id)


def test_prediction_position_check_and_foreign_keys(tmp_path):
    db = tmp_path / "checks.db"
    migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO alpha_prediction_record VALUES ('p','m','d','e','packet','book','LIVE')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO alpha_candidate_recall_hit VALUES ('missing','also-missing')")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_market_projection_freezes_identity_and_yes_no_token_mapping(tmp_path):
    db = tmp_path / "market.db"
    repo = AlphaRepository(db)
    snapshot = _market_snapshot("market_snapshot:001")
    repo.save_contract(snapshot)
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT event_id, condition_id, yes_token_id, no_token_id FROM alpha_market"
    ).fetchone() == ("event-1", "condition-1", "yes-token", "no-token")
    assert conn.execute(
        "SELECT side, token_id FROM alpha_market_token_map ORDER BY side"
    ).fetchall() == [("NO", "no-token"), ("YES", "yes-token")]
    conn.close()

    conflict = _market_snapshot("market_snapshot:002", yes_token_id="changed-yes-token")
    with pytest.raises(ContractConflictError, match="conflicting canonical identity"):
        repo.save_contract(conflict)
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT count(*) FROM alpha_contract_record WHERE record_id='market_snapshot:002'"
    ).fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
