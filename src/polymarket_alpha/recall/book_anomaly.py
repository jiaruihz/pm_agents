"""Offline-only structural order-book anomaly recall (P0-06E).

The provider consumes already-normalized, paired SENSING snapshots.  It never
declares capture demand, opens a connection, reads a database, or estimates a
fair probability.  Its reasons describe only observable market structure.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from pydantic import Field, field_validator, model_validator

from ..contracts import (
    AlphaContract,
    OrderbookSnapshot,
    ProvenanceRef,
    RecallHit,
    RecallerType,
    TargetDepthMetrics,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc
from ..contracts.base import validate_sha256


BOOK_ANOMALY_RECALLER_VERSION = "p0_06e_v1"
BOOK_ANOMALY_PROVIDER_ID = "book_anomaly"


class BookAnomalyRouteStatus(StrEnum):
    EMITTED = "EMITTED"
    SKIPPED = "SKIPPED"
    NO_ANOMALY = "NO_ANOMALY"


class BookAnomalySkipReason(StrEnum):
    NO_BOOK = "NO_BOOK"
    STALE_BOOK = "STALE_BOOK"
    ONE_SIDED_BOOK = "ONE_SIDED_BOOK"
    INSUFFICIENT_DEPTH = "INSUFFICIENT_DEPTH"
    FUTURE_BOOK = "FUTURE_BOOK"
    CROSSED_BOOK = "CROSSED_BOOK"


class FamilyThresholdRelation(AlphaContract):
    """Explicit, caller-supplied ordering relation for two threshold markets.

    The relation contains snapshot ids *and* hashes.  The provider verifies
    those references against the request before applying any monotonic rule,
    so a family label cannot silently join stale or substituted book lineage.
    """

    relation_id: str
    family_id: str
    lower_market_id: str
    lower_snapshot_id: str
    lower_snapshot_sha256: str
    higher_market_id: str
    higher_snapshot_id: str
    higher_snapshot_sha256: str

    @field_validator(
        "relation_id",
        "family_id",
        "lower_market_id",
        "lower_snapshot_id",
        "higher_market_id",
        "higher_snapshot_id",
    )
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("family relation identity cannot be blank")
        return value

    @field_validator("lower_snapshot_sha256", "higher_snapshot_sha256")
    @classmethod
    def hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def markets_are_distinct(self) -> "FamilyThresholdRelation":
        if self.lower_market_id == self.higher_market_id:
            raise ValueError("threshold relation requires distinct markets")
        return self


class BookAnomalyRecallConfig(AlphaContract):
    provider_id: str = BOOK_ANOMALY_PROVIDER_ID
    provider_version: str = BOOK_ANOMALY_RECALLER_VERSION
    max_book_age_seconds: int = Field(default=120, ge=0)
    required_target_size: Decimal = Field(default=Decimal("10"), gt=0)
    max_spread: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    min_visible_depth: Decimal = Field(default=Decimal("10"), gt=0)
    visible_levels: int = Field(default=2, ge=1, le=100)
    paired_mid_sum_tolerance: Decimal = Field(default=Decimal("0.08"), ge=0, le=1)
    family_monotonic_tolerance: Decimal = Field(default=Decimal("0.02"), ge=0, le=1)
    hit_validity_seconds: int = Field(default=900, ge=1)
    hit_raw_score: Decimal = Field(default=Decimal("0.5"), ge=0)

    @field_validator("provider_id", "provider_version")
    @classmethod
    def provider_text_is_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("provider identity cannot be blank")
        return value


class BookAnomalyRecallRequest(AlphaContract):
    """Frozen provider input; no implicit clock or upstream lookup exists."""

    run_id: str
    as_of: datetime
    snapshots: tuple[OrderbookSnapshot, ...] = ()
    family_relations: tuple[FamilyThresholdRelation, ...] = ()

    @field_validator("run_id")
    @classmethod
    def run_id_is_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("run_id cannot be blank")
        return value

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def snapshot_lineage_is_unambiguous(self) -> "BookAnomalyRecallRequest":
        ids: dict[str, str] = {}
        markets: dict[str, tuple[str, str, str]] = {}
        for snapshot in self.snapshots:
            existing_hash = ids.setdefault(snapshot.record_id, snapshot.canonical_sha256)
            if existing_hash != snapshot.canonical_sha256:
                raise ValueError("snapshot record id has conflicting canonical lineage")
            mapping = (
                snapshot.identity.yes_token_id,
                snapshot.identity.no_token_id,
                snapshot.record_id,
            )
            existing_mapping = markets.setdefault(snapshot.identity.market_id, mapping)
            if existing_mapping != mapping:
                raise ValueError("market has conflicting token or snapshot lineage")
        relation_ids = [item.relation_id for item in self.family_relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("family relation ids must be unique")
        for relation in self.family_relations:
            lower_hash = ids.get(relation.lower_snapshot_id)
            higher_hash = ids.get(relation.higher_snapshot_id)
            lower_mapping = markets.get(relation.lower_market_id)
            higher_mapping = markets.get(relation.higher_market_id)
            if (
                lower_hash != relation.lower_snapshot_sha256
                or higher_hash != relation.higher_snapshot_sha256
                or lower_mapping is None
                or higher_mapping is None
                or lower_mapping[2] != relation.lower_snapshot_id
                or higher_mapping[2] != relation.higher_snapshot_id
            ):
                raise ValueError("family relation snapshot lineage conflicts with request")
        return self


class BookAnomalyRouteResult(AlphaContract):
    market_id: str | None = None
    snapshot_id: str | None = None
    status: BookAnomalyRouteStatus
    skip_reason: BookAnomalySkipReason | None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def route_state_is_consistent(self) -> "BookAnomalyRouteResult":
        if self.status == BookAnomalyRouteStatus.SKIPPED:
            if self.skip_reason is None or self.reason_codes:
                raise ValueError("skipped route requires exactly one typed skip reason")
        elif self.skip_reason is not None:
            raise ValueError("non-skipped route cannot contain a skip reason")
        if self.status == BookAnomalyRouteStatus.EMITTED and not self.reason_codes:
            raise ValueError("emitted route requires structural reasons")
        if self.status == BookAnomalyRouteStatus.NO_ANOMALY and self.reason_codes:
            raise ValueError("no-anomaly route cannot contain reasons")
        return self


class BookAnomalyRecallOutcome(AlphaContract):
    provider_id: str
    provider_version: str
    as_of: datetime
    input_sha256: str
    hits: tuple[RecallHit, ...] = ()
    route_results: tuple[BookAnomalyRouteResult, ...] = ()

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def emitted_hits_match_provider(self) -> "BookAnomalyRecallOutcome":
        allowed = {
            "YES_SPREAD_WIDE",
            "NO_SPREAD_WIDE",
            "VISIBLE_DEPTH_SHALLOW",
            "PAIRED_MID_SUM_INCONSISTENT",
            "FAMILY_THRESHOLD_MONOTONICITY_INCONSISTENT",
        }
        for hit in self.hits:
            if (
                hit.source != self.provider_id
                or hit.source_version != self.provider_version
                or hit.recaller != RecallerType.BOOK_ANOMALY
                or hit.recaller_version != self.provider_version
                or not hit.reason_codes
                or not set(hit.reason_codes) <= allowed
            ):
                raise ValueError("book anomaly hit violates the provider output contract")
        return self


def _best_prices(snapshot: OrderbookSnapshot) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    return (
        snapshot.yes_leg.bids[0].price,
        snapshot.yes_leg.asks[0].price,
        snapshot.no_leg.bids[0].price,
        snapshot.no_leg.asks[0].price,
    )


def _target_is_sufficient(metrics: Iterable[TargetDepthMetrics], target: Decimal) -> bool:
    matching = [item for item in metrics if item.target_size == target]
    return bool(matching) and all(
        not item.buy_insufficient_depth and not item.sell_insufficient_depth for item in matching
    )


def _visible_depth(snapshot: OrderbookSnapshot, levels: int) -> dict[str, Decimal]:
    return {
        "yes_bid": sum((item.size for item in snapshot.yes_leg.bids[:levels]), Decimal("0")),
        "yes_ask": sum((item.size for item in snapshot.yes_leg.asks[:levels]), Decimal("0")),
        "no_bid": sum((item.size for item in snapshot.no_leg.bids[:levels]), Decimal("0")),
        "no_ask": sum((item.size for item in snapshot.no_leg.asks[:levels]), Decimal("0")),
    }


class BookAnomalyRecallProvider:
    """Pure optional book route; skipped routes never prevent other providers."""

    def __init__(self, config: BookAnomalyRecallConfig | None = None) -> None:
        self.config = config or BookAnomalyRecallConfig()

    def recall(self, request: BookAnomalyRecallRequest) -> BookAnomalyRecallOutcome:
        input_sha256 = content_sha256(
            {
                "as_of": request.as_of,
                "snapshots": request.snapshots,
                "family_relations": request.family_relations,
                "config": self.config,
            }
        )
        if not request.snapshots:
            return BookAnomalyRecallOutcome(
                provider_id=self.config.provider_id,
                provider_version=self.config.provider_version,
                as_of=request.as_of,
                input_sha256=input_sha256,
                route_results=(
                    BookAnomalyRouteResult(
                        status=BookAnomalyRouteStatus.SKIPPED,
                        skip_reason=BookAnomalySkipReason.NO_BOOK,
                    ),
                ),
            )

        usable: dict[str, OrderbookSnapshot] = {}
        route_reasons: dict[str, set[str]] = {}
        results: list[BookAnomalyRouteResult] = []
        for snapshot in sorted(request.snapshots, key=lambda item: item.identity.market_id):
            skip = self._skip_reason(snapshot, request.as_of)
            if skip is not None:
                results.append(
                    BookAnomalyRouteResult(
                        market_id=snapshot.identity.market_id,
                        snapshot_id=snapshot.record_id,
                        status=BookAnomalyRouteStatus.SKIPPED,
                        skip_reason=skip,
                    )
                )
                continue
            usable[snapshot.identity.market_id] = snapshot
            route_reasons[snapshot.identity.market_id] = set(self._local_reasons(snapshot))

        self._add_family_reasons(usable, request.family_relations, route_reasons)
        hits: list[RecallHit] = []
        for market_id, snapshot in sorted(usable.items()):
            reasons = tuple(sorted(route_reasons[market_id]))
            if not reasons:
                results.append(
                    BookAnomalyRouteResult(
                        market_id=market_id,
                        snapshot_id=snapshot.record_id,
                        status=BookAnomalyRouteStatus.NO_ANOMALY,
                    )
                )
                continue
            hit = self._hit(snapshot, reasons, request.as_of, request.run_id, input_sha256)
            hits.append(hit)
            results.append(
                BookAnomalyRouteResult(
                    market_id=market_id,
                    snapshot_id=snapshot.record_id,
                    status=BookAnomalyRouteStatus.EMITTED,
                    reason_codes=reasons,
                )
            )
        return BookAnomalyRecallOutcome(
            provider_id=self.config.provider_id,
            provider_version=self.config.provider_version,
            as_of=request.as_of,
            input_sha256=input_sha256,
            hits=tuple(hits),
            route_results=tuple(sorted(results, key=lambda item: (item.market_id or "", item.snapshot_id or ""))),
        )

    def _skip_reason(
        self, snapshot: OrderbookSnapshot, as_of: datetime
    ) -> BookAnomalySkipReason | None:
        age_seconds = (as_of - snapshot.source_observed_at).total_seconds()
        if snapshot.source_observed_at > as_of or snapshot.captured_at > as_of:
            return BookAnomalySkipReason.FUTURE_BOOK
        if snapshot.stale or age_seconds >= self.config.max_book_age_seconds:
            return BookAnomalySkipReason.STALE_BOOK
        if not (
            snapshot.yes_leg.bids
            and snapshot.yes_leg.asks
            and snapshot.no_leg.bids
            and snapshot.no_leg.asks
        ):
            return BookAnomalySkipReason.ONE_SIDED_BOOK
        yes_bid, yes_ask, no_bid, no_ask = _best_prices(snapshot)
        if yes_bid >= yes_ask or no_bid >= no_ask:
            return BookAnomalySkipReason.CROSSED_BOOK
        if not (
            _target_is_sufficient(snapshot.yes_depth, self.config.required_target_size)
            and _target_is_sufficient(snapshot.no_depth, self.config.required_target_size)
        ):
            return BookAnomalySkipReason.INSUFFICIENT_DEPTH
        return None

    def _local_reasons(self, snapshot: OrderbookSnapshot) -> set[str]:
        yes_bid, yes_ask, no_bid, no_ask = _best_prices(snapshot)
        reasons: set[str] = set()
        if yes_ask - yes_bid > self.config.max_spread:
            reasons.add("YES_SPREAD_WIDE")
        if no_ask - no_bid > self.config.max_spread:
            reasons.add("NO_SPREAD_WIDE")
        depth = _visible_depth(snapshot, self.config.visible_levels)
        if min(depth.values()) < self.config.min_visible_depth:
            reasons.add("VISIBLE_DEPTH_SHALLOW")
        yes_mid = (yes_bid + yes_ask) / Decimal("2")
        no_mid = (no_bid + no_ask) / Decimal("2")
        if abs((yes_mid + no_mid) - Decimal("1")) > self.config.paired_mid_sum_tolerance:
            reasons.add("PAIRED_MID_SUM_INCONSISTENT")
        return reasons

    def _add_family_reasons(
        self,
        usable: dict[str, OrderbookSnapshot],
        relations: tuple[FamilyThresholdRelation, ...],
        route_reasons: dict[str, set[str]],
    ) -> None:
        for relation in sorted(relations, key=lambda item: item.relation_id):
            lower = usable.get(relation.lower_market_id)
            higher = usable.get(relation.higher_market_id)
            if lower is None or higher is None:
                continue
            self._validate_relation_lineage(relation, lower, higher)
            lower_mid = (lower.yes_leg.bids[0].price + lower.yes_leg.asks[0].price) / Decimal("2")
            higher_mid = (higher.yes_leg.bids[0].price + higher.yes_leg.asks[0].price) / Decimal("2")
            if higher_mid > lower_mid + self.config.family_monotonic_tolerance:
                route_reasons[lower.identity.market_id].add("FAMILY_THRESHOLD_MONOTONICITY_INCONSISTENT")
                route_reasons[higher.identity.market_id].add("FAMILY_THRESHOLD_MONOTONICITY_INCONSISTENT")

    @staticmethod
    def _validate_relation_lineage(
        relation: FamilyThresholdRelation,
        lower: OrderbookSnapshot,
        higher: OrderbookSnapshot,
    ) -> None:
        if (
            relation.lower_snapshot_id != lower.record_id
            or relation.lower_snapshot_sha256 != lower.canonical_sha256
            or relation.higher_snapshot_id != higher.record_id
            or relation.higher_snapshot_sha256 != higher.canonical_sha256
        ):
            raise ValueError("family relation snapshot lineage conflicts with request")

    def _hit(
        self,
        snapshot: OrderbookSnapshot,
        reasons: tuple[str, ...],
        as_of: datetime,
        run_id: str,
        input_sha256: str,
    ) -> RecallHit:
        yes_bid, yes_ask, no_bid, no_ask = _best_prices(snapshot)
        visible_depth = _visible_depth(snapshot, self.config.visible_levels)
        features = {
            "feature_schema_version": BOOK_ANOMALY_RECALLER_VERSION,
            "book_snapshot_id": snapshot.record_id,
            "book_snapshot_sha256": snapshot.canonical_sha256,
            "capture_group_id": snapshot.capture_group_id,
            "source_observed_at": snapshot.source_observed_at,
            "yes_spread": yes_ask - yes_bid,
            "no_spread": no_ask - no_bid,
            "paired_mid_sum": (yes_bid + yes_ask + no_bid + no_ask) / Decimal("2"),
            "visible_depth": visible_depth,
            "required_target_size": self.config.required_target_size,
            "provider_input_sha256": input_sha256,
        }
        identity = {
            "market_id": snapshot.identity.market_id,
            "snapshot_id": snapshot.record_id,
            "snapshot_sha256": snapshot.canonical_sha256,
            "reason_codes": reasons,
            "provider_version": self.config.provider_version,
            "run_id": run_id,
            "as_of": as_of,
        }
        record_id = stable_record_id("recall_hit", identity)
        return RecallHit(
            record_id=record_id,
            run_id=run_id,
            created_at=as_of,
            source=self.config.provider_id,
            source_version=self.config.provider_version,
            provenance=(
                ProvenanceRef(
                    source_artifact_id=snapshot.record_id,
                    relation="sensing_orderbook_snapshot",
                    content_sha256=snapshot.canonical_sha256,
                    source_observed_at=snapshot.source_observed_at,
                ),
            ),
            extensions={},
            market_id=snapshot.identity.market_id,
            recaller=RecallerType.BOOK_ANOMALY,
            recaller_version=self.config.provider_version,
            reason_codes=reasons,
            features=features,
            raw_score=self.config.hit_raw_score,
            observed_at=snapshot.source_observed_at,
            valid_until=min(
                as_of + timedelta(seconds=self.config.hit_validity_seconds),
                snapshot.source_observed_at
                + timedelta(seconds=self.config.max_book_age_seconds),
            ),
        )
