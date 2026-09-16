#!/usr/bin/env python3
"""Zero-notional pre-live wrapper for the frozen no-obs-age carry v2."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import weather_current_yes_core_carry_pre_live_v1 as runner  # noqa: E402


runner.STRATEGY_INSTANCE = "current_yes_core_carry_pre_live_v2"
runner.OUTPUT_DIR = ROOT / "runtime/weather_edge_v1" / runner.STRATEGY_INSTANCE
runner.ARTIFACT_PATH = (
    ROOT / "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v2.json"
)


if __name__ == "__main__":
    raise SystemExit(runner.main())
