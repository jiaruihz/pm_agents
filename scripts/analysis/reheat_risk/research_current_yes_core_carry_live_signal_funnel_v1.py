#!/usr/bin/env python3
"""Audit the frozen v3 Core Carry live signal funnel from raw runtime scores."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_DIR = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "current_yes_core_carry_tiny_live_v2"
)
MODEL_VERSION = "current_yes_core_carry_model_v3_no_peak_clock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    parser.add_argument("--start-target-date", default="2026-07-27")
    parser.add_argument("--end-target-date", default="2026-07-29")
    return parser.parse_args()


def load_completed_scores(path: Path, start: str, end: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("model_version") != MODEL_VERSION:
                continue
            target_date = str(row.get("target_date") or "")
            if not start <= target_date <= end:
                continue
            if row.get("model_probability_hold") is None:
                continue
            rows.append(row)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[str(row["target_date"])].append(row)

    def aggregate(group: list[dict[str, Any]]) -> dict[str, Any]:
        below = 0
        in_band = 0
        above = 0
        positive_mid_residual = 0
        positive_taker_edge = 0
        positive_mid_but_negative_taker = 0
        positive_mid_but_insufficient_depth = 0
        eligible = 0
        for row in group:
            market_mid = float(row["market_mid"])
            model_probability = float(row["model_probability_hold"])
            edge = row.get("model_edge_after_fee_and_depth")
            if market_mid < 0.80:
                below += 1
            elif market_mid > 0.9895:
                above += 1
            else:
                in_band += 1
                if model_probability > market_mid:
                    positive_mid_residual += 1
                    if edge is None:
                        positive_mid_but_insufficient_depth += 1
                    elif float(edge) <= 0:
                        positive_mid_but_negative_taker += 1
                if edge is not None and float(edge) > 0:
                    positive_taker_edge += 1
            eligible += int(bool(row.get("eligible")))
        return {
            "completed_checkpoints": len(group),
            "distinct_cities": len({str(row["city"]) for row in group}),
            "below_market_mid_floor": below,
            "within_frozen_market_mid_domain": in_band,
            "above_market_mid_ceiling": above,
            "positive_model_minus_mid": positive_mid_residual,
            "positive_model_minus_taker_cost": positive_taker_edge,
            "positive_mid_but_negative_taker_cost": positive_mid_but_negative_taker,
            "positive_mid_but_insufficient_depth": positive_mid_but_insufficient_depth,
            "eligible_signals": eligible,
            "signal_city_days": len(
                {
                    (str(row["city"]), str(row["target_date"]))
                    for row in group
                    if row.get("eligible")
                }
            ),
        }

    total = aggregate(rows)
    completed = total["completed_checkpoints"]
    in_band = total["within_frozen_market_mid_domain"]
    positive_mid = total["positive_model_minus_mid"]
    total["within_domain_share"] = in_band / completed if completed else None
    total["positive_mid_residual_share_within_domain"] = (
        positive_mid / in_band if in_band else None
    )
    total["positive_taker_edge_share_within_domain"] = (
        total["positive_model_minus_taker_cost"] / in_band if in_band else None
    )
    total["eligible_share_of_completed"] = (
        total["eligible_signals"] / completed if completed else None
    )
    return {
        "model_version": MODEL_VERSION,
        "total": total,
        "by_target_date": {
            target_date: aggregate(group)
            for target_date, group in sorted(by_date.items())
        },
    }


def main() -> int:
    args = parse_args()
    path = args.runtime_dir / "pre_live_scores.jsonl"
    rows = load_completed_scores(
        path,
        args.start_target_date,
        args.end_target_date,
    )
    payload = {
        "data_source": str(path),
        "target_date_start": args.start_target_date,
        "target_date_end": args.end_target_date,
        **summarize(rows),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
