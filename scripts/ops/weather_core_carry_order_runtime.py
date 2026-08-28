"""Single-path core-carry integration for the shared weather order runtime."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from scripts.ops.weather_polymarket_live_transport import build_live_transport
from src.strategies.weather_edge_v1.execution.contracts import (
    ChildOrderPlan,
    ExecutionRunContext,
    LifecycleContext,
    MarketBook,
    RestingOrderState,
)
from src.strategies.weather_edge_v1.execution.engine import (
    build_core_carry_legacy_plan_compatibility,
)
from src.strategies.weather_edge_v1.execution.quote_engine import (
    round_price_to_tick,
)
from src.strategies.weather_edge_v1.execution.venue.polymarket import (
    PolymarketOrderRequest,
    PolymarketVenueAdapter,
)
from src.strategies.weather_edge_v1.runtime.execution_journal import (
    JsonlExecutionJournal,
)
from src.strategies.weather_edge_v1.runtime.order_runtime import (
    LifecycleWorkItem,
    OrderRuntime,
    json_ready,
)
from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (
    maker_resting_price,
    walk_ask_ladder,
)
from src.strategies.weather_edge_v1.tools.execution_pipeline import (
    append_jsonl_dedup,
    build_live_order_record,
    build_paper_order,
    read_jsonl,
    stable_hash,
)


RUNTIME_OWNER = "current_yes_core_carry_tiny_live_v2"
AUTHORIZATION_REF = "user-approved-direct-runtime-migration-2026-07-28"
RETRYABLE_MAKER_FAILURES = frozenset(
    {
        "maker_only_no_resting_price",
        "maker_only_price_would_cross",
        "post_only_crosses_book",
    }
)
IDEMPOTENT_BLOCK_REASONS = frozenset(
    {
        "plan_dedupe_claim_not_acquired",
        "live_exposure_reservation_not_acquired",
        "lifecycle_action_claim_not_acquired",
    }
)


def _order_id(row: Mapping[str, Any]) -> str:
    response = row.get("exchange_response")
    payloads = [row]
    if isinstance(response, Mapping):
        payloads.append(response)
        place = response.get("place")
        if isinstance(place, Mapping):
            payloads.append(place)
        raw = response.get("raw_response")
        if isinstance(raw, Mapping):
            payloads.append(raw)
    for payload in payloads:
        for key in ("venue_order_id", "order_id", "orderID", "id"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    return ""


def _missing_payload_is_error(action) -> bool:
    if action.status in {"noop", "cancelled"}:
        return False
    return not (
        action.status == "blocked"
        and action.reason in IDEMPOTENT_BLOCK_REASONS
    )


def _metadata_from_live_orders(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        order_id = _order_id(row)
        if not order_id:
            continue
        result[order_id] = {
            "plan_id": row.get("plan_id"),
            "legacy_plan_id": row.get("plan_id"),
            "token_id": row.get("token_id"),
            "side": row.get("order_side") or "BUY",
            "outcome_side": "YES" if row.get("signal_side") == "BUY_YES" else "NO",
            "shares": row.get("size"),
            "price": row.get("posted_price") or row.get("limit_price"),
            "post_only": bool(row.get("maker_only")),
            "created_at_utc": row.get("created_at_utc"),
            "execution_profile": (
                row.get("resolved_execution_profile")
                or row.get("execution_profile")
            ),
            "execution_policy": row.get("execution_policy"),
            "order_lifecycle_policy": row.get("order_lifecycle_policy"),
            "data_epoch_ref": (
                row.get("data_epoch_ref") or row.get("source_report_ts_utc")
            ),
            "root_order_id": row.get("root_order_id") or order_id,
            "source_order_id": row.get("source_order_id") or order_id,
            "reprice_count": row.get("maker_lifecycle_reprice_count") or 0,
            "lifecycle_owner": RUNTIME_OWNER,
        }
    return result


class CoreCarryRuntimeRisk:
    def __init__(self, *, max_child_shares: float, max_batch_cost_usd: float) -> None:
        self.max_child_shares = Decimal(str(max_child_shares))
        self.max_batch_cost_usd = Decimal(str(max_batch_cost_usd))

    def check(self, *, stage: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        children = payload.get("children")
        if children:
            total = sum(
                (
                    child.requested_shares
                    * (
                        child.intent.strategy_price_cap
                        or child.intent.constraints.price_cap
                        or Decimal("1")
                    )
                    for child in children
                ),
                Decimal("0"),
            )
            return {
                "allowed": total <= self.max_batch_cost_usd,
                "reason": "aggregate_batch_cap",
            }
        child = payload.get("child")
        if not isinstance(child, ChildOrderPlan):
            return {"allowed": False, "reason": "missing_child"}
        intent = child.intent
        cap = (
            intent.strategy_price_cap
            or intent.constraints.price_cap
            or Decimal("1")
        )
        allowed = (
            intent.venue_side == "BUY"
            and intent.outcome_side == "YES"
            and child.requested_shares <= self.max_child_shares
            and child.requested_shares * cap <= self.max_child_shares
        )
        return {"allowed": allowed, "reason": "core_carry_child_cap"}


class CoreCarryRequestBuilder:
    def __init__(self, plans: Sequence[Mapping[str, Any]]) -> None:
        self.plans = {
            str(plan.get("plan_id") or ""): dict(plan)
            for plan in plans
            if str(plan.get("plan_id") or "")
        }

    def __call__(
        self,
        intent,
        child,
        book: MarketBook,
        _capabilities,
        _fees,
        replacement,
    ) -> PolymarketOrderRequest:
        plan_id = str(intent.metadata.get("legacy_plan_id") or "")
        plan = self.plans.get(plan_id)
        if plan is None:
            raise ValueError(f"missing core-carry plan for {plan_id}")
        if not book.bids or not book.asks:
            raise ValueError("fresh two-sided book unavailable")
        if child.maker_only:
            planned_limit = Decimal(str(plan.get("limit_price") or "0"))
            strategy_cap = min(
                Decimal(str(plan.get("maker_price_cap") or "0")),
                intent.model_token_probability or Decimal("0"),
            )
            maker_arm = str(plan.get("maker_arm") or "staged")
            if maker_arm == "pullback":
                price = round_price_to_tick(
                    min(planned_limit, strategy_cap),
                    book.tick_size,
                    venue_side="BUY",
                )
                if price <= 0 or price >= book.asks[0].price:
                    raise ValueError("static pullback maker price would cross fresh ask")
            elif replacement is not None:
                decision_bid = Decimal(
                    str(plan.get("maker_reprice_decision_best_bid") or "0")
                )
                decision_ask = Decimal(
                    str(plan.get("maker_reprice_decision_best_ask") or "0")
                )
                max_drift_ticks = Decimal(
                    str(plan.get("maker_replacement_max_quote_drift_ticks") or "1")
                )
                max_drift = max_drift_ticks * book.tick_size
                if decision_bid > 0 and decision_ask > 0 and (
                    abs(book.bids[0].price - decision_bid) > max_drift
                    or abs(book.asks[0].price - decision_ask) > max_drift
                ):
                    raise ValueError(
                        "fresh maker book drifted beyond replacement decision"
                    )
                shared_pullback_handoff = (
                    str(plan.get("maker_budget_mode") or "")
                    == "single_active_order_staged_then_pullback"
                    and str(plan.get("maker_last_reprice_stage") or "")
                    == "pullback_handoff"
                )
                if shared_pullback_handoff:
                    if not replacement.cancel_confirmed:
                        raise ValueError(
                            "shared maker pullback handoff requires confirmed cancel"
                        )
                    price = round_price_to_tick(
                        min(
                            planned_limit,
                            strategy_cap,
                            book.asks[0].price - book.tick_size,
                        ),
                        book.tick_size,
                        venue_side="BUY",
                    )
                    if price <= 0 or price >= book.asks[0].price:
                        raise ValueError(
                            "shared maker pullback handoff would cross fresh ask"
                        )
                else:
                    fresh_competitive = Decimal(
                        str(
                            maker_resting_price(
                                best_bid=float(book.bids[0].price),
                                best_ask=float(book.asks[0].price),
                                tick_size=float(book.tick_size),
                                price_cap=float(strategy_cap),
                            )
                        )
                    )
                    source_price = replacement.posted_price
                    if fresh_competitive <= source_price:
                        raise ValueError(
                            "fresh maker book no longer supports an improving replacement"
                        )
                    fresh_ceiling = min(
                        strategy_cap,
                        book.asks[0].price - book.tick_size,
                    )
                    price = round_price_to_tick(
                        min(planned_limit, fresh_ceiling),
                        book.tick_size,
                        venue_side="BUY",
                    )
                    if price <= source_price:
                        raise ValueError(
                            "replacement maker price does not improve source order"
                        )
            else:
                cap = min(strategy_cap, planned_limit)
                price = Decimal(
                    str(
                        maker_resting_price(
                            best_bid=float(book.bids[0].price),
                            best_ask=float(book.asks[0].price),
                            tick_size=float(book.tick_size),
                            price_cap=float(cap),
                        )
                    )
                )
            if price <= 0:
                raise ValueError("no fresh non-crossing maker price")
            if price > planned_limit:
                raise ValueError("fresh maker price exceeds lifecycle plan limit")
            post_only = True
        else:
            ladder = walk_ask_ladder(
                [
                    {"price": float(level.price), "size": float(level.size)}
                    for level in book.asks
                ],
                float(child.requested_shares),
            )
            effective = Decimal(
                str(ladder.get("effective_cost_per_share") or "0")
            )
            probability = intent.model_token_probability or Decimal("0")
            if not bool(ladder.get("executable")):
                raise ValueError("insufficient fresh exact-share ask ladder")
            if effective >= probability:
                raise ValueError("fresh ladder non-positive model EV")
            price = Decimal(str(ladder.get("max_ask_price") or "0"))
            post_only = False
        return PolymarketOrderRequest(
            token_id=intent.token_id,
            venue_side=intent.venue_side,
            price=price,
            shares=child.requested_shares,
            order_type="GTC",
            post_only=post_only,
            book_epoch_ref=book.book_epoch_ref,
            now_utc=datetime.now(timezone.utc).isoformat(),
            client_order_prefix=str(plan.get("client_order_prefix") or "pmc_"),
        )


def _run_context(*, live: bool, code_commit: str) -> ExecutionRunContext:
    now = datetime.now(timezone.utc).isoformat()
    return ExecutionRunContext(
        run_id=f"core-carry-{now}",
        execution_mode="live" if live else "paper",
        run_purpose="live_probe" if live else "shadow",
        dry_run=False,
        confirm_live=live,
        pause_state="unpaused" if live else "paused",
        authorization_ref=AUTHORIZATION_REF if live else None,
        runtime_owner=RUNTIME_OWNER,
        code_commit=code_commit,
        invoked_at_utc=now,
    )


def _legacy_response(payload: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    raw = payload.get("raw_response")
    text = " ".join(
        str(value or "")
        for value in (
            payload.get("reason"),
            payload.get("error"),
            (raw or {}).get("status") if isinstance(raw, Mapping) else "",
            (raw or {}).get("error") if isinstance(raw, Mapping) else "",
            (raw or {}).get("message") if isinstance(raw, Mapping) else "",
        )
    ).lower()
    if "post_only_crosses_book" in text or (
        "post-only" in text and ("cross" in text or "invalid" in text)
    ):
        error_classification = "post_only_crosses_book"
    elif "no fresh non-crossing maker price" in text:
        error_classification = "maker_only_no_resting_price"
    elif "crosses book" in text:
        error_classification = "maker_only_price_would_cross"
    else:
        error_classification = ""
    response = {
        "place": dict(raw) if isinstance(raw, Mapping) else {},
        "client_order_id": payload.get("client_order_id"),
        "expected_venue_order_id": payload.get("expected_venue_order_id"),
        "venue_order_id": payload.get("venue_order_id"),
        "requested_price": payload.get("requested_price"),
        "posted_price": payload.get("posted_price"),
        "maker_only": payload.get("maker_only", plan.get("maker_only")),
        "best_bid": payload.get("quote_best_bid"),
        "best_ask": payload.get("quote_best_ask"),
        "quote_best_bid": payload.get("quote_best_bid"),
        "quote_best_ask": payload.get("quote_best_ask"),
        "quote_tick_size": payload.get("tick_size"),
        "quote_status": (
            "accepted"
            if str(payload.get("status") or "") in {"submitted", "accepted", "filled"}
            else "rejected"
        ),
        "quote_reason": payload.get("reason"),
        "quote_mode": plan.get("quote_mode"),
        "model_token_probability": plan.get("model_token_probability"),
        "required_quote_edge": plan.get("required_quote_edge"),
        "clob_client": "py_clob_client_v2_shared_runtime",
        "fee_schedule_ref": payload.get("fee_schedule_ref"),
        "fee_identity": payload.get("fee_identity"),
        "estimated_fee_usd": payload.get("estimated_fee_usd"),
        "estimated_maker_rebate_usd": payload.get("estimated_maker_rebate_usd"),
        "error_classification": error_classification,
        "error_reason": payload.get("reason") if error_classification else "",
    }
    return response


def _permit_known_safe_maker_retry(
    *,
    plan: Mapping[str, Any],
    action,
    journal: JsonlExecutionJournal,
    live_exposure_key: str,
) -> None:
    if not bool(plan.get("maker_only")) or action.status == "submitted":
        return
    response = _legacy_response(dict(action.payload or {}), plan)
    classification = str(response.get("error_classification") or "")
    if classification not in RETRYABLE_MAKER_FAILURES:
        return
    journal.record_outcome(
        {
            "identity_key": action.identity_key,
            "live_exposure_key": live_exposure_key,
            "kind": "submit",
            "status": "retry_permitted",
            "reason": classification,
        }
    )


def _project_submit(
    *,
    plan: Mapping[str, Any],
    action,
    live_out: Path,
) -> tuple[int, int]:
    payload = dict(action.payload or {})
    response = _legacy_response(payload, plan)
    status = "submitted" if action.status == "submitted" else "error"
    row = build_live_order_record(dict(plan), response, status=status)
    row.update(
        {
            "execution_schema_version": plan.get("execution_schema_version"),
            "resolved_execution_profile": plan.get("resolved_execution_profile"),
            "execution_config_id": plan.get("execution_config_id"),
            "comparison_group_id": plan.get("comparison_group_id"),
            "plan_dedupe_key": plan.get("plan_dedupe_key"),
            "live_exposure_key": plan.get("live_exposure_key"),
            "client_order_id": payload.get("client_order_id"),
            "expected_venue_order_id": payload.get("expected_venue_order_id"),
            "venue_order_id": payload.get("venue_order_id"),
            "root_order_id": payload.get("root_order_id"),
            "replacement_of_order_id": payload.get("source_order_id"),
        }
    )
    result = append_jsonl_dedup(
        live_out,
        [dict(json_ready(row))],
        key_field="execution_id",
    )
    return result["written"], 0 if status == "submitted" else 1


def _submit_journal_state(
    journal: JsonlExecutionJournal,
    identity_key: str,
) -> tuple[str, dict[str, Any]]:
    """Return the latest durable state for one submit side effect."""

    attempted = False
    latest_state = ""
    latest_payload: dict[str, Any] = {}
    for row in journal.read_rows():
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        if str(payload.get("identity_key") or "") != identity_key:
            continue
        if (
            row.get("event_type") == "attempt_before_side_effect"
            and str(payload.get("kind") or "") == "submit"
        ):
            attempted = True
            latest_state = "attempt_without_outcome"
            latest_payload = {}
        elif (
            row.get("event_type") == "outcome"
            and str(payload.get("kind") or "") == "submit"
        ):
            attempted = True
            latest_state = str(payload.get("status") or "").lower()
            latest_payload = dict(payload)
    return (latest_state if attempted else "not_attempted"), latest_payload


def _recover_submit_projection(
    *,
    plan: Mapping[str, Any],
    identity_key: str,
    journal: JsonlExecutionJournal,
    live_out: Path,
) -> tuple[bool, int]:
    """Recover an exchange outcome journaled before a crashed JSONL projection."""

    state, payload = _submit_journal_state(journal, identity_key)
    if state not in {"submitted", "accepted", "filled", "reconciled"}:
        return state == "attempt_without_outcome", 0
    venue_order_id = str(
        payload.get("venue_order_id")
        or (
            payload.get("raw_response", {}).get("orderID")
            if isinstance(payload.get("raw_response"), Mapping)
            else ""
        )
        or ""
    )
    if venue_order_id and any(
        _order_id(row) == venue_order_id for row in read_jsonl(live_out)
    ):
        return False, 0
    action = SimpleNamespace(
        status="submitted",
        payload={
            **payload,
            "reason": payload.get("reason") or "execution_journal_projection_recovery",
        },
    )
    written, errors = _project_submit(plan=plan, action=action, live_out=live_out)
    return False, written if errors == 0 else 0


def _project_terminal(
    *,
    plan: Mapping[str, Any],
    action,
    live_out: Path,
    source_order_id: str,
) -> int:
    payload = dict(action.payload or {})
    order_state = dict(payload.get("order_state") or {})
    terminal_status = str(order_state.get("status") or "terminal").lower()
    if terminal_status not in {"filled", "rejected", "expired", "cancelled", "terminal"}:
        terminal_status = "terminal"
    response = {
        "quote_status": terminal_status,
        "quote_reason": action.reason,
        "posted_price": order_state.get("posted_price") or 0,
        "maker_only": True,
        "authoritative_order_state": order_state,
    }
    row = build_live_order_record(dict(plan), response, status=terminal_status)
    row.update(
        {
            "child_order_role": "core_carry_maker_terminal",
            "execution_action": "core_carry_maker_terminal",
            "execution_schema_version": plan.get("execution_schema_version"),
            "resolved_execution_profile": plan.get("resolved_execution_profile"),
            "execution_config_id": plan.get("execution_config_id"),
            "comparison_group_id": plan.get("comparison_group_id"),
            "root_order_id": action.root_order_id,
            "replacement_of_order_id": source_order_id,
        }
    )
    result = append_jsonl_dedup(
        live_out,
        [dict(json_ready(row))],
        key_field="execution_id",
    )
    return result["written"]


def _stub_order_state(plan: Mapping[str, Any], source: Mapping[str, Any]) -> RestingOrderState:
    order_id = str(plan.get("cancel_before_order_id") or _order_id(source) or "")
    shares = Decimal(str(source.get("size") or plan.get("size") or "5"))
    return RestingOrderState(
        order_id=order_id,
        client_order_id=str(source.get("client_order_id") or f"legacy:{order_id}"),
        expected_venue_order_id=str(source.get("expected_venue_order_id") or "") or None,
        root_order_id=str(source.get("root_order_id") or order_id),
        source_order_id=str(source.get("source_order_id") or order_id),
        plan_id=str(source.get("plan_id") or plan.get("source_plan_id") or ""),
        token_id=str(source.get("token_id") or plan.get("token_id") or ""),
        venue_side="BUY",
        outcome_side="YES",
        requested_shares=shares,
        matched_shares="0",
        remaining_shares=shares,
        posted_price=str(source.get("posted_price") or source.get("limit_price") or "0"),
        status="live",
        created_at_utc=str(source.get("created_at_utc") or plan.get("created_at_utc")),
        maker_only=True,
        execution_profile=str(
            source.get("resolved_execution_profile")
            or source.get("execution_profile")
        ),
        execution_policy=str(source.get("execution_policy") or plan.get("execution_policy")),
        order_lifecycle_policy=str(
            source.get("order_lifecycle_policy")
            or plan.get("order_lifecycle_policy")
        ),
        reprice_count=int(source.get("maker_lifecycle_reprice_count") or 0),
        data_epoch_ref=str(
            source.get("data_epoch_ref") or source.get("source_report_ts_utc") or ""
        ),
        authoritative_state_version="stub:lookup-required",
        lifecycle_owner=RUNTIME_OWNER,
    )


def _entry_like_lifecycle_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    ignored = {
        "execution_action",
        "cancel_before_order_id",
        "source_order_id",
        "source_plan_id",
        "source_execution_id",
        "replacement_requires_order_state",
        "cancel_only",
    }
    comparison_group_id = str(plan.get("comparison_group_id") or "")
    if not comparison_group_id:
        comparison_group_id = stable_hash(
            {
                "signal_id": plan.get("signal_id"),
                "token_id": plan.get("token_id"),
            }
        )
    return {
        **{key: value for key, value in plan.items() if key not in ignored},
        "child_order_role": (
            "maker_pullback"
            if str(plan.get("maker_arm") or "") == "pullback"
            else "maker_staged"
        ),
        "maker_only": True,
        "comparison_group_id": comparison_group_id,
        "status": "accepted",
    }


def execute_core_carry_plans(
    *,
    plans: Sequence[Mapping[str, Any]],
    output_dir: Path,
    live: bool,
    market_proxy: str | None,
    max_child_shares: float,
    max_batch_cost_usd: float,
    code_commit: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paper_out = output_dir / "paper_orders.jsonl"
    live_out = output_dir / "live_orders.jsonl"
    if not live:
        paper_rows = [build_paper_order(dict(plan)) for plan in plans]
        result = (
            append_jsonl_dedup(paper_out, paper_rows, key_field="execution_id")
            if paper_rows
            else {"written": 0, "skipped_existing": 0}
        )
        return {
            "plans_read": len(plans),
            "paper_orders": len(paper_rows),
            "paper_written": result["written"],
            "live_requested": False,
            "live_orders": 0,
            "live_written": 0,
            "live_errors": 0,
            "execution_runtime": "OrderRuntime",
        }

    metadata = _metadata_from_live_orders(live_out)
    transport, proxy = build_live_transport(
        market_proxy=market_proxy,
        metadata_by_order_id=metadata,
    )
    request_builder = CoreCarryRequestBuilder(plans)
    venue = PolymarketVenueAdapter(
        transport=transport,
        order_request_builder=request_builder,
    )
    journal = JsonlExecutionJournal(
        output_dir / "execution_journal.jsonl",
        writer_id=RUNTIME_OWNER,
    )
    risk = CoreCarryRuntimeRisk(
        max_child_shares=max_child_shares,
        max_batch_cost_usd=max_batch_cost_usd,
    )
    run_context = _run_context(live=True, code_commit=code_commit)
    source_rows = {
        _order_id(row): row for row in read_jsonl(live_out) if _order_id(row)
    }
    entries = [plan for plan in plans if not str(plan.get("execution_action") or "")]
    lifecycle = [plan for plan in plans if str(plan.get("execution_action") or "")]
    live_written = 0
    live_errors = 0
    action_count = 0
    reconciliation_required = 0

    for plan in entries:
        compatibility = build_core_carry_legacy_plan_compatibility(
            legacy_plans=[plan]
        )
        intent = compatibility.intents[0]
        child = compatibility.children[0]
        identity_key = intent.plan_dedupe_key + ":" + child.child_role
        unresolved, recovered = _recover_submit_projection(
            plan=plan,
            identity_key=identity_key,
            journal=journal,
            live_out=live_out,
        )
        if unresolved:
            reconciliation_required += 1
            live_errors += 1
            action_count += 1
            continue
        if recovered:
            live_written += recovered
            action_count += 1
            continue
        runtime = OrderRuntime(
            venue=venue,
            risk=risk,
            journal=journal,
            planner=lambda *_args, selected=child: [selected],
        )
        result = runtime.submit_intent(intent, run_context)
        action_count += len(result.actions)
        for action in result.actions:
            if action.payload is None:
                if _missing_payload_is_error(action):
                    live_errors += 1
                continue
            _permit_known_safe_maker_retry(
                plan=plan,
                action=action,
                journal=journal,
                live_exposure_key=intent.live_exposure_key + ":" + child.child_role,
            )
            written, errors = _project_submit(
                plan=plan,
                action=action,
                live_out=live_out,
            )
            live_written += written
            live_errors += errors

    for plan in lifecycle:
        source_id = str(plan.get("cancel_before_order_id") or "")
        action_name = str(plan.get("execution_action") or "")
        if not source_id and action_name == "core_carry_maker_repost":
            entry_like = _entry_like_lifecycle_plan(plan)
            compatibility = build_core_carry_legacy_plan_compatibility(
                legacy_plans=[entry_like]
            )
            intent = compatibility.intents[0]
            child = compatibility.children[0]
            runtime = OrderRuntime(
                venue=venue,
                risk=risk,
                journal=journal,
                planner=lambda *_args, selected=child: [selected],
            )
            result = runtime.submit_intent(intent, run_context)
            action_count += len(result.actions)
            for action in result.actions:
                if action.payload is None:
                    if _missing_payload_is_error(action):
                        live_errors += 1
                    continue
                _permit_known_safe_maker_retry(
                    plan=plan,
                    action=action,
                    journal=journal,
                    live_exposure_key=intent.live_exposure_key + ":" + child.child_role,
                )
                written, errors = _project_submit(
                    plan=plan,
                    action=action,
                    live_out=live_out,
                )
                live_written += written
                live_errors += errors
            continue
        source = source_rows.get(source_id)
        if source is None:
            live_errors += 1
            continue
        stub = _stub_order_state(plan, source)
        force_cancel = bool(plan.get("cancel_only"))
        invalidate = force_cancel or action_name in {
            "core_carry_maker_cancel_new_observation",
            "core_carry_maker_cancel_weather_state",
        }
        deadline = (
            datetime.now(timezone.utc).isoformat()
            if force_cancel or action_name == "core_carry_maker_cancel_ttl"
            else str(plan.get("maker_lifecycle_deadline_utc") or plan.get("expires_at_utc") or "")
        )
        context = LifecycleContext(
            now_utc=datetime.now(timezone.utc).isoformat(),
            data_epoch_ref=(
                f"{stub.data_epoch_ref}:invalidated"
                if invalidate
                else str(
                    plan.get("data_epoch_ref")
                    or plan.get("source_report_ts_utc")
                    or stub.data_epoch_ref
                    or ""
                )
            ),
            deadline_utc=deadline or None,
            lifecycle_owner=RUNTIME_OWNER,
            thesis_valid=not invalidate,
            token_unchanged=not invalidate,
            book_fresh=True,
            price_cap_valid=True,
            maker_price_cap=str(
                min(
                    Decimal(str(plan.get("maker_price_cap") or "0")),
                    Decimal(
                        str(
                            plan.get("limit_price")
                            if action_name == "core_carry_maker_reprice"
                            else plan.get("maker_price_cap") or "0"
                        )
                    ),
                )
            ),
        )

        def replacement_planner(final_order, decision, remaining, selected_plan=plan):
            entry_like = _entry_like_lifecycle_plan(selected_plan)
            compatibility = build_core_carry_legacy_plan_compatibility(
                legacy_plans=[entry_like]
            )
            base_intent = compatibility.intents[0]
            base_child = compatibility.children[0]
            intent = replace(
                base_intent,
                total_shares=remaining,
                plan_dedupe_key=decision.lifecycle_action_id,
                data_epoch_ref=context.data_epoch_ref,
                metadata={
                    **dict(base_intent.metadata),
                    "lifecycle_owner": RUNTIME_OWNER,
                    "reprice_count": final_order.reprice_count + 1,
                },
            )
            return intent, replace(
                base_child,
                intent=intent,
                requested_shares=remaining,
            )

        runtime = OrderRuntime(
            venue=venue,
            risk=risk,
            journal=journal,
            planner=lambda *_args: [],
            replacement_planner=replacement_planner,
        )
        result = runtime.manage_active_orders(
            RUNTIME_OWNER,
            [LifecycleWorkItem(order_state=stub, lifecycle_context=context)],
            run_context,
            datetime.now(timezone.utc),
        )
        action_count += len(result.actions)
        for action in result.actions:
            if action.payload is None:
                if _missing_payload_is_error(action):
                    live_errors += 1
                continue
            if action.action == "TERMINAL":
                live_written += _project_terminal(
                    plan=plan,
                    action=action,
                    live_out=live_out,
                    source_order_id=source_id,
                )
            elif action.action == "CANCEL":
                # Cancellation is already journaled by the shared runtime.  Keep
                # one legacy-compatible evidence row for canonical migration.
                response = {
                    "quote_status": "cancelled",
                    "quote_reason": action.reason,
                    "posted_price": 0,
                    "maker_only": True,
                    "cancel": dict(action.payload),
                }
                row = build_live_order_record(dict(plan), response, status="submitted")
                row.update(
                    {
                        "execution_schema_version": plan.get("execution_schema_version"),
                        "resolved_execution_profile": plan.get("resolved_execution_profile"),
                        "execution_config_id": plan.get("execution_config_id"),
                        "comparison_group_id": plan.get("comparison_group_id"),
                        "root_order_id": action.root_order_id,
                        "replacement_of_order_id": source_id,
                    }
                )
                append_jsonl_dedup(
                    live_out,
                    [dict(json_ready(row))],
                    key_field="execution_id",
                )
            else:
                written, errors = _project_submit(
                    plan=plan,
                    action=action,
                    live_out=live_out,
                )
                live_written += written
                live_errors += errors

    return {
        "plans_read": len(plans),
        "entry_plans": len(entries),
        "lifecycle_plans": len(lifecycle),
        "runtime_actions": action_count,
        "paper_orders": 0,
        "paper_written": 0,
        "live_requested": True,
        "live_orders": live_written,
        "live_written": live_written,
        "live_errors": live_errors,
        "reconciliation_required": reconciliation_required,
        "live_out": str(live_out),
        "execution_journal": str(output_dir / "execution_journal.jsonl"),
        "execution_runtime": "OrderRuntime",
        "venue_adapter": "PolymarketVenueAdapter",
        "market_proxy": proxy,
    }
