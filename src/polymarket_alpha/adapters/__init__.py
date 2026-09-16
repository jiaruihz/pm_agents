"""Offline Gamma catalog adapters for Polymarket Alpha P0 (P0-03).

The adapters in this package never perform network I/O.  Page payloads arrive
through injected callables or pre-captured fixtures; the production fetch
wiring (wrapping ``src/platform/clients/gamma.py``) is an operational-pilot
concern and deliberately lives outside this package so the offline capability
profile stays pure.
"""

from .gamma_identity import (
    GammaIdentityMismatchError,
    extract_condition_id,
    extract_event_ids,
    extract_primary_event_id,
    extract_market_id,
    map_yes_no_tokens,
    verify_market_response_identity,
)
from .gamma_pages import (
    DEFAULT_MAX_PAGES,
    PageCollection,
    PaginationReceipt,
    PaginationTermination,
    collect_gamma_pages,
)
from .gamma_raw import (
    GammaMarketPayloadArtifact,
    GammaPageArtifact,
    build_market_payload_artifact,
    build_page_artifact,
    decimalize_payload,
)
from .gamma_normalize import (
    KNOWN_GAMMA_MARKET_FIELDS,
    GammaNormalizationError,
    NormalizedMarket,
    lifecycle_status,
    normalize_market_payload,
    scan_unknown_fields,
)

__all__ = [
    "DEFAULT_MAX_PAGES",
    "KNOWN_GAMMA_MARKET_FIELDS",
    "GammaIdentityMismatchError",
    "GammaMarketPayloadArtifact",
    "GammaNormalizationError",
    "GammaPageArtifact",
    "NormalizedMarket",
    "PageCollection",
    "PaginationReceipt",
    "PaginationTermination",
    "build_market_payload_artifact",
    "build_page_artifact",
    "collect_gamma_pages",
    "decimalize_payload",
    "extract_condition_id",
    "extract_event_ids",
    "extract_primary_event_id",
    "extract_market_id",
    "lifecycle_status",
    "map_yes_no_tokens",
    "normalize_market_payload",
    "scan_unknown_fields",
    "verify_market_response_identity",
]
