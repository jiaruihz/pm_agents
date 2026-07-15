#!/usr/bin/env python3
"""Idempotently register the d1_yes_high_mid_v1 shadow into the canonical
strategy lineage tables (strategy_def / strategy_instance / strategy_config /
weather_strategy_shadow_queue).

Zero-notional shadow: no orders/fills/fact_trades rows are created.  The rich
runtime-status table (weather_strategy_runtime_registry) is left to the existing
refresher, fed by the runtime monitor WatchSpec added in weather_runtime_monitor.py.

Safe to re-run; uses INSERT OR REPLACE / upsert.  Run with --dry-run to preview.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "runtime/weather.db"

STRATEGY_KEY = "d1_yes_high_mid"
INSTANCE_ID = "d1_yes_high_mid_shadow_v1"
FAMILY = "market_structure_edge.favorite_low_estimation"
DISPLAY = "d1 YES high-mid favorite low-estimation shadow"
RUNTIME_DIR = "runtime/weather_edge_v1/d1_yes_high_mid_shadow_v1"
START_SCRIPT = "scripts/ops/start_d1_yes_high_mid_shadow.sh"
TMUX_SESSION = "d1_yes_high_mid_shadow_v1"
SOURCE_DOC = "docs/analysis/2026-07/2026-07-15-d1-yes-high-mid-strategy-v1.md"
RUNNER_MODULE = "scripts/ops/d1_yes_high_mid_shadow_v1.py"
CONFIG_ID = "d1_yes_high_mid_shadow_v1_frozen_rule_v1"

RULE_PARAMS = {
    "rule_id": "d1_yes_mid_ge_0p80_first_per_city_date_taker_v1",
    "trigger": "d1_yes_mid >= 0.80",
    "d1_definition": "tail_distance==1 above running-max bracket (factory add_state_siblings semantics)",
    "entry": "taker at 1 - d1_no_bid",
    "fee": "0.05*p*(1-p) per share",
    "dedupe": "first qualifying poll per (city, target_date) -> promotion track",
    "freshness_gate_min": 45,
    "no_extra_filter": True,
    "v11_secondary_guards": {"ask_le": 0.95, "remaining_heat_le": 1.3},
    "hold": "to settlement, no stop/take-profit",
    "sizing_if_promoted": "fixed 5 shares taker (not authorized until gate passes)",
}


def upsert(conn: sqlite3.Connection, sql: str, params: tuple, dry: bool) -> None:
    print(("[dry] " if dry else "[run] ") + sql.strip().split("\n")[0][:90])
    if not dry:
        conn.execute(sql, params)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--activate", action="store_true",
                    help="set shadow_queue status=active (use after the runner is started)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=3000")
    try:
        upsert(
            conn,
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
                STRATEGY_KEY, FAMILY, "weather", "weather",
                "Favorite-side low-estimation harvest: buy d1 YES when market mid>=0.80; "
                "only calibration cell that may clear taker friction (calibration curve v1).",
                1, "d1 YES high-mid", RUNNER_MODULE,
                json.dumps({"source_doc": SOURCE_DOC, "verdict": "inconclusive_positive_signal_shadow_only"}),
                "instance_family", "shadow_candidate",
                "zero-notional shadow; needs full-ladder coverage + 10 fwd dates + depth audit before tiny-live",
            ),
            args.dry_run,
        )

        upsert(
            conn,
            """INSERT INTO strategy_config (config_id, name, params, strategy_key)
               VALUES (?,?,?,?)
               ON CONFLICT(config_id) DO UPDATE SET
                 name=excluded.name, params=excluded.params, strategy_key=excluded.strategy_key""",
            (CONFIG_ID, "d1_yes_high_mid frozen rule v1", json.dumps(RULE_PARAMS), STRATEGY_KEY),
            args.dry_run,
        )

        upsert(
            conn,
            """INSERT INTO strategy_instance
               (instance_id, strategy_key, display_name, family, lifecycle_status, execution_mode,
                desired_status, source_layer, runtime_dir, start_script, tmux_session,
                expected_live, notes, config_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(instance_id) DO UPDATE SET
                 lifecycle_status=excluded.lifecycle_status, execution_mode=excluded.execution_mode,
                 desired_status=excluded.desired_status, runtime_dir=excluded.runtime_dir,
                 start_script=excluded.start_script, tmux_session=excluded.tmux_session,
                 notes=excluded.notes, config_id=excluded.config_id,
                 updated_at_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (
                INSTANCE_ID, STRATEGY_KEY, DISPLAY, FAMILY, "shadow", "zero_notional_shadow",
                "enabled", "runtime_local", RUNTIME_DIR, START_SCRIPT, TMUX_SESSION,
                0, "frozen rule v1; parallel v1.1 guards recorded; not live", CONFIG_ID,
            ),
            args.dry_run,
        )

        upsert(
            conn,
            """INSERT INTO weather_strategy_shadow_queue
               (shadow_id, display_name, family, proposed_execution_mode, priority, status,
                source_doc, target_runtime_dir, required_fields_json, blockers_json, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(shadow_id) DO UPDATE SET
                 status=excluded.status, source_doc=excluded.source_doc,
                 required_fields_json=excluded.required_fields_json,
                 blockers_json=excluded.blockers_json, notes=excluded.notes,
                 refreshed_at_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (
                INSTANCE_ID, DISPLAY, FAMILY, "zero_notional_shadow", "medium",
                "active" if args.activate else "runner_ready",
                SOURCE_DOC, RUNTIME_DIR,
                json.dumps(["d1_no_bid_size", "d1_no_depth_bid_5c", "obs_age_min",
                            "forecast_remaining_heat_native", "full_ladder_coverage"]),
                json.dumps(["narrow_targeted_coverage_until_snapshot_full",
                            "forecast_remaining_heat_join_pending"]),
                "favorite low-estimation; only calibration cell that may clear taker friction",
            ),
            args.dry_run,
        )

        if not args.dry_run:
            conn.commit()
        print("done." + (" (dry-run, nothing written)" if args.dry_run else ""))

        # echo back
        for tbl, key, col in [
            ("strategy_def", STRATEGY_KEY, "strategy_key"),
            ("strategy_instance", INSTANCE_ID, "instance_id"),
            ("strategy_config", CONFIG_ID, "config_id"),
            ("weather_strategy_shadow_queue", INSTANCE_ID, "shadow_id"),
        ]:
            n = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {col}=?", (key,)).fetchone()[0]
            print(f"  {tbl}: {n} row for {key}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
