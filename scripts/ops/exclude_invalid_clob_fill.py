#!/usr/bin/env python3
"""Append and import a fail-closed CLOB fill exclusion."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.ingest.clob_fill_validity_adjustments import (
    DEFAULT_VALIDITY_ADJUSTMENT_PATH,
    append_validity_adjustment,
    import_validity_adjustments,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--fill-id", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--evidence-json", required=True)
    parser.add_argument("--journal", type=Path, default=DEFAULT_VALIDITY_ADJUSTMENT_PATH)
    args = parser.parse_args()
    evidence = json.loads(args.evidence_json)
    adjustment_id = hashlib.sha256(
        f"{args.fill_id}|excluded|{args.reason}".encode()
    ).hexdigest()
    row = {
        "adjustment_id": adjustment_id,
        "fill_id": args.fill_id,
        "effective_status": "excluded",
        "reason": args.reason,
        "evidence": evidence,
        "source_path": str(args.journal),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    appended = append_validity_adjustment(row, args.journal)
    conn = sqlite3.connect(str(args.db_path), timeout=30)
    imported = import_validity_adjustments(conn, args.journal)
    print(json.dumps({"appended": appended, "imported": imported, **row}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
