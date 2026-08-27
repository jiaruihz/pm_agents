from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pydantic import ValidationError
import pytest
from src.polymarket_alpha.contracts import (
    AlphaContract,
    CandidateState,
    RecallHit,
    RecallerType,
)
from src.polymarket_alpha.pipeline.recall import (
    MultiRecallProviderStatus,
    MultiRecallScanRequest,
    MultiRecallScanner,
    RecallProviderErrorCode,
)
from src.polymarket_alpha.recall import ProviderDescriptor, ProviderRegistry
from src.polymarket_alpha.recall.book_anomaly import BookAnomalyRecallRequest
from src.polymarket_alpha.recall.controversy import ControversyRecallRequest
from src.polymarket_alpha.recall.new_changed import PrebookRecallRequest
from src.polymarket_alpha.recall.wallet import WalletObservationClock, WalletRecallRequest


NOW = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)


class Outcome(AlphaContract):
    provider_id: str = "fixture"
    recaller_version: str = "fixture"
    as_of: datetime = NOW
    request_sha256: str | None = None
    hits: tuple[RecallHit, ...] = ()
    rejections: tuple[str, ...] = ()
    skips: tuple[str, ...] = ()
    suppressions: tuple[str, ...] = ()


class FakeProvider:
    def __init__(self, descriptor: ProviderDescriptor, outcome: Outcome | Exception) -> None:
        self.descriptor = descriptor
        self.outcome = outcome

    def recall(self, _request: object) -> Outcome:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _descriptor(provider_id: str, recaller: RecallerType, version: str, *, book: bool = False) -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id=provider_id,
        recaller=recaller,
        recaller_version=version,
        requires_book=book,
    )


DESCRIPTORS = (
    _descriptor("new_changed", RecallerType.NEW_CHANGED, "new-v1"),
    _descriptor("structural_metadata", RecallerType.STRUCTURAL_METADATA, "structural-v1"),
    _descriptor("controversy", RecallerType.CONTROVERSY, "controversy-v1"),
    _descriptor("specialist_wallet", RecallerType.SPECIALIST_WALLET, "wallet-v1"),
    _descriptor("book_anomaly", RecallerType.BOOK_ANOMALY, "book-v1", book=True),
)


def _hit(provider: ProviderDescriptor, marker: str, *, market_id: str = "market-1") -> RecallHit:
    reason = {
        RecallerType.NEW_CHANGED: "NEW_MARKET",
        RecallerType.STRUCTURAL_METADATA: "METADATA_REVISION",
        RecallerType.CONTROVERSY: "CONTROVERSY_RULE_CONTESTED",
        RecallerType.SPECIALIST_WALLET: "SPECIALIST_WALLET_ENTRY",
        RecallerType.BOOK_ANOMALY: "YES_SPREAD_WIDE",
    }[provider.recaller]
    return RecallHit(
        record_id=f"recall_hit:{marker * 64}",
        run_id="fixture-run",
        created_at=NOW,
        source=provider.provider_id,
        source_version=provider.recaller_version,
        provenance=(),
        extensions={},
        market_id=market_id,
        recaller=provider.recaller,
        recaller_version=provider.recaller_version,
        reason_codes=(reason,),
        features={"fixture": marker},
        raw_score=Decimal("0.5"),
        observed_at=NOW,
    )


def _request(**updates: object) -> MultiRecallScanRequest:
    values: dict[str, object] = {
        "run_id": "scan-1",
        "created_at": NOW,
        "as_of": NOW,
        "prebook_request": PrebookRecallRequest(run_id="scan-1", as_of=NOW),
    }
    values.update(updates)
    return MultiRecallScanRequest(**values)


def _optional_request(model: type[AlphaContract]) -> AlphaContract:
    """A typed opaque token is enough for fake-provider isolation tests."""
    if model is ControversyRecallRequest:
        return model.model_construct(run_id="scan-1", as_of=NOW)
    if model is WalletRecallRequest:
        return model.model_construct(
            run_id="scan-1", clock=WalletObservationClock(as_of=NOW)
        )
    if model is BookAnomalyRecallRequest:
        return model.model_construct(run_id="scan-1", as_of=NOW)
    raise AssertionError(f"unsupported fake request model: {model}")


