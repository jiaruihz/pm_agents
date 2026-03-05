from __future__ import annotations

from pathlib import Path

from src.platform.strategy_runtime.store import StrategyRuntimeStore


def test_strategy_runtime_store_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "strategy_runtime.db"
    store = StrategyRuntimeStore(str(db_path), snapshot_retention_days=1)
    try:
        store.upsert_strategy(
            {
                "strategy_key": "demo_v1",
                "strategy_name": "Demo",
                "strategy_group": "test",
                "strategy_family": "test",
                "domain": "pmm",
                "is_active": True,
                "runner_module": "demo.runner",
                "description": "demo",
                "meta": {"x": 1},
            }
        )

        store.upsert_start(
            instance_id="inst_1",
            strategy_key="demo_v1",
            label="Demo Instance",
            execution_mode="paper",
            market_data_source="ws",
            account_id="acc_1",
            wallet_address="0xabc",
            token_ids=["t1", "t2"],
            max_position=30.0,
            telegram_enabled=False,
            pid=123,
            log_file="runtime/logs/demo.log",
            metrics_path="runtime/metrics.jsonl",
            cwd="/tmp",
            run_params={"a": 1},
            runtime_paths={"b": 2},
        )
        store.heartbeat(
            instance_id="inst_1",
            tick=1,
            pnl=1.2,
            equity=100.2,
            usdc_balance=80.0,
            open_orders=2,
            fills_total=3,
            placed_total=4,
            canceled_total=1,
            errors_total=0,
            state={"k": "v"},
            snapshot_interval_sec=1,
        )
        rows = store.list_instances(limit=10)
        assert len(rows) == 1
        assert rows[0]["strategy_key"] == "demo_v1"
        assert rows[0]["account_id"] == "acc_1"

        history = store.list_instance_history("inst_1", limit=10)
        assert len(history) >= 1

        store.mark_stopped("inst_1", status="stopped")
        stopped = store.get_instance("inst_1")
        assert stopped is not None
        assert stopped["status"] == "stopped"
    finally:
        store.close()
