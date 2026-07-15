#!/usr/bin/env python3
"""Register the heat-death tiny-live probe heads in canonical lineage.

Two separately attributed instances per the preregistered promotion criteria
(docs/analysis/2026-07/2026-07-15-heat-death-live-promotion-preregistration-v1.md).
The original single-cap instance ``current_yes_heat_death_tiny_live_v1`` never
submitted an order and is retired here.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "runtime/weather.db"

STRATEGY_KEY = "current_yes_heat_death_physical"
RETIRED_INSTANCE_ID = "current_yes_heat_death_tiny_live_v1"
PREREG_DOC = "docs/analysis/2026-07/2026-07-15-heat-death-live-promotion-preregistration-v1.md"

HEADS = {
    "h1_late_carry": {
        "instance_id": "current_yes_heat_death_tiny_live_h1_late_carry_v1",
        "config_id": "current_yes_heat_death_tiny_live_h1_late_carry_v1_fixed10",
        "display_name": "current YES heat-death H1 late-carry 10-share probe",
        "config_name": "heat-death H1 late-carry fixed 10 shares, ask 0.95-0.99",
        "min_ask": 0.95,
        "max_ask": 0.99,
    },
    "h2_early_dislocation": {
        "instance_id": "current_yes_heat_death_tiny_live_h2_early_dislocation_v1",
        "config_id": "current_yes_heat_death_tiny_live_h2_early_dislocation_v1_fixed10",
        "display_name": "current YES heat-death H2 early-dislocation 10-share probe",
        "config_name": "heat-death H2 early dislocation fixed 10 shares, ask 0.50-0.93",
        "min_ask": 0.50,
        "max_ask": 0.93,
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=3000")
    statements: list[tuple[str, tuple]] = [
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
                "BUY current YES after heat-death physical confirmation, split into H1 late-carry and H2 early-dislocation entry regimes.",
                1,
                "current YES heat-death dual-head",
                "scripts/ops/weather_current_yes_heat_death_tiny_live_v1.py",
                json.dumps(
                    {
                        "research_verdict": "inconclusive_forward_probe_dual_head",
                        "preregistration_doc": PREREG_DOC,
                        "source_doc": "docs/analysis/2026-07/2026-07-14-current-yes-heat-death-physical-backtest-v1.md",
                    }
                ),
                "instance_family",
                "tiny_live_forward_probe",
                "10-share dual-head probe only; not confirmed or approved for size-up",
            ),
        ),
    ]
    for head, spec in HEADS.items():
        statements.append(
            (
                """INSERT INTO strategy_config (config_id, name, params, strategy_key)
                   VALUES (?,?,?,?)
                   ON CONFLICT(config_id) DO UPDATE SET
                     name=excluded.name, params=excluded.params, strategy_key=excluded.strategy_key""",
                (
                    spec["config_id"],
                    spec["config_name"],
                    json.dumps(
                        {
                            "entry_regime_head": head,
                            "expression": "current_bracket BUY_YES",
                            "eligibility": "physical_confirmation_strong",
                            "sizing": "fixed 10 shares",
                            "dedupe": "one submitted order per city-target_date per head",
                            "max_orders_per_utc_day": 3,
                            "min_ask": spec["min_ask"],
                            "max_ask": spec["max_ask"],
                            "buffer_band_excluded": [0.93, 0.95],
                            "min_top_ask_shares": 10,
                            "max_snapshot_age_min": 20,
                            "order_ttl_min": 15,
                            "execution": "fresh-book taker-capable GTC",
                        }
                    ),
                    STRATEGY_KEY,
                ),
            )
        )
        statements.append(
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
                    spec["instance_id"],
                    STRATEGY_KEY,
                    spec["display_name"],
                    "reheat_risk.current_yes",
                    "tiny_live_probe",
                    "tiny_live_taker_probe",
                    "enabled",
                    "runtime_local",
                    f"runtime/weather_edge_v1/{spec['instance_id']}",
                    "scripts/ops/start_weather_current_yes_heat_death_tiny_live_v1.sh",
                    f"weather_{spec['instance_id']}",
                    1,
                    f"explicit user-approved 10-share forward probe head {head}; promotion gates frozen in {PREREG_DOC}",
                    spec["config_id"],
                ),
            )
        )
    statements.append(
        (
            """UPDATE strategy_instance
               SET desired_status='retired', expected_live=0,
                   notes='retired 2026-07-15: split into h1_late_carry/h2_early_dislocation heads; zero orders were submitted under this instance',
                   updated_at_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE instance_id=?""",
            (RETIRED_INSTANCE_ID,),
        )
    )
    try:
        for sql, params in statements:
            print(("[dry] " if args.dry_run else "[run] ") + sql.strip().splitlines()[0])
            if not args.dry_run:
                conn.execute(sql, params)
        if not args.dry_run:
            conn.commit()
        checks = [("strategy_def", "strategy_key", STRATEGY_KEY)]
        for spec in HEADS.values():
            checks.append(("strategy_config", "config_id", spec["config_id"]))
            checks.append(("strategy_instance", "instance_id", spec["instance_id"]))
        for table, column, key in checks:
            count = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (key,)).fetchone()[0]
            print(f"{table}: {count} row for {key}")
        status = conn.execute(
            "SELECT desired_status FROM strategy_instance WHERE instance_id=?", (RETIRED_INSTANCE_ID,)
        ).fetchone()
        print(f"{RETIRED_INSTANCE_ID}: desired_status={status[0] if status else 'missing'}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
