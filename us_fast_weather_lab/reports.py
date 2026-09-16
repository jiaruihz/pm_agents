"""Generate evidence-backed smoke, latency, blocker, and disposition reports."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from us_fast_weather_lab.storage import canonical_json, git_commit


ALLOWED_DISPOSITIONS = {
    "ADOPT_WIS2_PUBLIC_FAST_PATH",
    "ADOPT_SWIFT_PRIMARY_WIS2_FALLBACK",
    "ADOPT_PAID_RELAY_WITH_PROVEN_EDGE",
    "NO_MATERIAL_FAST_SOURCE_ADVANTAGE",
    "BLOCKED_BY_ACCESS_OR_INSUFFICIENT_EVIDENCE",
}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _json_write(path: Path, value: Any) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _paired_rows(conn: sqlite3.Connection, *, valid_only: bool) -> list[dict[str, Any]]:
    valid_sql = "WHERE clock_valid=1" if valid_only else ""
    rows = _rows(
        conn,
        f"""
        WITH ranked_seen AS (
          SELECT observation_version_id, source_id, vantage_id,
                 first_actionable_seen_at_ns, clock_valid,
                 ROW_NUMBER() OVER (
                   PARTITION BY observation_version_id, source_id, vantage_id
                   ORDER BY first_actionable_seen_at_ns, source_seen_id
                 ) AS first_rank
          FROM source_observation_seen
          WHERE evidence_status='actionable'
        ),
        first_seen AS (
          SELECT observation_version_id, source_id, vantage_id,
                 first_actionable_seen_at_ns, clock_valid
          FROM ranked_seen
          WHERE first_rank=1
        ),
        eligible_first_seen AS (
          SELECT * FROM first_seen {valid_sql}
        )
        SELECT a.observation_version_id, e.event_family_id, e.station_id,
               e.observation_time, e.report_kind, e.semantic_version_id,
               a.vantage_id, a.source_id AS source_a, b.source_id AS source_b,
               a.first_actionable_seen_at_ns AS source_a_seen_ns,
               b.first_actionable_seen_at_ns AS source_b_seen_ns,
               (a.first_actionable_seen_at_ns-b.first_actionable_seen_at_ns)/1000000000.0 AS delta_a_minus_b_seconds,
               a.clock_valid AS source_a_clock_valid, b.clock_valid AS source_b_clock_valid
        FROM eligible_first_seen a
        JOIN eligible_first_seen b
          ON b.observation_version_id=a.observation_version_id
         AND b.vantage_id=a.vantage_id
         AND b.source_id>a.source_id
        JOIN observation_event e ON e.observation_version_id=a.observation_version_id
        ORDER BY a.observation_version_id, a.source_id, b.source_id
        """,
    )
    return rows


def _source_stats(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[float]] = {}
    for row in pairs:
        key = (row["vantage_id"], row["source_a"], row["source_b"])
        grouped.setdefault(key, []).append(float(row["delta_a_minus_b_seconds"]))
    output: list[dict[str, Any]] = []
    for (vantage, source_a, source_b), values in sorted(grouped.items()):
        ordered = sorted(values)
        p90_index = max(0, math.ceil(0.9 * len(ordered)) - 1)
        output.append(
            {
                "vantage_id": vantage,
                "source_a": source_a,
                "source_b": source_b,
                "paired_events": len(values),
                "median_delta_a_minus_b_seconds": statistics.median(values),
                "p90_delta_a_minus_b_seconds": ordered[p90_index],
                "source_a_win_rate": sum(value < 0 for value in values) / len(values),
            }
        )
    return output


def _hash_file(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _evidence_index(paths: list[Path], *, stage: int, status: str) -> dict[str, Any]:
    files = [_hash_file(path) for path in paths if path.exists() and path.is_file()]
    return {"stage": stage, "status": status, "generated_at_ns": time.time_ns(), "files": files}


def generate_reports(runtime_root: Path, reports_root: Path, *, config_paths: list[Path]) -> dict[str, Any]:
    db_path = runtime_root / "evidence.sqlite3"
    reports_root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    runs = _rows(conn, "SELECT * FROM collector_run ORDER BY started_at_ns")
    run_ends = _rows(conn, "SELECT * FROM collector_run_end ORDER BY ended_at_ns")
    messages = _scalar(conn, "SELECT COUNT(*) FROM transport_message")
    observations = _scalar(conn, "SELECT COUNT(*) FROM observation_event")
    seen = _scalar(conn, "SELECT COUNT(*) FROM source_observation_seen")
    raw_unique = _scalar(conn, "SELECT COUNT(DISTINCT raw_payload_sha256) FROM transport_message")
    raw_paths = _scalar(conn, "SELECT COUNT(*) FROM transport_message WHERE raw_payload_path <> ''")
    invalid_clock = _scalar(conn, "SELECT COUNT(*) FROM source_observation_seen WHERE clock_valid=0")
    valid_pairs = _paired_rows(conn, valid_only=True)
    all_pairs = _paired_rows(conn, valid_only=False)
    stats = _source_stats(valid_pairs)
    source_counts = _rows(
        conn,
        """SELECT source_id, COUNT(*) AS messages, COUNT(DISTINCT raw_payload_sha256) AS unique_payloads
           FROM transport_message GROUP BY source_id ORDER BY source_id""",
    )
    wis2_messages = _scalar(conn, "SELECT COUNT(*) FROM transport_message WHERE source_id LIKE 'WIS2%'")
    fetch_inventory = _rows(
        conn,
        """SELECT COALESCE(sniffed_format,'unknown') AS sniffed_format,
                  COALESCE(f.content_type,'') AS content_type,
                  COUNT(*) AS n,
                  SUM(CASE WHEN f.error_code IS NULL THEN 1 ELSE 0 END) AS success_n
           FROM payload_fetch f
           JOIN transport_message t USING(transport_message_id)
           WHERE t.source_id LIKE 'WIS2%'
           GROUP BY 1,2 ORDER BY n DESC""",
    )
    access = _rows(
        conn,
        """SELECT source_family, endpoint, phase, status, COUNT(*) AS n
           FROM access_attempt GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""",
    )
    clock_rows = _rows(conn, "SELECT * FROM clock_health ORDER BY sampled_at_ns")
    duration_seconds = 0.0
    if runs:
        end_ns = max([row["ended_at_ns"] for row in run_ends], default=time.time_ns())
        duration_seconds = max(0.0, (end_ns - min(row["started_at_ns"] for row in runs)) / 1e9)
    stage3_ready = duration_seconds >= 72 * 3600 and len(valid_pairs) >= 150
    disposition = "BLOCKED_BY_ACCESS_OR_INSUFFICIENT_EVIDENCE"
    assert disposition in ALLOWED_DISPOSITIONS

    inventory_lines = [
        "# WIS2 payload inventory",
        "",
        f"OBSERVED WIS2 transport messages: `{wis2_messages}`; WIS2 payload fetch rows: `{sum(int(row['n']) for row in fetch_inventory)}`.",
        "",
        "| sniffed format | content type | rows | successful |",
        "|---|---|---:|---:|",
    ]
    inventory_lines.extend(
        f"| {row['sniffed_format']} | {row['content_type']} | {row['n']} | {row['success_n']} |"
        for row in fetch_inventory
    )
    if not fetch_inventory:
        inventory_lines.append("| none observed |  | 0 | 0 |")
    _write(reports_root / "wis2_payload_inventory.md", "\n".join(inventory_lines))

    health_lines = [
        "# Source health smoke report",
        "",
        f"OBSERVED duration: `{duration_seconds:.1f}s`; raw messages: `{messages}`; actionable seen rows: `{seen}`.",
        f"OBSERVED raw-index coverage: `{raw_paths}/{messages}`; unique transport payloads: `{raw_unique}`.",
        "UNVERIFIED 24-hour health: this run has not completed 24 hours." if duration_seconds < 24 * 3600 else "OBSERVED 24-hour duration threshold reached.",
        "",
        "| source | messages | unique payloads |",
        "|---|---:|---:|",
    ]
    health_lines.extend(f"| {row['source_id']} | {row['messages']} | {row['unique_payloads']} |" for row in source_counts)
    health_lines.extend(["", "## Access evidence", "", "| source | endpoint | phase | status | n |", "|---|---|---|---|---:|"])
    health_lines.extend(
        f"| {row['source_family']} | {row['endpoint']} | {row['phase']} | {row['status']} | {row['n']} |"
        for row in access
    )
    _write(reports_root / "source_health_24h.md", "\n".join(health_lines))

    ranking_lines = [
        "# Paired first-actionable latency ranking",
        "",
        f"OBSERVED clock-valid paired rows: `{len(valid_pairs)}`; all paired rows including invalid clock: `{len(all_pairs)}`.",
        "BLOCKED: 72-hour ranking threshold not reached." if not stage3_ready else "OBSERVED: 72-hour minimum duration/sample threshold reached.",
        "Rows with invalid clock never enter the ranking.",
        "",
        "| vantage | source A | source B | paired | median A-B sec | p90 A-B sec | A win rate |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    ranking_lines.extend(
        f"| {row['vantage_id']} | {row['source_a']} | {row['source_b']} | {row['paired_events']} | "
        f"{row['median_delta_a_minus_b_seconds']:.3f} | {row['p90_delta_a_minus_b_seconds']:.3f} | {row['source_a_win_rate']:.1%} |"
        for row in stats
    )
    if not stats:
        ranking_lines.append("| no clock-valid pairs |  |  | 0 |  |  |  |")
    _write(reports_root / "latency_ranking_72h.md", "\n".join(ranking_lines))

    unmatched = _rows(
        conn,
        """SELECT e.event_family_id, e.station_id, e.observation_time, e.report_kind,
                  GROUP_CONCAT(DISTINCT s.source_id) AS sources, COUNT(DISTINCT s.source_id) AS source_n
           FROM observation_event e
           JOIN source_observation_seen s USING(observation_version_id)
           GROUP BY e.observation_version_id HAVING source_n=1
           ORDER BY e.observation_time, e.station_id""",
    )
    unmatched_lines = [
        "# Unmatched events",
        "",
        f"OBSERVED single-source semantic versions: `{len(unmatched)}`.",
        "",
        "| event family | station | observation time | kind | source |",
        "|---|---|---|---|---|",
    ]
    unmatched_lines.extend(
        f"| {row['event_family_id']} | {row['station_id']} | {row['observation_time']} | {row['report_kind']} | {row['sources']} |"
        for row in unmatched[:500]
    )
    _write(reports_root / "unmatched_events.md", "\n".join(unmatched_lines))

    clock_lines = [
        "# Clock audit",
        "",
        f"OBSERVED clock samples: `{len(clock_rows)}`; invalid actionable rows: `{invalid_clock}`.",
        "",
        "| sampled ns | probe | offset ms | uncertainty ms | valid | raw |",
        "|---:|---|---:|---:|---:|---|",
    ]
    clock_lines.extend(
        f"| {row['sampled_at_ns']} | {row['probe_kind']} | {row['offset_ms']} | {row['uncertainty_ms']} | "
        f"{row['clock_valid']} | {str(row['raw_probe']).replace('|', '/')} |"
        for row in clock_rows
    )
    _write(reports_root / "clock_audit.md", "\n".join(clock_lines))

    table = pa.Table.from_pylist(
        all_pairs,
        schema=pa.schema(
            [
                ("observation_version_id", pa.string()),
                ("event_family_id", pa.string()),
                ("station_id", pa.string()),
                ("observation_time", pa.string()),
                ("report_kind", pa.string()),
                ("semantic_version_id", pa.string()),
                ("vantage_id", pa.string()),
                ("source_a", pa.string()),
                ("source_b", pa.string()),
                ("source_a_seen_ns", pa.int64()),
                ("source_b_seen_ns", pa.int64()),
                ("delta_a_minus_b_seconds", pa.float64()),
                ("source_a_clock_valid", pa.int64()),
                ("source_b_clock_valid", pa.int64()),
            ]
        ),
    )
    pq.write_table(table, reports_root / "FINAL_PAIRED_LATENCY.parquet")

    _write(
        reports_root / "FINAL_SOURCE_DISPOSITION.md",
        f"""# Final source disposition

