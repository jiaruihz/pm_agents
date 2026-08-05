#!/usr/bin/env python3
"""Replay an immutable exact-run capture and verify live/replay parity."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.forecast_run_contract import stable_content_hash  # noqa: E402
from weather_data_feed.historical_forecast_runs import fetch_single_run_batch  # noqa: E402
from weather_data_feed_service.forecast_run_capture import materialize_capture  # noqa: E402


def _jsonl(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture_dir", type=Path)
    args = parser.parse_args(argv)
    latest = json.loads((args.capture_dir / "latest.json").read_text(encoding="utf-8"))
    city_inputs = list(latest["city_inputs"])
    run = str(latest["requested_run"])
    models = list(latest["models_requested"])
    raw_dir = args.capture_dir / "raw" / str(latest["capture_id"])
    responses_by_model = {}
    metadata_by_model = {}
    for model in models:
        responses, metadata = fetch_single_run_batch(
            city_inputs,
            run=run,
            models=(str(model),),
            forecast_days=4,
            cache_dir=raw_dir,
            max_attempts=1,
        )
        if not metadata.get("cache_hit"):
            raise RuntimeError(f"parity replay unexpectedly fetched network for {model}")
        original_metadata = dict(latest["model_request_metadata"][str(model)])
        if metadata.get("raw_hash") != original_metadata.get("raw_hash"):
            raise RuntimeError(f"raw payload hash drift for {model}")
        if metadata.get("request_key") != original_metadata.get("request_key"):
            raise RuntimeError(f"request hash drift for {model}")
        responses_by_model[str(model)] = responses
        # Replay uses the original fetch/availability clocks from the capture
        # manifest, not the later immutable-cache read clock.
        metadata_by_model[str(model)] = original_metadata
    replay_rows, replay_batches, _ = materialize_capture(
        run=run,
        captured_at_utc=datetime.fromisoformat(str(latest["captured_at_utc"])),
        city_inputs=city_inputs,
        responses_by_model=responses_by_model,
        metadata_by_model=metadata_by_model,
        expected_models=[str(model) for model in models],
    )
    live_rows = _jsonl(args.capture_dir / "forecast_run_rows.jsonl")
    live_batches = _jsonl(args.capture_dir / "forecast_batches.jsonl")
    live_rows_hash = stable_content_hash(live_rows)
    replay_rows_hash = stable_content_hash(replay_rows)
    live_batches_hash = stable_content_hash(live_batches)
    replay_batches_hash = stable_content_hash(replay_batches)
    result = {
        "schema_version": "forecast_run_capture_parity_v1",
        "capture_id": latest["capture_id"],
        "live_rows_hash": live_rows_hash,
        "replay_rows_hash": replay_rows_hash,
        "live_batches_hash": live_batches_hash,
        "replay_batches_hash": replay_batches_hash,
        "rows_equal": live_rows_hash == replay_rows_hash,
        "batches_equal": live_batches_hash == replay_batches_hash,
        "parity_pass": live_rows_hash == replay_rows_hash and live_batches_hash == replay_batches_hash,
        "network_fetches": 0,
    }
    (args.capture_dir / "parity.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["parity_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
