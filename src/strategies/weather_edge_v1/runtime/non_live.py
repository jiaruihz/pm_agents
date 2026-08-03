"""Shared non-live venue/risk adapters for direct execution-runtime migration."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol

from src.strategies.weather_edge_v1.execution.contracts import (
    BookLevel,
    ChildOrderPlan,
    ExecutionIntent,
    ExecutionRunContext,
    FeeSchedule,
    MarketBook,
    RestingOrderState,
    VenueCapabilities,
)
from src.strategies.weather_edge_v1.runtime.execution_journal import JsonlExecutionJournal
from src.strategies.weather_edge_v1.runtime.order_runtime import OrderRuntime


class LegacyCompatibility(Protocol):
    intents: tuple[ExecutionIntent, ...]
    children: tuple[ChildOrderPlan, ...]
    legacy_plan_ids: tuple[str, ...]


@dataclass(frozen=True)
class NonLiveOrderSpec:
    market_book: MarketBook


class ContractRisk:
    """Minimal shared recheck for prevalidated non-live strategy plans."""

    def check(self, *, stage: str, payload: Mapping[str, Any]) -> bool | Mapping[str, Any]:
        intent = payload.get("intent")
        if not isinstance(intent, ExecutionIntent):
            return {"allowed": False, "reason": "missing_execution_intent"}
        children = payload.get("children")
        if children is None:
            child = payload.get("child")
            children = () if child is None else (child,)
        for child in children:
            if not isinstance(child, ChildOrderPlan) or child.intent != intent:
                return {"allowed": False, "reason": "child_intent_mismatch"}
            shares = child.requested_shares
            if intent.constraints.minimum_shares is not None and shares < intent.constraints.minimum_shares:
                return {"allowed": False, "reason": "shares_below_minimum"}
            if intent.constraints.maximum_shares is not None and shares > intent.constraints.maximum_shares:
                return {"allowed": False, "reason": "shares_above_maximum"}
        return {"allowed": True, "stage": stage}


class NonLivePaperVenue:
    """Injected deterministic paper venue; it has no client, key or network path."""

    def __init__(self, specs: Mapping[str, NonLiveOrderSpec]) -> None:
        self.specs = dict(specs)
        self.place_calls: list[dict[str, Any]] = []

    def fetch_market_book(self, token_id: str) -> MarketBook:
        return self.specs[token_id].market_book

    def fetch_capabilities(self) -> VenueCapabilities:
        return VenueCapabilities(
            venue="non_live_paper",
            protocol_version="paper_v1",
            client_version="none",
            collateral_asset="USDC",
            supported_order_types=("GTC", "GTD"),
            post_only_order_types=("GTC", "GTD"),
            price_precision=3,
            size_precision=3,
            amount_precision_by_order_type={"GTC": 3, "GTD": 3},
            gtd_security_threshold_sec=60,
            capabilities_fetched_at_utc="1970-01-01T00:00:00Z",
            fee_schedule_ref="non_live_fee_v1",
        )

    def fetch_fee_schedule(self) -> FeeSchedule:
        return FeeSchedule(
            venue="non_live_paper",
            fee_schedule_ref="non_live_fee_v1",
            fee_schedule_fetched_at_utc="1970-01-01T00:00:00Z",
            fee_formula_id="evidence_only_no_realized_fee",
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
        requested_price = intent.strategy_price_cap or intent.constraints.price_cap
        if requested_price is None:
            raise ValueError("non-live paper order requires an explicit strategy price cap")
        payload = {
            "status": "accepted",
            "execution_mode": "paper",
            "venue": "non_live_paper",
            "client_order_id": child.intent.plan_dedupe_key + ":" + child.child_role,
            "requested_price": format(requested_price, "f"),
            "requested_shares": format(child.requested_shares, "f"),
            "maker_only": child.maker_only,
            "book_epoch_ref": market_book.book_epoch_ref,
            "fee_schedule_ref": None if fee_schedule is None else fee_schedule.fee_schedule_ref,
            "replacement_of": None if replacement_of is None else replacement_of.order_id,
        }
        self.place_calls.append(payload)
        return payload

    def cancel(self, order_state: RestingOrderState) -> Mapping[str, Any]:
        return {"status": "cancelled", "execution_mode": "paper", "order_id": order_state.order_id}

    def fetch_order_state(self, order_id: str | None, client_order_id: str) -> RestingOrderState | None:
        return None

    def reconcile_unknown(self, *, kind: str, identity_key: str) -> Mapping[str, Any] | None:
        return {"status": "not_found", "kind": kind, "identity_key": identity_key}


def market_book_from_legacy_plan(plan: Mapping[str, Any]) -> MarketBook:
    token_id = str(plan.get("token_id") or "").strip()
    if not token_id:
        raise ValueError("legacy plan missing token_id")
    tick = Decimal(str(plan.get("tick_size") or plan.get("quote_tick_size") or "0.001"))
    minimum = Decimal(str(plan.get("fixed_order_shares") or "1"))
    best_bid = Decimal(str(plan.get("best_bid") or plan.get("quote_best_bid") or "0"))
    best_ask = Decimal(str(plan.get("best_ask") or plan.get("quote_best_ask") or "0"))
    requested = Decimal(str(plan.get("size") or plan.get("fixed_order_shares") or "1"))
    return MarketBook(
        token_id=token_id,
        status="ok",
        fetched_at_utc=str(plan.get("created_at_utc") or "1970-01-01T00:00:00Z"),
        venue_timestamp_utc=None,
        book_epoch_ref=str(plan.get("book_epoch_ref") or plan.get("source_snapshot_path") or "legacy_plan_snapshot"),
        tick_size=tick,
        tick_size_source="legacy_plan_explicit",
        minimum_order_shares=minimum,
        bids=() if best_bid <= 0 else (BookLevel(best_bid, requested),),
        asks=() if best_ask <= 0 else (BookLevel(best_ask, requested),),
    )


def execute_legacy_compatibility_paper(
    *,
    compatibility: LegacyCompatibility,
    legacy_plans: Mapping[str, Mapping[str, Any]],
    journal_path: Path,
    strategy_instance: str,
    generated_at_utc: str,
    code_commit: str,
) -> dict[str, Any]:
    """Run preplanned compatibility children through the shared paper runtime."""

    missing = set(compatibility.legacy_plan_ids) - set(legacy_plans)
    if missing:
        raise ValueError(f"missing legacy plan payloads: {sorted(missing)}")
    specs = {
        intent.token_id: NonLiveOrderSpec(
            market_book_from_legacy_plan(legacy_plans[legacy_id])
        )
        for intent, legacy_id in zip(
            compatibility.intents,
            compatibility.legacy_plan_ids,
            strict=True,
        )
    }
    venue = NonLivePaperVenue(specs)
    runtime = OrderRuntime(
        venue=venue,
        risk=ContractRisk(),
        journal=JsonlExecutionJournal(journal_path, writer_id=strategy_instance),
        planner=lambda *args: [],
    )
    run_id = hashlib.sha256(
        json.dumps(
            {"strategy_instance": strategy_instance, "cycle": generated_at_utc},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    context = ExecutionRunContext(
        run_id=run_id,
        execution_mode="paper",
        run_purpose="shadow",
        dry_run=False,
        confirm_live=False,
        pause_state="paused",
        authorization_ref=None,
        runtime_owner=strategy_instance,
        code_commit=code_commit,
        invoked_at_utc=generated_at_utc,
    )
    actions = []
    for intent, child in zip(
        compatibility.intents,
        compatibility.children,
        strict=True,
    ):
        actions.extend(runtime.submit_preplanned(intent, child, context).actions)
    return {
        "authority": "shared_order_runtime",
        "execution_mode": "paper",
        "legacy_plan_journal": "deprecated_read_only",
        "input_plans": len(compatibility.legacy_plan_ids),
        "submitted": sum(action.status == "submitted" for action in actions),
        "deduped": sum(action.reason == "plan_dedupe_claim_not_acquired" for action in actions),
        "blocked": sum(
            action.status == "blocked"
            and action.reason != "plan_dedupe_claim_not_acquired"
            for action in actions
        ),
        "venue_calls": len(venue.place_calls),
        "journal": str(journal_path),
    }
