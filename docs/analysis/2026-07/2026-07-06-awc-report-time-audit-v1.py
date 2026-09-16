#!/usr/bin/env python3
"""Audit persisted AviationWeather timestamps against raw METAR DDHHMMZ."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.observation_sources import parse_metar_report_time  # noqa: E402


REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-06-awc-report-time-audit-v1.md"
DEFAULT_INPUTS = [
    ROOT / "runtime/n100_recovery_20260705/weather_data_feed_service_runtime/output/source_events/sources.jsonl",
    ROOT / "runtime/weather_edge_v1/source_orderbook_timing/sources.jsonl",
    ROOT / "runtime/analysis_inputs/forecast_update_time_repricing_v0/sources.jsonl",
]
AWC_SOURCES = {"aviationweather_metar", "aviationweather_cache_csv"}


@dataclass
class Stats:
    rows: int = 0
    ok_rows: int = 0
    raw_metar_rows: int = 0
    raw_api_report_time_rows: int = 0
    source_report_matches_raw: int = 0
    source_report_diffs: int = 0
    missing_source_report: int = 0
    missing_raw_report: int = 0
    detect_lags_min: list[float] | None = None
    fetch_end_lags_min: list[float] | None = None
    api_report_diffs_min: list[float] | None = None
    examples: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        self.detect_lags_min = []
        self.fetch_end_lags_min = []
        self.api_report_diffs_min = []
        self.examples = []


def parse_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str:
    return "" if dt is None else dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[idx]


def fmt_num(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and not math.isfinite(value):
        return "n/a"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def iter_jsonl(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield line_no, row


def audit_path(path: Path, max_rows: int | None = None) -> Stats:
    stats = Stats()
    if not path.exists():
        return stats
    for line_no, row in iter_jsonl(path):
        source = str(row.get("source") or "")
        if source not in AWC_SOURCES:
            continue
        stats.rows += 1
        if row.get("status") == "ok":
            stats.ok_rows += 1
        raw = str(row.get("raw_metar") or row.get("rawOb") or "").strip()
        if raw:
            stats.raw_metar_rows += 1
        report_time_raw = row.get("reportTime") or row.get("obsTime")
        if report_time_raw:
            stats.raw_api_report_time_rows += 1
        source_report = parse_utc(row.get("source_report_ts_utc"))
        reference = source_report or parse_utc(row.get("local_detect_ts_utc")) or parse_utc(row.get("ts_utc"))
        raw_report = parse_metar_report_time(raw, reference) if raw and reference is not None else None
        if source_report is None:
            stats.missing_source_report += 1
        if raw_report is None:
            stats.missing_raw_report += 1
        if source_report is not None and raw_report is not None:
            diff_min = (source_report - raw_report).total_seconds() / 60.0
            if abs(diff_min) < 1e-9:
                stats.source_report_matches_raw += 1
            else:
                stats.source_report_diffs += 1
                if len(stats.examples or []) < 5:
                    stats.examples.append(
                        {
                            "path": str(path),
                            "line": line_no,
                            "city": row.get("city"),
                            "source": source,
                            "station": row.get("station"),
                            "raw_metar": raw,
                            "source_report_ts_utc": iso(source_report),
                            "raw_ddhhmm_ts_utc": iso(raw_report),
                            "diff_min": round(diff_min, 3),
                        }
                    )
        detect = parse_utc(row.get("local_detect_ts_utc") or row.get("ts_utc"))
        if detect is not None and raw_report is not None:
            stats.detect_lags_min.append((detect - raw_report).total_seconds() / 60.0)
        fetch_end = parse_utc(row.get("source_fetch_end_utc"))
        if fetch_end is not None and raw_report is not None:
            stats.fetch_end_lags_min.append((fetch_end - raw_report).total_seconds() / 60.0)
        api_report = parse_utc(report_time_raw)
        if api_report is not None and raw_report is not None:
            stats.api_report_diffs_min.append((api_report - raw_report).total_seconds() / 60.0)
        if max_rows is not None and stats.rows >= max_rows:
            break
    return stats


def merge_stats(stats_by_path: dict[Path, Stats]) -> Stats:
    total = Stats()
    for stats in stats_by_path.values():
        total.rows += stats.rows
        total.ok_rows += stats.ok_rows
        total.raw_metar_rows += stats.raw_metar_rows
        total.raw_api_report_time_rows += stats.raw_api_report_time_rows
        total.source_report_matches_raw += stats.source_report_matches_raw
        total.source_report_diffs += stats.source_report_diffs
        total.missing_source_report += stats.missing_source_report
        total.missing_raw_report += stats.missing_raw_report
        total.detect_lags_min.extend(stats.detect_lags_min or [])
        total.fetch_end_lags_min.extend(stats.fetch_end_lags_min or [])
        total.api_report_diffs_min.extend(stats.api_report_diffs_min or [])
        total.examples.extend((stats.examples or [])[: max(0, 5 - len(total.examples))])
    return total


def lag_summary(values: list[float]) -> str:
    if not values:
        return "n/a"
    return (
        f"n={len(values)}, min={fmt_num(min(values))}, p50={fmt_num(float(median(values)))}, "
        f"p90={fmt_num(pct(values, 0.90))}, p95={fmt_num(pct(values, 0.95))}, "
        f"p99={fmt_num(pct(values, 0.99))}, max={fmt_num(max(values))}"
    )


def render_report(stats_by_path: dict[Path, Stats]) -> str:
    total = merge_stats(stats_by_path)
    rows = "\n".join(
        "| `{path}` | {rows} | {ok} | {raw} | {api} | {match} | {diff} | {detect} |".format(
            path=path,
            rows=stats.rows,
            ok=stats.ok_rows,
            raw=stats.raw_metar_rows,
            api=stats.raw_api_report_time_rows,
            match=stats.source_report_matches_raw,
            diff=stats.source_report_diffs,
            detect=lag_summary(stats.detect_lags_min or []),
        )
        for path, stats in stats_by_path.items()
    )
    example_text = "None"
    if total.examples:
        example_text = "\n".join(f"- `{json.dumps(item, ensure_ascii=False, sort_keys=True)}`" for item in total.examples)
    api_report_verdict = (
        "No persisted rows carried raw API `reportTime`/`obsTime`, so local disk evidence cannot directly prove whether the AviationWeather API field ever equals fetch/detect time."
        if total.raw_api_report_time_rows == 0
        else f"Persisted raw API reportTime rows: {total.raw_api_report_time_rows}; reportTime-minus-DDHHMMZ lag summary: {lag_summary(total.api_report_diffs_min or [])}."
    )
    diff_paths = [str(path) for path, stats in stats_by_path.items() if stats.source_report_diffs]
    diff_verdict = (
        "No persisted source_report-vs-DDHHMMZ diffs were found."
        if not diff_paths
        else "source_report-vs-DDHHMMZ diffs appeared only in: " + ", ".join(f"`{path}`" for path in diff_paths) + "."
    )
    live_bug_text = (
        "This audit does not prove an active live bug in theta/metar_cross direct AWC fetches, because the local historical payloads have already been normalized and do not preserve the original API `reportTime`. "
        "It does prove the contract requirement: `source_report_ts_utc` must come from raw METAR `DDHHMMZ`, while `local_detect_ts_utc`/`source_fetch_end_utc` are separate detection times. "
        "If a caller feeds detect/fetch time into `reportTime`, freshness age is understated by the detect lag below."
    )
    if total.raw_api_report_time_rows > 0 and total.api_report_diffs_min and any(abs(v) > 1e-9 for v in total.api_report_diffs_min):
        live_bug_text = (
            "Persisted raw API reportTime differed from raw METAR `DDHHMMZ`; direct callers that use `reportTime` as observation time are exposed to a live freshness/trigger bug. "
            "Do not patch live runners until the fix plan is reviewed."
        )
    return f"""# AWC reportTime Timestamp Audit v1

