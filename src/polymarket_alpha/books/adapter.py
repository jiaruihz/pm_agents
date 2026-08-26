"""Pure demand and artifact adapter for the existing market-book owner.

This module declares capture demand and normalizes caller-supplied frozen
artifacts.  It never opens a socket, reads a runtime path, starts a collector,
or changes the canonical owner's configuration.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, NamedTuple, Sequence

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from src.platform.market_data.capture_contract import (
    COLLECTOR_EXACT_RESPONSE_CLOCK,
    ORDERBOOK_CAPTURE_SCHEMA_VERSION,
)
from src.platform.market_data.capture_demand import CaptureDemand
from src.platform.market_data.identity import canonical_json_hash

from ..contracts import (
    BookCaptureDemand,
    BookCapturePurpose,
    BookCaptureReceipt,
    BookCaptureStatus,
    BookLeg,
    BookLevel,
    MarketChangeEvent,
    MarketIdentity,
    OrderbookSnapshot,
    PacketStage,
    ResearchImportReason,
    ResearchImportReceipt,
    ResearchImportStatus,
    ResearchResultEnvelope,
    TargetDepthMetrics,
    stable_record_id,
)
from ..contracts.base import ensure_utc


BOOK_ADAPTER_VERSION = "p0_05a_v1"
CAPTURE_OWNER = "weather_market_books"
OWNER_CONSUMER_ID = "polymarket_alpha"
OWNER_STRATEGY_KEY = "polymarket_alpha.p0_offline"


class FrozenOwnerBookArtifact(BaseModel):
    """One exact-clock owner capture plus the raw CLOB payload it hashed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str
    raw_book: dict[str, Any]
    capture: dict[str, Any]
    raw_artifact_id: str

    @field_validator("token_id", "raw_artifact_id")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("owner artifact identity must not be blank")
        return value

    @model_validator(mode="after")
    def capture_lineage_is_exact(self) -> "FrozenOwnerBookArtifact":
        if self.capture.get("schema_version") != ORDERBOOK_CAPTURE_SCHEMA_VERSION:
            raise ValueError("owner artifact requires the released exact-clock schema")
        if str(self.capture.get("token_id") or "") != self.token_id:
            raise ValueError("owner capture token does not match artifact token")
        if self.capture.get("clock_lineage_status") != COLLECTOR_EXACT_RESPONSE_CLOCK:
            raise ValueError("owner artifact lacks exact response-clock lineage")
        if self.capture.get("event_time_pit_scorable") is not True:
            raise ValueError("owner artifact is not PIT-scorable")
        if not str(self.capture.get("request_batch_capture_id") or "").strip():
            raise ValueError("owner artifact requires request_batch_capture_id")
        if not str(self.capture.get("book_capture_id") or "").strip():
            raise ValueError("owner artifact requires book_capture_id")
        expected_raw_hash = canonical_json_hash(self.raw_book)
        if self.capture.get("raw_payload_hash") != expected_raw_hash:
            raise ValueError("owner raw_payload_hash does not match supplied raw_book")
        for clock in ("request_started_at_utc", "response_received_at_utc", "parsed_at_utc"):
            _parse_utc(self.capture.get(clock), field=clock)
        return self


class OwnerDemandBundle(NamedTuple):
    alpha_demand: BookCaptureDemand
    owner_demands: tuple[CaptureDemand, CaptureDemand]


class PairedBookNormalization(NamedTuple):
    snapshot: OrderbookSnapshot | None
    receipt: BookCaptureReceipt


def _parse_utc(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} is not a valid timestamp") from error
    return ensure_utc(parsed)


