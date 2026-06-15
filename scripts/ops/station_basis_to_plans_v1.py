#!/usr/bin/env python3
"""v1 maker-plan bridge: read v1 shadow entries -> executor plans.

Thin wrapper over station_basis_to_plans (same pattern as exec_v1 wraps exec).
v0 bridge hardcodes the v0 shadow dir; v1 entries live in the v1 ledger under
the data project root. This wrapper repoints IO at v1 and narrows the pilot to
the two cleanest, market-anchored cities (Paris, London).

Still maker-only, still pilot caps ($1/trade). live_enabled stays false unless
--arm is passed; the actual fire happens via weather_order_executor.py.
"""

from __future__ import annotations

import os
import sys

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
OPS = ROOT / "scripts/ops"
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

import station_basis_to_plans as base  # noqa: E402

DATA_ROOT = __import__("pathlib").Path(
    os.environ.get("STATION_BASIS_DATA_ROOT")
    or os.environ.get("DATA_PROJECT_DIR")
    or ROOT
)

# Guard check() resolves the kill switch relative to base.ROOT; point it at the
# data root so the v1 PAUSE file is found in the same place exec_v1 uses.
base.ROOT = DATA_ROOT
base.SHADOW_DIR = DATA_ROOT / "runtime/weather_edge_v1/station_basis_shadow_v1"
base.EXEC_DIR = DATA_ROOT / "runtime/weather_edge_v1/station_basis_exec_v1"
base.ENTRIES = base.SHADOW_DIR / "entries.jsonl"
base.PLAN_OUT = base.EXEC_DIR / "station_basis_plans.jsonl"
# v1 pilot: the two cities whose market most strongly anchors the official
# station (cleanest forward edge in the executability reconcile).
base.PILOT_CITIES = {"Paris", "London"}
# v1 kill switch lives next to the v1 shadow ledger.
base.PILOT_RISK = base.RiskConfig(
    per_trade_max_notional_usd=base.PILOT_RISK.per_trade_max_notional_usd,
    per_city_daily_max_notional_usd=base.PILOT_RISK.per_city_daily_max_notional_usd,
    daily_max_deployed_usd=base.PILOT_RISK.daily_max_deployed_usd,
    daily_loss_stop_usd=base.PILOT_RISK.daily_loss_stop_usd,
    max_open_positions=base.PILOT_RISK.max_open_positions,
    max_open_positions_per_city=base.PILOT_RISK.max_open_positions_per_city,
    kill_switch_path="runtime/weather_edge_v1/station_basis_shadow_v1/PAUSE",
)


if __name__ == "__main__":
    sys.exit(base.main())
