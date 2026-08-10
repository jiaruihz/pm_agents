#!/usr/bin/env python3
"""Audit the canonical Full-Ladder First-Seen Residual forward denominator.

This is a research/zero-notional scorecard.  It does not create plans, orders,
fills, exits, or any live-policy output.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_model_evaluation import pooled_ladder_transport  # noqa: E402
from weather_model_evaluation import source_event_ws_linkage  # noqa: E402


DEFAULT_CANDIDATES = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/"
    "first_seen_zero_notional/candidates.jsonl"
)
DEFAULT_SETTLEMENT_DB = ROOT / "runtime" / "weather.db"
DEFAULT_OUT_DIR = (
    ROOT
    / "docs"
    / "analysis"
    / "2026-07"
    / "generated"
    / "full_ladder_first_seen_residual_v1"
)
DEFAULT_REPORT = (
    ROOT
    / "docs"
    / "analysis"
    / "2026-07"
    / "2026-07-29-full-ladder-first-seen-residual-v1.md"
)
FEE_RATE = 0.05
PROPER_SCORE_COLUMNS = [
    "state_checkpoint_id",
    "event_date",
    "city",
    "model",
    "multiclass_logloss",
    "multiclass_brier",
    "ranked_probability_score",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_candidates(path: Path) -> pd.DataFrame:
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                continue
            if value.get("candidate_grain_version") != "v2_event_checkpoint":
                continue
            if value.get("strategy_key") != "first_seen_exact_bracket_residual_v1":
                continue
            candidate_id = str(value.get("candidate_id") or "")
            if not candidate_id:
                raise ValueError(f"missing candidate_id at {path}:{line_number}")
            rows[candidate_id] = value
    if not rows:
        raise RuntimeError(f"no v2 first-seen candidates found in {path}")
    frame = pd.DataFrame(rows.values())
    required = {
        "candidate_id",
        "state_checkpoint_id",
        "trigger_event_id",
        "event_date",
        "city",
        "condition_id",
        "bracket",
        "side",
        "candidate_status",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"candidate telemetry missing required fields: {missing}")
    return frame


def load_settlements(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["condition_id", "final_yes", "settlement_status"])
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    try:
        rows = conn.execute(
            """
            SELECT
              condition_id,
              CASE
                WHEN final_price >= 0.99 THEN 1.0
                WHEN final_price <= 0.01 THEN 0.0
                ELSE NULL
              END AS final_yes,
              settlement_status
            FROM settlement_outcomes
            WHERE condition_id IS NOT NULL
            """
        ).fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["condition_id", "final_yes", "settlement_status"]).drop_duplicates(
        "condition_id", keep="last"
    )


def attach_settlements(frame: pd.DataFrame, settlements: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.drop(columns=["final_yes", "settlement_status"], errors="ignore", inplace=True)
    if settlements.empty:
        result["final_yes"] = np.nan
        result["settlement_status"] = None
        return result
    return result.merge(settlements, on="condition_id", how="left")


def weather_fee(price: float) -> float:
    return round(FEE_RATE * price * (1.0 - price), 5)


def _bracket_order(value: Any) -> tuple[float, str]:
    text = str(value or "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    number = float(match.group()) if match else math.inf
    return number, text


def checkpoint_audit(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for checkpoint_id, group in frame.groupby("state_checkpoint_id", sort=False):
        yes = group[group["side"].eq("BUY_YES")].copy()
        conditions = set(group["condition_id"].dropna().astype(str))
        paired_conditions = 0
        for condition_id in conditions:
            sides = set(group.loc[group["condition_id"].astype(str).eq(condition_id), "side"])
            paired_conditions += int(sides == {"BUY_YES", "BUY_NO"})

        model_after = pd.to_numeric(yes.get("model_probability_after"), errors="coerce")
        market_after = pd.to_numeric(yes.get("market_probability"), errors="coerce")
        model_before = pd.to_numeric(yes.get("model_probability_before"), errors="coerce")
        market_before = pd.to_numeric(yes.get("market_probability_before"), errors="coerce")
        executable = pd.to_numeric(group.get("decision_entry_price"), errors="coerce")
        model_paired = model_before.notna() & model_after.notna()
        market_paired = market_before.notna() & market_after.notna()
        model_changed = bool(
            model_paired.any()
            and ((model_after[model_paired] - model_before[model_paired]).abs() > 1e-12).any()
        )
        market_changed = bool(
            market_paired.any()
            and ((market_after[market_paired] - market_before[market_paired]).abs() > 1e-12).any()
        )
        settled_yes = yes[pd.to_numeric(yes.get("final_yes"), errors="coerce").eq(1.0)]
        rows.append(
            {
                "state_checkpoint_id": checkpoint_id,
                "trigger_event_id": group["trigger_event_id"].iloc[0],
                "event_date": group["event_date"].iloc[0],
                "city": group["city"].iloc[0],
                "candidate_rows": len(group),
                "rungs": len(conditions),
                "all_conditions_have_yes_no": paired_conditions == len(conditions),
                "full_model_distribution": bool(len(yes) and model_after.notna().all()),
                "model_probability_sum": (
                    float(model_after.sum()) if len(yes) and model_after.notna().all() else np.nan
                ),
                "model_simplex_within_2c": bool(
                    len(yes)
                    and model_after.notna().all()
                    and abs(float(model_after.sum()) - 1.0) <= 0.02
                ),
                "full_market_distribution": bool(len(yes) and market_after.notna().all()),
                "market_probability_sum": (
                    float(market_after.sum()) if len(yes) and market_after.notna().all() else np.nan
                ),
                "market_simplex_within_2c": bool(
                    len(yes)
                    and market_after.notna().all()
                    and abs(float(market_after.sum()) - 1.0) <= 0.02
                ),
                "any_scored_expression": bool(group["candidate_status"].eq("scored").any()),
                "full_two_sided_executable_ladder": bool(len(group) and executable.notna().all()),
                "model_before_after_available": bool(model_paired.all() and len(model_paired)),
                "model_changed_after_event": model_changed,
                "market_before_after_available": bool(market_paired.all() and len(market_paired)),
                "market_changed_after_event": market_changed,
                "settled": bool(len(settled_yes) == 1),
                "winner_condition_id": (
                    str(settled_yes["condition_id"].iloc[0]) if len(settled_yes) == 1 else None
                ),
            }
        )
    return pd.DataFrame(rows)


def executable_rows(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame[frame["candidate_status"].eq("scored")].copy()
    result["model_probability_after"] = pd.to_numeric(
        result.get("model_probability_after"), errors="coerce"
    )
    result["decision_entry_price"] = pd.to_numeric(
        result.get("decision_entry_price"), errors="coerce"
    )
    result = result[
        result["model_probability_after"].notna()
        & result["decision_entry_price"].between(0.001, 0.999, inclusive="both")
    ].copy()
    result["fee_per_share"] = result["decision_entry_price"].map(weather_fee)
    result["effective_cost"] = result["decision_entry_price"] + result["fee_per_share"]
    result["diagnostic_net_edge"] = result["model_probability_after"] - result["effective_cost"]
    result["diagnostic_positive_edge"] = result["diagnostic_net_edge"].gt(0)
    result["diagnostic_edge_gt_1c"] = result["diagnostic_net_edge"].gt(0.01)
    return result


def proper_scores(frame: pd.DataFrame, checkpoints: pd.DataFrame) -> pd.DataFrame:
    settled_ids = set(checkpoints.loc[checkpoints["settled"], "state_checkpoint_id"].astype(str))
    rows: list[dict[str, Any]] = []
    for checkpoint_id, group in frame[
        frame["state_checkpoint_id"].astype(str).isin(settled_ids) & frame["side"].eq("BUY_YES")
    ].groupby("state_checkpoint_id"):
        group = group.copy()
        group["_bracket_order"] = group["bracket"].map(_bracket_order)
        group.sort_values("_bracket_order", inplace=True)
        winner = group[pd.to_numeric(group["final_yes"], errors="coerce").eq(1.0)]
        if len(winner) != 1:
            continue
        for model_name, probability_column in (
            ("market", "market_probability"),
            ("snapshot_model_placeholder", "model_probability_after"),
        ):
            probabilities = pd.to_numeric(group.get(probability_column), errors="coerce")
            if probabilities.isna().any() or float(probabilities.sum()) <= 0:
                continue
            probabilities = probabilities.clip(lower=1e-9)
            probabilities = probabilities / probabilities.sum()
            labels = group["condition_id"].eq(winner["condition_id"].iloc[0]).astype(float)
            winner_probability = float(probabilities[labels.eq(1.0)].iloc[0])
            rps = float(
                ((probabilities.cumsum().iloc[:-1] - labels.cumsum().iloc[:-1]) ** 2).sum()
            )
            rows.append(
                {
                    "state_checkpoint_id": checkpoint_id,
                    "event_date": group["event_date"].iloc[0],
                    "city": group["city"].iloc[0],
                    "model": model_name,
                    "multiclass_logloss": -math.log(winner_probability),
                    "multiclass_brier": float(((probabilities - labels) ** 2).sum()),
                    "ranked_probability_score": rps,
                }
            )
    return pd.DataFrame(rows, columns=PROPER_SCORE_COLUMNS)


def _count_blockers(frame: pd.DataFrame) -> Counter[str]:
    counter: Counter[str] = Counter()
    for value in frame.get("candidate_blocker", pd.Series(dtype=object)).dropna():
        counter.update(part for part in str(value).split("|") if part)
    return counter


def summarize(
    frame: pd.DataFrame,
    checkpoints: pd.DataFrame,
    executable: pd.DataFrame,
    scores: pd.DataFrame,
    *,
    candidate_path: Path,
    settlement_db: Path,
) -> dict[str, Any]:
    blockers = _count_blockers(frame)
    scored = frame["candidate_status"].eq("scored")
    zero_notional_ok = bool(
        frame.get("zero_notional", pd.Series(False, index=frame.index)).fillna(False).astype(bool).all()
        and frame.get("no_order_placed", pd.Series(False, index=frame.index))
        .fillna(False)
        .astype(bool)
        .all()
    )
    model_update_rate = float(checkpoints["model_changed_after_event"].mean())
    settled_dates = int(checkpoints.loc[checkpoints["settled"], "event_date"].nunique())
    ready_for_probability_test = bool(
        settled_dates >= 15
        and int(checkpoints["full_model_distribution"].sum()) >= 300
        and int(checkpoints["model_changed_after_event"].sum()) > 0
    )
    ready_for_execution_test = bool(
        ready_for_probability_test
        and int(checkpoints["full_two_sided_executable_ladder"].sum()) >= 30
    )
    verdict = (
        "research_forward_ready"
        if ready_for_probability_test
        else "inconclusive_measurement_head_not_ready"
    )
    return {
        "generated_at_utc": _utc_now(),
        "status": verdict,
        "inputs": {
            "candidate_jsonl": str(candidate_path),
            "candidate_mtime_utc": datetime.fromtimestamp(
                candidate_path.stat().st_mtime, timezone.utc
            ).isoformat(),
            "settlement_db": str(settlement_db),
            "settlement_db_mtime_utc": (
                datetime.fromtimestamp(settlement_db.stat().st_mtime, timezone.utc).isoformat()
                if settlement_db.exists()
                else None
            ),
        },
        "signal_funnel": {
            "candidate_rows": int(len(frame)),
            "information_events": int(frame["trigger_event_id"].nunique()),
            "state_checkpoints": int(frame["state_checkpoint_id"].nunique()),
            "cities": int(frame["city"].nunique()),
            "target_dates": int(frame["event_date"].nunique()),
            "mapped_expressions": int(len(frame)),
            "scored_expressions": int(scored.sum()),
            "positive_diagnostic_edge_expressions": int(
                executable["diagnostic_positive_edge"].sum()
            ),
            "edge_gt_1c_diagnostic_expressions": int(
                executable["diagnostic_edge_gt_1c"].sum()
            ),
            "policy_selected": int(pd.to_numeric(frame.get("policy_selected"), errors="coerce").fillna(0).sum()),
        },
        "evidence_funnel": {
            "lineage_class_exported": int("pit_lineage_class" in frame.columns),
            "feature_frame_available_checkpoints": int(
                (~frame.groupby("state_checkpoint_id")["candidate_blocker"].apply(
                    lambda values: any("missing_feature_frame" in str(value) for value in values.dropna())
                )).sum()
            ),
            "full_model_distribution_checkpoints": int(
                checkpoints["full_model_distribution"].sum()
            ),
            "model_simplex_checkpoints": int(checkpoints["model_simplex_within_2c"].sum()),
            "full_market_distribution_checkpoints": int(
                checkpoints["full_market_distribution"].sum()
            ),
            "market_simplex_checkpoints": int(checkpoints["market_simplex_within_2c"].sum()),
            "any_scored_expression_checkpoints": int(
                checkpoints["any_scored_expression"].sum()
            ),
            "full_two_sided_executable_ladder_checkpoints": int(
                checkpoints["full_two_sided_executable_ladder"].sum()
            ),
            "settled_checkpoints": int(checkpoints["settled"].sum()),
            "settled_target_dates": settled_dates,
            "proper_score_rows": int(len(scores)),
            "actual_fills": 0,
        },
        "measurement_audit": {
            "model_before_after_available_checkpoints": int(
                checkpoints["model_before_after_available"].sum()
            ),
            "model_changed_after_event_checkpoints": int(
                checkpoints["model_changed_after_event"].sum()
            ),
            "model_update_rate": model_update_rate,
            "market_before_after_available_checkpoints": int(
                checkpoints["market_before_after_available"].sum()
            ),
            "market_changed_after_event_checkpoints": int(
                checkpoints["market_changed_after_event"].sum()
            ),
            "candidate_model_is_trained_first_seen_update_head": False,
            "candidate_model_role": (
                "paper_snapshot probability telemetry baseline; it does not consume "
                "trigger-event feature_frame_ref as a fitted residual update"
            ),
        },
        "candidate_blockers": dict(blockers.most_common()),
        "gates": {
            "probability_test_ready": ready_for_probability_test,
            "execution_test_ready": ready_for_execution_test,
            "significance": "NA",
            "baseline": "NA",
            "forward": "FAIL",
            "conclusion": "inconclusive",
        },
        "safety": {
            "zero_notional_rows_only": zero_notional_ok,
            "plan_order_fill_exit_changes": 0,
            "live_behavior_changes": 0,
        },
    }


def render_report(summary: dict[str, Any]) -> str:
    signal = summary["signal_funnel"]
    evidence = summary["evidence_funnel"]
    measurement = summary["measurement_audit"]
    blockers = summary["candidate_blockers"]
    gates = summary["gates"]
    return f"""# Full-Ladder First-Seen Residual v1 — Phase 0 / 首轮 forward 审计

