import hashlib
import json
import pytest
from weather_dashboard.ingest.common import row_hash, already_ingested, record_ingestion
from weather_dashboard.ingest.ledger_csv import ingest_ledger_csv
from weather_dashboard.ingest.settlements import ingest_settlement_rows

def test_row_hash_deterministic():
    row = {'a': 1, 'b': 'hello', 'c': 3.14}
    h1 = row_hash(row)
    h2 = row_hash(row)
    assert h1 == h2
    assert len(h1) == 64

def test_row_hash_different_dicts_different():
    row1 = {'a': 1, 'b': 'hello'}
    row2 = {'a': 1, 'b': 'world'}
    h1 = row_hash(row1)
    h2 = row_hash(row2)
    assert h1 != h2

def test_already_ingested_false_then_true(tmp_db_with_schema):
    source = '/path/to/some.csv'
    h = 'abc123def456'
    table = 'signals'

    # Initially not ingested
    assert already_ingested(tmp_db_with_schema, source, h, table) is False

    # Record ingestion
    record_ingestion(tmp_db_with_schema, source, h, table, 'sig1')

    # Now should be True
    assert already_ingested(tmp_db_with_schema, source, h, table) is True

def test_record_ingestion_idempotent(tmp_db_with_schema):
    source = '/path/to/some.csv'
    h = 'abc123def456'
    table = 'signals'
    target_id = 'sig1'

    # First call - no error
    record_ingestion(tmp_db_with_schema, source, h, table, target_id)

    # Second call - should not raise (idempotent via OR IGNORE)
    record_ingestion(tmp_db_with_schema, source, h, table, target_id)

def test_ingest_ledger_csv_inserts_signal_and_fill(tmp_db_with_schema):
    rows = [
        {
            'snapshot_file': 'snap1.csv',
            'snapshot_ts_utc': '2026-05-09T10:00:00Z',
            'city': 'Tokyo',
            'bracket': '23',
            'side': 'BUY_YES',
            'model': 'ecmwf',
            'model_prob': '0.65',
            'market_yes_price': '0.55',
            'edge': '0.10',
            'abs_edge': '0.10',
            'event_date': '2026-05-09',
            'shares': '100',
            'cost_usd': '55.00',
            'entry_price': '0.55',
            'mode': 'paper',
            'order_id': 'ord1',
            'created_at_utc': '2026-05-09T10:01:00Z',
            'settlement_status': 'settled',
        },
        {
            'snapshot_file': 'snap2.csv',
            'snapshot_ts_utc': '2026-05-09T11:00:00Z',
            'city': 'Warsaw',
            'bracket': '25',
            'side': 'BUY_NO',
            'model': 'gfs',
            'model_prob': '0.45',
            'market_yes_price': '0.60',
            'edge': '-0.05',
            'abs_edge': '0.05',
            'event_date': '2026-05-09',
            'shares': '50',
            'cost_usd': '30.00',
            'entry_price': '0.40',
            'mode': 'paper',
            'order_id': 'ord2',
            'created_at_utc': '2026-05-09T11:01:00Z',
            'settlement_status': 'settled',
        },
    ]

    count = ingest_ledger_csv(
        tmp_db_with_schema, rows,
        source_path='/path/to/ledger.csv',
        run_id='run1',
        config_id='cfg1',
    )

    signals = tmp_db_with_schema.execute("SELECT * FROM signals").fetchall()
    fills = tmp_db_with_schema.execute("SELECT * FROM fills").fetchall()
    assert len(signals) == 2
    assert len(fills) == 2

def test_ingest_ledger_csv_idempotent(tmp_db_with_schema):
    rows = [
        {
            'snapshot_file': 'snap1.csv',
            'snapshot_ts_utc': '2026-05-09T10:00:00Z',
            'city': 'Tokyo',
            'bracket': '23',
            'side': 'BUY_YES',
            'model': 'ecmwf',
            'model_prob': '0.65',
            'market_yes_price': '0.55',
            'edge': '0.10',
            'abs_edge': '0.10',
            'event_date': '2026-05-09',
            'shares': '100',
            'cost_usd': '55.00',
            'entry_price': '0.55',
            'mode': 'paper',
            'order_id': 'ord1',
            'created_at_utc': '2026-05-09T10:01:00Z',
            'settlement_status': 'settled',
        },
    ]

    source = '/path/to/ledger.csv'
    run_id = 'run1'
    config_id = 'cfg1'

    # First ingest
    ingest_ledger_csv(tmp_db_with_schema, rows, source, run_id, config_id)

    # Second ingest same data - should be idempotent
    count = ingest_ledger_csv(tmp_db_with_schema, rows, source, run_id, config_id)

    signals = tmp_db_with_schema.execute("SELECT * FROM signals").fetchall()
    assert len(signals) == 1  # Still 1, not 2

def test_ingest_settlement_rows_insert_and_idempotent(tmp_db_with_schema):
    rows = [
        {
            'event_date': '2026-05-09',
            'bracket': '23',
            'settlement_status': 'settled',
            'final_yes': '1',
        },
        {
            'event_date': '2026-05-09',
            'bracket': '24',
            'settlement_status': 'missing_event',
            'final_yes': '',
        },
    ]

    source = '/path/to/ledger.csv'
    count = ingest_settlement_rows(tmp_db_with_schema, rows, source)

    s1 = tmp_db_with_schema.execute("SELECT * FROM settlements").fetchall()
    assert len(s1) == 2

    # Second call - idempotent
    count2 = ingest_settlement_rows(tmp_db_with_schema, rows, source)
    s2 = tmp_db_with_schema.execute("SELECT * FROM settlements").fetchall()
    assert len(s2) == 2  # Still 2