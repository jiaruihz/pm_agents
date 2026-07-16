from __future__ import annotations

import importlib.util
import json
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


def quote(token: str) -> dict[str, object]:
    return {"token_id": token, "ask": 0.5, "bid": 0.49}


def test_fahrenheit_current_is_anchored_before_selecting_d1() -> None:
    ladder = {
        "92-93": {"yes": quote("y92"), "no": quote("n92")},
        "94-95": {"yes": quote("y94"), "no": quote("n94")},
        "96-97": {"yes": quote("y96"), "no": quote("n96")},
    }

    current, d1, d1_no, d1_yes = runner.find_current_and_d1(ladder, 93.92, "F")

    assert current == "94-95"
    assert d1 == "96-97"
    assert d1_no == ladder["96-97"]["no"]
    assert d1_yes == ladder["96-97"]["yes"]


def test_missing_current_bracket_fails_closed() -> None:
    ladder = {"38": {"yes": quote("y38"), "no": quote("n38")}}

    assert runner.find_current_and_d1(ladder, 36.1, "C") == (None, None, None, None)


def test_missing_intermediate_bracket_does_not_turn_d2_into_d1() -> None:
    ladder = {
        "37": {"yes": quote("y37"), "no": quote("n37")},
        "39+": {"yes": quote("y39"), "no": quote("n39")},
    }

    assert runner.find_current_and_d1(ladder, 37.1, "C") == ("37", None, None, None)


def test_exact_celsius_current_selects_immediate_higher_bracket() -> None:
    ladder = {
        "37": {"yes": quote("y37"), "no": quote("n37")},
        "38": {"yes": quote("y38"), "no": quote("n38")},
    }

    current, d1, _, d1_yes = runner.find_current_and_d1(ladder, 37.1, "C")

    assert current == "37"
    assert d1 == "38"
    assert d1_yes == ladder["38"]["yes"]


def test_successful_live_orders_excludes_failed_submission(tmp_path: Path) -> None:
    path = tmp_path / "live_orders.jsonl"
    rows = [
        {"status": "submitted", "exchange_response": {"place": {"success": True, "orderID": "ok"}}},
        {"status": "submitted", "exchange_response": {"place": {"success": False, "orderID": "bad"}}},
        {"status": "failed", "exchange_response": {"place": {"success": True, "orderID": "no"}}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    order_ids = [
        row["exchange_response"]["place"]["orderID"]
        for row in runner.successful_live_orders(path)
    ]
    assert order_ids == ["ok"]


def test_apply_live_fill_basis_scales_pnl_to_actual_shares() -> None:
    position = {"execution_mode": "tiny_live_taker_5shares"}
    order = {
        "exchange_response": {
            "place": {
                "success": True,
                "orderID": "clob-1",
                "takingAmount": "5",
                "makingAmount": "4.2",
            }
        }
    }
    runner.apply_live_fill_basis(position, order)
    assert position["position_shares"] == 5.0
    assert position["fill_price"] == 0.84
    assert position["entry_cost_usd"] == 4.2
    assert position["entry_cost_with_fee"] == 4.2336
    assert round(5.0 - position["entry_cost_with_fee"], 6) == 0.7664