Status: `{summary["status"]}`

Scope: `research + zero-notional`; no plan/order/fill/exit/live change

## 结论

当前动作是**继续 collector，但不启动 expression policy**。first-seen canonical
分母已经产生，但概率更新头和可执行证据都没有达到可评价状态：

1. `{signal["information_events"]:,}` 个 information events / `{signal["state_checkpoints"]:,}`
   个 checkpoint 展开为 `{signal["candidate_rows"]:,}` 个 v2 expressions；
2. 只有 `{evidence["any_scored_expression_checkpoints"]:,}` 个 checkpoint 至少有一个
   scored expression，完整双边可执行 ladder 为
   `{evidence["full_two_sided_executable_ladder_checkpoints"]:,}`；
3. 已结算 checkpoint/date 为 `{evidence["settled_checkpoints"]:,}` /
   `{evidence["settled_target_dates"]:,}`，因此不能计算同 rows market proper score、
   fee ROI 或任何 live 结论；
4. 当前 `model_probability_before/after` 只是 paper-snapshot model telemetry。
   `{measurement["model_changed_after_event_checkpoints"]:,}` /
   `{signal["state_checkpoints"]:,}` checkpoints 出现数值变化，但 builder 没有让一个
   fitted first-seen residual head 消费 trigger event 的 `feature_frame_ref`；所以这些变化
   不能归因给 source event，`diagnostic_net_edge` 也不能当策略 edge。

