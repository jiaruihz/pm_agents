"""Fail-closed per-consumer inboxes for the single market-book capture owner.

This module only reads bounded append-only demand declarations.  It has no
transport, collector, scheduler, or receipt-writing capability.  A caller
must opt in to a fixed consumer name; the reader never enumerates a root
directory or accepts arbitrary filenames.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Iterable

from src.platform.market_data.capture_demand import CaptureDemand


DEFAULT_ALPHA_CONSUMER = "polymarket_alpha"
DEMAND_FILENAME = "capture_demands.jsonl"
_SAFE_CONSUMER_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


@dataclass(frozen=True)
class InboxDemandLine:
    """One complete JSONL line and the consumer directory that supplied it."""

    consumer_id: str
    line: bytes


class CaptureDemandInbox:
    """Read known per-consumer demand streams without directory discovery.

    A malformed, symlinked, oversized, or truncated stream contributes no
    rows.  This lets the canonical owner continue its weather selection while
    failing closed for an optional consumer such as Alpha.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        consumers: Iterable[str] = (DEFAULT_ALPHA_CONSUMER,),
        max_bytes_per_consumer: int = 256_000,
        max_lines_per_consumer: int = 200,
    ) -> None:
        self.root = Path(root)
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise ValueError("capture inbox root must be an absolute non-traversing path")
        self.consumers = tuple(sorted(set(str(value) for value in consumers)))
        if not self.consumers:
            raise ValueError("capture inbox requires at least one explicit consumer")
        if any(not self._safe_consumer(value) for value in self.consumers):
            raise ValueError("capture inbox consumer is not a safe identifier")
        if max_bytes_per_consumer <= 0 or max_lines_per_consumer <= 0:
            raise ValueError("capture inbox limits must be positive")
        self.max_bytes_per_consumer = max_bytes_per_consumer
        self.max_lines_per_consumer = max_lines_per_consumer
        self.last_errors: tuple[str, ...] = ()
        self._sealed_payloads: dict[tuple[str, str], bytes] = {}

    @staticmethod
    def _safe_consumer(value: str) -> bool:
        return bool(value) and set(value) <= _SAFE_CONSUMER_CHARS

    @staticmethod
    def _directory_flags() -> int:
        return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)

    def _open_root_without_symlinks(self) -> int:
        """Walk every absolute root component through dirfd + O_NOFOLLOW."""

        current_fd = os.open(self.root.anchor, self._directory_flags())
        try:
            for component in self.root.parts[1:]:
                next_fd = os.open(component, self._directory_flags(), dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except BaseException:
            os.close(current_fd)
            raise

    def _validated_lines(
        self,
        consumer_id: str,
        lines: list[bytes],
    ) -> tuple[InboxDemandLine, ...]:
        """Validate a whole consumer journal; never partially accept it."""

        by_identity: dict[str, bytes] = {}
        for line in lines:
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("capture demand line must be an object")
                demand = CaptureDemand(
                    demand_id=str(row["demand_id"]),
                    consumer_id=str(row["consumer_id"]),
                    strategy_key=str(row["strategy_key"]),
                    condition_id=str(row["condition_id"]),
                    token_id=str(row["token_id"]),
                    reason=str(row["reason"]),
                    priority=str(row["priority"]),
                    requested_at_utc=str(row["requested_at_utc"]),
                    expires_at_utc=str(row["expires_at_utc"]),
                    desired_transport=str(row["desired_transport"]),
                    requested_checkpoints_seconds=tuple(
                        row.get("requested_checkpoints_seconds") or ()
                    ),
                    trigger_event_id=row.get("trigger_event_id"),
                    metadata=row.get("metadata") or {},
                    schema_version=str(row["schema_version"]),
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                raise ValueError("invalid capture demand journal line") from error
            if demand.consumer_id != consumer_id:
                raise ValueError("capture demand consumer does not match inbox")
            normalized = (
                json.dumps(
                    demand.to_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
                + b"\n"
            )
            previous = by_identity.get(demand.demand_id)
            if previous is not None and previous != normalized:
                raise ValueError("conflicting immutable demand replay")
            by_identity[demand.demand_id] = normalized
        for demand_id, payload in by_identity.items():
            sealed = self._sealed_payloads.get((consumer_id, demand_id))
            if sealed is not None and sealed != payload:
                raise ValueError("conflicting immutable demand replay")
        result = tuple(
            InboxDemandLine(consumer_id, payload)
            for _identity, payload in sorted(by_identity.items())
        )
        for demand_id, payload in by_identity.items():
            self._sealed_payloads[(consumer_id, demand_id)] = payload
        return result

    def read_lines(self) -> tuple[InboxDemandLine, ...]:
        """Return complete bounded lines from configured consumers only.

        ``dir_fd`` and ``O_NOFOLLOW`` ensure the root, consumer directory and
        journal are each opened as non-symlink filesystem objects.  Absent
        consumer journals are normal; they simply mean that consumer has no
        active demand.
        """

        errors: list[str] = []
        try:
            root_fd = self._open_root_without_symlinks()
        except OSError as error:
            self.last_errors = (f"inbox_root_unavailable:{error.__class__.__name__}",)
            return ()
        try:
            root_stat = os.fstat(root_fd)
            if not stat.S_ISDIR(root_stat.st_mode):
                self.last_errors = ("inbox_root_not_directory",)
                return ()
            result: list[InboxDemandLine] = []
            for consumer_id in self.consumers:
                try:
                    consumer_fd = os.open(consumer_id, self._directory_flags(), dir_fd=root_fd)
                except FileNotFoundError:
                    continue
                except OSError as error:
                    errors.append(f"consumer_unavailable:{consumer_id}:{error.__class__.__name__}")
                    continue
                try:
                    if not stat.S_ISDIR(os.fstat(consumer_fd).st_mode):
                        errors.append(f"consumer_not_directory:{consumer_id}")
                        continue
                    try:
                        file_fd = os.open(
                            DEMAND_FILENAME,
                            os.O_RDONLY
                            | getattr(os, "O_NOFOLLOW", 0)
                            | getattr(os, "O_NONBLOCK", 0),
                            dir_fd=consumer_fd,
                        )
                    except FileNotFoundError:
                        continue
                    except OSError as error:
                        errors.append(f"journal_unavailable:{consumer_id}:{error.__class__.__name__}")
                        continue
                    try:
                        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                            errors.append(f"journal_not_regular:{consumer_id}")
                            continue
                        payload = os.read(file_fd, self.max_bytes_per_consumer + 1)
                    finally:
                        os.close(file_fd)
                    if len(payload) > self.max_bytes_per_consumer:
                        errors.append(f"journal_byte_budget_exceeded:{consumer_id}")
                        continue
                    lines = payload.splitlines(keepends=True)
                    if lines and not lines[-1].endswith(b"\n"):
                        errors.append(f"journal_truncated:{consumer_id}")
                        continue
                    complete = [line for line in lines if line.strip()]
                    if len(complete) > self.max_lines_per_consumer:
                        errors.append(f"journal_line_budget_exceeded:{consumer_id}")
                        continue
                    try:
                        result.extend(self._validated_lines(consumer_id, complete))
                    except ValueError as error:
                        errors.append(f"journal_invalid:{consumer_id}:{error}")
                finally:
                    os.close(consumer_fd)
            self.last_errors = tuple(errors)
            return tuple(result)
        finally:
            os.close(root_fd)
