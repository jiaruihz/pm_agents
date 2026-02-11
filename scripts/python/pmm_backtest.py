from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pmm.backtest.scenario_generator import generate_all_from_catalog
from pmm.backtest.plotter import plot_all, plot_scenario
from pmm.backtest.replay_runner import (
    run_scenario_compare,
    run_scenario_file,
    run_scenarios_dir,
)


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
    return parser


def main() -> None:
    args = _build_parser().parse_args()
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
        return
    if args.command == "plot":
        out_dir = args.out_dir.strip() if isinstance(args.out_dir, str) else ""
        result = plot_scenario(args.result_dir, out_dir=out_dir or None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "plot-all":
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
