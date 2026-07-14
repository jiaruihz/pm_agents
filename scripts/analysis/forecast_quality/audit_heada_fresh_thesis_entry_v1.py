#!/usr/bin/env python3
"""Audit HeadA entry orders against the latest data-feed thesis available as-of order time."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.low_price_yes_tail_telemetry import parse_bracket_bounds
from weather_data_feed.city_calendar import city_timezone_name

INSTANCE = "low_price_yes_lottery_tiny_live_v1"
ENTRY_POLICY = "low_price_yes_lottery_maker_first_v1"


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def number(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def utc(value: Any) -> datetime | None:
    raw = text(value)
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        out = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def snapshot_clock(path: Path) -> datetime:
    stamp = path.stem.removeprefix("snapshot_")
    local = datetime.strptime(stamp, "%Y%m%d_%H%M").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return local.astimezone(timezone.utc)


def load_snapshot(path: Path) -> tuple[datetime | None, list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return utc(payload.get("ts_utc")), [r for r in payload.get("records", []) if isinstance(r, dict)]


def order_id(row: dict[str, Any]) -> str:
    place = row.get("exchange_response", {}).get("place", {}) if isinstance(row.get("exchange_response"), dict) else {}
    return text(row.get("order_id") or row.get("clob_order_id") or place.get("orderID") or place.get("order_id"))


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def lineage_by_key(db: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    with connect(db) as conn:
        rows = conn.execute(
            """
            SELECT city, target_date, bracket,
                   COUNT(*) AS fills,
                   SUM(cost_usd) AS fill_cost_usd,
                   SUM(CASE WHEN settled = 1 THEN pnl_usd_at_fill ELSE 0 END) AS settled_pnl_usd,
                   MAX(settled) AS settled,
                   MAX(contract_won) AS won
            FROM fact_trades
            WHERE instance_id = ?
            GROUP BY city, target_date, bracket
            """,
            (INSTANCE,),
        ).fetchall()
    return {(text(r["city"]), text(r["target_date"]), text(r["bracket"])): dict(r) for r in rows}


def old_decision_for(
    order: dict[str, Any], decisions: list[dict[str, Any]], placed: datetime
) -> dict[str, Any] | None:
    key = (text(order.get("city")), text(order.get("target_date")), text(order.get("bracket")))
    matches = []
    for row in decisions:
        if row.get("decision_status") != "planned":
            continue
        if (text(row.get("city")), text(row.get("target_date")), text(row.get("bracket"))) != key:
            continue
        created = utc(row.get("created_at_utc"))
        if created and created <= placed + timedelta(seconds=15):
            matches.append((created, row))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def classify_fresh(
    row: dict[str, Any] | None, snapshot_ts: datetime | None, placed: datetime, city: str
) -> tuple[bool, list[str], dict[str, Any]]:
    reasons: list[str] = []
    if row is None:
        return False, ["market_missing_in_asof_snapshot"], {}
    target = datetime.strptime(text(row.get("event_date")), "%Y-%m-%d").date()
    tz_name = city_timezone_name(city) or "UTC"
    local_date = snapshot_ts.astimezone(ZoneInfo(tz_name)).date() if snapshot_ts else placed.astimezone(ZoneInfo(tz_name)).date()
    if target != local_date + timedelta(days=1):
        reasons.append("not_city_local_d1")
    if text(row.get("probability_status")) != "ok":
        reasons.append("probability_not_ok")
    if text(row.get("side")) != "BUY_YES":
        reasons.append("side_not_buy_yes")
    ask = number(row.get("entry_price") or row.get("market_yes_price"))
    p_yes = number(row.get("model_prob"))
    edge = number(row.get("edge"))
    hours = number(row.get("hours_to_settle"), 0.0)
    low, _ = parse_bracket_bounds(row.get("bracket"))
    forecast = number(row.get("forecast_max_native"))
    dist = (low - forecast) if low is not None and math.isfinite(forecast) else math.nan
    age_min = (placed - snapshot_ts).total_seconds() / 60.0 if snapshot_ts else math.nan
    if not math.isfinite(age_min) or age_min > 30.0:
        reasons.append("asof_snapshot_too_stale")
    if not math.isfinite(ask) or not (0.05 <= ask <= 0.20):
        reasons.append("ask_outside_05_20")
    if not math.isfinite(edge) or edge < 0.20:
        reasons.append("edge_below_020")
    if not math.isfinite(dist) or dist <= 0:
        reasons.append("dist_le0_or_missing")
    if hours < 1.0:
        reasons.append("hours_to_settle_below_1")
    return not reasons, reasons, {
        "fresh_model_p_yes": p_yes,
        "fresh_snapshot_ask": ask,
        "fresh_edge": edge,
        "fresh_dist_native": dist,
        "asof_snapshot_age_min": age_min,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    orders = [
        row
        for row in read_jsonl(Path(args.orders))
        if text(row.get("record_type")) == "weather_edge_live_order"
        and text(row.get("status")) == "submitted"
        and text(row.get("execution_policy")) == ENTRY_POLICY
    ]
    decisions = read_jsonl(Path(args.decisions))
    snapshots = sorted(Path(args.snapshot_dir).glob("snapshot_*.json"), key=snapshot_clock)
    snapshot_times = [snapshot_clock(path) for path in snapshots]
    lineage = lineage_by_key(Path(args.db))
    details: list[dict[str, Any]] = []
    for order in orders:
        placed = utc(order.get("created_at_utc"))
        if placed is None:
            continue
        city = text(order.get("city"))
        target_date = text(order.get("target_date"))
        bracket = text(order.get("bracket"))
        tz_name = city_timezone_name(city) or "UTC"
        local = placed.astimezone(ZoneInfo(tz_name))
        target = datetime.strptime(target_date, "%Y-%m-%d").date()
        day_offset = (target - local.date()).days
        scope = "D-1" if day_offset == 1 else "D0" if day_offset == 0 else f"D{day_offset:+d}"
        old = old_decision_for(order, decisions, placed)
        old_ts = utc(old.get("decision_snapshot_ts_utc")) if old else None
        old_age = (placed - old_ts).total_seconds() / 60.0 if old_ts else math.nan
        idx = bisect.bisect_right(snapshot_times, placed) - 1
        asof_path = snapshots[idx] if idx >= 0 else None
        asof_ts: datetime | None = None
        fresh_row = None
        if asof_path is not None:
            asof_ts, records = load_snapshot(asof_path)
            while asof_ts is not None and asof_ts > placed and idx > 0:
                idx -= 1
                asof_path = snapshots[idx]
                asof_ts, records = load_snapshot(asof_path)
            fresh_row = next(
                (
                    row
                    for row in records
                    if text(row.get("city")) == city
                    and text(row.get("event_date")) == target_date
                    and text(row.get("bracket")) == bracket
                ),
                None,
            )
        qualifies, reasons, fresh = classify_fresh(fresh_row, asof_ts, placed, city)
        stale_old = math.isfinite(old_age) and old_age > 30.0
        scope_leak = scope != "D-1"
        stale_caused = stale_old and not qualifies and not scope_leak
        trade = lineage.get((city, target_date, bracket), {})
        details.append(
            {
                "placed_at_utc": placed.isoformat().replace("+00:00", "Z"),
                "placed_at_local": local.isoformat(),
                "local_hour": local.hour,
                "entry_scope": scope,
                "city": city,
                "target_date": target_date,
                "bracket": bracket,
                "order_id": order_id(order),
                "old_model_p_yes": number(old.get("model_p_yes") if old else order.get("model_p_yes_used")),
                "old_decision_snapshot_ts_utc": old_ts.isoformat().replace("+00:00", "Z") if old_ts else "",
                "old_decision_age_min": old_age,
                "old_thesis_stale_gt30m": stale_old,
                "asof_snapshot_path": str(asof_path) if asof_path else "",
                "asof_snapshot_ts_utc": asof_ts.isoformat().replace("+00:00", "Z") if asof_ts else "",
                "fresh_signal_qualifies": qualifies,
                "fresh_block_reasons": ";".join(reasons),
                "stale_thesis_caused_order": stale_caused,
                "d0_scope_leak": scope_leak,
                **fresh,
                "filled": int(number(trade.get("fills"), 0.0) > 0),
                "settled": int(number(trade.get("settled"), 0.0) > 0),
                "won": int(number(trade.get("won"), 0.0) > 0),
                "fill_cost_usd": number(trade.get("fill_cost_usd"), 0.0),
                "settled_pnl_usd": number(trade.get("settled_pnl_usd"), 0.0),
            }
        )

    scope_counts = Counter(row["entry_scope"] for row in details)
    local_hour_counts = Counter(row["local_hour"] for row in details if row["entry_scope"] == "D0")
    summary = {
        "orders_submitted": len(details),
        "snapshot_archive_start": snapshot_times[0].isoformat().replace("+00:00", "Z") if snapshot_times else "",
        "snapshot_archive_end": snapshot_times[-1].isoformat().replace("+00:00", "Z") if snapshot_times else "",
        "old_thesis_stale_gt30m_submitted": sum(row["old_thesis_stale_gt30m"] for row in details),
        "stale_thesis_caused_submitted": sum(row["stale_thesis_caused_order"] for row in details),
        "stale_thesis_caused_filled": sum(row["stale_thesis_caused_order"] and row["filled"] for row in details),
        "stale_thesis_caused_winners": sum(row["stale_thesis_caused_order"] and row["won"] for row in details),
        "stale_thesis_caused_settled_pnl_usd": sum(
            row["settled_pnl_usd"] for row in details if row["stale_thesis_caused_order"]
        ),
        "d0_scope_leak_submitted": sum(row["d0_scope_leak"] for row in details),
        "d0_scope_leak_filled": sum(row["d0_scope_leak"] and row["filled"] for row in details),
        "d0_scope_leak_winners": sum(row["d0_scope_leak"] and row["won"] for row in details),
        "d0_scope_leak_settled_pnl_usd": sum(row["settled_pnl_usd"] for row in details if row["d0_scope_leak"]),
        "d1_submitted": sum(not row["d0_scope_leak"] for row in details),
        "d1_filled": sum(not row["d0_scope_leak"] and row["filled"] for row in details),
        "d1_winners": sum(not row["d0_scope_leak"] and row["won"] for row in details),
        "d1_settled_pnl_usd": sum(row["settled_pnl_usd"] for row in details if not row["d0_scope_leak"]),
        "fresh_signal_would_qualify": sum(row["fresh_signal_qualifies"] for row in details),
        "repaired_policy_invalid_submitted": sum(not row["fresh_signal_qualifies"] for row in details),
        "repaired_policy_invalid_filled": sum(not row["fresh_signal_qualifies"] and row["filled"] for row in details),
        "repaired_policy_invalid_winners": sum(not row["fresh_signal_qualifies"] and row["won"] for row in details),
        "repaired_policy_invalid_settled_pnl_usd": sum(
            row["settled_pnl_usd"] for row in details if not row["fresh_signal_qualifies"]
        ),
        "scope_counts": dict(sorted(scope_counts.items())),
        "d0_local_hour_counts": {str(k): v for k, v in sorted(local_hour_counts.items())},
        "d0_at_or_after_13_local": sum(row["d0_scope_leak"] and row["local_hour"] >= 13 for row in details),
    }
    return {"summary": summary, "details": details}


def write_outputs(payload: dict[str, Any], args: argparse.Namespace) -> None:
    out_json = Path(args.out_json)
    out_csv = Path(args.out_csv)
    out_md = Path(args.out_md)
    for path in (out_json, out_csv, out_md):
        path.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    rows = payload["details"]
    with out_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [], lineterminator="\n")
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    s = payload["summary"]
    stale_rows = [r for r in rows if r["stale_thesis_caused_order"]]
    d0_rows = [r for r in rows if r["d0_scope_leak"]]
    def table(items: list[dict[str, Any]]) -> str:
        lines = ["| placed local | city | target | bracket | old age min | fresh p | fresh edge | reasons | filled | won |", "|---|---|---|---|---:|---:|---:|---|---:|---:|"]
        for r in items:
            lines.append(
                f"| {r['placed_at_local']} | {r['city']} | {r['target_date']} | {r['bracket']} | "
                f"{r['old_decision_age_min']:.1f} | {r.get('fresh_model_p_yes', math.nan):.3f} | "
                f"{r.get('fresh_edge', math.nan):.3f} | {r['fresh_block_reasons']} | {r['filled']} | {r['won']} |"
            )
        return "\n".join(lines)
    report = f"""# HeadA Fresh-Thesis Entry Audit v1

