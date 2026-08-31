"""Extract measured usage from one dedicated Codex agent session JSONL."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from .contracts import UsageRecord


DEFAULT_CODEX_SESSION_ROOTS = (
    Path.home() / ".codex" / "sessions",
    Path.home() / ".codex" / "archived_sessions",
)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def find_codex_session(
    thread_identity: str,
    *,
    started_at_utc: str | None = None,
    roots: tuple[Path, ...] = DEFAULT_CODEX_SESSION_ROOTS,
) -> Path:
    """Find the newest Codex session by thread UUID or spawned agent path."""

    normalized = "/" + thread_identity.strip("/")
    started_at = _timestamp(started_at_utc) if started_at_utc else None
    matches: list[tuple[datetime, Path]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                with path.open(encoding="utf-8") as handle:
                    item = json.loads(handle.readline())
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if item.get("type") != "session_meta":
                continue
            payload = item.get("payload") or {}
            source = payload.get("source") or {}
            spawn = (
                ((source.get("subagent") or {}).get("thread_spawn") or {})
                if isinstance(source, dict)
                else {}
            )
            if (
                payload.get("id") != thread_identity
                and payload.get("session_id") != thread_identity
                and spawn.get("agent_path") != normalized
            ):
                continue
            created_at = _timestamp(item.get("timestamp") or "1970-01-01T00:00:00Z")
            # The session file is created before the coordinator records the spawn.
            if started_at and created_at < started_at - timedelta(minutes=10):
                continue
            matches.append((created_at, path))
    if not matches:
        boundary = f" after {started_at_utc}" if started_at_utc else ""
        raise FileNotFoundError(
            f"no Codex session for thread_identity={thread_identity}{boundary}"
        )
    return max(matches, key=lambda item: item[0])[1]


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
    start = _timestamp(first_timestamp)
    finish = _timestamp(last_timestamp)
    usage = UsageRecord(
        source=f"codex_session:{path.resolve()}",
        input_tokens=int(totals["input_tokens"]),
        output_tokens=int(totals["output_tokens"]),
        cached_tokens=int(totals.get("cached_input_tokens", 0)),
    )
    return usage, observed_model, (finish - start).total_seconds(), tool_calls


__all__ = ["DEFAULT_CODEX_SESSION_ROOTS", "find_codex_session", "usage_from_codex_session"]
