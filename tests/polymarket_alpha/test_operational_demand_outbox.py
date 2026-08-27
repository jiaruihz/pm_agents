"""GLM-OP-02 acceptance tests: Alpha paired demand outbox writer.

All filesystem activity stays inside pytest tmp_path; no network, no owner
process, no weather configuration.
"""

from __future__ import annotations

import inspect
import json
import os
import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.platform.market_data.capture_demand import CaptureDemand
from src.polymarket_alpha.books.adapter import (
    build_owner_capture_demands,
    build_sensing_demand,
)
from src.polymarket_alpha.operational import (
    DEMAND_OUTBOX_LOCATOR,
    DemandOutboxBudgetError,
    DemandOutboxConflictError,
    DemandOutboxCorruptError,
    DemandOutboxLegError,
    DemandOutboxPathError,
    DemandOutboxWriteError,
    append_owner_demand_bundle,
)
from tests.polymarket_alpha.test_book_adapter_p0_05a import _change, _identity


def _bundle(*, requested_offset: timedelta = timedelta(0), valid_minutes: int = 10):
    from datetime import datetime, timezone

    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc) + requested_offset
    demand = build_sensing_demand(
        change_event=_change(),
        identity=_identity(),
        requested_at=now,
        valid_until=now + timedelta(minutes=valid_minutes),
        max_staleness_seconds=30,
        target_sizes=(Decimal("10"), Decimal("100")),
        run_id="op-outbox-run",
    )
    return build_owner_capture_demands(demand)


def _outbox_path(root: Path) -> Path:
    return root / DEMAND_OUTBOX_LOCATOR


def _append(root: Path, bundle, *, now=None):
    return append_owner_demand_bundle(
        root, bundle=bundle, now=now or bundle.alpha_demand.requested_at
    )


def test_happy_append_writes_one_canonical_line_with_full_receipt(tmp_path: Path) -> None:
    bundle = _bundle()
    receipt = _append(tmp_path, bundle)
    assert not receipt.replayed
    assert receipt.outbox_locator == DEMAND_OUTBOX_LOCATOR
    assert receipt.bundle_id == bundle.alpha_demand.demand_id
    assert receipt.pre_size == 0 and receipt.post_size > 0
    assert receipt.line_count == 1
    assert receipt.locked_exclusively and receipt.fsynced
    lines = _outbox_path(tmp_path).read_text().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["schema_version"] == "polymarket_alpha_demand_outbox_v1"
    assert [row["metadata"]["outcome_side"] for row in payload["owner_demands"]] == ["YES", "NO"]


def test_retry_of_same_bundle_is_replay_not_duplicate(tmp_path: Path) -> None:
    bundle = _bundle()
    first = _append(tmp_path, bundle)
    second = _append(tmp_path, bundle)
    assert second.replayed
    assert second.pre_size == second.post_size == first.post_size
    assert second.line_count == 1
    assert len(_outbox_path(tmp_path).read_text().splitlines()) == 1


def test_same_bundle_id_with_different_bytes_is_typed_conflict(tmp_path: Path) -> None:
    bundle = _bundle()
    _append(tmp_path, bundle)
    line = _outbox_path(tmp_path).read_text().splitlines()[0]
    payload = json.loads(line)
    payload["owner_demands"][0]["priority"] = "P0"
    tampered = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    _outbox_path(tmp_path).write_text(tampered)
    with pytest.raises(DemandOutboxConflictError, match="different bytes"):
        _append(tmp_path, bundle)


