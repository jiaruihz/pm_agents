"""Sync git-authored instance specs into the DB control plane.

Upserts strategy_def (one row per family, keyed by family until real head
keys land in B3) and strategy_instance. desired_status is operator-owned and
deliberately NOT updated on re-sync.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.strategies.runtime.specs import (
    StrategySpec,
    load_instance_specs,
    params_hash,
    spec_commit,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sync_instance_specs(
    conn: sqlite3.Connection, specs: list[StrategySpec] | None = None
) -> dict:
    specs = specs if specs is not None else load_instance_specs()
    commit = spec_commit()
    now = _now()

    families = sorted({s.family for s in specs})
    for family in families:
        conn.execute(
            """
            INSERT INTO strategy_def (strategy_key, family, spec_commit, updated_at_utc)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(strategy_key) DO UPDATE SET
                family=excluded.family,
                spec_commit=excluded.spec_commit,
                updated_at_utc=excluded.updated_at_utc
            """,
            (family, family, commit, now),
        )

    for s in specs:
        conn.execute(
            """
            INSERT INTO strategy_instance
                (instance_id, strategy_key, display_name, family, lifecycle_status,
                 execution_mode, source_layer, runtime_dir, start_script, tmux_session,
                 expected_live, spec_commit, params_hash, notes, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance_id) DO UPDATE SET
                strategy_key=excluded.strategy_key,
                display_name=excluded.display_name,
                family=excluded.family,
                lifecycle_status=excluded.lifecycle_status,
                execution_mode=excluded.execution_mode,
                source_layer=excluded.source_layer,
                runtime_dir=excluded.runtime_dir,
                start_script=excluded.start_script,
                tmux_session=excluded.tmux_session,
                expected_live=excluded.expected_live,
                spec_commit=excluded.spec_commit,
                params_hash=excluded.params_hash,
                notes=excluded.notes,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                s.strategy_instance, s.family, s.display_name, s.family,
                s.lifecycle_status, s.execution_mode, s.source_layer, s.runtime_dir,
                s.start_script, s.tmux_session,
                int(s.expected_live) if s.expected_live is not None else None,
                commit, params_hash(s), s.notes, now,
            ),
        )

    conn.commit()
    return {"def_rows": len(families), "instance_rows": len(specs), "spec_commit": commit}
