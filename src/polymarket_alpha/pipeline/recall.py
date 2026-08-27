"""Deterministic, offline fan-in for the P0 recall providers.

This module owns scheduling only.  Providers, their input contracts, and
Candidate construction remain owned by their released P0-06 modules.  It
never opens a database, makes a request, or performs book capture.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from ..contracts import AlphaContract, CandidateCard, RecallHit, content_sha256
from ..contracts.base import ensure_utc
from ..recall.aggregate import (
    RecallAggregationOutcome,
    RecallAggregationRequest,
    RecallAggregator,
    ProviderBatch,
)
from ..recall.book_anomaly import BookAnomalyRecallProvider, BookAnomalyRecallRequest
from ..recall.controversy import ControversyRecaller, ControversyRecallRequest
from ..recall.new_changed import NewChangedRecaller, PrebookRecallRequest
from ..recall.registry import ProviderDescriptor, ProviderRegistry
from ..recall.structural_metadata import StructuralMetadataRecaller
from ..recall.wallet import WalletRecallProvider, WalletRecallRequest


class MultiRecallProviderStatus(StrEnum):
    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class RecallProviderErrorCode(StrEnum):
    OPTIONAL_REQUEST_MISSING = "OPTIONAL_REQUEST_MISSING"
    BOOK_ROUTE_DISABLED = "BOOK_ROUTE_DISABLED"
    PROVIDER_VALUE_ERROR = "PROVIDER_VALUE_ERROR"
    PROVIDER_EXCEPTION = "PROVIDER_EXCEPTION"
    PROVIDER_OUTPUT_INVALID = "PROVIDER_OUTPUT_INVALID"
    REGISTRY_DESCRIPTOR_MISMATCH = "REGISTRY_DESCRIPTOR_MISMATCH"
    OUTPUT_REGISTRY_MISMATCH = "OUTPUT_REGISTRY_MISMATCH"


class MultiRecallScanRequest(AlphaContract):
    """All caller-supplied, frozen inputs for one recall scan.

    New/changed and structural metadata share one mandatory pre-book request.
    Controversy, wallet and book are opt-in routes.  In particular, a book
    request cannot become a prerequisite for Candidate formation.
    """

    run_id: str
    created_at: datetime
    as_of: datetime
    prebook_request: PrebookRecallRequest
    controversy_request: ControversyRecallRequest | None = None
    wallet_request: WalletRecallRequest | None = None
    book_anomaly_request: BookAnomalyRecallRequest | None = None
    enable_book_anomaly: bool = False
    prior_candidates: tuple[CandidateCard, ...] = ()
    prior_hits: tuple[RecallHit, ...] = ()
    prior_projection_input_hashes: dict[str, str] = Field(default_factory=dict)
    merged_projection_input_hashes: dict[str, str] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def nonblank_run_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("run_id must not be blank")
        return value

    @field_validator("created_at", "as_of")
    @classmethod
    def utc_times(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def provider_clocks_and_runs_are_bound(self) -> "MultiRecallScanRequest":
        if self.prebook_request.run_id != self.run_id or self.prebook_request.as_of != self.as_of:
            raise ValueError("prebook request must bind the unified scan run/as_of")
        if self.controversy_request is not None and (
            self.controversy_request.run_id != self.run_id
            or self.controversy_request.as_of != self.as_of
        ):
            raise ValueError("controversy request must bind the unified scan run/as_of")
        if self.wallet_request is not None and (
            self.wallet_request.run_id != self.run_id
            or self.wallet_request.clock.as_of != self.as_of
        ):
            raise ValueError("wallet request must bind the unified scan run/as_of")
        if self.book_anomaly_request is not None and (
            self.book_anomaly_request.run_id != self.run_id
            or self.book_anomaly_request.as_of != self.as_of
        ):
            raise ValueError("book request must bind the unified scan run/as_of")
        return self


class MultiRecallProviderReceipt(AlphaContract):
    provider_id: str
    status: MultiRecallProviderStatus
    request_sha256: str
    output_sha256: str
    hit_count: int = Field(ge=0)
    rejection_count: int = Field(ge=0)
    skip_count: int = Field(ge=0)
    suppression_count: int = Field(ge=0)
    error_code: RecallProviderErrorCode | None = None


class MultiRecallScanOutcome(AlphaContract):
    request_sha256: str
    provider_receipts: tuple[MultiRecallProviderReceipt, ...]
    accepted_hits: tuple[RecallHit, ...]
    aggregation: RecallAggregationOutcome


def _descriptor(provider: Any) -> ProviderDescriptor:
    candidate = provider.descriptor
    return candidate() if callable(candidate) else candidate


def _outcome_counts(outcome: Any) -> tuple[int, int, int, int]:
    """Extract only non-sensitive control-plane counts from released outcomes."""

    route_skips = sum(
        1
        for item in getattr(outcome, "route_results", ())
        if getattr(getattr(item, "status", None), "value", getattr(item, "status", None)) == "SKIPPED"
    )
    return (
        len(getattr(outcome, "hits", ())),
        len(getattr(outcome, "rejections", ())),
        len(getattr(outcome, "skips", ())) + route_skips,
        len(getattr(outcome, "suppressions", ())),
    )


def _released_request_hash(fallback: str, outcome: Any) -> str:
    """Use a provider's released canonical request fingerprint when present."""

    if outcome is None:
        return fallback
    for field in ("request_sha256", "input_sha256"):
        value = getattr(outcome, field, None)
        if isinstance(value, str) and len(value) == 64:
            return value
    # Wallet intentionally separates public and private input fingerprints.
    public = getattr(outcome, "public_input_sha256", None)
    private = getattr(outcome, "private_input_sha256", None)
    if isinstance(public, str) and isinstance(private, str):
        return content_sha256({"public_input_sha256": public, "private_input_sha256": private})
    return fallback


