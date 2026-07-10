"""Sync git-authored strategy metadata + instance specs into the DB control plane.

Populates strategy_def as the unified metadata catalog from two git-authored
sources — the file manifests (src/strategies/<key>/manifest.yaml, def_source=
'manifest') and the weather head families derived from instances.yaml
(def_source='instance_family') — plus strategy_instance. desired_status is
operator-owned and deliberately NOT updated on re-sync.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.strategies.registry import load_strategy_catalog
from src.strategies.runtime.specs import (
    StrategySpec,
    load_instance_specs,
    params_hash,
    spec_commit,
)
from src.strategies.runtime.definitions import load_strategy_definitions
from src.strategies.runtime.ownership import backfill_order_instance_links, sync_config_ownership


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sync_manifest_defs(conn: sqlite3.Connection, commit: str | None, now: str) -> int:
    """Load the git-authored file manifests (src/strategies/<key>/manifest.yaml)
    into strategy_def as the unified metadata catalog (def_source='manifest')."""
    manifests = load_strategy_catalog()
    for m in manifests:
        conn.execute(
            """
            INSERT INTO strategy_def
                (strategy_key, family, strategy_group, domain, strategy_name,
                 runner_module, strategy_module, meta_json, def_source, description,
                 is_active, spec_commit, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manifest', ?, ?, ?, ?)
            ON CONFLICT(strategy_key) DO UPDATE SET
                family=excluded.family,
                strategy_group=excluded.strategy_group,
                domain=excluded.domain,
                strategy_name=excluded.strategy_name,
                runner_module=excluded.runner_module,
                strategy_module=excluded.strategy_module,
                meta_json=excluded.meta_json,
                def_source=excluded.def_source,
                description=excluded.description,
                is_active=excluded.is_active,
                spec_commit=excluded.spec_commit,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                m.strategy_key, m.strategy_family or "", m.strategy_group or "weather",
                m.domain or "weather", m.strategy_name, m.runner_module or "",
                m.strategy_module or "", json.dumps(m.meta or {}, ensure_ascii=False),
                m.description or "", int(bool(m.is_active)), commit, now,
            ),
        )
    return len(manifests)


def _sync_runtime_defs(conn: sqlite3.Connection, commit: str | None, now: str) -> int:
    definitions = load_strategy_definitions()
    for definition in definitions:
        conn.execute(
            """
            INSERT INTO strategy_def
                (strategy_key, family, strategy_group, domain, strategy_name,
                 meta_json, def_source, description, portfolio_status, portfolio_note,
                 is_active, spec_commit, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, 'runtime_definition', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_key) DO UPDATE SET
                family=excluded.family,
                strategy_group=excluded.strategy_group,
                domain=excluded.domain,
                strategy_name=excluded.strategy_name,
                meta_json=excluded.meta_json,
                def_source=CASE
                    WHEN strategy_def.def_source='manifest' THEN strategy_def.def_source
                    ELSE excluded.def_source
                END,
                description=excluded.description,
                portfolio_status=excluded.portfolio_status,
                portfolio_note=excluded.portfolio_note,
                is_active=excluded.is_active,
                spec_commit=excluded.spec_commit,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                definition.strategy_key, definition.family, definition.strategy_group,
                definition.domain, definition.strategy_name,
                json.dumps(definition.meta, ensure_ascii=False), definition.description,
                definition.portfolio_status, definition.portfolio_note,
                int(definition.is_active), commit, now,
            ),
        )
    return len(definitions)


def sync_instance_specs(
    conn: sqlite3.Connection, specs: list[StrategySpec] | None = None
) -> dict:
    specs = specs if specs is not None else load_instance_specs()
    commit = spec_commit()
    now = _now()

    # (a) file manifests → strategy_def (def_source='manifest')
    manifest_def_rows = _sync_manifest_defs(conn, commit, now)

    # (b) explicit weather definitions and a legacy fallback for any family
    # not yet described in definitions.yaml.
    runtime_def_rows = _sync_runtime_defs(conn, commit, now)
    families = sorted({s.family for s in specs})
    for family in families:
        conn.execute(
            """
            INSERT INTO strategy_def (strategy_key, family, def_source, spec_commit, updated_at_utc)
            VALUES (?, ?, 'instance_family', ?, ?)
            ON CONFLICT(strategy_key) DO UPDATE SET
                family=excluded.family,
                spec_commit=excluded.spec_commit,
                updated_at_utc=excluded.updated_at_utc
            """,
            (family, family, commit, now),
        )

    ownership = sync_config_ownership(conn)
    registered_config_ids = {
        str(row[0]) for row in conn.execute("SELECT config_id FROM strategy_config").fetchall()
    }
    unregistered_instance_configs = 0

    for s in specs:
        config_id = s.config_id if s.config_id in registered_config_ids else None
        if s.config_id and config_id is None:
            unregistered_instance_configs += 1
        conn.execute(
            """
            INSERT INTO strategy_instance
                (instance_id, strategy_key, display_name, family, lifecycle_status,
                 execution_mode, config_id, source_layer, runtime_dir, start_script, tmux_session,
                 expected_live, spec_commit, params_hash, notes, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance_id) DO UPDATE SET
                strategy_key=excluded.strategy_key,
                display_name=excluded.display_name,
                family=excluded.family,
                lifecycle_status=excluded.lifecycle_status,
                execution_mode=excluded.execution_mode,
                config_id=excluded.config_id,
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
                s.lifecycle_status, s.execution_mode, config_id, s.source_layer, s.runtime_dir,
                s.start_script, s.tmux_session,
                int(s.expected_live) if s.expected_live is not None else None,
                commit, params_hash(s), s.notes, now,
            ),
        )

    order_links = backfill_order_instance_links(conn)
    conn.commit()
    return {
        "manifest_def_rows": manifest_def_rows,
        "runtime_def_rows": runtime_def_rows,
        "family_def_rows": len(families),
        "instance_rows": len(specs),
        "unregistered_instance_configs": unregistered_instance_configs,
        **ownership,
        **order_links,
        "spec_commit": commit,
    }
