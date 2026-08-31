#!/usr/bin/env python3
"""Measure METAR.ws HF/D-ATIS agreement and confirmation by fast official METAR.

This is a derived, read-only source study.  It never writes to the evidence
database and never creates market intents or orders.  Same-host monotonic
receive time is used only for exploratory ordering; wall-clock latency remains
ineligible when the source run's clock contract is invalid.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Sequence


FAST_SOURCES = ("METAR_WS_HFMETAR", "METAR_WS_DATIS")
OFFICIAL_SOURCE = "METAR_WS_METAR"
SOURCE_MAX_AGE_SECONDS = {
    "METAR_WS_HFMETAR": 1_800.0,
    "METAR_WS_DATIS": 300.0,
    "METAR_WS_METAR": 600.0,
}
HORIZONS_MINUTES = (10, 20, 60, 120)


@dataclass(frozen=True)
class Event:
    source_id: str
    observation_version_id: str
    station_id: str
    report_kind: str
    observation_time: datetime
    air_temperature_c: float
    received_wall_ns: int
    received_monotonic_ns: int
    clock_valid: bool
    source_age_seconds: float
    live_eligible: bool


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _quantile(values: Iterable[float], q: float) -> float | None:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _distribution(values: Iterable[float]) -> dict[str, float | int | None]:
    rows = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "n": len(rows),
        "mean": mean(rows) if rows else None,
        "p10": _quantile(rows, 0.10),
        "p25": _quantile(rows, 0.25),
        "median": median(rows) if rows else None,
        "p75": _quantile(rows, 0.75),
        "p90": _quantile(rows, 0.90),
    }


def _agreement(pairs: Sequence[tuple[Event, Event]]) -> dict[str, Any]:
    deltas = [fast.air_temperature_c - official.air_temperature_c for fast, official in pairs]
    absolute = [abs(value) for value in deltas]
    return {
        "pairs": len(pairs),
        "stations": len({fast.station_id for fast, _ in pairs}),
        "exact_temperature": sum(abs(value) < 1e-9 for value in deltas),
        "within_1c": sum(value <= 1.0 + 1e-9 for value in absolute),
        "signed_delta_c": _distribution(deltas),
        "absolute_delta_c": _distribution(absolute),
    }


def deduplicate_first_seen(events: Iterable[Event]) -> list[Event]:
    """Keep first transport per canonical source/station/UTC observation instant."""
    first: dict[tuple[str, str, datetime], Event] = {}
    for event in events:
        key = (event.source_id, event.station_id, event.observation_time)
        incumbent = first.get(key)
        if incumbent is None or (
            event.received_monotonic_ns,
            event.observation_version_id,
        ) < (
            incumbent.received_monotonic_ns,
            incumbent.observation_version_id,
        ):
            first[key] = event
    return sorted(
        first.values(),
        key=lambda row: (
            row.received_monotonic_ns,
            row.source_id,
            row.station_id,
            row.observation_time,
        ),
    )


def load_events(db_path: Path) -> tuple[list[Event], dict[str, Any]]:
    uri = f"file:{db_path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    try:
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        run_rows = connection.execute(
            "SELECT run_id, collector_commit, config_hash, vantage_id, started_at_ns FROM collector_run ORDER BY started_at_ns"
        ).fetchall()
        if len(run_rows) != 1:
            raise ValueError(f"expected exactly one collector_run, found {len(run_rows)}")
        run_row = run_rows[0]
        run_id = str(run_row["run_id"])
        rows = connection.execute(
            """
            SELECT
                s.source_id,
                e.observation_version_id,
                e.station_id,
                e.report_kind,
                e.observation_time,
                e.air_temperature_c,
                t.received_wall_ns,
                t.received_monotonic_ns,
                s.clock_valid
            FROM source_observation_seen AS s
            JOIN observation_event AS e USING (observation_version_id)
            JOIN transport_message AS t USING (transport_message_id)
            WHERE s.evidence_status = 'actionable'
              AND s.source_id IN (?, ?, ?)
              AND e.air_temperature_c IS NOT NULL
              AND t.run_id = ?
            ORDER BY t.received_monotonic_ns, s.source_id, e.station_id, e.observation_time
            """,
            (*FAST_SOURCES, OFFICIAL_SOURCE, run_id),
        ).fetchall()
        run_end_rows = connection.execute(
            "SELECT ended_at_ns, termination_reason FROM collector_run_end WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        if len(run_end_rows) > 1:
            raise ValueError(f"expected at most one collector_run_end for {run_id}, found {len(run_end_rows)}")
        clock = connection.execute(
            "SELECT COUNT(*) AS n, SUM(clock_valid) AS valid FROM clock_health WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        connection.close()

    candidates: list[Event] = []
    for row in rows:
        observation_time = _dt(str(row["observation_time"]))
        source_id = str(row["source_id"])
        age = int(row["received_wall_ns"]) / 1_000_000_000 - observation_time.timestamp()
        maximum_age = SOURCE_MAX_AGE_SECONDS[source_id]
        candidates.append(
            Event(
                source_id=source_id,
                observation_version_id=str(row["observation_version_id"]),
                station_id=str(row["station_id"]),
                report_kind=str(row["report_kind"]),
                observation_time=observation_time,
                air_temperature_c=float(row["air_temperature_c"]),
                received_wall_ns=int(row["received_wall_ns"]),
                received_monotonic_ns=int(row["received_monotonic_ns"]),
                clock_valid=bool(row["clock_valid"]),
                source_age_seconds=age,
                live_eligible=-1.0 <= age <= maximum_age,
            )
        )
    if not candidates:
        raise ValueError(f"no actionable METAR.ws source rows found for run {run_id}")
    events = deduplicate_first_seen(candidates)
    latest_monotonic_ns = max(event.received_monotonic_ns for event in candidates)
    latest_wall_ns = max(event.received_wall_ns for event in candidates)
    raw_counts = {
        source: sum(event.source_id == source for event in candidates)
        for source in (*FAST_SOURCES, OFFICIAL_SOURCE)
    }
    clock_samples = int(clock["n"])
    clock_valid_samples = int(clock["valid"] or 0)
    ended = bool(run_end_rows)
    metadata = {
        "database": str(db_path.resolve()),
        "database_size_bytes": db_path.stat().st_size,
        "database_mtime_utc": datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat(),
        "sqlite_quick_check": integrity,
        "collector_run_ended": ended,
        "collector_ended_at_ns": int(run_end_rows[0]["ended_at_ns"]) if ended else None,
        "collector_termination_reason": str(run_end_rows[0]["termination_reason"]) if ended else None,
        "clock_samples": clock_samples,
        "clock_valid_samples": clock_valid_samples,
        "formal_clock_eligible": bool(clock_samples and clock_valid_samples == clock_samples),
        "latest_received_monotonic_ns": latest_monotonic_ns,
        "latest_received_wall_ns": latest_wall_ns,
        "latest_received_utc": datetime.fromtimestamp(latest_wall_ns / 1_000_000_000, timezone.utc).isoformat(),
        "raw_actionable_rows": raw_counts,
        "deduplicated_first_seen_rows": {
            source: sum(event.source_id == source for event in events)
            for source in (*FAST_SOURCES, OFFICIAL_SOURCE)
        },
        "live_eligible_first_seen_rows": {
            source: sum(event.source_id == source and event.live_eligible for event in events)
            for source in (*FAST_SOURCES, OFFICIAL_SOURCE)
        },
        "observation_coverage": {
            source: {
                "all_first_observation_utc": min(
                    (event.observation_time for event in events if event.source_id == source),
                    default=None,
                ).isoformat()
                if any(event.source_id == source for event in events)
                else None,
                "all_last_observation_utc": max(
                    (event.observation_time for event in events if event.source_id == source),
                    default=None,
                ).isoformat()
                if any(event.source_id == source for event in events)
                else None,
                "live_first_observation_utc": min(
                    (
                        event.observation_time
                        for event in events
                        if event.source_id == source and event.live_eligible
                    ),
                    default=None,
                ).isoformat()
                if any(event.source_id == source and event.live_eligible for event in events)
                else None,
                "live_last_observation_utc": max(
                    (
                        event.observation_time
                        for event in events
                        if event.source_id == source and event.live_eligible
                    ),
                    default=None,
                ).isoformat()
                if any(event.source_id == source and event.live_eligible for event in events)
                else None,
            }
            for source in (*FAST_SOURCES, OFFICIAL_SOURCE)
        },
        **(dict(run_row) if run_row is not None else {}),
    }
    return events, metadata


def exact_time_pairs(events: Sequence[Event], source: str) -> list[tuple[Event, Event]]:
    official = {
        (event.station_id, event.observation_time): event
        for event in events
        if event.source_id == OFFICIAL_SOURCE and event.live_eligible
    }
    return [
        (event, official[(event.station_id, event.observation_time)])
        for event in events
        if event.source_id == source
        and event.live_eligible
        and (event.station_id, event.observation_time) in official
    ]


def official_anchor_pairs(
    events: Sequence[Event], source: str, *, max_observation_gap_minutes: int = 15, require_early: bool = False
) -> list[tuple[Event, Event]]:
    fast_by_station: dict[str, list[Event]] = {}
    for event in events:
        if event.source_id == source and event.live_eligible:
            fast_by_station.setdefault(event.station_id, []).append(event)
    output: list[tuple[Event, Event]] = []
    max_gap = max_observation_gap_minutes * 60
    for official in events:
        if official.source_id != OFFICIAL_SOURCE or not official.live_eligible:
            continue
        candidates = [
            fast
            for fast in fast_by_station.get(official.station_id, [])
            if 0 <= (official.observation_time - fast.observation_time).total_seconds() <= max_gap
            and (not require_early or fast.received_monotonic_ns <= official.received_monotonic_ns)
        ]
        if candidates:
            fast = max(candidates, key=lambda row: (row.observation_time, -row.received_monotonic_ns))
            output.append((fast, official))
    return output


def _pair_summary(pairs: Sequence[tuple[Event, Event]]) -> dict[str, Any]:
    summary = _agreement(pairs)
    leads = [
        (official.received_monotonic_ns - fast.received_monotonic_ns) / 1_000_000_000
        for fast, official in pairs
    ]
    observation_gaps = [
        (official.observation_time - fast.observation_time).total_seconds()
        for fast, official in pairs
    ]
    summary.update(
        {
            "fast_received_before_official": sum(value > 0 for value in leads),
            "receipt_lead_seconds_official_minus_fast": _distribution(leads),
            "observation_gap_seconds_official_minus_fast": _distribution(observation_gaps),
        }
    )
    return summary


def confirmation_episodes(events: Sequence[Event], source: str) -> list[dict[str, Any]]:
    official_by_station: dict[str, list[Event]] = {}
    for event in events:
        if event.source_id == OFFICIAL_SOURCE and event.live_eligible:
            official_by_station.setdefault(event.station_id, []).append(event)
    for rows in official_by_station.values():
        rows.sort(key=lambda row: (row.received_monotonic_ns, row.observation_time))

    output: list[dict[str, Any]] = []
    seen_episode: set[tuple[str, str, str, float]] = set()
    fast_events = sorted(
        (event for event in events if event.source_id == source and event.live_eligible),
        key=lambda row: (row.received_monotonic_ns, row.station_id, row.observation_time),
    )
    for fast in fast_events:
        officials = official_by_station.get(fast.station_id, [])
        if not officials:
            continue
        receive_keys = [row.received_monotonic_ns for row in officials]
        official_watermark_ns = receive_keys[-1]
        prior_index = bisect_right(receive_keys, fast.received_monotonic_ns) - 1
        if prior_index < 0:
            continue
        prior = officials[prior_index]
        if fast.observation_time < prior.observation_time:
            continue
        delta = fast.air_temperature_c - prior.air_temperature_c
        if abs(delta) < 1e-9:
            continue
        episode_key = (
            source,
            fast.station_id,
            prior.observation_version_id,
            fast.air_temperature_c,
        )
        if episode_key in seen_episode:
            continue
        seen_episode.add(episode_key)

        later = [
            row
            for row in officials[bisect_left(receive_keys, fast.received_monotonic_ns + 1) :]
            if row.observation_time >= fast.observation_time
        ]
        next_official = later[0] if later else None
        direction = "up" if delta > 0 else "down"
        row: dict[str, Any] = {
            "source_id": source,
            "station_id": fast.station_id,
            "direction": direction,
            "fast_observation_time": fast.observation_time.isoformat(),
            "fast_received_monotonic_ns": fast.received_monotonic_ns,
            "fast_source_age_seconds": fast.source_age_seconds,
            "prior_official_observation_time": prior.observation_time.isoformat(),
            "prior_official_temp_c": prior.air_temperature_c,
            "fast_temp_c": fast.air_temperature_c,
            "fast_minus_prior_official_c": delta,
            "next_official_observation_time": next_official.observation_time.isoformat() if next_official else None,
            "next_official_temp_c": next_official.air_temperature_c if next_official else None,
            "next_official_delay_seconds": (
                (next_official.received_monotonic_ns - fast.received_monotonic_ns) / 1_000_000_000
                if next_official
                else None
            ),
            "next_official_exact": bool(
                next_official and abs(next_official.air_temperature_c - fast.air_temperature_c) < 1e-9
            ),
            "next_official_within_1c": bool(
                next_official and abs(next_official.air_temperature_c - fast.air_temperature_c) <= 1.0 + 1e-9
            ),
            "next_official_directional_confirm": bool(
                next_official
                and (
                    (direction == "up" and next_official.air_temperature_c >= fast.air_temperature_c)
                    or (direction == "down" and next_official.air_temperature_c <= fast.air_temperature_c)
                )
            ),
        }
        for minutes in HORIZONS_MINUTES:
            horizon_ns = fast.received_monotonic_ns + minutes * 60 * 1_000_000_000
            horizon_rows = [candidate for candidate in later if candidate.received_monotonic_ns <= horizon_ns]
            exact = any(abs(candidate.air_temperature_c - fast.air_temperature_c) < 1e-9 for candidate in horizon_rows)
            directional = any(
                (direction == "up" and candidate.air_temperature_c >= fast.air_temperature_c)
                or (direction == "down" and candidate.air_temperature_c <= fast.air_temperature_c)
                for candidate in horizon_rows
            )
            row[f"resolved_{minutes}m"] = bool(exact or directional or official_watermark_ns >= horizon_ns)
            row[f"exact_confirmed_{minutes}m"] = exact
            row[f"directional_confirmed_{minutes}m"] = directional
        output.append(row)
    return output


def _episodes_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "episodes": len(rows),
        "stations": len({str(row["station_id"]) for row in rows}),
        "up_episodes": sum(row["direction"] == "up" for row in rows),
        "down_episodes": sum(row["direction"] == "down" for row in rows),
        "next_official_available": sum(row["next_official_temp_c"] is not None for row in rows),
        "next_official_exact": sum(bool(row["next_official_exact"]) for row in rows),
        "next_official_within_1c": sum(bool(row["next_official_within_1c"]) for row in rows),
        "next_official_directional_confirm": sum(bool(row["next_official_directional_confirm"]) for row in rows),
        "next_official_delay_seconds": _distribution(
            float(row["next_official_delay_seconds"])
            for row in rows
            if row["next_official_delay_seconds"] is not None
        ),
    }
    for minutes in HORIZONS_MINUTES:
        resolved = [row for row in rows if row[f"resolved_{minutes}m"]]
        result[f"horizon_{minutes}m"] = {
            "resolved_denominator": len(resolved),
            "right_censored": len(rows) - len(resolved),
            "exact_confirmed": sum(bool(row[f"exact_confirmed_{minutes}m"]) for row in resolved),
            "directional_confirmed": sum(bool(row[f"directional_confirmed_{minutes}m"]) for row in resolved),
        }
    return result


def _episodes_by_station(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    stations = sorted({str(row["station_id"]) for row in rows})
    return {
        station: {
            "all_changes": _episodes_summary([row for row in rows if row["station_id"] == station]),
            "up_changes": _episodes_summary(
                [row for row in rows if row["station_id"] == station and row["direction"] == "up"]
            ),
        }
        for station in stations
    }


def analyze(db_path: Path) -> dict[str, Any]:
    events, metadata = load_events(db_path)
    exact = {source: _pair_summary(exact_time_pairs(events, source)) for source in FAST_SOURCES}
    anchored: dict[str, Any] = {}
    episodes: dict[str, Any] = {}
    episode_rows: list[dict[str, Any]] = []
    for source in FAST_SOURCES:
        semantic_pairs = official_anchor_pairs(events, source, require_early=False)
        early_pairs = official_anchor_pairs(events, source, require_early=True)
        anchored[source] = {
            "semantic_latest_within_15m": _pair_summary(semantic_pairs),
            "available_before_official_within_15m": _pair_summary(early_pairs),
        }
        rows = confirmation_episodes(events, source)
        episode_rows.extend(rows)
        episodes[source] = {
            "all_changes": _episodes_summary(rows),
            "up_changes": _episodes_summary([row for row in rows if row["direction"] == "up"]),
            "by_station": _episodes_by_station(rows),
        }
    return {
        "schema_version": "metarws_fast_official_confirmation_v1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "exploratory_same_host_monotonic_not_formal_latency",
        "grain": {
            "source_event": "first actionable row per source_id×station_id×observation_time",
            "official_anchor": "one fast row within 15 observation minutes before each first-seen official report",
            "confirmation_episode": "first changed fast temperature per source×station×prior-official×fast-temperature",
        },
        "source_live_age_contract_seconds": SOURCE_MAX_AGE_SECONDS,
        "metadata": metadata,
        "exact_observation_time_pairs": exact,
        "official_anchor_pairs": anchored,
        "confirmation_episodes": episodes,
        "episode_rows": episode_rows,
    }


def _pct(numerator: int, denominator: int) -> str:
    return "NA" if not denominator else f"{100 * numerator / denominator:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    lines = [
        "# METAR.ws HF/D-ATIS → fast official confirmation v1",
        "",
        f"Observed at: `{report['observed_at_utc']}`  ",
        f"Input: `{metadata['database']}`  ",
        f"Run: `{metadata.get('run_id', '')}`; collector ended: `{metadata['collector_run_ended']}`  ",
        "Status: `exploratory_same_host_monotonic_not_formal_latency`",
        "",
        "## Data integrity and fixed denominator",
        "",
        f"- SQLite quick check: `{metadata['sqlite_quick_check']}`.",
        f"- Capture through: `{metadata['latest_received_utc']}`.",
        f"- Clock-valid probes: `{metadata['clock_valid_samples']}/{metadata['clock_samples']}`; formal wall-clock latency ranking is excluded.",
        "- Event grain is the first actionable row per source×station×observation-time. Cached and late-age rows are excluded from live ordering.",
        "- `obs10` is treated as HF-METAR/MADIS-derived data, not as a guaranteed ten-minute transport SLA.",
        f"- HF first-seen rows: `{metadata['live_eligible_first_seen_rows']['METAR_WS_HFMETAR']}` live-eligible of `{metadata['deduplicated_first_seen_rows']['METAR_WS_HFMETAR']}` deduplicated actionable rows; old/late replay rows remain evidence but do not enter live ordering.",
        "",
        "## Same-time D-ATIS / HF versus official METAR",
        "",
        "| source | pairs | stations | temp exact | fast arrived first | median official-minus-fast receive |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for source in FAST_SOURCES:
        row = report["exact_observation_time_pairs"][source]
        lead = row["receipt_lead_seconds_official_minus_fast"]["median"]
        lines.append(
            f"| {source} | {row['pairs']} | {row['stations']} | "
            f"{row['exact_temperature']}/{row['pairs']} ({_pct(row['exact_temperature'], row['pairs'])}) | "
            f"{row['fast_received_before_official']}/{row['pairs']} | "
            f"{lead:.3f}s |" if lead is not None else
            f"| {source} | {row['pairs']} | {row['stations']} | 0/0 | 0/0 | NA |"
        )
    lines += [
        "",
        "## Latest fast observation available before each official report",
        "",
        "| source | official anchors | temp exact | within 1C | median abs delta | median receive lead |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for source in FAST_SOURCES:
        row = report["official_anchor_pairs"][source]["available_before_official_within_15m"]
        abs_delta = row["absolute_delta_c"]["median"]
        lead = row["receipt_lead_seconds_official_minus_fast"]["median"]
        lines.append(
            f"| {source} | {row['pairs']} | {row['exact_temperature']}/{row['pairs']} | "
            f"{row['within_1c']}/{row['pairs']} | "
            f"{abs_delta:.3f}C | {lead:.3f}s |"
            if abs_delta is not None and lead is not None
            else f"| {source} | {row['pairs']} | NA | NA | NA | NA |"
        )
    lines += [
        "",
        "## Changed-source episodes and later official confirmation",
        "",
        "An episode starts only when the fast source differs from the latest official temperature already available to the host. Repeated identical prints before the next official are collapsed.",
        "",
        "| source | episodes | upward | next official exact | directional confirm | 10m confirm/resolved | 20m | 60m | 120m |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for source in FAST_SOURCES:
        row = report["confirmation_episodes"][source]["all_changes"]
        horizon_cells = []
        for minutes in HORIZONS_MINUTES:
            horizon = row[f"horizon_{minutes}m"]
            horizon_cells.append(
                f"{horizon['directional_confirmed']}/{horizon['resolved_denominator']}"
            )
        lines.append(
            f"| {source} | {row['episodes']} | {row['up_episodes']} | "
            f"{row['next_official_exact']}/{row['next_official_available']} | "
            f"{row['next_official_directional_confirm']}/{row['next_official_available']} | "
            + " | ".join(horizon_cells)
            + " |"
        )
    lines += [
        "",
        "## Interpretation boundary",
        "",
        "- This report measures source agreement and confirmation, not settlement truth. METAR/D-ATIS/HF prints remain proxies for Weather Underground/native-lattice settlement.",
        "- A high confirmation rate can justify pre-arming subscriptions or a two-stage filter; it does not prove a fee-adjusted trade. The separate event×token market-reaction report must show executable price after official confirmation.",
        "- The active run is interim until `collector_run_end` exists. Final rates must be regenerated from the sealed run without merging prior partial runs.",
        "",
    ]
    return "\n".join(lines)


def write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if fields:
            writer.writeheader()
            writer.writerows(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    report = analyze(args.db)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "confirmation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output_dir / "confirmation_report.md").write_text(render_markdown(report), encoding="utf-8")
    write_csv(args.output_dir / "confirmation_episodes.csv", report["episode_rows"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
