"""Raw Gamma artifacts: content-addressed, immutable, dual-clock (ADR-005).

Every observed page and market payload is sealed as a canonical contract
record before any normalization runs, so a failed normalization still leaves
the raw evidence queryable.  Artifacts preserve canonicalized parsed JSON
(Unicode-NFC, floats converted to ``Decimal`` per ADR-012), not byte-for-byte
HTTP response bytes.

Cross-run identity (BF-P003-04): the envelope carries run-scoped fields
(``run_id``, ``created_at``), so the record id includes every field that can
change canonical bytes — payload content, observation clock, ingest clock and
run id.  Re-ingesting the same captured bytes under a new work order
therefore produces a *new* artifact (never a ``ContractConflictError``),
while ``payload_sha256``/``page_sha256`` remain the explicit logical
deduplication keys.  Per-run association belongs to
``alpha_run_artifact_link`` (P0-02 delta request).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import model_validator

from ..contracts import (
    CommonEnvelope,
    canonical_datetime,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc


def decimalize_payload(value: Any) -> Any:
    """Recursively convert floats to ``Decimal`` for canonical storage."""

    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Decimal):
        return value
    if isinstance(value, Mapping):
        return {key: decimalize_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [decimalize_payload(item) for item in value]
    return value


def _event_sort_key(event: Any) -> str:
    if isinstance(event, Mapping):
        identifier = event.get("id")
        if identifier is None:
            identifier = event.get("eventId")
        if isinstance(identifier, (str, int)) and not isinstance(identifier, bool):
            return str(identifier).strip()
    return ""


def canonicalize_market_payload(value: Any) -> Any:
    """Decimalize and stabilize the non-semantic ``events`` array order.

    Gamma returns a market's events as an unordered set; array order carries
    no source semantics (BF-P003-02), so it is stabilized by sorting on the
    event id before hashing — the same class of non-semantic normalization
    as canonical-JSON key sorting and rule-text whitespace stabilization.
    Every event object and field is preserved verbatim; only the sequence of
    the set is canonicalized.
    """

    sealed = decimalize_payload(value)
    if isinstance(sealed, Mapping) and isinstance(sealed.get("events"), list):
        events = sealed["events"]
        if all(isinstance(event, Mapping) for event in events):
            sealed = dict(sealed)
            sealed["events"] = sorted(events, key=_event_sort_key)
    return sealed


class GammaPageArtifact(CommonEnvelope):
    """One observed Gamma pagination page, preserved as canonical JSON."""

    items: tuple[Any, ...]
    page_sha256: str
    offset: int
    page_index: int
    source_observed_at: datetime

    @model_validator(mode="after")
    def page_sha_matches_stored_content(self) -> "GammaPageArtifact":
        if self.page_sha256 != content_sha256(list(self.items)):
            raise ValueError("page_sha256 does not match canonical stored items")
        if self.offset < 0:
            raise ValueError("offset must be non-negative")
        return self

    @property
    def logical_key(self) -> str:
        """Run-independent deduplication key for the captured page content."""

        return f"gamma_page_sha256:{self.page_sha256}"

    @staticmethod
    def record_identity(
        *,
        page_sha256: str,
        offset: int,
        source_observed_at: datetime,
        ingested_at: datetime,
        run_id: str,
    ) -> str:
        return stable_record_id(
            "gamma_page",
            page_sha256,
            offset,
            canonical_datetime(source_observed_at),
            canonical_datetime(ingested_at),
            run_id,
        )


class GammaMarketPayloadArtifact(CommonEnvelope):
    """One observed market payload, preserved as canonical JSON."""

    payload: Any
    payload_sha256: str
    source_observed_at: datetime

    @model_validator(mode="after")
    def payload_sha_matches_stored_content(self) -> "GammaMarketPayloadArtifact":
        if self.payload_sha256 != content_sha256(self.payload):
            raise ValueError("payload_sha256 does not match canonical stored payload")
        return self

    @property
    def logical_key(self) -> str:
        """Run-independent deduplication key for the captured payload bytes."""

        return f"gamma_market_payload_sha256:{self.payload_sha256}"

    @staticmethod
    def record_identity(
        *,
        payload_sha256: str,
        source_observed_at: datetime,
        ingested_at: datetime,
        run_id: str,
    ) -> str:
        return stable_record_id(
            "gamma_market_payload",
            payload_sha256,
            canonical_datetime(source_observed_at),
            canonical_datetime(ingested_at),
            run_id,
        )


def build_page_artifact(
    items: Sequence[Mapping[str, Any]] | Sequence[Any],
    *,
    offset: int,
    page_index: int,
    run_id: str,
    source_version: str,
    source_observed_at: datetime,
    ingested_at: datetime,
) -> GammaPageArtifact:
    observed = ensure_utc(source_observed_at)
    ingested = ensure_utc(ingested_at)
    if ingested < observed:
        raise ValueError("ingested_at cannot precede source_observed_at")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    sealed = tuple(canonicalize_market_payload(item) for item in items)
    page_sha256 = content_sha256(list(sealed))
    return GammaPageArtifact(
        record_id=GammaPageArtifact.record_identity(
            page_sha256=page_sha256,
            offset=offset,
            source_observed_at=observed,
            ingested_at=ingested,
            run_id=run_id,
        ),
        run_id=run_id,
        created_at=ingested,
        source="gamma_catalog_adapter",
        source_version=source_version,
        items=sealed,
        page_sha256=page_sha256,
        offset=offset,
        page_index=page_index,
        source_observed_at=observed,
    )


def build_market_payload_artifact(
    payload: Any,
    *,
    run_id: str,
    source_version: str,
    source_observed_at: datetime,
    ingested_at: datetime,
) -> GammaMarketPayloadArtifact:
    observed = ensure_utc(source_observed_at)
    ingested = ensure_utc(ingested_at)
    if ingested < observed:
        raise ValueError("ingested_at cannot precede source_observed_at")
    sealed = canonicalize_market_payload(payload)
    payload_sha256 = content_sha256(sealed)
    return GammaMarketPayloadArtifact(
        record_id=GammaMarketPayloadArtifact.record_identity(
            payload_sha256=payload_sha256,
            source_observed_at=observed,
            ingested_at=ingested,
            run_id=run_id,
        ),
        run_id=run_id,
        created_at=ingested,
        source="gamma_catalog_adapter",
        source_version=source_version,
        payload=sealed,
        payload_sha256=payload_sha256,
        source_observed_at=observed,
    )
