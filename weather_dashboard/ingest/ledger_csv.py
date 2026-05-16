import hashlib
from weather_dashboard.ingest.common import row_hash, already_ingested, record_ingestion

def _signal_id(snapshot_file: str, city: str, bracket: str, side: str, model: str) -> str:
    raw = f"{snapshot_file}|{city}|{bracket}|{side}|{model}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def _plan_id(signal_id: str, run_id: str) -> str:
    raw = f"{signal_id}|{run_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def _order_id_from_plan(plan_id: str, order_ref: str) -> str:
    raw = f"{plan_id}|{order_ref}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def _fill_id(order_id: str, fill_ref: str) -> str:
    raw = f"{order_id}|{fill_ref}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def _ensure_run_and_config(conn, run_id: str, config_id: str) -> None:
    """Ensure run_id exists in runs table and config_id exists in strategy_config."""
    # Check if config_id exists
    existing = conn.execute(
        "SELECT 1 FROM strategy_config WHERE config_id = ?", (config_id,)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO strategy_config (config_id, name, params) VALUES (?, ?, '{}')",
            (config_id, f"auto_config_{config_id[:8]}")
        )

    # Check if run_id exists
    existing = conn.execute(
        "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO runs (run_id, config_id, execution_mode, state) VALUES (?, ?, 'snapshot_replay', 'explore')",
            (run_id, config_id)
        )

def ingest_ledger_csv(
    conn,
    rows: list[dict],
    source_path: str,
    run_id: str,
    config_id: str,
) -> int:
    """
    Idempotent ingest from ledger CSV rows.
    Each CSV row -> signal + plan + order + fill.
    Returns number of new rows inserted.
    """
    new_inserted = 0

    for row in rows:
        # Compute IDs
        model = row.get('model', '')
        signal_id = _signal_id(
            row['snapshot_file'], row['city'], row['bracket'],
            row['side'], model
        )

        # CSV side is 'BUY_YES'/'BUY_NO', signals.side is 'YES'/'NO'
        csv_side = row['side']  # 'BUY_YES' or 'BUY_NO'
        signal_side = 'YES' if 'YES' in csv_side else 'NO'

        plan_id = _plan_id(signal_id, run_id)

        # order_id from CSV or derive
        csv_order_id = row.get('order_id', '').strip()
        if csv_order_id:
            order_id = csv_order_id
        else:
            order_id = _order_id_from_plan(plan_id, 'order')

        fill_id = _fill_id(order_id, 'fill')

        h = row_hash(row)

        # Ensure run_id and config_id exist (FK requirements for plans/orders)
        _ensure_run_and_config(conn, run_id, config_id)

        # Insert signal (idempotent via ingest log)
        if not already_ingested(conn, source_path, h, 'signals'):
            conn.execute("""
                INSERT INTO signals (signal_id, snapshot_ts_utc, snapshot_file, target_date,
                    city, bracket, side, model_version, model_p_yes, market_price, edge, abs_edge)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal_id,
                row.get('snapshot_ts_utc', ''),
                row.get('snapshot_file', ''),
                row.get('event_date', ''),
                row['city'],
                row['bracket'],
                signal_side,
                model,
                row.get('model_prob', ''),
                row.get('market_yes_price', ''),
                row.get('edge', ''),
                row.get('abs_edge', ''),
            ))
            record_ingestion(conn, source_path, h, 'signals', signal_id)
            new_inserted += 1

        # Insert plan
        if not already_ingested(conn, source_path, h, 'plans'):
            conn.execute("""
                INSERT INTO plans (plan_id, run_id, signal_id, config_id, desired_shares, skip_reason)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                plan_id,
                run_id,
                signal_id,
                config_id,
                row.get('shares', ''),
                None,  # skip_reason NULL = ordered
            ))
            record_ingestion(conn, source_path, h, 'plans', plan_id)
            new_inserted += 1

        # Insert order
        if not already_ingested(conn, source_path, h, 'orders'):
            conn.execute("""
                INSERT INTO orders (order_id, run_id, plan_id, execution_mode, side,
                    entry_price, shares, cost_usd, placed_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_id,
                run_id,
                plan_id,
                row.get('mode', ''),
                csv_side,
                row.get('entry_price', ''),
                row.get('shares', ''),
                row.get('cost_usd', ''),
                row.get('created_at_utc', ''),
            ))
            record_ingestion(conn, source_path, h, 'orders', order_id)
            new_inserted += 1

        # Insert fill
        if not already_ingested(conn, source_path, h, 'fills'):
            settlement_status = row.get('settlement_status', '')
            status = 'filled' if settlement_status == 'settled' else settlement_status
            conn.execute("""
                INSERT INTO fills (fill_id, order_id, filled_shares, filled_price,
                    fees_usd, status, filled_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                fill_id,
                order_id,
                row.get('shares', ''),
                row.get('entry_price', ''),
                '0',
                status if status else 'filled',
                row.get('created_at_utc', ''),
            ))
            record_ingestion(conn, source_path, h, 'fills', fill_id)
            new_inserted += 1

    conn.commit()
    return new_inserted