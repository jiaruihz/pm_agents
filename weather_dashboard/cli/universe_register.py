"""
universe_register.py

Register a universe (set of cities + models) from a YAML file.
universe_id is taken from the YAML; if absent, generated as sha256(name)[:16].
Idempotent: re-running with the same universe_id is a no-op.

Usage:
    python -m weather_dashboard.cli.universe_register \
        --universe universes/t24_cities_v1.yaml \
        --db-path runtime/weather.db
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml


def _universe_id(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()[:16]


def register_universe(conn, universe_id: str, name: str, description: str,
                      cities: list, models: list) -> str:
    """
    Insert universe into DB if not already present.
    Returns universe_id.
    """
    exists = conn.execute(
        "SELECT 1 FROM universes WHERE universe_id = ?", (universe_id,)
    ).fetchone()

    if not exists:
        conn.execute(
            "INSERT INTO universes (universe_id, name, description, cities, models, created_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                universe_id,
                name,
                description or "",
                json.dumps(cities),
                json.dumps(models),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        print(f"Registered universe '{name}' → universe_id={universe_id}")
    else:
        print(f"Universe already registered: universe_id={universe_id}")

    return universe_id


def register_universe_from_yaml(yaml_path: str, db_path: str) -> str:
    """Load YAML and register. Returns universe_id."""
    from weather_dashboard.db.apply_schema import init_db
    from weather_dashboard.db.connection import get_conn

    data = yaml.safe_load(Path(yaml_path).read_text())
    name = data["name"]
    uid = data.get("universe_id") or _universe_id(name)
    description = data.get("description", "")
    cities = data.get("cities", [])
    models = data.get("models", [])

    init_db(db_path)
    conn = get_conn(db_path)
    try:
        return register_universe(conn, uid, name, description, cities, models)
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Register universe from YAML")
    parser.add_argument("--universe", required=True, help="Path to universe YAML file")
    parser.add_argument("--db-path", required=True, help="Path to SQLite DB")
    args = parser.parse_args()

    uid = register_universe_from_yaml(args.universe, args.db_path)
    print(f"universe_id: {uid}")
