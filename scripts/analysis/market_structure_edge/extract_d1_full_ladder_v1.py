#!/usr/bin/env python3
"""Extract full-ladder D1 quotes from immutable paper snapshots."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_brackets import parse_market_bracket
DEFAULT_BASKETS = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_snapshot_history_v4/executable_baskets.csv"
)
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/d1_full_ladder_no_v1/"
    "full_ladder_rows.csv"
)
DEFAULT_SNAPSHOT_DIR = (
    ROOT
    / "runtime/weather_edge_v1/"
    "market_data.pre_external_20260706T215828/paper_snapshots"
)
HOLDOUT_START = "2026-06-17"
HOLDOUT_END = "2026-07-23"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baskets", type=Path, default=DEFAULT_BASKETS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=DEFAULT_SNAPSHOT_DIR,
        help="Readable immutable snapshot mirror; files are matched by basename.",
    )
    parser.add_argument("--holdout-start", default=HOLDOUT_START)
    parser.add_argument("--holdout-end", default=HOLDOUT_END)
    return parser.parse_args()


def finite(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def bracket_sort_key(row: dict[str, Any]) -> tuple[float, str]:
    label = str(row.get("bracket") or "")
    parsed = parse_market_bracket(label, str(row.get("question") or ""))
    if parsed is None:
        return math.inf, label
    if parsed.bottom:
        value = float(parsed.high)
    elif parsed.top:
        value = float(parsed.low)
    else:
        value = (float(parsed.low) + float(parsed.high)) / 2.0
    return value, label


def load_outcomes(db: Path) -> pd.DataFrame:
    query = """
    SELECT city, target_date, bracket,
           MAX(CASE WHEN final_price >= 0.999 THEN 1.0
                    WHEN final_price <= 0.001 THEN 0.0
                    ELSE final_price END) AS yes_win
    FROM settlement_outcomes
    WHERE settlement_status = 'settled'
    GROUP BY city, target_date, bracket
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return pd.read_sql_query(query, conn)
    finally:
        conn.close()


def extract(args: argparse.Namespace) -> pd.DataFrame:
    baskets = pd.read_csv(args.baskets)
    baskets = baskets[
        baskets["target_date"].between(
            args.holdout_start, args.holdout_end
        )
    ].copy()
    records: list[dict[str, Any]] = []
    for path_text, group in baskets.groupby("source_path", sort=True):
        source_path = Path(path_text)
        readable_path = args.snapshot_dir / source_path.name
        if not readable_path.exists():
            continue
        payload = json.loads(readable_path.read_text(encoding="utf-8"))
        raw_rows = [
            row
            for row in payload.get("records") or []
            if isinstance(row, dict)
        ]
        for basket in group.to_dict("records"):
            event_slug = str(basket["snapshot_key"]).split("|", 3)[-1]
            ladder = [
                row
                for row in raw_rows
                if str(row.get("city") or "") == str(basket["city"])
                and str(
                    row.get("target_date") or row.get("event_date") or ""
                )
                == str(basket["target_date"])
                and str(
                    row.get("event_slug")
                    or f"{basket['city']}|{basket['target_date']}"
                )
                == event_slug
            ]
            ladder.sort(key=bracket_sort_key)
            rung_count = len(ladder)
            for index, row in enumerate(ladder):
                no_bid = finite(row.get("no_best_bid"))
                no_ask = finite(row.get("no_best_ask"))
                no_size = finite(row.get("no_ask_size"))
                records.append(
                    {
                        "snapshot_key": basket["snapshot_key"],
                        "source_path": path_text,
                        "city": basket["city"],
                        "target_date": basket["target_date"],
                        "decision_ts_utc": basket["decision_ts_utc"],
                        "market_unit": basket["market_unit"],
                        "bracket": str(row.get("bracket") or ""),
                        "question": str(row.get("question") or ""),
                        "rung_index": index,
                        "rung_count": rung_count,
                        "distance_from_nearest_endpoint": min(
                            index, rung_count - 1 - index
                        ),
                        "no_best_bid": no_bid,
                        "no_best_ask": no_ask,
                        "no_ask_size": no_size,
                        "yes_best_bid": finite(row.get("yes_best_bid")),
                        "yes_best_ask": finite(row.get("yes_best_ask")),
                        "no_executable": (
                            0.001 <= no_bid <= no_ask <= 0.999
                            and no_size >= 1.0
                        ),
                    }
                )
    frame = pd.DataFrame(records)
    outcomes = load_outcomes(args.db)
    frame = frame.merge(
        outcomes,
        on=["city", "target_date", "bracket"],
        how="left",
        validate="many_to_one",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    summary = {
        "baskets": int(frame["snapshot_key"].nunique()),
        "rungs": len(frame),
        "settled_rungs": int(frame["yes_win"].notna().sum()),
        "executable_rungs": int(frame["no_executable"].sum()),
        "target_dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
    }
    (args.output.parent / "extraction_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return frame


def main() -> None:
    extract(parse_args())


if __name__ == "__main__":
    main()
