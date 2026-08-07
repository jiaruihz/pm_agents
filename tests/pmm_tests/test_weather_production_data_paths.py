from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.strategies.runtime.production import load_production_spec
from weather_data_feed_service import cli


ROOT = Path(__file__).resolve().parents[2]


def test_production_data_roots_are_distinct_and_canonical() -> None:
    spec = load_production_spec()
    assert spec.resolved_market_books_root() == spec.data_feed_runtime_root / "market_books"
    assert spec.resolved_strategy_snapshot_root() == spec.data_feed_runtime_root / "strategy_snapshots"
    assert spec.resolved_market_ladder_snapshot_root() == spec.data_feed_runtime_root / "market_ladder_snapshots"
    assert spec.resolved_forecast_output_root() == spec.data_feed_runtime_root / "forecast"


def test_production_path_cli_uses_the_shared_loader() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/ops/weather_production_path.py", "market_books_latest"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "/Volumes/jrs/weather_data_feed_service_runtime/market_books/latest.json"


def test_strategy_snapshot_is_the_canonical_snapshot_command(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        cli,
        "_run_legacy",
        lambda module, argv: calls.append((module, argv)) or 0,
    )
    assert cli.main(["strategy-snapshot", "--", "--orderbook-scope", "all"]) == 0
    assert calls == [
        (
            "paper_snapshot",
            ["--orderbook-scope", "all", "--orderbook-scope", "strategy_live"],
        )
    ]


def test_current_entrypoints_do_not_depend_on_retired_data_roots() -> None:
    current_entrypoints = [
        "start_mac_weather_data_feed_loop.sh",
        "start_weather_forecast_curve_collector_v1.sh",
        "start_weather_market_books.sh",
        "start_weather_current_yes_core_carry_tiny_live_v2.sh",
        "start_weather_current_yes_heat_death_shadow_v1.sh",
        "start_low_price_yes_integrated_tail_shadow_v2.sh",
        "start_low_price_yes_lottery_tiny_live.sh",
        "start_mac_regime_routed_no_shadow_loop.sh",
        "start_metar_reversal_false_fade_reheat_shadow_v1.sh",
        "start_weather_knmi_first_seen_ladder_shadow_v1.sh",
        "start_d1_multisource_consensus_shadow_v1.sh",
        "start_europe_d1_distance2_dual_no_shadow_v1.sh",
        "start_tmax_distribution_edge_first_lock_no_current_yes_shadow_v1.sh",
        "start_weather_korea_first_seen_collector.sh",
        "start_weather_fast_source_stale_book_production.sh",
    ]
    for name in current_entrypoints:
        text = (ROOT / "scripts/ops" / name).read_text(encoding="utf-8")
        assert "targeted_output" not in text, name
        assert "full_ladder_output" not in text, name
