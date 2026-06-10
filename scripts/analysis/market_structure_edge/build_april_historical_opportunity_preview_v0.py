#!/usr/bin/env python3
"""Build an April historical opportunity preview from gamma + CLOB price history.

This produces fact-like preview rows only. It intentionally does not compute
strategy PnL, does not infer live actions, and does not modify runtime/weather.db.
Rows are useful as the input contract for a future fact_signal_candidates
backfill once settlement and forecast probability joins are available.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
MARKET_DATA = ROOT / "runtime" / "weather_edge_v1" / "market_data"
GAMMA_DIR = MARKET_DATA / "gamma_events"
CLOB_HISTORY_DIR = MARKET_DATA / "clob_price_history"
RESEARCH_DIR = MARKET_DATA / "research"
OUT_CSV_DEFAULT = RESEARCH_DIR / "april_historical_opportunity_preview_v0.csv.gz"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-april-historical-opportunity-preview-v0.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-april-historical-opportunity-preview-v0.md"

BRACKET_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


FIELDNAMES = [
    "preview_id",
    "city",
    "event_date",
    "condition_id",
    "market_id",
    "bracket",
    "bracket_low_f",
    "bracket_high_f",
    "bracket_center_f",
    "side",
    "token_id",
    "yes_token_id",
    "no_token_id",
    "decision_hours_to_noon_utc",
    "decision_snapshot_ts_utc",
    "token_price_ts_utc",
    "token_price_stale_minutes",
    "entry_price",
    "market_yes_price",
    "model_p_yes",
    "final_yes",
    "orderbook_depth_available",
    "source",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--start-date", default="2026-04-01")
    parser.add_argument("--end-date", default="2026-04-30")
    parser.add_argument("--horizons", default="24,18,12,6", help="Comma separated hours before event-date noon UTC.")
    parser.add_argument("--out-csv", default=str(OUT_CSV_DEFAULT))
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


def parse_bracket_range(label: Any) -> tuple[float | None, float | None, float | None]:
    if label is None:
        return None, None, None
    text = str(label)
    nums = [float(x) for x in BRACKET_NUM_RE.findall(text)]
    if not nums:
        return None, None, None
    if "below" in text.lower() or "or less" in text.lower():
        high = nums[0]
        return None, high, high
    if "above" in text.lower() or "or higher" in text.lower() or "or more" in text.lower():
        low = nums[0]
        return low, None, low
    low = min(nums)
    high = max(nums)
    return low, high, (low + high) / 2.0


def in_range(date_text: str, start: str, end: str) -> bool:
    return start <= date_text <= end


def iso_from_ts(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def load_token_price_history(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    history = data.get("history")
    if not isinstance(history, list):
        return []
    out = []
    for point in history:
        if not isinstance(point, dict):
            continue
        ts = point.get("t")
        price = point.get("p")
        if not isinstance(ts, int):
            continue
        try:
            price_f = float(price)
        except (TypeError, ValueError):
            continue
        out.append({"t": ts, "p": price_f})
    return sorted(out, key=lambda row: row["t"])


def price_at_or_before(history: list[dict[str, Any]], decision_ts: int) -> dict[str, Any] | None:
    best = None
    for point in history:
        if point["t"] <= decision_ts:
            best = point
        else:
            break
    return best


def load_markets(start: str, end: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    token_to_market: dict[str, dict[str, Any]] = {}
    stats = Counter()
    by_date = Counter()
    by_city = Counter()
    for path in sorted(GAMMA_DIR.glob("*/*.json")):
        event_date = path.parent.name
        if not in_range(event_date, start, end):
            continue
        city = path.stem
        stats["gamma_files"] += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stats["bad_gamma_files"] += 1
            continue
        if not data.get("found") or not isinstance(data.get("event"), dict):
            stats["missing_gamma_files"] += 1
            continue
        by_date[event_date] += 1
        by_city[city] += 1
        for market in data["event"].get("markets") or []:
            if not isinstance(market, dict):
                continue
            tokens = [str(x) for x in parse_json_array(market.get("clobTokenIds"))]
            outcomes = [str(x) for x in parse_json_array(market.get("outcomes"))]
            if len(tokens) != 2:
                stats["markets_without_token_pair"] += 1
                continue
            yes_token = tokens[0]
            no_token = tokens[1]
            bracket = market.get("groupItemTitle") or market.get("question")
            low, high, center = parse_bracket_range(bracket)
            row = {
                "city": city,
                "event_date": event_date,
                "condition_id": market.get("conditionId"),
                "market_id": market.get("id"),
                "bracket": bracket,
                "bracket_low_f": low,
                "bracket_high_f": high,
                "bracket_center_f": center,
                "yes_token_id": yes_token,
                "no_token_id": no_token,
                "outcomes": outcomes,
            }
            markets.append(row)
            for idx, token in enumerate(tokens):
                side = outcomes[idx] if idx < len(outcomes) else ("Yes" if idx == 0 else "No")
                token_to_market[token] = {**row, "side": side, "token_id": token}
    return markets, token_to_market, {
        **stats,
        "event_dates": len(by_date),
        "cities": len(by_city),
        "city_days": sum(by_date.values()),
        "markets": len(markets),
        "tokens": len(token_to_market),
        "by_date": dict(sorted(by_date.items())),
        "top_cities": dict(by_city.most_common(10)),
    }


def build_preview_rows(
    start: str,
    end: str,
    horizons: list[int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _, token_to_market, gamma_stats = load_markets(start, end)
    token_histories: dict[str, list[dict[str, Any]]] = {}
    loaded_token_files = 0
    unmatched_token_files = 0
    for path in sorted(CLOB_HISTORY_DIR.glob("*/*/*.json")):
        event_date = path.parents[1].name
        if not in_range(event_date, start, end):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        token = str(data.get("token_id") or "")
        if token not in token_to_market:
            unmatched_token_files += 1
            continue
        history = data.get("history")
        if not isinstance(history, list):
            continue
        token_histories[token] = load_token_price_history(path)
        loaded_token_files += 1

    out_rows: list[dict[str, Any]] = []
    coverage = {hours: Counter() for hours in horizons}
    for token, mapping in sorted(token_to_market.items(), key=lambda item: (item[1]["event_date"], item[1]["city"], item[1]["market_id"] or "", item[1]["side"])):
        history = token_histories.get(token, [])
        yes_history = token_histories.get(mapping["yes_token_id"], [])
        event_noon = datetime.fromisoformat(f"{mapping['event_date']}T12:00:00+00:00")
        for hours in horizons:
            decision_dt = event_noon - timedelta(hours=hours)
            decision_ts = int(decision_dt.timestamp())
            token_point = price_at_or_before(history, decision_ts)
            yes_point = price_at_or_before(yes_history, decision_ts)
            if token_point:
                coverage[hours]["token_price_available"] += 1
            if yes_point:
                coverage[hours]["yes_price_available"] += 1
            entry_price = token_point["p"] if token_point else None
            market_yes_price = yes_point["p"] if yes_point else None
            stale_minutes = ((decision_ts - token_point["t"]) / 60.0) if token_point else None
            side_norm = "BUY_YES" if mapping["side"].lower() == "yes" else "BUY_NO"
            out_rows.append(
                {
                    "preview_id": f"{mapping['condition_id']}|{side_norm}|{mapping['event_date']}|T{hours}",
                    "city": mapping["city"],
                    "event_date": mapping["event_date"],
                    "condition_id": mapping["condition_id"],
                    "market_id": mapping["market_id"],
                    "bracket": mapping["bracket"],
                    "bracket_low_f": mapping["bracket_low_f"],
                    "bracket_high_f": mapping["bracket_high_f"],
                    "bracket_center_f": mapping["bracket_center_f"],
                    "side": side_norm,
                    "token_id": token,
                    "yes_token_id": mapping["yes_token_id"],
                    "no_token_id": mapping["no_token_id"],
                    "decision_hours_to_noon_utc": hours,
                    "decision_snapshot_ts_utc": decision_dt.isoformat(),
                    "token_price_ts_utc": iso_from_ts(token_point["t"]) if token_point else None,
                    "token_price_stale_minutes": stale_minutes,
                    "entry_price": entry_price,
                    "market_yes_price": market_yes_price,
                    "model_p_yes": None,
                    "final_yes": None,
                    "orderbook_depth_available": 0,
                    "source": "gamma_events+clob_price_history",
                }
            )
    stats = {
        "gamma": gamma_stats,
        "clob_token_files_loaded": loaded_token_files,
        "clob_token_files_unmatched": unmatched_token_files,
        "preview_rows": len(out_rows),
        "coverage_by_horizon": {
            str(hours): {
                "rows": sum(1 for row in out_rows if row["decision_hours_to_noon_utc"] == hours),
                "token_price_available": int(coverage[hours]["token_price_available"]),
                "yes_price_available": int(coverage[hours]["yes_price_available"]),
            }
            for hours in horizons
        },
    }
    for item in stats["coverage_by_horizon"].values():
        rows_n = item["rows"] or 1
        item["token_price_coverage_rate"] = item["token_price_available"] / rows_n
        item["yes_price_coverage_rate"] = item["yes_price_available"] / rows_n
    return out_rows, stats


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


def write_csv(path: Path, rows_out: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows_out:
            writer.writerow({field: row.get(field) for field in FIELDNAMES})


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
    stats = report["preview_stats"]
    lines = [
        "# April Historical Opportunity Preview v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> git_sha: `{report['git_sha']}`",
        "> Scope: fact-like preview only; no raw-file strategy PnL; no N100/live config changed.",
        "",
        "## 数据快照",
        "",
        "- 策略结论授权源仍是 `runtime/weather.db.fact_signal_candidates` / `fact_trades`。",
        "- 本 preview 不写入 DB，不计算 ROI，不填 live action。",
        f"- 输出 CSV：`{report['out_csv']}`。",
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
        "## Preview 覆盖",
        "",
        table(
            ["metric", "value"],
            [
                ["April gamma city-days", stats["gamma"]["city_days"]],
                ["April gamma markets", stats["gamma"]["markets"]],
                ["April gamma tokens", stats["gamma"]["tokens"]],
                ["CLOB token files loaded", stats["clob_token_files_loaded"]],
                ["Preview rows", stats["preview_rows"]],
            ],
        ),
        "",
        "## Decision Price 覆盖",
        "",
        table(
            ["hours_to_noon_utc", "rows", "entry token coverage", "YES price coverage"],
            [
                [
                    hours,
                    item["rows"],
                    f"{item['token_price_coverage_rate']:.1%}",
                    f"{item['yes_price_coverage_rate']:.1%}",
                ]
                for hours, item in stats["coverage_by_horizon"].items()
            ],
        ),
        "",
        "## 人话结论",
        "",
        "- April 可以生成 fact-like opportunity preview：city/date/bracket/condition/token/side/decision price 都能落表。",
        "- 这一步解决的是“交易机会分母和历史价格”问题，不解决最终策略验证。",
        "- 仍缺 `model_p_yes`、`final_yes`、orderbook depth，所以不能从这个 preview 直接算 ROI 或 live-test。",
        "- 下一步应先补 forecast probability join 和 settlement join，再把 preview 升级成真正的 historical `fact_signal_candidates` backfill。",
        "",
        "## Schema 缺口",
        "",
        "- `model_p_yes=NULL`：还没接历史 forecast probability engine。",
        "- `final_yes=NULL`：April settlement 未进入 `settlements`/pm_history city-day fact。",
        "- `orderbook_depth_available=0`：CLOB history 是价格序列，不是盘口深度。",
        "",
        "## Sample Rows",
        "",
        "```json",
        json.dumps(report["sample_rows"], ensure_ascii=False, indent=2),
        "```",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    horizons = [int(x.strip()) for x in args.horizons.split(",") if x.strip()]
    conn = connect(args.db_path)
    preview_rows, preview_stats = build_preview_rows(args.start_date, args.end_date, horizons)
    write_csv(Path(args.out_csv), preview_rows)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "window": {"start_date": args.start_date, "end_date": args.end_date},
        "horizons": horizons,
        "out_csv": str(Path(args.out_csv)),
        "db": summarize_db(conn, Path(args.db_path)),
        "preview_stats": preview_stats,
        "sample_rows": preview_rows[:8],
        "verdict": {
            "can_use_for_strategy_roi": False,
            "can_use_as_backfill_input": True,
            "reason": "Missing model_p_yes, final_yes, and orderbook depth; preview rows must be upgraded into fact_signal_candidates before strategy gates.",
        },
    }
    write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(args.out_csv)
    print(args.out_json)
    print(args.out_md)


if __name__ == "__main__":
    main()
