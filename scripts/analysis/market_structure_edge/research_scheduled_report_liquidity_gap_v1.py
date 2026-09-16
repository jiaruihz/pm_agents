#!/usr/bin/env python3
"""Measure weather-book liquidity around scheduled observation reports.

This is an offline, zero-notional market-microstructure study.  It aligns
point-in-time YES-token books to both the observation's nominal report time
and the first time that the configured source was seen locally.  The main
hypothesis is that liquidity providers withdraw shortly before a predictable
report, widening spreads and reducing top-of-book depth.

The script deliberately does not infer maker fills from future price touches.
It reports only the contemporaneous maker price-improvement upper bound
``ask - (bid + one_tick)``.  Real fill, queue position, and adverse selection
need a forward collector/order journal.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import gzip
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402
from weather_data_feed.production_paths import historical_orderbook_roots  # noqa: E402


DEFAULT_DENSE_SOURCES = ROOT / "runtime/weather_edge_v1/remote_pm_agent/source_orderbook_timing/sources.jsonl"
DEFAULT_DENSE_BOOKS = ROOT / "runtime/weather_edge_v1/remote_pm_agent/source_orderbook_timing/books.jsonl"
DEFAULT_CURRENT_ROOT = load_production_spec().data_feed_runtime_root
DEFAULT_CURRENT_SOURCES = load_production_spec().source_events_root() / "sources.jsonl"
DEFAULT_OUTPUT_DIR = ROOT / "docs/analysis/2026-07/generated/scheduled_report_liquidity_gap_v1"

UTC = dt.timezone.utc
TICK = 0.001
BIN_EDGES_MIN = {
    "pre_far": (-15.0, -8.0),
    "pre_near": (-8.0, 0.0),
    "post_near": (0.0, 8.0),
    "post_far": (8.0, 15.0),
}


def parse_ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso(value: dt.datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def median(values: Iterable[float | None]) -> float | None:
    cleaned = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.median(cleaned) if cleaned else None


def mean(values: Iterable[float | None]) -> float | None:
    cleaned = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return sum(cleaned) / len(cleaned) if cleaned else None


def percentile(values: Iterable[float | None], q: float) -> float | None:
    cleaned = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not cleaned:
        return None
    index = min(len(cleaned) - 1, max(0, round(q * (len(cleaned) - 1))))
    return cleaned[index]


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            if isinstance(row, dict):
                yield row


def iter_paths(paths: Iterable[Path]) -> Iterator[dict[str, Any]]:
    for path in paths:
        yield from iter_jsonl(path)


@dataclass(frozen=True)
class SourceEvent:
    city: str
    target_date: str
    report_ts: dt.datetime
    first_seen_ts: dt.datetime
    source: str

    @property
    def event_id(self) -> str:
        return f"{self.city}|{self.target_date}|{iso(self.report_ts)}"


@dataclass
class MetricAccumulator:
    rows: int = 0
    sums: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, values: dict[str, float | None]) -> None:
        self.rows += 1
        for key, value in values.items():
            if value is None or not math.isfinite(float(value)):
                continue
            self.sums[key] += float(value)
            self.counts[key] += 1

    def averages(self) -> dict[str, float | None]:
        return {
            key: (self.sums[key] / self.counts[key] if self.counts[key] else None)
            for key in set(self.sums) | set(self.counts)
        }


def load_source_events(path: Path, source_name: str) -> tuple[list[SourceEvent], dict[str, Any]]:
    earliest: dict[tuple[str, str, str], SourceEvent] = {}
    scanned = 0
    candidate_rows = 0
    source_marker = f'"source": "{source_name}"'
    status_marker = '"status": "ok"'
    with path.open("rt", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            scanned += 1
            if source_marker not in line or status_marker not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            city = str(row.get("city") or "")
            target_date = str(row.get("target_date") or "")
            report_ts = parse_ts(row.get("source_report_ts_utc"))
            detect_ts = parse_ts(row.get("local_detect_ts_utc") or row.get("ts_utc"))
            if not city or not target_date or report_ts is None or detect_ts is None:
                continue
            candidate_rows += 1
            key = (city, target_date, iso(report_ts))
            event = SourceEvent(city, target_date, report_ts, detect_ts, source_name)
            previous = earliest.get(key)
            if previous is None or event.first_seen_ts < previous.first_seen_ts:
                earliest[key] = event
    events = sorted(earliest.values(), key=lambda event: (event.city, event.target_date, event.report_ts))
    lags = [(event.first_seen_ts - event.report_ts).total_seconds() / 60.0 for event in events]
    return events, {
        "path": str(path),
        "rows_scanned": scanned,
        "source_rows": candidate_rows,
        "unique_events": len(events),
        "cities": len({event.city for event in events}),
        "dates": len({event.target_date for event in events}),
        "first_seen_lag_min_p25": percentile(lags, 0.25),
        "first_seen_lag_min_p50": percentile(lags, 0.50),
        "first_seen_lag_min_p75": percentile(lags, 0.75),
    }


def compact_book(row: dict[str, Any]) -> dict[str, Any] | None:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else row
    side = str(row.get("side") or row.get("outcome") or "").upper()
    if side != "YES" or row.get("status") != "ok":
        return None
    city = str(row.get("city") or "")
    target_date = str(row.get("target_date") or row.get("event_date") or "")
    token_id = str(row.get("token_id") or "")
    ts = parse_ts(
        row.get("book_fetch_end_utc")
        or row.get("fetched_at_utc")
        or row.get("snapshot_ts_utc")
        or row.get("ts_utc")
    )
    if not city or not target_date or not token_id or ts is None:
        return None
    bid = finite_float(summary.get("best_bid"))
    ask = finite_float(summary.get("best_ask"))
    bid_size = finite_float(summary.get("best_bid_size") if "best_bid_size" in summary else summary.get("bid_size"))
    ask_size = finite_float(summary.get("best_ask_size") if "best_ask_size" in summary else summary.get("ask_size"))
    return {
        "city": city,
        "target_date": target_date,
        "token_id": token_id,
        "bracket": row.get("bracket"),
        "ts": ts,
        "bid": bid,
        "ask": ask,
        "bid_size": bid_size,
        "ask_size": ask_size,
    }


def source_indexes(events: list[SourceEvent], anchor: str) -> dict[tuple[str, str], tuple[list[dt.datetime], list[SourceEvent]]]:
    grouped: dict[tuple[str, str], list[SourceEvent]] = defaultdict(list)
    for event in events:
        grouped[(event.city, event.target_date)].append(event)
    out: dict[tuple[str, str], tuple[list[dt.datetime], list[SourceEvent]]] = {}
    for key, rows in grouped.items():
        rows.sort(key=lambda event: event.report_ts if anchor == "report" else event.first_seen_ts)
        times = [event.report_ts if anchor == "report" else event.first_seen_ts for event in rows]
        out[key] = (times, rows)
    return out


def nearest_event(
    index: dict[tuple[str, str], tuple[list[dt.datetime], list[SourceEvent]]],
    city: str,
    target_date: str,
    ts: dt.datetime,
    anchor: str,
) -> tuple[SourceEvent, float] | None:
    payload = index.get((city, target_date))
    if payload is None:
        return None
    times, events = payload
    position = bisect.bisect_left(times, ts)
    candidates: list[tuple[float, SourceEvent]] = []
    for idx in (position - 1, position):
        if 0 <= idx < len(events):
            event = events[idx]
            anchor_ts = event.report_ts if anchor == "report" else event.first_seen_ts
            delta_min = (ts - anchor_ts).total_seconds() / 60.0
            candidates.append((abs(delta_min), event))
    if not candidates:
        return None
    _, event = min(candidates, key=lambda item: item[0])
    anchor_ts = event.report_ts if anchor == "report" else event.first_seen_ts
    return event, (ts - anchor_ts).total_seconds() / 60.0


def time_bin(delta_min: float) -> str | None:
    for name, (left, right) in BIN_EDGES_MIN.items():
        if left <= delta_min < right:
            return name
    return None


def active_tokens(book_paths: list[Path], cache_path: Path | None = None) -> tuple[set[str], dict[str, int]]:
    if cache_path is not None and cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        expected_paths = [str(path) for path in book_paths]
        if cached.get("paths") == expected_paths:
            return set(cached.get("active_tokens", [])), dict(cached.get("counts", {}))
    active: set[str] = set()
    counts = {"raw_rows": 0, "yes_ok_rows": 0, "two_sided_rows": 0}
    for row in iter_paths(book_paths):
        counts["raw_rows"] += 1
        book = compact_book(row)
        if book is None:
            continue
        counts["yes_ok_rows"] += 1
        bid = book["bid"]
        ask = book["ask"]
        if bid is None or ask is None or ask < bid:
            continue
        counts["two_sided_rows"] += 1
        midpoint = (bid + ask) / 2.0
        if 0.05 <= midpoint <= 0.95:
            active.add(book["token_id"])
    counts["active_tokens"] = len(active)
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {"paths": [str(path) for path in book_paths], "active_tokens": sorted(active), "counts": counts},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return active, counts


def block_bootstrap_ci(rows: list[dict[str, Any]], field: str, iterations: int = 2000) -> tuple[float | None, float | None]:
    by_date: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = finite_float(row.get(field))
        if value is not None:
            by_date[row["target_date"]].append(value)
    dates = sorted(by_date)
    if not dates:
        return None, None
    rng = random.Random(f"scheduled-report-liquidity-v1|{field}|{len(rows)}")
    samples: list[float] = []
    for _ in range(iterations):
        chosen = [rng.choice(dates) for _ in dates]
        values = [value for date in chosen for value in by_date[date]]
        if values:
            samples.append(sum(values) / len(values))
    return percentile(samples, 0.025), percentile(samples, 0.975)


def paired_delta(row: dict[str, Any], metric: str, left_bin: str, right_bin: str) -> float | None:
    left = finite_float(row.get(f"{metric}_{left_bin}"))
    right = finite_float(row.get(f"{metric}_{right_bin}"))
    if left is None or right is None:
        return None
    return left - right


def analyze_cohort(
    name: str,
    source_path: Path,
    book_paths: list[Path],
    source_name: str,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    events, source_stats = load_source_events(source_path, source_name)
    indexes = {anchor: source_indexes(events, anchor) for anchor in ("report", "detect")}
    cache_path = None if cache_dir is None else cache_dir / f"{name}_active_tokens_cache.json"
    active, book_stats = active_tokens(book_paths, cache_path)
    token_bins: dict[tuple[str, str, str, str], MetricAccumulator] = defaultdict(MetricAccumulator)
    aligned_rows = 0

    for row in iter_paths(book_paths):
        book = compact_book(row)
        if book is None or book["token_id"] not in active:
            continue
        bid = book["bid"]
        ask = book["ask"]
        bid_size = book["bid_size"]
        ask_size = book["ask_size"]
        two_sided = bid is not None and ask is not None and ask >= bid
        spread = (ask - bid) if two_sided else None
        improvement = max(0.0, spread - TICK) if spread is not None else None
        midpoint = (ask + bid) / 2.0 if two_sided else None
        min_depth = min(bid_size or 0.0, ask_size or 0.0) if two_sided else 0.0
        sum_depth = (bid_size or 0.0) + (ask_size or 0.0)
        values = {
            "two_sided_rate": 1.0 if two_sided else 0.0,
            "spread": spread,
            "maker_improvement": improvement,
            "min_top_depth": min_depth,
            "sum_top_depth": sum_depth,
            "bid": bid,
            "ask": ask,
            "mid": midpoint,
        }
        for anchor in ("report", "detect"):
            match = nearest_event(indexes[anchor], book["city"], book["target_date"], book["ts"], anchor)
            if match is None:
                continue
            event, delta_min = match
            bucket = time_bin(delta_min)
            if bucket is None:
                continue
            token_bins[(anchor, event.event_id, book["token_id"], bucket)].add(values)
            aligned_rows += 1

    events_by_id = {event.event_id: event for event in events}
    per_event_tokens: dict[tuple[str, str], dict[str, dict[str, dict[str, float | None]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for (anchor, event_id, token_id, bucket), accumulator in token_bins.items():
        per_event_tokens[(anchor, event_id)][token_id][bucket] = accumulator.averages()

    event_rows: list[dict[str, Any]] = []
    metrics = ["two_sided_rate", "spread", "maker_improvement", "min_top_depth", "sum_top_depth", "bid", "ask", "mid"]
    for (anchor, event_id), token_payload in sorted(per_event_tokens.items()):
        event = events_by_id[event_id]
        row: dict[str, Any] = {
            "cohort": name,
            "anchor": anchor,
            "event_id": event_id,
            "city": event.city,
            "target_date": event.target_date,
            "report_ts_utc": iso(event.report_ts),
            "first_seen_ts_utc": iso(event.first_seen_ts),
            "first_seen_lag_min": (event.first_seen_ts - event.report_ts).total_seconds() / 60.0,
            "tokens": len(token_payload),
        }
        for bucket in BIN_EDGES_MIN:
            bucket_tokens = [payload[bucket] for payload in token_payload.values() if bucket in payload]
            row[f"tokens_{bucket}"] = len(bucket_tokens)
            for metric in metrics:
                row[f"{metric}_{bucket}"] = median(payload.get(metric) for payload in bucket_tokens)
        for metric in metrics:
            row[f"{metric}_pre_near_minus_pre_far"] = paired_delta(row, metric, "pre_near", "pre_far")
            row[f"{metric}_post_near_minus_pre_near"] = paired_delta(row, metric, "post_near", "pre_near")
            row[f"{metric}_post_far_minus_pre_near"] = paired_delta(row, metric, "post_far", "pre_near")

        maker_markouts: list[float] = []
        taker_markouts: list[float] = []
        taker_net_markouts: list[float] = []
        for token_payload_by_bin in token_payload.values():
            pre = token_payload_by_bin.get("pre_near")
            post = token_payload_by_bin.get("post_far")
            if not pre or not post:
                continue
            bid = finite_float(pre.get("bid"))
            ask = finite_float(pre.get("ask"))
            post_mid = finite_float(post.get("mid"))
            if bid is None or ask is None or post_mid is None or ask <= bid + TICK:
                continue
            maker_limit = min(ask - TICK, bid + TICK)
            maker_markouts.append(post_mid - maker_limit)
            taker_markouts.append(post_mid - ask)
            taker_fee = 0.05 * ask * (1.0 - ask)
            taker_net_markouts.append(post_mid - ask - taker_fee)
        row["maker_markout_to_post_far_mid_upper_bound"] = median(maker_markouts)
        row["taker_markout_to_post_far_mid"] = median(taker_markouts)
        row["taker_fee_adjusted_markout_to_post_far_mid"] = median(taker_net_markouts)
        row["markout_tokens"] = len(maker_markouts)
        event_rows.append(row)

    delta_specs = [
        ("spread_pre_near_minus_pre_far", "pre-report spread widening"),
        ("maker_improvement_pre_near_minus_pre_far", "pre-report maker improvement"),
        ("min_top_depth_pre_near_minus_pre_far", "pre-report min-depth change"),
        ("two_sided_rate_pre_near_minus_pre_far", "pre-report two-sided quote change"),
        ("spread_post_far_minus_pre_near", "post-report spread recovery"),
        ("maker_markout_to_post_far_mid_upper_bound", "maker markout upper bound"),
        ("taker_fee_adjusted_markout_to_post_far_mid", "taker fee-adjusted markout"),
    ]
    overall: dict[str, Any] = {}
    by_city: list[dict[str, Any]] = []
    for anchor in ("report", "detect"):
        anchor_rows = [row for row in event_rows if row["anchor"] == anchor]
        payload: dict[str, Any] = {
            "events": len(anchor_rows),
            "cities": len({row["city"] for row in anchor_rows}),
            "dates": len({row["target_date"] for row in anchor_rows}),
        }
        for field_name, _ in delta_specs:
            values = [finite_float(row.get(field_name)) for row in anchor_rows]
            values = [value for value in values if value is not None]
            ci_low, ci_high = block_bootstrap_ci(anchor_rows, field_name)
            payload[field_name] = {
                "n": len(values),
                "mean": mean(values),
                "p50": median(values),
                "ci95_date_block": [ci_low, ci_high],
            }
        overall[anchor] = payload

        cities = sorted({row["city"] for row in anchor_rows})
        for city in cities:
            rows = [row for row in anchor_rows if row["city"] == city]
            city_row: dict[str, Any] = {
                "cohort": name,
                "anchor": anchor,
                "city": city,
                "events": len(rows),
                "dates": len({row["target_date"] for row in rows}),
                "first_seen_lag_min_p50": median(row["first_seen_lag_min"] for row in rows),
            }
            for field_name, _ in delta_specs:
                city_row[field_name] = mean(finite_float(row.get(field_name)) for row in rows)
                city_row[f"{field_name}_n"] = sum(finite_float(row.get(field_name)) is not None for row in rows)
                ci_low, ci_high = block_bootstrap_ci(rows, field_name)
                city_row[f"{field_name}_ci95_low"] = ci_low
                city_row[f"{field_name}_ci95_high"] = ci_high
            by_city.append(city_row)

    ankara_rows = [row for row in event_rows if row["city"] == "Ankara"]
    return {
        "name": name,
        "source_stats": source_stats,
        "book_stats": {**book_stats, "paths": [str(path) for path in book_paths], "aligned_rows": aligned_rows},
        "overall": overall,
        "event_rows": event_rows,
        "by_city": by_city,
        "ankara_rows": ankara_rows,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def current_book_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    orderbook_roots = (
        historical_orderbook_roots()
        if root == DEFAULT_CURRENT_ROOT
        else (
            root / "targeted_output" / "orderbook_snapshots",
            root / "full_ladder_output" / "orderbook_snapshots",
        )
    )
    for orderbook_root in orderbook_roots:
        paths.extend(orderbook_root.glob("*/*.jsonl.gz"))
    return sorted(set(paths))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dense-sources", type=Path, default=DEFAULT_DENSE_SOURCES)
    parser.add_argument("--dense-books", type=Path, default=DEFAULT_DENSE_BOOKS)
    parser.add_argument("--current-root", type=Path, default=DEFAULT_CURRENT_ROOT)
    parser.add_argument("--current-sources", type=Path, default=DEFAULT_CURRENT_SOURCES)
    parser.add_argument("--source", default="aviationweather_metar")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-dense", action="store_true")
    parser.add_argument("--skip-current", action="store_true")
    args = parser.parse_args()

    cohorts: list[dict[str, Any]] = []
    if not args.skip_dense:
        cohorts.append(
            analyze_cohort(
                "dense_2026_06", args.dense_sources, [args.dense_books], args.source, args.output_dir
            )
        )
    if not args.skip_current:
        cohorts.append(
            analyze_cohort(
                "jrs_current_2026_07",
                args.current_sources,
                current_book_paths(args.current_root),
                args.source,
                args.output_dir,
            )
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    event_rows = [row for cohort in cohorts for row in cohort.pop("event_rows")]
    city_rows = [row for cohort in cohorts for row in cohort.pop("by_city")]
    ankara_rows = [row for cohort in cohorts for row in cohort.pop("ankara_rows")]
    payload = {
        "generated_at_utc": iso(dt.datetime.now(UTC)),
        "schema_version": "scheduled_report_liquidity_gap_v1",
        "hypothesis": "scheduled observation reports cause pre-report maker withdrawal and a temporary liquidity gap",
        "bins_min": BIN_EDGES_MIN,
        "active_token_rule": "YES token has a two-sided midpoint in [0.05, 0.95] at least once in cohort",
        "maker_fill_rule": "no fill inferred; maker improvement and markout are upper bounds only",
        "cohorts": cohorts,
    }
    (args.output_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(args.output_dir / "event_deltas.csv", event_rows)
    write_csv(args.output_dir / "city_summary.csv", city_rows)
    write_csv(args.output_dir / "ankara_event_detail.csv", ankara_rows)
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "cohorts": [cohort["name"] for cohort in cohorts],
        "event_rows": len(event_rows),
        "city_rows": len(city_rows),
        "ankara_rows": len(ankara_rows),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
