from datetime import datetime, timedelta, timezone

from scripts.ops.weather_live_runtime_patrol import evaluate, notification_kind, read_recent_orders


def test_repeated_deterministic_submit_failures_require_runner_stop():
    now = datetime(2026, 7, 15, 6, 0, tzinfo=timezone.utc)
    rows = [
        {
            "live_submit_status": "submit_failed",
            "error": "PolyApiException: invalid expiration value, must be in the future for GTD orders",
        }
        for _ in range(3)
    ]
    health = evaluate(
        now=now,
        latest={"generated_at_utc": (now - timedelta(seconds=20)).isoformat()},
        recent_orders=rows,
        pids=[123],
        max_latest_age_sec=180,
        failure_threshold=3,
    )
    assert health["status"] == "critical"
    assert health["stop_runner_required"] is True


def test_fresh_runner_without_submit_failures_is_ok():
    now = datetime(2026, 7, 15, 6, 0, tzinfo=timezone.utc)
    health = evaluate(
        now=now,
        latest={"generated_at_utc": (now - timedelta(seconds=20)).isoformat()},
        recent_orders=[],
        pids=[123],
        max_latest_age_sec=180,
        failure_threshold=3,
    )
    assert health["status"] == "ok"
    assert health["stop_runner_required"] is False


def test_telegram_notifications_fire_once_per_incident_and_on_recovery():
    critical = {"status": "critical", "reasons": ["runner_latest_stale"]}
    assert notification_kind(critical, {}) == "critical"
    assert notification_kind(
        critical,
        {"last_status": "critical", "last_notified_fingerprint": "runner_latest_stale"},
    ) == ""
    assert notification_kind({"status": "ok", "reasons": []}, {"last_status": "critical"}) == "recovered"


def test_unreadable_order_file_does_not_crash_patrol(tmp_path, monkeypatch):
    orders = tmp_path / "orders.jsonl"
    orders.write_text("{}\n", encoding="utf-8")

    def denied(*_args, **_kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(type(orders), "read_text", denied)
    assert read_recent_orders(orders, cutoff=datetime.now(timezone.utc)) == []
