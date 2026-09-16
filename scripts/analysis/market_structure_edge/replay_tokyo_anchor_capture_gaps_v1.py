#!/usr/bin/env python3
"""Replay legacy Tokyo anchor-mismatch polls against the PIT ladder union."""

from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_city_probability_shadow.tokyo import (
    _book_contains_anchor,
    _market_prices,
    _official_history,
    _parse_ts,
    _round_native_c,
)

UTC = timezone.utc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def replay(args: argparse.Namespace) -> dict[str, Any]:
    cutoff = _parse_ts(args.cutoff)
    error_rows = [
        row for row in read_jsonl(Path(args.errors))
        if row.get("city") == "Tokyo"
        and "official/book current bracket mismatch" in str(row.get("error") or "")
        and _parse_ts(str(row["ts_utc"])) <= cutoff
    ]
    books = [
        row for path in sorted(Path(args.book_dir).glob("*.jsonl"))
        for row in read_jsonl(path)
        if row.get("city") == "Tokyo"
        and row.get("source") == "jma_amedas"
        and row.get("outcome") == "no"
        and row.get("book_status") == "ok"
        and row.get("book_fetched_at_utc")
    ]
    centers = sorted(
        [row for row in books if row.get("relative_offset") == 0],
        key=lambda row: _parse_ts(str(row["book_fetched_at_utc"])),
    )
    center_times = [_parse_ts(str(row["book_fetched_at_utc"])) for row in centers]
    details = []
    for error in error_rows:
        error_ts = _parse_ts(str(error["ts_utc"]))
        idx = bisect_right(center_times, error_ts) - 1
        if idx < 0:
            details.append({"error_ts_utc": error_ts.isoformat(), "status": "missing_center_book"})
            continue
        center = centers[idx]
        decision = _parse_ts(str(center["book_fetched_at_utc"]))
        target_date = str(center["target_date"])
        official = _official_history(Path(args.official_dir), target_date, decision)
        if not official:
            details.append({"error_ts_utc": error_ts.isoformat(), "status": "missing_official"})
            continue
        official_anchor = _round_native_c(float(official[-1]["running_max_c"]))
        siblings = [
            row for row in books
            if row.get("target_date") == target_date
            and row.get("source_obs_ts_utc") == center.get("source_obs_ts_utc")
            and row.get("reference_market_value") == center.get("reference_market_value")
            and abs((_parse_ts(str(row["book_fetched_at_utc"])) - decision).total_seconds()) <= 45
            and _parse_ts(str(row["book_fetched_at_utc"])) <= error_ts
        ]
        matches = [row for row in siblings if _book_contains_anchor(row, official_anchor)]
        selected = max(
            matches,
            default=None,
            key=lambda row: _parse_ts(str(row["book_fetched_at_utc"])),
        )
        if selected is None:
            status = "anchor_still_missing"
            quote_state = None
        else:
            quote_state = _market_prices(selected)["quote_state"]
            status = "recover_scored" if quote_state == "two_sided" else "recover_not_scorable"
        details.append({
            "error_ts_utc": error_ts.isoformat(),
            "legacy_error": error["error"],
            "target_date": target_date,
            "center_book_ts_utc": decision.isoformat(),
            "source_obs_ts_utc": center.get("source_obs_ts_utc"),
            "source_anchor": center.get("reference_market_value"),
            "official_anchor": official_anchor,
            "captured_brackets": sorted({str(row.get("bracket") or "") for row in siblings}),
            "selected_bracket": None if selected is None else selected.get("bracket"),
            "selected_book_ts_utc": None if selected is None else selected.get("book_fetched_at_utc"),
            "quote_state": quote_state,
            "status": status,
        })
    counts: dict[str, int] = {}
    for row in details:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "schema_version": "tokyo_anchor_capture_gap_replay_v1",
        "cutoff_utc": cutoff.isoformat(),
        "error_poll_rows": len(error_rows),
        "status_counts": counts,
        "rows": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--errors", default="/Volumes/jrs/weather_data_feed_service_runtime/output/city_probability_shadow_v1/errors.jsonl")
    parser.add_argument("--book-dir", default="/Volumes/jrs/weather_data_feed_service_runtime/output/tokyo_current_break_active_ladder_shadow/active_bracket_books")
    parser.add_argument("--official-dir", default="/Volumes/jrs/weather_data_feed_service_runtime/output/observations")
    parser.add_argument("--cutoff", default="2026-08-01T05:48:16Z")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = replay(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
