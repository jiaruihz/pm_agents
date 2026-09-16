"""Append-only filesystem outbox for Alpha paired capture demands (GLM-OP-02).

The only accepted input is an :class:`OwnerDemandBundle` produced by the
released ``build_owner_capture_demands`` translator: one Alpha demand plus
its paired YES/NO :class:`CaptureDemand` rows.  The outbox file lives at one
fixed relative locator under the caller-supplied artifact root; callers
cannot choose the path.  Every directory in the fixed chain is resolved from
an owned dirfd with ``O_NOFOLLOW``, the file is opened with ``O_APPEND``,
mutations hold an exclusive ``flock``, the bundle is written by a single
``os.write`` syscall, and durability is forced with ``fsync``.  Exact replay
of the same bundle never appends a second line; the same bundle id with
different bytes is a typed conflict.  The module never reads the network,
starts an owner process, or touches weather configuration.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from ..books.adapter import (
    OWNER_CONSUMER_ID,
    OWNER_STRATEGY_KEY,
    OwnerDemandBundle,
)
from ..artifacts import ArtifactPathError, ArtifactStore
from ..contracts import bytes_sha256, canonical_json, content_sha256
from ..contracts.base import ensure_utc


DEMAND_OUTBOX_SCHEMA_VERSION = "polymarket_alpha_demand_outbox_v1"
DEMAND_OUTBOX_LOCATOR = "polymarket_alpha/capture_demands.jsonl"
OUTBOX_LOCK_FILENAME = ".capture_demands.lock"
MAX_BUNDLES_PER_MINUTE = 5
MAX_UNEXPIRED_BUNDLES = 5
_BUNDLE_LEGS = 2


class DemandOutboxError(ValueError):
    """Base class for typed, fail-closed outbox violations."""

    code = "DEMAND_OUTBOX_ERROR"


class DemandOutboxPathError(DemandOutboxError):
    code = "OUTBOX_PATH_UNSAFE"


class DemandOutboxLegError(DemandOutboxError):
    code = "BUNDLE_LEGS_INVALID"


class DemandOutboxConflictError(DemandOutboxError):
    code = "BUNDLE_ID_CONFLICT"


class DemandOutboxBudgetError(DemandOutboxError):
    code = "BUDGET_EXCEEDED"


class DemandOutboxCorruptError(DemandOutboxError):
    code = "OUTBOX_CORRUPT"


class DemandOutboxWriteError(DemandOutboxError):
    code = "OUTBOX_WRITE_FAILED"


@dataclass(frozen=True)
class DemandOutboxAppendReceipt:
    """Append fact for one bundle submission."""

    outbox_locator: str
    bundle_id: str
    bundle_sha256: str
    replayed: bool
    file_device: int
    file_inode: int
    pre_size: int
    post_size: int
    line_count: int
    appended_line_sha256: str
    locked_exclusively: bool
    fsynced: bool
    parent_fsynced: bool
    appended_at: datetime


def _utc_from_text(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise DemandOutboxCorruptError(f"existing line has an unparseable {field}") from error
    if parsed.tzinfo is None:
        raise DemandOutboxCorruptError(f"existing line has a naive {field}")
    return parsed.astimezone(timezone.utc)


def validate_owner_demand_bundle(
    bundle: OwnerDemandBundle,
    *,
    now: datetime | None = None,
) -> None:
    """Fail closed unless the bundle is one exact paired YES/NO translation.

    Shared with the book bridge (GLM-OP-03) so both seams enforce the same
    demand binding: common ``condition_id``, distinct tokens mapped to the
    Alpha identity, shared trigger/expiry, and the fixed Alpha consumer and
    strategy keys.
    """

    demand = bundle.alpha_demand
    rows = bundle.owner_demands
    if len(rows) != _BUNDLE_LEGS:
        raise DemandOutboxLegError(f"bundle must carry exactly {_BUNDLE_LEGS} owner legs")
    identity = demand.identity
    if identity.condition_id is None:
        raise DemandOutboxLegError("Alpha demand identity requires condition_id")
    yes_row, no_row = rows
    for row in rows:
        if row.consumer_id != OWNER_CONSUMER_ID:
            raise DemandOutboxLegError("owner leg consumer_id must be polymarket_alpha")
        if row.strategy_key != OWNER_STRATEGY_KEY:
            raise DemandOutboxLegError("owner leg strategy_key must be polymarket_alpha.p0_offline")
        if row.condition_id != identity.condition_id:
            raise DemandOutboxLegError("owner leg condition_id does not match the Alpha demand")
        if row.trigger_event_id != demand.demand_id:
            raise DemandOutboxLegError("owner leg trigger must bind the Alpha demand id")
        if row.metadata.get("alpha_demand_id") != demand.demand_id:
            raise DemandOutboxLegError("owner leg metadata must bind the Alpha demand id")
    if yes_row.token_id == no_row.token_id:
        raise DemandOutboxLegError("YES/NO owner legs must use distinct token ids")
    sides = {str(row.metadata.get("outcome_side")): row.token_id for row in rows}
    if set(sides) != {"YES", "NO"}:
        raise DemandOutboxLegError("owner legs must declare exactly one YES and one NO side")
    if sides["YES"] != identity.yes_token_id or sides["NO"] != identity.no_token_id:
        raise DemandOutboxLegError("owner leg tokens do not map to the Alpha YES/NO identity")
    shared_fields = ("requested_at_utc", "expires_at_utc", "reason", "priority", "desired_transport")
    for field in shared_fields:
        if getattr(yes_row, field) != getattr(no_row, field):
            raise DemandOutboxLegError(f"YES/NO owner legs disagree on {field}")
    if yes_row.requested_checkpoints_seconds != no_row.requested_checkpoints_seconds:
        raise DemandOutboxLegError("YES/NO owner legs disagree on requested_checkpoints_seconds")
    if now is not None:
        now = ensure_utc(now)
        requested = _utc_from_text(yes_row.requested_at_utc, field="requested_at_utc")
        expiry = _utc_from_text(yes_row.expires_at_utc, field="expires_at_utc")
        if requested > now:
            raise DemandOutboxBudgetError("demand request clock is in the future")
        if now >= expiry:
            raise DemandOutboxBudgetError("demand is already expired at submission time")


def _bundle_line_payload(bundle: OwnerDemandBundle) -> dict[str, Any]:
    return {
        "schema_version": DEMAND_OUTBOX_SCHEMA_VERSION,
        "bundle_id": bundle.alpha_demand.demand_id,
        "alpha_purpose": bundle.alpha_demand.purpose.value,
        "owner_demands": [row.to_dict() for row in bundle.owner_demands],
    }


def _read_locked_lines(parent_fd: int, name: str) -> list[tuple[bytes, str, datetime, datetime]]:
    """Parse existing lines from a dedicated read fd while holding the lock."""

    try:
        read_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
    except OSError as error:
        raise DemandOutboxPathError(f"outbox file cannot be read safely: {error}") from error
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(read_fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(read_fd)
    raw = b"".join(chunks)
    if not raw:
        return []
    lines = raw.splitlines(keepends=True)
    parsed: list[tuple[bytes, str, datetime, datetime]] = []
    for index, line in enumerate(lines):
        if not line.endswith(b"\n"):
            raise DemandOutboxCorruptError(
                f"outbox line {index} lacks a trailing newline (partial write or tampering)"
            )
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise DemandOutboxCorruptError(f"outbox line {index} is not valid JSON: {error}") from error
        bundle_id = value.get("bundle_id") if isinstance(value, Mapping) else None
        demands = value.get("owner_demands") if isinstance(value, Mapping) else None
        if not isinstance(bundle_id, str) or not isinstance(demands, list) or len(demands) != _BUNDLE_LEGS:
            raise DemandOutboxCorruptError(f"outbox line {index} is not a paired demand bundle")
        requested = _utc_from_text(demands[0].get("requested_at_utc"), field="requested_at_utc")
        expires = _utc_from_text(demands[0].get("expires_at_utc"), field="expires_at_utc")
        parsed.append((line, bundle_id, requested, expires))
    return parsed


def _enforce_budgets(
    lines: list[tuple[bytes, str, datetime, datetime]], *, now: datetime
) -> None:
    window_start = now - timedelta(seconds=60)
    # A later-clock writer may win the file lock before an earlier-clock writer.
    # Existing rows after this caller's clock are therefore not proof of
    # corruption. Count them conservatively so they cannot evade the rate cap;
    # the incoming bundle itself is still rejected above when future-dated.
    recent = sum(1 for _, _, requested, _ in lines if window_start < requested)
    if recent >= MAX_BUNDLES_PER_MINUTE:
        raise DemandOutboxBudgetError(
            f"per-minute bundle budget reached: {recent} bundles in the last 60s"
        )
    unexpired = sum(1 for _, _, _, expires in lines if expires > now)
    if unexpired + 1 > MAX_UNEXPIRED_BUNDLES:
        raise DemandOutboxBudgetError(
            f"unexpired demand budget reached: {unexpired} unexpired bundles already queued"
        )


def append_owner_demand_bundle(
    artifact_root: Path,
    *,
    bundle: OwnerDemandBundle,
    now: datetime,
) -> DemandOutboxAppendReceipt:
    """Append exactly one paired YES/NO bundle to the fixed outbox locator.

    Budget interpretation: each JSONL line is one bundle (one Alpha demand,
    two owner rows). The rate budget counts every existing bundle newer than
    ``now - 60s``; rows later than this caller's clock count conservatively so
    concurrent lock ordering cannot evade the cap. New future request clocks
    are rejected. The standing budget
    counts bundles whose ``expires_at_utc`` is still in the future, including
    the one about to be appended.  Clocks are caller-supplied per the
    offline contract; no wall clock is read here.
    """

    now = ensure_utc(now)
    validate_owner_demand_bundle(bundle, now=now)
    payload = _bundle_line_payload(bundle)
    line_bytes = (canonical_json(payload) + "\n").encode("utf-8")
    bundle_id = str(payload["bundle_id"])

    try:
        store = ArtifactStore(Path(artifact_root))
        root = store.root
    except ArtifactPathError as error:
        raise DemandOutboxPathError(str(error)) from error
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    except OSError as error:
        raise DemandOutboxPathError(f"artifact root cannot be opened safely: {error}") from error
    # Bootstrap serialization: directory creation racing with an openat file
    # create inside the fresh directory is not safe on every supported
    # platform (observed spurious ENOENT on sandboxed darwin), so the whole
    # ensure+append runs under one exclusive lock on a root-level lock file.
    # The root itself already exists, so creating the lock file there has no
    # directory-creation race.
    try:
        lock_fd = _open_create_with_enoent_retry(
            root_fd, OUTBOX_LOCK_FILENAME, extra_flags=0, mode=0o600
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                return _append_under_root_lock(
                    store,
                    root_fd=root_fd,
                    line_bytes=line_bytes,
                    payload=payload,
                    bundle_id=bundle_id,
                    now=now,
                )
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
    finally:
        os.close(root_fd)


def _open_create_with_enoent_retry(
    dir_fd: int,
    name: str,
    *,
    extra_flags: int,
    mode: int,
    attempts: int = 4,
    settle_seconds: float = 0.02,
) -> int:
    """Open ``name`` under ``dir_fd`` with create flags, retrying pure ENOENT.

    Why a retry is safe here: the caller holds the exclusive bootstrap lock,
    so the parent directory cannot appear or disappear concurrently, and
    ``openat`` with ``O_CREAT`` under a verified directory can only raise
    ENOENT spuriously (observed on sandboxed darwin runners).  Every other
    errno fails closed immediately, and exhausting the bounded retries also
    fails closed.
    """

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_WRONLY | os.O_CREAT | extra_flags | nofollow
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            return os.open(name, flags, mode, dir_fd=dir_fd)
        except FileNotFoundError as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(settle_seconds)
        except OSError as error:
            raise DemandOutboxPathError(f"outbox file cannot be opened safely: {error}") from error
    raise DemandOutboxPathError(
        f"outbox create kept failing ENOENT under lock: {last_error}"
    ) from last_error


def _append_under_root_lock(
    store: ArtifactStore,
    *,
    root_fd: int,
    line_bytes: bytes,
    payload: Mapping[str, Any],
    bundle_id: str,
    now: datetime,
) -> DemandOutboxAppendReceipt:
    try:
        parent_fd, name = store.open_parent(DEMAND_OUTBOX_LOCATOR, create_parents=True)
    except ArtifactPathError as error:
        raise DemandOutboxPathError(str(error)) from error
    write_fd = -1
    try:
        write_fd = _open_create_with_enoent_retry(
            parent_fd, name, extra_flags=os.O_APPEND, mode=0o600
        )
        pre_stat = os.fstat(write_fd)
        if not stat.S_ISREG(pre_stat.st_mode):
            raise DemandOutboxPathError("outbox locator must be a regular file")
        fcntl.flock(write_fd, fcntl.LOCK_EX)
        try:
            lines = _read_locked_lines(parent_fd, name)
            replay = next((line for line, existing_id, _, _ in lines if existing_id == bundle_id), None)
            if replay is not None:
                if replay == line_bytes:
                    post_stat = os.fstat(write_fd)
                    return DemandOutboxAppendReceipt(
                        outbox_locator=DEMAND_OUTBOX_LOCATOR,
                        bundle_id=bundle_id,
                        bundle_sha256=bytes_sha256(line_bytes[:-1]),
                        replayed=True,
                        file_device=pre_stat.st_dev,
                        file_inode=pre_stat.st_ino,
                        pre_size=pre_stat.st_size,
                        post_size=post_stat.st_size,
                        line_count=len(lines),
                        appended_line_sha256=bytes_sha256(line_bytes[:-1]),
                        locked_exclusively=True,
                        fsynced=False,
                        parent_fsynced=False,
                        appended_at=now,
                    )
                raise DemandOutboxConflictError(
                    f"bundle id {bundle_id} already exists with different bytes"
                )
            _enforce_budgets(lines, now=now)
            written = os.write(write_fd, line_bytes)
            if written != len(line_bytes):
                raise DemandOutboxWriteError(
                    f"single append syscall wrote {written} of {len(line_bytes)} bytes"
                )
            try:
                os.fsync(write_fd)
            except OSError as error:
                raise DemandOutboxWriteError(f"fsync failed after append: {error}") from error
            try:
                # Persist the directory entry too, so a post-receipt crash
                # cannot lose a newly created outbox file.
                os.fsync(parent_fd)
                parent_fsynced = True
            except OSError as error:
                raise DemandOutboxWriteError(f"parent fsync failed: {error}") from error
            post_stat = os.fstat(write_fd)
            if post_stat.st_size != pre_stat.st_size + len(line_bytes):
                raise DemandOutboxWriteError("outbox size moved by more than the appended line")
            return DemandOutboxAppendReceipt(
                outbox_locator=DEMAND_OUTBOX_LOCATOR,
                bundle_id=bundle_id,
                bundle_sha256=content_sha256(payload),
                replayed=False,
                file_device=pre_stat.st_dev,
                file_inode=pre_stat.st_ino,
                pre_size=pre_stat.st_size,
                post_size=post_stat.st_size,
                line_count=len(lines) + 1,
                appended_line_sha256=bytes_sha256(line_bytes[:-1]),
                locked_exclusively=True,
                fsynced=True,
                parent_fsynced=parent_fsynced,
                appended_at=now,
            )
        finally:
            fcntl.flock(write_fd, fcntl.LOCK_UN)
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(parent_fd)
