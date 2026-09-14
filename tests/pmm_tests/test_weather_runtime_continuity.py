import json
from pathlib import Path

from src.strategies.runtime.continuity import (
    RuntimeContinuity,
    acknowledge_reconciliation,
    publish_primary_error_best_effort,
)


def test_pre_execution_permission_failure_retries_and_recovers(tmp_path: Path) -> None:
    continuity = RuntimeContinuity("live", "live", True, root=tmp_path)
    continuity.begin_cycle()
    fault = continuity.record_failure(PermissionError(1, "Operation not permitted"))

    assert fault["status"] == "degraded_retrying"
    assert fault["failure_class"] == "storage_permission"
    assert fault["retry_safe"] is True
    assert fault["suspended"] is False
    assert continuity.retry_delay(10) == 10

    recovered = continuity.record_recovery()
    assert recovered is not None
    assert recovered["status"] == "recovered"
    assert recovered["active"] is False
    assert json.loads((tmp_path / "live/latest.json").read_text()) == recovered
    assert len((tmp_path / "live/events.jsonl").read_text().splitlines()) == 2


def test_exception_after_execution_requires_reconciliation(tmp_path: Path) -> None:
    continuity = RuntimeContinuity("live", "live", True, root=tmp_path)
    continuity.begin_cycle()
    continuity.mark_execution_started()
    fault = continuity.record_failure(OSError("journal unavailable"))

    assert fault["status"] == "reconciliation_required"
    assert fault["retry_safe"] is False
    assert fault["suspended"] is True
    heartbeat = continuity.record_suspended_heartbeat()
    assert heartbeat["status"] == "reconciliation_required"
    assert heartbeat["error"] == "OSError: journal unavailable"


def test_primary_error_failure_is_returned_for_internal_fallback() -> None:
    def fail() -> None:
        raise PermissionError("primary runtime unavailable")

    error = publish_primary_error_best_effort(fail)
    assert isinstance(error, PermissionError)


def test_restart_loads_existing_outage_and_requires_explicit_ack(tmp_path: Path) -> None:
    first = RuntimeContinuity("live", "live", True, root=tmp_path)
    first.mark_execution_started()
    first.record_failure(RuntimeError("ambiguous exchange handoff"))

    restarted = RuntimeContinuity("live", "live", True, root=tmp_path)
    assert restarted.suspended is True
    assert restarted.outage_started_utc is not None

    acknowledged = acknowledge_reconciliation(
        "live", reason="exchange and journal reconciled", root=tmp_path
    )
    assert acknowledged["active"] is False
    clean = RuntimeContinuity("live", "live", True, root=tmp_path)
    assert clean.suspended is False


def test_old_heartbeat_preserves_ack_without_resuming_old_process(tmp_path: Path) -> None:
    old = RuntimeContinuity("live", "live", True, root=tmp_path)
    old.mark_execution_started()
    old.record_failure(RuntimeError("ambiguous"))
    ack = acknowledge_reconciliation("live", reason="reconciled", root=tmp_path)
    before = (tmp_path / "live/latest.json").read_bytes()
    events = (tmp_path / "live/events.jsonl").read_bytes()
    for _ in range(3):
        assert old.record_suspended_heartbeat() == ack
    assert old.suspended is True
    assert (tmp_path / "live/latest.json").read_bytes() == before
    assert (tmp_path / "live/events.jsonl").read_bytes() == events
    assert RuntimeContinuity("live", "live", True, root=tmp_path).suspended is False


