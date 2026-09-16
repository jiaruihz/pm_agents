"""CLI tool to remove orphan runs (runs with zero fills)."""

import argparse
import sys

from weather_dashboard.db.connection import get_conn


def list_orphan_runs(conn) -> list[dict]:
    """Return runs that have no associated fills."""
    rows = conn.execute(
        """
        SELECT r.run_id, r.state, r.execution_mode, r.created_at_utc,
               COUNT(f.fill_id) AS fill_count
        FROM runs r
        LEFT JOIN orders o ON o.run_id = r.run_id
        LEFT JOIN fills f  ON f.order_id = o.order_id AND f.status = 'filled'
        GROUP BY r.run_id
        HAVING fill_count = 0
        ORDER BY r.created_at_utc
        """
    ).fetchall()
    return [dict(r) for r in rows]


def delete_run(conn, run_id: str, dry_run: bool = False) -> int:
    """
    Delete a run and all its child records.
    Returns number of rows deleted from `runs`.
    Raises ValueError if the run has fills (safety check).
    """
    fill_count = conn.execute(
        """
        SELECT COUNT(*) FROM fills f
        JOIN orders o ON f.order_id = o.order_id
        WHERE o.run_id = ? AND f.status = 'filled'
        """,
        (run_id,),
    ).fetchone()[0]

    if fill_count > 0:
        raise ValueError(
            f"Run {run_id} has {fill_count} fill(s) — refusing to delete. "
            "Only orphan runs (0 fills) can be cleaned up."
        )

    if dry_run:
        print(f"[dry-run] would delete run {run_id} and its child records")
        return 0

    # Delete child records in dependency order
    # fills → orders → plans → (signals are shared, leave them) → runs
    conn.execute(
        "DELETE FROM fills WHERE order_id IN (SELECT order_id FROM orders WHERE run_id = ?)",
        (run_id,),
    )
    conn.execute(
        "DELETE FROM orders WHERE run_id = ?",
        (run_id,),
    )
    conn.execute(
        "DELETE FROM plans WHERE run_id = ?",
        (run_id,),
    )
    conn.execute(
        "DELETE FROM ingestion_log WHERE source_path LIKE ? OR source_path LIKE ?",
        (f"%{run_id}%", f"%{run_id}%"),
    )
    deleted = conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,)).rowcount
    conn.commit()
    return deleted


def main():
    parser = argparse.ArgumentParser(description="Clean up orphan runs with zero fills")
    parser.add_argument("--db-path", default="runtime/weather.db", help="SQLite DB path")
    parser.add_argument("--run-id", help="Delete a specific run ID (must have 0 fills)")
    parser.add_argument("--all-orphans", action="store_true", help="Delete ALL runs with 0 fills")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted without doing it")
    args = parser.parse_args()

    conn = get_conn(args.db_path)

    if not args.run_id and not args.all_orphans:
        # Just list orphans
        orphans = list_orphan_runs(conn)
        if not orphans:
            print("No orphan runs found.")
        else:
            print(f"Found {len(orphans)} orphan run(s):")
            for r in orphans:
                print(f"  {r['run_id']}  state={r['state']}  mode={r['execution_mode']}  created={r['created_at_utc']}")
            print("\nRe-run with --all-orphans to delete them, or --run-id <id> to delete one.")
        conn.close()
        return

    if args.run_id:
        try:
            n = delete_run(conn, args.run_id, dry_run=args.dry_run)
            if not args.dry_run:
                print(f"Deleted run {args.run_id} ({n} row(s) removed from runs table)")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.all_orphans:
        orphans = list_orphan_runs(conn)
        if not orphans:
            print("No orphan runs to delete.")
        else:
            for r in orphans:
                try:
                    n = delete_run(conn, r["run_id"], dry_run=args.dry_run)
                    if args.dry_run:
                        print(f"[dry-run] would delete {r['run_id']}")
                    else:
                        print(f"Deleted {r['run_id']} ({n} row removed)")
                except ValueError as e:
                    print(f"Skipped {r['run_id']}: {e}", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()
