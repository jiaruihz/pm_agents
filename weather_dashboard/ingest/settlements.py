import hashlib
from weather_dashboard.ingest.common import row_hash, already_ingested, record_ingestion

def _settlement_id(target_date: str, bracket: str) -> str:
    raw = f"{target_date}|{bracket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def ingest_settlement_rows(
    conn,
    rows: list[dict],
    source_path: str,
) -> int:
    """
    Idempotent ingest of settlement records from ledger CSV rows.
    UNIQUE(target_date, bracket) ensures no duplicates.
    Returns number of new rows inserted.
    """
    new_inserted = 0

    for row in rows:
        target_date = row.get('event_date', '')
        bracket = row.get('bracket', '')
        if not target_date or not bracket:
            continue

        settlement_id = _settlement_id(target_date, bracket)
        h = row_hash({'event_date': target_date, 'bracket': bracket})

        if not already_ingested(conn, source_path, h, 'settlements'):
            final_yes = row.get('final_yes', '')
            if final_yes not in ('', '0', '1'):
                final_yes = None
            elif final_yes == '':
                final_yes = None
            else:
                final_yes = int(final_yes)

            status = row.get('settlement_status', '')
            if status not in ('settled', 'missing_event', 'missing_bracket'):
                status = 'missing_event'

            conn.execute("""
                INSERT OR IGNORE INTO settlements (settlement_id, target_date, bracket, final_yes, status)
                VALUES (?, ?, ?, ?, ?)
            """, (
                settlement_id,
                target_date,
                bracket,
                final_yes,
                status,
            ))
            record_ingestion(conn, source_path, h, 'settlements', settlement_id)
            new_inserted += 1

    conn.commit()
    return new_inserted