def _utc_text(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def _canonical_target_sizes(values: Sequence[Decimal]) -> tuple[Decimal, ...]:
    result = tuple(values)
    if not result or any(not isinstance(item, Decimal) or item <= 0 for item in result):
        raise ValueError("target_sizes must contain positive Decimal values")
    if result != tuple(sorted(set(result))):
        raise ValueError("target_sizes must be unique and sorted ascending")
    return result


def _demand_id(
    *,
    identity: MarketIdentity,
    purpose: BookCapturePurpose,
    trigger_artifact_id: str,
    trigger_artifact_sha256: str,
    requested_at: datetime,
    valid_until: datetime,
    max_staleness_seconds: int,
    target_sizes: tuple[Decimal, ...],
    run_id: str,
) -> str:
    return stable_record_id(
        "book_demand",
        identity,
        purpose,
        trigger_artifact_id,
        trigger_artifact_sha256,
        ensure_utc(requested_at),
        ensure_utc(valid_until),
        max_staleness_seconds,
        target_sizes,
        run_id,
    )


def build_sensing_demand(
    *,
    change_event: MarketChangeEvent,
    identity: MarketIdentity,
    requested_at: datetime,
    valid_until: datetime,
    max_staleness_seconds: int,
    target_sizes: Sequence[Decimal],
    run_id: str,
) -> BookCaptureDemand:
    """Declare optional SENSING enrichment from an immutable change event."""

    if change_event.market_id != identity.market_id:
        raise ValueError("change event and market identity do not match")
    sizes = _canonical_target_sizes(target_sizes)
    record_id = _demand_id(
        identity=identity,
        purpose=BookCapturePurpose.SENSING,
        trigger_artifact_id=change_event.record_id,
        trigger_artifact_sha256=change_event.canonical_sha256,
        requested_at=requested_at,
        valid_until=valid_until,
        max_staleness_seconds=max_staleness_seconds,
        target_sizes=sizes,
        run_id=run_id,
    )
    return BookCaptureDemand(
        record_id=record_id,
        run_id=run_id,
        created_at=ensure_utc(requested_at),
        source="alpha_book_demand_builder",
        source_version=BOOK_ADAPTER_VERSION,
        provenance=(),
        extensions={},
        demand_id=record_id,
        identity=identity,
        purpose=BookCapturePurpose.SENSING,
        trigger_artifact_id=change_event.record_id,
        trigger_artifact_sha256=change_event.canonical_sha256,
        requested_at=requested_at,
        valid_until=valid_until,
        max_staleness_seconds=max_staleness_seconds,
        target_sizes=sizes,
    )


def build_formal_review_demand(
    *,
    blind_result: ResearchResultEnvelope,
    import_receipt: ResearchImportReceipt,
    identity: MarketIdentity,
    requested_at: datetime,
    valid_until: datetime,
    max_staleness_seconds: int,
    target_sizes: Sequence[Decimal],
    run_id: str,
) -> BookCaptureDemand:
    """Declare FORMAL_REVIEW only after a bound Blind result was accepted."""

    if blind_result.packet_stage != PacketStage.BLIND:
        raise ValueError("FORMAL_REVIEW requires a BLIND research result")
    if import_receipt.packet_stage != PacketStage.BLIND:
        raise ValueError("accepted import receipt must be for the BLIND stage")
    if import_receipt.status != ResearchImportStatus.ACCEPTED:
        raise ValueError("FORMAL_REVIEW requires an accepted research import receipt")
    if import_receipt.reasons != (ResearchImportReason.ACCEPTED,):
        raise ValueError("accepted research receipt has inconsistent reasons")
    if import_receipt.packet_id != blind_result.packet_id:
        raise ValueError("receipt and Blind result packet ids do not match")
    if import_receipt.packet_sha256 != blind_result.packet_sha256:
        raise ValueError("receipt and Blind result packet hashes do not match")
    if import_receipt.accepted_result_id != blind_result.result_id:
        raise ValueError("receipt does not accept this Blind result id")
    if import_receipt.accepted_result_sha256 != blind_result.canonical_sha256:
        raise ValueError("receipt does not accept these Blind result bytes")
    requested_at = ensure_utc(requested_at)
    if blind_result.completed_at > import_receipt.imported_at:
        raise ValueError("Blind import receipt cannot precede result completion")
    if import_receipt.imported_at > requested_at:
        raise ValueError("formal-review demand cannot precede Blind acceptance")
    sizes = _canonical_target_sizes(target_sizes)
    record_id = _demand_id(
        identity=identity,
        purpose=BookCapturePurpose.FORMAL_REVIEW,
        trigger_artifact_id=blind_result.result_id,
        trigger_artifact_sha256=blind_result.canonical_sha256,
        requested_at=requested_at,
        valid_until=valid_until,
        max_staleness_seconds=max_staleness_seconds,
        target_sizes=sizes,
        run_id=run_id,
    )
    return BookCaptureDemand(
        record_id=record_id,
        run_id=run_id,
        created_at=ensure_utc(requested_at),
        source="alpha_book_demand_builder",
        source_version=BOOK_ADAPTER_VERSION,
        provenance=(),
        extensions={},
        demand_id=record_id,
        identity=identity,
        purpose=BookCapturePurpose.FORMAL_REVIEW,
        trigger_artifact_id=blind_result.result_id,
        trigger_artifact_sha256=blind_result.canonical_sha256,
        blind_result_id=blind_result.result_id,
        requested_at=requested_at,
        valid_until=valid_until,
        max_staleness_seconds=max_staleness_seconds,
        target_sizes=sizes,
    )


def build_owner_capture_demands(demand: BookCaptureDemand) -> OwnerDemandBundle:
    """Translate one Alpha demand into paired declarations for the sole owner."""

    condition_id = demand.identity.condition_id
    if condition_id is None:
        raise ValueError("existing capture owner requires explicit condition_id")
    priority = "P0" if demand.purpose == BookCapturePurpose.FORMAL_REVIEW else "P1"
    reason = f"alpha_p0_{demand.purpose.value.lower()}"
    owner_rows = tuple(
        CaptureDemand.create(
            consumer_id=OWNER_CONSUMER_ID,
            strategy_key=OWNER_STRATEGY_KEY,
            condition_id=condition_id,
            token_id=token_id,
            reason=reason,
            priority=priority,
            requested_at_utc=_utc_text(demand.requested_at),
            expires_at_utc=_utc_text(demand.valid_until),
            desired_transport="REST",
            requested_checkpoints_seconds=(0,),
            trigger_event_id=demand.demand_id,
            metadata={
                "alpha_demand_id": demand.demand_id,
                "alpha_market_id": demand.identity.market_id,
                "alpha_purpose": demand.purpose.value,
                "outcome_side": side,
            },
        )
        for side, token_id in (
            ("YES", demand.identity.yes_token_id),
            ("NO", demand.identity.no_token_id),
        )
    )
    assert len(owner_rows) == 2
    return OwnerDemandBundle(demand, (owner_rows[0], owner_rows[1]))


def _decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, float) or isinstance(value, bool):
        raise ValueError(f"{field} must not use binary float/bool input")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} is not a valid Decimal") from error
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _levels(raw: Any, *, side: str) -> tuple[BookLevel, ...]:
    if raw in (None, ()):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{side} levels must be a sequence")
    result: list[BookLevel] = []
    for index, row in enumerate(raw):
        if isinstance(row, Mapping):
            price, size = row.get("price"), row.get("size")
        elif isinstance(row, (list, tuple)) and len(row) == 2:
            price, size = row
        else:
            raise ValueError(f"{side}[{index}] has invalid level shape")
        result.append(
            BookLevel(
                price=_decimal(price, field=f"{side}[{index}].price"),
                size=_decimal(size, field=f"{side}[{index}].size"),
            )
        )
    reverse = side == "bids"
    result.sort(key=lambda item: item.price, reverse=reverse)
    prices = [item.price for item in result]
    if len(prices) != len(set(prices)):
        raise ValueError(f"{side} contains duplicate price levels")
    return tuple(result)


