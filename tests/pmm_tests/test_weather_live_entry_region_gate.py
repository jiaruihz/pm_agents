import json
import subprocess
import sys

import pytest

from scripts.ops.weather_live_entry_region_gate import (
    GATE_SCHEMA_VERSION,
    LiveEntryRegionBlocked,
    assert_live_entry_region_allowed,
)


def _write_gate(tmp_path, **changes):
    (tmp_path / "maintain.lock").write_text("", encoding="utf-8")
    payload = {
        "schema_version": GATE_SCHEMA_VERSION,
        "updated_at_epoch": 1_000,
        "entry_allowed": True,
        "verified_node": "hk-node",
        "reason": "verified_tradeable_route",
    }
    payload.update(changes)
    (tmp_path / "live_entry_gate.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_exact_execution_proxy_must_be_tradeable(tmp_path):
    _write_gate(tmp_path)
    calls = []

    result = assert_live_entry_region_allowed(
        "http://127.0.0.1:7896",
        state_root=tmp_path,
        now_epoch=1_010,
        geoblock_probe=lambda proxy: calls.append(proxy)
        or {"ok": True, "blocked": False, "trading_allowed": True},
    )

    assert calls == ["http://127.0.0.1:7896"]
    assert result["gate"]["verified_node"] == "hk-node"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"entry_allowed": False, "reason": "route_switch_in_progress"}, "route_switch_in_progress"),
        ({"updated_at_epoch": 600}, "route_gate_stale"),
        ({"schema_version": "old"}, "route_gate_schema_mismatch"),
    ],
)
def test_closed_stale_or_unknown_gate_fails_before_network(
    tmp_path, changes, reason
):
    _write_gate(tmp_path, **changes)

    with pytest.raises(LiveEntryRegionBlocked, match=reason):
        assert_live_entry_region_allowed(
            "http://127.0.0.1:7896",
            state_root=tmp_path,
            now_epoch=1_000,
            geoblock_probe=lambda _proxy: pytest.fail("must not probe"),
        )


def test_live_geoblock_is_final_authority(tmp_path):
    _write_gate(tmp_path)

    with pytest.raises(LiveEntryRegionBlocked, match="polymarket_region_blocked"):
        assert_live_entry_region_allowed(
            "http://127.0.0.1:7896",
            state_root=tmp_path,
            now_epoch=1_001,
            geoblock_probe=lambda _proxy: {
                "ok": True,
                "blocked": True,
                "trading_allowed": False,
            },
        )


def test_missing_lock_or_gate_fails_closed(tmp_path):
    with pytest.raises(LiveEntryRegionBlocked, match="route_lock_unavailable"):
        assert_live_entry_region_allowed(
            "http://127.0.0.1:7896", state_root=tmp_path
        )

    (tmp_path / "maintain.lock").write_text("", encoding="utf-8")
    with pytest.raises(LiveEntryRegionBlocked, match="route_gate_unavailable"):
        assert_live_entry_region_allowed(
            "http://127.0.0.1:7896", state_root=tmp_path
        )


def test_submit_refuses_while_controller_holds_exclusive_switch_lock(tmp_path):
    _write_gate(tmp_path)
    script = (
        "import fcntl,sys,time; "
        "f=open(sys.argv[1], 'r'); "
        "fcntl.flock(f.fileno(), fcntl.LOCK_EX); "
        "print('locked', flush=True); time.sleep(5)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path / "maintain.lock")],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "locked"
        with pytest.raises(LiveEntryRegionBlocked, match="route_switch_in_progress"):
            assert_live_entry_region_allowed(
                "http://127.0.0.1:7896",
                state_root=tmp_path,
                now_epoch=1_001,
                lock_wait_sec=0.1,
            )
    finally:
        proc.terminate()
        proc.wait(timeout=2)
