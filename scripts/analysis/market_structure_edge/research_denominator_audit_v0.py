#!/usr/bin/env python3
"""Denominator audit for weather strategy research scripts.

The goal is not to prove strategy alpha. It checks whether research scripts may
be using a denominator that is too narrow for city-day/range work: BUY_YES-only
universes, eligible-only hard gates, pre-group settlement filters, full-universe
settlement requirements, or orderbook full-match-only conclusions.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-11-denominator-audit-v0.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-11-denominator-audit-v0.md"
TARGET_METRIC = "weather_research_denominator_integrity_audit_v0"
SCRIPT_DIRS = [
    ROOT / "scripts/analysis/market_structure_edge",
    ROOT / "scripts/analysis/forecast_quality",
    ROOT / "scripts/analysis/side_alpha",
    ROOT / "scripts/analysis/entry_timing",
    ROOT / "scripts/analysis/execution_quality",
]

PATTERNS: dict[str, tuple[str, int, str]] = {
    "buy_yes_only_universe": (r"WHERE\s+side\s*=\s*['\"]BUY_YES['\"]|side\s*=\s*['\"]BUY_YES['\"]", 4, "可能把 BUY_NO-only bracket 从 city-day universe 删除"),
    "eligible_hard_gate": (r"eligible\s*=\s*1|all_legs_eligible|require_eligible\s*[:=]\s*True|int\([^\n]*eligible", 3, "可能把旧单腿 planner 过滤器误当成市场机会全集"),
    "settled_prefilter": (r"final_yes\s+IS\s+NOT\s+NULL|settlement_status\s*=\s*['\"]settled['\"]", 2, "若在 group decision-set 前使用，会把未结算 bracket 从分布删除"),
    "all_legs_settled": (r"all\([^\n]*(settlement_status|final_yes)|legs_ready", 4, "若要求全 universe settled，会把样本卡死；应只要求被选腿可评估"),
    "full_orderbook_match_only": (r"fully_matched|all_legs_have_price|all_legs_have_spread|orderbook.*matched", 2, "适合执行压力测试，但不应作为机会全集主分母"),
    "posthoc_best_or_top": (r"best_.*holdout|rank_key|sorted\([^\n]*holdout|top_or_none", 2, "需要确认不是看 holdout 后挑规则"),
}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=str(DB_DEFAULT))
    ap.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    ap.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    return ap.parse_args()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def data_self_check(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "max_fact_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class_distribution": rows(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
        "settlement_status_distribution": rows(conn, "SELECT COALESCE(settlement_status,'') AS settlement_status, COUNT(*) AS rows FROM fact_trades GROUP BY COALESCE(settlement_status,'') ORDER BY settlement_status"),
        "candidate_coverage": rows(conn, "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled FROM fact_signal_candidates")[0],
        "order_fill_coverage": rows(conn, "SELECT o.status, COUNT(*) AS orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill FROM orders o LEFT JOIN fills f USING(execution_id) WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status"),
        "fact_signal_candidates_max_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"),
    }


def bracket_sort_value(label: str) -> float:
    text = str(label).strip()
    if text.endswith("+"):
        text = text[:-1]
    if "-" in text:
        text = text.split("-", 1)[0]
    try:
        return float(text)
    except ValueError:
        return 9999.0


def choose_row(prev: dict[str, Any] | None, row: dict[str, Any]) -> dict[str, Any]:
    if prev is None:
        return row
    if prev.get("final_yes") is None and row.get("final_yes") is not None:
        return row
    if int(prev.get("eligible") or 0) == 0 and int(row.get("eligible") or 0) == 1:
        return row
    if prev.get("side") != "BUY_YES" and row.get("side") == "BUY_YES":
        return row
    return prev


def load_candidate_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows(
        conn,
        """
        SELECT condition_id, market_id, side, event_date, city, city_pool, bracket,
               forecast_source, model_version, decision_snapshot_ts_utc, model_p_yes,
               market_yes_price, eligible, settlement_status, final_yes
        FROM fact_signal_candidates
        WHERE decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND condition_id IS NOT NULL
          AND market_id IS NOT NULL
          AND event_date IS NOT NULL
          AND city IS NOT NULL
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND market_yes_price > 0
          AND market_yes_price < 1
        """,
    )


def decision_sets(candidate_rows: list[dict[str, Any]], *, side: str | None = None, eligible_only: bool = False, settled_prefilter: bool = False) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in candidate_rows:
        if side is not None and row.get("side") != side:
            continue
        if eligible_only and int(row.get("eligible") or 0) != 1:
            continue
        if settled_prefilter and not (row.get("settlement_status") == "settled" and row.get("final_yes") is not None):
            continue
        key = (
            str(row["city"]),
            str(row["event_date"]),
            str(row["forecast_source"]),
            str(row["model_version"]),
            str(row["decision_snapshot_ts_utc"]),
        )
        bracket = str(row["bracket"])
        grouped[key][bracket] = choose_row(grouped[key].get(bracket), row)
    out = []
    for by_bracket in grouped.values():
        ordered = sorted(by_bracket.values(), key=lambda r: bracket_sort_value(str(r["bracket"])))
        if len(ordered) >= 3:
            out.append(ordered)
    return out


def mode_adj3_selected_legs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mode_idx = max(range(len(items)), key=lambda idx: float(items[idx]["model_p_yes"]))
    start = min(max(0, mode_idx - 1), len(items) - 3)
    return items[start : start + 3]


def funnel_summary(name: str, sets: list[list[dict[str, Any]]]) -> dict[str, Any]:
    dist = Counter(len(items) for items in sets)
    mode_evaluable = 0
    fully_settled = 0
    selected_dates: set[str] = set()
    selected_cities: set[str] = set()
    for items in sets:
        if all(row.get("settlement_status") == "settled" and row.get("final_yes") is not None for row in items):
            fully_settled += 1
        selected = mode_adj3_selected_legs(items)
        if all(row.get("settlement_status") == "settled" and row.get("final_yes") is not None for row in selected):
            mode_evaluable += 1
            selected_dates.add(str(items[0]["event_date"]))
            selected_cities.add(str(items[0]["city"]))
    return {
        "name": name,
        "decision_sets": len(sets),
        "bracket_count_distribution": dict(sorted(dist.items())),
        "min_5_brackets": sum(1 for items in sets if len(items) >= 5),
        "min_7_brackets": sum(1 for items in sets if len(items) >= 7),
        "min_9_brackets": sum(1 for items in sets if len(items) >= 9),
        "min_11_brackets": sum(1 for items in sets if len(items) >= 11),
        "mode_adj3_selected_legs_settled": mode_evaluable,
        "mode_adj3_selected_event_dates": len(selected_dates),
        "mode_adj3_selected_cities": len(selected_cities),
        "all_brackets_settled": fully_settled,
    }


def dynamic_denominator_audit(conn: sqlite3.Connection) -> dict[str, Any]:
    base = load_candidate_rows(conn)
    return {
        "base_rows": len(base),
        "side_distribution": rows(conn, "SELECT side, COUNT(*) AS rows, SUM(CASE WHEN final_yes IS NOT NULL THEN 1 ELSE 0 END) AS final_rows, SUM(CASE WHEN eligible=1 THEN 1 ELSE 0 END) AS eligible_rows FROM fact_signal_candidates GROUP BY side ORDER BY side"),
        "funnels": [
            funnel_summary("buy_yes_only", decision_sets(base, side="BUY_YES")),
            funnel_summary("buy_no_only", decision_sets(base, side="BUY_NO")),
            funnel_summary("union_buy_yes_buy_no", decision_sets(base, side=None)),
            funnel_summary("union_eligible_only", decision_sets(base, side=None, eligible_only=True)),
            funnel_summary("union_settled_prefilter", decision_sets(base, side=None, settled_prefilter=True)),
            funnel_summary("union_eligible_and_settled_prefilter", decision_sets(base, side=None, eligible_only=True, settled_prefilter=True)),
        ],
    }


def scan_file(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    hits = []
    score = 0
    lines = text.splitlines()
    for name, (pattern, weight, reason) in PATTERNS.items():
        rx = re.compile(pattern, re.IGNORECASE)
        matched = []
        for idx, line in enumerate(lines, start=1):
            if rx.search(line):
                matched.append({"line": idx, "text": line.strip()[:180]})
        if matched:
            score += weight * min(len(matched), 3)
            hits.append({"pattern": name, "weight": weight, "reason": reason, "matches": matched[:6], "match_count": len(matched)})
    if not hits:
        return None
    return {
        "path": str(path.relative_to(ROOT)),
        "risk_score": score,
        "hits": hits,
    }


def static_scan() -> list[dict[str, Any]]:
    found = []
    for directory in SCRIPT_DIRS:
        for path in sorted(directory.glob("*.py")):
            item = scan_file(path)
            if item:
                found.append(item)
    return sorted(found, key=lambda x: (x["risk_score"], x["path"]), reverse=True)


def fmt_int(value: Any) -> str:
    return "NA" if value is None else str(value)


def table(headers: list[str], body: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in body:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def render_md(report: dict[str, Any]) -> str:
    funnels = report["dynamic_audit"]["funnels"]
    rows_out = []
    for row in funnels:
        rows_out.append([
            row["name"],
            row["decision_sets"],
            row["min_9_brackets"],
            row["min_11_brackets"],
            row["mode_adj3_selected_legs_settled"],
            row["mode_adj3_selected_event_dates"],
            row["all_brackets_settled"],
        ])
    priority = report["priority_findings"]
    static_rows = []
    for item in priority:
        static_rows.append([item["path"], item["risk_score"], ", ".join(hit["pattern"] for hit in item["hits"][:4])])
    lines = [
        "# Weather Strategy Denominator Audit v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: local research audit only; no N100/live config changed; no live action.",
        "",
        "## 一句话",
        "",
        "- 是的，类似 denominator / 方法口径风险不止 adjacent3 一个；老 Range RV、matched baseline、market-shape、forecast-first 里都有需要复核的硬过滤。",
        "- 最大风险不是 SQL 写错，而是把 `fact_signal_candidates` 当完整 bracket 分布使用时先按 side、eligible、settlement 或 orderbook 过滤，导致 city-day universe 被切残。",
        "- 本报告只给审计优先级，不给策略 live 结论。",
        "",
        "## 数据快照",
        "",
        f"- fact_signal_candidates rows: `{report['data_self_check']['candidate_coverage']['rows']}`; fact built: `{report['data_self_check']['fact_signal_candidates_max_built_at_utc']}`。",
        f"- fact_trades max built: `{report['data_self_check']['max_fact_built_at_utc']}`。",
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
        "## 实证分母漏斗",
        "",
        table(
            ["denominator", "decision sets", "n>=9", "n>=11", "mode adj3 selected settled", "selected dates", "all brackets settled"],
            rows_out,
        ),
        "",
        "## 静态扫描优先级",
        "",
        table(["script", "risk_score", "patterns"], static_rows[:20]),
        "",
        "## 需要优先复核的脚本",
        "",
        "1. `research_adjacent3_quality_shadow_journal_v0.py` / `research_adjacent3_quality_matched_baseline_v0.py`: BUY_YES-only universe，必须用 union builder 重跑。",
        "2. `research_range_rv_scanner.py`: 先 `settlement_status='settled'` / `final_yes IS NOT NULL` 再 group，容易把未结算 bracket 从分布删掉。",
        "3. `research_range_rv_variant_lab_v03.py`: 继承 scanner denominator，且部分算法用 eligible 子集；需要 union + selected-leg settled 口径复跑。",
        "4. `research_forecast_first_adjacent_range_rv.py`: `all_legs_eligible` / all legs price/spread 属于执行覆盖过滤，不能当主机会分母。",
        "5. `research_range_rv_market_shape_v05.py`: `require_eligible=True` 默认值需要复核，避免旧 planner 过滤器进入新策略研究。",
        "",
        "## 新标准",
        "",
        "- city-day/range 研究先建 `BUY_YES + BUY_NO` union bracket universe。",
        "- 选择策略时可以用完整 universe；评估 PnL 时只要求被选中的腿有 `final_yes`。",
        "- `eligible=1`、`all_legs_eligible`、`fully_matched_orderbook` 只能作为子分析或执行压力测试，不能默认替代机会全集。",
        "- 每份新报告必须输出 bracket coverage distribution 和每层漏斗。",
        "",
        "## 三门状态",
        "",
        table(
            ["gate", "status", "reason"],
            [
                ["significance", "NA", "本报告是 denominator 审计，不估计策略 ROI 显著性。"],
                ["baseline", "NA", "不比较交易规则收益，只审计分母风险。"],
                ["forward", "NA", "无 live 动作；下一步是按新分母重跑候选。"],
            ],
        ),
        "",
        "结论：`audit_only`。不改 live、不加 size、不扩池。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    conn = connect(args.db_path)
    static = static_scan()
    priority = [
        item for item in static
        if item["path"] not in {
            "scripts/analysis/market_structure_edge/research_denominator_audit_v0.py",
            "scripts/analysis/market_structure_edge/research_adjacent3_union_flexible_v02.py",
        }
    ]
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": TARGET_METRIC,
        "db_path": str(Path(args.db_path)),
        "data_self_check": data_self_check(conn),
        "dynamic_audit": dynamic_denominator_audit(conn),
        "static_scan": static,
        "priority_findings": priority[:25],
        "verdict": "audit_only",
        "live_action": "none",
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_md(report), encoding="utf-8")
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    print(f"static_findings={len(static)} verdict={report['verdict']}")


if __name__ == "__main__":
    main()
