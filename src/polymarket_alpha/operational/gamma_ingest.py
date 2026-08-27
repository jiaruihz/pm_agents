"""Offline ingest seam for already-captured Gamma ``/events`` responses.

GLM-OP-01 glue only: this module verifies caller-supplied raw response bytes
against a sealed request/response receipt, flattens the nested ``markets``
arrays into the released :class:`CapturedPage` shape, and delegates identity
normalization plus storage to the existing :class:`GammaCatalogIngestor`.
It never opens a socket, imports an HTTP client, or owns a second
normalizer.  Every pre-ingest violation fails closed with a typed receipt and
zero repository writes; per-market normalization failures keep the existing
error-receipt behavior of the catalog ingestor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from ..adapters.gamma_identity import extract_event_ids, normalize_json_list
from ..census.catalog import CatalogIngestResult, CapturedPage, GammaCatalogIngestor
from ..contracts import bytes_sha256
from ..contracts.base import ensure_utc
from ..storage import AlphaRepository


GAMMA_EVENTS_HOST = "gamma-api.polymarket.com"
GAMMA_EVENTS_PATH = "/events"
GAMMA_EVENTS_METHOD = "GET"
GAMMA_INGEST_SOURCE_VERSION = "op_gamma_ingest_v1"
GAMMA_INGEST_HTTP_OK = 200


class GammaIngestFailureCode(StrEnum):
    """Typed fail-closed reasons for a captured response ingest."""

    HTTP_STATUS_NOT_OK = "HTTP_STATUS_NOT_OK"
    ENDPOINT_IDENTITY_MISMATCH = "ENDPOINT_IDENTITY_MISMATCH"
    RECEIPT_HASH_MISMATCH = "RECEIPT_HASH_MISMATCH"
    RECEIPT_LENGTH_MISMATCH = "RECEIPT_LENGTH_MISMATCH"
    RECEIPT_CLOCK_MISMATCH = "RECEIPT_CLOCK_MISMATCH"
    MALFORMED_JSON = "MALFORMED_JSON"
    PAYLOAD_NOT_LIST = "PAYLOAD_NOT_LIST"
    EVENT_NOT_MAPPING = "EVENT_NOT_MAPPING"
    MARKETS_FIELD_INVALID = "MARKETS_FIELD_INVALID"
    NESTED_MARKET_NOT_MAPPING = "NESTED_MARKET_NOT_MAPPING"
    MARKET_COUNT_OVER_BUDGET = "MARKET_COUNT_OVER_BUDGET"
    MARKET_COUNT_OVER_CALLER_LIMIT = "MARKET_COUNT_OVER_CALLER_LIMIT"


@dataclass(frozen=True)
class GammaResponseReceipt:
    """Caller-supplied sealed request/response fact for one captured page.

    ``response_bytes_sha256``/``response_byte_length`` bind the exact raw
    bytes; the endpoint fields must match the canonical public ``/events``
    route this seam is allowed to ingest.
    """

    request_method: str
    endpoint_host: str
    endpoint_path: str
    http_status: int
    response_bytes_sha256: str
    response_byte_length: int
    response_received_at: datetime

    def __post_init__(self) -> None:
        if not str(self.request_method).strip():
            raise ValueError("receipt request_method must not be blank")
        if not str(self.endpoint_host).strip() or not str(self.endpoint_path).strip():
            raise ValueError("receipt endpoint identity must not be blank")
        if self.response_byte_length < 0:
            raise ValueError("receipt response_byte_length must be non-negative")
        ensure_utc(self.response_received_at)


@dataclass(frozen=True)
class GammaIngestFailure:
    """One typed fail-closed outcome; no repository write happened."""

    code: GammaIngestFailureCode
    detail: str
    response_bytes_sha256: str | None = None
    response_byte_length: int | None = None


@dataclass(frozen=True)
class GammaOperationalIngestResult:
    accepted: bool
    failure: GammaIngestFailure | None
    catalog: CatalogIngestResult | None
    response_bytes_sha256: str
    response_byte_length: int
    event_ids: tuple[str, ...]
    flattened_market_count: int


class _IngestRejected(Exception):
    """Internal control-flow carrier; converted to a typed receipt."""

    def __init__(self, code: GammaIngestFailureCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _parent_event_identity(event: Mapping[str, Any]) -> tuple[str, ...]:
    """Reuse the released event-id extraction on the parent event object."""

    return extract_event_ids({"events": [event]})


def _merge_parent_event(market: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    """Carry the parent event on the market's existing ``events`` path.

    The catalog normalizer reads event ids and titles from the market
    payload's ``events`` array (BF-P003-02), so flattening keeps that exact
    path: existing entries are preserved, the parent event (without its
    nested ``markets`` list) is appended, and id duplicates collapse.
    """

    merged = dict(market)
    entries: list[Any] = [
        item for item in normalize_json_list(merged.get("events")) if isinstance(item, Mapping)
    ]
    parent = {key: value for key, value in event.items() if key != "markets"}
    parent_ids = set(_parent_event_identity(parent))
    existing_ids = set(extract_event_ids({"events": entries}))
    if not parent_ids.issubset(existing_ids):
        entries.append(parent)
    merged["events"] = entries
    return merged


def _flatten_events_payload(
    raw: bytes, *, page_budget: int
) -> tuple[list[dict[str, Any]], tuple[str, ...], int]:
    """Return (flattened markets, sorted event ids, number of events).

    Note: the caller-supplied raw response bytes are bound exactly by the
    receipt hash, while the sealed catalog artifacts store the canonicalized
    merged market payloads (parent event carried on the market ``events``
    path); that is the released catalog contract, not verbatim HTTP bytes.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _IngestRejected(GammaIngestFailureCode.MALFORMED_JSON, str(error)) from error
    if not isinstance(payload, list):
        raise _IngestRejected(
            GammaIngestFailureCode.PAYLOAD_NOT_LIST,
            f"/events response must be a JSON list, got {type(payload).__name__}",
        )

    markets: list[dict[str, Any]] = []
    event_ids: list[str] = []
    for index, event in enumerate(payload):
        if not isinstance(event, Mapping):
            raise _IngestRejected(
                GammaIngestFailureCode.EVENT_NOT_MAPPING,
                f"events[{index}] is not a JSON object: {type(event).__name__}",
            )
        ids = _parent_event_identity(event)
        event_ids.extend(ids)
        nested = event.get("markets")
        if nested is None:
            continue
        if not isinstance(nested, list):
            raise _IngestRejected(
                GammaIngestFailureCode.MARKETS_FIELD_INVALID,
                f"events[{index}].markets must be a list, got {type(nested).__name__}",
            )
        for market_index, market in enumerate(nested):
            if not isinstance(market, Mapping):
                raise _IngestRejected(
                    GammaIngestFailureCode.NESTED_MARKET_NOT_MAPPING,
                    f"events[{index}].markets[{market_index}] is not a JSON object",
                )
            markets.append(_merge_parent_event(market, event))
    if len(markets) > page_budget:
        raise _IngestRejected(
            GammaIngestFailureCode.MARKET_COUNT_OVER_BUDGET,
            f"response flattens to {len(markets)} markets over the page budget {page_budget}",
        )
    return markets, tuple(sorted(set(event_ids))), len(payload)


