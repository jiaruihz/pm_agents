#!/usr/bin/env python3
"""Build the strict D-1/D-2 weather dataset and separate market evidence funnel."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.forecast_run_contract import parse_utc, stable_content_hash  # noqa: E402
from weather_model_evaluation.d1_revision_repricing import (  # noqa: E402
    material_forecast_batches,
    run_study as run_revision_repricing_study,
)
from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)


OUTPUT_FAMILY = "d1_d2_run_aware_dataset_v1"


def read_records(path: Path | None, *, keys: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(row) for row in payload]
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [dict(row) for row in value]
    return []


def _clock_valid(row: dict[str, Any]) -> bool:
    try:
        first_seen = parse_utc(row.get("first_seen_at_utc"), field="first_seen_at_utc")
        available = parse_utc(row.get("available_at_utc"), field="available_at_utc")
    except ValueError:
        return False
    return first_seen <= available


def build_dataset(
    forecast_rows: Iterable[dict[str, Any]],
    forecast_batches: Iterable[dict[str, Any]],
    settlements: Iterable[dict[str, Any]],
    ladder_checkpoints: Iterable[dict[str, Any]],
    *,
    frozen_forward_start: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [dict(row) for row in forecast_rows]
    batches = [dict(row) for row in forecast_batches]
    settlement_by_key = {
        (str(row.get("city")), str(row.get("target_date"))): dict(row)
        for row in settlements
    }
    market_by_key = {
        (
            str(row.get("city")),
            str(row.get("target_date")),
            int(row.get("horizon_days") or -1),
        ): dict(row)
        for row in ladder_checkpoints
    }
    material_batches, rows_by_batch = material_forecast_batches(rows, batches)

    dataset: list[dict[str, Any]] = []
    for batch in material_batches:
        batch_id = str(batch.get("batch_capture_id") or "")
        members = rows_by_batch.get(batch_id, [])
        horizon_values = {
            int(row["horizon_days_local"])
            for row in members
            if row.get("horizon_days_local") is not None
        }
        horizon = next(iter(horizon_values)) if len(horizon_values) == 1 else None
        city = str(batch.get("city") or "")
        target_date = str(batch.get("target_date") or "")
        settlement = settlement_by_key.get((city, target_date))
        real_run = bool(members) and all(
            row.get("forecast_run_lineage_status") == "identified"
            and row.get("forecast_run_at_utc")
            and _clock_valid(row)
            for row in members
        )
        batch_complete = not batch.get("missing_model_keys") and bool(members)
        settlement_complete = bool(
            settlement
            and settlement.get("settlement_complete") is True
            and settlement.get("settlement_native_tmax") is not None
        )
        market = market_by_key.get((city, target_date, horizon if horizon is not None else -1))
        market_complete = bool(
            market
            and market.get("rung_completeness") is True
            and market.get("market_distribution_complete") is True
            and market.get("feature_book_snapshot_id")
        )
        oof_scoreable = real_run and batch_complete and settlement_complete and horizon in (1, 2)
        frozen_forward = bool(
            oof_scoreable
            and frozen_forward_start
            and target_date >= frozen_forward_start
        )
        dataset.append(
            {
                "dataset_row_id": stable_content_hash(
                    {"batch_capture_id": batch_id, "horizon_days_local": horizon}
                ),
                "batch_capture_id": batch_id,
                "material_batch_key": batch.get("material_batch_key"),
                "delivery_count": batch.get("delivery_count"),
                "batch_content_hash": batch.get("batch_content_hash"),
                "forecast_run_at_utc": batch.get("forecast_run_at_utc"),
                "city": city,
                "target_date": target_date,
                "horizon_days_local": horizon,
                "available_at_utc": batch.get("batch_available_at_utc"),
                "model_values": batch.get("model_values"),
                "model_count": batch.get("model_count"),
                "missing_model_keys": batch.get("missing_model_keys"),
                "mean_f": batch.get("mean_f"),
                "median_f": batch.get("median_f"),
                "q25_f": batch.get("q25_f"),
                "q75_f": batch.get("q75_f"),
                "spread_f": batch.get("spread_f"),
                "iqr_f": batch.get("iqr_f"),
                "assigned_model_value_f": batch.get("assigned_model_value_f"),
                "assigned_minus_consensus_f": batch.get("assigned_minus_consensus_f"),
                "settlement_native_tmax": settlement.get("settlement_native_tmax") if settlement else None,
                "settlement_unit": settlement.get("settlement_unit") if settlement else None,
                "real_run_identified": real_run,
                "batch_complete": batch_complete,
                "settlement_complete": settlement_complete,
                "native_ladder_complete": bool(market and market.get("rung_completeness")),
                "market_complete": market_complete,
                "feature_book_snapshot_id": market.get("feature_book_snapshot_id") if market else None,
                "oof_scoreable": oof_scoreable,
                "frozen_forward": frozen_forward,
                "weather_only_status": "scoreable" if oof_scoreable else "blocked",
                "weather_only_blockers": [
                    code
                    for condition, code in (
                        (not real_run, "real_run_unidentified"),
                        (not batch_complete, "batch_incomplete"),
                        (not settlement_complete, "settlement_incomplete"),
                        (horizon not in (1, 2), "horizon_not_d1_d2"),
                    )
                    if condition
                ],
                "market_residual_status": (
                    "eligible_after_weather_gate"
                    if oof_scoreable and horizon == 1 and market_complete
                    else "blocked"
                ),
                "market_residual_blockers": [
                    code
                    for condition, code in (
                        (not oof_scoreable, "weather_row_not_scoreable"),
                        (horizon == 2 and not market_complete, "d2_market_ladder_unavailable"),
                        (horizon == 1 and not market_complete, "d1_market_ladder_incomplete"),
                    )
                    if condition
                ],
            }
        )

    signal_funnel = {
        "unit": "forecast_batch",
        "raw_forecast_versions": len(rows),
        "raw_forecast_batches": len(batches),
        "forecast_batches": len(dataset),
        "duplicate_poll_batches_collapsed": len(batches) - len(dataset),
        "real_run_identified": sum(row["real_run_identified"] for row in dataset),
        "batch_complete": sum(row["real_run_identified"] and row["batch_complete"] for row in dataset),
        "settlement_complete": sum(row["real_run_identified"] and row["batch_complete"] and row["settlement_complete"] for row in dataset),
        "oof_scoreable": sum(row["oof_scoreable"] for row in dataset),
        "frozen_forward": sum(row["frozen_forward"] for row in dataset),
    }
    evidence_funnel = {
        "unit": "market_checkpoint",
        "decision_checkpoints": len(market_by_key),
        "native_ladder_complete": sum(bool(row.get("rung_completeness")) for row in market_by_key.values()),
        "market_complete": sum(bool(row.get("rung_completeness")) and bool(row.get("market_distribution_complete")) for row in market_by_key.values()),
        "executable": sum(bool(row.get("executable")) for row in market_by_key.values()),
        "actual_fills": sum(int(row.get("actual_fills") or 0) for row in market_by_key.values()),
    }
    by_horizon = {
        str(horizon): {
            "batches": sum(row["horizon_days_local"] == horizon for row in dataset),
            "oof_scoreable": sum(row["horizon_days_local"] == horizon and row["oof_scoreable"] for row in dataset),
            "market_complete": sum(row["horizon_days_local"] == horizon and row["market_complete"] for row in dataset),
        }
        for horizon in (1, 2)
    }
    return dataset, {
        "schema_version": "d1_d2_run_aware_dataset_summary_v1",
        "signal_funnel": signal_funnel,
        "evidence_funnel": evidence_funnel,
        "by_horizon": by_horizon,
        "frozen_forward_start": frozen_forward_start,
        "market_residual_gate": "not_run_by_contract_until_weather_only_passes",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forecast-rows", type=Path)
    parser.add_argument("--forecast-batches", type=Path)
    parser.add_argument("--settlements", type=Path)
    parser.add_argument("--ladder-checkpoints", type=Path)
    parser.add_argument("--frozen-forward-start")
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--revision-repricing", action="store_true")
    parser.add_argument("--capture-dir", type=Path)
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output_dir = resolve_run_output(
            OUTPUT_FAMILY,
            run_id=args.run_id,
            explicit_output=args.output_dir,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.revision_repricing:
        prepare_new_run_output(output_dir)
        kwargs = {
            key: value
            for key, value in {
                "capture_dir": args.capture_dir,
                "snapshot_dir": args.snapshot_dir,
                "db": args.db,
                "output_dir": output_dir,
                "report": args.report,
            }.items()
            if value is not None
        }
        run_revision_repricing_study(**kwargs)
        return 0
    if args.forecast_rows is None:
        raise SystemExit("--forecast-rows is required unless --revision-repricing is set")
    forecast_rows = read_records(args.forecast_rows, keys=("forecast_rows", "records"))
    forecast_batches = read_records(args.forecast_batches or args.forecast_rows, keys=("forecast_batches", "batches"))
    settlements = read_records(args.settlements, keys=("settlements", "records"))
    ladders = read_records(args.ladder_checkpoints, keys=("ladder_checkpoints", "records"))
    dataset, summary = build_dataset(
        forecast_rows,
        forecast_batches,
        settlements,
        ladders,
        frozen_forward_start=args.frozen_forward_start,
    )
    prepare_new_run_output(output_dir)
    with (output_dir / "dataset.jsonl").open("w", encoding="utf-8") as handle:
        for row in dataset:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "funnels.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("funnel", "stage", "unit", "count"))
        writer.writeheader()
        for name in ("signal_funnel", "evidence_funnel"):
            funnel = summary[name]
            for stage, count in funnel.items():
                if stage != "unit":
                    writer.writerow({"funnel": name, "stage": stage, "unit": funnel["unit"], "count": count})
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
