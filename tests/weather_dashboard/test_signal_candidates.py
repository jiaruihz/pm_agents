"""Tests for build_weather_signal_candidates (opportunity-grain table)."""

import json
import sqlite3

import pytest

from scripts.etl.build_weather_fact_trades import FACT_DDL
from scripts.etl.build_weather_signal_candidates import (
    build,
    write_db,
    _counterfactual_pnl,
)


# The builder reads the CANONICAL settlements shape (condition_id / final_price /
# settlement_status), matching runtime/weather.db. The legacy conftest fixture
# uses the old schema, so this test stands up its own minimal canonical DB.
@pytest.fixture
def canon_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(FACT_DDL)
    conn.execute(
        "CREATE TABLE settlements ("
        "settlement_id TEXT PRIMARY KEY, target_date TEXT, condition_id TEXT, "
        "market_id TEXT, bracket TEXT, token_id TEXT, final_price REAL, "
        "settlement_status TEXT, created_at_utc TEXT)"
    )
    conn.commit()
    yield conn
    conn.close()


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_snapshot(snap_dir, fname, ts_utc, records):
    snap_dir.mkdir(parents=True, exist_ok=True)
    payload = {"ts_utc": ts_utc, "records": records}
    (snap_dir / fname).write_text(json.dumps(payload))


def _rec(**kw):
    base = {
        "condition_id": "0xCID1",
        "side": "BUY_NO",
        "event_date": "2026-05-09",
        "bracket": "23",
        "city": "Tokyo",
        "city_pool": "t1_trading",
        "icao": "RJTT",
        "unit": "C",
        "forecast_source": "open_meteo_live_ecmwf",
        "model": "ecmwf",
        "time_bucket": "t24",
        "window": "pre_t24",
        "market_id": "M1",
        "model_prob": 0.65,
        "market_yes_price": 0.55,
        "edge": 0.10,
        "abs_edge": 0.10,
        "entry_price": 0.45,
        "shares": 10,
        "hours_to_settle": 23.0,
        "eligible_for_paper_order": True,
    }
    base.update(kw)
    return base


def _seed_settlement(conn, target_date, condition_id, bracket, final_price):
    conn.execute(
        "INSERT INTO settlements (settlement_id, target_date, condition_id, bracket, "
        "final_price, settlement_status, created_at_utc) VALUES (?,?,?,?,?,?,?)",
        (f"set_{condition_id}_{bracket}", target_date, condition_id, bracket,
         final_price, "settled", "2026-05-10T00:00:00Z"),
    )
    conn.commit()


def _seed_live_fill(conn, condition_id, side, target_date, fill_id, fill_price, qty, pnl):
    conn.execute(
        "INSERT INTO fact_trades (fill_id, condition_id, side, target_date, trade_class, "
        "fill_price, fill_qty, pnl_usd_at_fill) VALUES (?,?,?,?,?,?,?,?)",
        (fill_id, condition_id, side, target_date, "live_real", fill_price, qty, pnl),
    )
    conn.commit()


# ── counterfactual PnL formula ────────────────────────────────────────────────

def test_counterfactual_pnl_buy_no_win():
    # BUY_NO, final_yes=0 (NO won), entry 0.45, 10 shares → (1-0 - 0.45)*10 = 5.5
    assert _counterfactual_pnl("BUY_NO", 0.45, 0.0, 10) == pytest.approx(5.5)


def test_counterfactual_pnl_buy_yes_loss():
    # BUY_YES, final_yes=0 (YES lost), entry 0.55, 10 → (0-0.55)*10 = -5.5
    assert _counterfactual_pnl("BUY_YES", 0.55, 0.0, 10) == pytest.approx(-5.5)


def test_counterfactual_pnl_unsettled_is_none():
    assert _counterfactual_pnl("BUY_NO", 0.45, None, 10) is None


# ── decision-window selection ─────────────────────────────────────────────────

