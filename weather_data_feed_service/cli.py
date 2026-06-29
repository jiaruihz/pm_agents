"""CLI entrypoints for the standalone weather data feed service."""

from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SERVICE_ROOT.parent
LEGACY_RUNNER_DIR = SERVICE_ROOT / "legacy_weather_predict"
DEFAULT_RUNTIME_ROOT = SERVICE_ROOT / "runtime"


def _set_default_env(output_root: str | None, cache_root: str | None) -> None:
    output = Path(output_root) if output_root else DEFAULT_RUNTIME_ROOT / "output"
    cache = Path(cache_root) if cache_root else DEFAULT_RUNTIME_ROOT / "cache"
    os.environ.setdefault("WEATHER_DATA_FEED_OUTPUT_ROOT", str(output))
    os.environ.setdefault("WEATHER_DATA_FEED_CACHE_ROOT", str(cache))
    os.environ.setdefault("WEATHER_DATA_FEED_ROOT", str(REPO_ROOT))
    for path in (REPO_ROOT, LEGACY_RUNNER_DIR):
        raw = str(path)
        if raw not in sys.path:
            sys.path.insert(0, raw)


def _run_legacy(module_name: str, argv: list[str]) -> int:
    old_argv = sys.argv[:]
    try:
        sys.argv = [module_name, *argv]
        runpy.run_module(f"weather_data_feed_service.legacy_weather_predict.{module_name}", run_name="__main__")
    finally:
        sys.argv = old_argv
    return 0


def _append_forced_option(argv: list[str], option: str, value: str) -> list[str]:
    """Append an argparse option so the service mode wins over caller defaults."""
    return [*argv, option, value]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weather data feed service")
    parser.add_argument(
        "--output-root",
        default=None,
        help="Directory that contains paper_snapshots/, orderbook_snapshots/, and research outputs.",
    )
    parser.add_argument(
        "--cache-root",
        default=None,
        help="Directory that contains pm_history/, gfs_daily/, wu_obs/, and forecast caches.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot", help="Run the legacy-compatible targeted snapshot collector")
    snapshot.add_argument("runner_args", nargs=argparse.REMAINDER)
    snapshot_targeted = subparsers.add_parser(
        "snapshot-targeted",
        help="Run the snapshot collector with targeted current/d1 orderbook enrichment",
    )
    snapshot_targeted.add_argument("runner_args", nargs=argparse.REMAINDER)
    snapshot_full = subparsers.add_parser(
        "snapshot-full",
        help="Run the snapshot collector with full all-bracket orderbook enrichment",
    )
    snapshot_full.add_argument("runner_args", nargs=argparse.REMAINDER)
    daily = subparsers.add_parser("daily", help="Run the daily cache pipeline")
    daily.add_argument("runner_args", nargs=argparse.REMAINDER)
    observations = subparsers.add_parser("observations", help="Build the fast observation cache")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, unknown_args = parser.parse_known_args(argv)
    _set_default_env(args.output_root, args.cache_root)
    runner_args = list(getattr(args, "runner_args", [])) + list(unknown_args)
    if runner_args and runner_args[0] == "--":
        runner_args = runner_args[1:]
    if args.command == "snapshot":
        return _run_legacy("paper_snapshot", runner_args)
    if args.command == "snapshot-targeted":
        return _run_legacy("paper_snapshot", _append_forced_option(runner_args, "--orderbook-scope", "current_d1"))
    if args.command == "snapshot-full":
        return _run_legacy("paper_snapshot", _append_forced_option(runner_args, "--orderbook-scope", "all"))
    if args.command == "daily":
        return _run_legacy("daily_pipeline", runner_args)
    if args.command == "observations":
        from weather_data_feed_service.observations import main as observations_main

        return observations_main(runner_args)
    parser.error(f"unknown command: {args.command}")
    return 2
