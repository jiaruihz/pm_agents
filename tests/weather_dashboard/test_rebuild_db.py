import pytest
from weather_dashboard.ingest.rebuild_db_from_raw import rebuild_db

CSV_CONTENT = """\
snapshot_file,snapshot_ts_utc,city,bracket,side,model,model_prob,market_yes_price,edge,abs_edge,event_date,shares,cost_usd,entry_price,mode,order_id,created_at_utc,settlement_status
snap1.csv,2026-05-09T10:00:00Z,Tokyo,23,BUY_YES,ecmwf,0.65,0.55,0.10,0.10,2026-05-09,100,55.00,0.55,paper,ord1,2026-05-09T10:01:00Z,settled
snap2.csv,2026-05-09T11:00:00Z,Warsaw,25,BUY_NO,gfs,0.45,0.60,-0.05,0.05,2026-05-09,50,30.00,0.40,paper,ord2,2026-05-09T11:01:00Z,settled
"""


def _write_csv(tmp_path, name="ledger.csv"):
    p = tmp_path / name
    p.write_text(CSV_CONTENT, encoding="utf-8")
    return p


def test_rebuild_db_empty_dir(tmp_path):
    csv_dir = tmp_path / "empty"
    csv_dir.mkdir()
    db_path = str(tmp_path / "test.db")

    result = rebuild_db(str(csv_dir), db_path, run_id="r1", config_id="c1")
    assert result == {"files_processed": 0, "rows_inserted": 0}


def test_rebuild_db_single_csv(tmp_path):
    csv_dir = tmp_path / "data"
    csv_dir.mkdir()
    _write_csv(csv_dir)
    db_path = str(tmp_path / "test.db")

    result = rebuild_db(str(csv_dir), db_path, run_id="r1", config_id="c1")

    assert result["files_processed"] == 1
    # 2 signals + 2 fills inserted from ledger_csv (4), plus settlement rows
    # settlements are idempotent via UNIQUE but counted in rows_inserted too
    assert result["rows_inserted"] > 0

    from weather_dashboard.db.connection import get_conn
    conn = get_conn(db_path)
    signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    conn.close()

    assert signals == 2
    assert fills == 2


def test_rebuild_db_idempotent(tmp_path):
    csv_dir = tmp_path / "data"
    csv_dir.mkdir()
    _write_csv(csv_dir)
    db_path = str(tmp_path / "test.db")

    rebuild_db(str(csv_dir), db_path, run_id="r1", config_id="c1")
    rebuild_db(str(csv_dir), db_path, run_id="r1", config_id="c1")

    from weather_dashboard.db.connection import get_conn
    conn = get_conn(db_path)
    signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    conn.close()

    assert signals == 2  # Still 2, not 4


def test_rebuild_db_dry_run(tmp_path):
    csv_dir = tmp_path / "data"
    csv_dir.mkdir()
    _write_csv(csv_dir)
    db_path = str(tmp_path / "test.db")

    result = rebuild_db(str(csv_dir), db_path, run_id="r1", config_id="c1", dry_run=True)

    assert result["files_processed"] == 1
    assert result["rows_inserted"] == 0
    # DB should not have been created
    import os
    assert not os.path.exists(db_path)