def _scanner(*outcomes: Outcome | Exception, registry: ProviderRegistry | None = None) -> MultiRecallScanner:
    normalized = tuple(
        outcome.model_copy(
            update={
                "provider_id": descriptor.provider_id,
                "recaller_version": descriptor.recaller_version,
                "as_of": NOW,
            }
        )
        if type(outcome) is Outcome
        else outcome
        for descriptor, outcome in zip(DESCRIPTORS, outcomes, strict=True)
    )
    providers = tuple(FakeProvider(descriptor, outcome) for descriptor, outcome in zip(DESCRIPTORS, normalized, strict=True))
    return MultiRecallScanner(
        registry or ProviderRegistry(DESCRIPTORS),
        new_changed=providers[0], structural_metadata=providers[1], controversy=providers[2], wallet=providers[3], book_anomaly=providers[4],
    )


def test_four_prebook_routes_merge_multiple_markets_without_book() -> None:
    outcomes = (
        Outcome(hits=(_hit(DESCRIPTORS[0], "a"), _hit(DESCRIPTORS[0], "b", market_id="market-2"))),
        Outcome(hits=(_hit(DESCRIPTORS[1], "c"),)),
        Outcome(hits=(_hit(DESCRIPTORS[2], "d"),)),
        Outcome(hits=(_hit(DESCRIPTORS[3], "e"),)),
        Outcome(hits=(_hit(DESCRIPTORS[4], "f"),)),
    )
    result = _scanner(*outcomes).scan(
        _request(
            controversy_request=_optional_request(ControversyRecallRequest),
            wallet_request=_optional_request(WalletRecallRequest),
        )
    )
    assert [item.status for item in result.provider_receipts] == [
        MultiRecallProviderStatus.SUCCESS,
        MultiRecallProviderStatus.SUCCESS,
        MultiRecallProviderStatus.SUCCESS,
        MultiRecallProviderStatus.SUCCESS,
        MultiRecallProviderStatus.SKIPPED,
    ]
    merged = {item.candidate.market_id: item.candidate for item in result.aggregation.results}
    assert set(merged) == {"market-1", "market-2"}
    assert len(merged["market-1"].recall_hit_ids) == 4
    assert all("book_anomaly" not in item.candidate.selection_rationale for item in result.aggregation.results)


def test_optional_failures_are_isolated_and_book_is_never_a_precondition() -> None:
    outcomes = (
        Outcome(hits=(_hit(DESCRIPTORS[0], "a"),)),
        Outcome(),
        ValueError("bad dispute fixture"),
        ValueError("bad wallet fixture"),
        Outcome(hits=(_hit(DESCRIPTORS[4], "b"),)),
    )
    result = _scanner(*outcomes).scan(
        _request(
            controversy_request=_optional_request(ControversyRecallRequest),
            wallet_request=_optional_request(WalletRecallRequest),
        )
    )
    receipts = {item.provider_id: item for item in result.provider_receipts}
    assert receipts["controversy"].error_code == RecallProviderErrorCode.PROVIDER_VALUE_ERROR
    assert receipts["specialist_wallet"].error_code == RecallProviderErrorCode.PROVIDER_VALUE_ERROR
    assert receipts["book_anomaly"].error_code == RecallProviderErrorCode.BOOK_ROUTE_DISABLED
    assert len(result.aggregation.results) == 1


@pytest.mark.parametrize("malformed", [object(), Outcome.model_construct(hits=None)])
def test_malformed_provider_output_is_isolated(malformed: object) -> None:
    result = _scanner(
        Outcome(hits=(_hit(DESCRIPTORS[0], "a"),)),
        malformed,
        Outcome(),
        Outcome(),
        Outcome(),
    ).scan(_request())
    receipts = {item.provider_id: item for item in result.provider_receipts}
    assert receipts["structural_metadata"].status == MultiRecallProviderStatus.FAILED
    assert (
        receipts["structural_metadata"].error_code
        == RecallProviderErrorCode.PROVIDER_OUTPUT_INVALID
    )
    assert len(result.aggregation.results) == 1


