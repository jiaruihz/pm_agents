"""
run_create.py

Create a new run entry in the DB and print its run_id.
repro_key = sha256(config_id|code_version|universe_id|date_range_start|date_range_end)[:32]

Usage:
    python -m weather_dashboard.cli.run_create \
        --config-id abc123 \
        --universe-id t24_cities_v1 \
        --code-version $(git rev-parse HEAD) \
        --execution-mode snapshot_replay \
        --date-range-start 2026-01-01 \
        --date-range-end 2026-05-01 \
        --db-path runtime/weather.db \
        [--state explore] \
        [--tags '["paper","v2"]'] \
        [--notes "initial backtest"]
"""

import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timezone


def _repro_key(config_id: str, code_version: str, universe_id: str,
               date_range_start: str, date_range_end: str) -> str:
    raw = "|".join([config_id, code_version, universe_id,
                    date_range_start or "", date_range_end or ""])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _current_git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"


def create_run(
    conn,
    config_id: str,
    universe_id: str,
    code_version: str,
    execution_mode: str,
    date_range_start: str = None,
    date_range_end: str = None,
    state: str = "explore",
    tags: list = None,
    notes: str = None,
) -> str:
    """
    Insert a new run. Returns run_id.
    Verifies config_id and universe_id exist (FK).
    """
    # Verify FKs exist
    if not conn.execute("SELECT 1 FROM strategy_config WHERE config_id=?", (config_id,)).fetchone():
        raise ValueError(f"config_id not found: {config_id}")
    if not conn.execute("SELECT 1 FROM universes WHERE universe_id=?", (universe_id,)).fetchone():
        raise ValueError(f"universe_id not found: {universe_id}")

    # Ensure code_version row exists
    if not conn.execute("SELECT 1 FROM code_versions WHERE code_version=?", (code_version,)).fetchone():
        conn.execute("INSERT INTO code_versions (code_version) VALUES (?)", (code_version,))

    run_id = str(uuid.uuid4())
    repro = _repro_key(config_id, code_version, universe_id,
                       date_range_start or "", date_range_end or "")
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO runs
           (run_id, config_id, universe_id, code_version, execution_mode,
            date_range_start, date_range_end, started_at_utc, state,
            repro_key, tags, notes, created_at_utc)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id, config_id, universe_id, code_version, execution_mode,
            date_range_start, date_range_end, now, state,
            repro, json.dumps(tags or []), notes, now,
        ),
    )
    conn.commit()
    return run_id


def create_run_from_args(
    db_path: str,
    config_id: str,
    universe_id: str,
    code_version: str,
    execution_mode: str,
    date_range_start: str = None,
    date_range_end: str = None,
    state: str = "explore",
    tags: list = None,
    notes: str = None,
) -> str:
    from weather_dashboard.db.apply_schema import init_db
    from weather_dashboard.db.connection import get_conn

    init_db(db_path)
    conn = get_conn(db_path)
    try:
        return create_run(
            conn, config_id, universe_id, code_version, execution_mode,
            date_range_start, date_range_end, state, tags, notes,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Create a new run in the weather dashboard DB")
    parser.add_argument("--config-id", required=True)
    parser.add_argument("--universe-id", required=True)
    parser.add_argument("--code-version", default=None,
                        help="Git SHA (defaults to current HEAD)")
    parser.add_argument("--execution-mode", required=True,
                        choices=["snapshot_replay", "paper", "live"])
    parser.add_argument("--date-range-start")
    parser.add_argument("--date-range-end")
    parser.add_argument("--state", default="explore",
                        choices=["explore", "paper", "live", "retired"])
    parser.add_argument("--tags", default="[]", help="JSON array of tag strings")
    parser.add_argument("--notes")
    parser.add_argument("--db-path", required=True)
    args = parser.parse_args()

    code_version = args.code_version or _current_git_sha()
    tags = json.loads(args.tags)

    run_id = create_run_from_args(
        db_path=args.db_path,
        config_id=args.config_id,
        universe_id=args.universe_id,
        code_version=code_version,
        execution_mode=args.execution_mode,
        date_range_start=args.date_range_start,
        date_range_end=args.date_range_end,
        state=args.state,
        tags=tags,
        notes=args.notes,
    )
    print(f"run_id: {run_id}")
