from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts/ops/backfill_weather_execution_evidence.py"
    spec = importlib.util.spec_from_file_location("weather_execution_evidence_backfill", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BACKFILL = _load_module()
NOW = datetime(2026, 8, 30, 1, 0, tzinfo=timezone.utc)


def _epoch(epoch_id: str, token: str, *, previous: str | None = None) -> dict:
    return {
        "subscription_epoch_id": epoch_id,
        "previous_subscription_epoch_id": previous,
        "started_at_utc": NOW.isoformat().replace("+00:00", "Z"),
        "reason": "connect",
        "token_ids": [token],
        "producer_build_id": "test-build",
        "selector_version": "test-selector",
        "capture_policy": {"scope": "test"},
        "token_rows": {token: {"city": "Tokyo", "event_date": "2026-08-30"}},
        "capture_demands": [],
    }


def _frame(epoch_id: str, token: str, at: datetime, *, kind: str = "book") -> dict:
    if kind == "book":
        message = {
            "event_type": "book",
            "asset_id": token,
            "timestamp": str(int(at.timestamp() * 1000)),
            "bids": [{"price": "0.50", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
        }
    else:
        message = {
            "event_type": "price_change",
            "timestamp": str(int(at.timestamp() * 1000)),
            "price_changes": [{"asset_id": token, "side": "SELL", "price": "0.52", "size": "8"}],
        }
    return {
        "subscription_epoch_id": epoch_id,
        "received_at_utc": at.isoformat().replace("+00:00", "Z"),
        "received_at_ns": int(at.timestamp() * 1_000_000_000),
        "producer": "test",
        "producer_build_id": "test-build",
        "selector_version": "test-selector",
        "message": message,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _book_rows(root: Path) -> list[dict]:
    return [
        json.loads(line)
        for path in sorted((root / "public_books").glob("*/*.jsonl"))
        for line in path.read_text().splitlines()
    ]


def test_backfill_replays_epoch_switch_with_physical_raw_lineage(tmp_path) -> None:
    raw = tmp_path / "ws/2026-08-30/market_books_ws_a.jsonl"
    epochs = tmp_path / "epochs/subscription_epochs_2026-08-30.jsonl"
    _write_jsonl(epochs, [_epoch("epoch-1", "token-a"), _epoch("epoch-2", "token-b", previous="epoch-1")])
    _write_jsonl(
        raw,
        [
            _frame("epoch-1", "token-a", NOW),
            _frame("epoch-2", "token-b", NOW + timedelta(seconds=1)),
            _frame("epoch-2", "token-b", NOW + timedelta(seconds=12)),
        ],
    )

    report = BACKFILL.run_backfill(
        raw_paths=[raw], epoch_paths=[epochs], output_root=tmp_path / "evidence"
    )

    assert report["status"] == "complete"
    assert report["input_frames"] == 3
    assert report["activated_epoch_ids"] == ["epoch-1", "epoch-2"]
    assert report["recorder_health"]["applied_frames"] == 3
    rows = _book_rows(tmp_path / "evidence")
    assert {row["subscription_epoch_id"] for row in rows} == {"epoch-1", "epoch-2"}
    assert {row["baseline_raw_frame_ref"]["archive_path"] for row in rows} == {str(raw)}
    assert {row["baseline_raw_frame_ref"]["line_number"] for row in rows} == {1, 2, 3}


def test_backfill_input_budget_truncates_at_a_frame_boundary_deterministically(tmp_path) -> None:
    raw = tmp_path / "raw.jsonl"
    epochs = tmp_path / "epochs.jsonl"
    _write_jsonl(epochs, [_epoch("epoch-1", "token-a")])
    frames = [_frame("epoch-1", "token-a", NOW + timedelta(seconds=index)) for index in range(3)]
    _write_jsonl(raw, frames)
    first_line_bytes = len((json.dumps(frames[0], sort_keys=True) + "\n").encode())

    report = BACKFILL.run_backfill(
        raw_paths=[raw],
        epoch_paths=[epochs],
        output_root=tmp_path / "evidence",
        max_bytes=first_line_bytes,
    )

    assert report["status"] == "budget_truncated"
    assert report["input_frames"] == 1
    assert report["input_bytes"] == first_line_bytes
    assert report["activated_epoch_ids"] == ["epoch-1"]
    assert report["recorder_health"]["applied_frames"] == 1
    assert report["report_id"] == BACKFILL.hashlib.sha256(
        json.dumps(
            {key: value for key, value in report.items() if key != "report_id"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def test_backfill_refuses_duplicate_append_into_existing_output(tmp_path) -> None:
    raw = tmp_path / "raw.jsonl"
    epochs = tmp_path / "epochs.jsonl"
    output = tmp_path / "evidence"
    _write_jsonl(epochs, [_epoch("epoch-1", "token-a")])
    _write_jsonl(raw, [_frame("epoch-1", "token-a", NOW)])
    BACKFILL.run_backfill(
        raw_paths=[raw], epoch_paths=[epochs], output_root=output
    )

    with pytest.raises(BACKFILL.BackfillError, match="already contains"):
        BACKFILL.run_backfill(
            raw_paths=[raw], epoch_paths=[epochs], output_root=output
        )


def test_resolve_epoch_paths_loads_previous_journal_for_midnight_spanning_epoch(
    tmp_path,
) -> None:
    root = tmp_path / "epochs"
    previous = root / "subscription_epochs_2026-08-29.jsonl"
    selected = root / "subscription_epochs_2026-08-30.jsonl"
    _write_jsonl(previous, [_epoch("epoch-before-midnight", "token-a")])
    _write_jsonl(selected, [_epoch("epoch-after-midnight", "token-b")])

    paths = BACKFILL.resolve_epoch_paths(
        epoch_paths=[], epoch_root=root, dates=["2026-08-30"]
    )

    assert paths == [previous.resolve(), selected.resolve()]