Disposition: `{disposition}`

OBSERVED: `{duration_seconds:.1f}` seconds, `{observations}` semantic observation versions,
`{len(valid_pairs)}` clock-valid paired rows.

BLOCKED: a final source adoption decision requires the frozen 72-hour public
benchmark and then 7–14 days / at least 500 paired events. FAA SWIFT, live
MADIS LDM, IDD, and commercial-relay trials also require external access or
human agreements. Existing historical OMO evidence is retained as source-basis
evidence, not counted as WIS2 transport evidence.
""",
    )
    _write(
        reports_root / "FINAL_SOURCE_FINGERPRINT.md",
        """# Final source fingerprint

OBSERVED: source fingerprints currently distinguish WIS2 broker/channel,
AWC batch API, and AWC full cache by immutable raw hashes and first-seen clocks.

INFERRED: direct NOAA MADIS historical data and IEM MADISHF are the same OMO
observation family on overlapping timestamps; this does not identify a live
LDM transport fingerprint.

UNVERIFIED: FAA SWIFT, commercial relay, and authenticated target-product
fingerprints remain unavailable.
""",
    )
    _write(
        reports_root / "FINAL_ARCHITECTURE_RECOMMENDATION.md",
        """# Final architecture recommendation

Current action: continue the append-only collector; do not change live trading.

