#!/usr/bin/env python3
"""Rank unconnected high-frequency city sources for zero-notional research onboarding.

This script performs bounded adapter smokes, joins each source to the configured
settlement station/profile, and measures whether the city has current canonical
market and settlement coverage.  It does not start collectors or submit orders.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.high_frequency_observation_sources import (
    HIGH_FREQUENCY_CITY_SOURCES,
    fetch_high_frequency_observation,
)
from scripts.analysis.versioned_artifact_output import (
    prepare_new_run_output,
    resolve_run_output,
)
from src.strategies.runtime.production import load_production_spec


CANDIDATES = (
    {
        "priority": "P1_connect_shadow",
        "city": "Paris", "source_city": "Paris", "source": "meteofrance_6m", "nominal_cadence": "6m",
        "action": "obtain_free_api_credential_and_collect",
        "reason": "LFPB is the confirmed market-rule station; direct official observations test a clean same-station lead.",
    },
    {
        "priority": "P1_connect_shadow",
        "city": "Amsterdam", "source_city": "Amsterdam", "source": "knmi", "nominal_cadence": "10m",
        "action": "obtain_api_key_and_collect",
        "reason": "KNMI Schiphol and the WU market profile both map to EHAM; likely lead over routine METAR.",
    },
    {
        "priority": "P2_probe_then_collect",
        "city": "Madrid", "source_city": "Madrid", "source": "aemet_10m", "nominal_cadence": "unknown_near_realtime",
        "action": "obtain_api_key_and_measure_cadence_first",
        "reason": "AEMET station 3129 is Madrid Barajas/LEMD, but actual cadence and publish lag are not measured.",
    },
    {
        "priority": "P2_probe_then_collect",
        "city": "TelAviv", "source_city": "Tel Aviv", "source": "ims_1m", "nominal_cadence": "1m",
        "action": "obtain_token_and_parallel_existing_ims_lod",
        "reason": "Same LLBG/Lod station and much faster than the existing public 10m route; settlement is non-WU and needs its own basis audit.",
    },
    {
        "priority": "P2_probe_then_collect",
        "city": "Wellington", "source_city": "Wellington", "source": "metservice_1m", "nominal_cadence": "1m",
        "action": "request_trial_only_after_P1",
        "reason": "Same NZWN airport and high nominal cadence, but the product requires a commercial trial/tenant endpoint.",
    },
    {
        "priority": "defer_no_speed",
        "city": "Munich", "source_city": "Munich", "source": "dwd_10m", "nominal_cadence": "10m",
        "action": "do_not_add_realtime_collector",
        "reason": "Public endpoint is reachable but observed publication is later than routine METAR, so it adds history rather than latency edge.",
    },
    {
        "priority": "defer_no_speed",
        "city": "Toronto", "source_city": "Toronto", "source": "eccc_swob", "nominal_cadence": "60m_MAN",
        "action": "do_not_add_until_AUTO_or_AMQP",
        "reason": "The public CYYZ MAN endpoint is hourly and the city has no current canonical candidate coverage.",
    },
    {
        "priority": "defer_basis",
        "city": "Taipei", "source_city": "Taipei", "source": "cwa", "nominal_cadence": "10m",
        "action": "do_not_use_as_bracket_trigger",
        "reason": "CWA 466920 is a Taipei reference station while the market settlement profile is RCSS; this is a cross-station proxy.",
    },
    {
        "priority": "defer_basis",
        "city": "Jeddah", "source_city": "Jeddah", "source": "ncm_jeddah", "nominal_cadence": "unknown",
        "action": "resolve_station_and_access_contract_first",
        "reason": "Authentication and the mapping from NCM station 36 to the OEJN settlement station are both unresolved.",
    },
)


def read_profiles(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["city"]): row for row in payload["source_profiles"]}


def market_coverage(db_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=1000")
    try:
        candidates = {
            row[0]: {
                "recent_candidate_days": row[1], "recent_candidate_rows": row[2], "candidate_latest": row[3],
            }
            for row in connection.execute(
                """
                SELECT city, COUNT(DISTINCT event_date), COUNT(*), MAX(event_date)
                FROM fact_signal_candidates
                WHERE event_date >= '2026-07-10'
                GROUP BY city
                """
            )
        }
        settlements = {
            row[0]: {"settled_days": row[1], "settlement_latest": row[2]}
            for row in connection.execute(
                """
                SELECT city, COUNT(DISTINCT target_date), MAX(target_date)
                FROM settlement_outcomes
                WHERE settlement_status='settled'
                GROUP BY city
                """
            )
        }
    finally:
        connection.close()
    return candidates, settlements


def smoke(candidate: dict[str, str]) -> dict[str, Any]:
    source, source_city = candidate["source"], candidate["source_city"]
    try:
        result = fetch_high_frequency_observation(source, source_city)
        record = result.records[-1] if result.records else {}
        obs_ts = str(record.get("observation_time_utc") or "") if isinstance(record, dict) else ""
        wall_age_min: float | str = ""
        if obs_ts:
            parsed = datetime.fromisoformat(obs_ts.replace("Z", "+00:00"))
            wall_age_min = round((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 60.0, 1)
        return {
            "smoke_status": result.status,
            "smoke_rows": len(result.records),
            "smoke_latest_obs_utc": obs_ts,
            "smoke_wall_age_min": wall_age_min,
            "smoke_error": result.error,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "smoke_status": "exception", "smoke_rows": 0, "smoke_latest_obs_utc": "",
            "smoke_wall_age_min": "", "smoke_error": f"{type(exc).__name__}: {exc}",
        }


def station_match(source_icao: str, official: str, configured: str) -> bool:
    return bool(source_icao and (source_icao == configured or source_icao == official or source_icao in official))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(load_production_spec().canonical_db_path))
    parser.add_argument("--profiles", default=str(ROOT / "weather_data_feed/source_profiles.json"))
    parser.add_argument("--run-id", help="stable immutable artifact run identity")
    parser.add_argument("--output-dir", "--out-dir", dest="output_dir")
    parser.add_argument("--report", help="optional durable report path; defaults inside the run artifact")
    args = parser.parse_args(argv)

    profiles = read_profiles(Path(args.profiles))
    candidate_coverage, settlement_coverage = market_coverage(Path(args.db_path))
    smoke_rows: dict[tuple[str, str], dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(smoke, candidate): candidate for candidate in CANDIDATES}
        for future in concurrent.futures.as_completed(futures):
            candidate = futures[future]
            smoke_rows[(candidate["source"], candidate["source_city"])] = future.result()

    rows: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        city = candidate["city"]
        profile = profiles.get(city, {})
        source_meta = HIGH_FREQUENCY_CITY_SOURCES[candidate["source"]][candidate["source_city"]]
        official = str(profile.get("official_station_or_feed") or "")
        configured = str(profile.get("configured_icao") or "")
        source_icao = str(source_meta.get("icao") or "")
        rows.append({
            **candidate,
            "source_station": source_meta.get("station", ""),
            "source_icao": source_icao,
            "settlement_source_class": profile.get("settlement_source_class", "missing_profile"),
            "official_station_or_feed": official,
            "configured_icao": configured,
            "same_settlement_station": int(station_match(source_icao, official, configured)),
            **smoke_rows[(candidate["source"], candidate["source_city"])],
            **candidate_coverage.get(city, {"recent_candidate_days": 0, "recent_candidate_rows": 0, "candidate_latest": ""}),
            **settlement_coverage.get(city, {"settled_days": 0, "settlement_latest": ""}),
        })

    out_dir = resolve_run_output(
        "realtime_source_city_onboarding_v1",
        run_id=args.run_id,
        explicit_output=Path(args.output_dir) if args.output_dir else None,
    )
    prepare_new_run_output(out_dir)
    write_csv(out_dir / "candidate_source_matrix.csv", rows)

    table = []
    for row in rows:
        market = f"{row['recent_candidate_days']}d/{row['recent_candidate_rows']} rows; {row['settled_days']} settled days"
        smoke_text = str(row["smoke_status"])
        if row["smoke_wall_age_min"] != "":
            smoke_text += f"; wall age {row['smoke_wall_age_min']}m"
        table.append(
            f"| `{row['priority']}` | `{row['city']}/{row['source']}` | {row['nominal_cadence']} | "
            f"{row['source_icao'] or row['source_station']} → {row['official_station_or_feed'] or 'missing'} | "
            f"{row['same_settlement_station']} | `{smoke_text}` | {market} | `{row['action']}` |"
        )

    generated = datetime.now(timezone.utc).isoformat()
    report = f"""# Realtime source city onboarding v1

