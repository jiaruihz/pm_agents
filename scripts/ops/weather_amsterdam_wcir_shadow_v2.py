#!/usr/bin/env python3
"""Run the Amsterdam frozen scorer and materialize zero-notional shadow evidence."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis.forecast_quality.wcir_amsterdam_pilot_v1_1 import (
    DEFAULT_DECISIONS, DEFAULT_KNMI_ROOT, DEFAULT_OBSERVATIONS_ROOT,
    score_shadow_once,
)
from weather_city_runtime.amsterdam_frozen_shadow_v2 import AmsterdamFrozenShadowRuntime, read_jsonl

DEFAULT_REVIEW = ROOT / "reviews/wcir_unified_data_amsterdam_pilot_v1_1"
DEFAULT_ROOT = Path("/Volumes/jrs/pm_agents/runtime/research/wcir_amsterdam_frozen_v2")
DEFAULT_BOOTSTRAP = Path("/Volumes/jrs-archive/pm_agents/research/artifact_store/wcir_amsterdam_score_only_shadow/epoch=wcir_amsterdam_score_only_v2_bb04a8acbeafd66b/predictions.jsonl")
DEFAULT_SOURCE_EVENTS = Path("/Volumes/jrs/weather_data_feed_service_runtime/output/source_events")
DEFAULT_SETTLEMENT_DB = Path("/Volumes/jrs/pm_agents/runtime/weather.db")
DEFAULT_MARKET_LATEST = Path("/Volumes/jrs/weather_data_feed_service_runtime/market_books/latest.json")
DEFAULT_WS_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop-seconds", type=int, default=0)
    parser.add_argument("--shadow-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--review-output", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--knmi-root", type=Path, default=DEFAULT_KNMI_ROOT)
    parser.add_argument("--observations-root", type=Path, default=DEFAULT_OBSERVATIONS_ROOT)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--source-events", type=Path, default=DEFAULT_SOURCE_EVENTS)
    parser.add_argument("--settlement-db", type=Path, default=DEFAULT_SETTLEMENT_DB)
    parser.add_argument("--market-latest", type=Path, default=DEFAULT_MARKET_LATEST)
    parser.add_argument("--ws-runtime-root", type=Path, default=DEFAULT_WS_RUNTIME)
    parser.add_argument("--bootstrap-predictions", type=Path, default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--max-events", type=int, default=256)
    args = parser.parse_args()
    if not args.once and args.loop_seconds <= 0:
        parser.error("supply --once or a positive --loop-seconds")
    config = json.loads((args.review_output / "FROZEN_SCORE_ONLY_SHADOW_CONFIG_V2.json").read_text(encoding="utf-8"))
    epoch_manifest = json.loads((args.review_output / "FORWARD_EPOCH_MANIFEST_V2.json").read_text(encoding="utf-8"))
    epoch = str(config["forward_epoch_id"])
    if epoch != str(epoch_manifest.get("forward_epoch_id") or ""):
        raise RuntimeError("frozen config/epoch mismatch")
    root = args.shadow_root / f"epoch={epoch}"
    runtime = AmsterdamFrozenShadowRuntime(
        root,
        source_events_path=args.source_events,
        settlement_db=args.settlement_db,
        market_latest_path=args.market_latest,
        ws_runtime_root=args.ws_runtime_root,
    )
    if args.bootstrap_predictions.is_file():
        runtime.ingest_predictions(read_jsonl(args.bootstrap_predictions))
    while True:
        score = score_shadow_once(output=args.review_output, shadow_root=args.shadow_root, knmi_root=args.knmi_root,
                                  observations_root=args.observations_root, decisions=args.decisions, max_events=args.max_events)
        if str(score.get("forward_epoch_id") or "") != epoch:
            raise RuntimeError("scorer returned unexpected frozen epoch")
        # score_shadow_once appends directly to the epoch journal. Refresh the
        # materializer's identity index instead of re-ingesting the same rows.
        runtime.refresh_predictions()
        result = runtime.materialize()
        health = runtime.write_health(epoch=epoch, frozen_hashes={
            "feature_builder_hash": epoch_manifest.get("feature_builder_hash"),
            "frozen_scorer_hash": epoch_manifest.get("frozen_scorer_hash"),
            "model_artifacts": epoch_manifest.get("model_artifacts"),
            "source_label_contract_hash": epoch_manifest.get("source_label_contract_hash"),
            "opportunity_generator_hash": epoch_manifest.get("opportunity_generator_hash"),
            "market_identity_resolver_hash": epoch_manifest.get("market_identity_resolver_hash"),
            "prediction_journal_sha256": score.get("prediction_journal_sha256"),
        })
        print(json.dumps({"score": score, "materialized": result, "health": health}, ensure_ascii=False, sort_keys=True))
        if args.once:
            return 0
        time.sleep(args.loop_seconds)

if __name__ == "__main__":
    raise SystemExit(main())
