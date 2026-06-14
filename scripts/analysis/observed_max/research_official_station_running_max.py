#!/usr/bin/env python3
"""Build running-max residual detail for cities whose official station differs from wu_obs.

Mirrors the schema of research_m3_observed_max_residual.py detail output
(city, target_date, decision_hour_local, running_max_*, final_max_*) but sources
observations from the official-station IEM METAR cache fetched by
research_official_station_alignment.py.

Output can be concatenated with the wu_obs-based residual detail to extend
orderbook backtests to the previously-mismatched cities.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO = Path(__file__).resolve().parents[3]

# city -> (official_icao, market_unit, iem_data_col, tz)
DIFF_CITIES = {
    "Paris": ("LFPB", "C", "tmpc", "Europe/Paris"),
    "London": ("EGLC", "C", "tmpc", "Europe/London"),
    "Milan": ("LIMC", "C", "tmpc", "Europe/Rome"),
    "Chicago": ("KORD", "F", "tmpf", "America/Chicago"),
    "KualaLumpur": ("WMKK", "C", "tmpc", "Asia/Kuala_Lumpur"),
    "PanamaCity": ("MPMG", "C", "tmpc", "America/Panama"),
}

MIN_OBS_PER_DAY = 8


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache-dir", default=str(REPO / "runtime/rule_source_research/obs_cache")
    )
    parser.add_argument("--decision-hours", default="10,11,12,13,14,15,16,17,18,19,20,21")
    parser.add_argument("--end", default="2026-06-10")
    parser.add_argument(
        "--output-dir",
        default=str(REPO / "docs/analysis/2026-06/generated/official_station_running_max_v0"),
    )
    args = parser.parse_args()

    hours = [int(x) for x in args.decision_hours.split(",") if x.strip()]
    cache_dir = Path(args.cache_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for city, (icao, unit, data_col, tz_name) in DIFF_CITIES.items():
        files = sorted(cache_dir.glob(f"iem_{icao}_*.csv"))
        if not files:
            print(f"!! no IEM cache for {city} ({icao}), skip")
            continue
        df = pd.read_csv(files[-1])
        df["valid"] = pd.to_datetime(df["valid"], utc=True)
        df[data_col] = pd.to_numeric(df[data_col], errors="coerce")
        df = df.dropna(subset=[data_col])
        tz = ZoneInfo(tz_name)
        df["local_ts"] = df["valid"].dt.tz_convert(tz)
        df["local_date"] = df["local_ts"].dt.date.astype(str)
        df = df[df["local_date"] <= args.end]

        for day, g in df.groupby("local_date"):
            if len(g) < MIN_OBS_PER_DAY:
                continue
            final_max = float(g[data_col].max())
            for hour in hours:
                cutoff = g[g["local_ts"].dt.hour <= hour]
                # require an observation in the decision hour itself so the
                # running max is actually fresh at decision time
                if cutoff.empty or cutoff["local_ts"].dt.hour.max() < hour:
                    continue
                running_max = float(cutoff[data_col].max())
                current_temp = float(cutoff.sort_values("local_ts")[data_col].iloc[-1])
                if unit == "C":
                    running_c, final_c, current_c = running_max, final_max, current_temp
                else:
                    running_c = (running_max - 32.0) * 5.0 / 9.0
                    final_c = (final_max - 32.0) * 5.0 / 9.0
                    current_c = (current_temp - 32.0) * 5.0 / 9.0
                residual_c = final_c - running_c
                rows.append(
                    {
                        "city": city,
                        "icao": icao,
                        "station_kind": "official_iem",
                        "target_date": day,
                        "decision_hour_local": hour,
                        "n_obs_day": len(g),
                        "running_max_c": running_c,
                        "final_max_c": final_c,
                        "current_temp_c": current_c,
                        "residual_c": residual_c,
                        "running_max_raw": running_max,
                        "final_max_raw": final_max,
                        "unit": unit,
                        "floor_c_bucket_delta": math.floor(final_c) - math.floor(running_c),
                        "round_c_bucket_delta": math.floor(final_c + 0.5) - math.floor(running_c + 0.5),
                    }
                )

    detail = pd.DataFrame(rows)
    detail_path = out_dir / "official_station_running_max_detail.csv"
    detail.to_csv(detail_path, index=False)

    if len(detail):
        summary = (
            detail.assign(jump=detail["round_c_bucket_delta"].ge(1).astype(int))
            .groupby(["city", "decision_hour_local"])
            .agg(days=("jump", "size"), jump_rate=("jump", "mean"))
            .reset_index()
        )
        summary_path = out_dir / "official_station_running_max_summary.csv"
        summary.to_csv(summary_path, index=False)
        print(summary.to_string(index=False))

    manifest = {
        "experiment": "official_station_running_max_v0",
        "cities": {c: v[0] for c, v in DIFF_CITIES.items()},
        "decision_hours": hours,
        "min_obs_per_day": MIN_OBS_PER_DAY,
        "detail_rows": int(len(detail)),
        "notes": [
            "Observations from IEM METAR archive for the official resolution stations.",
            "running max requires at least one obs in the decision hour itself.",
            "Schema mirrors m3_observed_max_residual_detail.csv core columns.",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nsaved {detail_path} rows={len(detail)}")


if __name__ == "__main__":
    main()