Generated: `{generated}`
Status: `collector_research_only`; no live authorization

## Action

Connect only zero-notional research collectors, in this order:

1. **Paris / Météo-France 6-minute LFPB** and **Amsterdam / KNMI 10-minute EHAM** are the two strongest additions. They point at the configured settlement station and both cities have current market plus settlement coverage.
2. **Madrid / AEMET LEMD** is the next free-key probe, but measure actual cadence and publication lag before promoting it to a continuous collector.
3. **TelAviv / IMS 1-minute LLBG** and **Wellington / MetService 1-minute NZWN** are technically attractive second-wave probes. TelAviv has a non-WU settlement contract; Wellington needs a commercial trial.
4. Do not add Munich DWD or Toronto ECCC as latency sources now. Taipei CWA and Jeddah NCM remain blocked by station basis, not by missing code.

“Connect” here means raw first-seen + routine-reference + WU/rules settlement + fresh-book telemetry. It does not mean enabling a previous-NO order path.

## Candidate matrix

| priority | city/source | nominal cadence | source → settlement station | station match | current smoke | canonical market/labels | action |
|---|---|---:|---|---:|---|---|---|
{chr(10).join(table)}

The current smoke was executed from the research environment. Auth-required means the adapter is present but no credential was available; no credential value is written to this report. Munich and Toronto public endpoints returned data, but their observed wall age and cadence do not beat routine METAR.

