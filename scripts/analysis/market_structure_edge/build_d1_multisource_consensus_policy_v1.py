#!/usr/bin/env python3
"""Freeze city/model forecast residuals for the D-1 consensus shadow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "historical_forecast_enrichment_bias_v1/daily_error_rows.csv"
)
DEFAULT_OUTPUT = (
    ROOT / "configs/weather/d1_multisource_consensus_shadow_v1.json"
)
DEFAULT_CUTOFF = "2026-07-07"
MIN_DATES = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = pd.read_csv(args.input)
    rows["target_date"] = rows["target_date"].astype(str)
    rows = rows[rows["target_date"].le(args.cutoff)].copy()
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
