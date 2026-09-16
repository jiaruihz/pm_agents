import sqlite3
from pathlib import Path

from scripts.ops.merge_weather_first_seen_db import TABLES, merge_tables


def create_db(path: Path, *, source: bool) -> None:
    conn = sqlite3.connect(path)
    for table, primary_key in TABLES:
        extra = ", source_only TEXT" if source else ", target_only TEXT DEFAULT 'target'"
        conn.execute(
            f"""
            CREATE TABLE {table} (
                {primary_key} TEXT PRIMARY KEY,
                shared TEXT NOT NULL
                {extra}
            )
            """
        )
        conn.execute(
            f"INSERT INTO {table} ({primary_key}, shared) VALUES (?, ?)",
            (f"{table}-shared", "shared"),
        )
        if source:
            conn.execute(
                f"INSERT INTO {table} ({primary_key}, shared, source_only) VALUES (?, ?, ?)",
                (f"{table}-source", "source", "ignored"),
            )
    conn.commit()
    conn.close()


def test_dry_run_reports_missing_rows_without_writing(tmp_path):
    target = tmp_path / "target.db"
    source = tmp_path / "source.db"
    create_db(target, source=False)
    create_db(source, source=True)

    report = merge_tables(target, source, apply=False)

    assert all(row["inserted"] == 0 for row in report["tables"])


def test_apply_merges_intersection_and_preserves_target_defaults(tmp_path):
    target = tmp_path / "target.db"
    source = tmp_path / "source.db"
    create_db(target, source=False)
    create_db(source, source=True)

    report = merge_tables(target, source, apply=True)

    assert all(row["inserted"] == 1 for row in report["tables"])
    assert report["foreign_key_violations"] == []
    conn = sqlite3.connect(target)
    for table, primary_key in TABLES:
        row = conn.execute(
            f"SELECT shared, target_only FROM {table} WHERE {primary_key} = ?",
            (f"{table}-source",),
        ).fetchone()
        assert row == ("source", "target")
    conn.close()


def test_apply_nulls_source_native_orphan_revision_and_reports_it(tmp_path):
    target = tmp_path / "target.db"
    source = tmp_path / "source.db"
    for path in (target, source):
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
        for table, primary_key in TABLES:
            if table == "weather_information_events":
                conn.execute(
                    f"""
                    CREATE TABLE {table} (
                        {primary_key} TEXT PRIMARY KEY,
                        shared TEXT NOT NULL,
                        revision_of_event_id TEXT REFERENCES {table}({primary_key})
                    )
                    """
                )
            else:
                conn.execute(
                    f"CREATE TABLE {table} ({primary_key} TEXT PRIMARY KEY, shared TEXT NOT NULL)"
                )
        conn.commit()
        conn.close()

    conn = sqlite3.connect(source)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        INSERT INTO weather_information_events
          (information_event_id, shared, revision_of_event_id)
        VALUES ('orphan-revision', 'source', 'missing-parent')
        """
    )
    conn.commit()
    conn.close()

    report = merge_tables(target, source, apply=True)

    information = next(
        row for row in report["tables"] if row["table"] == "weather_information_events"
    )
    assert information["normalized_orphan_revisions"] == 1
    conn = sqlite3.connect(target)
    assert conn.execute(
        """
        SELECT revision_of_event_id
        FROM weather_information_events
        WHERE information_event_id = 'orphan-revision'
        """
    ).fetchone() == (None,)
    conn.close()
