#!/usr/bin/env python3
"""One-shot, non-production probe for auditable D-1/D-2 provider runs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)
from weather_data_feed.forecast_run_contract import (  # noqa: E402
    EXACT_SINGLE_RUN_ENDPOINT,
    build_forecast_row,
    run_lineage_evidence,
    stable_content_hash,
    summarize_forecast_batch,
)
from weather_data_feed.historical_forecast_runs import (  # noqa: E402
    DEFAULT_GLOBAL_SINGLE_RUN_MODELS,
    ModelRunUnavailable,
    conservative_available_run,
    fetch_single_run_batch,
)


ARTIFACT_FAMILY = "d1_d2_forecast_run_probe_v1"


def _daily_max(response: dict[str, Any], target_date: str) -> float:
    hourly = response.get("hourly") or {}
    times = hourly.get("time") or []
    values = hourly.get("temperature_2m") or []
    matches = [
        float(value)
        for timestamp, value in zip(times, values)
        if str(timestamp).startswith(target_date) and value is not None
    ]
    if not matches:
        raise ValueError(f"no temperature_2m values for {target_date}")
    return max(matches)


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc)
    decision_at = args.decision_at_utc or observed_at.isoformat()
    requested_run = args.run or conservative_available_run(
        decision_at,
        availability_lag_hours=args.availability_lag_hours,
    )
    capture_id = stable_content_hash(
        {
            "purpose": "d1_d2_forecast_run_probe_v1",
            "observed_at_utc": observed_at.isoformat(),
            "requested_run": requested_run,
            "city": args.city,
        }
    )
    location = {
        "city": args.city,
        "latitude": args.latitude,
        "longitude": args.longitude,
    }
    rows: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    provider_samples: list[dict[str, Any]] = []
    for model in args.models:
        fetched_started = datetime.now(timezone.utc)
        try:
            responses, metadata = fetch_single_run_batch(
                [location],
                run=requested_run,
                models=(model,),
                forecast_days=args.forecast_days,
                cache_dir=args.output_dir / "raw",
                timeout_sec=args.timeout_sec,
                max_attempts=1,
            )
            fetched_ended = datetime.now(timezone.utc)
            request_hash = str(metadata.get("request_key") or "")
            raw_hash = str(metadata.get("raw_hash") or "")
            evidence = run_lineage_evidence(
                # The API's ``run`` parameter is UTC but its accepted wire
                # format omits the suffix.  Add it only in the normalized
                # contract field; preserve the exact request above.
                request_run_at_utc=f"{requested_run}Z",
                request_endpoint=EXACT_SINGLE_RUN_ENDPOINT,
                request_succeeded=True,
                fallback_applied=False,
                raw_payload_hash=raw_hash,
                request_hash=request_hash,
            )
            response = responses[0]
            model_rows: list[dict[str, Any]] = []
            response_dates = sorted(
                {
                    str(value)[:10]
                    for value in ((response.get("hourly") or {}).get("time") or [])
                    if value
                }
            )
            for target_date in response_dates:
                horizon = (date.fromisoformat(target_date) - fetched_ended.date()).days
                if horizon not in (1, 2):
                    continue
                batch_capture_id = stable_content_hash(
                    {
                        "capture_id": capture_id,
                        "city": args.city,
                        "target_date": target_date,
                    }
                )
                row = build_forecast_row(
                    model_key=model,
                    city=args.city,
                    target_date=target_date,
                    forecast_max_f=_daily_max(response, target_date),
                    source_fetched_at_utc=fetched_ended.isoformat(),
                    detected_at_utc=fetched_ended.isoformat(),
                    first_seen_at_utc=fetched_ended.isoformat(),
                    available_at_utc=fetched_ended.isoformat(),
                    raw_payload_hash=raw_hash,
                    producer_build_identity="probe_d1_d2_forecast_run_contract_v1",
                    capture_id=stable_content_hash(
                        {"capture_id": capture_id, "model": model, "target_date": target_date}
                    ),
                    batch_capture_id=batch_capture_id,
                    horizon_days_local=horizon,
                    forecast_run_at_utc=evidence["forecast_run_at_utc"],
                    forecast_run_evidence=evidence["forecast_run_evidence"],
                    forecast_run_lineage_status=evidence["forecast_run_lineage_status"],
                    lineage_blocker=evidence["blocker"],
                )
                model_rows.append(row)
                rows.append(row)
            provider_samples.append(
                {
                    "model_key": model,
                    "status": "identified",
                    "requested_run": requested_run,
                    "request_hash": request_hash,
                    "raw_payload_hash": raw_hash,
                    "cache_hit": bool(metadata.get("cache_hit")),
                    "source_fetch_started_at_utc": fetched_started.isoformat(),
                    "source_fetch_ended_at_utc": fetched_ended.isoformat(),
                    "d1_d2_rows": len(model_rows),
                }
            )
        except (ModelRunUnavailable, RuntimeError, ValueError) as exc:
            fetched_ended = datetime.now(timezone.utc)
            blocker = {
                "model_key": model,
                "status": "blocked",
                "code": "exact_single_run_probe_failed",
                "requested_run": requested_run,
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
                "source_fetch_started_at_utc": fetched_started.isoformat(),
                "source_fetch_ended_at_utc": fetched_ended.isoformat(),
            }
            blockers.append(blocker)
            provider_samples.append(blocker)

    batches: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(
            (str(row["batch_capture_id"]), str(row["city"]), str(row["target_date"])),
            [],
        ).append(row)
    for group in grouped.values():
        batches.append(summarize_forecast_batch(group, expected_model_keys=args.models))
    return {
        "schema_version": "d1_d2_forecast_run_probe_v1",
        "probe_kind": "one_shot_non_production",
        "observed_at_utc": observed_at.isoformat(),
        "decision_at_utc": decision_at,
        "requested_run_candidate": requested_run,
        "requested_run_candidate_basis": (
            "explicit_cli" if args.run else f"decision_minus_{args.availability_lag_hours}h_floored_6h"
        ),
        "run_acceptance_rule": "exact endpoint success + no fallback + request/raw hash",
        "city": args.city,
        "provider_samples": provider_samples,
        "identified_models": sum(row["status"] == "identified" for row in provider_samples),
        "blocked_models": len(blockers),
        "forecast_rows": rows,
        "forecast_batches": batches,
        "blockers": blockers,
        "production_writes": 0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--city", default="Tokyo")
    parser.add_argument("--latitude", type=float, default=35.6762)
    parser.add_argument("--longitude", type=float, default=139.6503)
    parser.add_argument("--decision-at-utc")
    parser.add_argument("--run")
    parser.add_argument("--availability-lag-hours", type=int, default=12)
    parser.add_argument("--forecast-days", type=int, default=4)
    parser.add_argument("--timeout-sec", type=float, default=45.0)
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_GLOBAL_SINGLE_RUN_MODELS))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.output_dir = prepare_new_run_output(
        resolve_run_output(
            ARTIFACT_FAMILY,
            run_id=args.run_id,
            explicit_output=args.output_dir,
        )
    )
    result = run_probe(args)
    output = args.output_dir / "summary.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"forecast_rows", "forecast_batches", "provider_samples", "blockers"}}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
