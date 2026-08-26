"""Offset pagination adapter with bounded, receipted termination (F-02, BF-P003-06).

``collect_gamma_pages`` wraps an injected ``fetch_page(offset)`` callable.  It
never imports an HTTP client itself, so the offline capability profile of the
catalog package is preserved.  Every collection carries a finite page budget:

* ``max_pages`` bounds the scan when provided and otherwise falls back to
  ``DEFAULT_MAX_PAGES`` — an unbounded collection cannot be requested, so an
  upstream that returns an endless sequence of unique full pages still
  terminates deterministically with ``MAX_PAGES_BOUND`` (``truncated=True``);
* an empty page ends the scan;
* a short page (fewer items than ``limit``) ends the scan;
* a page whose content fingerprint repeats an earlier page ends the scan with
  ``DUPLICATE_PAGE`` and the repeated items are not re-emitted.

The returned receipt is the authoritative record of the requested offset/limit
window, the pages fetched, and the termination reason; ingestors must persist
it instead of reconstructing offsets from item counts.  Overlapping-but-
different pages are passed through; item-level deduplication happens naturally
downstream because catalog revisions are content-addressed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ..contracts import content_sha256
from .gamma_raw import decimalize_payload


DEFAULT_MAX_PAGES = 500


class PaginationTermination:
    """Terminal states of an offset pagination scan (plain string enum)."""

    EMPTY_PAGE = "EMPTY_PAGE"
    SHORT_PAGE = "SHORT_PAGE"
    DUPLICATE_PAGE = "DUPLICATE_PAGE"
    MAX_PAGES_BOUND = "MAX_PAGES_BOUND"


@dataclass(frozen=True)
class PaginationReceipt:
    requested_offset: int
    requested_limit: int
    effective_max_pages: int
    pages_fetched: int
    items_returned: int
    termination: str
    duplicate_page_offsets: tuple[int, ...]
    truncated: bool


@dataclass(frozen=True)
class PageCollection:
    items: tuple[dict[str, object], ...]
    receipt: PaginationReceipt


def collect_gamma_pages(
    fetch_page: Callable[[int], Sequence[Mapping[str, object]]],
    *,
    limit: int,
    max_pages: int | None = None,
    start_offset: int = 0,
) -> PageCollection:
    """Collect pages from ``fetch_page`` under a finite, fail-closed budget."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    if start_offset < 0:
        raise ValueError("start_offset must be non-negative")
    if max_pages is None:
        effective_max_pages = DEFAULT_MAX_PAGES
    elif max_pages <= 0:
        raise ValueError("max_pages must be positive when provided")
    else:
        effective_max_pages = max_pages

    collected: list[dict[str, object]] = []
    seen_fingerprints: set[str] = set()
    duplicate_offsets: list[int] = []
    offset = start_offset
    pages_fetched = 0
    termination: str | None = None

    while termination is None:
        if pages_fetched >= effective_max_pages:
            termination = PaginationTermination.MAX_PAGES_BOUND
            break
        raw_items = fetch_page(offset)
        items = [decimalize_payload(dict(item)) for item in raw_items]
        pages_fetched += 1
        if not items:
            termination = PaginationTermination.EMPTY_PAGE
            break
        fingerprint = content_sha256(items)
        if fingerprint in seen_fingerprints:
            duplicate_offsets.append(offset)
            termination = PaginationTermination.DUPLICATE_PAGE
            break
        seen_fingerprints.add(fingerprint)
        collected.extend(items)
        if len(items) < limit:
            termination = PaginationTermination.SHORT_PAGE
            break
        offset += limit

    assert termination is not None
    receipt = PaginationReceipt(
        requested_offset=start_offset,
        requested_limit=limit,
        effective_max_pages=effective_max_pages,
        pages_fetched=pages_fetched,
        items_returned=len(collected),
        termination=termination,
        duplicate_page_offsets=tuple(duplicate_offsets),
        truncated=termination == PaginationTermination.MAX_PAGES_BOUND,
    )
    return PageCollection(items=tuple(collected), receipt=receipt)
