"""Small dependency resolver; deliberately not a general workflow engine."""

from __future__ import annotations

from .contracts import WorkOrder, WorkStatus


class DependencyResolver:
    def validate(self, work_orders: tuple[WorkOrder, ...]) -> None:
        by_id = {item.work_order_id: item for item in work_orders}
        if len(by_id) != len(work_orders):
            raise ValueError("work_order_id values must be unique")
        for item in work_orders:
            missing = sorted(set(item.depends_on) - set(by_id))
            if missing:
                raise ValueError(f"{item.work_order_id} has missing dependencies: {missing}")
            if item.work_order_id in item.depends_on:
                raise ValueError(f"{item.work_order_id} cannot depend on itself")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(work_order_id: str) -> None:
            if work_order_id in visiting:
                raise ValueError(f"dependency cycle includes {work_order_id}")
            if work_order_id in visited:
                return
            visiting.add(work_order_id)
            for dependency in by_id[work_order_id].depends_on:
                visit(dependency)
            visiting.remove(work_order_id)
            visited.add(work_order_id)

        for work_order_id in by_id:
            visit(work_order_id)

    def refresh(self, work_orders: tuple[WorkOrder, ...]) -> tuple[WorkOrder, ...]:
        self.validate(work_orders)
        status = {item.work_order_id: item.status for item in work_orders}
        refreshed: list[WorkOrder] = []
        claimed_write_owners: set[str] = {
            owner
            for item in work_orders
            if item.status in {WorkStatus.RUNNING, WorkStatus.REVIEW}
            for owner in item.write_owners
        }
        for item in work_orders:
            if item.status not in {WorkStatus.PENDING, WorkStatus.READY}:
                refreshed.append(item)
                continue
            dependency_states = [status[value] for value in item.depends_on]
            if any(
                value in {WorkStatus.FAILED, WorkStatus.BLOCKED}
                for value in dependency_states
            ):
                next_status = WorkStatus.BLOCKED
            elif all(value == WorkStatus.COMPLETE for value in dependency_states):
                conflicts = claimed_write_owners.intersection(item.write_owners)
                next_status = WorkStatus.PENDING if conflicts else WorkStatus.READY
                if next_status == WorkStatus.READY:
                    claimed_write_owners.update(item.write_owners)
            else:
                next_status = WorkStatus.PENDING
            refreshed.append(item.model_copy(update={"status": next_status}))
        return tuple(refreshed)


__all__ = ["DependencyResolver"]
