from datetime import datetime, timedelta, timezone

from scripts.ops.weather_live_runtime_patrol import evaluate


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
