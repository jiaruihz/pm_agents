import hashlib
import json

def row_hash(row: dict) -> str:
    """sha256(canonical JSON), returns 64-char hex."""
    canonical = json.dumps(row, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()

def already_ingested(conn, source_path: str, source_row_hash: str, target_table: str) -> bool:
    """Check ingestion_log for existing record."""
    result = conn.execute(
        "SELECT 1 FROM ingestion_log WHERE source_path=? AND source_row_hash=? AND target_table=?",
        (source_path, source_row_hash, target_table)
    ).fetchone()
    return result is not None

def record_ingestion(conn, source_path: str, source_row_hash: str, target_table: str, target_id: str) -> None:
    """INSERT OR IGNORE INTO ingestion_log. Idempotent."""
    conn.execute(
        "INSERT OR IGNORE INTO ingestion_log (source_path, source_row_hash, target_table, target_id) VALUES (?, ?, ?, ?)",
        (source_path, source_row_hash, target_table, target_id)
    )
    conn.commit()