def _depth(levels: tuple[BookLevel, ...], target: Decimal) -> tuple[Decimal | None, bool]:
    remaining = target
    notional = Decimal("0")
    for level in levels:
        take = min(remaining, level.size)
        notional += take * level.price
        remaining -= take
        if remaining == 0:
            return notional / target, False
    return None, True


def _depth_metrics(
    *, bids: tuple[BookLevel, ...], asks: tuple[BookLevel, ...], targets: tuple[Decimal, ...]
) -> tuple[TargetDepthMetrics, ...]:
    rows: list[TargetDepthMetrics] = []
    for target in targets:
        buy_vwap, buy_insufficient = _depth(asks, target)
        sell_vwap, sell_insufficient = _depth(bids, target)
        rows.append(
            TargetDepthMetrics(
                target_size=target,
                buy_vwap=buy_vwap,
                sell_vwap=sell_vwap,
                buy_insufficient_depth=buy_insufficient,
                sell_insufficient_depth=sell_insufficient,
            )
        )
    return tuple(rows)


def _failure_receipt(
    demand: BookCaptureDemand,
    *,
    status: BookCaptureStatus,
    error_code: str,
    received_at: datetime,
) -> BookCaptureReceipt:
    received_at = ensure_utc(received_at)
    record_id = stable_record_id(
        "book_receipt", demand.demand_id, status, error_code, received_at
    )
    return BookCaptureReceipt(
        record_id=record_id,
        run_id=demand.run_id,
        created_at=received_at,
        source="alpha_existing_book_owner_adapter",
        source_version=BOOK_ADAPTER_VERSION,
        provenance=(),
        extensions={},
        receipt_id=record_id,
        demand_id=demand.demand_id,
        demand_sha256=demand.canonical_sha256,
        market_id=demand.identity.market_id,
        purpose=demand.purpose,
        status=status,
        capture_owner=CAPTURE_OWNER,
        received_at=received_at,
        error_code=error_code,
    )


