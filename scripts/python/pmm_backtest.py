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
from pmm.backtest.replay_runner import run_scenario_file, run_scenarios_dir


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
    raise ValueError(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()

