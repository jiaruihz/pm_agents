from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

from scripts.analysis.market_structure_edge import (
    research_full_ladder_first_seen_residual_v1 as residual_runner,
)
from weather_model_evaluation import source_event_ws_linkage as linkage


def _ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def test_frame_paths_follow_utc_capture_shards_across_business_date_boundary(
    tmp_path: Path,
) -> None:
    first = tmp_path / "2026-08-01" / "frames.jsonl"
    second = tmp_path / "2026-08-02" / "frames.jsonl.gz"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("", encoding="utf-8")
    second.write_bytes(b"")

    paths = linkage._physical_frame_paths(
        tmp_path,
        _ts("2026-08-01T23:59:30Z"),
        _ts("2026-08-02T00:00:30Z"),
    )

    assert paths == [first, second]


def test_markout_requires_transport_evidence_through_horizon() -> None:
    frames = {"epoch-a": [_ts("2026-08-01T10:00:05Z"), _ts("2026-08-01T10:00:40Z")]}

    assert linkage._transport_covers_horizon(
        epoch_id="epoch-a",
        horizon_ts=_ts("2026-08-01T10:00:30Z"),
        next_epoch_started=None,
        epoch_frame_times=frames,
    )
    assert not linkage._transport_covers_horizon(
        epoch_id="epoch-a",
        horizon_ts=_ts("2026-08-01T10:01:00Z"),
        next_epoch_started=None,
        epoch_frame_times=frames,
    )


def test_late_frame_does_not_retroactively_prove_transport_continuity() -> None:
    frames = {"epoch-a": [_ts("2026-08-01T11:00:00Z")]}

    assert not linkage._transport_covers_horizon(
        epoch_id="epoch-a",
        horizon_ts=_ts("2026-08-01T10:01:00Z"),
        next_epoch_started=None,
        epoch_frame_times=frames,
    )


def test_markout_stops_at_subscription_epoch_change() -> None:
    frames = {"epoch-a": [_ts("2026-08-01T10:01:10Z")]}

    assert not linkage._transport_covers_horizon(
        epoch_id="epoch-a",
        horizon_ts=_ts("2026-08-01T10:01:00Z"),
        next_epoch_started=_ts("2026-08-01T10:00:45Z"),
        epoch_frame_times=frames,
    )


def test_invalid_or_naive_timestamp_is_not_treated_as_utc() -> None:
    assert linkage._timestamp("not-a-timestamp") is None
    assert linkage._timestamp("2026-08-01T10:00:00") is None
    assert linkage._timestamp("2026-08-01T10:00:00Z") == datetime(
        2026, 8, 1, 10, tzinfo=timezone.utc
    ).timestamp()


def test_existing_first_seen_runner_dispatches_ws_linkage_mode(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    captured = {}
    monkeypatch.setattr(
        residual_runner.source_event_ws_linkage,
        "run",
        lambda args: captured.update(vars(args)) or {"status": "ok"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "research_full_ladder_first_seen_residual_v1.py",
            "--mode",
            "helsinki-ws-linkage",
            "--ws-target-date",
            "2026-08-01",
            "--ws-output-dir",
            str(tmp_path),
        ],
    )

    assert residual_runner.main() == 0
    assert captured["target_date"] == "2026-08-01"
    assert captured["output_dir"] == tmp_path
    assert captured["entrypoint_path"] == Path(residual_runner.__file__)
    assert '"status": "ok"' in capsys.readouterr().out
