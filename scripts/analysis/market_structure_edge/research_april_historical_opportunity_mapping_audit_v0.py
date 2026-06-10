#!/usr/bin/env python3
"""Audit whether April raw market data can become opportunity facts.

This is a schema and coverage audit only. It does not compute strategy PnL from
raw files and does not change N100/live configuration. Any strategy conclusion
still requires materializing data into runtime/weather.db fact tables first.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
MARKET_DATA = ROOT / "runtime" / "weather_edge_v1" / "market_data"
GAMMA_DIR = MARKET_DATA / "gamma_events"
CLOB_HISTORY_DIR = MARKET_DATA / "clob_price_history"
PM_HISTORY_DIR = MARKET_DATA / "cache" / "pm_history"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-april-historical-opportunity-mapping-audit-v0.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-april-historical-opportunity-mapping-audit-v0.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--start-date", default="2026-04-01")
    parser.add_argument("--end-date", default="2026-04-30")
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


def parse_json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def in_range(date_text: str, start: str, end: str) -> bool:
    return start <= date_text <= end


def summarize_db(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
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


def summarize_gamma_events(start: str, end: str) -> dict[str, Any]:
    files = sorted(GAMMA_DIR.glob("*/*.json"))
    token_to_market: dict[str, dict[str, Any]] = {}
    city_day_market_counts: Counter[str] = Counter()
    city_day_found: Counter[str] = Counter()
    by_date: Counter[str] = Counter()
    by_city: Counter[str] = Counter()
    found_files = 0
    missing_files = 0
    market_rows = 0
    missing_condition = 0
    missing_token_pair = 0
    missing_bracket_label = 0
    sample_markets: list[dict[str, Any]] = []

    for path in files:
        target_date = path.parent.name
        if not in_range(target_date, start, end):
            continue
        city = path.stem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            missing_files += 1
            continue
        city_day = f"{city}|{target_date}"
        if not data.get("found") or not isinstance(data.get("event"), dict):
            missing_files += 1
            continue
        found_files += 1
        city_day_found[city_day] += 1
        by_date[target_date] += 1
        by_city[city] += 1
        event = data["event"]
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            market_rows += 1
            city_day_market_counts[city_day] += 1
            condition_id = market.get("conditionId")
            if not condition_id:
                missing_condition += 1
            tokens = parse_json_array(market.get("clobTokenIds"))
            if len(tokens) != 2:
                missing_token_pair += 1
            bracket = market.get("groupItemTitle") or market.get("question")
            if not bracket:
                missing_bracket_label += 1
            outcomes = parse_json_array(market.get("outcomes"))
            for side_idx, token_id in enumerate(tokens):
                token_to_market[str(token_id)] = {
                    "city": city,
                    "event_date": target_date,
                    "condition_id": condition_id,
                    "market_id": market.get("id"),
                    "bracket": bracket,
                    "side": str(outcomes[side_idx]) if side_idx < len(outcomes) else ("Yes" if side_idx == 0 else "No"),
                    "active": market.get("active"),
                    "closed": market.get("closed"),
                    "accepting_orders": market.get("acceptingOrders"),
                    "best_bid": market.get("bestBid"),
                    "best_ask": market.get("bestAsk"),
                    "last_trade_price": market.get("lastTradePrice"),
                }
            if len(sample_markets) < 5:
                sample_markets.append(
                    {
                        "city": city,
                        "event_date": target_date,
                        "condition_id": condition_id,
                        "market_id": market.get("id"),
                        "bracket": bracket,
                        "outcomes": outcomes,
                        "token_count": len(tokens),
                        "best_bid": market.get("bestBid"),
                        "best_ask": market.get("bestAsk"),
                    }
                )

    market_count_values = list(city_day_market_counts.values())
    return {
        "path": str(GAMMA_DIR),
        "files_in_range": found_files + missing_files,
        "found_event_files": found_files,
        "missing_or_bad_files": missing_files,
        "city_days_found": len(city_day_found),
        "event_dates_found": len(by_date),
        "cities_found": len(by_city),
        "market_rows": market_rows,
        "token_rows": len(token_to_market),
        "city_day_market_count_min": min(market_count_values) if market_count_values else None,
        "city_day_market_count_max": max(market_count_values) if market_count_values else None,
        "missing_condition_rows": missing_condition,
        "missing_token_pair_rows": missing_token_pair,
        "missing_bracket_label_rows": missing_bracket_label,
        "by_date": dict(sorted(by_date.items())),
        "top_cities": dict(by_city.most_common(10)),
        "sample_markets": sample_markets,
        "token_to_market": token_to_market,
    }


def summarize_clob_history(start: str, end: str, token_to_market: dict[str, dict[str, Any]]) -> dict[str, Any]:
    files = sorted(CLOB_HISTORY_DIR.glob("*/*/*.json"))
    in_range_files = 0
    decoded_files = 0
    matched_tokens = 0
    unmatched_tokens = 0
    history_rows = 0
    rows_before_event_date = 0
    rows_after_event_date = 0
    min_ts: int | None = None
    max_ts: int | None = None
    matched_city_days: set[str] = set()
    matched_markets: set[str] = set()
    by_date: Counter[str] = Counter()
    by_city: Counter[str] = Counter()
    sample_matched: list[dict[str, Any]] = []
    sample_unmatched: list[dict[str, Any]] = []
    horizon_hours = [24, 18, 12, 6]
    decision_price_coverage = {hours: 0 for hours in horizon_hours}
    decision_price_stale_minutes: dict[int, list[float]] = {hours: [] for hours in horizon_hours}

    for path in files:
        target_date = path.parents[1].name
        if not in_range(target_date, start, end):
            continue
        in_range_files += 1
        city = path.parent.name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        decoded_files += 1
        token_id = str(data.get("token_id") or "")
        mapping = token_to_market.get(token_id)
        if mapping:
            matched_tokens += 1
            matched_city_days.add(f"{mapping['city']}|{mapping['event_date']}")
            matched_markets.add(f"{mapping.get('condition_id')}|{mapping.get('side')}")
            by_date[mapping["event_date"]] += 1
            by_city[mapping["city"]] += 1
            if len(sample_matched) < 5:
                sample_matched.append(
                    {
                        "file": str(path.relative_to(MARKET_DATA)),
                        "city": mapping["city"],
                        "event_date": mapping["event_date"],
                        "bracket": mapping["bracket"],
                        "side": mapping["side"],
                        "history_rows": len(data.get("history") or []),
                    }
                )
            history_ts = [point.get("t") for point in data.get("history") or [] if isinstance(point.get("t"), int)]
            event_noon_ts = int(datetime.fromisoformat(f"{mapping['event_date']}T12:00:00+00:00").timestamp())
            for hours in horizon_hours:
                decision_ts = event_noon_ts - hours * 3600
                eligible_ts = [ts for ts in history_ts if ts <= decision_ts]
                if eligible_ts:
                    best_ts = max(eligible_ts)
                    decision_price_coverage[hours] += 1
                    decision_price_stale_minutes[hours].append((decision_ts - best_ts) / 60.0)
        else:
            unmatched_tokens += 1
            if len(sample_unmatched) < 5:
                sample_unmatched.append({"file": str(path.relative_to(MARKET_DATA)), "token_id_suffix": token_id[-20:]})
        event_start_ts = int(datetime.fromisoformat(f"{target_date}T00:00:00+00:00").timestamp())
        for point in data.get("history") or []:
            ts = point.get("t")
            if not isinstance(ts, int):
                continue
            history_rows += 1
            min_ts = ts if min_ts is None else min(min_ts, ts)
            max_ts = ts if max_ts is None else max(max_ts, ts)
            if ts <= event_start_ts:
                rows_before_event_date += 1
            else:
                rows_after_event_date += 1

    def iso(ts: int | None) -> str | None:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None

    return {
        "path": str(CLOB_HISTORY_DIR),
        "files_in_range": in_range_files,
        "decoded_files": decoded_files,
        "matched_token_files": matched_tokens,
        "unmatched_token_files": unmatched_tokens,
        "matched_city_days": len(matched_city_days),
        "matched_condition_side_rows": len(matched_markets),
        "history_rows": history_rows,
        "history_min_ts_utc": iso(min_ts),
        "history_max_ts_utc": iso(max_ts),
        "history_rows_before_event_date_utc": rows_before_event_date,
        "history_rows_after_event_date_utc": rows_after_event_date,
        "decision_price_coverage_by_hours_to_noon_utc": {
            str(hours): {
                "tokens_with_price_at_or_before_decision": decision_price_coverage[hours],
                "coverage_rate": decision_price_coverage[hours] / matched_tokens if matched_tokens else 0.0,
                "median_stale_minutes": median(decision_price_stale_minutes[hours]),
            }
            for hours in horizon_hours
        },
        "by_event_date": dict(sorted(by_date.items())),
        "top_cities": dict(by_city.most_common(10)),
        "sample_matched": sample_matched,
        "sample_unmatched": sample_unmatched,
    }


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def summarize_pm_history_settlements(start: str, end: str) -> dict[str, Any]:
    files = sorted(PM_HISTORY_DIR.glob("*.json"))
    city_day_files = []
    price_files = []
    for path in files:
        name = path.name
        if name.startswith("prices_"):
            price_files.append(path)
            continue
        if not name.endswith(".json") or "_" not in name:
            continue
        date_text = name.rsplit("_", 1)[-1].replace(".json", "")
        if in_range(date_text, start, end):
            city_day_files.append(path)
    return {
        "path": str(PM_HISTORY_DIR),
        "city_day_settlement_files_in_range": len(city_day_files),
        "price_history_files_total": len(price_files),
        "sample_city_day_files": [str(path.relative_to(PM_HISTORY_DIR)) for path in city_day_files[:5]],
        "sample_price_files": [str(path.relative_to(PM_HISTORY_DIR)) for path in price_files[:5]],
    }


def build_assessment(report: dict[str, Any]) -> dict[str, Any]:
    gamma = report["gamma_events"]
    clob = report["clob_price_history"]
    settlement = report["pm_history"]
    token_match_rate = (
        clob["matched_token_files"] / clob["decoded_files"]
        if clob["decoded_files"]
        else 0.0
    )
    has_gamma_mapping = gamma["market_rows"] > 0 and gamma["missing_condition_rows"] == 0 and gamma["missing_token_pair_rows"] == 0
    has_tradeable_price_series = clob["matched_token_files"] > 0 and token_match_rate > 0.95
    has_settlements = settlement["city_day_settlement_files_in_range"] > 0
    return {
        "can_materialize_full_fact_signal_candidates_now": bool(has_gamma_mapping and has_tradeable_price_series and has_settlements),
        "can_build_mapping_prototype": bool(has_gamma_mapping and has_tradeable_price_series),
        "token_match_rate": token_match_rate,
        "hard_blockers": [
            blocker
            for blocker, active in [
                ("April city-day settlement files are missing from pm_history, so final_yes cannot be filled.", not has_settlements),
                ("CLOB history is token price history, not orderbook depth; taker/maker executable cost still needs orderbook or a conservative price proxy.", has_tradeable_price_series),
                ("No model probability/eligible snapshot is present in gamma/clob history; a historical forecast join is required before strategy rules can be evaluated.", True),
                ("Raw files are not an authorized strategy PnL source; they must be materialized into fact_signal_candidates first.", True),
            ]
            if active
        ],
        "minimum_builder_plan": [
            "Build token_to_market from gamma_events markets: city, event_date, condition_id, market_id, bracket, side, clob token.",
            "Join clob_price_history by full token_id to attach historical YES/NO token price time series.",
            "Choose a frozen decision timestamp policy before looking at outcomes, then select last price at or before that timestamp.",
            "Join historical forecast/model probability visible at that decision timestamp.",
            "Backfill April city-day settlement final_yes at the same bracket grain.",
            "Materialize rows into a fact-like table with condition_id, side, event_date, decision_snapshot_ts_utc, market_yes_price, decision_entry_price, final_yes.",
            "Only after materialization, rerun Range RV/adjacent3 gates with train/holdout and baselines.",
        ],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def table(headers: list[str], rows_in: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows_in:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def write_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gamma = report["gamma_events"]
    clob = report["clob_price_history"]
    settlement = report["pm_history"]
    assessment = report["assessment"]
    lines = [
        "# April Historical Opportunity Mapping Audit v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> git_sha: `{report['git_sha']}`",
        "> Scope: schema/coverage audit only; no raw-file strategy PnL; no N100/live config changed.",
        "",
        "## 数据快照",
        "",
        "- 策略结论授权源仍是 `runtime/weather.db.fact_signal_candidates` / `fact_trades`。",
        f"- 审计窗口：`{report['window']['start_date']}` 到 `{report['window']['end_date']}`。",
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
        "## Mapping 覆盖",
        "",
        table(
            ["source", "coverage", "meaning"],
            [
                [
                    "gamma_events",
                    f"{gamma['found_event_files']} found files / {gamma['city_days_found']} city-days / {gamma['market_rows']} markets / {gamma['token_rows']} tokens",
                    "可提供 city、event_date、condition_id、market_id、bracket、YES/NO token 映射。",
                ],
                [
                    "clob_price_history",
                    f"{clob['matched_token_files']}/{clob['decoded_files']} token files matched; {clob['matched_city_days']} city-days",
                    "可按 token 接历史成交价格序列，但不是 orderbook depth。",
                ],
                [
                    "pm_history city-day settlement",
                    f"{settlement['city_day_settlement_files_in_range']} files in April",
                    "当前缺 April 同 grain final_yes，是 fact 化硬缺口。",
                ],
            ],
        ),
        "",
        "## Decision-Time Price 覆盖",
        "",
        table(
            ["hours_to_noon_utc", "tokens with price <= decision_ts", "coverage", "median stale minutes"],
            [
                [
                    hours,
                    item["tokens_with_price_at_or_before_decision"],
                    f"{item['coverage_rate']:.1%}",
                    item["median_stale_minutes"],
                ]
                for hours, item in clob["decision_price_coverage_by_hours_to_noon_utc"].items()
            ],
        ),
        "",
        "## 人话结论",
        "",
        f"- April raw market mapping 原型：`{'可以做' if assessment['can_build_mapping_prototype'] else '暂时不能做'}`。",
        f"- 直接扩成完整 `fact_signal_candidates`：`{'可以' if assessment['can_materialize_full_fact_signal_candidates_now'] else '不可以'}`。",
        "- 关键好消息：gamma 事件本身已经给出 city/date/bracket/condition/token 映射，clob price history 也能用 full token_id 对上。",
        "- 关键坏消息：这还只是 token 价格历史，不是可成交盘口；而且 April city-day settlement final_yes 当前缺失，forecast/model probability 也还没接进来。",
        "- 所以它能支持下一步 historical opportunity builder 原型，不能直接支持策略 ROI 或 live-test 结论。",
        "",
        "## Hard Blockers",
        "",
        "\n".join(f"- {item}" for item in assessment["hard_blockers"]),
        "",
        "## 最小 Builder 路线",
        "",
        "\n".join(f"{idx + 1}. {step}" for idx, step in enumerate(assessment["minimum_builder_plan"])),
        "",
        "## 样例 Market",
        "",
        "```json",
        json.dumps(gamma["sample_markets"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 样例 Matched Price Files",
        "",
        "```json",
        json.dumps(clob["sample_matched"], ensure_ascii=False, indent=2),
        "```",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    conn = connect(args.db_path)
    gamma = summarize_gamma_events(args.start_date, args.end_date)
    token_to_market = gamma.pop("token_to_market")
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "window": {"start_date": args.start_date, "end_date": args.end_date},
        "db": summarize_db(conn, Path(args.db_path)),
        "gamma_events": gamma,
        "clob_price_history": summarize_clob_history(args.start_date, args.end_date, token_to_market),
        "pm_history": summarize_pm_history_settlements(args.start_date, args.end_date),
    }
    report["assessment"] = build_assessment(report)
    write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(args.out_json)
    print(args.out_md)


if __name__ == "__main__":
    main()
