#!/usr/bin/env python3
"""Attribute recent live weather strategy losses from canonical fact tables."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


CORE9 = {"Boston", "LA", "London", "Miami", "NYC", "Phoenix", "Shanghai", "Tokyo", "Warsaw"}
NEW_T1_2026_05_26 = {"Ankara", "Guangzhou", "Istanbul", "Jeddah", "Karachi", "Lucknow", "Moscow", "Seattle"}
NEW_T1_2026_05_27 = {"Amsterdam", "BuenosAires", "Chengdu", "Manila", "Munich", "Singapore"}
DEMOTED_OR_WATCH = {"Amsterdam", "Austin", "Beijing", "BuenosAires", "Chicago", "Paris"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="runtime/weather.db")
    parser.add_argument("--start-date", default=None, help="target_date start, inclusive")
    parser.add_argument("--end-date", default=None, help="target_date end, inclusive")
    parser.add_argument(
        "--json-out",
        default="docs/analysis/2026-06/2026-06-06-recent-live-loss-attribution.json",
    )
    parser.add_argument(
        "--md-out",
        default="docs/analysis/2026-06/2026-06-06-recent-live-loss-attribution.md",
    )
    return parser.parse_args()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> Any:
    return conn.execute(sql, tuple(params)).fetchone()[0]


def cohort(city: str | None) -> str:
    if city in CORE9:
        return "core9"
    if city in NEW_T1_2026_05_26:
        return "new_t1_2026_05_26"
    if city in NEW_T1_2026_05_27:
        return "new_t1_2026_05_27"
    if city in DEMOTED_OR_WATCH:
        return "demoted_or_watch"
    return "other"


def f2(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.2f}"


def pct(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value) * 100:.1f}%"


def safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den in (None, 0):
        return None
    return num / den


def add_roi(row: dict[str, Any], pnl_key: str = "realized_pnl", cost_key: str = "settled_cost") -> dict[str, Any]:
    row["roi"] = safe_div(row.get(pnl_key), row.get(cost_key))
    return row


def group_realized(conn: sqlite3.Connection, start: str, end: str, fields: list[str]) -> list[dict[str, Any]]:
    select_fields = ", ".join(fields)
    group_fields = ", ".join(fields)
    data = rows(
        conn,
        f"""
        SELECT {select_fields},
               COUNT(*) AS settled_fills,
               COUNT(DISTINCT target_date) AS active_days,
               SUM(cost_usd) AS settled_cost,
               SUM(pnl_usd_at_fill) AS realized_pnl,
               AVG(CAST(win_by_count AS REAL)) AS win_rate,
               AVG(fill_price) AS avg_fill_price
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND target_date BETWEEN ? AND ?
        GROUP BY {group_fields}
        ORDER BY realized_pnl ASC
        """,
        (start, end),
    )
    for row in data:
        add_roi(row)
        if "city" in row:
            row["cohort"] = cohort(row["city"])
    return data


def group_open(conn: sqlite3.Connection, start: str, end: str, fields: list[str]) -> list[dict[str, Any]]:
    select_fields = ", ".join(fields)
    group_fields = ", ".join(fields)
    data = rows(
        conn,
        f"""
        SELECT {select_fields},
               COUNT(*) AS open_fills,
               COUNT(DISTINCT target_date) AS active_days,
               SUM(cost_usd) AS open_cost,
               SUM(unrealized_pnl_mid) AS open_mtm_mid,
               MAX(val_snapshot_ts_utc) AS val_snapshot_ts_utc
        FROM fact_trades
        WHERE trade_class='live_real'
          AND (settlement_status IS NULL OR settlement_status='unsettled')
          AND target_date BETWEEN ? AND ?
        GROUP BY {group_fields}
        ORDER BY open_mtm_mid ASC
        """,
        (start, end),
    )
    for row in data:
        if "city" in row:
            row["cohort"] = cohort(row["city"])
    return data


def join_city_realized_open(realized: list[dict[str, Any]], open_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in realized:
        item = out.setdefault(row["city"], {"city": row["city"], "cohort": row["cohort"]})
        item.update({f"settled_{k}": row.get(k) for k in ["settled_fills", "active_days", "settled_cost", "realized_pnl", "roi", "win_rate"]})
    for row in open_rows:
        item = out.setdefault(row["city"], {"city": row["city"], "cohort": row["cohort"]})
        item.update({f"open_{k}": row.get(k) for k in ["open_fills", "active_days", "open_cost", "open_mtm_mid", "val_snapshot_ts_utc"]})
    data = list(out.values())
    data.sort(key=lambda row: (row.get("settled_realized_pnl") or 0) + (row.get("open_open_mtm_mid") or 0))
    return data


def table(headers: list[str], body: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def render(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 最近一周 live_real 亏损归因")
    lines.append("")
    lines.append("## 数据快照")
    lines.append("")
    snap = payload["snapshot"]
    lines.append(
        table(
            ["字段", "值"],
            [
                ["数据源", f"`{snap['db_path']}` 的 `fact_trades` / `fact_signal_candidates`"],
                ["生成时间", snap["generated_at"]],
                ["target_date 窗口", f"{snap['start_date']}..{snap['end_date']}"],
                ["fact_built_at", str(payload["integrity_checks"]["max_fact_built_at"][0][0])],
                ["live_real rows in window", str(snap["live_real_rows"])],
                ["settled rows", str(snap["settled_rows"])],
                ["open/null rows", str(snap["open_rows"])],
                ["missing_bracket rows", str(snap["missing_bracket_rows"])],
                ["open valuation max ts", str(snap["max_val_snapshot_ts_utc"])],
            ],
        )
    )
    lines.append("")
    lines.append("## 强制自检")
    lines.append("")
    lines.append("```text")
    for key, value in payload["integrity_checks"].items():
        lines.append(f"{key}: {value}")
    lines.append("```")
    lines.append("")
    s = payload["summary"]
    lines.append("## 一句话结论")
    lines.append("")
    lines.append(
        f"按 `target_date={snap['start_date']}..{snap['end_date']}`，最近一周已结算 live_real 是 "
        f"`{f2(s['settled_realized_pnl'])}`，settled cost `{f2(s['settled_cost'])}`，ROI `{pct(s['settled_roi'])}`；"
        f"未结算 open cost `{f2(s['open_cost'])}`，按最新 mid 估值 MTM `{f2(s['open_mtm_mid'])}`。"
    )
    lines.append("")
    lines.append(
        "亏损主因不是一个抽象的“天气模型整体坏了”，而是 **新增/扩池城市 + 特定 side + V2/YES 兑现差**。"
        "core9 在这个窗口仍然赚钱；新增两批城市合计贡献了主要 realized 亏损。"
    )
    lines.append("")
    lines.append("## 按 cohort")
    lines.append("")
    lines.append(
        table(
            ["cohort", "settled fills", "settled pnl", "ROI", "open cost", "open MTM"],
            [
                [
                    row["cohort"],
                    str(row.get("settled_fills") or ""),
                    f2(row.get("settled_cost")),
                    f"{f2(row.get('realized_pnl'))} / {pct(row.get('roi'))}",
                    f2(row.get("open_cost")),
                    f2(row.get("open_mtm_mid")),
                ]
                for row in payload["by_cohort"]
            ],
        )
    )
    lines.append("")
    lines.append("## 按城市：realized + open")
    lines.append("")
    lines.append(
        table(
            ["city", "cohort", "settled pnl/ROI", "settled fills", "open cost", "open MTM", "判断"],
            [
                [
                    row["city"],
                    row["cohort"],
                    f"{f2(row.get('settled_realized_pnl'))} / {pct(row.get('settled_roi'))}",
                    str(row.get("settled_settled_fills") or ""),
                    f2(row.get("open_open_cost")),
                    f2(row.get("open_open_mtm_mid")),
                    city_read(row),
                ]
                for row in payload["city_attribution"][:24]
            ],
        )
    )
    lines.append("")
    lines.append("## 按 side")
    lines.append("")
    lines.append(
        table(
            ["side", "settled fills", "settled pnl/ROI", "win", "avg fill"],
            [
                [
                    row["side"],
                    str(row["settled_fills"]),
                    f"{f2(row['realized_pnl'])} / {pct(row['roi'])}",
                    pct(row["win_rate"]),
                    f2(row["avg_fill_price"]),
                ]
                for row in payload["by_side"]
            ],
        )
    )
    lines.append("")
    lines.append("## 按 strategy_id")
    lines.append("")
    lines.append(
        table(
            ["strategy_id", "fills", "pnl/ROI", "win", "avg fill"],
            [
                [
                    row["strategy_id"],
                    str(row["settled_fills"]),
                    f"{f2(row['realized_pnl'])} / {pct(row['roi'])}",
                    pct(row["win_rate"]),
                    f2(row["avg_fill_price"]),
                ]
                for row in payload["by_strategy_id"]
            ],
        )
    )
    lines.append("")
    lines.append("## 最差 city×side")
    lines.append("")
    lines.append(
        table(
            ["city", "side", "cohort", "fills", "pnl/ROI", "win"],
            [
                [
                    row["city"],
                    row["side"],
                    row["cohort"],
                    str(row["settled_fills"]),
                    f"{f2(row['realized_pnl'])} / {pct(row['roi'])}",
                    pct(row["win_rate"]),
                ]
                for row in payload["by_city_side"][:18]
            ],
        )
    )
    lines.append("")
    lines.append("## 交易动作")
    lines.append("")
    lines.append("1. 不要把最近一周亏损归因成“所有城市都不行”：core9 仍然正，扩池城市明显拖累。")
    lines.append("2. 新增城市不要继续同权 live；先按 `city×side` 降到 shadow/low size，尤其是报告表里的负 `city×side`。")
    lines.append("3. V2/YES 相关亏损需要继续单独复盘；短期不应用 V2 或新城市池做扩大。")
    lines.append("4. 这不是钱包现金流报告；如果要解释 USDC 余额少了多少，要再跑 `weather_live_account_reconcile.py --date-field fill_date_bj`。")
    lines.append("")
    return "\n".join(lines) + "\n"


def city_read(row: dict[str, Any]) -> str:
    pnl = row.get("settled_realized_pnl") or 0
    open_mtm = row.get("open_open_mtm_mid") or 0
    cohort_name = row.get("cohort")
    if pnl < -10 and cohort_name.startswith("new_t1"):
        return "主要扩池亏损"
    if pnl < -10:
        return "主要亏损城市"
    if pnl > 10 and cohort_name == "core9":
        return "core9 正贡献"
    if open_mtm < -5:
        return "未结算风险偏负"
    return "观察"


def main() -> None:
    args = parse_args()
    conn = connect(args.db_path)

    max_date_text = scalar(conn, "SELECT MAX(target_date) FROM fact_trades WHERE trade_class='live_real'")
    end = args.end_date or max_date_text
    start = args.start_date or (date.fromisoformat(end) - timedelta(days=6)).isoformat()

    integrity_checks = {
        "max_fact_built_at": [tuple(row) for row in conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades")],
        "trade_class_distribution": [
            tuple(row)
            for row in conn.execute("SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class ORDER BY trade_class")
        ],
        "settlement_status_distribution": [
            tuple(row)
            for row in conn.execute(
                "SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status"
            )
        ],
        "signal_candidate_coverage": [
            tuple(row)
            for row in conn.execute(
                "SELECT COUNT(*), SUM(eligible), SUM(paper_ordered), SUM(live_filled) FROM fact_signal_candidates"
            )
        ],
        "clob_order_fill_join": [
            tuple(row)
            for row in conn.execute(
                """
                SELECT o.status, COUNT(*) orders,
                       SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill
                FROM orders o LEFT JOIN fills f USING(execution_id)
                WHERE o.venue='polymarket_clob'
                GROUP BY o.status ORDER BY o.status
                """
            )
        ],
    }

    summary = rows(
        conn,
        """
        SELECT
          SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS settled_realized_pnl,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status='unsettled' THEN cost_usd ELSE 0 END) AS open_cost,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status='unsettled' THEN unrealized_pnl_mid ELSE 0 END) AS open_mtm_mid,
          COUNT(*) AS live_real_rows,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_rows,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status='unsettled' THEN 1 ELSE 0 END) AS open_rows,
          SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) AS missing_bracket_rows,
          MAX(val_snapshot_ts_utc) AS max_val_snapshot_ts_utc
        FROM fact_trades
        WHERE trade_class='live_real' AND target_date BETWEEN ? AND ?
        """,
        (start, end),
    )[0]
    summary["settled_roi"] = safe_div(summary["settled_realized_pnl"], summary["settled_cost"])

    by_cohort: dict[str, dict[str, Any]] = {}
    for row in group_realized(conn, start, end, ["city"]):
        bucket = by_cohort.setdefault(
            row["cohort"],
            {"cohort": row["cohort"], "settled_fills": 0, "settled_cost": 0.0, "realized_pnl": 0.0, "win_weight": 0.0},
        )
        bucket["settled_fills"] += row["settled_fills"] or 0
        bucket["settled_cost"] += row["settled_cost"] or 0.0
        bucket["realized_pnl"] += row["realized_pnl"] or 0.0
        bucket["win_weight"] += (row["win_rate"] or 0.0) * (row["settled_fills"] or 0)
    for row in group_open(conn, start, end, ["city"]):
        bucket = by_cohort.setdefault(row["cohort"], {"cohort": row["cohort"]})
        bucket["open_cost"] = bucket.get("open_cost", 0.0) + (row["open_cost"] or 0.0)
        bucket["open_mtm_mid"] = bucket.get("open_mtm_mid", 0.0) + (row["open_mtm_mid"] or 0.0)
    for bucket in by_cohort.values():
        bucket["roi"] = safe_div(bucket.get("realized_pnl"), bucket.get("settled_cost"))
        bucket["win_rate"] = safe_div(bucket.get("win_weight"), bucket.get("settled_fills"))
        bucket.pop("win_weight", None)

    city_realized = group_realized(conn, start, end, ["city"])
    city_open = group_open(conn, start, end, ["city"])
    payload = {
        "snapshot": {
            "db_path": args.db_path,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "start_date": start,
            "end_date": end,
            **{key: summary[key] for key in ["live_real_rows", "settled_rows", "open_rows", "missing_bracket_rows", "max_val_snapshot_ts_utc"]},
        },
        "integrity_checks": integrity_checks,
        "summary": summary,
        "by_cohort": sorted(by_cohort.values(), key=lambda row: row.get("realized_pnl") or 0),
        "city_attribution": join_city_realized_open(city_realized, city_open),
        "by_side": group_realized(conn, start, end, ["side"]),
        "by_strategy_id": group_realized(conn, start, end, ["strategy_id"]),
        "by_execution_policy": group_realized(conn, start, end, ["execution_policy"]),
        "by_city_side": group_realized(conn, start, end, ["city", "side"]),
        "open_by_city": city_open,
        "open_by_strategy_id": group_open(conn, start, end, ["strategy_id"]),
    }

    json_out = Path(args.json_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_out = Path(args.md_out)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.write_text(render(payload), encoding="utf-8")
    print(f"wrote {json_out}")
    print(f"wrote {md_out}")


if __name__ == "__main__":
    main()
