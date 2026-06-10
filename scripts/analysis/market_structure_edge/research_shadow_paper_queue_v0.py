#!/usr/bin/env python3
"""Build the current weather shadow/paper strategy queue.

The queue is an operational research registry. It does not change live/N100
configuration, does not place orders, and does not promote any strategy to live.
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
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-shadow-paper-queue-v0.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-shadow-paper-queue-v0.md"


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


def iso_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def data_self_check(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    return {
        "db_path": str(db_path),
        "db_last_modified_utc": iso_mtime(db_path),
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
        "fact_signal_candidates_range": rows(
            conn,
            "SELECT MIN(event_date) AS min_event_date, MAX(event_date) AS max_event_date, "
            "COUNT(DISTINCT event_date) AS event_dates, COUNT(*) AS rows FROM fact_signal_candidates",
        )[0],
    }


def evidence_file(path: str) -> dict[str, Any]:
    p = ROOT / path
    return {
        "path": path,
        "exists": p.exists(),
        "last_modified_utc": iso_mtime(p),
    }


def build_queue() -> dict[str, Any]:
    candidates = [
        {
            "queue_id": "forecast_quality_medium_adjacent3_shadow_v0",
            "family": "forecast_first_range_rv",
            "mode": "shadow_primary",
            "can_run_now": True,
            "live_allowed_now": False,
            "paper_allowed_now": False,
            "reason_human": (
                "非 all-YES 里最像天气预测核心策略：模型分布很集中时观察 mode 附近三档 YES。"
                "但 matched baseline/holdout 仍不过，只能 shadow。"
            ),
            "current_gates": {"significance": "FAIL", "baseline": "FAIL_OR_NA", "forward": "FAIL"},
            "run_command": (
                "python3 scripts/analysis/market_structure_edge/"
                "research_adjacent3_quality_shadow_journal_v0.py"
            ),
            "evidence": [
                evidence_file("docs/analysis/2026-06/2026-06-10-adjacent3-quality-shadow-journal-v0.md"),
                evidence_file("docs/analysis/2026-06/2026-06-10-adjacent3-quality-matched-baseline-v0.md"),
                evidence_file("docs/analysis/2026-06/2026-06-10-live-test-readiness-scoreboard-v0.md"),
            ],
            "promotion_requirements": [
                ">=30 forward event_dates",
                ">=100 settled shadow decisions",
                ">=50 full orderbook matched decisions",
                "decision proxy cluster CI lower > 0",
                "orderbook taker cluster CI lower > 0",
                "matched baseline excess CI lower > 0",
                "top5 removed orderbook ROI > 0",
                "all orderbook_snapshot_ts_utc <= decision_snapshot_ts_utc",
            ],
        },
        {
            "queue_id": "side_band_mechanism_shadow_tags_v1",
            "family": "side_band",
            "mode": "shadow_tag_only",
            "can_run_now": True,
            "live_allowed_now": False,
            "paper_allowed_now": False,
            "reason_human": (
                "早期真钱赚过是真的，但机制归因显示收益依赖少数日期和 side/price 形态；"
                "适合继续打 tag 观察，不适合独立 paper/live。"
            ),
            "current_gates": {"significance": "FAIL", "baseline": "FAIL", "forward": "FAIL"},
            "run_command": "python3 scripts/analysis/side_alpha/research_side_band_mechanism_attribution_v1.py",
            "evidence": [
                evidence_file("docs/analysis/2026-06/2026-06-10-side-band-mechanism-attribution-v1.md"),
                evidence_file("docs/analysis/2026-06/2026-06-10-side-band-forecast-regime-v0.md"),
            ],
            "promotion_requirements": [
                "预注册新 selector，不复用本轮 holdout 调参",
                "top5 removed ROI > 0",
                "matched same side/hour/price baseline excess CI lower > 0",
                "forward dates >=30",
            ],
        },
        {
            "queue_id": "blended_single_leg_paper_profiles",
            "family": "single_leg_blended_baselines",
            "mode": "paper_baseline",
            "can_run_now": True,
            "live_allowed_now": False,
            "paper_allowed_now": True,
            "reason_human": (
                "已有 ops paper profiles，可作为低成本对照组继续跑；"
                "它不是当前 live 候选，主要用于比较 adjacent3/side-band 是否真的有增量。"
            ),
            "current_gates": {"significance": "NA", "baseline": "NA", "forward": "NA"},
            "run_command": "scripts/ops/weather_blended_shadow_paper_loop.sh",
            "evidence": [
                evidence_file("scripts/ops/weather_blended_shadow_paper.py"),
                evidence_file("scripts/ops/weather_blended_shadow_paper_loop.sh"),
            ],
            "promotion_requirements": [
                "只作为 baseline/paper，不从该队列直接晋级 live",
                "与 primary shadow 使用同一日期窗口对比",
            ],
        },
        {
            "queue_id": "all_yes_underround_engineering_shadow",
            "family": "model_free_underround",
            "mode": "engineering_shadow_deferred",
            "can_run_now": True,
            "live_allowed_now": False,
            "paper_allowed_now": False,
            "reason_human": (
                "统计三门通过，但用户已明确 all-YES 暂不实盘；"
                "只能做工程 shadow：多腿 partial fill、滑点、手续费、unwind 仿真。"
            ),
            "current_gates": {"significance": "PASS", "baseline": "PASS", "forward": "PASS"},
            "run_command": "none yet; requires dedicated basket execution simulator",
            "evidence": [
                evidence_file("docs/analysis/2026-06/2026-06-09-range-rv-underround-robust-v1-0.md"),
                evidence_file("docs/analysis/2026-06/2026-06-10-live-test-readiness-scoreboard-v0.md"),
            ],
            "promotion_requirements": [
                "user explicitly re-allows all-YES live consideration",
                "basket execution simulator with partial fill unwind",
                "fee/slippage stress remains positive",
            ],
        },
        {
            "queue_id": "single_high_conviction_yes_fallback",
            "family": "single_leg_fallback",
            "mode": "fallback_shadow_only",
            "can_run_now": True,
            "live_allowed_now": False,
            "paper_allowed_now": False,
            "reason_human": (
                "99%+ 与 adjacent3 重叠，不是独立策略；只在 adjacent3 三腿太贵或缺腿时记录 fallback。"
            ),
            "current_gates": {"significance": "FAIL", "baseline": "FAIL", "forward": "FAIL"},
            "run_command": "covered by hybrid adjacent3/single research; no standalone loop",
            "evidence": [
                evidence_file("docs/analysis/2026-06/2026-06-10-hybrid-adjacent3-single-v0.md"),
            ],
            "promotion_requirements": [
                "必须证明不是 adjacent3 的重复加注",
                "same cost/same market baseline excess CI lower > 0",
            ],
        },
        {
            "queue_id": "april_historical_opportunity_backfill",
            "family": "data_expansion",
            "mode": "backfill_input",
            "can_run_now": True,
            "live_allowed_now": False,
            "paper_allowed_now": False,
            "reason_human": (
                "这是扩样数据工程，不是策略。已经能生成 April decision price preview；"
                "下一步补 model_p_yes/final_yes/orderbook proxy 后，才能回测 shadow 候选。"
            ),
            "current_gates": {"significance": "NA", "baseline": "NA", "forward": "NA"},
            "run_command": (
                "python3 scripts/analysis/market_structure_edge/"
                "build_april_historical_opportunity_preview_v0.py"
            ),
            "evidence": [
                evidence_file("docs/analysis/2026-06/2026-06-10-april-historical-opportunity-preview-v0.md"),
                evidence_file("docs/analysis/2026-06/2026-06-10-april-historical-opportunity-mapping-audit-v0.md"),
            ],
            "promotion_requirements": [
                "join historical forecast probability to model_p_yes",
                "join April settlement to final_yes",
                "define executable cost proxy or orderbook depth availability",
                "materialize into fact_signal_candidates-compatible table",
            ],
        },
    ]
    return {
        "policy": {
            "live_now": [],
            "primary_shadow": ["forecast_quality_medium_adjacent3_shadow_v0"],
            "paper_now": ["blended_single_leg_paper_profiles"],
            "shadow_tags": ["side_band_mechanism_shadow_tags_v1", "single_high_conviction_yes_fallback"],
            "engineering_shadow": ["all_yes_underround_engineering_shadow"],
            "data_backfill": ["april_historical_opportunity_backfill"],
        },
        "candidates": candidates,
        "live_promotion_rule": (
            "A queue item can enter live scheduling only after significance/baseline/forward PASS, "
            "execution coverage is time-aligned, top5 stress is positive, and user explicitly approves live scheduling."
        ),
    }


def table(headers: list[str], rows_in: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows_in:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    queue = report["queue"]
    candidates = queue["candidates"]
    lines = [
        "# Weather Shadow/Paper Queue v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> git_sha: `{report['git_sha']}`",
        "> Scope: local research queue only; no live/N100 config changed; no orders placed.",
        "",
        "## 数据快照",
        "",
        "- 数据源：`runtime/weather.db.fact_signal_candidates` / `fact_trades` 自检 + 已提交研究报告。",
        f"- fact_signal_candidates 当前范围：`{report['db']['fact_signal_candidates_range']}`。",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(
            {
                "max_fact_built_at_utc": report["db"]["max_fact_built_at_utc"],
                "trade_class_distribution": report["db"]["trade_class_distribution"],
                "settlement_status_distribution": report["db"]["settlement_status_distribution"],
                "candidate_coverage": report["db"]["candidate_coverage"],
                "order_fill_coverage": report["db"]["order_fill_coverage"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## 人话结论",
        "",
        "- 可以同时跑多个 shadow/paper，但要分层：shadow 主策略、paper baseline、shadow tag、工程 shadow、数据 backfill 分开。",
        "- 当前真钱 live 队列为空；live 后面单独排班，不能和 shadow/paper 混在一起。",
        "- 主 shadow 只有 `forecast_quality_medium_adjacent3_shadow_v0`；它最贴天气预测核心，但三门仍不过。",
        "- 旧 side-band 可以继续打 tag 观察；blended single-leg paper profiles 可以继续当 baseline；April preview 是扩样工程输入。",
        "",
        "## 队列表",
        "",
        table(
            ["queue_id", "mode", "run now", "paper now", "live now", "gates", "reason"],
            [
                [
                    item["queue_id"],
                    item["mode"],
                    item["can_run_now"],
                    item["paper_allowed_now"],
                    item["live_allowed_now"],
                    "/".join(item["current_gates"].values()),
                    item["reason_human"],
                ]
                for item in candidates
            ],
        ),
        "",
        "## 建议执行节奏",
        "",
        "1. 每日/每次 fact refresh 后跑 primary shadow：`research_adjacent3_quality_shadow_journal_v0.py`。",
        "2. 保持 blended single-leg paper loop 作为 baseline，不从它直接晋级 live。",
        "3. side-band / single-leg fallback 只作为 tag 写入 shadow journal 或独立 attribution，不作为下单规则。",
        "4. April backfill 继续补 `model_p_yes` 和 `final_yes`，补完后重跑 adjacent3/side-band 三门。",
        "5. live 排班只接收三门全过且 execution stress 过关的候选。",
        "",
        "## Live 排班硬门",
        "",
        f"`{queue['live_promotion_rule']}`",
        "",
        "## Run Commands",
        "",
        table(
            ["queue_id", "command"],
            [[item["queue_id"], item["run_command"]] for item in candidates],
        ),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    conn = connect(str(db_path))
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "db": data_self_check(conn, db_path),
        "queue": build_queue(),
    }
    write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(args.out_json)
    print(args.out_md)


if __name__ == "__main__":
    main()
