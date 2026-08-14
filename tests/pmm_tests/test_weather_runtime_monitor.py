import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.ops import weather_runtime_monitor as monitor
from weather_dashboard import analysis_freshness


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_executor_timeout_becomes_critical_alert(tmp_path: Path) -> None:
    now = datetime(2026, 7, 3, 15, 30, tzinfo=timezone.utc)
    runtime = tmp_path / "regime"
    current_failure = {
        "returncode": 1,
        "output_tail": "httpx.ConnectTimeout: timed out\nPolyApiException[status_code=None]",
    }
    write_json(
        runtime / "latest_summary.json",
        {
            "generated_at_utc": (now - timedelta(minutes=1)).isoformat(),
            "live_enabled": True,
            "candidate_rows": 1,
            "routed_candidates": 1,
            "execution_eligible": 1,
            "plans_written": 1,
            "executor_result": current_failure,
        },
    )
    append_jsonl(
        runtime / "summary_history.jsonl",
        [
            {
                "generated_at_utc": (now - timedelta(minutes=2)).isoformat(),
                "candidate_rows": 1,
                "routed_candidates": 1,
                "execution_eligible": 1,
                "plans_written": 1,
                "executor_result": current_failure,
            }
        ],
    )
    spec = monitor.WatchSpec(
        instance="regime_test",
        display_name="Regime test",
        runtime_dir=runtime,
        mode="live",
        expected_live=True,
    )
    result = monitor.evaluate_spec(spec, now)
    assert result["status"] == "critical"
    assert any(alert["kind"] == "executor_failure" for alert in result["alerts"])
    assert any(alert["kind"] == "plans_without_live_orders" for alert in result["alerts"])


def test_historical_executor_failure_downgrades_after_current_success(tmp_path: Path) -> None:
    now = datetime(2026, 7, 3, 15, 30, tzinfo=timezone.utc)
    runtime = tmp_path / "regime"
    recovered = {
        "generated_at_utc": (now - timedelta(minutes=1)).isoformat(),
        "executor_result": {"returncode": 0, "parsed": {"paper_written": 0}},
    }
    write_json(runtime / "latest_summary.json", recovered)
    append_jsonl(
        runtime / "summary_history.jsonl",
        [
            {
                "generated_at_utc": (now - timedelta(minutes=2)).isoformat(),
                "executor_result": {"returncode": 1, "output_tail": "timeout"},
            },
            recovered,
        ],
    )
    spec = monitor.WatchSpec(
        instance="regime_test",
        display_name="Regime test",
        runtime_dir=runtime,
        mode="zero_notional_shadow",
    )

    result = monitor.evaluate_spec(spec, now)

    assert result["status"] == "warning"
    assert any(alert["kind"] == "executor_failure_recovered" for alert in result["alerts"])


def test_stale_target_date_becomes_warning_for_shadow(tmp_path: Path) -> None:
    now = datetime(2026, 7, 3, 15, 30, tzinfo=timezone.utc)
    runtime = tmp_path / "tmax"
    write_json(
        runtime / "latest_summary.json",
        {
            "generated_at_utc": (now - timedelta(minutes=1)).isoformat(),
            "target_dates": ["2026-07-01"],
            "rows_written_this_cycle": 396,
            "selected_rows_this_cycle": 157,
        },
    )
    append_jsonl(runtime / "summary_history.jsonl", [{"generated_at_utc": now.isoformat()}])
    spec = monitor.WatchSpec(
        instance="tmax_test",
        display_name="Tmax test",
        runtime_dir=runtime,
        mode="zero_notional_shadow",
        target_date_lag_warn_days=1,
    )
    result = monitor.evaluate_spec(spec, now)
    assert result["status"] == "warning"
    assert any(alert["kind"] == "stale_target_date" for alert in result["alerts"])


def test_price_blocked_live_runner_is_idle_by_policy_not_failure(tmp_path: Path) -> None:
    now = datetime(2026, 7, 3, 15, 30, tzinfo=timezone.utc)
    runtime = tmp_path / "regime"
    summary = {
        "generated_at_utc": (now - timedelta(minutes=1)).isoformat(),
        "live_enabled": True,
        "candidate_rows": 2,
        "routed_candidates": 2,
        "execution_eligible": 0,
        "plans_written": 0,
        "skip_reasons": {
            "ask_above_route_price_cap|soft_weight_to_ask_ratio_below_min|soft_size_below_min_shares": 2
        },
        "meta": {"snapshot_age_min": 7.0},
    }
    write_json(runtime / "latest_summary.json", summary)
    append_jsonl(runtime / "summary_history.jsonl", [summary])
    spec = monitor.WatchSpec(
        instance="regime_test",
        display_name="Regime test",
        runtime_dir=runtime,
        mode="live",
        expected_live=True,
        no_live_order_warn_hours=None,
    )
    result = monitor.evaluate_spec(spec, now)
    assert result["status"] == "idle_by_policy"
    assert result["alerts"] == []


