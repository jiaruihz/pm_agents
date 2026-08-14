from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.ops import production_reliability_supervisor as supervisor


def result(returncode: int = 0, stdout: str = "", stderr: str = "") -> supervisor.CommandResult:
    return supervisor.CommandResult(returncode, stdout, stderr, 0.01)


def test_incident_debounce_and_resolution_preserve_impact_window() -> None:
    start = datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc)
    finding = supervisor.issue(
        "weather.runtime.books",
        "critical",
        "stale",
        component="weather-runtime",
    )
    state1, transitions1 = supervisor.update_incident_state({}, [finding], start)
    assert transitions1 == []
    assert state1["issues"][finding["key"]]["active"] is False

    state2, transitions2 = supervisor.update_incident_state(
        state1, [finding], start + timedelta(minutes=1)
    )
    assert [row["event"] for row in transitions2] == ["opened"]
    assert state2["issues"][finding["key"]]["active"] is True

    _, transitions3 = supervisor.update_incident_state(
        state2, [], start + timedelta(minutes=2)
    )
    assert [row["event"] for row in transitions3] == ["resolved"]
    assert transitions3[0]["impact_started_utc"] == supervisor.iso_utc(start)
    assert transitions3[0]["impact_ended_utc"] == supervisor.iso_utc(
        start + timedelta(minutes=2)
    )


def test_weather_health_classifies_live_and_safe_shadow() -> None:
    payload = {
        "status": "critical",
        "manifest_status": "healthy",
        "critical_manifest_findings": [],
        "jrs_context_health": {"status": "healthy", "returncode": 0},
        "data_feed_semantic_health": {"status": "healthy"},
        "runtimes": [
            {
                "instance_id": "live-one",
                "status": "critical",
                "expected_live": True,
                "role": "strategy",
                "recovery_policy": "guarded_live",
                "issues": ["health_artifact_stale"],
            },
            {
                "instance_id": "shadow-one",
                "status": "critical",
                "expected_live": False,
                "role": "shadow",
                "recovery_policy": "safe",
                "issues": ["health_artifact_stale"],
            },
        ],
    }

    def runner(command: list[str], cwd: Path | None, timeout: float) -> supervisor.CommandResult:
        return result(stdout=json.dumps(payload))

    health = supervisor.collect_weather(runner)
    by_key = {row["key"]: row for row in health["findings"]}
    assert by_key["weather.runtime.live-one"]["severity"] == "critical"
    assert "repair" not in by_key["weather.runtime.live-one"]
    assert by_key["weather.runtime.shadow-one"]["severity"] == "warning"
    assert by_key["weather.runtime.shadow-one"]["repair"] == {
        "kind": "weather_restart",
        "target": "shadow-one",
    }


def test_crypto_registry_and_data_freshness(tmp_path: Path) -> None:
    crypto_root = tmp_path / "crypto"
    runtime_root = tmp_path / "runtime"
    raw_root = tmp_path / "raw"
    (crypto_root / "configs").mkdir(parents=True)
    runtime_root.mkdir()
    collector_root = raw_root / "2026-08-14" / "session-btc"
    collector_root.mkdir(parents=True)
    eth_root = raw_root / "2026-08-14" / "session-eth"
    eth_root.mkdir(parents=True)
    registry = {
        "profile_id": "test",
        "services": [
            {
                "service_id": "one",
                "lifecycle": "active",
                "labels": ["com.cryptoquant.pm5mone"],
            }
        ],
    }
    (crypto_root / "configs/pm5m-runtime.json").write_text(json.dumps(registry))
    (runtime_root / "settlement-service.status.json").write_text("{}")
    (runtime_root / "future-context.status.json").write_text("{}")
    (collector_root / "collector.status.json").write_text(
        json.dumps({"symbol": "btc", "connected": True, "transport_warm": True})
    )
    (eth_root / "collector.status.json").write_text(
        json.dumps({"symbol": "eth", "connected": True, "transport_warm": True})
    )
    now_epoch = max(path.stat().st_mtime for path in runtime_root.iterdir())

    def runner(command: list[str], cwd: Path | None, timeout: float) -> supervisor.CommandResult:
        return result(stdout="123\t0\tcom.cryptoquant.pm5mone\n")

    health = supervisor.collect_crypto(
        runner,
        crypto_root=crypto_root,
        runtime_root=runtime_root,
        raw_root=raw_root,
        now_epoch=now_epoch,
    )
    assert health["status"] == "healthy"
    assert health["expected_labels"] == 1
    assert health["running_labels"] == 1


def test_crypto_collector_idle_between_capture_windows_is_healthy(tmp_path: Path) -> None:
    crypto_root = tmp_path / "crypto"
    runtime_root = tmp_path / "runtime"
    raw_root = tmp_path / "raw"
    (crypto_root / "configs").mkdir(parents=True)
    runtime_root.mkdir()
    # Keep one active service so the registry is considered valid.
    (crypto_root / "configs/pm5m-runtime.json").write_text(
        json.dumps(
            {
                "profile_id": "test",
                "services": [
                    {
                        "service_id": "one",
                        "lifecycle": "active",
                        "labels": ["com.cryptoquant.pm5mone"],
                    }
                ],
            }
        )
    )
    (runtime_root / "settlement-service.status.json").write_text("{}")
    (runtime_root / "future-context.status.json").write_text("{}")
    for symbol in ("btc", "eth"):
        collector = raw_root / "2026-08-14" / f"session-{symbol}"
        collector.mkdir(parents=True)
        (collector / "collector.status.json").write_text(
            json.dumps(
                {
                    "symbol": symbol,
                    "connected": False,
                    "transport_warm": False,
                    "idle_until_capture_window": True,
                    "next_capture_window_seconds": 60,
                }
            )
        )
    now_epoch = max(path.stat().st_mtime for path in runtime_root.iterdir())

    def runner(
        command: list[str], cwd: Path | None, timeout: float
    ) -> supervisor.CommandResult:
        return result(stdout="123\t0\tcom.cryptoquant.pm5mone\n")

    health = supervisor.collect_crypto(
        runner,
        crypto_root=crypto_root,
        runtime_root=runtime_root,
        raw_root=raw_root,
        now_epoch=now_epoch,
    )
    assert health["status"] == "healthy"
    assert all(
        row["idle_until_capture_window"] is True for row in health["artifacts"][-2:]
    )