Candidate architecture pending evidence:

```text
race-and-dedupe notifications: WIS2 origin across four Global Brokers
reliable fallback:             WIS2 cache + bounded AWC batch/cache
context-only pre-signal:       OMO, only after source-to-routine basis modeling
application-gated candidate:   FAA SWIFT, only if paired evidence beats WIS2
```

Deploy identical commit/config to `US_EAST` and `ASIA_SG_OR_MY`. The current
single-node smoke result cannot separate upstream latency from network route.
""",
    )
    manifest = {
        "generated_at_ns": time.time_ns(),
        "git_commit": git_commit(),
        "runtime_db": str(db_path),
        "runtime_db_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
        "config_files": [_hash_file(path) for path in config_paths],
        "runs": runs,
        "run_ends": run_ends,
        "counts": {
            "transport_messages": messages,
            "observation_versions": observations,
            "source_seen_rows": seen,
            "clock_valid_pairs": len(valid_pairs),
            "all_pairs": len(all_pairs),
        },
        "disposition": disposition,
    }
    _json_write(reports_root / "FINAL_REPRODUCIBILITY_MANIFEST.json", manifest)

    stages = {
        0: ("OBSERVED", [Path(__file__).resolve().parent / "sql" / "schema.sql", db_path]),
        1: ("OBSERVED" if any(row["source_id"].startswith("WIS2") for row in source_counts) else "BLOCKED", [reports_root / "wis2_payload_inventory.md"]),
        2: ("OBSERVED" if any(row["source_id"].startswith("AWC") for row in source_counts) else "BLOCKED", [reports_root / "source_health_24h.md"]),
        3: ("OBSERVED" if stage3_ready else "BLOCKED", [reports_root / "latency_ranking_72h.md", reports_root / "unmatched_events.md", reports_root / "clock_audit.md"]),
        4: ("BLOCKED", []),
        5: ("BLOCKED", []),
        6: ("BLOCKED", []),
    }
    blockers = {
        0: [] if messages or observations else ["No collector evidence has been captured yet."],
        1: [] if stages[1][0] == "OBSERVED" else ["No WIS2 notification captured in this bounded window; access evidence is preserved."],
        2: [] if stages[2][0] == "OBSERVED" else ["No AWC response captured."],
        3: ["Frozen 24h smoke and 72h ranking duration/sample thresholds are not complete."] if not stage3_ready else [],
        4: ["Human FAA account/email verification/SAA and current Jumpstart Kit are not present."],
        5: ["Human MADIS LDM approval and declared Linux host/upstream are not present."],
        6: ["IDD eligibility/upstream and commercial seven-day trials are not present."],
    }
    next_tasks = {
        0: "Keep schema and replay tests fixed while collecting.",
        1: "Continue four-broker origin/cache collection without changing topic or identity contracts.",
        2: "Continue one-request/20-station AWC polling at 2s and cache verification at 60s.",
        3: "Run unchanged collectors for 24h smoke, then 72h ranking on two vantages.",
        4: "After human access, capture the live catalog/SAA hash/kit and add official JMS consumer evidence.",
        5: "After approval, timestamp LDM file ingress and measure OMO lead separately from METAR pairing.",
        6: "Retain IDD/RFI evidence; benchmark only authorized feeds with timestamp semantics.",
    }
    for stage, (status, paths) in stages.items():
        _write(
            reports_root / f"STAGE{stage}_EXECUTION_SUMMARY.md",
            f"# Stage {stage} execution summary\n\nOBSERVED status: `{status}`.\n\n"
            f"INFERRED: none beyond cited evidence.\n\nUNVERIFIED: long-window stability and unobserved sources.\n\n"
            f"BLOCKED: {', '.join(blockers[stage]) if blockers[stage] else 'none for the completed bounded scope'}.\n",
        )
        _json_write(
            reports_root / f"STAGE{stage}_EVIDENCE_INDEX.json",
            _evidence_index(paths + [reports_root / f"STAGE{stage}_EXECUTION_SUMMARY.md"], stage=stage, status=status),
        )
        _write(
            reports_root / f"STAGE{stage}_BLOCKERS.md",
            "# Stage blockers\n\n" + ("\n".join(f"- {item}" for item in blockers[stage]) if blockers[stage] else "None for the completed bounded scope."),
        )
        _write(reports_root / f"STAGE{stage}_NEXT_TASK.md", f"# Stage next task\n\n{next_tasks[stage]}")
    conn.close()
    return manifest
