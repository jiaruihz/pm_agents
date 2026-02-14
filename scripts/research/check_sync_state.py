#!/usr/bin/env python3
import datetime as dt
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.domains.research.db import get_connection


def main() -> None:
    conn = get_connection()
    cur = conn.cursor()

    print("sync_state:")
    rows = cur.execute(
        "SELECT source, active, page_size, offset, updated_at_utc "
        "FROM sync_state ORDER BY source, active, page_size"
    ).fetchall()
    for row in rows:
        print(
            f"- source={row['source']} active={row['active']} page_size={row['page_size']} "
            f"offset={row['offset']} updated_at_utc={row['updated_at_utc']}"
        )

    print("\nmarket counts:")
    total = cur.execute("SELECT COUNT(*) AS c FROM markets").fetchone()["c"]
    active = cur.execute(
        "SELECT COUNT(*) AS c FROM markets WHERE active=1 AND resolved=0"
    ).fetchone()["c"]
    print(f"- total={total}")
    print(f"- active_unresolved={active}")

    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    expired = cur.execute(
        "SELECT COUNT(*) AS c FROM markets WHERE end_at_utc IS NOT NULL AND date(end_at_utc) < ?",
        (today,),
    ).fetchone()["c"]
    print(f"- expired_before_today={expired} (today_utc={today})")

    print("\nlatest markets:")
    latest = cur.execute(
        "SELECT market_id, slug, end_at_utc, last_synced_at_utc "
        "FROM markets ORDER BY last_synced_at_utc DESC LIMIT 5"
    ).fetchall()
    for row in latest:
        print(
            f"- {row['market_id']} slug={row['slug']} end_at_utc={row['end_at_utc']} "
            f"last_synced_at_utc={row['last_synced_at_utc']}"
        )

    conn.close()


if __name__ == "__main__":
    main()
