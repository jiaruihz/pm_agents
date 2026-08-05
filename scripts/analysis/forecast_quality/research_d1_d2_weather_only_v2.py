#!/usr/bin/env python3
"""Pre-registered weather-only v2 gate over the clean run-aware dataset.

Model fitting is intentionally fail-closed until the clean dataset contains
enough distinct settled target dates.  This prevents legacy daily cache or the
one-shot probe from silently becoming training evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
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
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_D1_CHALLENGER_SPEC = (
    ROOT / "docs/analysis/2026-08/2026-08-05-d1-weather-only-clean-forward-freeze-v1.json"
)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_challenger_spec(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("model_identity") != "d1_weather_only_probability_challenger":
        raise ValueError("unexpected D-1 challenger model_identity")
    if payload.get("gates", {}).get("market_residual") != "not_run_by_contract":
        raise ValueError("D-1 challenger spec must keep market residual blocked")
    return {
        "model_identity": payload["model_identity"],
        "schema_version": payload.get("schema_version"),
        "spec_sha256": hashlib.sha256(raw).hexdigest(),
        "frozen_at_utc": payload.get("frozen_at_utc"),
        "artifact_role": payload.get("artifact_role"),
    }


def build_gate(
    rows: list[dict[str, Any]],
    *,
    minimum_settled_target_dates: int,
    d1_challenger: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scoreable = [row for row in rows if row.get("oof_scoreable") is True]
    target_dates = sorted({str(row["target_date"]) for row in scoreable})
    horizons = {
        horizon: sorted({str(row["target_date"]) for row in scoreable if row.get("horizon_days_local") == horizon})
        for horizon in (1, 2)
    }
    status_by_horizon = {
        horizon: (
            "ready_for_inner_train"
            if len(horizons[horizon]) >= minimum_settled_target_dates
            else "blocked_insufficient_clean_run_aware_history"
        )
        for horizon in (1, 2)
    }
    if all(status == "ready_for_inner_train" for status in status_by_horizon.values()):
        overall_status = "ready_for_inner_train"
    elif any(status == "ready_for_inner_train" for status in status_by_horizon.values()):
        overall_status = "partially_ready_by_horizon"
    else:
        overall_status = "blocked_insufficient_clean_run_aware_history"
    model_rows = [
        {
            "horizon_days_local": horizon,
            "model_code": code,
            "model_id": model_id,
            "status": status_by_horizon[horizon],
            "exact_bracket_logloss": None,
            "rung_brier": None,
            "rps": None,
            "winner_probability_mean": None,
            "calibration": None,
            "forward_status": "not_started",
        }
        for horizon in (1, 2)
        for code, model_id in MODELS
    ]
    model_rows.append(
        {
            "horizon_days_local": 1,
            "model_code": "L0",
            "model_id": d1_challenger["model_identity"],
            "status": status_by_horizon[1],
            "exact_bracket_logloss": None,
            "rung_brier": None,
            "rps": None,
            "winner_probability_mean": None,
            "calibration": None,
            "forward_status": "frozen_waiting_for_scoreable_dates",
        }
    )
    summary = {
        "schema_version": "d1_d2_weather_only_v2_preregistered_gate",
        "weather_only_status": overall_status,
        "weather_only_status_by_horizon": {str(key): value for key, value in status_by_horizon.items()},
        "scoreable_rows": len(scoreable),
        "distinct_scoreable_target_dates": len(target_dates),
        "scoreable_target_dates_by_horizon": {str(key): len(value) for key, value in horizons.items()},
        "minimum_settled_target_dates_per_horizon": minimum_settled_target_dates,
        "d1_frozen_challenger": d1_challenger,
        "d1_blocked_by_d2": False,
        "legacy_daily_cache_used": False,
        "estimated_run_timestamp_used": False,
        "market_features_used": False,
        "market_residual_status": "not_run_by_contract",
        "production_action": "none",
    }
    return model_rows, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum-settled-target-dates", type=int, default=30)
    parser.add_argument("--d1-frozen-challenger-spec", type=Path, default=DEFAULT_D1_CHALLENGER_SPEC)
    args = parser.parse_args(argv)
    rows = _jsonl(args.dataset)
    model_rows, summary = build_gate(
        rows,
        minimum_settled_target_dates=args.minimum_settled_target_dates,
        d1_challenger=load_challenger_spec(args.d1_frozen_challenger_spec),
    )
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