def test_collect_weather_ignores_only_its_bounded_worker_session() -> None:
    payload = {
        "status": "warning",
        "manifest_status": "warning",
        "critical_manifest_findings": [],
        "critical_runtimes": [],
        "extra_sessions": ["weather_reliability_worker_123_456"],
        "jrs_context_health": {"status": "healthy"},
        "data_feed_semantic_health": {"status": "healthy"},
        "runtimes": [],
    }

    def runner(
        command: list[str], cwd: Path | None, timeout: float
    ) -> supervisor.CommandResult:
        return result(stdout=json.dumps(payload))

    health = supervisor.collect_weather(runner)
    assert health["status"] == "healthy"
    assert health["health"]["extra_sessions"] == []


def test_outer_supervisor_collects_snapshot_through_jrs_worker(tmp_path: Path) -> None:
    payload = {
        "schema_version": "production_reliability_snapshot_v1",
        "status": "healthy",
        "findings": [],
        "sections": {},
    }
    seen: list[str] = []

    def runner(
        command: list[str], cwd: Path | None, timeout: float
    ) -> supervisor.CommandResult:
        seen.extend(command)
        return result(stdout=json.dumps(payload))

    snapshot = supervisor.collect_snapshot_via_jrs(
        crypto_root=tmp_path / "crypto",
        crypto_runtime_root=tmp_path / "runtime",
        crypto_raw_root=tmp_path / "raw",
        maintain_weather_route=True,
        runner=runner,
    )
    assert "--jrs-worker" in seen
    assert "--maintain-weather-route" in seen
    assert snapshot["status"] == "healthy"
    assert snapshot["jrs_worker"]["returncode"] == 0


def test_telegram_delivery_routes_are_deduplicated(monkeypatch) -> None:
    class Route:
        def __init__(self, route_key: str, proxy_url: str) -> None:
            self.route_key = route_key
            self.proxy_url = proxy_url

    class Spec:
        market_proxy_routes = (
            Route("allblue", "http://127.0.0.1:7897"),
            Route("stable", "http://127.0.0.1:7896"),
        )
        market_proxy_stable_upstream_url = "http://127.0.0.1:7897"

    monkeypatch.setattr(supervisor, "load_production_spec", lambda: Spec())
    assert supervisor.telegram_delivery_routes() == [
        ("allblue", "http://127.0.0.1:7897"),
        ("direct", None),
    ]


def test_telegram_notification_falls_back_to_next_route(monkeypatch) -> None:
    attempts: list[str | None] = []

    def send(message: str, *, proxy_url: str | None = None) -> dict:
        attempts.append(proxy_url)
        if proxy_url == "http://127.0.0.1:7897":
            raise TimeoutError("first route unavailable")
        return {"ok": True}

    monkeypatch.setattr(
        supervisor,
        "telegram_delivery_routes",
        lambda: [
            ("allblue", "http://127.0.0.1:7897"),
            ("stable_upstream", "http://127.0.0.1:7890"),
        ],
    )
    monkeypatch.setattr(
        "src.platform.notification.telegram.send_telegram_message_sync", send
    )
    result = supervisor.notify_telegram(
        [{"event": "opened", "severity": "critical", "key": "test"}]
    )
    assert attempts == ["http://127.0.0.1:7897", "http://127.0.0.1:7890"]
    assert result == {"status": "sent", "response_ok": True, "route": "stable_upstream"}


def test_safe_repair_never_adds_live_confirmation(tmp_path: Path) -> None:
    state = {
        "issues": {
            "weather.runtime.shadow": {
                "key": "weather.runtime.shadow",
                "active": True,
                "repair": {"kind": "weather_restart", "target": "shadow"},
                "repair_attempts": 0,
                "last_repair_epoch": 0,
                "first_seen_utc": "2026-08-14T00:00:00Z",
            }
        }
    }
    seen: list[str] = []

    def runner(command: list[str], cwd: Path | None, timeout: float) -> supervisor.CommandResult:
        seen.extend(command)
        return result(stdout="{}")

    action = supervisor.apply_one_safe_repair(
        state, runtime_storage_ok=True, runner=runner, crypto_root=tmp_path
    )
    assert action is not None
    assert "--confirm-live" not in seen
    assert seen[seen.index("--instance") + 1] == "shadow"


def test_safe_repair_is_blocked_when_jrs_is_unhealthy(tmp_path: Path) -> None:
    state = {
        "issues": {
            "weather.runtime.shadow": {
                "key": "weather.runtime.shadow",
                "active": True,
                "repair": {"kind": "weather_restart", "target": "shadow"},
                "repair_attempts": 0,
                "last_repair_epoch": 0,
                "first_seen_utc": "2026-08-14T00:00:00Z",
            }
        }
    }
    called = False

    def runner(command: list[str], cwd: Path | None, timeout: float) -> supervisor.CommandResult:
        nonlocal called
        called = True
        return result()

    assert (
        supervisor.apply_one_safe_repair(
            state, runtime_storage_ok=False, runner=runner, crypto_root=tmp_path
        )
        is None
    )
    assert called is False
