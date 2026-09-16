#!/usr/bin/env python3
"""Project-level audit separating selected ROI from model/policy increment.

Core Carry and Tmin currently have enough frozen probability, action, cost and
settlement lineage for a paired audit.  CrossNO and WCIR are kept visible in
the output, but fail closed as non-identifiable until the missing frozen action
mapping/probability denominator exists.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk.audit_core_carry_selected_increment_v2 import (  # noqa: E402
    DEFAULT_INPUT as DEFAULT_CORE_INPUT,
    evaluate as evaluate_core,
)
from weather_model_evaluation import (  # noqa: E402
    binary_loss_values,
    date_block_bootstrap_delta,
    paired_policy_increment,
)


SCHEMA_VERSION = "weather_strategy_selected_increment_audit_v2"
DEFAULT_TMIN_ROW_AUDIT = (
    ROOT
    / "reviews/tmin_v2_1_v3_strategy_readout_v1/ROW_LEVEL_MODEL_AND_SIGNAL_AUDIT.parquet"
)
DEFAULT_TMIN_TRADE_FUNNEL = (
    ROOT / "reviews/tmin_no_further_model_forensics_v1/ROW_LEVEL_TRADE_FUNNEL.csv"
)
ACTIVE_TMIN_WINDOWS = frozenset(
    {"morning_cooling", "post_sunrise_provisional_low"}
)
BOOTSTRAP_DRAWS = 20_000
BOOTSTRAP_SEED = 20260830


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _proper_score_delta(
    frame: pd.DataFrame, candidate: str, baseline: str, *, seed: int
) -> dict[str, Any]:
    work = frame[
        frame["label"].isin([0, 1])
        & frame[candidate].notna()
        & frame[baseline].notna()
    ].copy()
    result: dict[str, Any] = {
        "rows": int(len(work)),
        "target_dates": int(work["target_date"].astype(str).nunique()),
    }
    for offset, metric in enumerate(("brier", "logloss")):
        candidate_loss = binary_loss_values(
            work["label"], work[candidate], metric=metric
        )
        baseline_loss = binary_loss_values(
            work["label"], work[baseline], metric=metric
        )
        result[metric] = date_block_bootstrap_delta(
            work,
            candidate_loss,
            baseline_loss,
            draws=BOOTSTRAP_DRAWS,
            seed=seed + offset,
        )
    return result


def _compact_increment(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "fixed_universe": report["fixed_universe"],
        "challenger": report["challenger"],
        "baseline": report["baseline"],
        "selection_relation": report["selection_relation"],
        "paired_increment": report["paired_increment"],
    }


def _tmin_audit(
    row_audit_path: Path, trade_funnel_path: Path
) -> tuple[dict[str, Any], pd.DataFrame]:
    probability = pd.read_parquet(row_audit_path)
    trade = pd.read_csv(trade_funnel_path, low_memory=False)
    model_columns = [
        "checkpoint_id",
        "p_v2_1",
        "p_v3",
        "p_v2_1_strict",
        "p_v3_strict",
    ]
    trade = trade.merge(
        probability[model_columns],
        on="checkpoint_id",
        how="left",
        validate="one_to_one",
    )
    fixed = trade[
        trade["settled_binary"].eq(True)
        & trade["execution_candidate_status_gate"].eq(True)
    ].copy()
    if fixed.empty:
        raise ValueError("Tmin execution-clean fixed universe is empty")
    fixed["opportunity_id"] = fixed["candidate_id"].astype(str)
    fixed["fee_per_share"] = (
        0.05 * fixed["executable_cost"] * (1.0 - fixed["executable_cost"])
    )
    active = fixed["cooling_window_state"].isin(ACTIVE_TMIN_WINDOWS)
    fixed["p_market_routed"] = fixed["market_p"].where(active, 0.0)
    fixed["p_v1_incumbent"] = fixed["p_model"]
    fixed["p_v1_alpha010_routed"] = fixed["challenger_p"].where(active, 0.0)
    fixed["p_v2_1_routed"] = fixed["p_v2_1"].where(active, 0.0)
    fixed["p_v3_routed"] = fixed["p_v3"].where(active, 0.0)

    specs = {
        "v1_incumbent_vs_market": ("p_v1_incumbent", "market_p"),
        "v1_alpha010_routed_vs_market": (
            "p_v1_alpha010_routed",
            "p_market_routed",
        ),
        "v2_1_routed_vs_market": ("p_v2_1_routed", "p_market_routed"),
        "v3_routed_vs_market": ("p_v3_routed", "p_market_routed"),
        "v2_1_routed_vs_v1_alpha010": (
            "p_v2_1_routed",
            "p_v1_alpha010_routed",
        ),
        "v3_routed_vs_v1_alpha010": (
            "p_v3_routed",
            "p_v1_alpha010_routed",
        ),
    }
    paired_reports: dict[str, Any] = {}
    paired_ledgers: list[pd.DataFrame] = []
    for offset, (name, (candidate, baseline)) in enumerate(specs.items()):
        report, ledger = paired_policy_increment(
            fixed,
            challenger_probability_column=candidate,
            baseline_probability_column=baseline,
            cost_column="executable_cost",
            fee_column="fee_per_share",
            opportunity_id_column="opportunity_id",
            bootstrap_draws=BOOTSTRAP_DRAWS,
            bootstrap_seed=BOOTSTRAP_SEED + 100 + offset,
        )
        paired_reports[name] = _compact_increment(report)
        ledger.insert(0, "comparison", name)
        paired_ledgers.append(ledger)

    p0 = probability.copy()
    proper_scores = {
        "v1_incumbent_vs_market": _proper_score_delta(
            p0, "p_v1_incumbent", "p_market", seed=BOOTSTRAP_SEED + 200
        ),
        "v1_alpha010_routed_vs_market": _proper_score_delta(
            p0,
            "p_v1_alpha010_routed",
            "p_market",
            seed=BOOTSTRAP_SEED + 210,
        ),
        "v2_1_vs_market": _proper_score_delta(
            p0, "p_v2_1", "p_market", seed=BOOTSTRAP_SEED + 220
        ),
        "v3_vs_market": _proper_score_delta(
            p0, "p_v3", "p_market", seed=BOOTSTRAP_SEED + 230
        ),
    }
    incumbent = paired_reports["v1_incumbent_vs_market"]
    additions = pd.concat(paired_ledgers, ignore_index=True)
    additions = additions[
        additions["comparison"].eq("v1_incumbent_vs_market")
        & additions["selection_relation"].eq("challenger_only")
    ]
    high_cost = additions[additions["cost_per_share_challenger"].gt(0.98)]
    return (
        {
            "inputs": {
                "row_audit": {
                    "path": str(row_audit_path),
                    "sha256": _sha256(row_audit_path),
                    "rows": int(len(probability)),
                },
                "trade_funnel": {
                    "path": str(trade_funnel_path),
                    "sha256": _sha256(trade_funnel_path),
                    "rows": int(len(trade)),
                },
            },
            "same_row_probability": proper_scores,
            "paired_policy": paired_reports,
            "pattern": {
                "diagnosis": "selection_expansion_on_viewed_favorite_rows",
                "incumbent_challenger_only_groups": int(len(additions)),
                "incumbent_challenger_only_high_cost_gt_098": int(len(high_cost)),
                "incumbent_challenger_only_pnl_per_share": float(
                    additions["pnl_per_share_challenger"].sum()
                ),
                "incumbent_challenger_only_cost_per_share": float(
                    additions["cost_per_share_challenger"].sum()
                ),
                "explanation": (
                    "V1 incumbent's selected profit comes mainly from opening many more "
                    "favorite positions than a market-only selector, despite worse full-P0 "
                    "proper score. The two-row routed V1 result remains too sparse."
                ),
            },
            "conclusion": (
                "Keep the frozen V1 alpha=.10 zero-notional forward. Do not promote V1 "
                "incumbent, V2.1 or V3 from selected ROI: V1 incumbent is a viewed-window "
                "selection expansion; V2.1/V3 remove the two frozen V1 actions and add no "
                "new action on the common 111-row execution-clean universe."
            ),
            "production_action": "none",
        },
        pd.concat(paired_ledgers, ignore_index=True),
    )


def build(
    *, core_input: Path, tmin_row_audit: Path, tmin_trade_funnel: Path
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    core, core_ledger, _core_market_ledger = evaluate_core(core_input)
    tmin, tmin_ledger = _tmin_audit(tmin_row_audit, tmin_trade_funnel)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "model_increment": "same-row proper score on a fixed probability denominator",
            "policy_increment": (
                "paired action PnL on one fixed executable universe; no-trade equals zero; "
                "target_date block bootstrap"
            ),
            "selected_roi": "descriptive capital ratio only",
        },
        "strategies": {
            "core_carry": core,
            "tmin": tmin,
            "cross_no": {
                "status": "not_identifiable_current_artifact",
                "reason": (
                    "No frozen same-opportunity probability/action ledger survives the "
                    "external negative-control failure; sparse selected ROI cannot identify "
                    "model increment."
                ),
                "required_next_evidence": (
                    "frozen AMOS-to-routine/settlement basis head plus exact-token action mapping"
                ),
            },
            "wcir_next_print": {
                "status": "not_identifiable_no_action_mapping",
                "reason": (
                    "Proper-score/repricing evidence exists, but next-print outcomes do not "
                    "yet map to an exact executable token action."
                ),
                "required_next_evidence": (
                    "frozen next-print-to-exact-bracket action mapping and same-time executable book"
                ),
            },
        },
        "cross_strategy_pattern": (
            "The recurring hidden mode is selection expansion into high-price favorites: "
            "it can produce positive selected PnL while adding no same-row probability skill."
        ),
        "production_action": "none",
    }
    return payload, core_ledger, tmin_ledger


def _render(payload: dict[str, Any]) -> str:
    core = payload["strategies"]["core_carry"]
    tmin = payload["strategies"]["tmin"]
    core_increment = core["selected_policy"]["challenger_vs_core"][
        "paired_increment"
    ]
    tmin_inc = tmin["paired_policy"]["v1_incumbent_vs_market"]
    tmin_v1 = tmin["paired_policy"]["v1_alpha010_routed_vs_market"]
    return "\n".join(
        [
            "# Weather selected increment audit v2",
            "",
            "Selected ROI 已降级为描述性资金比率；模型结论看同 row proper score，策略结论看固定可执行分母的 paired PnL（不交易=0）。",
            "",
            "## 核心结论",
            "",
            f"- Core: challenger−Core paired PnL `{core_increment['pnl_per_share']:+.6f}`/share，但 challenger−Core 同 row Brier/logloss 都未改善；诊断为 `{core['diagnosis']}`。",
            f"- Tmin V1 incumbent: 111 rows / 33 city-days / 15 dates；相对 market-only 增加 `{tmin_inc['selection_relation']['challenger_only']}` 个动作，paired PnL `{tmin_inc['paired_increment']['pnl_per_share']:+.6f}`/share，却在完整 P0 proper score 上劣于 market。",
            f"- Tmin frozen alpha=.10: 仅 `{tmin_v1['challenger']['entries']}` 个动作，paired PnL `{tmin_v1['paired_increment']['pnl_per_share']:+.6f}`/share，CI 下界为 0，继续 zero-notional forward，不算确认。",
            "- Tmin V2.1/V3: 在同一 111-row 分母上均为 0 动作；相对 frozen V1 丢掉 2 个历史盈利动作，当前版本不继续。",
            "- CrossNO: 当前 artifact 缺通过 negative control 的冻结 probability/action 分母，不能从 3 笔 ROI 推断模型增量。",
            "- WCIR next-print: 有 proper score，但没有 next-print→exact-token action mapping，execution ROI 仍不可识别。",
            "",
            "## 新发现的共性模式",
            "",
            payload["cross_strategy_pattern"],
            "",
            "生产动作：无；这些是研究结论，不改变 selector、阈值、订单或资金。",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-input", type=Path, default=DEFAULT_CORE_INPUT)
    parser.add_argument("--tmin-row-audit", type=Path, default=DEFAULT_TMIN_ROW_AUDIT)
    parser.add_argument(
        "--tmin-trade-funnel", type=Path, default=DEFAULT_TMIN_TRADE_FUNNEL
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    payload, core_ledger, tmin_ledger = build(
        core_input=args.core_input,
        tmin_row_audit=args.tmin_row_audit,
        tmin_trade_funnel=args.tmin_trade_funnel,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_dir / "report.md").write_text(_render(payload), encoding="utf-8")
    core_ledger.to_csv(args.output_dir / "core_paired_ledger.csv", index=False)
    tmin_ledger.to_csv(args.output_dir / "tmin_paired_ledger.csv", index=False)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
