#!/usr/bin/env python3
"""Print one canonical weather production path from production.yaml."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec


def paths() -> dict[str, str]:
    spec = load_production_spec()
    return {
        "data_feed_runtime_root": str(spec.data_feed_runtime_root),
        "pm_runtime_root": str(spec.pm_runtime_root),
        "feature_store_root": str(spec.pm_runtime_root / "weather_feature_store"),
        "archive_storage_root": str(spec.archive_storage_root),
        "research_artifact_root": str(spec.research_artifact_root),
        "market_books_root": str(spec.resolved_market_books_root()),
        "market_books_latest": str(spec.resolved_market_books_root() / "latest.json"),
        "strategy_snapshot_root": str(spec.resolved_strategy_snapshot_root()),
        "strategy_paper_snapshot_dir": str(spec.strategy_paper_snapshot_dir()),
        "market_ladder_snapshot_root": str(spec.resolved_market_ladder_snapshot_root()),
        "forecast_output_root": str(spec.resolved_forecast_output_root()),
        "forecast_hourly_curve_dir": str(spec.forecast_hourly_curve_dir()),
        "observation_cache_path": str(spec.observation_cache_path()),
        "market_proxy_state_path": str(spec.market_proxy_state_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", choices=sorted(paths()))
    args = parser.parse_args()
    print(paths()[args.name])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
