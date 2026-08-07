"""Append-only execution evidence and deterministic claim storage."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol

import fcntl


ORPHAN_CLAIM_RETRY_AFTER_SEC = 60


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class ExecutionJournal(Protocol):
    def claim_plan(self, plan_dedupe_key: str, owner: str, payload: Mapping[str, Any] | None = None) -> bool: ...
    def reserve_live_exposure(self, exposure_key: str, owner: str, payload: Mapping[str, Any] | None = None) -> bool: ...
    def claim_lifecycle_action(self, lifecycle_action_id: str, owner: str, payload: Mapping[str, Any] | None = None) -> bool: ...
    def record_attempt(self, payload: Mapping[str, Any]) -> None: ...
    def record_outcome(self, payload: Mapping[str, Any]) -> None: ...
    def read_rows(self) -> list[dict[str, Any]]: ...
    def lookup_order_chain(self, root_order_id: str | None = None, source_order_id: str | None = None) -> list[dict[str, Any]]: ...
    def unknown_records(self, identity_key: str) -> list[dict[str, Any]]: ...
    def open_or_reserved_exposure(self) -> list[dict[str, Any]]: ...


class JsonlExecutionJournal:
    """One-file append-only journal with an advisory process lock per append/claim."""

    def __init__(self, path: Path, *, writer_id: str) -> None:
        self.path = Path(path)
        self.writer_id = str(writer_id)

    @contextmanager
    def _locked_file(self) -> Iterator[Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                yield handle
            finally:
                handle.flush()
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _rows_from_handle(handle: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def _append(self, payload: Mapping[str, Any]) -> None:
        row = {
            "journal_schema_version": "weather_execution_journal_v1",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "writer_id": self.writer_id,
            **dict(payload),
        }
        with self._locked_file() as handle:
            handle.seek(0, 2)
            handle.write(json.dumps(_json_safe(row), ensure_ascii=True, sort_keys=True) + "\n")

    def _claim(self, *, event_type: str, claim_key: str, owner: str, payload: Mapping[str, Any] | None) -> bool:
        with self._locked_file() as handle:
            rows = self._rows_from_handle(handle)
            claim_rows = [
                row
                for row in rows
                if row.get("event_type")
                in {event_type, f"{event_type}_retry_claimed", f"{event_type}_orphan_reclaimed"}
                and row.get("claim_key") == claim_key
            ]
            prior_claim = bool(claim_rows)
            retry_claim_type = f"{event_type}_retry_claimed"
            retry_permission_count = sum(
                1
                for row in rows
                if row.get("event_type") == "outcome"
                and isinstance(row.get("payload"), Mapping)
                and claim_key in {row["payload"].get("identity_key"), row["payload"].get("live_exposure_key")}
                and row["payload"].get("status") == "retry_permitted"
            )
            retry_claim_count = sum(
                1
                for row in rows
                if row.get("event_type") == retry_claim_type
                and row.get("claim_key") == claim_key
            )
            retry_permitted = retry_permission_count > retry_claim_count
            attempt_identity = str(
                claim_key
                if event_type == "plan_claimed"
                else dict(payload or {}).get("plan_dedupe_key") or ""
            )
            attempt_exists = any(
                row.get("event_type") == "attempt_before_side_effect"
                and isinstance(row.get("payload"), Mapping)
                and row["payload"].get("identity_key") == attempt_identity
                for row in rows
            )
            latest_claim_at = None
            if claim_rows:
                raw_claim_at = str(claim_rows[-1].get("recorded_at_utc") or "")
                try:
                    latest_claim_at = datetime.fromisoformat(
                        raw_claim_at.replace("Z", "+00:00")
                    )
                    if latest_claim_at.tzinfo is None:
                        latest_claim_at = latest_claim_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    latest_claim_at = None
            orphan_reclaim = bool(
                prior_claim
                and not attempt_exists
                and latest_claim_at is not None
                and (
                    datetime.now(timezone.utc) - latest_claim_at.astimezone(timezone.utc)
                ).total_seconds()
                >= ORPHAN_CLAIM_RETRY_AFTER_SEC
            )
            if prior_claim and not retry_permitted and not orphan_reclaim:
                return False
            handle.seek(0, 2)
            row = {
                "journal_schema_version": "weather_execution_journal_v1",
                "event_type": (
                    f"{event_type}_orphan_reclaimed"
                    if orphan_reclaim
                    else retry_claim_type
                    if prior_claim
                    else event_type
                ),
                "claim_key": claim_key,
                "owner": owner,
                "payload": dict(payload or {}),
                "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                "writer_id": self.writer_id,
            }
            handle.write(json.dumps(_json_safe(row), ensure_ascii=True, sort_keys=True) + "\n")
            return True

    def claim_plan(self, plan_dedupe_key: str, owner: str, payload: Mapping[str, Any] | None = None) -> bool:
        return self._claim(event_type="plan_claimed", claim_key=str(plan_dedupe_key), owner=owner, payload=payload)

    def reserve_live_exposure(self, exposure_key: str, owner: str, payload: Mapping[str, Any] | None = None) -> bool:
        return self._claim(event_type="live_exposure_reserved", claim_key=str(exposure_key), owner=owner, payload=payload)

    def claim_lifecycle_action(self, lifecycle_action_id: str, owner: str, payload: Mapping[str, Any] | None = None) -> bool:
        return self._claim(event_type="lifecycle_action_claimed", claim_key=str(lifecycle_action_id), owner=owner, payload=payload)

    def record_attempt(self, payload: Mapping[str, Any]) -> None:
        self._append({"event_type": "attempt_before_side_effect", "payload": dict(payload)})

    def record_outcome(self, payload: Mapping[str, Any]) -> None:
        self._append({"event_type": "outcome", "payload": dict(payload)})

    def read_rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            rows = self._rows_from_handle(handle)
        for row in rows:
            row.setdefault("event_type", "legacy")
        return rows

    def lookup_order_chain(self, root_order_id: str | None = None, source_order_id: str | None = None) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for row in self.read_rows():
            payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
            values = {row.get("root_order_id"), row.get("source_order_id"), payload.get("root_order_id"), payload.get("source_order_id")}
            if (root_order_id and root_order_id in values) or (source_order_id and source_order_id in values):
                matches.append(row)
        return matches

    def unknown_records(self, identity_key: str) -> list[dict[str, Any]]:
        return [row for row in self.read_rows() if row.get("event_type") == "outcome" and isinstance(row.get("payload"), Mapping) and row["payload"].get("identity_key") == identity_key and row["payload"].get("status") == "unknown"]

    def open_or_reserved_exposure(self) -> list[dict[str, Any]]:
        return [row for row in self.read_rows() if row.get("event_type") in {"live_exposure_reserved", "outcome"}]
