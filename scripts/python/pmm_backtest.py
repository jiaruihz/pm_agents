from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pmm.backtest.replay_runner import (
    run_scenario_compare,
    run_scenario_file,
    run_scenarios_dir,
    run_scenarios_dir_with_fill_models,
)
from pmm.backtest.scenario_generator import generate_all_from_catalog


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pmm_backtest",
        description="PMM synthetic scenario generator and replay backtest runner",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="Generate scenario JSON files from case catalog")
    p_gen.add_argument(
        "--catalog",
        default="pmm/backtest/case_catalog.json",
        help="Path to case catalog json",
    )
    p_gen.add_argument(
        "--out-dir",
        default="pmm/backtest/scenarios",
        help="Output directory for generated scenarios",
    )
    p_gen.add_argument("--seed", type=int, default=42, help="Base random seed")

    p_run = sub.add_parser("run", help="Run replay on one scenario file")
    p_run.add_argument("--scenario", required=True, help="Scenario JSON file path")
    p_run.add_argument(
        "--out-dir",
        default="pmm/backtest/results",
        help="Output directory for run artifacts",
    )

    p_all = sub.add_parser("run-all", help="Run replay on all scenarios in a directory")
    p_all.add_argument(
        "--scenarios-dir",
        default="pmm/backtest/scenarios",
        help="Directory containing scenario json files",
    )
    p_all.add_argument(
        "--out-dir",
        default="pmm/backtest/results",
        help="Output directory for run artifacts",
    )

    p_all_models = sub.add_parser(
        "run-all-fill-models",
        help="Run all scenarios with multiple paper fill models (optimistic/conservative) and output merged leaderboard",
    )
    p_all_models.add_argument(
        "--scenarios-dir",
        default="pmm/backtest/scenarios",
        help="Directory containing scenario json files",
    )
    p_all_models.add_argument(
        "--out-dir",
        default="pmm/backtest/results_fill_models",
        help="Output directory for run artifacts",
    )
    p_all_models.add_argument(
        "--fill-models",
        default="conservative,optimistic",
        help="Comma separated fill models, e.g. conservative,optimistic",
    )

    p_plot = sub.add_parser("plot", help="Plot one scenario result directory")
    p_plot.add_argument(
        "--result-dir",
        required=True,
        help="Scenario result directory, e.g. pmm/backtest/results/b50_oscillating_fill",
    )
    p_plot.add_argument(
        "--out-dir",
        default="",
        help="Output directory for plots (default: <result-dir>/plots)",
    )

    p_plot_all = sub.add_parser("plot-all", help="Plot summary charts for all scenarios")
    p_plot_all.add_argument(
        "--results-dir",
        default="pmm/backtest/results",
        help="Directory containing summary_all.json",
    )
    p_plot_all.add_argument(
        "--out-dir",
        default="",
        help="Output directory for plots (default: <results-dir>/plots)",
    )

    p_compare = sub.add_parser(
        "compare",
        help="Run one scenario against multiple strategy profiles and output compare report",
    )
    p_compare.add_argument("--scenario", required=True, help="Base scenario json path")
    p_compare.add_argument(
        "--profiles",
        default="pmm/backtest/compare_profiles_single_level.json",
        help="Strategy profiles json path",
    )
    p_compare.add_argument(
        "--out-dir",
        default="pmm/backtest/results_compare",
        help="Output directory for compare run artifacts",
    )

    p_record = sub.add_parser(
        "record-live",
        help="Record live market orderbook + estimated trade_flow and output scenario/jsonl",
    )
    p_record.add_argument(
        "--tokens",
        required=True,
        help="Comma separated token IDs to record, e.g. YES_TOKEN_ID,NO_TOKEN_ID",
    )
    p_record.add_argument(
        "--duration",
        type=int,
        default=120,
        help="Duration in seconds (default: 120)",
    )
    p_record.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Snapshot interval in seconds (default: 1.0)",
    )
    p_record.add_argument(
        "--warmup",
        type=float,
        default=4.0,
        help="Warmup seconds before first sampled tick (default: 4.0)",
    )
    p_record.add_argument(
        "--max-levels",
        type=int,
        default=20,
        help="Top levels per side to store in each tick (default: 20)",
    )
    p_record.add_argument(
        "--out-scenario",
        default="pmm/backtest/scenarios/recorded_live.json",
        help="Output scenario JSON path",
    )
    p_record.add_argument(
        "--out-jsonl",
        default="",
        help="Optional output jsonl path (default: <out-scenario>.jsonl)",
    )
    p_record.add_argument(
        "--initial-usdc",
        type=float,
        default=100.0,
        help="Initial USDC in generated scenario (default: 100.0)",
    )
    p_record.add_argument(
        "--initial-positions-json",
        default="",
        help='Initial positions JSON, e.g. \'{"YES_ID":50,"NO_ID":50}\'',
    )

    p_convert = sub.add_parser(
        "convert-live",
        help="Convert recorded jsonl to standard replay scenario JSON",
    )
    p_convert.add_argument("--jsonl", required=True, help="Input tick jsonl file")
    p_convert.add_argument(
        "--out-scenario",
        required=True,
        help="Output scenario JSON path",
    )
    p_convert.add_argument(
        "--tokens",
        default="",
        help="Optional token IDs override, comma separated",
    )
    p_convert.add_argument(
        "--initial-usdc",
        type=float,
        default=100.0,
        help="Initial USDC in generated scenario (default: 100.0)",
    )
    p_convert.add_argument(
        "--initial-positions-json",
        default="",
        help='Initial positions JSON, e.g. \'{"YES_ID":50,"NO_ID":50}\'',
    )

    p_validate = sub.add_parser(
        "validate",
        help="Validate one scenario JSON before replay",
    )
    p_validate.add_argument("--scenario", required=True, help="Scenario JSON file path")
    p_validate.add_argument(
        "--strict",
        action="store_true",
        help="Exit with error when validation has errors",
    )

    p_validate_dir = sub.add_parser(
        "validate-dir",
        help="Validate all scenario JSON files under a directory",
    )
    p_validate_dir.add_argument(
        "--scenarios-dir",
        default="pmm/backtest/scenarios",
        help="Directory containing scenario json files",
    )
    p_validate_dir.add_argument(
        "--strict",
        action="store_true",
        help="Exit with error when any scenario has errors",
    )
    p_validate_dir.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at first failed scenario",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    if args.command == "record-live":
        from pmm.backtest.recorder import LiveRecorder
        import asyncio

        token_ids = [x.strip() for x in args.tokens.split(",") if x.strip()]
        initial_positions = {}
        if isinstance(args.initial_positions_json, str) and args.initial_positions_json.strip():
            initial_positions = json.loads(args.initial_positions_json)
            if not isinstance(initial_positions, dict):
                raise ValueError("--initial-positions-json must be a JSON object")
        recorder = LiveRecorder(
            token_ids=token_ids,
            interval=args.interval,
            max_levels=args.max_levels,
        )
        result = asyncio.run(
            recorder.run(
                duration_sec=args.duration,
                output_file=args.out_scenario,
                output_jsonl=args.out_jsonl.strip() or None,
                warmup_sec=args.warmup,
                initial_usdc=args.initial_usdc,
                initial_positions=initial_positions,
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "convert-live":
        from pmm.backtest.recorder import convert_jsonl_to_scenario

        tokens = [x.strip() for x in args.tokens.split(",") if x.strip()]
        token_ids = tokens if tokens else None
        initial_positions = {}
        if isinstance(args.initial_positions_json, str) and args.initial_positions_json.strip():
            initial_positions = json.loads(args.initial_positions_json)
            if not isinstance(initial_positions, dict):
                raise ValueError("--initial-positions-json must be a JSON object")
        result = convert_jsonl_to_scenario(
            jsonl_file=args.jsonl,
            output_file=args.out_scenario,
            token_ids=token_ids,
            initial_usdc=args.initial_usdc,
            initial_positions=initial_positions,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "validate":
        from pmm.backtest.scenario_validator import validate_scenario_file

        report = validate_scenario_file(args.scenario, strict=args.strict)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.strict and not bool(report.get("ok", False)):
            raise ValueError("scenario validation failed")
        return

    if args.command == "validate-dir":
        from pmm.backtest.scenario_validator import validate_scenarios_dir

        report = validate_scenarios_dir(
            scenarios_dir=args.scenarios_dir,
            strict=False,
            fail_fast=args.fail_fast,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.strict and int(report.get("total_errors", 0)) > 0:
            raise ValueError("scenario dir validation failed")
        return

    if args.command == "generate":
        res = generate_all_from_catalog(args.catalog, args.out_dir, seed=args.seed)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    if args.command == "run":
        result = run_scenario_file(args.scenario, out_dir=args.out_dir)
        print(json.dumps(result.summary, ensure_ascii=False, indent=2))
        print(f"metrics={result.metrics_path}")
        print(f"actions={result.actions_path}")
        print(f"summary={result.summary_path}")
        return
    if args.command == "run-all":
        report = run_scenarios_dir(args.scenarios_dir, out_dir=args.out_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"report={Path(args.out_dir) / 'summary_all.json'}")
        if report.get("leaderboard_rows"):
            print(f"table={Path(args.out_dir) / 'summary_all_table.txt'}")
        return
    if args.command == "run-all-fill-models":
        fill_models = [x.strip() for x in args.fill_models.split(",") if x.strip()]
        report = run_scenarios_dir_with_fill_models(
            scenarios_dir=args.scenarios_dir,
            out_dir=args.out_dir,
            fill_models=fill_models,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"report={Path(args.out_dir) / 'summary_all_fill_models.json'}")
        print(f"table={Path(args.out_dir) / 'summary_all_fill_models_table.txt'}")
        print(f"csv={Path(args.out_dir) / 'summary_all_fill_models_table.csv'}")
        return
    if args.command == "plot":
        from pmm.backtest.plotter import plot_scenario

        out_dir = args.out_dir.strip() if isinstance(args.out_dir, str) else ""
        result = plot_scenario(args.result_dir, out_dir=out_dir or None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "plot-all":
        from pmm.backtest.plotter import plot_all

        out_dir = args.out_dir.strip() if isinstance(args.out_dir, str) else ""
        result = plot_all(args.results_dir, out_dir=out_dir or None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "compare":
        result = run_scenario_compare(
            scenario_file=args.scenario,
            profiles_file=args.profiles,
            out_dir=args.out_dir,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"report={Path(args.out_dir) / 'compare_summary.json'}")
        return
    raise ValueError(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
