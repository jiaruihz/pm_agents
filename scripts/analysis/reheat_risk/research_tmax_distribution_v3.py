#!/usr/bin/env python3
"""Canonical entry point for the unified Tmax distribution v3 replay.

The implementation reuses the canonical v2 materializer, the restoration
enrichment cache, and the focused ``tmax_distribution_v3`` replay modules.
It never reads or writes live runner configuration.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import research_tmax_distribution_v3_model_v1 as replay  # noqa: E402
from scripts.analysis.reheat_risk.tmax_distribution_v3 import common, report as report_mod  # noqa: E402
from src.strategies.weather_edge_v1.tools.tmax_distribution_v3 import BUCKETS, probability_artifact_row  # noqa: E402

REPORT_MD = ROOT / "docs/analysis/2026-07/2026-07-13-tmax-distribution-v3.md"
REPORT_JSON = ROOT / "docs/analysis/2026-07/2026-07-13-tmax-distribution-v3.json"


def _write_primary_probability_artifact() -> dict[str, object]:
    payload = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    primary = str(payload["primary_route"])
    probs = pd.read_csv(common.OUT_DIR / f"route_probs_{primary}.csv", low_memory=False)
    states = pd.read_csv(common.OUT_DIR / "state_rows_v3.csv", low_memory=False)
    keys = ["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"]
    for frame in (states, probs):
        frame["target_date"] = frame["target_date"].astype(str)
        frame["decision_hour_local"] = pd.to_numeric(frame["decision_hour_local"], errors="raise").astype(int)
        frame["decision_snapshot_ts_utc"] = pd.to_datetime(frame["decision_snapshot_ts_utc"], utc=True)
    merged = states.merge(probs, on=keys, how="inner", suffixes=("", "_pred"), validate="one_to_one")
    if merged.empty:
        raise AssertionError("primary probability artifact merge produced zero rows")
    merged = merged.sort_values(["city", "target_date", "decision_snapshot_ts_utc"])
    merged["anchor_shift_from_prev"] = 0
    for _, indices in merged.groupby(["city", "target_date"], sort=False).groups.items():
        previous = None
        for index in indices:
            current = str(merged.at[index, "current_bracket"])
            merged.at[index, "anchor_shift_from_prev"] = common.bracket_step_shift(previous, current) if previous is not None else 0
            previous = current
    rows = []
    for record in merged.to_dict("records"):
        labels, ladder_probs = report_mod.full_ladder_probs(record)
        rows.append(probability_artifact_row(
            city=str(record["city"]), target_date=str(record["target_date"]),
            decision_ts_utc=str(record["decision_snapshot_ts_utc"]), route=primary,
            lineage="strict_pit", anchor_bracket=str(record["current_bracket"]),
            ladder_rungs=labels, full_ladder_probs=ladder_probs,
            five_bucket={bucket: float(record[f"route_p_{bucket}"]) for bucket in BUCKETS},
            fusion_alpha=float(record["fusion_alpha"]),
            coherence_weight=float(record["coherence_weight"]),
            anchor_shift_from_prev=int(record["anchor_shift_from_prev"]), train_through_date=str(record["train_through_date"]),
            market_available=bool(record["market_score_ready"]),
            sequential_hazards={
                "h0": float(record["hazard_reach_d1"]),
                "h1": float(record["hazard_reach_d2"]),
                "h2": float(record["hazard_reach_tail"]),
                "h_cont": float(record["hazard_tail_continue"]),
            },
        ))
    artifact = pd.DataFrame(rows)
    artifact_path = common.OUT_DIR / "primary_probability_artifact_rows.csv"
    artifact.to_csv(artifact_path, index=False)
    sums = artifact[[f"p_{bucket}" for bucket in BUCKETS]].sum(axis=1)
    full_sums = artifact["p_full_ladder_json"].map(lambda value: sum(json.loads(value)))
    if not sums.between(1 - 1e-6, 1 + 1e-6).all():
        raise AssertionError("primary five-bucket artifact does not sum to one")
    if not full_sums.between(1 - 1e-6, 1 + 1e-6).all():
        raise AssertionError("primary full-ladder artifact does not sum to one")
    payload["primary_probability_artifact"] = {
        "path": str(artifact_path.relative_to(ROOT)), "rows": len(artifact),
        "five_bucket_sum_max_abs_error": float((sums - 1.0).abs().max()),
        "full_ladder_sum_max_abs_error": float((full_sums - 1.0).abs().max()),
        "includes_sequential_hazards": True,
    }
    payload["gfs_ecmwf_variant"] = {
        "status": "not_answerable",
        "reason": "strict_asof_lag_le_60m_dual_source_coverage_is_zero_on_2026_06_21_plus",
        "performance_rows": 0,
    }
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with REPORT_MD.open("a", encoding="utf-8") as handle:
        handle.write("\n## Probability Artifact Schema\n\n")
        handle.write(f"Primary `{primary}` 输出 `{len(artifact)}` 行 full-ladder + five-bucket + sequential hazards；")
        handle.write("每行 two probability spaces 均归一化，artifact 为 `generated/tmax_distribution_v3/primary_probability_artifact_rows.csv`。\n")
        handle.write("\nGFS+ECMWF variant：`not_answerable`；strict as-of lag<=60m 双源覆盖在 2026-06-21+ 为 0，因此没有训练或性能结果。\n")
    return payload["primary_probability_artifact"]


def main() -> int:
    common.REPORT_MD = REPORT_MD
    common.REPORT_JSON = REPORT_JSON
    report_mod.REPORT_MD = REPORT_MD
    report_mod.REPORT_JSON = REPORT_JSON
    exit_code = replay.main()
    if exit_code != 0:
        return exit_code
    artifact = _write_primary_probability_artifact()
    print(json.dumps({"report": str(REPORT_MD), "json": str(REPORT_JSON), "probability_artifact": artifact}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