这轮没有把 coverage gap 包装成筛选器，也没有从未结算的正 residual 推导收益。

## 数据快照

- candidates：`{summary["inputs"]["candidate_jsonl"]}`
- candidate mtime UTC：`{summary["inputs"]["candidate_mtime_utc"]}`
- settlement DB：`{summary["inputs"]["settlement_db"]}`
- settlement DB mtime UTC：`{summary["inputs"]["settlement_db_mtime_utc"]}`
- rows：`{signal["candidate_rows"]:,}`；unsettled checkpoint：
  `{signal["state_checkpoints"] - evidence["settled_checkpoints"]:,}` /
  `{signal["state_checkpoints"]:,}`；missing bracket 无法在未结算期判定

## Frozen research target

在每个 genuinely new information event 首次可用后，估计完整
`P(final exact bracket | PIT event + path + forecast + market)`，并在相同 checkpoint
上检验 M2/M3 相对 normalized market ladder 的 multiclass logloss、Brier 和 RPS。
先赢 probability baseline，再评 full-depth taker expression。

## Signal funnel

- v2 candidate rows：`{signal["candidate_rows"]:,}`
- trigger events / checkpoints：`{signal["information_events"]:,}` /
  `{signal["state_checkpoints"]:,}`
- cities / target dates：`{signal["cities"]}` / `{signal["target_dates"]}`
- scored expressions：`{signal["scored_expressions"]:,}`
- diagnostic edge > 0 / > 1c：`{signal["positive_diagnostic_edge_expressions"]:,}` /
  `{signal["edge_gt_1c_diagnostic_expressions"]:,}`（仅 telemetry，不是策略选择）
