from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from src.strategies.runtime.production import load_production_spec
from scripts.analysis.market_structure_edge.research_scheduled_report_liquidity_gap_v1 import (
    current_book_paths,
)
from weather_data_feed.production_paths import (
    current_forecast_curves,
    current_strategy_snapshots,
    historical_orderbook_roots,
    historical_strategy_snapshots,
    strategy_snapshot_roots,
)
from weather_data_feed_service import cli


ROOT = Path(__file__).resolve().parents[2]


def test_production_data_roots_are_distinct_and_canonical() -> None:
    spec = load_production_spec()
    assert spec.resolved_market_books_root() == spec.data_feed_runtime_root / "market_books"
    assert spec.resolved_strategy_snapshot_root() == spec.data_feed_runtime_root / "strategy_snapshots"
    assert spec.resolved_market_ladder_snapshot_root() == spec.data_feed_runtime_root / "market_ladder_snapshots"
    assert spec.resolved_forecast_output_root() == spec.data_feed_runtime_root / "forecast"
    assert spec.resolved_historical_paper_snapshot_root() == Path(
        "/Volumes/jrs-archive/pm_agents/runtime/weather_edge_v1/market_data/paper_snapshots"
    )
    assert spec.resolved_historical_data_feed_runtime_root() == Path(
        "/Volumes/jrs-archive/weather_data_feed_service_runtime"
    )
    assert spec.historical_full_ladder_root() == Path(
        "/Volumes/jrs-archive/weather_data_feed_service_runtime/full_ladder_output"
    )
    assert spec.historical_targeted_root() == Path(
        "/Volumes/jrs-archive/weather_data_feed_service_runtime/targeted_output"
    )


def test_production_loader_honors_controller_injected_absolute_config(monkeypatch) -> None:
    config = ROOT / "src/strategies/runtime/production.yaml"
    monkeypatch.setenv("WEATHER_PRODUCTION_CONFIG", str(config))
    assert load_production_spec().host_role == "mac_current_production"


def test_production_loader_rejects_relative_injected_config(monkeypatch) -> None:
    monkeypatch.setenv("WEATHER_PRODUCTION_CONFIG", "src/strategies/runtime/production.yaml")
    with pytest.raises(ValueError, match="must be absolute"):
        load_production_spec()


def test_semantic_data_path_helpers_cover_current_and_history() -> None:
    spec = load_production_spec()
    assert current_strategy_snapshots() == spec.strategy_paper_snapshot_dir()
    assert historical_strategy_snapshots() == spec.resolved_historical_paper_snapshot_root()
    assert strategy_snapshot_roots() == (
        current_strategy_snapshots(),
        historical_strategy_snapshots(),
    )
    assert current_forecast_curves() == spec.forecast_hourly_curve_dir()
    assert historical_orderbook_roots() == (
        spec.historical_full_ladder_root() / "orderbook_snapshots",
        spec.historical_targeted_root() / "orderbook_snapshots",
    )


def test_historical_book_cli_override_keeps_explicit_runtime_root(tmp_path) -> None:
    targeted = tmp_path / "targeted_output/orderbook_snapshots/2026-08-01/a.jsonl.gz"
    full = tmp_path / "full_ladder_output/orderbook_snapshots/2026-08-01/b.jsonl.gz"
    targeted.parent.mkdir(parents=True)
    full.parent.mkdir(parents=True)
    targeted.write_bytes(b"")
    full.write_bytes(b"")

    assert current_book_paths(tmp_path) == [full, targeted]


def test_production_path_cli_uses_the_shared_loader() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/ops/weather_production_path.py", "market_books_latest"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "/Volumes/jrs/weather_data_feed_service_runtime/market_books/latest.json"


def test_production_value_cli_exposes_the_default_market_proxy() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/ops/weather_production_path.py", "market_proxy_default_url"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == load_production_spec().market_proxy_default_url


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
        "feature_store_root": "/Volumes/jrs/pm_agents/runtime/weather_feature_store",
        "archive_storage_root": "/Volumes/jrs-archive",
        "historical_data_feed_runtime_root": "/Volumes/jrs-archive/weather_data_feed_service_runtime",
        "historical_full_ladder_root": "/Volumes/jrs-archive/weather_data_feed_service_runtime/full_ladder_output",
        "historical_targeted_root": "/Volumes/jrs-archive/weather_data_feed_service_runtime/targeted_output",
        "historical_paper_snapshot_root": "/Volumes/jrs-archive/pm_agents/runtime/weather_edge_v1/market_data/paper_snapshots",
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


