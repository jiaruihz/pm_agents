#!/usr/bin/env python3
"""Audit weather-predict and pm_agent producer outputs against the canonical protocol.

This is the first executable step of the protocol-first collection unification
plan.  It is intentionally read-only: it reports current field gaps before any
producer starts dual-writing canonical aliases.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
MARKET_DATA_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "market_data"
REMOTE_PM_AGENT_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "remote_pm_agent"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-16-weather-protocol-audit-v0.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-16-weather-protocol-audit-v0.md"


AUDITS = {
    "weather_predict_orderbook_snapshot": {
        "required": [
            "producer_system",
            "producer_run_id",
            "schema_version",
            "snapshot_ts_utc",
            "target_date",
            "city",
            "bracket",
            "condition_id",
            "market_id",
            "token_id",
            "market_price",
            "signal_side",
        ],
        "legacy_aliases": ["event_date", "outcome"],
    },
    "weather_predict_research_paper_order": {
        "required": [
            "producer_system",
            "producer_run_id",
            "schema_version",
            "target_date",
            "model_version",
            "model_p_yes",
            "market_price",
            "order_side",
        ],
        "legacy_aliases": ["event_date", "model", "model_prob", "market_yes_price", "side", "mode"],
    },
    "pm_agent_live_signal": {
        "required": [
            "producer_system",
            "producer_run_id",
            "schema_version",
            "target_date",
            "forecast_source",
            "model_p_yes",
            "order_side",
            "signal_side",
        ],
        "legacy_aliases": ["source_system", "source_run_id", "profile", "model_probability_yes"],
    },
    "pm_agent_trade_plan": {
        "required": [
            "producer_system",
            "producer_run_id",
            "schema_version",
            "target_date",
            "forecast_source",
            "model_p_yes",
            "order_side",
            "execution_policy",
        ],
        "legacy_aliases": ["profile", "model_token_probability"],
    },
    "pm_agent_live_order": {
        "required": [
            "producer_system",
            "producer_run_id",
            "schema_version",
            "target_date",
            "order_side",
            "execution_policy",
            "execution_id",
        ],
        "legacy_aliases": ["model_token_probability"],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--market-data-root", default=str(MARKET_DATA_DEFAULT))
    parser.add_argument("--remote-pm-agent-root", default=str(REMOTE_PM_AGENT_DEFAULT))
    parser.add_argument("--sample-rows", type=int, default=200)
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    return parser.parse_args()


def latest_file(paths: Iterable[Path], *, non_empty: bool = False) -> Path | None:
    files = [path for path in paths if path.exists()]
    if non_empty:
        files = [path for path in files if path.stat().st_size > 0]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def read_records(path: Path | None, limit: int) -> list[dict[str, Any]]:
    if path is None:
        return []
    opener = gzip.open if path.suffix == ".gz" else open
    rows: list[dict[str, Any]] = []
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
            if len(rows) >= limit:
                break
    return rows


def audit_records(kind: str, path: Path | None, rows: list[dict[str, Any]]) -> dict[str, Any]:
    spec = AUDITS[kind]
    missing = Counter()
    present = Counter()
    legacy = Counter()
    legacy_values = Counter()
    for row in rows:
        for field in spec["required"]:
            if row.get(field) in (None, ""):
                missing[field] += 1
            else:
                present[field] += 1
        for field in spec["legacy_aliases"]:
            if field in row and row.get(field) not in (None, ""):
                legacy[field] += 1
        if row.get("order_side") == "BUY":
            legacy_values["order_side=BUY"] += 1
    return {
        "kind": kind,
        "path": str(path) if path else None,
        "rows_sampled": len(rows),
        "required_fields": spec["required"],
        "missing_required_counts": dict(sorted(missing.items())),
        "present_required_counts": dict(sorted(present.items())),
        "legacy_alias_counts": dict(sorted(legacy.items())),
        "legacy_value_counts": dict(sorted(legacy_values.items())),
        "canonical_complete": bool(rows) and not missing,
    }


def settlement_outcomes_audit(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"kind": "settlement_outcomes", "db_path": str(db_path), "table_exists": False}
    conn = sqlite3.connect(str(db_path), timeout=1.0)
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settlement_outcomes'"
        ).fetchone()
        if not exists:
            return {"kind": "settlement_outcomes", "db_path": str(db_path), "table_exists": False}
        row = conn.execute(
            """
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT city || '|' || target_date) AS city_days,
                   SUM(condition_id IS NULL) AS null_condition_id,
                   MAX(target_date) AS max_target_date
            FROM settlement_outcomes
            """
        ).fetchone()
        by_status = [
            dict(item)
            for item in conn.execute(
                "SELECT settlement_status, COUNT(*) AS rows FROM settlement_outcomes GROUP BY settlement_status"
            )
        ]
        return {
            "kind": "settlement_outcomes",
            "db_path": str(db_path),
            "table_exists": True,
            "rows": row["rows"],
            "city_days": row["city_days"],
            "null_condition_id": row["null_condition_id"],
            "max_target_date": row["max_target_date"],
            "by_status": by_status,
        }
    finally:
        conn.close()


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Weather Protocol Audit v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        "> Scope: read-only protocol audit for weather-predict and pm_agent producer outputs.",
        "",
        "## Summary",
        "",
    ]
    for item in report["audits"]:
        if item["kind"] == "settlement_outcomes":
            lines.append(
                f"- `settlement_outcomes`: table_exists={item.get('table_exists')}, rows={item.get('rows')}, city_days={item.get('city_days')}, max_target_date={item.get('max_target_date')}."
            )
            continue
        lines.append(
            f"- `{item['kind']}`: sampled {item['rows_sampled']} rows from `{item['path']}`; canonical_complete={item['canonical_complete']}."
        )
        if item["missing_required_counts"]:
            lines.append(f"  Missing required: `{item['missing_required_counts']}`.")
        if item["legacy_alias_counts"] or item["legacy_value_counts"]:
            lines.append(f"  Legacy aliases/values: `{item['legacy_alias_counts'] | item['legacy_value_counts']}`.")
    lines.extend(
        [
            "",
            "## Recommended Next Steps",
            "",
            "1. Add canonical aliases to weather-predict snapshots and research paper orders first.",
            "2. Add canonical aliases to pm_agent live signals/plans/orders without changing execution.",
            "3. Keep `settlement_outcomes` as the DB bridge for pm_history truth while producer ownership remains in weather-predict.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    market_root = Path(args.market_data_root)
    remote_root = Path(args.remote_pm_agent_root)
    sources = {
        "weather_predict_orderbook_snapshot": latest_file(market_root.glob("orderbook_snapshots/*/orderbook_snapshot_*.jsonl.gz")),
        "weather_predict_research_paper_order": market_root / "paper_trades" / "paper_orders.jsonl",
        "pm_agent_live_signal": latest_file((remote_root / "signals").glob("*.jsonl"), non_empty=True),
        "pm_agent_trade_plan": latest_file((remote_root / "plans").glob("*.jsonl"), non_empty=True),
        "pm_agent_live_order": latest_file((remote_root / "live").glob("*.jsonl"), non_empty=True),
    }
    audits = [
        audit_records(kind, path, read_records(path, args.sample_rows))
        for kind, path in sources.items()
    ]
    audits.append(settlement_outcomes_audit(Path(args.db_path)))
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sample_rows": args.sample_rows,
        "sources": {kind: str(path) if path else None for kind, path in sources.items()},
        "audits": audits,
    }
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    write_md(Path(args.out_md), report)
    print(json.dumps({"out_json": str(out_json), "out_md": args.out_md, "audits": len(audits)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
