"""Project raw market-book batches into canonical exact-ladder snapshots.

The market-book collector has retained complete event ladders longer than the
``strategy_snapshots`` consumption view.  This adapter groups the append-only
YES/NO book captures at their recorded batch boundary.  It preserves direct
token quotes and uses the latest response/fetch clock in each ladder as the
conservative PIT availability time.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import gzip
import json
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from weather_data_feed.source_registry import load_source_profiles
from weather_clock_contract import parse_utc_or_none


FULL_LADDER_REASONS = {"scheduled_full_ladder_snapshot", "full_market_ladder"}


def _utc(value: Any) -> datetime | None:
    return parse_utc_or_none(value, field="market_ladder_timestamp")


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _summary_fields(row: dict[str, Any], prefix: str) -> dict[str, Any]:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    return {
        f"{prefix}_token_id": _text(row.get("token_id")),
        f"{prefix}_best_bid": summary.get("best_bid"),
        f"{prefix}_best_ask": summary.get("best_ask"),
        f"{prefix}_bid_size": summary.get("bid_size"),
        f"{prefix}_ask_size": summary.get("ask_size"),
        f"{prefix}_depth_bid_5c": summary.get("depth_bid_5c"),
        f"{prefix}_depth_ask_5c": summary.get("depth_ask_5c"),
        f"{prefix}_depth_bid_10c": summary.get("depth_bid_10c"),
        f"{prefix}_depth_ask_10c": summary.get("depth_ask_10c"),
        f"{prefix}_book_status": _text(row.get("status")),
        f"{prefix}_book_fetched_at_utc": (
            _text(row.get("available_at_utc"))
            or _text(row.get("response_received_at_utc"))
            or _text(row.get("fetched_at_utc"))
        ),
    }


def _capture_directories(root: Path, start: str, end: str) -> list[Path]:
    first = datetime.fromisoformat(start).date() - timedelta(days=2)
    last = datetime.fromisoformat(end).date() + timedelta(days=2)
    output = []
    current = first
    while current <= last:
        path = root / current.isoformat()
        if path.is_dir():
            output.append(path)
        current += timedelta(days=1)
    return output


def _group_key(path: Path, row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    batch = _text(row.get("request_batch_capture_id")) or path.name
    city = _text(row.get("city")) or ""
    target_date = _text(row.get("event_date") or row.get("target_date")) or ""
    event = _text(row.get("event_slug")) or f"{city}|{target_date}"
    return batch, city, target_date, event, str(row.get("capture_reason") or "legacy_full_file")


def _iter_file_groups(path: Path) -> Iterable[tuple[tuple[str, str, str, str, str], list[dict[str, Any]]]]:
    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                reason = _text(row.get("capture_reason"))
                # Legacy orderbook_snapshot files are whole-ladder captures.
                # Modern files also contain hot subsets, which are not ladders.
                if reason is not None and reason not in FULL_LADDER_REASONS:
                    continue
                groups[_group_key(path, row)].append(row)
    except OSError:
        return
    yield from groups.items()


def iter_market_book_ladders(
    root: Path,
    start: str,
    end: str,
) -> Iterable[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Yield canonical-materializer metadata and direct rung records."""

    profiles = load_source_profiles()
    for directory in _capture_directories(root, start, end):
        for path in sorted(directory.glob("*.jsonl.gz")):
            for (_, city, target_date, event, reason), rows in _iter_file_groups(path):
                if not city or not target_date or not (start <= target_date <= end):
                    continue
                profile = profiles.get(city)
                if profile is None:
                    continue
                by_bracket: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
                invalid = False
                clocks: list[datetime] = []
                for row in rows:
                    bracket = _text(row.get("bracket"))
                    outcome = str(row.get("outcome") or "").lower()
                    if not bracket or outcome not in {"yes", "no"} or outcome in by_bracket[bracket]:
                        invalid = True
                        break
                    by_bracket[bracket][outcome] = row
                    clock = _utc(
                        row.get("available_at_utc")
                        or row.get("response_received_at_utc")
                        or row.get("fetched_at_utc")
                        or row.get("snapshot_ts_utc")
                    )
                    if clock is not None:
                        clocks.append(clock)
                # Exact-bracket events in this collector have ten or eleven
                # native rungs.  Smaller modern groups are strategy-hot subsets.
                if invalid or len(by_bracket) < 8 or not clocks:
                    continue
                available = max(clocks)
                local = available.astimezone(ZoneInfo(profile.timezone_name))
                offset = int((local.utcoffset() or timedelta()).total_seconds())
                records: list[dict[str, Any]] = []
                for bracket, pair in by_bracket.items():
                    # Near resolution the exchange may stop returning one of
                    # the complementary token books even though the collector
                    # still captured the complete native bracket lattice.  A
                    # missing complementary book is quote availability, not a
                    # missing exact-bracket rung.  Preserve the present direct
                    # side and leave the absent side null; downstream PIT
                    # scoring can reconstruct a complementary bound only when
                    # the observed direct quote permits it.
                    yes = pair.get("yes", {})
                    no = pair.get("no", {})
                    present = list(pair.values())
                    condition_ids = {_text(row.get("condition_id")) for row in present}
                    market_ids = {_text(row.get("market_id")) for row in present}
                    if None in condition_ids or len(condition_ids) != 1 or None in market_ids or len(market_ids) != 1:
                        invalid = True
                        break
                    records.append(
                        {
                            "city": city,
                            "target_date": target_date,
                            "event_slug": event,
                            "bracket": bracket,
                            "condition_id": next(iter(condition_ids)),
                            "market_id": next(iter(market_ids)),
                            "unit": profile.unit,
                            "timezone_name": profile.timezone_name,
                            "forecast_utc_offset_seconds": offset,
                            "settlement_source_class": profile.settlement_source_class,
                            **_summary_fields(yes, "yes"),
                            **_summary_fields(no, "no"),
                        }
                    )
                if invalid:
                    continue
                timestamp = available.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                yield (
                    {
                        "source_system": "weather_market_books_full_ladder",
                        "source_path": str(path),
                        "source_snapshot_ts_utc": timestamp,
                        "available_at_utc": timestamp,
                        "city": city,
                        "target_date": target_date,
                        "event_slug": event,
                        "event_identity": event,
                        "source_capture_reason": reason,
                    },
                    records,
                )