def test_decision_window_picks_closest_to_target(tmp_path, canon_db):
    snap = tmp_path / "snaps"
    # three snapshots: hts 26 (out of band), 23.5 (in band), 22.1 (in band, target=23)
    _write_snapshot(snap, "s1.json", "2026-05-08T00:00:00Z",
                    [_rec(hours_to_settle=26.0, entry_price=0.50)])
    _write_snapshot(snap, "s2.json", "2026-05-08T02:00:00Z",
                    [_rec(hours_to_settle=23.5, entry_price=0.45)])
    _write_snapshot(snap, "s3.json", "2026-05-08T03:00:00Z",
                    [_rec(hours_to_settle=22.1, entry_price=0.40)])

    rows, alerts, stats = build(canon_db, snapshot_dir=snap,
                                paper_orders_path=tmp_path / "none.jsonl",
                                hts_min=22.0, hts_max=24.0)
    assert len(rows) == 1
    r = rows[0]
    # target = 23.0; closest in-band is 23.5 (dist 0.5) vs 22.1 (dist 0.9)
    assert r["decision_hours_to_settle"] == 23.5
    assert r["decision_entry_price"] == 0.45
    assert r["decision_window_missing"] == 0
    # best_entry_price = cheapest across ALL snapshots = 0.40
    assert r["best_entry_price"] == 0.40
    assert r["n_snapshots"] == 3


def test_decision_window_preserves_forecast_peak_fields(tmp_path, canon_db):
    snap = tmp_path / "snaps"
    _write_snapshot(snap, "s1.json", "2026-05-08T02:00:00Z", [
        _rec(
            hours_to_settle=23.0,
            forecast_max_f=86.0,
            forecast_max_native=30.0,
            forecast_peak_hour_local=14,
            forecast_peak_time_local="2026-05-09T14:00",
            forecast_peak_hour_utc=5,
            forecast_peak_time_utc="2026-05-09T05:00:00Z",
            forecast_hourly_count=24,
            forecast_values_hash="abc123def4567890",
            forecast_peak_source="open_meteo_live_ecmwf",
            forecast_timezone="Asia/Tokyo",
            forecast_utc_offset_seconds=32400,
            forecast_peak_delta_hours_local=-1.0,
            forecast_max_in_bracket=1,
            forecast_max_above_bracket_f=0.0,
            forecast_max_below_bracket_f=0.0,
            forecast_max_above_metar_max_f=1.8,
        )
    ])

    rows, _, _ = build(canon_db, snapshot_dir=snap,
                       paper_orders_path=tmp_path / "none.jsonl",
                       hts_min=22.0, hts_max=24.0)

    assert len(rows) == 1
    r = rows[0]
    assert r["forecast_max_f"] == pytest.approx(86.0)
    assert r["forecast_max_native"] == pytest.approx(30.0)
    assert r["forecast_peak_hour_local"] == 14
    assert r["forecast_peak_time_local"] == "2026-05-09T14:00"
    assert r["forecast_peak_hour_utc"] == 5
    assert r["forecast_peak_time_utc"] == "2026-05-09T05:00:00Z"
    assert r["forecast_hourly_count"] == 24
    assert r["forecast_values_hash"] == "abc123def4567890"
    assert r["forecast_peak_source"] == "open_meteo_live_ecmwf"
    assert r["forecast_timezone"] == "Asia/Tokyo"
    assert r["forecast_utc_offset_seconds"] == 32400
    assert r["forecast_peak_delta_hours_local"] == pytest.approx(-1.0)
    assert r["forecast_max_in_bracket"] == 1
    assert r["forecast_max_above_bracket_f"] == pytest.approx(0.0)
    assert r["forecast_max_below_bracket_f"] == pytest.approx(0.0)
    assert r["forecast_max_above_metar_max_f"] == pytest.approx(1.8)


