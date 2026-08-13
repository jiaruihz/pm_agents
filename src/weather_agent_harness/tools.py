"""Typed tool registry used by the agent run loop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from .contracts import ActionResult, RiskLevel, RunState, TaskSpec, utc_now
from .evidence import EvidenceStore


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    risk: RiskLevel
    task_types: tuple[str, ...]
    input_schema: dict[str, Any] = Field(default_factory=dict)
    agent_side: bool = False
    replay_safe: bool = False


@dataclass(frozen=True)
class ToolContext:
    task: TaskSpec
    state: RunState
    store: EvidenceStore
    repo_root: Path


ToolHandler = Callable[[dict[str, Any], ToolContext], ActionResult]


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, contract: ToolContract, handler: ToolHandler) -> None:
        if contract.name in self._tools:
            raise ValueError(f"duplicate tool: {contract.name}")
        self._tools[contract.name] = RegisteredTool(contract, handler)

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown harness tool: {name}") from exc

    def contracts_for(self, task_type: str, names: set[str] | None = None) -> list[ToolContract]:
        contracts = [
            item.contract
            for item in self._tools.values()
            if (task_type in item.contract.task_types or "*" in item.contract.task_types)
            and (names is None or item.contract.name in names)
        ]
        return sorted(contracts, key=lambda item: item.name)

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ActionResult:
        tool = self.get(name)
        if (
            context.task.task_type not in tool.contract.task_types
            and "*" not in tool.contract.task_types
        ):
            raise ValueError(f"tool {name} does not support {context.task.task_type}")
        self.validate_arguments(name, arguments)
        return tool.handler(arguments, context)

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> None:
        contract = self.get(name).contract
        schema = contract.input_schema
        if schema.get("type") not in (None, "object"):
            raise ValueError(f"tool {name} only supports object input schemas")
        required = schema.get("required") or []
        missing = [key for key in required if key not in arguments]
        if missing:
            raise ValueError(f"tool {name} missing required arguments: {missing}")
        if schema.get("additionalProperties") is False:
            allowed = set((schema.get("properties") or {}).keys())
            unknown = sorted(set(arguments) - allowed)
            if unknown:
                raise ValueError(f"tool {name} has unknown arguments: {unknown}")


def command_handler(
    *,
    argv_builder: Callable[[dict[str, Any], ToolContext], list[str]],
    success_facts: Callable[[dict[str, Any], ToolContext], dict[str, Any]] | None = None,
    timeout_seconds: int = 600,
) -> ToolHandler:
    """Build a non-shell subprocess tool with durable stdout/stderr evidence."""

    def run(arguments: dict[str, Any], context: ToolContext) -> ActionResult:
        started = utc_now()
        argv = argv_builder(arguments, context)
        if not argv:
            raise ValueError("command argv must not be empty")
        sequence = context.state.action_count + 1
        stdout_path = context.store.artifact_path(f"actions/{sequence:04d}-{Path(argv[0]).name}.stdout.txt")
        stderr_path = context.store.artifact_path(f"actions/{sequence:04d}-{Path(argv[0]).name}.stderr.txt")
        proc = subprocess.run(
            argv,
            cwd=context.repo_root,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8")
        refs = (str(stdout_path), str(stderr_path))
        facts = {"returncode": proc.returncode, "argv": argv}
        if success_facts is not None:
            facts.update(success_facts(arguments, context))
        return ActionResult(
            status="succeeded" if proc.returncode == 0 else "failed",
            summary=f"command exited {proc.returncode}: {argv[0]}",
            evidence_refs=refs,
            facts=facts,
            started_at_utc=started,
        )

    return run


__all__ = [
    "RegisteredTool",
    "ToolContext",
    "ToolContract",
    "ToolHandler",
    "ToolRegistry",
    "command_handler",
]
