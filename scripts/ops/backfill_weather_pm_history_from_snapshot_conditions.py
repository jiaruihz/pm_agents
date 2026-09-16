#!/usr/bin/env python3
"""Recover missing weather pm_history files from saved snapshot condition IDs.

Gamma event lookup can lose old events even while the CLOB condition endpoint
still resolves each bracket.  This tool uses the exact condition IDs saved in
paper snapshots, fetches their closed YES prices, and writes the normal
City_YYYY-MM-DD pm_history format for the canonical ingest path.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.ingest.settlement_outcomes import (  # noqa: E402
    ensure_settlement_outcomes_schema,
    insert_settlement_outcome,
    make_settlement_outcome_id,
    settlement_status,
    stored_final_price,
)

DEFAULT_CANDIDATES = (
    ROOT / "docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2/snapshot_candidates.csv"
)
DEFAULT_OUT = ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history"
CLOB_MARKET = "https://clob.polymarket.com/markets/{condition_id}"


def settled_city_days(db_path: Path) -> set[tuple[str, str]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    try:
        return {
            (str(city), str(target_date))
            for city, target_date in conn.execute(
                """SELECT DISTINCT city,target_date FROM settlement_outcomes
                   WHERE settlement_status='settled' AND final_price > 0.5"""
            )
        }
    finally:
        conn.close()


def fetch(condition_id: str) -> tuple[str, dict[str, Any] | None, str | None]:
    try:
        response = requests.get(
            CLOB_MARKET.format(condition_id=condition_id),
            timeout=20,
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            return condition_id, None, f"http_{response.status_code}"
        return condition_id, response.json(), None
    except Exception as exc:
        return condition_id, None, repr(exc)


def yes_token(market: dict[str, Any]) -> tuple[float | None, str | None]:
    for token in market.get("tokens") or []:
        if str(token.get("outcome") or "").lower() == "yes":
            try:
                price = float(token.get("price"))
            except (TypeError, ValueError):
                return None, None
            return price, str(token.get("token_id") or "") or None
    return None, None


def group_records(candidates_path: Path, missing: set[tuple[str, str]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    candidates = pd.read_csv(candidates_path, low_memory=False)
    candidates = candidates[
        candidates.apply(lambda row: (str(row["city"]), str(row["target_date"])) in missing, axis=1)
    ].copy()
    candidates.sort_values("snapshot_ts_utc", inplace=True)
    collected: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in candidates.itertuples(index=False):
        payload = json.loads(Path(row.snapshot_path).read_text(encoding="utf-8"))
        records = [
            item
            for item in payload.get("records", [])
            if str(item.get("city") or "") == str(row.city)
            and str(item.get("target_date") or item.get("event_date") or "") == str(row.target_date)
            and item.get("condition_id")
        ]
        key = (str(row.city), str(row.target_date))
        for item in records:
            collected.setdefault(key, {})[str(item.get("bracket") or item["condition_id"])] = item
    return {key: list(by_bracket.values()) for key, by_bracket in collected.items() if by_bracket}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=ROOT / "runtime/weather.db")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--ingest-outcomes", action="store_true")
    args = parser.parse_args()

    candidates = pd.read_csv(args.candidates, usecols=["city", "target_date"]).drop_duplicates()
    if args.start_date:
        candidates = candidates[candidates["target_date"].astype(str).ge(args.start_date)]
    if args.end_date:
        candidates = candidates[candidates["target_date"].astype(str).le(args.end_date)]
    settled = settled_city_days(args.db_path)
    missing = {
        (str(row.city), str(row.target_date))
        for row in candidates.itertuples(index=False)
        if (str(row.city), str(row.target_date)) not in settled
    }
    grouped = group_records(args.candidates, missing)
    condition_ids = sorted(
        {str(item["condition_id"]) for records in grouped.values() for item in records if item.get("condition_id")}
    )
    fetched: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(fetch, condition_id): condition_id for condition_id in condition_ids}
        for future in as_completed(futures):
            condition_id, market, error = future.result()
            if market is None:
                errors.append({"condition_id": condition_id, "error": error or "missing"})
            else:
                fetched[condition_id] = market

    args.out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    unresolved_city_days: list[str] = []
    outcome_rows: list[dict[str, Any]] = []
    for (city, target_date), records in grouped.items():
        brackets: list[dict[str, Any]] = []
        unit = str(records[0].get("unit") or "")
        for record in records:
            market = fetched.get(str(record.get("condition_id") or ""))
            if market is None or not market.get("closed"):
                continue
            price, token_id = yes_token(market)
            if price is None:
                continue
            brackets.append(
                {
                    "label": str(record.get("bracket") or ""),
                    "final_price": price,
                    "token_id": token_id,
                    "closed": True,
                    "question": str(record.get("question") or market.get("question") or ""),
                }
            )
            condition_id = str(record.get("condition_id") or "")
            label = str(record.get("bracket") or "")
            outcome_rows.append(
                {
                    "settlement_outcome_id": make_settlement_outcome_id(
                        "polymarket_api", city, target_date, label
                    ),
                    "source_system": "polymarket_api",
                    "source_path": CLOB_MARKET.format(condition_id=condition_id),
                    "city": city,
                    "target_date": target_date,
                    "bracket": label,
                    "unit": unit,
                    "condition_id": condition_id,
                    "market_id": str(record.get("market_id") or "") or None,
                    "token_id": token_id,
                    "raw_final_price": price,
                    "final_price": stored_final_price(price),
                    "settlement_status": settlement_status(price),
                    "question": str(record.get("question") or market.get("question") or ""),
                    "payload": json.dumps(
                        {"closed": market.get("closed"), "tokens": market.get("tokens")}, sort_keys=True
                    ),
                }
            )
        if not brackets or not any(float(item["final_price"]) >= 0.999 for item in brackets):
            unresolved_city_days.append(f"{city}|{target_date}")
            continue
        path = args.out_dir / f"{city}_{target_date}.json"
        path.write_text(
            json.dumps({"unit": unit, "brackets": brackets, "date": target_date, "city": city}, ensure_ascii=False, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        written.append(str(path))

    inserted = 0
    if args.ingest_outcomes and outcome_rows:
        conn = sqlite3.connect(args.db_path)
        try:
            ensure_settlement_outcomes_schema(conn)
            for outcome in outcome_rows:
                inserted += insert_settlement_outcome(conn, outcome)
            conn.commit()
        finally:
            conn.close()

    print(
        json.dumps(
            {
                "missing_city_days": len(missing),
                "snapshot_city_days": len(grouped),
                "condition_ids": len(condition_ids),
                "markets_fetched": len(fetched),
                "files_written": len(written),
                "polymarket_outcomes_inserted": inserted,
                "unresolved_city_days": unresolved_city_days,
                "api_errors": errors[:20],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
