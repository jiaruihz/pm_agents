"""Gamma catalog ingestor: raw artifact → identity → immutable revision.

Pipeline per captured page (ADR-005):

1. seal the page as a :class:`GammaPageArtifact` (content-addressed, raw);
   a page-artifact conflict is recorded as an error receipt and the batch
   continues — per-market artifacts still preserve the evidence;
2. for each market payload: seal a :class:`GammaMarketPayloadArtifact`
   *before* normalization, so failed markets still leave raw evidence;
3. scan for schema drift (unknown top-level fields → drift receipt; the
   values stay inside the raw artifact);
4. normalize identity/content with fail-closed validation (error receipt on
   any violation, including non-mapping payloads and contradictory lifecycle
   flags);
5. check ``condition_id`` uniqueness within the batch and through the
   repository-native stored-catalog lookup.  The repository also owns the
   database unique index, so cross-batch duplicates cannot fail open;
6. write an append-only ``MarketSnapshot`` revision whose ``record_id`` is
   derived from the full snapshot content (including ``run_id`` and the
   ingest clock, matching the artifact identity invariant of BF-P003-04), so
   identical replays deduplicate while any change — or a new run — produces
   new revisions and old revisions stay queryable.  ``created_at`` is pinned
   to the batch ingest clock to keep canonical bytes deterministic.

Artifact record ids include run-scoped fields (BF-P003-04), so re-ingesting
the same captured bytes under a new work order creates new artifacts with the
same ``payload_sha256``/``page_sha256`` logical keys instead of conflicting.
All repository saves inside the batch are wrapped in error-receipt boundaries;
no ``ContractConflictError`` escapes the ingestor and no batch dies midway
with partial writes and no receipt.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from ..contracts import (
    MarketAlias,
    MarketSnapshot,
    ProvenanceRef,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc
from ..storage import AlphaRepository, ContractConflictError
from ..adapters.gamma_identity import (
    GammaIdentityMismatchError,
    extract_condition_id,
    extract_market_id,
    verify_market_response_identity,
)
from ..adapters.gamma_normalize import (
    GammaNormalizationError,
    NormalizedMarket,
    normalize_market_payload,
    scan_unknown_fields,
)
from ..adapters.gamma_raw import (
    build_market_payload_artifact,
    build_page_artifact,
)


ADAPTER_SOURCE = "gamma_catalog_adapter"


@dataclass(frozen=True)
class CapturedPage:
    """One offline-captured Gamma page with its observation clock and window.

    ``requested_offset``/``requested_limit`` preserve the authoritative
    pagination window (BF-P003-06); ``termination`` carries the pagination
    receipt reason when the page came from ``collect_gamma_pages``.
    """

    items: tuple[Mapping[str, Any], ...]
    observed_at: datetime
    requested_offset: int = 0
    requested_limit: int | None = None
    termination: str | None = None

    def __post_init__(self) -> None:
        if self.requested_offset < 0:
            raise ValueError("requested_offset must be non-negative")
        if self.requested_limit is not None and self.requested_limit <= 0:
            raise ValueError("requested_limit must be positive when provided")

    @staticmethod
    def of(
        items: Sequence[Mapping[str, Any]],
        observed_at: datetime,
        *,
        requested_offset: int = 0,
        requested_limit: int | None = None,
        termination: str | None = None,
    ) -> "CapturedPage":
        return CapturedPage(
            items=tuple(items),
            observed_at=observed_at,
            requested_offset=requested_offset,
            requested_limit=requested_limit,
            termination=termination,
        )


@dataclass(frozen=True)
class DriftReceipt:
    market_ref: str
    unknown_fields: tuple[str, ...]


@dataclass(frozen=True)
class ErrorReceipt:
    market_ref: str
    error_code: str
    detail: str
    raw_artifact_id: str | None


@dataclass(frozen=True)
class CatalogIngestResult:
    run_id: str
    page_artifact_ids: tuple[str, ...] = ()
    raw_artifact_ids: tuple[str, ...] = ()
    snapshot_ids: tuple[str, ...] = ()
    snapshots: tuple[MarketSnapshot, ...] = ()
    aliases: tuple[MarketAlias, ...] = ()
    drift_receipts: tuple[DriftReceipt, ...] = ()
    error_receipts: tuple[ErrorReceipt, ...] = ()

    def counts(self) -> dict[str, int]:
        return {
            "pages": len(self.page_artifact_ids),
            "raw_market_artifacts": len(self.raw_artifact_ids),
            "snapshots": len(self.snapshot_ids),
            "aliases": len(self.aliases),
            "drift_receipts": len(self.drift_receipts),
            "error_receipts": len(self.error_receipts),
        }


def build_market_snapshot(
    normalized: NormalizedMarket,
    *,
    raw_artifact_id: str,
    run_id: str,
    source_version: str,
    source_observed_at: datetime,
    ingested_at: datetime,
) -> MarketSnapshot:
    """Assemble the immutable snapshot revision with a content-derived id."""

    fields: dict[str, Any] = {
        "run_id": run_id,
        "created_at": ensure_utc(ingested_at),
        "source": ADAPTER_SOURCE,
        "source_version": source_version,
        "identity": normalized.identity,
        "title": normalized.title,
        "question": normalized.question,
        "slug": normalized.slug,
        "status": normalized.status,
        "end_at": normalized.end_at,
        "tags": (),
        "rules_raw": normalized.rules_raw,
        "volume": normalized.volume,
        "liquidity": normalized.liquidity,
        "source_observed_at": ensure_utc(source_observed_at),
        "ingested_at": ensure_utc(ingested_at),
        "provenance": (
            ProvenanceRef(
                source_artifact_id=raw_artifact_id,
                relation="gamma_market_payload",
                source_observed_at=ensure_utc(source_observed_at),
            ),
        ),
        # Full event set travels on extensions (BF-P003-02): the repository
        # projects every event-market join from it; the singular identity
        # stays the deterministic minimum event id.
        "extensions": {"gamma_event_ids": list(normalized.event_ids)},
    }
    fingerprint = content_sha256(fields)
    record_id = stable_record_id(
        "gamma_market_snapshot", normalized.identity.market_id, fingerprint
    )
    return MarketSnapshot(record_id=record_id, **fields)


def build_slug_alias(
    normalized: NormalizedMarket,
    *,
    source_observed_at: datetime,
) -> MarketAlias | None:
    """Build the current-interval slug alias value object.

    Persistence (including closing the previous interval when the slug
    changes) is a repository capability requested from P0-02 — the adapter
    must not write alias rows itself (BF-P003-05).
    """

    if not normalized.slug:
        return None
    return MarketAlias(
        market_id=normalized.identity.market_id,
        source="gamma",
        alias_type="SLUG",
        alias_value=normalized.slug,
        effective_from=ensure_utc(source_observed_at),
    )


class GammaCatalogIngestor:
    """Offline catalog writer over the canonical contract repository."""

    def __init__(
        self,
        repository: AlphaRepository,
        *,
        source_version: str,
    ) -> None:
        self._repository = repository
        self._source_version = source_version

    @property
    def source_version(self) -> str:
        return self._source_version

    def ingest_pages(
        self,
        pages: Iterable[CapturedPage],
        *,
        run_id: str,
        ingested_at: datetime,
    ) -> CatalogIngestResult:
        ingested = ensure_utc(ingested_at)
        page_artifact_ids: list[str] = []
        raw_artifact_ids: list[str] = []
        snapshot_ids: list[str] = []
        snapshots: list[MarketSnapshot] = []
        aliases: list[MarketAlias] = []
        drift_receipts: list[DriftReceipt] = []
        error_receipts: list[ErrorReceipt] = []
        condition_owners: dict[str, str] = {}

        for page_index, page in enumerate(pages):
            observed = ensure_utc(page.observed_at)
            if observed > ingested:
                raise ValueError("page observed_at cannot be after ingested_at")
            page_artifact = build_page_artifact(
                page.items,
                offset=page.requested_offset,
                page_index=page_index,
                run_id=run_id,
                source_version=self._source_version,
                source_observed_at=observed,
                ingested_at=ingested,
            )
            try:
                self._repository.save_contract(page_artifact)
            except ContractConflictError as exc:
                error_receipts.append(
                    ErrorReceipt(
                        market_ref=f"page:{page_index}",
                        error_code="PAGE_ARTIFACT_CONFLICT",
                        detail=str(exc),
                        raw_artifact_id=page_artifact.record_id,
                    )
                )
            else:
                if page_artifact.record_id not in page_artifact_ids:
                    page_artifact_ids.append(page_artifact.record_id)
                try:
                    self._repository.link_run_artifact(
                        run_id, page_artifact.record_id, "gamma_page_capture"
                    )
                except ContractConflictError as exc:
                    error_receipts.append(
                        ErrorReceipt(
                            market_ref=f"page:{page_index}",
                            error_code="PAGE_ARTIFACT_LINK_CONFLICT",
                            detail=str(exc),
                            raw_artifact_id=page_artifact.record_id,
                        )
                    )

            for payload in page.items:
                self._ingest_market_payload(
                    payload,
                    page_index=page_index,
                    source_observed_at=observed,
                    ingested_at=ingested,
                    run_id=run_id,
                    condition_owners=condition_owners,
                    raw_artifact_ids=raw_artifact_ids,
                    snapshot_ids=snapshot_ids,
                    snapshots=snapshots,
                    aliases=aliases,
                    drift_receipts=drift_receipts,
                    error_receipts=error_receipts,
                )

        return CatalogIngestResult(
            run_id=run_id,
            page_artifact_ids=tuple(page_artifact_ids),
            raw_artifact_ids=tuple(raw_artifact_ids),
            snapshot_ids=tuple(snapshot_ids),
            snapshots=tuple(snapshots),
            aliases=tuple(aliases),
            drift_receipts=tuple(drift_receipts),
            error_receipts=tuple(error_receipts),
        )

    def ingest_verified_market(
        self,
        payload: Mapping[str, Any] | None,
        *,
        expected_market_id: str | None = None,
        expected_slug: str | None = None,
        expected_condition_id: str | None = None,
        run_id: str,
        source_observed_at: datetime,
        ingested_at: datetime,
    ) -> CatalogIngestResult:
        """Ingest a single-market query response after F-05 verification.

        Identity mismatches (and missing/blank expectations) fail closed as
        error receipts — the verified path never accepts anonymously and
        never crashes the caller (BF-P003-03).
        """

        requested_ref = expected_market_id or expected_condition_id or expected_slug
        try:
            verified = verify_market_response_identity(
                payload,
                expected_market_id=expected_market_id,
                expected_slug=expected_slug,
                expected_condition_id=expected_condition_id,
            )
        except GammaIdentityMismatchError as exc:
            return CatalogIngestResult(
                run_id=run_id,
                error_receipts=(
                    ErrorReceipt(
                        market_ref=requested_ref or "unknown",
                        error_code="IDENTITY_MISMATCH",
                        detail=exc.detail,
                        raw_artifact_id=None,
                    ),
                ),
            )
        if verified is None:
            return CatalogIngestResult(
                run_id=run_id,
                error_receipts=(
                    ErrorReceipt(
                        market_ref=requested_ref or "unknown",
                        error_code="QUERY_RETURNED_NOTHING",
                        detail="market query returned no payload",
                        raw_artifact_id=None,
                    ),
                ),
            )
        page = CapturedPage.of([verified], source_observed_at)
        return self.ingest_pages([page], run_id=run_id, ingested_at=ingested_at)

    def _ingest_market_payload(
        self,
        payload: Mapping[str, Any],
        *,
        page_index: int,
        source_observed_at: datetime,
        ingested_at: datetime,
        run_id: str,
        condition_owners: dict[str, str],
        raw_artifact_ids: list[str],
        snapshot_ids: list[str],
        snapshots: list[MarketSnapshot],
        aliases: list[MarketAlias],
        drift_receipts: list[DriftReceipt],
        error_receipts: list[ErrorReceipt],
    ) -> None:
        def _receipt(error_code: str, detail: str, raw_artifact_id: str | None, market_ref: str) -> None:
            error_receipts.append(
                ErrorReceipt(
                    market_ref=market_ref,
                    error_code=error_code,
                    detail=detail,
                    raw_artifact_id=raw_artifact_id,
                )
            )

        if not isinstance(payload, Mapping):
            _receipt(
                "PAYLOAD_NOT_MAPPING",
                f"page {page_index} item is not a JSON object: {type(payload).__name__}",
                None,
                f"page:{page_index}",
            )
            return

        # Raw first (ADR-005): even an unnormalizable payload leaves evidence.
        try:
            raw_artifact = build_market_payload_artifact(
                payload,
                run_id=run_id,
                source_version=self._source_version,
                source_observed_at=source_observed_at,
                ingested_at=ingested_at,
            )
            self._repository.save_contract(raw_artifact)
            self._repository.link_run_artifact(
                run_id, raw_artifact.record_id, "gamma_market_payload"
            )
        except ContractConflictError as exc:
            _receipt("RAW_ARTIFACT_CONFLICT", str(exc), None, f"page:{page_index}")
            return
        except (ValidationError, ValueError) as exc:
            _receipt("RAW_ARTIFACT_INVALID", str(exc), None, f"page:{page_index}")
            return
        if raw_artifact.record_id not in raw_artifact_ids:
            raw_artifact_ids.append(raw_artifact.record_id)

        market_ref = extract_market_id(payload) or raw_artifact.payload_sha256[:16]

        unknown_fields = scan_unknown_fields(payload)
        if unknown_fields:
            drift_receipts.append(
                DriftReceipt(market_ref=market_ref, unknown_fields=unknown_fields)
            )

        try:
            normalized = normalize_market_payload(
                payload, source_observed_at=source_observed_at
            )
        except GammaNormalizationError as exc:
            _receipt(exc.reason_code, exc.detail, raw_artifact.record_id, market_ref)
            return

        condition_id = normalized.identity.condition_id
        if condition_id:
            owner = condition_owners.get(condition_id)
            if owner is None:
                owner = self._repository.market_id_for_condition(condition_id)
            if owner is not None and owner != normalized.identity.market_id:
                _receipt(
                    "CONDITION_ID_CONFLICT",
                    f"condition_id {condition_id} already belongs to market {owner}",
                    raw_artifact.record_id,
                    market_ref,
                )
                return
            condition_owners.setdefault(condition_id, normalized.identity.market_id)

        try:
            snapshot = build_market_snapshot(
                normalized,
                raw_artifact_id=raw_artifact.record_id,
                run_id=run_id,
                source_version=self._source_version,
                source_observed_at=source_observed_at,
                ingested_at=ingested_at,
            )
            self._repository.save_contract(snapshot)
        except ContractConflictError as exc:
            _receipt("IDENTITY_CONFLICT", str(exc), raw_artifact.record_id, market_ref)
            return
        except ValidationError as exc:
            _receipt("SNAPSHOT_INVALID", str(exc), raw_artifact.record_id, market_ref)
            return

        if snapshot.record_id not in snapshot_ids:
            snapshot_ids.append(snapshot.record_id)
            snapshots.append(snapshot)
        alias = build_slug_alias(normalized, source_observed_at=source_observed_at)
        if alias is not None:
            try:
                self._repository.save_market_alias(alias)
            except (ContractConflictError, ValueError) as exc:
                _receipt("ALIAS_CONFLICT", str(exc), raw_artifact.record_id, market_ref)
            else:
                if alias not in aliases:
                    aliases.append(alias)
