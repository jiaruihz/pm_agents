"""
config_register.py

Register a strategy config from a YAML file into the weather_dashboard DB.
config_id is deterministic: sha256(canonical JSON of params)[:32]
Idempotent: safe to run multiple times with the same file.

Usage:
    python -m weather_dashboard.cli.config_register \
        --config configs/my_strategy.yaml \
        --db-path runtime/weather.db
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml


def _config_id(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def register_config(conn, name: str, params: dict) -> str:
    """
    Insert strategy config into DB if not already present.
    Returns config_id.
    """
    cid = _config_id(params)
    exists = conn.execute(
        "SELECT 1 FROM strategy_config WHERE config_id = ?", (cid,)
    ).fetchone()

    if not exists:
        conn.execute(
            "INSERT INTO strategy_config (config_id, name, params, created_at_utc) VALUES (?, ?, ?, ?)",
            (cid, name, json.dumps(params, sort_keys=True), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        print(f"Registered config '{name}' → config_id={cid}")
    else:
        print(f"Config already registered: config_id={cid}")

    return cid


def register_config_from_yaml(yaml_path: str, db_path: str) -> str:
    """Load YAML and register. Returns config_id."""
    from weather_dashboard.db.apply_schema import init_db
    from weather_dashboard.db.connection import get_conn

    data = yaml.safe_load(Path(yaml_path).read_text())
    name = data.get("name") or Path(yaml_path).stem
    params = data.get("params", {})

    init_db(db_path)
    conn = get_conn(db_path)
    try:
        return register_config(conn, name, params)
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Register strategy config from YAML")
    parser.add_argument("--config", required=True, help="Path to config YAML file")
    parser.add_argument("--db-path", required=True, help="Path to SQLite DB")
    args = parser.parse_args()

    cid = register_config_from_yaml(args.config, args.db_path)
    print(f"config_id: {cid}")
