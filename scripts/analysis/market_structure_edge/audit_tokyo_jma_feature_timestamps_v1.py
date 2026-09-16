#!/usr/bin/env python3
"""Audit Tokyo JMA dimensions and reconstruct only hash-verified exact fields.

Run inside the canonical JRS tmux permission context when ``--raw-jsonl``
points at the production runtime.  The JMA point archive is accepted for a
collector-exact event only when its per-observation raw hash matches the hash
captured by the original poll.  A later value with no matching hash remains a
late backfill and is excluded from PIT model input.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median
import sys
from typing import Any
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.high_frequency_observation_sources import (
    parse_jma_amedas_payload,
    stable_hash,
)


DEFAULT_RAW = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/"
    "live_cross_observations"
)
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_feature_timestamp_audit_v1"
)
JMA_POINT_BASE = "https://www.jma.go.jp/bosai/amedas/data/point/44166"
TOKYO = ZoneInfo("Asia/Tokyo")


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def timestamp_contract_violation(
    observation: datetime | None,
    decision: datetime | None,
    available: datetime | None,
) -> str:
    if observation is None or decision is None:
        return "missing_observation_or_exact_first_seen"
    if decision < observation:
        return "decision_before_observation"
    if available is not None and available < decision:
        return "available_before_decision"
    return ""


def iter_raw(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    paths = [path] if path.is_file() else sorted(
        path.glob("????-??-??/high_frequency_observations.jsonl")
    )
    for physical_path in paths:
        with physical_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("city") == "Tokyo" and row.get("source") == "jma_amedas":
                    rows.append(row)
    return rows


def fetch_point_payloads(
    observation_times: list[datetime],
) -> dict[str, dict[str, Any]]:
    bucket_keys = {
        f"{local:%Y%m%d}_{(local.hour // 3) * 3:02d}"
        for obs in observation_times
        for local in [obs.astimezone(TOKYO)]
    }
    output: dict[str, dict[str, Any]] = {}
    with httpx.Client(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "pm-agents-research/1"},
    ) as client:
        for bucket in sorted(bucket_keys):
            response = client.get(f"{JMA_POINT_BASE}/{bucket}.json")
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                output.update(payload)
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-jsonl", default=str(DEFAULT_RAW))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    raw_rows = iter_raw(Path(args.raw_jsonl))
    earliest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in raw_rows:
        observation = str(row.get("observation_time_utc") or "")
        raw_hash = str(row.get("raw_payload_hash") or "")
        if not observation or not raw_hash:
            continue
        key = (observation, raw_hash)
        first_seen = parse_dt(row.get("source_first_seen_at_utc"))
        prior = earliest.get(key)
        if prior is None or (
            first_seen is not None
            and (
                parse_dt(prior.get("source_first_seen_at_utc")) is None
                or first_seen
                < parse_dt(prior.get("source_first_seen_at_utc"))  # type: ignore[operator]
            )
        ):
            earliest[key] = row
    observations = [
        obs
        for row in earliest.values()
        if (obs := parse_dt(row.get("observation_time_utc"))) is not None
    ]
    official = fetch_point_payloads(observations)
    enriched: list[dict[str, Any]] = []
    timestamp_violations: list[dict[str, Any]] = []
    hash_mismatch_rows: list[dict[str, Any]] = []
    hash_mismatch = 0
    for raw in sorted(
        earliest.values(), key=lambda row: str(row.get("observation_time_utc"))
    ):
        obs = parse_dt(raw.get("observation_time_utc"))
        first_seen = parse_dt(raw.get("source_first_seen_at_utc"))
        available = parse_dt(
            raw.get("available_at_utc") or raw.get("source_published_at_utc")
        )
        violation = timestamp_contract_violation(obs, first_seen, available)
        if violation:
            timestamp_violations.append(
                {
                    "observation_time_utc": raw.get("observation_time_utc"),
                    "source_first_seen_at_utc": (
                        first_seen.isoformat() if first_seen else ""
                    ),
                    "available_at_utc": available.isoformat() if available else "",
                    "reason": violation,
                }
            )
            continue
        assert obs is not None and first_seen is not None
        local_key = obs.astimezone(TOKYO).strftime("%Y%m%d%H%M%S")
        item = official.get(local_key)
        candidate_hash = stable_hash({local_key: item}) if item else ""
        captured_hash = str(raw.get("raw_payload_hash") or "")
        verified = bool(item and captured_hash == candidate_hash)
        if not verified:
            hash_mismatch += 1
            hash_mismatch_rows.append(
                {
                    "observation_time_utc": obs.isoformat(),
                    "source_first_seen_at_utc": first_seen.isoformat(),
                    "captured_raw_payload_hash": captured_hash,
                    "current_jma_archive_hash": candidate_hash,
                    "reason": (
                        "archive_item_missing"
                        if item is None
                        else "archive_revision_hash_mismatch"
                    ),
                    "pit_treatment": "exclude_enrichment_keep_temperature_only",
                }
            )
            continue
        normalized = parse_jma_amedas_payload(
            {local_key: item},
            target_date=str(raw.get("target_date") or ""),
            fetched_at=first_seen,
        )
        if not normalized:
            continue
        parsed = normalized[0]
        enriched.append(
            {
                "city": "Tokyo",
                "source": "jma_amedas",
                "station": "44166",
                "target_date": raw.get("target_date"),
                "observation_time_utc": obs.isoformat(),
                "source_first_seen_at_utc": first_seen.isoformat(),
                "decision_ts_utc": first_seen.isoformat(),
                "available_at_utc": (
                    available.isoformat() if available is not None else ""
                ),
                "first_seen_age_min": round(
                    (first_seen - obs).total_seconds() / 60.0, 6
                ),
                "raw_payload_hash": captured_hash,
                "hash_verified_against_jma_point_archive": 1,
                "pit_lineage_class": "collector_exact_hash_verified_enrichment",
                "temp_c": parsed.get("temp_c"),
                "wind_speed_kt": parsed.get("wind_speed_kt"),
                "wind_dir_deg": parsed.get("wind_dir_deg"),
                "wind_gust_kt": parsed.get("wind_gust_kt"),
                "wind_gust_dir_deg": parsed.get("wind_gust_dir_deg"),
                "precipitation_10m_mm": parsed.get("precipitation_10m_mm"),
                "precipitation_1h_mm": parsed.get("precipitation_1h_mm"),
                "precipitation_3h_mm": parsed.get("precipitation_3h_mm"),
                "precipitation_24h_mm": parsed.get("precipitation_24h_mm"),
                "relative_humidity_pct": parsed.get("relative_humidity_pct"),
                "jma_temp_quality_code": parsed.get("jma_temp_quality_code"),
                "jma_wind_quality_code": parsed.get("jma_wind_quality_code"),
                "jma_precipitation_10m_quality_code": parsed.get(
                    "jma_precipitation_10m_quality_code"
                ),
            }
        )
    out = Path(args.out_dir)
    write_csv(out / "tokyo_jma_exact_enriched.csv", enriched)
    write_csv(out / "timestamp_violations.csv", timestamp_violations)
    write_csv(out / "hash_mismatch_rows.csv", hash_mismatch_rows)
    ages = [float(row["first_seen_age_min"]) for row in enriched]
    feature_fields = (
        "temp_c",
        "wind_speed_kt",
        "wind_dir_deg",
        "wind_gust_kt",
        "wind_gust_dir_deg",
        "precipitation_10m_mm",
        "relative_humidity_pct",
    )
    summary = {
        "schema_version": "tokyo_jma_feature_timestamp_audit_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_matching_rows": len(raw_rows),
        "distinct_observation_revisions": len(earliest),
        "hash_verified_enriched_rows": len(enriched),
        "hash_mismatch_or_archive_missing": hash_mismatch,
        "timestamp_violations": len(timestamp_violations),
        "first_seen_age_min": {
            "min": min(ages) if ages else None,
            "median": median(ages) if ages else None,
            "max": max(ages) if ages else None,
        },
        "feature_non_null_rows": {
            field: sum(row.get(field) is not None for row in enriched)
            for field in feature_fields
        },
        "excluded_from_model_features": [
            "maxTemp",
            "maxTempTime",
            "minTemp",
            "minTempTime",
            "gustTime",
            "source_published_at_utc",
        ],
        "clock_contract": {
            "feature_cutoff": "decision_ts_utc=source_first_seen_at_utc",
            "require": "observation_time_utc <= decision_ts_utc <= available_at_utc",
            "late_backfill": "excluded unless original raw_payload_hash matches",
            "labels": "strictly after decision_ts_utc; never joined as features",
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
