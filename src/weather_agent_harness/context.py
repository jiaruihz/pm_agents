"""Build bounded, phase-specific context for an agent planner."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .contracts import RunState, TaskSpec
from .evidence import EvidenceStore
from .tools import ToolContract


class ContextBuilder:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def build(
        self,
        task: TaskSpec,
        state: RunState,
        store: EvidenceStore,
        tools: list[ToolContract],
    ) -> dict[str, Any]:
        references: list[dict[str, Any]] = []
        for ref in task.context_refs:
            if ref.phases and state.phase not in ref.phases:
                continue
            path = Path(ref.path)
            if not path.is_absolute():
                path = self.repo_root / path
            if not path.exists():
                if ref.required:
                    raise FileNotFoundError(path)
                references.append({"path": str(path), "status": "missing_optional"})
                continue
            raw = path.read_text(encoding="utf-8")
            references.append(
                {
                    "path": str(path),
                    "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                    "truncated": len(raw) > ref.max_chars,
                    "content": raw[: ref.max_chars],
                }
            )
        return {
            "task": task.model_dump(mode="json"),
            "state": state.model_dump(mode="json"),
            "available_tools": [tool.model_dump(mode="json") for tool in tools],
            "recent_evidence": [
                item.model_dump(mode="json") for item in store.recent_entries()
            ],
            "context_refs": references,
        }


__all__ = ["ContextBuilder"]
