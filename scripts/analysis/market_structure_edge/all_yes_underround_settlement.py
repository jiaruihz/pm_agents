"""Settlement helpers for all-YES underround basket facts.

The canonical `settlements` table is the first choice when it has
condition-level rows.  The source-grain `settlement_outcomes` table is the
second choice for city/date/bracket outcomes.  Direct pm_history reads are kept
only as a migration fallback for old DB snapshots that lack settlement_outcomes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from weather_dashboard.ingest.settlement_outcomes import load_settlement_outcomes


ROOT = Path(__file__).resolve().parents[3]
PM_HISTORY_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "market_data" / "cache" / "pm_history"


def final_yes(price: Any) -> float | None:
    if price is None:
        return None
    value = float(price)
    if value >= 0.999:
        return 1.0
    if value <= 0.001:
        return 0.0
    return None


def load_condition_settlements(
    conn: sqlite3.Connection,
    condition_ids: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    if condition_ids is not None:
        condition_ids = sorted({str(value) for value in condition_ids if value})
        if not condition_ids:
            return {}
        placeholders = ",".join("?" for _ in condition_ids)
        rows = conn.execute(
            "SELECT condition_id, target_date, bracket, final_price, settlement_status "
            f"FROM settlements WHERE condition_id IN ({placeholders})",
            condition_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT condition_id, target_date, bracket, final_price, settlement_status "
            "FROM settlements WHERE condition_id IS NOT NULL"
        ).fetchall()
    return {str(row["condition_id"]): dict(row) for row in rows if row["condition_id"]}


def load_pm_history_settlements(pm_history_dir: str | Path = PM_HISTORY_DEFAULT) -> dict[tuple[str, str, str], dict[str, Any]]:
    root = Path(pm_history_dir)
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not root.exists():
        return rows
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        city = str(data.get("city") or path.name.rsplit("_", 1)[0])
        date = str(data.get("date") or path.stem.rsplit("_", 1)[-1])
        for bracket in data.get("brackets") or []:
            label = str(bracket.get("label") or "").strip()
            if not label:
                continue
            price = bracket.get("final_price")
            yes = final_yes(price)
            rows[(city, date, label)] = {
                "condition_id": None,
                "target_date": date,
                "city": city,
                "bracket": label,
                "token_id": bracket.get("token_id"),
                "final_price": price,
                "settlement_status": "settled" if yes is not None else "unresolved",
                "settlement_source": "pm_history_city_bracket",
            }
    return rows


def load_city_bracket_settlements(
    conn: sqlite3.Connection | None = None,
    pm_history_dir: str | Path | None = None,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    if conn is not None:
        rows = load_settlement_outcomes(conn)
        if rows:
            return rows
    if pm_history_dir is None:
        return {}
    return load_pm_history_settlements(pm_history_dir)


def resolve_leg_settlement(
    *,
    leg: dict[str, Any],
    city: Any,
    event_date: Any,
    condition_settlements: dict[str, dict[str, Any]],
    city_bracket_settlements: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    condition_id = str(leg.get("condition_id") or "")
    if condition_id:
        settlement = condition_settlements.get(condition_id)
        if settlement and settlement.get("settlement_status") == "settled" and final_yes(settlement.get("final_price")) is not None:
            out = dict(settlement)
            out.setdefault("settlement_source", "settlements_condition_id")
            return out

    bracket = str(leg.get("bracket") or "").strip()
    if bracket:
        settlement = city_bracket_settlements.get((str(city), str(event_date), bracket))
        if settlement:
            return dict(settlement)
    if condition_id and condition_id in condition_settlements:
        out = dict(condition_settlements[condition_id])
        out.setdefault("settlement_source", "settlements_condition_id_unresolved")
        return out
    return None
