#!/usr/bin/env python3
"""Build the deployable calibration bundle used by HeadA shadow telemetry.

The research CSVs remain the auditable build inputs.  Production runners read
only the compact, versioned bundle under ``src/.../config`` so a clean
git checkout never depends on untracked ``docs/analysis/generated`` files.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BIAS = (
    ROOT
    / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1"
    / "daily_error_rows.csv"
)
DEFAULT_FORECAST = (
    ROOT
    / "docs/analysis/2026-07/generated/historical_forecast_enrichment_bias_v1"
    / "daily_error_rows.csv"
)
DEFAULT_PCAL = (
    ROOT / "docs/analysis/2026-07/2026-07-03-low-price-yes-tail-pcal-v2.json"
)
DEFAULT_BUNDLE = (
    ROOT
    / "src/strategies/weather_edge_v1/config"
    / "low_price_yes_tail_calibration_v1.json.gz"
)
DEFAULT_PCAL_OUT = (
    ROOT
    / "src/strategies/weather_edge_v1/config"
    / "low_price_yes_tail_pcal_v2.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bias-rows", type=Path, default=DEFAULT_BIAS)
    parser.add_argument("--forecast-rows", type=Path, default=DEFAULT_FORECAST)
    parser.add_argument("--pcal-json", type=Path, default=DEFAULT_PCAL)
    parser.add_argument("--bundle-out", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--pcal-out", type=Path, default=DEFAULT_PCAL_OUT)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_rows(path: Path, columns: tuple[str, ...]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            values = [row.get(column) for column in columns]
            if all(value not in (None, "") for value in values):
                rows.append(values)
    return rows


def main() -> int:
    args = parse_args()
    bias_columns = ("city", "model", "date", "error_f_actual_minus_forecast")
    forecast_columns = (
        "city",
        "target_date",
        "model_key",
        "model_label",
        "error_f",
    )
    bias_rows = read_rows(args.bias_rows, bias_columns)
    forecast_rows = read_rows(args.forecast_rows, forecast_columns)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "schema_version": "low_price_yes_tail_calibration_v1",
        "generated_at_utc": generated,
        "source": {
            "bias_rows": {
                "path": str(args.bias_rows.relative_to(ROOT)),
                "sha256": sha256(args.bias_rows),
                "columns": list(bias_columns),
                "rows": len(bias_rows),
            },
            "forecast_rows": {
                "path": str(args.forecast_rows.relative_to(ROOT)),
                "sha256": sha256(args.forecast_rows),
                "columns": list(forecast_columns),
                "rows": len(forecast_rows),
            },
        },
        "legacy_bias_rows": bias_rows,
        "forecast_calibration_rows": forecast_rows,
    }
    args.bundle_out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.bundle_out, "wt", encoding="utf-8", compresslevel=9) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))

    pcal_source = json.loads(args.pcal_json.read_text(encoding="utf-8"))
    pcal_payload = {
        "schema_version": "low_price_yes_tail_pcal_v2",
        "packaged_at_utc": generated,
        "source_path": str(args.pcal_json.relative_to(ROOT)),
        "source_sha256": sha256(args.pcal_json),
        "frozen_selector": pcal_source["frozen_selector"],
    }
    args.pcal_out.write_text(
        json.dumps(pcal_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "bundle": str(args.bundle_out),
                "bundle_bytes": args.bundle_out.stat().st_size,
                "legacy_bias_rows": len(bias_rows),
                "forecast_calibration_rows": len(forecast_rows),
                "pcal": str(args.pcal_out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
