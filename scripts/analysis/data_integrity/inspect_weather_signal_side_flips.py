#!/usr/bin/env python3
"""Inspect YES/NO side flips across weather signal scans.

This is a raw lineage diagnostic: it reads mirrored remote_pm_agent signals
JSONL files and groups rows by target_date/city/condition/bracket.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


BJ = timezone(timedelta(hours=8))


def parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    raw = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def pick(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def row_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    target_date = str(pick(row, "target_date", "event_date", "date") or "")
    city = str(pick(row, "city", "city_name") or "")
    condition_id = str(pick(row, "condition_id", "market_id") or "")
    bracket = str(pick(row, "bracket", "bracket_label", "label") or "")
    return target_date, city, condition_id, bracket


def signal_side(row: dict[str, Any]) -> str:
    side = str(pick(row, "signal_side", "side", "order_side") or "")
    return side.replace("BUY_", "").upper()


def signal_ts(row: dict[str, Any], file_ts: datetime | None) -> datetime | None:
    return (
        parse_ts(
            str(
                pick(
                    row,
                    "snapshot_ts_utc",
                    "snapshot_fetched_at_utc",
                    "created_at_utc",
                    "imported_at_utc",
                    "ts_utc",
                )
                or ""
            )
        )
        or file_ts
    )


def numeric_range(rows: list[dict[str, Any]], name: str) -> tuple[float | str, float | str]:
    vals = [float(r[name]) for r in rows if r.get(name) is not None]
    if not vals:
        return "", ""
    return min(vals), max(vals)


def fmt_num(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


def file_ts(path: Path) -> datetime | None:
    for part in path.stem.split("_"):
        if len(part) == 16 and part.endswith("Z") and "T" in part:
            try:
                return datetime.strptime(part, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


def iter_rows(signals_dir: Path, scan_dates_bj: set[str] | None) -> Any:
    for path in sorted(signals_dir.glob("*signals.jsonl")):
        fallback_ts = file_ts(path)
        if scan_dates_bj and fallback_ts:
            if fallback_ts.astimezone(BJ).date().isoformat() not in scan_dates_bj:
                continue
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
                ts = signal_ts(row, fallback_ts)
                if scan_dates_bj and ts:
                    if ts.astimezone(BJ).date().isoformat() not in scan_dates_bj:
                        continue
                yield path, ts, row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--signals-dir",
        default="runtime/weather_edge_v1/remote_pm_agent/signals",
        type=Path,
    )
    parser.add_argument("--scan-date-bj", action="append", default=[])
    parser.add_argument("--target-date", action="append", default=[])
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    scan_dates = set(args.scan_date_bj) if args.scan_date_bj else None
    target_dates = set(args.target_date) if args.target_date else None

    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    total = 0
    for path, ts, row in iter_rows(args.signals_dir, scan_dates):
        key = row_key(row)
        if target_dates and key[0] not in target_dates:
            continue
        side = signal_side(row)
        if side not in {"YES", "NO"}:
            continue
        total += 1
        groups[key].append(
            {
                "file": path.name,
                "ts_utc": ts.isoformat().replace("+00:00", "Z") if ts else "",
                "scan_date_bj": ts.astimezone(BJ).date().isoformat() if ts else "",
                "side": side,
                "model_p_yes": pick(row, "model_p_yes", "model_probability_yes", "p_yes"),
                "market_price": pick(row, "market_price", "entry_price", "price"),
                "edge": pick(row, "edge"),
                "strategy_id": pick(row, "strategy_id", "strategy_instance", "config_id"),
            }
        )

    flips = []
    for key, rows in groups.items():
        sides = {r["side"] for r in rows}
        if len(sides) < 2:
            continue
        rows.sort(key=lambda r: r["ts_utc"])
        switches = sum(1 for prev, cur in zip(rows, rows[1:]) if prev["side"] != cur["side"])
        first = rows[0]
        last = rows[-1]
        min_p, max_p = numeric_range(rows, "model_p_yes")
        min_price, max_price = numeric_range(rows, "market_price")
        min_edge, max_edge = numeric_range(rows, "edge")
        flips.append(
            {
                "target_date": key[0],
                "city": key[1],
                "condition_id": key[2],
                "bracket": key[3],
                "rows": len(rows),
                "switches": switches,
                "sides": ",".join(sorted(sides)),
                "first_ts": first["ts_utc"],
                "first_side": first["side"],
                "last_ts": last["ts_utc"],
                "last_side": last["side"],
                "min_p": min_p,
                "max_p": max_p,
                "min_price": min_price,
                "max_price": max_price,
                "min_edge": min_edge,
                "max_edge": max_edge,
                "sample_sequence": " -> ".join(
                    (
                        f"{r['ts_utc']}:{r['side']}"
                        f"(p={fmt_num(r['model_p_yes'])},px={fmt_num(r['market_price'])},e={fmt_num(r['edge'])})"
                    )
                    for r in rows[:10]
                ),
            }
        )

    flips.sort(key=lambda r: (r["target_date"], r["city"], r["bracket"]))

    print(f"total_signal_rows={total}")
    print(f"group_count={len(groups)}")
    print(f"flip_group_count={len(flips)}")
    import sys

    fieldnames = [
        "target_date",
        "city",
        "bracket",
        "rows",
        "switches",
        "sides",
        "first_ts",
        "first_side",
        "last_ts",
        "last_side",
        "min_p",
        "max_p",
        "min_price",
        "max_price",
        "min_edge",
        "max_edge",
        "sample_sequence",
    ]
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    for row in flips[: args.limit]:
        writer.writerow({k: row[k] for k in fieldnames})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
