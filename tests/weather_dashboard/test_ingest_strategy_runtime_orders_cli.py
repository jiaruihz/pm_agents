from pathlib import Path

from weather_dashboard.cli import ingest_strategy_runtime_orders as cli
from src.strategies.runtime.production import WeatherManagedRuntimeSpec, WeatherProductionSpec


def test_order_files_supplement_default_runtime_roots(tmp_path, monkeypatch) -> None:
    root = tmp_path / "runtime"
    discovered = root / "d1" / "live_orders.jsonl"
    discovered.parent.mkdir(parents=True)
    discovered.write_text("")
    explicit = tmp_path / "external" / "orders.jsonl"
    explicit.parent.mkdir()
    explicit.write_text("")
    monkeypatch.setattr(cli, "DEFAULT_ROOTS", (str(root),))

    assert cli.resolve_order_paths(None, [str(explicit)]) == [discovered, explicit]


def test_active_live_order_paths_follow_production_manifest(tmp_path) -> None:
    active = tmp_path / "runtime" / "active"
    active.mkdir(parents=True)
    active_orders = active / "live_orders.jsonl"
    active_orders.write_text("")
    dormant = tmp_path / "runtime" / "dormant"
    dormant.mkdir(parents=True)
    (dormant / "live_orders.jsonl").write_text("")
    external = tmp_path / "external"
    external.mkdir()
    external_orders = external / "orders.jsonl"
    external_orders.write_text("")

    spec = WeatherProductionSpec(
        version="test",
        host_role="test",
        operational_repo_root=tmp_path,
        canonical_db_path=tmp_path / "runtime/weather.db",
        compatibility_db_paths=(Path("runtime/weather.db"),),
        data_feed_runtime_root=tmp_path / "feed",
        pm_runtime_root=tmp_path / "runtime",
        canonical_tmux_socket="test",
        canonical_tmux_binary=Path("/usr/bin/tmux"),
        market_proxy_state_path=tmp_path / "runtime/market_proxy_state.json",
        market_proxy_default_url="http://127.0.0.1:7897",
        managed_runtimes=(
            WeatherManagedRuntimeSpec(
                instance_id="active", tmux_session="active", role="strategy",
                execution_mode="live", expected_live=True,
                live_order_path=active_orders.relative_to(tmp_path),
            ),
            WeatherManagedRuntimeSpec(
                instance_id="paper", tmux_session="paper", role="strategy",
                execution_mode="shadow", live_order_path=dormant / "live_orders.jsonl",
            ),
            WeatherManagedRuntimeSpec(
                instance_id="external", tmux_session="external", role="strategy",
                execution_mode="live", expected_live=True, live_order_path=external_orders,
            ),
        )
    )

    assert cli.resolve_active_live_order_paths(tmp_path, production_spec=spec) == [
        active_orders,
        external_orders,
    ]
