#!/usr/bin/env python3
"""Join causal public books to private CLOB fills as execution evidence."""

from __future__ import annotations

import argparse
import bisect
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_clock_contract import parse_utc, utc_text  # noqa: E402


SCHEMA_VERSION = "weather_execution_evidence_materialization_v1"
PUBLIC_BOOK_SCHEMA_VERSION = "weather_public_book_evidence_v1"
PUBLIC_BOOK_SEMANTICS = "public_orderbook_state_not_fill_or_queue"
COMPOSITE_STATUS = "private_fill_plus_causal_public_book"


def _iter_public_books(
    root: Path, dates: Iterable[str]
) -> Iterable[dict[str, Any]]:
    for date_text in sorted(set(dates)):
        day_root = root / date_text
        if not day_root.is_dir():
            continue
        for path in sorted(day_root.glob("*.jsonl")):
            with path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"invalid public book JSON {path}:{line_number}"
                        ) from exc
                    if not isinstance(row, dict):
                        raise ValueError(
                            f"public book row must be object {path}:{line_number}"
                        )
                    if row.get("schema_version") != PUBLIC_BOOK_SCHEMA_VERSION:
                        raise ValueError(
                            f"unexpected public book schema {path}:{line_number}"
                        )
                    if row.get("evidence_class") != PUBLIC_BOOK_SEMANTICS:
                        raise ValueError(
                            f"public book semantics drift {path}:{line_number}"
                        )
                    observed = parse_utc(
                        row.get("book_observed_at_utc"),
                        field="book_observed_at_utc",
                    )
                    assert observed is not None
                    yield {
                        **row,
                        "_observed": observed,
                        "_source_path": str(path),
                        "_line_number": line_number,
                    }


def _unlinked_fills(
    conn: sqlite3.Connection,
    *,
    fill_dates: tuple[str, ...] = (),
    limit: int = 5000,
) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    params: list[Any] = []
    date_filter = ""
    if fill_dates:
        placeholders = ",".join("?" for _ in fill_dates)
        date_filter = (
            "AND substr(COALESCE(timestamp_adj.corrected_filled_at_utc, "
            f"f.filled_at_utc),1,10) IN ({placeholders})"
        )
        params.extend(fill_dates)
    params.append(int(limit))
    rows = conn.execute(
        f"""
        SELECT
          f.fill_id, f.execution_id, f.order_id,
          COALESCE(price_adj.corrected_filled_price, f.filled_price) AS fill_price,
          COALESCE(timestamp_adj.corrected_filled_at_utc, f.filled_at_utc)
            AS fill_ts_utc,
          COALESCE(fee_adj.transaction_hash, f.transaction_hash) AS transaction_hash,
          COALESCE(fee_adj.fee_source, f.fee_source) AS fee_source,
          o.order_side, COALESCE(o.placed_at_utc, o.created_at_utc) AS order_ts_utc,
          o.order_payload, s.token_id, s.city, s.target_date, s.bracket,
          sc.strategy_key
        FROM fills f
        JOIN orders o ON o.execution_id=f.execution_id
        JOIN plans p ON p.plan_id=o.plan_id
        JOIN signals s ON s.signal_id=p.signal_id
        JOIN runs r ON r.run_id=o.run_id
        LEFT JOIN strategy_config sc ON sc.config_id=r.config_id
        LEFT JOIN fill_price_adjustments price_adj ON price_adj.fill_id=f.fill_id
        LEFT JOIN fill_timestamp_adjustments timestamp_adj
          ON timestamp_adj.fill_id=f.fill_id
        LEFT JOIN (
          SELECT fill_id, MAX(transaction_hash) AS transaction_hash,
                 MAX(fee_source) AS fee_source
          FROM fill_fee_adjustments GROUP BY fill_id
        ) fee_adj ON fee_adj.fill_id=f.fill_id
        LEFT JOIN execution_evidence_links evidence
          ON evidence.fill_id=f.fill_id
        WHERE f.status='filled'
          AND o.venue='polymarket_clob'
          AND evidence.fill_id IS NULL
          {date_filter}
        ORDER BY f.rowid
        LIMIT ?
        """,
        params,
    ).fetchall()
    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        order_clock = parse_utc(row["order_ts_utc"], field="order_ts_utc")
        fill_clock = parse_utc(row["fill_ts_utc"], field="fill_ts_utc")
        assert order_clock is not None and fill_clock is not None
        # Exchange activity is second-resolution. Larger inversions remain a
        # true clock fault and must not be hidden by evidence materialization.
        if order_clock - fill_clock > timedelta(seconds=1):
            raise ValueError(
                f"order clock materially after fill for {row['fill_id']}"
            )
        row["_order_clock"] = order_clock
        row["_fill_clock"] = fill_clock
        output.append(row)
    return output


