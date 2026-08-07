"""Injected Polymarket CLOB normalization; this module never creates a network client."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Protocol

from ..contracts import (
    BookLevel,
    ChildOrderPlan,
    ExecutionIntent,
    FeeSchedule,
    JsonContract,
    MarketBook,
    RestingOrderState,
    VenueCapabilities,
    canonical_json,
)
from ..quote_engine import round_marketable_price_to_tick, round_price_to_tick


class VenueAdapterError(ValueError):
    """An injected transport payload was incomplete or incompatible with the CLOB contract."""


class PolymarketTransport(Protocol):
    def fetch_capabilities(self) -> VenueCapabilities | Mapping[str, Any]: ...
    def fetch_fee_schedule(self) -> FeeSchedule | Mapping[str, Any]: ...
    def fetch_instrument(self, token_id: str) -> Mapping[str, Any]: ...
    def fetch_market_book(self, token_id: str) -> Mapping[str, Any]: ...
    def create_order(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def post_order(self, signed_order: Mapping[str, Any], *, order_type: str, post_only: bool) -> Mapping[str, Any]: ...
    def cancel_order(self, order_id: str) -> Mapping[str, Any]: ...
    def fetch_order(self, order_id: str | None, client_order_id: str) -> Mapping[str, Any] | None: ...
    def reconcile_unknown(self, *, kind: str, identity_key: str) -> Mapping[str, Any] | None: ...


def _decimal(value: Decimal | str | int, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise VenueAdapterError(f"{name} must be a Decimal, integer, or decimal string")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise VenueAdapterError(f"invalid decimal for {name}: {value!r}") from exc


def _required(mapping: Mapping[str, Any], name: str) -> Any:
    value = mapping.get(name)
    if value is None or not str(value).strip():
        raise VenueAdapterError(f"missing required venue field: {name}")
    return value


def _parse_utc(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise VenueAdapterError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _decimal_places(value: Decimal) -> int:
    return max(0, -value.normalize().as_tuple().exponent)


def _level(value: Any, name: str) -> BookLevel:
    if isinstance(value, Mapping):
        return BookLevel(price=value.get("price"), size=value.get("size"))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return BookLevel(price=value[0], size=value[1])
    raise VenueAdapterError(f"invalid {name} book level")


def _response_text(payload: Mapping[str, Any]) -> str:
    return " ".join(str(payload.get(key) or "") for key in ("status", "error", "message", "reason")).lower()


def _post_only_cross(payload: Mapping[str, Any]) -> bool:
    text = _response_text(payload)
    return ("post-only" in text and ("cross" in text or "invalid" in text)) or "order crosses book" in text


def _normalized_submit_status(payload: Mapping[str, Any]) -> str:
    text = _response_text(payload)
    if _post_only_cross(payload):
        return "rejected"
    if any(word in text for word in ("timeout", "unknown", "pending", "retrying")):
        return "unknown"
    if any(word in text for word in ("reject", "fail", "error")):
        return "rejected"
    if any(word in text for word in ("accepted", "success", "placed", "live", "open", "filled", "matched")):
        return "submitted"
    return "unknown"


def _normalized_cancel_status(payload: Mapping[str, Any]) -> str:
    text = _response_text(payload)
    if any(word in text for word in ("timeout", "unknown", "pending", "retrying")):
        return "unknown"
    if "expired" in text:
        return "expired"
    if "cancel" in text and not any(word in text for word in ("fail", "reject", "error")):
        return "cancelled"
    if any(word in text for word in ("reject", "fail", "error")):
        return "rejected"
    return "unknown"


def make_fee_identity(fee_schedule: FeeSchedule) -> str:
    payload = {
        "fee_schedule_ref": fee_schedule.fee_schedule_ref,
        "fee_formula_id": fee_schedule.fee_formula_id,
        "maker_fee_parameters": fee_schedule.maker_fee_parameters,
        "maker_rebate_program": fee_schedule.maker_rebate_program,
        "taker_fee_parameters": fee_schedule.taker_fee_parameters,
    }
    return "pmfee:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def make_client_order_id(
    *,
    plan_dedupe_key: str,
    child_role: str,
    request: "PolymarketOrderRequest",
    fee_identity: str,
    normalized_price: Decimal,
) -> str:
    payload = {
        "child_role": child_role,
        "book_epoch_ref": request.book_epoch_ref,
        "expiration_utc": request.expiration_utc,
        "fee_identity": fee_identity,
        "order_type": request.order_type,
        "plan_dedupe_key": plan_dedupe_key,
        "post_only": request.post_only,
        "price": normalized_price,
        "shares": request.shares,
        "token_id": request.token_id,
        "venue_side": request.venue_side,
    }
    return "pmc_" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:40]


@dataclass(frozen=True)
class PolymarketOrderRequest(JsonContract):
    token_id: str
    venue_side: str
    price: Decimal | str | int
    shares: Decimal | str | int
    order_type: str
    post_only: bool
    book_epoch_ref: str
    now_utc: str
    expiration_utc: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "token_id", str(self.token_id).strip())
        object.__setattr__(self, "venue_side", str(self.venue_side).upper().strip())
        object.__setattr__(self, "order_type", str(self.order_type).upper().strip())
        object.__setattr__(self, "book_epoch_ref", str(self.book_epoch_ref).strip())
        object.__setattr__(self, "price", _decimal(self.price, "price"))
        object.__setattr__(self, "shares", _decimal(self.shares, "shares"))
        if not self.token_id or self.venue_side not in {"BUY", "SELL"} or not self.order_type or not self.book_epoch_ref:
            raise VenueAdapterError("order request has missing or invalid identity fields")
        if self.price <= 0 or self.price >= 1 or self.shares <= 0:
            raise VenueAdapterError("order price must be between 0 and 1 and shares must be positive")
        _parse_utc(self.now_utc)
        if self.expiration_utc is not None:
            _parse_utc(self.expiration_utc)


@dataclass(frozen=True)
class PreparedPolymarketOrder(JsonContract):
    status: str
    reason: str
    request: PolymarketOrderRequest
    normalized_price: Decimal | None
    market_book: MarketBook | None
    fee_identity: str | None
    estimated_fee_usd: Decimal | None
    estimated_maker_rebate_usd: Decimal | None


OrderRequestBuilder = Callable[[ExecutionIntent, ChildOrderPlan, MarketBook, VenueCapabilities, FeeSchedule | None, RestingOrderState | None], PolymarketOrderRequest]


class PolymarketVenueAdapter:
    """CLOB adapter with injected transport only; callers supply every side-effect implementation."""

    def __init__(self, *, transport: PolymarketTransport, order_request_builder: OrderRequestBuilder | None = None) -> None:
        self.transport = transport
        self.order_request_builder = order_request_builder

    def fetch_capabilities(self) -> VenueCapabilities:
        raw = self.transport.fetch_capabilities()
        if isinstance(raw, VenueCapabilities):
            return raw
        return VenueCapabilities(
            venue=_required(raw, "venue"),
            protocol_version=_required(raw, "protocol_version"),
            client_version=_required(raw, "client_version"),
            collateral_asset=_required(raw, "collateral_asset"),
            supported_order_types=tuple(_required(raw, "supported_order_types")),
            post_only_order_types=tuple(raw.get("post_only_order_types", ())),
            price_precision=int(_required(raw, "price_precision")),
            size_precision=int(_required(raw, "size_precision")),
            amount_precision_by_order_type=_required(raw, "amount_precision_by_order_type"),
            gtd_security_threshold_sec=int(_required(raw, "gtd_security_threshold_sec")),
            capabilities_fetched_at_utc=_required(raw, "capabilities_fetched_at_utc"),
            fee_schedule_ref=_required(raw, "fee_schedule_ref"),
        )

    def fetch_fee_schedule(self) -> FeeSchedule:
        raw = self.transport.fetch_fee_schedule()
        if isinstance(raw, FeeSchedule):
            return raw
        return FeeSchedule(
            venue=_required(raw, "venue"),
            fee_schedule_ref=_required(raw, "fee_schedule_ref"),
            fee_schedule_fetched_at_utc=_required(raw, "fee_schedule_fetched_at_utc"),
            fee_formula_id=_required(raw, "fee_formula_id"),
            taker_fee_parameters=raw.get("taker_fee_parameters", {}),
            maker_fee_parameters=raw.get("maker_fee_parameters", {}),
            maker_rebate_program=raw.get("maker_rebate_program"),
        )

    def fetch_market_book(self, token_id: str) -> MarketBook:
        instrument = self.transport.fetch_instrument(token_id)
        raw = self.transport.fetch_market_book(token_id)
        tick = instrument.get("tick_size", raw.get("tick_size"))
        minimum = instrument.get("minimum_order_shares", raw.get("minimum_order_shares"))
        tick_source = instrument.get("tick_size_source", raw.get("tick_size_source"))
        if tick is None or minimum is None or not tick_source:
            raise VenueAdapterError("instrument/book must provide tick, minimum_order_shares, and tick_size_source")
        bids = tuple(_level(value, "bid") for value in raw.get("bids", ()))
        asks = tuple(_level(value, "ask") for value in raw.get("asks", ()))
        epoch_payload = {
            "asks": asks,
            "bids": bids,
            "instrument_version": instrument.get("instrument_version", ""),
            "sequence": raw.get("sequence", raw.get("book_epoch_ref", "")),
            "tick_size": tick,
            "token_id": token_id,
        }
        epoch = "pmbook:" + hashlib.sha256(canonical_json(epoch_payload).encode("utf-8")).hexdigest()
        return MarketBook(
            token_id=token_id,
            status=_required(raw, "status"),
            fetched_at_utc=_required(raw, "fetched_at_utc"),
            venue_timestamp_utc=raw.get("venue_timestamp_utc"),
            book_epoch_ref=epoch,
            tick_size=tick,
            tick_size_source=str(tick_source),
            minimum_order_shares=minimum,
            bids=bids,
            asks=asks,
        )

    def prepare_order(
        self,
        *,
        request: PolymarketOrderRequest,
        market_book: MarketBook,
        capabilities: VenueCapabilities,
        fee_schedule: FeeSchedule | None,
        revalidate_book: bool = True,
    ) -> PreparedPolymarketOrder:
        if fee_schedule is None:
            return PreparedPolymarketOrder("blocked", "missing_fee_schedule", request, None, None, None, None, None)
        if fee_schedule.fee_schedule_ref != capabilities.fee_schedule_ref:
            return PreparedPolymarketOrder("blocked", "fee_capability_provenance_mismatch", request, None, None, None, None, None)
        current_book = self.fetch_market_book(request.token_id) if revalidate_book else market_book
        if request.book_epoch_ref != market_book.book_epoch_ref or current_book.book_epoch_ref != market_book.book_epoch_ref:
            return PreparedPolymarketOrder("blocked", "book_epoch_changed_replan_required", request, None, current_book, None, None, None)
        if request.order_type not in capabilities.supported_order_types:
            return PreparedPolymarketOrder("blocked", "unsupported_order_type", request, None, current_book, None, None, None)
        if request.post_only and request.order_type not in capabilities.post_only_order_types:
            return PreparedPolymarketOrder("blocked", "unsupported_post_only_order_type", request, None, current_book, None, None, None)
        if request.order_type == "GTD":
            if request.expiration_utc is None:
                return PreparedPolymarketOrder("blocked", "gtd_expiration_required", request, None, current_book, None, None, None)
            if (_parse_utc(request.expiration_utc) - _parse_utc(request.now_utc)).total_seconds() <= capabilities.gtd_security_threshold_sec:
                return PreparedPolymarketOrder("blocked", "gtd_expiration_inside_security_threshold", request, None, current_book, None, None, None)
        elif request.expiration_utc is not None:
            return PreparedPolymarketOrder("blocked", "expiration_only_supported_for_gtd", request, None, current_book, None, None, None)
        normalized_price = round_price_to_tick(request.price, current_book.tick_size, venue_side=request.venue_side) if request.post_only else round_marketable_price_to_tick(request.price, current_book.tick_size, venue_side=request.venue_side)
        if _decimal_places(normalized_price) > capabilities.price_precision:
            return PreparedPolymarketOrder("blocked", "price_precision_exceeded", request, normalized_price, current_book, None, None, None)
        if _decimal_places(request.shares) > capabilities.size_precision:
            return PreparedPolymarketOrder("blocked", "size_precision_exceeded", request, normalized_price, current_book, None, None, None)
        amount_precision = capabilities.amount_precision_by_order_type.get(request.order_type)
        if amount_precision is None:
            return PreparedPolymarketOrder("blocked", "missing_amount_precision_for_order_type", request, normalized_price, current_book, None, None, None)
        if _decimal_places(normalized_price * request.shares) > amount_precision:
            return PreparedPolymarketOrder("blocked", "amount_precision_exceeded", request, normalized_price, current_book, None, None, None)
        if request.shares < current_book.minimum_order_shares:
            return PreparedPolymarketOrder("blocked", "shares_below_venue_minimum", request, normalized_price, current_book, None, None, None)
        if request.post_only and current_book.bids and current_book.asks:
            best_bid, best_ask = current_book.bids[0].price, current_book.asks[0].price
            crosses = (request.venue_side == "BUY" and normalized_price >= best_ask) or (request.venue_side == "SELL" and normalized_price <= best_bid)
            if crosses:
                return PreparedPolymarketOrder("blocked", "post_only_crosses_book", request, normalized_price, current_book, None, None, None)
        parameters = fee_schedule.maker_fee_parameters if request.post_only else fee_schedule.taker_fee_parameters
        rate = _decimal(parameters.get("rate", "0"), "fee rate")
        rebate_rate = _decimal(fee_schedule.maker_fee_parameters.get("rebate_rate", "0"), "maker rebate rate") if request.post_only else Decimal("0")
        notional = normalized_price * request.shares
        return PreparedPolymarketOrder(
            "ready",
            "validated",
            request,
            normalized_price,
            current_book,
            make_fee_identity(fee_schedule),
            notional * rate,
            notional * rebate_rate,
        )

    def place(
        self,
        *,
        intent: ExecutionIntent,
        child: ChildOrderPlan,
        market_book: MarketBook,
        capabilities: VenueCapabilities,
        fee_schedule: FeeSchedule | None,
        replacement_of: RestingOrderState | None = None,
    ) -> Mapping[str, Any]:
        if self.order_request_builder is None:
            return {"status": "rejected", "reason": "missing_injected_order_request_builder"}
        token_fee_fetcher = getattr(self.transport, "fetch_fee_schedule_for_token", None)
        if callable(token_fee_fetcher):
            fresh_fee = token_fee_fetcher(intent.token_id)
            fee_schedule = (
                fresh_fee
                if isinstance(fresh_fee, FeeSchedule)
                else FeeSchedule(
                    venue=_required(fresh_fee, "venue"),
                    fee_schedule_ref=_required(fresh_fee, "fee_schedule_ref"),
                    fee_schedule_fetched_at_utc=_required(
                        fresh_fee, "fee_schedule_fetched_at_utc"
                    ),
                    fee_formula_id=_required(fresh_fee, "fee_formula_id"),
                    taker_fee_parameters=fresh_fee.get("taker_fee_parameters", {}),
                    maker_fee_parameters=fresh_fee.get("maker_fee_parameters", {}),
                    maker_rebate_program=fresh_fee.get("maker_rebate_program"),
                )
            )
        try:
            request = self.order_request_builder(
                intent,
                child,
                market_book,
                capabilities,
                fee_schedule,
                replacement_of,
            )
        except Exception as exc:
            return {
                "status": "rejected",
                "reason": "order_request_builder_rejected",
                "error": f"{type(exc).__name__}: {exc}",
            }
        if (
            request.token_id != intent.token_id
            or request.venue_side != intent.venue_side
            or request.shares != child.requested_shares
            or request.post_only != child.maker_only
        ):
            return {"status": "rejected", "reason": "injected_order_request_does_not_match_intent_child"}
        prepared = self.prepare_order(request=request, market_book=market_book, capabilities=capabilities, fee_schedule=fee_schedule)
        if (
            prepared.status == "blocked"
            and prepared.reason == "book_epoch_changed_replan_required"
            and prepared.market_book is not None
        ):
            try:
                request = self.order_request_builder(
                    intent,
                    child,
                    prepared.market_book,
                    capabilities,
                    fee_schedule,
                    replacement_of,
                )
            except Exception as exc:
                return {
                    "status": "rejected",
                    "reason": "fresh_book_replan_rejected",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            prepared = self.prepare_order(
                request=request,
                market_book=prepared.market_book,
                capabilities=capabilities,
                fee_schedule=fee_schedule,
                revalidate_book=False,
            )
        if prepared.status != "ready":
            return {"status": "rejected", "reason": prepared.reason, "prepared_order": prepared.to_json()}
        assert prepared.normalized_price is not None and prepared.fee_identity is not None
        client_order_id = make_client_order_id(
            plan_dedupe_key=intent.plan_dedupe_key,
            child_role=child.child_role,
            request=request,
            fee_identity=prepared.fee_identity,
            normalized_price=prepared.normalized_price,
        )
        payload = {
            "client_order_id": client_order_id,
            "price": prepared.normalized_price,
            "shares": request.shares,
            "side": request.venue_side,
            "token_id": request.token_id,
            "expiration_utc": request.expiration_utc,
            "identity_key": intent.plan_dedupe_key + ":" + child.child_role,
            "plan_id": intent.metadata.get("legacy_plan_id") or intent.plan_dedupe_key,
            "legacy_plan_id": intent.metadata.get("legacy_plan_id"),
            "created_at_utc": intent.created_at_utc,
            "outcome_side": intent.outcome_side,
            "execution_profile": intent.resolved_execution_profile,
            "execution_policy": child.execution_policy,
            "order_lifecycle_policy": child.order_lifecycle_policy,
            "data_epoch_ref": intent.data_epoch_ref,
            "lifecycle_owner": intent.metadata.get("lifecycle_owner"),
            "reprice_count": intent.metadata.get("reprice_count", 0),
            "root_order_id": (
                replacement_of.root_order_id if replacement_of is not None else None
            ),
            "source_order_id": (
                replacement_of.order_id if replacement_of is not None else None
            ),
        }
        try:
            signed_order = self.transport.create_order(payload)
            expected_venue_order_id = signed_order.get("expected_venue_order_id", signed_order.get("order_id"))
        except Exception as exc:
            return {"status": "rejected", "reason": "create_order_failed_before_dispatch", "client_order_id": client_order_id, "error": f"{type(exc).__name__}: {exc}", "dispatch_stage": "create_order"}
        try:
            response = self.transport.post_order(signed_order, order_type=request.order_type, post_only=request.post_only)
        except Exception as exc:
            return {"status": "unknown", "reason": "post_order_dispatch_unknown", "client_order_id": client_order_id, "expected_venue_order_id": expected_venue_order_id, "error": f"{type(exc).__name__}: {exc}", "dispatch_stage": "post_order"}
        status = _normalized_submit_status(response)
        return {
            "status": status,
            "reason": "post_only_crosses_book" if _post_only_cross(response) else "venue_submit_response",
            "client_order_id": client_order_id,
            "expected_venue_order_id": expected_venue_order_id,
            "venue_order_id": response.get(
                "order_id", response.get("orderID", response.get("id"))
            ),
            "root_order_id": (
                replacement_of.root_order_id
                if replacement_of is not None
                else response.get(
                    "order_id", response.get("orderID", response.get("id"))
                )
            ),
            "source_order_id": (
                replacement_of.order_id if replacement_of is not None else None
            ),
            "requested_price": request.price,
            "posted_price": prepared.normalized_price,
            "requested_shares": request.shares,
            "maker_only": request.post_only,
            "order_type": request.order_type,
            "quote_best_bid": (
                prepared.market_book.bids[0].price
                if prepared.market_book and prepared.market_book.bids
                else None
            ),
            "quote_best_ask": (
                prepared.market_book.asks[0].price
                if prepared.market_book and prepared.market_book.asks
                else None
            ),
            "book_epoch_ref": prepared.market_book.book_epoch_ref if prepared.market_book else None,
            "tick_size": prepared.market_book.tick_size if prepared.market_book else None,
            "fee_schedule_ref": fee_schedule.fee_schedule_ref if fee_schedule else None,
            "fee_identity": prepared.fee_identity,
            "estimated_fee_usd": prepared.estimated_fee_usd,
            "estimated_maker_rebate_usd": prepared.estimated_maker_rebate_usd,
            "realized_maker_rebate_usd": None,
            "protocol_version": capabilities.protocol_version,
            "client_version": capabilities.client_version,
            "collateral_asset": capabilities.collateral_asset,
            "raw_response": dict(response),
        }

    def cancel(self, order_state: RestingOrderState) -> Mapping[str, Any]:
        if not order_state.order_id:
            return {"status": "rejected", "reason": "missing_venue_order_id_for_cancel"}
        try:
            response = self.transport.cancel_order(order_state.order_id)
        except Exception as exc:
            return {"status": "unknown", "reason": "cancel_order_dispatch_unknown", "error": f"{type(exc).__name__}: {exc}", "dispatch_stage": "cancel_order"}
        return {"status": _normalized_cancel_status(response), "raw_response": dict(response)}

    def fetch_order_state(self, order_id: str | None, client_order_id: str) -> RestingOrderState | None:
        raw = self.transport.fetch_order(order_id, client_order_id)
        if raw is None:
            return None
        raw_status = str(_required(raw, "status")).upper()
        normalized = {
            "OPEN": "live", "LIVE": "live", "PLACED": "live", "CANCELLED": "cancelled", "CANCELED": "cancelled",
            "EXPIRED": "expired", "REJECTED": "rejected", "FILLED": "filled",
        }.get(raw_status, "unknown")
        requested = _decimal(_required(raw, "requested_shares"), "requested_shares")
        matched = _decimal(_required(raw, "matched_shares"), "matched_shares")
        remaining = _decimal(_required(raw, "remaining_shares"), "remaining_shares")
        if raw_status == "MATCHED" and remaining == 0:
            normalized = "filled"
        if raw_status in {"FILLED", "MATCHED"} and remaining != 0:
            normalized = "unknown"
        return RestingOrderState(
            order_id=raw.get("order_id", order_id),
            client_order_id=client_order_id,
            expected_venue_order_id=raw.get("expected_venue_order_id"),
            root_order_id=raw.get("root_order_id"),
            source_order_id=raw.get("source_order_id"),
            plan_id=_required(raw, "plan_id"),
            token_id=_required(raw, "token_id"),
            venue_side=_required(raw, "venue_side"),
            outcome_side=_required(raw, "outcome_side"),
            requested_shares=requested,
            matched_shares=matched,
            remaining_shares=remaining,
            posted_price=_required(raw, "posted_price"),
            status=normalized,
            created_at_utc=_required(raw, "created_at_utc"),
            maker_only=bool(raw.get("maker_only", False)),
            execution_profile=_required(raw, "execution_profile"),
            execution_policy=_required(raw, "execution_policy"),
            order_lifecycle_policy=_required(raw, "order_lifecycle_policy"),
            reprice_count=int(raw.get("reprice_count", 0)),
            data_epoch_ref=raw.get("data_epoch_ref"),
            authoritative_state_version=raw.get("authoritative_state_version"),
            lifecycle_owner=raw.get("lifecycle_owner"),
            cancel_confirmed=normalized in {"cancelled", "expired"},
            raw_venue_status=raw_status,
            order_state_provenance="polymarket_authenticated_order_lookup_v1",
        )

    def reconcile_unknown(self, *, kind: str, identity_key: str) -> Mapping[str, Any] | None:
        callback = getattr(self.transport, "reconcile_unknown", None)
        if not callable(callback):
            return None
        raw = callback(kind=kind, identity_key=identity_key)
        if raw is None:
            return None
        status = _normalized_cancel_status(raw) if kind == "cancel" else _normalized_submit_status(raw)
        return {"status": status, "raw_response": dict(raw), "identity_key": identity_key}
