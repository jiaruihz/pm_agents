"""Canonical identity extraction and YES/NO token mapping (ADR-003).

Identity rules enforced here:

* ``market_id`` (Gamma ``id``/``marketId``) is the primary canonical id;
* event ids come from the payload's ``events`` array as a *complete deduped,
  order-independent* set (BF-P003-02): the singular contract identity uses the
  deterministic minimum id, never the response array order;
* ``condition_id`` is an optional alternate key, never a title/slug surrogate;
* YES/NO tokens are mapped by normalized outcome *label*, never by array
  index, so an outcome order reversal cannot swap the legs;
* single-market query responses must match a stated requested identity (F-05,
  BF-P003-03): entering the verified path without at least one non-blank
  expected identity value is rejected.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


class GammaAdapterError(ValueError):
    """Base class for fail-closed Gamma adapter violations."""

    error_code = "GAMMA_ADAPTER_ERROR"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class GammaTokenMappingError(GammaAdapterError):
    error_code = "TOKEN_MAPPING_INVALID"


class GammaIdentityMismatchError(GammaAdapterError):
    """A query response did not match the requested market identity (F-05)."""

    error_code = "IDENTITY_MISMATCH"


def _identifier_text(value: Any) -> str | None:
    """Render a Gamma identifier; reject shapes that would stringify garbage."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text if text else None
    # dicts/lists/floats would str() into non-canonical garbage — fail closed.
    return None


def normalize_json_list(value: Any) -> list[Any]:
    """Parse Gamma list fields that may be JSON-encoded strings."""

    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
        if isinstance(parsed, list):
            return parsed
        return [parsed] if parsed else []
    return []


def extract_market_id(payload: Mapping[str, Any]) -> str | None:
    return _identifier_text(payload.get("id")) or _identifier_text(
        payload.get("marketId")
    )


def extract_event_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return every distinct event id on the payload, order-independent."""

    ids: set[str] = set()
    for event in normalize_json_list(payload.get("events")):
        if not isinstance(event, Mapping):
            continue
        event_id = _identifier_text(event.get("id")) or _identifier_text(
            event.get("eventId")
        )
        if event_id:
            ids.add(event_id)
    return tuple(sorted(ids))


def extract_primary_event_id(payload: Mapping[str, Any]) -> str | None:
    event_ids = extract_event_ids(payload)
    return event_ids[0] if event_ids else None


def extract_condition_id(payload: Mapping[str, Any]) -> str | None:
    return _identifier_text(payload.get("conditionId")) or _identifier_text(
        payload.get("condition_id")
    )


def map_yes_no_tokens(outcomes: Any, clob_token_ids: Any) -> tuple[str, str]:
    """Return ``(yes_token_id, no_token_id)`` mapped by outcome label.

    The token arrays are zipped with the outcome labels, so an outcome order
    reversal swaps the labels together with the tokens instead of silently
    swapping YES and NO.  Anything that is not exactly a two-element YES/NO
    market with string/int token ids fails closed.
    """

    labels = [str(label).strip().casefold() for label in normalize_json_list(outcomes)]
    raw_tokens = normalize_json_list(clob_token_ids)
    tokens = [_identifier_text(token) for token in raw_tokens]
    if len(labels) != 2 or len(tokens) != 2:
        raise GammaTokenMappingError(
            f"expected two outcomes and two tokens, got outcomes={labels!r} tokens={raw_tokens!r}"
        )
    if sorted(labels) != ["no", "yes"]:
        raise GammaTokenMappingError(f"outcome labels must be YES/NO, got {labels!r}")
    if labels[0] == labels[1]:
        raise GammaTokenMappingError("outcome labels must be distinct")
    yes_token = tokens[0] if labels[0] == "yes" else tokens[1]
    no_token = tokens[1] if labels[0] == "yes" else tokens[0]
    if not yes_token or not no_token:
        raise GammaTokenMappingError(
            f"token ids must be non-blank str/int, got {raw_tokens!r}"
        )
    if yes_token == no_token:
        raise GammaTokenMappingError("YES and NO token ids must be distinct")
    return yes_token, no_token


def _expected_value(name: str, value: str | None) -> str:
    if value is None or not str(value).strip():
        raise GammaIdentityMismatchError(
            f"expected {name} must be a non-blank value; refusing to verify without a stated identity"
        )
    return str(value).strip()


def verify_market_response_identity(
    payload: Mapping[str, Any] | None,
    *,
    expected_market_id: str | None = None,
    expected_slug: str | None = None,
    expected_condition_id: str | None = None,
) -> Mapping[str, Any] | None:
    """Verify a single-market query response against the request (F-05).

    Gamma's ``fetch_market_by_id_or_slug`` style endpoints may return the
    first element of an unfiltered list; this guard rejects a response whose
    identity does not match the request before the catalog can ingest it.  At
    least one expected identity must be stated and non-blank (BF-P003-03) —
    the "verified" path must not be enterable anonymously.  Title is never
    accepted as an identity match.  ``None`` payload means the query returned
    nothing, which callers translate into a skip receipt.
    """

    if (
        expected_market_id is None
        and expected_slug is None
        and expected_condition_id is None
    ):
        raise GammaIdentityMismatchError(
            "verify_market_response_identity requires at least one expected identity value"
        )

    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise GammaIdentityMismatchError("market response payload must be a mapping")

    if expected_market_id is not None:
        wanted = _expected_value("market_id", expected_market_id)
        actual = extract_market_id(payload)
        if actual is None or actual != wanted:
            raise GammaIdentityMismatchError(
                f"expected market_id={wanted!r}, got {actual!r}"
            )
    if expected_condition_id is not None:
        wanted = _expected_value("condition_id", expected_condition_id)
        actual = extract_condition_id(payload)
        if actual is None or actual != wanted:
            raise GammaIdentityMismatchError(
                f"expected condition_id={wanted!r}, got {actual!r}"
            )
    if expected_slug is not None:
        wanted = _expected_value("slug", expected_slug)
        slug_value = payload.get("slug")
        actual = (
            str(slug_value).strip()
            if isinstance(slug_value, str) and slug_value.strip()
            else None
        )
        if actual is None or actual != wanted:
            raise GammaIdentityMismatchError(
                f"expected slug={wanted!r}, got {actual!r}"
            )
    return payload