def test_decision_window_missing_when_no_snapshot_in_band(tmp_path, canon_db):
    snap = tmp_path / "snaps"
    _write_snapshot(snap, "s1.json", "2026-05-08T00:00:00Z",
                    [_rec(hours_to_settle=11.5, entry_price=0.50)])
    rows, _, _ = build(canon_db, snapshot_dir=snap,
                       paper_orders_path=tmp_path / "none.jsonl",
                       hts_min=22.0, hts_max=24.0)
    assert len(rows) == 1
    r = rows[0]
    assert r["decision_window_missing"] == 1
    assert r["decision_entry_price"] is None
    assert r["decision_hours_to_settle"] is None
    # opportunity still present; aggregate still computed
    assert r["n_snapshots"] == 1
    assert r["counterfactual_pnl"] is None  # no decision entry price


def test_missing_condition_id_dropped(tmp_path, canon_db):
    snap = tmp_path / "snaps"
    _write_snapshot(snap, "s1.json", "2026-05-08T00:00:00Z",
                    [_rec(), _rec(condition_id=None)])
    rows, _, stats = build(canon_db, snapshot_dir=snap,
                           paper_orders_path=tmp_path / "none.jsonl")
    assert len(rows) == 1
    assert stats["n_dropped_no_cid"] == 1


# ── link flags + counterfactual / settlement join ─────────────────────────────

def test_full_linkage(tmp_path, canon_db):
    conn = canon_db
    snap = tmp_path / "snaps"
    _write_snapshot(snap, "s1.json", "2026-05-08T02:00:00Z",
                    [_rec(hours_to_settle=23.0, entry_price=0.45)])

    # paper order for the same opportunity
    po = tmp_path / "paper_orders.jsonl"
    po.write_text(json.dumps({
        "condition_id": "0xCID1", "side": "BUY_NO", "event_date": "2026-05-09",
        "order_id": "0xCID1|BUY_NO", "entry_price": 0.44, "shares": 10,
        "snapshot_ts_utc": "2026-05-08T02:00:00Z",
    }) + "\n")

    # live fill at a worse price → positive slippage
    _seed_live_fill(conn, "0xCID1", "BUY_NO", "2026-05-09", "fill_1", 0.47, 10, 5.3)
    # settlement: NO won (final_yes=0)
    _seed_settlement(conn, "2026-05-09", "0xCID1", "23", 0.0)

    rows, alerts, _ = build(conn, snapshot_dir=snap, paper_orders_path=po)
    assert len(rows) == 1
    r = rows[0]
    assert r["seen"] == 1
    assert r["eligible"] == 1
    assert r["paper_ordered"] == 1
    assert r["live_filled"] == 1
    assert r["fill_id"] == "fill_1"
    assert r["live_pnl_usd"] == pytest.approx(5.3)
    assert r["slippage_vs_paper"] == pytest.approx(0.47 - 0.44)
    assert r["final_yes"] == 0.0
    assert r["bracket_hit"] == 0          # YES did not resolve 1
    assert r["win_by_count"] == 1         # BUY_NO won
    # counterfactual uses decision_entry_price (0.45): (1-0-0.45)*10 = 5.5
    assert r["counterfactual_pnl"] == pytest.approx(5.5)
    assert alerts == []