def test_invalid_legs_never_touch_the_filesystem(tmp_path: Path) -> None:
    good = _bundle()
    yes, no = good.owner_demands
    cases = {
        "single leg": (yes,),
        "three legs": (yes, no, no),
    }
    for label, rows in cases.items():
        with pytest.raises(DemandOutboxLegError):
            append_owner_demand_bundle(
                tmp_path,
                bundle=type(good)(good.alpha_demand, rows),
                now=good.alpha_demand.requested_at,
            )
    # different condition id on one leg (identity must be rebuilt canonically)
    foreign = CaptureDemand.create(
        consumer_id=yes.consumer_id,
        strategy_key=yes.strategy_key,
        condition_id="0xothercondition",
        token_id=yes.token_id,
        reason=yes.reason,
        priority=yes.priority,
        requested_at_utc=yes.requested_at_utc,
        expires_at_utc=yes.expires_at_utc,
        desired_transport=yes.desired_transport,
        metadata=dict(yes.metadata),
    )
    with pytest.raises(DemandOutboxLegError):
        append_owner_demand_bundle(
            tmp_path,
            bundle=type(good)(good.alpha_demand, (foreign, no)),
            now=good.alpha_demand.requested_at,
        )
    # same token on both legs
    same_token = CaptureDemand.create(
        consumer_id=no.consumer_id,
        strategy_key=no.strategy_key,
        condition_id=no.condition_id,
        token_id=yes.token_id,
        reason=no.reason,
        priority=no.priority,
        requested_at_utc=no.requested_at_utc,
        expires_at_utc=no.expires_at_utc,
        desired_transport=no.desired_transport,
        metadata=dict(no.metadata),
    )
    with pytest.raises(DemandOutboxLegError):
        append_owner_demand_bundle(
            tmp_path,
            bundle=type(good)(good.alpha_demand, (yes, same_token)),
            now=good.alpha_demand.requested_at,
        )
    # wrong consumer / strategy key
    impostor = CaptureDemand.create(
        consumer_id="some_other_consumer",
        strategy_key=no.strategy_key,
        condition_id=no.condition_id,
        token_id=no.token_id,
        reason=no.reason,
        priority=no.priority,
        requested_at_utc=no.requested_at_utc,
        expires_at_utc=no.expires_at_utc,
        desired_transport=no.desired_transport,
        metadata=dict(no.metadata),
    )
    with pytest.raises(DemandOutboxLegError):
        append_owner_demand_bundle(
            tmp_path,
            bundle=type(good)(good.alpha_demand, (yes, impostor)),
            now=good.alpha_demand.requested_at,
        )
    assert not _outbox_path(tmp_path).exists()


def test_expired_demand_is_rejected_before_writes(tmp_path: Path) -> None:
    bundle = _bundle()
    with pytest.raises(DemandOutboxBudgetError, match="expired"):
        _append(tmp_path, bundle, now=bundle.alpha_demand.valid_until + timedelta(seconds=1))
    assert not _outbox_path(tmp_path).exists()


def test_per_minute_budget_of_five_bundles(tmp_path: Path) -> None:
    for index in range(5):
        _append(tmp_path, _bundle(requested_offset=timedelta(seconds=index)))
    sixth = _bundle(requested_offset=timedelta(seconds=5))
    with pytest.raises(DemandOutboxBudgetError, match="per-minute"):
        _append(tmp_path, sixth)
    assert len(_outbox_path(tmp_path).read_text().splitlines()) == 5


def test_unexpired_demand_budget_of_five_bundles(tmp_path: Path) -> None:
    for index in range(5):
        _append(tmp_path, _bundle(requested_offset=timedelta(seconds=70 * index)))
    sixth = _bundle(requested_offset=timedelta(seconds=70 * 5))
    with pytest.raises(DemandOutboxBudgetError, match="unexpired"):
        _append(tmp_path, sixth)
    assert len(_outbox_path(tmp_path).read_text().splitlines()) == 5


def test_expired_history_does_not_count_toward_standing_budget(tmp_path: Path) -> None:
    # five short-lived bundles, all already expired at the new submission clock
    for index in range(5):
        _append(tmp_path, _bundle(requested_offset=timedelta(hours=index), valid_minutes=1))
    fresh = _bundle(requested_offset=timedelta(hours=6))
    receipt = _append(tmp_path, fresh)
    assert receipt.line_count == 6