Date: 2026-07-06

Scope: read-only audit of locally persisted AviationWeather-like rows. No live runner behavior was changed, no network fetch was performed, and no source_events service was started.

## Verdict

- Persisted `source_report_ts_utc` is consistent with raw METAR `DDHHMMZ`: `{total.source_report_matches_raw}` matches, `{total.source_report_diffs}` diffs.
- {diff_verdict}
- {api_report_verdict}
- Detection/fetch time is materially later than observation time: detect lag summary `{lag_summary(total.detect_lags_min or [])}`.
- Contract conclusion: carry both observation time and detection time. Observation time = `source_report_ts_utc` derived from raw METAR `DDHHMMZ`; detection time = `local_detect_ts_utc` / `source_fetch_end_utc`.

## Impact Read

{live_bug_text}

Affected code surfaces to review before any live fix:

- `weather_data_feed.observation_sources.parse_aviationweather_records` already prefers raw METAR `DDHHMMZ` over API `reportTime`.
- `weather_source_orderbook_timing_monitor.py` already uses the shared AWC parser.
- `weather_theta_current_yes_tiny_live.py::aviationweather_obs`, `weather_metar_cross_prev_no_shadow.py::parse_metar_records`, and `regime_routed_no_tiny_live.py` still consume `reportTime` directly when they live-fetch AviationWeather JSON.
- `metar_cross` source-events mode is not affected by this specific parser issue because it consumes normalized `source_report_ts_utc`.

## Input Summary

| Input | AWC rows | OK rows | raw METAR rows | raw API reportTime rows | source_report==DDHHMMZ | source_report diffs | detect lag min |
|---|---:|---:|---:|---:|---:|---:|---|
{rows}

## Diff Examples

{example_text}

## Notes

- The largest local datasets are normalized source-event histories, not original AviationWeather API JSON payload archives. They preserve `raw_metar`, `source_report_ts_utc`, and detect/fetch timestamps, but not the original raw `reportTime` field.
- Therefore this report is a contract/impact audit, not a direct proof that the live AviationWeather API currently emits detect-time `reportTime`.
- Follow-up fix, if approved, should centralize direct AWC JSON parsing on `parse_aviationweather_records` and keep `source_report_ts_utc` and detect/fetched timestamps explicit in caller outputs.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--max-rows-per-input", type=int, default=0, help="0 means no limit")
    parser.add_argument("inputs", nargs="*", type=Path, default=DEFAULT_INPUTS)
    args = parser.parse_args()
    stats_by_path = {
        path: audit_path(path, None if args.max_rows_per_input <= 0 else args.max_rows_per_input)
        for path in args.inputs
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(stats_by_path), encoding="utf-8")
    total = merge_stats(stats_by_path)
    print(
        f"rows={total.rows} ok={total.ok_rows} raw_metar={total.raw_metar_rows} "
        f"raw_api_reportTime={total.raw_api_report_time_rows} "
        f"source_report_matches_raw={total.source_report_matches_raw} "
        f"source_report_diffs={total.source_report_diffs}"
    )
    print(f"detect_lag_min {lag_summary(total.detect_lags_min or [])}")
    print(f"wrote {args.report}")


if __name__ == "__main__":
    main()