def test_multiple_orders_and_fills_are_aggregated(tmp_path, canon_db):
    conn = canon_db
    snap = tmp_path / "snaps"
    _write_snapshot(snap, "s1.json", "2026-05-08T02:00:00Z",
                    [_rec(hours_to_settle=23.0, entry_price=0.45)])

    po = tmp_path / "paper_orders.jsonl"
    po.write_text(
        json.dumps({
            "condition_id": "0xCID1", "side": "BUY_NO", "event_date": "2026-05-09",
            "order_id": "paper_1", "entry_price": 0.40, "shares": 10,
            "snapshot_ts_utc": "2026-05-08T02:00:00Z",
        }) + "\n" +
        json.dumps({
            "condition_id": "0xCID1", "side": "BUY_NO", "event_date": "2026-05-09",
            "order_id": "paper_2", "entry_price": 0.50, "shares": 5,
            "snapshot_ts_utc": "2026-05-08T02:30:00Z",
        }) + "\n"
    )

    _seed_live_fill(conn, "0xCID1", "BUY_NO", "2026-05-09", "fill_1", 0.44, 10, 5.6)
    _seed_live_fill(conn, "0xCID1", "BUY_NO", "2026-05-09", "fill_2", 0.47, 5, 2.65)
    _seed_settlement(conn, "2026-05-09", "0xCID1", "23", 0.0)

    rows, alerts, stats = build(conn, snapshot_dir=snap, paper_orders_path=po)

    assert alerts == []
    assert stats["n_paper_order_rows"] == 2
    assert stats["n_paper_orders"] == 1
    assert stats["n_paper_order_duplicate_rows"] == 1
    assert stats["n_live_fill_rows"] == 2
    assert stats["n_live_fills"] == 1
    assert stats["n_live_fill_duplicate_rows"] == 1

    r = rows[0]
    assert r["paper_order_id"] == "paper_1,paper_2"
    assert r["paper_shares"] == pytest.approx(15)
    assert r["paper_entry_price"] == pytest.approx((0.40 * 10 + 0.50 * 5) / 15)
    assert r["paper_snapshot_ts_utc"] == "2026-05-08T02:30:00Z"
    assert r["fill_id"] == "fill_1,fill_2"
    assert r["live_fill_qty"] == pytest.approx(15)
    assert r["live_fill_price"] == pytest.approx((0.44 * 10 + 0.47 * 5) / 15)
    assert r["live_pnl_usd"] == pytest.approx(8.25)
    assert r["slippage_vs_paper"] == pytest.approx(
        ((0.44 * 10 + 0.47 * 5) / 15) - ((0.40 * 10 + 0.50 * 5) / 15)
    )


def test_missed_fill_and_counterfactual_win(tmp_path, canon_db):
    conn = canon_db
    snap = tmp_path / "snaps"
    # opportunity A: paper ordered, NOT live filled → missed_fill
    # opportunity B: never ordered, but settles a winner → counterfactual_win
    _write_snapshot(snap, "s1.json", "2026-05-08T02:00:00Z", [
        _rec(condition_id="0xA", hours_to_settle=23.0, entry_price=0.45, bracket="23"),
        _rec(condition_id="0xB", side="BUY_NO", hours_to_settle=23.0,
             entry_price=0.30, bracket="24"),
    ])
    po = tmp_path / "paper_orders.jsonl"
    po.write_text(json.dumps({
        "condition_id": "0xA", "side": "BUY_NO", "event_date": "2026-05-09",
        "order_id": "0xA|BUY_NO", "entry_price": 0.45, "shares": 10,
    }) + "\n")
    _seed_settlement(conn, "2026-05-09", "0xA", "23", 0.0)
    _seed_settlement(conn, "2026-05-09", "0xB", "24", 0.0)

    rows, _, _ = build(conn, snapshot_dir=snap, paper_orders_path=po)
    by_cid = {r["condition_id"]: r for r in rows}

    a = by_cid["0xA"]
    assert a["paper_ordered"] == 1 and a["live_filled"] == 0  # missed fill

    b = by_cid["0xB"]
    assert b["paper_ordered"] == 0
    assert b["win_by_count"] == 1                 # NO won, never ordered
    assert b["counterfactual_pnl"] == pytest.approx((1 - 0 - 0.30) * 10)


def test_write_db_roundtrip(tmp_path, canon_db):
    conn = canon_db
    snap = tmp_path / "snaps"
    _write_snapshot(snap, "s1.json", "2026-05-08T02:00:00Z",
                    [_rec(hours_to_settle=23.0)])
    rows, _, _ = build(conn, snapshot_dir=snap, paper_orders_path=tmp_path / "none.jsonl")
    write_db(conn, rows)
    n = conn.execute("SELECT count(*) FROM fact_signal_candidates").fetchone()[0]
    assert n == 1
    cid = conn.execute("SELECT candidate_id FROM fact_signal_candidates").fetchone()[0]
    assert cid == "0xCID1|BUY_NO|2026-05-09"
