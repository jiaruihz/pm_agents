#!/usr/bin/env python3
"""Dry-run executor wrapper for station-basis v1.

v1 has its own shadow ledger and must not share the v0 executor cursor or
orders ledger. This wrapper reuses the same RiskGuard/executor code while
redirecting all IO to station_basis_shadow_v1 and station_basis_exec_v1.

Default mode remains dry_run; live order placement is still hard-gated in the
base executor.
"""

from __future__ import annotations

import sys

import weather_station_basis_exec as base


base.SHADOW = base.DATA_ROOT / "runtime/weather_edge_v1/station_basis_shadow_v1"
base.EXEC_DIR = base.DATA_ROOT / "runtime/weather_edge_v1/station_basis_exec_v1"
base.ENTRIES = base.SHADOW / "entries.jsonl"
base.SETTLEMENTS = base.SHADOW / "settlements.jsonl"
base.ORDERS = base.EXEC_DIR / "orders.jsonl"
base.CURSOR = base.EXEC_DIR / "cursor.json"
base.RISK_CONFIG_PATH = base.EXEC_DIR / "risk_config.json"

V1_KILL_SWITCH_PATH = "runtime/weather_edge_v1/station_basis_shadow_v1/PAUSE"


def ensure_v1_risk_config() -> None:
    """Materialize or repair the v1 risk config before the base executor loads it."""
    base.EXEC_DIR.mkdir(parents=True, exist_ok=True)
    cfg = base.RiskConfig.load(base.RISK_CONFIG_PATH)
    if cfg.kill_switch_path != V1_KILL_SWITCH_PATH:
        cfg = base.RiskConfig(
            per_trade_max_notional_usd=cfg.per_trade_max_notional_usd,
            per_city_daily_max_notional_usd=cfg.per_city_daily_max_notional_usd,
            daily_max_deployed_usd=cfg.daily_max_deployed_usd,
            daily_loss_stop_usd=cfg.daily_loss_stop_usd,
            max_open_positions=cfg.max_open_positions,
            max_open_positions_per_city=cfg.max_open_positions_per_city,
            min_ask=cfg.min_ask,
            max_ask=cfg.max_ask,
            kill_switch_path=V1_KILL_SWITCH_PATH,
        )
        cfg.dump(base.RISK_CONFIG_PATH)


if __name__ == "__main__":
    ensure_v1_risk_config()
    sys.exit(base.main())
