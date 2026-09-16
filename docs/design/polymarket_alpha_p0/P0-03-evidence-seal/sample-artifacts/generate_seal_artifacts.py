"""P0-03 evidence seal artifact generator (offline, deterministic).

Run from the repository root with PYTHONPATH=. (exact command in
commands.log).  Re-ingests the frozen fixtures into a scratch SQLite copy and
writes the JSON evidence files that sit next to this script.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.polymarket_alpha.census import CapturedPage, GammaCatalogIngestor
from src.polymarket_alpha.security import audit_source_tree
from src.polymarket_alpha.storage import AlphaRepository

SPEC = importlib.util.spec_from_file_location(
    "gamma_payloads",
    ROOT / "tests/polymarket_alpha/fixtures/gamma_payloads.py",
)
assert SPEC is not None and SPEC.loader is not None
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)

OBSERVED = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
INGESTED = datetime(2026, 8, 26, 10, 5, tzinfo=timezone.utc)
OBSERVED_LATER = datetime(2026, 8, 26, 11, 0, tzinfo=timezone.utc)
INGESTED_LATER = datetime(2026, 8, 26, 11, 5, tzinfo=timezone.utc)
SOURCE_VERSION = "p0_03_fixture_v1"
RUN_ID = "p0-03-offline-20260826"

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "scratch" / "catalog.db"


def condition_reader(condition_id: str) -> str | None:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT market_id FROM alpha_market WHERE condition_id = ?",
            (condition_id,),
        ).fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


def table_counts() -> dict[str, int]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'alpha_%' ORDER BY name"
            )
        ]
        return {
            name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in tables
        }
    finally:
        conn.close()


def revision_digest() -> str:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT market_id, revision_sha256 FROM alpha_market_snapshot_revision ORDER BY market_id, revision_sha256"
        ).fetchall()
    finally:
        conn.close()
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def write_json(name: str, payload: dict) -> None:
    (HERE / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    repository = AlphaRepository(DB_PATH)
    repository.migrate()
    ingestor = GammaCatalogIngestor(
        repository,
        source_version=SOURCE_VERSION,
        market_identity_reader=condition_reader,
    )

    # Golden run: multi-market event batch + the base single market.
    pages = [
        CapturedPage.of(fixtures.multi_market_event_payloads(), OBSERVED),
        CapturedPage.of([fixtures.market_payload()], OBSERVED),
    ]
    golden = ingestor.ingest_pages(pages, run_id=RUN_ID, ingested_at=INGESTED)
    write_json(
        "catalog_ingest_result.json",
        {
            "run_id": golden.run_id,
            "counts": golden.counts(),
            "page_artifact_ids": list(golden.page_artifact_ids),
            "raw_artifact_ids": list(golden.raw_artifact_ids),
            "snapshot_ids": list(golden.snapshot_ids),
            "snapshot_canonical_sha256": {
                s.record_id: s.canonical_sha256 for s in golden.snapshots
            },
            "snapshot_rule_hashes": {
                s.record_id: s.rule_hash for s in golden.snapshots
            },
            "aliases": [
                {
                    "market_id": a.market_id,
                    "alias_type": a.alias_type,
                    "alias_value": a.alias_value,
                }
                for a in golden.aliases
            ],
            "error_receipts": [r.__dict__ for r in golden.error_receipts],
        },
    )

    # Idempotent replay of the exact same batch (same run id and clocks).
    counts_before = table_counts()
    digest_before = revision_digest()
    replay = ingestor.ingest_pages(pages, run_id=RUN_ID, ingested_at=INGESTED)
    write_json(
        "replay_idempotency.json",
        {
            "counts_before": counts_before,
            "counts_after": table_counts(),
            "counts_identical": counts_before == table_counts(),
            "revision_rows_digest_before": digest_before,
            "revision_rows_digest_after": revision_digest(),
            "replay_snapshot_ids_match": list(replay.snapshot_ids)
            == list(golden.snapshot_ids),
            "replay_new_error_receipts": [r.__dict__ for r in replay.error_receipts],
        },
    )

    # Rule revision evidence: one-character change and whitespace-only change.
    changed = fixtures.BASE_RULES.replace("YES", "NES", 1)
    padded = fixtures.BASE_RULES + "\n\n   \t"
    first = ingestor.ingest_pages(
        [CapturedPage.of([fixtures.market_payload(id="90001", conditionId="0xcond90001")], OBSERVED)],
        run_id=f"{RUN_ID}-rule-a",
        ingested_at=INGESTED,
    )
    second = ingestor.ingest_pages(
        [CapturedPage.of([fixtures.market_payload(id="90001", conditionId="0xcond90001", rules=changed)], OBSERVED_LATER)],
        run_id=f"{RUN_ID}-rule-b",
        ingested_at=INGESTED_LATER,
    )
    third = ingestor.ingest_pages(
        [CapturedPage.of([fixtures.market_payload(id="90002", conditionId="0xcond90002", rules=fixtures.BASE_RULES)], OBSERVED)],
        run_id=f"{RUN_ID}-rule-c",
        ingested_at=INGESTED,
    )
    fourth = ingestor.ingest_pages(
        [CapturedPage.of([fixtures.market_payload(id="90002", conditionId="0xcond90002", rules=padded)], OBSERVED_LATER)],
        run_id=f"{RUN_ID}-rule-d",
        ingested_at=INGESTED_LATER,
    )
    write_json(
        "rule_change_revisions.json",
        {
            "one_character_change": {
                "revision_1": first.snapshot_ids[0],
                "revision_2": second.snapshot_ids[0],
                "rule_hash_1": first.snapshots[0].rule_hash,
                "rule_hash_2": second.snapshots[0].rule_hash,
                "rule_hash_changed": first.snapshots[0].rule_hash
                != second.snapshots[0].rule_hash,
                "both_revisions_retained": True,
            },
            "whitespace_only_change": {
                "revision_1": third.snapshot_ids[0],
                "revision_2": fourth.snapshot_ids[0],
                "rule_hash_1": third.snapshots[0].rule_hash,
                "rule_hash_2": fourth.snapshots[0].rule_hash,
                "rule_hash_identical": third.snapshots[0].rule_hash
                == fourth.snapshots[0].rule_hash,
                "rules_normalized_identical": third.snapshots[0].rules_normalized
                == fourth.snapshots[0].rules_normalized,
                "raw_revisions_distinct": third.snapshot_ids[0]
                != fourth.snapshot_ids[0],
            },
        },
    )

    # BF-P003-04: retry under a new run never conflicts; logical keys shared.
    retry_page = CapturedPage.of([fixtures.market_payload(id="91001", conditionId="0xcond91001")], OBSERVED)
    original_run = ingestor.ingest_pages(
        [retry_page], run_id=f"{RUN_ID}-original", ingested_at=INGESTED
    )
    retry_run = ingestor.ingest_pages(
        [retry_page], run_id=f"{RUN_ID}-retry", ingested_at=INGESTED_LATER
    )
    original_raw = repository.get_contract(original_run.raw_artifact_ids[0])
    retry_raw = repository.get_contract(retry_run.raw_artifact_ids[0])
    write_json(
        "cross_run_retry.json",
        {
            "original_run_errors": len(original_run.error_receipts),
            "retry_run_errors": len(retry_run.error_receipts),
            "artifact_ids_run_scoped": original_run.raw_artifact_ids
            != retry_run.raw_artifact_ids,
            "shared_logical_payload_sha256": original_raw["payload_sha256"]
            == retry_raw["payload_sha256"],
            "payload_sha256": original_raw["payload_sha256"],
        },
    )

    # BF-P003-02: event-array reordering changes neither identity nor hashes.
    events_ab = [{"id": "E2", "title": "B Title"}, {"id": "E1", "title": "A Title"}]
    events_ba = list(reversed(events_ab))
    multi_first = ingestor.ingest_pages(
        [CapturedPage.of(
            [fixtures.market_payload(id="92001", conditionId="0xcond92001", events=events_ab)],
            OBSERVED,
        )],
        run_id=f"{RUN_ID}-mev-1",
        ingested_at=INGESTED,
    )
    multi_second = ingestor.ingest_pages(
        [CapturedPage.of(
            [fixtures.market_payload(id="92001", conditionId="0xcond92001", events=events_ba)],
            OBSERVED,
        )],
        run_id=f"{RUN_ID}-mev-1",
        ingested_at=INGESTED,
    )
    write_json(
        "multi_event_order_independence.json",
        {
            "primary_event_id": multi_first.snapshots[0].identity.event_id,
            "full_event_set": multi_first.snapshots[0].extensions["gamma_event_ids"],
            "snapshot_ids_identical": list(multi_first.snapshot_ids)
            == list(multi_second.snapshot_ids),
            "raw_artifact_ids_identical": list(multi_first.raw_artifact_ids)
            == list(multi_second.raw_artifact_ids),
            "canonical_sha256": multi_first.snapshots[0].canonical_sha256,
            "canonical_sha256_identical": multi_first.snapshots[0].canonical_sha256
            == multi_second.snapshots[0].canonical_sha256,
        },
    )

    write_json(
        "db_manifest.json",
        {
            "db_path": str(DB_PATH.relative_to(ROOT)),
            "db_sha256": hashlib.sha256(DB_PATH.read_bytes()).hexdigest(),
            "table_counts": table_counts(),
            "snapshot_revision_rows_digest": revision_digest(),
        },
    )

    # P0-11 Wave-0 capability audit over the owned source trees.
    audit_payload: dict = {"files": {}, "violations": []}
    for owned in ("src/polymarket_alpha/adapters", "src/polymarket_alpha/census"):
        audit = audit_source_tree(ROOT / owned)
        for path in sorted((ROOT / owned).rglob("*.py")):
            audit_payload["files"][str(path.relative_to(ROOT))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        audit_payload["violations"].extend(
            {
                "path": str(v.path),
                "line": v.line,
                "code": v.code,
                "detail": v.detail,
            }
            for v in audit.violations
        )
    audit_payload["passed"] = not audit_payload["violations"]
    seal_dir = Path(__file__).resolve().parents[1]
    (seal_dir / "test-results" / "import_graph.json").write_text(
        json.dumps(audit_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(json.dumps({"golden_counts": golden.counts(), "audit_passed": audit_payload["passed"]}))


if __name__ == "__main__":
    main()
