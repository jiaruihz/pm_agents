"""Gamma market payload → canonical identity + snapshot fields (fail closed).

Normalization never invents values.  Required identity/content fields
(``id``, at least one event id, ``question``, ``rules``, two YES/NO tokens,
three lifecycle flags, the page observation clock) must be present and
parseable; any violation raises :class:`GammaNormalizationError` and the
caller records an error receipt while the raw payload artifact is retained.

Order independence (BF-P003-02): the complete deduplicated event-id set is
extracted sorted; the singular contract identity uses the deterministic
minimum id and the full set travels on the snapshot ``extensions`` so the
repository can project every event-market join.  Reordering the upstream
``events`` array therefore changes neither canonical identity nor hashes.

Dual-clock policy (R-025): ``source_observed_at`` is the observation clock of
the capture (supplied per page by the caller); the ingest clock is applied by
the catalog at save time.  Gamma's own ``updatedAt`` stays inside the raw
payload only and is never used to fake an observation clock.

``SUPERSEDED`` is never derived here — closed by the coordinator as
``SUPERSEDED_P0_POLICY=DEFERRED_UNREACHABLE`` (P0-03 review §5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..contracts import MarketIdentity, MarketStatus
from ..contracts.base import ensure_utc
from .gamma_identity import (
    GammaAdapterError,
    extract_condition_id,
    extract_event_ids,
    extract_market_id,
    map_yes_no_tokens,
    normalize_json_list,
)


# Top-level Gamma market fields this adapter understands.  Anything else is
# reported in a schema-drift receipt (the value is still preserved inside the
# raw payload artifact).
KNOWN_GAMMA_MARKET_FIELDS = frozenset(
    {
        "id",
        "marketId",
        "conditionId",
        "condition_id",
        "slug",
        "question",
        "title",
        "description",
        "rules",
        "category",
        "active",
        "closed",
        "resolved",
        "archived",
        "endDate",
        "endDateISO",
        "endDateIso",
        "endTime",
        "volume",
        "volumeNum",
        "liquidity",
        "liquidityNum",
        "liquidityCp",
        "outcomes",
        "outcomePrices",
        "clobTokenIds",
        "clob_token_ids",
        "clob_token_ids_json",
        "oneHourPriceChange",
        "spread",
        "bestBid",
        "bestAsk",
        "lastTradePrice",
        "updatedAt",
        "updated_at",
        "createdAt",
        "gameStartTime",
        "negRisk",
        "negRiskMarketID",
        "negRiskRequestID",
        "events",
        "icon",
        "image",
        "imageThumb",
        "thumbnail",
        "secondaryImages",
        "takerBaseFee",
        "makerBaseFee",
        "enabled",
        "pendingOrderbook",
        "acceptingOrders",
        "acceptingOrdersTimestamp",
        "closedTime",
        "umaResolutionStatus",
    }
)

_LIFECYCLE_FIELDS = ("active", "closed", "resolved")


class GammaNormalizationError(GammaAdapterError):
    """A market payload cannot be normalized; fail closed with a receipt."""

    error_code = "NORMALIZATION_FAILED"

    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(f"{error_code}: {detail}")
        self.reason_code = error_code
        self.detail = detail


@dataclass(frozen=True)
class NormalizedMarket:
    identity: MarketIdentity
    event_ids: tuple[str, ...]
    title: str
    question: str
    slug: str | None
    status: MarketStatus
    end_at: datetime | None
    rules_raw: str
    volume: Decimal | None
    liquidity: Decimal | None


def scan_unknown_fields(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return sorted top-level fields the adapter does not know (drift)."""

    return tuple(
        sorted(str(key) for key in payload if str(key) not in KNOWN_GAMMA_MARKET_FIELDS)
    )


def _parse_utc_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise GammaNormalizationError(
            "END_DATE_INVALID", f"cannot parse endDate value {value!r}"
        ) from None
    if parsed.tzinfo is None:
        raise GammaNormalizationError(
            "END_DATE_INVALID",
            f"endDate value {value!r} is timezone-naive; refusing to assume UTC",
        )
    return ensure_utc(parsed)


def _lifecycle_flag(payload: Mapping[str, Any], field: str) -> bool:
    value = payload.get(field)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise GammaNormalizationError(
        "LIFECYCLE_FIELD_INVALID",
        f"lifecycle field {field!r} must be a boolean, got {value!r}",
    )


