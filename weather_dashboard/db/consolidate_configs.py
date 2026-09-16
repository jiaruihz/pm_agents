"""Build / refresh the config_aliases table so fragmented strategy_config
rows resolve back to a single canonical strategy identity.

Why aliases instead of UPDATE
-----------------------------
strategy_config / plans / runs all have BEFORE UPDATE and BEFORE DELETE
triggers (`xxx is append-only`). The blood-lineage design is intentionally
immutable. Instead of rewriting old rows we keep them and add a side table:

    config_aliases(alias_config_id PK, canonical_config_id, created_at_utc)

For every existing strategy_config row we compute the canonical id via the
identity-only hash (see legacy_migration.live_cycle._strategy_config_id),
INSERT OR IGNORE the canonical strategy_config row, and INSERT OR IGNORE the
alias mapping. A canonical row is its own alias as well.

Equity / list / metrics queries JOIN through config_aliases:

    JOIN config_aliases ca ON ca.alias_config_id = r.config_id
    WHERE ca.canonical_config_id = ?

Idempotent: re-running is a no-op once aliases are in place.

Run:
    python -m weather_dashboard.db.consolidate_configs --db-path runtime/weather.db
    python -m weather_dashboard.db.consolidate_configs --db-path runtime/weather.db --dry-run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from typing import Any

from weather_dashboard.legacy_migration.live_cycle import _strategy_config_id


def _params_populated_score(params: dict[str, Any]) -> int:
    return sum(1 for v in params.values() if v not in (None, "", "null"))


def ensure_aliases_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS config_aliases (
            alias_config_id     TEXT PRIMARY KEY,
            canonical_config_id TEXT NOT NULL,
            created_at_utc      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            FOREIGN KEY (canonical_config_id) REFERENCES strategy_config(config_id)
        );
        CREATE INDEX IF NOT EXISTS idx_config_aliases_canonical
            ON config_aliases(canonical_config_id);
        """
    )
    conn.commit()


def consolidate(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    ensure_aliases_table(conn)

    rows = conn.execute(
        "SELECT config_id, name, params FROM strategy_config"
    ).fetchall()

    # legacy_research_weather_edge is hardcoded (no hash). It aliases to itself.
    # live_weather_edge_v1_* rows get re-hashed via the identity-only function.
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        if r["config_id"] == "legacy_research_weather_edge":
            groups[r["config_id"]].append(r)
            continue
        params = json.loads(r["params"] or "{}")
        canonical_id = _strategy_config_id(params)
        groups[canonical_id].append(r)

    summary_moves: list[dict[str, Any]] = []
    canonical_inserts = 0
    aliases_added = 0

    for canonical_id, fragments in groups.items():
        # Choose representative params from the most-populated fragment.
        rep = max(
            fragments,
            key=lambda r: _params_populated_score(json.loads(r["params"] or "{}")),
        )
        rep_params = json.loads(rep["params"] or "{}")
        rep_name = rep["name"]

        if not dry_run:
            cur = conn.execute(
                "INSERT OR IGNORE INTO strategy_config (config_id, name, params) "
                "VALUES (?, ?, ?)",
                (
                    canonical_id,
                    rep_name,
                    json.dumps(rep_params, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")),
                ),
            )
            canonical_inserts += cur.rowcount or 0

        old_ids = [r["config_id"] for r in fragments]
        # canonical aliases to itself, plus every fragment aliases to canonical.
        all_aliases = set(old_ids) | {canonical_id}

        for alias in all_aliases:
            if dry_run:
                exists = conn.execute(
                    "SELECT 1 FROM config_aliases WHERE alias_config_id = ?", (alias,)
                ).fetchone()
                if not exists:
                    aliases_added += 1
            else:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO config_aliases "
                    "(alias_config_id, canonical_config_id) VALUES (?, ?)",
                    (alias, canonical_id),
                )
                aliases_added += cur.rowcount or 0

        # Tally remap counts (for visibility, even though we don't UPDATE).
        plan_remap = sum(
            conn.execute(
                "SELECT COUNT(*) FROM plans WHERE config_id = ?", (old,)
            ).fetchone()[0]
            for old in old_ids if old != canonical_id
        )
        run_remap = sum(
            conn.execute(
                "SELECT COUNT(*) FROM runs WHERE config_id = ?", (old,)
            ).fetchone()[0]
            for old in old_ids if old != canonical_id
        )

        if len(fragments) > 1 or (len(fragments) == 1 and fragments[0]["config_id"] != canonical_id):
            summary_moves.append({
                "canonical_id": canonical_id,
                "canonical_name": rep_name,
                "fragments": old_ids,
                "fragment_count": len(old_ids),
                "plans_aliased": plan_remap,
                "runs_aliased": run_remap,
            })

    if not dry_run:
        conn.commit()

    return {
        "dry_run": dry_run,
        "groups_total": len(groups),
        "groups_with_fragments": len(summary_moves),
        "canonical_rows_inserted": canonical_inserts,
        "aliases_added": aliases_added,
        "moves": summary_moves,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="runtime/weather.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    try:
        result = consolidate(conn, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
