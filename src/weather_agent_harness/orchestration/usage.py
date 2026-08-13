"""Extract measured usage from one dedicated Codex agent session JSONL."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from .contracts import UsageRecord


def usage_from_codex_session(path: Path) -> tuple[UsageRecord, str, float, int]:
    """Return usage, observed model, duration and tool-call count for one session."""

    first_timestamp: str | None = None
    last_timestamp: str | None = None
    observed_models: set[str] = set()
    totals: dict | None = None
    tool_calls = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            timestamp = item.get("timestamp")
            if timestamp:
                first_timestamp = first_timestamp or timestamp
                last_timestamp = timestamp
            payload = item.get("payload") or {}
            if item.get("type") == "turn_context" and payload.get("model"):
                observed_models.add(payload["model"])
            if item.get("type") == "event_msg" and payload.get("type") == "token_count":
                totals = (payload.get("info") or {}).get("total_token_usage") or totals
            if item.get("type") == "response_item" and payload.get("type") in {
                "function_call",
                "custom_tool_call",
            }:
                tool_calls += 1
    if not observed_models:
        raise ValueError("Codex session has no observed model")
    if len(observed_models) != 1:
        raise ValueError(f"Codex session switched models: {sorted(observed_models)}")
    observed_model = next(iter(observed_models))
    if totals is None:
        raise ValueError("Codex session has no token_count event")
    if first_timestamp is None or last_timestamp is None:
        raise ValueError("Codex session has no timestamp range")
    start = datetime.fromisoformat(first_timestamp.replace("Z", "+00:00"))
    finish = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
    usage = UsageRecord(
        source=f"codex_session:{path.resolve()}",
        input_tokens=int(totals["input_tokens"]),
        output_tokens=int(totals["output_tokens"]),
        cached_tokens=int(totals.get("cached_input_tokens", 0)),
    )
    return usage, observed_model, (finish - start).total_seconds(), tool_calls


__all__ = ["usage_from_codex_session"]
