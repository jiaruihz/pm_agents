"""
LEGACY v1 ingest entrypoint.

Do not use this CLI for the weather dashboard canonical rebuild path. The
normal path is now:

    db-canonical-rebuild -> ingest_legacy_research -> ingest_live_cycle

This module is kept only for old tests/manual forensics around the pre-canonical
schema and field aliases.

ingest_run.py

One-shot CLI: register config+universe (if needed), create a run, ingest a
real weather-strategy CSV into the DB, and persist computed metrics.

Usage:
    python -m weather_dashboard.cli.ingest_run \\
        --csv     runtime/weather_edge_v1/market_data/research/t24_paper_snapshot_replay_trades.csv \\
        --db-path runtime/weather.db \\
        --run-name "snapshot_replay_t24_v1" \\
        --execution-mode snapshot_replay \\
        [--config-name my_config] \\
        [--config-params '{"min_edge":0.08}'] \\
        [--universe-name "T24 Cities"] \\
        [--date-range-start 2026-01-01] \\
        [--date-range-end 2026-05-15] \\
        [--state explore] \\
        [--dry-run]
"""

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from weather_dashboard.cli.config_register import _config_id, register_config
from weather_dashboard.cli.run_create import _current_git_sha, create_run
from weather_dashboard.cli.universe_register import _universe_id, register_universe
from weather_dashboard.db.apply_schema import init_db
from weather_dashboard.db.connection import get_conn
from scripts.analysis.build_weather_fact_trades import FACT_DDL
from weather_dashboard.ingest.ledger_csv import ingest_ledger_csv
from weather_dashboard.ingest.real_ledger_adapter import adapt_rows
from weather_dashboard.ingest.settlements import ingest_settlement_rows
from weather_dashboard.metrics.save import save_metrics


def _infer_cities_models(rows: list[dict]) -> tuple[list[str], list[str]]:
    cities = sorted({r.get("city", "") for r in rows if r.get("city")})
    models = sorted({r.get("model", "") for r in rows if r.get("model")})
    return cities, models


def run_ingest(
    csv_path: str,
    db_path: str,
    run_name: str,
    execution_mode: str,
    config_name: str = "default",
    config_params: dict | None = None,
    universe_name: str = "weather_universe",
    date_range_start: str | None = None,
    date_range_end: str | None = None,
    state: str = "explore",
    dry_run: bool = False,
) -> dict:
    """
    Full pipeline: CSV → adapt → register config/universe/run → ingest → save metrics.
    Returns summary dict.
    """
    csv_file = Path(csv_path)
    if not csv_file.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    # Read and adapt rows
    with open(csv_file, newline="", encoding="utf-8") as fh:
        raw_rows = list(csv.DictReader(fh))
    rows = adapt_rows(raw_rows, default_mode=execution_mode)

    if dry_run:
        cities, models = _infer_cities_models(rows)
        print(f"[dry-run] {len(rows)} rows in {csv_path}")
        print(f"  cities: {cities}")
        print(f"  models: {models}")
        return {"dry_run": True, "rows": len(rows)}

    # Init DB
    init_db(db_path)
    conn = get_conn(db_path)
    conn.execute(FACT_DDL)
    conn.commit()

    try:
        # Ensure config
        params = config_params or {}
        cid = register_config(conn, config_name, params)

        # Ensure universe (infer cities/models from CSV)
        cities, models = _infer_cities_models(rows)
        uid = _universe_id(universe_name)
        register_universe(conn, uid, universe_name, "", cities, models)

        # Create run
        code_version = _current_git_sha()
        run_id = create_run(
            conn,
            config_id=cid,
            universe_id=uid,
            code_version=code_version,
            execution_mode=execution_mode,
            date_range_start=date_range_start,
            date_range_end=date_range_end,
            state=state,
            tags=[run_name],
            notes=f"Ingested from {csv_file.name}",
        )
        print(f"Created run: {run_id}")

        # Ingest
        source_path = str(csv_file.resolve())
        n_signals = ingest_ledger_csv(conn, rows, source_path, run_id, cid)
        n_settlements = ingest_settlement_rows(conn, rows, source_path)
        print(f"Inserted: {n_signals} signal/plan/order/fill rows, {n_settlements} settlement rows")

        # Compute and persist metrics
        metrics = save_metrics(conn, run_id)
        print(f"Metrics: {json.dumps(metrics, indent=2)}")

        return {
            "run_id": run_id,
            "rows_ingested": n_signals,
            "settlements": n_settlements,
            "metrics": metrics,
        }

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest a real weather CSV into the dashboard DB")
    parser.add_argument("--csv", required=True, help="Path to CSV file")
    parser.add_argument("--db-path", required=True, help="SQLite DB path")
    parser.add_argument("--run-name", required=True, help="Tag/label for this run")
    parser.add_argument("--execution-mode", required=True,
                        choices=["snapshot_replay", "paper", "live"])
    parser.add_argument("--config-name", default="weather_edge_v1")
    parser.add_argument("--config-params", default="{}", help="JSON object")
    parser.add_argument("--universe-name", default="weather_universe")
    parser.add_argument("--date-range-start")
    parser.add_argument("--date-range-end")
    parser.add_argument("--state", default="explore",
                        choices=["explore", "paper", "live", "retired"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run_ingest(
        csv_path=args.csv,
        db_path=args.db_path,
        run_name=args.run_name,
        execution_mode=args.execution_mode,
        config_name=args.config_name,
        config_params=json.loads(args.config_params),
        universe_name=args.universe_name,
        date_range_start=args.date_range_start,
        date_range_end=args.date_range_end,
        state=args.state,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
