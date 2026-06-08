#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime" / "weather_edge_v1"
SNAPSHOT_DIR = RUNTIME / "market_data" / "paper_snapshots"
PM_HISTORY_DIR = RUNTIME / "market_data" / "cache" / "pm_history"
DB_PATH = ROOT / "runtime" / "weather.db"


@dataclass(frozen=True)
class Trade:
    snapshot: str
    target_date: str
    city: str
    current_pool: str
    model: str
    side: str
    bracket: str
    market_key: str
    entry_price: float
    shares: float
    win: bool
    pnl_usd: float
    cost_usd: float


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _latest_snapshot() -> Path:
    files = sorted(SNAPSHOT_DIR.glob("snapshot_*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit("No paper snapshots found")
    return files[-1]


def _current_city_pools() -> dict[str, str]:
    payload = _load_json(_latest_snapshot())
    pools: dict[str, str] = {}
    for city in payload.get("trading_t1_cities") or []:
        pools[str(city)] = "current_t1"
    for city in payload.get("research_t2_cities") or []:
        pools[str(city)] = "current_t2"
    if not pools:
        raw = payload.get("city_pools")
        if isinstance(raw, dict):
            pools = {str(city): str(pool).replace("t1_trading", "current_t1").replace("t2_research", "current_t2") for city, pool in raw.items()}
    return pools


def _market_key(record: dict[str, Any]) -> str:
    side = str(record.get("side") or "")
    market_id = str(record.get("market_id") or "").strip()
    condition_id = str(record.get("condition_id") or "").strip()
    if market_id:
        return f"market_id:{market_id}|{side}"
    if condition_id:
        return f"condition_id:{condition_id}|{side}"
    return "|".join(
        [
            "city_bracket",
            str(record.get("city") or ""),
            str(record.get("event_date") or ""),
            str(record.get("bracket") or ""),
            side,
        ]
    )


def _qualifies(record: dict[str, Any], city_pools: dict[str, str]) -> bool:
    city = str(record.get("city") or "")
    if city not in city_pools:
        return False
    side = str(record.get("side") or "")
    if side not in {"BUY_YES", "BUY_NO"}:
        return False
    hours = _safe_float(record.get("hours_to_settle"), math.nan)
    if math.isnan(hours) or not (22.0 <= hours <= 28.0):
        return False
    edge = abs(_safe_float(record.get("abs_edge"), _safe_float(record.get("edge"), 0.0)))
    entry_price = _safe_float(record.get("entry_price"), 0.0)
    return edge >= 0.10 and 0.25 <= entry_price < 0.75


def _winner_brackets(city: str, target_date: str) -> set[str] | None:
    path = PM_HISTORY_DIR / f"{city}_{target_date}.json"
    if not path.exists():
        return None
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return None
    winners: set[str] = set()
    for bracket in payload.get("brackets", []):
        if not isinstance(bracket, dict):
            continue
        if _safe_float(bracket.get("final_price"), -1.0) == 1.0:
            winners.add(str(bracket.get("label") or bracket.get("outcome") or "").strip())
    return winners or None


def _settle(snapshot: Path, record: dict[str, Any], city_pools: dict[str, str], shares: float) -> Trade | None:
    city = str(record.get("city") or "")
    target_date = str(record.get("event_date") or "")
    bracket = str(record.get("bracket") or "")
    side = str(record.get("side") or "")
    winners = _winner_brackets(city, target_date)
    if winners is None:
        return None
    bracket_hit = bracket in winners
    if side == "BUY_YES":
        win = bracket_hit
    elif side == "BUY_NO":
        win = not bracket_hit
    else:
        return None
    entry_price = _safe_float(record.get("entry_price"), 0.0)
    payout = 1.0 if win else 0.0
    return Trade(
        snapshot=snapshot.name,
        target_date=target_date,
        city=city,
        current_pool=city_pools[city],
        model=str(record.get("model") or record.get("forecast_source") or ""),
        side=side,
        bracket=bracket,
        market_key=_market_key(record),
        entry_price=entry_price,
        shares=shares,
        win=win,
        pnl_usd=(payout - entry_price) * shares,
        cost_usd=entry_price * shares,
    )


def _collect(city_pools: dict[str, str], shares: float) -> tuple[list[Trade], int, int]:
    candidates: dict[str, tuple[Path, dict[str, Any]]] = {}
    raw_candidates = 0
    for snapshot in sorted(SNAPSHOT_DIR.glob("snapshot_*.json")):
        payload = _load_json(snapshot)
        records = payload.get("records", []) if isinstance(payload, dict) else []
        for record in records:
            if not isinstance(record, dict) or not _qualifies(record, city_pools):
                continue
            raw_candidates += 1
            key = _market_key(record)
            if key not in candidates:
                candidates[key] = (snapshot, record)
    trades: list[Trade] = []
    unsettled = 0
    for snapshot, record in candidates.values():
        trade = _settle(snapshot, record, city_pools, shares)
        if trade is None:
            unsettled += 1
        else:
            trades.append(trade)
    return trades, raw_candidates, unsettled


def _summarize(trades: list[Trade]) -> dict[str, Any]:
    cost = sum(t.cost_usd for t in trades)
    pnl = sum(t.pnl_usd for t in trades)
    wins = sum(1 for t in trades if t.win)
    return {
        "n": len(trades),
        "wins": wins,
        "win_rate": wins / len(trades) if trades else 0.0,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else 0.0,
    }


def _group(trades: list[Trade], key_fn) -> list[tuple[str, dict[str, Any]]]:
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        buckets[str(key_fn(trade))].append(trade)
    return sorted((name, _summarize(rows)) for name, rows in buckets.items())


def _fmt_usd(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_pct(value: float) -> str:
    return f"{value:.1%}"


def _row(name: str, s: dict[str, Any]) -> str:
    return f"| {name} | {s['n']} | {s['wins']} | {_fmt_pct(s['win_rate'])} | {_fmt_usd(s['cost_usd'])} | {_fmt_usd(s['pnl_usd'])} | {_fmt_pct(s['roi'])} |"


def _db_note() -> str:
    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
        return "DB unavailable: weather.db missing_or_empty"
    try:
        conn = sqlite3.connect(DB_PATH)
        fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
        conn.close()
    except Exception as exc:
        return f"DB unavailable: {type(exc).__name__}: {exc}"
    return f"DB available with fills={fills}, but city candidate replay requires snapshot universe"


def _write_report(out_path: Path, trades: list[Trade], city_pools: dict[str, str], raw_candidates: int, unsettled: int) -> None:
    latest = _latest_snapshot()
    snapshot_ts = datetime.fromtimestamp(latest.stat().st_mtime).isoformat(timespec="seconds")
    overall = _summarize(trades)
    by_pool = dict(_group(trades, lambda t: t.current_pool))
    current_t1 = [t for t in trades if t.current_pool == "current_t1"]
    current_t2 = [t for t in trades if t.current_pool == "current_t2"]
    city_rows = _group(trades, lambda t: t.city)
    t2_good = [(name, s) for name, s in city_rows if city_pools.get(name) == "current_t2" and s["n"] >= 5 and s["pnl_usd"] > 0 and s["roi"] >= 0.10]
    t1_bad = [(name, s) for name, s in city_rows if city_pools.get(name) == "current_t1" and s["n"] >= 5 and (s["pnl_usd"] < 0 or s["roi"] < 0.05)]
    t2_good = sorted(t2_good, key=lambda item: item[1]["pnl_usd"], reverse=True)
    t1_bad = sorted(t1_bad, key=lambda item: item[1]["pnl_usd"])

    lines = [
        "# 绩效分析：current T1/T2 city contribution replay",
        "",
        "> 时间窗：全部已结算镜像样本（北京时间 target_date）",
        "> 策略：snapshot replay / live-like wide capture",
        "> 城市池：current_t1 vs current_t2",
        "> 数据源：镜像 JSON/pm_history（DB/API 无法回答未执行候选 replay）",
        "",
        "## 数据快照",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        "| 数据源路径 | runtime/weather_edge_v1/market_data/paper_snapshots + cache/pm_history |",
        f"| 数据快照时间 | {snapshot_ts} |",
        f"| replay trades 行数 | {overall['n']} |",
        f"| raw candidate rows | {raw_candidates} |",
        f"| unsettled 占比 | {unsettled} / {overall['n'] + unsettled} |",
        "| missing_bracket 数 | N/A（pm_history 缺失/未结算计入 unsettled） |",
        f"| DB/API 降级原因 | {_db_note()}; API 无 counterfactual candidate replay endpoint |",
        "",
        "## 分析口径",
        "",
        "- 当前 T1/T2 归属取最新 snapshot 的 `trading_t1_cities` / `research_t2_cities`。",
        "- 信号规则：`22 <= hours_to_settle <= 28`，`abs(edge) >= 0.10`，`0.25 <= entry_price < 0.75`。",
        "- 去重：每个 `market_id/condition_id + side` 只保留第一次满足条件的候选。",
        "- PnL：10 shares replay；未结算不计入已结算 PnL。",
        "",
        "## 总览",
        "",
        "| group | trades | wins | win_rate | cost_usd | pnl_usd | roi |",
        "|---|---|---|---|---|---|---|",
        _row("all", overall),
        _row("current_t1", by_pool.get("current_t1", _summarize([]))),
        _row("current_t2", by_pool.get("current_t2", _summarize([]))),
        "",
        "## 当前 T2 里表现较好的城市（n>=5, pnl>0, roi>=10%）",
        "",
        "| city | trades | wins | win_rate | cost_usd | pnl_usd | roi |",
        "|---|---|---|---|---|---|---|",
    ]
    lines.extend(_row(name, s) for name, s in t2_good[:20])
    lines.extend(
        [
            "",
            "## 当前 T1 里表现较弱的城市（n>=5 且 pnl<0 或 roi<5%）",
            "",
            "| city | trades | wins | win_rate | cost_usd | pnl_usd | roi |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    lines.extend(_row(name, s) for name, s in t1_bad[:20])
    lines.extend(
        [
            "",
            "## 切片：by_city（全部）",
            "",
            "| city | pool | trades | wins | win_rate | cost_usd | pnl_usd | roi |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for name, s in city_rows:
        lines.append(f"| {name} | {city_pools.get(name, 'unknown')} | {s['n']} | {s['wins']} | {_fmt_pct(s['win_rate'])} | {_fmt_usd(s['cost_usd'])} | {_fmt_usd(s['pnl_usd'])} | {_fmt_pct(s['roi'])} |")
    lines.extend(
        [
            "",
            "## 切片：by_side",
            "",
            "| side | trades | wins | win_rate | cost_usd | pnl_usd | roi |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    lines.extend(_row(name, s) for name, s in _group(trades, lambda t: t.side))
    lines.extend(
        [
            "",
            "## 切片：by_model",
            "",
            "| model | trades | wins | win_rate | cost_usd | pnl_usd | roi |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    lines.extend(_row(name, s) for name, s in _group(trades, lambda t: t.model))
    lines.extend(
        [
            "",
            "## 数据完整性自检",
            "",
            "- [x] DB/API 降级原因已注明；本报告为 counterfactual snapshot replay。",
            "- [x] current_t1/current_t2 取 latest snapshot，不混用历史 city_pool 字段。",
            f"- [x] unsettled={unsettled}，未计入已结算总览。",
            "- [x] 切片限定为 by_city / by_side / by_model / by_pool 派生视图。",
            "",
            "## 观察与建议",
            "",
            "当前 T2 整体可以继续作为晋升候选池，但应按城市与 side 分层推进；当前 T1 中低 ROI 或负 PnL 城市建议先降 size 或 shadow，再等更多 live fill 样本确认。",
            "",
        ]
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shares", type=float, default=10.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    city_pools = _current_city_pools()
    if not city_pools:
        raise SystemExit("No current city pools found")
    trades, raw_candidates, unsettled = _collect(city_pools, args.shares)
    out_path = Path(args.out) if args.out else ROOT / "docs" / "analysis" / "2026-05" / "2026-05-27-performance-city-pool-contribution.md"
    _write_report(out_path, trades, city_pools, raw_candidates, unsettled)
    print(json.dumps({
        "out": str(out_path),
        "overall": _summarize(trades),
        "by_pool": dict(_group(trades, lambda t: t.current_pool)),
        "raw_candidates": raw_candidates,
        "unsettled": unsettled,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
