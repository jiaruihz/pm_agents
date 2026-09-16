"""Semantic weather data paths resolved from the production contract.

Consumers import these helpers instead of embedding physical current, archive,
or retired collector directory names.  The physical mapping remains owned by
``src/strategies/runtime/production.yaml``.
"""

from __future__ import annotations

from pathlib import Path

from src.strategies.runtime.production import WeatherProductionSpec, load_production_spec


def spec() -> WeatherProductionSpec:
    return load_production_spec()


def current_strategy_snapshots() -> Path:
    return spec().strategy_paper_snapshot_dir()


def historical_strategy_snapshots() -> Path:
    return spec().resolved_historical_paper_snapshot_root()


def strategy_snapshot_roots() -> tuple[Path, ...]:
    """Current first, then immutable history; callers dedupe by capture identity."""

    return (current_strategy_snapshots(), historical_strategy_snapshots())


def current_forecast_curves() -> Path:
    return spec().forecast_hourly_curve_dir()


def historical_full_ladder_root() -> Path:
    return spec().historical_full_ladder_root()


def historical_targeted_root() -> Path:
    return spec().historical_targeted_root()


def historical_orderbook_roots() -> tuple[Path, ...]:
    return (
        historical_full_ladder_root() / "orderbook_snapshots",
        historical_targeted_root() / "orderbook_snapshots",
    )
