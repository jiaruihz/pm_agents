#!/usr/bin/env python3
"""Register the user-authorized d1 YES split live policy in canonical lineage."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "runtime/weather.db"
STRATEGY_KEY = "d1_yes_high_mid"
INSTANCE_ID = "d1_yes_high_mid_live_v1"
OLD_INSTANCE_ID = "d1_yes_high_mid_shadow_v1"
CONFIG_ID = "d1_yes_high_mid_live_v1_user_policy"
FAMILY = "market_structure_edge.favorite_low_estimation"
RUNTIME_DIR = "runtime/weather_edge_v1/d1_yes_high_mid_live_v1"
START_SCRIPT = "scripts/ops/start_d1_yes_high_mid_live.sh"
SOURCE_DOC = "docs/analysis/2026-07/2026-07-15-d1-yes-high-mid-strategy-v1.md"

PARAMS = {
    "rule_id": "d1_yes_mid_ge_0p80_first_per_city_date_taker_v1",
    "trigger": "fresh d1 YES mid >= 0.80",
    "d1_definition": "immediate bounded bracket above rounded-running current bracket",
    "city_policy": {"Taipei": "zero_notional_shadow", "other_cities": "tiny_live"},
    "non_exact_policy": "open-upper X+ and invalid ladder states remain shadow",
    "sizing": "fixed 5 shares",
    "max_orders_per_day": 10,
    "max_daily_cost_usd": 50,
    "execution": "fresh CLOB quote/depth recheck, BUY_YES taker limit at top ask",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps(PARAMS, ensure_ascii=False, indent=2))
        return 0

    conn = sqlite3.connect(args.db, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=3000")
    try:
        conn.execute(
            """UPDATE strategy_def SET portfolio_status=?, portfolio_note=?, meta_json=?,
               updated_at_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE strategy_key=?""",
            (
                "tiny_live_user_authorized",
                "Taipei zero-notional shadow; other cities fixed 5-share live with exact-ladder and fresh-book execution checks",
                json.dumps({"source_doc": SOURCE_DOC, "research_verdict": "inconclusive_positive_signal", "live_authority": "explicit_user_policy_2026-07-16"}),
                STRATEGY_KEY,
            ),
        )
        conn.execute(
            """INSERT INTO strategy_config (config_id, name, params, strategy_key)
               VALUES (?,?,?,?) ON CONFLICT(config_id) DO UPDATE SET
               name=excluded.name, params=excluded.params, strategy_key=excluded.strategy_key""",
            (CONFIG_ID, "d1 YES high-mid Taipei-shadow split live v1", json.dumps(PARAMS), STRATEGY_KEY),
        )
        conn.execute(
            """INSERT INTO strategy_instance
               (instance_id, strategy_key, display_name, family, lifecycle_status, execution_mode,
                desired_status, source_layer, runtime_dir, start_script, tmux_session,
                expected_live, notes, config_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(instance_id) DO UPDATE SET
                display_name=excluded.display_name, lifecycle_status=excluded.lifecycle_status,
                execution_mode=excluded.execution_mode, desired_status=excluded.desired_status,
                runtime_dir=excluded.runtime_dir, start_script=excluded.start_script,
                tmux_session=excluded.tmux_session, expected_live=excluded.expected_live,
                notes=excluded.notes, config_id=excluded.config_id,
                updated_at_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (
                INSTANCE_ID, STRATEGY_KEY, "d1 YES high-mid live (Taipei shadow)", FAMILY,
                "live", "live", "enabled", "runtime_local",
                RUNTIME_DIR, START_SCRIPT, INSTANCE_ID, 1,
                "explicit user-authorized split policy; open-upper and invalid ladder states remain shadow",
                CONFIG_ID,
            ),
        )
        conn.execute(
            """UPDATE strategy_instance SET desired_status='shelved',
               notes='superseded-for-now by d1_yes_high_mid_live_v1; historical zero-notional evidence retained',
               updated_at_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE instance_id=?""",
            (OLD_INSTANCE_ID,),
        )
        conn.execute(
            """UPDATE weather_strategy_shadow_queue SET status='active',
               notes='Taipei remains shadow inside d1_yes_high_mid_live_v1; other cities user-authorized tiny-live',
               refreshed_at_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE shadow_id=?""",
            (OLD_INSTANCE_ID,),
        )
        conn.commit()
        for table, key, col in (
            ("strategy_def", STRATEGY_KEY, "strategy_key"),
            ("strategy_config", CONFIG_ID, "config_id"),
            ("strategy_instance", INSTANCE_ID, "instance_id"),
        ):
            count = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=?", (key,)).fetchone()[0]
            print(f"{table}: {count} row for {key}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
