from __future__ import annotations

import argparse
import json
from pathlib import Path

from weather_dashboard.db.apply_schema_canonical import init_db_canonical
from weather_dashboard.db.connection import get_conn
from weather_dashboard.legacy_migration.research_csv import (
    DEFAULT_RESEARCH_INPUTS,
    migrate_research_csv,
    write_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy weather research CSVs into canonical DB")
    parser.add_argument("--db-path", default="runtime/weather.db")
    parser.add_argument("--report-dir", default="runtime/weather_dashboard_migration/reports")
    parser.add_argument(
        "--input",
        action="append",
        nargs=3,
        metavar=("CSV", "MODE", "RUN_NAME"),
        help="Input triple. Can be repeated. Defaults to the two T24 research CSVs.",
    )
    args = parser.parse_args()

    init_db_canonical(args.db_path)
    inputs = args.input or DEFAULT_RESEARCH_INPUTS
    conn = get_conn(args.db_path)
    try:
        reports = [
            migrate_research_csv(
                conn,
                csv_path=Path(csv_path),
                execution_mode=execution_mode,
                run_name=run_name,
            )
            for csv_path, execution_mode, run_name in inputs
        ]
    finally:
        conn.close()

    report_path = write_report(reports, args.report_dir)
    print(json.dumps({
        "report_path": str(report_path),
        "reports": [report.as_dict() for report in reports],
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