- policy_selected：`{signal["policy_selected"]}`

## Evidence funnel

- PIT lineage class 随 candidate export：`{evidence["lineage_class_exported"]}`；
  当前必须回连 canonical event table 才能区分 `collector_exact` 与 archive lineage
- feature-frame available checkpoints：`{evidence["feature_frame_available_checkpoints"]:,}`
- full model distribution / simplex±2c：`{evidence["full_model_distribution_checkpoints"]:,}` /
  `{evidence["model_simplex_checkpoints"]:,}`
- full market distribution / simplex±2c：`{evidence["full_market_distribution_checkpoints"]:,}` /
  `{evidence["market_simplex_checkpoints"]:,}`
- any scored checkpoint：`{evidence["any_scored_expression_checkpoints"]:,}`
- full two-sided executable ladder：`{evidence["full_two_sided_executable_ladder_checkpoints"]:,}`
- settled checkpoints / dates：`{evidence["settled_checkpoints"]:,}` /
  `{evidence["settled_target_dates"]:,}`
- actual fills：`0`

主要 blocker：`{json.dumps(blockers, ensure_ascii=False, sort_keys=True)}`。

## 研究判定

- 当前输出是 M0/M1 measurement baseline，不是已经训练完成的 M2 source innovation head。
- 下一份 frozen artifact 只允许：
  `normalized market prior + source innovation + compact path/remaining heat`；
  train/OOF 按 target_date 整块滚动，不能同日泄漏。
