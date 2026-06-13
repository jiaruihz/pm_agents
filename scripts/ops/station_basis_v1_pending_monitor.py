#!/usr/bin/env python3
"""Monitor open station-basis v1 shadow entries.

For each unsettled v1 shadow entry, report:
- whether the official-station running value currently hits the bought bracket
- current CLOB bid/ask/mid for the held token
- shadow and dry-run mark-to-market using bid/mid when available

No orders are placed and no production config is changed.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

OPS = Path(__file__).resolve().parent
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

import weather_station_basis_shadow_v1 as v1  # noqa: E402


base = v1.base
SHADOW = base.DATA_ROOT / "runtime/weather_edge_v1/station_basis_shadow_v1"
EXEC = base.DATA_ROOT / "runtime/weather_edge_v1/station_basis_exec_v1"
OUT = SHADOW / "pending_monitor.json"
HISTORY = SHADOW / "pending_monitor_history.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def best_bid_from_book(book: dict) -> tuple[float, float] | None:
    bids = book.get("bids") or []
    if not bids:
        return None
    best = max(bids, key=lambda row: float(row["price"]))
    return float(best["price"]), float(best["size"])


def bracket_hit(label: str, running_value: int, question: str = "") -> bool:
    parsed = base.parse_label(label, question)
    if parsed is None:
        return False
    low = parsed["low"]
    high = parsed["high"]
    if parsed.get("bottom"):
        return high is not None and running_value <= high
    if parsed.get("top"):
        return low is not None and running_value >= low
    return low is not None and high is not None and low <= running_value <= high


def dry_run_order_for(entry: dict, orders: list[dict]) -> dict | None:
    key = (
        entry.get("city"),
        entry.get("target_date"),
        entry.get("rule"),
        entry.get("side"),
        entry.get("bracket"),
        entry.get("ts_utc"),
    )
    for row in reversed(orders):
        row_key = (
            row.get("city"),
            row.get("target_date"),
            row.get("rule"),
            row.get("side"),
            row.get("bracket"),
            row.get("shadow_ts_utc"),
        )
        if row_key == key:
            return row
    return None


def monitor_entry(entry: dict, orders: list[dict], now_utc: datetime) -> dict:
    city = entry["city"]
    slug, icao, unit, tz_name = base.CITIES[city]
    tz = ZoneInfo(tz_name)
    local_date = datetime.fromisoformat(entry["target_date"]).date()
    now_local = now_utc.astimezone(tz)
    row = {
        "entry": entry,
        "ts_utc": now_utc.isoformat(),
        "local_time": now_local.isoformat(),
        "status": "unknown",
        "dry_run_order": dry_run_order_for(entry, orders),
    }

    try:
        met = base.fetch_metar_day(icao, tz, local_date)
    except RuntimeError as exc:
        row["status"] = "metar_fetch_failed"
        row["error"] = str(exc)
        return row
    row["metar"] = met
    if met.get("status") != "ok":
        row["status"] = met.get("status", "metar_not_ok")
        return row

    if unit == "F":
        running_value = base.round_half_up(met["running_max_c"] * 9 / 5 + 32)
    else:
        running_value = base.round_half_up(met["running_max_c"])
    row["running_value"] = running_value
    hit = bracket_hit(str(entry["bracket"]), running_value)
    row["current_bracket_hit"] = hit
    if entry.get("side") == "BUY_NO":
        row["thesis_status"] = "breached_current_running_value" if hit else "alive_current_running_value"
    else:
        row["thesis_status"] = "hit_current_running_value" if hit else "not_hit_current_running_value"

    try:
        book = base.fetch_json(f"{base.CLOB}/book", {"token_id": entry["token_id"]})
        bid = best_bid_from_book(book)
        ask = base.best_ask_from_book(book)
    except RuntimeError as exc:
        row["book_status"] = "book_fetch_failed"
        row["book_error"] = str(exc)
        return row

    row["book_status"] = "ok"
    row["best_bid"] = None if bid is None else {"price": bid[0], "size": bid[1]}
    row["best_ask"] = None if ask is None else {"price": ask[0], "size": ask[1]}
    bid_price = bid[0] if bid is not None else None
    ask_price = ask[0] if ask is not None else None
    mid_price = (bid_price + ask_price) / 2 if bid_price is not None and ask_price is not None else None
    row["mid_price"] = mid_price

    entry_price = float(entry["ask"])
    shadow_shares = float(entry["shares"])
    dry_order = row["dry_run_order"] or {}
    dry_shares = float(dry_order.get("shares", 0.0) or 0.0)

    def pnl(mark: float | None, shares: float) -> float | None:
        if mark is None:
            return None
        return round((mark - entry_price) * shares, 4)

    row["mtm"] = {
        "shadow_bid_pnl": pnl(bid_price, shadow_shares),
        "shadow_mid_pnl": pnl(mid_price, shadow_shares),
        "dry_run_bid_pnl": pnl(bid_price, dry_shares),
        "dry_run_mid_pnl": pnl(mid_price, dry_shares),
        "entry_price": entry_price,
        "shadow_shares": shadow_shares,
        "dry_run_shares": dry_shares,
    }
    row["status"] = "ok"
    return row


def main() -> int:
    entries = read_jsonl(SHADOW / "entries.jsonl")
    settlements = read_jsonl(SHADOW / "settlements.jsonl")
    settled_keys = {
        (row["city"], row["target_date"], row["rule"], row["side"], row["bracket"])
        for row in settlements
    }
    pending = [
        row for row in entries
        if (row["city"], row["target_date"], row["rule"], row["side"], row["bracket"]) not in settled_keys
    ]
    orders = read_jsonl(EXEC / "orders.jsonl")
    now_utc = datetime.now(timezone.utc)
    rows = [monitor_entry(row, orders, now_utc) for row in pending]
    status_counts: dict[str, int] = {}
    thesis_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        thesis = row.get("thesis_status", "unknown")
        thesis_counts[thesis] = thesis_counts.get(thesis, 0) + 1
    out = {
        "generated_at_utc": now_utc.isoformat(),
        "entries": len(entries),
        "settled": len(settlements),
        "pending": len(pending),
        "status_counts": status_counts,
        "thesis_counts": thesis_counts,
        "rows": rows,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    history_row = {
        "generated_at_utc": out["generated_at_utc"],
        "entries": out["entries"],
        "settled": out["settled"],
        "pending": out["pending"],
        "status_counts": status_counts,
        "thesis_counts": thesis_counts,
        "rows": [
            {
                "city": row["entry"].get("city"),
                "target_date": row["entry"].get("target_date"),
                "rule": row["entry"].get("rule"),
                "side": row["entry"].get("side"),
                "bracket": row["entry"].get("bracket"),
                "entry_ts_utc": row["entry"].get("ts_utc"),
                "status": row.get("status"),
                "thesis_status": row.get("thesis_status"),
                "running_value": row.get("running_value"),
                "current_bracket_hit": row.get("current_bracket_hit"),
                "best_bid": row.get("best_bid"),
                "best_ask": row.get("best_ask"),
                "mid_price": row.get("mid_price"),
                "mtm": row.get("mtm"),
            }
            for row in rows
        ],
    }
    append_jsonl(HISTORY, history_row)
    print(json.dumps({"output": str(OUT), "pending": len(pending), "thesis_counts": thesis_counts}, ensure_ascii=False))
    for row in rows:
        entry = row["entry"]
        mtm = row.get("mtm", {})
        print(
            f"{entry['city']:12} {entry['rule']:10} {entry['side']:7} {entry['bracket']:6} "
            f"thesis={row.get('thesis_status')} bid_pnl={mtm.get('dry_run_bid_pnl')} mid_pnl={mtm.get('dry_run_mid_pnl')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