def normalize_paired_owner_books(
    *,
    demand: BookCaptureDemand,
    yes_artifact: FrozenOwnerBookArtifact | None,
    no_artifact: FrozenOwnerBookArtifact | None,
    received_at: datetime,
) -> PairedBookNormalization:
    """Normalize paired frozen owner artifacts and emit a typed receipt."""

    received_at = ensure_utc(received_at)
    if received_at >= demand.valid_until:
        return PairedBookNormalization(
            None,
            _failure_receipt(
                demand,
                status=BookCaptureStatus.EXPIRED,
                error_code="DEMAND_EXPIRED",
                received_at=received_at,
            ),
        )
    if yes_artifact is None or no_artifact is None:
        return PairedBookNormalization(
            None,
            _failure_receipt(
                demand,
                status=BookCaptureStatus.SKIPPED,
                error_code="PAIRED_LEG_MISSING",
                received_at=received_at,
            ),
        )
    if yes_artifact.token_id != demand.identity.yes_token_id:
        raise ValueError("YES owner artifact token mapping is incorrect")
    if no_artifact.token_id != demand.identity.no_token_id:
        raise ValueError("NO owner artifact token mapping is incorrect")
    yes_group = str(yes_artifact.capture["request_batch_capture_id"])
    no_group = str(no_artifact.capture["request_batch_capture_id"])
    if yes_group != no_group:
        return PairedBookNormalization(
            None,
            _failure_receipt(
                demand,
                status=BookCaptureStatus.FAILED,
                error_code="CAPTURE_GROUP_MISMATCH",
                received_at=received_at,
            ),
        )

    yes_bids = _levels(yes_artifact.raw_book.get("bids"), side="bids")
    yes_asks = _levels(yes_artifact.raw_book.get("asks"), side="asks")
    no_bids = _levels(no_artifact.raw_book.get("bids"), side="bids")
    no_asks = _levels(no_artifact.raw_book.get("asks"), side="asks")
    flags: set[str] = set()
    for prefix, bids, asks in (
        ("YES", yes_bids, yes_asks),
        ("NO", no_bids, no_asks),
    ):
        if not bids:
            flags.add(f"{prefix}_BIDS_EMPTY")
        if not asks:
            flags.add(f"{prefix}_ASKS_EMPTY")
        if bids and asks and bids[0].price >= asks[0].price:
            flags.add(f"{prefix}_BOOK_CROSSED")

    response_clocks = (
        _parse_utc(
            yes_artifact.capture["response_received_at_utc"],
            field="yes.response_received_at_utc",
        ),
        _parse_utc(
            no_artifact.capture["response_received_at_utc"],
            field="no.response_received_at_utc",
        ),
    )
    captured_at = max(response_clocks)
    if received_at < captured_at:
        raise ValueError("received_at cannot precede owner response clock")
    exchange_values = (
        yes_artifact.capture.get("exchange_book_ts_utc"),
        no_artifact.capture.get("exchange_book_ts_utc"),
    )
    if any(value in (None, "") for value in exchange_values):
        flags.add("PAIRED_EXCHANGE_CLOCK_INCOMPLETE")
    observed_clocks = tuple(
        response_clock
        if exchange_value in (None, "")
        else _parse_utc(exchange_value, field="exchange_book_ts_utc")
        for exchange_value, response_clock in zip(exchange_values, response_clocks)
    )
    source_observed_at = max(observed_clocks)
    stale = (received_at - captured_at).total_seconds() > demand.max_staleness_seconds

    yes_depth = _depth_metrics(
        bids=yes_bids, asks=yes_asks, targets=demand.target_sizes
    )
    no_depth = _depth_metrics(
        bids=no_bids, asks=no_asks, targets=demand.target_sizes
    )
    if any(
        row.buy_insufficient_depth or row.sell_insufficient_depth
        for row in (*yes_depth, *no_depth)
    ):
        flags.add("TARGET_DEPTH_INSUFFICIENT")
    raw_artifact_ids = tuple(
        sorted((yes_artifact.raw_artifact_id, no_artifact.raw_artifact_id))
    )
    snapshot_id = stable_record_id(
        "orderbook_snapshot",
        demand.demand_id,
        yes_group,
        yes_artifact.capture["raw_payload_hash"],
        no_artifact.capture["raw_payload_hash"],
        canonical_json_hash(yes_artifact.capture),
        canonical_json_hash(no_artifact.capture),
        raw_artifact_ids,
    )
    snapshot = OrderbookSnapshot(
        record_id=snapshot_id,
        run_id=demand.run_id,
        created_at=received_at,
        source="alpha_existing_book_owner_adapter",
        source_version=BOOK_ADAPTER_VERSION,
        provenance=(),
        extensions={},
        identity=demand.identity,
        capture_group_id=yes_group,
        captured_at=captured_at,
        source_observed_at=source_observed_at,
        yes_leg=BookLeg(token_id=yes_artifact.token_id, bids=yes_bids, asks=yes_asks),
        no_leg=BookLeg(token_id=no_artifact.token_id, bids=no_bids, asks=no_asks),
        yes_depth=yes_depth,
        no_depth=no_depth,
        stale=stale,
        quality_flags=tuple(sorted(flags)),
        raw_artifact_ids=raw_artifact_ids,
    )
    status = BookCaptureStatus.STALE if stale else BookCaptureStatus.ACCEPTED
    error_code = "BOOK_STALE" if stale else None
    receipt_id = stable_record_id(
        "book_receipt",
        demand.demand_id,
        status,
        snapshot.canonical_sha256,
        received_at,
    )
    receipt = BookCaptureReceipt(
        record_id=receipt_id,
        run_id=demand.run_id,
        created_at=received_at,
        source="alpha_existing_book_owner_adapter",
        source_version=BOOK_ADAPTER_VERSION,
        provenance=(),
        extensions={},
        receipt_id=receipt_id,
        demand_id=demand.demand_id,
        demand_sha256=demand.canonical_sha256,
        market_id=demand.identity.market_id,
        purpose=demand.purpose,
        status=status,
        capture_owner=CAPTURE_OWNER,
        received_at=received_at,
        orderbook_snapshot_id=snapshot.record_id,
        orderbook_snapshot_sha256=snapshot.canonical_sha256,
        capture_group_id=snapshot.capture_group_id,
        source_observed_at=snapshot.source_observed_at,
        error_code=error_code,
    )
    return PairedBookNormalization(snapshot, receipt)