@pytest.mark.parametrize(
    "outside_root",
    [
        "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/market_data/paper_snapshots",
        "/Volumes/jrs-archive/../jrs/pm_agents/runtime/weather_edge_v1/market_data/paper_snapshots",
    ],
)
def test_historical_snapshot_root_cannot_escape_archive_storage(
    tmp_path, outside_root
) -> None:
    source = ROOT / "src/strategies/runtime/production.yaml"
    invalid = tmp_path / "production.yaml"
    invalid.write_text(
        source.read_text(encoding="utf-8").replace(
            "/Volumes/jrs-archive/pm_agents/runtime/weather_edge_v1/market_data/paper_snapshots",
            outside_root,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="historical_paper_snapshot_root must live under archive_storage_root",
    ):
        load_production_spec(invalid)


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
        "start_weather_source_event_ladder_repricing_shadow.sh",
    ]
    for name in current_entrypoints:
        text = (ROOT / "scripts/ops" / name).read_text(encoding="utf-8")
        assert "targeted_output" not in text, name
        assert "full_ladder_output" not in text, name
        assert "$PROJECT_DIR/runtime/weather_edge_v1" not in text, name
        assert "$ROOT/runtime/weather_edge_v1" not in text, name


def test_current_executable_consumers_do_not_use_retired_fast_observation_root() -> None:
    current_consumers = [
        "weather_data_feed_prod_health_check.py",
        "weather_fast_source_stale_book_observer.py",
        "tmax_distribution_edge_live_candidate_v1.py",
        "start_weather_fast_source_stale_book_production.sh",
        "start_weather_source_event_ladder_repricing_shadow.sh",
    ]
    for name in current_consumers:
        text = (ROOT / "scripts/ops" / name).read_text(encoding="utf-8")
        assert "output/high_frequency_observations" not in text, name


def test_data_feed_entrypoint_cannot_start_duplicate_fast_observation_owner() -> None:
    direct = (ROOT / "scripts/ops/start_mac_weather_data_feed_loop.sh").read_text(
        encoding="utf-8"
    )
    tmux = (ROOT / "scripts/ops/start_mac_weather_data_feed_jrs_tmux.sh").read_text(
        encoding="utf-8"
    )
    assert "high-frequency-observations --" not in direct
    assert "duplicate high-frequency producer is retired" in direct
    assert "WEATHER_DATA_FEED_HIGH_FREQUENCY_OBSERVATIONS_ENABLED='" not in tmux
    assert "fast_observation_owner=weather_live_cross_observations" in tmux


def test_executable_consumers_do_not_embed_retired_weather_data_paths() -> None:
    forbidden = (
        "targeted_output/",
        "full_ladder_output/",
        "runtime/weather_edge_v1/market_data/paper_snapshots",
        "/home/jiarui/projects/weather-predict",
        "/home/jiarui/projects/weather_data_feed_service_runtime",
    )
    roots = (
        ROOT / "scripts",
        ROOT / "src",
        ROOT / "weather_dashboard",
        ROOT / "weather_data_feed",
        ROOT / "weather_data_feed_service",
        ROOT / "weather_model_evaluation",
        ROOT / "configs",
    )
    executable_suffixes = {".py", ".sh", ".service", ".json"}
    violations: list[str] = []
    for source_root in roots:
        for path in source_root.rglob("*"):
            if not path.is_file() or path.suffix not in executable_suffixes:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            matches = [fragment for fragment in forbidden if fragment in text]
            if matches:
                violations.append(f"{path.relative_to(ROOT)}: {matches}")
    assert violations == []


def test_metar_reversal_entrypoint_passes_controller_runtime_dir() -> None:
    text = (
        ROOT / "scripts/ops/start_metar_reversal_false_fade_reheat_shadow_v1.sh"
    ).read_text(encoding="utf-8")
    assert "--runtime-dir" in text
    assert '$(printf \'%q\' "$RUNTIME_DIR")' in text


def test_reliability_supervisor_includes_execution_semantic_health() -> None:
    text = (ROOT / "scripts/ops/production_reliability_supervisor.py").read_text(
        encoding="utf-8"
    )
    assert "collect_weather_execution_semantics" in text
    assert "weather_execution_semantic_health" in text


def test_active_feature_writers_use_controller_feature_store() -> None:
    entrypoints = {
        "start_low_price_yes_integrated_tail_shadow_v2.sh": "WEATHER_FEATURE_STORE_DIR",
        "start_low_price_yes_lottery_tiny_live.sh": "WEATHER_FEATURE_STORE_DIR",
        "start_weather_current_yes_heat_death_shadow_v1.sh": "--feature-store",
    }
    for name, marker in entrypoints.items():
        text = (ROOT / "scripts/ops" / name).read_text(encoding="utf-8")
        assert "feature_store_root" in text, name
        assert marker in text, name
