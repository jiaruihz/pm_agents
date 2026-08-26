"""P0-03 Gamma catalog adapter tests: identity, revisions, drift, pagination.

All inputs are offline fixtures; no network, no production DB.  The tests
double as the acceptance matrix for the twelve required coverage items, the
two BLOCKING_FOR_P0_03_ACCEPTANCE findings (F-02 duplicate pagination, F-05
response identity verification) and the rework items BF-P003-01..06 from the
Codex independent review.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.polymarket_alpha.adapters import (
    DEFAULT_MAX_PAGES,
    GammaIdentityMismatchError,
    GammaMarketPayloadArtifact,
    collect_gamma_pages,
    lifecycle_status,
    map_yes_no_tokens,
    verify_market_response_identity,
)
from src.polymarket_alpha.adapters.gamma_identity import GammaTokenMappingError
from src.polymarket_alpha.adapters.gamma_pages import PaginationTermination
from src.polymarket_alpha.census import CapturedPage, GammaCatalogIngestor
from src.polymarket_alpha.contracts import MarketIdentity, MarketStatus
from src.polymarket_alpha.security import audit_source_tree
from src.polymarket_alpha.storage import AlphaRepository

_SPEC = importlib.util.spec_from_file_location(
    "gamma_payloads", Path(__file__).with_name("fixtures") / "gamma_payloads.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_fixtures = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_fixtures)
market_payload = _fixtures.market_payload
multi_market_event_payloads = _fixtures.multi_market_event_payloads
BASE_RULES = _fixtures.BASE_RULES

OBSERVED = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
INGESTED = datetime(2026, 8, 26, 10, 5, tzinfo=timezone.utc)
OBSERVED_LATER = datetime(2026, 8, 26, 11, 0, tzinfo=timezone.utc)
INGESTED_LATER = datetime(2026, 8, 26, 11, 5, tzinfo=timezone.utc)
SOURCE_VERSION = "p0_03_fixture_v1"


def _repository(tmp_path: Path) -> tuple[AlphaRepository, Path]:
    db = tmp_path if tmp_path.suffix == ".db" else tmp_path / "catalog.db"
    repository = AlphaRepository(db)
    repository.migrate()  # materialize the schema so read-only probes always work
    return repository, db


def _table_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {
            name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in (
                "alpha_contract_record",
                "alpha_event",
                "alpha_event_market",
                "alpha_market",
                "alpha_market_snapshot_revision",
            )
        }
    finally:
        conn.close()


def _ingestor(repository: AlphaRepository, db_path: Path) -> GammaCatalogIngestor:
    del db_path
    return GammaCatalogIngestor(repository, source_version=SOURCE_VERSION)


# BF-P003-01: stored identity lookup is repository-owned, not injectable
def test_constructor_uses_repository_identity_capability(tmp_path: Path) -> None:
    repository, _db = _repository(tmp_path)
    assert GammaCatalogIngestor(
        repository, source_version=SOURCE_VERSION
    ).source_version == SOURCE_VERSION


# 1. multi-market event and event-market join
def test_multi_market_event_join_writes_event_market_rows(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    result = _ingestor(repository, db).ingest_pages(
        [CapturedPage.of(multi_market_event_payloads(), OBSERVED)],
        run_id="run-multi",
        ingested_at=INGESTED,
    )

    assert result.counts() == {
        "pages": 1,
        "raw_market_artifacts": 3,
        "snapshots": 3,
        "aliases": 3,
        "drift_receipts": 0,
        "error_receipts": 0,
    }
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        join_rows = conn.execute(
            "SELECT event_id, market_id FROM alpha_event_market ORDER BY market_id"
        ).fetchall()
        condition_rows = conn.execute(
            "SELECT market_id, condition_id FROM alpha_market ORDER BY market_id"
        ).fetchall()
    finally:
        conn.close()
    assert join_rows == [
        ("event-multi", "20001"),
        ("event-multi", "20002"),
        ("event-multi", "20003"),
    ]
    # 2. missing condition id stays an explicit NULL pending alternate key
    assert condition_rows == [
        ("20001", "0xcond20001"),
        ("20002", "0xcond20002"),
        ("20003", None),
    ]


# BF-P003-02: multi-event market keeps every relationship, order-independent
def test_multi_event_market_is_order_independent(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    ingestor = _ingestor(repository, db)
    events_ab = [{"id": "E2", "title": "B Title"}, {"id": "E1", "title": "A Title"}]
    events_ba = list(reversed(events_ab))

    first = ingestor.ingest_pages(
        [CapturedPage.of([market_payload(id="21001", conditionId="0xcond21001", events=events_ab)], OBSERVED)],
        run_id="run-mev-1",
        ingested_at=INGESTED,
    )
    second = ingestor.ingest_pages(
        [CapturedPage.of([market_payload(id="21001", conditionId="0xcond21001", events=events_ba)], OBSERVED)],
        run_id="run-mev-1",
        ingested_at=INGESTED,
    )

    snapshot = first.snapshots[0]
    assert snapshot.identity.event_id == "E1"  # deterministic minimum, not array order
    assert snapshot.extensions["gamma_event_ids"] == ["E1", "E2"]  # full set preserved
    # reordering the events array changes neither identity nor canonical bytes
    assert second.snapshot_ids == first.snapshot_ids
    assert second.raw_artifact_ids == first.raw_artifact_ids
    assert second.page_artifact_ids == first.page_artifact_ids
    assert second.snapshots[0].canonical_sha256 == snapshot.canonical_sha256
    assert second.counts()["error_receipts"] == 0


# 2. duplicate condition id fails closed (in-batch and across batches)
def test_duplicate_condition_id_fails_closed_in_batch(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    payloads = [
        market_payload(id="30001", conditionId="0xdup", slug="dup-a"),
        market_payload(id="30002", conditionId="0xdup", slug="dup-b"),
    ]
    result = _ingestor(repository, db).ingest_pages(
        [CapturedPage.of(payloads, OBSERVED)], run_id="run-dup", ingested_at=INGESTED
    )

    assert result.counts()["snapshots"] == 1
    assert len(result.error_receipts) == 1
    receipt = result.error_receipts[0]
    assert receipt.error_code == "CONDITION_ID_CONFLICT"
    assert receipt.market_ref == "30002"
    assert receipt.raw_artifact_id is not None


def test_duplicate_condition_id_fails_closed_across_batches(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    ingestor = _ingestor(repository, db)
    first = ingestor.ingest_pages(
        [CapturedPage.of([market_payload(id="30001", conditionId="0xdup")], OBSERVED)],
        run_id="run-dup-1",
        ingested_at=INGESTED,
    )
    second = ingestor.ingest_pages(
        [CapturedPage.of([market_payload(id="30999", conditionId="0xdup")], OBSERVED_LATER)],
        run_id="run-dup-2",
        ingested_at=INGESTED_LATER,
    )

    assert first.counts()["error_receipts"] == 0
    assert [r.error_code for r in second.error_receipts] == ["CONDITION_ID_CONFLICT"]
    assert _table_counts(db)["alpha_market"] == 1


# 3. YES/NO token label permutation
def test_token_order_reversal_keeps_label_mapping() -> None:
    yes, no = map_yes_no_tokens('["No", "Yes"]', '["tok-no", "tok-yes"]')
    assert (yes, no) == ("tok-yes", "tok-no")
    yes, no = map_yes_no_tokens(["YES", "NO"], ["tok-yes-2", "tok-no-2"])
    assert (yes, no) == ("tok-yes-2", "tok-no-2")


@pytest.mark.parametrize(
    "outcomes,tokens",
    [
        ('["Yes", "No", "Maybe"]', '["a", "b", "c"]'),
        ('["Over", "Under"]', '["a", "b"]'),
        ('["Yes", "Yes"]', '["a", "b"]'),
        ('["Yes", "No"]', '["", "b"]'),
        ('["Yes", "No"]', '["same", "same"]'),
        ("[]", "[]"),
        ('["Yes", "No"]', '[{"a": 1}, {"b": 2}]'),
    ],
)
def test_invalid_token_mappings_fail_closed(outcomes: str, tokens: str) -> None:
    with pytest.raises(GammaTokenMappingError):
        map_yes_no_tokens(outcomes, tokens)


def test_reordered_outcomes_ingest_with_correct_yes_no_legs(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    payload = market_payload(
        id="31001",
        outcomes='["No", "Yes"]',
        clobTokenIds='["tok-no-31001", "tok-yes-31001"]',
    )
    result = _ingestor(repository, db).ingest_pages(
        [CapturedPage.of([payload], OBSERVED)], run_id="run-rev", ingested_at=INGESTED
    )

    assert result.counts()["error_receipts"] == 0
    snapshot = result.snapshots[0]
    assert snapshot.identity.yes_token_id == "tok-yes-31001"
    assert snapshot.identity.no_token_id == "tok-no-31001"


# 4. title/slug are aliases, never identity
def test_same_slug_different_markets_stay_separate(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    payloads = [
        market_payload(id="40001", conditionId="0x40001", slug="same-slug"),
        market_payload(id="40002", conditionId="0x40002", slug="same-slug"),
    ]
    result = _ingestor(repository, db).ingest_pages(
        [CapturedPage.of(payloads, OBSERVED)], run_id="run-slug", ingested_at=INGESTED
    )

    assert result.counts()["snapshots"] == 2
    assert _table_counts(db)["alpha_market"] == 2
    for snapshot in result.snapshots:
        assert "slug" not in MarketIdentity.model_fields  # identity is slug-free
        assert snapshot.slug == "same-slug"  # slug survives as descriptive data
    assert {alias.market_id for alias in result.aliases} == {"40001", "40002"}
    assert all(alias.alias_type == "SLUG" and alias.source == "gamma" for alias in result.aliases)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT count(*) FROM alpha_market_alias").fetchone()[0] == 2
    finally:
        conn.close()


def test_alias_values_replay_identically_and_dedup_in_batch(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    payload = market_payload()  # slug will-x-happen
    result = _ingestor(repository, db).ingest_pages(
        [CapturedPage.of([payload, dict(payload)], OBSERVED)],
        run_id="run-alias",
        ingested_at=INGESTED,
    )

    assert result.counts()["snapshots"] == 1
    assert result.counts()["raw_market_artifacts"] == 1  # identical payload dedups
    assert len(result.aliases) == 1
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT count(*) FROM alpha_market_alias").fetchone()[0] == 1
    finally:
        conn.close()


def test_slug_change_closes_previous_alias_interval(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    ingestor = _ingestor(repository, db)
    first = ingestor.ingest_pages(
        [CapturedPage.of([market_payload(slug="old-slug")], OBSERVED)],
        run_id="run-alias-old",
        ingested_at=INGESTED,
    )
    second = ingestor.ingest_pages(
        [CapturedPage.of([market_payload(slug="new-slug")], OBSERVED_LATER)],
        run_id="run-alias-new",
        ingested_at=INGESTED_LATER,
    )

    assert first.counts()["error_receipts"] == 0
    assert second.counts()["error_receipts"] == 0
    replay_old = ingestor.ingest_pages(
        [CapturedPage.of([market_payload(slug="old-slug")], OBSERVED)],
        run_id="run-alias-old",
        ingested_at=INGESTED,
    )
    assert replay_old.counts()["error_receipts"] == 0
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert conn.execute(
            "SELECT alias_value, effective_to_utc IS NULL "
            "FROM alpha_market_alias ORDER BY effective_from_utc"
        ).fetchall() == [("old-slug", 0), ("new-slug", 1)]
    finally:
        conn.close()


# 5. raw artifact hashes + immutable revisions + idempotent replay
def test_replay_same_batch_is_idempotent_with_stable_hashes(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    ingestor = _ingestor(repository, db)
    pages = [CapturedPage.of(multi_market_event_payloads(), OBSERVED)]

    first = ingestor.ingest_pages(pages, run_id="run-replay", ingested_at=INGESTED)
    counts_after_first = _table_counts(db)
    second = ingestor.ingest_pages(pages, run_id="run-replay", ingested_at=INGESTED)

    assert second.snapshot_ids == first.snapshot_ids
    assert second.raw_artifact_ids == first.raw_artifact_ids
    assert _table_counts(db) == counts_after_first  # zero new logical rows

    assert first.snapshots[0].canonical_sha256 == second.snapshots[0].canonical_sha256
    for snapshot in first.snapshots:
        stored = repository.get_contract_json(snapshot.record_id)
        assert stored is not None
        assert snapshot.record_id.startswith("gamma_market_snapshot:")


# BF-P003-04: retry under a new run_id must not conflict with artifact ids
def test_reingest_under_new_run_never_conflicts(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    ingestor = _ingestor(repository, db)
    page = CapturedPage.of([market_payload()], OBSERVED)

    first = ingestor.ingest_pages([page], run_id="run-original", ingested_at=INGESTED)
    second = ingestor.ingest_pages([page], run_id="run-retry", ingested_at=INGESTED_LATER)

    assert first.counts()["error_receipts"] == 0
    assert second.counts()["error_receipts"] == 0  # no ContractConflictError escape
    assert second.page_artifact_ids != first.page_artifact_ids  # run-scoped ids
    assert second.raw_artifact_ids != first.raw_artifact_ids
    # same captured bytes keep one explicit logical deduplication key
    first_raw = repository.get_contract(first.raw_artifact_ids[0])
    second_raw = repository.get_contract(second.raw_artifact_ids[0])
    assert first_raw["payload_sha256"] == second_raw["payload_sha256"]
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT count(*) FROM alpha_raw_artifact").fetchone()[0] == 4
        assert conn.execute("SELECT count(*) FROM alpha_run_artifact_link").fetchone()[0] == 4
    finally:
        conn.close()


def test_golden_record_ids_stable_across_fresh_databases(tmp_path: Path) -> None:
    results = []
    for name in ("golden-a.db", "golden-b.db"):
        repository, db = _repository(tmp_path / name)
        result = _ingestor(repository, db).ingest_pages(
            [CapturedPage.of([market_payload()], OBSERVED)],
            run_id="run-golden",
            ingested_at=INGESTED,
        )
        results.append(result)
    first, second = results
    assert first.snapshot_ids == second.snapshot_ids
    assert first.raw_artifact_ids == second.raw_artifact_ids
    assert first.page_artifact_ids == second.page_artifact_ids
    assert [s.canonical_sha256 for s in first.snapshots] == [
        s.canonical_sha256 for s in second.snapshots
    ]


# 6. one-character rule change keeps both revisions
def test_one_character_rule_change_appends_second_revision(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    ingestor = _ingestor(repository, db)
    changed_rule = BASE_RULES.replace("YES", "NES", 1)  # one-character edit
    assert changed_rule != BASE_RULES and len(changed_rule) == len(BASE_RULES)

    first = ingestor.ingest_pages(
        [CapturedPage.of([market_payload()], OBSERVED)],
        run_id="run-rule-1",
        ingested_at=INGESTED,
    )
    second = ingestor.ingest_pages(
        [CapturedPage.of([market_payload(rules=changed_rule)], OBSERVED_LATER)],
        run_id="run-rule-2",
        ingested_at=INGESTED_LATER,
    )

    assert first.snapshots[0].rule_hash != second.snapshots[0].rule_hash
    assert first.snapshot_ids != second.snapshot_ids
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        revisions = conn.execute(
            "SELECT snapshot_id FROM alpha_market_snapshot_revision WHERE market_id = '12345'"
        ).fetchall()
    finally:
        conn.close()
    assert {row[0] for row in revisions} == set(first.snapshot_ids) | set(
        second.snapshot_ids
    )
    # the old revision is still fully retrievable and unchanged
    stored_old = repository.get_contract(first.snapshot_ids[0])
    assert stored_old is not None
    assert stored_old["identity"]["market_id"] == "12345"


# 7. whitespace-only rule change: new revision, identical rule_hash
def test_whitespace_only_rule_change_keeps_rule_hash(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    ingestor = _ingestor(repository, db)
    padded_rule = BASE_RULES + "\n\n   \t"

    first = ingestor.ingest_pages(
        [CapturedPage.of([market_payload()], OBSERVED)],
        run_id="run-ws-1",
        ingested_at=INGESTED,
    )
    second = ingestor.ingest_pages(
        [CapturedPage.of([market_payload(rules=padded_rule)], OBSERVED_LATER)],
        run_id="run-ws-2",
        ingested_at=INGESTED_LATER,
    )

    assert first.snapshot_ids != second.snapshot_ids  # raw content is preserved
    assert first.snapshots[0].rule_hash == second.snapshots[0].rule_hash
    assert first.snapshots[0].rules_normalized == second.snapshots[0].rules_normalized


# 8. lifecycle mapping and missing lifecycle fields fail closed
@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"active": True, "closed": False, "resolved": False}, MarketStatus.ACTIVE),
        ({"active": False, "closed": True, "resolved": False}, MarketStatus.CLOSED),
        ({"active": False, "closed": True, "resolved": True}, MarketStatus.RESOLVED),
        ({"active": 1, "closed": 0, "resolved": 0}, MarketStatus.ACTIVE),
    ],
)
def test_lifecycle_status_mapping(overrides: dict, expected: MarketStatus) -> None:
    assert lifecycle_status(market_payload(**overrides)) == expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"active": True, "closed": True, "resolved": False},
        {"active": True, "closed": False, "resolved": True},
    ],
)
def test_contradictory_lifecycle_flags_fail_closed(overrides: dict) -> None:
    from src.polymarket_alpha.adapters.gamma_normalize import GammaNormalizationError

    with pytest.raises(GammaNormalizationError) as excinfo:
        lifecycle_status(market_payload(**overrides))
    assert excinfo.value.reason_code == "LIFECYCLE_CONTRADICTORY"


def test_missing_lifecycle_field_fails_closed_with_receipt(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    payload = market_payload(id="50001")
    payload.pop("closed")

    result = _ingestor(repository, db).ingest_pages(
        [CapturedPage.of([payload], OBSERVED)], run_id="run-lc", ingested_at=INGESTED
    )

    assert result.counts()["snapshots"] == 0
    assert result.error_receipts[0].error_code == "LIFECYCLE_FIELD_MISSING"
    # raw payload is retained even though normalization failed
    assert repository.get_contract(result.raw_artifact_ids[0]) is not None


def test_superseded_is_never_derived(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    payload = market_payload(
        id="50002", umaResolutionStatus="superseded", extraFutureFlag="maybe"
    )
    result = _ingestor(repository, db).ingest_pages(
        [CapturedPage.of([payload], OBSERVED)], run_id="run-sup", ingested_at=INGESTED
    )

    assert result.snapshots[0].status is MarketStatus.ACTIVE  # coordinator: DEFERRED_UNREACHABLE
    assert [r.unknown_fields for r in result.drift_receipts] == [("extraFutureFlag",)]


# 9. schema drift receipt and missing critical fields
def test_unknown_fields_produce_drift_receipt_and_stay_in_raw(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    payload = market_payload(id="60001", brandNewField="v2", anotherNewField=7)

    result = _ingestor(repository, db).ingest_pages(
        [CapturedPage.of([payload], OBSERVED)], run_id="run-drift", ingested_at=INGESTED
    )

    assert result.counts()["snapshots"] == 1  # drift is a receipt, not a failure
    assert result.drift_receipts[0].unknown_fields == (
        "anotherNewField",
        "brandNewField",
    )
    raw = repository.get_contract(result.raw_artifact_ids[0])
    assert raw is not None
    assert raw["payload"]["brandNewField"] == "v2"


@pytest.mark.parametrize(
    "drop_field,error_code",
    [
        ("id", "MARKET_ID_MISSING"),
        ("events", "EVENT_ID_MISSING"),
        ("question", "QUESTION_MISSING"),
        ("rules", "RULES_MISSING"),
        ("clobTokenIds", "TOKEN_MAPPING_INVALID"),
    ],
)
def test_missing_critical_fields_fail_closed(
    tmp_path: Path, drop_field: str, error_code: str
) -> None:
    repository, db = _repository(tmp_path)
    payload = market_payload(id="61001")
    payload.pop(drop_field)

    result = _ingestor(repository, db).ingest_pages(
        [CapturedPage.of([payload], OBSERVED)], run_id="run-crit", ingested_at=INGESTED
    )

    assert result.counts()["snapshots"] == 0
    assert result.error_receipts[0].error_code == error_code
    assert result.error_receipts[0].raw_artifact_id is not None


def test_invalid_numeric_and_end_date_fail_closed(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    bad_volume = market_payload(id="62001", volume="-5")
    bad_date = market_payload(id="62002", endDate="not-a-date")
    naive_date = market_payload(id="62003", endDate="2026-12-31T00:00:00")

    result = _ingestor(repository, db).ingest_pages(
        [CapturedPage.of([bad_volume, bad_date, naive_date], OBSERVED)],
        run_id="run-bad",
        ingested_at=INGESTED,
    )

    assert sorted(r.error_code for r in result.error_receipts) == [
        "END_DATE_INVALID",
        "END_DATE_INVALID",
        "NUMERIC_FIELD_INVALID",
    ]


def test_non_mapping_payload_item_gets_receipt_and_batch_survives(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    result = _ingestor(repository, db).ingest_pages(
        [CapturedPage.of(["not-a-json-object", market_payload(id="63001")], OBSERVED)],
        run_id="run-nonmap",
        ingested_at=INGESTED,
    )

    assert [r.error_code for r in result.error_receipts] == ["PAYLOAD_NOT_MAPPING"]
    assert result.counts()["snapshots"] == 1  # the valid market still ingests


# 10. source observed clock vs ingest clock
def test_dual_clocks_are_separate_and_required(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    result = _ingestor(repository, db).ingest_pages(
        [CapturedPage.of([market_payload()], OBSERVED)],
        run_id="run-clock",
        ingested_at=INGESTED,
    )

    snapshot = result.snapshots[0]
    assert snapshot.source_observed_at == OBSERVED
    assert snapshot.ingested_at == INGESTED
    assert snapshot.ingested_at > snapshot.source_observed_at
    assert snapshot.created_at == INGESTED  # pinned for byte determinism


def test_observed_clock_after_ingest_clock_fails_closed(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    with pytest.raises(ValueError):
        _ingestor(repository, db).ingest_pages(
            [CapturedPage.of([market_payload()], INGESTED_LATER)],
            run_id="run-clock-bad",
            ingested_at=INGESTED,
        )


# BF-P003-06: captured pages carry the authoritative pagination window
def test_captured_page_rejects_malformed_window() -> None:
    with pytest.raises(ValueError):
        CapturedPage.of([market_payload()], OBSERVED, requested_offset=-1)
    with pytest.raises(ValueError):
        CapturedPage.of([market_payload()], OBSERVED, requested_limit=0)


# 11. F-02 / BF-P003-06: duplicate full page must terminate, collection bounded
def test_duplicate_full_page_terminates_without_infinite_loop() -> None:
    page = [market_payload(id=f"7000{i}") for i in range(2)]
    calls = {"count": 0}

    def fetch_page(offset: int):
        calls["count"] += 1
        return page  # server keeps answering with the same full page

    collection = collect_gamma_pages(fetch_page, limit=2)

    assert collection.receipt.termination == PaginationTermination.DUPLICATE_PAGE
    assert collection.receipt.pages_fetched == 2
    assert collection.receipt.duplicate_page_offsets == (2,)
    assert collection.receipt.truncated is False
    assert len(collection.items) == 2  # duplicate items are not re-emitted
    assert calls["count"] == 2


def test_short_empty_and_max_pages_termination() -> None:
    short = collect_gamma_pages(
        lambda offset: [market_payload()] if offset == 0 else [], limit=2
    )
    assert short.receipt.termination == PaginationTermination.SHORT_PAGE

    empty = collect_gamma_pages(lambda offset: [], limit=2)
    assert empty.receipt.termination == PaginationTermination.EMPTY_PAGE
    assert empty.items == ()

    def full_pages(offset: int):
        return [market_payload(id=f"p{offset}-{i}") for i in range(2)]

    bounded = collect_gamma_pages(full_pages, limit=2, max_pages=3)
    assert bounded.receipt.termination == PaginationTermination.MAX_PAGES_BOUND
    assert bounded.receipt.truncated is True
    assert len(bounded.items) == 6


def test_endless_unique_pages_terminate_at_finite_default_budget() -> None:
    def endless_unique(offset: int):
        return [market_payload(id=f"endless-{offset}-{i}") for i in range(2)]

    collection = collect_gamma_pages(endless_unique, limit=2)  # no max_pages given

    assert DEFAULT_MAX_PAGES > 0
    assert collection.receipt.effective_max_pages == DEFAULT_MAX_PAGES
    assert collection.receipt.termination == PaginationTermination.MAX_PAGES_BOUND
    assert collection.receipt.truncated is True
    assert collection.receipt.pages_fetched == DEFAULT_MAX_PAGES


def test_negative_start_offset_rejected() -> None:
    with pytest.raises(ValueError):
        collect_gamma_pages(lambda offset: [], limit=2, start_offset=-1)


# 12. F-05 / BF-P003-03: query response identity verification
def test_verify_market_identity_rejects_wrong_market() -> None:
    wrong = market_payload(id="99999")
    with pytest.raises(GammaIdentityMismatchError):
        verify_market_response_identity(wrong, expected_market_id="12345")
    with pytest.raises(GammaIdentityMismatchError):
        verify_market_response_identity(wrong, expected_condition_id="0xother")
    with pytest.raises(GammaIdentityMismatchError):
        verify_market_response_identity(wrong, expected_slug="will-y-happen")
    assert verify_market_response_identity(None, expected_market_id="12345") is None
    assert verify_market_response_identity(wrong, expected_market_id="99999") is wrong


def test_verify_requires_at_least_one_stated_identity() -> None:
    payload = market_payload()
    with pytest.raises(GammaIdentityMismatchError):
        verify_market_response_identity(payload)  # anonymous verified path
    with pytest.raises(GammaIdentityMismatchError):
        verify_market_response_identity(payload, expected_market_id="")
    with pytest.raises(GammaIdentityMismatchError):
        verify_market_response_identity(payload, expected_slug="   ")


def test_ingest_verified_market_fails_closed_on_every_bad_path(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    ingestor = _ingestor(repository, db)

    mismatch = ingestor.ingest_verified_market(
        market_payload(id="99999"),
        expected_market_id="12345",
        run_id="run-f05",
        source_observed_at=OBSERVED,
        ingested_at=INGESTED,
    )
    assert [r.error_code for r in mismatch.error_receipts] == ["IDENTITY_MISMATCH"]
    assert mismatch.counts()["snapshots"] == 0

    anonymous = ingestor.ingest_verified_market(
        market_payload(),
        run_id="run-f05",
        source_observed_at=OBSERVED,
        ingested_at=INGESTED,
    )
    assert [r.error_code for r in anonymous.error_receipts] == ["IDENTITY_MISMATCH"]

    nothing = ingestor.ingest_verified_market(
        None,
        expected_market_id="12345",
        run_id="run-f05",
        source_observed_at=OBSERVED,
        ingested_at=INGESTED,
    )
    assert nothing.error_receipts[0].error_code == "QUERY_RETURNED_NOTHING"

    assert _table_counts(db)["alpha_market_snapshot_revision"] == 0

    accepted = ingestor.ingest_verified_market(
        market_payload(),
        expected_market_id="12345",
        expected_condition_id="0xcondition12345",
        run_id="run-f05",
        source_observed_at=OBSERVED,
        ingested_at=INGESTED,
    )
    assert accepted.counts()["snapshots"] == 1


# identity mutation against stored catalog fails closed, old revision intact
def test_identity_mutation_fails_closed_and_preserves_old_revision(tmp_path: Path) -> None:
    repository, db = _repository(tmp_path)
    ingestor = _ingestor(repository, db)
    first = ingestor.ingest_pages(
        [CapturedPage.of([market_payload()], OBSERVED)],
        run_id="run-mut-1",
        ingested_at=INGESTED,
    )
    mutated = market_payload(clobTokenIds='["tok-yes-swap", "tok-no-12345"]')
    second = ingestor.ingest_pages(
        [CapturedPage.of([mutated], OBSERVED_LATER)],
        run_id="run-mut-2",
        ingested_at=INGESTED_LATER,
    )

    assert [r.error_code for r in second.error_receipts] == ["IDENTITY_CONFLICT"]
    revisions = _table_counts(db)["alpha_market_snapshot_revision"]
    assert revisions == 1
    assert repository.get_contract(first.snapshot_ids[0]) is not None


# hardening: artifact hashes are validated at model level, not only in builders
def test_artifact_models_reject_mismatched_content_hash() -> None:
    with pytest.raises(ValidationError):
        GammaMarketPayloadArtifact(
            record_id="gamma_market_payload:" + "0" * 64,
            run_id="run",
            source="gamma_catalog_adapter",
            source_version="v1",
            payload={"id": "1"},
            payload_sha256="f" * 64,
            source_observed_at=OBSERVED,
        )


# P0-11 subset: offline capability audit of the owned source tree
def test_p0_03_owned_source_tree_passes_capability_audit() -> None:
    for owned in ("src/polymarket_alpha/adapters", "src/polymarket_alpha/census"):
        result = audit_source_tree(ROOT / owned)
        assert result.passed, result.violations
