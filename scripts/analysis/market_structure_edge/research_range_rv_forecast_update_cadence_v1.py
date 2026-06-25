#!/usr/bin/env python3
"""Audit city-level forecast update cadence for Range RV.

The Range RV shadow runner consumes weather-predict paper snapshots. Local
clock buckets are not enough: forecast_source/model_init/run_age/hash determine
what information was actually visible. This report reconstructs that lineage
from paper snapshots and joins it back to Range RV shadow candidates.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
TARGET_METRIC = "range_rv_forecast_update_cadence_v1"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-26-range-rv-forecast-update-cadence-v1.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-26-range-rv-forecast-update-cadence-v1.md"
OUT_CSV_DEFAULT = ROOT / "docs/analysis/2026-06/generated/range_rv_forecast_update_cadence_v1/city_source_cadence.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", default=str(ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots"))
    parser.add_argument(
        "--range-rv-journal",
        default=str(ROOT / "runtime/weather_edge_v1/remote_pm_agent/range_rv_shadow_v0/shadow_journal.jsonl"),
    )
    parser.add_argument("--pm-history-dir", default=str(ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history"))
    parser.add_argument("--db-path", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--out-csv", default=str(OUT_CSV_DEFAULT))
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "NA"
    return f"{value * 100:+.1f}%"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def sql_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def sql_scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def mandatory_self_check(db_path: Path) -> dict[str, Any]:
    with connect_ro(db_path) as conn:
        return {
            "max_fact_built_at_utc": sql_scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
            "trade_class_distribution": sql_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
            ),
            "settlement_status_distribution": sql_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
            ),
            "candidate_coverage": sql_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "order_fill_coverage": sql_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
        }


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_snapshot_groups(snapshot_dir: Path) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    paths = sorted(snapshot_dir.glob("snapshot_*.json"))
    for path in paths:
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        snapshot_ts = str(payload.get("ts_utc") or "")
        if not snapshot_ts:
            continue
        for rec in payload.get("records") or []:
            if rec.get("record_type") != "edge_signal" or rec.get("probability_status") != "ok":
                continue
            city = str(rec.get("city") or "")
            event_date = str(rec.get("event_date") or "")
            source = str(rec.get("forecast_source") or "")
            model = str(rec.get("model") or "")
            if not city or not event_date or not source or not model:
                continue
            key = (snapshot_ts, city, event_date, source, model)
            group = groups.setdefault(
                key,
                {
                    "snapshot_ts_utc": snapshot_ts,
                    "snapshot_path": str(path),
                    "city": city,
                    "event_date": event_date,
                    "forecast_source": source,
                    "model_version": model,
                    "city_pool": rec.get("city_pool"),
                    "timezone_name": rec.get("timezone_name"),
                    "city_local_date_at_snapshot": rec.get("city_local_date_at_snapshot"),
                    "ts_local": rec.get("ts_local"),
                    "model_init_utc_estimated": rec.get("model_init_utc_estimated"),
                    "model_run_age_hours_estimated": safe_float(rec.get("model_run_age_hours_estimated")),
                    "forecast_target_lead_hours_estimated": safe_float(rec.get("forecast_target_lead_hours_estimated")),
                    "forecast_values_hash": rec.get("forecast_values_hash"),
                    "forecast_max_f": safe_float(rec.get("forecast_max_f")),
                    "forecast_max_native": safe_float(rec.get("forecast_max_native")),
                    "forecast_peak_hour_local": safe_float(rec.get("forecast_peak_hour_local")),
                    "forecast_peak_time_local": rec.get("forecast_peak_time_local"),
                    "forecast_peak_time_utc": rec.get("forecast_peak_time_utc"),
                    "metar_current_max_f": safe_float(rec.get("metar_current_max_f")),
                    "brackets": 0,
                    "prob_sum": 0.0,
                },
            )
            group["brackets"] += 1
            p = safe_float(rec.get("model_prob"))
            if p is not None:
                group["prob_sum"] += p

    out = list(groups.values())
    for row in out:
        ts_local = str(row.get("ts_local") or "")
        try:
            row["decision_hour_local"] = int(ts_local.split(" ")[1].split(":")[0]) if " " in ts_local else None
        except (IndexError, ValueError):
            row["decision_hour_local"] = None
        row["forecast_state_key"] = forecast_state_key(row)
    return sorted(out, key=lambda r: (r["city"], r["event_date"], r["forecast_source"], r["model_version"], r["snapshot_ts_utc"]))


def forecast_state_key(row: dict[str, Any]) -> str:
    value_hash = row.get("forecast_values_hash")
    if value_hash:
        return f"hash:{value_hash}"
    return "|".join(
        [
            f"init:{row.get('model_init_utc_estimated')}",
            f"max:{row.get('forecast_max_f')}",
            f"peak:{row.get('forecast_peak_time_utc')}",
        ]
    )


def annotate_changes(groups: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[tuple[str, str, str, str, str], dict[str, Any]]]:
    by_stream: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in groups:
        by_stream[(row["city"], row["event_date"], row["forecast_source"], row["model_version"])].append(row)

    first_seen: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    annotated = []
    for stream_key, rows in by_stream.items():
        rows.sort(key=lambda r: r["snapshot_ts_utc"])
        last_state = None
        state_seq = 0
        for row in rows:
            state = str(row["forecast_state_key"])
            changed = state != last_state
            if changed:
                state_seq += 1
                first_seen[(stream_key[0], stream_key[1], stream_key[2], stream_key[3], state)] = row
            first = first_seen[(stream_key[0], stream_key[1], stream_key[2], stream_key[3], state)]
            age_min = (parse_ts(row["snapshot_ts_utc"]) - parse_ts(first["snapshot_ts_utc"])).total_seconds() / 60.0
            out = {
                **row,
                "forecast_changed_since_previous_snapshot": changed,
                "forecast_state_seq": state_seq,
                "forecast_state_first_seen_utc": first["snapshot_ts_utc"],
                "forecast_state_first_seen_local": first.get("ts_local"),
                "forecast_state_age_minutes": age_min,
            }
            annotated.append(out)
            last_state = state
    by_exact = {(r["snapshot_ts_utc"], r["city"], r["event_date"], r["forecast_source"], r["model_version"]): r for r in annotated}
    return annotated, by_exact


def percentile(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if math.isfinite(v))
    if not vals:
        return None
    idx = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * q))))
    return vals[idx]


def summarize_cadence(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_city_source: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in groups:
        by_city_source[(row["city"], row["forecast_source"], row["model_version"])].append(row)

    out = []
    for (city, source, model), rows in by_city_source.items():
        state_rows = [r for r in rows if r["forecast_changed_since_previous_snapshot"]]
        run_ages = [float(r["model_run_age_hours_estimated"]) for r in state_rows if r.get("model_run_age_hours_estimated") is not None]
        state_seen_ts = sorted(parse_ts(r["snapshot_ts_utc"]) for r in state_rows)
        gaps = [(b - a).total_seconds() / 3600.0 for a, b in zip(state_seen_ts, state_seen_ts[1:])]
        hours = Counter(r.get("decision_hour_local") for r in state_rows if r.get("decision_hour_local") is not None)
        inits = Counter(str(r.get("model_init_utc_estimated") or "") for r in state_rows)
        hash_avail = sum(1 for r in rows if r.get("forecast_values_hash")) / len(rows) if rows else None
        out.append(
            {
                "city": city,
                "forecast_source": source,
                "model_version": model,
                "snapshot_groups": len(rows),
                "event_dates": len({r["event_date"] for r in rows}),
                "forecast_state_changes": len(state_rows),
                "unique_forecast_states": len({r["forecast_state_key"] for r in rows}),
                "hash_available_rate": hash_avail,
                "first_snapshot_ts_utc": min(r["snapshot_ts_utc"] for r in rows),
                "last_snapshot_ts_utc": max(r["snapshot_ts_utc"] for r in rows),
                "change_hours_local_top": ",".join(f"{k}:{v}" for k, v in hours.most_common(5)),
                "model_init_top": ",".join(f"{k}:{v}" for k, v in inits.most_common(5)),
                "first_seen_run_age_p25": percentile(run_ages, 0.25),
                "first_seen_run_age_p50": percentile(run_ages, 0.50),
                "first_seen_run_age_p75": percentile(run_ages, 0.75),
                "state_gap_hours_p50": percentile(gaps, 0.50),
            }
        )
    return sorted(out, key=lambda r: (r["city"], r["forecast_source"], r["model_version"]))


def winner_for(pm_history_dir: Path, city: str, event_date: str) -> tuple[str | None, str]:
    path = pm_history_dir / f"{city}_{event_date}.json"
    if not path.exists():
        return None, "missing_event"
    data = json.loads(path.read_text())
    winners = []
    for bracket in data.get("brackets", []):
        price = safe_float(bracket.get("final_price"))
        if price is not None and price >= 0.99:
            winners.append(str(bracket.get("label")))
    if len(winners) != 1:
        return None, f"winner_count_{len(winners)}"
    return winners[0], "settled"


def range_rv_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(row["city"]), str(row["event_date"]), str(row.get("forecast_source") or ""), str(row.get("model_version") or ""))


def evaluate_range_rv_with_forecast_meta(
    journal_rows: list[dict[str, Any]],
    forecast_by_exact: dict[tuple[str, str, str, str, str], dict[str, Any]],
    pm_history_dir: Path,
) -> dict[str, Any]:
    by_group: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in journal_rows:
        by_group[range_rv_key(row)].append(row)

    selected = []
    for group in by_group.values():
        group.sort(key=lambda r: str(r.get("snapshot_ts_utc")))
        d0_morning = []
        prev_day = []
        for row in group:
            meta = forecast_by_exact.get(
                (
                    str(row.get("snapshot_ts_utc")),
                    str(row.get("city")),
                    str(row.get("event_date")),
                    str(row.get("forecast_source") or ""),
                    str(row.get("model_version") or ""),
                )
            )
            if not meta:
                continue
            hour = meta.get("decision_hour_local")
            local_date = str(meta.get("city_local_date_at_snapshot") or "")
            event_date = str(row.get("event_date"))
            if local_date < event_date:
                prev_day.append((row, meta))
            if local_date == event_date and hour is not None and 6 <= int(hour) <= 11:
                d0_morning.append((row, meta))
        if prev_day:
            row, meta = prev_day[-1]
            selected.append((row, meta, "prev_day_latest"))
        if d0_morning:
            row, meta = d0_morning[-1]
            selected.append((row, meta, "d0_morning_latest"))

    evaluated = []
    for row, meta, policy in selected:
        winner, status = winner_for(pm_history_dir, str(row["city"]), str(row["event_date"]))
        if status != "settled":
            pnl = None
            hit = None
        else:
            inside = set(map(str, row.get("inside_brackets") or []))
            hit = winner in inside
            gross_cost = sum(float(leg["best_ask"]) for leg in row.get("legs") or [] if leg.get("best_ask") is not None)
            if row["expression"] == "inside_yes":
                pnl = (1.0 if hit else 0.0) - gross_cost
            elif row["expression"] == "outside_no":
                outside = {str(leg.get("bracket")) for leg in row.get("legs") or []}
                payout = (len(outside) - 1.0) if winner in outside else float(len(outside))
                pnl = payout - gross_cost
            else:
                pnl = None
        evaluated.append(
            {
                **{k: row.get(k) for k in ["city", "event_date", "forecast_source", "model_version", "snapshot_ts_utc", "expression", "inside_brackets"]},
                "policy": policy,
                "settlement_status": status,
                "winner": winner,
                "hit": hit,
                "pnl": pnl,
                "effective_range_cost": safe_float(row.get("effective_range_cost")),
                "range_model_mass_norm": safe_float(row.get("range_model_mass_norm")),
                "model_init_utc_estimated": meta.get("model_init_utc_estimated"),
                "model_run_age_hours_estimated": meta.get("model_run_age_hours_estimated"),
                "forecast_values_hash": meta.get("forecast_values_hash"),
                "forecast_state_seq": meta.get("forecast_state_seq"),
                "forecast_state_age_minutes": meta.get("forecast_state_age_minutes"),
                "forecast_changed_since_previous_snapshot": meta.get("forecast_changed_since_previous_snapshot"),
                "forecast_state_first_seen_utc": meta.get("forecast_state_first_seen_utc"),
                "decision_hour_local": meta.get("decision_hour_local"),
                "ts_local": meta.get("ts_local"),
                "city_local_date_at_snapshot": meta.get("city_local_date_at_snapshot"),
            }
        )
    return {
        "selected_rows": len(evaluated),
        "by_policy": grouped_perf(evaluated, "policy"),
        "by_policy_source_init": grouped_perf(evaluated, "policy_source_init"),
        "rows": evaluated,
    }


def grouped_perf(rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if mode == "policy":
            key = str(row["policy"])
        elif mode == "policy_source_init":
            key = "|".join([str(row["policy"]), str(row.get("forecast_source")), str(row.get("model_init_utc_estimated"))])
        else:
            key = "all"
        groups[key].append(row)
    out = []
    for key, vals in groups.items():
        settled = [r for r in vals if r.get("pnl") is not None]
        cost = sum(float(r["effective_range_cost"]) for r in settled if r.get("effective_range_cost") is not None)
        pnl = sum(float(r["pnl"]) for r in settled)
        wins = sum(1 for r in settled if r.get("pnl") is not None and float(r["pnl"]) > 0)
        ages = [float(r["forecast_state_age_minutes"]) for r in vals if r.get("forecast_state_age_minutes") is not None]
        out.append(
            {
                "key": key,
                "rows": len(vals),
                "settled_rows": len(settled),
                "event_dates": len({r["event_date"] for r in settled}),
                "cities": len({r["city"] for r in settled}),
                "cost": cost,
                "pnl": pnl,
                "roi": None if cost <= 0 else pnl / cost,
                "hit_rate": None if not settled else wins / len(settled),
                "avg_forecast_state_age_min": None if not ages else sum(ages) / len(ages),
            }
        )
    return sorted(out, key=lambda r: (r["settled_rows"], r["roi"] if r["roi"] is not None else -999), reverse=True)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def compact_perf(row: dict[str, Any]) -> list[Any]:
    return [
        row["key"],
        row["settled_rows"],
        row["event_dates"],
        row["cities"],
        f"{row['cost']:.3f}",
        f"{row['pnl']:+.3f}",
        pct(row["roi"]),
        pct(row["hit_rate"]),
        "NA" if row["avg_forecast_state_age_min"] is None else f"{row['avg_forecast_state_age_min']:.1f}",
    ]


def render_md(payload: dict[str, Any]) -> str:
    cadence_rows = [
        [
            r["city"],
            r["forecast_source"].replace("open_meteo_live_", ""),
            r["snapshot_groups"],
            r["event_dates"],
            r["forecast_state_changes"],
            r["unique_forecast_states"],
            pct(r["hash_available_rate"]),
            r["model_init_top"],
            r["change_hours_local_top"],
            "NA" if r["first_seen_run_age_p50"] is None else f"{r['first_seen_run_age_p50']:.1f}",
        ]
        for r in payload["cadence_summary"][:80]
    ]
    policy_rows = [compact_perf(r) for r in payload["range_rv_join"]["by_policy"]]
    source_init_rows = [compact_perf(r) for r in payload["range_rv_join"]["by_policy_source_init"][:20]]
    lines = [
        "# Range RV Forecast Update Cadence v1",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        "",
        "## Data Snapshot",
        "",
        "- Evidence: weather-predict paper snapshots plus Range RV zero-notional shadow journal.",
        f"- snapshot_groups: `{payload['snapshot_group_count']}` from `{payload['snapshot_file_count']}` snapshot files.",
        f"- snapshot range: `{payload['snapshot_range']['min']}` -> `{payload['snapshot_range']['max']}`.",
        f"- range_rv_shadow_rows: `{payload['range_rv_shadow_rows']}`.",
        "- `forecast_values_hash` is only available in newer snapshots; older rows fall back to `model_init + forecast_max + peak_time` as state key.",
        "",
        "### Mandatory SQL Self-Check",
        "",
        "```json",
        json.dumps(payload["self_check"], indent=2, sort_keys=True),
        "```",
        "",
        "## Main Correction",
        "",
        "`local hour` is not a forecast information state. For Range RV, the unit to analyze is `city + event_date + forecast_source + model_init/run_age/hash + snapshot_ts_utc`.",
        "",
        "The historical Range RV journal did not persist model init, run age, or forecast hash. This report reconstructs them from paper snapshots, which is good enough for audit but not enough for clean forward telemetry; current runner rows should write these fields directly.",
        "",
        "## City/Source Cadence Sample",
        "",
        table(
            [
                "city",
                "source",
                "groups",
                "dates",
                "state_changes",
                "unique_states",
                "hash_rate",
                "init_top",
                "change_hours_local_top",
                "run_age_p50",
            ],
            cadence_rows,
        ),
        "",
        "Full CSV: `docs/analysis/2026-06/generated/range_rv_forecast_update_cadence_v1/city_source_cadence.csv`.",
        "",
        "## Range RV Re-read",
        "",
        "Policies below are exploratory re-reads using reconstructed forecast metadata. `prev_day_latest` means latest candidate before the city-local target date; `d0_morning_latest` means latest target-date candidate from local 06:00-11:59.",
        "",
        table(
            ["policy", "rows", "dates", "cities", "cost", "pnl", "roi", "hit", "avg_state_age_min"],
            policy_rows,
        ),
        "",
        "## Source/Init Breakdown",
        "",
        table(
            ["policy|source|init", "rows", "dates", "cities", "cost", "pnl", "roi", "hit", "avg_state_age_min"],
            source_init_rows,
        ),
        "",
        "## Interpretation",
        "",
        "- The previous `latest_before_local_18` framing is too coarse and should not be used as a candidate label.",
        "- D0 morning can be a valid research branch, but only as `forecast-update Range RV`, not as prev-day Range RV.",
        "- The next selection variable should be forecast-state arrival and market repricing lag: source/init/run_age/hash-change, not just local clock hour.",
        "- Regime/METAR layers should stay separate: they can calibrate or size forecast-update trades, but they are not the same as forecast distribution generation.",
        "",
        "## Required Runtime Fix",
        "",
        "`range_rv_shadow_v0` must persist these fields before future forward shadow can be treated as clean evidence: `model_init_utc_estimated`, `model_run_age_hours_estimated`, `forecast_values_hash`, `forecast_state_changed`, `forecast_state_first_seen_utc`, and `forecast_state_age_minutes`. This is implemented for new runner rows; historical rows in this audit are reconstructed from snapshots.",
        "",
        "significance=NA, baseline=NA, forward=NA, conclusion=diagnostic_only",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    snapshot_dir = Path(args.snapshot_dir)
    groups = load_snapshot_groups(snapshot_dir)
    annotated, by_exact = annotate_changes(groups)
    cadence = summarize_cadence(annotated)
    journal = load_jsonl(Path(args.range_rv_journal))
    rv_join = evaluate_range_rv_with_forecast_meta(journal, by_exact, Path(args.pm_history_dir))
    snapshot_ts = [r["snapshot_ts_utc"] for r in groups]
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "parameters": {
            "snapshot_dir": str(snapshot_dir),
            "range_rv_journal": args.range_rv_journal,
            "pm_history_dir": args.pm_history_dir,
            "db_path": args.db_path,
        },
        "self_check": mandatory_self_check(Path(args.db_path)),
        "snapshot_file_count": len(list(snapshot_dir.glob("snapshot_*.json"))),
        "snapshot_group_count": len(groups),
        "snapshot_range": {"min": min(snapshot_ts) if snapshot_ts else None, "max": max(snapshot_ts) if snapshot_ts else None},
        "range_rv_shadow_rows": len(journal),
        "cadence_summary": cadence,
        "range_rv_join": {k: v for k, v in rv_join.items() if k != "rows"},
        "range_rv_join_rows_sample": rv_join["rows"][:200],
    }
    write_csv(Path(args.out_csv), cadence)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    out_md.write_text(render_md(payload))
    print(
        json.dumps(
            {
                "snapshot_groups": len(groups),
                "range_rv_shadow_rows": len(journal),
                "out_json": str(out_json),
                "out_md": str(out_md),
                "out_csv": str(args.out_csv),
                "policy_summary": payload["range_rv_join"]["by_policy"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
