"""PIT all-event source-to-full-ladder panel materializer.

The panel keeps one row per source event and native market rung.  Quote slots
are wide columns so a missing pre/t0/markout book remains an evidence gap, not
a strategy filter.  This module is telemetry/research only and never creates
orders or fills.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import UTC, datetime
import gzip
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from weather_data_feed.city_calendar import city_local_date
from weather_data_feed.forecast_run_contract import stable_content_hash
from weather_clock_contract import parse_utc_or_none, utc_text as canonical_utc_text


PANEL_SCHEMA_VERSION = "first_seen_event_ladder_panel_v1"
SLOT_TARGET_SECONDS = {
    "pre": None,
    "t0": 0,
    "p30": 30,
    "p120": 120,
    "p300": 300,
    "next_official": None,
}
SLOT_TOLERANCE_SECONDS = {
    "pre": 300,
    "t0": 30,
    "p30": 30,
    "p120": 60,
    "p300": 120,
    "next_official": 30,
}


def parse_utc(value: Any) -> datetime | None:
    return parse_utc_or_none(value, field="first_seen_panel_clock")


def utc_text(value: datetime | None) -> str | None:
    return canonical_utc_text(value) if value else None


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value


def _event_anchor(row: Mapping[str, Any]) -> datetime | None:
    return parse_utc(
        row.get("first_seen_at_utc")
        or row.get("source_first_seen_at_utc")
        or row.get("local_detect_ts_utc")
        or row.get("available_at_utc")
    )


def normalize_source_event(
    row: Mapping[str, Any],
    *,
    city: str,
    source: str,
) -> dict[str, Any] | None:
    if str(row.get("city") or "") != city or str(row.get("source") or "") != source:
        return None
    if row.get("material_state_change") is False:
        return None
    if str(row.get("information_event_status") or "material") not in {"", "material"}:
        return None
    anchor = _event_anchor(row)
    observed = parse_utc(
        row.get("source_event_ts_utc") or row.get("observation_time_utc")
    )
    if anchor is None or observed is None:
        return None
    target_date = str(row.get("target_date") or city_local_date(city, observed))
    event_id = str(row.get("information_event_id") or "")
    if not event_id:
        event_id = stable_content_hash(
            {
                "city": city,
                "source": source,
                "station": row.get("station_id") or row.get("station"),
                "observed_at_utc": utc_text(observed),
                "payload_hash": row.get("payload_hash") or row.get("raw_payload_hash"),
            }
        )
    return {
        "event_id": event_id,
        "city": city,
        "source": source,
        "target_date": target_date,
        "station_id": row.get("station_id") or row.get("station"),
        "observed_at_utc": utc_text(observed),
        "first_seen_at_utc": utc_text(anchor),
        "available_at_utc": row.get("available_at_utc"),
        "detected_at_utc": row.get("detected_at_utc") or row.get("local_detect_ts_utc"),
        "published_at_utc": row.get("source_published_at_utc") or row.get("published_at_utc"),
        "pit_lineage_class": row.get("pit_lineage_class"),
        "producer_contract": row.get("producer_contract"),
        "provider_item_id": row.get("provider_item_id"),
        "content_key": row.get("content_key"),
        "payload_hash": row.get("payload_hash") or row.get("raw_payload_hash"),
        "temp_c": row.get("temp_c"),
        "temp_f": row.get("temp_f"),
        "wind_speed_kt": row.get("wind_speed_kt"),
    }


def load_source_events(
    paths: Iterable[Path],
    *,
    city: str,
    source: str,
    start_date: str,
    end_date: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts: Counter[str] = Counter()
    by_id: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        counts["input_files"] += 1
        for row in _read_jsonl(path):
            counts["raw_rows"] += 1
            event = normalize_source_event(row, city=city, source=source)
            if event is None:
                continue
            counts["city_source_material_rows"] += 1
            if not start_date <= event["target_date"] <= end_date:
                continue
            counts["date_window_rows"] += 1
            existing = by_id.get(event["event_id"])
            if existing is None or str(event["first_seen_at_utc"]) < str(existing["first_seen_at_utc"]):
                by_id[event["event_id"]] = event
    events = sorted(by_id.values(), key=lambda item: (item["first_seen_at_utc"], item["event_id"]))
    counts["unique_events"] = len(events)
    counts["target_dates"] = len({event["target_date"] for event in events})
    return events, dict(counts)


def load_collector_exact_metar_reports(
    paths: Iterable[Path],
    *,
    city: str,
    start_date: str,
    end_date: str,
    allowed_sources: set[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Recover report-grain METAR events from append-only source-event raw.

    Observation-cache daily snapshots are a state product and may stop being
    partitioned even while the source-event journal remains healthy.  Grouping
    by report clock and keeping the earliest collector first-seen preserves the
    actual PIT report event without treating later polling revisions as new
    observations.
    """
    sources = allowed_sources or {"aviationweather_metar"}
    counts: Counter[str] = Counter()
    by_report: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        counts["input_files"] += 1
        for row in _read_jsonl(path):
            counts["raw_rows"] += 1
            target_date = str(row.get("target_date") or "")
            if (
                str(row.get("city") or "") != city
                or str(row.get("source") or "") not in sources
                or str(row.get("status") or "") != "ok"
                or str(row.get("pit_lineage_class") or "") != "collector_exact"
                or row.get("original_first_seen_unknown") is True
                or not start_date <= target_date <= end_date
            ):
                continue
            report = parse_utc(row.get("source_report_ts_utc") or row.get("source_event_ts_utc"))
            first_seen = _event_anchor(row)
            temp_c = _finite(row.get("temp_c"))
            if report is None or first_seen is None or temp_c is None:
                counts["invalid_report_rows"] += 1
                continue
            counts["eligible_raw_rows"] += 1
            station = str(row.get("station_id") or row.get("station") or "")
            key = (target_date, station, utc_text(report) or "")
            value = {
                "target_date": target_date,
                "city": city,
                "source": str(row.get("source") or ""),
                "station": station,
                "valid_utc": pd.Timestamp(report),
                "decision_ts_utc": pd.Timestamp(first_seen),
                "temp_round_c": temp_c,
                "temp_c": temp_c,
                "raw_metar": row.get("raw_metar"),
                "information_event_id": row.get("information_event_id"),
                "pit_lineage_class": row.get("pit_lineage_class"),
                "content_key": row.get("content_key"),
            }
            previous = by_report.get(key)
            if previous is None or value["decision_ts_utc"] < previous["decision_ts_utc"]:
                by_report[key] = value
    frame = pd.DataFrame(by_report.values())
    if not frame.empty:
        frame = frame.sort_values(["target_date", "decision_ts_utc", "valid_utc"]).reset_index(drop=True)
    counts["unique_reports"] = len(frame)
    counts["target_dates"] = int(frame["target_date"].nunique()) if not frame.empty else 0
    counts["collector_exact_reports"] = int(
        frame["pit_lineage_class"].eq("collector_exact").sum()
    ) if not frame.empty else 0
    return frame, dict(counts)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _effective_yes_quote(
    yes_book: Mapping[str, Any] | None,
    no_book: Mapping[str, Any] | None,
) -> dict[str, Any]:
    yes_summary = dict((yes_book or {}).get("summary") or {})
    no_summary = dict((no_book or {}).get("summary") or {})
    yes_bid = _finite(yes_summary.get("best_bid"))
    yes_ask = _finite(yes_summary.get("best_ask"))
    no_bid = _finite(no_summary.get("best_bid"))
    no_ask = _finite(no_summary.get("best_ask"))
    bid_candidates = [
        pair
        for pair in (
            (yes_bid, _finite(yes_summary.get("bid_size"))),
            (1.0 - no_ask, _finite(no_summary.get("ask_size")))
            if no_ask is not None
            else None,
        )
        if pair is not None and pair[0] is not None
    ]
    ask_candidates = [
        pair
        for pair in (
            (yes_ask, _finite(yes_summary.get("ask_size"))),
            (1.0 - no_bid, _finite(no_summary.get("bid_size")))
            if no_bid is not None
            else None,
        )
        if pair is not None and pair[0] is not None
    ]
    bid, bid_size = max(bid_candidates, key=lambda pair: pair[0]) if bid_candidates else (None, None)
    ask, ask_size = min(ask_candidates, key=lambda pair: pair[0]) if ask_candidates else (None, None)
    if bid is not None and ask is not None and ask < bid - 1e-9:
        bid = ask = None
        bid_size = ask_size = None
    return {
        "yes_bid": bid,
        "yes_ask": ask,
        "yes_mid": (bid + ask) / 2.0 if bid is not None and ask is not None else None,
        "yes_spread": ask - bid if bid is not None and ask is not None else None,
        "yes_bid_size": bid_size,
        "yes_ask_size": ask_size,
    }


