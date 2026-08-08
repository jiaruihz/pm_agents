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


def test_pm_runtime_path_cli_uses_the_shared_loader() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/ops/weather_production_path.py", "pm_runtime_root"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "/Volumes/jrs/pm_agents/runtime"


def test_archive_paths_cli_uses_the_shared_loader() -> None:
    expected = {
        "archive_storage_root": "/Volumes/jrs-archive",
        "research_artifact_root": "/Volumes/jrs-archive/pm_agents/research/artifact_store",
    }
    for name, path in expected.items():
        result = subprocess.run(
            [sys.executable, "scripts/ops/weather_production_path.py", name],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == path


def test_managed_runtime_artifacts_stay_on_contract_storage() -> None:
    spec = load_production_spec()
    for runtime in spec.managed_runtimes:
        if runtime.health_path:
            assert runtime.health_path.is_relative_to(spec.data_feed_runtime_root) or runtime.health_path.is_relative_to(
                spec.pm_runtime_root
            ), runtime.instance_id
        if runtime.live_order_path:
            assert runtime.live_order_path.is_relative_to(spec.pm_runtime_root) or runtime.live_order_path.is_relative_to(
                spec.data_feed_runtime_root
            ), runtime.instance_id


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
        "start_low_price_yes_lottery_shadow_tmux.sh",
        "start_low_price_yes_lottery_tiny_live.sh",
        "start_mac_regime_routed_no_shadow_loop.sh",
        "start_metar_reversal_false_fade_reheat_shadow_v1.sh",
        "start_weather_knmi_first_seen_ladder_shadow_v1.sh",
        "start_d1_multisource_consensus_shadow_v1.sh",
        "start_europe_d1_distance2_dual_no_shadow_v1.sh",
        "start_tmax_distribution_edge_first_lock_no_current_yes_shadow_v1.sh",
        "start_weather_korea_first_seen_collector.sh",
        "start_weather_fast_source_stale_book_production.sh",
        "start_weather_runtime_monitor.sh",
    ]
    for name in current_entrypoints:
        text = (ROOT / "scripts/ops" / name).read_text(encoding="utf-8")
        assert "targeted_output" not in text, name
        assert "full_ladder_output" not in text, name
        assert "$PROJECT_DIR/runtime/weather_edge_v1" not in text, name
        assert "$ROOT/runtime/weather_edge_v1" not in text, name


def test_metar_reversal_entrypoint_passes_controller_runtime_dir() -> None:
    text = (
        ROOT / "scripts/ops/start_metar_reversal_false_fade_reheat_shadow_v1.sh"
    ).read_text(encoding="utf-8")
    assert "--runtime-dir" in text
    assert '$(printf \'%q\' "$RUNTIME_DIR")' in text
