#!/usr/bin/env python3
"""Authoritative Weather City Intraday Runtime (WCIR)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_city_probability_shadow import (
    BusanOnlineMarketPriorAdapter,
    ObservationCoverageAdapter,
    ShadowRuntime,
)
from src.strategies.weather_city_probability_shadow.amsterdam import (
    AmsterdamKnmiRemainingHeatV7Adapter,
)
from src.strategies.weather_city_probability_shadow.helsinki import HelsinkiRemainingHeatAdapter
from src.strategies.weather_city_probability_shadow.tokyo import TokyoMarketAnchorAdapter
from weather_city_runtime import DecisionContractJournalSink


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("once", "loop"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    decision_sink = DecisionContractJournalSink.from_config(config)
    runtime = ShadowRuntime(
        config,
        {
            "amsterdam_knmi_remaining_heat_v7": AmsterdamKnmiRemainingHeatV7Adapter(),
            "amsterdam_knmi_remaining_heat_v9": AmsterdamKnmiRemainingHeatV7Adapter(),
            "amsterdam_knmi_cross_survival_v1": AmsterdamKnmiRemainingHeatV7Adapter(),
            "amsterdam_knmi_market_offset_probability_v2": AmsterdamKnmiRemainingHeatV7Adapter(),
            "busan_online_market_prior_v1": BusanOnlineMarketPriorAdapter(),
            "helsinki_remaining_heat_v1": HelsinkiRemainingHeatAdapter(),
            "tokyo_market_anchor_v7": TokyoMarketAnchorAdapter(),
            "tokyo_overshoot_market_residual_v2": TokyoMarketAnchorAdapter(),
            "observation_coverage_v1": ObservationCoverageAdapter(),
        },
        config_path=config_path,
        entrypoint_path=Path(__file__),
        decision_sink=decision_sink,
    )
    while True:
        print(json.dumps(runtime.run_once(), sort_keys=True), flush=True)
        if args.mode == "once":
            break
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
