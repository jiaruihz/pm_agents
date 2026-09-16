#!/usr/bin/env python3
"""Re-evaluate Core Carry selected ROI as a paired policy increment.

The input is an immutable temporal-holdout prediction ledger.  This audit does
not refit or choose a model and never changes production configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_model_evaluation import (  # noqa: E402
    binary_loss_values,
    binary_score,
    date_block_bootstrap_delta,
    paired_policy_increment,
)


SCHEMA_VERSION = "weather_core_carry_selected_increment_audit_v2"
DEFAULT_INPUT = Path(
    "/Volumes/jrs-archive/pm_agents/research/artifact_store/active/"
    "current_yes_core_carry_price_conditioned_challenger_v1/"
    "historical_temporal_holdout_7d_20260809/temporal_holdout_predictions.csv"
)
BOOTSTRAP_DRAWS = 20_000
BOOTSTRAP_SEED = 20260830


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _proper_score_comparison(
    frame: pd.DataFrame, challenger: str, baseline: str
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for metric in ("brier", "logloss"):
        challenger_loss = binary_loss_values(
            frame["label"], frame[challenger], metric=metric
        )
        baseline_loss = binary_loss_values(
            frame["label"], frame[baseline], metric=metric
        )
        output[metric] = date_block_bootstrap_delta(
            frame,
            challenger_loss,
            baseline_loss,
            draws=BOOTSTRAP_DRAWS,
            seed=BOOTSTRAP_SEED + (0 if metric == "brier" else 1),
        )
    return output


def _exact_bounded(frame: pd.DataFrame) -> pd.Series:
    bracket = frame["current_bracket"].astype(str).str.lower()
    return ~bracket.str.contains(r"\+|below|under|higher|above", regex=True)


def _pattern_rows(paired: pd.DataFrame) -> dict[str, Any]:
    changed = paired[paired["selection_relation"].ne("same")].copy()
    challenger_only = changed[
        changed["selection_relation"].eq("challenger_only")
    ].copy()
    switched = changed[changed["selection_relation"].eq("switched")].copy()
    city = (
        changed.groupby("city", sort=True)[
            ["delta_pnl_per_share", "delta_cost_per_share"]
        ]
        .agg(["count", "sum"])
        .reset_index()
    )
    city.columns = [
        "city",
        "changed_groups",
        "delta_pnl_per_share",
        "cost_count",
        "delta_cost_per_share",
    ]
    city = city.drop(columns=["cost_count"])
    return {
        "changed_policy_groups": int(len(changed)),
        "challenger_only_groups": int(len(challenger_only)),
        "switched_groups": int(len(switched)),
        "challenger_only_wins": int(
            pd.to_numeric(
                challenger_only["label_challenger"], errors="coerce"
            ).sum()
        ),
        "challenger_only_losses": int(
            len(challenger_only)
            - pd.to_numeric(
                challenger_only["label_challenger"], errors="coerce"
            ).sum()
        ),
        "challenger_only_median_cost": (
            None
            if challenger_only.empty
            else float(challenger_only["cost_per_share_challenger"].median())
        ),
        "challenger_only_min_cost": (
            None
            if challenger_only.empty
            else float(challenger_only["cost_per_share_challenger"].min())
        ),
        "challenger_only_max_cost": (
            None
            if challenger_only.empty
            else float(challenger_only["cost_per_share_challenger"].max())
        ),
        "by_city": city.sort_values(
            "delta_pnl_per_share", ascending=False
        ).to_dict("records"),
    }


def evaluate(path: Path) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    frame = pd.read_csv(path, low_memory=False)
    required = {
        "opportunity_id",
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "current_bracket",
        "label",
        "p_core_hold",
        "p_market_hold",
        "p_challenger",
        "ten_share_executable",
        "ten_share_cost_per_share",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"input missing columns: {missing}")
    for probability in ("p_core_hold", "p_market_hold", "p_challenger"):
        values = pd.to_numeric(frame[probability], errors="raise")
        if not values.between(0, 1).all():
            raise ValueError(f"{probability} outside [0,1]")
    frame["decision_snapshot_dt"] = pd.to_datetime(
        frame["decision_snapshot_ts_utc"], utc=True, errors="raise"
    )
    fixed = frame[
        frame["ten_share_executable"].fillna(False).astype(bool)
        & _exact_bounded(frame)
        & pd.to_numeric(
            frame["ten_share_cost_per_share"], errors="coerce"
        ).notna()
    ].copy()
    vs_core, paired_core = paired_policy_increment(
        fixed,
        challenger_probability_column="p_challenger",
        baseline_probability_column="p_core_hold",
        cost_column="ten_share_cost_per_share",
        time_column="decision_snapshot_dt",
        bootstrap_draws=BOOTSTRAP_DRAWS,
        bootstrap_seed=BOOTSTRAP_SEED + 10,
    )
    vs_market, paired_market = paired_policy_increment(
        fixed,
        challenger_probability_column="p_challenger",
        baseline_probability_column="p_market_hold",
        cost_column="ten_share_cost_per_share",
        time_column="decision_snapshot_dt",
        bootstrap_draws=BOOTSTRAP_DRAWS,
        bootstrap_seed=BOOTSTRAP_SEED + 20,
    )
    challenger_vs_core = _proper_score_comparison(
        frame, "p_challenger", "p_core_hold"
    )
    challenger_vs_market = _proper_score_comparison(
        frame, "p_challenger", "p_market_hold"
    )
    core_worse = all(
        challenger_vs_core[metric]["delta"] >= 0
        for metric in ("brier", "logloss")
    )
    selection_increment_positive = vs_core["paired_increment"]["pnl_per_share"] > 0
    diagnosis = (
        "selection_expansion_without_probability_increment"
        if core_worse and selection_increment_positive
        else "probability_and_selection_increment_aligned"
        if not core_worse and selection_increment_positive
        else "no_positive_selection_increment"
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": {
            "path": str(path),
            "sha256": _sha256(path),
            "rows": int(len(frame)),
            "target_dates": int(frame["target_date"].astype(str).nunique()),
        },
        "same_row_probability": {
            "challenger": binary_score(
                frame, frame["p_challenger"], label_column="label"
            ),
            "core": binary_score(frame, frame["p_core_hold"], label_column="label"),
            "market": binary_score(
                frame, frame["p_market_hold"], label_column="label"
            ),
            "challenger_minus_core": challenger_vs_core,
            "challenger_minus_market": challenger_vs_market,
        },
        "selected_policy": {
            "challenger_vs_core": vs_core,
            "challenger_vs_market": vs_market,
            "primary": "challenger_vs_core",
            "selected_roi_role": "descriptive_policy_ratio_not_model_increment",
        },
        "core_pattern_audit": _pattern_rows(paired_core),
        "diagnosis": diagnosis,
        "conclusion": (
            "The challenger widened high-cost favorite selection and added paired PnL in "
            "this viewed seven-date window, while same-row probability quality did not "
            "improve versus Core. Keep Core; test the selection expansion only on new "
            "frozen dates."
        ),
        "evidence_class": "retrospective_temporal_holdout_not_clean_forward",
        "production_action": "none",
    }
    return payload, paired_core, paired_market


def _render(payload: dict[str, Any]) -> str:
    probability = payload["same_row_probability"]
    selected = payload["selected_policy"]["challenger_vs_core"]
    increment = selected["paired_increment"]
    pattern = payload["core_pattern_audit"]
    return "\n".join(
        [
            "# Core Carry selected increment audit v2",
            "",
            f"Status: `{payload['diagnosis']}`",
            "",
            "## 结论",
            "",
            "selected ROI 与模型增量已拆开：ROI 只描述各自投入资金；主结果改为固定可执行机会集上的 paired increment，未交易记 0。",
            "",
            f"- 同行 challenger−Core Brier Δ `{probability['challenger_minus_core']['brier']['delta']:+.6f}`；logloss Δ `{probability['challenger_minus_core']['logloss']['delta']:+.6f}`。",
            f"- 固定分母 {selected['fixed_universe']['opportunities']} checkpoints / {selected['fixed_universe']['policy_groups']} city-days / {selected['fixed_universe']['target_dates']} dates。",
            f"- challenger−Core paired PnL `{increment['pnl_per_share']:+.6f}`/share；date-equal mean CI `[{increment['date_equal_mean_pnl_ci95'][0]:+.6f},{increment['date_equal_mean_pnl_ci95'][1]:+.6f}]`。",
            f"- challenger-only {pattern['challenger_only_groups']} 组，{pattern['challenger_only_wins']}胜{pattern['challenger_only_losses']}负；成本中位数 `{pattern['challenger_only_median_cost']:.4f}`。",
            "- 该窗口只有 7 个已查看 target dates；这是“扩大高价 favorite 选择”的历史现象，不是新概率 alpha，也不支持改 live。",
            "",
            "## 解释边界",
            "",
            "same-row market 仍是 probability baseline。market mid 在 executable ask 之上没有正 edge 时，market selector 选择 0 笔是正确的 no-trade 基线，不应伪造一个 market ROI。",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    payload, paired_core, paired_market = evaluate(args.predictions)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "report.md").write_text(_render(payload), encoding="utf-8")
    paired_core.to_csv(args.output_dir / "paired_challenger_vs_core.csv", index=False)
    paired_market.to_csv(
        args.output_dir / "paired_challenger_vs_market.csv", index=False
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