def _book_clock_exact(row: Mapping[str, Any] | None, published_at: datetime) -> bool:
    if row is None or row.get("status") != "ok" or row.get("event_time_pit_scorable") is not True:
        return False
    request = parse_utc(row.get("request_started_at_utc"))
    response = parse_utc(row.get("response_received_at_utc"))
    parsed = parse_utc(row.get("parsed_at_utc"))
    return bool(request and response and parsed and request <= response <= parsed <= published_at)


def _prefixed_book_clock_exact(
    row: Mapping[str, Any],
    prefix: str,
    published_at: datetime,
) -> bool:
    if row.get(f"{prefix}_book_status") != "ok":
        return False
    request = parse_utc(row.get(f"{prefix}_book_request_started_at_utc"))
    response = parse_utc(row.get(f"{prefix}_book_response_received_at_utc"))
    parsed = parse_utc(row.get(f"{prefix}_book_parsed_at_utc"))
    return bool(request and response and parsed and request <= response <= parsed <= published_at)


def _market_book_path(books_root: Path, ladder_path: Path) -> Path | None:
    stem = ladder_path.stem.replace("market_ladder_snapshot_", "market_books_", 1)
    day = ladder_path.parent.name
    candidates = (
        books_root / "batches" / day / f"{stem}.jsonl.gz",
        books_root / "batches" / day / f"{stem}.jsonl",
    )
    return next((path for path in candidates if path.exists()), None)


