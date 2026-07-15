#!/usr/bin/env python3
"""Register the heat-death late-carry tiny-live probe in canonical lineage."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "runtime/weather.db"

STRATEGY_KEY = "current_yes_heat_death_physical"
INSTANCE_ID = "current_yes_heat_death_tiny_live_v1"
CONFIG_ID = "current_yes_heat_death_tiny_live_v1_fixed10"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=3000")
    statements = [
        (
            """INSERT INTO strategy_def
               (strategy_key, family, strategy_group, domain, description, is_active,
                strategy_name, runner_module, meta_json, def_source, portfolio_status, portfolio_note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(strategy_key) DO UPDATE SET
                 family=excluded.family, description=excluded.description,
                 runner_module=excluded.runner_module, meta_json=excluded.meta_json,
                 portfolio_status=excluded.portfolio_status, portfolio_note=excluded.portfolio_note,
                 updated_at_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (
                STRATEGY_KEY,
                "reheat_risk.current_yes",
                "weather",
                "weather",
                "Late-carry BUY current YES after heat-death physical confirmation.",
                1,
                "current YES heat-death late carry",
                "scripts/ops/weather_current_yes_heat_death_tiny_live_v1.py",
                json.dumps(
                    {
                        "research_verdict": "historical_proxy_positive_forward_probe",
                        "source_doc": "docs/analysis/2026-07/2026-07-14-current-yes-heat-death-physical-backtest-v1.md",
                    }
                ),
                "instance_family",
                "tiny_live_forward_probe",
                "10-share probe only; not confirmed or approved for size-up",
            ),
        ),
        (
            """INSERT INTO strategy_config (config_id, name, params, strategy_key)
               VALUES (?,?,?,?)
               ON CONFLICT(config_id) DO UPDATE SET
                 name=excluded.name, params=excluded.params, strategy_key=excluded.strategy_key""",
            (
                CONFIG_ID,
                "heat-death late carry fixed 10 shares",
                json.dumps(
                    {
                        "expression": "current_bracket BUY_YES",
                        "eligibility": "physical_confirmation_strong",
                        "sizing": "fixed 10 shares",
                        "dedupe": "one submitted order per city-target_date",
                        "max_orders_per_utc_day": 3,
                        "max_ask": 0.99,
                        "min_top_ask_shares": 10,
                        "max_snapshot_age_min": 20,
                        "order_ttl_min": 15,
                        "execution": "fresh-book taker-capable GTC",
                    }
                ),
                STRATEGY_KEY,
            ),
        ),
        (
            """INSERT INTO strategy_instance
               (instance_id, strategy_key, display_name, family, lifecycle_status, execution_mode,
                desired_status, source_layer, runtime_dir, start_script, tmux_session,
                expected_live, notes, config_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(instance_id) DO UPDATE SET
                 lifecycle_status=excluded.lifecycle_status, execution_mode=excluded.execution_mode,
                 desired_status=excluded.desired_status, runtime_dir=excluded.runtime_dir,
                 start_script=excluded.start_script, tmux_session=excluded.tmux_session,
                 expected_live=excluded.expected_live, notes=excluded.notes, config_id=excluded.config_id,
                 updated_at_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (
                INSTANCE_ID,
                STRATEGY_KEY,
                "current YES heat-death 10-share probe",
                "reheat_risk.current_yes",
                "tiny_live_probe",
                "tiny_live_taker_probe",
                "enabled",
                "runtime_local",
                "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_v1",
                "scripts/ops/start_weather_current_yes_heat_death_tiny_live_v1.sh",
                "weather_current_yes_heat_death_tiny_live_v1",
                1,
                "explicit user-approved 10-share forward probe; no scale-up implication",
                CONFIG_ID,
            ),
        ),
    ]
    try:
        for sql, params in statements:
            print(("[dry] " if args.dry_run else "[run] ") + sql.strip().splitlines()[0])
            if not args.dry_run:
                conn.execute(sql, params)
        if not args.dry_run:
            conn.commit()
        for table, column, key in (
            ("strategy_def", "strategy_key", STRATEGY_KEY),
            ("strategy_config", "config_id", CONFIG_ID),
            ("strategy_instance", "instance_id", INSTANCE_ID),
        ):
            count = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (key,)).fetchone()[0]
            print(f"{table}: {count} row for {key}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
