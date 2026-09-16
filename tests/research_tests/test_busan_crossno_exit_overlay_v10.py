from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts/analysis/reheat_risk/busan_crossno_exit_overlay.py"
)
SPEC = importlib.util.spec_from_file_location("busan_exit_v10", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_month_boundary_metar_clock() -> None:
    timestamp = MODULE.report_ts(
        "METAR RKPK 312300Z 03006KT 9999=", "2026-08-01"
    )
    assert timestamp.isoformat() == "2026-07-31T23:00:00+00:00"


def test_exact_book_does_not_use_terminal_bracket() -> None:
    row = {
        "market_capture": {
            "books": [
                {"outcome": "no", "bracket": "35", "question": "35 or below"},
                {"outcome": "no", "bracket": "35", "question": "exact 35"},
            ]
        }
    }
    assert MODULE.exact_no_book(row, 35)["question"] == "exact 35"


def test_sell_vwap_requires_full_depth_and_deducts_fee() -> None:
    book = {
        "summary": {
            "bids": [
                {"price": 0.23, "size": 2},
                {"price": 0.22, "size": 3},
            ]
        }
    }
    result = MODULE.sell_vwap(book, 5)
    assert result is not None
    assert result["sell_vwap"] == (2 * 0.23 + 3 * 0.22) / 5
    assert result["sell_net_usd"] < result["sell_gross_usd"]
    assert MODULE.sell_vwap({"summary": {"bids": [{"price": 0.2, "size": 4}]}}, 5) is None