## 结论

- 实际首次提交 `{s['orders_submitted']}` 笔；其中旧 decision 超过 30 分钟 `{s['old_thesis_stale_gt30m_submitted']}` 笔。
- 严格 as-of 重放后，因 stale thesis 才会提交 `{s['stale_thesis_caused_submitted']}` 笔，其中进入成交链 `{s['stale_thesis_caused_filled']}` 笔、赢家 `{s['stale_thesis_caused_winners']}` 笔、settled PnL `${s['stale_thesis_caused_settled_pnl_usd']:.3f}`。
- 不属于 D-1 HeadA 范围的 D0 首次提交 `{s['d0_scope_leak_submitted']}` 笔，其中进入成交链 `{s['d0_scope_leak_filled']}` 笔、赢家 `{s['d0_scope_leak_winners']}` 笔、settled PnL `${s['d0_scope_leak_settled_pnl_usd']:.3f}`；当地 13:00 后 `{s['d0_at_or_after_13_local']}` 笔。
- 真正 D-1 是 `{s['d1_submitted']}` 笔 submitted / `{s['d1_filled']}` 笔进入成交链 / `{s['d1_winners']}` 笔赢家，settled PnL `${s['d1_settled_pnl_usd']:.3f}`。
- 合并 fresh thesis + D-1 两项修复后，历史会挡掉 `{s['repaired_policy_invalid_submitted']}` 笔 submitted / `{s['repaired_policy_invalid_filled']}` 笔成交链；其中有 `{s['repaired_policy_invalid_winners']}` 笔赢家，故不能把“挡掉的历史 PnL”当作策略增益，修复依据是时点一致性和策略分母一致性。
- 正确口径是城市当地 D-1；D0 动态天气属于 METAR/reversal 线，不应由 HeadA 首次开仓。

