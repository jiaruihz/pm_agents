#!/usr/bin/env python3
"""Run the repository's reproducible engineering validation profiles."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]

MAINTAINED_TEST_PATHS = (
    "tests/pmm_tests",
    "tests/strategy_runtime",
    "tests/basket",
    "tests/blend",
    "tests/polymarket_alpha",
    "tests/alpha_capital_agent",
    "tests/weather_data_feed",
    "tests/weather_dashboard",
    "tests/test_platform_market_data_contracts.py",
    "tests/test_platform_storage_jsonl.py",
    "tests/test_weather_clock_contract.py",
)


def validation_commands(profile: str, python: str) -> list[list[str]]:
    commands = [
        [python, "scripts/ops/check_project_structure.py", "--strict"],
        [
            python,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "tests/governance",
            "tests/ops_tests",
        ],
    ]
    if profile in {"maintained", "full"}:
        commands.append(
            [
                python,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-q",
                *MAINTAINED_TEST_PATHS,
            ]
        )
    if profile == "full":
        commands.extend(
            [
                [python, "scripts/ops/check_weather_docs.py"],
                [
                    python,
                    "-m",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    "-q",
                    "tests/research_tests",
                ],
            ]
        )
    return commands


def run_profile(
    profile: str,
    *,
    repo_root: Path = ROOT,
    python: str = sys.executable,
) -> int:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{repo_root}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(repo_root)
    )
    for command in validation_commands(profile, python):
        print(f"+ {shlex.join(command)}", flush=True)
        completed = subprocess.run(command, cwd=repo_root, env=env, check=False)
        if completed.returncode != 0:
            return completed.returncode
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("fast", "maintained", "full"),
        default="fast",
        help="fast=governance; maintained=CI suites; full=adds docs and research tests",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_profile(args.profile)


if __name__ == "__main__":
    raise SystemExit(main())
