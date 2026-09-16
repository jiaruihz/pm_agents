"""Specialist wallet recall provider (P0-06D).

Pure, offline transformation of caller-supplied frozen wallet facts into
deterministic ``SPECIALIST_WALLET`` :class:`RecallHit` records.  The provider
never opens a wallet store, performs HTTP I/O, updates a tracker, or triggers
copy-trade behavior; every input (facts, source identity, pagination receipt,
observation clock, address/entity mapping) is supplied by the caller.

Privacy contract
----------------

Trade direction is private.  ``side``, ``position_direction``, ``notional_usdc``
and ``size`` may only influence the private provider input fingerprint.  The
traded ``token_id`` is treated as direction-bearing as well (binary-market legs
encode YES/NO), so it never reaches public features either.  Public hits carry
an allowlist of wallet-identity features only, and every public projection is
re-scanned by :func:`wallet_public_leak_reasons` before emission; any hit whose
public payload would leak direction is rejected fail-closed.

Identity contract
-----------------

An address is never an entity.  Entity attribution exists only per address and
only with explicit provenance plus confidence at/above the configured minimum.
Addresses are never merged: two addresses mapping to one entity remain two
distinct public addresses with two distinct attribution records.

Fail-closed contract
--------------------

Source identity mismatch, pagination truncation/incompleteness, missing window
coverage, unmapped or ambiguous token/market mapping, ambiguous aliases,
below-threshold alias confidence, unsafe alias text and facts observed after
the as-of clock all reject the affected facts (or the whole batch) with an
explicit machine-readable reason; none of them silently downgrades.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Iterable

from pydantic import Field, field_validator, model_validator

from ..contracts import (
    AlphaContract,
    ProvenanceRef,
    RecallHit,
    RecallerType,
    canonical_json,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256
from .registry import ProviderDescriptor


WALLET_REASON_ACTIVITY = "SPECIALIST_WALLET_ACTIVITY"
WALLET_REASON_ENTITY_ACTIVITY = "SPECIALIST_WALLET_ENTITY_ACTIVITY"
WALLET_REASON_CODES = frozenset({WALLET_REASON_ACTIVITY, WALLET_REASON_ENTITY_ACTIVITY})

PROVIDER_VERSION = "wallet-p0-06d-v1"

_ETHEREUM_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
_BOUNDED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

# Fact fields that are private forever: they may shape only the private
# provider input fingerprint, never a public feature, reason or record id.
PRIVATE_FACT_FIELDS = frozenset({"side", "position_direction", "notional_usdc", "size"})
# The traded token id is direction-bearing on binary markets (YES/NO legs) and
# is excluded from every public view alongside the private fact fields.
PUBLIC_FACT_EXCLUDED_FIELDS = PRIVATE_FACT_FIELDS | {"token_id"}

# Key fragments that must never appear in wallet-derived public payloads.  The
# traded token id is direction-bearing on binary markets, hence "token".
_PUBLIC_BANNED_KEY_FRAGMENTS = (
    "side",
    "direction",
    "position",
    "notional",
    "size",
    "buy",
    "sell",
    "bought",
    "sold",
    "long",
    "short",
    "bid",
    "ask",
    "usdc",
    "amount",
    "price",
    "prob",
    "fair",
    "edge",
    "pnl",
    "token",
)
# Full-token direction words.  Kept exact-match only ("yes"/"no" as fragments
# would false-positive on ordinary prose such as "monotonic" or "knowledge").
_DIRECTION_VALUE_TOKENS = frozenset(
    {"BUY", "SELL", "BOUGHT", "SOLD", "LONG", "SHORT", "YES", "NO", "BID", "ASK"}
)


class WalletRecallRejectionReason(StrEnum):
    SOURCE_IDENTITY_MISMATCH = "SOURCE_IDENTITY_MISMATCH"
    PAGINATION_TRUNCATED = "PAGINATION_TRUNCATED"
    PAGINATION_INCOMPLETE = "PAGINATION_INCOMPLETE"
    WINDOW_COVERAGE_MISSING = "WINDOW_COVERAGE_MISSING"
    FACT_OBSERVED_AFTER_AS_OF = "FACT_OBSERVED_AFTER_AS_OF"
    FACT_OUTSIDE_SNAPSHOT_WINDOW = "FACT_OUTSIDE_SNAPSHOT_WINDOW"
    TOKEN_MARKET_UNMAPPED = "TOKEN_MARKET_UNMAPPED"
    AMBIGUOUS_TOKEN_MARKET = "AMBIGUOUS_TOKEN_MARKET"
    AMBIGUOUS_ADDRESS_ALIAS = "AMBIGUOUS_ADDRESS_ALIAS"
    ALIAS_CONFIDENCE_BELOW_THRESHOLD = "ALIAS_CONFIDENCE_BELOW_THRESHOLD"
    ALIAS_PUBLIC_TEXT_UNSAFE = "ALIAS_PUBLIC_TEXT_UNSAFE"
    PUBLIC_PAYLOAD_UNSAFE = "PUBLIC_PAYLOAD_UNSAFE"


def wallet_public_leak_reasons(payload: Any) -> tuple[str, ...]:
    """Return leak descriptions for direction-bearing data in a public payload.

    Recursively scans keys and string values: any banned key fragment, any
    fragment inside a string value, or any exact direction token as a value is
    reported.  An empty result means the payload may be published to Blind
    research.
    """

    found: list[str] = []

    def walk(item: Any, path: str) -> None:
        if isinstance(item, str):
            lowered = item.lower()
            for fragment in _PUBLIC_BANNED_KEY_FRAGMENTS:
                if fragment in lowered:
                    found.append(f"{path}:fragment:{fragment}")
            if item.strip().upper() in _DIRECTION_VALUE_TOKENS:
                found.append(f"{path}:direction_token")
        elif isinstance(item, dict):
            for key, value in item.items():
                key_path = f"{path}.{key}"
                if isinstance(key, str):
                    lowered_key = key.lower()
                    for fragment in _PUBLIC_BANNED_KEY_FRAGMENTS:
                        if fragment in lowered_key:
                            found.append(f"{key_path}:key_fragment:{fragment}")
                walk(value, key_path)
        elif isinstance(item, (list, tuple)):
            for index, value in enumerate(item):
                walk(value, f"{path}[{index}]")

    walk(payload, "$")
    return tuple(found)


class WalletSourceIdentity(AlphaContract):
    """Caller-asserted identity of one frozen wallet source snapshot."""

    source_id: str
    snapshot_id: str
    snapshot_sha256: str | None = None
    captured_at: datetime

    @field_validator("source_id", "snapshot_id")
    @classmethod
    def bounded_id(cls, value: str) -> str:
        if not _BOUNDED_ID_RE.fullmatch(value):
            raise ValueError("value must be a bounded identifier")
        return value

    @field_validator("snapshot_sha256")
    @classmethod
    def sha_is_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("captured_at")
    @classmethod
    def captured_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class WalletPaginationReceipt(AlphaContract):
    """Completeness receipt for one paginated wallet snapshot read."""

    pages_expected: int = Field(ge=1)
    pages_received: int = Field(ge=0)
    truncated: bool
    window_start: datetime
    window_end: datetime

    @field_validator("window_start", "window_end")
    @classmethod
    def window_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def window_is_ordered(self) -> "WalletPaginationReceipt":
        if self.window_end < self.window_start:
            raise ValueError("window_end must not precede window_start")
        return self


class WalletFact(AlphaContract):
    """One frozen wallet activity fact supplied by the caller.

    ``side``, ``position_direction``, ``notional_usdc`` and ``size`` are
    private direction/position fields and never reach public output.  Addresses
    are normalized lowercase EVM addresses.
    """

    fact_id: str
    address: str
    token_id: str
    observed_at: datetime
    side: str | None = None
    position_direction: str | None = None
    notional_usdc: Decimal | None = Field(default=None, ge=0)
    size: Decimal | None = Field(default=None, ge=0)

    @field_validator("fact_id", "token_id")
    @classmethod
    def bounded_id(cls, value: str) -> str:
        if not _BOUNDED_ID_RE.fullmatch(value):
            raise ValueError("value must be a bounded identifier")
        return value

    @field_validator("address")
    @classmethod
    def address_is_normalized(cls, value: str) -> str:
        value = value.strip().lower()
        if not _ETHEREUM_ADDRESS_RE.fullmatch(value):
            raise ValueError("address must be a lowercase 0x-prefixed 20-byte hex value")
        return value

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("side", "position_direction")
    @classmethod
    def private_text_is_bounded(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value or len(value) > 32:
            raise ValueError("private direction text must be non-blank and bounded")
        return value


class WalletAddressAlias(AlphaContract):
    """One address-to-entity attribution with explicit provenance.

    An address alias is the only way an address may reference an entity; the
    entity namespace is deliberately disjoint from the address namespace.
    """

    address: str
    entity_id: str
    provenance: str
    confidence: Decimal = Field(ge=0, le=1)

    @field_validator("address")
    @classmethod
    def address_is_normalized(cls, value: str) -> str:
        value = value.strip().lower()
        if not _ETHEREUM_ADDRESS_RE.fullmatch(value):
            raise ValueError("address must be a lowercase 0x-prefixed 20-byte hex value")
        return value

    @field_validator("entity_id", "provenance")
    @classmethod
    def attribution_text_is_bounded(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 128:
            raise ValueError("value must be non-blank and bounded")
        return value

    @field_validator("entity_id")
    @classmethod
    def entity_is_not_an_address(cls, value: str) -> str:
        if _ETHEREUM_ADDRESS_RE.fullmatch(value.lower()):
            raise ValueError("entity_id must not be an address; Address != Entity")
        return value


class WalletTokenMarketMapping(AlphaContract):
    """Caller-supplied token-to-market resolution used to place facts."""

    token_id: str
    market_id: str

    @field_validator("token_id", "market_id")
    @classmethod
    def bounded_id(cls, value: str) -> str:
        if not _BOUNDED_ID_RE.fullmatch(value):
            raise ValueError("value must be a bounded identifier")
        return value


class WalletObservationClock(AlphaContract):
    """Caller-supplied deterministic observation clock (no wall time)."""

    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class WalletRecallRequest(AlphaContract):
    """One pure recall request over a frozen wallet snapshot."""

    run_id: str
    clock: WalletObservationClock
    source: WalletSourceIdentity
    pagination: WalletPaginationReceipt
    facts: tuple[WalletFact, ...] = ()
    aliases: tuple[WalletAddressAlias, ...] = ()
    token_markets: tuple[WalletTokenMarketMapping, ...] = ()

    @field_validator("run_id")
    @classmethod
    def run_id_is_bounded(cls, value: str) -> str:
        if not _BOUNDED_ID_RE.fullmatch(value):
            raise ValueError("run_id must be a bounded identifier")
        return value

    @model_validator(mode="after")
    def fact_ids_are_unique(self) -> "WalletRecallRequest":
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("fact_ids must be unique within one request")
        return self


class WalletRecallConfig(AlphaContract):
    """Configurable freshness, attribution and scoring policy."""

    provider_id: str = "specialist_wallet"
    provider_version: str = PROVIDER_VERSION
    expected_source_id: str = "specialist_wallet_store"
    max_source_age_seconds: int = Field(default=21600, ge=0)
    alias_min_confidence: Decimal = Field(default=Decimal("0.8"), ge=0, le=1)
    hit_validity_seconds: int = Field(default=86400, ge=1)
    hit_raw_score: Decimal = Field(default=Decimal("0.5"), ge=0)

    @field_validator("provider_id", "provider_version", "expected_source_id")
    @classmethod
    def bounded_text(cls, value: str) -> str:
        if not _BOUNDED_ID_RE.fullmatch(value):
            raise ValueError("value must be a bounded identifier")
        return value


class WalletFactRejection(AlphaContract):
    fact_id: str
    reason: WalletRecallRejectionReason

    @field_validator("fact_id")
    @classmethod
    def bounded_id(cls, value: str) -> str:
        if not _BOUNDED_ID_RE.fullmatch(value):
            raise ValueError("value must be a bounded identifier")
        return value


class WalletRecallOutcome(AlphaContract):
    """Deterministic result of one wallet recall pass.

    ``hits`` are current RecallHits; ``historical_hits`` carry
    ``historical_only=True`` and are rejected downstream by the aggregator, so
    stale facts can never form current Recall.  Fingerprints cover source
    inputs plus config only — never the run envelope — so cross-run processing
    retains the same semantic fingerprints.  RecallHit ids remain attempt
    scoped so a new run cannot collide with different canonical envelope bytes
    in append-only storage; P0-06A deduplicates their business semantics.
    """

    provider_id: str
    provider_version: str
    as_of: datetime
    hits: tuple[RecallHit, ...] = ()
    historical_hits: tuple[RecallHit, ...] = ()
    rejections: tuple[WalletFactRejection, ...] = ()
    public_input_sha256: str
    private_input_sha256: str


class _AliasState:
    __slots__ = ("alias", "rejection")

    def __init__(
        self,
        alias: WalletAddressAlias | None,
        rejection: WalletRecallRejectionReason | None,
    ) -> None:
        self.alias = alias
        self.rejection = rejection


_NO_ALIAS = _AliasState(alias=None, rejection=None)


def _sorted_canonical_items(items: Iterable[Any]) -> list[str]:
    """Order-free canonical item list: canonical JSON bytes, sorted."""

    return sorted(canonical_json(item) for item in items)


class WalletRecallProvider:
    """Pure specialist-wallet recall provider (offline, deterministic)."""

    def __init__(self, config: WalletRecallConfig | None = None) -> None:
        self.config = config or WalletRecallConfig()

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self.config.provider_id,
            recaller=RecallerType.SPECIALIST_WALLET,
            recaller_version=self.config.provider_version,
        )

    def recall(self, request: WalletRecallRequest) -> WalletRecallOutcome:
        config = self.config
        fresh: dict[str, list[WalletFact]] = {}
        historical: dict[str, list[WalletFact]] = {}
        rejections: list[WalletFactRejection] = []
        alias_states = self._alias_states(request.aliases)

        batch_reason = self._batch_rejection(request)
        if batch_reason is not None:
            rejections.extend(
                WalletFactRejection(fact_id=fact.fact_id, reason=batch_reason)
                for fact in request.facts
            )
        else:
            token_markets = self._token_market_index(request.token_markets)
            max_age = timedelta(seconds=config.max_source_age_seconds)
            for fact in request.facts:
                reason = self._fact_rejection(fact, token_markets, alias_states, request)
                if reason is not None:
                    rejections.append(WalletFactRejection(fact_id=fact.fact_id, reason=reason))
                    continue
                market_id = token_markets[fact.token_id][0]
                stale = (request.clock.as_of - fact.observed_at) > max_age
                bucket = historical if stale else fresh
                bucket.setdefault(market_id, []).append(fact)

        hits: list[RecallHit] = []
        for market_id in sorted(set(fresh) | set(historical)):
            for bucket, historical_only in ((fresh, False), (historical, True)):
                facts = bucket.get(market_id, ())
                if not facts:
                    continue
                hit = self._build_hit(request, market_id, facts, alias_states, historical_only)
                if hit is None:
                    rejections.extend(
                        WalletFactRejection(
                            fact_id=fact.fact_id,
                            reason=WalletRecallRejectionReason.PUBLIC_PAYLOAD_UNSAFE,
                        )
                        for fact in facts
                    )
                else:
                    hits.append(hit)

        hits.sort(key=lambda hit: (hit.historical_only, hit.market_id))
        current = tuple(hit for hit in hits if not hit.historical_only)
        historical_hits = tuple(hit for hit in hits if hit.historical_only)
        rejections.sort(key=lambda item: (item.fact_id, item.reason))
        public_view, private_view = self._input_views(request)
        return WalletRecallOutcome(
            provider_id=config.provider_id,
            provider_version=config.provider_version,
            as_of=request.clock.as_of,
            hits=current,
            historical_hits=historical_hits,
            rejections=tuple(rejections),
            public_input_sha256=content_sha256(public_view),
            private_input_sha256=content_sha256(private_view),
        )

    def _batch_rejection(self, request: WalletRecallRequest) -> WalletRecallRejectionReason | None:
        config = self.config
        if request.source.source_id != config.expected_source_id:
            return WalletRecallRejectionReason.SOURCE_IDENTITY_MISMATCH
        pagination = request.pagination
        if pagination.truncated:
            return WalletRecallRejectionReason.PAGINATION_TRUNCATED
        if pagination.pages_received != pagination.pages_expected:
            return WalletRecallRejectionReason.PAGINATION_INCOMPLETE
        max_age = timedelta(seconds=config.max_source_age_seconds)
        as_of = request.clock.as_of
        if pagination.window_start > as_of - max_age or pagination.window_end < as_of:
            return WalletRecallRejectionReason.WINDOW_COVERAGE_MISSING
        return None

    def _token_market_index(
        self, mappings: tuple[WalletTokenMarketMapping, ...]
    ) -> dict[str, tuple[str, ...]]:
        index: dict[str, set[str]] = {}
        for mapping in mappings:
            index.setdefault(mapping.token_id, set()).add(mapping.market_id)
        return {token: tuple(sorted(markets)) for token, markets in index.items()}

    def _alias_states(
        self, aliases: tuple[WalletAddressAlias, ...]
    ) -> dict[str, _AliasState]:
        grouped: dict[str, list[WalletAddressAlias]] = {}
        for alias in aliases:
            grouped.setdefault(alias.address, []).append(alias)
        states: dict[str, _AliasState] = {}
        for address, group in grouped.items():
            distinct = {canonical_json(alias): alias for alias in group}
            if len(distinct) > 1:
                states[address] = _AliasState(None, WalletRecallRejectionReason.AMBIGUOUS_ADDRESS_ALIAS)
                continue
            alias = next(iter(distinct.values()))
            if wallet_public_leak_reasons(
                {"entity_id": alias.entity_id, "provenance": alias.provenance}
            ):
                states[address] = _AliasState(None, WalletRecallRejectionReason.ALIAS_PUBLIC_TEXT_UNSAFE)
            elif alias.confidence < self.config.alias_min_confidence:
                states[address] = _AliasState(
                    None, WalletRecallRejectionReason.ALIAS_CONFIDENCE_BELOW_THRESHOLD
                )
            else:
                states[address] = _AliasState(alias, None)
        return states

    def _fact_rejection(
        self,
        fact: WalletFact,
        token_markets: dict[str, tuple[str, ...]],
        alias_states: dict[str, _AliasState],
        request: WalletRecallRequest,
    ) -> WalletRecallRejectionReason | None:
        markets = token_markets.get(fact.token_id)
        if markets is None:
            return WalletRecallRejectionReason.TOKEN_MARKET_UNMAPPED
        if len(markets) > 1:
            return WalletRecallRejectionReason.AMBIGUOUS_TOKEN_MARKET
        alias_state = alias_states.get(fact.address, _NO_ALIAS)
        if alias_state.rejection is not None:
            return alias_state.rejection
        if fact.observed_at > request.clock.as_of:
            return WalletRecallRejectionReason.FACT_OBSERVED_AFTER_AS_OF
        window = request.pagination
        if fact.observed_at < window.window_start or fact.observed_at > window.window_end:
            return WalletRecallRejectionReason.FACT_OUTSIDE_SNAPSHOT_WINDOW
        return None

    def _build_hit(
        self,
        request: WalletRecallRequest,
        market_id: str,
        facts: tuple[WalletFact, ...] | list[WalletFact],
        alias_states: dict[str, _AliasState],
        historical_only: bool,
    ) -> RecallHit | None:
        config = self.config
        addresses = tuple(sorted({fact.address for fact in facts}))
        attributions = tuple(
            sorted(
                {
                    state.alias
                    for state in (
                        alias_states.get(fact.address, _NO_ALIAS) for fact in facts
                    )
                    if state.alias is not None
                },
                key=canonical_json,
            )
        )
        reason_codes = (
            (WALLET_REASON_ACTIVITY, WALLET_REASON_ENTITY_ACTIVITY)
            if attributions
            else (WALLET_REASON_ACTIVITY,)
        )
        features: dict[str, Any] = {
            "specialist_wallet_addresses": addresses,
            "specialist_wallet_address_count": len(addresses),
            "specialist_entity_attributions": attributions,
            "freshness_max_age_seconds": config.max_source_age_seconds,
        }
        if wallet_public_leak_reasons({"features": features, "reason_codes": reason_codes}):
            return None

        observed_at = max(fact.observed_at for fact in facts)
        valid_until = request.clock.as_of + timedelta(seconds=config.hit_validity_seconds)
        record_identity = {
            "provider_id": config.provider_id,
            "provider_version": config.provider_version,
            "run_id": request.run_id,
            "source": (request.source.source_id, request.source.snapshot_id),
            "market_id": market_id,
            "features_sha256": content_sha256(features),
            "reason_codes": tuple(sorted(reason_codes)),
            "raw_score": config.hit_raw_score,
            "observed_at": observed_at,
            "valid_until": valid_until,
            "historical_only": historical_only,
        }
        return RecallHit(
            record_id=stable_record_id("recall_hit", record_identity),
            run_id=request.run_id,
            created_at=request.clock.as_of,
            source=config.provider_id,
            source_version=config.provider_version,
            provenance=(
                ProvenanceRef(
                    source_artifact_id=request.source.snapshot_id,
                    relation="wallet_source_snapshot",
                    content_sha256=request.source.snapshot_sha256,
                    source_observed_at=request.source.captured_at,
                ),
            ),
            extensions={},
            market_id=market_id,
            recaller=RecallerType.SPECIALIST_WALLET,
            recaller_version=config.provider_version,
            reason_codes=reason_codes,
            features=features,
            raw_score=config.hit_raw_score,
            observed_at=observed_at,
            valid_until=valid_until,
            historical_only=historical_only,
        )

    def _input_views(self, request: WalletRecallRequest) -> tuple[dict[str, Any], dict[str, Any]]:
        config = self.config
        provider_view = {
            "provider_id": config.provider_id,
            "provider_version": config.provider_version,
            "expected_source_id": config.expected_source_id,
            "max_source_age_seconds": config.max_source_age_seconds,
            "alias_min_confidence": config.alias_min_confidence,
            "hit_raw_score": config.hit_raw_score,
            "hit_validity_seconds": config.hit_validity_seconds,
        }
        public_facts = sorted(
            canonical_json(
                {
                    key: value
                    for key, value in fact.model_dump(mode="python").items()
                    if key not in PUBLIC_FACT_EXCLUDED_FIELDS
                }
            )
            for fact in request.facts
        )
        public_view = {
            "provider": provider_view,
            "as_of": request.clock.as_of,
            "source": request.source,
            "pagination": request.pagination,
            "aliases": _sorted_canonical_items(request.aliases),
            "markets": tuple(
                sorted({mapping.market_id for mapping in request.token_markets})
            ),
            "facts": public_facts,
        }
        private_view = {
            "provider": provider_view,
            "as_of": request.clock.as_of,
            "source": request.source,
            "pagination": request.pagination,
            "aliases": _sorted_canonical_items(request.aliases),
            "token_markets": _sorted_canonical_items(request.token_markets),
            "facts": _sorted_canonical_items(request.facts),
        }
        return public_view, private_view