def test_late_book_hit_requires_refresh_after_blind_is_frozen() -> None:
    first = _scanner(Outcome(hits=(_hit(DESCRIPTORS[0], "a"),)), Outcome(), Outcome(), Outcome(), Outcome()).scan(_request())
    prior = first.aggregation.results[0].candidate.model_copy(update={"state": CandidateState.BLIND_PACKET_FROZEN})
    second = _scanner(Outcome(), Outcome(), Outcome(), Outcome(), Outcome(hits=(_hit(DESCRIPTORS[4], "b"),))).scan(
        _request(
            enable_book_anomaly=True,
            book_anomaly_request=_optional_request(BookAnomalyRecallRequest),
            prior_candidates=(prior,),
            prior_hits=(_hit(DESCRIPTORS[0], "a"),),
            prior_projection_input_hashes={prior.candidate_id: "a" * 64},
            merged_projection_input_hashes={prior.candidate_id: "b" * 64},
        )
    )
    impact = second.aggregation.results[0].impact
    assert impact.impact.value == "RESEARCH_REFRESH_REQUIRED"
    assert not impact.safe_to_advance


def test_reorder_retry_is_stable_and_mismatched_output_fails_closed() -> None:
    canonical_hash = "c" * 64
    first = _scanner(Outcome(hits=(_hit(DESCRIPTORS[0], "a"),), request_sha256=canonical_hash), Outcome(hits=(_hit(DESCRIPTORS[1], "b"),), request_sha256=canonical_hash), Outcome(), Outcome(), Outcome()).scan(_request())
    second = _scanner(Outcome(hits=(_hit(DESCRIPTORS[0], "a"),), request_sha256=canonical_hash), Outcome(hits=(_hit(DESCRIPTORS[1], "b"),), request_sha256=canonical_hash), Outcome(), Outcome(), Outcome()).scan(_request())
    assert first.aggregation.results[0].candidate.canonical_sha256 == second.aggregation.results[0].candidate.canonical_sha256
    assert first.request_sha256 == second.request_sha256
    bad = _hit(DESCRIPTORS[0], "z").model_copy(update={"source": "wrong-source"})
    rejected = _scanner(Outcome(hits=(bad,)), Outcome(), Outcome(), Outcome(), Outcome()).scan(_request())
    assert rejected.provider_receipts[0].status == MultiRecallProviderStatus.FAILED
    assert rejected.provider_receipts[0].error_code == RecallProviderErrorCode.OUTPUT_REGISTRY_MISMATCH
    assert rejected.aggregation.results == ()


def test_registry_descriptor_mismatch_is_fail_closed() -> None:
    incomplete = ProviderRegistry(DESCRIPTORS[1:])
    result = _scanner(Outcome(hits=(_hit(DESCRIPTORS[0], "a"),)), Outcome(), Outcome(), Outcome(), Outcome(), registry=incomplete).scan(_request())
    assert result.provider_receipts[0].error_code == RecallProviderErrorCode.REGISTRY_DESCRIPTOR_MISMATCH
    assert result.aggregation.results == ()


def test_scan_request_binds_provider_run_and_as_of() -> None:
    with pytest.raises(ValidationError, match="prebook request"):
        _request(prebook_request=PrebookRecallRequest(run_id="other", as_of=NOW))
    with pytest.raises(ValidationError, match="prebook request"):
        _request(
            prebook_request=PrebookRecallRequest(
                run_id="scan-1",
                as_of=NOW.replace(hour=9),
            )
        )


def test_scan_outcome_exposes_only_aggregator_accepted_hit_payloads() -> None:
    historical = _hit(DESCRIPTORS[0], "h").model_copy(
        update={"historical_only": True}
    )
    result = _scanner(
        Outcome(hits=(historical,)),
        Outcome(),
        Outcome(),
        Outcome(),
        Outcome(),
    ).scan(_request())
    assert result.accepted_hits == ()
    assert result.aggregation.results == ()
    assert result.aggregation.rejected[0].recall_hit_id == historical.record_id


def test_outcome_provider_metadata_mismatch_fails_without_hits() -> None:
    class MismatchedOutcome(Outcome):
        provider_id: str
        recaller_version: str
        as_of: datetime

    bad = MismatchedOutcome(
        provider_id="wrong",
        recaller_version=DESCRIPTORS[0].recaller_version,
        as_of=NOW,
    )
    result = _scanner(bad, Outcome(), Outcome(), Outcome(), Outcome()).scan(_request())
    assert result.provider_receipts[0].status == MultiRecallProviderStatus.FAILED
    assert result.provider_receipts[0].error_code == RecallProviderErrorCode.OUTPUT_REGISTRY_MISMATCH
