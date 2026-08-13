#!/usr/bin/env python3
"""Freeze city/model forecast residuals for the D-1 consensus shadow."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed_service.legacy_weather_predict.city_pools import (  # noqa: E402
    FULL_CITY_CONFIGS,
)
from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    resolve_content_addressed_artifact,
)

HISTORICAL_FORECAST_ERRORS_SHA256 = (
    "35802482add6330d9f39f90ecff1b11c4813704b07ac710f7a153f1f6a100719"
)
DEFAULT_OUTPUT = (
    ROOT / "configs/weather/d1_multisource_consensus_shadow_v1.json"
)
DEFAULT_CUTOFF = "2026-07-07"
MIN_DATES = 20


def model_geography_is_valid(city: str, model_key: str) -> bool:
    cfg = FULL_CITY_CONFIGS.get(city) or {}
    lat = float(cfg.get("lat", math.nan))
    lon = float(cfg.get("lon", math.nan))
    if not math.isfinite(lat) or not math.isfinite(lon):
        return False
    if model_key in {
        "ncep_hrrr_conus",
        "ncep_nbm_conus",
        "ncep_nam_conus",
        "gem_regional",
        "gem_hrdps_continental",
    }:
        return 15.0 <= lat <= 75.0 and -170.0 <= lon <= -45.0
    if model_key in {"icon_eu", "icon_d2"}:
        return 25.0 <= lat <= 72.0 and -25.0 <= lon <= 45.0
    if model_key == "meteofrance_arome_france_hd":
        return 38.0 <= lat <= 56.0 and -12.0 <= lon <= 16.0
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=resolve_content_addressed_artifact(
            HISTORICAL_FORECAST_ERRORS_SHA256
        ),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = pd.read_csv(args.input)
    rows["target_date"] = rows["target_date"].astype(str)
    rows = rows[rows["target_date"].le(args.cutoff)].copy()
    rows = rows[
        [
            model_geography_is_valid(str(row.city), str(row.model_key))
            for row in rows.itertuples(index=False)
        ]
    ].copy()
    summary = (
        rows.groupby(
            ["city", "model_key", "model_label", "provider", "tier"],
            as_index=False,
            dropna=False,
        )
        .agg(
            dates=("target_date", "nunique"),
            bias_correction_f=("error_f", "mean"),
            median_bias_correction_f=("error_f", "median"),
            mae_f=("abs_error_f", "mean"),
            residual_std_f=("error_f", "std"),
        )
    )
    summary = summary[summary["dates"].ge(MIN_DATES)].copy()
    records = json.loads(summary.to_json(orient="records"))
    payload = {
        "schema_version": "d1_multisource_consensus_policy_v1",
        "status": "frozen_shadow_only",
        "training_cutoff": args.cutoff,
        "minimum_city_model_dates": MIN_DATES,
        "bias_definition": "mean(settlement_mid_f - forecast_max_f)",
        "correction_formula": "corrected_forecast_f = forecast_max_f + bias_correction_f",
        "consensus_formula": "median(corrected_forecast_f across eligible models)",
        "spread_formula": "q75(corrected_forecast_f) - q25(corrected_forecast_f)",
        "regional_model_policy": "geographic_domain_filter_v1",
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "rows": len(records),
                "cities": int(summary["city"].nunique()),
                "models": int(summary["model_label"].nunique()),
                "cutoff": args.cutoff,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
