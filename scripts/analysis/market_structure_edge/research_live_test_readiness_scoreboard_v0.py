#!/usr/bin/env python3
"""Build a live-test readiness scoreboard from submitted weather research.

This is a synthesis report, not a new parameter sweep. It reads committed JSON
reports that were generated from runtime/weather.db fact tables, adds the
mandatory fact-table self check, and classifies candidates by live-test
readiness.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-live-test-readiness-scoreboard-v0.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-live-test-readiness-scoreboard-v0.md"

REPORTS = {
    "all_yes_underround": ROOT
    / "docs"
    / "analysis"
    / "2026-06"
    / "2026-06-09-range-rv-underround-robust-v1-0.json",
    "side_band_forecast_regime": ROOT
    / "docs"
    / "analysis"
    / "2026-06"
    / "2026-06-10-side-band-forecast-regime-v0.json",
    "adjacent3_shadow": ROOT
    / "docs"
    / "analysis"
    / "2026-06"
    / "2026-06-10-adjacent3-quality-shadow-journal-v0.json",
    "hybrid_single": ROOT
    / "docs"
    / "analysis"
    / "2026-06"
    / "2026-06-10-hybrid-adjacent3-single-v0.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    return parser.parse_args()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def safe_div(num: float, den: float) -> float | None:
    return num / den if den else None


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def table(headers: list[str], rows_in: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows_in:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def data_self_check(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    return {
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat()
        if db_path.exists()
        else None,
        "max_fact_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class_distribution": rows(
            conn, "SELECT trade_class, COUNT(*) AS n FROM fact_trades GROUP BY trade_class ORDER BY trade_class"
        ),
        "settlement_status_distribution": rows(
            conn,
            "SELECT settlement_status, COUNT(*) AS n FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "candidate_coverage": rows(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        )[0],
        "order_fill_coverage": rows(
            conn,
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
        ),
        "candidate_event_range": rows(
            conn,
            "SELECT MIN(event_date) AS min_event_date, MAX(event_date) AS max_event_date, "
            "COUNT(DISTINCT event_date) AS event_dates, COUNT(*) AS rows FROM fact_signal_candidates",
        )[0],
        "candidate_usable_main_denominator": rows(
            conn,
            "SELECT COUNT(*) AS rows, COUNT(DISTINCT event_date) AS event_dates, "
            "COUNT(DISTINCT city || '|' || event_date) AS city_days "
            "FROM fact_signal_candidates "
            "WHERE eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0",
        )[0],
    }


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_summary(report: dict[str, Any], rule_id: str, split: str, source: str) -> dict[str, Any]:
    for row in report.get("summary", []):
        if row.get("rule_id") == rule_id and row.get("split") == split:
            return row.get(source, {})
        if row.get("shadow_rule_id") == rule_id and row.get("split") == split:
            return row.get(source, {})
    return {}


def build_candidates(reports: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    under = reports["all_yes_underround"]
    side = reports["side_band_forecast_regime"]
    adj = reports["adjacent3_shadow"]
    hybrid = reports["hybrid_single"]

    side_rule = (side.get("selected_rules") or [{}])[0]
    side_holdout = side_rule.get("holdout", {})
    side_selected = side_holdout.get("selected", {})
    side_baseline = side_holdout.get("baseline", {})

    adj_holdout = find_summary(adj, "adjacent3_medium_quality", "holdout", "orderbook_taker")
    adj_decision_holdout = find_summary(adj, "adjacent3_medium_quality", "holdout", "decision_proxy")
    single_holdout = find_summary(hybrid, "single_high_conviction_yes", "holdout", "orderbook_taker")
    single_decision_holdout = find_summary(hybrid, "single_high_conviction_yes", "holdout", "decision_proxy")
    overlap = {
        row.get("rule_id"): row for row in hybrid.get("overlap_summary", [])
    }

    return [
        {
            "candidate_id": "all_yes_underround",
            "human_name": "all-YES underround",
            "plain_idea": "所有 YES 总价低于 1 时买完整互斥篮子，押市场 no-arb 定价错误。",
            "evidence": (
                f"verdict={under.get('verdict')}; "
                f"confirmed_orderbook_algorithms={len(under.get('confirmed_orderbook_algorithms') or [])}; "
                f"fully_matched_strategy_rows={under.get('orderbook_coverage', {}).get('fully_matched_strategy_rows')}"
            ),
            "holdout_or_forward": "proxy/executable 多阈值过三门",
            "significance": under.get("gates", {}).get("significance", "NA"),
            "baseline": under.get("gates", {}).get("baseline", "NA"),
            "forward": under.get("gates", {}).get("forward", "NA"),
            "readiness": "confirmed_but_deferred",
            "live_test_decision": "不选当前 live",
            "reason": "统计三门通过，但用户已明确 all-YES 暂不实盘；多腿速度、partial fill、滑点和手续费执行风险仍是主问题。",
            "next_evidence_needed": "只做工程 shadow：完整篮子下单仿真、partial fill unwind、fee/slippage 容量测试。",
        },
        {
            "candidate_id": "forecast_quality_adjacent3",
            "human_name": "forecast-quality adjacent3",
            "plain_idea": "当模型分布集中在 mode 附近三档时，买相邻 3 档 YES，用 forecast quality 做软门。",
            "evidence": (
                f"decision holdout rows={adj_decision_holdout.get('usable_rows')}, "
                f"decision ROI={pct(adj_decision_holdout.get('roi'))}; "
                f"orderbook rows={adj_holdout.get('usable_rows')}, "
                f"orderbook ROI={pct(adj_holdout.get('roi'))}"
            ),
            "holdout_or_forward": f"orderbook CI={fmt_ci(adj_holdout.get('roi_ci95'))}",
            "significance": "FAIL",
            "baseline": "NA",
            "forward": "FAIL",
            "readiness": "best_shadow_candidate",
            "live_test_decision": "不选 live，选 shadow/paper 主线",
            "reason": "点估计最好且逻辑贴近天气预测，但样本只有 8 条 holdout orderbook，CI/forward/baseline 不够。",
            "next_evidence_needed": "固定规则跑 forward shadow：>=10 event_dates、>=30 executable rows、CI 下界 >0、top5 removed >0、matched baseline >0。",
        },
        {
            "candidate_id": "single_high_conviction_yes",
            "human_name": "single high-conviction YES",
            "plain_idea": "每个 city-day 只买一个模型最强、价格较低的 YES 单腿。",
            "evidence": (
                f"decision holdout rows={single_decision_holdout.get('usable_rows')}, "
                f"decision ROI={pct(single_decision_holdout.get('roi'))}; "
                f"orderbook rows={single_holdout.get('usable_rows')}, "
                f"orderbook ROI={pct(single_holdout.get('roi'))}; "
                f"inside adjacent3 rate={pct((overlap.get('single_high_conviction_yes') or {}).get('inside_adjacent3_rate'))}"
            ),
            "holdout_or_forward": f"orderbook CI={fmt_ci(single_holdout.get('roi_ci95'))}",
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "readiness": "feature_not_strategy",
            "live_test_decision": "不选 live",
            "reason": "99%+ 都在 adjacent3 内，更多是重复加注或降级表达，不是独立互补 edge。",
            "next_evidence_needed": "只作为 adjacent3 太贵/盘口不全时的 fallback 记录，不做叠加真钱。",
        },
        {
            "candidate_id": "side_band_forecast_regime",
            "human_name": "side-band + forecast regime",
            "plain_idea": "复查早期 side-band/mid-price 赚钱是否能被 forecast regime 稳定复现。",
            "evidence": (
                f"holdout selected rows={side_selected.get('rows')}, "
                f"selected ROI={pct(side_selected.get('roi'))}; "
                f"baseline ROI={pct(side_baseline.get('roi'))}; "
                f"excess ROI={pct(side_holdout.get('excess_roi'))}"
            ),
            "holdout_or_forward": f"excess CI={fmt_ci(side_holdout.get('excess_roi_ci95_cluster_by_event_date'))}",
            "significance": side_rule.get("gates", {}).get("significance", "FAIL"),
            "baseline": side_rule.get("gates", {}).get("baseline", "FAIL"),
            "forward": side_rule.get("gates", {}).get("forward", "FAIL"),
            "readiness": "rejected_for_live",
            "live_test_decision": "不选 live",
            "reason": "真钱早期赚过是真的，但 clean holdout 反向，top5 stress 不稳。",
            "next_evidence_needed": "保留为特征输入，不再按旧 side-band 规则独立实盘。",
        },
        {
            "candidate_id": "outside_range_no_overlay",
            "human_name": "outside-range NO overlay",
            "plain_idea": "adjacent3 外侧某档被市场高估时买 NO，理论上是区间外互补。",
            "evidence": "hybrid v0 固定归一化口径下触发 0 行。",
            "holdout_or_forward": "NA",
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "readiness": "concept_only",
            "live_test_decision": "不选 live",
            "reason": "概念上互补，但当前 fact 近窗固定规则没有样本。",
            "next_evidence_needed": "等更长 opportunity fact 或 shadow 期自然触发后再评估。",
        },
    ]


def build_shadow_rule_spec(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    adj = reports["adjacent3_shadow"]
    thresholds = adj.get("quality_thresholds_train_only", {})
    params = adj.get("rule_params", {})
    return {
        "rule_id": "forecast_quality_medium_adjacent3_shadow_v0",
        "status": "frozen_shadow_not_live",
        "plain_idea": "只在模型分布非常集中、尾部很低、价格没贵到离谱时，记录 mode 附近三档 YES would-trade。",
        "selection": {
            "range_width": 3,
            "range": "model mode around adjacent 3 YES brackets",
            "decision_hours_to_settle": [22, 24],
            "market_cost_sum_max": params.get("market_cost_cap", 0.85),
            "range_edge_min": params.get("min_edge", 0.0),
            "model_adjacent3_mass_min": thresholds.get("adjacent3_mass_train_median"),
            "model_tail_mass_outside_adjacent3_max": thresholds.get("tail_mass_train_median"),
            "entropy": "record_only_not_hard_gate",
        },
        "record_fields": [
            "rule_version",
            "git_sha",
            "generated_at_utc",
            "city",
            "event_date",
            "forecast_source",
            "model_version",
            "decision_snapshot_ts_utc",
            "decision_hours_to_settle",
            "selected_brackets",
            "leg condition_id/market_id/bracket/model_p_yes/market_yes_price",
            "market_cost_sum",
            "model_adjacent3_mass",
            "model_tail_mass_outside_adjacent3",
            "entropy",
            "mode_probability",
            "range_edge",
            "per-leg orderbook_snapshot_ts_utc <= decision_snapshot_ts_utc",
            "best ask/spread/depth/full-match/taker cost",
            "settlement final bracket and event_date cluster metrics",
        ],
    }


def readiness_thresholds() -> dict[str, Any]:
    return {
        "forward_event_dates_min": 30,
        "settled_shadow_decisions_min": 100,
        "full_orderbook_matched_decisions_min": 50,
        "decision_proxy_cluster_ci95_lower_gt_zero": True,
        "orderbook_taker_cluster_ci95_lower_gt_zero": True,
        "matched_baseline_excess_ci95_lower_gt_zero": True,
        "top5_removed_orderbook_roi_gt_zero": True,
        "no_orderbook_leakage": "all orderbook_snapshot_ts_utc <= decision_snapshot_ts_utc",
    }


def choose_recommendation(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "real_live_test_candidate": None,
        "shadow_primary": "forecast_quality_adjacent3",
        "engineering_shadow": "all_yes_underround",
        "reason": (
            "没有非 all-YES 策略通过三门。all-YES 是 confirmed 但执行复杂且用户暂不实盘；"
            "forecast-quality adjacent3 是最接近天气预测核心逻辑的 shadow 主线。"
        ),
        "next_action": "freeze forecast_quality_medium_adjacent3_shadow_v0 and accumulate forward shadow evidence",
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidates = report["candidates"]
    rows_out = [
        [
            row["human_name"],
            row["readiness"],
            row["live_test_decision"],
            f"{row['significance']}/{row['baseline']}/{row['forward']}",
            row["evidence"],
            row["reason"],
        ]
        for row in candidates
    ]
    next_rows = [
        [row["human_name"], row["next_evidence_needed"]]
        for row in candidates
    ]
    shadow_rule = report["shadow_rule_spec"]
    threshold_rows = [[key, value] for key, value in report["readiness_thresholds"].items()]
    shadow_selection_rows = [[key, value] for key, value in shadow_rule["selection"].items()]
    lines = [
        "# Live-Test Readiness Scoreboard v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> git_sha: `{report['git_sha']}`",
        f"> Scope: synthesis of submitted fact-table research; no N100/live config changed; no live orders.",
        "",
        "## 数据快照",
        "",
        "- 数据源：`runtime/weather.db.fact_signal_candidates` / `fact_trades`，以及已提交研究 JSON。",
        f"- fact_signal_candidates event range：`{report['data_self_check']['candidate_event_range']}`。",
        f"- 主 opportunity usable 近似分母：`{report['data_self_check']['candidate_usable_main_denominator']}`。",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(
            {
                "max_fact_built_at_utc": report["data_self_check"]["max_fact_built_at_utc"],
                "trade_class_distribution": report["data_self_check"]["trade_class_distribution"],
                "settlement_status_distribution": report["data_self_check"]["settlement_status_distribution"],
                "candidate_coverage": report["data_self_check"]["candidate_coverage"],
                "order_fill_coverage": report["data_self_check"]["order_fill_coverage"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## 一句话结论",
        "",
        "当前仍不选真钱 live-test。最接近的是 `forecast-quality adjacent3`，但只能做 shadow/paper；`all-YES underround` 统计上 confirmed，但执行复杂且用户已明确暂不实盘。",
        "",
        "## Readiness 表",
        "",
        table(
            ["candidate", "readiness", "decision", "gates", "evidence", "reason"],
            rows_out,
        ),
        "",
        "## 下一步门槛",
        "",
        table(["candidate", "needed evidence"], next_rows),
        "",
        "## 冻结 Shadow 规则",
        "",
        f"- rule_id：`{shadow_rule['rule_id']}`",
        f"- status：`{shadow_rule['status']}`",
        f"- 人话：{shadow_rule['plain_idea']}",
        "",
        table(["field", "value"], shadow_selection_rows),
        "",
        "记录字段：",
        "",
        "\n".join(f"- `{field}`" for field in shadow_rule["record_fields"]),
        "",
        "## Live-Test Readiness 硬门",
        "",
        table(["requirement", "threshold"], threshold_rows),
        "",
        "## 推荐路径",
        "",
        f"- real live test candidate：`{report['recommendation']['real_live_test_candidate']}`。",
        f"- primary shadow：`{report['recommendation']['shadow_primary']}`。",
        f"- engineering shadow：`{report['recommendation']['engineering_shadow']}`。",
        f"- next action：`{report['recommendation']['next_action']}`。",
        f"- 原因：{report['recommendation']['reason']}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    conn = connect(str(db_path))
    reports = {name: load_report(path) for name, path in REPORTS.items()}
    candidates = build_candidates(reports)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "db_path": str(db_path),
        "input_reports": {name: str(path) for name, path in REPORTS.items()},
        "data_self_check": data_self_check(conn, db_path),
        "candidates": candidates,
        "shadow_rule_spec": build_shadow_rule_spec(reports),
        "readiness_thresholds": readiness_thresholds(),
        "recommendation": choose_recommendation(candidates),
    }
    write_json(Path(args.out_json), payload)
    write_md(Path(args.out_md), payload)
    print(args.out_json)
    print(args.out_md)


if __name__ == "__main__":
    main()
