"""Domain controller protocol."""

from __future__ import annotations

from typing import Protocol

from ..contracts import ActionRequest, ActionResult, CompletionDecision, RunState, TaskSpec


class DomainController(Protocol):
    task_type: str
    initial_phase: str

    def allowed_tools(self, task: TaskSpec, state: RunState) -> set[str]: ...

    def apply_result(
        self,
        task: TaskSpec,
        state: RunState,
        action: ActionRequest,
        result: ActionResult,
    ) -> None: ...

    def verify(self, task: TaskSpec, state: RunState) -> CompletionDecision: ...


__all__ = ["DomainController"]
