import re
import sqlite3
from pathlib import Path

from weather_dashboard.db.connection import get_conn

def _extract_sql_statements(sql_text: str) -> list[str]:
    """Extract complete SQL statements from schema file.
    Handles multi-line CREATE TABLE/INDEX/TRIGGER statements.
    """
    # Pattern to match CREATE statements with proper handling of BEGIN...END in triggers
    # For triggers, the body can contain semicolons, but END; is the terminator
    pattern = r'''
        CREATE \s+ (
            TABLE \s+ IF \s+ NOT \s+ EXISTS \s+ \w+ \s* \( .*? \);
            |
            INDEX \s+ IF \s+ NOT \s+ EXISTS \s+ \w+ \s+ ON \s+ \w+ \( .*? \);
            |
            TRIGGER \s+ IF \s+ NOT \s+ EXISTS \s+ \w+ .*? END;
        )
    '''
    return [m.group().strip() for m in re.finditer(pattern, sql_text, re.DOTALL | re.VERBOSE)]

def apply_schema(conn: sqlite3.Connection) -> None:
    """Read schema.sql, execute each statement, then insert version=1 if schema_version is empty. Idempotent."""
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text()

    stmts = _extract_sql_statements(sql)
    for stmt in stmts:
        conn.execute(stmt)

    # Check if schema_version has any rows
    row = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
    if row[0] == 0:
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (1, 'initial schema v1')"
        )
    conn.commit()

def init_db(db_path: str) -> None:
    """get_conn(db_path) + apply_schema + close."""
    conn = get_conn(db_path)
    apply_schema(conn)
    conn.close()