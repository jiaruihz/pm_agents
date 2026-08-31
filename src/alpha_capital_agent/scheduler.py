"""Caller-driven keyset scans and due-work scheduling for ACA shadow mode.

No function in this module imports an HTTP client, sleeps, starts a daemon, or
owns production desired state.  Network capture remains an injected owner;
this module validates ordering, overlap, budgets and immutable receipts.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from src.polymarket_alpha.contracts import (
    AlphaContract,
    CommonEnvelope,
    stable_record_id,
)
from src.polymarket_alpha.contracts.base import ensure_utc, validate_sha256

from .universe import NextEvaluation


SCHEDULER_SOURCE = "alpha_capital_agent.scheduler"
SCHEDULER_VERSION = "aca_scheduler_v1"
CADENCE_WORK_VERSION = "aca_scheduler_cadence_v1"


class ScanLane(StrEnum):
    LIFECYCLE_EVENT = "LIFECYCLE_EVENT"
    GAMMA_DELTA = "GAMMA_DELTA"
    MARKETABILITY_WATCH = "MARKETABILITY_WATCH"
    FULL_ACTIVE_CENSUS = "FULL_ACTIVE_CENSUS"
    DAILY_ANTI_ENTROPY = "DAILY_ANTI_ENTROPY"
    RESEARCH_ADMISSION = "RESEARCH_ADMISSION"
    PORTFOLIO_REFRESH = "PORTFOLIO_REFRESH"
    ACCOUNT_RECONCILIATION = "ACCOUNT_RECONCILIATION"


class ScanTermination(StrEnum):
    END_OF_STREAM = "END_OF_STREAM"
    WATERMARK_REACHED = "WATERMARK_REACHED"
    MAX_PAGES_BOUND = "MAX_PAGES_BOUND"
    DUPLICATE_CURSOR = "DUPLICATE_CURSOR"
    ORDERING_INVALID = "ORDERING_INVALID"
    FETCH_FAILED = "FETCH_FAILED"
    DISABLED = "DISABLED"
    CLOCK_SKEW = "CLOCK_SKEW"


class UniverseScanPolicy(CommonEnvelope):
    scan_policy_id: str
    policy_name: str
    enabled: bool = True
    delta_interval_seconds: int = Field(default=300, gt=0)
    overlap_seconds: int = Field(default=900, gt=0)
    near_interval_seconds: int = Field(default=300, gt=0)
    dormant_interval_seconds: int = Field(default=1800, gt=0)
    full_census_interval_seconds: int = Field(default=21600, gt=0)
    anti_entropy_interval_seconds: int = Field(default=86400, gt=0)
    portfolio_fallback_seconds: int = Field(default=300, gt=0)
    account_snapshot_fallback_seconds: int = Field(default=60, gt=0)
    account_reconciliation_seconds: int = Field(default=900, gt=0)
    debounce_seconds: int = Field(default=30, gt=0)
    formal_input_ttl_seconds: int = Field(default=60, gt=0)
    page_limit: int = Field(default=500, gt=0, le=500)
    max_pages_per_run: int = Field(default=100, gt=0)
    max_clock_skew_seconds: int = Field(default=5, ge=0)
    mode: Literal["READ_ONLY_SHADOW"] = "READ_ONLY_SHADOW"
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @model_validator(mode="after")
    def policy_identity_and_cadence_hold(self) -> "UniverseScanPolicy":
        if self.scan_policy_id != self.record_id:
            raise ValueError("scan_policy_id must equal record_id")
        if not self.record_id.startswith("universe_scan_policy:"):
            raise ValueError("scan policy id must use universe_scan_policy namespace")
        if self.overlap_seconds < self.delta_interval_seconds:
            raise ValueError("delta overlap must cover at least one delta interval")
        if self.near_interval_seconds > self.dormant_interval_seconds:
            raise ValueError("near watch cannot be slower than dormant watch")
        if self.formal_input_ttl_seconds > self.portfolio_fallback_seconds:
            raise ValueError("formal inputs must expire before portfolio fallback")
        return self


class UniverseScanCursor(CommonEnvelope):
    cursor_id: str
    lane: ScanLane
    watermark_updated_at: datetime | None = None
    watermark_market_id: str | None = None
    coverage_complete: bool
    prior_cursor_id: str | None = None
    prior_cursor_sha256: str | None = None
    observed_at: datetime
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("watermark_updated_at", "observed_at")
    @classmethod
    def cursor_clocks_are_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @field_validator("prior_cursor_sha256")
    @classmethod
    def prior_hash_is_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def cursor_identity_and_pairs_hold(self) -> "UniverseScanCursor":
        if self.cursor_id != self.record_id:
            raise ValueError("cursor_id must equal record_id")
        if (self.watermark_updated_at is None) != (self.watermark_market_id is None):
            raise ValueError("watermark timestamp and market id must be set together")
        if (self.prior_cursor_id is None) != (self.prior_cursor_sha256 is None):
            raise ValueError("prior cursor id/hash must be set together")
        return self


class UniverseScanRunReceipt(CommonEnvelope):
    scan_receipt_id: str
    lane: ScanLane
    scan_policy_id: str
    scan_policy_sha256: str
    scheduled_for: datetime
    started_at: datetime
    completed_at: datetime
    pages_fetched: int = Field(ge=0)
    items_fetched: int = Field(ge=0)
    unique_markets_seen: int = Field(ge=0)
    selected_market_count: int = Field(ge=0)
    termination: ScanTermination
    coverage_complete: bool
    input_cursor_id: str | None = None
    input_cursor_sha256: str | None = None
    output_cursor_id: str
    output_cursor_sha256: str
    error_codes: tuple[str, ...] = ()
    mode: Literal["READ_ONLY_SHADOW"] = "READ_ONLY_SHADOW"
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("scan_policy_sha256", "output_cursor_sha256")
    @classmethod
    def receipt_hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("input_cursor_sha256")
    @classmethod
    def optional_hash_is_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("scheduled_for", "started_at", "completed_at")
    @classmethod
    def receipt_clocks_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("error_codes")
    @classmethod
    def errors_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("error codes cannot be blank")
        if normalized != tuple(sorted(set(normalized))):
            raise ValueError("error codes must be unique and sorted")
        return normalized

    @model_validator(mode="after")
    def receipt_identity_and_status_hold(self) -> "UniverseScanRunReceipt":
        if self.scan_receipt_id != self.record_id:
            raise ValueError("scan_receipt_id must equal record_id")
        if not (self.scheduled_for <= self.started_at <= self.completed_at):
            raise ValueError("scan clocks must be ordered")
        if (self.input_cursor_id is None) != (self.input_cursor_sha256 is None):
            raise ValueError("input cursor id/hash must be set together")
        complete_terms = {
            ScanTermination.END_OF_STREAM,
            ScanTermination.WATERMARK_REACHED,
        }
        if self.coverage_complete != (self.termination in complete_terms):
            raise ValueError("coverage_complete must match a successful termination")
        if self.coverage_complete and self.error_codes:
            raise ValueError("complete scan cannot carry error codes")
        return self


class CadenceResource(StrEnum):
    """Mutex namespace for periodic lanes owned by one scheduler."""

    GAMMA_DELTA = "GAMMA_DELTA"
    UNIVERSE_RECONCILIATION = "UNIVERSE_RECONCILIATION"


class CadenceWorkOrder(CommonEnvelope):
    """Immutable periodic lane slot; lease state remains operational storage."""

    work_id: str
    lane: ScanLane
    resource: CadenceResource
    scan_policy_id: str
    scan_policy_sha256: str
    cadence_due_at: datetime
    mode: Literal["READ_ONLY_SHADOW"] = "READ_ONLY_SHADOW"
    execution: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("scan_policy_sha256")
    @classmethod
    def cadence_policy_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("cadence_due_at")
    @classmethod
    def cadence_due_clock_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def cadence_identity_and_resource_hold(self) -> "CadenceWorkOrder":
        if self.work_id != self.record_id:
            raise ValueError("cadence work_id must equal record_id")
        if not self.record_id.startswith("cadence_work_order:"):
            raise ValueError("cadence work id must use cadence_work_order namespace")
        if self.resource != cadence_resource_for_lane(self.lane):
            raise ValueError("cadence lane uses the wrong mutex resource")
        return self


@dataclass(frozen=True, slots=True)
class KeysetPage:
    items: tuple[Mapping[str, Any], ...]
    next_cursor: str | None

    @staticmethod
    def of(
        items: Sequence[Mapping[str, Any]], next_cursor: str | None
    ) -> "KeysetPage":
        cursor = None if next_cursor is None else next_cursor.strip()
        if next_cursor is not None and not cursor:
            cursor = None
        return KeysetPage(items=tuple(items), next_cursor=cursor)


@dataclass(frozen=True, slots=True)
class IncrementalScanResult:
    selected_items: tuple[dict[str, Any], ...]
    cursor: UniverseScanCursor
    receipt: UniverseScanRunReceipt


def cadence_resource_for_lane(lane: ScanLane) -> CadenceResource:
    if lane is ScanLane.GAMMA_DELTA:
        return CadenceResource.GAMMA_DELTA
    if lane in {
        ScanLane.FULL_ACTIVE_CENSUS,
        ScanLane.DAILY_ANTI_ENTROPY,
    }:
        return CadenceResource.UNIVERSE_RECONCILIATION
    raise ValueError(f"lane {lane.value} is not a periodic universe scan")


def cadence_interval_seconds(
    policy: UniverseScanPolicy, lane: ScanLane
) -> int:
    if lane is ScanLane.GAMMA_DELTA:
        return policy.delta_interval_seconds
    if lane is ScanLane.FULL_ACTIVE_CENSUS:
        return policy.full_census_interval_seconds
    if lane is ScanLane.DAILY_ANTI_ENTROPY:
        return policy.anti_entropy_interval_seconds
    raise ValueError(f"lane {lane.value} has no periodic cadence")


def latest_due_cadence_slot(
    *,
    policy: UniverseScanPolicy,
    lane: ScanLane,
    as_of: datetime,
    last_completed_due_at: datetime | None = None,
) -> datetime | None:
    """Return one collapsed catch-up slot anchored to the policy release clock."""

    now = ensure_utc(as_of)
    anchor = policy.created_at
    if last_completed_due_at is not None:
        last_completed_due_at = ensure_utc(last_completed_due_at)
        if last_completed_due_at < anchor:
            raise ValueError("completed cadence slot predates its policy")
    if now < anchor:
        return None
    interval = cadence_interval_seconds(policy, lane)
    elapsed_seconds = int((now - anchor).total_seconds())
    latest = anchor + timedelta(seconds=(elapsed_seconds // interval) * interval)
    if last_completed_due_at is not None and latest <= last_completed_due_at:
        return None
    return latest


def build_cadence_work_order(
    *,
    policy: UniverseScanPolicy,
    lane: ScanLane,
    cadence_due_at: datetime,
) -> CadenceWorkOrder:
    due = ensure_utc(cadence_due_at)
    if due < policy.created_at:
        raise ValueError("cadence work cannot predate its policy")
    interval = cadence_interval_seconds(policy, lane)
    if int((due - policy.created_at).total_seconds()) % interval != 0:
        raise ValueError("cadence work must lie on a policy cadence boundary")
    resource = cadence_resource_for_lane(lane)
    work_id = stable_record_id(
        "cadence_work_order",
        policy.scan_policy_id,
        policy.canonical_sha256,
        lane,
        resource,
        due,
    )
    return CadenceWorkOrder(
        record_id=work_id,
        work_id=work_id,
        run_id=policy.run_id,
        created_at=due,
        source=SCHEDULER_SOURCE,
        source_version=CADENCE_WORK_VERSION,
        provenance=(),
        extensions={},
        lane=lane,
        resource=resource,
        scan_policy_id=policy.scan_policy_id,
        scan_policy_sha256=policy.canonical_sha256,
        cadence_due_at=due,
    )


def build_scan_policy(
    *,
    policy_name: str,
    run_id: str,
    created_at: datetime,
    enabled: bool = True,
    source_version: str = SCHEDULER_VERSION,
    page_limit: int = 500,
    max_pages_per_run: int = 100,
    max_clock_skew_seconds: int = 5,
) -> UniverseScanPolicy:
    created = ensure_utc(created_at)
    identity = {
        "run_id": run_id,
        "created_at": created,
        "policy_name": policy_name,
        "source_version": source_version,
        "enabled": enabled,
        "cadence": {
            "delta": 300,
            "overlap": 900,
            "near": 300,
            "dormant": 1800,
            "full": 21600,
            "anti_entropy": 86400,
            "portfolio": 300,
            "account_snapshot": 60,
            "account_reconciliation": 900,
            "debounce": 30,
            "formal_ttl": 60,
        },
        "paging": {
            "page_limit": page_limit,
            "max_pages_per_run": max_pages_per_run,
            "max_clock_skew_seconds": max_clock_skew_seconds,
        },
    }
    policy_id = stable_record_id("universe_scan_policy", identity)
    return UniverseScanPolicy(
        record_id=policy_id,
        scan_policy_id=policy_id,
        run_id=run_id,
        created_at=created,
        source=SCHEDULER_SOURCE,
        source_version=source_version,
        policy_name=policy_name,
        enabled=enabled,
        page_limit=page_limit,
        max_pages_per_run=max_pages_per_run,
        max_clock_skew_seconds=max_clock_skew_seconds,
    )


def _parse_updated_at(item: Mapping[str, Any]) -> datetime:
    raw = item.get("updatedAt", item.get("updated_at"))
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("UPDATED_AT_MISSING")
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("UPDATED_AT_INVALID") from exc
    return ensure_utc(parsed)


def _market_key(item: Mapping[str, Any]) -> tuple[datetime, str]:
    market_id = str(item.get("id") or item.get("marketId") or "").strip()
    if not market_id:
        raise ValueError("MARKET_ID_MISSING")
    return _parse_updated_at(item), market_id


def _make_cursor(
    *,
    lane: ScanLane,
    watermark: tuple[datetime, str] | None,
    coverage_complete: bool,
    prior: UniverseScanCursor | None,
    observed_at: datetime,
    run_id: str,
) -> UniverseScanCursor:
    fields = {
        "run_id": run_id,
        "created_at": observed_at,
        "source": SCHEDULER_SOURCE,
        "source_version": SCHEDULER_VERSION,
        "lane": lane,
        "watermark_updated_at": None if watermark is None else watermark[0],
        "watermark_market_id": None if watermark is None else watermark[1],
        "coverage_complete": coverage_complete,
        "prior_cursor_id": None if prior is None else prior.cursor_id,
        "prior_cursor_sha256": None if prior is None else prior.canonical_sha256,
        "observed_at": observed_at,
        "execution": "NO_ORDER",
    }
    cursor_id = stable_record_id("universe_scan_cursor", fields)
    return UniverseScanCursor(
        record_id=cursor_id,
        cursor_id=cursor_id,
        **fields,
    )


def _make_receipt(
    *,
    lane: ScanLane,
    policy: UniverseScanPolicy,
    scheduled_for: datetime,
    started_at: datetime,
    completed_at: datetime,
    pages_fetched: int,
    items_fetched: int,
    unique_markets_seen: int,
    selected_market_count: int,
    termination: ScanTermination,
    prior: UniverseScanCursor | None,
    cursor: UniverseScanCursor,
    error_codes: tuple[str, ...],
    run_id: str,
) -> UniverseScanRunReceipt:
    fields = {
        "run_id": run_id,
        "created_at": completed_at,
        "source": SCHEDULER_SOURCE,
        "source_version": SCHEDULER_VERSION,
        "lane": lane,
        "scan_policy_id": policy.scan_policy_id,
        "scan_policy_sha256": policy.canonical_sha256,
        "scheduled_for": scheduled_for,
        "started_at": started_at,
        "completed_at": completed_at,
        "pages_fetched": pages_fetched,
        "items_fetched": items_fetched,
        "unique_markets_seen": unique_markets_seen,
        "selected_market_count": selected_market_count,
        "termination": termination,
        "coverage_complete": termination
        in {ScanTermination.END_OF_STREAM, ScanTermination.WATERMARK_REACHED},
        "input_cursor_id": None if prior is None else prior.cursor_id,
        "input_cursor_sha256": None if prior is None else prior.canonical_sha256,
        "output_cursor_id": cursor.cursor_id,
        "output_cursor_sha256": cursor.canonical_sha256,
        "error_codes": tuple(sorted(set(error_codes))),
        "mode": "READ_ONLY_SHADOW",
        "execution": "NO_ORDER",
    }
    receipt_id = stable_record_id("universe_scan_receipt", fields)
    return UniverseScanRunReceipt(
        record_id=receipt_id,
        scan_receipt_id=receipt_id,
        **fields,
    )


def run_keyset_scan(
    *,
    fetch_page: Callable[[str | None, int], KeysetPage],
    lane: ScanLane,
    policy: UniverseScanPolicy,
    prior_cursor: UniverseScanCursor | None,
    scheduled_for: datetime,
    started_at: datetime,
    completed_at: datetime,
    run_id: str,
) -> IncrementalScanResult:
    """Run one bounded descending-updatedAt keyset walk over an injected owner."""

    scheduled = ensure_utc(scheduled_for)
    started = ensure_utc(started_at)
    completed = ensure_utc(completed_at)
    if not scheduled <= started <= completed:
        raise ValueError("scan clocks must be ordered")
    prior_watermark = (
        None
        if prior_cursor is None or prior_cursor.watermark_updated_at is None
        else (
            prior_cursor.watermark_updated_at,
            str(prior_cursor.watermark_market_id),
        )
    )
    if prior_cursor is not None and prior_cursor.lane != lane:
        raise ValueError("prior cursor belongs to another scan lane")
    if not policy.enabled:
        cursor = _make_cursor(
            lane=lane,
            watermark=prior_watermark,
            coverage_complete=False,
            prior=prior_cursor,
            observed_at=completed,
            run_id=run_id,
        )
        receipt = _make_receipt(
            lane=lane,
            policy=policy,
            scheduled_for=scheduled,
            started_at=started,
            completed_at=completed,
            pages_fetched=0,
            items_fetched=0,
            unique_markets_seen=0,
            selected_market_count=0,
            termination=ScanTermination.DISABLED,
            prior=prior_cursor,
            cursor=cursor,
            error_codes=("SCHEDULER_DISABLED",),
            run_id=run_id,
        )
        return IncrementalScanResult((), cursor, receipt)
    if abs((started - scheduled).total_seconds()) > policy.max_clock_skew_seconds:
        cursor = _make_cursor(
            lane=lane,
            watermark=prior_watermark,
            coverage_complete=False,
            prior=prior_cursor,
            observed_at=completed,
            run_id=run_id,
        )
        receipt = _make_receipt(
            lane=lane,
            policy=policy,
            scheduled_for=scheduled,
            started_at=started,
            completed_at=completed,
            pages_fetched=0,
            items_fetched=0,
            unique_markets_seen=0,
            selected_market_count=0,
            termination=ScanTermination.CLOCK_SKEW,
            prior=prior_cursor,
            cursor=cursor,
            error_codes=("CLOCK_SKEW",),
            run_id=run_id,
        )
        return IncrementalScanResult((), cursor, receipt)
    cutoff = (
        None
        if prior_watermark is None or lane != ScanLane.GAMMA_DELTA
        else prior_watermark[0] - timedelta(seconds=policy.overlap_seconds)
    )
    requested_cursor: str | None = None
    seen_request_cursors: set[str | None] = set()
    selected_by_market: dict[str, tuple[tuple[datetime, str], dict[str, Any]]] = {}
    all_market_ids: set[str] = set()
    pages_fetched = 0
    items_fetched = 0
    prior_page_tail: tuple[datetime, str] | None = None
    maximum_seen: tuple[datetime, str] | None = prior_watermark
    termination: ScanTermination | None = None
    errors: list[str] = []
    while termination is None and pages_fetched < policy.max_pages_per_run:
        if requested_cursor in seen_request_cursors:
            termination = ScanTermination.DUPLICATE_CURSOR
            errors.append("DUPLICATE_CURSOR")
            break
        seen_request_cursors.add(requested_cursor)
        try:
            page = fetch_page(requested_cursor, policy.page_limit)
        except Exception as exc:  # injected transport boundary: receipt, not partial advance
            termination = ScanTermination.FETCH_FAILED
            errors.append(f"FETCH_FAILED:{type(exc).__name__}")
            break
        pages_fetched += 1
        items_fetched += len(page.items)
        try:
            keyed = [(_market_key(item), dict(item)) for item in page.items]
        except (TypeError, ValueError) as exc:
            termination = ScanTermination.ORDERING_INVALID
            errors.append(str(exc))
            break
        keys = [item[0] for item in keyed]
        if keys != sorted(keys, reverse=True):
            termination = ScanTermination.ORDERING_INVALID
            errors.append("PAGE_NOT_DESCENDING_UPDATED_AT_ID")
            break
        if prior_page_tail is not None and keys and keys[0] > prior_page_tail:
            termination = ScanTermination.ORDERING_INVALID
            errors.append("CROSS_PAGE_ORDERING_VIOLATION")
            break
        if keys:
            prior_page_tail = keys[-1]
            maximum_seen = max(maximum_seen, keys[0]) if maximum_seen is not None else keys[0]
        for key, item in keyed:
            all_market_ids.add(key[1])
            if cutoff is not None and key[0] < cutoff:
                continue
            market_id = key[1]
            existing = selected_by_market.get(market_id)
            if existing is None or key > existing[0]:
                selected_by_market[market_id] = (key, item)
        if cutoff is not None and keys and keys[-1][0] < cutoff:
            termination = ScanTermination.WATERMARK_REACHED
            break
        if page.next_cursor is None:
            termination = ScanTermination.END_OF_STREAM
            break
        if page.next_cursor in seen_request_cursors:
            termination = ScanTermination.DUPLICATE_CURSOR
            errors.append("DUPLICATE_CURSOR")
            break
        requested_cursor = page.next_cursor
    if termination is None:
        termination = ScanTermination.MAX_PAGES_BOUND
        errors.append("MAX_PAGES_BOUND")
    coverage_complete = termination in {
        ScanTermination.END_OF_STREAM,
        ScanTermination.WATERMARK_REACHED,
    }
    # A first bootstrap has no safe watermark stop; it is complete only at EOS.
    if prior_watermark is None and termination == ScanTermination.WATERMARK_REACHED:
        coverage_complete = False
        termination = ScanTermination.ORDERING_INVALID
        errors.append("BOOTSTRAP_REQUIRES_END_OF_STREAM")
    if not coverage_complete:
        selected_by_market.clear()
        maximum_seen = prior_watermark
    selected_items = tuple(
        item
        for _key, item in sorted(
            selected_by_market.values(), key=lambda entry: entry[0], reverse=True
        )
    )
    cursor = _make_cursor(
        lane=lane,
        watermark=maximum_seen,
        coverage_complete=coverage_complete,
        prior=prior_cursor,
        observed_at=completed,
        run_id=run_id,
    )
    receipt = _make_receipt(
        lane=lane,
        policy=policy,
        scheduled_for=scheduled,
        started_at=started,
        completed_at=completed,
        pages_fetched=pages_fetched,
        items_fetched=items_fetched,
        unique_markets_seen=len(all_market_ids),
        selected_market_count=len(selected_items),
        termination=termination,
        prior=prior_cursor,
        cursor=cursor,
        error_codes=tuple(errors),
        run_id=run_id,
    )
    return IncrementalScanResult(selected_items, cursor, receipt)


def select_due_evaluations(
    schedules: Sequence[NextEvaluation],
    *,
    as_of: datetime,
    max_items: int,
) -> tuple[NextEvaluation, ...]:
    """Select latest due work per market with deterministic priority ordering."""

    if max_items < 0:
        raise ValueError("max_items cannot be negative")
    now = ensure_utc(as_of)
    latest: dict[str, NextEvaluation] = {}
    for item in schedules:
        current = latest.get(item.market_id)
        if current is None or (item.created_at, item.record_id) > (
            current.created_at,
            current.record_id,
        ):
            latest[item.market_id] = item
    due = [item for item in latest.values() if item.due_at <= now]
    due.sort(key=lambda item: (-item.priority, item.due_at, item.market_id, item.record_id))
    return tuple(due[:max_items])
