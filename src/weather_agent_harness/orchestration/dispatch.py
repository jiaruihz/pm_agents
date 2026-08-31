"""Thin Codex dispatch boundary; Codex itself owns agent threads."""

from __future__ import annotations

from typing import Any

from .contracts import RoleSpec, WorkOrder
from .execution import compile_execution_profile
from .store import OrchestrationStore


class CodexDispatchAdapter:
    """Build a bounded worker prompt and record the external Codex thread identity."""

    def instruction(self, order: WorkOrder, role: RoleSpec) -> dict[str, Any]:
        profile = compile_execution_profile(role, order)
        task_identity = "_".join(
            item for item in (order.parent_run_id, order.work_order_id) if item
        )
        return {
            "task_name": task_identity.replace("-", "_"),
            "fork_turns": "none",
            "agent_type": profile.agent_type,
            "model": role.requested_model,
            "reasoning_effort": role.reasoning_effort,
            "execution_profile": profile.model_dump(mode="json"),
            "message": (
                f"WorkOrder {order.work_order_id}\n"
                f"Objective: {order.objective}\n"
                f"Scope: {order.scope}\n"
                f"Acceptance: {list(order.acceptance)}\n"
                f"Risk: {order.risk.value}\n"
                f"Native sandbox: {profile.sandbox_mode}; network: disabled.\n"
                f"Scheduling write owners (not a sandbox boundary): "
                f"{list(order.write_owners)}\n"
                f"Lease timeout: {order.lease_timeout_seconds}s; heartbeat every "
                f"{order.heartbeat_interval_seconds}s once the coordinator returns "
                "the attempt and lease_id.\n"
                "Return a structured WorkResult with evidence references. "
                "On an abnormal runtime exit, the dispatch integration must invoke "
                "the terminal callback. Do not expand scope or declare the parent run complete."
            ),
        }

    def prepare(
        self,
        store: OrchestrationStore,
        work_order_id: str,
    ) -> tuple[WorkOrder, RoleSpec, dict[str, Any]]:
        state, _ = store.reap_expired()
        order = next(
            item for item in state.work_orders if item.work_order_id == work_order_id
        )
        store.assert_dispatch_authority(order)
        role = next(item for item in state.roles if item.name == order.role)
        return order, role, self.instruction(order, role)

    def record_spawn(
        self,
        store: OrchestrationStore,
        work_order_id: str,
        *,
        thread_id: str,
        observed_model: str | None,
    ) -> WorkOrder:
        _, order, _ = store.start(
            work_order_id,
            thread_id=thread_id,
            observed_model=observed_model,
        )
        return order

    def record_terminal(
        self,
        store: OrchestrationStore,
        work_order_id: str,
        *,
        attempt: int,
        lease_id: str,
        runtime_status: str,
        summary: str | None = None,
    ) -> bool:
        _, accepted = store.record_runtime_exit(
            work_order_id,
            attempt=attempt,
            lease_id=lease_id,
            runtime_status=runtime_status,
            summary=summary,
        )
        return accepted


__all__ = ["CodexDispatchAdapter"]
