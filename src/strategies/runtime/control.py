"""Control-plane mutations + append-only audit log.

Every start/stop/enable-live goes through record_control_action so there is
always a durable strategy_control_log row — this is what lets the DB-first
control plane satisfy the git-first "traceable" hard boundary.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_desired_status(conn: sqlite3.Connection, instance_id: str) -> str | None:
    row = conn.execute(
        "SELECT desired_status FROM strategy_instance WHERE instance_id=?",
        (instance_id,),
    ).fetchone()
    return row[0] if row else None


def set_desired_status(conn: sqlite3.Connection, instance_id: str, desired_status: str) -> None:
    conn.execute(
        "UPDATE strategy_instance SET desired_status=?, updated_at_utc=? WHERE instance_id=?",
        (desired_status, _now(), instance_id),
    )
    conn.commit()


def write_control_log(
    conn: sqlite3.Connection,
    *,
    instance_id: str,
    actor: str,
    action: str,
    from_state: str | None,
    to_state: str | None,
    reason: str | None,
    spec_commit: str | None,
    params_hash: str | None,
) -> str:
    log_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO strategy_control_log
            (log_id, instance_id, actor, action, from_state, to_state, reason,
             spec_commit, params_hash, ts_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (log_id, instance_id, actor, action, from_state, to_state, reason,
         spec_commit, params_hash, _now()),
    )
    conn.commit()
    return log_id


def record_control_action(
    conn: sqlite3.Connection,
    *,
    instance_id: str,
    action: str,
    to_state: str | None,
    reason: str | None,
    actor: str,
    spec_commit: str | None = None,
    params_hash: str | None = None,
) -> str:
    from_state = get_desired_status(conn, instance_id)
    if to_state is not None:
        set_desired_status(conn, instance_id, to_state)
    return write_control_log(
        conn, instance_id=instance_id, actor=actor, action=action,
        from_state=from_state, to_state=to_state, reason=reason,
        spec_commit=spec_commit, params_hash=params_hash,
    )
