"""Verdict, Lucknow fixed case, model card and report writer for v3."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from src.strategies.weather_edge_v1.tools.tmax_distribution_v3 import (
    BUCKETS,
    MODEL_VERSION,
    build_model_card,
    probability_artifact_row,
    ROUTE_SPECS,
)
from .execution import _eligible_expressions

from .common import FROZEN_POLICY, OUT_DIR, REPORT_JSON, REPORT_MD, ROOT, json_ready, markdown_table

KEY = ["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"]

# Reference numbers from prior audited reports (different, partly mixed-lineage
# denominators; cited for orientation, not recomputed here).
PRIOR_BASELINES = {
    "clean_fusion_v2": {"paired_rows": 2777, "logloss": 0.6043, "market_logloss": 0.4984, "first_lock_roi": "+3.38% (275 trades, CI [-4.23%, +9.39%])"},
    "legacy_full_feature": {"roi": "+8.08% (159 trades, mixed lineage, not snapshot-reconstructible)"},
    "coherent_cal_quote": {"roi": "+11.19% (167 trades, mixed lineage, paired delta vs full-feature +3.11pp CI [+0.63, +5.73])"},
}


def final_selection(primary_probs: pd.DataFrame) -> tuple[float, float]:
    if primary_probs.empty:
        return 0.5, 0.25
    last_date = primary_probs["target_date"].max()
    row = primary_probs[primary_probs["target_date"] == last_date].iloc[-1]
    return float(row["fusion_alpha"]), float(row["coherence_weight"])


def final_calibrator_c(primary_probs: pd.DataFrame) -> float | None:
    if primary_probs.empty or "calibrator_c" not in primary_probs:
        return None
    last_date = primary_probs["target_date"].max()
    value = primary_probs.loc[primary_probs["target_date"] == last_date, "calibrator_c"].iloc[-1]
    return float(value) if pd.notna(value) else None


def full_ladder_probs(record: dict[str, Any]) -> tuple[list[str], list[float]]:
    book = json.loads(record["ladder_book_json"])
    labels = [str(r["bracket"]) for r in book]
    anchor = int(record["anchor_rung_index"])
    bucket = {b: float(record[f"route_p_{b}"]) for b in BUCKETS}
    if anchor == 0:
        # No visible lower sibling exists, but the five-bucket basis head can
        # still carry settlement-basis mismatch mass. Preserve it explicitly.
        labels = ["__below_anchor__", *labels]
        anchor = 1
        book = [{"bracket": "__below_anchor__"}, *book]
    probs = np.zeros(len(book))
    if anchor > 0:
        probs[:anchor] = bucket["below"] / anchor
    probs[anchor] = bucket["current"]
    probs[anchor + 1] = bucket["d1"]
    probs[anchor + 2] = bucket["d2"]
    tail_rungs = len(book) - anchor - 3
    if tail_rungs > 0:
        h = float(np.clip(record.get("h_cont", 0.3), 1e-6, 1 - 1e-6))
        masses = [bucket["tail"] * (1 - h) * h**k for k in range(tail_rungs - 1)]
        masses.append(max(0.0, bucket["tail"] - sum(masses)))
        probs[anchor + 3 :] = masses
    elif bucket["tail"] > 0:
        probs[-1] += bucket["tail"]
    total = probs.sum()
    if total > 0:
        probs = probs / total
    return labels, [float(p) for p in probs]


def lucknow_case(
    exec_primary: pd.DataFrame, ledger: pd.DataFrame, first_lock: pd.DataFrame, primary_route: str
) -> dict[str, Any]:
    day = exec_primary[(exec_primary["city"] == "Lucknow") & (exec_primary["target_date"] == "2026-07-05")]
    day = day.sort_values("decision_snapshot_ts_utc")
    decisions = []
    old_target = "FLAT"
    for record in day.to_dict("records"):
        labels, probs = full_ladder_probs(record)
        candidates = _eligible_expressions(record)
        best = candidates[0] if candidates else None
        new_target = f"{best['side']}:{best['bracket']}" if best is not None else old_target
        artifact_row = probability_artifact_row(
                city="Lucknow", target_date="2026-07-05",
                decision_ts_utc=str(record["decision_snapshot_ts_utc"]),
                route=primary_route, lineage=ROUTE_SPECS[primary_route]["lineage"],
                anchor_bracket=str(record["current_bracket"]), ladder_rungs=labels,
                full_ladder_probs=probs,
                five_bucket={b: float(record[f"route_p_{b}"]) for b in BUCKETS},
                fusion_alpha=float(record["fusion_alpha"]),
                coherence_weight=float(record["coherence_weight"]),
                anchor_shift_from_prev=0, train_through_date=str(record["train_through_date"]),
                market_available=bool(record["market_score_ready"]),
                sequential_hazards={
                    "h0": float(record.get("hazard_reach_d1", math.nan)),
                    "h1": float(record.get("hazard_reach_d2", math.nan)),
                    "h2": float(record.get("hazard_reach_tail", math.nan)),
                    "h_cont": float(record.get("hazard_tail_continue", record.get("h_cont", math.nan))),
                },
            )
        artifact_row.update({
            "old_target_position": old_target,
            "new_target_position": new_target,
            "candidate_expression": best["expression"] if best is not None else "",
            "estimated_entry_cost_5sh": 5.0 * (best["ask"] + best["fee"]) if best is not None else 0.0,
            "decision_reason": "fixed_policy_eligible" if best is not None else "no_fixed_policy_expression_eligible_hold_flat",
        })
        decisions.append(artifact_row)
        old_target = new_target
    case_ledger = ledger[(ledger["city"] == "Lucknow") & (ledger["target_date"] == "2026-07-05")] if not ledger.empty else pd.DataFrame()
    case_first = first_lock[
        (first_lock["route"] == primary_route)
        & (first_lock["city"] == "Lucknow")
        & (first_lock["target_date"] == "2026-07-05")
    ]
    pd.DataFrame(decisions).to_csv(OUT_DIR / "lucknow_20260705_decisions.csv", index=False)
    case_ledger.to_csv(OUT_DIR / "lucknow_20260705_ledger.csv", index=False)
    # Dedupe / self-cross assertions: at most one open position at any time and
    # every close references a previously opened position id.
    open_ids, active = [], 0
    if not case_ledger.empty:
        for record in case_ledger.sort_values("ts").to_dict("records"):
            if record["action"] in {"open", "reopen"}:
                active += 1
                open_ids.append(record["position_id"])
                assert active == 1, "more than one simultaneous position in Lucknow case"
            elif record["action"].startswith("close"):
                assert record["position_id"] in open_ids, "close without open lineage"
                active -= 1
    return {
        "decisions": len(decisions),
        "ledger_actions": int(len(case_ledger)),
        "first_lock_trades": int(len(case_first)),
        "flat_hold_decisions": int(sum(row["decision_reason"].endswith("hold_flat") for row in decisions)),
        "self_cross_possible": False,
        "note": "single tracked position per city-day; every close references its open position_id",
    }


def target_book_stats(ledger: pd.DataFrame, positions: pd.DataFrame) -> dict[str, Any]:
    if ledger.empty:
        return {"status": "no_ledger_rows"}
    cash = float(ledger["cash"].sum())
    outflow = float(-ledger.loc[ledger["cash"] < 0, "cash"].sum())
    actions = ledger["action"].value_counts().to_dict()
    daily = ledger.groupby("target_date")["cash"].sum().sort_index()
    cum = daily.cumsum()
    return {
        "net_pnl": cash,
        "gross_cost": outflow,
        "roi": cash / outflow if outflow else math.nan,
        "actions": {str(k): int(v) for k, v in actions.items()},
        "positions": int(len(positions)),
        "locked_positions": int((positions["exit"] == "locked_complement").sum()) if not positions.empty else 0,
        "max_drawdown_usd": float((cum - cum.cummax()).min()),
        "dates": int(daily.shape[0]),
    }


def _three_gates(summary: pd.DataFrame, deltas: pd.DataFrame, primary_route: str) -> dict[str, Any]:
    primary = summary[summary["route"] == primary_route]
    market = summary[summary["route"] == "market"]
    baseline = "FAIL"
    if not primary.empty and not market.empty:
        if float(primary.iloc[0]["logloss_date_equal"]) < float(market.iloc[0]["logloss_date_equal"]):
            baseline = "PASS"
    sig_row = deltas[(deltas["challenger"] == primary_route) & (deltas["baseline"] == "market")]
    significance = "FAIL"
    if not sig_row.empty and bool(sig_row.iloc[0]["direction_stable"]):
        significance = "PASS"
    gates = {
        "baseline": baseline,
        "significance": significance,
        "forward": "FAIL_fresh_forward_0",
        "conclusion": "shadow_candidate_no_live",
    }
    if baseline == "PASS" and significance == "PASS":
        gates["single_gap"] = "fresh_forward_evidence_only_from_shadow_runner_accumulation"
    else:
        gates["single_gap"] = "primary does not beat market on paired proper score; shadow forward evidence still required"
    return gates


def write_report(**kw: Any) -> dict[str, Any]:
    summary, deltas, primary_route = kw["summary"], kw["deltas"], kw["primary_route"]
    gates = _three_gates(summary, deltas, primary_route)
    model_card = build_model_card(
        primary_route=primary_route, verdict=gates,
        denominators={k: kw["counts"][k] for k in ("labeled_exec_states", "hourly_states", "paired_rows", "paired_dates", "freeze_date")},
        frozen_policy={k: (list(v) if isinstance(v, tuple) else v) for k, v in FROZEN_POLICY.items()},
        known_gaps=[
            "fresh-forward evidence = 0 dates (all in-window results retrospective_diagnostic)",
            "forecast hourly curves only cover 2026-07-04+ (remaining-heat features flagged missing before)",
            "weather_mechanism layer is archive-reconstructed upper bound, never live artifact",
            "dual GFS+ECMWF same-time coverage too thin to answer forward source increments",
            "temporal coherence was not selected (frozen weight=0); shift=0 no-break elapsed-time survival decay is not implemented",
        ],
    )
    (OUT_DIR / "model_card.json").write_text(json.dumps(json_ready(model_card), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_version": MODEL_VERSION,
        "primary_route": primary_route,
        "three_gates": gates,
        "counts": kw["counts"],
        "remeasured_flagged_numbers": kw["remeasured"],
        "paired_score_summary": summary.to_dict("records"),
        "ablation_deltas": deltas.to_dict("records"),
        "trailing_window_summary": kw["trailing_summary"].to_dict("records"),
        "execution_summary": kw["exec_summary"].to_dict("records"),
        "execution_side": kw["exec_side"].to_dict("records"),
        "execution_expression": kw["exec_expr"].to_dict("records"),
        "execution_unit": kw["exec_unit"].to_dict("records"),
        "execution_source": kw["exec_source"].to_dict("records"),
        "execution_family": kw["exec_family"].to_dict("records"),
        "stability": kw["stability"],
        "target_book": kw["target_book"],
        "full_ladder_secondary_audit": kw["audit"],
        "lucknow_case": kw["lucknow"],
        "frozen_artifact": kw["artifact"],
        "shadow_event_rows": kw["shadow_rows"],
        "prior_baselines_cited": PRIOR_BASELINES,
        "frozen_policy": {k: (list(v) if isinstance(v, tuple) else v) for k, v in FROZEN_POLICY.items()},
        "scope": "offline research + zero-notional shadow artifacts only; no live change, no order",
    }
    REPORT_JSON.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    REPORT_MD.write_text("\n".join(_markdown(payload, kw)), encoding="utf-8")
    return payload


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "NA"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _markdown(payload: dict[str, Any], kw: dict[str, Any]) -> list[str]:
    summary: pd.DataFrame = kw["summary"]
    market_row = summary[summary["route"] == "market"].iloc[0] if not summary.empty else None
    primary_row = summary[summary["route"] == payload["primary_route"]]
    primary_row = primary_row.iloc[0] if not primary_row.empty else None
    gates = payload["three_gates"]
    lines = [
        "# Tmax Distribution v3 Model v1",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}` · model `{MODEL_VERSION}` · primary route `{payload['primary_route']}`",
        "> Scope: offline replay + zero-notional shadow artifacts. No live restore, no live config change, no real order.",
        "",
        "## 开头直接回答",
        "",
        f"- **新模型是什么**：full-ladder 竞争风险 hazard chain + 两个 constrained market recalibrator。global 仅使用 log(market five-bucket probs)，path 只增加预注册连续 residual，无 city；C/alpha 均只按过去训练窗 date-equal logloss 选择。",
        f"- **比当前 clean fusion**：clean fusion paired logloss `0.6043` vs market `0.4984`（2,777 rows）。本轮 primary `{payload['primary_route']}` 在 `{payload['counts']['paired_rows']}` paired rows 上 date-equal logloss `{_fmt(primary_row['logloss_date_equal']) if primary_row is not None else 'NA'}` vs market `{_fmt(market_row['logloss_date_equal']) if market_row is not None else 'NA'}`（row-mean `{_fmt(primary_row['logloss_row_mean']) if primary_row is not None else 'NA'}` vs `{_fmt(market_row['logloss_row_mean']) if market_row is not None else 'NA'}`）。",
        "- **比旧 full-feature (+8.08%) / coherent_cal_quote (+11.19%)**：旧数字是混合血缘分母（非 saved-snapshot 可重建），不可同分母复算；本轮同 clean 分母的 first-lock ROI 见下执行表，直接对比只在 clean 分母内做。",
        "- **改善来自哪层**：见 Ablation 表（date-bootstrap CI）；CI 跨零的层只保留 calibration/机制价值，不宣称 alpha 证实。",
        "- **哪些只是假设**：weather_mechanism（湿度/云/风）为 archive 重建 upper bound，不入 live artifact；forecast curve 剩余加热特征仅 7/04+ 有覆盖；双源增量不可答（见重测数字）。",
        f"- **能否 shadow/live**：`{gates['conclusion']}`。baseline gate `{gates['baseline']}`，significance gate `{gates['significance']}`，forward gate `{gates['forward']}`。唯一缺口：{gates['single_gap']}。**不恢复 live。**",
        "- **Temporal coherence**：candidate anchor-shift conditioning 存在，但本轮 frozen coherence weight=0，未被 proper score 选择；shift=0 的 elapsed-time no-break survival decay 尚未实现。因此不能宣称已经解决跨小时一致性，这是 residual blocker。",
        "",
        "## 重测的两个数字（任务书标注需重测）",
        "",
        f"- 双 GFS+ECMWF as-of 同刻覆盖率（本分母 states）：`{_fmt(payload['remeasured_flagged_numbers']['dual_source_asof_state_share']*100, 2)}%`；非零日期：`{json.dumps(payload['remeasured_flagged_numbers']['dual_source_dates_nonzero'])}`。",
        f"- 双源 strict-asof lag<=60m 行数：`{json.dumps(payload['remeasured_flagged_numbers'].get('dual_source_asof_rows_by_date', {}))}`；6/21+ 为 0，结论 `not_answerable`，不产性能。",
        f"- archive meteo strict-asof lag<=60m coverage：`{payload['remeasured_flagged_numbers'].get('archive_meteo_asof_60m_rows', 0)}` / `{payload['counts']['labeled_exec_states']}`；lineage=`report_time_reconstruction_only_upper_bound`。",
        f"- atlas 直接 `city/date/hour` 连接的未来泄漏：`{payload['remeasured_flagged_numbers']['direct_join_future_leakage_rows']}` / `{payload['remeasured_flagged_numbers']['direct_join_rows']}` 行泄漏（占比 `{_fmt(payload['remeasured_flagged_numbers']['direct_join_future_leakage_share']*100, 1)}%`），正泄漏中位 `{_fmt(payload['remeasured_flagged_numbers']['direct_join_median_positive_leakage_minutes'], 1)}` 分钟（p90 `{_fmt(payload['remeasured_flagged_numbers']['direct_join_p90_positive_leakage_minutes'], 1)}`）。因此本轮一律 as-of 连接。",
        "",
        "## Paired Proper Score（primary denominator）",
        "",
        *markdown_table(summary),
        "",
        "所有 in-window 结果均为 `retrospective_diagnostic`；fresh-forward dates = 0。",
        "",
        "## Ablation increments（date-equal logloss delta，负为更好）",
        "",
        *markdown_table(kw["deltas"]),
        "",
        "## Trailing-window robustness（primary，train=最近10天）",
        "",
        *markdown_table(kw["trailing_summary"]),
        "",
        "## Execution first-lock（frozen policy，与旧口径可比）",
        "",
        *markdown_table(kw["exec_summary"]),
        "",
        "### Side / Expression / Unit",
        "",
        *markdown_table(kw["exec_side"]),
        "",
        *markdown_table(kw["exec_expr"]),
        "",
        *markdown_table(kw["exec_unit"]),
        "",
        "### Primary route source/family 切片",
        "",
        *markdown_table(kw["exec_source"]),
        "",
        *markdown_table(kw["exec_family"]),
        "",
        f"### Stability：`{json.dumps(json_ready(kw['stability']))}`",
        "",
        "## Position-aware target-book（primary）",
        "",
        f"`{json.dumps(json_ready(kw['target_book']))}`",
        "",
        "## Saved visible-ladder quote secondary audit（不等同完整 absolute event ladder）",
        "",
        f"`{json.dumps(json_ready(kw['audit']))}`",
        "",
        "## Lucknow 2026-07-05 固定案例",
        "",
        f"`{json.dumps(json_ready(kw['lucknow']))}`",
        "",
        "逐决策全 ladder 概率：`generated/tmax_distribution_v3/lucknow_20260705_decisions.csv`；ledger：`lucknow_20260705_ledger.csv`。ledger 结构杜绝无持仓 YES/NO 自撞，但该模型该日未触发交易，未验证原交易反事实。",
        "",
        "## Frozen artifact / shadow",
        "",
        f"`{json.dumps(json_ready(kw['artifact']))}`",
        "",
        f"shadow source rows: `{kw['shadow_rows']}` → `docs/analysis/2026-07/generated/tmax_distribution_v3/shadow_events.csv`。复用统一 runner：`.venv/bin/python scripts/ops/tmax_distribution_edge_shadow_v1.py run --source docs/analysis/2026-07/generated/tmax_distribution_v3/shadow_events.csv --runtime-dir runtime/weather_edge_v1/tmax_distribution_v3_shadow`。",
        "",
        "## Three Gates",
        "",
        f"baseline=`{gates['baseline']}`；significance=`{gates['significance']}`；forward=`{gates['forward']}`；conclusion=`{gates['conclusion']}`。",
        "",
    ]
    return lines
