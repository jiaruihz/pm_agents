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
from weather_data_feed.assigned_forecast_models import assigned_single_run_model_key
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
from weather_clock_contract import parse_utc


DEFAULT_OUTPUT_DIR = DEFAULT_RUNTIME_ROOT / "output" / "forecast_run_capture"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


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


def _target_temperature_content(
    response: dict[str, Any], target_date: str
) -> tuple[float, str]:
    hourly = response.get("hourly") or {}
    target_values = [
        (str(timestamp), float(value))
        for timestamp, value in zip(hourly.get("time") or [], hourly.get("temperature_2m") or [])
        if str(timestamp).startswith(target_date) and value is not None
    ]
    if not target_values:
        raise ValueError(f"no temperature_2m for target_date={target_date}")
    return max(value for _, value in target_values), stable_content_hash(target_values)


def upgrade_first_seen_state_from_rows(
    state: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Upgrade legacy state without inventing a new first-seen clock.

    V1 keyed first-seen by raw content, so volatile provider metadata made the
    same model run look newly seen on every poll.  The append-only journal can
    still recover the earliest *observed* run clock.  We preserve that clock
    and label it as legacy evidence; future unseen runs are collector-exact.
    """

    upgraded = dict(state)
    if upgraded.get("state_schema_version") == "weather_forecast_run_capture_state_v2":
        return upgraded
    first_seen_by_run = dict(upgraded.get("first_seen_by_run") or {})
    first_seen_status_by_run = dict(upgraded.get("first_seen_status_by_run") or {})
    for row in rows:
        run_at = str(row.get("forecast_run_at_utc") or "")
        if not run_at:
            continue
        run_identity = "|".join(
            (
                str(row.get("model_key") or ""),
                str(row.get("city") or ""),
                str(row.get("target_date") or ""),
                run_at,
            )
        )
        observed = str(
            row.get("run_first_seen_at_utc")
            or row.get("first_seen_at_utc")
            or row.get("available_at_utc")
            or ""
        )
        if not observed:
            continue
        prior = first_seen_by_run.get(run_identity)
        if not prior or observed < str(prior):
            first_seen_by_run[run_identity] = observed
        first_seen_status_by_run[run_identity] = "legacy_earliest_observed"
    upgraded.update(
        {
            "state_schema_version": "weather_forecast_run_capture_state_v2",
            "first_seen_by_run": first_seen_by_run,
            "first_seen_status_by_run": first_seen_status_by_run,
        }
    )
    return upgraded


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
    run_history = {
        str(sequence_key): dict(versions)
        for sequence_key, versions in dict(
            state.get("run_history_by_model_city_target") or {}
        ).items()
    }
    first_seen_by_content = dict(state.get("first_seen_by_content") or {})
    first_seen_by_run = dict(state.get("first_seen_by_run") or {})
    first_seen_status_by_run = dict(state.get("first_seen_status_by_run") or {})
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
        response_complete = metadata.get("source_fetch_clock_status") == "response_complete"
        available_text = str(metadata.get("source_fetch_end_utc") or captured_text)
        available_utc = parse_utc(
            available_text, field="forecast_source_fetch_end_utc"
        )
        assert available_utc is not None
        for city_input, response in zip(city_inputs, responses):
            local_today = available_utc.astimezone(
                ZoneInfo(str(city_input["timezone_name"]))
            ).date()
            for horizon in (1, 2):
                target_date = (local_today + timedelta(days=horizon)).isoformat()
                try:
                    forecast_max_f, normalized_content_hash = _target_temperature_content(
                        response, target_date
                    )
                except ValueError:
                    continue
                sequence_key = "|".join((model, str(city_input["city"]), target_date))
                versions = dict(run_history.get(sequence_key) or {})
                current_run_ts = str(evidence["forecast_run_at_utc"])
                same_run_prior = dict(versions.get(current_run_ts) or {})
                earlier_run_timestamps = [
                    timestamp for timestamp in versions if timestamp < current_run_ts
                ]
                previous_run = (
                    dict(versions[max(earlier_run_timestamps)])
                    if earlier_run_timestamps
                    else {}
                )
                raw_hash = str(metadata.get("raw_hash") or "")
                run_identity = "|".join(
                    (model, str(city_input["city"]), target_date, current_run_ts)
                )
                run_first_observation = run_identity not in first_seen_by_run
                run_first_seen = str(
                    first_seen_by_run.setdefault(run_identity, available_text)
                )
                run_first_seen_status = str(
                    first_seen_status_by_run.setdefault(
                        run_identity,
                        (
                            "collector_response_complete"
                            if response_complete
                            else "cache_or_legacy_clock_not_response_complete"
                        ),
                    )
                )
                content_identity = stable_content_hash(
                    {
                        "model": model,
                        "city": city_input["city"],
                        "target_date": target_date,
                        "run": evidence["forecast_run_at_utc"],
                        "normalized_content_hash": normalized_content_hash,
                    }
                )
                content_first_seen = str(
                    first_seen_by_content.setdefault(content_identity, available_text)
                )
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
                row = build_forecast_row(
                    model_key=model,
                    city=str(city_input["city"]),
                    target_date=target_date,
                    forecast_max_f=forecast_max_f,
                    source_fetched_at_utc=available_text,
                    detected_at_utc=available_text,
                    first_seen_at_utc=run_first_seen,
                    available_at_utc=available_text,
                    raw_payload_hash=raw_hash,
                    normalized_content_hash=normalized_content_hash,
                    content_first_seen_at_utc=content_first_seen,
                    run_first_seen_status=run_first_seen_status,
                    producer_build_identity=producer_build_identity,
                    capture_id=capture_id,
                    batch_capture_id=batch_capture_id,
                    horizon_days_local=horizon,
                    forecast_run_at_utc=evidence["forecast_run_at_utc"],
                    forecast_run_evidence=evidence["forecast_run_evidence"],
                    forecast_run_lineage_status=evidence["forecast_run_lineage_status"],
                    lineage_blocker=evidence["blocker"],
                    assigned_model=(
                        model == assigned_single_run_model_key(str(city_input["city"]))
                    ),
                    previous_run_ts=(
                        str(previous_run.get("forecast_run_at_utc"))
                        if previous_run
                        else None
                    ),
                    previous_run_forecast_max_f=(
                        float(previous_run["forecast_max_f"])
                        if previous_run.get("forecast_max_f") is not None
                        else None
                    ),
                    previous_content_hash=(
                        str(same_run_prior.get("content_hash"))
                        if same_run_prior
                        else None
                    ),
                    previous_content_forecast_max_f=(
                        float(same_run_prior["forecast_max_f"])
                        if same_run_prior.get("forecast_max_f") is not None
                        else None
                    ),
                    revision_of_content_id=(
                        str(same_run_prior.get("capture_id")) if same_run_prior else None
                    ),
                    request_started_at_utc=metadata.get("source_fetch_start_utc"),
                    response_received_at_utc=(
                        metadata.get("source_fetch_end_utc") if response_complete else None
                    ),
                )
                row["run_first_observation"] = run_first_observation
                contract_rows.append(row)
                version_state = {
                    "forecast_run_at_utc": row["forecast_run_at_utc"],
                    "forecast_max_f": row["forecast_max_f"],
                    "content_hash": row["content_hash"],
                    "capture_id": row["capture_id"],
                }
                versions[current_run_ts] = version_state
                run_history[sequence_key] = versions
                latest_prior = dict(latest.get(sequence_key) or {})
                if not latest_prior or str(latest_prior.get("forecast_run_at_utc") or "") <= current_run_ts:
                    latest[sequence_key] = version_state
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
        "run_history_by_model_city_target": run_history,
        "first_seen_by_content": first_seen_by_content,
        "state_schema_version": "weather_forecast_run_capture_state_v2",
        "first_seen_by_run": first_seen_by_run,
        "first_seen_status_by_run": first_seen_status_by_run,
    }


def build_d1_market_capture_demands(
    rows: list[dict[str, Any]],
    *,
    ttl_minutes: int = 120,
    max_token_count: int = 12,
    allowed_cities: set[str] | None = None,
    max_model_run_age_hours: float | None = None,
) -> list[dict[str, Any]]:
    """Request bounded WS evidence for genuinely new D-1 provider runs.

    The demand is city/date based because forecast capture does not own market
    discovery.  ``market_books_ws`` resolves the current event ladder from its
    canonical REST denominator.  Duplicate polling deliveries do not emit a
    second demand because their run first-seen clock predates availability.
    """

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        run_age = row.get("model_run_age_hours")
        if (
            int(row.get("horizon_days_local") or -1) != 1
            or row.get("run_first_observation") is not True
            or row.get("run_first_seen_status") != "collector_response_complete"
            or (allowed_cities is not None and str(row.get("city") or "") not in allowed_cities)
            or (
                max_model_run_age_hours is not None
                and (run_age is None or float(run_age) > max_model_run_age_hours)
            )
            or row.get("forecast_run_lineage_status") != "identified"
            or row.get("lineage_blocker")
            or str(row.get("first_seen_at_utc") or "")
            != str(row.get("available_at_utc") or "")
        ):
            continue
        key = (
            str(row.get("city") or ""),
            str(row.get("target_date") or ""),
            str(row.get("forecast_run_at_utc") or ""),
        )
        if all(key):
            grouped.setdefault(key, []).append(row)
    output: list[dict[str, Any]] = []
    unit_by_city = {
        config.city: config.unit
        for config in load_city_configs(include_station_diff=False)
    }
    for (city, target_date, run_at), members in sorted(grouped.items()):
        revised_members = [
            row
            for row in members
            if row.get("forecast_max_f") is not None
            and row.get("run_to_run_delta_f") is not None
        ]
        if not revised_members:
            continue
        after_f = sum(float(row["forecast_max_f"]) for row in revised_members) / len(
            revised_members
        )
        before_f = sum(
            float(row["forecast_max_f"]) - float(row["run_to_run_delta_f"])
            for row in revised_members
        ) / len(revised_members)
        if abs(after_f - before_f) <= 1e-12:
            continue
        unit = unit_by_city[city]
        to_native = lambda value: value if unit == "F" else (value - 32.0) * 5.0 / 9.0
        requested_at = max(str(row["available_at_utc"]) for row in members)
        requested_clock = parse_utc(
            requested_at, field="market_capture_requested_at_utc"
        )
        assert requested_clock is not None
        model_events = [
            {
                "model_key": str(row.get("model_key") or ""),
                "capture_id": str(row.get("capture_id") or ""),
                "batch_capture_id": str(row.get("batch_capture_id") or ""),
                "run_to_run_delta_f": row.get("run_to_run_delta_f"),
                "forecast_max_f": row.get("forecast_max_f"),
                "assigned_model": bool(row.get("assigned_model")),
            }
            for row in sorted(members, key=lambda item: str(item.get("model_key") or ""))
        ]
        identity = {
            "city": city,
            "target_date": target_date,
            "forecast_run_at_utc": run_at,
            "model_revisions": [
                (row["model_key"], row["run_to_run_delta_f"])
                for row in model_events
            ],
        }
        output.append(
            {
                "schema_version": "weather_market_capture_demand_v1",
                "capture_request_id": stable_content_hash(identity),
                "reason": "d1_provider_run_first_seen",
                "city": city,
                "target_date": target_date,
                "forecast_run_at_utc": run_at,
                "requested_at_utc": requested_at,
                "available_at_utc": requested_at,
                "expires_at_utc": (
                    requested_clock + timedelta(minutes=ttl_minutes)
                ).isoformat().replace("+00:00", "Z"),
                "ladder_scope": "revision_path_plus_one_neighbor_yes_no",
                "native_unit": unit,
                "consensus_before_native": to_native(before_f),
                "consensus_after_native": to_native(after_f),
                "max_token_count": max_token_count,
                "model_events": model_events,
                "producer": "weather_data_feed_service.forecast_run_capture",
            }
        )
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--run", help="Exact UTC run wire value, e.g. 2026-08-04T12:00")
    parser.add_argument("--candidate-latest-cycle", action="store_true")
    parser.add_argument("--candidate-cycle-offset-hours", type=int, default=0)
    parser.add_argument("--now-utc")
    parser.add_argument("--cities", nargs="*")
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_GLOBAL_SINGLE_RUN_MODELS))
    parser.add_argument("--forecast-days", type=int, default=4)
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    parser.add_argument(
        "--market-capture-demand-output",
        type=Path,
        help="optional append-only D-1 WS capture demand journal",
    )
    parser.add_argument("--market-capture-demand-ttl-min", type=int, default=120)
    parser.add_argument(
        "--market-capture-demand-cities",
        nargs="*",
        help="explicit bounded pilot city allowlist; empty disables demand emission",
    )
    parser.add_argument("--market-capture-demand-max-run-age-hours", type=float, default=24.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.run) == bool(args.candidate_latest_cycle):
        raise SystemExit("specify exactly one of --run or --candidate-latest-cycle")
    now_utc = (
        parse_utc(args.now_utc, field="now_utc")
        if args.now_utc
        else datetime.now(timezone.utc)
    )
    if args.candidate_cycle_offset_hours < 0 or args.candidate_cycle_offset_hours % 6:
        raise SystemExit("--candidate-cycle-offset-hours must be a non-negative multiple of 6")
    run = args.run or latest_cycle_candidate(
        now_utc - timedelta(hours=args.candidate_cycle_offset_hours)
    )
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
    previous_state = upgrade_first_seen_state_from_rows(
        read_json(state_path, {}),
        _read_jsonl(args.output_dir / "forecast_run_rows.jsonl"),
    )
    rows, batches, state = materialize_capture(
        run=run,
        captured_at_utc=now_utc,
        city_inputs=city_inputs,
        responses_by_model=responses_by_model,
        metadata_by_model=metadata_by_model,
        expected_models=list(args.models),
        previous_state=previous_state,
    )
    append_jsonl(args.output_dir / "forecast_run_rows.jsonl", rows)
    append_jsonl(args.output_dir / "forecast_batches.jsonl", batches)
    append_jsonl(args.output_dir / "blockers.jsonl", blockers)
    demands = build_d1_market_capture_demands(
        rows,
        ttl_minutes=args.market_capture_demand_ttl_min,
        allowed_cities=set(args.market_capture_demand_cities or []),
        max_model_run_age_hours=args.market_capture_demand_max_run_age_hours,
    )
    if args.market_capture_demand_output is not None:
        append_jsonl(args.market_capture_demand_output, demands)
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
        "market_capture_demands": len(demands),
        "market_capture_demand_output": (
            str(args.market_capture_demand_output)
            if args.market_capture_demand_output is not None
            else None
        ),
    }
    write_json(args.output_dir / "latest.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
