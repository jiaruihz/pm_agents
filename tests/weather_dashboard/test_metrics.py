"""Tests for weather_dashboard.metrics.calc"""

import pytest
from weather_dashboard.metrics.calc import compute_metrics, _trade_pnl
from decimal import Decimal


# ── unit tests for _trade_pnl ─────────────────────────────────────────────────

def test_trade_pnl_buy_yes_win():
    # Buy YES at 0.6, 100 shares → cost 60, payout 100, pnl 40
    pnl = _trade_pnl("100", "0.6", "0", final_yes=1, order_side="BUY_YES")
    assert pnl == Decimal("40")


def test_trade_pnl_buy_yes_lose():
    # Buy YES at 0.6, 100 shares → payout 0, pnl -60
    pnl = _trade_pnl("100", "0.6", "0", final_yes=0, order_side="BUY_YES")
    assert pnl == Decimal("-60")


def test_trade_pnl_buy_no_win():
    # Buy NO at 0.4, 50 shares → cost 20, payout 50, pnl 30
    pnl = _trade_pnl("50", "0.4", "0", final_yes=0, order_side="BUY_NO")
    assert pnl == Decimal("30")


def test_trade_pnl_buy_no_lose():
    # Buy NO at 0.4, 50 shares → payout 0, pnl -20
    pnl = _trade_pnl("50", "0.4", "0", final_yes=1, order_side="BUY_NO")
    assert pnl == Decimal("-20")


def test_trade_pnl_with_fees():
    # Buy YES at 0.5, 100 shares, fee 1 → pnl = 100 - 50 - 1 = 49
    pnl = _trade_pnl("100", "0.5", "1", final_yes=1, order_side="BUY_YES")
    assert pnl == Decimal("49")


def test_trade_pnl_unsettled_returns_none():
    pnl = _trade_pnl("100", "0.5", "0", final_yes=None, order_side="BUY_YES")
    assert pnl is None


# ── integration test: compute_metrics ────────────────────────────────────────

def _insert_full_trade(conn, run_id, config_id,
                       city, bracket, target_date,
                       order_side, shares, price, final_yes=None):
    """Helper: insert signal→plan→order→fill and optionally settlement."""
    from datetime import datetime, timezone
    import hashlib, uuid

    now = datetime.now(timezone.utc).isoformat()

    signal_side = "YES" if "YES" in order_side else "NO"
    signal_id = hashlib.sha256(f"{city}{bracket}{signal_side}".encode()).hexdigest()[:32]
    plan_id = hashlib.sha256(f"{signal_id}{run_id}".encode()).hexdigest()[:32]
    order_id = str(uuid.uuid4())
    fill_id = str(uuid.uuid4())

    conn.execute(
        "INSERT OR IGNORE INTO signals (signal_id, target_date, city, bracket, side, created_at_utc) "
        "VALUES (?,?,?,?,?,?)",
        (signal_id, target_date, city, bracket, signal_side, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO plans (plan_id, run_id, signal_id, config_id, created_at_utc) "
        "VALUES (?,?,?,?,?)",
        (plan_id, run_id, signal_id, config_id, now),
    )
    conn.execute(
        "INSERT INTO orders (order_id, run_id, plan_id, execution_mode, side, "
        "entry_price, shares, cost_usd, placed_at_utc, created_at_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (order_id, run_id, plan_id, "snapshot_replay", order_side,
         str(price), str(shares), str(float(shares) * float(price)), now, now),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, order_id, filled_shares, filled_price, status, filled_at_utc, created_at_utc) "
        "VALUES (?,?,?,?,?,?,?)",
        (fill_id, order_id, str(shares), str(price), "filled", now, now),
    )

    if final_yes is not None:
        import hashlib as h
        sid = h.sha256(f"{target_date}{bracket}".encode()).hexdigest()[:32]
        conn.execute(
            "INSERT OR IGNORE INTO settlements (settlement_id, target_date, bracket, final_yes, status, created_at_utc) "
            "VALUES (?,?,?,?,?,?)",
            (sid, target_date, bracket, final_yes, "settled", now),
        )

    conn.commit()
    return run_id


def _setup_run(conn):
    """Create minimal config, universe, code_version, run."""
    from weather_dashboard.cli.config_register import register_config
    from weather_dashboard.cli.universe_register import register_universe
    from weather_dashboard.cli.run_create import create_run

    register_config(conn, "cfg", {"min_edge": 0.08})
    from weather_dashboard.cli.config_register import _config_id
    cid = _config_id({"min_edge": 0.08})

    register_universe(conn, "u1", "Universe", "", ["Tokyo"], ["ecmwf"])
    run_id = create_run(conn, cid, "u1", "sha_test", "snapshot_replay")
    return run_id, cid


def test_compute_metrics_no_trades(tmp_db_with_schema):
    run_id, _ = _setup_run(tmp_db_with_schema)
    m = compute_metrics(tmp_db_with_schema, run_id)
    assert m["num_trades"] == 0
    assert m["total_pnl_usd"] is None


def test_compute_metrics_settled_win(tmp_db_with_schema):
    run_id, cid = _setup_run(tmp_db_with_schema)
    # Buy YES at 0.5, 100 shares → cost 50, payout 100, pnl +50
    _insert_full_trade(tmp_db_with_schema, run_id, cid,
                       "Tokyo", "23", "2026-05-09",
                       "BUY_YES", 100, 0.5, final_yes=1)

    m = compute_metrics(tmp_db_with_schema, run_id)
    assert m["num_trades"] == 1
    assert m["total_pnl_usd"] == 50.0
    assert m["win_rate"] == 1.0
    assert m["settled_trades"] == 1
    assert m["unsettled_trades"] == 0


def test_compute_metrics_mixed(tmp_db_with_schema):
    run_id, cid = _setup_run(tmp_db_with_schema)
    # Win: +50
    _insert_full_trade(tmp_db_with_schema, run_id, cid,
                       "Tokyo", "23", "2026-05-09", "BUY_YES", 100, 0.5, final_yes=1)
    # Loss: -20
    _insert_full_trade(tmp_db_with_schema, run_id, cid,
                       "Warsaw", "25", "2026-05-09", "BUY_NO", 50, 0.4, final_yes=1)

    m = compute_metrics(tmp_db_with_schema, run_id)
    assert m["num_trades"] == 2
    assert m["total_pnl_usd"] == 30.0  # 50 - 20
    assert m["win_rate"] == 0.5


def test_compute_metrics_unsettled(tmp_db_with_schema):
    run_id, cid = _setup_run(tmp_db_with_schema)
    _insert_full_trade(tmp_db_with_schema, run_id, cid,
                       "Tokyo", "23", "2026-05-09", "BUY_YES", 100, 0.5, final_yes=None)

    m = compute_metrics(tmp_db_with_schema, run_id)
    assert m["unsettled_trades"] == 1
    assert m["total_pnl_usd"] is None