def load_canonical_ladder_snapshots(
    *,
    ladder_root: Path,
    books_root: Path,
    city: str,
    start_date: str,
    end_date: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    snapshots: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for path in sorted(ladder_root.glob("20??-??-??/market_ladder_snapshot_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        published = parse_utc(payload.get("available_at_utc"))
        if published is None:
            counts["missing_published_clock_files"] += 1
            continue
        relevant = [
            item
            for item in payload.get("records") or []
            if isinstance(item, dict)
            and str(item.get("city") or "") == city
            and start_date <= str(item.get("target_date") or "") <= end_date
        ]
        if not relevant:
            continue
        counts["ladder_files"] += 1
        book_path = _market_book_path(books_root, path)
        books = list(_read_jsonl(book_path)) if book_path else []
        book_index = {str(row.get("book_capture_id") or ""): row for row in books}
        for event in relevant:
            rungs: list[dict[str, Any]] = []
            exact = bool(book_path and event.get("rungs"))
            for rung in event.get("rungs") or []:
                yes = book_index.get(str(rung.get("yes_book_capture_id") or ""))
                no = book_index.get(str(rung.get("no_book_capture_id") or ""))
                exact = exact and _book_clock_exact(yes, published) and _book_clock_exact(no, published)
                rungs.append(
                    {
                        "bracket": str(rung.get("bracket") or ""),
                        "condition_id": str(rung.get("condition_id") or ""),
                        "market_id": str(rung.get("market_id") or ""),
                        "yes_token_id": str(rung.get("yes_token_id") or ""),
                        "no_token_id": str(rung.get("no_token_id") or ""),
                        **_effective_yes_quote(yes, no),
                    }
                )
            snapshots.append(
                {
                    "snapshot_id": stable_content_hash(
                        {
                            "batch_capture_id": payload.get("batch_capture_id"),
                            "event_id": event.get("event_id"),
                            "city": city,
                            "target_date": event.get("target_date"),
                        }
                    ),
                    "city": city,
                    "target_date": str(event.get("target_date") or ""),
                    "event_slug": event.get("event_slug"),
                    "available_at_utc": utc_text(published),
                    "clock_lineage_status": (
                        "collector_exact_market_books_clock" if exact else "missing_or_incomplete_market_books_clock"
                    ),
                    "event_time_pit_scorable": bool(exact),
                    "source_contract": "canonical_market_books_v1",
                    "source_path": str(path),
                    "source_book_path": str(book_path) if book_path else None,
                    "rungs": rungs,
                }
            )
            counts["snapshots"] += 1
            counts["rungs"] += len(rungs)
            counts["exact_clock_snapshots"] += int(exact)
    snapshots.sort(key=lambda item: (item["available_at_utc"], item["snapshot_id"]))
    counts["target_dates"] = len({row["target_date"] for row in snapshots})
    return snapshots, dict(counts)


def load_direct_event_snapshots(
    root: Path,
    *,
    city: str,
    start_date: str,
    end_date: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Load dedicated first-seen ladder captures such as Amsterdam/KNMI."""
    snapshots: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for path in sorted(root.glob("pre_event_snapshots/20??-??-??/pre_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_rows = [row for row in payload.get("records") or [] if isinstance(row, dict)]
        target_date = str(
            next(iter(raw_rows), {}).get("target_date")
            or next(iter(raw_rows), {}).get("event_date")
            or ""
        )
        if not start_date <= target_date <= end_date:
            continue
        grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in raw_rows:
            if str(row.get("city") or "") != city:
                continue
            outcome = str(row.get("outcome") or "").lower()
            if outcome in {"yes", "no"}:
                grouped[str(row.get("condition_id") or row.get("bracket") or "")][outcome] = row
        if not grouped:
            continue
        response_clocks = [
            parse_utc(row.get("response_received_at_utc") or row.get("fetched_at_utc"))
            for row in raw_rows
        ]
        available = max((value for value in response_clocks if value is not None), default=None)
        if available is None:
            available = parse_utc(payload.get("ts_utc"))
        if available is None:
            continue
        rungs = []
        exact = True
        for sides in grouped.values():
            yes, no = sides.get("yes"), sides.get("no")
            exact = exact and _book_clock_exact(yes, available) and _book_clock_exact(no, available)
            identity = yes or no or {}
            rungs.append(
                {
                    "bracket": str(identity.get("bracket") or ""),
                    "condition_id": str(identity.get("condition_id") or ""),
                    "market_id": str(identity.get("market_id") or ""),
                    "yes_token_id": str((yes or {}).get("token_id") or ""),
                    "no_token_id": str((no or {}).get("token_id") or ""),
                    **_effective_yes_quote(yes, no),
                }
            )
        snapshots.append(
            {
                "snapshot_id": stable_content_hash({"source_path": str(path), "available_at_utc": utc_text(available)}),
                "source_event_id": payload.get("source_event_id"),
                "scheduled_offset_seconds": "pre",
                "city": city,
                "target_date": target_date,
                "available_at_utc": utc_text(available),
                "clock_lineage_status": (
                    "collector_exact_market_books_clock" if exact else "missing_or_incomplete_market_books_clock"
                ),
                "event_time_pit_scorable": bool(exact),
                "source_contract": "dedicated_first_seen_pre_event_full_ladder_v1",
                "source_path": str(path),
                "rungs": rungs,
            }
        )
        counts["pre_snapshots"] += 1
        counts["rungs"] += len(rungs)
        counts["exact_clock_snapshots"] += int(exact)
    for path in sorted(root.glob("snapshots/20??-??-??/snapshot_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        target_date = str(payload.get("target_date") or "")
        if not start_date <= target_date <= end_date:
            continue
        # A completed multi-book snapshot is not available at its start clock.
        # Prefer the payload completion clock; the start clock is legacy-only.
        available = parse_utc(payload.get("ts_utc") or payload.get("capture_started_at_utc"))
        if available is None:
            continue
        rungs = []
        exact = True
        for row in payload.get("records") or []:
            if str(row.get("city") or "") != city:
                continue
            exact = exact and (
                _prefixed_book_clock_exact(row, "yes", available)
                and _prefixed_book_clock_exact(row, "no", available)
            )
            quote = _effective_yes_quote(
                {
                    "summary": {
                        "best_bid": row.get("yes_best_bid"),
                        "best_ask": row.get("yes_best_ask"),
                        "bid_size": row.get("yes_bid_size"),
                        "ask_size": row.get("yes_ask_size"),
                    }
                },
                {
                    "summary": {
                        "best_bid": row.get("no_best_bid"),
                        "best_ask": row.get("no_best_ask"),
                        "bid_size": row.get("no_bid_size"),
                        "ask_size": row.get("no_ask_size"),
                    }
                },
            )
            rungs.append(
                {
                    "bracket": str(row.get("bracket") or ""),
                    "condition_id": str(row.get("condition_id") or ""),
                    "market_id": str(row.get("market_id") or ""),
                    "yes_token_id": str(row.get("yes_token_id") or ""),
                    "no_token_id": str(row.get("no_token_id") or ""),
                    **quote,
                }
            )
        if not rungs:
            continue
        snapshots.append(
            {
                "snapshot_id": stable_content_hash({"source_path": str(path), "available_at_utc": utc_text(available)}),
                "source_event_id": payload.get("source_event_id"),
                "scheduled_offset_seconds": payload.get("scheduled_offset_seconds"),
                "city": city,
                "target_date": target_date,
                "available_at_utc": utc_text(available),
                "clock_lineage_status": (
                    "collector_exact_market_books_clock" if exact else "legacy_missing_response_clock"
                ),
                "event_time_pit_scorable": bool(exact),
                "source_contract": "dedicated_first_seen_full_ladder_v1",
                "source_path": str(path),
                "rungs": rungs,
            }
        )
        counts["snapshots"] += 1
        counts["rungs"] += len(rungs)
        counts["exact_clock_snapshots"] += int(exact)
    snapshots.sort(key=lambda item: (item["available_at_utc"], item["snapshot_id"]))
    counts["target_dates"] = len({row["target_date"] for row in snapshots})
    return snapshots, dict(counts)


def _select_snapshot(
    snapshots: list[dict[str, Any]],
    anchor: datetime,
    *,
    slot: str,
    next_anchor: datetime | None,
) -> tuple[dict[str, Any] | None, float | None, str]:
    clocks = [parse_utc(row["available_at_utc"]) for row in snapshots]
    valid_clocks = [value for value in clocks if value is not None]
    if len(valid_clocks) != len(snapshots):
        raise ValueError("snapshot list contains a missing availability clock")
    if slot == "pre":
        position = bisect_left(valid_clocks, anchor) - 1
        if position < 0:
            return None, None, "no_pre_snapshot"
        delta = (valid_clocks[position] - anchor).total_seconds()
        if abs(delta) > SLOT_TOLERANCE_SECONDS[slot]:
            return None, delta, "pre_snapshot_too_old"
        return snapshots[position], delta, "found"
    if slot == "next_official" and next_anchor is None:
        return None, None, "no_next_official_event"
    target = next_anchor if slot == "next_official" else anchor.timestamp() + float(SLOT_TARGET_SECONDS[slot])
    target_dt = target if isinstance(target, datetime) else datetime.fromtimestamp(target, tz=UTC)
    position = bisect_left(valid_clocks, target_dt)
    if position >= len(snapshots):
        return None, None, "no_post_snapshot"
    delta = (valid_clocks[position] - anchor).total_seconds()
    target_delta = (target_dt - anchor).total_seconds()
    if delta - target_delta > SLOT_TOLERANCE_SECONDS[slot]:
        return None, delta, "snapshot_outside_slot_tolerance"
    return snapshots[position], delta, "found"


def materialize_panel(
    events: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snapshot in snapshots:
        by_date[str(snapshot["target_date"])].append(snapshot)
    for values in by_date.values():
        values.sort(key=lambda item: (item["available_at_utc"], item["snapshot_id"]))
    next_event_by_id: dict[str, dict[str, Any] | None] = {}
    by_stream: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_stream[(event["city"], event["source"], event["target_date"])].append(event)
    for stream in by_stream.values():
        stream.sort(key=lambda item: item["first_seen_at_utc"])
        for index, event in enumerate(stream):
            next_event_by_id[event["event_id"]] = stream[index + 1] if index + 1 < len(stream) else None

    event_rows: list[dict[str, Any]] = []
    panel_rows: list[dict[str, Any]] = []
    slot_counts: Counter[str] = Counter()
    for event in events:
        anchor = parse_utc(event["first_seen_at_utc"])
        if anchor is None:
            continue
        next_event = next_event_by_id.get(event["event_id"])
        next_anchor = parse_utc((next_event or {}).get("first_seen_at_utc"))
        candidates = by_date.get(event["target_date"], [])
        selected: dict[str, dict[str, Any] | None] = {}
        event_row = dict(event)
        event_row["next_official_event_id"] = (next_event or {}).get("event_id")
        event_row["next_official_first_seen_at_utc"] = (next_event or {}).get("first_seen_at_utc")
        for slot in SLOT_TARGET_SECONDS:
            snapshot, delta, status = _select_snapshot(
                candidates,
                anchor,
                slot=slot,
                next_anchor=next_anchor,
            )
            selected[slot] = snapshot
            event_row[f"{slot}_status"] = status
            event_row[f"{slot}_actual_offset_seconds"] = delta
            event_row[f"{slot}_snapshot_id"] = (snapshot or {}).get("snapshot_id")
            event_row[f"{slot}_event_time_pit_scorable"] = bool(
                snapshot and snapshot.get("event_time_pit_scorable")
            )
            slot_counts[f"{slot}_found"] += int(snapshot is not None)
            slot_counts[f"{slot}_exact_clock"] += int(
                bool(snapshot and snapshot.get("event_time_pit_scorable"))
            )
        event_rows.append(event_row)

        rung_identity: dict[str, dict[str, Any]] = {}
        for snapshot in selected.values():
            for rung in (snapshot or {}).get("rungs") or []:
                key = str(rung.get("condition_id") or rung.get("bracket") or "")
                if key:
                    rung_identity.setdefault(key, rung)
        for rung_key, identity in sorted(rung_identity.items(), key=lambda item: str(item[1].get("bracket"))):
            row = {
                **{key: value for key, value in event.items() if key != "producer_contract"},
                "condition_id": identity.get("condition_id"),
                "bracket": identity.get("bracket"),
                "market_id": identity.get("market_id"),
                "yes_token_id": identity.get("yes_token_id"),
                "no_token_id": identity.get("no_token_id"),
            }
            for slot, snapshot in selected.items():
                slot_rungs = list((snapshot or {}).get("rungs") or [])
                slot_mids = [_finite(rung.get("yes_mid")) for rung in slot_rungs]
                market_complete = bool(slot_rungs) and all(value is not None for value in slot_mids)
                market_total = sum(float(value) for value in slot_mids if value is not None)
                match = next(
                    (
                        rung
                        for rung in (snapshot or {}).get("rungs") or []
                        if str(rung.get("condition_id") or rung.get("bracket") or "") == rung_key
                    ),
                    None,
                )
                row[f"{slot}_snapshot_id"] = (snapshot or {}).get("snapshot_id")
                row[f"{slot}_clock_exact"] = bool(snapshot and snapshot.get("event_time_pit_scorable"))
                row[f"{slot}_actual_offset_seconds"] = event_row[f"{slot}_actual_offset_seconds"]
                row[f"{slot}_market_distribution_complete"] = bool(
                    market_complete and market_total > 0
                )
                for field in (
                    "yes_bid", "yes_ask", "yes_mid", "yes_spread", "yes_bid_size", "yes_ask_size"
                ):
                    row[f"{slot}_{field}"] = (match or {}).get(field)
                match_mid = _finite((match or {}).get("yes_mid"))
                row[f"{slot}_market_probability"] = (
                    match_mid / market_total
                    if match_mid is not None and market_complete and market_total > 0
                    else None
                )
            panel_rows.append(row)

    events_frame = pd.DataFrame(event_rows)
    panel = pd.DataFrame(panel_rows)
    if not panel.empty:
        for slot in ("p30", "p120", "p300", "next_official"):
            panel[f"{slot}_market_probability_change_from_t0"] = (
                panel[f"{slot}_market_probability"] - panel["t0_market_probability"]
            )
            panel[f"{slot}_yes_mid_change_from_t0"] = (
                panel[f"{slot}_yes_mid"] - panel["t0_yes_mid"]
            )
    event_count = len(event_rows)
    coverage = {
        "schema_version": PANEL_SCHEMA_VERSION,
        "signal_funnel": {
            "material_unique_events": len(events),
            "target_dates": len({event["target_date"] for event in events}),
            "panel_events": event_count,
            "panel_event_rungs": len(panel_rows),
        },
        "evidence_funnel": {
            **dict(slot_counts),
            "events_with_all_markouts": sum(
                all(row.get(f"{slot}_status") == "found" for slot in ("pre", "t0", "p30", "p120", "p300"))
                for row in event_rows
            ),
            "events_with_all_markouts_exact_clock": sum(
                all(row.get(f"{slot}_event_time_pit_scorable") for slot in ("pre", "t0", "p30", "p120", "p300"))
                for row in event_rows
            ),
        },
        "slot_coverage_rate": {
            slot: (slot_counts[f"{slot}_found"] / event_count if event_count else None)
            for slot in SLOT_TARGET_SECONDS
        },
        "slot_exact_clock_rate": {
            slot: (slot_counts[f"{slot}_exact_clock"] / event_count if event_count else None)
            for slot in SLOT_TARGET_SECONDS
        },
        "zero_notional": True,
        "no_order_placed": True,
    }
    return panel, events_frame, coverage
