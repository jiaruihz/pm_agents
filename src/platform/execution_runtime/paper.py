"""Append-only paper execution contracts with no venue or credential path."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from src.platform.market_data.identity import canonical_json_hash


@dataclass(frozen=True)
class PaperPlan:
    plan_id: str
    intent_id: str
    candidate_id: str
    token_id: str
    side: str
    hypothetical_shares: float
    limit_price: float
    book_snapshot_id: str | None
    created_at_utc: str
    price_lineage_status: str = "candidate_time_book"
    mode: str = "zero_notional"
    schema_version: str = "polymarket_paper_plan_v1"

    @classmethod
    def create(cls, **values: Any) -> "PaperPlan":
        plan_id = canonical_json_hash(
            {
                "schema_version": "polymarket_paper_plan_v1",
                "intent_id": values["intent_id"],
                "book_snapshot_id": values.get("book_snapshot_id"),
                "hypothetical_shares": values["hypothetical_shares"],
                "limit_price": values["limit_price"],
            }
        )
        return cls(plan_id=plan_id, **values)

    def __post_init__(self) -> None:
        if self.mode != "zero_notional":
            raise ValueError("paper plan cannot grant live authority")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("paper plan side must be BUY or SELL")
        if self.hypothetical_shares <= 0:
            raise ValueError("paper plan requires positive hypothetical shares")
        if not 0 <= self.limit_price <= 1:
            raise ValueError("paper plan limit price must be in [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    plan_id: str
    intent_id: str
    token_id: str
    side: str
    hypothetical_shares: float
    limit_price: float
    accepted_at_utc: str
    status: str = "accepted"
    actual_notional: float = 0.0
    schema_version: str = "polymarket_paper_order_v1"

    @classmethod
    def from_plan(cls, plan: PaperPlan) -> "PaperOrder":
        return cls(
            order_id=canonical_json_hash(
                {"schema_version": "polymarket_paper_order_v1", "plan_id": plan.plan_id}
            ),
            plan_id=plan.plan_id,
            intent_id=plan.intent_id,
            token_id=plan.token_id,
            side=plan.side,
            hypothetical_shares=plan.hypothetical_shares,
            limit_price=plan.limit_price,
            accepted_at_utc=plan.created_at_utc,
        )

    def __post_init__(self) -> None:
        if self.actual_notional != 0:
            raise ValueError("paper order must have zero actual notional")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperFill:
    fill_id: str
    order_id: str
    plan_id: str
    intent_id: str
    candidate_id: str
    token_id: str
    side: str
    hypothetical_shares: float
    fill_price: float
    hypothetical_fee_per_share: float
    observed_at_utc: str
    book_snapshot_id: str | None
    price_lineage_status: str = "candidate_time_book"
    actual_shares: float = 0.0
    actual_cost: float = 0.0
    schema_version: str = "polymarket_paper_fill_v1"

    @classmethod
    def from_plan(
        cls,
        plan: PaperPlan,
        order: PaperOrder,
        *,
        hypothetical_fee_per_share: float,
    ) -> "PaperFill":
        return cls(
            fill_id=canonical_json_hash(
                {
                    "schema_version": "polymarket_paper_fill_v1",
                    "order_id": order.order_id,
                    "fill_price": plan.limit_price,
                    "hypothetical_shares": plan.hypothetical_shares,
                }
            ),
            order_id=order.order_id,
            plan_id=plan.plan_id,
            intent_id=plan.intent_id,
            candidate_id=plan.candidate_id,
            token_id=plan.token_id,
            side=plan.side,
            hypothetical_shares=plan.hypothetical_shares,
            fill_price=plan.limit_price,
            hypothetical_fee_per_share=hypothetical_fee_per_share,
            observed_at_utc=order.accepted_at_utc,
            book_snapshot_id=plan.book_snapshot_id,
            price_lineage_status=plan.price_lineage_status,
        )

    def __post_init__(self) -> None:
        if self.actual_shares != 0 or self.actual_cost != 0:
            raise ValueError("paper fill must not contain real execution")
        if self.hypothetical_shares <= 0:
            raise ValueError("paper fill requires positive hypothetical shares")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def execution_bundle(
    *,
    intent: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> tuple[PaperPlan, PaperOrder, PaperFill]:
    if intent.get("mode") != "zero_notional" or float(intent.get("requested_size") or 0) != 0:
        raise ValueError("only zero-notional intents may enter paper execution")
    price_raw = candidate.get("selected_ask_vwap")
    if price_raw is None:
        price_raw = candidate.get("reverse_ask_vwap")
    if price_raw is None:
        price_raw = candidate.get("ask_vwap")
    shares_raw = (intent.get("metadata") or {}).get("intended_observation_quantity")
    if shares_raw is None:
        shares_raw = candidate.get("quantity")
    if price_raw is None or shares_raw is None:
        raise ValueError("paper execution requires executable price and observation quantity")
    plan = PaperPlan.create(
        intent_id=str(intent["intent_id"]),
        candidate_id=str(intent["candidate_id"]),
        token_id=str(intent["token_id"]),
        side=str(intent["venue_side"]),
        hypothetical_shares=float(shares_raw),
        limit_price=float(price_raw),
        book_snapshot_id=intent.get("execution_book_snapshot_id"),
        created_at_utc=str(intent["created_at_utc"]),
        price_lineage_status=str(
            (intent.get("metadata") or {}).get("price_lineage_status")
            or (
                "candidate_time_book"
                if intent.get("execution_book_snapshot_id")
                else "candidate_embedded_legacy"
            )
        ),
    )
    order = PaperOrder.from_plan(plan)
    fee = candidate.get("modeled_fee_per_share")
    fill = PaperFill.from_plan(
        plan,
        order,
        hypothetical_fee_per_share=0.0 if fee is None else float(fee),
    )
    return plan, order, fill
