#!/usr/bin/env python3
"""Analyze post-cross repricing around METAR temperature crossings.

This is a market-structure research tool, not a live trading runner. It reads
the metar-cross opportunity log and the timing monitor orderbook log, then
measures how nearby brackets repriced around the observation report timestamp.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_OPPORTUNITIES = Path("runtime/weather_edge_v1/metar_cross_prev_no_shadow/opportunities.jsonl")
DEFAULT_BOOKS = Path("runtime/weather_edge_v1/source_orderbook_timing/books.jsonl")


def parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return dt.datetime.fromisoformat(value)


def parse_horizons(value: str) -> list[int]:
    out: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out


def json_rows(path: Path):
    with path.open() as handle:
        for line_no, line in enumerate(handle, start=1):
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc


def yes_mid(row: dict[str, Any] | None) -> float | None:
    if not row or row.get("status") != "ok":
        return None
    bid = row.get("best_bid")
    ask = row.get("best_ask")
    if isinstance(bid, (int, float)) and isinstance(ask, (int, float)):
        return (float(bid) + float(ask)) / 2.0
    if isinstance(bid, (int, float)):
        return float(bid)
    if isinstance(ask, (int, float)):
        return float(ask)
    return None


def no_ask(row: dict[str, Any] | None) -> float | None:
    if not row or row.get("status") != "ok":
        return None
    value = row.get("best_ask")
    return float(value) if isinstance(value, (int, float)) else None


def snapshot(rows: list[dict[str, Any]], side: str, ts: dt.datetime, mode: str) -> dict[str, Any] | None:
    filtered = [row for row in rows if row.get("side") == side]
    if mode == "before":
        candidates = [row for row in filtered if (row_ts := parse_ts(row.get("ts_utc"))) and row_ts <= ts]
        return candidates[-1] if candidates else None
    candidates = [row for row in filtered if (row_ts := parse_ts(row.get("ts_utc"))) and row_ts >= ts]
    return candidates[0] if candidates else None


def percentile(values: list[float], q: float) -> float | None:
    cleaned = sorted(value for value in values if value is not None and math.isfinite(value))
    if not cleaned:
        return None
    idx = min(len(cleaned) - 1, round(q * (len(cleaned) - 1)))
    return cleaned[idx]


def fmt(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.3f}"


def load_events(path: Path, since: dt.datetime | None, max_events: int | None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float]] = set()
    for row in json_rows(path):
        if row.get("status") != "cross_detected":
            continue
        report_ts = parse_ts(row.get("obs_last_obs_utc"))
        event_ts = parse_ts(row.get("ts_utc"))
        if not report_ts or not event_ts:
            continue
        if since and event_ts < since:
            continue
        event_slug = row.get("event_slug")
        current_value = row.get("recent_running_value")
        if not event_slug or not isinstance(current_value, (int, float)):
            continue
        key = (event_slug, row.get("obs_last_obs_utc"), float(current_value))
        if key in seen:
            continue
        seen.add(key)
        events.append(row)
        if max_events and len(events) >= max_events:
            break
    return events


def load_books(
    path: Path,
    slugs: set[str],
    event_windows: dict[str, list[tuple[dt.datetime, dt.datetime]]],
) -> dict[tuple[str, float, str], list[dict[str, Any]]]:
    out: dict[tuple[str, float, str], list[dict[str, Any]]] = defaultdict(list)
    for row in json_rows(path):
        slug = row.get("event_slug")
        if slug not in slugs:
            continue
        row_ts = parse_ts(row.get("ts_utc"))
        if not row_ts:
            continue
        if not any(start <= row_ts <= end for start, end in event_windows.get(slug, [])):
            continue
        bracket = row.get("bracket")
        label = row.get("market_label")
        if isinstance(bracket, (int, float)) and isinstance(label, str):
            out[(slug, float(bracket), label)].append(row)
    for rows in out.values():
        rows.sort(key=lambda row: row.get("ts_utc", ""))
    return out


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    since = parse_ts(args.since) if args.since else None
    horizons = parse_horizons(args.horizons_sec)
    events = load_events(args.opportunities, since, args.max_events)
    slugs = {row["event_slug"] for row in events}
    lookback = dt.timedelta(seconds=args.lookback_sec)
    lookahead = dt.timedelta(seconds=max(horizons + [0]) + args.lookahead_buffer_sec)

    event_windows: dict[str, list[tuple[dt.datetime, dt.datetime]]] = defaultdict(list)
    for event in events:
        report_ts = parse_ts(event.get("obs_last_obs_utc"))
        if report_ts:
            event_windows[event["event_slug"]].append((report_ts - lookback, report_ts + lookahead))

    books = load_books(args.books, slugs, event_windows)
    rows: list[dict[str, Any]] = []
    rel_specs = [(-1, "prev_or_cross"), (0, "current"), (1, "next1"), (2, "next2")]

    for event in events:
        report_ts = parse_ts(event.get("obs_last_obs_utc"))
        if not report_ts:
            continue
        current_value = float(event["recent_running_value"])
        event_slug = event["event_slug"]
        for rel_delta, rel_name in rel_specs:
            bracket = current_value + rel_delta
            matches = [
                (key, value)
                for key, value in books.items()
                if key[0] == event_slug and abs(key[1] - bracket) < 1e-9
            ]
            if not matches:
                continue
            (key, book_rows) = matches[0]
            label = key[2]
            before_ts = report_ts - dt.timedelta(seconds=args.before_sec)
            before_yes = snapshot(book_rows, "YES", before_ts, "before")
            before_no = snapshot(book_rows, "NO", before_ts, "before")
            row: dict[str, Any] = {
                "city": event.get("city"),
                "event_slug": event_slug,
                "target_date": event.get("target_date"),
                "source_report_ts_utc": event.get("obs_last_obs_utc"),
                "cross_detect_ts_utc": event.get("ts_utc"),
                "detected_after_report_sec": event.get("detected_after_report_sec"),
                "rel": rel_name,
                "bracket": bracket,
                "market_label": label,
                "yes_mid_before": yes_mid(before_yes),
                "no_ask_before": no_ask(before_no),
            }
            for horizon in horizons:
                target_ts = report_ts + dt.timedelta(seconds=horizon)
                after_yes = snapshot(book_rows, "YES", target_ts, "after")
                after_no = snapshot(book_rows, "NO", target_ts, "after")
                row[f"yes_mid_{horizon}s"] = yes_mid(after_yes)
                row[f"no_ask_{horizon}s"] = no_ask(after_no)
                if row["yes_mid_before"] is not None and row[f"yes_mid_{horizon}s"] is not None:
                    row[f"yes_delta_{horizon}s"] = row[f"yes_mid_{horizon}s"] - row["yes_mid_before"]
                else:
                    row[f"yes_delta_{horizon}s"] = None
            rows.append(row)

    summary: dict[str, Any] = {
        "inputs": {
            "opportunities": str(args.opportunities),
            "books": str(args.books),
            "since": args.since,
            "before_sec": args.before_sec,
            "horizons_sec": horizons,
            "lookback_sec": args.lookback_sec,
        },
        "funnel": {
            "cross_events": len(events),
            "event_slugs": len(slugs),
            "bracket_rows": len(rows),
        },
        "by_rel": {},
    }
    for rel_name in ["prev_or_cross", "current", "next1", "next2"]:
        rel_rows = [row for row in rows if row["rel"] == rel_name]
        rel_summary: dict[str, Any] = {"rows": len(rel_rows)}
        for horizon in horizons:
            deltas = [row[f"yes_delta_{horizon}s"] for row in rel_rows if row[f"yes_delta_{horizon}s"] is not None]
            rel_summary[f"yes_delta_{horizon}s"] = {
                "n": len(deltas),
                "mean": (sum(deltas) / len(deltas)) if deltas else None,
                "p25": percentile(deltas, 0.25),
                "p50": percentile(deltas, 0.50),
                "p75": percentile(deltas, 0.75),
            }
        summary["by_rel"][rel_name] = rel_summary
    summary["rows"] = rows
    return summary


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Post-Cross Repricing V0",
        "",
        "Status: generated analysis output",
        "",
        "## Funnel",
        "",
        f"- cross_events: `{payload['funnel']['cross_events']}`",
        f"- event_slugs: `{payload['funnel']['event_slugs']}`",
        f"- bracket_rows: `{payload['funnel']['bracket_rows']}`",
        "",
        "## YES Delta By Relative Bracket",
        "",
        "| rel | horizon | n | mean | p25 | p50 | p75 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rel_name, rel_payload in payload["by_rel"].items():
        for key, value in rel_payload.items():
            if not key.startswith("yes_delta_"):
                continue
            horizon = key.removeprefix("yes_delta_")
            lines.append(
                "| {rel} | {horizon} | {n} | {mean} | {p25} | {p50} | {p75} |".format(
                    rel=rel_name,
                    horizon=horizon,
                    n=value["n"],
                    mean=fmt(value["mean"]),
                    p25=fmt(value["p25"]),
                    p50=fmt(value["p50"]),
                    p75=fmt(value["p75"]),
                )
            )
    current_rows = [
        row
        for row in payload["rows"]
        if row["rel"] == "current"
        and row.get("yes_mid_before") is not None
        and row.get("yes_mid_180s") is not None
    ]
    current_rows.sort(key=lambda row: row["yes_mid_180s"] - row["yes_mid_before"], reverse=True)
    lines.extend(["", "## Biggest Current-Bracket Repricings", ""])
    for row in current_rows[:20]:
        delta = row["yes_mid_180s"] - row["yes_mid_before"]
        lines.append(
            "- `{city}` `{report}` `{label}` YES `{before}` -> `{after}` (`{delta}`), detect_lag `{lag}`".format(
                city=row["city"],
                report=row["source_report_ts_utc"],
                label=row["market_label"],
                before=fmt(row["yes_mid_before"]),
                after=fmt(row["yes_mid_180s"]),
                delta=fmt(delta),
                lag=row.get("detected_after_report_sec"),
            )
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opportunities", type=Path, default=DEFAULT_OPPORTUNITIES)
    parser.add_argument("--books", type=Path, default=DEFAULT_BOOKS)
    parser.add_argument("--since", default=None, help="UTC ISO timestamp lower bound for opportunity ts_utc")
    parser.add_argument("--before-sec", type=int, default=30)
    parser.add_argument("--horizons-sec", default="30,90,180,300")
    parser.add_argument("--lookback-sec", type=int, default=600)
    parser.add_argument("--lookahead-buffer-sec", type=int, default=60)
    parser.add_argument("--max-events", type=int, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    args = parser.parse_args()

    payload = analyze(args)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(render_markdown(payload))
    if not args.output_json and not args.output_md:
        print(json.dumps(payload["by_rel"], indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
