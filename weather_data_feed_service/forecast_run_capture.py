"""Exact-run forecast collector for strict D-1/D-2 research lineage.

The collector requests one explicitly named provider run per model.  It never
falls back to another run.  An unavailable model/run is append-only blocker
evidence and can be retried by a later bounded invocation of the same run.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from weather_data_feed import load_city_configs
from weather_data_feed.forecast_run_contract import (
    EXACT_SINGLE_RUN_ENDPOINT,
    build_forecast_row,
    run_lineage_evidence,
    stable_content_hash,
    summarize_forecast_batch,
)
from weather_data_feed.historical_forecast_runs import (
    DEFAULT_GLOBAL_SINGLE_RUN_MODELS,
    ModelRunUnavailable,
    fetch_single_run_batch,
)
from weather_data_feed_service.cli import DEFAULT_RUNTIME_ROOT
from weather_data_feed_service.forecast_enrichment import city_coordinates
from weather_data_feed_service.io_utils import append_jsonl, read_json, write_json


DEFAULT_OUTPUT_DIR = DEFAULT_RUNTIME_ROOT / "output" / "forecast_run_capture"


def latest_cycle_candidate(now_utc: datetime, *, cycle_hours: int = 6) -> str:
    if cycle_hours <= 0 or 24 % cycle_hours:
        raise ValueError("cycle_hours must divide 24")
    floored = now_utc.astimezone(timezone.utc).replace(
        hour=now_utc.hour - now_utc.hour % cycle_hours,
        minute=0,
        second=0,
        microsecond=0,
    )
    return floored.strftime("%Y-%m-%dT%H:00")


def _daily_max(response: dict[str, Any], target_date: str) -> float:
    hourly = response.get("hourly") or {}
    values = [
        float(value)
        for timestamp, value in zip(hourly.get("time") or [], hourly.get("temperature_2m") or [])
        if str(timestamp).startswith(target_date) and value is not None
    ]
    if not values:
        raise ValueError(f"no temperature_2m for target_date={target_date}")
    return max(values)


def materialize_capture(
    *,
    run: str,
    captured_at_utc: datetime,
    city_inputs: list[dict[str, Any]],
    responses_by_model: dict[str, list[dict[str, Any]]],
    metadata_by_model: dict[str, dict[str, Any]],
    expected_models: list[str],
    previous_state: dict[str, Any] | None = None,
    producer_build_identity: str = "weather_data_feed_service.forecast_run_capture:v1",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    state = dict(previous_state or {})
    latest = dict(state.get("latest_by_model_city_target") or {})
    first_seen_by_content = dict(state.get("first_seen_by_content") or {})
    captured_text = captured_at_utc.astimezone(timezone.utc).isoformat()
    contract_rows: list[dict[str, Any]] = []
    for model in expected_models:
        responses = responses_by_model.get(model) or []
        metadata = metadata_by_model.get(model) or {}
        if len(responses) != len(city_inputs):
            continue
        evidence = run_lineage_evidence(
            request_run_at_utc=f"{run}Z",
            request_endpoint=EXACT_SINGLE_RUN_ENDPOINT,
            request_succeeded=True,
            fallback_applied=False,
            raw_payload_hash=str(metadata.get("raw_hash") or ""),
            request_hash=str(metadata.get("request_key") or ""),
        )
        available_text = str(metadata.get("source_fetch_end_utc") or captured_text)
        for city_input, response in zip(city_inputs, responses):
            local_today = captured_at_utc.astimezone(ZoneInfo(str(city_input["timezone_name"]))).date()
            for horizon in (1, 2):
                target_date = (local_today + timedelta(days=horizon)).isoformat()
                try:
                    forecast_max_f = _daily_max(response, target_date)
                except ValueError:
                    continue
                sequence_key = "|".join((model, str(city_input["city"]), target_date))
                prior = dict(latest.get(sequence_key) or {})
                raw_hash = str(metadata.get("raw_hash") or "")
                content_identity = stable_content_hash(
                    {
                        "model": model,
                        "city": city_input["city"],
                        "target_date": target_date,
                        "run": evidence["forecast_run_at_utc"],
                        "forecast_max_f": forecast_max_f,
                        "raw_payload_hash": raw_hash,
                    }
                )
                first_seen = str(first_seen_by_content.setdefault(content_identity, available_text))
                batch_capture_id = stable_content_hash(
                    {
                        "run": run,
                        "captured_at_utc": captured_text,
                        "city": city_input["city"],
                        "target_date": target_date,
                    }
                )
                capture_id = stable_content_hash(
                    {"batch_capture_id": batch_capture_id, "model": model}
                )
                same_prior_run = prior.get("forecast_run_at_utc") == evidence["forecast_run_at_utc"]
                row = build_forecast_row(
                    model_key=model,
                    city=str(city_input["city"]),
                    target_date=target_date,
                    forecast_max_f=forecast_max_f,
                    source_fetched_at_utc=available_text,
                    detected_at_utc=available_text,
                    first_seen_at_utc=first_seen,
                    available_at_utc=available_text,
                    raw_payload_hash=raw_hash,
                    producer_build_identity=producer_build_identity,
                    capture_id=capture_id,
                    batch_capture_id=batch_capture_id,
                    horizon_days_local=horizon,
                    forecast_run_at_utc=evidence["forecast_run_at_utc"],
                    forecast_run_evidence=evidence["forecast_run_evidence"],
                    forecast_run_lineage_status=evidence["forecast_run_lineage_status"],
                    lineage_blocker=evidence["blocker"],
                    previous_run_ts=(
                        str(prior.get("forecast_run_at_utc"))
                        if prior and not same_prior_run
                        else None
                    ),
                    previous_run_forecast_max_f=(
                        float(prior["forecast_max_f"])
                        if prior and not same_prior_run and prior.get("forecast_max_f") is not None
                        else None
                    ),
                    previous_content_hash=(
                        str(prior.get("content_hash"))
                        if prior and same_prior_run
                        else None
                    ),
                    previous_content_forecast_max_f=(
                        float(prior["forecast_max_f"])
                        if prior and same_prior_run and prior.get("forecast_max_f") is not None
                        else None
                    ),
                    revision_of_content_id=(
                        str(prior.get("capture_id")) if prior and same_prior_run else None
                    ),
                )
                contract_rows.append(row)
                latest[sequence_key] = {
                    "forecast_run_at_utc": row["forecast_run_at_utc"],
                    "forecast_max_f": row["forecast_max_f"],
                    "content_hash": row["content_hash"],
                    "capture_id": row["capture_id"],
                }
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in contract_rows:
        grouped.setdefault(
            (str(row["batch_capture_id"]), str(row["city"]), str(row["target_date"])),
            [],
        ).append(row)
    batches = [
        summarize_forecast_batch(group, expected_model_keys=expected_models)
        for group in grouped.values()
    ]
    return contract_rows, batches, {
        "latest_by_model_city_target": latest,
        "first_seen_by_content": first_seen_by_content,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--run", help="Exact UTC run wire value, e.g. 2026-08-04T12:00")
    parser.add_argument("--candidate-latest-cycle", action="store_true")
    parser.add_argument("--now-utc")
    parser.add_argument("--cities", nargs="*")
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_GLOBAL_SINGLE_RUN_MODELS))
    parser.add_argument("--forecast-days", type=int, default=4)
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.run) == bool(args.candidate_latest_cycle):
        raise SystemExit("specify exactly one of --run or --candidate-latest-cycle")
    now_utc = (
        datetime.fromisoformat(args.now_utc.replace("Z", "+00:00")).astimezone(timezone.utc)
        if args.now_utc
        else datetime.now(timezone.utc)
    )
    run = args.run or latest_cycle_candidate(now_utc)
    configs = load_city_configs(
        include_station_diff=False,
        only_cities=set(args.cities or []) or None,
    )
    city_inputs: list[dict[str, Any]] = []
    for cfg in configs:
        coords = city_coordinates(cfg.city)
        if coords.get("lat") is None or coords.get("lon") is None:
            continue
        city_inputs.append(
            {
                "city": cfg.city,
                "latitude": float(coords["lat"]),
                "longitude": float(coords["lon"]),
                "timezone_name": cfg.timezone_name,
            }
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    capture_id = stable_content_hash({"run": run, "captured_at_utc": now_utc.isoformat()})
    responses_by_model: dict[str, list[dict[str, Any]]] = {}
    metadata_by_model: dict[str, dict[str, Any]] = {}
    blockers: list[dict[str, Any]] = []
    for model in args.models:
        try:
            responses, metadata = fetch_single_run_batch(
                city_inputs,
                run=run,
                models=(model,),
                forecast_days=args.forecast_days,
                cache_dir=args.output_dir / "raw" / capture_id,
                timeout_sec=args.timeout_sec,
                max_attempts=1,
            )
            responses_by_model[model] = responses
            metadata_by_model[model] = metadata
        except (ModelRunUnavailable, RuntimeError, ValueError) as exc:
            blockers.append(
                {
                    "schema_version": "weather_forecast_run_blocker_v1",
                    "captured_at_utc": now_utc.isoformat(),
                    "requested_run": run,
                    "model_key": model,
                    "code": "exact_run_unavailable_or_fetch_failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                    "fallback_attempted": False,
                }
            )
    state_path = args.output_dir / "state.json"
    rows, batches, state = materialize_capture(
        run=run,
        captured_at_utc=now_utc,
        city_inputs=city_inputs,
        responses_by_model=responses_by_model,
        metadata_by_model=metadata_by_model,
        expected_models=list(args.models),
        previous_state=read_json(state_path, {}),
    )
    append_jsonl(args.output_dir / "forecast_run_rows.jsonl", rows)
    append_jsonl(args.output_dir / "forecast_batches.jsonl", batches)
    append_jsonl(args.output_dir / "blockers.jsonl", blockers)
    write_json(state_path, state)
    summary = {
        "schema_version": "weather_forecast_run_capture_summary_v1",
        "status": "ok",
        "capture_status": (
            "complete"
            if len(responses_by_model) == len(args.models)
            else ("partial" if responses_by_model else "blocked")
        ),
        "capture_id": capture_id,
        "captured_at_utc": now_utc.isoformat(),
        "requested_run": run,
        "candidate_only_until_exact_request_success": True,
        "fallback_attempted": False,
        "cities": len(city_inputs),
        "models_requested": list(args.models),
        "models_identified": sorted(responses_by_model),
        "city_inputs": city_inputs,
        "model_request_metadata": metadata_by_model,
        "forecast_rows": len(rows),
        "forecast_batches": len(batches),
        "blockers": blockers,
    }
    write_json(args.output_dir / "latest.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