def _book_index(
    root: Path, fills: Iterable[dict[str, Any]]
) -> dict[str, tuple[list[datetime], list[dict[str, Any]]]]:
    dates: set[str] = set()
    for fill in fills:
        day = fill["_order_clock"].date()
        dates.add(day.isoformat())
        dates.add((day - timedelta(days=1)).isoformat())
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _iter_public_books(root, dates):
        token_id = str(row.get("token_id") or "")
        if token_id:
            grouped[token_id].append(row)
    output: dict[str, tuple[list[datetime], list[dict[str, Any]]]] = {}
    for token_id, rows in grouped.items():
        rows.sort(key=lambda row: (row["_observed"], str(row.get("evidence_id"))))
        output[token_id] = ([row["_observed"] for row in rows], rows)
    return output


def _match_book(
    fill: dict[str, Any],
    index: dict[str, tuple[list[datetime], list[dict[str, Any]]]],
    *,
    max_book_age_sec: float,
) -> tuple[dict[str, Any] | None, str]:
    token_id = str(fill.get("token_id") or "")
    if not token_id:
        return None, "missing_token_id"
    history = index.get(token_id)
    if history is None:
        return None, "no_public_book_history_for_token"
    clocks, rows = history
    position = bisect.bisect_right(clocks, fill["_order_clock"]) - 1
    if position < 0:
        return None, "no_causal_public_book_before_order"
    quote_field = "best_ask" if str(fill["order_side"]).startswith("BUY_") else "best_bid"
    while position >= 0 and rows[position].get(quote_field) is None:
        position -= 1
    if position < 0:
        return None, f"no_causal_{quote_field}"
    matched = rows[position]
    age_sec = (fill["_order_clock"] - matched["_observed"]).total_seconds()
    if age_sec > max_book_age_sec:
        return None, "causal_public_book_too_old"
    return matched, "matched"


def _link(fill: dict[str, Any], book: dict[str, Any]) -> dict[str, Any]:
    is_buy = str(fill["order_side"]).startswith("BUY_")
    quote_side = "ask" if is_buy else "bid"
    quote_price = float(book[f"best_{quote_side}"])
    fill_price = float(fill["fill_price"])
    adverse_slippage = round(
        fill_price - quote_price if is_buy else quote_price - fill_price,
        12,
    )
    age_ms = round(
        (fill["_order_clock"] - book["_observed"]).total_seconds() * 1000.0,
        3,
    )
    identity = {
        "fill_id": fill["fill_id"],
        "public_book_evidence_id": book["evidence_id"],
        "execution_book_snapshot_id": book["book_snapshot_id"],
    }
    link_id = hashlib.sha256(
        (SCHEMA_VERSION + "|" + json.dumps(identity, sort_keys=True)).encode("utf-8")
    ).hexdigest()
    private_class = (
        "authenticated_clob_fill_with_transaction_hash"
        if fill.get("transaction_hash")
        else "canonical_clob_fill_without_transaction_hash"
    )
    raw_lineage = {
        "public_book": {
            "evidence_id": book["evidence_id"],
            "source_path": book["_source_path"],
            "line_number": book["_line_number"],
            "baseline_raw_frame_ref": book.get("baseline_raw_frame_ref"),
            "delta_first_raw_frame_ref": book.get("delta_first_raw_frame_ref"),
            "delta_last_raw_frame_ref": book.get("delta_last_raw_frame_ref"),
            "delta_chain_hash": book.get("delta_chain_hash"),
            "subscription_epoch_id": book.get("subscription_epoch_id"),
        },
        "private_fill": {
            "fill_id": fill["fill_id"],
            "execution_id": fill["execution_id"],
            "order_id": fill.get("order_id"),
            "transaction_hash": fill.get("transaction_hash"),
            "fee_source": fill.get("fee_source"),
        },
    }
    return {
        "execution_evidence_link_id": link_id,
        "fill_id": fill["fill_id"],
        "execution_id": fill["execution_id"],
        "order_id": fill.get("order_id"),
        "token_id": fill["token_id"],
        "order_ts_utc": utc_text(fill["_order_clock"], timespec="auto"),
        "fill_ts_utc": utc_text(fill["_fill_clock"], timespec="auto"),
        "public_book_evidence_id": book["evidence_id"],
        "execution_book_snapshot_id": book["book_snapshot_id"],
        "book_observed_at_utc": utc_text(book["_observed"], timespec="auto"),
        "book_age_ms": age_ms,
        "quote_side": quote_side,
        "executable_quote_price": quote_price,
        "fill_price": fill_price,
        "adverse_slippage": adverse_slippage,
        "evidence_status": COMPOSITE_STATUS,
        "public_book_semantics": PUBLIC_BOOK_SEMANTICS,
        "private_fill_evidence_class": private_class,
        "raw_lineage_json": json.dumps(raw_lineage, sort_keys=True),
        "evidence_json": json.dumps(
            {
                "strategy_key": fill.get("strategy_key"),
                "city": fill.get("city"),
                "target_date": fill.get("target_date"),
                "bracket": fill.get("bracket"),
                "order_side": fill.get("order_side"),
                "sequence_status": book.get("sequence_status"),
                "gap_detection_status": book.get("gap_detection_status"),
                "sweeps": book.get("sweeps"),
            },
            sort_keys=True,
        ),
        "source_path": f"{book['_source_path']}:{book['_line_number']}",
        "created_at_utc": utc_text(datetime.now(timezone.utc), timespec="auto"),
    }


