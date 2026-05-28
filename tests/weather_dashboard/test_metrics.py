"""Tests for weather_dashboard.metrics.calc — reads from fact_trades."""

import sqlite3
import pytest
from weather_dashboard.metrics.calc import compute_metrics

FACT_DDL = """
CREATE TABLE IF NOT EXISTS fact_trades (
  fill_id TEXT, run_id TEXT, side TEXT,
  fill_price REAL, fill_qty REAL, fees_usd REAL, cost_usd REAL,
  pnl_usd_at_fill REAL, settlement_status TEXT, win_by_count INTEGER
)
"""


def _mk_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(FACT_DDL)
    return conn


def _insert(conn, run_id: str, side: str, fill_price: float, qty: float,
            final_yes=None, fees: float = 0.0):
    cost = fill_price * qty
    if final_yes is not None:
        if side == "BUY_YES":
            pnl = (final_yes - fill_price) * qty - fees
        else:
            pnl = ((1.0 - final_yes) - fill_price) * qty - fees
        status = "settled"
        win = int(pnl > 0)
    else:
        pnl = None
        status = "unsettled"
        win = None
    conn.execute(
        "INSERT INTO fact_trades VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f"f_{side}_{fill_price}", run_id, side, fill_price, qty, fees, cost, pnl, status, win),
    )
    conn.commit()


def test_compute_metrics_no_trades():
    conn = _mk_db()
    m = compute_metrics(conn, "run1")
    assert m["num_trades"] == 0
    assert m["total_pnl_usd"] is None


def test_compute_metrics_settled_win():
    conn = _mk_db()
    # Buy YES at 0.5, 100 shares → pnl +50
    _insert(conn, "r1", "BUY_YES", 0.5, 100, final_yes=1)
    m = compute_metrics(conn, "r1")
    assert m["num_trades"] == 1
    assert m["total_pnl_usd"] == 50.0
    assert m["win_rate"] == 1.0
    assert m["settled_trades"] == 1
    assert m["unsettled_trades"] == 0


def test_compute_metrics_buy_no_win():
    conn = _mk_db()
    # Buy NO at 0.4, 50 shares → pnl = (1-0-0.4)*50 = 30
    _insert(conn, "r1", "BUY_NO", 0.4, 50, final_yes=0)
    m = compute_metrics(conn, "r1")
    assert m["total_pnl_usd"] == 30.0
    assert m["win_rate"] == 1.0


def test_compute_metrics_with_fees():
    conn = _mk_db()
    # Buy YES at 0.5, 100 shares, fee 1 → pnl = 50 - 1 = 49
    _insert(conn, "r1", "BUY_YES", 0.5, 100, final_yes=1, fees=1.0)
    m = compute_metrics(conn, "r1")
    assert m["total_pnl_usd"] == 49.0
    assert m["fees_paid_usd"] == 1.0


def test_compute_metrics_mixed():
    conn = _mk_db()
    _insert(conn, "r1", "BUY_YES", 0.5, 100, final_yes=1)   # +50
    _insert(conn, "r1", "BUY_NO", 0.4, 50, final_yes=1)     # -20
    m = compute_metrics(conn, "r1")
    assert m["num_trades"] == 2
    assert m["total_pnl_usd"] == 30.0
    assert m["win_rate"] == 0.5


def test_compute_metrics_unsettled():
    conn = _mk_db()
    _insert(conn, "r1", "BUY_YES", 0.5, 100, final_yes=None)
    m = compute_metrics(conn, "r1")
    assert m["unsettled_trades"] == 1
    assert m["total_pnl_usd"] is None


def test_compute_metrics_roi():
    conn = _mk_db()
    _insert(conn, "r1", "BUY_YES", 0.5, 100, final_yes=1)  # cost=50, pnl=50
    m = compute_metrics(conn, "r1")
    assert m["roi"] == pytest.approx(1.0)


def test_compute_metrics_expectancy():
    conn = _mk_db()
    _insert(conn, "r1", "BUY_YES", 0.5, 100, final_yes=1)   # +50 win
    _insert(conn, "r1", "BUY_NO",  0.4, 50,  final_yes=1)   # -20 loss
    m = compute_metrics(conn, "r1")
    # avg_win=50, avg_loss=-20, win_rate=0.5, loss_rate=0.5
    assert m["expectancy_usd"] == pytest.approx(50*0.5 + (-20)*0.5)


def test_compute_metrics_max_drawdown():
    conn = _mk_db()
    _insert(conn, "r1", "BUY_YES", 0.5, 100, final_yes=1)   # +50
    _insert(conn, "r1", "BUY_NO",  0.4, 50,  final_yes=1)   # -20
    _insert(conn, "r1", "BUY_YES", 0.3, 100, final_yes=1)   # +70
    m = compute_metrics(conn, "r1")
    # cumulative: 50, 30, 100 → peak=50, trough=30, dd=20
    assert m["max_drawdown_usd"] == pytest.approx(20.0)