- 每个 checkpoint 保存全部 bracket×side，执行层每 city-date 最多选择一个 expression；
  price/depth 缺失进入 evidence funnel，不是 eligibility filter。
- Atlanta 2026-07-17 必须固定进入 source-basis negative-control 报告；它不能被 QC pass
  或 persistent cross 洗掉。

## Gate

`significance={gates["significance"]} baseline={gates["baseline"]}
forward={gates["forward"]} conclusion={gates["conclusion"]}`。

冻结 forward 的最低复核条件仍是 ≥15 settled target dates、≥300 个全分母
scoreable observations、≥30 个 full-depth policy opportunities；在此之前只继续
zero-notional collector，不改变任何 live runner。

## 8 环

- covered：canonical v2 opportunity denominator、signal/evidence funnel、full-distribution
  consistency、event前后 measurement audit、top-book/fee diagnostic。
- missing：settlement/proper score、full-depth execution、capacity、真实 fill/queue、
  frozen forward significance、组合相关性。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=(
            "first-seen-residual",
            "helsinki-ws-linkage",
            "pooled-ladder-transport-readiness",
        ),
        default="first-seen-residual",
    )
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--settlement-db", type=Path, default=DEFAULT_SETTLEMENT_DB)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ws_defaults = source_event_ws_linkage.default_input_paths()
    parser.add_argument("--ws-target-date")
    parser.add_argument("--ws-root", type=Path, default=ws_defaults["ws_root"])
    parser.add_argument(
        "--ws-source-event-path",
        type=Path,
        default=ws_defaults["source_event_path"],
    )
    parser.add_argument("--ws-fmi-path", type=Path, default=ws_defaults["fmi_path"])
    parser.add_argument("--ws-output-dir", type=Path)
    pooled_ladder_transport.add_cli_arguments(parser)
    args = parser.parse_args()

    if args.mode == "pooled-ladder-transport-readiness":
        result = pooled_ladder_transport.run(
            pooled_ladder_transport.namespace_from_cli(
                args,
                entrypoint_path=Path(__file__),
            )
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    if args.mode == "helsinki-ws-linkage":
        if not args.ws_target_date or args.ws_output_dir is None:
            parser.error(
                "helsinki-ws-linkage requires --ws-target-date and --ws-output-dir"
            )
        result = source_event_ws_linkage.run(
            argparse.Namespace(
                target_date=args.ws_target_date,
                ws_root=args.ws_root,
                source_event_path=args.ws_source_event_path,
                fmi_path=args.ws_fmi_path,
                output_dir=args.ws_output_dir,
                entrypoint_path=Path(__file__),
            )
        )
        print(json.dumps(result, sort_keys=True))
        return 0

    frame = attach_settlements(load_candidates(args.candidates), load_settlements(args.settlement_db))
    checkpoints = checkpoint_audit(frame)
    executable = executable_rows(frame)
    scores = proper_scores(frame, checkpoints)
    summary = summarize(
        frame,
        checkpoints,
        executable,
        scores,
        candidate_path=args.candidates,
        settlement_db=args.settlement_db,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    checkpoints.to_csv(args.out_dir / "checkpoint_audit.csv", index=False)
    executable.to_csv(args.out_dir / "executable_expression_diagnostics.csv", index=False)
    scores.to_csv(args.out_dir / "proper_scores.csv", index=False)
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