def ingest_captured_events_response(
    repository: AlphaRepository,
    *,
    raw_response: bytes,
    response_receipt: GammaResponseReceipt,
    run_id: str,
    observed_at: datetime,
    ingested_at: datetime,
    page_budget: int,
    requested_offset: int = 0,
    requested_limit: int | None = None,
    max_flattened_markets: int | None = None,
) -> GammaOperationalIngestResult:
    """Verify, flatten and ingest one captured ``/events`` response page.

    Fail-closed ordering: every receipt/hash/shape/budget check runs before
    the first repository write, so a rejected response leaves zero rows.
    Replaying the exact same inputs is idempotent because the catalog
    ingestor seals content-addressed, run-scoped artifacts.
    """

    if not isinstance(raw_response, bytes):
        raise TypeError("raw_response must be caller-supplied bytes")
    if page_budget <= 0:
        raise ValueError("page_budget must be positive")
    if requested_offset < 0:
        raise ValueError("requested_offset must be non-negative")
    if max_flattened_markets is not None and max_flattened_markets <= 0:
        raise ValueError("max_flattened_markets must be positive when supplied")
    observed_at = ensure_utc(observed_at)
    ingested_at = ensure_utc(ingested_at)
    raw_hash = bytes_sha256(raw_response)

    def _fail(code: GammaIngestFailureCode, detail: str) -> GammaOperationalIngestResult:
        return GammaOperationalIngestResult(
            accepted=False,
            failure=GammaIngestFailure(
                code=code,
                detail=detail,
                response_bytes_sha256=raw_hash,
                response_byte_length=len(raw_response),
            ),
            catalog=None,
            response_bytes_sha256=raw_hash,
            response_byte_length=len(raw_response),
            event_ids=(),
            flattened_market_count=0,
        )

    if response_receipt.http_status != GAMMA_INGEST_HTTP_OK:
        return _fail(
            GammaIngestFailureCode.HTTP_STATUS_NOT_OK,
            f"captured status {response_receipt.http_status} is not {GAMMA_INGEST_HTTP_OK}",
        )
    if (
        response_receipt.request_method != GAMMA_EVENTS_METHOD
        or response_receipt.endpoint_host != GAMMA_EVENTS_HOST
        or response_receipt.endpoint_path != GAMMA_EVENTS_PATH
    ):
        return _fail(
            GammaIngestFailureCode.ENDPOINT_IDENTITY_MISMATCH,
            "receipt endpoint is not the canonical Gamma public /events route",
        )
    if response_receipt.response_bytes_sha256 != raw_hash:
        return _fail(
            GammaIngestFailureCode.RECEIPT_HASH_MISMATCH,
            "raw response bytes do not match the sealed receipt hash",
        )
    if response_receipt.response_byte_length != len(raw_response):
        return _fail(
            GammaIngestFailureCode.RECEIPT_LENGTH_MISMATCH,
            f"raw response length {len(raw_response)} does not match receipt "
            f"{response_receipt.response_byte_length}",
        )
    if observed_at < response_receipt.response_received_at:
        return _fail(
            GammaIngestFailureCode.RECEIPT_CLOCK_MISMATCH,
            "page observed_at cannot precede the receipt response clock",
        )
    if ingested_at < observed_at:
        return _fail(
            GammaIngestFailureCode.RECEIPT_CLOCK_MISMATCH,
            "ingested_at cannot precede observed_at",
        )

    try:
        markets, event_ids, event_count = _flatten_events_payload(raw_response, page_budget=page_budget)
    except _IngestRejected as rejected:
        failure = GammaIngestFailure(
            code=rejected.code,
            detail=rejected.detail,
            response_bytes_sha256=raw_hash,
            response_byte_length=len(raw_response),
        )
        return GammaOperationalIngestResult(
            accepted=False,
            failure=failure,
            catalog=None,
            response_bytes_sha256=raw_hash,
            response_byte_length=len(raw_response),
            event_ids=(),
            flattened_market_count=0,
        )

    if max_flattened_markets is not None and len(markets) > max_flattened_markets:
        return _fail(
            GammaIngestFailureCode.MARKET_COUNT_OVER_CALLER_LIMIT,
            f"response flattens to {len(markets)} markets over the caller limit "
            f"{max_flattened_markets}",
        )

    termination = None
    # /events pagination limits count events, so the short-page signal is
    # derived from the event count, never from the flattened market count.
    if requested_limit is not None and event_count < requested_limit:
        termination = "SHORT_PAGE"
    page = CapturedPage.of(
        markets,
        observed_at,
        requested_offset=requested_offset,
        requested_limit=requested_limit,
        termination=termination,
    )
    ingestor = GammaCatalogIngestor(repository, source_version=GAMMA_INGEST_SOURCE_VERSION)
    catalog_result = ingestor.ingest_pages([page], run_id=run_id, ingested_at=ingested_at)
    return GammaOperationalIngestResult(
        accepted=True,
        failure=None,
        catalog=catalog_result,
        response_bytes_sha256=raw_hash,
        response_byte_length=len(raw_response),
        event_ids=event_ids,
        flattened_market_count=len(markets),
    )