def test_symlinked_root_and_directories_are_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real-root"
    real.mkdir()
    link = tmp_path / "link-root"
    link.symlink_to(real)
    with pytest.raises(DemandOutboxPathError):
        _append(link, _bundle())

    good_root = tmp_path / "root2"
    good_root.mkdir()
    _append(good_root, _bundle())
    fresh_root = tmp_path / "root3"
    fresh_root.mkdir()
    (fresh_root / "polymarket_alpha").symlink_to(good_root / "polymarket_alpha")
    with pytest.raises(DemandOutboxPathError):
        _append(fresh_root, _bundle())

    root4 = tmp_path / "root4"
    root4.mkdir()
    (root4 / "polymarket_alpha").mkdir()
    (root4 / "polymarket_alpha" / "capture_demands.jsonl").symlink_to(
        good_root / "polymarket_alpha" / "capture_demands.jsonl"
    )
    with pytest.raises(DemandOutboxPathError):
        _append(root4, _bundle())


def test_corrupt_outbox_lines_fail_closed(tmp_path: Path) -> None:
    root = tmp_path
    _append(root, _bundle())
    target = _outbox_path(root)
    original = target.read_text()
    target.write_text(original + "{broken-json\n")
    with pytest.raises(DemandOutboxCorruptError):
        _append(root, _bundle(requested_offset=timedelta(seconds=61)))
    target.write_text(original + '{"bundle_id": "x"}')  # no trailing newline
    with pytest.raises(DemandOutboxCorruptError):
        _append(root, _bundle(requested_offset=timedelta(seconds=62)))


def test_partial_write_leaves_a_torn_line_and_next_append_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path
    bundle = _bundle()
    real_write = os.write

    def half_write(fd: int, data: bytes) -> int:
        return real_write(fd, data[: len(data) // 2])

    monkeypatch.setattr(os, "write", half_write)
    with pytest.raises(DemandOutboxWriteError):
        _append(root, bundle)
    monkeypatch.undo()
    content = _outbox_path(root).read_text()
    # the torn half-line really is on disk (crash semantics); the writer must
    # have refused to report success, and the next append must fail closed
    assert content and not content.endswith("\n")
    with pytest.raises(DemandOutboxCorruptError):
        _append(root, _bundle(requested_offset=timedelta(seconds=61)))


def test_fsync_failure_after_full_write_recovers_as_replay(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    bundle = _bundle()

    def broken_fsync(_fd: int) -> None:
        raise OSError("disk detached")

    monkeypatch.setattr(os, "fsync", broken_fsync)
    with pytest.raises(DemandOutboxWriteError):
        _append(root, bundle)
    monkeypatch.undo()
    receipt = _append(root, bundle)
    assert receipt.replayed
    assert receipt.line_count == 1


def test_concurrent_appends_serialize_under_the_file_lock(tmp_path: Path) -> None:
    root = tmp_path
    bundles = [_bundle(requested_offset=timedelta(seconds=index)) for index in range(3)]
    results: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(bundle) -> None:
        try:
            receipt = _append(root, bundle)
            with lock:
                results.append(receipt)
        except BaseException as error:  # noqa: BLE001 - collected for assertion
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=worker, args=(bundle,)) for bundle in bundles]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(results) == 3
    lines = _outbox_path(root).read_text().splitlines()
    assert len(lines) == 3
    ids = [json.loads(line)["bundle_id"] for line in lines]
    assert len(set(ids)) == 3


def test_callers_cannot_select_the_outbox_path(tmp_path: Path) -> None:
    bundle = _bundle()
    receipt = _append(tmp_path, bundle)
    assert receipt.outbox_locator == "polymarket_alpha/capture_demands.jsonl"
    assert (tmp_path / "polymarket_alpha" / "capture_demands.jsonl").is_file()
    # the fixed locator is a module constant; there is no path parameter at all
    signature = inspect.signature(append_owner_demand_bundle)
    assert set(signature.parameters) == {"artifact_root", "bundle", "now"}
