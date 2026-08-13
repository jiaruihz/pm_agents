"""Sol-owned persistence for WorkOrders; workers never write this state directly."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
from pathlib import Path
import uuid
from typing import Iterator

from ..contracts import RunStatus, utc_now
from ..evidence import EvidenceStore
from .contracts import (
    AgentRunRecord,
    ORCHESTRATION_SCHEMA_VERSION,
    OrchestrationState,
    RoleSpec,
    RouteDecision,
    RouteLevel,
    WorkOrder,
    WorkResult,
    WorkStatus,
)
from .dependency import DependencyResolver
from .pricing import models_match, price_usage


class OrchestrationStore:
    """Single-coordinator state store with attempt/lease result fencing."""

    def __init__(self, evidence_store: EvidenceStore):
        self.evidence_store = evidence_store
        self.path = evidence_store.run_dir / "orchestration.json"
        self.lock_path = evidence_store.run_dir / ".orchestration.lock"
        self.resolver = DependencyResolver()

    def initialize(
        self, route: RouteDecision, roles: tuple[RoleSpec, ...]
    ) -> OrchestrationState:
        if route.level == RouteLevel.DIRECT:
            raise ValueError("L0 direct tasks do not create an orchestration run")
        if len({item.name for item in roles}) != len(roles):
            raise ValueError("role names must be unique")
        with self._lock():
            if self.path.exists():
                return self.load()
            state = OrchestrationState(
                run_id=self.evidence_store.load_task().run_id,
                route=route,
                roles=roles,
            )
            self._save(state)
            self.evidence_store.append(
                "orchestration_initialized",
                phase=self.evidence_store.load_state().phase,
                payload={"route": route.model_dump(mode="json")},
            )
            return state

    def load(self) -> OrchestrationState:
        return OrchestrationState.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def add_work_orders(self, *orders: WorkOrder) -> OrchestrationState:
        with self._lock():
            state = self.load()
            role_names = {item.name for item in state.roles}
            unknown_roles = sorted({item.role for item in orders} - role_names)
            if unknown_roles:
                raise ValueError(f"unknown roles: {unknown_roles}")
            task_acceptance = set(self.evidence_store.load_task().acceptance)
            unknown_acceptance = sorted(
                {
                    key
                    for order in orders
                    for key in order.closes_acceptance
                    if key not in task_acceptance
                }
            )
            if unknown_acceptance:
                raise ValueError(
                    f"unknown parent acceptance conditions: {unknown_acceptance}"
                )
            combined = (*state.work_orders, *orders)
            refreshed = self.resolver.refresh(combined)
            state = state.model_copy(
                update={"work_orders": refreshed, "updated_at_utc": utc_now()}
            )
            self._save(state)
            for item in orders:
                self.evidence_store.append(
                    "work_order_created",
                    phase=self.evidence_store.load_state().phase,
                    payload=item.model_dump(mode="json"),
                )
            return state

    def start(
        self,
        work_order_id: str,
        *,
        thread_id: str | None = None,
        observed_model: str | None = None,
        now_utc: str | None = None,
    ) -> tuple[OrchestrationState, WorkOrder, AgentRunRecord]:
        with self._lock():
            state = self.load()
            state, _ = self._reap_expired_locked(state, now_utc=now_utc)
            order = self._order(state, work_order_id)
            if order.status != WorkStatus.READY:
                raise RuntimeError(f"work order is not ready: {order.status.value}")
            if order.attempt >= order.max_attempts:
                raise RuntimeError("work order attempt budget exhausted")
            role = self._role(state, order.role)
            if role.require_exact_model:
                if not observed_model:
                    raise ValueError("exact-model role requires an observed dispatch model")
                if not models_match(role.requested_model, observed_model):
                    raise ValueError(
                        "dispatch model mismatch: requested "
                        f"{role.requested_model}, observed {observed_model}"
                    )
            if thread_id and any(item.thread_id == thread_id for item in state.agent_runs):
                raise ValueError(
                    "thread_id already belongs to another attempt; dedicated threads "
                    "are required for unambiguous token accounting"
                )
            lease_id = uuid.uuid4().hex
            started_at = now_utc or utc_now()
            lease_expires_at = self._plus_seconds(
                started_at, order.lease_timeout_seconds
            )
            started = order.model_copy(
                update={
                    "status": WorkStatus.RUNNING,
                    "attempt": order.attempt + 1,
                    "owner": role.name,
                    "lease_id": lease_id,
                    "last_heartbeat_at_utc": started_at,
                    "lease_expires_at_utc": lease_expires_at,
                }
            )
            record = AgentRunRecord(
                work_order_id=work_order_id,
                role=role.name,
                requested_model=role.requested_model,
                attempt=started.attempt,
                lease_id=lease_id,
                observed_model=observed_model,
                reasoning_effort=role.reasoning_effort,
                thread_id=thread_id,
                started_at_utc=started_at,
                last_heartbeat_at_utc=started_at,
            )
            state = state.model_copy(
                update={
                    "work_orders": self._replace(state.work_orders, started),
                    "agent_runs": (*state.agent_runs, record),
                    "updated_at_utc": utc_now(),
                }
            )
            self._save(state)
            self.evidence_store.append(
                "work_order_dispatched",
                phase=self.evidence_store.load_state().phase,
                payload={
                    "work_order_id": work_order_id,
                    "attempt": started.attempt,
                    "lease_id": lease_id,
                    "role": role.name,
                    "requested_model": role.requested_model,
                    "observed_model": observed_model,
                    "thread_id": thread_id,
                    "lease_expires_at_utc": lease_expires_at,
                    "heartbeat_interval_seconds": order.heartbeat_interval_seconds,
                },
            )
            return state, started, record

    def heartbeat(
        self,
        work_order_id: str,
        *,
        attempt: int,
        lease_id: str,
        now_utc: str | None = None,
    ) -> OrchestrationState:
        """Renew one live lease; stale workers cannot revive an expired attempt."""

        with self._lock():
            state = self.load()
            state, expired = self._reap_expired_locked(state, now_utc=now_utc)
            if work_order_id in expired:
                raise RuntimeError("work order lease expired and was reaped")
            order = self._order(state, work_order_id)
            if not (
                order.status == WorkStatus.RUNNING
                and order.attempt == attempt
                and order.lease_id == lease_id
            ):
                raise RuntimeError("heartbeat does not match the active lease")
            heartbeat_at = now_utc or utc_now()
            renewed = order.model_copy(
                update={
                    "last_heartbeat_at_utc": heartbeat_at,
                    "lease_expires_at_utc": self._plus_seconds(
                        heartbeat_at, order.lease_timeout_seconds
                    ),
                }
            )
            agent_runs = self._update_agent_run(
                state,
                order,
                {"last_heartbeat_at_utc": heartbeat_at},
            )
            state = state.model_copy(
                update={
                    "work_orders": self._replace(state.work_orders, renewed),
                    "agent_runs": agent_runs,
                    "updated_at_utc": utc_now(),
                }
            )
            self._save(state)
            self.evidence_store.append(
                "work_order_heartbeat",
                phase=self.evidence_store.load_state().phase,
                payload={
                    "work_order_id": work_order_id,
                    "attempt": attempt,
                    "lease_id": lease_id,
                    "heartbeat_at_utc": heartbeat_at,
                    "lease_expires_at_utc": renewed.lease_expires_at_utc,
                },
            )
            return state

    def reap_expired(
        self, *, now_utc: str | None = None
    ) -> tuple[OrchestrationState, tuple[str, ...]]:
        """Turn expired running attempts into fenced failure results."""

        with self._lock():
            return self._reap_expired_locked(self.load(), now_utc=now_utc)

    def record_result(self, result: WorkResult) -> tuple[OrchestrationState, bool]:
        with self._lock():
            state = self.load()
            state, _ = self._reap_expired_locked(state)
            return self._record_result_locked(state, result)

    def record_runtime_exit(
        self,
        work_order_id: str,
        *,
        attempt: int,
        lease_id: str,
        runtime_status: str,
        summary: str | None = None,
        now_utc: str | None = None,
    ) -> tuple[OrchestrationState, bool]:
        """Callback for interrupted/cancelled/crashed agent threads."""

        if runtime_status not in {"interrupted", "cancelled", "crashed", "lost"}:
            raise ValueError("runtime_status must describe an abnormal terminal exit")
        result = WorkResult(
            work_order_id=work_order_id,
            attempt=attempt,
            lease_id=lease_id,
            status="failed",
            summary=summary or f"agent runtime exited: {runtime_status}",
            unresolved=(f"runtime_exit:{runtime_status}",),
        )
        with self._lock():
            state = self.load()
            state, _ = self._reap_expired_locked(state, now_utc=now_utc)
            return self._record_result_locked(
                state,
                result,
                event_type="agent_runtime_exit_recorded",
                terminal_reason=runtime_status,
            )

    def _record_result_locked(
        self,
        state: OrchestrationState,
        result: WorkResult,
        *,
        event_type: str = "work_result_recorded",
        terminal_reason: str | None = None,
    ) -> tuple[OrchestrationState, bool]:
        order = self._order(state, result.work_order_id)
        duplicate = any(
            item.work_order_id == result.work_order_id
            and item.attempt == result.attempt
            and item.lease_id == result.lease_id
            for item in state.results
        )
        if duplicate:
            return state, False
        current = (
            order.status == WorkStatus.RUNNING
            and order.attempt == result.attempt
            and order.lease_id == result.lease_id
        )
        if not current:
            self.evidence_store.append(
                "stale_work_result_ignored",
                phase=self.evidence_store.load_state().phase,
                payload=result.model_dump(mode="json"),
            )
            return state, False
        role = self._role(state, order.role)
        active_record = next(
            item
            for item in reversed(state.agent_runs)
            if item.work_order_id == order.work_order_id
            and item.attempt == order.attempt
            and item.lease_id == order.lease_id
        )
        observed_model = result.observed_model or active_record.observed_model
        if result.status == "succeeded" and role.require_exact_model:
            if not observed_model:
                raise ValueError("successful result requires an observed model")
            if not models_match(role.requested_model, observed_model):
                raise ValueError(
                    "result model mismatch: requested "
                    f"{role.requested_model}, observed {observed_model}"
                )
        usage = result.usage
        if result.status == "succeeded" and usage is not None and usage.source != "unavailable":
            if not observed_model:
                raise ValueError("measured usage requires an observed model")
            usage = price_usage(
                usage,
                model=observed_model,
                baseline_model=role.baseline_model,
                fast_mode=usage.service_tier == "fast",
            )
        result = result.model_copy(
            update={"observed_model": observed_model, "usage": usage}
        )
        if result.status == "succeeded":
            next_status = WorkStatus.REVIEW
        elif result.status == "blocked":
            next_status = WorkStatus.BLOCKED
        elif order.attempt < order.max_attempts:
            next_status = WorkStatus.PENDING
        else:
            next_status = WorkStatus.FAILED
        updated = order.model_copy(
            update={
                "status": next_status,
                "result_ref": f"work-result:{order.work_order_id}:{order.attempt}",
            }
        )
        agent_runs = list(state.agent_runs)
        for index in range(len(agent_runs) - 1, -1, -1):
            record = agent_runs[index]
            if (
                record.work_order_id == order.work_order_id
                and record.finished_at_utc is None
            ):
                if record.attempt not in {0, order.attempt}:
                    continue
                if record.lease_id not in {None, order.lease_id}:
                    continue
                agent_runs[index] = record.model_copy(
                    update={
                        "observed_model": result.observed_model
                        or record.observed_model,
                        "execution_mode": result.execution_mode,
                        "finished_at_utc": result.finished_at_utc,
                        "terminal_reason": terminal_reason or result.status,
                        "duration_seconds": result.duration_seconds,
                        "tool_calls": result.tool_calls,
                        "usage": result.usage,
                    }
                )
                break
        state = state.model_copy(
            update={
                "work_orders": self.resolver.refresh(
                    self._replace(state.work_orders, updated)
                ),
                "results": (*state.results, result),
                "agent_runs": tuple(agent_runs),
                "updated_at_utc": utc_now(),
            }
        )
        self._save(state)
        self.evidence_store.append(
            event_type,
            phase=self.evidence_store.load_state().phase,
            payload=result.model_dump(mode="json"),
        )
        return state, True

    def accept(
        self, work_order_id: str, *, verified_acceptance: tuple[str, ...]
    ) -> OrchestrationState:
        with self._lock():
            state = self.load()
            order = self._order(state, work_order_id)
            if order.status != WorkStatus.REVIEW:
                raise RuntimeError("only a reviewed worker result can be accepted")
            missing = sorted(set(order.acceptance) - set(verified_acceptance))
            if missing:
                raise ValueError(f"unverified acceptance conditions: {missing}")
            result = next(
                item
                for item in reversed(state.results)
                if item.work_order_id == work_order_id
                and item.attempt == order.attempt
                and item.lease_id == order.lease_id
            )
            if not result.evidence_refs:
                raise ValueError("accepted work requires durable evidence_refs")
            role = self._role(state, order.role)
            if role.require_usage and (
                result.usage is None or result.usage.source == "unavailable"
            ):
                raise ValueError("accepted work requires measured token usage")
            missing_refs = [
                ref for ref in result.evidence_refs if not self._evidence_exists(ref)
            ]
            if missing_refs:
                raise ValueError(f"missing evidence refs: {missing_refs}")
            complete = order.model_copy(update={"status": WorkStatus.COMPLETE})
            state = state.model_copy(
                update={
                    "work_orders": self.resolver.refresh(
                        self._replace(state.work_orders, complete)
                    ),
                    "updated_at_utc": utc_now(),
                }
            )
            self._save(state)
            self.evidence_store.append(
                "work_order_accepted",
                phase=self.evidence_store.load_state().phase,
                payload={
                    "work_order_id": work_order_id,
                    "attempt": order.attempt,
                    "verified_acceptance": list(verified_acceptance),
                },
            )
            self._sync_parent_acceptance(complete)
            return state

    def ready(self) -> tuple[WorkOrder, ...]:
        state, _ = self.reap_expired()
        return tuple(
            item for item in state.work_orders if item.status == WorkStatus.READY
        )

    def _reap_expired_locked(
        self,
        state: OrchestrationState,
        *,
        now_utc: str | None = None,
    ) -> tuple[OrchestrationState, tuple[str, ...]]:
        now = self._parse_utc(now_utc or utc_now())
        expired_ids = tuple(
            item.work_order_id
            for item in state.work_orders
            if item.status == WorkStatus.RUNNING
            and item.lease_expires_at_utc is not None
            and self._parse_utc(item.lease_expires_at_utc) <= now
        )
        for work_order_id in expired_ids:
            order = self._order(state, work_order_id)
            self.evidence_store.append(
                "work_lease_expired",
                phase=self.evidence_store.load_state().phase,
                payload={
                    "work_order_id": order.work_order_id,
                    "attempt": order.attempt,
                    "lease_id": order.lease_id,
                    "lease_expires_at_utc": order.lease_expires_at_utc,
                    "reaped_at_utc": self._format_utc(now),
                },
            )
            result = WorkResult(
                work_order_id=order.work_order_id,
                attempt=order.attempt,
                lease_id=order.lease_id or "",
                status="failed",
                summary="worker lease expired without a heartbeat or terminal result",
                unresolved=("lease_expired",),
                finished_at_utc=self._format_utc(now),
            )
            state, _ = self._record_result_locked(
                state,
                result,
                event_type="expired_work_result_recorded",
                terminal_reason="lease_expired",
            )
        return state, expired_ids

    def _sync_parent_acceptance(self, order: WorkOrder) -> None:
        if not order.closes_acceptance:
            return
        task = self.evidence_store.load_task()
        run_state = self.evidence_store.load_state()
        run_state.mark_acceptance(*order.closes_acceptance)
        unmet = tuple(
            key for key in task.acceptance if key not in run_state.completed_acceptance
        )
        if task.domain is not None and task.domain.auto_complete_on_acceptance and not unmet:
            run_state.phase = task.domain.terminal_phase
            run_state.completion_state = task.domain.terminal_state
            run_state.status = RunStatus.COMPLETE
        self.evidence_store.append(
            "parent_acceptance_updated",
            phase=run_state.phase,
            payload={
                "work_order_id": order.work_order_id,
                "closed": list(order.closes_acceptance),
                "unmet": list(unmet),
                "completion_state": run_state.completion_state.value,
            },
        )
        self.evidence_store.save_state(run_state)

    @staticmethod
    def _update_agent_run(
        state: OrchestrationState,
        order: WorkOrder,
        updates: dict,
    ) -> tuple[AgentRunRecord, ...]:
        records = list(state.agent_runs)
        for index in range(len(records) - 1, -1, -1):
            record = records[index]
            if (
                record.work_order_id == order.work_order_id
                and record.attempt in {0, order.attempt}
                and record.lease_id in {None, order.lease_id}
                and record.finished_at_utc is None
            ):
                records[index] = record.model_copy(update=updates)
                break
        return tuple(records)

    @staticmethod
    def _parse_utc(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _plus_seconds(cls, value: str, seconds: int) -> str:
        return cls._format_utc(cls._parse_utc(value) + timedelta(seconds=seconds))

    @staticmethod
    def _format_utc(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _evidence_exists(self, reference: str) -> bool:
        path = Path(reference)
        choices = (
            path if path.is_absolute() else self.evidence_store.run_dir / path,
            self.evidence_store.artifacts_dir / path,
        )
        return any(item.resolve().is_file() for item in choices)

    @staticmethod
    def _order(state: OrchestrationState, work_order_id: str) -> WorkOrder:
        try:
            return next(
                item for item in state.work_orders if item.work_order_id == work_order_id
            )
        except StopIteration as exc:
            raise KeyError(f"unknown work order: {work_order_id}") from exc

    @staticmethod
    def _role(state: OrchestrationState, name: str) -> RoleSpec:
        return next(item for item in state.roles if item.name == name)

    @staticmethod
    def _replace(
        work_orders: tuple[WorkOrder, ...], replacement: WorkOrder
    ) -> tuple[WorkOrder, ...]:
        return tuple(
            replacement if item.work_order_id == replacement.work_order_id else item
            for item in work_orders
        )

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _save(self, state: OrchestrationState) -> None:
        if state.schema_version != ORCHESTRATION_SCHEMA_VERSION:
            state = state.model_copy(
                update={"schema_version": ORCHESTRATION_SCHEMA_VERSION}
            )
        self.evidence_store._write_json_atomic(
            self.path, state.model_dump(mode="json")
        )


__all__ = ["OrchestrationStore"]
