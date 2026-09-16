"""Planner interfaces for deterministic tests and optional Codex planning."""

from __future__ import annotations

from collections import deque
import json
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.agents.llm.codex_cli_client import run_codex_exec_json

from .contracts import ActionRequest


class Planner(Protocol):
    def choose_action(self, context: dict[str, Any]) -> ActionRequest: ...


class QueuePlanner:
    """Deterministic planner used by tests and bounded scripted runs."""

    def __init__(self, actions: list[ActionRequest]):
        self._actions = deque(actions)

    def choose_action(self, context: dict[str, Any]) -> ActionRequest:
        if not self._actions:
            raise RuntimeError("planner has no remaining actions")
        return self._actions.popleft()


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    expected_evidence: list[str] = Field(default_factory=list)


class CodexPlanner:
    """Use Codex only to choose a typed action; the harness executes the tool."""

    def __init__(
        self,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds

    def choose_action(self, context: dict[str, Any]) -> ActionRequest:
        prompt = (
            "You are the planner inside a stateful weather task harness. "
            "Choose exactly one available typed tool. Do not claim completion, "
            "do not invent evidence, and do not request a tool outside available_tools. "
            "Prefer the action that closes the earliest unmet acceptance condition.\n\n"
            + json.dumps(context, ensure_ascii=False, sort_keys=True)
        )
        payload = run_codex_exec_json(
            prompt=prompt,
            output_model=PlannerOutput,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            timeout_seconds=self.timeout_seconds,
            isolated_context=True,
            forbid_tool_calls=True,
        )
        return ActionRequest(
            tool_name=payload["tool_name"],
            arguments=payload["arguments"],
            rationale=payload["rationale"],
            expected_evidence=tuple(payload["expected_evidence"]),
        )


__all__ = ["CodexPlanner", "Planner", "PlannerOutput", "QueuePlanner"]
