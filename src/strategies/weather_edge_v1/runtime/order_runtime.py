from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from src.strategies.weather_edge_v1.execution.contracts import (
    ChildOrderPlan,
    ExecutionIntent,
    ExecutionRunContext,
    FeeSchedule,
    LifecycleContext,
    MarketBook,
    RestingOrderState,
    VenueCapabilities,
)
from src.strategies.weather_edge_v1.execution.lifecycle import evaluate_order_lifecycle
from src.strategies.weather_edge_v1.execution.profiles import get_execution_profile
from src.strategies.weather_edge_v1.execution.reconciliation import reconcile_replacement
from src.strategies.weather_edge_v1.runtime.execution_journal import ExecutionJournal
from weather_clock_contract import parse_utc


def json_ready(value: Any) -> Any:
    """Return a JSON-serializable representation for strategy runtime ledgers."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_ready(v) for v in value]
    if hasattr(value, "item"):
        try:
            return json_ready(value.item())
        except Exception:
            pass
    try:
        if math.isnan(value):  # type: ignore[arg-type]
            return None
    except Exception:
        pass
    return str(value)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_ready(dict(payload)), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(json_ready(dict(row)), ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(json_ready(dict(row)), ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def parse_executor_json(stdout: str) -> dict[str, Any] | None:
    try:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start >= 0 and end > start:
            payload = json.loads(stdout[start : end + 1])
            return payload if isinstance(payload, dict) else None
    except Exception:
        return None
    return None


def executor_proxy_env(proxy_url: str = "") -> dict[str, str]:
    env = os.environ.copy()
    proxy = str(proxy_url or "").strip()
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        env["ALL_PROXY"] = proxy
    else:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(key, None)
    return env


def weather_order_executor_cmd(
    *,
    root: Path,
    plans_path: Path,
    paper_out: Path,
    live_out: Path,
    live: bool,
    confirm_live: bool,
    allow_taker: bool = False,
    cancel_expired: bool = False,
    no_telegram: bool = True,
    python_executable: str | None = None,
    market_proxy: str | None = None,
) -> list[str]:
    py = python_executable or sys.executable
    cmd = [
        py,
        "scripts/ops/weather_order_executor.py",
        "--plans",
        str(plans_path),
        "--paper-out",
        str(paper_out),
        "--live-out",
        str(live_out),
    ]
    if live:
        cmd.extend(["--live", "--confirm-live"])
    if allow_taker:
        cmd.append("--allow-taker")
    if cancel_expired:
        cmd.append("--cancel-expired")
    if no_telegram:
        cmd.append("--no-telegram")
    if str(market_proxy or "").strip():
        cmd.extend(["--market-proxy", str(market_proxy)])
    return cmd


def run_weather_order_executor(
    *,
    root: Path,
    plans_path: Path,
    paper_out: Path,
    live_out: Path,
    live: bool,
    confirm_live: bool,
    allow_taker: bool = False,
    cancel_expired: bool = False,
    no_telegram: bool = True,
    timeout_sec: float = 180.0,
    env: dict[str, str] | None = None,
    python_executable: str | None = None,
    market_proxy: str | None = None,
) -> dict[str, Any]:
    if not confirm_live:
        if live:
            raise RuntimeError("--live requires --confirm-live")
    cmd = weather_order_executor_cmd(
        root=root,
        plans_path=plans_path,
        paper_out=paper_out,
        live_out=live_out,
        live=live,
        confirm_live=confirm_live,
        allow_taker=allow_taker,
        cancel_expired=cancel_expired,
        no_telegram=no_telegram,
        python_executable=python_executable,
        market_proxy=market_proxy,
    )
    proc = subprocess.run(
        cmd,
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_sec,
    )
    parsed = parse_executor_json(proc.stdout)
    output_tail = proc.stdout[-8000:]
    return {
        "executor_cmd": cmd,
        "executor_returncode": proc.returncode,
        "executor_output": output_tail,
        "executor_output_tail": output_tail,
        "executor_result": parsed,
        "cmd": cmd,
        "returncode": proc.returncode,
        "output_tail": output_tail,
        "parsed": parsed,
    }


class RuntimeVenue(Protocol):
    def fetch_market_book(self, token_id: str) -> MarketBook: ...
    def fetch_capabilities(self) -> VenueCapabilities: ...
    def fetch_fee_schedule(self) -> FeeSchedule: ...
    def place(self, *, intent: ExecutionIntent, child: ChildOrderPlan, market_book: MarketBook, capabilities: VenueCapabilities, fee_schedule: FeeSchedule | None, replacement_of: RestingOrderState | None = None) -> Mapping[str, Any]: ...
    def cancel(self, order_state: RestingOrderState) -> Mapping[str, Any]: ...
    def fetch_order_state(self, order_id: str | None, client_order_id: str) -> RestingOrderState | None: ...
    def reconcile_unknown(self, *, kind: str, identity_key: str) -> Mapping[str, Any] | None: ...


class RuntimeRisk(Protocol):
    def check(self, *, stage: str, payload: Mapping[str, Any]) -> bool | Mapping[str, Any]: ...


Planner = Callable[[ExecutionIntent, Any, MarketBook, VenueCapabilities, FeeSchedule | None, datetime], list[ChildOrderPlan]]
ReplacementPlanner = Callable[[RestingOrderState, Any, Decimal], tuple[ExecutionIntent, ChildOrderPlan]]


@dataclass(frozen=True)
class LifecycleWorkItem:
    order_state: RestingOrderState
    lifecycle_context: LifecycleContext


@dataclass(frozen=True)
class RuntimeActionResult:
    status: str
    reason: str
    identity_key: str | None = None
    root_order_id: str | None = None
    action: str | None = None
    payload: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ExecutionRuntimeResult:
    status: str
    actions: tuple[RuntimeActionResult, ...]


def _allowed(result: bool | Mapping[str, Any]) -> bool:
    return result if isinstance(result, bool) else bool(result.get("allowed", False))


def _reconciliation_status(payload: Mapping[str, Any] | None) -> str:
    return str((payload or {}).get("status") or "").lower()


_CANCEL_FINAL_STATUSES = frozenset({"cancelled", "canceled", "expired"})


class OrderRuntime:
    """Injected Phase 4 orchestrator; it has no venue/network implementation of its own."""

    def __init__(
        self,
        *,
        venue: RuntimeVenue,
        risk: RuntimeRisk,
        journal: ExecutionJournal,
        planner: Planner,
        replacement_planner: ReplacementPlanner | None = None,
        profile_resolver: Callable[[str], Any] = get_execution_profile,
    ) -> None:
        self.venue = venue
        self.risk = risk
        self.journal = journal
        self.planner = planner
        self.replacement_planner = replacement_planner
        self.profile_resolver = profile_resolver

    def _risk_allows(self, *, stage: str, payload: Mapping[str, Any]) -> bool:
        return _allowed(self.risk.check(stage=stage, payload=payload))

    def _gate(self, run_context: ExecutionRunContext) -> RuntimeActionResult | None:
        try:
            run_context.validate_submission()
        except Exception as exc:
            return RuntimeActionResult(status="blocked", reason=f"run_context_gate:{exc}")
        return None

    def _submit_once(
        self,
        *,
        intent: ExecutionIntent,
        child: ChildOrderPlan,
        run_context: ExecutionRunContext,
        replacement_of: RestingOrderState | None = None,
    ) -> RuntimeActionResult:
        identity_key = child.intent.plan_dedupe_key + ":" + child.child_role
        exposure_key = child.intent.live_exposure_key + ":" + child.child_role
        capabilities = self.venue.fetch_capabilities()
        fee_schedule = self.venue.fetch_fee_schedule()
        book = self.venue.fetch_market_book(intent.token_id)
        risk_payload = {
            "intent": intent,
            "child": child,
            "replacement_of": replacement_of,
            "open_or_reserved_exposure": self.journal.open_or_reserved_exposure(),
        }
        stage = "replacement" if replacement_of is not None else "initial_child"
        if not self._risk_allows(stage=stage, payload=risk_payload):
            return RuntimeActionResult(status="blocked", reason="risk_recheck_rejected", identity_key=identity_key)
        self.journal.record_attempt({"identity_key": identity_key, "kind": "submit", "root_order_id": None if replacement_of is None else replacement_of.root_order_id, "source_order_id": None if replacement_of is None else replacement_of.source_order_id, "child_role": child.child_role})
        outcome = dict(self.venue.place(intent=intent, child=child, market_book=book, capabilities=capabilities, fee_schedule=fee_schedule, replacement_of=replacement_of))
        status = str(outcome.get("status") or "unknown").lower()
        if status == "unknown":
            self.journal.record_outcome({"identity_key": identity_key, "live_exposure_key": exposure_key, "kind": "submit", "status": "unknown", **outcome})
            reconciled = self.venue.reconcile_unknown(kind="submit", identity_key=identity_key)
            reconciled_status = _reconciliation_status(reconciled)
            if reconciled and reconciled_status in {"submitted", "accepted", "filled"}:
                self.journal.record_outcome({"identity_key": identity_key, "live_exposure_key": exposure_key, "kind": "submit", **dict(reconciled), "status": "reconciled"})
                return RuntimeActionResult(status="submitted", reason="unknown_submit_reconciled", identity_key=identity_key, payload=reconciled)
            if reconciled and reconciled_status in {"not_found", "retry_permitted"}:
                self.journal.record_outcome({"identity_key": identity_key, "live_exposure_key": exposure_key, "kind": "submit", **dict(reconciled), "status": "retry_permitted"})
                return RuntimeActionResult(status="unknown", reason="unknown_submit_reconciled_retry_permitted", identity_key=identity_key, payload=reconciled)
            return RuntimeActionResult(status="unknown", reason="unknown_submit_requires_reconciliation", identity_key=identity_key, payload=outcome)
        self.journal.record_outcome({"identity_key": identity_key, "live_exposure_key": exposure_key, "kind": "submit", "status": status, **outcome})
        return RuntimeActionResult(status="submitted" if status in {"submitted", "accepted", "filled"} else "failed", reason="venue_submit_result", identity_key=identity_key, payload=outcome)

    def submit_intent(self, intent: ExecutionIntent, run_context: ExecutionRunContext) -> ExecutionRuntimeResult:
        gate = self._gate(run_context)
        if gate is not None:
            return ExecutionRuntimeResult(status="blocked", actions=(gate,))
        profile = self.profile_resolver(intent.resolved_execution_profile)
        capabilities = self.venue.fetch_capabilities()
        fee_schedule = self.venue.fetch_fee_schedule()
        book = self.venue.fetch_market_book(intent.token_id)
        invoked_at = parse_utc(
            run_context.invoked_at_utc, field="execution_run_invoked_at_utc"
        )
        assert invoked_at is not None
        children = self.planner(
            intent, profile, book, capabilities, fee_schedule, invoked_at
        )
        aggregate = {"intent": intent, "children": tuple(children), "open_or_reserved_exposure": self.journal.open_or_reserved_exposure()}
        if not self._risk_allows(stage="initial_aggregate", payload=aggregate):
            return ExecutionRuntimeResult(status="blocked", actions=(RuntimeActionResult(status="blocked", reason="aggregate_risk_rejected"),))
        actions: list[RuntimeActionResult] = []
        for child in children:
            plan_key = child.intent.plan_dedupe_key + ":" + child.child_role
            exposure_key = child.intent.live_exposure_key + ":" + child.child_role
            if not self.journal.claim_plan(plan_key, run_context.runtime_owner, {"intent": intent.to_json(), "child_role": child.child_role}):
                actions.append(RuntimeActionResult(status="blocked", reason="plan_dedupe_claim_not_acquired", identity_key=plan_key))
                continue
            if not self.journal.reserve_live_exposure(exposure_key, run_context.runtime_owner, {"plan_dedupe_key": plan_key}):
                actions.append(RuntimeActionResult(status="blocked", reason="live_exposure_reservation_not_acquired", identity_key=plan_key))
                continue
            actions.append(self._submit_once(intent=intent, child=child, run_context=run_context))
        return ExecutionRuntimeResult(status="ok", actions=tuple(actions))

    def submit_preplanned(
        self,
        intent: ExecutionIntent,
        child: ChildOrderPlan,
        run_context: ExecutionRunContext,
    ) -> ExecutionRuntimeResult:
        """Submit one already-planned compatibility child through shared controls."""

        gate = self._gate(run_context)
        if gate is not None:
            return ExecutionRuntimeResult(status="blocked", actions=(gate,))
        if child.intent != intent:
            return ExecutionRuntimeResult(
                status="blocked",
                actions=(RuntimeActionResult(status="blocked", reason="preplanned_child_intent_mismatch"),),
            )
        aggregate = {
            "intent": intent,
            "children": (child,),
            "open_or_reserved_exposure": self.journal.open_or_reserved_exposure(),
        }
        if not self._risk_allows(stage="initial_aggregate", payload=aggregate):
            return ExecutionRuntimeResult(
                status="blocked",
                actions=(RuntimeActionResult(status="blocked", reason="aggregate_risk_rejected"),),
            )
        plan_key = child.intent.plan_dedupe_key + ":" + child.child_role
        exposure_key = child.intent.live_exposure_key + ":" + child.child_role
        if not self.journal.claim_plan(
            plan_key,
            run_context.runtime_owner,
            {"intent": intent.to_json(), "child_role": child.child_role},
        ):
            return ExecutionRuntimeResult(
                status="blocked",
                actions=(RuntimeActionResult(status="blocked", reason="plan_dedupe_claim_not_acquired", identity_key=plan_key),),
            )
        if not self.journal.reserve_live_exposure(
            exposure_key,
            run_context.runtime_owner,
            {"plan_dedupe_key": plan_key},
        ):
            return ExecutionRuntimeResult(
                status="blocked",
                actions=(RuntimeActionResult(status="blocked", reason="live_exposure_reservation_not_acquired", identity_key=plan_key),),
            )
        action = self._submit_once(intent=intent, child=child, run_context=run_context)
        return ExecutionRuntimeResult(status="ok", actions=(action,))

    def manage_active_orders(
        self,
        owner: str,
        lifecycle_contexts: Iterable[LifecycleWorkItem],
        run_context: ExecutionRunContext,
        now: datetime,
    ) -> ExecutionRuntimeResult:
        gate = self._gate(run_context)
        if gate is not None:
            return ExecutionRuntimeResult(status="blocked", actions=(gate,))
        actions: list[RuntimeActionResult] = []
        for item in lifecycle_contexts:
            order = self.venue.fetch_order_state(item.order_state.order_id, item.order_state.client_order_id)
            if order is None:
                actions.append(RuntimeActionResult(status="blocked", reason="authoritative_order_state_missing"))
                continue
            if order.lifecycle_owner and order.lifecycle_owner != owner:
                actions.append(RuntimeActionResult(status="blocked", reason="lifecycle_owner_mismatch", root_order_id=order.root_order_id))
                continue
            profile = self.profile_resolver(order.execution_profile)
            book = self.venue.fetch_market_book(order.token_id)
            decision = evaluate_order_lifecycle(profile=profile, order_state=order, market_book=book, lifecycle_context=item.lifecycle_context, now_utc=now)
            if decision.action in {"REST", "TERMINAL"}:
                actions.append(
                    RuntimeActionResult(
                        status="noop",
                        reason=decision.reason,
                        root_order_id=decision.root_order_id,
                        action=decision.action,
                        payload=(
                            {"order_state": order.to_json()}
                            if decision.action == "TERMINAL"
                            else None
                        ),
                    )
                )
                continue
            if not decision.lifecycle_action_id or not self.journal.claim_lifecycle_action(decision.lifecycle_action_id, owner, decision.to_json()):
                actions.append(RuntimeActionResult(status="blocked", reason="lifecycle_action_claim_not_acquired", root_order_id=decision.root_order_id, action=decision.action))
                continue
            self.journal.record_attempt({"identity_key": decision.lifecycle_action_id, "kind": decision.action.lower(), "root_order_id": decision.root_order_id, "source_order_id": decision.source_order_id})
            if decision.action == "CANCEL":
                outcome = dict(self.venue.cancel(order))
                status = str(outcome.get("status") or "unknown").lower()
                self.journal.record_outcome({"identity_key": decision.lifecycle_action_id, "kind": "cancel", "status": status, **outcome})
                if status == "unknown":
                    reconciled = self.venue.reconcile_unknown(kind="cancel", identity_key=decision.lifecycle_action_id)
                    reconciled_status = _reconciliation_status(reconciled)
                    if reconciled and reconciled_status in {"cancelled", "canceled", "expired"}:
                        self.journal.record_outcome({"identity_key": decision.lifecycle_action_id, "kind": "cancel", **dict(reconciled), "status": "reconciled"})
                    elif reconciled and reconciled_status in {"not_found", "retry_permitted"}:
                        self.journal.record_outcome({"identity_key": decision.lifecycle_action_id, "kind": "cancel", **dict(reconciled), "status": "retry_permitted"})
                        actions.append(RuntimeActionResult(status="unknown", reason="unknown_cancel_reconciled_retry_permitted", root_order_id=decision.root_order_id, action="CANCEL"))
                        continue
                    else:
                        actions.append(RuntimeActionResult(status="unknown", reason="unknown_cancel_requires_reconciliation", root_order_id=decision.root_order_id, action="CANCEL"))
                        continue
                actions.append(RuntimeActionResult(status="cancelled", reason="cancel_attempt_recorded", root_order_id=decision.root_order_id, action="CANCEL", payload=outcome))
                continue
            if decision.action == "TAKER_FALLBACK":
                final_order = order
            else:
                cancel_outcome = dict(self.venue.cancel(order))
                cancel_status = str(cancel_outcome.get("status") or "unknown").lower()
                self.journal.record_outcome({"identity_key": decision.lifecycle_action_id, "kind": "cancel_before_replacement", "status": cancel_status, **cancel_outcome})
                if cancel_status == "unknown":
                    reconciled = self.venue.reconcile_unknown(kind="cancel", identity_key=decision.lifecycle_action_id)
                    reconciled_status = _reconciliation_status(reconciled)
                    if reconciled and reconciled_status in {"cancelled", "canceled", "expired"}:
                        self.journal.record_outcome({"identity_key": decision.lifecycle_action_id, "kind": "cancel_before_replacement", **dict(reconciled), "status": "reconciled"})
                    elif reconciled and reconciled_status in {"not_found", "retry_permitted"}:
                        self.journal.record_outcome({"identity_key": decision.lifecycle_action_id, "kind": "cancel_before_replacement", **dict(reconciled), "status": "retry_permitted"})
                        actions.append(RuntimeActionResult(status="unknown", reason="unknown_cancel_reconciled_retry_permitted", root_order_id=decision.root_order_id, action=decision.action))
                        continue
                    else:
                        actions.append(RuntimeActionResult(status="unknown", reason="unknown_cancel_requires_reconciliation", root_order_id=decision.root_order_id, action=decision.action))
                        continue
                elif cancel_status not in _CANCEL_FINAL_STATUSES:
                    actions.append(RuntimeActionResult(status="blocked", reason="cancel_before_replacement_not_final", root_order_id=decision.root_order_id, action=decision.action, payload=cancel_outcome))
                    continue
                final_order = self.venue.fetch_order_state(order.order_id, order.client_order_id)
            if final_order is None:
                actions.append(RuntimeActionResult(status="blocked", reason="post_cancel_authoritative_state_missing", root_order_id=decision.root_order_id, action=decision.action))
                continue
            if final_order.status.lower().strip() not in _CANCEL_FINAL_STATUSES or not final_order.cancel_confirmed:
                actions.append(RuntimeActionResult(status="blocked", reason="replacement_requires_authoritative_final_cancel", root_order_id=final_order.root_order_id, action=decision.action))
                continue
            replacement = reconcile_replacement(order_state=final_order, minimum_order_shares=book.minimum_order_shares, require_cancel_confirmation=True, action=decision.action, normalized_target_price=decision.replacement_price, data_epoch_ref=item.lifecycle_context.data_epoch_ref, book_epoch_ref=book.book_epoch_ref)
            if replacement.status != "ready" or replacement.authoritative_remaining_shares is None:
                actions.append(RuntimeActionResult(status="blocked", reason=replacement.reason, root_order_id=replacement.root_order_id, action=decision.action))
                continue
            if self.replacement_planner is None:
                actions.append(RuntimeActionResult(status="blocked", reason="replacement_requires_injected_child_plan", root_order_id=replacement.root_order_id, action=decision.action))
                continue
            intent, child = self.replacement_planner(final_order, decision, replacement.authoritative_remaining_shares)
            if child.requested_shares != replacement.authoritative_remaining_shares:
                actions.append(RuntimeActionResult(status="blocked", reason="replacement_child_shares_do_not_match_authoritative_remaining", root_order_id=replacement.root_order_id, action=decision.action))
                continue
            submitted = self._submit_once(intent=intent, child=child, run_context=run_context, replacement_of=final_order)
            actions.append(RuntimeActionResult(status=submitted.status, reason=submitted.reason, identity_key=submitted.identity_key, root_order_id=replacement.root_order_id, action=decision.action, payload=submitted.payload))
        return ExecutionRuntimeResult(status="ok", actions=tuple(actions))
