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
from .daily_minimum_next_colder import run_daily_minimum_next_colder_development
from .busan_market_prior import (
    DEFAULT_WEATHER_COLUMN as BUSAN_DEFAULT_WEATHER_COLUMN,
    DEFAULT_WEATHER_WEIGHT as BUSAN_DEFAULT_WEATHER_WEIGHT,
    ONLINE_MODEL_ID as BUSAN_ONLINE_MARKET_PRIOR_MODEL_ID,
    SCHEMA_VERSION as BUSAN_MARKET_PRIOR_SCHEMA_VERSION,
    evaluate_busan_market_prior,
    evaluate_online_busan_market_prior,
    evaluate_weight_grid as evaluate_busan_weight_grid,
    load_prediction_frame as load_busan_prediction_frame,
    replay_first_positive_edge as replay_busan_first_positive_edge,
    summarize_trade_replay as summarize_busan_trade_replay,
)


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
        "--freeze-after-min-train-dates",
        action="store_true",
        help=(
            "Fit every OOF test date on the same first min-train-dates; use "
            "when later dates form one untouched frozen-forward window."
        ),
    )
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


def _add_daily_minimum_next_colder_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "daily-minimum-next-colder-no",
        help=(
            "Build fixed intraday Tmin checkpoints and expanding-OOF "
            "physical no-touch / exact next-colder NO development heads."
        ),
    )
    parser.add_argument("--forecast-root", type=Path, required=True)
    parser.add_argument("--observation-root", type=Path, required=True)
    parser.add_argument("--ladder-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cities", nargs="+", required=True)
    parser.add_argument(
        "--checkpoint-hours-local",
        nargs="+",
        type=int,
        default=[6, 9, 12, 18, 21, 23],
    )
    parser.add_argument("--min-train-dates", type=int, default=7)
    parser.add_argument("--promotion-min-dates", type=int, default=30)
    parser.add_argument("--code-revision")
    parser.set_defaults(handler=run_daily_minimum_next_colder)


def _add_busan_market_prior_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "busan-market-prior",
        help=(
            "Score the frozen Busan weather probability as a bounded correction "
            "to the contemporaneous market prior."
        ),
    )
    parser.add_argument("--development-input", type=Path, required=True)
    parser.add_argument("--evaluation-input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--weather-column", default=BUSAN_DEFAULT_WEATHER_COLUMN
    )
    parser.add_argument(
        "--weather-weight", type=float, default=BUSAN_DEFAULT_WEATHER_WEIGHT
    )
    parser.add_argument("--freeze-cutoff", required=True)
    parser.add_argument("--forward-start", required=True)
    parser.add_argument("--bootstrap-draws", type=int, default=10_000)
    parser.set_defaults(handler=run_busan_market_prior)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="workflow", required=True)
    _add_first_seen_panel_parser(subparsers)
    _add_market_prior_parser(subparsers)
    _add_forecast_repricing_position_parser(subparsers)
    _add_forecast_repricing_tape_parser(subparsers)
    _add_daily_minimum_parser(subparsers)
    _add_daily_minimum_next_colder_parser(subparsers)
    _add_busan_market_prior_parser(subparsers)
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


