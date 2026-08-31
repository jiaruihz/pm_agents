#!/usr/bin/env python3
"""Build audited Tmin settlement-source path truth for Seoul and Tokyo.

The exchange label is read from the append-only lowest-temperature Gamma
cache.  Historical IEM METAR is the broad station-path mirror.  Any final-rung
disagreement is fail-closed and can only be reconciled by a frozen direct
weather.com/WU official-history snapshot for that exact city-date.

This script is research-only.  It never writes production state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.observation_sources.fetchers import (
    FetchSettings,
    fetch_weather_com_history_hourly,
)
from weather_data_feed.observation_sources.router import ObservationSourceRequest


CITY_CONFIG = {
    "Seoul": {
        "station": "RKSI",
        "timezone": "Asia/Seoul",
        "country": "KR",
        "native_unit": "C",
    },
    "Tokyo": {
        "station": "RJTT",
        "timezone": "Asia/Tokyo",
        "country": "JP",
        "native_unit": "C",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def arithmetic_round(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def rung_contains(label: str, rung: int) -> bool:
    text = str(label).strip().lower()
    if text.lstrip("-").isdigit():
        return rung == int(text)
    first = text.split()[0]
    if first.lstrip("-").isdigit() and "or below" in text:
        return rung <= int(first)
    if first.lstrip("-").isdigit() and "or higher" in text:
        return rung >= int(first)
    return False


def load_exchange_labels(pm_history_dir: Path, start_date: str, end_date: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(pm_history_dir.glob("*.json")):
        try:
            city, target_date = path.stem.rsplit("_", 1)
        except ValueError:
            continue
        if city not in CITY_CONFIG or not (start_date <= target_date <= end_date):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        winners = []
        for bracket in payload.get("brackets") or []:
            try:
                final_price = float(bracket.get("final_price"))
            except (TypeError, ValueError):
                continue
            if final_price > 0.99:
                winners.append(bracket)
        rows.append(
            {
                "city": city,
                "target_date": target_date,
                "exchange_resolved_rung": (
                    str(winners[0].get("label")) if len(winners) == 1 else None
                ),
                "exchange_winner_count": len(winners),
                "exchange_condition_id": (
                    winners[0].get("condition_id") if len(winners) == 1 else None
                ),
                "exchange_market_id": (
                    winners[0].get("market_id") if len(winners) == 1 else None
                ),
                "exchange_source_file": str(path.relative_to(ROOT)),
                "exchange_source_sha256": _sha256(path),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("no exchange labels found")
    if frame[["city", "target_date"]].duplicated().any():
        raise ValueError("duplicate exchange city-date labels")
    return frame.sort_values(["target_date", "city"]).reset_index(drop=True)


def load_iem_path(city: str, path: Path, start_date: str, end_date: str) -> pd.DataFrame:
    cfg = CITY_CONFIG[city]
    frame = pd.read_csv(path)
    required = {"station", "valid", "tmpc", "metar"}
    if required - set(frame):
        raise ValueError(f"{path} missing {sorted(required - set(frame))}")
    frame["observation_event_time_utc"] = pd.to_datetime(frame["valid"], utc=True)
    local = frame["observation_event_time_utc"].dt.tz_convert(ZoneInfo(cfg["timezone"]))
    frame["target_date"] = local.dt.date.astype(str)
    frame = frame[frame["target_date"].between(start_date, end_date)].copy()
    frame["city"] = city
    frame["source"] = "iem_asos_station_archive"
    frame["source_version"] = "downloaded_2026-08-20"
    frame["published_available_time_utc"] = pd.NaT
    frame["raw_unit"] = "C"
    frame["raw_value"] = pd.to_numeric(frame["tmpc"], errors="coerce")
    frame["normalized_native_value"] = frame["raw_value"]
    frame["normalized_native_rung"] = frame["raw_value"].map(arithmetic_round)
    frame["local_observation_time"] = local.astype(str)
    frame["source_identity"] = cfg["station"]
    frame["pit_usage"] = "LABEL_TRUTH_ONLY_EX_POST_ARCHIVE"
    try:
        source_file = str(path.relative_to(ROOT))
    except ValueError:
        source_file = f"external:{path.name}"
    frame["source_file"] = source_file
    frame = frame.dropna(subset=["raw_value"])
    duplicate = frame.duplicated(["source_identity", "observation_event_time_utc"], keep=False)
    conflicts = (
        frame.loc[duplicate]
        .groupby(["source_identity", "observation_event_time_utc"])["raw_value"]
        .nunique()
    )
    if (conflicts > 1).any():
        raise ValueError(f"conflicting IEM revisions in {path}")
    return frame.drop_duplicates(
        ["source_identity", "observation_event_time_utc"], keep="last"
    )


def _snapshot_path(directory: Path, city: str, target_date: str) -> Path:
    return directory / f"{city}_{target_date}.json"


def fetch_wu_snapshot(city: str, target_date: str, output: Path) -> dict[str, Any]:
    cfg = CITY_CONFIG[city]
    request = ObservationSourceRequest(
        city=city,
        station_or_feed=cfg["station"],
        target_date=target_date,
        timezone_name=cfg["timezone"],
        source_key="weather_com_history_hourly",
        metadata={
            "native_unit": cfg["native_unit"],
            "weather_com_country": cfg["country"],
        },
    )
    result = fetch_weather_com_history_hourly(
        request,
        FetchSettings(
            timeout_sec=20.0,
            proxy_candidates=("http://127.0.0.1:7897", None),
        ),
    )
    if result.status != "ok" or not result.records:
        raise RuntimeError(f"WU fetch failed for {city} {target_date}: {result.status}")
    payload = {
        "schema_version": "tmin_wu_official_dispute_snapshot_v1",
        "city": city,
        "target_date": target_date,
        "station": cfg["station"],
        "timezone": cfg["timezone"],
        "fetched_at_utc": result.fetched_at_utc,
        "source": "weather_com_history_hourly",
        "source_metadata": result.metadata,
        "records": [
            {
                "observation_event_time_utc": record.obs_ts_utc,
                "published_available_time_utc": result.fetched_at_utc,
                "temp_c": record.temp_c,
                "native_temp": record.metadata.get("native_temp"),
                "native_round": record.metadata.get("native_round"),
                "raw_text": record.raw_text,
                "metadata": record.metadata,
            }
            for record in result.records
        ],
    }
    _write_json(output, payload)
    return payload


def load_wu_snapshot(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    city = str(payload["city"])
    cfg = CITY_CONFIG[city]
    rows = pd.DataFrame(payload["records"])
    rows["observation_event_time_utc"] = pd.to_datetime(
        rows["observation_event_time_utc"], utc=True
    )
    local = rows["observation_event_time_utc"].dt.tz_convert(ZoneInfo(cfg["timezone"]))
    rows["target_date"] = local.dt.date.astype(str)
    rows["city"] = city
    rows["source"] = "weather_com_history_hourly"
    rows["source_version"] = payload["source_metadata"].get("raw_payload_hash")
    rows["published_available_time_utc"] = pd.to_datetime(
        rows["published_available_time_utc"], utc=True
    )
    rows["raw_unit"] = "C"
    rows["raw_value"] = pd.to_numeric(rows["native_temp"], errors="coerce")
    rows["normalized_native_value"] = rows["raw_value"]
    rows["normalized_native_rung"] = rows["raw_value"].map(arithmetic_round)
    rows["local_observation_time"] = local.astype(str)
    rows["source_identity"] = cfg["station"]
    rows["pit_usage"] = "LABEL_TRUTH_ONLY_EX_POST_OFFICIAL_HISTORY"
    rows["source_file"] = str(path)
    return rows


def summarize_path(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    grouped = frame.groupby(["city", "target_date"], sort=True)
    summary = grouped.agg(
        **{
            f"{prefix}_observation_count": ("raw_value", "size"),
            f"{prefix}_final_min_native": ("normalized_native_value", "min"),
            f"{prefix}_first_observation_utc": ("observation_event_time_utc", "min"),
            f"{prefix}_last_observation_utc": ("observation_event_time_utc", "max"),
        }
    ).reset_index()
    summary[f"{prefix}_final_min_rung"] = summary[
        f"{prefix}_final_min_native"
    ].map(arithmetic_round)
    return summary


def build(args: argparse.Namespace) -> dict[str, Any]:
    if (
        args.output_dir.exists()
        and any(args.output_dir.iterdir())
        and not args.allow_existing_output
    ):
        raise FileExistsError(
            f"refusing to overwrite non-empty output directory: {args.output_dir}"
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.wu_snapshot_dir.mkdir(parents=True, exist_ok=True)
    exchange = load_exchange_labels(args.pm_history_dir, args.start_date, args.end_date)
    iem_paths = pd.concat(
        [
            load_iem_path("Seoul", args.iem_seoul, args.start_date, args.end_date),
            load_iem_path("Tokyo", args.iem_tokyo, args.start_date, args.end_date),
        ],
        ignore_index=True,
    )
    initial = exchange.merge(
        summarize_path(iem_paths, "iem"), on=["city", "target_date"], how="left"
    )
    initial["iem_exact_match"] = initial.apply(
        lambda row: bool(
            pd.notna(row["iem_final_min_rung"])
            and row["exchange_resolved_rung"] is not None
            and rung_contains(
                row["exchange_resolved_rung"], int(row["iem_final_min_rung"])
            )
        ),
        axis=1,
    )
    resolved_exchange = initial["exchange_winner_count"].eq(1)
    disputes = initial.loc[
        resolved_exchange
        & (initial["iem_final_min_rung"].isna() | ~initial["iem_exact_match"]),
        ["city", "target_date"],
    ]
    wu_frames: list[pd.DataFrame] = []
    for row in disputes.itertuples(index=False):
        snapshot = _snapshot_path(args.wu_snapshot_dir, row.city, row.target_date)
        if args.fetch_wu_disputes:
            fetch_wu_snapshot(row.city, row.target_date, snapshot)
        if snapshot.exists():
            wu_frames.append(load_wu_snapshot(snapshot))
    wu_paths = (
        pd.concat(wu_frames, ignore_index=True)
        if wu_frames
        else pd.DataFrame(columns=iem_paths.columns)
    )
    wu_summary = summarize_path(wu_paths, "wu") if not wu_paths.empty else pd.DataFrame(
        columns=["city", "target_date", "wu_observation_count", "wu_final_min_native",
                 "wu_first_observation_utc", "wu_last_observation_utc", "wu_final_min_rung"]
    )
    audit = initial.merge(wu_summary, on=["city", "target_date"], how="left")
    use_wu = ~audit["iem_exact_match"] & audit["wu_final_min_rung"].notna()
    audit["selected_source"] = "iem_asos_station_archive"
    audit.loc[use_wu, "selected_source"] = "weather_com_history_hourly"
    audit["reconstructed_final_min_rung"] = audit["iem_final_min_rung"]
    audit.loc[use_wu, "reconstructed_final_min_rung"] = audit.loc[
        use_wu, "wu_final_min_rung"
    ]
    audit["reconciliation_status"] = audit.apply(
        lambda row: (
            "EXCHANGE_RUNG_UNRESOLVED"
            if row["exchange_winner_count"] != 1
            else "EXACT_MATCH_WU_OFFICIAL_OVERRIDE"
            if row["selected_source"] == "weather_com_history_hourly"
            and rung_contains(
                row["exchange_resolved_rung"], int(row["reconstructed_final_min_rung"])
            )
            else "EXACT_MATCH_IEM_MIRROR"
            if pd.notna(row["reconstructed_final_min_rung"])
            and row["exchange_resolved_rung"] is not None
            and rung_contains(
                row["exchange_resolved_rung"], int(row["reconstructed_final_min_rung"])
            )
            else "UNRESOLVED_MISMATCH"
        ),
        axis=1,
    )
    resolved_mask = audit["exchange_winner_count"].eq(1)
    expected = int(resolved_mask.sum())
    covered = int(
        (resolved_mask & audit["reconstructed_final_min_rung"].notna()).sum()
    )
    exact = int(
        (resolved_mask & audit["reconciliation_status"].str.startswith("EXACT_MATCH")).sum()
    )
    coverage = covered / expected if expected else 0.0
    exact_rate = exact / covered if covered else 0.0
    unresolved = int(
        (resolved_mask & audit["reconciliation_status"].eq("UNRESOLVED_MISMATCH")).sum()
    )
    gate_pass = coverage >= 0.98 and exact_rate >= 0.99 and unresolved == 0
    selected_paths = iem_paths.copy()
    if not wu_paths.empty:
        dispute_keys = set(map(tuple, disputes[["city", "target_date"]].to_numpy()))
        selected_paths = selected_paths[
            ~selected_paths[["city", "target_date"]].apply(tuple, axis=1).isin(dispute_keys)
        ]
        selected_paths = pd.concat([selected_paths, wu_paths], ignore_index=True)
    selected_paths["truth_path_selected"] = True
    selected_paths.to_parquet(args.output_dir / "SETTLEMENT_SOURCE_PATH_ROWS.parquet", index=False)
    audit.to_parquet(args.output_dir / "SETTLEMENT_SOURCE_RECONCILIATION.parquet", index=False)
    gate = {
        "contract": "TMIN_SETTLEMENT_SOURCE_PATH_TRUTH_V1",
        "start_date": args.start_date,
        "end_date": args.end_date,
        "expected_exchange_city_dates": expected,
        "exchange_unresolved_city_dates": int((~resolved_mask).sum()),
        "covered_city_dates": covered,
        "coverage": coverage,
        "exact_match_city_dates": exact,
        "exact_match_rate": exact_rate,
        "initial_iem_exact_match_city_dates": int(
            (resolved_mask & audit["iem_exact_match"]).sum()
        ),
        "initial_iem_exact_match_rate": float(
            (resolved_mask & audit["iem_exact_match"]).sum() / expected
        ),
        "wu_official_dispute_audit_city_dates": int(use_wu.sum()),
        "unresolved_mismatches": unresolved,
        "gate_thresholds": {
            "coverage_min": 0.98,
            "exact_match_min": 0.99,
            "all_mismatches_audited": True,
        },
        "gate_pass": gate_pass,
        "status": "PASS" if gate_pass else "STOP_SETTLEMENT_SOURCE_TRUTH_BLOCKED",
        "timezone_rule": "IANA timezone conversion; Asia/Seoul and Asia/Tokyo are UTC+09:00",
        "prior_replay_bug": "The old replay used UTC-15h; correct local date is UTC+09h.",
        "label_truth_only": True,
        "pit_feature_authorization": False,
    }
    _write_json(args.output_dir / "SETTLEMENT_SOURCE_PATH_TRUTH_GATE.json", gate)
    return gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--iem-seoul",
        type=Path,
        default=ROOT / "runtime/research/tmin_cross_metar_basis_replay_v1/raw/RKSI_apr14_aug21.csv",
    )
    parser.add_argument(
        "--iem-tokyo",
        type=Path,
        default=ROOT / "runtime/research/tmin_cross_metar_basis_replay_v1/raw/RJTT_apr14_aug21.csv",
    )
    parser.add_argument(
        "--pm-history-dir",
        type=Path,
        default=ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history_lowest",
    )
    parser.add_argument("--wu-snapshot-dir", type=Path, required=True)
    parser.add_argument("--fetch-wu-disputes", action="store_true")
    parser.add_argument("--start-date", default="2026-04-15")
    parser.add_argument("--end-date", default="2026-08-20")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-existing-output", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