## 时间分布

- scope: `{json.dumps(s['scope_counts'], ensure_ascii=False)}`
- D0 local hour: `{json.dumps(s['d0_local_hour_counts'], ensure_ascii=False)}`
- snapshot archive: `{s['snapshot_archive_start']} .. {s['snapshot_archive_end']}`

## Stale Thesis 逐笔

{table(stale_rows)}

## D0 Scope Leak 逐笔

{table(d0_rows)}

## 口径

实际订单来自 live order journal 的首次 `maker_first` submitted 行；成交/结算来自 canonical `fact_trades`，按 city-target-bracket 成交链聚合。反事实只使用 `snapshot_ts <= order_ts` 的最新标准 data-feed snapshot，freshness 上限 30 分钟，规则为 D-1、BUY_YES、ask 5-20c、edge >=20pp、dist>0、至少 1 小时到结算。该报告是事故影响审计，不是策略绩效确认。

## 修复

live 首次入场改为直接读取最新标准 data-feed snapshot；缺失或超过 30 分钟显式失败，不回落 canonical 历史候选。maker lifecycle 在改价或 taker fallback 前重取同 snapshot 的概率、方向、dist 和 fee edge；thesis 失效时只撤单。所有新订单持久化 `decision_snapshot_ts_utc` 和 `source_snapshot_path`。HeadA 首次入场限定城市当地 D-1，目标日动态天气留给 METAR/reversal family。
"""
    out_md.write_text(report, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", default=str(ROOT / "runtime/weather_edge_v1/live/low_price_yes_lottery_tiny_live_v1_orders.jsonl"))
    parser.add_argument("--decisions", default=str(ROOT / "runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/shadow_decisions.jsonl"))
    parser.add_argument("--snapshot-dir", default="/Volumes/jrs/weather_data_feed_service_runtime/targeted_output/paper_snapshots")
    parser.add_argument("--db", default=str(ROOT / "runtime/weather.db"))
    base = ROOT / "docs/analysis/2026-07/generated/heada_fresh_thesis_entry_audit_v1"
    parser.add_argument("--out-json", default=str(base / "audit.json"))
    parser.add_argument("--out-csv", default=str(base / "orders.csv"))
    parser.add_argument("--out-md", default=str(ROOT / "docs/analysis/2026-07/2026-07-14-heada-fresh-thesis-entry-audit-v1.md"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = run(args)
    write_outputs(payload, args)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
