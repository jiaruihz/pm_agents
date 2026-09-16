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
    MarketAlias,
    canonical_json,
)
from src.polymarket_alpha.storage import (
    AlphaRepository,
    CATALOG_INTEGRITY_MIGRATION_ID,
    ContractConflictError,
    RULE_CONTRACT_INSTANCE_MIGRATION_ID,
    RULE_CORPUS_REVISION_MIGRATION_ID,
    StoredContractCorruptionError,
    catalog_integrity_manifest,
    migrate,
    rule_corpus_revision_manifest,
    rule_contract_instance_manifest,
    schema_manifest,
)
from src.polymarket_alpha.adapters import build_market_payload_artifact, build_page_artifact


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
        "SELECT sql_sha256 FROM alpha_schema_migrations WHERE migration_id = ?",
        (CATALOG_INTEGRITY_MIGRATION_ID,),
    ).fetchone()[0] == catalog_integrity_manifest()["sql_sha256"]
    assert conn.execute(
        "SELECT sql_sha256 FROM alpha_schema_migrations WHERE migration_id = ?",
        (RULE_CONTRACT_INSTANCE_MIGRATION_ID,),
    ).fetchone()[0] == rule_contract_instance_manifest()["sql_sha256"]
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


def test_condition_identity_migration_is_additive_and_fails_closed_on_legacy_duplicates(tmp_path):
    db = tmp_path / "duplicate-condition.db"
    migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("DROP INDEX alpha_market_condition_uidx")
    conn.execute("DELETE FROM alpha_schema_migrations WHERE migration_id=?", (CATALOG_INTEGRITY_MIGRATION_ID,))
    conn.execute("INSERT INTO alpha_event VALUES ('event-a')")
    conn.execute("INSERT INTO alpha_event VALUES ('event-b')")
    conn.execute("INSERT INTO alpha_market VALUES ('market-a','event-a','duplicate','yes-a','no-a')")
    conn.execute("INSERT INTO alpha_market VALUES ('market-b','event-b','duplicate','yes-b','no-b')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        migrate(conn)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='alpha_market_condition_uidx'").fetchone() is None
    assert conn.execute("SELECT count(*) FROM alpha_market").fetchone()[0] == 2
    assert conn.execute(
        "SELECT sql_sha256 FROM alpha_schema_migrations WHERE migration_id=?",
        (CATALOG_INTEGRITY_MIGRATION_ID,),
    ).fetchone() is None
    conn.close()


def test_condition_lookup_uniqueness_and_multi_event_projection_fail_closed(tmp_path):
    db = tmp_path / "conditions.db"
    repo = AlphaRepository(db)
    first = _market_snapshot("market_snapshot:condition")
    first = first.model_copy(update={"extensions": {"gamma_event_ids": ["event-1", "event-2"]}})
    repo.save_contract(first)
    assert repo.market_id_for_condition("condition-1") == "market-1"
    assert repo.market_id_for_condition("missing") is None
    with pytest.raises(ValueError, match="non-blank"):
        repo.market_id_for_condition(" \t")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT event_id FROM alpha_event_market ORDER BY event_id").fetchall() == [("event-1",), ("event-2",)]
    conn.close()

    duplicate = _market_snapshot("market_snapshot:condition-duplicate")
    duplicate = duplicate.model_copy(update={"identity": duplicate.identity.model_copy(update={"market_id": "market-2"})})
    with pytest.raises(ContractConflictError, match="condition_id"):
        repo.save_contract(duplicate)
    malformed = _market_snapshot("market_snapshot:bad-events")
    malformed = malformed.model_copy(update={"extensions": {"gamma_event_ids": ["event-2", "event-1"]}})
    with pytest.raises(ContractConflictError, match="sorted"):
        repo.save_contract(malformed)


def test_raw_artifact_hash_alias_intervals_and_run_links(tmp_path):
    db = tmp_path / "projections.db"
    repo = AlphaRepository(db)
    snapshot = _market_snapshot("market_snapshot:alias")
    repo.save_contract(snapshot)
    page = build_page_artifact(
        [{"id": "one"}], offset=0, page_index=0, run_id="run-a", source_version="test",
        source_observed_at=NOW, ingested_at=NOW,
    )
    payload = build_market_payload_artifact(
        {"id": "one"}, run_id="run-a", source_version="test", source_observed_at=NOW, ingested_at=NOW,
    )
    repo.save_contract(page)
    repo.save_contract(payload)
    # Exact replay repairs a typed projection created by older repository
    # code rather than returning early and leaving it absent forever.
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM alpha_raw_artifact WHERE artifact_id=?", (page.record_id,))
    conn.commit()
    conn.close()
    repo.save_contract(page)
    repo.link_run_artifact("run-a", page.record_id, "page")
    repo.link_run_artifact("run-a", page.record_id, "page")
    with pytest.raises(ContractConflictError, match="run-artifact"):
        repo.link_run_artifact("run-a", "missing:artifact", "page")
    first = MarketAlias(market_id="market-1", source="gamma", alias_type="SLUG", alias_value="old", effective_from=NOW)
    repo.save_market_alias(first)
    repo.save_market_alias(first)
    changed = first.model_copy(update={"alias_value": "new", "effective_from": NOW + timedelta(minutes=1)})
    repo.save_market_alias(changed)
    repo.save_market_alias(first)  # replay of the now-closed historical row is idempotent
    with pytest.raises(ContractConflictError, match="same or earlier"):
        repo.save_market_alias(first.model_copy(update={"alias_value": "third"}))
    conn = sqlite3.connect(db)
    assert {row[0] for row in conn.execute("SELECT content_sha256 FROM alpha_raw_artifact")} == {
        page.page_sha256, payload.payload_sha256,
    }
    assert conn.execute("SELECT alias_value, effective_to_utc IS NULL FROM alpha_market_alias ORDER BY effective_from_utc").fetchall() == [("old", 0), ("new", 1)]
    assert conn.execute("SELECT count(*) FROM alpha_run_artifact_link").fetchone()[0] == 1
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='alpha_market_alias_active_uidx'"
    ).fetchone() == ("alpha_market_alias_active_uidx",)
    conn.close()


def test_alias_same_value_is_independent_per_market_and_concurrent_replay(tmp_path):
    db = tmp_path / "aliases.db"
    repo = AlphaRepository(db)
    repo.save_contract(_market_snapshot("market_snapshot:one"))
    other = _market_snapshot("market_snapshot:two")
    other = other.model_copy(update={"identity": other.identity.model_copy(update={"market_id": "market-2", "condition_id": "condition-2", "event_id": "event-2", "yes_token_id": "yes-2", "no_token_id": "no-2"})})
    repo.save_contract(other)
    alias = MarketAlias(market_id="market-1", source="gamma", alias_type="SLUG", alias_value="same", effective_from=NOW)
    repo.save_market_alias(alias)
    repo.save_market_alias(alias.model_copy(update={"effective_from": NOW + timedelta(minutes=2)}))
    repo.save_market_alias(alias.model_copy(update={"market_id": "market-2"}))
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)
    def write() -> None:
        try:
            barrier.wait()
            AlphaRepository(db).save_market_alias(alias)
        except BaseException as exc:
            errors.append(exc)
    threads = [threading.Thread(target=write) for _ in range(2)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert not errors
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT market_id, count(*) FROM alpha_market_alias GROUP BY market_id ORDER BY market_id").fetchall() == [("market-1", 1), ("market-2", 1)]
    conn.close()