def test_blocked_lifecycle_rows_do_not_count_as_recent_live_orders(tmp_path: Path) -> None:
    now = datetime(2026, 7, 18, 15, 30, tzinfo=timezone.utc)
    runtime = tmp_path / "heat"
    summary = {
        "generated_at_utc": (now - timedelta(minutes=1)).isoformat(),
        "live_enabled": True,
        "plans_written": 3,
        "executor_result": {
            "returncode": 0,
            "parsed": {"live_written": 3, "live_guard_blocks": 3},
        },
    }
    write_json(runtime / "latest_summary.json", summary)
    append_jsonl(runtime / "summary_history.jsonl", [summary])
    append_jsonl(
        runtime / "live_orders.jsonl",
        [
            {
                "status": "submitted",
                "created_at_utc": (now - timedelta(hours=4)).isoformat(),
            },
            {
                "status": "blocked",
                "created_at_utc": (now - timedelta(seconds=10)).isoformat(),
            },
        ],
    )
    spec = monitor.WatchSpec(
        instance="heat_test",
        display_name="Heat test",
        runtime_dir=runtime,
        mode="live",
        expected_live=True,
        no_live_order_warn_hours=1,
    )

    result = monitor.evaluate_spec(spec, now)

    assert result["status"] == "critical"
    assert result["latest_live_order_ts_utc"] == (now - timedelta(hours=4)).isoformat().replace("+00:00", "Z")
    assert any(alert["kind"] == "repeated_live_guard_blocks" for alert in result["alerts"])
    assert any(alert["kind"] == "no_recent_live_orders" for alert in result["alerts"])


def test_orderbook_stale_becomes_warning(tmp_path: Path) -> None:
    now = datetime(2026, 7, 3, 15, 30, tzinfo=timezone.utc)
    old_day = tmp_path / "orderbook_snapshots" / "2026-07-01"
    old_day.mkdir(parents=True)
    (old_day / "orderbook_snapshot_20260701_1200.jsonl.gz").write_bytes(b"")

    result = analysis_freshness.orderbook_freshness(tmp_path / "orderbook_snapshots", now)

    assert result["status"] == "warning"
    assert result["latest_date"] == "2026-07-01"
    assert any(alert["kind"] == "full_orderbook_snapshots_stale" for alert in result["alerts"])


def test_settlement_stale_becomes_warning(tmp_path: Path) -> None:
    import sqlite3

    now = datetime(2026, 7, 3, 15, 30, tzinfo=timezone.utc)
    db = tmp_path / "weather.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE fact_signal_candidates (
          event_date TEXT,
          decision_snapshot_ts_utc TEXT
        );
        CREATE TABLE settlement_outcomes (
          city TEXT,
          target_date TEXT
        );
        INSERT INTO fact_signal_candidates VALUES ('2026-07-03', '2026-07-03T15:00:00Z');
        INSERT INTO settlement_outcomes VALUES ('NYC', '2026-06-30');
        """
    )
    conn.close()

    result = analysis_freshness.db_freshness(db, now)

    assert result["status"] == "warning"
    assert result["settlement_outcomes_max_target_date"] == "2026-06-30"
    assert any(alert["kind"] == "settlement_outcomes_stale" for alert in result["alerts"])


def test_market_proxy_geoblock_becomes_critical_alert(tmp_path: Path) -> None:
    now = datetime(2026, 8, 14, 3, 30, tzinfo=timezone.utc)
    path = tmp_path / "market_proxy_control" / "latest.json"
    write_json(
        path,
        {
            "generated_at_utc": (now - timedelta(seconds=30)).isoformat(),
            "probe": {
                "ok": False,
                "checks": [
                    {"name": "gamma", "ok": True},
                    {"name": "clob", "ok": True},
                    {
                        "name": "geoblock",
                        "ok": False,
                        "blocked": True,
                        "trading_allowed": False,
                        "country": "SG",
                    },
                ],
            },
        },
    )

    result = monitor.evaluate_market_proxy_health(path, now)

    assert result["status"] == "critical"
    alert = next(
        row
        for row in result["alerts"]
        if row["kind"] == "market_proxy_trading_region_blocked"
    )
    assert alert["detail"]["country"] == "SG"


def test_market_proxy_unblocked_route_is_healthy(tmp_path: Path) -> None:
    now = datetime(2026, 8, 14, 3, 30, tzinfo=timezone.utc)
    path = tmp_path / "market_proxy_control" / "latest.json"
    write_json(
        path,
        {
            "generated_at_utc": (now - timedelta(seconds=30)).isoformat(),
            "probe": {
                "ok": True,
                "checks": [
                    {"name": "gamma", "ok": True},
                    {"name": "clob", "ok": True},
                    {
                        "name": "geoblock",
                        "ok": True,
                        "blocked": False,
                        "trading_allowed": True,
                        "country": "HK",
                    },
                ],
            },
        },
    )

    result = monitor.evaluate_market_proxy_health(path, now)

    assert result["status"] == "healthy"
    assert result["alerts"] == []
