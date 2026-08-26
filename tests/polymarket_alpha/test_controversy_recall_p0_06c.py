from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.polymarket_alpha.contracts import RecallHit, RecallerType, stable_record_id
from src.polymarket_alpha.recall import (
    ProviderBatch,
    ProviderDescriptor,
    ProviderRegistry,
    RecallAggregationRequest,
    RecallAggregator,
)
from src.polymarket_alpha.recall.controversy import (
    CONTROVERSY_PROVIDER_ID,
    CONTROVERSY_RAW_SCORE,
    CONTROVERSY_RECALLER_VERSION,
    ControversyFinding,
    ControversyMarketMapping,
    ControversyRejectionReason,
    ControversyRecallOutcome,
    ControversyRecallRequest,
    ControversyRecaller,
    ControversySkipReason,
    ControversySourceExpectation,
    ControversySourceIdentity,
    ControversySourcePayload,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "controversy"
CORPUS_REVISION_SHA256 = "3a21a4a0e7538fd0b34ea04b1d87d8815af57bffe108742d19e146c6964112ee"
AS_OF = datetime(2026, 8, 24, tzinfo=timezone.utc)
CASE_001_EFFECTIVE_AT = datetime(2026, 8, 20, 14, 2, tzinfo=timezone.utc)

# Golden identities for the frozen corpus_v1 fixture under AS_OF (case-004 is
# deliberately unmapped).  Any drift in hit identity construction must fail here.
GOLDEN_HIT_RECORD_IDS = {
    "case-001": "recall_hit:594bf38de90d1f61996fb80856bf23430834677e6fc6e9f47af4d250b3232410",
    "case-002": "recall_hit:7f5bdfa7638f0b441e0382d4c2cf84f72591ee4fd2d4ad4dada2157ab0c0150c",
    "case-003": "recall_hit:6589da262f731035eaa39f7e493ae37e458870a619fc693e8d4d186af77337c8",
}


def _payload(name: str) -> ControversySourcePayload:
    return ControversySourcePayload.model_validate(
        json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    )


def _expectation(payload: ControversySourcePayload) -> ControversySourceExpectation:
    return ControversySourceExpectation(
        identity=payload.identity,
        source_artifact_id=payload.source_artifact_id,
        artifact_sha256=payload.artifact_sha256,
    )


def _mapping() -> tuple[ControversyMarketMapping, ...]:
    return (
        ControversyMarketMapping(market_ref="fed-rate-cut", market_id="market-fed-1"),
        ControversyMarketMapping(market_ref="nyc-mayor-special", market_id="market-nyc-2"),
    )


def _request(
    *sources: ControversySourcePayload,
    expectations: tuple[ControversySourceExpectation, ...] | None = None,
    as_of: datetime = AS_OF,
    run_id: str = "controversy-run",
    mapping: tuple[ControversyMarketMapping, ...] | None = None,
    provider_id: str = CONTROVERSY_PROVIDER_ID,
) -> ControversyRecallRequest:
    return ControversyRecallRequest(
        run_id=run_id,
        provider_id=provider_id,
        as_of=as_of,
        corpus_revision_sha256=CORPUS_REVISION_SHA256,
        expected_sources=expectations if expectations is not None else tuple(
            _expectation(item) for item in sources
        ),
        sources=sources,
        market_mapping=_mapping() if mapping is None else mapping,
    )


def _recall(request: ControversyRecallRequest) -> ControversyRecallOutcome:
    return ControversyRecaller().recall(request)


def _reasons(outcome: ControversyRecallOutcome) -> list[str]:
    return [item.reason.value for item in outcome.rejections]


def test_frozen_corpus_emits_fully_bound_controversy_hits() -> None:
    payload = _payload("corpus_v1.json")
    outcome = _recall(_request(payload))

    assert [hit.record_id for hit in outcome.hits] == sorted(GOLDEN_HIT_RECORD_IDS.values())
    assert outcome.skips == ()
    assert _reasons(outcome) == [ControversyRejectionReason.UNMAPPED_MARKET.value]
    assert outcome.rejections[0].case_id == "case-004"
    assert outcome.provider_id == CONTROVERSY_PROVIDER_ID
    assert outcome.recaller_version == CONTROVERSY_RECALLER_VERSION
    assert outcome.as_of == AS_OF

    hits_by_case = {
        hit.features["case_id"]: hit for hit in outcome.hits
    }
    assert set(hits_by_case) == {"case-001", "case-002", "case-003"}
    for case_id, hit in hits_by_case.items():
        assert hit.record_id == GOLDEN_HIT_RECORD_IDS[case_id]
        assert hit.recaller is RecallerType.CONTROVERSY
        assert hit.source == CONTROVERSY_PROVIDER_ID
        assert hit.source_version == CONTROVERSY_RECALLER_VERSION
        assert hit.recaller_version == CONTROVERSY_RECALLER_VERSION
        assert all(code.startswith("CONTROVERSY_") for code in hit.reason_codes)
        assert hit.raw_score == CONTROVERSY_RAW_SCORE == Decimal("1")
        assert hit.created_at == AS_OF
        assert hit.observed_at == hit.features["effective_at"]
        assert hit.valid_until is None and not hit.historical_only
        assert hit.features["source_path"] == payload.identity.source_path
        assert hit.features["fixture_id"] == payload.identity.fixture_id
        assert hit.features["source_device"] is None and hit.features["source_inode"] is None
        assert hit.features["schema_sha256"] == payload.identity.schema_sha256
        assert hit.features["corpus_revision_sha256"] == CORPUS_REVISION_SHA256
        assert hit.features["source_artifact_id"] == payload.source_artifact_id
        start, end = hit.features["quote_start"], hit.features["quote_end"]
        excerpt = payload.artifact_text[start:end]
        assert excerpt.strip()
        assert hit.features["finding"] in {
            "RULE_CONTESTED", "RESOLUTION_DISPUTED", "CLARIFICATION_PENDING", "AMBIGUITY_RAISED"
        }
        assert hit.features["quote_sha256"] == hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        assert hit.provenance[0].source_artifact_id == payload.source_artifact_id
        assert hit.provenance[0].content_sha256 == payload.artifact_sha256

    assert hits_by_case["case-001"].market_id == "market-fed-1"
    assert hits_by_case["case-002"].market_id == "market-fed-1"
    assert hits_by_case["case-003"].market_id == "market-nyc-2"
    assert {hit.reason_codes[0] for hit in outcome.hits} == {
        "CONTROVERSY_RULE_CONTESTED",
        "CONTROVERSY_CLARIFICATION_PENDING",
        "CONTROVERSY_RESOLUTION_DISPUTED",
    }


@pytest.mark.parametrize(
    ("as_of", "expected_hits", "expected_post_cutoff"),
    [
        (CASE_001_EFFECTIVE_AT, 1, 3),
        (CASE_001_EFFECTIVE_AT - timedelta(microseconds=1), 0, 4),
        (AS_OF, 3, 0),
    ],
)
def test_exact_pit_boundary_is_inclusive_and_post_cutoff_rejected(
    as_of: datetime, expected_hits: int, expected_post_cutoff: int
) -> None:
    payload = _payload("corpus_v1.json")
    outcome = _recall(_request(payload, as_of=as_of))

    assert len(outcome.hits) == expected_hits
    assert _reasons(outcome).count(ControversyRejectionReason.POST_CUTOFF_EVIDENCE.value) == (
        expected_post_cutoff
    )
    if expected_hits == 1:
        assert outcome.hits[0].features["case_id"] == "case-001"
        assert outcome.hits[0].observed_at == CASE_001_EFFECTIVE_AT
    for rejection in outcome.rejections:
        if rejection.reason is ControversyRejectionReason.POST_CUTOFF_EVIDENCE:
            assert "2026-08-2" in rejection.detail and "as_of" in rejection.detail


def test_source_identity_mismatch_rejects_every_case_of_the_source() -> None:
    payload = _payload("corpus_v1.json")
    other_hash = "0" * 64
    variants = {
        "schema_hash": _expectation(payload).model_copy(
            update={
                "identity": payload.identity.model_copy(update={"schema_sha256": other_hash})
            }
        ),
        "fixture_id": _expectation(payload).model_copy(
            update={
                "identity": payload.identity.model_copy(
                    update={"fixture_id": "controversy-fixture:corpus-v2"}
                )
            }
        ),
        "artifact_id": _expectation(payload).model_copy(
            update={"source_artifact_id": payload.source_artifact_id + "-revised"}
        ),
        "artifact_hash": _expectation(payload).model_copy(
            update={"artifact_sha256": other_hash}
        ),
    }
    for name, expectation in variants.items():
        outcome = _recall(_request(payload, expectations=(expectation,)))
        assert outcome.hits == (), name
        assert outcome.skips == (), name
        assert _reasons(outcome) == [
            ControversyRejectionReason.SOURCE_IDENTITY_MISMATCH.value
        ] * len(payload.cases), name
        assert {item.case_id for item in outcome.rejections} == {
            "case-001", "case-002", "case-003", "case-004"
        }, name


def test_real_file_identity_binds_device_and_inode(tmp_path: Path) -> None:
    payload = _payload("corpus_v1.json")
    path = tmp_path / "dispute_corpus.txt"
    path.write_text(payload.artifact_text, encoding="utf-8")
    status = path.stat()
    identity = ControversySourceIdentity(
        source_path=str(path),
        device=status.st_dev,
        inode=status.st_ino,
        fixture_id=None,
        schema_sha256=payload.identity.schema_sha256,
    )
    file_payload = payload.model_copy(update={"identity": identity})
    outcome = _recall(_request(file_payload))

    assert len(outcome.hits) == 3
    for hit in outcome.hits:
        assert hit.features["source_path"] == str(path)
        assert hit.features["source_device"] == status.st_dev
        assert hit.features["source_inode"] == status.st_ino
        assert hit.features["fixture_id"] is None

    drifted_expectation = _expectation(file_payload).model_copy(
        update={"identity": identity.model_copy(update={"inode": status.st_ino + 1})}
    )
    drifted = _recall(_request(file_payload, expectations=(drifted_expectation,)))
    assert drifted.hits == ()
    assert _reasons(drifted) == [
        ControversyRejectionReason.SOURCE_IDENTITY_MISMATCH.value
    ] * len(file_payload.cases)

    tampered = file_payload.model_copy(
        update={"artifact_text": payload.artifact_text[:100]}
    )
    hashed_out = _recall(_request(tampered))
    assert hashed_out.hits == ()
    assert _reasons(hashed_out) == [
        ControversyRejectionReason.SOURCE_HASH_MISMATCH.value
    ] * len(tampered.cases)


def test_duplicate_logical_case_is_adjudicated_deterministically() -> None:
    corpus = _payload("corpus_v1.json")
    mirror_identity = corpus.identity.model_copy(
        update={
            "source_path": "/frozen/polymarket_alpha/controversy/corpus_v1_mirror",
            "fixture_id": "controversy-fixture:corpus-v1-mirror",
        }
    )
    mirror = corpus.model_copy(update={"identity": mirror_identity})

    redundant = _recall(_request(corpus, mirror))
    assert len(redundant.hits) == 3
    assert {hit.features["case_id"] for hit in redundant.hits} == {
        "case-001", "case-002", "case-003"
    }
    assert sorted(
        item.case_id for item in redundant.rejections
        if item.reason is ControversyRejectionReason.DUPLICATE_CASE
    ) == ["case-001", "case-002", "case-003"]
    assert sorted(
        item.source_path for item in redundant.rejections
        if item.reason is ControversyRejectionReason.DUPLICATE_CASE
    ) == [mirror_identity.source_path] * 3
    assert _reasons(redundant).count(ControversyRejectionReason.UNMAPPED_MARKET.value) == 2

    conflicting_case = mirror.cases[1].model_copy(
        update={"finding": ControversyFinding.RESOLUTION_DISPUTED}
    )
    conflicting_mirror = mirror.model_copy(
        update={
            "cases": tuple(
                conflicting_case if case.case_id == "case-002" else case
                for case in mirror.cases
            )
        }
    )
    conflict = _recall(_request(corpus, conflicting_mirror))
    assert {hit.features["case_id"] for hit in conflict.hits} == {"case-001", "case-003"}
    conflicting = [
        item for item in conflict.rejections
        if item.reason is ControversyRejectionReason.DUPLICATE_CASE_CONFLICT
    ]
    assert {item.case_id for item in conflicting} == {"case-002"}
    assert {item.source_path for item in conflicting} == {
        corpus.identity.source_path,
        mirror_identity.source_path,
    }
    assert "conflicting entries" in conflicting[0].detail


def test_missing_source_is_a_typed_skip_not_a_global_failure() -> None:
    corpus = _payload("corpus_v1.json")
    absent_identity = ControversySourceIdentity(
        source_path="/frozen/polymarket_alpha/controversy/corpus_v2",
        fixture_id="controversy-fixture:corpus-v2",
        schema_sha256=corpus.identity.schema_sha256,
    )
    absent_expectation = ControversySourceExpectation(
        identity=absent_identity,
        source_artifact_id="controversy-artifact:corpus-v2",
        artifact_sha256="1" * 64,
    )

    outcome = _recall(
        _request(corpus, expectations=(_expectation(corpus), absent_expectation))
    )
    assert len(outcome.hits) == 3
    assert len(outcome.skips) == 1
    skip = outcome.skips[0]
    assert skip.reason is ControversySkipReason.SOURCE_MISSING
    assert skip.source_path == absent_identity.source_path
    assert skip.provider_id == CONTROVERSY_PROVIDER_ID
    assert "controversy-artifact:corpus-v2" in skip.detail

    unexpected = corpus.model_copy(
        update={
            "identity": corpus.identity.model_copy(
                update={
                    "source_path": "/frozen/polymarket_alpha/controversy/corpus_rogue",
                    "fixture_id": "controversy-fixture:corpus-v1-rogue",
                }
            )
        }
    )
    isolated = _recall(
        _request(unexpected, expectations=(_expectation(corpus),))
    )
    assert isolated.hits == ()
    # The expected corpus stays missing while the rogue payload is unexpected:
    # both are typed per-source skips and neither blocks the other.
    assert [
        (item.source_path, item.reason) for item in isolated.skips
    ] == [
        (unexpected.identity.source_path, ControversySkipReason.SOURCE_UNEXPECTED),
        (corpus.identity.source_path, ControversySkipReason.SOURCE_MISSING),
    ]


def test_incomplete_or_truncated_sources_are_rejected_typed() -> None:
    truncated = _payload("corpus_v1_truncated.json")
    truncated_outcome = _recall(_request(truncated))
    assert truncated_outcome.hits == ()
    assert truncated_outcome.skips == ()
    assert _reasons(truncated_outcome) == [
        ControversyRejectionReason.SOURCE_HASH_MISMATCH.value
    ] * len(truncated.cases)

    overflow = _payload("corpus_offset_overflow.json")
    overflow_outcome = _recall(_request(overflow))
    assert {hit.features["case_id"] for hit in overflow_outcome.hits} == {
        "case-001", "case-002", "case-003"
    }
    incomplete = [
        item for item in overflow_outcome.rejections
        if item.reason is ControversyRejectionReason.SOURCE_INCOMPLETE
    ]
    assert [item.case_id for item in incomplete] == ["case-005"]
    assert overflow_outcome.hits[0].features["source_artifact_id"] == (
        "controversy-artifact:corpus-offset-overflow"
    )

    separator = overflow.artifact_text.index("\n\n")
    corpus_only = _payload("corpus_v1.json")
    blank_span_case = corpus_only.cases[0].model_copy(
        update={"quote_start": separator + 1, "quote_end": separator + 2}
    )
    assert corpus_only.artifact_text[separator + 1 : separator + 2].strip() == ""
    blank_payload = corpus_only.model_copy(
        update={
            "cases": tuple(
                blank_span_case if case.case_id == "case-001" else case
                for case in corpus_only.cases
            )
        }
    )
    blank_outcome = _recall(_request(blank_payload))
    blank_reasons = [
        (item.case_id, item.reason)
        for item in blank_outcome.rejections
        if item.reason is ControversyRejectionReason.SOURCE_INCOMPLETE
    ]
    assert blank_reasons == [("case-001", ControversyRejectionReason.SOURCE_INCOMPLETE)]
    assert {hit.features["case_id"] for hit in blank_outcome.hits} == {"case-002", "case-003"}


def test_input_reordering_produces_identical_outcomes() -> None:
    corpus = _payload("corpus_v1.json")
    mirror_identity = corpus.identity.model_copy(
        update={
            "source_path": "/frozen/polymarket_alpha/controversy/corpus_v1_mirror",
            "fixture_id": "controversy-fixture:corpus-v1-mirror",
        }
    )
    mirror = corpus.model_copy(update={"identity": mirror_identity})

    forward = _recall(
        _request(
            corpus,
            mirror,
            expectations=(_expectation(corpus), _expectation(mirror)),
            mapping=_mapping(),
        )
    )
    reordered = _recall(
        ControversyRecallRequest(
            run_id="controversy-run",
            as_of=AS_OF,
            corpus_revision_sha256=CORPUS_REVISION_SHA256,
            expected_sources=( _expectation(mirror), _expectation(corpus)),
            sources=(
                mirror.model_copy(update={"cases": tuple(reversed(mirror.cases))}),
                corpus.model_copy(update={"cases": tuple(reversed(corpus.cases))}),
            ),
            market_mapping=tuple(reversed(_mapping())),
        )
    )

    assert reordered == forward
    assert reordered.request_sha256 == forward.request_sha256
    assert [hit.record_id for hit in reordered.hits] == [hit.record_id for hit in forward.hits]


def test_exact_retry_is_stable_and_cross_run_attempt_is_distinct() -> None:
    corpus = _payload("corpus_v1.json")
    first = _recall(_request(corpus, run_id="controversy-run"))
    exact_retry = _recall(_request(corpus, run_id="controversy-run"))
    later_run = _recall(_request(corpus, run_id="controversy-retry-run"))

    assert exact_retry == first
    assert [hit.record_id for hit in later_run.hits] != [
        hit.record_id for hit in first.hits
    ]
    later_by_case = {hit.features["case_id"]: hit for hit in later_run.hits}
    for original in first.hits:
        retried = later_by_case[original.features["case_id"]]
        assert retried.features == original.features
        assert retried.reason_codes == original.reason_codes
        assert retried.market_id == original.market_id
        assert retried.observed_at == original.observed_at
        assert retried.provenance == original.provenance
        assert retried.raw_score == original.raw_score
        assert retried.canonical_sha256 != original.canonical_sha256
    assert later_run.rejections == first.rejections
    assert later_run.skips == first.skips
    assert later_run.request_sha256 != first.request_sha256

    hit_by_case = {hit.features["case_id"]: hit for hit in first.hits}
    assert hit_by_case["case-001"].record_id == stable_record_id(
        "recall_hit",
        {
            "provider_id": CONTROVERSY_PROVIDER_ID,
            "recaller_version": CONTROVERSY_RECALLER_VERSION,
            "run_id": "controversy-run",
            "created_at": AS_OF,
            "market_id": "market-fed-1",
            "reason_code": "CONTROVERSY_RULE_CONTESTED",
            "case_id": "case-001",
            "effective_at": CASE_001_EFFECTIVE_AT,
            "quote_start": hit_by_case["case-001"].features["quote_start"],
            "quote_end": hit_by_case["case-001"].features["quote_end"],
            "quote_sha256": hit_by_case["case-001"].features["quote_sha256"],
            "source_path": corpus.identity.source_path,
            "source_device": None,
            "source_inode": None,
            "fixture_id": corpus.identity.fixture_id,
            "schema_sha256": corpus.identity.schema_sha256,
            "corpus_revision_sha256": CORPUS_REVISION_SHA256,
            "source_artifact_id": corpus.source_artifact_id,
        },
    )


def _external_hit(marker: str) -> RecallHit:
    return RecallHit(
        record_id=f"recall_hit:{marker * 64}",
        run_id="new-changed-run",
        created_at=AS_OF,
        source="new_changed",
        source_version="new-v1",
        provenance=(),
        extensions={},
        market_id="market-fed-1",
        recaller=RecallerType.NEW_CHANGED,
        recaller_version="new-v1",
        reason_codes=("NEW_MARKET",),
        features={"fixture": marker},
        raw_score=Decimal("0.6"),
        observed_at=AS_OF,
    )


def _aggregation(*batches: ProviderBatch) -> tuple:
    aggregator = RecallAggregator(
        ProviderRegistry(
            (
                ControversyRecaller().descriptor(),
                ProviderDescriptor(
                    provider_id="new_changed",
                    recaller=RecallerType.NEW_CHANGED,
                    recaller_version="new-v1",
                ),
            )
        )
    )
    outcome = aggregator.aggregate(
        RecallAggregationRequest(
            run_id="aggregation-run",
            created_at=AS_OF,
            as_of=AS_OF,
            batches=batches,
        )
    )
    return outcome, aggregator


def test_provider_isolation_in_registry_and_aggregation() -> None:
    corpus = _payload("corpus_v1.json")
    controversy = _recall(_request(corpus))
    registry = ProviderRegistry((ControversyRecaller().descriptor(),))
    for hit in controversy.hits:
        assert registry.validate_hit(CONTROVERSY_PROVIDER_ID, hit, include_book=False) is None

    outcome, _ = _aggregation(
        ProviderBatch(provider_id=CONTROVERSY_PROVIDER_ID, hits=controversy.hits),
        ProviderBatch(provider_id="new_changed", hits=(_external_hit("a"),)),
    )
    results_by_market = {result.candidate.market_id: result for result in outcome.results}
    assert set(results_by_market) == {"market-fed-1", "market-nyc-2"}
    assert set(results_by_market["market-fed-1"].candidate.recall_hit_ids) == {
        hit.record_id for hit in controversy.hits if hit.market_id == "market-fed-1"
    } | {_external_hit("a").record_id}
    assert set(results_by_market["market-nyc-2"].candidate.recall_hit_ids) == {
        hit.record_id for hit in controversy.hits if hit.market_id == "market-nyc-2"
    }
    assert outcome.rejected == ()

    absent_identity = ControversySourceIdentity(
        source_path="/frozen/polymarket_alpha/controversy/corpus_v2",
        fixture_id="controversy-fixture:corpus-v2",
        schema_sha256=corpus.identity.schema_sha256,
    )
    skipped = _recall(
        _request(
            expectations=(
                ControversySourceExpectation(
                    identity=absent_identity,
                    source_artifact_id="controversy-artifact:corpus-v2",
                    artifact_sha256="1" * 64,
                ),
            )
        )
    )
    assert skipped.hits == () and len(skipped.skips) == 1
    isolated_outcome, _ = _aggregation(
        ProviderBatch(provider_id=CONTROVERSY_PROVIDER_ID, hits=()),
        ProviderBatch(provider_id="new_changed", hits=(_external_hit("b"),)),
    )
    assert len(isolated_outcome.results) == 1
    assert isolated_outcome.results[0].candidate.recall_hit_ids == (
        _external_hit("b").record_id,
    )
    assert isolated_outcome.rejected == ()

    disabled_registry = ProviderRegistry(
        (ControversyRecaller().descriptor().model_copy(update={"enabled": False}),)
    )
    assert (
        disabled_registry.validate_hit(
            CONTROVERSY_PROVIDER_ID, controversy.hits[0], include_book=False
        )
        == "PROVIDER_DISABLED"
    )
    assert CONTROVERSY_PROVIDER_ID not in tuple(
        item.provider_id for item in disabled_registry.active(include_book=False)
    )


def test_blank_or_ambiguous_market_mapping_fails_closed() -> None:
    corpus = _payload("corpus_v1.json")
    with pytest.raises(ValueError, match="blank"):
        ControversyMarketMapping(market_ref="fed-rate-cut", market_id="  ")
    with pytest.raises(ValueError, match="blank"):
        ControversyMarketMapping(market_ref="", market_id="market-fed-1")
    with pytest.raises(ValueError, match="unique"):
        _request(
            corpus,
            mapping=_mapping() + (
                ControversyMarketMapping(market_ref="fed-rate-cut", market_id="market-fed-9"),
            ),
        )


def test_outcome_contract_forbids_estimation_semantics() -> None:
    corpus = _payload("corpus_v1.json")
    outcome = _recall(_request(corpus))

    def rebuild(hits: tuple[RecallHit, ...]) -> ControversyRecallOutcome:
        return ControversyRecallOutcome(
            provider_id=outcome.provider_id,
            recaller_version=outcome.recaller_version,
            as_of=outcome.as_of,
            request_sha256=outcome.request_sha256,
            hits=hits,
        )

    fair_value_hit = outcome.hits[0].model_copy(
        update={"features": {**outcome.hits[0].features, "fair_value": "0.7"}}
    )
    with pytest.raises(ValueError, match="restricted to source binding"):
        rebuild((fair_value_hit,))

    foreign_reason_hit = outcome.hits[0].model_copy(
        update={"reason_codes": ("NEW_MARKET",)}
    )
    with pytest.raises(ValueError, match="controversy vocabulary"):
        rebuild((foreign_reason_hit,))

    wrong_recaller_hit = outcome.hits[0].model_copy(
        update={"recaller": RecallerType.NEW_CHANGED}
    )
    with pytest.raises(ValueError, match="CONTROVERSY"):
        rebuild((wrong_recaller_hit,))


def test_descriptor_and_request_wiring() -> None:
    descriptor = ControversyRecaller().descriptor()
    assert descriptor.provider_id == CONTROVERSY_PROVIDER_ID
    assert descriptor.recaller is RecallerType.CONTROVERSY
    assert descriptor.recaller_version == CONTROVERSY_RECALLER_VERSION
    assert descriptor.enabled and not descriptor.requires_book

    with pytest.raises(ValueError, match="different provider"):
        _recall(_request(_payload("corpus_v1.json"), provider_id="other_provider"))

    with pytest.raises(ValueError, match="must be provided together"):
        ControversySourceIdentity(
            source_path="/frozen/x", device=1, fixture_id=None, schema_sha256="2" * 64
        )
    with pytest.raises(ValueError, match="cannot claim file and fixture grounding"):
        ControversySourceIdentity(
            source_path="/frozen/x", device=1, inode=2,
            fixture_id="controversy-fixture:x", schema_sha256="2" * 64,
        )
    with pytest.raises(ValueError, match="absolute"):
        ControversySourceIdentity(
            source_path="relative/x", fixture_id="controversy-fixture:x",
            schema_sha256="2" * 64,
        )
    with pytest.raises(ValueError, match="source payload paths must be unique"):
        corpus = _payload("corpus_v1.json")
        _request(corpus, corpus, expectations=(_expectation(corpus),))
