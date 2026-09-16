import json
from datetime import datetime, timezone

import weather_dashboard.legacy_migration.strategy_runtime_orders as migration
from scripts.ops.reconcile_weather_signal_clocks import _build_adjustment


def _issue():
    return {
        "signal_id": "canonical-signal",
        "strategy_key": "forecast_quality.low_price_yes_lottery",
        "city": "Shanghai",
        "target_date": "2026-07-05",
        "bracket": "35",
        "token_id": "token-causal",
        "model_p_yes": 0.4022,
        "condition_id": "condition",
        "market_id": "market",
        "original_snapshot_ts_utc": "2026-07-05T00:51:16Z",
        "earliest_order_ts_utc": "2026-07-04T15:00:22Z",
        "representative_execution_id": "execution-1",
        "order_payload": {
            "snapshot_ts_utc": "2026-07-05T00:51:16Z",
            "city": "Shanghai",
            "target_date": "2026-07-05",
            "bracket": "35",
            "token_id": "token-causal",
            "model_p_yes": 0.4022,
        },
        "fill_ids": ["fill-1"],
        "execution_ids": ["execution-1"],
        "fact_rows": 1,
        "cost_usd": 0.8,
        "settled_pnl_usd": 0.2,
    }


def test_reconciliation_reconstructs_only_causal_snapshot(tmp_path, monkeypatch):
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    record = {
        "city": "Shanghai",
        "event_date": "2026-07-05",
        "bracket": "35",
        "token_id": "token-causal",
        "model_prob": 0.4022,
        "snapshot_ts_utc": "2026-07-04T14:48:08Z",
    }
    (snapshot_root / "snapshot_20260704_2248.json").write_text(
        json.dumps({"records": [record]}), encoding="utf-8"
    )
    (snapshot_root / "snapshot_20260704_2304.json").write_text(
        json.dumps(
            {
                "records": [
                    {**record, "snapshot_ts_utc": "2026-07-04T15:04:23Z"}
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(migration, "SNAPSHOT_DIRS", (snapshot_root,))

    adjustment = _build_adjustment(
        _issue(), created_at_utc=datetime(2026, 8, 30, tzinfo=timezone.utc)
    )

    assert adjustment["corrected_snapshot_ts_utc"] == "2026-07-04T14:48:08Z"
    assert adjustment["timestamp_evidence_class"] == "reconstructed"
    assert adjustment["lineage_status"] == "reconstructed_causal"
    assert adjustment["source_snapshot_ref"].endswith(
        "snapshot_20260704_2248.json"
    )


def test_reconciliation_marks_missing_feature_clock_as_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "SNAPSHOT_DIRS", (tmp_path,))

    adjustment = _build_adjustment(
        _issue(), created_at_utc=datetime(2026, 8, 30, tzinfo=timezone.utc)
    )

    assert adjustment["corrected_snapshot_ts_utc"] == "2026-07-04T15:00:22Z"
    assert adjustment["timestamp_evidence_class"] == "proxy"
    assert adjustment["lineage_status"] == "blocked_no_signal_snapshot"
    assert adjustment["evidence"]["blocked_reason"] == (
        "no_matching_causal_strategy_snapshot"
    )