def test_ack_waits_for_inflight_heartbeat(tmp_path: Path, monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import src.strategies.runtime.continuity as module

    old = RuntimeContinuity("live", "live", True, root=tmp_path)
    old.mark_execution_started()
    old.record_failure(RuntimeError("ambiguous"))
    heartbeat_in_write = Event()
    release_heartbeat = Event()
    ack_started = Event()
    original = module._atomic_write_json

    def paused_write(path, payload):
        if payload.get("status") == "reconciliation_required":
            heartbeat_in_write.set()
            assert release_heartbeat.wait(5)
        original(path, payload)

    def ack():
        ack_started.set()
        return acknowledge_reconciliation("live", reason="reconciled", root=tmp_path)

    monkeypatch.setattr(module, "_atomic_write_json", paused_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        heartbeat = pool.submit(old.record_suspended_heartbeat)
        assert heartbeat_in_write.wait(5)
        acknowledgement = pool.submit(ack)
        try:
            assert ack_started.wait(5)
            assert not acknowledgement.done()
        finally:
            release_heartbeat.set()
        heartbeat.result(timeout=5)
        result = acknowledgement.result(timeout=5)
    assert module.continuity_status("live", tmp_path) == result
    assert old.record_suspended_heartbeat() == result
    assert old.suspended is True


def test_new_failure_after_ack_still_blocks_restart(tmp_path: Path) -> None:
    old = RuntimeContinuity("live", "live", True, root=tmp_path)
    old.mark_execution_started()
    old.record_failure(RuntimeError("first incident"))
    acknowledge_reconciliation("live", reason="first reconciled", root=tmp_path)
    new = RuntimeContinuity("live", "live", True, root=tmp_path)
    new.mark_execution_started()
    new.record_failure(RuntimeError("new ambiguous execution"))
    old.record_suspended_heartbeat()
    assert RuntimeContinuity("live", "live", True, root=tmp_path).suspended is True
    assert json.loads((tmp_path / "live/latest.json").read_text())["error"] == "RuntimeError: new ambiguous execution"


def test_non_object_latest_fails_closed_on_heartbeat(tmp_path: Path) -> None:
    old = RuntimeContinuity("live", "live", True, root=tmp_path)
    old.mark_execution_started()
    old.record_failure(RuntimeError("ambiguous"))
    (tmp_path / "live/latest.json").write_text("[]")
    assert old.record_suspended_heartbeat()["retry_safe"] is False
    assert RuntimeContinuity("live", "live", True, root=tmp_path).suspended is True


def test_invalid_persisted_state_never_silently_resumes_live(tmp_path: Path) -> None:
    path = tmp_path / "live/latest.json"
    path.parent.mkdir()
    for raw in ['{', '[]', '{}', '{"active":false}', '{"instance_id":"live","active":true}', '{"instance_id":"other","active":false}']:
        path.write_text(raw)
        runtime = RuntimeContinuity("live", "live", True, root=tmp_path)
        assert runtime.suspended is True
        assert runtime.phase == "continuity_state_unreadable"
        assert path.read_text() == raw
    acknowledge_reconciliation("live", reason="invalid state manually reconciled", root=tmp_path)
    assert RuntimeContinuity("live", "live", True, root=tmp_path).suspended is False


def test_unreadable_existing_state_requires_reconciliation(tmp_path: Path, monkeypatch) -> None:
    original = Path.read_text
    def unreadable(path, *args, **kwargs):
        if path.name == "latest.json":
            raise PermissionError("volume inaccessible")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", unreadable)
    assert RuntimeContinuity("live", "live", True, root=tmp_path).suspended is True


def test_safe_retry_state_survives_restart(tmp_path: Path) -> None:
    runtime = RuntimeContinuity("live", "live", True, root=tmp_path)
    runtime.record_failure(OSError("read failure before execution"))
    restarted = RuntimeContinuity("live", "live", True, root=tmp_path)
    assert restarted.suspended is False
    assert restarted.consecutive_failures == 1


def test_state_lock_blocks_a_separate_process(tmp_path: Path) -> None:
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    runtime = RuntimeContinuity("live", "live", True, root=tmp_path)
    runtime.mark_execution_started()
    runtime.record_failure(RuntimeError("ambiguous"))
    child = subprocess.Popen(
        [sys.executable, "-c", "from pathlib import Path; import sys; from src.strategies.runtime.continuity import _state_lock\nwith _state_lock(Path(sys.argv[1])):\n print('locked', flush=True)\n sys.stdin.readline()", str(tmp_path / "live")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    finished = Event()
    def ack():
        result = acknowledge_reconciliation("live", reason="reconciled", root=tmp_path)
        finished.set()
        return result
    try:
        assert child.stdout.readline().strip() == "locked"
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ack)
            try:
                assert not finished.wait(0.1)
            finally:
                child.stdin.write("release\n")
                child.stdin.flush()
            assert future.result(timeout=5)["status"] == "reconciliation_acknowledged"
        assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
