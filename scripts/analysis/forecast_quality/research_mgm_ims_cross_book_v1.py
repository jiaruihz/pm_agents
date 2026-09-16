#!/usr/bin/env python3
"""Evaluate Istanbul, Tel Aviv, and Ankara fast-source crosses with PIT books."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)
from src.strategies.runtime.production import load_production_spec  # noqa: E402

CITIES = {"Istanbul", "TelAviv", "Ankara"}
METAR_SOURCES = {"aviationweather_metar", "synopticdata_timeseries"}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def safe_float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def read_first_crosses(path: Path) -> list[dict[str, Any]]:
    first: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    with path.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            city = str(row.get("city") or "")
            prior = row.get("metar_running_max_round_c")
            candidate = row.get("source_round_c")
            if city not in CITIES or prior is None or candidate is None:
                continue
            key = (city, str(row.get("target_date")), int(prior), int(candidate))
            ts = parse_dt(row.get("ts_utc"))
            old_ts = parse_dt(first.get(key, {}).get("ts_utc"))
            if key not in first or (ts is not None and (old_ts is None or ts < old_ts)):
                first[key] = row
    return sorted(first.values(), key=lambda row: str(row.get("ts_utc") or ""))


def read_metar_reports(path: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    first: dict[tuple[str, str, str], dict[str, Any]] = {}
    paths = [path] if path.is_file() else sorted(path.glob("????-??-??/sources.jsonl"))
    for physical_path in paths:
        with physical_path.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                city = str(row.get("city") or "")
                source = str(row.get("source") or "")
                report_ts = row.get("source_report_ts_utc")
                temp = safe_float(row.get("temp_c"))
                if city not in CITIES or source not in METAR_SOURCES or not report_ts or temp is None:
                    continue
                key = (city, str(row.get("target_date")), str(report_ts))
                detect = parse_dt(row.get("local_detect_ts_utc") or row.get("ts_utc"))
                old_detect = parse_dt(first.get(key, {}).get("local_detect_ts_utc") or first.get(key, {}).get("ts_utc"))
                if key not in first or (detect is not None and (old_detect is None or detect < old_detect)):
                    first[key] = row
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (city, target_date, _), row in first.items():
        grouped[(city, target_date)].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: str(row.get("source_report_ts_utc")))
    return grouped


def read_settlements(path: Path) -> dict[tuple[str, str], int]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        rows = conn.execute(
            """
            SELECT city, target_date, bracket
            FROM settlement_outcomes
            WHERE city IN ('Istanbul', 'TelAviv', 'Ankara')
              AND settlement_status = 'settled' AND final_price >= 0.999
            """
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for city, target_date, bracket in rows:
        try:
            out[(str(city), str(target_date))] = int(float(bracket))
        except (TypeError, ValueError):
            continue
    return out


def enrich(
    crosses: list[dict[str, Any]],
    reports: dict[tuple[str, str], list[dict[str, Any]]],
    settlements: dict[tuple[str, str], int],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in crosses:
        city = str(row["city"])
        target_date = str(row["target_date"])
        prior_report = parse_dt(row.get("latest_metar_report_ts_utc"))
        trigger = parse_dt(row.get("ts_utc"))
        source_obs = parse_dt(row.get("source_obs_ts_utc"))
        source_detect = parse_dt(row.get("source_detect_ts_utc"))
        candidate = int(row["source_round_c"])
        next_report = next(
            (
                report
                for report in reports.get((city, target_date), [])
                if parse_dt(report.get("source_report_ts_utc"))
                and prior_report
                and parse_dt(report.get("source_report_ts_utc")) > prior_report
            ),
            None,
        )
        next_temp = safe_float(next_report.get("temp_c")) if next_report else None
        next_round = round(next_temp) if next_temp is not None else None
        next_detect = parse_dt(next_report.get("local_detect_ts_utc") or next_report.get("ts_utc")) if next_report else None
        day_temps = [safe_float(report.get("temp_c")) for report in reports.get((city, target_date), [])]
        day_temps = [value for value in day_temps if value is not None]
        best_ask = safe_float(row.get("best_ask"))
        ask_size = safe_float(row.get("ask_size"))
        max_ask = safe_float(row.get("max_no_ask"))
        required_shares = 5.0
        winning_bracket = settlements.get((city, target_date))
        book_tradeable = best_ask is not None and max_ask is not None and best_ask <= max_ask and ask_size is not None and ask_size >= required_shares
        settlement_no_win = winning_bracket != int(row["metar_running_max_round_c"]) if winning_bracket is not None else ""
        out.append(
            {
                "city": city,
                "target_date": target_date,
                "source": row.get("source"),
                "source_obs_ts_utc": row.get("source_obs_ts_utc"),
                "source_detect_ts_utc": row.get("source_detect_ts_utc"),
                "trigger_ts_utc": row.get("ts_utc"),
                "source_temp_c": row.get("source_temp_c"),
                "prior_metar_report_ts_utc": row.get("latest_metar_report_ts_utc"),
                "prior_metar_max_c": row.get("metar_running_max_round_c"),
                "candidate_c": candidate,
                "next_metar_report_ts_utc": next_report.get("source_report_ts_utc") if next_report else "",
                "next_metar_detect_ts_utc": (next_report.get("local_detect_ts_utc") or next_report.get("ts_utc")) if next_report else "",
                "next_metar_temp_c": next_temp if next_temp is not None else "",
                "next_report_cross_hit": next_round >= candidate if next_round is not None else "",
                "eventual_metar_cross_hit": round(max(day_temps)) >= candidate if day_temps else "",
                "source_detect_lag_min": round((source_detect - source_obs).total_seconds() / 60, 3) if source_detect and source_obs else "",
                "lead_to_next_metar_detect_min": round((next_detect - trigger).total_seconds() / 60, 3) if next_detect and trigger else "",
                "best_no_ask": best_ask if best_ask is not None else "",
                "ask_size": ask_size if ask_size is not None else "",
                "book_available": best_ask is not None,
                "book_at_or_below_cap": best_ask is not None and max_ask is not None and best_ask <= max_ask,
                "required_probe_shares": required_shares,
                "book_executable_size": best_ask is not None and ask_size is not None and ask_size >= required_shares,
                "book_tradeable": book_tradeable,
                "settlement_winning_bracket_c": winning_bracket if winning_bracket is not None else "",
                "settlement_no_win": settlement_no_win,
                "gross_pnl_per_share": round((1.0 if settlement_no_win else 0.0) - best_ask, 4)
                if book_tradeable and settlement_no_win != ""
                else "",
                "question": row.get("question"),
            }
        )
    return out


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for city in sorted(CITIES):
        group = [row for row in rows if row["city"] == city]
        labeled = [row for row in group if row["next_report_cross_hit"] != ""]
        eventual = [row for row in group if row["eventual_metar_cross_hit"] != ""]
        lags = [float(row["source_detect_lag_min"]) for row in group if row["source_detect_lag_min"] != ""]
        leads = [float(row["lead_to_next_metar_detect_min"]) for row in group if row["lead_to_next_metar_detect_min"] != ""]
        tradeable = [row for row in group if row["book_tradeable"] and row["settlement_no_win"] != ""]
        tradeable_cost = sum(float(row["best_no_ask"]) for row in tradeable)
        tradeable_pnl = sum(float(row["gross_pnl_per_share"]) for row in tradeable)
        out.append(
            {
                "city": city,
                "active_dates": len({row["target_date"] for row in group}),
                "first_crosses": len(group),
                "next_report_labeled": len(labeled),
                "next_report_hits": sum(bool(row["next_report_cross_hit"]) for row in labeled),
                "next_report_precision": round(sum(bool(row["next_report_cross_hit"]) for row in labeled) / len(labeled), 4) if labeled else "",
                "eventual_hits": sum(bool(row["eventual_metar_cross_hit"]) for row in eventual),
                "eventual_precision": round(sum(bool(row["eventual_metar_cross_hit"]) for row in eventual) / len(eventual), 4) if eventual else "",
                "books_available": sum(bool(row["book_available"]) for row in group),
                "books_at_or_below_cap": sum(bool(row["book_at_or_below_cap"]) for row in group),
                "books_executable_size": sum(bool(row["book_executable_size"]) for row in group),
                "settled_tradeable_books": len(tradeable),
                "settled_tradeable_wins": sum(bool(row["settlement_no_win"]) for row in tradeable),
                "settled_tradeable_gross_pnl_per_one_share": round(tradeable_pnl, 4),
                "settled_tradeable_gross_roi": round(tradeable_pnl / tradeable_cost, 4) if tradeable_cost else "",
                "median_source_detect_lag_min": round(statistics.median(lags), 2) if lags else "",
                "median_lead_to_next_metar_detect_min": round(statistics.median(leads), 2) if leads else "",
            }
        )
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    spec = load_production_spec()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", default=str(spec.data_feed_runtime_root))
    parser.add_argument("--db-path", default=str(spec.canonical_db_path))
    parser.add_argument("--run-id", help="stable immutable artifact run identity")
    parser.add_argument("--output-dir")
    args = parser.parse_args(argv)

    runtime_root = Path(args.runtime_root)
    crosses = read_first_crosses(runtime_root / "output/fast_source_prev_no_trial/events.jsonl")
    reports = read_metar_reports(runtime_root / "output/source_events")
    settlements = read_settlements(Path(args.db_path))
    rows = enrich(crosses, reports, settlements)
    summary = summarize(rows)
    out_dir = resolve_run_output(
        "mgm_ims_cross_book_v1",
        run_id=args.run_id,
        explicit_output=Path(args.output_dir) if args.output_dir else None,
    )
    prepare_new_run_output(out_dir)
    write_csv(out_dir / "first_crosses.csv", rows)
    write_csv(out_dir / "city_summary.csv", summary)
    window = [
        min(row["target_date"] for row in rows),
        max(row["target_date"] for row in rows),
    ] if rows else [None, None]
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": window,
        "grain": "first PIT cross per city/date/prior METAR max/candidate",
        "summary": summary,
        "rows": rows,
        "verdict": {"significance": "NA", "baseline": "NA", "forward": "NA", "conclusion": "inconclusive"},
    }
    json_path = out_dir / "summary.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# MGM / IMS Cross and Book v1",
        "",
        f"Generated: `{payload['generated_at_utc']}`",
        f"Window: `{payload['window'][0] or 'none'}..{payload['window'][1] or 'none'}`",
        "",
        "| city | days | crosses | next METAR hits | precision | eventual | books | <= cap | settled tradeable W/L | gross PnL (1 share/event) | gross ROI | source lag | METAR lead |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {row['city']} | {row['active_dates']} | {row['first_crosses']} | {row['next_report_hits']}/{row['next_report_labeled']} | "
            f"{row['next_report_precision']} | {row['eventual_precision']} | {row['books_available']} | {row['books_at_or_below_cap']} | "
            f"{row['settled_tradeable_wins']}/{row['settled_tradeable_books'] - row['settled_tradeable_wins']} | "
            f"{row['settled_tradeable_gross_pnl_per_one_share']} | {row['settled_tradeable_gross_roi']} | "
            f"{row['median_source_detect_lag_min']}m | {row['median_lead_to_next_metar_detect_min']}m |"
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            "significance=NA; baseline=NA; forward=NA; conclusion=inconclusive",
            "",
            "The sample is descriptive only. It is below the city-level active-day threshold and does not authorize live orders.",
            "Tradeable means first-seen NO ask <= the runner cap with at least 5 shares displayed. Gross PnL/ROI excludes fees and is a counterfactual, not a live fill result.",
            "Historical book fields use the first runner event recorded at the PIT trigger; absent books are not reconstructed from later prices.",
            "",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"summary": summary, "rows": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