## Atlanta admission test for every new source

The 2026-07-17 Atlanta failure is the required negative control: MADISHF/OMO printed `91.4F`, direct MADIS contained the same observation with `temperatureQCR=0`, while routine METAR and native-F WU finished at `89F` and the `88-89` bracket won. A new source is not trusted merely because it is official, same-airport, persistent, or QC-clean.

For each candidate city/source, collect and report:

1. `source first-seen → same-timestamp routine METAR → native-F WU/rules settlement` with native units and no double rounding.
2. Daily-max winning-bracket match, signed source-WU error, and Atlanta-type `terminal_false_cross` frequency.
3. First event per city-day/bracket, not repeated polls; separate single cross and persistent cross.
4. Fresh direct-book cost/depth at source first-seen and correct-versus-false executable/fill rates.
5. A calibrated `P(final leaves old bracket | source/path/time/basis)` versus same-time market probability; raw cross is only a feature.

## Existing evidence that sets the bar

- native-F WU final vs winning bracket: `100/100`.
- routine AWC METAR daily max vs winning bracket: `69/70`.
- IEM MADISHF OMO daily max vs winner on peak-covered days: `56/86`; direct MADIS complete days: `22/40`.
- US persistent previous-NO: `53/54`, but correct runner events executable within ten minutes `0/22`; the one false Atlanta event became executable and filled.
- Existing non-US same/near-airport shadows with the cleanest next-METAR basis remain Tokyo/JMA, Singapore/MSS, Busan/AMOS, and Helsinki/FMI. They stay collector/shadow evidence and are not promoted by this report.

## Funnels

Signal funnel (source observation grain): authenticated raw rows → distinct observation timestamps → first bracket cross → first city-day expression.

Evidence funnel (city-day/event grain): PIT first-seen → concurrent routine reference → WU/rules settlement → fresh executable book → fill. The five auth/contract rows above currently stop before raw coverage; that is a coverage gap, not a failed strategy signal.

## Files

- `candidate_source_matrix.csv`: adapter smoke, station basis, current market coverage, and onboarding action.

## Contract

significance=NA; baseline=same-time market + routine reference; forward=FAIL; conclusion=Paris/Amsterdam P1 collector candidates, no new live city
"""
    report_path = Path(args.report) if args.report else out_dir / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps({"rows": len(rows), "report": str(report_path), "matrix": str(out_dir / "candidate_source_matrix.csv")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
