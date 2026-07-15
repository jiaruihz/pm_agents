from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/ops/d1_yes_high_mid_shadow_v1.py"
SPEC = importlib.util.spec_from_file_location("d1_yes_high_mid_shadow_v1_test", MODULE_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def snapshot_file(root: Path, stamp: str, *, complete: bool, age_sec: float = 0.0) -> Path:
    path = root / "orderbook_snapshots" / stamp[:8] / f"orderbook_snapshot_{stamp}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    if complete:
        marker = root / "paper_snapshots" / f"snapshot_{stamp}.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}")
    ts = time.time() - age_sec
    os.utime(path, (ts, ts))
    return path


def test_full_ladder_ignores_newer_incomplete_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "full_ladder_output"
    completed = snapshot_file(root, "20260715_1502", complete=True, age_sec=60)
    snapshot_file(root, "20260715_1533", complete=False, age_sec=0)

    assert runner._latest_in_dir(root / "orderbook_snapshots", require_complete=True) == completed


def test_priority_prefers_completed_full_ladder_over_fresher_targeted(tmp_path: Path) -> None:
    full_root = tmp_path / "full_ladder_output"
    targeted_root = tmp_path / "targeted_output"
    completed = snapshot_file(full_root, "20260715_1502", complete=True, age_sec=60)
    snapshot_file(targeted_root, "20260715_1533", complete=False, age_sec=0)

    assert runner.latest_orderbook_file(
        [full_root / "orderbook_snapshots", targeted_root / "orderbook_snapshots"]
    ) == completed


def test_stale_full_ladder_falls_back_to_targeted(tmp_path: Path) -> None:
    full_root = tmp_path / "full_ladder_output"
    targeted_root = tmp_path / "targeted_output"
    snapshot_file(full_root, "20260715_1400", complete=True, age_sec=2500)
    targeted = snapshot_file(targeted_root, "20260715_1533", complete=False, age_sec=30)

    assert runner.latest_orderbook_file(
        [full_root / "orderbook_snapshots", targeted_root / "orderbook_snapshots"]
    ) == targeted


def test_all_stale_sources_fail_closed(tmp_path: Path) -> None:
    full_root = tmp_path / "full_ladder_output"
    targeted_root = tmp_path / "targeted_output"
    snapshot_file(full_root, "20260715_1400", complete=True, age_sec=2500)
    snapshot_file(targeted_root, "20260715_1401", complete=False, age_sec=2500)

    assert runner.latest_orderbook_file(
        [full_root / "orderbook_snapshots", targeted_root / "orderbook_snapshots"]
    ) is None


def test_coverage_requires_36_book_cities_for_target_dates(tmp_path: Path) -> None:
    full = snapshot_file(tmp_path / "full_ladder_output", "20260715_1502", complete=True)
    targeted = snapshot_file(tmp_path / "targeted_output", "20260715_1533", complete=False)

    assert runner.coverage_note(full, 35) == "full_ladder_partial"
    assert runner.coverage_note(full, 36) == "full_ladder"
    assert runner.coverage_note(targeted, 36) == "narrow_targeted_coverage"