def run_daily_minimum_next_colder(args: argparse.Namespace) -> int:
    summary = run_daily_minimum_next_colder_development(
        forecast_root=args.forecast_root,
        observation_root=args.observation_root,
        ladder_root=args.ladder_root,
        output_dir=args.output_dir,
        cities=args.cities,
        checkpoint_hours_local=tuple(args.checkpoint_hours_local),
        min_train_dates=args.min_train_dates,
        promotion_min_dates=args.promotion_min_dates,
        code_revision=args.code_revision,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def run_busan_market_prior(args: argparse.Namespace) -> int:
    development_raw = load_busan_prediction_frame(args.development_input)
    evaluation = load_busan_prediction_frame(args.evaluation_input)
    evaluation_dates = pd.to_datetime(
        evaluation["target_date"].astype(str), errors="raise"
    )
    evaluation_start = evaluation_dates.min()
    development_dates = pd.to_datetime(
        development_raw["target_date"].astype(str), errors="raise"
    )
    development = development_raw.loc[
        development_dates < evaluation_start
    ].copy()
    if development.empty:
        raise ValueError("no non-overlapping development dates before evaluation")
    freeze_cutoff = pd.Timestamp(args.freeze_cutoff)
    forward_start = pd.Timestamp(args.forward_start)
    if evaluation_dates.max() > freeze_cutoff:
        raise ValueError(
            "evaluation input extends beyond freeze cutoff; this would contaminate "
            "the declared next-forward window"
        )
    if forward_start <= freeze_cutoff:
        raise ValueError("forward-start must be after freeze-cutoff")

    development_grid = evaluate_busan_weight_grid(
        development,
        weather_column=args.weather_column,
        bootstrap_draws=args.bootstrap_draws,
        seed=8400,
    )
    evaluation_grid = evaluate_busan_weight_grid(
        evaluation,
        weather_column=args.weather_column,
        bootstrap_draws=args.bootstrap_draws,
        seed=8500,
    )
    selected = evaluate_busan_market_prior(
        evaluation,
        weather_column=args.weather_column,
        weather_weight=args.weather_weight,
        bootstrap_draws=args.bootstrap_draws,
        seed=8600,
    )
    online = evaluate_online_busan_market_prior(
        development,
        evaluation,
        weather_column=args.weather_column,
        bootstrap_draws=args.bootstrap_draws,
        seed=8700,
    )
    robustness_specs = (
        ("logloss_primary", (0.0, 0.125, 0.25, 0.375, 0.5, 1.0), "logloss"),
        ("brier_primary", (0.0, 0.125, 0.25, 0.375, 0.5, 1.0), "brier"),
        ("logloss_coarse", (0.0, 0.25, 0.5, 1.0), "logloss"),
        ("logloss_decimal", (0.0, 0.1, 0.2, 0.3, 0.4, 0.5), "logloss"),
    )
    robustness_rows: list[dict[str, Any]] = []
    for index, (name, weights, metric) in enumerate(robustness_specs):
        result = evaluate_online_busan_market_prior(
            development,
            evaluation,
            weather_column=args.weather_column,
            weights=weights,
            selection_metric=metric,
            bootstrap_draws=args.bootstrap_draws,
            seed=8800 + index * 100,
        )
        score = result.summary["scores"]["online_market_prior_posterior"]
        delta = result.summary["paired_candidate_minus_market"]
        replay = result.summary["fee_adjusted_taker_replay"]
        robustness_rows.append(
            {
                "variant": name,
                "selection_metric": metric,
                "weight_grid": json.dumps(list(weights)),
                "logloss": score["logloss"],
                "logloss_delta_vs_market": delta["logloss"]["delta"],
                "logloss_delta_ci_low": delta["logloss"]["ci_low"],
                "logloss_delta_ci_high": delta["logloss"]["ci_high"],
                "brier": score["brier"],
                "brier_delta_vs_market": delta["brier"]["delta"],
                "brier_delta_ci_low": delta["brier"]["ci_low"],
                "brier_delta_ci_high": delta["brier"]["ci_high"],
                "orders": replay["orders"],
                "trade_dates": replay["target_dates"],
                "pnl_usd": replay["pnl_usd"],
                "roi": replay["roi"],
                "roi_ci_low": replay["roi_ci95"][0],
                "roi_ci_high": replay["roi_ci95"][1],
                "next_weather_weight": result.summary["next_date_state"][
                    "selected_weather_weight"
                ],
            }
        )
    robustness = pd.DataFrame(robustness_rows)
    execution_rows: list[dict[str, Any]] = []
    for index, edge_buffer in enumerate((0.0, 0.005, 0.01, 0.02, 0.03, 0.05)):
        buffered_trades = replay_busan_first_positive_edge(
            online.predictions, minimum_edge=edge_buffer
        )
        buffered_summary = summarize_busan_trade_replay(
            buffered_trades,
            draws=args.bootstrap_draws,
            seed=9300 + index,
        )
        execution_rows.append(
            {
                "minimum_edge_per_share": edge_buffer,
                **buffered_summary,
            }
        )
    execution_sensitivity = pd.DataFrame(execution_rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = args.output_dir / "online_oof_predictions.csv.gz"
    trades_path = args.output_dir / "online_oof_first_positive_edge_trades.csv"
    weight_history_path = args.output_dir / "online_weight_history.csv"
    fixed_predictions_path = args.output_dir / "fixed_weight_seen_predictions.csv.gz"
    fixed_trades_path = args.output_dir / "fixed_weight_seen_trades.csv"
    robustness_path = args.output_dir / "online_robustness_variants.csv"
    execution_sensitivity_path = (
        args.output_dir / "online_execution_edge_buffer_sensitivity.csv"
    )
    development_grid_path = args.output_dir / "development_weight_grid.csv"
    evaluation_grid_path = args.output_dir / "seen_window_weight_grid.csv"
    candidate_path = args.output_dir / "candidate_spec.json"
    online.predictions.to_csv(predictions_path, index=False, compression="gzip")
    online.trades.to_csv(trades_path, index=False)
    online.weight_history.to_csv(weight_history_path, index=False)
    selected.predictions.to_csv(
        fixed_predictions_path, index=False, compression="gzip"
    )
    selected.trades.to_csv(fixed_trades_path, index=False)
    robustness.to_csv(robustness_path, index=False)
    execution_sensitivity.to_csv(execution_sensitivity_path, index=False)
    development_grid.to_csv(development_grid_path, index=False)
    evaluation_grid.to_csv(evaluation_grid_path, index=False)

    probability_delta = online.summary["paired_candidate_minus_market"]
    trade = online.summary["fee_adjusted_taker_replay"]
    gates = {
        "proper_score_point_better_than_market": bool(
            probability_delta["logloss"]["delta"] < 0.0
            and probability_delta["brier"]["delta"] < 0.0
        ),
        "proper_score_ci_better_than_market": bool(
            probability_delta["logloss"]["ci_high"] < -1e-12
            and probability_delta["brier"]["ci_high"] < -1e-12
        ),
        "fee_adjusted_roi_positive": bool(
            trade["roi"] is not None and trade["roi"] > 0.0
        ),
        "fee_adjusted_roi_ci_positive": bool(
            trade["roi_ci95"] is not None and trade["roi_ci95"][0] > 0.0
        ),
        "minimum_five_independent_innovation_dates": bool(
            online.weight_history["selected_weather_weight"].gt(0.0).sum() >= 5
        ),
        "clean_forward_evidence_present": False,
    }
    candidate_spec = {
        "schema_version": BUSAN_MARKET_PRIOR_SCHEMA_VERSION,
        "model_id": BUSAN_ONLINE_MARKET_PRIOR_MODEL_ID,
        "city": "Busan",
        "target": "final exact-rung NO settlement probability",
        "formula": (
            "daily expanding-date selection of weather_weight, then "
            "logit(p_post)=logit(p_market)+weather_weight*"
            "(logit(p_weather)-logit(p_market))"
        ),
        "weather_probability_model": args.weather_column,
        "weight_grid": [0.0, 0.125, 0.25, 0.375, 0.5, 1.0],
        "weight_selection_metric": "date-equal prior-settlement logloss",
        "current_weather_weight": online.summary["next_date_state"][
            "selected_weather_weight"
        ],
        "current_weight_trained_through": online.summary["next_date_state"][
            "trained_through"
        ],
        "market_feature_role": "prior_offset",
        "market_feature_clock": "decision_current",
        "weight_selection_provenance": (
            "each seen-window date is OOF using strictly earlier settled dates; "
            "the online family itself was selected after reviewing the seen window"
        ),
        "freeze_cutoff": args.freeze_cutoff,
        "clean_forward_start": args.forward_start,
        "clean_forward_scored_dates": 0,
        "execution_policy": (
            "first fee-positive taker edge per target_date/routine rung; "
            "max 5 shares; official Weather fee"
        ),
        "signal_notional": 0.0,
        "research_only_zero_notional": True,
        "offline_evaluator_only": True,
        "zero_notional_shadow_ready": False,
        "live_eligible": False,
        "ws_feature_role": "coverage_diagnostic_only_not_model_input",
        "admission_gates_on_seen_window": gates,
        "admission_status": (
            "fail_ci_low_sample_no_clean_forward_and_no_online_adapter"
        ),
    }
    write_summary(candidate_path, candidate_spec)

    development_sha = sha256_file(args.development_input)
    evaluation_sha = sha256_file(args.evaluation_input)
    model_source = Path(__file__).with_name("busan_market_prior.py").resolve()
    summary = {
        "schema_version": BUSAN_MARKET_PRIOR_SCHEMA_VERSION,
        "status": "offline_candidate_blocked_for_shadow",
        "candidate": candidate_spec,
        "development": {
            "input": str(args.development_input.resolve()),
            "input_sha256": development_sha,
            "raw_rows": int(len(development_raw)),
            "nonoverlapping_rows": int(len(development)),
            "overlap_rows_removed": int(len(development_raw) - len(development)),
            "overlap_policy": "target_date strictly before evaluation start",
            "target_date_start": str(development["target_date"].astype(str).min()),
            "target_date_end": str(development["target_date"].astype(str).max()),
            "weight_grid": development_grid.to_dict(orient="records"),
        },
        "online_walk_forward_seen_window": {
            "input": str(args.evaluation_input.resolve()),
            "input_sha256": evaluation_sha,
            "raw_rows": int(len(evaluation)),
            **online.summary,
            "weight_history": online.weight_history.to_dict(orient="records"),
        },
        "fixed_weight_seen_window_diagnostic": {
            **selected.summary,
            "weight_grid": evaluation_grid.to_dict(orient="records"),
        },
        "fixed_denominator": (
            "Busan settled exact-NO checkpoints with causal contemporaneous "
            "market probability; no price/edge eligibility filter for proper scores"
        ),
        "outputs": {
            "predictions": str(predictions_path),
            "trades": str(trades_path),
            "weight_history": str(weight_history_path),
            "fixed_weight_diagnostic_predictions": str(fixed_predictions_path),
            "fixed_weight_diagnostic_trades": str(fixed_trades_path),
            "robustness_variants": str(robustness_path),
            "execution_edge_buffer_sensitivity": str(execution_sensitivity_path),
            "development_weight_grid": str(development_grid_path),
            "seen_window_weight_grid": str(evaluation_grid_path),
            "candidate_spec": str(candidate_path),
        },
        "producer": {
            "entrypoint": "weather_model_evaluation.cli:busan-market-prior",
            "cli_source_sha256": sha256_file(Path(__file__).resolve()),
            "model_source_sha256": sha256_file(model_source),
            "build_id": sha256_json(
                {
                    "development_sha256": development_sha,
                    "evaluation_sha256": evaluation_sha,
                    "weather_column": args.weather_column,
                    "fixed_diagnostic_weather_weight": args.weather_weight,
                    "online_weight_grid": [0.0, 0.125, 0.25, 0.375, 0.5, 1.0],
                    "freeze_cutoff": args.freeze_cutoff,
                    "forward_start": args.forward_start,
                }
            ),
            "observed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
        "qualification": {
            "formal_forward": False,
            "reason": (
                "Seen-window dates are expanding-date OOF, but the online model "
                "family was chosen after inspecting that window. Only dates from "
                "clean_forward_start onward may be used for formal admission."
            ),
            "live_eligible": False,
        },
        "robustness_variants": robustness.to_dict(orient="records"),
        "execution_edge_buffer_sensitivity": execution_sensitivity.to_dict(
            orient="records"
        ),
    }
    write_summary(args.output_dir / "summary.json", summary)
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
        freeze_after_min_train_dates=args.freeze_after_min_train_dates,
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
    candidate_spec_path = args.output_dir / "strong_shrinkage_candidate_spec.json"
    latest_fold = result.folds.sort_values("test_date").iloc[-1]
    selection_mask = frame["target_date"].astype(str).between(
        str(latest_fold["train_start"]),
        str(latest_fold["train_end"]),
    )
    clock_classes = frame.get(
        "availability_clock_class",
        pd.Series(index=frame.index, dtype=object),
    )
    candidate_spec = {
        "schema_version": "market_weather_strong_shrinkage_candidate_v1",
        "model_id": "market_weather_strong_shrinkage_logit_blend_v1",
        "city": args.city,
        "target": "caller_expression_binary_probability",
        "formula": (
            "logit(p_post)=(1-weather_weight)*logit(p_market)+"
            "weather_weight*logit(p_weather)"
        ),
        "weather_weight": float(
            latest_fold["strong_shrinkage_weather_weight"]
        ),
        "weight_grid": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
        "selection_metric": "date-equal training Brier; ties choose lower weather weight",
        "selected_without_test_date_labels": True,
        "selection_train_start": str(latest_fold["train_start"]),
        "selection_train_end": str(latest_fold["train_end"]),
        "selection_train_dates": int(latest_fold["train_dates"]),
        "selection_train_rows": int(latest_fold["train_rows"]),
        "forward_test_start": str(result.folds["test_date"].min()),
        "forward_test_end": str(result.folds["test_date"].max()),
        "forward_test_dates": int(result.folds["test_date"].nunique()),
        "weather_model_ids": sorted(
            {
                str(value)
                for value in frame.get("weather_model_id", pd.Series(dtype=str)).dropna()
                if str(value)
            }
        ),
        "selection_clock_classes": sorted(
            {
                str(value)
                for value in clock_classes.loc[selection_mask].dropna()
                if str(value)
            }
        ),
        "last_evaluated_test_date": str(latest_fold["test_date"]),
        "research_only_zero_notional": True,
        "live_eligible": False,
    }
    write_summary(candidate_spec_path, candidate_spec)
    outputs["strong_shrinkage_candidate_spec"] = candidate_spec_path
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
            "freeze_after_min_train_dates": args.freeze_after_min_train_dates,
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
        "freeze_after_min_train_dates": args.freeze_after_min_train_dates,
        "denominator": result.denominator,
        "scores": result.scores.to_dict(orient="records"),
        "market_paired_bootstrap": result.bootstrap.to_dict(orient="records"),
        "trade_summary": result.trade_summary.to_dict(orient="records"),
        "strong_shrinkage_candidate": candidate_spec,
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