def _outcome_metadata_matches(
    outcome: Any, descriptor: ProviderDescriptor, *, as_of: datetime
) -> bool:
    provider_id = getattr(outcome, "provider_id", None)
    version = getattr(
        outcome,
        "recaller_version",
        getattr(outcome, "provider_version", None),
    )
    outcome_as_of = getattr(outcome, "as_of", None)
    return (
        provider_id == descriptor.provider_id
        and version == descriptor.recaller_version
        and outcome_as_of == as_of
    )


class MultiRecallScanner:
    """Fixed-order, failure-isolating fan-out and shared aggregation."""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        new_changed: Any | None = None,
        structural_metadata: Any | None = None,
        controversy: Any | None = None,
        wallet: Any | None = None,
        book_anomaly: Any | None = None,
        aggregator: RecallAggregator | None = None,
    ) -> None:
        self.registry = registry
        self.providers = (
            ("new_changed", new_changed or NewChangedRecaller()),
            ("structural_metadata", structural_metadata or StructuralMetadataRecaller()),
            ("controversy", controversy or ControversyRecaller()),
            ("specialist_wallet", wallet or WalletRecallProvider()),
            ("book_anomaly", book_anomaly or BookAnomalyRecallProvider()),
        )
        self.aggregator = aggregator or RecallAggregator(registry)

    def scan(self, request: MultiRecallScanRequest) -> MultiRecallScanOutcome:
        routes = (
            ("new_changed", request.prebook_request, False),
            ("structural_metadata", request.prebook_request, False),
            ("controversy", request.controversy_request, True),
            ("specialist_wallet", request.wallet_request, True),
            ("book_anomaly", request.book_anomaly_request, True),
        )
        receipts: list[MultiRecallProviderReceipt] = []
        batches: list[ProviderBatch] = []
        accepted_hits: list[RecallHit] = []
        for (route_name, provider), (_, provider_request, optional) in zip(self.providers, routes, strict=True):
            descriptor = _descriptor(provider)
            request_hash = content_sha256(provider_request) if provider_request is not None else content_sha256({"route": route_name, "missing": True})
            registered = self.registry.get(descriptor.provider_id)
            if descriptor.provider_id != route_name or registered != descriptor:
                receipts.append(self._receipt(route_name, MultiRecallProviderStatus.FAILED, request_hash, (), RecallProviderErrorCode.REGISTRY_DESCRIPTOR_MISMATCH))
                continue
            if route_name == "book_anomaly" and not request.enable_book_anomaly:
                receipts.append(self._receipt(route_name, MultiRecallProviderStatus.SKIPPED, request_hash, (), RecallProviderErrorCode.BOOK_ROUTE_DISABLED))
                continue
            if provider_request is None:
                if not optional:
                    raise ValueError(f"mandatory request missing for {route_name}")
                receipts.append(self._receipt(route_name, MultiRecallProviderStatus.SKIPPED, request_hash, (), RecallProviderErrorCode.OPTIONAL_REQUEST_MISSING))
                continue
            try:
                result = provider.recall(provider_request)
            except ValueError:
                receipts.append(self._receipt(route_name, MultiRecallProviderStatus.FAILED, request_hash, (), RecallProviderErrorCode.PROVIDER_VALUE_ERROR))
                continue
            except Exception:
                receipts.append(self._receipt(route_name, MultiRecallProviderStatus.FAILED, request_hash, (), RecallProviderErrorCode.PROVIDER_EXCEPTION))
                continue
            try:
                raw_hits = getattr(result, "hits")
                if not isinstance(result, AlphaContract) or not isinstance(raw_hits, tuple):
                    raise TypeError("provider output/hits must be released immutable contracts")
                hits = raw_hits
                if any(not isinstance(hit, RecallHit) for hit in hits):
                    raise TypeError("provider hits must be RecallHit contracts")
                released_hash = _released_request_hash(request_hash, result)
                if not _outcome_metadata_matches(result, descriptor, as_of=request.as_of):
                    receipts.append(self._receipt(route_name, MultiRecallProviderStatus.FAILED, released_hash, result, RecallProviderErrorCode.OUTPUT_REGISTRY_MISMATCH))
                    continue
                mismatch = next((hit for hit in hits if self.registry.validate_hit(descriptor.provider_id, hit, include_book=request.enable_book_anomaly) is not None), None)
                if mismatch is not None:
                    receipts.append(self._receipt(route_name, MultiRecallProviderStatus.FAILED, released_hash, result, RecallProviderErrorCode.OUTPUT_REGISTRY_MISMATCH))
                    continue
                receipt = self._receipt(
                    route_name,
                    MultiRecallProviderStatus.SUCCESS,
                    released_hash,
                    result,
                    None,
                )
            except Exception:
                receipts.append(self._receipt(route_name, MultiRecallProviderStatus.FAILED, request_hash, (), RecallProviderErrorCode.PROVIDER_OUTPUT_INVALID))
                continue
            receipts.append(receipt)
            batches.append(ProviderBatch(provider_id=descriptor.provider_id, hits=hits))
            accepted_hits.extend(hits)

        aggregation = self.aggregator.aggregate(
            RecallAggregationRequest(
                run_id=request.run_id,
                created_at=request.created_at,
                as_of=request.as_of,
                include_book_providers=request.enable_book_anomaly,
                batches=tuple(batches),
                prior_candidates=request.prior_candidates,
                prior_hits=request.prior_hits,
                prior_projection_input_hashes=request.prior_projection_input_hashes,
                merged_projection_input_hashes=request.merged_projection_input_hashes,
            )
        )
        aggregated_hit_ids = {
            hit_id
            for result in aggregation.results
            for hit_id in result.accepted_recall_hit_ids
        }
        available_hits: dict[str, RecallHit] = {}
        for item in (*request.prior_hits, *accepted_hits):
            existing = available_hits.get(item.record_id)
            if existing is not None and existing.canonical_sha256 != item.canonical_sha256:
                raise ValueError("same RecallHit record_id has conflicting prior/current payloads")
            available_hits[item.record_id] = item
        if not aggregated_hit_ids.issubset(available_hits):
            raise ValueError("aggregator accepted a RecallHit without an available canonical payload")
        return MultiRecallScanOutcome(
            request_sha256=content_sha256(
                {
                    "run_id": request.run_id,
                    "created_at": request.created_at,
                    "as_of": request.as_of,
                    "enable_book_anomaly": request.enable_book_anomaly,
                    "provider_request_sha256": [
                        (item.provider_id, item.request_sha256) for item in receipts
                    ],
                    "prior_candidate_ids": sorted(item.candidate_id for item in request.prior_candidates),
                    "prior_hit_ids": sorted(item.record_id for item in request.prior_hits),
                    "prior_projection_input_hashes": request.prior_projection_input_hashes,
                    "merged_projection_input_hashes": request.merged_projection_input_hashes,
                }
            ),
            provider_receipts=tuple(receipts),
            accepted_hits=tuple(
                sorted(
                    (available_hits[item] for item in aggregated_hit_ids),
                    key=lambda item: item.record_id,
                )
            ),
            aggregation=aggregation,
        )

    @staticmethod
    def _receipt(
        provider_id: str,
        status: MultiRecallProviderStatus,
        request_sha256: str,
        outcome: Any,
        error_code: RecallProviderErrorCode | None,
    ) -> MultiRecallProviderReceipt:
        hits, rejections, skips, suppressions = _outcome_counts(outcome)
        # Hash only the already-redacted provider contract/output, never echo it.
        output_sha256 = content_sha256(outcome if outcome else {"provider_id": provider_id, "status": status})
        return MultiRecallProviderReceipt(
            provider_id=provider_id,
            status=status,
            request_sha256=request_sha256,
            output_sha256=output_sha256,
            hit_count=hits,
            rejection_count=rejections,
            skip_count=skips,
            suppression_count=suppressions,
            error_code=error_code,
        )
