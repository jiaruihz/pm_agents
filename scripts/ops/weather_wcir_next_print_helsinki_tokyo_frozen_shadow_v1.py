#!/usr/bin/env python3
"""Run Helsinki/Tokyo WCIR next-print zero-notional frozen forward."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_city_runtime.helsinki_tokyo_next_print_frozen_v1 import (  # noqa: E402
    ACTION_SHADOW_EPOCH,
    ACTION_SHADOW_START_UTC,
    HelsinkiTokyoNextPrintFrozenRuntime,
)


DEFAULT_REVIEW = ROOT / "reviews/wcir_next_official_print_helsinki_tokyo_frozen_v1"
DEFAULT_ROOT = Path(
    "/Volumes/jrs/pm_agents/runtime/research/"
    "wcir_next_print_helsinki_tokyo_frozen_v1"
)
DEFAULT_EVENTS = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/"
    "fast_source_prev_no_trial/events.jsonl"
)
DEFAULT_PRODUCER_HEALTH = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/"
    "fast_source_prev_no_trial/latest_summary.json"
)
DEFAULT_FAST_PATH = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/live_cross_observations"
)
DEFAULT_OFFICIAL = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/source_events"
)
DEFAULT_SETTLEMENT_DB = Path("/Volumes/jrs/pm_agents/runtime/weather.db")
DEFAULT_WS_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_MARKET_LATEST = DEFAULT_WS_RUNTIME / "market_books" / "latest.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop-seconds", type=int, default=0)
    parser.add_argument("--shadow-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--review-root", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument(
        "--producer-health", type=Path, default=DEFAULT_PRODUCER_HEALTH
    )
    parser.add_argument("--fast-path-root", type=Path, default=DEFAULT_FAST_PATH)
    parser.add_argument("--official-path-root", type=Path, default=DEFAULT_OFFICIAL)
    parser.add_argument("--settlement-db", type=Path, default=DEFAULT_SETTLEMENT_DB)
    parser.add_argument("--ws-runtime-root", type=Path, default=DEFAULT_WS_RUNTIME)
    parser.add_argument("--market-latest", type=Path, default=DEFAULT_MARKET_LATEST)
    parser.add_argument("--forward-epoch-id", default=ACTION_SHADOW_EPOCH)
    parser.add_argument("--forward-start-utc", default=ACTION_SHADOW_START_UTC)
    args = parser.parse_args()
    if not args.once and args.loop_seconds <= 0:
        parser.error("supply --once or a positive --loop-seconds")
    epoch = str(args.forward_epoch_id)
    runtime = HelsinkiTokyoNextPrintFrozenRuntime(
        args.shadow_root / f"epoch={epoch}",
        review_root=args.review_root,
        event_path=args.events,
        producer_health_path=args.producer_health,
        source_path_root=args.fast_path_root,
        official_path_root=args.official_path_root,
        settlement_db=args.settlement_db,
        ws_runtime_root=args.ws_runtime_root,
        market_latest_path=args.market_latest,
        forward_epoch_id=epoch,
        forward_start_utc=args.forward_start_utc,
    )
    while True:
        result = runtime.run_once()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        if args.once:
            return 0
        time.sleep(args.loop_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
