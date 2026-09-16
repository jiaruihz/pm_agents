#!/usr/bin/env python3
"""
Backfill missing fact_signal_candidates decision-window fields.

The base candidate builder uses paper_snapshots as the opportunity universe and
only treats a row as decision-window-complete when that stream contains a
representative T-22~24h record. This script leaves the universe unchanged and
fills missing decision-window rows from two auditable sources:

1. Same city+event_date anchor time from already-complete candidates.
2. Raw orderbook snapshots at or before the anchor time for executable prices.
3. Nearest paper_snapshot record near the anchor time for model probability.

It is local-analysis only. It does not change live behavior.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_clock_contract import parse_utc_or_none  # noqa: E402

DB_PATH = ROOT / "runtime" / "weather.db"
MARKET_DATA_DIR = ROOT / "runtime" / "weather_edge_v1" / "market_data"
PAPER_SNAPSHOT_DIR = MARKET_DATA_DIR / "paper_snapshots"
ORDERBOOK_DIR = MARKET_DATA_DIR / "orderbook_snapshots"
REPORT_JSON = MARKET_DATA_DIR / "research" / "decision_window_backfill_report.json"
REPORT_MD = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-decision-window-backfill.md"


BACKFILL_COLUMNS = {
    "decision_window_source": "TEXT",
    "decision_window_backfill_anchor_ts_utc": "TEXT",
    "decision_window_backfill_orderbook_ts_utc": "TEXT",
    "decision_window_backfill_orderbook_age_min": "REAL",
    "decision_window_backfill_paper_ts_utc": "TEXT",
    "decision_window_backfill_paper_age_min": "REAL",
    "decision_window_wear_cents": "REAL",
    "decision_window_backfilled_at_utc": "TEXT",
}


def parse_ts(value: Any) -> datetime | None:
    return parse_utc_or_none(value, field="decision_window_clock")


def iso_z(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp_price(value: float | None) -> float | None:
    if value is None:
        return None
    return min(max(value, 0.0), 1.0)


def side_outcome(side: str) -> str:
    return "yes" if side == "BUY_YES" else "no"


def side_edge(side: str, model_p_yes: float | None, entry_price: float | None) -> float | None:
    if model_p_yes is None or entry_price is None:
        return None
    if side == "BUY_YES":
        return model_p_yes - entry_price
    if side == "BUY_NO":
        return (1.0 - model_p_yes) - entry_price
    return None


def counterfactual_pnl(
    side: str, entry_price: float | None, final_yes: float | None, shares: float | None
) -> float | None:
    if entry_price is None or final_yes is None or shares is None:
        return None
    if side == "BUY_YES":
        return (final_yes - entry_price) * shares
    if side == "BUY_NO":
        return ((1.0 - final_yes) - entry_price) * shares
    return None


@dataclass
class Candidate:
    candidate_id: str
    condition_id: str
    side: str
    event_date: str
    city: str
    final_yes: float | None


@dataclass
class PaperRecord:
    ts: datetime
    model_p_yes: float | None
    shares: float | None


@dataclass
class BookRecord:
    ts: datetime
    best_ask: float | None
    spread: float | None
    depth_ask_5c: float | None


@dataclass
class BackfillRow:
    candidate: Candidate
    anchor_ts: datetime
    paper: PaperRecord
    selected_book: BookRecord
    yes_book: BookRecord | None
    no_book: BookRecord | None
    paper_age_min: float
    orderbook_age_min: float
    decision_entry_price: float
    market_yes_price: float | None
    edge: float | None
    abs_edge: float | None
    counterfactual_pnl: float | None


def ensure_backfill_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(fact_signal_candidates)").fetchall()
    }
    for name, col_type in BACKFILL_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE fact_signal_candidates ADD COLUMN {name} {col_type}")
    conn.commit()


def load_missing_candidates(conn: sqlite3.Connection) -> list[Candidate]:
    rows = conn.execute(
        """
        SELECT candidate_id, condition_id, side, event_date, city, final_yes
        FROM fact_signal_candidates
        WHERE decision_window_missing=1
          AND condition_id IS NOT NULL
          AND side IN ('BUY_YES', 'BUY_NO')
          AND event_date IS NOT NULL
          AND city IS NOT NULL
        """
    ).fetchall()
    return [
        Candidate(
            candidate_id=str(r[0]),
            condition_id=str(r[1]),
            side=str(r[2]),
            event_date=str(r[3]),
            city=str(r[4]),
            final_yes=safe_float(r[5]),
        )
        for r in rows
    ]


def load_city_date_anchors(conn: sqlite3.Connection) -> dict[tuple[str, str], datetime]:
    grouped: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    for city, event_date, ts_text in conn.execute(
        """
        SELECT city, event_date, decision_snapshot_ts_utc
        FROM fact_signal_candidates
        WHERE decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND city IS NOT NULL
          AND event_date IS NOT NULL
        """
    ):
        ts = parse_ts(ts_text)
        if ts is not None:
            grouped[(str(city), str(event_date))].append(ts)
    anchors: dict[tuple[str, str], datetime] = {}
    for key, values in grouped.items():
        epochs = [v.timestamp() for v in values]
        anchors[key] = datetime.fromtimestamp(median(epochs), tz=timezone.utc)
    return anchors


def load_paper_records(
    paper_dir: Path, wanted_keys: set[tuple[str, str, str]]
) -> dict[tuple[str, str, str], list[PaperRecord]]:
    out: dict[tuple[str, str, str], list[PaperRecord]] = defaultdict(list)
    for path in sorted(paper_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for rec in payload.get("records", []):
            if not isinstance(rec, dict):
                continue
            cid = rec.get("condition_id")
            side = rec.get("side")
            event_date = rec.get("event_date")
            if not cid or not side or not event_date:
                continue
            key = (str(cid), str(side), str(event_date))
            if key not in wanted_keys:
                continue
            ts = parse_ts(rec.get("ts_utc"))
            if ts is None:
                continue
            out[key].append(
                PaperRecord(
                    ts=ts,
                    model_p_yes=safe_float(rec.get("model_prob")),
                    shares=safe_float(rec.get("shares")),
                )
            )
    for records in out.values():
        records.sort(key=lambda r: r.ts)
    return out


def needed_orderbook_dates(anchors: dict[tuple[str, str], datetime]) -> set[str]:
    dates: set[str] = set()
    for anchor in anchors.values():
        dates.add(anchor.date().isoformat())
        prev = datetime.fromtimestamp(anchor.timestamp() - 86400, tz=timezone.utc)
        dates.add(prev.date().isoformat())
    return dates


def load_orderbook_records(
    orderbook_dir: Path,
    dates: set[str],
    wanted_condition_ids: set[str],
) -> dict[tuple[str, str], list[BookRecord]]:
    out: dict[tuple[str, str], list[BookRecord]] = defaultdict(list)
    for date in sorted(dates):
        day_dir = orderbook_dir / date
        if not day_dir.exists():
            continue
        for path in sorted(day_dir.glob("*.jsonl.gz")):
            try:
                fh = gzip.open(path, "rt", encoding="utf-8")
            except OSError:
                continue
            with fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    cid = rec.get("condition_id")
                    if cid not in wanted_condition_ids:
                        continue
                    outcome = rec.get("outcome")
                    if outcome not in ("yes", "no"):
                        continue
                    ts = parse_ts(rec.get("snapshot_ts_utc"))
                    if ts is None:
                        continue
                    summary = rec.get("summary") or {}
                    out[(str(cid), str(outcome))].append(
                        BookRecord(
                            ts=ts,
                            best_ask=safe_float(summary.get("best_ask")),
                            spread=safe_float(summary.get("spread")),
                            depth_ask_5c=safe_float(summary.get("depth_ask_5c")),
                        )
                    )
    for records in out.values():
        records.sort(key=lambda r: r.ts)
    return out


def nearest_paper(
    records: list[PaperRecord], anchor: datetime, max_age_min: float
) -> tuple[PaperRecord | None, float | None]:
    best: PaperRecord | None = None
    best_age: float | None = None
    for rec in records:
        age = abs((anchor - rec.ts).total_seconds()) / 60.0
        if age <= max_age_min and (best_age is None or age < best_age):
            best = rec
            best_age = age
    return best, best_age


def latest_book_before(
    records: list[BookRecord], anchor: datetime, max_age_min: float
) -> tuple[BookRecord | None, float | None]:
    best: BookRecord | None = None
    best_age: float | None = None
    for rec in records:
        if rec.ts > anchor:
            break
        age = (anchor - rec.ts).total_seconds() / 60.0
        if age <= max_age_min:
            best = rec
            best_age = age
    return best, best_age


def build_backfills(
    candidates: list[Candidate],
    anchors: dict[tuple[str, str], datetime],
    paper_records: dict[tuple[str, str, str], list[PaperRecord]],
    book_records: dict[tuple[str, str], list[BookRecord]],
    max_paper_age_min: float,
    max_orderbook_age_min: float,
    wear_cents: float,
) -> tuple[list[BackfillRow], dict[str, int]]:
    rows: list[BackfillRow] = []
    stats: dict[str, int] = defaultdict(int)
    for cand in candidates:
        stats["missing_candidates"] += 1
        anchor = anchors.get((cand.city, cand.event_date))
        if anchor is None:
            stats["skip_no_city_date_anchor"] += 1
            continue
        paper, paper_age = nearest_paper(
            paper_records.get((cand.condition_id, cand.side, cand.event_date), []),
            anchor,
            max_paper_age_min,
        )
        if paper is None or paper_age is None or paper.model_p_yes is None:
            stats["skip_no_near_paper_model"] += 1
            continue
        selected_outcome = side_outcome(cand.side)
        selected_book, book_age = latest_book_before(
            book_records.get((cand.condition_id, selected_outcome), []),
            anchor,
            max_orderbook_age_min,
        )
        if selected_book is None or book_age is None or selected_book.best_ask is None:
            stats["skip_no_near_orderbook"] += 1
            continue
        yes_book, _ = latest_book_before(
            book_records.get((cand.condition_id, "yes"), []),
            anchor,
            max_orderbook_age_min,
        )
        no_book, _ = latest_book_before(
            book_records.get((cand.condition_id, "no"), []),
            anchor,
            max_orderbook_age_min,
        )
        decision_entry = clamp_price(selected_book.best_ask + wear_cents)
        market_yes = yes_book.best_ask if yes_book and yes_book.best_ask is not None else None
        edge = side_edge(cand.side, paper.model_p_yes, decision_entry)
        rows.append(
            BackfillRow(
                candidate=cand,
                anchor_ts=anchor,
                paper=paper,
                selected_book=selected_book,
                yes_book=yes_book,
                no_book=no_book,
                paper_age_min=paper_age,
                orderbook_age_min=book_age,
                decision_entry_price=decision_entry,
                market_yes_price=market_yes,
                edge=edge,
                abs_edge=abs(edge) if edge is not None else None,
                counterfactual_pnl=counterfactual_pnl(
                    cand.side, decision_entry, cand.final_yes, paper.shares
                ),
            )
        )
        stats["backfillable"] += 1
    return rows, dict(stats)


def apply_backfills(conn: sqlite3.Connection, rows: list[BackfillRow], wear_cents: float) -> None:
    ensure_backfill_columns(conn)
    now = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        payload.append(
            (
                "orderbook_backfill",
                iso_z(row.anchor_ts),
                row.decision_entry_price,
                row.market_yes_price,
                row.edge,
                row.abs_edge,
                row.yes_book.spread if row.yes_book else None,
                row.no_book.spread if row.no_book else None,
                row.yes_book.depth_ask_5c if row.yes_book else None,
                row.no_book.depth_ask_5c if row.no_book else None,
                row.paper.model_p_yes,
                row.counterfactual_pnl,
                iso_z(row.anchor_ts),
                iso_z(row.selected_book.ts),
                row.orderbook_age_min,
                iso_z(row.paper.ts),
                row.paper_age_min,
                wear_cents,
                now,
                row.candidate.candidate_id,
            )
        )
    conn.executemany(
        """
        UPDATE fact_signal_candidates
        SET
          decision_window_missing=0,
          decision_window_source=?,
          decision_hours_to_settle=NULL,
          decision_snapshot_ts_utc=?,
          decision_entry_price=?,
          market_yes_price=?,
          edge=?,
          abs_edge=?,
          yes_spread=?,
          no_spread=?,
          yes_depth_ask_5c=?,
          no_depth_ask_5c=?,
          model_p_yes=?,
          counterfactual_pnl=?,
          decision_window_backfill_anchor_ts_utc=?,
          decision_window_backfill_orderbook_ts_utc=?,
          decision_window_backfill_orderbook_age_min=?,
          decision_window_backfill_paper_ts_utc=?,
          decision_window_backfill_paper_age_min=?,
          decision_window_wear_cents=?,
          decision_window_backfilled_at_utc=?
        WHERE candidate_id=?
        """,
        payload,
    )
    conn.commit()


def summarize(conn: sqlite3.Connection) -> dict[str, Any]:
    by_missing = [
        {
            "decision_window_missing": row[0],
            "rows": row[1],
            "eligible": row[2],
            "live_filled": row[3],
            "paper_ordered": row[4],
        }
        for row in conn.execute(
            """
            SELECT decision_window_missing, COUNT(1), SUM(eligible), SUM(live_filled), SUM(paper_ordered)
            FROM fact_signal_candidates
            GROUP BY decision_window_missing
            ORDER BY decision_window_missing
            """
        )
    ]
    source_rows = []
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(fact_signal_candidates)").fetchall()
    }
    if "decision_window_source" in existing_cols:
        source_rows = [
            {"decision_window_source": row[0], "rows": row[1]}
            for row in conn.execute(
                """
                SELECT COALESCE(decision_window_source, 'original_or_unset'), COUNT(1)
                FROM fact_signal_candidates
                GROUP BY COALESCE(decision_window_source, 'original_or_unset')
                ORDER BY COUNT(1) DESC
                """
            )
        ]
    return {"by_missing": by_missing, "by_source": source_rows}


def write_report(path_json: Path, path_md: Path, report: dict[str, Any]) -> None:
    path_json.parent.mkdir(parents=True, exist_ok=True)
    path_md.parent.mkdir(parents=True, exist_ok=True)
    path_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    stats = report["stats"]
    before = report["before"]["by_missing"]
    after = report["after"]["by_missing"]
    lines = [
        "# Decision Window Backfill Report",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> db: `{report['db_path']}`",
        f"> mode: `{'apply' if report['applied'] else 'dry_run'}`",
        "",
        "## Parameters",
        "",
        f"- max paper age min: `{report['params']['max_paper_age_min']}`",
        f"- max orderbook age min: `{report['params']['max_orderbook_age_min']}`",
        f"- wear cents added to selected best ask: `{report['params']['wear_cents']}`",
        "",
        "## Backfill Result",
        "",
        f"- missing candidates scanned: `{stats.get('missing_candidates', 0)}`",
        f"- backfillable: `{stats.get('backfillable', 0)}`",
        f"- skipped no city/date anchor: `{stats.get('skip_no_city_date_anchor', 0)}`",
        f"- skipped no near paper model: `{stats.get('skip_no_near_paper_model', 0)}`",
        f"- skipped no near orderbook: `{stats.get('skip_no_near_orderbook', 0)}`",
        "",
        "## Before",
        "",
        "| missing | rows | eligible | live_filled | paper_ordered |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in before:
        lines.append(
            f"| `{row['decision_window_missing']}` | {row['rows']} | {row['eligible']} | {row['live_filled']} | {row['paper_ordered']} |"
        )
    lines += [
        "",
        "## After",
        "",
        "| missing | rows | eligible | live_filled | paper_ordered |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in after:
        lines.append(
            f"| `{row['decision_window_missing']}` | {row['rows']} | {row['eligible']} | {row['live_filled']} | {row['paper_ordered']} |"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- Backfilled rows are marked with `decision_window_source='orderbook_backfill'`.",
        "- `decision_snapshot_ts_utc` is the city/date anchor from complete candidates; raw orderbook uses latest snapshot at or before that anchor.",
        "- This is an analysis DB repair only and does not change N100 live behavior.",
    ]
    path_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill fact_signal_candidates decision-window fields")
    ap.add_argument("--db-path", default=str(DB_PATH))
    ap.add_argument("--paper-snapshot-dir", default=str(PAPER_SNAPSHOT_DIR))
    ap.add_argument("--orderbook-dir", default=str(ORDERBOOK_DIR))
    ap.add_argument("--max-paper-age-min", type=float, default=360.0)
    ap.add_argument("--max-orderbook-age-min", type=float, default=180.0)
    ap.add_argument("--wear-cents", type=float, default=0.0,
                    help="Conservative cost added to selected side best_ask, e.g. 0.005")
    ap.add_argument("--report-json", default=str(REPORT_JSON))
    ap.add_argument("--report-md", default=str(REPORT_MD))
    ap.add_argument("--apply", action="store_true", help="Write updates to DB")
    args = ap.parse_args()

    db_path = Path(args.db_path)
    conn = sqlite3.connect(db_path)
    try:
        before = summarize(conn)
        candidates = load_missing_candidates(conn)
        anchors = load_city_date_anchors(conn)
        wanted_keys = {(c.condition_id, c.side, c.event_date) for c in candidates}
        paper = load_paper_records(Path(args.paper_snapshot_dir), wanted_keys)
        candidate_anchors = {
            (c.city, c.event_date): anchors[(c.city, c.event_date)]
            for c in candidates
            if (c.city, c.event_date) in anchors
        }
        dates = needed_orderbook_dates(candidate_anchors)
        books = load_orderbook_records(
            Path(args.orderbook_dir), dates, {c.condition_id for c in candidates}
        )
        rows, stats = build_backfills(
            candidates=candidates,
            anchors=anchors,
            paper_records=paper,
            book_records=books,
            max_paper_age_min=args.max_paper_age_min,
            max_orderbook_age_min=args.max_orderbook_age_min,
            wear_cents=args.wear_cents,
        )
        if args.apply:
            apply_backfills(conn, rows, wear_cents=args.wear_cents)
        after = summarize(conn)
        report = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "db_path": str(db_path),
            "applied": bool(args.apply),
            "params": {
                "max_paper_age_min": args.max_paper_age_min,
                "max_orderbook_age_min": args.max_orderbook_age_min,
                "wear_cents": args.wear_cents,
            },
            "stats": stats,
            "before": before,
            "after": after,
        }
        write_report(Path(args.report_json), Path(args.report_md), report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