def lifecycle_status(payload: Mapping[str, Any]) -> MarketStatus:
    """Map Gamma lifecycle flags to :class:`MarketStatus` (fail closed).

    Contradictory flag combinations (``active`` with ``closed``, or ``active``
    with ``resolved``) are rejected instead of silently applying precedence.
    ``SUPERSEDED`` is never produced here (see module docstring).
    """

    missing = [field for field in _LIFECYCLE_FIELDS if field not in payload]
    # The current public Gamma active-market payload omits ``resolved``.  For
    # an explicitly active, explicitly non-closed market, resolution is
    # necessarily false and can be derived without ambiguity.  Closed rows
    # still fail closed because Gamma exposes several resolution-status fields
    # whose semantics are not interchangeable.
    if missing == ["resolved"]:
        active = _lifecycle_flag(payload, "active")
        closed = _lifecycle_flag(payload, "closed")
        if active and not closed:
            return MarketStatus.ACTIVE
    if missing:
        raise GammaNormalizationError(
            "LIFECYCLE_FIELD_MISSING", f"missing lifecycle fields: {missing}"
        )
    active = _lifecycle_flag(payload, "active")
    closed = _lifecycle_flag(payload, "closed")
    resolved = _lifecycle_flag(payload, "resolved")
    if (active and closed) or (active and resolved):
        raise GammaNormalizationError(
            "LIFECYCLE_CONTRADICTORY",
            f"contradictory lifecycle flags: active={active} closed={closed} resolved={resolved}",
        )
    if resolved:
        return MarketStatus.RESOLVED
    if closed:
        return MarketStatus.CLOSED
    return MarketStatus.ACTIVE if active else MarketStatus.CLOSED


def _decimal_or_none(value: Any, field: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        raise GammaNormalizationError(
            "NUMERIC_FIELD_INVALID", f"cannot parse {field}={value!r} as Decimal"
        ) from None
    if not parsed.is_finite() or parsed < 0:
        raise GammaNormalizationError(
            "NUMERIC_FIELD_INVALID", f"{field}={value!r} must be a non-negative finite Decimal"
        )
    return parsed


def _required_text(payload: Mapping[str, Any], field: str, error_code: str) -> str:
    value = payload.get(field)
    text = "" if value is None else str(value).strip()
    if not text:
        raise GammaNormalizationError(error_code, f"{field} must be a non-blank string")
    return text


def normalize_market_payload(
    payload: Mapping[str, Any],
    *,
    source_observed_at: datetime,
) -> NormalizedMarket:
    ensure_utc(source_observed_at)

    market_id = extract_market_id(payload)
    if not market_id:
        raise GammaNormalizationError("MARKET_ID_MISSING", "payload has no market id")
    event_ids = extract_event_ids(payload)
    if not event_ids:
        raise GammaNormalizationError("EVENT_ID_MISSING", f"market {market_id} has no event id")
    condition_id = extract_condition_id(payload)
    try:
        yes_token, no_token = map_yes_no_tokens(
            payload.get("outcomes"), payload.get("clobTokenIds") or payload.get("clob_token_ids")
        )
    except GammaAdapterError as exc:
        raise GammaNormalizationError("TOKEN_MAPPING_INVALID", str(exc)) from None

    question = _required_text(payload, "question", "QUESTION_MISSING")
    # Gamma's current public market/event payload calls the binding text
    # ``description``; older captured fixtures used ``rules``.  Preserve both
    # spellings and prefer the explicit historical field when present.
    rules_text = payload.get("rules") or payload.get("description")
    if not isinstance(rules_text, str) or not rules_text.strip():
        raise GammaNormalizationError(
            "RULES_MISSING", "rules must be a non-blank string"
        )

    status = lifecycle_status(payload)
    end_at = _parse_utc_datetime(
        payload.get("endDate")
        or payload.get("endDateISO")
        or payload.get("endDateIso")
        or payload.get("endTime")
    )

    event_titles = sorted(
        str(event.get("title"))
        for event in normalize_json_list(payload.get("events"))
        if isinstance(event, Mapping) and event.get("title")
    )
    title = event_titles[0] if event_titles else question
    slug_value = payload.get("slug")
    slug = str(slug_value).strip() if isinstance(slug_value, str) and slug_value.strip() else None

    return NormalizedMarket(
        identity=MarketIdentity(
            event_id=event_ids[0],
            market_id=market_id,
            condition_id=condition_id,
            yes_token_id=yes_token,
            no_token_id=no_token,
        ),
        event_ids=event_ids,
        title=title,
        question=question,
        slug=slug,
        status=status,
        end_at=end_at,
        rules_raw=rules_text,
        volume=_decimal_or_none(payload.get("volume"), "volume"),
        liquidity=_decimal_or_none(payload.get("liquidity"), "liquidity"),
    )