def materialize(
    conn: sqlite3.Connection,
    *,
    public_books_root: Path,
    apply: bool,
    fill_dates: tuple[str, ...] = (),
    max_unlinked_fills: int = 5000,
    max_book_age_sec: float = 120.0,
) -> dict[str, Any]:
    if max_book_age_sec <= 0:
        raise ValueError("max_book_age_sec must be positive")
    fills = _unlinked_fills(
        conn, fill_dates=fill_dates, limit=max_unlinked_fills
    )
    index = _book_index(public_books_root, fills)
    links: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for fill in fills:
        book, reason = _match_book(
            fill, index, max_book_age_sec=max_book_age_sec
        )
        if book is None:
            missing.append(
                {
                    "fill_id": fill["fill_id"],
                    "execution_id": fill["execution_id"],
                    "token_id": fill.get("token_id"),
                    "order_ts_utc": fill["order_ts_utc"],
                    "reason": reason,
                }
            )
            continue
        links.append(_link(fill, book))

    inserted = 0
    if apply and links:
        columns = tuple(links[0])
        placeholders = ",".join("?" for _ in columns)
        conn.execute("BEGIN IMMEDIATE")
        before = conn.total_changes
        conn.executemany(
            f"INSERT OR IGNORE INTO execution_evidence_links "
            f"({','.join(columns)}) VALUES ({placeholders})",
            [[row[column] for column in columns] for row in links],
        )
        inserted = conn.total_changes - before
        conn.commit()
        if inserted != len(links):
            raise RuntimeError(
                f"execution evidence insert drift: {inserted} != {len(links)}"
            )

    reason_counts = Counter(row["reason"] for row in missing)
    ages = [float(row["book_age_ms"]) for row in links]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_text(datetime.now(timezone.utc), timespec="auto"),
        "apply": apply,
        "public_books_root": str(public_books_root),
        "max_book_age_sec": max_book_age_sec,
        "unlinked_private_fills": len(fills),
        "matched_links": len(links),
        "inserted_links": inserted,
        "missing_links": len(missing),
        "missing_by_reason": dict(sorted(reason_counts.items())),
        "book_age_ms": {
            "min": min(ages) if ages else None,
            "max": max(ages) if ages else None,
            "mean": sum(ages) / len(ages) if ages else None,
        },
        "links": links,
        "missing": missing,
        "status": "complete" if not missing else "partial_missing_public_book",
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--public-books-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--fill-date", action="append", default=[])
    parser.add_argument("--max-unlinked-fills", type=int, default=5000)
    parser.add_argument("--max-book-age-sec", type=float, default=120.0)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.public_books_root.is_dir():
        raise SystemExit(f"public books root missing: {args.public_books_root}")
    conn = sqlite3.connect(args.db_path, timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        report = materialize(
            conn,
            public_books_root=args.public_books_root,
            apply=args.apply,
            fill_dates=tuple(args.fill_date),
            max_unlinked_fills=args.max_unlinked_fills,
            max_book_age_sec=args.max_book_age_sec,
        )
    finally:
        conn.close()
    _write_json_atomic(args.report, report)
    print(
        json.dumps(
            {key: report[key] for key in (
                "status", "unlinked_private_fills", "matched_links",
                "inserted_links", "missing_links", "missing_by_reason",
            )},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
