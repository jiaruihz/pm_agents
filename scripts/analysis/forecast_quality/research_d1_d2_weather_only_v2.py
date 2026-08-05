#!/usr/bin/env python3
"""Pre-registered weather-only v2 gate over the clean run-aware dataset.

Model fitting is intentionally fail-closed until the clean dataset contains
enough distinct settled target dates.  This prevents legacy daily cache or the
one-shot probe from silently becoming training evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


MODELS = (
    ("A", "climatology_source_season"),
    ("B", "pooled_normal_negative_control"),
    ("C", "bias_corrected_pooled"),
    ("D", "coherent_pooled_ordinal_survival"),
    ("E", "partial_hierarchy_v2"),
    ("F", "hierarchy_plus_run_spread_lead_season"),
    ("G", "hierarchy_plus_physical_width"),
)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum-settled-target-dates", type=int, default=30)
    args = parser.parse_args(argv)
    rows = _jsonl(args.dataset)
    scoreable = [row for row in rows if row.get("oof_scoreable") is True]
    target_dates = sorted({str(row["target_date"]) for row in scoreable})
    horizons = {
        horizon: sorted({str(row["target_date"]) for row in scoreable if row.get("horizon_days_local") == horizon})
        for horizon in (1, 2)
    }
    enough = all(len(horizons[horizon]) >= args.minimum_settled_target_dates for horizon in (1, 2))
    status = "ready_for_inner_train" if enough else "blocked_insufficient_clean_run_aware_history"
    model_rows = [
        {
            "model_code": code,
            "model_id": model_id,
            "status": status,
            "exact_bracket_logloss": None,
            "rung_brier": None,
            "rps": None,
            "winner_probability_mean": None,
            "calibration": None,
            "forward_status": "not_started",
        }
        for code, model_id in MODELS
    ]
    summary = {
        "schema_version": "d1_d2_weather_only_v2_preregistered_gate",
        "weather_only_status": status,
        "scoreable_rows": len(scoreable),
        "distinct_scoreable_target_dates": len(target_dates),
        "scoreable_target_dates_by_horizon": {str(key): len(value) for key, value in horizons.items()},
        "minimum_settled_target_dates_per_horizon": args.minimum_settled_target_dates,
        "legacy_daily_cache_used": False,
        "estimated_run_timestamp_used": False,
        "market_features_used": False,
        "market_residual_status": "not_run_by_contract",
        "production_action": "none",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "model_score_table.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(model_rows[0]))
        writer.writeheader()
        writer.writerows(model_rows)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
