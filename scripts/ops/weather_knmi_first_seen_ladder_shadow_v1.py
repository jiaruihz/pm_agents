#!/usr/bin/env python3
"""Capture the complete Amsterdam exact-bracket ladder around every KNMI first-seen event.

Telemetry only: this process reads public observations and order books and never
constructs, signs, or submits an order.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.etl.materialize_weather_information_events import _legacy_knmi_event  # noqa: E402
from scripts.ops.weather_fast_source_stale_book_observer import (  # noqa: E402
    MarketToken,
    augment_market_index_from_gamma,
    build_market_index,
    fetch_fresh_book,
    ordered_market_tokens,
    temperature_event_slug,
)
from weather_data_feed.city_calendar import city_local_date  # noqa: E402


CAPTURE_OFFSETS_SECONDS = (0, 15, 30, 60, 120, 300)
BOOK_FETCH_ATTEMPTS = 3
SCHEMA_VERSION = "weather_knmi_first_seen_full_ladder_v1"
CITY = "Amsterdam"


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "source_offset": 0,
            "active_events": [],
            "seen_event_ids": [],
        }
    return read_json(path)


def latest_snapshot_at_or_before(root: Path, as_of: datetime) -> Path | None:
    for path in sorted(root.rglob("snapshot_*.json"), reverse=True):
        try:
            payload = read_json(path)
            timestamp = parse_dt(
                payload.get("ts_utc")
                or next(iter(payload.get("records") or []), {}).get("snapshot_ts_utc")
            )
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if timestamp is not None and timestamp <= as_of:
            return path
    return None


def latest_snapshot(root: Path) -> Path | None:
    for path in sorted(root.rglob("snapshot_*.json"), reverse=True):
        try:
            payload = read_json(path)
            timestamp = parse_dt(
                payload.get("ts_utc")
                or next(iter(payload.get("records") or []), {}).get("snapshot_ts_utc")
            )
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if timestamp is not None:
            return path
    return None


def latest_orderbook_archive_at_or_before(
    root: Path,
    as_of: datetime,
    target_date: str,
    expected_condition_ids: set[str] | None = None,
) -> tuple[Path | None, list[dict[str, Any]]]:
    latest_partial: tuple[Path, list[dict[str, Any]]] | None = None
    for path in sorted(root.rglob("*.jsonl*"), reverse=True):
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
        except (OSError, json.JSONDecodeError):
            continue
        city_rows = [
            row
            for row in rows
            if str(row.get("city") or "") == CITY
            and str(row.get("target_date") or row.get("event_date") or "") == target_date
            and str(row.get("outcome") or "").lower() in {"yes", "no"}
        ]
        timestamp = parse_dt(
            next(iter(city_rows), {}).get("snapshot_ts_utc")
            or next(iter(rows), {}).get("snapshot_ts_utc")
        )
        if timestamp is not None and timestamp <= as_of and city_rows:
            if latest_partial is None:
                latest_partial = (path, city_rows)
            outcomes_by_condition: dict[str, set[str]] = {}
            all_ok = True
            for row in city_rows:
                condition_id = str(row.get("condition_id") or "")
                outcome = str(row.get("outcome") or "").lower()
                outcomes_by_condition.setdefault(condition_id, set()).add(outcome)
                all_ok = all_ok and str(row.get("status") or "") == "ok"
            covered = {
                condition_id
                for condition_id, outcomes in outcomes_by_condition.items()
                if outcomes == {"yes", "no"}
            }
            expected = expected_condition_ids or set(outcomes_by_condition)
            if all_ok and expected and expected.issubset(covered):
                return path, city_rows
    return latest_partial or (None, [])


def _information_event(row: dict[str, Any], path: Path) -> dict[str, Any] | None:
    if str(row.get("city") or "") != CITY or str(row.get("source") or "") != "knmi":
        return None
    event_id = str(row.get("information_event_id") or "")
    if event_id:
        if not bool(row.get("material_state_change", True)):
            return None
        return {
            key: row.get(key)
            for key in (
                "information_event_id",
                "event_kind",
                "event_role",
                "source",
                "city",
                "station_id",
                "provider_item_id",
                "content_key",
                "payload_hash",
                "source_event_ts_utc",
                "first_seen_at_utc",
                "available_at_utc",
                "pit_lineage_class",
                "material_state_change",
            )
        }
    if str(row.get("knmi_revision_kind") or "initial") != "initial":
        return None
    return _legacy_knmi_event(row, path)


def read_new_events(
    source_path: Path,
    state: dict[str, Any],
    *,
    bootstrap_at_end: bool,
    now: datetime,
    max_event_age_seconds: float,
) -> list[dict[str, Any]]:
    size = source_path.stat().st_size
    if not state.get("source_initialized") and bootstrap_at_end:
        state["source_offset"] = size
        state["source_initialized"] = True
        return []
    offset = int(state.get("source_offset") or 0)
    if size < offset:
        raise RuntimeError(f"KNMI source file shrank: {source_path}")
    seen = set(state.get("seen_event_ids") or [])
    events = []
    with source_path.open("rb") as handle:
        handle.seek(offset)
        while True:
            line_start = handle.tell()
            line = handle.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                handle.seek(line_start)
                break
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            event = _information_event(row, source_path)
            if event is None:
                continue
            event_id = str(event["information_event_id"])
            anchor = parse_dt(event.get("first_seen_at_utc") or event.get("available_at_utc"))
            if anchor is None or event_id in seen:
                continue
            age = (now - anchor).total_seconds()
            if age > max_event_age_seconds:
                seen.add(event_id)
                continue
            events.append({**event, "capture_anchor_utc": utc_iso(anchor), "captured_offsets": []})
            seen.add(event_id)
        state["source_offset"] = handle.tell()
    state["source_initialized"] = True
    state["seen_event_ids"] = sorted(seen)[-10000:]
    return events


def _depth(levels: list[dict[str, Any]], best: float | None, distance: float, side: str) -> float | None:
    if best is None:
        return None
    if side == "ask":
        selected = [row for row in levels if float(row["price"]) <= best + distance + 1e-9]
    else:
        selected = [row for row in levels if float(row["price"]) >= best - distance - 1e-9]
    return round(sum(float(row["size"]) for row in selected), 8)


def _base_records(path: Path | None, target_date: str) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = read_json(path)
    return {
        str(row.get("condition_id") or row.get("bracket") or ""): dict(row)
        for row in payload.get("records") or []
        if str(row.get("city") or "") == CITY
        and str(row.get("target_date") or row.get("event_date") or "") == target_date
    }


def _record_for_token(
    token: MarketToken,
    base: dict[str, dict[str, Any]],
    *,
    capture_ts: str,
) -> dict[str, Any]:
    row = deepcopy(base.get(token.condition_id) or base.get(token.bracket) or {})
    row.update(
        {
            "city": CITY,
            "target_date": token.target_date,
            "event_date": token.target_date,
            "market_local_date": token.target_date,
            "bracket": token.bracket,
            "question": token.question,
            "event_slug": token.event_slug,
            "market_id": token.market_id,
            "condition_id": token.condition_id,
            "yes_token_id": token.yes_token_id,
            "no_token_id": token.no_token_id,
            "snapshot_ts_utc": capture_ts,
            "ts_utc": capture_ts,
            "collection_started_at_utc": capture_ts,
            "record_type": "knmi_first_seen_full_ladder_quote",
        }
    )
    return row


def _apply_book(row: dict[str, Any], side: str, result: dict[str, Any]) -> None:
    summary = dict(result.get("summary") or {})
    bids = list(summary.get("bids") or [])
    asks = list(summary.get("asks") or [])
    best_bid = summary.get("best_bid")
    best_ask = summary.get("best_ask")
    row.update(
        {
            f"{side}_book_status": result.get("status"),
            f"{side}_book_fetched_at_utc": result.get("fetched_at_utc"),
            f"{side}_best_bid": best_bid,
            f"{side}_best_ask": best_ask,
            f"{side}_bid_size": summary.get("bid_size"),
            f"{side}_ask_size": summary.get("ask_size"),
            f"{side}_spread": summary.get("spread"),
            f"{side}_book_bids": bids,
            f"{side}_book_asks": asks,
            f"{side}_depth_ask_5c": _depth(asks, best_ask, 0.05, "ask"),
            f"{side}_depth_ask_10c": _depth(asks, best_ask, 0.10, "ask"),
            f"{side}_depth_bid_5c": _depth(bids, best_bid, 0.05, "bid"),
            f"{side}_depth_bid_10c": _depth(bids, best_bid, 0.10, "bid"),
            f"{side}_book_error": result.get("error"),
            f"{side}_book_fetch_attempt_count": result.get("fetch_attempt_count", 1),
            f"{side}_book_fetch_attempt_statuses": result.get(
                "fetch_attempt_statuses",
                [result.get("status")],
            ),
        }
    )


def _fetch_book_with_retries(
    book_fetcher: Callable[..., dict[str, Any]],
    token_id: str,
    *,
    market_proxy: str,
    top_n: int,
) -> dict[str, Any]:
    statuses = []
    result: dict[str, Any] = {}
    for attempt in range(BOOK_FETCH_ATTEMPTS):
        result = book_fetcher(
            token_id,
            proxy=market_proxy,
            timeout_sec=6.0,
            top_n=top_n,
        )
        statuses.append(result.get("status"))
        if result.get("status") == "ok":
            break
        if attempt + 1 < BOOK_FETCH_ATTEMPTS:
            time.sleep(0.15)
    result = dict(result)
    result["fetch_attempt_count"] = len(statuses)
    result["fetch_attempt_statuses"] = statuses
    return result


def capture_snapshot(
    event: dict[str, Any],
    offset_seconds: int,
    *,
    paper_snapshot_root: Path,
    market_proxy: str,
    max_workers: int,
    top_n: int,
    book_fetcher: Callable[..., dict[str, Any]] = fetch_fresh_book,
    now: datetime | None = None,
) -> dict[str, Any]:
    capture_started = now or datetime.now(timezone.utc)
    target_date = city_local_date(CITY, capture_started).isoformat()
    base_path = latest_snapshot(paper_snapshot_root)
    index = build_market_index(base_path, {target_date}, "max")
    index = augment_market_index_from_gamma(
        index,
        target_dates={target_date},
        cities={CITY},
        event_slugs={CITY: temperature_event_slug(CITY, target_date, "max")},
        market_proxy=market_proxy,
    )
    tokens = ordered_market_tokens(index, CITY, target_date)
    if not tokens:
        raise RuntimeError(f"no Amsterdam complete-ladder markets for {target_date}")
    base = _base_records(base_path, target_date)
    capture_ts = utc_iso(capture_started)
    books: dict[tuple[str, str], dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {}
        for token in tokens:
            for side, token_id in (("yes", token.yes_token_id), ("no", token.no_token_id)):
                futures[
                    executor.submit(
                        _fetch_book_with_retries,
                        book_fetcher,
                        token_id,
                        market_proxy=market_proxy,
                        top_n=top_n,
                    )
                ] = (token.condition_id, side)
        for future in as_completed(futures):
            books[futures[future]] = future.result()
    records = []
    ok_books = 0
    for token in tokens:
        row = _record_for_token(token, base, capture_ts=capture_ts)
        for side in ("yes", "no"):
            result = books.get((token.condition_id, side)) or {
                "status": "missing_result",
                "fetched_at_utc": utc_iso(),
                "summary": {},
            }
            ok_books += int(result.get("status") == "ok")
            _apply_book(row, side, result)
        records.append(row)
    completed = datetime.now(timezone.utc)
    anchor = parse_dt(event["capture_anchor_utc"])
    expected_books = len(tokens) * 2
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "knmi_first_seen_full_ladder_snapshot",
        "ts_utc": utc_iso(completed),
        "capture_started_at_utc": capture_ts,
        "source_event_id": event["information_event_id"],
        "source_event_first_seen_at_utc": event.get("first_seen_at_utc"),
        "source_event_available_at_utc": event.get("available_at_utc"),
        "source_event_observation_time_utc": event.get("source_event_ts_utc"),
        "scheduled_offset_seconds": offset_seconds,
        "actual_start_offset_seconds": (
            round((capture_started - anchor).total_seconds(), 6) if anchor else None
        ),
        "target_date": target_date,
        "base_paper_snapshot_path": str(base_path) if base_path else None,
        "ladder_market_count": len(tokens),
        "book_request_count": expected_books,
        "book_ok_count": ok_books,
        "capture_status": "complete" if ok_books == expected_books else "partial",
        "zero_notional": True,
        "no_order_placed": True,
        "records": records,
    }


def _capture_filename(event: dict[str, Any], offset_seconds: int, target_date: str) -> str:
    anchor = parse_dt(event["capture_anchor_utc"])
    stamp = anchor.strftime("%Y%m%d_%H%M%S_%f") if anchor else "unknown"
    return (
        f"snapshot_{stamp}_{str(event['information_event_id'])[:12]}"
        f"_t{offset_seconds:03d}_{target_date}.json"
    )


def register_pre_event(
    event: dict[str, Any],
    paper_root: Path,
    orderbook_root: Path,
    output_dir: Path,
) -> None:
    anchor = parse_dt(event["capture_anchor_utc"])
    pre_path = latest_snapshot_at_or_before(paper_root, anchor) if anchor else None
    target_date = city_local_date(CITY, anchor).isoformat() if anchor else ""
    expected_condition_ids = {
        str(row.get("condition_id") or "")
        for row in (_base_records(pre_path, target_date).values() if pre_path else [])
        if row.get("condition_id")
    }
    archive_path, archive_rows = (
        latest_orderbook_archive_at_or_before(
            orderbook_root,
            anchor,
            target_date,
            expected_condition_ids=expected_condition_ids,
        )
        if anchor
        else (None, [])
    )
    archive_ts = parse_dt(next(iter(archive_rows), {}).get("snapshot_ts_utc"))
    pre_snapshot_path = (
        output_dir
        / "pre_event_snapshots"
        / target_date
        / (
            f"pre_{str(event['information_event_id'])[:12]}_"
            f"{anchor.strftime('%Y%m%d_%H%M%S_%f')}.json"
        )
        if archive_rows and anchor
        else None
    )
    if pre_snapshot_path is not None:
        write_json(
            pre_snapshot_path,
            {
                "schema_version": SCHEMA_VERSION,
                "record_type": "knmi_first_seen_pre_event_full_ladder",
                "ts_utc": utc_iso(archive_ts),
                "source_event_id": event["information_event_id"],
                "source_event_first_seen_at_utc": event.get("first_seen_at_utc"),
                "scheduled_offset": "pre",
                "actual_offset_seconds": (
                    round((archive_ts - anchor).total_seconds(), 6)
                    if archive_ts is not None and anchor is not None
                    else None
                ),
                "source_orderbook_archive_path": str(archive_path),
                "base_paper_snapshot_path": str(pre_path) if pre_path else None,
                "ladder_market_count": len(
                    {str(row.get("condition_id") or "") for row in archive_rows}
                ),
                "book_ok_count": sum(str(row.get("status") or "") == "ok" for row in archive_rows),
                "zero_notional": True,
                "no_order_placed": True,
                "records": archive_rows,
            },
        )
    row = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "knmi_first_seen_pre_event_reference",
        "generated_at_utc": utc_iso(),
        "source_event_id": event["information_event_id"],
        "source_event_first_seen_at_utc": event.get("first_seen_at_utc"),
        "scheduled_offset": "pre",
        "paper_snapshot_path": str(pre_path) if pre_path else None,
        "paper_snapshot_status": "found" if pre_path else "missing",
        "orderbook_archive_path": str(archive_path) if archive_path else None,
        "full_ladder_snapshot_path": str(pre_snapshot_path) if pre_snapshot_path else None,
        "full_ladder_book_count": len(archive_rows),
        "full_ladder_status": "found" if archive_rows else "missing",
        "zero_notional": True,
        "no_order_placed": True,
    }
    append_jsonl(output_dir / "pre_event_references.jsonl", row)


def run_cycle(args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    source_path = Path(args.knmi_observations)
    output_dir = Path(args.output_dir)
    new_events = read_new_events(
        source_path,
        state,
        bootstrap_at_end=bool(args.bootstrap_at_end),
        now=now,
        max_event_age_seconds=float(args.max_new_event_age_seconds),
    )
    active = list(state.get("active_events") or [])
    for event in new_events:
        register_pre_event(
            event,
            Path(args.paper_snapshots),
            Path(args.orderbook_snapshots),
            output_dir,
        )
        append_jsonl(output_dir / "events.jsonl", event)
        active.append(event)
    captures = []
    remaining = []
    for event in active:
        anchor = parse_dt(event.get("capture_anchor_utc"))
        captured = {int(value) for value in event.get("captured_offsets") or []}
        if anchor is None:
            continue
        due = [
            value
            for value in CAPTURE_OFFSETS_SECONDS
            if value not in captured and now >= anchor + timedelta(seconds=value)
        ]
        for offset in due:
            payload = capture_snapshot(
                event,
                offset,
                paper_snapshot_root=Path(args.paper_snapshots),
                market_proxy=args.market_proxy,
                max_workers=int(args.max_workers),
                top_n=int(args.top_n),
            )
            target_date = str(payload["target_date"])
            path = output_dir / "snapshots" / target_date / _capture_filename(
                event, offset, target_date
            )
            write_json(path, payload)
            append_jsonl(
                output_dir / "captures.jsonl",
                {key: value for key, value in payload.items() if key != "records"}
                | {"snapshot_path": str(path)},
            )
            captured.add(offset)
            captures.append({"event_id": event["information_event_id"], "offset": offset, "path": str(path)})
        event["captured_offsets"] = sorted(captured)
        if len(captured) < len(CAPTURE_OFFSETS_SECONDS):
            remaining.append(event)
    state["active_events"] = remaining
    state["updated_at_utc"] = utc_iso()
    return {
        "status": "ok",
        "new_events": len(new_events),
        "captures": captures,
        "active_events": len(remaining),
        "zero_notional": True,
        "no_order_placed": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knmi-observations", required=True)
    parser.add_argument("--paper-snapshots", required=True)
    parser.add_argument("--orderbook-snapshots", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--state")
    parser.add_argument("--market-proxy", default="")
    parser.add_argument("--max-workers", type=int, default=16)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--max-new-event-age-seconds", type=float, default=60.0)
    parser.add_argument("--bootstrap-at-end", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state_path = Path(args.state or Path(args.output_dir) / "state.json")
    state = load_state(state_path)
    while True:
        result = run_cycle(args, state)
        write_json(state_path, state)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        if not args.loop:
            return 0
        time.sleep(max(0.2, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
