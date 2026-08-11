#!/usr/bin/env python3
"""Shared CLI for reusable weather probability evaluation workflows."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .first_seen_event_ladder_panel import (
    PANEL_SCHEMA_VERSION,
    load_canonical_ladder_snapshots,
    load_direct_event_snapshots,
    load_source_events,
    materialize_panel,
)
from .forecast_repricing_position import (
    train_position_policy,
    write_position_policy_outputs,
)
from .forecast_repricing_tape import run_tape_research
from .market_prior_posterior import (
    replay_fmi_entry_metar_correction,
    run_market_prior_posterior_research,
    select_city_rows,
)
from .daily_minimum import run_daily_minimum_development


REPO_ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(
        json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def json_safe(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def write_summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    safe = json_safe(payload)
    text = json.dumps(
        safe,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return safe


def _add_first_seen_panel_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "first-seen-panel",
        help="Materialize one all-event first-seen/full-ladder research panel.",
    )
    parser.add_argument("--city", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--event-path", type=Path, action="append", required=True)
    parser.add_argument("--ladder-root", type=Path, required=True)
    parser.add_argument("--books-root", type=Path, required=True)
    parser.add_argument("--direct-capture-root", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.set_defaults(handler=run_first_seen_panel)


def _add_market_prior_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "market-prior",
        help="Run a city-level market-prior posterior walk-forward evaluation.",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument(
        "--input-city-assertion",
        help=(
            "Explicit city identity for a legacy city-scoped input that lacks a "
            "city column; must exactly match --city and is recorded in lineage."
        ),
    )
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--min-train-dates", type=int, default=3)
    parser.add_argument("--bootstrap-draws", type=int, default=4000)
    parser.add_argument(
        "--probability-event-source",
        choices=("fmi", "metar"),
        help="Optional explicit source scope for probability-head rows.",
    )
    parser.add_argument("--research-entry-cost-min-exclusive", type=float)
    parser.add_argument("--research-entry-cost-max-exclusive", type=float)
    parser.add_argument(
        "--include-ladder-features",
        action="store_true",
        help=(
            "Add reusable mode-distance, neighbor, relative-markout, and "
            "weather-shock interaction features without changing the row universe."
        ),
    )
    parser.set_defaults(handler=run_market_prior)


def _add_forecast_repricing_position_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "forecast-repricing-position",
        help=(
            "Train and replay the D-1 full-ladder entry/hold/exit position policy "
            "on an existing forecast-event rung panel."
        ),
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-train-dates", type=int, default=15)
    parser.add_argument("--holdout-fraction", type=float, default=0.20)
    parser.add_argument("--bootstrap-draws", type=int, default=2000)
    parser.add_argument(
        "--fixed-signal-input",
        type=Path,
        help="optional frozen position list to replay on the repaired input panel",
    )
    parser.set_defaults(handler=run_forecast_repricing_position)


def _add_forecast_repricing_tape_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "forecast-repricing-tape",
        help="Replay queue-conservative passive fills from reconstructed WS tape.",
    )
    parser.add_argument("--ws-root", type=Path, required=True)
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--end-utc", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--quote-modes",
        nargs="+",
        choices=("best_bid", "bid_plus_tick", "bid_plus_cent", "midpoint"),
        default=("best_bid", "bid_plus_tick", "midpoint"),
    )
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.set_defaults(handler=run_forecast_repricing_tape)


def _add_daily_minimum_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "daily-minimum",
        help="Build the Tmin PIT checkpoint panel and W0 proxy-label baseline.",
    )
    parser.add_argument("--forecast-root", type=Path, required=True)
    parser.add_argument("--observation-root", type=Path, required=True)
    parser.add_argument("--ladder-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cities", nargs="+", required=True)
    parser.add_argument("--checkpoint-hour-local", type=int, default=18)
    parser.add_argument("--min-train-dates", type=int, default=7)
    parser.add_argument("--promotion-min-dates", type=int, default=30)
    parser.set_defaults(handler=run_daily_minimum)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="workflow", required=True)
    _add_first_seen_panel_parser(subparsers)
    _add_market_prior_parser(subparsers)
    _add_forecast_repricing_position_parser(subparsers)
    _add_forecast_repricing_tape_parser(subparsers)
    _add_daily_minimum_parser(subparsers)
    return parser


def run_daily_minimum(args: argparse.Namespace) -> int:
    summary = run_daily_minimum_development(
        forecast_root=args.forecast_root,
        observation_root=args.observation_root,
        ladder_root=args.ladder_root,
        output_dir=args.output_dir,
        cities=args.cities,
        checkpoint_hour_local=args.checkpoint_hour_local,
        min_train_dates=args.min_train_dates,
        promotion_min_dates=args.promotion_min_dates,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def run_forecast_repricing_position(args: argparse.Namespace) -> int:
    frame = pd.read_csv(args.input, low_memory=False)
    result = train_position_policy(
        frame,
        min_train_dates=args.min_train_dates,
        holdout_fraction=args.holdout_fraction,
        draws=args.bootstrap_draws,
    )
    summary = write_position_policy_outputs(result, args.input, args.output_dir)
    if args.fixed_signal_input:
        from weather_model_evaluation.forecast_repricing_position import (
            replay_fixed_signal_execution,
        )

        if str(result["status"]).startswith("blocked_"):
            raise ValueError("cannot replay fixed signals without a trained position policy")
        fixed = pd.read_csv(args.fixed_signal_input, low_memory=False)
        replay, replay_summary = replay_fixed_signal_execution(
            frame,
            fixed,
            result["bundle"],
            draws=args.bootstrap_draws,
        )
        replay.to_csv(args.output_dir / "fixed_signal_execution_replay.csv", index=False)
        (args.output_dir / "fixed_signal_execution_summary.json").write_text(
            json.dumps(replay_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": summary["status"],
                "output_dir": str(args.output_dir),
                "production": summary["production"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def run_forecast_repricing_tape(args: argparse.Namespace) -> int:
    summary = run_tape_research(
        ws_root=args.ws_root,
        start_utc=args.start_utc,
        end_utc=args.end_utc,
        output_dir=args.output_dir,
        quote_modes=args.quote_modes,
        tick_size=args.tick_size,
    )
    print(
        json.dumps(
            {
                "status": summary["status"],
                "output_dir": str(args.output_dir),
                "production": summary["production"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def run_first_seen_panel(args: argparse.Namespace) -> int:
    events, event_coverage = load_source_events(
        args.event_path,
        city=args.city,
        source=args.source,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    canonical, canonical_coverage = load_canonical_ladder_snapshots(
        ladder_root=args.ladder_root,
        books_root=args.books_root,
        city=args.city,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    direct: list[dict[str, Any]] = []
    direct_coverage: dict[str, int] = {}
    if args.direct_capture_root:
        direct, direct_coverage = load_direct_event_snapshots(
            args.direct_capture_root,
            city=args.city,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    snapshots = sorted(
        [*canonical, *direct],
        key=lambda item: (item["available_at_utc"], item["snapshot_id"]),
    )
    panel, event_panel, coverage = materialize_panel(events, snapshots)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    panel_path = args.output_dir / "event_rung_panel.csv.gz"
    events_path = args.output_dir / "event_slot_coverage.csv.gz"
    panel.to_csv(panel_path, index=False, compression="gzip")
    event_panel.to_csv(events_path, index=False, compression="gzip")
    output = {
        **coverage,
        "city": args.city,
        "source": args.source,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "source_event_input_coverage": event_coverage,
        "canonical_snapshot_input_coverage": canonical_coverage,
        "direct_snapshot_input_coverage": direct_coverage,
        "input_paths": {
            "events": [str(path) for path in args.event_path],
            "ladder_root": str(args.ladder_root),
            "books_root": str(args.books_root),
            "direct_capture_root": (
                str(args.direct_capture_root) if args.direct_capture_root else None
            ),
        },
        "outputs": {
            "event_rung_panel": str(panel_path),
            "event_slot_coverage": str(events_path),
        },
        "producer": {
            "schema_version": PANEL_SCHEMA_VERSION,
            "entrypoint": "weather_model_evaluation.cli:first-seen-panel",
            "source_path": str(Path(__file__).resolve().relative_to(REPO_ROOT)),
            "source_sha256": sha256_file(Path(__file__).resolve()),
        },
    }
    write_summary(args.output_dir / "summary.json", output)
    return 0


def run_market_prior(args: argparse.Namespace) -> int:
    input_frame = pd.read_csv(args.input)
    city_scope_origin = "input_city_column"
    if "city" not in input_frame.columns:
        if args.input_city_assertion != args.city:
            raise ValueError(
                "cityless research input requires --input-city-assertion "
                "matching --city"
            )
        input_frame = input_frame.copy()
        input_frame["city"] = args.city
        city_scope_origin = "explicit_legacy_cityless_input_assertion"
    elif args.input_city_assertion is not None:
        raise ValueError(
            "--input-city-assertion is only valid when the input lacks a city column"
        )
    frame = select_city_rows(input_frame, args.city)
    input_sha256 = sha256_file(args.input)
    producer_sha256 = sha256_file(Path(__file__).resolve())
    model_source_path = Path(__file__).with_name("market_prior_posterior.py").resolve()
    model_source_sha256 = sha256_file(model_source_path)
    ladder_source_path = Path(__file__).with_name("ladder_microstructure.py").resolve()
    ladder_source_sha256 = (
        sha256_file(ladder_source_path) if args.include_ladder_features else None
    )
    probability_frame = frame
    if args.probability_event_source:
        probability_frame = frame.loc[
            frame["event_source"]
            .astype(str)
            .str.lower()
            .eq(args.probability_event_source)
        ].copy()
    result = run_market_prior_posterior_research(
        probability_frame,
        timezone=args.timezone,
        min_train_dates=args.min_train_dates,
        bootstrap_draws=args.bootstrap_draws,
        include_ladder_features=args.include_ladder_features,
    )
    source_role_replay, source_role_summary = replay_fmi_entry_metar_correction(
        frame, bootstrap_draws=args.bootstrap_draws
    )
    research_slice_replay = None
    research_slice_summary = None
    if (
        args.research_entry_cost_min_exclusive is not None
        or args.research_entry_cost_max_exclusive is not None
    ):
        research_slice_replay, research_slice_summary = (
            replay_fmi_entry_metar_correction(
                frame,
                bootstrap_draws=args.bootstrap_draws,
                research_entry_cost_min_exclusive=(
                    args.research_entry_cost_min_exclusive
                ),
                research_entry_cost_max_exclusive=(
                    args.research_entry_cost_max_exclusive
                ),
            )
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "oof_event_bracket_predictions": args.output_dir
        / "oof_event_bracket_predictions.csv.gz",
        "probability_scores": args.output_dir / "probability_scores.csv",
        "market_paired_bootstrap": args.output_dir / "market_paired_bootstrap.csv",
        "calibration": args.output_dir / "calibration.csv",
        "probability_slice_scores": args.output_dir
        / "probability_slice_scores.csv",
        "first_date_bracket_trades": args.output_dir
        / "first_date_bracket_trades.csv",
        "trade_summary": args.output_dir / "trade_summary.csv",
        "trade_slices": args.output_dir / "trade_slices.csv",
        "folds": args.output_dir / "folds.csv",
        "fmi_entry_metar_correction_replay": args.output_dir
        / "fmi_entry_metar_correction_replay.csv",
    }
    result.predictions.to_csv(
        outputs["oof_event_bracket_predictions"], index=False, compression="gzip"
    )
    result.scores.to_csv(outputs["probability_scores"], index=False)
    result.bootstrap.to_csv(outputs["market_paired_bootstrap"], index=False)
    result.calibration.to_csv(outputs["calibration"], index=False)
    result.slice_scores.to_csv(outputs["probability_slice_scores"], index=False)
    result.trades.to_csv(outputs["first_date_bracket_trades"], index=False)
    result.trade_summary.to_csv(outputs["trade_summary"], index=False)
    result.trade_slices.to_csv(outputs["trade_slices"], index=False)
    result.folds.to_csv(outputs["folds"], index=False)
    source_role_replay.to_csv(
        outputs["fmi_entry_metar_correction_replay"], index=False
    )
    if research_slice_replay is not None:
        slice_path = (
            args.output_dir / "fmi_entry_metar_correction_research_price_slice.csv"
        )
        research_slice_replay.to_csv(slice_path, index=False)
        outputs["fmi_entry_metar_correction_research_price_slice"] = slice_path
    build_id = sha256_json(
        {
            "input_sha256": input_sha256,
            "producer_sha256": producer_sha256,
            "model_source_sha256": model_source_sha256,
            "ladder_source_sha256": ladder_source_sha256,
            "city": args.city,
            "timezone": args.timezone,
            "probability_event_source": args.probability_event_source,
            "min_train_dates": args.min_train_dates,
            "bootstrap_draws": args.bootstrap_draws,
            "research_entry_cost_min_exclusive": (
                args.research_entry_cost_min_exclusive
            ),
            "research_entry_cost_max_exclusive": (
                args.research_entry_cost_max_exclusive
            ),
            "include_ladder_features": args.include_ladder_features,
            "input_city_assertion": args.input_city_assertion,
        }
    )
    summary = {
        "schema_version": "city_market_prior_posterior_v1",
        "city": args.city,
        "timezone": args.timezone,
        "input": str(args.input.resolve()),
        "input_scope": {
            "raw_rows": int(len(input_frame)),
            "city_rows": int(len(frame)),
            "city_filter": args.city,
            "city_scope_origin": city_scope_origin,
            "input_sha256": input_sha256,
            "target_date_start": str(frame["target_date"].astype(str).min()),
            "target_date_end": str(frame["target_date"].astype(str).max()),
            "event_sources": sorted(
                frame["event_source"].dropna().astype(str).str.lower().unique()
            ),
        },
        "probability_event_source": args.probability_event_source or "all",
        "include_ladder_features": args.include_ladder_features,
        "denominator": result.denominator,
        "scores": result.scores.to_dict(orient="records"),
        "market_paired_bootstrap": result.bootstrap.to_dict(orient="records"),
        "trade_summary": result.trade_summary.to_dict(orient="records"),
        "fmi_entry_metar_correction": source_role_summary,
        "fmi_entry_metar_correction_research_price_slice": research_slice_summary,
        "outputs": {key: str(path) for key, path in outputs.items()},
        "producer": {
            "entrypoint": "weather_model_evaluation.cli:market-prior",
            "source_path": str(Path(__file__).resolve().relative_to(REPO_ROOT)),
            "source_sha256": producer_sha256,
            "source_components": {
                "market_prior_posterior.py": model_source_sha256,
                "ladder_microstructure.py": ladder_source_sha256,
            },
            "build_id": build_id,
            "observed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
        "qualification": {
            "research_status": "exploratory_seen_oof",
            "formal_forward": False,
            "live_eligible": False,
            "notes": [
                "Expanding target-date OOF is confined to the supplied seen window.",
                (
                    "Probability evaluation applies no internal price or posterior-edge "
                    "threshold; any optional source scope is recorded explicitly."
                ),
                (
                    "Five-share replay uses official-fee-adjusted effective cost from "
                    "the input artifact."
                ),
            ],
        },
    }
    write_summary(args.output_dir / "summary.json", summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
