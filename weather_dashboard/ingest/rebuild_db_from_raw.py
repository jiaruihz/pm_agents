"""
rebuild_db_from_raw.py

Batch-ingest all ledger CSV files in a directory into a SQLite DB.
Idempotent: safe to re-run on the same data.
"""

import csv
import os
from pathlib import Path

from weather_dashboard.db.apply_schema import init_db
from weather_dashboard.db.connection import get_conn
from weather_dashboard.ingest.ledger_csv import ingest_ledger_csv
from weather_dashboard.ingest.settlements import ingest_settlement_rows


def rebuild_db(
    csv_dir: str,
    db_path: str,
    run_id: str,
    config_id: str,
    dry_run: bool = False,
) -> dict:
    """
    Read all *.csv files in csv_dir (sorted by name), initialise the SQLite DB
    at db_path, and ingest each file via ingest_ledger_csv + ingest_settlement_rows.

    dry_run=True prints the file list without writing anything.

    Returns {"files_processed": N, "rows_inserted": M}.
    """
    csv_files = sorted(Path(csv_dir).glob("*.csv"))

    if dry_run:
        print(f"[dry-run] Would process {len(csv_files)} file(s):")
        for f in csv_files:
            print(f"  {f}")
        return {"files_processed": len(csv_files), "rows_inserted": 0}

    if not csv_files:
        return {"files_processed": 0, "rows_inserted": 0}

    init_db(db_path)
    conn = get_conn(db_path)

    total_inserted = 0
    try:
        for csv_file in csv_files:
            source_path = str(csv_file.resolve())
            with open(csv_file, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))

            total_inserted += ingest_ledger_csv(conn, rows, source_path, run_id, config_id)
            total_inserted += ingest_settlement_rows(conn, rows, source_path)
    finally:
        conn.close()

    return {"files_processed": len(csv_files), "rows_inserted": total_inserted}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Rebuild weather dashboard DB from raw CSV files")
    parser.add_argument("--csv-dir", required=True, help="Directory containing ledger CSV files")
    parser.add_argument("--db-path", required=True, help="Path to SQLite database file")
    parser.add_argument("--run-id", required=True, help="run_id to associate with all ingested rows")
    parser.add_argument("--config-id", required=True, help="config_id to associate with all ingested rows")
    parser.add_argument("--dry-run", action="store_true", help="Print files without writing to DB")
    args = parser.parse_args()

    result = rebuild_db(
        csv_dir=args.csv_dir,
        db_path=args.db_path,
        run_id=args.run_id,
        config_id=args.config_id,
        dry_run=args.dry_run,
    )
    print(result)
